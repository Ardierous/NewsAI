"""Онлайн-снимок прогресса шага 1 (пока идёт сбор кандидатов)."""

from __future__ import annotations

import threading
import time
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

_PHASE_LABELS_RU: dict[str, str] = {
    "web_search": "Веб-поиск (ProxyAPI)",
    "alt_search": "Поиск SerpAPI / Tavily",
    "http_verify": "Проверка страниц по ссылкам",
    "telegram": "Монитор Telegram",
    "crew": "Добор через CrewAI",
    "seed_fallback": "Обход seed-листингов",
    "other": "Подготовка",
    "startup": "Запуск сбора",
}

_LOCK = threading.Lock()
_LIVE: dict[int, "Step1LiveSnapshot"] = {}
_BOUND_DIGEST: ContextVar[int | None] = ContextVar("step1_live_digest_id", default=None)


@dataclass
class Step1LiveSnapshot:
    digest_id: int
    started_monotonic: float
    collection_target: int = 15
    phase_key: str = "startup"
    phase: str = _PHASE_LABELS_RU["startup"]
    iteration: int = 0
    urls_raw: int = 0
    urls_raw_merged: int = 0
    urls_prefilter_rejected: int = 0
    urls_sent_to_http: int = 0
    verified_pool: int = 0
    rejected_total: int = 0
    web_search_api_calls: int = 0
    web_search_citation_urls: int = 0
    web_search_cost_est_rub: float = 0.0
    cancel_requested: bool = False
    finished: bool = False
    finished_elapsed_sec: int = 0
    extra: dict[str, Any] = field(default_factory=dict)


def bind_live_digest(digest_id: int | None) -> None:
    _BOUND_DIGEST.set(int(digest_id) if digest_id is not None else None)


def phase_label_ru(phase_key: str) -> str:
    key = str(phase_key or "other").strip() or "other"
    return _PHASE_LABELS_RU.get(key, key)


def begin_live_progress(digest_id: int, *, collection_target: int = 15) -> None:
    did = int(digest_id)
    snap = Step1LiveSnapshot(
        digest_id=did,
        started_monotonic=time.monotonic(),
        collection_target=max(1, int(collection_target or 15)),
    )
    with _LOCK:
        _LIVE[did] = snap


def finish_live_progress(digest_id: int) -> None:
    """Зафиксировать итоговый снимок после завершения сбора (остаётся для UI)."""
    did = int(digest_id)
    with _LOCK:
        snap = _LIVE.get(did)
        if snap is None:
            return
        snap.finished = True
        snap.finished_elapsed_sec = max(0, int(time.monotonic() - snap.started_monotonic))
        if snap.phase_key not in ("done", "cancelled"):
            snap.phase_key = "done"
            snap.phase = "Сбор завершён"


def end_live_progress(digest_id: int) -> None:
    """Удалить снимок (тесты, новый запуск перезапишет через begin_live_progress)."""
    did = int(digest_id)
    with _LOCK:
        _LIVE.pop(did, None)


def mark_live_cancel_requested(digest_id: int) -> None:
    did = int(digest_id)
    with _LOCK:
        snap = _LIVE.get(did)
        if snap is not None:
            snap.cancel_requested = True


def touch_live_phase(digest_id: int | None, phase_key: str) -> None:
    if digest_id is None:
        return
    did = int(digest_id)
    label = phase_label_ru(phase_key)
    with _LOCK:
        snap = _LIVE.get(did)
        if snap is None:
            return
        snap.phase_key = str(phase_key or "other")
        snap.phase = label


def sync_live_progress(
    digest_id: int,
    *,
    meta: dict[str, Any],
    reject_total: int = 0,
    iteration_no: int = 0,
) -> None:
    did = int(digest_id)
    with _LOCK:
        snap = _LIVE.get(did)
        if snap is None:
            return
        snap.iteration = max(0, int(iteration_no or meta.get("iterations") or 0))
        snap.urls_raw = max(
            int(meta.get("urls_raw_unique") or 0),
            int(meta.get("urls_raw_merged") or 0),
        )
        snap.urls_raw_merged = int(meta.get("urls_raw_merged") or 0)
        snap.urls_prefilter_rejected = int(meta.get("urls_prefilter_rejected") or 0)
        snap.urls_sent_to_http = int(meta.get("urls_sent_to_http") or 0)
        snap.verified_pool = int(meta.get("verified_total") or 0)
        snap.rejected_total = max(0, int(reject_total))
        snap.web_search_api_calls = int(meta.get("web_search_api_calls") or 0)
        snap.web_search_citation_urls = int(meta.get("web_search_citation_urls") or 0)
        snap.web_search_cost_est_rub = float(meta.get("web_search_cost_est_rub") or 0.0)
        if meta.get("collection_target_pages") is not None:
            snap.collection_target = max(1, int(meta.get("collection_target_pages") or snap.collection_target))


