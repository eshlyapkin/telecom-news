# ARCHITECTURE — telecom-news (MVP)

> Статус: planning. Документ завершает planning-стадию вместе с `ROADMAP.md`.
> Основной код ещё не написан (M0 не начат).

## 1. Цель системы

Автоматизированный ежедневный мониторинг и публикация новостей **прежде всего об SMS-индустрии** и технологиях мобильных сообщений: A2P/P2A/P2P, SMS-вендоры и messaging-платформы, агрегаторы, операторы/carriers в контексте SMS-бизнеса, SMS hubs, новые услуги/продукты, маршрутизация/доставка/фильтрация/безопасность/anti-fraud (если непосредственно про SMS), партнёрства и сделки, затрагивающие SMS/messaging-бизнес, регуляторные изменения, напрямую влияющие на SMS.

**Общие телеком-новости без прямой связи с SMS/messaging не приоритетны и по умолчанию отфильтровываются.** Источники — русскоязычные и англоязычные. Выбранные материалы обрабатываются локальной LLM через LM Studio, результат публикуется в Telegram-канале.

## 2. Целевой MVP-pipeline (один вертикальный сценарий)

```
источник → сбор → нормализация → storage/deduplication → LLM (LM Studio) → подготовка публикации → Telegram
                                                                                      ↑
                                                                        CLI + scheduler запускают pipeline
```

Простой вертикальный сценарий MVP: **один реальный источник → одна статья → нормализация → хранение → обработка → результат**. Всё остальное — масштабирование после MVP.

## 3. Компоненты

Формат описания каждого компонента: назначение / вход / выход / зависимости / ответственность / что НЕ делает.

### 3.1 Sources (источники новостей)

- **Назначение:** декларация реальных источников (RSS/API), из которых собираются новости SMS/messaging-экосистемы.
- **Вход:** конфигурация источника (URL, имя, язык, приоритет).
- **Выход:** список активных источников для collectors. В M6 включены четыре
  официальных RSS-источника: Sinch Blog, Twilio Blog, Infobip Blog и GSMA Newsroom.
- **Зависимости:** `config.py`.
- **Ответственность:** хранить параметры источника; включать/отключать источник; метки source health (M6/M7).
- **НЕ делает:** не скачивает и не парсит контент; не знает о pipeline.

### 3.2 Collectors (`collectors/`)

- **Назначение:** загрузка сырых материалов из источников. Первый источник выбирается в M1 после фактической проверки доступных вариантов: предпочтение структурированному официальному интерфейсу — `rss.py` или API-клиент (D-007, proposed); scraping только там, где нет RSS/API (M6).
- **Вход:** конфигурация одного источника; таймаут/лимиты.
- **Выход:** список сырых записей (`RawItem`: url, title, published_at, raw content/description, source_id, language hint).
- **Зависимости:** `httpx` (или stdlib urllib — решение фиксируется в M0/M1), `feedparser` или эквивалент для RSS.
- **Ответственность:** сетевой запрос с таймаутом и retry; парсинг; нормализация времени в UTC; передача ошибок наверх.
- **НЕ делает:** не хранит, не дедуплицирует, не определяет релевантность, не вызывает LLM. Ошибка одного источника не роняет весь pipeline (M6).

### 3.3 Normalization (`processors/normalize.py`)

- **Назначение:** превращение `RawItem` во внутреннюю модель `Article`.
- **Вход:** `RawItem`.
- **Выход:** `Article` (см. 3.4) с вычисленным `content_hash`.
- **Зависимости:** модель `Article`; утилиты хеширования/текста.
- **Ответственность:** нормализация URL (trim, removal tracking-параметров), текста (whitespace, длина), языка (ru/en по метке источника или эвристика), вычисление стабильного `content_hash`.
- **НЕ делает:** не обращается к сети и БД; не определяет релевантность.

### 3.4 Доменная модель Article (`src/telecom_news/models.py`)

Модель `Article` — доменная модель приложения (M0). Она **не зависит от persistence/storage слоя**: в M0 это dataclass без БД. Storage-specific модели и схема появятся отдельно в M2 (`storage/database.py`).

