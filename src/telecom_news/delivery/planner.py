"""Subscriber fan-out planning (M8): article x subscriber x language.

Pure function without I/O, so the rules are testable in isolation:

* every subscriber receives each article once per selected language;
* already delivered ``(chat_id, article_id, lang)`` triples never repeat;
* articles older than ``max_age_hours`` are not delivered (no flood of history
  after downtime, a new subscription or a long churn);
* one subscriber gets at most ``max_per_user`` messages per cycle, oldest
  article first;
* failed deliveries are retried, but not more than ``max_attempts`` times.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from ..models import Article


@dataclass(frozen=True)
class DeliveryPlan:
    """One message that must be sent to one subscriber in one language."""

    chat_id: str
    article: Article
    lang: str
    attempts: int = 0


def article_moment(article: Article) -> datetime | None:
    """Best available timestamp of an article (publication beats collection)."""
    return article.published_at_telegram or article.published_at or article.fetched_at


def plan_deliveries(
    articles: list[Article],
    subscribers: dict[str, list[str]],
    sent: set[tuple[str, int, str]],
    *,
    now: datetime,
    max_age_hours: float = 24.0,
    max_per_user: int = 10,
    max_attempts: int = 3,
    attempts_of: Callable[[str, int, str], int] | None = None,
) -> list[DeliveryPlan]:
    """Message plan for one delivery cycle (see module docstring)."""
    cutoff = now - timedelta(hours=max(max_age_hours, 0.0))
    attempts_lookup = attempts_of or (lambda chat_id, article_id, lang: 0)
    plans: list[DeliveryPlan] = []
    for chat_id in sorted(subscribers):
        langs = subscribers[chat_id]
        if not langs:
            continue
        taken = 0
        for article in articles:
            if article.id is None or taken >= max_per_user:
                break
            moment = article_moment(article)
            if moment is not None and moment < cutoff:
                continue
            for lang in langs:
                if taken >= max_per_user:
                    break
                key = (str(chat_id), int(article.id), lang)
                if key in sent:
                    continue
                attempts = attempts_lookup(*key)
                if attempts >= max_attempts:
                    continue
                plans.append(
                    DeliveryPlan(
                        chat_id=str(chat_id), article=article, lang=lang, attempts=attempts
                    )
                )
                taken += 1
    return plans
