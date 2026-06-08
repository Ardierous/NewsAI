"""Детерминированная вёрстка текстов для Telegram, MAX, VK и Дзен."""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any

from app.services.digest_type_policy import is_curious_digest

MAX_NEWS_SEP = "..."
DZEN_NEWS_SEP = "—"
SEP_VK = "· · ·"
HEADER_TITLE_SERIOUS = "⚡Пять актуальных новостей про ИИ"
HEADER_TITLE_CURIOUS = "⚡Пять забавных новостей про ИИ"
# Обратная совместимость для тестов и импортов.
HEADER_TITLE = HEADER_TITLE_SERIOUS
DEFAULT_LEAD_SERIOUS = "Коротко: главные сдвиги в мире ИИ и вокруг экосистемы продуктов за сегодня."
DEFAULT_LEAD_CURIOUS = (
    "Коротко: пять забавных, странных и неожиданных историй про нейросети и ИИ за этот период."
)
DEFAULT_LEAD = DEFAULT_LEAD_SERIOUS

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

TELEGRAM_MAX_CHARS = 3800
MAX_PLATFORM_MAX_CHARS = 4000
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


def subscription_md_inline() -> str:
    return (
        "👉 Подпишитесь на ExTellect: [Telegram](https://t.me/extellect) • "
        "[ВКонтакте](https://vk.com/extellect) • "
        "[MAX](https://max.ru/join/fu6Q3ibyBe8ONaZEg5J_3md_GXpZbJ5WlNKBOzeg4rY) • "
        "[Дзен](https://dzen.ru/extellect) • [Boosty](https://boosty.to/extellect)"
    )


def subscription_html_inline() -> str:
    """Подпись для веб-редактора MAX (вставка HTML из буфера)."""
    return (
        "👉 Подпишитесь на ExTellect: "
        '<a href="https://t.me/extellect">Telegram</a> • '
        '<a href="https://vk.com/extellect">ВКонтакте</a> • '
        '<a href="https://max.ru/join/fu6Q3ibyBe8ONaZEg5J_3md_GXpZbJ5WlNKBOzeg4rY">MAX</a> • '
        '<a href="https://dzen.ru/extellect">Дзен</a> • '
        '<a href="https://boosty.to/extellect">Boosty</a>'
    )


def subscription_md_block() -> str:
    """Многострочная подпись (Дзен и совместимость)."""
    return (
        "👉 Подпишитесь на ExTellect:\n"
        "[Telegram](https://t.me/extellect)\n"
        "[ВКонтакте](https://vk.com/extellect)\n"
        "[MAX](https://max.ru/join/fu6Q3ibyBe8ONaZEg5J_3md_GXpZbJ5WlNKBOzeg4rY)\n"
        "[Дзен](https://dzen.ru/extellect)\n"
        "[Boosty](https://boosty.to/extellect)"
    )


def subscription_vk_block() -> str:
    return (
        "👉 Подпишитесь на ExTellect:\n"
        "Telegram: https://t.me/extellect\n"
        "ВКонтакте: https://vk.com/extellect\n"
        "MAX: https://max.ru/join/fu6Q3ibyBe8ONaZEg5J_3md_GXpZbJ5WlNKBOzeg4rY\n"
        "Дзен: https://dzen.ru/extellect\n"
        "Boosty: https://boosty.to/extellect"
    )


def resolve_header_title(payload: dict[str, Any]) -> str:
    if is_curious_digest(payload.get("digest_type")):
        return HEADER_TITLE_CURIOUS
    return HEADER_TITLE_SERIOUS


def resolve_default_lead(payload: dict[str, Any]) -> str:
    if is_curious_digest(payload.get("digest_type")):
        return DEFAULT_LEAD_CURIOUS
    return DEFAULT_LEAD_SERIOUS


def normalize_hashtag_tokens(tags: list[Any], minimum: int, maximum: int) -> str:
    seen: list[str] = []
    for t in tags:
        s = str(t).strip()
        if not s:
            continue
        if not s.startswith("#"):
            s = f"#{s}"
        if s not in seen:
            seen.append(s)
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
    return resolve_default_lead(payload)


HTML_LAYOUT_PLATFORMS = frozenset({"max", "dzen"})


def needs_html_layout_refresh(platform: str, content: str) -> bool:
    """True, если текст MAX/Дзен сохранён в старом markdown/plain формате."""
    if platform not in HTML_LAYOUT_PLATFORMS:
        return False
    text = str(content or "").strip()
    if not text:
        return False
    lower = text.lower()
    if "<a href=" in lower and "<b>" in lower:
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


def _news_link_line(title: str, url: str) -> str:
    safe_title = escape_md_link_label(title)
    return f"➤ [{safe_title}]({url.strip()})"


def _news_link_html(title: str, url: str) -> str:
    safe_title = escape_html_text(title.strip())
    safe_url = escape_html_attr(url.strip())
    return f'➤ <a href="{safe_url}">{safe_title}</a>'


def _html_news_block(item: dict[str, Any], *, max_body_chars: int | None = None) -> str:
    title = str(item["title"]).strip()
    url = str(item["url"]).strip()
    body_raw = " ".join(_item_summary_short(item).split())
    if max_body_chars is not None:
        body_raw = _truncate_at_word(body_raw, max_body_chars)
    summary = escape_html_text(body_raw)
    return f"{_news_link_html(title, url)}<br><br>{summary}"


