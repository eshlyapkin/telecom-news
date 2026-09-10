"""Tests for collectors (M1): RSS parsing from fixture, fetch retries.

No real network: parsing tests use an inline RSS fixture, fetch tests use
``httpx.MockTransport``.
"""

from __future__ import annotations

from datetime import datetime, timezone

import httpx
import pytest

from telecom_news.collectors import CollectorError, RssCollector, parse_feed
from telecom_news.collectors.base import fetch_url

SAMPLE_RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/">
  <channel>
    <title>Sample Messaging Blog</title>
    <link>https://example.com/blog/</link>
    <item>
      <title>  RCS adoption grows among banks  </title>
      <link>https://example.com/blog/rcs-banks/?utm_source=feed&amp;page=2</link>
      <pubDate>Fri, 28 Aug 2026 14:10:59 +0000</pubDate>
      <description>Short summary.</description>
      <content:encoded><![CDATA[<p>Full article body about RCS.</p>]]></content:encoded>
    </item>
    <item>
      <title>SMS firewall update</title>
      <link>https://example.com/blog/sms-firewall/</link>
      <description>Only a summary, no date, no full content.</description>
    </item>
    <item>
      <title>Entry without link is skipped</title>
      <description>No link element at all.</description>
    </item>
  </channel>
</rss>
"""


def test_parse_feed_maps_entries_to_raw_items() -> None:
    items = parse_feed(SAMPLE_RSS.encode("utf-8"), "sample", "en")

    assert len(items) == 2  # entry without link is skipped
    first, second = items

    assert first.source_id == "sample"
    assert first.title == "RCS adoption grows among banks"  # trimmed
    assert first.url == "https://example.com/blog/rcs-banks/?utm_source=feed&page=2"
    assert first.published_at == datetime(2026, 8, 28, 14, 10, 59, tzinfo=timezone.utc)
    assert first.content == "<p>Full article body about RCS.</p>"  # content:encoded wins
    assert first.language == "en"

    assert second.title == "SMS firewall update"
    assert second.published_at is None  # missing date stays None
    assert second.content == "Only a summary, no date, no full content."  # summary fallback


def test_parse_feed_rejects_garbage() -> None:
    with pytest.raises(CollectorError):
        parse_feed(b"this is not xml at all \x00\x01", "sample")


def _mock_client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_collector_collects_through_mock_transport() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == "https://example.com/feed/"
        return httpx.Response(200, content=SAMPLE_RSS.encode("utf-8"))

    collector = RssCollector("sample", "https://example.com/feed/", language="en")
    items = collector.collect(client=_mock_client(handler))

    assert len(items) == 2
    assert all(item.source_id == "sample" for item in items)


def test_collector_limit_is_respected() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=SAMPLE_RSS.encode("utf-8"))

    collector = RssCollector("sample", "https://example.com/feed/")
    assert len(collector.collect(limit=1, client=_mock_client(handler))) == 1


def test_fetch_retries_transient_errors_then_succeeds() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ConnectError("boom", request=request)
        return httpx.Response(200, content=b"ok")

    body = fetch_url(
        "https://example.com/feed/", max_retries=3, backoff_base=0, client=_mock_client(handler)
    )
    assert body == b"ok"
    assert calls["n"] == 2


def test_fetch_retries_http_500_then_succeeds() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(500, content=b"error")
        return httpx.Response(200, content=b"recovered")

    body = fetch_url(
        "https://example.com/feed/", max_retries=3, backoff_base=0, client=_mock_client(handler)
    )
    assert body == b"recovered"
    assert calls["n"] == 3


def test_fetch_gives_up_after_max_retries() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(500, content=b"always broken")

    with pytest.raises(CollectorError):
        fetch_url(
            "https://example.com/feed/",
            max_retries=2,
            backoff_base=0,
            client=_mock_client(handler),
        )
    assert calls["n"] == 2


def test_fetch_does_not_retry_http_404() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(404, content=b"missing")

    with pytest.raises(CollectorError, match="HTTP 404"):
        fetch_url(
            "https://example.com/feed/",
            max_retries=3,
            backoff_base=0,
            client=_mock_client(handler),
        )
    assert calls["n"] == 1


def test_collector_wraps_http_failure_as_collector_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, content=b"missing")

    collector = RssCollector("sample", "https://example.com/feed/", backoff_base=0)
    with pytest.raises(CollectorError):
        collector.collect(client=_mock_client(handler))
