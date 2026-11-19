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


@pytest.fixture(autouse=True)
def _disable_live_description_translation(monkeypatch: pytest.MonkeyPatch) -> None:
    """Не звать ProxyAPI при заполнении описаний кандидатов в тестах."""
    monkeypatch.setattr(
        "app.services.digest_service.DigestService._translate_blurb_to_russian",
        lambda self, url, text: text,
    )


@pytest.fixture(autouse=True)
def _skip_pool_title_translation_on_digest_load(monkeypatch: pytest.MonkeyPatch) -> None:
    """get_digest не ходит в ProxyAPI за переводом заголовков в unit-тестах."""
    monkeypatch.setattr(
        "app.services.digest_service.DigestService._ensure_russian_pool_titles",
        lambda self, digest_id, max_translate=15: 0,
    )
