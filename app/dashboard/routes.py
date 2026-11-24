"""Веб-дашборд: кейсы с деталями, интеграции, аналитика и настройки."""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, Form
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.routes import schedule_sheets_export
from app.config import BASE_DIR, settings
from app.database import models as m
from app.database.session import get_session
from app.services import case_service
from app.services.integrations_store import all_data, set_key
from app.services.summary import build_summary, list_cases, to_case_read

router = APIRouter(tags=["dashboard"])
templates = Jinja2Templates(directory="app/templates")

DELIVERY_STATUS_LABELS: dict[str, str] = {
    "pending": "Ожидает отправки",
    "in_transit": "В пути",
    "accepted": "Принято",
    "completed": "Завершено",
}


class CaseActionPayload(BaseModel):
    """Тело запроса для действия над кейсом с дашборда."""

    action: str = Field(
        ...,
        pattern="^(confirm_docs|confirm_offboarding|update_delivery|close)$",
    )
    delivery_time: str | None = None

# Плейсхолдер в поле ввода активной карточки. Если форма вернёт именно его,
# значит пользователь не менял ключ — старое значение остаётся в файле.
MASKED_KEY = "********"


def mask_key(value: str) -> str:
    """Безопасное представление ключа для интерфейса (без секрета)."""
    key = (value or "").strip()
    if not key:
        return ""
    if len(key) <= 8:
        return MASKED_KEY
    # Показываем только первые 3 символа (например, `sk-`), остальное скрыто.
    return f"{key[:3]}...****"


# Путь к .env вынесен в константу, чтобы тесты могли подменить его на temp-файл.
ENV_PATH = BASE_DIR / ".env"


def mask_spreadsheet_id(value: str) -> str:
    """Короткий вид ID таблицы для карточки: `1JdC0...ObTQ`."""
    spreadsheet_id = (value or "").strip()
    if len(spreadsheet_id) <= 12:
        return spreadsheet_id
    return f"{spreadsheet_id[:5]}...{spreadsheet_id[-4:]}"


def _format_env_value(value: str) -> str:
    """Подготовить значение для строки `.env` (кавычки только когда нужно)."""
    if value == "":
        return ""
    if any(char.isspace() for char in value) or any(
        char in value for char in ('"', "'", "#", "\\")
    ):
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return value


def _update_env_value(name: str, value: str) -> None:
    """Изменить или добавить переменную в `.env`, сохранив остальные строки.

    Дубликаты удаляются: python-dotenv берёт последнее вхождение, и старое
    значение могло бы незаметно переопределить новое после перезапуска.
    """
    lines = ENV_PATH.read_text(encoding="utf-8").splitlines() if ENV_PATH.exists() else []
    prefix = f"{name}="
    rendered = f"{name}={_format_env_value(value)}"

    updated = False
    result: list[str] = []
    for line in lines:
        stripped = line.lstrip()
        key_part = stripped[7:] if stripped.startswith("export ") else stripped
        if key_part.startswith(prefix):
            if not updated:
                result.append(rendered)
                updated = True
            continue
        result.append(line)

    if not updated:
        result.append(rendered)
    ENV_PATH.write_text("\n".join(result) + "\n", encoding="utf-8")


def _ilike_contains(value: str) -> str:
    """Экранировать пользовательский ввод для `LIKE '%...%'`."""
    escaped = (
        value.replace("\\", "\\\\")
        .replace("%", "\\%")
        .replace("_", "\\_")
    )
    return f"%{escaped}%"


def _case_matches(case: m.CaseORM, needle: str) -> bool:
    """Unicode-совпадание по коду, названию и категории (для SQLite)."""
    return any(
        needle in (value or "").casefold()
        for value in (case.code, case.title, case.category)
    )


SERVICE_NAMES: dict[str, str] = {
    "openrouter": "OpenRouter",
    "gigachat": "GigaChat",
    "telegram": "Telegram",
    "hh": "hh.ru",
    "google": "Google Sheets",
    "crm": "CRM",
}


