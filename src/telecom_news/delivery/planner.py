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
    """Best available timestamp of an article (publication beats collection).

    The order mirrors ``storage.database.FRESHNESS_SQL`` (D-020): the article's
    own publication date decides its age, then the moment we posted it, then the
    collection time. ``published_at`` must win over ``fetched_at`` — a feed that
    serves a 2022 entry today has a fresh collection time but is still old news.
    """
    return article.published_at or article.published_at_telegram or article.fetched_at


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
    """Message plan for one delivery cycle (see module docstring).

    ``max_age_hours <= 0`` disables the freshness window and ``max_per_user <= 0``
    disables the per-subscriber cap — the same "0 switches the guard off"
    convention as ``TELECOM_NEWS_MAX_ARTICLE_AGE_DAYS`` (D-017/D-020).
    """
    cutoff = now - timedelta(hours=max_age_hours) if max_age_hours > 0 else None
    per_user_cap = max_per_user if max_per_user > 0 else None
    attempts_lookup = attempts_of or (lambda chat_id, article_id, lang: 0)
    plans: list[DeliveryPlan] = []
    for chat_id in sorted(subscribers):
        langs = subscribers[chat_id]
        if not langs:
            continue
        taken = 0
        for article in articles:
            if article.id is None or (per_user_cap is not None and taken >= per_user_cap):
                break
            moment = article_moment(article)
            if cutoff is not None and moment is not None and moment < cutoff:
                continue
            for lang in langs:
                if per_user_cap is not None and taken >= per_user_cap:
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
