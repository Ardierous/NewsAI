"""Редактор доменов tiers для UI шага 0: чтение/запись txt + счётчики «найдено / пул / топ‑5»."""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import NewsCandidate, SelectedNews, Step1UrlRegistry
from app.schemas_source_tiers import (
    SourceHostOut,
    SourceHostStatsOut,
    SourceTierGroupIn,
    SourceTierGroupOut,
    SourceTiersEditorOut,
    SourceTiersEditorUpdate,
)
from app.services.digest_type_policy import is_curious_digest, normalize_digest_type
from app.services.step1_tiers_autoblock import _AUTO_MARKER
from app.source_tiers_policy import _HOST_RULES_MARKER, _parse_host_rules, invalidate_policy_cache

_AUTO_LINE_RE = re.compile(r"^\s*#\s*auto-unreachable:", re.IGNORECASE)

SERIOUS_GROUP_SPECS: tuple[tuple[str, str, bool, int], ...] = (
    ("search_seed_urls", "Ленты, Telegram и seed-URL", False, 1),
    ("tier1_hosts", "Tier-1", False, 2),
    ("tier2_hosts", "Tier-2", False, 3),
    ("aggregator_hosts", "Агрегаторы (Tier-2)", False, 4),
    ("tier3_hosts", "Tier-3", False, 5),
    ("tier4_hosts", "Tier-4", False, 6),
    ("banned_media_hosts", "Tier-5 — запрещённые медиа", True, 7),
    ("blocked_search_hosts", "Чёрный список поиска", True, 8),
)

CURIOUS_GROUP_SPECS: tuple[tuple[str, str, bool, int], ...] = (
    ("search_seed_urls", "Ленты, Telegram и seed-URL", False, 1),
    ("curious_tier1_hosts", "Curious-T1", False, 2),
    ("curious_tier2_hosts", "Curious-T2", False, 3),
    ("aggregator_hosts", "Агрегаторы", False, 4),
    ("banned_media_hosts", "Запрещённые медиа", True, 5),
    ("blocked_search_hosts", "Чёрный список поиска", True, 6),
)

SERIOUS_TIER_SEED_SECTIONS: tuple[str, ...] = (
    "tier1_hosts",
    "tier2_hosts",
    "tier3_hosts",
    "tier4_hosts",
    "aggregator_hosts",
)
CURIOUS_TIER_SEED_SECTIONS: tuple[str, ...] = (
    "curious_tier1_hosts",
    "curious_tier2_hosts",
    "aggregator_hosts",
)


def _normalize_marker(value: str) -> str:
    token = str(value or "").strip().lower().removeprefix("www.")
    if "#" in token:
        token = token.split("#", 1)[0].strip()
    return token


def _host_from_url(url: str) -> str:
    return (urlparse(str(url or "").strip()).hostname or "").lower().removeprefix("www.")


def _marker_matches_host(marker: str, host: str) -> bool:
    m = _normalize_marker(marker)
    h = str(host or "").lower().removeprefix("www.")
    if not m or not h:
        return False
    return m in h or h in m


def _seed_is_bare_homepage(seed: str) -> bool:
    try:
        path = (urlparse(str(seed or "").strip()).path or "").rstrip("/") or "/"
    except Exception:
        return False
    return path in ("", "/")


def _site_has_path_seeds(host: str, seeds: list[str]) -> bool:
    """У домена уже есть seed с путём (не главная) — голую главную не дублируем."""
    h = str(host or "").lower().removeprefix("www.")
    if not h:
        return False
    for seed in seeds:
        if _seed_is_bare_homepage(seed):
            continue
        sh = _host_from_url(seed)
        if sh and _marker_matches_host(h, sh):
            return True
    return False


def _skip_redundant_homepage_seed(seed: str, existing_seeds: list[str]) -> bool:
    if not _seed_is_bare_homepage(seed):
        return False
    host = _host_from_url(seed)
    return _site_has_path_seeds(host, existing_seeds)


def tiers_file_path(settings: Any, digest_type: str | None) -> Path:
    dtype = normalize_digest_type(digest_type)
    if is_curious_digest(dtype):
        return Path(settings.curious_source_hosts_path)
    return Path(settings.source_tiers_path)


def _telegram_seed_markers(settings: Any) -> list[str]:
    if not getattr(settings, "step1_telegram_monitor_enabled", True):
        return []
    from app.services.telegram_channel_monitor import parse_monitor_channels

    raw = getattr(settings, "step1_telegram_monitor_channels", "") or ""
    return [f"https://t.me/s/{ch}" for ch in parse_monitor_channels(raw) if ch]


