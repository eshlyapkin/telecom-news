"""SQLite storage (M2, see ARCHITECTURE.md 3.5).

Persists :class:`Article` records with stable identity (``content_hash`` and
normalized ``url``, both UNIQUE) and a processing ``status``. Single-process
use; the UNIQUE constraints are the deduplication backstop. Timestamps are
stored as ISO 8601 text, ``llm_result`` as JSON text.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..models import Article

logger = logging.getLogger(__name__)

STATUSES = ("new", "processed", "published", "skipped", "error")

SCHEMA = """
CREATE TABLE IF NOT EXISTS articles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    url TEXT NOT NULL UNIQUE,
    content_hash TEXT NOT NULL UNIQUE,
    source_id TEXT NOT NULL DEFAULT '',
    source_url TEXT,
    title TEXT NOT NULL DEFAULT '',
    summary_raw TEXT NOT NULL DEFAULT '',
    body TEXT NOT NULL DEFAULT '',
    language TEXT NOT NULL DEFAULT 'en',
    published_at TEXT,
    fetched_at TEXT,
    status TEXT NOT NULL DEFAULT 'new',
    relevance TEXT,
    category TEXT,
    llm_result TEXT,
    published_at_telegram TEXT
);
CREATE INDEX IF NOT EXISTS idx_articles_status ON articles(status);
CREATE INDEX IF NOT EXISTS idx_articles_source ON articles(source_id);

