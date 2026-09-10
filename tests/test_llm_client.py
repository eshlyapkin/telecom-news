"""Tests for the LM Studio client (M3). HTTP is mocked, no server needed."""

from __future__ import annotations

import json

import httpx
import pytest

from telecom_news.llm.client import (
    LLMClient,
    LLMResponseError,
    LLMUnavailableError,
    parse_json_response,
)


def _mock_client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def _chat_envelope(content: str) -> dict:
    return {"choices": [{"message": {"role": "assistant", "content": content}}]}


def test_chat_posts_openai_compatible_payload() -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["payload"] = json.loads(request.content)
        return httpx.Response(200, json=_chat_envelope("hi"))

    client = LLMClient(model="test-model", backoff_base=0)
    messages = [{"role": "user", "content": "hello"}]
    assert client.chat(messages, client=_mock_client(handler)) == "hi"
    assert seen["url"] == "http://localhost:1234/v1/chat/completions"
    assert seen["payload"]["model"] == "test-model"
    assert seen["payload"]["messages"] == messages


def test_ensure_model_autodetects_first_loaded() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        assert request.url.path == "/v1/models"
        return httpx.Response(200, json={"data": [{"id": "first"}, {"id": "second"}]})

    client = LLMClient(backoff_base=0)
    mock = _mock_client(handler)
    assert client.ensure_model(client=mock) == "first"
    assert client.ensure_model(client=mock) == "first"  # cached
    assert calls["n"] == 1


def test_ensure_model_prefers_explicit() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("no HTTP expected")

    client = LLMClient(model="explicit")
    assert client.ensure_model(client=_mock_client(handler)) == "explicit"


def test_no_models_loaded_is_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": []})

    client = LLMClient(backoff_base=0)
    with pytest.raises(LLMUnavailableError, match="no models loaded"):
        client.ensure_model(client=_mock_client(handler))


def test_chat_retries_timeout_then_succeeds() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.TimeoutException("slow", request=request)
        return httpx.Response(200, json=_chat_envelope("recovered"))

    client = LLMClient(model="m", backoff_base=0)
    reply = client.chat([{"role": "user", "content": "x"}], client=_mock_client(handler))
    assert reply == "recovered"
    assert calls["n"] == 2


def test_persistent_500_is_unavailable() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(500, json={"error": "boom"})

    client = LLMClient(model="m", max_retries=2, backoff_base=0)
    with pytest.raises(LLMUnavailableError):
        client.chat([{"role": "user", "content": "x"}], client=_mock_client(handler))
    assert calls["n"] == 2


def test_404_fails_fast() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(404, json={"error": "missing"})

    client = LLMClient(model="m", max_retries=3, backoff_base=0)
    with pytest.raises(LLMUnavailableError, match="HTTP 404"):
        client.chat([{"role": "user", "content": "x"}], client=_mock_client(handler))
    assert calls["n"] == 1


def test_malformed_envelope_is_response_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"unexpected": True})

    client = LLMClient(model="m", backoff_base=0)
    with pytest.raises(LLMResponseError):
        client.chat([{"role": "user", "content": "x"}], client=_mock_client(handler))


def test_empty_content_is_response_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_chat_envelope("   "))

    client = LLMClient(model="m", backoff_base=0)
    with pytest.raises(LLMResponseError):
        client.chat([{"role": "user", "content": "x"}], client=_mock_client(handler))


def test_parse_plain_json() -> None:
    assert parse_json_response('{"a": 1}') == {"a": 1}


def test_parse_fenced_json() -> None:
    assert parse_json_response('```json\n{"a": 1}\n```') == {"a": 1}


def test_parse_embedded_json() -> None:
    assert parse_json_response('Sure: {"a": 1} done.') == {"a": 1}


def test_parse_rejects_garbage() -> None:
    with pytest.raises(LLMResponseError):
        parse_json_response("no json here")


def test_parse_rejects_non_object() -> None:
    with pytest.raises(LLMResponseError):
        parse_json_response("[1, 2]")
