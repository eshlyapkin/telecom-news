# Technical Specification – M0 (Foundation)

## Scope
The first milestone (M0) establishes the minimal technical foundation for the telecom‑news application. It does **not** include any real news collection, LLM integration, or Telegram delivery. The goal is to provide a clean, testable Python package that can be installed, executed, and verified in isolation.

## Files and Structure
- `pyproject.toml` – project metadata, Python 3.10+ requirement, and dev‑dependency `pytest`.
- `src/telecom_news/__init__.py` – package marker.
- `src/telecom_news/main.py` – CLI entry point exposing `--help`.
- `src/telecom_news/config.py` – configuration placeholders (paths, env‑vars, LM Studio endpoint – no real calls).
- `src/telecom_news/logging_setup.py` – standard Python logging configuration.
- `src/telecom_news/models.py` – minimal `Article` dataclass (no persistence).
- `tests/test_smoke.py` – smoke test that imports the package, creates an `Article`, and verifies CLI help output.

## Implementation Details
1. **Python Packaging** – Use `setuptools` in `pyproject.toml` with `packages=find:`.
2. **CLI** – Implement a `click`‑based CLI in `main.py` with sub‑commands `run` and `status` as placeholders.
3. **Configuration** – `config.py` should expose a `Config` dataclass with default values; environment variables are read via `os.getenv` but not required.
4. **Logging** – `logging_setup.py` configures a root logger with level `INFO` and a console handler.
5. **Article Model** – `Article` dataclass with fields: `title`, `url`, `published_at`, `content_hash`.
6. **Testing** – `tests/test_smoke.py`:
   - Import `telecom_news` package.
   - Instantiate `Article` with dummy data.
   - Run `python -m telecom_news --help` via `subprocess` and assert expected help text.

## Testing Strategy
- **Unit Tests** – Verify that `Article` can be instantiated and that its `content_hash` is deterministic.
- **Integration Test** – Run the CLI with `--help` and check that the output contains the expected command names.
- **Environment Isolation** – Tests should run in a fresh virtual environment; no external network or database access.

## Success Criteria
- `pip install -e .` succeeds in a clean virtual environment.
- `python -m telecom_news --help` prints a help message listing at least the `run` and `status` commands.
- `pytest` passes all tests with 100 % coverage for the smoke test.
- The repository is in a clean Git state with all files committed.

## Next Steps

## M1 – One News Source

## M2 – Storage and Deduplication

The second milestone focuses on collecting news from a single RSS feed (Sinch) and preparing content for publication. It introduces the first real data ingestion, localization, persistence, and Telegram delivery components.

### Scope
- Implement an RSS collector that fetches articles from `https://sinch.com/news/rss`.
- Normalize and store articles in a local SQLite database.
- Support two target languages (`en`, `ru`) and generate a publication in the user‑selected language.
- Publish the formatted article to a Telegram channel via Bot API.
- Ensure the system can be extended to additional RSS/HTTP sources in future releases.

### Files and Structure
- `src/telecom_news/collectors/rss.py` – RSS collector implementation.
- `src/telecom_news/storage/database.py` – SQLite wrapper with schema for `articles` and `backups`.
- `src/telecom_news/localization.py` – language selection and translation utilities.
- `src/telecom_news/delivery/telegram.py` – Telegram Bot API client.
- `src/telecom_news/cli.py` – CLI commands `collect`, `process`, `publish`.
- `tests/test_collectors.py` – unit tests for RSS parsing and deduplication.
- `tests/test_storage.py` – integration tests for SQLite persistence.
- `tests/test_telegram.py` – mock tests for Telegram publishing.

