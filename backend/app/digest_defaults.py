"""Стабильные дефолты шага 0 (файл рядом с config.py)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

_DEFAULTS_PATH = Path(__file__).resolve().parent / "digest_defaults.json"


@dataclass(frozen=True)
class Step0Defaults:
    digest_type_default: Literal["serious", "curious"] = "curious"
    news_window_days_default: int = 3
    news_window_day_kind_default: Literal["calendar", "working"] = "working"


@dataclass(frozen=True)
class DigestDefaults:
    step0: Step0Defaults


def _coerce_step0(raw: dict[str, Any]) -> Step0Defaults:
    dtype = str(raw.get("digest_type_default") or "curious").strip().lower()
    if dtype not in {"serious", "curious"}:
        dtype = "curious"
    kind = str(raw.get("news_window_day_kind_default") or "working").strip().lower()
    if kind not in {"calendar", "working"}:
        kind = "working"
    days = max(1, min(90, int(raw.get("news_window_days_default") or 3)))
    return Step0Defaults(
        digest_type_default=dtype,  # type: ignore[arg-type]
        news_window_days_default=days,
        news_window_day_kind_default=kind,  # type: ignore[arg-type]
    )


@lru_cache
def get_digest_defaults() -> DigestDefaults:
    data: dict[str, Any] = {}
    if _DEFAULTS_PATH.is_file():
        try:
            data = json.loads(_DEFAULTS_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            data = {}
    step0_raw = data.get("step0") if isinstance(data.get("step0"), dict) else {}
    return DigestDefaults(step0=_coerce_step0(step0_raw))
