"""Загрузка правил tiers из одного файла (промпт для LLM + host_rules для кода)."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

_HOST_RULES_MARKER = "--- HOST_RULES ---"


@dataclass(frozen=True)
class SourceTiersPolicy:
    prompt_text: str
    aggregator_hosts: tuple[str, ...]
    tier1_hosts: tuple[str, ...]
    tier2_hosts: tuple[str, ...]
    tier3_hosts: tuple[str, ...]
    tier4_hosts: tuple[str, ...]
    banned_media_hosts: tuple[str, ...]
    foreign_agent_hosts: tuple[str, ...]
    russian_host_markers: tuple[str, ...]
    blocked_search_hosts: tuple[str, ...]
    search_seed_urls: tuple[str, ...]

    def prompt_for_llm(self) -> str:
        return self.prompt_text.strip() + "\n"


def _parse_host_rules(text: str) -> dict[str, list[str]]:
    sections: dict[str, list[str]] = {}
    current: str | None = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            current = line[1:-1].strip().lower()
            sections.setdefault(current, [])
            continue
        if current:
            sections[current].append(line)
    return sections


def _tuple_hosts(sections: dict[str, list[str]], key: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys(sections.get(key, ())))


def load_source_tiers(path: Path) -> SourceTiersPolicy:
    raw = path.read_text(encoding="utf-8")
    if _HOST_RULES_MARKER in raw:
        prompt, rules_block = raw.split(_HOST_RULES_MARKER, 1)
    else:
        prompt, rules_block = raw, ""
    sections = _parse_host_rules(rules_block)
    return SourceTiersPolicy(
        prompt_text=prompt,
        aggregator_hosts=_tuple_hosts(sections, "aggregator_hosts"),
        tier1_hosts=_tuple_hosts(sections, "tier1_hosts"),
        tier2_hosts=_tuple_hosts(sections, "tier2_hosts"),
        tier3_hosts=_tuple_hosts(sections, "tier3_hosts"),
        tier4_hosts=_tuple_hosts(sections, "tier4_hosts"),
        banned_media_hosts=_tuple_hosts(sections, "banned_media_hosts"),
        foreign_agent_hosts=_tuple_hosts(sections, "foreign_agent_hosts"),
        russian_host_markers=_tuple_hosts(sections, "russian_host_markers"),
        blocked_search_hosts=_tuple_hosts(sections, "blocked_search_hosts"),
        search_seed_urls=_tuple_hosts(sections, "search_seed_urls"),
    )


@lru_cache(maxsize=4)
def _cached_policy(path_str: str, mtime_ns: int) -> SourceTiersPolicy:
    return load_source_tiers(Path(path_str))


def get_source_tiers_policy(path: Path | None = None) -> SourceTiersPolicy:
    if path is None:
        from app.config import get_settings

        path = get_settings().source_tiers_path
    try:
        mtime_ns = path.stat().st_mtime_ns
    except OSError:
        mtime_ns = 0
    return _cached_policy(str(path.resolve()), mtime_ns)


def _host_from_url(url: str) -> str:
    try:
        h = urlparse(url).hostname
        return (h or "").replace("www.", "").lower()
    except Exception:
        return ""


def _host_contains_marker(host: str, markers: tuple[str, ...]) -> bool:
    return any(marker in host for marker in markers)


def is_tier5_forbidden_source(url: str, policy: SourceTiersPolicy | None = None) -> bool:
    p = policy or get_source_tiers_policy()
    host = _host_from_url(url)
    return _host_contains_marker(host, p.banned_media_hosts)


def is_foreign_agent_source(url: str, policy: SourceTiersPolicy | None = None) -> bool:
    p = policy or get_source_tiers_policy()
    host = _host_from_url(url)
    return _host_contains_marker(host, p.foreign_agent_hosts)


def is_aggregator_source(url: str, policy: SourceTiersPolicy | None = None) -> bool:
    p = policy or get_source_tiers_policy()
    host = _host_from_url(url).lower()
    low_url = url.lower()
    return any(marker in host or marker in low_url for marker in p.aggregator_hosts)


def is_blocked_search_host(url: str, policy: SourceTiersPolicy | None = None) -> bool:
    p = policy or get_source_tiers_policy()
    host = _host_from_url(url)
    return _host_contains_marker(host, p.blocked_search_hosts)


def is_russian_host(host: str, policy: SourceTiersPolicy | None = None) -> bool:
    p = policy or get_source_tiers_policy()
    h = str(host or "").lower().strip()
    if h.endswith(".ru") or h.endswith(".su"):
        return True
    return _host_contains_marker(h, p.russian_host_markers)


def is_policy_tier_source(url: str, policy: SourceTiersPolicy | None = None) -> bool:
    """URL с хоста tier-1…tier-4 из политики (не агрегатор и не tier-5)."""
    p = policy or get_source_tiers_policy()
    if is_tier5_forbidden_source(url, p) or is_aggregator_source(url, p) or is_blocked_search_host(url, p):
        return False
    host = _host_from_url(url)
    return (
        _host_contains_marker(host, p.tier1_hosts)
        or _host_contains_marker(host, p.tier2_hosts)
        or _host_contains_marker(host, p.tier3_hosts)
        or _host_contains_marker(host, p.tier4_hosts)
    )


def policy_tier_host_groups(policy: SourceTiersPolicy | None = None) -> tuple[tuple[str, tuple[str, ...]], ...]:
    """Группы хостов по приоритету: tier-1 → tier-4."""
    p = policy or get_source_tiers_policy()
    return (
        ("Tier-1", p.tier1_hosts),
        ("Tier-2", p.tier2_hosts),
        ("Tier-3", p.tier3_hosts),
        ("Tier-4", p.tier4_hosts),
    )


def batched_site_host_groups(hosts: tuple[str, ...], *, batch_size: int = 3) -> list[tuple[str, ...]]:
    """Разбивает маркеры хостов на батчи для site: запросов."""
    clean = [str(h or "").strip().lower() for h in hosts if str(h or "").strip()]
    if not clean:
        return []
    size = max(1, int(batch_size or 3))
    return [tuple(clean[i : i + size]) for i in range(0, len(clean), size)]


def classify_source_policy(
    url: str,
    policy: SourceTiersPolicy | None = None,
) -> tuple[str, bool, str]:
    """(tier, is_aggregator, reliability_status) по домену URL."""
    p = policy or get_source_tiers_policy()
    host = _host_from_url(url)
    if is_tier5_forbidden_source(url, p):
        return "Tier-5", False, "❗ без подтверждения"
    if is_aggregator_source(url, p):
        return "Tier-5", True, "❗ без подтверждения"
    if _host_contains_marker(host, p.tier1_hosts):
        return "Tier-1", False, "✅ подтверждено"
    if _host_contains_marker(host, p.tier2_hosts):
        return "Tier-2", False, "✅ подтверждено"
    if _host_contains_marker(host, p.tier3_hosts):
        return "Tier-3", False, "✅ подтверждено"
    if _host_contains_marker(host, p.tier4_hosts):
        return "Tier-4", False, "✅ подтверждено"
    return "Tier-3", False, "⚠️ сомнительный"


def invalidate_policy_cache() -> None:
    _cached_policy.cache_clear()
