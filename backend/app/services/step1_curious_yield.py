"""Бюджет и скорость сбора курьёзного пула шага 1: ≥10 кандидатов, ≤10 мин, ≤50 ₽."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.services.digest_type_policy import is_curious_digest


@dataclass(frozen=True)
class CuriousYieldPolicy:
    enabled: bool = True
    min_verified: int = 10
    target_pool: int = 12
    soft_time_sec: int = 480
    hard_time_sec: int = 600
    max_cost_rub: float = 50.0
    min_iterations: int = 2
    supplement_rounds: int = 2
    max_search_batches: int = 5
    skip_aggregator_search: bool = True
    no_progress_limit: int = 2
    seed_listing_scan_limit: int = 4
    entertainment_rescue_queries: int = 2
    rescue_collect_batch: int = 8


def curious_yield_policy_from_settings(settings: Any) -> CuriousYieldPolicy | None:
    if not bool(getattr(settings, "step1_curious_yield_enabled", True)):
        return None
    return CuriousYieldPolicy(
        enabled=True,
        min_verified=max(10, int(getattr(settings, "step1_curious_yield_min_verified", 10) or 10)),
        target_pool=max(10, int(getattr(settings, "step1_curious_yield_target_pool", 12) or 12)),
        soft_time_sec=max(60, int(getattr(settings, "step1_curious_yield_soft_time_sec", 480) or 480)),
        hard_time_sec=max(120, int(getattr(settings, "step1_curious_yield_hard_time_sec", 600) or 600)),
        max_cost_rub=float(getattr(settings, "step1_curious_yield_max_cost_rub", 50.0) or 50.0),
        min_iterations=max(1, int(getattr(settings, "step1_curious_yield_min_iterations", 2) or 2)),
        supplement_rounds=max(0, int(getattr(settings, "step1_curious_yield_supplement_rounds", 2) or 2)),
        max_search_batches=max(1, int(getattr(settings, "step1_curious_yield_max_search_batches", 5) or 5)),
        skip_aggregator_search=bool(getattr(settings, "step1_curious_yield_skip_aggregator_search", True)),
        no_progress_limit=max(1, int(getattr(settings, "step1_curious_yield_no_progress_limit", 2) or 2)),
        seed_listing_scan_limit=max(0, int(getattr(settings, "step1_curious_yield_seed_listing_scan_limit", 4) or 4)),
        entertainment_rescue_queries=max(
            0, int(getattr(settings, "step1_curious_yield_entertainment_rescue_queries", 2) or 2)
        ),
        rescue_collect_batch=max(4, int(getattr(settings, "step1_curious_yield_rescue_collect_batch", 8) or 8)),
    )


def apply_curious_yield_limits(
    settings: Any,
    *,
    digest_type: str | None,
    soft_time_sec: int,
    hard_time_sec: int,
    collection_target: int,
    min_iterations: int,
) -> tuple[int, int, int, int, CuriousYieldPolicy | None]:
    """Подрезает лимиты под curious yield; для serious — без изменений."""
    if not is_curious_digest(digest_type):
        return soft_time_sec, hard_time_sec, collection_target, min_iterations, None
    policy = curious_yield_policy_from_settings(settings)
    if policy is None:
        return soft_time_sec, hard_time_sec, collection_target, min_iterations, None
    soft = min(soft_time_sec, policy.soft_time_sec)
    hard = min(max(soft, hard_time_sec), policy.hard_time_sec)
    target = min(collection_target, policy.target_pool)
    iters = min(min_iterations, policy.min_iterations)
    return soft, hard, target, iters, policy


def curious_yield_min_met(verified_count: int, policy: CuriousYieldPolicy | None) -> bool:
    if policy is None:
        return verified_count >= 10
    return verified_count >= policy.min_verified
