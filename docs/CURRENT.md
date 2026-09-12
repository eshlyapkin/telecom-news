# CURRENT — telecom-news

Canonical project handoff. Claims rest on repository files, Git state and actual
command results; anything else is marked NOT VERIFIED.

## Session 2026-09-12 (prod MVP): merge checklist + bot/subscriber holes

Goal ① — production MVP for the **news engine** (not full GUI §§40–70).

Code holes fixed (only what ops already hit):

- `bot.lock` stores PID; stale empty/dead-PID lock is removed on `bot` start
- `diagnose` reports Bot running/stale lock, active subscribers, suspicious
  username matching the bot nick (`sms_telecom_news_bot` pattern)
- `Database.list_subscribers()` for ops
- Runbook: `docs/SKILLS/prod-mvp-checklist.md`

Operator still must: merge/pull, `/start` in private chat, confirm scheduler +
bot after reboot. Channel quietness from relevance filter is **not** a publish bug.

## Session 2026-09-12 (later): M9a multi-project foundation (S1+S2 + code)

Operator brief §§40–70 accepted as **vision** (not full build-out). Delivered:

- `docs/VISION_MULTI_PROJECT.md` — S1 vision + S2 gap analysis vs CLI/D-020
- `src/telecom_news/projects.py` — JSON project registry, default `sms-business-news`
- `src/telecom_news/api/` — optional FastAPI + static GUI (dashboard/switcher)
- CLI: `projects` (list/show/create/pause-publish/dashboard), `serve`
- `publish` respects global + default-project kill switches
- D-021, ROADMAP M9, pyproject optional extra `api`

**Operator machine (from earlier same day, still true unless re-checked):**
pipeline OK; channel quiet because relevance filter skipped non-SMS items;
bot was restarted; sole subscriber row still looked like bot self-chat
(`1288967298` / username `sms_telecom_news_bot`) — needs live `/start` in DM.
**NOT VERIFIED after M9a:** `serve` on operator host, GUI in browser, auth.

Default project keeps using legacy `data/news.db` — no forced multi-DB migration.

## Статус расширения источников (2026-09-11)

- Пользователь загрузил в `master` книгу
  `docs/sms_messaging_rss_100_unique_sources_verified_2026-09-11.xlsx`
  (100 уникальных источников, лист «A+ приоритет», 17 дополнительных
  тематических лент, лист отклонённых, методология).
- Импорт выполнен командой `python -m telecom_news sources import --csv
  docs/...xlsx`: **каталог 102 записи — 42 новостные ленты и 60
  status-эндпоинтов**; реестр `config.SOURCES` — **63 источника, 59
  включённых** (было 19/17 до M8); `catalog_issues() == []`.
- Status-эндпоинты объявлены с `enabled=False` и в `config.SOURCES` не
  попадают: это ленты инцидентов, а не новости (D-016). Вид источника
  определяется типом и URL, а не MIME: 60 строк таблицы помечены
  `application/rss+xml`.
- Разбор книги: 189 строк данных → 42 news + 60 status + 87 пропусков (54
  дубля endpoint, 18 совпадений с рукописными записями `config.py`, 15 строк,
  отклонённых таблицей). Повторный импорт даёт тот же файл (идемпотентность).
- Проверено: `pytest -q` → **218 passed**; `ruff check`,
  `ruff format --check`, `git diff --check` — чисто. **NOT VERIFIED:** живая
  проверка новых лент (`sources --verify` требует исходящей сети, у агента её
  нет) и фактическая свежесть 40+ новых новостных фидов — это делается на
  машине пользователя.

## Status (2026-09-11)

- **M0–M7 are implemented.** `master` carries M0–M3 (`242ac0d`); M4–M7 live on
  `arena/01a08bb1-telecom-news` (tip `6dbe4ed`, 11 commits ahead, `master` is its
  ancestor).
- M4–M7 were fast-forwarded into `arena/01a09044-telecom-news` and pushed.
  **PR #1 (`6dbe4ed` → `master`) is open, GitHub reports `MERGEABLE`**; it is not
  merged, so `master` on its own still describes only M0–M3.
