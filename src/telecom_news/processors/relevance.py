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

# Cheap deterministic guard for broad vendor feeds. It prevents obvious
# voice/video/developer material from reaching the LLM and being misclassified
# as messaging merely because the publisher is a communications company.
# Built-in defaults. Runtime may override via data/ai_rules.json (M9c).
DEFAULT_MESSAGING_TERMS: tuple[str, ...] = (
    # Latin script. These also match Russian articles that keep the Latin
    # spelling ("SMS-рассылки"), but see the Cyrillic block below.
    "sms",
    "a2p",
    "p2a",
    "p2p",
    "mms",
    "rcs",
    "whatsapp",
    "messaging",
    "text message",
    "texting",
    "otp",
    "one-time password",
    "verification code",
    "smishing",
    "short message",
    "business message",
    "telegram",
    "chatbot",
    # Cyrillic spellings and transliterations. Russian outlets write "СМС",
    # "чат-бот", "мошенничество с кодом подтверждения" — a Latin-only list
    # skipped every such article before the LLM ever saw it, which made the
    # RU sources added in M7 publish nothing at all (see DECISIONS.md D-011).
    "смс",
    "эсэмэс",
    "смшинг",
    "ммс",
    "а2п",
    "мессендж",
    "чат-бот",
    "чатбот",
    "вотсап",
    "телеграм",
    "код подтверждения",
    "одноразовый пароль",
)
# Deliberately excluded: "сообщени" (matches "по сообщению пресс-службы" in
# every press release), "рассылк" (mostly email marketing noise), "верификац"
# (KYC/fintech noise), "ркс" (Cyrillic "RCS" collides with unrelated "РКС").
# They would turn this guard into a no-op and spend LM Studio calls on junk.
DEFAULT_OFF_TOPIC_TERMS: tuple[str, ...] = (
    "video chat",
    "programmable video",
    "web rtc",
    "webrtc",
    "video application",
    "video app",
    "voice network",
    "voice call",
    "sip trunk",
    "email marketing",
    "email sending",
    "voip",
    # Russian equivalents, same intent as the Latin entries above.
    "видеозвон",
    "видеосвяз",
    "голосовая связь",
    "голосовые вызовы",
    "голосовой звонок",
    "email-рассылк",
    "почтовая рассылк",
)

# Back-compat aliases (tests / importers may still reference these names).
MESSAGING_TERMS: tuple[str, ...] = DEFAULT_MESSAGING_TERMS
OBVIOUSLY_OFF_TOPIC_TERMS: tuple[str, ...] = DEFAULT_OFF_TOPIC_TERMS


def _active_messaging_terms() -> tuple[str, ...]:
    try:
        from ..ai_rules import load_rules

        return tuple(load_rules().messaging_terms)
    except Exception:  # noqa: BLE001 — never break classify on rules I/O
        return DEFAULT_MESSAGING_TERMS


def _active_off_topic_terms() -> tuple[str, ...]:
    try:
        from ..ai_rules import load_rules

        return tuple(load_rules().off_topic_terms)
    except Exception:  # noqa: BLE001
        return DEFAULT_OFF_TOPIC_TERMS


def _active_system_prompt() -> str:
    try:
        from ..ai_rules import load_rules

        return load_rules().system_prompt
    except Exception:  # noqa: BLE001
        return DEFAULT_RELEVANCE_SYSTEM_PROMPT


def _force_llm_gate() -> bool:
    try:
        from ..ai_rules import load_rules

        return bool(load_rules().force_llm_gate)
    except Exception:  # noqa: BLE001
        return False


def _haystack(article: Article) -> str:
    return f"{article.title} {article.body}".casefold()


def has_messaging_signal(article: Article) -> bool:
    """Return true when the text explicitly signals the target ecosystem."""
    terms = _active_messaging_terms()
    return any(term in _haystack(article) for term in terms)


def is_obviously_off_topic(article: Article) -> bool:
    """Return true for clear non-SMS content from broad communication feeds.

    Two independent ways in, so the guard can actually reject material:

    1. an off-topic term in the **title** while the title carries no messaging
       term — the title defines the story, an incidental body mention of "SMS"
       must not rescue a video/voice product announcement;
    2. an off-topic term anywhere when there is no messaging signal at all.

    Before D-011 only rule 2 existed, and :func:`check_relevance` already
    returned early for articles without a signal, so rule 2 could never fire
    there and the whole guard was dead code.
    """
    messaging = _active_messaging_terms()
    off_topic = _active_off_topic_terms()
    title = (article.title or "").casefold()
    if any(term in title for term in off_topic) and not any(term in title for term in messaging):
        return True
    return any(term in _haystack(article) for term in off_topic) and not (
        has_messaging_signal(article)
    )


DEFAULT_RELEVANCE_SYSTEM_PROMPT = (
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

# Back-compat alias.
RELEVANCE_SYSTEM_PROMPT = DEFAULT_RELEVANCE_SYSTEM_PROMPT

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


def check_relevance(
    client: LLMClient, article: Article, *, use_gate: bool = True
) -> RelevanceResult:
    """Classify whether the article belongs to the SMS/messaging ecosystem.

    ``use_gate=False`` skips the deterministic keyword guard and sends the
    article straight to the model. Narrow feeds whose item text is too thin for
    the keyword list need that (DECISIONS.md D-012); broad vendor feeds keep the
    guard so obvious voice/video/email material never costs an LLM call.
    """
    gate_on = use_gate and not _force_llm_gate()
    if gate_on:
        if not has_messaging_signal(article):
            return RelevanceResult(
                relevant=False,
                category=None,
                reason="no explicit SMS/messaging signal in title or feed text",
            )
        if is_obviously_off_topic(article):
            return RelevanceResult(
                relevant=False,
                category=None,
                reason="obvious voice/video/email topic without an SMS/messaging link",
            )
    content = client.chat(
        [
            {"role": "system", "content": _active_system_prompt()},
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
