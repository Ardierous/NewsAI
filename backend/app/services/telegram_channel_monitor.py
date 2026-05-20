"""Парсинг публичных Telegram-каналов (t.me/s/) — извлечение внешних URL из постов."""

from __future__ import annotations

import html
import logging
import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import TYPE_CHECKING
from urllib.parse import urlparse

import requests

if TYPE_CHECKING:
    from app.config import Settings

logger = logging.getLogger("app.telegram_monitor")

_TME_S_BASE = "https://t.me/s/"
_DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# Хосты, которые не считаем «новостью из поста» (служебные / сам канал / CDN TG).
_SKIP_HOST_MARKERS = (
    "t.me",
    "telegram.me",
    "telegram.org",
    "telesco.pe",
    "tg.dev",
)

# Сайт компании в шапке канала — не внешняя новость.
_SKIP_EXACT_HOSTS = frozenset({"technokratos.com", "www.technokratos.com"})

_MESSAGE_WRAP_SPLIT_RE = re.compile(r'<div class="tgme_widget_message_wrap', re.IGNORECASE)
_DATA_POST_RE = re.compile(r'data-post="([^"/]+)/(\d+)"', re.IGNORECASE)
_TIME_RE = re.compile(r'<time datetime="([^"]+)"', re.IGNORECASE)
_MESSAGE_TEXT_OPEN_RE = re.compile(
    r'<div class="tgme_widget_message_text[^"]*"[^>]*>',
    re.IGNORECASE,
)
_HREF_RE = re.compile(r"""href=["'](https?://[^"'#\s]+)""", re.IGNORECASE)
_PREV_BEFORE_RE = re.compile(r"""href="/s/([^"?]+)\?before=(\d+)""")


@dataclass(frozen=True)
class TelegramPostLinks:
    channel: str
    post_id: int
    published_at: datetime
    urls: tuple[str, ...]


def parse_monitor_channels(raw: str | None) -> tuple[str, ...]:
    """Список username каналов из STEP1_TELEGRAM_MONITOR_CHANNELS (через запятую)."""
    if not raw or not str(raw).strip():
        return ()
    out: list[str] = []
    seen: set[str] = set()
    for part in str(raw).split(","):
        ch = normalize_channel_username(part)
        if ch and ch not in seen:
            seen.add(ch)
            out.append(ch)
    return tuple(out)


def normalize_channel_username(value: str) -> str | None:
    v = (value or "").strip()
    if not v:
        return None
    for prefix in ("https://t.me/s/", "https://t.me/", "http://t.me/s/", "http://t.me/", "t.me/s/", "t.me/"):
        if v.lower().startswith(prefix):
            v = v[len(prefix) :]
            break
    v = v.lstrip("@").split("?")[0].split("/")[0].strip().lower()
    if not v or not re.fullmatch(r"[a-z0-9_]{3,64}", v):
        return None
    return v


def is_telegram_internal_url(url: str) -> bool:
    try:
        host = (urlparse(url).hostname or "").lower().replace("www.", "")
    except Exception:
        return True
    if host in _SKIP_EXACT_HOSTS:
        return True
    return any(marker in host or marker in url.lower() for marker in _SKIP_HOST_MARKERS)


def _extract_message_text_html(chunk: str) -> str:
    m_open = _MESSAGE_TEXT_OPEN_RE.search(chunk)
    if not m_open:
        return ""
    tail = chunk[m_open.end() :]
    # Первый закрывающий </div> после открытия блока текста поста.
    end = tail.lower().find("</div>")
    if end < 0:
        return ""
    return tail[:end]


def extract_external_urls_from_message_html(fragment: str) -> list[str]:
    if not fragment:
        return []
    seen: set[str] = set()
    out: list[str] = []
    for m in _HREF_RE.finditer(fragment):
        raw = html.unescape(m.group(1)).strip().rstrip(".,);]")
        if not raw.startswith("http"):
            continue
        if is_telegram_internal_url(raw):
            continue
        if raw in seen:
            continue
        seen.add(raw)
        out.append(raw)
    return out


