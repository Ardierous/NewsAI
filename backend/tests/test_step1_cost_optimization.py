"""Регрессия оптимизации стоимости шага 1 (шаги 1–3 плана)."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.pipeline_settings import read_pipeline_config
from app.services import news_search


def _settings(**overrides: object) -> SimpleNamespace:
    base = {
        "enable_web_fetch": True,
        "proxyapi_web_search_enabled": True,
        "serpapi_api_key": "serp-key",
        "tavily_api_key": None,
        "step1_web_search_prefer_alt_providers": True,
        "step1_min_urls_before_proxyapi": 5,
        "step1_tier_max_web_search_batches": 2,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def test_step1_config_economy_defaults():
    """Шаг 1: экономный конфиг в pipeline_settings.json."""
    cfg = read_pipeline_config()["step1"]
    assert cfg["max_cost_rub"] == 40.0
    assert cfg["telegram_via_proxyapi"] is False
    assert cfg["telegram_direct_fallback"] is True
    assert cfg["web_search_context_size"] == "low"
    assert cfg["tier_max_web_search_batches"] == 6
    assert cfg["web_search_prefer_alt_providers"] is False


def test_fetch_merged_skips_proxyapi_when_serpapi_enough(monkeypatch: pytest.MonkeyPatch):
    proxy_calls: list[str] = []

    def _fake_proxy_search(*_a, **_k):
        proxy_calls.append("proxy")
        return ["https://proxy-only.example/a"]

    def _fake_serpapi(_key, _q, _lim):
        return [f"https://ria.ru/news/2026/06/04/article-{i}" for i in range(8)]

    monkeypatch.setattr(news_search, "_serpapi_google_news_urls", _fake_serpapi)
    proxy = MagicMock()
    proxy.search_news_article_urls.side_effect = _fake_proxy_search

    urls = news_search.fetch_article_urls_raw_merged(
        _settings(),
        "after:2026-06-01 ИИ",
        limit=10,
        proxy=proxy,
    )
    assert len(urls) >= 5
    proxy.search_news_article_urls.assert_not_called()


def test_fetch_merged_uses_proxyapi_when_alt_sparse(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(news_search, "_serpapi_google_news_urls", lambda *_a, **_k: [])
    monkeypatch.setattr(news_search, "_tavily_search_urls", lambda *_a, **_k: [])

    proxy = MagicMock()
    proxy.search_news_article_urls.return_value = ["https://tass.ru/a/1"]

    urls = news_search.fetch_article_urls_raw_merged(_settings(), "ИИ", limit=10, proxy=proxy)
    assert urls == ["https://tass.ru/a/1"]
    proxy.search_news_article_urls.assert_called_once()


def test_tier_search_respects_max_batches(monkeypatch: pytest.MonkeyPatch):
    from app.source_tiers_policy import SourceTiersPolicy

    policy = SourceTiersPolicy(
        prompt_text="t",
        aggregator_hosts=(),
        tier1_hosts=("ria.ru", "tass.ru", "interfax.ru", "kommersant.ru"),
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

    def _fake_merged(settings, query, limit, *, proxy=None, search_context_size=None, **kwargs):
        calls.append(1)
        return [f"https://{limit}.example.com/a"]

    monkeypatch.setattr(news_search, "fetch_article_urls_raw_merged", _fake_merged)
    settings = _settings(step1_tier_max_web_search_batches=2)
    news_search.fetch_tier_prioritized_raw_urls(
        settings,
        window_prefix="",
        topic_terms="ИИ",
        product_excludes="",
        fetch_limit=20,
        proxy=MagicMock(),
        policy=policy,
    )
    assert len(calls) <= 2


def test_log_proxyapi_usage_reads_cached_tokens():
    from app.proxyapi_client import _extract_cached_tokens

    usage = SimpleNamespace(
        prompt_tokens=2000,
        completion_tokens=100,
        prompt_tokens_details=SimpleNamespace(cached_tokens=1920),
    )
    assert _extract_cached_tokens(usage) == 1920
