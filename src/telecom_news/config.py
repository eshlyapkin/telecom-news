"""Application configuration (see ARCHITECTURE.md section 6).

Defaults + environment overrides, no side effects. Only what is actually
needed so far: project root, data directory, SQLite path, log level, source
registry, LM Studio endpoint/model and the publication target language.
Telegram token/chat_id are read from environment for M4; no network calls
are made by configuration loading.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _project_root() -> Path:
    """Project root = parent of the ``src/`` directory containing this file."""
    return Path(__file__).resolve().parent.parent.parent


@dataclass(frozen=True)
class Config:
    """Runtime configuration. Defaults + env overrides, no side effects."""

    project_root: Path = field(default_factory=_project_root)
    data_dir: Path = field(init=False)
    db_path: Path = field(init=False)
    log_level: str = "INFO"
    lmstudio_base_url: str = "http://localhost:1234/v1"
    lmstudio_model: str = ""  # empty = first model loaded in LM Studio
    target_lang: str = "ru"  # publication language (ARCHITECTURE.md 3.9)
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    telegram_min_interval: float = 0.1

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "data_dir",
            Path(os.environ.get("TELECOM_NEWS_DATA_DIR", self.project_root / "data")),
        )
        object.__setattr__(
            self,
            "db_path",
            Path(os.environ.get("TELECOM_NEWS_DB", self.data_dir / "news.db")),
        )
        env_level = os.environ.get("LOG_LEVEL")
        if env_level:
            object.__setattr__(self, "log_level", env_level.upper())
        lmstudio_base_url = os.environ.get("LMSTUDIO_BASE_URL")
        if lmstudio_base_url:
            object.__setattr__(self, "lmstudio_base_url", lmstudio_base_url)
        lmstudio_model = os.environ.get("LMSTUDIO_MODEL")
        if lmstudio_model:
            object.__setattr__(self, "lmstudio_model", lmstudio_model)
        target_lang = os.environ.get("TELECOM_NEWS_TARGET_LANG")
        if target_lang:
            object.__setattr__(self, "target_lang", target_lang.lower())
        object.__setattr__(self, "telegram_bot_token", os.environ.get("TELEGRAM_BOT_TOKEN", ""))
        object.__setattr__(self, "telegram_chat_id", os.environ.get("TELEGRAM_CHAT_ID", ""))
        interval = os.environ.get("TELEGRAM_MIN_INTERVAL")
        if interval:
            try:
                object.__setattr__(self, "telegram_min_interval", max(0.0, float(interval)))
            except ValueError:
                pass

    @property
    def logs_dir(self) -> Path:
        return self.data_dir / "logs"


def load_config() -> Config:
    """Build a :class:`Config` from defaults and environment variables."""
    return Config()


SOURCE_TYPE_RSS = "rss"


@dataclass(frozen=True)
class SourceConfig:
    """Declaration of one news source (M1, see ARCHITECTURE.md section 6)."""

    id: str
    type: str = SOURCE_TYPE_RSS
    url: str = ""
    language: str = "en"
    enabled: bool = True


# First real source, chosen and verified in M1 (see DECISIONS.md D-007):
# Sinch customer communications blog — regular EN material on business
# messaging/CPaaS, fetchable structured RSS, no scraping required.
SOURCES: dict[str, SourceConfig] = {
    "sinch-blog": SourceConfig(
        id="sinch-blog",
        type=SOURCE_TYPE_RSS,
        url="https://sinch.com/blog/feed/",
        language="en",
        enabled=True,
    ),
    # Official feeds verified during M6 source discovery.
    "twilio-blog": SourceConfig(
        id="twilio-blog",
        type=SOURCE_TYPE_RSS,
        url="https://www.twilio.com/en-us/blog.feed.xml",
        language="en",
        enabled=True,
    ),
    "infobip-blog": SourceConfig(
        id="infobip-blog",
        type=SOURCE_TYPE_RSS,
        url="https://www.infobip.com/blog/feed",
        language="en",
        enabled=True,
    ),
    "gsma-newsroom": SourceConfig(
        id="gsma-newsroom",
        type=SOURCE_TYPE_RSS,
        url="https://www.gsma.com/newsroom/feed/",
        language="en",
        enabled=True,
    ),
    # Russian telecom and digital communications feeds verified during M7
    # source discovery; all are structured RSS and do not require scraping.
    "content-review": SourceConfig(
        id="content-review",
        type=SOURCE_TYPE_RSS,
        url="https://content-review.com/feed.xml",
        language="ru",
        enabled=True,
    ),
    "iksmedia": SourceConfig(
        id="iksmedia",
        type=SOURCE_TYPE_RSS,
        url="https://www.iksmedia.ru/rss/rss_yandex.rss",
        language="ru",
        enabled=True,
    ),
    "habr-cellular-news": SourceConfig(
        id="habr-cellular-news",
        type=SOURCE_TYPE_RSS,
        url="https://habr.com/ru/rss/hubs/cellular/news/?fl=ru",
        language="ru",
        enabled=True,
    ),
    # Russian telecom/security feeds added after the RU source review of
    # 2026-09-11 (see DECISIONS.md D-011). The grade in each trailing comment is
    # the domain-relevance rating from that review; every URL is the outlet's own
    # published feed endpoint with tracking parameters removed. "agent verified"
    # = fetched and fresh items seen by the agent on 2026-09-11; "user verified"
    # = confirmed by the user but not re-proven here (the agent sandbox has no
    # outbound network). Disabled entries stay declared so they can be switched
    # on without a code change (M6 rule); they skip collect/run/doctor.
    # --- CNews (official category feeds, application/xml) ---
    "cnews-telecom": SourceConfig(
        id="cnews-telecom",
        type=SOURCE_TYPE_RSS,
        url="https://www.cnews.ru/inc/rss/telecom.xml",
        language="ru",
        enabled=True,
    ),  # A — "Телеком"; agent verified
    "cnews-safe": SourceConfig(
        id="cnews-safe",
        type=SOURCE_TYPE_RSS,
        url="https://www.cnews.ru/inc/rss/safe.xml",
        language="ru",
        enabled=True,
    ),  # B+ — "Безопасность": SMS/OTP fraud, anti-fraud; agent verified
    "cnews-biz": SourceConfig(
        id="cnews-biz",
        type=SOURCE_TYPE_RSS,
        url="https://www.cnews.ru/inc/rss/biz.xml",
        language="ru",
        enabled=True,
    ),  # B+ — "ИКТ-бизнес": сделки, M&A, vendors; user verified
    "cnews-internet": SourceConfig(
        id="cnews-internet",
        type=SOURCE_TYPE_RSS,
        url="https://www.cnews.ru/inc/rss/internet.xml",
        language="ru",
        enabled=True,
    ),  # B-/C — мессенджеры и их регулирование; user verified
    "cnews-corp": SourceConfig(
        id="cnews-corp",
        type=SOURCE_TYPE_RSS,
        url="https://www.cnews.ru/inc/rss/corp.xml",
        language="ru",
        enabled=False,
    ),  # B — "Интеграция": mostly deployment PR, off to limit LLM load
    # --- SecurityLab (text/xml) ---
    "securitylab-news": SourceConfig(
        id="securitylab-news",
        type=SOURCE_TYPE_RSS,
        url="https://www.securitylab.ru/_Services/Export/RSS/news/",
        language="ru",
        enabled=True,
    ),  # B+ — smishing/OTP fraud, telecom security; user verified
    "securitylab-analytics": SourceConfig(
        id="securitylab-analytics",
        type=SOURCE_TYPE_RSS,
        url="https://www.securitylab.ru/_Services/Export/RSS/analytics/",
        language="ru",
        enabled=True,
    ),  # B — anti-fraud/security analytics; user verified
    "securitylab-vulnerabilities": SourceConfig(
        id="securitylab-vulnerabilities",
        type=SOURCE_TYPE_RSS,
        url="https://www.securitylab.ru/_Services/Export/RSS/vulnerabilities/",
        language="ru",
        enabled=False,
    ),  # B-/C — mostly non-messaging CVE noise
    # --- Anti-Malware.ru ---
    "anti-malware-news": SourceConfig(
        id="anti-malware-news",
        type=SOURCE_TYPE_RSS,
        url="https://www.anti-malware.ru/news/feed",
        language="ru",
        enabled=True,
    ),  # B+ — SMS-bombing, OTP fraud; agent verified (items of 2026-09-11)
    "anti-malware-analytics": SourceConfig(
        id="anti-malware-analytics",
        type=SOURCE_TYPE_RSS,
        url="https://www.anti-malware.ru/taxonomy/term/76/feed",
        language="ru",
        enabled=True,
    ),  # B — anti-fraud analytics; user verified
    "anti-malware-press": SourceConfig(
        id="anti-malware-press",
        type=SOURCE_TYPE_RSS,
        url="https://www.anti-malware.ru/press/feed",
        language="ru",
        enabled=False,
    ),  # B — press releases, overlaps anti-malware-news
    # --- NAG ---
    "nag-all": SourceConfig(
        id="nag-all",
        type=SOURCE_TYPE_RSS,
        url="https://nag.ru/rss/all",
        language="ru",
        enabled=False,
    ),  # A-/B+ — carriers/routing/regulation. The endpoint answers but serves
    # text/html, and the agent could not confirm entries through it. Enable
    # after a live `collect --source nag-all` returns items (note: collect exits
    # 2 on a disabled source, so flip `enabled` for that test run).
}


def get_source(source_id: str) -> SourceConfig | None:
    """Return the declared source by id, or None when unknown."""
    return SOURCES.get(source_id)
