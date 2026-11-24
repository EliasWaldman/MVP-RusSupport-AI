# RusSupport AI — решение(test-assignment)

## 1. Промпт AI-ассистента

```text
Ты — AI-ассистент. Если пользователь здоровается — ответь текстом. Если просит действие — верни JSON: {action: '...', case_code: '...', params: {...}}. Действия: confirm_docs, confirm_offboarding, update_delivery, close_case.
```

Для текстового ответа модель возвращает `{"message": "..."}`. В system-message
передаётся JSON-контекст кейсов (`code`, `title`, `category`, `status`).

Промпт живёт в `app/services/ai_agent.py` (`COMMAND_SYSTEM_PROMPT`) и передаётся
в провайдер через необязательный аргумент `system_prompt`. Клиент использует официальный SDK
`from gigachat import GigaChatAsyncClient`, модель `GigaChat` и root-метод
`await client._achat(...)` (с запасными вариантами для старых SDK). Запрос идёт
на v1-эндпоинт `GIGACHAT_BASE_URL=https://gigachat.devices.sberbank.ru/api/v1`.
Ключ читается из `GIGACHAT_CREDENTIALS`, scope — `GIGACHAT_API_PERS`. Активный
провайдер выбирается настройкой `AI_PROVIDER=gigachat` в `app/ai/__init__.py`.
Любое исключение SDK — авторизация, таймаут, сеть, отсутствующий сертификат
Минцифры (`CERTIFICATE_VERIFY_FAILED`) — превращается в `AIProviderError`, после
чего `app/services/ai_agent.py` использует regex-fallback.

### Важно: почему `_achat()` и `/api/v1`, а не `achat.create()`

SDK `gigachat==0.2.4` в методе `achat.create()` принудительно переписывает
любой versioned `base_url` на `/v2/chat/completions` (`resolve_primary_chat_url()`)
и формирует тело с `content: [{text: ...}]` и вложенным `model_options.temperature`.
Для тарифа с моделью `GigaChat` этот v2-путь отвечал:

```text
GigaChat: ошибка запроса: 404 https://api.giga.chat/v2/chat/completions:
{"status":404,"message":"No such model",...}
```

При этом тот же ключ через `curl` на `https://gigachat.devices.sberbank.ru/api/v1/chat/completions`
с обычным строковым `content` и верхнеуровневым `temperature` отвечал корректно.
Root-метод `_achat()` использует именно такую форму запроса и через `httpx`
склеивает относительный `/chat/completions` с `base_url`, получая верный адрес
`https://gigachat.devices.sberbank.ru/api/v1/chat/completions`. Переключение на
него вместе с явным `GIGACHAT_BASE_URL` устраняет `404 No such model`.

На Linux корневой сертификат Минцифры нужно установить заранее:

```bash
cd /var/www/russupport-ai
source .venv/bin/activate
curl -k "https://gu-st.ru/content/lending/russian_trusted_root_ca_pem.crt" -w "\n" >> $(python -m certifi)
pip install -r requirements.txt
```

Альтернатива — указать путь к сертификату в `GIGACHAT_CA_BUNDLE_FILE`.
Отключать проверку TLS (`GIGACHAT_VERIFY_SSL_CERTS=false`) можно только для
локальной отладки.

Freemium-лимит GigaChat: 365 млн токенов на 12 месяцев, но генерация идёт в
один поток — параллельные команды становятся в очередь. Для демо этого достаточно.

---

## 2. Скрипт запуска и проверки

```powershell
cd D:\EliasWaldman\work\graf-magic.ru\test
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env

# Basic smoke
python -m pytest -q
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload

# Telegram-бот (опционально, токен только в .env)
python -m app.bot.telegram_bot

# Разовая отправка сводки в Telegram (опционально)
python telegram_summary.py
```

Проверка Command Bar через API:

```powershell
Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/ai/command" `
  -Method Post -ContentType "application/json" `
  -Body '{"command":"Подтверди документы S-17"}' | ConvertTo-Json
```

Отдельный скрипт проверки (сервер должен быть запущен):

