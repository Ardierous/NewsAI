"""Детерминированная вёрстка текстов для Telegram, MAX, VK и Дзен."""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any

from app.services.digest_type_policy import is_legacy_stored_curious
from app.services.digest_topic_policy import is_style_digest
from app.services.reader_copy import strip_analytics_scaffold

MAX_NEWS_SEP = "..."
DZEN_NEWS_SEP = "—"
SEP_VK = "· · ·"
HEADER_TITLE_SERIOUS = "⚡Пять актуальных новостей про ИИ"
HEADER_TITLE_AI = "⚡Пять новостей про ИИ"
HEADER_TITLE_CURIOUS = "⚡Пять забавных новостей про ИИ"
HEADER_TITLE_STYLE = "⚡Стиль: пять главных новостей моды"
# Обратная совместимость для тестов и импортов.
HEADER_TITLE = HEADER_TITLE_AI
DEFAULT_LEAD_SERIOUS = "Коротко: главные сдвиги в мире ИИ и вокруг экосистемы продуктов за сегодня."
DEFAULT_LEAD_AI = (
    "Коротко: деловые, практичные и неожиданные истории про ИИ и нейросети за этот период."
)
DEFAULT_LEAD_CURIOUS = (
    "Коротко: пять забавных, странных и неожиданных историй про нейросети и ИИ за этот период."
)
DEFAULT_LEAD_STYLE = "Коротко: главные новости моды, стиля и fashion-индустрии за этот период."
DEFAULT_LEAD = DEFAULT_LEAD_AI

MAX_CHANNEL_URL = "https://max.ru/channel_extellect"

DEFAULT_SUBSCRIPTION_INTRO = "👉 Подпишитесь на ExTellect:"
SUBSCRIPTION_CHANNEL_ORDER: tuple[str, ...] = ("telegram", "vk", "max", "dzen", "boosty")
SUBSCRIPTION_CHANNEL_META: dict[str, tuple[str, str]] = {
    "telegram": ("Telegram", "https://t.me/extellect"),
    "vk": ("ВКонтакте", "https://vk.com/extellect"),
    "max": ("MAX", MAX_CHANNEL_URL),
    "dzen": ("Дзен", "https://dzen.ru/extellect"),
    "boosty": ("Boosty", "https://boosty.to/extellect"),
}
DEFAULT_SUBSCRIPTION_CHANNELS: dict[str, bool] = {key: True for key in SUBSCRIPTION_CHANNEL_ORDER}

_MONTHS_RU = (
    "января",
    "февраля",
    "марта",
    "апреля",
    "мая",
    "июня",
    "июля",
    "августа",
    "сентября",
    "октября",
    "ноября",
    "декабря",
)

# Для отдельного Telegram-сообщения (не подпись к медиа) держим комфортный лимит.
TELEGRAM_MAX_CHARS = 3200
MAX_PLATFORM_MAX_CHARS = 4000
# Целевой объём описания одной новости в MAX для темы «Стиль» (весь пост ≤ MAX_PLATFORM_MAX_CHARS).
STYLE_MAX_NEWS_BODY_TARGET_CHARS = 720
# Пост в редакторе Дзена (не длинная «статья»): https://dzen.ru/help/ru/channel/post.html
DZEN_POST_MAX_CHARS = 4096


def digest_docx_filename(digest_date: date | datetime | str, digest_id: int | None = None) -> str:
    """Имя файла выгрузки: digest_2026-05-16.docx"""
    if isinstance(digest_date, datetime):
        iso = digest_date.date().isoformat()
    elif isinstance(digest_date, date):
        iso = digest_date.isoformat()
    else:
        iso = str(digest_date).strip().split("T")[0]
    if digest_id is not None:
        return f"digest_{iso}_id{digest_id}.docx"
    return f"digest_{iso}.docx"


def format_digest_date_ru(value: date | datetime | str | None) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        d = value.date()
    elif isinstance(value, date):
        d = value
    else:
        raw = str(value).strip().split("T")[0]
        parts = raw.split("-")
        if len(parts) != 3:
            return raw
        try:
            d = date(int(parts[0]), int(parts[1]), int(parts[2]))
        except ValueError:
            return raw
    return f"{d.day} {_MONTHS_RU[d.month - 1]} {d.year}"


