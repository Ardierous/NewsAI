"""Тесты редактора tiers (шаг 0 UI)."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.database import Base
from app.models import Digest, NewsCandidate, SelectedNews, Step1UrlRegistry
from app.schemas_source_tiers import SourceHostIn, SourceTierGroupIn, SourceTiersEditorUpdate
from app.services.source_tiers_editor import (
    _format_section_body,
    _rewrite_host_rules,
    build_source_tiers_editor,
    expand_search_seed_urls,
    marker_to_listing_seed_url,
    save_source_tiers_editor,
)


@pytest.fixture
def tiers_rules_tmp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from app.config import Settings, get_settings

    serious = tmp_path / "source_tiers.txt"
    serious.write_text(
        "prompt\n--- HOST_RULES ---\n[tier1_hosts]\nhabr.com\n\n[tier2_hosts]\nvc.ru\n",
        encoding="utf-8",
    )
    curious = tmp_path / "curious.txt"
    curious.write_text(
        "--- HOST_RULES ---\n[curious_tier1_hosts]\n9gag.com\n",
        encoding="utf-8",
    )
    settings = Settings(source_tiers_path=serious, curious_source_hosts_path=curious)
    monkeypatch.setattr("app.config.get_settings", lambda: settings)
    get_settings.cache_clear()
    return serious, curious


@pytest.fixture()
def db_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(
        bind=engine,
        tables=[
            Digest.__table__,
            NewsCandidate.__table__,
            SelectedNews.__table__,
            Step1UrlRegistry.__table__,
        ],
    )
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def test_format_section_body_keeps_auto_block():
    existing = ["# auto-unreachable: test", "bad.example.com", "habr.com"]
    body = _format_section_body(["vc.ru"], existing)
    assert "vc.ru" in body
    assert "bad.example.com" in body
    assert "habr.com" not in body


def test_rewrite_host_rules_updates_section(tmp_path: Path):
    path = tmp_path / "rules.txt"
    path.write_text(
        "--- HOST_RULES ---\n[tier1_hosts]\nold.com\n\n[tier2_hosts]\nvc.ru\n",
        encoding="utf-8",
    )
    _rewrite_host_rules(path, {"tier1_hosts": ["habr.com", "vedomosti.ru"]})
    text = path.read_text(encoding="utf-8")
    assert "habr.com" in text
    assert "vedomosti.ru" in text
    assert "old.com" not in text
    assert "vc.ru" in text


def test_build_and_save_serious_editor(db_session: Session, tiers_rules_tmp):
    serious, curious = tiers_rules_tmp
    settings = type("S", (), {"source_tiers_path": serious, "curious_source_hosts_path": curious})()
    payload = build_source_tiers_editor(db_session, settings, "serious")
    assert payload.digest_type == "serious"
    assert payload.groups[0].id == "search_seed_urls"
    assert payload.groups[0].priority == 1
    assert any(g.id == "tier1_hosts" for g in payload.groups)

    updated = save_source_tiers_editor(
        db_session,
        settings,
        SourceTiersEditorUpdate(
            digest_type="serious",
            groups=[
                SourceTierGroupIn(id="tier1_hosts", hosts=[SourceHostIn(marker="habr.com"), SourceHostIn(marker="ria.ru")]),
                SourceTierGroupIn(id="tier2_hosts", hosts=[SourceHostIn(marker="vc.ru")]),
            ],
        ),
    )
    assert any(h.marker == "ria.ru" for g in updated.groups if g.id == "tier1_hosts" for h in g.hosts)
    text = serious.read_text(encoding="utf-8")
    assert "ria.ru" in text


def test_host_stats_aggregation(db_session: Session, tiers_rules_tmp):
    serious, curious = tiers_rules_tmp
    settings = type("S", (), {"source_tiers_path": serious, "curious_source_hosts_path": curious})()
    now = datetime.utcnow()
    db_session.add(
        Step1UrlRegistry(
            url_fingerprint="habr.com/news/1",
            url="https://habr.com/news/1",
            host="habr.com",
            digest_type="serious",
            bucket="raw",
            last_seen_at=now,
            expires_at=now + timedelta(days=90),
        )
    )
    digest = Digest(date=now.date(), digest_type="serious", status="step_1_candidates", current_step="step_1_candidates")
    db_session.add(digest)
    db_session.flush()
    cand = NewsCandidate(
        digest_id=digest.id,
        original_number=1,
        title="t",
        url="https://habr.com/news/2",
        source="Habr",
        published_at="2026-06-18",
        category="technology",
        description="d",
        page_verified=True,
        link_status=True,
        headline_editorial_ok=True,
    )
    db_session.add(cand)
    db_session.flush()
    db_session.add(SelectedNews(digest_id=digest.id, candidate_id=cand.id, original_number=1, output_position=1))
    db_session.commit()

    payload = build_source_tiers_editor(db_session, settings, "serious", window_days=30)
    tier1 = next(g for g in payload.groups if g.id == "tier1_hosts")
    habr = next(h for h in tier1.hosts if h.marker == "habr.com")
    assert habr.stats.raw_count >= 1
    assert habr.stats.pool_count >= 1
    assert habr.stats.selected_count >= 1
    assert habr.stats.raw_count >= habr.stats.pool_count


def test_raw_count_includes_verified_bucket(db_session: Session, tiers_rules_tmp):
    serious, curious = tiers_rules_tmp
    settings = type("S", (), {"source_tiers_path": serious, "curious_source_hosts_path": curious})()
    now = datetime.utcnow()
    db_session.add(
        Step1UrlRegistry(
            url_fingerprint="vc.ru/news/9",
            url="https://vc.ru/news/9",
            host="vc.ru",
            digest_type="serious",
            bucket="verified",
            last_seen_at=now,
            expires_at=now + timedelta(days=90),
        )
    )
    db_session.commit()

    payload = build_source_tiers_editor(db_session, settings, "serious", window_days=30)
    tier2 = next(g for g in payload.groups if g.id == "tier2_hosts")
    vc = next(h for h in tier2.hosts if h.marker == "vc.ru")
    assert vc.stats.raw_count >= 1


def test_telegram_channel_seed_stats(db_session: Session, tiers_rules_tmp):
    serious, curious = tiers_rules_tmp
    settings = type(
        "S",
        (),
        {
            "source_tiers_path": serious,
            "curious_source_hosts_path": curious,
            "step1_telegram_monitor_enabled": True,
            "step1_telegram_monitor_channels": "technokratos",
        },
    )()
    now = datetime.utcnow()
    tg_marker = "https://t.me/s/technokratos"
    db_session.add(
        Step1UrlRegistry(
            url_fingerprint="habr.com/news/tg1",
            url="https://habr.com/news/tg1",
            host="habr.com",
            digest_type="serious",
            bucket="raw",
            seed_marker=tg_marker,
            last_seen_at=now,
            expires_at=now + timedelta(days=90),
        )
    )
    digest = Digest(date=now.date(), digest_type="serious", status="step_1_candidates", current_step="step_1_candidates")
    db_session.add(digest)
    db_session.flush()
    cand = NewsCandidate(
        digest_id=digest.id,
        original_number=1,
        title="t",
        url="https://habr.com/news/tg2",
        source="Habr",
        published_at="2026-06-18",
        category="technology",
        description="d",
        page_verified=True,
        link_status=True,
        headline_editorial_ok=True,
        seed_marker=tg_marker,
    )
    db_session.add(cand)
    db_session.commit()

    payload = build_source_tiers_editor(db_session, settings, "serious", window_days=30)
    seeds = next(g for g in payload.groups if g.id == "search_seed_urls")
    tg = next(h for h in seeds.hosts if h.marker == tg_marker)
    assert tg.stats.raw_count >= 1
    assert tg.stats.pool_count >= 1


def test_search_seed_urls_includes_telegram_monitor(db_session: Session, tiers_rules_tmp):
    serious, curious = tiers_rules_tmp
    settings = type(
        "S",
        (),
        {
            "source_tiers_path": serious,
            "curious_source_hosts_path": curious,
            "step1_telegram_monitor_enabled": True,
            "step1_telegram_monitor_channels": "technokratos",
        },
    )()
    payload = build_source_tiers_editor(db_session, settings, "serious", window_days=30)
    seeds = next(g for g in payload.groups if g.id == "search_seed_urls")
    assert any(h.marker == "https://t.me/s/technokratos" for h in seeds.hosts)


def test_marker_to_listing_seed_url():
    assert marker_to_listing_seed_url("habr.com") == "https://habr.com/"
    assert marker_to_listing_seed_url("https://vc.ru/ai") == "https://vc.ru/ai"
    assert marker_to_listing_seed_url("news.google.") is None


def test_save_tier_host_syncs_search_seed_urls(db_session: Session, tiers_rules_tmp):
    serious, curious = tiers_rules_tmp
    settings = type("S", (), {"source_tiers_path": serious, "curious_source_hosts_path": curious})()
    save_source_tiers_editor(
        db_session,
        settings,
        SourceTiersEditorUpdate(
            digest_type="serious",
            groups=[
                SourceTierGroupIn(id="tier1_hosts", hosts=[SourceHostIn(marker="example.com")]),
            ],
        ),
    )
    text = serious.read_text(encoding="utf-8")
    assert "example.com" in text
    assert "https://example.com/" in text


def test_expand_search_seed_urls_includes_tier_hosts(tiers_rules_tmp):
    serious, curious = tiers_rules_tmp
    settings = type("S", (), {"source_tiers_path": serious, "curious_source_hosts_path": curious})()
    expanded = expand_search_seed_urls((), settings=settings, digest_type="serious")
    assert "https://habr.com/" in expanded
    assert "https://vc.ru/" in expanded


def test_expand_search_seed_urls_skips_investing_homepage_when_sections_exist():
    from app.config import get_settings
    from app.source_tiers_policy import get_source_tiers_policy

    settings = get_settings()
    policy = get_source_tiers_policy(settings.source_tiers_path)
    expanded = expand_search_seed_urls(
        policy.search_seed_urls,
        settings=settings,
        digest_type="serious",
    )
    assert "https://investing.com/" not in expanded
    assert "https://ru.investing.com/news/stock-market-news" in expanded


def test_build_search_seed_urls_includes_tier_hosts(db_session: Session, tiers_rules_tmp):
    serious, curious = tiers_rules_tmp
    settings = type("S", (), {"source_tiers_path": serious, "curious_source_hosts_path": curious})()
    payload = build_source_tiers_editor(db_session, settings, "serious")
    seeds = next(g for g in payload.groups if g.id == "search_seed_urls")
    assert any("habr.com" in h.marker for h in seeds.hosts)
