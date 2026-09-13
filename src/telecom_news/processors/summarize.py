"""Summarization + translation (M3, see ARCHITECTURE.md 3.9).

One LLM call per relevant article produces a headline and a summary in the
publication language (translating when the source language differs).

The headline matters as much as the body: an English post that keeps the
original Russian headline reads as broken, and until 2026-09-13 that is exactly
what the channel sent, because renditions stored ``title=article.title``.
``title`` is optional in the reply so that a model which only returns a summary
still works — the caller falls back to the original headline.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..llm.client import LLMClient, LLMResponseError, parse_json_response
from ..models import Article
from .relevance import prepare_article_text

_LANGUAGE_NAMES = {"ru": "Russian", "en": "English"}


@dataclass(frozen=True)
class SummaryResult:
    """LLM headline + summary for one article."""

    summary: str
    summary_language: str
    title: str = ""  # empty = model returned none, keep the original headline


def summarize(client: LLMClient, article: Article, *, target_lang: str) -> SummaryResult:
    """Summarize a relevant article in the publication language."""
    language_name = _LANGUAGE_NAMES.get(target_lang, target_lang)
    content = client.chat(
        [
            {
                "role": "system",
                "content": (
                    "You are a news editor for an SMS-industry Telegram channel. "
                    f"Write a headline and a summary of the article below in {language_name}. "
                    f"Both must be written in {language_name}, even when the article is in "
                    "another language — translate rather than copy. "
                    "The headline is one line, at most 120 characters, no trailing period. "
                    "The summary is 2-4 sentences. Keep facts, names and numbers. "
                    "No preamble. Reply with a single JSON object only: "
                    '{"title": "<headline>", "summary": "<summary text>"}'
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
    raw_title = data.get("title")
    title = raw_title.strip() if isinstance(raw_title, str) else ""
    return SummaryResult(
        summary=summary.strip(),
        summary_language=target_lang,
        title=title,
    )
