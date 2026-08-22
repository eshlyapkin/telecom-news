"""Tests for the domain model ``Article`` (M0)."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from telecom_news.models import Article, compute_content_hash


def test_article_created_with_minimal_data() -> None:
    article = Article(url="https://example.com/news/1", source_id="test-source")
    assert article.url == "https://example.com/news/1"
    assert article.source_id == "test-source"
    # Defaults for fields filled in by later pipeline stages.
    assert article.status == "new"
    assert article.relevance is None
    assert article.category is None
    assert article.llm_result is None


def test_article_content_hash_computed_and_stable() -> None:
    a = Article(url="https://example.com/1", source_id="s", body="hello world")
    b = Article(url="https://example.com/1", source_id="s", body="hello world")
    assert a.content_hash == b.content_hash
    assert len(a.content_hash) == 64  # sha256 hex digest


def test_article_content_hash_changes_with_text() -> None:
    a = Article(url="https://example.com/1", source_id="s", body="text A")
    b = Article(url="https://example.com/1", source_id="s", body="text B")
    assert a.content_hash != b.content_hash


def test_explicit_content_hash_is_preserved() -> None:
    article = Article(
        url="https://example.com/1",
        source_id="s",
        body="body",
        content_hash="fixed-hash",
    )
    assert article.content_hash == "fixed-hash"


def test_compute_content_hash_is_deterministic() -> None:
    assert compute_content_hash("abc") == compute_content_hash("abc")
    assert compute_content_hash("abc") != compute_content_hash("abd")


@pytest.mark.parametrize(
    ("url", "source_id"),
    [
        ("https://example.com/a", "src-a"),
        ("http://example.org/b?x=1", "src-b"),
    ],
)
def test_article_with_full_minimal_fields(url: str, source_id: str) -> None:
    now = datetime.now(timezone.utc)
    article = Article(
        url=url,
        source_id=source_id,
        title="Title",
        language="ru",
        published_at=now,
        fetched_at=now,
    )
    assert article.language == "ru"
    assert article.published_at is not None
    assert article.fetched_at is not None