def bump_live_progress(
    digest_id: int | None = None,
    *,
    phase_key: str | None = None,
    iteration: int | None = None,
    urls_raw: int | None = None,
    urls_raw_merged: int | None = None,
    urls_prefilter_rejected: int | None = None,
    urls_sent_to_http: int | None = None,
    verified_pool: int | None = None,
    rejected_total: int | None = None,
    web_search_api_calls: int | None = None,
    web_search_citation_urls: int | None = None,
    web_search_cost_est_rub: float | None = None,
) -> None:
    """Частичное обновление снимка без persist_collection_meta (оба типа дайджеста)."""
    did = int(digest_id) if digest_id is not None else _BOUND_DIGEST.get()
    if did is None:
        return
    with _LOCK:
        snap = _LIVE.get(int(did))
        if snap is None:
            return
        if phase_key is not None:
            snap.phase_key = str(phase_key)
            snap.phase = phase_label_ru(phase_key)
        if iteration is not None:
            snap.iteration = max(0, int(iteration))
        if urls_raw is not None:
            snap.urls_raw = max(snap.urls_raw, int(urls_raw))
        if urls_raw_merged is not None:
            snap.urls_raw_merged = int(urls_raw_merged)
            snap.urls_raw = max(snap.urls_raw, int(urls_raw_merged))
        if urls_prefilter_rejected is not None:
            snap.urls_prefilter_rejected = int(urls_prefilter_rejected)
        if urls_sent_to_http is not None:
            snap.urls_sent_to_http = int(urls_sent_to_http)
        if verified_pool is not None:
            snap.verified_pool = int(verified_pool)
        if rejected_total is not None:
            snap.rejected_total = max(0, int(rejected_total))
        if web_search_api_calls is not None:
            snap.web_search_api_calls = int(web_search_api_calls)
        if web_search_citation_urls is not None:
            snap.web_search_citation_urls = int(web_search_citation_urls)
        if web_search_cost_est_rub is not None:
            snap.web_search_cost_est_rub = float(web_search_cost_est_rub)


def sync_live_from_web_search_stats(digest_id: int | None = None) -> None:
    """Подтянуть счётчики ProxyAPI web_search в live-снимок."""
    from app.services.step1_web_search_stats import current_step1_web_search_stats

    stats = current_step1_web_search_stats()
    if stats is None:
        return
    meta = stats.to_meta()
    bump_live_progress(
        digest_id,
        web_search_api_calls=int(meta.get("web_search_api_calls") or 0),
        web_search_citation_urls=int(meta.get("web_search_citation_urls") or 0),
        web_search_cost_est_rub=float(meta.get("web_search_cost_est_rub") or 0.0),
    )


def _format_elapsed(sec: int) -> str:
    sec = max(0, int(sec))
    if sec < 60:
        return f"{sec} с"
    m, s = divmod(sec, 60)
    return f"{m} мин {s} с" if s else f"{m} мин"


def snapshot_live_progress(digest_id: int) -> dict[str, Any] | None:
    did = int(digest_id)
    with _LOCK:
        snap = _LIVE.get(did)
        if snap is None:
            return None
        elapsed = (
            int(snap.finished_elapsed_sec)
            if snap.finished
            else int(time.monotonic() - snap.started_monotonic)
        )
        return {
            "running": not snap.finished,
            "phase": snap.phase,
            "phase_key": snap.phase_key,
            "elapsed_sec": elapsed,
            "elapsed_human": _format_elapsed(elapsed),
            "iteration": snap.iteration,
            "web_search_api_calls": snap.web_search_api_calls,
            "web_search_citation_urls": snap.web_search_citation_urls,
            "web_search_cost_est_rub": round(float(snap.web_search_cost_est_rub), 4),
            "urls_raw": snap.urls_raw,
            "urls_raw_merged": snap.urls_raw_merged,
            "urls_prefilter_rejected": snap.urls_prefilter_rejected,
            "urls_sent_to_http": snap.urls_sent_to_http,
            "verified_pool": snap.verified_pool,
            "rejected_total": snap.rejected_total,
            "collection_target": snap.collection_target,
            "cancel_requested": bool(snap.cancel_requested),
        }


def on_step1_phase_enter(phase_key: str) -> None:
    did = _BOUND_DIGEST.get()
    if did is not None:
        touch_live_phase(did, phase_key)
