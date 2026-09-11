"""Telegram Bot API delivery for prepared articles."""

from __future__ import annotations

import html
import logging
import random
import time
from typing import Any

import httpx

from ..models import Article

logger = logging.getLogger(__name__)


class TelegramError(RuntimeError):
    """Telegram rejected a request or the API could not be reached."""

    def __init__(
        self, message: str, *, retryable: bool = False, status_code: int | None = None
    ) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.status_code = status_code

    @property
    def blocked_by_user(self) -> bool:
        """True when Telegram says the user blocked the bot (403 / deactivated)."""
        if self.status_code == 403:
            return True
        text = str(self).lower()
        return "blocked by the user" in text or "user is deactivated" in text


def _escape(value: str | None) -> str:
    return html.escape(value or "", quote=True)


# Localized post labels (M8). A post is written in the language of its
# rendition, so the footer must not stay Russian for an English post.
POST_LABELS: dict[str, dict[str, str]] = {
    "ru": {"category": "Категория", "source": "Источник", "flag": "🇷🇺"},
    "en": {"category": "Category", "source": "Source", "flag": "🇬🇧"},
}


def format_post(
    article: Article,
    *,
    lang: str | None = None,
    summary: str | None = None,
    show_flag: bool = False,
) -> str:
    """Format one article as an HTML Telegram message.

    ``lang`` selects the localized labels and, when ``show_flag`` is true, adds a
    country flag so a subscriber who reads two languages can tell the versions
    apart. ``summary`` overrides the text used for the body (a cached rendition);
    without it the summary from ``llm_result`` is used, which keeps the previous
    single-language behaviour intact.
    """
    if not article.llm_result and summary is None:
        raise TelegramError(f"article id={article.id} has no LLM result")
    if summary is None:
        llm_result = article.llm_result or {}
        summary = llm_result.get("summary")
        if lang is None:
            lang = str(llm_result.get("summary_language") or article.language)
    effective_lang = (lang or "ru").lower()
    labels = POST_LABELS.get(effective_lang, POST_LABELS["ru"])
    if not isinstance(summary, str) or not summary.strip():
        raise TelegramError(f"article id={article.id} has no summary")
    category = article.category or (article.llm_result or {}).get("category") or "news"
    header = f"{labels['flag']} " if show_flag else ""
    return (
        f"{header}<b>{_escape(article.title)}</b>\n\n"
        f"{_escape(summary.strip())}\n\n"
        f"<b>{labels['category']}:</b> {_escape(str(category))}\n"
        f'<a href="{_escape(article.url)}">{labels["source"]}</a>'
    )


