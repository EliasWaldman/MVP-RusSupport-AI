"""Конфигурация приложения.

Все настройки читаются из переменных окружения / файла `.env`.
Секреты никогда не должны попадать в исходный код.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    """Настройки RusSupport AI."""

    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- Приложение ---
    app_name: str = "RusSupport AI"
    app_env: str = "development"
    debug: bool = Field(default=False)
    host: str = "127.0.0.1"
    port: int = 8000
    log_level: str = "INFO"

    # --- База данных ---
    database_url: str = f"sqlite+aiosqlite:///{(BASE_DIR / 'russupport.db').as_posix()}"
    database_echo: bool = False

    # --- Демонстрационные данные ---
    mock_data_path: Path = BASE_DIR / "data" / "mock_data.json"
    load_mock_data_on_startup: bool = True

    # --- AI-провайдер (GigaChat / OpenRouter) ---
    # AI_PROVIDER выбирает активный клиент: gigachat (основной) или openrouter.
    # Реальные вызовы выполняются только если AI_ENABLED=true и задан ключ.
    ai_enabled: bool = False
    ai_provider: str = "gigachat"

    # GigaChat (developers.sber.ru). Ключ хранится только в .env / окружении.
    gigachat_credentials: str = ""
    gigachat_scope: str = "GIGACHAT_API_PERS"
    # Точная модель из кабинета: "GigaChat" (не "GigaChat:latest").
    gigachat_model: str = "GigaChat"
    # v1-эндпоинт: в нём есть модель GigaChat. SDK 0.2.x по умолчанию уходит
    # на /v2/chat/completions, где эта модель отдаёт 404 "No such model".
    gigachat_base_url: str = "https://gigachat.devices.sberbank.ru/api/v1"
    # Путь к корневому сертификату Минцифры. Пусто — SDK использует системный
    # store / certifi (сертификат можно добавить в certifi на сервере).
    gigachat_ca_bundle_file: str = ""
    # false только для локальной отладки; на сервере нужен root CA Минцифры.
    gigachat_verify_ssl_certs: bool = True

    # Сетевой прокси. systemd НЕ наследует HTTP_PROXY/HTTPS_PROXY из
    # интерактивного shell, поэтому значения нужно записать в .env —
    # клиент GigaChat применит их явно перед созданием httpx-клиента.
    http_proxy: str = ""
    https_proxy: str = ""
    no_proxy: str = ""

    # OpenRouter оставлен как запасной вариант (на заблокированном IP не работает).
    openrouter_api_key: str = ""
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    openrouter_model: str = "openrouter/free"
    # Необязательный доверенный HTTPS-прокси. Если пусто — httpx использует
    # переменные окружения HTTP_PROXY / HTTPS_PROXY (trust_env=True).
    openrouter_proxy: str = ""
    ai_timeout_seconds: float = 30.0
    ai_max_retries: int = 2

    # --- Google Sheets (выгрузка кейсов в таблицу) ---
    google_sheets_enabled: bool = False
    # Относительный путь резолвится относительно корня проекта (BASE_DIR).
    google_sheets_credentials_file: str = str(
        BASE_DIR / "data" / "google_credentials.json"
    )
    google_sheets_spreadsheet_id: str = ""

    # --- Telegram-бот (следующие этапы) ---
    telegram_bot_token: str = ""

    @property
    def is_sqlite(self) -> bool:
        return "sqlite" in self.database_url

    @property
    def ai_configured(self) -> bool:
        """AI реально доступен только если включён и есть ключ активного провайдера."""
        if not self.ai_enabled:
            return False
        if self.ai_provider == "gigachat":
            return bool(self.gigachat_credentials)
        return bool(self.openrouter_api_key)


@lru_cache
def get_settings() -> Settings:
    """Возвращает кэшированный экземпляр настроек."""
    return Settings()


settings = get_settings()
