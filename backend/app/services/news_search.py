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

from app.source_tiers_policy import is_aggregator_source, is_blocked_search_host
from app.services.step1_candidate_policy import is_product_tool_landing_url

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
_LISTING_PAGE_NUM_RE = re.compile(r"(?:^|/)page(?:-|/)?\d+(?:/|$)", re.IGNORECASE)
_HABR_HUB_LISTING_RE = re.compile(r"^/ru/hubs?/[^/]+/(?:articles|posts|news)(?:/|$)", re.IGNORECASE)
_TOP_ARCHIVE_PATH_RE = re.compile(r"/top/(?:daily|weekly|monthly|yearly)(?:/|$)", re.IGNORECASE)
_ARXIV_LIST_PATH_RE = re.compile(r"^/list(?:/|$)", re.IGNORECASE)
_RIA_YEAR_INDEX_RE = re.compile(r"^/\d{8}/\d{4}\.html$", re.IGNORECASE)
_AUTHOR_PATH_RE = re.compile(r"/authors?(?:/|$|\?)", re.IGNORECASE)
_AUTH_GATE_PATH_RE = re.compile(r"/auth(?:/|$|\?)", re.IGNORECASE)


_SUSPICIOUS_DATE_SEGMENT = re.compile(r"/(\d{8})(?:/|$)")


def _suspicious_eight_digit_path_segment(seg: str) -> bool:
    """
    8 цифр в path: отсекаем «левые даты» (20261340, 15052026 как ddmmyyyy),
    но не numeric id статей (TASS /15543211/, RBC hex id в другом формате).
    """
    if len(seg) != 8 or not seg.isdigit():
        return False
    if seg.startswith(("19", "20")):
        try:
            y, mo, d = int(seg[:4]), int(seg[4:6]), int(seg[6:8])
            if 1990 <= y <= 2035 and 1 <= mo <= 12 and 1 <= d <= 31:
                return False
        except ValueError:
            pass
        return True
    try:
        d, mo, y = int(seg[0:2]), int(seg[2:4]), int(seg[4:8])
        if 2010 <= y <= 2036 and 1 <= mo <= 12 and 1 <= d <= 31:
            return True
    except ValueError:
        pass
    return False


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
        if _suspicious_eight_digit_path_segment(m.group(1)):
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
    if _LISTING_PAGE_NUM_RE.search(path):
        return True
    if _HABR_HUB_LISTING_RE.search(path):
        return True
    if _TOP_ARCHIVE_PATH_RE.search(path):
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


def search_url_prefilter_reason(
    url: str,
    is_enabled: Any | None = None,
    order: list[str] | None = None,
) -> str | None:
    """Код отказа до HTTP-проверки (поиск/ингест). None — можно качать страницу."""
    enabled = is_enabled or (lambda _fid: True)
    u = (url or "").strip()
    if not u.startswith("http"):
        return "invalid_url" if enabled("invalid_url") else None
    try:
        host = (urlparse(u).netloc or "").lower()
        path = (urlparse(u).path or "").lower().rstrip("/") or "/"
    except Exception:
        return "invalid_url" if enabled("invalid_url") else None
    if not host:
        return "invalid_url" if enabled("invalid_url") else None
    checks: dict[str, bool] = {
        "aggregator_source": is_aggregator_source(u),
        "forbidden_media_source": is_blocked_search_host(u),
        "news_listing_page": (
            path in ("", "/")
            or any(x in path for x in ("/search", "/tag/", "/tags/", "/category/", "/topics/"))
            or path.endswith("/neiroseti")
            or path.endswith("/artificial_intelligence")
            or path.endswith("/artificial-intelligence")
            or path in ("/artificial-intelligence", "/ai", "/topic/artificial-intelligence")
            or bool(re.search(r"^/articles/[\w_-]+$", path))
            or is_search_noise_url(u)
            or is_listing_page_url(u)
            or is_topic_pool_page_url(u)
        ),
        "llm_hallucinated_url": url_suspected_hallucinated(u),
        "product_tool_page": is_product_tool_landing_url(u),
    }
    sequence = [x for x in (order or []) if x in checks]
    for fid in ("aggregator_source", "forbidden_media_source", "news_listing_page", "llm_hallucinated_url", "product_tool_page"):
        if fid not in sequence:
            sequence.append(fid)
    for fid in sequence:
        if enabled(fid) and checks.get(fid):
            return fid
    return None


def is_search_noise_url(url: str) -> bool:
    """URL из выдачи, которые не стоит качать: индексы, авторы, SSO, PDF arXiv."""
    u = (url or "").strip()
    if not u.startswith("http"):
        return True
    try:
        parsed = urlparse(u)
        host = (parsed.hostname or "").lower()
        path = (parsed.path or "").rstrip("/").lower() or "/"
    except Exception:
        return True
    if is_blocked_search_host(u):
        return True
    # Годовой индекс РИА: /20011020/2026.html — не статья; /20260519/slug.html — статья.
    if "ria.ru" in host and _RIA_YEAR_INDEX_RE.search(path + (".html" if not path.endswith(".html") else "")):
        return True
    if _AUTHOR_PATH_RE.search(path):
        return True
    if _AUTH_GATE_PATH_RE.search(path):
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


def fetch_article_urls_raw_merged(
    settings: "Settings",
    query: str,
    limit: int = 15,
    *,
    proxy: "ProxyApiClient | None" = None,
    search_context_size: str | None = None,
) -> list[str]:
    """
    Сырые уникальные URL от всех доступных провайдеров (до _filter_search_urls).
    ProxyAPI + SerpAPI + Tavily объединяются, а не «первый победил».
    """
    fetch_cap = max(limit, 15)
    merged: list[str] = []
    if settings.enable_web_fetch and settings.proxyapi_web_search_enabled and proxy is not None:
        try:
            merged.extend(
                proxy.search_news_article_urls(query, limit=fetch_cap, search_context_size=search_context_size)
            )
        except Exception:
            logger.warning("ProxyAPI web_search: ошибка запроса", exc_info=True)
    if settings.serpapi_api_key:
        merged.extend(_serpapi_google_news_urls(settings.serpapi_api_key, query, fetch_cap))
    if settings.tavily_api_key:
        merged.extend(_tavily_search_urls(settings.tavily_api_key, query, fetch_cap))
    out = _uniq_urls(merged)
    if out:
        logger.info(
            "Веб-поиск: сырые URL (все провайдеры) | count=%s cap=%s",
            len(out),
            fetch_cap,
        )
    return out


def fetch_article_urls_from_search(
    settings: "Settings",
    query: str,
    limit: int = 15,
    *,
    proxy: "ProxyApiClient | None" = None,
    is_enabled: Any | None = None,
    order: list[str] | None = None,
    search_context_size: str | None = None,
) -> list[str]:
    """URL после фильтра search_url_prefilter_reason (legacy / supplement)."""
    raw = fetch_article_urls_raw_merged(
        settings, query, limit=limit, proxy=proxy, search_context_size=search_context_size
    )
    return _filter_search_urls(raw, limit, is_enabled=is_enabled, order=order)


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


def _filter_search_urls(
    urls: list[str],
    limit: int,
    *,
    is_enabled: Any | None = None,
    order: list[str] | None = None,
) -> list[str]:
    out: list[str] = []
    for u in urls:
        if not u.startswith("http"):
            continue
        if search_url_prefilter_reason(u, is_enabled=is_enabled, order=order):
            continue
        out.append(u.split("#")[0])
        if len(out) >= limit:
            break
    return out


def _is_bad_search_url(url: str) -> bool:
    return search_url_prefilter_reason(url) is not None


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
