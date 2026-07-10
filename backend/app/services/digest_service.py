import json
import logging
from difflib import SequenceMatcher
from concurrent.futures import ThreadPoolExecutor, as_completed
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
from app.source_tiers_policy import (
    classify_source_policy as _classify_source_policy,
    get_source_tiers_policy,
    is_aggregator_source as _is_aggregator_source,
    is_blocked_search_host,
    is_foreign_agent_source as _is_foreign_agent_source,
    is_policy_tier_source,
    is_russian_host as _is_russian_host,
    is_tier5_forbidden_source as _is_tier5_forbidden_source,
)
from app.crew.model_policy import AGENT_MODEL_RECOMMENDATIONS, PRICING_RUB, STEP2_AI_ORDER_MODEL
from app.crew.workflow import CrewWorkflow, complete_analytics_result, current_msk_iso
from app.services.article_reader_fallback import (
    fetch_article_bundle_via_reader_proxy,
    is_bot_challenge_html as _is_bot_challenge_html_ext,
    looks_like_antibot_shell,
)
from app.services.reader_copy import build_platform_description, sanitize_reader_description
from app.services.platform_assembly import (
    assemble_platform_outputs,
    digest_docx_filename,
    extract_lead_from_legacy_platform_text,
    format_digest_date_ru,
    needs_html_layout_refresh,
)
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
from app.proxyapi_client import ProxyApiClient, set_proxyapi_log_context
from app.services.cost_attribution import (
    compute_digest_total_cost_rub,
    digest_release_spent_rub,
    step1_run_cost_rub,
)
from app.services.cost_tracker import ProxyApiCostTracker, record_today_balance, proxyapi_spent_today_rub
from app.services.export_service import build_docx
from app.services.news_search import (
    fetch_article_urls_from_search,
    fetch_article_urls_raw_merged,
    fetch_curious_prioritized_raw_urls,
    fetch_tier_prioritized_raw_urls,
    is_curated_list_url,
    is_editorial_listing_title,
    is_listing_page_url,
    is_search_noise_url,
    is_step1_listing_seed_url,
    is_topic_pool_page_url,
    search_url_path_date_outside_window,
    search_url_prefilter_reason,
    publication_day_from_url_candidates,
    set_step1_strict_web_search_citations,
    url_path_publication_day,
    url_suspected_hallucinated,
)
from app.services.step1_candidate_policy import (
    has_substantive_news_event_signal as _has_substantive_news_event_signal,
    is_press_release_candidate_dict as _is_press_release_candidate_dict,
    apply_material_form_to_candidate as _apply_material_form_to_candidate,
    classify_material_form as _classify_material_form,
    has_substantive_news_event_signal as _has_substantive_news_event_signal,
    is_product_tool_landing_url as _is_product_tool_landing_url,
    is_support_documentation_url as _is_support_documentation_url,
    is_research_pool_item as _is_research_pool_item,
    is_research_science_candidate as _is_research_science_candidate,
    is_substantive_press_for_pool as _is_substantive_press_for_pool,
    is_training_education_candidate as _is_training_education_candidate,
    is_theme_pool_item as _is_theme_pool_item,
    is_training_pool_item as _is_training_pool_item,
    looks_like_product_tool_promo as _looks_like_product_tool_promo,
    pool_item_theme as _pool_item_theme,
    should_reject_commercial_non_article as _should_reject_commercial_non_article,
    commercial_non_article_reject_reason as _commercial_non_article_reject_reason,
    manual_url_commercial_reject_reason as _manual_url_commercial_reject_reason,
    SERIOUS_POOL_THEME_QUOTAS,
    theme_pool_quota as _theme_pool_quota,
    MATERIAL_FORM_SERVICE,
    MATERIAL_FORM_TRAINING,
)
from app.services.step1_recent_top5 import (
    article_page_fingerprint,
    query_recent_top5_url_fingerprints,
)
from app.services.step1_filter_settings import (
    load_step1_filter_settings,
    normalize_step1_filter_states,
    save_step1_filter_settings,
)
from app.services.step1_conversion_stats import (
    apply_funnel_conversions_to_meta,
    estimate_raw_urls_for_target,
    median_e2e_for_digest_type,
    record_e2e_sample,
)
from app.services.step1_cancellation import (
    begin_run as step1_begin_run,
    end_run as step1_end_run,
    is_cancelled as step1_is_cancelled,
    request_cancel as step1_request_cancel,
)
from app.services.candidate_origin import apply_resolved_origin
from app.services.source_tiers_editor import expand_search_seed_urls
from app.services.telegram_channel_monitor import collect_telegram_seed_urls_for_digest
from app.services.step1_filters import (
    CURIOUS_PREFILTER_DEFAULT_ORDER,
    STEP1_FILTER_DEF_BY_ID,
    filter_def_applies_to_digest_type,
    record_step1_filter_reject,
    reset_step1_filter_stats,
    step1_enabled_map,
    step1_filter_catalog_payload,
)
from app.services.step1_filter_audit import (
    Step1CuriousToneAudit,
    reject_audit_top_reasons_limit,
    reject_samples_per_reason_limit,
)
from app.services.step1_curious_yield import (
    apply_curious_yield_limits,
    curious_yield_min_met,
)
from app.services.step1_search_routing import resolve_step1_search_routing
from app.services.step1_tiers_autoblock import record_host_unreachable, sync_autoblocked_hosts
from app.services.step1_url_registry import (
    classify_registry_item,
    detect_newly_disabled_filters,
    enabled_map_from_filter_states,
    load_registry_raw_urls,
    list_urls_for_reverify,
    purge_expired_registry,
    purge_registry_urls_outside_window,
    register_raw_urls,
    registry_bucket_counts,
    registry_row_to_skeleton,
    save_filter_snapshot,
)
from app.services.step1_web_search_stats import (
    current_step1_web_search_stats,
    reset_step1_web_search_stats,
    set_step1_web_search_api_cap,
    step1_web_search_api_cap_reached,
    step1_web_search_cost_budget_exceeded,
)
from app.services.step1_phase_timers import current_step1_phase_timers, reset_step1_phase_timers, step1_phase

logger = logging.getLogger("app.digest")
MSK_TZ = ZoneInfo("Europe/Moscow")

STATUS_DRAFT = "draft"
STATUS_STEP0 = "step_0"
STATUS_STEP1 = "step_1_candidates"
STATUS_SELECTED = "selected"
STATUS_ANALYTICS = "analytics_ready"
STATUS_FINAL = "final_ready"

SELECT_NEWS_ALLOWED = {STATUS_STEP1, STATUS_SELECTED, STATUS_ANALYTICS, STATUS_FINAL}
ORDER_STEP2_ALLOWED = {STATUS_SELECTED, STATUS_ANALYTICS, STATUS_FINAL}
STEP2_ORDER_RATIONALE_ASSET = "step2_order_rationale"


def _catalog_step1_filter_enabled(filter_id: str) -> bool:
    """Дефолты каталога фильтров, когда нет контекста выпуска (smoke-тесты, утилиты)."""
    fdef = STEP1_FILTER_DEF_BY_ID.get(filter_id)
    return bool(fdef.default_enabled) if fdef else False

STEP4_PLATFORMS = ("telegram", "max", "vk", "dzen")
STEP4_IMAGE_VARIANT_COUNT = 4

STEP1_TARGET_VERIFIED = 20
STEP1_MIN_VERIFIED = 10
STEP1_SUPPLEMENT_MAX_ROUNDS = 5
STEP1_PRE_CREW_SUPPLEMENT_ROUNDS = 3

from app.curious_source_policy import (
    classify_curious_source,
    curious_tier_priority,
    get_curious_source_policy,
    is_curious_aggregator_source,
    is_curious_allowed_source,
    is_curious_blocked_host,
    is_curious_gray_zone_source,
    is_curious_policy_source,
)
from app.services.curious_tone import (
    curious_raw_url_rank_key,
    curious_tone_score,
    curious_total_score_from_tone,
    has_curious_positive_signal,
    has_curious_serious_blockers,
    passes_curious_pool_gate,
    passes_curious_tone_gate,
)
from app.services.digest_type_policy import (
    DIGEST_TYPE_CURIOUS,
    curious_ru_share_bounds,
    is_curious_digest,
    normalize_digest_type,
    step1_curious_entertainment_anchor_en,
    step1_curious_entertainment_anchor_ru,
    step1_curious_foreign_topic_terms,
    step1_product_excludes_for_digest_type,
    step1_topic_terms_for_digest_type,
)

ASSET_PROXYAPI_BUDGET_ALERT = "step1_proxyapi_budget_exceeded"
PROXYAPI_ZERO_BALANCE_USER_MESSAGE = (
    "Нулевой баланс аккаунта ProxyAPI. "
    "Пополните счёт в личном кабинете proxyapi.ru — без этого веб-поиск и LLM недоступны."
)
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
STEP1_MAX_PER_SOURCE = 2
STEP1_MAX_PER_SOURCE_SHORT_POOL = 3
STEP2_MAX_PER_SOURCE = 2
"""Максимум статей с одного домена в пуле кандидатов (шаг 1)."""
STEP1_POOL_PER_HOST_CAP = 3
"""0 = без лимита URL с одного домена в сырой выдаче web_search."""
STEP1_SEARCH_MAX_URLS_PER_HOST = 0
STEP1_MIN_AUTO_DISCOVERED = 5
STEP1_RU_SHARE_MIN = 0.30
STEP1_RU_SHARE_MAX = 0.50
STEP1_PRESS_SHARE_MIN = 0.20
STEP1_PRESS_SHARE_MAX = 0.35
READER_FALLBACK_HOST_MARKERS = (
    "reuters.com",
    "ft.com",
    "wsj.com",
    "bloomberg.com",
)

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


_JS_DISABLED_HEADLINE_RE = re.compile(
    r"(?:javascript\s+(?:is\s+disabled|disabled|required|недоступн|отключен)"
    r"|функци\w*\s+javascript|enable\s+javascript|включите\s+javascript)",
    re.IGNORECASE,
)


def _is_social_embed_status_url(url: str) -> bool:
    """Посты X/Twitter/YouTube/TikTok: без JS/API заголовок статьи не извлекается."""
    try:
        p = urlparse(url.strip())
        host = (p.hostname or "").lower().removeprefix("www.")
        path = (p.path or "").lower()
    except Exception:
        return False
    if host in {"x.com", "twitter.com", "mobile.twitter.com"}:
        return "/status/" in path or "/i/web/" in path
    if host in {"youtube.com", "youtu.be", "m.youtube.com"}:
        return path.startswith("/watch") or "/shorts/" in path
    if host in {"tiktok.com", "vm.tiktok.com"}:
        return "/video/" in path or "/@" in path
    return False


def _headline_unusable_for_digest(title: str) -> bool:
    t = _strip_site_branding(_clean_headline_text(title))
    if _JS_DISABLED_HEADLINE_RE.search(t):
        return True
    return _editorial_headline_rejected(title)


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


def _headline_topic_tokens(title: str) -> set[str]:
    """Нормализованные токены заголовка для near-duplicate контроля."""
    cleaned = _strip_site_branding(_clean_headline_text(title)).lower()
    tokens = re.findall(r"[a-zа-яё0-9]{4,}", cleaned, flags=re.IGNORECASE)
    if not tokens:
        return set()
    stop = {
        "news",
        "today",
        "latest",
        "update",
        "after",
        "about",
        "with",
        "что",
        "этот",
        "этого",
        "после",
        "новости",
        "новость",
        "обзор",
        "рынок",
        "рынка",
    }
    normalized: set[str] = set()
    for token in tokens:
        if token in stop:
            continue
        # Грубая стемминг-нормализация: для русского/английского заголовка
        # достаточно первых символов, чтобы "предложил/предложит" считались одним сюжетом.
        normalized.add(token[:6] if len(token) > 6 else token)
    return normalized


def _titles_are_near_duplicates(left: str, right: str) -> bool:
    """Почти одинаковые заголовки одной темы (Jaccard по токенам)."""
    a = _headline_topic_tokens(left)
    b = _headline_topic_tokens(right)
    if not a or not b:
        return False
    common = len(a & b)
    base = max(len(a | b), 1)
    jaccard = common / base
    if common >= 2 and jaccard >= 0.45:
        return True
    if common >= 1:
        seq = SequenceMatcher(
            None,
            _strip_site_branding(_clean_headline_text(left)).lower(),
            _strip_site_branding(_clean_headline_text(right)).lower(),
        ).ratio()
        if seq >= 0.60:
            return True
    return False


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


def _curious_foreign_source_url(url: str) -> bool:
    host = _host_from_url(url).lower()
    policy = get_curious_source_policy()
    return any(marker in host for marker in policy.curious_foreign_hosts)


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


def _candidate_url_fingerprint_sets(urls: list[str]) -> tuple[set[str], set[str]]:
    """Наборы отпечатков URL финального пула кандидатов (шаг 2)."""
    url_fps: set[str] = set()
    page_fps: set[str] = set()
    for raw in urls:
        u = str(raw or "").strip()
        if not u.startswith("http"):
            continue
        fp = _url_fingerprint(u)
        apf = article_page_fingerprint(u)
        if fp:
            url_fps.add(fp)
        if apf:
            page_fps.add(apf)
    return url_fps, page_fps


def _discovered_url_in_final_pool(url: str, *, url_fps: set[str], page_fps: set[str]) -> bool:
    u = str(url or "").strip()
    if not u.startswith("http"):
        return False
    fp = _url_fingerprint(u)
    apf = article_page_fingerprint(u)
    return (fp and fp in url_fps) or (apf and apf in page_fps)


def _discovered_row_verification_passed(row: Any) -> bool:
    return bool(row.link_status) and bool(row.headline_editorial_ok) and bool(row.page_verified)


POOL_REBALANCE_REJECT_CODE = "excluded_from_final_pool"


def _align_discovered_journal_with_final_pool(
    rows_by_fp: dict[str, dict[str, Any]],
    *,
    final_candidate_urls: list[str],
) -> None:
    """Синхронизирует журнал с финальным списком NewsCandidate после rebalance."""
    url_fps, page_fps = _candidate_url_fingerprint_sets(final_candidate_urls)
    for snap in rows_by_fp.values():
        u = str(snap.get("url") or "")
        verified = bool(snap.get("headline_editorial_ok")) and bool(snap.get("link_status"))
        in_final = _discovered_url_in_final_pool(u, url_fps=url_fps, page_fps=page_fps)
        if verified and not in_final:
            _append_reject_reason(snap, POOL_REBALANCE_REJECT_CODE)


def _is_bot_challenge_html(chunk: str) -> bool:
    """Антибот-заглушка (TASS, Cloudflare и др.) без разметки статьи."""
    return _is_bot_challenge_html_ext(chunk)


_ANTIBOT_GATE_TITLE_RE = re.compile(
    r"(?:just a moment|checking your browser|attention required|access denied|"
    r"please wait|ddos|captcha|подтвердите|проверка браузера|момент|подождите)",
    re.IGNORECASE,
)


def _headline_looks_like_antibot_gate(headline: str | None) -> bool:
    t = str(headline or "").strip()
    if len(t) < 8:
        return False
    return bool(_ANTIBOT_GATE_TITLE_RE.search(t))


def _reader_fallback_allowed(url: str) -> bool:
    from app.services.article_reader_fallback import reader_fallback_allowed

    return reader_fallback_allowed(url)


def _reader_extract_headline(markdown_text: str) -> str | None:
    from app.services.article_reader_fallback import extract_reader_headline

    return extract_reader_headline(markdown_text)


def _reader_topic_corpus(markdown_text: str) -> str:
    from app.services.article_reader_fallback import extract_reader_topic_corpus

    return extract_reader_topic_corpus(markdown_text)


def _fetch_article_bundle_via_reader_proxy(initial_url: str) -> dict[str, Any] | None:
    return fetch_article_bundle_via_reader_proxy(initial_url)


def _try_reader_article_bundle(initial_url: str) -> dict[str, Any] | None:
    return _fetch_article_bundle_via_reader_proxy(initial_url)


def _truncate_article_excerpt(text: str, max_len: int = 4000) -> str:
    s = " ".join(str(text or "").split())
    if len(s) <= max_len:
        return s
    return f"{s[: max_len - 1]}…"


def _candidate_article_excerpt(item: dict[str, Any]) -> str:
    raw = str(item.get("article_excerpt") or item.get("topic_corpus") or "").strip()
    return _truncate_article_excerpt(raw)


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
            # Cross-host redirect is suspicious only if the final page has no recognizable article content.
            # Many sites legitimately redirect (tracking, regional, CDN). If we have a headline, trust it.
            if len(headline) >= 8 or bool(bundle.get("article_markers")) or bool(bundle.get("soft_article_signals")):
                return False
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


STEP1_LISTING_EXPAND_CHILDREN = 8


def _listing_max_children(url: str) -> int:
    """С investing.com и других лент берём больше дочерних статей."""
    u = str(url or "").lower()
    if "investing.com" in u:
        return 16
    return STEP1_LISTING_EXPAND_CHILDREN


def _published_at_from_url_path_candidates(
    seed_url: str | None,
    page_url: str,
) -> datetime | None:
    """Дата из path любого из URL (исходный, финальный): YYYY/MM/DD, DD.MM.YYYY и т.п."""
    pub_day = publication_day_from_url_candidates(seed_url, page_url)
    if pub_day is None:
        return None
    return datetime(pub_day.year, pub_day.month, pub_day.day, tzinfo=MSK_TZ)


def _manual_seed_page_is_listing(stored: str, bundle: dict[str, Any]) -> bool:
    if bundle.get("is_listing_page"):
        return True
    if is_step1_listing_seed_url(stored):
        return True
    return False


def _manual_seed_page_is_article(stored: str, bundle: dict[str, Any]) -> bool:
    if not bundle.get("ok"):
        return False
    if _manual_seed_page_is_listing(stored, bundle):
        return False
    low = stored.lower()
    if "vc.ru/id" in low or re.search(r"vc\.ru/id\d+", low):
        return False
    if "rules-recommendations" in low or "/rules" in low:
        return False
    if is_editorial_listing_title(str(bundle.get("headline") or "")):
        return False
    if bundle.get("headline_strict") or bundle.get("article_markers"):
        return True
    return _page_is_article_like(bundle)


