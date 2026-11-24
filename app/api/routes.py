"""API-эндпоинты RusSupport AI."""

from __future__ import annotations

import logging

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Response
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database.session import get_session
from app.schemas.case import CaseCreateRequest, CaseDetailRead, CaseRead, CaseUpdate
from app.schemas.message import MessageCreate, MessageRead
from app.schemas.summary import SummaryRead
from app.services import case_service
from app.services.ai_agent import AIAgent
from app.services.google_sheets import GoogleSheetsError, GoogleSheetsService
from app.services.summary import build_summary, list_cases

router = APIRouter(prefix="/api", tags=["RusSupport AI"])
logger = logging.getLogger(__name__)


class AICommandPayload(BaseModel):
    """Тело запроса текстовой команды AI-агента."""

    command: str = Field(..., min_length=1)


async def schedule_sheets_export(
    background_tasks: BackgroundTasks,
    session: AsyncSession,
) -> None:
    """Запланировать выгрузку кейсов после успешной мутации БД.

    Данные кейсов снимаются сразу (пока сессия жива), а сам сетевой вызов
    gspread уходит в фоновую задачу — ответ пользователю не ждёт Google.
    """
    if not settings.google_sheets_enabled:
        return

    cases = await list_cases(session)
    logger.info("Запланирована автовыгрузка %d кейсов в Google Sheets", len(cases))

    async def run_export() -> None:
        try:
            await GoogleSheetsService().export_cases(cases)
        except Exception as exc:
            # Фоновая ошибка не должна менять уже отправленный ответ CRUD.
            logger.warning("Автовыгрузка в Google Sheets не удалась: %s", exc)

    background_tasks.add_task(run_export)


@router.get("/health", response_model=dict, summary="Проверка здоровья сервиса")
async def health() -> dict:
    """Вернуть базовый статус сервиса."""
    return {
        "status": "ok",
        "app_name": settings.app_name,
        "env": settings.app_env,
        "ai_enabled": settings.ai_enabled,
    }


@router.get("/cases", response_model=list[CaseRead], summary="Список всех кейсов")
async def cases_api(session: AsyncSession = Depends(get_session)) -> list[CaseRead]:
    """Вернуть все кейсы с приоритетами и выявленными проблемами."""
    return await list_cases(session)


@router.post(
    "/cases", response_model=CaseRead, status_code=201, summary="Создать новую задачу"
)
async def create_case_api(
    payload: CaseCreateRequest,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
) -> CaseRead:
    """Создать кейс; код и начальные статусы вычисляются сервисом."""
    try:
        created = await case_service.create_case(
            session,
            category=payload.category,
            title=payload.title,
            employee=payload.employee,
            assignee=payload.assignee,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    await schedule_sheets_export(background_tasks, session)
    return created


@router.get("/cases/{case_code}", response_model=CaseDetailRead, summary="Детали кейса")
async def case_detail_api(
    case_code: str, session: AsyncSession = Depends(get_session)
) -> CaseDetailRead:
    """Вернуть кейс с полными рабочими полями и всей перепиской."""
    detail = await case_service.get_case_detail(session, case_code)
    if detail is None:
        raise HTTPException(status_code=404, detail=f"Кейс {case_code} не найден")
    return detail


@router.patch(
    "/cases/{case_code}",
    response_model=CaseRead,
    summary="Редактировать карточку вручную",
)
async def update_case_api(
    case_code: str,
    payload: CaseUpdate,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
) -> CaseRead:
    """Обновить переданные поля карточки (название, статусы, ответственный, решение)."""
    case = await case_service.get_case(session, case_code)
    if case is None:
        raise HTTPException(status_code=404, detail=f"Кейс {case_code} не найден")
    updated = await case_service.update_case(session, case, payload)
    await schedule_sheets_export(background_tasks, session)
    return updated


@router.delete(
    "/cases/{case_code}", status_code=204, summary="Удалить кейс с сообщениями"
)
async def delete_case_api(
    case_code: str,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
) -> Response:
    """Удалить кейс и связанные записи (вызывается крестиком на карточке)."""
    if not await case_service.delete_case(session, case_code):
        raise HTTPException(status_code=404, detail=f"Кейс {case_code} не найден")
    await schedule_sheets_export(background_tasks, session)
    return Response(status_code=204)


@router.post(
    "/cases/{case_code}/messages",
    response_model=MessageRead,
    status_code=201,
    summary="Добавить комментарий к кейсу",
)
async def add_message_api(
    case_code: str,
    payload: MessageCreate,
    session: AsyncSession = Depends(get_session),
) -> MessageRead:
    """Добавить новое сообщение/комментарий в переписку кейса."""
    case = await case_service.get_case(session, case_code)
    if case is None:
        raise HTTPException(status_code=404, detail=f"Кейс {case_code} не найден")
    return await case_service.add_message(session, case, payload)


@router.get("/summary", response_model=SummaryRead, summary="Ежедневная сводка")
async def summary_api(session: AsyncSession = Depends(get_session)) -> SummaryRead:
    """Вернуть KPI-сводку и текстовое резюме для руководителя."""
    return await build_summary(session)


@router.post("/ai/command", response_model=dict, summary="Текстовая команда AI-агента")
async def ai_command(
    payload: AICommandPayload,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Отправить команду AI-агенту OpenRouter и вернуть JSON status/message."""
    agent = AIAgent()
    result = await agent.process_command(payload.command.strip(), session)

    # Изменение БД было только при успешном действии: у таких ответов есть
    # поле `action`. `unknown`/fallback/простой ответ ничего не меняют.
    if result.get("status") == "ok" and result.get("action"):
        await schedule_sheets_export(background_tasks, session)

    return result


@router.post("/export/sheets", response_model=dict, summary="Выгрузить кейсы в Google Sheets")
async def export_cases_to_sheets(
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Выгрузить все кейсы в настроенную таблицу Google Sheets."""
    cases = await list_cases(session)
    try:
        return await GoogleSheetsService().export_cases(cases)
    except GoogleSheetsError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
