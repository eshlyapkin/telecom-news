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

## Источники новостей

17 включённых RSS-источников; узкие ленты (`content-review`, `anti-malware-*`,
`securitylab-*`) передают релевантность LLM без keyword-guard (D-012), остальные
фильтруются детерминированно. Основные:

- `sinch-blog` — `https://sinch.com/blog/feed/`;
- `twilio-blog` — `https://www.twilio.com/en-us/blog.feed.xml`;
- `infobip-blog` — `https://www.infobip.com/blog/feed`;
- `gsma-newsroom` — `https://www.gsma.com/newsroom/feed/`;
- `content-review` — `https://content-review.com/feed.xml` (ru);
- `iksmedia` — `https://www.iksmedia.ru/rss/rss_yandex.rss` (ru);
- `habr-cellular-news` — `https://habr.com/ru/rss/hubs/cellular/news/?fl=ru` (ru);
- `mef-news` — `https://mobileecosystemforum.com/feed/` (en, D-014);
- `mobilesquared` — `https://www.mobilesquared.co.uk/feed/` (en, D-014).

`run` без `--source` проверяет все enabled-источники. Ошибка одного источника
не останавливает остальные; результат сохраняется в `source_health` и виден в
`status`. Дедупликация общая для всех источников.

Перед LLM действует консервативный guard: материалы без явного сигнала
SMS/messaging, а также очевидные voice/video/email-материалы без такой связи,
отбрасываются без вызова модели. Это снижает шум от широких Twilio/GSMA-лент,
а LLM остаётся вторым уровнем классификации.

## Telegram (для команды publish)

- Настройки: `TELEGRAM_BOT_TOKEN` и `TELEGRAM_CHAT_ID`.
- `publish --dry-run` не требует токена и сети, а только показывает HTML-текст.
- Обычный `publish` отправляет только статьи со статусом `processed` и после
  успешной отправки атомарно переводит их в `published`.
- Ошибка Telegram оставляет статью `processed`, чтобы повторить отправку;
  статья без саммари помечается `error`.
- Живая отправка в канал в этой среде не выполнялась: нужен chat ID тестового
  канала и явное предоставление переменных окружения пользователем.

## Секреты и безопасность

Telegram credentials не хранятся в репозитории. Подробный runbook ротации токена
находится в `docs/SECURITY.md`, а повторяемая процедура — в
`docs/SKILLS/telegram-token-rotation.md`. Перед commit hook запускает
`scripts/check_secrets.sh` и блокирует очевидные Telegram Bot API tokens в
staged additions. Если секрет попал в лог или чат, его нужно отозвать через
BotFather до следующей отправки.

`TELEGRAM_MIN_INTERVAL` задаёт минимальную паузу между Telegram API-запросами
(default `0.1` seconds); значение `0` отключает дополнительное pacing.

## M7 и диагностика

Команда `doctor` реализована и проверяет БД, Telegram Bot API, LM Studio и все
enabled RSS-источники. Она возвращает `0`, если все проверки прошли, и `1`,
если хотя бы одна зависимость недоступна. Acceptance criteria и recovery rules
зафиксированы в `docs/SKILLS/m7-doctor-and-recovery.md`.

Запуск:

```bash
.venv/bin/python -m telecom_news doctor
```

`diagnose` отвечает на вопрос «почему ничего не публикуется»: читает счётчики
статей, `source_health`, последние блоки прогонов из `data/logs/pipeline.log` и
(если не указан `--offline`) пробует LM Studio и Telegram Bot API, после чего печатает
вердикт. Команда ничего не пишет в БД и не отправляет сообщений; exit code `1`
означает, что найдена блокирующая причина.

```bash
.venv/bin/python -m telecom_news diagnose            # вердикт + доказательства
.venv/bin/python -m telecom_news diagnose --offline   # без сетевых проб
.venv/bin/python -m telecom_news diagnose --json      # для скриптов
```

M8+: каталог источников (D-016):

