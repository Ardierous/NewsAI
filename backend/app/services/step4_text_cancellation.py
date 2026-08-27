"""Флаг отмены длительной генерации текстов шага 4 (по digest_id)."""

from __future__ import annotations

import threading

_lock = threading.Lock()
_running: set[int] = set()
_cancelled: set[int] = set()


class Step4TextsCancelledError(Exception):
    """Генерация текстов шага 4 прервана по запросу пользователя."""


def begin_run(digest_id: int) -> None:
    with _lock:
        did = int(digest_id)
        _running.add(did)
        _cancelled.discard(did)


def end_run(digest_id: int) -> None:
    with _lock:
        did = int(digest_id)
        _running.discard(did)
        _cancelled.discard(did)


def is_running(digest_id: int) -> bool:
    with _lock:
        return int(digest_id) in _running


def request_cancel(digest_id: int) -> bool:
    with _lock:
        did = int(digest_id)
        if did not in _running:
            return False
        _cancelled.add(did)
        return True


def is_cancelled(digest_id: int) -> bool:
    with _lock:
        return int(digest_id) in _cancelled

