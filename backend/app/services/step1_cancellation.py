"""Флаг отмены длительного сбора кандидатов шага 1 (по digest_id)."""

from __future__ import annotations

import threading

_lock = threading.Lock()
_running: set[int] = set()
_cancelled: set[int] = set()


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


def request_cancel(digest_id: int) -> bool:
    """Запросить остановку. True, если для digest_id идёт сбор."""
    with _lock:
        did = int(digest_id)
        if did not in _running:
            return False
        _cancelled.add(did)
        return True


def is_cancelled(digest_id: int) -> bool:
    with _lock:
        return int(digest_id) in _cancelled
