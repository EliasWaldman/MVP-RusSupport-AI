"""Клиент OpenRouter: прямой HTTP POST через httpx.

Ключ читается из окружения, никогда не хранится в коде. Все ошибки провайдера
преобразуются в ``AIProviderError``, чтобы агент мог включить regex-fallback.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

import httpx

from app.ai.base import AIProvider, AIProviderError
from app.config import settings

logger = logging.getLogger(__name__)

OPENROUTER_CHAT_URL = "https://openrouter.ai/api/v1/chat/completions"

COMMAND_SYSTEM_PROMPT = (
    "Ты — AI-ассистент. Если пользователь здоровается — ответь текстом. "
    "Если просит действие — верни JSON: {action: '...', case_code: '...', "
    "params: {...}}. Действия: confirm_docs, confirm_offboarding, "
    "update_delivery, close_case, create_case (создать новую задачу; "
    "кейс-код не указывать, передать params.category/title/employee/assignee)."
)

# response_format=json_object требует JSON даже для приветствий.
JSON_ONLY_NOTE = (
    ' Ответ всегда должен быть одним JSON-объектом. '
    'Для текстового ответа верни {"message": "..."}.'
)


def _strip_code_fence(content: str) -> str:
    """Убрать markdown-обёртку, если модель всё же её добавила."""
    cleaned = content.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(
            r"^```\s*(?:json)?\s*", "", cleaned, flags=re.IGNORECASE
        )
        cleaned = re.sub(r"\s*```\s*$", "", cleaned)
    return cleaned.strip()


class OpenRouterClient(AIProvider):
    """Провайдер OpenRouter с прямыми вызовами Chat Completions API."""

    name = "openrouter"

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        proxy: str | None = None,
    ) -> None:
        self._api_key = api_key or settings.openrouter_api_key
        self._model = model or settings.openrouter_model
        self._proxy = proxy or settings.openrouter_proxy

    async def is_available(self) -> bool:
        return settings.ai_enabled and bool(self._api_key)

    def _headers(self) -> dict[str, str]:
        if not self._api_key:
            raise AIProviderError(
                "OPENROUTER_API_KEY не задан — реальные AI-вызовы недоступны."
            )
        return {
            "Authorization": f"Bearer {self._api_key}",
            "HTTP-Referer": "https://ai.graf-magic.ru",
            "X-Title": "RusSupport AI",
            "Content-Type": "application/json",
        }

    async def _post_chat(
        self,
        messages: list[dict[str, str]],
        *,
        model: str | None = None,
        response_format: dict[str, str] | None = None,
        temperature: float = 0.0,
    ) -> str:
        """Отправить запрос и вернуть текст первого ответа."""
        body: dict[str, Any] = {
            "model": model or self._model,
            "messages": messages,
            "temperature": temperature,
        }
        if response_format is not None:
            body["response_format"] = response_format

        client_kwargs: dict[str, Any] = {
            "timeout": settings.ai_timeout_seconds
        }
        if self._proxy:
            client_kwargs["proxy"] = self._proxy

        try:
            async with httpx.AsyncClient(**client_kwargs) as client:
                response = await client.post(
                    OPENROUTER_CHAT_URL,
                    headers=self._headers(),
                    json=body,
                )
        except httpx.TimeoutException as exc:
            raise AIProviderError("OpenRouter: таймаут запроса") from exc
        except httpx.HTTPError as exc:
            raise AIProviderError("OpenRouter: ошибка сети") from exc

        status = response.status_code
        if status == 403:
            raise AIProviderError(
                "OpenRouter: Access denied by security policy (HTTP 403). "
                "Запрос отклонён на уровне сети/Cloudflare до проверки ключа — "
                "нужен другой IP-маршрут или прокси."
            )
        if status == 429:
            raise AIProviderError("OpenRouter: слишком много запросов (HTTP 429)")
        if 500 <= status < 600:
            raise AIProviderError(f"OpenRouter недоступен (HTTP {status})")
        if status != 200:
            raise AIProviderError(f"OpenRouter вернул HTTP {status}")

        try:
            payload = response.json()
        except ValueError as exc:
            raise AIProviderError("OpenRouter вернул невалидный JSON") from exc

        choices = payload.get("choices") if isinstance(payload, dict) else None
        if not choices:
            raise AIProviderError("OpenRouter вернул пустой список choices")

        message = choices[0].get("message") if isinstance(choices[0], dict) else None
        content = message.get("content") if isinstance(message, dict) else None
        if not content:
            raise AIProviderError("OpenRouter вернул пустой ответ")
        return str(content)

    async def generate_command_intent(
        self,
        user_text: str,
        cases_context: list,
        *,
        system_prompt: str | None = None,
    ) -> dict:
        """Распознать команду и вернуть JSON-намерение.

        ``system_prompt`` приходит из AIAgent. Если его нет, используется
        встроенный промпт. Ошибки 429/5xx, таймаут и сетевые сбои выбрасывают
        ``AIProviderError``.
        """
        if system_prompt is None:
            system_prompt = (
                COMMAND_SYSTEM_PROMPT
                + JSON_ONLY_NOTE
                + "\n\nКонтекст кейсов (JSON):\n"
                + json.dumps(cases_context, ensure_ascii=False)
            )

        content = await self._post_chat(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_text},
            ],
            response_format={"type": "json_object"},
        )

        cleaned = _strip_code_fence(content)
        try:
            parsed = json.loads(cleaned)
        except json.JSONDecodeError:
            return {"message": cleaned}

        if isinstance(parsed, dict):
            return parsed
        return {"message": str(parsed)}

    async def analyze(self, prompt: str, **kwargs: Any) -> str:
        """Текстовый ответ для резюме/советов (без json_object)."""
        return await self._post_chat(
            messages=[{"role": "user", "content": prompt}],
            model=kwargs.get("model"),
            temperature=float(kwargs.get("temperature", 0.0)),
        )

    async def aclose(self) -> None:
        """httpx-клиент создаётся на запрос, освобождать нечего."""
        return None
