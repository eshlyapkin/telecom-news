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
        def __init__(self, token: str, **kwargs) -> None:
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
        def __init__(self, token: str, **kwargs) -> None:
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


def test_publish_sends_each_language_to_its_own_channel(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    path = tmp_path / "news.db"
    db = Database(path)
    db.upsert_by_hash(
        Article(
            url="https://example.com/a",
            source_id="test",
            title="A2P messaging deal",
            body="body",
            category="vendor",
            llm_result={"summary": "Русское саммари", "summary_language": "ru"},
            status="processed",
        )
    )
    db.set_status(1, "processed")
    db.save_rendition(1, "ru", title="A2P messaging deal", summary="Русское саммари")
    db.save_rendition(1, "en", title="A2P messaging deal", summary="English summary")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "-100")
    monkeypatch.setenv("TELEGRAM_CHAT_ID_EN", "-200")
    monkeypatch.setenv("TELECOM_NEWS_TARGET_LANGS", "ru,en")

    class FakeClient:
        sent: list[tuple[str, str]] = []

        def __init__(self, token: str, **kwargs) -> None:
            pass

        def send_message(self, chat_id: str, text: str, *, reply_markup=None) -> dict:
            type(self).sent.append((chat_id, text))
            return {"ok": True, "result": {"message_id": len(type(self).sent)}}

    import telecom_news.delivery.telegram as telegram

    monkeypatch.setattr(telegram, "TelegramClient", FakeClient)
    assert _cmd_publish(None, False, db_path=path) == 0

    assert [chat for chat, _ in FakeClient.sent] == ["-100", "-200"]
    russian, english = FakeClient.sent[0][1], FakeClient.sent[1][1]
    assert "Русское саммари" in russian and "Категория" in russian
    assert "English summary" in english and "Category" in english
    assert "🇷🇺" in russian and "🇬🇧" in english
    assert Database(path).count_by_status()["published"] == 1
    assert Database(path).sent_deliveries() == {("-100", 1, "ru"), ("-200", 1, "en")}

    # Nothing is sent twice on a rerun, and a half-finished article is completed.
    assert _cmd_publish(None, False, db_path=path) == 0
    assert len(FakeClient.sent) == 2


def test_pre_m8_published_rows_are_not_reposted(tmp_path: Path, monkeypatch, capsys) -> None:
    """A database from before M8 has published rows without delivery records.

    Re-sending them duplicates old posts in the channel (the user saw 10 of the
    23 legacy rows re-posted on 2026-09-11), so publish records them as sent.
    """
    path = tmp_path / "news.db"
    _seed(path)
    db = Database(path)
    db.set_status(1, "processed")
    db.mark_published(1)  # published by an older version: no `deliveries` row

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "chat")
    sends: list[tuple[str, str]] = []

    class FakeClient:
        def __init__(self, token: str, **kwargs) -> None:
            pass

        def send_message(self, chat_id: str, text: str) -> dict:
            sends.append((chat_id, text))
            return {"ok": True, "result": {"message_id": 1}}

    import telecom_news.delivery.telegram as telegram

    monkeypatch.setattr(telegram, "TelegramClient", FakeClient)
    assert _cmd_publish(None, False, db_path=path) == 0

    assert sends == [], "a pre-M8 published article must not be sent again"
    out = capsys.readouterr().out
    assert "already published before delivery tracking" in out
    assert db.sent_deliveries() == {("chat", 1, "ru")}
    assert db.count_by_status()["published"] == 1


# --- publication caps and freshness window (D-020) --------------------------


def _seed_many(path: Path, count: int, *, hours_ago: float = 1.0) -> None:
    from datetime import datetime, timedelta, timezone

    db = Database(path)
    moment = datetime.now(timezone.utc) - timedelta(hours=hours_ago)
    for n in range(1, count + 1):
        db.upsert_by_hash(
            Article(
                url=f"https://example.com/{n}",
                source_id="test",
                title=f"Title {n}",
                body="body",
                category="vendor",
                content_hash=f"hash-{n:04d}",
                published_at=moment,
                fetched_at=moment,
                llm_result={"summary": f"Summary {n}", "summary_language": "ru"},
                status="processed",
            )
        )
        db.set_status(n, "processed")


def _fake_client(monkeypatch) -> list[tuple[str, str]]:
    sent: list[tuple[str, str]] = []

    class FakeClient:
        def __init__(self, token: str, **kwargs) -> None:
            pass

        def send_message(self, chat_id: str, text: str, **kwargs) -> dict:
            sent.append((chat_id, text))
            return {"ok": True, "result": {"message_id": len(sent)}}

    import telecom_news.delivery.telegram as telegram

    monkeypatch.setattr(telegram, "TelegramClient", FakeClient)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "chat")
    return sent


def test_publish_stops_at_the_configured_cap(tmp_path: Path, monkeypatch, capsys) -> None:
    """One cycle must not flood the channel: PUBLISH_MAX_PER_CYCLE bounds it."""
    path = tmp_path / "news.db"
    _seed_many(path, 5)
    sent = _fake_client(monkeypatch)
    monkeypatch.setenv("PUBLISH_MAX_PER_CYCLE", "2")

    assert _cmd_publish(None, False, db_path=path) == 0

    assert len(sent) == 2
    out = capsys.readouterr().out
    assert "Cap reached: at most 2 article(s) per cycle" in out
    counts = Database(path).count_by_status()
    assert (counts["published"], counts["processed"]) == (2, 3)


def test_publish_cap_zero_means_no_cap(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "news.db"
    _seed_many(path, 4)
    sent = _fake_client(monkeypatch)
    monkeypatch.setenv("PUBLISH_MAX_PER_CYCLE", "0")

    assert _cmd_publish(None, False, db_path=path) == 0
    assert len(sent) == 4


def test_publish_holds_back_articles_older_than_the_window(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    """Processed rows from a stale backlog are not posted as fresh news."""
    path = tmp_path / "news.db"
    _seed_many(path, 2, hours_ago=200.0)
    sent = _fake_client(monkeypatch)

    assert _cmd_publish(None, False, db_path=path) == 0

    assert sent == []
    out = capsys.readouterr().out
    assert "2 processed article(s) are older than 48 h and stay unpublished" in out
    assert Database(path).count_by_status()["processed"] == 2


def test_publish_window_can_be_disabled_per_run(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "news.db"
    _seed_many(path, 2, hours_ago=200.0)
    sent = _fake_client(monkeypatch)

    assert _cmd_publish(None, False, db_path=path, max_age_hours=0) == 0
    assert len(sent) == 2


def test_publish_still_sends_an_undated_article(tmp_path: Path, monkeypatch) -> None:
    """No timestamp = no known age, so the window keeps the row (as collect does)."""
    path = tmp_path / "news.db"
    _seed_many(path, 1)
    db = Database(path)
    db.upsert_by_hash(
        Article(
            url="https://example.com/undated",
            source_id="test",
            title="Undated",
            body="body",
            content_hash="hash-undated",
            llm_result={"summary": "Summary", "summary_language": "ru"},
            status="processed",
        )
    )
    db.set_status(2, "processed")
    sent = _fake_client(monkeypatch)

    assert _cmd_publish(None, False, db_path=path) == 0
    assert len(sent) == 2


def test_publish_rejects_a_negative_window(tmp_path: Path, capsys) -> None:
    path = tmp_path / "news.db"
    _seed_many(path, 1)
    assert _cmd_publish(None, True, db_path=path, max_age_hours=-1) == 2
    assert "--max-age-hours must be >= 0" in capsys.readouterr().err
