# RusSupport AI — Отчёт и инструкция по запуску (Этап 1, MVP)

**Дата:** 17.11.2026
**Статус:** MVP этапа 1 готов к локальному тестированию
**Назначение документа:** описать, что реализовано, какой итоговый функционал получился, и дать пошаговую инструкцию по запуску и проверке.

---

## 1. Что сделано (по модулям)

### 1.1. Конфигурация — `app/config.py`
- Настройки через `pydantic-settings` (`BaseSettings`), читаются из `.env` и переменных окружения (регистронезависимо).
- Ключевые параметры: `APP_NAME`, `APP_ENV`, `DEBUG`, `HOST/PORT`, `LOG_LEVEL`, `DATABASE_URL`, `LOAD_MOCK_DATA_ON_STARTUP`, `AI_ENABLED`, `AI_PROVIDER`, `OPENROUTER_API_KEY`, `OPENROUTER_BASE_URL`, `OPENROUTER_MODEL`, `AI_TIMEOUT_SECONDS`, `AI_MAX_RETRIES`, `TELEGRAM_BOT_TOKEN`.
- API-ключей в исходном коде нет; ключ хранится только в `.env`.
- Хелперы: `is_sqlite`, `ai_configured` (AI доступен только при `AI_ENABLED=true` **и** непустом ключе).

### 1.2. База данных — `app/database/`
- `session.py`: асинхронный движок SQLAlchemy 2 (`sqlite+aiosqlite`), `AsyncSessionLocal`, dependency `get_session()`, `init_db()`.
- `models.py`: ORM-модели `CaseORM`, `MessageORM`, `IssueORM`.
  - `MessageORM.case_id` и `IssueORM.case_id` — внешние ключи на `cases.id` (каскадное удаление).
  - Поля кейса хранят **сигналы** из CRM и чатов; CRM-статус — отдельное поле `crm_status`.
- Таблицы создаются автоматически при старте (`Base.metadata.create_all`). Файл БД: `russupport.db`.

### 1.3. Валидация — `app/schemas/`
- `case.py`: `CaseSignals` (вход для правил), `CaseIssue` (frozen-модель вывода), `CaseRead`, `CaseCreate`, `CaseStatusUpdate`.
- `message.py`: `MessageCreate`, `MessageRead`.
- `summary.py`: `SummaryStats`, `SummaryRead`.
- Типы строгие: `Literal` для категории кейса, статуса, приоритета, severity, источника и статуса доставки — невалидные значения отклоняются на уровне Pydantic.

### 1.4. Детерминированные правила — `app/services/prioritization.py`
Веса severity: `info=0`, `warning=10`, `critical=60`. Счёт кейса = сумма весов (макс 100).
Пороги: `>=60 critical`, `>=30 high`, `>=10 medium`, иначе `low`.

| Правило | Условие | Severity | Источник |
|---|---|---|---|
| `ONBOARDING_DOCS_UNCONFIRMED` | Онбординг, первый день завтра, документы ожидаются, подтверждения в CRM нет | warning | crm |
| `OFFBOARDING_OFFICIAL_CONFIRMATION_MISSING` | Оффбординг, последний рабочий день сегодня, подтверждение руководителя отсутствует | critical | crm |
| `DELIVERY_DEADLINE_RISK` | Доставка в пути/ожидает, не подтверждена, есть сообщение о задержке или ETA позже дедлайна | critical | chat |
| `DELIVERY_CONFIRMED` | Доставка принята и подтверждена | info | crm |

- `is_problematic()`: кейс проблемный, если есть хотя бы один `warning`/`critical`.
- Приоритет вычисляется **только** этими правилами; LLM на приоритеты не влияет.

### 1.5. Сводка — `app/services/summary.py`
- `list_cases()`: список кейсов `CaseRead` с вычисленными приоритетом и проблемами.
- `build_summary()`: KPI (`total_cases`, `problematic_cases`, `resolved_cases`, `total_issues`, `by_priority`, `by_status`), текстовый `headline`, `highlights`, `problematic_codes`.
- Маппинг ORM → `CaseSignals` выполняется в `_case_signals()`; CRM-поля остаются официальными.