def _load_integrations_data() -> dict[str, dict[str, str]]:
    """Подключённые сервисы для шаблона — только маски ключей.

    Ключ может прийти из ``data/integrations.json`` или из ``.env``
    (GigaChat, Telegram, OpenRouter). Google Sheets считается подключённой
    по включённой выгрузке и заполненному ID таблицы в ``.env``.
    В контекст шаблона попадает маска и источник, но не само значение секрета.
    """
    connected: dict[str, dict[str, str]] = {}
    for service, item in all_data().items():
        key = str(item.get("api_key") or "").strip()
        if key:
            connected[service] = {"source": "file", "masked_key": mask_key(key)}

    env_keys = {
        "telegram": settings.telegram_bot_token,
        "gigachat": settings.gigachat_credentials,
        "openrouter": settings.openrouter_api_key,
    }
    for service, value in env_keys.items():
        if service not in connected and (value or "").strip():
            connected[service] = {
                "source": "env",
                "masked_key": mask_key(value),
            }

    # У Google Sheets нет «ключа» в .env: активность определяется настройками
    # выгрузки. ID таблицы не секрет и в шаблон не передаётся.
    if (
        settings.google_sheets_enabled
        and (settings.google_sheets_spreadsheet_id or "").strip()
    ):
        connected.setdefault("google", {"source": "env", "masked_key": ""})

    return connected


def _build_integrations(integrations_data: dict[str, dict[str, str]]) -> list[dict]:
    """Список интеграций со стабильными ID и текущим статусом."""

    def card(
        key: str,
        name: str,
        icon: str,
        description: str,
        category: str,
        inactive_label: str = "Не подключено",
    ) -> dict:
        is_active = key in integrations_data
        return {
            # ID используется в data-service, id карточки и URL сохранения.
            # Не должен зависеть от отображаемого названия (hh.ru, google...).
            "key": key,
            "name": name,
            "icon": icon,
            "description": description,
            "category": category,
            "status": "active" if is_active else "inactive",
            "status_label": "Активно" if is_active else inactive_label,
            "action": "Изменить" if is_active else "Настроить",
            "href": f"/integrations#{key}",
        }

    return [
        card(
            "openrouter",
            "OpenRouter",
            "◆",
            "LLM для текстового анализа и рекомендаций.",
            "AI / LLM",
        ),
        card(
            "gigachat",
            "GigaChat",
            "G",
            "Российский LLM-провайдер",
            "AI / LLM",
            "Настроить",
        ),
        card(
            "hh",
            "hh.ru",
            "⟁",
            "Парсинг вакансий и сопровождение кандидатов.",
            "HR",
        ),
        card(
            "google",
            "Google Sheets",
            "▦",
            "Выгрузка отчётов и сводок в таблицы.",
            "Таблицы",
        ),
        card(
            "telegram",
            "Telegram",
            "✦",
            "Уведомления и чаты со сотрудниками.",
            "Мессенджеры",
        ),
        card(
            "crm",
            "CRM (Bitrix24 / AmoCRM)",
            "★",
            "Официальный источник статусов кейсов.",
            "CRM",
        ),
    ]


