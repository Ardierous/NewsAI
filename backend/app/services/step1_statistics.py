"""Снимок аналитики шага 1: воронка, отбраковка, журнал по каждой ссылке."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.models import Asset, Digest, NewsCandidate, Step1DiscoveredNews, Step1DiscoveryRun
from app.schemas import (
    PoolCollectionStatsOut,
    Step1LinkAnalyticsOut,
    Step1StatisticsInsightsOut,
    Step1StatisticsOut,
    Step1StatisticsSummaryOut,
)
from app.services.digest_pool_stats import build_pool_collection_stats
from app.services.digest_service import (
    _candidate_url_fingerprint_sets,
    _discovered_row_verification_passed,
    _discovered_url_in_final_pool,
    _host_from_url,
)
from app.services.digest_type_policy import normalize_digest_type
from app.services.step1_url_registry import registry_bucket_counts

REJECT_REASON_LABELS_RU: dict[str, str] = {
    "aggregator_source": "ссылка ведёт на ленту или агрегатор, а не на статью",
    "http_unreachable": "страница не открылась",
    "no_article_markers": "похоже не отдельная статья",
    "news_listing_page": "это лента или рубрика, а не статья",
    "non_article_page": "нет нормального заголовка материала",
    "off_topic_not_ai": "тема не про ИИ и нейросети",
    "support_documentation_page": "справка или документация, не статья",
    "off_topic_not_curious": "сухой официоз для курьёзного выпуска",
    "excluded_from_final_pool": "прошла проверку, но не вошла в финальный список",
    "headline_low_quality": "заголовок выглядит служебным",
    "invalid_url": "неверный адрес ссылки",
    "placeholder_candidate": "учебная заглушка",
    "manual_unverified": "ручную ссылку не удалось подтвердить",
    "url_mutated_between_agents": "ссылка изменилась между агентами",
    "llm_hallucinated_url": "адрес похоже придуман моделью",
    "published_before_window": "дата публикации раньше окна шага 0",
    "published_date_undefined": "не удалось определить дату публикации",
    "url_redirect_mismatch": "редирект на другую страницу",
    "forbidden_media_source": "источник Tier-5 (запрещённые СМИ)",
    "non_policy_source": "домен вне tier-1…tier-4",
    "unknown_reject": "причина не указана",
    "duplicate_url_skip": "дубликат или уже проверялась",
    "recent_top5_repeat": "та же статья была в топ-5 недавних выпусков",
    "product_tool_page": "страница продукта, а не новость",
    "product_tool_promo": "промо инструмента",
    "duplicate_story_title": "дубликат по заголовку",
}


def reject_label_ru(code: str) -> str:
    return REJECT_REASON_LABELS_RU.get(code, code)


def _read_asset_json(db: Session, digest_id: int, asset_type: str) -> dict[str, Any]:
    row = (
        db.query(Asset)
        .filter(Asset.digest_id == digest_id, Asset.type == asset_type)
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


def _read_asset_int_map(db: Session, digest_id: int, asset_type: str) -> dict[str, int]:
    raw = _read_asset_json(db, digest_id, asset_type)
    out: dict[str, int] = {}
    for key, val in raw.items():
        try:
            out[str(key)] = int(val or 0)
        except (TypeError, ValueError):
            continue
    return out


def _link_outcome(*, in_pool: bool, verification_passed: bool, reject_codes: list[str]) -> str:
    if in_pool:
        return "in_pool"
    if verification_passed and not reject_codes:
        return "verified_only"
    return "rejected"


def build_step1_statistics(
    db: Session,
    digest_id: int,
    *,
    discovery_run_id: int | None = None,
) -> Step1StatisticsOut:
    digest = db.query(Digest).filter(Digest.id == digest_id).first()
    if digest is None:
        raise ValueError(f"digest {digest_id} not found")

    if discovery_run_id is None:
        last_run = (
            db.query(Step1DiscoveryRun)
            .filter(Step1DiscoveryRun.digest_id == digest_id)
            .order_by(Step1DiscoveryRun.id.desc())
            .first()
        )
        discovery_run_id = last_run.id if last_run else None

    candidates = db.query(NewsCandidate).filter(NewsCandidate.digest_id == digest_id).all()
    candidate_url_fps, candidate_page_fps = _candidate_url_fingerprint_sets([str(c.url or "") for c in candidates])

    discovered_rows = (
        db.query(Step1DiscoveredNews)
        .filter(Step1DiscoveredNews.digest_id == digest_id)
        .order_by(Step1DiscoveredNews.id.asc())
        .all()
    )

    links: list[Step1LinkAnalyticsOut] = []
    in_pool = 0
    rejected = 0
    verified_passed = 0

    for row in discovered_rows:
        reject_codes = [x for x in str(row.reject_codes or "").split(",") if x]
        verification_passed = _discovered_row_verification_passed(row)
        pool_match = _discovered_url_in_final_pool(
            str(row.url or ""),
            url_fps=candidate_url_fps,
            page_fps=candidate_page_fps,
        ) if (candidate_url_fps or candidate_page_fps) else False
        in_candidate_pool = pool_match
        outcome = _link_outcome(
            in_pool=in_candidate_pool,
            verification_passed=verification_passed,
            reject_codes=reject_codes,
        )
        if in_candidate_pool:
            in_pool += 1
        elif outcome == "rejected":
            rejected += 1
        if verification_passed:
            verified_passed += 1

        links.append(
            Step1LinkAnalyticsOut(
                id=row.id,
                url=row.url,
                host=_host_from_url(str(row.url or "")),
                title=row.title,
                source=row.source,
                published_at=row.published_at,
                source_stage=row.source_stage,
                outcome=outcome,
                link_status=bool(row.link_status),
                headline_editorial_ok=bool(row.headline_editorial_ok),
                page_verification_passed=verification_passed,
                in_candidate_pool=in_candidate_pool,
                reject_codes=reject_codes,
                reject_labels=[reject_label_ru(c) for c in reject_codes],
                verification_comment=row.verification_comment or "",
            )
        )

    pool_stats = PoolCollectionStatsOut.model_validate(build_pool_collection_stats(db, digest_id))
    step1_collection_meta = _read_asset_json(db, digest_id, "step1_collection_meta")
    rejected_reasons_summary = _read_asset_int_map(db, digest_id, "step1_rejected_reasons")
    step1_reject_audit = _read_asset_json(db, digest_id, "step1_reject_audit")
    filter_counters = _read_asset_int_map(db, digest_id, "step1_filter_counters")
    curious_tone_audit = _read_asset_json(db, digest_id, "step1_curious_tone_audit")
    dtype = normalize_digest_type(digest.digest_type)
    registry_buckets = registry_bucket_counts(db)
    summary_out = Step1StatisticsSummaryOut(
        total_links=len(links),
        in_pool=in_pool,
        rejected=rejected,
        verified_passed=verified_passed,
    )
    step1_cost_rub = float(pool_stats.step1_total_rub or 0.0)
    from app.services.step1_statistics_insights import build_step1_insights

    insights = Step1StatisticsInsightsOut.model_validate(
        build_step1_insights(
            digest_type=dtype,
            rejected_summary=rejected_reasons_summary,
            step1_collection_meta=step1_collection_meta,
            summary=summary_out.model_dump(),
            registry_buckets=registry_buckets,
            step1_cost_rub=step1_cost_rub,
        )
    )

    return Step1StatisticsOut(
        digest_id=digest_id,
        digest_type=dtype,
        discovery_run_id=discovery_run_id,
        generated_at=datetime.utcnow(),
        summary=summary_out,
        step1_collection_meta=step1_collection_meta,
        rejected_reasons_summary=rejected_reasons_summary,
        step1_reject_audit=step1_reject_audit,
        curious_tone_audit=curious_tone_audit,
        pool_collection_stats=pool_stats,
        registry_buckets=registry_buckets,
        filter_counters=filter_counters,
        links=links,
        insights=insights,
    )


def persist_step1_statistics_snapshot(
    db: Session,
    digest_id: int,
    *,
    discovery_run_id: int | None = None,
) -> Step1StatisticsOut:
    payload = build_step1_statistics(db, digest_id, discovery_run_id=discovery_run_id)
    db.query(Asset).filter(Asset.digest_id == digest_id, Asset.type == "step1_statistics_snapshot").delete()
    db.add(
        Asset(
            digest_id=digest_id,
            type="step1_statistics_snapshot",
            path="",
            prompt=json.dumps(payload.model_dump(mode="json"), ensure_ascii=False),
        )
    )
    db.commit()
    return payload


def load_step1_statistics_snapshot(db: Session, digest_id: int) -> Step1StatisticsOut | None:
    raw = _read_asset_json(db, digest_id, "step1_statistics_snapshot")
    if not raw:
        return None
    try:
        snap = Step1StatisticsOut.model_validate(raw)
    except Exception:
        return None
    return _ensure_statistics_insights(snap)


def _ensure_statistics_insights(snap: Step1StatisticsOut) -> Step1StatisticsOut:
    if snap.insights is not None and snap.insights.headline:
        return snap
    from app.services.step1_statistics_insights import build_step1_insights

    cost = float(snap.pool_collection_stats.step1_total_rub or 0.0)
    insights = Step1StatisticsInsightsOut.model_validate(
        build_step1_insights(
            digest_type=snap.digest_type,
            rejected_summary=snap.rejected_reasons_summary,
            step1_collection_meta=snap.step1_collection_meta,
            summary=snap.summary.model_dump(),
            registry_buckets=snap.registry_buckets,
            step1_cost_rub=cost,
        )
    )
    return snap.model_copy(update={"insights": insights})