- **D-011 sits on top of `6dbe4ed` on this branch:** 12 Russian feeds in
  `config.SOURCES`, the Cyrillic-aware relevance guard and the off-topic fix in
  `processors/relevance.py`, plus tests and this document. It is a separate
  commit, so the M0–M3 → M4–M7 history stays attributable per milestone.
- Verified by the agent on 2026-09-11: `pytest -q` → **143 passed** (125 before
  D-011); `ruff check` and `ruff format --check` clean; `git diff --check` clean;
  working tree clean before the D-011 edits.
- Verified behaviour of the merged code: `doctor` prints per-dependency
  `[OK]`/`[FAIL]` and exits `1` when any check fails; `backup` writes an
  integrity-checked `0600` file and honours `--keep-days`; `collect` on a
  disabled source exits `2`.

## Why the channel looked empty (2026-09-10 evening → 2026-09-11)

Root cause, proven from code, not inferred:

1. M6 (`9a7c3a6`) added a deterministic pre-LLM gate: `check_relevance()`
   returns `relevant=False` when `has_messaging_signal()` finds no term from
   `MESSAGING_TERMS`.
2. That tuple was **entirely Latin-script** (asserted by a test now). Russian
   articles write «СМС», «чат-бот», «мессенджер» — no match, so every such item
   was stored as `skipped` **without the model ever being asked**.
3. The three RU feeds had just been enabled in M7 (`6dbe4ed`), and the four EN
   feeds publish 2–3 times a week, so a 15-minute cron produced no publications.
4. Secondary defect found in the same function: `is_obviously_off_topic()` ended
   with `and not has_messaging_signal(article)`, which is always `False` at that
   point of `check_relevance()` — the voice/video/email guard could never fire
   (dead code).

D-011 fixes both: Cyrillic signal terms (recall) plus a title-based off-topic
rule that can actually fire. `смс`/`чат-бот`/`мессендж` stories now reach the
LLM while generic «5G/LTE/камеры» CNews items are still skipped without a call.

Measured against reality, so expectations stay honest: of 25 real fresh RU
headlines fetched from `cnews-telecom`, `cnews-safe` and `anti-malware-news` on
2026-09-11, **0** mention the target ecosystem in the title. That is correct
filtering, not a defect — today those feeds carried 5G/LTE rollouts, cameras,
SAP CVEs, passkeys and AI stories. Two structural limits follow:

- `parse_feed()` has no description for most CNews `/news/line/` items (only
  `/news/top/` ones carry a lead), and the guard reads title + feed text only.
  An SMS angle that appears further down the article is therefore invisible to
  both the guard and the LLM. `content-review` (operator/SMS-fraud focus) is the
  productive RU feed; CNews is a low-yield supplement.
- If the volume stays too thin, the next step is a per-source decision rather
  than more keywords: skip the deterministic gate for narrow sources
  (e.g. `content-review`) and let the model judge. Not implemented — needs
  approval as D-012.

## Sources

21 declared, **17 enabled** (D-014 добавил `mef-news`, `mobilesquared`).
`sinch-blog`, `twilio-blog`, `infobip-blog`,
`gsma-newsroom`, `mef-news`, `mobilesquared` (en); `content-review`, `iksmedia`, `habr-cellular-news`,
`cnews-telecom`, `cnews-safe`, `cnews-biz`, `cnews-internet`, `securitylab-news`,
`securitylab-analytics`, `anti-malware-news`, `anti-malware-analytics` (ru).

Declared but disabled: `cnews-corp`, `securitylab-vulnerabilities`,
`anti-malware-press`, `nag-all`. Flip `enabled` in `src/telecom_news/config.py`
to activate — no other code change is needed.

Verification level per feed: `cnews-telecom`, `cnews-safe`, `anti-malware-news`
were fetched by the agent on 2026-09-11 and showed items dated 2026-09-11; the
other nine new feeds are **user-verified only** — the agent sandbox has no
outbound network (`curl` to any host fails), so they are NOT VERIFIED here.

## Security status

- **Token rotation: user confirmed a new token on 2026-09-11.** The revocation of
  the old one is user-attested; the agent cannot inspect BotFather state.
- No Telegram token exists in the repository: every blob of `master` and of
  `arena/01a08bb1-telecom-news` was scanned for the Bot API token pattern — clean.
