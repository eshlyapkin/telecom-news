# TEST SPEC — telecom-news

How the suite is organised and what it guarantees. Run it with `pytest -q` from
the repository root; nothing else is needed.

## The suite is hermetic

`tests/conftest.py` has one autouse fixture that, for **every** test:

- points `TELECOM_NEWS_DATA_DIR` at a fresh per-test directory;
- deletes the project's environment variables (`TELECOM_NEWS_*`, `TELEGRAM_*`,
  `LMSTUDIO_*`, `LOG_LEVEL`, `SUBSCRIBER_MAX_*`, `PUBLISH_MAX_*`);
- resets the process-global `config.SOURCES` to its declared baseline before and
  after the test.

This exists because the suite used to read the operator's live `data/`, and
whatever the control panel had last written decided whether tests passed — a
hand-edited `ai_rules.json` failed the relevance tests, a language chosen in the
panel failed seven configuration tests, and `git push` needed
`env -u TELECOM_NEWS_DISABLED_SOURCES` (D-026).

**Consequences for writing tests:** a test that needs a config value sets it
itself; a test asserting a default really tests the default; no test may read
`data/`.

## No network, no LLM, no Telegram

- HTTP goes through `httpx.MockTransport`, passed in as `client=`.
- The LLM is a small fake returning canned JSON.
- Telegram sends are asserted against a fake transport, never a real bot.
- The one test that runs a shell script builds a sandbox with a stub
  `.venv/bin/python` that only reports its environment.

## Map

| Area | Files |
|---|---|
| Config, languages, channel targets | `test_config.py`, `test_channel_languages.py` |
| Collecting | `test_collectors.py`, `test_collector_sitemap.py`, `test_normalize.py`, `test_freshness.py` |
| Storage, dedup | `test_storage.py`, `test_dedup.py`, `test_models.py` |
| Relevance, summaries, renditions | `test_relevance.py`, `test_llm_client.py`, `test_bot_and_languages.py` |
| CLI stages | `test_cli_run.py`, `test_cli_process.py`, `test_cli_publish.py`, `test_cli_deliver.py`, `test_cli_prune.py`, `test_cli_storage.py`, `test_cli_backup.py` |
| Ops, diagnostics | `test_diagnostics.py`, `test_cli_diagnose.py`, `test_doctor.py`, `test_source_health.py` |
| Bot, subscribers | `test_bot_lock_and_subscribers.py`, `test_bot_and_languages.py` |
| Sources registry | `test_source_import.py`, `test_api_m9d_sources_crud.py` |
| Control panel API | `test_api_m9b_panel.py`, `test_api_m9c_run_rules.py`, `test_api_published.py`, `test_api_projects.py`, `test_projects.py` |
| Discovery | `test_source_discovery.py` |
| Scheduler scripts, rendition backfill | `test_pipeline_env_and_backfill.py` |
| Logging, smoke | `test_logging.py`, `test_smoke.py`, `test_telegram.py` |

## What a test is expected to pin

A regression that reached production gets a test that states the fact, not the
implementation — for example "an English post must not keep the Russian
headline", "a saved term list must not shrink the built-in one", "a scheduled run
must see the env file". Those tests are the reason the invariants in AGENTS.md
stay true.

## Before pushing

`git push` runs the full suite through `.githooks/pre-push`. If it is red, fix
the code — `--no-verify` is not a remedy.
