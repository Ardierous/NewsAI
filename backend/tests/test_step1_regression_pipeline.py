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
    _rebalance_verified_pool,
)
from app.services.step1_manual_ratings_export import sync_step1_manual_ratings_export


@pytest.fixture(autouse=True)
def _isolated_step1_filter_settings(tmp_path, monkeypatch):
    """Не читать/писать рабочий step1_filter_settings.json; порог воронки = 10 для тестов пула."""
    from app.services import step1_filter_settings as settings_mod
    from app.services.step1_filter_settings import _bootstrap_filter_config, save_step1_filter_settings

    path = tmp_path / "step1_filter_settings.json"
    monkeypatch.setattr(settings_mod, "_STEP1_FILTER_SETTINGS_PATH", path)
    cfg = _bootstrap_filter_config()
    for key in ("serious", "curious"):
        cfg[key]["min_discovered_pages"] = 10
        cfg[key]["min_collection_iterations"] = 1
    import json

    path.write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")
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
    service.settings.step1_soft_time_limit_sec = 30
    service.settings.step1_hard_time_limit_sec = 60
    service.settings.auto_run_step3_after_order = False

    seed_rows = [{"original_number": i + 1, "title": f"Candidate {i}", "url": f"https://source{i}.example.com/news/{i}"} for i in range(10)]
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
    monkeypatch.setattr(service, "_collect_search_verified_candidates", lambda *args, **kwargs: ([], {}))
    # Реальный rebalance: лимит ≤2 на домен и квоты RU/press.
    monkeypatch.setattr(ds, "_expand_listing_url_candidates", _fake_expand_listing)
    monkeypatch.setattr(
        ds,
        "digest_news_anchor_date",
        lambda digest: digest.date if isinstance(digest.date, date) else digest.date,
    )
    return service, digest


def test_build_manual_candidates_classifies_tier_hosts(monkeypatch: pytest.MonkeyPatch):
    service, digest = _make_service(monkeypatch)
    monkeypatch.setattr(
        ds,
        "_fetch_article_page_bundle",
        lambda url: {
            "ok": True,
            "final_url": url,
            "display_url": url,
            "headline": "Нейросеть и искусственный интеллект: тестовая новость",
            "topic_corpus": "искусственный интеллект нейросети машинное обучение",
        },
    )
    monkeypatch.setattr(service, "_ensure_russian_candidate_title", lambda _d, _u, title: title)
    rows = service._build_manual_candidates(
        digest,
        ["https://habr.com/ru/news/1036050/"],
        "2026-05-21T12:00:00+03:00",
        mandatory=False,
    )
    assert len(rows) == 1
    assert rows[0]["tier"] == "Tier-2"
    assert rows[0]["reliability_status"] == "✅ подтверждено"
    assert rows[0]["is_aggregator"] is False


def test_step1_raises_402_when_proxyapi_budget_exceeded(monkeypatch: pytest.MonkeyPatch):
    service, digest = _make_service(monkeypatch)
    service.proxy.last_error_kind = "budget_exceeded"

    with pytest.raises(HTTPException) as ex:
        service.run_step_1(digest.id, [])

    assert ex.value.status_code == 402
    assert "402" in str(ex.value.detail)
    assert service.digest_proxyapi_budget_exceeded(digest.id)


def test_step1_raises_402_when_proxyapi_zero_balance(monkeypatch: pytest.MonkeyPatch):
    service, digest = _make_service(monkeypatch)
    monkeypatch.setattr(
        service.cost_tracker,
        "get_balance_snapshot",
        lambda: SimpleNamespace(balance=0.0, budget_limit=500.0, budget_used=10.0),
    )

    with pytest.raises(HTTPException) as ex:
        service.run_step_1(digest.id, [])

    assert ex.value.status_code == 402
    assert "нулевой баланс" in str(ex.value.detail).lower()
    assert service.digest_proxyapi_budget_exceeded(digest.id)


def test_manual_listing_url_rejected_not_expanded(monkeypatch: pytest.MonkeyPatch):
    """Ручное поле: лента vc.ru не разворачивается — нужна прямая ссылка на статью."""
    service, digest = _make_service(monkeypatch)

    monkeypatch.setattr(
        ds,
        "_fetch_article_page_bundle",
        lambda _url: {
            "ok": True,
            "final_url": "https://vc.ru/ai",
            "display_url": "https://vc.ru/ai",
            "headline": "AI",
            "topic_corpus": "ai",
            "is_listing_page": True,
            "published_at": "2026-06-01T10:00:00+03:00",
        },
    )

    rows = service._build_manual_candidates(digest, ["https://vc.ru/ai"], "2026-06-01T12:00:00+03:00", mandatory=True)
    assert len(rows) == 1
    row = rows[0]
    assert row["page_verified"] is False
    assert "news_listing_page" in str(row.get("verification_comment") or "")


def test_manual_vc_profile_rejected(monkeypatch: pytest.MonkeyPatch):
    service, digest = _make_service(monkeypatch)
    monkeypatch.setattr(
        ds,
        "_fetch_article_page_bundle",
        lambda _url: {
            "ok": True,
            "final_url": "https://vc.ru/id5731761",
            "display_url": "https://vc.ru/id5731761",
            "headline": "Артур Мартыгин (id5731761)",
            "topic_corpus": "профиль " * 30,
            "is_listing_page": False,
            "headline_strict": True,
            "article_markers": True,
        },
    )
    rows = service._build_manual_candidates(
        digest, ["https://vc.ru/id5731761"], "2026-06-01T12:00:00+03:00", mandatory=True
    )
    assert len(rows) == 1
    assert rows[0]["page_verified"] is False


def test_rebalance_keeps_manual_required_in_pool():
    manual = {
        "url": "https://manual.example.com/ai-story",
        "verification_comment": "MANUAL_REQUIRED: добавлено пользователем",
        "description": "Вставлено в поле URL на шаге 1",
        "total_score": 3,
        "tier": "Tier-3",
        "link_status": True,
        "headline_editorial_ok": True,
        "title": "Ручная новость",
    }
    filler = [
        {
            "url": f"https://filler{i}.example.com/news/{i}",
            "verification_comment": "",
            "description": "search",
            "total_score": 30 - i,
            "tier": "Tier-1",
            "link_status": True,
            "headline_editorial_ok": True,
            "title": f"Filler {i}",
        }
        for i in range(12)
    ]
    pool = filler + [manual]
    chosen = _rebalance_verified_pool(pool, target=5, digest_type="serious")
    urls = {str(x.get("url") or "") for x in chosen}
    assert manual["url"] in urls


