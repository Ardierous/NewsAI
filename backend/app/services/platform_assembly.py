"""Детерминированная вёрстка текстов для Telegram, MAX, VK и Дзен."""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any

MAX_NEWS_SEP = "..."
DZEN_NEWS_SEP = "—"
SEP_VK = "· · ·"
HEADER_TITLE = "⚡Пять актуальных новостей про ИИ"
DEFAULT_LEAD = "Коротко: главные сдвиги в мире ИИ и вокруг экосистемы продуктов за сегодня."

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
    return DEFAULT_LEAD


def _item_summary_short(item: dict[str, Any]) -> str:
    short = str(item.get("summary_short") or "").strip()
    if short:
        return short
    return " ".join(str(item.get("summary", "")).split())


def _news_link_line(title: str, url: str) -> str:
    safe_title = escape_md_link_label(title)
    return f"➤ [{safe_title}]({url.strip()})"


def _paste_friendly_headline(title: str, url: str) -> str:
    """Заголовок + URL отдельными строками (Дзен/MAX веб не парсят [text](url) при вставке)."""
    return f"➤ {title.strip()}\n{url.strip()}"


def _max_news_block(item: dict[str, Any]) -> str:
    title = str(item["title"]).strip()
    url = str(item["url"]).strip()
    summary = " ".join(_item_summary_short(item).split())
    return f"{_paste_friendly_headline(title, url)}\n\n{summary}"


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


def _dzen_post_news_block(item: dict[str, Any], *, max_body_chars: int) -> str:
    """Компактный блок для поста Дзена (лимит 4096 на весь пост)."""
    title = str(item["title"]).strip()
    url = str(item["url"]).strip()
    source = str(item.get("source") or "Источник").strip()
    body = _truncate_at_word(" ".join(_item_summary_short(item).split()), max_body_chars)
    return (
        f"{_paste_friendly_headline(title, url)}\n\n"
        f"{body}\n\n"
        f"Читать подробнее: {source} — {url}"
    )


def assemble_telegram(payload: dict[str, Any]) -> str:
    date_ru = format_digest_date_ru(payload.get("date"))
    lead = resolve_lead(payload, "telegram")
    tags = normalize_hashtag_tokens(list(payload.get("hashtags") or []), 4, 6)
    news_blocks: list[str] = []
    for item in payload.get("selected_news") or []:
        summary = _item_summary_short(item)
        news_blocks.append(f"{_news_link_line(item['title'], item['url'])}\n{summary}")
    body = "\n\n".join(news_blocks)
    text = (
        f"**{HEADER_TITLE} | {date_ru}**\n\n"
        f"{lead}\n\n"
        f"{body}\n\n"
        f"{subscription_md_inline()}\n\n"
        f"{tags}"
    )
    return truncate_platform_text(compress_paragraphs(fix_markdown_links(text)), TELEGRAM_MAX_CHARS)


def assemble_max(payload: dict[str, Any]) -> str:
    date_ru = format_digest_date_ru(payload.get("date"))
    lead = resolve_lead(payload, "max")
    tags = normalize_hashtag_tokens(list(payload.get("hashtags") or []), 4, 6)
    news_blocks = [_max_news_block(item) for item in payload.get("selected_news") or []]
    body = f"\n\n{MAX_NEWS_SEP}\n\n".join(news_blocks)
    text = (
        f"⚡ {HEADER_TITLE} | {date_ru}\n\n"
        f"{lead}\n\n"
        f"{body}\n\n"
        f"{subscription_vk_block()}\n\n"
        f"{tags}"
    )
    return truncate_platform_text(compress_paragraphs(fix_markdown_links(text)), MAX_PLATFORM_MAX_CHARS)


def assemble_vk(payload: dict[str, Any]) -> str:
    date_ru = format_digest_date_ru(payload.get("date"))
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
        f"{HEADER_TITLE} | {date_ru}\n\n"
        f"{lead}\n\n"
        f"{SEP_VK}\n\n"
        f"{body}\n\n"
        f"{subscription_vk_block()}\n\n"
        f"{tags}"
    )
    return compress_paragraphs(text)


def assemble_dzen(payload: dict[str, Any]) -> str:
    """Пост для поля «Пост» в Дзене — не более 4096 символов (см. справку Дзена)."""
    date_ru = format_digest_date_ru(payload.get("date"))
    tags = normalize_hashtag_tokens(list(payload.get("hashtags") or []), 3, 5)
    intro = str(payload.get("dzen_intro") or "").strip()
    if not intro:
        intro = DEFAULT_LEAD
    intro = _truncate_at_word(intro, 280)
    footer = f"\n\n{subscription_vk_block()}\n\n{tags}"
    header = f"⚡ {HEADER_TITLE} | {date_ru}\n\n{intro}\n\n"
    news = list(payload.get("selected_news") or [])
    n = max(len(news), 1)
    budget = DZEN_POST_MAX_CHARS - len(header) - len(footer)
    per_news = max(180, (budget // n) - 80)
    dzen_blocks = [_dzen_post_news_block(item, max_body_chars=per_news) for item in news]
    body = f"\n\n{DZEN_NEWS_SEP}\n\n".join(dzen_blocks)
    text = header + body + footer
    if len(text) > DZEN_POST_MAX_CHARS:
        text = truncate_platform_text(text, DZEN_POST_MAX_CHARS)
    return compress_paragraphs(fix_markdown_links(text))


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
