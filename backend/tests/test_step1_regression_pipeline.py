import json
from datetime import date, datetime
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import Analytics, Asset, Digest, NewsCandidate, SelectedNews, Step1DiscoveredNews, Step1ManualRatingLog
from app.services import digest_service as ds
from app.services.digest_service import (
    DigestService,
    STATUS_ANALYTICS,
    STATUS_SELECTED,
    STATUS_STEP0,
    STATUS_STEP1,
)
from app.services.step1_manual_ratings_export import sync_step1_manual_ratings_export


def _fake_expand_listing(url: str, max_children: int = 10) -> list[tuple[str, dict]]:
    u = url.strip()
    bundle = {
        "ok": True,
        "is_listing_page": False,
        "final_url": u,
        "display_url": u,
        "article_markers": True,
        "soft_article_signals": True,
        "headline": "Нейросеть и искусственный интеллект: тест",
        "topic_corpus": "искусственный интеллект нейросети",
        "headline_strict": True,
    }
    return [(u, bundle)]


def _make_service(monkeypatch: pytest.MonkeyPatch) -> tuple[DigestService, Digest]:
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    testing_session = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)
    db = testing_session()

    digest = Digest(date=date(2026, 5, 14), status=STATUS_STEP0, current_step=STATUS_STEP0, digest_type="serious")
    db.add(digest)
    db.commit()
    db.refresh(digest)

    service = DigestService(db)
    service.settings.enable_web_fetch = True
    service.settings.step1_max_cost_rub = 9999.0

    seed_rows = [{"original_number": i + 1, "title": f"Candidate {i}", "url": f"https://example.com/news/{i}"} for i in range(6)]
    monkeypatch.setattr(
        service.cost_tracker,
        "measure",
        lambda fn, source, **kwargs: (fn(), SimpleNamespace(cost_rub=0.0)),
    )
    monkeypatch.setattr(service.workflow, "run_candidates_research", lambda digest_type, now_msk, manual_urls: seed_rows)
    monkeypatch.setattr(service.workflow, "run_candidates_verify", lambda research_rows: [dict(x) for x in research_rows])
    monkeypatch.setattr(service, "_step1_fetch_supplementary_dicts", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        service, "_prefilter_llm_candidates_fetchable", lambda _digest_id, rows: (rows, [])
    )
    monkeypatch.setattr(service, "_collect_search_verified_candidates", lambda *args, **kwargs: [])
    monkeypatch.setattr(ds, "_expand_listing_url_candidates", _fake_expand_listing)
    return service, digest


def test_step1_raises_402_when_proxyapi_budget_exceeded(monkeypatch: pytest.MonkeyPatch):
    service, digest = _make_service(monkeypatch)
    service.proxy.last_error_kind = "budget_exceeded"

    with pytest.raises(HTTPException) as ex:
        service.run_step_1(digest.id, [])

    assert ex.value.status_code == 402
    assert "402" in str(ex.value.detail)
    assert service.digest_proxyapi_budget_exceeded(digest.id)


def test_step1_persists_reject_reasons_on_502(monkeypatch: pytest.MonkeyPatch):
    service, digest = _make_service(monkeypatch)

    score_rows = [{"title": f"Candidate {i}", "url": f"https://example.com/news/{i}"} for i in range(6)]
    monkeypatch.setattr(service.workflow, "run_candidates_score", lambda verify_rows, now_msk: score_rows)

    def fake_verify(_digest_id: int, item: dict, **_kwargs) -> None:
        item["headline_editorial_ok"] = False
        item["link_status"] = False
        item["verification_comment"] = "REJECT_REASON:off_topic_not_ai"

    monkeypatch.setattr(service, "_verify_llm_candidate_dict", fake_verify)

    with pytest.raises(HTTPException) as ex:
        service.run_step_1(digest.id, [])

    assert ex.value.status_code == 502
    assert "Основные причины отбраковки: off_topic_not_ai=6." in str(ex.value.detail)

    saved = (
        service.db.query(Asset)
        .filter(Asset.digest_id == digest.id, Asset.type == "step1_rejected_reasons")
        .order_by(Asset.id.desc())
        .first()
    )
    assert saved is not None
    stats = json.loads(saved.prompt or "{}")
    assert stats.get("off_topic_not_ai") == 6

    preview_count = service.db.query(NewsCandidate).filter(NewsCandidate.digest_id == digest.id).count()
    assert preview_count == 6


