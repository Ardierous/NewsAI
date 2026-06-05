"""Конверсия воронки шага 1 и оценка объёма сырого web-поиска."""

from __future__ import annotations

import json
import statistics
from pathlib import Path
from typing import Any

DEFAULT_E2E_CONVERSION = 0.18
SAFETY_FACTOR = 1.4
MAX_HISTORY_SAMPLES = 20
MIN_RAW_FETCH = 30
MAX_RAW_FETCH = 80
TARGET_VERIFIED = 10

_HISTORY_PATH = Path(__file__).resolve().parent.parent / "step1_conversion_history.json"


def compute_funnel_conversions(
    *,
    urls_raw_merged: int,
    urls_raw_unique: int,
    urls_prefilter_rejected: int,
    urls_sent_to_http: int,
    verified_total: int,
) -> dict[str, Any]:
    raw_unique = max(0, int(urls_raw_unique))
    raw_batch = max(0, int(urls_raw_merged))
    sent = max(0, int(urls_sent_to_http))
    verified = max(0, int(verified_total))
    prefilter_rejected = max(0, int(urls_prefilter_rejected))

    conv_prefilter = (sent / raw_unique) if raw_unique else None
    conv_http = (verified / sent) if sent else None
    conv_e2e = (verified / raw_unique) if raw_unique else None

    return {
        "urls_raw_unique": raw_unique,
        "urls_raw_merged": raw_batch,
        "urls_prefilter_rejected": prefilter_rejected,
        "urls_sent_to_http": sent,
        "verified_total": verified,
        "conversion_prefilter_pct": _pct(conv_prefilter),
        "conversion_http_pct": _pct(conv_http),
        "conversion_e2e_pct": _pct(conv_e2e),
        "conversion_e2e": round(conv_e2e, 4) if conv_e2e is not None else None,
    }


def estimate_raw_urls_for_target(
    conversion_e2e: float | None,
    *,
    target: int = TARGET_VERIFIED,
    safety_factor: float = SAFETY_FACTOR,
    default_e2e: float = DEFAULT_E2E_CONVERSION,
    min_raw: int = MIN_RAW_FETCH,
    max_raw: int = MAX_RAW_FETCH,
) -> int:
    rate = conversion_e2e if conversion_e2e and conversion_e2e > 0.01 else default_e2e
    need = int((target / rate) * safety_factor + 0.999)
    return max(min_raw, min(max_raw, need))


def load_conversion_history(path: Path | None = None) -> dict[str, list[float]]:
    path = path or _HISTORY_PATH
    empty: dict[str, list[float]] = {"serious": [], "curious": []}
    if not path.is_file():
        return empty
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return empty
    if not isinstance(raw, dict):
        return empty
    out = dict(empty)
    for key in out:
        items = raw.get(key)
        if not isinstance(items, list):
            continue
        out[key] = [float(x) for x in items if isinstance(x, (int, float)) and 0 < float(x) <= 1]
    return out


def median_e2e_for_digest_type(digest_type: str, path: Path | None = None) -> float | None:
    hist = load_conversion_history(path)
    key = "curious" if str(digest_type or "").strip().lower() == "curious" else "serious"
    samples = hist.get(key) or []
    if not samples:
        return None
    return float(statistics.median(samples))


def record_e2e_sample(digest_type: str, conversion_e2e: float | None, path: Path | None = None) -> None:
    if conversion_e2e is None or conversion_e2e <= 0 or conversion_e2e > 1:
        return
    path = path or _HISTORY_PATH
    hist = load_conversion_history(path)
    key = "curious" if str(digest_type or "").strip().lower() == "curious" else "serious"
    samples = list(hist.get(key) or [])
    samples.append(float(conversion_e2e))
    hist[key] = samples[-MAX_HISTORY_SAMPLES:]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(hist, ensure_ascii=False, indent=2), encoding="utf-8")


def apply_funnel_conversions_to_meta(meta: dict[str, Any], *, digest_type: str) -> dict[str, Any]:
    conv = compute_funnel_conversions(
        urls_raw_merged=int(meta.get("urls_raw_merged", 0) or 0),
        urls_raw_unique=int(meta.get("urls_raw_unique", 0) or 0),
        urls_prefilter_rejected=int(meta.get("urls_prefilter_rejected", 0) or 0),
        urls_sent_to_http=int(meta.get("urls_sent_to_http", 0) or 0),
        verified_total=int(meta.get("verified_total", 0) or 0),
    )
    meta.update(conv)
    baseline = median_e2e_for_digest_type(digest_type)
    meta["conversion_e2e_baseline"] = round(baseline, 4) if baseline is not None else None
    meta["estimated_raw_for_10"] = estimate_raw_urls_for_target(baseline)
    if conv.get("conversion_e2e"):
        meta["estimated_raw_for_10_run"] = estimate_raw_urls_for_target(float(conv["conversion_e2e"]))
    return meta


def _pct(value: float | None) -> float | None:
    if value is None:
        return None
    return round(value * 100.0, 1)
