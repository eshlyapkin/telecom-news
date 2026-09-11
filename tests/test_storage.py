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
    assert backup.stat().st_mode & 0o777 == 0o600
    assert Database(backup).integrity_check() == "ok"

    restored = tmp_path / "restored.db"
    Database.restore_from(backup, restored)
    assert Database(restored).get_unprocessed()[0].title == "Title 1"


def test_restore_rejects_missing_backup(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        Database.restore_from(tmp_path / "missing.db", tmp_path / "restored.db")


def test_reset_errors_returns_articles_to_new(tmp_path: Path) -> None:
    db = _db(tmp_path)
    db.upsert_by_hash(_article(1))
    db.set_status(1, "error")
    assert db.reset_errors() == 1
    assert db.count_by_status()["new"] == 1


def test_reset_errors_respects_limit(tmp_path: Path) -> None:
    db = _db(tmp_path)
    db.upsert_by_hash(_article(1))
    db.upsert_by_hash(_article(2))
    db.set_status(1, "error")
    db.set_status(2, "error")
    assert db.reset_errors(1) == 1
    assert db.count_by_status()["error"] == 1


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


def test_last_published_returns_newest_publication(tmp_path: Path) -> None:
    db = _db(tmp_path)
    assert db.last_published() is None

    db.upsert_by_hash(_article(1))
    db.upsert_by_hash(_article(2))
    db.save_processing_result(
        1, relevance="relevant", category="vendor", llm_result={}, status="processed"
    )
    db.save_processing_result(
        2, relevance="relevant", category="vendor", llm_result={}, status="processed"
    )
    older = datetime(2026, 9, 10, 15, 0, 0, tzinfo=timezone.utc)
    newer = datetime(2026, 9, 11, 12, 30, 0, tzinfo=timezone.utc)
    assert db.mark_published(1, published_at=older) is True
    assert db.mark_published(2, published_at=newer) is True

    published = db.last_published()
    assert published is not None
    assert published.id == 2
    assert published.published_at_telegram == newer


def test_oldest_with_status_picks_lowest_id(tmp_path: Path) -> None:
    db = _db(tmp_path)
    assert db.oldest_with_status("new") is None

    db.upsert_by_hash(_article(1))
    db.upsert_by_hash(_article(2))
    db.set_status(1, "skipped")

    oldest = db.oldest_with_status("new")
    assert oldest is not None and oldest.id == 2
    assert db.oldest_with_status("skipped").id == 1  # type: ignore[union-attr]
    with pytest.raises(ValueError):
        db.oldest_with_status("nope")


def test_mark_error_counts_attempts_and_reset_is_bounded(tmp_path: Path) -> None:
    db = _db(tmp_path)
    db.upsert_by_hash(_article(1))
    db.upsert_by_hash(_article(2))

    assert db.mark_error(1) == 1
    assert db.mark_error(1) == 2

    # Article 2 is healthy: it must stay reachable while article 1 is parked.
    assert db.reset_errors(max_attempts=2) == 0
    assert db.parked_errors(max_attempts=2) == 1
    assert db.mark_error(1) == 3
    assert db.reset_errors(max_attempts=3) == 0
    # A forced retry ignores the limit (manual recovery).
    assert db.reset_errors(max_attempts=None) == 1
    assert db.count_by_status()["new"] == 2


def test_reset_errors_returns_only_retryable_articles(tmp_path: Path) -> None:
    db = _db(tmp_path)
    db.upsert_by_hash(_article(1))
    db.upsert_by_hash(_article(2))
    db.upsert_by_hash(_article(3))
    db.set_status(3, "processed")  # keeps the 'new' counter unambiguous
    for _ in range(3):
        db.mark_error(1)
    db.mark_error(2)

    assert db.reset_errors(max_attempts=3) == 1
    assert db.count_by_status()["new"] == 1
    assert db.count_by_status()["error"] == 1  # only the parked article
    # Nothing retryable left, and the limit is respected for a new failure.
    assert db.reset_errors(max_attempts=3, limit=5) == 0
    db.mark_error(3)
    assert db.reset_errors(max_attempts=3, limit=1) == 1


def test_existing_database_gets_attempts_column(tmp_path: Path) -> None:
    """A database created before D-015 (no attempts column) is migrated in place."""
    import sqlite3

    path = tmp_path / "legacy.db"
    legacy = sqlite3.connect(path)
    legacy.executescript(
        "CREATE TABLE articles (id INTEGER PRIMARY KEY AUTOINCREMENT, url TEXT NOT NULL UNIQUE,"
        " content_hash TEXT NOT NULL UNIQUE, source_id TEXT, source_url TEXT, title TEXT,"
        " summary_raw TEXT, body TEXT, language TEXT, published_at TEXT, fetched_at TEXT,"
        " status TEXT NOT NULL DEFAULT 'new', relevance TEXT, category TEXT, llm_result TEXT,"
        " published_at_telegram TEXT);"
        "INSERT INTO articles (url, content_hash, title, status)"
        " VALUES ('https://example.com/old', 'hash-old', 'Old', 'error');"
    )
    legacy.commit()
    legacy.close()

    db = Database(path)
    assert db.count_by_status()["error"] == 1
    assert db.mark_error(1) == 1
    assert db.reset_errors(max_attempts=2) == 1
    assert db.get_unprocessed()[0].attempts == 1
