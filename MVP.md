# RusSupport AI — MVP: подробное описание функционала

| Параметр | Значение |
|---|---|
| Проект | RusSupport AI (test-assignment) |
| Версия приложения | `0.1.0` (указана в `app/main.py`) |
| Версия документа | `1.0` |
| Дата фиксации | 18.11.2026 |
| Статус | MVP готов и работает; основной AI-провайдер — GigaChat |
| Корень проекта (локально) | `D:\EliasWaldman\work\graf-magic.ru\test` |
| Корень проекта (сервер) | `/var/www/russupport-ai` |
| Язык интерфейса | Русский |
| Деплой | FastAPI + Uvicorn, systemd-сервис `russupport` |

> Этот документ — исчерпывающая фиксация **текущего** функционала. Он дополняет
> `SOLUTION.md` (там собраны технические решения и ответы на пункты ТЗ), а не
> заменяет его. Секреты, ключи и токены в документ **не включены**.

---

## 1. Назначение и границы MVP

### 1.1 Что делает система

RusSupport AI — это веб-приложение рабочего места сотрудника Support-отдела
компании **Руссмаркет**. Оно объединяет:

1. **Детерминированную аналитику** — правила приоритизации кейсов оформления,
   увольнения и доставки карт working без участия LLM.
2. **Дашборд** — KPI, карточки кейсов с раскрывающимися деталями, раздел задач,
   страницы Integrations / Analytics / Settings.
3. **AI Command Bar** — глобальную плавающую командную строку, где обычным
   Russian-текстом можно отдавать команды ассистенту (`Закрой S-24`,
   `Создай задачу на оформление сотрудника Елена Аверина, HR manager`).
4. **Полный CRUD карточек** — создать, посмотреть, отредактировать вручную,
   выполнить действие и удалить кейс.
5. **Telegram-бота** — альтернативный канал reporting и управления на той же БД.

### 1.2 Что входит в MVP на момент фиксации

- FastAPI-приложение с тремя страницами интерфейса и плавающим AI-баром.
- SQLite + async SQLAlchemy (`aiosqlite`), автоматическое создание таблиц.
- Идемпотентная загрузка 4 демо-кейсов из `data/mock_data.json`.
- GigaChat через официальный SDK `gigachat==0.2.4`, v1-эндпоинт, модель
  `GigaChat` / `GigaChat:latest` (зависит от настроек кабинета).
- Regex-fallback на случай недоступности AI.
- Действия `confirm_docs`, `confirm_offboarding`, `update_delivery`, `close_case`,
  `create_case`.
- Ручное редактирование карточки: название, CRM-статус, статус кейса, статус
  задачи, ответственный, решение.
- Удаление карточки крестиком с подтверждением.
- Telegram-бот на `aiogram==3.13.1`.
- 60 автоматических тестов.

### 1.3 Что сознательно НЕ входит

- Авторизация/роли пользователей.
- Отдельная страница карточки (`case_detail.html`) и `GET /case/{code}` —
  детали раскрываются **inline** на дашборде.
- Alembic-миграции (есть минимальная «доводка» схемы в `init_db()`).
- Настоящая интеграция с CRM/Bitrix/Amo — только имитация действий в локальной БД.
- Автоматическое удаление закрытых задач. Закрытые (`task_status == "done"`)
  просто перестают показываться в разделе «Задачи», но остаются в БД до ручного
  удаления.
- Хранение ключей интеграций в секрет-менеджере — сейчас файл
  `data/integrations.json` (демо-уровень).
- Многопоточная генерация GigaChat: freemium-тариф работает в один поток,
  параллельные команды встают в очередь.

### 1.4 Ключевые принципы реализации

1. **LLM только выбирает действие.** Все изменения БД делает `case_service`.
2. **Приоритет и инциденты — детерминированные.** LLM не может их перезаписать.
3. **CRM — официальный источник статусов.** AI и правила не меняют `crm_status`;
   его может изменить только человек через форму ручного редактирования.
4. **Чат — дополнительное свидетельство.** Факты из переписки не подменяют CRM.
5. **Ошибка AI никогда не ломает интерфейс.** Включается regex-fallback.
6. **Секреты только в `.env` / окружении.** В код и документацию не пишутся.

---

## 2. Архитектура

### 2.1 Слои

```text
┌──────────────────────────────────────────────────────────────────┐
│  BROWSER                                                          │
│  base.html: сайдбар, topbar, #ai-response, #ai-toast, ai-bar      │
│  dashboard.html: KPI, секции «Требует внимания» и «Задачи»,       │
│                  карточки, inline-детали, кнопки действий,        │
│                  крестик удаления, форма ручного редактирования   │
└───────────────┬──────────────────────────────────────────────────┘
                │ fetch / POST формы
┌───────────────▼──────────────────────────────────────────────────┐
│  HTTP-СЛОЙ                                                        │
│  app/main.py               FastAPI, lifespan, static, exception   │
│  app/api/routes.py         /api/*: health, cases, ai/command      │
│  app/dashboard/routes.py   /, /integrations, /analytics,          │
│                            /settings, action, интеграции          │
└───────┬───────────────────────────────┬──────────────────────────┘
        │                               │
┌───────▼───────────────┐   ┌───────────▼──────────────────────────┐
│  AI-СЛОЙ              │   │  BUSINESS-СЛОЙ                        │
│  app/ai/__init__.py   │   │  app/services/ai_agent.py             │
│  gigachat_client.py   │   │  app/services/case_service.py         │
│  openrouter_client.py │   │  app/services/summary.py              │
│  base.py              │   │  app/services/prioritization.py       │
│                       │   │  app/services/mock_data.py            │
│  get_provider()       │   │  app/services/integrations_store.py   │
└───────┬───────────────┘   └───────────┬──────────────────────────┘
        │                               │
┌───────▼───────────────────────────────▼──────────────────────────┐
│  ДАННЫЕ                                                           │
│  app/database/models.py    CaseORM, MessageORM, IssueORM           │
│  app/database/session.py   engine, AsyncSession, init_db           │
│  app/schemas/*             Pydantic-схемы                          │
│  SQLite: russupport.db                                            │
└──────────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────────┐
│  ОТДЕЛЬНЫЙ ПРОЦЕСС                                                │
│  app/bot/telegram_bot.py  → работает с той же БД через case_service│
└──────────────────────────────────────────────────────────────────┘
```

### 2.2 Карта файлов

