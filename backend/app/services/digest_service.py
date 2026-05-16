import json
import logging
from contextlib import contextmanager
import html
import re
import shutil
import time
from datetime import date, datetime, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse, urljoin
from zoneinfo import ZoneInfo

import requests
from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import get_settings
from app.crew.model_policy import AGENT_MODEL_RECOMMENDATIONS, PRICING_RUB, STEP2_AI_ORDER_MODEL
from app.crew.workflow import CrewWorkflow, complete_analytics_result, current_msk_iso
from app.services.platform_assembly import digest_docx_filename, format_digest_date_ru
from app.models import (
    Analytics,
    Asset,
    Digest,
    FinalOutput,
    LlmCostRecord,
    NewsCandidate,
    QualityCheck,
    SelectedNews,
    Step1DiscoveredNews,
    Step1DiscoveryRun,
    Step1ManualRatingLog,
)
from app.services.step1_manual_ratings_export import sync_step1_manual_ratings_export
from app.proxyapi_client import ProxyApiClient
from app.services.cost_tracker import ProxyApiCostTracker, record_today_balance, proxyapi_spent_today_rub
from app.services.export_service import build_docx
from app.services.news_search import (
    fetch_article_urls_from_search,
    is_listing_page_url,
    is_topic_pool_page_url,
    url_suspected_hallucinated,
)

logger = logging.getLogger("app.digest")
MSK_TZ = ZoneInfo("Europe/Moscow")

STATUS_DRAFT = "draft"
STATUS_STEP0 = "step_0"
STATUS_STEP1 = "step_1_candidates"
STATUS_SELECTED = "selected"
STATUS_ANALYTICS = "analytics_ready"
STATUS_FINAL = "final_ready"

STEP4_PLATFORMS = ("telegram", "max", "vk", "dzen")
STEP4_IMAGE_VARIANT_COUNT = 4

STEP1_TARGET_VERIFIED = 10
STEP1_MIN_VERIFIED = 5
STEP1_SUPPLEMENT_MAX_ROUNDS = 5
STEP1_PRE_CREW_SUPPLEMENT_ROUNDS = 3

ASSET_PROXYAPI_BUDGET_ALERT = "step1_proxyapi_budget_exceeded"
PROXYAPI_BUDGET_USER_MESSAGE = (
    "Исчерпан бюджет API-ключа ProxyAPI (ошибка 402). "
    "Пополните счёт в личном кабинете proxyapi.ru "
    "или измените ограничения бюджета ключа в настройках ProxyAPI."
)
STEP1_DISCOVERED_REASON_CODES = {
    "published_out_of_range",
    "http_unreachable",
    "url_redirect_mismatch",
    "off_topic_not_ai",
    "other",
}

