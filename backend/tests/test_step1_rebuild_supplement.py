"""Пересборка шага 1: по умолчанию дополняет пул, не удаляя текущих кандидатов."""

from __future__ import annotations

from datetime import date, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import Digest, NewsCandidate
from app.services.digest_service import DigestService


@pytest.fixture()
def db_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine, tables=[Digest.__table__, NewsCandidate.__table__])
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def test_rebuild_without_keep_ids_preserves_existing_candidates(db_session):
    digest = Digest(
        date=date(2026, 6, 16),
        digest_type="serious",
        status="step_1_candidates",
        current_step="step_1_candidates",
        news_window_days=7,
        news_window_day_kind="calendar",
    )
    db_session.add(digest)
    db_session.flush()
    db_session.add(
        NewsCandidate(
            digest_id=digest.id,
            original_number=1,
            title="Old one",
            url="https://ria.ru/old.html",
            source="RIA",
            published_at="2026-06-16",
            category="technology",
            description="d",
            page_verified=True,
            link_status=True,
            headline_editorial_ok=True,
        )
    )
    db_session.add(
        NewsCandidate(
            digest_id=digest.id,
            original_number=2,
            title="Old two",
            url="https://vedomosti.ru/old.html",
            source="Vedomosti",
            published_at="2026-06-16",
            category="technology",
            description="d",
            page_verified=True,
            link_status=True,
            headline_editorial_ok=True,
        )
    )
    db_session.commit()

    service = DigestService(db_session)
    service.settings = SimpleNamespace(
        enable_web_fetch=False,
        step1_telegram_monitor_enabled=False,
        source_tiers_path="app/prompts/source_tiers.txt",
        curious_source_hosts_path="app/prompts/curious_source_hosts.txt",
    )
    service.proxy = MagicMock()

    with patch.object(DigestService, "get_digest", return_value=digest):
        with patch.object(DigestService, "update_news_window", return_value=digest):
            with pytest.raises(Exception):
                service.run_step_1(digest.id, [], rebuild=True, keep_candidate_ids=[])

    remaining = (
        db_session.query(NewsCandidate)
        .filter(NewsCandidate.digest_id == digest.id)
        .order_by(NewsCandidate.original_number)
        .all()
    )
    assert len(remaining) == 2
