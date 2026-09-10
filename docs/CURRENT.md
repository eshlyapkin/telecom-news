# CURRENT — telecom-news (working state)

Короткий canonical working-state для быстрого восстановления нового чата. Подробные документы читаются лениво (см. AI_WORKFLOW.md, Level 2). Актуальный HEAD определяется командой `git log -3 --oneline`, а не этим файлом.

## Проект и цель
Система ежедневного мониторинга и публикации новостей **прежде всего об SMS-индустрии** (A2P/P2A/P2P, SMS-вендоры/агрегаторы/carriers, hubs, маршрутизация/delivery/security/anti-fraud по SMS, партнёрства, регуляторика). Общие телеком-новости без связи с SMS/messaging отфильтровываются. Источники — ru и en. Pipeline: источники → сбор → нормализация → storage/dedup → LLM (LM Studio) → публикация в Telegram. Интерфейс MVP — CLI.

## Текущая стадия
Implementation — **M6 в работе**: M4 проверен живой отправкой (9 публикаций), M5 добавил одноразовый `run` и scheduler script, M6 добавил четыре официальных RSS-источника, сбор всех enabled sources и source health. Автоматический запуск по расписанию настроен пользователем и проверен несколькими циклами.

## Текущий milestone
M6 — Multiple Sources. **Код multi-source готов; осталось проверить реальные новые статьи из новых RSS и при необходимости откорректировать релевантность/шум.**

## Завершён ли предыдущий milestone
Да: M0–M3 завершены. Оговорка по M3: критерий «реальный прогон `process` с запущенным LM Studio» не мог быть проверен в песочнице (нет LM Studio) — NOT VERIFIED; вместо этого проверена вся обвязка (см. критерии ниже). Проверка на живой модели: `collect --source sinch-blog --limit 3`, затем `process`, затем `status` (ожидается: релевантные → `processed` с категорией и русским саммари, общие телеком → `skipped`).

## Следующий конкретный шаг
Получить `TELEGRAM_CHAT_ID` тестового канала и выполнить одну живую отправку с `publish --limit 1`, затем повторный запуск для проверки отсутствия дублей. Scheduler и полный `run` остаются scope M5.

## Ключевые решения, влияющие на следующий шаг
- **accepted:** D-001…D-004, D-007 (`sinch-blog`), D-008 Telegram delivery (реализация — M4).
- M3 зафиксировал: `LLMUnavailableError` (transport/5xx/нет моделей → прогон останавливается, exit 1, статьи остаются `new`) vs `LLMResponseError` (битый ответ → статья `error`, прогон продолжается); `process`: 0 завершён / 2 usage / 1 LLM недоступна; `target_lang` по умолчанию `ru` (ARCHITECTURE 3.9); пустой `LMSTUDIO_MODEL` = первая загруженная модель.

## Фактически существующая реализация
M0–M4 + dev-инфраструктура (текущие изменения в working tree; коммит только по запросу пользователя):
- `pyproject.toml` (Python 3.10+, src-layout; runtime: httpx, feedparser; dev: pytest, ruff)
- `src/telecom_news/`: M1/M2-модули + `llm/` (`client.py`: `LLMClient`, `LLMUnavailableError`, `LLMResponseError`, `parse_json_response`), `processors/relevance.py` (`check_relevance`, `CATEGORIES`, `prepare_article_text`), `processors/summarize.py` (`summarize`); `cli.py` (+ команда `process --limit`); `config.py` (+ `lmstudio_base_url`/`lmstudio_model`/`target_lang`, env `LMSTUDIO_*`, `TELECOM_NEWS_TARGET_LANG`); `storage/database.py` (+ `save_processing_result`)
- Известные шероховатости (кандидаты на чистку): `main.py` — мёртвый дубликат CLI; `logging_setup.py` — параллельная реализация logging (CLI использует `logging_config`)
- `tests/`: 114 тестов (M0–M6; MockTransport/tmp-БД/fake LLM, без реальной сети и продовой БД)
- Dev-инфраструктура: ruff, `.githooks/`, `docs/DEVELOPMENT.md` (+ раздел LM Studio), `scripts/setup.sh`
- `.venv/` (project-local). Локальная `data/news.db` (5 статей `new`, gitignored).

**Не реализовано (scope M5+):** scheduler, scraping, FastAPI. Живая Telegram-отправка ещё не проверена.

## Blockers
Нет. Для M4 нужны токен бота и chat_id тестового канала; для перепроверки M3 на живой модели — запущенный LM Studio.

## Критерии готовности M3 (проверено, кроме живой модели)
- Релевантная статья → `category` + саммари на целевом языке, статус `processed`: **выполнено на фейковой LLM** (E2E: 2 processed с категорией vendor и русским саммари); **на живой модели — NOT VERIFIED**
- Нерелевантная → `skipped` без суммаризации: **выполнено на фейковой LLM** (E2E: 1 skipped); **на живой модели — NOT VERIFIED**
- Недоступный LM Studio: явная ошибка, статьи не теряются, повторный прогон продолжает: **выполнено по-настоящему** (connection refused → exit 1, статьи остались `new`; + unit-тесты retry/5xx/таймаута)
- `pytest`: **зелёный** (98 passed); `ruff check` + `format --check`: **чисто**

## Scope M3 (LLM Processing) — завершён
Входило: `llm/client.py` (OpenAI-совместимый endpoint, таймаут 60с, retry, `LLMUnavailableError`, автоопределение модели); релевантность + 9 категорий; саммари с переводом на `target_lang`; `llm_result` JSON; `skipped` для нерелевантных; `process` с exit-кодами 0/2/1.

НЕ входило (и не сделано): публикация в Telegram; scheduler.

## Scope M2 (Storage and Deduplication) — завершён ранее
SQLite (`storage/database.py`), дедупликация, `collect` с сохранением, рабочий `status`. Проверено двумя реальными прогонами и pytest.
