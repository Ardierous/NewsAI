"""Политика доменов для курьёзного выпуска (отдельно от source_tiers.txt)."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlparse

# Ленты/агрегаторы вне [aggregator_hosts] в curious_source_hosts.txt.
_EXTRA_CURIOUS_AGGREGATOR_HOST_MARKERS: tuple[str, ...] = (
    "pulse.mail.ru",
    "tgstat.ru",
    "telemetr.io",
    "telemetr.me",
)

from app.source_tiers_policy import _host_contains_marker, _host_from_url, _parse_host_rules, _tuple_hosts

_HOST_RULES_MARKER = "--- HOST_RULES ---"


@dataclass(frozen=True)
class CuriousSourcePolicy:
    curious_tier1_hosts: tuple[str, ...]
    curious_tier2_hosts: tuple[str, ...]
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
        return tuple(dict.fromkeys(self.curious_tier1_hosts + self.curious_tier2_hosts))


def _is_russian_marker_host(host: str, markers: tuple[str, ...]) -> bool:
    host = (host or "").lower()
    if host.endswith(".ru") or host.endswith(".su"):
        return True
    return _host_contains_marker(host, markers)


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
    foreign = _tuple_hosts(sections, "curious_foreign_hosts")
    if not ru_ent and legacy_ru:
        ru_ent = legacy_ru
    tier1 = _tuple_hosts(sections, "curious_tier1_hosts")
    tier2 = _tuple_hosts(sections, "curious_tier2_hosts")
    russian_markers = _tuple_hosts(sections, "russian_host_markers")
    if not tier1:
        tier1 = tuple(dict.fromkeys((*ru_ent, *(h for h in foreign if h in {
            "reddit.com", "9gag.com", "imgur.com", "theverge.com", "futurism.com", "mashable.com", "particle.news",
        }))))
    if not tier2:
        tier2 = tuple(dict.fromkeys((*ru_tech, *(h for h in foreign if h not in tier1))))
    return CuriousSourcePolicy(
        curious_tier1_hosts=tier1,
        curious_tier2_hosts=tier2,
        curious_ru_entertainment_hosts=ru_ent,
        curious_ru_tech_hosts=ru_tech,
        curious_foreign_hosts=foreign,
        aggregator_hosts=_tuple_hosts(sections, "aggregator_hosts"),
        banned_media_hosts=_tuple_hosts(sections, "banned_media_hosts"),
        blocked_search_hosts=_tuple_hosts(sections, "blocked_search_hosts"),
        russian_host_markers=russian_markers,
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


def curious_tier_priority(tier: str | None) -> int:
    """1 = Curious-T1 (лучше), 2 = Curious-T2, 9 = вне политики / запрет."""
    value = str(tier or "").strip()
    if value == "Curious-T1":
        return 1
    if value == "Curious-T2":
        return 2
    return 9


def is_curious_aggregator_source(url: str, policy: CuriousSourcePolicy | None = None) -> bool:
    p = policy or get_curious_source_policy()
    host = _host_from_url(url).lower()
    low_url = url.lower()
    if any(marker in host or marker in low_url for marker in _EXTRA_CURIOUS_AGGREGATOR_HOST_MARKERS):
        return True
    return any(marker in host or marker in low_url for marker in p.aggregator_hosts)


def curious_aggregator_host_markers(policy: CuriousSourcePolicy | None = None) -> tuple[str, ...]:
    p = policy or get_curious_source_policy()
    return tuple(dict.fromkeys((*p.aggregator_hosts, *_EXTRA_CURIOUS_AGGREGATOR_HOST_MARKERS)))


def is_curious_blocked_host(url: str, policy: CuriousSourcePolicy | None = None) -> bool:
    p = policy or get_curious_source_policy()
    host = _host_from_url(url)
    return _host_contains_marker(host, p.blocked_search_hosts) or _host_contains_marker(
        host, p.banned_media_hosts
    )


def is_curious_policy_source(url: str, policy: CuriousSourcePolicy | None = None) -> bool:
    """URL с домена Curious-T1/T2 или агрегатора (навигация, не blacklist)."""
    p = policy or get_curious_source_policy()
    if is_curious_blocked_host(url, p):
        return False
    if is_curious_aggregator_source(url, p):
        return True
    host = _host_from_url(url)
    return _host_contains_marker(host, p.curious_tier1_hosts) or _host_contains_marker(
        host, p.curious_tier2_hosts
    )


def is_curious_russian_host(url: str, policy: CuriousSourcePolicy | None = None) -> bool:
    p = policy or get_curious_source_policy()
    host = _host_from_url(url)
    return _is_russian_marker_host(host, p.russian_host_markers)


def curious_host_search_groups(policy: CuriousSourcePolicy | None = None) -> tuple[tuple[str, tuple[str, ...]], ...]:
    p = policy or get_curious_source_policy()
    groups: list[tuple[str, tuple[str, ...]]] = []
    if p.curious_tier1_hosts:
        groups.append(("Curious-T1", p.curious_tier1_hosts))
    if p.curious_tier2_hosts:
        groups.append(("Curious-T2", p.curious_tier2_hosts))
    if p.aggregator_hosts:
        groups.append(("Curious-T2-Aggregators", p.aggregator_hosts))
    return tuple(groups)


def classify_curious_source(url: str, policy: CuriousSourcePolicy | None = None) -> tuple[str, bool, str]:
    p = policy or get_curious_source_policy()
    if is_curious_blocked_host(url, p):
        return "Curious-T5", False, "❗ без подтверждения"
    if is_curious_aggregator_source(url, p):
        return "Curious-T2", True, "⚠️ сомнительный"
    host = _host_from_url(url)
    if _host_contains_marker(host, p.curious_tier1_hosts):
        return "Curious-T1", False, "✅ подтверждено"
    if _host_contains_marker(host, p.curious_tier2_hosts):
        return "Curious-T2", False, "✅ подтверждено"
    return "Curious-T5", False, "❗ без подтверждения"


def _load_gray_zone_hosts(path: Path) -> tuple[str, ...]:
    if not path.is_file():
        return ()
    hosts: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        token = line.split("#", 1)[0].strip().lower()
        if token and not token.startswith("["):
            hosts.append(token)
    return tuple(dict.fromkeys(hosts))


@lru_cache(maxsize=4)
def _cached_gray_zone_hosts(path_str: str, mtime_ns: int) -> tuple[str, ...]:
    return _load_gray_zone_hosts(Path(path_str))


def get_curious_gray_zone_hosts(path: Path | None = None) -> tuple[str, ...]:
    if path is None:
        from app.config import get_settings

        path = get_settings().curious_gray_zone_hosts_path
    try:
        mtime_ns = path.stat().st_mtime_ns
    except OSError:
        mtime_ns = 0
    return _cached_gray_zone_hosts(str(path.resolve()), mtime_ns)


def is_curious_gray_zone_source(url: str, policy: CuriousSourcePolicy | None = None) -> bool:
    """Домен из серой зоны: не tier-1/2 и не агрегатор, но допускается на prefilter."""
    p = policy or get_curious_source_policy()
    if is_curious_blocked_host(url, p):
        return False
    if is_curious_aggregator_source(url, p):
        return False
    host = _host_from_url(url)
    if _host_contains_marker(host, p.curious_tier1_hosts) or _host_contains_marker(host, p.curious_tier2_hosts):
        return False
    return _host_contains_marker(host, get_curious_gray_zone_hosts())


def is_curious_allowed_source(url: str, policy: CuriousSourcePolicy | None = None) -> bool:
    """Белый список (tier/aggregator) или серая зона."""
    return is_curious_policy_source(url, policy) or is_curious_gray_zone_source(url, policy)


def invalidate_curious_policy_cache() -> None:
    _cached_curious_policy.cache_clear()
    _cached_gray_zone_hosts.cache_clear()
