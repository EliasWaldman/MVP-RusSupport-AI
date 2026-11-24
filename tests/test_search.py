"""Тесты живого поиска кейсов на дашборде."""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.database import models as m
from app.database.session import Base, get_session
from app.main import app


@pytest.fixture
def search_client():
    """Изолированная in-memory БД с тремя кейсами для поиска."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")

    async def prepare() -> None:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with AsyncSession(engine, expire_on_commit=False) as db:
            db.add_all(
                [
                    m.CaseORM(
                        code="S-17",
                        title="Оформление сотрудника",
                        category="onboarding",
                    ),
                    m.CaseORM(code="S-24", title="Увольнение", category="offboarding"),
                    m.CaseORM(code="P-08", title="Поставка карт", category="delivery"),
                ]
            )
            await db.commit()

    asyncio.run(prepare())

    async def override_get_session():
        async with AsyncSession(engine, expire_on_commit=False) as db:
            yield db

    app.dependency_overrides[get_session] = override_get_session
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()
        asyncio.run(engine.dispose())


def test_search_input_wiring(search_client: TestClient):
    html = search_client.get("/").text

    assert 'id="search-input"' in html
    assert 'name="q"' in html
    assert 'hx-get="/"' in html
    assert 'hx-trigger="keyup changed delay:300ms"' in html
    assert 'hx-target="#cases-list"' in html
    assert 'id="cases-list"' in html
    assert "data-search-clear" in html


def test_search_matches_code_case_insensitive(search_client: TestClient):
    html = search_client.get("/", params={"q": "s-24"}).text

    assert 'data-code="S-24"' in html
    assert 'data-code="S-17"' not in html
    assert 'data-code="P-08"' not in html
    assert 'value="s-24"' in html


def test_search_matches_title_and_category(search_client: TestClient):
    by_title = search_client.get("/", params={"q": "поставка"}).text
    assert 'data-code="P-08"' in by_title
    assert 'data-code="S-17"' not in by_title

    by_category = search_client.get("/", params={"q": "DELIVERY"}).text
    assert 'data-code="P-08"' in by_category
    assert 'data-code="S-24"' not in by_category


def test_search_empty_state_message(search_client: TestClient):
    html = search_client.get("/", params={"q": "нет-такого"}).text

    assert "По запросу «нет-такого» ничего не найдено." in html
    assert 'id="cases-list"' in html


def test_empty_query_returns_all_cases(search_client: TestClient):
    html = search_client.get("/").text

    assert 'data-code="S-17"' in html
    assert 'data-code="S-24"' in html
    assert 'data-code="P-08"' in html