REJECT_REASON_PREFIX = "REJECT_REASON:"
AGGREGATOR_HOST_MARKERS = (
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
TIER1_HOST_MARKERS = ("openai.com", "anthropic.com", "ai.google.dev", "deepmind.google", "microsoft.com")
TIER2_HOST_MARKERS = ("techcrunch.com", "theverge.com", "wired.com", "venturebeat.com", "arstechnica.com")

def _is_placeholder_candidate_dict(item: dict[str, Any]) -> bool:
    """Резервный набор из workflow при сбое парсинга JSON (не реальные новости)."""
    url = str(item.get("url", "")).lower()
    title = str(item.get("title", ""))
    if "example.com/ai-news" in url:
        return True
    if title.startswith("AI Candidate"):
        return True
    return False


def _manual_required_dict(item: dict[str, Any]) -> bool:
    return "MANUAL_REQUIRED:" in str(item.get("verification_comment") or "")


def _reject_reason_codes(comment: str | None) -> list[str]:
    if not comment:
        return []
    codes: list[str] = []
    for token in str(comment).split():
        if token.startswith(REJECT_REASON_PREFIX):
            code = token.removeprefix(REJECT_REASON_PREFIX).strip()
            if code and code not in codes:
                codes.append(code)
    return codes


def _append_reject_reason(item: dict[str, Any], code: str) -> None:
    if not code:
        return
    existing = str(item.get("verification_comment") or "").strip()
    marker = f"{REJECT_REASON_PREFIX}{code}"
    if marker in existing:
        return
    item["verification_comment"] = f"{existing} {marker}".strip()


def _append_url_audit(item: dict[str, Any], stage: str, original_url: str, final_url: str) -> None:
    left = str(original_url or "").strip()[:260]
    right = str(final_url or "").strip()[:260]
    if not left or not right:
        return
    marker = f"URL_AUDIT:{stage}|from={left}|to={right}"
    existing = str(item.get("verification_comment") or "").strip()
    if marker in existing:
        return
    item["verification_comment"] = f"{existing} {marker}".strip()


def _editorial_headline_rejected(title: str) -> bool:
    """
    Отсекаем «технические» заголовки (только id документа, длинный числовой префикс без нормального заголовка),
    чтобы в карточке была человекочитаемая новость, а не номер реестра.
    """
    t = _strip_site_branding(_clean_headline_text(title))
    if len(t) < 8:
        return True
    letters = [c for c in t if c.isalpha()]
    if len(letters) < 5:
        return True
    if re.match(r"^\d{8,}\b", t):
        return True
    digit_count = sum(1 for c in t if c.isdigit())
    alnum = digit_count + len(letters)
    if alnum > 0 and digit_count / alnum > 0.55:
        return True
    core_digits = re.sub(r"\D", "", t)
    if len(core_digits) >= 10 and len(letters) < 8:
        return True
    return False


def _clean_headline_text(raw: str) -> str:
    s = html.unescape(re.sub(r"\s+", " ", raw)).strip()
    s = re.sub(r"\s+", " ", s)
    return s


def _strip_site_branding(title: str) -> str:
    """Убирает хвост « | Сайт » / « — Сайт », если основная часть достаточно длинная."""
    t = title.strip()
    for sep in (" | ", " – ", " — ", " - "):
        if sep not in t:
            continue
        left, right = t.split(sep, 1)
        left, right = left.strip(), right.strip()
        if len(left) >= 12 and len(right) <= 48 and len(left) >= len(right):
            return left
    return t


def _title_primarily_russian(title: str) -> bool:
    letters = [c for c in title if c.isalpha()]
    if not letters:
        return True
    cyr = sum(1 for c in letters if "\u0400" <= c <= "\u04FF")
    return cyr / len(letters) >= 0.35


def _meta_property_content(chunk: str, prop: str) -> str | None:
    pat = rf'<meta[^>]+property=["\']{re.escape(prop)}["\'][^>]+content=["\']([^"\']+)["\']'
    m = re.search(pat, chunk, re.IGNORECASE | re.DOTALL)
    if m:
        return _clean_headline_text(m.group(1))
    pat2 = rf'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']{re.escape(prop)}["\']'
    m2 = re.search(pat2, chunk, re.IGNORECASE | re.DOTALL)
    return _clean_headline_text(m2.group(1)) if m2 else None


def _meta_name_content(chunk: str, name: str) -> str | None:
    pat = rf'<meta[^>]+name=["\']{re.escape(name)}["\'][^>]+content=["\']([^"\']+)["\']'
    m = re.search(pat, chunk, re.IGNORECASE | re.DOTALL)
    if m:
        return _clean_headline_text(m.group(1))
    pat2 = rf'<meta[^>]+content=["\']([^"\']+)["\'][^>]+name=["\']{re.escape(name)}["\']'
    m2 = re.search(pat2, chunk, re.IGNORECASE | re.DOTALL)
    return _clean_headline_text(m2.group(1)) if m2 else None


def _url_fingerprint(url: str) -> str:
    """Сравнение «одна и та же страница» без учёта схемы www и хвостового /."""
    try:
        p = urlparse(url.strip())
    except Exception:
        return ""
    host = (p.hostname or "").lower().removeprefix("www.")
    path = (p.path or "").rstrip("/") or "/"
    return f"{host}{path.lower()}"


def _is_site_homepage_url(url: str) -> bool:
    try:
        p = urlparse(url.strip())
    except Exception:
        return False
    path = (p.path or "").strip().rstrip("/")
    return path == ""


def _redirect_should_reject(original_url: str, stored_url: str, bundle: dict[str, Any]) -> bool:
    """
    Отклоняем редирект только если финал — главная или страница без признаков статьи.
    Если HTML уже отдал заголовок материала — ссылка рабочая (типичный редирект поиска → канонический URL).
    """
    if _is_site_homepage_url(stored_url):
        return True
    if _url_fingerprint(original_url) == _url_fingerprint(stored_url):
        return False
    headline = str(bundle.get("headline") or "").strip()
    if len(headline) >= 8:
        return False
    if bool(bundle.get("article_markers")) or bool(bundle.get("soft_article_signals")):
        return False
    try:
        o_host = (urlparse(original_url).hostname or "").lower().removeprefix("www.")
        s_host = (urlparse(stored_url).hostname or "").lower().removeprefix("www.")
        if o_host and s_host and o_host != s_host:
            return True
    except Exception:
        pass
    return True


def _page_is_article_like(bundle: dict[str, Any]) -> bool:
    """Страница похожа на материал, а не на ленту/раздел (смягчённо, как в debug-пайплайне)."""
    if bundle.get("is_listing_page"):
        return False
    if bool(bundle.get("article_markers")) or bool(bundle.get("soft_article_signals")):
        return True
    headline = str(bundle.get("headline") or "").strip()
    corpus = str(bundle.get("topic_corpus") or "")
    if len(headline) >= 8 and len(corpus) >= 120:
        return True
    return False


_LISTING_PATH_HINTS = re.compile(
    r"(?:^|/)(?:neiroseti|artificial_intelligence|neural|ai-news|"
    r"book/mutual|book|mutual|"
    r"category|tag|tags|topic|topics|rubric|section)(?:/|$)",
    re.IGNORECASE,
)

_CNEWS_ARTICLE_PATH_RE = re.compile(
    r"^/(?:news/(?:top|line)|articles)/\d{4}-\d{2}-\d{2}",
    re.IGNORECASE,
)


def _looks_like_article_url_from_listing(url: str, listing_url: str) -> bool:
    """Ссылка похожа на отдельную статью, а не на рубрику/пагинацию."""
    try:
        p = urlparse(url.strip())
        listing = urlparse(listing_url.strip())
    except Exception:
        return False
    path = (p.path or "").rstrip("/")
    if not path or _is_site_homepage_url(url):
        return False
    low = url.lower()
    if any(x in low for x in ("?page=", "/page/", "/search", "/login", "/subscribe", "/rss", "/feed")):
        return False
    if re.search(r"\.(jpg|jpeg|png|gif|webp|pdf|zip)(\?|$)", low):
        return False
    if "arxiv.org" in low and "/format/" in low:
        return False
    if _CNEWS_ARTICLE_PATH_RE.search(path):
        return True
    listing_path = (listing.path or "").rstrip("/")
    listing_depth = len([x for x in listing_path.split("/") if x])
    path_depth = len([x for x in path.split("/") if x])
    last_seg = path.split("/")[-1]
    if listing_path and path.startswith(listing_path + "/") and len(path) > len(listing_path) + 4:
        return True
    if re.search(r"\d{5,}", path):
        return True
    if re.search(r"\d{4}[/-]\d{2}", path):
        return True
    if re.search(r"\.html?$", path, re.IGNORECASE):
        return True
    if len(last_seg) >= 18 and "-" in last_seg:
        return True
    if path_depth > listing_depth + 1 and len(last_seg) >= 10:
        return True
    if listing_path and path.startswith("/articles/") and path != listing_path and path.count("/") >= 3:
        return True
    return False


def _extract_listing_article_urls(chunk: str, page_url: str, limit: int = 12) -> list[str]:
    """Извлекает ссылки на отдельные материалы со страницы-ленты/рубрики."""
    try:
        base = urlparse(page_url)
    except Exception:
        return []
    host = (base.hostname or "").lower().removeprefix("www.")
    scored: list[tuple[int, str]] = []
    seen: set[str] = set()
    for m in re.finditer(r'<a[^>]+href=["\']([^"\']+)["\']', chunk, re.IGNORECASE):
        href = html.unescape(m.group(1).strip())
        if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
            continue
        try:
            abs_url = urljoin(page_url, href).split("#")[0]
            pu = urlparse(abs_url)
        except Exception:
            continue
        phost = (pu.hostname or "").lower().removeprefix("www.")
        if phost != host:
            continue
        if not _looks_like_article_url_from_listing(abs_url, page_url):
            continue
        key = _url_fingerprint(abs_url)
        if not key or key in seen:
            continue
        seen.add(key)
        path = (pu.path or "")
        score = len(path) + (20 if re.search(r"\d", path) else 0)
        scored.append((score, abs_url))
    scored.sort(key=lambda x: (-x[0], x[1]))
    return [u for _, u in scored[:limit]]


def _is_news_listing_page(page_url: str, chunk: str, bundle: dict[str, Any]) -> bool:
    """Страница-лента/рубрика со списком новостей, а не одна статья."""
    if is_listing_page_url(page_url):
        return True
    if is_topic_pool_page_url(page_url):
        return True
    if _is_site_homepage_url(page_url):
        return True
    try:
        path = (urlparse(page_url).path or "").rstrip("/").lower()
    except Exception:
        path = ""
    child_urls = _extract_listing_article_urls(chunk, page_url, limit=10)
    h2_count = len(re.findall(r"<h2\b", chunk, re.IGNORECASE))
    og_type = (_meta_property_content(chunk, "og:type") or "").lower()

    if len(child_urls) >= 3 and _LISTING_PATH_HINTS.search(path):
        return True
    if re.search(r"^/articles/[\w_-]+$", path, re.IGNORECASE) and len(child_urls) >= 3:
        return True
    if path.endswith("/neiroseti") and len(child_urls) >= 3:
        return True
    if len(child_urls) >= 3 and not bundle.get("article_markers"):
        if og_type in {"", "website", "webpage"} or not bundle.get("headline_strict"):
            return True
    if len(child_urls) >= 5 and h2_count >= 3:
        if not bundle.get("article_markers"):
            return True
        if og_type in {"", "website", "webpage"} and not bundle.get("headline_strict"):
            return True
        if not bundle.get("soft_article_signals"):
            return True
    link_count = len(re.findall(r"<a\b[^>]*\bhref\s*=", chunk, re.IGNORECASE))
    para_n = len(re.findall(r"<p\b", chunk, re.IGNORECASE))
    if link_count >= 30 and para_n <= 4 and len(child_urls) >= 2:
        return True
    return False


def _expand_listing_url_candidates(initial_url: str, max_children: int = 10) -> list[tuple[str, dict[str, Any]]]:
    """
    Если URL — лента, возвращает пары (url, bundle) дочерних статей.
    Иначе одну пару для самой страницы.
    """
    if is_listing_page_url(initial_url):
        bundle = _fetch_article_page_bundle(initial_url)
        if not bundle.get("ok"):
            return []
        children = list(bundle.get("listing_article_urls") or [])
        if not children:
            chunk_resp = _http_get_html_for_article(initial_url)
            if chunk_resp and chunk_resp.text:
                children = _extract_listing_article_urls(chunk_resp.text[:400_000], initial_url, limit=max_children + 4)
        out: list[tuple[str, dict[str, Any]]] = []
        for child in children[:max_children]:
            child_bundle = _fetch_article_page_bundle(child)
            if not child_bundle.get("ok") or child_bundle.get("is_listing_page"):
                continue
            if is_listing_page_url(str(child_bundle.get("final_url") or child)):
                continue
            stored = str(child_bundle.get("final_url") or child_bundle.get("display_url") or child).strip()
            out.append((stored, child_bundle))
        if out:
            logger.info(
                "Шаг 1: разбор ленты | listing=%s extracted=%s",
                initial_url[:100],
                len(out),
            )
        else:
            logger.warning(
                "Шаг 1: индексная страница без дочерних статей | url=%s",
                initial_url[:120],
            )
        return out

    bundle = _fetch_article_page_bundle(initial_url)
    if not bundle.get("ok"):
        return []
    if bundle.get("is_listing_page"):
        out: list[tuple[str, dict[str, Any]]] = []
        for child in (bundle.get("listing_article_urls") or [])[:max_children]:
            child_bundle = _fetch_article_page_bundle(child)
            if not child_bundle.get("ok") or child_bundle.get("is_listing_page"):
                continue
            stored = str(child_bundle.get("final_url") or child_bundle.get("display_url") or child).strip()
            out.append((stored, child_bundle))
        if out:
            logger.info(
                "Шаг 1: разбор ленты | listing=%s extracted=%s",
                initial_url[:100],
                len(out),
            )
            return out
        if is_topic_pool_page_url(initial_url) or is_listing_page_url(initial_url):
            logger.warning(
                "Шаг 1: индекс/лента без дочерних статей | url=%s",
                initial_url[:120],
            )
            return []
        # Ложная «лента» (много ссылок, но это статья) — только если URL не похож на индекс.
        if _page_is_article_like({**bundle, "is_listing_page": False}):
            stored = str(bundle.get("final_url") or bundle.get("display_url") or initial_url).strip()
            if is_listing_page_url(stored):
                logger.warning("Шаг 1: лента по URL после редиректа | url=%s", stored[:120])
                return []
            single = {**bundle, "is_listing_page": False}
            logger.info(
                "Шаг 1: лента без дочерних URL, проверяем как статью | url=%s",
                stored[:100],
            )
            return [(stored, single)]
        logger.warning(
            "Шаг 1: лента без извлечённых статей | url=%s",
            initial_url[:120],
        )
        return []
    stored = str(bundle.get("final_url") or bundle.get("display_url") or initial_url).strip()
    return [(stored, bundle)]


def _ld_article_url_field(obj: dict[str, Any]) -> str | None:
    for key in ("url", "@id"):
        v = obj.get(key)
        if isinstance(v, str) and v.strip().startswith(("http://", "https://")):
            return v.strip()
    m = obj.get("mainEntityOfPage")
    if isinstance(m, str) and m.strip().startswith(("http://", "https://")):
        return m.strip()
    if isinstance(m, dict):
        for kk in ("@id", "url"):
            vv = m.get(kk)
            if isinstance(vv, str) and vv.strip().startswith(("http://", "https://")):
                return vv.strip()
    return None


def _ld_json_collect_articles(obj: Any) -> list[tuple[str, str | None]]:
    rows: list[tuple[str, str | None]] = []
    if isinstance(obj, dict):
        types = obj.get("@type")
        if isinstance(types, str):
            type_list = [types]
        elif isinstance(types, list):
            type_list = [str(x) for x in types]
        else:
            type_list = []
        if any(t in ("NewsArticle", "Article", "BlogPosting") for t in type_list) or (
            "WebPage" in type_list and _ld_article_url_field(obj) is not None
        ):
            h: str | None = None
            for key in ("headline", "name", "title"):
                v = obj.get(key)
                if isinstance(v, str) and len(v.strip()) >= 8:
                    h = _clean_headline_text(v.strip())
                    break
            if h:
                rows.append((h, _ld_article_url_field(obj)))
        g = obj.get("@graph")
        if isinstance(g, list):
            for it in g:
                rows.extend(_ld_json_collect_articles(it))
        for k, v in obj.items():
            if k == "@graph":
                continue
            rows.extend(_ld_json_collect_articles(v))
    elif isinstance(obj, list):
        for it in obj:
            rows.extend(_ld_json_collect_articles(it))
    return rows


def _extract_ld_article_pairs_chunk(chunk: str) -> list[tuple[str, str | None]]:
    rows: list[tuple[str, str | None]] = []
    for m in re.finditer(
        r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        chunk,
        re.IGNORECASE | re.DOTALL,
    ):
        raw = m.group(1).strip()
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        rows.extend(_ld_json_collect_articles(data))
    seen: set[tuple[str, str]] = set()
    out: list[tuple[str, str | None]] = []
    for h, u in rows:
        key = (h, u or "")
        if key in seen:
            continue
        seen.add(key)
        out.append((h, u))
    return out


def _link_rel_canonical_href(chunk: str, base_url: str) -> str | None:
    for pat in (
        r'<link[^>]+rel=["\']canonical["\'][^>]+href=["\']([^"\']+)["\']',
        r'<link[^>]+href=["\']([^"\']+)["\'][^>]+rel=["\']canonical["\']',
    ):
        m = re.search(pat, chunk, re.IGNORECASE | re.DOTALL)
        if m:
            href = m.group(1).strip()
            if href:
                try:
                    return urljoin(base_url, href).split("#")[0]
                except Exception:
                    return None
    return None


def _pick_display_url(final_url: str, canonical: str | None, og_url: str | None) -> str:
    """Если canonical/og:url указывают на ту же страницу — берём «чистый» URL из разметки."""
    ff = _url_fingerprint(final_url)
    for cand in (canonical, og_url):
        if not cand or not str(cand).strip().startswith("http"):
            continue
        cu = str(cand).strip().split("#")[0]
        try:
            p1, p2 = urlparse(final_url), urlparse(cu)
        except Exception:
            continue
        h1 = (p1.hostname or "").lower().removeprefix("www.")
        h2 = (p2.hostname or "").lower().removeprefix("www.")
        if h1 != h2:
            continue
        if _url_fingerprint(cu) == ff:
            return cu
    return final_url.split("#")[0]


def _choose_coherent_headline(
    final_url: str,
    og_title: str | None,
    og_url: str | None,
    twitter_title: str | None,
    ld_pairs: list[tuple[str, str | None]],
    h1: str | None,
    html_title: str | None,
) -> tuple[str | None, str, bool]:
    """Возвращает (headline, source, strict_match). strict_match=True только для сильного URL-согласования."""
    fp = _url_fingerprint(final_url)

    def polish(t: str) -> str | None:
        s = _strip_site_branding(_clean_headline_text(t))
        return s[:480] if len(s) >= 8 else None

    for h, u in ld_pairs:
        if not u:
            continue
        u2 = u.split("#")[0]
        if _url_fingerprint(u2) == fp:
            got = polish(h)
            if got:
                return got, "ld_url_match", True

    if og_title and og_url:
        ou = og_url.split("#")[0]
        if _url_fingerprint(ou) == fp:
            got = polish(og_title)
            if got:
                return got, "og_url_match", True
            if twitter_title:
                got2 = polish(twitter_title)
                if got2:
                    return got2, "twitter_url_match", True
    elif og_title and not og_url:
        got = polish(og_title)
        if got:
            return got, "og_title_no_ogurl", False

    if h1:
        got = polish(h1)
        if got:
            return got, "h1_fallback", False

    try:
        host = (urlparse(final_url).hostname or "").lower().removeprefix("www.")
    except Exception:
        host = ""
    for h, u in ld_pairs:
        if not u:
            continue
        uh = (urlparse(u).hostname or "").lower().removeprefix("www.")
        if host and uh == host:
            got = polish(h)
            if got:
                return got, "ld_same_host_fallback", False

    for h, _u in ld_pairs:
        got = polish(h)
        if got:
            return got, "ld_any_fallback", False

    if html_title:
        got = polish(html_title)
        if got:
            return got, "html_title_fallback", False
    return None, "none", False


def _rough_visible_text_from_html(chunk: str, limit: int = 4500) -> str:
    """Грубое снятие текста со страницы для тематической эвристики (без BeautifulSoup)."""
    t = re.sub(r"(?is)<script[^>]*>.*?</script>", " ", chunk)
    t = re.sub(r"(?is)<style[^>]*>.*?</style>", " ", t)
    t = re.sub(r"(?is)<noscript[^>]*>.*?</noscript>", " ", t)
    t = re.sub(r"<[^>]+>", " ", t)
    return _clean_headline_text(html.unescape(t))[:limit]


def _rough_visible_text_after_first_h1(chunk: str, limit: int = 4000) -> str:
    """Текст после первого h1 — меньше шума из шапки сайта (реклама «ИИ-сервисов» и т.п.)."""
    m = re.search(r"(?is)</h1\s*>", chunk)
    start = m.end() if m else 0
    slice_chunk = chunk[start : start + 180_000]
    return _rough_visible_text_from_html(slice_chunk, limit=limit)


_AI_TOPIC_RES: list[re.Pattern[str]] = [
    re.compile(p, re.IGNORECASE)
    for p in (
        r"искусственн\w*\s+интеллект",
        r"интеллект\w*\s+искусственн",
        r"нейросет",
        r"нейронн\w*\s+сет",
        r"машинн\w*\s+обучен",
        r"machine\s+learning",
        r"deep\s+learning",
        r"reinforcement\s+learning",
        r"\bgpt-?\d",
        r"chatgpt",
        r"\bopenai\b",
        r"anthropic",
        r"\bclaude\b",
        r"\bgemini\b",
        r"google\s+deepmind",
        r"\bdeepmind\b",
        r"\bllm\b",
        r"large\s+language",
        r"языков\w*\s+модел",
        r"генеративн\w*\s+(?:модел|ии|ai)",
        r"мультимодальн",
        r"stable\s+diffusion",
        r"midjourney",
        r"\bmistral\b",
        r"\bllama\b",
        r"yandexgpt",
        r"gigachat",
        r"copilot",
        r"nvidia\b.{0,60}\b(ai|нейро|искусствен)",
        r"(?<![А-Яа-яЁё])ИИ(?=\s|[.,;:!?»\"']|$)",
        r"\bai\b",
        r"artificial\s+intelligence",
        r"computer\s+vision",
        r"embeddings?",
        r"fine[- ]tuning",
        r"трансформер\w*\s+(?:модел|архитектур)",
        r"tokenization",
        r"\bagi\b",
        r"искусственн\w*\s+нейро",
    )
]


def _topic_corpus_from_article_chunk(
    chunk: str,
    *,
    html_title: str | None,
    og_title: str | None,
    h1: str | None,
) -> str:
    parts: list[str] = []
    for p in (
        og_title,
        html_title,
        h1,
        _meta_property_content(chunk, "og:description"),
        _meta_name_content(chunk, "description"),
        _meta_name_content(chunk, "twitter:description"),
        _meta_property_content(chunk, "twitter:description"),
    ):
        if p:
            parts.append(p)
    parts.append(_rough_visible_text_after_first_h1(chunk))
    # Доп. фрагмент текста из начала body — если после h1 только «шум», тема ИИ может быть ниже.
    body_lo = chunk.lower().find("<body")
    slice_start = body_lo if body_lo != -1 else 0
    body_snip = _rough_visible_text_from_html(chunk[slice_start : slice_start + 120_000], limit=4000)
    if body_snip:
        parts.append(body_snip)
    return _clean_headline_text(" ".join(parts))[:14_000]


def _ai_digest_topic_matches(corpus: str, extra: str = "") -> bool:
    """Дайджест только про ИИ/нейросети: ищем явные маркеры в тексте страницы и заголовке."""
    extra_s = _clean_headline_text(extra or "").strip()
    # Короткий, но явный заголовок про ИИ — не требуем длинного corpus (часть сайтов отдаёт мало текста в HTML).
    if extra_s and any(rx.search(extra_s) for rx in _AI_TOPIC_RES):
        return True
    merged = _clean_headline_text(f"{corpus} {extra}")
    if len(merged.strip()) < 14:
        return False
    return any(rx.search(merged) for rx in _AI_TOPIC_RES)


def _has_article_markers(chunk: str, ld_pairs: list[tuple[str, str | None]]) -> bool:
    if _meta_property_content(chunk, "article:published_time"):
        return True
    og_type = (_meta_property_content(chunk, "og:type") or "").lower()
    if og_type in {"article", "newsarticle"}:
        return True
    if re.search(r'"datePublished"\s*:\s*"[^"]+"', chunk, flags=re.IGNORECASE):
        return True
    if any(u for _h, u in ld_pairs):
        return True
    if re.search(r"<article\b", chunk, re.IGNORECASE):
        return True
    if re.search(r'itemprop\s*=\s*["\']articleBody["\']', chunk, re.IGNORECASE):
        return True
    if re.search(r"\bNewsArticle\b|\bBlogPosting\b", chunk):
        return True
    if re.search(r"<time\b[^>]*\bdatetime\s*=", chunk, re.IGNORECASE):
        return True
    return False


_PUBLISHED_AT_MIN_YEAR = 2010
PUBLISHED_AT_UNDEFINED = "UNDEFINED"
NEWS_WINDOW_DAY_KINDS = frozenset({"calendar", "working"})
_URL_PATH_DATE_RE = re.compile(r"/(\d{4})/(\d{2})/(\d{2})(?:/|$)")
_URL_PATH_DATE_DASH_RE = re.compile(r"/(\d{4})-(\d{2})-(\d{2})(?:/|$)")
_RU_MONTHS: dict[str, int] = {
    "января": 1,
    "февраля": 2,
    "марта": 3,
    "апреля": 4,
    "мая": 5,
    "июня": 6,
    "июля": 7,
    "августа": 8,
    "сентября": 9,
    "октября": 10,
    "ноября": 11,
    "декабря": 12,
}
_RU_DATE_TEXT_RE = re.compile(
    r"(\d{1,2})\s+("
    + "|".join(_RU_MONTHS)
    + r")\s+(\d{4})(?:,?\s*(\d{1,2}):(\d{2}))?",
    re.IGNORECASE,
)
_DOT_DATE_RE = re.compile(
    r"\b(\d{1,2})\.(\d{1,2})\.(\d{4})(?:\s+(\d{1,2}):(\d{2})(?::(\d{2}))?)?\b"
)


def _parse_published_at_raw(raw: str) -> datetime | None:
    """Разбор даты публикации из meta / JSON-LD / time."""
    s = (raw or "").strip()
    if not s or len(s) > 120:
        return None
    if re.fullmatch(r"\d{10,13}", s):
        try:
            ts = int(s[:13])
            if len(s) > 10:
                ts = ts / 1000.0
            return datetime.fromtimestamp(ts, tz=MSK_TZ)
        except (ValueError, OSError):
            return None
    norm = s.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(norm)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=MSK_TZ)
        return dt.astimezone(MSK_TZ)
    except ValueError:
        pass
    dot = _DOT_DATE_RE.search(s)
    if dot:
        try:
            sec = int(dot.group(6)) if dot.group(6) else 0
            return datetime(
                int(dot.group(3)),
                int(dot.group(2)),
                int(dot.group(1)),
                int(dot.group(4) or 0),
                int(dot.group(5) or 0),
                sec,
                tzinfo=MSK_TZ,
            )
        except ValueError:
            pass
    for fmt in (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d",
        "%d.%m.%Y %H:%M:%S",
        "%d.%m.%Y %H:%M",
        "%d.%m.%Y",
    ):
        try:
            return datetime.strptime(s[:19], fmt).replace(tzinfo=MSK_TZ)
        except ValueError:
            continue
    try:
        dt = parsedate_to_datetime(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=MSK_TZ)
        return dt.astimezone(MSK_TZ)
    except (TypeError, ValueError, OverflowError):
        pass
    m_ru = _RU_DATE_TEXT_RE.search(s)
    if m_ru:
        month = _RU_MONTHS.get(m_ru.group(2).lower())
        if month:
            try:
                hour = int(m_ru.group(4)) if m_ru.group(4) else 0
                minute = int(m_ru.group(5)) if m_ru.group(5) else 0
                return datetime(
                    int(m_ru.group(3)),
                    month,
                    int(m_ru.group(1)),
                    hour,
                    minute,
                    tzinfo=MSK_TZ,
                )
            except ValueError:
                return None
    return None


def _normalize_news_window_day_kind(kind: str | None) -> str:
    k = (kind or "working").strip().lower()
    return k if k in NEWS_WINDOW_DAY_KINDS else "working"


def _subtract_working_days_rf(anchor: date, days: int) -> date:
    """N рабочих дней (пн–пт) назад от anchor, не включая сам anchor."""
    d = anchor
    left = max(0, int(days))
    while left > 0:
        d -= timedelta(days=1)
        if d.weekday() < 5:
            left -= 1
    return d


def digest_earliest_news_date(digest: Digest) -> date:
    anchor = digest.date if isinstance(digest.date, date) else digest.date
    days = max(1, int(getattr(digest, "news_window_days", None) or 3))
    kind = _normalize_news_window_day_kind(getattr(digest, "news_window_day_kind", None))
    if kind == "working":
        return _subtract_working_days_rf(anchor, days)
    return anchor - timedelta(days=days)


def _published_at_from_url_path(url: str) -> datetime | None:
    try:
        path = urlparse(url.strip()).path or ""
    except Exception:
        return None
    for pattern in (_URL_PATH_DATE_DASH_RE, _URL_PATH_DATE_RE):
        m = pattern.search(path)
        if not m:
            continue
        try:
            return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)), tzinfo=MSK_TZ)
        except ValueError:
            continue
    return None


