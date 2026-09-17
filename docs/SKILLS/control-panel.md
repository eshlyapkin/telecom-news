# Control panel — what each tab changes, and where it lands

Operator runbook for `python -m telecom_news serve` (127.0.0.1:8765).
**No authentication by design** (D-021): bind to localhost only, never expose it.

Everything the panel changes is written to a file under `data/`, not held in the
server's memory. That is why a change reaches the pipeline that runs from the
scheduler as a separate process, and why nothing needs restarting.

```bash
systemctl --user restart telecom-news-serve   # after a code update
curl -s http://127.0.0.1:8765/api/health      # version, languages, channel targets
```

## Overview

| Control | Effect | File |
|---|---|---|
| Pause publish (project / global) | `publish` stops posting; `--dry-run` still previews | `data/projects/registry.json` |
| Run now | one collect/process/publish/deliver cycle inside the API process | — |
| Channel languages | one post per language; both may share one chat id | `data/channel_languages.json` |
| Archive pace | how fast material older than the publish window drips into the channel | `data/publish_settings.json` |

"Use env value" deletes the override file, handing control back to the
environment — `TELECOM_NEWS_TARGET_LANGS` for the languages,
`PUBLISH_BACKLOG_PER_DAY` / `PUBLISH_BACKLOG_MIN_GAP_HOURS` for the pace. Until
somebody presses Apply there is no file at all and the card says so. A language with no chat id publishes nothing at all,
and the card names it.

Enabling a language does **not** strand older articles: the next `process` renders
the missing rendition for anything still inside the publish window.

**Archive pace** (D-031) is the evergreen lane: an article older than
`PUBLISH_MAX_AGE_HOURS` is reference material worth posting whatever its date, so
it is dripped — N a day, never two closer than Y hours, one per cycle, after the
fresh candidates. `0` a day holds the archive back entirely, which is how the
pipeline behaved before the lane existed. The queue is ordered by category:
`network_protocol` and `technology` first, `regulation` last.

## Queue / Published

Queue is what is waiting. Published is what actually went out: the post text per
language, whether the channel copy was sent, its Telegram message id, and how many
subscribers received it. Use it to check a rendition before blaming the model.

## Sources

Add (RSS feed, or **sitemap** for outlets that publish no feed), delete, enable.

- A custom feed lands in `data/custom_sources.json`.
- Deleting a built-in writes a soft-delete to `data/removed_sources.json`; the
  declaration in code is never edited, so adding the id back restores it.
- Toggles land in `data/disabled_sources.json`.

A sitemap source reads the list a site publishes for search engines. Entries
without a publication date are ignored — a plain archive cannot be told apart
from fresh news.

## Discovery

See `docs/SKILLS/source-discovery.md`.

## AI rules

The system prompt is **editorial policy only** — what counts as relevant. The JSON
response contract is appended by code on every call and must not be written here:
a prompt without it used to make the model answer in prose, and every article that
reached it was parked as `error` (D-023).

Term lists **widen** the built-in ones; a line deleted here comes back. Narrowing
needs a code change in `processors/relevance.py` (D-022).

Editing the rules also changes what discovery searches for (D-027).

## When the channel goes quiet

In this order — each has actually been the cause:

```bash
tail -40 data/logs/pipeline.log          # does the scheduled run reach publish?
.venv/bin/python -m telecom_news diagnose
curl -s http://127.0.0.1:8765/api/ops/status | python -m json.tool
```

1. Is publish paused (banner on Overview)?
2. Does the scheduled run have credentials? `run_pipeline.sh` reads
   `~/.config/telecom-news/env`; that file is a systemd EnvironmentFile, so
   `VAR=value`, never `export VAR=value`.
3. Are articles being parked as `error`? That is the model's reply failing to
   parse — check the AI rules prompt.
4. Only then suspect the relevance filter: a strict policy legitimately publishes
   less.
