from __future__ import annotations

from datetime import datetime

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import LlmCostRecord, NewsCandidate, Step1DiscoveryRun
from app.services.cost_attribution import step1_run_cost_rub
from app.services.cost_labels import enrich_llm_cost_row
from app.services.digest_service import (
    _host_from_url,
    _is_russian_host,
    _publisher_host_key,
)
from app.services.step1_candidate_policy import is_substantive_press_for_pool
from app.services.step1_usage_breakdown import build_step1_usage_breakdown, resolve_step1_proxyapi_cost_rub


def format_duration(seconds: int | None) -> str:
    if seconds is None or seconds < 0:
        return "—"
    m, s = divmod(seconds, 60)
    if m > 0:
        return f"{m} мин {s} с"
    return f"{s} с"


def _read_step1_collection_meta(db: Session, digest_id: int) -> dict:
    from app.models import Asset
    import json

    row = (
        db.query(Asset)
        .filter(Asset.digest_id == digest_id, Asset.type == "step1_collection_meta")
        .order_by(Asset.id.desc())
        .first()
    )
    if not row or not row.prompt:
        return {}
    try:
        raw = json.loads(row.prompt)
        return raw if isinstance(raw, dict) else {}
    except json.JSONDecodeError:
        return {}


def build_pool_collection_stats(db: Session, digest_id: int) -> dict:
    pool = _pool_stats(db, digest_id)
    history = _discovery_run_history(db, digest_id)
    last_run = history[0] if history else None
    step1_costs = _step1_cost_breakdown(db, digest_id)
    step1_total_rub = round(sum(float(r.get("cost_rub") or 0.0) for r in step1_costs), 4)
    collection_meta = _read_step1_collection_meta(db, digest_id)
    step1_usage = build_step1_usage_breakdown(
        collection_meta,
        step1_costs=step1_costs,
        step1_total_rub=step1_total_rub,
        last_run_cost_rub=float(last_run.get("cost_rub") or 0.0) if last_run else None,
    )
    if last_run and step1_usage:
        resolved_cost, _ = resolve_step1_proxyapi_cost_rub(
            collection_meta,
            step1_total_rub=step1_total_rub,
            last_run_cost_rub=float(last_run.get("cost_rub") or 0.0),
            step1_costs=step1_costs,
        )
        if resolved_cost > float(last_run.get("cost_rub") or 0.0):
            last_run["cost_rub"] = resolved_cost
    if step1_usage and float(step1_usage.get("total_cost_rub") or 0) > step1_total_rub:
        step1_total_rub = round(float(step1_usage["total_cost_rub"]), 4)
    return {
        "pool": pool,
        "last_run": last_run,
        "step1_total_rub": step1_total_rub,
        "step1_costs": step1_costs,
        "step1_usage": step1_usage,
        "history": history,
    }


def _pool_stats(db: Session, digest_id: int) -> dict:
    candidates = db.query(NewsCandidate).filter(NewsCandidate.digest_id == digest_id).all()
    total = len(candidates)
    if not total:
        return {
            "total": 0,
            "press_count": 0,
            "press_share": 0.0,
            "ru_count": 0,
            "ru_share": 0.0,
            "max_per_source": 0,
            "foreign_agent_count": 0,
            "forbidden_count": 0,
        }
    source_count: dict[str, int] = {}
    press_count = 0
    ru_count = 0
    foreign_agent_count = 0
    forbidden_count = 0
    for c in candidates:
        host = _publisher_host_key({"url": c.url, "source": c.source})
        source_count[host] = source_count.get(host, 0) + 1
        item = {"url": c.url, "source": c.source, "title": c.title, "description": c.description}
        if is_substantive_press_for_pool(item):
            press_count += 1
        if _is_russian_host(host):
            ru_count += 1
        if c.is_foreign_agent:
            foreign_agent_count += 1
        if c.is_aggregator or c.reliability_status == "❗ без подтверждения":
            forbidden_count += 1
    return {
        "total": total,
        "press_count": press_count,
        "press_share": round(press_count / total, 4),
        "ru_count": ru_count,
        "ru_share": round(ru_count / total, 4),
        "max_per_source": max(source_count.values()) if source_count else 0,
        "foreign_agent_count": foreign_agent_count,
        "forbidden_count": forbidden_count,
    }


def _discovery_run_history(db: Session, digest_id: int, limit: int = 10) -> list[dict]:
    runs = (
        db.query(Step1DiscoveryRun)
        .filter(Step1DiscoveryRun.digest_id == digest_id)
        .order_by(Step1DiscoveryRun.run_number.desc())
        .limit(limit)
        .all()
    )
    out: list[dict] = []
    for run in runs:
        started = run.started_at
        ended = run.pool_formed_at
        duration_sec = run.duration_sec
        if duration_sec is None and started:
            end = ended or datetime.utcnow()
            duration_sec = max(0, int((end - started).total_seconds()))
        cost_rub = run.cost_rub
        if cost_rub is None and started:
            cost_rub = step1_run_cost_rub(db, digest_id, run)
        out.append(
            {
                "run_number": run.run_number,
                "started_at": started.isoformat() if started else None,
                "completed_at": ended.isoformat() if ended else None,
                "duration_sec": duration_sec,
                "duration_human": format_duration(duration_sec),
                "cost_rub": round(float(cost_rub or 0.0), 4),
                "news_count": run.news_count,
                "pool_candidates": None,
            }
        )
    return out


def _step1_cost_breakdown(db: Session, digest_id: int) -> list[dict]:
    rows = (
        db.query(LlmCostRecord)
        .filter(LlmCostRecord.digest_id == digest_id, LlmCostRecord.step == "step_1")
        .order_by(LlmCostRecord.id.asc())
        .all()
    )
    return [
        enrich_llm_cost_row(
            {
                "step": r.step,
                "agent_name": r.agent_name,
                "model": r.model,
                "request_label": r.request_label,
                "cost_rub": r.cost_rub,
                "created_at": r.created_at,
            }
        )
        for r in rows
    ]