```powershell
python scripts/check_ai_command.py
python scripts/check_ai_command.py "Закрой S-24"
```

Для тестовой команды `"Курьер задержится до 15:00 для P-08"` LLM должна вернуть интент:

```json
{"action":"update_delivery","case_code":"P-08","params":{"time":"15:00"}}
```

Ожидаемый успешный ответ API после действия:

```json
{"status":"ok","message":"Действие выполнено: update_delivery для кейса P-08","action":"update_delivery"}
```

Ответ на приветствие (перезагрузки страницы нет):

```json
{"status":"ok","message":"Привет! Чем могу помочь?"}
```

Если GigaChat недоступен и regex-парсер тоже не справился:

```json
{"status":"error","message":"AI временно недоступен, но я не понял команду. Попробуйте: 'Закрой S-24'"}
```

Любой `message` показывается в блоке `#ai-response` над командной строкой;
ошибки подсвечиваются красным. После действий страница перезагружается через 1.5 c.

### Создание новых задач (`create_case`)

Команда `Создай задачу на оформление сотрудника Елена Аверина, HR manager`
разбирается LLM в интент:

```json
{"action":"create_case","params":{"category":"onboarding","title":"Оформление сотрудника Елена Аверина","employee":"Елена Аверина","assignee":"HR manager"}}
```

`case_service.create_case()` генерирует свободный код (`S-25` для кадровых задач,
`P-10` для доставки), создаёт кейс и стартовое сообщение в БД. Ответ агента:

```json
{"status":"ok","message":"Действие выполнено: create_case для кейса S-25","action":"create_case","case_code":"S-25"}
```

Новая карточка появляется в разделе «Задачи» дашборда (все незакрытые кейсы).
Тот же путь работает через regex-fallback, если GigaChat недоступен. Создание
доступно и напрямую: `POST /api/cases` с телом `category/title/employee/assignee`.

---

## 3. Сводка по 4 демо-кейсам

### S-17 — Оформление сотрудника, onboarding, medium

- **CRM-подтверждено:** статус `open`, `crm_status=pending`, первый рабочий день завтра, документы ожидаются, официального подтверждения документов в CRM нет. Rule `ONBOARDING_DOCS_UNCONFIRMED` → warning, source `crm`.
- **Только из чата:** Иван Иванов сообщил, что отправил фотографии документов руководителю в личные сообщения. Это не заменяет подтверждение в CRM.

### S-24 — Увольнение, offboarding, critical

- **CRM-подтверждено:** последний рабочий день сегодня, `official_confirmation_provided=false`, `crm_status=pending`. Rule `OFFBOARDING_OFFICIAL_CONFIRMATION_MISSING` → critical, source `crm`.
- **Только из чата:** Ольга Петрова написала, что вопрос обсуждён с руководителем. Утверждение сотрудника в чате не является официальным подтверждением.

### P-08 — Поставка карт, delivery, critical

- **CRM-подтверждено:** доставка в пути (`in_transit`), дедлайн 2026-01-03, приёмка не подтверждена, зафиксирован риск срыва срока. Rule `DELIVERY_DEADLINE_RISK` → critical, source `chat` (сигнал о задержке пришёл из переписки).
- **Только из чата:** курьер сообщил о задержке и ориентировочном прибытии не раньше 13:00. Точное время — chat-only факт, в схеме хранится отдельно от date-only `delivery_eta`.

### P-09 — Поставка карт, delivery, low

- **CRM-подтверждено:** приёмка подтверждена, `delivery_status=accepted`, `delivery_confirmed=true`, `crm_status=resolved`, статус кейса `resolved`. Rule `DELIVERY_CONFIRMED` → info, source `crm`.
- **Только из чата:** получатель подтвердил получение и проверку количества. Это дополнительное свидетельство, а не основной источник статуса.

Исходные KPI после первой загрузки: `4` кейса, `3` проблемных, `1` решённый, `4` инцидента. Критические: `S-24`, `P-08`; medium: `S-17`; low: `P-09`.

---

## 4. Описание первого запуска

