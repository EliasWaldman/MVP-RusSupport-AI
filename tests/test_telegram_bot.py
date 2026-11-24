"""Тесты оформления и команд Telegram-бота RusSupport AI."""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import pytest
from aiogram.types import InlineKeyboardMarkup

from app.bot.telegram_bot import (
    BUTTON_CRITICAL,
    BUTTON_DELIVERY,
    BUTTON_OFFBOARDING,
    BUTTON_ONBOARDING,
    BUTTON_SUMMARY,
    CB_BACK_LIST,
    CB_BACK_MENU,
    CB_CANCEL,
    CB_CREATE,
    CB_MANAGE,
    CB_SUMMARY,
    NO_CASES_MESSAGE,
    CreateCase,
    DeliveryTime,
    EditCase,
    action_choices_keyboard,
    build_case_update,
    case_card_keyboard,
    cases_inline_keyboard,
    cmd_start,
    delete_confirm_keyboard,
    edit_fields_keyboard,
    format_case,
    format_critical,
    format_delivery,
    format_offboarding,
    format_summary,
    main_inline_keyboard,
    main_keyboard,
    normalize_category,
    parse_create_input,
    validate_delivery_time,
)
from app.schemas.case import CaseIssue, CaseRead


def _issue(code: str, title: str, severity: str = "critical", source: str = "crm") -> CaseIssue:
    return CaseIssue(
        code=code,
        title=title,
        severity=severity,  # type: ignore[arg-type]
        source=source,  # type: ignore[arg-type]
        basis="Основание для вывода.",
    )


def _case(
    code: str,
    category: str,
    priority: str = "low",
    issues: list[CaseIssue] | None = None,
) -> CaseRead:
    return CaseRead(
        code=code,
        title=f"Кейс {code}",
        category=category,  # type: ignore[arg-type]
        status="open",
        crm_status="pending",
        priority=priority,  # type: ignore[arg-type]
        issues=issues or [],
        task_status="open",
    )


def _raw(category: str) -> SimpleNamespace:
    return SimpleNamespace(
        documents_confirmed=False,
        official_confirmation_provided=False,
        delivery_status="in_transit",
        delivery_eta=date(2026, 11, 20),
        messages=[
            SimpleNamespace(
                role="courier" if category == "delivery" else "employee",
                author="Участник чата",
                text="Сообщение из чата",
                created_at=None,
            )
        ],
    )


class FakeMessage:
    def __init__(self, text: str = "") -> None:
        self.text = text
        self.answers: list[tuple[str, object]] = []

    async def answer(self, text: str, reply_markup: object = None) -> None:
        self.answers.append((text, reply_markup))


def test_main_keyboard_has_all_required_buttons() -> None:
    keyboard = main_keyboard()
    labels = [button.text for row in keyboard.keyboard for button in row]

    assert labels == [
        BUTTON_SUMMARY,
        BUTTON_ONBOARDING,
        BUTTON_OFFBOARDING,
        BUTTON_DELIVERY,
        BUTTON_CRITICAL,
    ]
    assert keyboard.resize_keyboard is True
    assert keyboard.persistent is True


async def test_start_is_polite_and_returns_inline_menu() -> None:
    message = FakeMessage("/start")

    await cmd_start(message)

    text, markup = message.answers[0]
    assert "Здравствуйте! Я AI-ассистент Support-отдела Руссмаркет." in text
    assert "оформлением, увольнением и доставкой карт" in text
    assert "Используйте кнопки ниже или введите команду" in text
    assert "Управление кейсами теперь через кнопки" in text
    assert isinstance(markup, InlineKeyboardMarkup)
    assert [button.callback_data for row in markup.inline_keyboard for button in row] == [
        CB_SUMMARY,
        CB_CREATE,
        CB_MANAGE,
    ]


def test_summary_has_kpi_processes_sources_and_critical_reason() -> None:
    critical = _case(
        "S-24",
        "offboarding",
        "critical",
        [_issue("OFFBOARDING_OFFICIAL_CONFIRMATION_MISSING", "Официальное подтверждение отсутствует")],
    )
    regular = _case("P-08", "delivery")
    summary = SimpleNamespace(
        total_cases=2,
        problematic_cases=1,
        resolved_cases=0,
        total_issues=1,
    )
    raw_by_code = {"S-24": _raw("offboarding"), "P-08": _raw("delivery")}

    text = format_summary(summary, [critical, regular], raw_by_code)

    assert "Всего кейсов: <b>2</b>" in text
    assert "Проблемных: <b>1</b>" in text
    assert "Решённых: <b>0</b>" in text
    assert "Оформление (onboarding)" in text
    assert "Увольнение (offboarding)" in text
    assert "Доставка (delivery)" in text
    assert "Подтверждено в CRM" in text
    assert "Известно из чата" in text
    assert "🚨 <b>S-24</b>" in text
    assert "Официальное подтверждение отсутствует" in text


