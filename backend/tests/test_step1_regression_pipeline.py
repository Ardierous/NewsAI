import json
from datetime import date
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import Asset, Digest, NewsCandidate
from app.services.digest_service import DigestService, STATUS_STEP0, STATUS_STEP1


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
    monkeypatch.setattr(service.cost_tracker, "measure", lambda fn, source: (fn(), SimpleNamespace(cost_rub=0.0)))
    monkeypatch.setattr(service.workflow, "run_candidates_research", lambda digest_type, now_msk, manual_urls: seed_rows)
    monkeypatch.setattr(service.workflow, "run_candidates_verify", lambda research_rows: [dict(x) for x in research_rows])
    monkeypatch.setattr(service, "_step1_fetch_supplementary_dicts", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        service, "_prefilter_llm_candidates_fetchable", lambda _digest_id, rows: (rows, [])
    )
    monkeypatch.setattr(service, "_collect_search_verified_candidates", lambda *args, **kwargs: [])
    return service, digest


def test_step1_persists_reject_reasons_on_502(monkeypatch: pytest.MonkeyPatch):
    service, digest = _make_service(monkeypatch)

    score_rows = [{"title": f"Candidate {i}", "url": f"https://example.com/news/{i}"} for i in range(6)]
    monkeypatch.setattr(service.workflow, "run_candidates_score", lambda verify_rows, now_msk: score_rows)

    def fake_verify(_digest_id: int, item: dict) -> None:
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

    def fake_verify(_digest_id: int, item: dict) -> None:
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


def test_step1_rejects_urls_mutated_between_verify_and_score(monkeypatch: pytest.MonkeyPatch):
    service, digest = _make_service(monkeypatch)

    verify_rows = [{"original_number": i + 1, "title": f"Candidate {i}", "url": f"https://example.com/news/{i}"} for i in range(6)]
    score_rows = [dict(x) for x in verify_rows]
    score_rows[-1]["url"] = "https://broken.example.net/new-path"

    monkeypatch.setattr(service.workflow, "run_candidates_research", lambda digest_type, now_msk, manual_urls: verify_rows)
    monkeypatch.setattr(service.workflow, "run_candidates_verify", lambda research_rows: verify_rows)
    monkeypatch.setattr(service.workflow, "run_candidates_score", lambda verify_rows, now_msk: score_rows)

    def fake_verify(_digest_id: int, item: dict) -> None:
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
    assert len(rows) == 5
    saved = (
        service.db.query(Asset)
        .filter(Asset.digest_id == digest.id, Asset.type == "step1_rejected_reasons")
        .order_by(Asset.id.desc())
        .first()
    )
    assert saved is not None
    stats = json.loads(saved.prompt or "{}")
    assert stats.get("url_mutated_between_agents") == 1