CREATE TABLE IF NOT EXISTS source_health (
    source_id TEXT PRIMARY KEY,
    last_checked_at TEXT NOT NULL,
    last_success_at TEXT,
    last_error TEXT,
    last_item_count INTEGER NOT NULL DEFAULT 0
);
"""

_INSERT_SQL = """
INSERT INTO articles (
    url, content_hash, source_id, source_url, title, summary_raw, body,
    language, published_at, fetched_at, status, relevance, category,
    llm_result, published_at_telegram
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""


def _dt_to_text(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _text_to_dt(value: str | None) -> datetime | None:
    if value is None:
        return None
    return datetime.fromisoformat(value)


def _validate_status(status: str) -> None:
    if status not in STATUSES:
        raise ValueError(f"unknown status {status!r}; expected one of {', '.join(STATUSES)}")


def _row_to_article(row: sqlite3.Row) -> Article:
    llm_raw = row["llm_result"]
    return Article(
        id=row["id"],
        url=row["url"],
        content_hash=row["content_hash"],
        source_id=row["source_id"] or "",
        source_url=row["source_url"],
        title=row["title"] or "",
        summary_raw=row["summary_raw"] or "",
        body=row["body"] or "",
        language=row["language"] or "en",
        published_at=_text_to_dt(row["published_at"]),
        fetched_at=_text_to_dt(row["fetched_at"]),
        status=row["status"] or "new",
        relevance=row["relevance"],
        category=row["category"],
        llm_result=json.loads(llm_raw) if llm_raw else None,
        published_at_telegram=_text_to_dt(row["published_at_telegram"]),
    )


class Database:
    """SQLite-backed article store. Creates schema and directories on first use."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.executescript(SCHEMA)
        return conn

    def find_id(self, *, content_hash: str, url: str) -> int | None:
        """Id of the article with this hash or url, or None when unknown."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT id FROM articles WHERE content_hash = ? OR url = ?",
                (content_hash, url),
            ).fetchone()
            return int(row["id"]) if row is not None else None

    def upsert_by_hash(self, article: Article) -> tuple[int, bool]:
        """Insert ``article`` unless known by hash or url.

        Returns ``(article id, is_new)``. Known rows are left untouched, so
        repeated runs never reprocess or duplicate anything.
        """
        known_id = self.find_id(content_hash=article.content_hash, url=article.url)
        if known_id is not None:
            return known_id, False
        with self._connect() as conn:
            cursor = conn.execute(
                _INSERT_SQL,
                (
                    article.url,
                    article.content_hash,
                    article.source_id,
                    article.source_url,
                    article.title,
                    article.summary_raw,
                    article.body,
                    article.language,
                    _dt_to_text(article.published_at),
                    _dt_to_text(article.fetched_at),
                    article.status or "new",
                    article.relevance,
                    article.category,
                    json.dumps(article.llm_result) if article.llm_result is not None else None,
                    _dt_to_text(article.published_at_telegram),
                ),
            )
            article_id = int(cursor.lastrowid or 0)
        logger.debug("stored article id=%d url=%s", article_id, article.url)
        return article_id, True

    def get_unprocessed(self, *, status: str = "new", limit: int | None = None) -> list[Article]:
        """Articles with the given ``status``, oldest first (at most ``limit``)."""
        query = "SELECT * FROM articles WHERE status = ? ORDER BY id"
        params: tuple = (status,)
        if limit is not None:
            query += " LIMIT ?"
            params = (status, limit)
        with self._connect() as conn:
            return [_row_to_article(row) for row in conn.execute(query, params)]

    def set_status(self, article_id: int, status: str) -> None:
        """Move one article to ``status``. Unknown ids/statuses raise."""
        _validate_status(status)
        with self._connect() as conn:
            cursor = conn.execute(
                "UPDATE articles SET status = ? WHERE id = ?", (status, article_id)
            )
            if cursor.rowcount == 0:
                raise KeyError(f"no article with id {article_id}")

    def save_processing_result(
        self,
        article_id: int,
        *,
        relevance: str,
        category: str | None,
        llm_result: dict[str, Any],
        status: str,
    ) -> None:
        """Persist LLM results and move the article to ``status`` (M3)."""
        _validate_status(status)
        if relevance not in ("relevant", "irrelevant"):
            raise ValueError(f"unknown relevance {relevance!r}; expected 'relevant'/'irrelevant'")
        with self._connect() as conn:
            cursor = conn.execute(
                "UPDATE articles SET relevance = ?, category = ?, llm_result = ?, status = ? "
                "WHERE id = ?",
                (relevance, category, json.dumps(llm_result), status, article_id),
            )
            if cursor.rowcount == 0:
                raise KeyError(f"no article with id {article_id}")

    def mark_published(self, article_id: int, *, published_at: datetime | None = None) -> bool:
        """Atomically mark a processed article as published.

        Returns false when the row is missing or no longer has ``processed``
        status, preventing a concurrent/repeated publish from duplicating it.
        """
        published_at = published_at or datetime.now(timezone.utc)
        with self._connect() as conn:
            cursor = conn.execute(
                "UPDATE articles SET status = 'published', published_at_telegram = ? "
                "WHERE id = ? AND status = 'processed'",
                (_dt_to_text(published_at), article_id),
            )
            return cursor.rowcount == 1

    def record_source_health(
        self, source_id: str, *, success: bool, item_count: int | None = 0, error: str | None = None
    ) -> None:
        """Record a collection result; ``None`` preserves the item count."""
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            if item_count is None:
                cursor = conn.execute(
                    "UPDATE source_health SET last_checked_at = ?, "
                    "last_success_at = CASE WHEN ? THEN ? ELSE last_success_at END, "
                    "last_error = ? WHERE source_id = ?",
                    (now, success, now if success else None, error, source_id),
                )
                if cursor.rowcount:
                    return
                item_count = 0
            conn.execute(
                "INSERT INTO source_health "
                "(source_id, last_checked_at, last_success_at, last_error, last_item_count) "
                "VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(source_id) DO UPDATE SET "
                "last_checked_at = excluded.last_checked_at, "
                "last_success_at = COALESCE(excluded.last_success_at, "
                "source_health.last_success_at), "
                "last_error = excluded.last_error, last_item_count = excluded.last_item_count",
                (source_id, now, now if success else None, error, item_count),
            )

    def source_health(self) -> list[dict[str, Any]]:
        """Return source health records ordered by source id."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT source_id, last_checked_at, last_success_at, last_error, "
                "last_item_count FROM source_health ORDER BY source_id"
            )
            return [dict(row) for row in rows]

    def integrity_check(self) -> str:
        """Return SQLite's integrity-check result (normally ``ok``)."""
        with self._connect() as conn:
            row = conn.execute("PRAGMA integrity_check").fetchone()
            return str(row[0]) if row else "unknown"

    def backup_to(self, destination: Path | str) -> Path:
        """Create a consistent SQLite backup using SQLite's backup API."""
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as source, sqlite3.connect(destination) as target:
            source.backup(target)
        os.chmod(destination, 0o600)
        return destination

    @staticmethod
    def restore_from(backup: Path | str, destination: Path | str) -> Path:
        """Restore a verified backup atomically into ``destination``."""
        backup = Path(backup)
        destination = Path(destination)
        if not backup.exists():
            raise FileNotFoundError(backup)
        with sqlite3.connect(backup) as source:
            result = source.execute("PRAGMA integrity_check").fetchone()
            if not result or result[0] != "ok":
                raise ValueError(
                    f"backup failed integrity check: {result[0] if result else 'unknown'}"
                )
            destination.parent.mkdir(parents=True, exist_ok=True)
            temporary = destination.with_suffix(destination.suffix + ".restore.tmp")
            try:
                with sqlite3.connect(temporary) as target:
                    source.backup(target)
                os.chmod(temporary, 0o600)
                temporary.replace(destination)
                os.chmod(destination, 0o600)
            finally:
                temporary.unlink(missing_ok=True)
        return destination

    def reset_errors(self, limit: int | None = None) -> int:
        """Return error articles to ``new`` for a controlled retry."""
        with self._connect() as conn:
            if limit is None:
                cursor = conn.execute("UPDATE articles SET status = 'new' WHERE status = 'error'")
            else:
                rows = conn.execute(
                    "SELECT id FROM articles WHERE status = 'error' ORDER BY id LIMIT ?", (limit,)
                ).fetchall()
                ids = [row["id"] for row in rows]
                if not ids:
                    return 0
                placeholders = ",".join("?" for _ in ids)
                cursor = conn.execute(
                    f"UPDATE articles SET status = 'new' WHERE id IN ({placeholders})", ids
                )
            return cursor.rowcount

    def count_by_status(self) -> dict[str, int]:
        """Article counters per status (all known statuses always present)."""
        with self._connect() as conn:
            rows = conn.execute("SELECT status, COUNT(*) AS n FROM articles GROUP BY status")
            counts = {status: 0 for status in STATUSES}
            for row in rows:
                counts[row["status"]] = int(row["n"])
            return counts