- Runtime secrets stay outside the repo in `~/.config/telecom-news/env` (mode
  `600`). `scripts/check_secrets.sh` and the pre-commit hook check staged
  additions; `httpx` request-URL logging stays suppressed because Bot API URLs
  embed the token.
- Never paste a token into chat, issues, commits or logs. `TELEGRAM_CHAT_ID` is
  configured through the environment too, though it is not a secret.

## Session 2026-09-11 (evening): `diagnose` + языковая подсистема

**Почему канал молчит после 2026-09-10 18:03 — что проверено от этого
репозитория (удалённый доступ к машине пользователя у агента отсутствует):**

- Обе ветки, содержащие D-011, влиты в `master`: `11dcb2e` («M7+: add Russian
  sources and make the relevance guard Cyrillic-aware») — коммит от
  **2026-09-11T13:28:45Z**. Любой checkout, обновлённый до этого момента, всё
  ещё содержит Latin-only guard из M6, а он отбрасывал русские статьи без
  запроса к LLM (см. раздел выше). Это ровно объясняет окно «2026-09-10 вечер →
  2026-09-11».
- Supply-side факт, проверен веб-инструментом агента 2026-09-11 (в песочнице
  нет исходящей сети, поэтому подтверждение через внешний загрузчик страниц):
  в `cnews-telecom` (18:37 MSK) — «5G в Краснодаре/Казани/Самаре», камеры,
  спутники, чипы; в `content-review` (до 16:25 MSK) — Range Rover, МТС
  «нежелательные звонки» (голос), благотворительность, 5G-обзор, Apple, Uber;
  в `anti-malware-news` (18:25 MSK) — NGFW, eVTOL, passkeys, Android-троян,
  5G. **Ни одной статьи про SMS/messaging/OTP-мошенничество в этих лентах за
  сутки нет.**
- Следствие: cron с интервалом 15 минут — это частота *проверки* источников, а
  не частота публикаций. При 15 лентах и строгом SMS-фильтре паузы в
  несколько часов — ожидаемое поведение, а не сбой. Диагностика «почему
  молчит» теперь выполняет эту работу за оператора.

**Что добавлено в код:**

- `src/telecom_news/diagnostics.py` — разбор `data/logs/pipeline.log`
  (`parse_pipeline_log`), правила вердикта (`analyze`/`Facts`/`Finding`),
  рендер отчёта и опциональные пробы LM Studio и Bot API (`probe_llm`,
  `probe_telegram`, токен в вывод не попадает).
- CLI: `python -m telecom_news diagnose [--runs N] [--offline] [--json]`.
  Read-only; exit `1`, если найдена блокирующая причина. Различает:
  шедулер не запускается, нет доступа к LM Studio, нет секретов у процесса
  cron, `processed` не публикуются, недоступен Telegram, битые источники,
  «всё отфильтровано как нерелевантное», «в лентах нет нового».
- `Database.last_published()` и `Database.oldest_with_status(status)` —
  read-only помощники для диагностики.
- Тесты: `tests/test_diagnostics.py` (16), `tests/test_cli_diagnose.py` (6),
  дополнения в `tests/test_storage.py`. Проверено агентом 2026-09-11:
  **`pytest -q` → 167 passed** (было 143), `ruff check` и
  `ruff format --check` чисто.
- Документация: `docs/DESIGN_LANGUAGE_SUBSCRIPTIONS.md` (proposal),
  раздел `diagnose` в `docs/DEVELOPMENT.md`, команда в `README.md`.

**Языковая подсистема — статус:** спроектирована как отдельный milestone M8
(подписки в личке бота, `subscriber_languages` с набором языков на
пользователя, смена языка в любой момент, доставка отдельным сообщением на
каждый выбранный язык, ленивая генерация перевода в таблицу `renditions`).
Реализация не начата: нужны 4 решения пользователя (раздел «Открытые вопросы»
в `docs/DESIGN_LANGUAGE_SUBSCRIPTIONS.md`).

## Session 2026-09-11 (night): M8 — языки и подписки реализованы

Решения пользователя: гибрид «канал + личка бота»; на каждый язык — отдельное
сообщение; перевод генерируется лениво и кэшируется; по объёму публикаций —
«и расширить источники, и ослабить фильтр». Зафиксировано как D-012 и D-013.

**Реализовано (файлы и контракты):**

