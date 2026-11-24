"""Точка входа FastAPI-приложения RusSupport AI."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.ai import get_provider
from app.api.routes import router as api_router
from app.config import settings
from app.database.session import AsyncSessionLocal, engine, init_db
from app.dashboard.routes import router as dashboard_router
from app.services.integrations_store import ensure_store_file

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Жизненный цикл: логирование, инициализация БД, демо-данные."""
    logging.basicConfig(
        level=getattr(logging, settings.log_level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    logger.info("Запуск %s (env=%s)", settings.app_name, settings.app_env)

    await init_db()

    # Файл ключей интеграций должен существовать с первого запуска.
    ensure_store_file()

    if settings.load_mock_data_on_startup:
        from app.services import mock_data

        async with AsyncSessionLocal() as session:
            await mock_data.load_mock_cases(session)

    logger.info("База данных готова, демонстрационные данные загружены.")
    yield

    await engine.dispose()


app = FastAPI(
    title=settings.app_name,
    description="Интеллектуальный помощник Support-отдела RusSupport AI.",
    version="0.1.0",
    lifespan=lifespan,
    # /docs отдан странице документации продукта, Swagger переехал на /api/docs.
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/openapi.json",
)


app.mount("/static", StaticFiles(directory="app/static"), name="static")

app.include_router(api_router)
app.include_router(dashboard_router)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request, exc):  # type: ignore[no-untyped-def]
    """Обработчик ошибок: единый JSON-ответ для API."""
    logger.exception("Unhandled error: %s", exc)
    return JSONResponse(
        status_code=500,
        content={"status": "error", "detail": str(exc)},
    )


__all__ = ["app", "get_provider"]