def test_step1_success_sets_status_and_candidates(monkeypatch: pytest.MonkeyPatch):
    service, digest = _make_service(monkeypatch)

    score_rows = [{"title": f"Candidate {i}", "url": f"https://example.com/news/{i}"} for i in range(6)]
    monkeypatch.setattr(service.workflow, "run_candidates_score", lambda verify_rows, now_msk: score_rows)

    def fake_verify(_digest_id: int, item: dict, **_kwargs) -> None:
        item["headline_editorial_ok"] = True
        item["link_status"] = True
        item["is_aggregator"] = False
        item["verification_comment"] = ""
        item["title"] = f"ИИ: {item.get('title', 'Новость')}"
        item["source"] = "Example"
        item["published_at"] = "2026-05-14T12:00:00"
        item["category"] = "technology"
        item["description"] = "Описание"
        item["significance_score"] = 2
        item["novelty_score"] = 2
        item["impact_score"] = 2
        item["total_score"] = 6
        item["reliability_status"] = "✅ подтверждено"

    monkeypatch.setattr(service, "_verify_llm_candidate_dict", fake_verify)

    rows = service.run_step_1(digest.id, [])
    service.db.refresh(digest)

    assert len(rows) >= 5
    assert digest.status == STATUS_STEP1
    assert digest.current_step == STATUS_STEP1
    assert service.db.query(NewsCandidate).filter(NewsCandidate.digest_id == digest.id).count() >= 5
    assert service.db.query(Step1DiscoveredNews).filter(Step1DiscoveredNews.digest_id == digest.id).count() >= 5


def test_step1_discovered_feedback_saved(monkeypatch: pytest.MonkeyPatch):
    service, digest = _make_service(monkeypatch)

    score_rows = [{"title": f"Candidate {i}", "url": f"https://example.com/news/{i}"} for i in range(6)]
    monkeypatch.setattr(service.workflow, "run_candidates_score", lambda verify_rows, now_msk: score_rows)

    def fake_verify(_digest_id: int, item: dict, **_kwargs) -> None:
        item["headline_editorial_ok"] = True
        item["link_status"] = True
        item["is_aggregator"] = False
        item["verification_comment"] = ""
        item["title"] = f"ИИ: {item.get('title', 'Новость')}"
        item["source"] = "Example"
        item["published_at"] = "2026-05-14T12:00:00"
        item["category"] = "technology"
        item["description"] = "Описание"
        item["significance_score"] = 2
        item["novelty_score"] = 2
        item["impact_score"] = 2
        item["total_score"] = 6
        item["reliability_status"] = "✅ подтверждено"

    monkeypatch.setattr(service, "_verify_llm_candidate_dict", fake_verify)
    service.run_step_1(digest.id, [])
    row = (
        service.db.query(Step1DiscoveredNews)
        .filter(Step1DiscoveredNews.digest_id == digest.id)
        .order_by(Step1DiscoveredNews.id.asc())
        .first()
    )
    assert row is not None
    updated = service.save_step1_discovered_feedback(
        digest_id=digest.id,
        news_id=row.id,
        score=1,
        reason="off_topic_not_ai",
        reason_other="",
    )
    assert updated.manual_score == 1
    assert updated.manual_reason == "off_topic_not_ai"
    assert service.db.query(Step1ManualRatingLog).filter(Step1ManualRatingLog.digest_id == digest.id).count() == 1
    assert service.settings.step1_manual_ratings_path.exists()
    payload = json.loads(service.settings.step1_manual_ratings_path.read_text(encoding="utf-8"))
    assert payload["pool_dates"]
    assert payload["pool_dates"][0]["runs"][0]["ratings"][0]["rated_at"]


