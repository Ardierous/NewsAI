"""Сквозной офлайн-тест пайплайна шагов 0→4 без live ProxyAPI."""

from datetime import date
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import Analytics, Asset, Digest, FinalOutput
from app.services import digest_service as ds
from app.services.digest_service import (
    DigestService,
    STATUS_ANALYTICS,
    STATUS_FINAL,
    STATUS_SELECTED,
    STATUS_STEP0,
    STATUS_STEP1,
)
from app.services.step1_filter_settings import _bootstrap_filter_config, save_step1_filter_settings


@pytest.fixture(autouse=True)
def _isolated_step1_filter_settings(tmp_path, monkeypatch):
    from app.services import step1_filter_settings as settings_mod

    path = tmp_path / "step1_filter_settings.json"
    monkeypatch.setattr(settings_mod, "_STEP1_FILTER_SETTINGS_PATH", path)
    cfg = _bootstrap_filter_config()
    cfg["min_discovered_pages"] = 10
    cfg["min_collection_iterations"] = 1
    save_step1_filter_settings(cfg)
    yield


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


def _make_chain_service(monkeypatch: pytest.MonkeyPatch, tmp_path) -> tuple[DigestService, Digest]:
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
    service.settings.step2_max_cost_rub = 9999.0
    service.settings.auto_run_step3_after_order = False
    service.settings.enable_step4_image_generation = False
    service.settings.docx_dir = tmp_path / "docx"
    service.settings.image_dir = tmp_path / "images"
    service.settings.docx_dir.mkdir(parents=True, exist_ok=True)
    service.settings.image_dir.mkdir(parents=True, exist_ok=True)

    seed_rows = [
        {"original_number": i + 1, "title": f"Candidate {i}", "url": f"https://source{i}.example.com/news/{i}"}
        for i in range(10)
    ]
    monkeypatch.setattr(
        service.cost_tracker,
        "measure",
        lambda fn, source, **kwargs: (fn(), SimpleNamespace(cost_rub=0.0)),
    )
    monkeypatch.setattr(service.workflow, "run_candidates_research", lambda digest_type, now_msk, manual_urls: seed_rows)
    monkeypatch.setattr(service.workflow, "run_candidates_verify", lambda research_rows: [dict(x) for x in research_rows])
    monkeypatch.setattr(service, "_step1_fetch_supplementary_dicts", lambda *args, **kwargs: [])
    monkeypatch.setattr(service, "_prefilter_llm_candidates_fetchable", lambda _digest_id, rows: (rows, []))
    monkeypatch.setattr(service, "_collect_search_verified_candidates", lambda *args, **kwargs: ([], {}))
    monkeypatch.setattr(ds, "_expand_listing_url_candidates", _fake_expand_listing)

    score_rows = [{"title": f"Candidate {i}", "url": f"https://source{i}.example.com/news/{i}"} for i in range(10)]
    monkeypatch.setattr(service.workflow, "run_candidates_score", lambda verify_rows, now_msk: score_rows)

    def fake_verify(_digest, item: dict, **_kwargs) -> None:
        item["headline_editorial_ok"] = True
        item["link_status"] = True
        item["page_verified"] = True
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

    monkeypatch.setattr(
        service.workflow,
        "run_ordering",
        lambda order_payload: [
            {
                "candidate_id": item["candidate_id"],
                "output_position": idx + 1,
                "ordering_reason": "Тестовый порядок",
            }
            for idx, item in enumerate(order_payload)
        ],
    )

    def fake_analytics(payload):
        return {
            "items": [
                {
                    "candidate_id": row["candidate_id"],
                    "essence": f"Суть {row['title'][:40]}",
                    "comment": "Комментарий для публикации.",
                    "analysis": "Анализ последствий.",
                }
                for row in payload
            ],
            "overall_analysis": "Общий вывод по выпуску.",
            "hashtags": ["#ИИ", "#Нейросети"],
            "self_check": [{"check_name": "coverage", "status": "pass", "comment": "ok"}],
        }

    monkeypatch.setattr(service.workflow, "run_analytics", fake_analytics)

    monkeypatch.setattr(
        service.workflow,
        "run_platform_writer",
        lambda payload, platforms=None: {p: f"Текст для {p}: {payload.get('hook_variant', 'A')}" for p in (platforms or [])},
    )
    monkeypatch.setattr(
        service.workflow,
        "run_qc",
        lambda outputs, has_ok=True: [{"check_name": "format", "status": "pass", "comment": "ok"}],
    )

    return service, digest


def test_digest_pipeline_steps_0_to_4(monkeypatch: pytest.MonkeyPatch, tmp_path):
    service, digest = _make_chain_service(monkeypatch, tmp_path)

    step0 = service.run_step_0(digest.id, "serious", news_window_days=3, news_window_day_kind="calendar")
    assert step0.status == STATUS_STEP0
    assert step0.digest_type == "serious"

    candidates = service.run_step_1(digest.id, [])
    service.db.refresh(digest)
    assert len(candidates) >= 10
    assert digest.status == STATUS_STEP1

    candidate_ids = [c.id for c in candidates[:5]]
    selected = service.select_news(digest.id, candidate_ids, top5=False)
    service.db.refresh(digest)
    assert len(selected) == 5
    assert digest.status == STATUS_SELECTED

    ordered = service.run_step_2_order(digest.id, candidate_ids)
    assert len(ordered) == 5
    assert all(r.output_position for r in ordered)

    analytics = service.run_step_3_analytics(digest.id, "")
    service.db.refresh(digest)
    assert digest.status == STATUS_ANALYTICS
    assert len(analytics.get("items", [])) == 5
    assert service.db.query(Analytics).filter(Analytics.digest_id == digest.id).count() == 5

    texts = service.run_step_4_generate_texts(digest.id, ["telegram", "vk"], hook_variant="A")
    service.db.refresh(digest)
    assert digest.status == STATUS_FINAL
    assert "telegram" in texts["platforms"]
    assert service.db.query(FinalOutput).filter(FinalOutput.digest_id == digest.id).count() >= 2
    docx_asset = (
        service.db.query(Asset).filter(Asset.digest_id == digest.id, Asset.type == "docx").order_by(Asset.id.desc()).first()
    )
    assert docx_asset is not None