| Путь (от корня проекта) | Назначение |
|---|---|
| `app/main.py` | Точка входа; lifespan: логирование → `init_db()` → демо-данные; монтирование `/static`; подключение роутеров; единый JSON-обработчик 500 |
| `app/config.py` | Все настройки из `.env` через `pydantic-settings`, `lru_cache`, properties `is_sqlite` / `ai_configured` |
| `app/database/session.py` | Асинхронный движок, `AsyncSessionLocal`, `get_session()`, `init_db()` с доводкой схемы |
| `app/database/models.py` | ORM-модели `CaseORM`, `MessageORM`, `IssueORM` |
| `app/schemas/case.py` | `CaseCategory`, `CaseStatus`, `TaskStatus`, `CaseIssue`, `CaseSignals`, `CaseRead`, `CaseUpdate`, `CaseCreate`, `CaseCreateRequest`, `CaseDetailRead`, `CaseStatusUpdate` |
| `app/schemas/message.py` | `MessageCreate`, `MessageRead` |
| `app/schemas/summary.py` | `SummaryStats`, `SummaryRead` |
| `app/services/case_service.py` | CRUD кейсов: `get_case`, `get_case_detail`, `create_case`, `next_case_code`, `update_case`, `delete_case`, `add_message`, `apply_action` |
| `app/services/ai_agent.py` | `AIAgent`, `COMMAND_SYSTEM_PROMPT`, VALID_ACTIONS, regex-fallback, разбор интента, execute-логика |
| `app/services/summary.py` | `case_signals`, `to_case_read`, `list_cases`, `build_summary` |
| `app/services/prioritization.py` | `detect_issues`, `compute_priority_score`, `resolve_priority`, `prioritize`, `is_problematic` |
| `app/services/mock_data.py` | Идемпотентная загрузка демо-кейсов из JSON |
| `app/services/integrations_store.py` | Файловое хранилище ключей интеграций |
| `app/services/google_sheets.py` | `GoogleSheetsService.export_cases()` — очистка листа и выгрузка кейсов; запись сериализуется `threading.Lock`, чтобы параллельные автовыгрузки не бились за один лист |
| `app/ai/base.py` | Абстрактный `AIProvider`, `AIProviderError` |
| `app/ai/__init__.py` | `get_provider()`, `MockProvider` |
| `app/ai/gigachat_client.py` | Основной провайдер, SDK `GigaChatAsyncClient`, v1, очистка JSON |
| `app/ai/openrouter_client.py` | Резервный HTTP-провайдер на `httpx` |
| `app/api/routes.py` | `/api/health`, `/api/cases*`, `/api/summary`, `/api/ai/command`, `/api/export/sheets`; `schedule_sheets_export()` планирует автовыгрузку после мутаций |
| `app/dashboard/routes.py` | HTML-страницы и POST-экшены дашборда; `/api/cases/{code}/action` тоже триггерит автовыгрузку |
| `app/templates/base.html` | Общий каркас, навигация, AI Command Bar, темы, `#ai-response` |
| `app/templates/dashboard.html` | KPI, карточки, действия, удаление, JS-логика SPA |
| `app/templates/_case_edit.html` | Форма ручного редактирования (подключается в обе секции карточек) |
| `app/templates/analytics.html`, `integrations.html`, `settings.html` | Остальные разделы |
| `app/static/style.css` | Вся стилистика, включая scroll-fix и стили формы редактирования |
| `app/bot/telegram_bot.py` | Telegram-бот на aiogram 3 |
| `data/mock_data.json` | 4 демо-кейса |
| `scripts/check_ai_command.py` | Быстрая проверка `/api/ai/command` |
| `telegram_summary.py` | Разовая отправка сводки в Telegram |
| `tests/test_prioritization.py` | 9 тестов правил приоритизации |
| `tests/test_create_case.py` | 6 тестов create/edit/delete + AI-команд |
| `requirements.txt` | Зафиксированные версии зависимостей |
| `.env.example` | Шаблон конфигурации без реальных секретов |
| `pytest.ini` | `asyncio_mode = auto`, `testpaths = tests` |
| `SOLUTION.md` | Техническое решение и ответы на ТЗ |
| `MVP.md` | Этот документ |

### 2.3 Жизненный цикл AI-команды (пример создания задачи)

```text
1. Пользователь вводит в #ai-command:
   «Создай задачу на оформление сотрудника Елена Аверина, HR manager»
2. base.html → POST /api/ai/command {"command": "..."}
3. app/api/routes.py → AIAgent().process_command(text, session)
4. AIAgent._build_context(session) → список {code,title,category,status}
5. AIAgent подставляет контекст в COMMAND_SYSTEM_PROMPT (replace, не format!)
6. provider.generate_command_intent(text, context, system_prompt=...)
   → GigaChatClient → POST /api/v1/chat/completions
7. Очистка ответа: снимается обёртка ```json, JSON парсится в dict интента
8. AIAgent._normalize_action() → "create_case"
9. AIAgent._execute_create_case():
   category = onboarding, title = «Оформление сотрудника Елена Аверина»,
   employee = «Елена Аверина», assignee = «HR manager»
10. case_service.create_case() → код S-25, запись cases + messages, commit
11. Ответ API:
    {"status":"ok",
     "message":"Действие выполнено: create_case для кейса S-25",
     "action":"create_case",
     "case_code":"S-25"}
12. base.html показывает message в #ai-response и через 1.5 c делает reload
13. dashboard/routes.py передаёт в шаблон tasks = [незакрытые кейсы]
14. Новая карточка видна в секции «Задачи»
```

---

## 3. Стек и зависимости

Версии зафиксированы в `requirements.txt`:

| Пакет | Версия | Роль |
|---|---|---|
| `fastapi` | 0.115.6 | HTTP API и HTML-роутинг |
| `uvicorn[standard]` | 0.34.0 | ASGI-сервер |
| `sqlalchemy` | 2.0.36 | ORM, async-сессии |
| `aiosqlite` | 0.20.0 | Асинхронный драйвер SQLite |
| `aiogram` | 3.13.1 | Telegram-бот |
| `pydantic` | 2.9.2 | Схемы и валидация |
| `pydantic-settings` | 2.7.1 | Конфигурация из `.env` |
| `jinja2` | 3.1.5 | Шаблоны |
| `python-multipart` | 0.0.20 | Form-данные для интеграций |
| `requests` | 2.32.2 | Скрипт проверки AI |
| `httpx` | 0.28.1 | Резервный OpenRouter-клиент |
| `pytest` | 8.3.4 | Тесты |
| `pytest-asyncio` | 0.25.0 | Async-тесты |
| `python-dotenv` | 1.0.1 | `.env` |
| `gigachat` | 0.2.4 | Официальный SDK GigaChat |

Дополнительно: Python 3.12+ (локально проверено на 3.13), Node.js — только для
`node --check` JavaScript при разработке (в рантайме не нужен).

---

## 4. Установка и запуск

### 4.1 Локально (Windows, PowerShell)

```powershell
cd D:\EliasWaldman\work\graf-magic.ru\test
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env

# Тесты
python -m pytest -q

# Приложение
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload

# Проверка Command Bar (в другом терминале, сервер должен быть поднят)
python scripts/check_ai_command.py
python scripts/check_ai_command.py "Закрой S-24"

# Telegram-бот (если задан TELEGRAM_BOT_TOKEN)
python -m app.bot.telegram_bot
```

Открыть: `http://127.0.0.1:8000/`. Документация продукта: `/docs`. Документация API (Swagger): `/api/docs`, спецификация: `/openapi.json`.

### 4.2 Сервер (Linux)

```bash
cd /var/www/russupport-ai
source .venv/bin/activate

# Корневой сертификат Минцифры (обязательно для GigaChat в проде)
curl -k "https://gu-st.ru/content/lending/russian_trusted_root_ca_pem.crt" -w "\n" >> $(python -m certifi)
pip install -r requirements.txt

sudo systemctl restart russupport
sudo journalctl -u russupport -f
```

Сервис запускает:

```bash
uvicorn app.main:app --host 127.0.0.1 --port 8001
```

Важно: из unit-файла `/etc/systemd/system/russupport.service` была удалена
строка `EnvironmentFile=/etc/russupport/ai-proxy.env`. Наличие этого файла
приводило к подмене прокси и `ConnectTimeout` при обращении к GigaChat. Прокси,
если он нужен, задаётся в `.env` (`HTTP_PROXY`, `HTTPS_PROXY`, `NO_PROXY`).

### 4.3 Шаблон `.env` (без секретов)

```dotenv
APP_NAME=RusSupport AI
APP_ENV=development
DEBUG=true
HOST=127.0.0.1
PORT=8000
LOG_LEVEL=INFO

DATABASE_URL=sqlite+aiosqlite:///./russupport.db
DATABASE_ECHO=false
LOAD_MOCK_DATA_ON_STARTUP=true

AI_ENABLED=True
AI_PROVIDER=gigachat

GIGACHAT_CREDENTIALS=          # только реальный ключ, больше нигде
GIGACHAT_SCOPE=GIGACHAT_API_PERS
GIGACHAT_MODEL=GigaChat
GIGACHAT_BASE_URL=https://gigachat.devices.sberbank.ru/api/v1
GIGACHAT_VERIFY_SSL_CERTS=true
# GIGACHAT_CA_BUNDLE_FILE=/etc/ssl/certs/russian_trusted_root_ca.crt

# HTTP_PROXY=
# HTTPS_PROXY=
# NO_PROXY=127.0.0.1,localhost

# OPENROUTER_API_KEY=
# OPENROUTER_BASE_URL=https://openrouter.ai/api/v1
# OPENROUTER_MODEL=openrouter/free
AI_TIMEOUT_SECONDS=30
AI_MAX_RETRIES=2

TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
```