- `processors/relevance.py::check_relevance(..., use_gate=True)`; `config.SourceConfig.relevance_gate`
  (`strict`/`llm`). На `llm` переведены `content-review`, `anti-malware-news`,
  `anti-malware-analytics`, `securitylab-news`, `securitylab-analytics` (D-012).
- `config.py`: `target_langs` (env `TELECOM_NEWS_TARGET_LANGS`, старый
  `TELECOM_NEWS_TARGET_LANG` работает), `channel_chat_ids`
  (`TELEGRAM_CHAT_ID` + `TELEGRAM_CHAT_ID_<LANG>`), `SUBSCRIBER_MAX_AGE_HOURS`
  (24), `SUBSCRIBER_MAX_PER_CYCLE` (10), `SUBSCRIBER_MAX_ATTEMPTS` (3).
- `storage/database.py`: таблицы `renditions`, `subscribers`, `subscriber_languages`,
  `deliveries`, `bot_state` + методы рендеров, подписок, доставок, состояния бота,
  `recent_articles`, `last_published`, `oldest_with_status` (аддитивная миграция).
- `processors/renditions.py::ensure_rendition` — кэш «статья × язык»; языки каналов
  генерируются в `process`, языки подписчиков — лениво в `deliver`.
- `delivery/telegram.py`: локализованный `format_post(article, lang=..., summary=..., show_flag=...)`
  (подписи RU/EN, флаг страны), `send_message(..., reply_markup=...)`, `call()`,
  `get_updates`, `answer_callback_query`, `edit_message_reply_markup`,
  `TelegramError.status_code`/`.blocked_by_user`.
- `delivery/planner.py::plan_deliveries` — окно свежести, лимит на подписчика,
  лимит попыток, пропуск уже отправленного, порядок по id.
- `bot.py`: `/start` (сохраняет язык по локали Telegram — `ru*` → ru, иначе en),
  `/language`|`/lang`|`/язык`, `/status`, `/stop`, `/help`, кнопки
  `lang:toggle:<lang>` и `lang:done`; `handle_update` не требует сети, `poll_once`/`run_bot`
  выполняют long polling, offset хранится в `bot_state`.
- CLI: `publish` (по одному посту на язык, каналы из конфига, идемпотентность через
  `deliveries`, для до-M8 строк использует `llm_result.summary`, LLM не вызывает),
  `deliver [--dry-run] [--limit]`, `bot [--once] [--poll-timeout]` (единственный
  экземпляр через `data/bot.lock`). `run` = `collect → process → publish → deliver`.
- Документы: D-012/D-013 в `DECISIONS.md`, M8 в `ROADMAP.md`, раздел 3.12a в
  `ARCHITECTURE.md`, `DESIGN_LANGUAGE_SUBSCRIPTIONS.md` переведён в `implemented`.
- Проверено агентом 2026-09-11: **`pytest -q` → 195 passed** (было 167),
  `ruff check`/`ruff format --check` чисто, `git diff --check` чисто.
  Новые тесты: `tests/test_bot_and_languages.py` (17), `tests/test_cli_deliver.py` (5),
  мультиязычная публикация в `tests/test_cli_publish.py`, `D-012` в `tests/test_relevance.py`,
  языковые настройки в `tests/test_config.py`.

**Не проверено (NOT VERIFIED):** живой прогон `bot` в Telegram, живая отправка
`publish`/`deliver` реальным подписчикам, поведение `flock`/`bot.lock` в WSL,
расширение списка источников (решение «и то, и другое» ещё не выполнено —
кандидаты не подобраны и не проверены).

## Session 2026-09-11 (late): D-015 — фикс блокировки очереди, D-016 — каталог источников

**D-015 (исправление, которое просил пользователь).** `run` вызывал `recover`,
возвращавший в `new` **все** `error`-статьи, а `process` берёт самые старые `new`
с лимитом `--limit` (10 в `run_pipeline.sh`). Статьи с постоянно невалидным
ответом LLM сортировались первыми, каждый прогон заново занимали весь бюджет
обработки и снова падали в `error` — новые статьи не доходили до модели, канал
молчал бесконечно. Исправление: колонка `articles.attempts` (аддитивная
миграция), `Database.mark_error()` считает попытки, `reset_errors(max_attempts=3)`
не возвращает «запаркованные» статьи, `recover [--max-attempts N]`,
`diagnose` показывает `Parked errors` и подсказывает `recover --max-attempts 0`
(он же обнуляет счётчик). Миграция помечает строки, которые были в `error` до
появления счётчика, как исчерпавшие ретраи: иначе накопленный «хвост» ошибок
после обновления разом вернулся бы в очередь и снова перекрыл свежие статьи.
Регрессионный тест: 3 «отравленные» статьи + 1 свежая → после 4 прогонов свежая
`processed`, отравленные остаются `error`.

