"""Subscriber fan-out through the CLI: languages, caching, retries (no network)."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from telecom_news import cli
from telecom_news.models import Article
from telecom_news.storage import Database

NOW = datetime.now(timezone.utc)


def _seed(tmp_path: Path, *, langs=("ru", "en")) -> Database:
    db = Database(tmp_path / "news.db")
    article = Article(
        url="https://example.com/a",
        source_id="test",
        title="A2P messaging deal",
        body="body",
        category="vendor",
        fetched_at=NOW - timedelta(hours=1),
        llm_result={"summary": "Русское саммари", "summary_language": "ru"},
    )
    db.upsert_by_hash(article)
    db.set_status(1, "processed")
    db.upsert_subscriber("100", username="tester", ui_lang="ru")
    db.set_subscriber_languages("100", list(langs))
    return db


class _FakeTelegram:
    """Records outgoing messages; fails on demand."""

    fail_with: Exception | None = None
    sent: list[tuple[str, str]] = []

    def __init__(self, token: str, **kwargs) -> None:
        assert token == "token"

    def send_message(self, chat_id: str, text: str, *, reply_markup=None) -> dict:
        type(self).sent.append((chat_id, text))
        if type(self).fail_with is not None:
            raise type(self).fail_with
        return {"ok": True, "result": {"message_id": 77}}


class _FakeLLM:
    def __init__(self, base_url: str = "", model: str = "", **kwargs) -> None:
        pass

    def ensure_model(self) -> str:
        return "fake-model"

    def chat(self, messages, **kwargs) -> str:
        prompt = messages[1]["content"]
        if "English" in messages[0]["content"]:
            return json.dumps({"summary": "English summary"})
        assert "Текст" in prompt or "Text" in prompt or prompt
        return json.dumps({"summary": "Русское саммари"})


def _patch(monkeypatch) -> None:
    import telecom_news.delivery.telegram as telegram
    import telecom_news.llm.client as llm_client

    _FakeTelegram.sent = []
    _FakeTelegram.fail_with = None
    monkeypatch.setattr(telegram, "TelegramClient", _FakeTelegram)
    monkeypatch.setattr(llm_client, "LLMClient", _FakeLLM)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token")


def test_deliver_sends_one_message_per_language(tmp_path: Path, monkeypatch, capsys) -> None:
    path = tmp_path / "news.db"
    db = _seed(tmp_path)
    _patch(monkeypatch)

    code = cli._cmd_deliver(None, False, db_path=path)
    out = capsys.readouterr().out

    assert code == 0
    assert len(_FakeTelegram.sent) == 2
    texts = [text for _, text in _FakeTelegram.sent]
    assert any("Русское саммари" in text and "Категория" in text for text in texts)
    assert any("English summary" in text and "Category" in text for text in texts)
    assert "2 delivered" in out
    # both languages are cached now, so a second run sends nothing
    assert db.sent_deliveries() == {("100", 1, "ru"), ("100", 1, "en")}

    assert cli._cmd_deliver(None, False, db_path=path) == 0
    assert len(_FakeTelegram.sent) == 2
    assert "Nothing to deliver" in capsys.readouterr().out


def test_deliver_dry_run_previews_without_sending(tmp_path: Path, monkeypatch, capsys) -> None:
    path = tmp_path / "news.db"
    _seed(tmp_path)
    _patch(monkeypatch)

    code = cli._cmd_deliver(None, True, db_path=path)
    out = capsys.readouterr().out

    assert code == 0
    assert _FakeTelegram.sent == []
    assert "article=1" in out and "planned" in out


def test_deliver_records_failure_and_retries_not_more_than_allowed(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    path = tmp_path / "news.db"
    db = _seed(tmp_path, langs=("ru",))
    _patch(monkeypatch)
    from telecom_news.delivery.telegram import TelegramError

    _FakeTelegram.fail_with = TelegramError("temporary", retryable=True)

    assert cli._cmd_deliver(None, False, db_path=path) == 0
    assert db.failed_delivery_attempts(chat_id="100", article_id=1, lang="ru") == 1
    capsys.readouterr()

    # Retries continue until SUBSCRIBER_MAX_ATTEMPTS is reached, then stop.
    for _ in range(2):
        cli._cmd_deliver(None, False, db_path=path)
    attempts = db.failed_delivery_attempts(chat_id="100", article_id=1, lang="ru")
    assert attempts == 3

    _FakeTelegram.sent = []
    assert cli._cmd_deliver(None, False, db_path=path) == 0
    assert _FakeTelegram.sent == []
    assert "Nothing to deliver" in capsys.readouterr().out


def test_deliver_blocks_a_user_who_blocked_the_bot(tmp_path: Path, monkeypatch, capsys) -> None:
    path = tmp_path / "news.db"
    db = _seed(tmp_path, langs=("ru",))
    _patch(monkeypatch)
    from telecom_news.delivery.telegram import TelegramError

    _FakeTelegram.fail_with = TelegramError(
        "Telegram API error: Forbidden: bot was blocked by the user", status_code=403
    )

    assert cli._cmd_deliver(None, False, db_path=path) == 0
    assert db.subscriber("100")["status"] == "blocked"
    assert db.subscribers_with_languages() == {}
    assert "blocked" in capsys.readouterr().out


def test_deliver_without_subscribers_is_a_noop(tmp_path: Path, monkeypatch, capsys) -> None:
    path = tmp_path / "news.db"
    Database(path).count_by_status()
    _patch(monkeypatch)

    assert cli._cmd_deliver(None, False, db_path=path) == 0
    assert "No active subscribers" in capsys.readouterr().out
