"""Журнал шага 1: «в пуле» = финальный список NewsCandidate, не все прошедшие HTTP."""

from datetime import date, datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import Digest, NewsCandidate, Step1DiscoveredNews
from app.services.digest_service import DigestService, _align_discovered_journal_with_final_pool


def _make_db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    session_cls = sessionmaker(bind=engine)
    Base.metadata.create_all(bind=engine)
    return session_cls()


def test_journal_in_pool_counts_final_candidates_only():
    db = _make_db()
    digest = Digest(
        date=date(2026, 5, 20),
        status="step_1_candidates",
        current_step="step_1_candidates",
        digest_type="curious",
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    db.add(digest)
    db.commit()
    db.refresh(digest)

    db.add(
        Step1DiscoveredNews(
            digest_id=digest.id,
            source_stage="step1",
            title="In final",
            url="https://example.com/in-final",
            source="example.com",
            published_at="2026-05-20",
            headline_editorial_ok=True,
            link_status=True,
            page_verified=True,
            reject_codes="",
        )
    )
    db.add(
        Step1DiscoveredNews(
            digest_id=digest.id,
            source_stage="step1",
            title="Verified only",
            url="https://example.com/verified-only",
            source="example.com",
            published_at="2026-05-20",
            headline_editorial_ok=True,
            link_status=True,
            page_verified=True,
            reject_codes="",
        )
    )
    db.add(
        NewsCandidate(
            digest_id=digest.id,
            original_number=1,
            title="In final",
            url="https://example.com/in-final",
            source="example.com",
            tier="Tier-3",
            published_at="2026-05-20",
            category="technology",
            description="",
            significance_score=1,
            novelty_score=1,
            impact_score=1,
            total_score=3,
            reliability_status="ok",
            link_status=True,
            headline_editorial_ok=True,
            page_verified=True,
        )
    )
    db.commit()

    service = DigestService(db)
    totals = service._journal_totals_from_discovered_news(digest.id)
    assert totals["total"] == 2
    assert totals["in_pool"] == 1
    assert totals["rejected"] == 1


def test_align_discovered_marks_rebalance_exclusion():
    rows = {
        "a": {
            "url": "https://example.com/a",
            "headline_editorial_ok": True,
            "link_status": True,
            "verification_comment": "",
        }
    }
    _align_discovered_journal_with_final_pool(rows, final_candidate_urls=[])
    assert "excluded_from_final_pool" in rows["a"]["verification_comment"]
