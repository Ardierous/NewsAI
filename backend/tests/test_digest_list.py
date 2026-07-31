from datetime import date, datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import Analytics, Asset, Digest, LlmCostRecord, NewsCandidate, SelectedNews
from app.services.digest_list import build_digest_list_payload
from app.services.digest_service import STATUS_ANALYTICS, STATUS_SELECTED


def test_build_digest_list_payload_summary_top5_and_cost():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Session = sessionmaker(bind=engine)
    Base.metadata.create_all(bind=engine)
    db = Session()

    digest = Digest(
        date=date(2026, 5, 18),
        status=STATUS_ANALYTICS,
        current_step=STATUS_ANALYTICS,
        digest_type="serious",
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    db.add(digest)
    db.commit()
    db.refresh(digest)

    db.add(
        Asset(
            digest_id=digest.id,
            type="overall_analysis",
            path="",
            prompt="Главная линия выпуска: регулирование ИИ и крупные внедрения в госсекторе.",
        )
    )
    c1 = NewsCandidate(
        digest_id=digest.id,
        original_number=1,
        title="Новость один",
        url="https://a.example/1",
        source="a.example",
        tier="Tier-2",
        published_at="2026-05-18",
        category="AI",
        description="",
        total_score=5,
    )
    c2 = NewsCandidate(
        digest_id=digest.id,
        original_number=2,
        title="Новость два",
        url="https://b.example/2",
        source="b.example",
        tier="Tier-2",
        published_at="2026-05-18",
        category="AI",
        description="",
        total_score=4,
    )
    db.add_all([c1, c2])
    db.commit()
    db.refresh(c1)
    db.refresh(c2)

    db.add(
        SelectedNews(
            digest_id=digest.id,
            candidate_id=c1.id,
            original_number=1,
            output_position=1,
            ordering_reason="",
        )
    )
    db.add(
        SelectedNews(
            digest_id=digest.id,
            candidate_id=c2.id,
            original_number=2,
            output_position=2,
            ordering_reason="",
        )
    )
    db.add(
        LlmCostRecord(
            digest_id=digest.id,
            step="step_1",
            agent_name="NewsResearchAgent",
            model="gpt-4.1-mini",
            request_label="step_1_collect_pool",
            cost_rub=1.5,
        )
    )
    db.add(
        LlmCostRecord(
            digest_id=digest.id,
            step="step_3",
            agent_name="AnalyticsAgent",
            model="gpt-4.1-mini",
            request_label="step_3_analytics",
            cost_rub=2.25,
        )
    )
    db.commit()

    rows = build_digest_list_payload(db, [digest])
    assert len(rows) == 1
    row = rows[0]
    assert "регулирование ИИ" in row["summary_title"]
    assert row["digest_topic"] == "ai"
    assert row["status_label_ru"] == "Аналитика готова"
    assert row["total_cost_rub"] == 3.75
    assert len(row["top5"]) == 2
    assert row["top5"][0]["title"] == "Новость один"


def test_build_digest_list_payload_uses_proxyapi_snapshot_when_records_empty():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Session = sessionmaker(bind=engine)
    Base.metadata.create_all(bind=engine)
    db = Session()

    digest = Digest(
        date=date(2026, 5, 29),
        status=STATUS_SELECTED,
        current_step=STATUS_SELECTED,
        digest_type="serious",
        proxyapi_budget_used_session_start=100.0,
        proxyapi_budget_used_after=106.42,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    db.add(digest)
    db.commit()
    db.refresh(digest)

    rows = build_digest_list_payload(db, [digest])
    assert len(rows) == 1
    assert rows[0]["total_cost_rub"] == 6.42


def test_build_digest_list_payload_prefers_session_tail_over_records():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Session = sessionmaker(bind=engine)
    Base.metadata.create_all(bind=engine)
    db = Session()

    digest = Digest(
        date=date(2026, 5, 29),
        status=STATUS_SELECTED,
        current_step=STATUS_SELECTED,
        digest_type="serious",
        proxyapi_budget_used_session_start=50.0,
        proxyapi_budget_used_after=53.5,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    db.add(digest)
    db.commit()
    db.refresh(digest)

    db.add(
        LlmCostRecord(
            digest_id=digest.id,
            step="step_1",
            agent_name="NewsResearchAgent",
            model="gpt-4.1-mini",
            request_label="step_1_collect_pool",
            cost_rub=1.0,
        )
    )
    db.commit()

    rows = build_digest_list_payload(db, [digest])
    assert rows[0]["total_cost_rub"] == 3.5


def test_digest_proxyapi_spent_rub_live_session_tail():
    from app.services.cost_attribution import digest_proxyapi_spent_rub

    digest = Digest(
        date=date(2026, 5, 29),
        status=STATUS_SELECTED,
        current_step=STATUS_SELECTED,
        proxyapi_balance_session_start=416.7,
    )
    assert digest_proxyapi_spent_rub(digest, live_balance=400.0) == 16.7


def test_digest_proxyapi_spent_rub_from_prev_anchor_and_after_only():
    from app.services.cost_attribution import digest_proxyapi_spent_rub

    digest = Digest(
        date=date(2026, 5, 28),
        status=STATUS_SELECTED,
        current_step=STATUS_SELECTED,
        proxyapi_balance_after=438.0465,
    )
    assert digest_proxyapi_spent_rub(digest, prev_anchor_balance=579.4299) == 141.3834


def test_build_digest_list_payload_chains_prev_digest_balance():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Session = sessionmaker(bind=engine)
    Base.metadata.create_all(bind=engine)
    db = Session()

    prev = Digest(
        date=date(2026, 5, 27),
        status=STATUS_SELECTED,
        current_step=STATUS_SELECTED,
        proxyapi_balance_after=579.4299,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    current = Digest(
        date=date(2026, 5, 28),
        status=STATUS_SELECTED,
        current_step=STATUS_SELECTED,
        proxyapi_balance_after=438.0465,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    db.add_all([prev, current])
    db.commit()
    db.refresh(prev)
    db.refresh(current)

    rows = build_digest_list_payload(db, [current, prev])
    by_id = {row["id"]: row["total_cost_rub"] for row in rows}
    assert by_id[current.id] == 141.3834


def test_build_digest_list_payload_does_not_chain_balance_between_topics():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Session = sessionmaker(bind=engine)
    Base.metadata.create_all(bind=engine)
    db = Session()

    ai_prev = Digest(
        date=date(2026, 5, 27),
        status=STATUS_SELECTED,
        current_step=STATUS_SELECTED,
        digest_topic="ai",
        proxyapi_balance_after=579.4299,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    style_current = Digest(
        date=date(2026, 5, 28),
        status=STATUS_SELECTED,
        current_step=STATUS_SELECTED,
        digest_topic="style",
        proxyapi_balance_after=438.0465,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    db.add_all([ai_prev, style_current])
    db.commit()
    db.refresh(ai_prev)
    db.refresh(style_current)

    rows = build_digest_list_payload(db, [ai_prev, style_current])
    by_id = {row["id"]: row["total_cost_rub"] for row in rows}
    assert by_id[style_current.id] == 0.0