После установки зависимостей и копирования `.env.example` в `.env` приложение запускается одной командой `uvicorn app.main:app --reload`. При старте автоматически создаётся SQLite-база и идемпотентно загружаются 4 демонстрационных кейса из `data/mock_data.json`. Главная страница показывает KPI `4 / 3 / 1 / 4`, карточки «Требует внимания» и раздел «Задачи» со всеми незакрытыми кейсами; карточка раскрывается прямо на дашборде без отдельной страницы. Плавающая AI Command Bar доступна на всех четырёх разделах: Dashboard, Integrations, Analytics, Settings. В правой колонке дашборда расположены быстрые команды: по клику нужная команда подставляется в поле ввода, пользователю остаётся нажать Enter. Команды сначала разбираются LLM через GigaChat, при недоступности API — regex-парсером, затем меняют рабочие поля через общий `case_service`. Ответ ассистента появляется в блоке `#ai-response`: действия обновляют данные и перезагружают страницу через 1.5 c, текстовые ответы остаются на экране. При новом вводе старый ответ очищается. Telegram-бот запускается отдельно и работает с той же базой; токен хранится только в локальном `.env`.

---

## 5. Проверено

- `python -m pytest -q` → **9 passed**.
- `python -m py_compile app/api/routes.py app/services/ai_agent.py app/ai/gigachat_client.py app/ai/__init__.py app/ai/openrouter_client.py app/config.py` → успешно.
- TestClient: `/`, `/integrations`, `/analytics`, `/settings` → `200`, на каждой странице ровно по одному `id="ai-bar"`, `id="ai-command"`, `id="ai-toast"`, `id="ai-response"`.
- Все `<script>`-блоки `base.html` и `dashboard.html` прошли `node --check`.
- С подставным SDK и `httpx.MockTransport` проверено: реальный запрос уходит `POST https://gigachat.devices.sberbank.ru/api/v1/chat/completions` с заголовком `Authorization: Bearer ...`, телом со строковым `content` и верхнеуровневым `temperature=0.0`, моделью `GigaChat`, двумя сообщениями (system + user) и контекстом кейсов в системном промпте. Ответ в формате v1 (`choices[0].message.content`) и v2 (`messages[role=assistant].content[].text`) разбирается одинаково.
- JSON-ответ модели возвращается как словарь интента; обёртка ```json ... ``` снимается; произвольный текст нормализуется в `{"message": "..."}`.
- Любое исключение SDK (включая `SSLCertVerificationError`) и ошибка авторизации превращаются в `AIProviderError`; ключ авторизации в тексте ошибки заменяется на `***`.
- `AI_PROVIDER=gigachat` → `get_provider()` возвращает `GigaChatClient`; `AI_ENABLED=false` → `MockProvider`, а `AIAgent` уходит в regex-fallback.
- С подставным провайдером: действие → `Действие выполнено: update_delivery для кейса P-08` + поле `action`; приветствие → `Привет! Чем могу помочь?` без поля `action`; отказ API + нераспознанная команда → точный текст fallback-ошибки; отказ API + `Закрой S-24` → успех через regex.
- End-to-end через `POST /api/ai/command` (fallback-путь): `Закрой S-24` → `200` и `Действие выполнено: close_case для кейса S-24`; нераспознанная фраза → `200` и точный текст fallback-ошибки.
- `POST /api/cases/{code}/action`: `200` при успехе, `404` для неизвестного кода, `422` для невалидного действия.
- `DELETE /api/cases/{code}`: `204` при удалении (крестик на карточке с подтверждением), `404` для неизвестного кода; вместе с кейсом удаляются сообщения и инциденты. Автоматического удаления закрытых кейсов пока нет — раздел «Задачи» просто скрывает задачи со статусом `done`.
- `PATCH /api/cases/{code}`: `200` — ручное редактирование карточки кнопкой «Редактировать»: название, официальный статус CRM, статус кейса, статус задачи, ответственный и решение. Меняются только переданные поля; приоритет пересчитывается из сигналов. CRM-статус может изменить только человек вручную — AI и правила его не перезаписывают.
- Telegram-бота команды `/start`, `/summary`, `/critical`, `/case`, `/onboarding`, `/offboarding`, `/delivery`, `/confirm_docs`, `/confirm_offboarding`, `/update_delivery`, `/close_case` проверены на демо-данных; секретный токен в документацию не включён.

