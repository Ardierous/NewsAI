from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field


class DigestCreateResponse(BaseModel):
    id: int
    date: date
    status: str
    current_step: str


class DigestItem(BaseModel):
    id: int
    date: date
    digest_type: str | None
    digest_type_via_default: bool = False
    news_window_days: int = 3
    news_window_day_kind: Literal["calendar", "working"] = "working"
    status: str
    current_step: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class Step0Request(BaseModel):
    digest_type: Literal["serious", "curious"] | None = None
    news_window_days: int = Field(default=3, ge=1, le=90)
    news_window_day_kind: Literal["calendar", "working"] = "working"


class Step0Response(BaseModel):
    digest_id: int
    digest_type: str
    default_applied: bool
    news_window_days: int
    news_window_day_kind: Literal["calendar", "working"]


class Step1RunRequest(BaseModel):
    manual_urls: list[str] = Field(default_factory=list)
    """Полная пересборка пула после шагов 2–4 (сброс выбора, порядка, аналитики, финала)."""
    rebuild: bool = False


class Step1DiscoveredFeedbackRequest(BaseModel):
    score: int = Field(ge=1, le=3)
    reason: Literal["published_out_of_range", "http_unreachable", "url_redirect_mismatch", "off_topic_not_ai", "other"] | None = None
    reason_other: str | None = None


class CandidateOut(BaseModel):
    id: int
    original_number: int
    title: str
    url: str
    source: str
    tier: str
    published_at: str
    category: str
    description: str
    significance_score: int
    novelty_score: int
    impact_score: int
    total_score: int
    reliability_status: str
    verification_comment: str
    link_status: bool
    headline_editorial_ok: bool = False
    page_verified: bool = False

    model_config = {"from_attributes": True}


class SelectRequest(BaseModel):
    selected_ids: list[int] = Field(default_factory=list)
    top5: bool = False


class OrderRequest(BaseModel):
    ordered_candidate_ids: list[int] = Field(default_factory=list)


class CommandRequest(BaseModel):
    command: str = ""
    hook_variant: Literal["A", "B", "V"] | None = None


class Step4GenerateImagesRequest(BaseModel):
    hook_variant: Literal["A", "B", "V"] | None = None


class Step4SelectImageRequest(BaseModel):
    variant: int = Field(ge=1, le=4)


class Step4GenerateTextsRequest(BaseModel):
    platforms: list[str] = Field(min_length=1)
    hook_variant: Literal["A", "B", "V"] | None = None


class ImageVariantOut(BaseModel):
    variant: int
    available: bool = True


class SelectedNewsOut(BaseModel):
    candidate_id: int
    original_number: int
    output_position: int
    ordering_reason: str
    title: str
    source: str
    url: str
    total_score: int


class Step1DiscoveredNewsOut(BaseModel):
    id: int
    title: str
    url: str
    source: str
    published_at: str
    source_stage: str
    link_status: bool = False
    headline_editorial_ok: bool = False
    page_verified: bool = False
    reject_codes: list[str] = Field(default_factory=list)
    verification_comment: str = ""
    manual_score: int | None = None
    manual_reason: str | None = None
    manual_reason_other: str | None = None
    rated_at: datetime | None = None


class AnalyticsItemOut(BaseModel):
    candidate_id: int
    source_name: str
    source_url: str
    published_at: str
    essence: str
    comment: str
    analysis: str


class QualityCheckOut(BaseModel):
    check_name: str
    status: str
    comment: str


class FinalOutputOut(BaseModel):
    platform: str
    content: str
    character_count: int
    qc_status: str


class LlmCostRecordOut(BaseModel):
    step: str
    step_title_ru: str = ""
    agent_name: str
    agent_title_ru: str = ""
    model: str
    request_label: str
    operation_title_ru: str = ""
    cost_rub: float | None
    created_at: datetime


class AgentModelRecommendationOut(BaseModel):
    agent_name: str
    recommended_model: str
    input_rub_per_1m: float
    output_rub_per_1m: float
    rationale: str


class DigestDetail(BaseModel):
    digest: DigestItem
    candidates: list[CandidateOut]
    discovered_news: list[Step1DiscoveredNewsOut] = Field(default_factory=list)
    candidates_are_demo_fallback: bool = False
    budget_notices: list[str] = Field(default_factory=list)
    proxyapi_budget_exceeded: bool = False
    proxyapi_budget_message: str | None = None
    rejected_reasons_summary: dict[str, int] = Field(default_factory=dict)
    selected: list[SelectedNewsOut]
    analytics: list[AnalyticsItemOut]
    outputs: list[FinalOutputOut]
    checks: list[QualityCheckOut]
    hashtags: list[str]
    image_path: str | None
    image_variants: list[ImageVariantOut] = Field(default_factory=list)
    step4_selected_image_variant: int | None = None
    docx_path: str | None
    llm_costs: list[LlmCostRecordOut]
    total_cost_rub: float
    """Сумма cost_rub по всем выпускам за календарный день (МСК) — учёт приложения."""
    tracked_spend_today_rub: float
    """Траты за день по балансу ProxyAPI (открытие суток → текущий баланс / budget.used)."""
    proxyapi_spent_today_rub: float | None = None
    enable_step4_image_generation: bool = False
    model_recommendations: list[AgentModelRecommendationOut]