### Implementation Details
1. **RSS Collector** – Use `feedparser` to parse the Sinch RSS feed. Extract title, link, published date, and content. Compute a deterministic `content_hash` (e.g., SHA‑256 of title+link+content). Store new articles only if the hash is not already present.
2. **Localization** – Store the original language of the article. The target language is set via a configuration option (`TARGET_LANG`). If the source language differs, use the LLM client (M3) to translate; for MVP v1.0, a simple stub that returns the original text is acceptable.
3. **SQLite Storage** – Create a table `articles` with columns: `id`, `title`, `url`, `published_at`, `content_hash`, `source_lang`, `target_lang`, `status` (`new`, `processed`, `published`). Provide CRUD helpers in `database.py`.
4. **Telegram Publishing** – Use `requests` to POST to `https://api.telegram.org/bot<token>/sendMessage`. The token and chat_id are read from environment variables (`TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`). The message format includes title, summary, link, and language.
5. **CLI** – Add sub‑commands: `collect` (fetch and store), `publish` (send new processed articles), and `status` (list article statuses).

### Testing Strategy
- **Unit Tests** – Verify RSS parsing, hash calculation, and deduplication logic.
- **Integration Tests** – Run `collect` against a local fixture of the Sinch RSS feed, confirm records in SQLite, and that duplicate runs do not create new rows.
- **Localization Tests** – Ensure that the target language setting is respected and that the publication text is generated in the correct language.
- **Telegram Tests** – Use a mock server or `responses` library to intercept HTTP calls and assert the payload structure.
- **End‑to‑End Test** – In a temporary directory, run `collect`, `publish`, and verify that the SQLite database contains a `published` record and that the mock Telegram endpoint received the message.

### Success Criteria
- `collect` fetches at least one article from the Sinch RSS feed and stores it in SQLite.
- Duplicate runs of `collect` do not insert duplicate rows.
- `publish` sends a correctly formatted message to Telegram (verified via mock) and updates the article status to `published`.
- The system correctly handles both `en` and `ru` target languages.
- All tests pass with coverage above 80 % for the new modules.
- No sensitive data (Telegram token, chat ID) is committed to the repository.

### Timeline
- This point will be considered complete once the above success criteria are met and the repository is in a clean Git state.

### Roadmap Context
- According to `docs/ROADMAP.md`, there are 8 milestones (M0–M7). This is the second milestone (M1). The next milestone after this will be M2 – Storage and Deduplication.

## M2 – Storage and Deduplication

The third milestone focuses on persisting articles and preventing duplicate processing. It introduces a local SQLite database, status tracking, and deduplication logic.

## M3 – LLM Processing

The third milestone focuses on integrating a local LLM for relevance filtering, classification, summarization, and translation. It introduces an LM Studio client, prompt templates, and result handling.

### Scope
- Implement an LM Studio HTTP client (`src/telecom_news/llm/client.py`).
- Provide a `LLMUnavailableError` exception.
- Add relevance filtering for SMS/messaging‑related news.
- Classify article type (technology, vendor, aggregator, carrier, product_service, partnership, ma_investment, security_antifraud, regulation).
- Summarize content in the target language; if source language differs, translate via the LLM.
- Store the raw LLM JSON response in the database (`llm_result` column).
- Ensure the pipeline does not crash if the LLM is unavailable.

### Files and Structure
- `src/telecom_news/llm/client.py` – HTTP client for LM Studio.
- `src/telecom_news/processors/relevance.py` – relevance check.
- `src/telecom_news/processors/summarize.py` – summarization and translation.
- `src/telecom_news/processors/classify.py` – article type classification.
- `src/telecom_news/cli.py` – add `process` command.
- `tests/test_llm_client.py` – mock server tests.
- `tests/test_relevance.py` – relevance logic tests.
- `tests/test_summarize.py` – summarization tests.
- `tests/test_classify.py` – classification tests.

### Implementation Details
1. **Client** – Use `httpx` with retries and timeout; base URL defaults to `http://localhost:1234/v1`.
2. **LLMUnavailableError** – Raised when the client cannot reach the endpoint.
3. **Relevance** – Send a prompt asking if the article is about SMS/messaging; parse boolean.
4. **Classification** – Prompt the LLM to return one of the predefined categories.
5. **Summarization** – Prompt for a concise summary in the target language.
6. **Translation** – If source language ≠ target, include translation prompt.
7. **Result Storage** – Persist the full LLM JSON in `llm_result` column.