def test_rebalance_keeps_manual_required_despite_host_cap():
    host = "habr.com"
    filler = [
        {
            "url": f"https://{host}/ru/news/auto-{i}/",
            "verification_comment": "",
            "description": "search",
            "total_score": 40 - i,
            "tier": "Tier-1",
            "link_status": True,
            "headline_editorial_ok": True,
            "title": f"Авто {i}",
        }
        for i in range(3)
    ]
    manual = {
        "url": f"https://{host}/ru/news/manual-user-story/",
        "verification_comment": "MANUAL_REQUIRED: добавлено пользователем",
        "description": "Вставлено в поле URL на шаге 1",
        "total_score": 3,
        "tier": "Tier-3",
        "link_status": True,
        "headline_editorial_ok": True,
        "title": "Ручная новость пользователя",
    }
    pool = filler + [manual]
    chosen = _rebalance_verified_pool(
        pool,
        target=3,
        digest_type="serious",
        per_host_cap=ds.STEP1_POOL_PER_HOST_CAP,
    )
    urls = {str(x.get("url") or "") for x in chosen}
    assert manual["url"] in urls


def test_manual_unreachable_url_rejected(monkeypatch: pytest.MonkeyPatch):
    service, digest = _make_service(monkeypatch)
    monkeypatch.setattr(ds, "_fetch_article_page_bundle", lambda _url: {"ok": False})
    rows = service._build_manual_candidates(
        digest, ["https://example.com/broken"], "2026-06-01T12:00:00+03:00", mandatory=True
    )
    assert len(rows) == 1
    assert rows[0]["page_verified"] is False
    assert rows[0]["link_status"] is False
    assert "seed_unverified" in str(rows[0].get("verification_comment") or "")


def test_manual_ai_section_path_is_rejected_even_without_listing_flag(monkeypatch: pytest.MonkeyPatch):
    service, digest = _make_service(monkeypatch)

    monkeypatch.setattr(
        ds,
        "_fetch_article_page_bundle",
        lambda _url: {
            "ok": True,
            "final_url": "https://vc.ru/ai",
            "display_url": "https://vc.ru/ai",
            "headline": "AI - Сообщество на vc.ru",
            "topic_corpus": "Лента сообщества",
            "is_listing_page": False,
            "published_at": "2026-06-01T10:00:00+03:00",
        },
    )

    rows = service._build_manual_candidates(digest, ["https://vc.ru/ai"], "2026-06-01T12:00:00+03:00", mandatory=False)
    assert len(rows) == 1
    row = rows[0]
    assert row["page_verified"] is False
    assert row["headline_editorial_ok"] is False
    assert "REJECT_REASON:news_listing_page" in str(row.get("verification_comment") or "")


def test_step1_persists_reject_reasons_on_502(monkeypatch: pytest.MonkeyPatch):
    service, digest = _make_service(monkeypatch)

    score_rows = [{"title": f"Candidate {i}", "url": f"https://source{i}.example.com/news/{i}"} for i in range(10)]
    monkeypatch.setattr(service.workflow, "run_candidates_score", lambda verify_rows, now_msk, **kw: score_rows)

    def fake_verify(_digest_id: int, item: dict, **_kwargs) -> None:
        item["headline_editorial_ok"] = False
        item["link_status"] = False
        item["verification_comment"] = "REJECT_REASON:off_topic_not_ai"

    monkeypatch.setattr(service, "_verify_llm_candidate_dict", fake_verify)

    with pytest.raises(HTTPException) as ex:
        service.run_step_1(digest.id, [])

    assert ex.value.status_code == 502
    assert "Основные причины отбраковки: off_topic_not_ai=10." in str(ex.value.detail)

    saved = (
        service.db.query(Asset)
        .filter(Asset.digest_id == digest.id, Asset.type == "step1_rejected_reasons")
        .order_by(Asset.id.desc())
        .first()
    )
    assert saved is not None
    stats = json.loads(saved.prompt or "{}")
    assert stats.get("off_topic_not_ai") == 10

    preview_count = service.db.query(NewsCandidate).filter(NewsCandidate.digest_id == digest.id).count()
    assert preview_count == 0


def test_step1_success_sets_status_and_candidates(monkeypatch: pytest.MonkeyPatch):
    service, digest = _make_service(monkeypatch)

    score_rows = [{"title": f"Candidate {i}", "url": f"https://source{i}.example.com/news/{i}"} for i in range(10)]
    monkeypatch.setattr(service.workflow, "run_candidates_score", lambda verify_rows, now_msk, **kw: score_rows)

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

    assert len(rows) >= 10
    assert digest.status == STATUS_STEP1
    assert digest.current_step == STATUS_STEP1


def test_search_ingest_does_not_pre_mark_verified_urls_as_seen(monkeypatch: pytest.MonkeyPatch):
    service, digest = _make_service(monkeypatch)

    def fake_verify(_digest: Digest, item: dict, **_kwargs) -> None:
        item["headline_editorial_ok"] = True
        item["link_status"] = True
        item["page_verified"] = True
        item["title"] = "ИИ ускорил обработку данных"
        item["verification_comment"] = ""

    monkeypatch.setattr(service, "_verify_llm_candidate_dict", fake_verify)

    seen_fp: set[str] = set()
    rows = service._ingest_step1_urls_with_listing_expansion(
        digest,
        ["https://techcrunch.com/2026/05/14/openai-ai-news/"],
        "2026-05-14T12:00:00+03:00",
        seen_fp,
        limit=1,
    )

    assert len(rows) == 1
    assert seen_fp == set()


def test_step1_forms_pool_as_soon_as_ten_verified_found(monkeypatch: pytest.MonkeyPatch):
    service, digest = _make_service(monkeypatch)

    rows = []
    for i in range(10):
        rows.append(
            {
                "original_number": i + 1,
                "title": f"ИИ новость {i + 1}",
                "url": f"https://source{i}.example.com/news/{i}",
                "source": f"source{i}.example.com",
                "tier": "Tier-3",
                "published_at": "2026-05-14T12:00:00",
                "category": "technology",
                "description": "Описание",
                "significance_score": 2,
                "novelty_score": 2,
                "impact_score": 2,
                "total_score": 6,
                "reliability_status": "✅ подтверждено",
                "link_status": True,
                "headline_editorial_ok": True,
                "page_verified": True,
                "is_aggregator": False,
                "verification_comment": "",
            }
        )

    monkeypatch.setattr(service, "_collect_search_verified_candidates", lambda *args, **kwargs: (rows, {}))

    result = service.run_step_1(digest.id, [])

    assert len(result) == 10
    assert service.db.query(NewsCandidate).filter(NewsCandidate.digest_id == digest.id).count() == 10


