"""AI-агент Command Bar: интент LLM (GigaChat) + regex-fallback.

Активный провайдер выбирается через ``app.ai.get_provider()``: при
``AI_PROVIDER=gigachat`` это ``GigaChatClient``. LLM выбирает только действие
и код кейса. Все изменения БД выполняются через ``case_service`` по правилам
предметной области: CRM-статусы и приоритеты детерминированным кодом не
перезаписываются.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai import get_provider
from app.ai.base import AIProviderError
from app.database import models as m
from app.schemas.case import CaseUpdate
from app.services import case_service
from app.services.summary import list_cases

logger = logging.getLogger(__name__)

FALLBACK_ERROR_MESSAGE = (
    "AI временно недоступен, но я не понял команду. Попробуйте: 'Закрой S-24'"
)
AI_UNKNOWN_MESSAGE = (
    "Не понял команду. Попробуйте: 'Закрой S-24' или "
    "'Подтверди документы S-17'"
)
SUCCESS_MESSAGE = "Действие выполнено: {action} для кейса {case_code}"
NO_TIME_MESSAGE = (
    "Укажите время доставки в формате ЧЧ:MM, например: "
    "Курьер приедет в 14:00 для P-08"
)

# Системный промпт живёт здесь, в агенте: агент знает предметную область
# и доступные действия. Провайдер (GigaChat / OpenRouter) получает готовую
# строку и не содержит бизнес-логики.
COMMAND_SYSTEM_PROMPT = """Ты — AI-ассистент Support-отдела компании Руссмаркет. Твоя задача — помогать сотрудникам управлять кейсами оформления, увольнения и доставки карт.
У тебя есть список активных кейсов:
{cases_context}

Доступные действия:
1. confirm_docs — подтвердить документы (для onboarding).
2. confirm_offboarding — подтвердить увольнение (для offboarding).
3. update_delivery — обновить время доставки (для delivery, требует параметр time).
4. close_case — закрыть кейс (для любого).
5. create_case — СОЗДАТЬ НОВУЮ задачу/кейс, если пользователь просит «создай задачу», «новый кейс», «оформи нового сотрудника» и т.п. Код кейса НЕ указывай — он генерируется системой.

Категории новых задач: onboarding (оформление), offboarding (увольнение), delivery (доставка карт).

Если пользователь просит выполнить действие, верни СТРОГО JSON:
{"action": "название_действия", "case_code": "код_кейса", "params": {"time": "время"}}
Пример: {"action": "close_case", "case_code": "S-24"}
Пример создания: {"action": "create_case", "params": {"category": "onboarding", "title": "Оформление сотрудника Елена Аверина", "employee": "Елена Аверина", "assignee": "HR manager"}}

Для create_case обязательно заполни params.category и params.title, а также params.employee и params.assignee, если они названы в запросе.