---

## 6. Почему OpenRouter заменён на GigaChat

На сервере `/var/www/russupport-ai` OpenRouter отвечал `HTTP 403` с телом
`{"success": false, "error": "Access denied by security policy."}` и заголовками
`Server: cloudflare` / `CF-RAY`. Запрос отклонялся на входе, до проверки ключа:
одинаковый результат с валидным и фиктивным ключом это подтверждал. Замена ключа
или прокси проблему не решала, поэтому основной провайдер изменён на GigaChat.

Клиент OpenRouter оставлен в репозитории как запасной (`AI_PROVIDER=openrouter`),
но основным теперь считается `gigachat`. Переключатель находится в
`app/ai/__init__.py`, агент получает провайдер через `get_provider()`.

**Важно:** ключ OpenRouter публиковался в переписке и считается
скомпрометированным — его нужно отозвать, удалить из `.env` и почистить историю
команд на сервере (`history -c`, зачистка `~/.bash_history`).

### Чек-лист развёртывания на сервере

```bash
cd /var/www/russupport-ai
source .venv/bin/activate
curl -k "https://gu-st.ru/content/lending/russian_trusted_root_ca_pem.crt" -w "\n" >> $(python -m certifi)
pip install -r requirements.txt
# скопировать обновлённые app/config.py и app/ai/gigachat_client.py
# в .env должны быть:
#   AI_PROVIDER=gigachat
#   GIGACHAT_MODEL=GigaChat
#   GIGACHAT_BASE_URL=https://gigachat.devices.sberbank.ru/api/v1
#   GIGACHAT_SCOPE=GIGACHAT_API_PERS
#   GIGACHAT_CREDENTIALS=<ключ авторизации без префикса Basic и кавычек>
systemctl restart russupport
```

Быстрая проверка API:

```bash
curl -s -X POST http://127.0.0.1:8001/api/ai/command \
  -H 'Content-Type: application/json' \
  -d '{"command":"Привет"}'
# ожидается: {"status":"ok","message":"..."}
```

Проверка в интерфейсе: ввести «Привет» в AI Command Bar. Ожидается текстовый
ответ GigaChat без перезагрузки страницы. Если вместо ответа точный текст
`AI временно недоступен, но я не понял команду...` — значит SDK выбросил
исключение и сработал fallback; смотреть `journalctl -u russupport -n 100`.

Типичные причины падения: не установлен root CA Минцифры, пустой или неверный
`GIGACHAT_CREDENTIALS`, не тот `GIGACHAT_SCOPE`, отсутствует пакет `gigachat`,
а также использование старого v2-пути (`404 No such model`) вместо `GIGACHAT_BASE_URL=.../api/v1`.

---

## 7. Ожидает / не включено

- Реальный вызов GigaChat настроен через `AI_PROVIDER=gigachat`, модель `GigaChat`, v1-эндпоинт и ключ из `.env`; root CA Минцифры установлен на сервере. Если ключ не задан или API недоступен — работает regex-fallback. Приоритеты по-прежнему считаются детерминированными правилами, LLM их не меняет. Freemium-генерация идёт в один поток — параллельные запросы встают в очередь.
- Реальной интеграции с CRM нет: действия имитируют изменения рабочих полей локально; официальные CRM-статусы не перезаписываются.
- Нет аутентификации и авторизации; SQLite и автозагрузка демо-данных предназначены только для демонстрации.
- Нет миграций Alembic и production-БД (PostgreSQL).
- Старые заглушки `app/bot/handlers.py`, `app/bot/keyboards.py` не используются; рабочая логика бота — в `app/bot/telegram_bot.py`.
- Токен Telegram и ключи интеграций хранятся только в локальных `.env` / `data/integrations.json` и в репозиторий не попадают.
