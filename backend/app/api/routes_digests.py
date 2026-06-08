import json
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from datetime import datetime, time, timezone

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.models import Analytics, Asset, Digest, FinalOutput, LlmCostRecord, NewsCandidate, QualityCheck, SelectedNews, Step1DiscoveredNews
from app.services.cost_labels import enrich_llm_cost_row
from app.services.cost_tracker import proxyapi_spent_today_rub
from app.services.platform_assembly import digest_docx_filename
from app.services.digest_pool_stats import build_pool_collection_stats
from app.services.step1_manual_ratings_export import sync_step1_manual_ratings_export
from app.schemas import (
    CandidateOut,
    CommandRequest,
    DigestCreateResponse,
    DigestDetail,
    DigestItem,
    DigestListItem,
    Step1DiscoveredFeedbackRequest,
    Step1DiscoveredNewsOut,
    OrderRequest,
    SelectRequest,
    Step2AddManualUrlRequest,
    Step0Request,
    Step0Response,
    PoolCollectionStatsOut,
    NewsWindowPatch,
    Step1RunRequest,
    Step1FilterConfig,
    Step1FiltersResponse,
    Step4GenerateImagesRequest,
    Step4GenerateTextsRequest,
    Step4SelectImageRequest,
    ImageVariantOut,
    FinalizeReleaseResponse,
)
from app.services.digest_service import DigestService

router = APIRouter(prefix="/digests", tags=["digests"])


def _candidate_row_is_demo_placeholder(c: NewsCandidate) -> bool:
    url = (c.url or "").lower()
    title = c.title or ""
    if "example.com/ai-news" in url:
        return True
    if title.startswith("AI Candidate"):
        return True
    if (c.source or "") == "Example Tech":
        return True
    return False


@router.post("/create", response_model=DigestCreateResponse)
def create_digest(db: Session = Depends(get_db)) -> DigestCreateResponse:
    service = DigestService(db)
    digest = service.create_digest_for_today()
    return DigestCreateResponse(id=digest.id, date=digest.date, status=digest.status, current_step=digest.current_step)


@router.get("", response_model=list[DigestListItem])
def list_digests(db: Session = Depends(get_db)) -> list[DigestListItem]:
    service = DigestService(db)
    return [DigestListItem.model_validate(x) for x in service.build_digest_list_items()]


@router.get("/step1/manual-ratings/export")
def download_step1_manual_ratings_export(db: Session = Depends(get_db)) -> FileResponse:
    """Синхронизирует журнал оценок по всем выпускам и отдаёт JSON-файл."""
    path = sync_step1_manual_ratings_export(db, get_settings().step1_manual_ratings_path)
    return FileResponse(
        path,
        media_type="application/json; charset=utf-8",
        filename=path.name,
    )