### Testing Strategy
- **Unit Tests** – Verify client request formation, retry logic, and error handling.
- **Mock Server Tests** – Use a local HTTP server to return canned LLM responses.
- **Relevance Tests** – Confirm that irrelevant articles are flagged as `skipped`.
- **Classification Tests** – Ensure correct category mapping.
- **Summarization Tests** – Check that summaries are non‑empty and in target language.
- **End‑to‑End Tests** – Run `process` on a fixture article and verify status transitions and database updates.

### Success Criteria
- The LM client can connect to a running LM Studio instance and return a JSON response.
- Articles are correctly marked as `skipped` when irrelevant.
- Classification returns one of the allowed categories.
- Summaries are generated and stored.
- The pipeline continues gracefully when the LLM is unavailable.
- All tests pass with coverage above 80 % for the LLM modules.
- No sensitive data is stored in the database.

### Timeline
- This point will be considered complete once the above success criteria are met and the repository is in a clean Git state.

---

*This section is added to the technical specification for milestone M3.*

## M4 – Telegram Publishing

The fourth milestone focuses on publishing processed articles to Telegram. It introduces a Bot API client, message formatting, and dry‑run capabilities.

### Scope
- Implement a Telegram client that sends messages via the Bot API.
- Store the bot token and chat ID in environment variables (`TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`).
- Provide a `--dry-run` flag to preview the message without sending.
- Ensure idempotent publishing: already published articles are not sent again.
- Add a CLI command `publish` that processes new articles and sends them.

### Files and Structure
- `src/telecom_news/delivery/telegram.py` – Bot API client.
- `src/telecom_news/cli.py` – add `publish` command.
- `tests/test_telegram.py` – mock tests for Telegram publishing.

### Implementation Details
1. **Client** – Use `requests` to POST to `https://api.telegram.org/bot<token>/sendMessage`.
2. **Dry‑run** – When enabled, log the message payload instead of sending.
3. **Idempotency** – Mark articles as `published` after successful send; skip if already published.
4. **Error Handling** – Log failures and retry logic.

### Testing Strategy
- **Unit Tests** – Verify message payload construction.
- **Mock Tests** – Intercept HTTP calls and assert correct endpoint and payload.
- **Dry‑run Test** – Ensure no network call is made and output is logged.
- **Idempotency Test** – Publish twice and confirm only one API call.

### Success Criteria
- Telegram messages are sent correctly when token and chat ID are set.
- Dry‑run produces the expected output without network activity.
- No duplicate messages are sent for the same article.
- All tests pass with coverage above 80 % for the Telegram module.
- Sensitive credentials are not committed.

### Timeline
- This point will be considered complete once the above success criteria are met and the repository is in a clean Git state.

---

*This section is added to the technical specification for milestone M4.*

## M5 – Automation

The fifth milestone focuses on automating the daily pipeline execution. It introduces a scheduler, retry logic, logging, and exit code handling.

### Scope
- Implement a simple scheduler that triggers `python -m telecom_news run` at a configurable interval (e.g., daily at 02:00 UTC).
- Add retry mechanisms for transient errors in each pipeline stage (collect, process, publish).
- Log each run to a local file (`data/logs/run-<timestamp>.log`).
- Define clear exit codes: 0 for success, non‑zero for specific failure types (source error, LLM error, Telegram error).
- Provide a CLI command `run` that starts the scheduled or one‑off execution.
- Ensure the system can be stopped gracefully and resumes correctly after a crash.

### Files and Structure
- `src/telecom_news/scheduler.py` – scheduling logic.
- `src/telecom_news/cli.py` – add `run` command.
- `src/telecom_news/utils/retry.py` – retry decorator.
- `src/telecom_news/utils/logger.py` – structured logging.
- `tests/test_scheduler.py` – test scheduling and retry behavior.
- `tests/test_run_cli.py` – test run command and exit codes.