**D-016 (каталог источников).** `sources_catalog.py` (`kind`: `news`/`status`,
`gate`, `grade`, `verified`) + мерж в `config.SOURCES` только для news;
статус-страницы (`application/json`, `*.statuspage.io`, `/history.rss`) объявлены,
но не собираются — это не новости, и `feedparser` их не разбирает. Новые команды:
`sources [--kind all] [--json] [--verify]` и `sources import --csv <таблица>`.
Импортёр распознаёт русские заголовки таблицы, классифицирует строки по MIME,
отбрасывает «исключён/404/410/403/502/timeout», пропускает URL, уже объявленные
в `config.SOURCES`, и перезаписывает `sources_catalog.py`. Из таблицы перенесены
5 проверенных messaging-лент: `mef-news`, `mobilesquared`, `total-telecom`,
`simpletexting`, `textmagic` (реестр: 24 news-источника, 20 включённых).

**Почему не перенёс все 100 строк из скриншотов:** транскрипция URL из картинки
даёт недопустимый риск опечатки в рабочем конфиге, а агент не может проверить
эндпоинты (нет исходящей сети). Путь: выгрузить таблицу в CSV и выполнить
`sources import --csv sheet.csv --dry-run`, затем `sources import`,
`sources --kind all`, `sources --verify` (проверка всех лент на машине
пользователя), после чего посмотреть `git diff`.

Проверено агентом 2026-09-11: **`pytest -q` → 214 passed** (было 195),
`ruff check`/`ruff format --check` чисто, `git diff --check` чисто. Новый файл
тестов — `tests/test_source_import.py` (14 тестов).

## Session 2026-09-11 (late night): D-017/D-018 в master, D-019 в патче

**D-017/D-018 смержены.** Пользователь применил b64-патч, прогнал тесты
(**229 passed**), запушил ветку `d017-freshness-killswitch` и смержил PR #3 —
squash-коммит `102a6bb` (14 файлов, +349/−23). В тот же коммит попал
`.gitignore`-блок `data/backups/` и `data/pipeline.lock`: бэкап БД (в
`deliveries`/`subscribers` есть chat_id) и lock-файл случайно ушли в git, их
убрали из индекса. Позже пользователь добавил `data/*.lock` — под лок бота.

**Правда про env-файл.** Строки в `~/.config/telecom-news/env` были дописаны без
`export`, поэтому `scripts/run_pipeline.sh` (дочерний процесс) не видел ни
`TELECOM_NEWS_LIMIT=30`, ни `LMSTUDIO_*`: прогон брал дефолтные 10 статей, а
запуски из планировщика раньше получали отказы `localhost:1234`. После `sed`,
добавившего `export`, лимит заработал — 9 прогонов подряд обрабатывали по 30
статей с `exit_code=0`.

**Состояние на машине пользователя (2026-09-11 21:10 UTC, `diagnose`):**
published=48, new=88, skipped=599, error=0; последняя публикация 0.2 ч назад;
LM Studio OK (`qwen3-vl-8b-instruct`), Telegram OK (`@sms_telecom_news_bot`).

**D-019 (патч, ещё не в `master`).** Две правки: (1) `diagnose` больше не
считает сбоями старые `last_error` у источников, выключенных
`TELECOM_NEWS_DISABLED_SOURCES` (выключенная лента не опрашивается, поэтому
запись остаётся навсегда и отчёт вечно печатал «4 of 55 … failed»); такие
источники выводятся строкой `Disabled sources with stale errors (not counted)`.
(2) `bot.describe_update()` + `logging.info` в `poll_once`: каждый апдейт
печатает строку, `channel_post` помечается как игнорируемый — команда в канале
не подписывает, подписка живёт в личке с ботом. Проверено в песочнице:
**233 passed**, `ruff check`/`ruff format --check` чисто.