def test_step1_manual_ratings_backfill_all_digests(tmp_path):
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    testing_session = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)
    db = testing_session()
    export_path = tmp_path / "ratings.json"

    d1 = Digest(date=date(2026, 5, 14), status=STATUS_STEP1, current_step=STATUS_STEP1, digest_type="serious")
    d2 = Digest(date=date(2026, 5, 15), status=STATUS_STEP1, current_step=STATUS_STEP1, digest_type="serious")
    db.add_all([d1, d2])
    db.commit()
    db.refresh(d1)
    db.refresh(d2)

    db.add_all(
        [
            Step1DiscoveredNews(
                digest_id=d1.id,
                title="Старая оценка 1",
                url="https://example.com/a",
                source="Example",
                manual_score=2,
                manual_reason="off_topic_not_ai",
                rated_at=datetime(2026, 5, 14, 12, 0, 0),
            ),
            Step1DiscoveredNews(
                digest_id=d2.id,
                title="Старая оценка 2",
                url="https://example.com/b",
                source="Example",
                manual_score=1,
                manual_reason="http_unreachable",
                rated_at=datetime(2026, 5, 15, 12, 0, 0),
            ),
        ]
    )
    db.commit()
    assert db.query(Step1ManualRatingLog).count() == 0

    sync_step1_manual_ratings_export(db, export_path)
    assert db.query(Step1ManualRatingLog).count() == 2
    payload = json.loads(export_path.read_text(encoding="utf-8"))
    pool_dates = {x["pool_date"] for x in payload["pool_dates"]}
    assert pool_dates == {"2026-05-14", "2026-05-15"}
    total_ratings = sum(len(run["ratings"]) for block in payload["pool_dates"] for run in block["runs"])
    assert total_ratings == 2


def test_step1_keeps_verify_url_when_score_mutates_url(monkeypatch: pytest.MonkeyPatch):
    service, digest = _make_service(monkeypatch)

    verify_rows = [{"original_number": i + 1, "title": f"Candidate {i}", "url": f"https://example.com/news/{i}"} for i in range(6)]
    score_rows = [dict(x) for x in verify_rows]
    score_rows[-1]["url"] = "https://broken.example.net/new-path"
    score_rows[-1]["total_score"] = 99

    monkeypatch.setattr(service.workflow, "run_candidates_research", lambda digest_type, now_msk, manual_urls: verify_rows)
    monkeypatch.setattr(service.workflow, "run_candidates_verify", lambda research_rows: verify_rows)
    monkeypatch.setattr(service.workflow, "run_candidates_score", lambda verify_rows, now_msk: score_rows)

    def fake_verify(_digest_id: int, item: dict, **_kwargs) -> None:
        item["headline_editorial_ok"] = True
        item["link_status"] = True
        item["is_aggregator"] = False
        item["verification_comment"] = str(item.get("verification_comment") or "")
        item["title"] = f"ИИ: {item.get('title', 'Новость')}"
        item["source"] = "Example"
        item["published_at"] = "2026-05-14T12:00:00"
        item["category"] = "technology"
        item["description"] = "Описание"
        item["significance_score"] = 2
        item["novelty_score"] = 2
        item["impact_score"] = 2
        item["total_score"] = 6
        item["reliability_status"] = "✅ подтверждено"

    monkeypatch.setattr(service, "_verify_llm_candidate_dict", fake_verify)

    rows = service.run_step_1(digest.id, [])
    assert len(rows) == 6
    assert rows[-1].url.endswith("/news/5")
    assert "broken.example.net" not in rows[-1].url