> Если ключ/токен когда-то был раскрыт в переписке или логах — его нужно
> **ротировать** в кабинете провайдера. В коде и `.md` файлы секреты не пишутся.

### 4.4 Полная таблица настроек

| Переменная | По умолчанию в `config.py` | Назначение |
|---|---|---|
| `APP_NAME` | `RusSupport AI` | Имя приложения, заголовки |
| `APP_ENV` | `development` | Логируемая среда |
| `DEBUG` | `False` | Режим отладки |
| `HOST` / `PORT` | `127.0.0.1` / `8000` | Адрес локального запуска |
| `LOG_LEVEL` | `INFO` | Уровень логирования |
| `DATABASE_URL` | `sqlite+aiosqlite:///<root>/russupport.db` | URL БД |
| `DATABASE_ECHO` | `False` | SQL-echo |
| `MOCK_DATA_PATH` | `<root>/data/mock_data.json` | Путь демо-данных |
| `LOAD_MOCK_DATA_ON_STARTUP` | `True` | Загружать демо при старте |
| `AI_ENABLED` | `False` | Включать реальные AI-вызовы. В `.env.example` уже `True` |
| `AI_PROVIDER` | `gigachat` | `gigachat` / `openrouter` / другое → MockProvider |
| `GIGACHAT_CREDENTIALS` | `""` | Ключ из developers.sber.ru |
| `GIGACHAT_SCOPE` | `GIGACHAT_API_PERS` | Scope токена |
| `GIGACHAT_MODEL` | `GigaChat` | Модель запроса |
| `GIGACHAT_BASE_URL` | `https://gigachat.devices.sberbank.ru/api/v1` | v1-эндпоинт |
| `GIGACHAT_CA_BUNDLE_FILE` | `""` | Путь к root CA (опционально) |
| `GIGACHAT_VERIFY_SSL_CERTS` | `True` | Проверка TLS; `false` только для отладки |
| `HTTP_PROXY` / `HTTPS_PROXY` / `NO_PROXY` | `""` | Прокси, явно подставляются перед созданием httpx-клиента |
| `OPENROUTER_API_KEY` | `""` | Резервный провайдер |
| `OPENROUTER_BASE_URL` | `https://openrouter.ai/api/v1` | Базовый URL |
| `OPENROUTER_MODEL` | `openrouter/free` | Модель |
| `OPENROUTER_PROXY` | `""` | Доверенный прокси для OpenRouter |
| `AI_TIMEOUT_SECONDS` | `30.0` | Таймаут AI |
| `AI_MAX_RETRIES` | `2` | Повторы |
| `TELEGRAM_BOT_TOKEN` | `""` | Токен бота |

Свойство `settings.ai_configured` истинно только при `AI_ENABLED=true` **и**
наличии ключа активного провайдера.

---

## 5. База данных

### 5.1 Движок и инициализация

- Асинхронный движок SQLAlchemy, `async_sessionmaker(expire_on_commit=False)`.
- `init_db()` вызывает `Base.metadata.create_all`, затем «демо-миграцию»:
  для уже существующего SQLite-файла добавляет недостающие колонки
  `task_status`, `assignee`, `resolution`, `updated_at`, а для старых строк
  копирует `updated_at = created_at`.
- Alembic не используется.

### 5.2 Таблица `cases`

| Колонка | Тип | Default / nullable | Смысл |
|---|---|---|---|
| `id` | INTEGER PK autoincrement | — | Внутренний ID |
| `code` | VARCHAR(32), unique, index | NOT NULL | Код кейса (`S-17`, `P-08`, `S-25`) |
| `title` | VARCHAR(200) | NOT NULL | Название |
| `category` | VARCHAR(32) | NOT NULL | `onboarding` / `offboarding` / `delivery` |
| `status` | VARCHAR(16) | `open` | Статус кейса |
| `crm_status` | VARCHAR(64) | nullable | Официальный статус CRM |
| `priority` | VARCHAR(16) | `low` | Кэш приоритета (пересчитывается на чтении) |
| `created_at` | DATETIME | `func.now()` | Создание |
| `task_status` | VARCHAR(16) | `open` | Статус задачи в работе |
| `assignee` | VARCHAR(64) | nullable | Ответственный |
| `resolution` | TEXT | nullable | Решение/результат |
| `updated_at` | DATETIME | nullable, `onupdate=func.now()` | Последнее изменение |
| `first_day_tomorrow` | BOOL | `False` | Онбординг: первый день завтра |
| `documents_expected` | BOOL | `False` | Онбординг: документы ожидаются |
| `documents_sent_by_employee` | BOOL | `False` | Документы отправлены сотрудником |
| `documents_confirmed` | BOOL | `False` | Документы подтверждены в CRM |
| `last_work_day_today` | BOOL | `False` | Оффбординг: последний день сегодня |
| `official_confirmation_provided` | BOOL | `False` | Подтверждение руководителя |
| `employee_claimed_discussed` | BOOL | `False` | Сотрудник утверждает, что вопрос решён |
| `delivery_status` | VARCHAR(32) | nullable | `pending` / `in_transit` / `accepted` / `completed` |
| `delivery_deadline` | DATE | nullable | Срок по CRM |
| `delivery_eta` | DATE | nullable | Ожидаемая дата из чата |
| `delivery_delay_reported` | BOOL | `False` | Задержка зафиксирована |
| `delivery_confirmed` | BOOL | `False` | Приёмка подтверждена |

### 5.3 Таблица `messages`

| Колонка | Тип | Смысл |
|---|---|---|
| `id` | INTEGER PK | — |
| `case_id` | FK → `cases.id` | Связь с кейсом |
| `case_code` | VARCHAR(32), index | Код кейса (дублируется для удобства) |
| `role` | VARCHAR(32) | `employee` / `courier` / `recipient` / `support` |
| `author` | VARCHAR(255) | Автор сообщения |
| `text` | TEXT | Текст |
| `created_at` | DATETIME | Время |

`CaseORM.messages` — `cascade="all, delete-orphan"`, `lazy="selectin"`.

### 5.4 Таблица `issues`

| Колонка | Тип | Смысл |
|---|---|---|
| `id` | INTEGER PK | — |
| `case_id` | FK → `cases.id` | — |
| `case_code` | VARCHAR(32), index | — |
| `code` | VARCHAR(64) | Код правила |
| `title` | VARCHAR(200) | Заголовок |
| `severity` | VARCHAR(16) | `info` / `warning` / `critical` |
| `source` | VARCHAR(16) | `crm` / `chat` / `rule` / `ai` |
| `basis` | TEXT | Обоснование |

> В текущем MVP таблица `issues` не наполняется: расхождения считаются «на
> лету» функцией `detect_issues()`. Страница Analytics делает запрос к таблице
> ради соответствия ТЗ, но для KPI берёт `max(summary.total_issues, count)`.

### 5.5 Демо-данные (`data/mock_data.json`)

Загрузка идемпотентна (upsert по `code`); сообщения удаляются и создаются заново при
повторной загрузке.

| Код | Категория | Статус | Ключевые факты | Итог |
|---|---|---|---|---|
| `S-17` | onboarding | open | первый день завтра, документы ожидаются и отправлены, но не подтверждены; Иван Иванов написал в чат | `ONBOARDING_DOCS_UNCONFIRMED`, warning, `medium` |
| `S-24` | offboarding | open | последний день сегодня, подтверждения руководителя нет; Ольга Петрова утверждает, что «все обсудил» | `OFFBOARDING_OFFICIAL_CONFIRMATION_MISSING`, critical, `critical` |
| `P-08` | delivery | open | `in_transit`, дедлайн 2026-01-03, курьер сообщил о задержке до 13:00 | `DELIVERY_DEADLINE_RISK`, critical, `critical` |
| `P-09` | delivery | resolved | `accepted`, приёмка подтверждена | `DELIVERY_CONFIRMED`, info, `low` |

Стартовые KPI: **4 кейса / 3 проблемных / 1 решённый / 4 инцидента**.

