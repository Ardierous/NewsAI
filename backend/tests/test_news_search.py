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
        lambda key, query, limit: ["https://ria.ru/20260519/a.html", "https://habr.com/c"],
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
