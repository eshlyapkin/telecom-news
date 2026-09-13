"""Source discovery: propose feeds, score them with the operator's own AI rules.

No real network — every fetch goes through ``httpx.MockTransport``.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from telecom_news import config as config_mod
from telecom_news import source_discovery as discovery
from telecom_news.models import Article
from telecom_news.storage.database import Database

MESSAGING_FEED = """<?xml version="1.0"?><rss version="2.0"><channel>
  <item><title>A2P SMS traffic grows 12% in Q3</title><link>https://good.example/1</link>
        <description>Operators report higher SMS volumes.</description></item>
  <item><title>New SMPP gateway for enterprise messaging</title><link>https://good.example/2</link>
        <description>SMSC interconnect upgrade.</description></item>
  <item><title>OTP fraud crackdown</title><link>https://good.example/3</link>
        <description>One-time password interception ring shut down.</description></item>
  <item><title>RCS business messaging launch</title><link>https://good.example/4</link>
        <description>Carrier enables RCS.</description></item>
</channel></rss>"""

OFF_TOPIC_FEED = """<?xml version="1.0"?><rss version="2.0"><channel>
  <item><title>Quarterly cloud earnings</title><link>https://noise.example/1</link>
        <description>Data-center revenue.</description></item>
  <item><title>New laptop lineup</title><link>https://noise.example/2</link>
        <description>Hardware refresh.</description></item>
  <item><title>Fibre rollout continues</title><link>https://noise.example/3</link>
        <description>Trenching works.</description></item>
  <item><title>Satellite launch delayed</title><link>https://noise.example/4</link>
        <description>Weather.</description></item>
