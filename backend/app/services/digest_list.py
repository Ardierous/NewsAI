from __future__ import annotations

import re
from collections import defaultdict
from datetime import date, datetime

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import Asset, Digest, LlmCostRecord, NewsCandidate, SelectedNews
from app.services.cost_attribution import (
    compute_digest_total_cost_rub,
    digest_proxyapi_spent_rub,
    digest_release_spent_rub,
)
from app.services.digest_status_labels import digest_status_label_ru

_SUMMARY_MAX_LEN = 220


def _truncate_summary(text: str, max_len: int = _SUMMARY_MAX_LEN) -> str:
    s = re.sub(r"\s+", " ", str(text or "")).strip()
    if not s:
        return ""
    if len(s) <= max_len:
        return s
    cut = s[: max_len - 1].rsplit(" ", 1)[0]
    return (cut or s[: max_len - 1]).rstrip() + "…"


def _format_digest_date_label(raw: date | datetime) -> str:
    if isinstance(raw, datetime):
        d = raw.date()
    else:
        d = raw
    return d.strftime("%d.%m.%Y")


def _fallback_summary_title(digest: Digest) -> str:
    type_ru = "деловой" if (digest.digest_type or "serious") == "serious" else "курьёзный"
    return f"Дайджест за {_format_digest_date_label(digest.date)} ({type_ru} тон)"


def build_digest_list_payload(db: Session, digests: list[Digest]) -> list[dict]:
    if not digests:
        return []
    ids = [d.id for d in digests]

    cost_by_id: dict[int, float] = {}
    for digest_id, total in (
        db.query(LlmCostRecord.digest_id, func.coalesce(func.sum(LlmCostRecord.cost_rub), 0.0))
        .filter(LlmCostRecord.digest_id.in_(ids))
        .group_by(LlmCostRecord.digest_id)
        .all()
    ):
        cost_by_id[int(digest_id)] = round(float(total or 0.0), 4)

    overall_by_id: dict[int, str] = {}
    for asset in db.query(Asset).filter(Asset.digest_id.in_(ids), Asset.type == "overall_analysis").all():
        text = str(asset.prompt or "").strip()
        if text:
            overall_by_id[asset.digest_id] = text

    top5_by_id: dict[int, list[dict]] = defaultdict(list)
    selected_rows = (
        db.query(SelectedNews, NewsCandidate)
        .join(NewsCandidate, NewsCandidate.id == SelectedNews.candidate_id)
        .filter(SelectedNews.digest_id.in_(ids))
        .order_by(SelectedNews.digest_id.asc(), SelectedNews.output_position.asc())
        .all()
    )
    for sel, cand in selected_rows:
        top5_by_id[sel.digest_id].append(
            {
                "position": int(sel.output_position),
                "title": str(cand.title or "").strip(),
                "source": str(cand.source or "").strip() or None,
            }
        )

    # Без live-опроса баланса на каждый GET списка.
    live_balance: float | None = None
    live_budget_used: float | None = None

    cost_by_digest_id: dict[int, float] = {}
    prev_anchor_balance: float | None = None
    for digest in sorted(digests, key=lambda item: item.id):
        session_spent = digest_proxyapi_spent_rub(
            digest,
            live_balance=live_balance,
            live_budget_used=live_budget_used,
            prev_anchor_balance=prev_anchor_balance,
        )
        release_spent = digest_release_spent_rub(
            digest,
            live_balance=live_balance,
            live_budget_used=live_budget_used,
        )
        cost_by_digest_id[digest.id] = compute_digest_total_cost_rub(
            records_sum_rub=cost_by_id.get(digest.id, 0.0),
            session_spent_rub=session_spent,
            release_spent_rub=release_spent,
            finalized_cost_rub=digest.proxyapi_finalized_cost_rub,
        )
        if digest.proxyapi_balance_after is not None:
            prev_anchor_balance = float(digest.proxyapi_balance_after)
        elif session_spent is not None and prev_anchor_balance is not None:
            prev_anchor_balance = float(prev_anchor_balance) - float(session_spent)
        elif digest.proxyapi_balance_session_start is not None and live_balance is not None:
            prev_anchor_balance = float(live_balance)

    out: list[dict] = []
    for digest in digests:
        overall = overall_by_id.get(digest.id, "")
        summary_title = _truncate_summary(overall) if overall else _fallback_summary_title(digest)
        out.append(
            {
                "id": digest.id,
                "date": digest.date,
                "digest_type": digest.digest_type,
                "digest_type_via_default": bool(digest.digest_type_via_default),
                "news_window_days": digest.news_window_days,
                "news_window_day_kind": digest.news_window_day_kind,
                "status": digest.status,
                "current_step": digest.current_step,
                "status_label_ru": digest_status_label_ru(digest.status),
                "created_at": digest.created_at,
                "updated_at": digest.updated_at,
                "summary_title": summary_title,
                "top5": top5_by_id.get(digest.id, []),
                "total_cost_rub": cost_by_digest_id.get(digest.id, 0.0),
            }
        )
    return out
