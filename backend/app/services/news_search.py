"""Веб-поиск якорных URL: ProxyAPI (OpenAI web_search) → SerpAPI → Tavily."""

from __future__ import annotations

import json
import logging
import re
import time
from contextvars import ContextVar
from datetime import date
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

import requests

if TYPE_CHECKING:
    from app.config import Settings
    from app.proxyapi_client import ProxyApiClient

from app.curious_source_policy import (
    curious_aggregator_host_markers,
    curious_host_search_groups,
    get_curious_source_policy,
    is_curious_aggregator_source,
    is_curious_allowed_source,
    is_curious_blocked_host,
    is_curious_policy_source,
)
from app.services.step1_filters import CURIOUS_PREFILTER_DEFAULT_ORDER, record_step1_filter_reject
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
from app.services.curious_tone import curious_raw_url_rank_key
from app.services.step1_candidate_policy import is_product_tool_landing_url, is_support_documentation_url

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
_SECTION_LISTING_PATH_RE = re.compile(
    r"(?:^|/)(?:humor|humour|umor|memes|kek|funny)(?:/|$)",
    re.IGNORECASE,
)
_DTF_USER_PROFILE_RE = re.compile(r"^/id\d+/?$", re.IGNORECASE)
_RIA_PRODUCT_LISTING_RE = re.compile(r"^/product_[\w-]+$", re.IGNORECASE)

_LISTING_EXCEPTION_KEYWORDS: tuple[str, ...] = (
    "top",
    "best",
    "roundup",
    "round-up",
    "funny",
    "fail",
    "fails",
    "crazy",
    "absurd",
    "weird",
    "viral",
    "meme",
    "memes",
    "list",
    "lists",
    "compilation",
    "roundups",
    "fails-of",
    "wtf",
)

_LISTING_EXCEPTION_PATH_RE = re.compile(
    r"(?:^|/|-)(?:"
    + "|".join(re.escape(k) for k in _LISTING_EXCEPTION_KEYWORDS)
    + r")(?:/|$|-)",
    re.IGNORECASE,
)


_SUSPICIOUS_DATE_SEGMENT = re.compile(r"/(\d{8})(?:/|$)")
_URL_PATH_DATE_COMPACT_RE = re.compile(r"/(\d{8})(?:/|\.html?|$)", re.IGNORECASE)
_URL_PATH_DATE_DASH_RE = re.compile(r"/(\d{4})-(\d{1,2})-(\d{1,2})(?:/|$)")
_URL_PATH_DATE_RE = re.compile(r"/(\d{4})/(\d{1,2})/(\d{1,2})(?:/|$)")
_URL_PATH_DATE_DOT_DMY_RE = re.compile(r"/(\d{1,2})\.(\d{1,2})\.(\d{4})(?:/|$)")
_URL_PATH_DATE_SLASH_DMY_RE = re.compile(r"/(\d{1,2})/(\d{1,2})/(\d{4})(?:/|$)")
_URL_PATH_DATE_DASH_DMY_RE = re.compile(r"/(\d{1,2})-(\d{1,2})-(\d{4})(?:/|$)")
_URL_PATH_DATE_EMBEDDED_ISO_RE = re.compile(
    r"(?:^|[/_\-.])(\d{4})-(\d{1,2})-(\d{1,2})(?:[/_\-.]|\.html?|$)",
    re.IGNORECASE,
)
_URL_PATH_MIN_YEAR = 2010
_URL_PATH_MAX_YEAR = 2032


def _url_path_plausible_ymd(year: int, month: int, day: int) -> date | None:
    if year < _URL_PATH_MIN_YEAR or year > _URL_PATH_MAX_YEAR:
        return None
    if month < 1 or month > 12 or day < 1 or day > 31:
        return None
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _url_path_disambiguate_day_month(first: int, second: int, year: int) -> date | None:
    """DD/MM/YYYY vs MM/DD/YYYY: при неоднозначности — европейский DD/MM."""
    if first > 31 or second > 31:
        return None
    if first > 12 and second <= 12:
        return _url_path_plausible_ymd(year, second, first)
    if second > 12 and first <= 12:
        return _url_path_plausible_ymd(year, first, second)
    if first <= 12 and second <= 12:
        return _url_path_plausible_ymd(year, second, first) or _url_path_plausible_ymd(
            year, first, second
        )
    return None


def _url_path_date_from_compact(segment: str) -> date | None:
    if len(segment) != 8 or not segment.isdigit():
        return None
    return _url_path_plausible_ymd(int(segment[:4]), int(segment[4:6]), int(segment[6:8]))


