"""Continuous source discovery: propose new feeds, never add them silently.

The registry only holds feeds somebody added by hand, so the pipeline can only
ever see as much of the world as the operator already knows about. This module
widens it: it takes the outbound domains that collected articles already link
to, probes each for an RSS/Atom feed, and scores the feed by how much of its
recent content passes the operator's *own* relevance gate — the messaging terms
from ``data/ai_rules.json``. Feeds above the threshold are written to
``data/source_candidates.json`` as candidates for the control panel; accepting
one hands it to :func:`custom_sources.add_source`, dismissing one keeps later
scans from proposing it again.

Two things seed a scan. The links of collected articles map the neighbourhood of
what the operator already reads; a topical news search widens it to the whole
world. The search is used to find **publishers**, never articles: Google News
answers with redirect links that end on a consent page and carry no article
text, but every entry names the publisher's own domain, so the scan probes that
domain for its real feed and the pipeline ends up reading the outlet directly —
with real URLs, real bodies and working deduplication.

No API key is needed. Search is one request per query, so keep the query list
short and the schedule daily; ``use_search=False`` turns it off entirely.
"""

from __future__ import annotations

import json
import logging
import re
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus, urljoin, urlparse

import feedparser

from .collectors.base import CollectorError, fetch_url
from .collectors.rss import parse_feed

logger = logging.getLogger(__name__)

_LOCK = threading.RLock()

# Probed after autodiscovery finds nothing; the order is the one publishers use.
COMMON_FEED_PATHS: tuple[str, ...] = (
    "/feed/",
    "/rss.xml",
    "/blog/feed/",
    "/news/feed/",
    "/atom.xml",
)

# A scan is interactive (the panel has a "Scan now" button), so it must not spend
# minutes on sites that answer nothing. A site whose front page we cannot even
# read is skipped outright, and probing stops after this many failed guesses.
PROBE_TIMEOUT = 6.0
MAX_FAILED_PROBES_PER_SITE = 3

# A candidate must look like a working feed with enough on-topic material. Both
# numbers are deliberately strict: a proposal the operator has to research costs
# more attention than a feed we simply never mention.
MIN_ITEMS = 3
MIN_HIT_RATE = 0.25
MAX_SAMPLE_TITLES = 5

# Topical search: one request per query, publisher domains taken from the feed's
# own `source` element. The locale follows the script of the query, so a Russian
# query reaches the Russian-language press instead of the US edition.
SEARCH_ENDPOINT = "https://news.google.com/rss/search"
SEARCH_LOCALES = {
    "en": {"hl": "en-US", "gl": "US", "ceid": "US:en"},
    "ru": {"hl": "ru", "gl": "RU", "ceid": "RU:ru"},
}
DEFAULT_QUERIES: tuple[str, ...] = (
    '"A2P SMS"',
    '"SMS aggregator" OR "messaging aggregator"',
    '"business messaging" SMS',
    '"SMS firewall" OR "SMS fraud" OR smishing',
    '"RCS business messaging"',
    'SMPP OR SMSC OR "SMS gateway"',
    "смс рассылки бизнес",
    "смс мошенничество операторы",
)

# Links that look like a feed or a page listing feeds. Assets are excluded: an
# "rss.svg" icon is not a feed, and thefastmode.com links exactly that next to
# the /rss-feeds page that does hold its feeds.
_FEEDISH_HREF_RE = re.compile(r'href=["\']([^"\'<>\s]*(?:rss|feed|atom)[^"\'<>\s]*)["\']', re.I)
_ASSET_SUFFIXES: tuple[str, ...] = (".svg", ".png", ".jpg", ".jpeg", ".gif", ".css", ".js", ".ico")
# Publishers routinely host their feeds elsewhere — thefastmode.com lists 14 of
# them on feeds.feedburner.com — so a feeds page may legitimately point off-site.
_FEED_HOSTS: tuple[str, ...] = ("feedburner.com", "feedpress.me", "feedblitz.com")
MAX_LINKS_FROM_FEED_PAGE = 6
MAX_SITEMAPS_PROBED_PER_SITE = 2

