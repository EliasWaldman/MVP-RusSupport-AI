"""Промпты для AI-анализа (готовятся к следующим этапам).

Все промпты локализованы на русском. Внедрять реальные AI-вызовы только
после готовности детерминированных правил.
"""

from __future__ import annotations

from app.schemas.case import CaseSignals

SUMMARY_PROMPT_TEMPLATE = """
Ты — анализатор Support-отдела RusSupport. Твоя задача — сформировать краткое
текстовое резюме для руководителя на основе уже выявленных правил.

Входные данные (уже валидированы и проверены правилами):
- Код кейса: {case_code}
- Категория: {category}
- Статус CRM: {crm_status}
- Очередь правил: {issues_codes}
- Приоритет (вычислен детерминированно): {priority}

Правила:
1. Не придумывай факты — используй только переданные данные.
2. Всегда указывай источник: CRM или чат.
3. Не меняй статус CRM и не выдавай предположения за факты.
4. Ответ на русском, 2-3 предложения, без общих фраз.

Пример:
"Кейс P-08: риск нарушения срока доставки. CRM: доставка запланирована на
2026-01-03, статус «В пути». Чат: курьер сообщил о задержке до 13:00."

Сформируй резюме:
""".strip()


def build_summary_prompt(signals: CaseSignals, issues: list[str], priority: str) -> str:
    """Собрать готовый промпт для LLM."""
    return SUMMARY_PROMPT_TEMPLATE.format(
        case_code=signals.code,
        category=signals.category,
        crm_status=signals.crm_status or "—",
        issues_codes=", ".join(issues) if issues else "нет",
        priority=priority,
    )