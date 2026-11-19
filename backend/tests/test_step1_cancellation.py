from app.services.step1_cancellation import (
    begin_run,
    end_run,
    is_cancelled,
    is_running,
    register_io_abort,
    request_cancel,
    should_abort_step1_web_search,
)


def test_step1_cancel_lifecycle():
    begin_run(42)
    assert is_running(42)
    assert not is_cancelled(42)
    assert request_cancel(42) is True
    assert is_cancelled(42)
    end_run(42)
    assert not is_running(42)
    assert not is_cancelled(42)
    assert request_cancel(42) is False


def test_cancel_blocks_new_web_search_calls():
    begin_run(7)
    assert request_cancel(7) is True
    from app.services.step1_web_search_stats import consume_web_search_api_call, reset_step1_web_search_stats

    reset_step1_web_search_stats()
    assert consume_web_search_api_call() is False
    end_run(7)


def test_request_cancel_closes_in_flight_io():
    begin_run(11)
    closed: list[int] = []
    register_io_abort(lambda: closed.append(1))
    assert request_cancel(11) is True
    assert closed == [1]
    assert should_abort_step1_web_search() is True
    end_run(11)


def test_fetch_article_urls_skips_search_when_cancelled(monkeypatch):
    from types import SimpleNamespace

    from app.services.news_search import fetch_article_urls_raw_merged

    begin_run(12)
    request_cancel(12)
    called: list[str] = []

    def boom(*_args, **_kwargs):
        called.append("proxy")
        raise AssertionError("web_search must not run after cancel")

    settings = SimpleNamespace(
        step1_web_search_prefer_alt_providers=False,
        serpapi_api_key="",
        tavily_api_key="",
        step1_min_urls_before_proxyapi=5,
        enable_web_fetch=True,
        proxyapi_web_search_enabled=True,
        step1_web_search_cache_enabled=False,
        step1_max_web_search_api_calls=0,
    )
    proxy = SimpleNamespace(search_news_article_urls=boom)
    assert fetch_article_urls_raw_merged(settings, "query", limit=10, proxy=proxy) == []
    assert called == []
    end_run(12)
