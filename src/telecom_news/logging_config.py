"""Basic logging setup on top of the standard library ``logging``.

M0 scope: a clear format, a configurable level and one basic setup function.
No external logging libraries. File output to ``data/logs/`` is wired in M5
together with per-run pipeline logging (ARCHITECTURE.md 3.13).
"""

from __future__ import annotations

import logging
import sys

LOG_FORMAT = "%(asctime)s %(levelname)-8s %(name)s: %(message)s"


def setup_logging(level: str = "INFO") -> None:
    """Configure the root logger for console output.

    Idempotent: repeated calls replace existing handlers so the format and
    level stay consistent no matter how many times it is invoked.
    """
    numeric_level = getattr(logging, level.upper(), logging.INFO)
    if not isinstance(numeric_level, int):
        numeric_level = logging.INFO

    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)
        handler.close()

    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter(LOG_FORMAT))
    root.addHandler(handler)
    root.setLevel(numeric_level)
    # httpx logs full request URLs at INFO; Telegram URLs contain the bot token.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
