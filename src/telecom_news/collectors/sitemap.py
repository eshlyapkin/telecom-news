"""Sitemap collector: read outlets that publish no RSS feed.

Several publishers in this domain have no feed at all (telecompaper.com) or hide
it behind a WAF, yet every one of them ships a sitemap for search engines. The
news-sitemap format carries exactly what the pipeline needs — the article URL,
its headline and its publication date — so it is a first-class source type next
to RSS rather than a scraping workaround.

Two shapes are handled. A ``<sitemapindex>`` lists child sitemaps; the ones whose
address mentions "news" are read first, then the most recently modified. A
``<urlset>`` lists the articles themselves: ``<news:title>`` and
``<news:publication_date>`` when the publisher uses the news extension,
``<lastmod>`` otherwise.

**An entry without a date is skipped.** A plain sitemap lists a site's whole
archive in no particular order, so without a date there is no way to tell this
week's article from one published in 2019 — and the freshness guard (D-017)
needs the date anyway. For entries that carry a date but no headline the page is
fetched once and its own ``<title>``/``og:title`` and description are used: that
is the page's declared metadata, not per-site scraping.
"""

from __future__ import annotations

import gzip
import html as html_entities
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse
from xml.etree import ElementTree

import httpx

from .base import (
    DEFAULT_BACKOFF_BASE,
    DEFAULT_MAX_RETRIES,
    DEFAULT_TIMEOUT,
    CollectorError,
    RawItem,
    fetch_url,
)

logger = logging.getLogger(__name__)

SITEMAP_NS = "http://www.sitemaps.org/schemas/sitemap/0.9"
NEWS_NS = "http://www.google.com/schemas/sitemap-news/0.9"

COMMON_SITEMAP_PATHS: tuple[str, ...] = (
    "/news-sitemap.xml",
    "/sitemap-news.xml",
    "/sitemap_index.xml",
    "/sitemap.xml",
    "/sitemapindex.xml",
)

# Bounds for one collection pass: a sitemap index can list hundreds of children
# and a urlset tens of thousands of URLs.
MAX_CHILD_SITEMAPS = 3
MAX_ITEMS = 40
MAX_TITLE_FETCHES = 15
MAX_DOCUMENT_BYTES = 20 * 1024 * 1024

_ROBOTS_SITEMAP_RE = re.compile(rb"(?im)^\s*sitemap:\s*(\S+)")
_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)
_META_RE = re.compile(r"<meta\s+[^>]*>", re.I)
_ATTR_RE = re.compile(r'(\w[\w:-]*)\s*=\s*"([^"]*)"|(\w[\w:-]*)\s*=\s*\'([^\']*)\'')
_TAG_RE = re.compile(r"<[^>]+>")


@dataclass
class SitemapEntry:
    """One ``<url>`` of a urlset."""

    url: str
    title: str = ""
    published_at: datetime | None = None


@dataclass
class SitemapDocument:
    """A parsed sitemap: either child sitemaps, or article entries."""

    children: list[str] = field(default_factory=list)
    entries: list[SitemapEntry] = field(default_factory=list)


def _decompress(body: bytes) -> bytes:
    """Sitemaps are routinely served gzipped (``.xml.gz``)."""
    if body[:2] == b"\x1f\x8b":
        try:
            return gzip.decompress(body)
        except OSError as exc:
            raise CollectorError(f"could not decompress sitemap: {exc}") from exc
    return body


