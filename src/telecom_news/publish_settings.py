"""Live control over the evergreen publication pace (control panel).

``PUBLISH_BACKLOG_PER_DAY`` and ``PUBLISH_BACKLOG_MIN_GAP_HOURS`` are read once
per process, so changing how fast an archive reaches the channel meant editing
the env file and restarting the serve unit, while the scheduled pipeline kept
the old pace until its next start. This module stores the operator's choice in
``data/publish_settings.json`` instead, where :class:`telecom_news.config.Config`
picks it up on every ``load_config()`` — a pipeline cycle is a fresh process and
the API builds a config per request, so a change takes effect on the next cycle
without restarting anything.

The file is an override with the same rule as ``channel_languages``: delete it
and the environment is in charge again, which keeps a scripted deployment
authoritative when nobody has touched the panel. A field the file does not carry
is left to the environment on its own — changing the pace must not silently
reset the daily limit.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

_LOCK = threading.RLock()

FILENAME = "publish_settings.json"

# Upper bounds exist only so a typo cannot turn the drip into a flood or into
# "never": 200 posts a day is far past any sane channel, a week is far past any
# sane gap.
MAX_BACKLOG_PER_DAY = 200
MAX_BACKLOG_MIN_GAP_HOURS = 24.0 * 7


def settings_path(data_dir: Path | str) -> Path:
    return Path(data_dir) / FILENAME


def _coerce_per_day(value: Any) -> int:
    number = int(value)
    if not 0 <= number <= MAX_BACKLOG_PER_DAY:
        raise ValueError(
            f"backlog_per_day must be between 0 and {MAX_BACKLOG_PER_DAY} "
            "(0 = never publish articles older than the freshness window)"
        )
    return number


def _coerce_gap(value: Any) -> float:
    number = float(value)
    if not 0.0 <= number <= MAX_BACKLOG_MIN_GAP_HOURS:
        raise ValueError(
            f"backlog_min_gap_hours must be between 0 and {MAX_BACKLOG_MIN_GAP_HOURS:g}"
        )
    return number


_FIELDS = {
    "backlog_per_day": _coerce_per_day,
    "backlog_min_gap_hours": _coerce_gap,
}


def load_override(data_dir: Path | str) -> dict[str, Any]:
    """Values chosen in the panel; missing or unusable ones are simply absent.

    Never raises: configuration loading must not break because a data file is
    missing, unreadable or malformed.
    """
    path = settings_path(data_dir)
    try:
        if not path.is_file():
            return {}
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(raw, dict):
        return {}
    override: dict[str, Any] = {}
    for key, coerce in _FIELDS.items():
        if key not in raw:
            continue
        try:
            override[key] = coerce(raw[key])
        except (TypeError, ValueError):
            continue
    return override


def save_override(
    *,
    data_dir: Path | str,
    backlog_per_day: int | None = None,
    backlog_min_gap_hours: float | None = None,
) -> dict[str, Any]:
    """Store the fields given, keep the rest. Raises ValueError on a bad value."""
    stored = load_override(data_dir)
    for key, value in (
        ("backlog_per_day", backlog_per_day),
        ("backlog_min_gap_hours", backlog_min_gap_hours),
    ):
        if value is None:
            continue
        try:
            stored[key] = _FIELDS[key](value)
        except (TypeError, ValueError) as exc:
            message = str(exc) if isinstance(exc, ValueError) else f"{key} must be a number"
            raise ValueError(message) from exc
    path = settings_path(data_dir)
    with _LOCK:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(json.dumps(stored, indent=2) + "\n", encoding="utf-8")
        temporary.replace(path)
    return stored


def clear_override(data_dir: Path | str) -> None:
    """Hand the pace back to the environment."""
    with _LOCK:
        settings_path(data_dir).unlink(missing_ok=True)


def settings_for_api(data_dir: Path | str) -> dict[str, Any]:
    """Effective pace, which fields the panel owns, and where they are stored."""
    from .config import load_config

    current = load_config()
    override = load_override(data_dir)
    return {
        "backlog_per_day": current.publish_backlog_per_day,
        "backlog_min_gap_hours": current.publish_backlog_min_gap_hours,
        "overridden": sorted(override),
        "path": str(settings_path(data_dir)),
        "max_backlog_per_day": MAX_BACKLOG_PER_DAY,
        "max_backlog_min_gap_hours": MAX_BACKLOG_MIN_GAP_HOURS,
    }