def fix_markdown_links(text: str) -> str:
    return re.sub(r"\]\s+\(", "](", text)


def escape_md_link_label(title: str) -> str:
    return title.strip().replace("\\", "\\\\").replace("[", "\\[")


def escape_html_text(text: str) -> str:
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def escape_html_attr(text: str) -> str:
    return escape_html_text(text).replace("'", "&#39;")


def compress_paragraphs(text: str, max_blank: int = 1) -> str:
    lines = text.replace("\r\n", "\n").split("\n")
    out: list[str] = []
    blank_run = 0
    for line in lines:
        if not line.strip():
            blank_run += 1
            if blank_run <= max_blank:
                out.append("")
            continue
        blank_run = 0
        out.append(line.rstrip())
    return "\n".join(out).strip()


def normalize_subscription_channels(raw: Any = None) -> dict[str, bool]:
    """Нормализует набор площадок в подписи; по умолчанию все пять включены."""
    out = dict(DEFAULT_SUBSCRIPTION_CHANNELS)
    if isinstance(raw, dict):
        for key in SUBSCRIPTION_CHANNEL_ORDER:
            if key in raw:
                out[key] = bool(raw.get(key))
    elif isinstance(raw, (list, tuple, set)):
        selected = {str(x).strip().lower() for x in raw}
        for key in SUBSCRIPTION_CHANNEL_ORDER:
            out[key] = key in selected
    return out


def normalize_subscription_intro(raw: Any = None) -> str:
    text = " ".join(str(raw or "").split()).strip()
    return text or DEFAULT_SUBSCRIPTION_INTRO


def resolve_subscription_options(payload: dict[str, Any] | None = None) -> tuple[str, dict[str, bool]]:
    data = payload or {}
    intro = normalize_subscription_intro(data.get("subscription_intro"))
    channels = normalize_subscription_channels(data.get("subscription_channels"))
    return intro, channels


def _selected_subscription_channels(channels: dict[str, bool] | None = None) -> list[tuple[str, str, str]]:
    flags = normalize_subscription_channels(channels)
    selected: list[tuple[str, str, str]] = []
    for key in SUBSCRIPTION_CHANNEL_ORDER:
        if not flags.get(key):
            continue
        label, url = SUBSCRIPTION_CHANNEL_META[key]
        selected.append((key, label, url))
    return selected


def format_subscription_md_inline(
    intro: str | None = None,
    channels: dict[str, bool] | None = None,
) -> str:
    head = normalize_subscription_intro(intro)
    links = [f"[{label}]({url})" for _key, label, url in _selected_subscription_channels(channels)]
    if not links:
        return head
    return f"{head} {' • '.join(links)}"


def format_subscription_html_inline(
    intro: str | None = None,
    channels: dict[str, bool] | None = None,
) -> str:
    head = escape_html_text(normalize_subscription_intro(intro))
    links = [
        f'<a href="{escape_html_attr(url)}">{escape_html_text(label)}</a>'
        for _key, label, url in _selected_subscription_channels(channels)
    ]
    if not links:
        return head
    return f"{head} {' • '.join(links)}"


def format_subscription_md_block(
    intro: str | None = None,
    channels: dict[str, bool] | None = None,
) -> str:
    head = normalize_subscription_intro(intro)
    lines = [f"[{label}]({url})" for _key, label, url in _selected_subscription_channels(channels)]
    if not lines:
        return head
    return head + "\n" + "\n".join(lines)


def format_subscription_vk_block(
    intro: str | None = None,
    channels: dict[str, bool] | None = None,
) -> str:
    head = normalize_subscription_intro(intro)
    lines = [f"{label}: {url}" for _key, label, url in _selected_subscription_channels(channels)]
    if not lines:
        return head
    return head + "\n" + "\n".join(lines)


def subscription_md_inline(
    intro: str | None = None,
    channels: dict[str, bool] | None = None,
) -> str:
    return format_subscription_md_inline(intro, channels)


def subscription_html_inline(
    intro: str | None = None,
    channels: dict[str, bool] | None = None,
) -> str:
    """Подпись для веб-редактора MAX (вставка HTML из буфера)."""
    return format_subscription_html_inline(intro, channels)


