"""Локальный кэш сырых URL от ProxyAPI web_search (SQLite, TTL 90 дней по умолчанию)."""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from sqlalchemy.orm import Session

from app.models import Step1WebSearchCache

if TYPE_CHECKING:
    from app.config import Settings

logger = logging.getLogger(__name__)

_CACHE_SCHEMA_VERSION = 1


def web_search_cache_ttl_days(settings: Any) -> int:
    return max(1, int(getattr(settings, "step1_web_search_cache_ttl_days", 90) or 90))


def web_search_cache_enabled(settings: Any) -> bool:
    return bool(getattr(settings, "step1_web_search_cache_enabled", True))


def build_web_search_cache_key(
    *,
    query: str,
    limit: int,
    search_context_size: str | None,
    allowed_hosts: list[str] | None,
    curious_search: bool,
    proxy_fallback_on_empty: bool,
) -> str:
    normalized_query = " ".join(str(query or "").split())
    hosts = sorted({str(h or "").strip().lower() for h in (allowed_hosts or []) if str(h or "").strip()})
    payload = {
        "v": _CACHE_SCHEMA_VERSION,
        "query": normalized_query,
        "limit": int(limit),
        "ctx": str(search_context_size or "").strip().lower(),
        "hosts": hosts,
        "curious": bool(curious_search),
    }
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _filter_urls_for_query_window(query: str, urls: list[str]) -> list[str]:
    from app.services.news_search import parse_search_window_dates, search_url_path_date_outside_window

    earliest, anchor = parse_search_window_dates(query)
    if earliest is None and anchor is None:
        return list(urls)
    kept: list[str] = []
    for url in urls:
        if search_url_path_date_outside_window(url, earliest=earliest, anchor=anchor):
            continue
        kept.append(url)
    return kept


def _open_db() -> Session:
    from app.database import SessionLocal

    return SessionLocal()


def purge_expired_web_search_cache(
    settings: Any,
    *,
    db: Session | None = None,
) -> int:
    """Удаляет записи старше TTL. Возвращает число удалённых строк."""
    if not web_search_cache_enabled(settings):
        return 0
    owns = db is None
    if owns:
        db = _open_db()
    try:
        cutoff = datetime.utcnow() - timedelta(days=web_search_cache_ttl_days(settings))
        deleted = (
            db.query(Step1WebSearchCache)
            .filter(Step1WebSearchCache.created_at < cutoff)
            .delete(synchronize_session=False)
        )
        if deleted:
            db.commit()
            logger.info("Web search cache: purge expired | deleted=%s ttl_days=%s", deleted, web_search_cache_ttl_days(settings))
        elif owns:
            db.rollback()
        return int(deleted or 0)
    except Exception:
        if owns:
            db.rollback()
        logger.warning("Web search cache: purge failed", exc_info=True)
        return 0
    finally:
        if owns:
            db.close()


def get_cached_proxy_search_urls(
    settings: "Settings",
    *,
    query: str,
    limit: int,
    search_context_size: str | None,
    allowed_hosts: list[str] | None,
    curious_search: bool,
    proxy_fallback_on_empty: bool,
    db: Session | None = None,
) -> list[str] | None:
    if not web_search_cache_enabled(settings):
        return None
    cache_key = build_web_search_cache_key(
        query=query,
        limit=limit,
        search_context_size=search_context_size,
        allowed_hosts=allowed_hosts,
        curious_search=curious_search,
        proxy_fallback_on_empty=proxy_fallback_on_empty,
    )
    owns = db is None
    if owns:
        db = _open_db()
    try:
        row = db.get(Step1WebSearchCache, cache_key)
        if row is None:
            return None
        ttl = timedelta(days=web_search_cache_ttl_days(settings))
        if row.created_at < datetime.utcnow() - ttl:
            db.delete(row)
            db.commit()
            return None
        try:
            urls = json.loads(row.urls_json or "[]")
        except json.JSONDecodeError:
            db.delete(row)
            db.commit()
            return None
        if not isinstance(urls, list):
            db.delete(row)
            db.commit()
            return None
        urls = [str(u).strip() for u in urls if str(u).strip().startswith("http")]
        urls = _filter_urls_for_query_window(query, urls)
        if not urls:
            db.delete(row)
            db.commit()
            return None
        row.hit_count = int(row.hit_count or 0) + 1
        row.last_hit_at = datetime.utcnow()
        db.commit()
        try:
            from app.services.step1_web_search_stats import record_web_search_cache_hit

            record_web_search_cache_hit()
        except ImportError:
            pass
        logger.info(
            "Web search cache: hit | key=%s count=%s hits=%s age_days=%s",
            cache_key[:12],
            len(urls),
            row.hit_count,
            (datetime.utcnow() - row.created_at).days,
        )
        return urls[: max(1, int(limit))]
    except Exception:
        if owns:
            db.rollback()
        logger.warning("Web search cache: read failed", exc_info=True)
        return None
    finally:
        if owns:
            db.close()


def store_proxy_search_urls_cache(
    settings: "Settings",
    urls: list[str],
    *,
    query: str,
    limit: int,
    search_context_size: str | None,
    allowed_hosts: list[str] | None,
    curious_search: bool,
    proxy_fallback_on_empty: bool,
    db: Session | None = None,
) -> None:
    if not web_search_cache_enabled(settings):
        return
    clean = [str(u).strip() for u in urls if str(u).strip().startswith("http")]
    if not clean:
        return
    cache_key = build_web_search_cache_key(
        query=query,
        limit=limit,
        search_context_size=search_context_size,
        allowed_hosts=allowed_hosts,
        curious_search=curious_search,
        proxy_fallback_on_empty=proxy_fallback_on_empty,
    )
    owns = db is None
    if owns:
        db = _open_db()
    try:
        preview = " ".join(str(query or "").split())[:480]
        row = db.get(Step1WebSearchCache, cache_key)
        payload = json.dumps(clean, ensure_ascii=False)
        now = datetime.utcnow()
        if row is None:
            row = Step1WebSearchCache(
                cache_key=cache_key,
                urls_json=payload,
                query_preview=preview,
                url_count=len(clean),
                hit_count=0,
                created_at=now,
            )
            db.add(row)
        else:
            row.urls_json = payload
            row.query_preview = preview
            row.url_count = len(clean)
            row.created_at = now
            row.last_hit_at = None
        db.commit()
        logger.info(
            "Web search cache: store | key=%s count=%s ttl_days=%s",
            cache_key[:12],
            len(clean),
            web_search_cache_ttl_days(settings),
        )
    except Exception:
        if owns:
            db.rollback()
        logger.warning("Web search cache: store failed", exc_info=True)
    finally:
        if owns:
            db.close()
