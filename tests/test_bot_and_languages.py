"""Tests for the M8 language/subscriber logic (no network, tmp database)."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

from telecom_news.bot import (
    BOT_OFFSET_KEY,
    LANGS,
    UI,
    AnswerCallback,
    EditKeyboard,
    SendText,
    describe_update,
    handle_update,
    poll_once,
)
from telecom_news.delivery.planner import plan_deliveries
from telecom_news.delivery.telegram import format_post
from telecom_news.models import Article
from telecom_news.processors.renditions import ensure_rendition
from telecom_news.storage import Database

NOW = datetime(2026, 9, 11, 18, 0, 0, tzinfo=timezone.utc)


def _db(tmp_path: Path) -> Database:
    return Database(tmp_path / "news.db")


def _message(update_id: int, chat_id: int, text: str, *, language_code: str = "en") -> dict:
    return {
        "update_id": update_id,
        "message": {
            "message_id": update_id,
            "chat": {"id": chat_id},
            "from": {"id": chat_id, "username": "tester", "language_code": language_code},
            "text": text,
        },
    }


def _callback(update_id: int, chat_id: int, data: str, *, message_id: int = 5) -> dict:
    return {
        "update_id": update_id,
        "callback_query": {
            "id": f"cb-{update_id}",
            "data": data,
            "from": {"id": chat_id, "language_code": "ru"},
            "message": {"message_id": message_id, "chat": {"id": chat_id}},
        },
    }


def _article(n: int, *, hours_ago: float = 1.0, status: str = "processed") -> Article:
    moment = NOW - timedelta(hours=hours_ago)
    article = Article(
        url=f"https://example.com/{n}",
        source_id="test-source",
        title=f"Title {n}",
        body=f"Body {n}",
        content_hash=f"hash-{n}",
        fetched_at=moment,
        published_at=moment,
        llm_result={"summary": f"Summary {n}", "summary_language": "ru"},
        status=status,
    )
    article.id = n
    return article


# --- language choice --------------------------------------------------------


def test_start_saves_locale_default_and_shows_both_languages(tmp_path: Path) -> None:
    db = _db(tmp_path)
    (action,) = handle_update(db, _message(1, 100, "/start", language_code="ru-RU"), now=NOW)

    assert isinstance(action, SendText)
    assert db.subscriber_languages(100) == ["ru"]
    keyboard = action.reply_markup
    assert keyboard is not None
    labels = json.dumps(keyboard, ensure_ascii=False)
    assert "Русский" in labels and "English" in labels and "Готово" in labels
    assert db.subscriber(100)["status"] == "active"


def test_start_defaults_to_english_for_non_russian_locale(tmp_path: Path) -> None:
    db = _db(tmp_path)
    handle_update(db, _message(1, 7, "/start", language_code="de"), now=NOW)
    assert db.subscriber_languages(7) == ["en"]


def test_toggle_adds_and_removes_languages_immediately(tmp_path: Path) -> None:
    db = _db(tmp_path)
    handle_update(db, _message(1, 100, "/start", language_code="ru"), now=NOW)

    actions = handle_update(db, _callback(2, 100, "lang:toggle:en"), now=NOW)
    assert db.subscriber_languages(100) == ["en", "ru"]
    assert any(isinstance(action, EditKeyboard) for action in actions)

    handle_update(db, _callback(3, 100, "lang:toggle:ru"), now=NOW)
    assert db.subscriber_languages(100) == ["en"]

    handle_update(db, _callback(4, 100, "lang:toggle:en"), now=NOW)
    assert db.subscriber_languages(100) == []


def test_done_without_languages_explains_that_nothing_will_arrive(tmp_path: Path) -> None:
    db = _db(tmp_path)
    handle_update(db, _message(1, 100, "/start", language_code="ru"), now=NOW)
    handle_update(db, _callback(2, 100, "lang:toggle:ru"), now=NOW)

    actions = handle_update(db, _callback(3, 100, "lang:done"), now=NOW)

    toast = next(action for action in actions if type(action).__name__ == "AnswerCallback")
    assert "не выбран" in toast.text
    assert any(isinstance(action, SendText) and action.reply_markup for action in actions)
    assert db.subscriber_languages(100) == []


def test_language_command_changes_selection_at_any_time(tmp_path: Path) -> None:
    db = _db(tmp_path)
    handle_update(db, _message(1, 100, "/start", language_code="ru"), now=NOW)
    handle_update(db, _callback(2, 100, "lang:toggle:en"), now=NOW)

    (action,) = handle_update(db, _message(3, 100, "/language"), now=NOW)

    assert isinstance(action, SendText)
    assert db.subscriber_languages(100) == ["en", "ru"]
    keyboard = json.dumps(action.reply_markup, ensure_ascii=False)
    assert keyboard.count("✅") == 2


def test_stop_and_status_commands(tmp_path: Path) -> None:
    db = _db(tmp_path)
    handle_update(db, _message(1, 100, "/start", language_code="ru"), now=NOW)

    (stopped,) = handle_update(db, _message(2, 100, "/stop"), now=NOW)
    assert "приостановлена" in stopped.text
    assert db.subscriber(100)["status"] == "stopped"

    (status,) = handle_update(db, _message(3, 100, "/status"), now=NOW)
    assert "приостановлена" in status.text

    handle_update(db, _message(4, 100, "/start"), now=NOW)
    assert db.subscriber(100)["status"] == "active"


def test_status_reports_selected_languages(tmp_path: Path) -> None:
    db = _db(tmp_path)
    handle_update(db, _message(1, 100, "/start", language_code="ru"), now=NOW)
    (action,) = handle_update(db, _message(2, 100, "/status"), now=NOW)

    assert "🇷🇺 Русский" in action.text


def test_unknown_text_returns_help_hint(tmp_path: Path) -> None:
    db = _db(tmp_path)
    handle_update(db, _message(1, 100, "/start", language_code="en"), now=NOW)
    (action,) = handle_update(db, _message(2, 100, "hello bot"), now=NOW)
    assert "/language" in action.text


def test_ui_texts_exist_for_every_supported_language() -> None:
    assert set(LANGS) == {"ru", "en"}
    for lang in LANGS:
        assert {"choose", "welcome", "status", "stopped", "help"} <= set(UI[lang])


# --- polling ----------------------------------------------------------------


class _FakeBotClient:
    def __init__(self, updates: list[dict]) -> None:
        self._updates = updates
        self.sent: list[tuple[str, str]] = []
        self.answered: list[str] = []
        self.edited: list[tuple[str, int]] = []
        self.offsets: list[int | None] = []

    def get_updates(self, offset, *, timeout: int = 25) -> list[dict]:
        self.offsets.append(offset)
        return list(self._updates)

    def send_message(self, chat_id: str, text: str, *, reply_markup=None) -> dict:
        self.sent.append((chat_id, text))
        return {"ok": True, "result": {"message_id": len(self.sent)}}

    def answer_callback_query(self, callback_query_id: str, *, text: str = "") -> None:
        self.answered.append(callback_query_id)

    def edit_message_reply_markup(self, chat_id, message_id, reply_markup) -> None:
        self.edited.append((chat_id, message_id))


def test_poll_once_handles_updates_and_persists_offset(tmp_path: Path) -> None:
    db = _db(tmp_path)
    client = _FakeBotClient([_message(10, 100, "/start"), _callback(11, 100, "lang:toggle:en")])

    stats = poll_once(db, client, now=NOW)  # type: ignore[arg-type]

    assert stats.updates == 2
    assert len(client.sent) == 1 and len(client.edited) == 1
    assert db.get_state(BOT_OFFSET_KEY) == "12"

    client.offsets.clear()
    poll_once(db, client, now=NOW)  # type: ignore[arg-type]
    assert client.offsets == [12]


def test_poll_once_logs_ignored_channel_posts(tmp_path: Path, caplog) -> None:
    """D-019: a command typed in the channel is dropped, but the log says so."""
    db = _db(tmp_path)
    channel_post = {
        "update_id": 30,
        "channel_post": {
            "chat": {"id": -100123, "title": "SMS Telecom News"},
            "text": "/start",
        },
    }
    client = _FakeBotClient([channel_post])

    with caplog.at_level(logging.INFO, logger="telecom_news.bot"):
        stats = poll_once(db, client, now=NOW)  # type: ignore[arg-type]

    assert stats.updates == 1 and stats.actions == []
    assert "channel post" in caplog.text and "private chat" in caplog.text
    assert db.get_state(BOT_OFFSET_KEY) == "31"


def test_describe_update_summarizes_each_update_kind() -> None:
    channel = {
        "update_id": 7,
        "channel_post": {"chat": {"id": -100123, "title": "SMS Telecom News"}, "text": "/start"},
    }
    line = describe_update(channel, [])

    assert "update 7" in line and "channel post" in line
    assert "SMS Telecom News" in line and "private chat" in line

    private = {"update_id": 8, "message": {"chat": {"id": 42}, "text": "/start"}}
    line = describe_update(private, [SendText(chat_id="42", text="hi")])
    assert "message '/start'" in line and "1 action(s)" in line

    callback = {
        "update_id": 9,
        "callback_query": {"data": "lang:toggle:en", "message": {"chat": {"id": 42}}},
    }
    line = describe_update(callback, [AnswerCallback(callback_query_id="1")])
    assert "lang:toggle:en" in line and "1 action(s)" in line

    assert "unsupported" in describe_update({"update_id": 10}, [])


# --- planner ----------------------------------------------------------------


def test_planner_sends_each_article_once_per_language() -> None:
    plans = plan_deliveries(
        [_article(1)],
        {"100": ["en", "ru"], "200": ["ru"]},
        set(),
        now=NOW,
        max_age_hours=24,
        max_per_user=10,
        max_attempts=3,
        attempts_of=lambda *_: 0,
    )

    assert {(plan.chat_id, plan.lang) for plan in plans} == {
        ("100", "en"),
        ("100", "ru"),
        ("200", "ru"),
    }


def test_planner_skips_already_sent_and_old_articles() -> None:
    sent = {("100", 1, "ru"), ("100", 1, "en")}
    plans = plan_deliveries(
        [_article(1), _article(2, hours_ago=48)],
        {"100": ["ru", "en"]},
        sent,
        now=NOW,
        max_age_hours=24,
        max_per_user=10,
        max_attempts=3,
    )

    assert plans == []


def test_planner_respects_per_user_limit_and_attempts() -> None:
    plans = plan_deliveries(
        [_article(1), _article(2), _article(3)],
        {"100": ["ru"]},
        set(),
        now=NOW,
        max_age_hours=24,
        max_per_user=2,
        max_attempts=3,
        attempts_of=lambda chat_id, article_id, lang: 3 if article_id == 2 else 0,
    )

    assert [plan.article.id for plan in plans] == [1, 3]


def test_planner_orders_by_article_id_and_ignores_subscribers_without_languages() -> None:
    plans = plan_deliveries(
        [_article(2), _article(1)],
        {"100": [], "200": ["ru"]},
        set(),
        now=NOW,
    )

    assert [(plan.chat_id, plan.article.id) for plan in plans] == [("200", 2), ("200", 1)]


# --- renditions and formatting ---------------------------------------------


class _FakeLLM:
    def __init__(self, replies: list[str]) -> None:
        self.replies = list(replies)
        self.prompts: list[list[dict[str, str]]] = []

    def chat(self, messages: list[dict[str, str]], **kwargs) -> str:
        self.prompts.append(messages)
        return self.replies.pop(0)


def test_ensure_rendition_caches_and_reuses(tmp_path: Path) -> None:
    db = _db(tmp_path)
    article = _article(1)
    db.upsert_by_hash(article)
    llm = _FakeLLM([json.dumps({"summary": "Русское саммари"})])

    first = ensure_rendition(db, llm, article, "ru", model_name="test-model")  # type: ignore[arg-type]
    second = ensure_rendition(db, llm, article, "ru")  # type: ignore[arg-type]

    assert first.cached is False and first.summary == "Русское саммари"
    assert second.cached is True and second.summary == "Русское саммари"
    assert len(llm.prompts) == 1
    assert db.rendition_languages(article.id or 0) == ["ru"]


def test_format_post_is_localized_and_can_show_a_flag() -> None:
    article = _article(1)

    russian = format_post(article, lang="ru")
    english = format_post(article, lang="en", summary="English summary", show_flag=True)

    assert "Категория" in russian and "Источник" in russian
    assert "Category" in english and "Source" in english
    assert "🇬🇧" in english and "Категория" not in english
    assert "English summary" in english


def test_article_status_round_trip_for_subscribers(tmp_path: Path) -> None:
    db = _db(tmp_path)
    db.upsert_subscriber("100", username="tester", ui_lang="ru", now=NOW)
    db.set_subscriber_languages("100", ["en", "ru"], now=NOW)
    db.record_delivery("100", 1, "ru", status="sent", message_id=42)
    db.record_delivery("100", 1, "en", status="failed", error="boom")

    assert db.subscribers_with_languages() == {"100": ["en", "ru"]}
    assert db.sent_deliveries() == {("100", 1, "ru")}
    assert db.failed_delivery_attempts(chat_id="100", article_id=1, lang="en") == 1

    db.record_delivery("100", 1, "en", status="sent", message_id=43)
    assert db.failed_delivery_attempts(chat_id="100", article_id=1, lang="en") == 0
    assert ("100", 1, "en") in db.sent_deliveries()

    db.set_subscriber_status("100", "blocked")
    assert db.subscribers_with_languages() == {}
