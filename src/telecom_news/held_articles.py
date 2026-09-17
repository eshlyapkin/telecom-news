"""Articles the operator has parked: in the queue, but never published on their own.

A hold is not a status. It is a decision about one article that must survive
re-processing and be undone without touching the row, so it lives in
``data/held_articles.json`` — the same override shape as disabled sources and
the channel languages. Publishing an article by hand still works while it is
held; only the automatic lanes skip it.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

_LOCK = threading.RLock()

FILENAME = "held_articles.json"


def held_path(data_dir: Path | str) -> Path:
    return Path(data_dir) / FILENAME


def load_held(data_dir: Path | str) -> set[int]:
    """Ids on hold. Never raises: publishing must not break on a bad data file."""
    path = held_path(data_dir)
    try:
        if not path.is_file():
            return set()
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return set()
    ids = raw.get("ids") if isinstance(raw, dict) else raw
    if not isinstance(ids, list):
        return set()
    held: set[int] = set()
    for value in ids:
        try:
            held.add(int(value))
        except (TypeError, ValueError):
            continue
    return held


def _save(data_dir: Path | str, ids: set[int]) -> set[int]:
    path = held_path(data_dir)
    with _LOCK:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(json.dumps({"ids": sorted(ids)}, indent=2) + "\n", encoding="utf-8")
        temporary.replace(path)
    return ids


def hold(data_dir: Path | str, article_id: int) -> set[int]:
    """Park one article. Idempotent."""
    with _LOCK:
        return _save(data_dir, load_held(data_dir) | {int(article_id)})


def release(data_dir: Path | str, article_id: int) -> set[int]:
    """Let one article back into the lanes. Idempotent."""
    with _LOCK:
        return _save(data_dir, load_held(data_dir) - {int(article_id)})
