"""Флаг отмены длительного сбора кандидатов шага 1 (по digest_id)."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

_lock = threading.Lock()
_running: set[int] = set()
_cancelled: set[int] = set()
_io_abort_hooks: list[Callable[[], None]] = []
logger = logging.getLogger("app.digest")


class Step1CancelledError(Exception):
    """Сбор шага 1 прерван по запросу пользователя."""


def begin_run(digest_id: int) -> None:
    with _lock:
        _running.add(int(digest_id))
        _cancelled.discard(int(digest_id))


def end_run(digest_id: int) -> None:
    with _lock:
        did = int(digest_id)
        _running.discard(did)
        _cancelled.discard(did)


def is_running(digest_id: int) -> bool:
    with _lock:
        return int(digest_id) in _running


def register_io_abort(hook: Callable[[], None]) -> None:
    """Закрыть сокет текущего HTTP/поиска, когда пользователь нажал «стоп»."""
    with _lock:
        _io_abort_hooks.append(hook)


def unregister_io_abort(hook: Callable[[], None]) -> None:
    with _lock:
        try:
            _io_abort_hooks.remove(hook)
        except ValueError:
            pass


def abort_in_flight_io() -> None:
    with _lock:
        hooks = list(_io_abort_hooks)
        _io_abort_hooks.clear()
    for hook in hooks:
        try:
            hook()
        except Exception:
            logger.debug("Шаг 1: обрыв сетевого запроса при отмене", exc_info=True)


def request_cancel(digest_id: int) -> bool:
    """Запросить остановку. True, если для digest_id идёт сбор."""
    with _lock:
        did = int(digest_id)
        if did not in _running:
            return False
        _cancelled.add(did)
        try:
            from app.services.step1_live_progress import mark_live_cancel_requested

            mark_live_cancel_requested(did)
        except ImportError:
            pass
    abort_in_flight_io()
    return True


def is_cancelled(digest_id: int) -> bool:
    with _lock:
        return int(digest_id) in _cancelled


def should_abort_step1_web_search() -> bool:
    """True — новые платные web_search-вызовы для активного digest не запускать."""
    with _lock:
        return bool(_cancelled)


@contextmanager
def abortable_requests_session() -> Iterator[Any]:
    """requests.Session, который закрывается сразу при отмене шага 1."""
    import requests

    session = requests.Session()
    register_io_abort(session.close)
    try:
        yield session
    finally:
        unregister_io_abort(session.close)
        try:
            session.close()
        except Exception:
            pass
