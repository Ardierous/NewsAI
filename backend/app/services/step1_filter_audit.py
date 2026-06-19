"""Диагностика отсева шага 1: счётчики, примеры, трассировка курьёзного tone gate."""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any, Literal
from urllib.parse import urlparse

from app.services.curious_tone import explain_curious_gates

logger = logging.getLogger("app.step1.curious_tone")

_filter_stats_every_n = 50
_reject_samples_per_reason = 8
_reject_audit_top_reasons = 5


def configure_step1_filter_audit(settings: Any) -> None:
    """Вызывается из setup_logging после загрузки Settings."""
    global _filter_stats_every_n, _reject_samples_per_reason, _reject_audit_top_reasons
    _filter_stats_every_n = max(1, int(getattr(settings, "step1_log_filter_stats_every_n", 50) or 50))
    _reject_samples_per_reason = max(1, int(getattr(settings, "step1_log_reject_samples_per_reason", 8) or 8))
    _reject_audit_top_reasons = max(1, int(getattr(settings, "step1_log_reject_audit_top_reasons", 5) or 5))


def filter_stats_log_interval() -> int:
    return _filter_stats_every_n


def reject_samples_per_reason_limit() -> int:
    return _reject_samples_per_reason


def reject_audit_top_reasons_limit() -> int:
    return _reject_audit_top_reasons


class Step1CuriousToneAudit:
    """Сессия трассировки решений pool/tone gate для одного прогона шага 1."""

    def __init__(self, settings: Any) -> None:
        self._enabled = bool(getattr(settings, "step1_curious_tone_log_enabled", True))
        self._log_accept = bool(getattr(settings, "step1_curious_tone_log_accept", True))
        self._log_reject = bool(getattr(settings, "step1_curious_tone_log_reject", True))
        self._log_low_tone = bool(getattr(settings, "step1_curious_tone_log_low_tone", True))
        self._max_events = max(0, int(getattr(settings, "step1_curious_tone_log_max_events", 200) or 200))
        self._title_chars = max(40, int(getattr(settings, "step1_curious_tone_title_preview_chars", 120) or 120))
        self._corpus_chars = max(0, int(getattr(settings, "step1_curious_tone_corpus_preview_chars", 160) or 160))
        self._include_signals = bool(getattr(settings, "step1_curious_tone_include_signals", True))
        self._digest_id: int | None = None
        self._events_logged = 0
        self._counts: dict[str, int] = defaultdict(int)
        self._samples: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self._accept_samples: list[dict[str, Any]] = []
        self._low_tone_samples: list[dict[str, Any]] = []
        self._flushed = False
        self._last_summary: dict[str, Any] = {}

    def begin_run(self, digest_id: int) -> None:
        self._digest_id = digest_id
        self._events_logged = 0
        self._counts.clear()
        self._samples.clear()
        self._accept_samples.clear()
        self._low_tone_samples.clear()
        if self._enabled:
            logger.info(
                "[CURIOUS_TONE] run start | digest_id=%s max_events=%s accept=%s reject=%s low_tone=%s",
                digest_id,
                self._max_events,
                self._log_accept,
                self._log_reject,
                self._log_low_tone,
            )

    def record(
        self,
        *,
        url: str,
        title: str,
        corpus: str = "",
        stage: str = "verify",
        outcome: Literal["accept", "reject", "low_tone"],
        filter_code: str | None = None,
        explanation: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        expl = explanation or explain_curious_gates(title, corpus, url=url)
        reason = str(expl.get("pool_reason") or expl.get("tone_reason") or "unknown")
        bucket = filter_code or reason
        self._counts[bucket] += 1

        sample = self._sample_row(url, title, corpus, stage, outcome, expl, filter_code)
        if outcome == "reject":
            samples = self._samples[bucket]
            if len(samples) < reject_samples_per_reason_limit():
                samples.append(sample)
        elif outcome == "low_tone" and len(self._low_tone_samples) < reject_samples_per_reason_limit():
            self._low_tone_samples.append(sample)
        elif outcome == "accept" and len(self._accept_samples) < reject_samples_per_reason_limit():
            self._accept_samples.append(sample)

        if not self._enabled or self._events_logged >= self._max_events:
            return expl
        if outcome == "accept" and not self._log_accept:
            return expl
        if outcome == "reject" and not self._log_reject:
            return expl
        if outcome == "low_tone" and not self._log_low_tone:
            return expl

        host = (urlparse(url).hostname or "").lower() if url else ""
        msg = (
            "[CURIOUS_TONE] digest_id=%s stage=%s outcome=%s host=%s "
            "pool=%s tone=%s dry=%s score=%s pool_reason=%s tone_reason=%s title=%r"
        )
        args: list[Any] = [
            self._digest_id,
            stage,
            outcome,
            host or "-",
            expl.get("pool_pass"),
            expl.get("tone_pass"),
            expl.get("dry_serious"),
            expl.get("tone_score"),
            expl.get("pool_reason"),
            expl.get("tone_reason"),
            (title or "")[: self._title_chars],
        ]
        if self._corpus_chars and corpus:
            msg += " corpus=%r"
            args.append(corpus[: self._corpus_chars])
        if self._include_signals:
            msg += " signals=%s"
            args.append(
                {
                    "pos": expl.get("has_positive"),
                    "pos_title": expl.get("has_positive_title"),
                    "serious": expl.get("has_serious"),
                    "serious_title": expl.get("has_serious_title"),
                    "human": expl.get("has_human_interest"),
                }
            )
        if filter_code:
            msg += " filter=%s"
            args.append(filter_code)
        logger.info(msg, *args)
        self._events_logged += 1
        return expl

    def flush_summary(self) -> dict[str, Any]:
        if self._flushed:
            return dict(self._last_summary)
        if not self._enabled or self._digest_id is None:
            self._flushed = True
            return {}
        summary = {
            "digest_id": self._digest_id,
            "events_logged": self._events_logged,
            "counts": dict(self._counts),
            "reject_samples_by_reason": {k: list(v) for k, v in self._samples.items()},
            "accept_samples": list(self._accept_samples),
            "low_tone_samples": list(self._low_tone_samples),
        }
        top = sorted(self._counts.items(), key=lambda x: (-x[1], x[0]))[: reject_audit_top_reasons_limit()]
        logger.info(
            "[CURIOUS_TONE_SUMMARY] digest_id=%s events_logged=%s/%s top=%s accept=%s low_tone=%s",
            self._digest_id,
            self._events_logged,
            self._max_events,
            top,
            len(self._accept_samples),
            len(self._low_tone_samples),
        )
        self._flushed = True
        self._last_summary = summary
        return summary

    def _sample_row(
        self,
        url: str,
        title: str,
        corpus: str,
        stage: str,
        outcome: str,
        expl: dict[str, Any],
        filter_code: str | None,
    ) -> dict[str, Any]:
        return {
            "url": (url or "")[:500],
            "host": (urlparse(url).hostname or "").lower()[:120] if url else "",
            "title": (title or "")[:220],
            "stage": stage,
            "outcome": outcome,
            "filter_code": filter_code or "",
            "pool_pass": expl.get("pool_pass"),
            "tone_pass": expl.get("tone_pass"),
            "pool_reason": expl.get("pool_reason"),
            "tone_reason": expl.get("tone_reason"),
            "tone_score": expl.get("tone_score"),
            "dry_serious": expl.get("dry_serious"),
        }
