"""Строгий лимит web_search API: без refund и без раздувания tier-батчей."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.services.news_search import fetch_tier_prioritized_raw_urls
from app.services.step1_web_search_stats import (
    consume_web_search_api_call,
    reset_step1_web_search_stats,
    set_step1_web_search_api_cap,
    step1_web_search_api_cap_reached,
)


@pytest.fixture(autouse=True)
def _isolated_stats():
    reset_step1_web_search_stats()
    yield
    reset_step1_web_search_stats()


def test_tier_search_stops_after_explicit_api_cap(monkeypatch: pytest.MonkeyPatch):
    from app.source_tiers_policy import SourceTiersPolicy

    policy = SourceTiersPolicy(
        prompt_text="t",
        aggregator_hosts=(),
        tier1_hosts=("ria.ru", "tass.ru", "interfax.ru"),
        tier2_hosts=("vedomosti.ru",),
        tier3_hosts=(),
        tier4_hosts=(),
        banned_media_hosts=(),
        foreign_agent_hosts=(),
        russian_host_markers=(),
        blocked_search_hosts=(),
        search_seed_urls=(),
    )
    calls: list[str] = []

    def _fake_merged(settings, query, limit, *, proxy=None, **kwargs):
        if not consume_web_search_api_call():
            return []
        calls.append(query[:40])
        return [f"https://ria.ru/a/{len(calls)}"]

    monkeypatch.setattr("app.services.news_search.fetch_article_urls_raw_merged", _fake_merged)
    set_step1_web_search_api_cap(1, strict=True)
    settings = SimpleNamespace(
        step1_max_web_search_api_calls=1,
        step1_tier_max_web_search_batches=1,
        step1_web_search_prefer_alt_providers=False,
        step1_min_urls_before_proxyapi=5,
        enable_web_fetch=True,
        proxyapi_web_search_enabled=True,
    )
    fetch_tier_prioritized_raw_urls(
        settings,
        window_prefix="",
        topic_terms="ИИ",
        product_excludes="",
        fetch_limit=20,
        proxy=MagicMock(),
        policy=policy,
        current_verified=0,
        pool_shortfall=10,
    )
    assert len(calls) == 1


def test_tier_search_stops_outer_loop_once_on_api_cap(monkeypatch: pytest.MonkeyPatch):
    """После cap не перебираем все tier-группы — одно предупреждение, один батч."""
    from app.source_tiers_policy import SourceTiersPolicy

    policy = SourceTiersPolicy(
        prompt_text="t",
        aggregator_hosts=(),
        tier1_hosts=("ria.ru", "tass.ru"),
        tier2_hosts=("vedomosti.ru", "forbes.ru"),
        tier3_hosts=("habr.com",),
        tier4_hosts=(),
        banned_media_hosts=(),
        foreign_agent_hosts=(),
        russian_host_markers=(),
        blocked_search_hosts=(),
        search_seed_urls=(),
    )
    calls: list[int] = []

    def _fake_merged(settings, query, limit, *, proxy=None, **kwargs):
        if not consume_web_search_api_call():
            return []
        calls.append(1)
        return []

    monkeypatch.setattr("app.services.news_search.fetch_article_urls_raw_merged", _fake_merged)
    set_step1_web_search_api_cap(1, strict=True)
    settings = SimpleNamespace(
        step1_max_web_search_api_calls=1,
        step1_tier_max_web_search_batches=3,
        step1_web_search_prefer_alt_providers=False,
        step1_min_urls_before_proxyapi=5,
        enable_web_fetch=True,
        proxyapi_web_search_enabled=True,
    )
    fetch_tier_prioritized_raw_urls(
        settings,
        window_prefix="",
        topic_terms="ИИ",
        product_excludes="",
        fetch_limit=20,
        proxy=MagicMock(),
        policy=policy,
        current_verified=0,
        pool_shortfall=10,
    )
    assert len(calls) == 1
    assert step1_web_search_api_cap_reached()
