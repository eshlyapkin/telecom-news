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
# Some publishers sit behind Cloudflare/WAF and answer 403 to any non-browser
# User-Agent (three catalog feeds did exactly that on 2026-09-11). The first
# 403/406 is therefore retried once with a plain browser User-Agent.
BROWSER_USER_AGENT: str = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36"
)


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
    user_agent = USER_AGENT
    # Switching to the browser User-Agent must not spend a retry. Discovery
    # probes with max_retries=1, so the "retry as a browser" below never ran
    # there: the loop was already on its last attempt when the switch happened,
    # and every publisher behind a plain User-Agent filter looked unreachable.
    # mobileworldlive.com is the case that surfaced it — 403 to the probe, 200
    # and a full feed to a browser. The budget therefore grows by one when the
    # agent changes, which can happen only once.
    budget = max_retries
    attempt = 0
    while attempt < budget:
        attempt += 1
        try:
            if client is None:
                with httpx.Client(timeout=timeout, follow_redirects=True) as owned:
                    response = owned.get(url, headers={"User-Agent": user_agent})
            else:
                response = client.get(url, headers={"User-Agent": user_agent})
            response.raise_for_status()
            return response.content
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            last_error = exc
            if status in (403, 406) and user_agent != BROWSER_USER_AGENT:
                logger.warning(
                    "GET %s -> HTTP %s with the project User-Agent; retrying as a browser",
                    url,
                    status,
                )
                user_agent = BROWSER_USER_AGENT
                budget += 1
                continue
            if 500 <= status < 600 and attempt < budget:
                logger.warning(
                    "GET %s -> HTTP %s (attempt %d/%d), retrying",
                    url,
                    status,
                    attempt,
                    budget,
                )
            else:
                raise CollectorError(f"GET {url} failed with HTTP {status}") from exc
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            last_error = exc
            if attempt < budget:
                logger.warning(
                    "GET %s failed (%s, attempt %d/%d), retrying",
                    url,
                    exc.__class__.__name__,
                    attempt,
                    budget,
                )
            else:
                raise CollectorError(f"GET {url} failed after {attempt} attempts: {exc}") from exc
        except httpx.RequestError as exc:
            # DecodingError and the other RequestError kinds are NOT
            # TransportError subclasses, so they used to escape this wrapper
            # entirely: a single site answering with a Content-Encoding its body
            # does not honour ("Error -3 while decompressing data") aborted a
            # whole discovery scan instead of costing one skipped host. Retrying
            # cannot fix a body that will not decompress, so this fails at once.
            raise CollectorError(f"GET {url} failed: {exc.__class__.__name__}: {exc}") from exc
        if attempt < budget:
            time.sleep(backoff_base * 2 ** (attempt - 1))
    # Only reachable with max_retries < 1; kept as a defensive guard.
    raise CollectorError(f"GET {url} failed after {attempt} attempts: {last_error}")