---

## 6. Детерминированная приоритизация

### 6.1 Поток данных

```text
CaseORM → case_signals() → CaseSignals (валидация Pydantic)
        → detect_issues() → list[CaseIssue]
        → compute_priority_score() → int 0..100
        → resolve_priority() → critical / high / medium / low
        → to_case_read() → CaseRead (передаётся в API и шаблон)
```

### 6.2 Правила

| Код правила | Условие | severity | source |
|---|---|---|---|
| `ONBOARDING_DOCS_UNCONFIRMED` | `category == onboarding` И `first_day_tomorrow` И `documents_expected` И НЕ `documents_confirmed` | `warning` | `crm` |
| `OFFBOARDING_OFFICIAL_CONFIRMATION_MISSING` | `category == offboarding` И `last_work_day_today` И НЕ `official_confirmation_provided` | `critical` | `crm` |
| `DELIVERY_DEADLINE_RISK` | `category == delivery` И `delivery_status in (pending, in_transit)` И НЕ `delivery_confirmed` И (`delivery_delay_reported` ИЛИ `delivery_eta > delivery_deadline`) | `critical` | `chat` |
| `DELIVERY_CONFIRMED` | `delivery_status == accepted` И `delivery_confirmed` | `info` | `crm` |

### 6.3 Веса и пороги

| severity | вес |
|---|---|
| `info` | 0 |
| `warning` | 10 |
| `critical` | 60 |

Сумма ограничена 100. Пороги: `>= 60 → critical`, `>= 30 → high`,
`>= 10 → medium`, иначе `low`.

`is_problematic()` = есть хотя бы один `warning` или `critical`.

### 6.4 Пример

Для `S-24`: одно правило `critical` → счёт 60 → приоритет `critical` →
попадает в KPI «Проблемных» и в блок «Требуют внимания».

---

## 7. AI Command Bar

### 7.1 Пользовательский интерфейс

В `app/templates/base.html` на **всех** страницах присутствуют:

| Элемент | ID / класс | Назначение |
|---|---|---|
| Форма | `#ai-bar`, `.ai-bar` | Отправка команды по Enter или кнопке |
| Поле ввода | `#ai-command` | Текст команды |
| Блок ответа | `#ai-response` | Сообщение ассистента; ошибки красные |
| Toast | `#ai-toast` | Дополнительные уведомления |
| Кнопка отправки | `.ai-bar__send` | Стрелка |

Поведение front-end:

- При новом вводе старый ответ очищается (`input` → `clearAiResponse`).
- Пока запрос идёт, кнопка отправки блокируется.
- Ответ показывается в `#ai-response` (`is-error` для ошибок).
- **Перезагрузка страницы через 1.5 c происходит только если в JSON есть
  поле `action`**. Просто текстовый ответ (приветствие) страницу не трогает.
- Быстрые команды в правой колонке дашборда подставляют текст в поле и
  диспатчат событие `input`, чтобы старый ответ исчез.
- Тема (dark/light) хранится в `localStorage`, применяется до отрисовки
  (anti-FOUC).

### 7.2 Контракт `POST /api/ai/command`

Запрос:

```json
{"command": "Закрой S-24"}
```

Ответ — всегда `200` (кроме ошибок сервера/валидации). Возможные варианты:

```json
// Успешное действие
{"status":"ok","message":"Действие выполнено: close_case для кейса S-24","action":"close_case"}

// Создание задачи
{"status":"ok","message":"Действие выполнено: create_case для кейса S-25","action":"create_case","case_code":"S-25"}

// Текстовый ответ модели
{"status":"ok","message":"Привет! Как могу помочь?"}

// AI прислал {"action":"unknown"}
{"status":"error","message":"Не понял команду. Попробуйте: 'Закрой S-24' или 'Подтверди документы S-17'"}

// AI недоступен и regex не распознал команду — ТОЧНЫЙ текст
{"status":"error","message":"AI временно недоступен, но я не понял команду. Попробуйте: 'Закрой S-24'"}
```

Точные константы определены в `app/services/ai_agent.py`:
`SUCCESS_MESSAGE`, `AI_UNKNOWN_MESSAGE`, `FALLBACK_ERROR_MESSAGE`,
`NO_TIME_MESSAGE`.

### 7.3 Выбор провайдера

`app/ai/__init__.py → get_provider()`:

| Условие | Провайдер |
|---|---|
| `AI_ENABLED=false` | `MockProvider` → бросает `AIProviderError`, агент уходит в regex |
| `AI_PROVIDER=gigachat` | `GigaChatClient` (основной) |
| `AI_PROVIDER=openrouter` | `OpenRouterClient` (резерв) |
| другое значение | `MockProvider` |

### 7.4 Системный промпт

Промпт живёт в `app/services/ai_agent.py` как `COMMAND_SYSTEM_PROMPT`.
Агент подставляет контекст через `str.replace("{cases_context}", ...)`, а не
через `format()`, потому что промпт содержит литеральные фигурные скобки
примеров JSON.

Полный текст:

```text
Ты — AI-ассистент Support-отдела компании Руссмаркет. Твоя задача — помогать сотрудникам управлять кейсами оформления, увольнения и доставки карт.
У тебя есть список активных кейсов:
{cases_context}

Доступные действия:
1. confirm_docs — подтвердить документы (для onboarding).
2. confirm_offboarding — подтвердить увольнение (для offboarding).
3. update_delivery — обновить время доставки (для delivery, требует параметр time).
4. close_case — закрыть кейс (для любого).
5. create_case — СОЗДАТЬ НОВУЮ задачу/кейс, если пользователь просит «создай задачу», «новый кейс», «оформи нового сотрудника» и т.п. Код кейса НЕ указывай — он генерируется системой.

Категории новых задач: onboarding (оформление), offboarding (увольнение), delivery (доставка карт).

Если пользователь просит выполнить действие, верни СТРОГО JSON:
{"action": "название_действия", "case_code": "код_кейса", "params": {"time": "время"}}
Пример: {"action": "close_case", "case_code": "S-24"}
Пример создания: {"action": "create_case", "params": {"category": "onboarding", "title": "Оформление сотрудника Елена Аверина", "employee": "Елена Аверина", "assignee": "HR manager"}}

Для create_case обязательно заполни params.category и params.title, а также params.employee и params.assignee, если они названы в запросе.

Если пользователь здоровается или задает вопрос — ответь обычным текстом (не JSON).
Если не понял команду — верни {"action": "unknown"}.
```

Контекст кейсов — JSON-массив:

```json
[
  {"code":"S-17","title":"Оформление сотрудника","category":"onboarding","status":"open"},
  {"code":"S-24","title":"Увольнение","category":"offboarding","status":"open"}
]
```

Промпт передаётся в провайдер как именованный аргумент `system_prompt=`.
У `GigaChatClient`, `OpenRouterClient` и `MockProvider` есть собственные
дефолтные промпты для прямых вызовов и тестов — они не ломают работу агента.

### 7.5 Поддерживаемые действия

| `action` | Что делает | Категория | Параметры |
|---|---|---|---|
| `confirm_docs` | Подтверждает документы; ставит `documents_confirmed=true`, `task_status=in_progress`, затем агент выставляет `task_status=done` | `onboarding` | `case_code` |
| `confirm_offboarding` | Официальное подтверждение увольнения; `official_confirmation_provided=true`, `task_status` → `done` | `offboarding` | `case_code` |
| `update_delivery` | Обновляет время доставки; `resolution="Ожидаемое время доставки: HH:MM"`, `task_status=waiting` | `delivery` | `case_code`, `params.time` |
| `close_case` | Закрывает кейс: `task_status=done`, `status=resolved` | любая | `case_code` |
| `create_case` | Создаёт новую карточку | новая | `params.category`, `params.title`, `params.employee`, `params.assignee` |

Синонимы, которые агент приводит к контрактным именам:
`create_task`, `new_case`, `add_case` → `create_case`.
`{"action":"unknown"}` обрабатывается явно и даёт `AI_UNKNOWN_MESSAGE`.

