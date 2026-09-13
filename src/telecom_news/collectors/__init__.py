"""Source collectors (M1): raw record retrieval, no normalization/storage."""

from .base import CollectorError, RawItem, fetch_url
from .rss import RssCollector, parse_feed
from .sitemap import SitemapCollector, discover_sitemaps, parse_sitemap

__all__ = [
    "CollectorError",
    "RawItem",
    "RssCollector",
    "SitemapCollector",
    "discover_sitemaps",
    "fetch_url",
    "parse_feed",
    "parse_sitemap",
]
