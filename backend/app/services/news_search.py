"""Веб-поиск якорных URL: ProxyAPI (OpenAI web_search) → SerpAPI → Tavily."""

from __future__ import annotations

import json
import logging
import re
from datetime import date
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

import requests

if TYPE_CHECKING:
    from app.config import Settings
    from app.proxyapi_client import ProxyApiClient

from app.curious_source_policy import (
    curious_host_search_groups,
    get_curious_source_policy,
    is_curious_aggregator_source,
    is_curious_blocked_host,
    is_curious_policy_source,
)
from app.source_tiers_policy import (
    batched_site_host_groups,
    get_source_tiers_policy,
    is_aggregator_source,
    is_blocked_search_host,
    is_policy_tier_source,
    is_russian_host,
    policy_tier_host_groups_ru_first,
)
from app.services.digest_type_policy import (
    step1_curious_entertainment_anchor_en,
    step1_curious_entertainment_anchor_ru,
)
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
        "/ai",
        "/artificial-intelligence",
        "/artificial_intelligence",
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
_URL_PATH_DATE_COMPACT_RE = re.compile(r"/(\d{4})(\d{2})(\d{2})(?:/|$)")
_URL_PATH_DATE_DASH_RE = re.compile(r"/(\d{4})-(\d{2})-(\d{2})(?:/|$)")
_URL_PATH_DATE_RE = re.compile(r"/(\d{4})/(\d{2})/(\d{2})(?:/|$)")
_EDITORIAL_DATED_STORY_RE = re.compile(
    r"^/\d{4}/\d{2}/\d{2}/\d{6,8}/([^/?#]+)",
    re.IGNORECASE,
)
_LLM_GENERIC_SLUG_TAIL_RE = re.compile(
    r"(?:raises-questions|revolutionizes|accelerates|transforms-the|breakthrough-in|game-changer|reshapes-the)",
    re.IGNORECASE,
)
_SYNTHETIC_EDITORIAL_HOST_MARKERS = (
    "technologyreview.com",
    "wired.com",
    "theverge.com",
    "arstechnica.com",
    "scientificamerican.com",
)
_FOREIGN_EDITORIAL_ACCEPT_CAP_HOSTS = (
    "technologyreview.com",
    "spectrum.ieee.org",
    "wired.com",
    "sciencedaily.com",
    "techxplore.com",
)
_MIT_TR_VALID_STORY_PATH_RE = re.compile(
    r"^/\d{4}/\d{2}/\d{2}/\d{6,8}/",
    re.IGNORECASE,
)
_EDITORIAL_YEAR_SUFFIX_SLUG_RE = re.compile(
    r"/\d{4}/\d{2}/\d{2}/[^/]*-(?:202[4-9]|2030)(?:/|$)",
    re.IGNORECASE,
)


def parse_search_window_dates(window_prefix: str) -> tuple[date | None, date | None]:
    """Из after:/before: в префиксе поискового запроса — границы окна (inclusive)."""
    prefix = window_prefix or ""
    earliest: date | None = None
    anchor: date | None = None
    m_after = re.search(r"after:(\d{4}-\d{2}-\d{2})", prefix)
    if m_after:
        try:
            earliest = date.fromisoformat(m_after.group(1))
        except ValueError:
            pass
    m_before = re.search(r"before:(\d{4}-\d{2}-\d{2})", prefix)
    if m_before:
        try:
            anchor = date.fromisoformat(m_before.group(1))
        except ValueError:
            pass
    return earliest, anchor


def url_path_publication_day(url: str) -> date | None:
    """Календарный день из path URL (compact, dash, slash) без HTTP."""
    try:
        path = urlparse(url).path or ""
    except Exception:
        return None
    for pattern in (_URL_PATH_DATE_COMPACT_RE, _URL_PATH_DATE_DASH_RE, _URL_PATH_DATE_RE):
        m = pattern.search(path)
        if not m:
            continue
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            continue
    return None


