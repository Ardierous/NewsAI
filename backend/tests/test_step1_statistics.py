"""API и снимок статистики шага 1."""

from datetime import date, datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import Asset, Digest, NewsCandidate, Step1DiscoveredNews
from app.services.step1_statistics import (
    build_step1_statistics,
    load_step1_statistics_snapshot,
    persist_step1_statistics_snapshot,
)


def _make_db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    session_cls = sessionmaker(bind=engine)
    Base.metadata.create_all(bind=engine)
    return session_cls()


def test_build_step1_statistics_links_and_summary():
    db = _make_db()
    digest = Digest(
        date=date(2026, 6, 6),
        status="step_1_candidates",
        current_step="step_1_candidates",
        digest_type="serious",
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    db.add(digest)
    db.commit()
    db.refresh(digest)

    db.add(
        Step1DiscoveredNews(
            digest_id=digest.id,
            source_stage="search",
            title="OK",
            url="https://example.com/ok",
            source="example.com",
            published_at="2026-06-05",
            headline_editorial_ok=True,
            link_status=True,
            page_verified=True,
            reject_codes="",
        )
    )
    db.add(
        Step1DiscoveredNews(
            digest_id=digest.id,
            source_stage="crew",
            title="Bad",
            url="https://example.com/bad",
            source="example.com",
            published_at="2026-06-05",
            headline_editorial_ok=False,
            link_status=False,
            page_verified=False,
            reject_codes="http_unreachable,url_mutated_between_agents",
            verification_comment="REJECT_REASON:http_unreachable",
        )
    )
    db.add(
        NewsCandidate(
            digest_id=digest.id,
            original_number=1,
            title="OK",
            url="https://example.com/ok",
            source="example.com",
            tier="Tier-3",
            published_at="2026-06-05",
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
    db.add(
        Asset(
            digest_id=digest.id,
            type="step1_rejected_reasons",
            path="",
            prompt='{"http_unreachable": 1, "url_mutated_between_agents": 1}',
        )
    )
    db.commit()

    stats = build_step1_statistics(db, digest.id)
    assert stats.summary.total_links == 2
    assert stats.insights is not None
    assert stats.insights.headline
    assert len(stats.insights.dominant_rejects) >= 1
    assert stats.summary.in_pool == 1
    assert stats.summary.rejected == 1
    assert stats.rejected_reasons_summary.get("http_unreachable") == 1

    bad = next(x for x in stats.links if "bad" in x.url)
    assert bad.outcome == "rejected"
    assert "http_unreachable" in bad.reject_codes
    assert bad.reject_labels


def test_persist_and_load_statistics_snapshot():
    db = _make_db()
    digest = Digest(
        date=date(2026, 6, 6),
        status="step_1_candidates",
        current_step="step_1_candidates",
        digest_type="serious",
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    db.add(digest)
    db.commit()
    db.refresh(digest)

    db.add(
        Step1DiscoveredNews(
            digest_id=digest.id,
            source_stage="search",
            title="One",
            url="https://example.com/one",
            source="example.com",
            published_at="2026-06-05",
            headline_editorial_ok=False,
            link_status=False,
            page_verified=False,
            reject_codes="http_unreachable",
        )
    )
    db.commit()

    persist_step1_statistics_snapshot(db, digest.id, discovery_run_id=42)
    loaded = load_step1_statistics_snapshot(db, digest.id)
    assert loaded is not None
    assert loaded.discovery_run_id == 42
    assert loaded.digest_type == "serious"
    assert loaded.summary.total_links == 1
    assert loaded.links[0].reject_codes == ["http_unreachable"]


def test_curious_statistics_same_shape_as_serious():
    db = _make_db()
    digest = Digest(
        date=date(2026, 6, 7),
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
            source_stage="search",
            title="Funny AI",
            url="https://example.com/funny",
            source="example.com",
            published_at="2026-06-06",
            headline_editorial_ok=False,
            link_status=False,
            page_verified=False,
            reject_codes="off_topic_not_curious",
        )
    )
    db.add(
        Asset(
            digest_id=digest.id,
            type="step1_curious_tone_audit",
            path="",
            prompt='{"accepted": 1, "rejected_dry": 2}',
        )
    )
    db.commit()

    stats = build_step1_statistics(db, digest.id)
    assert stats.digest_type == "curious"
    assert stats.registry_buckets is not None
    assert stats.curious_tone_audit.get("rejected_dry") == 2
    assert stats.links[0].reject_codes == ["off_topic_not_curious"]