# What a scan is willing to propose. "both" tries the feed first and falls back
# to the sitemap, which is the useful default; the single-kind modes exist so an
# operator can say "only feeds, I do not want sitemap sources" or go looking
# specifically at the outlets that publish no feed.
LOOK_FOR_RSS = "rss"
LOOK_FOR_SITEMAP = "sitemap"
LOOK_FOR_BOTH = "both"
LOOK_FOR_CHOICES: tuple[str, ...] = (LOOK_FOR_BOTH, LOOK_FOR_RSS, LOOK_FOR_SITEMAP)

_CYRILLIC_RE = re.compile(r"[а-яё]", re.IGNORECASE)
_HREF_RE = re.compile(r'href=["\'](https?://[^"\'<>\s]+)["\']', re.IGNORECASE)
# Aggregators, social networks and CDNs: linked from everywhere, never a source.
_IGNORED_HOST_PARTS: tuple[str, ...] = (
    "google.",
    "facebook.",
    "twitter.",
    "x.com",
    "t.me",
    "telegram.",
    "youtube.",
    "youtu.be",
    "linkedin.",
    "instagram.",
    "vk.com",
    "ok.ru",
    "wikipedia.org",
    "archive.org",
    "amazonaws.com",
    "cloudfront.net",
    "gravatar.com",
    "doubleclick.",
    "githubusercontent.com",
    "w3.org",
    "schema.org",
    "apple.com",
    "play.google.com",
    # Widgets, booking and marketing infrastructure: they appear in the boilerplate
    # of half the articles and never publish news.
    "addtoany.com",
    "calendly.com",
    "hubspot.com",
    "eventbrite.",
    "zoom.us",
    "g2.com",
    "trustpilot.",
    "bit.ly",
    "lnkd.in",
)

# Service subdomains: the editorial content lives on the parent domain, so
# "app2.simpletexting.com" should be probed as "simpletexting.com".
_SERVICE_SUBDOMAINS: frozenset[str] = frozenset(
    {
        "app",
        "apps",
        "account",
        "accounts",
        "api",
        "auth",
        "cdn",
        "dashboard",
        "docs",
        "help",
        "img",
        "login",
        "mail",
        "media",
        "my",
        "portal",
        "shop",
        "static",
        "status",
        "store",
        "support",
        "cta-service-cms2",
    }
)


