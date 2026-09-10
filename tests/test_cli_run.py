"""Tests for one-shot pipeline orchestration."""

from __future__ import annotations

from telecom_news import cli


def test_run_executes_all_stages_in_order(monkeypatch) -> None:
    calls: list[tuple] = []

    monkeypatch.setattr(
        cli, "_cmd_collect", lambda source, limit: calls.append(("collect", source, limit)) or 0
    )
    monkeypatch.setattr(cli, "_cmd_process", lambda limit: calls.append(("process", limit)) or 0)
    monkeypatch.setattr(
        cli, "_cmd_publish", lambda limit, dry: calls.append(("publish", limit, dry)) or 0
    )

    assert cli._cmd_run("test-source", 3, True) == 0
    assert calls == [
        ("collect", "test-source", 3),
        ("process", 3),
        ("publish", 3, True),
    ]


def test_run_still_publishes_existing_processed_articles_on_process_failure(monkeypatch) -> None:
    calls: list[str] = []

    monkeypatch.setattr(cli, "_cmd_collect", lambda source, limit: calls.append("collect") or 0)
    monkeypatch.setattr(cli, "_cmd_process", lambda limit: calls.append("process") or 1)
    monkeypatch.setattr(cli, "_cmd_publish", lambda limit, dry: calls.append("publish") or 0)

    assert cli._cmd_run("test-source", None, False) == 1
    assert calls == ["collect", "process", "publish"]


def test_run_stops_after_collection_failure(monkeypatch) -> None:
    calls: list[str] = []

    monkeypatch.setattr(cli, "_cmd_collect", lambda source, limit: calls.append("collect") or 1)
    monkeypatch.setattr(cli, "_cmd_process", lambda limit: calls.append("process") or 0)
    monkeypatch.setattr(cli, "_cmd_publish", lambda limit, dry: calls.append("publish") or 0)

    assert cli._cmd_run("test-source", None, False) == 1
    assert calls == ["collect"]
