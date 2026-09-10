"""Tests for collection source health persistence."""

from __future__ import annotations

from pathlib import Path

from telecom_news.storage import Database


def test_source_health_records_success_and_preserves_last_success(tmp_path: Path) -> None:
    db = Database(tmp_path / "news.db")
    db.record_source_health("source-a", success=True, item_count=4)
    first = db.source_health()[0]
    assert first["source_id"] == "source-a"
    assert first["last_success_at"] == first["last_checked_at"]
    assert first["last_item_count"] == 4
    assert first["last_error"] is None

    db.record_source_health("source-a", success=False, error="timeout")
    current = db.source_health()[0]
    assert current["last_success_at"] == first["last_success_at"]
    assert current["last_error"] == "timeout"
    assert current["last_item_count"] == 0