def test_step1_rebuild_from_selected_clears_downstream(monkeypatch: pytest.MonkeyPatch):
    service, digest = _make_service(monkeypatch)

    score_rows = [{"title": f"Candidate {i}", "url": f"https://example.com/news/{i}"} for i in range(6)]
    monkeypatch.setattr(service.workflow, "run_candidates_score", lambda verify_rows, now_msk: score_rows)

    def fake_verify(_digest_id: int, item: dict, **_kwargs) -> None:
        item["headline_editorial_ok"] = True
        item["link_status"] = True
        item["is_aggregator"] = False
        item["verification_comment"] = ""
        item["title"] = f"ИИ: {item.get('title', 'Новость')}"
        item["source"] = "Example"
        item["published_at"] = "2026-05-10T09:00:00+03:00"
        item["category"] = "technology"
        item["description"] = "Описание"
        item["significance_score"] = 2
        item["novelty_score"] = 2
        item["impact_score"] = 2
        item["total_score"] = 6
        item["reliability_status"] = "✅ подтверждено"

    monkeypatch.setattr(service, "_verify_llm_candidate_dict", fake_verify)

    first = service.run_step_1(digest.id, [])
    assert len(first) >= 5
    digest.status = STATUS_SELECTED
    digest.current_step = STATUS_SELECTED
    service.db.add(
        SelectedNews(
            digest_id=digest.id,
            candidate_id=first[0].id,
            original_number=1,
            output_position=1,
            ordering_reason="test",
        )
    )
    service.db.add(
        Analytics(
            digest_id=digest.id,
            candidate_id=first[0].id,
            essence="e",
            comment="c",
            analysis="a",
            source_url=first[0].url,
            source_name=first[0].source,
            published_at=first[0].published_at,
        )
    )
    service.db.commit()

    with pytest.raises(HTTPException) as ex:
        service.run_step_1(digest.id, [], rebuild=False)
    assert ex.value.status_code == 400
    assert "rebuild=true" in str(ex.value.detail)

    rows = service.run_step_1(digest.id, [], rebuild=True)
    service.db.refresh(digest)
    assert digest.status == STATUS_STEP1
    assert service.db.query(SelectedNews).filter(SelectedNews.digest_id == digest.id).count() == 0
    assert service.db.query(Analytics).filter(Analytics.digest_id == digest.id).count() == 0
    assert service.db.query(NewsCandidate).filter(NewsCandidate.digest_id == digest.id).count() >= 5
    assert len(rows) >= 5


def test_step1_rebuild_from_analytics_ready(monkeypatch: pytest.MonkeyPatch):
    service, digest = _make_service(monkeypatch)
    digest.status = STATUS_ANALYTICS
    digest.current_step = STATUS_ANALYTICS
    service.db.commit()

    score_rows = [{"title": f"Candidate {i}", "url": f"https://example.com/news/{i}"} for i in range(6)]
    monkeypatch.setattr(service.workflow, "run_candidates_score", lambda verify_rows, now_msk: score_rows)

    def fake_verify(_digest_id: int, item: dict, **_kwargs) -> None:
        item["headline_editorial_ok"] = True
        item["link_status"] = True
        item["is_aggregator"] = False
        item["verification_comment"] = ""
        item["title"] = f"ИИ: {item.get('title', 'Новость')}"
        item["source"] = "Example"
        item["published_at"] = "2026-05-11T12:00:00+03:00"
        item["category"] = "technology"
        item["description"] = "Описание"
        item["significance_score"] = 2
        item["novelty_score"] = 2
        item["impact_score"] = 2
        item["total_score"] = 6
        item["reliability_status"] = "✅ подтверждено"

    monkeypatch.setattr(service, "_verify_llm_candidate_dict", fake_verify)

    rows = service.run_step_1(digest.id, [], rebuild=True)
    service.db.refresh(digest)
    assert digest.status == STATUS_STEP1
    assert len(rows) >= 5
