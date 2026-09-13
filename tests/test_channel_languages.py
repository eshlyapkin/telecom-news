"""Live control of the channel's publication languages (panel override)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from telecom_news.channel_languages import (
    clear_override,
    languages_path,
    load_override,
    save_languages,
)
from telecom_news.config import load_config


@pytest.fixture(autouse=True)
def _isolated_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("TELECOM_NEWS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "-100")
    monkeypatch.delenv("TELEGRAM_CHAT_ID_EN", raising=False)
    monkeypatch.setenv("TELECOM_NEWS_TARGET_LANGS", "ru")
    return tmp_path


def test_override_replaces_the_env_value(tmp_path: Path) -> None:
    assert load_config().target_langs == ("ru",)

    save_languages(["ru", "en"], tmp_path)

    config = load_config()
    assert config.target_langs == ("ru", "en")
    assert config.channel_chat_ids == (("ru", "-100"), ("en", "-100"))


def test_clearing_the_override_restores_the_env_value(tmp_path: Path) -> None:
    save_languages(["en"], tmp_path)
    assert load_config().target_langs == ("en",)

    clear_override(tmp_path)
    assert load_config().target_langs == ("ru",)
    assert not languages_path(tmp_path).exists()


def test_saved_languages_are_normalized(tmp_path: Path) -> None:
    assert save_languages([" RU ", "en", "ru"], tmp_path) == ["ru", "en"]
    stored = json.loads(languages_path(tmp_path).read_text(encoding="utf-8"))
    assert stored == {"langs": ["ru", "en"]}


def test_unusable_choices_are_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="unsupported"):
        save_languages(["de"], tmp_path)
    with pytest.raises(ValueError, match="at least one"):
        save_languages([], tmp_path)
    assert not languages_path(tmp_path).exists()


def test_a_broken_file_never_breaks_configuration(tmp_path: Path) -> None:
    """Config loading runs everywhere; a bad data file must not take it down."""
    languages_path(tmp_path).write_text("{not json", encoding="utf-8")
    assert load_override(tmp_path) is None
    assert load_config().target_langs == ("ru",)

    languages_path(tmp_path).write_text('{"langs": ["de"]}', encoding="utf-8")
    assert load_config().target_langs == ("ru",)  # unsupported entry ignored


# --- API --------------------------------------------------------------------

pytest.importorskip("fastapi")


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from fastapi.testclient import TestClient

    from telecom_news.api.app import create_app
    from telecom_news.projects import ProjectRegistry

    monkeypatch.delenv("TELECOM_NEWS_DISABLED_SOURCES", raising=False)
    registry = ProjectRegistry(tmp_path / "projects" / "registry.json")
    registry.ensure_default()
    return TestClient(create_app(registry=registry))


def test_api_reports_env_languages_and_targets(client) -> None:
    body = client.get("/api/channel-languages").json()
    assert body["langs"] == ["ru"]
    assert body["source"] == "env"
    assert body["supported"] == ["ru", "en"]
    assert body["channel_targets"] == [{"lang": "ru", "chat_id": "-100"}]


def test_api_changes_languages_for_other_processes(client, tmp_path: Path) -> None:
    """The scheduled pipeline is a different process — the file is what it reads."""
    updated = client.put("/api/channel-languages", json={"langs": ["ru", "en"]})
    assert updated.status_code == 200
    assert updated.json()["source"] == "panel"
    assert load_config().target_langs == ("ru", "en")

    reset = client.delete("/api/channel-languages")
    assert reset.json()["langs"] == ["ru"]
    assert load_config().target_langs == ("ru",)


def test_api_rejects_unsupported_languages(client) -> None:
    assert client.put("/api/channel-languages", json={"langs": ["de"]}).status_code == 400
    assert client.put("/api/channel-languages", json={"langs": []}).status_code == 422


def test_api_flags_a_language_with_nowhere_to_post(
    client, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    monkeypatch.setenv("TELEGRAM_CHAT_ID_RU", "-100")
    body = client.put("/api/channel-languages", json={"langs": ["ru", "en"]}).json()
    assert body["unreachable"] == ["en"]


def test_gui_offers_the_language_switch(client) -> None:
    html = client.get("/").text
    assert "Channel languages" in html
    assert 'id="btn-save-langs"' in html
    assert "refreshLanguages" in client.get("/static/app.js").text
