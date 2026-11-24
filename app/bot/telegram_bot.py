"""Telegram-бот RusSupport AI на aiogram 3.x.

Запуск:
    python -m app.bot.telegram_bot

Токен читается из `.env` (TELEGRAM_BOT_TOKEN) через app.config.settings.
Если токен не задан, модуль импортируется без ошибок, а при запуске
выводится предупреждение и бот завершается.

Основной интерфейс — inline-кнопки и FSM:
    /start                    — приветствие и главное меню
    callback "summary"        — сводка как в /summary
    callback "manage_cases"   — список кейсов кнопками
    callback "case_<code>"    — карточка кейса
    callback "create_task"    — FSM создания кейса
    callback "edit_<code>"    — выбор поля для редактирования
    callback "action_<code>"  — выбор действия над кейсом
    callback "delete_<code>"  — подтверждение удаления

Резервные текстовые команды оставлены рабочими:
    /summary, /onboarding, /offboarding, /delivery, /critical, /case <code>
    /confirm_docs, /confirm_offboarding, /update_delivery, /close_case

Все изменения данных дополнительно синхронизируют Google Sheets, если
GOOGLE_SHEETS_ENABLED=True.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from html import escape

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.filters import Command, or_f
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import models as m
from app.database.session import AsyncSessionLocal, init_db
from app.schemas.case import CaseRead, CaseUpdate
from app.services.case_service import (
    apply_action,
    create_case,
    delete_case,
    get_case,
    update_case,
)
from app.services.google_sheets import GoogleSheetsService
from app.services.summary import build_summary, list_cases, to_case_read

logger = logging.getLogger(__name__)

router = Router()
MESSAGE_LIMIT = 3900  # запас до лимита Telegram в 4096 символов

CATEGORY_TITLES: dict[str, str] = {
    "onboarding": "Оформление (onboarding)",
    "offboarding": "Увольнение (offboarding)",
    "delivery": "Доставка (delivery)",
}
CATEGORY_SHORT: dict[str, str] = {
    "onboarding": "Оформление",
    "offboarding": "Увольнение",
    "delivery": "Доставка",
}
DELIVERY_STATUS_LABELS: dict[str, str] = {
    "pending": "ожидает отправки",
    "in_transit": "в пути",
    "accepted": "принято",
    "completed": "завершено",
}
TASK_STATUS_LABELS: dict[str, str] = {
    "open": "Открыт",
    "in_progress": "В работе",
    "waiting": "Ожидает",
    "done": "Завершён",
}

CONFIRMATION_ISSUE_CODE = "OFFBOARDING_OFFICIAL_CONFIRMATION_MISSING"
DEADLINE_ISSUE_CODE = "DELIVERY_DEADLINE_RISK"
DOCS_ISSUE_CODE = "ONBOARDING_DOCS_UNCONFIRMED"

NO_CASES_MESSAGE = "Нет активных кейсов в этой категории."
NO_CRITICAL_MESSAGE = "Нет критических кейсов."
DIVIDER = "---"

# Старые кнопки под полем ввода оставляем для совместимости с уже
# задеплоенными версиями бота. Новый /start использует inline-меню.
BUTTON_SUMMARY = "📊 Сводка"
BUTTON_ONBOARDING = "📋 Оформление"
BUTTON_OFFBOARDING = "🚪 Увольнение"
BUTTON_DELIVERY = "🚚 Доставка"
BUTTON_CRITICAL = "⚠️ Критические"

CB_SUMMARY = "summary"
CB_CREATE = "create_task"
CB_MANAGE = "manage_cases"
CB_BACK_LIST = "back_to_list"
CB_BACK_MENU = "back_to_menu"
CB_CANCEL = "cancel"

FIELD_LABELS: dict[str, str] = {
    "title": "Название",
    "crm_status": "Статус CRM",
    "assignee": "Ответственный",
    "resolution": "Решение",
}
FIELD_TOKENS: dict[str, str] = {
    "title": "title",
    "crm": "crm_status",
    "assignee": "assignee",
    "resolution": "resolution",
}
DO_ACTION_TOKENS: dict[str, str] = {
    "docs": "confirm_docs",
    "offboarding": "confirm_offboarding",
    "delivery": "update_delivery",
    "close": "close",
}

CATEGORY_ALIASES: dict[str, str] = {
    "onboarding": "onboarding",
    "оформление": "onboarding",
    "оформление сотрудников": "onboarding",
    "offboarding": "offboarding",
    "увольнение": "offboarding",
    "увольнения": "offboarding",
    "delivery": "delivery",
    "доставка": "delivery",
    "доставка карт": "delivery",
}

_DELIVERY_TIME_RE = re.compile(r"^(?:[01]?\d|2[0-3]):[0-5]\d$")
_CLEAR_VALUES = frozenset({"-", "—", "нет", "пропустить", "очистить"})


# ---------------------------------------------------------------------------
# FSM
# ---------------------------------------------------------------------------

class CreateCase(StatesGroup):
    """Создание кейса: категория | название | сотрудник | ответственный."""

    waiting_data = State()


class EditCase(StatesGroup):
    """Редактирование одного поля карточки."""

    waiting_value = State()


class DeliveryTime(StatesGroup):
    """Ввод времени доставки ЧЧ:MM."""

    waiting_time = State()


# ---------------------------------------------------------------------------
# Клавиатуры
# ---------------------------------------------------------------------------

def main_keyboard() -> ReplyKeyboardMarkup:
    """Резервная постоянная клавиатура для текстовых команд."""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BUTTON_SUMMARY), KeyboardButton(text=BUTTON_ONBOARDING)],
            [KeyboardButton(text=BUTTON_OFFBOARDING), KeyboardButton(text=BUTTON_DELIVERY)],
            [KeyboardButton(text=BUTTON_CRITICAL)],
        ],
        resize_keyboard=True,
        persistent=True,
    )


def main_inline_keyboard() -> InlineKeyboardMarkup:
    """Главное меню из шага 1 ТЗ."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📊 Сводка", callback_data=CB_SUMMARY)],
            [InlineKeyboardButton(text="➕ Создать задачу", callback_data=CB_CREATE)],
            [InlineKeyboardButton(text="📝 Управление кейсами", callback_data=CB_MANAGE)],
        ]
    )