def test_step1_can_return_up_to_fifteen_candidates(monkeypatch: pytest.MonkeyPatch):
    service, digest = _make_service(monkeypatch)
    service.settings.step1_max_candidates_for_ui = 15
    service.settings.step1_batch_size = 20

    rows = []
    for i in range(20):
        rows.append(
            {
                "original_number": i + 1,
                "title": f"ИИ новость {i + 1}",
                "url": f"https://source{i}.example.com/news/{i}",
                "source": f"source{i}.example.com",
                "tier": "Tier-3",
                "published_at": "2026-05-14T12:00:00",
                "category": "technology",
                "description": "Описание",
                "significance_score": 2,
                "novelty_score": 2,
                "impact_score": 2,
                "total_score": 6,
                "reliability_status": "✅ подтверждено",
                "link_status": True,
                "headline_editorial_ok": True,
                "page_verified": True,
                "is_aggregator": False,
                "verification_comment": "",
            }
        )

    monkeypatch.setattr(service, "_collect_search_verified_candidates", lambda *args, **kwargs: (rows, {}))
    monkeypatch.setattr(service, "_step1_run_web_supplement_rounds", lambda *args, **kwargs: None)

    result = service.run_step_1(digest.id, [])

    assert len(result) == 15
    assert service.db.query(NewsCandidate).filter(NewsCandidate.digest_id == digest.id).count() == 15


def test_step1_records_hard_timeout_stop_reason(monkeypatch: pytest.MonkeyPatch):
    service, digest = _make_service(monkeypatch)
    calls = {"n": 0}

    def fake_monotonic() -> float:
        calls["n"] += 1
        return 0.0 if calls["n"] == 1 else 31.0

    monkeypatch.setattr(ds.time, "monotonic", fake_monotonic)
    monkeypatch.setattr(service, "_collect_search_verified_candidates", lambda *args, **kwargs: ([], {}))
    monkeypatch.setattr(service, "_step1_run_web_supplement_rounds", lambda *args, **kwargs: None)

    verified_pool: list[dict] = []
    seen_fp: set[str] = set()
    excluded_urls: list[str] = []
    meta = service._step1_collect_iterative_batches(
        digest,
        verified_pool=verified_pool,
        seen_fp=seen_fp,
        excluded_urls=excluded_urls,
        now_msk="2026-05-19T12:00:00+03:00",
        snapshot_preview_row=lambda _x: None,
        append_verified=lambda item: verified_pool.append(item),
        register_reject=lambda _x: None,
        target_min_verified=10,
        target_max_candidates=15,
        batch_size=20,
        soft_limit_sec=180,
        hard_limit_sec=300,
        started_monotonic=0.0,
        start_iteration=0,
        min_iterations=1,
    )
    assert meta["stop_reason"] in {"hard_timeout", "no_progress"}


def test_step1_collect_runs_at_least_min_iterations_before_no_progress(monkeypatch: pytest.MonkeyPatch):
    service, digest = _make_service(monkeypatch)
    tick = {"n": 0}

    def fake_monotonic() -> float:
        tick["n"] += 1
        return float(tick["n"])

    monkeypatch.setattr(ds.time, "monotonic", fake_monotonic)
    monkeypatch.setattr(service, "_collect_search_verified_candidates", lambda *args, **kwargs: ([], {}))
    monkeypatch.setattr(service, "_step1_run_web_supplement_rounds", lambda *args, **kwargs: None)

    verified_pool: list[dict] = []
    meta = service._step1_collect_iterative_batches(
        digest,
        verified_pool=verified_pool,
        seen_fp=set(),
        excluded_urls=[],
        now_msk="2026-05-19T12:00:00+03:00",
        snapshot_preview_row=lambda _x: None,
        append_verified=lambda item: verified_pool.append(item),
        register_reject=lambda _x: None,
        target_min_verified=10,
        target_max_candidates=20,
        batch_size=20,
        soft_limit_sec=9999,
        hard_limit_sec=9999,
        started_monotonic=1.0,
        start_iteration=0,
        min_iterations=5,
    )
    assert meta["iterations"] >= 5
    assert meta["stop_reason"] == "no_progress"


def test_select_news_allows_pool_below_step1_min_when_five_selectable(monkeypatch: pytest.MonkeyPatch):
    """После частичной пересборки в пуле может быть <10 строк — выбор пятёрки всё равно доступен."""
    service, digest = _make_service(monkeypatch)
    digest.status = STATUS_STEP1
    digest.current_step = STATUS_STEP1
    for idx in range(9):
        service.db.add(
            NewsCandidate(
                digest_id=digest.id,
                original_number=idx + 1,
                title=f"ИИ новость {idx + 1}",
                url=f"https://source{idx + 1}.example.com/news/{idx + 1}",
                source="Example",
                tier="Tier-2",
                published_at="2026-05-14T12:00:00",
                category="technology",
                description="Описание",
                significance_score=2,
                novelty_score=2,
                impact_score=2,
                total_score=6,
                reliability_status="✅ подтверждено",
                link_status=True,
                headline_editorial_ok=True,
                page_verified=True,
            )
        )
    service.db.commit()

    selected = service.select_news(digest.id, [], top5=True)
    assert len(selected) == 5


def test_select_news_allows_reselection_when_status_selected(monkeypatch: pytest.MonkeyPatch):
    """После сохранения пятёрки (status=selected) можно изменить выбор до запуска аналитики."""
    service, digest = _make_service(monkeypatch)
    digest.status = STATUS_STEP1
    digest.current_step = STATUS_STEP1
    candidate_ids: list[int] = []
    for idx in range(6):
        row = NewsCandidate(
            digest_id=digest.id,
            original_number=idx + 1,
            title=f"ИИ новость {idx + 1}",
            url=f"https://source{idx + 1}.example.com/news/{idx + 1}",
            source="Example",
            tier="Tier-2",
            published_at="2026-05-14T12:00:00",
            category="technology",
            description="Описание",
            significance_score=2,
            novelty_score=2,
            impact_score=2,
            total_score=6 - (0 if idx < 5 else 1),
            reliability_status="✅ подтверждено",
            link_status=True,
            headline_editorial_ok=True,
            page_verified=True,
        )
        service.db.add(row)
        service.db.flush()
        candidate_ids.append(row.id)
    service.db.commit()

    first_pick = candidate_ids[:5]
    service.select_news(digest.id, first_pick, top5=False)
    service.db.refresh(digest)
    assert digest.status == STATUS_SELECTED

    new_pick = candidate_ids[1:6]
    updated = service.select_news(digest.id, new_pick, top5=False)
    assert len(updated) == 5
    assert {row.candidate_id for row in updated} == set(new_pick)


