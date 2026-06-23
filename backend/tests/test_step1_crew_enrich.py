"""Crew enrich (вариант B): оценка уже HTTP-проверенного пула без поиска URL."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.services.digest_service import DigestService


def _verified_item(url: str, *, total: int = 6) -> dict:
    return {
        "url": url,
        "title": "Новость про ИИ",
        "source": "example.com",
        "tier": "Tier-2",
        "published_at": "2026-06-01",
        "category": "technology",
        "description": "Кратко",
        "significance_score": 2,
        "novelty_score": 2,
        "impact_score": 2,
        "total_score": total,
        "reliability_status": "✅",
        "link_status": True,
        "headline_editorial_ok": True,
        "page_verified": True,
    }


def test_crew_enrich_disabled_by_default():
    svc = DigestService.__new__(DigestService)
    svc.settings = SimpleNamespace(
        step1_crew_enrich_verified_scores=False,
        step1_crew_enrich_min_verified=1,
        step1_crew_enrich_max_items=12,
    )
    pool = [_verified_item("https://news.example.com/a")]
    digest = SimpleNamespace(id=1, digest_type="serious")
    out, meta = svc._step1_crew_enrich_verified_pool(digest, pool, "2026-06-16T12:00:00+03:00")
    assert out is pool
    assert meta["skipped"] == "disabled"


def test_crew_enrich_merges_scores_without_changing_url(monkeypatch: pytest.MonkeyPatch):
    svc = DigestService.__new__(DigestService)
    svc.settings = SimpleNamespace(
        step1_crew_enrich_verified_scores=True,
        step1_crew_enrich_min_verified=1,
        step1_crew_enrich_max_items=12,
    )
    url = "https://news.example.com/article-1"
    pool = [_verified_item(url)]
    digest = SimpleNamespace(id=7, digest_type="serious")

    def _fake_enrich(rows, now_msk, *, digest_type="serious"):
        row = dict(rows[0])
        row["significance_score"] = 3
        row["novelty_score"] = 3
        row["impact_score"] = 3
        row["total_score"] = 9
        row["description"] = "Редакционное описание от Crew"
        row["url"] = "https://evil.example.com/hallucinated"
        return [row]

    svc.workflow = MagicMock()
    svc.workflow.run_verified_pool_score_enrichment.side_effect = _fake_enrich

    out, meta = svc._step1_crew_enrich_verified_pool(digest, pool, "2026-06-16T12:00:00+03:00")
    assert meta["enabled"] is True
    assert meta["items_enriched"] == 1
    assert out[0]["url"] == url
    assert out[0]["total_score"] == 9
    assert out[0]["description"] == "Редакционное описание от Crew"
    svc.workflow.run_verified_pool_score_enrichment.assert_called_once()


def test_crew_enrich_curious_keeps_tone_total(monkeypatch: pytest.MonkeyPatch):
    svc = DigestService.__new__(DigestService)
    svc.settings = SimpleNamespace(
        step1_crew_enrich_verified_scores=True,
        step1_crew_enrich_min_verified=1,
        step1_crew_enrich_max_items=12,
    )
    url = "https://fun.example.com/story"
    item = _verified_item(url, total=6)
    item["curious_tone_score"] = 3
    item["curious_tone_low"] = False
    digest = SimpleNamespace(id=8, digest_type="curious")

    def _fake_enrich(rows, now_msk, *, digest_type="curious"):
        row = dict(rows[0])
        row["total_score"] = 9
        row["description"] = "Funny story"
        return [row]

    svc.workflow = MagicMock()
    svc.workflow.run_verified_pool_score_enrichment.side_effect = _fake_enrich

    out, meta = svc._step1_crew_enrich_verified_pool(digest, [item], "2026-06-16T12:00:00+03:00")
    assert meta["items_enriched"] == 1
    assert out[0]["description"] == "Funny story"
    assert out[0]["total_score"] == 7


def test_pipeline_config_enables_crew_enrich():
    from app.pipeline_settings import read_pipeline_config

    cfg = read_pipeline_config()["step1"]
    assert cfg["crew_enrich_verified_scores"] is True
    assert cfg["crew_enrich_max_items"] == 12