def _merge_section_entries(
    section_id: str,
    entries: list[tuple[str, bool]],
    *,
    settings: Any,
    digest_type: str | None = None,
    section_entries: dict[str, list[tuple[str, bool]]] | None = None,
) -> list[tuple[str, bool]]:
    if section_id != "search_seed_urls":
        return entries
    base = [seed for seed in expand_search_seed_urls(
        [marker for marker, _ in entries],
        settings=settings,
        digest_type=digest_type,
        section_entries=section_entries,
    )]
    seen: set[str] = set()
    merged: list[tuple[str, bool]] = []
    locked_by_marker = {marker: locked for marker, locked in entries}
    for seed in base:
        display = seed if seed.startswith(("http://", "https://")) else _normalize_marker(seed)
        key = _normalize_marker(display)
        if not key or key in seen:
            continue
        seen.add(key)
        merged.append((display, locked_by_marker.get(key, False)))
    return merged


def group_specs_for_digest_type(digest_type: str | None) -> tuple[tuple[str, str, bool, int], ...]:
    if is_curious_digest(digest_type):
        return CURIOUS_GROUP_SPECS
    return SERIOUS_GROUP_SPECS


def tier_seed_section_ids(digest_type: str | None) -> tuple[str, ...]:
    if is_curious_digest(digest_type):
        return CURIOUS_TIER_SEED_SECTIONS
    return SERIOUS_TIER_SEED_SECTIONS


def marker_to_listing_seed_url(marker: str) -> str | None:
    """
    Маркер домена или URL из модалки «Источники» → seed для HTTP-разбора лент на шаге 1.
    Полные https-URL сохраняются; голый домен → https://domain/.
    """
    raw = str(marker or "").strip()
    if not raw or raw.startswith("#"):
        return None
    if raw.startswith(("http://", "https://")):
        return raw.split("#", 1)[0].strip()
    norm = _normalize_marker(raw)
    if not norm or norm.endswith("."):
        return None
    if "/" in raw:
        return f"https://{raw.lstrip('/')}"
    if "." not in norm:
        return None
    return f"https://{norm}/"


def _tier_markers_from_sections(
    section_entries: dict[str, list[tuple[str, bool]]],
    digest_type: str | None,
) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for section_id in tier_seed_section_ids(digest_type):
        for marker, _locked in section_entries.get(section_id, []):
            m = _normalize_marker(marker)
            if not m or m in seen:
                continue
            seen.add(m)
            out.append(m)
    return out


def expand_search_seed_urls(
    base_seeds: tuple[str, ...] | list[str],
    *,
    settings: Any,
    digest_type: str | None,
    section_entries: dict[str, list[tuple[str, bool]]] | None = None,
) -> tuple[str, ...]:
    """search_seed_urls из файла + Telegram + seed-URL, собранные из tier-доменов модалки."""
    path = tiers_file_path(settings, digest_type)
    sections = section_entries if section_entries is not None else _load_section_entries(path)
    merged: list[str] = []
    seen: set[str] = set()
    for raw in (*base_seeds, *_telegram_seed_markers(settings)):
        seed = marker_to_listing_seed_url(raw) or str(raw or "").strip()
        if not seed:
            continue
        key = _normalize_marker(seed)
        if key in seen:
            continue
        seen.add(key)
        merged.append(seed)
    for marker in _tier_markers_from_sections(sections, digest_type):
        seed = marker_to_listing_seed_url(marker)
        if not seed:
            continue
        if _skip_redundant_homepage_seed(seed, merged):
            continue
        key = _normalize_marker(seed)
        if key in seen:
            continue
        seen.add(key)
        merged.append(seed)
    return tuple(merged)


def sync_tier_markers_to_search_seeds(updated: dict[str, list[str]], digest_type: str | None) -> None:
    """При сохранении модалки: tier-домены дублируются в search_seed_urls (как t.me/s/…)."""
    seeds = list(updated.get("search_seed_urls") or [])
    seen = {_normalize_marker(s) for s in seeds}
    for section_id in tier_seed_section_ids(digest_type):
        for marker in updated.get(section_id) or []:
            seed = marker_to_listing_seed_url(marker)
            if not seed:
                continue
            key = _normalize_marker(seed)
            if key in seen:
                continue
            seen.add(key)
            seeds.append(seed)
    updated["search_seed_urls"] = seeds


