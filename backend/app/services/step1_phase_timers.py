"""Накопление времени по фазам одного прогона шага 1 (контекст запроса)."""

from __future__ import annotations

import time
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Iterator

PHASE_KEYS = (
    "web_search",
    "alt_search",
    "http_verify",
    "telegram",
    "crew",
    "crew_enrich",
    "seed_fallback",
    "other",
)


@dataclass
class Step1PhaseTimers:
    seconds: dict[str, float] = field(default_factory=dict)

    def add(self, phase: str, delta_sec: float) -> None:
        if delta_sec <= 0:
            return
        key = str(phase or "other").strip() or "other"
        if key not in PHASE_KEYS:
            key = "other"
        self.seconds[key] = float(self.seconds.get(key, 0.0) or 0.0) + float(delta_sec)

    def to_meta(self) -> dict[str, int]:
        return {k: int(round(float(self.seconds.get(k, 0.0) or 0.0))) for k in PHASE_KEYS if self.seconds.get(k)}

    def merge_into(self, meta: dict) -> None:
        bucket = meta.setdefault("phase_sec", {})
        if not isinstance(bucket, dict):
            bucket = {}
            meta["phase_sec"] = bucket
        for key, value in self.to_meta().items():
            bucket[key] = int(bucket.get(key, 0) or 0) + int(value)


_TIMERS: ContextVar[Step1PhaseTimers | None] = ContextVar("step1_phase_timers", default=None)


def reset_step1_phase_timers() -> Step1PhaseTimers:
    timers = Step1PhaseTimers()
    _TIMERS.set(timers)
    return timers


def current_step1_phase_timers() -> Step1PhaseTimers | None:
    return _TIMERS.get()


from app.services.step1_live_progress import on_step1_phase_enter


@contextmanager
def step1_phase(phase: str) -> Iterator[None]:
    timers = _TIMERS.get()
    on_step1_phase_enter(phase)
    if timers is None:
        yield
        return
    started = time.monotonic()
    try:
        yield
    finally:
        timers.add(phase, time.monotonic() - started)
