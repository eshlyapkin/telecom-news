"""Tests for RawItem -> Article normalization (M1). Pure, no network."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from telecom_news.collectors.base import RawItem
from telecom_news.models import compute_content_hash
from telecom_news.processors import (
    ensure_utc,
    normalize_item,
    normalize_text,
    normalize_url,
)


def test_tracking_params_are_removed_others_kept() -> None:
    url = (
        "https://Example.COM/blog/post/?utm_source=feed&utm_medium=rss&fbclid=abc"
        "&gclid=def&page=2&q=sms"
    )
    assert normalize_url(url) == "https://example.com/blog/post/?page=2&q=sms"


def test_fragment_is_dropped_and_url_trimmed() -> None:
    assert (
        normalize_url("  https://example.com/a/b/?x=1#section-2 \n")
        == "https://example.com/a/b/?x=1"
    )


def test_url_without_tracking_params_is_unchanged() -> None:
    assert normalize_url("https://sinch.com/blog/some-post/") == "https://sinch.com/blog/some-post/"


def test_malformed_url_returned_as_is() -> None:
    assert normalize_url("  not a url  ") == "not a url"


def test_whitespace_is_collapsed() -> None:
    assert normalize_text("  hello\n\t world \r\n test  ") == "hello world test"


def test_ensure_utc_converts_aware_datetimes() -> None:
    plus3 = timezone(timedelta(hours=3))
    aware = datetime(2026, 8, 28, 17, 10, 59, tzinfo=plus3)
    assert ensure_utc(aware) == datetime(2026, 8, 28, 14, 10, 59, tzinfo=timezone.utc)


def test_ensure_utc_assumes_naive_is_utc() -> None:
    naive = datetime(2026, 8, 28, 14, 10, 59)
    assert ensure_utc(naive) == naive.replace(tzinfo=timezone.utc)


def test_ensure_utc_keeps_none() -> None:
    assert ensure_utc(None) is None


def _raw(**overrides) -> RawItem:
    base = {
        "url": "https://example.com/blog/x/?utm_source=feed",
        "source_id": "sample",
        "title": "  Hello   SMS  ",
        "published_at": datetime(2026, 8, 28, 14, 10, 59, tzinfo=timezone.utc),
        "content": "Body\nwith   spacing.",
        "language": "en",
    }
    base.update(overrides)
    return RawItem(**base)


def test_normalize_item_maps_and_cleans_fields() -> None:
    fetched = datetime(2026, 9, 10, 12, 0, 0, tzinfo=timezone.utc)
    article = normalize_item(_raw(), fetched_at=fetched)

    assert article.url == "https://example.com/blog/x/"
    assert article.source_id == "sample"
    assert article.title == "Hello SMS"
    assert article.body == "Body with spacing."
    assert article.language == "en"
    assert article.published_at == datetime(2026, 8, 28, 14, 10, 59, tzinfo=timezone.utc)
    assert article.fetched_at == fetched
    assert article.content_hash == compute_content_hash("Body with spacing.")


def test_content_hash_is_stable_across_runs() -> None:
    first = normalize_item(_raw())
    second = normalize_item(_raw())
    assert first.content_hash == second.content_hash
    assert len(first.content_hash) == 64


def test_content_hash_changes_when_body_changes() -> None:
    assert (
        normalize_item(_raw(content="text A")).content_hash
        != normalize_item(_raw(content="text B")).content_hash
    )


def test_empty_body_falls_back_to_title_for_hash() -> None:
    article = normalize_item(_raw(content="   ", title="Title only"))
    assert article.body == ""
    assert article.content_hash == compute_content_hash("Title only")


def test_missing_language_defaults_to_en() -> None:
    assert normalize_item(_raw(language=None)).language == "en"


def test_fetched_at_defaults_to_now_utc() -> None:
    before = datetime.now(timezone.utc)
    article = normalize_item(_raw())
    after = datetime.now(timezone.utc)
    assert article.fetched_at is not None
    assert before <= article.fetched_at <= after
