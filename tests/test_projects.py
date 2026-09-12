"""M9a multi-project registry tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from telecom_news.projects import (
    DEFAULT_PROJECT_ID,
    ProjectRegistry,
    default_project,
)


def test_default_project_seed_fields() -> None:
    project = default_project()
    assert project.id == DEFAULT_PROJECT_ID
    assert project.status == "running"
    assert project.publish_paused is False
    assert "SMS" in project.topics or project.topics


def test_registry_seeds_default(tmp_path: Path) -> None:
    path = tmp_path / "registry.json"
    registry = ProjectRegistry(path)
    project = registry.ensure_default()
    assert project.id == DEFAULT_PROJECT_ID
    assert path.is_file()
    listed = registry.list_projects()
    assert len(listed) == 1
    assert listed[0].id == DEFAULT_PROJECT_ID


def test_create_and_get_project(tmp_path: Path) -> None:
    registry = ProjectRegistry(tmp_path / "registry.json")
    registry.ensure_default()
    created = registry.create(name="RCS News", project_id="rcs-news", topics=["RCS"])
    assert created.id == "rcs-news"
    assert registry.get("rcs-news").name == "RCS News"
    ids = {item.id for item in registry.list_projects()}
    assert ids == {DEFAULT_PROJECT_ID, "rcs-news"}


def test_create_rejects_duplicate_and_bad_id(tmp_path: Path) -> None:
    registry = ProjectRegistry(tmp_path / "registry.json")
    registry.ensure_default()
    with pytest.raises(ValueError, match="already exists"):
        registry.create(name="Dup", project_id=DEFAULT_PROJECT_ID)
    with pytest.raises(ValueError, match="invalid project id"):
        registry.create(name="Bad", project_id="1-starts-with-digit")
    with pytest.raises(ValueError, match="invalid project id"):
        registry.create(name="Bad", project_id="has_underscore")


def test_update_pause_and_delete(tmp_path: Path) -> None:
    registry = ProjectRegistry(tmp_path / "registry.json")
    registry.ensure_default()
    registry.create(name="Temp", project_id="temp-proj")
    updated = registry.update("temp-proj", publish_paused=True, status="paused")
    assert updated.publish_paused is True
    assert updated.status == "paused"
    registry.delete("temp-proj")
    with pytest.raises(KeyError):
        registry.get("temp-proj")
    with pytest.raises(ValueError, match="default"):
        registry.delete(DEFAULT_PROJECT_ID)


def test_global_kill_switch(tmp_path: Path) -> None:
    registry = ProjectRegistry(tmp_path / "registry.json")
    registry.ensure_default()
    assert registry.global_publish_paused() is False
    assert registry.set_global_publish_paused(True) is True
    assert registry.global_publish_paused() is True


def test_dashboard_snapshot_without_db(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    registry = ProjectRegistry(data_dir / "projects" / "registry.json")
    registry.ensure_default()
    snap = registry.dashboard_snapshot(data_dir=data_dir, count_articles=True)
    assert snap["projects_total"] == 1
    assert snap["projects_running"] == 1
    assert snap["global_publish_paused"] is False
    assert snap["projects"][0]["id"] == DEFAULT_PROJECT_ID


def test_cli_projects_list(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELECOM_NEWS_DATA_DIR", str(tmp_path))
    from telecom_news.cli import main

    code = main(["projects", "list"])
    assert code == 0
