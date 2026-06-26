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
    "seed_fallback": "Обход seed-лент",
    "cheap_seeds": "Обход seed-лент (бесплатно)",
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
    rejected_links: int = 0
    web_search_api_calls: int = 0
    web_search_citation_urls: int = 0
    web_search_cost_est_rub: float = 0.0
    pool_carried_over: int = 0
    pool_added_this_run: int = 0
    links_found_paid: int = 0
    links_found_free: int = 0
    links_processed: int = 0
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
        snap.rejected_links = max(snap.rejected_links, int(meta.get("rejected_links") or 0))
        snap.web_search_api_calls = int(meta.get("web_search_api_calls") or 0)
        snap.web_search_citation_urls = int(meta.get("web_search_citation_urls") or 0)
        snap.web_search_cost_est_rub = float(meta.get("web_search_cost_est_rub") or 0.0)
        paid_found = max(
            int(meta.get("links_found_paid") or 0),
            int(meta.get("web_search_citation_urls") or 0),
            int(meta.get("urls_raw_unique") or 0),
            int(meta.get("urls_raw_merged") or 0),
        )
        snap.links_found_paid = max(snap.links_found_paid, paid_found)
        snap.links_found_free = max(snap.links_found_free, int(meta.get("links_found_free") or 0))
        snap.links_processed = max(snap.links_processed, int(meta.get("links_processed") or 0))
        snap.pool_carried_over = max(snap.pool_carried_over, int(meta.get("pool_carried_over") or 0))
        snap.pool_added_this_run = max(snap.pool_added_this_run, int(meta.get("pool_added_this_run") or 0))
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
    rejected_links: int | None = None,
    web_search_api_calls: int | None = None,
    web_search_citation_urls: int | None = None,
    web_search_cost_est_rub: float | None = None,
    pool_carried_over: int | None = None,
    pool_added_this_run: int | None = None,
    links_found_paid: int | None = None,
    links_found_free: int | None = None,
    links_processed: int | None = None,
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
        if rejected_links is not None:
            snap.rejected_links = max(0, int(rejected_links))
        if web_search_api_calls is not None:
            snap.web_search_api_calls = int(web_search_api_calls)
        if web_search_citation_urls is not None:
            snap.web_search_citation_urls = int(web_search_citation_urls)
        if web_search_cost_est_rub is not None:
            snap.web_search_cost_est_rub = float(web_search_cost_est_rub)
        if pool_carried_over is not None:
            snap.pool_carried_over = max(0, int(pool_carried_over))
        if pool_added_this_run is not None:
            snap.pool_added_this_run = max(0, int(pool_added_this_run))
        if links_found_paid is not None:
            snap.links_found_paid = max(snap.links_found_paid, int(links_found_paid))
        if links_found_free is not None:
            snap.links_found_free = max(snap.links_found_free, int(links_found_free))
        if links_processed is not None:
            snap.links_processed = max(snap.links_processed, int(links_processed))


def record_link_rejected(digest_id: int | None = None) -> None:
    did = int(digest_id) if digest_id is not None else _BOUND_DIGEST.get()
    if did is None:
        return
    with _LOCK:
        snap = _LIVE.get(int(did))
        if snap is not None:
            snap.rejected_links += 1


def record_link_accepted_to_pool(digest_id: int | None = None) -> None:
    did = int(digest_id) if digest_id is not None else _BOUND_DIGEST.get()
    if did is None:
        return
    with _LOCK:
        snap = _LIVE.get(int(did))
        if snap is not None:
            snap.pool_added_this_run += 1


def record_links_found_paid(digest_id: int | None = None, *, count: int = 1) -> None:
    if count <= 0:
        return
    did = int(digest_id) if digest_id is not None else _BOUND_DIGEST.get()
    if did is None:
        return
    with _LOCK:
        snap = _LIVE.get(int(did))
        if snap is not None:
            snap.links_found_paid += int(count)


