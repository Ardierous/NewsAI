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
    status: str
    current_step: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class Step0Request(BaseModel):
    digest_type: Literal["serious", "curious"] | None = None


class Step0Response(BaseModel):
    digest_id: int
    digest_type: str
    default_applied: bool


class Step1RunRequest(BaseModel):
    manual_urls: list[str] = Field(default_factory=list)


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

    model_config = {"from_attributes": True}


class SelectRequest(BaseModel):
    selected_ids: list[int] = Field(default_factory=list)
    top5: bool = False


class OrderRequest(BaseModel):
    ordered_candidate_ids: list[int] = Field(default_factory=list)


class CommandRequest(BaseModel):
    command: str
    hook_variant: Literal["A", "B", "V"] | None = None


class SelectedNewsOut(BaseModel):
    candidate_id: int
    original_number: int
    output_position: int
    ordering_reason: str
    title: str
    source: str
    url: str
    total_score: int


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


class DigestDetail(BaseModel):
    digest: DigestItem
    candidates: list[CandidateOut]
    selected: list[SelectedNewsOut]
    analytics: list[AnalyticsItemOut]
    outputs: list[FinalOutputOut]
    checks: list[QualityCheckOut]
    hashtags: list[str]
    image_path: str | None
    docx_path: str | None
