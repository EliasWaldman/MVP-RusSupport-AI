"""Тесты ключей интеграций: запись, маскировка, редактирование, GigaChat."""

from __future__ import annotations

import json
import re

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.dashboard import routes as dashboard_routes
from app.main import app
from app.services import integrations_store

client = TestClient(app)


@pytest.fixture
def store(tmp_path, monkeypatch):
    """Подменить файл хранилища, чтобы не трогать реальные ключи."""
    path = tmp_path / "integrations.json"
    monkeypatch.setattr(integrations_store, "STORE_PATH", path)
    monkeypatch.setattr(settings, "telegram_bot_token", "")
    monkeypatch.setattr(settings, "gigachat_credentials", "")
    monkeypatch.setattr(settings, "openrouter_api_key", "")
    monkeypatch.setattr(settings, "google_sheets_enabled", False)
    monkeypatch.setattr(settings, "google_sheets_spreadsheet_id", "")
    return path


def _card(html: str, service: str) -> str:
    match = re.search(
        r'<article[^>]*id="' + re.escape(service) + r'".*?</article>',
        html,
        re.S,
    )
    assert match, f"Карточка {service} не найдена"
    return match.group(0)


def test_ensure_store_file_creates_empty_object(store):
    store.unlink(missing_ok=True)

    path = integrations_store.ensure_store_file()

    assert path == store
    assert json.loads(store.read_text(encoding="utf-8")) == {}


