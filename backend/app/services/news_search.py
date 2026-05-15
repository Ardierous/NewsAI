"""Веб-поиск якорных URL: ProxyAPI (OpenAI web_search) → SerpAPI → Tavily."""

from __future__ import annotations

import json
import logging
import re
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

import requests

if TYPE_CHECKING:
    from app.config import Settings
    from app.proxyapi_client import ProxyApiClient

logger = logging.getLogger("app.news_search")

_TOPIC_POOL_PATH_RE = re.compile(
    r"/book/mutual/\d+/\d+(?:/|$)",
    re.IGNORECASE,
)

# Индекс / лента / рубрика — не отдельная статья (по path, без HTML).
_LISTING_PATH_EXACT = frozenset(
    {
        "/news",
        "/articles",
        "/article",
        "/blog",
        "/posts",
        "/novosti",
        "/press",
        "/press-center",
        "/media",
        "/all",
        "/latest",
        "/top",
        "/line",
    }
)
_LISTING_PATH_SUFFIX_RE = re.compile(
    r"(?:^|/)(?:ai-news-today|news-today|all-news|latest-news|newsline|news-line)(?:/|$)",
    re.IGNORECASE,
)
_ARXIV_LIST_PATH_RE = re.compile(r"^/list(?:/|$)", re.IGNORECASE)


_SUSPICIOUS_DATE_SEGMENT = re.compile(r"/(\d{8})(?:/|$)")


def url_suspected_hallucinated(url: str) -> bool:
    """Эвристика «URL от LLM»: обрезанный slug, неверная дата в пути (15052026), слишком короткий path."""
    u = (url or "").strip()
    if not u.startswith("http"):
        return True
    try:
        path = (urlparse(u).path or "").rstrip("/")
    except Exception:
        return True
    if not path or path == "/":
        return True
    last_seg = path.split("/")[-1]
    if last_seg.endswith("-") or last_seg.endswith("_"):
        return True
    if re.search(r"/doc/\d{5,9}$", path, re.IGNORECASE):
        return True
    for m in _SUSPICIOUS_DATE_SEGMENT.finditer(path):
        seg = m.group(1)
        if seg.startswith("20") and len(seg) == 8:
            try:
                y, mo, d = int(seg[:4]), int(seg[4:6]), int(seg[6:8])
                if 2010 <= y <= 2035 and 1 <= mo <= 12 and 1 <= d <= 31:
                    continue
            except ValueError:
                pass
        return True
    return False


def is_listing_page_url(url: str) -> bool:
    """URL ведёт на ленту/рубрику/индекс, а не на одну статью (эвристика по path)."""
    u = (url or "").strip()
    if not u.startswith("http"):
        return False
    if is_topic_pool_page_url(u):
        return True
    try:
        parsed = urlparse(u)
        host = (parsed.hostname or "").lower()
        path = (parsed.path or "").rstrip("/").lower() or "/"
    except Exception:
        return False
    if path in _LISTING_PATH_EXACT:
        return True
    if _LISTING_PATH_SUFFIX_RE.search(path):
        return True
    if "arxiv.org" in host and _ARXIV_LIST_PATH_RE.search(path):
        return True
    if "arxiv.org" in host and "/format/" in path:
        return True
    if "arxiv.org" in host and "/abs/" not in path and path.startswith("/list"):
        return True
    segments = [s for s in path.split("/") if s]
    if len(segments) == 1 and segments[0] in ("news", "articles", "blog", "novosti", "press"):
        return True
    if len(segments) == 2 and segments[0] in ("news", "articles") and segments[1] in ("top", "line", "all", "latest"):
        return True
    if re.search(r"^/technologies/?$", path):
        return True
    return False


def is_topic_pool_page_url(url: str) -> bool:
    """Страница-пул/индекс по теме (CNews «Индексная книга»), не отдельная новость."""
    u = (url or "").strip()
    if not u.startswith("http"):
        return False
    try:
        path = (urlparse(u).path or "").lower().rstrip("/")
    except Exception:
        return False
    if _TOPIC_POOL_PATH_RE.search(path):
        return True
    if "/book/mutual/" in path:
        return True
    return False


_AGGREGATOR_HOST_MARKERS = (
    "news.google.",
    "google.com/news",
    "news.ycombinator.com",
    "reddit.com",
    "medium.com/tag/",
    "feedly.com",
    "flipboard.com",
    "newsnow.",
    "news.yahoo.",
    "yandex.ru/news",
    "x.com",
    "t.co",
)


def fetch_article_urls_from_search(
    settings: "Settings",
    query: str,
    limit: int = 15,
    *,
    proxy: "ProxyApiClient | None" = None,
) -> list[str]:
    """
    Возвращает список URL статей из выдачи поиска (без CrewAI).
    Порядок: ProxyAPI web_search → SerpAPI (google_news) → Tavily.
    """
    if settings.enable_web_fetch and settings.proxyapi_web_search_enabled and proxy is not None:
        urls = proxy.search_news_article_urls(query, limit=limit)
        if urls:
            logger.info("ProxyAPI web_search: получено URL | count=%s", len(urls))
            return urls
    if settings.serpapi_api_key:
        urls = _serpapi_google_news_urls(settings.serpapi_api_key, query, limit)
        if urls:
            logger.info("SerpAPI: получено URL | count=%s", len(urls))
            return urls
    if settings.tavily_api_key:
        urls = _tavily_search_urls(settings.tavily_api_key, query, limit)
        if urls:
            logger.info("Tavily: получено URL | count=%s", len(urls))
            return urls
    return []


