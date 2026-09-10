# Skill: M7 doctor and recovery

## Purpose

Verify external dependencies before an automated run and recover safely after
transient failures or an interrupted SQLite pipeline.

## Current status

`doctor` is implemented as a read/check command. It performs live checks for the
configured database, Telegram Bot API, LM Studio and enabled RSS sources. It
returns `0` only when all checks pass and `1` when at least one check fails.

## Required checks for `doctor`

- SQLite path exists or can be created;
- database schema is readable and writable;
- every enabled RSS source resolves DNS and returns an acceptable HTTP response;
- LM Studio `/v1/models` is reachable and has a usable model;
- Telegram Bot API is reachable without logging the bot token;
- `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` are present;
- the scheduler log directory is writable;
- source health records are readable;
- no overlapping pipeline lock is held.

## Recovery rules

- A source failure must not delete or reset existing articles.
- A failed Telegram request leaves the article `processed` for retry.
- A failed LLM request leaves unprocessed work available for the next run.
- A process interrupted during collection or processing must be safe to rerun;
  URL/hash deduplication and status transitions prevent duplicates.
- Backups must be created outside the live SQLite write path and tested by
  restoring into a temporary database.

## Verification commands after implementation

```bash
.venv/bin/python -m telecom_news doctor
.venv/bin/python -m pytest -q
.venv/bin/ruff check .
.venv/bin/ruff format --check .
```