</channel></rss>"""

HOMEPAGE = (
    '<html><head><link rel="alternate" type="application/rss+xml" href="/custom/feed.xml">'
    "</head><body>hi</body></html>"
)


@pytest.fixture(autouse=True)
def _restore_registry():
    yield
    config_mod.reset_sources_to_baseline()


def _seeded_db(tmp_path: Path, links: dict[int, list[str]]) -> Database:
    db = Database(tmp_path / "news.db")
    for index, urls in links.items():
        body = " ".join(f'<a href="{url}">x</a>' for url in urls)
        db.upsert_by_hash(
            Article(
                url=f"https://example.com/{index}",
                content_hash=f"h{index}",
                source_id="sinch-blog",
                title=f"Story {index}",
                body=body,
            )
        )
    return db


# --- host normalization -----------------------------------------------------


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://www.techcrunch.com/x", "techcrunch.com"),
        ("https://app2.simpletexting.com/login", "simpletexting.com"),
        ("https://support.textmagic.com/a", "textmagic.com"),
        ("https://news.example.co.uk/a", "news.example.co.uk"),
        ("https://app.io/x", "app.io"),  # never strip down to a bare TLD
    ],
)
def test_host_of_strips_service_subdomains(url: str, expected: str) -> None:
    assert discovery.host_of(url) == expected


# --- seeds ------------------------------------------------------------------


def test_seed_hosts_rank_by_distinct_articles(tmp_path: Path) -> None:
    """Boilerplate links sit in one article; a real neighbour is cited by several."""
    db = _seeded_db(
        tmp_path,
        {
            1: ["https://widget.example/a", "https://widget.example/b", "https://good.example/1"],
            2: ["https://good.example/2"],
            3: ["https://good.example/3"],
        },
    )
    hosts = [host for host, _url in discovery.seed_hosts(db, limit=10)]
    assert hosts[0] == "good.example"
    assert "widget.example" not in hosts  # only one article links it


def test_seed_hosts_skip_registered_and_ignored_hosts(tmp_path: Path) -> None:
    db = _seeded_db(
        tmp_path,
        {
            1: ["https://sinch.com/blog/x", "https://calendly.com/a", "https://good.example/1"],
            2: ["https://sinch.com/blog/y", "https://calendly.com/b", "https://good.example/2"],
        },
    )
    hosts = [host for host, _url in discovery.seed_hosts(db, limit=10)]
    assert hosts == ["good.example"]  # sinch is a source, calendly is a widget


# --- feed lookup and scoring ------------------------------------------------


def test_feed_urls_prefer_autodiscovery_then_conventions() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=HOMEPAGE)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    urls = discovery.feed_urls_for_site("https://good.example/", client=client)
    assert urls[0] == "https://good.example/custom/feed.xml"
    assert "https://good.example/feed/" in urls


def test_feed_page_is_followed_one_level_down() -> None:
    """Outlets often keep their feeds on an "our feeds" page, hosted elsewhere."""
    homepage = (
        '<html><body><a href="/media/rss.svg">icon</a>'
        '<a href="/rss-feeds">Our RSS feeds</a></body></html>'
    )
    feeds_page = (
        '<html><body><a href="http://feeds.feedburner.com/GoodMessaging">messaging</a>'
        '<a href="https://elsewhere.example/feed/">someone else</a></body></html>'
    )

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url == "https://good.example/":
            return httpx.Response(200, text=homepage)
        if url == "https://good.example/rss-feeds":
            return httpx.Response(200, text=feeds_page)
        return httpx.Response(404, text="nope")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    urls = discovery.feed_urls_for_site("https://good.example/", client=client)

    assert "http://feeds.feedburner.com/GoodMessaging" in urls  # feed host allowed
    assert "https://elsewhere.example/feed/" not in urls  # unrelated host is not
    assert not any(url.endswith(".svg") for url in urls)  # the icon is not a feed
    assert "https://good.example/feed/" in urls  # conventional paths still tried


def test_front_page_links_stay_on_the_same_host() -> None:
    """Another publisher's feed linked from a front page is not this site's feed."""
    homepage = '<html><body><a href="https://other.example/feed/">partner</a></body></html>'

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=homepage)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    urls = discovery.feed_urls_for_site("https://good.example/", client=client)
    assert not any("other.example" in url for url in urls)


def test_an_existing_candidate_survives_a_scan_that_finds_nothing(tmp_path: Path) -> None:
    discovery.save_state(
        {
            "candidates": [
                {"id": "keep-me", "feed_url": "https://keep.example/feed/", "status": "new"}
            ],
            "dismissed": [],
            "scanned_at": "earlier",
        },
        tmp_path,
    )
    db = _seeded_db(tmp_path, {})
    discovery.scan(db=db, data_dir=tmp_path, max_sites=2, use_search=False)
    assert [c["id"] for c in discovery.list_candidates(tmp_path)["candidates"]] == ["keep-me"]


def test_evaluate_feed_scores_with_the_messaging_gate() -> None:
    verdict = discovery.evaluate_feed(
        MESSAGING_FEED.encode("utf-8"), feed_url="https://good.example/feed/"
    )
    assert verdict is not None
    assert verdict["items"] == 4 and verdict["hits"] == 4
    assert verdict["hit_rate"] == 1.0
    assert verdict["language"] == "en"
    assert any("A2P" in title for title in verdict["sample_titles"])


def test_evaluate_feed_rejects_off_topic_and_unusable_feeds() -> None:
    off_topic = discovery.evaluate_feed(
        OFF_TOPIC_FEED.encode("utf-8"), feed_url="https://noise.example/feed/"
    )
    assert off_topic is not None and off_topic["hit_rate"] == 0.0
    assert discovery.evaluate_feed(b"<html>not a feed</html>", feed_url="x") is None


# --- worldwide topical search ----------------------------------------------

SEARCH_FEED = """<?xml version="1.0"?><rss version="2.0"><channel>
  <item><title>A2P SMS grows - Telecompaper</title>
        <link>https://news.google.com/rss/articles/CBMiOPAQUE</link>
        <source url="https://www.telecompaper.com">Telecompaper</source></item>
  <item><title>SMS hub deal - Capacity</title>
        <link>https://news.google.com/rss/articles/CBMiANOTHER</link>
        <source url="https://www.capacityglobal.com">Capacity</source></item>
  <item><title>Vendor news - Telecompaper</title>
        <link>https://news.google.com/rss/articles/CBMiTHIRD</link>
        <source url="https://www.telecompaper.com">Telecompaper</source></item>
  <item><title>Sinch update - Sinch</title>
        <link>https://news.google.com/rss/articles/CBMiFOURTH</link>
        <source url="https://sinch.com">Sinch</source></item>
