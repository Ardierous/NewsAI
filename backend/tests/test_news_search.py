import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.services import news_search


def test_extract_http_urls_from_json_array():
    raw = '["https://example.com/a", "https://techcrunch.com/b"]'
    urls = news_search.extract_http_urls_from_text(raw, limit=10)
    assert urls == ["https://example.com/a", "https://techcrunch.com/b"]


def test_extract_http_urls_filters_aggregators():
    raw = '["https://news.google.com/articles/abc", "https://vc.ru/ai/123"]'
    urls = news_search.extract_http_urls_from_text(raw, limit=10)
    assert urls == ["https://vc.ru/ai/123"]


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
    monkeypatch.setattr(
        news_search,
        "_serpapi_google_news_urls",
        lambda key, query, limit: ["https://serpapi.com/article"],
    )

    urls = news_search.fetch_article_urls_from_search(
        settings, "AI news", limit=5, proxy=proxy
    )
    assert urls == ["https://serpapi.com/article"]
