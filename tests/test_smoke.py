"""M0 smoke tests.

No network, no SQLite, no LM Studio, no Telegram — pure in-process checks of
the foundation: package import, domain model, CLI help, config and logging.
"""

from __future__ import annotations

import subprocess
import sys

import telecom_news


def test_package_imports() -> None:
    assert hasattr(telecom_news, "__version__")
    assert isinstance(telecom_news.__version__, str)


def test_cli_help_succeeds() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "telecom_news", "--help"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "usage" in result.stdout.lower()


def test_cli_run_placeholder_reports_not_implemented() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "telecom_news", "run"],
        capture_output=True,
        text=True,
    )
    # Placeholder exits with a non-zero code and says so explicitly.
    assert result.returncode != 0
    combined = result.stdout + result.stderr
    assert "not implemented" in combined.lower()


def test_cli_status_placeholder_reports_not_implemented() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "telecom_news", "status"],
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    combined = result.stdout + result.stderr
    assert "not implemented" in combined.lower()
