"""Subscriber bot (M8): language choice and subscription state.

The user connects to the bot, presses ``/start`` and picks the languages they
want to read. Every press is stored immediately, so the language can be changed
at any moment (``/language``) and several languages can be active at once — the
delivery layer then sends the same article once per selected language.

:func:`handle_update` is a pure-ish function: it reads one Telegram update,
writes subscriber state to SQLite and returns the actions the caller must
execute (send a message, answer a button press, edit a keyboard). That keeps the
Telegram API and the loop out of the logic and makes it testable without network.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from .delivery.telegram import TelegramClient, TelegramError
from .storage.database import Database

logger = logging.getLogger(__name__)

LANGS: tuple[str, ...] = ("ru", "en")
LANG_TITLES = {"ru": "🇷🇺 Русский", "en": "🇬🇧 English"}
BOT_OFFSET_KEY = "telegram_updates_offset"

UI: dict[str, dict[str, str]] = {
    "ru": {
        "choose": "Выберите языки, на которых хотите получать новости:",
        "welcome": (
            "👋 Привет! Это бот новостей об SMS и бизнес-сообщениях.\n\n"
            "Выберите языки — можно несколько сразу:"
        ),
        "saved": "Готово ✅\nВы получаете новости на языках: {langs}.",
        "empty": "Пока не выбран ни один язык — новости приходить не будут.",
        "status": (
            "Вы получаете новости на языках: {langs}.\nСменить — /language, отключиться — /stop."
        ),
        "stopped": "Рассылка приостановлена. Вернуться — /start.",
        "resumed": "Снова подписан ✅ Новости придут при следующем цикле.",
        "help": (
            "Команды:\n"
            "/start — подписаться и выбрать языки\n"
            "/language — изменить языки\n"
            "/status — что вы получаете\n"
            "/stop — приостановить рассылку\n"
            "/help — эта справка"
        ),
        "unknown": "Не понял сообщение. Доступно: /start, /language, /status, /stop, /help.",
    },
    "en": {
        "choose": "Choose the languages you want to receive news in:",
        "welcome": (
            "👋 Hi! This bot publishes SMS and business-messaging news.\n\n"
            "Pick one or more languages:"
        ),
        "saved": "Done ✅\nYou will receive news in: {langs}.",
        "empty": "No language selected yet — no news will be delivered.",
        "status": "You receive news in: {langs}.\nChange it with /language, pause with /stop.",
        "stopped": "Delivery paused. Send /start to resume.",
        "resumed": "Subscribed again ✅ News arrives in the next cycle.",
        "help": (
            "Commands:\n"
            "/start — subscribe and choose languages\n"
            "/language — change languages\n"
            "/status — current subscription\n"
            "/stop — pause delivery\n"
            "/help — this help"
        ),
        "unknown": "Unknown command. Available: /start, /language, /status, /stop, /help.",
    },
}

_LANGUAGE_COMMANDS = {"/start", "/language", "/lang", "/язык"}
_STATUS_COMMANDS = {"/status", "/статус"}
_STOP_COMMANDS = {"/stop", "/стоп"}
_HELP_COMMANDS = {"/help", "/помощь"}


@dataclass(frozen=True)
class SendText:
    """Send a message to a chat (optionally with an inline keyboard)."""

    chat_id: str
    text: str
    reply_markup: dict[str, Any] | None = None


@dataclass(frozen=True)
class AnswerCallback:
    """Acknowledge a button press."""

    callback_query_id: str
    text: str = ""


@dataclass(frozen=True)
class EditKeyboard:
    """Replace the inline keyboard of an existing message."""

    chat_id: str
    message_id: int
    reply_markup: dict[str, Any]


Action = SendText | AnswerCallback | EditKeyboard


def language_keyboard(selected: list[str]) -> dict[str, Any]:
    """Inline keyboard with one toggle button per language plus 'done'."""
    rows: list[list[dict[str, str]]] = []
    for lang in LANGS:
        mark = "✅" if lang in selected else "⬜"
        rows.append(
            [{"text": f"{LANG_TITLES[lang]} {mark}", "callback_data": f"lang:toggle:{lang}"}]
        )
    rows.append([{"text": "Готово / Done", "callback_data": "lang:done"}])
    return {"inline_keyboard": rows}


def _ui_lang(language_code: str | None, saved: str | None) -> str:
    """Interface language: saved choice first, then Telegram locale, else ru."""
    if saved in LANGS:
        return str(saved)
    if language_code and str(language_code).lower().startswith("ru"):
        return "ru"
    if language_code:
        return "en"
    return "ru"


def _langs_text(langs: list[str]) -> str:
    if not langs:
        return "—"
    return ", ".join(LANG_TITLES.get(lang, lang) for lang in langs)


def _default_selection(language_code: str | None) -> list[str]:
    """Preselection for a first-time user: their Telegram locale, else English."""
    if language_code and str(language_code).lower().startswith("ru"):
        return ["ru"]
    return ["en"]


def handle_update(
    db: Database, update: dict[str, Any], *, now: datetime | None = None
) -> list[Action]:
    """Process one Telegram update; store state; return actions to execute."""
    moment = now or datetime.now(timezone.utc)
    callback = update.get("callback_query")
    if isinstance(callback, dict):
        return _handle_callback(db, callback, now=moment)
    message = update.get("message")
    if isinstance(message, dict):
        return _handle_message(db, message, now=moment)
    return []


def _user_identity(payload: dict[str, Any]) -> tuple[str, str | None]:
    chat = payload.get("chat") or {}
    sender = payload.get("from") or {}
    chat_id = str(chat.get("id") or sender.get("id") or "")
    username = sender.get("username")
    return chat_id, str(username) if username else None


def _handle_message(db: Database, message: dict[str, Any], *, now: datetime) -> list[Action]:
    chat_id, username = _user_identity(message)
    if not chat_id:
        return []
    language_code = (message.get("from") or {}).get("language_code")
    text = str(message.get("text") or "").strip().lower()
    command = text.split()[0] if text else ""
    existing = db.subscriber(chat_id)
    ui_lang = _ui_lang(language_code, (existing or {}).get("ui_lang"))
    texts = UI[ui_lang]
    db.upsert_subscriber(chat_id, username=username, ui_lang=ui_lang, now=now)

    if command in _LANGUAGE_COMMANDS:
        selected = db.subscriber_languages(chat_id)
        if command == "/start" and not selected:
            # First contact: save the locale-based default so news start flowing
            # immediately, and show the keyboard so it can be changed at once.
            selected = db.set_subscriber_languages(
                chat_id, _default_selection(language_code), now=now
            )
        if command == "/start" and (existing or {}).get("status") == "stopped":
            db.set_subscriber_status(chat_id, "active")
        intro = texts["welcome"] if command == "/start" else texts["choose"]
        return [
            SendText(
                chat_id=chat_id,
                text=f"{intro}\n\n{texts['status'].format(langs=_langs_text(selected))}",
                reply_markup=language_keyboard(selected),
            )
        ]
    if command in _STATUS_COMMANDS:
        selected = db.subscriber_languages(chat_id)
        current = db.subscriber(chat_id) or {}
        if current.get("status") == "stopped":
            body = texts["stopped"]
        elif not selected:
            body = texts["empty"]
        else:
            body = texts["status"].format(langs=_langs_text(selected))
        return [SendText(chat_id=chat_id, text=body)]
    if command in _STOP_COMMANDS:
        if existing:
            db.set_subscriber_status(chat_id, "stopped")
        return [SendText(chat_id=chat_id, text=texts["stopped"])]
    if command in _HELP_COMMANDS:
        return [SendText(chat_id=chat_id, text=texts["help"])]
    selected = db.subscriber_languages(chat_id)
    return [
        SendText(
            chat_id=chat_id,
            text=f"{texts['unknown']}\n\n{texts['choose']}",
            reply_markup=language_keyboard(selected) if not selected else None,
        )
    ]


def _handle_callback(db: Database, callback: dict[str, Any], *, now: datetime) -> list[Action]:
    callback_id = str(callback.get("id") or "")
    message = callback.get("message") or {}
    # Who pressed the button is callback["from"]. message["from"] is the bot.
    presser = callback.get("from") if isinstance(callback.get("from"), dict) else {}
    chat = message.get("chat") if isinstance(message.get("chat"), dict) else {}
    chat_id = str(chat.get("id") or presser.get("id") or "")
    raw_username = presser.get("username")
    username = str(raw_username) if raw_username else None
    if not chat_id:
        return []
    message_id = int(message.get("message_id") or 0)
    existing = db.subscriber(chat_id)
    ui_lang = _ui_lang(presser.get("language_code"), (existing or {}).get("ui_lang"))
    texts = UI[ui_lang]
    db.upsert_subscriber(chat_id, username=username, ui_lang=ui_lang, now=now)

    action = str(callback.get("data") or "")
    parts = action.split(":")
    if len(parts) == 3 and parts[0] == "lang" and parts[1] == "toggle":
        lang = parts[2]
        if lang not in LANGS:
            return [AnswerCallback(callback_query_id=callback_id, text="Unsupported language")]
        selected = db.toggle_subscriber_language(chat_id, lang, now=now)
        actions: list[Action] = [AnswerCallback(callback_query_id=callback_id)]
        if selected or True:
            actions.append(
                EditKeyboard(
                    chat_id=chat_id,
                    message_id=message_id,
                    reply_markup=language_keyboard(selected),
                )
            )
        return actions
    if len(parts) >= 2 and parts[0] == "lang" and parts[1] == "done":
        selected = db.subscriber_languages(chat_id)
        if not selected:
            return [
                AnswerCallback(callback_query_id=callback_id, text=texts["empty"]),
                SendText(
                    chat_id=chat_id,
                    text=texts["choose"],
                    reply_markup=language_keyboard(selected),
                ),
            ]
        return [
            AnswerCallback(callback_query_id=callback_id),
            SendText(chat_id=chat_id, text=texts["saved"].format(langs=_langs_text(selected))),
        ]
    return [AnswerCallback(callback_query_id=callback_id)]


def execute_actions(client: TelegramClient, actions: list[Action]) -> None:
    """Perform the actions returned by :func:`handle_update` via the Bot API."""
    for action in actions:
        if isinstance(action, SendText):
            client.send_message(action.chat_id, action.text, reply_markup=action.reply_markup)
        elif isinstance(action, AnswerCallback):
            client.answer_callback_query(action.callback_query_id, text=action.text)
        elif isinstance(action, EditKeyboard):
            client.edit_message_reply_markup(action.chat_id, action.message_id, action.reply_markup)


@dataclass
class BotRunStats:
    """Counters of one polling session (for logs and tests)."""

    updates: int = 0
    errors: int = 0
    iterations: int = 0
    actions: list[str] = field(default_factory=list)


def describe_update(update: dict[str, Any], actions: list[Action]) -> str:
    """One-line summary of a Telegram update for the bot log (D-019).

    The bot is a member of the news channel, so it also receives ``channel_post``
    updates there. Only private messages and button presses produce actions:
    a command typed in the channel is consumed and dropped, and without this log
    line that looks exactly like "the bot is silent".
    """
    update_id = update.get("update_id")
    prefix = f"update {update_id}" if update_id else "update"
    channel_post = update.get("channel_post")
    if isinstance(channel_post, dict):
        chat = channel_post.get("chat") or {}
        where = chat.get("title") or chat.get("username") or chat.get("id") or "?"
        return (
            f"{prefix}: channel post in {where!r} ignored — subscriptions live in the "
            "private chat (open the bot and press /start)"
        )
    callback = update.get("callback_query")
    if isinstance(callback, dict):
        data = str(callback.get("data") or "")
        chat = (callback.get("message") or {}).get("chat") or {}
        return f"{prefix}: button {data!r} in chat {chat.get('id')} → {len(actions)} action(s)"
    message = update.get("message")
    if isinstance(message, dict):
        chat = message.get("chat") or {}
        text = str(message.get("text") or "").strip()
        command = text.split()[0] if text else "(no text)"
        return f"{prefix}: message {command!r} in chat {chat.get('id')} → {len(actions)} action(s)"
    return f"{prefix}: unsupported update type ignored"


def poll_once(
    db: Database,
    client: TelegramClient,
    *,
    poll_timeout: int = 25,
    now: datetime | None = None,
) -> BotRunStats:
    """One ``getUpdates`` round: handle commands, save the offset."""
    stats = BotRunStats()
    stored = db.get_state(BOT_OFFSET_KEY)
    offset = int(stored) if stored else None
    updates = client.get_updates(offset, timeout=poll_timeout)
    stats.updates = len(updates)
    for update in updates:
        update_id = int(update.get("update_id") or 0)
        actions = handle_update(db, update, now=now)
        execute_actions(client, actions)
        logger.info("%s", describe_update(update, actions))
        stats.actions.extend(type(action).__name__ for action in actions)
        if update_id:
            db.set_state(BOT_OFFSET_KEY, str(update_id + 1))
    return stats


def run_bot(
    db: Database,
    client: TelegramClient,
    *,
    poll_timeout: int = 25,
    max_iterations: int | None = None,
    sleep: Any = time.sleep,
) -> BotRunStats:
    """Long-poll loop for commands. ``max_iterations=1`` is a single pass."""
    total = BotRunStats()
    iteration = 0
    while max_iterations is None or iteration < max_iterations:
        iteration += 1
        total.iterations = iteration
        try:
            stats = poll_once(db, client, poll_timeout=poll_timeout)
        except TelegramError as exc:
            total.errors += 1
            logger.warning("bot polling failed: %s", exc)
            sleep(5)
            continue
        total.updates += stats.updates
        total.actions.extend(stats.actions)
    return total
