# CURRENT — telecom-news (working state)

Короткий canonical working-state для быстрого восстановления нового чата. Подробные документы читаются лениво (см. AI_WORKFLOW.md, Level 2). Актуальный HEAD определяется командой `git log -3 --oneline`, а не этим файлом.

## Проект и цель
Система ежедневного мониторинга и публикации новостей **прежде всего об SMS-индустрии** (A2P/P2A/P2P, SMS-вендоры/агрегаторы/carriers, hubs, маршрутизация/delivery/security/anti-fraud по SMS, партнёрства, регуляторика). Общие телеком-новости без связи с SMS/messaging отфильтровываются. Источники — ru и en. Pipeline: источники → сбор → нормализация → storage/dedup → LLM (LM Studio) → публикация в Telegram. Интерфейс MVP — CLI.

## Текущая стадия
Implementation — **M0 завершён** (подтверждено: editable install, `python -m telecom_news --help`, pytest зелёные).

## Текущий milestone
M1 — One News Source. **Не начат.** Старт только после явного подтверждения пользователя.

## Завершён ли предыдущий milestone
Да: M0 завершён (см. «Фактически существующая реализация» и критерии готовности ниже).

## Следующий конкретный шаг
Начать M1 по составу из docs/ROADMAP.md (только после подтверждения пользователя): выбор одного реального SMS/messaging-источника, `httpx` + `feedparser`, `collectors/base.py` + коллектор выбранного механизма, `processors/normalize.py` (`RawItem → Article`), CLI `collect --source <id>`. M1 НЕ начат.

## Ключевые решения, влияющие на следующий шаг
- **accepted:** D-001 Python 3.10+ / src-layout; D-002 слои Collectors → Processors → Storage (+ llm/, delivery/); D-003 SQLite по умолчанию (реализация — M2); D-004 CLI для MVP, FastAPI deferred; D-008 Telegram delivery (реализация — M4).
- **proposed:** D-007 — первый источник выбирается в M1 после фактической проверки (предпочтение RSS/API над scraping).

## Фактически существующая реализация
Документация + завершённый M0 Foundation:
- `pyproject.toml` (Python 3.10+, src-layout; build backend — setuptools; **runtime-зависимостей нет**; dev: pytest)
- `src/telecom_news/`: `__init__.py`, `__main__.py`, `cli.py` (argparse; `--help`, `--version`; заглушки `run`/`status` с явным сообщением «not implemented yet» и exit code 2), `config.py` (`Config`: project_root, data_dir, log_level + env-переменные `TELECOM_NEWS_DATA_DIR`, `LOG_LEVEL`), `logging_config.py` (stdlib logging, `setup_logging(level)`), `models.py` (dataclass `Article` по ARCHITECTURE 3.4 + `compute_content_hash`)
- Пустой каталог `src/telecom_news/api/` удалён (D-004); пустые каталоги `collectors/`, `processors/`, `storage/` не отслеживаются Git'ом и заполняются в M1–M2
- `tests/`: `test_smoke.py`, `test_models.py`, `test_config.py`, `test_logging.py` (18 тестов, без сети/БД/LLM/Telegram)
- `.venv/` (project-local; исключён .gitignore). Системный Python не изменялся.

**Не реализовано (по scope M0):** httpx/feedparser, реальные collectors, SQLite, LM Studio, Telegram, scheduler, scraping, FastAPI.

## Blockers
Нет. Для M1 понадобится выбор реального источника (D-007, proposed). Внешние зависимости LM Studio — M3, Telegram-токен/chat_id — M4.

## Критерии готовности M0 (проверено)
- `pip install -e .` в project-local venv: **выполнено** (uv pip install -e ".[dev]", Python 3.12.3; системный python3 не имеет pip/ensurepip, поэтому venv создан через `uv venv`)
- `python -m telecom_news --help`: **работает**, печатает список команд (`run`, `status`), exit code 0
- `pytest`: **зелёный** (18 passed; импорт пакета, создание Article, CLI help, config, logging setup)

## Scope M0 (Foundation) — завершён
Входило: `pyproject.toml` (Python 3.10+, src-layout; **без внешних runtime-зависимостей**); package `src/telecom_news/` с `__init__.py`; удаление пустого каталога `api/` (D-004); `config.py` (пути, LM Studio endpoint, env — без реальных вызовов); стандартный Python logging; минимальная доменная модель **`Article` в `src/telecom_news/models.py`** (dataclass, без БД и persistence); минимальный CLI skeleton (`python -m telecom_news --help`, заглушки `run/status`); pytest infrastructure + smoke test.

НЕ входит: httpx; feedparser; реальный RSS/API collector; SQLite; LM Studio; Telegram; scheduler; scraping; FastAPI. Внешние зависимости (httpx, feedparser) добавляются в milestone, где становятся фактически необходимыми (M1).

Критерии готовности M0: `pip install -e .` работает в чистом venv; `python -m telecom_news --help` печатает список команд; `pytest` зелёный (импорт пакета, создание Article, CLI help).
