"""Локальный кэш batch ProxyAPI web_search в SQLite."""

from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import Step1WebSearchCache
from app.services import news_search
from app.services.step1_web_search_cache import (
    build_web_search_cache_key,
    get_cached_proxy_search_urls,
    purge_expired_web_search_cache,
    store_proxy_search_urls_cache,
)


@pytest.fixture
def cache_db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    db = Session()
    try:
        yield db
    finally:
        db.close()


def _settings(**overrides: object) -> SimpleNamespace:
    base = {
        "enable_web_fetch": True,
        "proxyapi_web_search_enabled": True,
        "step1_web_search_cache_enabled": True,
        "step1_web_search_cache_ttl_days": 90,
        "step1_web_search_prefer_alt_providers": False,
        "step1_min_urls_before_proxyapi": 5,
        "serpapi_api_key": None,
        "tavily_api_key": None,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def test_cache_key_stable_for_same_query():
    kwargs = dict(
        query="after:2026-05-07 before:2026-06-06 site:habr.com ИИ",
        limit=20,
        search_context_size="low",
        allowed_hosts=["habr.com", "vc.ru"],
        curious_search=True,
        proxy_fallback_on_empty=False,
    )
    assert build_web_search_cache_key(**kwargs) == build_web_search_cache_key(**kwargs)


def test_cache_key_changes_with_window_or_hosts():
    base = dict(
        query="after:2026-05-07 before:2026-06-06 site:habr.com ИИ",
        limit=20,
        search_context_size="low",
        allowed_hosts=["habr.com"],
        curious_search=False,
        proxy_fallback_on_empty=False,
    )
    k1 = build_web_search_cache_key(**base)
    k2 = build_web_search_cache_key(
        **{**base, "query": "after:2026-05-08 before:2026-06-07 site:habr.com ИИ"}
    )
    k3 = build_web_search_cache_key(**{**base, "allowed_hosts": ["vc.ru"]})
    assert k1 != k2
    assert k1 != k3


def test_store_and_hit(cache_db):
    settings = _settings()
    query = "after:2026-05-07 before:2026-06-06 site:habr.com курьёз"
    urls = ["https://habr.com/ru/news/2026/06/05/funny-ai/"]
    store_proxy_search_urls_cache(
        settings,
        urls,
        query=query,
        limit=15,
        search_context_size="low",
        allowed_hosts=["habr.com"],
        curious_search=True,
        proxy_fallback_on_empty=False,
        db=cache_db,
    )
    hit = get_cached_proxy_search_urls(
        settings,
        query=query,
        limit=15,
        search_context_size="low",
        allowed_hosts=["habr.com"],
        curious_search=True,
        proxy_fallback_on_empty=False,
        db=cache_db,
    )
    assert hit == urls
    row = cache_db.get(Step1WebSearchCache, build_web_search_cache_key(
        query=query,
        limit=15,
        search_context_size="low",
        allowed_hosts=["habr.com"],
        curious_search=True,
        proxy_fallback_on_empty=False,
    ))
    assert row is not None
    assert row.hit_count == 1


def test_expired_entry_is_purged(cache_db):
    settings = _settings(step1_web_search_cache_ttl_days=7)
    query = "after:2026-05-07 before:2026-06-06 site:vc.ru ИИ"
    key = build_web_search_cache_key(
        query=query,
        limit=10,
        search_context_size="low",
        allowed_hosts=["vc.ru"],
        curious_search=False,
        proxy_fallback_on_empty=False,
    )
    cache_db.add(
        Step1WebSearchCache(
            cache_key=key,
            urls_json='["https://vc.ru/ai/1"]',
            query_preview=query[:100],
            url_count=1,
            hit_count=0,
            created_at=datetime.utcnow() - timedelta(days=10),
        )
    )
    cache_db.commit()
    assert purge_expired_web_search_cache(settings, db=cache_db) == 1
    assert cache_db.get(Step1WebSearchCache, key) is None


def test_cache_miss_when_all_urls_outside_window(cache_db):
    settings = _settings()
    query = "after:2026-06-01 before:2026-06-07 site:habr.com ИИ"
    old_url = "https://habr.com/ru/news/2023/04/01/old-ai/"
    store_proxy_search_urls_cache(
        settings,
        [old_url],
        query=query,
        limit=10,
        search_context_size="low",
        allowed_hosts=["habr.com"],
        curious_search=False,
        proxy_fallback_on_empty=False,
        db=cache_db,
    )
    assert (
        get_cached_proxy_search_urls(
            settings,
            query=query,
            limit=10,
            search_context_size="low",
            allowed_hosts=["habr.com"],
            curious_search=False,
            proxy_fallback_on_empty=False,
            db=cache_db,
        )
        is None
    )


def test_fetch_merged_uses_cache_and_skips_proxy(monkeypatch: pytest.MonkeyPatch, cache_db):
    settings = _settings()
    query = "after:2026-06-01 before:2026-06-07 site:ria.ru serious"
    cached = ["https://ria.ru/20260605/example.html"]
    store_proxy_search_urls_cache(
        settings,
        cached,
        query=query,
        limit=15,
        search_context_size="low",
        allowed_hosts=["ria.ru"],
        curious_search=False,
        proxy_fallback_on_empty=False,
        db=cache_db,
    )

    import app.services.step1_web_search_cache as cache_mod

    orig_get = cache_mod.get_cached_proxy_search_urls
    orig_store = cache_mod.store_proxy_search_urls_cache

    def _get(settings_obj, **kwargs):
        kwargs["db"] = cache_db
        return orig_get(settings_obj, **kwargs)

    def _store(settings_obj, urls, **kwargs):
        kwargs["db"] = cache_db
        return orig_store(settings_obj, urls, **kwargs)

    monkeypatch.setattr(cache_mod, "get_cached_proxy_search_urls", _get)
    monkeypatch.setattr(cache_mod, "store_proxy_search_urls_cache", _store)

    from unittest.mock import MagicMock

    proxy = MagicMock()
    urls = news_search.fetch_article_urls_raw_merged(
        settings,
        query,
        limit=10,
        proxy=proxy,
        search_context_size="low",
        allowed_hosts=["ria.ru"],
    )
    assert urls == cached
    proxy.search_news_article_urls.assert_not_called()


def test_fetch_merged_bypasses_cache_on_force_proxyapi(monkeypatch: pytest.MonkeyPatch, cache_db):
    settings = _settings()
    query = "after:2026-06-01 before:2026-06-07 site:ria.ru serious"
    store_proxy_search_urls_cache(
        settings,
        ["https://ria.ru/20260605/cached.html"],
        query=query,
        limit=10,
        search_context_size="low",
        allowed_hosts=["ria.ru"],
        curious_search=False,
        proxy_fallback_on_empty=False,
        db=cache_db,
    )

    import app.services.step1_web_search_cache as cache_mod

    orig_get = cache_mod.get_cached_proxy_search_urls

    def _get(settings_obj, **kwargs):
        kwargs["db"] = cache_db
        return orig_get(settings_obj, **kwargs)

    monkeypatch.setattr(cache_mod, "get_cached_proxy_search_urls", _get)

    from unittest.mock import MagicMock

    proxy = MagicMock()
    proxy.search_news_article_urls.return_value = ["https://ria.ru/20260606/fresh.html"]
    urls = news_search.fetch_article_urls_raw_merged(
        settings,
        query,
        limit=10,
        proxy=proxy,
        search_context_size="low",
        allowed_hosts=["ria.ru"],
        force_proxyapi=True,
    )
    assert urls == ["https://ria.ru/20260606/fresh.html"]
    proxy.search_news_article_urls.assert_called_once()
