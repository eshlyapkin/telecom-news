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


def test_cli_run_is_registered() -> None:
    from telecom_news.cli import build_parser

    args = build_parser().parse_args(["run", "--dry-run", "--limit", "3"])
    assert args.command == "run"
    assert args.dry_run is True
    assert args.limit == 3


def test_cli_status_succeeds() -> None:
    # Since M2 `status` is a working command (article counters by status).
    result = subprocess.run(
        [sys.executable, "-m", "telecom_news", "status"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