### Implementation Details
1. **Scheduler** – Use `schedule` or `APScheduler` to schedule daily runs; fallback to simple `time.sleep` loop for minimal dependencies.
2. **Retry** – Decorate each stage with a retry decorator that retries up to 3 times with exponential backoff.
3. **Logging** – Use `logging` module with file handler; rotate logs daily.
4. **Exit Codes** – Map exceptions to exit codes: 1 for source errors, 2 for LLM errors, 3 for Telegram errors, 4 for unknown.
5. **Graceful Stop** – Handle SIGINT/SIGTERM to finish current stage and exit cleanly.

### Testing Strategy
- **Unit Tests** – Verify retry decorator retries on exception and stops after max attempts.
- **Scheduler Tests** – Mock time to ensure `run` is called at scheduled times.
- **CLI Tests** – Run `python -m telecom_news run` and check exit code and log creation.
- **Integration Tests** – Simulate a full pipeline run with mocked external services and confirm exit code 0.

### Success Criteria
- Scheduler triggers the pipeline at the configured time without manual intervention.
- Retries occur on transient failures and succeed or fail after max attempts.
- Logs are written to `data/logs/` with timestamps.
- Exit codes correctly reflect the type of failure.
- All tests pass with coverage above 80 % for automation modules.
- The system can recover from crashes and resume next scheduled run.

### Timeline
- This point will be considered complete once the above success criteria are met and the repository is in a clean Git state.

---

*This section is added to the technical specification for milestone M5.*

## M6 – Multiple Sources

The sixth milestone focuses on expanding the set of real news sources with isolation of errors. It introduces additional RSS/HTTP collectors, source configuration, health status, and graceful failure handling.

### Scope
- Add support for multiple RSS/HTTP sources (e.g., additional RSS feeds, APIs, optional scraping).
- Provide a configuration schema to enable/disable sources and set priority.
- Implement a health check for each source (last successful/failed fetch timestamp).
- Ensure that a failure in one source does not halt the entire pipeline.
- Update the CLI to include a `status` command that reports health per source.

### Files and Structure
- `src/telecom_news/collectors/multi.py` – orchestrates multiple collectors.
- `src/telecom_news/config.py` – extend configuration to include source list and enabled flag.
- `src/telecom_news/cli.py` – add `status` command for source health.
- `tests/test_multi_collectors.py` – tests for multi-source orchestration and error isolation.
- `tests/test_source_status.py` – tests for health reporting.

### Implementation Details
1. **Source Configuration** – Define a list of source objects with fields: `id`, `type` (rss/api), `url`, `enabled`, `priority`.
2. **Collector Orchestration** – Iterate over enabled sources, invoking the appropriate collector; catch and log exceptions per source.
3. **Health Tracking** – Store last success/failure timestamps in a separate table or in-memory cache; expose via CLI.
4. **Error Isolation** – Use try/except around each source call; continue to next source on failure.
5. **Priority** – Process higher priority sources first; allow configuration to adjust.

### Testing Strategy
- **Unit Tests** – Verify that the orchestrator skips disabled sources and handles exceptions.
- **Integration Tests** – Simulate multiple sources, including one that raises an error, and confirm that other sources still collect.
- **Health Tests** – Check that the status command reports correct timestamps and enabled flags.
- **End‑to‑End Tests** – Run the full pipeline with multiple sources and ensure no duplicate processing and correct status transitions.

### Success Criteria
- The system can collect from at least three distinct sources without manual intervention.
- A failure in one source does not prevent collection from others.
- The `status` command accurately reflects each source’s health.
- All tests pass with coverage above 80 % for the multi-source modules.
- No sensitive data is stored in the database.

### Timeline
- This point will be considered complete once the above success criteria are met and the repository is in a clean Git state.

---

*This section is added to the technical specification for milestone M6.*

## M7 – Hardening

The seventh milestone focuses on operational reliability and diagnostics. It introduces recovery mechanisms, backups, rate limiting, health checks, and a diagnostic CLI.

### Scope
- Implement graceful recovery from crashes and incomplete transactions.
- Add SQLite backup and restore utilities.
- Enforce rate limits for sources, LM Studio, and Telegram API.
- Provide a `doctor` CLI command that checks connectivity to all external services and database health.
- Document operational procedures and create a README section for hardening.

