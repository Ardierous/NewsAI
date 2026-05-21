import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.services import news_search


def test_extract_http_urls_from_json_array():
    raw = '["https://example.com/a", "https://techcrunch.com/b"]'
    urls = news_search.extract_http_urls_from_text(raw, limit=10)
    assert urls == ["https://example.com/a", "https://techcrunch.com/b"]


def test_bad_search_url_rejects_listing_sections():
    assert news_search._is_bad_search_url("https://www.unian.net/techno/neiroseti") is True
    assert news_search._is_bad_search_url("https://www.content-review.com/articles/artificial_intelligence/") is True
    assert news_search._is_bad_search_url("https://vc.ru/ai/12345-some-article-title") is False


def test_hallucinated_urls_rejected():
    assert news_search.url_suspected_hallucinated(
        "https://www.wsj.com/articles/openai-launches-gpt-5-with-enhanced-reasoning-abilities-2026-"
    )
    assert news_search.url_suspected_hallucinated(
        "https://tass.ru/ekonomika/15052026/mincifry-zapuskaet-programmu"
    )
    assert news_search._is_bad_search_url("https://www.kommersant.ru/doc/5678901")
    assert not news_search.url_suspected_hallucinated(
        "https://www.cnews.ru/news/top/2026-05-06_sozdateli_yandeksa_potratyat"
    )
    assert not news_search.url_suspected_hallucinated(
        "https://tass.ru/ekonomika/15543211/rosatom-zapuskaet-proekt-po-sozdaniyu-ii-dlya-upravleniya-energosistemami"
    )


def test_listing_page_urls_rejected():
    assert news_search.is_listing_page_url("https://shtruzel.ru/news") is True
    assert news_search.is_listing_page_url("https://arxiv.org/list/cs.CL/2024-03") is True
    assert news_search.is_listing_page_url("https://www.aiweekly.co/ai-news-today") is True
    assert news_search._is_bad_search_url("https://shtruzel.ru/news") is True
    assert news_search._is_bad_search_url("https://arxiv.org/list/cs.CL/2024-03") is True
    assert news_search.is_listing_page_url("https://www.1tv.ru/news/2026-04-26/540448") is False
    assert news_search.is_listing_page_url("https://arxiv.org/abs/2403.08295") is False
    assert news_search._is_bad_search_url("https://arxiv.org/abs/2403.08295") is True
    assert (
        news_search.is_listing_page_url(
            "https://habr.com/ru/hubs/artificial_intelligence/articles/top/yearly/page114/"
        )
        is True
    )
    assert news_search._is_bad_search_url("https://www.networkworld.com/artificial-intelligence/") is True


def test_topic_pool_urls_rejected():
    assert news_search.is_topic_pool_page_url("https://www.cnews.ru/book/mutual/8757/251081") is True
    assert news_search.is_topic_pool_page_url("https://www.cnews.ru/book/mutual/95/6095") is True
    assert news_search._is_bad_search_url("https://www.cnews.ru/book/mutual/95/6095") is True
    assert (
        news_search._is_bad_search_url(
            "https://www.cnews.ru/news/top/2026-05-06_sozdateli_yandeksa_potratyat"
        )
        is False
    )


def test_extract_http_urls_filters_aggregators():
    raw = '["https://news.google.com/articles/abc", "https://news.tek.fm/news/306335", "https://vc.ru/ai/123"]'
    urls = news_search.extract_http_urls_from_text(raw, limit=10)
    assert urls == ["https://vc.ru/ai/123"]


def test_fetch_article_urls_raw_merges_providers(monkeypatch: pytest.MonkeyPatch):
    settings = SimpleNamespace(
        enable_web_fetch=True,
        proxyapi_web_search_enabled=True,
        serpapi_api_key="serp",
        tavily_api_key="tav",
    )
    proxy = MagicMock()
    proxy.search_news_article_urls.return_value = ["https://ria.ru/20260519/a.html"]
    monkeypatch.setattr(
        news_search,
        "_serpapi_google_news_urls",
        lambda key, query, limit: ["https://www.interfax.ru/ai/b"],
    )
    monkeypatch.setattr(
        news_search,
        "_tavily_search_urls",
        lambda key, query, limit, include_domains=None: [
            "https://ria.ru/20260519/a.html",
            "https://habr.com/c",
        ],
    )
    raw = news_search.fetch_article_urls_raw_merged(settings, "AI", limit=10, proxy=proxy)
    assert "https://ria.ru/20260519/a.html" in raw
    assert "https://www.interfax.ru/ai/b" in raw
    assert "https://habr.com/c" in raw
    assert len(raw) == 3


