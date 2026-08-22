"""Minimal configuration for M0 (see ARCHITECTURE.md section 6).

Only what is actually needed at this milestone: project root, data directory
and log level. Values can be overridden through environment variables.

Deliberately NOT included yet (added in the milestones where they become
necessary): LM Studio endpoint/model (M3), Telegram token/chat_id (M4), RSS/API
source declarations (M1). No real network calls are made here.
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
    log_level: str = "INFO"

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "data_dir",
            Path(os.environ.get("TELECOM_NEWS_DATA_DIR", self.project_root / "data")),
        )
        env_level = os.environ.get("LOG_LEVEL")
        if env_level:
            object.__setattr__(self, "log_level", env_level.upper())

    @property
    def logs_dir(self) -> Path:
        return self.data_dir / "logs"


def load_config() -> Config:
    """Build a :class:`Config` from defaults and environment variables."""
    return Config()