class TelegramClient:
    """Small retrying client for Telegram's ``sendMessage`` endpoint."""

    def __init__(
        self,
        token: str,
        *,
        timeout: float = 30.0,
        max_retries: int = 3,
        min_interval: float = 0.0,
        transport: httpx.BaseTransport | None = None,
        base_url: str = "https://api.telegram.org",
        sleep: Any = time.sleep,
    ) -> None:
        if not token:
            raise ValueError("Telegram bot token must not be empty")
        self.token = token
        self.timeout = timeout
        self.max_retries = max(0, max_retries)
        self.min_interval = max(0.0, min_interval)
        self._last_request_at: float | None = None
        self.base_url = base_url.rstrip("/")
        self._transport = transport
        self._sleep = sleep

    @property
    def endpoint(self) -> str:
        return f"{self.base_url}/bot{self.token}/sendMessage"

    def send_message(
        self, chat_id: str, text: str, *, reply_markup: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Send one HTML message, retrying transient API failures."""
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "link_preview_options": {"is_disabled": False},
        }
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        with httpx.Client(timeout=self.timeout, transport=self._transport) as client:
            for attempt in range(self.max_retries + 1):
                self._wait_for_slot()
                try:
                    response = client.post(self.endpoint, json=payload)
                except httpx.HTTPError as exc:
                    if attempt >= self.max_retries:
                        raise TelegramError(
                            f"Telegram request failed: {exc}", retryable=True
                        ) from exc
                    self._sleep(self._backoff(attempt))
                    continue
                if response.status_code == 429:
                    if attempt >= self.max_retries:
                        raise TelegramError("Telegram rate limit persisted", retryable=True)
                    self._sleep(self._retry_after(response, attempt))
                    continue
                if response.status_code >= 500:
                    if attempt >= self.max_retries:
                        raise TelegramError(
                            f"Telegram server error ({response.status_code})", retryable=True
                        )
                    self._sleep(self._backoff(attempt))
                    continue
                try:
                    data = response.json()
                except ValueError as exc:
                    raise TelegramError("Telegram returned invalid JSON") from exc
                if response.status_code >= 400 or not data.get("ok"):
                    description = data.get("description", response.text)
                    raise TelegramError(
                        f"Telegram API error: {description}", status_code=response.status_code
                    )
                return data
        raise AssertionError("retry loop returned without a result")

    def _wait_for_slot(self) -> None:
        now = time.monotonic()
        if self._last_request_at is not None:
            remaining = self.min_interval - (now - self._last_request_at)
            if remaining > 0:
                self._sleep(remaining)
        self._last_request_at = time.monotonic()

    def _backoff(self, attempt: int) -> float:
        return min(8.0, 0.5 * (2**attempt)) + random.uniform(0, 0.1)

    def _retry_after(self, response: httpx.Response, attempt: int) -> float:
        try:
            value = response.json().get("parameters", {}).get("retry_after")
            if value is not None:
                return max(0.0, float(value))
        except (ValueError, TypeError, AttributeError):
            pass
        return self._backoff(attempt)

    def call(self, method: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        """Generic Bot API call with the same retry policy as ``send_message``.

        Used by the subscriber bot (``getUpdates``, ``answerCallbackQuery``,
        ``editMessageReplyMarkup``). URLs embed the token, so no request URL is
        ever logged. Returns the raw API payload.
        """
        endpoint = f"{self.base_url}/bot{self.token}/{method}"
        with httpx.Client(timeout=self.timeout, transport=self._transport) as client:
            for attempt in range(self.max_retries + 1):
                self._wait_for_slot()
                try:
                    response = client.post(endpoint, json=payload or {})
                except httpx.HTTPError as exc:
                    if attempt >= self.max_retries:
                        raise TelegramError(
                            f"Telegram request failed: {exc}", retryable=True
                        ) from exc
                    self._sleep(self._backoff(attempt))
                    continue
                if response.status_code == 429 or response.status_code >= 500:
                    if attempt >= self.max_retries:
                        raise TelegramError(
                            f"Telegram error {response.status_code}", retryable=True
                        )
                    self._sleep(self._retry_after(response, attempt))
                    continue
                try:
                    data = response.json()
                except ValueError as exc:
                    raise TelegramError("Telegram returned invalid JSON") from exc
                if response.status_code >= 400 or not data.get("ok"):
                    raise TelegramError(
                        f"Telegram API error: {data.get('description', response.text)}",
                        status_code=response.status_code,
                    )
                return data
        raise AssertionError("retry loop returned without a result")

    def get_updates(self, offset: int | None, *, timeout: int = 25) -> list[dict[str, Any]]:
        """Long-poll ``getUpdates``; returns the update list (possibly empty)."""
        payload: dict[str, Any] = {
            "timeout": max(0, int(timeout)),
            "allowed_updates": ["message", "callback_query"],
        }
        if offset is not None:
            payload["offset"] = offset
        data = self.call("getUpdates", payload)
        result = data.get("result")
        return result if isinstance(result, list) else []

    def answer_callback_query(self, callback_query_id: str, *, text: str = "") -> None:
        """Acknowledge a button press (otherwise the client shows a spinner)."""
        self.call("answerCallbackQuery", {"callback_query_id": callback_query_id, "text": text})

    def edit_message_reply_markup(
        self, chat_id: str | int, message_id: int, reply_markup: dict[str, Any]
    ) -> None:
        """Replace the inline keyboard of a message after a toggle."""
        self.call(
            "editMessageReplyMarkup",
            {"chat_id": str(chat_id), "message_id": message_id, "reply_markup": reply_markup},
        )


def post_text(article: Article) -> str:
    """Compatibility alias for callers that prefer a publication-specific name."""
    return format_post(article)