@dataclass
class Candidate:
    """One proposed feed, as stored in ``data/source_candidates.json``."""

    id: str
    feed_url: str
    site_url: str = ""
    title: str = ""
    language: str = "en"
    # "rss" or "sitemap" — an outlet without a feed can still be proposed if it
    # publishes a dated news sitemap.
    type: str = "rss"
    origin: str = ""
    items: int = 0
    hits: int = 0
    hit_rate: float = 0.0
    sample_titles: list[str] = field(default_factory=list)
    discovered_at: str = ""
    status: str = "new"  # new | dismissed

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class _FeedLinkParser(HTMLParser):
    """Collects ``<link rel="alternate" type="application/rss+xml">`` targets."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.feeds: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "link":
            return
        values = {key.lower(): (value or "") for key, value in attrs}
        rel = values.get("rel", "").lower()
        kind = values.get("type", "").lower()
        href = values.get("href", "").strip()
        if not href or "alternate" not in rel:
            return
        if "rss" in kind or "atom" in kind or kind == "application/xml":
            self.feeds.append(href)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def candidates_path(data_dir: Path | None = None) -> Path:
    if data_dir is not None:
        return Path(data_dir) / "source_candidates.json"
    from .config import load_config

    return load_config().data_dir / "source_candidates.json"


def load_state(data_dir: Path | None = None) -> dict[str, Any]:
    """Stored candidates plus the dismissed feed urls, tolerant of a broken file."""
    path = candidates_path(data_dir)
    empty: dict[str, Any] = {"candidates": [], "dismissed": [], "scanned_at": None}
    if not path.is_file():
        return empty
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return empty
    if not isinstance(raw, dict):
        return empty
    candidates = [item for item in raw.get("candidates", []) if isinstance(item, dict)]
    dismissed = [str(item) for item in raw.get("dismissed", []) if str(item).strip()]
    return {
        "candidates": candidates,
        "dismissed": dismissed,
        "scanned_at": raw.get("scanned_at"),
    }


def save_state(state: dict[str, Any], data_dir: Path | None = None) -> None:
    path = candidates_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def host_of(url: str) -> str:
    """Editorial host of a url: no ``www.`` and no service subdomain."""
    host = (urlparse(url).netloc or "").lower().split("@")[-1].split(":")[0]
    if host.startswith("www."):
        host = host[4:]
    label, _, rest = host.partition(".")
    # Strip only a leading service label, and only when something real remains
    # ("app.textmagic.com" -> "textmagic.com", but never "app.io" -> "io").
    if rest.count(".") >= 1 and (label in _SERVICE_SUBDOMAINS or re.fullmatch(r"app\d+", label)):
        return rest
    return host


def _is_ignorable(host: str) -> bool:
    return not host or any(part in host for part in _IGNORED_HOST_PARTS)


def known_hosts() -> set[str]:
    """Hosts already registered as sources (feed url and declared site)."""
    from .config import SOURCES

    return {host_of(source.url) for source in SOURCES.values() if source.url}


def candidate_id(feed_url: str) -> str:
    from .custom_sources import slug_from_url

    return slug_from_url(feed_url)


def seed_hosts(
    db: Any,
    *,
    limit: int = 15,
    scan_articles: int = 600,
    min_articles: int = 2,
) -> list[tuple[str, str]]:
    """External hosts worth probing, as ``(host, sample url)``, best first.

    Publishers quote and link each other, so the outbound links of a curated feed
    are the cheapest map of the neighbourhood the operator already cares about.
    Hosts are ranked by how many *distinct* articles link them, not by how often
    a link appears: a share widget or a booking form sits in the boilerplate of
    every article of one source, while a real neighbour is cited by several
    unrelated stories.
    """
    linking_articles: dict[str, set[int]] = {}
    samples: dict[str, str] = {}
    known = known_hosts()
    with db._connect() as conn:  # noqa: SLF001 — read-only, same package
        rows = conn.execute(
            "SELECT id, body FROM articles WHERE body != '' ORDER BY id DESC LIMIT ?",
            (max(1, int(scan_articles)),),
        )
        for row in rows:
            for url in set(_HREF_RE.findall(row["body"] or "")):
                host = host_of(url)
                if _is_ignorable(host) or host in known:
                    continue
                linking_articles.setdefault(host, set()).add(int(row["id"]))
                samples.setdefault(host, url)
    ranked = sorted(
        (host for host, ids in linking_articles.items() if len(ids) >= min_articles),
        key=lambda host: (-len(linking_articles[host]), host),
    )
    return [(host, samples[host]) for host in ranked[:limit]]


def queries_path(data_dir: Path | None = None) -> Path:
    if data_dir is not None:
        return Path(data_dir) / "discovery_queries.json"
    from .config import load_config

    return load_config().data_dir / "discovery_queries.json"


def load_queries(data_dir: Path | None = None) -> list[str]:
    """Operator's search topics, or the built-in defaults."""
    path = queries_path(data_dir)
    try:
        if not path.is_file():
            return list(DEFAULT_QUERIES)
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return list(DEFAULT_QUERIES)
    if isinstance(raw, dict):
        raw = raw.get("queries") or []
    if not isinstance(raw, list):
        return list(DEFAULT_QUERIES)
    queries = [str(item).strip() for item in raw if str(item).strip()]
    return queries or list(DEFAULT_QUERIES)


