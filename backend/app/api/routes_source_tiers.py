from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.database import get_db
from app.schemas_source_tiers import SourceTiersEditorOut, SourceTiersEditorUpdate
from app.services.source_tiers_editor import build_source_tiers_editor, save_source_tiers_editor

router = APIRouter(tags=["config"])


@router.get("/config/source-tiers", response_model=SourceTiersEditorOut)
def get_source_tiers_editor(
    digest_type: str = Query(..., pattern="^(serious|curious)$"),
    window_days: int = Query(30, ge=1, le=365),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> SourceTiersEditorOut:
    return build_source_tiers_editor(db, settings, digest_type, window_days=window_days)


@router.put("/config/source-tiers", response_model=SourceTiersEditorOut)
def put_source_tiers_editor(
    payload: SourceTiersEditorUpdate,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> SourceTiersEditorOut:
    return save_source_tiers_editor(db, settings, payload)
