"""CRUD-операции рабочего места support-специалиста."""

from __future__ import annotations

import re
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import models as m
from app.schemas.case import CaseDetailRead, CaseRead, CaseUpdate
from app.schemas.message import MessageCreate, MessageRead
from app.services.summary import to_case_read


CATEGORY_PREFIXES: dict[str, str] = {
    "onboarding": "S",
    "offboarding": "S",
    "delivery": "P",
}
_CODE_RE = re.compile(r"^([SP])-(\d+)$")


def next_case_code(existing_codes: list[str], category: str) -> str:
    """Сгенерировать следующий свободный код: S-N для кадров, P-N для доставки."""
    prefix = CATEGORY_PREFIXES[category]
    numbers = []
    for raw in existing_codes:
        match = _CODE_RE.match((raw or "").strip().upper())
        if match and match.group(1) == prefix:
            numbers.append(int(match.group(2)))
    return f"{prefix}-{max(numbers, default=0) + 1:02d}"


async def create_case(
    session: AsyncSession,
    *,
    category: str,
    title: str,
    employee: str | None = None,
    assignee: str | None = None,
) -> CaseRead:
    """Создать новую задачу/кейс.

    Называние кода и начальные статусы определяются детерминированно, чтобы
    AI-агент не мог перезаписать CRM-поля. Возвращает готовую карточку.
    """
    normalized = (category or "").strip().lower()
    if normalized not in CATEGORY_PREFIXES:
        raise ValueError(f"Неизвестная категория: {category}")

    clean_title = (title or "").strip()[:200]
    if not clean_title:
        raise ValueError("Название задачи не может быть пустым")

    rows = await session.execute(select(m.CaseORM.code))
    code = next_case_code(list(rows.scalars().all()), normalized)

    case = m.CaseORM(
        code=code,
        title=clean_title,
        category=normalized,
        status="open",
        crm_status="pending",
        task_status="open",
        assignee=(assignee or "").strip()[:64] or None,
    )
    session.add(case)
    await session.flush()  # нужен case.id для связи с сообщением

    note = f"Задача создана через AI Command Bar: {clean_title}."
    if employee and employee.strip():
        note += f" Сотрудник: {employee.strip()}."
    session.add(
        m.MessageORM(
            case_id=case.id,
            case_code=code,
            role="support",
            author="AI Command Bar",
            text=note,
            created_at=datetime.now(timezone.utc),
        )
    )

    await session.commit()
    await session.refresh(case)
    return to_case_read(case)


async def delete_case(session: AsyncSession, case_code: str) -> bool:
    """Удалить кейс вместе с сообщениями и инцидентами. True, если удалён."""
    case = await get_case(session, case_code)
    if case is None:
        return False
    await session.delete(case)
    await session.commit()
    return True


async def get_case(session: AsyncSession, case_code: str) -> m.CaseORM | None:
    """Найти кейс по коду (например, S-17). None, если не существует."""
    result = await session.execute(
        select(m.CaseORM).where(m.CaseORM.code == case_code)
    )
    return result.scalars().first()


async def get_case_detail(session: AsyncSession, case_code: str) -> CaseDetailRead | None:
    """Полный дайджест кейса: поля + выявленные проблемы + переписка."""
    case = await get_case(session, case_code)
    if case is None:
        return None
    read = to_case_read(case)
    messages = [MessageRead.model_validate(msg) for msg in case.messages]
    return CaseDetailRead(**read.model_dump(), messages=messages)


async def update_case(
    session: AsyncSession, case: m.CaseORM, payload: CaseUpdate
) -> CaseRead:
    """Обновить переданные поля карточки (ручное редактирование).

    Меняются только явно переданные поля (exclude_unset). Приоритет и
    выявленные проблемы пересчитываются из сигналов; статус CRM меняется
    только если пользователь прислал его в payload вручную.
    """
    data = payload.model_dump(exclude_unset=True)
    for field, value in data.items():
        setattr(case, field, value)
    await session.commit()
    await session.refresh(case)
    return to_case_read(case)


async def add_message(
    session: AsyncSession, case: m.CaseORM, payload: MessageCreate
) -> MessageRead:
    """Добавить комментарий/сообщение к кейсу."""
    msg = m.MessageORM(
        case_id=case.id,
        case_code=case.code,
        role=payload.role,
        author=payload.author.strip() or "support",
        text=payload.text,
        created_at=payload.created_at or datetime.now(timezone.utc),
    )
    session.add(msg)
    await session.commit()
    await session.refresh(msg)
    return MessageRead.model_validate(msg)


ACTION_TASK_STATUSES: dict[str, str] = {
    "confirm_docs": "in_progress",
    "confirm_offboarding": "in_progress",
    "update_delivery": "waiting",
    "close": "done",
}
_TIME_PATTERN = re.compile(r"^(?:[01]?\d|2[0-3]):[0-5]\d$")


async def apply_action(
    session: AsyncSession,
    case_code: str,
    action: str,
    delivery_time: str | None = None,
) -> dict:
    """Имитация действия support-специалиста над кейсом.

    CRM-статусы не перезаписываются: меняются только рабочие поля и флаги,
    которые участвуют в детерминированных правилах приоритизации.
    """
    case = await get_case(session, case_code)
    if case is None:
        return {"found": False}

    if action == "confirm_docs":
        if case.category != "onboarding":
            return {"found": True, "ok": False, "error": "Действие доступно только для оформления."}
        case.documents_confirmed = True
        case.task_status = ACTION_TASK_STATUSES[action]
        message = f"Документы для кейса {case.code} подтверждены. Статус обновлен."

    elif action == "confirm_offboarding":
        if case.category != "offboarding":
            return {"found": True, "ok": False, "error": "Действие доступно только для увольнений."}
        case.official_confirmation_provided = True
        case.task_status = ACTION_TASK_STATUSES[action]
        message = f"Увольнение {case.code} официально подтверждено руководителем."

    elif action == "update_delivery":
        if case.category != "delivery":
            return {"found": True, "ok": False, "error": "Действие доступно только для доставки."}
        time_value = (delivery_time or "").strip()
        if not _TIME_PATTERN.fullmatch(time_value):
            return {
                "found": True,
                "ok": False,
                "error": "Укажите время в формате ЧЧ:MM, например 13:30.",
            }
        case.task_status = ACTION_TASK_STATUSES[action]
        case.resolution = f"Ожидаемое время доставки: {time_value}"
        message = (
            f"Ожидаемое время доставки для {case.code} обновлено на {time_value}. "
            "Получатель предупрежден."
        )

    elif action == "close":
        case.task_status = ACTION_TASK_STATUSES[action]
        case.status = "resolved"
        message = "Кейс закрыт."

    else:
        return {"found": True, "ok": False, "error": f"Неизвестное действие: {action}"}

    await session.commit()
    await session.refresh(case)
    read = to_case_read(case)

    return {
        "found": True,
        "ok": True,
        "action": action,
        "message": message,
        "code": case.code,
        "new_status": case.task_status,
        "status": case.status,
        "case": read,
    }
