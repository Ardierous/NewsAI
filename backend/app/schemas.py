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
    digest_topic: Literal["ai", "style"] = "ai"
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


def _step0_digest_topic_default() -> Literal["ai", "style"]:
    return get_digest_defaults().step0.digest_topic_default


class Step0Request(BaseModel):
    """digest_type: legacy serious|curious принимаются; канонически всегда «Дайджест ИИ» (serious)."""
    digest_type: Literal["serious", "curious"] | None = None
    digest_topic: Literal["ai", "style"] | None = None
    news_window_days: int = Field(default_factory=_step0_news_window_days_default, ge=1, le=90)
    news_window_day_kind: Literal["calendar", "working"] = Field(
        default_factory=_step0_news_window_day_kind_default
    )


class FinalizeReleaseResponse(BaseModel):
    digest_id: int
    finalized: bool
    already_finalized: bool = False
    release_cost_rub: float
    finalized_at: str | None = None


class Step0Response(BaseModel):
    digest_id: int
    digest_type: str
    digest_topic: Literal["ai", "style"]
    default_applied: bool
    topic_default_applied: bool = False
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
    """Параметры окна из шага 0 UI — сохраняются в выпуск перед каждым запуском сбора."""
    news_window_days: int = Field(default_factory=_step0_news_window_days_default, ge=1, le=90)
    news_window_day_kind: Literal["calendar", "working"] = Field(
        default_factory=_step0_news_window_day_kind_default
    )


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
    reason: Literal[
        "published_out_of_range",
        "http_unreachable",
        "url_redirect_mismatch",
        "off_topic_not_ai",
        "off_topic_not_style",
        "other",
    ] | None = None
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
    material_form: str = "article"
    not_ad_disclosure: bool = False
    editorial_angle: str = "serious"

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
        from app.services.step1_candidate_policy import (
            has_not_ad_disclosure,
            parse_editorial_angle_from_comment,
            parse_material_form_from_comment,
        )

        comment = str(payload.get("verification_comment") or "")
        payload["material_form"] = parse_material_form_from_comment(comment)
        payload["not_ad_disclosure"] = has_not_ad_disclosure(comment)
        angle = parse_editorial_angle_from_comment(comment)
        tier = str(payload.get("tier") or "")
        if angle == "serious" and tier.startswith("Curious-"):
            angle = "curious"
        payload["editorial_angle"] = angle
        return payload


class SelectRequest(BaseModel):
    selected_ids: list[int] = Field(default_factory=list)
    top5: bool = False


class Step2AddManualUrlRequest(BaseModel):
    url: str = ""
    urls: list[str] = Field(default_factory=list)


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


class Step1ToolUsageOut(BaseModel):
    id: str
    label: str
    color: str = "#94a3b8"
    time_sec: int = 0
    time_human: str = "—"
    cost_rub: float = 0.0
    time_share: float = 0.0
    cost_share: float = 0.0
    calls: int | None = None
    urls: int | None = None
    detail: str | None = None


class Step1UsageBreakdownOut(BaseModel):
    total_time_sec: int = 0
    total_time_human: str = "—"
    total_cost_rub: float = 0.0
    cost_source: str = "none"
    cost_source_note: str = ""
    tools: list[Step1ToolUsageOut] = Field(default_factory=list)
    funnel: dict[str, Any] = Field(default_factory=dict)
    summary: dict[str, Any] = Field(default_factory=dict)


class PoolCollectionStatsOut(BaseModel):
    pool: PoolStatsOut
    last_run: Step1RunStatsOut | None = None
    step1_total_rub: float = 0.0
    step1_costs: list[LlmCostRecordOut] = Field(default_factory=list)
    step1_usage: Step1UsageBreakdownOut | None = None
    history: list[Step1RunStatsOut] = Field(default_factory=list)


class Step1StatisticsSummaryOut(BaseModel):
    total_links: int = 0
    in_pool: int = 0
    rejected: int = 0
    verified_passed: int = 0


class Step1DominantRejectOut(BaseModel):
    code: str
    label: str = ""
    count: int = 0
    share_pct: float = 0.0
    is_dominant: bool = False


class Step1FunnelBottleneckOut(BaseModel):
    stage: str
    label: str = ""
    lost: int = 0
    detail: str = ""


