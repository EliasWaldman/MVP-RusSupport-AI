"""Базовый интерфейс AI-провайдера.

Реализации находятся в отдельных модулях (``openrouter_client.py`` и т.п.),
бизнес-логика работает только с этим интерфейсом.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class AIProviderError(RuntimeError):
    """Ошибка AI-провайдера (сеть, авторизация, невалидный ответ)."""


class AIProvider(ABC):
    """Интерфейс для всех AI-провайдеров."""

    name: str = "base"

    @abstractmethod
    async def analyze(self, prompt: str, **kwargs: Any) -> str:
        """Вернуть текстовый ответ на промпт."""

    async def is_available(self) -> bool:
        """Проверить, готов ли провайдер к вызову."""
        return True

    async def aclose(self) -> None:
        """Освободить ресурсы провайдера (по умолчанию нечего освобождать)."""
        return None