def record_links_found_free(digest_id: int | None = None, *, count: int = 1) -> None:
    if count <= 0:
        return
    did = int(digest_id) if digest_id is not None else _BOUND_DIGEST.get()
    if did is None:
        return
    with _LOCK:
        snap = _LIVE.get(int(did))
        if snap is not None:
            snap.links_found_free += int(count)


def sync_live_from_web_search_stats(digest_id: int | None = None) -> None:
    """Подтянуть счётчики ProxyAPI web_search в live-снимок."""
    from app.services.step1_web_search_stats import current_step1_web_search_stats

    stats = current_step1_web_search_stats()
    if stats is None:
        return
    meta = stats.to_meta()
    citations = int(meta.get("web_search_citation_urls") or 0)
    bump_live_progress(
        digest_id,
        web_search_api_calls=int(meta.get("web_search_api_calls") or 0),
        web_search_citation_urls=citations,
        web_search_cost_est_rub=float(meta.get("web_search_cost_est_rub") or 0.0),
        links_found_paid=citations,
    )


def _format_elapsed(sec: int) -> str:
    sec = max(0, int(sec))
    if sec < 60:
        return f"{sec} с"
    m, s = divmod(sec, 60)
    return f"{m} мин {s} с" if s else f"{m} мин"


def _derive_links_found(
    *,
    links_found_paid: int,
    links_found_free: int,
    web_search_citation_urls: int,
    urls_raw: int,
    urls_sent_to_http: int,
    urls_raw_merged: int,
) -> tuple[int, int, int]:
    paid = max(links_found_paid, web_search_citation_urls, urls_raw, max(0, urls_raw_merged - links_found_free))
    free = max(0, links_found_free)
    total = paid + free
    if total == 0:
        total = max(urls_sent_to_http, urls_raw_merged)
        if total > 0 and paid == 0 and free == 0:
            paid = total
    return paid, free, total


def _derive_funnel_counts(
    *,
    rejected_links: int,
    pool_added_this_run: int,
    reject_reason_events: int,
    links_found_total: int,
    pool_carried_over: int,
) -> dict[str, Any]:
    rejected_links = max(0, int(rejected_links))
    pool_added_this_run = max(0, int(pool_added_this_run))
    links_checked = rejected_links + pool_added_this_run
    pool_yield_pct: float | None = None
    if links_checked > 0:
        pool_yield_pct = round(100.0 * pool_added_this_run / links_checked, 1)
    recheck_only = links_checked > links_found_total > 0 and pool_carried_over > 0
    return {
        "rejected_links": rejected_links,
        "reject_reason_events": max(0, int(reject_reason_events)),
        "links_checked": links_checked,
        "pool_yield_pct": pool_yield_pct,
        "recheck_only": recheck_only,
    }


def _build_live_payload(snap: Step1LiveSnapshot, elapsed: int) -> dict[str, Any]:
    pool_carried_over = max(0, snap.pool_carried_over)
    pool_added_this_run = max(0, snap.pool_added_this_run)
    verified_pool = max(0, snap.verified_pool)
    links_found_paid, links_found_free, links_found_total = _derive_links_found(
        links_found_paid=snap.links_found_paid,
        links_found_free=snap.links_found_free,
        web_search_citation_urls=snap.web_search_citation_urls,
        urls_raw=snap.urls_raw,
        urls_sent_to_http=snap.urls_sent_to_http,
        urls_raw_merged=snap.urls_raw_merged,
    )
    funnel = _derive_funnel_counts(
        rejected_links=snap.rejected_links,
        pool_added_this_run=pool_added_this_run,
        reject_reason_events=snap.rejected_total,
        links_found_total=links_found_total,
        pool_carried_over=pool_carried_over,
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
        "verified_pool": verified_pool,
        "rejected_total": funnel["rejected_links"],
        "rejected_links": funnel["rejected_links"],
        "reject_reason_events": funnel["reject_reason_events"],
        "collection_target": snap.collection_target,
        "cancel_requested": bool(snap.cancel_requested),
        "pool_carried_over": pool_carried_over,
        "pool_added_this_run": pool_added_this_run,
        "links_found_paid": links_found_paid,
        "links_found_free": links_found_free,
        "links_found_total": links_found_total,
        "links_processed": funnel["links_checked"],
        "links_checked": funnel["links_checked"],
        "pool_yield_pct": funnel["pool_yield_pct"],
        "recheck_only": funnel["recheck_only"],
    }


