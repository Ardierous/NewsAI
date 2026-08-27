"""Порядок сбора URL на шаге 1: дешёвые ленты/реестр до tier web_search."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from datetime import date

import pytest

from app.services.digest_service import DigestService
from app.source_tiers_policy import count_urls_on_host_markers, get_source_tiers_policy


def test_count_urls_on_host_markers():
    policy = get_source_tiers_policy()
    urls = [
        "https://ria.ru/20260616/ai-123.html",
        "https://vc.ru/ai/123",
        "https://example.com/x",
    ]
    assert count_urls_on_host_markers(urls, policy.tier1_hosts) == 1


def _make_service(settings_overrides: dict | None = None) -> tuple[DigestService, SimpleNamespace]:
    db = MagicMock()
    settings = {
        "source_tiers_path": Path("app/prompts/source_tiers.txt"),
        "step1_tier_strict_search": True,
        "step1_curious_use_serious_tiers": False,
        "step1_serious_use_curious_tiers": True,
        "step1_serious_curious_search_batches": 4,
        "step1_serious_curious_extra_batches": 0,
        "step1_search_fetch_limit": 36,
        "step1_urls_checked_per_collect": 24,
        "step1_search_tier1_min_raw_urls": 15,
        "step1_tier_max_web_search_batches": 2,
        "step1_max_web_search_api_calls": 0,
        "step1_max_cost_rub": 50.0,
        "step1_web_search_api_bonus_near_target": 0,
        "step1_cheap_sources_first": True,
        "step1_seed_urls_max": 24,
        "proxyapi_web_search_context_size_supplement": "low",
        "enable_web_fetch": True,
    }
    if settings_overrides:
        settings.update(settings_overrides)
    service = DigestService(db)
    service.settings = SimpleNamespace(**settings)
    service.proxy = MagicMock()
    digest = SimpleNamespace(
        id=99,
        digest_type="serious",
        news_window_days=30,
        news_window_day_kind="calendar",
        date=date(2026, 6, 16),
        proxyapi_budget_used_before=None,
        proxyapi_budget_used_after=None,
        proxyapi_balance_before=None,
        proxyapi_balance_after=None,
    )
    return service, digest


def test_cheap_sources_run_before_tier_web_search(monkeypatch: pytest.MonkeyPatch):
    call_order: list[str] = []

    def _fake_tier(*_a, **_k):
        call_order.append("tier")
        yield "https://ria.ru/20260616/ai-tier.html"

    def _fake_registry(*_a, **_k):
        call_order.append("registry")
        return 1

    def _fake_listing(self, digest, *, seen_raw, raw_unique, skip_urls, max_seeds=None, max_children_per_seed=3):
        call_order.append("listing")
        raw_unique.append("https://ria.ru/20260616/from-listing.html")
        return 1

    monkeypatch.setattr(
        "app.services.digest_service.fetch_tier_prioritized_raw_urls",
        _fake_tier,
    )
    monkeypatch.setattr(
        "app.services.digest_service.fetch_curious_prioritized_raw_urls",
        lambda *_a, **_k: [],
    )
    monkeypatch.setattr(
        "app.services.digest_service.fetch_article_urls_raw_merged",
        lambda *_a, **_k: [],
    )
    monkeypatch.setattr(
        DigestService,
        "_step1_seed_collect_from_registry",
        _fake_registry,
    )
    monkeypatch.setattr(
        DigestService,
        "_step1_add_seed_listing_raw_urls",
        _fake_listing,
    )
    monkeypatch.setattr(
        DigestService,
        "_ingest_step1_urls_with_listing_expansion",
        lambda *_a, **_k: [],
    )
    monkeypatch.setattr(
        DigestService,
        "_step1_search_query_parts",
        lambda self, digest: ("after:2026-06-01 ", "ИИ", "-pricing"),
    )
    monkeypatch.setattr(
        "app.services.step1_web_search_stats.reset_empty_citation_streak",
        lambda: None,
    )
    monkeypatch.setattr(
        "app.services.digest_service.register_raw_urls",
        lambda *a, **k: 0,
    )
    monkeypatch.setattr(
        DigestService,
        "_step1_prioritize_search_urls",
        lambda self, urls, digest=None: list(urls),
    )

    service, digest = _make_service()
    service._collect_search_verified_candidates(
        99,
        digest,
        "2026-06-16T12:00:00+03:00",
        set(),
        limit=10,
        verified_pool_size=0,
    )

    assert call_order[:3] == ["registry", "listing", "tier"]


def test_cheap_sources_skip_tier_when_listing_fills_raw(monkeypatch: pytest.MonkeyPatch):
    call_order: list[str] = []

    def _fake_tier(*_a, **_k):
        call_order.append("tier")
        yield "https://ria.ru/20260616/ai-tier.html"

    def _fake_registry(*_a, **_k):
        call_order.append("registry")
        return 0

    def _fake_listing(self, digest, *, seen_raw, raw_unique, skip_urls, max_seeds=None, max_children_per_seed=3):
        call_order.append("listing")
        for i in range(30):
            raw_unique.append(f"https://ria.ru/20260616/list-{i}.html")
        return 30

    monkeypatch.setattr("app.services.digest_service.fetch_tier_prioritized_raw_urls", _fake_tier)
    monkeypatch.setattr("app.services.digest_service.fetch_curious_prioritized_raw_urls", lambda *_a, **_k: [])
    monkeypatch.setattr("app.services.digest_service.fetch_article_urls_raw_merged", lambda *_a, **_k: [])
    monkeypatch.setattr(DigestService, "_step1_seed_collect_from_registry", _fake_registry)
    monkeypatch.setattr(DigestService, "_step1_add_seed_listing_raw_urls", _fake_listing)
    monkeypatch.setattr(DigestService, "_ingest_step1_urls_with_listing_expansion", lambda *_a, **_k: [])
    monkeypatch.setattr(DigestService, "_step1_search_query_parts", lambda self, d: ("after:2026-06-01 ", "ИИ", "-pricing"))
    monkeypatch.setattr("app.services.step1_web_search_stats.reset_empty_citation_streak", lambda: None)
    monkeypatch.setattr("app.services.digest_service.register_raw_urls", lambda *a, **k: 0)
    monkeypatch.setattr(DigestService, "_step1_prioritize_search_urls", lambda self, urls, digest=None: list(urls))

    service, digest = _make_service()
    service._collect_search_verified_candidates(
        99,
        digest,
        "2026-06-16T12:00:00+03:00",
        set(),
        limit=10,
        verified_pool_size=0,
    )

    assert "listing" in call_order
    assert "tier" not in call_order


def test_full_pool_skips_tier_web_search(monkeypatch: pytest.MonkeyPatch):
    tier_called = {"v": False}

    def _fake_tier(*_a, **_k):
        tier_called["v"] = True
        yield "https://ria.ru/20260616/ai-tier.html"

    monkeypatch.setattr("app.services.digest_service.fetch_tier_prioritized_raw_urls", _fake_tier)
    monkeypatch.setattr("app.services.digest_service.fetch_curious_prioritized_raw_urls", lambda *_a, **_k: [])
    monkeypatch.setattr("app.services.digest_service.fetch_article_urls_raw_merged", lambda *_a, **_k: [])
    monkeypatch.setattr(DigestService, "_step1_seed_collect_from_registry", lambda *_a, **_k: 0)
    monkeypatch.setattr(DigestService, "_step1_add_seed_listing_raw_urls", lambda *_a, **_k: 0)
    monkeypatch.setattr(DigestService, "_ingest_step1_urls_with_listing_expansion", lambda *_a, **_k: [])
    monkeypatch.setattr(DigestService, "_step1_search_query_parts", lambda self, d: ("after:2026-06-01 ", "ИИ", "-pricing"))
    monkeypatch.setattr("app.services.step1_web_search_stats.reset_empty_citation_streak", lambda: None)
    monkeypatch.setattr("app.services.digest_service.register_raw_urls", lambda *a, **k: 0)
    monkeypatch.setattr(DigestService, "_step1_prioritize_search_urls", lambda self, urls, digest=None: list(urls))

    service, digest = _make_service()
    service._collect_search_verified_candidates(
        99,
        digest,
        "2026-06-16T12:00:00+03:00",
        set(),
        limit=10,
        verified_pool_size=10,
    )

    assert tier_called["v"] is False


def test_short_pool_applies_extra_curious_batches(monkeypatch: pytest.MonkeyPatch):
    seen: dict[str, int] = {}

    monkeypatch.setattr("app.services.digest_service.fetch_tier_prioritized_raw_urls", lambda *_a, **_k: [])

    def _fake_curious(*_a, **kwargs):
        seen["max_search_batches"] = int(kwargs.get("max_search_batches") or 0)
        return []

    monkeypatch.setattr("app.services.digest_service.fetch_curious_prioritized_raw_urls", _fake_curious)
    monkeypatch.setattr(DigestService, "_step1_seed_collect_from_registry", lambda *_a, **_k: 0)
    monkeypatch.setattr(DigestService, "_step1_add_seed_listing_raw_urls", lambda *_a, **_k: 0)
    monkeypatch.setattr(DigestService, "_ingest_step1_urls_with_listing_expansion", lambda *_a, **_k: [])
    monkeypatch.setattr(DigestService, "_step1_search_query_parts", lambda self, d: ("after:2026-06-01 ", "ИИ", "-pricing"))
    monkeypatch.setattr("app.services.step1_web_search_stats.reset_empty_citation_streak", lambda: None)
    monkeypatch.setattr("app.services.digest_service.register_raw_urls", lambda *a, **k: 0)
    monkeypatch.setattr(DigestService, "_step1_prioritize_search_urls", lambda self, urls, digest=None: list(urls))

    service, digest = _make_service({"step1_serious_curious_search_batches": 4, "step1_serious_curious_extra_batches": 2})
    service._collect_search_verified_candidates(
        99,
        digest,
        "2026-06-16T12:00:00+03:00",
        set(),
        limit=10,
        verified_pool_size=0,
    )

    assert seen["max_search_batches"] == 6
