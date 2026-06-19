"""Оптимизация ProxyAPI web_search: fallback только при ошибке API, context size."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app.proxyapi_client import ProxyApiClient, build_web_search_user_prompt, set_proxyapi_log_context


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
        "source_tiers_path": MagicMock(),
    }
    base.update(kwargs)
    return SimpleNamespace(**base)


def test_search_empty_responses_does_not_call_preview(monkeypatch: pytest.MonkeyPatch):
    client = ProxyApiClient.__new__(ProxyApiClient)
    client.settings = _settings()
    client.last_error_kind = None
    client._last_api_response = None
    client.client = MagicMock()

    empty_resp = MagicMock()
    empty_resp.output_text = "[]"
    empty_resp.output = []
    client.client.responses.create.return_value = empty_resp

    preview = MagicMock()
    monkeypatch.setattr(client, "_search_news_urls_chat_preview", preview)

    urls = client.search_news_article_urls("AI news", limit=5)
    assert urls == []
    preview.assert_not_called()


def test_search_api_error_calls_preview(monkeypatch: pytest.MonkeyPatch):
    client = ProxyApiClient.__new__(ProxyApiClient)
    client.settings = _settings()
    client.last_error_kind = None
    client._last_api_response = None
    client.client = MagicMock()
    client.client.responses.create.side_effect = RuntimeError("API down")

    preview = MagicMock(return_value=["https://example.com/a"])
    monkeypatch.setattr(client, "_search_news_urls_chat_preview", preview)

    urls = client.search_news_article_urls("AI news", limit=5)
    assert urls == ["https://example.com/a"]
    preview.assert_called_once()
    assert preview.call_args.kwargs.get("search_context_size") == "medium"


def test_search_passes_supplement_context_size(monkeypatch: pytest.MonkeyPatch):
    client = ProxyApiClient.__new__(ProxyApiClient)
    client.settings = _settings()
    client.last_error_kind = None
    client._last_api_response = None
    client.client = MagicMock()

    resp = MagicMock()
    resp.output_text = '["https://ria.ru/20260519/a.html"]'
    resp.output = []
    client.client.responses.create.return_value = resp

    with patch("app.proxyapi_client.extract_urls_from_responses_payload", return_value=["https://ria.ru/20260519/a.html"]):
        client.search_news_article_urls("q", limit=3, search_context_size="low")

    tools = client.client.responses.create.call_args.kwargs["tools"]
    assert tools[0]["search_context_size"] == "low"
    assert client.client.responses.create.call_args.kwargs["max_output_tokens"] == 220


def test_search_passes_proxyapi_log_headers(monkeypatch: pytest.MonkeyPatch):
    client = ProxyApiClient.__new__(ProxyApiClient)
    client.settings = _settings()
    client.last_error_kind = None
    client._last_api_response = None
    client.client = MagicMock()

    resp = MagicMock()
    resp.output_text = '["https://ria.ru/20260519/a.html"]'
    resp.output = []
    client.client.responses.create.return_value = resp
    set_proxyapi_log_context(DigestId=42, Step="step1", DigestType="curious")

    with patch("app.proxyapi_client.extract_urls_from_responses_payload", return_value=["https://ria.ru/20260519/a.html"]):
        client.search_news_article_urls("q", limit=3, search_context_size="low")

    headers = client.client.responses.create.call_args.kwargs["extra_headers"]
    assert headers["X-Log-DigestId"] == "42"
    assert headers["X-Log-Step"] == "step1"
    assert headers["X-Log-DigestType"] == "curious"
    assert headers["X-Log-Source"] == "step1_web_search"
    set_proxyapi_log_context()


def test_build_web_search_user_prompt_includes_query_and_hosts():
    query = "after:2026-06-01 before:2026-06-16 ИИ (site:habr.com OR site:vc.ru)"
    prompt = build_web_search_user_prompt(
        query,
        12,
        curious_search=True,
        allowed_hosts=["habr.com", "vc.ru"],
        source_tiers_path=MagicMock(),
    )
    assert query in prompt
    assert "Поисковый запрос" in prompt
    assert "habr.com, vc.ru" in prompt
    assert "курьёзного дайджеста" in prompt
    assert "Тема выпуска" in prompt
    assert "LLM" in prompt


def test_build_web_search_user_prompt_serious_topic_anchor():
    prompt = build_web_search_user_prompt(
        "after:2026-06-01 AI",
        10,
        curious_search=False,
        allowed_hosts=["ria.ru", "tass.ru"],
    )
    assert "Найди до 10 СВЕЖИХ новостей про ИИ, нейросети, LLM" in prompt
    assert "Тема выпуска: новости про искусственный интеллект" in prompt
    assert "Гигачат" in prompt
    assert "Искать ТОЛЬКО на доменах из политики источников: ria.ru, tass.ru" in prompt