### 7.6 Создание задачи (`create_case`)

Пример интента:

```json
{
  "action": "create_case",
  "params": {
    "category": "onboarding",
    "title": "Оформление сотрудника Елена Аверина",
    "employee": "Елена Аверина",
    "assignee": "HR manager"
  }
}
```

`_execute_create_case()`:

- нормализует категорию: русские и английские слова (`оформлен`, `увольн`,
  `доставк`, `onboard`, `hire`, `dismiss`, `deliver`…) → `onboarding` /
  `offboarding` / `delivery`;
- если `title` не пришёл — строит его из шаблона (`Оформление сотрудника`,
  `Увольнение сотрудника`, `Доставка карты`) + имя сотрудника;
- извлекает сотрудника из `params.employee/full_name/fio/name` или regex
  `сотрудник(а|у|и) <Имя Фамилия>`;
- извлекает ответственного из `params.assignee/owner/responsible/role` или
  текста после последней запятой;
- вызывает `case_service.create_case()`.

Генерация кода: `S-N` для `onboarding`/`offboarding`, `P-N` для `delivery`.
Берётся максимум существующих номеров с этим префиксом + 1, формат двух цифр.
На демо-данных (`S-17`, `S-24`, `P-08`, `P-09`) получим `S-25` и `P-10`.

Новый кейс получает начальные значения:

| Поле | Значение |
|---|---|
| `status` | `open` |
| `crm_status` | `pending` |
| `task_status` | `open` |
| `assignee` | из команды или `NULL` |
| `resolution` | `NULL` |
| сообщение | `Задача создана через AI Command Bar: <title>. Сотрудник: <employee>.` (author = `AI Command Bar`, role = `support`) |

### 7.7 Regex-fallback

Если провайдер бросил `AIProviderError` или любое другое исключение,
`AIAgent._process_fallback()` вызывает детерминированный парсер
`_parse_regex()`. Порядок проверок важен:

1. **Создание**: `созда`, `создать`, `новая задача`, `новую задачу`,
   `новый кейс`, `create`, `добавь задачу` → `create_case`.
2. Увольнение: `увол`, `увольн`, `fire`, `dismiss`, `terminate`, `offboard`.
3. Оформление: `оформ`, `onboard`, `hire`, `register` или
   (`подтверд` + `документ`) → `confirm_docs`.
4. Доставка: `достав`, `deliver`, `задерж`, `delay`, `курьер`, `courier`,
   `приед`, `arriv`; при наличии `HH:MM` → `update_delivery`, иначе
   fallback-только действие `delay_delivery` (`task_status=waiting`,
   `delivery_delay_reported=true`, `resolution=Доставка задержана...`).
5. Закрытие: `закр`, `close`, `resolve`.

Код кейса ищется regex `\b([A-Za-z]+-\d+)\b`, время — `\b([01]?\d|2[0-3]):([0-5]\d)\b`.
Поиск кейса без кода идёт по категории и токенам имён (с учётом алиасов
`olga→ольга`, `ivan→иван`) в `title` и в поле `author` сообщений.

Если и fallback не распознал команду — возвращается **точный**
`FALLBACK_ERROR_MESSAGE`.

### 7.8 Особенности GigaChat

- Импорт SDK ленивый: приложение стартует и без пакета `gigachat` (уйдёт в fallback).
- Клиент: `from gigachat import GigaChatAsyncClient`.
- Используется root-метод `await client._achat(payload)`, а не `achat.create()`.
  Причина: в SDK `0.2.4` `achat.create()` принудительно уходит на
  `/v2/chat/completions` и получает `404 No such model` для модели `GigaChat`.
- Явно задан `GIGACHAT_BASE_URL=https://gigachat.devices.sberbank.ru/api/v1`.
- Тело запроса: строковый `content`, верхнеуровневый `temperature=0.0`,
  два сообщения (`system`, `user`), модель `GigaChat`.
- Парсятся оба формата ответа: v1 (`choices[0].message.content`) и v2
  (`messages[role=assistant].content[].text`).
- Markdown-обёртка ` ```json … ``` ` снимается регулярками; если её нет —
  просто `json.loads`. Не-JSON → `{"message": "<текст>"}`.
- Любое исключение SDK превращается в `AIProviderError`; ключ в тексте ошибки
  заменяется на `***`.
- Клиент создаётся и закрывается на каждый запрос (`aclose()` в `finally`).
- Прокси из `.env` подставляется в `os.environ` перед созданием httpx-клиента.
- Нужен root CA Минцифры; `GIGACHAT_VERIFY_SSL_CERTS=false` — только для отладки.
- Freemium: генерация в один поток, параллельные команды встают в очередь.

### 7.9 Резервный OpenRouter

`app/ai/openrouter_client.py` — прямой `httpx` POST на
`https://openrouter.ai/api/v1/chat/completions`. На тестовом сервере Cloudflare
возвращал `403 Access denied by security policy`, поэтому OpenRouter оставлен
только как backup. Поддерживает тот же контракт `generate_command_intent(...,
system_prompt=...)` и ту же очистку markdown.

### 7.10 Безопасность AI-слоя

- Ключ читается только из окрунения/`.env`.
- Логи не содержат ключ (`_safe_error()`).
- LLM не имеет прямого доступа к БД: он возвращает только интент.
- Действия применяются через `case_service`, где есть проверки категории и
  формата времени.
- `crm_status` и приоритет не могут быть изменены LLM.

---

## 8. CRUD карточек

### 8.1 Create

- UI: команда в AI Bar или быстрая команда в rail → `create_case`.
- API: `POST /api/cases`.
- Тело `CaseCreateRequest`:

```json
{
  "category": "onboarding",
  "title": "Оформление сотрудника Елена Аверина",
  "employee": "Елена Аверина",
  "assignee": "HR manager"
}
```

- Ответ `201` + `CaseRead`. `400`, если категория неизвестна или title пустой.
- Реализация: `case_service.create_case()` (см. §7.6).

### 8.2 Read

| Endpoint | Ответ |
|---|---|
| `GET /api/cases` | `list[CaseRead]`, сортировка `created_at DESC` |
| `GET /api/cases/{code}` | `CaseDetailRead` = `CaseRead` + `messages` |
| `GET /` | HTML-дашборд с `cases`, `tasks`, `case_extras`, `summary` |

`CaseRead` содержит вычисленные `priority`, `issues`, `has_problems`, а также
`task_status`, `assignee`, `resolution`, `updated_at`.

### 8.3 Update (ручное редактирование)

В каждой карточке есть кнопка **«Редактировать»**. Она раскрывает форму
`_case_edit.html` внутри `.case-details`. Поля:

| Поле HTML | Поле БД | Тип |
|---|---|---|
| Название задачи | `title` | text, required, ≤200 |
| Официальный статус (CRM) | `crm_status` | text, ≤64 |
| Ответственный | `assignee` | text, ≤64 |
| Статус кейса | `status` | select: `open` / `pending` / `resolved` |
| Статус задачи | `task_status` | select: `open` / `in_progress` / `waiting` / `done` |
| Решение | `resolution` | textarea, ≤2000 |

Кнопки: **Сохранить** (`PATCH`) и **Отмена** (просто скрывает форму).
При сохранении страница перезагружается. При ошибке текст показывается рядом
с кнопками в `data-edit-feedback`.

API:

```http
PATCH /api/cases/S-25
Content-Type: application/json

{
  "title": "Оформление Елены Авериной (исправлено)",
  "crm_status": "completed",
  "status": "resolved",
  "task_status": "done",
  "assignee": "Иван Иванов",
  "resolution": "Документы приняты"
}
```

Ответ `200` + обновлённый `CaseRead`. Меняются **только переданные** поля
(`model_dump(exclude_unset=True)`). `updated_at` обновляется через
`onupdate=func.now()`.

### 8.4 Действия (`POST /api/cases/{code}/action`)

Тело:

```json
{"action": "confirm_docs"}
```

или для доставки:

```json
{"action": "update_delivery", "delivery_time": "14:00"}
```