def cancel_keyboard() -> InlineKeyboardMarkup:
    """Кнопка отмены для любого FSM-сценария."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="❌ Отмена", callback_data=CB_CANCEL)]
        ]
    )


def cases_inline_keyboard(cases: list[CaseRead]) -> InlineKeyboardMarkup:
    """Список кейсов в виде inline-кнопок."""
    rows = [
        [
            InlineKeyboardButton(
                text=f"{case.code}: {CATEGORY_SHORT.get(case.category, case.category)}",
                callback_data=f"case_{case.code}",
            )
        ]
        for case in cases
    ]
    rows.append([InlineKeyboardButton(text="🔙 Главное меню", callback_data=CB_BACK_MENU)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def case_card_keyboard(code: str) -> InlineKeyboardMarkup:
    """Кнопки карточки кейса из шага 2 ТЗ."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✏️ Редактировать", callback_data=f"edit_{code}")],
            [InlineKeyboardButton(text="✅ Выполнить действие", callback_data=f"action_{code}")],
            [InlineKeyboardButton(text="🗑 Удалить", callback_data=f"delete_{code}")],
            [InlineKeyboardButton(text="🔙 Назад", callback_data=CB_BACK_LIST)],
        ]
    )


def edit_fields_keyboard(code: str) -> InlineKeyboardMarkup:
    """Выбор поля для редактирования."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=FIELD_LABELS["title"], callback_data=f"field_title_{code}")],
            [InlineKeyboardButton(text=FIELD_LABELS["crm_status"], callback_data=f"field_crm_{code}")],
            [InlineKeyboardButton(text=FIELD_LABELS["assignee"], callback_data=f"field_assignee_{code}")],
            [InlineKeyboardButton(text=FIELD_LABELS["resolution"], callback_data=f"field_resolution_{code}")],
            [InlineKeyboardButton(text="🔙 Назад", callback_data=f"case_{code}")],
        ]
    )


def action_choices_keyboard(code: str) -> InlineKeyboardMarkup:
    """Выбор действия над кейсом."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Подтвердить документы", callback_data=f"do_docs_{code}")],
            [InlineKeyboardButton(text="Подтвердить увольнение", callback_data=f"do_offboarding_{code}")],
            [InlineKeyboardButton(text="Обновить доставку", callback_data=f"do_delivery_{code}")],
            [InlineKeyboardButton(text="Закрыть", callback_data=f"do_close_{code}")],
            [InlineKeyboardButton(text="🔙 Назад", callback_data=f"case_{code}")],
        ]
    )


