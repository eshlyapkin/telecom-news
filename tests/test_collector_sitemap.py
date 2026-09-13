"""Sitemap collector: read outlets that publish no RSS feed. No real network."""

from __future__ import annotations

import gzip
from datetime import datetime, timezone

import httpx
import pytest

from telecom_news.collectors import CollectorError, SitemapCollector, discover_sitemaps
from telecom_news.collectors.sitemap import page_metadata, parse_sitemap

NEWS_SITEMAP = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"
        xmlns:news="http://www.google.com/schemas/sitemap-news/0.9">
  <url>
    <loc>https://outlet.example/a2p-growth</loc>
    <news:news>
      <news:publication><news:name>Outlet</news:name><news:language>en</news:language></news:publication>
      <news:publication_date>2026-09-11T18:37:06+03:00</news:publication_date>
      <news:title>A2P SMS traffic grows</news:title>
    </news:news>
  </url>
  <url>
    <loc>https://outlet.example/smpp-upgrade</loc>
    <news:news>
      <news:publication_date>2026-09-12T09:00:00Z</news:publication_date>
      <news:title>SMPP gateway upgrade</news:title>
    </news:news>
  </url>
</urlset>"""

PLAIN_SITEMAP = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://outlet.example/dated</loc><lastmod>2026-09-10</lastmod></url>
  <url><loc>https://outlet.example/undated</loc></url>
</urlset>"""

SITEMAP_INDEX = """<?xml version="1.0" encoding="UTF-8"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <sitemap><loc>https://outlet.example/sitemap-archive.xml</loc></sitemap>
  <sitemap><loc>https://outlet.example/news-sitemap.xml</loc></sitemap>
</sitemapindex>"""

ARTICLE_HTML = (
    "<html><head><title>Fallback title | Outlet</title>"
    '<meta property="og:title" content="Dated story headline">'
    '<meta name="description" content="What the story is about."></head><body>x</body></html>'
)


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


# --- parsing ----------------------------------------------------------------


def test_news_sitemap_yields_headline_and_date() -> None:
    document = parse_sitemap(NEWS_SITEMAP.encode("utf-8"))
    assert document.children == []
    first, second = document.entries
    assert first.title == "A2P SMS traffic grows"
    assert first.published_at == datetime(
        2026, 9, 11, 18, 37, 6, tzinfo=timezone(__import__("datetime").timedelta(hours=3))
    )
    assert second.published_at == datetime(2026, 9, 12, 9, 0, tzinfo=timezone.utc)


def test_plain_sitemap_uses_lastmod_and_keeps_undated_entries_dateless() -> None:
    entries = parse_sitemap(PLAIN_SITEMAP.encode("utf-8")).entries
    dated, undated = entries
    assert dated.published_at == datetime(2026, 9, 10, tzinfo=timezone.utc)
    assert undated.published_at is None


def test_sitemap_index_returns_children() -> None:
    document = parse_sitemap(SITEMAP_INDEX.encode("utf-8"))
    assert document.entries == []
    assert document.children == [
        "https://outlet.example/sitemap-archive.xml",
        "https://outlet.example/news-sitemap.xml",
    ]


def test_gzipped_sitemaps_are_read() -> None:
    document = parse_sitemap(gzip.compress(NEWS_SITEMAP.encode("utf-8")))
    assert len(document.entries) == 2


def test_a_sitemap_with_a_dtd_is_refused() -> None:
    """ElementTree expands internal entities; a sitemap never needs a DTD."""
    bomb = b'<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY lol "lol">]><urlset/>'
    with pytest.raises(CollectorError, match="DTD"):
        parse_sitemap(bomb)


def test_unusable_documents_raise() -> None:
    with pytest.raises(CollectorError, match="could not parse"):
        parse_sitemap(b"<urlset>")
    with pytest.raises(CollectorError, match="root element"):
        parse_sitemap(b'<?xml version="1.0"?><rss version="2.0"><channel/></rss>')


# --- discovery --------------------------------------------------------------