Допустимые значения `action` в этом эндпоинте:
`confirm_docs`, `confirm_offboarding`, `update_delivery`, `close`.

| Действие | Изменения | Сообщение |
|---|---|---|
| `confirm_docs` | `documents_confirmed=true`, `task_status=in_progress` | `Документы для кейса <code> подтверждены. Статус обновлен.` |
| `confirm_offboarding` | `official_confirmation_provided=true`, `task_status=in_progress` | `Увольнение <code> официально подтверждено руководителем.` |
| `update_delivery` | `task_status=waiting`, `resolution=Ожидаемое время доставки: HH:MM` | `Ожидаемое время доставки для <code> обновлено на HH:MM. Получатель предупрежден.` |
| `close` | `task_status=done`, `status=resolved` | `Кейс закрыт.` |

Коды ответов: `200` (успех), `400` (неверная категория/время),
`404` (кейс не найден), `422` (невалидное действие).

> Нюанс: при вызове через AI агент после `confirm_docs` / `confirm_offboarding`
> дополнительно выставляется `task_status=done` — подтверждение приравнивается
> к завершению работы. При нажатии кнопки в карточке остаётся `in_progress`.

### 8.5 Delete

- Крестик `×` (`.card-delete`, `data-delete`) в правой части каждой карточки.
- По клику: `window.confirm('Удалить кейс <code>? Это действие нельзя отменить.')`.
- При согласии: `DELETE /api/cases/<code>` → `204` → `location.reload()`.
- При отмене — ничего не происходит.
- Сервис `case_service.delete_case()` удаляет `CaseORM`; благодаря
  `cascade="all, delete-orphan"` удаляются сообщения и инциденты.
- Повторное удаление → `404`.
- Автоудаления закрытых кейсов нет.

---

## 9. Дашборд и UI

### 9.1 Раскладка

```text
┌────────────┬───────────────────────────────────────┬──────────────┐
│ Сайдбар    │ Topbar + основной контент              │ Rail         │
│ 240px      │  KPI-сетка (4 карточки)               │ 320px        │
│ Dashboard  │  Секция «Требует внимания»             │ быстрые      │
│ Integr.    │  Секция «Задачи»                       │ команды AI   │
│ Analytics  │                                       │              │
│ Settings   │                                       │              │
└────────────┴───────────────────────────────────────┴──────────────┘
                     ↓ плавающая AI Command Bar снизу
```

### 9.2 KPI

| Карточка | Поле `summary` | Подпись |
|---|---|---|
| Всего кейсов | `total_cases` | по всем источникам |
| Проблемных | `problematic_cases` | требуют действий |
| Решенных | `resolved_cases` | закрыто за период |
| Инцидентов | `total_issues` | выявлено правилами |

### 9.3 Секции карточек

1. **`id="cases"` — «Требует внимания»**. Показывает **все** кейсы из
   `list_cases()`. У каждой карточки: код, заголовок, категория, source-теги,
   priority-чип, status-чип,在三 раскрывающихся блоках — «Состояние CRM»,
   «Сведения из чата», «Расхождение».
2. **`id="tasks"` — «Задачи»**. Показывает `tasks = [c for c in cases if
   c.task_status != "done"]`. Это и есть место, где появляются новые карточки
   после `create_case`. Компактные карточки с тегами категории, статуса задачи
   и ответственного; при раскрытии — код, статусы, ответственный, CRM-статус,
   решение и кнопка закрытия.

`case_extras` (словарь по `code`) содержит `last_message`, подписанный
`delivery_status` и `resolution`.

### 9.4 Анатомия карточки

```html
<div class="action-card" role="button" tabindex="0" aria-expanded="false" data-code="S-25">
  <span class="action-code">S-25</span>
  <div class="action-main">
    <span class="action-title">Оформление сотрудника Елена Аверина</span>
    <span class="action-meta"><span class="tag">onboarding</span>…</span>
  </div>
  <span class="action-side">
    <span class="chip chip--low">low</span>
    <span class="chip chip--status">open</span>
    <button class="card-delete" data-delete aria-label="Удалить кейс S-25">×</button>
  </span>
  <div class="case-details" aria-hidden="true">
    <div class="case-details__grid">…три detail-block…</div>
    <div class="case-actions">
      <button data-edit>Редактировать</button>
      <button data-action="confirm_docs">Подтвердить документы</button>
      <button data-action="close">Закрыть кейс</button>
      <span class="action-feedback" role="status"></span>
    </div>
    {% include '_case_edit.html' %}
  </div>
</div>
```

### 9.5 JavaScript-поведение `dashboard.html`

Один делегированный `click`-обработчик на `document`:

| Клик | Действие |
|---|---|
| `[data-delete]` | confirm → `DELETE` → reload |
| `[data-edit]` | показать форму, `aria-hidden=false`, focus на первом поле, пересчёт `max-height` |
| `[data-edit-cancel]` | скрыть форму, пересчёт `max-height` |
| `[data-action]` | POST action → reload; для `update_delivery` берётся значение `input[type=time]` |
| внутри `.case-details` | не закрывать карточку |
| по остальной части карточки | toggle `is-open`, `aria-expanded`, `aria-hidden`, анимация |

Отдельный `submit`-обработчик сохраняет форму редактирования.
Отдельный `keydown` позволяет раскрывать карточку с клавиатуры (Enter/Space).
Быстрые команды подставляют текст в `#ai-command`.

### 9.6 Scroll-fix (важно не ломать)

В `app/static/style.css`:

```css
.app-body { min-height: 0; overflow: hidden; }
.main {
  padding: 28px 32px 140px;
  min-height: 0;
  overflow-y: auto;
  overflow-x: hidden;
  overscroll-behavior: contain;
}
```

`padding-bottom: 140px` — запас под плавающую AI-панель. В JS:

```js
function animateDetails(details, open) {
  details.style.maxHeight = '0px';
  requestAnimationFrame(() => {
    details.style.maxHeight = details.scrollHeight + 'px';
    requestAnimationFrame(() => cardScrollIntoView(details));
  });
}
function cardScrollIntoView(details) {
  details.closest('.action-card')
          .scrollIntoView({ behavior: 'smooth', block: 'start' });
}
```

Точная высота берётся из `scrollHeight`, а не из жёсткого `max-height: 1400px`.
На `resize` высота открытых блоков пересчитывается.

### 9.7 Быстрые команды в rail

Сейчас 7 шаблонов (клик подставляет текст в AI Bar):

1. `Создай задачу на оформление сотрудника Елена Аверина, HR manager`
2. `Подтверди документы S-17`
3. `Закрой S-24`
4. `Доставка P-08 13:30`
5. `Задержится P-08`
6. `Уволить S-24`
7. `Оформить S-17`

### 9.8 Остальные страницы

| Страница | Наполнение |
|---|---|
| `/integrations` | Список OpenRouter, **GigaChat**, hh.ru, Google Sheets, Telegram, CRM со статусом «Активно/Не подключено» (у GigaChat — «Настроить»); ключ в интерфейсе маскируется (`sk-...****`), у активной карточки с ключом есть «Изменить»; `********` в форме означает «оставить прежний ключ»; сохранение → `POST /integrations/{service}/save` → редирект 303 и reload; ключи в `data/integrations.json` в формате `{"telegram": {"api_key": "..."}}`. Из `.env`: GigaChat/Telegram/OpenRouter активны при непустом ключе, Google Sheets — при `GOOGLE_SHEETS_ENABLED=True` и непустом `GOOGLE_SHEETS_SPREADSHEET_ID`. У Google Sheets своё поле «ID таблицы»: ID маскируется (`1JdC0...ObTQ`), редактируется через `POST /integrations/google_sheets/save`, которое перезаписывает `GOOGLE_SHEETS_SPREADSHEET_ID` в `.env` (остальные строки сохраняются); подсказка про ссылку на таблицу и `data/google_credentials.json` |
| `/analytics` | KPI напрямую из БД: всего кейсов, проблемных, сообщений, расхождений; распределение по категориям с процентами; последние 5 сообщений |
| `/settings` | Профиль и параметры системы (статический шаблон) |
| `/docs` | Документация продукта в стиле Next Evolution UI: «О продукте», «Как пользоваться» (дашборд / AI Command Bar / Telegram), команды бота, Google Sheets и автовыгрузка, полезные ссылки, краткая архитектура. В сайдбаре — пункт «Документация» с иконкой книги; Swagger переехал на `/api/docs` |