### Files and Structure
- `src/telecom_news/utils/backup.py` – backup/restore helpers.
- `src/telecom_news/utils/rate_limiter.py` – simple token bucket implementation.
- `src/telecom_news/cli.py` – add `doctor` command.
- `src/telecom_news/cli.py` – add `backup` and `restore` commands.
- `tests/test_backup.py` – test backup/restore logic.
- `tests/test_rate_limiter.py` – test rate limiting behavior.
- `tests/test_doctor.py` – test diagnostic command.

### Implementation Details
1. **Recovery** – Wrap each pipeline stage in a transaction; on exception, roll back and log.
2. **Backup** – Copy the SQLite file to a timestamped backup directory; provide restore by replacing the live DB.
3. **Rate Limiting** – Use a token bucket per service; refill at configured intervals.
4. **Doctor** – Attempt test connections to the database, LM Studio endpoint, and Telegram API; report status.
5. **CLI** – Expose `backup`, `restore`, and `doctor` commands.

### Testing Strategy
- **Unit Tests** – Verify backup file creation, restore overwrites, and rate limiter token consumption.
- **Integration Tests** – Simulate a crash during a transaction and confirm rollback.
- **Doctor Tests** – Mock external services and ensure the command reports success/failure.

### Success Criteria
- The system recovers from crashes without data loss.
- Backups are created and can be restored.
- Rate limits prevent exceeding API quotas.
- The `doctor` command reports all services as healthy or provides clear error messages.
- All tests pass with coverage above 80 % for hardening modules.
- No sensitive data is exposed in logs or backups.

### Timeline
- This point will be considered complete once the above success criteria are met and the repository is in a clean Git state.

---

*This section is added to the technical specification for milestone M7.*

### Scope
- Design a SQLite schema for articles with fields: `id`, `title`, `url`, `published_at`, `content_hash`, `source_lang`, `target_lang`, `status` (`new`, `processed`, `published`).
- Implement CRUD helpers in `src/telecom_news/storage/database.py`.
- Add a deduplication layer that checks `content_hash` before inserting.
- Update article status transitions: `new → processed → published`.
- Ensure the system can recover from crashes without data loss.

### Files and Structure
- `src/telecom_news/storage/database.py` – SQLite wrapper and schema.
- `src/telecom_news/storage/dedup.py` – deduplication logic.
- `src/telecom_news/cli.py` – add `status` command to view article statuses.
- `tests/test_storage.py` – integration tests for database operations.
- `tests/test_dedup.py` – tests for deduplication behavior.

### Implementation Details
1. **Schema** – Use `sqlite3` with a single table `articles`. Add a unique constraint on `content_hash`.
2. **CRUD** – Provide functions: `add_article`, `get_article_by_hash`, `update_status`, `list_articles_by_status`.
3. **Deduplication** – Before inserting, query by `content_hash`; if exists, skip insertion.
4. **Status Flow** – After `collect`, set status to `new`. After `process`, set to `processed`. After `publish`, set to `published`.
5. **Recovery** – Use transactions and `PRAGMA journal_mode=WAL` for durability.

### Testing Strategy
- **Unit Tests** – Verify schema creation, unique constraint, and CRUD functions.
- **Integration Tests** – Simulate a full pipeline: collect, process, publish, and check status transitions.
- **Deduplication Tests** – Insert duplicate articles and assert that only one record exists.
- **Recovery Tests** – Simulate a crash by raising an exception during a transaction and ensure no partial writes.

### Success Criteria
- Database schema is created without errors.
- Duplicate articles are not stored.
- Status transitions occur correctly and are persisted.
- All tests pass with coverage above 80 % for storage modules.
- No sensitive data is stored in the database.

### Timeline
- This point will be considered complete once the above success criteria are met and the repository is in a clean Git state.

---

*This section is added to the technical specification for milestone M2.*

---

*This section is added to the technical specification for milestone M1.*
- After M0 is completed, the next milestone is **M1 – One News Source**.
- According to the roadmap, there are **8 milestones** (M0–M7) in total.

---

*This document is generated for the first milestone (M0) and will be updated as subsequent milestones are implemented.*
