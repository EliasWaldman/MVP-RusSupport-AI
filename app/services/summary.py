"""Формирование ежедневной сводки для руководителя.

Сводка строится из детерминированных правил приоритизации. При включённом
AI-провайдере (ai_enabled=True) текстовый блок резюме может дополняться
через абстракцию провайдера, но статусы и приоритеты остаются правилами.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import models as m
from app.schemas.case import CaseRead
from app.schemas.summary import SummaryRead
from app.services.prioritization import (
    compute_priority_score,
    detect_issues,
    is_problematic,
    resolve_priority,
)


def case_signals(case: m.CaseORM):
    """Преобразует ORM-строку в валидированные сигналы для правил."""
    from app.schemas.case import CaseSignals

    return CaseSignals(
        code=case.code,
        title=case.title,
        category=case.category,
        status=case.status,
        crm_status=case.crm_status,
        first_day_tomorrow=case.first_day_tomorrow,
        documents_expected=case.documents_expected,
        documents_sent_by_employee=case.documents_sent_by_employee,
        documents_confirmed=case.documents_confirmed,
        last_work_day_today=case.last_work_day_today,
        official_confirmation_provided=case.official_confirmation_provided,
        employee_claimed_discussed=case.employee_claimed_discussed,
        delivery_status=case.delivery_status,
        delivery_deadline=case.delivery_deadline,
        delivery_eta=case.delivery_eta,
        delivery_delay_reported=case.delivery_delay_reported,
        delivery_confirmed=case.delivery_confirmed,
    )


def to_case_read(case: m.CaseORM) -> CaseRead:
    """ORM -> Pydantic-схема с вычисленными приоритетом и проблемами."""
    signals = case_signals(case)
    issues = detect_issues(signals)
    priority = resolve_priority(compute_priority_score(issues))
    return CaseRead(
        code=case.code,
        title=case.title,
        category=case.category,
        status=case.status,
        crm_status=case.crm_status,
        priority=priority,
        issues=issues,
        created_at=case.created_at,
        task_status=case.task_status,  # type: ignore[arg-type]
        assignee=case.assignee,
        resolution=case.resolution,
        updated_at=case.updated_at,
    )


async def list_cases(session: AsyncSession) -> list[CaseRead]:
    """Список всех кейсов с приоритетами и проблемами."""
    result = await session.execute(
        select(m.CaseORM).order_by(m.CaseORM.created_at.desc())
    )
    return [to_case_read(case) for case in result.scalars().all()]


async def build_summary(session: AsyncSession) -> SummaryRead:
    """Собрать KPI-сводку по всем кейсам."""
    result = await session.execute(select(m.CaseORM).order_by(m.CaseORM.created_at.asc()))
    cases = result.scalars().all()

    total = len(cases)
    problematic = 0
    resolved = 0
    total_issues = 0
    by_priority: dict[str, int] = {}
    by_status: dict[str, int] = {}
    highlights: list[str] = []
    problematic_codes: list[str] = []

    for case in cases:
        signals = case_signals(case)
        issues = detect_issues(signals)
        priority = resolve_priority(compute_priority_score(issues))

        by_priority[priority] = by_priority.get(priority, 0) + 1
        by_status[case.status] = by_status.get(case.status, 0) + 1
        total_issues += len(issues)

        if is_problematic(issues):
            problematic += 1
            problematic_codes.append(case.code)
        if case.status == "resolved":
            resolved += 1
        if priority == "critical":
            highlights.append(f"{case.code} — {case.title} (приоритет: {priority})")

    headline = (
        f"Всего кейсов: {total}. Проблемных: {problematic}. "
        f"Решённых: {resolved}. Выявлено инцидентов: {total_issues}."
    )
    if problematic_codes:
        headline += f" Требуют внимания руководителя: {', '.join(problematic_codes)}."
    else:
        headline += " Критических расхождений не выявлено."

    return SummaryRead(
        generated_at=datetime.now(timezone.utc),
        headline=headline,
        total_cases=total,
        problematic_cases=problematic,
        resolved_cases=resolved,
        total_issues=total_issues,
        by_priority=by_priority,  # type: ignore[arg-type]
        by_status=by_status,  # type: ignore[arg-type]
        highlights=highlights,
        problematic_codes=problematic_codes,
    )
