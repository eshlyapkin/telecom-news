"""Publish CLI tests with a temporary SQLite database."""

from __future__ import annotations

from pathlib import Path

from telecom_news.cli import _cmd_publish
from telecom_news.models import Article
from telecom_news.storage import Database


def _seed(path: Path, *, summary: str = "Summary") -> None:
    db = Database(path)
    db.upsert_by_hash(
        Article(
            url="https://example.com/a",
            source_id="test",
            title="Title",
            body="body",
            category="vendor",
            llm_result={"summary": summary, "summary_language": "ru"},
            status="processed",
        )
    )
    db.set_status(1, "processed")


def test_dry_run_does_not_need_credentials_or_change_status(tmp_path: Path, capsys) -> None:
    path = tmp_path / "news.db"
    _seed(path)
    assert _cmd_publish(None, True, db_path=path) == 0
    assert "Dry run" in capsys.readouterr().out
    assert Database(path).count_by_status()["processed"] == 1


def test_missing_credentials_is_usage_error(tmp_path: Path, monkeypatch, capsys) -> None:
    path = tmp_path / "news.db"
    _seed(path)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    assert _cmd_publish(None, False, db_path=path) == 2
    assert "TELEGRAM_BOT_TOKEN" in capsys.readouterr().err


def test_publish_marks_article_after_success(tmp_path: Path, monkeypatch, capsys) -> None:
    path = tmp_path / "news.db"
    _seed(path)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "chat")

    class FakeClient:
        def __init__(self, token: str) -> None:
            assert token == "token"

        def send_message(self, chat_id: str, text: str) -> dict:
            assert (chat_id, "<b>Title</b>") == ("chat", text.split("\n")[0])
            return {"ok": True}

    import telecom_news.delivery.telegram as telegram

    monkeypatch.setattr(telegram, "TelegramClient", FakeClient)
    assert _cmd_publish(None, False, db_path=path) == 0
    assert Database(path).count_by_status()["published"] == 1
    assert "published" in capsys.readouterr().out


def test_send_failure_leaves_article_processed(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "news.db"
    _seed(path)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "chat")

    class BrokenClient:
        def __init__(self, token: str) -> None:
            pass

        def send_message(self, chat_id: str, text: str) -> dict:
            from telecom_news.delivery.telegram import TelegramError

            raise TelegramError("temporary")

    import telecom_news.delivery.telegram as telegram

    monkeypatch.setattr(telegram, "TelegramClient", BrokenClient)
    assert _cmd_publish(None, False, db_path=path) == 1
    assert Database(path).count_by_status()["processed"] == 1


def test_missing_summary_becomes_error(tmp_path: Path) -> None:
    path = tmp_path / "news.db"
    _seed(path, summary="")
    assert _cmd_publish(None, True, db_path=path) == 0
    assert Database(path).count_by_status()["error"] == 1
