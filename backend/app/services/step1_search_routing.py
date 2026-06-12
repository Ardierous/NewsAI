"""
Маршрутизация веб-поиска шага 1.

- serious → source_tiers.txt (tier_strict)
- curious + curious_use_serious_tiers → source_tiers для сбора URL, curious_verify для тона
- curious без флага → curious_source_hosts.txt
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from app.services.digest_type_policy import is_curious_digest

SearchRoute = Literal["curious_hosts", "serious_tier", "legacy_open", "query_override"]


@dataclass(frozen=True)
class Step1SearchRouting:
    route: SearchRoute
    tier_strict: bool
    curious_strict: bool
    curious_verify: bool

    @property
    def uses_source_tiers(self) -> bool:
        return self.route == "serious_tier"

    @property
    def uses_curious_hosts(self) -> bool:
        return self.route == "curious_hosts"


def resolve_step1_search_routing(
    digest_type: str | None,
    *,
    query_override: str | None,
    tier_strict_setting: bool,
    curious_use_serious_tiers: bool = False,
) -> Step1SearchRouting:
    """
    Выбор контура поиска и prefilter для одного батча шага 1.

    - curious + curious_use_serious_tiers → source_tiers (как serious), фильтр тона curious на verify
    - curious без флага → curious_source_hosts.txt, curious_strict prefilter
    - serious + tier_strict → source_tiers.txt
    - иначе → legacy open query
    """
    curious = is_curious_digest(digest_type)
    if query_override is not None:
        return Step1SearchRouting(
            route="query_override",
            tier_strict=False,
            curious_strict=False,
            curious_verify=curious,
        )
    if curious and curious_use_serious_tiers:
        return Step1SearchRouting(
            route="serious_tier",
            tier_strict=True,
            curious_strict=False,
            curious_verify=True,
        )
    if curious:
        return Step1SearchRouting(
            route="curious_hosts",
            tier_strict=False,
            curious_strict=True,
            curious_verify=True,
        )
    if tier_strict_setting:
        return Step1SearchRouting(
            route="serious_tier",
            tier_strict=True,
            curious_strict=False,
            curious_verify=False,
        )
    return Step1SearchRouting(
        route="legacy_open",
        tier_strict=False,
        curious_strict=False,
        curious_verify=False,
    )
