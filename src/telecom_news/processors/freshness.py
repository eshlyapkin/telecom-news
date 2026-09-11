"""Freshness guard for collected items (D-017).

Dormant blogs and archive pages keep serving years-old entries: the 2026-09-11
catalog import surfaced feeds whose newest item was from 2021. Storing them
would push old news into the channel, so ``collect`` drops anything older than
``TELECOM_NEWS_MAX_ARTICLE_AGE_DAYS`` (default 30; ``0`` disables the guard).

Items without a date are kept: a missing date is not proof of old age, and
several feeds publish dates only in their markup.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone


def is_stale(
    published_at: datetime | None,
    max_age_days: int | None,
    *,
    now: datetime | None = None,
) -> bool:
    """True when ``published_at`` is older than ``max_age_days`` (0 = never)."""
    if not max_age_days or max_age_days <= 0:
        return False
    if published_at is None:
        return False
    moment = now or datetime.now(timezone.utc)
    if published_at.tzinfo is None:
        published_at = published_at.replace(tzinfo=timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return published_at < moment - timedelta(days=max_age_days)
