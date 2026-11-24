"""Схемы кейсов, сигналов и выявленных проблем.

`CaseSignals` — валидированный вход для детерминированных правил
приоритизации. `CaseRead` — модель ответа API / рендера дашборда.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.message import MessageRead

# --- Перечисления предметной области (через Literal — валидируются Pydantic) ---

CaseCategory = Literal["onboarding", "offboarding", "delivery"]
CaseStatus = Literal["open", "pending", "resolved"]
Priority = Literal["critical", "high", "medium", "low"]
IssueSeverity = Literal["info", "warning", "critical"]
# Источник вывода: CRM — официальный источник, чат — дополнительное свидетельство.
IssueSource = Literal["crm", "chat", "rule", "ai"]
DeliveryStatus = Literal["pending", "in_transit", "accepted", "completed"]
# Внутренний статус работы support-отдела (не путать с официальным статусом CRM).
TaskStatus = Literal["open", "in_progress", "waiting", "done"]


class CaseIssue(BaseModel):
    """Один выявленный риск / расхождение по кейсу.

    Для каждого вывода явно фиксируются источник и основание, чтобы
    предположения не выдавались за подтверждённые факты.
    """

    model_config = ConfigDict(frozen=True)

    code: str = Field(..., min_length=1, max_length=64)
    title: str = Field(..., min_length=1, max_length=200)
    severity: IssueSeverity
    source: IssueSource
    basis: str = Field(..., min_length=1, max_length=500)


class CaseSignals(BaseModel):
    """Валидированные сигналы кейса для правил приоритизации."""

    model_config = ConfigDict(str_strip_whitespace=True)

    code: str = Field(..., min_length=1, max_length=32)
    title: str = Field(..., min_length=1, max_length=200)
    category: CaseCategory
    status: CaseStatus = "open"
    crm_status: str | None = Field(default=None, max_length=64)

    # --- Онбординг (S-*) ---
    first_day_tomorrow: bool = False
    documents_expected: bool = False
    documents_sent_by_employee: bool = False
    documents_confirmed: bool = False

    # --- Оффбординг / увольнение (S-*) ---
    last_work_day_today: bool = False
    official_confirmation_provided: bool = False
    employee_claimed_discussed: bool = False

    # --- Поставка (P-*) ---
    delivery_status: DeliveryStatus | None = None
    delivery_deadline: date | None = None
    delivery_eta: date | None = None
    delivery_delay_reported: bool = False
    delivery_confirmed: bool = False


class CaseRead(BaseModel):
    """Кейс для API и дашборда."""

    model_config = ConfigDict(from_attributes=True)

    code: str
    title: str
    category: CaseCategory
    status: CaseStatus
    crm_status: str | None = None
    priority: Priority
    issues: list[CaseIssue] = Field(default_factory=list)
    created_at: datetime | None = None

    # --- Рабочее место support-специалиста ---
    task_status: TaskStatus = "open"
    assignee: str | None = None
    resolution: str | None = None
    updated_at: datetime | None = None

    @property
    def has_problems(self) -> bool:
        return any(issue.severity != "info" for issue in self.issues)


class CaseUpdate(BaseModel):
    """Частичное обновление полей кейса (PATCH, ручное редактирование)."""

    model_config = ConfigDict(str_strip_whitespace=True)

    title: str | None = Field(default=None, min_length=1, max_length=200)
    status: CaseStatus | None = None
    # crm_status — официальный статус: меняется только вручную пользователем,
    # AI и детерминированные правила его не перезаписывают.
    crm_status: str | None = Field(default=None, max_length=64)
    task_status: TaskStatus | None = None
    assignee: str | None = Field(default=None, max_length=64)
    resolution: str | None = Field(default=None, max_length=2000)


class CaseDetailRead(CaseRead):
    """Детальный ответ: кейс + полная переписка."""

    messages: list[MessageRead] = Field(default_factory=list)


class CaseCreateRequest(BaseModel):
    """Создание новой задачи через API или AI-команду (код генерируется сервисом)."""

    model_config = ConfigDict(str_strip_whitespace=True)

    category: CaseCategory
    title: str = Field(..., min_length=1, max_length=200)
    employee: str | None = Field(default=None, max_length=200)
    assignee: str | None = Field(default=None, max_length=64)


class CaseCreate(BaseModel):
    """Входная схема создания кейса (например, при загрузке mock-данных)."""

    model_config = ConfigDict(str_strip_whitespace=True)

    code: str = Field(..., min_length=1, max_length=32)
    title: str = Field(..., min_length=1, max_length=200)
    category: CaseCategory
    status: CaseStatus = "open"
    crm_status: str | None = Field(default=None, max_length=64)

    first_day_tomorrow: bool = False
    documents_expected: bool = False
    documents_sent_by_employee: bool = False
    documents_confirmed: bool = False

    last_work_day_today: bool = False
    official_confirmation_provided: bool = False
    employee_claimed_discussed: bool = False

    delivery_status: DeliveryStatus | None = None
    delivery_deadline: date | None = None
    delivery_eta: date | None = None
    delivery_delay_reported: bool = False
    delivery_confirmed: bool = False


class CaseStatusUpdate(BaseModel):
    """Смена статуса кейса (CRM остаётся официальным источником)."""

    status: CaseStatus