@router.get("/{digest_id}", response_model=DigestDetail)
def get_digest(digest_id: int, db: Session = Depends(get_db)) -> DigestDetail:
    service = DigestService(db)
    digest = service.get_digest(digest_id)
    service.refresh_stale_html_platform_outputs(digest)
    candidates = db.query(NewsCandidate).filter(NewsCandidate.digest_id == digest.id).order_by(NewsCandidate.original_number).all()
    selected_rows = (
        db.query(SelectedNews).filter(SelectedNews.digest_id == digest.id).order_by(SelectedNews.output_position.asc()).all()
    )
    analytics = db.query(Analytics).filter(Analytics.digest_id == digest.id).all()
    outputs = db.query(FinalOutput).filter(FinalOutput.digest_id == digest.id).all()
    checks = db.query(QualityCheck).filter(QualityCheck.digest_id == digest.id).all()
    llm_costs = db.query(LlmCostRecord).filter(LlmCostRecord.digest_id == digest.id).order_by(LlmCostRecord.id.asc()).all()
    assets = db.query(Asset).filter(Asset.digest_id == digest.id).all()
    discovered_news_rows = (
        db.query(Step1DiscoveredNews)
        .filter(Step1DiscoveredNews.digest_id == digest.id)
        .order_by(Step1DiscoveredNews.id.asc())
        .all()
    )
    candidate_map = {c.id: c for c in candidates}
    selected = []
    for row in selected_rows:
        c = candidate_map.get(row.candidate_id)
        if not c:
            continue
        selected.append(
            {
                "candidate_id": row.candidate_id,
                "original_number": row.original_number,
                "output_position": row.output_position,
                "ordering_reason": row.ordering_reason,
                "title": c.title,
                "source": c.source,
                "url": c.url,
                "total_score": c.total_score,
            }
        )
    hashtags = []
    image_path = None
    docx_path = None
    image_variants: list[ImageVariantOut] = []
    rejected_reasons_summary: dict[str, int] = {}
    step1_reject_audit: dict[str, object] = {}
    step1_collection_meta: dict[str, object] = {}
    step2_order_rationale = ""
    for a in assets:
        if a.type == "hashtags":
            hashtags = a.prompt.split()
        if a.type == "image":
            image_path = a.path
        if a.type == "docx":
            docx_path = a.path
        if a.type.startswith("image_v"):
            suffix = a.type[7:]
            if suffix.isdigit():
                variant_num = int(suffix)
                if 1 <= variant_num <= 4:
                    image_variants.append(
                        ImageVariantOut(variant=variant_num, available=bool(a.path and Path(a.path).exists()))
                    )
        if a.type == "step1_rejected_reasons":
            try:
                raw = json.loads(a.prompt or "{}")
                if isinstance(raw, dict):
                    rejected_reasons_summary = {str(k): int(v) for k, v in raw.items()}
            except Exception:
                pass
        if a.type == "step1_reject_audit":
            try:
                raw = json.loads(a.prompt or "{}")
                if isinstance(raw, dict):
                    step1_reject_audit = raw
            except Exception:
                pass
        if a.type == "step1_collection_meta":
            try:
                raw = json.loads(a.prompt or "{}")
                if isinstance(raw, dict):
                    step1_collection_meta = raw
            except Exception:
                pass
        if a.type == "step2_order_rationale":
            step2_order_rationale = str(a.prompt or "").strip()
    image_variants.sort(key=lambda x: x.variant)
    release_cost = service.digest_release_cost_rub(digest)
    digest_cost = service.compute_digest_total_cost_rub(digest)
    total_cost_rub = digest_cost
    release_finalized = digest.proxyapi_finalized_cost_rub is not None
    try:
        from zoneinfo import ZoneInfo

        msk = ZoneInfo("Europe/Moscow")
        start_msk = datetime.combine(datetime.now(msk).date(), time.min, tzinfo=msk)
        day_start = start_msk.astimezone(timezone.utc).replace(tzinfo=None)
    except Exception:
        day_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    tracked_spend_today_rub = round(
        float(
            db.query(func.coalesce(func.sum(LlmCostRecord.cost_rub), 0.0))
            .filter(LlmCostRecord.created_at >= day_start)
            .scalar()
            or 0.0
        ),
        4,
    )
    spent_today_proxy = proxyapi_spent_today_rub(db, service.cost_tracker)
    from app.services.digest_service import (
        _candidate_url_fingerprint_sets,
        _discovered_row_verification_passed,
        _discovered_url_in_final_pool,
    )

    candidate_url_fps, candidate_page_fps = _candidate_url_fingerprint_sets([str(c.url or "") for c in candidates])
    candidates_are_demo_fallback = bool(candidates) and all(_candidate_row_is_demo_placeholder(c) for c in candidates)
    budget_notices = service.build_budget_notices(digest)
    proxyapi_budget_message = service.digest_proxyapi_budget_blocked_message(digest.id)
    pool_collection_stats = PoolCollectionStatsOut.model_validate(build_pool_collection_stats(db, digest.id))
    return DigestDetail(
        digest=DigestItem.model_validate(digest),
        candidates=[CandidateOut.model_validate(c) for c in candidates],
        discovered_news=[
            Step1DiscoveredNewsOut(
                id=row.id,
                title=row.title,
                url=row.url,
                source=row.source,
                published_at=row.published_at,
                source_stage=row.source_stage,
                link_status=bool(row.link_status),
                headline_editorial_ok=bool(row.headline_editorial_ok),
                page_verified=bool(row.page_verified),
                page_verification_passed=_discovered_row_verification_passed(row),
                in_candidate_pool=(
                    _discovered_url_in_final_pool(
                        str(row.url or ""),
                        url_fps=candidate_url_fps,
                        page_fps=candidate_page_fps,
                    )
                    if candidate_url_fps or candidate_page_fps
                    else False
                ),
                reject_codes=[x for x in str(row.reject_codes or "").split(",") if x],
                verification_comment=row.verification_comment or "",
                manual_score=row.manual_score,
                manual_reason=row.manual_reason,
                manual_reason_other=row.manual_reason_other,
                rated_at=row.rated_at,
            )
            for row in discovered_news_rows
        ],
        candidates_are_demo_fallback=candidates_are_demo_fallback,
        budget_notices=budget_notices,
        proxyapi_budget_exceeded=proxyapi_budget_message is not None,
        proxyapi_budget_message=proxyapi_budget_message,
        rejected_reasons_summary=rejected_reasons_summary,
        step1_reject_audit=step1_reject_audit,
        step1_collection_meta=step1_collection_meta,
        selected=selected,
        analytics=[
            {
                "candidate_id": a.candidate_id,
                "source_name": a.source_name,
                "source_url": a.source_url,
                "published_at": a.published_at,
                "essence": a.essence,
                "comment": a.comment,
                "analysis": a.analysis,
                "reader_text": a.reader_text or "",
            }
            for a in analytics
        ],
        outputs=[
            {
                "platform": o.platform,
                "content": o.content,
                "character_count": o.character_count,
                "qc_status": o.qc_status,
            }
            for o in outputs
        ],
        checks=[{"check_name": c.check_name, "status": c.status, "comment": c.comment} for c in checks],
        hashtags=hashtags,
        image_path=image_path,
        image_variants=image_variants,
        step4_selected_image_variant=digest.step4_selected_image_variant,
        docx_path=docx_path,
        llm_costs=[
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
            for r in llm_costs
        ],
        total_cost_rub=total_cost_rub,
        release_cost_rub=round(float(release_cost or total_cost_rub or 0.0), 4),
        release_cost_finalized=release_finalized,
        release_cost_finalized_at=digest.proxyapi_finalized_at,
        tracked_spend_today_rub=tracked_spend_today_rub,
        proxyapi_spent_today_rub=spent_today_proxy,
        enable_step4_image_generation=get_settings().enable_step4_image_generation,
        model_recommendations=service.get_model_recommendations(),
        pool_collection_stats=pool_collection_stats,
        step2_order_rationale=step2_order_rationale,
    )


