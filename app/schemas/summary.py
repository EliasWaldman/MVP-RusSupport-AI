"""Схемы ежедневной сводки для руководителя."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Priority = Literal["critical", "high", "medium", "low"]
CaseStatus = Literal["open", "pending", "resolved"]


class SummaryStats(BaseModel):
    """Агрегированные KPI по кейсам."""

    total_cases: int = Field(default=0, ge=0)
    problematic_cases: int = Field(default=0, ge=0)
    resolved_cases: int = Field(default=0, ge=0)
    total_issues: int = Field(default=0, ge=0)
    by_priority: dict[Priority, int] = Field(default_factory=dict)
    by_status: dict[CaseStatus, int] = Field(default_factory=dict)


class SummaryRead(SummaryStats):
    """Ответ `/api/summary`: KPI + текстовое резюме + затронутые кейсы."""

    model_config = ConfigDict(from_attributes=True)

    generated_at: datetime
    headline: str = ""
    highlights: list[str] = Field(default_factory=list)
    problematic_codes: list[str] = Field(default_factory=list)
