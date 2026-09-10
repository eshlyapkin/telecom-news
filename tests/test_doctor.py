"""Tests for the M7 dependency doctor without real network services."""

from __future__ import annotations

from pathlib import Path

import httpx

from telecom_news import cli
from telecom_news.config import SourceConfig


def test_doctor_reports_all_checks_ok(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "-100")
    monkeypatch.setattr(
        "telecom_news.config.SOURCES",
        {"test": SourceConfig(id="test", url="https://example.com/feed.xml", language="en")},
    )

    class FakeRSS:
        def __init__(self, **kwargs) -> None:
            pass

        def collect(self, *, limit: int):
            assert limit == 1
            return [object()]

    class FakeLLM:
        def __init__(self, **kwargs) -> None:
            pass

        def ensure_model(self) -> str:
            return "test-model"

    class FakeHTTPClient:
        def __init__(self, **kwargs) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def get(self, url: str) -> httpx.Response:
            assert url.endswith("/getMe")
            return httpx.Response(200, json={"ok": True})

    monkeypatch.setattr("telecom_news.collectors.RssCollector", FakeRSS)
    monkeypatch.setattr("telecom_news.llm.client.LLMClient", FakeLLM)
    monkeypatch.setattr(httpx, "Client", FakeHTTPClient)

    assert cli._cmd_doctor(tmp_path / "news.db") == 0
    output = capsys.readouterr().out
    assert "[OK] database" in output
    assert "[OK] telegram" in output
    assert "[OK] lmstudio" in output
    assert "[OK] source:test" in output
