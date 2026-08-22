"""Tests for the basic stdlib logging setup (M0)."""

from __future__ import annotations

import logging


def test_setup_logging_runs_without_error() -> None:
    from telecom_news.logging_config import setup_logging

    # Must not raise and must be callable repeatedly.
    setup_logging("INFO")
    setup_logging("DEBUG")


def test_setup_logging_sets_root_level_and_handler() -> None:
    from telecom_news.logging_config import setup_logging

    setup_logging("WARNING")
    root = logging.getLogger()
    assert root.level == logging.WARNING
    assert any(isinstance(h, logging.StreamHandler) for h in root.handlers)


def test_setup_logging_accepts_invalid_level_gracefully() -> None:
    from telecom_news.logging_config import setup_logging

    # Unknown level falls back to INFO instead of raising.
    setup_logging("NOT-A-LEVEL")
    assert logging.getLogger().level == logging.INFO