```bash
.venv/bin/python -m telecom_news sources                     # список каталога (news)
.venv/bin/python -m telecom_news sources --kind all --json   # все эндпоинты, включая status
.venv/bin/python -m telecom_news sources --verify --verify-limit 10   # быстрая живая проверка
.venv/bin/python -m telecom_news sources --verify              # все 59 включённых лент (несколько минут)
.venv/bin/python -m telecom_news sources import --csv sheet.csv --dry-run
.venv/bin/python -m telecom_news sources import --csv sheet.csv   # перезаписать sources_catalog.py
```

Управление источниками и свежестью (M8+, D-017):

```bash
# выключить мёртвые ленты, не правя сгенерированный каталог (переживает sources import)
TELECOM_NEWS_DISABLED_SOURCES=2600hz,thundersms,textmarks

# порог свежести: старше 30 дней не сохраняем (0 = сохранять всё)
TELECOM_NEWS_MAX_ARTICLE_AGE_DAYS=30
.venv/bin/python -m telecom_news collect --source cnews-telecom --max-age-days 90
```

Реестр вырос до 63 источников (59 включённых), поэтому `doctor` и `run` теперь
последовательно обходят 59 лент: таймаут каждой — 15 с, то есть при недоступной
сети один прогон `doctor` может идти минуты. Для быстрой проверки после
обновления сначала `sources --verify --verify-limit 10`, и только потом полный
`doctor`.

Каталог — `src/telecom_news/sources_catalog.py`: `kind="news"` попадает в
`config.SOURCES` и публикуется, `kind="status"` (`status.<vendor>/history.rss`,
`*.statuspage.io`) только объявлен — это ленты инцидентов, а не новости.
Импорт принимает **весь XLSX** (все листы, нужен `openpyxl` из dev-зависимостей)
или CSV одного листа: колонки, тип источника, статус проверки и язык
распознаются автоматически, строки «не вошёл / не подтверждён / 404 / timeout»
отбрасываются, дубли и совпадения с рукописными записями `config.py`
пропускаются. Рукописные записи (`sinch-blog`, `twilio-blog`, `mef-news`,
`mobilesquared`, CNews/SecurityLab/Anti-Malware/Habr) при повторном импорте не
теряются.

Текущий импорт (2026-09-11): 102 записи каталога (42 news + 60 status),
63 источника в реестре, 59 включённых. Столько лент каждый прогон `run`
обходит целиком, поэтому первый живой прогон стоит замерить:
`time ./scripts/run_pipeline.sh` и `python -m telecom_news diagnose`.

M8: языки, подписки и бот:

```bash
.venv/bin/python -m telecom_news bot --once          # обработать команды один раз (из шедулера)
.venv/bin/python -m telecom_news bot                 # long polling (долгоживущий процесс)
.venv/bin/python -m telecom_news deliver --dry-run   # предпросмотр рассылки подписчикам
.venv/bin/python -m telecom_news deliver             # отправить подписчикам
```

Переменные окружения M8: `TELECOM_NEWS_TARGET_LANGS=ru,en` (набор языков публикации;
старый `TELECOM_NEWS_TARGET_LANG` продолжает работать), `TELEGRAM_CHAT_ID_EN` (канал
для второго языка; первый язык берёт `TELEGRAM_CHAT_ID`), `SUBSCRIBER_MAX_AGE_HOURS`,
`SUBSCRIBER_MAX_PER_CYCLE`, `SUBSCRIBER_MAX_ATTEMPTS`. Подписки заводятся сами:
пользователь пишет боту `/start` и выбирает языки кнопками. `run` теперь выполняет
`collect → process → publish → deliver`.

### Лимиты публикации и свежесть (D-020)

`run --limit N` — это бюджет **сбора и обработки** (сколько статей за цикл достаём
и отдаём модели). Лимиты рассылки задаются отдельно и по умолчанию не связаны
с `--limit`:

| Переменная | По умолчанию | Смысл |
|---|---|---|
| `PUBLISH_MAX_PER_CYCLE` | 10 | сколько постов максимально уходит в канал за цикл (`0` = без лимита) |
| `PUBLISH_MAX_AGE_HOURS` | 48 | статьи старше этого окна в канал не публикуются (`0` = публиковать всё) |
| `SUBSCRIBER_MAX_PER_CYCLE` | 10 | сколько личных сообщений максимально получает один подписчик за цикл (`0` = без лимита) |
| `SUBSCRIBER_MAX_AGE_HOURS` | 24 | окно свежести для рассылки подписчикам (`0` = без окна) |