def _parse_published_at_storage_value(value: str | None) -> datetime | None:
    s = (value or "").strip()
    if not s or s == PUBLISHED_AT_UNDEFINED:
        return None
    if s.startswith("1970-"):
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return _parse_published_at_raw(s)


def _published_at_before_digest_window(digest: Digest, published_at: str | None, page_url: str) -> bool:
    """True, если дата материала раньше допустимого окна от даты выпуска."""
    earliest = digest_earliest_news_date(digest)
    dt = _parse_published_at_storage_value(published_at)
    if dt is None:
        dt = _published_at_from_url_path(page_url)
    if dt is None:
        return False
    pub_day = dt.astimezone(MSK_TZ).date()
    return pub_day < earliest


def _published_at_plausible(dt: datetime, *, now: datetime | None = None) -> bool:
    now = now or datetime.now(MSK_TZ)
    if dt.year < _PUBLISHED_AT_MIN_YEAR:
        return False
    return dt <= now + timedelta(days=2)


def _format_published_at_storage(dt: datetime) -> str:
    return dt.astimezone(MSK_TZ).isoformat(timespec="seconds")[:100]


def _ld_collect_publication_dates(obj: Any, out: list[tuple[int, str]]) -> None:
    """Приоритет: datePublished (2) раньше dateCreated (4)."""
    if isinstance(obj, dict):
        types = obj.get("@type")
        if isinstance(types, str):
            type_list = [types]
        elif isinstance(types, list):
            type_list = [str(x) for x in types]
        else:
            type_list = []
        is_article = any(
            t in ("NewsArticle", "Article", "BlogPosting", "WebPage", "ReportageNewsArticle")
            for t in type_list
        )
        if is_article:
            dp = obj.get("datePublished")
            if isinstance(dp, str) and dp.strip():
                out.append((2, dp.strip()))
            dc = obj.get("dateCreated")
            if isinstance(dc, str) and dc.strip():
                out.append((4, dc.strip()))
        g = obj.get("@graph")
        if isinstance(g, list):
            for it in g:
                _ld_collect_publication_dates(it, out)
        for k, v in obj.items():
            if k == "@graph":
                continue
            _ld_collect_publication_dates(v, out)
    elif isinstance(obj, list):
        for it in obj:
            _ld_collect_publication_dates(it, out)


def _article_focus_html(chunk: str, *, max_len: int = 30_000) -> str:
    """Фрагмент вокруг заголовка материала — без лент «популярное» и подвала."""
    m = re.search(r"<h1\b", chunk, re.IGNORECASE)
    if not m:
        return chunk[:max_len]
    return chunk[m.start() : m.start() + max_len]


def _collect_published_at_raw_candidates(chunk: str) -> list[tuple[int, str]]:
    """(priority, raw) — меньший priority предпочтительнее."""
    found: list[tuple[int, str]] = []
    focus = _article_focus_html(chunk)

    def add(priority: int, raw: str | None) -> None:
        if raw and raw.strip():
            found.append((priority, raw.strip()[:120]))

    add(1, _meta_property_content(chunk, "article:published_time"))
    add(1, _meta_property_content(chunk, "og:article:published_time"))
    add(2, _meta_property_content(chunk, "article:published"))
    add(3, _meta_name_content(chunk, "pubdate"))
    add(3, _meta_name_content(chunk, "publishdate"))
    add(3, _meta_name_content(chunk, "date"))

    for m in re.finditer(
        r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        chunk,
        re.IGNORECASE | re.DOTALL,
    ):
        raw_json = m.group(1).strip()
        if not raw_json:
            continue
        try:
            data = json.loads(raw_json)
        except json.JSONDecodeError:
            continue
        _ld_collect_publication_dates(data, found)

    for m in re.finditer(r'"datePublished"\s*:\s*"([^"]+)"', chunk, flags=re.IGNORECASE):
        add(2, m.group(1))

    for m in re.finditer(
        r'itemprop\s*=\s*["\']datePublished["\'][^>]*content\s*=\s*["\']([^"\']+)["\']',
        chunk,
        re.IGNORECASE,
    ):
        add(2, m.group(1))
    for m in re.finditer(
        r'content\s*=\s*["\']([^"\']+)["\'][^>]*itemprop\s*=\s*["\']datePublished["\']',
        chunk,
        re.IGNORECASE,
    ):
        add(2, m.group(1))

    for m in re.finditer(r"<time\b([^>]*)>", chunk, re.IGNORECASE):
        tag = m.group(1)
        dm = re.search(r'\bdatetime\s*=\s*["\']([^"\']+)["\']', tag, re.IGNORECASE)
        if not dm:
            continue
        val = dm.group(1)
        if re.search(r'itemprop\s*=\s*["\']datePublished["\']', tag, re.IGNORECASE):
            add(1, val)
        else:
            add(3, val)

    for m in _DOT_DATE_RE.finditer(focus):
        add(3, m.group(0))

    for m in _RU_DATE_TEXT_RE.finditer(focus):
        add(4, m.group(0))

    return found


def _extract_published_at_from_page(chunk: str, page_url: str = "") -> str | None:
    """Дата из разметки страницы; при отсутствии — из пути URL (/YYYY/MM/DD/)."""
    got = _extract_published_at_from_chunk(chunk)
    if got:
        return got
    from_url = _published_at_from_url_path(page_url)
    if from_url and _published_at_plausible(from_url):
        return _format_published_at_storage(from_url)
    return None


def _extract_published_at_from_chunk(chunk: str) -> str | None:
    """Дата публикации материала со страницы (МСК, ISO) или None."""
    best: tuple[int, datetime] | None = None
    for priority, raw in _collect_published_at_raw_candidates(chunk):
        dt = _parse_published_at_raw(raw)
        if dt is None or not _published_at_plausible(dt):
            continue
        if best is None or priority < best[0]:
            best = (priority, dt)
        elif priority == best[0] and priority <= 2 and dt > best[1]:
            # meta / JSON-LD: при нескольких метках берём более позднюю
            best = (priority, dt)
        # priority >= 3 (текст, time без itemprop): оставляем первую в порядке разбора
    if best is None:
        return None
    dt = best[1]
    if not (dt.hour or dt.minute or dt.second):
        for _p, raw in _collect_published_at_raw_candidates(chunk):
            parsed = _parse_published_at_raw(raw)
            if (
                parsed
                and parsed.date() == dt.date()
                and (parsed.hour or parsed.minute)
                and _published_at_plausible(parsed)
            ):
                dt = parsed
                break
    return _format_published_at_storage(dt)


def _apply_bundle_published_at(item: dict[str, Any], bundle: dict[str, Any]) -> None:
    """Дата только со страницы; технические метки и даты от LLM не сохраняем."""
    pub = bundle.get("published_at")
    if isinstance(pub, str) and pub.strip():
        item["published_at"] = pub.strip()[:100]
    else:
        item["published_at"] = PUBLISHED_AT_UNDEFINED


def _http_get_html_for_article(url: str) -> requests.Response | None:
    """Несколько попыток и второй User-Agent: снижаем ложные http_unreachable (403/таймаут/обрыв)."""
    headers_base = {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
        "Cache-Control": "no-cache",
        "Upgrade-Insecure-Requests": "1",
    }
    agents = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:124.0) Gecko/20100101 Firefox/124.0",
    )
    last_exc: BaseException | None = None
    for attempt in range(3):
        if attempt:
            time.sleep(0.45 * attempt)
        last_server_err: requests.Response | None = None
        for agent in agents:
            try:
                r = requests.get(
                    url,
                    timeout=(12, 28),
                    headers={**headers_base, "User-Agent": agent},
                    allow_redirects=True,
                )
                if r.status_code in (403, 429) and agent == agents[0]:
                    continue
                if r.status_code in (502, 503, 504):
                    last_server_err = r
                    continue
                return r
            except requests.RequestException as exc:
                last_exc = exc
                continue
        if last_server_err is not None and attempt < 2:
            continue
        if last_server_err is not None:
            return last_server_err
    if last_exc:
        logger.debug("Статья: все попытки GET не удались url=%s err=%s", url[:160], last_exc)
    return None


def _fetch_article_page_bundle(initial_url: str) -> dict[str, Any]:
    """
    Один GET: финальный URL после редиректов, при необходимости canonical/og:url,
    заголовок только если он согласован с URL страницы (не «первый попавшийся» meta).
    """
    empty: dict[str, Any] = {
        "ok": False,
        "status_code": None,
        "final_url": None,
        "display_url": None,
        "headline": None,
        "published_at": None,
        "topic_corpus": "",
    }
    try:
        r = _http_get_html_for_article(initial_url)
        if r is None:
            return empty
        empty["status_code"] = r.status_code
        if r.status_code >= 400 or not r.text:
            return empty
        r.encoding = r.apparent_encoding or getattr(r, "encoding", None) or "utf-8"
        final_url = str(r.url).split("#")[0]
        chunk = r.text[:400_000]
        canonical = _link_rel_canonical_href(chunk, final_url)
        og_url_raw = _meta_property_content(chunk, "og:url")
        og_url_abs: str | None = None
        if og_url_raw:
            try:
                og_url_abs = urljoin(final_url, og_url_raw.strip()).split("#")[0]
            except Exception:
                og_url_abs = None
        display = _pick_display_url(final_url, canonical, og_url_abs)
        og_title = _meta_property_content(chunk, "og:title")
        tw = _meta_name_content(chunk, "twitter:title") or _meta_property_content(chunk, "twitter:title")
        ld_pairs = _extract_ld_article_pairs_chunk(chunk)
        h1 = _first_h1_text(chunk)
        html_t = _html_title_tag(chunk)
        headline, headline_source, headline_strict = _choose_coherent_headline(
            display, og_title, og_url_abs, tw, ld_pairs, h1, html_t
        )
        article_markers = _has_article_markers(chunk, ld_pairs)
        topic_corpus = _topic_corpus_from_article_chunk(
            chunk, html_title=html_t, og_title=og_title, h1=h1
        )
        rough_vis = _rough_visible_text_from_html(chunk, 6500)
        para_n = len(re.findall(r"<p\b", chunk, re.IGNORECASE))
        hl_plain = str(headline or "").strip()
        soft_article_signals = (
            bool(headline)
            and len(hl_plain) >= 12
            and len(topic_corpus) >= 720
            and para_n >= 2
            and len(rough_vis) >= 380
        )
        partial_bundle = {
            "ok": True,
            "status_code": r.status_code,
            "final_url": final_url,
            "display_url": display,
            "headline": headline,
            "headline_source": headline_source,
            "headline_strict": headline_strict,
            "article_markers": article_markers,
            "soft_article_signals": soft_article_signals,
            "topic_corpus": topic_corpus,
        }
        is_listing = _is_news_listing_page(display or final_url, chunk, partial_bundle)
        listing_urls = _extract_listing_article_urls(chunk, display or final_url, limit=14) if is_listing else []
        published_at = _extract_published_at_from_page(chunk, display or final_url)
        return {
            **partial_bundle,
            "published_at": published_at,
            "is_listing_page": is_listing,
            "listing_article_urls": listing_urls,
        }
    except Exception:
        logger.debug("Не удалось загрузить страницу для согласования URL/заголовка", exc_info=True)
        return empty


def _first_h1_text(chunk: str) -> str | None:
    m = re.search(r"<h1[^>]*>(.*?)</h1>", chunk, re.IGNORECASE | re.DOTALL)
    if not m:
        return None
    inner = m.group(1)
    inner = re.sub(r"<[^>]+>", " ", inner)
    t = _clean_headline_text(inner)
    if len(t) < 8:
        return None
    return t


def _html_title_tag(chunk: str) -> str | None:
    m = re.search(r"<title[^>]*>([^<]+)</title>", chunk, re.IGNORECASE | re.DOTALL)
    if not m:
        return None
    return _clean_headline_text(m.group(1))


def _host_from_url(url: str) -> str:
    try:
        h = urlparse(url).hostname
        return (h or "").replace("www.", "") or "Manual"
    except Exception:
        return "Manual"


def _classify_source_policy(url: str) -> tuple[str, bool, str]:
    """
    Возвращает (tier, is_aggregator, reliability_status) по домену.
    Это дополнительная защита до агентной верификации.
    """
    host = _host_from_url(url).lower()
    if any(marker in host for marker in AGGREGATOR_HOST_MARKERS):
        return "Tier-4", True, "❗ без подтверждения"
    if any(marker in host for marker in TIER1_HOST_MARKERS):
        return "Tier-1", False, "✅ подтверждено"
    if any(marker in host for marker in TIER2_HOST_MARKERS):
        return "Tier-2", False, "✅ подтверждено"
    return "Tier-3", False, "⚠️ сомнительный"


