"""ProxyAPI web_search для seed URL из t.me/s/technokratos."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app.proxyapi_client import ProxyApiClient


def _settings(**kwargs):
    base = {
        "proxyapi_api_key": "test-key",
        "proxyapi_base_url": "https://openai.api.proxyapi.ru/v1",
        "proxyapi_model": "openai/gpt-4.1",
        "proxyapi_image_model": "openai/gpt-image-1",
        "proxyapi_web_search_enabled": True,
        "proxyapi_web_search_model": "gpt-4o-mini",
        "proxyapi_web_search_preview_model": "gpt-4o-mini-search-preview",
        "proxyapi_web_search_context_size": "medium",
    }
    base.update(kwargs)
    return SimpleNamespace(**base)


def test_fetch_telegram_digest_returns_urls_from_responses():
    client = ProxyApiClient.__new__(ProxyApiClient)
    client.settings = _settings()
    client.last_error_kind = None
    client._last_api_response = None
    client.client = MagicMock()

    resp = MagicMock()
    resp.output_text = '["https://habr.com/ru/news/1/"]'
    resp.output = []
    client.client.responses.create.return_value = resp

    with patch("app.proxyapi_client.extract_urls_from_responses_payload", return_value=["https://habr.com/ru/news/1/"]):
        urls, html_pages = client.fetch_telegram_digest_seed_urls(
            "technokratos",
            max_digest_posts=3,
            post_text_filter="Дайджест",
        )

    assert urls == ["https://habr.com/ru/news/1/"]
    assert html_pages == []
    tools = client.client.responses.create.call_args.kwargs["tools"]
    assert tools[0]["search_context_size"] == "high"
    prompt = client.client.responses.create.call_args.kwargs["input"][0]["content"]
    assert "t.me/s/technokratos" in prompt
    assert "Дайджест" in prompt


def test_fetch_telegram_digest_disabled_returns_empty():
    client = ProxyApiClient.__new__(ProxyApiClient)
    client.settings = _settings(proxyapi_web_search_enabled=False)
    client.client = MagicMock()

    urls, html_pages = client.fetch_telegram_digest_seed_urls("technokratos")
    assert urls == []
    assert html_pages == []
    client.client.responses.create.assert_not_called()
