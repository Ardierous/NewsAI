"""Привязка стоимости к выпуску и запускам шага 1."""
from __future__ import annotations

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import Digest, LlmCostRecord, Step1DiscoveryRun


def step1_run_cost_rub(
    db: Session,
    digest_id: int,
    run: Step1DiscoveryRun,
    *,
    step_delta_rub: float | None = None,
) -> float:
    """Стоимость одного запуска сбора шага 1.

    Запись llm_cost_records создаётся в finally после pool_formed_at, поэтому
    нельзя отрезать по pool_formed_at — привязываем по started_at и границе
    следующего запуска.
    """
    if step_delta_rub is not None and step_delta_rub > 0:
        return round(float(step_delta_rub), 4)

    started = run.started_at
    if started is None:
        return 0.0

    next_run = (
        db.query(Step1DiscoveryRun)
        .filter(
            Step1DiscoveryRun.digest_id == digest_id,
            Step1DiscoveryRun.run_number > run.run_number,
        )
        .order_by(Step1DiscoveryRun.run_number.asc())
        .first()
    )
    q = db.query(func.coalesce(func.sum(LlmCostRecord.cost_rub), 0.0)).filter(
        LlmCostRecord.digest_id == digest_id,
        LlmCostRecord.step == "step_1",
        LlmCostRecord.created_at >= started,
    )
    if next_run and next_run.started_at is not None:
        q = q.filter(LlmCostRecord.created_at < next_run.started_at)
    return round(float(q.scalar() or 0.0), 4)


def _positive_delta_rub(start: float, end: float) -> float | None:
    spent = float(start) - float(end)
    if spent > 1e-6:
        return round(spent, 4)
    return None


def _positive_budget_delta_rub(start: float, end: float) -> float | None:
    spent = float(end) - float(start)
    if spent > 1e-6:
        return round(spent, 4)
    return None


def digest_proxyapi_snapshot_spent_rub(digest: Digest) -> float | None:
    """Расход по сохранённым снимкам ProxyAPI на модели Digest (без live API)."""
    if (
        digest.proxyapi_budget_used_session_start is not None
        and digest.proxyapi_budget_used_after is not None
    ):
        return _positive_budget_delta_rub(
            digest.proxyapi_budget_used_session_start,
            digest.proxyapi_budget_used_after,
        )
    if digest.proxyapi_balance_session_start is not None and digest.proxyapi_balance_after is not None:
        return _positive_delta_rub(digest.proxyapi_balance_session_start, digest.proxyapi_balance_after)
    if digest.proxyapi_budget_used_before is not None and digest.proxyapi_budget_used_after is not None:
        return _positive_budget_delta_rub(
            digest.proxyapi_budget_used_before,
            digest.proxyapi_budget_used_after,
        )
    if digest.proxyapi_balance_before is not None and digest.proxyapi_balance_after is not None:
        return _positive_delta_rub(digest.proxyapi_balance_before, digest.proxyapi_balance_after)
    return None


def digest_release_spent_rub(
    digest: Digest,
    *,
    live_balance: float | None = None,
    live_budget_used: float | None = None,
) -> float | None:
    """Накопительный расход ProxyAPI по выпуску: открытие выпуска → текущий баланс/бюджет."""
    if digest.proxyapi_finalized_cost_rub is not None:
        return round(float(digest.proxyapi_finalized_cost_rub), 4)

    open_budget = digest.proxyapi_release_open_budget_used
    if open_budget is not None and live_budget_used is not None:
        delta = _positive_budget_delta_rub(float(open_budget), float(live_budget_used))
        if delta is not None:
            return delta

    open_balance = digest.proxyapi_release_open_balance
    if open_balance is None and digest.proxyapi_balance_session_start is not None:
        open_balance = digest.proxyapi_balance_session_start
    if open_balance is not None and live_balance is not None:
        delta = _positive_delta_rub(float(open_balance), float(live_balance))
        if delta is not None:
            return delta

    return digest_proxyapi_spent_rub(
        digest,
        live_balance=live_balance,
        live_budget_used=live_budget_used,
    )


def digest_proxyapi_spent_rub(
    digest: Digest,
    *,
    live_balance: float | None = None,
    live_budget_used: float | None = None,
    prev_anchor_balance: float | None = None,
) -> float | None:
    """Расход по выпуску: закрытые снимки, live-хвост или якорь предыдущего выпуска."""
    spent = digest_proxyapi_snapshot_spent_rub(digest)
    if spent is not None:
        return spent

    if live_budget_used is not None and digest.proxyapi_budget_used_session_start is not None:
        delta = _positive_budget_delta_rub(
            digest.proxyapi_budget_used_session_start,
            live_budget_used,
        )
        if delta is not None:
            return delta
    if live_balance is not None and digest.proxyapi_balance_session_start is not None:
        delta = _positive_delta_rub(digest.proxyapi_balance_session_start, live_balance)
        if delta is not None:
            return delta

    if live_budget_used is not None and digest.proxyapi_budget_used_before is not None:
        delta = _positive_budget_delta_rub(digest.proxyapi_budget_used_before, live_budget_used)
        if delta is not None:
            return delta
    if live_balance is not None and digest.proxyapi_balance_before is not None:
        delta = _positive_delta_rub(digest.proxyapi_balance_before, live_balance)
        if delta is not None:
            return delta

    if (
        prev_anchor_balance is not None
        and digest.proxyapi_balance_after is not None
        and digest.proxyapi_balance_before is None
        and digest.proxyapi_balance_session_start is None
    ):
        return _positive_delta_rub(prev_anchor_balance, digest.proxyapi_balance_after)

    return None


def compute_digest_total_cost_rub(
    *,
    records_sum_rub: float,
    session_spent_rub: float | None,
    release_spent_rub: float | None = None,
    finalized_cost_rub: float | None = None,
) -> float:
    """Сумма по выпуску: зафиксированная стоимость или накопительный расход ProxyAPI с начала выпуска."""
    if finalized_cost_rub is not None:
        return round(float(finalized_cost_rub), 4)
    if release_spent_rub is not None and float(release_spent_rub) > 1e-6:
        return round(float(release_spent_rub), 4)
    committed = round(float(records_sum_rub or 0.0), 4)
    session = round(float(session_spent_rub), 4) if session_spent_rub is not None else None
    if session is not None and session > committed + 1e-4:
        return session
    return committed
