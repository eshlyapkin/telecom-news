"""Tests for the `diagnose` CLI command (tmp database + synthetic scheduler log)."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from telecom_news import cli
from telecom_news.models import Article
from telecom_news.storage import Database

NOW = datetime(2026, 9, 11, 18, 0, 0, tzinfo=timezone.utc)


def _article(n: int, **overrides) -> Article:
    base = {
        "url": f"https://example.com/news/{n}",
        "source_id": "test-source",
        "title": f"Title {n}",
        "body": f"Body {n}",
        "content_hash": f"hash-{n:04d}",
        "fetched_at": NOW - timedelta(hours=20),
    }
    base.update(overrides)
    return Article(**base)


def _log_text(*blocks: tuple[str, list[str]]) -> str:
    lines: list[str] = []
    for started, body in blocks:
        lines.append(f"===== {started} =====")
        lines.extend(body)
        lines.append("exit_code=0")
    return "\n".join(lines) + "\n"


def _write_log(tmp_path: Path, text: str) -> Path:
    logs = tmp_path / "logs"
    logs.mkdir(exist_ok=True)
    path = logs / "pipeline.log"
    path.write_text(text)
    return path


def _set_credentials(monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "-100123")


def test_diagnose_explains_a_quiet_channel(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("TELECOM_NEWS_DATA_DIR", str(tmp_path))
    _set_credentials(monkeypatch)
    db = Database(tmp_path / "news.db")
    db.upsert_by_hash(_article(1))
    db.save_processing_result(
        1, relevance="relevant", category="vendor", llm_result={"summary": "s"}, status="processed"
    )
    assert db.mark_published(1, published_at=NOW - timedelta(hours=20)) is True
    db.upsert_by_hash(_article(2))
    db.save_processing_result(
        2, relevance="irrelevant", category=None, llm_result={}, status="skipped"
    )
    _write_log(
        tmp_path,
        _log_text(
            (
                "2026-09-11T17:30:00+00:00",
                [
                    "Collected 10 article(s) from 'cnews-telecom':",
                    "Stored 0 new, skipped 10 duplicate(s).",
                ],
            ),
            (
                "2026-09-11T17:45:00+00:00",
                [
                    "Collected 10 article(s) from 'content-review':",
                    "Stored 0 new, skipped 10 duplicate(s).",
                    "Nothing to process (no articles with status 'new').",
                    "Nothing to publish (no articles with status 'processed').",
                ],
            ),
        ),
    )

    code = cli._cmd_diagnose(offline=True, now=NOW)
    out = capsys.readouterr().out

    assert code == 0
    assert "published=1" in out
    assert "Last publication: 2026-09-10T22:00:00+00:00" in out
    assert "no new articles" in out
    assert "DIAGNOSIS:" in out


def test_diagnose_blocks_when_lm_studio_is_down(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("TELECOM_NEWS_DATA_DIR", str(tmp_path))
    db = Database(tmp_path / "news.db")
    db.upsert_by_hash(_article(1))
    _write_log(tmp_path, _log_text(("2026-09-11T17:45:00+00:00", ["Collected 0 article(s):"])))
    monkeypatch.setattr(
        "telecom_news.diagnostics.probe_llm",
        lambda base_url, **kwargs: (False, "ConnectError at http://localhost:1234/v1/models"),
    )

    code = cli._cmd_diagnose(now=NOW)
    out = capsys.readouterr().out

    assert code == 1
    assert "LM Studio" in out
    assert "status 'new'" in out


def test_diagnose_reports_a_missing_database(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("TELECOM_NEWS_DATA_DIR", str(tmp_path))

    code = cli._cmd_diagnose(offline=True, now=NOW)
    out = capsys.readouterr().out

    assert code == 1
    assert "No database" in out


def test_diagnose_detects_a_stopped_scheduler(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("TELECOM_NEWS_DATA_DIR", str(tmp_path))
    _set_credentials(monkeypatch)
    Database(tmp_path / "news.db").count_by_status()
    log_path = _write_log(
        tmp_path, _log_text(("2026-09-11T06:00:00+00:00", ["Collected 0 article(s):"]))
    )
    stale = (NOW - timedelta(hours=9)).timestamp()
    import os

    os.utime(log_path, (stale, stale))

    code = cli._cmd_diagnose(offline=True, now=NOW)
    out = capsys.readouterr().out

    assert code == 1
    assert "scheduler log has not changed" in out


def test_diagnose_json_output_is_machine_readable(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("TELECOM_NEWS_DATA_DIR", str(tmp_path))
    _set_credentials(monkeypatch)
    Database(tmp_path / "news.db").count_by_status()
    _write_log(tmp_path, _log_text(("2026-09-11T17:45:00+00:00", ["Collected 0 article(s):"])))

    code = cli._cmd_diagnose(offline=True, as_json=True, now=NOW)
    payload = json.loads(capsys.readouterr().out)

    assert code == 0
    assert payload["blocking"] is False
    assert payload["findings"][0]["code"] == "never-published"


def test_diagnose_rejects_bad_runs_argument(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("TELECOM_NEWS_DATA_DIR", str(tmp_path))

    assert cli._cmd_diagnose(runs=0, offline=True) == 2
    assert "--runs must be >= 1" in capsys.readouterr().err


def test_diagnose_reports_articles_parked_after_retries(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    monkeypatch.setenv("TELECOM_NEWS_DATA_DIR", str(tmp_path))
    _set_credentials(monkeypatch)
    db = Database(tmp_path / "news.db")
    db.upsert_by_hash(_article(1))
    for _ in range(3):
        db.mark_error(1)
    _write_log(tmp_path, _log_text(("2026-09-11T17:45:00+00:00", ["Collected 0 article(s):"])))

    code = cli._cmd_diagnose(offline=True, now=NOW)
    out = capsys.readouterr().out

    assert code == 0
    assert "Parked errors (retries exhausted): 1" in out
    assert "recover --max-attempts 0" in out