def subscription_md_block(
    intro: str | None = None,
    channels: dict[str, bool] | None = None,
) -> str:
    """Многострочная подпись (Дзен и совместимость)."""
    return format_subscription_md_block(intro, channels)


def subscription_vk_block(
    intro: str | None = None,
    channels: dict[str, bool] | None = None,
) -> str:
    return format_subscription_vk_block(intro, channels)


def subscription_for_payload(payload: dict[str, Any], kind: str) -> str:
    """Подпись для площадки с учётом payload; для style — пусто."""
    if is_style_digest(payload.get("digest_topic")):
        return ""
    intro, channels = resolve_subscription_options(payload)
    if kind == "md_inline":
        return format_subscription_md_inline(intro, channels)
    if kind == "html_inline":
        return format_subscription_html_inline(intro, channels)
    if kind == "vk_block":
        return format_subscription_vk_block(intro, channels)
    if kind == "md_block":
        return format_subscription_md_block(intro, channels)
    return format_subscription_md_inline(intro, channels)


def resolve_header_title(payload: dict[str, Any]) -> str:
    if is_style_digest(payload.get("digest_topic")):
        return HEADER_TITLE_STYLE
    if is_legacy_stored_curious(payload.get("digest_type")):
        return HEADER_TITLE_CURIOUS
    return HEADER_TITLE_AI


def resolve_default_lead(payload: dict[str, Any]) -> str:
    if is_style_digest(payload.get("digest_topic")):
        return DEFAULT_LEAD_STYLE
    if is_legacy_stored_curious(payload.get("digest_type")):
        return DEFAULT_LEAD_CURIOUS
    return DEFAULT_LEAD_AI


def normalize_hashtag_tokens(
    tags: list[Any],
    minimum: int,
    maximum: int,
    *,
    digest_topic: str | None = None,
) -> str:
    seen: list[str] = []
    for t in tags:
        s = str(t).strip()
        if not s:
            continue
        if not s.startswith("#"):
            s = f"#{s}"
        if s not in seen:
            seen.append(s)
    if is_style_digest(digest_topic):
        defaults = ["#мода", "#стиль", "#fashion", "#тренды", "#одежда", "#стритстайл"]
    else:
        defaults = ["#ИИновости", "#нейросети", "#инновации", "#AI", "#технологии", "#машинноеОбучение"]
    for d in defaults:
        if len(seen) >= minimum:
            break
        if d not in seen:
            seen.append(d)
    return " ".join(seen[:maximum])


def resolve_lead(payload: dict[str, Any], platform: str = "telegram") -> str:
    key = "telegram_lead" if platform == "telegram" else "max_lead" if platform == "max" else f"lead_{platform}"
    custom = str(payload.get(key) or "").strip()
    if custom:
        return custom
    # Для VK используем тот же содержательный анонс, что и в MAX,
    # чтобы не скатываться к общему шаблону "Коротко: ...".
    if platform == "vk":
        max_lead = str(payload.get("max_lead") or "").strip()
        if max_lead:
            return max_lead
    return resolve_default_lead(payload)


HTML_LAYOUT_PLATFORMS = frozenset({"max", "dzen"})


def needs_html_layout_refresh(platform: str, content: str) -> bool:
    """True, если текст MAX/Дzen сохранён в старом markdown/plain формате или без подписи/тегов."""
    if platform not in HTML_LAYOUT_PLATFORMS:
        return False
    text = str(content or "").strip()
    if not text:
        return False
    lower = text.lower()
    if "<a href=" in lower and "<b>" in lower:
        if platform == "max":
            if not re.search(r"#\w", text):
                return True
        return False
    if "**" in text:
        return True
    return bool(re.search(r"\[[^\]]+\]\([^)]+\)", text))


def extract_lead_from_legacy_platform_text(content: str) -> str:
    """Вводный абзац из текста до первой новости (➤) или хэштегов."""
    lines = str(content or "").replace("\r\n", "\n").split("\n")
    idx = 0
    while idx < len(lines) and not lines[idx].strip():
        idx += 1
    if idx >= len(lines):
        return ""
    idx += 1
    while idx < len(lines) and not lines[idx].strip():
        idx += 1
    lead_lines: list[str] = []
    while idx < len(lines):
        stripped = lines[idx].strip()
        if stripped.startswith("➤") or stripped.startswith("#"):
            break
        if stripped in {MAX_NEWS_SEP, DZEN_NEWS_SEP, SEP_VK}:
            break
        lead_lines.append(lines[idx].rstrip())
        idx += 1
    return "\n".join(lead_lines).strip()