Если пользователь здоровается или задает вопрос — ответь обычным текстом (не JSON).
Если не понял команду — верни {"action": "unknown"}."""

CASE_CODE_RE = re.compile(r"\b([A-Za-z]+-\d+)\b")
TIME_RE = re.compile(r"\b([01]?\d|2[0-3]):([0-5]\d)\b")
TIME_FULL_RE = re.compile(r"^(?:[01]?\d|2[0-3]):[0-5]\d$")
EMPLOYEE_RE = re.compile(
    r"(?:сотрудник(?:а|у|и)?|employee)\s+"
    r"([А-ЯЁA-Z][А-Яа-яЁёA-Za-z-]+(?:\s+[А-ЯЁA-Z][А-Яа-яЁёA-Za-z-]+){0,2})",
    re.IGNORECASE,
)
ASSIGNEE_RE = re.compile(r",\s*([^,]+?)\s*$")

VALID_ACTIONS = {
    "confirm_docs",
    "confirm_offboarding",
    "update_delivery",
    "close_case",
    "create_case",
}
ACTION_CATEGORIES: dict[str, str | None] = {
    "confirm_docs": "onboarding",
    "confirm_offboarding": "offboarding",
    "update_delivery": "delivery",
    "close_case": None,
    "create_case": None,
}
SERVICE_ACTIONS: dict[str, str] = {
    "confirm_docs": "confirm_docs",
    "confirm_offboarding": "confirm_offboarding",
    "update_delivery": "update_delivery",
    "close_case": "close",
    "create_case": "create_case",
}

CATEGORY_WORDS: dict[str, tuple[str, ...]] = {
    "onboarding": ("оформлен", "оформить", "онбординг", "найм", "прием", "onboard", "hire"),
    "offboarding": ("увольн", "увол", "оффбординг", "offboard", "dismiss", "fire"),
    "delivery": ("доставк", "достав", "deliver", "delivery"),
}
TITLE_LABELS: dict[str, str] = {
    "onboarding": "Оформление сотрудника",
    "offboarding": "Увольнение сотрудника",
    "delivery": "Доставка карты",
}
TEXT_KEYS = ("message", "text", "response", "answer", "content")

NAME_ALIASES = {"olga": "ольга", "ivan": "иван"}
NAME_STOP_WORDS = {
    "уволить", "увольнение", "завершить", "закончить", "complete", "finish",
    "fire", "dismiss", "terminate", "offboard",
    "оформить", "оформление", "onboard", "onboarding", "hire", "register",
    "подтвердить", "подтверди", "документ", "документы", "document", "documents",
    "confirm", "approve", "доставка", "доставить", "задержится", "задержка",
    "delivery", "deliver", "delay", "delayed", "courier", "курьер", "приедет",
    "arrival", "закрыть", "закрой", "close", "closed", "resolve", "resolved",
    "кейс", "case", "для", "for", "the",
}


class AIAgent:
    """Принимает текст, запрашивает интент у LLM и применяет действие."""

    def __init__(self, provider: Any | None = None) -> None:
        # AI_PROVIDER=gigachat → GigaChatClient (см. app/ai/__init__.py).
        # Переданный provider используется в тестах и для принудительного режима.
        self.provider = provider or get_provider()

    async def process_command(self, user_text: str, db_session: AsyncSession) -> dict:
        """Основной вход Command Bar: вернуть ``status`` и ``message``."""
        user_text = (user_text or "").strip()
        if not user_text:
            return {"status": "error", "message": FALLBACK_ERROR_MESSAGE}

        context = await self._build_context(db_session)
        # Заменяем плейсхолдер через replace, а не format: промпт содержит
        # литеральные фигурные скобки примеров JSON.
        system_prompt = COMMAND_SYSTEM_PROMPT.replace(
            "{cases_context}", json.dumps(context, ensure_ascii=False)
        )

        try:
            intent = await self.provider.generate_command_intent(
                user_text, context, system_prompt=system_prompt
            )
        except AIProviderError as exc:
            logger.warning("AI недоступен, включается regex-fallback: %s", exc)
            return await self._process_fallback(user_text, db_session)
        except Exception as exc:  # сетевые/парсинг-ошибки провайдера
            logger.warning("Ошибка AI-вызова, включается regex-fallback: %s", exc)
            return await self._process_fallback(user_text, db_session)

        if isinstance(intent, str):
            return {"status": "ok", "message": intent}
        if not isinstance(intent, dict):
            return {"status": "ok", "message": str(intent)}

        raw_action = intent.get("action")
        if isinstance(raw_action, str) and raw_action.strip().lower() == "unknown":
            return {"status": "error", "message": AI_UNKNOWN_MESSAGE}

        action = self._normalize_action(raw_action)
        if action is None:
            text = self._extract_text(intent)
            if text:
                return {"status": "ok", "message": text}
            return {"status": "error", "message": AI_UNKNOWN_MESSAGE}

        return await self._execute_action(action, intent, user_text, db_session)

    async def _build_context(self, db_session: AsyncSession) -> list[dict]:
        cases = await list_cases(db_session)
        return [
            {
                "code": case.code,
                "title": case.title,
                "category": case.category,
                "status": case.status,
            }
            for case in cases
        ]

    def _normalize_action(self, value: Any) -> str | None:
        if not isinstance(value, str):
            return None
        action = value.strip().lower()
        if action in {"", "unknown", "none", "null"}:
            return None
        # LLM иногда присылает синоним вместо контрактного имени действия.
        action = {
            "create_task": "create_case",
            "new_case": "create_case",
            "add_case": "create_case",
        }.get(action, action)
        return action if action in VALID_ACTIONS else None

    def _extract_text(self, intent: dict) -> str | None:
        for key in TEXT_KEYS:
            value = intent.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return None

    @staticmethod
    def _extract_param(params: dict, keys: tuple[str, ...]) -> str | None:
        for key in keys:
            value = params.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return None

    def _normalize_category(self, value: Any, user_text: str = "") -> str | None:
        """Привести категорию из LLM/пользователя к схеме БД."""
        candidates = []
        if isinstance(value, str):
            candidates.append(value.strip().lower())
        if user_text:
            candidates.append(user_text.lower())
        for candidate in candidates:
            if candidate in CATEGORY_WORDS:
                return candidate
        for candidate in candidates:
            for category, needles in CATEGORY_WORDS.items():
                if any(needle in candidate for needle in needles):
                    return category
        return None

    @staticmethod
    def _extract_employee(user_text: str) -> str | None:
        match = EMPLOYEE_RE.search(user_text)
        if not match:
            return None
        name = match.group(1).strip(" ,.;:")
        # Не резюме/роль: это имя, если есть фамилия или кириллица.
        return name or None

    @staticmethod
    def _extract_assignee(user_text: str) -> str | None:
        match = ASSIGNEE_RE.search(user_text)
        if not match:
            return None
        value = match.group(1).strip(" ,.;:")[:64]
        return value or None

    async def _execute_create_case(
        self, intent: dict, user_text: str, db_session: AsyncSession
    ) -> dict:
        """Создать новую задачу через case_service и вернуть ответ Command Bar."""
        params = intent.get("params")
        if not isinstance(params, dict):
            params = {}

        category = self._normalize_category(params.get("category"), user_text)
        if category is None:
            return {"status": "error", "message": AI_UNKNOWN_MESSAGE}

        employee = self._extract_param(
            params, ("employee", "full_name", "fio", "employee_name", "name")
        ) or self._extract_employee(user_text)
        assignee = self._extract_param(
            params, ("assignee", "owner", "responsible", "role")
        ) or self._extract_assignee(user_text)
        title = self._extract_param(
            params, ("title", "task_title", "subject")
        )
        if not title:
            title = TITLE_LABELS[category]
            if employee:
                title = f"{title} {employee}"

        try:
            case = await case_service.create_case(
                db_session,
                category=category,
                title=title,
                employee=employee,
                assignee=assignee,
            )
        except ValueError as exc:
            logger.warning("Не удалось создать кейс: %s", exc)
            return {"status": "error", "message": AI_UNKNOWN_MESSAGE}

        return {
            "status": "ok",
            "message": SUCCESS_MESSAGE.format(
                action="create_case", case_code=case.code
            ),
            "action": "create_case",
            "case_code": case.code,
        }

    async def _execute_action(
        self,
        action: str,
        intent: dict,
        user_text: str,
        db_session: AsyncSession,
    ) -> dict:
        # У create_case нет существующего кода кейса — создаём новую запись.
        if action == "create_case":
            return await self._execute_create_case(intent, user_text, db_session)

        params = intent.get("params")
        if not isinstance(params, dict):
            params = {}

        parsed = {
            "action": action,
            "service_action": SERVICE_ACTIONS[action],
            "code": self._normalize_code(intent.get("case_code")),
            "delivery_time": self._extract_time(params, user_text),
            "name_tokens": self._name_tokens(user_text),
        }

        case = await self._find_case(
            db_session,
            parsed["code"],
            ACTION_CATEGORIES[action],
            parsed["name_tokens"],
        )
        if case is None:
            return {"status": "error", "message": AI_UNKNOWN_MESSAGE}

        result = await self._apply(db_session, case, parsed)
        if not result.get("ok"):
            return {
                "status": "error",
                "message": result.get("error") or AI_UNKNOWN_MESSAGE,
            }

        return {
            "status": "ok",
            "message": SUCCESS_MESSAGE.format(action=action, case_code=case.code),
            "action": action,
        }

    def _normalize_code(self, value: Any) -> str | None:
        if not isinstance(value, str):
            return None
        match = CASE_CODE_RE.search(value.upper())
        return match.group(1) if match else None

    def _extract_time(self, params: dict, user_text: str) -> str | None:
        for key in ("time", "delivery_time", "deliveryTime", "eta"):
            value = params.get(key)
            if value is None:
                continue
            match = TIME_RE.search(str(value))
            if match:
                return match.group(0)

        match = TIME_RE.search(user_text)
        return match.group(0) if match else None

    def _name_tokens(self, user_text: str) -> list[str]:
        cleaned = user_text
        code_match = CASE_CODE_RE.search(user_text)
        time_match = TIME_RE.search(user_text)
        if code_match:
            cleaned = cleaned.replace(code_match.group(0), " ")
        if time_match:
            cleaned = cleaned.replace(time_match.group(0), " ")

        return [
            token.lower()
            for token in re.findall(r"[A-Za-zА-Яа-яЁё]{3,}", cleaned)
            if token.lower() not in NAME_STOP_WORDS
        ]

    async def _find_case(
        self,
        db_session: AsyncSession,
        code: str | None,
        category: str | None,
        name_tokens: list[str],
    ) -> m.CaseORM | None:
        if code is not None:
            case = await case_service.get_case(db_session, code)
            if case is None:
                return None
            if category is not None and case.category != category:
                return None
            return case

        stmt = select(m.CaseORM)
        if category is not None:
            stmt = stmt.where(m.CaseORM.category == category)
        cases = (await db_session.execute(stmt)).scalars().all()

        if name_tokens:
            variants = [
                {token, NAME_ALIASES.get(token, token)} for token in name_tokens
            ]
            author_case_ids: set[int] = set()
            authors = (
                await db_session.execute(
                    select(m.MessageORM.case_id, m.MessageORM.author)
                )
            ).all()
            for case_id, author in authors:
                author_text = (author or "").lower()
                if any(any(v in author_text for v in item) for item in variants):
                    author_case_ids.add(case_id)

            for case in cases:
                haystack = f"{case.code} {case.title}".lower()
                if case.id in author_case_ids or any(
                    any(v in haystack for v in item) for item in variants
                ):
                    return case
            return None

        for case in cases:
            if case.status != "resolved":
                return case
        return cases[0] if cases else None

    async def _apply(
        self,
        db_session: AsyncSession,
        case: m.CaseORM,
        parsed: dict,
    ) -> dict:
        service_action = parsed["service_action"]

        if service_action in {"confirm_docs", "confirm_offboarding"}:
            result = await case_service.apply_action(
                db_session, case.code, service_action
            )
            if not result.get("ok"):
                return result
            # Подтверждение приравнивается к завершению работы по кейсу.
            await case_service.update_case(
                db_session, case, CaseUpdate(task_status="done")
            )
            return result

        if service_action == "update_delivery":
            time_value = parsed.get("delivery_time")
            if not time_value or not TIME_FULL_RE.fullmatch(time_value):
                return {"found": True, "ok": False, "error": NO_TIME_MESSAGE}
            return await case_service.apply_action(
                db_session, case.code, service_action, time_value
            )

        if service_action == "delay_delivery":
            await case_service.update_case(
                db_session,
                case,
                CaseUpdate(
                    task_status="waiting",
                    resolution="Доставка задержана, новое время не указано",
                ),
            )
            case.delivery_delay_reported = True
            await db_session.commit()
            await db_session.refresh(case)
            return {"found": True, "ok": True, "code": case.code}

        return await case_service.apply_action(db_session, case.code, "close")

    async def _process_fallback(
        self, user_text: str, db_session: AsyncSession
    ) -> dict:
        """Детерминированный парсер на случай недоступности GigaChat/LLM."""
        parsed = self._parse_regex(user_text)
        if parsed is None:
            return {"status": "error", "message": FALLBACK_ERROR_MESSAGE}

        if parsed["action"] == "create_case":
            return await self._execute_create_case(parsed, user_text, db_session)

        case = await self._find_case(
            db_session,
            parsed["code"],
            parsed["category"],
            parsed["name_tokens"],
        )
        if case is None:
            return {"status": "error", "message": FALLBACK_ERROR_MESSAGE}

        result = await self._apply(db_session, case, parsed)
        if not result.get("ok"):
            return {
                "status": "error",
                "message": result.get("error") or FALLBACK_ERROR_MESSAGE,
            }

        return {
            "status": "ok",
            "message": SUCCESS_MESSAGE.format(
                action=parsed["action"], case_code=case.code
            ),
            "action": parsed["action"],
        }

    def _parse_regex(self, user_text: str) -> dict | None:
        text = user_text.lower().strip()
        code_match = CASE_CODE_RE.search(user_text)
        time_match = TIME_RE.search(user_text)
        code = code_match.group(1).upper() if code_match else None
        delivery_time = time_match.group(0) if time_match else None

        def has(*needles: str) -> bool:
            return any(needle in text for needle in needles)

        if has(
            "созда", "создать", "новая задача", "новую задачу",
            "новый кейс", "create", "добавь задачу",
        ):
            # Создание не ищет существующий кейс — сразу уходит в case_service.
            category = self._normalize_category(None, user_text) or "onboarding"
            employee = self._extract_employee(user_text)
            assignee = self._extract_assignee(user_text)
            title = TITLE_LABELS[category]
            if employee:
                title = f"{title} {employee}"
            return {
                "action": "create_case",
                "params": {
                    "category": category,
                    "title": title,
                    "employee": employee,
                    "assignee": assignee,
                },
            }

        if has("увол", "увольн", "fire", "dismiss", "terminate", "offboard"):
            parsed = {
                "action": "confirm_offboarding",
                "service_action": "confirm_offboarding",
                "category": "offboarding",
            }
        elif has("оформ", "onboard", "hire", "register") or (
            has("подтверд", "confirm", "approve") and has("документ", "document")
        ):
            parsed = {
                "action": "confirm_docs",
                "service_action": "confirm_docs",
                "category": "onboarding",
            }
        elif has("достав", "deliver", "задерж", "delay", "курьер", "courier", "приед", "arriv"):
            parsed = {
                "action": "update_delivery",
                "service_action": (
                    "update_delivery" if delivery_time else "delay_delivery"
                ),
                "category": "delivery",
            }
        elif has("закр", "close", "resolve"):
            parsed = {
                "action": "close_case",
                "service_action": "close",
                "category": None,
            }
        else:
            return None

        parsed.update(
            code=code,
            delivery_time=delivery_time,
            name_tokens=self._name_tokens(user_text),
        )
        return parsed