@router.post("/{digest_id}/step0", response_model=Step0Response)
def run_step0(digest_id: int, payload: Step0Request, db: Session = Depends(get_db)) -> Step0Response:
    service = DigestService(db)
    digest = service.run_step_0(
        digest_id,
        payload.digest_type,
        news_window_days=payload.news_window_days,
        news_window_day_kind=payload.news_window_day_kind,
    )
    return Step0Response(
        digest_id=digest.id,
        digest_type=digest.digest_type or "serious",
        default_applied=payload.digest_type is None,
        news_window_days=digest.news_window_days,
        news_window_day_kind=digest.news_window_day_kind,  # type: ignore[arg-type]
    )


@router.patch("/{digest_id}/news-window", response_model=Step0Response)
def patch_digest_news_window(digest_id: int, payload: NewsWindowPatch, db: Session = Depends(get_db)) -> Step0Response:
    service = DigestService(db)
    digest = service.update_news_window(
        digest_id,
        news_window_days=payload.news_window_days,
        news_window_day_kind=payload.news_window_day_kind,
    )
    return Step0Response(
        digest_id=digest.id,
        digest_type=digest.digest_type or "serious",
        default_applied=bool(digest.digest_type_via_default),
        news_window_days=digest.news_window_days,
        news_window_day_kind=digest.news_window_day_kind,  # type: ignore[arg-type]
    )


