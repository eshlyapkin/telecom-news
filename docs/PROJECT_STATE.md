# PROJECT STATE — telecom-news

> Последнее обновление: завершение planning-стадии (созданы ARCHITECTURE.md и ROADMAP.md)

## Цель проекта

Автоматизированная система ежедневного мониторинга и публикации новостей **прежде всего об SMS-индустрии** и технологиях мобильных сообщений (A2P/P2A/P2P, SMS-вендоры и messaging-платформы, агрегаторы, операторы/carriers в контексте SMS-бизнеса, SMS hubs, новые услуги/продукты, маршрутизация/доставка/безопасность/anti-fraud по SMS, партнёрства и сделки, затрагивающие SMS/messaging-бизнес, регуляторные изменения). Общие телеком-новости без прямой связи с SMS/messaging не приоритетны. Источники — русскоязычные и англоязычные.

Целевая система на MVP — фоновый pipeline: источники → сбор → нормализация → хранение/дедупликация → LLM (LM Studio) → подготовка публикации → Telegram. Обязательный интерфейс для MVP — CLI. HTTP API (FastAPI) опционален и не входит в MVP (D-004).

## Текущее фактическое состояние

### Файлы, реально существующие в репозитории:

```
telecom-news/
├── .gitignore          # настроен (Python, venv, IDE, data, env)
├── README.md           # описание проекта + архитектура + целевая структура
├── data/.gitkeep       # placeholder для каталога данных
└── docs/
    ├── AI_WORKFLOW.md      # протоколы работы LLM (Startup / Work / End-of-session)
    ├── PROJECT_STATE.md    # этот файл
    ├── DECISIONS.md        # журнал технических решений (D-001…D-008)
    ├── SESSION_HANDOFF.md  # отчёт о последней сессии
    ├── ARCHITECTURE.md     # архитектура MVP-pipeline (создан в этой сессии)
    └── ROADMAP.md          # milestones M0–M7 + MVP Definition + Non-goals (создан в этой сессии)
```

Пустые каталоги `src/telecom_news/{api,collectors,processors,storage}` и `tests/` существуют локально, но **не отслеживаются Git'ом** (в них нет файлов). Никакого Python-кода, pyproject.toml и тестов ещё нет.

### Git-состояние:

- Репозиторий инициализирован, ветка `master`, working tree чистый.
- Актуальный HEAD является фактом Git и определяется командой (`git log --oneline -1`), а не этим документом. На момент завершения planning последний commit — `docs: finalize architecture and roadmap` (planning checkpoint).

## Текущая стадия разработки

**Planning / Project Setup — завершён.**
Созданы `docs/ARCHITECTURE.md` и `docs/ROADMAP.md`. Planning — отдельная предварительная стадия, она **не является M0**.

**M0 (Foundation) ещё НЕ начат.** Старт M0 — только после явного подтверждения пользователя.

## Что уже завершено

- [x] Каталог проекта создан
- [x] Git репозиторий инициализирован (ветка `master`), baseline commit зафиксирован
- [x] README.md с описанием и целевой архитектурой
- [x] .gitignore настроен
- [x] Каркас каталогов: `src/telecom_news/{api,collectors,processors,storage}`, `tests/`, `data/`, `docs/`
- [x] Система постоянной памяти (PROJECT_STATE.md, DECISIONS.md, SESSION_HANDOFF.md, AI_WORKFLOW.md)
- [x] Согласование MVP-архитектуры: pipeline + CLI, FastAPI deferred (D-004)
- [x] docs/ARCHITECTURE.md — архитектура MVP-pipeline
- [x] docs/ROADMAP.md — milestones M0–M7, MVP Definition, Non-goals

## Что ещё не сделано

- [ ] **M0 — Foundation:** pyproject.toml (без fastapi/uvicorn), `__init__.py` во всех пакетах, config.py, logging, базовая модель Article, CLI skeleton, тестовая инфраструктура + smoke test
- [ ] M1–M7 (см. docs/ROADMAP.md)
- [ ] Виртуальное окружение и установка зависимостей

## Статусы решений по итогам planning

- **D-008 (Telegram delivery): `accepted`** — публикация в Telegram является частью утверждённой цели проекта и MVP Definition; конкретная библиотека не фиксируется, реализация — M4.
- **D-007 (предпочтение структурированному официальному интерфейсу; RSS или API): `proposed`** — первый реальный источник ещё не выбран и не проверен; конкретный механизм определяется в M1. RSS не объявляется обязательным до выбора источника.

## Известные проблемы / замечания

- README описывает **целевую** структуру (pyproject.toml, src/telecom_news/*.py, tests/test_*.py), которой ещё нет — это «заявлено», а не «существует».
- Пустые каталоги (`src/telecom_news/*`, `tests/`) не отслеживаются Git'ом; попадут в репозиторий только после появления файлов (M0).
- Пустой каталог `src/telecom_news/api/` подлежит удалению при M0 (FastAPI не входит в MVP, D-004).
- D-007 остаётся `proposed` до M1 (выбор и проверка первого источника); D-008 — `accepted` (см. раздел «Статусы решений по итогам planning» выше).

## Следующий конкретный шаг

**M0 — Foundation** (только после явного подтверждения пользователя):
pyproject.toml + Python package (`__init__.py`) + config.py + logging + базовая модель Article + CLI skeleton + pytest smoke test. Детали и критерии готовности — в `docs/ROADMAP.md`, раздел M0.

## Git-состояние

Актуальный HEAD является фактом Git и определяется командой (`git log --oneline -1`), а не этим документом. На момент завершения planning: ветка `master`, working tree чистый, последний commit — `docs: finalize architecture and roadmap` (planning checkpoint).