def _read_rules_sections(path: Path) -> dict[str, list[str]]:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    if _HOST_RULES_MARKER in raw:
        _, rules_block = raw.split(_HOST_RULES_MARKER, 1)
    else:
        rules_block = raw
    return _parse_host_rules(rules_block)


def _section_entries(section_lines: list[str]) -> list[tuple[str, bool]]:
    """(marker, locked) — locked для auto-unreachable."""
    out: list[tuple[str, bool]] = []
    locked_next = False
    for raw in section_lines:
        line = raw.rstrip()
        stripped = line.strip()
        if not stripped:
            continue
        if _AUTO_LINE_RE.match(stripped):
            locked_next = True
            continue
        if stripped.startswith("#"):
            continue
        marker = _normalize_marker(stripped)
        if not marker:
            continue
        out.append((marker, locked_next))
        locked_next = False
    return out


def _load_section_entries(path: Path) -> dict[str, list[tuple[str, bool]]]:
    sections = _read_rules_sections(path)
    return {key: _section_entries(lines) for key, lines in sections.items()}


def _collect_host_stats(db: Session, *, window_days: int) -> dict[str, dict[str, int]]:
    since = datetime.utcnow() - timedelta(days=max(1, int(window_days)))
    raw_by_host: dict[str, int] = {}

    registry_rows = (
        db.query(Step1UrlRegistry.host, Step1UrlRegistry.bucket, func.count(Step1UrlRegistry.id))
        .filter(Step1UrlRegistry.last_seen_at >= since)
        .group_by(Step1UrlRegistry.host, Step1UrlRegistry.bucket)
        .all()
    )
    for host, _bucket, cnt in registry_rows:
        h = str(host or "").lower()
        n = int(cnt or 0)
        raw_by_host[h] = raw_by_host.get(h, 0) + n

    pool_by_host: dict[str, int] = {}
    from app.models import Digest

    pool_rows = (
        db.query(NewsCandidate.url)
        .join(Digest, Digest.id == NewsCandidate.digest_id)
        .filter(NewsCandidate.page_verified.is_(True), Digest.updated_at >= since)
        .all()
    )
    for (url,) in pool_rows:
        h = _host_from_url(url)
        if h:
            pool_by_host[h] = pool_by_host.get(h, 0) + 1

    selected_by_host: dict[str, int] = {}
    selected_rows = (
        db.query(NewsCandidate.url)
        .join(SelectedNews, SelectedNews.candidate_id == NewsCandidate.id)
        .join(Digest, Digest.id == NewsCandidate.digest_id)
        .filter(Digest.updated_at >= since)
        .all()
    )
    for (url,) in selected_rows:
        h = _host_from_url(url)
        if h:
            selected_by_host[h] = selected_by_host.get(h, 0) + 1

    return {
        "raw": raw_by_host,
        "pool": pool_by_host,
        "selected": selected_by_host,
    }


def _stats_for_marker(marker: str, aggregates: dict[str, dict[str, int]]) -> SourceHostStatsOut:
    raw = pool = selected = 0
    for host, cnt in aggregates["raw"].items():
        if _marker_matches_host(marker, host):
            raw += cnt
    for host, cnt in aggregates["pool"].items():
        if _marker_matches_host(marker, host):
            pool += cnt
    for host, cnt in aggregates["selected"].items():
        if _marker_matches_host(marker, host):
            selected += cnt
    if pool > raw:
        raw = pool
    return SourceHostStatsOut(raw_count=raw, pool_count=pool, selected_count=selected)


def build_source_tiers_editor(db: Session, settings: Any, digest_type: str, *, window_days: int = 30) -> SourceTiersEditorOut:
    dtype = normalize_digest_type(digest_type)
    path = tiers_file_path(settings, dtype)
    section_entries = _load_section_entries(path)
    aggregates = _collect_host_stats(db, window_days=window_days)
    groups: list[SourceTierGroupOut] = []

    for section_id, label, is_blacklist, priority in group_specs_for_digest_type(dtype):
        entries = section_entries.get(section_id, [])
        if not entries:
            markers = _read_rules_sections(path).get(section_id, [])
            entries = [(_normalize_marker(m), False) for m in markers if _normalize_marker(m)]
        entries = _merge_section_entries(
            section_id,
            entries,
            settings=settings,
            digest_type=dtype,
            section_entries=section_entries,
        )
        seen: set[str] = set()
        hosts: list[SourceHostOut] = []
        for marker, locked in entries:
            if marker in seen:
                continue
            seen.add(marker)
            hosts.append(
                SourceHostOut(
                    marker=marker,
                    locked=locked,
                    stats=_stats_for_marker(marker, aggregates),
                )
            )
        groups.append(
            SourceTierGroupOut(
                id=section_id,
                label=label,
                priority=priority,
                is_blacklist=is_blacklist,
                hosts=hosts,
            )
        )

    return SourceTiersEditorOut(
        digest_type=dtype,
        window_days=max(1, int(window_days)),
        file_name=path.name,
        groups=groups,
    )


