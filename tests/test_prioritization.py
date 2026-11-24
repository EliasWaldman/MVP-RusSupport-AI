"""Тесты правил приоритизации (детерминированная бизнес-логика)."""

from datetime import date

from app.schemas.case import CaseIssue, CaseSignals
from app.services.prioritization import (
    compute_priority_score,
    detect_issues,
    is_problematic,
    prioritize,
    resolve_priority,
    severity_weight,
)


def _issue(severity: str) -> CaseIssue:
    return CaseIssue(
        code="TEST",
        title="test",
        severity=severity,  # type: ignore[arg-type]
        source="crm",
        basis="basis",
    )


def test_onboarding_docs_unconfirmed_warning():
    """День входа завтра, документы в CRM не подтверждены → warning."""
    signals = CaseSignals(
        code="S-17",
        title="Оформление сотрудника",
        category="onboarding",
        first_day_tomorrow=True,
        documents_expected=True,
        documents_confirmed=False,
    )
    issues = detect_issues(signals)
    assert len(issues) == 1
    assert issues[0].code == "ONBOARDING_DOCS_UNCONFIRMED"
    assert issues[0].severity == "warning"
    assert issues[0].source == "crm"

    priority, _ = prioritize(signals)
    assert priority == "medium"


def test_offboarding_official_confirmation_missing_critical():
    """Последний рабочий день, официальное подтверждение отсутствует → critical."""
    signals = CaseSignals(
        code="S-24",
        title="Увольнение",
        category="offboarding",
        last_work_day_today=True,
        official_confirmation_provided=False,
    )
    issues = detect_issues(signals)
    assert len(issues) == 1
    assert issues[0].code == "OFFBOARDING_OFFICIAL_CONFIRMATION_MISSING"
    assert issues[0].severity == "critical"

    priority, _ = prioritize(signals)
    assert priority == "critical"


def test_delivery_deadline_risk_critical():
    """Доставка в пути, курьер сообщил о задержке → critical."""
    signals = CaseSignals(
        code="P-08",
        title="Поставка карт",
        category="delivery",
        delivery_status="in_transit",
        delivery_delay_reported=True,
        delivery_deadline=date(2026, 1, 3),
        delivery_eta=date(2026, 1, 3),
    )
    issues = detect_issues(signals)
    assert len(issues) == 1
    assert issues[0].code == "DELIVERY_DEADLINE_RISK"
    assert issues[0].severity == "critical"

    priority, _ = prioritize(signals)
    assert priority == "critical"


def test_delivery_confirmed_info():
    """Поставка подтверждена → info, низкий приоритет."""
    signals = CaseSignals(
        code="P-09",
        title="Поставка карт",
        category="delivery",
        delivery_status="accepted",
        delivery_confirmed=True,
    )
    issues = detect_issues(signals)
    assert any(i.code == "DELIVERY_CONFIRMED" for i in issues)

    priority, _ = prioritize(signals)
    assert priority == "low"


def test_severity_weights():
    assert severity_weight("info") == 0
    assert severity_weight("warning") == 10
    assert severity_weight("critical") == 60
    assert severity_weight("unknown") == 0


def test_resolve_priority_boundaries():
    assert resolve_priority(0) == "low"
    assert resolve_priority(9) == "low"
    assert resolve_priority(10) == "medium"
    assert resolve_priority(29) == "medium"
    assert resolve_priority(30) == "high"
    assert resolve_priority(59) == "high"
    assert resolve_priority(60) == "critical"
    assert resolve_priority(100) == "critical"


def test_is_problematic():
    assert is_problematic([]) is False
    assert is_problematic([_issue("info")]) is False
    assert is_problematic([_issue("warning")]) is True
    assert is_problematic([_issue("critical")]) is True


def test_compute_priority_score():
    assert compute_priority_score([]) == 0
    assert compute_priority_score([_issue("info")]) == 0
    assert compute_priority_score([_issue("warning")]) == 10
    assert compute_priority_score([_issue("critical")]) == 60
    assert compute_priority_score([_issue("warning"), _issue("critical")]) == 70
    # Ограничение сверху 100.
    assert compute_priority_score([_issue("critical"), _issue("critical")]) == 100


def test_chat_evidence_does_not_override_crm():
    """Сообщение из чата само по себе не меняет приоритет без правила."""
    signals = CaseSignals(
        code="X-1",
        title="Тест",
        category="delivery",
        delivery_status="in_transit",
        delivery_delay_reported=False,
        delivery_confirmed=False,
    )
    issues = detect_issues(signals)
    assert issues == []
    priority, _ = prioritize(signals)
    assert priority == "low"