def _item_summary_short(item: dict[str, Any]) -> str:
    short = str(item.get("summary_short") or "").strip()
    if short:
        return short
    return " ".join(str(item.get("summary", "")).split())


def _text_largely_contained(haystack: str, needle: str, *, min_needle: int = 36) -> bool:
    if not needle or len(needle) < min_needle:
        return False
    if needle in haystack or haystack in needle:
        return True
    return needle[: min(48, len(needle))] in haystack


def _item_summary_style_max(item: dict[str, Any], max_chars: int) -> str:
    """Описание для MAX (тема «Стиль»): только читательский текст, без шаблона аналитики."""
    parts: list[str] = []
    for key in ("reader_text", "summary_short", "essence"):
        part = strip_analytics_scaffold(str(item.get(key) or ""))
        if not part:
            continue
        combined = " ".join(parts)
        if parts and _text_largely_contained(combined, part):
            continue
        parts.append(part)
    body = " ".join(parts).strip()
    if not body:
        body = strip_analytics_scaffold(_item_summary_short(item))
    return _compact_plain_text(body, max_chars)


def _news_link_line(title: str, url: str) -> str:
    safe_title = escape_md_link_label(title)
    return f"➤ [{safe_title}]({url.strip()})"


def _news_link_html(title: str, url: str) -> str:
    safe_title = escape_html_text(title.strip())
    safe_url = escape_html_attr(url.strip())
    return f'➤ <a href="{safe_url}">{safe_title}</a>'


def _html_news_block(
    item: dict[str, Any],
    *,
    max_body_chars: int | None = None,
    body_text: str | None = None,
) -> str:
    title = str(item["title"]).strip()
    url = str(item["url"]).strip()
    body_raw = " ".join((body_text or _item_summary_short(item)).split())
    if max_body_chars is not None:
        body_raw = _compact_plain_text(body_raw, max_body_chars)
    summary = escape_html_text(body_raw)
    return f"{_news_link_html(title, url)}<br><br>{summary}"


def _max_news_block(item: dict[str, Any], *, max_body_chars: int | None = None) -> str:
    return _html_news_block(item, max_body_chars=max_body_chars)


def _truncate_at_word(text: str, max_len: int) -> str:
    if len(text) <= max_len:
        return text
    cut = text[: max_len - 1].rstrip()
    if " " in cut:
        cut = cut.rsplit(" ", 1)[0]
    return cut + "…"


def _compact_plain_text(text: str, max_len: int) -> str:
    """Сжимает текст без многоточий: берёт полные предложения, затем мягко подрезает."""
    src = " ".join(str(text or "").split())
    if len(src) <= max_len:
        return src
    parts = [p.strip() for p in re.split(r"(?<=[.!?])\s+", src) if p.strip()]
    if parts:
        out: list[str] = []
        cur = 0
        for part in parts:
            add = len(part) + (1 if out else 0)
            if cur + add > max_len:
                break
            out.append(part)
            cur += add
        if out:
            return " ".join(out)
    # Fallback: короткая версия без "…", заканчиваем точкой.
    cut = src[:max_len].rstrip(" ,;:-")
    if " " in cut:
        cut = cut.rsplit(" ", 1)[0]
    if cut and cut[-1] not in ".!?":
        cut += "."
    return cut


_GENERIC_LEAD_START_RE = re.compile(
    r"^\s*(в этом выпуске|в сегодняшнем выпуске|сегодня в (нашем )?дайджесте|в выпуске)\b",
    re.IGNORECASE,
)
_GENERIC_LEAD_FLUFF_RE = re.compile(
    r"(рассмотрим|расскажем|осветим|поговорим о)\s+пять\s+"
    r"(актуальных |важных |главных )?(новост|событи)",
    re.IGNORECASE,
)

# Подпись к картинке в Telegram ≈ 1024 символа; шапка + вводная должны укладываться уверенно.
TELEGRAM_CAPTION_LEAD_MAX_CHARS = 620


