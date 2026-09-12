"""FastAPI application: multi-project API + M9b control panel GUI."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from ..config import load_config
from ..projects import DEFAULT_PROJECT_ID, ProjectRegistry, get_registry
from ..source_overrides import apply_to_sources, list_sources_for_api, set_source_enabled
from .ops import ops_status, project_queue


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    id: str | None = Field(default=None, max_length=63)
    description: str = ""
    topics: list[str] = Field(default_factory=list)
    source_ids: list[str] = Field(default_factory=list)
    db_path: str | None = None
    status: str = "running"


class ProjectPatch(BaseModel):
    name: str | None = None
    description: str | None = None
    status: str | None = None
    publish_paused: bool | None = None
    timezone: str | None = None
    source_ids: list[str] | None = None
    channel_chat_ids: list[dict[str, str]] | None = None
    db_path: str | None = None
    topics: list[str] | None = None
    icon: str | None = None


class GlobalPauseBody(BaseModel):
    paused: bool
    confirm: bool = False


class ProjectPauseBody(BaseModel):
    paused: bool


class SourceEnableBody(BaseModel):
    enabled: bool


def create_app(registry: ProjectRegistry | None = None) -> FastAPI:
    """Build the API app. ``registry`` is injectable for tests."""
    config = load_config()
    reg = registry or get_registry(config.data_dir)
    # Honour GUI/file source disables for this process (and any shared SOURCES).
    apply_to_sources(config.data_dir)

    app = FastAPI(
        title="telecom-news",
        description=(
            "Multi-project news operations API (M9b control panel). "
            "No authentication — localhost only."
        ),
        version="0.3.0",
    )
    app.state.registry = reg
    app.state.data_dir = config.data_dir

    static_dir = Path(__file__).resolve().parent / "static"
    if static_dir.is_dir():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "service": "telecom-news",
            "version": "0.3.0",
            "global_publish_paused": reg.global_publish_paused(),
        }

    @app.get("/api/dashboard")
    def dashboard() -> dict[str, Any]:
        return reg.dashboard_snapshot(data_dir=config.data_dir, count_articles=True)

    @app.get("/api/ops/status")
    def status(project_id: str | None = None) -> dict[str, Any]:
        return ops_status(reg, data_dir=config.data_dir, project_id=project_id)

    @app.get("/api/projects")
    def list_projects() -> dict[str, Any]:
        projects = [project.to_dict() for project in reg.list_projects()]
        return {"projects": projects, "count": len(projects)}

    @app.post("/api/projects", status_code=201)
    def create_project(body: ProjectCreate) -> dict[str, Any]:
        try:
            project = reg.create(
                name=body.name,
                project_id=body.id,
                description=body.description,
                topics=body.topics,
                source_ids=body.source_ids,
                db_path=body.db_path,
                status=body.status,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return project.to_dict()

    @app.get("/api/projects/{project_id}")
    def get_project(project_id: str) -> dict[str, Any]:
        try:
            project = reg.get(project_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        payload = project.to_dict()
        db_path = reg.resolve_db_path(project, config.data_dir)
        payload["db_path_resolved"] = str(db_path)
        payload["db_exists"] = db_path.exists()
        if db_path.exists():
            try:
                from ..storage.database import Database

                payload["counts"] = Database(db_path).count_by_status()
            except OSError as exc:
                payload["counts_error"] = str(exc)
        return payload

    @app.patch("/api/projects/{project_id}")
    def patch_project(project_id: str, body: ProjectPatch) -> dict[str, Any]:
        changes = body.model_dump(exclude_unset=True)
        if not changes:
            raise HTTPException(status_code=400, detail="no fields to update")
        try:
            project = reg.update(project_id, **changes)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return project.to_dict()

    @app.delete("/api/projects/{project_id}")
    def delete_project(project_id: str) -> dict[str, str]:
        try:
            reg.delete(project_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"status": "deleted", "id": project_id}

    @app.post("/api/projects/{project_id}/publish-pause")
    def project_pause(project_id: str, body: ProjectPauseBody) -> dict[str, Any]:
        try:
            project = reg.update(project_id, publish_paused=bool(body.paused))
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {
            "id": project.id,
            "publish_paused": project.publish_paused,
            "global_publish_paused": reg.global_publish_paused(),
        }

    @app.get("/api/projects/{project_id}/queue")
    def queue(project_id: str, limit: int = 40) -> dict[str, Any]:
        if limit < 1 or limit > 200:
            raise HTTPException(status_code=400, detail="limit must be 1..200")
        try:
            return project_queue(reg, project_id, data_dir=config.data_dir, limit=limit)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/sources")
    def sources() -> dict[str, Any]:
        rows = list_sources_for_api()
        return {
            "sources": rows,
            "count": len(rows),
            "enabled": sum(1 for row in rows if row["enabled"]),
        }

    @app.patch("/api/sources/{source_id}")
    def patch_source(source_id: str, body: SourceEnableBody) -> dict[str, Any]:
        try:
            return set_source_enabled(source_id, body.enabled, data_dir=config.data_dir)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/system/publish-pause")
    def global_pause(body: GlobalPauseBody) -> dict[str, Any]:
        """Global kill switch (§56). Enabling pause requires confirm=true."""
        if body.paused and not body.confirm:
            raise HTTPException(
                status_code=400,
                detail="global pause requires confirm=true",
            )
        paused = reg.set_global_publish_paused(body.paused)
        return {"global_publish_paused": paused}

    @app.get("/", response_class=HTMLResponse)
    def gui_index() -> HTMLResponse:
        index = static_dir / "index.html"
        if not index.is_file():
            return HTMLResponse(
                "<h1>telecom-news</h1><p>GUI static files missing.</p>",
                status_code=500,
            )
        return HTMLResponse(index.read_text(encoding="utf-8"))

    @app.exception_handler(Exception)
    async def unhandled(_request: Request, exc: Exception) -> JSONResponse:
        return JSONResponse(status_code=500, content={"detail": str(exc)})

    # default project id exposed for the GUI bootstrap
    app.state.default_project_id = DEFAULT_PROJECT_ID
    return app
