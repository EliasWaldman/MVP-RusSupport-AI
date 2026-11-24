"""Тесты страницы документации продукта и переноса Swagger."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_docs_page_renders_product_guide():
    response = client.get("/docs")

    assert response.status_code == 200
    assert "Документация RusSupport AI" in response.text
    assert "AI-помощник для Support-отдела" in response.text
    assert "Как пользоваться" in response.text
    assert "Закрой S-24" in response.text
    assert "Технические детали" in response.text
    assert "Swagger UI" not in response.text


def test_docs_nav_item_is_active():
    html = client.get("/docs").text

    assert 'href="/docs"' in html
    assert 'class="nav-item is-active" href="/docs"' in html
    assert 'aria-current="page"' in html


def test_docs_page_has_all_external_links():
    html = client.get("/docs").text

    assert "https://t.me/RusSupport_AI_bot" in html
    assert "1JdC0W1RP3b0clVAmSH_sQHh5bpdhPIMgZBqFbi0ObTQ" in html
    assert 'href="/api/docs"' in html
    assert 'href="/openapi.json"' in html
    assert 'target="_blank"' in html
    assert 'rel="noopener noreferrer"' in html


def test_swagger_moved_to_api_docs():
    swagger = client.get("/api/docs")
    spec = client.get("/openapi.json")

    assert swagger.status_code == 200
    assert "Swagger UI" in swagger.text
    assert spec.status_code == 200
    assert spec.json()["info"]["title"] == "RusSupport AI"
