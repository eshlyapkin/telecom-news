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

No search engine and no API key: everything comes from feeds the project already
reads plus standard feed autodiscovery, which keeps the scan inside the domain
the operator curates and makes it safe to run on a schedule.
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
from urllib.parse import urljoin, urlparse

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

    found: list[str] = []
    parser = _FeedLinkParser()
    try:
        parser.feed(body.decode("utf-8", errors="replace"))
    except Exception:  # noqa: BLE001 — malformed markup must not stop a scan
        logger.debug("discovery: unparsable HTML at %s", site_url)
    found.extend(urljoin(site_url, href) for href in parser.feeds)
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


def scan(
    *,
    db: Any,
    data_dir: Path | None = None,
    max_sites: int = 12,
    min_hit_rate: float = MIN_HIT_RATE,
    client: Any | None = None,
) -> dict[str, Any]:
    """One discovery pass. Returns a summary and persists new candidates."""
    state = load_state(data_dir)
    dismissed = set(state["dismissed"])
    # Dismiss is a verdict about the outlet, not about one URL of it: a site
    # usually serves several feeds (/feed/, /blog/feed/, per-category ones), and
    # proposing the next one after the operator said no is just noise.
    dismissed_hosts = {host_of(url) for url in dismissed}
    existing = {str(item.get("feed_url")) for item in state["candidates"]}
    known = known_hosts()

    checked = 0
    added: list[Candidate] = []
    for host, sample_url in seed_hosts(db, limit=max_sites):
        if host in known or host in dismissed_hosts:
            continue
        scheme = urlparse(sample_url).scheme or "https"
        site_url = f"{scheme}://{host}/"
        checked += 1
        print(f"discovery: probing {host}")
        failed_probes = 0
        for feed_url in feed_urls_for_site(site_url, client=client):
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
                origin=f"linked from collected articles ({host})",
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
            break  # one feed per host is enough to propose

    with _LOCK:
        state = load_state(data_dir)
        known_urls = {str(item.get("feed_url")) for item in state["candidates"]}
        state["candidates"].extend(
            candidate.to_dict() for candidate in added if candidate.feed_url not in known_urls
        )
        state["scanned_at"] = _now()
        save_state(state, data_dir)

    print(f"Discovery: probed {checked} site(s), {len(added)} new candidate(s).")
    return {
        "checked_sites": checked,
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
