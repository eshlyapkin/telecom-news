"""Multi-language renditions (M8): one cached title+summary per (article, lang).

The channel languages are rendered once during ``process``; a language that only
some subscribers need is rendered lazily on first delivery (:func:`ensure_rendition`),
so the LLM never pays for a language nobody reads. The cache lives in the
``renditions`` table, therefore a restart or a repeated run never re-translates
the same article twice.

Cyrillic check: an LLM may return Latin text when asked for Russian (or vice
versa). A missing/incorrect script is logged but not treated as a failure — the
rendition is still stored, and the log line makes the model problem visible.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from ..llm.client import LLMClient
from ..models import Article
from ..storage.database import Database
from .summarize import summarize

logger = logging.getLogger(__name__)

_CYRILLIC_RE = re.compile(r"[а-яё]", re.IGNORECASE)
_LATIN_RE = re.compile(r"[a-z]", re.IGNORECASE)


@dataclass(frozen=True)
class Rendition:
    """Publication text of one article in one language."""

    article_id: int
    lang: str
    title: str
    summary: str
    model: str = ""
    cached: bool = False


def _script_mismatch(lang: str, text: str) -> bool:
    """True when the text looks like it was not written in ``lang``'s script."""
    if lang == "ru":
        return bool(_LATIN_RE.search(text)) and not _CYRILLIC_RE.search(text)
    if lang == "en":
        return bool(_CYRILLIC_RE.search(text))
    return False


def ensure_rendition(
    db: Database,
    client: LLMClient,
    article: Article,
    lang: str,
    *,
    model_name: str | None = None,
) -> Rendition:
    """Return the rendition for ``(article, lang)``, generating and caching it once.

    Raises :class:`~telecom_news.llm.client.LLMUnavailableError` /
    :class:`~telecom_news.llm.client.LLMResponseError` like :func:`summarize`;
    callers decide whether to retry next cycle.
    """
    article_id = article.id
    if article_id is None:
        raise ValueError("renditions require a stored article with an id")
    cached = db.get_rendition(article_id, lang)
    if cached is not None:
        return Rendition(
            article_id=article_id,
            lang=lang,
            title=str(cached.get("title") or ""),
            summary=str(cached.get("summary") or ""),
            model=str(cached.get("model") or ""),
            cached=True,
        )
    result = summarize(client, article, target_lang=lang)
    if _script_mismatch(lang, result.summary):
        logger.warning(
            "rendition for article id=%d looks like the wrong language (asked %s): %r",
            article_id,
            lang,
            result.summary[:80],
        )
    db.save_rendition(
        article_id,
        lang,
        title=article.title,
        summary=result.summary,
        model=model_name,
    )
    return Rendition(
        article_id=article_id,
        lang=lang,
        title=article.title,
        summary=result.summary,
        model=model_name or "",
    )
