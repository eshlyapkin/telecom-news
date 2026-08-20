# PROJECT STATE — telecom-news

> Последнее обновление: 2025-07 (сессия инициализации)

## Цель проекта

Система мониторинга и агрегации новостей в сфере телекоммуникаций.

Целевая система на MVP — фоновый pipeline: источники → сбор → нормализация → хранение/дедупликация → обработка локальной LLM (LM Studio) → подготовка публикации → Telegram. Обязательный интерфейс для MVP — CLI. HTTP API (FastAPI) опционален и не входит в MVP.

## Текущее фактическое состояние

### Файлы, реально существующие в репозитории:

```
telecom-news/
├── .gitignore          # настроен (Python, venv, IDE, data, env)
├── README.md           # описание проекта + архитектура + целевая структура
├── data/.gitkeep       # placeholder для каталога данных
├── docs/               # каталог документации (сейчас содержит только этот файл и SESSION_HANDOFF.md)
├── src/telecom_news/   # пустые подкаталоги: api/, collectors/, processors/, storage/
└── tests/              # пустой каталог
```

### Git-состояние:

- Репозиторий инициализирован (`git init` выполнен).
- Ветка: `master`.
- Создан baseline commit: **`539a26d`** — `chore: establish project planning baseline` (7 файлов, только документация + .gitignore + data/.gitkeep).
- Working tree чистый.

## Текущий этап разработки

**Этап 0 — Инициализация каркаса.**
Проект находится на самой ранней стадии: есть только структура каталогов, README и .gitignore. Ни одного Python-файла, ни pyproject.toml, ни кода ещё не существует.

## Что уже завершено

- [x] Каталог проекта создан
- [x] Git репозиторий инициализирован (ветка `master`)
- [x] README.md с описанием и целевой архитектурой
- [x] .gitignore настроен
- [x] Каркас каталогов: `src/telecom_news/{api,collectors,processors,storage}`, `tests/`, `data/`, `docs/`
- [x] Система постоянной памяти (docs/PROJECT_STATE.md, docs/DECISIONS.md, docs/SESSION_HANDOFF.md, docs/AI_WORKFLOW.md)

## Что ещё не сделано

- [ ] Первый Git commit
- [ ] pyproject.toml (конфигурация Python-проекта, зависимости)
- [ ] `__init__.py` во всех пакетах
- [ ] Основной код: collectors, processors, storage (+ delivery/notify для Telegram)
- [ ] main.py (CLI) / config.py
- [ ] Тесты
- [ ] docs/ARCHITECTURE.md (подробная архитектура MVP-pipeline) — **ещё не создан**
- [ ] docs/ROADMAP.md (milestones M0/M1/...) — **ещё не создан**
- [ ] Виртуальное окружение и установка зависимостей

## Известные проблемы

- README описывает целевую структуру, но фактические файлы ещё не созданы. До первого commit README может вводить в заблуждение — нужно чётко разделять «заявлено» и «существует».
- Каталог `docs/` был пуст до создания системы памяти; Git не отслеживает пустые каталоги, поэтому `src/telecom_news/{api,collectors,processors,storage}` и `tests/` **не попадут в первый commit** без placeholder-файлов.

## Следующий конкретный шаг

**Следующая рабочая сессия:**
1. Создать `docs/ARCHITECTURE.md` (подробная архитектура MVP-pipeline).
2. Создать `docs/ROADMAP.md` (milestones, начиная с M0/M1).
3. После подтверждения — переходить к коду: pyproject.toml (без fastapi/uvicorn, см. D-004) + базовый pipeline + CLI.

## Результаты последней проверки Git

```
$ git status
On branch master
nothing to commit, working tree clean

$ git log --oneline -3
539a26d chore: establish project planning baseline
```

**Hash последнего (baseline) commit:** `539a26d`
