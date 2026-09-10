"""Summarization + translation (M3, see ARCHITECTURE.md 3.9).

One LLM call per relevant article produces a summary in the publication
language (translating when the source language differs).
"""

from __future__ import annotations

from dataclasses import dataclass

from ..llm.client import LLMClient, LLMResponseError, parse_json_response
from ..models import Article
from .relevance import prepare_article_text

_LANGUAGE_NAMES = {"ru": "Russian", "en": "English"}


@dataclass(frozen=True)
class SummaryResult:
    """LLM summary for one article."""

    summary: str
    summary_language: str


def summarize(client: LLMClient, article: Article, *, target_lang: str) -> SummaryResult:
    """Summarize a relevant article in the publication language."""
    language_name = _LANGUAGE_NAMES.get(target_lang, target_lang)
    content = client.chat(
        [
            {
                "role": "system",
                "content": (
                    "You are a news summarizer for an SMS-industry Telegram channel. "
                    f"Summarize the article below in {language_name} in 2-4 sentences. "
                    "Keep facts, names and numbers. No preamble. "
                    'Reply with a single JSON object only: {"summary": "<summary text>"}'
                ),
            },
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
    summary = data.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        raise LLMResponseError(f"empty 'summary' in model output: {content[:200]!r}")
    return SummaryResult(summary=summary.strip(), summary_language=target_lang)