def url_path_publication_day(url: str) -> date | None:
    """Календарный день из path URL: YYYY/MM/DD, YYYYMMDD, DD.MM.YYYY, DD/MM/YYYY и т.п."""
    try:
        path = (urlparse(url).path or "").rstrip("/")
    except Exception:
        return None
    if not path:
        return None

    for pattern in (_URL_PATH_DATE_COMPACT_RE,):
        m = pattern.search(path)
        if m:
            got = _url_path_date_from_compact(m.group(1))
            if got:
                return got

    for pattern in (_URL_PATH_DATE_RE, _URL_PATH_DATE_DASH_RE, _URL_PATH_DATE_EMBEDDED_ISO_RE):
        m = pattern.search(path)
        if not m:
            continue
        got = _url_path_plausible_ymd(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        if got:
            return got

    m = _URL_PATH_DATE_DOT_DMY_RE.search(path)
    if m:
        got = _url_path_plausible_ymd(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        if got:
            return got

    for pattern in (_URL_PATH_DATE_SLASH_DMY_RE, _URL_PATH_DATE_DASH_DMY_RE):
        m = pattern.search(path)
        if not m:
            continue
        got = _url_path_disambiguate_day_month(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        if got:
            return got

    return None
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
_SLUG_ONLY_DATED_EDITORIAL_RE = re.compile(
    r"^/\d{4}/\d{2}/\d{2}/([^/?#]+)$",
    re.IGNORECASE,
)
_SLUG_ONLY_EDITORIAL_HOST_MARKERS = (
    "techcrunch.com",
    "thenextweb.com",
    "wired.com",
    "theverge.com",
)
_LLM_GENERIC_SLUG_PHRASE_RE = re.compile(
    r"(?:^|-)(?:"
    r"ai-company-announces|ai-startup-launches|announces-partnership|"
    r"launches-new-product|partnership-with-major|breakthroughs-20\d{2}|"
    r"automating-marketing|major-retailer|ai-breakthroughs|ai-ethics-debate"
    r")(?:-|$)",
    re.IGNORECASE,
)

_STRICT_CITATIONS_ONLY: ContextVar[bool] = ContextVar("step1_strict_web_search_citations", default=False)


def set_step1_strict_web_search_citations(strict: bool) -> None:
    """Опционально отключить vetted URL из текста модели при нуле citations (по умолчанию выкл.)."""
    _STRICT_CITATIONS_ONLY.set(bool(strict))


def _step1_strict_citations_active() -> bool:
    return bool(_STRICT_CITATIONS_ONLY.get())


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


def publication_day_from_url_candidates(*urls: str | None) -> date | None:
    """Первая распознанная дата в path среди переданных URL (seed, финальный и т.д.)."""
    seen: set[str] = set()
    for raw in urls:
        u = str(raw or "").strip()
        if not u.startswith("http"):
            continue
        key = u.lower().rstrip("/")
        if key in seen:
            continue
        seen.add(key)
        pub_day = url_path_publication_day(u)
        if pub_day is not None:
            return pub_day
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


def _slug_only_editorial_synthetic(host: str, norm_path: str) -> bool:
    """Slug-only /YYYY/MM/DD/title/ на TechCrunch и др. — типичная галлюцинация web_search."""
    if not any(marker in host for marker in _SLUG_ONLY_EDITORIAL_HOST_MARKERS):
        return False
    m = _SLUG_ONLY_DATED_EDITORIAL_RE.match(norm_path)
    if not m:
        return False
    slug = m.group(1).lower()
    if _LLM_GENERIC_SLUG_PHRASE_RE.search(slug):
        return True
    return slug.count("-") >= 5 and len(slug) >= 48 and bool(_LLM_GENERIC_SLUG_TAIL_RE.search(slug))


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
    norm_path = path if path.startswith("/") else f"/{path}"
    if _slug_only_editorial_synthetic(host, norm_path):
        return True
    if not any(marker in host for marker in _SYNTHETIC_EDITORIAL_HOST_MARKERS):
        return False
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
    is_enabled: Any | None = None,
    curious_strict: bool = False,
    tier_strict: bool = False,
) -> str | None:
    """Отсев сырого URL сразу после web_search (до HTTP шага 1)."""
    u = (url or "").strip()
    if not u.startswith("http"):
        code = "invalid_url"
        if is_enabled is None or is_enabled(code):
            record_step1_filter_reject(code)
            return code
        return None
    pre = search_url_prefilter_reason(
        u,
        is_enabled=is_enabled,
        curious_strict=curious_strict,
        tier_strict=tier_strict,
        earliest=earliest,
        anchor=anchor,
    )
    if pre:
        return pre
    if search_url_path_date_outside_window(u, earliest=earliest, anchor=anchor):
        if is_enabled is None or is_enabled("published_before_window"):
            record_step1_filter_reject("published_before_window")
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
        parsed = urlparse(u)
        path = (parsed.path or "").rstrip("/")
        host = (parsed.hostname or "").lower()
    except Exception:
        return True
    if host in ("example.com", "www.example.com") or host.endswith(".example") or "invented" in host:
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


def is_curated_list_url(url: str) -> bool:
    """Редакционная подборка (top-10, funny fails) — разворачивать, не отбраковывать на prefilter."""
    u = (url or "").strip()
    if not u.startswith("http"):
        return False
    if is_topic_pool_page_url(u):
        return False
    try:
        path = (urlparse(u).path or "").lower().rstrip("/") or "/"
    except Exception:
        return False
    if path in ("", "/"):
        return False
    return bool(_LISTING_EXCEPTION_PATH_RE.search(path))


def is_listing_page_url(url: str) -> bool:
    """URL ведёт на ленту/рубрику/индекс, а не на одну статью (эвристика по path)."""
    u = (url or "").strip()
    if not u.startswith("http"):
        return False
    if is_curated_list_url(u):
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
    if _SECTION_LISTING_PATH_RE.search(path):
        return True
    if "dtf.ru" in host and _DTF_USER_PROFILE_RE.match(path):
        return True
    if _RIA_PRODUCT_LISTING_RE.match(path):
        return True
    return False


def is_editorial_listing_title(title: str) -> bool:
    """Заголовок ленты/рубрики/профиля — не отдельная статья (после HTTP)."""
    t = (title or "").strip()
    if len(t) < 8:
        return False
    low = t.lower()
    if re.search(r"^новост\w*\s+\S", t, flags=re.IGNORECASE):
        return True
    if re.search(r"^новости\s*,\s*\d", t, flags=re.IGNORECASE):
        return True
    if re.search(r"^юмор\s*—", t, flags=re.IGNORECASE):
        return True
    if "все статьи и новости" in low or "все материалы по теме" in low:
        return True
    if "последние новости сегодня" in low:
        return True
    if re.match(r"^.{1,120}\(id\d+\)\s*$", t):
        return True
    return False


def search_url_prefilter_reason(
    url: str,
    is_enabled: Any | None = None,
    order: list[str] | None = None,
    *,
    tier_strict: bool = False,
    curious_strict: bool = False,
    allow_curious_tiers_in_serious: bool = False,
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
            curious_strict
            and not is_curious_allowed_source(u)
            and not is_curious_blocked_host(u)
        )
        or (
            tier_strict
            and not curious_strict
            and not is_policy_tier_source(u)
            and not (allow_curious_tiers_in_serious and is_curious_policy_source(u))
        ),
        "aggregator_source": (
            is_curious_aggregator_source(u) if curious_strict else is_aggregator_source(u)
        ),
        "forbidden_media_source": (
            is_curious_blocked_host(u) if curious_strict else is_blocked_search_host(u)
        ),
        "news_listing_page": (
            not is_curated_list_url(u)
            and (
                path in ("", "/")
                or any(x in path for x in ("/search", "/tag/", "/tags/", "/category/", "/topics/"))
                or path.endswith("/neiroseti")
                or path.endswith("/artificial_intelligence")
                or path.endswith("/artificial-intelligence")
                or path in ("/artificial-intelligence", "/ai", "/topic/artificial-intelligence")
                or bool(re.search(r"^/articles/[\w_-]+$", path))
                or bool(_RIA_PRODUCT_LISTING_RE.match(path))
                or is_search_noise_url(u)
                or is_listing_page_url(u)
                or is_topic_pool_page_url(u)
            )
        ),
        "support_documentation_page": is_support_documentation_url(u),
        "llm_hallucinated_url": url_suspected_hallucinated(u),
        "product_tool_page": is_product_tool_landing_url(u),
        "published_before_window": search_url_path_date_outside_window(
            u, earliest=earliest, anchor=anchor
        ),
    }
    sequence = [x for x in (order or []) if x in checks]
    default_tail = (
        tuple(x for x in CURIOUS_PREFILTER_DEFAULT_ORDER if x in checks)
        if curious_strict
        else (
            "non_policy_source",
            "aggregator_source",
            "forbidden_media_source",
            "news_listing_page",
            "support_documentation_page",
            "llm_hallucinated_url",
            "product_tool_page",
            "published_before_window",
        )
    )
    for fid in default_tail:
        if fid not in sequence:
            sequence.append(fid)
    for fid in sequence:
        if enabled(fid) and checks.get(fid):
            record_step1_filter_reject(fid)
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


def is_step1_listing_seed_url(url: str) -> bool:
    """
    URL ленты/рубрики/агрегатора: разворачивать в статьи на шаге 1, не брать как кандидата.
    Шире, чем is_listing_page_url — совпадает с эвристикой prefilter news_listing_page.
    """
    u = (url or "").strip()
    if not u.startswith("http"):
        return False
    if is_curated_list_url(u):
        return True
    if is_listing_page_url(u) or is_topic_pool_page_url(u):
        return True
    if is_curious_aggregator_source(u) or is_aggregator_source(u):
        return True
    try:
        path = (urlparse(u).path or "").rstrip("/").lower() or "/"
    except Exception:
        return False
    if path in ("", "/"):
        return True
    if any(x in path for x in ("/search", "/tag/", "/tags/", "/category/", "/topics/")):
        return True
    if path.endswith("/neiroseti"):
        return True
    if path.endswith("/artificial_intelligence") or path.endswith("/artificial-intelligence"):
        return True
    if path in ("/artificial-intelligence", "/ai", "/topic/artificial-intelligence"):
        return True
    if re.search(r"^/articles/[\w_-]+$", path):
        return True
    if _RIA_PRODUCT_LISTING_RE.match(path):
        return True
    if is_search_noise_url(u):
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
    proxy_fallback_on_empty: bool = True,
    curious_search: bool = False,
    current_verified: int = 0,
    pool_shortfall: int = 0,
) -> list[str]:
    """
    Сырые уникальные URL (до _filter_search_urls).
    При web_search_prefer_alt_providers: SerpAPI/Tavily первыми, ProxyAPI — если мало URL или force_proxyapi.
    """
    fetch_cap = max(limit, 15)
    prefer_alt = bool(getattr(settings, "step1_web_search_prefer_alt_providers", False))
    if int(pool_shortfall or 0) > 0 and getattr(settings, "serpapi_api_key", None):
        prefer_alt = True
    min_before_proxy = max(0, int(getattr(settings, "step1_min_urls_before_proxyapi", 5) or 5))
    merged: list[str] = []

    from app.services.step1_phase_timers import step1_phase

    with step1_phase("alt_search"):
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

    from app.services.step1_web_search_stats import (
        current_step1_web_search_stats,
        record_empty_citation_web_search,
        step1_web_search_api_cap_reached,
        step1_web_search_api_cap_should_stop,
        step1_web_search_cost_budget_exceeded,
        step1_web_search_cost_budget_should_stop,
        step1_strict_web_search_economy,
    )

    cost_budget_blocks = step1_web_search_cost_budget_should_stop(
        verified_count=int(current_verified or 0),
        pool_shortfall=int(pool_shortfall or 0),
    )
    api_cap_blocks = step1_web_search_api_cap_should_stop(
        verified_count=int(current_verified or 0),
        pool_shortfall=int(pool_shortfall or 0),
    )

    need_proxy = force_proxyapi or not prefer_alt or len(merged) < min_before_proxy
    if step1_strict_web_search_economy(settings):
        # cap=1 — только responses; cap>=2 — второй слот под preview при пустых citations.
        proxy_fallback_on_empty = int(getattr(settings, "step1_max_web_search_api_calls", 0) or 0) >= 2
    if need_proxy and settings.enable_web_fetch and settings.proxyapi_web_search_enabled and proxy is not None:
        ws = current_step1_web_search_stats()
        streak = int(getattr(ws, "empty_citation_streak", 0) or 0) if ws else 0
        empty_streak_skip = (
            ws is not None
            and streak >= 4
            and not force_proxyapi
            and (
                api_cap_blocks
                or cost_budget_blocks
            )
        )
        if (
            cost_budget_blocks
            or (
                current_step1_web_search_stats() is not None
                and current_step1_web_search_stats().api_cap is not None
                and api_cap_blocks
            )
            or empty_streak_skip
        ):
            if cost_budget_blocks:
                logger.warning(
                    "Веб-поиск: ProxyAPI пропущен — лимит ₽ web_search шага 1 | query=%s",
                    query[:120],
                )
            elif empty_streak_skip:
                logger.warning(
                    "Веб-поиск: ProxyAPI пропущен — серия пустых citation URL (%s) | query=%s",
                    getattr(ws, "empty_citation_streak", 0),
                    query[:120],
                )
            else:
                logger.warning(
                    "Веб-поиск: ProxyAPI пропущен — лимит web_search API шага 1 | query=%s",
                    query[:120],
                )
            # #region agent log
            try:
                from app.services.agent_debug_log import agent_debug_log
                from app.services.step1_web_search_stats import current_step1_web_search_stats

                _ws = current_step1_web_search_stats()
                agent_debug_log(
                    "H3",
                    "news_search.fetch_article_urls_raw_merged:proxy_skipped",
                    "proxyapi_not_called",
                    {
                        "query_head": query[:80],
                        "cost_budget": cost_budget_blocks,
                        "api_cap_reached": step1_web_search_api_cap_reached(),
                        "empty_streak_skip": empty_streak_skip,
                        "api_calls": getattr(_ws, "api_calls", None) if _ws else None,
                        "api_cap": getattr(_ws, "api_cap", None) if _ws else None,
                    },
                )
            except Exception:
                pass
            # #endregion
        else:
            with step1_phase("web_search"):
                proxy_urls: list[str] | None = None
                cache_used = False
                use_cache = not force_proxyapi and bool(getattr(settings, "step1_web_search_cache_enabled", True))
                if use_cache:
                    from app.services.step1_web_search_cache import get_cached_proxy_search_urls

                    proxy_urls = get_cached_proxy_search_urls(
                        settings,
                        query=query,
                        limit=fetch_cap,
                        search_context_size=search_context_size,
                        allowed_hosts=allowed_hosts,
                        curious_search=curious_search,
                        proxy_fallback_on_empty=proxy_fallback_on_empty,
                    )
                    if proxy_urls:
                        cache_used = True
                if proxy_urls is None:
                    try:
                        proxy_urls = proxy.search_news_article_urls(
                            query,
                            limit=fetch_cap,
                            search_context_size=search_context_size,
                            allowed_hosts=allowed_hosts,
                            fallback_on_empty=proxy_fallback_on_empty,
                            curious_search=curious_search,
                        )
                        if use_cache and proxy_urls:
                            from app.services.step1_web_search_cache import store_proxy_search_urls_cache

                            store_proxy_search_urls_cache(
                                settings,
                                proxy_urls,
                                query=query,
                                limit=fetch_cap,
                                search_context_size=search_context_size,
                                allowed_hosts=allowed_hosts,
                                curious_search=curious_search,
                                proxy_fallback_on_empty=proxy_fallback_on_empty,
                            )
                    except Exception:
                        logger.warning("ProxyAPI web_search: ошибка запроса", exc_info=True)
                        proxy_urls = []
                if proxy_urls:
                    merged = _uniq_urls([*merged, *proxy_urls])
                    logger.info(
                        "Веб-поиск: ProxyAPI web_search | count=%s prefer_alt=%s force=%s cache=%s",
                        len(proxy_urls),
                        prefer_alt,
                        force_proxyapi,
                        cache_used,
                    )

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
    pool_shortfall: int = 0,
    deadline_monotonic: float | None = None,
    digest_id: int | None = None,
    proxyapi_spent_rub: float = 0.0,
) -> list[str]:
    """
    Поиск строго по tier-1 → tier-4 из source_tiers.txt: батчи site: запросов по хостам политики.
    URL вне tier-1…4 не попадают в результат.
    """
    p = policy or get_source_tiers_policy()
    seeds = tuple(seed_urls or p.search_seed_urls)
    from app.services.step1_web_search_stats import (
        step1_hard_cost_limit_exceeded,
        step1_strict_web_search_economy,
        step1_web_search_api_cap_should_stop,
        step1_web_search_cost_budget_should_stop,
    )

    hard_limit_rub = float(getattr(settings, "step1_hard_stop_cost_rub", 100) or 100)

    def _abort_search() -> bool:
        if digest_id is not None:
            from app.services.step1_cancellation import is_cancelled

            if is_cancelled(digest_id):
                return True
        return step1_hard_cost_limit_exceeded(
            proxyapi_spent_rub=proxyapi_spent_rub,
            hard_limit_rub=hard_limit_rub,
        )

    economy = step1_strict_web_search_economy(settings)
    short_pool = max(0, int(current_verified or 0)) < 10 or max(0, int(pool_shortfall or 0)) > 0
    if short_pool:
        # Как в курьёзной ветке: больше URL на батч и не останавливаться на первой «достаточной» выдаче.
        per_batch_limit = max(14, min(32, max(fetch_limit // 3, 16)))
        raw_target = max(20, min(fetch_limit, 36))
        min_unique_hosts_target = max(6, min(12, max(fetch_limit // 3, 8)))
        max_urls_per_host = max(3, min(5, max(fetch_limit // 10, 3)))
    else:
        per_batch_limit = max(6, min(12, max(fetch_limit // 6, 6)))
        raw_target = max(14, min(fetch_limit, 22))
        min_unique_hosts_target = max(6, min(10, max(fetch_limit // 2, 6)))
        max_urls_per_host = max(2, min(4, max(fetch_limit // 12, 2)))
    merged: list[str] = []
    seen: set[str] = set()
    host_counts: dict[str, int] = {}
    max_batches = max(1, int(getattr(settings, "step1_tier_max_web_search_batches", 6) or 6))
    if short_pool and not economy:
        max_batches = max(max_batches, 10)
        if pool_shortfall >= 5:
            max_batches = max(max_batches, 14)
    batches_used = 0
    medium_escalations_used = 0
    preview_fallbacks_used = 0
    max_medium_escalations = 0 if economy else 3
    max_preview_fallbacks = 0 if economy else 1
    window_earliest, window_anchor = parse_search_window_dates(window_prefix)
    ru_hosts_seen = 0
    tier_ctx = str(search_context_size or "low").strip().lower() or "low"
    if short_pool and not economy and tier_ctx not in ("medium", "high"):
        tier_ctx = "low"
    tier_phase_started = time.monotonic()
    tier_phase_wall_sec = (
        150 if short_pool and not economy and pool_shortfall >= 5 else (90 if short_pool and not economy else None)
    )
    empty_batch_streak = 0
    tier_api_cap_hit = False

    def _enough_coverage() -> bool:
        if short_pool and pool_shortfall >= 5:
            return False
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
        if tier_api_cap_hit or _abort_search():
            break
        if tier_phase_wall_sec is not None and (time.monotonic() - tier_phase_started) >= tier_phase_wall_sec:
            logger.warning(
                "Tier-поиск: остановка по лимиту фазы | merged=%s batches=%s wall_sec=%s",
                len(merged),
                batches_used,
                tier_phase_wall_sec,
            )
            break
        if deadline_monotonic is not None and time.monotonic() >= deadline_monotonic:
            logger.warning("Tier-поиск: остановка по лимиту времени | batches=%s", batches_used)
            break
        if not hosts:
            continue
        base_tier = tier_label.removesuffix("-defer")
        if current_verified >= 10 and base_tier in ("Tier-3", "Tier-4"):
            continue
        if tier_label.endswith("-defer"):
            # Дорогие батчи с частыми 404: только если RU-выдача не наполнила пул.
            if short_pool and len(merged) < 6:
                continue
            if _enough_coverage() or (ru_hosts_seen >= 2 and len(merged) >= max(8, raw_target // 2)):
                continue
        if _enough_coverage():
            break
        for batch in batched_site_host_groups(hosts, batch_size=3):
            if _abort_search():
                logger.warning(
                    "Tier-поиск: остановка — отмена или жёсткий лимит ₽ | batches=%s merged=%s",
                    batches_used,
                    len(merged),
                )
                tier_api_cap_hit = True
                break
            if step1_web_search_api_cap_should_stop(
                verified_count=int(current_verified or 0),
                pool_shortfall=int(pool_shortfall or 0),
            ) or step1_web_search_cost_budget_should_stop(
                verified_count=int(current_verified or 0),
                pool_shortfall=int(pool_shortfall or 0),
                proxyapi_spent_rub=proxyapi_spent_rub,
                hard_limit_rub=hard_limit_rub,
            ):
                logger.warning(
                    "Tier-поиск: остановка — лимит web_search API/₽ | batches=%s merged=%s",
                    batches_used,
                    len(merged),
                )
                tier_api_cap_hit = True
                break
            if deadline_monotonic is not None and time.monotonic() >= deadline_monotonic:
                logger.warning("Tier-поиск: остановка по лимиту времени в батче | tier=%s", tier_label)
                break
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
                search_context_size=tier_ctx,
                include_domains=include_domains,
                allowed_hosts=allowed_hosts,
                force_proxyapi=short_pool and not economy,
                proxy_fallback_on_empty=(short_pool and not economy) or economy,
                current_verified=current_verified,
                pool_shortfall=pool_shortfall,
            )
            if (
                not batch_urls
                and not economy
                and tier_ctx == "low"
                and (tier_label in ("Tier-1", "Tier-2") or has_aggregator_hosts or short_pool)
                and medium_escalations_used < max_medium_escalations
            ):
                medium_escalations_used += 1
                use_preview_fallback = (
                    not economy
                    and has_aggregator_hosts
                    and preview_fallbacks_used < max_preview_fallbacks
                )
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
                    proxy_fallback_on_empty=use_preview_fallback or (short_pool and not economy),
                    force_proxyapi=short_pool and not economy,
                    current_verified=current_verified,
                    pool_shortfall=pool_shortfall,
                )
            batches_used += 1
            if not batch_urls:
                empty_batch_streak += 1
            else:
                empty_batch_streak = 0
            if short_pool and empty_batch_streak >= 4 and len(merged) >= 4:
                logger.warning(
                    "Tier-поиск: остановка — серия пустых батчей при достаточном merged | merged=%s",
                    len(merged),
                )
                break
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
    max_search_batches: int | None = None,
    skip_aggregator_tier: bool = False,
    pool_shortfall: int = 0,
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
    collect_cap = max(fetch_limit * 3, fetch_limit + 24, 48) if max_search_batches else max(
        fetch_limit * 4, fetch_limit + 40, 80
    )
    agg_markers = curious_aggregator_host_markers(p)
    search_batches_used = 0
    escalate_shortfall = max(0, int(pool_shortfall or 0))

    def _host_is_aggregator_marker(host: str) -> bool:
        h = (host or "").lower()
        return bool(h) and any(m in h for m in agg_markers)

    window_earliest, window_anchor = parse_search_window_dates(window_prefix)

    def _rank_url(url: str) -> tuple[int, int, int, int, int]:
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
        return curious_raw_url_rank_key(
            url,
            fresh_rank=fresh_rank,
            source_rank=source_rank,
            day_ordinal=day.toordinal() if day else None,
        )

    def _accept(urls: list[str]) -> None:
        for raw in urls:
            u = str(raw or "").strip()
            if not u.startswith("http"):
                continue
            is_aggregator = is_curious_aggregator_source(u, p)
            if not is_aggregator and not is_curious_allowed_source(u, p):
                continue
            if search_url_path_date_outside_window(
                u, earliest=window_earliest, anchor=window_anchor
            ):
                continue
            if search_url_raw_reject_reason(
                u,
                earliest=window_earliest,
                anchor=window_anchor,
                curious_strict=True,
                is_enabled=lambda fid: fid != "aggregator_source",
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
        if skip_aggregator_tier and tier_label == "Curious-T2-Aggregators":
            continue
        search_hosts = hosts
        if tier_label in ("Curious-T1", "Curious-T2"):
            search_hosts = tuple(h for h in hosts if not _host_is_aggregator_marker(h))
            if not search_hosts:
                continue
        host_batch_groups: list[tuple[str, ...]]
        if tier_label == "Curious-T1":
            ru_ent_hosts = tuple(h for h in search_hosts if h in p.curious_ru_entertainment_hosts)
            other_hosts = tuple(h for h in search_hosts if h not in p.curious_ru_entertainment_hosts)
            host_batch_groups = [
                *batched_site_host_groups(ru_ent_hosts, batch_size=3),
                *batched_site_host_groups(other_hosts, batch_size=3),
            ]
        else:
            host_batch_groups = list(batched_site_host_groups(search_hosts, batch_size=3))
        # В curious-поиске нельзя останавливаться на первой группе доменов:
        # иначе cap заполняется старым evergreen-контентом и поиск не доходит до Habr/VC/foreign.
        if len(merged) >= collect_cap:
            break
        for batch in host_batch_groups:
            if (
                max_search_batches is not None
                and search_batches_used >= max_search_batches
                and escalate_shortfall < 5
            ):
                break
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
                pool_shortfall=escalate_shortfall,
            )
            search_batches_used += 1
            _accept(batch_urls)
        if (
            max_search_batches is not None
            and search_batches_used >= max_search_batches
            and escalate_shortfall < 5
        ):
            break
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
    return_cap = max(fetch_limit * 3, fetch_limit + 24, 60)
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


def _vetted_model_urls_from_text(text: str, limit: int) -> list[str]:
    """URL из текста модели: без галлюцинаций и с prefilter — fallback при пустых citations."""
    raw = (text or "").strip()
    if not raw:
        return []
    stripped = _strip_markdown_fence(raw)
    raw_urls: list[str] = []
    try:
        if stripped.startswith("["):
            parsed = json.loads(stripped)
            if isinstance(parsed, list):
                raw_urls = [
                    u.strip()
                    for u in parsed
                    if isinstance(u, str) and u.strip().startswith("http")
                ]
    except json.JSONDecodeError:
        pass
    if not raw_urls:
        found = re.findall(r"https?://[^\s\]\)\"'<>]+", raw)
        raw_urls = [u.rstrip(".,;:)") for u in found if u.startswith("http")]
    out: list[str] = []
    for u in raw_urls:
        if url_suspected_hallucinated(u):
            continue
        if search_url_prefilter_reason(u):
            continue
        out.append(u.split("#")[0])
        if len(out) >= limit:
            break
    return out


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


_CITATION_ANNOTATION_TYPES = frozenset({"url_citation", "citation", "web_search_result"})


def _url_from_annotation(ann: Any) -> str | None:
    if ann is None:
        return None
    if isinstance(ann, dict):
        ann_type = str(ann.get("type") or "").strip().lower()
        url = ann.get("url")
    else:
        ann_type = str(getattr(ann, "type", None) or "").strip().lower()
        url = getattr(ann, "url", None)
    if not isinstance(url, str) or not url.startswith("http"):
        return None
    if ann_type and ann_type not in _CITATION_ANNOTATION_TYPES:
        return None
    return url.split("#")[0]


def _citation_urls_from_response_output(response: Any) -> list[str]:
    urls: list[str] = []
    for item in getattr(response, "output", None) or []:
        content = getattr(item, "content", None) or []
        for block in content:
            for ann in getattr(block, "annotations", None) or []:
                url = _url_from_annotation(ann)
                if url:
                    urls.append(url)
    return urls


def _response_text_parts(response: Any) -> list[str]:
    text_parts: list[str] = []
    output_text = getattr(response, "output_text", None)
    if isinstance(output_text, str) and output_text.strip():
        text_parts.append(output_text)
    for item in getattr(response, "output", None) or []:
        content = getattr(item, "content", None) or []
        for block in content:
            if getattr(block, "type", None) == "output_text":
                text_parts.append(getattr(block, "text", "") or "")
    return text_parts


def _finalize_web_search_urls(
    citation_urls: list[str],
    text_parts: list[str],
    limit: int,
    *,
    citations_only: bool,
    log_channel: str,
) -> list[str]:
    """Citations в приоритете; при нехватке — проверенные URL из текста модели (без галлюцинаций)."""
    joined = "\n".join(text_parts)
    if not citations_only:
        model_urls: list[str] = []
        for text in text_parts:
            model_urls.extend(extract_http_urls_from_text(text, limit))
        try:
            from app.services.step1_web_search_stats import record_web_search_citation_urls

            record_web_search_citation_urls(len(citation_urls), model_urls_dropped=0)
        except ImportError:
            pass
        return _uniq_urls(_filter_search_urls([*citation_urls, *model_urls], limit))

    out = _uniq_urls(_filter_search_urls(citation_urls, limit))
    dropped = len(extract_http_urls_from_text(joined, limit)) if joined.strip() else 0
    supplemented = 0
    strict = _step1_strict_citations_active()
    allow_vetted = len(out) < limit and joined.strip()
    if strict and len(citation_urls) == 0:
        allow_vetted = False
    if allow_vetted:
        vetted = _vetted_model_urls_from_text(joined, limit)
        seen = {u.lower().rstrip("/") for u in out}
        for u in vetted:
            key = u.lower().rstrip("/")
            if key in seen:
                continue
            seen.add(key)
            out.append(u)
            supplemented += 1
            if len(out) >= limit:
                break
        if supplemented:
            if out and len(citation_urls) == 0:
                logger.info(
                    "Web search: vetted model URL fallback (%s) | count=%s dropped_raw=%s",
                    log_channel,
                    len(out),
                    dropped,
                )
            else:
                logger.info(
                    "Web search: vetted supplement (%s) | citations=%s added=%s total=%s",
                    log_channel,
                    len(citation_urls),
                    supplemented,
                    len(out),
                )
        elif dropped and not out:
            logger.info(
                "Web search: citations-only — отброшено URL из текста модели | dropped=%s",
                dropped,
            )
    try:
        from app.services.step1_web_search_stats import record_web_search_citation_urls

        record_web_search_citation_urls(len(citation_urls), model_urls_dropped=max(0, dropped - supplemented))
    except ImportError:
        pass
    return out


def extract_urls_from_chat_web_search_response(
    response: Any,
    limit: int = 20,
    *,
    citations_only: bool = True,
) -> list[str]:
    """URL из chat completions + web_search_options: citations в приоритете, vetted-добор при нехватке."""
    citation_urls: list[str] = []
    choices = getattr(response, "choices", None) or []
    if not choices:
        return []
    message = getattr(choices[0], "message", None)
    if message is None:
        return []
    for ann in getattr(message, "annotations", None) or []:
        url = _url_from_annotation(ann)
        if url:
            citation_urls.append(url)
    text = getattr(message, "content", None) or ""
    text_parts = [text] if isinstance(text, str) and text.strip() else []
    return _finalize_web_search_urls(
        citation_urls,
        text_parts,
        limit,
        citations_only=citations_only,
        log_channel="chat",
    )


def extract_urls_from_responses_payload(
    response: Any,
    limit: int = 20,
    *,
    citations_only: bool = True,
) -> list[str]:
    """Responses API: citations в приоритете; vetted URL из текста — если citations пусты или их мало."""
    citation_urls = _citation_urls_from_response_output(response)
    text_parts = _response_text_parts(response)
    return _finalize_web_search_urls(
        citation_urls,
        text_parts,
        limit,
        citations_only=citations_only,
        log_channel="responses",
    )


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
