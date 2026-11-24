"""Сборка активного AI-провайдера по настройкам."""

from __future__ import annotations

from app.ai.base import AIProvider, AIProviderError
from app.ai.gigachat_client import GigaChatClient
from app.ai.openrouter_client import OpenRouterClient
from app.config import settings


class MockProvider(AIProvider):
    """Детерминированный провайдер для тестов и локальной отладки."""

    name = "mock"

    async def analyze(self, prompt: str, **kwargs) -> str:  # type: ignore[override]
        return (
            "Локальный режим: AI-провайдер не подключён. "
            "Выводы сформированы детерминированными правилами RusSupport AI."
        )

    async def generate_command_intent(
        self,
        user_text: str,
        cases_context: list,
        *,
        system_prompt: str | None = None,
    ) -> dict:
        """Перевести AIAgent на regex-fallback, если реальный AI выключен."""
        raise AIProviderError(
            "AI выключен (AI_ENABLED=false) — включается regex-fallback."
        )


def get_provider() -> AIProvider:
    """Вернуть активный провайдер согласно настройкам.

    При ``ai_enabled=False`` всегда используется ``MockProvider``, поэтому
    приложение работает без внешних API. При ``AI_PROVIDER=gigachat`` —
    официальный SDK GigaChat, при ``openrouter`` — запасной HTTP-клиент.
    """
    if not settings.ai_enabled:
        return MockProvider()
    if settings.ai_provider == "gigachat":
        return GigaChatClient()
    if settings.ai_provider == "openrouter":
        return OpenRouterClient()
    return MockProvider()


__all__ = [
    "AIProvider",
    "AIProviderError",
    "GigaChatClient",
    "MockProvider",
    "get_provider",
]