Разовые переопределения: `run --max-posts 3 --max-per-subscriber 2`,
`publish --max-age-hours 0`, `deliver --max-age-hours 96`, `deliver --limit 5`.
Раньше `run --limit 30` означал ещё и «до 30 постов в канал и до 30 личных
сообщений каждому подписчику за один 15-минутный цикл» — теперь нет.

Статьи, которые окно свежести не пропустило, остаются в `processed` и печатаются
явной строкой (`N processed article(s) are older than 48 h and stay unpublished`),
а `diagnose` показывает их как `Processed but older than 48 h: N (not posted)` —
это не сбой публикации.

Очередь `new`, накопленная до появления порога свежести (D-017), чистится
отдельной командой: такие статьи сортируются первыми и съедают бюджет обработки
каждого прогона.

```bash
.venv/bin/python -m telecom_news prune --dry-run               # что будет удалено
.venv/bin/python -m telecom_news prune                         # порог из TELECOM_NEWS_MAX_ARTICLE_AGE_DAYS (30)
.venv/bin/python -m telecom_news prune --max-age-days 60       # свой порог
```

`prune` удаляет только строки со статусом `new` и только те, у которых известна
дата публикации (`skipped`/`published` — память дедупликации и аудит, строки без
даты не считаются старыми). Вернуться удалённые статьи не могут: `collect`
отбросит их снова по порогу свежести. `diagnose` печатает
`Queued but older than 30 day(s): N (prune)`.

Recovery и backup SQLite:

```bash
.venv/bin/python -m telecom_news recover                     # вернёт не более 3-х попыток
.venv/bin/python -m telecom_news recover --max-attempts 0    # принудительно вернуть всё
```

Статья, которую модель не может обработать, после 3 попыток остаётся в `error`
и больше не блокирует очередь (D-015); `diagnose` показывает такие статьи
как «Parked errors». Статьи, которые были в `error` до обновления на версию с
D-015, миграция сразу считает исчерпавшими ретраи — чтобы разом вернувшийся
«хвост» ошибок не перекрыл свежие новости. Вернуть их в работу:
`recover --max-attempts 0` (обнуляет счётчик попыток).

`run` автоматически возвращает статьи со статусом `error` в очередь `new` перед
обработкой.

Если БД старше M8 (в ней нет таблицы `deliveries`), публикатор не рассылает
старые `published`-статьи заново: они помечаются доставленными без отправки
(D-018). Текущий порог свежести — 30 дней (`TELECOM_NEWS_MAX_ARTICLE_AGE_DAYS`,
`collect --max-age-days`); статьи старше порога не сохраняются вовсе.

`diagnose` считает сбоями только включённые источники: у выключенных
кил-свитчем лент в `source_health` остаётся старая ошибка, и они печатаются
отдельной строкой `Disabled sources with stale errors (not counted)` вместо
предупреждения (D-019). Так реальный новый сбой не тонет в шуме про уже
отключённые ленты.

Бот печатает по одной строке на каждый принятый апдейт
(`telecom_news.bot: update …`). Строка `channel post in '…' ignored` означает,
что команда написана **в канале**: бот обрабатывает только личку и кнопки, так
что подписка делается в личном чате с ботом (`/start` там). Если в логе нет ни
одной строки `telecom_news.bot`, процесс бота не запущен или `data/bot.lock`
держит другой экземпляр.

Backup и restore SQLite:

```bash
.venv/bin/python -m telecom_news backup --keep-days 14
.venv/bin/python -m telecom_news restore --input data/backups/news.db
```

Перед ручным restore остановите scheduler. После восстановления проверьте
`status`, `doctor` и только затем возобновляйте cron.

Текущий порядок перед M7-разработкой:

1. проверить `git status` и `docs/CURRENT.md`;
2. не публиковать секреты в issue, логи или документацию;
3. после изменения кода выполнить полный pytest и Ruff;
4. проверить `git diff --check` и staged secret scan.

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
