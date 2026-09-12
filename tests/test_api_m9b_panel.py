"""M9b control panel API: status, queue, sources toggle, project pause."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

from telecom_news.api.app import create_app
from telecom_news.models import Article
from telecom_news.projects import DEFAULT_PROJECT_ID, ProjectRegistry
from telecom_news.storage.database import Database


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("TELECOM_NEWS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "-100")
    registry = ProjectRegistry(tmp_path / "projects" / "registry.json")
    registry.ensure_default()
    db = Database(tmp_path / "news.db")
    now = datetime(2026, 9, 12, 12, 0, tzinfo=timezone.utc)
    article = Article(
        url="https://example.com/a1",
        content_hash="h1",
        source_id="sinch-blog",
        title="Sinch SMS API launch",
        body="body",
        status="new",
        fetched_at=now,
        published_at=now,
    )
    db.upsert_by_hash(article)
    app = create_app(registry=registry)
    return TestClient(app)


def test_ops_status_and_queue(client: TestClient) -> None:
    status = client.get(f"/api/ops/status?project_id={DEFAULT_PROJECT_ID}")
    assert status.status_code == 200
    body = status.json()
    assert body["project_id"] == DEFAULT_PROJECT_ID
    assert "bot_running" in body
    assert body["counts"].get("new", 0) >= 1

    queue = client.get(f"/api/projects/{DEFAULT_PROJECT_ID}/queue")
    assert queue.status_code == 200
    q = queue.json()
    assert q["db_exists"] is True
    assert any(row["title"].startswith("Sinch") for row in q["new"])


def test_project_and_global_pause(client: TestClient) -> None:
    paused = client.post(
        f"/api/projects/{DEFAULT_PROJECT_ID}/publish-pause",
        json={"paused": True},
    )
    assert paused.status_code == 200
    assert paused.json()["publish_paused"] is True

    status = client.get(f"/api/ops/status?project_id={DEFAULT_PROJECT_ID}").json()
    assert status["project_publish_paused"] is True
    assert status["publish_effectively_paused"] is True

    client.post(
        f"/api/projects/{DEFAULT_PROJECT_ID}/publish-pause",
        json={"paused": False},
    )
    g = client.post("/api/system/publish-pause", json={"paused": True, "confirm": True})
    assert g.json()["global_publish_paused"] is True
    client.post("/api/system/publish-pause", json={"paused": False, "confirm": False})


def test_sources_list_and_toggle(client: TestClient, tmp_path: Path) -> None:
    listed = client.get("/api/sources")
    assert listed.status_code == 200
    rows = listed.json()["sources"]
    assert any(row["id"] == "sinch-blog" for row in rows)

    off = client.patch("/api/sources/sinch-blog", json={"enabled": False})
    assert off.status_code == 200
    assert off.json()["enabled"] is False
    assert (tmp_path / "disabled_sources.json").is_file()

    again = client.get("/api/sources").json()["sources"]
    sinch = next(row for row in again if row["id"] == "sinch-blog")
    assert sinch["enabled"] is False

    on = client.patch("/api/sources/sinch-blog", json={"enabled": True})
    assert on.json()["enabled"] is True


def test_gui_index_has_control_buttons(client: TestClient) -> None:
    page = client.get("/")
    assert page.status_code == 200
    assert "Pause publish" in page.text
    assert "Sources" in page.text
    assert "Queue" in page.text
