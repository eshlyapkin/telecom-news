"""CLI collect/status against a temporary database (M2). Network is mocked."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from telecom_news.cli import _cmd_collect, _cmd_status
from telecom_news.collectors.base import RawItem
from telecom_news.collectors.rss import RssCollector
from telecom_news.storage import Database


def _raw_items() -> list[RawItem]:
    return [
        RawItem(
            url="https://example.com/blog/a/?utm_source=feed",
            source_id="sinch-blog",
            title="First",
            published_at=datetime(2026, 9, 1, 10, 0, 0, tzinfo=timezone.utc),
            content="Body A",
            language="en",
        ),
        RawItem(
            url="https://example.com/blog/b/",
            source_id="sinch-blog",
            title="Second",
            published_at=None,
            content="Body B",
            language="en",
        ),
    ]


def test_collect_stores_new_articles(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("TELECOM_NEWS_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(RssCollector, "collect", lambda self, **kwargs: _raw_items())

    assert _cmd_collect("sinch-blog", None) == 0
    assert "Stored 2 new" in capsys.readouterr().out

    db = Database(tmp_path / "news.db")
    assert db.count_by_status()["new"] == 2
    (first,) = [a for a in db.get_unprocessed() if a.title == "First"]
    assert first.url == "https://example.com/blog/a/"  # tracking param stripped


def test_collect_twice_creates_no_duplicates(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("TELECOM_NEWS_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(RssCollector, "collect", lambda self, **kwargs: _raw_items())

    assert _cmd_collect("sinch-blog", None) == 0
    capsys.readouterr()
    assert _cmd_collect("sinch-blog", None) == 0
    assert "Stored 0 new" in capsys.readouterr().out
    assert Database(tmp_path / "news.db").count_by_status()["new"] == 2


def test_status_shows_counters(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("TELECOM_NEWS_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(RssCollector, "collect", lambda self, **kwargs: _raw_items())
    _cmd_collect("sinch-blog", None)
    capsys.readouterr()

    assert _cmd_status() == 0
    out = capsys.readouterr().out
    assert "new: 2" in out
    assert "total: 2" in out


def test_status_without_database(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("TELECOM_NEWS_DATA_DIR", str(tmp_path))
    assert _cmd_status() == 0
    assert "No database yet" in capsys.readouterr().out
