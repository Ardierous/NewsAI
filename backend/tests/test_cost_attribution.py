from datetime import date, datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import Digest, LlmCostRecord, Step1DiscoveryRun
from app.services.cost_attribution import (
    compute_digest_total_cost_rub,
    digest_release_spent_rub,
    step1_run_cost_rub,
)
from app.services.digest_pool_stats import build_pool_collection_stats


def test_compute_digest_total_prefers_committed_records_after_session_reset():
    total = compute_digest_total_cost_rub(records_sum_rub=12.5, session_spent_rub=3.0)
    assert total == 12.5


def test_compute_digest_total_includes_live_session_tail():
    total = compute_digest_total_cost_rub(records_sum_rub=2.0, session_spent_rub=2.75)
    assert total == 2.75


def test_compute_digest_total_prefers_release_and_finalized():
    total = compute_digest_total_cost_rub(
        records_sum_rub=1.5,
        session_spent_rub=3.0,
        release_spent_rub=12.0,
    )
    assert total == 12.0
    locked = compute_digest_total_cost_rub(
        records_sum_rub=1.5,
        session_spent_rub=3.0,
        release_spent_rub=12.0,
        finalized_cost_rub=15.5,
    )
    assert locked == 15.5


def test_digest_release_spent_uses_opening_balance():
    digest = Digest(
        date=date(2026, 6, 4),
        status="final_ready",
        current_step="final_ready",
        proxyapi_release_open_balance=100.0,
        proxyapi_finalized_cost_rub=None,
    )
    assert digest_release_spent_rub(digest, live_balance=84.5) == 15.5
    digest.proxyapi_finalized_cost_rub = 20.0
    assert digest_release_spent_rub(digest, live_balance=50.0) == 20.0


def test_step1_run_cost_ignores_pool_formed_at_cutoff():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Session = sessionmaker(bind=engine)
    Base.metadata.create_all(bind=engine)
    db = Session()

    digest = Digest(date=date(2026, 5, 27), status="step_1_candidates", current_step="step_1_candidates")
    db.add(digest)
    db.commit()
    db.refresh(digest)

    started = datetime.utcnow() - timedelta(minutes=10)
    pool_formed = started + timedelta(minutes=8)
    cost_created = pool_formed + timedelta(seconds=30)

    run = Step1DiscoveryRun(
        digest_id=digest.id,
        run_number=1,
        started_at=started,
        pool_formed_at=pool_formed,
        news_count=5,
        duration_sec=480,
    )
    db.add(run)
    db.add(
        LlmCostRecord(
            digest_id=digest.id,
            step="step_1",
            agent_name="NewsResearchAgent",
            model="gpt-4.1-mini",
            request_label="step_1_collect_pool",
            cost_rub=4.2,
            created_at=cost_created,
        )
    )
    db.commit()

    assert step1_run_cost_rub(db, digest.id, run) == 4.2


def test_step1_run_cost_splits_multiple_runs():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Session = sessionmaker(bind=engine)
    Base.metadata.create_all(bind=engine)
    db = Session()

    digest = Digest(date=date(2026, 5, 27), status="step_1_candidates", current_step="step_1_candidates")
    db.add(digest)
    db.commit()
    db.refresh(digest)

    run1_started = datetime.utcnow() - timedelta(hours=2)
    run2_started = datetime.utcnow() - timedelta(hours=1)

    run1 = Step1DiscoveryRun(
        digest_id=digest.id,
        run_number=1,
        started_at=run1_started,
        pool_formed_at=run1_started + timedelta(minutes=5),
        news_count=10,
        cost_rub=None,
    )
    run2 = Step1DiscoveryRun(
        digest_id=digest.id,
        run_number=2,
        started_at=run2_started,
        pool_formed_at=run2_started + timedelta(minutes=6),
        news_count=12,
        cost_rub=None,
    )
    db.add_all([run1, run2])
    db.add(
        LlmCostRecord(
            digest_id=digest.id,
            step="step_1",
            agent_name="NewsResearchAgent",
            model="gpt-4.1-mini",
            request_label="step_1_collect_pool",
            cost_rub=1.5,
            created_at=run1_started + timedelta(minutes=6),
        )
    )
    db.add(
        LlmCostRecord(
            digest_id=digest.id,
            step="step_1",
            agent_name="NewsResearchAgent",
            model="gpt-4.1-mini",
            request_label="step_1_collect_pool",
            cost_rub=2.25,
            created_at=run2_started + timedelta(minutes=7),
        )
    )
    db.commit()

    assert step1_run_cost_rub(db, digest.id, run1) == 1.5
    assert step1_run_cost_rub(db, digest.id, run2) == 2.25

    stats = build_pool_collection_stats(db, digest.id)
    assert stats["last_run"]["cost_rub"] == 2.25
    assert stats["step1_total_rub"] == 3.75
