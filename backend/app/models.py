from datetime import datetime

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Digest(Base):
    __tablename__ = "digests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    date: Mapped[datetime] = mapped_column(Date, nullable=False, unique=True, index=True)
    digest_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    status: Mapped[str] = mapped_column(String(40), default="draft", nullable=False)
    current_step: Mapped[str] = mapped_column(String(40), default="draft", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

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
    significance_score: Mapped[int] = mapped_column(Integer, default=1)
    novelty_score: Mapped[int] = mapped_column(Integer, default=1)
    impact_score: Mapped[int] = mapped_column(Integer, default=1)
    total_score: Mapped[int] = mapped_column(Integer, default=3)
    reliability_status: Mapped[str] = mapped_column(String(40), default="⚠️ сомнительный")
    link_status: Mapped[bool] = mapped_column(Boolean, default=False)
    is_foreign_agent: Mapped[bool] = mapped_column(Boolean, default=False)
    is_aggregator: Mapped[bool] = mapped_column(Boolean, default=False)
    is_duplicate: Mapped[bool] = mapped_column(Boolean, default=False)
    verification_comment: Mapped[str] = mapped_column(Text, default="")

    digest: Mapped["Digest"] = relationship("Digest", back_populates="candidates")


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
