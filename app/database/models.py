"""ORM-модели: кейсы, сообщения, инциденты.

CRM остаётся официальным источником статусов — поля с суффиксом `_crm`
хранят официальные данные. Сообщения чата связаны с кейсом через FK.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from sqlalchemy import (
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.session import Base


class CaseORM(Base):
    __tablename__ = "cases"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(32), unique=True, index=True, nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    category: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="open", nullable=False)
    crm_status: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    priority: Mapped[str] = mapped_column(String(16), default="low", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=func.now(), server_default=func.now(), nullable=False
    )

    # --- Рабочее место support-специалиста ---
    # Внутренний статус (отличается от официального статуса CRM).
    task_status: Mapped[str] = mapped_column(String(16), default="open", nullable=False)
    assignee: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    resolution: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime, onupdate=func.now(), nullable=True)

    # Онбординг / увольнение.
    first_day_tomorrow: Mapped[bool] = mapped_column(default=False, nullable=False)
    documents_expected: Mapped[bool] = mapped_column(default=False, nullable=False)
    documents_sent_by_employee: Mapped[bool] = mapped_column(default=False, nullable=False)
    documents_confirmed: Mapped[bool] = mapped_column(default=False, nullable=False)

    last_work_day_today: Mapped[bool] = mapped_column(default=False, nullable=False)
    official_confirmation_provided: Mapped[bool] = mapped_column(default=False, nullable=False)
    employee_claimed_discussed: Mapped[bool] = mapped_column(default=False, nullable=False)

    # Поставка.
    delivery_status: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    delivery_deadline: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    delivery_eta: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    delivery_delay_reported: Mapped[bool] = mapped_column(default=False, nullable=False)
    delivery_confirmed: Mapped[bool] = mapped_column(default=False, nullable=False)

    messages: Mapped[list["MessageORM"]] = relationship(
        back_populates="case", cascade="all, delete-orphan", lazy="selectin"
    )
    issues: Mapped[list["IssueORM"]] = relationship(
        back_populates="case", cascade="all, delete-orphan", lazy="selectin"
    )


class MessageORM(Base):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("cases.id"), nullable=False)
    case_code: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    role: Mapped[str] = mapped_column(String(32), default="support", nullable=False)
    author: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    case: Mapped[CaseORM] = relationship(back_populates="messages", lazy="selectin")


class IssueORM(Base):
    __tablename__ = "issues"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("cases.id"), nullable=False)
    case_code: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), default="info", nullable=False)
    source: Mapped[str] = mapped_column(String(16), default="rule", nullable=False)
    basis: Mapped[str] = mapped_column(Text, nullable=False)

    case: Mapped[CaseORM] = relationship(back_populates="issues", lazy="selectin")