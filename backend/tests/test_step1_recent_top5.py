from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import Digest, NewsCandidate, SelectedNews
from app.services.digest_service import DigestService, _url_fingerprint
from app.services.step1_filters import STEP1_FILTER_DEF_BY_ID
from app.services.step1_recent_top5 import (
    RECENT_TOP5_LOOKBACK_DIGESTS,
    article_page_fingerprint,
    query_recent_top5_url_fingerprints,
)


def _make_db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    session_cls = sessionmaker(bind=engine)
    Base.metadata.create_all(bind=engine)
    return session_cls()


def _add_digest_with_top5(db, day: date, url: str, *, finalized: bool = True) -> Digest:
    digest = Digest(
        date=day,
        status="selected",
        current_step="selected",
        digest_type="serious",
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    if finalized:
        digest.proxyapi_finalized_at = datetime.utcnow()
        digest.proxyapi_finalized_cost_rub = 1.0
    db.add(digest)
    db.commit()
    db.refresh(digest)
    candidate = NewsCandidate(
        digest_id=digest.id,
        original_number=1,
        title="Тест",
        url=url,
        source="example.com",
        tier="Tier-2",
        published_at="2026-05-01",
        category="technology",
        description="Описание",
        significance_score=2,
        novelty_score=2,
        impact_score=2,
        total_score=6,
        reliability_status="✅",
        link_status=True,
        headline_editorial_ok=True,
        page_verified=True,
    )
    db.add(candidate)
    db.commit()
    db.refresh(candidate)
    db.add(
        SelectedNews(
            digest_id=digest.id,
            candidate_id=candidate.id,
            original_number=1,
            output_position=1,
        )
    )
    db.commit()
    return digest


def test_article_page_fingerprint_matches_digest_service():
    url = "https://www.Example.com/news/ai-story/"
    assert article_page_fingerprint(url) == _url_fingerprint(url)


def test_query_recent_top5_excludes_oldest_when_eighth_previous():
    db = _make_db()
    base = date(2026, 5, 20)
    repeat_url = "https://tech.example.com/articles/ai-1"
    for i in range(1, 8):
        _add_digest_with_top5(db, base - timedelta(days=i), f"https://other.example.com/{i}")
    _add_digest_with_top5(db, base - timedelta(days=8), repeat_url)

    current = Digest(
        date=base,
        status="step_0",
        current_step="step_0",
        digest_type="serious",
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    db.add(current)
    db.commit()
    db.refresh(current)

    fps = query_recent_top5_url_fingerprints(db, digest_id=current.id, digest_date=current.date)
    assert article_page_fingerprint(repeat_url) not in fps


def test_query_recent_top5_includes_repeat_in_last_seven():
    db = _make_db()
    base = date(2026, 5, 20)
    repeat_url = "https://tech.example.com/articles/ai-1"
    for i in range(1, 7):
        url = repeat_url if i == 2 else f"https://other.example.com/{i}"
        _add_digest_with_top5(db, base - timedelta(days=i), url)

    current = Digest(
        date=base,
        status="step_0",
        current_step="step_0",
        digest_type="serious",
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    db.add(current)
    db.commit()
    db.refresh(current)

    fps = query_recent_top5_url_fingerprints(db, digest_id=current.id, digest_date=current.date)
    assert article_page_fingerprint(repeat_url) in fps
    assert RECENT_TOP5_LOOKBACK_DIGESTS == 7


def test_query_recent_top5_ignores_non_finalized_previous():
    db = _make_db()
    repeat_url = "https://news.example.com/page/only-draft"
    _add_digest_with_top5(db, date(2026, 5, 18), repeat_url, finalized=False)
    current = Digest(
        date=date(2026, 5, 20),
        status="step_0",
        current_step="step_0",
        digest_type="serious",
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    db.add(current)
    db.commit()
    db.refresh(current)
    fps = query_recent_top5_url_fingerprints(db, digest_id=current.id, digest_date=current.date)
    assert article_page_fingerprint(repeat_url) not in fps


def test_different_url_same_story_allowed():
    db = _make_db()
    prev_day = date(2026, 5, 18)
    _add_digest_with_top5(db, prev_day, "https://source-a.example.com/news/ai-update")
    current = Digest(
        date=date(2026, 5, 20),
        status="step_0",
        current_step="step_0",
        digest_type="serious",
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    db.add(current)
    db.commit()
    db.refresh(current)
    fps = query_recent_top5_url_fingerprints(db, digest_id=current.id, digest_date=current.date)
    assert article_page_fingerprint("https://source-b.example.com/news/ai-update-v2") not in fps


def test_recent_top5_repeat_reason_on_service():
    db = _make_db()
    url = "https://news.example.com/page/42"
    _add_digest_with_top5(db, date(2026, 5, 18), url)
    current = Digest(
        date=date(2026, 5, 20),
        status="step_0",
        current_step="step_0",
        digest_type="serious",
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    db.add(current)
    db.commit()
    db.refresh(current)

    service = DigestService(db)
    service._activate_step1_filter_states(
        [{"id": "recent_top5_repeat", "enabled": True, "order": 1}]
    )
    service._active_recent_top5_fps = service._load_recent_top5_fingerprints(current)
    assert service._recent_top5_repeat_reason(url) == "recent_top5_repeat"
    assert service._recent_top5_repeat_reason("https://other.example.com/new") is None

    service._activate_step1_filter_states(
        [{"id": "recent_top5_repeat", "enabled": False, "order": 1}]
    )
    service._active_recent_top5_fps = set()
    assert service._recent_top5_repeat_reason(url) is None


def test_filter_in_catalog():
    assert "recent_top5_repeat" in STEP1_FILTER_DEF_BY_ID
    assert STEP1_FILTER_DEF_BY_ID["recent_top5_repeat"].stage == "step2"


def _add_pool_candidate(db, digest: Digest, *, url: str, number: int) -> NewsCandidate:
    candidate = NewsCandidate(
        digest_id=digest.id,
        original_number=number,
        title=f"Новость {number}",
        url=url,
        source="example.com",
        tier="Tier-2",
        published_at="2026-05-01",
        category="technology",
        description="Описание",
        significance_score=2,
        novelty_score=2,
        impact_score=2,
        total_score=number,
        reliability_status="✅",
        link_status=True,
        headline_editorial_ok=True,
        page_verified=True,
    )
    db.add(candidate)
    db.commit()
    db.refresh(candidate)
    return candidate


def test_select_news_rejects_recent_top5_repeat():
    db = _make_db()
    repeat_url = "https://news.example.com/page/repeat-me"
    _add_digest_with_top5(db, date(2026, 5, 18), repeat_url)
    current = Digest(
        date=date(2026, 5, 20),
        status="step_1_candidates",
        current_step="step_1",
        digest_type="serious",
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    db.add(current)
    db.commit()
    db.refresh(current)

    repeat_candidate = _add_pool_candidate(db, current, url=repeat_url, number=1)
    others = [
        _add_pool_candidate(db, current, url=f"https://fresh.example.com/{i}", number=i + 1)
        for i in range(1, 6)
    ]

    service = DigestService(db)
    with pytest.raises(Exception) as exc:
        service.select_news(current.id, [repeat_candidate.id] + [c.id for c in others[:4]], top5=False)
    assert "последних выпусков" in str(exc.value.detail)


def test_select_news_allows_repeat_in_pool_but_not_in_top5_auto():
    db = _make_db()
    repeat_url = "https://news.example.com/page/repeat-me"
    _add_digest_with_top5(db, date(2026, 5, 18), repeat_url)
    current = Digest(
        date=date(2026, 5, 20),
        status="step_1_candidates",
        current_step="step_1",
        digest_type="serious",
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    db.add(current)
    db.commit()
    db.refresh(current)

    _add_pool_candidate(db, current, url=repeat_url, number=10)
    for i in range(5):
        _add_pool_candidate(db, current, url=f"https://fresh.example.com/{i}", number=i + 1)

    service = DigestService(db)
    picked = service.select_news(current.id, [], top5=True)
    assert len(picked) == 5
    picked_urls = {
        article_page_fingerprint(str(c.url or ""))
        for c in db.query(NewsCandidate).filter(NewsCandidate.id.in_([p.candidate_id for p in picked])).all()
    }
    assert article_page_fingerprint(repeat_url) not in picked_urls
