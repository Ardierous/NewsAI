from app.services.step1_cancellation import (
    begin_run,
    end_run,
    is_cancelled,
    is_running,
    request_cancel,
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