def save_queries(queries: list[str], data_dir: Path | None = None) -> list[str]:
    """Persist the search topics. An empty list restores the defaults."""
    cleaned = list(dict.fromkeys(str(item).strip() for item in queries if str(item).strip()))
    path = queries_path(data_dir)
    with _LOCK:
        path.parent.mkdir(parents=True, exist_ok=True)
        if not cleaned:
            path.unlink(missing_ok=True)
            return list(DEFAULT_QUERIES)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(
            json.dumps({"queries": cleaned}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(path)
    return cleaned


def search_url(query: str) -> str:
    """News-search feed address for one topic, in the locale of its script."""
    locale = SEARCH_LOCALES["ru" if _CYRILLIC_RE.search(query) else "en"]
    return f"{SEARCH_ENDPOINT}?q={quote_plus(query)}&" + "&".join(
        f"{key}={value}" for key, value in locale.items()
    )


def search_publisher_hosts(
    queries: list[str], *, client: Any | None = None
) -> list[tuple[str, str]]:
    """Publisher hosts a topical news search points at, most-cited first.

    Only the publisher is taken from the search result. Its links are redirects
    that end on a consent page and carry no article text, so they are useless as
    articles — but each entry names the outlet's own domain, which is exactly
    what the scan needs in order to go and find that outlet's real feed.
    """
    counts: dict[str, int] = {}
    known = known_hosts()
    for query in queries:
        try:
            body = fetch_url(search_url(query), timeout=PROBE_TIMEOUT, max_retries=1, client=client)
        except CollectorError as exc:
            logger.debug("discovery: search failed for %r: %s", query, exc)
            continue
        parsed = feedparser.parse(body)
        for entry in parsed.entries:
            source = entry.get("source") or {}
            href = str(source.get("href") or "")
            if not href:
                continue
            host = host_of(href)
            if _is_ignorable(host) or host in known:
                continue
            counts[host] = counts.get(host, 0) + 1
    ranked = sorted(counts, key=lambda host: (-counts[host], host))
    return [(host, f"https://{host}/") for host in ranked]


def _autodiscovered(html: str, base_url: str) -> list[str]:
    """Absolute feed addresses a page advertises with ``<link rel="alternate">``."""
    parser = _FeedLinkParser()
    try:
        parser.feed(html)
    except Exception:  # noqa: BLE001 — malformed markup must not stop a scan
        logger.debug("discovery: unparsable HTML at %s", base_url)
        return []
    return [urljoin(base_url, href) for href in parser.feeds]


def _feedish_links(html: str, base_url: str, *, allow_feed_hosts: bool = False) -> list[str]:
    """Links whose address mentions rss/feed/atom, assets excluded.

    Same-host only by default — on a front page any other host is somebody
    else's feed. ``allow_feed_hosts`` additionally keeps the feed-hosting
    services, which is what a publisher's own "our feeds" page points at.
    """
    host = host_of(base_url)
    links: list[str] = []
    seen: set[str] = set()
    for href in _FEEDISH_HREF_RE.findall(html):
        url = urljoin(base_url, href)
        if url in seen:
            continue
        url_host = host_of(url)
        same_site = url_host == host
        hosted = allow_feed_hosts and any(part in url_host for part in _FEED_HOSTS)
        if not (same_site or hosted):
            continue
        if url.lower().split("?")[0].endswith(_ASSET_SUFFIXES):
            continue
        seen.add(url)
        links.append(url)
    return links


def feed_urls_for_site(site_url: str, *, client: Any | None = None) -> list[str]:
    """Feed addresses advertised by a page, then the conventional paths.

    An empty list means the site itself could not be read. Guessing feed paths at
    a host that already refused its front page is what made a scan take minutes:
    a WAF answers 403 to every guess, each one twice (the plain and the browser
    User-Agent of :func:`fetch_url`).
    """
    try:
        body = fetch_url(site_url, timeout=PROBE_TIMEOUT, max_retries=1, client=client)
    except CollectorError as exc:
        logger.debug("discovery: cannot read %s: %s", site_url, exc)
        return []

    html = body.decode("utf-8", errors="replace")
    found: list[str] = _autodiscovered(html, site_url)
    if not found:
        # No <link rel="alternate">. Many outlets instead keep a human page that
        # lists their feeds ("/rss-feeds", "/rss"); follow a couple of those one
        # level down rather than giving up on the site.
        for page_url in _feedish_links(html, site_url)[:2]:
            try:
                page = fetch_url(page_url, timeout=PROBE_TIMEOUT, max_retries=1, client=client)
            except CollectorError:
                continue
            page_html = page.decode("utf-8", errors="replace")
            found.extend(_autodiscovered(page_html, page_url))
            harvested = [
                url
                for url in _feedish_links(page_html, page_url, allow_feed_hosts=True)
                if url != page_url
            ]
            found.extend(harvested[:MAX_LINKS_FROM_FEED_PAGE])
    for path in COMMON_FEED_PATHS:
        found.append(urljoin(site_url, path))
    seen: set[str] = set()
    ordered: list[str] = []
    for url in found:
        if url not in seen:
            seen.add(url)
            ordered.append(url)
    return ordered


def evaluate_feed(feed_bytes: bytes, *, feed_url: str) -> dict[str, Any] | None:
    """Parse a feed and score it with the operator's relevance gate.

    Returns None when the bytes are not a usable feed. The score is the share of
    recent *headlines* that carry a messaging signal and no obvious off-topic
    subject, judged with the operator's own terms (``data/ai_rules.json``).

    Headlines, not full text, on purpose. The keyword gate is a permissive
    pre-filter — it only has to decide whether one article is worth an LLM call,
    and it looks at the body too. Scoring a whole outlet that way admits any
    vendor blog that mentions SMS in passing: an ecommerce email-marketing
    platform scored 90% on full text and 70% after the off-topic terms, while its
    headlines are about Shopify themes. What a publisher puts in its headlines is
    what it actually writes about.
    """
    from .models import Article
    from .processors.relevance import has_messaging_signal, is_obviously_off_topic

    try:
        items = parse_feed(feed_bytes, source_id="candidate")
    except Exception:  # noqa: BLE001 — feedparser is lenient but not infallible
        return None
    if len(items) < MIN_ITEMS:
        return None

    hits: list[str] = []
    for item in items:
        headline = Article(
            url=item.url,
            source_id="candidate",
            title=item.title,
            body="",
        )
        if has_messaging_signal(headline) and not is_obviously_off_topic(headline):
            hits.append(item.title or item.url)

    text = " ".join(item.title for item in items[:20])
    return {
        "feed_url": feed_url,
        "items": len(items),
        "hits": len(hits),
        "hit_rate": round(len(hits) / len(items), 3),
        "sample_titles": hits[:MAX_SAMPLE_TITLES],
        "language": "ru" if _CYRILLIC_RE.search(text) else "en",
    }


def _score_titles(titles: list[str]) -> dict[str, Any] | None:
    """Share of headlines that pass the operator's gate, or None when too few."""
    from .models import Article
    from .processors.relevance import has_messaging_signal, is_obviously_off_topic

    titles = [title for title in titles if title.strip()]
    if len(titles) < MIN_ITEMS:
        return None
    hits = [
        title
        for title in titles
        if has_messaging_signal(headline := Article(url="", source_id="candidate", title=title))
        and not is_obviously_off_topic(headline)
    ]
    text = " ".join(titles[:20])
    return {
        "items": len(titles),
        "hits": len(hits),
        "hit_rate": round(len(hits) / len(titles), 3),
        "sample_titles": hits[:MAX_SAMPLE_TITLES],
        "language": "ru" if _CYRILLIC_RE.search(text) else "en",
    }


def evaluate_sitemap(site_url: str, *, client: Any | None = None) -> dict[str, Any] | None:
    """Score a site's news sitemap, for outlets that publish no feed.

    Only dated entries that carry their own headline count: a plain archive
    listing says nothing about what the outlet writes now, and fetching hundreds
    of pages to find out is not something a scan should do.
    """
    from .collectors.sitemap import discover_sitemaps, parse_sitemap

    for sitemap_url in discover_sitemaps(site_url, client=client, timeout=PROBE_TIMEOUT)[
        :MAX_SITEMAPS_PROBED_PER_SITE
    ]:
        try:
            body = fetch_url(sitemap_url, timeout=PROBE_TIMEOUT, max_retries=1, client=client)
            document = parse_sitemap(body)
        except CollectorError:
            continue
        titles = [entry.title for entry in document.entries if entry.title and entry.published_at]
        verdict = _score_titles(titles)
        if verdict is not None:
            verdict["feed_url"] = sitemap_url
            return verdict
    return None


def scan(
    *,
    db: Any,
    data_dir: Path | None = None,
    max_sites: int = 12,
    min_hit_rate: float = MIN_HIT_RATE,
    use_search: bool = True,
    look_for: str = LOOK_FOR_BOTH,
    client: Any | None = None,
) -> dict[str, Any]:
    """One discovery pass. Returns a summary and persists new candidates.

    Topical search results come first: they answer "who writes about this
    anywhere in the world", while the links of collected articles only map the
    neighbourhood of what is already being read. With ``use_search=False`` the
    scan makes no search requests at all.

    ``look_for`` picks what counts as a source: ``"rss"`` only feeds,
    ``"sitemap"`` only sitemaps (of outlets that publish no feed), ``"both"``
    the feed first and the sitemap as a fallback.
    """
    look_for = (look_for or LOOK_FOR_BOTH).strip().lower()
    if look_for not in LOOK_FOR_CHOICES:
        raise ValueError(f"look_for must be one of {', '.join(LOOK_FOR_CHOICES)}")
    state = load_state(data_dir)
    dismissed = set(state["dismissed"])
    # Dismiss is a verdict about the outlet, not about one URL of it: a site
    # usually serves several feeds (/feed/, /blog/feed/, per-category ones), and
    # proposing the next one after the operator said no is just noise.
    dismissed_hosts = {host_of(url) for url in dismissed}
    existing = {str(item.get("feed_url")) for item in state["candidates"]}
    known = known_hosts()

    seeds: list[tuple[str, str]] = []
    seen_hosts: set[str] = set()
    origins: dict[str, str] = {}
    searched = 0
    if use_search:
        queries = load_queries(data_dir)
        searched = len(queries)
        print(f"discovery: searching {searched} topic(s) worldwide")
        for host, url in search_publisher_hosts(queries, client=client):
            if host not in seen_hosts:
                seen_hosts.add(host)
                seeds.append((host, url))
                origins[host] = f"news search for the configured topics ({host})"
    for host, url in seed_hosts(db, limit=max_sites):
        if host not in seen_hosts:
            seen_hosts.add(host)
            seeds.append((host, url))

    checked = 0
    added: list[Candidate] = []
    for host, sample_url in seeds[:max_sites]:
        if host in known or host in dismissed_hosts:
            continue
        scheme = urlparse(sample_url).scheme or "https"
        site_url = f"{scheme}://{host}/"
        checked += 1
        print(f"discovery: probing {host}")
        failed_probes = 0
        proposed = False
        feed_urls = (
            feed_urls_for_site(site_url, client=client)
            if look_for in (LOOK_FOR_RSS, LOOK_FOR_BOTH)
            else []
        )
        for feed_url in feed_urls:
            if failed_probes >= MAX_FAILED_PROBES_PER_SITE:
                break
            if feed_url in dismissed or feed_url in existing:
                continue
            try:
                body = fetch_url(feed_url, timeout=PROBE_TIMEOUT, max_retries=1, client=client)
            except CollectorError:
                failed_probes += 1
                continue
            verdict = evaluate_feed(body, feed_url=feed_url)
            if verdict is None or verdict["hit_rate"] < min_hit_rate:
                continue
            candidate = Candidate(
                id=candidate_id(feed_url),
                feed_url=feed_url,
                site_url=site_url,
                title=host,
                language=str(verdict["language"]),
                origin=origins.get(host, f"linked from collected articles ({host})"),
                items=int(verdict["items"]),
                hits=int(verdict["hits"]),
                hit_rate=float(verdict["hit_rate"]),
                sample_titles=list(verdict["sample_titles"]),
                discovered_at=_now(),
            )
            added.append(candidate)
            existing.add(feed_url)
            print(
                f"discovery: candidate {candidate.id} — {candidate.hits}/{candidate.items} "
                f"items on topic ({candidate.hit_rate:.0%})"
            )
            proposed = True
            break  # one feed per host is enough to propose

        if not proposed and look_for in (LOOK_FOR_SITEMAP, LOOK_FOR_BOTH):
            # No usable feed — including the case where every guess was refused,
            # which is exactly when an outlet is worth checking for a sitemap.
            # collect can read a dated news sitemap as a source of its own (D-026).
            verdict = evaluate_sitemap(site_url, client=client)
            sitemap_url = str(verdict["feed_url"]) if verdict else ""
            if (
                verdict
                and verdict["hit_rate"] >= min_hit_rate
                and sitemap_url not in dismissed
                and sitemap_url not in existing
            ):
                candidate = Candidate(
                    id=candidate_id(sitemap_url),
                    feed_url=sitemap_url,
                    site_url=site_url,
                    title=host,
                    language=str(verdict["language"]),
                    type="sitemap",
                    origin=origins.get(host, f"sitemap of {host} (no feed published)"),
                    items=int(verdict["items"]),
                    hits=int(verdict["hits"]),
                    hit_rate=float(verdict["hit_rate"]),
                    sample_titles=list(verdict["sample_titles"]),
                    discovered_at=_now(),
                )
                added.append(candidate)
                existing.add(sitemap_url)
                print(
                    f"discovery: candidate {candidate.id} (sitemap) — "
                    f"{candidate.hits}/{candidate.items} on topic ({candidate.hit_rate:.0%})"
                )

    with _LOCK:
        state = load_state(data_dir)
        known_urls = {str(item.get("feed_url")) for item in state["candidates"]}
        state["candidates"].extend(
            candidate.to_dict() for candidate in added if candidate.feed_url not in known_urls
        )
        state["scanned_at"] = _now()
        save_state(state, data_dir)

    print(
        f"Discovery: searched {searched} topic(s), probed {checked} site(s), "
        f"{len(added)} new candidate(s)."
    )
    return {
        "searched_topics": searched,
        "checked_sites": checked,
        "look_for": look_for,
        "new_candidates": len(added),
        "candidates": [candidate.to_dict() for candidate in added],
        "scanned_at": state["scanned_at"],
    }


def list_candidates(data_dir: Path | None = None) -> dict[str, Any]:
    """Open proposals, best first, plus what the operator already dismissed."""
    state = load_state(data_dir)
    known = known_hosts()
    open_rows = [
        item
        for item in state["candidates"]
        if item.get("status", "new") == "new" and host_of(str(item.get("feed_url"))) not in known
    ]
    open_rows.sort(key=lambda item: (-float(item.get("hit_rate") or 0), str(item.get("id"))))
    return {
        "candidates": open_rows,
        "count": len(open_rows),
        "dismissed": len(state["dismissed"]),
        "scanned_at": state["scanned_at"],
        "path": str(candidates_path(data_dir)),
    }


def _pop_candidate(state: dict[str, Any], candidate_id_or_url: str) -> dict[str, Any] | None:
    for index, item in enumerate(state["candidates"]):
        if candidate_id_or_url in (item.get("id"), item.get("feed_url")):
            return state["candidates"].pop(index)
    return None


def accept_candidate(
    candidate_id_or_url: str,
    *,
    data_dir: Path | None = None,
    relevance_gate: str = "strict",
) -> dict[str, Any]:
    """Turn a proposal into a real source (M9d custom source) and drop it."""
    from .custom_sources import add_source

    with _LOCK:
        state = load_state(data_dir)
        candidate = _pop_candidate(state, candidate_id_or_url)
        if candidate is None:
            raise KeyError(f"unknown candidate {candidate_id_or_url!r}")
        source = add_source(
            str(candidate["feed_url"]),
            source_id=str(candidate.get("id") or "") or None,
            language=str(candidate.get("language") or "en"),
            relevance_gate=relevance_gate,
            source_type=str(candidate.get("type") or "rss"),
            data_dir=data_dir,
        )
        save_state(state, data_dir)
    return {"accepted": candidate, "source": source}


def dismiss_candidate(candidate_id_or_url: str, *, data_dir: Path | None = None) -> dict[str, Any]:
    """Remove a proposal and remember its site, so later scans stay quiet about it."""
    with _LOCK:
        state = load_state(data_dir)
        candidate = _pop_candidate(state, candidate_id_or_url)
        if candidate is None:
            raise KeyError(f"unknown candidate {candidate_id_or_url!r}")
        feed_url = str(candidate.get("feed_url"))
        if feed_url not in state["dismissed"]:
            state["dismissed"].append(feed_url)
        save_state(state, data_dir)
    return {"dismissed": candidate}
