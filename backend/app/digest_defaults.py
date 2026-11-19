"""Стабильные дефолты шага 0 и подписи шага 4 (файл рядом с config.py)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from app.services.platform_assembly import (
    DEFAULT_SUBSCRIPTION_CHANNELS,
    DEFAULT_SUBSCRIPTION_INTRO,
    normalize_subscription_channels,
    normalize_subscription_intro,
)

_DEFAULTS_PATH = Path(__file__).resolve().parent / "digest_defaults.json"


@dataclass(frozen=True)
class Step0Defaults:
    digest_type_default: Literal["serious", "curious"] = "serious"
    digest_topic_default: Literal["ai", "style"] = "ai"
    news_window_days_default: int = 3
    news_window_day_kind_default: Literal["calendar", "working"] = "working"


@dataclass(frozen=True)
class Step4SubscriptionDefaults:
    subscription_intro: str = DEFAULT_SUBSCRIPTION_INTRO
    subscription_channels: dict[str, bool] | None = None

    def channels(self) -> dict[str, bool]:
        return normalize_subscription_channels(self.subscription_channels)

    def as_payload(self) -> dict[str, Any]:
        return {
            "subscription_intro": self.subscription_intro,
            "subscription_channels": self.channels(),
        }


@dataclass(frozen=True)
class DigestDefaults:
    step0: Step0Defaults
    step4_subscription: Step4SubscriptionDefaults


def _coerce_step0(raw: dict[str, Any]) -> Step0Defaults:
    dtype = str(raw.get("digest_type_default") or "serious").strip().lower()
    if dtype not in {"serious", "curious"}:
        dtype = "serious"
    kind = str(raw.get("news_window_day_kind_default") or "working").strip().lower()
    if kind not in {"calendar", "working"}:
        kind = "working"
    days = max(1, min(90, int(raw.get("news_window_days_default") or 3)))
    topic = str(raw.get("digest_topic_default") or "ai").strip().lower()
    if topic not in {"ai", "style"}:
        topic = "ai"
    return Step0Defaults(
        digest_type_default=dtype,  # type: ignore[arg-type]
        digest_topic_default=topic,  # type: ignore[arg-type]
        news_window_days_default=days,
        news_window_day_kind_default=kind,  # type: ignore[arg-type]
    )


def _coerce_step4_subscription(raw: dict[str, Any]) -> Step4SubscriptionDefaults:
    intro = normalize_subscription_intro(raw.get("subscription_intro"))
    channels = normalize_subscription_channels(raw.get("subscription_channels"))
    return Step4SubscriptionDefaults(subscription_intro=intro, subscription_channels=channels)


def _read_defaults_file() -> dict[str, Any]:
    if not _DEFAULTS_PATH.is_file():
        return {}
    try:
        data = json.loads(_DEFAULTS_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


@lru_cache
def get_digest_defaults() -> DigestDefaults:
    data = _read_defaults_file()
    step0_raw = data.get("step0") if isinstance(data.get("step0"), dict) else {}
    step4_raw = data.get("step4") if isinstance(data.get("step4"), dict) else {}
    return DigestDefaults(
        step0=_coerce_step0(step0_raw),
        step4_subscription=_coerce_step4_subscription(step4_raw),
    )


def get_step4_subscription_defaults() -> Step4SubscriptionDefaults:
    return get_digest_defaults().step4_subscription


def save_step4_subscription_defaults(
    *,
    subscription_intro: str | None = None,
    subscription_channels: dict[str, bool] | None = None,
) -> Step4SubscriptionDefaults:
    """Сохранить подпись шага 4 как будущий дефолт (после генерации текстов)."""
    current = get_step4_subscription_defaults()
    intro = normalize_subscription_intro(
        subscription_intro if subscription_intro is not None else current.subscription_intro
    )
    channels = normalize_subscription_channels(
        subscription_channels if subscription_channels is not None else current.channels()
    )
    data = _read_defaults_file()
    step0 = data.get("step0") if isinstance(data.get("step0"), dict) else {}
    payload = {
        "step0": step0
        or {
            "digest_type_default": "serious",
            "digest_topic_default": "ai",
            "news_window_days_default": 3,
            "news_window_day_kind_default": "working",
        },
        "step4": {
            "subscription_intro": intro,
            "subscription_channels": channels,
        },
    }
    _DEFAULTS_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    get_digest_defaults.cache_clear()
    return Step4SubscriptionDefaults(subscription_intro=intro, subscription_channels=channels)


# Для обратной совместимости импортов/тестов.
__all__ = [
    "DEFAULT_SUBSCRIPTION_CHANNELS",
    "DEFAULT_SUBSCRIPTION_INTRO",
    "DigestDefaults",
    "Step0Defaults",
    "Step4SubscriptionDefaults",
    "get_digest_defaults",
    "get_step4_subscription_defaults",
    "save_step4_subscription_defaults",
]