@router.get("/", include_in_schema=False)
async def dashboard_page(
    request: Request,
    q: str = "",
    session: AsyncSession = Depends(get_session),
):
    """Главная страница: карточки раскрываются прямо на дашборде."""
    query = (q or "").strip()
    if query:
        if any(ord(char) > 127 for char in query):
            # SQLite lower()/ilike не понимает кириллицу — сравниваем в Python.
            result = await session.execute(
                select(m.CaseORM).order_by(m.CaseORM.created_at.desc())
            )
            needle = query.casefold()
            cases = [
                to_case_read(case)
                for case in result.scalars().all()
                if _case_matches(case, needle)
            ]
        else:
            # ASCII-запрос фильтруется прямо в БД: код, название и категория.
            pattern = _ilike_contains(query)
            result = await session.execute(
                select(m.CaseORM)
                .where(
                    or_(
                        m.CaseORM.code.ilike(pattern, escape="\\"),
                        m.CaseORM.title.ilike(pattern, escape="\\"),
                        m.CaseORM.category.ilike(pattern, escape="\\"),
                    )
                )
                .order_by(m.CaseORM.created_at.desc())
            )
            cases = [to_case_read(case) for case in result.scalars().all()]
    else:
        cases = await list_cases(session)

    summary = await build_summary(session)
    # Раздел «Задачи»: все незакрытые кейсы, включая созданные AI-командой.
    # При поиске список задач тоже сужается до найденных кейсов.
    tasks = [case for case in cases if case.task_status != "done"]

    # Дополнительные данные для раскрывающихся деталей каждой карточки.
    rows = await session.execute(select(m.CaseORM))
    case_extras: dict[str, dict] = {}
    for case in rows.scalars().all():
        messages = sorted(
            case.messages,
            key=lambda item: item.created_at or datetime.min,
            reverse=True,
        )
        last_message = messages[0] if messages else None
        case_extras[case.code] = {
            "last_message": last_message,
            "delivery_status": DELIVERY_STATUS_LABELS.get(
                case.delivery_status or "", case.delivery_status or "—"
            ),
            "resolution": case.resolution,
        }

    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={
            "app_name": settings.app_name,
            "active_page": "dashboard",
            "cases": cases,
            "tasks": tasks,
            "case_extras": case_extras,
            "summary": summary,
            "priority_order": ["critical", "high", "medium", "low"],
            "search_query": query,
        },
    )


@router.post("/api/cases/{case_code}/action", response_class=JSONResponse)
async def case_action(
    case_code: str,
    payload: CaseActionPayload,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
):
    """Выполнить действие support-специалиста над кейсом (имитация CRM)."""
    result = await case_service.apply_action(
        session, case_code, payload.action, payload.delivery_time
    )
    if not result["found"]:
        raise HTTPException(status_code=404, detail=f"Кейс {case_code} не найден")
    if not result["ok"]:
        return JSONResponse(
            status_code=400,
            content={"status": "error", "detail": result["error"]},
        )

    await schedule_sheets_export(background_tasks, session)
    return JSONResponse(
        status_code=200,
        content={
            "status": "ok",
            "code": result["code"],
            "new_status": result["new_status"],
            "case_status": result["status"],
            "message": result["message"],
        },
    )


@router.get("/integrations", include_in_schema=False)
async def integrations_page(request: Request):
    """Страница интеграций: подключённые и доступные сервисы."""
    integrations_data = _load_integrations_data()
    spreadsheet_id = (settings.google_sheets_spreadsheet_id or "").strip()
    return templates.TemplateResponse(
        request=request,
        name="integrations.html",
        context={
            "app_name": settings.app_name,
            "active_page": "integrations",
            "integrations": _build_integrations(integrations_data),
            "integrations_data": integrations_data,
            "masked_placeholder": MASKED_KEY,
            "google_spreadsheet_id": spreadsheet_id,
            "google_spreadsheet_id_masked": mask_spreadsheet_id(spreadsheet_id),
        },
    )


@router.post("/integrations/google_sheets/save", include_in_schema=False)
async def save_google_sheets(spreadsheet_id: str = Form("")):
    """Сохранить ID таблицы Google Sheets в `.env` и вернуться на страницу.

    Отдельный роут объявлен раньше универсального ``/{service_name}/save``,
    иначе ``google_sheets`` был бы принят за неизвестную интеграцию.
    Значение меняется и в ``.env``, и в уже загруженном ``settings``: после
    редиректа карточка сразу показывает новый ID без перезапуска сервиса.
    """
    value = (spreadsheet_id or "").strip()
    _update_env_value("GOOGLE_SHEETS_SPREADSHEET_ID", value)
    settings.google_sheets_spreadsheet_id = value
    return RedirectResponse(url="/integrations", status_code=303)


@router.post("/integrations/{service_name}/save", include_in_schema=False)
async def save_integration(
    service_name: str,
    api_key: str = Form(...),
):
    """Сохранить ключ в data/integrations.json и вернуться на страницу.

    ``api_key=********`` означает «оставить прежний ключ». Ответ — редирект
    303 (PRG): обычная HTML-форма попадает на обновлённую страницу, а fetch
    в шаблоне следует за редиректом и делает reload.
    """
    if service_name not in SERVICE_NAMES:
        raise HTTPException(status_code=404, detail="Неизвестная интеграция")

    key = (api_key or "").strip()
    if not key:
        raise HTTPException(status_code=400, detail="API ключ не может быть пустым")

    # Активная карточка отправляет `********`, если пользователь не менял
    # ключ. В этом случае старое значение остаётся в файле без изменений.
    if key != MASKED_KEY:
        set_key(service_name, key)

    return RedirectResponse(url="/integrations", status_code=303)


