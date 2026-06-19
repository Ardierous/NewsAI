"""Citations-only URL из ProxyAPI web_search и счётчики вызовов."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app.services import news_search
from app.services.step1_web_search_stats import (
    current_step1_web_search_stats,
    reset_step1_web_search_stats,
)


def _responses_with_citation_and_json() -> SimpleNamespace:
    ann = SimpleNamespace(type="url_citation", url="https://habr.com/ru/news/2026/06/06/funny-ai/")
    block = SimpleNamespace(
        type="output_text",
        text='["https://www.example.com/invented-by-model"]',
        annotations=[ann],
    )
    item = SimpleNamespace(content=[block])
    return SimpleNamespace(
        output_text='["https://www.example.com/invented-by-model"]',
        output=[item],
    )


def test_extract_responses_vetted_model_fallback_when_no_citations():
    """При пустых citations — берём только проверенные URL из текста модели."""
    good = "https://habr.com/ru/articles/1042282/"
    bad = "https://www.example.com/invented-by-model-"
    block = SimpleNamespace(
        type="output_text",
        text=f'["{bad}", "{good}"]',
        annotations=[],
    )
    item = SimpleNamespace(content=[block])
    resp = SimpleNamespace(output_text=f'["{bad}", "{good}"]', output=[item])
    urls = news_search.extract_urls_from_responses_payload(resp, limit=10, citations_only=True)
    assert urls == [good]


def test_extract_responses_strict_skips_vetted_when_no_citations():
    """Serious: без citations не добираем URL из текста модели."""
    good = "https://habr.com/ru/articles/1042282/"
    block = SimpleNamespace(
        type="output_text",
        text=f'["{good}"]',
        annotations=[],
    )
    item = SimpleNamespace(content=[block])
    resp = SimpleNamespace(output_text=f'["{good}"]', output=[item])
    news_search.set_step1_strict_web_search_citations(True)
    try:
        urls = news_search.extract_urls_from_responses_payload(resp, limit=10, citations_only=True)
        assert urls == []
    finally:
        news_search.set_step1_strict_web_search_citations(False)


def test_extract_responses_citations_only_ignores_model_json():
    resp = _responses_with_citation_and_json()
    urls = news_search.extract_urls_from_responses_payload(resp, limit=10, citations_only=True)
    assert urls == ["https://habr.com/ru/news/2026/06/06/funny-ai/"]


def test_extract_responses_vetted_supplements_when_citations_sparse():
    ann = SimpleNamespace(type="url_citation", url="https://habr.com/ru/news/2026/06/06/funny-ai/")
    extra = "https://vc.ru/ai/12345-funny-story"
    block = SimpleNamespace(
        type="output_text",
        text=f'["{extra}"]',
        annotations=[ann],
    )
    item = SimpleNamespace(content=[block])
    resp = SimpleNamespace(output_text=f'["{extra}"]', output=[item])
    urls = news_search.extract_urls_from_responses_payload(resp, limit=10, citations_only=True)
    assert "https://habr.com/ru/news/2026/06/06/funny-ai/" in urls
    assert extra in urls


def test_extract_responses_legacy_mode_includes_model_json():
    ann = SimpleNamespace(type="url_citation", url="https://habr.com/ru/news/2026/06/06/funny-ai/")
    extra = "https://ria.ru/20260606/extra-from-model.html"
    block = SimpleNamespace(
        type="output_text",
        text=f'["{extra}"]',
        annotations=[ann],
    )
    item = SimpleNamespace(content=[block])
    resp = SimpleNamespace(output_text=f'["{extra}"]', output=[item])
    urls = news_search.extract_urls_from_responses_payload(resp, limit=10, citations_only=False)
    assert "https://habr.com/ru/news/2026/06/06/funny-ai/" in urls
    assert extra in urls


def test_extract_chat_citations_only_uses_annotations():
    ann = SimpleNamespace(type="url_citation", url="https://ria.ru/20260606/a.html")
    message = SimpleNamespace(
        content='["https://invented.example/x"]',
        annotations=[ann],
    )
    choice = SimpleNamespace(message=message)
    resp = SimpleNamespace(choices=[choice])
    urls = news_search.extract_urls_from_chat_web_search_response(resp, limit=5, citations_only=True)
    assert urls == ["https://ria.ru/20260606/a.html"]


def test_web_search_stats_record_api_and_citations():
    reset_step1_web_search_stats()
    from app.services.step1_web_search_stats import (
        record_web_search_api_call,
        record_web_search_citation_urls,
        record_web_search_est_cost,
    )

    record_web_search_api_call()
    record_web_search_api_call()
    record_web_search_citation_urls(3, model_urls_dropped=5)
    record_web_search_est_cost(service_rub=1.0, token_rub=0.35)
    record_web_search_est_cost(service_rub=1.0, token_rub=0.12)
    stats = current_step1_web_search_stats()
    assert stats is not None
    assert stats.api_calls == 2
    assert stats.citation_urls == 3
    assert stats.model_urls_dropped == 5
    assert stats.service_cost_est_rub == 2.0
    assert stats.token_cost_est_rub == pytest.approx(0.47)
    meta: dict = {}
    stats.apply_to_meta(meta)
    assert meta["web_search_api_calls"] == 2
    assert meta["web_search_citation_urls"] == 3
    assert meta["web_search_cost_est_rub"] == pytest.approx(2.47)


def test_log_proxyapi_usage_without_usage_records_service_fee():
    from app.proxyapi_client import _log_proxyapi_usage

    reset_step1_web_search_stats()
    resp = SimpleNamespace(id="resp-empty-usage")
    _log_proxyapi_usage(resp, kind="responses.web_search", model="gpt-4o-mini")
    stats = current_step1_web_search_stats()
    assert stats is not None
    assert stats.service_cost_est_rub == 1.0
    assert stats.token_cost_est_rub == 0.0
    assert stats.service_cost_est_rub + stats.token_cost_est_rub == 1.0


def test_proxy_search_counts_api_call(monkeypatch: pytest.MonkeyPatch):
    from app.proxyapi_client import ProxyApiClient

    reset_step1_web_search_stats()
    client = ProxyApiClient.__new__(ProxyApiClient)
    client.settings = SimpleNamespace(
        proxyapi_web_search_enabled=True,
        proxyapi_web_search_model="gpt-4o-mini",
        proxyapi_web_search_preview_model="gpt-4o-mini-search-preview",
        proxyapi_web_search_context_size="low",
        source_tiers_path=MagicMock(),
    )
    client.last_error_kind = None
    client._last_api_response = None
    client.client = MagicMock()
    client.client.responses.create.return_value = _responses_with_citation_and_json()

    urls = client.search_news_article_urls("after:2026-06-01 ИИ", limit=5)
    assert urls == ["https://habr.com/ru/news/2026/06/06/funny-ai/"]
    stats = current_step1_web_search_stats()
    assert stats is not None
    assert stats.api_calls == 1
    assert stats.service_cost_est_rub == 1.0
