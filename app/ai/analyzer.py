"""AI-анализатор: оркестрация правил и промптов.

На этапе 1 MVP AI используется опционально: детерминированные правила
вынесены в ``app.services.prioritization``, а промпт/вывод AI дополняют
нарратив сводки, если провайдер подключён.
"""

from __future__ import annotations

from typing import Any

from app.ai import get_provider
from app.schemas.case import CaseIssue, CaseSignals


class CaseAnalyzer:
    """Анализирует сигналы кейса через правила и (опционально) AI."""

    def __init__(self, provider: Any | None = None) -> None:
        self.provider = provider or get_provider()

    async def analyze(self, signals: CaseSignals) -> tuple[str, list[CaseIssue]]:
        """Возвращает (summary_text, issues) для кейса.

        Правила всегда выполняются; AI в MVP не должен менять данные CRM.
        """
        from app.services.prioritization import detect_issues, resolve_priority

        issues = detect_issues(signals)
        priority = resolve_priority(
            sum(
                {"info": 0, "warning": 30, "critical": 60}[i.severity]
                for i in issues
            )
        )

        summary_text = self._fallback_summary(signals, issues, priority)
        if await self.provider.is_available():
            try:
                summary_text = await self._ask_ai(signals, issues, priority, summary_text)
            except Exception:
                # AI не должен ломать MVP — используем детерминированный вывод.
                pass

        return summary_text, issues

    def _fallback_summary(
        self, signals: CaseSignals, issues: list[CaseIssue], priority: str
    ) -> str:
        parts = [f"Кейс {signals.code}: приоритет {priority}."]
        for issue in issues:
            parts.append(
                f"[{issue.source}] {issue.title}: {issue.basis}"
            )
        return " ".join(parts)

    async def _ask_ai(
        self,
        signals: CaseSignals,
        issues: list[CaseIssue],
        priority: str,
        fallback: str,
    ) -> str:
        prompt = (
            "Проанализируй кейс поддержки RusSupport AI. "
            f"Код: {signals.code}. Категория: {signals.category}. "
            f"Приоритет (детерминированно): {priority}. "
            f"Проблемы: {[i.code for i in issues]}."
        )
        return await self.provider.analyze(prompt)


def get_analyzer() -> CaseAnalyzer:
    """Фабрика анализатора (можно подменить провайдера в тестах)."""
    return CaseAnalyzer()