def test_select_news_clears_analytics_on_reselection(monkeypatch: pytest.MonkeyPatch):
    """После аналитики можно перевыбрать пятёрку — шаги 3–4 сбрасываются, статус возвращается к selected."""
    service, digest = _make_service(monkeypatch)
    digest.status = STATUS_ANALYTICS
    candidate_ids: list[int] = []
    for idx in range(6):
        row = NewsCandidate(
            digest_id=digest.id,
            original_number=idx + 1,
            title=f"ИИ новость {idx + 1}",
            url=f"https://source{idx + 1}.example.com/news/{idx + 1}",
            source="Example",
            tier="Tier-2",
            published_at="2026-05-14T12:00:00",
            category="technology",
            description="Описание",
            significance_score=2,
            novelty_score=2,
            impact_score=2,
            total_score=6,
            reliability_status="✅ подтверждено",
            link_status=True,
            headline_editorial_ok=True,
            page_verified=True,
        )
        service.db.add(row)
        service.db.flush()
        candidate_ids.append(row.id)
        if idx < 5:
            service.db.add(
                SelectedNews(
                    digest_id=digest.id,
                    candidate_id=row.id,
                    original_number=row.original_number,
                    output_position=idx + 1,
                    ordering_reason="test",
                )
            )
            service.db.add(
                Analytics(
                    digest_id=digest.id,
                    candidate_id=row.id,
                    essence="e",
                    comment="c",
                    analysis="a",
                    source_url=row.url,
                    source_name=row.source,
                    published_at=row.published_at,
                )
            )
    service.db.commit()

    new_pick = candidate_ids[1:6]
    updated = service.select_news(digest.id, new_pick, top5=False)
    service.db.refresh(digest)

    assert digest.status == STATUS_SELECTED
    assert len(updated) == 5
    assert {row.candidate_id for row in updated} == set(new_pick)
    assert service.db.query(Analytics).filter(Analytics.digest_id == digest.id).count() == 0


def test_run_step_2_order_from_analytics_ready_clears_downstream(monkeypatch: pytest.MonkeyPatch):
    service, digest = _make_service(monkeypatch)
    digest.status = STATUS_ANALYTICS
    candidate_ids: list[int] = []
    for idx in range(5):
        row = NewsCandidate(
            digest_id=digest.id,
            original_number=idx + 1,
            title=f"ИИ новость {idx + 1}",
            url=f"https://source{idx + 1}.example.com/news/{idx + 1}",
            source="Example",
            tier="Tier-2",
            published_at="2026-05-14T12:00:00",
            category="technology",
            description="Описание",
            significance_score=2,
            novelty_score=2,
            impact_score=2,
            total_score=6,
            reliability_status="✅ подтверждено",
            link_status=True,
            headline_editorial_ok=True,
            page_verified=True,
        )
        service.db.add(row)
        service.db.flush()
        candidate_ids.append(row.id)
        service.db.add(
            SelectedNews(
                digest_id=digest.id,
                candidate_id=row.id,
                original_number=row.original_number,
                output_position=idx + 1,
                ordering_reason="old",
            )
        )
        service.db.add(
            Analytics(
                digest_id=digest.id,
                candidate_id=row.id,
                essence="e",
                comment="c",
                analysis="a",
                source_url=row.url,
                source_name=row.source,
                published_at=row.published_at,
            )
        )
    service.db.commit()

    ordered = service.run_step_2_order(digest.id, list(reversed(candidate_ids)))
    service.db.refresh(digest)

    assert digest.status == STATUS_SELECTED
    assert len(ordered) == 5
    assert [row.candidate_id for row in ordered] == list(reversed(candidate_ids))
    assert service.db.query(Analytics).filter(Analytics.digest_id == digest.id).count() == 0
    assert service.load_step2_order_rationale(digest.id).startswith("Порядок задан вручную")


def test_select_news_rejects_when_fewer_than_five_selectable(monkeypatch: pytest.MonkeyPatch):
    service, digest = _make_service(monkeypatch)
    digest.status = STATUS_STEP1
    digest.current_step = STATUS_STEP1
    for idx in range(4):
        service.db.add(
            NewsCandidate(
                digest_id=digest.id,
                original_number=idx + 1,
                title=f"ИИ новость {idx + 1}",
                url=f"https://source{idx + 1}.example.com/news/{idx + 1}",
                source="Example",
                tier="Tier-2",
                published_at="2026-05-14T12:00:00",
                category="technology",
                description="Описание",
                significance_score=2,
                novelty_score=2,
                impact_score=2,
                total_score=6,
                reliability_status="✅ подтверждено",
                link_status=True,
                headline_editorial_ok=True,
                page_verified=True,
            )
        )
    service.db.commit()

    with pytest.raises(HTTPException) as ex:
        service.select_news(digest.id, [], top5=True)

    assert ex.value.status_code == 400
    assert "5" in str(ex.value.detail)


def test_select_news_top5_picks_best_when_many_mandatory_manual(monkeypatch: pytest.MonkeyPatch):
    service, digest = _make_service(monkeypatch)
    digest.status = STATUS_STEP1
    digest.current_step = STATUS_STEP1
    hosts = ["ria.ru", "rbc.ru", "kommersant.ru", "vedomosti.ru", "tass.ru", "interfax.ru", "cnews.ru"]
    for idx in range(12):
        mandatory = idx < 7
        host = hosts[idx % len(hosts)]
        service.db.add(
            NewsCandidate(
                digest_id=digest.id,
                original_number=idx + 1,
                title=f"Новость {idx}",
                url=f"https://{host}/2026052{idx}/story",
                source=host,
                tier="Tier-1",
                published_at="2026-05-20T12:00:00",
                category="manual" if mandatory else "technology",
                description=(
                    "Вставлено в поле URL на шаге 1; материал обязателен к использованию в выпуске."
                    if mandatory
                    else "Описание"
                ),
                significance_score=2,
                novelty_score=2,
                impact_score=2,
                total_score=10 - idx,
                reliability_status="✅ подтверждено",
                link_status=True,
                headline_editorial_ok=True,
                page_verified=True,
                verification_comment="MANUAL_REQUIRED: добавлено пользователем" if mandatory else "",
            )
        )
    service.db.commit()

    selected = service.select_news(digest.id, [], top5=True)
    assert len(selected) == 5
    service.db.refresh(digest)
    assert digest.status == STATUS_SELECTED


