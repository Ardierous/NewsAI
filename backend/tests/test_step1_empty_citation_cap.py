"""Пустые citation URL не съедают лимит web_search API шага 1."""

from __future__ import annotations

from app.services.step1_web_search_stats import (
    consume_web_search_api_call,
    record_empty_citation_web_search,
    refund_web_search_api_call,
    reset_step1_web_search_stats,
    step1_web_search_api_cap_reached,
)


def test_refund_restores_cap_slot():
    stats = reset_step1_web_search_stats()
    stats.api_cap = 5
    assert consume_web_search_api_call() is True
    assert stats.api_calls == 1
    refund_web_search_api_call()
    assert stats.api_calls == 0
    assert step1_web_search_api_cap_reached() is False


def test_empty_citation_streak_threshold():
    stats = reset_step1_web_search_stats()
    stats.api_cap = 50
    for _ in range(7):
        assert record_empty_citation_web_search(threshold=8) is False
    assert record_empty_citation_web_search(threshold=8) is True
    assert stats.empty_citation_streak == 8
