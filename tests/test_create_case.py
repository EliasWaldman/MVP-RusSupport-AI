"""Тесты создания новых задач через сервис и AI-команду."""

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.ai.base import AIProviderError
from app.database import models as m
from app.database.session import Base
from app.schemas.case import CaseUpdate
from app.services import case_service
from app.services.ai_agent import AIAgent


class FakeProvider:
    """Подставной провайдер: отдаёт интент или имитирует отказ API."""

    def __init__(self, intent: dict | None = None, *, error: bool = False) -> None:
        self.intent = intent
        self.error = error
        self.system_prompt: str | None = None

    async def generate_command_intent(
        self, user_text: str, cases_context: list, *, system_prompt: str | None = None
    ) -> dict:
        self.system_prompt = system_prompt
        if self.error:
            raise AIProviderError("AI недоступен")
        return self.intent or {}


@pytest.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with AsyncSession(engine, expire_on_commit=False) as db:
        yield db
    await engine.dispose()


def test_next_case_code():
    codes = ["S-17", "S-24", "P-08", "P-09"]
    assert case_service.next_case_code(codes, "onboarding") == "S-25"
    assert case_service.next_case_code(codes, "delivery") == "P-10"
    assert case_service.next_case_code([], "offboarding") == "S-01"


async def test_create_case_service_persists_card(session):
    created = await case_service.create_case(
        session,
        category="onboarding",
        title="Оформление сотрудника Елена Аверина",
        employee="Елена Аверина",
        assignee="HR manager",
    )
    assert created.code == "S-01"
    assert created.assignee == "HR manager"
    assert created.task_status == "open"
    assert created.crm_status == "pending"

    messages = (
        await session.execute(
            select(m.MessageORM).where(m.MessageORM.case_code == "S-01")
        )
    ).scalars().all()
    assert len(messages) == 1
    assert "Елена Аверина" in messages[0].text


async def test_ai_command_creates_case(session):
    provider = FakeProvider(
        {
            "action": "create_case",
            "params": {
                "category": "onboarding",
                "title": "Оформление сотрудника Елена Аверина",
                "employee": "Елена Аверина",
                "assignee": "HR manager",
            },
        }
    )
    result = await AIAgent(provider=provider).process_command(
        "Создай задачу на оформление сотрудника Елена Аверина, HR manager",
        session,
    )

    assert result == {
        "status": "ok",
        "message": "Действие выполнено: create_case для кейса S-01",
        "action": "create_case",
        "case_code": "S-01",
    }
    case = await case_service.get_case(session, "S-01")
    assert case is not None
    assert case.title == "Оформление сотрудника Елена Аверина"
    assert case.assignee == "HR manager"
    assert "create_case" in (provider.system_prompt or "")


async def test_fallback_creates_case_when_ai_unavailable(session):
    result = await AIAgent(provider=FakeProvider(error=True)).process_command(
        "Создай задачу на увольнение сотрудника Ольга Петрова, HR", session
    )

    assert result["status"] == "ok"
    assert result["action"] == "create_case"
    case = await case_service.get_case(session, "S-01")
    assert case is not None
    assert case.category == "offboarding"
    assert case.assignee == "HR"


async def test_manual_edit_updates_case_fields(session):
    await case_service.create_case(
        session,
        category="onboarding",
        title="Оформление сотрудника Елена Аверина",
        employee="Елена Аверина",
        assignee="HR manager",
    )
    case = await case_service.get_case(session, "S-01")
    assert case is not None

    updated = await case_service.update_case(
        session,
        case,
        CaseUpdate(
            title="Оформление Елены Авериной (исправлено)",
            status="resolved",
            crm_status="completed",
            task_status="done",
            assignee="Иван Иванов",
            resolution="Документы приняты",
        ),
    )

    assert updated.title == "Оформление Елены Авериной (исправлено)"
    assert updated.status == "resolved"
    assert updated.crm_status == "completed"
    assert updated.task_status == "done"
    assert updated.assignee == "Иван Иванов"
    assert updated.resolution == "Документы приняты"

    stored = await case_service.get_case(session, "S-01")
    assert stored is not None
    assert stored.crm_status == "completed"


async def test_delete_case_removes_case_and_messages(session):
    await case_service.create_case(
        session,
        category="onboarding",
        title="Оформление сотрудника Елена Аверина",
        employee="Елена Аверина",
    )

    assert await case_service.delete_case(session, "S-01") is True
    assert await case_service.get_case(session, "S-01") is None

    messages = (await session.execute(select(m.MessageORM))).scalars().all()
    assert messages == []
    assert await case_service.delete_case(session, "S-01") is False