def live_payload_from_meta(meta: dict[str, Any]) -> dict[str, Any]:
    """Снимок после завершения сбора (из step1_collection_meta)."""
    elapsed = int(meta.get("elapsed_sec") or 0)
    urls_raw_merged = int(meta.get("urls_raw_merged") or 0)
    urls_sent_to_http = int(meta.get("urls_sent_to_http") or 0)
    links_found_free = int(meta.get("links_found_free") or 0)
    links_found_free = max(links_found_free, int(meta.get("urls_raw_from_seed_listings") or 0))
    links_found_paid, links_found_free, links_found_total = _derive_links_found(
        links_found_paid=int(meta.get("links_found_paid") or 0),
        links_found_free=links_found_free,
        web_search_citation_urls=int(meta.get("web_search_citation_urls") or 0),
        urls_raw=max(int(meta.get("urls_raw_unique") or 0), urls_raw_merged),
        urls_sent_to_http=urls_sent_to_http,
        urls_raw_merged=urls_raw_merged,
    )
    pool_carried_over = int(meta.get("pool_carried_over") or 0)
    pool_added_this_run = int(meta.get("pool_added_this_run") or 0)
    reject_reason_events = int(meta.get("reject_reason_events") or meta.get("rejected_total") or 0)
    rejected_links = int(meta.get("rejected_links") or 0)
    if rejected_links <= 0 and reject_reason_events > 0:
        legacy_checked = int(meta.get("links_checked") or meta.get("links_processed") or 0)
        if legacy_checked > 0:
            rejected_links = max(0, legacy_checked - pool_added_this_run)
        else:
            rejected_links = reject_reason_events
    verified_pool = int(meta.get("verified_total") or 0)
    funnel = _derive_funnel_counts(
        rejected_links=rejected_links,
        pool_added_this_run=pool_added_this_run,
        reject_reason_events=reject_reason_events,
        links_found_total=links_found_total,
        pool_carried_over=pool_carried_over,
    )
    return {
        "running": False,
        "phase": "Сбор завершён",
        "phase_key": "done",
        "elapsed_sec": elapsed,
        "elapsed_human": _format_elapsed(elapsed),
        "iteration": int(meta.get("iterations") or 0),
        "web_search_api_calls": int(meta.get("web_search_api_calls") or 0),
        "web_search_citation_urls": int(meta.get("web_search_citation_urls") or 0),
        "web_search_cost_est_rub": float(meta.get("web_search_cost_est_rub") or 0.0),
        "urls_raw": max(int(meta.get("urls_raw_unique") or 0), int(meta.get("urls_raw_merged") or 0)),
        "urls_raw_merged": int(meta.get("urls_raw_merged") or 0),
        "urls_prefilter_rejected": int(meta.get("urls_prefilter_rejected") or 0),
        "urls_sent_to_http": int(meta.get("urls_sent_to_http") or 0),
        "verified_pool": verified_pool,
        "rejected_total": funnel["rejected_links"],
        "rejected_links": funnel["rejected_links"],
        "reject_reason_events": funnel["reject_reason_events"],
        "collection_target": int(meta.get("collection_target_pages") or 15),
        "cancel_requested": False,
        "pool_carried_over": pool_carried_over,
        "pool_added_this_run": pool_added_this_run,
        "links_found_paid": links_found_paid,
        "links_found_free": links_found_free,
        "links_found_total": links_found_total,
        "links_processed": funnel["links_checked"],
        "links_checked": funnel["links_checked"],
        "pool_yield_pct": funnel["pool_yield_pct"],
        "recheck_only": funnel["recheck_only"],
    }


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
        return _build_live_payload(snap, elapsed)


def on_step1_phase_enter(phase_key: str) -> None:
    did = _BOUND_DIGEST.get()
    if did is not None:
        touch_live_phase(did, phase_key)
