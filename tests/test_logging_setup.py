# tests/test_logging_setup.py
"""Tests for the logging setup helper.

Only a minimal check is performed: after calling :func:`setup_logging` the
root logger should have the level specified in the configuration and a
console handler should be present.
"""

from __future__ import annotations

import logging

from telecom_news.config import load_config
from telecom_news.logging_setup import setup_logging


def test_setup_logging_configures_root() -> None:
    # Ensure that calling the function does not raise and configures the root
    # logger appropriately.
    setup_logging()
    root = logging.getLogger()
    cfg = load_config()
    assert root.level == logging.getLevelName(cfg.log_level)
    # There should be at least one handler (the console one).
    assert any(isinstance(h, logging.StreamHandler) for h in root.handlers)