def search_url_path_date_outside_window(
    url: str,
    *,
    earliest: date | None,
    anchor: date | None,
) -> bool:
    """True, если дата в path явно вне [earliest, anchor]."""
    pub_day = url_path_publication_day(url)
    if pub_day is None:
        return False
    if earliest is not None and pub_day < earliest:
        return True
    if anchor is not None and pub_day > anchor:
        return True
    return False


def _fake_numeric_story_id(story_id: str) -> bool:
    sid = (story_id or "").strip()
    if not sid.isdigit() or len(sid) < 6:
        return False
    if sid.startswith("123456") or sid.startswith("000000"):
        return True
    return len(set(sid)) <= 2


def _mit_tr_invented_path(norm_path: str) -> bool:
    """MIT TR: реальные статьи — /YYYY/MM/DD/<6-8 digit id>/slug; без id — типичная галлюцинация."""
    if not re.match(r"^/\d{4}/\d{2}/\d{2}/", norm_path):
        return False
    if _MIT_TR_VALID_STORY_PATH_RE.match(norm_path):
        id_m = re.match(r"^/\d{4}/\d{2}/\d{2}/(\d+)/", norm_path)
        return bool(id_m and _fake_numeric_story_id(id_m.group(1)))
    return True


def url_suspected_synthetic_editorial_story(url: str) -> bool:
    """Выдуманные URL editorial-сайтов (без HTTP): шаблонный slug, фейковый id, path без story id."""
    u = (url or "").strip()
    if not u.startswith("http"):
        return False
    try:
        parsed = urlparse(u)
        host = (parsed.hostname or "").lower()
        path = (parsed.path or "").rstrip("/")
    except Exception:
        return False
    if not any(marker in host for marker in _SYNTHETIC_EDITORIAL_HOST_MARKERS):
        return False
    norm_path = path if path.startswith("/") else f"/{path}"
    if "technologyreview.com" in host and _mit_tr_invented_path(norm_path):
        return True
    if _EDITORIAL_YEAR_SUFFIX_SLUG_RE.search(norm_path):
        return True
    m = _EDITORIAL_DATED_STORY_RE.match(norm_path)
    if not m:
        return False
    slug = m.group(1).lower()
    if _fake_numeric_story_id(slug):
        return True
    return slug.count("-") >= 4 and len(slug) >= 40 and bool(_LLM_GENERIC_SLUG_TAIL_RE.search(slug))


