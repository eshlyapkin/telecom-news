"""Multi-project registry (M9a / VISION §§40–46).

Persists project metadata in a JSON file under the data directory. The built-in
default project ``sms-business-news`` mirrors today's single-tenant CLI setup so
operators who never open the GUI keep the same database and source list.

This module does **not** rewrite articles onto ``project_id`` yet (M10–M11).
Isolation is enforced at the registry/API layer for project records themselves.
"""

from __future__ import annotations

import json
import re
import threading
from collections.abc import Iterable
from copy import deepcopy
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_PROJECT_ID = "sms-business-news"
REGISTRY_VERSION = 1
_ID_RE = re.compile(r"^[a-z][a-z0-9-]{1,62}$")

PROJECT_STATUSES = ("running", "paused", "error")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _slugify(name: str) -> str:
    text = name.strip().lower()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    text = re.sub(r"-{2,}", "-", text).strip("-")
    if not text:
        text = "project"
    if text[0].isdigit():
        text = f"p-{text}"
    return text[:63]


@dataclass
class Project:
    """One news operations project (VISION §40)."""

    id: str
    name: str
    description: str = ""
    status: str = "running"  # running | paused | error
    publish_paused: bool = False  # project-level kill switch (§56)
    timezone: str = "UTC"
    # Empty source_ids = use every enabled entry of config.SOURCES (legacy default).
    source_ids: list[str] = field(default_factory=list)
    # Optional overrides; empty = fall back to process env / Config.
    channel_chat_ids: list[dict[str, str]] = field(default_factory=list)
    # Relative to data_dir unless absolute. Default project uses legacy news.db.
    db_path: str = "news.db"
    topics: list[str] = field(default_factory=list)
    icon: str = ""
    created_at: str = field(default_factory=_now_iso)
    updated_at: str = field(default_factory=_now_iso)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Project:
        known = {
            "id",
            "name",
            "description",
            "status",
            "publish_paused",
            "timezone",
            "source_ids",
            "channel_chat_ids",
            "db_path",
            "topics",
            "icon",
            "created_at",
            "updated_at",
        }
        payload = {key: value for key, value in data.items() if key in known}
        if "source_ids" in payload and payload["source_ids"] is None:
            payload["source_ids"] = []
        if "channel_chat_ids" in payload and payload["channel_chat_ids"] is None:
            payload["channel_chat_ids"] = []
        if "topics" in payload and payload["topics"] is None:
            payload["topics"] = []
        project = cls(**payload)
        if project.status not in PROJECT_STATUSES:
            raise ValueError(f"unknown project status {project.status!r}")
        if not _ID_RE.match(project.id):
            raise ValueError(f"invalid project id {project.id!r}; expected {_ID_RE.pattern}")
        return project


def default_project() -> Project:
    """Seed project equivalent to the current single-tenant deployment."""
    return Project(
        id=DEFAULT_PROJECT_ID,
        name="SMS Business News",
        description=(
            "Default project: SMS/A2P/messaging industry news (legacy single-tenant pipeline)."
        ),
        status="running",
        publish_paused=False,
        topics=["SMS", "A2P", "Messaging", "CPaaS"],
        db_path="news.db",
        source_ids=[],
    )


