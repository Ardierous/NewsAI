import json
import logging
import html
import re
import time
from datetime import date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse, urljoin
from zoneinfo import ZoneInfo

import requests
from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import get_settings
from app.crew.model_policy import AGENT_MODEL_RECOMMENDATIONS, PRICING_RUB
from app.crew.workflow import CrewWorkflow, current_msk_iso
from app.models import Analytics, Asset, Digest, FinalOutput, LlmCostRecord, NewsCandidate, QualityCheck, SelectedNews
from app.proxyapi_client import ProxyApiClient
from app.services.cost_tracker import ProxyApiCostTracker
from app.services.export_service import build_docx
from app.services.news_search import fetch_article_urls_from_search

logger = logging.getLogger("app.digest")
MSK_TZ = ZoneInfo("Europe/Moscow")

STATUS_DRAFT = "draft"
STATUS_STEP0 = "step_0"
STATUS_STEP1 = "step_1_candidates"
STATUS_SELECTED = "selected"
STATUS_ANALYTICS = "analytics_ready"
STATUS_FINAL = "final_ready"

STEP1_TARGET_VERIFIED = 10
STEP1_MIN_VERIFIED = 5
STEP1_SUPPLEMENT_MAX_ROUNDS = 5

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
        return {
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

    def build_budget_notices(self, digest: Digest) -> list[str]:
        """Человекочитаемые предупреждения о лимите расходов для UI."""
        notices: list[str] = []
        if digest.step1_budget_capped:
            spent = self._digest_step_llm_cost_sum(digest.id, "step_1")
            lim = self.settings.step1_max_cost_rub
            notices.append(
                f"Достигнут лимит расходов на сбор кандидатов ({lim:g} ₽). По учёту ProxyAPI на этом шаге ~{spent:.2f} ₽; "
                "дополнительный добор новостей через ИИ остановлен — список мог быть короче 10 позиций. "
                "При необходимости увеличьте лимит в настройках сервера (STEP1_MAX_COST_RUB) или задайте прямые ссылки на статьи."
            )
        if digest.step2_budget_capped:
            spent2 = self._digest_step_llm_cost_sum(digest.id, "step_2")
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
        digest = Digest(date=today, status=STATUS_DRAFT, current_step=STATUS_DRAFT)
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

    def run_step_0(self, digest_id: int, digest_type: str | None) -> Digest:
        digest = self.get_digest(digest_id)
        via_default = digest_type is None
        if via_default:
            weekday = datetime.now(MSK_TZ).weekday()
            digest_type = "serious" if weekday < 5 else "curious"
        if digest_type not in {"serious", "curious"}:
            raise HTTPException(status_code=400, detail="digest_type must be serious or curious")
        digest.digest_type = digest_type
        digest.digest_type_via_default = via_default
        digest.status = STATUS_STEP0
        digest.current_step = STATUS_STEP0
        self.db.commit()
        self.db.refresh(digest)
        logger.info("Шаг 0: тип дайджеста | digest_id=%s type=%s", digest.id, digest_type)
        return digest

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
                published_at=str(item.get("published_at", ""))[:100] or "1970-01-01T00:00:00",
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

    def run_step_1(self, digest_id: int, manual_urls: list[str]) -> list[NewsCandidate]:
        digest = self.get_digest(digest_id)
        if digest.status not in {STATUS_STEP0, STATUS_STEP1}:
            raise HTTPException(status_code=400, detail="Step 1 requires step_0")

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
        self.db.query(LlmCostRecord).filter(LlmCostRecord.digest_id == digest.id).delete()
        self.db.query(NewsCandidate).filter(NewsCandidate.digest_id == digest.id).delete()

        digest.step1_budget_capped = False
        digest.step2_budget_capped = False

        logger.info(
            "Шаг 1: запуск сбора кандидатов | digest_id=%s manual_urls=%s",
            digest.id,
            len(normalized_manual_urls),
        )
        now_msk = current_msk_iso()
        candidates: list[dict[str, Any]] = []
        web_flow_available = self.settings.enable_web_fetch
        preview_by_fp: dict[str, dict[str, Any]] = {}

        def snapshot_preview_row(row: dict[str, Any]) -> None:
            fp = _url_fingerprint(str(row.get("url", "")).strip())
            if fp:
                preview_by_fp[fp] = dict(row)

        if self.settings.enable_web_fetch:
            try:
                research_raw, research_cost = self.cost_tracker.measure(
                    lambda: self.workflow.run_candidates_research(
                        digest_type=digest.digest_type or "serious",
                        now_msk=now_msk,
                        manual_urls=normalized_manual_urls,
                    ),
                    source="step1_news_research",
                )
                self._save_cost(
                    digest_id=digest.id,
                    step="step_1",
                    agent_name="NewsResearchAgent",
                    model=AGENT_MODEL_RECOMMENDATIONS["NewsResearchAgent"],
                    request_label="run_candidates_research",
                    cost_rub=research_cost.cost_rub,
                )
                verify_raw, verify_cost = self.cost_tracker.measure(
                    lambda: self.workflow.run_candidates_verify(research_raw),
                    source="step1_source_verification",
                )
                self._save_cost(
                    digest_id=digest.id,
                    step="step_1",
                    agent_name="SourceVerificationAgent",
                    model=AGENT_MODEL_RECOMMENDATIONS["SourceVerificationAgent"],
                    request_label="run_candidates_verify",
                    cost_rub=verify_cost.cost_rub,
                )
                candidates, score_cost = self.cost_tracker.measure(
                    lambda: self.workflow.run_candidates_score(verify_raw, now_msk=now_msk),
                    source="step1_scoring",
                )
                self._save_cost(
                    digest_id=digest.id,
                    step="step_1",
                    agent_name="ScoringAgent",
                    model=AGENT_MODEL_RECOMMENDATIONS["ScoringAgent"],
                    request_label="run_candidates_score",
                    cost_rub=score_cost.cost_rub,
                )
            except Exception:
                web_flow_available = False
                logger.exception("Шаг 1: веб-поиск недоступен, переключение на ручные ссылки")
                if not normalized_manual_urls:
                    raise HTTPException(
                        status_code=400,
                        detail="Веб-поиск временно недоступен. Вставьте вручную 5-10 ссылок в поле manual_urls.",
                    )

        manual_candidates = self._build_manual_candidates(digest.id, normalized_manual_urls, now_msk)
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
            # Нужна видимость причин даже при 502: записываем сводку до raise.
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

        llm_merged = self._merge_candidates([], candidates, limit=40) if candidates else []
        for item in llm_merged:
            if _is_placeholder_candidate_dict(item):
                continue
            raw_u = str(item.get("url") or "").strip()
            if not raw_u.startswith("http"):
                continue
            if _url_fingerprint(raw_u) in seen_fp:
                continue
            self._verify_llm_candidate_dict(digest.id, item)
            snapshot_preview_row(item)
            if item.get("headline_editorial_ok") and item.get("link_status"):
                append_verified(item)
            else:
                excluded_urls.append(raw_u[:800])
                register_reject(item)

        sup_round = 0
        while len(verified_pool) < STEP1_TARGET_VERIFIED and sup_round < STEP1_SUPPLEMENT_MAX_ROUNDS:
            sup_round += 1
            need = STEP1_TARGET_VERIFIED - len(verified_pool)
            if need <= 0:
                break
            spent_step1 = self._digest_step_llm_cost_sum(digest.id, "step_1")
            if spent_step1 >= self.settings.step1_max_cost_rub:
                logger.warning(
                    "Шаг 1: добор остановлен — лимит LLM step_1_max_cost_rub (%s ₽) | digest_id=%s spent=%.4f verified=%s",
                    self.settings.step1_max_cost_rub,
                    digest.id,
                    spent_step1,
                    len(verified_pool),
                )
                break
            extra = self._step1_fetch_supplementary_dicts(digest, seen_fp, excluded_urls, now_msk, need)
            if not extra:
                logger.warning(
                    "Шаг 1: добор кандидатов без результата | digest_id=%s round=%s",
                    digest.id,
                    sup_round,
                )
                continue
            for item in extra:
                raw_u = str(item.get("url") or "").strip()
                if not raw_u.startswith("http"):
                    continue
                if _url_fingerprint(raw_u) in seen_fp:
                    continue
                self._verify_llm_candidate_dict(digest.id, item)
                snapshot_preview_row(item)
                if item.get("headline_editorial_ok") and item.get("link_status"):
                    append_verified(item)
                else:
                    excluded_urls.append(raw_u[:800])
                    register_reject(item)

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
            self._persist_step1_preview_candidates(digest.id, preview_by_fp)
            spent_step1 = self._digest_step_llm_cost_sum(digest.id, "step_1")
            tail = (
                f" Сработал лимит расходов на сбор кандидатов ({self.settings.step1_max_cost_rub:g} ₽): по учёту ProxyAPI ~{spent_step1:.2f} ₽. "
                "Увеличьте STEP1_MAX_COST_RUB в backend/.env или добавьте прямые URL статей."
                if spent_step1 >= self.settings.step1_max_cost_rub - 1e-9
                else ""
            )
            search_hint = (
                " Поиск без ручных URL работает только при наличии SERPAPI_API_KEY или TAVILY_API_KEY в backend/.env."
                if not normalized_manual_urls and not (self.settings.serpapi_api_key or self.settings.tavily_api_key)
                else ""
            )
            raise HTTPException(
                status_code=502,
                detail=(
                    f"Подтверждено только {len(verified_pool)} материалов по страницам (нужно минимум {STEP1_MIN_VERIFIED}). "
                    "Добавьте прямые URL статей в поле шага 1 или укажите SERPAPI_API_KEY или TAVILY_API_KEY в backend/.env для добора ссылок из поиска."
                    + top_reject_reasons()
                    + search_hint
                    + tail
                ),
            )

        # Кандидатов удаляем только перед успешной записью: промежуточные commit в _save_cost не должны оставлять пустой список при 502.
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
                link_status=bool(item.get("link_status", True)),
                headline_editorial_ok=bool(item.get("headline_editorial_ok", False)),
                page_verified=bool(item.get("headline_editorial_ok", False)) and bool(item.get("link_status", False)),
                is_foreign_agent=bool(item.get("is_foreign_agent", False)),
                is_aggregator=bool(item.get("is_aggregator", False)),
                is_duplicate=is_duplicate,
                verification_comment=str(item.get("verification_comment", "")),
            )
            entities.append(entity)
            self.db.add(entity)

        spent_step1_final = self._digest_step_llm_cost_sum(digest.id, "step_1")
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

        spent_step2_before = self._digest_step_llm_cost_sum(digest.id, "step_2")
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
            agent_order, measure = self.cost_tracker.measure(
                lambda: self.workflow.run_ordering(order_payload),
                source="step2_ordering",
            )
            self._save_cost(
                digest_id=digest.id,
                step="step_2",
                agent_name="OrderingAgent",
                model=AGENT_MODEL_RECOMMENDATIONS["OrderingAgent"],
                request_label="run_ordering",
                cost_rub=measure.cost_rub,
            )
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
        return ordered

    def run_step_3_analytics(self, digest_id: int, command: str) -> dict[str, Any]:
        digest = self.get_digest(digest_id)
        cmd = (command or "").strip().lower()
        if cmd and cmd != "готово":
            raise HTTPException(
                status_code=400,
                detail='Запустите аналитику кнопкой ниже или введите команду «готово».',
            )
        if digest.status != STATUS_SELECTED:
            raise HTTPException(status_code=400, detail="Step 3 requires selected status")

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
        result, measure = self.cost_tracker.measure(
            lambda: self.workflow.run_analytics(payload),
            source="step3_analytics",
        )
        self._save_cost(
            digest_id=digest.id,
            step="step_3",
            agent_name="AnalyticsAgent",
            model=AGENT_MODEL_RECOMMENDATIONS["AnalyticsAgent"],
            request_label="run_analytics",
            cost_rub=measure.cost_rub,
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

        digest.status = STATUS_ANALYTICS
        digest.current_step = STATUS_ANALYTICS
        self.db.commit()
        logger.info("Шаг 3: готово | digest_id=%s analytics_rows=%s", digest.id, len(result.get("items", [])))
        return result

    def run_step_4_final(self, digest_id: int, command: str, hook_variant: str | None) -> dict[str, Any]:
        digest = self.get_digest(digest_id)
        if command.strip().lower() not in {"ок", "ok"}:
            raise HTTPException(status_code=400, detail='Для шага 4 нужно ввести команду "Ок"')
        if digest.status != STATUS_ANALYTICS:
            raise HTTPException(status_code=400, detail="Step 4 requires analytics_ready")

        rotation = ["A", "B", "V"]
        hook = hook_variant if hook_variant in rotation else rotation[digest.id % 3]
        logger.info("Шаг 4: финальная сборка | digest_id=%s hook=%s", digest.id, hook)
        analytics_rows = self.db.query(Analytics).filter(Analytics.digest_id == digest.id).all()
        selected_rows = (
            self.db.query(SelectedNews)
            .filter(SelectedNews.digest_id == digest.id)
            .order_by(SelectedNews.output_position.asc())
            .all()
        )
        selected_payload = []
        for row in selected_rows:
            candidate = self.db.query(NewsCandidate).filter(NewsCandidate.id == row.candidate_id).first()
            analytics = next((a for a in analytics_rows if a.candidate_id == row.candidate_id), None)
            if not candidate or not analytics:
                continue
            selected_payload.append(
                {
                    "title": candidate.title,
                    "url": candidate.url,
                    "source": candidate.source,
                    "summary": f"{analytics.essence} {analytics.analysis}",
                }
            )

        hashtags_asset = (
            self.db.query(Asset).filter(Asset.digest_id == digest.id, Asset.type == "hashtags").order_by(Asset.id.desc()).first()
        )
        hashtags = hashtags_asset.prompt.split() if hashtags_asset and hashtags_asset.prompt else ["#ИИ", "#AI"]
        image_prompt, prompt_measure = self.cost_tracker.measure(
            lambda: self.workflow.run_image_prompt(hook, selected_payload),
            source="step4_image_prompt",
        )
        self._save_cost(
            digest_id=digest.id,
            step="step_4",
            agent_name="ImagePromptAgent",
            model=AGENT_MODEL_RECOMMENDATIONS["ImagePromptAgent"],
            request_label="run_image_prompt",
            cost_rub=prompt_measure.cost_rub,
        )
        image_path = self.settings.image_dir / f"digest_{digest.id}.png"
        _, image_measure = self.cost_tracker.measure(
            lambda: self.proxy.generate_image(image_prompt, image_path),
            source="step4_image_generate",
        )
        self._save_cost(
            digest_id=digest.id,
            step="step_4",
            agent_name="ImagePromptAgent",
            model=self.settings.proxyapi_image_model,
            request_label="generate_image",
            cost_rub=image_measure.cost_rub,
        )
        self.db.add(Asset(digest_id=digest.id, type="image", path=str(image_path), prompt=image_prompt))

        outputs, writer_measure = self.cost_tracker.measure(
            lambda: self.workflow.run_platform_writer(
                {
                    "hook_variant": hook,
                    "selected_news": selected_payload,
                    "hashtags": hashtags,
                    "date": digest.date.isoformat(),
                }
            ),
            source="step4_platform_writer",
        )
        self._save_cost(
            digest_id=digest.id,
            step="step_4",
            agent_name="PlatformWriterAgent",
            model=AGENT_MODEL_RECOMMENDATIONS["PlatformWriterAgent"],
            request_label="run_platform_writer",
            cost_rub=writer_measure.cost_rub,
        )

        self.db.query(FinalOutput).filter(FinalOutput.digest_id == digest.id).delete()
        for platform, content in outputs.items():
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

        checks, qc_measure = self.cost_tracker.measure(
            lambda: self.workflow.run_qc(outputs, has_ok=True),
            source="step4_qc",
        )
        self._save_cost(
            digest_id=digest.id,
            step="step_4",
            agent_name="QualityControlAgent",
            model=AGENT_MODEL_RECOMMENDATIONS["QualityControlAgent"],
            request_label="run_qc",
            cost_rub=qc_measure.cost_rub,
        )
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
            regenerated, repair_measure = self.cost_tracker.measure(
                lambda: self.workflow.run_platform_writer(
                    {
                        "hook_variant": hook,
                        "selected_news": selected_payload,
                        "hashtags": hashtags,
                        "fix_mode": True,
                    }
                ),
                source="step4_platform_writer_repair",
            )
            self._save_cost(
                digest_id=digest.id,
                step="step_4",
                agent_name="PlatformWriterAgent",
                model=AGENT_MODEL_RECOMMENDATIONS["PlatformWriterAgent"],
                request_label="run_platform_writer_repair",
                cost_rub=repair_measure.cost_rub,
            )
            for row in self.db.query(FinalOutput).filter(FinalOutput.digest_id == digest.id).all():
                row.content = regenerated.get(row.platform, row.content)
                row.character_count = len(row.content)
                row.qc_status = "repaired"
            self.db.commit()

        digest.status = STATUS_FINAL
        digest.current_step = STATUS_FINAL
        self.db.commit()

        docx_path = self.settings.docx_dir / f"digest_{digest.id}.docx"
        build_docx(self.db, digest, docx_path)
        self.db.add(Asset(digest_id=digest.id, type="docx", path=str(docx_path), prompt="final export"))
        self.db.commit()
        logger.info(
            "Шаг 4: завершено | digest_id=%s image=%s docx=%s",
            digest.id,
            image_path.name,
            docx_path.name,
        )
        return {"hook_variant": hook, "image_path": str(image_path), "docx_path": str(docx_path)}

    def _check_url(self, url: str) -> bool:
        if not url.startswith("http"):
            return False
        try:
            response = requests.head(url, timeout=5, allow_redirects=True)
            if response.status_code < 400:
                return True
            response = requests.get(url, timeout=5, allow_redirects=True)
            return response.status_code < 400
        except Exception:
            return False

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

            translated, measure = self.cost_tracker.measure(do, source="step1_manual_title_translate")
            self._save_cost(
                digest_id=digest_id,
                step="step_1",
                agent_name="ScoringAgent",
                model=model,
                request_label="manual_headline_translate_ru",
                cost_rub=measure.cost_rub,
            )
            line = translated.strip().splitlines()[0].strip().strip("'\"«»„“")
            if len(line) >= 6:
                return line[:500]
            return t[:500]
        except Exception:
            logger.warning("Перевод заголовка пропущен (ошибка API) | url=%s", url[:100], exc_info=True)
            return t[:500]

    def _build_manual_candidates(self, digest_id: int, manual_urls: list[str], now_msk: str) -> list[dict[str, Any]]:
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
                title = self._ensure_russian_candidate_title(digest_id, stored, raw_headline)
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
            link_ok = self._check_url(stored)
            headline_editorial_ok = bool(bundle.get("ok") and link_ok and raw_headline)
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
                    "published_at": now_msk,
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
            "published_at": now_msk,
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

    def _verify_llm_candidate_dict(self, digest_id: int, item: dict[str, Any]) -> None:
        """Нормализация URL и заголовка по HTML.

        headline_editorial_ok — читаемый редакционный заголовок (можно выбирать в топ-5).
        link_status — ссылка отвечает и проходит проверку доступности.
        page_verified — оба условия одновременно (совместимость с API/старыми клиентами).
        """
        if _manual_required_dict(item):
            return
        item["headline_editorial_ok"] = False
        item["page_verified"] = False
        item["link_status"] = bool(item.get("link_status", False))
        if _is_placeholder_candidate_dict(item):
            item["link_status"] = False
            _append_reject_reason(item, "placeholder_candidate")
            return
        u = str(item.get("url") or "").strip()
        if not u.startswith("http"):
            item["link_status"] = False
            _append_reject_reason(item, "invalid_url")
            return
        tier, is_aggregator, reliability_status = _classify_source_policy(u)
        item["tier"] = tier
        item["is_aggregator"] = is_aggregator
        item["reliability_status"] = reliability_status
        if is_aggregator:
            item["link_status"] = False
            _append_reject_reason(item, "aggregator_source")
            return
        bundle = _fetch_article_page_bundle(u)
        if not bundle.get("ok"):
            item["link_status"] = False
            _append_reject_reason(item, "http_unreachable")
            return
        stored = str(bundle.get("final_url") or bundle.get("display_url") or u).strip()
        item["url"] = stored[:1000]
        # HTML уже успешно загружен в bundle — отдельный HEAD часто даёт ложный отказ (403/405).
        item["link_status"] = True
        if not bool(bundle.get("article_markers")) and not bool(bundle.get("soft_article_signals")):
            _append_reject_reason(item, "no_article_markers")
            return
        h = bundle.get("headline")
        if not isinstance(h, str) or len(h.strip()) < 8:
            _append_reject_reason(item, "non_article_page")
            return
        if not _ai_digest_topic_matches(str(bundle.get("topic_corpus") or ""), h):
            _append_reject_reason(item, "off_topic_not_ai")
            return
        # Раньше требовали headline_strict (совпадение URL с og:url / JSON-LD). У многих статей og:title и h1 есть,
        # а og:url отсутствует или ведёт на другой вариант URL — из-за этого отваливались все кандидаты при живых ссылках.
        final_title = self._ensure_russian_candidate_title(digest_id, stored, h)[:500]
        if _editorial_headline_rejected(final_title) or _editorial_headline_rejected(h):
            _append_reject_reason(item, "headline_low_quality")
            return
        item["title"] = final_title
        item["headline_editorial_ok"] = True
        item["page_verified"] = bool(item.get("link_status", False)) and bool(item["headline_editorial_ok"])

    def _step1_fetch_supplementary_dicts(
        self,
        digest: Digest,
        seen_fp: set[str],
        excluded_urls: list[str],
        now_msk: str,
        need_hint: int,
    ) -> list[dict[str, Any]]:
        query = (
            "искусственный интеллект нейросети машинное обучение новости"
            if (digest.digest_type or "serious") == "serious"
            else "AI artificial intelligence neural networks machine learning news"
        )
        urls = fetch_article_urls_from_search(self.settings, query, limit=max(need_hint * 3, 14))
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
        spent = self._digest_step_llm_cost_sum(digest.id, "step_1")
        if spent >= self.settings.step1_max_cost_rub:
            logger.warning(
                "Шаг 1: LLM-refill пропущен — достигнут STEP1_MAX_COST_RUB | digest_id=%s spent=%.4f",
                digest.id,
                spent,
            )
            return []
        refill_raw, measure = self.cost_tracker.measure(
            lambda: self.workflow.run_candidates_refill(
                digest.digest_type or "serious",
                now_msk,
                excluded_urls[-55:],
            ),
            source="step1_refill",
        )
        self._save_cost(
            digest_id=digest.id,
            step="step_1",
            agent_name="NewsResearchAgent",
            model=AGENT_MODEL_RECOMMENDATIONS["NewsResearchAgent"],
            request_label="run_candidates_refill",
            cost_rub=measure.cost_rub,
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
