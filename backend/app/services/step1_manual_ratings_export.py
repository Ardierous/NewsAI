"""Выгрузка ручных оценок пула шага 1 в файл для последующей калибровки."""

from __future__ import annotations

import json
import logging
from datetime import date, datetime
from pathlib import Path
from typing import Any

from collections import defaultdict
from datetime import date

from sqlalchemy.orm import Session

from app.models import Digest, Step1DiscoveredNews, Step1DiscoveryRun, Step1ManualRatingLog

logger = logging.getLogger("app.step1_ratings_export")

try:
    from zoneinfo import ZoneInfo

    MSK_TZ = ZoneInfo("Europe/Moscow")
except Exception:
    MSK_TZ = None  # type: ignore[misc, assignment]

MANUAL_REASON_LABELS: dict[str, str] = {
    "published_out_of_range": "дата не в диапазоне",
    "http_unreachable": "ссылка не открылась",
    "url_redirect_mismatch": "ссылка открылась на другую страницу",
    "off_topic_not_ai": "не про ИИ",
    "other": "другое",
}


def _format_dt_msk(value: datetime | None) -> str | None:
    if value is None:
        return None
    if MSK_TZ is not None:
        if value.tzinfo is None:
            value = value.replace(tzinfo=ZoneInfo("UTC"))
        local = value.astimezone(MSK_TZ)
    else:
        local = value
    return local.strftime("%Y-%m-%d %H:%M:%S МСК")


def _pool_date_str(digest_date: date | datetime) -> str:
    if isinstance(digest_date, datetime):
        return digest_date.date().isoformat()
    return digest_date.isoformat()


def _digest_pool_date(digest: Digest) -> date:
    raw = digest.date
    if isinstance(raw, datetime):
        return raw.date()
    return raw


def _upsert_manual_rating_log(
    db: Session,
    *,
    run: Step1DiscoveryRun,
    digest: Digest,
    row: Step1DiscoveredNews,
) -> bool:
    """Возвращает True, если запись журнала создана или обновлена."""
    if row.manual_score is None:
        return False
    pool_date = _digest_pool_date(digest)
    url_key = str(row.url).strip().lower()
    run_logs = (
        db.query(Step1ManualRatingLog)
        .filter(Step1ManualRatingLog.discovery_run_id == run.id)
        .all()
    )
    existing = next((x for x in run_logs if str(x.url).strip().lower() == url_key), None)
    rated_at = row.rated_at or datetime.utcnow()
    if existing is not None:
        changed = (
            existing.manual_score != int(row.manual_score)
            or existing.manual_reason != row.manual_reason
            or existing.manual_reason_other != row.manual_reason_other
            or existing.rated_at != rated_at
            or existing.discovered_news_id != row.id
        )
        existing.discovered_news_id = row.id
        existing.title = row.title
        existing.published_at = row.published_at
        existing.manual_score = int(row.manual_score)
        existing.manual_reason = row.manual_reason
        existing.manual_reason_other = row.manual_reason_other
        existing.rated_at = rated_at
        return changed
    db.add(
        Step1ManualRatingLog(
            discovery_run_id=run.id,
            digest_id=digest.id,
            pool_date=pool_date,
            run_number=run.run_number,
            discovered_news_id=row.id,
            title=row.title,
            url=row.url,
            published_at=row.published_at,
            manual_score=int(row.manual_score),
            manual_reason=row.manual_reason,
            manual_reason_other=row.manual_reason_other,
            rated_at=rated_at,
        )
    )
    return True


def _ensure_legacy_discovery_run(db: Session, digest: Digest, rated_rows: list[Step1DiscoveredNews]) -> Step1DiscoveryRun:
    runs = (
        db.query(Step1DiscoveryRun)
        .filter(Step1DiscoveryRun.digest_id == digest.id)
        .order_by(Step1DiscoveryRun.run_number.asc(), Step1DiscoveryRun.id.asc())
        .all()
    )
    if runs:
        return next((r for r in runs if r.run_number == 1), runs[0])
    stamps = [row.rated_at or row.created_at for row in rated_rows if row.rated_at or row.created_at]
    started_at = min(stamps) if stamps else datetime.utcnow()
    pool_formed_at = max(stamps) if stamps else None
    run = Step1DiscoveryRun(
        digest_id=digest.id,
        run_number=1,
        started_at=started_at,
        pool_formed_at=pool_formed_at,
        news_count=len(rated_rows),
    )
    db.add(run)
    db.flush()
    return run