def search_url_raw_reject_reason(
    url: str,
    *,
    earliest: date | None = None,
    anchor: date | None = None,
) -> str | None:
    """Отсев сырого URL сразу после web_search (до HTTP шага 1)."""
    u = (url or "").strip()
    if not u.startswith("http"):
        return "invalid_url"
    pre = search_url_prefilter_reason(u)
    if pre:
        return pre
    if search_url_path_date_outside_window(u, earliest=earliest, anchor=anchor):
        return "published_before_window"
    return None


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
    if url_suspected_synthetic_editorial_story(u):
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
    *,
    tier_strict: bool = False,
    curious_strict: bool = False,
    earliest: date | None = None,
    anchor: date | None = None,
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
        "non_policy_source": (
            curious_strict and not is_curious_policy_source(u)
        )
        or (tier_strict and not curious_strict and not is_policy_tier_source(u)),
        "aggregator_source": (
            is_curious_aggregator_source(u) if curious_strict else is_aggregator_source(u)
        ),
        "forbidden_media_source": (
            is_curious_blocked_host(u) if curious_strict else is_blocked_search_host(u)
        ),
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
        "published_before_window": search_url_path_date_outside_window(
            u, earliest=earliest, anchor=anchor
        ),
    }
    sequence = [x for x in (order or []) if x in checks]
    for fid in (
        "non_policy_source",
        "aggregator_source",
        "forbidden_media_source",
        "news_listing_page",
        "llm_hallucinated_url",
        "product_tool_page",
        "published_before_window",
    ):
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
    include_domains: list[str] | None = None,
    allowed_hosts: list[str] | None = None,
    force_proxyapi: bool = False,
    proxy_fallback_on_empty: bool = False,
    curious_search: bool = False,
) -> list[str]:
    """
    Сырые уникальные URL (до _filter_search_urls).
    При web_search_prefer_alt_providers: SerpAPI/Tavily первыми, ProxyAPI — если мало URL или force_proxyapi.
    """
    fetch_cap = max(limit, 15)
    prefer_alt = bool(getattr(settings, "step1_web_search_prefer_alt_providers", True))
    min_before_proxy = max(0, int(getattr(settings, "step1_min_urls_before_proxyapi", 5) or 5))
    merged: list[str] = []

    if settings.serpapi_api_key:
        merged.extend(_serpapi_google_news_urls(settings.serpapi_api_key, query, fetch_cap))
    if settings.tavily_api_key:
        merged.extend(
            _tavily_search_urls(
                settings.tavily_api_key,
                query,
                fetch_cap,
                include_domains=include_domains or allowed_hosts,
            )
        )
    merged = _uniq_urls(merged)

    need_proxy = force_proxyapi or not prefer_alt or len(merged) < min_before_proxy
    if need_proxy and settings.enable_web_fetch and settings.proxyapi_web_search_enabled and proxy is not None:
        try:
            proxy_urls = proxy.search_news_article_urls(
                query,
                limit=fetch_cap,
                search_context_size=search_context_size,
                allowed_hosts=allowed_hosts,
                fallback_on_empty=proxy_fallback_on_empty,
                curious_search=curious_search,
            )
            if proxy_urls:
                merged = _uniq_urls([*merged, *proxy_urls])
                logger.info(
                    "Веб-поиск: ProxyAPI web_search | count=%s prefer_alt=%s force=%s",
                    len(proxy_urls),
                    prefer_alt,
                    force_proxyapi,
                )
        except Exception:
            logger.warning("ProxyAPI web_search: ошибка запроса", exc_info=True)

    out = _uniq_urls(merged)
    if out:
        logger.info(
            "Веб-поиск: сырые URL | count=%s cap=%s proxyapi_used=%s",
            len(out),
            fetch_cap,
            need_proxy and bool(proxy),
        )
    return out