def _plain_lead_text(text: str) -> str:
    raw = str(text or "")
    raw = raw.replace("<br><br>", "\n").replace("<br>", "\n")
    raw = re.sub(r"<[^>]+>", " ", raw)
    return " ".join(raw.split()).strip()


def _lead_looks_generic_fluff(text: str) -> bool:
    """Пустая «обёртка» без фактуры выпуска (типичный слабый MAX/Telegram-лид)."""
    t = _plain_lead_text(text)
    if not t:
        return True
    defaults = {
        DEFAULT_LEAD_AI,
        DEFAULT_LEAD_SERIOUS,
        DEFAULT_LEAD_CURIOUS,
        DEFAULT_LEAD_STYLE,
        DEFAULT_LEAD,
    }
    if t in defaults:
        return True
    if len(t) < 160 and _GENERIC_LEAD_FLUFF_RE.search(t):
        return True
    if len(t) < 110 and _GENERIC_LEAD_START_RE.search(t):
        return True
    return False


def platform_lead_is_weak(text: str) -> bool:
    """Публичная проверка: вводная слишком общая и почти без фактуры."""
    return _lead_looks_generic_fluff(text)


def _lead_quality_score(text: str) -> int:
    t = _plain_lead_text(text)
    if not t:
        return -10_000
    score = len(t)
    if _lead_looks_generic_fluff(t):
        score -= 280
    if re.search(r"\d", t):
        score += 25
    # Конкретика тем обычно длиннее общей фразы «пять новостей».
    if len(t) >= 180:
        score += 40
    return score


