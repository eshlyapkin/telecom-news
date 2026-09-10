# DEVELOPMENT — инструкции разработчика

Рабочие инструкции: окружение, запуск, тесты, линт, хуки, конвенции.
Протоколы AI-сессий — в `AGENTS.md` (корень) и `docs/AI_WORKFLOW.md`.

## Требования

- Python 3.10+, git. Больше ничего: зависимости ставятся в project-local `.venv`
  (системный Python не трогаем).

## Быстрый старт

```bash
./scripts/setup.sh   # .venv + pip install -e ".[dev]" + git-хуки + pytest
```

Вручную (то же самое по шагам):

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
git config core.hooksPath .githooks
.venv/bin/python -m pytest -q
```

## Запуск

```bash
.venv/bin/python -m telecom_news --help
.venv/bin/python -m telecom_news collect --source sinch-blog --limit 5
.venv/bin/python -m telecom_news status
```

`run` выполняет один полный цикл `collect → process → publish`. Локальная БД
создаётся сама: `data/news.db` (переопределение — `TELECOM_NEWS_DB`; каталог
`data/` не в git). Для запуска из cron/Task Scheduler используется
`scripts/run_pipeline.sh`: он пишет лог в `data/logs/pipeline.log` и блокирует
параллельные прогоны через `flock`.

## LM Studio (для команды process)

- Установите LM Studio, загрузите модель и включите сервер: вкладка Developer →
  Status: Running (по умолчанию `http://localhost:1234/v1`).
- Проверка: `curl http://localhost:1234/v1/models` должен вернуть список
  с загруженной моделью.
- Настройки: `LMSTUDIO_BASE_URL`, `LMSTUDIO_MODEL` (пусто = первая загруженная
  модель), `TELECOM_NEWS_TARGET_LANG` (язык саммари, по умолчанию `ru`).
- Без запущенного сервера `process` останавливается с exit 1, статьи остаются `new`.

## Автоматический запуск

Один цикл вручную:

```bash
./scripts/run_pipeline.sh
```

Для WSL рекомендуется Windows Task Scheduler: создать задачу с повтором каждые
15 минут, действие — `wsl.exe`, аргументы —
`-d <ваш-дистрибутив> -- bash -lc 'source ~/.config/telecom-news/env && cd /home/joe/Projects/telecom-news && ./scripts/run_pipeline.sh'`.
Секреты храните вне репозитория, например в `~/.config/telecom-news/env` с
правами `chmod 600`; файл должен экспортировать `TELEGRAM_BOT_TOKEN` и
`TELEGRAM_CHAT_ID`. LM Studio должен быть доступен из WSL во время прогона.

Для cron аналогичная запись запускает скрипт каждые 15 минут:

```cron
*/15 * * * * bash -lc 'source ~/.config/telecom-news/env && /home/joe/Projects/telecom-news/scripts/run_pipeline.sh'
```

## Telegram (для команды publish)

- Настройки: `TELEGRAM_BOT_TOKEN` и `TELEGRAM_CHAT_ID`.
- `publish --dry-run` не требует токена и сети, а только показывает HTML-текст.
- Обычный `publish` отправляет только статьи со статусом `processed` и после
  успешной отправки атомарно переводит их в `published`.
- Ошибка Telegram оставляет статью `processed`, чтобы повторить отправку;
  статья без саммари помечается `error`.
- Живая отправка в канал в этой среде не выполнялась: нужен chat ID тестового
  канала и явное предоставление переменных окружения пользователем.

## Тесты

```bash
.venv/bin/python -m pytest -q        # весь сьют
.venv/bin/python -m pytest tests/test_storage.py -q   # один файл
```

Правила тестов (обязательно):

- без реальной сети — HTTP мокается через `httpx.MockTransport`;
- без реальной БД — только `tmp_path` (продовая `data/news.db` в тестах не трогается);
- без LLM/Telegram — только фикстуры и моки (M3/M4);
- новый модуль = новый `tests/test_<module>.py`.

## Линт и формат

```bash
.venv/bin/ruff check .    # линт: E, F, W, I (isort), UP (py310), B (bugbear)
.venv/bin/ruff format .   # форматирование
```

Конфиг — `[tool.ruff]` в `pyproject.toml`: line-length 100, target py310,
first-party `telecom_news`. Код на современном синтаксисе 3.10+
(`X | None`, builtin generics) — ruff UP это проверяет.

## Git-хуки

Каталог `.githooks/` (включается через `git config core.hooksPath .githooks`,
`setup.sh` делает это сам):

| Хук | Что делает |
|---|---|
| `pre-commit` | `git diff --check` + `ruff check` + `ruff format --check` (быстро, без тестов) |
| `pre-push` | полный `pytest` |

Обход — только осознанно: `git commit --no-verify` / `git push --no-verify`.
Если ruff/pytest не установлены, pre-commit пропускает линт с предупреждением,
а pre-push падает с подсказкой про `setup.sh`.

## Git-конвенции

- Ветка `master`, working tree перед коммитом — чистый от мусора.
- Коммит — одна логичная единица (milestone целиком или его завершённая часть).
- Перед коммитом: `pytest -q`, `git diff --check`, осмотр `git status`/`git diff`.
- AI-сессии коммитят только по явному запросу пользователя (см. `AGENTS.md`).
- Никогда не коммитить: `.venv/`, `data/*.db`, `.env*`, токены, `docs/telegram_bot_details.txt`.

## Конвенции кода

- src-layout, type hints везде, frozen dataclasses для конфигов.
- Ошибки коллекторов — только `CollectorError` (httpx наружу не торчит).
- Статусы статей — только из `storage.STATUSES`, переходы — через `set_status`.
- Нормализация — чистые функции без сети/БД; дедупликация — по hash + URL.
- Env-переменные: префикс `TELECOM_NEWS_*`, плюс `LOG_LEVEL` (см. `config.py`).

## Troubleshooting

- `No module named feedparser/httpx` — ставить через `.venv` (`setup.sh`),
  а не в системный Python.
- Хуки не срабатывают — проверить `git config core.hooksPath` (= `.githooks`).
- `status` говорит «No database yet» — сначала `collect` (БД создаётся при сборе).
