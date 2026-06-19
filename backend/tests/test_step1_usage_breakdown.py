from app.services.step1_phase_timers import reset_step1_phase_timers
from app.services.step1_usage_breakdown import build_step1_usage_breakdown, resolve_step1_proxyapi_cost_rub


def test_phase_timers_merge_into_meta():
    timers = reset_step1_phase_timers()
    timers.add("web_search", 2.0)
    timers.add("http_verify", 12.5)
    meta: dict = {}
    timers.merge_into(meta)
    assert meta["phase_sec"]["web_search"] == 2
    assert meta["phase_sec"]["http_verify"] == 12


def test_build_usage_breakdown_from_meta_and_costs():
    meta = {
        "elapsed_sec": 600,
        "iterations": 3,
        "stop_reason": "target_min_met",
        "verified_total": 10,
        "batch_size": 20,
        "collection_target_pages": 15,
        "urls_raw_unique": 80,
        "urls_prefilter_rejected": 25,
        "urls_sent_to_http": 40,
        "web_search_api_calls": 12,
        "web_search_cache_hits": 4,
        "web_search_citation_urls": 30,
        "phase_sec": {"http_verify": 480, "web_search": 90, "other": 30},
        "conversion_e2e_pct": 12.5,
        "conversion_http_pct": 25.0,
    }
    costs = [
        {"request_label": "step_1_collect_pool", "cost_rub": 41.5},
    ]
    usage = build_step1_usage_breakdown(meta, step1_costs=costs, step1_total_rub=41.5)
    assert usage is not None
    assert usage["total_time_sec"] == 600
    assert usage["total_cost_rub"] == 41.5
    assert usage["cost_source"] in {"records", "estimate", "balance"}
    tool_ids = {t["id"] for t in usage["tools"]}
    assert "http_verify" in tool_ids
    assert "web_search" in tool_ids
    ws = next(t for t in usage["tools"] if t["id"] == "web_search")
    assert ws["calls"] == 12
    assert "кэш 4" in (ws.get("detail") or "")


def test_usage_breakdown_uses_web_search_estimate_when_no_records():
    meta = {
        "elapsed_sec": 600,
        "iterations": 2,
        "verified_total": 0,
        "web_search_api_calls": 43,
        "web_search_response_calls": 43,
        "web_search_service_cost_est_rub": 43.0,
        "web_search_token_cost_est_rub": 15.5,
        "web_search_cost_est_rub": 58.5,
    }
    total, source = resolve_step1_proxyapi_cost_rub(meta, step1_total_rub=0.0, last_run_cost_rub=0.0, step1_costs=[])
    assert total == 58.5
    assert source == "estimate"
    usage = build_step1_usage_breakdown(meta, step1_costs=[], step1_total_rub=0.0, last_run_cost_rub=0.0)
    assert usage is not None
    assert usage["total_cost_rub"] == 58.5
    ws = next(t for t in usage["tools"] if t["id"] == "web_search")
    assert ws["cost_rub"] == 58.5
    assert "service 43" in (ws.get("detail") or "")
