# Telecom News

Система мониторинга и агрегации новостей **прежде всего об SMS-индустрии** и технологиях мобильных сообщений.

## Описание

Проект для сбора, обработки и публикации новостей из различных источников (RSS-фиды, API; веб-скрапинг только там, где нет надёжного интерфейса) в области телекоммуникационной отрасли. Основной тематический приоритет — SMS/messaging-экосистема: A2P/P2A/P2P SMS, SMS-вендоры и messaging-платформы, агрегаторы, операторы/carriers в контексте SMS-бизнеса, SMS hubs, новые услуги/продукты, маршрутизация/доставка/безопасность/anti-fraud по SMS, партнёрства и сделки, затрагивающие SMS/messaging-бизнес, регуляторные изменения. **Общие телеком-новости без прямой связи с SMS/messaging не приоритетны и по умолчанию отфильтровываются.** Источники — русскоязычные и англоязычные.

## Возможности

- Сбор новостей из реестра источников: RSS/Atom-ленты и карты сайта для изданий без ленты
- Дедупликация и фильтрация контента
- Категоризация по темам (5G, оптика, IoT, операторы, оборудование)
- Хранение истории публикаций
- Обработка локальной LLM через LM Studio
- Подготовка публикации и доставка в Telegram
- Публикация на нескольких языках (сейчас `ru`/`en`) — отдельный пост на каждый язык
- Бот с подписками: пользователь выбирает языки при подключении, меняет их в любой момент и может получать несколько языков одновременно
- CLI для ручного запуска pipeline, отладки и dry-run
- Multi-project registry (M9a): несколько Project в одном GUI/API; default = текущий SMS pipeline
- HTTP API + панель управления (FastAPI) — **опционально** (`pip install -e '.[api]'`), use case confirmed (D-021 / VISION §§40–70)
- Панель: паузы, очередь, опубликованные посты с предпросмотром, добавление/удаление источников,
  языки канала на лету, редактор правил релевантности, запуск цикла вручную
- Автопоиск новых источников: темы берутся из правил релевантности, кандидаты предлагаются
  оператору с историей проверок; в пайплайн ничего не попадает без явного подтверждения

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

> **HTTP API (FastAPI)** не входил в обязательный MVP и остаётся опциональным extra
> (`pip install -e '.[api]'`). Use case подтверждён в D-021, панель реализована в M9a–M11
> и работает только на 127.0.0.1 без аутентификации.

## Структура проекта

Актуальная раскладка пакета описана в **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) §4**
и не дублируется здесь, чтобы две копии не разошлись. Верхний уровень:

```
telecom-news/
├── AGENTS.md / CLAUDE.md       # правила работы для AI-ассистентов
├── pyproject.toml              # src-layout; runtime: httpx, feedparser; extras: dev, api
├── .githooks/                  # pre-commit (секреты + ruff), pre-push (pytest)
├── scripts/                    # setup.sh, run_pipeline.sh, run_discovery.sh, check_secrets.sh
├── src/telecom_news/           # пакет (см. ARCHITECTURE §4)
├── tests/                      # pytest; conftest.py изолирует данные и окружение
├── data/                       # не в git: news.db, logs/, операторские настройки
└── docs/                       # CURRENT (состояние), DECISIONS, ARCHITECTURE, ROADMAP, SKILLS/
```

Состояние проекта — **[docs/CURRENT.md](docs/CURRENT.md)**, раздел STATE.

## Требования

- Python 3.10+
- SQLite — через stdlib `sqlite3`, БД создаётся автоматически
- Для `process`: запущенный LM Studio с загруженной моделью (см. docs/DEVELOPMENT.md)

## Быстрый старт

Одной командой: `scripts/setup.sh` создаёт `.venv`, ставит пакет с extras,
включает git-хуки (`core.hooksPath .githooks`) и прогоняет тесты. Вручную:

```bash
# Project-local окружение (системный Python не используется)
python3 -m venv .venv          # или: uv venv .venv
source .venv/bin/activate
pip install -e ".[dev,api]"     # dev: pytest, ruff | api: панель управления
git config core.hooksPath .githooks

# Проверка CLI (M5: run выполняет collect/process/publish)
python -m telecom_news --help
python -m telecom_news collect --source sinch-blog --limit 3
python -m telecom_news process --limit 3   # нужен запущенный LM Studio
python -m telecom_news publish --dry-run
python -m telecom_news run --dry-run --limit 3
python -m telecom_news doctor
python -m telecom_news status
python -m telecom_news diagnose        # channel + bot + subscribers (read-only)
# prod MVP ops: docs/SKILLS/prod-mvp-checklist.md
python -m telecom_news sources            # каталог источников; --verify проверит ленты
python -m telecom_news deliver --dry-run   # предпросмотр рассылки подписчикам (M8)
python -m telecom_news bot --once          # обработать команды /start и /language (M8)
python -m telecom_news prune --dry-run     # старые статьи в очереди (D-020)
python -m telecom_news projects list       # multi-project registry (M9a)
python -m telecom_news projects dashboard
python -m telecom_news discover --list     # предложенные источники (ничего не добавляет)
python -m telecom_news discover --history  # где поиск уже был и когда вернётся

# Панель управления (localhost, без аутентификации)
python -m telecom_news serve               # http://127.0.0.1:8765/

# Тесты
pytest
```

> Панель — optional extra `.[api]` (D-021), слушает только localhost и не имеет
> аутентификации. Что делает каждая вкладка: `docs/SKILLS/control-panel.md`.
> Поиск источников: `docs/SKILLS/source-discovery.md`.
> Полное видение multi-project: `docs/VISION_MULTI_PROJECT.md`.

## Для AI-ассистентов

Правила работы над проектом — **[AGENTS.md](AGENTS.md)** (читается любым
ассистентом; `CLAUDE.md` ссылается на него). Состояние — `docs/CURRENT.md`,
раздел STATE. Обоснование решений — `docs/DECISIONS.md`.

## Лицензия

MIT
