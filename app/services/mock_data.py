"""Загрузка демонстрационных данных из data/mock_data.json.

При старте приложения (если включено в настройках) кейсы и сообщения
загружаются в SQLite. Повторная загрузка безопасна (upsert по code).
"""

from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import models as m
from app.schemas.case import CaseCreate
from app.schemas.message import MessageCreate

logger = logging.getLogger(__name__)


async def _upsert_case(session: AsyncSession, data: dict[str, Any]) -> m.CaseORM:
    """Создать или обновить кейс по code."""
    payload = CaseCreate(**data)
    result = await session.execute(
        select(m.CaseORM).where(m.CaseORM.code == payload.code)
    )
    existing = result.scalars().first()
    if existing is None:
        case = m.CaseORM(
            code=payload.code,
            title=payload.title,
            category=payload.category,
            status=payload.status,
            crm_status=payload.crm_status,
            first_day_tomorrow=payload.first_day_tomorrow,
            documents_expected=payload.documents_expected,
            documents_sent_by_employee=payload.documents_sent_by_employee,
            documents_confirmed=payload.documents_confirmed,
            last_work_day_today=payload.last_work_day_today,
            official_confirmation_provided=payload.official_confirmation_provided,
            employee_claimed_discussed=payload.employee_claimed_discussed,
            delivery_status=payload.delivery_status,
            delivery_deadline=payload.delivery_deadline,
            delivery_eta=payload.delivery_eta,
            delivery_delay_reported=payload.delivery_delay_reported,
            delivery_confirmed=payload.delivery_confirmed,
        )
        session.add(case)
        await session.flush()
    else:
        existing.title = payload.title
        existing.category = payload.category
        existing.status = payload.status
        existing.crm_status = payload.crm_status
        existing.first_day_tomorrow = payload.first_day_tomorrow
        existing.documents_expected = payload.documents_expected
        existing.documents_sent_by_employee = payload.documents_sent_by_employee
        existing.documents_confirmed = payload.documents_confirmed
        existing.last_work_day_today = payload.last_work_day_today
        existing.official_confirmation_provided = payload.official_confirmation_provided
        existing.employee_claimed_discussed = payload.employee_claimed_discussed
        existing.delivery_status = payload.delivery_status
        existing.delivery_deadline = payload.delivery_deadline
        existing.delivery_eta = payload.delivery_eta
        existing.delivery_delay_reported = payload.delivery_delay_reported
        existing.delivery_confirmed = payload.delivery_confirmed
        case = existing
    return case


async def _upsert_messages(session: AsyncSession, case_orm: m.CaseORM, messages: list[dict[str, Any]]) -> None:
    """Заменить сообщения кейса на загруженные (демо-данные)."""
    await session.execute(delete(m.MessageORM).where(m.MessageORM.case_id == case_orm.id))
    for msg_data in messages:
        msg = MessageCreate(**msg_data)
        session.add(
            m.MessageORM(
                case_id=case_orm.id,
                case_code=case_orm.code,
                role=msg.role,
                author=msg.author,
                text=msg.text,
                created_at=msg.created_at,
            )
        )


async def load_mock_cases(session: AsyncSession) -> int:
    """Загрузить кейсы и сообщения из mock-файла. Вернуть число кейсов."""
    path = settings.mock_data_path
    if not path.exists():
        logger.info("Mock-файл не найден: %s", path)
        return 0

    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)

    cases = data.get("cases", [])
    loaded = 0
    for case_data in cases:
        case_orm = await _upsert_case(session, case_data)
        messages = case_data.get("messages", [])
        await _upsert_messages(session, case_orm, messages)
        loaded += 1

    await session.commit()
    logger.info("Загружено демонстрационных кейсов: %d", loaded)
    return loaded