def backfill_step1_manual_ratings_from_discovered(db: Session) -> int:
    """Переносит все оценки из step1_discovered_news в журнал по всем выпускам."""
    rated_rows = (
        db.query(Step1DiscoveredNews, Digest)
        .join(Digest, Digest.id == Step1DiscoveredNews.digest_id)
        .filter(Step1DiscoveredNews.manual_score.isnot(None))
        .order_by(Step1DiscoveredNews.digest_id.asc(), Step1DiscoveredNews.id.asc())
        .all()
    )
    if not rated_rows:
        return 0

    by_digest: dict[int, list[tuple[Step1DiscoveredNews, Digest]]] = defaultdict(list)
    for row, digest in rated_rows:
        by_digest[digest.id].append((row, digest))

    touched = 0
    for digest_id, items in by_digest.items():
        digest = items[0][1]
        orphans: list[Step1DiscoveredNews] = []
        by_run_id: dict[int, list[Step1DiscoveredNews]] = defaultdict(list)
        for row, _ in items:
            if row.discovery_run_id is None:
                orphans.append(row)
            else:
                run = db.query(Step1DiscoveryRun).filter(Step1DiscoveryRun.id == row.discovery_run_id).first()
                if run is None or run.digest_id != digest_id:
                    orphans.append(row)
                else:
                    by_run_id[run.id].append(row)

        if orphans:
            legacy_run = _ensure_legacy_discovery_run(db, digest, [r for r, _ in items])
            for row in orphans:
                row.discovery_run_id = legacy_run.id
                by_run_id[legacy_run.id].append(row)

        for run_id, rows in by_run_id.items():
            run = db.query(Step1DiscoveryRun).filter(Step1DiscoveryRun.id == run_id).first()
            if run is None:
                continue
            for row in rows:
                if _upsert_manual_rating_log(db, run=run, digest=digest, row=row):
                    touched += 1

    if touched:
        db.commit()
    return touched


def sync_step1_manual_ratings_export(db: Session, export_path: Path) -> Path:
    """Синхронизирует журнал из БД и пересобирает файл выгрузки."""
    imported = backfill_step1_manual_ratings_from_discovered(db)
    if imported:
        logger.info("Импортировано/обновлено оценок в журнал из discovered: %s", imported)
    return rebuild_step1_manual_ratings_file(db, export_path)


def rebuild_step1_manual_ratings_file(db: Session, export_path: Path) -> Path:
    """Собирает файл из журнала оценок и запусков (история не теряется при пересборке пула)."""
    runs = (
        db.query(Step1DiscoveryRun, Digest)
        .join(Digest, Digest.id == Step1DiscoveryRun.digest_id)
        .order_by(Digest.date.asc(), Step1DiscoveryRun.run_number.asc(), Step1DiscoveryRun.id.asc())
        .all()
    )
    ratings = (
        db.query(Step1ManualRatingLog)
        .order_by(Step1ManualRatingLog.rated_at.asc(), Step1ManualRatingLog.id.asc())
        .all()
    )
    ratings_by_run: dict[int, list[Step1ManualRatingLog]] = {}
    for row in ratings:
        ratings_by_run.setdefault(row.discovery_run_id, []).append(row)

    by_date: dict[str, dict[str, Any]] = {}
    for run, digest in runs:
        pool_date = _pool_date_str(digest.date)
        date_bucket = by_date.setdefault(
            pool_date,
            {"pool_date": pool_date, "runs": []},
        )
        run_ratings = ratings_by_run.get(run.id, [])
        date_bucket["runs"].append(
            {
                "run_number": run.run_number,
                "run_started_at": _format_dt_msk(run.started_at),
                "pool_formed_at": _format_dt_msk(run.pool_formed_at),
                "digest_id": digest.id,
                "discovery_run_id": run.id,
                "news_in_pool": run.news_count,
                "ratings_count": len(run_ratings),
                "ratings": [
                    {
                        "rated_at": _format_dt_msk(r.rated_at),
                        "score": r.manual_score,
                        "reason_code": r.manual_reason,
                        "reason_label": MANUAL_REASON_LABELS.get(r.manual_reason or "", r.manual_reason or ""),
                        "reason_other": r.manual_reason_other,
                        "title": r.title,
                        "url": r.url,
                        "published_at": r.published_at,
                        "discovered_news_id": r.discovered_news_id,
                    }
                    for r in run_ratings
                ],
            }
        )

    payload = {
        "updated_at": _format_dt_msk(datetime.utcnow()),
        "pool_dates": [by_date[k] for k in sorted(by_date.keys())],
    }
    export_path.parent.mkdir(parents=True, exist_ok=True)
    export_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("Файл ручных оценок шага 1 обновлён | path=%s runs=%s ratings=%s", export_path, len(runs), len(ratings))
    return export_path
