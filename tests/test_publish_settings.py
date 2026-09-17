"""The evergreen pace is an operator setting, editable in the panel (D-031).

`PUBLISH_BACKLOG_PER_DAY` / `PUBLISH_BACKLOG_MIN_GAP_HOURS` are read once per
process; the panel writes a file instead, so a change reaches the next cycle
without restarting the serve unit.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from telecom_news import publish_settings
from telecom_news.config import load_config


def test_without_a_file_the_environment_is_in_charge(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TELECOM_NEWS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("PUBLISH_BACKLOG_PER_DAY", "3")
    monkeypatch.setenv("PUBLISH_BACKLOG_MIN_GAP_HOURS", "4")

    config = load_config()

    assert (config.publish_backlog_per_day, config.publish_backlog_min_gap_hours) == (3, 4.0)
    assert publish_settings.load_override(tmp_path) == {}


def test_the_panel_file_overrides_the_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TELECOM_NEWS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("PUBLISH_BACKLOG_PER_DAY", "3")
    publish_settings.save_override(data_dir=tmp_path, backlog_per_day=10)

    assert load_config().publish_backlog_per_day == 10

    publish_settings.clear_override(tmp_path)
    assert load_config().publish_backlog_per_day == 3


def test_saving_one_field_keeps_the_other(tmp_path: Path) -> None:
    publish_settings.save_override(data_dir=tmp_path, backlog_per_day=8)
    publish_settings.save_override(data_dir=tmp_path, backlog_min_gap_hours=1.5)

    assert publish_settings.load_override(tmp_path) == {
        "backlog_per_day": 8,
        "backlog_min_gap_hours": 1.5,
    }


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("backlog_per_day", -1),
        ("backlog_per_day", 10_000),
        ("backlog_per_day", "often"),
        ("backlog_min_gap_hours", -0.5),
        ("backlog_min_gap_hours", 10_000),
    ],
)
def test_an_unusable_value_is_rejected(tmp_path: Path, field: str, value: object) -> None:
    with pytest.raises(ValueError):
        publish_settings.save_override(data_dir=tmp_path, **{field: value})
    assert not publish_settings.settings_path(tmp_path).exists()


def test_a_broken_file_leaves_the_environment_in_charge(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TELECOM_NEWS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("PUBLISH_BACKLOG_PER_DAY", "3")
    publish_settings.settings_path(tmp_path).write_text("{oops", encoding="utf-8")

    assert publish_settings.load_override(tmp_path) == {}
    assert load_config().publish_backlog_per_day == 3


def test_one_unusable_field_does_not_discard_the_other(tmp_path: Path) -> None:
    publish_settings.settings_path(tmp_path).write_text(
        json.dumps({"backlog_per_day": 7, "backlog_min_gap_hours": "soon"}), encoding="utf-8"
    )
    assert publish_settings.load_override(tmp_path) == {"backlog_per_day": 7}


def test_the_panel_reads_and_writes_the_pace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from telecom_news.api.app import create_app
    from telecom_news.projects import ProjectRegistry

    monkeypatch.setenv("TELECOM_NEWS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("PUBLISH_BACKLOG_PER_DAY", "6")
    registry = ProjectRegistry(tmp_path / "projects" / "registry.json")
    registry.ensure_default()
    client = TestClient(create_app(registry))

    first = client.get("/api/publish-settings").json()
    assert first["backlog_per_day"] == 6
    assert first["overridden"] == []

    saved = client.put("/api/publish-settings", json={"backlog_per_day": 2}).json()
    assert saved["backlog_per_day"] == 2
    assert saved["overridden"] == ["backlog_per_day"]

    assert client.put("/api/publish-settings", json={"backlog_per_day": -1}).status_code == 422

    back = client.delete("/api/publish-settings").json()
    assert back["backlog_per_day"] == 6
    assert back["overridden"] == []
