"""Онлайн-снимок прогресса шага 1."""

from __future__ import annotations

import time

from app.services import step1_live_progress as live


def teardown_function() -> None:
    for did in list(live._LIVE.keys()):
        live.end_live_progress(did)


def test_begin_sync_snapshot_and_end():
    live.begin_live_progress(42, collection_target=20)
    live.touch_live_phase(42, "web_search")

    meta = {
        "iterations": 2,
        "urls_raw_unique": 55,
        "urls_raw_merged": 50,
        "urls_prefilter_rejected": 12,
        "urls_sent_to_http": 38,
        "verified_total": 7,
        "web_search_api_calls": 9,
        "web_search_citation_urls": 3,
        "web_search_cost_est_rub": 1.25,
        "collection_target_pages": 20,
    }
    live.sync_live_progress(42, meta=meta, reject_total=31, iteration_no=2)
    for _ in range(28):
        live.record_link_rejected(42)
    for _ in range(3):
        live.record_link_accepted_to_pool(42)

    snap = live.snapshot_live_progress(42)
    assert snap is not None
    assert snap["running"] is True
    assert snap["phase_key"] == "web_search"
    assert snap["phase"] == "Веб-поиск (ProxyAPI)"
    assert snap["iteration"] == 2
    assert snap["urls_raw"] == 55
    assert snap["urls_sent_to_http"] == 38
    assert snap["verified_pool"] == 7
    assert snap["rejected_links"] == 28
    assert snap["reject_reason_events"] == 31
    assert snap["links_checked"] == 31
    assert snap["web_search_api_calls"] == 9
    assert snap["collection_target"] == 20
    assert snap["elapsed_sec"] >= 0

    live.finish_live_progress(42)
    snap = live.snapshot_live_progress(42)
    assert snap is not None
    assert snap["running"] is False
    assert snap["phase"] == "Сбор завершён"
    live.end_live_progress(42)
    assert live.snapshot_live_progress(42) is None


def test_on_step1_phase_enter_uses_bound_digest():
    live.begin_live_progress(7)
    live.bind_live_digest(7)
    try:
        live.on_step1_phase_enter("http_verify")
        snap = live.snapshot_live_progress(7)
        assert snap is not None
        assert snap["phase_key"] == "http_verify"
        assert snap["phase"] == "Проверка страниц по ссылкам"
    finally:
        live.bind_live_digest(None)
        live.end_live_progress(7)


def test_finish_live_progress_keeps_snapshot():
    live.begin_live_progress(88, collection_target=10)
    live.bump_live_progress(88, urls_raw_merged=12, verified_pool=4)
    live.finish_live_progress(88)
    snap = live.snapshot_live_progress(88)
    assert snap is not None
    assert snap["running"] is False
    assert snap["urls_raw"] == 12
    assert snap["verified_pool"] == 4
    live.end_live_progress(88)


def test_mark_cancel_requested():
    live.begin_live_progress(99)
    live.mark_live_cancel_requested(99)
    snap = live.snapshot_live_progress(99)
    assert snap is not None
    assert snap["cancel_requested"] is True


def test_funnel_counters_and_yield():
    live.begin_live_progress(50, collection_target=20)
    live.bump_live_progress(50, pool_carried_over=8, iteration=2)
    live.record_links_found_free(50, count=5)
    live.sync_live_from_web_search_stats(50)
    for _ in range(12):
        live.record_link_rejected(50)
    for _ in range(3):
        live.record_link_accepted_to_pool(50)
    live.bump_live_progress(50, verified_pool=11, rejected_total=12)

    snap = live.snapshot_live_progress(50)
    assert snap is not None
    assert snap["links_found_free"] == 5
    assert snap["pool_carried_over"] == 8
    assert snap["pool_added_this_run"] == 3
    assert snap["rejected_links"] == 12
    assert snap["links_checked"] == 15
    assert snap["pool_yield_pct"] == 20.0
    assert snap["links_found_total"] >= 5

    meta = {
        "iterations": 2,
        "elapsed_sec": 605,
        "verified_total": 10,
        "rejected_total": 48,
        "reject_reason_events": 48,
        "rejected_links": 38,
        "pool_carried_over": 8,
        "pool_added_this_run": 2,
        "links_found_paid": 11,
        "links_found_free": 0,
        "urls_raw_merged": 11,
        "urls_sent_to_http": 7,
        "collection_target_pages": 20,
        "web_search_api_calls": 1,
        "web_search_citation_urls": 11,
        "web_search_cost_est_rub": 12.2,
    }
    payload = live.live_payload_from_meta(meta)
    assert payload["running"] is False
    assert payload["links_found_total"] == 11
    assert payload["links_checked"] == 40
    assert payload["rejected_links"] == 38
    assert payload["pool_yield_pct"] == 5.0
    live.end_live_progress(50)


def test_finalize_live_pool_stats():
    live.begin_live_progress(77, collection_target=20)
    live.bump_live_progress(77, pool_carried_over=3)
    for _ in range(8):
        live.record_link_accepted_to_pool(77)
    live.finalize_live_pool_stats(77, verified_pool=5, pool_carried_over=3, rejected_links=10)
    snap = live.snapshot_live_progress(77)
    assert snap is not None
    assert snap["verified_pool"] == 5
    assert snap["pool_carried_over"] == 3
    assert snap["pool_added_this_run"] == 2
    assert snap["links_checked"] == 12
    live.end_live_progress(77)


def test_bump_live_progress_partial():
    live.begin_live_progress(5, collection_target=12)
    live.bump_live_progress(5, iteration=2, urls_raw_merged=40, verified_pool=3, phase_key="http_verify")
    live.sync_live_from_web_search_stats(5)
    snap = live.snapshot_live_progress(5)
    assert snap is not None
    assert snap["iteration"] == 2
    assert snap["urls_raw"] == 40
    assert snap["verified_pool"] == 3
    assert snap["phase_key"] == "http_verify"
    live.end_live_progress(5)

    live.begin_live_progress(1)
    snap = live.snapshot_live_progress(1)
    assert snap is not None
    assert "с" in snap["elapsed_human"]
    time.sleep(0.01)
    snap2 = live.snapshot_live_progress(1)
    assert snap2 is not None
    assert snap2["elapsed_sec"] >= snap["elapsed_sec"]