---

## 10. полный справочник API

| Метод | Путь | Описание | Успех |
|---|---|---|---|
| GET | `/api/health` | Статус сервиса, `app_name`, `env`, `ai_enabled` | 200 |
| GET | `/api/cases` | Все кейсы | 200 `list[CaseRead]` |
| POST | `/api/cases` | Создать задачу | 201 `CaseRead` |
| GET | `/api/cases/{code}` | Детали + сообщения | 200 `CaseDetailRead` |
| PATCH | `/api/cases/{code}` | Ручное редактирование карточки | 200 `CaseRead` |
| DELETE | `/api/cases/{code}` | Удалить кейс с сообщениями | 204 (пусто) |
| POST | `/api/cases/{code}/action` | Выполнить действие | 200 JSON |
| POST | `/api/cases/{code}/messages` | Добавить сообщение | 201 `MessageRead` |
| GET | `/api/summary` | KPI и текстовое резюме | 200 `SummaryRead` |
| POST | `/api/ai/command` | Команда AI-агента | 200 JSON |
| POST | `/api/export/sheets` | Выгрузить все кейсы в Google Sheets | 200 `{"status":"ok","rows_exported":N}`; 503 при ошибке конфигурации/API |
| GET | `/` | Дашборд | 200 HTML |
| GET | `/integrations` | Интеграции | 200 HTML |
| GET | `/docs` | Документация продукта (описание, инструкции, ссылки, архитектура) | 200 HTML |
| GET | `/api/docs` | Swagger UI API (переехал с `/docs`) | 200 HTML |
| POST | `/integrations/{name}/save` | Сохранить ключ | 303 redirect на `/integrations` |
| POST | `/integrations/google_sheets/save` | Сохранить `GOOGLE_SHEETS_SPREADSHEET_ID` в `.env` | 303 redirect на `/integrations` |
| GET | `/analytics` | Аналитика | 200 HTML |
| GET | `/settings` | Настройки | 200 HTML |

> **Автовыгрузка в Google Sheets.** После успешного `POST /api/cases`, `PATCH /api/cases/{code}`, `DELETE /api/cases/{code}`, `POST /api/cases/{code}/action`, а также после успешной мутирующей команды `POST /api/ai/command` (в ответе есть поле `action`) вызывается `schedule_sheets_export()`. Он снимает актуальный список кейсов и добавляет `GoogleSheetsService().export_cases(cases)` в `BackgroundTasks`: ответ клиенту не ждёт вызова Google. Ошибки фоновой выгрузки логируются и не меняют код ответа CRUD. При `GOOGLE_SHEETS_ENABLED=False` триггеры не срабатывают. Ручной `POST /api/export/sheets` остаётся доступным и по-прежнему возвращает 503 при ошибке.

Пример ответа `/api/summary`:

```json
{
  "generated_at": "2026-11-18T19:05:17Z",
  "headline": "Всего кейсов: 4. Проблемных: 3. Решённых: 1. Выявлено инцидентов: 4. Требуют внимания руководителя: S-24, P-08.",
  "total_cases": 4,
  "problematic_cases": 3,
  "resolved_cases": 1,
  "total_issues": 4,
  "by_priority": {"medium": 1, "critical": 2, "low": 1},
  "by_status": {"open": 3, "resolved": 1},
  "highlights": ["S-24 — Увольнение (приоритет: critical)", "P-08 — Поставка карт (приоритет: critical)"],
  "problematic_codes": ["S-17", "S-24", "P-08"]
}
```

---

## 11. Telegram-бот

Файл: `app/bot/telegram_bot.py`. Запуск: `python -m app.bot.telegram_bot`.
Работает с той же SQLite-БД через `case_service` и `summary`.

### 11.1 Inline-меню и FSM (полный CRUD)

`/start` отправляет вежливое приветствие и Inline-клавиатуру:

| Кнопка | callback_data | Действие |
|---|---|---|
| `📊 Сводка` | `summary` | Та же сводка, что и `/summary` |
| `➕ Создать задачу` | `create_task` | FSM `CreateCase.waiting_data` |
| `📝 Управление кейсами` | `manage_cases` | Список кейсов кнопками `case_<code>` |

Карточка кейса содержит CRM-статус, проблему, ответственного и кнопки:
`✏️ Редактировать` (`edit_<code>`), `✅ Выполнить действие` (`action_<code>`),
`🗑 Удалить` (`delete_<code>`), `🔙 Назад` (`back_to_list`).

- Создание: ввод `Категория | Название | Сотрудник | Ответственный` →
  `case_service.create_case()` → возврат в главное меню.
- Редактирование: выбор поля «Название / Статус CRM / Ответственный / Решение»
  → FSM `EditCase.waiting_value` → `case_service.update_case()`.
- Действия: «Подтвердить документы / Подтвердить увольнение / Обновить доставку /
  Закрыть» → `case_service.apply_action()`. Для времени доставки используется
  FSM `DeliveryTime.waiting_time` и формат `ЧЧ:MM`.
- Удаление: запрос «Вы уверены?» → `case_service.delete_case()` → список кейсов.
- Любой сценарий прерывается кнопкой «❌ Отмена» (`cancel`) или `/cancel`.
  После завершения состояние FSM всегда сбрасывается.
- `Dispatcher(storage=MemoryStorage())` — хранилище состояний бота.

Резервные текстовые команды остаются рабочими:

| Команда | Что показывает |
|---|---|
| `/summary` | KPI (всего/проблемных/решённых), разбивка по процессам, критические кейсы с причиной, разделение «Подтверждено в CRM» / «Известно из чата» |
| `/onboarding`, `/offboarding`, `/delivery`, `/critical` | Отчёты по процессам |
| `/case <code>` | Детали: CRM, чат, расхождения |
| `/confirm_docs`, `/confirm_offboarding`, `/update_delivery`, `/close_case` | Управляющие действия |

Старая ReplyKeyboardMarkup (`📊 Сводка`, `📋 Оформление`, `🚪 Увольнение`,
`🚚 Доставка`, `⚠️ Критические`) сохранена как резерв для текстовых команд.
Сообщения оформляются в HTML: жирные заголовки, разделитель `---`, акценты
`🚨 / ⚠️ / ✅` только по делу, весь текст на русском языке в деловом стиле.
Если данных нет, бот отвечает «Нет активных кейсов в этой категории.».

Все CRUD-изменения бота дополнительно вызывают `GoogleSheetsService`, чтобы
содержимое таблицы оставалось актуальным и при изменении из Telegram.

Дополнительно: `telegram_summary.py` — разовая отправка сводки в
`TELEGRAM_CHAT_ID`. Токен хранится только в `.env`; без него бот выводит
предупреждение и завершается.

---

## 12. Тестирование

`pytest.ini`:

```ini
[pytest]
asyncio_mode = auto
asyncio_default_fixture_loop_scope = function
testpaths = tests
addopts = -v
```

Текущий результат: **60 passed**.