def test_save_writes_nested_format_and_page_shows_active(store):
    response = client.post(
        "/integrations/hh/save",
        data={"api_key": "secret-hh"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/integrations"
    assert json.loads(store.read_text(encoding="utf-8")) == {
        "hh": {"api_key": "secret-hh"}
    }

    page = client.get("/integrations")
    assert page.status_code == 200
    assert "Активно" in _card(page.text, "hh")
    assert "Не подключено" in _card(page.text, "google")
    assert 'data-service="hh"' in page.text
    assert 'data-service="google"' in page.text
    assert 'data-service="crm"' in page.text


def test_saved_key_is_masked_in_page(store):
    client.post(
        "/integrations/hh/save",
        data={"api_key": "sk-or-secret-123456"},
        follow_redirects=False,
    )

    card = _card(client.get("/integrations").text, "hh")

    assert "sk-...****" in card
    assert "sk-or-secret-123456" not in card
    assert "data-masked-key" in card
    assert "data-edit-key" in card
    # Поле ввода активной карточки заблокировано до нажатия «Изменить».
    assert "disabled" in card


def test_inactive_google_card_shows_sheets_input_without_edit_button(store):
    card = _card(client.get("/integrations").text, "google")

    assert 'name="spreadsheet_id"' in card
    assert 'name="api_key"' not in card
    assert "Не подключено" in card
    assert "data-edit-key" not in card


def test_masked_placeholder_keeps_previous_key(store):
    client.post(
        "/integrations/crm/save",
        data={"api_key": "old-crm-token"},
        follow_redirects=False,
    )

    response = client.post(
        "/integrations/crm/save",
        data={"api_key": "********"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert json.loads(store.read_text(encoding="utf-8"))["crm"]["api_key"] == (
        "old-crm-token"
    )


def test_new_key_overwrites_previous_one(store):
    client.post(
        "/integrations/crm/save",
        data={"api_key": "old-crm-token"},
        follow_redirects=False,
    )
    client.post(
        "/integrations/crm/save",
        data={"api_key": "new-crm-token"},
        follow_redirects=False,
    )

    assert json.loads(store.read_text(encoding="utf-8"))["crm"]["api_key"] == (
        "new-crm-token"
    )


def test_save_rejects_unknown_service_and_empty_key(store):
    assert (
        client.post(
            "/integrations/unknown/save",
            data={"api_key": "x"},
            follow_redirects=False,
        ).status_code
        == 404
    )
    assert (
        client.post(
            "/integrations/crm/save",
            data={"api_key": "   "},
            follow_redirects=False,
        ).status_code
        == 400
    )
    assert not store.exists()


def test_legacy_flat_format_is_normalized_on_startup(store):
    store.write_text(json.dumps({"telegram": "old-token"}), encoding="utf-8")

    assert integrations_store.get_key("telegram") == "old-token"
    integrations_store.ensure_store_file()

    assert json.loads(store.read_text(encoding="utf-8")) == {
        "telegram": {"api_key": "old-token"}
    }
    assert "Активно" in _card(client.get("/integrations").text, "telegram")


def test_telegram_key_from_env_marks_card_active(store, monkeypatch):
    monkeypatch.setattr(settings, "telegram_bot_token", "env-token")

    page = client.get("/integrations")

    assert "Активно" in _card(page.text, "telegram")
    assert "Ключ задан в .env" in _card(page.text, "telegram")


def test_gigachat_card_from_env(store, monkeypatch):
    monkeypatch.setattr(settings, "gigachat_credentials", "giga-env-key")

    card = _card(client.get("/integrations").text, "gigachat")

    assert "Российский LLM-провайдер" in card
    assert "Активно" in card
    assert "giga-env-key" not in card
    assert "Ключ задан в .env" in card


def test_gigachat_card_from_file(store):
    client.post(
        "/integrations/gigachat/save",
        data={"api_key": "giga-file-key"},
        follow_redirects=False,
    )

    card = _card(client.get("/integrations").text, "gigachat")

    assert "Активно" in card
    assert "gig...****" in card
    assert "giga-file-key" not in card


def test_openrouter_key_from_env_marks_card_active(store, monkeypatch):
    monkeypatch.setattr(settings, "openrouter_api_key", "sk-or-env-key")

    card = _card(client.get("/integrations").text, "openrouter")

    assert "Активно" in card
    assert "sk-or-env-key" not in card
    assert "data-masked-key" in card
    assert "data-edit-key" in card


def test_google_sheets_from_env_marks_card_active(store, monkeypatch):
    monkeypatch.setattr(settings, "google_sheets_enabled", True)
    monkeypatch.setattr(settings, "google_sheets_spreadsheet_id", "sheet-id-123")

    card = _card(client.get("/integrations").text, "google")

    assert 'data-connected="true"' in card
    assert "Активно" in card
    assert "Не подключено" not in card
    # Теперь у Google Sheets своё поле: ID виден и редактируется.
    assert "ID таблицы (Spreadsheet ID)" in card
    assert "data-masked-key" in card
    assert "data-edit-key" in card
    assert 'name="spreadsheet_id"' in card
    assert 'action="/integrations/google_sheets/save"' in card
    assert "ID берется из ссылки на таблицу." in card
    assert "data/google_credentials.json" in card
    assert 'name="api_key"' not in card


@pytest.mark.parametrize(
    ("enabled", "spreadsheet_id"),
    [(False, "sheet-id-123"), (True, "")],
)
def test_google_sheets_incomplete_env_stays_inactive(
    store, monkeypatch, enabled, spreadsheet_id
):
    monkeypatch.setattr(settings, "google_sheets_enabled", enabled)
    monkeypatch.setattr(settings, "google_sheets_spreadsheet_id", spreadsheet_id)

    card = _card(client.get("/integrations").text, "google")

    assert 'data-connected="false"' in card
    assert "Не подключено" in card
    assert 'name="spreadsheet_id"' in card


def test_google_sheets_card_masks_long_spreadsheet_id(store, monkeypatch):
    spreadsheet_id = "1JdC0abcdefghijklmnopqrstuvwxyzObTQ"
    monkeypatch.setattr(settings, "google_sheets_enabled", True)
    monkeypatch.setattr(settings, "google_sheets_spreadsheet_id", spreadsheet_id)

    card = _card(client.get("/integrations").text, "google")

    assert "1JdC0...ObTQ" in card
    # Полный ID нужен только для редактирования, в видимом тексте его нет.
    assert 'data-masked-key>' + spreadsheet_id not in card
    assert 'data-edit-value="' + spreadsheet_id + '"' in card


def test_save_google_sheets_updates_env_and_settings(store, tmp_path, monkeypatch):
    env_path = tmp_path / ".env"
    env_path.write_text(
        "\n".join(
            [
                "APP_NAME=RusSupport AI",
                "GOOGLE_SHEETS_ENABLED=True",
                'GOOGLE_SHEETS_SPREADSHEET_ID="old-sheet-id"',
                "KEEP_ME=unchanged",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(dashboard_routes, "ENV_PATH", env_path)
    monkeypatch.setattr(settings, "google_sheets_enabled", True)
    monkeypatch.setattr(settings, "google_sheets_spreadsheet_id", "old-sheet-id")

    response = client.post(
        "/integrations/google_sheets/save",
        data={"spreadsheet_id": "new-sheet-id"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/integrations"

    env_text = env_path.read_text(encoding="utf-8")
    assert "GOOGLE_SHEETS_SPREADSHEET_ID=new-sheet-id" in env_text
    assert "old-sheet-id" not in env_text
    assert "KEEP_ME=unchanged" in env_text
    assert "APP_NAME=RusSupport AI" in env_text
    assert settings.google_sheets_spreadsheet_id == "new-sheet-id"

    card = _card(client.get("/integrations").text, "google")
    assert "new-sheet-id" in card