def test_select_news_rejects_more_than_two_per_host(monkeypatch: pytest.MonkeyPatch):
    service, digest = _make_service(monkeypatch)
    digest.status = STATUS_STEP1
    digest.current_step = STATUS_STEP1
    rows = []
    for idx in range(6):
        host = "ria.ru" if idx < 3 else f"site{idx}.com"
        row = NewsCandidate(
            digest_id=digest.id,
            original_number=idx + 1,
            title=f"Новость {idx}",
            url=f"https://{host}/story{idx}",
            source=host,
            tier="Tier-1",
            published_at="2026-05-20T12:00:00",
            category="technology",
            description="Описание",
            significance_score=2,
            novelty_score=2,
            impact_score=2,
            total_score=10 - idx,
            reliability_status="✅ подтверждено",
            link_status=True,
            headline_editorial_ok=True,
            page_verified=True,
        )
        service.db.add(row)
        rows.append(row)
    service.db.commit()
    pick_ids = [rows[0].id, rows[1].id, rows[2].id, rows[3].id, rows[4].id]
    with pytest.raises(HTTPException) as ex:
        service.select_news(digest.id, pick_ids, top5=False)
    assert ex.value.status_code == 400
    assert "ria.ru" in str(ex.value.detail)


def test_select_news_does_not_auto_run_step3(monkeypatch: pytest.MonkeyPatch):
    """select_news только сохраняет пятёрку (status=selected), step 3 запускается после order."""
    service, digest = _make_service(monkeypatch)
    service.settings.auto_run_step3_after_order = True
    calls: list[int] = []
    monkeypatch.setattr(
        service,
        "_run_step3_after_order",
        lambda digest_id: calls.append(digest_id) or {"items": []},
    )
    digest.status = STATUS_STEP1
    digest.current_step = STATUS_STEP1
    for idx in range(10):
        service.db.add(
            NewsCandidate(
                digest_id=digest.id,
                original_number=idx + 1,
                title=f"Новость {idx}",
                url=f"https://habr.com/ru/news/{idx + 1}/",
                source="habr.com",
                tier="Tier-2",
                published_at="2026-05-20T12:00:00",
                category="search",
                description="Описание",
                significance_score=2,
                novelty_score=2,
                impact_score=2,
                total_score=10 - idx,
                reliability_status="✅ подтверждено",
                link_status=True,
                headline_editorial_ok=True,
                page_verified=True,
            )
        )
    service.db.commit()
    pick_ids = [c.id for c in service.db.query(NewsCandidate).filter(NewsCandidate.digest_id == digest.id).limit(5)]

    service.select_news(digest.id, pick_ids, top5=False)

    assert calls == [], "select_news НЕ должен запускать step 3 — только order"
    service.db.refresh(digest)
    assert digest.status == STATUS_SELECTED


def test_ai_optimal_order_does_not_auto_run_step3(monkeypatch: pytest.MonkeyPatch):
    """AI-оптимизация меняет только порядок шага 2; шаг 3 не должен стартовать сам."""
    service, digest = _make_service(monkeypatch)
    service.settings.auto_run_step3_after_order = True
    digest.status = STATUS_SELECTED
    digest.current_step = STATUS_SELECTED
    calls: list[int] = []
    monkeypatch.setattr(
        service,
        "_run_step3_after_order",
        lambda digest_id: calls.append(digest_id) or {"items": []},
    )

    candidate_ids: list[int] = []
    for idx in range(5):
        row = NewsCandidate(
            digest_id=digest.id,
            original_number=idx + 1,
            title=f"Новость {idx + 1}",
            url=f"https://source{idx + 1}.example.com/news/{idx + 1}",
            source=f"source{idx + 1}.example.com",
            tier="Tier-2",
            published_at="2026-05-20T12:00:00",
            category="search",
            description="Описание",
            significance_score=2,
            novelty_score=2,
            impact_score=2,
            total_score=10 - idx,
            reliability_status="✅ подтверждено",
            link_status=True,
            headline_editorial_ok=True,
            page_verified=True,
        )
        service.db.add(row)
        service.db.flush()
        candidate_ids.append(row.id)
        service.db.add(
            SelectedNews(
                digest_id=digest.id,
                candidate_id=row.id,
                original_number=row.original_number,
                output_position=idx + 1,
                ordering_reason="old",
            )
        )
    service.db.commit()

    monkeypatch.setattr(
        service.proxy,
        "suggest_news_order",
        lambda order_input, digest_type, model: {
            "items": [
                {
                    "candidate_id": row["candidate_id"],
                    "output_position": pos + 1,
                    "ordering_reason": f"Причина {pos + 1}",
                }
                for pos, row in enumerate(reversed(order_input))
            ],
            "overall_rationale": "Сильный заход, затем развитие и ясный финал.",
        },
    )

    ordered = service.run_step_2_order_ai_optimal(digest.id)
    service.db.refresh(digest)

    assert calls == []
    assert digest.status == STATUS_SELECTED
    assert [row.candidate_id for row in ordered] == list(reversed(candidate_ids))
    assert service.load_step2_order_rationale(digest.id).startswith("Сильный заход")


def test_step2_add_manual_url_appends_to_pool(monkeypatch: pytest.MonkeyPatch):
    service, digest = _make_service(monkeypatch)
    digest.status = STATUS_STEP1
    digest.current_step = STATUS_STEP1
    service.db.add(
        NewsCandidate(
            digest_id=digest.id,
            original_number=1,
            title="Из поиска",
            url="https://ria.ru/article/1",
            source="ria.ru",
            tier="Tier-1",
            published_at="2026-05-13T12:00:00",
            category="search",
            description="Web-поиск",
            significance_score=2,
            novelty_score=2,
            impact_score=2,
            total_score=8,
            reliability_status="✅ подтверждено",
            link_status=True,
            headline_editorial_ok=True,
            page_verified=True,
        )
    )
    service.db.commit()

    manual_url = "https://habr.com/ru/news/manual-step2/"
    bundle = {
        "ok": True,
        "is_listing_page": False,
        "final_url": manual_url,
        "display_url": manual_url,
        "article_markers": True,
        "soft_article_signals": True,
        "headline": "Нейросеть и искусственный интеллект: ручная ссылка",
        "topic_corpus": "искусственный интеллект нейросети",
        "headline_strict": True,
        "published_at": "2026-05-13T12:00:00",
    }
    monkeypatch.setattr(ds, "_fetch_article_page_bundle", lambda url: bundle)

    result = service.add_manual_urls_to_pool(digest.id, [manual_url])

    assert len(result["added"]) == 1
    assert result["pool_count"] == 2
    row = service.db.query(NewsCandidate).filter(NewsCandidate.id == result["added"][0]["id"]).one()
    assert "шаге 2" in row.description
    assert row.page_verified
    assert service._is_manual_required_candidate(row.verification_comment, row.description)
    assert service.db.query(NewsCandidate).filter(NewsCandidate.digest_id == digest.id).count() == 2

    dup = service.add_manual_urls_to_pool(digest.id, [manual_url])
    assert dup["added"] == []
    assert manual_url in dup["skipped_duplicates"]


