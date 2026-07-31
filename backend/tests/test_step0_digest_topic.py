"""Шаг 0: сохранение digest_topic и разведение выпусков по темам."""

from datetime import date

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import Analytics, Digest, FinalOutput, NewsCandidate, SelectedNews, Step1DiscoveredNews
from app.services.digest_service import DigestService


@pytest.fixture()
def db_session():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


def test_run_step_0_saves_digest_topic_style(db_session) -> None:
    digest = Digest(date=date(2026, 7, 31), status="draft", current_step="draft")
    db_session.add(digest)
    db_session.commit()
    db_session.refresh(digest)

    svc = DigestService(db_session)
    updated = svc.run_step_0(digest.id, "serious", digest_topic="style")
    assert updated.digest_topic == "style"
    assert updated.digest_type == "serious"
    assert updated.news_window_days == 7
    assert updated.news_window_day_kind == "calendar"


def test_run_step_0_default_topic_is_ai(db_session) -> None:
    digest = Digest(date=date(2026, 7, 30), status="draft", current_step="draft")
    db_session.add(digest)
    db_session.commit()
    db_session.refresh(digest)

    svc = DigestService(db_session)
    updated = svc.run_step_0(digest.id, "serious")
    assert updated.digest_topic == "ai"


def test_run_step_0_topic_change_clears_pool_and_downstream(db_session) -> None:
    digest = Digest(
        date=date(2026, 7, 29),
        status="step_1",
        current_step="step_1",
        digest_topic="ai",
        digest_type="serious",
    )
    db_session.add(digest)
    db_session.commit()
    db_session.refresh(digest)

    cand = NewsCandidate(
        digest_id=digest.id,
        original_number=1,
        title="AI news",
        url="https://example.com/ai",
        source="example.com",
        tier="Tier-1",
        published_at="2026-07-28T00:00:00+03:00",
        category="news",
        description="ai",
        significance_score=1,
        novelty_score=1,
        impact_score=1,
        total_score=3,
        reliability_status="ok",
        is_foreign_agent=False,
        is_aggregator=False,
        is_duplicate=False,
        verification_comment="ok",
        link_status=True,
        headline_editorial_ok=True,
        page_verified=True,
    )
    db_session.add(cand)
    db_session.flush()
    db_session.add(
        SelectedNews(
            digest_id=digest.id,
            candidate_id=cand.id,
            original_number=1,
            output_position=1,
        )
    )
    db_session.add(
        Analytics(
            digest_id=digest.id,
            candidate_id=cand.id,
            essence="e",
            comment="c",
            analysis="a",
            source_url=cand.url,
            source_name=cand.source,
            published_at=cand.published_at,
        )
    )
    db_session.add(
        FinalOutput(
            digest_id=digest.id,
            platform="telegram",
            content="old ai text",
            character_count=11,
        )
    )
    db_session.add(
        Step1DiscoveredNews(
            digest_id=digest.id,
            title="old",
            url="https://example.com/ai",
            source="example.com",
        )
    )
    db_session.commit()

    svc = DigestService(db_session)
    updated = svc.run_step_0(
        digest.id,
        "serious",
        digest_topic="style",
        news_window_days=3,
        news_window_day_kind="working",
    )
    assert updated.digest_topic == "style"
    assert updated.id != digest.id
    assert updated.status == "step_0"
    assert updated.news_window_days == 7
    assert updated.news_window_day_kind == "calendar"
    # Старый ИИ-выпуск не перезаписываем и не чистим.
    assert db_session.query(NewsCandidate).filter(NewsCandidate.digest_id == digest.id).count() == 1
    assert db_session.query(SelectedNews).filter(SelectedNews.digest_id == digest.id).count() == 1
    assert db_session.query(Analytics).filter(Analytics.digest_id == digest.id).count() == 1
    assert db_session.query(FinalOutput).filter(FinalOutput.digest_id == digest.id).count() == 1
    assert db_session.query(Step1DiscoveredNews).filter(Step1DiscoveredNews.digest_id == digest.id).count() == 1


def test_run_step_0_same_style_topic_keeps_custom_window(db_session) -> None:
    digest = Digest(
        date=date(2026, 7, 28),
        status="step_0",
        current_step="step_0",
        digest_topic="style",
        digest_type="serious",
        news_window_days=7,
        news_window_day_kind="calendar",
    )
    db_session.add(digest)
    db_session.commit()
    db_session.refresh(digest)

    svc = DigestService(db_session)
    updated = svc.run_step_0(
        digest.id,
        "serious",
        digest_topic="style",
        news_window_days=14,
        news_window_day_kind="calendar",
    )
    assert updated.id == digest.id
    assert updated.news_window_days == 14
    assert updated.news_window_day_kind == "calendar"


def test_run_step_0_switch_to_existing_topic_digest_same_day(db_session) -> None:
    ai_digest = Digest(
        date=date(2026, 7, 27),
        status="step_1",
        current_step="step_1",
        digest_topic="ai",
        digest_type="serious",
    )
    style_digest = Digest(
        date=date(2026, 7, 27),
        status="step_0",
        current_step="step_0",
        digest_topic="style",
        digest_type="serious",
    )
    db_session.add_all([ai_digest, style_digest])
    db_session.commit()
    db_session.refresh(ai_digest)
    db_session.refresh(style_digest)

    svc = DigestService(db_session)
    updated = svc.run_step_0(
        ai_digest.id,
        "serious",
        digest_topic="style",
        news_window_days=11,
        news_window_day_kind="calendar",
    )
    assert updated.id == style_digest.id
    assert updated.digest_topic == "style"
    assert updated.news_window_days == 7
    assert updated.news_window_day_kind == "calendar"


def test_run_step_0_switch_back_to_ai_resets_window_defaults(db_session) -> None:
    style_digest = Digest(
        date=date(2026, 7, 28),
        status="step_1",
        current_step="step_1",
        digest_topic="style",
        digest_type="serious",
        news_window_days=7,
        news_window_day_kind="calendar",
    )
    ai_digest = Digest(
        date=date(2026, 7, 28),
        status="step_0",
        current_step="step_0",
        digest_topic="ai",
        digest_type="serious",
        news_window_days=7,
        news_window_day_kind="calendar",
    )
    db_session.add_all([style_digest, ai_digest])
    db_session.commit()
    db_session.refresh(style_digest)
    db_session.refresh(ai_digest)

    svc = DigestService(db_session)
    updated = svc.run_step_0(
        style_digest.id,
        "serious",
        digest_topic="ai",
        news_window_days=7,
        news_window_day_kind="calendar",
    )
    assert updated.id == ai_digest.id
    assert updated.digest_topic == "ai"
    assert updated.news_window_days == 3
    assert updated.news_window_day_kind == "working"
