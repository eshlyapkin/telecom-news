#!/usr/bin/env bash
# Look for new sources once. The scheduler (cron / Task Scheduler) invokes this
# script; it only writes proposals to data/source_candidates.json and never
# changes the source registry — accepting a proposal stays a human decision
# (Discovery tab, or `discover --accept <id>`).
set -u

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
mkdir -p data/logs

# Same configuration as the pipeline and the serve unit (see run_pipeline.sh).
ENV_FILE="${TELECOM_NEWS_ENV_FILE:-$HOME/.config/telecom-news/env}"
if [ -f "$ENV_FILE" ]; then
  set -a
  # shellcheck source=/dev/null
  . "$ENV_FILE"
  set +a
fi

# A scan probes external sites for a couple of minutes; never let two overlap.
exec 9>"data/discovery.lock"
flock -n 9 || exit 0

LOG_FILE="data/logs/discovery.log"
{
  printf '\n===== %s =====\n' "$(date --iso-8601=seconds)"
  .venv/bin/python -m telecom_news discover --max-sites "${TELECOM_NEWS_DISCOVER_SITES:-25}"
  printf 'exit_code=%s\n' "$?"
} >>"$LOG_FILE" 2>&1