| Файл | Кол-во | Что проверяет |
|---|---|---|
| `tests/test_prioritization.py` | 9 | Правила `ONBOARDING_DOCS_UNCONFIRMED`, `OFFBOARDING_OFFICIAL_CONFIRMATION_MISSING`, `DELIVERY_DEADLINE_RISK`, `DELIVERY_CONFIRMED`, веса, пороги, `is_problematic` |
| `tests/test_create_case.py` | 6 | Генерация кода; запись create_case в БД + стартовое сообщение; AI-команда create_case; fallback create_case; ручное редактирование всех полей; удаление с каскадом и повторный 404 |
| `tests/test_integrations.py` | 17 | Файл ключей, вложенный формат, маскировка, sentinel `********`, GigaChat/Telegram/OpenRouter из `.env`/файла, Google Sheets по `.env`, маска и редактирование Spreadsheet ID, запись `.env`, бейджи |
| `tests/test_docs.py` | 4 | Страница `/docs`, активный пункт сайдбара, внешние ссылки, перенос Swagger на `/api/docs` и spec `/openapi.json` |
| `tests/test_search.py` | 5 | Живой поиск: подключение поля и `#cases-list`, регистронезависимый поиск по коду/title/category (включая кириллицу), пустое состояние с точным текстом, пустой запрос возвращает все кейсы |
| `tests/test_google_sheets.py` | 10 | Заголовки и строки, fallback пути credentials, ошибки конфигурации, `PermissionError()` без текста, POST `/api/export/sheets`, автовыгрузка после create/patch/delete/action/AI-команды, подавление фоновых ошибок |
| `tests/test_telegram_bot.py` | 9 | Inline-меню и клавиатуры CRUD, FSM-состояния, разбор ввода создания/редактирования, валидация времени доставки, приветствие, KPI/процессы/CRM-чат в `/summary`, акценты в отчётах, детали кейса, пустые категории |

Дополнительно выполнялись ручные проверки:

- `py_compile` всех изменённых модулей — OK.
- `node --check` всех `<script>` в `base.html` и `dashboard.html` — OK.
- TestClient: `/`, `/integrations`, `/analytics`, `/settings` → `200`; на каждой
  странице ровно по одному `id="ai-bar"`, `id="ai-command"`, `id="ai-toast"`,
  `id="ai-response"`.
- Дополнительные проверки: с подставным SDK и `httpx.MockTransport` подтверждено, что
  запрос уходит на `POST https://gigachat.devices.sberbank.ru/api/v1/chat/completions`
  со строковым `content`, верхнеуровневым `temperature=0.0`, моделью `GigaChat`,
  system+user и контекстом кейсов.
- E2E Check: `Создай задачу на оформление…` → `200` + `create_case для кейса S-01`;
  `PATCH` → `200` со всеми полями; `DELETE` → `204`, повтор → `404`; новая
  карточка и форма редактирования присутствуют в HTML дашборда.
- На живом сайте пользователь видел ответ GigaChat «Привет! Как могу помочь?»
  прямо в Command Bar.

---

## 13. Деплой и эксплуатация

### 13.1 Что копировать на сервер

Полный список файлов, менявшихся в ходе последнего этапа:

```text
app/main.py                         (первоначальная сборка)
app/config.py
app/database/models.py
app/database/session.py
app/schemas/case.py
app/schemas/message.py
app/schemas/summary.py
app/services/case_service.py
app/services/ai_agent.py
app/services/summary.py
app/services/prioritization.py
app/services/mock_data.py
app/services/integrations_store.py
app/services/google_sheets.py                  ← новый файл
app/ai/base.py
app/ai/__init__.py
app/ai/gigachat_client.py
app/ai/openrouter_client.py
app/api/routes.py
app/dashboard/routes.py
app/templates/base.html
app/templates/dashboard.html
app/templates/_case_edit.html        ← новый файл
app/templates/analytics.html
app/templates/integrations.html
app/templates/settings.html
app/static/style.css
app/bot/telegram_bot.py
data/mock_data.json
scripts/check_ai_command.py
telegram_summary.py
tests/test_prioritization.py
tests/test_create_case.py
tests/test_docs.py
tests/test_integrations.py
tests/test_google_sheets.py
tests/test_search.py
tests/test_telegram_bot.py
requirements.txt
.env.example
pytest.ini
SOLUTION.md
MVP.md
```

### 13.2 Команды

```bash
sudo systemctl restart russupport
sudo systemctl status russupport
sudo journalctl -u russupport -n 100 --no-pager
curl http://127.0.0.1:8001/api/health
```

### 13.3 Типовые неисправности

| Симптом | Причина / лечение |
|---|---|
| `AI временно недоступен…` | GigaChat недоступен и regex не распознал команду. Проверить `.env`, сеть, сертификат, логи |
| `404 No such model` | Запрос ушёл на `/v2`; убедиться, что `GIGACHAT_BASE_URL` = `.../api/v1` и используется `_achat()` |
| `ConnectTimeout` | Проверить unit: не должно быть `EnvironmentFile` с прокси; прокси задаётся в `.env` |
| `CERTIFICATE_VERIFY_FAILED` | Добавить root CA Минцифры в `certifi` либо задать `GIGACHAT_CA_BUNDLE_FILE` |
| Команда не создаёт карточку | Убедиться, что скопирован `app/services/ai_agent.py` с `create_case` и перезапущен сервис |
| Крестик/редактирование не работают | Проверить, что скопированы `app/api/routes.py`, `app/templates/dashboard.html`, `_case_edit.html`, `app/static/style.css` |
| Новая карточка не видна | Раздел «Задачи» скрывает `task_status=done`; проверить статус и сделать reload |

---

## 14. Ограничения и возможное развитие

| Ограничение | Комментарий |
|---|---|
| Нет авторизации | Любой пользователь может менять и удалять карточки |
| Ключи интеграций в файле | Для прода — Vault/ переменные окружения |
| `crm_status` — просто строка | Нет синхронизации с реальной CRM |
| Таблица `issues` пуста | Расхождения считаются на лету; можно материализовать |
| AI в один поток | Ограничение freemium GigaChat |
| Нет автоудаления закрытых кейсов | По требованию пользователя сделано только ручное удаление |
| Нет отдельной страницы кейса | Детали раскрываются inline (осознанное решение ТЗ) |
| SQLite | Для прода — PostgreSQL + Alembic |
| Нет `.gitignore`-проверки | Убедиться, что `.env` и `data/integrations.json` не попадают в репозиторий |

Возможные следующие шаги: аутентификация, real-time обновления без reload,
история изменений карточки (audit log), фильтры/поиск по кейсам, экспорт в CSV,
материализация issues, PostgreSQL.

---

## 15. История последних изменений (фиксация MVP)

| Этап | Что сделано |
|---|---|
| 1 | GigaChat подключён как основной провайдер; v1 + `_achat()`; устранён `404 No such model` и `ConnectTimeout` (удалён `EnvironmentFile` из systemd) |
| 2 | Системный промпт перенесён в `AIAgent` (`COMMAND_SYSTEM_PROMPT`); явная обработка `unknown`; снятие markdown-обёртки; обновлено сообщение `AI_UNKNOWN_MESSAGE` |
| 3 | Реализован `create_case`: генерация кода, запись в БД, команда «Создай задачу…», раздел «Задачи» на дашборде, `POST /api/cases` |
| 4 | Добавлено удаление: крестик с `confirm()`, `DELETE /api/cases/{code}`, каскад message/issues |
| 5 | Добавлено ручное редактирование: `_case_edit.html`, расширенный `CaseUpdate`, `PATCH`, кнопки Сохранить/Отмена |
| 6 | Зафиксирован этот документ |

---

## 16. Чек-лист приёмки MVP

- [ ] `pip install -r requirements.txt` выполняется
- [ ] `.env` создан из `.env.example`, реальные ключи не попали в код/доки
- [ ] `python -m pytest -q` → `60 passed`
- [ ] `python -m uvicorn app.main:app` поднимается без ошибок
- [ ] `/` открывается, KPI `4 / 3 / 1 / 4`
- [ ] Есть секции «Требует внимания» и «Задачи»
- [ ] Карточка раскрывается по клику без отдельной страницы
- [ ] AI Bar отвечает на приветствие текстом и **не** перезагружает страницу
- [ ] `Закрой S-24` → успех + reload
- [ ] `Создай задачу на оформление сотрудника Елена Аверина, HR manager` → новая карточка `S-25` в «Задачах»
- [ ] Крестик с подтверждением удаляет кейс; повтор → 404
- [ ] «Редактировать» меняет название/статусы/ответственного/решение
- [ ] При отключённом AI работает regex-fallback
- [ ] На сервере `systemctl restart russupport` и `/api/health` → `ok`

---

**Конец документа.**