</channel></rss>"""


def test_search_url_follows_the_script_of_the_query() -> None:
    assert "hl=en-US" in discovery.search_url('"A2P SMS"')
    assert "ceid=RU:ru" in discovery.search_url("смс мошенничество")
    assert "q=%22A2P+SMS%22" in discovery.search_url('"A2P SMS"')


def test_search_returns_publisher_hosts_not_article_links() -> None:
    """Search links are consent-walled redirects; only the publisher is usable."""

    def handler(request: httpx.Request) -> httpx.Response:
        assert "news.google.com" in str(request.url)
        return httpx.Response(200, content=SEARCH_FEED.encode("utf-8"))

    client = httpx.Client(transport=httpx.MockTransport(handler))
    hosts = discovery.search_publisher_hosts(['"A2P SMS"'], client=client)

    assert [host for host, _url in hosts] == ["telecompaper.com", "capacityglobal.com"]
    assert all(url.startswith("https://") for _host, url in hosts)
    assert all("news.google.com" not in url for _host, url in hosts)
    # sinch.com is already a source, so it is not proposed again
    assert "sinch.com" not in {host for host, _ in hosts}


def test_topics_are_generated_from_the_ai_rules(tmp_path: Path) -> None:
    """One source of truth: discovery hunts for what the rules say is relevant."""
    from telecom_news.ai_rules import invalidate_cache, save_rules

    save_rules({"messaging_terms": ["carrier billing fraud"]}, data_dir=tmp_path)
    invalidate_cache()

    queries = discovery.queries_from_rules(tmp_path)
    assert any("carrier billing fraud" in query for query in queries)  # the added rule
    assert any("smishing" in query for query in queries)  # and the built-in terms
    assert discovery.load_queries(tmp_path) == queries
    assert discovery.queries_are_custom(tmp_path) is False
    invalidate_cache()


def test_terms_too_generic_to_search_are_left_out(tmp_path: Path) -> None:
    """A lone "sms" returns noise; a phrase returns news."""
    from telecom_news.ai_rules import invalidate_cache, save_rules

    save_rules({"messaging_terms": ["sms", "ss7", "smishing", "sms firewall"]}, data_dir=tmp_path)
    invalidate_cache()
    joined = " ".join(discovery.queries_from_rules(tmp_path))
    assert '"sms firewall"' in joined and '"smishing"' in joined
    assert '"sms"' not in joined and '"ss7"' not in joined
    invalidate_cache()


def test_a_custom_list_overrides_the_rules_and_can_be_cleared(tmp_path: Path) -> None:
    saved = discovery.save_queries(["  SMPP routing ", "SMPP routing", "смс"], tmp_path)
    assert saved == ["SMPP routing", "смс"]
    assert discovery.load_queries(tmp_path) == ["SMPP routing", "смс"]
    assert discovery.queries_are_custom(tmp_path) is True

    # an empty list goes back to the rules rather than disabling search silently
    assert discovery.save_queries([], tmp_path) == discovery.queries_from_rules(tmp_path)
    assert not discovery.queries_path(tmp_path).exists()


def test_broken_queries_file_falls_back_to_the_rules(tmp_path: Path) -> None:
    discovery.queries_path(tmp_path).write_text("{oops", encoding="utf-8")
    assert discovery.load_queries(tmp_path) == discovery.queries_from_rules(tmp_path)


def test_a_scan_works_through_the_topics_in_batches(tmp_path: Path) -> None:
    """Every rule gets its turn instead of an arbitrary dozen being searched forever."""
    queries = [f"q{index}" for index in range(10)]
    assert discovery.queries_for_scan(queries, offset=0, per_scan=4) == ["q0", "q1", "q2", "q3"]
    assert discovery.queries_for_scan(queries, offset=4, per_scan=4) == ["q4", "q5", "q6", "q7"]
    # and wraps around rather than stopping at the end
    assert discovery.queries_for_scan(queries, offset=8, per_scan=4) == ["q8", "q9", "q0", "q1"]
    assert discovery.queries_for_scan([], offset=0) == []


def test_the_offset_advances_between_scans(tmp_path: Path) -> None:
    searched: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "news.google.com" in url:
            searched.append(url)
            return httpx.Response(200, content=SEARCH_FEED.encode("utf-8"))
        return httpx.Response(404)

    discovery.save_queries([f"topic-{index}" for index in range(8)], tmp_path)
    db = _seeded_db(tmp_path, {})
    client = httpx.Client(transport=httpx.MockTransport(handler))

    first = discovery.scan(db=db, data_dir=tmp_path, max_sites=0, client=client)
    second = discovery.scan(db=db, data_dir=tmp_path, max_sites=0, client=client)
    assert first["searched_topics"] == second["searched_topics"] == discovery.QUERIES_PER_SCAN
    assert "topic-0" in searched[0] and "topic-4" in searched[discovery.QUERIES_PER_SCAN]


def test_scan_probes_publishers_found_by_search(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "news.google.com" in url:
            return httpx.Response(200, content=SEARCH_FEED.encode("utf-8"))
        if url == "https://telecompaper.com/feed/":
            return httpx.Response(200, content=MESSAGING_FEED.encode("utf-8"))
        if url.rstrip("/").endswith(("telecompaper.com", "capacityglobal.com")):
            return httpx.Response(200, text="<html><head></head><body>site</body></html>")
        return httpx.Response(404, text="nope")

    db = _seeded_db(tmp_path, {1: ["https://noise.example/1"]})
    result = discovery.scan(
        db=db,
        data_dir=tmp_path,
        max_sites=5,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    assert result["searched_topics"] > 0
    (candidate,) = discovery.list_candidates(tmp_path)["candidates"]
    assert candidate["feed_url"] == "https://telecompaper.com/feed/"
    assert "news search" in candidate["origin"]


def test_scan_without_search_makes_no_search_request(tmp_path: Path) -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(404, text="nope")

    db = _seeded_db(tmp_path, {1: ["https://good.example/1"], 2: ["https://good.example/2"]})
    result = discovery.scan(
        db=db,
        data_dir=tmp_path,
        max_sites=5,
        use_search=False,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    assert result["searched_topics"] == 0
    assert not any("news.google.com" in url for url in seen)


# --- a full pass ------------------------------------------------------------


def _scan_client() -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith("/feed/") and "good.example" in url:
            return httpx.Response(200, content=MESSAGING_FEED.encode("utf-8"))
        if url.endswith("/feed/") and "noise.example" in url:
            return httpx.Response(200, content=OFF_TOPIC_FEED.encode("utf-8"))
        if url.rstrip("/").endswith("example"):
            return httpx.Response(200, text="<html><head></head><body>site</body></html>")
        return httpx.Response(404, text="nope")

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_scan_proposes_only_on_topic_feeds(tmp_path: Path) -> None:
    db = _seeded_db(
        tmp_path,
        {
            1: ["https://good.example/1", "https://noise.example/1"],
            2: ["https://good.example/2", "https://noise.example/2"],
        },
    )
    result = discovery.scan(
        db=db, data_dir=tmp_path, max_sites=5, use_search=False, client=_scan_client()
    )
    assert result["new_candidates"] == 1

    listing = discovery.list_candidates(tmp_path)
    (candidate,) = listing["candidates"]
    assert candidate["feed_url"] == "https://good.example/feed/"
    assert candidate["hit_rate"] == 1.0
    assert "good.example" in candidate["origin"]
    stored = json.loads((tmp_path / "source_candidates.json").read_text(encoding="utf-8"))
    assert len(stored["candidates"]) == 1


def test_scan_does_not_touch_the_source_registry(tmp_path: Path) -> None:
    """A proposal is a proposal: collect must not start reading it by itself."""
    db = _seeded_db(tmp_path, {1: ["https://good.example/1"], 2: ["https://good.example/2"]})
    before = set(config_mod.SOURCES)
    discovery.scan(db=db, data_dir=tmp_path, max_sites=5, use_search=False, client=_scan_client())
    assert set(config_mod.SOURCES) == before


def test_accept_turns_a_candidate_into_a_source(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("TELECOM_NEWS_DISABLED_SOURCES", raising=False)
    db = _seeded_db(tmp_path, {1: ["https://good.example/1"], 2: ["https://good.example/2"]})
    discovery.scan(db=db, data_dir=tmp_path, max_sites=5, use_search=False, client=_scan_client())
    (candidate,) = discovery.list_candidates(tmp_path)["candidates"]

    result = discovery.accept_candidate(candidate["id"], data_dir=tmp_path)
    assert result["source"]["url"] == "https://good.example/feed/"
    assert config_mod.SOURCES[result["source"]["id"]].enabled is True
    assert discovery.list_candidates(tmp_path)["count"] == 0


def test_dismissed_candidates_are_not_proposed_again(tmp_path: Path) -> None:
    db = _seeded_db(tmp_path, {1: ["https://good.example/1"], 2: ["https://good.example/2"]})
    discovery.scan(db=db, data_dir=tmp_path, max_sites=5, use_search=False, client=_scan_client())
    (candidate,) = discovery.list_candidates(tmp_path)["candidates"]

    discovery.dismiss_candidate(candidate["id"], data_dir=tmp_path)
    assert discovery.list_candidates(tmp_path)["count"] == 0

    again = discovery.scan(
        db=db, data_dir=tmp_path, max_sites=5, use_search=False, client=_scan_client()
    )
    assert again["new_candidates"] == 0
    assert discovery.list_candidates(tmp_path)["count"] == 0


def test_unknown_candidate_raises(tmp_path: Path) -> None:
    with pytest.raises(KeyError):
        discovery.accept_candidate("no-such", data_dir=tmp_path)
    with pytest.raises(KeyError):
        discovery.dismiss_candidate("no-such", data_dir=tmp_path)


# --- control panel ----------------------------------------------------------

pytest.importorskip("fastapi")


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from fastapi.testclient import TestClient

    from telecom_news.api.app import create_app
    from telecom_news.pipeline_run import reset_for_tests
    from telecom_news.projects import ProjectRegistry

    monkeypatch.setenv("TELECOM_NEWS_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("TELECOM_NEWS_DISABLED_SOURCES", raising=False)
    reset_for_tests()
    db = _seeded_db(tmp_path, {1: ["https://good.example/1"], 2: ["https://good.example/2"]})
    discovery.scan(db=db, data_dir=tmp_path, max_sites=5, use_search=False, client=_scan_client())
    registry = ProjectRegistry(tmp_path / "projects" / "registry.json")
    registry.ensure_default()
    with TestClient(create_app(registry=registry)) as test_client:
        yield test_client
    reset_for_tests()


def test_api_lists_candidates(client) -> None:
    body = client.get("/api/source-candidates").json()
    assert body["count"] == 1
    assert body["candidates"][0]["feed_url"] == "https://good.example/feed/"


def test_api_accept_creates_a_source(client) -> None:
    candidate_id = client.get("/api/source-candidates").json()["candidates"][0]["id"]
    accepted = client.post(f"/api/source-candidates/{candidate_id}/accept", json={})
    assert accepted.status_code == 201
    assert accepted.json()["source"]["url"] == "https://good.example/feed/"

    ids = {row["id"] for row in client.get("/api/sources").json()["sources"]}
    assert candidate_id in ids
    assert client.get("/api/source-candidates").json()["count"] == 0


def test_api_dismiss_removes_the_candidate(client) -> None:
    candidate_id = client.get("/api/source-candidates").json()["candidates"][0]["id"]
    assert client.post(f"/api/source-candidates/{candidate_id}/dismiss").status_code == 200
    body = client.get("/api/source-candidates").json()
    assert body["count"] == 0 and body["dismissed"] == 1


def test_api_unknown_candidate_is_404(client) -> None:
    assert client.post("/api/source-candidates/nope/dismiss").status_code == 404
    assert client.post("/api/source-candidates/nope/accept", json={}).status_code == 404


def test_api_scan_runs_in_the_background(client, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def fake_runner(**kwargs) -> int:  # noqa: ANN003
        calls.append(str(kwargs.get("stage")))
        return 0

    monkeypatch.setattr("telecom_news.pipeline_run._default_runner", fake_runner)
    started = client.post("/api/source-candidates/scan", json={"max_sites": 3})
    assert started.status_code == 200
    assert started.json()["stage"] == "discover"
    for _ in range(40):
        if client.get("/api/run/status").json()["status"] != "running":
            break
    assert calls == ["discover"]


def test_gui_has_a_discovery_tab(client) -> None:
    html = client.get("/").text
    assert 'data-tab="discovery"' in html
    assert 'id="panel-discovery"' in html
    js = client.get("/static/app.js").text
    assert "refreshCandidates" in js and "candidate-add" in js


def test_api_edits_the_search_topics(client, tmp_path: Path) -> None:
    body = client.get("/api/discovery-queries").json()
    assert body["source"] == "ai-rules"
    assert body["queries"] == discovery.queries_from_rules(tmp_path)
    assert len(body["next_batch"]) == body["per_scan"]

    updated = client.put("/api/discovery-queries", json={"queries": ["SMPP routing", "  "]})
    assert updated.json()["queries"] == ["SMPP routing"]
    assert updated.json()["source"] == "custom"

    restored = client.put("/api/discovery-queries", json={"queries": []})
    assert restored.json()["source"] == "ai-rules"
    assert restored.json()["queries"] == discovery.queries_from_rules(tmp_path)


def test_gui_exposes_the_search_topics(client) -> None:
    html = client.get("/").text
    assert "Search topics" in html
    assert 'id="discover-queries"' in html
    assert "refreshQueries" in client.get("/static/app.js").text


# --- outlets with no feed ---------------------------------------------------

SITEMAP_XML = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"
        xmlns:news="http://www.google.com/schemas/sitemap-news/0.9">
  <url><loc>https://nofeed.example/1</loc><news:news>
    <news:publication_date>2026-09-12T09:00:00Z</news:publication_date>
    <news:title>A2P SMS volumes climb</news:title></news:news></url>
  <url><loc>https://nofeed.example/2</loc><news:news>
    <news:publication_date>2026-09-12T08:00:00Z</news:publication_date>
    <news:title>Business messaging deal signed</news:title></news:news></url>
  <url><loc>https://nofeed.example/3</loc><news:news>
    <news:publication_date>2026-09-12T07:00:00Z</news:publication_date>
    <news:title>Smishing ring dismantled</news:title></news:news></url>
</urlset>"""


