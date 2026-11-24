"""Файловое хранилище ключей интеграций (демо-уровень).

Ключи лежат в ``data/integrations.json`` во вложенном формате::

    {
      "openrouter": {"api_key": "..."},
      "telegram":   {"api_key": "..."}
    }

Старый плоский формат (``{"telegram": "..."}``) читается и автоматически
приводится к новому при чтении/сохранении. Для продакшена — переменные
окружения или секрет-менеджер (Vault и т.п.).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent.parent.parent
STORE_PATH = BASE_DIR / "data" / "integrations.json"


def ensure_store_file() -> Path:
    """Гарантировать существование файла и актуальный формат.

    Вызывается при старте приложения: если файла нет — создаётся ``{}``;
    если есть — legacy-формат нормализуется во вложенный. Битый JSON не
    перезаписывается, чтобы не потерять данные.
    """
    STORE_PATH.parent.mkdir(parents=True, exist_ok=True)

    if not STORE_PATH.exists():
        STORE_PATH.write_text("{}\n", encoding="utf-8")
        return STORE_PATH

    try:
        raw = json.loads(STORE_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return STORE_PATH
    if not isinstance(raw, dict):
        return STORE_PATH

    normalized = _normalize(raw)
    STORE_PATH.write_text(
        json.dumps(normalized, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return STORE_PATH


def _normalize(raw: dict[str, Any]) -> dict[str, dict[str, str]]:
    """Привести любой поддерживаемый формат к ``{service: {"api_key": ...}}``."""
    data: dict[str, dict[str, str]] = {}
    for service, value in raw.items():
        name = str(service)
        if isinstance(value, dict):
            key = str(value.get("api_key") or value.get("key") or "").strip()
        else:
            # Legacy: {"telegram": "token"}
            key = str(value or "").strip()
        if key:
            data[name] = {"api_key": key}
    return data


def _load() -> dict[str, dict[str, str]]:
    """Прочитать и нормализовать файл; при ошибке вернуть пустой словарь."""
    if not STORE_PATH.exists():
        return {}
    try:
        raw = json.loads(STORE_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    if not isinstance(raw, dict):
        return {}
    return _normalize(raw)


def all_data() -> dict[str, dict[str, str]]:
    """Все сохранённые сервисы с данными (вложенный формат)."""
    return _load()


def all_keys() -> dict[str, str]:
    """Сервис → ключ (плоский вид для обратной совместимости)."""
    return {name: item["api_key"] for name, item in _load().items()}


def get_key(service: str) -> str:
    """Ключ конкретного сервиса ('' если не сохранён)."""
    return _load().get(service, {}).get("api_key", "")


def set_key(service: str, key: str) -> None:
    """Сохранить ключ сервиса в ``data/integrations.json``."""
    data = _load()
    data[service] = {"api_key": key.strip()}
    ensure_store_file()
    STORE_PATH.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


__all__ = [
    "STORE_PATH",
    "all_data",
    "all_keys",
    "ensure_store_file",
    "get_key",
    "set_key",
]
