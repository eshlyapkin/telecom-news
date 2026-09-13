#!/usr/bin/env bash
# Run one pipeline cycle. Scheduler (cron/Task Scheduler) invokes this script.
set -u

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
mkdir -p data/logs

# Load the same configuration the serve unit gets through systemd's
# EnvironmentFile. The scheduler invokes this script from a non-login shell that
# has none of it, so until 2026-09-13 every scheduled run ended its publish stage
# with "TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are required" and exit 2 — the
# channel stayed silent while collect/process looked healthy.
#
# The file is the configuration (it wins over anything inherited), exactly as it
# does for the serve unit. Point TELECOM_NEWS_ENV_FILE elsewhere to use another
# one; set it to a non-existent path to run on the caller's environment alone.
ENV_FILE="${TELECOM_NEWS_ENV_FILE:-$HOME/.config/telecom-news/env}"
if [ -f "$ENV_FILE" ]; then
  set -a
  # shellcheck source=/dev/null
  . "$ENV_FILE"
  set +a
fi

# Avoid overlapping runs when collection or LM Studio takes longer than the interval.
exec 9>"data/pipeline.lock"
flock -n 9 || exit 0

LOG_FILE="data/logs/pipeline.log"
{
  printf '\n===== %s =====\n' "$(date --iso-8601=seconds)"
  .venv/bin/python -m telecom_news run --limit "${TELECOM_NEWS_LIMIT:-10}"
  printf 'exit_code=%s\n' "$?"
} >>"$LOG_FILE" 2>&1