class DigestService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.settings = get_settings()
        self.proxy = ProxyApiClient()
        self.cost_tracker = ProxyApiCostTracker()
        contract = self.settings.prompts_path.read_text(encoding="utf-8")
        tiers = self.settings.source_tiers_path.read_text(encoding="utf-8")
        self.workflow = CrewWorkflow(
            contract_prompt=contract + "\n\n---\n" + tiers.strip() + "\n",
        )

    def _digest_step_llm_cost_sum(self, digest_id: int, step: str) -> float:
        total = (
            self.db.query(func.coalesce(func.sum(LlmCostRecord.cost_rub), 0.0))
            .filter(LlmCostRecord.digest_id == digest_id, LlmCostRecord.step == step)
            .scalar()
        )
        return float(total or 0.0)

    def _snapshot_proxyapi_before(self, digest: Digest, *, reset: bool = False) -> None:
        snap = self.cost_tracker.get_balance_snapshot()
        record_today_balance(self.db, snap)
        if reset or digest.proxyapi_balance_session_start is None:
            digest.proxyapi_balance_session_start = snap.balance
            digest.proxyapi_budget_used_session_start = snap.budget_used
        digest.proxyapi_balance_before = snap.balance
        digest.proxyapi_budget_used_before = snap.budget_used
        self.db.commit()

    def _snapshot_proxyapi_after(self, digest: Digest) -> None:
        snap = self.cost_tracker.get_balance_snapshot()
        digest.proxyapi_balance_after = snap.balance
        digest.proxyapi_budget_used_after = snap.budget_used
        record_today_balance(self.db, snap)
        self.db.commit()

    def _last_step_balance_delta_rub(self, digest: Digest) -> float | None:
        if digest.proxyapi_budget_used_before is not None and digest.proxyapi_budget_used_after is not None:
            delta = float(digest.proxyapi_budget_used_after) - float(digest.proxyapi_budget_used_before)
            if delta > 0.0001:
                return round(delta, 6)
        if digest.proxyapi_balance_before is not None and digest.proxyapi_balance_after is not None:
            delta = float(digest.proxyapi_balance_before) - float(digest.proxyapi_balance_after)
            if delta > 0.0001:
                return round(delta, 6)
        return None

    def _record_proxyapi_step_cost(
        self,
        digest: Digest,
        *,
        step: str,
        agent_name: str,
        request_label: str,
        model: str,
    ) -> None:
        cost = self._last_step_balance_delta_rub(digest)
        if cost is None:
            return
        self._save_cost(
            digest_id=digest.id,
            step=step,
            agent_name=agent_name,
            model=model,
            request_label=request_label,
            cost_rub=cost,
        )

    @contextmanager
    def _digest_cost_session(
        self,
        digest: Digest,
        *,
        reset: bool = False,
        step: str,
        agent_name: str,
        request_label: str,
        model: str,
    ):
        self._snapshot_proxyapi_before(digest, reset=reset)
        try:
            yield
        finally:
            self.db.refresh(digest)
            self._snapshot_proxyapi_after(digest)
            self._record_proxyapi_step_cost(
                digest,
                step=step,
                agent_name=agent_name,
                request_label=request_label,
                model=model,
            )

    def _digest_proxyapi_spent_rub(self, digest: Digest) -> float:
        snap = self.cost_tracker.get_balance_snapshot()
        if digest.proxyapi_budget_used_before is not None and snap.budget_used is not None:
            return max(0.0, float(snap.budget_used) - float(digest.proxyapi_budget_used_before))
        if digest.proxyapi_balance_before is not None and snap.balance is not None:
            return max(0.0, float(digest.proxyapi_balance_before) - float(snap.balance))
        return 0.0

    def digest_proxyapi_cost_rub(self, digest: Digest) -> float | None:
        if (
            digest.proxyapi_budget_used_session_start is not None
            and digest.proxyapi_budget_used_after is not None
        ):
            spent = float(digest.proxyapi_budget_used_after) - float(digest.proxyapi_budget_used_session_start)
            return round(max(0.0, spent), 4)
        if digest.proxyapi_balance_session_start is not None and digest.proxyapi_balance_after is not None:
            spent = float(digest.proxyapi_balance_session_start) - float(digest.proxyapi_balance_after)
            return round(max(0.0, spent), 4)
        live = self._digest_proxyapi_spent_rub(digest)
        return round(live, 4) if live > 0 else None

    def _proxyapi_budget_exceeded(self) -> bool:
        return getattr(self.proxy, "last_error_kind", None) == "budget_exceeded"

    def _proxyapi_budget_depleted_from_api(self) -> bool:
        snap = self.cost_tracker.get_balance_snapshot()
        if snap.budget_limit is None or snap.budget_used is None:
            return False
        return float(snap.budget_used) >= float(snap.budget_limit) - 1e-6

    def _persist_proxyapi_budget_alert(self, digest_id: int) -> None:
        self.db.query(Asset).filter(
            Asset.digest_id == digest_id,
            Asset.type == ASSET_PROXYAPI_BUDGET_ALERT,
        ).delete()
        self.db.add(
            Asset(
                digest_id=digest_id,
                type=ASSET_PROXYAPI_BUDGET_ALERT,
                path="",
                prompt=PROXYAPI_BUDGET_USER_MESSAGE,
            )
        )
        self.db.commit()

    def _clear_proxyapi_budget_alert(self, digest_id: int) -> None:
        self.db.query(Asset).filter(
            Asset.digest_id == digest_id,
            Asset.type == ASSET_PROXYAPI_BUDGET_ALERT,
        ).delete()

    def _raise_proxyapi_budget_exceeded(self, digest_id: int) -> None:
        logger.warning("Шаг 1: исчерпан бюджет ключа ProxyAPI | digest_id=%s", digest_id)
        self._persist_proxyapi_budget_alert(digest_id)
        raise HTTPException(status_code=402, detail=PROXYAPI_BUDGET_USER_MESSAGE)

    def digest_proxyapi_budget_exceeded(self, digest_id: int) -> bool:
        row = (
            self.db.query(Asset)
            .filter(Asset.digest_id == digest_id, Asset.type == ASSET_PROXYAPI_BUDGET_ALERT)
            .first()
        )
        return row is not None

    def proxyapi_budget_alert_message(self, digest_id: int) -> str | None:
        row = (
            self.db.query(Asset)
            .filter(Asset.digest_id == digest_id, Asset.type == ASSET_PROXYAPI_BUDGET_ALERT)
            .first()
        )
        if row and (row.prompt or "").strip():
            return row.prompt.strip()
        return PROXYAPI_BUDGET_USER_MESSAGE if row else None

    def digest_proxyapi_budget_blocked_message(self, digest_id: int) -> str | None:
        saved = self.proxyapi_budget_alert_message(digest_id)
        if saved:
            return saved
        if self._proxyapi_budget_depleted_from_api():
            return PROXYAPI_BUDGET_USER_MESSAGE
        return None

    def build_budget_notices(self, digest: Digest) -> list[str]:
        """Человекочитаемые предупреждения о лимите расходов для UI."""
        notices: list[str] = []
        budget_msg = self.proxyapi_budget_alert_message(digest.id)
        if budget_msg:
            notices.insert(0, budget_msg)
        elif self._proxyapi_budget_depleted_from_api():
            notices.insert(0, PROXYAPI_BUDGET_USER_MESSAGE)
        if digest.step1_budget_capped:
            spent = self.digest_proxyapi_cost_rub(digest) or self._digest_proxyapi_spent_rub(digest)
            lim = self.settings.step1_max_cost_rub
            notices.append(
                f"Достигнут лимит расходов на сбор кандидатов ({lim:g} ₽). По учёту ProxyAPI на этом шаге ~{spent:.2f} ₽; "
                "дополнительный добор новостей через ИИ остановлен — список мог быть короче 10 позиций. "
                "При необходимости увеличьте лимит в настройках сервера (STEP1_MAX_COST_RUB) или задайте прямые ссылки на статьи."
            )
        if digest.step2_budget_capped:
            spent2 = self.digest_proxyapi_cost_rub(digest) or self._digest_proxyapi_spent_rub(digest)
            lim2 = self.settings.step2_max_cost_rub
            notices.append(
                f"Достигнут лимит расходов на упорядочивание новостей ({lim2:g} ₽). На этом шаге уже ~{spent2:.2f} ₽; "
                "последнее упорядочивание выполнено без ИИ (порядок без изменения выбранных материалов). "
                "Чтобы снова использовать AI-порядок, увеличьте STEP2_MAX_COST_RUB или начните новый выбор пяти новостей после сброса расходов."
            )
        return notices

    def create_digest_for_today(self) -> Digest:
        today = datetime.now(MSK_TZ).date()
        digest = self.db.query(Digest).filter(Digest.date == today).first()
        if digest:
            logger.info("Выпуск на сегодня уже существует | digest_id=%s date=%s", digest.id, today)
            return digest
        digest = Digest(
            date=today,
            status=STATUS_DRAFT,
            current_step=STATUS_DRAFT,
            news_window_days=3,
            news_window_day_kind="working",
        )
        self.db.add(digest)
        self.db.commit()
        self.db.refresh(digest)
        logger.info("Создан новый выпуск на сегодня | digest_id=%s date=%s", digest.id, today)
        return digest

    def list_digests(self) -> list[Digest]:
        return self.db.query(Digest).order_by(Digest.date.desc()).all()

    def get_digest(self, digest_id: int) -> Digest:
        digest = self.db.query(Digest).filter(Digest.id == digest_id).first()
        if not digest:
            raise HTTPException(status_code=404, detail="Digest not found")
        return digest

    def run_step_0(
        self,
        digest_id: int,
        digest_type: str | None,
        *,
        news_window_days: int = 3,
        news_window_day_kind: str = "working",
    ) -> Digest:
        digest = self.get_digest(digest_id)
        via_default = digest_type is None
        if via_default:
            weekday = datetime.now(MSK_TZ).weekday()
            digest_type = "serious" if weekday < 5 else "curious"
        if digest_type not in {"serious", "curious"}:
            raise HTTPException(status_code=400, detail="digest_type must be serious or curious")
        digest.digest_type = digest_type
        digest.digest_type_via_default = via_default
        digest.news_window_days = max(1, min(90, int(news_window_days)))
        digest.news_window_day_kind = _normalize_news_window_day_kind(news_window_day_kind)
        digest.status = STATUS_STEP0
        digest.current_step = STATUS_STEP0
        self.db.commit()
        self.db.refresh(digest)
        logger.info(
            "Шаг 0: параметры выпуска | digest_id=%s type=%s window_days=%s window_kind=%s",
            digest.id,
            digest_type,
            digest.news_window_days,
            digest.news_window_day_kind,
        )
        return digest

    def _backup_verified_candidate_dicts(self, digest_id: int) -> list[dict[str, Any]]:
        rows = (
            self.db.query(NewsCandidate)
            .filter(
                NewsCandidate.digest_id == digest_id,
                NewsCandidate.link_status.is_(True),
                NewsCandidate.headline_editorial_ok.is_(True),
            )
            .order_by(NewsCandidate.original_number)
            .all()
        )
        return [
            {
                "original_number": c.original_number,
                "title": c.title,
                "url": c.url,
                "source": c.source,
                "tier": c.tier,
                "published_at": c.published_at,
                "category": c.category,
                "description": c.description,
                "significance_score": c.significance_score,
                "novelty_score": c.novelty_score,
                "impact_score": c.impact_score,
                "total_score": c.total_score,
                "reliability_status": c.reliability_status,
                "link_status": True,
                "headline_editorial_ok": True,
                "page_verified": True,
                "is_foreign_agent": c.is_foreign_agent,
                "is_aggregator": c.is_aggregator,
                "is_duplicate": c.is_duplicate,
                "verification_comment": c.verification_comment or "",
            }
            for c in rows
        ]

    def _restore_step1_verified_candidates(self, digest_id: int, items: list[dict[str, Any]]) -> None:
        self.db.query(NewsCandidate).filter(NewsCandidate.digest_id == digest_id).delete()
        seen_urls_lower: set[str] = set()
        for idx, item in enumerate(items, start=1):
            url_norm = str(item.get("url", "")).strip().lower()
            is_duplicate = url_norm in seen_urls_lower
            seen_urls_lower.add(url_norm)
            self.db.add(
                NewsCandidate(
                    digest_id=digest_id,
                    original_number=idx,
                    title=str(item.get("title", ""))[:500],
                    url=str(item.get("url", ""))[:1000],
                    source=str(item.get("source", ""))[:255],
                    tier=str(item.get("tier", "Tier-3"))[:32],
                    published_at=str(item.get("published_at", "")),
                    category=str(item.get("category", "technology"))[:120],
                    description=str(item.get("description", "")),
                    significance_score=int(item.get("significance_score", 1)),
                    novelty_score=int(item.get("novelty_score", 1)),
                    impact_score=int(item.get("impact_score", 1)),
                    total_score=int(item.get("total_score", 3)),
                    reliability_status=str(item.get("reliability_status", "✅ подтверждено"))[:40],
                    link_status=True,
                    headline_editorial_ok=True,
                    page_verified=True,
                    is_foreign_agent=bool(item.get("is_foreign_agent", False)),
                    is_aggregator=bool(item.get("is_aggregator", False)),
                    is_duplicate=is_duplicate,
                    verification_comment=str(item.get("verification_comment", "")),
                )
            )
        self.db.commit()

    def _persist_step1_preview_candidates(self, digest_id: int, rows_by_fp: dict[str, dict[str, Any]]) -> None:
        """При неуспешном шаге 1 сохраняем карточки в БД для UI (часть строк может быть с отбраковкой)."""
        if not rows_by_fp:
            return
        self.db.query(NewsCandidate).filter(NewsCandidate.digest_id == digest_id).delete()
        ordered = sorted(rows_by_fp.values(), key=lambda x: int(x.get("original_number") or 9999))
        seen_lower: set[str] = set()
        seq = 0
        for item in ordered[:24]:
            url_norm = str(item.get("url", "")).strip().lower()
            if not url_norm.startswith("http"):
                continue
            if url_norm in seen_lower:
                continue
            seen_lower.add(url_norm)
            seq += 1
            title = str(item.get("title") or "").strip()
            if len(title) < 4:
                title = f"Страница: {_host_from_url(str(item.get('url', '')))}"
            entity = NewsCandidate(
                digest_id=digest_id,
                original_number=seq,
                title=title[:500],
                url=str(item.get("url", ""))[:1000],
                source=str(item.get("source", "") or _host_from_url(str(item.get("url", ""))))[:255],
                tier=str(item.get("tier", "Tier-3"))[:32],
                published_at=str(item.get("published_at", ""))[:100] or PUBLISHED_AT_UNDEFINED,
                category=str(item.get("category", "technology"))[:120],
                description=str(item.get("description", "")),
                significance_score=int(item.get("significance_score", 1)),
                novelty_score=int(item.get("novelty_score", 1)),
                impact_score=int(item.get("impact_score", 1)),
                total_score=int(item.get("total_score", 3)),
                reliability_status=str(item.get("reliability_status", "⚠️ сомнительный"))[:40],
                link_status=bool(item.get("link_status", False)),
                headline_editorial_ok=bool(item.get("headline_editorial_ok", False)),
                page_verified=bool(item.get("headline_editorial_ok", False)) and bool(item.get("link_status", False)),
                is_foreign_agent=bool(item.get("is_foreign_agent", False)),
                is_aggregator=bool(item.get("is_aggregator", False)),
                is_duplicate=bool(item.get("is_duplicate", False)),
                verification_comment=str(item.get("verification_comment", "")),
            )
            self.db.add(entity)
        self.db.commit()

    def _start_step1_discovery_run(self, digest: Digest) -> Step1DiscoveryRun:
        last = (
            self.db.query(Step1DiscoveryRun)
            .filter(Step1DiscoveryRun.digest_id == digest.id)
            .order_by(Step1DiscoveryRun.run_number.desc())
            .first()
        )
        run_number = (last.run_number + 1) if last else 1
        run = Step1DiscoveryRun(
            digest_id=digest.id,
            run_number=run_number,
            started_at=datetime.utcnow(),
        )
        self.db.add(run)
        self.db.commit()
        self.db.refresh(run)
        return run

    def _digest_pool_date(self, digest: Digest) -> date:
        raw = digest.date
        if isinstance(raw, datetime):
            return raw.date()
        return raw

    def _persist_step1_discovered_news(
        self,
        digest_id: int,
        discovery_run_id: int,
        rows_by_fp: dict[str, dict[str, Any]],
    ) -> None:
        if not rows_by_fp:
            return
        prev_rows = (
            self.db.query(Step1DiscoveredNews)
            .filter(Step1DiscoveredNews.digest_id == digest_id)
            .all()
        )
        prev_eval_by_url = {
            str(row.url).strip().lower(): (
                row.manual_score,
                row.manual_reason,
                row.manual_reason_other,
                row.rated_at,
            )
            for row in prev_rows
        }
        run = self.db.query(Step1DiscoveryRun).filter(Step1DiscoveryRun.id == discovery_run_id).first()
        if run is None:
            return
        self.db.query(Step1DiscoveredNews).filter(Step1DiscoveredNews.digest_id == digest_id).delete()
        ordered = sorted(rows_by_fp.values(), key=lambda x: int(x.get("original_number") or 999999))
        seen: set[str] = set()
        saved = 0
        for item in ordered[:300]:
            url = str(item.get("url") or "").strip()
            if not url.startswith("http"):
                continue
            key = url.lower()
            if key in seen:
                continue
            seen.add(key)
            title = str(item.get("title") or "").strip()
            if len(title) < 4:
                title = f"Страница: {_host_from_url(url)}"
            reject_codes = _reject_reason_codes(str(item.get("verification_comment") or ""))
            prev = prev_eval_by_url.get(key)
            saved += 1
            self.db.add(
                Step1DiscoveredNews(
                    digest_id=digest_id,
                    discovery_run_id=discovery_run_id,
                    source_stage=str(item.get("source_stage") or "step1"),
                    title=title[:500],
                    url=url[:1000],
                    source=str(item.get("source") or _host_from_url(url))[:255],
                    published_at=str(item.get("published_at") or PUBLISHED_AT_UNDEFINED)[:100],
                    headline_editorial_ok=bool(item.get("headline_editorial_ok", False)),
                    link_status=bool(item.get("link_status", False)),
                    page_verified=bool(item.get("headline_editorial_ok", False)) and bool(item.get("link_status", False)),
                    reject_codes=",".join(reject_codes),
                    verification_comment=str(item.get("verification_comment") or ""),
                    manual_score=prev[0] if prev else None,
                    manual_reason=prev[1] if prev else None,
                    manual_reason_other=prev[2] if prev else None,
                    rated_at=prev[3] if prev else None,
                )
            )
        run.pool_formed_at = datetime.utcnow()
        run.news_count = saved
        self.db.commit()
        try:
            sync_step1_manual_ratings_export(self.db, self.settings.step1_manual_ratings_path)
        except Exception:
            logger.exception("Не удалось обновить файл ручных оценок шага 1")

    def _append_manual_rating_log(self, row: Step1DiscoveredNews, digest: Digest) -> None:
        if row.discovery_run_id is None:
            return
        run = self.db.query(Step1DiscoveryRun).filter(Step1DiscoveryRun.id == row.discovery_run_id).first()
        if run is None:
            return
        pool_date = self._digest_pool_date(digest)
        url_key = str(row.url).strip().lower()
        run_logs = (
            self.db.query(Step1ManualRatingLog)
            .filter(Step1ManualRatingLog.discovery_run_id == run.id)
            .all()
        )
        existing = next((x for x in run_logs if str(x.url).strip().lower() == url_key), None)
        if existing is not None:
            existing.discovered_news_id = row.id
            existing.title = row.title
            existing.published_at = row.published_at
            existing.manual_score = int(row.manual_score or 0)
            existing.manual_reason = row.manual_reason
            existing.manual_reason_other = row.manual_reason_other
            existing.rated_at = row.rated_at or datetime.utcnow()
        else:
            self.db.add(
                Step1ManualRatingLog(
                    discovery_run_id=run.id,
                    digest_id=digest.id,
                    pool_date=pool_date,
                    run_number=run.run_number,
                    discovered_news_id=row.id,
                    title=row.title,
                    url=row.url,
                    published_at=row.published_at,
                    manual_score=int(row.manual_score or 0),
                    manual_reason=row.manual_reason,
                    manual_reason_other=row.manual_reason_other,
                    rated_at=row.rated_at or datetime.utcnow(),
                )
            )
        self.db.commit()

    def save_step1_discovered_feedback(
        self,
        *,
        digest_id: int,
        news_id: int,
        score: int,
        reason: str | None,
        reason_other: str | None,
    ) -> Step1DiscoveredNews:
        row = (
            self.db.query(Step1DiscoveredNews)
            .filter(
                Step1DiscoveredNews.id == news_id,
                Step1DiscoveredNews.digest_id == digest_id,
            )
            .first()
        )
        if row is None:
            raise HTTPException(status_code=404, detail="Новость из полного пула не найдена.")
        if score not in (1, 2, 3):
            raise HTTPException(status_code=400, detail="Оценка должна быть от 1 до 3.")
        clean_reason = (reason or "").strip() or None
        clean_other = (reason_other or "").strip() or None
        if score < 3:
            if clean_reason not in STEP1_DISCOVERED_REASON_CODES:
                raise HTTPException(status_code=400, detail="Для оценок ниже 3 укажите причину из списка.")
            if clean_reason == "other" and not clean_other:
                raise HTTPException(status_code=400, detail="Для причины «другое» заполните пояснение.")
        else:
            clean_reason = None
            clean_other = None
        if clean_reason != "other":
            clean_other = None
        row.manual_score = score
        row.manual_reason = clean_reason
        row.manual_reason_other = clean_other
        row.rated_at = datetime.utcnow()
        digest = self.get_digest(digest_id)
        self.db.commit()
        self.db.refresh(row)
        self._append_manual_rating_log(row, digest)
        try:
            sync_step1_manual_ratings_export(self.db, self.settings.step1_manual_ratings_path)
        except Exception:
            logger.exception("Не удалось обновить файл ручных оценок шага 1")
        return row

    def run_step_1(self, digest_id: int, manual_urls: list[str], *, rebuild: bool = False) -> list[NewsCandidate]:
        digest = self.get_digest(digest_id)
        if digest.status == STATUS_DRAFT:
            raise HTTPException(status_code=400, detail="Step 1 requires step_0 (choose digest type first).")
        step1_allowed = {STATUS_STEP0, STATUS_STEP1, STATUS_SELECTED, STATUS_ANALYTICS, STATUS_FINAL}
        if digest.status not in step1_allowed:
            raise HTTPException(status_code=400, detail=f"Cannot run step 1 from status {digest.status!r}")
        past_step1 = digest.status in {STATUS_SELECTED, STATUS_ANALYTICS, STATUS_FINAL}
        if past_step1 and not rebuild:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Выпуск уже прошёл выбор новостей. Для полной пересборки пула передайте rebuild=true "
                    "(сбросятся шаги 2–4: выбор, порядок, аналитика, финал)."
                ),
            )
        if rebuild and not past_step1 and digest.status != STATUS_STEP1:
            rebuild = False

        normalized_manual_urls = self._normalize_manual_urls(manual_urls)
        if not self.settings.enable_web_fetch and not normalized_manual_urls:
            raise HTTPException(
                status_code=400,
                detail="Нет веб-доступа (ENABLE_WEB_FETCH=false). Вставьте вручную 5-10 ссылок в поле manual_urls.",
            )

        self.db.query(SelectedNews).filter(SelectedNews.digest_id == digest.id).delete()
        self.db.query(Analytics).filter(Analytics.digest_id == digest.id).delete()
        self.db.query(FinalOutput).filter(FinalOutput.digest_id == digest.id).delete()
        self.db.query(QualityCheck).filter(QualityCheck.digest_id == digest.id).delete()
        self.db.query(Asset).filter(Asset.digest_id == digest.id).delete()
        # Записи llm_cost_records не удаляем: иначе «Суммарно AI» занижается после пересборки.
        prev_verified_backup = self._backup_verified_candidate_dicts(digest.id)

        digest.step1_budget_capped = False
        digest.step2_budget_capped = False

        if rebuild or past_step1:
            logger.info(
                "Шаг 1: полная пересборка пула | digest_id=%s prev_status=%s manual_urls=%s",
                digest.id,
                digest.status,
                len(normalized_manual_urls),
            )
        else:
            logger.info(
                "Шаг 1: запуск сбора кандидатов | digest_id=%s manual_urls=%s",
                digest.id,
                len(normalized_manual_urls),
            )
        discovered_by_fp: dict[str, dict[str, Any]] = {}
        discovery_run = self._start_step1_discovery_run(digest)
        try:
            self._snapshot_proxyapi_before(digest, reset=(rebuild or past_step1))
            if (
                self.settings.enable_web_fetch
                and not normalized_manual_urls
                and self.digest_proxyapi_budget_exceeded(digest.id)
            ):
                self._raise_proxyapi_budget_exceeded(digest.id)
            now_msk = current_msk_iso()
            candidates: list[dict[str, Any]] = []
            verify_candidates: list[dict[str, Any]] = []
            guard_rejected: list[dict[str, Any]] = []
            research_prefilter_dropped: list[dict[str, Any]] = []
            web_flow_available = self.settings.enable_web_fetch
            preview_by_fp: dict[str, dict[str, Any]] = {}
    
            def snapshot_preview_row(row: dict[str, Any]) -> None:
                fp = _url_fingerprint(str(row.get("url", "")).strip())
                if fp:
                    snap = dict(row)
                    if not snap.get("source_stage"):
                        snap["source_stage"] = "step1"
                    preview_by_fp[fp] = snap
                    discovered_by_fp[fp] = dict(snap)
    
            manual_candidates = self._build_manual_candidates(digest, normalized_manual_urls, now_msk)
            failed_manual = [str(x["url"])[:220] for x in manual_candidates if not x.get("page_verified")]
            if failed_manual:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        "Не удалось подтвердить ручные ссылки (страница недоступна или не отвечает): "
                        + "; ".join(failed_manual[:12])
                    ),
                )
    
            verified_pool: list[dict[str, Any]] = []
            seen_fp: set[str] = set()
            excluded_urls: list[str] = []
            reject_stats: dict[str, int] = {}
    
            def append_verified(item: dict[str, Any]) -> None:
                if not item.get("headline_editorial_ok") or not item.get("link_status"):
                    return
                if bool(item.get("is_aggregator")):
                    return
                fp = _url_fingerprint(str(item.get("url", "")))
                if not fp or fp in seen_fp:
                    return
                seen_fp.add(fp)
                verified_pool.append(item)
    
            def register_reject(item: dict[str, Any]) -> None:
                codes = _reject_reason_codes(str(item.get("verification_comment") or ""))
                if not codes:
                    codes = ["unknown_reject"]
                for code in codes:
                    reject_stats[code] = reject_stats.get(code, 0) + 1
    
            def persist_reject_stats() -> None:
                if not reject_stats:
                    return
                self.db.query(Asset).filter(Asset.digest_id == digest.id, Asset.type == "step1_rejected_reasons").delete()
                self.db.add(
                    Asset(
                        digest_id=digest.id,
                        type="step1_rejected_reasons",
                        path="",
                        prompt=json.dumps(reject_stats, ensure_ascii=False),
                    )
                )
                self.db.commit()
    
            def top_reject_reasons(limit: int = 3) -> str:
                if not reject_stats:
                    return ""
                items = sorted(reject_stats.items(), key=lambda x: (-x[1], x[0]))[:limit]
                details = ", ".join(f"{code}={count}" for code, count in items)
                return f" Основные причины отбраковки: {details}."
    
            for m in manual_candidates:
                snapshot_preview_row(m)
                append_verified(m)
    
            if self.settings.enable_web_fetch and len(verified_pool) < STEP1_TARGET_VERIFIED:
                try:
                    for item in self._collect_search_verified_candidates(
                        digest.id,
                        digest,
                        now_msk,
                        seen_fp,
                        limit=max(STEP1_TARGET_VERIFIED, STEP1_MIN_VERIFIED * 2),
                        on_row=snapshot_preview_row,
                    ):
                        append_verified(item)
                except Exception:
                    web_flow_available = False
                    logger.exception("Шаг 1: сбор URL через веб-поиск не выполнен")
            if (
                self._proxyapi_budget_exceeded()
                and len(verified_pool) < STEP1_MIN_VERIFIED
                and not normalized_manual_urls
            ):
                self._raise_proxyapi_budget_exceeded(digest.id)

            if self.settings.enable_web_fetch and len(verified_pool) < STEP1_MIN_VERIFIED:
                self._step1_run_web_supplement_rounds(
                    digest,
                    verified_pool=verified_pool,
                    seen_fp=seen_fp,
                    excluded_urls=excluded_urls,
                    now_msk=now_msk,
                    snapshot_preview_row=snapshot_preview_row,
                    append_verified=append_verified,
                    register_reject=register_reject,
                    stop_at=STEP1_MIN_VERIFIED,
                    max_rounds=STEP1_PRE_CREW_SUPPLEMENT_ROUNDS,
                )
    
            # CrewAI research/score часто портят URL; если веб-поиск уже дал минимум — не вызываем LLM-цепочку.
            if self.settings.enable_web_fetch and len(verified_pool) < STEP1_MIN_VERIFIED:
                try:
                    research_candidates = self.workflow.run_candidates_research(
                        digest_type=digest.digest_type or "serious",
                        now_msk=now_msk,
                        manual_urls=normalized_manual_urls,
                    )
                    research_candidates, research_prefilter_dropped = self._prefilter_llm_candidates_fetchable(
                        digest.id, research_candidates
                    )
                    for dropped in research_prefilter_dropped:
                        snapshot_preview_row(dropped)
                    verify_candidates = self.workflow.run_candidates_verify(research_candidates)
                    scored_candidates = self.workflow.run_candidates_score(verify_candidates, now_msk=now_msk)
                    candidates, guard_rejected = self._filter_score_url_mutations(verify_candidates, scored_candidates)
                    for item in guard_rejected:
                        snapshot_preview_row(item)
                except Exception:
                    web_flow_available = False
                    logger.exception("Шаг 1: веб-поиск недоступен, переключение на ручные ссылки")
                    if self._proxyapi_budget_exceeded() and not normalized_manual_urls:
                        self._raise_proxyapi_budget_exceeded(digest.id)
                    if not normalized_manual_urls:
                        raise HTTPException(
                            status_code=400,
                            detail="Веб-поиск временно недоступен. Вставьте вручную 5-10 ссылок в поле manual_urls.",
                        )
    
            for item in guard_rejected:
                register_reject(item)
                raw_u = str(item.get("url") or "").strip()
                if raw_u.startswith("http"):
                    excluded_urls.append(raw_u[:800])
    
            for item in research_prefilter_dropped:
                register_reject(item)
                raw_u = str(item.get("url") or "").strip()
                if raw_u.startswith("http"):
                    excluded_urls.append(raw_u[:800])
    
            llm_merged = self._merge_candidates([], candidates, limit=40) if candidates else []
            for item in llm_merged:
                if _is_placeholder_candidate_dict(item):
                    continue
                raw_u = str(item.get("url") or "").strip()
                if not raw_u.startswith("http"):
                    continue
                for resolved_url, bundle in _expand_listing_url_candidates(raw_u, max_children=6):
                    if _url_fingerprint(resolved_url) in seen_fp:
                        continue
                    work = dict(item)
                    work["url"] = resolved_url
                    work["title"] = ""
                    work["headline_editorial_ok"] = False
                    work["link_status"] = False
                    self._verify_llm_candidate_dict(digest, work, prefetched_bundle=bundle)
                    snapshot_preview_row(work)
                    if work.get("headline_editorial_ok") and work.get("link_status"):
                        append_verified(work)
                    else:
                        excluded_urls.append(resolved_url[:800])
                        register_reject(work)
    
            if len(verified_pool) < STEP1_TARGET_VERIFIED:
                self._step1_run_web_supplement_rounds(
                    digest,
                    verified_pool=verified_pool,
                    seen_fp=seen_fp,
                    excluded_urls=excluded_urls,
                    now_msk=now_msk,
                    snapshot_preview_row=snapshot_preview_row,
                    append_verified=append_verified,
                    register_reject=register_reject,
                    stop_at=STEP1_TARGET_VERIFIED,
                    max_rounds=STEP1_SUPPLEMENT_MAX_ROUNDS,
                )
    
            if verified_pool and all(_is_placeholder_candidate_dict(x) for x in verified_pool):
                persist_reject_stats()
                self._persist_step1_preview_candidates(digest.id, preview_by_fp)
                raise HTTPException(
                    status_code=502,
                    detail=(
                        "Не удалось получить реальные новости: модель вернула невалидный ответ или сработал внутренний шаблон-заглушка. "
                        "Проверьте PROXYAPI_API_KEY и снова запустите шаг 1. "
                        "Либо задайте ENABLE_WEB_FETCH=false в backend/.env и вставьте 5–10 прямых URL на статьи в поле шага 1 — "
                        "заголовки будут подтянуты с страниц без LLM-поиска."
                    ),
                )
    
            if not web_flow_available and len(candidates) == 0 and not normalized_manual_urls:
                raise HTTPException(
                    status_code=400,
                    detail="Не удалось собрать кандидатов автоматически. Вставьте вручную 5-10 ссылок.",
                )
    
            if len(verified_pool) < STEP1_MIN_VERIFIED:
                persist_reject_stats()
                if rebuild and len(prev_verified_backup) >= STEP1_MIN_VERIFIED:
                    self._restore_step1_verified_candidates(digest.id, prev_verified_backup)
                    raise HTTPException(
                        status_code=502,
                        detail=(
                            f"Пересборка не дала {STEP1_MIN_VERIFIED} подтверждённых материалов (получено {len(verified_pool)}). "
                            f"Восстановлен прежний пул из {len(prev_verified_backup)} проверенных новостей."
                            + top_reject_reasons()
                        ),
                    )
                self._persist_step1_preview_candidates(digest.id, preview_by_fp)
                spent_step1 = self._digest_proxyapi_spent_rub(digest)
                tail = (
                    f" Сработал лимит расходов на сбор кандидатов ({self.settings.step1_max_cost_rub:g} ₽): по учёту ProxyAPI ~{spent_step1:.2f} ₽. "
                    "Увеличьте STEP1_MAX_COST_RUB в backend/.env или добавьте прямые URL статей."
                    if spent_step1 >= self.settings.step1_max_cost_rub - 1e-9
                    else ""
                )
                search_hint = ""
                if not normalized_manual_urls and self.settings.enable_web_fetch:
                    search_hint = (
                        " Проверьте PROXYAPI_API_KEY и веб-поиск (PROXYAPI_WEB_SEARCH_ENABLED=true) "
                        "или добавьте SERPAPI_API_KEY / TAVILY_API_KEY в backend/.env."
                    )
                if self._proxyapi_budget_exceeded() or self.digest_proxyapi_budget_exceeded(digest.id):
                    self._raise_proxyapi_budget_exceeded(digest.id)
                raise HTTPException(
                    status_code=502,
                    detail=(
                        f"Подтверждено только {len(verified_pool)} материалов по страницам (нужно минимум {STEP1_MIN_VERIFIED}). "
                        "Добавьте прямые URL статей в поле шага 1 — автопоиск идёт через ProxyAPI web_search (и опционально SerpAPI/Tavily)."
                        + top_reject_reasons()
                        + search_hint
                        + tail
                    ),
                )
    
            self._clear_proxyapi_budget_alert(digest.id)

            # Кандидатов удаляем только перед успешной записью: промежуточные commit не должны оставлять пустой список при 502.
            self.db.query(NewsCandidate).filter(NewsCandidate.digest_id == digest.id).delete()
    
            verified_pool = verified_pool[:STEP1_TARGET_VERIFIED]
            entities: list[NewsCandidate] = []
            seen_urls_lower: set[str] = set()
            for idx, item in enumerate(verified_pool, start=1):
                url_norm = str(item.get("url", "")).strip().lower()
                is_duplicate = url_norm in seen_urls_lower or bool(item.get("is_duplicate", False))
                seen_urls_lower.add(url_norm)
                entity = NewsCandidate(
                    digest_id=digest.id,
                    original_number=idx,
                    title=str(item.get("title", ""))[:500],
                    url=str(item.get("url", ""))[:1000],
                    source=str(item.get("source", ""))[:255],
                    tier=str(item.get("tier", "Tier-3"))[:32],
                    published_at=str(item.get("published_at", "")),
                    category=str(item.get("category", "technology"))[:120],
                    description=str(item.get("description", "")),
                    significance_score=int(item.get("significance_score", 1)),
                    novelty_score=int(item.get("novelty_score", 1)),
                    impact_score=int(item.get("impact_score", 1)),
                    total_score=int(item.get("total_score", 3)),
                    reliability_status=str(item.get("reliability_status", "⚠️ сомнительный")),
                    link_status=bool(item.get("link_status", False)),
                    headline_editorial_ok=bool(item.get("headline_editorial_ok", False)),
                    page_verified=bool(item.get("headline_editorial_ok", False)) and bool(item.get("link_status", False)),
                    is_foreign_agent=bool(item.get("is_foreign_agent", False)),
                    is_aggregator=bool(item.get("is_aggregator", False)),
                    is_duplicate=is_duplicate,
                    verification_comment=str(item.get("verification_comment", "")),
                )
                entities.append(entity)
                self.db.add(entity)
    
            spent_step1_final = self._digest_proxyapi_spent_rub(digest)
            digest.step1_budget_capped = spent_step1_final >= self.settings.step1_max_cost_rub - 1e-9
            self.db.add(
                Asset(
                    digest_id=digest.id,
                    type="step1_rejected_reasons",
                    path="",
                    prompt=json.dumps(reject_stats, ensure_ascii=False),
                )
            )
            digest.status = STATUS_STEP1
            digest.current_step = STATUS_STEP1
            self.db.commit()
            logger.info(
                "Шаг 1: сохранено проверенных кандидатов | digest_id=%s count=%s rejected=%s",
                digest.id,
                len(entities),
                reject_stats,
            )
            return entities

        finally:
            if discovered_by_fp:
                self._persist_step1_discovered_news(digest.id, discovery_run.id, discovered_by_fp)
            self.db.refresh(digest)
            self._snapshot_proxyapi_after(digest)
            self._record_proxyapi_step_cost(
                digest,
                step="step_1",
                agent_name="NewsResearchAgent",
                request_label="step_1_collect_pool",
                model=AGENT_MODEL_RECOMMENDATIONS["NewsResearchAgent"],
            )

    def select_news(self, digest_id: int, selected_ids: list[int], top5: bool) -> list[SelectedNews]:
        digest = self.get_digest(digest_id)
        if digest.status != STATUS_STEP1:
            raise HTTPException(status_code=400, detail="Selection requires step_1_candidates")

        candidates = self.db.query(NewsCandidate).filter(NewsCandidate.digest_id == digest.id).all()
        if len(candidates) < 5:
            raise HTTPException(status_code=400, detail="Недостаточно кандидатов для выбора: нужно минимум 5.")
        candidate_by_id = {c.id: c for c in candidates}
        strict_allowed = [
            c
            for c in candidates
            if c.headline_editorial_ok
            and c.link_status
            and c.reliability_status != "❗ без подтверждения"
            and not c.is_aggregator
            and not c.is_duplicate
        ]
        mandatory_manual = [c for c in strict_allowed if self._is_manual_required_candidate(c.verification_comment)]
        if top5:
            if len(mandatory_manual) > 5:
                raise HTTPException(status_code=400, detail="Слишком много обязательных ручных ссылок: максимум 5.")
            chosen = list(mandatory_manual)
            strict_rest = [
                c for c in sorted(strict_allowed, key=lambda x: x.total_score, reverse=True) if c.id not in {m.id for m in chosen}
            ]
            chosen.extend(strict_rest[: max(0, 5 - len(chosen))])
            if len(chosen) < 5:
                raise HTTPException(
                    status_code=400,
                    detail="Недостаточно проверенных по странице кандидатов для топ-5. Запустите шаг 1 снова или добавьте ручные URL.",
                )
        else:
            uniq_ids = list(dict.fromkeys(selected_ids))
            if len(uniq_ids) != 5:
                raise HTTPException(status_code=400, detail="Нужно выбрать ровно 5 новостей")
            id_set = set(uniq_ids)
            chosen = [candidate_by_id[cid] for cid in uniq_ids if cid in candidate_by_id]
            if len(chosen) != 5:
                raise HTTPException(status_code=400, detail="Выбраны недопустимые новости")
            mandatory_ids = {c.id for c in mandatory_manual}
            if not mandatory_ids.issubset(id_set):
                raise HTTPException(
                    status_code=400,
                    detail="Не все обязательные ручные ссылки включены в выбор. Добавьте их в итоговые 5 новостей.",
                )
            for c in chosen:
                if not c.headline_editorial_ok or not c.link_status:
                    raise HTTPException(
                        status_code=400,
                        detail="Можно выбирать только новости с читаемым заголовком и рабочей ссылкой на материал.",
                    )

        self.db.query(SelectedNews).filter(SelectedNews.digest_id == digest.id).delete()
        created: list[SelectedNews] = []
        for idx, c in enumerate(chosen, start=1):
            item = SelectedNews(
                digest_id=digest.id,
                candidate_id=c.id,
                original_number=c.original_number,
                output_position=idx,
                ordering_reason="Выбрано пользователем",
            )
            self.db.add(item)
            created.append(item)
        digest.status = STATUS_SELECTED
        digest.current_step = "step_2"
        digest.step2_budget_capped = False
        self.db.commit()
        logger.info(
            "Выбор новостей | digest_id=%s top5=%s candidate_ids=%s",
            digest.id,
            top5,
            [c.id for c in chosen],
        )
        return created

    def run_step_2_order(self, digest_id: int, ordered_candidate_ids: list[int]) -> list[SelectedNews]:
        digest = self.get_digest(digest_id)
        if digest.status != STATUS_SELECTED:
            raise HTTPException(status_code=400, detail="Ordering requires selected status")
        selected = self.db.query(SelectedNews).filter(SelectedNews.digest_id == digest.id).order_by(SelectedNews.id).all()
        if len(selected) != 5:
            raise HTTPException(status_code=400, detail="Need exactly 5 selected news")

        selected_ids = [s.candidate_id for s in selected]
        if ordered_candidate_ids:
            if set(ordered_candidate_ids) != set(selected_ids):
                raise HTTPException(status_code=400, detail="Можно менять только порядок выбранных новостей")
            order_payload = [{"candidate_id": cid} for cid in ordered_candidate_ids]
        else:
            order_payload = [{"candidate_id": cid} for cid in selected_ids]

        with self._digest_cost_session(
            digest,
            step="step_2",
            agent_name="OrderingAgent",
            request_label="step_2_ordering",
            model=AGENT_MODEL_RECOMMENDATIONS["OrderingAgent"],
        ):
            spent_step2_before = self._digest_proxyapi_spent_rub(digest)
            if spent_step2_before >= self.settings.step2_max_cost_rub:
                logger.warning(
                    "Шаг 2: порядок без OrderingAgent — достигнут STEP2_MAX_COST_RUB (%s ₽) | digest_id=%s spent=%.4f",
                    self.settings.step2_max_cost_rub,
                    digest.id,
                    spent_step2_before,
                )
                agent_order = [
                    {
                        "candidate_id": item["candidate_id"],
                        "output_position": idx + 1,
                        "ordering_reason": "Порядок без ИИ: достигнут лимит расходов на упорядочивание (настройка сервера).",
                    }
                    for idx, item in enumerate(order_payload)
                ]
                digest.step2_budget_capped = True
            else:
                agent_order = self.workflow.run_ordering(order_payload)
                digest.step2_budget_capped = False
        for row in selected:
            for ord_item in agent_order:
                if ord_item["candidate_id"] == row.candidate_id:
                    row.output_position = int(ord_item["output_position"])
                    row.ordering_reason = str(ord_item["ordering_reason"])
        self.db.commit()
        ordered = self.db.query(SelectedNews).filter(SelectedNews.digest_id == digest.id).order_by(SelectedNews.output_position).all()
        logger.info(
            "Шаг 2: порядок | digest_id=%s positions=%s",
            digest.id,
            [(r.candidate_id, r.output_position) for r in ordered],
        )
        if self.settings.auto_run_step3_after_order:
            self._run_step3_after_order(digest.id)
        return ordered

    def run_step_2_order_ai_optimal(self, digest_id: int) -> list[SelectedNews]:
        """Оптимальный порядок пятёрки через ProxyAPI (gpt-4.1-mini), без CrewAI."""
        digest = self.get_digest(digest_id)
        if digest.status != STATUS_SELECTED:
            raise HTTPException(status_code=400, detail="AI ordering requires selected status")
        selected = (
            self.db.query(SelectedNews).filter(SelectedNews.digest_id == digest.id).order_by(SelectedNews.output_position).all()
        )
        if len(selected) != 5:
            raise HTTPException(status_code=400, detail="Need exactly 5 selected news")

        candidate_map = {
            c.id: c
            for c in self.db.query(NewsCandidate).filter(NewsCandidate.digest_id == digest.id).all()
        }
        order_input: list[dict[str, Any]] = []
        for row in selected:
            c = candidate_map.get(row.candidate_id)
            if not c:
                continue
            order_input.append(
                {
                    "candidate_id": row.candidate_id,
                    "title": (c.title or "")[:500],
                    "description": (c.description or "")[:800],
                    "source": (c.source or "")[:120],
                    "tier": (c.tier or "")[:32],
                    "total_score": int(c.total_score or 0),
                    "category": (c.category or "")[:80],
                }
            )
        if len(order_input) != 5:
            raise HTTPException(status_code=400, detail="Не все выбранные новости найдены в базе")

        with self._digest_cost_session(
            digest,
            step="step_2",
            agent_name="OrderingAgent",
            request_label="step_2_ai_optimal_order",
            model=STEP2_AI_ORDER_MODEL,
        ):
            spent_step2_before = self._digest_proxyapi_spent_rub(digest)
            if spent_step2_before >= self.settings.step2_max_cost_rub:
                digest.step2_budget_capped = True
                self.db.commit()
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"Достигнут лимит расходов на шаг 2 ({self.settings.step2_max_cost_rub:g} ₽). "
                        "Увеличьте STEP2_MAX_COST_RUB в backend/.env или расставьте порядок вручную."
                    ),
                )

            agent_order = self.proxy.suggest_news_order(
                order_input,
                digest_type=digest.digest_type or "serious",
                model=STEP2_AI_ORDER_MODEL,
            )
            digest.step2_budget_capped = False
        for row in selected:
            for ord_item in agent_order:
                if int(ord_item["candidate_id"]) == row.candidate_id:
                    row.output_position = int(ord_item["output_position"])
                    row.ordering_reason = str(ord_item["ordering_reason"])[:500]
        self.db.commit()
        ordered = (
            self.db.query(SelectedNews).filter(SelectedNews.digest_id == digest.id).order_by(SelectedNews.output_position).all()
        )
        logger.info(
            "Шаг 2: AI-оптимальный порядок | digest_id=%s positions=%s",
            digest.id,
            [(r.candidate_id, r.output_position) for r in ordered],
        )
        if self.settings.auto_run_step3_after_order:
            self._run_step3_after_order(digest.id)
        return ordered

    def _run_step3_after_order(self, digest_id: int) -> dict[str, Any]:
        logger.info("Шаг 3: автозапуск после сохранения порядка | digest_id=%s", digest_id)
        return self.run_step_3_analytics(digest_id, "")

    def run_step_3_analytics(self, digest_id: int, command: str) -> dict[str, Any]:
        digest = self.get_digest(digest_id)
        cmd = (command or "").strip().lower()
        if cmd and cmd != "готово":
            raise HTTPException(
                status_code=400,
                detail='Запустите аналитику кнопкой ниже или введите команду «готово».',
            )
        if digest.status not in {STATUS_SELECTED, STATUS_ANALYTICS}:
            raise HTTPException(status_code=400, detail="Step 3 requires selected or analytics_ready status")

        self.db.query(Analytics).filter(Analytics.digest_id == digest.id).delete()
        self.db.query(QualityCheck).filter(QualityCheck.digest_id == digest.id).delete()

        logger.info("Шаг 3: аналитика | digest_id=%s", digest.id)
        selected = (
            self.db.query(SelectedNews)
            .filter(SelectedNews.digest_id == digest.id)
            .order_by(SelectedNews.output_position.asc())
            .all()
        )
        payload = []
        for item in selected:
            candidate = self.db.query(NewsCandidate).filter(NewsCandidate.id == item.candidate_id).first()
            if candidate:
                payload.append(
                    {
                        "candidate_id": candidate.id,
                        "title": candidate.title,
                        "source": candidate.source,
                        "url": candidate.url,
                        "published_at": candidate.published_at,
                    }
                )
        with self._digest_cost_session(
            digest,
            step="step_3",
            agent_name="AnalyticsAgent",
            request_label="step_3_analytics",
            model=AGENT_MODEL_RECOMMENDATIONS["AnalyticsAgent"],
        ):
            result = self.workflow.run_analytics(payload)
        result = complete_analytics_result(result, payload)
        if len(result.get("items", [])) != len(payload):
            logger.warning(
                "Шаг 3: после нормализации аналитики ожидалось %s блоков, получено %s | digest_id=%s",
                len(payload),
                len(result.get("items", [])),
                digest.id,
            )
        for item in result.get("items", []):
            candidate = self.db.query(NewsCandidate).filter(NewsCandidate.id == item["candidate_id"]).first()
            if not candidate:
                continue
            self.db.add(
                Analytics(
                    digest_id=digest.id,
                    candidate_id=candidate.id,
                    essence=str(item.get("essence", "")),
                    comment=str(item.get("comment", "")),
                    analysis=str(item.get("analysis", "")),
                    source_url=candidate.url,
                    source_name=candidate.source,
                    published_at=candidate.published_at,
                )
            )
        checks = result.get("self_check", [])
        if not isinstance(checks, list):
            checks = [checks]
        for c in checks:
            if isinstance(c, dict):
                check_name = str(c.get("check_name", "Self check"))
                status = str(c.get("status", "pass"))
                comment = str(c.get("comment", ""))
            elif isinstance(c, str):
                check_name = c[:200] or "Self check"
                status = "pass"
                comment = ""
            else:
                check_name = "Self check"
                status = "pass"
                comment = str(c)[:300]
            self.db.add(
                QualityCheck(
                    digest_id=digest.id,
                    check_name=check_name,
                    status=status,
                    comment=comment,
                )
            )

        raw_tags = result.get("hashtags", [])
        if isinstance(raw_tags, str):
            tags = [x for x in raw_tags.split() if x]
        elif isinstance(raw_tags, list):
            tags = [str(x).strip() for x in raw_tags if str(x).strip()]
        else:
            tags = []
        hashtag_asset = Asset(
            digest_id=digest.id,
            type="hashtags",
            path="",
            prompt=" ".join(tags),
        )
        self.db.add(hashtag_asset)
        overall = str(result.get("overall_analysis") or "").strip()
        if overall:
            self.db.query(Asset).filter(Asset.digest_id == digest.id, Asset.type == "overall_analysis").delete()
            self.db.add(
                Asset(
                    digest_id=digest.id,
                    type="overall_analysis",
                    path="",
                    prompt=overall,
                )
            )

        digest.status = STATUS_ANALYTICS
        digest.current_step = STATUS_ANALYTICS
        self.db.commit()
        logger.info("Шаг 3: готово | digest_id=%s analytics_rows=%s", digest.id, len(result.get("items", [])))
        return result

    def _step4_allow_status(self, digest: Digest) -> None:
        if digest.status not in {STATUS_ANALYTICS, STATUS_FINAL}:
            raise HTTPException(status_code=400, detail="Step 4 requires analytics_ready")

    def _step4_resolve_hook(self, digest: Digest, hook_variant: str | None) -> str:
        rotation = ["A", "B", "V"]
        return hook_variant if hook_variant in rotation else rotation[digest.id % 3]

    def _step4_selected_payload(self, digest: Digest) -> list[dict[str, Any]]:
        analytics_rows = self.db.query(Analytics).filter(Analytics.digest_id == digest.id).all()
        selected_rows = (
            self.db.query(SelectedNews)
            .filter(SelectedNews.digest_id == digest.id)
            .order_by(SelectedNews.output_position.asc())
            .all()
        )
        selected_payload: list[dict[str, Any]] = []
        for row in selected_rows:
            candidate = self.db.query(NewsCandidate).filter(NewsCandidate.id == row.candidate_id).first()
            analytics = next((a for a in analytics_rows if a.candidate_id == row.candidate_id), None)
            if not candidate or not analytics:
                continue
            essence = str(analytics.essence or "").strip()
            comment = str(analytics.comment or "").strip()
            analysis = str(analytics.analysis or "").strip()
            selected_payload.append(
                {
                    "title": candidate.title,
                    "url": candidate.url,
                    "source": candidate.source,
                    "essence": essence,
                    "comment": comment,
                    "summary_short": f"{essence} {comment}".strip(),
                    "summary": f"{essence} {analysis}".strip(),
                }
            )
        return selected_payload

    def _step4_overall_analysis(self, digest: Digest) -> str:
        asset = (
            self.db.query(Asset)
            .filter(Asset.digest_id == digest.id, Asset.type == "overall_analysis")
            .order_by(Asset.id.desc())
            .first()
        )
        if asset and asset.prompt:
            return asset.prompt.strip()
        return ""

    def _step4_hashtags(self, digest: Digest) -> list[str]:
        hashtags_asset = (
            self.db.query(Asset)
            .filter(Asset.digest_id == digest.id, Asset.type == "hashtags")
            .order_by(Asset.id.desc())
            .first()
        )
        if hashtags_asset and hashtags_asset.prompt:
            return hashtags_asset.prompt.split()
        return ["#ИИ", "#AI"]

    def _step4_validate_platforms(self, platforms: list[str]) -> list[str]:
        allowed = set(STEP4_PLATFORMS)
        normalized: list[str] = []
        for raw in platforms:
            key = str(raw).strip().lower()
            if key not in allowed:
                raise HTTPException(status_code=400, detail=f"Неизвестная площадка: {raw}")
            if key not in normalized:
                normalized.append(key)
        if not normalized:
            raise HTTPException(status_code=400, detail="Укажите хотя бы одну площадку")
        return normalized

    def _step4_clear_image_variants(self, digest_id: int) -> None:
        variant_types = [f"image_v{v}" for v in range(1, STEP4_IMAGE_VARIANT_COUNT + 1)]
        for asset in self.db.query(Asset).filter(Asset.digest_id == digest_id, Asset.type.in_(variant_types)).all():
            path = Path(asset.path)
            if path.exists():
                path.unlink()
        self.db.query(Asset).filter(Asset.digest_id == digest_id, Asset.type.in_(variant_types)).delete()
        digest = self.get_digest(digest_id)
        digest.step4_selected_image_variant = None
        self.db.commit()

    def run_step_4_generate_images(self, digest_id: int, hook_variant: str | None) -> dict[str, Any]:
        digest = self.get_digest(digest_id)
        self._step4_allow_status(digest)
        if not self.settings.enable_step4_image_generation:
            raise HTTPException(
                status_code=503,
                detail="Генерация обложек временно отключена (ENABLE_STEP4_IMAGE_GENERATION=false).",
            )

        hook = self._step4_resolve_hook(digest, hook_variant)
        selected_payload = self._step4_selected_payload(digest)
        if not selected_payload:
            raise HTTPException(status_code=400, detail="Нет выбранных новостей с аналитикой для обложки")

        logger.info("Шаг 4: генерация обложек | digest_id=%s hook=%s", digest.id, hook)
        self._step4_clear_image_variants(digest.id)

        with self._digest_cost_session(
            digest,
            step="step_4",
            agent_name="ImagePromptAgent",
            request_label="step_4_images",
            model=self.settings.proxyapi_image_model,
        ):
            image_prompt = self.workflow.run_image_prompt(hook, selected_payload)
            variants: list[dict[str, Any]] = []
            for variant in range(1, STEP4_IMAGE_VARIANT_COUNT + 1):
                variant_prompt = f"{image_prompt} Alternate visual composition variant {variant} of {STEP4_IMAGE_VARIANT_COUNT}."
                image_path = self.settings.image_dir / f"digest_{digest.id}_v{variant}.png"
                self.proxy.generate_image(variant_prompt, image_path)
                self.db.add(
                    Asset(
                        digest_id=digest.id,
                        type=f"image_v{variant}",
                        path=str(image_path),
                        prompt=variant_prompt,
                    )
                )
                variants.append({"variant": variant, "path": str(image_path)})
            self.db.commit()
        logger.info("Шаг 4: обложки готовы | digest_id=%s count=%s", digest.id, len(variants))
        return {"hook_variant": hook, "variants": variants}

    def run_step_4_select_image(self, digest_id: int, variant: int) -> dict[str, Any]:
        digest = self.get_digest(digest_id)
        self._step4_allow_status(digest)
        if not self.settings.enable_step4_image_generation:
            raise HTTPException(
                status_code=503,
                detail="Выбор обложки недоступен: генерация изображений отключена на сервере.",
            )
        if variant < 1 or variant > STEP4_IMAGE_VARIANT_COUNT:
            raise HTTPException(status_code=400, detail=f"Вариант обложки должен быть от 1 до {STEP4_IMAGE_VARIANT_COUNT}")

        src = self.settings.image_dir / f"digest_{digest_id}_v{variant}.png"
        if not src.exists():
            raise HTTPException(status_code=404, detail=f"Обложка варианта {variant} не найдена — сначала сгенерируйте варианты")

        dst = self.settings.image_dir / f"digest_{digest_id}.png"
        shutil.copy2(src, dst)
        digest.step4_selected_image_variant = variant

        variant_asset = (
            self.db.query(Asset)
            .filter(Asset.digest_id == digest.id, Asset.type == f"image_v{variant}")
            .order_by(Asset.id.desc())
            .first()
        )
        prompt = variant_asset.prompt if variant_asset else ""
        image_asset = (
            self.db.query(Asset).filter(Asset.digest_id == digest.id, Asset.type == "image").order_by(Asset.id.desc()).first()
        )
        if image_asset:
            image_asset.path = str(dst)
            image_asset.prompt = prompt
        else:
            self.db.add(Asset(digest_id=digest.id, type="image", path=str(dst), prompt=prompt))
        self.db.commit()
        logger.info("Шаг 4: выбрана обложка | digest_id=%s variant=%s", digest.id, variant)
        return {"selected_variant": variant, "image_path": str(dst)}

    def run_step_4_generate_texts(
        self,
        digest_id: int,
        platforms: list[str],
        hook_variant: str | None,
    ) -> dict[str, Any]:
        digest = self.get_digest(digest_id)
        self._step4_allow_status(digest)
        platform_keys = self._step4_validate_platforms(platforms)

        hook = self._step4_resolve_hook(digest, hook_variant)
        selected_payload = self._step4_selected_payload(digest)
        if not selected_payload:
            raise HTTPException(status_code=400, detail="Нет выбранных новостей с аналитикой для текстов")

        hashtags = self._step4_hashtags(digest)
        writer_payload = {
            "hook_variant": hook,
            "selected_news": selected_payload,
            "hashtags": hashtags,
            "date": format_digest_date_ru(digest.date),
            "overall_analysis": self._step4_overall_analysis(digest),
        }
        logger.info(
            "Шаг 4: тексты площадок | digest_id=%s hook=%s platforms=%s",
            digest.id,
            hook,
            ",".join(platform_keys),
        )

        with self._digest_cost_session(
            digest,
            step="step_4",
            agent_name="PlatformWriterAgent",
            request_label="step_4_texts",
            model=AGENT_MODEL_RECOMMENDATIONS["PlatformWriterAgent"],
        ):
            outputs = self.workflow.run_platform_writer(writer_payload, platforms=platform_keys)

            self.db.query(FinalOutput).filter(
                FinalOutput.digest_id == digest.id,
                FinalOutput.platform.in_(platform_keys),
            ).delete()
            for platform in platform_keys:
                content = outputs.get(platform, "")
                self.db.add(
                    FinalOutput(
                        digest_id=digest.id,
                        platform=platform,
                        content=content,
                        character_count=len(content),
                        qc_status="pending",
                    )
                )
            self.db.commit()

            checks = self.workflow.run_qc(outputs, has_ok=True)
            self.db.query(QualityCheck).filter(QualityCheck.digest_id == digest.id).delete()
            failed = False
            for c in checks:
                status = str(c.get("status", "pass")).lower()
                self.db.add(
                    QualityCheck(
                        digest_id=digest.id,
                        check_name=str(c.get("check_name", "QC")),
                        status=status,
                        comment=str(c.get("comment", "")),
                    )
                )
                if status not in {"pass", "ok", "success"}:
                    failed = True
            self.db.commit()

            if failed:
                repair_payload = {
                    "hook_variant": hook,
                    "selected_news": selected_payload,
                    "hashtags": hashtags,
                    "fix_mode": True,
                    "date": format_digest_date_ru(digest.date),
                    "overall_analysis": self._step4_overall_analysis(digest),
                }
                regenerated = self.workflow.run_platform_writer(repair_payload, platforms=platform_keys)
                for row in (
                    self.db.query(FinalOutput)
                    .filter(FinalOutput.digest_id == digest.id, FinalOutput.platform.in_(platform_keys))
                    .all()
                ):
                    row.content = regenerated.get(row.platform, row.content)
                    row.character_count = len(row.content)
                    row.qc_status = "repaired"
                self.db.commit()

        digest.status = STATUS_FINAL
        digest.current_step = STATUS_FINAL
        self.db.commit()

        docx_name = digest_docx_filename(digest.date, digest.id)
        docx_path = self.settings.docx_dir / docx_name
        build_docx(self.db, digest, docx_path)
        self.db.query(Asset).filter(Asset.digest_id == digest.id, Asset.type == "docx").delete()
        self.db.add(Asset(digest_id=digest.id, type="docx", path=str(docx_path), prompt="final export"))
        self.db.commit()
        logger.info("Шаг 4: тексты готовы | digest_id=%s docx=%s", digest.id, docx_path.name)
        return {"hook_variant": hook, "platforms": platform_keys, "docx_path": str(docx_path)}

    def run_step_4_final(self, digest_id: int, hook_variant: str | None) -> dict[str, Any]:
        """Совместимость: обложки → выбор варианта 1 → все площадки (обложки — если включены)."""
        digest = self.get_digest(digest_id)
        hook = self._step4_resolve_hook(digest, hook_variant)
        texts = self.run_step_4_generate_texts(digest_id, list(STEP4_PLATFORMS), hook_variant)
        out: dict[str, Any] = {
            "hook_variant": hook,
            "docx_path": texts["docx_path"],
            "platforms": texts["platforms"],
            "variants": [],
            "image_path": None,
        }
        if self.settings.enable_step4_image_generation:
            images = self.run_step_4_generate_images(digest_id, hook_variant)
            selected = self.run_step_4_select_image(digest_id, 1)
            out["hook_variant"] = images["hook_variant"]
            out["image_path"] = selected["image_path"]
            out["variants"] = images["variants"]
        return out

    def _check_url(self, url: str) -> bool:
        if not url.startswith("http"):
            return False
        resp = _http_get_html_for_article(url)
        return bool(resp is not None and getattr(resp, "status_code", 500) < 400 and getattr(resp, "text", ""))

    def _save_cost(
        self,
        digest_id: int,
        step: str,
        agent_name: str,
        model: str,
        request_label: str,
        cost_rub: float | None,
    ) -> None:
        self.db.add(
            LlmCostRecord(
                digest_id=digest_id,
                step=step,
                agent_name=agent_name,
                model=model,
                request_label=request_label,
                cost_rub=cost_rub,
            )
        )
        self.db.commit()

    def get_model_recommendations(self) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for agent_name, model_name in AGENT_MODEL_RECOMMENDATIONS.items():
            pricing = PRICING_RUB.get(model_name)
            if pricing is None:
                continue
            result.append(
                {
                    "agent_name": agent_name,
                    "recommended_model": model_name,
                    "input_rub_per_1m": pricing.input_rub_per_1m,
                    "output_rub_per_1m": pricing.output_rub_per_1m,
                    "rationale": pricing.rationale,
                }
            )
        return result

    def _normalize_manual_urls(self, manual_urls: list[str]) -> list[str]:
        cleaned = []
        seen = set()
        for url in manual_urls:
            value = url.strip()
            if not value:
                continue
            if value in seen:
                continue
            seen.add(value)
            cleaned.append(value)
        return cleaned[:10]

    def _ensure_russian_candidate_title(self, digest_id: int, url: str, headline: str) -> str:
        """Заголовок для карточки кандидата: на русском; иностранный — короткий перевод через LLM."""
        t = headline.strip()
        if not t:
            return t
        if _title_primarily_russian(t):
            return t[:500]
        model = AGENT_MODEL_RECOMMENDATIONS["ScoringAgent"]
        try:

            def do() -> str:
                return self.proxy.chat(
                    system_prompt=(
                        "Ты редактор дайджеста. Переведи заголовок новости на русский язык. "
                        "Сохрани смысл и имена собственные (GPT, OpenAI, Gemini и т.д.) без искажений. "
                        "Ответ — одна строка без кавычек, без префиксов вроде «Перевод:», без пояснений."
                    ),
                    user_prompt=f"URL страницы: {url}\nЗаголовок:\n{t[:900]}",
                    model=model,
                )

            translated = do()
            line = translated.strip().splitlines()[0].strip().strip("'\"«»„“")
            if len(line) >= 6:
                return line[:500]
            return t[:500]
        except Exception:
            logger.warning("Перевод заголовка пропущен (ошибка API) | url=%s", url[:100], exc_info=True)
            return t[:500]

    def _build_manual_candidates(self, digest: Digest, manual_urls: list[str], now_msk: str) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for idx, url in enumerate(manual_urls, start=1):
            bundle = _fetch_article_page_bundle(url)
            if bundle.get("ok") and (bundle.get("final_url") or bundle.get("display_url")):
                # В карточке храним URL после редиректов (final_url). canonical/og:url в разметке часто битые или устаревшие.
                stored = str(bundle.get("final_url") or bundle.get("display_url") or url).strip()
            else:
                stored = url.strip()
            raw_headline = bundle.get("headline") if bundle.get("ok") else None
            host = _host_from_url(stored)
            manual_reject: str | None = None
            if raw_headline:
                title = self._ensure_russian_candidate_title(digest.id, stored, raw_headline)
                if _editorial_headline_rejected(title) or _editorial_headline_rejected(str(raw_headline)):
                    manual_reject = "headline_low_quality"
                    raw_headline = None
                    title = f"Статья по ссылке ({host})"
                    desc = (
                        "Заголовок на странице выглядит как технический идентификатор (номер документа), "
                        "а не как заголовок новости — такая ссылка не принимается как подтверждённая. "
                        "Откройте материал в браузере и вставьте URL страницы с нормальным заголовком в разметке."
                    )
                    logger.warning("Ручной URL: отклонён технический заголовок | url=%s", stored[:120])
                elif not _ai_digest_topic_matches(str(bundle.get("topic_corpus") or ""), str(raw_headline)):
                    manual_reject = "off_topic_not_ai"
                    raw_headline = None
                    title = f"Статья по ссылке ({host})"
                    desc = (
                        "Страница не относится к теме искусственного интеллекта и нейросетей — "
                        "в дайджест такой материал не берётся. Укажите ссылку на статью про ИИ/ML."
                    )
                    logger.warning("Ручной URL: вне темы ИИ | url=%s", stored[:120])
                else:
                    desc = (
                        "Материал добавлен пользователем вручную и обязателен к использованию в выпуске. "
                        "Заголовок извлечён со страницы по ссылке; при необходимости показан перевод на русский."
                    )
                    logger.info(
                        "Ручной URL: заголовок извлечён | url=%s raw_len=%s title_len=%s",
                        stored[:80],
                        len(str(bundle.get("headline") or "")),
                        len(title),
                    )
            else:
                title = f"Статья по ссылке ({host})"
                desc = (
                    "Материал по ссылке пользователя: заголовок публикации не удалось извлечь автоматически "
                    "(часто это раздел сайта вроде /news, а не конкретная статья). "
                    "Вставьте прямую ссылку на страницу материала — тогда заголовок подтянется из разметки страницы."
                )
                logger.warning("Ручной URL: заголовок не извлечён | url=%s", stored[:120])
            # Для ручных URL используем тот же результат GET, что и для LLM-кандидатов (без отдельного HEAD).
            pub_fields: dict[str, Any] = {}
            _apply_bundle_published_at(pub_fields, bundle if bundle.get("ok") else {})
            published_at = str(pub_fields.get("published_at") or PUBLISHED_AT_UNDEFINED)
            if not manual_reject and _published_at_before_digest_window(digest, published_at, stored):
                manual_reject = "published_before_window"
                raw_headline = None
                title = f"Статья по ссылке ({host})"
                desc = (
                    f"Дата публикации материала раньше допустимого окна (с {digest_earliest_news_date(digest).isoformat()}). "
                    "Укажите более свежую статью."
                )
            link_ok = bool(bundle.get("ok"))
            headline_editorial_ok = bool(link_ok and raw_headline and not manual_reject)
            page_verified = headline_editorial_ok and link_ok
            comment = "MANUAL_REQUIRED: добавлено пользователем"
            if manual_reject:
                comment = f"{comment} {REJECT_REASON_PREFIX}{manual_reject}"
            if not page_verified:
                comment = f"{comment} {REJECT_REASON_PREFIX}manual_unverified"
            result.append(
                {
                    "original_number": idx,
                    "title": title,
                    "url": stored[:1000],
                    "source": host,
                    "tier": "Tier-2",
                    "published_at": published_at[:100],
                    "category": "manual",
                    "description": desc,
                    "significance_score": 3,
                    "novelty_score": 3,
                    "impact_score": 3,
                    "total_score": 9,
                    "reliability_status": "⚠️ сомнительный",
                    "is_foreign_agent": False,
                    "is_aggregator": False,
                    "is_duplicate": False,
                    "verification_comment": comment,
                    "link_status": link_ok,
                    "headline_editorial_ok": headline_editorial_ok,
                    "page_verified": page_verified,
                }
            )
        return result

    def _skeleton_dict_from_search_url(self, url: str, now_msk: str, seq: int) -> dict[str, Any]:
        host = _host_from_url(url)
        tier, is_aggregator, reliability_status = _classify_source_policy(url)
        comment = "Источник из веб-поиска; проверка страницы обязательна."
        if is_aggregator:
            comment += f" {REJECT_REASON_PREFIX}aggregator_source"
        return {
            "original_number": seq,
            "title": "",
            "url": url[:1000],
            "source": host,
            "tier": tier,
            "published_at": "",
            "category": "search",
            "description": "Кандидат из веб-поиска; заголовок подтянут со страницы после проверки.",
            "significance_score": 2,
            "novelty_score": 2,
            "impact_score": 2,
            "total_score": 6,
            "reliability_status": reliability_status,
            "is_foreign_agent": False,
            "is_aggregator": is_aggregator,
            "is_duplicate": False,
            "verification_comment": comment,
            "link_status": True,
            "headline_editorial_ok": False,
            "page_verified": False,
        }

    def _prefilter_llm_candidates_fetchable(
        self, digest_id: int, rows: list[dict[str, Any]]
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Отсекаем URL, которые LLM придумал: страница не открывается по HTTP (с разворотом лент)."""
        kept: list[dict[str, Any]] = []
        dropped: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            u = str(item.get("url") or "").strip()
            if not u.startswith("http"):
                _append_reject_reason(item, "invalid_url")
                dropped.append(item)
                continue
            if url_suspected_hallucinated(u):
                _append_reject_reason(item, "llm_hallucinated_url")
                item["link_status"] = False
                dropped.append(item)
                continue
            resolved_ok = False
            for resolved_url, bundle in _expand_listing_url_candidates(u, max_children=4):
                if not bundle.get("ok"):
                    continue
                item["url"] = resolved_url[:1000]
                resolved_ok = True
                break
            if not resolved_ok:
                _append_reject_reason(item, "http_unreachable")
                item["link_status"] = False
                dropped.append(item)
                continue
            kept.append(item)
        return kept, dropped

    def _step1_search_query(self, digest: Digest) -> str:
        earliest = digest_earliest_news_date(digest)
        days = int(digest.news_window_days or 3)
        kind = _normalize_news_window_day_kind(digest.news_window_day_kind)
        kind_ru = "рабочих" if kind == "working" else "календарных"
        window_hint = (
            f"Только материалы с датой публикации не ранее {earliest.isoformat()} "
            f"(окно: {days} {kind_ru} дней от даты выпуска {digest.date}). "
        )
        if (digest.digest_type or "serious") == "serious":
            return (
                window_hint
                + "искусственный интеллект нейросети машинное обучение новости свежие"
            )
        return (
            window_hint
            + "AI artificial intelligence neural networks machine learning news recent"
        )

    def _step1_run_web_supplement_rounds(
        self,
        digest: Digest,
        *,
        verified_pool: list[dict[str, Any]],
        seen_fp: set[str],
        excluded_urls: list[str],
        now_msk: str,
        snapshot_preview_row: Any,
        append_verified: Any,
        register_reject: Any,
        stop_at: int,
        max_rounds: int,
    ) -> None:
        """Добор через ProxyAPI/SerpAPI/Tavily (без CrewAI), пока не набран stop_at подтверждённых."""
        sup_round = 0
        while len(verified_pool) < stop_at and sup_round < max_rounds:
            sup_round += 1
            need = stop_at - len(verified_pool)
            if need <= 0:
                break
            spent_step1 = self._digest_proxyapi_spent_rub(digest)
            if spent_step1 >= self.settings.step1_max_cost_rub:
                logger.warning(
                    "Шаг 1: добор web_search остановлен — лимит step_1_max_cost_rub | digest_id=%s verified=%s",
                    digest.id,
                    len(verified_pool),
                )
                break
            extra = self._step1_fetch_supplementary_dicts(digest, seen_fp, excluded_urls, now_msk, need)
            if not extra:
                logger.warning(
                    "Шаг 1: добор web_search без новых URL | digest_id=%s round=%s",
                    digest.id,
                    sup_round,
                )
                continue
            for item in extra:
                raw_u = str(item.get("url") or "").strip()
                if not raw_u.startswith("http") or url_suspected_hallucinated(raw_u):
                    continue
                for resolved_url, bundle in _expand_listing_url_candidates(raw_u, max_children=6):
                    if _url_fingerprint(resolved_url) in seen_fp:
                        continue
                    work = dict(item)
                    work["url"] = resolved_url
                    work["title"] = ""
                    work["headline_editorial_ok"] = False
                    work["link_status"] = False
                    self._verify_llm_candidate_dict(digest, work, prefetched_bundle=bundle)
                    snapshot_preview_row(work)
                    if work.get("headline_editorial_ok") and work.get("link_status"):
                        append_verified(work)
                    else:
                        excluded_urls.append(resolved_url[:800])
                        register_reject(work)

    def _collect_search_verified_candidates(
        self,
        digest_id: int,
        digest: Digest,
        now_msk: str,
        seen_fp: set[str],
        limit: int,
        on_row: Any | None = None,
    ) -> list[dict[str, Any]]:
        """Реальные URL из ProxyAPI web_search / SerpAPI / Tavily — до LLM-цепочки."""
        query = self._step1_search_query(digest)
        urls = fetch_article_urls_from_search(
            self.settings,
            query,
            limit=max(limit * 3, 18),
            proxy=self.proxy,
        )
        return self._ingest_step1_urls_with_listing_expansion(
            digest, urls, now_msk, seen_fp, limit=limit, seq_start=1, on_row=on_row
        )

    def _ingest_step1_urls_with_listing_expansion(
        self,
        digest: Digest,
        urls: list[str],
        now_msk: str,
        seen_fp: set[str],
        *,
        limit: int,
        seq_start: int = 1,
        max_children_per_listing: int = 8,
        on_row: Any | None = None,
    ) -> list[dict[str, Any]]:
        """Проверяет URL; страницы-ленты разворачивает в отдельные статьи."""
        verified: list[dict[str, Any]] = []
        seq = seq_start
        visited: set[str] = set()
        for u in urls:
            if len(verified) >= limit:
                break
            for resolved_url, bundle in _expand_listing_url_candidates(u, max_children=max_children_per_listing):
                fp = _url_fingerprint(resolved_url)
                if not fp or fp in seen_fp or fp in visited:
                    continue
                visited.add(fp)
                item = self._skeleton_dict_from_search_url(resolved_url, now_msk, seq)
                self._verify_llm_candidate_dict(digest, item, prefetched_bundle=bundle)
                if on_row is not None:
                    on_row(item)
                if item.get("headline_editorial_ok") and item.get("link_status"):
                    seen_fp.add(fp)
                    verified.append(item)
                    seq += 1
                if len(verified) >= limit:
                    break
        return verified

    def _verify_llm_candidate_dict(
        self, digest: Digest, item: dict[str, Any], prefetched_bundle: dict[str, Any] | None = None
    ) -> None:
        """Нормализация URL и заголовка по HTML.

        headline_editorial_ok — читаемый редакционный заголовок (можно выбирать в топ-5).
        link_status — ссылка отвечает и проходит проверку доступности.
        page_verified — оба условия одновременно (совместимость с API/старыми клиентами).
        """
        if _manual_required_dict(item):
            return
        item["headline_editorial_ok"] = False
        item["page_verified"] = False
        item["link_status"] = False
        original_url = str(item.get("url") or "").strip()
        if _is_placeholder_candidate_dict(item):
            item["link_status"] = False
            _append_reject_reason(item, "placeholder_candidate")
            return
        u = original_url
        if not u.startswith("http"):
            item["link_status"] = False
            _append_reject_reason(item, "invalid_url")
            return
        if url_suspected_hallucinated(u):
            item["link_status"] = False
            _append_reject_reason(item, "llm_hallucinated_url")
            return
        if is_topic_pool_page_url(u) or is_listing_page_url(u):
            item["link_status"] = False
            _append_reject_reason(item, "news_listing_page")
            return
        tier, is_aggregator, reliability_status = _classify_source_policy(u)
        item["tier"] = tier
        item["is_aggregator"] = is_aggregator
        item["reliability_status"] = reliability_status
        if is_aggregator:
            item["link_status"] = False
            _append_reject_reason(item, "aggregator_source")
            return
        bundle = prefetched_bundle if prefetched_bundle is not None else _fetch_article_page_bundle(u)
        if not bundle.get("ok"):
            item["link_status"] = False
            _append_reject_reason(item, "http_unreachable")
            return
        stored = str(bundle.get("final_url") or bundle.get("display_url") or u).strip()
        _append_url_audit(item, "http_verify", original_url, stored)
        if _redirect_should_reject(original_url, stored, bundle):
            _append_reject_reason(item, "url_redirect_mismatch")
            item["link_status"] = False
            return
        item["url"] = stored[:1000]
        if is_topic_pool_page_url(stored) or is_listing_page_url(stored):
            item["link_status"] = False
            _append_reject_reason(item, "news_listing_page")
            return
        if bundle.get("is_listing_page"):
            item["link_status"] = False
            _append_reject_reason(item, "news_listing_page")
            return
        # Страница открылась по HTTP — ссылка рабочая (отдельный HEAD не делаем: даёт ложные 403/405).
        item["link_status"] = True
        if not _page_is_article_like(bundle):
            item["link_status"] = False
            _append_reject_reason(item, "no_article_markers")
            return
        h = bundle.get("headline")
        if not isinstance(h, str) or len(h.strip()) < 8:
            item["link_status"] = False
            _append_reject_reason(item, "non_article_page")
            return
        if not _ai_digest_topic_matches(str(bundle.get("topic_corpus") or ""), h):
            item["link_status"] = False
            _append_reject_reason(item, "off_topic_not_ai")
            return
        final_title = self._ensure_russian_candidate_title(digest.id, stored, h)[:500]
        if _editorial_headline_rejected(final_title) or _editorial_headline_rejected(h):
            item["link_status"] = False
            _append_reject_reason(item, "headline_low_quality")
            return
        _apply_bundle_published_at(item, bundle)
        if _published_at_before_digest_window(digest, str(item.get("published_at") or ""), stored):
            item["link_status"] = False
            _append_reject_reason(item, "published_before_window")
            return
        item["title"] = final_title
        item["headline_editorial_ok"] = True
        item["page_verified"] = True

    def _filter_score_url_mutations(
        self,
        verify_rows: list[dict[str, Any]],
        scored_rows: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Скоринг не может менять URL: баллы с scored_rows, идентичность — из verify."""
        score_only_fields = (
            "significance_score",
            "novelty_score",
            "impact_score",
            "total_score",
            "reliability_status",
            "tier",
            "description",
            "verification_comment",
            "is_aggregator",
            "is_duplicate",
            "is_foreign_agent",
        )
        verify_by_num: dict[int, dict[str, Any]] = {}
        verify_fp_set: set[str] = set()
        for row in verify_rows:
            base = dict(row)
            url = str(base.get("url") or "").strip()
            fp = _url_fingerprint(url)
            if fp:
                verify_fp_set.add(fp)
            try:
                num = int(base.get("original_number"))
            except Exception:
                continue
            if url.startswith("http") and num > 0:
                verify_by_num[num] = base

        accepted: list[dict[str, Any]] = []
        rejected: list[dict[str, Any]] = []
        for row in scored_rows:
            item = dict(row)
            score_url = str(item.get("url") or "").strip()
            try:
                number = int(item.get("original_number"))
            except Exception:
                number = -1
            base = verify_by_num.get(number) if number > 0 else None
            if base is not None:
                merged = dict(base)
                for key in score_only_fields:
                    if key in item and item[key] is not None:
                        merged[key] = item[key]
                verify_url = str(base.get("url") or "").strip()
                if score_url and verify_url and _url_fingerprint(score_url) != _url_fingerprint(verify_url):
                    _append_url_audit(merged, "score_url_ignored", verify_url, score_url)
                accepted.append(merged)
                continue
            score_fp = _url_fingerprint(score_url)
            if score_fp and score_fp in verify_fp_set:
                base_by_fp = next(
                    (v for v in verify_by_num.values() if _url_fingerprint(str(v.get("url") or "")) == score_fp),
                    None,
                )
                if base_by_fp is not None:
                    merged = dict(base_by_fp)
                    for key in score_only_fields:
                        if key in item and item[key] is not None:
                            merged[key] = item[key]
                    accepted.append(merged)
                continue
            _append_reject_reason(item, "url_mutated_between_agents")
            _append_url_audit(item, "score_guard", "<not_in_verify>", score_url or "<missing>")
            item["link_status"] = False
            rejected.append(item)
        return accepted, rejected

    def _step1_fetch_supplementary_dicts(
        self,
        digest: Digest,
        seen_fp: set[str],
        excluded_urls: list[str],
        now_msk: str,
        need_hint: int,
    ) -> list[dict[str, Any]]:
        query = self._step1_search_query(digest)
        urls = fetch_article_urls_from_search(
            self.settings,
            query,
            limit=max(need_hint * 3, 14),
            proxy=self.proxy,
        )
        out: list[dict[str, Any]] = []
        seq = 900
        for u in urls:
            fp = _url_fingerprint(u)
            if not fp or fp in seen_fp:
                continue
            out.append(self._skeleton_dict_from_search_url(u, now_msk, seq))
            seq += 1
            if len(out) >= 24:
                break
        if out:
            return out
        if not self.settings.enable_web_fetch:
            return []
        spent = self._digest_proxyapi_spent_rub(digest)
        if spent >= self.settings.step1_max_cost_rub:
            logger.warning(
                "Шаг 1: LLM-refill пропущен — достигнут STEP1_MAX_COST_RUB | digest_id=%s spent=%.4f",
                digest.id,
                spent,
            )
            return []
        refill_raw = self.workflow.run_candidates_refill(
            digest.digest_type or "serious",
            now_msk,
            excluded_urls[-55:],
        )
        return [x for x in refill_raw if isinstance(x, dict)]

    def _merge_candidates(
        self, manual_candidates: list[dict[str, Any]], generated_candidates: list[dict[str, Any]], limit: int
    ) -> list[dict[str, Any]]:
        merged: list[dict[str, Any]] = []
        seen_urls = set()
        for item in manual_candidates + generated_candidates:
            url = str(item.get("url", "")).strip()
            if not url.startswith("http"):
                continue
            key = _url_fingerprint(url)
            if not key or key in seen_urls:
                continue
            seen_urls.add(key)
            merged.append(item)
            if len(merged) >= limit:
                break
        for idx, item in enumerate(merged, start=1):
            item["original_number"] = idx
        return merged

    def _is_manual_required_candidate(self, verification_comment: str | None) -> bool:
        if not verification_comment:
            return False
        return "MANUAL_REQUIRED:" in verification_comment
