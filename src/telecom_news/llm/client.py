"""LM Studio HTTP client (M3, see ARCHITECTURE.md 3.8).

Talks to the OpenAI-compatible endpoint (``POST /chat/completions``,
``GET /models``). Timeouts, retries with backoff, explicit model or
auto-detection of the first loaded model. Transport-level failures surface
as :class:`LLMUnavailableError` (transient — the pipeline must retry later);
successful HTTP replies with a broken envelope surface as
:class:`LLMResponseError`.
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "http://localhost:1234/v1"
DEFAULT_TIMEOUT = 60.0
DEFAULT_MAX_RETRIES = 3
DEFAULT_BACKOFF_BASE = 1.0


class LLMUnavailableError(Exception):
    """The LLM service cannot be reached or has no models loaded (transient)."""


class LLMResponseError(Exception):
    """The LLM service replied, but the response was malformed."""


_FENCED_JSON_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)
_BRACED_RE = re.compile(r"\{.*\}", re.DOTALL)


def parse_json_response(text: str) -> dict[str, Any]:
    """Extract a JSON object from model output (plain, fenced or embedded)."""
    candidates = [text.strip()]
    fenced = _FENCED_JSON_RE.search(text)
    if fenced:
        candidates.append(fenced.group(1))
    braced = _BRACED_RE.search(text)
    if braced:
        candidates.append(braced.group(0))
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except ValueError:
            continue
        if isinstance(parsed, dict):
            return parsed
    raise LLMResponseError(f"no JSON object found in model output: {text[:200]!r}")


@dataclass
class LLMClient:
    """Minimal OpenAI-compatible chat client for LM Studio (M3)."""

    base_url: str = DEFAULT_BASE_URL
    model: str = ""  # empty = auto-detect the first loaded model via /models
    timeout: float = DEFAULT_TIMEOUT
    max_retries: int = DEFAULT_MAX_RETRIES
    backoff_base: float = DEFAULT_BACKOFF_BASE
    _resolved_model: str | None = field(default=None, repr=False, compare=False)

    def _request(
        self, client: httpx.Client, method: str, path: str, payload: dict[str, Any] | None = None
    ) -> Any:
        url = self.base_url.rstrip("/") + path
        last_error: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            try:
                if method == "GET":
                    response = client.get(url)
                else:
                    response = client.post(url, json=payload)
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_error = exc
                if attempt >= self.max_retries:
                    break
                logger.warning(
                    "%s %s failed (%s, attempt %d/%d), retrying",
                    method,
                    url,
                    exc.__class__.__name__,
                    attempt,
                    self.max_retries,
                )
                time.sleep(self.backoff_base * 2 ** (attempt - 1))
                continue
            if response.status_code >= 500 and attempt < self.max_retries:
                last_error = RuntimeError(f"HTTP {response.status_code}")
                logger.warning(
                    "%s %s -> HTTP %s (attempt %d/%d), retrying",
                    method,
                    url,
                    response.status_code,
                    attempt,
                    self.max_retries,
                )
                time.sleep(self.backoff_base * 2 ** (attempt - 1))
                continue
            if response.status_code >= 400:
                raise LLMUnavailableError(f"{method} {url} failed with HTTP {response.status_code}")
            try:
                return response.json()
            except ValueError as exc:
                raise LLMResponseError(f"{method} {url} returned invalid JSON") from exc
        raise LLMUnavailableError(
            f"{method} {url} failed after {self.max_retries} attempts: {last_error}"
        )

    def _call(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        client: httpx.Client | None = None,
    ) -> Any:
        if client is not None:
            return self._request(client, method, path, payload)
        with httpx.Client(timeout=self.timeout) as owned:
            return self._request(owned, method, path, payload)

    def ensure_model(self, client: httpx.Client | None = None) -> str:
        """Configured model, or the first model loaded in LM Studio (cached)."""
        if self.model:
            return self.model
        if self._resolved_model is None:
            data = self._call("GET", "/models", None, client)
            models = data.get("data") if isinstance(data, dict) else None
            first = models[0] if isinstance(models, list) and models else None
            if not isinstance(first, dict) or not first.get("id"):
                raise LLMUnavailableError(
                    f"no models loaded at {self.base_url} (load a model in LM Studio)"
                )
            self._resolved_model = str(first["id"])
            logger.info("auto-detected LLM model: %s", self._resolved_model)
        return self._resolved_model

    def chat(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.0,
        max_tokens: int = 1024,
        client: httpx.Client | None = None,
    ) -> str:
        """One chat completion; returns the assistant message content."""
        model = self.ensure_model(client)
        data = self._call(
            "POST",
            "/chat/completions",
            {
                "model": model,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
            },
            client,
        )
        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMResponseError(
                f"chat completions envelope malformed: {str(data)[:200]}"
            ) from exc
        if not isinstance(content, str) or not content.strip():
            raise LLMResponseError("chat completions returned empty content")
        return content