### 1.6. Демо-данные — `app/services/mock_data.py` + `data/mock_data.json`
- 4 кейса (S-17, S-24, P-08, P-09) с сообщениями чатов и сигналами.
- Загрузка идемпотентна (upsert по `code`), сообщения заменяются; включается флагом `LOAD_MOCK_DATA_ON_STARTUP=true`.

### 1.7. AI-абстракция — `app/ai/`
- `base.py`: интерфейс `AIProvider` + `AIProviderError`.
- `openrouter_client.py`: реальный клиент OpenRouter (OpenAI-совместимый SDK), включается только при `AI_ENABLED=true` и заданном ключе; таймаут и retry из настроек.
- `__init__.py`: `MockProvider` (детерминированная заглушка) и `get_provider()` — выбор провайдера по настройкам.
- `analyzer.py`: `CaseAnalyzer` — сначала правила, затем (если провайдер доступен) AI дополняет только текст резюме; при любой ошибке AI — fallback на детерминированный вывод. AI не меняет данные CRM.
- `prompts.py`: шаблон промпта для сводки (русский, с требованием указывать источник).

### 1.8. API и веб — `app/api/routes.py`, `app/dashboard/routes.py`, `app/main.py`
- `GET /api/health` — статус сервиса.
- `GET /api/cases` — список кейсов с приоритетами и проблемами.
- `GET /api/summary` — KPI-сводка для руководителя.
- `GET /` — HTML-дашборд (Jinja2): KPI-карточки, таблица кейсов, цветовые бейджи приоритетов, цветовой статус каждого кейса, список расхождений с указанием источника.
- `app/main.py`: lifespan (логирование, DDL, загрузка demo-данных), монтирование `/static`, глобальный обработчик ошибок → JSON 500.

### 1.9. Тесты — `tests/test_prioritization.py`
9 тестов правил приоритизации (включая границы порогов, веса severity и правило «чат не переопределяет CRM»).

### 1.10. Сопроводительные файлы
`.env.example` (без секретов), `.env` (локальная копия), `.gitignore` (исключает `.env`, `*.db`, `__pycache__`, `.venv`), `requirements.txt`, `pytest.ini`, `README.md`.

---

## 2. Итоговый функционал MVP

1. FastAPI-приложение с асинхронными обработчиками.
2. SQLite с автоматическим созданием таблиц и идемпотентной загрузкой демо-данных из JSON.
3. Веб-дашборд на русском языке: KPI-карточки (всего / проблемных / решённых / инцидентов), таблица кейсов, цветовые индикаторы приоритетов, отдельный статус каждого кейса, адаптивная вёрстка (CSS Grid + медиа-запросы).
4. API `/api/health`, `/api/cases`, `/api/summary` (OpenAPI-документация на `/docs`).
5. Детерминированные правила приоритизации и выявления расхождений CRM ↔ чаты.
6. Абстракция AI-провайдера (Mock по умолчанию, OpenRouter — опционально), AI не может менять CRM.
7. Логирование, обработка ошибок, валидация всех входных/выходных схем.
8. Тестовый набор pytest.

---

## 3. Требования к окружению

- Windows 10/11, PowerShell.
- Python 3.11+ (проверено на Python 3.12.14).
- ~150 МБ для зависимостей.

> В этой песочнице Python доступен по полному пути:
> `C:\Users\Ilia\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe`
> На обычной машине используйте `py` / `python`.

---

## 4. Пошаговый запуск

```powershell
# 1. Перейти в проект
cd D:\EliasWaldman\tests\russupport-ai

# 2. Виртуальное окружение
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1

# 3. Зависимости
pip install -r requirements.txt

# 4. Настройки (секретов в .env.example нет)
Copy-Item .env.example .env

# 5. Запуск
uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

Если `py` недоступен:
```powershell
C:\Users\Ilia\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Сброс демо-данных: удалить `russupport.db` и перезапустить сервер.

