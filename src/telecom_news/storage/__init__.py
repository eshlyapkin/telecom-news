"""Article storage (M2): SQLite persistence, no business logic."""

from .database import STATUSES, Database

__all__ = ["STATUSES", "Database"]
