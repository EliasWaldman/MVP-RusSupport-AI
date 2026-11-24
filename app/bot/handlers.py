"""Обработчики Telegram-бота (заглушка следующего этапа)."""

from __future__ import annotations

from app.config import settings


async def register_handlers(bot) -> None:  # type: ignore[no-untyped-def]
    """Зарегистрировать обработчики в aiogram-боте (не реализовано)."""
    if not settings.telegram_bot_token:
        raise RuntimeError(
            "TELEGRAM_BOT_TOKEN не задан. Для Telegram-бота настройте переменную "
            "окружения и подключите aiogram."
        )
    return None


async def handle_start_command(message) -> None:  # type: ignore[no-untyped-def]
    """Точка входа пользователя (следующий этап)."""
    await message.reply("Привет! RusSupport AI пока не подключён к Telegram.")