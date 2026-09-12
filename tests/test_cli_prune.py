"""Tests for `prune` (D-020): clearing the pre-D-017 backlog of stale queue rows."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from telecom_news import cli
from telecom_news.models import Article
from telecom_news.storage import Database

NOW = datetime.now(timezone.utc)


def _seed(path: Path) -> Database:
    db = Database(path)
    rows = (
        (1, "new", NOW - timedelta(days=400)),  # stored before the collect guard: prunable
        (2, "new", NOW - timedelta(hours=2)),  # fresh: stays in the queue
        (3, "skipped", NOW - timedelta(days=400)),  # deduplication memory: never touched
        (4, "processed", NOW - timedelta(days=400)),  # already summarised: not a queue row
    )
    for number, status, moment in rows:
        db.upsert_by_hash(
            Article(
                url=f"https://example.com/{number}",
                source_id="dead-blog",
                title=f"Old news {number}",
                body="body",
                content_hash=f"hash-{number:04d}",
                published_at=moment,
                fetched_at=NOW,
                status=status,
            )
        )
        db.set_status(number, status)
    # An undated queued row: a missing date is not proof of old age, so it stays.
    db.upsert_by_hash(
        Article(
            url="https://example.com/undated",
            source_id="dead-blog",
            title="Undated",
            body="body",
            content_hash="hash-undated",
            fetched_at=NOW - timedelta(days=400),
        )
    )
    return db


def test_prune_dry_run_lists_and_keeps_everything(tmp_path: Path, capsys) -> None:
    path = tmp_path / "news.db"
    db = _seed(path)

    assert cli._cmd_prune(30, True, db_path=path) == 0

    out = capsys.readouterr().out
    assert "[1] dead-blog" in out and "Old news 1" in out
    assert "Dry run: 1 queued article(s) would be deleted." in out
    assert db.count_by_status()["new"] == 3


def test_prune_deletes_only_stale_queued_rows(tmp_path: Path, capsys) -> None:
    path = tmp_path / "news.db"
    db = _seed(path)

    assert cli._cmd_prune(30, False, db_path=path) == 0

    out = capsys.readouterr().out
    assert "Pruned 1 queued article(s) older than 30 day(s)." in out
    counts = db.count_by_status()
    assert counts["new"] == 2  # the fresh row and the undated one
    assert counts["skipped"] == 1 and counts["processed"] == 1
    assert db.find_id(content_hash="hash-0001", url="https://example.com/1") is None
    assert db.find_id(content_hash="hash-0002", url="https://example.com/2") == 2


def test_prune_uses_the_collect_threshold_by_default(tmp_path: Path, monkeypatch, capsys) -> None:
    path = tmp_path / "news.db"
    _seed(path)
    monkeypatch.setenv("TELECOM_NEWS_MAX_ARTICLE_AGE_DAYS", "90")

    assert cli._cmd_prune(None, False, db_path=path) == 0
    assert "older than 90 day(s)" in capsys.readouterr().out


def test_prune_without_stale_rows_is_a_noop(tmp_path: Path, capsys) -> None:
    path = tmp_path / "news.db"
    db = _seed(path)

    assert cli._cmd_prune(500, False, db_path=path) == 0
    assert "nothing to prune" in capsys.readouterr().out
    assert db.count_by_status()["new"] == 3


def test_prune_needs_a_positive_threshold(tmp_path: Path, capsys) -> None:
    path = tmp_path / "news.db"
    _seed(path)

    assert cli._cmd_prune(0, False, db_path=path) == 2
    err = capsys.readouterr().err
    assert "--max-age-days must be >= 1" in err


def test_prune_is_reachable_from_the_cli_parser(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("TELECOM_NEWS_DATA_DIR", str(tmp_path))
    _seed(tmp_path / "news.db")

    assert cli.main(["prune", "--max-age-days", "30", "--dry-run"]) == 0
    assert "would be deleted" in capsys.readouterr().out
