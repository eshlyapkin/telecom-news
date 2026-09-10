#!/usr/bin/env bash
# Run one pipeline cycle. Scheduler (cron/Task Scheduler) invokes this script.
set -u

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
mkdir -p data/logs

# Avoid overlapping runs when collection or LM Studio takes longer than the interval.
exec 9>"data/pipeline.lock"
flock -n 9 || exit 0

LOG_FILE="data/logs/pipeline.log"
{
  printf '\n===== %s =====\n' "$(date --iso-8601=seconds)"
  .venv/bin/python -m telecom_news run --limit "${TELECOM_NEWS_LIMIT:-10}"
  printf 'exit_code=%s\n' "$?"
} >>"$LOG_FILE" 2>&1
