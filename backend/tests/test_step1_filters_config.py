import json
from datetime import date, datetime
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import Digest, Step1DiscoveredNews
from app.services.digest_service import DigestService
from app.services.news_search import search_url_prefilter_reason
from app.services import step1_filter_settings as settings_mod
from app.services.step1_filter_settings import (
    get_min_discovered_pages,
    load_step1_filter_settings,
    normalize_step1_filter_config,
    normalize_step1_filter_states,
    save_step1_filter_settings,
)


def _make_db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    session_cls = sessionmaker(bind=engine)
    Base.metadata.create_all(bind=engine)
    return session_cls()


@pytest.fixture
def step1_settings_file(tmp_path, monkeypatch):
    path = tmp_path / "step1_filter_settings.json"
    monkeypatch.setattr(settings_mod, "_STEP1_FILTER_SETTINGS_PATH", path)
    return path


def test_step1_filter_states_keep_catalog_and_allow_full_toggle(step1_settings_file):
    boot = settings_mod._bootstrap_filter_config()
    step1_settings_file.write_text(json.dumps(boot, ensure_ascii=False), encoding="utf-8")
    states = normalize_step1_filter_states(
        [
            {"id": "http_unreachable", "enabled": False, "order": 1},
            {"id": "product_tool_promo", "enabled": False, "order": 2},
        ],
        digest_type="serious",
    )
    by_id = {x["id"]: x for x in states}
    assert by_id["http_unreachable"]["enabled"] is False
    assert by_id["product_tool_promo"]["enabled"] is False
    assert [x["order"] for x in states] == list(range(1, len(states) + 1))


def test_step1_filter_settings_roundtrip_via_service(step1_settings_file):
    boot = settings_mod._bootstrap_filter_config()
    step1_settings_file.write_text(json.dumps(boot, ensure_ascii=False), encoding="utf-8")
    db = _make_db()
    digest = Digest(
        date=date(2026, 5, 20),
        status="step_0",
        current_step="step_0",
        digest_type="serious",
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    db.add(digest)
    db.commit()
    db.refresh(digest)

    service = DigestService(db)
    saved = service.save_step1_filters_payload(
        digest.id,
        {
            "version": 1,
            "filters": [
                {"id": "product_tool_promo", "enabled": False, "order": 1},
                {"id": "http_unreachable", "enabled": False, "order": 2},
            ],
            "min_discovered_pages": 25,
            "min_collection_iterations": 7,
        },
    )
    by_id = {x["id"]: x for x in saved["config"]["filters"]}
    assert by_id["product_tool_promo"]["enabled"] is False
    assert by_id["http_unreachable"]["enabled"] is False
    assert saved["config"]["min_discovered_pages"] == 25
    assert saved["config"]["min_collection_iterations"] == 7

    fetched = service.get_step1_filters_payload(digest.id)
    assert len(fetched["catalog"]) >= 5
    assert "product_tool_promo" in fetched["counters"]
    assert fetched["config"]["min_discovered_pages"] == 25
    assert fetched["config"]["min_collection_iterations"] == 7
    assert "defaults" not in fetched

    raw = json.loads(step1_settings_file.read_text(encoding="utf-8"))
    assert raw["version"] == 2
    assert raw["serious"]["min_discovered_pages"] == 25
    assert raw["serious"]["min_collection_iterations"] == 7
    assert "off_topic_not_curious" not in {f["id"] for f in raw["serious"]["filters"]}


def test_normalize_step1_filter_config_reads_min_pages_from_file(step1_settings_file):
    step1_settings_file.write_text(
        json.dumps(
            {
                "version": 1,
                "min_discovered_pages": 30,
                "filters": [{"id": "invalid_url", "enabled": True, "order": 1}],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    assert get_min_discovered_pages("serious") == 30
    assert load_step1_filter_settings("serious")["min_discovered_pages"] == 30


def test_search_prefilter_respects_custom_order_for_conflicting_rules():
    url = "https://example.com/search/product/ai-assistant"
    assert (
        search_url_prefilter_reason(
            url,
            order=["product_tool_page", "news_listing_page", "llm_hallucinated_url"],
        )
        == "product_tool_page"
    )


def test_verify_skips_date_window_when_filter_disabled(monkeypatch):
    from types import SimpleNamespace

    from app.services import digest_service as ds

    url = "https://www.vedomosti.ru/technologies/articles/2023/05/31/old-ai"
    html = """
    <html><head>
      <meta property="og:type" content="article">
      <meta property="og:title" content="Старый материал про ИИ">
      <meta property="article:published_time" content="2023-05-31T12:00:00+03:00">
    </head><body><h1>Старый материал про ИИ</h1>
    <p>Нейросети и машинное обучение.</p></body></html>
    """

    class _Resp:
        def __init__(self, code: int, text: str):
            self.status_code = code
            self.text = text
            self.url = url
            self.encoding = "utf-8"
            self.apparent_encoding = "utf-8"

    monkeypatch.setattr(ds.requests, "get", lambda *_a, **_k: _Resp(200, html))

    digest = SimpleNamespace(
        id=1,
        date=__import__("datetime").date(2026, 5, 20),
        news_window_days=3,
        news_window_day_kind="calendar",
    )
    item = {"original_number": 1, "title": "", "url": url, "verification_comment": "", "link_status": False}
    svc = SimpleNamespace()
    svc._ensure_russian_candidate_title = lambda _d, _u, h: h

    def _off(fid: str) -> bool:
        if fid == "off_topic_not_curious":
            return False
        return fid != "published_before_window"

    ds.DigestService._verify_llm_candidate_dict(svc, digest, item, filter_enabled=_off)
    assert "published_before_window" not in str(item.get("verification_comment") or "")
    assert item.get("headline_editorial_ok") is True


def test_filter_counters_fall_back_to_discovered_news_journal():
    db = _make_db()
    digest = Digest(
        date=date(2026, 5, 20),
        status="step_1_candidates",
        current_step="step_1_candidates",
        digest_type="serious",
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    db.add(digest)
    db.commit()
    db.refresh(digest)
    db.add(
        Step1DiscoveredNews(
            digest_id=digest.id,
            source_stage="step1",
            title="Ok",
            url="https://example.com/ok",
            source="example.com",
            published_at="2026-05-20",
            headline_editorial_ok=True,
            link_status=True,
            page_verified=True,
            reject_codes="",
        )
    )
    db.add(
        Step1DiscoveredNews(
            digest_id=digest.id,
            source_stage="step1",
            title="Bad",
            url="https://example.com/bad",
            source="example.com",
            published_at="2026-05-19",
            headline_editorial_ok=False,
            link_status=False,
            page_verified=False,
            reject_codes="http_unreachable,published_before_window",
        )
    )
    db.commit()

    service = DigestService(db)
    payload = service.get_step1_filters_payload(digest.id)
    assert payload["counters"]["http_unreachable"] == 1
    assert payload["counters"]["published_before_window"] == 1
    assert payload["journal_totals"]["total"] == 2
    assert payload["journal_totals"]["rejected"] == 1