def test_declared_sitemaps_come_before_guessed_ones() -> None:
    """A guessed /news-sitemap.xml must not push the declared one out of reach.

    cnews.ru declares /inc/sitemap.xml and has no /news-sitemap.xml; sorting
    "news first" across the whole list spent both probe slots on 404s and the
    real sitemap was never read.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url).endswith("/robots.txt"):
            return httpx.Response(200, text="Sitemap: https://outlet.example/inc/sitemap.xml\n")
        return httpx.Response(404)

    urls = discover_sitemaps("https://outlet.example/", client=_client(handler))
    assert urls[0] == "https://outlet.example/inc/sitemap.xml"
    assert urls.index("https://outlet.example/inc/sitemap.xml") < urls.index(
        "https://outlet.example/news-sitemap.xml"
    )


def test_declared_news_sitemap_wins_among_declared_ones() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url).endswith("/robots.txt"):
            return httpx.Response(
                200,
                text="Sitemap: https://outlet.example/sitemap.xml\n"
                "Sitemap: https://outlet.example/news-sitemap.xml\n",
            )
        return httpx.Response(404)

    urls = discover_sitemaps("https://outlet.example/", client=_client(handler))
    assert urls[0] == "https://outlet.example/news-sitemap.xml"


def test_discovery_falls_back_to_conventional_paths() -> None:
    urls = discover_sitemaps(
        "https://outlet.example/", client=_client(lambda request: httpx.Response(404))
    )
    assert "https://outlet.example/sitemap.xml" in urls


# --- collecting -------------------------------------------------------------


def test_collect_returns_newest_first_without_fetching_pages() -> None:
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        return httpx.Response(200, text=NEWS_SITEMAP)

    items = SitemapCollector(
        source_id="outlet", sitemap_url="https://outlet.example/news-sitemap.xml", language="en"
    ).collect(client=_client(handler))

    assert [item.title for item in items] == ["SMPP gateway upgrade", "A2P SMS traffic grows"]
    assert all(item.language == "en" for item in items)
    assert requested == ["https://outlet.example/news-sitemap.xml"]  # titles were in the sitemap


def test_collect_reads_page_metadata_when_the_sitemap_has_no_title() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith("sitemap.xml"):
            return httpx.Response(200, text=PLAIN_SITEMAP)
        return httpx.Response(200, text=ARTICLE_HTML)

    items = SitemapCollector(
        source_id="outlet", sitemap_url="https://outlet.example/sitemap.xml"
    ).collect(client=_client(handler))

    (item,) = items  # the undated entry is dropped, the dated one is enriched
    assert item.url == "https://outlet.example/dated"
    assert item.title == "Dated story headline"
    assert item.content == "What the story is about."


def test_collect_follows_a_sitemap_index_news_first() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith("/sitemap_index.xml"):
            return httpx.Response(200, text=SITEMAP_INDEX)
        if url.endswith("/news-sitemap.xml"):
            return httpx.Response(200, text=NEWS_SITEMAP)
        return httpx.Response(200, text=PLAIN_SITEMAP)

    items = SitemapCollector(
        source_id="outlet",
        sitemap_url="https://outlet.example/sitemap_index.xml",
        max_child_sitemaps=1,
    ).collect(client=_client(handler))
    assert [item.title for item in items] == ["SMPP gateway upgrade", "A2P SMS traffic grows"]


def test_a_sitemap_without_any_date_is_an_error() -> None:
    """telecompaper.com lists 19982 undated URLs; publishing those as news is wrong."""
    undated = """<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
      <url><loc>https://outlet.example/one</loc></url>
      <url><loc>https://outlet.example/two</loc></url></urlset>"""

    with pytest.raises(CollectorError, match="no publication dates"):
        SitemapCollector(
            source_id="outlet", sitemap_url="https://outlet.example/sitemap.xml"
        ).collect(client=_client(lambda request: httpx.Response(200, text=undated)))


def test_collect_honours_the_limit() -> None:
    items = SitemapCollector(
        source_id="outlet", sitemap_url="https://outlet.example/news-sitemap.xml"
    ).collect(limit=1, client=_client(lambda request: httpx.Response(200, text=NEWS_SITEMAP)))
    assert len(items) == 1


# --- page metadata ----------------------------------------------------------


def test_page_metadata_prefers_open_graph_then_the_title_tag() -> None:
    assert page_metadata(ARTICLE_HTML) == ("Dated story headline", "What the story is about.")
    assert page_metadata("<html><head><title>Just a title</title></head></html>") == (
        "Just a title",
        "",
    )
    assert page_metadata("<html></html>") == ("", "")
