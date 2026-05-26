from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from app.digest_defaults import get_digest_defaults
from app.services.candidate_origin import resolve_candidate_origin


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


class DigestTop5ListItem(BaseModel):
    position: int
    title: str
    source: str | None = None


class DigestListItem(DigestItem):
    status_label_ru: str = ""
    summary_title: str = ""
    top5: list[DigestTop5ListItem] = Field(default_factory=list)
    total_cost_rub: float = 0.0


def _step0_news_window_days_default() -> int:
    return get_digest_defaults().step0.news_window_days_default


def _step0_news_window_day_kind_default() -> Literal["calendar", "working"]:
    return get_digest_defaults().step0.news_window_day_kind_default


class Step0Request(BaseModel):
    digest_type: Literal["serious", "curious"] | None = None
    news_window_days: int = Field(default_factory=_step0_news_window_days_default, ge=1, le=90)
    news_window_day_kind: Literal["calendar", "working"] = Field(
        default_factory=_step0_news_window_day_kind_default
    )


class Step0Response(BaseModel):
    digest_id: int
    digest_type: str
    default_applied: bool
    news_window_days: int
    news_window_day_kind: Literal["calendar", "working"]


class NewsWindowPatch(BaseModel):
    news_window_days: int = Field(ge=1, le=90)
    news_window_day_kind: Literal["calendar", "working"]


class Step1RunRequest(BaseModel):
    manual_urls: list[str] = Field(default_factory=list)
    """Полная пересборка пула после шагов 2–4 (сброс выбора, порядка, аналитики, финала)."""
    rebuild: bool = False
    """При rebuild=true: id кандидатов из шага 2, которые оставить в пуле; остальные слоты добираются заново."""
    keep_candidate_ids: list[int] = Field(default_factory=list)
    """Если задано — сохраняется в выпуск перед поиском (актуально при смене окна между пересборками)."""
    news_window_days: int | None = Field(default=None, ge=1, le=90)
    news_window_day_kind: Literal["calendar", "working"] | None = None


class Step1FilterCatalogItem(BaseModel):
    id: str
    label_ru: str
    description_ru: str
    stage: Literal["pre_http", "verify", "pool"]
    default_enabled: bool = True
    locked: bool = False


class Step1FilterState(BaseModel):
    id: str
    enabled: bool = True
    order: int = Field(ge=1)


class Step1FilterConfig(BaseModel):
    version: int = 2
    filters: list[Step1FilterState] = Field(default_factory=list)
    min_discovered_pages: int = Field(ge=10, le=200)
    min_collection_iterations: int = Field(ge=1, le=50)
    digest_type: str | None = None


class Step1JournalTotals(BaseModel):
    total: int = 0
    in_pool: int = 0
    rejected: int = 0


class Step1FilterAppliedSnapshot(BaseModel):
    id: str
    enabled: bool = True
    order: int = 0


class Step1FiltersResponse(BaseModel):
    catalog: list[Step1FilterCatalogItem] = Field(default_factory=list)
    config: Step1FilterConfig
    counters: dict[str, int] = Field(default_factory=dict)
    journal_totals: Step1JournalTotals = Field(default_factory=Step1JournalTotals)
    filters_applied_last_run: list[Step1FilterAppliedSnapshot] = Field(default_factory=list)


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
    is_foreign_agent: bool = False
    is_aggregator: bool = False
    is_duplicate: bool = False

    model_config = {"from_attributes": True}

    @model_validator(mode="before")
    @classmethod
    def normalize_origin_category(cls, data: Any) -> Any:
        if isinstance(data, dict):
            payload = dict(data)
        else:
            payload = {
                "id": data.id,
                "original_number": data.original_number,
                "title": data.title,
                "url": data.url,
                "source": data.source,
                "tier": data.tier,
                "published_at": data.published_at,
                "category": data.category,
                "description": data.description,
                "significance_score": data.significance_score,
                "novelty_score": data.novelty_score,
                "impact_score": data.impact_score,
                "total_score": data.total_score,
                "reliability_status": data.reliability_status,
                "verification_comment": data.verification_comment or "",
                "link_status": data.link_status,
                "headline_editorial_ok": data.headline_editorial_ok,
                "page_verified": data.page_verified,
                "is_foreign_agent": data.is_foreign_agent,
                "is_aggregator": data.is_aggregator,
                "is_duplicate": data.is_duplicate,
            }
        payload["category"] = resolve_candidate_origin(
            payload.get("category"),
            payload.get("verification_comment"),
            payload.get("description"),
        )
        return payload


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
    page_verification_passed: bool = False
    in_candidate_pool: bool = False
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
    reader_text: str = ""


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


class Step1RunStatsOut(BaseModel):
    run_number: int
    started_at: str | None = None
    completed_at: str | None = None
    duration_sec: int | None = None
    duration_human: str = "—"
    cost_rub: float = 0.0
    news_count: int = 0


class PoolStatsOut(BaseModel):
    total: int = 0
    press_count: int = 0
    press_share: float = 0.0
    ru_count: int = 0
    ru_share: float = 0.0
    max_per_source: int = 0
    foreign_agent_count: int = 0
    forbidden_count: int = 0


class PoolCollectionStatsOut(BaseModel):
    pool: PoolStatsOut
    last_run: Step1RunStatsOut | None = None
    step1_total_rub: float = 0.0
    step1_costs: list[LlmCostRecordOut] = Field(default_factory=list)
    history: list[Step1RunStatsOut] = Field(default_factory=list)


class DigestDetail(BaseModel):
    digest: DigestItem
    candidates: list[CandidateOut]
    discovered_news: list[Step1DiscoveredNewsOut] = Field(default_factory=list)
    candidates_are_demo_fallback: bool = False
    budget_notices: list[str] = Field(default_factory=list)
    proxyapi_budget_exceeded: bool = False
    proxyapi_budget_message: str | None = None
    rejected_reasons_summary: dict[str, int] = Field(default_factory=dict)
    step1_collection_meta: dict[str, Any] = Field(default_factory=dict)
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
    pool_collection_stats: PoolCollectionStatsOut | None = None
