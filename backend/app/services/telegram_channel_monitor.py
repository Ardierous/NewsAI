"""Парсинг публичных Telegram-каналов (t.me/s/) — извлечение внешних URL из постов."""

from __future__ import annotations

import html
import logging
import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

import requests

from app.services.seed_source_tracking import telegram_channel_marker, url_lookup_key

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


_DIGEST_TEXT_MARKER = "Дайджест"


@dataclass(frozen=True)
class TelegramPostLinks:
    channel: str
    post_id: int
    published_at: datetime
    text_html: str
    urls: tuple[str, ...]


def message_plain_text(text_html: str) -> str:
    if not text_html:
        return ""
    return re.sub(r"<[^>]+>", "", html.unescape(text_html))


def post_matches_text_filter(post: TelegramPostLinks, text_filter: str | None) -> bool:
    needle = (text_filter or "").strip()
    if not needle:
        return True
    return needle.casefold() in message_plain_text(post.text_html).casefold()


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
        posts.append(
            TelegramPostLinks(
                channel=channel_key,
                post_id=int(post_id_s),
                published_at=published,
                text_html=body,
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


def fetch_channel_html(channel: str, *, before: int | None = None, timeout: float = 10.0) -> str:
    ch = normalize_channel_username(channel)
    if not ch:
        raise ValueError(f"invalid channel: {channel!r}")
    url = f"{_TME_S_BASE}{ch}"
    if before is not None:
        url = f"{url}?before={before}"
    r = requests.get(url, headers={"User-Agent": _DEFAULT_UA, "Accept": "text/html"}, timeout=timeout)
    r.raise_for_status()
    return r.text


def collect_urls_from_digest_posts(
    posts: list[TelegramPostLinks],
    *,
    earliest_date: date | None = None,
    max_digest_posts: int = 3,
    post_text_filter: str | None = _DIGEST_TEXT_MARKER,
    max_links: int = 30,
) -> list[str]:
    """Внешние URL из последних digest-постов (сначала свежие посты канала)."""
    max_digest_posts = max(1, min(max_digest_posts, 10))
    max_links = max(1, min(max_links, 80))
    ordered: list[str] = []
    seen: set[str] = set()
    digest_posts_used = 0
    ordered_posts = sorted(posts, key=lambda p: p.published_at, reverse=True)
    for post in ordered_posts:
        if digest_posts_used >= max_digest_posts or len(ordered) >= max_links:
            break
        pub_day = post.published_at.date()
        if earliest_date is not None and pub_day < earliest_date:
            break
        if not post_matches_text_filter(post, post_text_filter):
            continue
        digest_posts_used += 1
        for url in post.urls:
            if url in seen:
                continue
            seen.add(url)
            ordered.append(url)
            if len(ordered) >= max_links:
                return ordered
    return ordered


def filter_telegram_external_urls(urls: list[str], *, max_links: int = 30) -> list[str]:
    ordered: list[str] = []
    seen: set[str] = set()
    for url in urls:
        u = (url or "").strip()
        if not u.startswith("http") or is_telegram_internal_url(u):
            continue
        if u in seen:
            continue
        seen.add(u)
        ordered.append(u)
        if len(ordered) >= max(1, max_links):
            break
    return ordered


def _merge_seed_bundles(
    primary_urls: list[str],
    primary_markers: dict[str, str],
    secondary_urls: list[str],
    secondary_markers: dict[str, str],
    *,
    max_links: int,
) -> tuple[list[str], dict[str, str]]:
    ordered: list[str] = []
    markers: dict[str, str] = {}
    seen: set[str] = set()
    for urls, src_markers in ((primary_urls, primary_markers), (secondary_urls, secondary_markers)):
        for url in filter_telegram_external_urls(urls, max_links=max_links):
            key = url_lookup_key(url)
            if not key or key in seen:
                continue
            seen.add(key)
            ordered.append(url)
            marker = src_markers.get(key)
            if marker:
                markers[key] = marker
            if len(ordered) >= max(1, max_links):
                return ordered, markers
    return ordered, markers


def _collect_via_proxyapi(
    settings: Settings,
    channels: tuple[str, ...],
    *,
    earliest_date: date | None,
    max_pages: int,
    max_links: int,
    max_digest_posts: int,
    post_text_filter: str | None,
    proxy: Any | None = None,
) -> tuple[list[str], dict[str, str]]:
    if not getattr(settings, "proxyapi_web_search_enabled", True):
        logger.info("Telegram monitor (ProxyAPI): web_search отключён, пропуск")
        return [], {}
    if not getattr(settings, "step1_telegram_via_proxyapi", True):
        return [], {}

    from app.proxyapi_client import ProxyApiClient

    client = proxy or ProxyApiClient()
    ctx_size = str(
        getattr(settings, "step1_telegram_proxyapi_context_size", None)
        or getattr(settings, "proxyapi_web_search_context_size", "medium")
        or "medium"
    ).strip().lower()
    if ctx_size not in ("low", "medium", "high"):
        ctx_size = "high"

    ordered: list[str] = []
    markers: dict[str, str] = {}
    seen: set[str] = set()
    html_urls: list[str] = []
    llm_pool: list[str] = []
    url_channel_markers: dict[str, str] = {}
    for channel in channels:
        channel_marker = telegram_channel_marker(channel)
        try:
            raw_urls, html_pages = client.fetch_telegram_digest_seed_urls(
                channel,
                earliest_date=earliest_date,
                max_digest_posts=max_digest_posts,
                post_text_filter=post_text_filter or _DIGEST_TEXT_MARKER,
                max_links=max_links,
                search_context_size=ctx_size,
                max_pages=max_pages,
            )
        except Exception:
            logger.exception("Telegram monitor (ProxyAPI): ошибка channel=%s", channel)
            continue

        llm_pool.extend(raw_urls or [])
        for html_text in html_pages:
            if "tgme_widget_message" not in html_text:
                continue
            posts = parse_channel_posts_html(html_text, channel)
            for post_url in collect_urls_from_digest_posts(
                posts,
                earliest_date=earliest_date,
                max_digest_posts=max_digest_posts,
                post_text_filter=post_text_filter,
                max_links=max_links,
            ):
                html_urls.append(post_url)
                if channel_marker:
                    key = url_lookup_key(post_url)
                    if key:
                        url_channel_markers[key] = channel_marker

    html_urls = filter_telegram_external_urls(html_urls, max_links=max_links)
    html_keys = {url_lookup_key(u) for u in html_urls if url_lookup_key(u)}
    validated_llm: list[str] = []
    from app.services.digest_service import channel_seed_llm_url_is_article

    for url in filter_telegram_external_urls(llm_pool, max_links=max_links):
        key = url_lookup_key(url)
        if not key or key in html_keys:
            continue
        if channel_seed_llm_url_is_article(url):
            validated_llm.append(url)
            html_keys.add(key)
        else:
            logger.info("Telegram monitor: URL модели отброшен (не статья) | url=%s", url[:120])

    for url in filter_telegram_external_urls([*html_urls, *validated_llm], max_links=max_links):
        key = url_lookup_key(url)
        if not key or key in seen:
            continue
        seen.add(key)
        ordered.append(url)
        marker = url_channel_markers.get(key)
        if marker:
            markers[key] = marker
        elif channels:
            fallback = telegram_channel_marker(channels[0])
            if fallback:
                markers[key] = fallback
        if len(ordered) >= max_links:
            break

    logger.info(
        "Telegram monitor (ProxyAPI): собрано внешних URL=%s channels=%s earliest=%s filter=%r max_digest_posts=%s",
        len(ordered),
        ",".join(channels),
        earliest_date.isoformat() if earliest_date else "any",
        post_text_filter or "",
        max_digest_posts,
    )
    return ordered, markers


def collect_external_links_from_channels(
    channels: tuple[str, ...],
    *,
    earliest_date: date | None = None,
    max_pages: int = 2,
    max_links: int = 30,
    max_digest_posts: int = 3,
    post_text_filter: str | None = _DIGEST_TEXT_MARKER,
    timeout: float = 10.0,
) -> tuple[list[str], dict[str, str]]:
    """
    Собрать уникальные внешние URL из последних постов каналов (новые посты первыми).
    По умолчанию — только max_digest_posts последних постов с «Дайджест» в тексте.
    Посты старше earliest_date не учитываются; при необходимости подгружается предыдущая страница t.me/s/.
    """
    if not channels:
        return [], {}
    max_pages = max(1, min(max_pages, 5))
    max_links = max(1, min(max_links, 80))
    max_digest_posts = max(1, min(max_digest_posts, 10))
    ordered: list[str] = []
    markers: dict[str, str] = {}
    seen: set[str] = set()

    for channel in channels:
        channel_marker = telegram_channel_marker(channel)
        before: int | None = None
        pages = 0
        digest_posts_used = 0
        stop_channel = False
        while pages < max_pages and not stop_channel and len(ordered) < max_links:
            if digest_posts_used >= max_digest_posts:
                break
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

            page_urls = collect_urls_from_digest_posts(
                posts,
                earliest_date=earliest_date,
                max_digest_posts=max_digest_posts - digest_posts_used,
                post_text_filter=post_text_filter,
                max_links=max_links - len(ordered),
            )
            for url in page_urls:
                key = url_lookup_key(url)
                if not key or key in seen:
                    continue
                seen.add(key)
                ordered.append(url)
                if channel_marker:
                    markers[key] = channel_marker
                if len(ordered) >= max_links:
                    return ordered, markers

            for post in sorted(posts, key=lambda p: p.published_at, reverse=True):
                if digest_posts_used >= max_digest_posts:
                    break
                if earliest_date is not None and post.published_at.date() < earliest_date:
                    break
                if not post_matches_text_filter(post, post_text_filter):
                    continue
                digest_posts_used += 1

            if digest_posts_used >= max_digest_posts:
                break
            if earliest_date is not None and oldest_on_page is not None and oldest_on_page < earliest_date:
                stop_channel = True
            if stop_channel:
                break
            next_before = _prev_before_id(html_page, channel)
            if next_before is None or next_before == before:
                break
            before = next_before

    logger.info(
        "Telegram monitor (direct): собрано внешних URL=%s channels=%s earliest=%s filter=%r max_digest_posts=%s",
        len(ordered),
        ",".join(channels),
        earliest_date.isoformat() if earliest_date else "any",
        post_text_filter or "",
        max_digest_posts,
    )
    return ordered, markers


def collect_telegram_seed_bundle_for_digest(
    settings: Settings,
    *,
    earliest_date: date | None,
    proxy: Any | None = None,
) -> tuple[list[str], dict[str, str]]:
    if not getattr(settings, "step1_telegram_monitor_enabled", True):
        return [], {}
    channels = parse_monitor_channels(getattr(settings, "step1_telegram_monitor_channels", "") or "")
    if not channels:
        return [], {}
    text_filter = getattr(settings, "step1_telegram_post_text_filter", _DIGEST_TEXT_MARKER) or ""
    max_pages = int(getattr(settings, "step1_telegram_max_pages", 2) or 2)
    max_links = int(getattr(settings, "step1_telegram_max_links", 30) or 30)
    max_digest_posts = int(getattr(settings, "step1_telegram_max_digest_posts", 3) or 3)
    post_text_filter = text_filter.strip() or None
    timeout = float(getattr(settings, "step1_telegram_timeout_sec", 10.0) or 10.0)
    kwargs = dict(
        earliest_date=earliest_date,
        max_pages=max_pages,
        max_links=max_links,
        max_digest_posts=max_digest_posts,
        post_text_filter=post_text_filter,
    )

    if getattr(settings, "step1_telegram_via_proxyapi", True):
        proxy_urls, proxy_markers = _collect_via_proxyapi(settings, channels, proxy=proxy, **kwargs)
        # Надёжность важнее: всегда дополняем/проверяем прямым парсингом канала.
        # Это защищает от ситуаций, когда ProxyAPI вернул «левые» или неполные URL.
        direct_urls, direct_markers = collect_external_links_from_channels(channels, timeout=timeout, **kwargs)
        if direct_urls:
            merged_urls, merged_markers = _merge_seed_bundles(
                direct_urls,
                direct_markers,
                proxy_urls,
                proxy_markers,
                max_links=max_links,
            )
            logger.info(
                "Telegram monitor: merged direct+proxy | direct=%s proxy=%s total=%s",
                len(direct_urls),
                len(proxy_urls),
                len(merged_urls),
            )
            return merged_urls, merged_markers
        if proxy_urls:
            return proxy_urls, proxy_markers
        if not getattr(settings, "step1_telegram_direct_fallback", True):
            return [], {}
        logger.info("Telegram monitor: ProxyAPI не вернул URL, пробуем direct t.me")
        return direct_urls, direct_markers

    return collect_external_links_from_channels(channels, timeout=timeout, **kwargs)


def collect_telegram_seed_url_markers_for_digest(
    settings: Settings,
    *,
    earliest_date: date | None,
    proxy: Any | None = None,
) -> dict[str, str]:
    _urls, markers = collect_telegram_seed_bundle_for_digest(
        settings,
        earliest_date=earliest_date,
        proxy=proxy,
    )
    return markers


def collect_telegram_seed_urls_for_digest(
    settings: Settings,
    *,
    earliest_date: date | None,
    proxy: Any | None = None,
) -> list[str]:
    urls, _markers = collect_telegram_seed_bundle_for_digest(
        settings,
        earliest_date=earliest_date,
        proxy=proxy,
    )
    return urls
