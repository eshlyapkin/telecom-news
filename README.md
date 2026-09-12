# Telecom News

Система мониторинга и агрегации новостей **прежде всего об SMS-индустрии** и технологиях мобильных сообщений.

## Описание

Проект для сбора, обработки и публикации новостей из различных источников (RSS-фиды, API; веб-скрапинг только там, где нет надёжного интерфейса) в области телекоммуникационной отрасли. Основной тематический приоритет — SMS/messaging-экосистема: A2P/P2A/P2P SMS, SMS-вендоры и messaging-платформы, агрегаторы, операторы/carriers в контексте SMS-бизнеса, SMS hubs, новые услуги/продукты, маршрутизация/доставка/безопасность/anti-fraud по SMS, партнёрства и сделки, затрагивающие SMS/messaging-бизнес, регуляторные изменения. **Общие телеком-новости без прямой связи с SMS/messaging не приоритетны и по умолчанию отфильтровываются.** Источники — русскоязычные и англоязычные.

## Возможности

- Сбор новостей из множества источников (63 источника в реестре, из них 59 включено)
- Дедупликация и фильтрация контента
- Категоризация по темам (5G, оптика, IoT, операторы, оборудование)
- Хранение истории публикаций
- Обработка локальной LLM через LM Studio
- Подготовка публикации и доставка в Telegram
- Публикация на нескольких языках (сейчас `ru`/`en`) — отдельный пост на каждый язык
- Бот с подписками: пользователь выбирает языки при подключении, меняет их в любой момент и может получать несколько языков одновременно
- CLI для ручного запуска pipeline, отладки и dry-run
- Multi-project registry (M9a): несколько Project в одном GUI/API; default = текущий SMS pipeline
- HTTP API + minimal GUI (FastAPI) — **опционально** (`pip install -e '.[api]'`), use case confirmed (D-021 / VISION §§40–70)

## Архитектура

```
┌──────────────────────────────────────────────────────────────┐
│                        CLI (MVP)                             │
├──────────────────────────────────────────────────────────────┤
│  Sources → Collect → Normalize → Store/Dedup                 │
│            → LLM (LM Studio) → Prepare Post → Telegram       │
└──────────────────────────────────────────────────────────────┘
        [HTTP API / FastAPI — опционально, не в MVP]
```

### Компоненты

1. **Collector** — модуль сбора данных из источников:
   - RSS-парсеры
   - API-клиенты
   - Веб-скраперы (при необходимости)

2. **Processor** — конвейер обработки:
   - Дедупликация (по URL, хешу контента)
   - Нормализация метаданных
   - Категоризация / тегирование
   - Оценка релевантности

3. **Storage** — слой хранения:
   - Основная БД (SQLite/PostgreSQL)
   - Кэш для часто запрашиваемых данных

4. **CLI** — интерфейс для MVP:
   - Ручной запуск pipeline (`python -m telecom_news`)
   - Отладка и dry-run
   - Служебные команды

> **HTTP API (FastAPI)** не входит в обязательный MVP. Целевая система на MVP — фоновый pipeline: источники → сбор → нормализация → хранение/дедупликация → LLM (LM Studio) → подготовка публикации → Telegram. FastAPI добавляется только при появлении подтверждённого use case для HTTP API.

## Структура проекта

Текущее состояние — **M4 Telegram Publishing реализован**: M1–M3 плюс Telegram Bot API, HTML-форматирование, `publish --dry-run`, retry и атомарный статус `published`. Живая отправка в канал требует `TELEGRAM_BOT_TOKEN` и `TELEGRAM_CHAT_ID`; без них проверяется через dry-run и моки.

```
telecom-news/
├── README.md
├── .gitignore
├── .editorconfig
├── pyproject.toml              # Конфигурация Python-проекта (src-layout; runtime: httpx, feedparser; dev: pytest, ruff)
├── .githooks/                  # pre-commit (diff--check + ruff), pre-push (pytest)
├── scripts/setup.sh            # One-shot dev setup: .venv + install + хуки
├── src/
│   └── telecom_news/
│       ├── __init__.py         # Версия пакета
│       ├── __main__.py         # python -m telecom_news
│       ├── cli.py              # CLI: collect, status, process, publish; run — заглушка до M5
│       ├── delivery/            # Telegram Bot API delivery
│       ├── config.py           # Конфигурация: пути, БД, LM Studio, log level, источники + env
│       ├── logging_config.py   # Настройка stdlib logging
│       ├── models.py           # Доменная модель Article
│       ├── collectors/         # base.py (RawItem, fetch+retry), rss.py (RSS-коллектор)
│       ├── llm/                # client.py (LM Studio HTTP-клиент)
│       ├── processors/         # normalize.py, dedup.py, relevance.py, summarize.py
│       └── storage/            # database.py (SQLite, CRUD, статусы)
├── tests/                      # pytest, unit/integration mocks (без реальной сети/продовой БД)
├── data/                       # Локальные данные (не в git): news.db
│   └── .gitkeep
└── docs/                       # Документация (ARCHITECTURE.md, ROADMAP.md, DEVELOPMENT.md и др.)
```

## Требования

- Python 3.10+
- SQLite — через stdlib `sqlite3`, БД создаётся автоматически
- Для `process`: запущенный LM Studio с загруженной моделью (см. docs/DEVELOPMENT.md)

## Быстрый старт

```bash
# Project-local окружение (системный Python не используется)
python3 -m venv .venv          # или: uv venv .venv
source .venv/bin/activate
pip install -e ".[dev]"         # dev-зависимости: pytest, ruff

# Проверка CLI (M5: run выполняет collect/process/publish)
python -m telecom_news --help
python -m telecom_news collect --source sinch-blog --limit 3
python -m telecom_news process --limit 3   # нужен запущенный LM Studio
python -m telecom_news publish --dry-run
python -m telecom_news run --dry-run --limit 3
python -m telecom_news doctor
python -m telecom_news status
python -m telecom_news diagnose        # почему ничего не публикуется (read-only)
python -m telecom_news sources            # каталог источников; --verify проверит ленты
python -m telecom_news deliver --dry-run   # предпросмотр рассылки подписчикам (M8)
python -m telecom_news bot --once          # обработать команды /start и /language (M8)
python -m telecom_news prune --dry-run     # старые статьи в очереди (D-020)
python -m telecom_news projects list       # multi-project registry (M9a)
python -m telecom_news projects dashboard

# Optional multi-project API + GUI (M9a; localhost, no auth)
pip install -e '.[api]'
python -m telecom_news serve               # http://127.0.0.1:8765/

# Тесты
pytest
```

> HTTP API/GUI — optional extra `.[api]` (D-021). Default bind is localhost; M9a has no authentication. Full multi-project vision: `docs/VISION_MULTI_PROJECT.md`.

## Лицензия

MIT
