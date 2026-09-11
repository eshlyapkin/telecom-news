"""Freshness guard for collected items (D-017)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from telecom_news.processors import is_stale

NOW = datetime(2026, 9, 11, 12, 0, 0, tzinfo=timezone.utc)


def test_recent_items_are_kept() -> None:
    assert not is_stale(NOW - timedelta(days=1), 30, now=NOW)
    assert not is_stale(NOW, 30, now=NOW)


def test_old_items_are_stale() -> None:
    assert is_stale(NOW - timedelta(days=31), 30, now=NOW)
    assert is_stale(datetime(2021, 9, 2, tzinfo=timezone.utc), 30, now=NOW)


def test_missing_date_is_never_stale() -> None:
    """An undated item is not proof of old age — keep it and let the LLM judge."""
    assert not is_stale(None, 30, now=NOW)


def test_zero_and_negative_disable_the_guard() -> None:
    assert not is_stale(datetime(2000, 1, 1, tzinfo=timezone.utc), 0, now=NOW)
    assert not is_stale(datetime(2000, 1, 1, tzinfo=timezone.utc), -5, now=NOW)
    assert not is_stale(datetime(2000, 1, 1, tzinfo=timezone.utc), None, now=NOW)


def test_naive_timestamps_are_read_as_utc() -> None:
    naive = (NOW - timedelta(days=40)).replace(tzinfo=None)
    assert is_stale(naive, 30, now=NOW)
