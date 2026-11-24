"""Рекомендации по кейсу: детерминированный вывод + опциональный AI.

Архитектура: базовая рекомендация строится из правил (не зависит от сети).
Если настроен реальный провайдер (settings.ai_configured), текст может быть
дополнен через AIProvider; при любой ошибке AI — fallback на детерминированный
вывод. AI никогда не меняет данные CRM и приоритеты.
"""

from __future__ import annotations

from app.ai import get_provider
from app.config import settings
from app.schemas.case import CaseIssue, CaseSignals

ADVICE_BY_CODE: dict[str, str] = {
    "ONBOARDING_DOCS_UNCONFIRMED": (
        "Запросить у сотрудника недостающие документы и подтвердить их получение "
        "в CRM до первого рабочего дня. Статус менять только после официального "
        "подтверждения в CRM."
    ),
    "OFFBOARDING_OFFICIAL_CONFIRMATION_MISSING": (
        "Получить официальное подтверждение руководителя в CRM до окончания "
        "последнего рабочего дня. Утверждение сотрудника в чате не является "
        "официальным подтверждением."
    ),
    "DELIVERY_DEADLINE_RISK": (
        "Связаться с курьером и получателем, зафиксировать актуальную ETA и "
        "при необходимости пересогласовать дедлайн поставки в CRM."
    ),
    "DELIVERY_CONFIRMED": (
        "Поставка подтверждена в CRM и в чате — кейс готов к закрытию."
    ),
}

GENERIC_ADVICE = (
    "Проверить факт в CRM как официальном источнике и только затем "
    "обновить рабочий статус кейса."
)


def build_recommendation(signals: CaseSignals, issues: list[CaseIssue]) -> str:
    """Детерминированная рекомендация на основе выявленных проблем."""
    if not issues:
        return (
            "Расхождений не выявлено: данные CRM и чатов согласованы. "
            "Кейс можно закрыть после финальной проверки в CRM."
        )

    parts = [
        f"Кейс {signals.code} ({signals.category}): "
        f"выявлено проблем: {len(issues)}."
    ]
    for index, issue in enumerate(issues, start=1):
        advice = ADVICE_BY_CODE.get(issue.code, GENERIC_ADVICE)
        parts.append(f"{index}. [{issue.source}] {issue.title} — {advice}")

    if any(issue.severity == "critical" for issue in issues):
        parts.append("Приоритет critical: эскалировать руководителю сегодня.")
    return " ".join(parts)


def recommendation_prompt(signals: CaseSignals, issues: list[CaseIssue]) -> str:
    """Промпт для LLM: только факты, с обязательным указанием источника."""
    issues_text = "\n".join(
        f"- {issue.code} ({issue.severity}, источник: {issue.source}): {issue.basis}"
        for issue in issues
    )
    return (
        "Ты — ассистент support-отдела RusSupport AI. Сформулируй краткую "
        "рекомендацию оператору на основе выявленных расхождений.\n\n"
        f"Код кейса: {signals.code}\n"
        f"Категория: {signals.category}\n"
        f"Официальный статус CRM: {signals.crm_status or '—'}\n"
        f"Проблемы:\n{issues_text or 'нет'}\n\n"
        "Правила: 1) только факты из переданных данных; 2) указывай источник "
        "(CRM или чат); 3) не меняй статус CRM; 4) ответ на русском, 2-3 "
        "предложения, без общих фраз.\nРекомендация:"
    )


async def build_recommendation_ai(
    signals: CaseSignals, issues: list[CaseIssue]
) -> str:
    """Рекомендация: AI при настроенном провайдере, иначе детерминированный текст."""
    fallback = build_recommendation(signals, issues)
    if not settings.ai_configured:
        return fallback
    try:
        provider = get_provider()
        if await provider.is_available():
            reply = await provider.analyze(recommendation_prompt(signals, issues))
            if reply and reply.strip():
                return reply.strip()
    except Exception:
        # AI не должен ломать рабочее место — используем детерминированный вывод.
        pass
    return fallback
