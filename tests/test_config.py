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


def test_russian_sources_stay_represented_after_the_catalog_import() -> None:
    """2026-09-11: the verified research table added ~40 EN news feeds.

    The registry is no longer RU-majority (20 ru of 63 sources), so instead of a
    majority this pins the count and the curated ru feeds the project relies on.
    """
    ru = [source for source in SOURCES.values() if source.language == "ru"]
    assert len(ru) == 20
    for source_id in (
        "cnews-telecom",
        "cnews-corp",
        "securitylab-news",
        "anti-malware-news",
        "habr-cellular-news",
        "content-review",
    ):
        assert source_id in SOURCES


def test_no_source_url_carries_tracking_parameters() -> None:
    """Discovery notes came with utm_* junk; config must store clean endpoints.

    Query strings that belong to the publisher's own feed URL are fine (Habr's
    ``?fl=ru``, Alertify's ``?x=1`` from the verified research table) — tracking
    junk is not.
    """
    tracking = ("utm_", "fbclid", "gclid", "yclid", "mc_cid", "mc_eid")
    for source_id, source in SOURCES.items():
        for marker in tracking:
            assert marker not in source.url, (source_id, marker)


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


def test_article_max_age_days_env_override(monkeypatch) -> None:
    monkeypatch.delenv("TELECOM_NEWS_MAX_ARTICLE_AGE_DAYS", raising=False)
    assert load_config().article_max_age_days == 30
    monkeypatch.setenv("TELECOM_NEWS_MAX_ARTICLE_AGE_DAYS", "7")
    assert load_config().article_max_age_days == 7
    monkeypatch.setenv("TELECOM_NEWS_MAX_ARTICLE_AGE_DAYS", "0")
    assert load_config().article_max_age_days == 0
    monkeypatch.setenv("TELECOM_NEWS_MAX_ARTICLE_AGE_DAYS", "junk")
    assert load_config().article_max_age_days == 30


def test_disabled_sources_env_forces_them_off(monkeypatch) -> None:
    """TELECOM_NEWS_DISABLED_SOURCES survives `sources import` (unlike editing the catalog)."""
    from telecom_news import config as config_module

    original = dict(config_module.SOURCES)
    try:
        monkeypatch.setenv("TELECOM_NEWS_DISABLED_SOURCES", "2600hz, thundersms")
        config_module._apply_disabled_override()
        assert config_module.SOURCES["2600hz"].enabled is False
        assert config_module.SOURCES["thundersms"].enabled is False
        assert config_module.SOURCES["slicktext"].enabled is True

        monkeypatch.setenv("TELECOM_NEWS_DISABLED_SOURCES", "no-such-source")
        with pytest.raises(ValueError, match="unknown source id"):
            config_module._apply_disabled_override()
    finally:
        config_module.SOURCES.clear()
        config_module.SOURCES.update(original)


def test_publication_caps_default_and_env_override(monkeypatch) -> None:
    """D-020: channel caps live in the config, not in `run --limit`."""
    for name in ("PUBLISH_MAX_PER_CYCLE", "PUBLISH_MAX_AGE_HOURS"):
        monkeypatch.delenv(name, raising=False)
    config = load_config()
    assert config.publish_max_per_cycle == 10
    assert config.publish_max_age_hours == 48.0

    monkeypatch.setenv("PUBLISH_MAX_PER_CYCLE", "3")
    monkeypatch.setenv("PUBLISH_MAX_AGE_HOURS", "12")
    config = load_config()
    assert config.publish_max_per_cycle == 3
    assert isinstance(config.publish_max_per_cycle, int)
    assert config.publish_max_age_hours == 12.0

    # 0 switches a guard off, the same convention as the collect-time threshold.
    monkeypatch.setenv("PUBLISH_MAX_PER_CYCLE", "0")
    monkeypatch.setenv("PUBLISH_MAX_AGE_HOURS", "0")
    config = load_config()
    assert config.publish_max_per_cycle == 0
    assert config.publish_max_age_hours == 0.0

    # An unparsable value is ignored, so the default survives a typo.
    monkeypatch.setenv("PUBLISH_MAX_PER_CYCLE", "junk")
    monkeypatch.setenv("PUBLISH_MAX_AGE_HOURS", "junk")
    config = load_config()
    assert config.publish_max_per_cycle == 10
    assert config.publish_max_age_hours == 48.0


def test_subscriber_counters_stay_integers(monkeypatch) -> None:
    monkeypatch.setenv("SUBSCRIBER_MAX_PER_CYCLE", "5")
    monkeypatch.setenv("SUBSCRIBER_MAX_ATTEMPTS", "2")
    config = load_config()
    assert config.subscriber_max_per_cycle == 5
    assert isinstance(config.subscriber_max_per_cycle, int)
    assert config.subscriber_max_attempts == 2
