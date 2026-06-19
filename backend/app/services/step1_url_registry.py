"""Реестр сырых и отбракованных URL шага 1: разделы raw / verified / reject:*, TTL, re-verify.

Единый для serious и curious — тип выпуска влияет только на фильтры при verify, не на ключ в БД.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

from sqlalchemy.orm import Session

from app.models import Step1FilterEnabledSnapshot, Step1UrlRegistry
from app.services.digest_type_policy import normalize_digest_type

if TYPE_CHECKING:
    from app.config import Settings

logger = logging.getLogger(__name__)

BUCKET_RAW = "raw"
BUCKET_VERIFIED = "verified"
BUCKET_MULTI = "reject:multi"


def registry_ttl_days(settings: Any) -> int:
    return max(1, int(getattr(settings, "step1_url_registry_ttl_days", 90) or 90))


def _expires_at(settings: Any, *, base: datetime | None = None) -> datetime:
    base = base or datetime.utcnow()
    return base + timedelta(days=registry_ttl_days(settings))


def _host_from_url(url: str) -> str:
    return (urlparse(str(url or "").strip()).hostname or "").lower().removeprefix("www.")


def _url_fingerprint(url: str) -> str:
    try:
        p = urlparse(url.strip())
    except Exception:
        return ""
    host = (p.hostname or "").lower().removeprefix("www.")
    path = (p.path or "").rstrip("/") or "/"
    return f"{host}{path.lower()}"


def _find_registry_row(db: Session, url: str) -> Step1UrlRegistry | None:
    fp = _url_fingerprint(url)
    if not fp:
        return None
    row = db.query(Step1UrlRegistry).filter(Step1UrlRegistry.url_fingerprint == fp).first()
    if row is not None:
        return row
    for prefixed in (f"serious:{fp}", f"curious:{fp}"):
        row = db.query(Step1UrlRegistry).filter(Step1UrlRegistry.url_fingerprint == prefixed).first()
        if row is not None:
            row.url_fingerprint = fp
            db.flush()
            return row
    return None


def reject_codes_from_item(item: dict[str, Any]) -> list[str]:
    prefix = "REJECT_REASON:"
    codes: list[str] = []
    for token in str(item.get("verification_comment") or "").split():
        if token.startswith(prefix):
            code = token.removeprefix(prefix).strip()
            if code:
                codes.append(code)
    if not codes and str(item.get("reject_codes") or "").strip():
        codes = [c.strip() for c in str(item["reject_codes"]).split(",") if c.strip()]
    return sorted(dict.fromkeys(codes))


def bucket_for_item(item: dict[str, Any]) -> tuple[str, str]:
    if bool(item.get("link_status")) and bool(item.get("headline_editorial_ok")):
        return BUCKET_VERIFIED, ""
    codes = reject_codes_from_item(item)
    if not codes:
        return BUCKET_RAW, ""
    if len(codes) == 1:
        return f"reject:{codes[0]}", codes[0]
    return BUCKET_MULTI, ",".join(codes)


def enabled_map_from_filter_states(filters: list[dict[str, Any]]) -> dict[str, bool]:
    out: dict[str, bool] = {}
    for row in filters or []:
        fid = str(row.get("id") or "").strip()
        if fid:
            out[fid] = bool(row.get("enabled", True))
    return out


def load_filter_snapshot(db: Session, digest_type: str | None) -> dict[str, bool]:
    dtype = normalize_digest_type(digest_type)
    row = db.get(Step1FilterEnabledSnapshot, dtype)
    if row is None or not str(row.enabled_json or "").strip():
        return {}
    try:
        data = json.loads(row.enabled_json)
    except json.JSONDecodeError:
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(k): bool(v) for k, v in data.items()}


def save_filter_snapshot(db: Session, digest_type: str | None, enabled_map: dict[str, bool]) -> None:
    dtype = normalize_digest_type(digest_type)
    payload = json.dumps(enabled_map, ensure_ascii=False, sort_keys=True)
    row = db.get(Step1FilterEnabledSnapshot, dtype)
    now = datetime.utcnow()
    if row is None:
        db.add(Step1FilterEnabledSnapshot(digest_type=dtype, enabled_json=payload, updated_at=now))
    else:
        row.enabled_json = payload
        row.updated_at = now
    db.flush()


def detect_newly_disabled_filters(
    db: Session,
    digest_type: str | None,
    current_enabled: dict[str, bool],
) -> list[str]:
    previous = load_filter_snapshot(db, digest_type)
    if not previous:
        return []
    disabled: list[str] = []
    keys = set(previous) | set(current_enabled)
    for fid in keys:
        if previous.get(fid, True) and not current_enabled.get(fid, True):
            disabled.append(fid)
    return sorted(disabled)


def purge_registry_urls_outside_window(
    db: Session,
    *,
    earliest: Any,
    anchor: Any | None = None,
) -> int:
    """Удалить из реестра raw-URL с датой в пути вне окна выпуска."""
    from app.services.news_search import search_url_path_date_outside_window

    now = datetime.utcnow()
    rows = (
        db.query(Step1UrlRegistry)
        .filter(
            Step1UrlRegistry.bucket == BUCKET_RAW,
            Step1UrlRegistry.expires_at >= now,
        )
        .all()
    )
    stale_ids: list[int] = []
    for row in rows:
        url = str(row.url or "").strip()
        if url and search_url_path_date_outside_window(url, earliest=earliest, anchor=anchor):
            stale_ids.append(int(row.id))
    if not stale_ids:
        return 0
    deleted = (
        db.query(Step1UrlRegistry)
        .filter(Step1UrlRegistry.id.in_(stale_ids))
        .delete(synchronize_session=False)
    )
    db.commit()
    logger.info(
        "Step1 URL registry: purge outside window | deleted=%s earliest=%s anchor=%s",
        deleted,
        earliest,
        anchor,
    )
    return int(deleted or 0)


def purge_expired_registry(db: Session, settings: "Settings") -> int:
    cutoff = datetime.utcnow()
    deleted = (
        db.query(Step1UrlRegistry)
        .filter(Step1UrlRegistry.expires_at < cutoff)
        .delete(synchronize_session=False)
    )
    if deleted:
        db.commit()
        logger.info(
            "Step1 URL registry: purge expired | deleted=%s ttl_days=%s",
            deleted,
            registry_ttl_days(settings),
        )
    return int(deleted or 0)


def registry_bucket_counts(db: Session, digest_type: str | None = None) -> dict[str, int]:
    """Счётчики bucket — общий реестр, digest_type не используется."""
    _ = digest_type
    rows = (
        db.query(Step1UrlRegistry.bucket, Step1UrlRegistry.id)
        .filter(Step1UrlRegistry.expires_at >= datetime.utcnow())
        .all()
    )
    out: dict[str, int] = {}
    for bucket, _ in rows:
        out[bucket] = out.get(bucket, 0) + 1
    return out


def register_raw_urls(
    db: Session,
    settings: "Settings",
    *,
    urls: list[str],
    digest_type: str | None,
    digest_id: int,
    source_stage: str = "search",
) -> int:
    dtype = normalize_digest_type(digest_type)
    now = datetime.utcnow()
    exp = _expires_at(settings, base=now)
    added = 0
    pending_fps: set[str] = set()
    for raw in urls:
        url = str(raw or "").strip()
        if not url.startswith("http"):
            continue
        fp = _url_fingerprint(url)
        if not fp or fp in pending_fps:
            continue
        row = _find_registry_row(db, url)
        if row is None:
            db.add(
                Step1UrlRegistry(
                    url_fingerprint=fp,
                    url=url[:1000],
                    host=_host_from_url(url)[:255],
                    digest_type=dtype,
                    bucket=BUCKET_RAW,
                    reject_codes="",
                    title="",
                    source_stage=str(source_stage or "search")[:40],
                    verification_comment="",
                    last_digest_id=int(digest_id),
                    first_seen_at=now,
                    last_seen_at=now,
                    expires_at=exp,
                )
            )
            pending_fps.add(fp)
            added += 1
        else:
            row.last_seen_at = now
            row.expires_at = exp
            row.last_digest_id = int(digest_id)
            row.digest_type = dtype
    if added:
        db.flush()
    return added


def load_registry_raw_urls(
    db: Session,
    settings: "Settings",
    *,
    digest_type: str | None,
    limit: int,
    skip_urls: list[str] | None = None,
    skip_fingerprints: set[str] | None = None,
    earliest: Any | None = None,
    anchor: Any | None = None,
) -> list[str]:
    """Сырые URL из общего реестра (bucket=raw), без повторного web_search."""
    _ = digest_type
    if not bool(getattr(settings, "step1_url_registry_reuse_enabled", True)):
        return []
    from app.services.news_search import search_url_path_date_outside_window

    now = datetime.utcnow()
    cap = max(1, int(limit or 1))
    skip_lower = {str(u or "").strip().lower().rstrip("/") for u in (skip_urls or []) if str(u or "").strip()}
    skip_fp = set(skip_fingerprints or set())
    rows = (
        db.query(Step1UrlRegistry)
        .filter(
            Step1UrlRegistry.bucket == BUCKET_RAW,
            Step1UrlRegistry.expires_at >= now,
        )
        .order_by(Step1UrlRegistry.last_seen_at.desc())
        .limit(max(cap * 3, cap))
        .all()
    )
    out: list[str] = []
    seen: set[str] = set()
    for row in rows:
        url = str(row.url or "").strip()
        if not url.startswith("http"):
            continue
        key = url.lower().rstrip("/")
        if key in skip_lower or key in seen:
            continue
        fp = _url_fingerprint(url)
        if fp and fp in skip_fp:
            continue
        if earliest is not None and search_url_path_date_outside_window(url, earliest=earliest, anchor=anchor):
            continue
        seen.add(key)
        out.append(url)
        if len(out) >= cap:
            break
    if out:
        logger.info(
            "Реестр URL: reuse raw | count=%s limit=%s",
            len(out),
            cap,
        )
    return out


def classify_registry_item(
    db: Session,
    settings: "Settings",
    item: dict[str, Any],
    *,
    digest_type: str | None,
    digest_id: int,
) -> None:
    url = str(item.get("url") or "").strip()
    if not url.startswith("http"):
        return
    fp = _url_fingerprint(url)
    if not fp:
        return
    dtype = normalize_digest_type(digest_type)
    bucket, codes_csv = bucket_for_item(item)
    now = datetime.utcnow()
    exp = _expires_at(settings, base=now)
    title = str(item.get("title") or "").strip()[:500]
    comment = str(item.get("verification_comment") or "").strip()
    stage = str(item.get("source_stage") or item.get("category") or "search")[:40]
    row = _find_registry_row(db, url)
    if row is None:
        db.add(
            Step1UrlRegistry(
                url_fingerprint=fp,
                url=url[:1000],
                host=_host_from_url(url)[:255],
                digest_type=dtype,
                bucket=bucket,
                reject_codes=codes_csv,
                title=title,
                source_stage=stage,
                verification_comment=comment[:4000],
                last_digest_id=int(digest_id),
                first_seen_at=now,
                last_seen_at=now,
                expires_at=exp,
            )
        )
    else:
        row.url = url[:1000]
        row.host = _host_from_url(url)[:255]
        row.digest_type = dtype
        row.bucket = bucket
        row.reject_codes = codes_csv
        row.title = title or row.title
        row.source_stage = stage or row.source_stage
        row.verification_comment = comment[:4000]
        row.last_digest_id = int(digest_id)
        row.last_seen_at = now
        row.expires_at = exp
        if row.url_fingerprint != fp:
            row.url_fingerprint = fp
    db.flush()


def list_urls_for_reverify(
    db: Session,
    digest_type: str | None,
    disabled_filter_ids: list[str],
    *,
    limit: int = 80,
) -> list[Step1UrlRegistry]:
    if not disabled_filter_ids:
        return []
    _ = digest_type
    disabled = set(disabled_filter_ids)
    now = datetime.utcnow()
    rows = (
        db.query(Step1UrlRegistry)
        .filter(
            Step1UrlRegistry.bucket.like("reject:%"),
            Step1UrlRegistry.expires_at >= now,
        )
        .order_by(Step1UrlRegistry.last_seen_at.desc())
        .limit(max(limit * 3, limit))
        .all()
    )
    out: list[Step1UrlRegistry] = []
    for row in rows:
        if row.bucket == BUCKET_MULTI:
            codes = [c.strip() for c in str(row.reject_codes or "").split(",") if c.strip()]
            if not any(c in disabled for c in codes):
                continue
        elif row.bucket.startswith("reject:"):
            code = row.bucket.removeprefix("reject:")
            if code not in disabled:
                continue
        else:
            continue
        out.append(row)
        if len(out) >= limit:
            break
    return out


def registry_row_to_skeleton(row: Step1UrlRegistry, *, seq: int) -> dict[str, Any]:
    return {
        "original_number": seq,
        "title": "",
        "url": str(row.url or "")[:1000],
        "source": str(row.host or ""),
        "published_at": "",
        "category": str(row.source_stage or "registry"),
        "description": "Повторная проверка из реестра URL (снят фильтр).",
        "link_status": False,
        "headline_editorial_ok": False,
        "page_verified": False,
        "verification_comment": "",
        "source_stage": "registry_reverify",
    }
