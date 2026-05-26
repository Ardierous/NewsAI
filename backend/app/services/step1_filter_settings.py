"""Настройки фильтров шага 1: отдельные профили для serious и curious (не смешиваются)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.services.digest_type_policy import is_curious_digest, normalize_digest_type
from app.services.step1_filters import (
    STEP1_FILTER_CATALOG,
    STEP1_FILTER_DEF_BY_ID,
    filter_def_applies_to_digest_type,
    step1_filter_catalog_payload,
)

_MIN_DISCOVERED_PAGES_LOWER = 10
_MIN_DISCOVERED_PAGES_UPPER = 200
_MIN_COLLECTION_ITERATIONS_LOWER = 1
_MIN_COLLECTION_ITERATIONS_UPPER = 50

_STEP1_FILTER_SETTINGS_PATH = Path(__file__).resolve().parents[1] / "step1_filter_settings.json"

_CURIOUS_ONLY_FILTER_IDS = frozenset({"off_topic_not_curious"})


def step1_filter_settings_path() -> Path:
    return _STEP1_FILTER_SETTINGS_PATH


def _bootstrap_filter_config() -> dict[str, Any]:
    """Профиль v2: два независимых набора фильтров."""
    shared_filters = [
        {"id": f.id, "enabled": bool(f.default_enabled), "order": idx}
        for idx, f in enumerate(STEP1_FILTER_CATALOG, start=1)
        if f.id not in _CURIOUS_ONLY_FILTER_IDS
    ]
    curious_filters = list(shared_filters)
    curious_filters.append(
        {"id": "off_topic_not_curious", "enabled": True, "order": len(curious_filters) + 1}
    )
    return {
        "version": 2,
        "serious": {
            "min_discovered_pages": 20,
            "min_collection_iterations": 5,
            "filters": shared_filters,
        },
        "curious": {
            "min_discovered_pages": 15,
            "min_collection_iterations": 6,
            "filters": curious_filters,
        },
    }


def _migrate_v1_to_v2(raw: dict[str, Any]) -> dict[str, Any]:
    filters = list(raw.get("filters") or [])
    min_pages = int(raw.get("min_discovered_pages") or 20)
    min_iters = int(raw.get("min_collection_iterations") or 5)
    serious_filters = [f for f in filters if str(f.get("id")) not in _CURIOUS_ONLY_FILTER_IDS]
    curious_filters = list(filters)
    if not any(str(f.get("id")) == "off_topic_not_curious" for f in curious_filters):
        curious_filters.append(
            {"id": "off_topic_not_curious", "enabled": True, "order": len(curious_filters) + 1}
        )
    return {
        "version": 2,
        "serious": {
            "min_discovered_pages": min_pages,
            "min_collection_iterations": min_iters,
            "filters": serious_filters,
        },
        "curious": {
            "min_discovered_pages": min(min_pages, 15),
            "min_collection_iterations": min_iters,
            "filters": curious_filters,
        },
    }


def _load_raw_settings_file() -> dict[str, Any]:
    path = step1_filter_settings_path()
    if not path.is_file():
        return _bootstrap_filter_config()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return _bootstrap_filter_config()
    if not isinstance(raw, dict):
        return _bootstrap_filter_config()
    if int(raw.get("version") or 1) < 2 or "serious" not in raw or "curious" not in raw:
        return _migrate_v1_to_v2(raw if isinstance(raw, dict) else {})
    return raw


def _normalize_section(payload: dict[str, Any] | list[Any] | None, *, digest_type: str) -> dict[str, Any]:
    """Нормализует один профиль (serious или curious)."""
    allowed_ids = {
        f.id for f in STEP1_FILTER_CATALOG if filter_def_applies_to_digest_type(f.id, digest_type)
    }
    if isinstance(payload, list):
        filters_raw = payload
        min_pages_raw = None
        min_iters_raw = None
        version = 2
    else:
        raw = payload if isinstance(payload, dict) else {}
        filters_raw = list(raw.get("filters") or [])
        min_pages_raw = raw.get("min_discovered_pages")
        min_iters_raw = raw.get("min_collection_iterations")
        version = int(raw.get("version", 2) or 2)

    if min_pages_raw is None:
        min_pages = 20 if digest_type == "serious" else 15
    else:
        try:
            min_pages = int(min_pages_raw)
        except (TypeError, ValueError):
            min_pages = 20 if digest_type == "serious" else 15
    min_pages = max(_MIN_DISCOVERED_PAGES_LOWER, min(_MIN_DISCOVERED_PAGES_UPPER, min_pages))

    if min_iters_raw is None:
        min_iters = 5
    else:
        try:
            min_iters = int(min_iters_raw)
        except (TypeError, ValueError):
            min_iters = 5
    min_iters = max(_MIN_COLLECTION_ITERATIONS_LOWER, min(_MIN_COLLECTION_ITERATIONS_UPPER, min_iters))

    by_id: dict[str, dict[str, Any]] = {}
    for row in filters_raw:
        rid = str(row.get("id") or "").strip()
        if rid in allowed_ids:
            by_id[rid] = {
                "id": rid,
                "enabled": bool(row.get("enabled", False)),
                "order": int(row.get("order", 0) or 0),
            }

    filters_out: list[dict[str, Any]] = []
    for f in STEP1_FILTER_CATALOG:
        if f.id not in allowed_ids:
            continue
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
        "digest_type": digest_type,
    }


def normalize_step1_filter_config(
    payload: dict[str, Any] | list[Any] | None,
    *,
    digest_type: str | None = None,
) -> dict[str, Any]:
    dtype = normalize_digest_type(digest_type)
    return _normalize_section(payload, digest_type=dtype)


def normalize_step1_filter_states(
    states: list[dict[str, Any]] | None,
    *,
    digest_type: str | None = None,
) -> list[dict[str, Any]]:
    return list(
        normalize_step1_filter_config({"version": 2, "filters": states or []}, digest_type=digest_type).get(
            "filters"
        )
        or []
    )


def get_min_discovered_pages(digest_type: str | None = None) -> int:
    return int(load_step1_filter_settings(digest_type).get("min_discovered_pages") or 20)


def load_step1_filter_settings(digest_type: str | None = None) -> dict[str, Any]:
    """Профиль фильтров для типа выпуска (serious / curious)."""
    dtype = normalize_digest_type(digest_type)
    raw = _load_raw_settings_file()
    section = raw.get(dtype)
    if not isinstance(section, dict):
        section = _bootstrap_filter_config()[dtype]
    return _normalize_section(section, digest_type=dtype)


def save_step1_filter_settings(
    config: dict[str, Any],
    *,
    digest_type: str | None = None,
) -> dict[str, Any]:
    """Сохраняет только профиль указанного типа выпуска, не трогая другой."""
    dtype = normalize_digest_type(digest_type)
    normalized = _normalize_section(config, digest_type=dtype)
    raw = _load_raw_settings_file()
    if int(raw.get("version") or 1) < 2:
        raw = _migrate_v1_to_v2(raw)
    raw["version"] = 2
    raw[dtype] = {
        "min_discovered_pages": normalized["min_discovered_pages"],
        "min_collection_iterations": normalized["min_collection_iterations"],
        "filters": normalized["filters"],
    }
    path = step1_filter_settings_path()
    path.write_text(json.dumps(raw, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return normalized


def load_step1_filter_settings_document() -> dict[str, Any]:
    """Полный документ v2 (для админки / сводки)."""
    return _load_raw_settings_file()
