"""Domain model of the application (M0).

``Article`` is a pure domain dataclass: it has no dependency on SQLite, LLM,
Telegram or any persistence layer. Storage-specific models and schema appear
separately in M2 (``storage/database.py``, see ARCHITECTURE.md 3.4/3.5).

Deduplication logic, LLM classification results and publication state are NOT
implemented here — the fields below only declare where such data will live.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional


def compute_content_hash(text: str) -> str:
    """Return a stable sha256 hex digest of the given text.

    Used as ``Article.content_hash`` — a stable content identifier that must
    be identical across repeated runs for the same normalized text.
    """
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass
class Article:
    """A single news item flowing through the pipeline (domain model).

    Minimal MVP field set per ARCHITECTURE.md 3.4. All timestamps are UTC.
    Fields that are filled in by later stages of the pipeline default to
    ``None`` so an article can be created with minimal data at collection time.
    """

    url: str
    source_id: str
    title: str = ""
    summary_raw: str = ""
    body: str = ""
    language: str = "en"  # 'ru' / 'en'
    published_at: Optional[datetime] = None  # UTC, from the source
    fetched_at: Optional[datetime] = None  # UTC, moment of collection
    content_hash: str = field(default="")

    # Filled in by later pipeline stages (M2–M4); not set at creation time.
    id: Optional[int] = None  # internal PK assigned by storage (M2)
    source_url: Optional[str] = None
    status: str = "new"  # new -> processed -> published (+ skipped, error)
    relevance: Optional[str] = None  # 'relevant' / 'irrelevant' (LLM result)
    category: Optional[str] = None  # technology/vendor/aggregator/...
    llm_result: Optional[dict[str, Any]] = None
    published_at_telegram: Optional[datetime] = None

    def __post_init__(self) -> None:
        if not self.content_hash:
            self.content_hash = compute_content_hash(self.body or self.title)
