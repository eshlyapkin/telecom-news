"""Collector primitives (M1, see ARCHITECTURE.md 3.2).

:class:`RawItem` is the raw record every collector produces. :func:`fetch_url`
is the shared HTTP getter: timeout, retries with exponential backoff and a
descriptive User-Agent. All failures surface as :class:`CollectorError` so
callers never have to handle httpx specifics.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime

import httpx

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT: float = 15.0
DEFAULT_MAX_RETRIES: int = 3
DEFAULT_BACKOFF_BASE: float = 1.0  # seconds; waits 1s, 2s, ... between attempts
USER_AGENT: str = "telecom-news/0.1 (+https://github.com/eshlyapkin/telecom-news)"


class CollectorError(Exception):
    """A collector could not retrieve or parse its source."""


@dataclass
class RawItem:
    """One raw record from a source, before normalization (M1).

    ``published_at`` should be UTC when the source provides a timezone;
    naive values are interpreted as UTC later, in ``processors.normalize``.
    """

    url: str
    source_id: str
    title: str = ""
    published_at: datetime | None = None
    content: str = ""
    language: str | None = None


def fetch_url(
    url: str,
    *,
    timeout: float = DEFAULT_TIMEOUT,
    max_retries: int = DEFAULT_MAX_RETRIES,
    backoff_base: float = DEFAULT_BACKOFF_BASE,
    client: httpx.Client | None = None,
) -> bytes:
    """GET ``url`` and return the response body.

    Retries transient failures (timeouts, network errors, HTTP 5xx) up to
    ``max_retries`` attempts with exponential backoff. HTTP 4xx and other
    fatal errors fail immediately. Every failure is wrapped in
    :class:`CollectorError`.

    ``client`` is an optional pre-configured ``httpx.Client`` (used by tests
    with ``httpx.MockTransport``); when omitted, a short-lived client with
    the project User-Agent is created.
    """
    last_error: Exception | None = None
    for attempt in range(1, max_retries + 1):
        try:
            if client is None:
                with httpx.Client(
                    timeout=timeout,
                    headers={"User-Agent": USER_AGENT},
                    follow_redirects=True,
                ) as owned:
                    response = owned.get(url)
            else:
                response = client.get(url)
            response.raise_for_status()
            return response.content
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            last_error = exc
            if 500 <= status < 600 and attempt < max_retries:
                logger.warning(
                    "GET %s -> HTTP %s (attempt %d/%d), retrying",
                    url,
                    status,
                    attempt,
                    max_retries,
                )
            else:
                raise CollectorError(f"GET {url} failed with HTTP {status}") from exc
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            last_error = exc
            if attempt < max_retries:
                logger.warning(
                    "GET %s failed (%s, attempt %d/%d), retrying",
                    url,
                    exc.__class__.__name__,
                    attempt,
                    max_retries,
                )
            else:
                raise CollectorError(
                    f"GET {url} failed after {max_retries} attempts: {exc}"
                ) from exc
        if attempt < max_retries:
            time.sleep(backoff_base * 2 ** (attempt - 1))
    # Only reachable with max_retries < 1; kept as a defensive guard.
    raise CollectorError(f"GET {url} failed after {max_retries} attempts: {last_error}")
