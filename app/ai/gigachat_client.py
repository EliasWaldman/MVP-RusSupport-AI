"""Клиент GigaChat для Command Bar.

Используется официальный SDK (``from gigachat import GigaChatAsyncClient``).
Импорт SDK намеренно ленивый: приложение должно запускаться и без пакета
``gigachat`` — тогда AIAgent корректно уйдёт в regex-fallback.

Любая ошибка SDK (авторизация, сертификат Минцифры, сеть, таймаут, пустой
ответ) превращается в ``AIProviderError``. Никакие секреты в логи не пишутся.
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

from app.ai.base import AIProvider, AIProviderError
from app.config import settings

logger = logging.getLogger(__name__)

# Тот же системный промпт, что использовался для OpenRouter.
COMMAND_SYSTEM_PROMPT = (
    "Ты — AI-ассистент. Если пользователь здоровается — ответь текстом. "
    "Если просит действие — верни JSON: {action: '...', case_code: '...', "
    "params: {...}}. Действия: confirm_docs, confirm_offboarding, "
    "update_delivery, close_case, create_case (создать новую задачу; "
    "кейс-код не указывать, передать params.category/title/employee/assignee)."
)

# GigaChat не гарантирует JSON-only режим для всех моделей, поэтому требование
# дублируется в промпте, а ответ дополнительно нормализуется на стороне кода.
JSON_ONLY_NOTE = (
    ' Ответ всегда должен быть одним JSON-объектом. '
    'Для текстового ответа верни {"message": "..."}.'
)


def _strip_code_fence(content: str) -> str:
    """Убрать markdown-обёртку, если модель всё же её добавила."""
    cleaned = content.strip()
    # GigaChat иногда оборачивает JSON в ```json ... ``` — снимаем обёртку.
    if cleaned.startswith("```"):
        cleaned = re.sub(
            r"^```\s*(?:json)?\s*", "", cleaned, flags=re.IGNORECASE
        )
        cleaned = re.sub(r"\s*```\s*$", "", cleaned)
    return cleaned.strip()


def _load_async_client_class() -> Any:
    """Лениво импортировать GigaChatAsyncClient из официального SDK."""
    try:
        from gigachat import GigaChatAsyncClient
    except ImportError as exc:
        raise AIProviderError(
            "GigaChat SDK не установлен: выполните `pip install gigachat`."
        ) from exc
    return GigaChatAsyncClient


def _get(value: Any, name: str, default: Any = None) -> Any:
    """Доступ к полю dict или pydantic-модели SDK."""
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)


def _items(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return list(value)
    return [value]


def _content_to_text(content: Any) -> str | None:
    """Привести текст ответа SDK (v1 str или v2 list[parts]) к строке."""
    if content is None:
        return None
    if isinstance(content, str):
        return content.strip() or None
    if isinstance(content, dict):
        for key in ("text", "content", "value"):
            text = _content_to_text(content.get(key))
            if text:
                return text
        return None

    chunks: list[str] = []
    for part in _items(content):
        text = _content_to_text(_get(part, "text")) or _content_to_text(
            _get(part, "content")
        )
        if text:
            chunks.append(text)
    return "".join(chunks) or None


def _extract_content(response: Any) -> str | None:
    """Достать текст из ответа GigaChat независимо от версии SDK/API."""
    # v1 (/v1/chat/completions): choices[0].message.content
    for choice in _items(_get(response, "choices")):
        message = _get(choice, "message")
        text = _content_to_text(_get(message, "content"))
        if text:
            return text

    # v2 (/v2/chat/completions): messages[role=assistant].content[].text
    for message in _items(_get(response, "messages")):
        role = _get(message, "role")
        if role not in (None, "assistant"):
            continue
        text = _content_to_text(_get(message, "content"))
        if text:
            return text
    return None


def _safe_error(exc: Exception) -> str:
    """Текст ошибки без ключа авторизации и без бесконечного лога."""
    text = str(exc)
    if settings.gigachat_credentials:
        text = text.replace(settings.gigachat_credentials, "***")
    return text[:500]


class GigaChatClient(AIProvider):
    """Провайдер GigaChat: распознавание команд и текстовые ответы."""

    name = "gigachat"

    def __init__(
        self,
        credentials: str | None = None,
        scope: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
        ca_bundle_file: str | None = None,
    ) -> None:
        self._credentials = credentials or settings.gigachat_credentials
        self._scope = scope or settings.gigachat_scope
        self._model = model or settings.gigachat_model
        self._base_url = base_url or settings.gigachat_base_url
        self._ca_bundle_file = ca_bundle_file or settings.gigachat_ca_bundle_file

    async def is_available(self) -> bool:
        return settings.ai_enabled and bool(self._credentials)

    def _build_client(self) -> Any:
        if not self._credentials:
            raise AIProviderError(
                "GIGACHAT_CREDENTIALS не задан — реальные AI-вызовы недоступны."
            )

        # httpx внутри SDK читает прокси из окружения в момент создания
        # httpx.AsyncClient. systemd не передаёт сервису proxy из shell,
        # поэтому подставляем значения, прочитанные из .env, явно.
        for env_name, value in (
            ("HTTP_PROXY", settings.http_proxy),
            ("HTTPS_PROXY", settings.https_proxy),
            ("NO_PROXY", settings.no_proxy),
        ):
            if value:
                os.environ[env_name] = value

        client_class = _load_async_client_class()
        kwargs: dict[str, Any] = {
            "credentials": self._credentials,
            "scope": self._scope,
            "model": self._model,
            "base_url": self._base_url,
            "timeout": settings.ai_timeout_seconds,
        }
        if self._ca_bundle_file:
            kwargs["ca_bundle_file"] = self._ca_bundle_file
        if not settings.gigachat_verify_ssl_certs:
            # Только для локальной отладки. В проде нужен root CA Минцифры.
            kwargs["verify_ssl_certs"] = False
        return client_class(**kwargs)

    async def _chat(self, messages: list[dict[str, str]]) -> str:
        """Отправить сообщения и вернуть текст первого ответа модели."""
        client = self._build_client()
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "temperature": 0.0,
        }

        try:
            # В SDK 0.2.x `achat.create()` принудительно переписывает URL на
            # /v2/chat/completions, где модель GigaChat отдаёт 404. Root-путь
            # `_achat()` шлёт такой же запрос на /v1/chat/completions со
            # строковым content — как рабочий curl. В старых SDK публичный
            # асинхронный метод назывался просто `achat()`.
            root_chat = getattr(client, "_achat", None)
            if callable(root_chat):
                response = await root_chat(payload)
            else:
                chat_api = getattr(client, "achat", None)
                if chat_api is not None and hasattr(chat_api, "create"):
                    response = await chat_api.create(payload)
                elif callable(chat_api):
                    try:
                        response = await chat_api(payload)
                    except TypeError:
                        response = await chat_api(**payload)
                else:
                    raise AIProviderError(
                        "GigaChat SDK не поддерживает асинхронный chat."
                    )
        except AIProviderError:
            raise
        except Exception as exc:
            # Сюда попадают и ssl.SSLCertVerificationError, и GigaChatException,
            # и сетевые сбои — агент должен переключиться на regex-fallback.
            raise AIProviderError(
                f"GigaChat: ошибка запроса ({type(exc).__name__}): "
                f"{_safe_error(exc)}"
            ) from exc
        finally:
            try:
                await client.aclose()
            except Exception:  # pragma: no cover - закрытие не должно ломать ответ
                logger.debug("GigaChat: не удалось закрыть клиент", exc_info=True)

        content = _extract_content(response)
        if not content:
            raise AIProviderError("GigaChat вернул пустой ответ")
        return content

    async def generate_command_intent(
        self,
        user_text: str,
        cases_context: list,
        *,
        system_prompt: str | None = None,
    ) -> dict:
        """Распознать команду и вернуть JSON-намерение.

        ``system_prompt`` приходит из AIAgent и содержит доменную логику.
        Если его нет (прямые вызовы/тесты), используется встроенный промпт.
        Чистый текст (приветствие) нормализуется в ``{"message": "..."}``.
        """
        if system_prompt is None:
            system_prompt = (
                COMMAND_SYSTEM_PROMPT
                + JSON_ONLY_NOTE
                + "\n\nКонтекст кейсов (JSON):\n"
                + json.dumps(cases_context, ensure_ascii=False)
            )

        content = await self._chat(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_text},
            ]
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
        """Текстовый ответ для резюме/советов."""
        return await self._chat([{"role": "user", "content": prompt}])

    async def aclose(self) -> None:
        """Клиент создаётся на запрос и закрывается в ``_chat``."""
        return None


__all__ = ["GigaChatClient"]
