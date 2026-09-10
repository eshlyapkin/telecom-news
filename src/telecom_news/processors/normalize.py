"""Normalization RawItem -> Article (M1, see ARCHITECTURE.md 3.3).

Pure functions: no network, no database. URL canonicalization (tracking
parameters removed), whitespace cleanup, UTC timestamps and a stable
``content_hash`` over the normalized text.

Deliberately NOT done in M1: HTML tag stripping (feed content stays raw
until LLM processing in M3), language detection (source-level label only),
deduplication (M2).
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from ..collectors.base import RawItem
from ..models import Article, compute_content_hash

# Best-effort list of marketing/tracking query parameters (plus any ``utm_*``).
_TRACKING_PARAMS = frozenset(
    {
        "fbclid",
        "gclid",
        "gclsrc",
        "dclid",
        "wbraid",
        "gbraid",
        "gad_source",
        "srsltid",
        "msclkid",
        "mc_cid",
        "mc_eid",
        "mc_c",
        "igshid",
        "twclid",
        "ttclid",
        "li_fat_id",
        "yclid",
        "pk_campaign",
        "pk_source",
        "pk_medium",
        "_hsenc",
        "_hsmi",
        "vero_id",
        "vero_conv",
        "nr_email_referer",
        "ircid",
        "rb_clickid",
    }
)
_UTM_PREFIX = "utm_"


def _is_tracking_param(name: str) -> bool:
    lowered = name.lower()
    return lowered.startswith(_UTM_PREFIX) or lowered in _TRACKING_PARAMS


def normalize_url(url: str) -> str:
    """Canonicalize a URL: trim, lowercase scheme/host, drop fragment and tracking params.

    Non-tracking query parameters are preserved. Malformed URLs (no scheme
    or host) are returned stripped but otherwise untouched.
    """
    url = url.strip()
    parts = urlsplit(url)
    if not parts.scheme or not parts.netloc:
        return url
    kept = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if not _is_tracking_param(key)
    ]
    return urlunsplit(
        (
            parts.scheme.lower(),
            parts.netloc.lower(),
            parts.path or "",
            urlencode(kept, doseq=True),
            "",
        )
    )


_WHITESPACE_RE = re.compile(r"\s+")


def normalize_text(text: str) -> str:
    """Collapse all whitespace runs to single spaces and trim."""
    return _WHITESPACE_RE.sub(" ", text).strip()


def ensure_utc(value: datetime | None) -> datetime | None:
    """Return an aware UTC datetime; naive values are assumed to be UTC."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def normalize_item(raw: RawItem, *, fetched_at: datetime | None = None) -> Article:
    """Convert one :class:`RawItem` into a domain :class:`Article`.

    The ``content_hash`` is computed over the normalized body (or title when
    the body is empty), so it is stable across repeated runs for identical
    source content. Language comes from the source-level label (M1 scope:
    no per-article detection).
    """
    title = normalize_text(raw.title or "")
    body = normalize_text(raw.content or "")
    return Article(
        url=normalize_url(raw.url),
        source_id=raw.source_id,
        title=title,
        body=body,
        language=raw.language or "en",
        published_at=ensure_utc(raw.published_at),
        fetched_at=fetched_at or datetime.now(timezone.utc),
        content_hash=compute_content_hash(body or title),
    )
