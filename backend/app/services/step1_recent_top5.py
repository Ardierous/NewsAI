"""Исключение повторов: та же страница статьи, что уже была в топ-5 прошлых выпусков."""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING
from urllib.parse import urlparse

from sqlalchemy.orm import Session

from app.models import Digest, NewsCandidate, SelectedNews

if TYPE_CHECKING:
    pass

RECENT_TOP5_LOOKBACK_DIGESTS = 7


def article_page_fingerprint(url: str) -> str:
    """Сравнение «одна и та же страница» без учёта схемы www и хвостового /."""
    try:
        p = urlparse((url or "").strip())
    except Exception:
        return ""
    host = (p.hostname or "").lower().removeprefix("www.")
    path = (p.path or "").rstrip("/") or "/"
    return f"{host}{path.lower()}"


def query_recent_top5_url_fingerprints(
    db: Session,
    *,
    digest_id: int,
    digest_date: date,
    lookback: int = RECENT_TOP5_LOOKBACK_DIGESTS,
) -> set[str]:
    """
    Отпечатки URL страниц из топ-5 предыдущих зафиксированных выпусков
    (до digest_date, не включая digest_id; только после «Зафиксировать»).
    Другой URL (другой источник, обновление сюжета) — другой отпечаток, в пул можно.
    """
    if lookback < 1:
        return set()
    prev_rows = (
        db.query(Digest.id)
        .filter(Digest.id != digest_id)
        .filter(Digest.date < digest_date)
        .filter(Digest.proxyapi_finalized_at.isnot(None))
        .order_by(Digest.date.desc())
        .limit(lookback)
        .all()
    )
    prev_ids = [int(r[0]) for r in prev_rows]
    if not prev_ids:
        return set()
    urls = (
        db.query(NewsCandidate.url)
        .join(SelectedNews, SelectedNews.candidate_id == NewsCandidate.id)
        .filter(SelectedNews.digest_id.in_(prev_ids))
        .all()
    )
    out: set[str] = set()
    for (url,) in urls:
        fp = article_page_fingerprint(str(url or ""))
        if fp:
            out.add(fp)
    return out