@router.post("/{digest_id}/step1/run", response_model=list[CandidateOut])
def run_step1(digest_id: int, payload: Step1RunRequest, db: Session = Depends(get_db)) -> list[CandidateOut]:
    service = DigestService(db)
    rows = service.run_step_1(
        digest_id,
        payload.manual_urls,
        rebuild=payload.rebuild,
        keep_candidate_ids=payload.keep_candidate_ids,
        news_window_days=payload.news_window_days,
        news_window_day_kind=payload.news_window_day_kind,
    )
    return [CandidateOut.model_validate(r) for r in rows]


@router.post("/{digest_id}/step1/cancel")
def cancel_step1(digest_id: int, db: Session = Depends(get_db)) -> dict[str, object]:
    service = DigestService(db)
    return service.cancel_step1_run(digest_id)


@router.get("/{digest_id}/step1/filters", response_model=Step1FiltersResponse)
def get_step1_filters(digest_id: int, db: Session = Depends(get_db)) -> Step1FiltersResponse:
    service = DigestService(db)
    payload = service.get_step1_filters_payload(digest_id)
    return Step1FiltersResponse.model_validate(payload)


@router.put("/{digest_id}/step1/filters", response_model=Step1FiltersResponse)
def save_step1_filters(digest_id: int, payload: Step1FilterConfig, db: Session = Depends(get_db)) -> Step1FiltersResponse:
    service = DigestService(db)
    data = service.save_step1_filters_payload(digest_id, payload.model_dump())
    return Step1FiltersResponse.model_validate(data)


@router.post("/{digest_id}/step1/discovered/{news_id}/feedback")
def save_step1_discovered_feedback(
    digest_id: int,
    news_id: int,
    payload: Step1DiscoveredFeedbackRequest,
    db: Session = Depends(get_db),
) -> dict:
    service = DigestService(db)
    row = service.save_step1_discovered_feedback(
        digest_id=digest_id,
        news_id=news_id,
        score=payload.score,
        reason=payload.reason,
        reason_other=payload.reason_other,
    )
    export_path = str(get_settings().step1_manual_ratings_path.resolve())
    return {
        "id": row.id,
        "manual_score": row.manual_score,
        "manual_reason": row.manual_reason,
        "manual_reason_other": row.manual_reason_other,
        "rated_at": row.rated_at.isoformat() if row.rated_at else None,
        "ratings_export_path": export_path,
    }


@router.post("/{digest_id}/step2/manual-url")
def step2_add_manual_url(digest_id: int, payload: Step2AddManualUrlRequest, db: Session = Depends(get_db)) -> dict:
    service = DigestService(db)
    urls = list(payload.urls or [])
    single = str(payload.url or "").strip()
    if single:
        urls.insert(0, single)
    return service.add_manual_urls_to_pool(digest_id, urls)


@router.post("/{digest_id}/step2/select")
def select_news(digest_id: int, payload: SelectRequest, db: Session = Depends(get_db)) -> dict:
    service = DigestService(db)
    rows = service.select_news(digest_id, payload.selected_ids, payload.top5)
    digest = service.get_digest(digest_id)
    ordered = sorted(rows, key=lambda r: r.output_position)
    return {
        "selected_count": len(ordered),
        "selected_ids": [r.candidate_id for r in ordered],
        "status": digest.status,
        "analytics_ready": digest.status in {"analytics_ready", "final_ready"},
    }


@router.post("/{digest_id}/step2/order")
def order_news(digest_id: int, payload: OrderRequest, db: Session = Depends(get_db)) -> dict:
    service = DigestService(db)
    rows = service.run_step_2_order(digest_id, payload.ordered_candidate_ids)
    return {
        "ordered": [
            {
                "candidate_id": r.candidate_id,
                "output_position": r.output_position,
                "ordering_reason": r.ordering_reason,
                "original_number": r.original_number,
            }
            for r in rows
        ]
    }


@router.post("/{digest_id}/step2/order/ai-optimal")
def order_news_ai_optimal(digest_id: int, db: Session = Depends(get_db)) -> dict:
    """Оптимальный порядок пятёрки по мнению ИИ (ProxyAPI gpt-4.1-mini, без Crew)."""
    service = DigestService(db)
    rows = service.run_step_2_order_ai_optimal(digest_id)
    candidate_map = {
        c.id: c
        for c in db.query(NewsCandidate).filter(NewsCandidate.digest_id == digest_id).all()
    }
    return {
        "ordered": [
            {
                "candidate_id": r.candidate_id,
                "output_position": r.output_position,
                "ordering_reason": r.ordering_reason,
                "original_number": r.original_number,
                "title": (candidate_map[r.candidate_id].title if r.candidate_id in candidate_map else ""),
            }
            for r in rows
        ]
    }