Поля (минимальный набор MVP):

| Поле | Описание |
|---|---|
| `id` | внутренний PK |
| `url` | нормализованный URL (кандидат в уникальный ключ) |
| `content_hash` | sha256 от нормализованного текста — стабильный идентификатор контента |
| `source_id`, `source_url` | источник |
| `title`, `summary_raw`, `body` | текст (оригинальный язык) |
| `language` | `ru` / `en` |
| `published_at` | UTC timestamp из источника |
| `fetched_at` | момент сбора, UTC |
| `status` | `new → processed → published` (+ `skipped`, `error`) |
| `relevance` | `relevant` / `irrelevant` (результат LLM) |
| `category` | тип новости: technology / vendor / aggregator / carrier / product_service / partnership / ma_investment / security_antifraud / regulation |
| `llm_result` | JSON-структура результата LLM (summary, перевод и т.д.) |
| `published_at` | момент публикации в Telegram |

### 3.5 Storage (`storage/database.py`)

- **Назначение:** персистентность статей и состояния обработки. MVP: **SQLite** (D-003), stdlib `sqlite3`; абстракция драйвера позволяет позже переключиться на PostgreSQL (non-goal для MVP).
- **Вход/Выход:** CRUD по `Article`: `upsert_by_hash`, `get_unprocessed`, `set_status`, `mark_published`; health по источникам через `record_source_health`/`source_health`.
- **Зависимости:** доменная модель `Article` (`src/telecom_news/models.py`); путь к БД из конфигурации (`data/news.db`).
- **Ответственность:** схема БД (создание таблиц при старте); идемпотентное сохранение; атомарные переходы статусов.
- **НЕ делает:** не дедуплицирует «на глаз», не вызывает LLM, не публикует.

### 3.6 Deduplication (`processors/dedup.py`)

- **Назначение:** предотвращение повторной обработки и публикации.
- **Вход:** `Article` (с `content_hash`, нормализованным `url`).
- **Выход:** флаг «новая / дубликат» + сохранённая запись.
- **Зависимости:** storage.
- **Ответственность:** проверка по `content_hash` (основной ключ) и по нормализованному URL; повторный запуск pipeline не обрабатывает и не публикует уже обработанные статьи.
- **НЕ делает:** семантическую дедупликацию (non-goal для MVP).

### 3.7 Relevance & Classification (`processors/relevance.py`)

- **Назначение:** определение релевантности именно SMS/messaging-тематике и классификация типа новости.
- **Вход:** `Article` (title + текст, язык).
- **Выход:** `{relevant: bool, category: str|None}` — часть `llm_result`.
- **Зависимости:** LLM client (3.8).
- **Ответственность:** промпт с явным критерием «только SMS/messaging-экосистема; общие телеком-новости без связи с SMS → irrelevant»; категории: technology, vendor, aggregator, carrier, product_service, partnership, ma_investment, security_antifraud, regulation.
- **НЕ делает:** суммаризацию и перевод (отдельные шаги 3.9); не публикует.

### 3.8 LLM Integration — LM Studio (`llm/client.py`)

- **Назначение:** доступ к локальной модели через HTTP API LM Studio (OpenAI-совместимый endpoint, по умолчанию `http://localhost:1234/v1`).
- **Вход:** промпт + параметры; выход: текст/JSON ответа.
- **Зависимости:** `httpx`; конфигурация (base_url, model name, timeout).
- **Ответственность:** единая точка вызова LLM для всех процессоров; таймауты и retry с backoff; обработка недоступности LM Studio как явной ошибки (`LLMUnavailableError`) — pipeline не падает, статья помечается `error`/остаётся `new`.
- **НЕ делает:** бизнес-логику (релевантность/суммаризация — в 3.7/3.9); не хранит результаты сама.

### 3.9 Summarization & Translation (`processors/summarize.py`)

