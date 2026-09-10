"""Tests for deduplication (M2): hash/URL identity, no reprocessing."""

from __future__ import annotations

from pathlib import Path

from telecom_news.models import Article
from telecom_news.processors.dedup import is_known, store_new
from telecom_news.storage import Database


def _article(hash_: str, url: str) -> Article:
    return Article(url=url, source_id="t", title="T", body="B", content_hash=hash_)


def test_store_new_then_duplicate(tmp_path: Path) -> None:
    db = Database(tmp_path / "t.db")
    article = _article("h1", "https://example.com/1")

    assert is_known(db, article) is False
    assert store_new(db, article) == (1, True)
    assert is_known(db, article) is True
    assert store_new(db, article) == (1, False)


def test_same_url_different_hash_is_duplicate(tmp_path: Path) -> None:
    db = Database(tmp_path / "t.db")
    store_new(db, _article("h1", "https://example.com/1"))
    assert store_new(db, _article("h2", "https://example.com/1")) == (1, False)


def test_same_hash_different_url_is_duplicate(tmp_path: Path) -> None:
    db = Database(tmp_path / "t.db")
    store_new(db, _article("h1", "https://example.com/1"))
    assert store_new(db, _article("h1", "https://example.com/2")) == (1, False)


def test_repeated_store_preserves_status(tmp_path: Path) -> None:
    db = Database(tmp_path / "t.db")
    article = _article("h1", "https://example.com/1")
    store_new(db, article)
    db.set_status(1, "processed")

    assert store_new(db, article) == (1, False)
    assert db.get_unprocessed(status="processed")[0].id == 1
