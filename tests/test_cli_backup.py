"""Tests for backup retention."""

from __future__ import annotations

import os
import time
from pathlib import Path

from telecom_news.cli import _cmd_backup
from telecom_news.models import Article
from telecom_news.storage import Database


def test_backup_prunes_old_automatic_files_but_keeps_manual(tmp_path: Path, monkeypatch) -> None:
    data_dir = tmp_path / "data"
    db_path = data_dir / "news.db"
    Database(db_path).upsert_by_hash(
        Article(url="https://example.com/a", source_id="test", title="Title", body="Body")
    )
    backup_dir = data_dir / "backups"
    backup_dir.mkdir()
    old_auto = backup_dir / "news-old.db"
    old_auto.write_bytes(b"old")
    manual = backup_dir / "manual.db"
    manual.write_bytes(b"manual")
    old_time = time.time() - 20 * 86400
    os.utime(old_auto, (old_time, old_time))
    os.utime(manual, (old_time, old_time))
    monkeypatch.setenv("TELECOM_NEWS_DATA_DIR", str(data_dir))
    monkeypatch.setenv("TELECOM_NEWS_DB", str(db_path))

    assert _cmd_backup(backup_dir / "news-new.db", keep_days=14) == 0
    assert not old_auto.exists()
    assert manual.exists()
    assert (backup_dir / "news-new.db").stat().st_mode & 0o777 == 0o600
