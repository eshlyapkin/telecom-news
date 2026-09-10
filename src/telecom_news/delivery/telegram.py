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

    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


def _escape(value: str | None) -> str:
    return html.escape(value or "", quote=True)


def format_post(article: Article) -> str:
    """Format a processed article as an HTML Telegram message."""
    if not article.llm_result:
        raise TelegramError(f"article id={article.id} has no LLM result")
    summary = article.llm_result.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        raise TelegramError(f"article id={article.id} has no summary")
    category = article.category or article.llm_result.get("category") or "news"
    language = article.llm_result.get("summary_language") or article.language
    return (
        f"<b>{_escape(article.title)}</b>\n\n"
        f"{_escape(summary.strip())}\n\n"
        f"<b>Категория:</b> {_escape(str(category))}\n"
        f"<b>Язык:</b> {_escape(str(language))}\n"
        f'<a href="{_escape(article.url)}">Источник</a>'
    )


class TelegramClient:
    """Small retrying client for Telegram's ``sendMessage`` endpoint."""

    def __init__(
        self,
        token: str,
        *,
        timeout: float = 30.0,
        max_retries: int = 3,
        transport: httpx.BaseTransport | None = None,
        base_url: str = "https://api.telegram.org",
        sleep: Any = time.sleep,
    ) -> None:
        if not token:
            raise ValueError("Telegram bot token must not be empty")
        self.token = token
        self.timeout = timeout
        self.max_retries = max(0, max_retries)
        self.base_url = base_url.rstrip("/")
        self._transport = transport
        self._sleep = sleep

    @property
    def endpoint(self) -> str:
        return f"{self.base_url}/bot{self.token}/sendMessage"

    def send_message(self, chat_id: str, text: str) -> dict[str, Any]:
        """Send one HTML message, retrying transient API failures."""
        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "link_preview_options": {"is_disabled": False},
        }
        with httpx.Client(timeout=self.timeout, transport=self._transport) as client:
            for attempt in range(self.max_retries + 1):
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
                    raise TelegramError(f"Telegram API error: {description}")
                return data
        raise AssertionError("retry loop returned without a result")

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


def post_text(article: Article) -> str:
    """Compatibility alias for callers that prefer a publication-specific name."""
    return format_post(article)
