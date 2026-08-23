"""
Маршрутизация веб-поиска шага 1.

- ai/style (unified) → source_tiers.txt (tier_strict) + добор curious/practical батчами
- legacy curious в API/БД нормализуется в serious_tier
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from app.services.digest_type_policy import normalize_digest_type
from app.services.digest_topic_policy import is_style_digest

SearchRoute = Literal["curious_hosts", "serious_tier", "style_tier", "legacy_open", "query_override"]


@dataclass(frozen=True)
class Step1SearchRouting:
    route: SearchRoute
    tier_strict: bool
    curious_strict: bool
    curious_verify: bool

    @property
    def uses_source_tiers(self) -> bool:
        return self.route in {"serious_tier", "style_tier"}

    @property
    def uses_curious_hosts(self) -> bool:
        return self.route == "curious_hosts"


def resolve_step1_search_routing(
    digest_type: str | None,
    *,
    digest_topic: str | None = None,
    query_override: str | None,
    tier_strict_setting: bool,
    curious_use_serious_tiers: bool = False,
) -> Step1SearchRouting:
    """
    Выбор контура поиска и prefilter для одного батча шага 1.

    - style → source_tiers_style.txt (tier_strict)
    - ai (unified) → source_tiers (tier_strict); curious_hosts не используется в UX
    - query_override → open query
    """
    _ = normalize_digest_type(digest_type)
    _ = curious_use_serious_tiers
    if is_style_digest(digest_topic):
        return Step1SearchRouting(
            route="style_tier",
            tier_strict=True,
            curious_strict=False,
            curious_verify=False,
        )
    if query_override is not None:
        return Step1SearchRouting(
            route="query_override",
            tier_strict=False,
            curious_strict=False,
            curious_verify=False,
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
