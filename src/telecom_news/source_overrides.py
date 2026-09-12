"""Runtime source enable/disable overrides (M9b GUI).

Persists a set of source ids forced off under ``data/disabled_sources.json``.
Applied on top of ``config.SOURCES`` (and merged with env
``TELECOM_NEWS_DISABLED_SOURCES`` when refreshing). Collect/run see the same
in-memory registry after :func:`apply_to_sources`.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

_lock = threading.RLock()


def overrides_path(data_dir: Path | None = None) -> Path:
    from .config import load_config

    root = data_dir if data_dir is not None else load_config().data_dir
    return Path(root) / "disabled_sources.json"


def load_disabled(data_dir: Path | None = None) -> set[str]:
    path = overrides_path(data_dir)
    if not path.exists():
        return set()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return set()
    if isinstance(raw, dict):
        ids = raw.get("disabled") or raw.get("ids") or []
    elif isinstance(raw, list):
        ids = raw
    else:
        return set()
    return {str(item).strip() for item in ids if str(item).strip()}


def save_disabled(ids: set[str] | list[str], data_dir: Path | None = None) -> list[str]:
    path = overrides_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    ordered = sorted({str(item).strip() for item in ids if str(item).strip()})
    payload = {"disabled": ordered}
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)
    return ordered


def apply_to_sources(data_dir: Path | None = None) -> list[str]:
    """Force-disable sources listed in the overrides file (and keep env disables).

    Sources not in the file are restored to their catalog/hand-written enabled
    flag when possible by re-reading the module-level defaults is hard; we only
    flip ``enabled=False`` for ids in the file and ``enabled=True`` for ids that
    were only disabled by this file (tracked via previous file contents is enough
    for GUI toggles: set enabled explicitly on toggle).
    """
    from dataclasses import replace

    from . import config as config_mod

    disabled = load_disabled(data_dir)
    with _lock:
        for source_id, source in list(config_mod.SOURCES.items()):
            if source_id in disabled and source.enabled:
                config_mod.SOURCES[source_id] = replace(source, enabled=False)
    return sorted(disabled)


def set_source_enabled(
    source_id: str, enabled: bool, data_dir: Path | None = None
) -> dict[str, Any]:
    """Enable or disable one known source; persist and update in-memory registry."""
    from dataclasses import replace

    from . import config as config_mod

    source = config_mod.SOURCES.get(source_id)
    if source is None:
        raise KeyError(f"unknown source id {source_id!r}")

    disabled = load_disabled(data_dir)
    if enabled:
        disabled.discard(source_id)
    else:
        disabled.add(source_id)
    save_disabled(disabled, data_dir)
    config_mod.SOURCES[source_id] = replace(source, enabled=enabled)
    apply_to_sources(data_dir)
    updated = config_mod.SOURCES[source_id]
    return {
        "id": updated.id,
        "enabled": updated.enabled,
        "url": updated.url,
        "language": updated.language,
        "relevance_gate": updated.relevance_gate,
    }


def list_sources_for_api() -> list[dict[str, Any]]:
    """Snapshot of the pipeline registry for the GUI."""
    from .config import SOURCES

    file_disabled = load_disabled()
    rows: list[dict[str, Any]] = []
    for source_id in sorted(SOURCES):
        source = SOURCES[source_id]
        rows.append(
            {
                "id": source.id,
                "enabled": source.enabled,
                "url": source.url,
                "language": source.language,
                "relevance_gate": source.relevance_gate,
                "disabled_by_file": source_id in file_disabled,
            }
        )
    return rows
