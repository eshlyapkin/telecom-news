"""Tests for the minimal M0 configuration."""

from __future__ import annotations

from telecom_news.config import Config, load_config


def test_load_config_returns_config() -> None:
    config = load_config()
    assert isinstance(config, Config)


def test_default_paths_point_into_project() -> None:
    config = load_config()
    assert config.project_root.is_dir()
    assert (config.project_root / "src").is_dir()
    assert str(config.data_dir).endswith("data") or config.data_dir.name == "data"
    assert config.logs_dir == config.data_dir / "logs"


def test_log_level_default_and_env_override(monkeypatch) -> None:
    monkeypatch.delenv("LOG_LEVEL", raising=False)
    assert load_config().log_level == "INFO"

    monkeypatch.setenv("LOG_LEVEL", "debug")
    assert load_config().log_level == "DEBUG"


def test_data_dir_env_override(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("TELECOM_NEWS_DATA_DIR", str(tmp_path))
    config = load_config()
    assert config.data_dir == tmp_path


def test_db_path_default_and_env_override(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("TELECOM_NEWS_DB", raising=False)
    monkeypatch.delenv("TELECOM_NEWS_DATA_DIR", raising=False)
    config = load_config()
    assert config.db_path == config.data_dir / "news.db"

    monkeypatch.setenv("TELECOM_NEWS_DB", str(tmp_path / "custom.db"))
    assert load_config().db_path == tmp_path / "custom.db"


def test_lmstudio_defaults_and_env_override(monkeypatch) -> None:
    monkeypatch.delenv("LMSTUDIO_BASE_URL", raising=False)
    monkeypatch.delenv("LMSTUDIO_MODEL", raising=False)
    monkeypatch.delenv("TELECOM_NEWS_TARGET_LANG", raising=False)
    config = load_config()
    assert config.lmstudio_base_url == "http://localhost:1234/v1"
    assert config.lmstudio_model == ""
    assert config.target_lang == "ru"

    monkeypatch.setenv("LMSTUDIO_BASE_URL", "http://example.com:1234/v1")
    monkeypatch.setenv("LMSTUDIO_MODEL", "my-model")
    monkeypatch.setenv("TELECOM_NEWS_TARGET_LANG", "EN")
    config = load_config()
    assert config.lmstudio_base_url == "http://example.com:1234/v1"
    assert config.lmstudio_model == "my-model"
    assert config.target_lang == "en"
