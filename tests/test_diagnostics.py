"""Tests for publication diagnostics (log parsing + verdict rules). No network."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

from telecom_news.diagnostics import (
    Facts,
    RunSummary,
    analyze,
    parse_pipeline_log,
    probe_llm,
    probe_telegram,
    render,
    report_json,
)

NOW = datetime(2026, 9, 11, 18, 0, 0, tzinfo=timezone.utc)
_REAL_HTTPX_CLIENT = httpx.Client


def _log_block(started: str, body: list[str], exit_code: int = 0) -> str:
    lines = [f"===== {started} =====", *body, f"exit_code={exit_code}"]
    return "\n".join(lines) + "\n"


SAMPLE_LOG = _log_block(
    "2026-09-11T17:30:00+00:00",
    [
        "Collected 10 article(s) from 'cnews-telecom' (https://example.com/feed.xml):",
        "Stored 0 new, skipped 10 duplicate(s).",
        "Collected 10 article(s) from 'content-review' (https://example.com/ru.xml):",
        "Stored 2 new, skipped 8 duplicate(s).",
        "[410] skipped (irrelevant): «МегаФон» запустил 5G в Краснодаре",
        "[411] skipped (irrelevant): Связь 5G запущена в России",
        "Done: 0 processed, 2 skipped, 0 error(s).",
        "Nothing to publish (no articles with status 'processed').",
    ],
) + _log_block(
    "2026-09-11T17:45:00+00:00",
    [
        "Collected 10 article(s) from 'sinch-blog' (https://example.com/feed.xml):",
        "Stored 1 new, skipped 9 duplicate(s).",
        "[412] processed (vendor): Sinch launches new A2P messaging API",
        "Done: 1 processed, 0 skipped, 0 error(s).",
        "[412] published: Sinch launches new A2P messaging API",
        "Done: 1 published, 0 error(s).",
    ],
)


def test_parse_pipeline_log_extracts_run_totals() -> None:
    runs = parse_pipeline_log(SAMPLE_LOG, limit=5)

    assert len(runs) == 2
    first, second = runs
    assert first.started_at == "2026-09-11T17:30:00+00:00"
    assert (first.collected, first.stored_new, first.skipped, first.published) == (20, 2, 2, 0)
    assert first.exit_code == 0
    assert (second.collected, second.stored_new, second.processed, second.published) == (
        10,
        1,
        1,
        1,
    )


def test_parse_pipeline_log_keeps_error_notes_and_truncated_blocks() -> None:
    text = _log_block(
        "2026-09-11T17:00:00+00:00",
        [
            "error: LLM unavailable: GET /models failed with HTTP 500",
            "Done: 0 processed, 0 skipped, 0 error(s) (run unfinished — LLM unavailable)",
        ],
        exit_code=1,
    )
    text += "===== 2026-09-11T17:15:00+00:00 =====\nCollected 3 article(s) from 'x'\n"

    runs = parse_pipeline_log(text, limit=5)

    assert len(runs) == 2
    assert runs[0].exit_code == 1
    assert runs[0].notes and "LLM unavailable" in runs[0].notes[0]
    assert runs[1].collected == 3 and runs[1].exit_code is None


def test_parse_pipeline_log_tolerates_empty_and_limit() -> None:
    assert parse_pipeline_log("", limit=5) == []
    assert parse_pipeline_log(SAMPLE_LOG, limit=0) == []
    assert len(parse_pipeline_log(SAMPLE_LOG, limit=1)) == 1


def _facts(**overrides) -> Facts:
    base = dict(
        now=NOW,
        db_exists=True,
        counts={"published": 23, "processed": 0, "new": 0, "skipped": 40, "error": 0},
        last_published_at=NOW - timedelta(hours=1),
        log_path=Path("/repo/data/logs/pipeline.log"),
        log_modified_at=NOW - timedelta(minutes=5),
        telegram_configured=True,
        runs=(
            RunSummary(
                started_at="2026-09-11T17:45:00+00:00",
                collected=10,
                stored_new=1,
                processed=1,
                published=1,
            ),
        ),
        llm_ok=True,
        llm_detail="qwen at http://localhost:1234/v1",
        telegram_ok=True,
        telegram_detail="bot @test_bot",
    )
    base.update(overrides)
    return Facts(**base)


def test_healthy_pipeline_is_not_blocking() -> None:
    report = analyze(_facts())

    assert report.blocking is False
    assert any(finding.code == "healthy" for finding in report.findings)


def test_llm_outage_blocks_processing() -> None:
    report = analyze(
        _facts(
            llm_ok=False,
            llm_detail="ConnectError at http://localhost:1234/v1/models",
            counts={"published": 0, "processed": 0, "new": 118, "skipped": 5, "error": 0},
            last_published_at=None,
        )
    )

    assert report.blocking is True
    assert report.findings[0].code == "llm-down"
    assert "LM Studio" in report.verdict


def test_stale_log_blocks_with_scheduler_verdict() -> None:
    report = analyze(_facts(log_modified_at=NOW - timedelta(hours=6)))

    assert report.blocking is True
    assert any(finding.code == "stale-runs" for finding in report.findings)
    assert "scheduler log has not changed" in report.verdict


def test_missing_telegram_credentials_block_publication() -> None:
    report = analyze(_facts(telegram_configured=False))

    assert report.blocking is True
    assert any(finding.code == "telegram-config" for finding in report.findings)


def test_processed_backlog_is_blocking() -> None:
    report = analyze(
        _facts(
            counts={"published": 22, "processed": 4, "new": 0, "skipped": 40, "error": 0},
            oldest_processed_at=NOW - timedelta(hours=3),
        )
    )

    assert report.blocking is True
    assert any(finding.code == "stuck-processed" for finding in report.findings)


def test_quiet_channel_is_a_warning_explaining_the_relevance_filter() -> None:
    report = analyze(
        _facts(
            counts={"published": 23, "processed": 0, "new": 0, "skipped": 90, "error": 0},
            last_published_at=NOW - timedelta(hours=20),
            runs=(
                RunSummary(
                    started_at="2026-09-11T17:30:00+00:00", collected=20, stored_new=2, skipped=2
                ),
                RunSummary(
                    started_at="2026-09-11T17:45:00+00:00", collected=10, stored_new=1, skipped=1
                ),
            ),
        )
    )

    quiet = next(finding for finding in report.findings if finding.code == "quiet-channel")
    assert report.blocking is False
    assert "rejected 3 as irrelevant" in quiet.message
    assert "D-012" in quiet.hint


def test_quiet_channel_without_new_items_mentions_supply() -> None:
    report = analyze(
        _facts(
            counts={"published": 23, "processed": 0, "new": 0, "skipped": 90, "error": 0},
            last_published_at=NOW - timedelta(hours=20),
            runs=(RunSummary(started_at="2026-09-11T17:45:00+00:00", collected=10, stored_new=0),),
        )
    )

    quiet = next(finding for finding in report.findings if finding.code == "quiet-channel")
    assert "no new articles" in quiet.message


def test_missing_database_is_blocking() -> None:
    report = analyze(_facts(db_exists=False, counts={}))

    assert report.blocking is True
    assert report.findings[0].code == "no-database"


def test_render_and_json_include_evidence() -> None:
    facts = _facts()
    report = analyze(facts)

    text = render(report, facts)
    assert "Articles: published=23" in text
    assert "DIAGNOSIS:" in text
    assert "collected=10" in text

    payload = report_json(report)
    assert '"blocking": false' in payload
    assert "healthy" in payload


def _mock_http(monkeypatch, handler: Callable[[httpx.Request], httpx.Response]) -> None:
    """Route every ``httpx.Client`` created by the module under test to a mock."""
    monkeypatch.setattr(
        httpx,
        "Client",
        lambda **kwargs: _REAL_HTTPX_CLIENT(transport=httpx.MockTransport(handler)),
    )


def test_probe_llm_reports_model_and_network_error(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [{"id": "qwen3-4b"}]})

    _mock_http(monkeypatch, handler)
    ok, detail = probe_llm("http://localhost:1234/v1")
    assert ok is True and "qwen3-4b" in detail

    def failing(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route", request=request)

    _mock_http(monkeypatch, failing)
    ok, detail = probe_llm("http://localhost:1234/v1")
    assert ok is False and "ConnectError" in detail

    def empty(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": []})

    _mock_http(monkeypatch, empty)
    ok, detail = probe_llm("http://localhost:1234/v1")
    assert ok is False and "no model loaded" in detail


def test_probe_telegram_reports_bot_username_and_rejects_bad_token(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if "badtoken" in request.url.path:
            return httpx.Response(401, json={"ok": False, "description": "Unauthorized"})
        return httpx.Response(200, json={"ok": True, "result": {"username": "news_bot"}})

    _mock_http(monkeypatch, handler)

    ok, detail = probe_telegram("good")
    assert ok is True and detail == "bot @news_bot"

    ok, detail = probe_telegram("badtoken")
    assert ok is False and detail == "Unauthorized"
    assert "badtoken" not in detail


def test_verdict_prefers_the_most_actionable_warning() -> None:
    facts = _facts(
        counts={"published": 23, "processed": 0, "new": 0, "skipped": 90, "error": 0},
        last_published_at=NOW - timedelta(hours=20),
        failing_sources=(("cnews-biz", "HTTP 403"),),
        runs=(
            RunSummary(
                started_at="2026-09-11T17:45:00+00:00", collected=10, stored_new=1, skipped=1
            ),
        ),
    )

    report = analyze(facts)

    assert report.blocking is False
    assert "no publication" in report.verdict.lower()
    assert any(finding.code == "sources-failing" for finding in report.findings)


def test_backlog_is_reported_while_articles_wait_for_the_llm() -> None:
    report = analyze(
        _facts(
            counts={"published": 20, "processed": 0, "new": 7, "skipped": 5, "error": 0},
            oldest_new_at=NOW - timedelta(hours=2),
        )
    )

    backlog = next(finding for finding in report.findings if finding.code == "backlog")
    assert backlog.severity == "info"
    assert "7 article(s)" in backlog.message


def test_disabled_sources_with_stale_errors_are_listed_but_not_counted() -> None:
    """D-019: a switched-off feed keeps its last error row but is not a failure."""
    facts = _facts(ignored_sources=("commlawblog", "iksmedia"))

    report = analyze(facts)

    text = render(report, facts)

    assert not any(finding.code == "sources-failing" for finding in report.findings)
    assert report.blocking is False
    assert "Failing sources" not in text
    assert "Disabled sources with stale errors (not counted): commlawblog, iksmedia" in text