---

## 5. Как тестировать

### 5.1. API (PowerShell)

```powershell
Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/health" | ConvertTo-Json
Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/cases"   | ConvertTo-Json -Depth 5
Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/summary" | ConvertTo-Json -Depth 5
```

**Ожидаемые результаты:**

`/api/health`:
```json
{"status":"ok","app_name":"RusSupport AI","env":"development","ai_enabled":false}
```

`/api/cases` — 4 кейса с приоритетами:

| Код | Категория | Статус | Приоритет | Проблема |
|---|---|---|---|---|
| S-17 | onboarding | open | medium | Документы не подтверждены в CRM (warning, crm) |
| S-24 | offboarding | open | critical | Официальное подтверждение отсутствует (critical, crm) |
| P-08 | delivery | open | critical | Риск нарушения срока доставки (critical, chat) |
| P-09 | delivery | resolved | low | Поставка завершена (info, crm) |

`/api/summary`:
```json
{
  "total_cases": 4,
  "problematic_cases": 3,
  "resolved_cases": 1,
  "total_issues": 4,
  "by_priority": {"medium": 1, "critical": 2, "low": 1},
  "by_status": {"open": 3, "resolved": 1},
  "problematic_codes": ["S-17", "S-24", "P-08"]
}
```

### 5.2. Дашборд

Открыть http://127.0.0.1:8000/ — проверить:
- 4 KPI-карточки (Всего / Проблемных / Решенных / Инцидентов).
- Таблица: код, заголовок, категория, цветной бейдж приоритета, цветной статус, список расхождений с меткой источника `[crm]`/`[chat]`.
- Стили подключаются с `/static/style.css` (HTTP 200), вёрстка адаптивна при сужении окна.

### 5.3. Тесты

```powershell
pytest -v
```
Ожидание: **9 passed**.

### 5.4. Дополнительно
- OpenAPI: http://127.0.0.1:8000/docs
- Логи: в консоли видно загрузку «Загружено демонстрационных кейсов: 4».

> Примечание: в консоли PowerShell кириллица в логах может отображаться «кракозябрами» — это особенность OEM-кодировки консоли; JSON/HTML ответы корректны в UTF-8.

---

## 6. Включение AI (опционально, не влияет на приоритеты)

```powershell
# в .env:
AI_ENABLED=true
AI_PROVIDER=openrouter
OPENROUTER_API_KEY=sk-...   # только в локальном .env, никогда в git
```
При `AI_ENABLED=false` (по умолчанию) используется `MockProvider` — приложение работает полностью локально, без сети.

---

## 7. Известные ограничения этапа 1

- Telegram-бот (aiogram 3) и реальные вызовы OpenRouter не подключены — заглушки/опциональный провайдер.
- HTMX-интерактивность и боковое меню не реализованы (навигация — верхняя панель).
- SQLite — только для демо; для продакшена нужна PostgreSQL/миграции (Alembic).
- Дашборд read-only: создание/изменение кейсов — следующие этапы.
- Нет аутентификации/авторизации.
- Ежедневная сводка формируется по запросу к `/api/summary`; рассылка руководителю — следующий этап.

---

## 8. Структура файлов

```
.env / .env.example / .gitignore / requirements.txt / pytest.ini / README.md / REPORT.md
data/mock_data.json
tests/__init__.py, tests/test_prioritization.py
app/
  main.py, config.py
  api/routes.py
  dashboard/routes.py
  ai/base.py, openrouter_client.py, analyzer.py, prompts.py, __init__.py
  bot/handlers.py, keyboards.py, __init__.py
  services/prioritization.py, summary.py, mock_data.py, __init__.py
  schemas/case.py, message.py, summary.py, __init__.py
  database/models.py, session.py, __init__.py
  templates/base.html, dashboard.html
  static/style.css
```
