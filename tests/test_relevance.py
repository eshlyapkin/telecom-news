"""Tests for relevance/classification and summarization prompts (M3).

Fake LLM, no network: canned replies plus prompt-content assertions.
"""

from __future__ import annotations

import json

import pytest

from telecom_news.llm.client import LLMResponseError
from telecom_news.models import Article
from telecom_news.processors.relevance import (
    CATEGORIES,
    check_relevance,
    has_messaging_signal,
    is_obviously_off_topic,
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


def test_obvious_video_article_is_rejected_without_llm_call() -> None:
    fake = _FakeLLM(['{"relevant": true, "category": "technology"}'])
    article = _article(
        title="Build a Video Chat Application with Programmable Video",
        body="Create a WebRTC video application.",
    )
    assert is_obviously_off_topic(article) is True
    result = check_relevance(fake, article)
    assert (result.relevant, result.category) == (False, None)
    assert fake.calls == []


def test_generic_vendor_article_without_messaging_signal_is_rejected() -> None:
    fake = _FakeLLM(['{"relevant": true, "category": "vendor"}'])
    article = _article(title="The future of enterprise AI", body="AI improves operations.")
    assert has_messaging_signal(article) is False
    assert check_relevance(fake, article).relevant is False
    assert fake.calls == []


def test_messaging_article_with_video_reference_is_not_rejected() -> None:
    article = _article(
        title="Business messaging and video chat for customer engagement",
        body="SMS fallback keeps the conversation reachable.",
    )
    assert is_obviously_off_topic(article) is False


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


# --- D-011: Russian-language coverage of the deterministic guard -------------


def _ru_article(title: str, body: str = "") -> Article:
    return Article(url="https://example.com/ru", source_id="cnews-telecom", title=title, body=body)


@pytest.mark.parametrize(
    "title",
    [
        "МТС запустила защиту от СМС-мошенничества для корпоративных клиентов",
        "Оператор внедрил код подтверждения вместо пароля в мобильном приложении",
        "Банк запустил официального чат-бота в Telegram для сообщений клиентам",
        "Разработана платформа А2П-сообщений для банков",
        "Эксперты описали новую схему смшинга через подмену номера",
        "Мессенджер ввёл бизнес-рассылки для ритейла",
    ],
)
def test_russian_messaging_wording_reaches_the_llm(title: str) -> None:
    """A Cyrillic story must not be dropped before the LLM sees it."""
    assert has_messaging_signal(_ru_article(title)) is True


@pytest.mark.parametrize(
    "title",
    [
        "МегаФон расширил зону LTE для трёх тысяч населённых пунктов",
        "Билайн запустил 5G с увеличением скорости мобильного интернета",
        "Ростелеком установил камеры видеонаблюдения на избирательных участках",
        "В России разработан комплект СВЧ-чипов для радаров и БПЛА",
    ],
)
def test_russian_generic_telecom_stays_out_without_llm(title: str) -> None:
    """General telecom/5G/сamera news is still gated off — the guard is RU-aware,
    not disabled for Russian text."""
    fake = _FakeLLM(['{"relevant": true, "category": "carrier"}'])
    assert has_messaging_signal(_ru_article(title)) is False
    assert check_relevance(fake, _ru_article(title)).relevant is False
    assert fake.calls == []


def test_russian_voice_topic_rejected_by_title_rule() -> None:
    """The title rule makes the off-topic guard reachable from check_relevance."""
    article = _ru_article(
        "Как настроить видеозвонки в корпоративной АТС",
        "Инструкция касается и SMS-уведомлений о пропущенных вызовах.",
    )
    assert has_messaging_signal(article) is True
    assert is_obviously_off_topic(article) is True
    fake = _FakeLLM(['{"relevant": true, "category": "technology"}'])
    assert check_relevance(fake, article).relevant is False
    assert fake.calls == []


def test_russian_messaging_title_survives_voice_reference() -> None:
    """A messaging story that merely mentions voice calls is not rejected."""
    article = _ru_article(
        "СМС-оповещение останется доступным при голосовых вызовах",
        "Клиент получает короткое сообщение, если абонент недоступен.",
    )
    assert is_obviously_off_topic(article) is False
    fake = _FakeLLM(['{"relevant": true, "category": "product_service", "reason": "SMS"}'])
    assert check_relevance(fake, article).relevant is True


def test_gate_bypass_sends_articles_without_keywords_to_the_model() -> None:
    """D-012: narrow feeds rely on the model, not on the keyword list."""
    article = Article(
        url="https://example.com/narrow",
        source_id="content-review",
        title="Оператор запустил сервис коротких сообщений для бизнеса",
        body="Подробности в материале.",
        language="ru",
    )
    llm = _FakeLLM([json.dumps({"relevant": True, "category": "carrier", "reason": "messaging"})])

    gated = check_relevance(llm, article)
    assert gated.relevant is False and llm.calls == []

    bypassed = check_relevance(llm, article, use_gate=False)
    assert bypassed.relevant is True and bypassed.category == "carrier"
    assert len(llm.calls) == 1
