"""Обход антибот-заглушек: чтение статьи через r.jina.ai, если прямой GET не дал текст."""

from __future__ import annotations

import logging
import re
from typing import Any
from urllib.parse import urlparse

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


def reader_fallback_allowed(url: str) -> bool:
    host = (urlparse(str(url or "").strip()).hostname or "").lower().removeprefix("www.")
    if not host or host in {"manual", "unknown", "localhost"}:
        return False
    return True


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
    headers = {
        "Accept": "text/plain",
        "User-Agent": "Mozilla/5.0 (compatible; NewsDigestBot/1.0; +reader-fallback)",
    }
    last_status: int | None = None
    for reader_url in _jina_reader_urls(initial_url):
        try:
            r = requests.get(
                reader_url,
                timeout=(5, 16),
                headers=headers,
                allow_redirects=True,
            )
            last_status = r.status_code
            if r.status_code >= 400 or not r.text:
                continue
            markdown_text = str(r.text)
            headline = extract_reader_headline(markdown_text)
            topic_corpus = extract_reader_topic_corpus(markdown_text)
            if not headline or len(topic_corpus) < 120:
                continue
            published_at = extract_reader_published_at(markdown_text)
            logger.info(
                "Reader fallback: статья получена | url=%s reader=%s headline_len=%s corpus_len=%s",
                initial_url[:160],
                reader_url[:80],
                len(headline),
                len(topic_corpus),
            )
            return {
                "ok": True,
                "status_code": r.status_code,
                "final_url": initial_url,
                "display_url": initial_url,
                "headline": headline,
                "headline_source": "reader_proxy",
                "headline_strict": True,
                "article_markers": True,
                "soft_article_signals": True,
                "topic_corpus": topic_corpus,
                "published_at": published_at,
                "is_listing_page": False,
                "listing_article_urls": [],
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