_LISTING_PATH_HINTS = re.compile(
    r"(?:^|/)(?:neiroseti|artificial_intelligence|neural|ai-news|"
    r"product_[\w-]+|"
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
    if is_search_noise_url(url):
        return False
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


_EDITORIAL_BLOG_ARTICLE_PATH_RE = re.compile(r"^/blogs/\d+/\d+$", re.IGNORECASE)


def _is_editorial_blog_article_page(page_url: str, bundle: dict[str, Any]) -> bool:
    """Одиночная публикация в блоге (sostav /blogs/id/post), не лента раздела."""
    if not bundle.get("headline_strict"):
        return False
    corpus = str(bundle.get("topic_corpus") or "")
    if len(corpus) < 200:
        return False
    try:
        path = (urlparse(page_url).path or "").rstrip("/")
    except Exception:
        return False
    return bool(_EDITORIAL_BLOG_ARTICLE_PATH_RE.match(path))


def _is_news_listing_page(page_url: str, chunk: str, bundle: dict[str, Any]) -> bool:
    """Страница-лента/рубрика со списком новостей, а не одна статья."""
    if is_curated_list_url(page_url):
        return False
    if _is_editorial_blog_article_page(page_url, bundle):
        return False
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


def _parse_tme_post_url(url: str) -> tuple[str | None, int | None]:
    m = re.search(r"(?:t\.me|telegram\.me)/s/([^/?#]+)(?:/(\d+))?", url, re.IGNORECASE)
    if not m:
        return None, None
    channel = str(m.group(1) or "").strip().lower()
    if not channel:
        return None, None
    post_id = int(m.group(2)) if m.group(2) else None
    return channel, post_id


def _expand_aggregator_seed_urls(initial_url: str, max_children: int = 10) -> list[tuple[str, dict[str, Any]]]:
    """
    URL агрегатора (t.me, news.google…): извлекаем первоисточники, сам агрегатор не возвращаем.
    """
    from app.curious_source_policy import is_curious_policy_source
    from app.services.telegram_channel_monitor import (
        filter_telegram_external_urls,
        parse_channel_posts_html,
    )

    if not (is_curious_aggregator_source(initial_url) or _is_aggregator_source(initial_url)):
        return []

    bundle = _fetch_article_page_bundle(initial_url)
    if bundle.get("ok"):
        stored = str(bundle.get("final_url") or bundle.get("display_url") or initial_url).strip()
        if stored and not is_curious_aggregator_source(stored) and not _is_aggregator_source(stored):
            return [(stored, bundle)]

    low = initial_url.lower()
    if "t.me" in low or "telegram.me" in low:
        resp = _http_get_html_for_article(initial_url)
        if not resp or not resp.text:
            return []
        channel, target_post = _parse_tme_post_url(initial_url)
        if not channel:
            return []
        posts = parse_channel_posts_html(resp.text[:600_000], channel)
        if target_post is not None:
            posts = [post for post in posts if post.post_id == target_post]
        outbound: list[str] = []
        for post in posts:
            outbound.extend(post.urls)
        child_urls = filter_telegram_external_urls(outbound, max_links=max_children)
        out: list[tuple[str, dict[str, Any]]] = []
        for child in child_urls[:max_children]:
            child_bundle = _fetch_article_page_bundle(child)
            if not child_bundle.get("ok") or child_bundle.get("is_listing_page"):
                continue
            stored = str(child_bundle.get("final_url") or child_bundle.get("display_url") or child).strip()
            if not stored or is_curious_aggregator_source(stored) or _is_aggregator_source(stored):
                continue
            out.append((stored, child_bundle))
        if out:
            logger.info(
                "Шаг 1: разбор агрегатора Telegram | seed=%s children=%s",
                initial_url[:100],
                len(out),
            )
        return out

    if not bundle.get("ok"):
        return []
    resp = _http_get_html_for_article(initial_url)
    chunk = resp.text[:400_000] if resp and resp.text else ""
    if not chunk:
        return []
    seen: set[str] = set()
    child_urls: list[str] = []
    for m in re.finditer(r"""href=["'](https?://[^"'#\s]+)["']""", chunk, re.IGNORECASE):
        href = html.unescape(m.group(1)).strip().rstrip(".,);]")
        if not href.startswith("http"):
            continue
        key = href.lower().rstrip("/")
        if key in seen:
            continue
        if is_curious_aggregator_source(href) or _is_aggregator_source(href):
            continue
        if is_step1_listing_seed_url(href) or is_search_noise_url(href):
            continue
        seen.add(key)
        child_urls.append(href)
        if len(child_urls) >= max_children:
            break
    out = []
    for child in child_urls:
        child_bundle = _fetch_article_page_bundle(child)
        if not child_bundle.get("ok") or child_bundle.get("is_listing_page"):
            continue
        stored = str(child_bundle.get("final_url") or child_bundle.get("display_url") or child).strip()
        if not stored or is_curious_aggregator_source(stored) or _is_aggregator_source(stored):
            continue
        out.append((stored, child_bundle))
    if out:
        logger.info(
            "Шаг 1: разбор агрегатора | seed=%s children=%s",
            initial_url[:100],
            len(out),
        )
    return out


def _expand_listing_url_candidates(initial_url: str, max_children: int = 10) -> list[tuple[str, dict[str, Any]]]:
    """
    Если URL — лента, возвращает пары (url, bundle) дочерних статей.
    Иначе одну пару для самой страницы.
    """
    if is_search_noise_url(initial_url):
        return []

    agg_pairs = _expand_aggregator_seed_urls(initial_url, max_children=max_children)
    if agg_pairs:
        return agg_pairs

    def _prioritize_children(children: list[str]) -> list[str]:
        today = datetime.now(MSK_TZ).date()

        def _rank(pair: tuple[int, str]) -> tuple[int, int, int]:
            idx, url = pair
            pub_day = _url_path_publication_day(url)
            if pub_day is None:
                return (1, 0, idx)
            age = (today - pub_day).days
            freshness_bucket = 0 if age <= 21 else 1 if age <= 120 else 2
            return (freshness_bucket, -pub_day.toordinal(), idx)

        enumerated = list(enumerate(children))
        enumerated.sort(key=_rank)
        return [u for _, u in enumerated]
    if is_step1_listing_seed_url(initial_url):
        bundle = _fetch_article_page_bundle(initial_url)
        if not bundle.get("ok"):
            return []
        children = list(bundle.get("listing_article_urls") or [])
        if not children:
            chunk_resp = _http_get_html_for_article(initial_url)
            if chunk_resp and chunk_resp.text:
                children = _extract_listing_article_urls(chunk_resp.text[:400_000], initial_url, limit=max(12, max_children * 2))
        ranked_children = _prioritize_children(children)
        out: list[tuple[str, dict[str, Any]]] = []
        for child in ranked_children[:max_children]:
            child_bundle = _fetch_article_page_bundle(child)
            if not child_bundle.get("ok") or child_bundle.get("is_listing_page"):
                continue
            if is_listing_page_url(str(child_bundle.get("final_url") or child)):
                continue
            stored = str(child_bundle.get("final_url") or child_bundle.get("display_url") or child).strip()
            out.append((stored, child_bundle))
        if out:
            dated_recent = sum(
                1
                for u in ranked_children[:max_children]
                if (_url_path_publication_day(u) is not None)
                and ((datetime.now(MSK_TZ).date() - _url_path_publication_day(u)).days <= 21)
            )
            logger.info(
                "Шаг 1: разбор ленты | listing=%s extracted=%s ranked=%s recent_url_dates=%s",
                initial_url[:100],
                len(out),
                min(len(ranked_children), max_children),
                dated_recent,
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
        ranked_children = _prioritize_children(list(bundle.get("listing_article_urls") or []))
        out: list[tuple[str, dict[str, Any]]] = []
        for child in ranked_children[:max_children]:
            child_bundle = _fetch_article_page_bundle(child)
            if not child_bundle.get("ok") or child_bundle.get("is_listing_page"):
                continue
            stored = str(child_bundle.get("final_url") or child_bundle.get("display_url") or child).strip()
            out.append((stored, child_bundle))
        if out:
            dated_recent = sum(
                1
                for u in ranked_children[:max_children]
                if (_url_path_publication_day(u) is not None)
                and ((datetime.now(MSK_TZ).date() - _url_path_publication_day(u)).days <= 21)
            )
            logger.info(
                "Шаг 1: разбор ленты | listing=%s extracted=%s ranked=%s recent_url_dates=%s",
                initial_url[:100],
                len(out),
                min(len(ranked_children), max_children),
                dated_recent,
            )
            return out
        if is_topic_pool_page_url(initial_url) or is_listing_page_url(initial_url) or is_step1_listing_seed_url(initial_url):
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
_RU_DATE_NO_YEAR_RE = re.compile(
    r"(?<!\d)(\d{1,2})\s+("
    + "|".join(_RU_MONTHS)
    + r")(?!\s+\d{4}\b)(?:,?\s*(\d{1,2}):(\d{2}))?",
    re.IGNORECASE,
)
_DOT_DATE_NO_YEAR_RE = re.compile(
    r"\b(\d{1,2})\.(\d{1,2})(?!\.\d{2,4})(?:\s+(\d{1,2}):(\d{2})(?::(\d{2}))?)?\b"
)


def _parse_published_at_raw(raw: str, *, reference_year: int | None = None) -> datetime | None:
    """Разбор даты публикации из meta / JSON-LD / time."""
    s = (raw or "").strip()
    if not s or len(s) > 120:
        return None
    ref_year = int(reference_year or datetime.now(MSK_TZ).year)
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
    m_ru_no_year = _RU_DATE_NO_YEAR_RE.search(s)
    if m_ru_no_year:
        month = _RU_MONTHS.get(m_ru_no_year.group(2).lower())
        if month:
            try:
                hour = int(m_ru_no_year.group(3)) if m_ru_no_year.group(3) else 0
                minute = int(m_ru_no_year.group(4)) if m_ru_no_year.group(4) else 0
                return datetime(
                    ref_year,
                    month,
                    int(m_ru_no_year.group(1)),
                    hour,
                    minute,
                    tzinfo=MSK_TZ,
                )
            except ValueError:
                return None
    dot_no_year = _DOT_DATE_NO_YEAR_RE.search(s)
    if dot_no_year:
        try:
            sec = int(dot_no_year.group(5)) if dot_no_year.group(5) else 0
            return datetime(
                ref_year,
                int(dot_no_year.group(2)),
                int(dot_no_year.group(1)),
                int(dot_no_year.group(3) or 0),
                int(dot_no_year.group(4) or 0),
                sec,
                tzinfo=MSK_TZ,
            )
        except ValueError:
            pass
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


def digest_news_anchor_date(digest: Digest) -> date:
    """Верхняя граница окна: дата выпуска, но не раньше сегодня по МСК (старый выпуск не сужает окно)."""
    digest_d = digest.date if isinstance(digest.date, date) else digest.date
    today = datetime.now(MSK_TZ).date()
    return max(digest_d, today)


def digest_earliest_news_date(digest: Digest) -> date:
    anchor = digest_news_anchor_date(digest)
    days = max(1, int(getattr(digest, "news_window_days", None) or 3))
    kind = _normalize_news_window_day_kind(getattr(digest, "news_window_day_kind", None))
    if kind == "working":
        return _subtract_working_days_rf(anchor, days)
    return anchor - timedelta(days=days)


def _published_at_from_url_path(url: str) -> datetime | None:
    pub_day = url_path_publication_day(url)
    if pub_day is None:
        return None
    return datetime(pub_day.year, pub_day.month, pub_day.day, tzinfo=MSK_TZ)


def _url_path_publication_day(url: str) -> date | None:
    return url_path_publication_day(url)


def _url_path_date_before_digest_window(digest: Digest, url: str) -> bool:
    """True, если дата в path URL явно вне окна шага 0 (без HTTP)."""
    pub_day = _url_path_publication_day(url)
    if pub_day is None:
        return False
    if pub_day < digest_earliest_news_date(digest):
        return True
    return pub_day > digest_news_anchor_date(digest)


def _parse_published_at_storage_value(value: str | None) -> datetime | None:
    s = (value or "").strip()
    if not s or s == PUBLISHED_AT_UNDEFINED:
        return None
    if s.startswith("1970-"):
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=MSK_TZ)
        return dt.astimezone(MSK_TZ)
    except ValueError:
        return _parse_published_at_raw(s)


def _published_at_window_reject_code(
    digest: Digest,
    published_at: str | None,
    page_url: str,
    *,
    seed_url: str | None = None,
) -> str | None:
    """
    Код отбраковки по дате или None, если по дате пропускаем.
    published_before_window — известная дата раньше окна;
    published_date_undefined — дату из URL/meta извлечь не удалось.
    """
    earliest = digest_earliest_news_date(digest)
    anchor = digest_news_anchor_date(digest)
    pub = (published_at or "").strip()
    url_dt = _published_at_from_url_path_candidates(seed_url, page_url)
    meta_dt = _parse_published_at_storage_value(published_at)
    has_url = url_dt is not None and _published_at_plausible(url_dt)
    has_meta = meta_dt is not None and _published_at_plausible(meta_dt)

    def _day_outside_window(day: date) -> bool:
        return day < earliest or day > anchor

    if has_url:
        url_day = url_dt.astimezone(MSK_TZ).date()
        if not _day_outside_window(url_day):
            return None
        if not has_meta or meta_dt.astimezone(MSK_TZ).date() <= url_day:
            return "published_before_window"
        meta_day = meta_dt.astimezone(MSK_TZ).date()
        if _day_outside_window(meta_day):
            return "published_before_window"
        return None

    if has_meta:
        if _day_outside_window(meta_dt.astimezone(MSK_TZ).date()):
            return "published_before_window"
        return None

    if not pub or pub == PUBLISHED_AT_UNDEFINED:
        return "published_date_undefined"
    return "published_date_undefined"


def _published_at_before_digest_window(digest: Digest, published_at: str | None, page_url: str) -> bool:
    return _published_at_window_reject_code(digest, published_at, page_url) == "published_before_window"


def _candidate_date_window_reject_code(digest: Digest, item: dict[str, Any]) -> str | None:
    return _published_at_window_reject_code(
        digest,
        str(item.get("published_at") or ""),
        str(item.get("url") or ""),
    )


def _filter_verified_pool_by_date_window(
    digest: Digest,
    pool: list[dict[str, Any]],
    *,
    is_filter_enabled: Any | None = None,
    keep_manual: bool = True,
) -> tuple[list[dict[str, Any]], int]:
    """Убирает из пула материалы вне окна шага 0 (после rescue/fallback date-фильтры могли ослабиться)."""
    is_enabled = is_filter_enabled or _catalog_step1_filter_enabled
    kept: list[dict[str, Any]] = []
    removed = 0
    for item in pool:
        if keep_manual and (_manual_required_dict(item) or bool(item.get("_user_pinned_pool"))):
            kept.append(item)
            continue
        code = _candidate_date_window_reject_code(digest, item)
        if code and is_enabled(code):
            removed += 1
            continue
        kept.append(item)
    return kept, removed


def digest_news_window_hint_ru(digest: Digest) -> str:
    """Краткое описание окна для сообщений UI/502."""
    earliest = digest_earliest_news_date(digest)
    days = max(1, int(getattr(digest, "news_window_days", None) or 3))
    kind = _normalize_news_window_day_kind(getattr(digest, "news_window_day_kind", None))
    kind_ru = "рабочих" if kind == "working" else "календарных"
    anchor = digest_news_anchor_date(digest)
    digest_d = digest.date if isinstance(digest.date, date) else digest.date
    anchor_note = ""
    if anchor > digest_d:
        anchor_note = f" (верхняя граница по сегодняшней дате МСК, выпуск {digest_d.isoformat()})"
    return (
        f"допустимы материалы с {earliest.isoformat()} по {anchor.isoformat()}{anchor_note} "
        f"({days} {kind_ru} дн. от верхней границы окна)"
    )


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

    for m in _DOT_DATE_NO_YEAR_RE.finditer(focus):
        add(4, m.group(0))

    for m in _RU_DATE_TEXT_RE.finditer(focus):
        add(4, m.group(0))

    for m in _RU_DATE_NO_YEAR_RE.finditer(focus):
        add(5, m.group(0))

    return found


def _extract_published_at_from_page(
    chunk: str, page_url: str = "", *, seed_url: str | None = None
) -> str | None:
    """Дата из path URL (любой порядок); при отсутствии — из разметки страницы."""
    path_dt = _published_at_from_url_path_candidates(seed_url, page_url)
    if path_dt and _published_at_plausible(path_dt):
        return _format_published_at_storage(path_dt)
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


def _apply_bundle_published_at(
    item: dict[str, Any], bundle: dict[str, Any], *, seed_url: str | None = None
) -> None:
    """Дата: сначала path исходного/финального URL, затем разметка страницы."""
    page_url = str(
        item.get("url") or bundle.get("display_url") or bundle.get("final_url") or ""
    ).strip()
    path_dt = _published_at_from_url_path_candidates(seed_url, page_url)
    if path_dt and _published_at_plausible(path_dt):
        item["published_at"] = _format_published_at_storage(path_dt)
        return
    pub = bundle.get("published_at")
    if isinstance(pub, str) and pub.strip():
        parsed = _parse_published_at_storage_value(pub.strip())
        if parsed and _published_at_plausible(parsed):
            item["published_at"] = _format_published_at_storage(parsed)
            return
    from_url = _published_at_from_url_path(page_url)
    if from_url and _published_at_plausible(from_url):
        item["published_at"] = _format_published_at_storage(from_url)
        return
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
    for attempt in range(2):
        if attempt:
            time.sleep(0.45 * attempt)
        last_server_err: requests.Response | None = None
        for agent in agents:
            try:
                r = requests.get(
                    url,
                    timeout=(4, 8),
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
            reader_bundle = _fetch_article_bundle_via_reader_proxy(initial_url)
            if reader_bundle is not None:
                return reader_bundle
            return empty
        empty["status_code"] = r.status_code
        if r.status_code >= 400 or not r.text:
            reader_bundle = _fetch_article_bundle_via_reader_proxy(initial_url)
            if reader_bundle is not None:
                return reader_bundle
            return empty
        r.encoding = r.apparent_encoding or getattr(r, "encoding", None) or "utf-8"
        final_url = str(r.url).split("#")[0]
        chunk = r.text[:400_000]
        rough_vis_early = _rough_visible_text_from_html(chunk, 6500)
        if _is_bot_challenge_html(chunk) or looks_like_antibot_shell(chunk, visible_text=rough_vis_early):
            reader_bundle = _fetch_article_bundle_via_reader_proxy(initial_url)
            if reader_bundle is not None:
                return reader_bundle
            return empty
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
        published_at = _extract_published_at_from_page(
            chunk, display or final_url, seed_url=initial_url
        )
        full_bundle = {
            **partial_bundle,
            "published_at": published_at,
            "is_listing_page": is_listing,
            "listing_article_urls": listing_urls,
        }
        if not _page_is_article_like(full_bundle) and (
            looks_like_antibot_shell(chunk, visible_text=rough_vis)
            or len(topic_corpus) < 120
            or _headline_looks_like_antibot_gate(headline)
        ):
            reader_bundle = _fetch_article_bundle_via_reader_proxy(initial_url)
            if reader_bundle is not None and _page_is_article_like(reader_bundle):
                return reader_bundle
        return full_bundle
    except Exception:
        logger.debug("Не удалось загрузить страницу для согласования URL/заголовка", exc_info=True)
        reader_bundle = _fetch_article_bundle_via_reader_proxy(initial_url)
        if reader_bundle is not None:
            return reader_bundle
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


def _publisher_host_key(item: dict[str, Any]) -> str:
    """Ключ издателя для квот: всегда домен URL, не текстовое поле source от LLM."""
    url = str(item.get("url") or "").strip()
    host = _host_from_url(url).lower()
    if host and host not in {"manual", "unknown"}:
        return host
    return str(item.get("source") or "").strip().lower() or "unknown"


def _candidate_host_key(item: dict[str, Any]) -> str:
    return _publisher_host_key(item)


def _normalize_candidate_source(item: dict[str, Any]) -> None:
    host = _publisher_host_key(item)
    if host and host != "unknown":
        item["source"] = host


def _apply_source_policy_from_url(
    item: dict[str, Any], url: str, *, curious_mode: bool = False
) -> None:
    if curious_mode:
        tier, is_aggregator, reliability_status = classify_curious_source(url)
    else:
        tier, is_aggregator, reliability_status = _classify_source_policy(url)
    item["tier"] = tier
    item["is_aggregator"] = is_aggregator
    item["reliability_status"] = reliability_status


def _pool_host_counts(pool: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in pool:
        key = _publisher_host_key(item)
        counts[key] = counts.get(key, 0) + 1
    return counts


def _pool_respects_source_cap(pool: list[dict[str, Any]], *, cap: int = STEP1_MAX_PER_SOURCE) -> bool:
    if not pool:
        return True
    if cap <= 0:
        return True
    return max(_pool_host_counts(pool).values()) <= cap


def _effective_host_cap(per_host_cap: int | None) -> int | None:
    """None — без лимита на домен (шаг 1)."""
    if per_host_cap is None:
        cap = STEP1_MAX_PER_SOURCE
    else:
        cap = int(per_host_cap)
    if cap <= 0:
        return None
    return max(1, cap)


def _host_cap_blocks(host_count: dict[str, int], host: str, host_cap: int | None) -> bool:
    if host_cap is None:
        return False
    return host_count.get(host, 0) >= host_cap


def _step1_search_query_exclude_saturated_hosts(base_query: str, saturated_hosts: set[str]) -> str:
    if not saturated_hosts:
        return base_query
    exclusions = " ".join(f"-site:{host}" for host in sorted(saturated_hosts) if host and host != "unknown")
    return f"{base_query} {exclusions}".strip()


def _rebalance_verified_pool(
    pool: list[dict[str, Any]],
    target: int,
    pinned_fps: set[str] | None = None,
    *,
    digest_type: str | None = None,
    per_host_cap: int | None = None,
) -> list[dict[str, Any]]:
    if not pool:
        return []
    host_cap = _effective_host_cap(per_host_cap)
    pinned_fps = pinned_fps or set()
    min_ru = max(0, int(round(target * STEP1_RU_SHARE_MIN + 0.499)))
    max_ru = max(min_ru, int(target * STEP1_RU_SHARE_MAX))
    if is_curious_digest(digest_type):
        min_press = 0
        max_press = 0
        ru_min, ru_max = curious_ru_share_bounds()
        min_ru = max(0, int(round(target * ru_min + 0.499)))
        max_ru = max(min_ru, int(target * ru_max))
        ru_in_pool = sum(
            1 for x in pool if _is_russian_host(_host_from_url(str(x.get("url") or "")))
        )
        if ru_in_pool < min_ru:
            min_ru = ru_in_pool
        if len(pool) >= STEP1_MIN_VERIFIED and ru_in_pool < int(target * ru_min):
            max_ru = min(target, max(max_ru, ru_in_pool + (target - ru_in_pool)))
    else:
        min_press = 0
        max_press = 0
        min_ru = 0
        max_ru = target

    serious_theme_rebalance = not is_curious_digest(digest_type)

    def _rank_key(x: dict[str, Any]) -> tuple[int, int, int]:
        total = int(x.get("total_score", 0))
        if is_curious_digest(digest_type):
            title = str(x.get("title") or "")
            excerpt = str(x.get("article_excerpt") or x.get("description") or "")
            tone = int(x.get("curious_tone_score", 0) or 0) or curious_tone_score(title, excerpt)
            total += tone * 3
            total += curious_tone_score(title, excerpt) * 2
            if bool(x.get("curious_tone_low")):
                total -= 16
            elif has_curious_serious_blockers(title, excerpt) and not has_curious_positive_signal(title, excerpt):
                total -= 12
            elif tone >= 4:
                total += 4
        if _looks_like_product_tool_promo(x):
            total -= 8
        if serious_theme_rebalance:
            title = str(x.get("title") or "")
            excerpt = str(x.get("article_excerpt") or x.get("description") or "")
            corpus = f"{title} {excerpt}"
            theme = _pool_item_theme(x) or _classify_material_form(x, extra=corpus)
            if theme in SERIOUS_POOL_THEME_QUOTAS:
                total += 8
            elif _is_substantive_press_for_pool(x, extra=corpus):
                total += 5
            elif _has_substantive_news_event_signal(x, extra=corpus):
                total += 3
        tier_raw = str(x.get("tier") or "Tier-9")
        if is_curious_digest(digest_type):
            tier_num = curious_tier_priority(tier_raw)
        else:
            try:
                tier_num = int(tier_raw.replace("Tier-", "")) if tier_raw.startswith("Tier-") else 9
            except ValueError:
                tier_num = 9
        ru_boost = (
            1
            if is_curious_digest(digest_type) and _is_russian_host(_host_from_url(str(x.get("url") or "")))
            else 0
        )
        return (total, ru_boost, -tier_num)

    ranked = sorted(pool, key=_rank_key, reverse=True)
    chosen: list[dict[str, Any]] = []
    host_count: dict[str, int] = {}
    ru_count = 0
    press_count = 0
    theme_counts: dict[str, int] = {theme: 0 for theme in SERIOUS_POOL_THEME_QUOTAS}
    selected_fps: set[str] = set()

    def _bump_theme_counts(item: dict[str, Any]) -> None:
        theme = _pool_item_theme(item)
        if theme:
            theme_counts[theme] = theme_counts.get(theme, 0) + 1

    def force_add_pinned(item: dict[str, Any]) -> bool:
        """Закреплённые при частичной пересборке — в пуле с учётом лимита на источник."""
        nonlocal ru_count, press_count
        fp = _url_fingerprint(str(item.get("url") or ""))
        if not fp or fp in selected_fps:
            return False
        host = _publisher_host_key(item)
        if not _manual_required_dict(item) and _host_cap_blocks(host_count, host, host_cap):
            return False
        chosen.append(item)
        selected_fps.add(fp)
        host_count[host] = host_count.get(host, 0) + 1
        if _is_russian_host(_host_from_url(str(item.get("url") or ""))):
            ru_count += 1
        if _is_substantive_press_for_pool(item):
            press_count += 1
        _bump_theme_counts(item)
        return True

    def add_item(item: dict[str, Any]) -> bool:
        nonlocal ru_count, press_count
        fp = _url_fingerprint(str(item.get("url") or ""))
        if not fp or fp in selected_fps:
            return False
        host = _publisher_host_key(item)
        if _host_cap_blocks(host_count, host, host_cap):
            return False
        is_ru = _is_russian_host(_host_from_url(str(item.get("url") or "")))
        is_press = _is_substantive_press_for_pool(item)
        if _looks_like_product_tool_promo(item):
            return False
        if is_curious_digest(digest_type):
            title = str(item.get("title") or "")
            excerpt = str(item.get("article_excerpt") or item.get("description") or "")
            if not passes_curious_pool_gate(title, excerpt):
                return False
        if is_ru and ru_count >= max_ru:
            return False
        if is_press and press_count >= max_press:
            return False
        if serious_theme_rebalance:
            theme = _pool_item_theme(item)
            if theme and theme_counts.get(theme, 0) >= _theme_pool_quota(theme):
                return False
        chosen.append(item)
        selected_fps.add(fp)
        host_count[host] = host_count.get(host, 0) + 1
        if is_ru:
            ru_count += 1
        if is_press:
            press_count += 1
        _bump_theme_counts(item)
        return True

    # Сначала обязательные ручные ссылки и закреплённые при частичной пересборке.
    for item in ranked:
        if _manual_required_dict(item):
            force_add_pinned(item)
    for item in ranked:
        fp = _url_fingerprint(str(item.get("url") or ""))
        if fp and fp in pinned_fps:
            force_add_pinned(item)

    for item in ranked:
        if len(chosen) >= target:
            break
        add_item(item)

    def fill_minimum(current: int, minimum: int, predicate: Any) -> None:
        if current >= minimum:
            return
        for item in ranked:
            if len(chosen) >= target and current >= minimum:
                break
            if not predicate(item):
                continue
            if add_item(item):
                current += 1

    if serious_theme_rebalance:
        for theme, quota in SERIOUS_POOL_THEME_QUOTAS.items():
            available = sum(1 for x in pool if _pool_item_theme(x) == theme)
            if available <= 0:
                continue
            fill_minimum(
                theme_counts.get(theme, 0),
                min(quota, available),
                lambda x, theme_key=theme: _is_theme_pool_item(x, theme_key),
            )
    else:
        fill_minimum(press_count, min_press, _is_substantive_press_for_pool)
        fill_minimum(ru_count, min_ru, lambda x: _is_russian_host(_host_from_url(str(x.get("url") or ""))))

    need_min = min(target, STEP1_MIN_VERIFIED)
    if len(chosen) < need_min and len(ranked) >= need_min:
        chosen = _rebalance_verified_pool_host_cap_only(
            pool,
            max(target, STEP1_MIN_VERIFIED),
            pinned_fps=pinned_fps,
            prechosen=chosen,
            digest_type=digest_type,
        )

    for item in chosen:
        _normalize_candidate_source(item)
    return chosen[:target]


def _rebalance_verified_pool_host_cap_only(
    pool: list[dict[str, Any]],
    target: int,
    *,
    pinned_fps: set[str] | None = None,
    prechosen: list[dict[str, Any]] | None = None,
    per_host_cap: int | None = None,
    digest_type: str | None = None,
) -> list[dict[str, Any]]:
    """Добор до target только по лимиту на источник, без квот RU/press (если квоты съели пул)."""
    apply_theme_caps = not is_curious_digest(digest_type)
    pinned_fps = pinned_fps or set()
    prechosen = prechosen or []
    host_cap = _effective_host_cap(per_host_cap)

    def _rank_key(x: dict[str, Any]) -> tuple[int, int]:
        total = int(x.get("total_score", 0))
        if _looks_like_product_tool_promo(x):
            total -= 8
        tier_raw = str(x.get("tier") or "Tier-9")
        if tier_raw.startswith("Curious-T"):
            tier_num = curious_tier_priority(tier_raw)
        else:
            try:
                tier_num = int(tier_raw.replace("Tier-", "")) if tier_raw.startswith("Tier-") else 9
            except ValueError:
                tier_num = 9
        return (total, -tier_num)

    ranked = sorted(pool, key=_rank_key, reverse=True)
    chosen: list[dict[str, Any]] = []
    host_count: dict[str, int] = {}
    theme_counts: dict[str, int] = (
        {theme: 0 for theme in SERIOUS_POOL_THEME_QUOTAS} if apply_theme_caps else {}
    )
    for row in prechosen:
        if not apply_theme_caps:
            continue
        theme = _pool_item_theme(row)
        if theme:
            theme_counts[theme] = theme_counts.get(theme, 0) + 1
    selected_fps: set[str] = set()
    for item in prechosen:
        fp = _url_fingerprint(str(item.get("url") or ""))
        if fp:
            selected_fps.add(fp)
        host = _publisher_host_key(item)
        host_count[host] = host_count.get(host, 0) + 1
        chosen.append(item)

    for item in ranked:
        if _manual_required_dict(item):
            fp = _url_fingerprint(str(item.get("url") or ""))
            if not fp or fp in selected_fps:
                continue
            host = _publisher_host_key(item)
            chosen.append(item)
            selected_fps.add(fp)
            host_count[host] = host_count.get(host, 0) + 1

    for item in ranked:
        fp = _url_fingerprint(str(item.get("url") or ""))
        if fp and fp in pinned_fps and fp not in selected_fps:
            host = _publisher_host_key(item)
            if not _manual_required_dict(item) and _host_cap_blocks(host_count, host, host_cap):
                continue
            chosen.append(item)
            selected_fps.add(fp)
            host_count[host] = host_count.get(host, 0) + 1

    def add_host_only(item: dict[str, Any]) -> bool:
        fp = _url_fingerprint(str(item.get("url") or ""))
        if not fp or fp in selected_fps:
            return False
        host = _publisher_host_key(item)
        if _host_cap_blocks(host_count, host, host_cap):
            return False
        theme = _pool_item_theme(item) if apply_theme_caps else ""
        if theme and theme_counts.get(theme, 0) >= _theme_pool_quota(theme):
            return False
        if _looks_like_product_tool_promo(item):
            return False
        if is_curious_digest(digest_type):
            title = str(item.get("title") or "")
            excerpt = str(item.get("article_excerpt") or item.get("description") or "")
            if not passes_curious_pool_gate(title, excerpt):
                return False
            if _is_substantive_press_for_pool(item):
                return False
        chosen.append(item)
        selected_fps.add(fp)
        host_count[host] = host_count.get(host, 0) + 1
        if theme:
            theme_counts[theme] = theme_counts.get(theme, 0) + 1
        return True

    for item in ranked:
        fp = _url_fingerprint(str(item.get("url") or ""))
        if fp and fp in pinned_fps:
            add_host_only(item)
            if len(chosen) >= target:
                return chosen[:target]

    for item in ranked:
        if len(chosen) >= target:
            break
        add_host_only(item)

    for item in chosen:
        _normalize_candidate_source(item)
    return chosen[:target]


class DigestService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.settings = get_settings()
        self.proxy = ProxyApiClient()
        self.cost_tracker = ProxyApiCostTracker()
        contract = self.settings.prompts_path.read_text(encoding="utf-8")
        tiers_prompt = get_source_tiers_policy(self.settings.source_tiers_path).prompt_for_llm()
        self.workflow = CrewWorkflow(
            contract_prompt=contract + "\n\n---\n" + tiers_prompt,
        )
        self._active_step1_filter_enabled: dict[str, bool] = {}
        self._active_step1_filter_order: dict[str, int] = {}
        self._active_step1_digest_type: str = "serious"
        self._active_recent_top5_fps: set[str] = set()
        self._active_unreachable_hosts: set[str] = set()
        self._active_unreachable_host_failures: dict[str, int] = {}

    def _step1_mark_host_unreachable(self, url: str) -> None:
        host = _host_from_url(url).lower()
        if not host or host in {"manual", "unknown"}:
            return
        failures = self._active_unreachable_host_failures.get(host, 0) + 1
        self._active_unreachable_host_failures[host] = failures
        if failures >= 2:
            self._active_unreachable_hosts.add(host)

    def _step1_maintain_url_registry(self, digest: Digest, filter_states: list[dict[str, Any]]) -> dict[str, Any]:
        """TTL-очистка, автоблок доменов, метаданные для UI."""
        meta: dict[str, Any] = {}
        try:
            purged = purge_expired_registry(self.db, self.settings)
            meta["registry_purged"] = purged
            earliest = digest_earliest_news_date(digest)
            anchor = digest_news_anchor_date(digest)
            stale = purge_registry_urls_outside_window(
                self.db,
                earliest=earliest,
                anchor=anchor,
            )
            if stale:
                meta["registry_purged_outside_window"] = stale
            blocked = sync_autoblocked_hosts(self.db, self.settings, digest_type=digest.digest_type)
            if blocked:
                meta["registry_hosts_autoblocked"] = blocked
            meta["registry_buckets"] = registry_bucket_counts(self.db, digest.digest_type)
            current_enabled = enabled_map_from_filter_states(filter_states)
            meta["registry_filters_recheck"] = detect_newly_disabled_filters(
                self.db, digest.digest_type, current_enabled
            )
        except Exception:
            logger.exception("Шаг 1: обслуживание реестра URL | digest_id=%s", digest.id)
        return meta

    def _step1_reverify_registry_candidates(
        self,
        digest: Digest,
        *,
        disabled_filter_ids: list[str],
        verified_pool: list[dict[str, Any]],
        seen_fp: set[str],
        excluded_urls: list[str],
        snapshot_preview_row: Any,
        append_verified: Any,
        register_reject: Any,
        limit: int = 40,
    ) -> int:
        if not disabled_filter_ids:
            return 0
        rows = list_urls_for_reverify(self.db, digest.digest_type, disabled_filter_ids, limit=limit)
        if not rows:
            return 0
        logger.info(
            "Шаг 1: повторная проверка из реестра URL | digest_id=%s filters=%s count=%s",
            digest.id,
            disabled_filter_ids,
            len(rows),
        )
        added = 0
        for idx, row in enumerate(rows, start=1):
            fp = _url_fingerprint(str(row.url or ""))
            if fp and fp in seen_fp:
                continue
            work = registry_row_to_skeleton(row, seq=9000 + idx)
            try:
                self._verify_llm_candidate_dict(
                    digest,
                    work,
                    filter_enabled=self._is_step1_filter_enabled,
                )
            except TypeError:
                self._verify_llm_candidate_dict(digest, work)
            snapshot_preview_row(work)
            try:
                classify_registry_item(
                    self.db,
                    self.settings,
                    work,
                    digest_type=digest.digest_type,
                    digest_id=digest.id,
                )
            except Exception:
                logger.debug("Реестр URL: classify после reverify", exc_info=True)
            if work.get("headline_editorial_ok") and work.get("link_status"):
                append_verified(work)
                added += 1
            else:
                register_reject(work)
                raw_u = str(work.get("url") or "").strip()
                if fp:
                    seen_fp.add(fp)
                if raw_u.startswith("http"):
                    excluded_urls.append(raw_u[:800])
        if added:
            self.db.commit()
        return added

    def _step1_registry_track_item(self, digest: Digest, item: dict[str, Any]) -> None:
        try:
            classify_registry_item(
                self.db,
                self.settings,
                item,
                digest_type=digest.digest_type,
                digest_id=digest.id,
            )
            codes = _reject_reason_codes(str(item.get("verification_comment") or ""))
            if "http_unreachable" in codes:
                record_host_unreachable(self.db, str(item.get("url") or ""))
        except Exception:
            logger.debug("Реестр URL: classify | digest_id=%s", digest.id, exc_info=True)

    def _step1_host_is_temporarily_unreachable(self, url: str) -> bool:
        host = _host_from_url(url).lower()
        return bool(host and host in self._active_unreachable_hosts)

    def _read_step1_filter_config(self, digest_type: str | None = None) -> dict[str, Any]:
        return load_step1_filter_settings(digest_type)

    def _read_step1_filter_states(self, digest_type: str | None = None) -> list[dict[str, Any]]:
        return list(self._read_step1_filter_config(digest_type).get("filters") or [])

    def _read_step1_filter_counters(self, digest_id: int) -> dict[str, int]:
        asset = (
            self.db.query(Asset)
            .filter(Asset.digest_id == digest_id, Asset.type == "step1_filter_counters")
            .order_by(Asset.id.desc())
            .first()
        )
        raw: dict[str, Any] = {}
        if asset and (asset.prompt or "").strip():
            try:
                loaded = json.loads(asset.prompt or "{}")
                if isinstance(loaded, dict):
                    raw = loaded
            except Exception:
                raw = {}
        out: dict[str, int] = {}
        for fid in STEP1_FILTER_DEF_BY_ID:
            out[fid] = int(raw.get(fid, 0) or 0)
        return out

    def _aggregate_filter_counters_from_discovered_news(self, digest_id: int) -> dict[str, int]:
        """Счётчики по кодам из журнала step1_discovered_news (по одному разу на URL и код)."""
        out: dict[str, int] = {fid: 0 for fid in STEP1_FILTER_DEF_BY_ID}
        rows = (
            self.db.query(Step1DiscoveredNews)
            .filter(Step1DiscoveredNews.digest_id == digest_id)
            .all()
        )
        for row in rows:
            codes: list[str] = []
            for part in str(row.reject_codes or "").split(","):
                code = part.strip()
                if code and code not in codes:
                    codes.append(code)
            if not codes:
                codes = _reject_reason_codes(str(row.verification_comment or ""))
            if not codes and not (
                bool(row.link_status) and bool(row.headline_editorial_ok) and bool(row.page_verified)
            ):
                codes = ["unknown_reject"]
            for code in codes:
                if code in out:
                    out[code] += 1
        return out

    def _journal_totals_from_discovered_news(self, digest_id: int) -> dict[str, int]:
        rows = self.db.query(Step1DiscoveredNews).filter(Step1DiscoveredNews.digest_id == digest_id).all()
        candidate_urls = [
            str(u)
            for (u,) in self.db.query(NewsCandidate.url).filter(NewsCandidate.digest_id == digest_id).all()
        ]
        url_fps, page_fps = _candidate_url_fingerprint_sets(candidate_urls)
        if url_fps or page_fps:
            in_pool = sum(
                1 for r in rows if _discovered_url_in_final_pool(str(r.url or ""), url_fps=url_fps, page_fps=page_fps)
            )
        else:
            in_pool = sum(1 for r in rows if _discovered_row_verification_passed(r))
        total = len(rows)
        return {"total": total, "in_pool": in_pool, "rejected": max(0, total - in_pool)}

    def _read_step1_collection_meta(self, digest_id: int) -> dict[str, Any]:
        asset = (
            self.db.query(Asset)
            .filter(Asset.digest_id == digest_id, Asset.type == "step1_collection_meta")
            .order_by(Asset.id.desc())
            .first()
        )
        if not asset or not (asset.prompt or "").strip():
            return {}
        try:
            raw = json.loads(asset.prompt or "{}")
            return raw if isinstance(raw, dict) else {}
        except Exception:
            return {}

    def _effective_step1_filter_counters(self, digest_id: int) -> dict[str, int]:
        """Счётчики по журналу последнего сбора (одна строка = один URL)."""
        journal = self._aggregate_filter_counters_from_discovered_news(digest_id)
        if sum(journal.values()) > 0 or self._journal_totals_from_discovered_news(digest_id)["total"] > 0:
            return journal
        return self._read_step1_filter_counters(digest_id)

    def _sync_filter_counters_from_journal(self, digest_id: int) -> None:
        counters = self._aggregate_filter_counters_from_discovered_news(digest_id)
        self._save_step1_filter_counters(digest_id, counters)

    def _save_step1_filter_counters(self, digest_id: int, counters: dict[str, int]) -> None:
        payload = {fid: int(counters.get(fid, 0) or 0) for fid in STEP1_FILTER_DEF_BY_ID}
        self.db.query(Asset).filter(Asset.digest_id == digest_id, Asset.type == "step1_filter_counters").delete()
        self.db.add(
            Asset(
                digest_id=digest_id,
                type="step1_filter_counters",
                path="",
                prompt=json.dumps(payload, ensure_ascii=False),
            )
        )
        self.db.commit()

    def _activate_step1_filter_states(
        self,
        states: list[dict[str, Any]],
        *,
        digest_type: str | None = None,
    ) -> None:
        dtype = normalize_digest_type(digest_type)
        self._active_step1_digest_type = dtype
        normalized = normalize_step1_filter_states(states, digest_type=dtype)
        self._active_step1_filter_enabled = step1_enabled_map(normalized)
        self._active_step1_filter_order = {str(x["id"]): int(x["order"]) for x in normalized}

    def _deactivate_step1_filter_states(self) -> None:
        self._active_step1_filter_enabled = {}
        self._active_step1_filter_order = {}
        self._active_step1_digest_type = "serious"
        self._active_recent_top5_fps = set()
        self._step1_curious_mode = False

    def _load_recent_top5_fingerprints(self, digest: Digest) -> set[str]:
        digest_d = digest.date if isinstance(digest.date, date) else digest.date
        return query_recent_top5_url_fingerprints(
            self.db,
            digest_id=int(digest.id),
            digest_date=digest_d,
        )

    def _recent_top5_repeat_reason(self, url: str) -> str | None:
        if not self._is_step1_filter_enabled("recent_top5_repeat"):
            return None
        fp = article_page_fingerprint(url)
        if fp and fp in self._active_recent_top5_fps:
            return "recent_top5_repeat"
        return None

    def _is_step1_filter_enabled(self, filter_id: str) -> bool:
        if not filter_def_applies_to_digest_type(filter_id, self._active_step1_digest_type):
            return False
        if filter_id in self._active_step1_filter_enabled:
            return bool(self._active_step1_filter_enabled.get(filter_id))
        fdef = STEP1_FILTER_DEF_BY_ID.get(filter_id)
        return bool(fdef.default_enabled) if fdef else False

    def _step1_stage_filter_order(self, stage: str, default_ids: list[str]) -> list[str]:
        ids = [fid for fid in default_ids if fid in STEP1_FILTER_DEF_BY_ID and STEP1_FILTER_DEF_BY_ID[fid].stage == stage]
        ids.sort(key=lambda fid: (int(self._active_step1_filter_order.get(fid, 9999)), fid))
        return ids

    def _step1_filters_payload_extras(self, digest_id: int) -> dict[str, Any]:
        meta = self._read_step1_collection_meta(digest_id)
        applied_raw = meta.get("filters_applied") if isinstance(meta.get("filters_applied"), list) else []
        applied = [
            {"id": str(x.get("id") or ""), "enabled": bool(x.get("enabled")), "order": int(x.get("order") or 0)}
            for x in applied_raw
            if isinstance(x, dict) and x.get("id")
        ]
        applied.sort(key=lambda x: x["order"])
        return {
            "journal_totals": self._journal_totals_from_discovered_news(digest_id),
            "filters_applied_last_run": applied,
        }

    def get_step1_filters_payload(self, digest_id: int) -> dict[str, Any]:
        digest = self.get_digest(digest_id)
        dtype = normalize_digest_type(digest.digest_type)
        config = self._read_step1_filter_config(dtype)
        counters = self._effective_step1_filter_counters(digest_id)
        return {
            "catalog": step1_filter_catalog_payload(dtype),
            "config": config,
            "counters": counters,
            **self._step1_filters_payload_extras(digest_id),
        }

    def save_step1_filters_payload(self, digest_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        digest = self.get_digest(digest_id)
        dtype = normalize_digest_type(digest.digest_type)
        config = save_step1_filter_settings(payload, digest_type=dtype)
        counters = self._effective_step1_filter_counters(digest_id)
        return {
            "catalog": step1_filter_catalog_payload(dtype),
            "config": config,
            "counters": counters,
            **self._step1_filters_payload_extras(digest_id),
        }

    def _digest_llm_cost_sum(self, digest_id: int) -> float:
        total = (
            self.db.query(func.coalesce(func.sum(LlmCostRecord.cost_rub), 0.0))
            .filter(LlmCostRecord.digest_id == digest_id)
            .scalar()
        )
        return float(total or 0.0)

    def _digest_step_llm_cost_sum(self, digest_id: int, step: str) -> float:
        total = (
            self.db.query(func.coalesce(func.sum(LlmCostRecord.cost_rub), 0.0))
            .filter(LlmCostRecord.digest_id == digest_id, LlmCostRecord.step == step)
            .scalar()
        )
        return float(total or 0.0)

    def _ensure_release_cost_opening(self, digest: Digest) -> None:
        """Один раз на выпуск: якорь баланса для накопительного учёта до кнопки «Зафиксировать»."""
        if digest.proxyapi_release_open_balance is not None or digest.proxyapi_release_open_budget_used is not None:
            return
        snap = self.cost_tracker.get_balance_snapshot()
        record_today_balance(self.db, snap)
        digest.proxyapi_release_open_balance = snap.balance
        digest.proxyapi_release_open_budget_used = snap.budget_used
        self.db.commit()

    def _snapshot_proxyapi_before(self, digest: Digest, *, reset: bool = False) -> None:
        self._ensure_release_cost_opening(digest)
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

    def _live_step1_balance_delta_rub(self, digest: Digest) -> float | None:
        if digest.proxyapi_balance_before is None:
            return None
        snap = self.cost_tracker.get_balance_snapshot()
        if snap.balance is None:
            return None
        delta = float(digest.proxyapi_balance_before) - float(snap.balance)
        if delta > 0.0001:
            return round(delta, 6)
        return None

    def _enrich_step1_proxyapi_cost_meta(self, digest: Digest, meta: dict[str, Any]) -> None:
        from app.services.step1_usage_breakdown import estimate_proxyapi_cost_from_meta

        meta["proxyapi_web_search_cost_est_rub"] = estimate_proxyapi_cost_from_meta(meta)
        self.db.refresh(digest)
        delta = self._last_step_balance_delta_rub(digest)
        if delta is None:
            delta = self._live_step1_balance_delta_rub(digest)
            if delta is not None and digest.proxyapi_balance_after is None:
                snap = self.cost_tracker.get_balance_snapshot()
                digest.proxyapi_balance_after = snap.balance
                digest.proxyapi_budget_used_after = snap.budget_used
                self.db.commit()
        if delta is not None and delta > 0:
            meta["proxyapi_balance_delta_rub"] = round(delta, 4)
        meta["proxyapi_cost_rub"] = round(
            max(float(meta.get("proxyapi_balance_delta_rub") or 0), float(meta.get("proxyapi_web_search_cost_est_rub") or 0)),
            4,
        )

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
        # Без live-опроса баланса: используем только снимки before/after шага.
        if digest.proxyapi_budget_used_before is not None and digest.proxyapi_budget_used_after is not None:
            return max(0.0, float(digest.proxyapi_budget_used_after) - float(digest.proxyapi_budget_used_before))
        if digest.proxyapi_balance_before is not None and digest.proxyapi_balance_after is not None:
            return max(0.0, float(digest.proxyapi_balance_before) - float(digest.proxyapi_balance_after))
        return 0.0

    def digest_proxyapi_cost_rub(self, digest: Digest) -> float | None:
        from app.services.cost_attribution import digest_proxyapi_spent_rub

        return digest_proxyapi_spent_rub(
            digest,
            live_balance=None,
            live_budget_used=None,
        )

    def digest_release_cost_rub(self, digest: Digest) -> float | None:
        return digest_release_spent_rub(
            digest,
            live_balance=None,
            live_budget_used=None,
        )

    def compute_digest_total_cost_rub(self, digest: Digest) -> float:
        release_spent = digest_release_spent_rub(
            digest,
            live_balance=None,
            live_budget_used=None,
        )
        return compute_digest_total_cost_rub(
            records_sum_rub=self._digest_llm_cost_sum(digest.id),
            session_spent_rub=self.digest_proxyapi_cost_rub(digest),
            release_spent_rub=release_spent,
            finalized_cost_rub=digest.proxyapi_finalized_cost_rub,
        )

    def finalize_digest_release(self, digest_id: int) -> dict[str, Any]:
        """Зафиксировать выпуск: стоимость ProxyAPI и топ-5 для фильтра recent_top5_repeat."""
        digest = self.get_digest(digest_id)
        if digest.status != STATUS_FINAL:
            raise HTTPException(
                status_code=400,
                detail="Зафиксировать выпуск можно после шага 4, когда статус «Готов к публикации».",
            )
        outputs = self.db.query(FinalOutput).filter(FinalOutput.digest_id == digest.id).count()
        if outputs < 1:
            raise HTTPException(
                status_code=400,
                detail="Сначала сгенерируйте тексты на шаге 4, затем нажмите «Зафиксировать».",
            )
        if digest.proxyapi_finalized_cost_rub is not None:
            return {
                "digest_id": digest.id,
                "finalized": True,
                "already_finalized": True,
                "release_cost_rub": round(float(digest.proxyapi_finalized_cost_rub), 4),
                "finalized_at": digest.proxyapi_finalized_at.isoformat() if digest.proxyapi_finalized_at else None,
            }
        self._ensure_release_cost_opening(digest)
        if digest.proxyapi_release_open_balance is None and digest.proxyapi_balance_session_start is not None:
            digest.proxyapi_release_open_balance = digest.proxyapi_balance_session_start
        snap = self.cost_tracker.get_balance_snapshot()
        record_today_balance(self.db, snap)
        cost = digest_release_spent_rub(
            digest,
            live_balance=snap.balance,
            live_budget_used=snap.budget_used,
        )
        cost_rub = round(float(cost or 0.0), 4)
        digest.proxyapi_finalized_cost_rub = cost_rub
        digest.proxyapi_finalized_at = datetime.utcnow()
        digest.proxyapi_balance_after = snap.balance
        digest.proxyapi_budget_used_after = snap.budget_used
        self.db.commit()
        self.db.refresh(digest)
        logger.info(
            "Выпуск зафиксирован: стоимость ProxyAPI | digest_id=%s cost_rub=%s",
            digest.id,
            cost_rub,
        )
        return {
            "digest_id": digest.id,
            "finalized": True,
            "already_finalized": False,
            "release_cost_rub": cost_rub,
            "finalized_at": digest.proxyapi_finalized_at.isoformat() if digest.proxyapi_finalized_at else None,
        }

    def _proxyapi_budget_exceeded(self) -> bool:
        return getattr(self.proxy, "last_error_kind", None) == "budget_exceeded"

    @staticmethod
    def _snapshot_proxyapi_funds_message(digest: Digest) -> str | None:
        """Статус средств по уже снятым before-снимкам (без live-опроса)."""
        if digest.proxyapi_balance_before is not None and float(digest.proxyapi_balance_before) <= 0.0:
            return PROXYAPI_ZERO_BALANCE_USER_MESSAGE
        return None

    def _proxyapi_funds_status(self) -> tuple[bool, str | None]:
        """True, если аккаунт/ключ ProxyAPI не может оплачивать запросы."""
        if self._proxyapi_budget_exceeded():
            return True, PROXYAPI_BUDGET_USER_MESSAGE
        # Live-опрос баланса отключён: статус определяем по API-ошибкам/алертам шага.
        return False, None

    def _proxyapi_funds_depleted(self, *, check_balance: bool = True) -> bool:
        if self._proxyapi_budget_exceeded():
            return True
        if not check_balance:
            return False
        depleted, _ = self._proxyapi_funds_status()
        return depleted

    def _proxyapi_funds_block_message(self) -> str:
        depleted, message = self._proxyapi_funds_status()
        if depleted and message:
            return message
        if self._proxyapi_budget_exceeded():
            return PROXYAPI_BUDGET_USER_MESSAGE
        return PROXYAPI_BUDGET_USER_MESSAGE

    def _proxyapi_budget_depleted_from_api(self) -> bool:
        return self._proxyapi_funds_depleted(check_balance=False)

    def _persist_proxyapi_budget_alert(self, digest_id: int, message: str | None = None) -> None:
        alert_text = (message or self._proxyapi_funds_block_message()).strip()
        self.db.query(Asset).filter(
            Asset.digest_id == digest_id,
            Asset.type == ASSET_PROXYAPI_BUDGET_ALERT,
        ).delete()
        self.db.add(
            Asset(
                digest_id=digest_id,
                type=ASSET_PROXYAPI_BUDGET_ALERT,
                path="",
                prompt=alert_text,
            )
        )
        self.db.commit()

    def _clear_proxyapi_budget_alert(self, digest_id: int) -> None:
        self.db.query(Asset).filter(
            Asset.digest_id == digest_id,
            Asset.type == ASSET_PROXYAPI_BUDGET_ALERT,
        ).delete()

    def _raise_proxyapi_budget_exceeded(self, digest_id: int, message: str | None = None) -> None:
        detail = (message or self._proxyapi_funds_block_message()).strip()
        logger.warning("Шаг 1: ProxyAPI недоступен (баланс/бюджет) | digest_id=%s", digest_id)
        self._persist_proxyapi_budget_alert(digest_id, detail)
        raise HTTPException(status_code=402, detail=detail)

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
            return self._proxyapi_funds_block_message()
        return None

    def build_budget_notices(self, digest: Digest) -> list[str]:
        """Человекочитаемые предупреждения о лимите расходов для UI."""
        notices: list[str] = []
        budget_msg = self.proxyapi_budget_alert_message(digest.id)
        if budget_msg:
            notices.insert(0, budget_msg)
        elif self._proxyapi_budget_depleted_from_api():
            notices.insert(0, self._proxyapi_funds_block_message())
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
        from app.digest_defaults import get_digest_defaults

        d0 = get_digest_defaults().step0
        digest = Digest(
            date=today,
            status=STATUS_DRAFT,
            current_step=STATUS_DRAFT,
            news_window_days=d0.news_window_days_default,
            news_window_day_kind=d0.news_window_day_kind_default,
        )
        self.db.add(digest)
        self.db.commit()
        self.db.refresh(digest)
        logger.info("Создан новый выпуск на сегодня | digest_id=%s date=%s", digest.id, today)
        return self.run_step_0(
            digest.id,
            d0.digest_type_default,
            news_window_days=d0.news_window_days_default,
            news_window_day_kind=d0.news_window_day_kind_default,
        )

    def list_digests(self) -> list[Digest]:
        return self.db.query(Digest).order_by(Digest.date.desc()).all()

    def build_digest_list_items(self) -> list[dict]:
        from app.services.digest_list import build_digest_list_payload

        digests = self.list_digests()
        return build_digest_list_payload(self.db, digests)

    def get_digest(self, digest_id: int) -> Digest:
        digest = self.db.query(Digest).filter(Digest.id == digest_id).first()
        if not digest:
            raise HTTPException(status_code=404, detail="Digest not found")
        self._repair_orphan_step1_status(digest)
        self._ensure_step0_type_for_draft(digest)
        self._dedupe_digest_candidate_rows(digest_id)
        self._purge_invalid_step1_pool_rows(digest_id)
        return digest

    def _purge_invalid_step1_pool_rows(self, digest_id: int) -> bool:
        """Удаляет из сохранённого пула ссылки, которые больше не проходят фильтры (справка и т.п.)."""
        rows = (
            self.db.query(NewsCandidate)
            .filter(NewsCandidate.digest_id == digest_id)
            .order_by(NewsCandidate.original_number.asc(), NewsCandidate.id.asc())
            .all()
        )
        if not rows:
            return False
        bad_ids: list[int] = []
        for row in rows:
            url = str(row.url or "").strip()
            if _is_support_documentation_url(url):
                bad_ids.append(int(row.id))
        if not bad_ids:
            return False
        self.db.query(NewsCandidate).filter(NewsCandidate.id.in_(bad_ids)).delete(synchronize_session=False)
        self._renumber_digest_candidate_rows(digest_id)
        logger.warning(
            "Пул кандидатов: удалены страницы справки/документации | digest_id=%s removed=%s",
            digest_id,
            len(bad_ids),
        )
        return True

    def refresh_stale_html_platform_outputs(self, digest: Digest) -> bool:
        """Пересобрать MAX/Дzen из данных выпуска, если в БД остался старый markdown."""
        rows = self.db.query(FinalOutput).filter(FinalOutput.digest_id == digest.id).all()
        stale_rows = [row for row in rows if needs_html_layout_refresh(row.platform, row.content or "")]
        if not stale_rows:
            return False
        selected_payload = self._step4_selected_payload(digest)
        if not selected_payload:
            return False

        by_platform = {row.platform: row.content or "" for row in rows}
        assembly_payload: dict[str, Any] = {
            "selected_news": selected_payload,
            "hashtags": self._step4_hashtags(digest),
            "date": format_digest_date_ru(digest.date),
            "overall_analysis": self._step4_overall_analysis(digest),
            "digest_type": digest.digest_type or "serious",
        }
        tg_lead = extract_lead_from_legacy_platform_text(by_platform.get("telegram", ""))
        if tg_lead:
            assembly_payload["telegram_lead"] = tg_lead
        max_lead = extract_lead_from_legacy_platform_text(by_platform.get("max", ""))
        if max_lead:
            assembly_payload["max_lead"] = max_lead
        dzen_intro = extract_lead_from_legacy_platform_text(by_platform.get("dzen", ""))
        if dzen_intro:
            assembly_payload["dzen_intro"] = dzen_intro

        platforms = sorted({row.platform for row in stale_rows})
        regenerated = assemble_platform_outputs(assembly_payload, platforms=platforms)
        changed = False
        for row in stale_rows:
            new_content = regenerated.get(row.platform, "")
            if not new_content or new_content == row.content:
                continue
            row.content = new_content
            row.character_count = len(new_content)
            row.qc_status = "layout_refreshed"
            changed = True

        if not changed:
            return False

        self.db.commit()
        docx_asset = (
            self.db.query(Asset)
            .filter(Asset.digest_id == digest.id, Asset.type == "docx")
            .order_by(Asset.id.desc())
            .first()
        )
        if docx_asset:
            docx_path = self.settings.docx_dir / digest_docx_filename(digest.date, digest.id)
            build_docx(self.db, digest, docx_path)
            docx_asset.path = str(docx_path)
            self.db.commit()
        logger.info(
            "Обновлена HTML-вёрстка площадок | digest_id=%s platforms=%s",
            digest.id,
            ",".join(platforms),
        )
        return True

    def _repair_orphan_step1_status(self, digest: Digest) -> None:
        """Пул кандидатов есть, а шаги 2–3 пусты — вернуть выбор топ-5."""
        if digest.status in {STATUS_DRAFT, STATUS_STEP0, STATUS_STEP1, STATUS_FINAL}:
            return
        has_candidates = (
            self.db.query(NewsCandidate.id).filter(NewsCandidate.digest_id == digest.id).limit(1).first()
            is not None
        )
        if not has_candidates:
            return
        has_selected = (
            self.db.query(SelectedNews.id).filter(SelectedNews.digest_id == digest.id).limit(1).first() is not None
        )
        has_analytics = (
            self.db.query(Analytics.id).filter(Analytics.digest_id == digest.id).limit(1).first() is not None
        )
        if has_selected or has_analytics:
            return
        logger.info(
            "Восстановление статуса step_1_candidates | digest_id=%s prev_status=%s",
            digest.id,
            digest.status,
        )
        digest.status = STATUS_STEP1
        digest.current_step = STATUS_STEP1
        self.db.commit()

    def _ensure_step0_type_for_draft(self, digest: Digest) -> None:
        """Старые выпуски без типа на шаге 0 — серьёзный из digest_defaults.json."""
        if digest.digest_type:
            return
        if digest.status not in {STATUS_DRAFT, STATUS_STEP0}:
            return
        from app.digest_defaults import get_digest_defaults

        d0 = get_digest_defaults().step0
        digest.digest_type = d0.digest_type_default
        digest.digest_type_via_default = False
        digest.status = STATUS_STEP0
        digest.current_step = STATUS_STEP0
        self._ensure_release_cost_opening(digest)
        self.db.commit()
        self.db.refresh(digest)
        logger.info(
            "Шаг 0: применён тип по умолчанию | digest_id=%s type=%s",
            digest.id,
            digest.digest_type,
        )

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
            from app.digest_defaults import get_digest_defaults

            digest_type = get_digest_defaults().step0.digest_type_default
        if digest_type not in {"serious", "curious"}:
            raise HTTPException(status_code=400, detail="digest_type must be serious or curious")
        digest.digest_type = digest_type
        digest.digest_type_via_default = via_default
        digest.news_window_days = max(1, min(90, int(news_window_days)))
        digest.news_window_day_kind = _normalize_news_window_day_kind(news_window_day_kind)
        digest.status = STATUS_STEP0
        digest.current_step = STATUS_STEP0
        self._ensure_release_cost_opening(digest)
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

    def update_news_window(
        self,
        digest_id: int,
        *,
        news_window_days: int,
        news_window_day_kind: str,
    ) -> Digest:
        """Обновить окно дат без смены типа дайджеста и без отката статуса выпуска."""
        digest = self.get_digest(digest_id)
        digest.news_window_days = max(1, min(90, int(news_window_days)))
        digest.news_window_day_kind = _normalize_news_window_day_kind(news_window_day_kind)
        self.db.commit()
        self.db.refresh(digest)
        logger.info(
            "Окно дат выпуска обновлено | digest_id=%s days=%s kind=%s earliest=%s anchor=%s",
            digest.id,
            digest.news_window_days,
            digest.news_window_day_kind,
            digest_earliest_news_date(digest).isoformat(),
            digest_news_anchor_date(digest).isoformat(),
        )
        return digest

    def _news_candidate_to_pool_dict(self, candidate: NewsCandidate) -> dict[str, Any]:
        item = {
            "original_number": candidate.original_number,
            "title": candidate.title,
            "url": candidate.url,
            "source": candidate.source,
            "tier": candidate.tier,
            "published_at": candidate.published_at,
            "category": candidate.category,
            "description": candidate.description,
            "significance_score": candidate.significance_score,
            "novelty_score": candidate.novelty_score,
            "impact_score": candidate.impact_score,
            "total_score": candidate.total_score,
            "reliability_status": candidate.reliability_status,
            "link_status": candidate.link_status,
            "headline_editorial_ok": candidate.headline_editorial_ok,
            "page_verified": candidate.page_verified,
            "is_foreign_agent": candidate.is_foreign_agent,
            "is_aggregator": candidate.is_aggregator,
            "is_duplicate": candidate.is_duplicate,
            "verification_comment": candidate.verification_comment or "",
        }
        apply_resolved_origin(item)
        return item

    def _load_keep_candidates_for_rebuild(
        self, digest_id: int, keep_candidate_ids: list[int]
    ) -> tuple[list[NewsCandidate], list[int]]:
        """Кандидаты, отмеченные в шаге 2 — остаются в пуле при пересборке."""
        uniq_ids = list(dict.fromkeys(int(x) for x in keep_candidate_ids if x is not None))[:STEP1_TARGET_VERIFIED]
        if not uniq_ids:
            return [], []
        rows = (
            self.db.query(NewsCandidate)
            .filter(NewsCandidate.digest_id == digest_id, NewsCandidate.id.in_(uniq_ids))
            .all()
        )
        by_id = {int(c.id): c for c in rows}
        missing = [cid for cid in uniq_ids if cid not in by_id]
        if missing:
            raise HTTPException(
                status_code=400,
                detail=f"Не найдены кандидаты для сохранения в пуле: {missing}",
            )
        ordered = [by_id[cid] for cid in uniq_ids]
        return ordered, uniq_ids

    def _load_dropped_pool_urls_for_partial_rebuild(
        self, digest_id: int, keep_candidate_ids: list[int]
    ) -> list[str]:
        """URL из текущего пула, не отмеченных галочкой — исключить из нового сбора."""
        keep_set = {int(x) for x in keep_candidate_ids if x is not None}
        rows = self.db.query(NewsCandidate).filter(NewsCandidate.digest_id == digest_id).all()
        out: list[str] = []
        seen_lower: set[str] = set()
        for row in rows:
            if int(row.id) in keep_set:
                continue
            raw_u = str(row.url or "").strip()
            if not raw_u.startswith("http"):
                continue
            key = raw_u.lower()
            if key in seen_lower:
                continue
            seen_lower.add(key)
            out.append(raw_u[:800])
        return out

    @staticmethod
    def _pin_kept_candidates_first(
        verified_pool: list[dict[str, Any]], keep_candidate_ids: list[int], kept_rows: list[NewsCandidate]
    ) -> list[dict[str, Any]]:
        if not keep_candidate_ids or not kept_rows:
            return verified_pool
        keep_fps: list[str] = []
        for row in kept_rows:
            fp = _url_fingerprint(str(row.url or ""))
            if fp:
                keep_fps.append(fp)
        keep_fp_set = set(keep_fps)
        by_fp = {_url_fingerprint(str(x.get("url") or "")): x for x in verified_pool if _url_fingerprint(str(x.get("url") or ""))}
        pinned: list[dict[str, Any]] = []
        for fp in keep_fps:
            item = by_fp.get(fp)
            if item:
                pinned.append(item)
        rest = [x for x in verified_pool if _url_fingerprint(str(x.get("url") or "")) not in keep_fp_set]
        return pinned + rest

    @staticmethod
    def _renumber_verified_pool_items(pool: list[dict[str, Any]]) -> None:
        for idx, item in enumerate(pool, start=1):
            item["original_number"] = idx

    @staticmethod
    def _dedupe_verified_pool_dicts(pool: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Один URL — одна карточка; при коллизии оставляем первое вхождение."""
        seen: set[str] = set()
        out: list[dict[str, Any]] = []
        for item in pool:
            raw = str(item.get("url") or "").strip()
            if not raw.startswith("http"):
                continue
            fp = _url_fingerprint(raw) or article_page_fingerprint(raw) or raw.lower()
            if fp in seen:
                continue
            seen.add(fp)
            out.append(item)
        return out

    def _dedupe_digest_candidate_rows(self, digest_id: int) -> bool:
        """Удаляет дубликаты URL в пуле (после сбоев пересборки). Возвращает True, если что-то удалили."""
        rows = (
            self.db.query(NewsCandidate)
            .filter(NewsCandidate.digest_id == digest_id)
            .order_by(NewsCandidate.original_number.asc(), NewsCandidate.id.asc())
            .all()
        )
        seen: set[str] = set()
        dup_ids: list[int] = []
        for row in rows:
            raw = str(row.url or "").strip()
            if not raw.startswith("http"):
                continue
            fp = _url_fingerprint(raw) or article_page_fingerprint(raw) or raw.lower()
            if fp in seen:
                dup_ids.append(int(row.id))
                continue
            seen.add(fp)
        if not dup_ids:
            return False
        self.db.query(NewsCandidate).filter(NewsCandidate.id.in_(dup_ids)).delete(synchronize_session=False)
        self._renumber_digest_candidate_rows(digest_id)
        logger.warning(
            "Пул кандидатов: удалены дубликаты URL | digest_id=%s removed=%s",
            digest_id,
            len(dup_ids),
        )
        return True

    def _renumber_digest_candidate_rows(self, digest_id: int) -> None:
        rows = (
            self.db.query(NewsCandidate)
            .filter(NewsCandidate.digest_id == digest_id)
            .order_by(NewsCandidate.original_number.asc(), NewsCandidate.id.asc())
            .all()
        )
        for idx, row in enumerate(rows, start=1):
            row.original_number = idx
        self.db.commit()

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
        target = min(
            len(items),
            max(STEP1_MIN_VERIFIED, int(getattr(self.settings, "step1_max_candidates_for_ui", 15) or 15)),
        )
        digest = self.get_digest(digest_id)
        items = (
            _rebalance_verified_pool(
                items,
                target,
                digest_type=digest.digest_type,
                per_host_cap=STEP1_POOL_PER_HOST_CAP,
            )
            if target and len(items) > target
            else items
        )
        self.db.query(NewsCandidate).filter(NewsCandidate.digest_id == digest_id).delete()
        seen_urls_lower: set[str] = set()
        for idx, item in enumerate(items, start=1):
            apply_resolved_origin(item)
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
                    article_excerpt=_candidate_article_excerpt(item),
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
        digest = self.get_digest(digest_id)
        digest.status = STATUS_STEP1
        digest.current_step = STATUS_STEP1
        self.db.commit()

    def _persist_step1_preview_candidates(
        self,
        digest_id: int,
        rows_by_fp: dict[str, dict[str, Any]],
        *,
        pinned_fps: set[str] | None = None,
    ) -> None:
        """При неуспешном шаге 1 сохраняем в UI только рабочие кандидаты (без «серых» ссылок)."""
        if not rows_by_fp:
            return
        ordered = sorted(rows_by_fp.values(), key=lambda x: int(x.get("original_number") or 9999))
        verified_preview = [
            x
            for x in ordered
            if x.get("headline_editorial_ok")
            and x.get("link_status")
            and (not self._is_step1_filter_enabled("aggregator_source") or not bool(x.get("is_aggregator")))
        ]
        digest = self.get_digest(digest_id)
        preview_target = min(
            max(STEP1_MIN_VERIFIED, int(getattr(self.settings, "step1_max_candidates_for_ui", 15) or 15)),
            len(verified_preview),
        )
        if pinned_fps:
            preview_target = max(preview_target, len(pinned_fps))
        if len(verified_preview) <= preview_target:
            capped_verified = _rebalance_verified_pool_host_cap_only(
                verified_preview,
                len(verified_preview),
                pinned_fps=pinned_fps,
                per_host_cap=STEP1_POOL_PER_HOST_CAP,
                digest_type=digest.digest_type,
            )
        else:
            capped_verified = _rebalance_verified_pool(
                verified_preview,
                preview_target,
                pinned_fps=pinned_fps,
                digest_type=digest.digest_type,
                per_host_cap=STEP1_POOL_PER_HOST_CAP,
            )
        capped_verified = self._dedupe_verified_pool_dicts(capped_verified)
        seen_lower: set[str] = set()
        entities: list[NewsCandidate] = []
        seq = 0
        for item in capped_verified:
            url_norm = str(item.get("url", "")).strip().lower()
            if not url_norm.startswith("http"):
                continue
            if self._is_step1_filter_enabled("forbidden_media_source") and is_blocked_search_host(url_norm):
                continue
            if url_norm in seen_lower:
                continue
            seen_lower.add(url_norm)
            apply_resolved_origin(item)
            _normalize_candidate_source(item)
            seq += 1
            title = str(item.get("title") or "").strip()
            if len(title) < 4:
                title = f"Страница: {_host_from_url(str(item.get('url', '')))}"
            entities.append(
                NewsCandidate(
                    digest_id=digest_id,
                    original_number=seq,
                    title=title[:500],
                    url=str(item.get("url", ""))[:1000],
                    source=str(item.get("source", "") or _host_from_url(str(item.get("url", ""))))[:255],
                    tier=str(item.get("tier", "Tier-3"))[:32],
                    published_at=str(item.get("published_at", ""))[:100] or PUBLISHED_AT_UNDEFINED,
                    category=str(item.get("category", "technology"))[:120],
                    description=str(item.get("description", "")),
                    article_excerpt=_candidate_article_excerpt(item),
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
            )
        if not entities:
            return
        self.db.query(NewsCandidate).filter(NewsCandidate.digest_id == digest_id).delete()
        for entity in entities:
            self.db.add(entity)
        digest = self.db.query(Digest).filter(Digest.id == digest_id).first()
        if digest and entities:
            digest.status = STATUS_STEP1
            digest.current_step = STATUS_STEP1
        self.db.commit()
        if digest and digest.status == STATUS_STEP1:
            logger.info(
                "Шаг 1: превью-пул сохранён, доступен выбор топ-5 | digest_id=%s count=%s",
                digest_id,
                len(entities),
            )

    def _finalize_step1_discovery_run_metrics(self, run: Step1DiscoveryRun, digest: Digest) -> None:
        from app.services.step1_usage_breakdown import estimate_proxyapi_cost_from_meta

        self.db.refresh(run)
        ended = run.pool_formed_at or datetime.utcnow()
        if run.duration_sec is None and run.started_at:
            run.duration_sec = max(0, int((ended - run.started_at).total_seconds()))
        step_delta = self._last_step_balance_delta_rub(digest)
        if step_delta is None:
            step_delta = self._live_step1_balance_delta_rub(digest)
        if step_delta is None:
            meta = self._read_step1_collection_meta(digest.id)
            est = estimate_proxyapi_cost_from_meta(meta)
            if est > 0:
                step_delta = est
        run.cost_rub = step1_run_cost_rub(
            self.db,
            digest.id,
            run,
            step_delta_rub=step_delta,
        )
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
        run.duration_sec = max(0, int((run.pool_formed_at - run.started_at).total_seconds()))
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

    def cancel_step1_run(self, digest_id: int) -> dict[str, str]:
        self.get_digest(digest_id)
        if not step1_request_cancel(digest_id):
            raise HTTPException(status_code=409, detail="Сбор кандидатов сейчас не выполняется.")
        return {
            "ok": True,
            "detail": "Запрос на остановку принят. Текущая проверка завершится, затем сохранится частичный результат.",
        }

    def get_step1_live_progress(self, digest_id: int) -> dict[str, Any]:
        from app.services.step1_cancellation import is_running as step1_is_running
        from app.services.step1_live_progress import live_payload_from_meta, snapshot_live_progress
        from app.services.step1_statistics import _read_asset_json

        self.get_digest(digest_id)
        snap = snapshot_live_progress(digest_id)
        if snap is not None:
            return snap
        if step1_is_running(digest_id):
            payload = live_payload_from_meta({})
            payload["running"] = True
            payload["phase"] = "Выполняется…"
            payload["phase_key"] = ""
            return payload
        meta = _read_asset_json(self.db, digest_id, "step1_collection_meta")
        if meta:
            return live_payload_from_meta(meta)
        return live_payload_from_meta({})

    def run_step_1(
        self,
        digest_id: int,
        manual_urls: list[str],
        *,
        rebuild: bool = False,
        keep_candidate_ids: list[int] | None = None,
        news_window_days: int | None = None,
        news_window_day_kind: str | None = None,
    ) -> list[NewsCandidate]:
        cur = self.get_digest(digest_id)
        self.update_news_window(
            digest_id,
            news_window_days=int(news_window_days if news_window_days is not None else cur.news_window_days),
            news_window_day_kind=_normalize_news_window_day_kind(
                news_window_day_kind if news_window_day_kind is not None else cur.news_window_day_kind
            ),
        )
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

        kept_rows: list[NewsCandidate] = []
        keep_ids_ordered: list[int] = []
        if rebuild:
            if keep_candidate_ids:
                kept_rows, keep_ids_ordered = self._load_keep_candidates_for_rebuild(digest.id, keep_candidate_ids)
            else:
                kept_rows = (
                    self.db.query(NewsCandidate)
                    .filter(NewsCandidate.digest_id == digest.id)
                    .order_by(NewsCandidate.original_number)
                    .all()
                )
                keep_ids_ordered = [int(r.id) for r in kept_rows]
        partial_rebuild = rebuild and bool(kept_rows)
        preview_pinned_fps: set[str] = set()
        explicit_keep_ids = list(keep_candidate_ids or [])
        if partial_rebuild and explicit_keep_ids:
            for row in kept_rows:
                fp = _url_fingerprint(str(row.url or ""))
                if fp:
                    preview_pinned_fps.add(fp)
        set_proxyapi_log_context(
            DigestId=digest.id,
            DigestType=digest.digest_type or "unknown",
            Step="step1",
            RunType="partial_rebuild" if partial_rebuild else ("full_rebuild" if rebuild else "initial"),
        )
        reset_step1_web_search_stats()
        reset_step1_phase_timers()
        tier_strict_search = bool(getattr(self.settings, "step1_tier_strict_search", True))
        # Строгость citations обновляется в _collect_search_verified_candidates по размеру пула.
        set_step1_strict_web_search_citations(False)
        ws_boot = current_step1_web_search_stats()
        if ws_boot is not None:
            ws_boot.max_cost_rub = self._step1_max_cost_rub()
            ws_boot.strict_api_cap = int(getattr(self.settings, "step1_max_web_search_api_calls", 0) or 0) > 0
        self._step1_web_search_abort_reason: str | None = None
        self._refresh_step1_web_search_api_cap(0)
        # #region agent log
        try:
            from app.services.agent_debug_log import agent_debug_log

            _ws = current_step1_web_search_stats()
            agent_debug_log(
                "H1",
                "digest_service.run_step_1:boot",
                "step1_settings_and_cap",
                {
                    "digest_id": digest.id,
                    "digest_type": digest.digest_type,
                    "max_web_search_api_calls": int(
                        getattr(self.settings, "step1_max_web_search_api_calls", 0) or 0
                    ),
                    "tier_max_batches": int(
                        getattr(self.settings, "step1_tier_max_web_search_batches", 1) or 1
                    ),
                    "max_cost_rub": float(getattr(self.settings, "step1_max_cost_rub", 0) or 0),
                    "effective_cap": self._effective_step1_web_search_api_cap(0),
                    "strict_api_cap": bool(getattr(_ws, "strict_api_cap", False) if _ws else False),
                    "enable_web_fetch": bool(self.settings.enable_web_fetch),
                },
            )
        except Exception:
            pass
        # #endregion
        if int(getattr(self.settings, "step1_max_web_search_api_calls", 0) or 0) > 0:
            ws_cap = int(getattr(self.settings, "step1_max_web_search_api_calls", 0) or 0)
            preview_mode = "on_if_empty" if ws_cap >= 2 else "off"
            logger.info(
                "Шаг 1: сбалансированный web_search | digest_id=%s api_cap=%s tier_batches=%s preview_fallback=%s",
                digest.id,
                ws_cap,
                int(getattr(self.settings, "step1_tier_max_web_search_batches", 1) or 1),
                preview_mode,
            )
        dropped_pool_urls: list[str] = []
        if rebuild:
            dropped_pool_urls = self._load_dropped_pool_urls_for_partial_rebuild(
                digest.id, keep_ids_ordered
            )

        user_manual_urls = self._normalize_manual_urls(manual_urls)
        if not user_manual_urls:
            carried_manual_urls = self._collect_previous_user_manual_urls(digest.id)
            if carried_manual_urls:
                user_manual_urls = carried_manual_urls
                logger.info(
                    "Шаг 1: подтянуты ручные URL из предыдущего пула | digest_id=%s count=%s",
                    digest.id,
                    len(carried_manual_urls),
                )
        telegram_seed_urls: list[str] = []
        normalized_manual_urls: list[str] = list(user_manual_urls)
        telegram_only_urls: list[str] = []
        if not self.settings.enable_web_fetch and not user_manual_urls:
            raise HTTPException(
                status_code=400,
                detail="Нет веб-доступа (web.enable_fetch=false в pipeline_settings.json). Вставьте вручную 5-10 ссылок в поле manual_urls.",
            )

        step1_filter_config = self._read_step1_filter_config(digest.digest_type)
        step1_filter_states = list(step1_filter_config.get("filters") or [])
        step1_filter_enabled = step1_enabled_map(step1_filter_states)
        min_discovered_pages = int(step1_filter_config["min_discovered_pages"])
        min_collection_iterations = max(1, int(step1_filter_config.get("min_collection_iterations") or 5))

        need_reset_step2_4 = (not partial_rebuild) or past_step1
        if need_reset_step2_4:
            self.db.query(SelectedNews).filter(SelectedNews.digest_id == digest.id).delete()
            self.db.query(Analytics).filter(Analytics.digest_id == digest.id).delete()
            self.db.query(FinalOutput).filter(FinalOutput.digest_id == digest.id).delete()
            self.db.query(QualityCheck).filter(QualityCheck.digest_id == digest.id).delete()
            self.db.query(Asset).filter(Asset.digest_id == digest.id).delete()
            if past_step1:
                digest.status = STATUS_STEP1
                digest.current_step = STATUS_STEP1
                self.db.flush()
        # Записи llm_cost_records не удаляем: иначе «Суммарно AI» занижается после пересборки.
        prev_verified_backup = self._backup_verified_candidate_dicts(digest.id)

        digest.step1_budget_capped = False
        digest.step2_budget_capped = False

        if partial_rebuild:
            logger.info(
                "Шаг 1: частичная пересборка пула | digest_id=%s keep=%s drop=%s manual_urls=%s",
                digest.id,
                keep_ids_ordered,
                len(dropped_pool_urls),
                len(normalized_manual_urls),
            )
        elif rebuild or past_step1:
            logger.info(
                "Шаг 1: %s | digest_id=%s prev_status=%s manual_urls=%s excluded_from_prev_pool=%s keep=%s",
                "дополнение пула" if partial_rebuild else "полная пересборка пула",
                digest.id,
                digest.status,
                len(normalized_manual_urls),
                len(dropped_pool_urls),
                len(kept_rows),
            )
            if not partial_rebuild:
                self.db.query(NewsCandidate).filter(NewsCandidate.digest_id == digest.id).delete()
                self.db.commit()
        else:
            logger.info(
                "Шаг 1: запуск сбора кандидатов | digest_id=%s seed_urls=%s (manual=%s telegram=%s)",
                digest.id,
                len(normalized_manual_urls),
                len(manual_urls),
                len(telegram_seed_urls),
            )
        discovered_by_fp: dict[str, dict[str, Any]] = {}
        discovery_run = self._start_step1_discovery_run(digest)
        step1_begin_run(digest.id)
        step1_user_cancelled = False
        step1_hard_cost_stopped = False
        step1_accept_short_pool = False
        step1_skip_final_preview = False
        step1_proxyapi_depleted = False
        try:
            self._activate_step1_filter_states(step1_filter_states, digest_type=digest.digest_type)
            reset_step1_filter_stats()
            self._active_recent_top5_fps = (
                self._load_recent_top5_fingerprints(digest)
                if self._is_step1_filter_enabled("recent_top5_repeat")
                else set()
            )
            self._active_unreachable_hosts = set()
            self._active_unreachable_host_failures = {}
            self._step1_curious_mode = is_curious_digest(digest.digest_type)
            if self._step1_curious_mode:
                self._step1_curious_tone_audit = Step1CuriousToneAudit(self.settings)
                self._step1_curious_tone_audit.begin_run(digest.id)
                logger.info(
                    "Шаг 1: режим курьёза (отдельные домены и фильтр тона) | digest_id=%s",
                    digest.id,
                )
            else:
                self._step1_curious_tone_audit = None
                logger.info(
                    "Шаг 1: режим серьёзного выпуска (source_tiers) | digest_id=%s tier_strict=%s",
                    digest.id,
                    bool(getattr(self.settings, "step1_tier_strict_search", True)),
                )
            logger.info(
                "Шаг 1: тип и фильтры | digest_id=%s digest_type=%s published_before_window=%s "
                "published_date_undefined=%s recent_top5_fps=%s",
                digest.id,
                normalize_digest_type(digest.digest_type),
                step1_filter_enabled.get("published_before_window"),
                step1_filter_enabled.get("published_date_undefined"),
                len(self._active_recent_top5_fps),
            )
            self._snapshot_proxyapi_before(digest, reset=(rebuild or past_step1))
            if self.settings.step1_telegram_monitor_enabled:
                try:
                    with step1_phase("telegram"):
                        telegram_seed_urls = collect_telegram_seed_urls_for_digest(
                            self.settings,
                            earliest_date=digest_earliest_news_date(digest),
                            proxy=self.proxy,
                        )
                except Exception:
                    logger.exception(
                        "Telegram monitor: ошибка сбора ссылок | digest_id=%s",
                        digest_id,
                    )
            merged_seed_urls = self._merge_step1_seed_urls(manual_urls, telegram_seed_urls)
            normalized_manual_urls = self._normalize_seed_urls(merged_seed_urls)
            telegram_only_urls = [
                u for u in self._normalize_seed_urls(telegram_seed_urls) if u not in set(user_manual_urls)
            ]
            if not normalized_manual_urls:
                depleted_msg = self._snapshot_proxyapi_funds_message(digest)
                if depleted_msg:
                    self._raise_proxyapi_budget_exceeded(digest.id, depleted_msg)
            if not self.settings.enable_web_fetch and not normalized_manual_urls:
                if self.digest_proxyapi_budget_exceeded(digest.id):
                    self._raise_proxyapi_budget_exceeded(digest.id)
                if self._proxyapi_funds_depleted(check_balance=False):
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
    
            manual_candidates: list[dict[str, Any]] = []
            if user_manual_urls:
                manual_candidates.extend(
                    self._build_manual_candidates(digest, user_manual_urls, now_msk, mandatory=True)
                )
            if telegram_only_urls:
                manual_candidates.extend(
                    self._build_manual_candidates(digest, telegram_only_urls, now_msk, mandatory=False)
                )
            valid_manual_candidates = [x for x in manual_candidates if x.get("page_verified")]
            failed_manual_candidates = [x for x in manual_candidates if not x.get("page_verified")]
            if failed_manual_candidates:
                failed_urls = [str(x.get("url") or "")[:220] for x in failed_manual_candidates]
                logger.warning(
                    "Шаг 1: пропущены невалидные seed URL | digest_id=%s failed=%s valid=%s urls=%s",
                    digest.id,
                    len(failed_manual_candidates),
                    len(valid_manual_candidates),
                    "; ".join(failed_urls[:8]),
                )
                if not valid_manual_candidates and not self.settings.enable_web_fetch:
                    raise HTTPException(
                        status_code=400,
                        detail=(
                            "Не удалось подтвердить seed-ссылки и веб-поиск отключён: "
                            + "; ".join(failed_urls[:12])
                        ),
                    )

            verified_pool: list[dict[str, Any]] = []
            seen_fp: set[str] = {_url_fingerprint(u) for u in dropped_pool_urls if _url_fingerprint(u)}
            excluded_urls: list[str] = list(dropped_pool_urls)
            reject_stats: dict[str, int] = {}
            unreachable_by_host: dict[str, int] = {}
            reject_samples_by_reason: dict[str, list[dict[str, Any]]] = {}
            target_verified_pages = STEP1_MIN_VERIFIED
            target_pool_pages = max(
                target_verified_pages,
                min(int(getattr(self.settings, "step1_max_candidates_for_ui", 15) or 15), 30),
            )
            min_auto_discovered = min(
                STEP1_MIN_AUTO_DISCOVERED,
                max(0, 30 - len(valid_manual_candidates)),
            )
            collection_target_pages = min(
                30,
                max(
                    target_pool_pages,
                    min_discovered_pages,
                    len(valid_manual_candidates) + min_auto_discovered,
                ),
            )
            step1_batch_size = max(1, int(getattr(self.settings, "step1_batch_size", 20) or 20))
            soft_time_limit_sec = max(30, int(getattr(self.settings, "step1_soft_time_limit_sec", 180) or 180))
            hard_time_limit_sec = max(soft_time_limit_sec, int(getattr(self.settings, "step1_hard_time_limit_sec", 300) or 300))
            self._step1_curious_yield = None
            if self._step1_curious_mode:
                (
                    soft_time_limit_sec,
                    hard_time_limit_sec,
                    collection_target_pages,
                    min_collection_iterations,
                    self._step1_curious_yield,
                ) = apply_curious_yield_limits(
                    self.settings,
                    digest_type=digest.digest_type,
                    soft_time_sec=soft_time_limit_sec,
                    hard_time_sec=hard_time_limit_sec,
                    collection_target=collection_target_pages,
                    min_iterations=min_collection_iterations,
                )
                if self._step1_curious_yield:
                    logger.info(
                        "Шаг 1: curious yield | digest_id=%s target_pool=%s min_verified=%s "
                        "soft=%ss hard=%ss max_rub=%s search_batches=%s",
                        digest.id,
                        self._step1_curious_yield.target_pool,
                        self._step1_curious_yield.min_verified,
                        soft_time_limit_sec,
                        hard_time_limit_sec,
                        self._step1_max_cost_rub(),
                        self._step1_curious_yield.max_search_batches,
                    )
            started_monotonic = time.monotonic()
            from app.services.step1_live_progress import begin_live_progress, bind_live_digest

            begin_live_progress(digest.id, collection_target=collection_target_pages)
            bind_live_digest(digest.id)
            iteration_no = 0
            step1_collection_meta: dict[str, Any] = {
                "batch_size": step1_batch_size,
                "soft_time_limit_sec": soft_time_limit_sec,
                "hard_time_limit_sec": hard_time_limit_sec,
                "target_min_verified": target_verified_pages,
                "target_max_candidates": collection_target_pages,
                "target_pool_pages": target_pool_pages,
                "target_min_auto_discovered": min_auto_discovered,
                "min_discovered_pages": min_discovered_pages,
                "min_collection_iterations": min_collection_iterations,
                "collection_target_pages": collection_target_pages,
                "iterations": 0,
                "stop_reason": "not_started",
                "elapsed_sec": 0,
                "verified_total": 0,
                "filters_applied": [
                    {"id": x["id"], "enabled": bool(x["enabled"]), "order": int(x["order"])}
                    for x in step1_filter_states
                ],
                "tier_strict_search": bool(getattr(self.settings, "step1_tier_strict_search", True)),
            }
            baseline_e2e = median_e2e_for_digest_type(digest.digest_type or "serious")
            estimated_raw_for_10 = estimate_raw_urls_for_target(baseline_e2e)
            step1_collection_meta["conversion_e2e_baseline"] = (
                round(baseline_e2e, 4) if baseline_e2e is not None else None
            )
            step1_collection_meta["estimated_raw_for_10"] = estimated_raw_for_10
            self._step1_raw_urls_registry: set[str] = set()
            self._step1_min_fetch_limit: int | None = (
                estimated_raw_for_10 if self.settings.enable_web_fetch else None
            )
            _checkpoint_last_verified = 0
            _checkpoint_last_mono = started_monotonic

            def _bump_live_rejects() -> None:
                try:
                    from app.services.step1_live_progress import bump_live_progress

                    bump_live_progress(
                        digest.id,
                        rejected_total=sum(int(v) for v in reject_stats.values()),
                    )
                except Exception:
                    pass

            def _record_link_rejected_live() -> None:
                try:
                    from app.services.step1_live_progress import record_link_rejected

                    record_link_rejected(digest.id)
                except Exception:
                    pass

            def _record_link_accepted_live() -> None:
                try:
                    from app.services.step1_live_progress import record_link_accepted_to_pool

                    record_link_accepted_to_pool(digest.id)
                except Exception:
                    pass

            def register_reject(item: dict[str, Any]) -> None:
                codes = _reject_reason_codes(str(item.get("verification_comment") or ""))
                if not codes:
                    codes = ["unknown_reject"]
                url = str(item.get("url") or "").strip()
                for code in codes:
                    reject_stats[code] = reject_stats.get(code, 0) + 1
                    bucket = reject_samples_by_reason.setdefault(code, [])
                    if len(bucket) < reject_samples_per_reason_limit():
                        bucket.append(
                            {
                                "url": url[:500],
                                "host": (urlparse(url).hostname or "").lower()[:120] if url else "",
                                "title": str(item.get("title") or "").strip()[:220],
                                "source": str(item.get("source") or "").strip()[:120],
                                "published_at": str(item.get("published_at") or "").strip()[:80],
                                "comment": str(item.get("verification_comment") or "").strip()[:500],
                            }
                        )
                if "http_unreachable" in codes and url:
                    host = (urlparse(url).hostname or "").lower()
                    if host:
                        unreachable_by_host[host] = unreachable_by_host.get(host, 0) + 1
                self._step1_registry_track_item(digest, item)
                _bump_live_rejects()
                _record_link_rejected_live()

            pool_carried_over_initial = 0

            for kept in kept_rows:
                kept_item = self._news_candidate_to_pool_dict(kept)
                kept_item["_user_pinned_pool"] = True
                raw_u = str(kept_item.get("url") or "").strip()
                if raw_u.startswith("http"):
                    excluded_urls.append(raw_u[:800])
                if (
                    self._is_step1_filter_enabled("support_documentation_page")
                    and _is_support_documentation_url(raw_u)
                ):
                    _append_reject_reason(kept_item, "support_documentation_page")
                    kept_item["headline_editorial_ok"] = False
                    kept_item["link_status"] = False
                    kept_item["page_verified"] = False
                    snapshot_preview_row(kept_item)
                    register_reject(kept_item)
                    continue
                snapshot_preview_row(kept_item)
                fp = _url_fingerprint(raw_u)
                if fp:
                    seen_fp.add(fp)
                if kept_item.get("headline_editorial_ok") and kept_item.get("link_status"):
                    date_reject = _candidate_date_window_reject_code(digest, kept_item)
                    if date_reject and self._is_step1_filter_enabled(date_reject) and not _manual_required_dict(kept_item):
                        _append_reject_reason(kept_item, date_reject)
                        kept_item["headline_editorial_ok"] = False
                        kept_item["link_status"] = False
                        kept_item["page_verified"] = False
                        snapshot_preview_row(kept_item)
                        register_reject(kept_item)
                    else:
                        verified_pool.append(kept_item)

            if verified_pool:
                pool_carried_over_initial = len(verified_pool)
                try:
                    from app.services.step1_live_progress import bump_live_progress

                    bump_live_progress(digest.id, pool_carried_over=pool_carried_over_initial)
                except Exception:
                    pass

            def append_verified(item: dict[str, Any]) -> None:
                if _manual_required_dict(item):
                    if not item.get("link_status"):
                        return
                    promo_corpus = (
                        f"{item.get('title', '')} {item.get('description', '')} "
                        f"{item.get('article_excerpt', '')}"
                    )
                    item["headline_editorial_ok"] = True
                    item["page_verified"] = True
                    _apply_material_form_to_candidate(item, extra=promo_corpus, digest_type=digest.digest_type)
                    _normalize_candidate_source(item)
                    fp = _url_fingerprint(str(item.get("url", "")))
                    if not fp or fp in seen_fp:
                        return
                    seen_fp.add(fp)
                    verified_pool.append(item)
                    self._step1_registry_track_item(digest, item)
                    if len(verified_pool) >= STEP1_MIN_VERIFIED:
                        self._step1_min_fetch_limit = None
                    _record_link_accepted_live()
                    try:
                        from app.services.step1_live_progress import bump_live_progress

                        bump_live_progress(
                            digest.id,
                            verified_pool=len(verified_pool),
                            rejected_total=sum(int(v) for v in reject_stats.values()),
                        )
                    except Exception:
                        pass
                    return
                if not item.get("headline_editorial_ok") or not item.get("link_status"):
                    return
                date_reject = _candidate_date_window_reject_code(digest, item)
                if date_reject and self._is_step1_filter_enabled(date_reject) and not _manual_required_dict(item):
                    _append_reject_reason(item, date_reject)
                    item["headline_editorial_ok"] = False
                    item["link_status"] = False
                    item["page_verified"] = False
                    snapshot_preview_row(item)
                    register_reject(item)
                    return
                if getattr(self, "_step1_curious_mode", False):
                    title = str(item.get("title") or "")
                    excerpt = _candidate_article_excerpt(item) or str(item.get("description") or "")
                    verified_curious = bool(item.get("page_verified")) and item.get("curious_tone_score") is not None
                    tone = int(item.get("curious_tone_score") or 0) if verified_curious else curious_tone_score(
                        title, excerpt
                    )
                    item["curious_tone_score"] = tone
                    if (
                        self._is_step1_filter_enabled("off_topic_not_curious")
                        and not verified_curious
                        and not passes_curious_pool_gate(title, excerpt, url=str(item.get("url") or ""))
                    ):
                        self._step1_log_curious_tone(
                            url=str(item.get("url") or ""),
                            title=title,
                            corpus=excerpt,
                            stage="append_verified",
                            outcome="reject",
                            filter_code="off_topic_not_curious",
                        )
                        _append_reject_reason(item, "off_topic_not_curious")
                        item["headline_editorial_ok"] = False
                        item["link_status"] = False
                        item["page_verified"] = False
                        snapshot_preview_row(item)
                        register_reject(item)
                        return
                    low_tone = not passes_curious_tone_gate(title, excerpt, url=str(item.get("url") or ""))
                    if low_tone:
                        item["curious_tone_low"] = True
                    self._step1_log_curious_tone(
                        url=str(item.get("url") or ""),
                        title=title,
                        corpus=excerpt,
                        stage="append_verified",
                        outcome="low_tone" if low_tone else "accept",
                    )
                    item["total_score"] = curious_total_score_from_tone(tone, low=low_tone)
                if getattr(self, "_step1_curious_mode", False):
                    stored_url = str(item.get("url") or "")
                    if is_curious_aggregator_source(stored_url) or _is_aggregator_source(stored_url):
                        _append_reject_reason(item, "aggregator_source")
                        item["headline_editorial_ok"] = False
                        item["link_status"] = False
                        item["page_verified"] = False
                        snapshot_preview_row(item)
                        register_reject(item)
                        return
                elif step1_filter_enabled.get("aggregator_source", True) and bool(item.get("is_aggregator")):
                    _append_reject_reason(item, "aggregator_source")
                    item["headline_editorial_ok"] = False
                    item["link_status"] = False
                    snapshot_preview_row(item)
                    register_reject(item)
                    return
                promo_corpus = (
                    f"{item.get('title', '')} {item.get('description', '')} "
                    f"{item.get('article_excerpt', '')}"
                )
                material_form = _classify_material_form(item, extra=promo_corpus)
                if _should_reject_commercial_non_article(item, material_form, promo_corpus):
                    reject_code = _commercial_non_article_reject_reason(item, material_form, promo_corpus)
                    if step1_filter_enabled.get(reject_code, True) or step1_filter_enabled.get(
                        "product_tool_promo", True
                    ):
                        _append_reject_reason(item, reject_code)
                        item["headline_editorial_ok"] = False
                        item["link_status"] = False
                        item["page_verified"] = False
                        snapshot_preview_row(item)
                        register_reject(item)
                        return
                if item.get("headline_editorial_ok") and item.get("link_status"):
                    _apply_material_form_to_candidate(item, extra=promo_corpus, digest_type=digest.digest_type)
                _normalize_candidate_source(item)
                fp = _url_fingerprint(str(item.get("url", "")))
                if not fp or fp in seen_fp:
                    return
                manual_required = _manual_required_dict(item)
                if not manual_required:
                    candidate_title = str(item.get("title") or "")
                    for existing in verified_pool:
                        if _manual_required_dict(existing):
                            continue
                        if _titles_are_near_duplicates(candidate_title, str(existing.get("title") or "")):
                            _append_reject_reason(item, "duplicate_story_title")
                            item["headline_editorial_ok"] = False
                            item["link_status"] = False
                            item["page_verified"] = False
                            snapshot_preview_row(item)
                            register_reject(item)
                            return
                seen_fp.add(fp)
                verified_pool.append(item)
                self._step1_registry_track_item(digest, item)
                if len(verified_pool) >= STEP1_MIN_VERIFIED:
                    self._step1_min_fetch_limit = None
                _record_link_accepted_live()
                try:
                    from app.services.step1_live_progress import bump_live_progress

                    bump_live_progress(
                        digest.id,
                        verified_pool=len(verified_pool),
                        rejected_total=sum(int(v) for v in reject_stats.values()),
                    )
                except Exception:
                    pass

            def persist_reject_stats() -> None:
                self.db.query(Asset).filter(Asset.digest_id == digest.id, Asset.type == "step1_rejected_reasons").delete()
                self.db.add(
                    Asset(
                        digest_id=digest.id,
                        type="step1_rejected_reasons",
                        path="",
                        prompt=json.dumps(reject_stats, ensure_ascii=False),
                    )
                )
                self.db.query(Asset).filter(Asset.digest_id == digest.id, Asset.type == "step1_reject_audit").delete()
                self.db.add(
                    Asset(
                        digest_id=digest.id,
                        type="step1_reject_audit",
                        path="",
                        prompt=json.dumps(
                            {
                                "counts": reject_stats,
                                "samples_by_reason": reject_samples_by_reason,
                            },
                            ensure_ascii=False,
                        ),
                    )
                )
                self.db.query(Asset).filter(Asset.digest_id == digest.id, Asset.type == "step1_filter_counters").delete()
                self.db.add(
                    Asset(
                        digest_id=digest.id,
                        type="step1_filter_counters",
                        path="",
                        prompt=json.dumps(
                            {fid: int(reject_stats.get(fid, 0) or 0) for fid in STEP1_FILTER_DEF_BY_ID},
                            ensure_ascii=False,
                        ),
                    )
                )
                self.db.commit()
                curious_audit = getattr(self, "_step1_curious_tone_audit", None)
                curious_summary = curious_audit.flush_summary() if curious_audit else {}
                if curious_summary:
                    self.db.query(Asset).filter(
                        Asset.digest_id == digest.id, Asset.type == "step1_curious_tone_audit"
                    ).delete()
                    self.db.add(
                        Asset(
                            digest_id=digest.id,
                            type="step1_curious_tone_audit",
                            path="",
                            prompt=json.dumps(curious_summary, ensure_ascii=False),
                        )
                    )
                    self.db.commit()
                if reject_samples_by_reason:
                    top_codes = sorted(reject_stats.items(), key=lambda x: (-x[1], x[0]))[
                        : reject_audit_top_reasons_limit()
                    ]
                    for code, count in top_codes:
                        samples = reject_samples_by_reason.get(code) or []
                        preview = "; ".join(
                            f"{x.get('host') or '-'} | {x.get('published_at') or '-'} | {x.get('title') or x.get('url')}"
                            for x in samples[:3]
                        )
                        logger.info(
                            "Шаг 1: аудит отсева | digest_id=%s reason=%s count=%s samples=%s",
                            digest.id,
                            code,
                            count,
                            preview or "-",
                        )

            def merge_collection_meta(meta: dict[str, Any] | None) -> None:
                if not meta:
                    return
                step1_collection_meta["iterations"] = iteration_no + int(meta.get("iterations", 0) or 0)
                step1_collection_meta["stop_reason"] = str(meta.get("stop_reason") or step1_collection_meta["stop_reason"])
                step1_collection_meta["elapsed_sec"] = int(meta.get("elapsed_sec", 0) or 0)
                step1_collection_meta["verified_total"] = int(meta.get("verified_total", len(verified_pool)) or len(verified_pool))
                for funnel_key in ("urls_raw_merged", "urls_prefilter_rejected", "urls_sent_to_http"):
                    step1_collection_meta[funnel_key] = int(step1_collection_meta.get(funnel_key, 0) or 0) + int(
                        meta.get(funnel_key, 0) or 0
                    )

            def persist_collection_meta() -> None:
                step1_collection_meta["iterations"] = iteration_no
                step1_collection_meta["elapsed_sec"] = int(time.monotonic() - started_monotonic)
                step1_collection_meta["verified_total"] = len(verified_pool)
                step1_collection_meta["reject_reason_events"] = sum(int(v) for v in reject_stats.values())
                step1_collection_meta["rejected_total"] = step1_collection_meta["reject_reason_events"]
                step1_collection_meta["urls_raw_unique"] = len(
                    getattr(self, "_step1_raw_urls_registry", set()) or set()
                )
                ws_stats = current_step1_web_search_stats()
                if ws_stats is not None:
                    ws_stats.apply_to_meta(step1_collection_meta)
                phase_timers = current_step1_phase_timers()
                if phase_timers is not None:
                    phase_timers.merge_into(step1_collection_meta)
                apply_funnel_conversions_to_meta(step1_collection_meta, digest_type=digest.digest_type or "serious")
                self._enrich_step1_proxyapi_cost_meta(digest, step1_collection_meta)
                step1_collection_meta["links_found_free"] = max(
                    int(step1_collection_meta.get("links_found_free") or 0),
                    int(step1_collection_meta.get("urls_raw_from_seed_listings") or 0),
                )
                step1_collection_meta["links_found_paid"] = max(
                    int(step1_collection_meta.get("links_found_paid") or 0),
                    int(step1_collection_meta.get("web_search_citation_urls") or 0),
                    int(step1_collection_meta.get("urls_raw_unique") or 0),
                    int(step1_collection_meta.get("urls_raw_merged") or 0)
                    - int(step1_collection_meta.get("links_found_free") or 0),
                )
                try:
                    from app.services.step1_live_progress import snapshot_live_progress, sync_live_progress

                    live_payload = snapshot_live_progress(digest.id)
                    if live_payload:
                        for key in (
                            "pool_carried_over",
                            "pool_added_this_run",
                            "links_found_paid",
                            "links_found_free",
                            "links_found_total",
                            "links_checked",
                            "links_processed",
                            "rejected_links",
                            "reject_reason_events",
                            "pool_yield_pct",
                            "recheck_only",
                        ):
                            if live_payload.get(key) is not None:
                                step1_collection_meta[key] = live_payload[key]
                    sync_live_progress(
                        digest.id,
                        meta=step1_collection_meta,
                        reject_total=int(step1_collection_meta.get("rejected_total") or 0),
                        iteration_no=iteration_no,
                    )
                except Exception:
                    logger.debug("Шаг 1: sync live progress", exc_info=True)
                self.db.query(Asset).filter(Asset.digest_id == digest.id, Asset.type == "step1_collection_meta").delete()
                self.db.add(
                    Asset(
                        digest_id=digest.id,
                        type="step1_collection_meta",
                        path="",
                        prompt=json.dumps(step1_collection_meta, ensure_ascii=False),
                    )
                )
                _maybe_checkpoint_step1_pool()

            def _maybe_checkpoint_step1_pool() -> None:
                nonlocal _checkpoint_last_verified, _checkpoint_last_mono
                if not verified_pool:
                    return
                now = time.monotonic()
                grew = len(verified_pool) > _checkpoint_last_verified
                due = (now - _checkpoint_last_mono) >= 90
                if not grew and not due:
                    return
                _checkpoint_last_verified = len(verified_pool)
                _checkpoint_last_mono = now
                try:
                    self._persist_step1_preview_candidates(
                        digest.id,
                        preview_by_fp,
                        pinned_fps=preview_pinned_fps or None,
                    )
                    self.db.commit()
                    step1_collection_meta["last_checkpoint_verified"] = len(verified_pool)
                    step1_collection_meta["last_checkpoint_sec"] = int(now - started_monotonic)
                    logger.info(
                        "Шаг 1: промежуточный checkpoint | digest_id=%s verified=%s elapsed_sec=%s",
                        digest.id,
                        len(verified_pool),
                        int(now - started_monotonic),
                    )
                except Exception:
                    logger.warning(
                        "Шаг 1: checkpoint не сохранён | digest_id=%s",
                        digest.id,
                        exc_info=True,
                    )
                    try:
                        self.db.rollback()
                    except Exception:
                        pass

            def top_reject_reasons(limit: int = 3) -> str:
                if not reject_stats:
                    return ""
                items = sorted(reject_stats.items(), key=lambda x: (-x[1], x[0]))[:limit]
                details = ", ".join(f"{code}={count}" for code, count in items)
                return f" Основные причины отбраковки: {details}."

            def request_elapsed_tail() -> str:
                return f" Время выполнения запроса: {int(time.monotonic() - started_monotonic)} сек."
    
            for m in failed_manual_candidates:
                snapshot_preview_row(m)
                register_reject(m)
            for m in valid_manual_candidates:
                snapshot_preview_row(m)
                append_verified(m)

            registry_meta = self._step1_maintain_url_registry(digest, step1_filter_states)
            step1_collection_meta.update({k: v for k, v in registry_meta.items() if v is not None})
            reverify_filters = list(registry_meta.get("registry_filters_recheck") or [])
            if reverify_filters and self.settings.enable_web_fetch:
                re_added = self._step1_reverify_registry_candidates(
                    digest,
                    disabled_filter_ids=reverify_filters,
                    verified_pool=verified_pool,
                    seen_fp=seen_fp,
                    excluded_urls=excluded_urls,
                    snapshot_preview_row=snapshot_preview_row,
                    append_verified=append_verified,
                    register_reject=register_reject,
                )
                if re_added:
                    step1_collection_meta["registry_reverified_added"] = re_added

            effective_min_iterations = min_collection_iterations
            if (
                self._step1_cheap_sources_first_enabled()
                and self.settings.enable_web_fetch
                and len(verified_pool) < collection_target_pages
            ):
                try:
                    with step1_phase("cheap_seeds"):
                        cheap_meta = self._step1_scan_seed_listings(
                            digest,
                            verified_pool=verified_pool,
                            seen_fp=seen_fp,
                            excluded_urls=excluded_urls,
                            now_msk=now_msk,
                            snapshot_preview_row=snapshot_preview_row,
                            append_verified=append_verified,
                            register_reject=register_reject,
                            stop_at=collection_target_pages,
                            hard_limit_monotonic=started_monotonic + hard_time_limit_sec,
                            filter_enabled=self._is_step1_filter_enabled,
                        )
                        step1_collection_meta.update(
                            {k: v for k, v in cheap_meta.items() if v is not None}
                        )
                except Exception:
                    logger.exception("Шаг 1: дешёвый сбор из seed-лент не выполнен")
            if self._step1_cheap_sources_first_enabled() and len(verified_pool) >= STEP1_MIN_VERIFIED:
                effective_min_iterations = 1

            partial_rebuild_web_added = 0
            partial_rebuild_no_web_progress = False
            if self.settings.enable_web_fetch and len(verified_pool) < collection_target_pages:
                try:
                    before_web_count = len(verified_pool)
                    try:
                        pre_meta = self._step1_collect_iterative_batches(
                            digest,
                            verified_pool=verified_pool,
                            seen_fp=seen_fp,
                            excluded_urls=excluded_urls,
                            now_msk=now_msk,
                            snapshot_preview_row=snapshot_preview_row,
                            append_verified=append_verified,
                            register_reject=register_reject,
                            target_min_verified=target_verified_pages,
                            target_max_candidates=collection_target_pages,
                            batch_size=step1_batch_size,
                            soft_limit_sec=soft_time_limit_sec,
                            hard_limit_sec=hard_time_limit_sec,
                            started_monotonic=started_monotonic,
                            start_iteration=iteration_no,
                            min_iterations=effective_min_iterations,
                            filter_enabled=self._is_step1_filter_enabled,
                            allow_supplement_rounds=(
                                not partial_rebuild
                            ) or len(verified_pool) < STEP1_MIN_VERIFIED,
                        )
                    except TypeError:
                        pre_meta = self._step1_collect_iterative_batches(
                            digest,
                            verified_pool=verified_pool,
                            seen_fp=seen_fp,
                            excluded_urls=excluded_urls,
                            now_msk=now_msk,
                            snapshot_preview_row=snapshot_preview_row,
                            append_verified=append_verified,
                            register_reject=register_reject,
                            target_min_verified=target_verified_pages,
                            target_max_candidates=collection_target_pages,
                            batch_size=step1_batch_size,
                            soft_limit_sec=soft_time_limit_sec,
                            hard_limit_sec=hard_time_limit_sec,
                            started_monotonic=started_monotonic,
                            start_iteration=iteration_no,
                            min_iterations=effective_min_iterations,
                        )
                    merge_collection_meta(pre_meta)
                    iteration_no += int(pre_meta.get("iterations", 0) or 0)
                    if str(pre_meta.get("stop_reason") or "") == "user_cancelled":
                        step1_user_cancelled = True
                    if str(pre_meta.get("stop_reason") or "") == "hard_cost_stop":
                        step1_hard_cost_stopped = True
                    if str(pre_meta.get("stop_reason") or "") == "proxyapi_budget_exceeded":
                        step1_proxyapi_depleted = True
                    partial_rebuild_web_added = max(0, len(verified_pool) - before_web_count)
                    partial_rebuild_no_web_progress = partial_rebuild and partial_rebuild_web_added <= 0
                    if partial_rebuild_no_web_progress:
                        step1_collection_meta["partial_rebuild_no_web_progress"] = True
                        logger.warning(
                            "Шаг 1: частичная пересборка без прироста после основного web-прохода | digest_id=%s kept=%s elapsed_sec=%s",
                            digest.id,
                            len(kept_rows),
                            int(time.monotonic() - started_monotonic),
                        )
                except Exception:
                    web_flow_available = False
                    logger.exception("Шаг 1: итеративный сбор URL через веб-поиск не выполнен")
            if (
                not step1_user_cancelled
                and not step1_proxyapi_depleted
                and self._proxyapi_budget_exceeded()
                and len(verified_pool) < STEP1_MIN_VERIFIED
                and not normalized_manual_urls
            ):
                self._raise_proxyapi_budget_exceeded(digest.id)

            # CrewAI: при обычном сборе не тратим 10–20 мин, если веб уже дал 1–9 статей (есть журнал отбраковки).
            # При пересборке/частичной пересборке CrewAI по-прежнему может добрать пул.
            crew_only_if_empty = bool(getattr(self.settings, "step1_crew_fallback_only_if_empty", True))
            partial_rebuild_has_manual_kept = partial_rebuild and any(
                "MANUAL_REQUIRED:" in str(getattr(r, "verification_comment", "") or "")
                for r in kept_rows
            )
            skip_crew_partial_web = (
                crew_only_if_empty
                and not rebuild
                and not partial_rebuild
                and 0 < len(verified_pool) < STEP1_MIN_VERIFIED
            )
            ws_stats = current_step1_web_search_stats()
            skip_crew_bad_web_search = False
            if ws_stats is not None and ws_stats.api_calls > 0:
                if step1_web_search_api_cap_reached() and ws_stats.citation_urls == 0:
                    skip_crew_bad_web_search = True
                elif (
                    ws_stats.citation_urls == 0
                    and ws_stats.model_urls_dropped >= 15
                    and ws_stats.api_calls >= 5
                ):
                    skip_crew_bad_web_search = True
            if skip_crew_bad_web_search and len(verified_pool) == 0:
                skip_crew_bad_web_search = False
            if skip_crew_bad_web_search:
                step1_collection_meta["crew_skipped_bad_web_search"] = True
                logger.warning(
                    "Шаг 1: CrewAI пропущен — web_search без citation URL (cap/галлюцинации) | digest_id=%s "
                    "api_calls=%s citations=%s dropped=%s",
                    digest.id,
                    getattr(ws_stats, "api_calls", 0),
                    getattr(ws_stats, "citation_urls", 0),
                    getattr(ws_stats, "model_urls_dropped", 0),
                )
            run_crew_fallback = (
                not step1_user_cancelled
                and not step1_proxyapi_depleted
                and self.settings.enable_web_fetch
                and len(verified_pool) < STEP1_MIN_VERIFIED
                and (time.monotonic() - started_monotonic) < hard_time_limit_sec
                and not skip_crew_partial_web
                and not partial_rebuild_has_manual_kept
                and not skip_crew_bad_web_search
            )
            if run_crew_fallback:
                try:
                    with step1_phase("crew"):
                        research_candidates = self.workflow.run_candidates_research(
                            digest_type=digest.digest_type or "serious",
                            now_msk=now_msk,
                            manual_urls=normalized_manual_urls,
                        )
                        try:
                            research_candidates, research_prefilter_dropped = self._prefilter_llm_candidates_fetchable(
                                digest.id,
                                research_candidates,
                                filter_enabled=self._is_step1_filter_enabled,
                            )
                        except TypeError:
                            research_candidates, research_prefilter_dropped = self._prefilter_llm_candidates_fetchable(
                                digest.id, research_candidates
                            )
                        for dropped in research_prefilter_dropped:
                            snapshot_preview_row(dropped)
                        verify_candidates = self.workflow.run_candidates_verify(research_candidates)
                        scored_candidates = self.workflow.run_candidates_score(
                            verify_candidates,
                            now_msk=now_msk,
                            digest_type=digest.digest_type or "serious",
                        )
                        candidates, guard_rejected = self._filter_score_url_mutations(
                            verify_candidates,
                            scored_candidates,
                            filter_enabled=self._is_step1_filter_enabled,
                        )
                        for item in guard_rejected:
                            snapshot_preview_row(item)
                except Exception as exc:
                    web_flow_available = False
                    err = str(exc).lower()
                    if "llm provider not provided" in err or "badrequesterror" in err:
                        logger.exception(
                            "Шаг 1: CrewAI/LiteLLM — проверьте PROXYAPI_MODEL и имена моделей (нужен префикс openai/)"
                        )
                        raise HTTPException(
                            status_code=502,
                            detail=(
                                "Сбой CrewAI при сборе кандидатов: модель LLM указана без провайдера для LiteLLM. "
                                "Перезапустите backend после обновления или задайте PROXYAPI_MODEL=openai/gpt-4.1-mini в backend/.env. "
                                "Либо вставьте не менее 10 прямых URL статей в поле шага 1."
                            ),
                        ) from exc
                    logger.exception("Шаг 1: добор через CrewAI недоступен")
                    if self._proxyapi_budget_exceeded() and not normalized_manual_urls:
                        self._raise_proxyapi_budget_exceeded(digest.id)
                    if not normalized_manual_urls:
                        self._persist_step1_preview_candidates(digest.id, preview_by_fp)
                        raise HTTPException(
                            status_code=400,
                            detail=(
                                "Автосбор кандидатов недоступен (веб-поиск и CrewAI). "
                                "Вставьте вручную не менее 10 прямых ссылок на статьи в поле шага 1 "
                                "или проверьте PROXYAPI_API_KEY и web.enable_fetch в pipeline_settings.json."
                            ),
                        ) from exc
    
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
                for resolved_url, bundle in _expand_listing_url_candidates(raw_u, max_children=_listing_max_children(raw_u)):
                    fp = _url_fingerprint(resolved_url)
                    if fp in seen_fp:
                        continue
                    work = dict(item)
                    work["url"] = resolved_url
                    work["title"] = ""
                    work["headline_editorial_ok"] = False
                    work["link_status"] = False
                    try:
                        self._verify_llm_candidate_dict(
                            digest,
                            work,
                            prefetched_bundle=bundle,
                            filter_enabled=self._is_step1_filter_enabled,
                        )
                    except TypeError:
                        self._verify_llm_candidate_dict(digest, work, prefetched_bundle=bundle)
                    snapshot_preview_row(work)
                    if work.get("headline_editorial_ok") and work.get("link_status"):
                        append_verified(work)
                    else:
                        if fp:
                            seen_fp.add(fp)
                        excluded_urls.append(resolved_url[:800])
                        register_reject(work)
    
            need_more_for_minimum = len(verified_pool) < STEP1_MIN_VERIFIED
            if (
                not step1_user_cancelled
                and not step1_proxyapi_depleted
                and self.settings.enable_web_fetch
                and not partial_rebuild_no_web_progress
                and len(verified_pool) < STEP1_MIN_VERIFIED
                and len(verified_pool) < collection_target_pages
                and (time.monotonic() - started_monotonic) < hard_time_limit_sec
                and (
                    need_more_for_minimum
                    or int(step1_collection_meta.get("elapsed_sec", 0) or 0) < soft_time_limit_sec
                )
                and (
                    need_more_for_minimum
                    or str(step1_collection_meta.get("stop_reason") or "") not in {
                        "soft_timeout_after_collect",
                        "hard_timeout_after_collect",
                        "soft_timeout_final_attempt",
                        "hard_timeout",
                        "web_search_api_cap",
                    }
                )
            ):
                try:
                    try:
                        post_meta = self._step1_collect_iterative_batches(
                            digest,
                            verified_pool=verified_pool,
                            seen_fp=seen_fp,
                            excluded_urls=excluded_urls,
                            now_msk=now_msk,
                            snapshot_preview_row=snapshot_preview_row,
                            append_verified=append_verified,
                            register_reject=register_reject,
                            target_min_verified=target_verified_pages,
                            target_max_candidates=collection_target_pages,
                            batch_size=step1_batch_size,
                            soft_limit_sec=soft_time_limit_sec,
                            hard_limit_sec=hard_time_limit_sec,
                            started_monotonic=started_monotonic,
                            start_iteration=iteration_no,
                            min_iterations=effective_min_iterations,
                            filter_enabled=self._is_step1_filter_enabled,
                            allow_supplement_rounds=(
                                not partial_rebuild
                            ) or len(verified_pool) < STEP1_MIN_VERIFIED,
                        )
                    except TypeError:
                        post_meta = self._step1_collect_iterative_batches(
                            digest,
                            verified_pool=verified_pool,
                            seen_fp=seen_fp,
                            excluded_urls=excluded_urls,
                            now_msk=now_msk,
                            snapshot_preview_row=snapshot_preview_row,
                            append_verified=append_verified,
                            register_reject=register_reject,
                            target_min_verified=target_verified_pages,
                            target_max_candidates=collection_target_pages,
                            batch_size=step1_batch_size,
                            soft_limit_sec=soft_time_limit_sec,
                            hard_limit_sec=hard_time_limit_sec,
                            started_monotonic=started_monotonic,
                            start_iteration=iteration_no,
                            min_iterations=effective_min_iterations,
                        )
                    merge_collection_meta(post_meta)
                    iteration_no += int(post_meta.get("iterations", 0) or 0)
                    if str(post_meta.get("stop_reason") or "") == "user_cancelled":
                        step1_user_cancelled = True
                    if str(post_meta.get("stop_reason") or "") == "hard_cost_stop":
                        step1_hard_cost_stopped = True
                    if str(post_meta.get("stop_reason") or "") == "proxyapi_budget_exceeded":
                        step1_proxyapi_depleted = True
                except Exception:
                    web_flow_available = False
                    logger.exception("Шаг 1: повторный итеративный добор web-поиском не выполнен")

            manual_verified_count = sum(1 for x in verified_pool if _manual_required_dict(x))
            auto_verified_count = max(0, len(verified_pool) - manual_verified_count)
            if (
                not step1_user_cancelled
                and not step1_proxyapi_depleted
                and self.settings.enable_web_fetch
                and manual_verified_count > 0
                and auto_verified_count == 0
            ):
                # Режим "только ручные" на пересборке воспринимается как регресс:
                # делаем отдельный rescue-проход автопоиска, чтобы попытаться добрать
                # хотя бы несколько авто-материалов независимо от текущего timeout-стопа.
                rescue_need = max(3, min_auto_discovered)
                logger.warning(
                    "Шаг 1: rescue-добор автоновостей (manual_only) | digest_id=%s keep_manual=%s need=%s",
                    digest.id,
                    manual_verified_count,
                    rescue_need,
                )
                try:
                    rescue_items, rescue_funnel = self._collect_search_verified_candidates(
                        digest.id,
                        digest,
                        now_msk,
                        seen_fp,
                        limit=rescue_need,
                        on_row=snapshot_preview_row,
                        register_reject=register_reject,
                        query_override=(
                            self._step1_curious_human_stories_query(digest)
                            if is_curious_digest(digest.digest_type)
                            else self._step1_fresh_tier1_query(digest)
                        ),
                        skip_urls=excluded_urls,
                        filter_enabled=self._is_step1_filter_enabled,
                    )
                    for key in ("urls_raw_merged", "urls_prefilter_rejected", "urls_sent_to_http"):
                        step1_collection_meta[key] = int(step1_collection_meta.get(key, 0) or 0) + int(
                            rescue_funnel.get(key, 0) or 0
                        )
                    step1_collection_meta["iterations"] = int(step1_collection_meta.get("iterations", 0) or 0) + 1
                    for item in rescue_items:
                        append_verified(item)
                except Exception:
                    logger.exception("Шаг 1: rescue-добор автоновостей не выполнен")
                manual_verified_count = sum(1 for x in verified_pool if _manual_required_dict(x))
                auto_verified_count = max(0, len(verified_pool) - manual_verified_count)
    
            if verified_pool and all(_is_placeholder_candidate_dict(x) for x in verified_pool):
                persist_collection_meta()
                persist_reject_stats()
                self._persist_step1_preview_candidates(digest.id, preview_by_fp)
                raise HTTPException(
                    status_code=502,
                    detail=(
                        "Не удалось получить реальные новости: модель вернула невалидный ответ или сработал внутренний шаблон-заглушка. "
                        "Проверьте PROXYAPI_API_KEY и снова запустите шаг 1. "
                        "Либо задайте web.enable_fetch=false в pipeline_settings.json и вставьте не менее 10 прямых URL на статьи в поле шага 1 — "
                        "заголовки будут подтянуты с страниц без LLM-поиска."
                    ),
                )
    
            if not web_flow_available and len(candidates) == 0 and not normalized_manual_urls:
                persist_collection_meta()
                self._persist_step1_preview_candidates(digest.id, preview_by_fp)
                raise HTTPException(
                    status_code=400,
                    detail="Не удалось собрать кандидатов автоматически. Вставьте вручную не менее 10 прямых ссылок на статьи.",
                )

            if self.settings.enable_web_fetch and manual_verified_count > 0 and auto_verified_count == 0:
                persist_collection_meta()
                persist_reject_stats()
                self._persist_step1_preview_candidates(digest.id, preview_by_fp)
                raise HTTPException(
                    status_code=502,
                    detail=(
                        "Пересборка сохранила только ручные новости и не добавила ни одной автоновости. "
                        "Проверьте окно дат шага 0 (лучше 5-7 календарных дней), затем повторите пересборку."
                        + top_reject_reasons()
                    ),
                )

            if (
                self.settings.enable_web_fetch
                and not normalized_manual_urls
                and len(verified_pool) < min_discovered_pages
                and len(verified_pool) >= STEP1_MIN_VERIFIED
            ):
                persist_collection_meta()
                persist_reject_stats()
                self._persist_step1_preview_candidates(digest.id, preview_by_fp)
                raise HTTPException(
                    status_code=502,
                    detail=(
                        f"Найдено конкретных проверенных страниц {len(verified_pool)} из требуемых "
                        f"{min_discovered_pages}. Увеличьте окно дат на шаге 0, ослабьте фильтры в настройках "
                        f"шага 1 или снизьте порог «минимум найденных страниц»."
                        + top_reject_reasons()
                    ),
                )

            if (
                not step1_user_cancelled
                and not step1_proxyapi_depleted
                and self.settings.enable_web_fetch
                and not partial_rebuild_no_web_progress
                and len(verified_pool) < 5
                and (time.monotonic() - started_monotonic) < hard_time_limit_sec
                and reject_stats.get("published_before_window", 0) >= 3
            ):
                logger.warning(
                    "Шаг 1: fallback-добор из seed-лент source_tiers | digest_id=%s verified=%s",
                    digest.id,
                    len(verified_pool),
                )
                try:
                    with step1_phase("seed_fallback"):
                        seed_candidates = self._step1_effective_search_seed_urls(digest)
                        seed_scan_limit = min(len(seed_candidates), 6 if partial_rebuild else 24)
                        yield_policy = getattr(self, "_step1_curious_yield", None)
                        if yield_policy and is_curious_digest(digest.digest_type):
                            seed_scan_limit = min(seed_scan_limit, yield_policy.seed_listing_scan_limit)
                        for seed_url in seed_candidates[:seed_scan_limit]:
                            if int(time.monotonic() - started_monotonic) >= hard_time_limit_sec:
                                step1_collection_meta["seed_listing_fallback_stopped_by_hard_limit"] = True
                                break
                            if len(verified_pool) >= STEP1_MIN_VERIFIED:
                                break
                            for resolved_url, bundle in _expand_listing_url_candidates(
                                str(seed_url), max_children=_listing_max_children(str(seed_url))
                            ):
                                if int(time.monotonic() - started_monotonic) >= hard_time_limit_sec:
                                    step1_collection_meta["seed_listing_fallback_stopped_by_hard_limit"] = True
                                    break
                                if len(verified_pool) >= STEP1_MIN_VERIFIED:
                                    break
                                fp = _url_fingerprint(resolved_url)
                                if fp and fp in seen_fp:
                                    continue
                                work = self._skeleton_dict_from_search_url(
                                    resolved_url,
                                    now_msk,
                                    max(1, len(verified_pool) + 1),
                                )
                                work["source_stage"] = "seed_listing"
                                work["title"] = ""
                                work["headline_editorial_ok"] = False
                                work["link_status"] = False
                                try:
                                    self._verify_llm_candidate_dict(
                                        digest,
                                        work,
                                        prefetched_bundle=bundle,
                                        filter_enabled=self._is_step1_filter_enabled,
                                    )
                                except TypeError:
                                    self._verify_llm_candidate_dict(digest, work, prefetched_bundle=bundle)
                                snapshot_preview_row(work)
                                if work.get("headline_editorial_ok") and work.get("link_status"):
                                    append_verified(work)
                                else:
                                    if fp:
                                        seen_fp.add(fp)
                                    excluded_urls.append(str(resolved_url)[:800])
                                    register_reject(work)
                        step1_collection_meta["seed_listing_fallback_scanned"] = seed_scan_limit
                except Exception:
                    logger.exception("Шаг 1: fallback-добор из seed-лент не выполнен")

            if (
                not step1_user_cancelled
                and not step1_proxyapi_depleted
                and self.settings.enable_web_fetch
                and is_curious_digest(digest.digest_type)
                and len(verified_pool) < STEP1_MIN_VERIFIED
                and (
                    reject_stats.get("off_topic_not_curious", 0) >= max(8, len(verified_pool) * 2)
                    or reject_stats.get("published_before_window", 0) >= 5
                    or reject_stats.get("http_unreachable", 0) >= 5
                    or len(verified_pool) < 3
                )
            ):
                logger.warning(
                    "Шаг 1: дополнительный entertainment-only добор для курьёзного выпуска | digest_id=%s verified=%s off_topic_not_curious=%s",
                    digest.id,
                    len(verified_pool),
                    reject_stats.get("off_topic_not_curious", 0),
                )

                no_progress_rounds = 0
                rescue_queries = [
                    self._step1_curious_human_stories_query(digest),
                    self._step1_curious_viral_query(digest),
                    self._step1_curious_angles_query(digest),
                ]
                yield_policy = getattr(self, "_step1_curious_yield", None)
                if yield_policy:
                    rescue_queries = rescue_queries[: max(yield_policy.entertainment_rescue_queries, 3)]
                for query in rescue_queries:
                    if len(verified_pool) >= STEP1_MIN_VERIFIED:
                        break
                    need = max(1, STEP1_MIN_VERIFIED - len(verified_pool) + 2)
                    rescue_batch = int(getattr(self.settings, "step1_batch_size", 10) or 10)
                    if yield_policy:
                        rescue_batch = yield_policy.rescue_collect_batch
                    need = min(max(6, rescue_batch), need)
                    rescue_items, rescue_funnel = self._collect_search_verified_candidates(
                        digest.id,
                        digest,
                        now_msk,
                        seen_fp,
                        limit=need,
                        on_row=snapshot_preview_row,
                        register_reject=register_reject,
                        query_override=query,
                        skip_urls=excluded_urls,
                        filter_enabled=self._is_step1_filter_enabled,
                    )
                    for key in ("urls_raw_merged", "urls_prefilter_rejected", "urls_sent_to_http"):
                        step1_collection_meta[key] = int(step1_collection_meta.get(key, 0) or 0) + int(
                            rescue_funnel.get(key, 0) or 0
                        )
                    step1_collection_meta["iterations"] = int(step1_collection_meta.get("iterations", 0) or 0) + 1
                    step1_collection_meta["curious_entertainment_rescue_applied"] = True
                    before_cnt = len(verified_pool)
                    for item in rescue_items:
                        append_verified(item)
                    added = len(verified_pool) - before_cnt
                    if added <= 0:
                        no_progress_rounds += 1
                    else:
                        no_progress_rounds = 0
                    if no_progress_rounds >= 2:
                        break

            if (
                not step1_user_cancelled
                and not step1_proxyapi_depleted
                and self.settings.enable_web_fetch
                and not partial_rebuild_no_web_progress
                and not is_curious_digest(digest.digest_type)
                and len(verified_pool) < STEP1_MIN_VERIFIED
                and (time.monotonic() - started_monotonic) < hard_time_limit_sec
                and reject_stats.get("published_before_window", 0) >= 15
            ):
                logger.warning(
                    "Шаг 1: fresh-search rescue для serious (много старых URL) | digest_id=%s verified=%s published_before=%s",
                    digest.id,
                    len(verified_pool),
                    reject_stats.get("published_before_window", 0),
                )
                no_progress_rounds = 0
                rescue_queries = [
                    self._step1_fresh_breaking_query(digest),
                    self._step1_fresh_tier1_query(digest),
                ]
                for query in rescue_queries:
                    if len(verified_pool) >= STEP1_MIN_VERIFIED:
                        break
                    if int(time.monotonic() - started_monotonic) >= hard_time_limit_sec:
                        break
                    need = max(1, STEP1_MIN_VERIFIED - len(verified_pool) + 2)
                    rescue_batch = max(8, int(getattr(self.settings, "step1_batch_size", 10) or 10))
                    need = min(max(6, rescue_batch), need)
                    rescue_items, rescue_funnel = self._collect_search_verified_candidates(
                        digest.id,
                        digest,
                        now_msk,
                        seen_fp,
                        limit=need,
                        on_row=snapshot_preview_row,
                        register_reject=register_reject,
                        query_override=query,
                        skip_urls=excluded_urls,
                        filter_enabled=self._is_step1_filter_enabled,
                        verified_pool_size=len(verified_pool),
                    )
                    for key in ("urls_raw_merged", "urls_prefilter_rejected", "urls_sent_to_http"):
                        step1_collection_meta[key] = int(step1_collection_meta.get(key, 0) or 0) + int(
                            rescue_funnel.get(key, 0) or 0
                        )
                    step1_collection_meta["iterations"] = int(step1_collection_meta.get("iterations", 0) or 0) + 1
                    step1_collection_meta["serious_fresh_search_rescue_applied"] = True
                    before_cnt = len(verified_pool)
                    for item in rescue_items:
                        append_verified(item)
                    added = len(verified_pool) - before_cnt
                    if added <= 0:
                        no_progress_rounds += 1
                    else:
                        no_progress_rounds = 0
                    if no_progress_rounds >= 2:
                        break
    
            verified_pool, removed_outside_window = _filter_verified_pool_by_date_window(
                digest,
                verified_pool,
                is_filter_enabled=self._is_step1_filter_enabled,
            )
            if removed_outside_window:
                logger.warning(
                    "Шаг 1: из пула убраны материалы вне окна дат | digest_id=%s removed=%s window=%s",
                    digest.id,
                    removed_outside_window,
                    digest_news_window_hint_ru(digest),
                )
                step1_collection_meta["pool_outside_window_removed"] = removed_outside_window
                reject_stats["published_before_window"] = reject_stats.get("published_before_window", 0) + removed_outside_window

            if (
                not step1_user_cancelled
                and not step1_proxyapi_depleted
                and self.settings.enable_web_fetch
                and not partial_rebuild_no_web_progress
                and len(verified_pool) < 8
                and (time.monotonic() - started_monotonic) < hard_time_limit_sec
            ):
                try:
                    topup_meta = self._step1_topup_search_until_minimum(
                        digest,
                        verified_pool=verified_pool,
                        seen_fp=seen_fp,
                        excluded_urls=excluded_urls,
                        now_msk=now_msk,
                        snapshot_preview_row=snapshot_preview_row,
                        append_verified=append_verified,
                        register_reject=register_reject,
                        started_monotonic=started_monotonic,
                        hard_limit_sec=hard_time_limit_sec,
                        batch_size=step1_batch_size,
                        max_rounds=6,
                        filter_enabled=self._is_step1_filter_enabled,
                        phase_label="pre_min_check",
                    )
                    step1_collection_meta["topup_pre_min_rounds"] = int(topup_meta.get("rounds", 0) or 0)
                    step1_collection_meta["topup_pre_min_added"] = int(topup_meta.get("added", 0) or 0)
                    for key in ("urls_raw_merged", "urls_prefilter_rejected", "urls_sent_to_http"):
                        step1_collection_meta[key] = int(step1_collection_meta.get(key, 0) or 0) + int(
                            topup_meta.get(key, 0) or 0
                        )
                    iteration_no += int(topup_meta.get("rounds", 0) or 0)
                    if str(topup_meta.get("stop_reason") or "") == "user_cancelled":
                        step1_user_cancelled = True
                    if str(topup_meta.get("stop_reason") or "") == "hard_cost_stop":
                        step1_hard_cost_stopped = True
                    if str(topup_meta.get("stop_reason") or "") == "proxyapi_budget_exceeded":
                        step1_proxyapi_depleted = True
                except Exception:
                    logger.exception("Шаг 1: top-up итерации до минимума не выполнены")

            if step1_proxyapi_depleted:
                step1_collection_meta["stop_reason"] = "proxyapi_budget_exceeded"
                step1_collection_meta["elapsed_sec"] = int(time.monotonic() - started_monotonic)
                logger.warning(
                    "Шаг 1: сбор остановлен — нулевой баланс или исчерпан бюджет ProxyAPI | digest_id=%s verified=%s",
                    digest.id,
                    len(verified_pool),
                )
                persist_collection_meta()
                persist_reject_stats()
                if not verified_pool:
                    self._persist_step1_preview_candidates(digest.id, preview_by_fp)
                    self._raise_proxyapi_budget_exceeded(digest.id)
            elif step1_user_cancelled:
                step1_collection_meta["stop_reason"] = "user_cancelled"
                step1_collection_meta["elapsed_sec"] = int(time.monotonic() - started_monotonic)
                logger.info(
                    "Шаг 1: сбор остановлен пользователем | digest_id=%s verified=%s",
                    digest.id,
                    len(verified_pool),
                )
                persist_collection_meta()
                persist_reject_stats()
                if not verified_pool:
                    self._persist_step1_preview_candidates(digest.id, preview_by_fp)
                    raise HTTPException(
                        status_code=499,
                        detail=(
                            "Сбор остановлен. Подтверждённых кандидатов нет — сохранён журнал найденных и отбракованных материалов."
                            + request_elapsed_tail()
                        ),
                    )
                step1_accept_short_pool = True
                step1_collection_meta["short_pool_user_cancel"] = True
                logger.warning(
                    "Шаг 1: остановка с частичным пулом | digest_id=%s verified=%s min=%s",
                    digest.id,
                    len(verified_pool),
                    STEP1_MIN_VERIFIED,
                )
            elif step1_hard_cost_stopped:
                step1_collection_meta["stop_reason"] = "hard_cost_stop"
                step1_collection_meta["elapsed_sec"] = int(time.monotonic() - started_monotonic)
                step1_collection_meta["hard_stop_cost_rub"] = self._step1_hard_stop_cost_rub()
                logger.warning(
                    "Шаг 1: жёсткий лимит ₽ без цели по пулу | digest_id=%s verified=%s limit=%s",
                    digest.id,
                    len(verified_pool),
                    self._step1_hard_stop_cost_rub(),
                )
                persist_collection_meta()
                persist_reject_stats()
                if not verified_pool:
                    self._persist_step1_preview_candidates(digest.id, preview_by_fp)
                    raise HTTPException(
                        status_code=502,
                        detail=(
                            f"Расход шага 1 превысил {self._step1_hard_stop_cost_rub():g} ₽, "
                            f"а пул кандидатов не собран (найдено {len(verified_pool)}). "
                            "Проверьте окно дат, фильтры или добавьте прямые URL."
                            + request_elapsed_tail()
                        ),
                    )
                step1_accept_short_pool = True
                step1_collection_meta["hard_cost_short_pool_accepted"] = True
            partial_rebuild_new_count = max(0, len(verified_pool) - len(kept_rows)) if partial_rebuild else 0
            partial_rebuild_short_pool_ok = (
                partial_rebuild
                and partial_rebuild_new_count > 0
                and len(verified_pool) >= len(kept_rows)
            )
            if partial_rebuild_short_pool_ok and len(verified_pool) < STEP1_MIN_VERIFIED:
                step1_collection_meta["partial_rebuild_short_pool_accepted"] = True
                step1_collection_meta["partial_rebuild_new_count"] = partial_rebuild_new_count
                logger.warning(
                    "Шаг 1: частичная пересборка принята коротким пулом | digest_id=%s total=%s kept=%s new=%s min=%s",
                    digest.id,
                    len(verified_pool),
                    len(kept_rows),
                    partial_rebuild_new_count,
                    STEP1_MIN_VERIFIED,
                )
            elif len(verified_pool) < STEP1_MIN_VERIFIED and not step1_accept_short_pool:
                persist_collection_meta()
                persist_reject_stats()
                rebuild_excluded_note = ""
                if rebuild and dropped_pool_urls:
                    rebuild_excluded_note = (
                        f" Из прошлого пула исключено {len(dropped_pool_urls)} URL без галочки — "
                        "автопоиск не нашёл достаточно других подтверждённых статей."
                    )
                if partial_rebuild and len(kept_rows) >= STEP1_MIN_VERIFIED:
                    self._restore_step1_verified_candidates(digest.id, prev_verified_backup)
                    raise HTTPException(
                        status_code=502,
                        detail=(
                            f"Не удалось добрать пул до {STEP1_MIN_VERIFIED} новостей (сохранено {len(verified_pool)} "
                            f"из {len(kept_rows)} отмеченных). Восстановлен прежний пул."
                            + rebuild_excluded_note
                            + top_reject_reasons()
                            + request_elapsed_tail()
                        ),
                    )
                if rebuild and prev_verified_backup and len(prev_verified_backup) > len(verified_pool):
                    self._restore_step1_verified_candidates(digest.id, prev_verified_backup)
                    kept_note = (
                        f" Отмечено для сохранения: {len(kept_rows)}."
                        if partial_rebuild and kept_rows
                        else ""
                    )
                    raise HTTPException(
                        status_code=502,
                        detail=(
                            f"Пересборка не добавила новых подтверждённых материалов (получено {len(verified_pool)}, "
                            f"в прежнем пуле было {len(prev_verified_backup)}).{kept_note}{rebuild_excluded_note} "
                            f"Восстановлен прежний пул."
                            + top_reject_reasons()
                            + request_elapsed_tail()
                        ),
                    )
                if len(verified_pool) > 0:
                    for item in verified_pool:
                        fp = _url_fingerprint(str(item.get("url") or ""))
                        if fp:
                            preview_by_fp[fp] = dict(item)
                    self._persist_step1_preview_candidates(digest.id, preview_by_fp)
                    self._restore_step1_verified_candidates(digest.id, verified_pool)
                    step1_skip_final_preview = True
                    raise HTTPException(
                        status_code=502,
                        detail=(
                            f"Подтверждено только {len(verified_pool)} материалов по страницам (нужно минимум {STEP1_MIN_VERIFIED}). "
                            f"Частичный пул из {len(verified_pool)} новостей сохранён — можно доработать вручную или пересобрать. "
                            f"Окно дат: {digest_news_window_hint_ru(digest)}."
                            + top_reject_reasons()
                            + request_elapsed_tail()
                        ),
                    )
                self.db.query(NewsCandidate).filter(NewsCandidate.digest_id == digest.id).delete()
                self.db.commit()
                step1_skip_final_preview = True
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
                window_hint = digest_news_window_hint_ru(digest)
                date_tail = ""
                if reject_stats.get("published_before_window", 0) >= 3:
                    date_tail = (
                        f" Много ссылок вне окна дат ({window_hint}) — увеличьте «Окно поиска» на шаге 0 "
                        "или выберите «календарные» дни, либо вставьте свежие прямые URL вручную."
                    )
                raise HTTPException(
                    status_code=502,
                    detail=(
                        f"Подтверждено только {len(verified_pool)} материалов по страницам (нужно минимум {STEP1_MIN_VERIFIED}). "
                        f"Окно дат: {window_hint}. "
                        "Добавьте прямые URL статей в поле шага 1 — автопоиск идёт через ProxyAPI web_search (и опционально SerpAPI/Tavily)."
                        + top_reject_reasons()
                        + date_tail
                        + search_hint
                        + tail
                        + request_elapsed_tail()
                    ),
                )

            if not step1_proxyapi_depleted:
                self._clear_proxyapi_budget_alert(digest.id)

            if not step1_user_cancelled and not step1_hard_cost_stopped and not step1_proxyapi_depleted and verified_pool:
                try:
                    verified_pool, crew_enrich_meta = self._step1_crew_enrich_verified_pool(
                        digest,
                        verified_pool,
                        now_msk,
                        filter_enabled=self._is_step1_filter_enabled,
                    )
                    if crew_enrich_meta.get("enabled"):
                        step1_collection_meta["crew_enrich"] = crew_enrich_meta
                except Exception:
                    logger.exception("Шаг 1: Crew enrich проверенного пула не выполнен")

            verified_pool_before_rebalance = list(verified_pool)
            logger.info(
                "Шаг 1: найдено конкретных проверенных страниц | digest_id=%s count=%s required=%s target_max=%s iterations=%s",
                digest.id,
                len(verified_pool_before_rebalance),
                target_verified_pages,
                target_pool_pages,
                iteration_no,
            )
            verified_pool = self._pin_kept_candidates_first(verified_pool, keep_ids_ordered, kept_rows)
            verified_pool = self._dedupe_verified_pool_dicts(verified_pool)
            pinned_fps = set(preview_pinned_fps)
            pool_host_cap = STEP1_POOL_PER_HOST_CAP
            if len(verified_pool) > collection_target_pages:
                verified_pool_before_rebalance = list(verified_pool)
                rebalance_target = collection_target_pages
                if partial_rebuild and pinned_fps:
                    rebalance_target = max(rebalance_target, len(pinned_fps))
                short_pool = len(verified_pool_before_rebalance) < STEP1_MIN_VERIFIED
                if short_pool:
                    verified_pool = _rebalance_verified_pool_host_cap_only(
                        verified_pool,
                        min(rebalance_target, len(verified_pool_before_rebalance)),
                        pinned_fps=pinned_fps,
                        per_host_cap=pool_host_cap,
                        digest_type=digest.digest_type,
                    )
                else:
                    verified_pool = _rebalance_verified_pool(
                        verified_pool,
                        rebalance_target,
                        pinned_fps=pinned_fps,
                        digest_type=digest.digest_type,
                        per_host_cap=pool_host_cap,
                    )
                if len(verified_pool) < min(rebalance_target, len(verified_pool_before_rebalance)):
                    verified_pool = _rebalance_verified_pool_host_cap_only(
                        verified_pool_before_rebalance,
                        min(rebalance_target, len(verified_pool_before_rebalance)),
                        pinned_fps=pinned_fps,
                        per_host_cap=pool_host_cap,
                        digest_type=digest.digest_type,
                    )
                elif (
                    len(verified_pool) < STEP1_MIN_VERIFIED
                    and len(verified_pool_before_rebalance) >= STEP1_MIN_VERIFIED
                ):
                    verified_pool = _rebalance_verified_pool_host_cap_only(
                        verified_pool_before_rebalance,
                        max(rebalance_target, STEP1_MIN_VERIFIED),
                        pinned_fps=pinned_fps,
                        per_host_cap=pool_host_cap,
                        digest_type=digest.digest_type,
                    )
                if (
                    len(verified_pool) < STEP1_MIN_VERIFIED
                    and len(verified_pool_before_rebalance) >= STEP1_MIN_VERIFIED
                ):
                    verified_pool = _rebalance_verified_pool_host_cap_only(
                        verified_pool_before_rebalance,
                        min(len(verified_pool_before_rebalance), collection_target_pages),
                        pinned_fps=pinned_fps,
                        per_host_cap=pool_host_cap,
                        digest_type=digest.digest_type,
                    )
                    logger.warning(
                        "Шаг 1: rebalance урезал пул ниже минимума — восстановлен проверенный список | digest_id=%s count=%s",
                        digest.id,
                        len(verified_pool),
                    )
            else:
                verified_pool_before_rebalance = list(verified_pool)
                verified_pool = _rebalance_verified_pool_host_cap_only(
                    verified_pool,
                    len(verified_pool),
                    pinned_fps=pinned_fps,
                    per_host_cap=pool_host_cap,
                    digest_type=digest.digest_type,
                )
                logger.info(
                    "Шаг 1: пул с лимитом на домен | digest_id=%s count=%s max_per_host=%s",
                    digest.id,
                    len(verified_pool),
                    pool_host_cap,
                )
            logger.info(
                "Шаг 1: итоговый пул перед сохранением | digest_id=%s count=%s iterations=%s",
                digest.id,
                len(verified_pool),
                iteration_no,
            )
            if (
                not step1_user_cancelled
                and not step1_proxyapi_depleted
                and len(verified_pool) < STEP1_MIN_VERIFIED
                and len(verified_pool_before_rebalance) >= STEP1_MIN_VERIFIED
            ):
                if self.settings.enable_web_fetch:
                    try:
                        topup_meta = self._step1_topup_search_until_minimum(
                            digest,
                            verified_pool=verified_pool,
                            seen_fp=seen_fp,
                            excluded_urls=excluded_urls,
                            now_msk=now_msk,
                            snapshot_preview_row=snapshot_preview_row,
                            append_verified=append_verified,
                            register_reject=register_reject,
                            started_monotonic=started_monotonic,
                            hard_limit_sec=hard_time_limit_sec,
                            batch_size=step1_batch_size,
                        max_rounds=6,
                        filter_enabled=self._is_step1_filter_enabled,
                        respect_host_cap=False,
                        phase_label="post_rebalance",
                        )
                        step1_collection_meta["topup_post_rebalance_rounds"] = int(topup_meta.get("rounds", 0) or 0)
                        step1_collection_meta["topup_post_rebalance_added"] = int(topup_meta.get("added", 0) or 0)
                        for key in ("urls_raw_merged", "urls_prefilter_rejected", "urls_sent_to_http"):
                            step1_collection_meta[key] = int(step1_collection_meta.get(key, 0) or 0) + int(
                                topup_meta.get(key, 0) or 0
                            )
                        iteration_no += int(topup_meta.get("rounds", 0) or 0)
                    except Exception:
                        logger.exception("Шаг 1: финальный top-up после rebalance не выполнен")

                if not step1_user_cancelled and not step1_proxyapi_depleted:
                    for item in verified_pool_before_rebalance:
                        fp = _url_fingerprint(str(item.get("url") or ""))
                        if fp:
                            preview_by_fp[fp] = dict(item)
                    self._persist_step1_preview_candidates(digest.id, preview_by_fp)
                    raise HTTPException(
                        status_code=502,
                        detail=(
                            f"Подтверждено {len(verified_pool_before_rebalance)} материалов, но в итоговый пул "
                            f"вошло только {len(verified_pool)} (нужно {STEP1_MIN_VERIFIED}). "
                            "В шаге 2 показаны все проверенные и отбракованные карточки — добавьте ручные URL с других источников "
                            "или пересоберите пул."
                        ),
                    )

            verified_pool, removed_after_topup = _filter_verified_pool_by_date_window(
                digest,
                verified_pool,
                is_filter_enabled=self._is_step1_filter_enabled,
            )
            if removed_after_topup:
                logger.warning(
                    "Шаг 1: после rebalance/top-up убраны материалы вне окна | digest_id=%s removed=%s",
                    digest.id,
                    removed_after_topup,
                )
                step1_collection_meta["pool_outside_window_removed_after_rebalance"] = removed_after_topup

            # Кандидатов удаляем только после успешного rebalance.
            self.db.query(NewsCandidate).filter(NewsCandidate.digest_id == digest.id).delete()

            verified_pool = self._dedupe_verified_pool_dicts(verified_pool)
            self._renumber_verified_pool_items(verified_pool)
            try:
                from app.services.step1_live_progress import finalize_live_pool_stats, snapshot_live_progress

                snap = snapshot_live_progress(digest.id)
                rejected_links = int(snap.get("rejected_links") or 0) if snap else None
                finalize_live_pool_stats(
                    digest.id,
                    verified_pool=len(verified_pool),
                    pool_carried_over=pool_carried_over_initial,
                    rejected_links=rejected_links,
                )
            except Exception:
                logger.debug("Шаг 1: finalize live pool stats", exc_info=True)
            # #region agent log
            try:
                from app.services.agent_debug_log import agent_debug_log

                _ws = current_step1_web_search_stats()
                agent_debug_log(
                    "H6",
                    "digest_service.run_step_1:before_save",
                    "final_pool",
                    {
                        "digest_id": digest.id,
                        "verified_pool": len(verified_pool),
                        "stop_reason": step1_collection_meta.get("stop_reason"),
                        "api_calls": getattr(_ws, "api_calls", None) if _ws else None,
                        "citation_urls": getattr(_ws, "citation_urls", None) if _ws else None,
                        "cache_hits": getattr(_ws, "cache_hits", None) if _ws else None,
                        "reject_top": sorted(
                            ((k, v) for k, v in reject_stats.items() if v),
                            key=lambda x: -int(x[1]),
                        )[:6],
                    },
                )
            except Exception:
                pass
            # #endregion
            entities: list[NewsCandidate] = []
            seen_urls_lower: set[str] = set()
            seq = 0
            for item in verified_pool:
                apply_resolved_origin(item)
                url_norm = str(item.get("url", "")).strip().lower()
                if not url_norm.startswith("http"):
                    continue
                if url_norm in seen_urls_lower:
                    continue
                is_duplicate = bool(item.get("is_duplicate", False))
                seen_urls_lower.add(url_norm)
                seq += 1
                entity = NewsCandidate(
                    digest_id=digest.id,
                    original_number=seq,
                    title=str(item.get("title", ""))[:500],
                    url=str(item.get("url", ""))[:1000],
                    source=_publisher_host_key(item)[:255],
                    tier=str(item.get("tier", "Tier-3"))[:32],
                    published_at=str(item.get("published_at", "")),
                    category=str(item.get("category", "technology"))[:120],
                    description=str(item.get("description", "")),
                    article_excerpt=_candidate_article_excerpt(item),
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
            persist_collection_meta()
            persist_reject_stats()
            if int(step1_collection_meta.get("verified_total", 0) or 0) >= 5:
                record_e2e_sample(
                    digest.digest_type or "serious",
                    step1_collection_meta.get("conversion_e2e"),
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
            if step1_proxyapi_depleted:
                self._raise_proxyapi_budget_exceeded(digest.id)
            return entities

        finally:
            step1_end_run(digest.id)
            try:
                from app.services.step1_live_progress import bind_live_digest, finish_live_progress

                bind_live_digest(None)
                finish_live_progress(digest.id)
            except Exception:
                pass
            self._deactivate_step1_filter_states()
            try:
                save_filter_snapshot(
                    self.db,
                    digest.digest_type,
                    enabled_map_from_filter_states(step1_filter_states),
                )
                self.db.commit()
            except Exception:
                logger.debug("Реестр URL: save filter snapshot | digest_id=%s", digest.id, exc_info=True)
            self._step1_raw_urls_registry = set()
            self._step1_min_fetch_limit = None
            self._active_unreachable_hosts = set()
            self._active_unreachable_host_failures = {}
            if discovered_by_fp:
                final_urls = [
                    str(u)
                    for (u,) in self.db.query(NewsCandidate.url).filter(NewsCandidate.digest_id == digest.id).all()
                ]
                _align_discovered_journal_with_final_pool(
                    discovered_by_fp,
                    final_candidate_urls=final_urls,
                )
                self._persist_step1_discovered_news(digest.id, discovery_run.id, discovered_by_fp)
                self._sync_filter_counters_from_journal(digest.id)
            try:
                from app.services.step1_statistics import persist_step1_statistics_snapshot

                persist_step1_statistics_snapshot(
                    self.db,
                    digest.id,
                    discovery_run_id=discovery_run.id,
                )
            except Exception:
                logger.debug(
                    "Шаг 1: сохранение снимка статистики | digest_id=%s",
                    digest.id,
                    exc_info=True,
                )
            pool_rows = self.db.query(NewsCandidate).filter(NewsCandidate.digest_id == digest.id).count()
            if pool_rows == 0 and discovered_by_fp and not step1_skip_final_preview:
                self._persist_step1_preview_candidates(digest.id, discovered_by_fp)
            self.db.refresh(digest)
            self._snapshot_proxyapi_after(digest)
            self.db.refresh(digest)
            step1_cost = self._last_step_balance_delta_rub(digest)
            if step1_cost is None:
                step1_cost = self._live_step1_balance_delta_rub(digest)
            if step1_cost is None:
                meta = self._read_step1_collection_meta(digest.id)
                from app.services.step1_usage_breakdown import estimate_proxyapi_cost_from_meta

                est = estimate_proxyapi_cost_from_meta(meta)
                if est > 0:
                    step1_cost = est
            if step1_cost is not None and step1_cost > 0:
                self._save_cost(
                    digest_id=digest.id,
                    step="step_1",
                    agent_name="NewsResearchAgent",
                    model=AGENT_MODEL_RECOMMENDATIONS["NewsResearchAgent"],
                    request_label="step_1_collect_pool",
                    cost_rub=step1_cost,
                )
            self._finalize_step1_discovery_run_metrics(discovery_run, digest)

    def _clear_downstream_for_reselect(self, digest: Digest) -> None:
        """Сброс шагов 3–4 при новом выборе пятёрки (аналитика, финал, картинки)."""
        for asset in self.db.query(Asset).filter(Asset.digest_id == digest.id).all():
            if asset.path:
                path = Path(asset.path)
                if path.exists():
                    try:
                        path.unlink()
                    except OSError:
                        pass
        self.db.query(Asset).filter(Asset.digest_id == digest.id).delete()
        self.db.query(Analytics).filter(Analytics.digest_id == digest.id).delete()
        self.db.query(FinalOutput).filter(FinalOutput.digest_id == digest.id).delete()
        self.db.query(QualityCheck).filter(QualityCheck.digest_id == digest.id).delete()
        digest.step4_selected_image_variant = None
        digest.step2_budget_capped = False

    def _save_step2_order_rationale(self, digest_id: int, rationale: str) -> None:
        text = str(rationale or "").strip()
        self.db.query(Asset).filter(
            Asset.digest_id == digest_id,
            Asset.type == STEP2_ORDER_RATIONALE_ASSET,
        ).delete()
        if text:
            self.db.add(
                Asset(
                    digest_id=digest_id,
                    type=STEP2_ORDER_RATIONALE_ASSET,
                    path="",
                    prompt=text[:2000],
                )
            )

    def load_step2_order_rationale(self, digest_id: int) -> str:
        row = (
            self.db.query(Asset)
            .filter(Asset.digest_id == digest_id, Asset.type == STEP2_ORDER_RATIONALE_ASSET)
            .order_by(Asset.id.desc())
            .first()
        )
        return str(row.prompt or "").strip() if row else ""

    def _prepare_for_reorder(self, digest: Digest) -> None:
        """Перед сменой порядка после шага 3+ — сбросить аналитику и финал, вернуть status=selected."""
        has_analytics = (
            self.db.query(Analytics.id).filter(Analytics.digest_id == digest.id).first() is not None
        )
        has_final = (
            self.db.query(FinalOutput.id).filter(FinalOutput.digest_id == digest.id).first() is not None
        )
        if digest.status in {STATUS_ANALYTICS, STATUS_FINAL} or has_analytics or has_final:
            self._clear_downstream_for_reselect(digest)
        digest.status = STATUS_SELECTED
        digest.current_step = "step_2"

    def add_manual_urls_to_pool(self, digest_id: int, urls: list[str]) -> dict[str, Any]:
        """Добавить ручные ссылки в пул кандидатов на шаге 2 (без пересборки шага 1)."""
        digest = self.get_digest(digest_id)
        if digest.status not in SELECT_NEWS_ALLOWED:
            raise HTTPException(
                status_code=400,
                detail="Добавление ссылок доступно после шага 1 (step_1_candidates) и на следующих шагах выпуска.",
            )
        merged_input: list[str] = []
        for raw in urls:
            value = str(raw or "").strip()
            if value:
                merged_input.append(value)
        normalized = self._normalize_manual_urls(merged_input)
        if not normalized:
            raise HTTPException(status_code=400, detail="Укажите хотя бы одну ссылку (http/https).")

        existing = self.db.query(NewsCandidate).filter(NewsCandidate.digest_id == digest.id).all()
        pool_cap = max(
            STEP1_MIN_VERIFIED,
            int(getattr(self.settings, "step1_max_candidates_for_ui", 15) or 15),
        )
        existing_urls_lower = {str(c.url or "").strip().lower() for c in existing}
        existing_fps = {article_page_fingerprint(str(c.url or "")) for c in existing if str(c.url or "").strip()}

        to_add: list[str] = []
        skipped_duplicates: list[str] = []
        for url in normalized:
            url_lower = url.strip().lower()
            fp = article_page_fingerprint(url)
            if url_lower in existing_urls_lower or (fp and fp in existing_fps):
                skipped_duplicates.append(url)
                continue
            to_add.append(url)

        if not to_add:
            return {
                "added": [],
                "skipped_duplicates": skipped_duplicates,
                "pool_count": len(existing),
                "detail": "Все указанные ссылки уже есть в пуле.",
            }
        if len(existing) + len(to_add) > pool_cap:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"В пуле уже {len(existing)} из {pool_cap} слотов. "
                    "Снимите лишние карточки пересборкой или добавьте меньше ссылок."
                ),
            )

        step1_filter_states = self._read_step1_filter_states(digest.digest_type)
        now_msk = current_msk_iso()
        created_rows: list[dict[str, Any]] = []
        try:
            self._activate_step1_filter_states(step1_filter_states, digest_type=digest.digest_type)
            built = self._build_manual_candidates(
                digest,
                to_add,
                now_msk,
                mandatory=True,
                manual_step=2,
            )
            max_seq = max((int(c.original_number or 0) for c in existing), default=0)
            for item in built:
                apply_resolved_origin(item)
                _normalize_candidate_source(item)
                max_seq += 1
                title = str(item.get("title") or "").strip()
                if len(title) < 4:
                    title = f"Страница: {_host_from_url(str(item.get('url', '')))}"
                entity = NewsCandidate(
                    digest_id=digest.id,
                    original_number=max_seq,
                    title=title[:500],
                    url=str(item.get("url", ""))[:1000],
                    source=str(item.get("source", "") or _host_from_url(str(item.get("url", ""))))[:255],
                    tier=str(item.get("tier", "Tier-3"))[:32],
                    published_at=str(item.get("published_at", ""))[:100] or PUBLISHED_AT_UNDEFINED,
                    category=str(item.get("category", "manual"))[:120],
                    description=str(item.get("description", "")),
                    article_excerpt=_candidate_article_excerpt(item),
                    significance_score=int(item.get("significance_score", 3)),
                    novelty_score=int(item.get("novelty_score", 3)),
                    impact_score=int(item.get("impact_score", 3)),
                    total_score=int(item.get("total_score", 9)),
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
                self.db.flush()
                created_rows.append(
                    {
                        "id": entity.id,
                        "url": entity.url,
                        "title": entity.title,
                        "page_verified": entity.page_verified,
                        "headline_editorial_ok": entity.headline_editorial_ok,
                        "link_status": entity.link_status,
                    }
                )
            self.db.commit()
        finally:
            self._deactivate_step1_filter_states()

        self._renumber_digest_candidate_rows(digest.id)
        self._dedupe_digest_candidate_rows(digest.id)

        logger.info(
            "Шаг 2: ручные URL в пул | digest_id=%s added=%s skipped_dup=%s pool=%s",
            digest.id,
            [r["id"] for r in created_rows],
            len(skipped_duplicates),
            len(existing) + len(created_rows),
        )
        return {
            "added": created_rows,
            "skipped_duplicates": skipped_duplicates,
            "pool_count": len(existing) + len(created_rows),
        }

    def _candidate_publisher_host(self, candidate: NewsCandidate) -> str:
        return _publisher_host_key({"url": str(candidate.url or ""), "source": str(candidate.source or "")})

    def _selection_respects_host_cap(
        self, candidates: list[NewsCandidate], *, max_per_host: int = STEP2_MAX_PER_SOURCE
    ) -> tuple[bool, str | None]:
        counts: dict[str, int] = {}
        for c in candidates:
            host = self._candidate_publisher_host(c)
            counts[host] = counts.get(host, 0) + 1
            if counts[host] > max_per_host:
                return False, host
        return True, None

    def _pick_top5_with_host_cap(
        self,
        strict_allowed: list[NewsCandidate],
        mandatory_manual: list[NewsCandidate],
        *,
        max_per_host: int = STEP2_MAX_PER_SOURCE,
    ) -> list[NewsCandidate]:
        chosen: list[NewsCandidate] = []
        host_counts: dict[str, int] = {}

        def can_add(c: NewsCandidate) -> bool:
            host = self._candidate_publisher_host(c)
            return host_counts.get(host, 0) < max_per_host

        def add(c: NewsCandidate) -> None:
            host = self._candidate_publisher_host(c)
            chosen.append(c)
            host_counts[host] = host_counts.get(host, 0) + 1

        for c in mandatory_manual:
            if len(chosen) >= 5:
                break
            if can_add(c):
                add(c)
        for c in sorted(strict_allowed, key=lambda x: x.total_score, reverse=True):
            if len(chosen) >= 5:
                break
            if c.id in {x.id for x in chosen}:
                continue
            if can_add(c):
                add(c)
        return chosen

    def select_news(self, digest_id: int, selected_ids: list[int], top5: bool) -> list[SelectedNews]:
        digest = self.get_digest(digest_id)
        if digest.status not in SELECT_NEWS_ALLOWED:
            raise HTTPException(
                status_code=400,
                detail="Выбор пятёрки доступен после шага 1 (step_1_candidates) и на следующих шагах выпуска.",
            )
        if digest.status != STATUS_STEP1:
            self._clear_downstream_for_reselect(digest)

        candidates = self.db.query(NewsCandidate).filter(NewsCandidate.digest_id == digest.id).all()
        candidate_by_id = {c.id: c for c in candidates}
        digest_d = digest.date if isinstance(digest.date, date) else digest.date
        recent_top5_fps = query_recent_top5_url_fingerprints(
            self.db,
            digest_id=int(digest.id),
            digest_date=digest_d,
        )

        def _is_recent_top5_repeat(candidate: NewsCandidate) -> bool:
            fp = article_page_fingerprint(str(candidate.url or ""))
            return bool(fp and fp in recent_top5_fps)

        strict_allowed = [
            c
            for c in candidates
            if c.headline_editorial_ok
            and c.link_status
            and c.reliability_status != "❗ без подтверждения"
            and not c.is_aggregator
            and not c.is_duplicate
            and not _is_recent_top5_repeat(c)
        ]
        if len(strict_allowed) < 5:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Недостаточно подходящих кандидатов для пятёрки: {len(strict_allowed)} из 5 нужных "
                    f"(всего в пуле {len(candidates)}). Отметьте строки с «Можно в топ‑5» или пересоберите пул."
                ),
            )
        mandatory_manual = [
            c
            for c in strict_allowed
            if self._is_manual_required_candidate(c.verification_comment, c.description)
        ]
        if top5:
            chosen = self._pick_top5_with_host_cap(strict_allowed, mandatory_manual)
            if len(chosen) < 5:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        "Недостаточно проверенных кандидатов для топ-5 с учётом лимита "
                        f"не более {STEP2_MAX_PER_SOURCE} новостей с одного сайта. "
                        "Выберите пятёрку вручную или пересоберите пул."
                    ),
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
                if _is_recent_top5_repeat(c):
                    raise HTTPException(
                        status_code=400,
                        detail=(
                            "Нельзя включить в пятёрку статью, которая уже была в топ-5 "
                            "одного из последних выпусков. Выберите другую новость из пула."
                        ),
                    )
                if not c.headline_editorial_ok or not c.link_status:
                    raise HTTPException(
                        status_code=400,
                        detail="Можно выбирать только новости с читаемым заголовком и рабочей ссылкой на материал.",
                    )
            ok_hosts, bad_host = self._selection_respects_host_cap(chosen)
            if not ok_hosts:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"С одного сайта ({bad_host}) можно выбрать не более {STEP2_MAX_PER_SOURCE} новостей. "
                        "Снимите лишние галочки с этого домена."
                    ),
                )

        self.db.query(SelectedNews).filter(SelectedNews.digest_id == digest.id).delete()
        created: list[SelectedNews] = []
        pick_reason = "Топ-5 по рейтингу" if top5 else "Выбрано пользователем"
        for idx, c in enumerate(chosen, start=1):
            item = SelectedNews(
                digest_id=digest.id,
                candidate_id=c.id,
                original_number=c.original_number,
                output_position=idx,
                ordering_reason=pick_reason,
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

    def _fallback_order_rationale(
        self, agent_order: list[dict[str, Any]], order_payload: list[dict[str, Any]]
    ) -> str:
        reasons = [
            str(row.get("ordering_reason") or "").strip()
            for row in sorted(agent_order, key=lambda x: int(x.get("output_position") or 0))
            if str(row.get("ordering_reason") or "").strip()
        ]
        if reasons:
            return " ".join(reasons[:3])[:2000]
        return "Порядок выстроен так, чтобы удержать внимание: сильный заход, ритм в середине и запоминающийся финал."

    def run_step_2_order(self, digest_id: int, ordered_candidate_ids: list[int]) -> list[SelectedNews]:
        digest = self.get_digest(digest_id)
        if digest.status not in ORDER_STEP2_ALLOWED:
            raise HTTPException(status_code=400, detail="Ordering requires a confirmed top-5 selection")
        if digest.status != STATUS_SELECTED:
            self._prepare_for_reorder(digest)
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

        # "Применить порядок" должен строго сохранять порядок после drag-and-drop.
        # ИИ-перестановка выполняется только в run_step_2_order_ai_optimal.
        agent_order = [
            {
                "candidate_id": item["candidate_id"],
                "output_position": idx + 1,
                "ordering_reason": "Порядок задан редактором вручную.",
            }
            for idx, item in enumerate(order_payload)
        ]
        digest.step2_budget_capped = False
        for row in selected:
            for ord_item in agent_order:
                if ord_item["candidate_id"] == row.candidate_id:
                    row.output_position = int(ord_item["output_position"])
                    row.ordering_reason = str(ord_item["ordering_reason"])
        self._save_step2_order_rationale(
            digest.id,
            "Порядок задан вручную: редактор расставил новости после перетаскивания.",
        )
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
        if digest.status not in ORDER_STEP2_ALLOWED:
            raise HTTPException(status_code=400, detail="AI ordering requires a confirmed top-5 selection")
        if digest.status != STATUS_SELECTED:
            self._prepare_for_reorder(digest)
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

            set_proxyapi_log_context(
                DigestId=digest.id,
                DigestType=digest.digest_type or "unknown",
                Step="step2_order",
                RunType="ai_optimal_order",
            )
            agent_order_result = self.proxy.suggest_news_order(
                order_input,
                digest_type=digest.digest_type or "serious",
                model=STEP2_AI_ORDER_MODEL,
            )
            if isinstance(agent_order_result, dict):
                agent_order = agent_order_result.get("items") or []
                overall_rationale = str(agent_order_result.get("overall_rationale") or "").strip()
            else:
                agent_order = agent_order_result
                overall_rationale = ""
            digest.step2_budget_capped = False
        for row in selected:
            for ord_item in agent_order:
                if int(ord_item["candidate_id"]) == row.candidate_id:
                    row.output_position = int(ord_item["output_position"])
                    row.ordering_reason = str(ord_item["ordering_reason"])[:500]
        self._save_step2_order_rationale(
            digest.id,
            overall_rationale or self._fallback_order_rationale(agent_order, order_input),
        )
        self.db.commit()
        ordered = (
            self.db.query(SelectedNews).filter(SelectedNews.digest_id == digest.id).order_by(SelectedNews.output_position).all()
        )
        logger.info(
            "Шаг 2: AI-оптимальный порядок | digest_id=%s positions=%s",
            digest.id,
            [(r.candidate_id, r.output_position) for r in ordered],
        )
        # После AI-оптимизации остаёмся на шаге 2: редактор должен подтвердить порядок вручную.
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
                        "category": candidate.category,
                        "source_description": sanitize_reader_description(candidate.description)[:900],
                        "article_excerpt": str(candidate.article_excerpt or "")[:4000],
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

    def _resolve_article_excerpt(self, candidate: NewsCandidate) -> str:
        excerpt = str(getattr(candidate, "article_excerpt", None) or "").strip()
        if len(excerpt) >= 120:
            return excerpt[:4000]
        if not candidate.url.startswith("http"):
            return excerpt[:4000]
        bundle = _fetch_article_page_bundle(candidate.url)
        if bundle.get("ok"):
            corpus = _truncate_article_excerpt(str(bundle.get("topic_corpus") or ""))
            if corpus:
                candidate.article_excerpt = corpus
                self.db.commit()
                return corpus
        return excerpt[:4000]

    def _step4_reader_payload(self, digest: Digest) -> list[dict[str, Any]]:
        analytics_rows = self.db.query(Analytics).filter(Analytics.digest_id == digest.id).all()
        selected_rows = (
            self.db.query(SelectedNews)
            .filter(SelectedNews.digest_id == digest.id)
            .order_by(SelectedNews.output_position.asc())
            .all()
        )
        reader_items: list[dict[str, Any]] = []
        for row in selected_rows:
            candidate = self.db.query(NewsCandidate).filter(NewsCandidate.id == row.candidate_id).first()
            analytics = next((a for a in analytics_rows if a.candidate_id == row.candidate_id), None)
            if not candidate or not analytics:
                continue
            reader_items.append(
                {
                    "candidate_id": candidate.id,
                    "title": candidate.title,
                    "essence": sanitize_reader_description(str(analytics.essence or "")),
                    "analysis": sanitize_reader_description(str(analytics.analysis or "")),
                    "article_excerpt": self._resolve_article_excerpt(candidate),
                }
            )
        return reader_items

    def _step4_generate_reader_texts(self, digest: Digest) -> None:
        reader_items = self._step4_reader_payload(digest)
        if not reader_items:
            raise HTTPException(status_code=400, detail="Нет выбранных новостей с аналитикой для текстов читателя")
        logger.info("Шаг 4: тексты для читателя | digest_id=%s count=%s", digest.id, len(reader_items))
        with self._digest_cost_session(
            digest,
            step="step_4",
            agent_name="ReaderCopyAgent",
            request_label="step_4_reader_texts",
            model=AGENT_MODEL_RECOMMENDATIONS["ReaderCopyAgent"],
        ):
            result = self.workflow.run_reader_descriptions(reader_items)
        by_id = {int(x["candidate_id"]): x for x in result.get("items", []) if isinstance(x, dict)}
        for row in self.db.query(Analytics).filter(Analytics.digest_id == digest.id).all():
            item = by_id.get(row.candidate_id)
            if item:
                row.reader_text = str(item.get("reader_text") or "")
        self.db.commit()

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
            essence = sanitize_reader_description(str(analytics.essence or ""))
            analysis = sanitize_reader_description(str(analytics.analysis or ""))
            reader_text = sanitize_reader_description(str(getattr(analytics, "reader_text", None) or ""))
            summary_short = build_platform_description(
                reader_text,
                essence=essence,
                analysis=analysis,
            )
            selected_payload.append(
                {
                    "title": candidate.title,
                    "url": candidate.url,
                    "source": candidate.source,
                    "essence": essence,
                    "reader_text": reader_text,
                    "summary_short": summary_short,
                    "summary": analysis or summary_short,
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
        self._step4_generate_reader_texts(digest)
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
            "digest_type": digest.digest_type or "serious",
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
                    "digest_type": digest.digest_type or "serious",
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

    def _merge_step1_seed_urls(self, manual_urls: list[str], telegram_urls: list[str]) -> list[str]:
        """Ручные URL первыми, затем ссылки из TG-постов (без дублей)."""
        out: list[str] = []
        seen: set[str] = set()
        for url in [*manual_urls, *telegram_urls]:
            value = (url or "").strip()
            if not value or value in seen:
                continue
            seen.add(value)
            out.append(value)
        return out

    def _normalize_seed_urls(self, urls: list[str]) -> list[str]:
        cap = max(1, int(getattr(self.settings, "step1_seed_urls_max", 35) or 35))
        cleaned: list[str] = []
        seen: set[str] = set()
        for url in urls:
            value = url.strip()
            if not value or value in seen:
                continue
            seen.add(value)
            cleaned.append(value)
        return cleaned[:cap]

    def _normalize_manual_urls(self, manual_urls: list[str]) -> list[str]:
        """Обратная совместимость: только ручной ввод с прежним лимитом 10."""
        return self._normalize_seed_urls(manual_urls)[:10]

    def _collect_previous_user_manual_urls(self, digest_id: int) -> list[str]:
        """Ручные URL из сохранённого пула — для пересборки без повторного ввода в поле."""
        rows = (
            self.db.query(NewsCandidate)
            .filter(NewsCandidate.digest_id == digest_id)
            .order_by(NewsCandidate.original_number.asc(), NewsCandidate.id.asc())
            .all()
        )
        urls: list[str] = []
        for row in rows:
            comment = str(row.verification_comment or "")
            desc = str(row.description or "")
            if "TELEGRAM_SEED:" in comment or "Telegram-монитор" in desc:
                continue
            if "MANUAL_REQUIRED:" in comment or self._is_manual_required_candidate(comment, desc):
                value = str(row.url or "").strip()
                if value:
                    urls.append(value)
        return self._normalize_manual_urls(urls)

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
                    max_completion_tokens=120,
                    log_source="step1_title_translation",
                )

            translated = do()
            line = translated.strip().splitlines()[0].strip().strip("'\"«»„“")
            if len(line) >= 6:
                return line[:500]
            return t[:500]
        except Exception:
            logger.warning("Перевод заголовка пропущен (ошибка API) | url=%s", url[:100], exc_info=True)
            return t[:500]

    def _manual_required_candidate_row(
        self,
        digest: Digest,
        stored: str,
        bundle: dict[str, Any],
        *,
        original_number: int,
        manual_step: int,
        listing_seed: str | None = None,
    ) -> dict[str, Any]:
        host = _host_from_url(stored)
        comment = "MANUAL_REQUIRED: добавлено пользователем"
        raw_headline = bundle.get("headline")
        if raw_headline:
            title = self._ensure_russian_candidate_title(digest.id, stored, str(raw_headline))
        else:
            title = f"Статья по ссылке ({host})"
        if listing_seed:
            desc = (
                f"Вставлено в поле URL на шаге {manual_step}; извлечено из ленты {listing_seed[:120]}. "
                "Материал обязателен к использованию в выпуске."
            )
        else:
            desc = (
                f"Вставлено в поле URL на шаге {manual_step}; материал обязателен к использованию в выпуске. "
                "Остальные фильтры шага 1 для ручной ссылки не применяются — страница открывается и это статья."
            )
        pub_fields: dict[str, Any] = {}
        _apply_bundle_published_at(pub_fields, bundle, seed_url=stored)
        published_at = str(pub_fields.get("published_at") or PUBLISHED_AT_UNDEFINED)
        if _is_foreign_agent_source(stored):
            comment = f"{comment} МАРКИРОВКА: ИНОСТРАННЫЙ_АГЕНТ"
        policy_fields: dict[str, Any] = {}
        _apply_source_policy_from_url(policy_fields, stored)
        topic_corpus = str(bundle.get("topic_corpus") or "")
        candidate_row: dict[str, Any] = {
            "original_number": original_number,
            "title": title[:500],
            "url": stored[:1000],
            "source": host,
            "tier": policy_fields.get("tier", "Tier-3"),
            "published_at": published_at[:100],
            "category": "manual",
            "description": desc,
            "significance_score": 3,
            "novelty_score": 3,
            "impact_score": 3,
            "total_score": 9,
            "reliability_status": policy_fields.get("reliability_status", "✅ подтверждено"),
            "is_foreign_agent": _is_foreign_agent_source(stored),
            "is_aggregator": policy_fields.get("is_aggregator", False),
            "is_duplicate": False,
            "verification_comment": comment,
            "link_status": True,
            "headline_editorial_ok": True,
            "page_verified": True,
        }
        if topic_corpus:
            candidate_row["article_excerpt"] = _truncate_article_excerpt(topic_corpus)
        return candidate_row

    def _build_manual_candidates(
        self,
        digest: Digest,
        manual_urls: list[str],
        now_msk: str,
        *,
        mandatory: bool = True,
        manual_step: int = 1,
    ) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for idx, url in enumerate(manual_urls, start=1):
            bundle = _fetch_article_page_bundle(url)
            if bundle.get("ok") and (bundle.get("final_url") or bundle.get("display_url")):
                stored = str(bundle.get("final_url") or bundle.get("display_url") or url).strip()
            else:
                stored = url.strip()
            host = _host_from_url(stored)
            link_ok = bool(bundle.get("ok"))

            if mandatory:
                comment = "MANUAL_REQUIRED: добавлено пользователем"
                if not link_ok:
                    title = f"Статья по ссылке ({host})"
                    desc = (
                        "Страница по ссылке не открылась — проверьте URL в браузере и вставьте снова."
                    )
                    comment = f"{comment} {REJECT_REASON_PREFIX}seed_unverified"
                    logger.warning("Seed URL: страница не открылась | url=%s", stored[:120])
                    policy_fields: dict[str, Any] = {}
                    _apply_source_policy_from_url(policy_fields, stored)
                    result.append(
                        {
                            "original_number": idx,
                            "title": title,
                            "url": stored[:1000],
                            "source": host,
                            "tier": policy_fields.get("tier", "Tier-3"),
                            "published_at": PUBLISHED_AT_UNDEFINED,
                            "category": "manual",
                            "description": desc,
                            "significance_score": 3,
                            "novelty_score": 3,
                            "impact_score": 3,
                            "total_score": 9,
                            "reliability_status": policy_fields.get("reliability_status", "⚠️ сомнительный"),
                            "is_foreign_agent": _is_foreign_agent_source(stored),
                            "is_aggregator": policy_fields.get("is_aggregator", False),
                            "is_duplicate": False,
                            "verification_comment": comment,
                            "link_status": False,
                            "headline_editorial_ok": False,
                            "page_verified": False,
                        }
                    )
                    continue

                if _manual_seed_page_is_listing(stored, bundle):
                    title = f"Статья по ссылке ({host})"
                    desc = (
                        "Указана страница ленты/рубрики, а не отдельная новость. "
                        "Вставьте прямую ссылку на материал (как в адресной строке браузера на статье)."
                    )
                    comment = f"{comment} {REJECT_REASON_PREFIX}news_listing_page"
                    policy_fields = {}
                    _apply_source_policy_from_url(policy_fields, stored)
                    result.append(
                        {
                            "original_number": idx,
                            "title": title,
                            "url": stored[:1000],
                            "source": host,
                            "tier": policy_fields.get("tier", "Tier-3"),
                            "published_at": PUBLISHED_AT_UNDEFINED,
                            "category": "manual",
                            "description": desc,
                            "significance_score": 3,
                            "novelty_score": 3,
                            "impact_score": 3,
                            "total_score": 9,
                            "reliability_status": policy_fields.get("reliability_status", "⚠️ сомнительный"),
                            "is_foreign_agent": _is_foreign_agent_source(stored),
                            "is_aggregator": policy_fields.get("is_aggregator", False),
                            "is_duplicate": False,
                            "verification_comment": comment,
                            "link_status": False,
                            "headline_editorial_ok": False,
                            "page_verified": False,
                        }
                    )
                    continue

                if not _manual_seed_page_is_article(stored, bundle):
                    title = f"Статья по ссылке ({host})"
                    desc = (
                        "Страница открывается, но это не отдельная статья (раздел, лендинг или сервис). "
                        "Вставьте прямую ссылку на публикацию с заголовком новости."
                    )
                    comment = f"{comment} {REJECT_REASON_PREFIX}non_article_page"
                    policy_fields = {}
                    _apply_source_policy_from_url(policy_fields, stored)
                    result.append(
                        {
                            "original_number": idx,
                            "title": title,
                            "url": stored[:1000],
                            "source": host,
                            "tier": policy_fields.get("tier", "Tier-3"),
                            "published_at": PUBLISHED_AT_UNDEFINED,
                            "category": "manual",
                            "description": desc,
                            "significance_score": 3,
                            "novelty_score": 3,
                            "impact_score": 3,
                            "total_score": 9,
                            "reliability_status": policy_fields.get("reliability_status", "⚠️ сомнительный"),
                            "is_foreign_agent": _is_foreign_agent_source(stored),
                            "is_aggregator": policy_fields.get("is_aggregator", False),
                            "is_duplicate": False,
                            "verification_comment": comment,
                            "link_status": False,
                            "headline_editorial_ok": False,
                            "page_verified": False,
                        }
                    )
                    continue

                result.append(
                    self._manual_required_candidate_row(
                        digest,
                        stored,
                        bundle,
                        original_number=idx,
                        manual_step=manual_step,
                    )
                )
                row = result[-1]
                corpus = f"{row.get('title', '')} {bundle.get('topic_corpus', '')}"
                _apply_material_form_to_candidate(row, extra=corpus, digest_type=digest.digest_type)
                continue

            raw_headline = bundle.get("headline") if bundle.get("ok") else None
            manual_reject: str | None = None
            pre_reason = search_url_prefilter_reason(
                stored,
                is_enabled=self._is_step1_filter_enabled,
                tier_strict=bool(getattr(self.settings, "step1_tier_strict_search", True)),
                curious_strict=is_curious_digest(digest.digest_type),
                earliest=digest_earliest_news_date(digest),
                anchor=digest_news_anchor_date(digest),
            )
            if pre_reason == "news_listing_page":
                manual_reject = "news_listing_page"
            if (
                bundle.get("ok")
                and self._is_step1_filter_enabled("news_listing_page")
                and (bool(bundle.get("is_listing_page")) or is_topic_pool_page_url(stored) or is_listing_page_url(stored))
            ):
                manual_reject = "news_listing_page"
                raw_headline = None
                title = f"Статья по ссылке ({host})"
                desc = (
                    "Указана страница ленты/рубрики, а не отдельная новость. "
                    "Подставьте прямую ссылку на статью или оставьте поле пустым — "
                    "шаг 1 попробует извлечь новости из лент автоматически."
                )
            if raw_headline:
                title = self._ensure_russian_candidate_title(digest.id, stored, raw_headline)
                if (
                    self._is_step1_filter_enabled("headline_low_quality")
                    and (_editorial_headline_rejected(title) or _editorial_headline_rejected(str(raw_headline)))
                ):
                    manual_reject = "headline_low_quality"
                    raw_headline = None
                    title = f"Статья по ссылке ({host})"
                    desc = (
                        "Заголовок на странице выглядит как технический идентификатор (номер документа), "
                        "а не как заголовок новости — такая ссылка не принимается как подтверждённая. "
                        "Откройте материал в браузере и вставьте URL страницы с нормальным заголовком в разметке."
                    )
                    logger.warning("Seed URL: отклонён технический заголовок | url=%s", stored[:120])
                elif self._is_step1_filter_enabled("off_topic_not_ai") and not _ai_digest_topic_matches(
                    str(bundle.get("topic_corpus") or ""), str(raw_headline)
                ):
                    manual_reject = "off_topic_not_ai"
                    raw_headline = None
                    title = f"Статья по ссылке ({host})"
                    desc = (
                        "Страница не относится к теме искусственного интеллекта и нейросетей — "
                        "в дайджест такой материал не берётся. Укажите ссылку на статью про ИИ/ML."
                    )
                    logger.warning("Seed URL: вне темы ИИ | url=%s", stored[:120])
                else:
                    if mandatory:
                        desc = (
                            f"Вставлено в поле URL на шаге {manual_step}; материал обязателен к использованию в выпуске. "
                            "Заголовок извлечён со страницы по ссылке; при необходимости показан перевод на русский."
                        )
                    else:
                        desc = (
                            "Собрано Telegram-монитором (канал из pipeline_settings.json). "
                            "Заголовок извлечён со страницы по ссылке; при необходимости показан перевод на русский."
                        )
                    logger.info(
                        "Seed URL: заголовок извлечён | url=%s raw_len=%s title_len=%s",
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
                logger.warning("Seed URL: заголовок не извлечён | url=%s", stored[:120])
            # Для ручных URL используем тот же результат GET, что и для LLM-кандидатов (без отдельного HEAD).
            pub_fields: dict[str, Any] = {}
            _apply_bundle_published_at(pub_fields, bundle if bundle.get("ok") else {}, seed_url=url)
            published_at = str(pub_fields.get("published_at") or PUBLISHED_AT_UNDEFINED)
            if not manual_reject:
                date_reject = _published_at_window_reject_code(digest, published_at, stored, seed_url=url)
                if date_reject and self._is_step1_filter_enabled(date_reject):
                    manual_reject = date_reject
                    raw_headline = None
                    title = f"Статья по ссылке ({host})"
                    if manual_reject == "published_date_undefined":
                        desc = (
                            "Не удалось определить дату публикации на странице. "
                            "Укажите прямую ссылку на статью с датой в разметке или отключите фильтр «Дата не определена»."
                        )
                    else:
                        desc = (
                            f"Дата публикации материала раньше допустимого окна (с {digest_earliest_news_date(digest).isoformat()} "
                            f"по {digest_news_anchor_date(digest).isoformat()}). Укажите более свежую статью."
                        )
            link_ok = bool(bundle.get("ok"))
            headline_editorial_ok = bool(link_ok and raw_headline and not manual_reject)
            page_verified = headline_editorial_ok and link_ok
            comment = (
                "MANUAL_REQUIRED: добавлено пользователем"
                if mandatory
                else "TELEGRAM_SEED: ссылка из мониторинга канала"
            )
            if manual_reject:
                comment = f"{comment} {REJECT_REASON_PREFIX}{manual_reject}"
            if not page_verified:
                comment = f"{comment} {REJECT_REASON_PREFIX}seed_unverified"
            if _is_foreign_agent_source(stored):
                comment = f"{comment} МАРКИРОВКА: ИНОСТРАННЫЙ_АГЕНТ"
            policy_fields: dict[str, Any] = {}
            _apply_source_policy_from_url(policy_fields, stored)
            candidate_row: dict[str, Any] = {
                "original_number": idx,
                "title": title,
                "url": stored[:1000],
                "source": host,
                "tier": policy_fields["tier"],
                "published_at": published_at[:100],
                "category": "manual" if mandatory else "telegram_seed",
                "description": desc,
                "significance_score": 3,
                "novelty_score": 3,
                "impact_score": 3,
                "total_score": 9,
                "reliability_status": policy_fields["reliability_status"],
                "is_foreign_agent": _is_foreign_agent_source(stored),
                "is_aggregator": policy_fields["is_aggregator"],
                "is_duplicate": False,
                "verification_comment": comment,
                "link_status": link_ok,
                "headline_editorial_ok": headline_editorial_ok,
                "page_verified": page_verified,
            }
            topic_corpus = str(bundle.get("topic_corpus") or "")
            if page_verified and headline_editorial_ok:
                candidate_row["article_excerpt"] = _truncate_article_excerpt(topic_corpus)
                promo_corpus = f"{title} {desc} {topic_corpus}"
                _apply_material_form_to_candidate(candidate_row, extra=promo_corpus, digest_type=digest.digest_type)
                if is_curious_digest(digest.digest_type) and self._is_step1_filter_enabled(
                    "off_topic_not_curious"
                ):
                    tone = curious_tone_score(title, topic_corpus)
                    candidate_row["curious_tone_score"] = tone
                    if not passes_curious_pool_gate(title, topic_corpus):
                        manual_reject = "off_topic_not_curious"
                        headline_editorial_ok = False
                        page_verified = False
                        candidate_row["headline_editorial_ok"] = False
                        candidate_row["link_status"] = link_ok
                        candidate_row["page_verified"] = False
                        comment = f"{comment} {REJECT_REASON_PREFIX}{manual_reject}"
                        candidate_row["verification_comment"] = comment
            result.append(candidate_row)
        return result

    def _skeleton_dict_from_search_url(self, url: str, now_msk: str, seq: int) -> dict[str, Any]:
        host = _host_from_url(url)
        tier, is_aggregator, reliability_status = _classify_source_policy(url)
        comment = "Источник из веб-поиска; проверка страницы обязательна."
        if _is_foreign_agent_source(url):
            comment += " МАРКИРОВКА: ИНОСТРАННЫЙ_АГЕНТ"
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
            "is_foreign_agent": _is_foreign_agent_source(url),
            "is_aggregator": is_aggregator,
            "is_duplicate": False,
            "verification_comment": comment,
            "link_status": True,
            "headline_editorial_ok": False,
            "page_verified": False,
        }

    def _verified_dict_for_crew_enrich(self, item: dict[str, Any], seq: int) -> dict[str, Any]:
        excerpt = _candidate_article_excerpt(item) or str(item.get("description") or "")
        return {
            "original_number": seq,
            "title": str(item.get("title") or "")[:500],
            "url": str(item.get("url") or "")[:1000],
            "source": str(item.get("source") or "")[:255],
            "tier": str(item.get("tier") or "Tier-3")[:32],
            "published_at": str(item.get("published_at") or "")[:100],
            "category": str(item.get("category") or "technology")[:120],
            "description": str(item.get("description") or "")[:900],
            "article_excerpt": excerpt[:1200],
            "significance_score": int(item.get("significance_score", 2) or 2),
            "novelty_score": int(item.get("novelty_score", 2) or 2),
            "impact_score": int(item.get("impact_score", 2) or 2),
            "total_score": int(item.get("total_score", 6) or 6),
            "reliability_status": str(item.get("reliability_status") or "✅"),
            "link_status": True,
            "headline_editorial_ok": True,
            "page_verified": True,
            "is_aggregator": bool(item.get("is_aggregator", False)),
            "is_duplicate": bool(item.get("is_duplicate", False)),
            "is_foreign_agent": bool(item.get("is_foreign_agent", False)),
        }

    def _step1_crew_enrich_verified_pool(
        self,
        digest: Digest,
        verified_pool: list[dict[str, Any]],
        now_msk: str,
        *,
        filter_enabled: Any | None = None,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """Вариант B: один LLM-проход ScoringAgent по уже HTTP-проверенным кандидатам."""
        meta: dict[str, Any] = {"enabled": False, "items_in": 0, "items_enriched": 0}
        if not bool(getattr(self.settings, "step1_crew_enrich_verified_scores", False)):
            meta["skipped"] = "disabled"
            return verified_pool, meta
        min_items = max(1, int(getattr(self.settings, "step1_crew_enrich_min_verified", 1) or 1))
        max_items = max(1, int(getattr(self.settings, "step1_crew_enrich_max_items", 12) or 12))
        eligible = [
            item
            for item in verified_pool
            if item.get("headline_editorial_ok")
            and item.get("link_status")
            and not _manual_required_dict(item)
        ]
        if len(eligible) < min_items:
            meta["skipped"] = "too_few_eligible"
            return verified_pool, meta
        to_enrich = sorted(eligible, key=lambda x: int(x.get("total_score", 0) or 0), reverse=True)[:max_items]
        rows = [self._verified_dict_for_crew_enrich(item, idx) for idx, item in enumerate(to_enrich, start=1)]
        meta["enabled"] = True
        meta["items_in"] = len(rows)
        try:
            with step1_phase("crew_enrich"):
                scored_rows = self.workflow.run_verified_pool_score_enrichment(
                    rows,
                    now_msk=now_msk,
                    digest_type=digest.digest_type or "serious",
                )
            merged_rows, rejected_rows = self._filter_score_url_mutations(
                rows,
                scored_rows,
                filter_enabled=filter_enabled,
            )
        except Exception:
            logger.exception(
                "Шаг 1: Crew enrich | digest_id=%s items=%s",
                digest.id,
                len(rows),
            )
            meta["skipped"] = "error"
            return verified_pool, meta
        meta["items_rejected"] = len(rejected_rows)
        enrich_by_fp: dict[str, dict[str, Any]] = {}
        for row in merged_rows:
            fp = _url_fingerprint(str(row.get("url") or ""))
            if fp:
                enrich_by_fp[fp] = row
        curious_mode = is_curious_digest(digest.digest_type)
        enriched_count = 0
        for item in verified_pool:
            fp = _url_fingerprint(str(item.get("url") or ""))
            if not fp or fp not in enrich_by_fp:
                continue
            patch = enrich_by_fp[fp]
            if curious_mode:
                desc = str(patch.get("description") or "").strip()
                if desc:
                    item["description"] = desc[:900]
                for key in ("significance_score", "novelty_score", "impact_score", "category"):
                    if key in patch and patch[key] is not None:
                        item[key] = patch[key]
                tone = int(item.get("curious_tone_score") or 0)
                item["total_score"] = curious_total_score_from_tone(
                    tone,
                    low=bool(item.get("curious_tone_low")),
                )
            else:
                for key in (
                    "significance_score",
                    "novelty_score",
                    "impact_score",
                    "total_score",
                    "description",
                    "category",
                ):
                    if key in patch and patch[key] is not None:
                        item[key] = patch[key]
            enriched_count += 1
        meta["items_enriched"] = enriched_count
        logger.info(
            "Шаг 1: Crew enrich проверенного пула | digest_id=%s in=%s enriched=%s rejected=%s",
            digest.id,
            meta["items_in"],
            enriched_count,
            meta.get("items_rejected", 0),
        )
        return verified_pool, meta

    def _prefilter_llm_candidates_fetchable(
        self,
        digest_id: int,
        rows: list[dict[str, Any]],
        *,
        filter_enabled: Any | None = None,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Отсекаем URL, которые LLM придумал: страница не открывается по HTTP (с разворотом лент)."""
        is_enabled = filter_enabled or _catalog_step1_filter_enabled
        kept: list[dict[str, Any]] = []
        dropped: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            u = str(item.get("url") or "").strip()
            if not u.startswith("http") and is_enabled("invalid_url"):
                _append_reject_reason(item, "invalid_url")
                dropped.append(item)
                continue
            if url_suspected_hallucinated(u) and is_enabled("llm_hallucinated_url"):
                _append_reject_reason(item, "llm_hallucinated_url")
                item["link_status"] = False
                dropped.append(item)
                continue
            resolved_ok = False
            for resolved_url, bundle in _expand_listing_url_candidates(u, max_children=_listing_max_children(u)):
                if not bundle.get("ok"):
                    continue
                item["url"] = resolved_url[:1000]
                resolved_ok = True
                break
            if not resolved_ok and is_enabled("http_unreachable"):
                _append_reject_reason(item, "http_unreachable")
                item["link_status"] = False
                dropped.append(item)
                continue
            kept.append(item)
        return kept, dropped

    def _step1_search_query_parts(self, digest: Digest) -> tuple[str, str, str]:
        """(окно дат, тематика, исключения промо) для web-поиска."""
        earliest = digest_earliest_news_date(digest)
        days = int(digest.news_window_days or 3)
        kind = _normalize_news_window_day_kind(digest.news_window_day_kind)
        kind_ru = "рабочих" if kind == "working" else "календарных"
        anchor = digest_news_anchor_date(digest)
        pub_year = anchor.year
        window_hint = (
            f"after:{earliest.isoformat()} before:{anchor.isoformat()} "
            f"Только материалы с датой публикации не ранее {earliest.isoformat()} "
            f"и не позже {anchor.isoformat()} "
            f"(окно: {days} {kind_ru} дней от даты выпуска {anchor}). "
            f"Год публикации: {pub_year}. "
            "Приоритет — публикации за последние 1–3 дня. "
        )
        product_excludes = step1_product_excludes_for_digest_type(digest.digest_type)
        topic_terms = step1_topic_terms_for_digest_type(digest.digest_type)
        return window_hint, topic_terms, product_excludes

    def _step1_search_query(self, digest: Digest) -> str:
        window_hint, topic_terms, product_excludes = self._step1_search_query_parts(digest)
        if is_curious_digest(digest.digest_type):
            source_hint = self._step1_curious_source_seed_hint()
        else:
            source_hint = self._step1_source_seed_hint()
        return window_hint + source_hint + topic_terms + product_excludes

    def _step1_curious_source_seed_hint(self, max_urls: int = 24) -> str:
        policy = get_curious_source_policy()
        seeds = expand_search_seed_urls(
            policy.search_seed_urls,
            settings=self.settings,
            digest_type="curious",
        )
        http_seeds = [u for u in seeds if u.startswith(("http://", "https://"))]
        if not http_seeds:
            return ""
        primary = http_seeds[:max_urls]
        return (
            "Сначала ищи в развлекательных и курьёзных разделах из curious_source_hosts: "
            + ", ".join(primary)
            + ". Приоритет — забавные, вирусные и неожиданные истории про ИИ; "
            "не возвращай сухие пресс-релизы, регуляторику и «компания представила модель». "
        )

    def _step1_source_seed_hint(self, max_urls: int = 30) -> str:
        policy = get_source_tiers_policy(self.settings.source_tiers_path)
        seeds = expand_search_seed_urls(
            policy.search_seed_urls,
            settings=self.settings,
            digest_type="serious",
        )
        http_seeds = [u for u in seeds if u.startswith(("http://", "https://"))]
        if not http_seeds:
            return ""
        primary = http_seeds[:max_urls]
        return (
            "Сначала ищи в проверенных AI-разделах и агрегаторах (Tier-2) из tier-файла: "
            + ", ".join(primary)
            + ". Агрегаторы (Google News, Reddit, Yandex News и т.п.) — для разведки сюжета; "
            "в ответе только прямые URL статей первоисточника, не ленты и не поисковые страницы агрегаторов. "
        )

    def _step1_prioritize_search_urls(self, urls: list[str], digest: Digest | None = None) -> list[str]:
        """Сортирует URL перед HTTP: serious — tier+дата; curious — entertainment или tier+дата (гибрид)."""
        if digest is not None and is_curious_digest(digest.digest_type):
            if not bool(getattr(self.settings, "step1_curious_use_serious_tiers", False)):
                return self._step1_prioritize_curious_search_urls(urls, digest)
        from app.source_tiers_policy import tier2_search_host_markers

        policy = get_source_tiers_policy(self.settings.source_tiers_path)
        tier_groups = (
            (0, policy.tier1_hosts),
            (1, tier2_search_host_markers(policy)),
            (2, policy.tier3_hosts),
            (3, policy.tier4_hosts),
        )
        earliest = digest_earliest_news_date(digest) if digest is not None else None
        seen: set[str] = set()
        unique: list[str] = []
        for u in urls:
            norm = str(u or "").strip()
            if not norm or norm in seen:
                continue
            seen.add(norm)
            unique.append(norm)

        def _rank(url: str) -> tuple[int, int, int]:
            host = _host_from_url(url).lower()
            tier_pri = 9
            for pri, markers in tier_groups:
                if any(m in host for m in markers):
                    tier_pri = pri
                    break
            if earliest is None:
                return (1, tier_pri, 0)
            pub_day = _url_path_publication_day(url)
            if pub_day is not None:
                if pub_day < earliest:
                    date_pri = 2
                else:
                    date_pri = 0
            else:
                date_pri = 1
            return (date_pri, tier_pri, 0)

        return sorted(unique, key=_rank)

    def _step1_prioritize_curious_search_urls(self, urls: list[str], digest: Digest | None = None) -> list[str]:
        """Курьёзный выпуск: entertainment/foreign выше tech; без tier-логики source_tiers."""
        policy = get_curious_source_policy()
        earliest = digest_earliest_news_date(digest) if digest is not None else None
        anchor = digest_news_anchor_date(digest) if digest is not None else None
        seen: set[str] = set()
        unique: list[str] = []
        for u in urls:
            norm = str(u or "").strip()
            if not norm or norm in seen:
                continue
            if earliest is not None and search_url_path_date_outside_window(
                norm, earliest=earliest, anchor=anchor
            ):
                continue
            seen.add(norm)
            unique.append(norm)

        def _rank(url: str) -> tuple[int, int, int, int, int]:
            host = _host_from_url(url).lower()
            pub_day = _url_path_publication_day(url)
            if earliest is not None and pub_day is not None:
                date_pri = 2 if pub_day < earliest else 0
            elif pub_day is not None:
                date_pri = 0
            elif any(h in host for h in (*policy.curious_tier1_hosts, *policy.curious_tier2_hosts)):
                date_pri = 0
            else:
                date_pri = 1
            if any(h in host for h in policy.curious_tier1_hosts):
                source_pri = 0
            elif any(h in host for h in policy.curious_tier2_hosts):
                source_pri = 1
            else:
                source_pri = 2
            return curious_raw_url_rank_key(
                url,
                fresh_rank=date_pri,
                source_rank=source_pri,
                day_ordinal=pub_day.toordinal() if pub_day else None,
            )

        return sorted(unique, key=_rank)

    def _step1_fresh_tier1_query(self, digest: Digest) -> str:
        """Узкий запрос по tier-1 с оператором after: — когда основная выдача даёт старые URL."""
        earliest = digest_earliest_news_date(digest)
        if is_curious_digest(digest.digest_type):
            policy = get_curious_source_policy()
            ru_ent = " OR ".join(f"site:{h}" for h in policy.curious_ru_entertainment_hosts[:5])
            foreign = " OR ".join(f"site:{h}" for h in policy.curious_foreign_hosts[:5])
            ru_tech = " OR ".join(f"site:{h}" for h in policy.curious_ru_tech_hosts[:3])
            return (
                f"after:{earliest.isoformat()} "
                "ИИ нейросеть чат-бот фейл глюк сломал странный кейс пользователи жалуются вирусный мем кринж "
                f"({ru_ent}) OR ({foreign}) OR ({ru_tech}) "
                "-регулирование -инвестиции -партнёрство -press -release -pricing -demo -careers"
            )
        return (
            f"after:{earliest.isoformat()} "
            "искусственный интеллект нейросети машинное обучение "
            "site:ria.ru OR site:interfax.ru OR site:vedomosti.ru OR site:habr.com OR site:vc.ru"
        )

    def _step1_fresh_breaking_query(self, digest: Digest) -> str:
        """Усиленный запрос на свежие публикации внутри окна (без ослабления дат-фильтра)."""
        earliest = digest_earliest_news_date(digest)
        anchor = digest_news_anchor_date(digest)
        return (
            f"after:{earliest.isoformat()} before:{anchor.isoformat()} "
            "\"искусственный интеллект\" OR \"генеративный ИИ\" OR \"нейросеть\" OR LLM "
            "новость OR сегодня OR вчера OR \"последние сутки\" OR \"breaking\" "
            "site:ria.ru OR site:interfax.ru OR site:vedomosti.ru OR site:habr.com OR site:vc.ru "
            "-archive -tag -tags -opinion -column -vacancy -jobs -events -digest"
        )

    def _step1_research_science_query(self, digest: Digest) -> str:
        """Прорывы, исследования и научные публикации про ИИ."""
        earliest = digest_earliest_news_date(digest)
        return (
            f"after:{earliest.isoformat()} "
            "\"искусственный интеллект\" OR \"нейросеть\" OR LLM OR \"машинное обучение\" "
            "прорыв OR исследование OR \"научные данные\" OR \"научная работа\" OR benchmark OR paper OR study "
            "site:habr.com OR site:nplus1.ru OR site:indicator.ru OR site:sciencedaily.com OR site:techxplore.com "
            "OR site:spectrum.ieee.org OR site:technologyreview.com OR site:openai.com OR site:deepmind.google "
            "-course -курс -обучение -webinar -pricing -demo -trial -signup -vacancy"
        )

    def _step1_curious_angles_query(self, digest: Digest) -> str:
        """Доп. запрос для курьёзного выпуска: забавные/неожиданные углы (не пресс-релизы)."""
        earliest = digest_earliest_news_date(digest)
        policy = get_curious_source_policy()
        ru_sites = " OR ".join(
            f"site:{h}"
            for h in (
                *policy.curious_ru_entertainment_hosts[:6],
                *policy.curious_ru_tech_hosts[:3],
            )
        )
        return (
            f"after:{earliest.isoformat()} "
            f"{step1_curious_entertainment_anchor_ru()} "
            f"(нейросеть OR ИИ OR чат-бот OR ChatGPT) "
            + step1_topic_terms_for_digest_type(DIGEST_TYPE_CURIOUS)
            + f" ({ru_sites}) "
            + " -regulation -investment -partnership -\"press release\" -earnings "
            "-pricing -demo -careers -webinar -обзор -анализ -\"новая версия\""
        )

    def _step1_curious_human_stories_query(self, digest: Digest) -> str:
        """Курьёзный угол: реакции людей, жалобы, странные бытовые истории вокруг ИИ."""
        earliest = digest_earliest_news_date(digest)
        policy = get_curious_source_policy()
        ru_ent = " OR ".join(f"site:{h}" for h in policy.curious_ru_entertainment_hosts[:6])
        ru_tech = " OR ".join(f"site:{h}" for h in policy.curious_ru_tech_hosts[:3])
        foreign = " OR ".join(f"site:{h}" for h in policy.curious_foreign_hosts[:6])
        return (
            f"after:{earliest.isoformat()} "
            f"{step1_curious_entertainment_anchor_ru()} "
            "ИИ OR нейросеть OR чат-бот OR ChatGPT OR Gemini "
            "пользователи жалуются OR недовольны OR сломал OR удалил код OR странный кейс OR фейл OR мем OR вирусный OR кринж OR угар "
            f"{step1_curious_entertainment_anchor_en()} "
            "AI users complained OR AI agent deleted code OR weird chatbot OR AI fail OR Reddit "
            f"({ru_ent}) OR ({ru_tech}) OR ({foreign}) "
            "-regulation -investment -partnership -earnings -press -release -pricing -demo -careers -overview -explainer"
        )

    def _step1_curious_viral_query(self, digest: Digest) -> str:
        """Второй угол курьёзного поиска: viral/fail + зарубежные ленты."""
        earliest = digest_earliest_news_date(digest)
        policy = get_curious_source_policy()
        foreign = " OR ".join(f"site:{h}" for h in policy.curious_foreign_hosts[:8])
        ru_tech = " OR ".join(f"site:{h}" for h in policy.curious_ru_tech_hosts[:4])
        return (
            f"after:{earliest.isoformat()} "
            f"{step1_curious_entertainment_anchor_en()} "
            f"{step1_curious_foreign_topic_terms()} viral meme fail bizarre unexpected deepfake scandal AI girlfriend "
            f"({foreign}) OR ({ru_tech}) "
            "-regulation -investment -earnings -partnership "
            "-pricing -demo -careers -overview -explainer -launch -unveil"
        )

    def _step1_press_release_query(self, digest: Digest) -> str:
        """Официальные заявления и пресс-релизы: wire-сервисы и блоги крупных AI-компаний."""
        earliest = digest_earliest_news_date(digest)
        return (
            f"after:{earliest.isoformat()} "
            "\"artificial intelligence\" OR \"generative AI\" OR LLM OR \"нейросеть\" OR \"искусственный интеллект\" "
            "(\"press release\" OR \"official announcement\" OR partnership OR investment OR deployment OR "
            "launches OR introduces OR \"rolls out\" OR roadmap OR strategy OR внедрение OR партнёрство OR план) "
            "site:openai.com OR site:blogs.nvidia.com OR site:anthropic.com OR site:microsoft.com OR site:ibm.com "
            "OR site:yandex.ru OR site:sber.ru OR site:sberbank.ru OR site:vk.company "
            "OR site:businesswire.com OR site:prnewswire.com OR site:globenewswire.com "
            "OR inurl:/press OR inurl:/newsroom OR inurl:/blog "
            "-pricing -demo -trial -signup -download -vacancy -careers -webinar -podcast -tutorial -how-to"
        )

    def _step1_topup_search_until_minimum(
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
        started_monotonic: float,
        hard_limit_sec: int,
        batch_size: int,
        target: int = STEP1_MIN_VERIFIED,
        max_rounds: int = 4,
        filter_enabled: Any | None = None,
        respect_host_cap: bool = False,
        phase_label: str = "topup",
    ) -> dict[str, Any]:
        """Добор пула дополнительными итерациями web-поиска без ослабления окна дат."""
        is_enabled = filter_enabled or self._is_step1_filter_enabled
        added_total = 0
        rounds_done = 0
        no_progress_rounds = 0
        funnel_acc: dict[str, int] = {
            "urls_raw_merged": 0,
            "urls_prefilter_rejected": 0,
            "urls_sent_to_http": 0,
        }

        def _query_for_round(round_idx: int) -> str:
            # На шаге 1 не исключаем домены из поиска — лимит 2/домен только на шаге 2 (выбор топ‑5).
            if is_curious_digest(digest.digest_type):
                if round_idx % 3 == 0:
                    return self._step1_curious_human_stories_query(digest)
                if round_idx % 3 == 1:
                    return self._step1_curious_angles_query(digest)
                if round_idx % 3 == 2:
                    return self._step1_curious_viral_query(digest)
            elif round_idx % 5 == 1:
                return self._step1_fresh_breaking_query(digest)
            elif round_idx % 5 == 2:
                return self._step1_press_release_query(digest)
            elif round_idx % 5 == 3:
                return self._step1_research_science_query(digest)
            base = self._step1_fresh_tier1_query(digest)
            return base

        for round_idx in range(max(1, int(max_rounds or 1))):
            if step1_is_cancelled(digest.id):
                return {
                    "rounds": rounds_done,
                    "added": added_total,
                    "stop_reason": "user_cancelled",
                    **funnel_acc,
                }
            if self._proxyapi_funds_depleted(check_balance=False):
                return {
                    "rounds": rounds_done,
                    "added": added_total,
                    "stop_reason": "proxyapi_budget_exceeded",
                    **funnel_acc,
                }
            if len(verified_pool) >= target:
                break
            if int(time.monotonic() - started_monotonic) >= int(hard_limit_sec):
                break
            if self._digest_proxyapi_spent_rub(digest) >= self._step1_max_cost_rub():
                break
            self._refresh_step1_web_search_api_cap(len(verified_pool))
            if self._step1_web_search_api_cap_stop_reason(len(verified_pool)):
                break

            shortfall = max(1, target - len(verified_pool))
            need = min(max(6, int(batch_size or 10)), shortfall + 4)
            query = _query_for_round(round_idx)
            logger.warning(
                "Шаг 1: top-up итерация web-поиска | digest_id=%s phase=%s round=%s need=%s verified=%s",
                digest.id,
                phase_label,
                round_idx + 1,
                need,
                len(verified_pool),
            )
            before_count = len(verified_pool)
            items, funnel = self._collect_search_verified_candidates(
                digest.id,
                digest,
                now_msk,
                seen_fp,
                limit=need,
                on_row=snapshot_preview_row,
                register_reject=register_reject,
                query_override=query,
                skip_urls=excluded_urls,
                filter_enabled=filter_enabled,
                verified_pool_size=len(verified_pool),
            )
            for key in funnel_acc:
                funnel_acc[key] += int(funnel.get(key, 0) or 0)

            if respect_host_cap:
                host_counts = _pool_host_counts(verified_pool)
                existing_fps = {_url_fingerprint(str(x.get("url") or "")) for x in verified_pool}
                for item in items:
                    if len(verified_pool) >= target:
                        break
                    fp = _url_fingerprint(str(item.get("url") or ""))
                    if not fp or fp in existing_fps:
                        continue
                    host = _publisher_host_key(item)
                    if host_counts.get(host, 0) >= STEP1_MAX_PER_SOURCE:
                        continue
                    pool_before = len(verified_pool)
                    append_verified(item)
                    if len(verified_pool) > pool_before:
                        existing_fps.add(fp)
                        host_counts[host] = host_counts.get(host, 0) + 1
            else:
                for item in items:
                    append_verified(item)

            filtered, removed = _filter_verified_pool_by_date_window(
                digest,
                verified_pool,
                is_filter_enabled=is_enabled,
            )
            if removed:
                verified_pool[:] = filtered
            added = len(verified_pool) - before_count
            added_total += max(0, added)
            rounds_done += 1
            if self._proxyapi_funds_depleted(check_balance=False):
                return {
                    "rounds": rounds_done,
                    "added": added_total,
                    "stop_reason": "proxyapi_budget_exceeded",
                    **funnel_acc,
                }
            if added <= 0:
                no_progress_rounds += 1
                no_progress_limit = 2 if len(verified_pool) >= target else 6
                if no_progress_rounds >= no_progress_limit:
                    break
            else:
                no_progress_rounds = 0

        return {
            "rounds": rounds_done,
            "added": added_total,
            "verified_total": len(verified_pool),
            **funnel_acc,
        }

    def _step1_collect_iterative_batches(
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
        target_min_verified: int,
        target_max_candidates: int,
        batch_size: int,
        soft_limit_sec: int,
        hard_limit_sec: int,
        started_monotonic: float,
        start_iteration: int = 0,
        min_iterations: int = 5,
        filter_enabled: Any | None = None,
        allow_supplement_rounds: bool = True,
    ) -> dict[str, Any]:
        iteration_no = start_iteration
        min_iterations = max(1, int(min_iterations or 1))
        stop_reason = "target_reached" if len(verified_pool) >= target_max_candidates else "not_started"
        tier_strict = bool(getattr(self.settings, "step1_tier_strict_search", True))
        press_query = self._step1_press_release_query(digest)
        press_min_target = max(2, int(STEP1_TARGET_VERIFIED * STEP1_PRESS_SHARE_MIN))
        no_progress_rounds = 0
        yield_policy = getattr(self, "_step1_curious_yield", None)
        curious_mode = is_curious_digest(digest.digest_type)
        funnel_acc: dict[str, int] = {
            "urls_raw_merged": 0,
            "urls_prefilter_rejected": 0,
            "urls_sent_to_http": 0,
        }

        def _run_collect(need: int, *, query_override: str | None = None) -> None:
            if getattr(self, "_step1_web_search_abort_reason", None):
                return
            abort = self._step1_check_abort_spending(
                digest,
                verified_count=len(verified_pool),
                collection_target=target_max_candidates,
            )
            if abort:
                self._step1_web_search_abort_reason = abort
                return
            items, funnel = self._collect_search_verified_candidates(
                digest.id,
                digest,
                now_msk,
                seen_fp,
                limit=need,
                on_row=snapshot_preview_row,
                register_reject=register_reject,
                query_override=query_override,
                skip_urls=excluded_urls,
                filter_enabled=filter_enabled,
                verified_pool_size=len(verified_pool),
                deadline_monotonic=started_monotonic + hard_limit_sec,
            )
            for key in funnel_acc:
                funnel_acc[key] += int(funnel.get(key, 0) or 0)
            for item in items:
                append_verified(item)

        while len(verified_pool) < target_max_candidates:
            self._refresh_step1_web_search_api_cap(len(verified_pool))
            if yield_policy and curious_yield_min_met(len(verified_pool), yield_policy):
                stop_reason = "curious_yield_min_met"
                break
            if step1_is_cancelled(digest.id):
                stop_reason = "user_cancelled"
                logger.info(
                    "Шаг 1: отмена в итерации web-поиска | digest_id=%s verified=%s iter=%s",
                    digest.id,
                    len(verified_pool),
                    iteration_no,
                )
                break
            abort = self._step1_check_abort_spending(
                digest,
                verified_count=len(verified_pool),
                collection_target=target_max_candidates,
            )
            if abort == "hard_cost_stop":
                stop_reason = "hard_cost_stop"
                logger.warning(
                    "Шаг 1: остановка web-поиска — жёсткий лимит ₽ | digest_id=%s verified=%s iter=%s limit=%s",
                    digest.id,
                    len(verified_pool),
                    iteration_no,
                    self._step1_hard_stop_cost_rub(),
                )
                break
            if self._proxyapi_funds_depleted(check_balance=False):
                stop_reason = "proxyapi_budget_exceeded"
                logger.warning(
                    "Шаг 1: остановка web-поиска — ProxyAPI без средств | digest_id=%s verified=%s iter=%s",
                    digest.id,
                    len(verified_pool),
                    iteration_no,
                )
                break
            elapsed_sec = int(time.monotonic() - started_monotonic)
            if elapsed_sec >= hard_limit_sec:
                stop_reason = "hard_timeout"
                break
            if self._digest_proxyapi_spent_rub(digest) >= self._step1_max_cost_rub():
                stop_reason = "budget_limit"
                break
            cap_stop = self._step1_web_search_api_cap_stop_reason(len(verified_pool))
            if cap_stop:
                stop_reason = cap_stop
                logger.warning(
                    "Шаг 1: остановка web-поиска — лимит web_search API | digest_id=%s verified=%s iter=%s",
                    digest.id,
                    len(verified_pool),
                    iteration_no,
                )
                break
            abort_reason = getattr(self, "_step1_web_search_abort_reason", None)
            if abort_reason in ("user_cancelled", "hard_cost_stop"):
                stop_reason = abort_reason
                logger.warning(
                    "Шаг 1: остановка web-поиска — %s | digest_id=%s verified=%s",
                    abort_reason,
                    digest.id,
                    len(verified_pool),
                )
                break
            if abort_reason:
                stop_reason = abort_reason
                logger.warning(
                    "Шаг 1: остановка web-поиска — серия битых URL | digest_id=%s reason=%s verified=%s",
                    digest.id,
                    abort_reason,
                    len(verified_pool),
                )
                break
            iteration_no += 1
            before_count = len(verified_pool)
            need = min(batch_size, target_max_candidates - len(verified_pool))
            if need <= 0:
                stop_reason = "target_reached"
                break
            logger.info(
                "Шаг 1: итерация web-поиска | digest_id=%s iter=%s need=%s elapsed_sec=%s",
                digest.id,
                iteration_no,
                need,
                elapsed_sec,
            )
            try:
                from app.services.step1_live_progress import bump_live_progress

                bump_live_progress(digest.id, iteration=iteration_no, phase_key="web_search")
            except Exception:
                pass
            _run_collect(need)
            self._refresh_step1_web_search_api_cap(len(verified_pool))
            if self._proxyapi_funds_depleted(check_balance=False):
                stop_reason = "proxyapi_budget_exceeded"
                break
            elapsed_after_collect = int(time.monotonic() - started_monotonic)
            if elapsed_after_collect >= hard_limit_sec:
                stop_reason = "hard_timeout_after_collect"
                break
            if elapsed_after_collect >= soft_limit_sec and len(verified_pool) >= target_min_verified:
                stop_reason = "soft_timeout_after_collect"
                break
            substantive_press = sum(1 for x in verified_pool if _is_substantive_press_for_pool(x))
            if len(verified_pool) < STEP1_MIN_VERIFIED:
                if (
                    not is_curious_digest(digest.digest_type)
                    and not tier_strict
                    and len(verified_pool) < target_max_candidates
                    and substantive_press < press_min_target
                ):
                    press_need = min(max(press_min_target - substantive_press, 2), target_max_candidates - len(verified_pool))
                    _run_collect(max(press_need, 4), query_override=press_query)
                elif (
                    curious_mode
                    and len(verified_pool) < target_max_candidates
                ):
                    curious_need = min(batch_size, target_max_candidates - len(verified_pool))
                    _run_collect(
                        max(curious_need, 6),
                        query_override=self._step1_curious_human_stories_query(digest),
                    )
                    if len(verified_pool) < target_min_verified:
                        _run_collect(
                            max(curious_need, 6),
                            query_override=self._step1_curious_angles_query(digest),
                        )
            if (
                allow_supplement_rounds
                and len(verified_pool) < STEP1_MIN_VERIFIED
                and len(verified_pool) < target_max_candidates
            ):
                supplement_stop = target_min_verified
                supplement_rounds = (
                    yield_policy.supplement_rounds
                    if yield_policy
                    else (6 if curious_mode else 5)
                )
                if tier_strict and len(verified_pool) < 3:
                    # Полный tier-поиск уже в _run_collect; 5 supplement-раундов дублируют API на ~8 мин.
                    supplement_rounds = min(supplement_rounds, 1)
                # Один–два supplement-раунда на итерацию, пока не набран минимум.
                self._step1_run_web_supplement_rounds(
                    digest,
                    verified_pool=verified_pool,
                    seen_fp=seen_fp,
                    excluded_urls=excluded_urls,
                    now_msk=now_msk,
                    snapshot_preview_row=snapshot_preview_row,
                    append_verified=append_verified,
                    register_reject=register_reject,
                    stop_at=supplement_stop,
                    max_rounds=supplement_rounds,
                    filter_enabled=filter_enabled,
                )

            added = len(verified_pool) - before_count
            if added <= 0:
                no_progress_rounds += 1
            else:
                no_progress_rounds = 0
            elapsed_sec = int(time.monotonic() - started_monotonic)
            logger.info(
                "Шаг 1: итерация завершена | digest_id=%s iter=%s added=%s total=%s elapsed_sec=%s",
                digest.id,
                iteration_no,
                added,
                len(verified_pool),
                elapsed_sec,
            )

            if len(verified_pool) >= target_max_candidates:
                stop_reason = "target_reached"
                break
            iterations_done = max(0, iteration_no - start_iteration)
            if len(verified_pool) >= target_max_candidates and iterations_done >= 2:
                stop_reason = "target_max_met"
                break
            may_stop_early = iterations_done >= min_iterations
            if elapsed_sec >= soft_limit_sec and may_stop_early and len(verified_pool) >= target_min_verified:
                if len(verified_pool) >= target_max_candidates:
                    stop_reason = "soft_timeout_target_met"
                    break
                # Финальная попытка: добрать до цели воронки (target_max), не останавливаться на 10.
                remaining = max(0, target_max_candidates - len(verified_pool))
                final_need = min(batch_size, max(4, remaining)) if remaining > 0 else max(4, batch_size // 2)
                _run_collect(final_need)
                if len(verified_pool) < target_max_candidates and not tier_strict:
                    final_override = (
                        self._step1_curious_angles_query(digest)
                        if is_curious_digest(digest.digest_type)
                        else self._step1_fresh_tier1_query(digest)
                    )
                    _run_collect(final_need, query_override=final_override)
                if allow_supplement_rounds and len(verified_pool) < target_max_candidates:
                    self._step1_run_web_supplement_rounds(
                        digest,
                        verified_pool=verified_pool,
                        seen_fp=seen_fp,
                        excluded_urls=excluded_urls,
                        now_msk=now_msk,
                        snapshot_preview_row=snapshot_preview_row,
                        append_verified=append_verified,
                        register_reject=register_reject,
                        stop_at=target_max_candidates,
                        max_rounds=1,
                        filter_enabled=filter_enabled,
                    )
                stop_reason = (
                    "soft_timeout_target_met"
                    if len(verified_pool) >= target_max_candidates
                    else "soft_timeout_final_attempt"
                )
                break
            no_progress_limit = (
                yield_policy.no_progress_limit
                if yield_policy
                else (6 if len(verified_pool) < target_min_verified else 3)
            )
            if len(verified_pool) < target_min_verified:
                no_progress_limit = max(no_progress_limit, 6)
            if no_progress_rounds >= no_progress_limit and may_stop_early:
                if len(verified_pool) < target_min_verified:
                    stop_reason = "no_progress_below_minimum"
                else:
                    stop_reason = (
                        "no_progress_target_met"
                        if len(verified_pool) >= target_max_candidates
                        else "no_progress"
                    )
                break

        # #region agent log
        try:
            from app.services.agent_debug_log import agent_debug_log

            agent_debug_log(
                "H5",
                "digest_service._step1_collect_iterative_batches:done",
                "iterative_collection_stop",
                {
                    "digest_id": digest.id,
                    "stop_reason": stop_reason,
                    "verified_total": len(verified_pool),
                    "iterations": max(0, iteration_no - start_iteration),
                    "min_iterations": min_iterations,
                    **funnel_acc,
                },
            )
        except Exception:
            pass
        # #endregion
        return {
            "iterations": max(0, iteration_no - start_iteration),
            "min_iterations": min_iterations,
            "stop_reason": stop_reason,
            "elapsed_sec": int(time.monotonic() - started_monotonic),
            "verified_total": len(verified_pool),
            **funnel_acc,
        }

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
        filter_enabled: Any | None = None,
    ) -> None:
        """Добор через ProxyAPI/SerpAPI/Tavily (без CrewAI), пока не набран stop_at подтверждённых."""
        sup_round = 0
        while len(verified_pool) < stop_at and sup_round < max_rounds:
            if step1_is_cancelled(digest.id):
                break
            if self._proxyapi_funds_depleted(check_balance=False):
                break
            self._refresh_step1_web_search_api_cap(len(verified_pool))
            cap_stop = self._step1_web_search_api_cap_stop_reason(len(verified_pool))
            if cap_stop:
                logger.warning(
                    "Шаг 1: добор web_search остановлен — %s | digest_id=%s verified=%s",
                    cap_stop,
                    digest.id,
                    len(verified_pool),
                )
                break
            sup_round += 1
            need = stop_at - len(verified_pool)
            if need <= 0:
                break
            spent_step1 = self._digest_proxyapi_spent_rub(digest)
            if spent_step1 >= self._step1_max_cost_rub():
                logger.warning(
                    "Шаг 1: добор web_search остановлен — лимит step_1_max_cost_rub | digest_id=%s verified=%s",
                    digest.id,
                    len(verified_pool),
                )
                break
            saturated_hosts: set[str] = set()
            extra = self._step1_fetch_supplementary_dicts(
                digest,
                seen_fp,
                excluded_urls,
                now_msk,
                need,
                exclude_hosts=saturated_hosts,
                filter_enabled=filter_enabled,
            )
            if not extra:
                logger.warning(
                    "Шаг 1: добор web_search без новых URL | digest_id=%s round=%s",
                    digest.id,
                    sup_round,
                )
                break
            for item in extra:
                raw_u = str(item.get("url") or "").strip()
                if not raw_u.startswith("http") and (filter_enabled is None or bool(filter_enabled("invalid_url"))):
                    continue
                if url_suspected_hallucinated(raw_u) and (
                    filter_enabled is None or bool(filter_enabled("llm_hallucinated_url"))
                ):
                    continue
                for resolved_url, bundle in _expand_listing_url_candidates(raw_u, max_children=_listing_max_children(raw_u)):
                    fp = _url_fingerprint(resolved_url)
                    if fp in seen_fp:
                        continue
                    work = dict(item)
                    work["url"] = resolved_url
                    work["title"] = ""
                    work["headline_editorial_ok"] = False
                    work["link_status"] = False
                    try:
                        self._verify_llm_candidate_dict(
                            digest,
                            work,
                            prefetched_bundle=bundle,
                            filter_enabled=filter_enabled,
                        )
                    except TypeError:
                        self._verify_llm_candidate_dict(digest, work, prefetched_bundle=bundle)
                    snapshot_preview_row(work)
                    if work.get("headline_editorial_ok") and work.get("link_status"):
                        append_verified(work)
                    else:
                        if fp:
                            seen_fp.add(fp)
                        excluded_urls.append(resolved_url[:800])
                        register_reject(work)

    def _step1_cheap_sources_first_enabled(self) -> bool:
        return bool(getattr(self.settings, "step1_cheap_sources_first", True))

    def _step1_effective_search_seed_urls(self, digest: Digest) -> list[str]:
        """Ленты из search_seed_urls + Telegram + tier-домены из модалки «Источники»."""
        if is_curious_digest(digest.digest_type):
            policy = get_curious_source_policy()
        else:
            policy = get_source_tiers_policy(self.settings.source_tiers_path)
        expanded = expand_search_seed_urls(
            policy.search_seed_urls,
            settings=self.settings,
            digest_type=digest.digest_type,
        )
        return [
            str(u).strip()
            for u in expanded
            if str(u).startswith(("http://", "https://"))
        ]

    def _step1_resolve_seed_urls(self, digest: Digest) -> list[str]:
        max_seeds = max(1, int(getattr(self.settings, "step1_seed_urls_max", 35) or 35))
        seeds = self._step1_effective_search_seed_urls(digest)
        return list(dict.fromkeys(seeds))[:max_seeds]

    def _step1_add_seed_listing_raw_urls(
        self,
        digest: Digest,
        *,
        seen_raw: set[str],
        raw_unique: list[str],
        skip_urls: list[str] | None,
        max_seeds: int | None = None,
        max_children_per_seed: int | None = None,
    ) -> int:
        """HTTP-разбор seed-лент/агрегаторов → сырые URL статей (без ProxyAPI web_search)."""
        skip_norm = {str(u).strip().lower().rstrip("/") for u in (skip_urls or []) if u}
        added = 0
        seeds = self._step1_resolve_seed_urls(digest)
        if max_seeds is not None:
            seeds = seeds[: max(0, int(max_seeds))]
        for seed_url in seeds:
            seed_key = str(seed_url).strip().lower().rstrip("/")
            if seed_key in skip_norm:
                continue
            expand_n = (
                int(max_children_per_seed)
                if max_children_per_seed is not None
                else _listing_max_children(str(seed_url))
            )
            try:
                pairs = _expand_listing_url_candidates(str(seed_url), max_children=expand_n)
            except Exception:
                logger.debug(
                    "Шаг 1: seed-лента не разобрана | digest_id=%s seed=%s",
                    digest.id,
                    str(seed_url)[:120],
                    exc_info=True,
                )
                continue
            for resolved_url, _bundle in pairs:
                key = str(resolved_url or "").strip().lower().rstrip("/")
                if not key or key in seen_raw or key in skip_norm:
                    continue
                seen_raw.add(key)
                raw_unique.append(str(resolved_url).strip())
                added += 1
        if added:
            logger.info(
                "Шаг 1: сырые URL из seed-лент (HTTP) | digest_id=%s count=%s",
                digest.id,
                added,
            )
        return added

    def _step1_scan_seed_listings(
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
        hard_limit_monotonic: float | None = None,
        filter_enabled: Any | None = None,
        max_seeds: int | None = None,
    ) -> dict[str, Any]:
        """Дешёвая фаза: ленты/агрегаторы из search_seed_urls + HTTP-проверка статей."""
        meta: dict[str, Any] = {
            "cheap_seed_listings_scanned": 0,
            "cheap_seed_listings_added": 0,
        }
        seeds = self._step1_resolve_seed_urls(digest)
        if max_seeds is not None:
            seeds = seeds[: max(0, int(max_seeds))]
        for seed_url in seeds:
            if len(verified_pool) >= stop_at:
                break
            abort = self._step1_check_abort_spending(
                digest,
                verified_count=len(verified_pool),
                collection_target=stop_at,
            )
            if abort:
                meta["cheap_seed_listings_stop_reason"] = abort
                break
            if hard_limit_monotonic is not None and time.monotonic() >= hard_limit_monotonic:
                meta["cheap_seed_listings_stopped_by_hard_limit"] = True
                break
            meta["cheap_seed_listings_scanned"] = int(meta["cheap_seed_listings_scanned"]) + 1
            for resolved_url, bundle in _expand_listing_url_candidates(
                str(seed_url), max_children=_listing_max_children(str(seed_url))
            ):
                if len(verified_pool) >= stop_at:
                    break
                if hard_limit_monotonic is not None and time.monotonic() >= hard_limit_monotonic:
                    meta["cheap_seed_listings_stopped_by_hard_limit"] = True
                    break
                fp = _url_fingerprint(resolved_url)
                if fp and fp in seen_fp:
                    continue
                try:
                    from app.services.step1_live_progress import record_links_found_free

                    record_links_found_free(digest.id)
                except Exception:
                    pass
                work = self._skeleton_dict_from_search_url(
                    resolved_url,
                    now_msk,
                    max(1, len(verified_pool) + 1),
                )
                work["source_stage"] = "cheap_seed_listing"
                work["title"] = ""
                work["headline_editorial_ok"] = False
                work["link_status"] = False
                try:
                    self._verify_llm_candidate_dict(
                        digest,
                        work,
                        prefetched_bundle=bundle,
                        filter_enabled=filter_enabled,
                    )
                except TypeError:
                    self._verify_llm_candidate_dict(digest, work, prefetched_bundle=bundle)
                snapshot_preview_row(work)
                before = len(verified_pool)
                if work.get("headline_editorial_ok") and work.get("link_status"):
                    append_verified(work)
                else:
                    if fp:
                        seen_fp.add(fp)
                    excluded_urls.append(str(resolved_url)[:800])
                    register_reject(work)
                if len(verified_pool) > before:
                    meta["cheap_seed_listings_added"] = int(meta["cheap_seed_listings_added"]) + 1
        if meta["cheap_seed_listings_scanned"]:
            logger.info(
                "Шаг 1: дешёвый сбор seed-лент | digest_id=%s scanned=%s added=%s pool=%s",
                digest.id,
                meta["cheap_seed_listings_scanned"],
                meta["cheap_seed_listings_added"],
                len(verified_pool),
            )
        return meta

    def _step1_seed_collect_from_registry(
        self,
        digest: Digest,
        *,
        fetch_limit: int,
        skip_urls: list[str] | None,
        seen_fp: set[str],
        seen_raw: set[str],
        raw_unique: list[str],
    ) -> int:
        skip_fps = set(seen_fp)
        for raw in skip_urls or []:
            fp = _url_fingerprint(str(raw or ""))
            if fp:
                skip_fps.add(fp)
        window_earliest = digest_earliest_news_date(digest)
        window_anchor = digest_news_anchor_date(digest)
        candidates = load_registry_raw_urls(
            self.db,
            self.settings,
            digest_type=digest.digest_type,
            limit=max(1, int(fetch_limit or 1)),
            skip_urls=skip_urls,
            skip_fingerprints=skip_fps,
            earliest=window_earliest,
            anchor=window_anchor,
        )
        added = 0
        curious = is_curious_digest(digest.digest_type)
        for url in candidates:
            if search_url_prefilter_reason(
                url,
                earliest=window_earliest,
                anchor=window_anchor,
                is_enabled=self._is_step1_filter_enabled,
            ):
                continue
            if _url_path_date_before_digest_window(digest, url):
                continue
            if curious:
                if is_curious_blocked_host(url):
                    continue
            elif is_blocked_search_host(url):
                continue
            fp = _url_fingerprint(url)
            if fp and fp in seen_fp:
                continue
            key = url.lower().rstrip("/")
            if key in seen_raw:
                continue
            seen_raw.add(key)
            raw_unique.append(url)
            added += 1
        if added:
            try:
                from app.services.step1_live_progress import record_links_found_free

                record_links_found_free(digest.id, count=added)
            except Exception:
                pass
            logger.info(
                "Шаг 1: сырые URL из реестра (без web_search) | digest_id=%s count=%s",
                digest.id,
                added,
            )
        return added

    def _collect_search_verified_candidates(
        self,
        digest_id: int,
        digest: Digest,
        now_msk: str,
        seen_fp: set[str],
        limit: int,
        on_row: Any | None = None,
        register_reject: Any | None = None,
        query_override: str | None = None,
        skip_urls: list[str] | None = None,
        filter_enabled: Any | None = None,
        verified_pool_size: int = 0,
        deadline_monotonic: float | None = None,
    ) -> tuple[list[dict[str, Any]], dict[str, int]]:
        """Реальные URL из ProxyAPI + SerpAPI + Tavily — до LLM-цепочки."""
        empty_funnel = {"urls_raw_merged": 0, "urls_prefilter_rejected": 0, "urls_sent_to_http": 0}
        try:
            from app.services.step1_web_search_stats import reset_empty_citation_streak

            reset_empty_citation_streak()
        except ImportError:
            pass
        tier_strict_search = bool(getattr(self.settings, "step1_tier_strict_search", True))
        # Strict citations отключён: vetted URL из preview нужны и для tier-1, и для забавных источников (разнообразие).
        set_step1_strict_web_search_citations(False)
        search_route = resolve_step1_search_routing(
            digest.digest_type,
            query_override=query_override,
            tier_strict_setting=bool(getattr(self.settings, "step1_tier_strict_search", True)),
            curious_use_serious_tiers=bool(getattr(self.settings, "step1_curious_use_serious_tiers", False)),
        )
        tier_strict = search_route.tier_strict
        curious_strict = search_route.curious_strict
        window_hint, topic_terms, product_excludes = self._step1_search_query_parts(digest)
        configured_fetch_limit = max(10, int(getattr(self.settings, "step1_search_fetch_limit", 36) or 36))
        configured_check_limit = max(5, int(getattr(self.settings, "step1_urls_checked_per_collect", 24) or 24))
        pool_shortfall = max(0, STEP1_MIN_VERIFIED - max(0, int(verified_pool_size or 0)))
        if pool_shortfall > 0:
            fetch_limit = configured_fetch_limit
            check_limit = configured_check_limit
            if pool_shortfall >= 5:
                fetch_limit = min(configured_fetch_limit + 24, 100)
                check_limit = min(configured_check_limit + 20, 80)
        else:
            fetch_limit = min(configured_fetch_limit, max(limit * 3, 12))
            check_limit = min(configured_check_limit, max(limit * 2, 8))
        min_fetch = getattr(self, "_step1_min_fetch_limit", None)
        if min_fetch:
            fetch_limit = min(max(fetch_limit, int(min_fetch)), configured_fetch_limit + 10)
        elif pool_shortfall <= 0:
            fetch_limit = min(fetch_limit, configured_fetch_limit + 5)
        if pool_shortfall <= 0:
            check_limit = min(check_limit, configured_check_limit)
        else:
            check_limit = min(check_limit, configured_check_limit + 20)
        if min_fetch and fetch_limit >= int(min_fetch):
            logger.info(
                "Шаг 1: fetch по estimated_raw_for_10 | digest_id=%s fetch=%s http=%s plan_raw=%s",
                digest_id,
                fetch_limit,
                check_limit,
                min_fetch,
            )
        if curious_strict:
            fetch_limit = max(fetch_limit, configured_fetch_limit + 14)
            check_limit = max(check_limit, configured_check_limit + 14)
        supplement_ctx = str(
            getattr(self.settings, "proxyapi_web_search_context_size_supplement", "low") or "low"
        ).lower()
        policy = get_source_tiers_policy(self.settings.source_tiers_path)
        seen_raw: set[str] = set()
        raw_unique: list[str] = []
        registry_raw_added = 0
        skip_registry = False
        tier_search_first = search_route.uses_source_tiers
        ws_stats = current_step1_web_search_stats()
        from app.services.step1_web_search_stats import step1_web_search_api_cap_should_stop

        api_cap_blocks_search = step1_web_search_api_cap_should_stop(
            verified_count=int(verified_pool_size or 0),
            min_verified=STEP1_MIN_VERIFIED,
            pool_shortfall=pool_shortfall,
        )
        api_cap_reached = bool(api_cap_blocks_search)
        skip_web_search = api_cap_reached
        if not tier_search_first and not skip_registry:
            registry_raw_added = self._step1_seed_collect_from_registry(
                digest,
                fetch_limit=fetch_limit,
                skip_urls=skip_urls,
                seen_fp=seen_fp,
                seen_raw=seen_raw,
                raw_unique=raw_unique,
            )
            skip_web_search = skip_web_search or (
                int(verified_pool_size or 0) >= STEP1_MIN_VERIFIED
                and len(raw_unique) >= check_limit
            )
        force_tier_web_search = tier_search_first and pool_shortfall > 0 and not api_cap_reached
        if skip_registry and not skip_web_search and not force_tier_web_search:
            logger.info(
                "Шаг 1: реестр URL пропущен — короткий пул, приоритет web_search | digest_id=%s shortfall=%s",
                digest.id,
                pool_shortfall,
            )
        if api_cap_reached and not raw_unique:
            logger.warning(
                "Шаг 1: сбор URL пропущен — лимит web_search API и пустой реестр | digest_id=%s limit=%s",
                digest_id,
                limit,
            )
            # #region agent log
            try:
                from app.services.agent_debug_log import agent_debug_log

                agent_debug_log(
                    "H2",
                    "digest_service._collect_search_verified_candidates:cap_abort",
                    "early_abort_api_cap_empty_raw",
                    {
                        "digest_id": digest_id,
                        "limit": limit,
                        "api_cap": getattr(ws_stats, "api_cap", None) if ws_stats else None,
                        "api_calls": getattr(ws_stats, "api_calls", None) if ws_stats else None,
                        "pool_shortfall": pool_shortfall,
                    },
                )
            except Exception:
                pass
            # #endregion
            return [], empty_funnel
        if skip_web_search and raw_unique:
            logger.info(
                "Шаг 1: web_search пропущен — достаточно сырых URL | digest_id=%s registry=%s total=%s need_http=%s cap=%s",
                digest.id,
                registry_raw_added,
                len(raw_unique),
                check_limit,
                api_cap_reached,
            )

        cheap_first = self._step1_cheap_sources_first_enabled()
        listing_raw_added = 0
        cheap_registry_done = False

        if tier_search_first and not skip_registry and cheap_first and pool_shortfall > 0:
            registry_raw_added = self._step1_seed_collect_from_registry(
                digest,
                fetch_limit=fetch_limit,
                skip_urls=skip_urls,
                seen_fp=seen_fp,
                seen_raw=seen_raw,
                raw_unique=raw_unique,
            )
            listing_raw_added = self._step1_add_seed_listing_raw_urls(
                digest,
                seen_raw=seen_raw,
                raw_unique=raw_unique,
                skip_urls=skip_urls,
            )
            cheap_registry_done = True
        elif tier_search_first and not skip_registry and pool_shortfall > 0:
            registry_raw_added = self._step1_seed_collect_from_registry(
                digest,
                fetch_limit=min(12, max(6, fetch_limit // 4)),
                skip_urls=skip_urls,
                seen_fp=seen_fp,
                seen_raw=seen_raw,
                raw_unique=raw_unique,
            )

        tier_web_search_needed = pool_shortfall > 0 and (
            not cheap_first or len(raw_unique) < configured_check_limit
        )
        if cheap_first and pool_shortfall <= 0:
            skip_web_search = True
        elif cheap_first and not tier_web_search_needed:
            skip_web_search = skip_web_search or True

        if (not skip_web_search or force_tier_web_search) and tier_web_search_needed:
            if search_route.uses_curious_hosts:
                logger.info(
                    "Шаг 1: курьёзный поиск по curious_source_hosts | digest_id=%s route=%s",
                    digest.id,
                    search_route.route,
                )
                for u in fetch_curious_prioritized_raw_urls(
                    self.settings,
                    window_prefix=window_hint,
                    topic_terms_ru=topic_terms,
                    topic_terms_foreign=step1_curious_foreign_topic_terms(),
                    product_excludes=product_excludes,
                    fetch_limit=fetch_limit,
                    proxy=self.proxy,
                    search_context_size=supplement_ctx,
                    max_search_batches=self._step1_curious_max_search_batches(
                        pool_shortfall=pool_shortfall,
                        verified_pool_size=int(verified_pool_size or 0),
                    ),
                    skip_aggregator_tier=bool(
                        getattr(self, "_step1_curious_yield", None)
                        and getattr(self, "_step1_curious_yield").skip_aggregator_search
                        and int(verified_pool_size or 0) >= 5
                        and pool_shortfall < 3
                    ),
                    pool_shortfall=pool_shortfall,
                ):
                    key = str(u or "").strip().lower().rstrip("/")
                    if not key or key in seen_raw:
                        continue
                    seen_raw.add(key)
                    raw_unique.append(str(u).strip())
            elif search_route.uses_source_tiers:
                if search_route.curious_verify:
                    logger.info(
                        "Шаг 1: курьёзный выпуск — поиск по source_tiers, фильтр тона curious | digest_id=%s route=%s",
                        digest.id,
                        search_route.route,
                    )
                else:
                    logger.info(
                        "Шаг 1: tier-строгий поиск по source_tiers | digest_id=%s route=%s",
                        digest.id,
                        search_route.route,
                    )
                before_tier_raw = len(raw_unique)
                for u in fetch_tier_prioritized_raw_urls(
                    self.settings,
                    window_prefix=window_hint,
                    topic_terms=topic_terms,
                    product_excludes=product_excludes,
                    fetch_limit=fetch_limit,
                    proxy=self.proxy,
                    search_context_size=supplement_ctx,
                    policy=policy,
                    current_verified=max(0, int(verified_pool_size or 0)),
                    pool_shortfall=pool_shortfall,
                    deadline_monotonic=deadline_monotonic,
                    digest_id=digest.id,
                    proxyapi_spent_rub=self._digest_proxyapi_spent_rub(digest),
                ):
                    key = str(u or "").strip().lower().rstrip("/")
                    if not key or key in seen_raw:
                        continue
                    seen_raw.add(key)
                    raw_unique.append(str(u).strip())
                tier_raw_added = max(0, len(raw_unique) - before_tier_raw)
                serious_curious_enabled = bool(
                    getattr(self.settings, "step1_serious_use_curious_tiers", True)
                )
                skip_curious_supplement = (
                    query_override is None
                    and not is_curious_digest(digest.digest_type)
                    and serious_curious_enabled
                    and (
                        tier_raw_added >= max(8, check_limit // 2)
                        or len(raw_unique) >= check_limit
                    )
                )
                if skip_curious_supplement:
                    logger.info(
                        "Шаг 1: курьёзный добор пропущен — достаточно tier/registry URL для HTTP | digest_id=%s tier_raw=%s raw=%s http=%s",
                        digest.id,
                        tier_raw_added,
                        len(raw_unique),
                        check_limit,
                    )
                elif (
                    query_override is None
                    and not is_curious_digest(digest.digest_type)
                    and serious_curious_enabled
                ):
                    curious_batches = max(
                        2,
                        min(
                            6,
                            int(getattr(self.settings, "step1_serious_curious_search_batches", 4) or 4),
                        ),
                    )
                    if tier_raw_added >= 5:
                        curious_batches = min(curious_batches, 2)
                    logger.info(
                        "Шаг 1: добор курьёзных источников для серьёзного выпуска | digest_id=%s batches=%s tier_raw=%s",
                        digest.id,
                        curious_batches,
                        tier_raw_added,
                    )
                    for u in fetch_curious_prioritized_raw_urls(
                        self.settings,
                        window_prefix=window_hint,
                        topic_terms_ru=topic_terms,
                        topic_terms_foreign=step1_curious_foreign_topic_terms(),
                        product_excludes=product_excludes,
                        fetch_limit=fetch_limit,
                        proxy=self.proxy,
                        search_context_size=supplement_ctx,
                        max_search_batches=curious_batches,
                        skip_aggregator_tier=True,
                        pool_shortfall=pool_shortfall,
                    ):
                        key = str(u or "").strip().lower().rstrip("/")
                        if not key or key in seen_raw:
                            continue
                        seen_raw.add(key)
                        raw_unique.append(str(u).strip())
                if query_override is None and raw_unique:
                    stale_urls = [u for u in raw_unique if _url_path_date_before_digest_window(digest, u)]
                    stale_ratio = (len(stale_urls) / max(1, len(raw_unique))) if raw_unique else 0.0
                    stale_threshold = max(2, min(10, len(raw_unique) // 4))
                    if len(stale_urls) >= stale_threshold and stale_ratio >= 0.25:
                        tier1_query = self._step1_fresh_tier1_query(digest)
                        logger.info(
                            "Шаг 1: свежий rescue-добор tier-1 из-за старой выдачи | digest_id=%s stale=%s raw=%s ratio=%.2f",
                            digest.id,
                            len(stale_urls),
                            len(raw_unique),
                            stale_ratio,
                        )
                        for u in fetch_article_urls_raw_merged(
                            self.settings,
                            tier1_query,
                            limit=fetch_limit,
                            proxy=self.proxy,
                            search_context_size=supplement_ctx,
                            force_proxyapi=True,
                        ):
                            key = str(u or "").strip().lower().rstrip("/")
                            if not key or key in seen_raw:
                                continue
                            seen_raw.add(key)
                            raw_unique.append(str(u).strip())
            else:
                query = query_override or self._step1_search_query(digest)
                raw_merged: list[str] = []
                if query_override is not None and is_curious_digest(digest.digest_type):
                    curious_policy = get_curious_source_policy()
                    allowed = list(curious_policy.all_search_hosts())
                    raw_merged.extend(
                        fetch_article_urls_raw_merged(
                            self.settings,
                            query,
                            limit=fetch_limit,
                            proxy=self.proxy,
                            include_domains=allowed,
                            allowed_hosts=allowed,
                            curious_search=True,
                        )
                    )
                else:
                    raw_merged.extend(
                        fetch_article_urls_raw_merged(
                            self.settings,
                            query,
                            limit=fetch_limit,
                            proxy=self.proxy,
                        )
                    )
                for u in raw_merged:
                    key = str(u or "").strip().lower().rstrip("/")
                    if not key or key in seen_raw:
                        continue
                    seen_raw.add(key)
                    raw_unique.append(str(u).strip())
                tier1_min_raw = max(1, int(getattr(self.settings, "step1_search_tier1_min_raw_urls", 15) or 15))
                if query_override is None and len(raw_unique) < tier1_min_raw:
                    tier1_query = self._step1_fresh_tier1_query(digest)
                    logger.info(
                        "Шаг 1: добор tier-1 web_search | digest_id=%s raw=%s min=%s",
                        digest.id,
                        len(raw_unique),
                        tier1_min_raw,
                    )
                    for u in fetch_article_urls_raw_merged(
                        self.settings,
                        tier1_query,
                        limit=fetch_limit,
                        proxy=self.proxy,
                        search_context_size=supplement_ctx,
                    ):
                        key = str(u or "").strip().lower().rstrip("/")
                        if not key or key in seen_raw:
                            continue
                        seen_raw.add(key)
                        raw_unique.append(str(u).strip())
        elif cheap_first and pool_shortfall <= 0:
            logger.info(
                "Шаг 1: tier web_search пропущен — пул уже ≥ %s (дешёвые источники)",
                STEP1_MIN_VERIFIED,
            )
        elif cheap_first and not tier_web_search_needed:
            logger.info(
                "Шаг 1: tier web_search пропущен — достаточно URL из лент/реестра | raw=%s http=%s listing=%s",
                len(raw_unique),
                check_limit,
                listing_raw_added,
            )
        if tier_search_first and not skip_registry and not cheap_registry_done:
            registry_limit = fetch_limit
            if pool_shortfall > 0:
                registry_limit = min(12, max(6, fetch_limit // 4))
            if pool_shortfall <= 0 or registry_raw_added == 0:
                registry_raw_added = self._step1_seed_collect_from_registry(
                    digest,
                    fetch_limit=registry_limit,
                    skip_urls=skip_urls,
                    seen_fp=seen_fp,
                    seen_raw=seen_raw,
                    raw_unique=raw_unique,
                )
            from app.source_tiers_policy import count_urls_on_host_markers

            tier1_min_raw = max(1, int(getattr(self.settings, "step1_search_tier1_min_raw_urls", 15) or 15))
            tier1_raw = count_urls_on_host_markers(raw_unique, policy.tier1_hosts)
            if tier1_raw < tier1_min_raw and not api_cap_reached:
                logger.info(
                    "Шаг 1: мало tier-1 в сырых URL после tier-поиска | digest_id=%s tier1=%s min=%s total=%s registry=%s",
                    digest.id,
                    tier1_raw,
                    tier1_min_raw,
                    len(raw_unique),
                    registry_raw_added,
                )
        funnel = {
            "urls_raw_merged": len(raw_unique),
            "urls_prefilter_rejected": 0,
            "urls_sent_to_http": 0,
        }
        if registry_raw_added:
            funnel["urls_raw_from_registry"] = registry_raw_added
        if listing_raw_added:
            funnel["urls_raw_from_seed_listings"] = listing_raw_added
        if skip_web_search and raw_unique:
            funnel["web_search_skipped"] = 1
        try:
            registered = register_raw_urls(
                self.db,
                self.settings,
                urls=raw_unique,
                digest_type=digest.digest_type,
                digest_id=digest_id,
                source_stage="search",
            )
            if registered:
                self.db.commit()
                funnel["urls_raw_registry_added"] = registered
        except Exception:
            logger.debug("Реестр URL: register raw | digest_id=%s", digest_id, exc_info=True)
        registry = getattr(self, "_step1_raw_urls_registry", None)
        if registry is not None:
            for raw_u in raw_unique:
                key = str(raw_u or "").strip().lower().rstrip("/")
                if key:
                    registry.add(key)
            funnel["urls_raw_unique"] = len(registry)
        urls_to_check: list[str] = []
        agg_prefilter_reasons: dict[str, int] = {}
        agg_raw_total = sum(1 for u in raw_unique if _is_aggregator_source(u, policy))
        agg_prefilter_rejected = 0
        seq_pf = 1
        prefilter_order = self._step1_stage_filter_order(
            "pre_http",
            list(CURIOUS_PREFILTER_DEFAULT_ORDER)
            if curious_strict
            else [
                "invalid_url",
                "duplicate_url_skip",
                "recent_top5_repeat",
                "non_policy_source",
                "aggregator_source",
                "forbidden_media_source",
                "news_listing_page",
                "support_documentation_page",
                "llm_hallucinated_url",
                "product_tool_page",
                "published_before_window",
            ],
        )
        search_window_earliest = digest_earliest_news_date(digest)
        search_window_anchor = digest_news_anchor_date(digest)
        serious_mixed_sources = (
            not is_curious_digest(digest.digest_type)
            and bool(getattr(self.settings, "step1_serious_use_curious_tiers", True))
        )
        for raw in raw_unique:
            if step1_is_cancelled(digest_id):
                break
            is_agg_raw = _is_aggregator_source(raw, policy)
            pre_reason = search_url_prefilter_reason(
                raw,
                is_enabled=filter_enabled,
                order=prefilter_order,
                tier_strict=tier_strict,
                curious_strict=curious_strict,
                allow_curious_tiers_in_serious=serious_mixed_sources,
                earliest=search_window_earliest,
                anchor=search_window_anchor,
            )
            if not pre_reason:
                pre_reason = self._recent_top5_repeat_reason(raw)
            # Страницы-ленты и агрегаторы не отбрасываем сразу: разворачиваем в статьи
            # в _ingest_step1_urls_with_listing_expansion().
            if pre_reason == "news_listing_page":
                urls_to_check.append(raw)
                continue
            if (
                pre_reason == "aggregator_source"
                or (
                    not pre_reason
                    and (is_curious_aggregator_source(raw) or _is_aggregator_source(raw, policy))
                )
            ):
                urls_to_check.append(raw)
                continue
            if pre_reason:
                funnel["urls_prefilter_rejected"] += 1
                if is_agg_raw:
                    agg_prefilter_rejected += 1
                    agg_prefilter_reasons[pre_reason] = agg_prefilter_reasons.get(pre_reason, 0) + 1
                self._record_prefilter_reject(
                    digest,
                    raw,
                    now_msk,
                    pre_reason,
                    on_row=on_row,
                    register_reject=register_reject,
                    seq=seq_pf,
                )
                seq_pf += 1
                continue
            if (filter_enabled is None or bool(filter_enabled("published_before_window"))) and _url_path_date_before_digest_window(
                digest, raw
            ):
                funnel["urls_prefilter_rejected"] += 1
                if is_agg_raw:
                    agg_prefilter_rejected += 1
                    agg_prefilter_reasons["published_before_window"] = agg_prefilter_reasons.get("published_before_window", 0) + 1
                self._record_prefilter_reject(
                    digest,
                    raw,
                    now_msk,
                    "published_before_window",
                    on_row=on_row,
                    register_reject=register_reject,
                    seq=seq_pf,
                )
                seq_pf += 1
                continue
            urls_to_check.append(raw)
        urls = self._step1_prioritize_search_urls(urls_to_check, digest)
        funnel["urls_sent_to_http"] = min(len(urls), check_limit)
        agg_sent_to_http = sum(1 for u in urls[:check_limit] if _is_aggregator_source(u, policy))
        logger.info(
            "Шаг 1: воронка поиска | digest_id=%s raw=%s prefilter=%s http=%s need_verified=%s",
            digest_id,
            funnel["urls_raw_merged"],
            funnel["urls_prefilter_rejected"],
            funnel["urls_sent_to_http"],
            limit,
        )
        try:
            from app.services.step1_live_progress import bump_live_progress, sync_live_from_web_search_stats

            sync_live_from_web_search_stats(digest_id)
            free_from_funnel = int(funnel.get("urls_raw_from_seed_listings") or 0) + int(
                funnel.get("urls_raw_from_registry") or 0
            )
            paid_from_funnel = max(0, int(funnel.get("urls_raw_merged") or 0) - free_from_funnel)
            bump_live_progress(
                digest_id,
                urls_raw_merged=int(funnel.get("urls_raw_merged") or 0),
                urls_raw=int(funnel.get("urls_raw_unique") or funnel.get("urls_raw_merged") or 0),
                urls_prefilter_rejected=int(funnel.get("urls_prefilter_rejected") or 0),
                urls_sent_to_http=int(funnel.get("urls_sent_to_http") or 0),
                links_found_paid=paid_from_funnel,
                links_found_free=free_from_funnel,
                phase_key="http_verify",
            )
        except Exception:
            pass
        if search_route.uses_source_tiers or agg_raw_total > 0:
            agg_hosts = sorted(
                {
                    (urlparse(u).hostname or "").lower()
                    for u in raw_unique
                    if _is_aggregator_source(u, policy)
                }
            )
            top_reasons = ", ".join(
                f"{k}={v}" for k, v in sorted(agg_prefilter_reasons.items(), key=lambda x: (-x[1], x[0]))[:4]
            )
            logger.info(
                "Шаг 1: агрегаторная диагностика | digest_id=%s raw=%s prefilter_rejected=%s sent_to_http=%s hosts=%s reasons=%s",
                digest_id,
                agg_raw_total,
                agg_prefilter_rejected,
                agg_sent_to_http,
                len(agg_hosts),
                top_reasons or "-",
            )
        verified = self._ingest_step1_urls_with_listing_expansion(
            digest,
            urls,
            now_msk,
            seen_fp,
            limit=limit,
            seq_start=seq_pf,
            on_row=on_row,
            register_reject=register_reject,
            skip_urls=set(skip_urls or []),
            max_urls_to_process=check_limit,
            max_pending_checks=max(check_limit * 2, 64),
            filter_enabled=filter_enabled,
        )
        # #region agent log
        try:
            from app.services.agent_debug_log import agent_debug_log

            agent_debug_log(
                "H4",
                "digest_service._collect_search_verified_candidates:done",
                "collect_funnel",
                {
                    "digest_id": digest_id,
                    "raw_unique": len(raw_unique),
                    "urls_to_check": len(urls_to_check),
                    "urls_http": len(urls),
                    "verified": len(verified),
                    "skip_web_search": bool(skip_web_search),
                    "api_cap_reached": bool(api_cap_reached),
                    "funnel": funnel,
                },
            )
        except Exception:
            pass
        # #endregion
        return verified, funnel

    def _record_prefilter_reject(
        self,
        digest: Digest,
        url: str,
        now_msk: str,
        reason_code: str,
        *,
        on_row: Any | None,
        register_reject: Any | None,
        seq: int,
    ) -> None:
        item = self._skeleton_dict_from_search_url(url, now_msk, seq)
        item["link_status"] = False
        item["headline_editorial_ok"] = False
        item["page_verified"] = False
        _append_reject_reason(item, reason_code)
        if on_row is not None:
            on_row(item)
        if register_reject is not None:
            register_reject(item)

    def _ingest_step1_urls_with_listing_expansion(
        self,
        digest: Digest,
        urls: list[str],
        now_msk: str,
        seen_fp: set[str],
        *,
        limit: int,
        seq_start: int = 1,
        max_children_per_listing: int = STEP1_LISTING_EXPAND_CHILDREN,
        on_row: Any | None = None,
        register_reject: Any | None = None,
        skip_urls: set[str] | None = None,
        max_urls_to_process: int | None = None,
        max_pending_checks: int | None = None,
        filter_enabled: Any | None = None,
    ) -> list[dict[str, Any]]:
        """Проверяет URL; страницы-ленты разворачивает в отдельные статьи."""
        verified: list[dict[str, Any]] = []
        seq = seq_start
        skip_norm = {s.strip().lower() for s in (skip_urls or set()) if s}
        workers = max(1, min(12, int(getattr(self.settings, "step1_verify_workers", 6) or 6)))
        pending: list[tuple[str, dict[str, Any] | None, int]] = []
        pending_host_counts: dict[str, int] = {}
        visited: set[str] = set()
        listing_child_total = 0
        listing_child_skipped_excluded = 0
        listing_child_rejected_by_url_date = 0
        process_cap = max_urls_to_process if max_urls_to_process is not None else max(limit * 10, 80)
        pending_cap = max_pending_checks if max_pending_checks is not None else max(limit * 12, 96)
        max_pending_per_host = max(0, int(getattr(self.settings, "step1_max_pending_per_host", 0) or 0))
        processed = 0

        logger.info(
            "Шаг 1: HTTP-проверка URL | digest_id=%s urls=%s limit=%s workers=%s",
            digest.id,
            min(len(urls), process_cap),
            limit,
            workers,
        )
        started_check = time.monotonic()

        for u in urls:
            if step1_is_cancelled(digest.id) or self._step1_hard_cost_limit_hit(digest):
                break
            if processed >= process_cap and len(verified) >= limit:
                break
            raw = str(u or "").strip()
            if not raw.startswith("http"):
                continue
            processed += 1
            u_norm = raw.lower()
            if u_norm in skip_norm:
                self._record_prefilter_reject(
                    digest, raw, now_msk, "duplicate_url_skip", on_row=on_row, register_reject=register_reject, seq=seq
                )
                seq += 1
                continue
            if is_step1_listing_seed_url(raw):
                pairs = _expand_listing_url_candidates(raw, max_children=max_children_per_listing)
            else:
                # Обычные URL не скачиваем здесь последовательно: отправляем сразу
                # в ThreadPoolExecutor, иначе 60-80 ссылок превращаются в длинный
                # синхронный pre-verify.
                pairs = [(raw, None)]
            for resolved_url, bundle in pairs:
                if resolved_url != raw:
                    listing_child_total += 1
                resolved_norm = str(resolved_url or "").strip().lower()
                if resolved_norm in skip_norm:
                    listing_child_skipped_excluded += 1
                    self._record_prefilter_reject(
                        digest,
                        resolved_url,
                        now_msk,
                        "duplicate_url_skip",
                        on_row=on_row,
                        register_reject=register_reject,
                        seq=seq,
                    )
                    seq += 1
                    continue
                if (
                    (filter_enabled is None or bool(filter_enabled("published_before_window")))
                    and _url_path_date_before_digest_window(digest, resolved_url)
                ):
                    listing_child_rejected_by_url_date += 1
                    self._record_prefilter_reject(
                        digest,
                        resolved_url,
                        now_msk,
                        "published_before_window",
                        on_row=on_row,
                        register_reject=register_reject,
                        seq=seq,
                    )
                    seq += 1
                    continue
                fp = _url_fingerprint(resolved_url)
                if not fp or fp in seen_fp or fp in visited:
                    continue
                host = _host_from_url(resolved_url).lower()
                if (
                    max_pending_per_host > 0
                    and host
                    and pending_host_counts.get(host, 0) >= max_pending_per_host
                ):
                    continue
                visited.add(fp)
                pending.append((resolved_url, bundle, seq))
                if host:
                    pending_host_counts[host] = pending_host_counts.get(host, 0) + 1
                seq += 1
                if len(pending) >= pending_cap:
                    break
            if len(pending) >= pending_cap:
                break

        def _verify_one(work: tuple[str, dict[str, Any] | None, int]) -> dict[str, Any]:
            resolved_url, bundle, work_seq = work
            item = self._skeleton_dict_from_search_url(resolved_url, now_msk, work_seq)
            if bundle is None or not bundle.get("ok"):
                bundle = _fetch_article_page_bundle(resolved_url)
            try:
                self._verify_llm_candidate_dict(
                    digest,
                    item,
                    prefetched_bundle=bundle,
                    filter_enabled=filter_enabled,
                )
            except TypeError:
                self._verify_llm_candidate_dict(digest, item, prefetched_bundle=bundle)
            return item

        if pending:
            logger.info(
                "Шаг 1: HTTP-проверка URL старт | digest_id=%s pending=%s workers=%s",
                digest.id,
                len(pending),
                min(workers, len(pending)),
            )
            if workers <= 1 or len(pending) == 1:
                verified_items = [_verify_one(w) for w in pending]
            else:
                verified_items = []
                with ThreadPoolExecutor(max_workers=min(workers, len(pending))) as pool:
                    futures = {pool.submit(_verify_one, w): w for w in pending}
                    for fut in as_completed(futures):
                        try:
                            verified_items.append(fut.result())
                        except Exception:
                            logger.exception("Шаг 1: ошибка параллельной верификации URL")
            for item in verified_items:
                if on_row is not None:
                    on_row(item)
                fp = _url_fingerprint(str(item.get("url") or ""))
                if item.get("headline_editorial_ok") and item.get("link_status"):
                    if len(verified) < limit:
                        verified.append(item)
                else:
                    if fp:
                        seen_fp.add(fp)
                    if register_reject is not None:
                        register_reject(item)
                if len(verified) >= limit:
                    break
        logger.info(
            "Шаг 1: HTTP-проверка URL завершена | digest_id=%s pending=%s verified=%s elapsed_sec=%s",
            digest.id,
            len(pending),
            len(verified),
            int(time.monotonic() - started_check),
        )
        phase_timers = current_step1_phase_timers()
        if phase_timers is not None:
            phase_timers.add("http_verify", time.monotonic() - started_check)
        if listing_child_total:
            logger.info(
                "Шаг 1: дети листингов до HTTP | digest_id=%s total=%s skipped_excluded=%s rejected_by_url_date=%s",
                digest.id,
                listing_child_total,
                listing_child_skipped_excluded,
                listing_child_rejected_by_url_date,
            )
        return verified

    def _step1_hard_stop_cost_rub(self) -> float:
        return max(1.0, float(getattr(self.settings, "step1_hard_stop_cost_rub", 100.0) or 100.0))

    def _step1_hard_cost_limit_hit(self, digest: Digest) -> bool:
        from app.services.step1_web_search_stats import step1_hard_cost_limit_exceeded

        return step1_hard_cost_limit_exceeded(
            proxyapi_spent_rub=self._digest_proxyapi_spent_rub(digest),
            hard_limit_rub=self._step1_hard_stop_cost_rub(),
        )

    def _step1_check_abort_spending(
        self,
        digest: Digest,
        *,
        verified_count: int,
        collection_target: int,
    ) -> str | None:
        if step1_is_cancelled(digest.id):
            return "user_cancelled"
        if self._step1_hard_cost_limit_hit(digest):
            if int(verified_count) >= int(collection_target):
                return "hard_cost_stop"
            return "hard_cost_stop"
        return None

    def _step1_max_cost_rub(self) -> float:
        base = float(getattr(self.settings, "step1_max_cost_rub", 50.0) or 50.0)
        policy = getattr(self, "_step1_curious_yield", None)
        if policy is not None:
            return min(base, float(policy.max_cost_rub))
        return base

    def _step1_web_search_api_cap_settings(self) -> tuple[int, int]:
        explicit = int(getattr(self.settings, "step1_max_web_search_api_calls", 0) or 0)
        bonus = max(0, int(getattr(self.settings, "step1_web_search_api_bonus_near_target", 10) or 10))
        if explicit > 0:
            return explicit, bonus
        max_cost = self._step1_max_cost_rub()
        # ~1.4 ₽/вызов: cap не должен превышать max_cost_rub, иначе tier-поиск съедает бюджет до verify.
        base = max(20, min(40, int(max_cost / 1.5)))
        return base, bonus

    def _effective_step1_web_search_api_cap(self, verified_count: int) -> int:
        base, bonus = self._step1_web_search_api_cap_settings()
        explicit = int(getattr(self.settings, "step1_max_web_search_api_calls", 0) or 0)
        if explicit > 0:
            return explicit
        min_target = STEP1_MIN_VERIFIED
        policy = getattr(self, "_step1_curious_yield", None)
        if policy is not None:
            min_target = int(policy.min_verified)
        cap = base + bonus if int(verified_count) >= max(1, min_target - 2) else base
        if int(verified_count) < min_target:
            shortfall = max(0, min_target - int(verified_count))
            cap = max(cap, base + bonus + shortfall * 3)
        ws_stats = current_step1_web_search_stats()
        if (
            ws_stats is not None
            and int(verified_count) < min_target
            and int(ws_stats.citation_urls) == 0
            and int(ws_stats.api_calls) >= cap
            and cap > 0
            and ws_stats.max_cost_rub is None
        ):
            # ProxyAPI часто отвечает без citation URL — даём ещё попытки на реестр/seed, не только на пустые ответы.
            cap += max(10, bonus)
            ws_stats.cap_hit = False
        return cap

    def _refresh_step1_web_search_api_cap(self, verified_count: int) -> None:
        explicit = int(getattr(self.settings, "step1_max_web_search_api_calls", 0) or 0)
        set_step1_web_search_api_cap(
            self._effective_step1_web_search_api_cap(verified_count),
            strict=explicit > 0,
        )

    def _step1_curious_max_search_batches(self, *, pool_shortfall: int, verified_pool_size: int) -> int:
        policy = getattr(self, "_step1_curious_yield", None)
        base = int(policy.max_search_batches if policy else 8)
        if base <= 1:
            return base
        if pool_shortfall >= 5:
            return min(base + 2, 12)
        if pool_shortfall > 0:
            return min(base + 1, 10)
        return base

    def _step1_web_search_api_cap_stop_reason(self, verified_count: int = 0) -> str | None:
        min_target = STEP1_MIN_VERIFIED
        policy = getattr(self, "_step1_curious_yield", None)
        if policy is not None:
            min_target = int(policy.min_verified)
        pool_shortfall = max(0, min_target - int(verified_count or 0))
        from app.services.step1_web_search_stats import (
            step1_web_search_api_cap_should_stop,
            step1_web_search_cost_budget_should_stop,
        )

        if step1_web_search_api_cap_should_stop(
            verified_count=int(verified_count or 0),
            min_verified=min_target,
            pool_shortfall=pool_shortfall,
        ):
            return "web_search_api_cap"
        if step1_web_search_cost_budget_should_stop(
            verified_count=int(verified_count or 0),
            min_verified=min_target,
            pool_shortfall=pool_shortfall,
        ):
            return "web_search_cost_budget"
        return None

    def _step1_log_curious_tone(
        self,
        *,
        url: str,
        title: str,
        corpus: str = "",
        stage: str = "verify",
        outcome: str,
        filter_code: str | None = None,
    ) -> None:
        audit = getattr(self, "_step1_curious_tone_audit", None)
        if audit is None:
            return
        audit.record(
            url=url,
            title=title,
            corpus=corpus,
            stage=stage,
            outcome=outcome,
            filter_code=filter_code,
        )

    def _verify_curious_candidate_dict(
        self,
        digest: Digest,
        item: dict[str, Any],
        prefetched_bundle: dict[str, Any] | None = None,
        filter_enabled: Any | None = None,
    ) -> None:
        """Verify для курьёзного выпуска: оптимизированный fail-fast после HTTP."""
        is_enabled = filter_enabled or _catalog_step1_filter_enabled
        if _manual_required_dict(item):
            return
        item["headline_editorial_ok"] = False
        item["page_verified"] = False
        item["link_status"] = False

        def _reject(code: str) -> None:
            item["link_status"] = False
            item["headline_editorial_ok"] = False
            item["page_verified"] = False
            _append_reject_reason(item, code)
            record_step1_filter_reject(code)

        original_url = str(item.get("url") or "").strip()
        if _is_placeholder_candidate_dict(item) and is_enabled("placeholder_candidate"):
            _reject("placeholder_candidate")
            return
        u = original_url
        if not u.startswith("http") and is_enabled("invalid_url"):
            _reject("invalid_url")
            return
        if url_suspected_hallucinated(u) and is_enabled("llm_hallucinated_url"):
            _reject("llm_hallucinated_url")
            return
        if is_search_noise_url(u) and is_enabled("news_listing_page"):
            _reject("news_listing_page")
            return
        if (
            (is_topic_pool_page_url(u) or is_listing_page_url(u) or is_step1_listing_seed_url(u))
            and not is_curated_list_url(u)
            and is_enabled("news_listing_page")
        ):
            _reject("news_listing_page")
            return
        if _is_social_embed_status_url(u) and is_enabled("non_article_page"):
            _reject("non_article_page")
            return
        tier, is_aggregator, reliability_status = classify_curious_source(u)
        if is_curious_blocked_host(u) and is_enabled("forbidden_media_source"):
            _reject("forbidden_media_source")
            return
        gray_zone = is_curious_gray_zone_source(u)
        if gray_zone:
            item["is_gray_zone_source"] = True
        if not is_curious_allowed_source(u) and is_enabled("non_policy_source"):
            _reject("non_policy_source")
            return
        item["tier"] = tier
        item["is_aggregator"] = is_aggregator
        item["is_foreign_agent"] = _is_foreign_agent_source(u)
        item["reliability_status"] = reliability_status

        bundle = prefetched_bundle if prefetched_bundle is not None else _fetch_article_page_bundle(u)
        if not bundle.get("ok"):
            if is_enabled("http_unreachable"):
                _reject("http_unreachable")
            self._step1_mark_host_unreachable(u)
            return

        stored = str(bundle.get("final_url") or bundle.get("display_url") or u).strip()
        _append_url_audit(item, "http_verify", original_url, stored)
        if _redirect_should_reject(original_url, stored, bundle) and is_enabled("url_redirect_mismatch"):
            _reject("url_redirect_mismatch")
            return
        item["url"] = stored[:1000]

        h = bundle.get("headline")
        if (not isinstance(h, str) or len(h.strip()) < 8) and is_enabled("non_article_page"):
            _reject("non_article_page")
            return
        final_title = self._ensure_russian_candidate_title(digest.id, stored, h)[:500]
        topic_excerpt = str(bundle.get("topic_corpus") or "")

        if is_enabled("news_listing_page") and is_editorial_listing_title(final_title):
            self._step1_log_curious_tone(
                url=stored,
                title=final_title,
                corpus=topic_excerpt,
                stage="verify",
                outcome="reject",
                filter_code="news_listing_page",
            )
            _reject("news_listing_page")
            return

        _apply_bundle_published_at(item, bundle, seed_url=original_url)
        date_reject = _published_at_window_reject_code(
            digest, str(item.get("published_at") or ""), stored, seed_url=original_url
        )
        if date_reject and is_enabled(date_reject):
            _reject(date_reject)
            return

        if is_enabled("off_topic_not_curious") and not passes_curious_pool_gate(
            final_title, topic_excerpt, url=stored
        ):
            self._step1_log_curious_tone(
                url=stored,
                title=final_title,
                corpus=topic_excerpt,
                stage="verify",
                outcome="reject",
                filter_code="off_topic_not_curious",
            )
            _reject("off_topic_not_curious")
            return

        tone = curious_tone_score(final_title, topic_excerpt)
        item["curious_tone_score"] = tone
        low_tone = not passes_curious_tone_gate(final_title, topic_excerpt, url=stored)
        if low_tone:
            item["curious_tone_low"] = True
        if gray_zone or is_curious_gray_zone_source(stored):
            item["is_gray_zone_source"] = True
            if low_tone or tone < 3:
                self._step1_log_curious_tone(
                    url=stored,
                    title=final_title,
                    corpus=topic_excerpt,
                    stage="verify",
                    outcome="reject",
                    filter_code="off_topic_not_curious",
                )
                _reject("off_topic_not_curious")
                return
        self._step1_log_curious_tone(
            url=stored,
            title=final_title,
            corpus=topic_excerpt,
            stage="verify",
            outcome="low_tone" if low_tone else "accept",
        )

        if (
            not _title_primarily_russian(final_title)
            and is_enabled("headline_low_quality")
            and not _curious_foreign_source_url(stored)
        ):
            _reject("headline_low_quality")
            return
        if (_headline_unusable_for_digest(final_title) or _headline_unusable_for_digest(h)) and is_enabled(
            "headline_low_quality"
        ):
            _reject("headline_low_quality")
            return

        stored_tier, stored_is_agg, stored_rel = classify_curious_source(stored)
        item["tier"] = stored_tier
        item["is_aggregator"] = stored_is_agg
        item["reliability_status"] = stored_rel
        if is_curious_aggregator_source(stored) or _is_aggregator_source(stored):
            _reject("aggregator_source")
            return

        if (
            (is_topic_pool_page_url(stored) or is_listing_page_url(stored) or is_step1_listing_seed_url(stored))
            and not is_curated_list_url(stored)
            and is_enabled("news_listing_page")
        ):
            _reject("news_listing_page")
            return
        if is_enabled("news_listing_page") and is_editorial_listing_title(final_title):
            _reject("news_listing_page")
            return
        if bundle.get("is_listing_page") and is_enabled("news_listing_page"):
            _reject("news_listing_page")
            return

        item["link_status"] = True
        if not _page_is_article_like(bundle) and is_enabled("no_article_markers"):
            _reject("no_article_markers")
            return

        if _is_support_documentation_url(stored) and is_enabled("support_documentation_page"):
            _reject("support_documentation_page")
            return

        if not _ai_digest_topic_matches(topic_excerpt, h) and is_enabled("off_topic_not_ai"):
            _reject("off_topic_not_ai")
            return

        promo_corpus = f"{final_title} {item.get('description', '')} {topic_excerpt}"
        material_form = _classify_material_form(item, extra=promo_corpus)
        if _should_reject_commercial_non_article(item, material_form, promo_corpus):
            reject_code = _commercial_non_article_reject_reason(item, material_form, promo_corpus)
            if is_enabled(reject_code):
                _reject(reject_code)
                return

        if is_enabled("recent_top5_repeat"):
            recent_fps = getattr(self, "_active_recent_top5_fps", set())
            repeat_fp = article_page_fingerprint(stored)
            if repeat_fp and repeat_fp in recent_fps:
                _reject("recent_top5_repeat")
                return

        _apply_source_policy_from_url(item, stored, curious_mode=True)
        if _is_foreign_agent_source(stored):
            item["is_foreign_agent"] = True
            vc = str(item.get("verification_comment") or "")
            if "ИНОСТРАННЫЙ_АГЕНТ" not in vc:
                item["verification_comment"] = f"{vc} МАРКИРОВКА: ИНОСТРАННЫЙ_АГЕНТ".strip()

        item["title"] = final_title
        date_reject = _published_at_window_reject_code(
            digest, str(item.get("published_at") or ""), stored, seed_url=original_url
        )
        if date_reject and is_enabled(date_reject):
            _reject(date_reject)
            return

        _apply_material_form_to_candidate(item, extra=promo_corpus, digest_type=digest.digest_type)
        item["headline_editorial_ok"] = True
        item["page_verified"] = True
        item["article_excerpt"] = _truncate_article_excerpt(topic_excerpt)
        item["total_score"] = curious_total_score_from_tone(
            int(item.get("curious_tone_score", 0) or 0),
            low=bool(item.get("curious_tone_low")),
        )
        _normalize_candidate_source(item)

    def _verify_llm_candidate_dict(
        self,
        digest: Digest,
        item: dict[str, Any],
        prefetched_bundle: dict[str, Any] | None = None,
        filter_enabled: Any | None = None,
    ) -> None:
        """Нормализация URL и заголовка по HTML.

        headline_editorial_ok — читаемый редакционный заголовок (можно выбирать в топ-5).
        link_status — ссылка отвечает и проходит проверку доступности.
        page_verified — оба условия одновременно (совместимость с API/старыми клиентами).
        """
        is_enabled = filter_enabled or _catalog_step1_filter_enabled
        if _manual_required_dict(item):
            return
        item["headline_editorial_ok"] = False
        item["page_verified"] = False
        item["link_status"] = False
        original_url = str(item.get("url") or "").strip()
        if _is_placeholder_candidate_dict(item) and is_enabled("placeholder_candidate"):
            item["link_status"] = False
            _append_reject_reason(item, "placeholder_candidate")
            return
        u = original_url
        if not u.startswith("http") and is_enabled("invalid_url"):
            item["link_status"] = False
            _append_reject_reason(item, "invalid_url")
            return
        if url_suspected_hallucinated(u) and is_enabled("llm_hallucinated_url"):
            item["link_status"] = False
            _append_reject_reason(item, "llm_hallucinated_url")
            return
        if is_search_noise_url(u) and is_enabled("news_listing_page"):
            item["link_status"] = False
            _append_reject_reason(item, "news_listing_page")
            return
        if (
            (is_topic_pool_page_url(u) or is_listing_page_url(u) or is_step1_listing_seed_url(u))
            and not is_curated_list_url(u)
            and is_enabled("news_listing_page")
        ):
            item["link_status"] = False
            _append_reject_reason(item, "news_listing_page")
            return
        if _is_social_embed_status_url(u) and is_enabled("non_article_page"):
            item["link_status"] = False
            _append_reject_reason(item, "non_article_page")
            return
        curious_mode = getattr(self, "_step1_curious_mode", False) is True
        curious_serious_tiers = curious_mode and bool(
            getattr(self.settings, "step1_curious_use_serious_tiers", False)
        )
        if curious_mode and not curious_serious_tiers:
            self._verify_curious_candidate_dict(
                digest,
                item,
                prefetched_bundle=prefetched_bundle,
                filter_enabled=filter_enabled,
            )
            return
        else:
            serious_curious_tiers = bool(getattr(self.settings, "step1_serious_use_curious_tiers", True))
            if (
                not curious_mode
                and serious_curious_tiers
                and is_curious_policy_source(u)
                and not is_policy_tier_source(u)
            ):
                tier, is_aggregator, reliability_status = classify_curious_source(u)
            else:
                tier, is_aggregator, reliability_status = _classify_source_policy(u)
            if _is_tier5_forbidden_source(u) and is_enabled("forbidden_media_source"):
                item["link_status"] = False
                _append_reject_reason(item, "forbidden_media_source")
                return
            if is_blocked_search_host(u) and is_enabled("forbidden_media_source"):
                item["link_status"] = False
                _append_reject_reason(item, "forbidden_media_source")
                return
        item["tier"] = tier
        item["is_aggregator"] = is_aggregator
        item["is_foreign_agent"] = _is_foreign_agent_source(u)
        item["reliability_status"] = reliability_status
        if curious_mode and is_curious_aggregator_source(u) and is_enabled("aggregator_source"):
            item["is_aggregator"] = True
        if is_aggregator and is_enabled("aggregator_source"):
            item["link_status"] = False
            _append_reject_reason(item, "aggregator_source")
            return
        bundle = prefetched_bundle if prefetched_bundle is not None else _fetch_article_page_bundle(u)
        if not bundle.get("ok"):
            item["link_status"] = False
            _append_reject_reason(item, "http_unreachable")
            self._step1_mark_host_unreachable(u)
            return
        stored = str(bundle.get("final_url") or bundle.get("display_url") or u).strip()
        _append_url_audit(item, "http_verify", original_url, stored)
        if _redirect_should_reject(original_url, stored, bundle) and is_enabled("url_redirect_mismatch"):
            _append_reject_reason(item, "url_redirect_mismatch")
            item["link_status"] = False
            return
        item["url"] = stored[:1000]
        if is_enabled("recent_top5_repeat"):
            recent_fps = getattr(self, "_active_recent_top5_fps", set())
            repeat_fp = article_page_fingerprint(stored)
            if repeat_fp and repeat_fp in recent_fps:
                item["link_status"] = False
                _append_reject_reason(item, "recent_top5_repeat")
                return
        _apply_source_policy_from_url(item, stored, curious_mode=curious_mode)
        if _is_foreign_agent_source(stored):
            item["is_foreign_agent"] = True
            vc = str(item.get("verification_comment") or "")
            if "ИНОСТРАННЫЙ_АГЕНТ" not in vc:
                item["verification_comment"] = f"{vc} МАРКИРОВКА: ИНОСТРАННЫЙ_АГЕНТ".strip()
        if (
            (is_topic_pool_page_url(stored) or is_listing_page_url(stored) or is_step1_listing_seed_url(stored))
            and not is_curated_list_url(stored)
            and is_enabled("news_listing_page")
        ):
            item["link_status"] = False
            _append_reject_reason(item, "news_listing_page")
            return
        if bundle.get("is_listing_page") and is_enabled("news_listing_page"):
            item["link_status"] = False
            _append_reject_reason(item, "news_listing_page")
            return
        # Страница открылась по HTTP — ссылка рабочая (отдельный HEAD не делаем: даёт ложные 403/405).
        item["link_status"] = True
        if not _page_is_article_like(bundle) and is_enabled("no_article_markers"):
            item["link_status"] = False
            _append_reject_reason(item, "no_article_markers")
            return
        h = bundle.get("headline")
        if (not isinstance(h, str) or len(h.strip()) < 8) and is_enabled("non_article_page"):
            item["link_status"] = False
            _append_reject_reason(item, "non_article_page")
            return
        if _is_support_documentation_url(stored) and is_enabled("support_documentation_page"):
            item["link_status"] = False
            _append_reject_reason(item, "support_documentation_page")
            return
        if not _ai_digest_topic_matches(str(bundle.get("topic_corpus") or ""), h) and is_enabled("off_topic_not_ai"):
            item["link_status"] = False
            _append_reject_reason(item, "off_topic_not_ai")
            return
        final_title = self._ensure_russian_candidate_title(digest.id, stored, h)[:500]
        topic_excerpt = str(bundle.get("topic_corpus") or "")
        if curious_mode:
            tone = curious_tone_score(final_title, topic_excerpt)
            item["curious_tone_score"] = tone
            if is_enabled("off_topic_not_curious") and not passes_curious_pool_gate(
                final_title, topic_excerpt, url=stored
            ):
                item["link_status"] = False
                _append_reject_reason(item, "off_topic_not_curious")
                return
            tone = curious_tone_score(final_title, topic_excerpt)
            item["curious_tone_score"] = tone
            if not passes_curious_tone_gate(final_title, topic_excerpt, url=stored):
                item["curious_tone_low"] = True
        if (
            curious_mode
            and not _title_primarily_russian(final_title)
            and is_enabled("headline_low_quality")
            and not _curious_foreign_source_url(stored)
        ):
            item["link_status"] = False
            _append_reject_reason(item, "headline_low_quality")
            return
        if (_headline_unusable_for_digest(final_title) or _headline_unusable_for_digest(h)) and is_enabled(
            "headline_low_quality"
        ):
            item["link_status"] = False
            _append_reject_reason(item, "headline_low_quality")
            return
        _apply_bundle_published_at(item, bundle, seed_url=original_url)
        date_reject = _published_at_window_reject_code(
            digest, str(item.get("published_at") or ""), stored, seed_url=original_url
        )
        if date_reject and is_enabled(date_reject):
            item["link_status"] = False
            _append_reject_reason(item, date_reject)
            return
        item["title"] = final_title
        promo_corpus = f"{final_title} {item.get('description', '')} {bundle.get('topic_corpus', '')}"
        material_form = _classify_material_form(item, extra=promo_corpus)
        if _should_reject_commercial_non_article(item, material_form, promo_corpus):
            reject_code = _commercial_non_article_reject_reason(item, material_form, promo_corpus)
            if is_enabled(reject_code):
                item["link_status"] = False
                _append_reject_reason(item, reject_code)
                return
        _apply_material_form_to_candidate(item, extra=promo_corpus, digest_type=digest.digest_type)
        item["headline_editorial_ok"] = True
        item["page_verified"] = True
        item["article_excerpt"] = _truncate_article_excerpt(topic_excerpt)
        if curious_mode:
            item["total_score"] = curious_total_score_from_tone(
                int(item.get("curious_tone_score", 0) or 0),
                low=bool(item.get("curious_tone_low")),
            )
        _normalize_candidate_source(item)

    def _filter_score_url_mutations(
        self,
        verify_rows: list[dict[str, Any]],
        scored_rows: list[dict[str, Any]],
        *,
        filter_enabled: Any | None = None,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Скоринг не может менять URL: баллы с scored_rows, идентичность — из verify."""
        score_only_fields = (
            "significance_score",
            "novelty_score",
            "impact_score",
            "total_score",
            "description",
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
        is_enabled = filter_enabled or _catalog_step1_filter_enabled
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
            if is_enabled("url_mutated_between_agents"):
                _append_reject_reason(item, "url_mutated_between_agents")
                _append_url_audit(item, "score_guard", "<not_in_verify>", score_url or "<missing>")
                item["link_status"] = False
                rejected.append(item)
            else:
                accepted.append(item)
        return accepted, rejected

    def _step1_fetch_supplementary_dicts(
        self,
        digest: Digest,
        seen_fp: set[str],
        excluded_urls: list[str],
        now_msk: str,
        need_hint: int,
        *,
        exclude_hosts: set[str] | None = None,
        filter_enabled: Any | None = None,
    ) -> list[dict[str, Any]]:
        search_route = resolve_step1_search_routing(
            digest.digest_type,
            query_override=None,
            tier_strict_setting=bool(getattr(self.settings, "step1_tier_strict_search", True)),
            curious_use_serious_tiers=bool(getattr(self.settings, "step1_curious_use_serious_tiers", False)),
        )
        tier_strict = search_route.tier_strict
        curious_strict = search_route.curious_strict
        supplement_ctx = str(
            getattr(self.settings, "proxyapi_web_search_context_size_supplement", "low") or "low"
        ).lower()
        fetch_limit = max(need_hint * 3, 14)
        prefilter_order = self._step1_stage_filter_order(
            "pre_http",
            list(CURIOUS_PREFILTER_DEFAULT_ORDER)
            if curious_strict
            else [
                "invalid_url",
                "duplicate_url_skip",
                "recent_top5_repeat",
                "non_policy_source",
                "aggregator_source",
                "forbidden_media_source",
                "news_listing_page",
                "llm_hallucinated_url",
                "product_tool_page",
                "published_before_window",
                "published_date_undefined",
            ],
        )
        saturated = set()

        if search_route.uses_curious_hosts:
            window_hint, topic_terms, product_excludes = self._step1_search_query_parts(digest)
            raw_urls = fetch_curious_prioritized_raw_urls(
                self.settings,
                window_prefix=window_hint,
                topic_terms_ru=topic_terms,
                topic_terms_foreign=step1_curious_foreign_topic_terms(),
                product_excludes=product_excludes,
                fetch_limit=fetch_limit,
                proxy=self.proxy,
                search_context_size=supplement_ctx,
            )
            urls: list[str] = []
            for u in raw_urls:
                pre_reason = search_url_prefilter_reason(
                    u,
                    is_enabled=filter_enabled,
                    order=prefilter_order,
                    curious_strict=True,
                    earliest=digest_earliest_news_date(digest),
                    anchor=digest_news_anchor_date(digest),
                )
                if not pre_reason:
                    pre_reason = self._recent_top5_repeat_reason(u)
                if pre_reason and pre_reason != "aggregator_source":
                    continue
                urls.append(u)
        elif search_route.uses_source_tiers:
            window_hint, topic_terms, product_excludes = self._step1_search_query_parts(digest)
            policy = get_source_tiers_policy(self.settings.source_tiers_path)
            raw_urls = fetch_tier_prioritized_raw_urls(
                self.settings,
                window_prefix=window_hint,
                topic_terms=topic_terms,
                product_excludes=product_excludes,
                fetch_limit=fetch_limit,
                proxy=self.proxy,
                search_context_size=supplement_ctx,
                policy=policy,
                current_verified=0,
                pool_shortfall=STEP1_MIN_VERIFIED,
            )
            urls: list[str] = []
            for u in raw_urls:
                pre_reason = search_url_prefilter_reason(
                    u,
                    is_enabled=filter_enabled,
                    order=prefilter_order,
                    tier_strict=True,
                    earliest=digest_earliest_news_date(digest),
                    anchor=digest_news_anchor_date(digest),
                )
                if not pre_reason:
                    pre_reason = self._recent_top5_repeat_reason(u)
                if pre_reason:
                    continue
                urls.append(u)
        else:
            query = self._step1_search_query(digest)
            urls = fetch_article_urls_from_search(
                self.settings,
                query,
                limit=fetch_limit,
                proxy=self.proxy,
                search_context_size=supplement_ctx,
                is_enabled=filter_enabled,
                order=prefilter_order,
            )
        out: list[dict[str, Any]] = []
        seq = 900
        for u in urls:
            if self._recent_top5_repeat_reason(u):
                continue
            fp = _url_fingerprint(u)
            if not fp or fp in seen_fp:
                continue
            out.append(self._skeleton_dict_from_search_url(u, now_msk, seq))
            seq += 1
            if len(out) >= 24:
                break
        # Возвращаем только URL, пришедшие из web-search провайдеров.
        # LLM-refill здесь создавал синтетические/неоткрываемые ссылки
        # и ломал partial rebuild при наличии ручных новостей.
        return out

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

    def _is_manual_required_candidate(
        self,
        verification_comment: str | None,
        description: str | None = None,
    ) -> bool:
        """Обязательны ссылки, вставленные пользователем в поле URL на шаге 1 или 2."""
        comment = str(verification_comment or "")
        desc = str(description or "")
        if "TELEGRAM_SEED:" in comment or "Telegram-монитор" in desc:
            return False
        return "поле URL на шаге 1" in desc or "поле URL на шаге 2" in desc
