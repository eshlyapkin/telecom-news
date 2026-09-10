#!/usr/bin/env bash
# Reject obvious Telegram Bot API tokens in staged additions.
set -eu

staged="$(git diff --cached --unified=0 --no-ext-diff -- . ':!*.lock' || true)"
if printf '%s\n' "$staged" | grep -E '^\+[^+].*[0-9]{8,12}:[A-Za-z0-9_-]{30,}' >/dev/null; then
    echo "secret check FAILED: possible Telegram Bot API token in staged changes" >&2
    exit 1
fi

echo "secret check OK"