CATEGORY_LABELS: dict[str, str] = {
    "onboarding": "Онбординг",
    "offboarding": "Оффбординг",
    "delivery": "Доставка",
}


@router.get("/docs", include_in_schema=False)
async def docs_page(request: Request):
    """Страница документации продукта (не путать со Swagger API)."""
    return templates.TemplateResponse(
        request=request,
        name="docs.html",
        context={
            "app_name": settings.app_name,
            "active_page": "docs",
        },
    )


@router.get("/settings", include_in_schema=False)
async def settings_page(request: Request):
    """Страница личного кабинета и параметров системы."""
    return templates.TemplateResponse(
        request=request,
        name="settings.html",
        context={
            "app_name": settings.app_name,
            "active_page": "settings",
        },
    )


@router.get("/analytics", include_in_schema=False)
async def analytics_page(
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Аналитика: KPI, распределение по категориям, последние события."""

    # --- KPI: считаем напрямую из БД ---

    total_cases = (
        await session.scalar(select(func.count()).select_from(m.CaseORM))
    ) or 0

    total_messages = (
        await session.scalar(select(func.count()).select_from(m.MessageORM))
    ) or 0

    summary = await build_summary(session)
    priority_counts = summary.by_priority
    problematic_cases = (
        priority_counts.get("critical", 0)
        + priority_counts.get("high", 0)
        + priority_counts.get("medium", 0)
    )

    # Таблица issues в этом приложении расхождения правилами не наполняется
    # (детект идёт на лету). Запрос выполняем как просит ТЗ, но для KPI
    # берём максимум из двух источников, чтобы не показывать ложный ноль.
    issues_table_count = (
        await session.scalar(text("SELECT COUNT(*) FROM issues"))
    ) or 0
    total_divergences = max(summary.total_issues, issues_table_count)

    # --- Распределение по категориям ---

    category_rows = await session.execute(
        select(m.CaseORM.category, func.count())
        .group_by(m.CaseORM.category)
        .order_by(func.count().desc())
    )
    category_counts = {row[0]: row[1] for row in category_rows}

    categories: list[dict[str, object]] = []
    for code, label in CATEGORY_LABELS.items():
        count = category_counts.get(code, 0)
        percent = round(count / total_cases * 100) if total_cases else 0
        categories.append({"code": code, "label": label, "count": count, "percent": percent})

    # Любые другие категории, которые встретятся в БД
    for code, count in category_counts.items():
        if code in CATEGORY_LABELS:
            continue
        percent = round(count / total_cases * 100) if total_cases else 0
        categories.append({"code": code, "label": code, "count": count, "percent": percent})

    # --- Последние сообщения (чаты считаем источником "chat") ---

    recent_rows = await session.execute(
        select(m.MessageORM).order_by(m.MessageORM.created_at.desc().nullslast()).limit(5)
    )
    recent_events = []
    for msg in recent_rows.scalars().all():
        text_value = msg.text or ""
        if len(text_value) > 90:
            text_value = text_value[:90].rstrip() + "…"
        created = msg.created_at.strftime("%d.%m %H:%M") if msg.created_at else "—"
        recent_events.append({
            "author": msg.author or msg.role,
            "text": text_value,
            "source": "chat",
            "case_code": msg.case_code,
            "created_at": created,
        })

    return templates.TemplateResponse(
        request=request,
        name="analytics.html",
        context={
            "app_name": settings.app_name,
            "active_page": "analytics",
            "total_cases": total_cases,
            "problematic_cases": problematic_cases,
            "total_messages": total_messages,
            "total_divergences": total_divergences,
            "categories": categories,
            "recent_events": recent_events,
        },
    )