def _nofeed_client(seen: list[str] | None = None) -> httpx.Client:
    """A site with no feed anywhere, but a proper news sitemap."""

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if seen is not None:
            seen.append(url)
        if url == "https://nofeed.example/":
            return httpx.Response(200, text="<html><head></head><body>site</body></html>")
        if url.endswith("/robots.txt"):
            return httpx.Response(200, text="Sitemap: https://nofeed.example/news-sitemap.xml\n")
        if url.endswith("/news-sitemap.xml"):
            return httpx.Response(200, text=SITEMAP_XML)
        return httpx.Response(404, text="no feed here")

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_evaluate_sitemap_scores_dated_headlines() -> None:
    verdict = discovery.evaluate_sitemap("https://nofeed.example/", client=_nofeed_client())
    assert verdict is not None
    assert verdict["feed_url"] == "https://nofeed.example/news-sitemap.xml"
    assert verdict["items"] == 3 and verdict["hits"] == 3


def test_scan_proposes_a_sitemap_when_there_is_no_feed(tmp_path: Path) -> None:
    db = _seeded_db(tmp_path, {1: ["https://nofeed.example/1"], 2: ["https://nofeed.example/2"]})
    result = discovery.scan(
        db=db, data_dir=tmp_path, max_sites=5, use_search=False, client=_nofeed_client()
    )
    assert result["new_candidates"] == 1
    (candidate,) = discovery.list_candidates(tmp_path)["candidates"]
    assert candidate["type"] == "sitemap"
    assert candidate["feed_url"] == "https://nofeed.example/news-sitemap.xml"
    assert "no feed published" in candidate["origin"]


