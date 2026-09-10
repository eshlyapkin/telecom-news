"""Tests for relevance/classification and summarization prompts (M3).

Fake LLM, no network: canned replies plus prompt-content assertions.
"""

from __future__ import annotations

import pytest

from telecom_news.llm.client import LLMResponseError
from telecom_news.models import Article
from telecom_news.processors.relevance import (
    CATEGORIES,
    check_relevance,
    prepare_article_text,
)
from telecom_news.processors.summarize import summarize


class _FakeLLM:
    """Canned replies; records prompts for assertions."""

    def __init__(self, replies: list[str]) -> None:
        self._replies = list(replies)
        self.calls: list[list[dict[str, str]]] = []

    def chat(self, messages: list[dict[str, str]], **kwargs) -> str:
        self.calls.append(messages)
        return self._replies.pop(0)


def _article(**overrides) -> Article:
    base = {
        "url": "https://example.com/1",
        "source_id": "t",
        "title": "Sinch launches SMS firewall",
        "body": "<p>Vendor news about <b>SMS</b> security.</p>",
    }
    base.update(overrides)
    return Article(**base)


def test_relevant_with_category() -> None:
    fake = _FakeLLM(['{"relevant": true, "category": "vendor", "reason": "SMS vendor news"}'])
    result = check_relevance(fake, _article())
    assert (result.relevant, result.category) == (True, "vendor")
    assert result.reason == "SMS vendor news"


def test_irrelevant_yields_no_category() -> None:
    fake = _FakeLLM(['{"relevant": false, "category": "vendor", "reason": "general telecom"}'])
    result = check_relevance(fake, _article())
    assert (result.relevant, result.category) == (False, None)


def test_missing_flag_is_response_error() -> None:
    fake = _FakeLLM(['{"category": "vendor"}'])
    with pytest.raises(LLMResponseError):
        check_relevance(fake, _article())


def test_string_flags_are_coerced() -> None:
    assert check_relevance(_FakeLLM(['{"relevant": "yes"}']), _article()).relevant is True
    assert check_relevance(_FakeLLM(['{"relevant": "no"}']), _article()).relevant is False


def test_unknown_flag_is_response_error() -> None:
    with pytest.raises(LLMResponseError):
        check_relevance(_FakeLLM(['{"relevant": "maybe"}']), _article())


def test_unknown_category_is_lenient_null() -> None:
    fake = _FakeLLM(['{"relevant": true, "category": "satellites"}'])
    result = check_relevance(fake, _article())
    assert (result.relevant, result.category) == (True, None)


def test_prompt_contains_title_and_plain_text() -> None:
    fake = _FakeLLM(['{"relevant": true, "category": "technology"}'])
    check_relevance(fake, _article())
    (system, user) = fake.calls[0]
    assert "SMS" in system["content"]
    assert "Sinch launches SMS firewall" in user["content"]
    assert "<p>" not in user["content"]  # tags stripped for the prompt
    assert "SMS security" in user["content"]


def test_all_roadmap_categories_are_known() -> None:
    assert set(CATEGORIES) == {
        "technology",
        "vendor",
        "aggregator",
        "carrier",
        "product_service",
        "partnership",
        "ma_investment",
        "security_antifraud",
        "regulation",
    }


def test_prepare_text_strips_and_truncates() -> None:
    assert prepare_article_text(_article()) == "Vendor news about SMS security."
    long_text = prepare_article_text(_article(body="<p>" + "word " * 2000 + "</p>"), max_chars=100)
    assert len(long_text) <= 101
    assert long_text.endswith("…")
    assert prepare_article_text(_article(body="")) == "Sinch launches SMS firewall"


def test_summarize_returns_summary_in_target_language() -> None:
    fake = _FakeLLM(['{"summary": "  Краткое саммари.  "}'])
    result = summarize(fake, _article(), target_lang="ru")
    assert result.summary == "Краткое саммари."
    assert result.summary_language == "ru"
    assert "Russian" in fake.calls[0][0]["content"]


def test_summarize_asks_english_for_en() -> None:
    fake = _FakeLLM(['{"summary": "Short summary."}'])
    summarize(fake, _article(), target_lang="en")
    assert "English" in fake.calls[0][0]["content"]


def test_empty_summary_is_response_error() -> None:
    with pytest.raises(LLMResponseError):
        summarize(_FakeLLM(['{"summary": "  "}']), _article(), target_lang="ru")
