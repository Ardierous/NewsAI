import json
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Analytics, Asset, FinalOutput, LlmCostRecord, NewsCandidate, QualityCheck, SelectedNews
from app.schemas import (
    CandidateOut,
    CommandRequest,
    DigestCreateResponse,
    DigestDetail,
    DigestItem,
    OrderRequest,
    SelectRequest,
    Step0Request,
    Step0Response,
    Step1RunRequest,
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


@router.get("", response_model=list[DigestItem])
def list_digests(db: Session = Depends(get_db)) -> list[DigestItem]:
    service = DigestService(db)
    return [DigestItem.model_validate(x) for x in service.list_digests()]


@router.get("/{digest_id}", response_model=DigestDetail)
def get_digest(digest_id: int, db: Session = Depends(get_db)) -> DigestDetail:
    service = DigestService(db)
    digest = service.get_digest(digest_id)
    candidates = db.query(NewsCandidate).filter(NewsCandidate.digest_id == digest.id).order_by(NewsCandidate.original_number).all()
    selected_rows = (
        db.query(SelectedNews).filter(SelectedNews.digest_id == digest.id).order_by(SelectedNews.output_position.asc()).all()
    )
    analytics = db.query(Analytics).filter(Analytics.digest_id == digest.id).all()
    outputs = db.query(FinalOutput).filter(FinalOutput.digest_id == digest.id).all()
    checks = db.query(QualityCheck).filter(QualityCheck.digest_id == digest.id).all()
    llm_costs = db.query(LlmCostRecord).filter(LlmCostRecord.digest_id == digest.id).order_by(LlmCostRecord.id.asc()).all()
    assets = db.query(Asset).filter(Asset.digest_id == digest.id).all()
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
    rejected_reasons_summary: dict[str, int] = {}
    for a in assets:
        if a.type == "hashtags":
            hashtags = a.prompt.split()
        if a.type == "image":
            image_path = a.path
        if a.type == "docx":
            docx_path = a.path
        if a.type == "step1_rejected_reasons":
            try:
                raw = json.loads(a.prompt or "{}")
                if isinstance(raw, dict):
                    rejected_reasons_summary = {str(k): int(v) for k, v in raw.items()}
            except Exception:
                pass
    total_cost_rub = round(sum([x.cost_rub or 0.0 for x in llm_costs]), 6)
    candidates_are_demo_fallback = bool(candidates) and all(_candidate_row_is_demo_placeholder(c) for c in candidates)
    budget_notices = service.build_budget_notices(digest)
    return DigestDetail(
        digest=DigestItem.model_validate(digest),
        candidates=[CandidateOut.model_validate(c) for c in candidates],
        candidates_are_demo_fallback=candidates_are_demo_fallback,
        budget_notices=budget_notices,
        rejected_reasons_summary=rejected_reasons_summary,
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
        docx_path=docx_path,
        llm_costs=[
            {
                "step": r.step,
                "agent_name": r.agent_name,
                "model": r.model,
                "request_label": r.request_label,
                "cost_rub": r.cost_rub,
                "created_at": r.created_at,
            }
            for r in llm_costs
        ],
        total_cost_rub=total_cost_rub,
        model_recommendations=service.get_model_recommendations(),
    )


@router.post("/{digest_id}/step0", response_model=Step0Response)
def run_step0(digest_id: int, payload: Step0Request, db: Session = Depends(get_db)) -> Step0Response:
    service = DigestService(db)
    digest = service.run_step_0(digest_id, payload.digest_type)
    return Step0Response(digest_id=digest.id, digest_type=digest.digest_type or "serious", default_applied=payload.digest_type is None)


@router.post("/{digest_id}/step1/run", response_model=list[CandidateOut])
def run_step1(digest_id: int, payload: Step1RunRequest, db: Session = Depends(get_db)) -> list[CandidateOut]:
    service = DigestService(db)
    rows = service.run_step_1(digest_id, payload.manual_urls)
    return [CandidateOut.model_validate(r) for r in rows]


@router.post("/{digest_id}/step2/select")
def select_news(digest_id: int, payload: SelectRequest, db: Session = Depends(get_db)) -> dict:
    service = DigestService(db)
    rows = service.select_news(digest_id, payload.selected_ids, payload.top5)
    return {"selected_count": len(rows)}


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


@router.post("/{digest_id}/step3/confirm-ready")
def confirm_ready(digest_id: int, payload: CommandRequest, db: Session = Depends(get_db)) -> dict:
    service = DigestService(db)
    data = service.run_step_3_analytics(digest_id, payload.command)
    return {"message": 'Напишите "Ок" для перехода к Шагу 4', "overall_analysis": data.get("overall_analysis", "")}


@router.post("/{digest_id}/step4/confirm-final")
def confirm_final(digest_id: int, payload: CommandRequest, db: Session = Depends(get_db)) -> dict:
    service = DigestService(db)
    data = service.run_step_4_final(digest_id, payload.command, payload.hook_variant)
    return data


@router.get("/{digest_id}/final")
def get_final_blocks(digest_id: int, db: Session = Depends(get_db)) -> dict:
    service = DigestService(db)
    service.get_digest(digest_id)
    rows = db.query(FinalOutput).filter(FinalOutput.digest_id == digest_id).all()
    return {row.platform: row.content for row in rows}


@router.get("/{digest_id}/docx")
def download_docx(digest_id: int, db: Session = Depends(get_db)) -> FileResponse:
    service = DigestService(db)
    service.get_digest(digest_id)
    asset = db.query(Asset).filter(Asset.digest_id == digest_id, Asset.type == "docx").order_by(Asset.id.desc()).first()
    if not asset or not Path(asset.path).exists():
        raise HTTPException(status_code=404, detail="DOCX not found")
    return FileResponse(path=asset.path, filename=f"digest_{digest_id}.docx")


@router.get("/{digest_id}/image")
def download_image(digest_id: int, db: Session = Depends(get_db)) -> FileResponse:
    service = DigestService(db)
    service.get_digest(digest_id)
    asset = db.query(Asset).filter(Asset.digest_id == digest_id, Asset.type == "image").order_by(Asset.id.desc()).first()
    if not asset or not Path(asset.path).exists():
        raise HTTPException(status_code=404, detail="Image not found")
    return FileResponse(path=asset.path, filename=f"digest_{digest_id}.png")
