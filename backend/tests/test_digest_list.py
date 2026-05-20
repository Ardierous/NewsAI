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
    assert row["status_label_ru"] == "Аналитика готова"
    assert row["total_cost_rub"] == 3.75
    assert len(row["top5"]) == 2
    assert row["top5"][0]["title"] == "Новость один"
