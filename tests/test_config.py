"""Tests for the minimal M0 configuration."""

from __future__ import annotations

import pytest

from telecom_news.config import SOURCES, Config, load_config


def test_load_config_returns_config() -> None:
    config = load_config()
    assert isinstance(config, Config)


def test_verified_russian_sources_are_declared() -> None:
    expected = {
        "content-review": "https://content-review.com/feed.xml",
        "iksmedia": "https://www.iksmedia.ru/rss/rss_yandex.rss",
        "habr-cellular-news": "https://habr.com/ru/rss/hubs/cellular/news/?fl=ru",
    }
    for source_id, url in expected.items():
        source = SOURCES[source_id]
        assert source.url == url
        assert source.language == "ru"
        assert source.enabled is True


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


# --- D-011: Russian source registry -----------------------------------------

RUS_SOURCES_ADDED_IN_D011 = {
    "cnews-telecom": "https://www.cnews.ru/inc/rss/telecom.xml",
    "cnews-safe": "https://www.cnews.ru/inc/rss/safe.xml",
    "cnews-biz": "https://www.cnews.ru/inc/rss/biz.xml",
    "cnews-internet": "https://www.cnews.ru/inc/rss/internet.xml",
    "cnews-corp": "https://www.cnews.ru/inc/rss/corp.xml",
    "securitylab-news": "https://www.securitylab.ru/_Services/Export/RSS/news/",
    "securitylab-analytics": "https://www.securitylab.ru/_Services/Export/RSS/analytics/",
    "securitylab-vulnerabilities": "https://www.securitylab.ru/_Services/Export/RSS/vulnerabilities/",
    "anti-malware-news": "https://www.anti-malware.ru/news/feed",
    "anti-malware-analytics": "https://www.anti-malware.ru/taxonomy/term/76/feed",
    "anti-malware-press": "https://www.anti-malware.ru/press/feed",
    "nag-all": "https://nag.ru/rss/all",
}


def test_d011_sources_are_declared_with_expected_urls() -> None:
    for source_id, url in RUS_SOURCES_ADDED_IN_D011.items():
        source = SOURCES[source_id]
        assert source.url == url
        assert source.language == "ru"
        assert source.type == "rss"


def test_d011_only_low_signal_feeds_ship_disabled() -> None:
    """Everything graded A/B+ runs by default; the noisy tails stay declared
    but off (M6: enabling a source must not require code changes)."""
    disabled = {source_id for source_id, source in SOURCES.items() if not source.enabled}
    assert disabled == {
        "cnews-corp",
        "securitylab-vulnerabilities",
        "anti-malware-press",
        "nag-all",
    }


def test_russian_sources_are_the_majority_of_the_registry() -> None:
    ru = [source for source in SOURCES.values() if source.language == "ru"]
    assert len(ru) == 15
    assert len(ru) * 2 >= len(SOURCES)


def test_no_source_url_carries_tracking_parameters() -> None:
    """Discovery notes came with utm_* junk; config must store clean endpoints."""
    for source_id, source in SOURCES.items():
        assert "utm_" not in source.url, source_id
        assert "?" not in source.url or source.url == (
            "https://habr.com/ru/rss/hubs/cellular/news/?fl=ru"
        ), source_id


def test_source_ids_and_urls_are_unique() -> None:
    urls = [source.url for source in SOURCES.values()]
    assert len(urls) == len(set(urls))
    assert sorted(SOURCES) == sorted(source.id for source in SOURCES.values())


def test_target_langs_default_and_env_override(monkeypatch) -> None:
    monkeypatch.delenv("TELECOM_NEWS_TARGET_LANGS", raising=False)
    monkeypatch.delenv("TELECOM_NEWS_TARGET_LANG", raising=False)
    assert load_config().target_langs == ("ru",)
    assert load_config().target_lang == "ru"

    monkeypatch.setenv("TELECOM_NEWS_TARGET_LANGS", "ru,en")
    config = load_config()
    assert config.target_langs == ("ru", "en")
    assert config.target_lang == "ru"

    # A single-language variable keeps working (backwards compatibility).
    monkeypatch.delenv("TELECOM_NEWS_TARGET_LANGS")
    monkeypatch.setenv("TELECOM_NEWS_TARGET_LANG", "en")
    assert load_config().target_langs == ("en",)


def test_unknown_target_language_is_rejected(monkeypatch) -> None:
    monkeypatch.setenv("TELECOM_NEWS_TARGET_LANGS", "ru,de")
    with pytest.raises(ValueError):
        load_config()


def test_channel_targets_follow_target_languages(monkeypatch) -> None:
    monkeypatch.setenv("TELECOM_NEWS_TARGET_LANGS", "ru,en")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "-100")
    monkeypatch.delenv("TELEGRAM_CHAT_ID_EN", raising=False)
    assert load_config().channel_chat_ids == (("ru", "-100"),)

    monkeypatch.setenv("TELEGRAM_CHAT_ID_EN", "-200")
    assert load_config().channel_chat_ids == (("ru", "-100"), ("en", "-200"))


def test_narrow_sources_allow_the_llm_gate(monkeypatch) -> None:
    for source_id in ("content-review", "anti-malware-news", "securitylab-news"):
        assert SOURCES[source_id].relevance_gate == "llm"
    for source_id in ("sinch-blog", "twilio-blog", "cnews-telecom"):
        assert SOURCES[source_id].relevance_gate == "strict"
