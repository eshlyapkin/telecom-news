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
    published_at_telegram TEXT,
    attempts INTEGER NOT NULL DEFAULT 0
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

CREATE TABLE IF NOT EXISTS renditions (
    article_id INTEGER NOT NULL,
    lang TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    summary TEXT NOT NULL DEFAULT '',
    model TEXT,
    created_at TEXT NOT NULL,
    PRIMARY KEY (article_id, lang)
);

CREATE TABLE IF NOT EXISTS subscribers (
    chat_id TEXT PRIMARY KEY,
    username TEXT,
    ui_lang TEXT NOT NULL DEFAULT 'ru',
    status TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS subscriber_languages (
    chat_id TEXT NOT NULL,
    lang TEXT NOT NULL,
    added_at TEXT NOT NULL,
    PRIMARY KEY (chat_id, lang)
);

CREATE TABLE IF NOT EXISTS deliveries (
    chat_id TEXT NOT NULL,
    article_id INTEGER NOT NULL,
    lang TEXT NOT NULL,
    message_id INTEGER,
    status TEXT NOT NULL DEFAULT 'sent',
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    sent_at TEXT,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (chat_id, article_id, lang)
);

CREATE TABLE IF NOT EXISTS bot_state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""

SUBSCRIBER_STATUSES = ("active", "stopped", "blocked")
DELIVERY_STATUSES = ("sent", "failed")

_INSERT_SQL = """
INSERT INTO articles (
    url, content_hash, source_id, source_url, title, summary_raw, body,
    language, published_at, fetched_at, status, relevance, category,
    llm_result, published_at_telegram
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""


def _dt_to_text(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _now_text() -> str:
    return datetime.now(timezone.utc).isoformat()


def _text_to_dt(value: str | None) -> datetime | None:
    if value is None:
        return None
    return datetime.fromisoformat(value)


def _as_utc(value: datetime) -> datetime:
    """Aware UTC copy of ``value``; a naive timestamp is read as UTC."""
    return (
        value.replace(tzinfo=timezone.utc)
        if value.tzinfo is None
        else value.astimezone(timezone.utc)
    )


def _validate_status(status: str) -> None:
    if status not in STATUSES:
        raise ValueError(f"unknown status {status!r}; expected one of {', '.join(STATUSES)}")


# Age of an article for every freshness window (D-020): its own publication
# date first, then the moment we posted it, then the collection time. One
# expression is shared by `publish`, `deliver` and `diagnose` so they cannot
# disagree about what "too old" means, and it mirrors
# `delivery.planner.article_moment`. A row where all three are NULL has no known
# age and is never treated as stale — a missing date is not proof of old age
# (the same rule as the collect-time guard, D-017).
FRESHNESS_SQL = "COALESCE(published_at, published_at_telegram, fetched_at)"


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
        attempts=int(row["attempts"] or 0) if "attempts" in row.keys() else 0,
    )


class Database:
    """SQLite-backed article store. Creates schema and directories on first use."""

    # Articles that were already in 'error' when the attempts counter did not
    # exist yet were requeued by every single run of the old code, so they are
    # treated as having exhausted their retries (D-015). `recover
    # --max-attempts 0` is the documented way to bring them back.
    PREVIOUS_ERROR_ATTEMPTS = 3

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.executescript(SCHEMA)
        self._migrate(conn)
        return conn

    @staticmethod
    def _migrate(conn: sqlite3.Connection) -> None:
        """Bring an existing database to the current schema (additive only).

        ``articles.attempts`` arrived after M7; databases created before it need
        the column, otherwise a permanently failing article is requeued by every
        ``run`` and blocks the queue (see DECISIONS.md D-015).
        """
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(articles)")}
        if "attempts" not in columns:
            conn.execute("ALTER TABLE articles ADD COLUMN attempts INTEGER NOT NULL DEFAULT 0")
            cursor = conn.execute(
                "UPDATE articles SET attempts = ? WHERE status = 'error'",
                (Database.PREVIOUS_ERROR_ATTEMPTS,),
            )
            logger.info(
                "migrated articles table: added attempts column; parked %d pre-existing error(s)",
                cursor.rowcount,
            )

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

    def last_published(self) -> Article | None:
        """Most recently published article, or None when nothing was published.

        Read-only helper for diagnostics (``telecom_news diagnose``).
        """
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM articles WHERE status = 'published' "
                "ORDER BY COALESCE(published_at_telegram, '') DESC, id DESC LIMIT 1"
            ).fetchone()
        return _row_to_article(row) if row is not None else None

    def oldest_with_status(self, status: str) -> Article | None:
        """Oldest article (lowest id) with ``status``, or None when there is none."""
        _validate_status(status)
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM articles WHERE status = ? ORDER BY id LIMIT 1", (status,)
            ).fetchone()
        return _row_to_article(row) if row is not None else None

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

    # --- renditions (multi-language content, M8) --------------------------

    def get_rendition(self, article_id: int, lang: str) -> dict[str, Any] | None:
        """Stored title/summary for ``(article, lang)``, or None."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT article_id, lang, title, summary, model, created_at "
                "FROM renditions WHERE article_id = ? AND lang = ?",
                (article_id, lang),
            ).fetchone()
        return dict(row) if row is not None else None

    def save_rendition(
        self,
        article_id: int,
        lang: str,
        *,
        title: str,
        summary: str,
        model: str | None = None,
    ) -> None:
        """Insert or replace the rendering of one article in one language."""
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO renditions (article_id, lang, title, summary, model, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(article_id, lang) DO UPDATE SET "
                "title = excluded.title, summary = excluded.summary, model = excluded.model, "
                "created_at = excluded.created_at",
                (article_id, lang, title, summary, model, _now_text()),
            )

    def rendition_languages(self, article_id: int) -> list[str]:
        """Languages already rendered for one article, sorted."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT lang FROM renditions WHERE article_id = ? ORDER BY lang", (article_id,)
            )
            return [str(row["lang"]) for row in rows]

    # --- subscribers (M8) -------------------------------------------------

    def upsert_subscriber(
        self,
        chat_id: str | int,
        *,
        username: str | None = None,
        ui_lang: str = "ru",
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """Register a user (or refresh ``last_seen_at``) and return the row."""
        chat = str(chat_id)
        timestamp = _dt_to_text(now or datetime.now(timezone.utc)) or _now_text()
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO subscribers (chat_id, username, ui_lang, status, created_at, "
                "last_seen_at) VALUES (?, ?, ?, 'active', ?, ?) "
                "ON CONFLICT(chat_id) DO UPDATE SET username = excluded.username, "
                "last_seen_at = excluded.last_seen_at, "
                "status = CASE WHEN subscribers.status = 'blocked' THEN 'blocked' "
                "ELSE subscribers.status END",
                (chat, username, ui_lang, timestamp, timestamp),
            )
            row = conn.execute("SELECT * FROM subscribers WHERE chat_id = ?", (chat,)).fetchone()
        return dict(row) if row is not None else {}

    def subscriber(self, chat_id: str | int) -> dict[str, Any] | None:
        """Subscriber row by chat id, or None."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM subscribers WHERE chat_id = ?", (str(chat_id),)
            ).fetchone()
        return dict(row) if row is not None else None

    def set_subscriber_status(self, chat_id: str | int, status: str) -> None:
        """Change subscription state (``active`` / ``stopped`` / ``blocked``)."""
        if status not in SUBSCRIBER_STATUSES:
            raise ValueError(f"unknown subscriber status {status!r}")
        with self._connect() as conn:
            cursor = conn.execute(
                "UPDATE subscribers SET status = ? WHERE chat_id = ?", (status, str(chat_id))
            )
            if cursor.rowcount == 0:
                raise KeyError(f"no subscriber with chat_id {chat_id}")

    def set_subscriber_ui_lang(self, chat_id: str | int, ui_lang: str) -> None:
        """Change the interface language of one subscriber."""
        with self._connect() as conn:
            cursor = conn.execute(
                "UPDATE subscribers SET ui_lang = ? WHERE chat_id = ?", (ui_lang, str(chat_id))
            )
            if cursor.rowcount == 0:
                raise KeyError(f"no subscriber with chat_id {chat_id}")

    def subscriber_languages(self, chat_id: str | int) -> list[str]:
        """Languages chosen by one subscriber, sorted."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT lang FROM subscriber_languages WHERE chat_id = ? ORDER BY lang",
                (str(chat_id),),
            )
            return [str(row["lang"]) for row in rows]

    def set_subscriber_languages(
        self, chat_id: str | int, langs: tuple[str, ...] | list[str], *, now: datetime | None = None
    ) -> list[str]:
        """Replace the language set of one subscriber; returns the new set."""
        timestamp = _dt_to_text(now or datetime.now(timezone.utc)) or _now_text()
        chat = str(chat_id)
        wanted = sorted({lang.strip().lower() for lang in langs if lang and lang.strip()})
        with self._connect() as conn:
            conn.execute("DELETE FROM subscriber_languages WHERE chat_id = ?", (chat,))
            conn.executemany(
                "INSERT INTO subscriber_languages (chat_id, lang, added_at) VALUES (?, ?, ?)",
                [(chat, lang, timestamp) for lang in wanted],
            )
        return wanted

    def toggle_subscriber_language(
        self, chat_id: str | int, lang: str, *, now: datetime | None = None
    ) -> list[str]:
        """Add or remove one language; returns the resulting set."""
        current = set(self.subscriber_languages(chat_id))
        if lang in current:
            current.discard(lang)
        else:
            current.add(lang)
        return self.set_subscriber_languages(chat_id, sorted(current), now=now)

    def subscribers_with_languages(self, *, status: str = "active") -> dict[str, list[str]]:
        """``{chat_id: [lang, ...]}`` for subscribers that have at least one language."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT s.chat_id AS chat_id, l.lang AS lang FROM subscribers s "
                "JOIN subscriber_languages l ON l.chat_id = s.chat_id "
                "WHERE s.status = ? ORDER BY s.chat_id, l.lang",
                (status,),
            ).fetchall()
        result: dict[str, list[str]] = {}
        for row in rows:
            result.setdefault(str(row["chat_id"]), []).append(str(row["lang"]))
        return result

    def list_subscribers(self) -> list[dict[str, Any]]:
        """All subscriber rows (any status), ordered by chat_id — for diagnose/ops."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT chat_id, username, ui_lang, status, created_at, last_seen_at "
                "FROM subscribers ORDER BY chat_id"
            ).fetchall()
        return [dict(row) for row in rows]

    # --- deliveries (idempotent fan-out, M8) ------------------------------

    def record_delivery(
        self,
        chat_id: str | int,
        article_id: int,
        lang: str,
        *,
        status: str = "sent",
        message_id: int | None = None,
        error: str | None = None,
    ) -> None:
        """Upsert one delivery attempt; ``sent`` rows are never sent again."""
        if status not in DELIVERY_STATUSES:
            raise ValueError(f"unknown delivery status {status!r}")
        now = _now_text()
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO deliveries (chat_id, article_id, lang, message_id, status, "
                "attempts, last_error, sent_at, updated_at) VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?) "
                "ON CONFLICT(chat_id, article_id, lang) DO UPDATE SET "
                "message_id = COALESCE(excluded.message_id, deliveries.message_id), "
                "status = excluded.status, attempts = deliveries.attempts + 1, "
                "last_error = excluded.last_error, "
                "sent_at = COALESCE(excluded.sent_at, deliveries.sent_at), "
                "updated_at = excluded.updated_at",
                (
                    str(chat_id),
                    article_id,
                    lang,
                    message_id,
                    status,
                    error,
                    now if status == "sent" else None,
                    now,
                ),
            )

    def sent_deliveries(
        self, *, chat_id: str | int | None = None, article_ids: list[int] | None = None
    ) -> set[tuple[str, int, str]]:
        """``{(chat_id, article_id, lang)}`` already delivered successfully."""
        query = "SELECT chat_id, article_id, lang FROM deliveries WHERE status = 'sent'"
        params: list[Any] = []
        if chat_id is not None:
            query += " AND chat_id = ?"
            params.append(str(chat_id))
        if article_ids:
            placeholders = ",".join("?" for _ in article_ids)
            query += f" AND article_id IN ({placeholders})"
            params.extend(article_ids)
        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return {(str(row["chat_id"]), int(row["article_id"]), str(row["lang"])) for row in rows}

    def failed_delivery_attempts(self, *, chat_id: str | int, article_id: int, lang: str) -> int:
        """How many times this delivery already failed (for retry limits)."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT attempts, status FROM deliveries WHERE chat_id = ? AND article_id = ? "
                "AND lang = ?",
                (str(chat_id), article_id, lang),
            ).fetchone()
        if row is None or row["status"] == "sent":
            return 0
        return int(row["attempts"])

    def recent_articles(
        self,
        *,
        statuses: tuple[str, ...] = ("processed", "published"),
        since: datetime | None = None,
        limit: int | None = None,
    ) -> list[Article]:
        """Articles in the given statuses, newest id last (fan-out candidates).

        ``since`` filters on :data:`FRESHNESS_SQL`. Rows without any timestamp
        are kept: they have no known age, and dropping them would silently hide
        articles from feeds that publish no date (D-020).

        The condition is appended to the WHERE clause, not to the finished
        query: the previous ``"... ORDER BY id" + " AND ... >= ?"`` produced
        ``ORDER BY id AND <expr>``, which SQLite happily accepts as a sort
        expression — so the window was silently not applied at all.
        """
        for status in statuses:
            _validate_status(status)
        conditions = ["status IN (" + ",".join("?" for _ in statuses) + ")"]
        params: list[Any] = list(statuses)
        if since is not None:
            conditions.append(f"({FRESHNESS_SQL} IS NULL OR {FRESHNESS_SQL} >= ?)")
            params.append(_dt_to_text(since))
        query = "SELECT * FROM articles WHERE " + " AND ".join(conditions) + " ORDER BY id"
        if limit is not None:
            query += " LIMIT ?"
            params.append(limit)
        with self._connect() as conn:
            return [_row_to_article(row) for row in conn.execute(query, params)]

    def count_stale(self, *, status: str, cutoff: datetime) -> int:
        """Articles in ``status`` that are older than ``cutoff`` (no age = not stale).

        Read-only helper for `publish` and `diagnose` (D-020): it counts the rows
        a freshness window holds back, so they are reported instead of silently
        staying in the queue forever.
        """
        _validate_status(status)
        with self._connect() as conn:
            row = conn.execute(
                f"SELECT COUNT(*) AS n FROM articles WHERE status = ? "
                f"AND {FRESHNESS_SQL} IS NOT NULL AND {FRESHNESS_SQL} < ?",
                (status, _dt_to_text(cutoff)),
            ).fetchone()
        return int(row["n"]) if row is not None else 0

    def stale_articles(self, *, status: str = "new", cutoff: datetime) -> list[Article]:
        """Articles in ``status`` whose own publication date is before ``cutoff``.

        Used by `prune` (D-020) to clear the backlog collected before the
        collect-time freshness guard existed. Only ``published_at`` counts: these
        rows were never posted, and a missing date is not proof of old age. The
        comparison happens on parsed datetimes (naive values are read as UTC),
        not on ISO text: this selects rows for deletion.
        """
        _validate_status(status)
        limit_moment = _as_utc(cutoff)
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM articles WHERE status = ? AND published_at IS NOT NULL ORDER BY id",
                (status,),
            ).fetchall()
        articles = [_row_to_article(row) for row in rows]
        return [
            article
            for article in articles
            if article.published_at is not None and _as_utc(article.published_at) < limit_moment
        ]

    def delete_articles(self, article_ids: list[int] | tuple[int, ...]) -> int:
        """Delete articles and their renditions/delivery records. Returns the count.

        Only `prune` calls this, and only for rows that never reached the model,
        so no rendition or delivery normally exists; they are removed anyway to
        avoid orphaned rows.
        """
        ids = [int(article_id) for article_id in article_ids]
        if not ids:
            return 0
        placeholders = ",".join("?" for _ in ids)
        with self._connect() as conn:
            conn.execute(f"DELETE FROM renditions WHERE article_id IN ({placeholders})", ids)
            conn.execute(f"DELETE FROM deliveries WHERE article_id IN ({placeholders})", ids)
            cursor = conn.execute(f"DELETE FROM articles WHERE id IN ({placeholders})", ids)
            return cursor.rowcount

    # --- bot state (M8) ---------------------------------------------------

    def get_state(self, key: str) -> str | None:
        """Value of a bot key/value entry, or None."""
        with self._connect() as conn:
            row = conn.execute("SELECT value FROM bot_state WHERE key = ?", (key,)).fetchone()
        return str(row["value"]) if row is not None else None

    def set_state(self, key: str, value: str) -> None:
        """Store a bot key/value entry (e.g. the ``getUpdates`` offset)."""
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO bot_state (key, value, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value, "
                "updated_at = excluded.updated_at",
                (key, value, _now_text()),
            )

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

    def mark_error(self, article_id: int) -> int:
        """Move an article to ``error`` and count the attempt. Returns the count.

        The counter is what bounds retries: :meth:`reset_errors` refuses to
        requeue an article that failed ``max_attempts`` times, so a few broken
        items cannot starve the rest of the queue (D-015).
        """
        with self._connect() as conn:
            cursor = conn.execute(
                "UPDATE articles SET status = 'error', attempts = attempts + 1 WHERE id = ?",
                (article_id,),
            )
            if cursor.rowcount == 0:
                raise KeyError(f"no article with id {article_id}")
            row = conn.execute(
                "SELECT attempts FROM articles WHERE id = ?", (article_id,)
            ).fetchone()
        return int(row["attempts"]) if row is not None else 0

    def parked_errors(self, *, max_attempts: int = 3) -> int:
        """Error articles that exhausted their retries and stay parked."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM articles WHERE status = 'error' AND attempts >= ?",
                (max_attempts,),
            ).fetchone()
        return int(row["n"]) if row is not None else 0

    def reset_errors(self, limit: int | None = None, *, max_attempts: int = 3) -> int:
        """Return retryable error articles to ``new``.

        Articles that already failed ``max_attempts`` times are left in ``error``:
        requeueing them every run made ``run`` spend its whole processing budget
        on the same broken items (D-015). ``max_attempts=None`` is the manual,
        unconditional retry: it also clears the counter, so the article gets a
        full set of fresh attempts instead of parking again after one run.
        """
        condition = "status = 'error'"
        params: list[Any] = []
        if max_attempts is not None:
            condition += " AND attempts < ?"
            params.append(max_attempts)
        with self._connect() as conn:
            if limit is not None:
                rows = conn.execute(
                    f"SELECT id FROM articles WHERE {condition} ORDER BY id LIMIT ?",
                    (*params, limit),
                ).fetchall()
                ids = [row["id"] for row in rows]
                if not ids:
                    return 0
                placeholders = ",".join("?" for _ in ids)
                cursor = conn.execute(
                    f"UPDATE articles SET status = 'new' WHERE id IN ({placeholders})", ids
                )
            elif max_attempts is None:
                cursor = conn.execute(
                    "UPDATE articles SET status = 'new', attempts = 0 WHERE status = 'error'"
                )
            else:
                cursor = conn.execute(
                    f"UPDATE articles SET status = 'new' WHERE {condition}", params
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
