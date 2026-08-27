"""Параметры пайплайна (не секреты): backend/app/pipeline_settings.json."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

_PIPELINE_SETTINGS_PATH = Path(__file__).resolve().parent / "pipeline_settings.json"


def pipeline_settings_path() -> Path:
    return _PIPELINE_SETTINGS_PATH


def _bootstrap_pipeline_config() -> dict[str, Any]:
    return {
        "version": 1,
        "web": {"enable_fetch": True},
        "step1": {
            "search_tier1_min_raw_urls": 15,
            "max_cost_rub": 40.0,
            "hard_stop_cost_rub": 100.0,
            "max_web_search_api_calls": 0,
            "web_search_api_bonus_near_target": 10,
            "web_search_context_size": "low",
            "web_search_context_size_supplement": "low",
            "tier_max_web_search_batches": 6,
            "min_urls_before_proxyapi": 5,
            "web_search_prefer_alt_providers": False,
            "web_search_cache_enabled": True,
            "web_search_cache_ttl_days": 90,
            "batch_size": 8,
            "search_fetch_limit": 36,
            "urls_checked_per_collect": 24,
            "soft_time_limit_sec": 90,
            "hard_time_limit_sec": 150,
            "max_candidates_for_ui": 15,
            "verify_workers": 6,
            "crew_fallback_only_if_empty": True,
            "crew_enrich_verified_scores": False,
            "crew_enrich_min_verified": 1,
            "crew_enrich_max_items": 12,
            "tier_strict_search": True,
            "curious_use_serious_tiers": False,
            "serious_use_curious_tiers": True,
            "serious_curious_search_batches": 4,
            "serious_curious_extra_batches": 0,
            "first_offer_min_candidates": 15,
            "telegram_monitor_enabled": True,
            "telegram_monitor_channels": "technokratos",
            "telegram_max_pages": 2,
            "telegram_max_links": 30,
            "telegram_max_digest_posts": 3,
            "telegram_post_text_filter": "Дайджест",
            "telegram_timeout_sec": 10.0,
            "telegram_via_proxyapi": False,
            "telegram_direct_fallback": True,
            "telegram_proxyapi_context_size": "low",
            "seed_urls_max": 35,
            "cheap_sources_first": True,
            "curious_yield": {
                "enabled": True,
                "min_verified": 10,
                "target_pool": 12,
                "soft_time_limit_sec": 480,
                "hard_time_limit_sec": 600,
                "max_cost_rub": 50.0,
                "min_collection_iterations": 2,
                "supplement_rounds_per_iter": 2,
                "max_search_batches": 5,
                "skip_aggregator_search": True,
                "no_progress_limit": 2,
                "seed_listing_scan_limit": 4,
                "entertainment_rescue_queries": 2,
                "rescue_collect_batch": 8,
            },
        },
        "step2": {"max_cost_rub": 50.0},
        "workflow": {"auto_run_step3_after_order": True},
        "step4": {"enable_image_generation": False},
        "logging": {
            "level": "INFO",
            "enable_file": True,
            "file_name": "app.log",
            "max_bytes": 5_000_000,
            "backup_count": 5,
            "step1": {
                "filter_stats_every_n": 50,
                "reject_audit_top_reasons": 5,
                "reject_samples_per_reason": 8,
                "curious_tone": {
                    "enabled": True,
                    "level": "INFO",
                    "separate_file": True,
                    "file_name": "step1-curious-tone.log",
                    "log_accept": True,
                    "log_reject": True,
                    "log_low_tone": True,
                    "max_events_per_run": 200,
                    "title_preview_chars": 120,
                    "corpus_preview_chars": 160,
                    "include_signals": True,
                },
                "logger_levels": {},
            },
        },
    }


def _coerce_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    if value is None:
        return default
    return bool(value)


def normalize_pipeline_config(raw: dict[str, Any] | None) -> dict[str, Any]:
    """Нормализует JSON; возвращает структуру с секциями step1/step2/workflow/step4/logging."""
    base = _bootstrap_pipeline_config()
    if not isinstance(raw, dict):
        return base

    web_raw = raw.get("web") if isinstance(raw.get("web"), dict) else {}
    step1_raw = raw.get("step1") if isinstance(raw.get("step1"), dict) else {}
    step2_raw = raw.get("step2") if isinstance(raw.get("step2"), dict) else {}
    workflow_raw = raw.get("workflow") if isinstance(raw.get("workflow"), dict) else {}
    step4_raw = raw.get("step4") if isinstance(raw.get("step4"), dict) else {}
    logging_raw = raw.get("logging") if isinstance(raw.get("logging"), dict) else {}

    def _int(section: dict[str, Any], key: str, default: int, *, lo: int, hi: int) -> int:
        try:
            val = int(section.get(key, default))
        except (TypeError, ValueError):
            val = default
        return max(lo, min(hi, val))

    def _float(section: dict[str, Any], key: str, default: float, *, lo: float, hi: float) -> float:
        try:
            val = float(section.get(key, default))
        except (TypeError, ValueError):
            val = default
        return max(lo, min(hi, val))

    web = dict(base["web"])
    web["enable_fetch"] = _coerce_bool(web_raw.get("enable_fetch"), web["enable_fetch"])

    step1 = dict(base["step1"])
    step1["search_tier1_min_raw_urls"] = _int(step1_raw, "search_tier1_min_raw_urls", step1["search_tier1_min_raw_urls"], lo=1, hi=100)
    step1["max_cost_rub"] = _float(step1_raw, "max_cost_rub", step1["max_cost_rub"], lo=1.0, hi=10_000.0)
    step1["hard_stop_cost_rub"] = _float(
        step1_raw, "hard_stop_cost_rub", step1["hard_stop_cost_rub"], lo=1.0, hi=10_000.0
    )
    step1["max_web_search_api_calls"] = _int(
        step1_raw, "max_web_search_api_calls", step1.get("max_web_search_api_calls", 0), lo=0, hi=500
    )
    step1["web_search_api_bonus_near_target"] = _int(
        step1_raw,
        "web_search_api_bonus_near_target",
        step1.get("web_search_api_bonus_near_target", 10),
        lo=0,
        hi=100,
    )
    ws_ctx = str(step1_raw.get("web_search_context_size") or step1.get("web_search_context_size", "low")).strip().lower()
    step1["web_search_context_size"] = ws_ctx if ws_ctx in ("low", "medium", "high") else "low"
    ws_sup = str(
        step1_raw.get("web_search_context_size_supplement") or step1.get("web_search_context_size_supplement", "low")
    ).strip().lower()
    step1["web_search_context_size_supplement"] = ws_sup if ws_sup in ("low", "medium", "high") else "low"
    step1["tier_max_web_search_batches"] = _int(
        step1_raw, "tier_max_web_search_batches", step1.get("tier_max_web_search_batches", 6), lo=1, hi=40
    )
    step1["min_urls_before_proxyapi"] = _int(
        step1_raw, "min_urls_before_proxyapi", step1.get("min_urls_before_proxyapi", 5), lo=0, hi=50
    )
    step1["web_search_prefer_alt_providers"] = _coerce_bool(
        step1_raw.get("web_search_prefer_alt_providers"),
        step1.get("web_search_prefer_alt_providers", True),
    )
    step1["web_search_cache_enabled"] = _coerce_bool(
        step1_raw.get("web_search_cache_enabled"),
        step1.get("web_search_cache_enabled", True),
    )
    step1["web_search_cache_ttl_days"] = _int(
        step1_raw,
        "web_search_cache_ttl_days",
        step1.get("web_search_cache_ttl_days", 90),
        lo=1,
        hi=365,
    )
    step1["batch_size"] = _int(step1_raw, "batch_size", step1["batch_size"], lo=1, hi=100)
    step1["search_fetch_limit"] = _int(step1_raw, "search_fetch_limit", step1["search_fetch_limit"], lo=10, hi=500)
    step1["urls_checked_per_collect"] = _int(step1_raw, "urls_checked_per_collect", step1["urls_checked_per_collect"], lo=10, hi=500)
    step1["soft_time_limit_sec"] = _int(step1_raw, "soft_time_limit_sec", step1["soft_time_limit_sec"], lo=30, hi=7200)
    step1["hard_time_limit_sec"] = _int(step1_raw, "hard_time_limit_sec", step1["hard_time_limit_sec"], lo=60, hi=14_400)
    if step1["hard_time_limit_sec"] < step1["soft_time_limit_sec"]:
        step1["hard_time_limit_sec"] = step1["soft_time_limit_sec"]
    step1["max_candidates_for_ui"] = _int(step1_raw, "max_candidates_for_ui", step1["max_candidates_for_ui"], lo=10, hi=30)
    step1["verify_workers"] = _int(step1_raw, "verify_workers", step1["verify_workers"], lo=1, hi=24)
    step1["crew_fallback_only_if_empty"] = _coerce_bool(
        step1_raw.get("crew_fallback_only_if_empty"), step1["crew_fallback_only_if_empty"]
    )
    step1["crew_enrich_verified_scores"] = _coerce_bool(
        step1_raw.get("crew_enrich_verified_scores"), step1["crew_enrich_verified_scores"]
    )
    step1["crew_enrich_min_verified"] = _int(
        step1_raw, "crew_enrich_min_verified", step1["crew_enrich_min_verified"], lo=1, hi=30
    )
    step1["crew_enrich_max_items"] = _int(
        step1_raw, "crew_enrich_max_items", step1["crew_enrich_max_items"], lo=1, hi=30
    )
    step1["tier_strict_search"] = _coerce_bool(step1_raw.get("tier_strict_search"), step1["tier_strict_search"])
    step1["curious_use_serious_tiers"] = _coerce_bool(
        step1_raw.get("curious_use_serious_tiers"),
        step1.get("curious_use_serious_tiers", False),
    )
    step1["serious_use_curious_tiers"] = _coerce_bool(
        step1_raw.get("serious_use_curious_tiers"),
        step1.get("serious_use_curious_tiers", True),
    )
    step1["serious_curious_search_batches"] = _int(
        step1_raw,
        "serious_curious_search_batches",
        step1.get("serious_curious_search_batches", 4),
        lo=1,
        hi=12,
    )
    step1["serious_curious_extra_batches"] = _int(
        step1_raw,
        "serious_curious_extra_batches",
        step1.get("serious_curious_extra_batches", 0),
        lo=0,
        hi=10,
    )
    step1["first_offer_min_candidates"] = _int(
        step1_raw,
        "first_offer_min_candidates",
        step1.get("first_offer_min_candidates", 15),
        lo=10,
        hi=30,
    )
    step1["telegram_monitor_enabled"] = _coerce_bool(
        step1_raw.get("telegram_monitor_enabled"), step1["telegram_monitor_enabled"]
    )
    channels = str(step1_raw.get("telegram_monitor_channels") or step1["telegram_monitor_channels"]).strip()
    step1["telegram_monitor_channels"] = channels or "technokratos"
    step1["telegram_max_pages"] = _int(step1_raw, "telegram_max_pages", step1["telegram_max_pages"], lo=1, hi=10)
    step1["telegram_max_links"] = _int(step1_raw, "telegram_max_links", step1["telegram_max_links"], lo=1, hi=200)
    step1["telegram_max_digest_posts"] = _int(
        step1_raw, "telegram_max_digest_posts", step1["telegram_max_digest_posts"], lo=1, hi=10
    )
    raw_text_filter = step1_raw.get("telegram_post_text_filter")
    if raw_text_filter is None:
        text_filter = str(step1["telegram_post_text_filter"]).strip()
    else:
        # Пустая строка в JSON = отключить текстовый фильтр Telegram-постов.
        text_filter = str(raw_text_filter).strip()
    step1["telegram_post_text_filter"] = text_filter
    step1["telegram_timeout_sec"] = _float(
        step1_raw,
        "telegram_timeout_sec",
        step1["telegram_timeout_sec"],
        lo=1.0,
        hi=30.0,
    )
    step1["telegram_via_proxyapi"] = _coerce_bool(
        step1_raw.get("telegram_via_proxyapi"), step1["telegram_via_proxyapi"]
    )
    step1["telegram_direct_fallback"] = _coerce_bool(
        step1_raw.get("telegram_direct_fallback"), step1["telegram_direct_fallback"]
    )
    ctx = str(step1_raw.get("telegram_proxyapi_context_size") or step1["telegram_proxyapi_context_size"]).strip().lower()
    step1["telegram_proxyapi_context_size"] = ctx if ctx in ("low", "medium", "high") else "high"
    step1["seed_urls_max"] = _int(step1_raw, "seed_urls_max", step1["seed_urls_max"], lo=1, hi=100)
    step1["cheap_sources_first"] = _coerce_bool(
        step1_raw.get("cheap_sources_first"), step1["cheap_sources_first"]
    )

    yield_raw = step1_raw.get("curious_yield") if isinstance(step1_raw.get("curious_yield"), dict) else {}
    yield_cfg = dict(step1.get("curious_yield") or _bootstrap_pipeline_config()["step1"]["curious_yield"])
    yield_cfg["enabled"] = _coerce_bool(yield_raw.get("enabled"), yield_cfg.get("enabled", True))
    yield_cfg["min_verified"] = _int(yield_raw, "min_verified", yield_cfg.get("min_verified", 10), lo=10, hi=30)
    yield_cfg["target_pool"] = _int(yield_raw, "target_pool", yield_cfg.get("target_pool", 12), lo=10, hi=30)
    yield_cfg["soft_time_limit_sec"] = _int(
        yield_raw, "soft_time_limit_sec", yield_cfg.get("soft_time_limit_sec", 480), lo=60, hi=7200
    )
    yield_cfg["hard_time_limit_sec"] = _int(
        yield_raw, "hard_time_limit_sec", yield_cfg.get("hard_time_limit_sec", 600), lo=120, hi=14_400
    )
    if yield_cfg["hard_time_limit_sec"] < yield_cfg["soft_time_limit_sec"]:
        yield_cfg["hard_time_limit_sec"] = yield_cfg["soft_time_limit_sec"]
    yield_cfg["max_cost_rub"] = _float(yield_raw, "max_cost_rub", yield_cfg.get("max_cost_rub", 50.0), lo=1.0, hi=500.0)
    yield_cfg["min_collection_iterations"] = _int(
        yield_raw, "min_collection_iterations", yield_cfg.get("min_collection_iterations", 2), lo=1, hi=20
    )
    yield_cfg["supplement_rounds_per_iter"] = _int(
        yield_raw, "supplement_rounds_per_iter", yield_cfg.get("supplement_rounds_per_iter", 2), lo=0, hi=10
    )
    yield_cfg["max_search_batches"] = _int(
        yield_raw, "max_search_batches", yield_cfg.get("max_search_batches", 5), lo=1, hi=40
    )
    yield_cfg["skip_aggregator_search"] = _coerce_bool(
        yield_raw.get("skip_aggregator_search"), yield_cfg.get("skip_aggregator_search", True)
    )
    yield_cfg["no_progress_limit"] = _int(
        yield_raw, "no_progress_limit", yield_cfg.get("no_progress_limit", 2), lo=1, hi=20
    )
    yield_cfg["seed_listing_scan_limit"] = _int(
        yield_raw, "seed_listing_scan_limit", yield_cfg.get("seed_listing_scan_limit", 4), lo=0, hi=30
    )
    yield_cfg["entertainment_rescue_queries"] = _int(
        yield_raw, "entertainment_rescue_queries", yield_cfg.get("entertainment_rescue_queries", 2), lo=0, hi=5
    )
    yield_cfg["rescue_collect_batch"] = _int(
        yield_raw, "rescue_collect_batch", yield_cfg.get("rescue_collect_batch", 8), lo=4, hi=30
    )
    step1["curious_yield"] = yield_cfg

    step2 = dict(base["step2"])
    step2["max_cost_rub"] = _float(step2_raw, "max_cost_rub", step2["max_cost_rub"], lo=1.0, hi=10_000.0)

    workflow = dict(base["workflow"])
    workflow["auto_run_step3_after_order"] = _coerce_bool(
        workflow_raw.get("auto_run_step3_after_order"), workflow["auto_run_step3_after_order"]
    )

    step4 = dict(base["step4"])
    step4["enable_image_generation"] = _coerce_bool(
        step4_raw.get("enable_image_generation"), step4["enable_image_generation"]
    )

    logging_cfg = dict(base["logging"])
    level = str(logging_raw.get("level") or logging_cfg["level"]).strip().upper()
    logging_cfg["level"] = level if level else "INFO"
    logging_cfg["enable_file"] = _coerce_bool(logging_raw.get("enable_file"), logging_cfg["enable_file"])
    file_name = str(logging_raw.get("file_name") or logging_cfg["file_name"]).strip()
    logging_cfg["file_name"] = file_name or "app.log"
    logging_cfg["max_bytes"] = _int(logging_raw, "max_bytes", logging_cfg["max_bytes"], lo=100_000, hi=100_000_000)
    logging_cfg["backup_count"] = _int(logging_raw, "backup_count", logging_cfg["backup_count"], lo=0, hi=50)

    step1_log_raw = logging_raw.get("step1") if isinstance(logging_raw.get("step1"), dict) else {}
    step1_log = dict(logging_cfg.get("step1") or _bootstrap_pipeline_config()["logging"]["step1"])
    step1_log["filter_stats_every_n"] = _int(
        step1_log_raw, "filter_stats_every_n", step1_log["filter_stats_every_n"], lo=1, hi=500
    )
    step1_log["reject_audit_top_reasons"] = _int(
        step1_log_raw, "reject_audit_top_reasons", step1_log["reject_audit_top_reasons"], lo=1, hi=30
    )
    step1_log["reject_samples_per_reason"] = _int(
        step1_log_raw, "reject_samples_per_reason", step1_log["reject_samples_per_reason"], lo=1, hi=30
    )
    curious_raw = step1_log_raw.get("curious_tone") if isinstance(step1_log_raw.get("curious_tone"), dict) else {}
    curious_log = dict(step1_log["curious_tone"])
    curious_log["enabled"] = _coerce_bool(curious_raw.get("enabled"), curious_log["enabled"])
    curious_level = str(curious_raw.get("level") or curious_log["level"]).strip().upper()
    curious_log["level"] = curious_level if curious_level else "INFO"
    curious_log["separate_file"] = _coerce_bool(curious_raw.get("separate_file"), curious_log["separate_file"])
    curious_file = str(curious_raw.get("file_name") or curious_log["file_name"]).strip()
    curious_log["file_name"] = curious_file or "step1-curious-tone.log"
    curious_log["log_accept"] = _coerce_bool(curious_raw.get("log_accept"), curious_log["log_accept"])
    curious_log["log_reject"] = _coerce_bool(curious_raw.get("log_reject"), curious_log["log_reject"])
    curious_log["log_low_tone"] = _coerce_bool(curious_raw.get("log_low_tone"), curious_log["log_low_tone"])
    curious_log["max_events_per_run"] = _int(
        curious_raw, "max_events_per_run", curious_log["max_events_per_run"], lo=0, hi=5000
    )
    curious_log["title_preview_chars"] = _int(
        curious_raw, "title_preview_chars", curious_log["title_preview_chars"], lo=40, hi=500
    )
    curious_log["corpus_preview_chars"] = _int(
        curious_raw, "corpus_preview_chars", curious_log["corpus_preview_chars"], lo=0, hi=2000
    )
    curious_log["include_signals"] = _coerce_bool(
        curious_raw.get("include_signals"), curious_log["include_signals"]
    )
    step1_log["curious_tone"] = curious_log
    logger_levels_raw = step1_log_raw.get("logger_levels")
    if isinstance(logger_levels_raw, dict):
        step1_log["logger_levels"] = {
            str(k): str(v).strip().upper() for k, v in logger_levels_raw.items() if str(k).strip()
        }
    logging_cfg["step1"] = step1_log

    return {
        "version": int(raw.get("version", base["version"]) or 1),
        "web": web,
        "step1": step1,
        "step2": step2,
        "workflow": workflow,
        "step4": step4,
        "logging": logging_cfg,
    }


def read_pipeline_config(path: Path | None = None) -> dict[str, Any]:
    cfg_path = path or _PIPELINE_SETTINGS_PATH
    raw: dict[str, Any] = {}
    if cfg_path.is_file():
        try:
            loaded = json.loads(cfg_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                raw = loaded
        except (json.JSONDecodeError, OSError):
            raw = {}
    return normalize_pipeline_config(raw)


def write_pipeline_config(config: dict[str, Any], path: Path | None = None) -> dict[str, Any]:
    cfg_path = path or _PIPELINE_SETTINGS_PATH
    normalized = normalize_pipeline_config(config if isinstance(config, dict) else {})
    cfg_path.write_text(json.dumps(normalized, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if cfg_path == _PIPELINE_SETTINGS_PATH:
        get_pipeline_config.cache_clear()
    return normalized


def pipeline_settings_flat(path: Path | None = None) -> dict[str, Any]:
    """Плоский словарь имён полей Settings ← значения из JSON."""
    cfg = read_pipeline_config(path) if path is not None else get_pipeline_config()
    web = cfg["web"]
    s1 = cfg["step1"]
    s2 = cfg["step2"]
    wf = cfg["workflow"]
    s4 = cfg["step4"]
    lg = cfg["logging"]
    step1_log = lg.get("step1") if isinstance(lg.get("step1"), dict) else {}
    curious_log = step1_log.get("curious_tone") if isinstance(step1_log.get("curious_tone"), dict) else {}
    curious_yield = s1.get("curious_yield") if isinstance(s1.get("curious_yield"), dict) else {}
    return {
        "enable_web_fetch": web["enable_fetch"],
        "step1_search_tier1_min_raw_urls": s1["search_tier1_min_raw_urls"],
        "step1_max_cost_rub": s1["max_cost_rub"],
        "step1_hard_stop_cost_rub": s1["hard_stop_cost_rub"],
        "step1_max_web_search_api_calls": s1.get("max_web_search_api_calls", 0),
        "step1_web_search_api_bonus_near_target": s1.get("web_search_api_bonus_near_target", 10),
        "proxyapi_web_search_context_size": s1["web_search_context_size"],
        "proxyapi_web_search_context_size_supplement": s1["web_search_context_size_supplement"],
        "step1_tier_max_web_search_batches": s1["tier_max_web_search_batches"],
        "step1_min_urls_before_proxyapi": s1["min_urls_before_proxyapi"],
        "step1_web_search_prefer_alt_providers": s1["web_search_prefer_alt_providers"],
        "step1_web_search_cache_enabled": s1["web_search_cache_enabled"],
        "step1_web_search_cache_ttl_days": s1["web_search_cache_ttl_days"],
        "step1_batch_size": s1["batch_size"],
        "step1_search_fetch_limit": s1["search_fetch_limit"],
        "step1_urls_checked_per_collect": s1["urls_checked_per_collect"],
        "step1_soft_time_limit_sec": s1["soft_time_limit_sec"],
        "step1_hard_time_limit_sec": s1["hard_time_limit_sec"],
        "step1_max_candidates_for_ui": s1["max_candidates_for_ui"],
        "step1_verify_workers": s1["verify_workers"],
        "step1_crew_fallback_only_if_empty": s1["crew_fallback_only_if_empty"],
        "step1_crew_enrich_verified_scores": s1["crew_enrich_verified_scores"],
        "step1_crew_enrich_min_verified": s1["crew_enrich_min_verified"],
        "step1_crew_enrich_max_items": s1["crew_enrich_max_items"],
        "step1_tier_strict_search": s1["tier_strict_search"],
        "step1_curious_use_serious_tiers": s1["curious_use_serious_tiers"],
        "step1_serious_use_curious_tiers": s1["serious_use_curious_tiers"],
        "step1_serious_curious_search_batches": s1["serious_curious_search_batches"],
        "step1_serious_curious_extra_batches": s1["serious_curious_extra_batches"],
        "step1_first_offer_min_candidates": s1["first_offer_min_candidates"],
        "step1_telegram_monitor_enabled": s1["telegram_monitor_enabled"],
        "step1_telegram_monitor_channels": s1["telegram_monitor_channels"],
        "step1_telegram_max_pages": s1["telegram_max_pages"],
        "step1_telegram_max_links": s1["telegram_max_links"],
        "step1_telegram_max_digest_posts": s1["telegram_max_digest_posts"],
        "step1_telegram_post_text_filter": s1["telegram_post_text_filter"],
        "step1_telegram_timeout_sec": s1["telegram_timeout_sec"],
        "step1_telegram_via_proxyapi": s1["telegram_via_proxyapi"],
        "step1_telegram_direct_fallback": s1["telegram_direct_fallback"],
        "step1_telegram_proxyapi_context_size": s1["telegram_proxyapi_context_size"],
        "step1_seed_urls_max": s1["seed_urls_max"],
        "step1_cheap_sources_first": s1["cheap_sources_first"],
        "step2_max_cost_rub": s2["max_cost_rub"],
        "auto_run_step3_after_order": wf["auto_run_step3_after_order"],
        "enable_step4_image_generation": s4["enable_image_generation"],
        "log_level": lg["level"],
        "log_enable_file": lg["enable_file"],
        "log_file_name": lg["file_name"],
        "log_max_bytes": lg["max_bytes"],
        "log_backup_count": lg["backup_count"],
        "step1_log_filter_stats_every_n": step1_log.get("filter_stats_every_n", 50),
        "step1_log_reject_audit_top_reasons": step1_log.get("reject_audit_top_reasons", 5),
        "step1_log_reject_samples_per_reason": step1_log.get("reject_samples_per_reason", 8),
        "step1_curious_tone_log_enabled": curious_log.get("enabled", True),
        "step1_curious_tone_log_level": curious_log.get("level", "INFO"),
        "step1_curious_tone_log_separate_file": curious_log.get("separate_file", True),
        "step1_curious_tone_log_file_name": curious_log.get("file_name", "step1-curious-tone.log"),
        "step1_curious_tone_log_accept": curious_log.get("log_accept", True),
        "step1_curious_tone_log_reject": curious_log.get("log_reject", True),
        "step1_curious_tone_log_low_tone": curious_log.get("log_low_tone", True),
        "step1_curious_tone_log_max_events": curious_log.get("max_events_per_run", 200),
        "step1_curious_tone_title_preview_chars": curious_log.get("title_preview_chars", 120),
        "step1_curious_tone_corpus_preview_chars": curious_log.get("corpus_preview_chars", 160),
        "step1_curious_tone_include_signals": curious_log.get("include_signals", True),
        "step1_curious_yield_enabled": curious_yield.get("enabled", True),
        "step1_curious_yield_min_verified": curious_yield.get("min_verified", 10),
        "step1_curious_yield_target_pool": curious_yield.get("target_pool", 12),
        "step1_curious_yield_soft_time_sec": curious_yield.get("soft_time_limit_sec", 480),
        "step1_curious_yield_hard_time_sec": curious_yield.get("hard_time_limit_sec", 600),
        "step1_curious_yield_max_cost_rub": curious_yield.get("max_cost_rub", 50.0),
        "step1_curious_yield_min_iterations": curious_yield.get("min_collection_iterations", 2),
        "step1_curious_yield_supplement_rounds": curious_yield.get("supplement_rounds_per_iter", 2),
        "step1_curious_yield_max_search_batches": curious_yield.get("max_search_batches", 5),
        "step1_curious_yield_skip_aggregator_search": curious_yield.get("skip_aggregator_search", True),
        "step1_curious_yield_no_progress_limit": curious_yield.get("no_progress_limit", 2),
        "step1_curious_yield_seed_listing_scan_limit": curious_yield.get("seed_listing_scan_limit", 4),
        "step1_curious_yield_entertainment_rescue_queries": curious_yield.get("entertainment_rescue_queries", 2),
        "step1_curious_yield_rescue_collect_batch": curious_yield.get("rescue_collect_batch", 8),
    }


@lru_cache
def get_pipeline_config() -> dict[str, Any]:
    return read_pipeline_config()
