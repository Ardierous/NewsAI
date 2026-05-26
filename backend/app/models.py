from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Digest(Base):
    __tablename__ = "digests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    date: Mapped[datetime] = mapped_column(Date, nullable=False, unique=True, index=True)
    digest_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    digest_type_via_default: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    status: Mapped[str] = mapped_column(String(40), default="draft", nullable=False)
    current_step: Mapped[str] = mapped_column(String(40), default="draft", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    step1_budget_capped: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    step2_budget_capped: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    news_window_days: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    news_window_day_kind: Mapped[str] = mapped_column(String(16), default="working", nullable=False)
    step4_selected_image_variant: Mapped[int | None] = mapped_column(Integer, nullable=True)
    proxyapi_balance_session_start: Mapped[float | None] = mapped_column(Float, nullable=True)
    proxyapi_budget_used_session_start: Mapped[float | None] = mapped_column(Float, nullable=True)
    proxyapi_balance_before: Mapped[float | None] = mapped_column(Float, nullable=True)
    proxyapi_balance_after: Mapped[float | None] = mapped_column(Float, nullable=True)
    proxyapi_budget_used_before: Mapped[float | None] = mapped_column(Float, nullable=True)
    proxyapi_budget_used_after: Mapped[float | None] = mapped_column(Float, nullable=True)

    candidates: Mapped[list["NewsCandidate"]] = relationship(
        "NewsCandidate", back_populates="digest", cascade="all, delete-orphan"
    )
    selected_items: Mapped[list["SelectedNews"]] = relationship(
        "SelectedNews", back_populates="digest", cascade="all, delete-orphan"
    )
    analytics_items: Mapped[list["Analytics"]] = relationship(
        "Analytics", back_populates="digest", cascade="all, delete-orphan"
    )
    final_outputs: Mapped[list["FinalOutput"]] = relationship(
        "FinalOutput", back_populates="digest", cascade="all, delete-orphan"
    )
    assets: Mapped[list["Asset"]] = relationship("Asset", back_populates="digest", cascade="all, delete-orphan")
    quality_checks: Mapped[list["QualityCheck"]] = relationship(
        "QualityCheck", back_populates="digest", cascade="all, delete-orphan"
    )
    llm_costs: Mapped[list["LlmCostRecord"]] = relationship(
        "LlmCostRecord", back_populates="digest", cascade="all, delete-orphan"
    )


class NewsCandidate(Base):
    __tablename__ = "news_candidates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    digest_id: Mapped[int] = mapped_column(ForeignKey("digests.id", ondelete="CASCADE"), nullable=False, index=True)
    original_number: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    url: Mapped[str] = mapped_column(String(1000), nullable=False)
    source: Mapped[str] = mapped_column(String(255), nullable=False)
    tier: Mapped[str] = mapped_column(String(32), default="Tier-3")
    published_at: Mapped[str] = mapped_column(String(100), nullable=False)
    category: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    article_excerpt: Mapped[str] = mapped_column(Text, default="", nullable=False)
    significance_score: Mapped[int] = mapped_column(Integer, default=1)
    novelty_score: Mapped[int] = mapped_column(Integer, default=1)
    impact_score: Mapped[int] = mapped_column(Integer, default=1)
    total_score: Mapped[int] = mapped_column(Integer, default=3)
    reliability_status: Mapped[str] = mapped_column(String(40), default="⚠️ сомнительный")
    link_status: Mapped[bool] = mapped_column(Boolean, default=False)
    headline_editorial_ok: Mapped[bool] = mapped_column(Boolean, default=False)
    page_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    is_foreign_agent: Mapped[bool] = mapped_column(Boolean, default=False)
    is_aggregator: Mapped[bool] = mapped_column(Boolean, default=False)
    is_duplicate: Mapped[bool] = mapped_column(Boolean, default=False)
    verification_comment: Mapped[str] = mapped_column(Text, default="")

    digest: Mapped["Digest"] = relationship("Digest", back_populates="candidates")


class Step1DiscoveryRun(Base):
    """Один запуск сбора кандидатов (шаг 1) для выпуска."""

    __tablename__ = "step1_discovery_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    digest_id: Mapped[int] = mapped_column(ForeignKey("digests.id", ondelete="CASCADE"), nullable=False, index=True)
    run_number: Mapped[int] = mapped_column(Integer, nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    pool_formed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    news_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    duration_sec: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cost_rub: Mapped[float | None] = mapped_column(Float, nullable=True)


class Step1DiscoveredNews(Base):
    __tablename__ = "step1_discovered_news"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    digest_id: Mapped[int] = mapped_column(ForeignKey("digests.id", ondelete="CASCADE"), nullable=False, index=True)
    discovery_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("step1_discovery_runs.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    source_stage: Mapped[str] = mapped_column(String(40), default="unknown", nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    url: Mapped[str] = mapped_column(String(1000), nullable=False)
    source: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    published_at: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    headline_editorial_ok: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    link_status: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    page_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    reject_codes: Mapped[str] = mapped_column(Text, default="", nullable=False)
    verification_comment: Mapped[str] = mapped_column(Text, default="", nullable=False)
    manual_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    manual_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    manual_reason_other: Mapped[str | None] = mapped_column(Text, nullable=True)
    rated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Step1ManualRatingLog(Base):
    """Журнал ручных оценок (не удаляется при пересборке пула)."""

    __tablename__ = "step1_manual_rating_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    discovery_run_id: Mapped[int] = mapped_column(
        ForeignKey("step1_discovery_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    digest_id: Mapped[int] = mapped_column(ForeignKey("digests.id", ondelete="CASCADE"), nullable=False, index=True)
    pool_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    run_number: Mapped[int] = mapped_column(Integer, nullable=False)
    discovered_news_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    url: Mapped[str] = mapped_column(String(1000), nullable=False)
    published_at: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    manual_score: Mapped[int] = mapped_column(Integer, nullable=False)
    manual_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    manual_reason_other: Mapped[str | None] = mapped_column(Text, nullable=True)
    rated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


class SelectedNews(Base):
    __tablename__ = "selected_news"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    digest_id: Mapped[int] = mapped_column(ForeignKey("digests.id", ondelete="CASCADE"), nullable=False, index=True)
    candidate_id: Mapped[int] = mapped_column(ForeignKey("news_candidates.id"), nullable=False)
    original_number: Mapped[int] = mapped_column(Integer, nullable=False)
    output_position: Mapped[int] = mapped_column(Integer, nullable=False)
    ordering_reason: Mapped[str] = mapped_column(Text, default="")

    digest: Mapped["Digest"] = relationship("Digest", back_populates="selected_items")


class Analytics(Base):
    __tablename__ = "analytics"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    digest_id: Mapped[int] = mapped_column(ForeignKey("digests.id", ondelete="CASCADE"), nullable=False, index=True)
    candidate_id: Mapped[int] = mapped_column(ForeignKey("news_candidates.id"), nullable=False)
    essence: Mapped[str] = mapped_column(Text, nullable=False)
    comment: Mapped[str] = mapped_column(Text, nullable=False)
    analysis: Mapped[str] = mapped_column(Text, nullable=False)
    reader_text: Mapped[str] = mapped_column(Text, default="", nullable=False)
    source_url: Mapped[str] = mapped_column(String(1000), nullable=False)
    source_name: Mapped[str] = mapped_column(String(255), nullable=False)
    published_at: Mapped[str] = mapped_column(String(100), nullable=False)

    digest: Mapped["Digest"] = relationship("Digest", back_populates="analytics_items")


class FinalOutput(Base):
    __tablename__ = "final_outputs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    digest_id: Mapped[int] = mapped_column(ForeignKey("digests.id", ondelete="CASCADE"), nullable=False, index=True)
    platform: Mapped[str] = mapped_column(String(50), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    character_count: Mapped[int] = mapped_column(Integer, nullable=False)
    qc_status: Mapped[str] = mapped_column(String(40), default="pending")

    digest: Mapped["Digest"] = relationship("Digest", back_populates="final_outputs")


class Asset(Base):
    __tablename__ = "assets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    digest_id: Mapped[int] = mapped_column(ForeignKey("digests.id", ondelete="CASCADE"), nullable=False, index=True)
    type: Mapped[str] = mapped_column(String(40), nullable=False)
    path: Mapped[str] = mapped_column(String(1000), nullable=False)
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    digest: Mapped["Digest"] = relationship("Digest", back_populates="assets")


class QualityCheck(Base):
    __tablename__ = "quality_checks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    digest_id: Mapped[int] = mapped_column(ForeignKey("digests.id", ondelete="CASCADE"), nullable=False, index=True)
    check_name: Mapped[str] = mapped_column(String(120), nullable=False)
    status: Mapped[str] = mapped_column(String(40), nullable=False)
    comment: Mapped[str] = mapped_column(Text, default="")

    digest: Mapped["Digest"] = relationship("Digest", back_populates="quality_checks")


class ProxyapiSpendDay(Base):
    """Снимок баланса ProxyAPI на границе суток (МСК) для «трат за сегодня»."""

    __tablename__ = "proxyapi_spend_days"

    day: Mapped[date] = mapped_column(Date, primary_key=True)
    opening_balance: Mapped[float | None] = mapped_column(Float, nullable=True)
    last_balance: Mapped[float | None] = mapped_column(Float, nullable=True)
    opening_budget_used: Mapped[float | None] = mapped_column(Float, nullable=True)
    last_budget_used: Mapped[float | None] = mapped_column(Float, nullable=True)


class LlmCostRecord(Base):
    __tablename__ = "llm_cost_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    digest_id: Mapped[int] = mapped_column(ForeignKey("digests.id", ondelete="CASCADE"), nullable=False, index=True)
    step: Mapped[str] = mapped_column(String(40), nullable=False)
    agent_name: Mapped[str] = mapped_column(String(80), nullable=False)
    model: Mapped[str] = mapped_column(String(80), nullable=False)
    request_label: Mapped[str] = mapped_column(String(120), nullable=False)
    cost_rub: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    digest: Mapped["Digest"] = relationship("Digest", back_populates="llm_costs")
