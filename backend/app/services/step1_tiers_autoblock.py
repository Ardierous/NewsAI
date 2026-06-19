"""Автодобавление доменов в blocked_search_hosts при частых http_unreachable."""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

from sqlalchemy.orm import Session

from app.models import Step1HostUnreachableStat
from app.services.digest_type_policy import is_curious_digest, normalize_digest_type

if TYPE_CHECKING:
    from app.config import Settings

logger = logging.getLogger(__name__)

_SECTION_RE = re.compile(r"^\[blocked_search_hosts\]\s*$", re.MULTILINE)
_AUTO_MARKER = "# auto-unreachable:"


def tiers_path_for_digest_type(settings: Any, digest_type: str | None) -> Path:
    dtype = normalize_digest_type(digest_type)
    if is_curious_digest(dtype):
        return Path(settings.curious_source_hosts_path)
    return Path(settings.source_tiers_path)


def append_blocked_search_host(path: Path, host: str, *, note: str) -> bool:
    """Добавляет host в [blocked_search_hosts], если его ещё нет. Возвращает True при изменении файла."""
    clean = str(host or "").strip().lower().removeprefix("www.")
    if not clean or "." not in clean:
        return False
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        logger.warning("Autoblock tiers: не удалось прочитать %s", path)
        return False
    if re.search(rf"(?m)^\s*{re.escape(clean)}\s*$", text):
        return False
    m = _SECTION_RE.search(text)
    if not m:
        logger.warning("Autoblock tiers: секция [blocked_search_hosts] не найдена в %s", path)
        return False
    insert_at = m.end()
    block = f"\n{_AUTO_MARKER} {note}\n{clean}\n"
    new_text = text[:insert_at] + block + text[insert_at:]
    path.write_text(new_text, encoding="utf-8")
    logger.warning(
        "Autoblock tiers: домен %s добавлен в blocked_search_hosts | file=%s",
        clean,
        path.name,
    )
    return True


def record_host_unreachable(db: Session, url: str) -> None:
    from urllib.parse import urlparse

    host = (urlparse(str(url or "").strip()).hostname or "").lower().removeprefix("www.")
    if not host or host in {"manual", "unknown", "localhost"}:
        return
    row = db.get(Step1HostUnreachableStat, host)
    now = datetime.utcnow()
    if row is None:
        row = Step1HostUnreachableStat(
            host=host,
            failure_count=1,
            first_failure_at=now,
            last_failure_at=now,
        )
        db.add(row)
    else:
        row.failure_count = int(row.failure_count or 0) + 1
        row.last_failure_at = now
    db.flush()


def sync_autoblocked_hosts(
    db: Session,
    settings: "Settings",
    *,
    digest_type: str | None = None,
    window_days: int | None = None,
    threshold: int | None = None,
) -> list[str]:
    """Домены с threshold+ недоступностей за window_days → blocked_search_hosts в tiers."""
    window = max(1, int(window_days or getattr(settings, "step1_url_registry_ttl_days", 90) or 90))
    limit = max(1, int(threshold or getattr(settings, "step1_host_unreachable_autoblock_threshold", 20) or 20))
    cutoff = datetime.utcnow() - timedelta(days=window)
    rows = (
        db.query(Step1HostUnreachableStat)
        .filter(
            Step1HostUnreachableStat.failure_count >= limit,
            Step1HostUnreachableStat.last_failure_at >= cutoff,
            Step1HostUnreachableStat.autoblocked_at.is_(None),
        )
        .all()
    )
    if not rows:
        return []
    tiers_path = tiers_path_for_digest_type(settings, digest_type)
    blocked: list[str] = []
    now = datetime.utcnow()
    for row in rows:
        note = f"{row.failure_count}+ http_unreachable за {window} дн. ({now.date().isoformat()})"
        if append_blocked_search_host(tiers_path, row.host, note=note):
            row.autoblocked_at = now
            blocked.append(row.host)
        else:
            row.autoblocked_at = row.autoblocked_at or now
    if blocked:
        db.commit()
        try:
            from app.curious_source_policy import _cached_curious_policy
            from app.source_tiers_policy import _cached_policy

            _cached_policy.cache_clear()
            _cached_curious_policy.cache_clear()
        except Exception:
            pass
    return blocked