class ProjectRegistry:
    """Thread-safe JSON registry of projects."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self._lock = threading.RLock()

    def _empty_document(self) -> dict[str, Any]:
        project = default_project()
        return {
            "version": REGISTRY_VERSION,
            "global_publish_paused": False,
            "projects": {project.id: project.to_dict()},
        }

    def _read(self) -> dict[str, Any]:
        if not self.path.exists():
            doc = self._empty_document()
            self._write(doc)
            return doc
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("project registry root must be an object")
        projects = raw.get("projects")
        if not isinstance(projects, dict) or not projects:
            raw = self._empty_document()
            self._write(raw)
            return raw
        raw.setdefault("version", REGISTRY_VERSION)
        raw.setdefault("global_publish_paused", False)
        return raw

    def _write(self, document: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        payload = json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True)
        temporary.write_text(payload + "\n", encoding="utf-8")
        temporary.replace(self.path)

    def ensure_default(self) -> Project:
        """Create the registry file with the default project if missing."""
        with self._lock:
            doc = self._read()
            projects = doc["projects"]
            if DEFAULT_PROJECT_ID not in projects:
                projects[DEFAULT_PROJECT_ID] = default_project().to_dict()
                self._write(doc)
            return Project.from_dict(projects[DEFAULT_PROJECT_ID])

    def list_projects(self) -> list[Project]:
        with self._lock:
            doc = self._read()
            items = [Project.from_dict(value) for value in doc["projects"].values()]
        return sorted(items, key=lambda item: item.name.lower())

    def get(self, project_id: str) -> Project:
        with self._lock:
            doc = self._read()
            raw = doc["projects"].get(project_id)
            if raw is None:
                raise KeyError(f"unknown project id {project_id!r}")
            return Project.from_dict(raw)

    def global_publish_paused(self) -> bool:
        with self._lock:
            return bool(self._read().get("global_publish_paused"))

    def set_global_publish_paused(self, paused: bool) -> bool:
        with self._lock:
            doc = self._read()
            doc["global_publish_paused"] = bool(paused)
            self._write(doc)
            return bool(paused)

    def create(
        self,
        *,
        name: str,
        project_id: str | None = None,
        description: str = "",
        topics: Iterable[str] | None = None,
        source_ids: Iterable[str] | None = None,
        db_path: str | None = None,
        status: str = "running",
    ) -> Project:
        """Register a new project. Raises ``ValueError`` on conflicts / bad ids."""
        name = name.strip()
        if not name:
            raise ValueError("project name must not be empty")
        pid = (project_id or _slugify(name)).strip().lower()
        if not _ID_RE.match(pid):
            raise ValueError(f"invalid project id {pid!r}")
        project = Project(
            id=pid,
            name=name,
            description=description.strip(),
            status=status if status in PROJECT_STATUSES else "running",
            topics=[str(topic).strip() for topic in (topics or []) if str(topic).strip()],
            source_ids=[str(sid).strip() for sid in (source_ids or []) if str(sid).strip()],
            db_path=db_path or f"projects/{pid}.db",
        )
        with self._lock:
            doc = self._read()
            if pid in doc["projects"]:
                raise ValueError(f"project id already exists: {pid}")
            doc["projects"][pid] = project.to_dict()
            self._write(doc)
        return project

    def update(self, project_id: str, **changes: Any) -> Project:
        """Patch mutable fields of one project."""
        allowed = {
            "name",
            "description",
            "status",
            "publish_paused",
            "timezone",
            "source_ids",
            "channel_chat_ids",
            "db_path",
            "topics",
            "icon",
        }
        unknown = sorted(set(changes) - allowed)
        if unknown:
            raise ValueError(f"unknown project field(s): {', '.join(unknown)}")
        with self._lock:
            doc = self._read()
            raw = doc["projects"].get(project_id)
            if raw is None:
                raise KeyError(f"unknown project id {project_id!r}")
            merged = deepcopy(raw)
            for key, value in changes.items():
                if value is None:
                    continue
                merged[key] = value
            merged["updated_at"] = _now_iso()
            project = Project.from_dict(merged)
            doc["projects"][project_id] = project.to_dict()
            self._write(doc)
            return project

    def delete(self, project_id: str) -> None:
        """Remove a project record. Refuses to delete the default project."""
        if project_id == DEFAULT_PROJECT_ID:
            raise ValueError("refusing to delete the default project")
        with self._lock:
            doc = self._read()
            if project_id not in doc["projects"]:
                raise KeyError(f"unknown project id {project_id!r}")
            del doc["projects"][project_id]
            self._write(doc)

    def resolve_db_path(self, project: Project, data_dir: Path) -> Path:
        """Absolute SQLite path for a project."""
        path = Path(project.db_path)
        if path.is_absolute():
            return path
        return (data_dir / path).resolve()

    def dashboard_snapshot(
        self,
        *,
        data_dir: Path,
        count_articles: bool = True,
    ) -> dict[str, Any]:
        """Aggregate view for Global Dashboard (§41) — registry + optional DB counts."""
        projects = self.list_projects()
        global_paused = self.global_publish_paused()
        cards: list[dict[str, Any]] = []
        total_queue = 0
        total_published = 0
        total_sources_declared = 0
        problems = 0
        for project in projects:
            card: dict[str, Any] = {
                "id": project.id,
                "name": project.name,
                "status": project.status,
                "publish_paused": project.publish_paused or global_paused,
                "topics": list(project.topics),
                "source_ids_count": len(project.source_ids),
                "channels_count": len(project.channel_chat_ids),
                "queue": None,
                "published": None,
                "db_exists": False,
            }
            if project.status == "error" or project.publish_paused:
                problems += 1
            if count_articles:
                db_path = self.resolve_db_path(project, data_dir)
                card["db_exists"] = db_path.exists()
                if db_path.exists():
                    try:
                        from .storage.database import Database

                        counts = Database(db_path).count_by_status()
                        queue = int(counts.get("new", 0)) + int(counts.get("processed", 0))
                        published = int(counts.get("published", 0))
                        card["queue"] = queue
                        card["published"] = published
                        card["counts"] = counts
                        total_queue += queue
                        total_published += published
                    except OSError:
                        card["status"] = "error"
                        problems += 1
            total_sources_declared += len(project.source_ids)
            cards.append(card)
        running = sum(1 for project in projects if project.status == "running")
        return {
            "projects_total": len(projects),
            "projects_running": running,
            "projects_problems": problems,
            "global_publish_paused": global_paused,
            "queue_total": total_queue,
            "published_total": total_published,
            "sources_declared_total": total_sources_declared,
            "projects": cards,
        }


def registry_path_from_config(data_dir: Path | None = None) -> Path:
    """Default registry location: ``<data_dir>/projects/registry.json``."""
    from .config import load_config

    root = data_dir if data_dir is not None else load_config().data_dir
    return Path(root) / "projects" / "registry.json"


def get_registry(data_dir: Path | None = None) -> ProjectRegistry:
    """Open (and seed) the process-wide project registry."""
    registry = ProjectRegistry(registry_path_from_config(data_dir))
    registry.ensure_default()
    return registry