def _extract_auto_blocks(section_lines: list[str]) -> list[list[str]]:
    blocks: list[list[str]] = []
    i = 0
    while i < len(section_lines):
        line = section_lines[i]
        if _AUTO_LINE_RE.match(line.strip()):
            block = [line.rstrip()]
            if i + 1 < len(section_lines):
                nxt = section_lines[i + 1].strip()
                if nxt and not nxt.startswith("#") and not nxt.startswith("["):
                    block.append(section_lines[i + 1].rstrip())
                    i += 1
            blocks.append(block)
        i += 1
    return blocks


def _format_section_body(user_markers: list[str], existing_lines: list[str]) -> list[str]:
    auto_blocks = _extract_auto_blocks(existing_lines)
    auto_markers = {_normalize_marker(b[-1]) for b in auto_blocks if len(b) >= 2}
    out: list[str] = []
    seen: set[str] = set()
    for marker in user_markers:
        m = _normalize_marker(marker)
        if not m or m in seen or m in auto_markers:
            continue
        seen.add(m)
        out.append(m)
    for block in auto_blocks:
        out.extend(block)
    return out


def _rewrite_host_rules(path: Path, updated_sections: dict[str, list[str]]) -> None:
    raw = path.read_text(encoding="utf-8")
    if _HOST_RULES_MARKER in raw:
        prompt, rules_block = raw.split(_HOST_RULES_MARKER, 1)
        prompt_part = prompt + _HOST_RULES_MARKER + "\n"
    else:
        prompt_part = ""
        rules_block = raw

    sections = _parse_host_rules(rules_block)
    lines = rules_block.splitlines()
    new_lines: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            sec = stripped[1:-1].strip().lower()
            new_lines.append(line)
            i += 1
            old_body: list[str] = []
            while i < len(lines):
                nxt = lines[i]
                nxt_st = nxt.strip()
                if nxt_st.startswith("[") and nxt_st.endswith("]"):
                    break
                old_body.append(nxt)
                i += 1
            if sec in updated_sections:
                body = _format_section_body(updated_sections[sec], old_body)
            else:
                body = old_body
            new_lines.extend(body)
            continue
        new_lines.append(line)
        i += 1

    for sec_id, markers in updated_sections.items():
        if sec_id in sections:
            continue
        new_lines.append(f"[{sec_id}]")
        new_lines.extend(_format_section_body(markers, []))

    path.write_text(prompt_part + "\n".join(new_lines).rstrip() + "\n", encoding="utf-8")


def save_source_tiers_editor(
    db: Session,
    settings: Any,
    payload: SourceTiersEditorUpdate,
) -> SourceTiersEditorOut:
    dtype = normalize_digest_type(payload.digest_type)
    path = tiers_file_path(settings, dtype)
    allowed = {spec[0] for spec in group_specs_for_digest_type(dtype)}
    updated: dict[str, list[str]] = {}
    for group in payload.groups:
        if group.id not in allowed:
            continue
        markers: list[str] = []
        seen: set[str] = set()
        for host in group.hosts:
            m = _normalize_marker(host.marker)
            if not m or m in seen:
                continue
            seen.add(m)
            markers.append(m)
        updated[group.id] = markers
    if "search_seed_urls" not in updated:
        existing = _load_section_entries(path)
        updated["search_seed_urls"] = [marker for marker, _ in existing.get("search_seed_urls", [])]
    sync_tier_markers_to_search_seeds(updated, dtype)
    _rewrite_host_rules(path, updated)
    invalidate_policy_cache()
    if is_curious_digest(dtype):
        from app.curious_source_policy import invalidate_curious_policy_cache

        invalidate_curious_policy_cache()
    try:
        from app.config import get_settings

        get_settings.cache_clear()
    except AttributeError:
        pass
    return build_source_tiers_editor(db, settings, dtype)
