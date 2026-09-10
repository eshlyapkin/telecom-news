# src/telecom_news/logging_setup.py
"""Logging configuration for the telecom‑news package.

The configuration is intentionally minimal – it sets up a root logger with a
console handler and a level that can be overridden via the :class:`Config`
dataclass in :mod:`telecom_news.config`.  The function is idempotent: calling
``setup_logging`` multiple times will not add duplicate handlers.

The format used is compatible with the default ``logging`` module and is
sufficient for the smoke tests and future CLI output.
"""

from __future__ import annotations

import logging
from typing import Final

from .config import load_config

# Default format – simple, readable, and includes the log level.
_LOG_FORMAT: Final[str] = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"

# Cache to avoid adding multiple handlers when ``setup_logging`` is called
# repeatedly (e.g. during tests).
_HANDLERS_ADDED: Final[set] = set()


def _create_console_handler(level: int) -> logging.Handler:
    """Return a console handler with the given level and default format."""
    handler = logging.StreamHandler()
    handler.setLevel(level)
    formatter = logging.Formatter(_LOG_FORMAT)
    handler.setFormatter(formatter)
    return handler


def setup_logging() -> None:
    """Configure the root logger.

    The function reads the current configuration via :func:`load_config` and
    applies the ``log_level`` to the root logger.  A console handler is added
    if one is not already present.
    """
    cfg = load_config()
    root = logging.getLogger()
    root.setLevel(cfg.log_level)

    # Avoid adding duplicate handlers – useful when tests import the module
    # multiple times.
    if "console" not in _HANDLERS_ADDED:
        console = _create_console_handler(cfg.log_level)
        root.addHandler(console)
        _HANDLERS_ADDED.add("console")


# Expose the function for importers.
__all__ = ["setup_logging"]
