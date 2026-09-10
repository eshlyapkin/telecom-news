"""Tests for SQLite storage (M2). Uses tmp_path; the real database is untouched."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from telecom_news.models import Article
from telecom_news.storage import Database


def _article(n: int = 1, **overrides) -> Article:
    base = {
        "url": f"https://example.com/news/{n}",
        "source_id": "test-source",
        "title": f"Title {n}",
        "body": f"Body {n}",
        "language": "en",
        "published_at": datetime(2026, 9, 1, 12, 0, 0, tzinfo=timezone.utc),
        "fetched_at": datetime(2026, 9, 10, 12, 0, 0, tzinfo=timezone.utc),
        "content_hash": f"hash-{n:04d}",
    }
    base.update(overrides)
    return Article(**base)


def _db(tmp_path: Path) -> Database:
    return Database(tmp_path / "test.db")


def test_upsert_inserts_new_article(tmp_path: Path) -> None:
    article_id, is_new = _db(tmp_path).upsert_by_hash(_article(1))
    assert is_new is True
    assert article_id == 1


def test_stored_article_round_trips(tmp_path: Path) -> None:
    db = _db(tmp_path)
    db.upsert_by_hash(_article(1, category="vendor", llm_result={"summary": "hi"}))

    (stored,) = db.get_unprocessed()
    assert stored.id == 1
    assert stored.url == "https://example.com/news/1"
    assert stored.content_hash == "hash-0001"
    assert stored.title == "Title 1"
    assert stored.status == "new"
    assert stored.category == "vendor"
    assert stored.llm_result == {"summary": "hi"}
    assert stored.published_at == datetime(2026, 9, 1, 12, 0, 0, tzinfo=timezone.utc)
    assert stored.fetched_at == datetime(2026, 9, 10, 12, 0, 0, tzinfo=timezone.utc)


def test_upsert_same_hash_twice_returns_same_id(tmp_path: Path) -> None:
    db = _db(tmp_path)
    first_id, first_new = db.upsert_by_hash(_article(1))
    second_id, second_new = db.upsert_by_hash(_article(1))
    assert first_new is True
    assert second_new is False
    assert first_id == second_id
    assert sum(db.count_by_status().values()) == 1


def test_upsert_same_url_counts_as_duplicate(tmp_path: Path) -> None:
    db = _db(tmp_path)
    db.upsert_by_hash(_article(1))
    article_id, is_new = db.upsert_by_hash(_article(2, url="https://example.com/news/1"))
    assert is_new is False
    assert article_id == 1


def test_distinct_articles_get_distinct_ids(tmp_path: Path) -> None:
    db = _db(tmp_path)
    id1, _ = db.upsert_by_hash(_article(1))
    id2, new2 = db.upsert_by_hash(_article(2))
    assert new2 is True
    assert id1 != id2


def test_get_unprocessed_filters_by_status_and_limit(tmp_path: Path) -> None:
    db = _db(tmp_path)
    db.upsert_by_hash(_article(1))
    db.upsert_by_hash(_article(2))
    db.upsert_by_hash(_article(3))
    db.set_status(1, "processed")

    assert [a.id for a in db.get_unprocessed()] == [2, 3]
    assert [a.id for a in db.get_unprocessed(limit=1)] == [2]
    assert [a.id for a in db.get_unprocessed(status="processed")] == [1]


def test_status_transitions(tmp_path: Path) -> None:
    db = _db(tmp_path)
    (article_id, _) = db.upsert_by_hash(_article(1))
    for status in ("processed", "published"):
        db.set_status(article_id, status)
    counts = db.count_by_status()
    assert counts["published"] == 1
    assert counts["new"] == 0


def test_set_status_rejects_unknown_status_and_id(tmp_path: Path) -> None:
    db = _db(tmp_path)
    db.upsert_by_hash(_article(1))
    with pytest.raises(ValueError):
        db.set_status(1, "nope")
    with pytest.raises(KeyError):
        db.set_status(999, "processed")


def test_count_by_status_zero_on_fresh_db(tmp_path: Path) -> None:
    assert _db(tmp_path).count_by_status() == {
        "new": 0,
        "processed": 0,
        "published": 0,
        "skipped": 0,
        "error": 0,
    }


def test_backup_and_restore_round_trip(tmp_path: Path) -> None:
    source = Database(tmp_path / "live.db")
    source.upsert_by_hash(_article(1))
    backup = source.backup_to(tmp_path / "backups" / "news.db")
    assert backup.exists()
    assert Database(backup).integrity_check() == "ok"

    restored = tmp_path / "restored.db"
    Database.restore_from(backup, restored)
    assert Database(restored).get_unprocessed()[0].title == "Title 1"


def test_restore_rejects_missing_backup(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        Database.restore_from(tmp_path / "missing.db", tmp_path / "restored.db")


def test_data_survives_reopen(tmp_path: Path) -> None:
    path = tmp_path / "test.db"
    Database(path).upsert_by_hash(_article(1))

    reopened = Database(path)
    assert sum(reopened.count_by_status().values()) == 1
    assert reopened.get_unprocessed()[0].title == "Title 1"


def test_save_processing_result(tmp_path: Path) -> None:
    db = _db(tmp_path)
    db.upsert_by_hash(_article(1))
    db.save_processing_result(
        1,
        relevance="relevant",
        category="vendor",
        llm_result={"summary": "hi", "model": "m"},
        status="processed",
    )

    (stored,) = db.get_unprocessed(status="processed")
    assert stored.relevance == "relevant"
    assert stored.category == "vendor"
    assert stored.llm_result == {"summary": "hi", "model": "m"}
    assert db.count_by_status()["new"] == 0


def test_save_skipped_result(tmp_path: Path) -> None:
    db = _db(tmp_path)
    db.upsert_by_hash(_article(1))
    db.save_processing_result(
        1,
        relevance="irrelevant",
        category=None,
        llm_result={"relevant": False},
        status="skipped",
    )
    (stored,) = db.get_unprocessed(status="skipped")
    assert stored.relevance == "irrelevant"
    assert stored.category is None


def test_save_processing_result_rejects_bad_values(tmp_path: Path) -> None:
    db = _db(tmp_path)
    db.upsert_by_hash(_article(1))
    with pytest.raises(ValueError):
        db.save_processing_result(
            1, relevance="maybe", category=None, llm_result={}, status="processed"
        )
    with pytest.raises(ValueError):
        db.save_processing_result(
            1, relevance="relevant", category=None, llm_result={}, status="nope"
        )
    with pytest.raises(KeyError):
        db.save_processing_result(
            999, relevance="relevant", category=None, llm_result={}, status="processed"
        )