@router.post("/{digest_id}/step3/confirm-ready")
def confirm_ready(digest_id: int, payload: CommandRequest, db: Session = Depends(get_db)) -> dict:
    service = DigestService(db)
    data = service.run_step_3_analytics(digest_id, payload.command)
    return {"message": "Аналитика готова — переходите к шагу 4", "overall_analysis": data.get("overall_analysis", "")}


@router.post("/{digest_id}/step4/generate-images")
def step4_generate_images(digest_id: int, payload: Step4GenerateImagesRequest, db: Session = Depends(get_db)) -> dict:
    service = DigestService(db)
    return service.run_step_4_generate_images(digest_id, payload.hook_variant)


@router.post("/{digest_id}/step4/select-image")
def step4_select_image(digest_id: int, payload: Step4SelectImageRequest, db: Session = Depends(get_db)) -> dict:
    service = DigestService(db)
    return service.run_step_4_select_image(digest_id, payload.variant)


@router.post("/{digest_id}/step4/generate-texts")
def step4_generate_texts(digest_id: int, payload: Step4GenerateTextsRequest, db: Session = Depends(get_db)) -> dict:
    service = DigestService(db)
    return service.run_step_4_generate_texts(
        digest_id,
        payload.platforms,
        payload.hook_variant,
    )


@router.post("/{digest_id}/step4/confirm-final")
def confirm_final(digest_id: int, payload: CommandRequest, db: Session = Depends(get_db)) -> dict:
    """Устаревший монолитный шаг 4: 4 обложки, выбор v1, все площадки."""
    service = DigestService(db)
    return service.run_step_4_final(digest_id, payload.hook_variant)


@router.post("/{digest_id}/finalize", response_model=FinalizeReleaseResponse)
def finalize_digest_release(digest_id: int, db: Session = Depends(get_db)) -> FinalizeReleaseResponse:
    """Зафиксировать накопительную стоимость выпуска (ProxyAPI) после готовности результата."""
    service = DigestService(db)
    data = service.finalize_digest_release(digest_id)
    return FinalizeReleaseResponse.model_validate(data)


@router.get("/{digest_id}/final")
def get_final_blocks(digest_id: int, db: Session = Depends(get_db)) -> dict:
    service = DigestService(db)
    service.get_digest(digest_id)
    rows = db.query(FinalOutput).filter(FinalOutput.digest_id == digest_id).all()
    return {row.platform: row.content for row in rows}


@router.get("/{digest_id}/docx")
def download_docx(digest_id: int, db: Session = Depends(get_db)) -> FileResponse:
    service = DigestService(db)
    digest = service.get_digest(digest_id)
    asset = db.query(Asset).filter(Asset.digest_id == digest_id, Asset.type == "docx").order_by(Asset.id.desc()).first()
    if not asset or not Path(asset.path).exists():
        raise HTTPException(status_code=404, detail="DOCX not found")
    download_name = digest_docx_filename(digest.date, digest.id)
    return FileResponse(path=asset.path, filename=download_name)


@router.get("/{digest_id}/image")
def download_image(
    digest_id: int,
    variant: int | None = Query(default=None, ge=1, le=4),
    db: Session = Depends(get_db),
) -> FileResponse:
    service = DigestService(db)
    service.get_digest(digest_id)
    asset_type = f"image_v{variant}" if variant else "image"
    asset = (
        db.query(Asset).filter(Asset.digest_id == digest_id, Asset.type == asset_type).order_by(Asset.id.desc()).first()
    )
    if not asset or not Path(asset.path).exists():
        raise HTTPException(status_code=404, detail="Image not found")
    suffix = f"_v{variant}" if variant else ""
    return FileResponse(path=asset.path, filename=f"digest_{digest_id}{suffix}.png")
