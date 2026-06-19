from types import SimpleNamespace

from app.services.step1_curious_yield import apply_curious_yield_limits, curious_yield_min_met


def test_apply_curious_yield_limits_caps_time_and_pool() -> None:
    settings = SimpleNamespace(
        step1_curious_yield_enabled=True,
        step1_curious_yield_min_verified=10,
        step1_curious_yield_target_pool=12,
        step1_curious_yield_soft_time_sec=480,
        step1_curious_yield_hard_time_sec=600,
        step1_curious_yield_max_cost_rub=50.0,
        step1_curious_yield_min_iterations=2,
        step1_curious_yield_supplement_rounds=2,
        step1_curious_yield_max_search_batches=5,
        step1_curious_yield_skip_aggregator_search=True,
        step1_curious_yield_no_progress_limit=2,
        step1_curious_yield_seed_listing_scan_limit=4,
        step1_curious_yield_entertainment_rescue_queries=2,
        step1_curious_yield_rescue_collect_batch=8,
    )
    soft, hard, target, iters, policy = apply_curious_yield_limits(
        settings,
        digest_type="curious",
        soft_time_sec=900,
        hard_time_sec=1200,
        collection_target=30,
        min_iterations=5,
    )
    assert policy is not None
    assert soft == 480
    assert hard == 600
    assert target == 12
    assert iters == 2
    assert curious_yield_min_met(10, policy)
    assert not curious_yield_min_met(9, policy)


def test_apply_curious_yield_limits_skips_serious() -> None:
    settings = SimpleNamespace(step1_curious_yield_enabled=True)
    soft, hard, target, iters, policy = apply_curious_yield_limits(
        settings,
        digest_type="serious",
        soft_time_sec=200,
        hard_time_sec=300,
        collection_target=20,
        min_iterations=5,
    )
    assert policy is None
    assert soft == 200
    assert target == 20
