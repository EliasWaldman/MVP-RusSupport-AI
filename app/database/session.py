"""Асинхронный движок и сессия SQLAlchemy."""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.config import settings


class Base(DeclarativeBase):
    """Базовая модель всех таблиц."""


engine = create_async_engine(
    settings.database_url,
    echo=settings.database_echo,
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def get_session() -> AsyncSession:
    """Dependency FastAPI: возвращает асинхронную сессию БД."""
    async with AsyncSessionLocal() as session:
        yield session


# Минимальная демонстрационная миграция: добавляет новые колонки cases
# в уже существующий SQLite-файл (для продакшена — Alembic).
_CASE_ALTERS: tuple[str, ...] = (
    "task_status VARCHAR(16) NOT NULL DEFAULT 'open'",
    "assignee VARCHAR(64)",
    "resolution TEXT",
    "updated_at DATETIME",
)


async def init_db() -> None:
    """Создать таблицы по моделям Base.metadata и довести схему до актуальной."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

        def _upgrade_cases(sync_conn) -> None:  # type: ignore[no-untyped-def]
            existing = {
                row[1]
                for row in sync_conn.exec_driver_sql(
                    "PRAGMA table_info(cases)"
                ).fetchall()
            }
            wanted = ("task_status", "assignee", "resolution", "updated_at")
            for column, definition in zip(wanted, _CASE_ALTERS, strict=True):
                if column not in existing:
                    sync_conn.exec_driver_sql(
                        f"ALTER TABLE cases ADD COLUMN {definition}"
                    )
            # Для старых строк updated_at = created_at, чтобы поле не было NULL.
            sync_conn.execute(
                text("UPDATE cases SET updated_at = created_at WHERE updated_at IS NULL")
            )

        await conn.run_sync(_upgrade_cases)