- **Назначение:** краткое саммари на целевом языке публикации; перевод RU ↔ EN при необходимости (например, англоязычная новость → русское саммари для канала).
- **Вход:** `Article` + результат релевантности.
- **Выход:** `{summary: str, summary_language: str}` в `llm_result`.
- **Зависимости:** LLM client.
- **Ответственность:** суммаризация только релевантных статей; перевод при расхождении языка источника и целевого языка канала (настраивается).
- **НЕ делает:** публикацию; не обрабатывает нерелевантные статьи.

### 3.10 Telegram Publishing (`delivery/telegram.py`)

- **Назначение:** отправка подготовленной публикации в Telegram-канал через Bot API.
- **Вход:** `Article` с заполненным `llm_result`; токен бота и chat_id из конфигурации (env).
- **Выход:** сообщение в канале; переход статьи в статус `published`.
- **Зависимости:** `httpx`; Telegram Bot API (`sendMessage`, Markdown/HTML формат); storage.
- **Ответственность:** формат публикации (заголовок, саммари, категория, ссылка, язык); dry-run/preview (печатает текст вместо отправки); защита от повторной публикации — публикация только статей со статусом `processed` и перевод в `published` атомарно; retry при временных ошибках API.
- **НЕ делает:** генерацию контента; не меняет релевантность/категорию.

### 3.11 CLI (`main.py`, `python -m telecom_news`)

- **Назначение:** единственный интерфейс MVP (D-004).
- **Команды (MVP):**
  - `run` — полный pipeline по всем enabled-источникам: collect → normalize → store/dedup → LLM → publish; флаги `--source <id>` (опционально ограничить одним источником), `--dry-run` (без отправки в Telegram), `--limit N`.
  - `collect` / `process` / `publish` — отдельные этапы для отладки.
  - `status` — счётчики по статусам статей, health источников.
- **Вход:** аргументы командной строки (argparse/stdlib).
- **Выход:** человекочитаемый лог + exit code (0 = успех, ≠0 = сбой; конкретные коды фиксируются в M5).
- **Зависимости:** все слои выше.
- **НЕ делает:** фоновую работу и расписание напрямую (это scheduler, 3.12); не является HTTP-сервером.

### 3.12 Scheduler (`scheduler.py`)

- **Назначение:** автоматический запуск pipeline по расписанию (M5). MVP-подход: простая встроенная петля/планировщик на stdlib или системный cron, вызывающий `python -m telecom_news run`.
- **Вход:** интервал из конфигурации.
- **Выход:** периодические запуски pipeline с логированием.
- **Зависимости:** CLI/pipeline; logging.
- **НЕ делает:** distributed workers, очереди (non-goal).

### 3.13 Logging (`logging.py` / stdlib `logging`)

- **Назначение:** единый структурированный лог всех этапов.
- **Формат:** stdlib `logging`, консоль + файл в `data/logs/`; уровень из конфигурации; для каждого этапа — source_id, article hash, статус.
- **НЕ делает:** мониторинг/alerting (M7).

### 3.14 Retries и обработка ошибок

- Сетевые ошибки collectors: retry с экспоненциальным backoff (2–3 попытки), затем ошибка источника логируется; **один проблемный источник не роняет pipeline** (M6).
- LM Studio недоступна: `LLMUnavailableError`, статья остаётся в статусе `new`/помечается `error`, повторная обработка на следующем запуске.
- Telegram API: retry при 429/5xx с учётом `retry_after`; неуспех публикации не меняет статус статьи (повторится позже).
- Временные ошибки не должны приводить к дублированию публикаций — защита через статусы в БД (3.6, 3.10).

## 4. Целевая структура пакетов (MVP)