def test_fetch_article_urls_proxyapi_first(monkeypatch: pytest.MonkeyPatch):
    settings = SimpleNamespace(
        enable_web_fetch=True,
        proxyapi_web_search_enabled=True,
        serpapi_api_key="serp",
        tavily_api_key=None,
    )
    proxy = MagicMock()
    proxy.search_news_article_urls.return_value = ["https://example.com/real"]

    urls = news_search.fetch_article_urls_from_search(
        settings, "AI news", limit=5, proxy=proxy
    )
    assert urls == ["https://example.com/real"]
    proxy.search_news_article_urls.assert_called_once()


def test_fetch_article_urls_falls_back_to_serpapi(monkeypatch: pytest.MonkeyPatch):
    settings = SimpleNamespace(
        enable_web_fetch=True,
        proxyapi_web_search_enabled=True,
        serpapi_api_key="serp",
        tavily_api_key=None,
    )
    proxy = MagicMock()
    proxy.search_news_article_urls.return_value = []
    serp_url = "https://www.vedomosti.ru/technologies/article/2026/05/19/ii-test"
    monkeypatch.setattr(
        news_search,
        "_serpapi_google_news_urls",
        lambda key, query, limit: [serp_url],
    )

    urls = news_search.fetch_article_urls_from_search(
        settings, "AI news", limit=5, proxy=proxy
    )
    assert urls == [serp_url]


def test_search_url_prefilter_non_policy_source_when_tier_strict():
    assert news_search.search_url_prefilter_reason(
        "https://random-blog.example.com/ai-news",
        tier_strict=True,
    ) == "non_policy_source"
    assert news_search.search_url_prefilter_reason(
        "https://random-blog.example.com/ai-news",
        tier_strict=False,
    ) is None
    assert news_search.search_url_prefilter_reason(
        "https://ria.ru/20260519/ai-story.html",
        tier_strict=True,
    ) is None


def test_fetch_tier_prioritized_raw_urls_batches_by_tier(monkeypatch: pytest.MonkeyPatch):
    from app.source_tiers_policy import SourceTiersPolicy

    policy = SourceTiersPolicy(
        prompt_text="test",
        aggregator_hosts=("news.google.",),
        tier1_hosts=("ria.ru", "tass.ru"),
        tier2_hosts=("techcrunch.com",),
        tier3_hosts=(),
        tier4_hosts=(),
        banned_media_hosts=(),
        foreign_agent_hosts=(),
        russian_host_markers=(),
        blocked_search_hosts=(),
        search_seed_urls=("https://ria.ru/product_iskusstvennyy-intellekt/",),
    )
    calls: list[dict] = []

    def _fake_fetch(settings, query, limit, *, proxy=None, search_context_size=None, include_domains=None, allowed_hosts=None):
        calls.append(
            {
                "query": query,
                "limit": limit,
                "include_domains": include_domains,
                "allowed_hosts": allowed_hosts,
            }
        )
        hosts = allowed_hosts or []
        out: list[str] = []
        if "ria.ru" in hosts:
            out.extend(["https://ria.ru/20260519/a.html", "https://news.google.com/x"])
        if "tass.ru" in hosts:
            out.append("https://tass.ru/ekonomika/123")
        if "techcrunch.com" in hosts:
            out.append("https://techcrunch.com/2026/ai-story")
        return out

    monkeypatch.setattr(news_search, "fetch_article_urls_raw_merged", _fake_fetch)
    settings = SimpleNamespace(enable_web_fetch=True, proxyapi_web_search_enabled=True)
    urls = news_search.fetch_tier_prioritized_raw_urls(
        settings,
        window_prefix="за неделю ",
        topic_terms="ИИ нейросети",
        product_excludes="-продукт",
        fetch_limit=20,
        proxy=MagicMock(),
        policy=policy,
    )
    assert "https://ria.ru/20260519/a.html" in urls
    assert "https://tass.ru/ekonomika/123" in urls
    assert "https://techcrunch.com/2026/ai-story" in urls
    assert all("news.google." not in u for u in urls)
    assert calls
    assert all(call["allowed_hosts"] for call in calls)
    assert "site:ria.ru" in calls[0]["query"] or "site:tass.ru" in calls[0]["query"]

