"""Prod-MVP: bot.lock PID/stale handling + subscriber summarize for diagnose."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

from telecom_news.diagnostics import (
    Facts,
    analyze,
    inspect_bot_lock,
    summarize_subscribers,
)
from telecom_news.storage.database import Database


def test_inspect_bot_lock_missing(tmp_path: Path) -> None:
    running, stale, detail = inspect_bot_lock(tmp_path / "bot.lock")
    assert running is False
    assert stale is False
    assert "no lock" in detail


def test_inspect_bot_lock_stale_empty(tmp_path: Path) -> None:
    lock = tmp_path / "bot.lock"
    lock.write_text("")
    running, stale, detail = inspect_bot_lock(lock)
    assert running is False
    assert stale is True
    assert "stale" in detail


def test_inspect_bot_lock_stale_dead_pid(tmp_path: Path) -> None:
    lock = tmp_path / "bot.lock"
    lock.write_text("999999")  # almost certainly not a live pid
    running, stale, detail = inspect_bot_lock(lock)
    assert running is False
    assert stale is True
    assert "999999" in detail


def test_inspect_bot_lock_live_pid(tmp_path: Path) -> None:
    lock = tmp_path / "bot.lock"
    lock.write_text(f"{os.getpid()}\n")
    running, stale, detail = inspect_bot_lock(lock)
    assert running is True
    assert stale is False
    assert str(os.getpid()) in detail


def test_summarize_subscribers_flags_bot_username() -> None:
    rows = [
        {
            "chat_id": "1288967298",
            "username": "sms_telecom_news_bot",
            "status": "active",
        },
        {"chat_id": "1", "username": "alice", "status": "active"},
        {"chat_id": "2", "username": "bob", "status": "stopped"},
    ]
    langs = {"1288967298": ["ru"], "1": ["en"]}
    active, without, suspicious = summarize_subscribers(rows, langs)
    assert active == 2
    assert without == 0
    assert any("sms_telecom_news_bot" in item for item in suspicious)


def test_summarize_subscribers_without_lang() -> None:
    rows = [{"chat_id": "9", "username": "x", "status": "active"}]
    active, without, suspicious = summarize_subscribers(rows, {})
    assert active == 0
    assert without == 1
    assert suspicious == ()


def test_analyze_flags_no_subscribers_and_bot_down() -> None:
    now = datetime(2026, 9, 12, 15, 0, tzinfo=timezone.utc)
    facts = Facts(
        now=now,
        db_exists=True,
        counts={"published": 1, "new": 0, "processed": 0, "skipped": 0, "error": 0},
        last_published_at=now,
        telegram_configured=True,
        llm_ok=True,
        telegram_ok=True,
        active_subscribers=0,
        bot_running=False,
        bot_lock_stale=False,
        log_path=Path("/tmp/x"),
        log_modified_at=now,
    )
    report = analyze(facts)
    codes = {finding.code for finding in report.findings}
    assert "no-subscribers" in codes
    assert "bot-down" in codes


def test_analyze_flags_suspicious_and_stale_lock() -> None:
    now = datetime(2026, 9, 12, 15, 0, tzinfo=timezone.utc)
    facts = Facts(
        now=now,
        db_exists=True,
        counts={"published": 1},
        last_published_at=now,
        telegram_configured=True,
        llm_ok=True,
        telegram_ok=True,
        active_subscribers=1,
        suspicious_subscribers=("sms_telecom_news_bot(chat=1)",),
        bot_running=False,
        bot_lock_stale=True,
        log_path=Path("/tmp/x"),
        log_modified_at=now,
    )
    report = analyze(facts)
    codes = {finding.code for finding in report.findings}
    assert "suspicious-subscriber" in codes
    assert "bot-lock-stale" in codes


def test_list_subscribers_and_cli_diagnose_surface(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("TELECOM_NEWS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "-100")
    db = Database(tmp_path / "news.db")
    db.upsert_subscriber("1288967298", username="sms_telecom_news_bot", ui_lang="en")
    db.set_subscriber_languages("1288967298", ["ru"])
    assert len(db.list_subscribers()) == 1

    (tmp_path / "bot.lock").write_text("")
    (tmp_path / "logs").mkdir()
    (tmp_path / "logs" / "pipeline.log").write_text(
        "===== 2026-09-12T15:00:00+00:00 =====\nexit_code=0\n"
    )

    from telecom_news import cli

    code = cli.main(["diagnose", "--offline"])
    out = capsys.readouterr().out
    assert "Subscribers:" in out
    assert "sms_telecom_news_bot" in out
    assert "Bot:" in out
    assert "STALE" in out or "stale" in out.lower()
    assert code in (0, 1)


def test_bot_removes_stale_lock_on_start(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("TELECOM_NEWS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    (tmp_path / "bot.lock").write_text("")  # stale empty

    from telecom_news.bot import BotRunStats

    def fake_poll_once(*_a, **_k):
        return BotRunStats(updates=0, actions=[], errors=0)

    class _FakeClient:
        def __init__(self, *a, **k):
            pass

    monkeypatch.setattr("telecom_news.delivery.telegram.TelegramClient", _FakeClient)
    monkeypatch.setattr("telecom_news.bot.poll_once", fake_poll_once)

    from telecom_news import cli

    code = cli.main(["bot", "--once"])
    out = capsys.readouterr().out + capsys.readouterr().err
    assert code == 0
    assert "stale" in out.lower()
    assert not (tmp_path / "bot.lock").exists()
