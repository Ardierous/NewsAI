"""Обход антибот-заглушек: чтение статьи через r.jina.ai, если прямой GET не дал текст."""

from __future__ import annotations

import html
import logging
import re
from typing import Any
from urllib.parse import urljoin, urlparse

import requests

logger = logging.getLogger("app.digest")

_ANTIBOT_MARKERS = (
    "checking your browser",
    "cf-browser-verification",
    "__cf_chl",
    "challenge-platform",
    "ddos-guard",
    "ddosguard",
    "servicepipe",
    "qrator",
    "captcha",
    "attention required",
    "please enable javascript",
    "enable javascript",
    "access denied",
    "bot detection",
    "/exhkqyad",
    "just a moment",
    "ray id",
    "проверка браузера",
    "подтвердите, что вы не робот",
    "включите javascript",
    "идёт проверка",
)

_ARTICLE_STRUCTURE_RE = re.compile(
    r"<article\b|itemtype=[\"']https?://schema\.org/NewsArticle|property=[\"']og:type[\"'][^>]+article|"
    r"articlebody|class=[\"'][^\"']*article[^\"']*[\"']",
    re.IGNORECASE,
)
_MD_LINK_RE = re.compile(
    r"\[[^\]]*\]\(\s*<?([^)\s>]+)>?\s*(?:\"[^\"]*\")?\s*\)",
    re.IGNORECASE,
)
_BARE_HTTP_RE = re.compile(r"https?://[^\s\]\)<>\"']+", re.IGNORECASE)
_JINA_WRAP_RE = re.compile(r"^https?://r\.jina\.ai/(https?://.+)$", re.IGNORECASE)
_STATIC_ASSET_RE = re.compile(
    r"\.(?:jpg|jpeg|png|gif|webp|svg|css|js|pdf|zip|woff2?)(?:\?|$)",
    re.IGNORECASE,
)


def reader_fallback_allowed(url: str) -> bool:
    host = (urlparse(str(url or "").strip()).hostname or "").lower().removeprefix("www.")
    if not host or host in {"manual", "unknown", "localhost"}:
        return False
    return True


def is_investing_com_url(url: str) -> bool:
    try:
        host = (urlparse(str(url or "").strip()).hostname or "").lower()
    except Exception:
        return False
    return "investing.com" in host


def _listing_seed_for_reader(url: str) -> bool:
    from app.services.news_search import is_step1_listing_seed_url

    return is_step1_listing_seed_url(url)


def _unwrap_jina_url(url: str) -> str:
    m = _JINA_WRAP_RE.match(str(url or "").strip())
    return m.group(1) if m else str(url or "").strip()


def _hosts_compatible(page_host: str, link_host: str) -> bool:
    a = (page_host or "").lower().removeprefix("www.")
    b = (link_host or "").lower().removeprefix("www.")
    if not a or not b:
        return False
    if a == b:
        return True
    return "investing.com" in a and "investing.com" in b


def extract_reader_markdown_urls(markdown_text: str, page_url: str, limit: int = 48) -> list[str]:
    """Same-host HTTP URLs from jina markdown ([text](url), bare links, Links/Buttons)."""
    text = markdown_text or ""
    try:
        page_host = urlparse(page_url).hostname or ""
    except Exception:
        return []
    candidates: list[str] = []
    for m in _MD_LINK_RE.finditer(text):
        candidates.append(m.group(1))
    for m in _BARE_HTTP_RE.finditer(text):
        candidates.append(m.group(0))
    page_key = str(page_url or "").strip().rstrip("/").lower()
    found: list[str] = []
    seen: set[str] = set()
    for raw in candidates:
        href = html.unescape(str(raw or "").strip()).rstrip(".,);]\"'")
        if not href or href.startswith(("#", "javascript:", "mailto:", "data:", "tel:")):
            continue
        href = _unwrap_jina_url(href)
        try:
            abs_url = urljoin(page_url, href).split("#")[0]
            abs_url = _unwrap_jina_url(abs_url)
            pu = urlparse(abs_url)
        except Exception:
            continue
        if pu.scheme not in ("http", "https"):
            continue
        if not _hosts_compatible(page_host, pu.hostname or ""):
            continue
        if _STATIC_ASSET_RE.search(pu.path or ""):
            continue
        key = abs_url.rstrip("/").lower()
        if not key or key in seen or key == page_key:
            continue
        seen.add(key)
        found.append(abs_url)
        if len(found) >= limit:
            break
    return found


def _reader_timeout(url: str) -> tuple[float, float]:
    if is_investing_com_url(url):
        return (6.0, 20.0)
    return (5.0, 16.0)


def _reader_request_headers(url: str, *, listing: bool) -> dict[str, str]:
    headers = {
        "Accept": "text/plain",
        "User-Agent": "Mozilla/5.0 (compatible; NewsDigestBot/1.0; +reader-fallback)",
    }
    if listing or is_investing_com_url(url):
        headers["X-With-Links-Summary"] = "all"
        headers["X-Timeout"] = "18"
    return headers


