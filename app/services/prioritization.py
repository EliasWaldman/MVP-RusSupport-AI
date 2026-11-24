"""Детерминированные правила приоритизации кейсов.

Критически важное требование: приоритет определяется только автоматическими
правилами предметной области и не должен зависеть от вывода LLM.
"""

from __future__ import annotations

from app.schemas.case import CaseIssue, CaseSignals, Priority

# Веса severity для агрегации (чем опаснее — тем выше приоритет).
SEVERITY_WEIGHTS: dict[str, int] = {
    "info": 0,
    "warning": 10,
    "critical": 60,
}

PRIORITY_CRITICAL_SCORE = 60
PRIORITY_HIGH_SCORE = 30
PRIORITY_MEDIUM_SCORE = 10

PROBLEM_SEVERITIES = frozenset({"warning", "critical"})


def severity_weight(severity: str) -> int:
    """Возвращает вес severity (неизвестные значения считаются нейтральными)."""
    return SEVERITY_WEIGHTS.get(severity, 0)


def compute_priority_score(issues: list[CaseIssue]) -> int:
    """Суммарный детерминированный счёт приоритета по весам проблем."""
    return min(100, sum(severity_weight(issue.severity) for issue in issues))


def resolve_priority(score: int) -> Priority:
    """Переводит детерминированный счёт в приоритет."""
    if score >= PRIORITY_CRITICAL_SCORE:
        return "critical"
    if score >= PRIORITY_HIGH_SCORE:
        return "high"
    if score >= PRIORITY_MEDIUM_SCORE:
        return "medium"
    return "low"


def is_problematic(issues: list[CaseIssue]) -> bool:
    """Кейс проблемный, если есть хотя бы один warning/critical-инцидент."""
    return any(issue.severity in PROBLEM_SEVERITIES for issue in issues)


def detect_issues(signals: CaseSignals) -> list[CaseIssue]:
    """Проверка правил предметной области. Возвращает список проблем.

    Правила:
    1. ``ONBOARDING_DOCS_UNCONFIRMED`` — CRM ожидает документы, а первый
       рабочий день наступает раньше, чем документы подтверждены.
    2. ``OFFBOARDING_OFFICIAL_CONFIRMATION_MISSING`` — последний рабочий
       день сегодня, официальное подтверждение руководителя отсутствует.
    3. ``DELIVERY_DEADLINE_RISK`` — доставка в пути, курьер сообщил о
       задержке, прибытие позже запланированного срока.
    4. ``DELIVERY_CONFIRMED`` — информационное: поставка завершена.
    """
    issues: list[CaseIssue] = []

    if (
        signals.category == "onboarding"
        and signals.first_day_tomorrow
        and signals.documents_expected
        and not signals.documents_confirmed
    ):
        issues.append(
            CaseIssue(
                code="ONBOARDING_DOCS_UNCONFIRMED",
                title="Документы не подтверждены в CRM",
                severity="warning",
                source="crm",
                basis=(
                    "CRM ожидает подтверждения документов, но официальное подтверждение "
                    "в CRM отсутствует. Первый рабочий день — завтра."
                ),
            )
        )

    if (
        signals.category == "offboarding"
        and signals.last_work_day_today
        and not signals.official_confirmation_provided
    ):
        issues.append(
            CaseIssue(
                code="OFFBOARDING_OFFICIAL_CONFIRMATION_MISSING",
                title="Официальное подтверждение отсутствует",
                severity="critical",
                source="crm",
                basis=(
                    "Последний рабочий день назначен сегодня, но подтверждение "
                    "ответственного руководителя в CRM отсутствует. Утверждение "
                    "сотрудника в чате не является официальным подтверждением."
                ),
            )
        )

    eta_after_deadline = (
        signals.delivery_deadline is not None
        and signals.delivery_eta is not None
        and signals.delivery_eta > signals.delivery_deadline
    )

    if (
        signals.category == "delivery"
        and signals.delivery_status in ("pending", "in_transit")
        and not signals.delivery_confirmed
        and (signals.delivery_delay_reported or eta_after_deadline)
    ):
        issues.append(
            CaseIssue(
                code="DELIVERY_DEADLINE_RISK",
                title="Риск нарушения срока доставки",
                severity="critical",
                source="chat",
                basis=(
                    f"CRM: доставка запланирована на {signals.delivery_deadline.isoformat()} "
                    "со статусом «В пути». Чат: курьер сообщил о задержке, прибытие "
                    f"ожидается не ранее {signals.delivery_eta.isoformat()}."
                ),
            )
        )

    if signals.delivery_status == "accepted" and signals.delivery_confirmed:
        issues.append(
            CaseIssue(
                code="DELIVERY_CONFIRMED",
                title="Поставка завершена",
                severity="info",
                source="crm",
                basis=(
                    "Приёмка подтверждена в CRM, получение и проверка количества "
                    "подтверждены в чате."
                ),
            )
        )

    return issues


def prioritize(signals: CaseSignals) -> tuple[Priority, list[CaseIssue]]:
    """Полный цикл: сигналы -> правила -> проблемы -> приоритет."""
    issues = detect_issues(signals)
    score = compute_priority_score(issues)
    return resolve_priority(score), issues
