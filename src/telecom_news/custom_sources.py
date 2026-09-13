"""Operator-managed source add/delete for the control panel (M9d).

Two overlay files under ``data/``:

* ``custom_sources.json`` — feeds added through the GUI. They are merged on top
  of the built-in registry, so a new RSS needs no code change and survives the
  next ``sources import`` (which regenerates :mod:`sources_catalog`).
* ``removed_sources.json`` — ids of *built-in* sources the operator deleted.
  Built-ins are declared in code/catalog and cannot be erased from there, so a
  delete is recorded as a soft-delete overlay instead.

Both are replayed by :func:`telecom_news.config.refresh_sources`, which rebuilds
``config.SOURCES`` from the baseline first. Removals are applied before customs
so that re-adding a deleted id works in a single step.
"""

from __future__ import annotations

import json
import re
import threading
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

_LOCK = threading.RLock()

SOURCE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{1,62}$")
VALID_GATES = ("strict", "llm")
_SLUG_STRIP_RE = re.compile(r"[^a-z0-9]+")


def _data_dir(data_dir: Path | None) -> Path:
    if data_dir is not None:
        return Path(data_dir)
    from .config import load_config

    return load_config().data_dir


def custom_sources_path(data_dir: Path | None = None) -> Path:
    return _data_dir(data_dir) / "custom_sources.json"


def removed_sources_path(data_dir: Path | None = None) -> Path:
    return _data_dir(data_dir) / "removed_sources.json"


def _read_json(path: Path) -> Any:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def _clean_row(raw: Any) -> dict[str, Any] | None:
    """One stored custom source, or None when the entry is unusable."""
    if not isinstance(raw, dict):
        return None
    source_id = str(raw.get("id", "")).strip().lower()
    url = str(raw.get("url", "")).strip()
    if not SOURCE_ID_RE.match(source_id) or not url:
        return None
    language = str(raw.get("language", "en")).strip().lower() or "en"
    gate = str(raw.get("relevance_gate", "strict")).strip().lower()
    from .config import SOURCE_TYPE_RSS, SOURCE_TYPES

    source_type = str(raw.get("type", SOURCE_TYPE_RSS)).strip().lower()
    return {
        "id": source_id,
        "url": url,
        "type": source_type if source_type in SOURCE_TYPES else SOURCE_TYPE_RSS,
        "language": language,
        "relevance_gate": gate if gate in VALID_GATES else "strict",
        "enabled": bool(raw.get("enabled", True)),
    }


def load_custom(data_dir: Path | None = None) -> list[dict[str, Any]]:
    """Custom sources as stored, silently dropping malformed entries."""
    raw = _read_json(custom_sources_path(data_dir))
    if isinstance(raw, dict):
        raw = raw.get("sources") or raw.get("custom") or []
    if not isinstance(raw, list):
        return []
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in raw:
        row = _clean_row(item)
        if row is None or row["id"] in seen:
            continue
        seen.add(row["id"])
        rows.append(row)
    return rows


def save_custom(rows: list[dict[str, Any]], data_dir: Path | None = None) -> list[dict[str, Any]]:
    ordered = sorted(rows, key=lambda row: row["id"])
    _write_json(custom_sources_path(data_dir), {"sources": ordered})
    return ordered


def load_removed(data_dir: Path | None = None) -> set[str]:
    """Ids of built-in sources the operator soft-deleted."""
    raw = _read_json(removed_sources_path(data_dir))
    if isinstance(raw, dict):
        raw = raw.get("removed") or raw.get("ids") or []
    if not isinstance(raw, list):
        return set()
    return {str(item).strip().lower() for item in raw if str(item).strip()}


def save_removed(ids: set[str] | list[str], data_dir: Path | None = None) -> list[str]:
    ordered = sorted({str(item).strip().lower() for item in ids if str(item).strip()})
    _write_json(removed_sources_path(data_dir), {"removed": ordered})
    return ordered


def slug_from_url(url: str, taken: set[str] | None = None) -> str:
    """Readable, unique-ish source id derived from the feed host and path."""
    parsed = urlparse(url)
    host = (parsed.netloc or parsed.path).lower()
    host = host.split("@")[-1].split(":")[0]
    if host.startswith("www."):
        host = host[4:]
    base = _SLUG_STRIP_RE.sub("-", host).strip("-") or "source"
    # A host can serve several feeds ("/feed/news", "/feed/blog"); keep the last
    # meaningful path segment so the ids stay tellable apart.
    segments = [
        _SLUG_STRIP_RE.sub("-", part.lower()).strip("-")
        for part in parsed.path.split("/")
        if part and part.lower() not in ("feed", "rss", "index.xml", "atom.xml")
    ]
    tail = next((part for part in reversed(segments) if part and not part.isdigit()), "")
    if tail and tail not in base:
        base = f"{base}-{tail}"
    base = base[:56].strip("-") or "source"
    if not base[0].isalnum():
        base = f"s-{base}"
    used = taken or set()
    if base not in used:
        return base
    for suffix in range(2, 100):
        candidate = f"{base}-{suffix}"
        if candidate not in used:
            return candidate
    raise ValueError(f"cannot derive a free source id from {url!r}")


