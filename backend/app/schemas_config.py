from typing import Any

from pydantic import BaseModel, Field


class AppConfigItemOut(BaseModel):
    label: str
    value: str
    source: str
    hint: str | None = None
    why_chosen: str = ""
    alternatives: str = ""


class AppConfigSectionOut(BaseModel):
    id: str
    title: str
    file: str
    items: list[AppConfigItemOut] = Field(default_factory=list)


class AppConfigResponse(BaseModel):
    sections: list[AppConfigSectionOut] = Field(default_factory=list)
    env_overrides: list[str] = Field(default_factory=list)
    note: str = ""
