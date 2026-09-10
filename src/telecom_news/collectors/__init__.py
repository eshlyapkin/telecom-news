"""Source collectors (M1): raw record retrieval, no normalization/storage."""

from .base import CollectorError, RawItem, fetch_url
from .rss import RssCollector, parse_feed

__all__ = ["CollectorError", "RawItem", "RssCollector", "fetch_url", "parse_feed"]