def _iter_payload_lead_candidates(payload: dict[str, Any]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for key in ("telegram_lead", "max_lead", "dzen_intro", "lead_vk"):
        t = _plain_lead_text(payload.get(key))
        if not t:
            continue
        key_norm = t.casefold()
        if key_norm in seen:
            continue
        seen.add(key_norm)
        out.append(t)
    analysis = _plain_lead_text(payload.get("overall_analysis"))
    if analysis:
        key_norm = analysis.casefold()
        if key_norm not in seen:
            out.append(analysis)
    return out


def _pick_best_payload_lead(payload: dict[str, Any], max_len: int) -> str:
    """Лучший содержательный анонс из уже готовых лидов площадок / overall_analysis."""
    candidates = _iter_payload_lead_candidates(payload)
    if not candidates:
        return ""
    best = max(candidates, key=_lead_quality_score)
    if _lead_quality_score(best) < 90:
        return ""
    if _lead_looks_generic_fluff(best):
        return ""
    return _compact_plain_text(best, max_len)


def _compose_news_facts_lead(payload: dict[str, Any], max_len: int, *, max_points: int = 4) -> str:
    points: list[str] = []
    per_point = 150 if max_len >= 500 else 110
    for item in list(payload.get("selected_news") or [])[:5]:
        summary = _item_summary_short(item)
        snippet = _compact_plain_text(summary, per_point).strip(" ,;:-")
        if snippet and snippet not in points:
            points.append(snippet)
        if len(points) >= max_points:
            break
    if not points:
        return ""
    return _compact_plain_text(f"В выпуске: {'; '.join(points)}.", max_len)


def promote_best_leads_in_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Подтянуть лучший лид в telegram/max, если текущий пустой или шаблонный."""
    best = _pick_best_payload_lead(payload, TELEGRAM_CAPTION_LEAD_MAX_CHARS)
    if not best:
        return payload
    tg = _plain_lead_text(payload.get("telegram_lead"))
    mx = _plain_lead_text(payload.get("max_lead"))
    if not tg or _lead_looks_generic_fluff(tg) or len(best) > len(tg) + 40:
        payload["telegram_lead"] = best
    if not mx or _lead_looks_generic_fluff(mx):
        payload["max_lead"] = _compact_plain_text(best, 320)
    return payload


def _compose_max_specific_lead(payload: dict[str, Any], max_len: int = 280) -> str:
    """Содержательный лид для MAX: свой max_lead, иначе лучший с других площадок / факты новостей."""
    custom = _plain_lead_text(payload.get("max_lead"))
    if custom and not _lead_looks_generic_fluff(custom):
        return _compact_plain_text(custom, max_len)

    best = _pick_best_payload_lead(payload, max_len)
    if best:
        return best

    facts = _compose_news_facts_lead(payload, max_len, max_points=3)
    if facts:
        return facts

    if custom:
        return _compact_plain_text(custom, max_len)
    return _compact_plain_text(resolve_default_lead(payload), max_len)


def _compose_telegram_lead(payload: dict[str, Any], max_len: int = TELEGRAM_CAPTION_LEAD_MAX_CHARS) -> str:
    """Вводная первого сообщения Telegram (под картинку): богаче MAX, с фактурой выпуска."""
    best = _pick_best_payload_lead(payload, max_len)
    facts = _compose_news_facts_lead(payload, max_len, max_points=4)

    if best and not _lead_looks_generic_fluff(best) and len(best) >= 160:
        # Если лид площадки хороший, но короткий — дополняем фактами новостей.
        if len(best) < 280 and facts and not _text_largely_contained(best, facts[:80], min_needle=40):
            merged = _compact_plain_text(f"{best} {facts}", max_len)
            if len(merged) > len(best) + 30:
                return merged
        return best

    if facts and (not best or len(facts) >= len(best)):
        return facts
    if best:
        return best
    if facts:
        return facts
    return _compact_plain_text(resolve_default_lead(payload), max_len)


def _split_platform_html_footer(text: str) -> tuple[str, str]:
    idx = text.find(subscription_html_inline())
    if idx < 0:
        for needle in ("👉", "Подпишитесь", "boosty.to/extellect", "t.me/extellect"):
            found = text.rfind(needle)
            if found >= 0:
                idx = found
                break
    if idx >= 0:
        start = text.rfind("<br><br>", 0, idx)
        if start >= 0:
            return text[:start], text[start:]
    # Для выпусков без подписи (например, тема "Стиль") режем по блоку хэштегов.
    hash_idx = text.rfind("<br><br>#")
    if hash_idx >= 0:
        return text[:hash_idx], text[hash_idx:]
    return text, ""


def _shrink_html_news_summaries(body: str, max_len: int) -> str:
    """Укорачивает текст после заголовка-ссылки, сами заголовки не трогаем."""
    if len(body) <= max_len:
        return body
    blocks = re.split(rf"(<br><br>{re.escape(MAX_NEWS_SEP)}<br><br>)", body)
    if len(blocks) <= 1:
        blocks = [body]
    news_parts: list[str] = []
    seps: list[str] = []
    for i, part in enumerate(blocks):
        if i % 2 == 1:
            seps.append(part)
        else:
            news_parts.append(part)
    for idx in range(len(news_parts) - 1, -1, -1):
        part = news_parts[idx]
        m = re.match(
            r"(?s)(.*?➤ <a href=\"[^\"]+\">)([^<]+)(</a><br><br>)(.*)$",
            part,
        )
        if not m:
            continue
        prefix, _title, mid, summary = m.groups()
        if not summary.strip():
            continue
        while len("".join(news_parts[:idx]) + "".join(seps[:idx]) + prefix + _title + mid + summary) > max_len and len(
            summary
        ) > 40:
            summary = _compact_plain_text(summary, max(40, len(summary) - 48))
        news_parts[idx] = prefix + _title + mid + summary
        rebuilt = ""
        for j, news in enumerate(news_parts):
            rebuilt += news
            if j < len(seps):
                rebuilt += seps[j]
        if len(rebuilt) <= max_len:
            return rebuilt
    rebuilt = ""
    for j, news in enumerate(news_parts):
        rebuilt += news
        if j < len(seps):
            rebuilt += seps[j]
    return rebuilt


def truncate_platform_text(text: str, max_chars: int, *, tail_marker: str = "…") -> str:
    if len(text) <= max_chars:
        return text
    cut = max_chars - len(tail_marker)
    if cut < 100:
        return text[:max_chars]
    chunk = text[:cut]
    last_news = chunk.rfind("\n➤ [")
    last_break = chunk.rfind("\n\n")
    safe = max(last_news, last_break)
    if safe > cut // 2:
        chunk = chunk[:safe]
    elif last_news > 0:
        line_end = chunk.find("\n", last_news + 1)
        if line_end > last_news:
            chunk = chunk[:line_end]
    return chunk.rstrip() + tail_marker


def truncate_platform_html(text: str, max_chars: int, *, tail_marker: str = "…") -> str:
    if len(text) <= max_chars:
        return text
    body, footer = _split_platform_html_footer(text)
    budget = max_chars - len(tail_marker) - len(footer)
    if budget < 100:
        return text[:max_chars]
    if len(body) <= budget:
        return body + footer
    chunk = body[:budget]
    sep = f"<br><br>{MAX_NEWS_SEP}<br><br>"
    while sep in chunk:
        last_sep = chunk.rfind(sep)
        if last_sep <= budget // 3:
            break
        chunk = chunk[:last_sep]
        if len(chunk) + len(footer) <= max_chars - len(tail_marker):
            return chunk.rstrip() + footer
    last_link = chunk.rfind("</a><br><br>")
    if last_link > budget // 2:
        chunk = chunk[: last_link + len("</a>")]
        if len(chunk) + len(footer) <= max_chars - len(tail_marker):
            return chunk + footer
    shrunk = _shrink_html_news_summaries(body, budget)
    if len(shrunk) + len(footer) <= max_chars - len(tail_marker):
        return shrunk + footer
    if last_link > 0:
        chunk = shrunk[: shrunk.rfind("</a>") + len("</a>")]
        return chunk.rstrip() + tail_marker + footer
    return shrunk[:budget].rstrip() + tail_marker + footer


def _dzen_post_news_block(item: dict[str, Any], *, max_body_chars: int) -> str:
    """Компактный блок для поста Дзена (лимит 4096 на весь пост)."""
    return _html_news_block(item, max_body_chars=max_body_chars)


def assemble_telegram(payload: dict[str, Any]) -> str:
    date_ru = format_digest_date_ru(payload.get("date"))
    header_title = resolve_header_title(payload)
    topic = payload.get("digest_topic")
    if is_style_digest(topic):
        lead = _compact_plain_text(resolve_lead(payload, "telegram"), 420)
    else:
        # Первое сообщение с картинкой: берём лучший лид площадок и делаем его содержательнее.
        lead = _compose_telegram_lead(payload, TELEGRAM_CAPTION_LEAD_MAX_CHARS)
    tags = normalize_hashtag_tokens(list(payload.get("hashtags") or []), 4, 6, digest_topic=topic)
    news_blocks: list[str] = []
    for item in payload.get("selected_news") or []:
        summary = _compact_plain_text(_item_summary_short(item), 240)
        news_blocks.append(f"{_news_link_line(item['title'], item['url'])}\n{summary}")
    body = "\n\n".join(news_blocks)
    sub = subscription_for_payload(payload, "md_inline")
    text = f"**{header_title} | {date_ru}**\n\n{lead}\n\n{body}\n\n"
    if sub:
        text += f"{sub}\n\n"
    text += tags
    return truncate_platform_text(compress_paragraphs(fix_markdown_links(text)), TELEGRAM_MAX_CHARS)


def assemble_max(payload: dict[str, Any]) -> str:
    """HTML для веб-редактора MAX: жирная шапка, ссылки в заголовках, отступы через <br>."""
    date_ru = format_digest_date_ru(payload.get("date"))
    header_title = resolve_header_title(payload)
    topic = payload.get("digest_topic")
    style = is_style_digest(topic)
    if style:
        lead = _compact_plain_text(resolve_lead(payload, "max") or resolve_default_lead(payload), 160)
    else:
        # Для MAX делаем более содержательный лид по фактуре выпуска, без обрыва многоточием.
        lead = _compose_max_specific_lead(payload, 280)
    tags = normalize_hashtag_tokens(list(payload.get("hashtags") or []), 4, 6, digest_topic=topic)
    sub = subscription_for_payload(payload, "html_inline")
    footer = (
        f"<br><br>{escape_html_text(tags)}"
        if not sub
        else f"<br><br>{sub}<br><br>{escape_html_text(tags)}"
    )
    header = (
        f"<b>{escape_html_text(header_title)} | {escape_html_text(date_ru)}</b><br><br>"
        f"{escape_html_text(lead)}<br><br>"
    )
    news = list(payload.get("selected_news") or [])
    n = max(len(news), 1)
    sep = f"<br><br>{MAX_NEWS_SEP}<br><br>"
    budget = MAX_PLATFORM_MAX_CHARS - len(header) - len(footer) - len(sep) * max(n - 1, 0)
    if style:
        per_news = max(
            STYLE_MAX_NEWS_BODY_TARGET_CHARS,
            (budget // n) - 12,
        )
        body = ""
        for _ in range(14):
            news_blocks = [
                _html_news_block(
                    item,
                    max_body_chars=per_news,
                    body_text=_item_summary_style_max(item, per_news),
                )
                for item in news
            ]
            body = sep.join(news_blocks)
            if len(header) + len(body) + len(footer) <= MAX_PLATFORM_MAX_CHARS:
                break
            per_news = max(STYLE_MAX_NEWS_BODY_TARGET_CHARS - 80, int(per_news * 0.92))
        text = header + body + footer
        if len(text) > MAX_PLATFORM_MAX_CHARS:
            shrunk = _shrink_html_news_summaries(body, MAX_PLATFORM_MAX_CHARS - len(header) - len(footer))
            text = header + shrunk + footer
        return text
    per_news = max(120, (budget // n) - 120)
    news_blocks = [_max_news_block(item, max_body_chars=per_news) for item in news]
    body = sep.join(news_blocks)
    text = header + body + footer
    return truncate_platform_html(text, MAX_PLATFORM_MAX_CHARS)


def assemble_vk(payload: dict[str, Any]) -> str:
    date_ru = format_digest_date_ru(payload.get("date"))
    header_title = resolve_header_title(payload)
    topic = payload.get("digest_topic")
    lead = resolve_lead(payload, "vk")
    tags = normalize_hashtag_tokens(list(payload.get("hashtags") or []), 3, 5, digest_topic=topic)
    news_blocks: list[str] = []
    for item in payload.get("selected_news") or []:
        title = str(item["title"]).strip().upper()
        summary = _item_summary_short(item)
        url = str(item["url"]).strip()
        news_blocks.append(f"{title}\n{summary}\nПодробности: {url}")
    body = f"\n\n{SEP_VK}\n\n".join(news_blocks)
    sub = subscription_for_payload(payload, "vk_block")
    text = f"{header_title} | {date_ru}\n\n{lead}\n\n{SEP_VK}\n\n{body}\n\n"
    if sub:
        text += f"{sub}\n\n"
    text += tags
    return compress_paragraphs(text)


def assemble_dzen(payload: dict[str, Any]) -> str:
    """HTML для веб-редактора Дзена — не более 4096 символов (см. справку Дзена)."""
    date_ru = format_digest_date_ru(payload.get("date"))
    header_title = resolve_header_title(payload)
    topic = payload.get("digest_topic")
    tags = normalize_hashtag_tokens(list(payload.get("hashtags") or []), 3, 5, digest_topic=topic)
    intro = str(payload.get("dzen_intro") or "").strip()
    if not intro:
        intro = resolve_default_lead(payload)
    intro = _truncate_at_word(intro, 280)
    sub = subscription_for_payload(payload, "html_inline")
    footer = (
        f"<br><br>{escape_html_text(tags)}"
        if not sub
        else f"<br><br>{sub}<br><br>{escape_html_text(tags)}"
    )
    header = (
        f"<b>{escape_html_text(header_title)} | {escape_html_text(date_ru)}</b><br><br>"
        f"{escape_html_text(intro)}<br><br>"
    )
    news = list(payload.get("selected_news") or [])
    n = max(len(news), 1)
    budget = DZEN_POST_MAX_CHARS - len(header) - len(footer)
    per_news = max(180, (budget // n) - 120)
    dzen_blocks = [_dzen_post_news_block(item, max_body_chars=per_news) for item in news]
    sep = f"<br><br>{escape_html_text(DZEN_NEWS_SEP)}<br><br>"
    body = sep.join(dzen_blocks)
    text = header + body + footer
    return truncate_platform_html(text, DZEN_POST_MAX_CHARS)


def assemble_platform_outputs(payload: dict[str, Any], platforms: list[str] | None = None) -> dict[str, str]:
    target = platforms or ["telegram", "max", "vk", "dzen"]
    builders = {
        "telegram": assemble_telegram,
        "max": assemble_max,
        "vk": assemble_vk,
        "dzen": assemble_dzen,
    }
    out: dict[str, str] = {}
    for key in target:
        builder = builders.get(key)
        if builder is not None:
            out[key] = builder(payload)
    return out