def test_select_news_manual_picks_ignore_legacy_telegram_manual_required(monkeypatch: pytest.MonkeyPatch):
    service, digest = _make_service(monkeypatch)
    digest.status = STATUS_STEP1
    digest.current_step = STATUS_STEP1
    candidates: list[NewsCandidate] = []
    for idx in range(10):
        is_legacy_tg = idx < 3
        c = NewsCandidate(
            digest_id=digest.id,
            original_number=idx + 1,
            title=f"Кандидат {idx + 1}",
            url=f"https://habr.com/ru/news/{idx + 1}/",
            source="habr.com",
            tier="Tier-2",
            published_at="2026-05-20T12:00:00",
            category="manual" if is_legacy_tg else "search",
            description="Старый telegram-seed без поля URL" if is_legacy_tg else "Web-поиск",
            significance_score=2,
            novelty_score=2,
            impact_score=2,
            total_score=10 - idx,
            reliability_status="✅ подтверждено",
            link_status=True,
            headline_editorial_ok=True,
            page_verified=True,
            verification_comment="MANUAL_REQUIRED: добавлено пользователем" if is_legacy_tg else "",
        )
        candidates.append(c)
        service.db.add(c)
    service.db.commit()
    pick_ids = [c.id for c in candidates[3:8]]

    selected = service.select_news(digest.id, pick_ids, top5=False)

    assert len(selected) == 5
    service.db.refresh(digest)
    assert digest.status == STATUS_SELECTED


def test_step1_discovered_feedback_saved(monkeypatch: pytest.MonkeyPatch):
    service, digest = _make_service(monkeypatch)

    score_rows = [{"title": f"Candidate {i}", "url": f"https://source{i}.example.com/news/{i}"} for i in range(10)]
    monkeypatch.setattr(service.workflow, "run_candidates_score", lambda verify_rows, now_msk, **kw: score_rows)

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

    verify_rows = [{"original_number": i + 1, "title": f"Candidate {i}", "url": f"https://source{i}.example.com/news/{i}"} for i in range(10)]
    score_rows = [dict(x) for x in verify_rows]
    score_rows[-1]["url"] = "https://broken.example.net/new-path"
    score_rows[-1]["total_score"] = 99
    score_rows[-1]["reliability_status"] = "⚠️ сомнительный"

    monkeypatch.setattr(service.workflow, "run_candidates_research", lambda digest_type, now_msk, manual_urls: verify_rows)
    monkeypatch.setattr(service.workflow, "run_candidates_verify", lambda research_rows: verify_rows)
    monkeypatch.setattr(service.workflow, "run_candidates_score", lambda verify_rows, now_msk, **kw: score_rows)

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
    assert len(rows) == 10
    assert rows[-1].url.endswith("/news/9")
    assert "broken.example.net" not in rows[-1].url
    assert rows[-1].reliability_status == "✅ подтверждено"


def test_step1_partial_rebuild_keeps_marked_candidates(monkeypatch: pytest.MonkeyPatch):
    service, digest = _make_service(monkeypatch)

    score_rows = [{"title": f"Candidate {i}", "url": f"https://source{i}.example.com/news/{i}"} for i in range(10)]
    monkeypatch.setattr(service.workflow, "run_candidates_score", lambda verify_rows, now_msk, **kw: score_rows)

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

    first = service.run_step_1(digest.id, [])
    assert len(first) == 10
    keep_ids = [first[0].id, first[1].id, first[2].id]
    keep_urls = {first[0].url, first[1].url, first[2].url}
    dropped_urls = {c.url for c in first[3:]}

    new_score_rows = [
        {"original_number": i + 1, "title": f"Fresh {i}", "url": f"https://fresh{i}.example.com/news/{i}"}
        for i in range(10)
    ]
    monkeypatch.setattr(service.workflow, "run_candidates_research", lambda digest_type, now_msk, manual_urls: new_score_rows)
    monkeypatch.setattr(service.workflow, "run_candidates_verify", lambda research_rows: [dict(x) for x in research_rows])
    monkeypatch.setattr(
        service.workflow,
        "run_candidates_score",
        lambda verify_rows, now_msk, **kw: [dict(x) for x in verify_rows],
    )

    rows = service.run_step_1(digest.id, [], rebuild=True, keep_candidate_ids=keep_ids)
    assert 10 <= len(rows) <= 15
    result_urls = {r.url for r in rows}
    assert keep_urls.issubset(result_urls)
    assert not dropped_urls.intersection(result_urls), "невыбранные из старого пула не должны вернуться"
    assert any("fresh" in u and "example.com" in u for u in result_urls)


def test_step1_partial_rebuild_accepts_short_pool_with_new_candidates(monkeypatch: pytest.MonkeyPatch):
    service, digest = _make_service(monkeypatch)

    score_rows = [{"title": f"Candidate {i}", "url": f"https://source{i}.example.com/news/{i}"} for i in range(10)]
    monkeypatch.setattr(service.workflow, "run_candidates_score", lambda verify_rows, now_msk, **kw: score_rows)

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

    first = service.run_step_1(digest.id, [])
    keep_ids = [first[0].id, first[1].id, first[2].id]
    keep_urls = {first[0].url, first[1].url, first[2].url}
    dropped_urls = {c.url for c in first[3:]}

    fresh_rows = [
        {"original_number": i + 1, "title": f"Fresh {i}", "url": f"https://fresh{i}.example.com/news/{i}"}
        for i in range(3)
    ]
    monkeypatch.setattr(service.workflow, "run_candidates_research", lambda digest_type, now_msk, manual_urls: fresh_rows)
    monkeypatch.setattr(service.workflow, "run_candidates_verify", lambda research_rows: [dict(x) for x in research_rows])
    monkeypatch.setattr(
        service.workflow,
        "run_candidates_score",
        lambda verify_rows, now_msk, **kw: [dict(x) for x in verify_rows],
    )

    rows = service.run_step_1(digest.id, [], rebuild=True, keep_candidate_ids=keep_ids)

    result_urls = {r.url for r in rows}
    assert len(rows) == 6
    assert keep_urls.issubset(result_urls)
    assert any("fresh" in u and "example.com" in u for u in result_urls)
    assert not dropped_urls.intersection(result_urls)


