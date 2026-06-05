from datetime import date
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import Digest, FinalOutput
from app.services.cost_tracker import BalanceSnapshot
from app.services.digest_service import DigestService, STATUS_FINAL


@pytest.fixture()
def db_session():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


def test_finalize_digest_release_locks_cost(db_session, monkeypatch: pytest.MonkeyPatch):
    digest = Digest(date=date(2026, 6, 4), status=STATUS_FINAL, current_step=STATUS_FINAL)
    digest.proxyapi_release_open_balance = 200.0
    db_session.add(digest)
    db_session.flush()
    db_session.add(FinalOutput(digest_id=digest.id, platform="telegram", content="x", character_count=1, qc_status="ok"))
    db_session.commit()

    service = DigestService(db_session)
    monkeypatch.setattr(
        service.cost_tracker,
        "get_balance_snapshot",
        lambda: BalanceSnapshot(balance=180.0, budget_limit=None, budget_used=None),
    )

    result = service.finalize_digest_release(digest.id)
    assert result["finalized"] is True
    assert result["release_cost_rub"] == 20.0

    db_session.refresh(digest)
    assert digest.proxyapi_finalized_cost_rub == 20.0
    assert digest.proxyapi_finalized_at is not None
    assert service.compute_digest_total_cost_rub(digest) == 20.0


def test_finalize_requires_final_status(db_session):
    digest = Digest(date=date(2026, 6, 5), status="analytics_ready", current_step="analytics_ready")
    db_session.add(digest)
    db_session.commit()
    service = DigestService(db_session)
    with pytest.raises(HTTPException) as exc:
        service.finalize_digest_release(digest.id)
    assert exc.value.status_code == 400
