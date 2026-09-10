"""Deduplication (M2, see ARCHITECTURE.md 3.6).

An article is a duplicate when its ``content_hash`` or normalized ``url`` is
already stored. New articles are persisted with status ``new``; known ones are
left untouched so repeated runs never reprocess anything. No semantic
deduplication (non-goal for MVP).
"""

from __future__ import annotations

from ..models import Article
from ..storage.database import Database


def is_known(db: Database, article: Article) -> bool:
    """True when an article with the same hash or url is already stored."""
    return db.find_id(content_hash=article.content_hash, url=article.url) is not None


def store_new(db: Database, article: Article) -> tuple[int, bool]:
    """Persist ``article`` unless known. Returns ``(article id, is_new)``."""
    return db.upsert_by_hash(article)
