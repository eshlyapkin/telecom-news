"""Telegram delivery tests; all HTTP is mocked."""

from __future__ import annotations

import httpx
import pytest

from telecom_news.delivery.telegram import TelegramClient, TelegramError, format_post
from telecom_news.models import Article


def _article(**overrides) -> Article:
    values = {
        "url": "https://example.com/a?x=1&y=2",
        "source_id": "test",
        "title": "A <headline>",
        "body": "body",
        "category": "vendor",
        "llm_result": {"summary": "Summary <here>", "summary_language": "ru"},
    }
    values.update(overrides)
    return Article(**values)


def test_format_post_escapes_html() -> None:
    text = format_post(_article())
    assert "A &lt;headline&gt;" in text
    assert "Summary &lt;here&gt;" in text
    assert 'href="https://example.com/a?x=1&amp;y=2"' in text
    assert "Категория:</b> vendor" in text


def test_format_post_requires_summary() -> None:
    with pytest.raises(TelegramError):
        format_post(_article(llm_result={}))


def test_send_message_payload() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 4}})

    result = TelegramClient(
        "token", transport=httpx.MockTransport(handler), sleep=lambda _: None
    ).send_message("-100", "<b>Hello</b>")
    assert result["ok"] is True
    assert requests[0].url.path == "/bottoken/sendMessage"
    assert requests[0].content.decode().find('"parse_mode":"HTML"') >= 0


def test_retries_rate_limit_using_retry_after() -> None:
    attempts = 0
    delays: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(429, json={"ok": False, "parameters": {"retry_after": 7}})
        return httpx.Response(200, json={"ok": True})

    TelegramClient(
        "token", max_retries=1, transport=httpx.MockTransport(handler), sleep=delays.append
    ).send_message("chat", "text")
    assert attempts == 2
    assert delays == [7.0]


def test_retries_transport_error() -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise httpx.ConnectError("down", request=request)
        return httpx.Response(200, json={"ok": True})

    TelegramClient(
        "token", max_retries=1, transport=httpx.MockTransport(handler), sleep=lambda _: None
    ).send_message("chat", "text")
    assert attempts == 2


def test_non_retryable_api_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"ok": False, "description": "bad chat"})

    with pytest.raises(TelegramError, match="bad chat"):
        TelegramClient("token", transport=httpx.MockTransport(handler)).send_message("chat", "text")
