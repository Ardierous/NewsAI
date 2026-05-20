from datetime import date, datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import Digest, LlmCostRecord, NewsCandidate, Step1DiscoveryRun
from app.services.digest_pool_stats import build_pool_collection_stats, format_duration


def test_format_duration():
    assert format_duration(45) == "45 с"
    assert format_duration(125) == "2 мин 5 с"


def test_build_pool_collection_stats():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Session = sessionmaker(bind=engine)
    Base.metadata.create_all(bind=engine)
    db = Session()

    digest = Digest(date=date(2026, 5, 18), status="step_1_candidates", current_step="step_1_candidates")
    db.add(digest)
    db.commit()
    db.refresh(digest)

    run = Step1DiscoveryRun(
        digest_id=digest.id,
        run_number=1,
        started_at=datetime.utcnow() - timedelta(seconds=90),
        pool_formed_at=datetime.utcnow(),
        news_count=12,
        duration_sec=90,
        cost_rub=3.5,
    )
    db.add(run)
    db.add(
        NewsCandidate(
            digest_id=digest.id,
            original_number=1,
            title="Press release on AI",
            url="https://businesswire.com/news/ai",
            source="businesswire.com",
            tier="Tier-3",
            published_at="2026-05-01",
            category="AI",
            description="Company announces new model",
            total_score=5,
        )
    )
    db.add(
        LlmCostRecord(
            digest_id=digest.id,
            step="step_1",
            agent_name="NewsResearchAgent",
            model="gpt-test",
            request_label="step_1_collect_pool",
            cost_rub=3.5,
            created_at=datetime.utcnow(),
        )
    )
    db.commit()

    stats = build_pool_collection_stats(db, digest.id)
    assert stats["pool"]["total"] == 1
    assert stats["last_run"]["run_number"] == 1
    assert stats["last_run"]["duration_sec"] == 90
    assert stats["last_run"]["cost_rub"] == 3.5
    assert stats["step1_total_rub"] == 3.5
