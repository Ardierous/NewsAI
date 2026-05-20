"""Единый конфиг-файл настроек фильтрации шага 1 (значения из окна настроек)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.services.step1_filters import STEP1_FILTER_CATALOG, STEP1_FILTER_DEF_BY_ID

_MIN_DISCOVERED_PAGES_LOWER = 10
_MIN_DISCOVERED_PAGES_UPPER = 200
_MIN_COLLECTION_ITERATIONS_LOWER = 1
_MIN_COLLECTION_ITERATIONS_UPPER = 50

_STEP1_FILTER_SETTINGS_PATH = Path(__file__).resolve().parents[1] / "step1_filter_settings.json"


def step1_filter_settings_path() -> Path:
    return _STEP1_FILTER_SETTINGS_PATH


def _bootstrap_filter_config() -> dict[str, Any]:
    return {
        "version": 1,
        "min_discovered_pages": 20,
        "min_collection_iterations": 5,
        "filters": [
            {"id": f.id, "enabled": bool(f.default_enabled), "order": idx}
            for idx, f in enumerate(STEP1_FILTER_CATALOG, start=1)
        ],
    }


def normalize_step1_filter_config(payload: dict[str, Any] | list[Any] | None) -> dict[str, Any]:
    """Нормализует конфиг; значения enabled/order берутся только из payload (файла или PUT)."""
    if isinstance(payload, list):
        filters_raw = payload
        min_pages_raw = None
        min_iters_raw = None
        version = 1
    else:
        raw = payload if isinstance(payload, dict) else {}
        filters_raw = list(raw.get("filters") or [])
        min_pages_raw = raw.get("min_discovered_pages")
        min_iters_raw = raw.get("min_collection_iterations")
        version = int(raw.get("version", 1) or 1)

    if min_pages_raw is None:
        min_pages = _min_discovered_pages_from_file_or_bootstrap()
    else:
        try:
            min_pages = int(min_pages_raw)
        except (TypeError, ValueError):
            min_pages = _min_discovered_pages_from_file_or_bootstrap()
    min_pages = max(_MIN_DISCOVERED_PAGES_LOWER, min(_MIN_DISCOVERED_PAGES_UPPER, min_pages))

    if min_iters_raw is None:
        min_iters = _min_collection_iterations_from_file_or_bootstrap()
    else:
        try:
            min_iters = int(min_iters_raw)
        except (TypeError, ValueError):
            min_iters = _min_collection_iterations_from_file_or_bootstrap()
    min_iters = max(_MIN_COLLECTION_ITERATIONS_LOWER, min(_MIN_COLLECTION_ITERATIONS_UPPER, min_iters))

    by_id: dict[str, dict[str, Any]] = {}
    for row in filters_raw:
        rid = str(row.get("id") or "").strip()
        if rid in STEP1_FILTER_DEF_BY_ID:
            by_id[rid] = {
                "id": rid,
                "enabled": bool(row.get("enabled", False)),
                "order": int(row.get("order", 0) or 0),
            }

    filters_out: list[dict[str, Any]] = []
    for f in STEP1_FILTER_CATALOG:
        src = by_id.get(f.id)
        if src is None:
            filters_out.append(
                {
                    "id": f.id,
                    "enabled": bool(f.default_enabled),
                    "order": 9999,
                }
            )
            continue
        filters_out.append(
            {
                "id": f.id,
                "enabled": bool(src["enabled"]),
                "order": max(1, int(src["order"])),
            }
        )
    filters_out.sort(key=lambda x: (int(x["order"]), str(x["id"])))
    for idx, row in enumerate(filters_out, start=1):
        row["order"] = idx

    return {
        "version": version,
        "filters": filters_out,
        "min_discovered_pages": min_pages,
        "min_collection_iterations": min_iters,
    }


def _min_discovered_pages_from_file_or_bootstrap() -> int:
    path = step1_filter_settings_path()
    if path.is_file():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(raw, dict) and raw.get("min_discovered_pages") is not None:
                return max(
                    _MIN_DISCOVERED_PAGES_LOWER,
                    min(_MIN_DISCOVERED_PAGES_UPPER, int(raw["min_discovered_pages"])),
                )
        except (json.JSONDecodeError, OSError, TypeError, ValueError):
            pass
    return int(_bootstrap_filter_config()["min_discovered_pages"])


def _min_collection_iterations_from_file_or_bootstrap() -> int:
    path = step1_filter_settings_path()
    if path.is_file():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(raw, dict) and raw.get("min_collection_iterations") is not None:
                return max(
                    _MIN_COLLECTION_ITERATIONS_LOWER,
                    min(_MIN_COLLECTION_ITERATIONS_UPPER, int(raw["min_collection_iterations"])),
                )
        except (json.JSONDecodeError, OSError, TypeError, ValueError):
            pass
    return int(_bootstrap_filter_config()["min_collection_iterations"])


def normalize_step1_filter_states(states: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    return list(normalize_step1_filter_config({"version": 1, "filters": states or []}).get("filters") or [])


def get_min_discovered_pages() -> int:
    return int(load_step1_filter_settings().get("min_discovered_pages") or _min_discovered_pages_from_file_or_bootstrap())


def load_step1_filter_settings() -> dict[str, Any]:
    path = step1_filter_settings_path()
    if not path.is_file():
        return save_step1_filter_settings(_bootstrap_filter_config())
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return save_step1_filter_settings(_bootstrap_filter_config())
    return normalize_step1_filter_config(raw)


def save_step1_filter_settings(config: dict[str, Any]) -> dict[str, Any]:
    normalized = normalize_step1_filter_config(config)
    path = step1_filter_settings_path()
    path.write_text(json.dumps(normalized, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return normalized
