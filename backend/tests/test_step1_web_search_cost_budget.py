"""Регрессия: лимит ₽ web_search не обрывает сбор, пока пул не добран до минимума."""

from app.services.step1_web_search_stats import (
    reset_step1_web_search_stats,
    set_step1_web_search_cost_budget,
    step1_web_search_cost_budget_should_stop,
)


def test_cost_budget_does_not_stop_while_pool_short():
    reset_step1_web_search_stats()
    set_step1_web_search_cost_budget(50.0)
    from app.services.step1_web_search_stats import current_step1_web_search_stats

    stats = current_step1_web_search_stats()
    assert stats is not None
    stats.service_cost_est_rub = 40.0
    stats.token_cost_est_rub = 12.0

    assert step1_web_search_cost_budget_should_stop(verified_count=3, pool_shortfall=7) is False
    assert step1_web_search_cost_budget_should_stop(verified_count=3, min_verified=10) is False


def test_cost_budget_stops_after_minimum_reached():
    reset_step1_web_search_stats()
    set_step1_web_search_cost_budget(50.0)
    from app.services.step1_web_search_stats import current_step1_web_search_stats

    stats = current_step1_web_search_stats()
    assert stats is not None
    stats.service_cost_est_rub = 40.0
    stats.token_cost_est_rub = 12.0

    assert step1_web_search_cost_budget_should_stop(verified_count=10, min_verified=10) is True
    assert step1_web_search_cost_budget_should_stop(verified_count=12, pool_shortfall=0) is True


def test_hard_cost_limit_stops_even_when_pool_short():
    reset_step1_web_search_stats()
    set_step1_web_search_cost_budget(50.0)
    from app.services.step1_web_search_stats import current_step1_web_search_stats

    stats = current_step1_web_search_stats()
    assert stats is not None
    stats.service_cost_est_rub = 90.0
    stats.token_cost_est_rub = 15.0

    assert step1_web_search_cost_budget_should_stop(
        verified_count=4,
        pool_shortfall=6,
        hard_limit_rub=100.0,
    ) is True