def test_category_reports_highlight_specific_risks() -> None:
    offboarding_issue = _issue(
        "OFFBOARDING_OFFICIAL_CONFIRMATION_MISSING",
        "Официальное подтверждение отсутствует",
    )
    offboarding = format_offboarding(
        [(_case("S-24", "offboarding", "critical", [offboarding_issue]), _raw("offboarding"))]
    )
    assert "Подтверждение руководителя: ⚠️ отсутствует" in offboarding
    assert "🚨 <b>S-24</b>" in offboarding

    delivery_issue = _issue(
        "DELIVERY_DEADLINE_RISK", "Риск нарушения срока доставки", source="chat"
    )
    delivery = format_delivery(
        [(_case("P-08", "delivery", "critical", [delivery_issue]), _raw("delivery"))]
    )
    assert "Риск задержки: 🚨 есть" in delivery
    assert "Статус доставки: в пути" in delivery


def test_case_details_and_empty_messages() -> None:
    case = _case(
        "S-17",
        "onboarding",
        "high",
        [_issue("ONBOARDING_DOCS_UNCONFIRMED", "Документы не подтверждены в CRM", "warning")],
    )
    messages = [
        SimpleNamespace(role="employee", author="Елена", text="Документы отправлены")
    ]

    text = format_case(case, messages)

    assert "Подтверждено в CRM (официальный источник)" in text
    assert "Известно из чата (не официальное подтверждение)" in text
    assert "Расхождения и риски" in text
    assert "Елена: Документы отправлены" in text
    assert "Документы не подтверждены в CRM" in text


def test_critical_and_empty_category_messages() -> None:
    critical = _case(
        "P-08",
        "delivery",
        "critical",
        [_issue("DELIVERY_DEADLINE_RISK", "Риск нарушения срока доставки", source="chat")],
    )

    assert "🚨 <b>P-08</b>" in format_critical([critical])
    assert format_critical([_case("S-01", "onboarding")]) == "Нет критических кейсов."
    assert NO_CASES_MESSAGE == "Нет активных кейсов в этой категории."


def test_inline_keyboards_cover_full_crud_navigation() -> None:
    menu = main_inline_keyboard()
    assert [button.text for row in menu.inline_keyboard for button in row] == [
        "📊 Сводка",
        "➕ Создать задачу",
        "📝 Управление кейсами",
    ]

    cases = cases_inline_keyboard([_case("S-24", "offboarding"), _case("P-08", "delivery")])
    case_buttons = [button for row in cases.inline_keyboard for button in row]
    assert case_buttons[0].text == "S-24: Увольнение"
    assert case_buttons[0].callback_data == "case_S-24"
    assert case_buttons[1].text == "P-08: Доставка"
    assert case_buttons[-1].callback_data == CB_BACK_MENU

    card = case_card_keyboard("S-24")
    assert [button.text for row in card.inline_keyboard for button in row] == [
        "✏️ Редактировать",
        "✅ Выполнить действие",
        "🗑 Удалить",
        "🔙 Назад",
    ]
    assert [button.callback_data for row in card.inline_keyboard for button in row] == [
        "edit_S-24",
        "action_S-24",
        "delete_S-24",
        CB_BACK_LIST,
    ]

    edit = edit_fields_keyboard("S-24")
    assert [button.text for row in edit.inline_keyboard for button in row][:4] == [
        "Название",
        "Статус CRM",
        "Ответственный",
        "Решение",
    ]

    actions = action_choices_keyboard("S-24")
    assert [button.text for row in actions.inline_keyboard for button in row][:4] == [
        "Подтвердить документы",
        "Подтвердить увольнение",
        "Обновить доставку",
        "Закрыть",
    ]

    delete = delete_confirm_keyboard("S-24")
    assert delete.inline_keyboard[0][0].callback_data == "confirm_delete_S-24"


def test_fsm_states_and_create_input_parsing() -> None:
    assert CreateCase.waiting_data.state == "CreateCase:waiting_data"
    assert EditCase.waiting_value.state == "EditCase:waiting_value"
    assert DeliveryTime.waiting_time.state == "DeliveryTime:waiting_time"

    assert normalize_category("Доставка") == "delivery"
    assert normalize_category("увольнение") == "offboarding"
    assert normalize_category("unknown") is None

    payload = parse_create_input(
        "доставка | Доставка карты ПАО | Иванова И.И. | Петров П.П."
    )
    assert payload == {
        "category": "delivery",
        "title": "Доставка карты ПАО",
        "employee": "Иванова И.И.",
        "assignee": "Петров П.П.",
    }

    with pytest.raises(ValueError, match="4 поля"):
        parse_create_input("delivery | Только название")
    with pytest.raises(ValueError, match="категория"):
        parse_create_input("нет | Название | - | -")
    with pytest.raises(ValueError, match="название"):
        parse_create_input("delivery |   | - | -")


def test_update_payload_and_delivery_time_validation() -> None:
    assert build_case_update("title", " New title ").title == "New title"
    assert build_case_update("crm_status", "-").crm_status is None
    assert build_case_update("assignee", "Иванов").assignee == "Иванов"
    assert build_case_update("resolution", "Решено").resolution == "Решено"

    with pytest.raises(ValueError, match="название"):
        build_case_update("title", "   ")

    assert validate_delivery_time("13:30") is True
    assert validate_delivery_time("9:05") is True
    assert validate_delivery_time("25:10") is False
    assert validate_delivery_time("13-30") is False
