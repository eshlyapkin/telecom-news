"""Tests for one-shot pipeline orchestration."""

from __future__ import annotations

from telecom_news import cli


def _stub_stages(monkeypatch, calls: list[tuple]) -> None:
    """Replace every stage of `run` with a recorder."""
    monkeypatch.setattr(cli, "_cmd_recover", lambda *a, **kw: calls.append(("recover", a, kw)) or 0)
    monkeypatch.setattr(
        cli, "_cmd_collect", lambda source, limit: calls.append(("collect", source, limit)) or 0
    )
    monkeypatch.setattr(cli, "_cmd_process", lambda limit: calls.append(("process", limit)) or 0)
    monkeypatch.setattr(
        cli, "_cmd_publish", lambda limit, dry: calls.append(("publish", limit, dry)) or 0
    )
    monkeypatch.setattr(
        cli, "_cmd_deliver", lambda limit, dry: calls.append(("deliver", limit, dry)) or 0
    )


def test_run_executes_all_stages_in_order(monkeypatch) -> None:
    calls: list[tuple] = []
    _stub_stages(monkeypatch, calls)

    assert cli._cmd_run("test-source", 3, True) == 0
    assert [call for call in calls if call[0] != "recover"] == [
        ("collect", "test-source", 3),
        ("process", 3),
        ("publish", None, True),
        ("deliver", None, True),
    ]


def test_run_limit_is_not_a_publication_cap(monkeypatch) -> None:
    """D-020: `run --limit 30` budgets collect/process, not the fan-out.

    Passing the processing budget through to publish/deliver meant that one
    scheduled cycle with TELECOM_NEWS_LIMIT=30 could put 30 posts in the channel
    and 30 private messages per subscriber at once; both stages now fall back to
    their own configured caps (PUBLISH_MAX_PER_CYCLE / SUBSCRIBER_MAX_PER_CYCLE)
    when `run` does not get an explicit one.
    """
    calls: list[tuple] = []
    _stub_stages(monkeypatch, calls)

    assert cli._cmd_run(None, 30, False) == 0
    assert ("process", 30) in calls
    assert ("publish", None, False) in calls
    assert ("deliver", None, False) in calls


def test_run_forwards_expensive_fanout_caps(monkeypatch) -> None:
    calls: list[tuple] = []
    _stub_stages(monkeypatch, calls)

    assert cli._cmd_run(None, 30, True, max_posts=5, max_per_subscriber=2) == 0
    assert ("publish", 5, True) in calls
    assert ("deliver", 2, True) in calls


def test_run_collects_all_enabled_sources(monkeypatch) -> None:
    calls: list[str] = []

    class Source:
        def __init__(self, source_id: str, enabled: bool = True) -> None:
            self.id = source_id
            self.enabled = enabled

    monkeypatch.setattr(cli, "_cmd_recover", lambda *a, **kw: 0)
    monkeypatch.setattr(cli, "_cmd_collect", lambda source, limit: calls.append(source) or 0)
    monkeypatch.setattr(cli, "_cmd_process", lambda limit: 0)
    monkeypatch.setattr(cli, "_cmd_publish", lambda limit, dry: 0)
    monkeypatch.setattr(cli, "_cmd_deliver", lambda limit, dry: 0)
    monkeypatch.setattr(
        "telecom_news.config.SOURCES",
        {"a": Source("a"), "b": Source("b"), "off": Source("off", False)},
    )

    assert cli._cmd_run(None, None, True) == 0
    assert calls == ["a", "b"]


def test_run_still_publishes_existing_processed_articles_on_process_failure(monkeypatch) -> None:
    calls: list[str] = []

    monkeypatch.setattr(cli, "_cmd_recover", lambda *a, **kw: 0)
    monkeypatch.setattr(cli, "_cmd_collect", lambda source, limit: calls.append("collect") or 0)
    monkeypatch.setattr(cli, "_cmd_process", lambda limit: calls.append("process") or 1)
    monkeypatch.setattr(cli, "_cmd_publish", lambda limit, dry: calls.append("publish") or 0)
    monkeypatch.setattr(cli, "_cmd_deliver", lambda limit, dry: calls.append("deliver") or 0)

    assert cli._cmd_run("test-source", None, False) == 1
    assert calls == ["collect", "process", "publish", "deliver"]


def test_run_continues_after_collection_failure(monkeypatch) -> None:
    calls: list[str] = []

    monkeypatch.setattr(cli, "_cmd_recover", lambda *a, **kw: 0)
    monkeypatch.setattr(cli, "_cmd_collect", lambda source, limit: calls.append("collect") or 1)
    monkeypatch.setattr(cli, "_cmd_process", lambda limit: calls.append("process") or 0)
    monkeypatch.setattr(cli, "_cmd_publish", lambda limit, dry: calls.append("publish") or 0)
    monkeypatch.setattr(cli, "_cmd_deliver", lambda limit, dry: calls.append("deliver") or 0)

    assert cli._cmd_run("test-source", None, False) == 1
    assert calls == ["collect", "process", "publish", "deliver"]
