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
            "web_search_context_size": "low",
            "web_search_context_size_supplement": "low",
            "tier_max_web_search_batches": 6,
            "min_urls_before_proxyapi": 5,
            "web_search_prefer_alt_providers": False,
            "batch_size": 8,
            "search_fetch_limit": 36,
            "urls_checked_per_collect": 24,
            "soft_time_limit_sec": 90,
            "hard_time_limit_sec": 150,
            "max_candidates_for_ui": 15,
            "verify_workers": 6,
            "crew_fallback_only_if_empty": True,
            "tier_strict_search": True,
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
    step1["tier_strict_search"] = _coerce_bool(step1_raw.get("tier_strict_search"), step1["tier_strict_search"])
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


def pipeline_settings_flat(path: Path | None = None) -> dict[str, Any]:
    """Плоский словарь имён полей Settings ← значения из JSON."""
    cfg = read_pipeline_config(path) if path is not None else get_pipeline_config()
    web = cfg["web"]
    s1 = cfg["step1"]
    s2 = cfg["step2"]
    wf = cfg["workflow"]
    s4 = cfg["step4"]
    lg = cfg["logging"]
    return {
        "enable_web_fetch": web["enable_fetch"],
        "step1_search_tier1_min_raw_urls": s1["search_tier1_min_raw_urls"],
        "step1_max_cost_rub": s1["max_cost_rub"],
        "proxyapi_web_search_context_size": s1["web_search_context_size"],
        "proxyapi_web_search_context_size_supplement": s1["web_search_context_size_supplement"],
        "step1_tier_max_web_search_batches": s1["tier_max_web_search_batches"],
        "step1_min_urls_before_proxyapi": s1["min_urls_before_proxyapi"],
        "step1_web_search_prefer_alt_providers": s1["web_search_prefer_alt_providers"],
        "step1_batch_size": s1["batch_size"],
        "step1_search_fetch_limit": s1["search_fetch_limit"],
        "step1_urls_checked_per_collect": s1["urls_checked_per_collect"],
        "step1_soft_time_limit_sec": s1["soft_time_limit_sec"],
        "step1_hard_time_limit_sec": s1["hard_time_limit_sec"],
        "step1_max_candidates_for_ui": s1["max_candidates_for_ui"],
        "step1_verify_workers": s1["verify_workers"],
        "step1_crew_fallback_only_if_empty": s1["crew_fallback_only_if_empty"],
        "step1_tier_strict_search": s1["tier_strict_search"],
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
        "step2_max_cost_rub": s2["max_cost_rub"],
        "auto_run_step3_after_order": wf["auto_run_step3_after_order"],
        "enable_step4_image_generation": s4["enable_image_generation"],
        "log_level": lg["level"],
        "log_enable_file": lg["enable_file"],
        "log_file_name": lg["file_name"],
        "log_max_bytes": lg["max_bytes"],
        "log_backup_count": lg["backup_count"],
    }


@lru_cache
def get_pipeline_config() -> dict[str, Any]:
    return read_pipeline_config()
