#!/usr/bin/env bash
# One-shot dev setup: project-local venv, editable install, git hooks.
set -euo pipefail
REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
cd "$REPO_ROOT"

if [ ! -x ".venv/bin/python" ]; then
    echo "--- creating .venv (project-local) ---"
    python3 -m venv .venv
fi
echo "--- installing package with dev extras ---"
.venv/bin/pip install -e ".[dev]"
echo "--- enabling git hooks (.githooks) ---"
git config core.hooksPath .githooks
echo "--- running test suite ---"
.venv/bin/python -m pytest -q
echo
echo "Setup complete. Try: .venv/bin/python -m telecom_news --help"