def fetch_tier_prioritized_raw_urls(
    settings: "Settings",
    *,
    window_prefix: str,
    topic_terms: str,
    product_excludes: str,
    fetch_limit: int,
    proxy: "ProxyApiClient | None" = None,
    search_context_size: str | None = None,
    policy: Any | None = None,
    seed_urls: tuple[str, ...] | None = None,
    current_verified: int = 0,
) -> list[str]:
    """
    Поиск строго по tier-1 → tier-4 из source_tiers.txt: батчи site: запросов по хостам политики.
    URL вне tier-1…4 не попадают в результат.
    """
    p = policy or get_source_tiers_policy()
    seeds = tuple(seed_urls or p.search_seed_urls)
    per_batch_limit = max(6, min(12, max(fetch_limit // 6, 6)))
    raw_target = max(14, min(fetch_limit, 22))
    min_unique_hosts_target = max(6, min(10, max(fetch_limit // 2, 6)))
    max_urls_per_host = max(2, min(4, max(fetch_limit // 12, 2)))
    short_pool = max(0, int(current_verified or 0)) < 10
    if short_pool:
        max_urls_per_host = max(3, min(5, max_urls_per_host + 1))
    merged: list[str] = []
    seen: set[str] = set()
    host_counts: dict[str, int] = {}
    max_batches = max(1, int(getattr(settings, "step1_tier_max_web_search_batches", 6) or 6))
    if short_pool:
        raw_target = max(raw_target, min(fetch_limit, 30))
        min_unique_hosts_target = max(4, min_unique_hosts_target - 2)
        max_batches = max(max_batches, 10)
    batches_used = 0
    medium_escalations_used = 0
    preview_fallbacks_used = 0
    max_medium_escalations = 3
    max_preview_fallbacks = 1
    window_earliest, window_anchor = parse_search_window_dates(window_prefix)
    ru_hosts_seen = 0

    def _enough_coverage() -> bool:
        return len(merged) >= raw_target and len(host_counts) >= min_unique_hosts_target

    def _accept(urls: list[str]) -> None:
        for raw in urls:
            u = str(raw or "").strip()
            if not u.startswith("http") or not is_policy_tier_source(u, p):
                continue
            if search_url_raw_reject_reason(
                u, earliest=window_earliest, anchor=window_anchor
            ):
                continue
            key = u.lower().rstrip("/")
            if key in seen:
                continue
            host = (urlparse(u).hostname or "").lower()
            if host and host_counts.get(host, 0) >= max_urls_per_host:
                continue
            if any(marker in host for marker in _FOREIGN_EDITORIAL_ACCEPT_CAP_HOSTS):
                if host_counts.get(host, 0) >= 1:
                    continue
            seen.add(key)
            merged.append(u)
            if host:
                host_counts[host] = host_counts.get(host, 0) + 1

    for tier_label, hosts in policy_tier_host_groups_ru_first(p):
        if not hosts:
            continue
        base_tier = tier_label.removesuffix("-defer")
        if current_verified >= 10 and base_tier in ("Tier-3", "Tier-4"):
            continue
        if tier_label.endswith("-defer"):
            # Дорогие батчи с частыми 404: только если RU-выдача не наполнила пул.
            if _enough_coverage() or (ru_hosts_seen >= 2 and len(merged) >= max(8, raw_target // 2)):
                continue
        if _enough_coverage():
            break
        for batch in batched_site_host_groups(hosts, batch_size=3):
            if _enough_coverage() or batches_used >= max_batches:
                break
            matching_seeds = [s for s in seeds if any(marker in s.lower() for marker in batch)]
            seed_hint = ""
            if matching_seeds:
                seed_hint = " Рекомендуемые разделы: " + ", ".join(matching_seeds[:8]) + ". "
            site_part = " OR ".join(f"site:{h}" for h in batch)
            has_aggregator_hosts = any(m in p.aggregator_hosts for m in batch)
            if has_aggregator_hosts:
                # Для агрегаторных батчей нельзя одновременно требовать:
                # 1) "только домены агрегаторов" и 2) "верни первоисточник не агрегатор".
                # Иначе модель часто возвращает пустой список.
                primary_scope_hosts = tuple(
                    dict.fromkeys(
                        (
                            *p.tier1_hosts,
                            *p.tier2_hosts,
                            *p.tier3_hosts,
                            *p.tier4_hosts,
                            *batch,
                        )
                    )
                )
                query = (
                    f"{window_prefix}{seed_hint}{topic_terms} "
                    "Можно использовать агрегаторы для обнаружения сюжета, "
                    "но в ответе верни прямые URL отдельных HTML-статей первоисточников "
                    "(не ленты/поиск/теги агрегаторов). "
                    f"Опорные домены для разведки: {', '.join(batch)}. "
                    f"{product_excludes}"
                )
                include_domains = list(primary_scope_hosts)
                allowed_hosts = list(primary_scope_hosts)
            else:
                query = (
                    f"{window_prefix}{seed_hint}{topic_terms} "
                    f"Искать ТОЛЬКО на доменах: {', '.join(batch)}. ({site_part}) "
                    f"{product_excludes}"
                )
                include_domains = list(batch)
                allowed_hosts = list(batch)
            logger.info(
                "Tier-поиск: %s | hosts=%s limit=%s",
                tier_label,
                ",".join(batch),
                per_batch_limit,
            )
            batch_urls = fetch_article_urls_raw_merged(
                settings,
                query,
                limit=per_batch_limit,
                proxy=proxy,
                search_context_size=search_context_size,
                include_domains=include_domains,
                allowed_hosts=allowed_hosts,
            )
            if (
                not batch_urls
                and search_context_size == "low"
                and (tier_label in ("Tier-1", "Tier-2") or has_aggregator_hosts)
                and medium_escalations_used < max_medium_escalations
            ):
                medium_escalations_used += 1
                use_preview_fallback = has_aggregator_hosts and preview_fallbacks_used < max_preview_fallbacks
                if use_preview_fallback:
                    preview_fallbacks_used += 1
                logger.info(
                    "Tier-поиск: эскалация пустого батча low→medium | tier=%s hosts=%s preview_fallback=%s escalation=%s/%s",
                    tier_label,
                    ",".join(batch),
                    use_preview_fallback,
                    medium_escalations_used,
                    max_medium_escalations,
                )
                batch_urls = fetch_article_urls_raw_merged(
                    settings,
                    query,
                    limit=per_batch_limit,
                    proxy=proxy,
                    search_context_size="medium",
                    include_domains=include_domains,
                    allowed_hosts=allowed_hosts,
                    proxy_fallback_on_empty=use_preview_fallback,
                )
            batches_used += 1
            if any(is_russian_host(h, p) or str(h).endswith(".ru") for h in batch):
                ru_hosts_seen += 1
            _accept(batch_urls)
    if merged:
        logger.info(
            "Tier-поиск: итого URL из политики tier-1…4 | count=%s unique_hosts=%s batches=%s/%s cap=%s ru_batches=%s",
            len(merged),
            len(host_counts),
            batches_used,
            max_batches,
            fetch_limit,
            ru_hosts_seen,
        )
    return merged[:fetch_limit]


def fetch_curious_prioritized_raw_urls(
    settings: "Settings",
    *,
    window_prefix: str,
    topic_terms_ru: str,
    topic_terms_foreign: str,
    product_excludes: str,
    fetch_limit: int,
    proxy: "ProxyApiClient | None" = None,
    search_context_size: str | None = None,
    policy: Any | None = None,
) -> list[str]:
    """
    Поиск для курьёзного выпуска: сначала RU-домены из curious_source_hosts.txt, затем зарубежные.
    """
    p = policy or get_curious_source_policy()
    seeds = p.search_seed_urls
    per_batch_limit = max(18, min(40, max(fetch_limit // 3, 20)))
    merged: list[str] = []
    seen: set[str] = set()
    host_counts: dict[str, int] = {}
    max_urls_per_host = 5
    collect_cap = max(fetch_limit * 3, fetch_limit + 24, 60)

    window_earliest, window_anchor = parse_search_window_dates(window_prefix)

    def _rank_url(url: str) -> tuple[int, int, int]:
        day = url_path_publication_day(url)
        host = (urlparse(url).hostname or "").lower()
        if search_url_path_date_outside_window(
            url, earliest=window_earliest, anchor=window_anchor
        ):
            fresh_rank = 2
        elif day:
            fresh_rank = 0
        elif any(h in host for h in (*p.curious_tier1_hosts, *p.curious_tier2_hosts)):
            # Curious-T1/T2 часто без даты в path — не штрафуем за «неизвестную свежесть».
            fresh_rank = 0
        else:
            fresh_rank = 1
        if any(h in host for h in p.curious_tier1_hosts):
            source_rank = 0
        elif any(h in host for h in p.curious_tier2_hosts):
            source_rank = 1
        else:
            source_rank = 2
        return (fresh_rank, source_rank, -(day.toordinal() if day else 0))

    def _accept(urls: list[str]) -> None:
        for raw in urls:
            u = str(raw or "").strip()
            if not u.startswith("http") or not is_curious_policy_source(u, p):
                continue
            if search_url_raw_reject_reason(
                u, earliest=window_earliest, anchor=window_anchor
            ):
                continue
            key = u.lower().rstrip("/")
            if key in seen:
                continue
            host = (urlparse(u).hostname or "").lower()
            if host and host_counts.get(host, 0) >= max_urls_per_host:
                continue
            seen.add(key)
            merged.append(u)
            if host:
                host_counts[host] = host_counts.get(host, 0) + 1

    groups = list(curious_host_search_groups(p))
    groups.sort(
        key=lambda x: {
            "Curious-T1": 0,
            "Curious-T2": 1,
            "Curious-T2-Aggregators": 2,
        }.get(x[0], 9)
    )
    for tier_label, hosts in groups:
        if not hosts:
            continue
        # В curious-поиске нельзя останавливаться на первой группе доменов:
        # иначе cap заполняется старым evergreen-контентом и поиск не доходит до Habr/VC/foreign.
        if len(merged) >= collect_cap:
            break
        for batch in batched_site_host_groups(hosts, batch_size=3):
            if tier_label == "Curious-T2":
                terms = topic_terms_ru if any(h in batch for h in p.curious_ru_tech_hosts) else topic_terms_foreign
            elif tier_label == "Curious-T1":
                terms = topic_terms_ru if any(h in batch for h in p.curious_ru_entertainment_hosts) else topic_terms_foreign
            else:
                terms = topic_terms_ru
            matching_seeds = [s for s in seeds if any(marker in s.lower() for marker in batch)]
            seed_hint = ""
            if matching_seeds:
                seed_hint = " Рекомендуемые разделы: " + ", ".join(matching_seeds[:8]) + ". "
            site_part = " OR ".join(f"site:{h}" for h in batch)
            agg_hint = ""
            if tier_label == "Curious-T2-Aggregators":
                agg_hint = (
                    " Домены агрегаторов Curious-T2: только прямые URL статей первоисточников, "
                    "не ленты/поиск/теги. "
                )
            query = (
                f"{window_prefix}{seed_hint}"
                f"ОБЯЗАТЕЛЬНО: в заголовке или тексте должен быть развлекательный угол "
                f"({step1_curious_entertainment_anchor_ru() if terms == topic_terms_ru else step1_curious_entertainment_anchor_en()}). "
                f"{terms} "
                "Только свежие отдельные статьи внутри окна дат; не возвращай архивы, подборки, справки, вакансии, "
                "ленты, рубрики и evergreen-материалы. "
                "Приоритет: забавные, смешные, вирусные, абсурдные и неожиданные истории про ИИ; "
                "не возвращай сухие пресс-релизы, регуляторику, инвестиции, обзоры моделей и новости «компания представила модель». "
                f"Искать ТОЛЬКО на доменах: {', '.join(batch)}. ({site_part}) "
                f"{agg_hint}{product_excludes}"
            )
            logger.info(
                "Курьёз-поиск: %s | hosts=%s limit=%s",
                tier_label,
                ",".join(batch),
                per_batch_limit,
            )
            batch_urls = fetch_article_urls_raw_merged(
                settings,
                query,
                limit=per_batch_limit,
                proxy=proxy,
                search_context_size=search_context_size,
                include_domains=list(batch),
                allowed_hosts=list(batch),
                curious_search=True,
            )
            _accept(batch_urls)
        if len(merged) >= collect_cap:
            break
    if merged:
        merged.sort(key=_rank_url)
        logger.info(
            "Курьёз-поиск: итого URL | count=%s hosts=%s cap=%s return=%s",
            len(merged),
            len(host_counts),
            collect_cap,
            min(len(merged), max(fetch_limit * 2, fetch_limit + 12)),
        )
    return_cap = max(fetch_limit * 2, fetch_limit + 12, 48)
    return merged[: min(len(merged), return_cap)]


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
                # Серверная фильтрация по свежести: прошлая неделя. Перекрывает 3 рабочих дня окна + буфер,
                # сильно снижает долю старых evergreen-статей из архивов tier-сайтов (habr, vc.ru и т.д.).
                "tbs": "qdr:w",
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


def _tavily_search_urls(
    api_key: str,
    query: str,
    limit: int,
    *,
    include_domains: list[str] | None = None,
) -> list[str]:
    try:
        payload: dict[str, Any] = {
            "api_key": api_key,
            "query": query,
            "max_results": min(limit, 20),
            "search_depth": "basic",
        }
        if include_domains:
            payload["include_domains"] = [str(x).strip() for x in include_domains if str(x).strip()][:20]
        r = requests.post(
            "https://api.tavily.com/search",
            json=payload,
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
