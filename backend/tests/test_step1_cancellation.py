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