```
src/telecom_news/
├── __init__.py
├── main.py            # CLI: run / collect / process / publish / status
├── config.py          # конфигурация: источники, LM Studio, Telegram (env), пути
├── logging_setup.py   # настройка логирования
├── models.py          # доменная модель Article (M0; без зависимости от storage)
├── collectors/
│   ├── __init__.py
│   ├── base.py        # RawItem + базовый коллектор (retry, таймауты)
│   ├── rss.py         # RSS-коллектор (кандидат на первый источник, D-007)
│   └── api.py         # API-клиент (альтернатива; выбор в M1)
├── processors/
│   ├── __init__.py
│   ├── normalize.py   # RawItem → Article
│   ├── dedup.py       # дедупликация по content_hash / URL
│   ├── relevance.py   # релевантность SMS/messaging + категория (LLM)
│   └── summarize.py   # саммари + перевод RU↔EN (LLM)
├── llm/
│   ├── __init__.py
│   └── client.py      # LM Studio HTTP client, retry, LLMUnavailableError
├── storage/
│   ├── __init__.py
│   └── database.py    # SQLite (stdlib sqlite3), схема, CRUD; storage-specific модели (M2)
├── delivery/
│   ├── __init__.py
│   └── telegram.py    # Telegram Bot API, dry-run, защита от повторной публикации
└── scheduler.py       # автоматический запуск (M5)

tests/                 # pytest: unit + smoke; фикстуры вместо реальных LLM/Telegram
data/                  # news.db, logs/ — не в git
```

Каталог `api/` **не создаётся** до подтверждения FastAPI (D-004). Существующий пустой каталог `src/telecom_news/api/` удаляется при M0.

## 5. Ключевые потоки данных

1. `collect`: source config → HTTP/RSS → `RawItem[]`.
2. `normalize`: `RawItem` → `Article` (hash, URL, язык, UTC).
3. `store/dedup`: `Article` → SQLite; дубликаты по hash/URL пропускаются.
4. `process` (LLM): статьи со статусом `new` → relevance+category → (если relevant) summary/translation → `llm_result`, статус `processed`.
5. `publish`: статьи `processed` и не `published` → Telegram → статус `published`.

Повторный запуск любого этапа идемпотентен благодаря статусам в БД.

## 6. Конфигурация

- `config.py`: значения по умолчанию + переопределение через env:
  - `TELECOM_NEWS_DB` (путь к SQLite, по умолчанию `data/news.db`)
  - `LMSTUDIO_BASE_URL` (по умолчанию `http://localhost:1234/v1`), `LMSTUDIO_MODEL`
  - `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`
  - `LOG_LEVEL`
- Источники — декларативный список в конфигурации (id, type=rss/api, url, language, enabled).

## 7. Non-goals для MVP (только возможное развитие после MVP)

| Компонент | Статус | Когда рассматривать |
|---|---|---|
| FastAPI / HTTP API | deferred (D-004) | только при подтверждённом use case |
| Web UI | non-goal | после MVP, при необходимости |
| PostgreSQL | non-goal для MVP | при росте данных/конкурентности; storage уже абстрагирует драйвер |
| Redis / очереди | non-goal | при distributed workers |
| Docker/Kubernetes | non-goal | при деплое на сервер |
| Distributed workers | non-goal | при превышении производительности одного процесса |
| Семантическая дедупликация (embeddings) | non-goal для MVP | после MVP, если дубли между источниками станут проблемой |
| Scraping сайтов с доступным RSS/API | non-goal | только там, где надёжного интерфейса нет (M6) |
| Аналитика Telegram-канала | non-goal | после MVP |
| Пользовательские аккаунты / authentication | non-goal | вместе с FastAPI, если появится |

## 8. Соответствие решениям DECISIONS.md

- D-001 (Python 3.10+, src-layout) — структура разделов 4.
- D-002 (Collectors → Processors → Storage) — разделы 3.2–3.6; добавлены `llm/`, `delivery/` как отдельные слои pipeline (расширение, не противоречие).
- D-003 (SQLite по умолчанию) — раздел 3.5.
- D-004 (CLI для MVP, FastAPI deferred) — разделы 3.11, 7; каталог `api/` не создаётся.
- D-007 (предпочтение структурированному официальному интерфейсу; RSS или API) — раздел 3.2: статус `proposed`; конкретный механизм первого источника определяется в M1 после фактической проверки.
- D-008 (Telegram delivery) — раздел 3.10: статус **accepted**; реализация в M4.