def parse_channel_posts_html(html_text: str, channel: str) -> list[TelegramPostLinks]:
    channel_key = normalize_channel_username(channel) or channel.lower()
    posts: list[TelegramPostLinks] = []
    chunks = _MESSAGE_WRAP_SPLIT_RE.split(html_text)
    for chunk in chunks[1:]:
        m_post = _DATA_POST_RE.search(chunk)
        m_time = _TIME_RE.search(chunk)
        if not m_post or not m_time:
            continue
        ch_name, post_id_s = m_post.group(1), m_post.group(2)
        if (ch_name or "").lower() != channel_key:
            continue
        try:
            published = datetime.fromisoformat(m_time.group(1).replace("Z", "+00:00"))
        except ValueError:
            continue
        body = _extract_message_text_html(chunk)
        urls = extract_external_urls_from_message_html(body)
        if not urls:
            continue
        posts.append(
            TelegramPostLinks(
                channel=channel_key,
                post_id=int(post_id_s),
                published_at=published,
                urls=tuple(urls),
            )
        )
    return posts


def _prev_before_id(html_text: str, channel: str) -> int | None:
    ch = (normalize_channel_username(channel) or channel).lower()
    for m in _PREV_BEFORE_RE.finditer(html_text):
        if m.group(1).lower() == ch:
            try:
                return int(m.group(2))
            except ValueError:
                continue
    return None


def fetch_channel_html(channel: str, *, before: int | None = None, timeout: float = 20.0) -> str:
    ch = normalize_channel_username(channel)
    if not ch:
        raise ValueError(f"invalid channel: {channel!r}")
    url = f"{_TME_S_BASE}{ch}"
    if before is not None:
        url = f"{url}?before={before}"
    r = requests.get(url, headers={"User-Agent": _DEFAULT_UA, "Accept": "text/html"}, timeout=timeout)
    r.raise_for_status()
    return r.text


def collect_external_links_from_channels(
    channels: tuple[str, ...],
    *,
    earliest_date: date | None = None,
    max_pages: int = 2,
    max_links: int = 30,
    timeout: float = 20.0,
) -> list[str]:
    """
    Собрать уникальные внешние URL из последних постов каналов (новые посты первыми).
    Посты старше earliest_date пропускаются; при необходимости подгружается предыдущая страница t.me/s/.
    """
    if not channels:
        return []
    max_pages = max(1, min(max_pages, 5))
    max_links = max(1, min(max_links, 80))
    ordered: list[str] = []
    seen: set[str] = set()

    for channel in channels:
        before: int | None = None
        pages = 0
        stop_channel = False
        while pages < max_pages and not stop_channel and len(ordered) < max_links:
            pages += 1
            try:
                html_page = fetch_channel_html(channel, before=before, timeout=timeout)
            except requests.RequestException as exc:
                logger.warning("Telegram monitor: не удалось загрузить t.me/s/%s | %s", channel, exc)
                break
            posts = parse_channel_posts_html(html_page, channel)
            if not posts:
                break
            oldest_on_page: date | None = None
            for post in posts:
                pub_day = post.published_at.date()
                if oldest_on_page is None or pub_day < oldest_on_page:
                    oldest_on_page = pub_day
                if earliest_date is not None and pub_day < earliest_date:
                    stop_channel = True
                    continue
                for url in post.urls:
                    if url in seen:
                        continue
                    seen.add(url)
                    ordered.append(url)
                    if len(ordered) >= max_links:
                        return ordered
            if earliest_date is not None and oldest_on_page is not None and oldest_on_page < earliest_date:
                stop_channel = True
            if stop_channel:
                break
            next_before = _prev_before_id(html_page, channel)
            if next_before is None or next_before == before:
                break
            before = next_before

    logger.info(
        "Telegram monitor: собрано внешних URL=%s channels=%s earliest=%s",
        len(ordered),
        ",".join(channels),
        earliest_date.isoformat() if earliest_date else "any",
    )
    return ordered


def collect_telegram_seed_urls_for_digest(
    settings: Settings,
    *,
    earliest_date: date | None,
) -> list[str]:
    if not getattr(settings, "step1_telegram_monitor_enabled", True):
        return []
    channels = parse_monitor_channels(getattr(settings, "step1_telegram_monitor_channels", "") or "")
    if not channels:
        return []
    return collect_external_links_from_channels(
        channels,
        earliest_date=earliest_date,
        max_pages=int(getattr(settings, "step1_telegram_max_pages", 2) or 2),
        max_links=int(getattr(settings, "step1_telegram_max_links", 30) or 30),
    )
