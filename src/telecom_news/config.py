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
}


def get_source(source_id: str) -> SourceConfig | None:
    """Return the declared source by id, or None when unknown."""
    return SOURCES.get(source_id)