def _max_news_block(item: dict[str, Any]) -> str:
    return _html_news_block(item)


def _truncate_at_word(text: str, max_len: int) -> str:
    if len(text) <= max_len:
        return text
    cut = text[: max_len - 1].rstrip()
    if " " in cut:
        cut = cut.rsplit(" ", 1)[0]
    return cut + "…"


def truncate_platform_text(text: str, max_chars: int, *, tail_marker: str = "…") -> str:
    if len(text) <= max_chars:
        return text
    cut = max_chars - len(tail_marker)
    if cut < 100:
        return text[:max_chars]
    chunk = text[:cut]
    last_break = chunk.rfind("\n\n")
    if last_break > cut // 2:
        chunk = chunk[:last_break]
    return chunk.rstrip() + tail_marker


def truncate_platform_html(text: str, max_chars: int, *, tail_marker: str = "…") -> str:
    if len(text) <= max_chars:
        return text
    cut = max_chars - len(tail_marker)
    if cut < 100:
        return text[:max_chars]
    chunk = text[:cut]
    last_break = chunk.rfind("<br><br>")
    if last_break > cut // 2:
        chunk = chunk[:last_break]
    return chunk.rstrip() + tail_marker


def _dzen_post_news_block(item: dict[str, Any], *, max_body_chars: int) -> str:
    """Компактный блок для поста Дзена (лимит 4096 на весь пост)."""
    return _html_news_block(item, max_body_chars=max_body_chars)


def assemble_telegram(payload: dict[str, Any]) -> str:
    date_ru = format_digest_date_ru(payload.get("date"))
    header_title = resolve_header_title(payload)
    lead = resolve_lead(payload, "telegram")
    tags = normalize_hashtag_tokens(list(payload.get("hashtags") or []), 4, 6)
    news_blocks: list[str] = []
    for item in payload.get("selected_news") or []:
        summary = _item_summary_short(item)
        news_blocks.append(f"{_news_link_line(item['title'], item['url'])}\n{summary}")
    body = "\n\n".join(news_blocks)
    text = (
        f"**{header_title} | {date_ru}**\n\n"
        f"{lead}\n\n"
        f"{body}\n\n"
        f"{subscription_md_inline()}\n\n"
        f"{tags}"
    )
    return truncate_platform_text(compress_paragraphs(fix_markdown_links(text)), TELEGRAM_MAX_CHARS)


def assemble_max(payload: dict[str, Any]) -> str:
    """HTML для веб-редактора MAX: жирная шапка, ссылки в заголовках, отступы через <br>."""
    date_ru = format_digest_date_ru(payload.get("date"))
    header_title = resolve_header_title(payload)
    lead = resolve_lead(payload, "max")
    tags = normalize_hashtag_tokens(list(payload.get("hashtags") or []), 4, 6)
    news_blocks = [_max_news_block(item) for item in payload.get("selected_news") or []]
    body = f"<br><br>{MAX_NEWS_SEP}<br><br>".join(news_blocks)
    text = (
        f"<b>{escape_html_text(header_title)} | {escape_html_text(date_ru)}</b><br><br>"
        f"{escape_html_text(lead)}<br><br>"
        f"{body}<br><br>"
        f"{subscription_html_inline()}<br><br>"
        f"{escape_html_text(tags)}"
    )
    return truncate_platform_html(text, MAX_PLATFORM_MAX_CHARS)


def assemble_vk(payload: dict[str, Any]) -> str:
    date_ru = format_digest_date_ru(payload.get("date"))
    header_title = resolve_header_title(payload)
    lead = resolve_lead(payload, "vk")
    tags = normalize_hashtag_tokens(list(payload.get("hashtags") or []), 3, 5)
    news_blocks: list[str] = []
    for item in payload.get("selected_news") or []:
        title = str(item["title"]).strip().upper()
        summary = _item_summary_short(item)
        url = str(item["url"]).strip()
        news_blocks.append(f"{title}\n{summary}\nПодробности: {url}")
    body = f"\n\n{SEP_VK}\n\n".join(news_blocks)
    text = (
        f"{header_title} | {date_ru}\n\n"
        f"{lead}\n\n"
        f"{SEP_VK}\n\n"
        f"{body}\n\n"
        f"{subscription_vk_block()}\n\n"
        f"{tags}"
    )
    return compress_paragraphs(text)


def assemble_dzen(payload: dict[str, Any]) -> str:
    """HTML для веб-редактора Дзена — не более 4096 символов (см. справку Дзена)."""
    date_ru = format_digest_date_ru(payload.get("date"))
    header_title = resolve_header_title(payload)
    tags = normalize_hashtag_tokens(list(payload.get("hashtags") or []), 3, 5)
    intro = str(payload.get("dzen_intro") or "").strip()
    if not intro:
        intro = resolve_default_lead(payload)
    intro = _truncate_at_word(intro, 280)
    footer = f"<br><br>{subscription_html_inline()}<br><br>{escape_html_text(tags)}"
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