def test_step1_rebuild_from_selected_clears_downstream(monkeypatch: pytest.MonkeyPatch):
    service, digest = _make_service(monkeypatch)

    score_rows = [{"title": f"Candidate {i}", "url": f"https://source{i}.example.com/news/{i}"} for i in range(10)]
    monkeypatch.setattr(service.workflow, "run_candidates_score", lambda verify_rows, now_msk, **kw: score_rows)

    def fake_verify(_digest_id: int, item: dict, **_kwargs) -> None:
        item["headline_editorial_ok"] = True
        item["link_status"] = True
        item["is_aggregator"] = False
        item["verification_comment"] = ""
        item["title"] = f"ИИ: {item.get('title', 'Новость')}"
        item["source"] = "Example"
        item["published_at"] = "2026-05-14T09:00:00+03:00"
        item["category"] = "technology"
        item["description"] = "Описание"
        item["significance_score"] = 2
        item["novelty_score"] = 2
        item["impact_score"] = 2
        item["total_score"] = 6
        item["reliability_status"] = "✅ подтверждено"

    monkeypatch.setattr(service, "_verify_llm_candidate_dict", fake_verify)

    first = service.run_step_1(digest.id, [])
    assert len(first) >= 10
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

    fresh_rows = [
        {"original_number": i + 1, "title": f"Fresh {i}", "url": f"https://fresh{i}.example.com/news/{i}"}
        for i in range(10)
    ]
    monkeypatch.setattr(service.workflow, "run_candidates_research", lambda digest_type, now_msk, manual_urls: fresh_rows)
    monkeypatch.setattr(service.workflow, "run_candidates_verify", lambda research_rows: [dict(x) for x in research_rows])
    monkeypatch.setattr(
        service.workflow,
        "run_candidates_score",
        lambda verify_rows, now_msk, **kw: [dict(x) for x in verify_rows],
    )

    rows = service.run_step_1(digest.id, [], rebuild=True)
    service.db.refresh(digest)
    assert digest.status == STATUS_STEP1
    assert service.db.query(SelectedNews).filter(SelectedNews.digest_id == digest.id).count() == 0
    assert service.db.query(Analytics).filter(Analytics.digest_id == digest.id).count() == 0
    assert service.db.query(NewsCandidate).filter(NewsCandidate.digest_id == digest.id).count() >= 10
    assert len(rows) >= 10
    old_urls = {f"https://source{i}.example.com/news/{i}" for i in range(10)}
    assert not old_urls.intersection({r.url for r in rows})


def test_step1_full_rebuild_excludes_entire_previous_pool(monkeypatch: pytest.MonkeyPatch):
    service, digest = _make_service(monkeypatch)

    score_rows = [{"title": f"Candidate {i}", "url": f"https://source{i}.example.com/news/{i}"} for i in range(10)]
    monkeypatch.setattr(service.workflow, "run_candidates_score", lambda verify_rows, now_msk, **kw: score_rows)

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

    first = service.run_step_1(digest.id, [])
    assert len(first) == 10
    old_urls = {c.url for c in first}

    fresh_rows = [
        {"original_number": i + 1, "title": f"Fresh {i}", "url": f"https://fresh{i}.example.com/news/{i}"}
        for i in range(10)
    ]
    monkeypatch.setattr(service.workflow, "run_candidates_research", lambda digest_type, now_msk, manual_urls: fresh_rows)
    monkeypatch.setattr(service.workflow, "run_candidates_verify", lambda research_rows: [dict(x) for x in research_rows])
    monkeypatch.setattr(
        service.workflow,
        "run_candidates_score",
        lambda verify_rows, now_msk, **kw: [dict(x) for x in verify_rows],
    )

    rows = service.run_step_1(digest.id, [], rebuild=True)
    assert not old_urls.intersection({r.url for r in rows})


def test_step1_fails_if_rebalance_drops_pool_below_ten(monkeypatch: pytest.MonkeyPatch):
    """Если после квот осталось <10 кандидатов, шаг 1 возвращает 502 и не сохраняет укороченный пул."""
    service, digest = _make_service(monkeypatch)

    score_rows: list[dict[str, str]] = []
    for i in range(16):
        host_no = (i % 3) + 1
        score_rows.append(
            {
                "original_number": i + 1,
                "title": f"Candidate {i}",
                "url": f"https://host{host_no}.example.com/news/{i}",
            }
        )
    monkeypatch.setattr(
        service.workflow,
        "run_candidates_research",
        lambda digest_type, now_msk, manual_urls: score_rows,
    )
    monkeypatch.setattr(service.workflow, "run_candidates_verify", lambda research_rows: [dict(x) for x in research_rows])
    monkeypatch.setattr(
        service.workflow,
        "run_candidates_score",
        lambda verify_rows, now_msk, **kw: [dict(x) for x in verify_rows],
    )

    def fake_verify(_digest_id: int, item: dict, **_kwargs) -> None:
        item["headline_editorial_ok"] = True
        item["link_status"] = True
        item["is_aggregator"] = False
        item["verification_comment"] = ""
        item["title"] = f"ИИ: {item.get('title', 'Новость')}"
        item["source"] = "Example"
        item["published_at"] = "2026-05-12T10:00:00+03:00"
        item["category"] = "technology"
        item["description"] = "Описание"
        item["significance_score"] = 2
        item["novelty_score"] = 2
        item["impact_score"] = 2
        item["total_score"] = 6
        item["reliability_status"] = "✅ подтверждено"

    monkeypatch.setattr(service, "_verify_llm_candidate_dict", fake_verify)

    with pytest.raises(HTTPException) as ex:
        service.run_step_1(digest.id, [])
    assert ex.value.status_code == 502
    assert "в пул с квотами вошло только" in str(ex.value.detail)


def test_step1_rebuild_from_analytics_ready(monkeypatch: pytest.MonkeyPatch):
    service, digest = _make_service(monkeypatch)
    digest.status = STATUS_ANALYTICS
    digest.current_step = STATUS_ANALYTICS
    service.db.commit()

    score_rows = [{"title": f"Candidate {i}", "url": f"https://source{i}.example.com/news/{i}"} for i in range(10)]
    monkeypatch.setattr(service.workflow, "run_candidates_score", lambda verify_rows, now_msk, **kw: score_rows)

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
    assert len(rows) >= 10