class Step1RecommendationOut(BaseModel):
    priority: str = "medium"
    title: str = ""
    detail: str = ""


class Step1StatisticsInsightsOut(BaseModel):
    headline: str = ""
    stop_reason: str = ""
    dominant_rejects: list[Step1DominantRejectOut] = Field(default_factory=list)
    funnel_bottlenecks: list[Step1FunnelBottleneckOut] = Field(default_factory=list)
    efficiency_notes: list[str] = Field(default_factory=list)
    recommendations: list[Step1RecommendationOut] = Field(default_factory=list)


class Step1LinkAnalyticsOut(BaseModel):
    id: int
    url: str
    host: str = ""
    title: str = ""
    source: str = ""
    published_at: str = ""
    source_stage: str = ""
    outcome: str = "rejected"
    link_status: bool = False
    headline_editorial_ok: bool = False
    page_verification_passed: bool = False
    in_candidate_pool: bool = False
    reject_codes: list[str] = Field(default_factory=list)
    reject_labels: list[str] = Field(default_factory=list)
    verification_comment: str = ""


class Step1StatisticsOut(BaseModel):
    digest_id: int
    digest_type: str = "serious"
    discovery_run_id: int | None = None
    generated_at: datetime
    summary: Step1StatisticsSummaryOut
    step1_collection_meta: dict[str, Any] = Field(default_factory=dict)
    rejected_reasons_summary: dict[str, int] = Field(default_factory=dict)
    step1_reject_audit: dict[str, Any] = Field(default_factory=dict)
    curious_tone_audit: dict[str, Any] = Field(default_factory=dict)
    pool_collection_stats: PoolCollectionStatsOut
    registry_buckets: dict[str, int] = Field(default_factory=dict)
    filter_counters: dict[str, int] = Field(default_factory=dict)
    links: list[Step1LinkAnalyticsOut] = Field(default_factory=list)
    insights: Step1StatisticsInsightsOut | None = None


class Step1LiveProgressOut(BaseModel):
    running: bool = False
    phase: str = ""
    phase_key: str = ""
    elapsed_sec: int = 0
    elapsed_human: str = "—"
    iteration: int = 0
    web_search_api_calls: int = 0
    web_search_citation_urls: int = 0
    web_search_cost_est_rub: float = 0.0
    urls_raw: int = 0
    urls_raw_merged: int = 0
    urls_prefilter_rejected: int = 0
    urls_sent_to_http: int = 0
    verified_pool: int = 0
    rejected_total: int = 0
    rejected_links: int = 0
    reject_reason_events: int = 0
    collection_target: int = 15
    cancel_requested: bool = False
    pool_carried_over: int = 0
    pool_added_this_run: int = 0
    links_found_paid: int = 0
    links_found_free: int = 0
    links_found_total: int = 0
    links_processed: int = 0
    links_checked: int = 0
    pool_yield_pct: float | None = None
    recheck_only: bool = False


class DigestDetail(BaseModel):
    digest: DigestItem
    candidates: list[CandidateOut]
    discovered_news: list[Step1DiscoveredNewsOut] = Field(default_factory=list)
    candidates_are_demo_fallback: bool = False
    budget_notices: list[str] = Field(default_factory=list)
    proxyapi_budget_exceeded: bool = False
    proxyapi_budget_message: str | None = None
    rejected_reasons_summary: dict[str, int] = Field(default_factory=dict)
    step1_reject_audit: dict[str, Any] = Field(default_factory=dict)
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
    """Накопительно по выпуску (ProxyAPI с якоря выпуска) или зафиксированная сумма."""
    release_cost_rub: float = 0.0
    release_cost_finalized: bool = False
    release_cost_finalized_at: datetime | None = None
    """Сумма cost_rub по всем выпускам за календарный день (МСК) — учёт приложения."""
    tracked_spend_today_rub: float
    """Траты за день по балансу ProxyAPI (открытие суток → текущий баланс / budget.used)."""
    proxyapi_spent_today_rub: float | None = None
    enable_step4_image_generation: bool = False
    model_recommendations: list[AgentModelRecommendationOut]
    pool_collection_stats: PoolCollectionStatsOut | None = None
    step2_order_rationale: str = ""