def _parse_datetime(value: str) -> datetime | None:
    text = (value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        # Date-only lastmod ("2026-09-11") is valid in the sitemap spec.
        try:
            parsed = datetime.strptime(text[:10], "%Y-%m-%d")
        except ValueError:
            return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _localname(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def parse_sitemap(body: bytes) -> SitemapDocument:
    """Parse sitemap XML. Raises :class:`CollectorError` on unusable input."""
    body = _decompress(body)
    if len(body) > MAX_DOCUMENT_BYTES:
        raise CollectorError(f"sitemap is larger than {MAX_DOCUMENT_BYTES} bytes")
    # A sitemap never needs a DTD, and ElementTree expands internal entities:
    # refusing the declaration outright keeps a hostile document from turning
    # into an entity-expansion bomb in our process.
    head = body[:4096].lower()
    if b"<!doctype" in head or b"<!entity" in head:
        raise CollectorError("sitemap declares a DTD; refusing to parse it")

    try:
        root = ElementTree.fromstring(body)
    except ElementTree.ParseError as exc:
        raise CollectorError(f"could not parse sitemap: {exc}") from exc

    document = SitemapDocument()
    root_name = _localname(root.tag)
    if root_name == "sitemapindex":
        for child in root:
            loc = child.find(f"{{{SITEMAP_NS}}}loc")
            if loc is None:
                loc = next((node for node in child if _localname(node.tag) == "loc"), None)
            if loc is not None and (loc.text or "").strip():
                document.children.append(loc.text.strip())
        return document
    if root_name != "urlset":
        raise CollectorError(f"unexpected sitemap root element '{root_name}'")

    for url_node in root:
        loc = ""
        lastmod = ""
        title = ""
        published = ""
        for node in url_node:
            name = _localname(node.tag)
            if name == "loc":
                loc = (node.text or "").strip()
            elif name == "lastmod":
                lastmod = (node.text or "").strip()
            elif name == "news":
                for news_node in node:
                    news_name = _localname(news_node.tag)
                    if news_name == "title":
                        title = (news_node.text or "").strip()
                    elif news_name == "publication_date":
                        published = (news_node.text or "").strip()
        if not loc:
            continue
        document.entries.append(
            SitemapEntry(
                url=loc,
                title=title,
                published_at=_parse_datetime(published) or _parse_datetime(lastmod),
            )
        )
    return document


def discover_sitemaps(
    site_url: str,
    *,
    client: httpx.Client | None = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> list[str]:
    """Sitemap addresses a site declares in robots.txt, then the usual paths.

    What the site declares always comes before what we guess: a guessed
    ``/news-sitemap.xml`` that happens to 404 must not push the real, declared
    sitemap past the caller's probe budget. Inside each group a news sitemap
    wins, being short, dated and made of articles only.
    """
    declared: list[str] = []
    try:
        robots = fetch_url(
            urljoin(site_url, "/robots.txt"), timeout=timeout, max_retries=1, client=client
        )
        declared.extend(
            match.decode("utf-8", "replace").strip() for match in _ROBOTS_SITEMAP_RE.findall(robots)
        )
    except CollectorError as exc:
        logger.debug("sitemap: no robots.txt at %s (%s)", site_url, exc)
    guessed = [urljoin(site_url, path) for path in COMMON_SITEMAP_PATHS]

    def _news_first(urls: list[str]) -> list[str]:
        return sorted(urls, key=lambda url: 0 if "news" in urlparse(url).path.lower() else 1)

    ordered: list[str] = []
    seen: set[str] = set()
    for url in _news_first(declared) + _news_first(guessed):
        if url and url not in seen:
            seen.add(url)
            ordered.append(url)
    return ordered


def _meta_content(html: str, keys: tuple[str, ...]) -> str:
    """Value of the first ``<meta>`` whose name/property is one of ``keys``."""
    for tag in _META_RE.findall(html):
        attrs: dict[str, str] = {}
        for name_a, value_a, name_b, value_b in _ATTR_RE.findall(tag):
            key = (name_a or name_b).lower()
            attrs[key] = value_a or value_b
        label = (attrs.get("property") or attrs.get("name") or "").lower()
        if label in keys and attrs.get("content"):
            return attrs["content"].strip()
    return ""


def page_metadata(html: str) -> tuple[str, str]:
    """``(title, description)`` a page declares about itself.

    Entities are resolved: a title read straight out of the markup carries
    ``&#8211;`` and ``&#8217;`` where the page shows an en dash and an
    apostrophe, and that text goes on to the classifier prompt and the channel
    post ("SMSC &#8211; 30 years later").
    """
    title = _meta_content(html, ("og:title", "twitter:title"))
    if not title:
        match = _TITLE_RE.search(html)
        title = _TAG_RE.sub(" ", match.group(1)).strip() if match else ""
    description = _meta_content(html, ("og:description", "description", "twitter:description"))
    return html_entities.unescape(title).strip(), html_entities.unescape(description).strip()


@dataclass
class SitemapCollector:
    """Collects :class:`RawItem` records from one sitemap (or sitemap index)."""

    source_id: str
    sitemap_url: str
    language: str | None = None
    timeout: float = DEFAULT_TIMEOUT
    max_retries: int = DEFAULT_MAX_RETRIES
    backoff_base: float = DEFAULT_BACKOFF_BASE
    max_items: int = MAX_ITEMS
    max_child_sitemaps: int = MAX_CHILD_SITEMAPS
    max_title_fetches: int = MAX_TITLE_FETCHES

    def _fetch(self, url: str, client: httpx.Client | None) -> bytes:
        return fetch_url(
            url,
            timeout=self.timeout,
            max_retries=self.max_retries,
            backoff_base=self.backoff_base,
            client=client,
        )

    def collect(
        self, *, limit: int | None = None, client: httpx.Client | None = None
    ) -> list[RawItem]:
        """Read the sitemap and return dated article records, newest first."""
        document = parse_sitemap(self._fetch(self.sitemap_url, client))
        entries = list(document.entries)
        if document.children:
            children = sorted(
                document.children,
                key=lambda url: 0 if "news" in urlparse(url).path.lower() else 1,
            )[: self.max_child_sitemaps]
            for child_url in children:
                try:
                    child = parse_sitemap(self._fetch(child_url, client))
                except CollectorError as exc:
                    logger.debug("sitemap: skipping child %s (%s)", child_url, exc)
                    continue
                entries.extend(child.entries)

        dated = [entry for entry in entries if entry.published_at is not None]
        if not dated:
            raise CollectorError(
                f"sitemap of source '{self.source_id}' carries no publication dates; "
                "a plain archive listing cannot be told apart from fresh news"
            )
        dated.sort(key=lambda entry: entry.published_at, reverse=True)  # type: ignore[arg-type,return-value]
        dated = dated[: limit or self.max_items]

        items: list[RawItem] = []
        fetched_titles = 0
        for entry in dated:
            title, body = entry.title, ""
            if not title and fetched_titles < self.max_title_fetches:
                fetched_titles += 1
                try:
                    page = self._fetch(entry.url, client)
                except CollectorError as exc:
                    logger.debug("sitemap: cannot read %s (%s)", entry.url, exc)
                    continue
                title, body = page_metadata(page.decode("utf-8", errors="replace"))
            if not title:
                continue
            items.append(
                RawItem(
                    url=entry.url,
                    source_id=self.source_id,
                    title=title,
                    published_at=entry.published_at,
                    content=body,
                    language=self.language,
                )
            )
        logger.info("source '%s': collected %d item(s) from sitemap", self.source_id, len(items))
        return items
