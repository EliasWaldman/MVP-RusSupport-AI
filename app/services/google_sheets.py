"""Выгрузка кейсов в Google Sheets через gspread.

Зависимости импортируются лениво: приложение стартует и без gspread,
а понятная ошибка возвращается только при попытке экспорта.
"""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path
from typing import Any

from app.config import BASE_DIR, settings

# Несколько CRUD-запросов могут запланировать выгрузку почти одновременно.
# gspread синхронный и работает в потоках, поэтому сериализуем именно запись.
_export_lock = threading.Lock()

HEADERS: list[str] = [
    "Код",
    "Название",
    "Категория",
    "Статус",
    "Приоритет",
    "Ответственный",
]

SCOPES: tuple[str, ...] = ("https://www.googleapis.com/auth/spreadsheets",)


class GoogleSheetsError(RuntimeError):
    """Ошибка конфигурации или выгрузки в Google Sheets."""


def _field(case: Any, name: str) -> Any:
    """Достать поле из Pydantic-модели, ORM-объекта или словаря."""
    if isinstance(case, dict):
        return case.get(name)
    return getattr(case, name, None)


def _as_text(value: Any) -> str:
    """Привести значение ячейки к строке (None → пустая ячейка)."""
    if value is None:
        return ""
    return str(value)


def _describe_error(exc: BaseException) -> str:
    """Сформировать непустое сообщение даже у `PermissionError()` без аргументов.

    gspread превращает HTTP 403 в голый `PermissionError`, у которого
    `str(exc) == ""`. Настоящая причина лежит в `exc.__cause__.response`.
    """
    parts: list[str] = []

    text = str(exc).strip()
    if text:
        parts.append(text)

    cause = exc.__cause__ or exc.__context__
    cause_text = str(cause).strip() if cause is not None else ""
    if cause_text and cause_text != text:
        parts.append(f"причина: {cause_text}")

    response = getattr(exc, "response", None) or getattr(cause, "response", None)
    if response is not None:
        status = getattr(response, "status_code", None)
        body = (getattr(response, "text", "") or "").strip()
        if status is not None:
            parts.insert(0, f"HTTP {status}")
        if body:
            parts.append(body[:500])

    if not parts:
        parts.append(type(exc).__name__)

    return "; ".join(parts)


class GoogleSheetsService:
    """Экспортирует список кейсов на первый лист настроенной таблицы."""

    def _client(self) -> Any:
        """Создать авторизованный gspread-клиент."""
        try:
            import gspread
            from google.oauth2.service_account import Credentials
        except ImportError as exc:
            raise GoogleSheetsError(
                "Для выгрузки установите зависимости: "
                "pip install gspread google-auth"
            ) from exc

        if not settings.google_sheets_enabled:
            raise GoogleSheetsError(
                "Google Sheets выключен (GOOGLE_SHEETS_ENABLED=False)"
            )

        spreadsheet_id = (settings.google_sheets_spreadsheet_id or "").strip()
        if not spreadsheet_id:
            raise GoogleSheetsError("Не задан GOOGLE_SHEETS_SPREADSHEET_ID")

        credentials_path = Path(
            settings.google_sheets_credentials_file or ""
        ).expanduser()
        if not credentials_path.is_absolute():
            # Поддерживаем и `data/google_credentials.json`, и просто
            # `google_credentials.json` (фактически лежащий в data/).
            candidates = (
                BASE_DIR / credentials_path,
                BASE_DIR / "data" / credentials_path,
            )
            credentials_path = next(
                (item for item in candidates if item.is_file()), candidates[0]
            )
        if not credentials_path.is_file():
            raise GoogleSheetsError(
                f"Файл credentials не найден: {credentials_path}"
            )

        try:
            credentials = Credentials.from_service_account_file(
                str(credentials_path), scopes=list(SCOPES)
            )
            return gspread.authorize(credentials)
        except Exception as exc:
            raise GoogleSheetsError(
                f"Ошибка авторизации Google Sheets: {_describe_error(exc)}"
            ) from exc

    @staticmethod
    def _write(worksheet: Any, rows: list[list[str]]) -> None:
        """Записать значения; совместимо с gspread 5.x и 6.x."""
        try:
            # gspread >= 6: update(values, range_name)
            worksheet.update(rows, "A1")
        except TypeError:
            # gspread 5.x: update(range_name, values)
            worksheet.update("A1", rows)

    def _export_sync(self, cases: list) -> None:
        """Сериализовать запись: автовыгрузки могут накладываться."""
        with _export_lock:
            self._export_unlocked(cases)

    def _export_unlocked(self, cases: list) -> None:
        """Синхронная часть экспорта (выполняется в отдельном потоке)."""
        client = self._client()
        spreadsheet_id = settings.google_sheets_spreadsheet_id.strip()

        try:
            spreadsheet = client.open_by_key(spreadsheet_id)
            worksheet = spreadsheet.sheet1
            worksheet.clear()

            rows = [
                [
                    _as_text(_field(case, "code")),
                    _as_text(_field(case, "title")),
                    _as_text(_field(case, "category")),
                    _as_text(_field(case, "status")),
                    _as_text(_field(case, "priority")),
                    _as_text(_field(case, "assignee")),
                ]
                for case in cases
            ]
            self._write(worksheet, [HEADERS, *rows])
        except GoogleSheetsError:
            raise
        except Exception as exc:
            raise GoogleSheetsError(
                f"Не удалось выгрузить кейсы: {_describe_error(exc)}"
            ) from exc

    async def export_cases(self, cases: list) -> dict:
        """Очистить лист, записать заголовки и строки кейсов."""
        # gspread синхронный — выносим сетевые вызовы из event loop.
        await asyncio.to_thread(self._export_sync, cases)
        return {"status": "ok", "rows_exported": len(cases)}


__all__ = ["GoogleSheetsError", "GoogleSheetsService", "HEADERS"]