## Session 2026-09-12: D-020 — лимиты рассылки, окно свежести `publish`, `prune`

**Отправная точка — реальный вывод команд пользователя** (бот перезапущен и
логирует апдейты, PR #4 смержен, `master` = `d7d591e`, `deliver --dry-run`
запланировал 10 сообщений подписчику `1288967298`). Разбор этого вывода и кода
дал четыре дефекта, все закрыты одним решением D-020:

1. **Флуд.** `scripts/run_pipeline.sh` зовёт `run --limit ${TELECOM_NEWS_LIMIT:-10}`,
   в env пользователя `TELECOM_NEWS_LIMIT=30`, а `_cmd_run` передавал это число
   дальше в `publish` (лимит постов) и в `deliver` (заменял
   `SUBSCRIBER_MAX_PER_CYCLE`). Один 15-минутный цикл мог отправить до 30 постов
   в канал и до 30 личных сообщений подписчику — хотя `deliver --dry-run` с
   дефолтами показывал 10. Теперь `--limit` budgets только `collect`/`process`;
   лимиты рассылки — `PUBLISH_MAX_PER_CYCLE` (10) и `SUBSCRIBER_MAX_PER_CYCLE`
   (10), разово `run --max-posts` / `run --max-per-subscriber`.
2. **У `publish` не было окна свежести**, а кандидаты сортируются по возрастанию
   `id` — публикация начинала с самых старых строк. Добавлено
   `PUBLISH_MAX_AGE_HOURS` (48, `0` = без окна) + `publish --max-age-hours`;
   удержанные статьи печатаются явной строкой и остаются `processed`.
3. **`recent_articles` никогда не применял `since` в SQL**: условие дописывалось
   после `ORDER BY id`, получалось `ORDER BY id AND COALESCE(...) >= ?` — SQLite
   принимает это как выражение сортировки. Для `deliver` окно компенсировал
   `planner`, для `publish` не компенсировало ничего. Найден при написании
   теста, исправлен; возраст теперь определяется одним выражением
   `FRESHNESS_SQL = COALESCE(published_at, published_at_telegram, fetched_at)`,
   одинаково в SQL и в `planner.article_moment` (дата публикации важнее даты
   сбора: лента, отдавшая запись 2021 года сегодня, — это старые новости).
   Строки без единой даты не отсекаются: отсутствие даты не доказательство
   возраста (правило D-017).
4. **Старую очередь было нечем почистить**: порог D-017 работает только на входе
   в `collect`, а 215 из 517 `new`-строк пользователя были старше 30 дней и
   сортировались первыми. Добавлена команда `prune [--max-age-days N]
   [--dry-run]`: удаляет только `new` с известной датой публикации (плюс их
   `renditions`/`deliveries`), `skipped`/`published` не трогает. Вернуться строки
   не могут — `collect` отбросит их снова.

`diagnose` теперь различает «старое, поэтому не публикуется» и «публикация
сломана»: `stale-processed` и `stale-queue` — предупреждения с подсказками
(`PUBLISH_MAX_AGE_HOURS`, `prune --max-age-days N --dry-run`), а блокирующий
`stuck-processed` считается только по свежим `processed`-строкам.

Проверено агентом 2026-09-12 в песочнице: **`pytest -q` → 265 passed** (было
233), `ruff check` и `ruff format --check` чисто, `git diff --check` чисто,
`telecom_news --help`/`prune --help`/`run --help` и `prune --dry-run` на пустой
БД работают. **NOT VERIFIED:** живой прогон на машине пользователя (реальные
`publish`/`deliver`/`prune` против `data/news.db` и Telegram).

**Что осталось непонятным по выводу пользователя (нужна его машина):** в логе бота
`/start` и кнопка языка приходят из чата `555001`, а в `subscribers` одна строка
`1288967298` с `username='sms_telecom_news_bot'` (ник самого бота, не
пользователя). Похоже на строку, созданную не живым `/start` (демо-апдейты
1001–1003 или ручная вставка). Пока это не проверено, рассылка может идти не в
тот чат.

## Still open

- **Проверить подписчика и сделать первую реальную рассылку** (на машине
  пользователя): `/start` в личке с ботом → строка в `subscribers` с реальным
  `chat_id` → `deliver --limit 2`. NOT VERIFIED.
