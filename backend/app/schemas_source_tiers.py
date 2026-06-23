"""API-схемы редактора источников (tiers) на шаге 0."""

from __future__ import annotations

from pydantic import BaseModel, Field


class SourceHostStatsOut(BaseModel):
    raw_count: int = Field(0, description="Найдено URL в реестре шага 1 за окно")
    pool_count: int = Field(0, description="Подтверждено в пуле кандидатов")
    selected_count: int = Field(0, description="Вошло в топ-5 пользователя")


class SourceHostOut(BaseModel):
    marker: str
    locked: bool = False
    stats: SourceHostStatsOut = Field(default_factory=SourceHostStatsOut)


class SourceTierGroupOut(BaseModel):
    id: str
    label: str
    priority: int
    is_blacklist: bool = False
    hosts: list[SourceHostOut] = Field(default_factory=list)


class SourceTiersEditorOut(BaseModel):
    digest_type: str
    window_days: int = 30
    file_name: str
    groups: list[SourceTierGroupOut] = Field(default_factory=list)


class SourceHostIn(BaseModel):
    marker: str


class SourceTierGroupIn(BaseModel):
    id: str
    hosts: list[SourceHostIn] = Field(default_factory=list)


class SourceTiersEditorUpdate(BaseModel):
    digest_type: str
    groups: list[SourceTierGroupIn] = Field(default_factory=list)
