"""Синхронизация окна дат перед шагом 1 (между пересборками пула)."""
from datetime import date

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import Digest
from app.services.digest_service import DigestService, STATUS_STEP0


def _service_with_digest(days: int = 3, kind: str = "working") -> tuple[DigestService, Digest]:
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Session = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)
    db = Session()
    digest = Digest(
        date=date(2026, 5, 15),
        status=STATUS_STEP0,
        current_step=STATUS_STEP0,
        digest_type="serious",
        news_window_days=days,
        news_window_day_kind=kind,
    )
    db.add(digest)
    db.commit()
    db.refresh(digest)
    return DigestService(db), digest


def test_update_news_window_persists_without_step0_reset():
    service, digest = _service_with_digest(3, "calendar")
    updated = service.update_news_window(digest.id, news_window_days=14, news_window_day_kind="working")
    assert updated.news_window_days == 14
    assert updated.news_window_day_kind == "working"
    assert updated.status == STATUS_STEP0
    assert updated.digest_type == "serious"


def test_run_step_1_applies_window_params_before_pipeline(monkeypatch):
    service, digest = _service_with_digest(3, "calendar")
    calls: list[dict] = []
    orig_update = DigestService.update_news_window.__get__(service, DigestService)

    def _capture_update(digest_id: int, *, news_window_days: int, news_window_day_kind: str):
        calls.append({"days": news_window_days, "kind": news_window_day_kind})
        return orig_update(digest_id, news_window_days=news_window_days, news_window_day_kind=news_window_day_kind)

    monkeypatch.setattr(service, "update_news_window", _capture_update)
    monkeypatch.setattr(service.settings, "enable_web_fetch", False)

    with pytest.raises(HTTPException):
        service.run_step_1(digest.id, [], news_window_days=7, news_window_day_kind="working")

    assert calls == [{"days": 7, "kind": "working"}]
    refreshed = service.get_digest(digest.id)
    assert refreshed.news_window_days == 7
    assert refreshed.news_window_day_kind == "working"