- Live run of `run` after D-011 — the published volume must be re-measured; the
  earlier figure (23 published, 29 skipped, 0 error) predates this change and
  came from RU feeds that were being skipped. NOT VERIFIED.
- **`diagnose` on the user's machine is the fastest way to name the cause** of an
  empty channel; the user's checkout, cron/статус Task Scheduler and LM Studio
  state are not visible to the agent server-side. NOT VERIFIED.
- D-012 (per-source bypass of the deterministic gate for narrow feeds) is still
  a proposal; it changes LLM cost per run, so it needs the user's approval.
- M8: живой прогон бота и рассылки в Telegram не выполнялся; расширение списка
  источников (часть решения «и то, и другое») не начато — нужен подбор и проверка
  фидов с фокусом на SMS/messaging.
- Бэклог на 2026-09-11 21:10 UTC — 88 статей `new`, дренаж по 30 за прогон
  (нужно ~3 прогона); после этого `process` печатает «Nothing to process».
  Сколько из них старше 30 дней — покажет `prune --dry-run` (D-020): такие
  строки сортируются первыми и съедают бюджет обработки каждого прогона.
- Бот живёт только пока запущен процесс `python -m telecom_news bot`
  (сейчас — `nohup`; для рестарта после перезагрузки WSL нужен Task Scheduler
  или systemd). `data/bot.lock` держит единственный экземпляр.
- ~~Риск «`recover` возвращает все `error`-статьи и блокирует очередь»~~ — закрыт
  D-015 (счётчик `attempts`, `recover --max-attempts`, `Parked errors` в
  `diagnose`); пункт оставался в этом списке по ошибке.
- **Качество текста поста (найдено в `deliver --dry-run` пользователя, не
  исправлено):** статьи 214 и 219 идут ru-подписчику с английским саммари —
  `renditions._script_mismatch()` только логирует warning и сохраняет рендер,
  ретрая нет. Заголовки не переводятся никогда (`format_post` берёт
  `article.title`), поэтому пост выглядит как «английский заголовок + русский
  текст». Категория печатается сырым слагом («Категория: product_service»).
- **Ссылка «Источник» может вести на картинку:** у статьи 55 в dry-run
  `url = https://i.content-review.com/…jpg`. `parse_feed` берёт `entry.link` как
  есть, sanity-проверки URL нет. Сколько таких строк — покажет запрос к БД
  пользователя (`select id, source_id, url from articles where url like '%.jpg'`).
- **Нет CI:** `.github/` отсутствует, тесты и ruff запускаются только локально
  (или в песочнице агента).
- `doctor` against live dependencies (LM Studio, Telegram, real feeds) — mock
  coverage only.
- Interrupted-run recovery required by M7 (kill mid-run, rerun without duplicates
  or loss) — covered by tests and indirectly by two transient DNS failures in
  WSL; no kill test recorded.
- Repeated-error notifications — not implemented.
- Cadence: cron runs every 15 minutes, ROADMAP M5 states a daily cycle. Decide
  whether 15 minutes is intended; at that rate `skipped` items also cost LLM calls.
- Documentation debt: `docs/PROJECT_STATE.md` and `docs/SESSION_HANDOFF.md` still
  describe the end of planning («M0 not started, no code at all»), `docs/ROADMAP.md`
  carries no milestone statuses, `docs/TECHNICAL_SPEC.md` specifies a superseded
  M0 (click CLI in `main.py`), `docs/TEST_SPEC.md` is a 3-line stub. Dead code from
  that superseded spec is still shipped: `src/telecom_news/main.py` (imported by
  nothing) and `logging_setup.py` (used only by its own test; the CLI uses
  `logging_config.py`).
- `AGENTS.md` states the project root as `/home/joe/Projects/telecom-news`.

## References

- `docs/DECISIONS.md` — D-007…D-011 (sources, Telegram delivery, Russian feeds).
- `docs/SECURITY.md` — token incident and rotation runbook.
- `docs/SKILLS/telegram-token-rotation.md`, `docs/SKILLS/m7-doctor-and-recovery.md`.
- `docs/DEVELOPMENT.md` — environment, scheduler, sources, hooks.