def test_rebalance_pool_respects_source_ru_and_press_shares():
    pool = [
        {"url": "https://example.com/news/1", "source": "example.com", "total_score": 9, "tier": "Tier-2"},
        {"url": "https://example.com/news/2", "source": "example.com", "total_score": 8, "tier": "Tier-2"},
        {"url": "https://example.com/news/3", "source": "example.com", "total_score": 7, "tier": "Tier-2"},
        {"url": "https://tass.ru/ai/1", "source": "tass.ru", "total_score": 9, "tier": "Tier-1"},
        {"url": "https://rbc.ru/technology/ai-1", "source": "rbc.ru", "total_score": 8, "tier": "Tier-1"},
        {"url": "https://cnews.ru/news/top/ai-1", "source": "cnews.ru", "total_score": 7, "tier": "Tier-2"},
        {"url": "https://company-a.com/press/ai-platform", "source": "company-a.com", "total_score": 9, "tier": "Tier-3"},
        {"url": "https://company-b.com/newsroom/ai-case", "source": "company-b.com", "total_score": 8, "tier": "Tier-3"},
        {"url": "https://www.prnewswire.com/news-releases/ai-case-1.html", "source": "prnewswire.com", "total_score": 8, "tier": "Tier-2"},
        {"url": "https://globenewswire.com/news-release/ai-case-2", "source": "globenewswire.com", "total_score": 7, "tier": "Tier-2"},
        {"url": "https://theverge.com/ai/story-1", "source": "theverge.com", "total_score": 7, "tier": "Tier-2"},
        {"url": "https://venturebeat.com/ai/story-2", "source": "venturebeat.com", "total_score": 7, "tier": "Tier-2"},
    ]

    out = ds._rebalance_verified_pool(pool, target=10)
    assert len(out) == 10

    by_host: dict[str, int] = {}
    ru_count = 0
    press_count = 0
    for item in out:
        host = ds._publisher_host_key(item)
        by_host[host] = by_host.get(host, 0) + 1
        if ds._is_russian_host(ds._host_from_url(str(item.get("url") or ""))):
            ru_count += 1
        if ds._is_press_release_candidate_dict(item):
            press_count += 1

    assert max(by_host.values()) <= 2
    assert 3 <= ru_count <= 5
    assert 2 <= press_count <= 4


def test_classify_source_policy_marks_tier5_forbidden_media():
    tier, is_aggregator, reliability = ds._classify_source_policy("https://meduza.io/feature/ai-case")
    assert tier == "Tier-5"
    assert is_aggregator is False
    assert reliability == "❗ без подтверждения"


def test_rebalance_caps_by_url_host_when_source_labels_differ():
    pool = []
    for src in ("cisoclub.ru", "CISO Club", "CISOCLUB", "cisoclub"):
        for i in range(4):
            pool.append(
                {
                    "url": f"https://cisoclub.ru/news/{src}-{i}",
                    "source": src,
                    "total_score": 9,
                    "tier": "Tier-2",
                }
            )
    for i in range(6):
        pool.append({"url": f"https://other{i}.example.com/n", "source": f"other{i}.example.com", "total_score": 7})
    out = ds._rebalance_verified_pool(pool, target=10)
    by_host: dict[str, int] = {}
    for item in out:
        host = ds._publisher_host_key(item)
        by_host[host] = by_host.get(host, 0) + 1
    assert by_host.get("cisoclub.ru", 0) <= 2
    assert max(by_host.values()) <= 2


def test_rebalance_host_cap_fallback_fills_when_press_quota_blocks():
    pool = []
    for i in range(12):
        pool.append(
            {
                "url": f"https://press{i}.example.com/press-release/ai-investment-plan-{i}.html",
                "source": f"press{i}.example.com",
                "title": f"Компания объявила план инвестиций в ИИ {i}",
                "description": "инвестиции партнёрство регулирование прорыв million",
                "total_score": 9,
                "tier": "Tier-2",
            }
        )
    out = ds._rebalance_verified_pool(pool, target=10)
    assert len(out) == 10
    assert max(ds._pool_host_counts(out).values()) <= 2


def test_rebalance_does_not_break_source_cap_even_if_pool_small():
    pool = []
    for i in range(6):
        pool.append({"url": f"https://cisoclub.ru/news/{i}", "source": "cisoclub.ru", "total_score": 8, "tier": "Tier-3"})
    for i in range(4):
        pool.append({"url": f"https://neuro-ai.ru/news/{i}", "source": "neuro-ai.ru", "total_score": 7, "tier": "Tier-3"})

    out = ds._rebalance_verified_pool(pool, target=10)
    by_host: dict[str, int] = {}
    for item in out:
        host = ds._publisher_host_key(item)
        by_host[host] = by_host.get(host, 0) + 1
    assert max(by_host.values()) <= 2
    assert len(out) == 4


def test_step1_requires_minimum_discovered_pages_before_pool(monkeypatch: pytest.MonkeyPatch):
    service, digest = _make_service(monkeypatch)
    from app.services.step1_filter_settings import load_step1_filter_settings, save_step1_filter_settings

    cfg = load_step1_filter_settings("serious")
    cfg["min_discovered_pages"] = 20
    save_step1_filter_settings(cfg, digest_type="serious")

    rows = []
    for i in range(19):
        rows.append(
            {
                "original_number": i + 1,
                "title": f"ИИ новость {i + 1}",
                "url": f"https://news{i}.example.com/ai/article-{i + 1}",
                "source": f"news{i}.example.com",
                "headline_editorial_ok": True,
                "link_status": True,
                "page_verified": True,
                "is_aggregator": False,
            }
        )

    monkeypatch.setattr(service, "_collect_search_verified_candidates", lambda *args, **kwargs: (rows, {}))
    monkeypatch.setattr(service.workflow, "run_candidates_research", lambda *args, **kwargs: [])
    monkeypatch.setattr(service.workflow, "run_candidates_verify", lambda rows: rows)
    monkeypatch.setattr(service.workflow, "run_candidates_score", lambda verify_rows, now_msk, **kw: verify_rows)

    with pytest.raises(HTTPException) as ex:
        service.run_step_1(digest.id, [])

    assert ex.value.status_code == 502
    assert "Найдено конкретных проверенных страниц 19 из требуемых 20" in str(ex.value.detail)


def test_repair_orphan_step1_status_after_failed_rebuild(monkeypatch: pytest.MonkeyPatch):
    service, digest = _make_service(monkeypatch)
    digest.status = STATUS_ANALYTICS
    digest.current_step = STATUS_ANALYTICS
    service.db.add(
        NewsCandidate(
            digest_id=digest.id,
            original_number=1,
            title="Новость",
            url="https://habr.com/ru/news/1/",
            source="habr.com",
            tier="Tier-2",
            published_at="2026-05-20T12:00:00",
            category="search",
            description="Описание",
            significance_score=2,
            novelty_score=2,
            impact_score=2,
            total_score=6,
            reliability_status="✅ подтверждено",
            link_status=True,
            headline_editorial_ok=True,
            page_verified=True,
        )
    )
    service.db.commit()

    repaired = service.get_digest(digest.id)

    assert repaired.status == STATUS_STEP1
    assert repaired.current_step == STATUS_STEP1
