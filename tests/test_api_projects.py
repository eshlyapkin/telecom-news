"""M9a FastAPI multi-project API tests (requires optional ``.[api]`` extra)."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

from telecom_news.api.app import create_app
from telecom_news.projects import DEFAULT_PROJECT_ID, ProjectRegistry


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("TELECOM_NEWS_DATA_DIR", str(tmp_path))
    registry = ProjectRegistry(tmp_path / "projects" / "registry.json")
    registry.ensure_default()
    app = create_app(registry=registry)
    return TestClient(app)


def test_health(client: TestClient) -> None:
    response = client.get("/api/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["global_publish_paused"] is False


def test_list_and_get_default_project(client: TestClient) -> None:
    response = client.get("/api/projects")
    assert response.status_code == 200
    body = response.json()
    assert body["count"] >= 1
    ids = {item["id"] for item in body["projects"]}
    assert DEFAULT_PROJECT_ID in ids

    detail = client.get(f"/api/projects/{DEFAULT_PROJECT_ID}")
    assert detail.status_code == 200
    assert detail.json()["name"] == "SMS Business News"


def test_create_patch_delete_project(client: TestClient) -> None:
    created = client.post(
        "/api/projects",
        json={"name": "CPaaS Market", "id": "cpaas-market", "topics": ["CPaaS"]},
    )
    assert created.status_code == 201
    assert created.json()["id"] == "cpaas-market"

    patched = client.patch(
        "/api/projects/cpaas-market",
        json={"publish_paused": True, "status": "paused"},
    )
    assert patched.status_code == 200
    assert patched.json()["publish_paused"] is True

    deleted = client.delete("/api/projects/cpaas-market")
    assert deleted.status_code == 200
    assert client.get("/api/projects/cpaas-market").status_code == 404


def test_dashboard_and_gui(client: TestClient) -> None:
    dash = client.get("/api/dashboard")
    assert dash.status_code == 200
    assert "projects_total" in dash.json()

    page = client.get("/")
    assert page.status_code == 200
    assert "Control Panel" in page.text or "Overview" in page.text
    assert "text/html" in page.headers.get("content-type", "")


def test_global_pause_requires_confirm(client: TestClient) -> None:
    denied = client.post("/api/system/publish-pause", json={"paused": True, "confirm": False})
    assert denied.status_code == 400
    ok = client.post("/api/system/publish-pause", json={"paused": True, "confirm": True})
    assert ok.status_code == 200
    assert ok.json()["global_publish_paused"] is True
    client.post("/api/system/publish-pause", json={"paused": False, "confirm": False})