def test_accepting_a_sitemap_candidate_creates_a_sitemap_source(tmp_path: Path) -> None:
    db = _seeded_db(tmp_path, {1: ["https://nofeed.example/1"], 2: ["https://nofeed.example/2"]})
    discovery.scan(db=db, data_dir=tmp_path, max_sites=5, use_search=False, client=_nofeed_client())
    (candidate,) = discovery.list_candidates(tmp_path)["candidates"]

    result = discovery.accept_candidate(candidate["id"], data_dir=tmp_path)
    assert result["source"]["type"] == "sitemap"
    assert config_mod.SOURCES[result["source"]["id"]].type == "sitemap"


def test_a_site_without_dated_headlines_is_not_proposed(tmp_path: Path) -> None:
    """An undated archive listing says nothing about what the outlet publishes now."""
    undated = """<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
      <url><loc>https://nofeed.example/a</loc></url>
      <url><loc>https://nofeed.example/b</loc></url>
      <url><loc>https://nofeed.example/c</loc></url></urlset>"""

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url == "https://nofeed.example/":
            return httpx.Response(200, text="<html><head></head><body>x</body></html>")
        if url.endswith(("/sitemap.xml", "/news-sitemap.xml", "/sitemap-news.xml")):
            return httpx.Response(200, text=undated)
        return httpx.Response(404)

    db = _seeded_db(tmp_path, {1: ["https://nofeed.example/1"], 2: ["https://nofeed.example/2"]})
    result = discovery.scan(
        db=db,
        data_dir=tmp_path,
        max_sites=5,
        use_search=False,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    assert result["new_candidates"] == 0


# --- choosing what to look for ----------------------------------------------


def test_look_for_rss_skips_the_sitemap_fallback(tmp_path: Path) -> None:
    db = _seeded_db(tmp_path, {1: ["https://nofeed.example/1"], 2: ["https://nofeed.example/2"]})
    result = discovery.scan(
        db=db,
        data_dir=tmp_path,
        max_sites=5,
        use_search=False,
        look_for="rss",
        client=_nofeed_client(),
    )
    assert result["look_for"] == "rss"
    assert result["new_candidates"] == 0  # the site has a sitemap but no feed


def test_look_for_sitemap_does_not_probe_feeds(tmp_path: Path) -> None:
    seen: list[str] = []
    db = _seeded_db(tmp_path, {1: ["https://nofeed.example/1"], 2: ["https://nofeed.example/2"]})
    result = discovery.scan(
        db=db,
        data_dir=tmp_path,
        max_sites=5,
        use_search=False,
        look_for="sitemap",
        client=_nofeed_client(seen),
    )
    assert result["new_candidates"] == 1
    assert not any(url.endswith(("/feed/", "/rss.xml", "/blog/feed/")) for url in seen)


def test_both_is_the_default_and_prefers_the_feed(tmp_path: Path) -> None:
    """A site with a working feed is proposed as rss, not as a sitemap."""

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url == "https://good.example/feed/":
            return httpx.Response(200, content=MESSAGING_FEED.encode("utf-8"))
        if url == "https://good.example/":
            return httpx.Response(200, text="<html><head></head><body>site</body></html>")
        return httpx.Response(404)

    db = _seeded_db(tmp_path, {1: ["https://good.example/1"], 2: ["https://good.example/2"]})
    result = discovery.scan(
        db=db,
        data_dir=tmp_path,
        max_sites=5,
        use_search=False,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    assert result["look_for"] == "both"
    (candidate,) = discovery.list_candidates(tmp_path)["candidates"]
    assert candidate["type"] == "rss"


def test_an_unknown_look_for_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="look_for"):
        discovery.scan(db=_seeded_db(tmp_path, {}), data_dir=tmp_path, look_for="carrier-pigeon")


def test_api_passes_look_for_to_the_scan(client, monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_runner(**kwargs) -> int:  # noqa: ANN003
        captured.update(kwargs)
        return 0

    monkeypatch.setattr("telecom_news.pipeline_run._default_runner", fake_runner)
    started = client.post(
        "/api/source-candidates/scan",
        json={"max_sites": 3, "look_for": "sitemap", "use_search": False},
    )
    assert started.status_code == 200
    for _ in range(40):
        if client.get("/api/run/status").json()["status"] != "running":
            break
    assert captured["options"] == {"use_search": False, "look_for": "sitemap"}


def test_api_rejects_an_unknown_look_for(client) -> None:
    response = client.post("/api/source-candidates/scan", json={"look_for": "nope"})
    assert response.status_code == 400


def test_gui_offers_the_look_for_choice(client) -> None:
    html = client.get("/").text
    assert 'id="discover-look-for"' in html
    assert "sitemaps only" in html
