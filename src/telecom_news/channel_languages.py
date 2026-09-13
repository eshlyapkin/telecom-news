"""Live control over the channel's publication languages (control panel).

``TELECOM_NEWS_TARGET_LANGS`` is read once per process, so changing which
languages the channel gets meant editing the env file and restarting the serve
unit — and the scheduled pipeline kept the old value until its next start. This
module stores the operator's choice in ``data/channel_languages.json`` instead,
where :class:`telecom_news.config.Config` picks it up on every ``load_config()``.
The pipeline starts a fresh process per cycle and the API builds a config per
request, so a change takes effect on the next cycle without restarting anything.

The file is an override: delete it (or clear the list) and the env variable is
in charge again, which keeps a scripted deployment authoritative when nobody has
touched the panel.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

_LOCK = threading.RLock()

FILENAME = "channel_languages.json"


def languages_path(data_dir: Path | str) -> Path:
    return Path(data_dir) / FILENAME


def load_override(data_dir: Path | str) -> tuple[str, ...] | None:
    """Languages chosen in the panel, or None when the env value should win.

    Never raises: configuration loading must not break because a data file is
    missing, unreadable or malformed.
    """
    path = languages_path(data_dir)
    try:
        if not path.is_file():
            return None
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if isinstance(raw, dict):
        raw = raw.get("langs") or raw.get("languages") or []
    if not isinstance(raw, list):
        return None
    langs = tuple(dict.fromkeys(str(item).strip().lower() for item in raw if str(item).strip()))
    return langs or None


def save_languages(langs: list[str] | tuple[str, ...], data_dir: Path | str) -> list[str]:
    """Persist the channel languages. Raises ValueError on an unusable choice."""
    from .config import SUPPORTED_LANGS

    ordered = list(dict.fromkeys(str(item).strip().lower() for item in langs if str(item).strip()))
    if not ordered:
        raise ValueError("at least one publication language is required")
    unsupported = [lang for lang in ordered if lang not in SUPPORTED_LANGS]
    if unsupported:
        raise ValueError(
            f"unsupported publication language(s) {', '.join(unsupported)}; "
            f"supported: {', '.join(SUPPORTED_LANGS)}"
        )
    path = languages_path(data_dir)
    with _LOCK:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(
            json.dumps({"langs": ordered}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(path)
    return ordered


def clear_override(data_dir: Path | str) -> None:
    """Hand control back to ``TELECOM_NEWS_TARGET_LANGS``."""
    with _LOCK:
        languages_path(data_dir).unlink(missing_ok=True)


def languages_for_api(data_dir: Path | str) -> dict[str, Any]:
    """Current channel languages plus where each one would be posted."""
    from .config import SUPPORTED_LANGS, load_config

    config = load_config()
    override = load_override(data_dir)
    return {
        "langs": list(config.target_langs),
        "supported": list(SUPPORTED_LANGS),
        "source": "panel" if override else "env",
        "path": str(languages_path(data_dir)),
        # One entry per language actually reachable; two languages sharing a chat
        # id means both renditions go into the same channel.
        "channel_targets": [
            {"lang": lang, "chat_id": chat_id} for lang, chat_id in config.channel_chat_ids
        ],
        "unreachable": [
            lang
            for lang in config.target_langs
            if lang not in {lang for lang, _ in config.channel_chat_ids}
        ],
    }
