"""Авторазбор доминирующих причин и рекомендации."""

from app.services.step1_statistics_insights import (
    build_dominant_rejects,
    build_recommendations,
    build_step1_insights,
)


def test_dominant_rejects_marks_top_share():
    dominant = build_dominant_rejects(
        {"http_unreachable": 10, "url_mutated_between_agents": 9, "aggregator_source": 1},
    )
    assert dominant[0]["code"] == "http_unreachable"
    assert dominant[0]["is_dominant"] is True
    assert dominant[0]["share_pct"] > 40


def test_recommendations_http_unreachable_and_cap():
    dominant = build_dominant_rejects({"http_unreachable": 8, "url_mutated_between_agents": 5})
    recs = build_recommendations(
        digest_type="serious",
        rejected_summary={"http_unreachable": 8, "url_mutated_between_agents": 5},
        dominant_rejects=dominant,
        meta={"stop_reason": "web_search_api_cap", "web_search_api_cap_hit": True},
        summary={"in_pool": 0, "rejected": 13, "total_links": 20},
        registry_buckets={"raw": 30},
    )
    titles = {r["title"] for r in recs}
    assert any("реестр" in t.lower() or "кэш" in t.lower() for t in titles)
    assert any("http_unreachable" in r["detail"].lower() or "не открылась" in r["detail"].lower() for r in recs)


def test_recommendations_mass_http_unreachable_warns_about_connectivity():
    dominant = build_dominant_rejects({"http_unreachable": 66, "news_listing_page": 10})
    recs = build_recommendations(
        digest_type="serious",
        rejected_summary={"http_unreachable": 66, "news_listing_page": 10},
        dominant_rejects=dominant,
        meta={"stop_reason": "hard_timeout"},
        summary={"in_pool": 0, "rejected": 76, "total_links": 80},
        registry_buckets={},
    )
    assert any("связ" in r["title"].lower() for r in recs)
    assert any("не перезапускайте" in r["title"].lower() or "повторный запуск" in r["detail"].lower() for r in recs)


def test_insights_headline_mentions_dominant():
    payload = build_step1_insights(
        digest_type="curious",
        rejected_summary={"off_topic_not_curious": 12, "http_unreachable": 2},
        step1_collection_meta={"stop_reason": "target_min_met", "verified_total": 3},
        summary={"in_pool": 3, "rejected": 14, "total_links": 17},
        registry_buckets={"raw": 5},
    )
    assert "off_topic_not_curious" in payload["headline"] or "официоз" in payload["headline"].lower()
    assert len(payload["recommendations"]) >= 1
    assert payload["dominant_rejects"][0]["code"] == "off_topic_not_curious"
