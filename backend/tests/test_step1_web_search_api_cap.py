"""Потолок ProxyAPI web_search за один прогон шага 1."""

from __future__ import annotations

import pytest

from app.services.step1_web_search_stats import (
    consume_web_search_api_call,
    reset_step1_web_search_stats,
    set_step1_web_search_api_cap,
    step1_web_search_api_cap_reached,
)


@pytest.fixture(autouse=True)
def _isolated_step1_web_search_stats():
    reset_step1_web_search_stats()
    yield
    reset_step1_web_search_stats()


def test_consume_respects_cap_and_marks_hit():
    reset_step1_web_search_stats()
    set_step1_web_search_api_cap(2)
    assert consume_web_search_api_call(kind="responses") is True
    assert consume_web_search_api_call(kind="preview") is True
    assert consume_web_search_api_call(kind="responses") is False
    assert step1_web_search_api_cap_reached() is True


def test_cap_grows_with_bonus_near_target():
    from app.services.digest_service import DigestService
    from app.config import Settings

    svc = DigestService.__new__(DigestService)
    svc.settings = Settings(step1_max_cost_rub=50.0, step1_max_web_search_api_calls=0)
    svc._step1_curious_yield = None
    base = svc._effective_step1_web_search_api_cap(0)
    near = svc._effective_step1_web_search_api_cap(8)
    assert base == 43
    assert near == base + 10


def test_explicit_cap_overrides_cost_formula():
    from app.services.digest_service import DigestService
    from app.config import Settings

    svc = DigestService.__new__(DigestService)
    svc.settings = Settings(step1_max_web_search_api_calls=60, step1_web_search_api_bonus_near_target=5)
    svc._step1_curious_yield = None
    assert svc._effective_step1_web_search_api_cap(0) == 60
    assert svc._effective_step1_web_search_api_cap(8) == 65