def delete_confirm_keyboard(code: str) -> InlineKeyboardMarkup:
    """Подтверждение удаления."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Да, удалить", callback_data=f"confirm_delete_{code}"),
                InlineKeyboardButton(text="🔙 Назад", callback_data=f"case_{code}"),
            ]
        ]
    )


# ---------------------------------------------------------------------------
# Отправка и разбиение текста
# ---------------------------------------------------------------------------

async def respond(
    message: Message,
    text: str,
    reply_markup: InlineKeyboardMarkup | ReplyKeyboardMarkup | None = None,
) -> None:
    """Отправить ответ порциями. По умолчанию сохраняем старые кнопки."""
    markup = reply_markup if reply_markup is not None else main_keyboard()
    for part in split_text(text):
        await message.answer(part, reply_markup=markup)


async def send_to_callback(
    callback: CallbackQuery,
    text: str,
    markup: InlineKeyboardMarkup | None = None,
) -> None:
    """Ответить на callback сообщением с inline-клавиатурой."""
    message = callback.message
    if isinstance(message, Message):
        await respond(message, text, markup or main_inline_keyboard())


def split_text(text: str, limit: int = MESSAGE_LIMIT) -> list[str]:
    """Разбить длинный ответ по строкам, не обрывая фразы."""
    parts: list[str] = []
    current: list[str] = []
    size = 0
    for line in text.splitlines():
        line_size = len(line) + 1
        if size + line_size > limit and current:
            parts.append("\n".join(current))
            current, size = [], 0
        current.append(line)
        size += line_size
    if current:
        parts.append("\n".join(current))
    return parts or [""]


# ---------------------------------------------------------------------------
# Вспомогательное форматирование и разбор ввода
# ---------------------------------------------------------------------------

def _esc(value: object) -> str:
    """Безопасное значение для HTML-режима Telegram."""
    if value is None:
        return "не указано"
    return escape(str(value))


def _issue_codes(case_read: CaseRead) -> set[str]:
    return {issue.code for issue in case_read.issues}


def _problem_text(case_read: CaseRead) -> str:
    if not case_read.issues:
        return "✅ не выявлена"
    return "⚠️ " + ", ".join(_esc(issue.title) for issue in case_read.issues)


def _critical_reason(case_read: CaseRead) -> str:
    reasons = [issue for issue in case_read.issues if issue.severity == "critical"]
    if not reasons:
        reasons = list(case_read.issues)
    return ", ".join(_esc(issue.title) for issue in reasons) or "причина не указана"


def _crm_facts(case_read: CaseRead, case: m.CaseORM) -> str:
    """Что официально подтверждено в CRM."""
    facts = [
        f"CRM-статус: {_esc(case_read.crm_status)}",
        f"Статус кейса: {_esc(case_read.status)}",
    ]

    if case_read.category == "onboarding":
        documents = "подтверждены" if case.documents_confirmed else "ожидают подтверждения"
        facts.append(f"Документы: {documents}")
    elif case_read.category == "offboarding":
        confirmation = (
            "получено" if case.official_confirmation_provided else "отсутствует"
        )
        facts.append(f"Подтверждение руководителя: {confirmation}")
    elif case_read.category == "delivery":
        status = DELIVERY_STATUS_LABELS.get(
            case.delivery_status or "", case.delivery_status or "не указан"
        )
        facts.append(f"Доставка: {_esc(status)}")
        if case.delivery_eta:
            facts.append(f"Ожидаемое прибытие: {case.delivery_eta.isoformat()}")

    return "; ".join(facts)


def _chat_facts(case: m.CaseORM) -> str:
    """Что известно из переписки. Чат не является официальным подтверждением."""
    chat_messages = [
        msg for msg in case.messages if (msg.role or "").lower() != "support"
    ]
    if not chat_messages:
        return "сообщений нет"

    pieces = []
    for msg in chat_messages[-2:]:
        author = _esc(msg.author or msg.role or "участник")
        text = _esc(msg.text)
        if len(text) > 140:
            text = text[:140] + "…"
        pieces.append(f"{author}: {text}")
    return "; ".join(pieces)


def _source_block(case_read: CaseRead, case: m.CaseORM) -> list[str]:
    """Разделение «подтверждено в CRM» и «известно из чата»."""
    return [
        f"  Подтверждено в CRM: {_crm_facts(case_read, case)}",
        f"  Известно из чата: {_chat_facts(case)}",
    ]


def _optional_value(raw: str) -> str | None:
    """Пустой ввод и '-' означают «очистить поле»."""
    value = (raw or "").strip()
    if not value or value.lower() in _CLEAR_VALUES:
        return None
    return value


def normalize_category(raw: str) -> str | None:
    """Привести категорию из ввода пользователя к внутреннему значению."""
    key = (raw or "").strip().lower()
    return CATEGORY_ALIASES.get(key)


def parse_create_input(raw: str) -> dict[str, str | None]:
    """Разобрать строку «Категория | Название | Сотрудник | Ответственный»."""
    parts = [part.strip() for part in (raw or "").split("|")]
    if len(parts) != 4:
        raise ValueError("нужно ровно 4 поля через символ '|'")

    category = normalize_category(parts[0])
    if category is None:
        raise ValueError(
            "категория должна быть onboarding/оформление, "
            "offboarding/увольнение или delivery/доставка"
        )

    title = parts[1]
    if not title:
        raise ValueError("название задачи не может быть пустым")
    if len(title) > 200:
        raise ValueError("название задачи длиннее 200 символов")

    return {
        "category": category,
        "title": title,
        "employee": _optional_value(parts[2]),
        "assignee": _optional_value(parts[3]),
    }


def build_case_update(field: str, raw: str) -> CaseUpdate:
    """Построить CaseUpdate для выбранного поля."""
    if field == "title":
        value = (raw or "").strip()
        if not value:
            raise ValueError("название не может быть пустым")
        return CaseUpdate(title=value[:200])
    if field == "crm_status":
        value = _optional_value(raw)
        return CaseUpdate(crm_status=value[:64] if value else None)
    if field == "assignee":
        value = _optional_value(raw)
        return CaseUpdate(assignee=value[:64] if value else None)
    if field == "resolution":
        value = _optional_value(raw)
        return CaseUpdate(resolution=value[:2000] if value else None)
    raise ValueError("неизвестное поле для редактирования")


def validate_delivery_time(raw: str) -> bool:
    """Проверить формат ЧЧ:MM."""
    return bool(_DELIVERY_TIME_RE.fullmatch((raw or "").strip()))


def start_text() -> str:
    """Приветствие /start с акцентом на кнопочное управление."""
    return (
        "<b>RusSupport AI</b>\n"
        f"{DIVIDER}\n"
        "Здравствуйте! Я AI-ассистент Support-отдела Руссмаркет. "
        "Помогаю следить за оформлением, увольнением и доставкой карт. "
        "Используйте кнопки ниже или введите команду.\n\n"
        "<b>Управление кейсами теперь через кнопки</b>\n"
        "• Сводка — KPI, процессы, CRM/чат и критические кейсы.\n"
        "• Создать задачу — мастер создания в 4 шага.\n"
        "• Управление кейсами — список, карточка, редактирование, "
        "действия и удаление.\n\n"
        "Текстовые команды (/summary, /case, /confirm_docs и другие) "
        "остаются доступными."
    )


# ---------------------------------------------------------------------------
# Отчёты
# ---------------------------------------------------------------------------

def format_summary(
    summary,
    cases: list[CaseRead],
    raw_by_code: dict[str, m.CaseORM],
) -> str:
    """Текст /summary: KPI, процессы, CRM/чат и критические кейсы."""
    critical = [case for case in cases if case.priority == "critical"]
    by_category = {category: 0 for category in CATEGORY_TITLES}
    for case in cases:
        if case.category in by_category:
            by_category[case.category] += 1

    lines = [
        "<b>RusSupport AI — общая сводка</b>",
        DIVIDER,
        f"Всего кейсов: <b>{summary.total_cases}</b>",
        f"Проблемных: <b>{summary.problematic_cases}</b>",
        f"Решённых: <b>{summary.resolved_cases}</b>",
        f"Критических: <b>{len(critical)}</b>",
        f"Выявлено инцидентов: <b>{summary.total_issues}</b>",
        "",
        "<b>Разделение по процессам</b>",
    ]
    for category, title in CATEGORY_TITLES.items():
        lines.append(f"• {_esc(title)}: <b>{by_category[category]}</b>")

    lines += ["", "<b>Критические кейсы</b>", DIVIDER]
    if critical:
        for case in critical:
            lines += [
                f"🚨 <b>{_esc(case.code)}</b> — {_esc(case.title)}",
                f"  Процесс: {_esc(CATEGORY_TITLES.get(case.category, case.category))}",
                f"  Причина: {_critical_reason(case)}",
            ]
    else:
        lines.append("🚨 Критических кейсов нет.")

    lines += ["", "<b>Подтверждения: CRM и чат</b>", DIVIDER]
    divergent = [case for case in cases if case.issues]
    if divergent:
        for case in divergent:
            raw = raw_by_code.get(case.code)
            lines.append(f"⚠️ <b>{_esc(case.code)}</b> — {_esc(case.title)}")
            if raw is not None:
                lines += _source_block(case, raw)
            else:
                lines.append(f"  Проблема: {_critical_reason(case)}")
    else:
        lines.append("✅ Расхождений между CRM и чатом не выявлено.")

    return "\n".join(lines)


def format_onboarding(rows: list[tuple[CaseRead, m.CaseORM]]) -> str:
    """Отчёт по оформлению: документы и расхождения."""
    lines = [f"<b>{CATEGORY_TITLES['onboarding']}</b>", DIVIDER]
    for case_read, case in rows:
        lines += [
            f"• <b>{_esc(case_read.code)}</b> — {_esc(case_read.title)}",
            *_source_block(case_read, case),
            f"  Проблема: {_problem_text(case_read)}",
            "",
        ]
    return "\n".join(lines).rstrip()


def format_offboarding(rows: list[tuple[CaseRead, m.CaseORM]]) -> str:
    """Увольнения: отдельно выделяем отсутствие подтверждения руководителя."""
    lines = [f"<b>{CATEGORY_TITLES['offboarding']}</b>", DIVIDER]
    for case_read, case in rows:
        missing = CONFIRMATION_ISSUE_CODE in _issue_codes(case_read)
        accent = "🚨 " if missing and case_read.priority == "critical" else ""
        confirmation = (
            "⚠️ отсутствует" if not case.official_confirmation_provided else "✅ получено"
        )
        lines += [
            f"• {accent}<b>{_esc(case_read.code)}</b> — {_esc(case_read.title)}",
            f"  Подтверждение руководителя: {confirmation}",
            *_source_block(case_read, case),
            f"  Проблема: {_problem_text(case_read)}",
            "",
        ]
    return "\n".join(lines).rstrip()


def format_delivery(rows: list[tuple[CaseRead, m.CaseORM]]) -> str:
    """Доставка карт: статус и риск нарушения срока."""
    lines = [f"<b>{CATEGORY_TITLES['delivery']}</b>", DIVIDER]
    for case_read, case in rows:
        raw_status = str(case.delivery_status or "")
        status_label = DELIVERY_STATUS_LABELS.get(raw_status, raw_status or "не указан")
        risk = DEADLINE_ISSUE_CODE in _issue_codes(case_read)
        lines += [
            f"• <b>{_esc(case_read.code)}</b> — {_esc(case_read.title)}",
            f"  Статус доставки: {_esc(status_label)}",
            f"  Риск задержки: {'🚨 есть' if risk else '✅ нет'}",
            *_source_block(case_read, case),
            f"  Проблема: {_problem_text(case_read)}",
            "",
        ]
    return "\n".join(lines).rstrip()


def format_critical(cases: list[CaseRead]) -> str:
    """Список критических кейсов с указанием причины."""
    critical_cases = [case for case in cases if case.priority == "critical"]
    if not critical_cases:
        return NO_CRITICAL_MESSAGE

    lines = ["<b>Критические кейсы</b>", DIVIDER]
    for case in critical_cases:
        lines += [
            f"🚨 <b>{_esc(case.code)}</b> — {_esc(case.title)}",
            f"  Процесс: {_esc(CATEGORY_TITLES.get(case.category, case.category))}",
            f"  Причина: {_critical_reason(case)}",
            "",
        ]
    return "\n".join(lines).rstrip()


def format_case(case_read: CaseRead, messages) -> str:  # type: ignore[no-untyped-def]
    """Детали кейса: официальный CRM-статус, чат и расхождения."""
    lines = [
        f"<b>{_esc(case_read.code)} — {_esc(case_read.title)}</b>",
        DIVIDER,
        f"Категория: {_esc(CATEGORY_TITLES.get(case_read.category, case_read.category))}",
        f"Приоритет: <b>{_esc(case_read.priority)}</b>",
        f"Рабочий статус: {_esc(TASK_STATUS_LABELS.get(case_read.task_status, case_read.task_status))}",
        "",
        "<b>Подтверждено в CRM (официальный источник)</b>",
        DIVIDER,
        f"• CRM-статус: {_esc(case_read.crm_status)}",
        f"• Статус кейса: {_esc(case_read.status)}",
        f"• Ответственный: {_esc(case_read.assignee)}",
    ]
    if case_read.resolution:
        lines.append(f"• Решение: {_esc(case_read.resolution)}")

    lines += ["", "<b>Известно из чата (не официальное подтверждение)</b>", DIVIDER]
    if messages:
        for msg in messages:
            author = _esc(getattr(msg, "author", "") or "неизвестно")
            role = _esc(getattr(msg, "role", "") or "чат")
            text = _esc(getattr(msg, "text", "") or "")
            lines.append(f"• [{role}] {author}: {text}")
    else:
        lines.append("• Сообщений нет.")

    lines += ["", "<b>Расхождения и риски</b>", DIVIDER]
    if case_read.issues:
        for issue in case_read.issues:
            lines += [
                f"• <b>{_esc(issue.title)}</b> [{_esc(issue.severity)} / {_esc(issue.source)}]",
                f"  Основание: {_esc(issue.basis)}",
            ]
    else:
        lines.append("✅ Расхождений не выявлено.")

    lines += [
        "",
        "Официальный источник статусов — CRM. Сообщения чата не заменяют подтверждение.",
    ]
    return "\n".join(lines)


def format_case_card(case_read: CaseRead, case: m.CaseORM) -> str:
    """Короткая карточка для inline-меню: CRM, проблема, ответственный."""
    return "\n".join(
        [
            f"<b>{_esc(case_read.code)} — {_esc(case_read.title)}</b>",
            DIVIDER,
            f"Категория: {_esc(CATEGORY_TITLES.get(case_read.category, case_read.category))}",
            f"CRM-статус: <b>{_esc(case_read.crm_status)}</b>",
            f"Статус кейса: {_esc(case_read.status)}",
            f"Рабочий статус: {_esc(TASK_STATUS_LABELS.get(case_read.task_status, case_read.task_status))}",
            f"Приоритет: {_esc(case_read.priority)}",
            f"Ответственный: {_esc(case_read.assignee)}",
            f"Проблема: {_problem_text(case_read)}",
            "",
            f"Подтверждено в CRM: {_crm_facts(case_read, case)}",
        ]
    )


# ---------------------------------------------------------------------------
# Загрузка данных и синхронизация
# ---------------------------------------------------------------------------

async def load_category_cases(
    session: AsyncSession, category: str
) -> list[tuple[CaseRead, m.CaseORM]]:
    """Кейсы категории: (CaseRead, ORM-строка)."""
    result = await session.execute(
        select(m.CaseORM)
        .where(m.CaseORM.category == category)
        .order_by(m.CaseORM.created_at.desc())
    )
    return [(to_case_read(case), case) for case in result.scalars().all()]


async def load_all_cases(session: AsyncSession) -> dict[str, m.CaseORM]:
    """ORM-строки по кодам — нужны для разделения CRM/чат в сводке."""
    result = await session.execute(
        select(m.CaseORM).order_by(m.CaseORM.created_at.desc())
    )
    return {case.code: case for case in result.scalars().all()}


async def sync_sheets_after_mutation(session: AsyncSession) -> None:
    """Поддержать Google Sheets в актуальном состоянии после изменения."""
    if not settings.google_sheets_enabled:
        return
    try:
        await GoogleSheetsService().export_cases(await list_cases(session))
    except Exception as exc:
        logger.warning("Не удалось синхронизировать Google Sheets после действия бота: %s", exc)


async def build_summary_text() -> str:
    """Собрать текст сводки из сервисов."""
    async with AsyncSessionLocal() as session:
        summary = await build_summary(session)
        cases = await list_cases(session)
        raw_by_code = await load_all_cases(session)
    return format_summary(summary, cases, raw_by_code)


async def execute_action(
    code: str,
    action: str,
    delivery_time: str | None = None,
) -> tuple[str, bool]:
    """Выполнить действие и вернуть (текст ответа, успешность)."""
    code = (code or "").strip().upper()
    if not code:
        return "Укажите код кейса. Проверьте команду и попробуйте снова.", False

    try:
        async with AsyncSessionLocal() as session:
            result = await apply_action(session, code, action, delivery_time)
            if result["found"] and result["ok"]:
                await sync_sheets_after_mutation(session)
    except Exception:
        logger.exception("Не удалось выполнить действие %s для кейса %s", action, code)
        return "Не удалось выполнить действие. Попробуйте позже.", False

    if not result["found"]:
        return f"Кейс <code>{_esc(code)}</code> не найден. Проверьте код.", False
    if not result["ok"]:
        return _esc(result.get("error", "Действие недоступно для этого кейса.")), False

    read = result["case"]
    status_label = TASK_STATUS_LABELS.get(read.task_status, read.task_status)
    text = (
        f"✅ {_esc(result['message'])}\n"
        f"{DIVIDER}\n"
        f"Текущий статус: {_esc(status_label)} ({_esc(read.task_status)})\n"
        f"Статус кейса: {_esc(read.status)}"
    )
    return text, True


# ---------------------------------------------------------------------------
# Навигация по inline-меню
# ---------------------------------------------------------------------------

async def show_main_menu(callback: CallbackQuery) -> None:
    """Показать главное меню."""
    await send_to_callback(callback, start_text(), main_inline_keyboard())


async def show_cases_list(callback: CallbackQuery, prefix: str | None = None) -> None:
    """Показать список кейсов кнопками."""
    try:
        async with AsyncSessionLocal() as session:
            cases = await list_cases(session)
    except Exception:
        logger.exception("Не удалось загрузить список кейсов для inline-меню")
        await send_to_callback(callback, "Не удалось получить список кейсов. Попробуйте позже.")
        return

    if not cases:
        await send_to_callback(callback, NO_CASES_MESSAGE, main_inline_keyboard())
        return

    header = prefix or "<b>Управление кейсами</b>"
    text = f"{header}\n{DIVIDER}\nВыберите кейс:"
    await send_to_callback(callback, text, cases_inline_keyboard(cases))


async def show_case_card(callback: CallbackQuery, code: str) -> None:
    """Открыть карточку кейса по коду."""
    code = (code or "").strip().upper()
    try:
        async with AsyncSessionLocal() as session:
            case = await get_case(session, code)
            if case is None:
                await send_to_callback(
                    callback,
                    f"Кейс <code>{_esc(code)}</code> не найден в базе.",
                    main_inline_keyboard(),
                )
                return
            read = to_case_read(case)
            text = format_case_card(read, case)
    except Exception:
        logger.exception("Не удалось сформировать карточку кейса %s", code)
        await send_to_callback(callback, "Не удалось получить карточку кейса. Попробуйте позже.")
        return

    await send_to_callback(callback, text, case_card_keyboard(code))


async def perform_action(
    callback: CallbackQuery,
    code: str,
    action: str,
    delivery_time: str | None = None,
) -> None:
    """Выполнить действие из inline-меню и вернуться в карточку."""
    text, _ = await execute_action(code, action, delivery_time)
    await send_to_callback(callback, text, case_card_keyboard(code.upper()))


# ---------------------------------------------------------------------------
# FSM: создание кейса
# ---------------------------------------------------------------------------

@router.callback_query(F.data == CB_CREATE)
async def cb_create_task(callback: CallbackQuery, state: FSMContext) -> None:
    """Запустить мастер создания кейса."""
    await callback.answer()
    await state.set_state(CreateCase.waiting_data)
    await send_to_callback(
        callback,
        "<b>Создание задачи</b>\n"
        f"{DIVIDER}\n"
        "Введите данные одним сообщением в формате:\n"
        "<code>Категория | Название | Сотрудник | Ответственный</code>\n\n"
        "Пример:\n"
        "<code>доставка | Доставка карты ПАО | Иванова И.И. | Петров П.П.</code>\n\n"
        "Категория: onboarding/оформление, offboarding/увольнение, "
        "delivery/доставка. Сотрудник и ответственный могут быть пустыми.",
        cancel_keyboard(),
    )


@router.message(CreateCase.waiting_data)
async def process_create_data(message: Message, state: FSMContext) -> None:
    """Создать кейс по введённым данным."""
    try:
        payload = parse_create_input(message.text or "")
    except ValueError as exc:
        await message.answer(
            f"Не удалось разобрать данные: {_esc(exc)}.\n"
            "Формат: <code>Категория | Название | Сотрудник | Ответственный</code>",
            reply_markup=cancel_keyboard(),
        )
        return

    try:
        async with AsyncSessionLocal() as session:
            created = await create_case(session, **payload)  # type: ignore[arg-type]
            await sync_sheets_after_mutation(session)
    except ValueError as exc:
        await message.answer(
            f"Проверьте данные: {_esc(exc)}",
            reply_markup=cancel_keyboard(),
        )
        return
    except Exception:
        logger.exception("Не удалось создать кейс из Telegram FSM")
        await message.answer(
            "Не удалось создать кейс. Попробуйте позже.",
            reply_markup=cancel_keyboard(),
        )
        return

    await state.clear()
    await message.answer(
        f"✅ Кейс <code>{_esc(created.code)}</code> создан.\n"
        f"{DIVIDER}\n{_esc(created.title)}",
        reply_markup=main_inline_keyboard(),
    )


# ---------------------------------------------------------------------------
# FSM: редактирование поля
# ---------------------------------------------------------------------------

@router.callback_query(F.data.startswith("edit_"))
async def cb_edit_case(callback: CallbackQuery) -> None:
    """Показать поля, доступные для редактирования."""
    await callback.answer()
    code = callback.data[len("edit_"):].strip().upper()
    await send_to_callback(
        callback,
        f"<b>Редактирование {_esc(code)}</b>\n{DIVIDER}\nВыберите поле:",
        edit_fields_keyboard(code),
    )


@router.callback_query(F.data.startswith("field_"))
async def cb_select_field(callback: CallbackQuery, state: FSMContext) -> None:
    """Запустить FSM ввода нового значения поля."""
    await callback.answer()
    parts = (callback.data or "").split("_", 2)
    if len(parts) != 3:
        await send_to_callback(callback, "Не удалось определить поле.")
        return

    token, code = parts[1], parts[2].strip().upper()
    field = FIELD_TOKENS.get(token)
    if field is None:
        await send_to_callback(callback, "Неизвестное поле для редактирования.")
        return

    await state.update_data(code=code, field=field)
    await state.set_state(EditCase.waiting_value)
    hint = ""
    if field in {"crm_status", "assignee", "resolution"}:
        hint = "\nЧтобы очистить поле, отправьте <code>-</code>."
    await send_to_callback(
        callback,
        f"Введите новое значение: <b>{FIELD_LABELS[field]}</b>.{hint}",
        cancel_keyboard(),
    )


@router.message(EditCase.waiting_value)
async def process_edit_value(message: Message, state: FSMContext) -> None:
    """Применить новое значение выбранного поля."""
    data = await state.get_data()
    code = str(data.get("code", "")).strip().upper()
    field = str(data.get("field", ""))
    try:
        payload = build_case_update(field, message.text or "")
    except ValueError as exc:
        await message.answer(
            f"Проверьте значение: {_esc(exc)}",
            reply_markup=cancel_keyboard(),
        )
        return

    try:
        async with AsyncSessionLocal() as session:
            case = await get_case(session, code)
            if case is None:
                await state.clear()
                await message.answer(
                    f"Кейс <code>{_esc(code)}</code> не найден.",
                    reply_markup=main_inline_keyboard(),
                )
                return
            updated = await update_case(session, case, payload)
            await sync_sheets_after_mutation(session)
            text = format_case_card(updated, case)
    except Exception:
        logger.exception("Не удалось обновить кейс %s из Telegram FSM", code)
        await message.answer(
            "Не удалось сохранить изменение. Попробуйте позже.",
            reply_markup=cancel_keyboard(),
        )
        return

    await state.clear()
    await message.answer(
        f"✅ Поле «{FIELD_LABELS.get(field, field)}» обновлено.\n{DIVIDER}\n{text}",
        reply_markup=case_card_keyboard(code),
    )


# ---------------------------------------------------------------------------
# FSM: время доставки
# ---------------------------------------------------------------------------

@router.callback_query(F.data.startswith("do_delivery_"))
async def cb_delivery_time(callback: CallbackQuery, state: FSMContext) -> None:
    """Запросить время доставки ЧЧ:MM."""
    await callback.answer()
    code = callback.data[len("do_delivery_"):].strip().upper()
    await state.update_data(code=code)
    await state.set_state(DeliveryTime.waiting_time)
    await send_to_callback(
        callback,
        f"Введите ожидаемое время доставки для <code>{_esc(code)}</code> "
        "в формате <code>ЧЧ:MM</code>, например <code>13:30</code>.",
        cancel_keyboard(),
    )


@router.message(DeliveryTime.waiting_time)
async def process_delivery_time(message: Message, state: FSMContext) -> None:
    """Применить время доставки через case_service.apply_action()."""
    raw = (message.text or "").strip()
    if not validate_delivery_time(raw):
        await message.answer(
            "Некорректный формат. Укажите время как <code>13:30</code>.",
            reply_markup=cancel_keyboard(),
        )
        return

    data = await state.get_data()
    code = str(data.get("code", "")).strip().upper()
    text, ok = await execute_action(code, "update_delivery", raw)
    await state.clear()
    await message.answer(
        text,
        reply_markup=case_card_keyboard(code) if ok else main_inline_keyboard(),
    )


# ---------------------------------------------------------------------------
# Callback: меню, сводка, список, карточка
# ---------------------------------------------------------------------------

@router.callback_query(F.data == CB_SUMMARY)
async def cb_summary(callback: CallbackQuery) -> None:
    """Сводка по кнопке «📊 Сводка»."""
    await callback.answer()
    try:
        text = await build_summary_text()
    except Exception:
        logger.exception("Не удалось сформировать сводку из inline-меню")
        await send_to_callback(callback, "Не удалось получить сводку. Попробуйте позже.")
        return
    await send_to_callback(callback, text, main_inline_keyboard())


@router.callback_query(F.data == CB_MANAGE)
async def cb_manage_cases(callback: CallbackQuery) -> None:
    """Список кейсов по кнопке «📝 Управление кейсами»."""
    await callback.answer()
    await show_cases_list(callback)


@router.callback_query(F.data == CB_BACK_MENU)
async def cb_back_to_menu(callback: CallbackQuery, state: FSMContext) -> None:
    """Вернуться в главное меню и сбросить возможное состояние."""
    await callback.answer()
    await state.clear()
    await show_main_menu(callback)


@router.callback_query(F.data == CB_BACK_LIST)
async def cb_back_to_list(callback: CallbackQuery) -> None:
    """Вернуться к списку кейсов."""
    await callback.answer()
    await show_cases_list(callback)


@router.callback_query(F.data.startswith("case_"))
async def cb_open_case(callback: CallbackQuery) -> None:
    """Открыть карточку кейса из списка."""
    await callback.answer()
    code = callback.data[len("case_"):].strip().upper()
    await show_case_card(callback, code)


# ---------------------------------------------------------------------------
# Callback: действия
# ---------------------------------------------------------------------------

@router.callback_query(F.data.startswith("action_"))
async def cb_action_menu(callback: CallbackQuery) -> None:
    """Показать доступные действия над кейсом."""
    await callback.answer()
    code = callback.data[len("action_"):].strip().upper()
    await send_to_callback(
        callback,
        f"<b>Действие для {_esc(code)}</b>\n{DIVIDER}\nВыберите действие:",
        action_choices_keyboard(code),
    )


@router.callback_query(F.data.startswith("do_"))
async def cb_do_action(callback: CallbackQuery, state: FSMContext) -> None:
    """Выполнить выбранное действие (кроме ввода времени доставки)."""
    await callback.answer()
    parts = (callback.data or "").split("_", 2)
    if len(parts) != 3:
        await send_to_callback(callback, "Не удалось определить действие.")
        return

    token, code = parts[1], parts[2].strip().upper()
    if token == "delivery":
        # Обработано отдельным хендлером выше.
        return

    action = DO_ACTION_TOKENS.get(token)
    if action is None:
        await send_to_callback(callback, "Неизвестное действие.")
        return

    await state.clear()
    await perform_action(callback, code, action)


# ---------------------------------------------------------------------------
# Callback: удаление
# ---------------------------------------------------------------------------

@router.callback_query(F.data.startswith("delete_"))
async def cb_delete_confirm(callback: CallbackQuery) -> None:
    """Запросить подтверждение удаления."""
    await callback.answer()
    code = callback.data[len("delete_"):].strip().upper()
    await send_to_callback(
        callback,
        f"Вы уверены? Кейс <code>{_esc(code)}</code> будет удалён вместе с сообщениями.",
        delete_confirm_keyboard(code),
    )


@router.callback_query(F.data.startswith("confirm_delete_"))
async def cb_delete_execute(callback: CallbackQuery) -> None:
    """Удалить кейс после подтверждения."""
    await callback.answer()
    code = callback.data[len("confirm_delete_"):].strip().upper()
    try:
        async with AsyncSessionLocal() as session:
            removed = await delete_case(session, code)
            if removed:
                await sync_sheets_after_mutation(session)
    except Exception:
        logger.exception("Не удалось удалить кейс %s из Telegram", code)
        await send_to_callback(
            callback,
            "Не удалось удалить кейс. Попробуйте позже.",
            case_card_keyboard(code),
        )
        return

    if not removed:
        await send_to_callback(
            callback,
            f"Кейс <code>{_esc(code)}</code> не найден.",
            main_inline_keyboard(),
        )
        return

    await show_cases_list(callback, prefix=f"✅ Кейс <code>{_esc(code)}</code> удалён.")


# ---------------------------------------------------------------------------
# Callback: отмена FSM
# ---------------------------------------------------------------------------

@router.callback_query(F.data == CB_CANCEL)
async def cb_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    """Отменить текущий сценарий."""
    await callback.answer()
    data = await state.get_data()
    await state.clear()
    code = str(data.get("code", "")).strip().upper()
    if code:
        await show_case_card(callback, code)
    else:
        await show_main_menu(callback)


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext) -> None:
    """Текстовая отмена FSM."""
    await state.clear()
    await message.answer("Сценарий отменён.", reply_markup=main_inline_keyboard())


# ---------------------------------------------------------------------------
# Резервные текстовые команды
# ---------------------------------------------------------------------------

@router.message(Command("start"))
async def cmd_start(message: Message) -> None:
    """Приветствие с inline-меню."""
    await message.answer(start_text(), reply_markup=main_inline_keyboard())


@router.message(or_f(Command("summary"), F.text == BUTTON_SUMMARY))
async def cmd_summary(message: Message) -> None:
    """Общая сводка с KPI, процессами и разделением CRM/чат."""
    try:
        text = await build_summary_text()
    except Exception:
        logger.exception("Не удалось сформировать /summary")
        await respond(message, "Не удалось получить сводку. Попробуйте позже.")
        return

    await respond(message, text)


async def send_category_report(message: Message, category: str, formatter) -> None:  # type: ignore[no-untyped-def]
    """Загрузить кейсы категории и отправить отчёт."""
    try:
        async with AsyncSessionLocal() as session:
            rows = await load_category_cases(session, category)
        if not rows:
            await respond(message, NO_CASES_MESSAGE)
            return
        text = formatter(rows)
    except Exception:
        logger.exception("Не удалось сформировать отчёт по категории %s", category)
        await respond(message, "Не удалось получить данные. Попробуйте позже.")
        return

    await respond(message, text)


@router.message(or_f(Command("onboarding"), F.text == BUTTON_ONBOARDING))
async def cmd_onboarding(message: Message) -> None:
    """Кейсы оформления сотрудников."""
    await send_category_report(message, "onboarding", format_onboarding)


@router.message(or_f(Command("offboarding"), F.text == BUTTON_OFFBOARDING))
async def cmd_offboarding(message: Message) -> None:
    """Увольнения с акцентом на подтверждение руководителя."""
    await send_category_report(message, "offboarding", format_offboarding)


@router.message(or_f(Command("delivery"), F.text == BUTTON_DELIVERY))
async def cmd_delivery(message: Message) -> None:
    """Поставка карт: статус и риск задержки."""
    await send_category_report(message, "delivery", format_delivery)


@router.message(or_f(Command("critical"), F.text == BUTTON_CRITICAL))
async def cmd_critical(message: Message) -> None:
    """Критические кейсы с причиной."""
    try:
        async with AsyncSessionLocal() as session:
            cases = await list_cases(session)
        if not [case for case in cases if case.priority == "critical"]:
            await respond(message, NO_CRITICAL_MESSAGE)
            return
        text = format_critical(cases)
    except Exception:
        logger.exception("Не удалось сформировать /critical")
        await respond(message, "Не удалось получить список кейсов. Попробуйте позже.")
        return

    await respond(message, text)


@router.message(Command("case"))
async def cmd_case(message: Message) -> None:
    """Детали кейса по коду: /case S-24."""
    parts = (message.text or "").split()
    code = parts[1].strip().upper() if len(parts) > 1 else ""

    if not code:
        await respond(message, "Укажите код кейса, например: <code>/case S-24</code>")
        return

    try:
        async with AsyncSessionLocal() as session:
            case = await get_case(session, code)
            if case is None:
                await respond(
                    message, f"Кейс <code>{_esc(code)}</code> не найден в базе."
                )
                return
            read = to_case_read(case)
            messages = sorted(
                case.messages,
                key=lambda item: item.created_at or datetime.min,
            )
            text = format_case(read, messages)
    except Exception:
        logger.exception("Не удалось сформировать /case %s", code)
        await respond(message, "Не удалось получить детали кейса. Попробуйте позже.")
        return

    await respond(message, text)


def _split_command(message: Message) -> list[str]:
    """Разобрать команду и аргументы, игнорируя @botname."""
    parts = (message.text or "").split()
    if parts:
        parts[0] = parts[0].split("@", 1)[0]
    return parts


async def send_action_result(
    message: Message,
    code: str,
    action: str,
    delivery_time: str | None = None,
) -> None:
    """Выполнить действие над кейсом и показать итог с текущим статусом."""
    text, _ = await execute_action(code, action, delivery_time)
    await respond(message, text)


@router.message(Command("confirm_docs"))
async def cmd_confirm_docs(message: Message) -> None:
    """Подтвердить получение документов по кейсу оформления."""
    parts = _split_command(message)
    await send_action_result(message, parts[1] if len(parts) > 1 else "", "confirm_docs")


@router.message(Command("confirm_offboarding"))
async def cmd_confirm_offboarding(message: Message) -> None:
    """Подтвердить увольнение руководителем."""
    parts = _split_command(message)
    await send_action_result(
        message, parts[1] if len(parts) > 1 else "", "confirm_offboarding"
    )


@router.message(Command("update_delivery"))
async def cmd_update_delivery(message: Message) -> None:
    """Обновить ожидаемое время доставки: /update_delivery P-08 13:30."""
    parts = _split_command(message)
    code = parts[1] if len(parts) > 1 else ""
    time_value = parts[2] if len(parts) > 2 else ""
    await send_action_result(message, code, "update_delivery", time_value)


@router.message(Command("close_case"))
async def cmd_close_case(message: Message) -> None:
    """Закрыть кейс."""
    parts = _split_command(message)
    await send_action_result(message, parts[1] if len(parts) > 1 else "", "close")


# ---------------------------------------------------------------------------
# Запуск
# ---------------------------------------------------------------------------

async def run_bot(token: str) -> None:
    """Инициализировать БД и запустить polling."""
    await init_db()

    if settings.load_mock_data_on_startup:
        from app.services import mock_data

        async with AsyncSessionLocal() as session:
            await mock_data.load_mock_cases(session)

    bot = Bot(token=token, default=DefaultBotProperties(parse_mode="HTML"))
    dispatcher = Dispatcher(storage=MemoryStorage())
    dispatcher.include_router(router)

    logger.info("Telegram-бот запущен (polling)")
    try:
        await dispatcher.start_polling(
            bot,
            allowed_updates=dispatcher.resolve_used_update_types(),
        )
    finally:
        await bot.session.close()


def main() -> None:
    """Точка входа для `python -m app.bot.telegram_bot`."""
    logging.basicConfig(
        level=getattr(logging, settings.log_level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    token = settings.telegram_bot_token.strip()
    if not token:
        logger.warning(
            "TELEGRAM_BOT_TOKEN не задан в .env — бот не запущен. "
            "Добавьте токен от @BotFather в .env и повторите запуск."
        )
        return

    import asyncio

    asyncio.run(run_bot(token))


if __name__ == "__main__":
    main()
