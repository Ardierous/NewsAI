"""Общие фикстуры тестов backend."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _disable_live_telegram_seeds(monkeypatch: pytest.MonkeyPatch) -> None:
    """Не ходить в t.me/s/ при unit-тестах шага 1."""
    monkeypatch.setattr(
        "app.services.digest_service.collect_telegram_seed_urls_for_digest",
        lambda *args, **kwargs: [],
    )
