"""RSS collector (M1, see ARCHITECTURE.md 3.2 and DECISIONS.md D-007).

Fetches a feed over HTTP (see :mod:`telecom_news.collectors.base`) and parses
it with ``feedparser`` into :class:`RawItem` records. Publication dates become
UTC datetimes; entries without a link are skipped because the URL is the
article's identity downstream.
"""

from __future__ import annotations

import calendar
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import feedparser
import httpx

from .base import (
    DEFAULT_BACKOFF_BASE,
    DEFAULT_MAX_RETRIES,
    DEFAULT_TIMEOUT,
    CollectorError,
    RawItem,
    fetch_url,
)

logger = logging.getLogger(__name__)


def _struct_to_utc(value: Any) -> datetime | None:
    """Convert a ``time.struct_time`` (assumed UTC) to an aware datetime."""
    if value is None:
        return None
    try:
        return datetime.fromtimestamp(calendar.timegm(value), tz=timezone.utc)
    except (TypeError, ValueError, OverflowError):
        return None


def _entry_content(entry: Any) -> str:
    """Best-effort article text: full ``content`` first, summary/description fallback."""
    content = entry.get("content")
    if content and content[0].get("value"):
        return content[0]["value"]
    return entry.get("summary") or entry.get("description") or ""


def parse_feed(
    feed_bytes: bytes, source_id: str, default_language: str | None = None
) -> list[RawItem]:
    """Parse RSS/Atom bytes into :class:`RawItem` records (no network)."""
    parsed = feedparser.parse(feed_bytes)
    if parsed.bozo and not parsed.entries:
        raise CollectorError(
            f"could not parse feed of source '{source_id}': {parsed.bozo_exception}"
        )
    items: list[RawItem] = []
    for entry in parsed.entries:
        url = (entry.get("link") or "").strip()
        if not url:
            logger.debug(
                "source '%s': skipping entry without link (title=%r)", source_id, entry.get("title")
            )
            continue
        published = _struct_to_utc(entry.get("published_parsed") or entry.get("updated_parsed"))
        items.append(
            RawItem(
                url=url,
                source_id=source_id,
                title=(entry.get("title") or "").strip(),
                published_at=published,
                content=_entry_content(entry),
                language=default_language,
            )
        )
    return items


@dataclass
class RssCollector:
    """Collects :class:`RawItem` records from one RSS/Atom feed."""

    source_id: str
    feed_url: str
    language: str | None = None
    timeout: float = DEFAULT_TIMEOUT
    max_retries: int = DEFAULT_MAX_RETRIES
    backoff_base: float = DEFAULT_BACKOFF_BASE

    def collect(
        self, *, limit: int | None = None, client: httpx.Client | None = None
    ) -> list[RawItem]:
        """Fetch the feed and return parsed items (at most ``limit``)."""
        body = fetch_url(
            self.feed_url,
            timeout=self.timeout,
            max_retries=self.max_retries,
            backoff_base=self.backoff_base,
            client=client,
        )
        items = parse_feed(body, self.source_id, self.language)
        if limit is not None:
            items = items[:limit]
        logger.info("source '%s': collected %d item(s)", self.source_id, len(items))
        return items
