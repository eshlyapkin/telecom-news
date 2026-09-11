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


SUPPORTED_LANGS: tuple[str, ...] = ("ru", "en")


@dataclass(frozen=True)
class Config:
    """Runtime configuration. Defaults + env overrides, no side effects."""

    project_root: Path = field(default_factory=_project_root)
    data_dir: Path = field(init=False)
    db_path: Path = field(init=False)
    log_level: str = "INFO"
    lmstudio_base_url: str = "http://localhost:1234/v1"
    lmstudio_model: str = ""  # empty = first model loaded in LM Studio
    # Languages of the public channel(s). One post per language; the first entry
    # is the primary one (it also fills llm_result.summary for compatibility).
    target_langs: tuple[str, ...] = ("ru",)
    channel_chat_ids: tuple[tuple[str, str], ...] = ()  # (lang, chat_id), filled from env
    subscriber_max_age_hours: float = 24.0
    subscriber_max_per_cycle: int = 10
    subscriber_max_attempts: int = 3
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
        raw_langs = os.environ.get("TELECOM_NEWS_TARGET_LANGS")
        if raw_langs is None:
            raw_langs = os.environ.get("TELECOM_NEWS_TARGET_LANG") or "ru"
        langs = tuple(
            dict.fromkeys(part.strip().lower() for part in raw_langs.split(",") if part.strip())
        )
        unsupported = [lang for lang in langs if lang not in SUPPORTED_LANGS]
        if not langs or unsupported:
            raise ValueError(
                f"unsupported publication language(s) {unsupported or raw_langs!r}; "
                f"supported: {', '.join(SUPPORTED_LANGS)}"
            )
        object.__setattr__(self, "target_langs", langs)
        channel_ids: list[tuple[str, str]] = []
        default_chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
        for index, lang in enumerate(langs):
            specific = os.environ.get(f"TELEGRAM_CHAT_ID_{lang.upper()}", "")
            chat_id = specific or (default_chat_id if index == 0 else "")
            if chat_id:
                channel_ids.append((lang, chat_id))
        object.__setattr__(self, "channel_chat_ids", tuple(channel_ids))
        for env_name, attribute in (
            ("SUBSCRIBER_MAX_AGE_HOURS", "subscriber_max_age_hours"),
            ("SUBSCRIBER_MAX_PER_CYCLE", "subscriber_max_per_cycle"),
            ("SUBSCRIBER_MAX_ATTEMPTS", "subscriber_max_attempts"),
        ):
            value = os.environ.get(env_name)
            if value:
                try:
                    object.__setattr__(self, attribute, max(0.0, float(value)))
                except ValueError:
                    pass
        object.__setattr__(self, "telegram_bot_token", os.environ.get("TELEGRAM_BOT_TOKEN", ""))
        object.__setattr__(self, "telegram_chat_id", os.environ.get("TELEGRAM_CHAT_ID", ""))
        interval = os.environ.get("TELEGRAM_MIN_INTERVAL")
        if interval:
            try:
                object.__setattr__(self, "telegram_min_interval", max(0.0, float(interval)))
            except ValueError:
                pass

    @property
    def target_lang(self) -> str:
        """Primary publication language (backwards-compatible accessor)."""
        return self.target_langs[0]

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
    # "strict" keeps the deterministic pre-LLM relevance guard (M6). "llm" lets
    # the model decide for narrow sources whose feed text is too thin for the
    # keyword guard (DECISIONS.md D-012).
    relevance_gate: str = "strict"


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
    # Messaging-focused feeds verified by hand on 2026-09-11 (D-014). They are
    # declared here — not in the catalog — so that a bulk `sources import` from
    # the research table cannot drop them: hand-written entries always win and
    # the matching table row is skipped.
    "mef-news": SourceConfig(
        id="mef-news",
        type=SOURCE_TYPE_RSS,
        url="https://mobileecosystemforum.com/feed/",
        language="en",
        enabled=True,
    ),  # A+ — MEF weekly digest: RCS/A2P/OTP business messaging
    "mobilesquared": SourceConfig(
        id="mobilesquared",
        type=SOURCE_TYPE_RSS,
        url="https://www.mobilesquared.co.uk/feed/",
        language="en",
        enabled=True,
    ),  # A+ — A2P SMS/RCS/WhatsApp market research; low volume, high precision
    # Russian telecom and digital communications feeds verified during M7
    # source discovery; all are structured RSS and do not require scraping.
    "content-review": SourceConfig(
        id="content-review",
        type=SOURCE_TYPE_RSS,
        url="https://content-review.com/feed.xml",
        language="ru",
        enabled=True,
        relevance_gate="llm",  # D-012: narrow feed, let the model decide
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
        relevance_gate="llm",  # D-012: narrow feed, let the model decide
    ),  # B+ — smishing/OTP fraud, telecom security; user verified
    "securitylab-analytics": SourceConfig(
        id="securitylab-analytics",
        type=SOURCE_TYPE_RSS,
        url="https://www.securitylab.ru/_Services/Export/RSS/analytics/",
        language="ru",
        enabled=True,
        relevance_gate="llm",  # D-012: narrow feed, let the model decide
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
        relevance_gate="llm",  # D-012: narrow feed, let the model decide
    ),  # B+ — SMS-bombing, OTP fraud; agent verified (items of 2026-09-11)
    "anti-malware-analytics": SourceConfig(
        id="anti-malware-analytics",
        type=SOURCE_TYPE_RSS,
        url="https://www.anti-malware.ru/taxonomy/term/76/feed",
        language="ru",
        enabled=True,
        relevance_gate="llm",  # D-012: narrow feed, let the model decide
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


def _catalog_news_sources() -> dict[str, SourceConfig]:
    """News entries of the catalog (``sources_catalog.CATALOG``) as configs.

    Status-page endpoints are deliberately excluded: they are JSON, not
    editorial feeds, and ``collect`` cannot parse them (D-016).
    """
    from .sources_catalog import CATALOG, KIND_NEWS

    return {
        entry.id: SourceConfig(
            id=entry.id,
            type=SOURCE_TYPE_RSS,
            url=entry.url,
            language=entry.language,
            enabled=entry.enabled,
            relevance_gate=entry.gate,
        )
        for entry in CATALOG
        if entry.kind == KIND_NEWS
    }


def _merge_catalog_sources() -> None:
    """Add catalog entries to ``SOURCES``; a collision is a configuration error."""
    catalog = _catalog_news_sources()
    duplicates = sorted(set(SOURCES) & set(catalog))
    if duplicates:
        raise ValueError(
            "source id declared both in SOURCES and in sources_catalog.CATALOG: "
            + ", ".join(duplicates)
        )
    SOURCES.update(catalog)


_merge_catalog_sources()


def get_source(source_id: str) -> SourceConfig | None:
    """Return the declared source by id, or None when unknown."""
    return SOURCES.get(source_id)
