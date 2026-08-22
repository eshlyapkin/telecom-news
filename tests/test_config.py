"""Tests for the minimal M0 configuration."""

from __future__ import annotations

import logging

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