def apply_custom_sources(data_dir: Path | None = None) -> dict[str, list[str]]:
    """Drop soft-deleted ids from ``config.SOURCES``, then merge custom feeds in.

    Called by :func:`telecom_news.config.refresh_sources` on a registry that was
    just reset to the baseline; on its own it is still idempotent.
    """
    from .config import SOURCE_TYPE_RSS, SOURCES, SourceConfig

    removed = load_removed(data_dir)
    custom = load_custom(data_dir)
    with _LOCK:
        for source_id in removed:
            SOURCES.pop(source_id, None)
        for row in custom:
            SOURCES[row["id"]] = SourceConfig(
                id=row["id"],
                type=row.get("type", SOURCE_TYPE_RSS),
                url=row["url"],
                language=row["language"],
                enabled=row["enabled"],
                relevance_gate=row["relevance_gate"],
            )
    return {"removed": sorted(removed), "custom": [row["id"] for row in custom]}


def add_source(
    url: str,
    *,
    source_id: str | None = None,
    language: str = "en",
    relevance_gate: str = "strict",
    source_type: str = "rss",
    enabled: bool = True,
    data_dir: Path | None = None,
) -> dict[str, Any]:
    """Add one source to the registry and persist it. Raises ValueError on bad input.

    ``source_type`` is ``"rss"`` for a feed or ``"sitemap"`` for an outlet that
    publishes none (see :mod:`telecom_news.collectors.sitemap`).
    """
    from .config import SOURCE_TYPES, SOURCES, SUPPORTED_LANGS, refresh_sources

    url = (url or "").strip()
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError(f"url must be an http(s) address, got {url!r}")

    source_type = (source_type or "rss").strip().lower()
    if source_type not in SOURCE_TYPES:
        raise ValueError(
            f"source type must be one of {', '.join(SOURCE_TYPES)}, got {source_type!r}"
        )

    language = (language or "en").strip().lower()
    if language not in SUPPORTED_LANGS:
        raise ValueError(
            f"unsupported language {language!r}; supported: {', '.join(SUPPORTED_LANGS)}"
        )
    relevance_gate = (relevance_gate or "strict").strip().lower()
    if relevance_gate not in VALID_GATES:
        raise ValueError(
            f"relevance_gate must be one of {', '.join(VALID_GATES)}, got {relevance_gate!r}"
        )

    with _LOCK:
        refresh_sources(data_dir)
        custom = load_custom(data_dir)
        removed = load_removed(data_dir)
        if source_id is None or not str(source_id).strip():
            new_id = slug_from_url(url, taken=set(SOURCES) | {row["id"] for row in custom})
        else:
            new_id = str(source_id).strip().lower()
            if not SOURCE_ID_RE.match(new_id):
                raise ValueError(
                    f"invalid source id {new_id!r}: use lowercase letters, digits, '-', '.', '_'"
                )
            if new_id in SOURCES:
                raise ValueError(f"source id {new_id!r} already exists")
        if any(row["url"] == url for row in custom):
            raise ValueError(f"feed {url!r} is already registered")

        # Re-adding a previously deleted built-in: forget the soft-delete so the
        # new entry is not dropped again on the next refresh.
        if new_id in removed:
            removed.discard(new_id)
            save_removed(removed, data_dir)

        custom = [row for row in custom if row["id"] != new_id]
        custom.append(
            {
                "id": new_id,
                "url": url,
                "type": source_type,
                "language": language,
                "relevance_gate": relevance_gate,
                "enabled": bool(enabled),
            }
        )
        save_custom(custom, data_dir)
        refresh_sources(data_dir)
        source = SOURCES[new_id]

    return {
        "id": source.id,
        "enabled": source.enabled,
        "url": source.url,
        "type": source.type,
        "language": source.language,
        "relevance_gate": source.relevance_gate,
        "custom": True,
    }


def delete_source(source_id: str, data_dir: Path | None = None) -> dict[str, Any]:
    """Remove a source from the registry. Raises KeyError when it is unknown."""
    from .config import SOURCES, refresh_sources
    from .source_overrides import load_disabled, save_disabled

    source_id = str(source_id or "").strip().lower()
    with _LOCK:
        refresh_sources(data_dir)
        if source_id not in SOURCES:
            raise KeyError(f"unknown source id {source_id!r}")
        custom = load_custom(data_dir)
        was_custom = any(row["id"] == source_id for row in custom)
        if was_custom:
            save_custom([row for row in custom if row["id"] != source_id], data_dir)
        else:
            removed = load_removed(data_dir)
            removed.add(source_id)
            save_removed(removed, data_dir)
        # A deleted source must not linger in the enable/disable overlay: it would
        # resurface as "disabled" if the same id is added back later.
        disabled = load_disabled(data_dir)
        if source_id in disabled:
            disabled.discard(source_id)
            save_disabled(disabled, data_dir)
        refresh_sources(data_dir)

    return {
        "id": source_id,
        "deleted": True,
        "kind": "custom" if was_custom else "builtin",
    }


def list_custom_ids(data_dir: Path | None = None) -> set[str]:
    return {row["id"] for row in load_custom(data_dir)}