def extract_http_urls_from_text(text: str, limit: int = 20) -> list[str]:
    """Извлекает URL из JSON-массива или произвольного текста ответа модели."""
    raw = (text or "").strip()
    if not raw:
        return []
    stripped = _strip_markdown_fence(raw)
    try:
        if stripped.startswith("["):
            parsed = json.loads(stripped)
            if isinstance(parsed, list):
                out = [u.strip() for u in parsed if isinstance(u, str) and u.strip().startswith("http")]
                return _filter_search_urls(out, limit)
    except json.JSONDecodeError:
        pass
    found = re.findall(r"https?://[^\s\]\)\"'<>]+", raw)
    cleaned = [u.rstrip(".,;:)") for u in found if u.startswith("http")]
    return _filter_search_urls(cleaned, limit)


def extract_urls_from_responses_payload(response: Any, limit: int = 20) -> list[str]:
    """Собирает URL из Responses API: output_text, annotations, citations."""
    urls: list[str] = []
    text_parts: list[str] = []

    output_text = getattr(response, "output_text", None)
    if isinstance(output_text, str) and output_text.strip():
        text_parts.append(output_text)

    for item in getattr(response, "output", None) or []:
        content = getattr(item, "content", None) or []
        for block in content:
            block_type = getattr(block, "type", None)
            if block_type == "output_text":
                text_parts.append(getattr(block, "text", "") or "")
            for ann in getattr(block, "annotations", None) or []:
                url = getattr(ann, "url", None)
                if isinstance(url, str) and url.startswith("http"):
                    urls.append(url.split("#")[0])
    for text in text_parts:
        urls.extend(extract_http_urls_from_text(text, limit))
    return _uniq_urls(_filter_search_urls(urls, limit))


def _filter_search_urls(urls: list[str], limit: int) -> list[str]:
    out: list[str] = []
    for u in urls:
        if not u.startswith("http"):
            continue
        if _is_bad_search_url(u):
            continue
        out.append(u.split("#")[0])
        if len(out) >= limit:
            break
    return out


def _is_bad_search_url(url: str) -> bool:
    host = (urlparse(url).netloc or "").lower()
    if not host:
        return True
    if any(marker in host or marker in url.lower() for marker in _AGGREGATOR_HOST_MARKERS):
        return True
    path = (urlparse(url).path or "").lower().rstrip("/")
    if path in ("", "/"):
        return True
    if any(x in path for x in ("/search", "/tag/", "/tags/", "/category/", "/topics/")):
        return True
    if path.endswith("/neiroseti") or path.endswith("/artificial_intelligence"):
        return True
    if re.search(r"^/articles/[\w_-]+$", path):
        return True
    if is_listing_page_url(url):
        return True
    if is_topic_pool_page_url(url):
        return True
    if url_suspected_hallucinated(url):
        return True
    return False


def _strip_markdown_fence(raw: str) -> str:
    s = raw.strip()
    m = re.search(r"```(?:json)?\s*\n?(.*?)```", s, flags=re.DOTALL | re.IGNORECASE)
    if m:
        return m.group(1).strip()
    return s


def _serpapi_google_news_urls(api_key: str, query: str, limit: int) -> list[str]:
    try:
        r = requests.get(
            "https://serpapi.com/search.json",
            params={
                "engine": "google_news",
                "q": query,
                "api_key": api_key,
                "hl": "ru",
            },
            timeout=25,
        )
        if r.status_code >= 400:
            logger.warning("SerpAPI HTTP %s", r.status_code)
            return []
        data = r.json()
        out: list[str] = []
        for block in ("news_results", "organic_results"):
            for row in data.get(block) or []:
                link = row.get("link") if isinstance(row, dict) else None
                if isinstance(link, str) and link.startswith("http"):
                    out.append(link.split("#")[0])
                if len(out) >= limit:
                    return _uniq_urls(out)
        return _uniq_urls(out)
    except Exception:
        logger.warning("SerpAPI недоступен", exc_info=True)
        return []


def _tavily_search_urls(api_key: str, query: str, limit: int) -> list[str]:
    try:
        r = requests.post(
            "https://api.tavily.com/search",
            json={
                "api_key": api_key,
                "query": query,
                "max_results": min(limit, 20),
                "search_depth": "basic",
                "include_domains": [],
            },
            timeout=25,
        )
        if r.status_code >= 400:
            logger.warning("Tavily HTTP %s", r.status_code)
            return []
        data = r.json()
        out: list[str] = []
        for row in data.get("results") or []:
            if not isinstance(row, dict):
                continue
            u = row.get("url")
            if isinstance(u, str) and u.startswith("http"):
                out.append(u.split("#")[0])
        return _uniq_urls(out)
    except Exception:
        logger.warning("Tavily недоступен", exc_info=True)
        return []


def _uniq_urls(urls: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for u in urls:
        key = u.lower().rstrip("/")
        if key in seen:
            continue
        seen.add(key)
        out.append(u)
    return out
