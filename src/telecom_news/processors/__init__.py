"""Article processors (M3: normalization, deduplication, LLM processing)."""

from .dedup import is_known, store_new
from .freshness import is_stale
from .normalize import ensure_utc, normalize_item, normalize_text, normalize_url
from .relevance import CATEGORIES, RelevanceResult, check_relevance, prepare_article_text
from .summarize import SummaryResult, summarize

__all__ = [
    "CATEGORIES",
    "RelevanceResult",
    "SummaryResult",
    "check_relevance",
    "ensure_utc",
    "is_stale",
    "is_known",
    "normalize_item",
    "normalize_text",
    "normalize_url",
    "prepare_article_text",
    "store_new",
    "summarize",
]
