"""Учёт происхождения URL: канал Telegram, seed-лента, агрегатор."""

from __future__ import annotations

from urllib.parse import urlparse

SEED_MARKER_COMMENT_PREFIX = "SEED_MARKER:"


def url_lookup_key(url: str) -> str:
    return str(url or "").strip().lower().rstrip("/")


def normalize_stats_marker(marker: str) -> str:
    raw = str(marker or "").strip()
    if not raw:
        return ""
    if raw.startswith(("http://", "https://")):
        try:
            p = urlparse(raw)
            host = (p.hostname or "").lower().removeprefix("www.")
            path = (p.path or "").rstrip("/") or "/"
            return f"{host}{path.lower()}"
        except Exception:
            return raw.lower()
    return raw.lower().removeprefix("www.")


def telegram_channel_marker(channel: str) -> str:
    ch = str(channel or "").strip().lstrip("@").split("?")[0].split("/")[0].lower()
    if not ch:
        return ""
    return f"https://t.me/s/{ch}"


def marker_matches_seed(stats_marker: str, seed_marker: str) -> bool:
    """Сопоставление маркера из модалки «Источники» с сохранённым seed_marker."""
    left = normalize_stats_marker(stats_marker)
    right = normalize_stats_marker(seed_marker)
    if not left or not right:
        return False
    if left == right:
        return True
    if left in right or right in left:
        return True
    left_host = left.split("/", 1)[0]
    right_host = right.split("/", 1)[0]
    if left_host and left_host == right_host:
        if "t.me" in left_host or "telegram" in left_host:
            return left in right or right in left
    return False


def lookup_seed_marker(
    url: str,
    seed_markers: dict[str, str] | None,
    *,
    stored_url: str | None = None,
) -> str:
    if not seed_markers:
        return ""
    for candidate in (url, stored_url):
        key = url_lookup_key(candidate or "")
        if key and key in seed_markers:
            return str(seed_markers[key] or "").strip()
    return ""


def attach_seed_marker(item: dict, seed_marker: str | None) -> None:
    marker = str(seed_marker or "").strip()
    if not marker:
        return
    item["seed_marker"] = marker[:500]


def seed_marker_from_item(item: dict | None) -> str:
    if not item:
        return ""
    direct = str(item.get("seed_marker") or "").strip()
    if direct:
        return direct[:500]
    comment = str(item.get("verification_comment") or "")
    for token in comment.split():
        if token.startswith(SEED_MARKER_COMMENT_PREFIX):
            return token.removeprefix(SEED_MARKER_COMMENT_PREFIX).strip()[:500]
    return ""


def merge_seed_marker_into_comment(comment: str, seed_marker: str | None) -> str:
    marker = str(seed_marker or "").strip()
    base = str(comment or "").strip()
    if not marker:
        return base
    token = f"{SEED_MARKER_COMMENT_PREFIX}{marker}"
    if token in base:
        return base
    return f"{base} {token}".strip()
