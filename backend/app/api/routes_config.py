from fastapi import APIRouter

from app.schemas_config import AppConfigResponse
from app.services.app_config_summary import build_app_config_summary

router = APIRouter(tags=["config"])


@router.get("/config", response_model=AppConfigResponse)
def get_app_config() -> AppConfigResponse:
    return AppConfigResponse.model_validate(build_app_config_summary())
