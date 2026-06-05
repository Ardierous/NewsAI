"""Политика доменов для курьёзного выпуска (отдельно от source_tiers.txt)."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlparse

from app.source_tiers_policy import _host_contains_marker, _host_from_url, _parse_host_rules, _tuple_hosts

_HOST_RULES_MARKER = "--- HOST_RULES ---"


@dataclass(frozen=True)
class CuriousSourcePolicy:
    curious_ru_entertainment_hosts: tuple[str, ...]
    curious_ru_tech_hosts: tuple[str, ...]
    curious_foreign_hosts: tuple[str, ...]
    aggregator_hosts: tuple[str, ...]
    banned_media_hosts: tuple[str, ...]
    blocked_search_hosts: tuple[str, ...]
    russian_host_markers: tuple[str, ...]
    search_seed_urls: tuple[str, ...]

    @property
    def curious_ru_hosts(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(self.curious_ru_entertainment_hosts + self.curious_ru_tech_hosts))

    def all_search_hosts(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(self.curious_ru_hosts + self.curious_foreign_hosts))


def load_curious_source_policy(path: Path) -> CuriousSourcePolicy:
    raw = path.read_text(encoding="utf-8")
    if _HOST_RULES_MARKER in raw:
        _, rules_block = raw.split(_HOST_RULES_MARKER, 1)
    else:
        rules_block = raw
    sections = _parse_host_rules(rules_block)
    legacy_ru = _tuple_hosts(sections, "curious_ru_hosts")
    ru_ent = _tuple_hosts(sections, "curious_ru_entertainment_hosts")
    ru_tech = _tuple_hosts(sections, "curious_ru_tech_hosts")
    if not ru_ent and legacy_ru:
        ru_ent = legacy_ru
    return CuriousSourcePolicy(
        curious_ru_entertainment_hosts=ru_ent,
        curious_ru_tech_hosts=ru_tech,
        curious_foreign_hosts=_tuple_hosts(sections, "curious_foreign_hosts"),
        aggregator_hosts=_tuple_hosts(sections, "aggregator_hosts"),
        banned_media_hosts=_tuple_hosts(sections, "banned_media_hosts"),
        blocked_search_hosts=_tuple_hosts(sections, "blocked_search_hosts"),
        russian_host_markers=_tuple_hosts(sections, "russian_host_markers"),
        search_seed_urls=_tuple_hosts(sections, "search_seed_urls"),
    )


@lru_cache(maxsize=4)
def _cached_curious_policy(path_str: str, mtime_ns: int) -> CuriousSourcePolicy:
    return load_curious_source_policy(Path(path_str))


def get_curious_source_policy(path: Path | None = None) -> CuriousSourcePolicy:
    if path is None:
        from app.config import get_settings

        path = get_settings().curious_source_hosts_path
    try:
        mtime_ns = path.stat().st_mtime_ns
    except OSError:
        mtime_ns = 0
    return _cached_curious_policy(str(path.resolve()), mtime_ns)


def is_curious_aggregator_source(url: str, policy: CuriousSourcePolicy | None = None) -> bool:
    p = policy or get_curious_source_policy()
    host = _host_from_url(url).lower()
    low_url = url.lower()
    return any(marker in host or marker in low_url for marker in p.aggregator_hosts)


def is_curious_blocked_host(url: str, policy: CuriousSourcePolicy | None = None) -> bool:
    p = policy or get_curious_source_policy()
    host = _host_from_url(url)
    return _host_contains_marker(host, p.blocked_search_hosts) or _host_contains_marker(
        host, p.banned_media_hosts
    )


def is_curious_policy_source(url: str, policy: CuriousSourcePolicy | None = None) -> bool:
    """URL с домена курьёзного списка или агрегатора Tier-2 (поиск, не blacklist)."""
    p = policy or get_curious_source_policy()
    if is_curious_blocked_host(url, p):
        return False
    if is_curious_aggregator_source(url, p):
        return True
    host = _host_from_url(url)
    return _host_contains_marker(host, p.curious_ru_hosts) or _host_contains_marker(
        host, p.curious_foreign_hosts
    )


def is_curious_russian_host(url: str, policy: CuriousSourcePolicy | None = None) -> bool:
    p = policy or get_curious_source_policy()
    host = _host_from_url(url)
    if host.endswith(".ru") or host.endswith(".su"):
        return True
    return _host_contains_marker(host, p.russian_host_markers)


def curious_host_search_groups(policy: CuriousSourcePolicy | None = None) -> tuple[tuple[str, tuple[str, ...]], ...]:
    p = policy or get_curious_source_policy()
    groups: list[tuple[str, tuple[str, ...]]] = []
    if p.curious_ru_entertainment_hosts:
        groups.append(("Curious-RU-Entertainment", p.curious_ru_entertainment_hosts))
    if p.curious_ru_tech_hosts:
        groups.append(("Curious-RU-Tech", p.curious_ru_tech_hosts))
    if p.curious_foreign_hosts:
        groups.append(("Curious-Foreign", p.curious_foreign_hosts))
    if p.aggregator_hosts:
        groups.append(("Curious-Tier2-Aggregators", p.aggregator_hosts))
    return tuple(groups)


def classify_curious_source(url: str, policy: CuriousSourcePolicy | None = None) -> tuple[str, bool, str]:
    p = policy or get_curious_source_policy()
    if is_curious_blocked_host(url, p):
        return "Tier-5", False, "❗ без подтверждения"
    if is_curious_aggregator_source(url, p):
        return "Tier-2", True, "⚠️ сомнительный"
    if not is_curious_policy_source(url, p):
        return "Tier-5", False, "❗ без подтверждения"
    if is_curious_russian_host(url, p):
        return "Tier-1", False, "✅ подтверждено"
    return "Tier-2", False, "✅ подтверждено"


def invalidate_curious_policy_cache() -> None:
    _cached_curious_policy.cache_clear()
