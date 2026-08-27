from fastapi import APIRouter

from app.config import clear_settings_cache
from app.pipeline_settings import read_pipeline_config, write_pipeline_config
from app.schemas_config import AppConfigResponse, Step1CuriousBalanceResponse, Step1CuriousBalanceUpdate
from app.services.app_config_summary import build_app_config_summary

router = APIRouter(tags=["config"])


@router.get("/config", response_model=AppConfigResponse)
def get_app_config() -> AppConfigResponse:
    return AppConfigResponse.model_validate(build_app_config_summary())


@router.get("/config/step1/serious-curious-extra-batches", response_model=Step1CuriousBalanceResponse)
def get_step1_curious_balance() -> Step1CuriousBalanceResponse:
    cfg = read_pipeline_config()
    step1 = cfg.get("step1") if isinstance(cfg.get("step1"), dict) else {}
    raw = int(step1.get("serious_curious_extra_batches", 3) or 3)
    value = max(1, min(10, raw))
    return Step1CuriousBalanceResponse(value=value)


@router.put("/config/step1/serious-curious-extra-batches", response_model=Step1CuriousBalanceResponse)
def put_step1_curious_balance(payload: Step1CuriousBalanceUpdate) -> Step1CuriousBalanceResponse:
    cfg = read_pipeline_config()
    step1 = dict(cfg.get("step1") if isinstance(cfg.get("step1"), dict) else {})
    step1["serious_curious_extra_batches"] = int(payload.value)
    cfg["step1"] = step1
    saved = write_pipeline_config(cfg)
    clear_settings_cache()
    saved_step1 = saved.get("step1") if isinstance(saved.get("step1"), dict) else {}
    raw = int(saved_step1.get("serious_curious_extra_batches", payload.value) or payload.value)
    value = max(1, min(10, raw))
    return Step1CuriousBalanceResponse(value=value)
