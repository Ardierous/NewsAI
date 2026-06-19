"""Тесты реестра URL шага 1 и автоблока доменов."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import Step1FilterEnabledSnapshot, Step1HostUnreachableStat, Step1UrlRegistry
from app.services.step1_tiers_autoblock import append_blocked_search_host, sync_autoblocked_hosts
from app.services.step1_url_registry import (
    BUCKET_MULTI,
    BUCKET_RAW,
    BUCKET_VERIFIED,
    classify_registry_item,
    detect_newly_disabled_filters,
    list_urls_for_reverify,
    load_registry_raw_urls,
    purge_expired_registry,
    register_raw_urls,
    save_filter_snapshot,
)


class _Settings:
    step1_url_registry_ttl_days = 90
    step1_url_registry_reuse_enabled = True
    step1_host_unreachable_autoblock_threshold = 20
    source_tiers_path = Path(__file__).resolve().parents[1] / "app" / "prompts" / "source_tiers.txt"
    curious_source_hosts_path = Path(__file__).resolve().parents[1] / "app" / "prompts" / "curious_source_hosts.txt"


@pytest.fixture()
def db_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(
        engine,
        tables=[
            Step1UrlRegistry.__table__,
            Step1HostUnreachableStat.__table__,
            Step1FilterEnabledSnapshot.__table__,
        ],
    )
    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        yield session
    finally:
        session.close()


def test_register_raw_and_classify_buckets(db_session):
    settings = _Settings()
    n = register_raw_urls(
        db_session,
        settings,
        urls=["https://example.com/a", "https://example.com/b"],
        digest_type="curious",
        digest_id=1,
    )
    db_session.commit()
    assert n == 2
    row = db_session.query(Step1UrlRegistry).filter(Step1UrlRegistry.url.like("%/a")).one()
    assert row.bucket == BUCKET_RAW

    classify_registry_item(
        db_session,
        settings,
        {
            "url": "https://example.com/a",
            "title": "Заголовок про ИИ",
            "link_status": True,
            "headline_editorial_ok": True,
            "verification_comment": "",
        },
        digest_type="curious",
        digest_id=1,
    )
    db_session.commit()
    row = db_session.query(Step1UrlRegistry).filter(Step1UrlRegistry.url.like("%/a")).one()
    assert row.bucket == BUCKET_VERIFIED

    classify_registry_item(
        db_session,
        settings,
        {
            "url": "https://example.com/b",
            "title": "",
            "link_status": False,
            "headline_editorial_ok": False,
            "verification_comment": "REJECT_REASON:http_unreachable",
        },
        digest_type="curious",
        digest_id=1,
    )
    db_session.commit()
    row = db_session.query(Step1UrlRegistry).filter(Step1UrlRegistry.url.like("%/b")).one()
    assert row.bucket == "reject:http_unreachable"


def test_register_raw_skips_duplicate_fingerprints_in_one_batch(db_session):
    settings = _Settings()
    url = "https://example.com/same-article"
    n = register_raw_urls(
        db_session,
        settings,
        urls=[url, url, f"{url}?utm=1"],
        digest_type="serious",
        digest_id=2,
    )
    db_session.commit()
    assert n == 1
    assert db_session.query(Step1UrlRegistry).count() == 1


def test_reverify_when_filter_disabled(db_session):
    settings = _Settings()
    save_filter_snapshot(
        db_session,
        "curious",
        {"off_topic_not_curious": True, "http_unreachable": True},
    )
    disabled = detect_newly_disabled_filters(
        db_session,
        "curious",
        {"off_topic_not_curious": False, "http_unreachable": True},
    )
    assert disabled == ["off_topic_not_curious"]

    db_session.add(
        Step1UrlRegistry(
            url_fingerprint="example.com/x",
            url="https://example.com/x",
            host="example.com",
            digest_type="curious",
            bucket="reject:off_topic_not_curious",
            reject_codes="off_topic_not_curious",
            title="t",
            source_stage="search",
            verification_comment="REJECT_REASON:off_topic_not_curious",
            last_digest_id=1,
            first_seen_at=datetime.utcnow(),
            last_seen_at=datetime.utcnow(),
            expires_at=datetime.utcnow() + timedelta(days=90),
        )
    )
    db_session.add(
        Step1UrlRegistry(
            url_fingerprint="example.com/y",
            url="https://example.com/y",
            host="example.com",
            digest_type="curious",
            bucket=BUCKET_MULTI,
            reject_codes="http_unreachable,off_topic_not_curious",
            title="t2",
            source_stage="search",
            verification_comment="REJECT_REASON:http_unreachable REJECT_REASON:off_topic_not_curious",
            last_digest_id=1,
            first_seen_at=datetime.utcnow(),
            last_seen_at=datetime.utcnow(),
            expires_at=datetime.utcnow() + timedelta(days=90),
        )
    )
    db_session.commit()
    rows = list_urls_for_reverify(db_session, "curious", ["off_topic_not_curious"])
    urls = {r.url for r in rows}
    assert "https://example.com/x" in urls
    assert "https://example.com/y" in urls


def test_purge_expired_registry(db_session):
    settings = _Settings()
    db_session.add(
        Step1UrlRegistry(
            url_fingerprint="old.example.com/z",
            url="https://old.example.com/z",
            host="old.example.com",
            digest_type="serious",
            bucket=BUCKET_RAW,
            reject_codes="",
            title="",
            source_stage="search",
            verification_comment="",
            last_digest_id=1,
            first_seen_at=datetime.utcnow() - timedelta(days=100),
            last_seen_at=datetime.utcnow() - timedelta(days=100),
            expires_at=datetime.utcnow() - timedelta(days=1),
        )
    )
    db_session.commit()
    deleted = purge_expired_registry(db_session, settings)
    assert deleted == 1


def test_append_blocked_search_host(tmp_path):
    path = tmp_path / "tiers.txt"
    path.write_text(
        "[blocked_search_hosts]\narxiv.org\n\n[search_seed_urls]\nhttps://example.com/\n",
        encoding="utf-8",
    )
    changed = append_blocked_search_host(path, "bad-host.example", note="test")
    assert changed is True
    text = path.read_text(encoding="utf-8")
    assert "bad-host.example" in text
    assert append_blocked_search_host(path, "bad-host.example", note="dup") is False


def test_load_registry_raw_urls_skips_reject_buckets(db_session):
    settings = _Settings()
    now = datetime.utcnow()
    exp = now + timedelta(days=90)
    db_session.add(
        Step1UrlRegistry(
            url_fingerprint="example.com/raw",
            url="https://example.com/raw",
            host="example.com",
            digest_type="curious",
            bucket=BUCKET_RAW,
            reject_codes="",
            title="",
            source_stage="search",
            verification_comment="",
            last_digest_id=1,
            first_seen_at=now,
            last_seen_at=now,
            expires_at=exp,
        )
    )
    db_session.add(
        Step1UrlRegistry(
            url_fingerprint="example.com/bad",
            url="https://example.com/bad",
            host="example.com",
            digest_type="curious",
            bucket="reject:http_unreachable",
            reject_codes="http_unreachable",
            title="",
            source_stage="search",
            verification_comment="REJECT_REASON:http_unreachable",
            last_digest_id=1,
            first_seen_at=now,
            last_seen_at=now,
            expires_at=exp,
        )
    )
    db_session.commit()
    urls = load_registry_raw_urls(db_session, settings, digest_type="curious", limit=10)
    assert urls == ["https://example.com/raw"]


def test_load_registry_raw_urls_respects_skip(db_session):
    settings = _Settings()
    register_raw_urls(
        db_session,
        settings,
        urls=["https://example.com/a", "https://example.com/b"],
        digest_type="curious",
        digest_id=1,
    )
    db_session.commit()
    urls = load_registry_raw_urls(
        db_session,
        settings,
        digest_type="curious",
        limit=10,
        skip_urls=["https://example.com/a"],
    )
    assert urls == ["https://example.com/b"]


def test_load_registry_raw_urls_disabled(db_session):
    settings = _Settings()
    settings.step1_url_registry_reuse_enabled = False
    register_raw_urls(
        db_session,
        settings,
        urls=["https://example.com/a"],
        digest_type="curious",
        digest_id=1,
    )
    db_session.commit()
    assert load_registry_raw_urls(db_session, settings, digest_type="curious", limit=5) == []


def test_registry_raw_reuse_shared_across_digest_types(db_session):
    settings = _Settings()
    url = "https://example.com/shared-article"
    register_raw_urls(
        db_session,
        settings,
        urls=[url],
        digest_type="serious",
        digest_id=1,
    )
    db_session.commit()
    assert load_registry_raw_urls(db_session, settings, digest_type="curious", limit=5) == [url]
    assert load_registry_raw_urls(db_session, settings, digest_type="serious", limit=5) == [url]
    assert db_session.query(Step1UrlRegistry).filter(Step1UrlRegistry.url == url).count() == 1


def test_sync_autoblocked_hosts_marks_row(db_session, monkeypatch):
    settings = _Settings()
    db_session.add(
        Step1HostUnreachableStat(
            host="bad-host.example",
            failure_count=25,
            first_failure_at=datetime.utcnow(),
            last_failure_at=datetime.utcnow(),
        )
    )
    db_session.commit()

    def _fake_append(path, host, *, note):
        return True

    monkeypatch.setattr(
        "app.services.step1_tiers_autoblock.append_blocked_search_host",
        _fake_append,
    )
    blocked = sync_autoblocked_hosts(db_session, settings, digest_type="serious")
    assert blocked == ["bad-host.example"]
    row = db_session.get(Step1HostUnreachableStat, "bad-host.example")
    assert row is not None
    assert row.autoblocked_at is not None
