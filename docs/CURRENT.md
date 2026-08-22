# CURRENT — telecom-news (working state)

Короткий canonical working-state для быстрого восстановления нового чата. Подробные документы читаются лениво (см. AI_WORKFLOW.md, Level 2). Актуальный HEAD определяется командой `git log -3 --oneline`, а не этим файлом.

## Проект и цель
Система ежедневного мониторинга и публикации новостей **прежде всего об SMS-индустрии** (A2P/P2A/P2P, SMS-вендоры/агрегаторы/carriers, hubs, маршрутизация/delivery/security/anti-fraud по SMS, партнёрства, регуляторика). Общие телеком-новости без связи с SMS/messaging отфильтровываются. Источники — ru и en. Pipeline: источники → сбор → нормализация → storage/dedup → LLM (LM Studio) → публикация в Telegram. Интерфейс MVP — CLI.

## Текущая стадия
Planning — **завершён** (ARCHITECTURE.md, ROADMAP.md созданы). Основной код не написан.

## Текущий milestone
M0 — Foundation. **Не начат.** Старт только после явного подтверждения пользователя.

## Завершён ли предыдущий milestone
Planning завершён; M0 ещё не начинался (предыдущего milestone нет).

## Следующий конкретный шаг
Начать M0 по составу ниже (только после подтверждения пользователя).

## Ключевые решения, влияющие на следующий шаг
- **accepted:** D-001 Python 3.10+ / src-layout; D-002 слои Collectors → Processors → Storage (+ llm/, delivery/); D-003 SQLite по умолчанию (реализация — M2); D-004 CLI для MVP, FastAPI deferred; D-008 Telegram delivery (реализация — M4).
- **proposed:** D-007 — первый источник выбирается в M1 после фактической проверки (предпочтение RSS/API над scraping).

## Фактически существующая реализация
Только документация и каркас: README.md, .gitignore, `data/.gitkeep`, `docs/*.md` (AI_WORKFLOW, CURRENT, PROJECT_STATE, DECISIONS, SESSION_HANDOFF, ARCHITECTURE, ROADMAP), пустые каталоги `src/telecom_news/{api,collectors,processors,storage}` и `tests/`. **Никакого Python-кода, pyproject.toml и тестов нет.**

## Blockers
Нет. Внешние зависимости (LM Studio — M3, Telegram-токен/chat_id — M4) пока не нужны.

## Scope M0 (Foundation)
Входит: `pyproject.toml` (Python 3.10+, src-layout; **без внешних runtime-зависимостей**); package `src/telecom_news/` с `__init__.py`; удаление пустого каталога `api/` (D-004); `config.py` (пути, LM Studio endpoint, env — без реальных вызовов); стандартный Python logging; минимальная доменная модель **`Article` в `src/telecom_news/models.py`** (dataclass, без БД и persistence); минимальный CLI skeleton (`python -m telecom_news --help`, заглушки `run/status`); pytest infrastructure + smoke test.

НЕ входит: httpx; feedparser; реальный RSS/API collector; SQLite; LM Studio; Telegram; scheduler; scraping; FastAPI. Внешние зависимости (httpx, feedparser) добавляются в milestone, где становятся фактически необходимыми (M1).

Критерии готовности M0: `pip install -e .` работает в чистом venv; `python -m telecom_news --help` печатает список команд; `pytest` зелёный (импорт пакета, создание Article, CLI help).