def looks_like_antibot_shell(chunk: str, *, visible_text: str | None = None) -> bool:
    if not chunk:
        return False
    sample = chunk[:100_000].lower()
    if _ARTICLE_STRUCTURE_RE.search(sample):
        vis = (visible_text or "").strip()
        if len(vis) >= 400:
            return False
    if "url=/exhkqyad" in sample or 'content="0; url=/exhkqyad"' in sample:
        return True
    if "<noscript>" in sample and "refresh" in sample and len(chunk) < 12_000:
        if not _ARTICLE_STRUCTURE_RE.search(sample):
            return True
    hits = sum(1 for marker in _ANTIBOT_MARKERS if marker in sample)
    if hits >= 2:
        return True
    if "cloudflare" in sample and ("challenge" in sample or "ray id" in sample):
        return True
    return False


def is_bot_challenge_html(chunk: str) -> bool:
    """Совместимость: антибот-заглушка без полноценной статьи."""
    return looks_like_antibot_shell(chunk)


def extract_reader_headline(markdown_text: str) -> str | None:
    def _clean(raw: str) -> str:
        t = re.sub(r"<[^>]+>", " ", str(raw or ""))
        return re.sub(r"\s+", " ", t).strip()

    lines = [ln.strip() for ln in (markdown_text or "").splitlines() if ln.strip()]
    for ln in lines[:20]:
        if ln.lower().startswith("title:"):
            title = _clean(ln.split(":", 1)[1])
            if len(title) >= 8:
                return title
    for ln in lines[:30]:
        if ln.startswith("# "):
            title = _clean(ln[2:])
            if len(title) >= 8:
                return title
    return None


def extract_reader_topic_corpus(markdown_text: str) -> str:
    text = re.sub(r"^Source URL:.*$", " ", markdown_text, flags=re.IGNORECASE | re.MULTILINE)
    text = re.sub(r"^Website URL:.*$", " ", text, flags=re.IGNORECASE | re.MULTILINE)
    text = re.sub(r"^Website Title:.*$", " ", text, flags=re.IGNORECASE | re.MULTILINE)
    text = re.sub(r"^Published Time:.*$", " ", text, flags=re.IGNORECASE | re.MULTILINE)
    text = re.sub(r"^Title:.*$", " ", text, flags=re.IGNORECASE | re.MULTILINE)
    text = re.sub(r"`+", " ", text)
    text = re.sub(r"\[(.*?)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:30_000]


def extract_reader_published_at(markdown_text: str) -> str | None:
    for ln in (markdown_text or "").splitlines()[:30]:
        low = ln.strip().lower()
        if low.startswith("published time:"):
            value = ln.split(":", 1)[1].strip()
            if value:
                return value[:80]
    return None


def _jina_reader_urls(initial_url: str) -> list[str]:
    url = str(initial_url or "").strip()
    if not url.startswith(("http://", "https://")):
        return []
    bare = re.sub(r"^https?://", "", url, flags=re.IGNORECASE)
    preferred = "https" if url.lower().startswith("https://") else "http"
    other = "http" if preferred == "https" else "https"
    return [
        f"https://r.jina.ai/{preferred}://{bare}",
        f"https://r.jina.ai/{other}://{bare}",
    ]


def fetch_article_bundle_via_reader_proxy(initial_url: str) -> dict[str, Any] | None:
    if not reader_fallback_allowed(initial_url):
        return None
    listing_seed = _listing_seed_for_reader(initial_url)
    headers = _reader_request_headers(initial_url, listing=listing_seed)
    timeout = _reader_timeout(initial_url)
    last_status: int | None = None
    for reader_url in _jina_reader_urls(initial_url):
        try:
            r = requests.get(
                reader_url,
                timeout=timeout,
                headers=headers,
                allow_redirects=True,
            )
            last_status = r.status_code
            if r.status_code >= 400 or not r.text:
                continue
            markdown_text = str(r.text)
            hrefs = extract_reader_markdown_urls(markdown_text, initial_url)
            headline = extract_reader_headline(markdown_text)
            topic_corpus = extract_reader_topic_corpus(markdown_text)
            listing_ok = listing_seed and len(hrefs) >= 2
            article_ok = bool(headline) and len(topic_corpus) >= 120
            if not listing_ok and not article_ok:
                continue
            if listing_ok and (not headline or len(str(headline).strip()) < 8):
                host = (urlparse(initial_url).hostname or "источник").removeprefix("www.")
                headline = f"Лента новостей {host}"
            if listing_ok and len(topic_corpus) < 40:
                topic_corpus = " ".join(hrefs[:12])[:4000]
            published_at = extract_reader_published_at(markdown_text)
            logger.info(
                "Reader fallback: %s получена | url=%s reader=%s headline_len=%s corpus_len=%s hrefs=%s",
                "лента" if listing_ok else "статья",
                initial_url[:160],
                reader_url[:80],
                len(headline or ""),
                len(topic_corpus),
                len(hrefs),
            )
            return {
                "ok": True,
                "status_code": r.status_code,
                "final_url": initial_url,
                "display_url": initial_url,
                "headline": headline,
                "headline_source": "reader_proxy",
                "headline_strict": not listing_ok,
                "article_markers": not listing_ok,
                "soft_article_signals": not listing_ok,
                "topic_corpus": topic_corpus,
                "published_at": published_at,
                "is_listing_page": listing_ok,
                "listing_article_urls": [],
                "reader_hrefs": hrefs,
                "fetch_via": "reader_proxy",
            }
        except Exception:
            logger.debug("Reader fallback: ошибка запроса | url=%s", reader_url[:120], exc_info=True)
            continue
    if last_status is not None:
        logger.debug(
            "Reader fallback: не удалось извлечь статью | url=%s last_status=%s",
            initial_url[:160],
            last_status,
        )
    return None
