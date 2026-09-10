"""Relevance filtering + classification (M3, see ARCHITECTURE.md 3.7).

One LLM call per article decides SMS/messaging relevance and (for relevant
ones) the news category. Unknown categories degrade to null with a warning
instead of failing the run; a missing relevance flag is a hard
:class:`LLMResponseError`.
"""

from __future__ import annotations

import html
import logging
import re
from dataclasses import dataclass

from ..llm.client import LLMClient, LLMResponseError, parse_json_response
from ..models import Article

logger = logging.getLogger(__name__)

CATEGORIES = (
    "technology",
    "vendor",
    "aggregator",
    "carrier",
    "product_service",
    "partnership",
    "ma_investment",
    "security_antifraud",
    "regulation",
)

MAX_INPUT_CHARS = 4000

RELEVANCE_SYSTEM_PROMPT = (
    "You are a news relevance classifier for an SMS-industry monitoring system. "
    "Decide whether the article is about the SMS/messaging ecosystem: A2P/P2A/P2P "
    "SMS, SMS vendors and messaging platforms, aggregators, carriers in SMS business "
    "context, SMS hubs, messaging routing/delivery/security/anti-fraud, RCS and "
    "business messaging, messaging partnerships and deals, SMS regulation. "
    "General telecom news WITHOUT a direct SMS/messaging link (5G, fiber, satellites, "
    "data centers, AI in general) is IRRELEVANT. "
    "Reply with a single JSON object only, no other text: "
    '{"relevant": true|false, "category": "<one of: technology, vendor, aggregator, '
    "carrier, product_service, partnership, ma_investment, security_antifraud, "
    'regulation> or null", "reason": "<one short sentence>"}. '
    "If relevant is false, category must be null."
)

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def prepare_article_text(article: Article, max_chars: int = MAX_INPUT_CHARS) -> str:
    """Plain-text article input for LLM prompts (tags stripped, truncated).

    Stored data is untouched — this only prepares prompt input.
    """
    text = _WS_RE.sub(" ", html.unescape(_TAG_RE.sub(" ", article.body or article.title or "")))
    text = text.strip()
    if len(text) > max_chars:
        text = text[:max_chars].rsplit(" ", 1)[0].rstrip() + "…"
    return text


@dataclass(frozen=True)
class RelevanceResult:
    """LLM verdict for one article."""

    relevant: bool
    category: str | None
    reason: str = ""


def _coerce_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in ("true", "yes", "1", "relevant"):
            return True
        if lowered in ("false", "no", "0", "irrelevant"):
            return False
    raise LLMResponseError(f"cannot interpret 'relevant' flag: {value!r}")


def check_relevance(client: LLMClient, article: Article) -> RelevanceResult:
    """Ask the LLM whether the article belongs to the SMS/messaging ecosystem."""
    content = client.chat(
        [
            {"role": "system", "content": RELEVANCE_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    f"Title: {article.title.strip() or '(no title)'}\n\n"
                    f"Text: {prepare_article_text(article)}"
                ),
            },
        ]
    )
    data = parse_json_response(content)
    if "relevant" not in data:
        raise LLMResponseError(f"'relevant' flag missing in model output: {content[:200]!r}")
    relevant = _coerce_bool(data["relevant"])
    reason = str(data.get("reason") or "")
    if not relevant:
        return RelevanceResult(relevant=False, category=None, reason=reason)
    category = data.get("category")
    if not isinstance(category, str) or category.strip().lower() not in CATEGORIES:
        logger.warning("unknown category %r, storing null (url=%s)", category, article.url)
        return RelevanceResult(relevant=True, category=None, reason=reason)
    return RelevanceResult(relevant=True, category=category.strip().lower(), reason=reason)
