"""What publish will post next, and when (control panel Queue tab).

The Queue showed which articles were waiting but not the order or the timing, so
"what goes out next?" could only be answered by reading the publish code. This
module computes the same order publish uses and, for the evergreen lane, the
actual clock times its pace allows.

Nothing here writes: it is a projection of the current state, and any manual
action (publish now, hold, remove) changes the projection on the next read.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .held_articles import load_held
from .models import Article

# Order the evergreen lane works through its queue. The channel exists for
# reference material first (operator, 2026-09-17): protocol and architecture
# walkthroughs ahead of everything, regulation last — it is the class the
# operator ranks lowest. Collection order would have posted six legal articles
# before the first technical one purely because they were fetched a day earlier.
# A category the list does not name sorts after all of them.
EVERGREEN_CATEGORY_ORDER: tuple[str, ...] = (
    "network_protocol",
    "technology",
    "event",
    "aggregator",
    "carrier",
    "vendor",
    "ma_investment",
    "partnership",
    "security_antifraud",
    "product_service",
    "regulation",
)

ROLLING_WINDOW = timedelta(hours=24)


def evergreen_rank(category: str | None) -> int:
    """Position of ``category`` in the evergreen queue; unknown sorts last."""
    try:
        return EVERGREEN_CATEGORY_ORDER.index(str(category))
    except ValueError:
        return len(EVERGREEN_CATEGORY_ORDER)


def _as_utc(value: datetime) -> datetime:
    return (
        value.replace(tzinfo=timezone.utc)
        if value.tzinfo is None
        else value.astimezone(timezone.utc)
    )


def article_moment(article: Article) -> datetime | None:
    """The article's age for a freshness window — mirrors storage.FRESHNESS_SQL."""
    for value in (article.published_at, article.published_at_telegram, article.fetched_at):
        if value is not None:
            return _as_utc(value)
    return None


def evergreen_slots(
    *,
    count: int,
    recent_posts: Sequence[datetime],
    now: datetime,
    per_day: int,
    gap_hours: float,
) -> list[datetime]:
    """When the next ``count`` archive articles can go out.

    Two constraints, the same ones publish enforces: never two posts closer than
    ``gap_hours``, and never more than ``per_day`` inside any rolling 24 hours.
    When the quota is full the next slot is the moment the oldest post inside the
    window falls out of it — the limit is a rolling window, not a calendar day.
    """
    if count <= 0 or per_day <= 0:
        return []
    gap = timedelta(hours=max(0.0, float(gap_hours)))
    posted = sorted(_as_utc(moment) for moment in recent_posts)
    slots: list[datetime] = []
    for _ in range(count):
        earliest = now
        if posted:
            earliest = max(earliest, posted[-1] + gap)
        # Rolling quota: wait for the oldest post still inside the window to leave.
        while True:
            in_window = [moment for moment in posted if moment > earliest - ROLLING_WINDOW]
            if len(in_window) < per_day:
                break
            earliest = max(earliest, min(in_window) + ROLLING_WINDOW)
        slots.append(earliest)
        posted.append(earliest)
        posted.sort()
    return slots


def build_plan(*, db: Any, data_dir: Path | str, config: Any, limit: int = 60) -> dict[str, Any]:
    """The publication order publish will follow, with times where they are known.

    News goes first and in whole cycles: the per-cycle cap belongs to it, and the
    exact clock time depends on the scheduler outside this process, so a news row
    carries the cycle it lands in rather than a timestamp. Archive rows carry real
    times, because their pace is decided here.
    """
    now = datetime.now(timezone.utc)
    window = float(config.publish_max_age_hours)
    since = now - timedelta(hours=window) if window > 0 else None
    held = load_held(data_dir)
    per_cycle = int(config.publish_max_per_cycle)

    news: list[Article] = []
    archive: list[Article] = []
    for article in db.get_unprocessed(status="processed"):
        moment = article_moment(article)
        is_archive = since is not None and moment is not None and moment < since
        (archive if is_archive else news).append(article)
    news.sort(key=lambda item: item.id or 0)
    archive.sort(key=lambda item: (evergreen_rank(item.category), item.id or 0))

    recent_posts = db.backlog_posts_since(since=now - ROLLING_WINDOW, window_hours=window)
    live_archive = [item for item in archive if item.id not in held]
    slots = evergreen_slots(
        count=min(len(live_archive), limit),
        recent_posts=recent_posts,
        now=now,
        per_day=int(config.publish_backlog_per_day),
        gap_hours=float(config.publish_backlog_min_gap_hours),
    )

    rows: list[dict[str, Any]] = []

    def _row(article: Article, lane: str, **extra: Any) -> dict[str, Any]:
        moment = article_moment(article)
        return {
            "id": article.id,
            "title": (article.title or "")[:160],
            "source_id": article.source_id,
            "category": article.category,
            "url": article.url,
            "published_at": moment.isoformat() if moment else None,
            "lane": lane,
            "held": article.id in held,
            **extra,
        }

    position = 0
    for article in news:
        if article.id in held:
            rows.append(_row(article, "news", position=None, cycle=None, eta=None))
            continue
        position += 1
        rows.append(
            _row(
                article,
                "news",
                position=position,
                # Cycle 1 is the next run of the scheduler, whenever that is.
                cycle=(position - 1) // max(1, per_cycle) + 1 if per_cycle else 1,
                eta=None,
            )
        )

    slot_index = 0
    for article in archive:
        if article.id in held:
            rows.append(_row(article, "archive", position=None, cycle=None, eta=None))
            continue
        eta = slots[slot_index] if slot_index < len(slots) else None
        slot_index += 1
        rows.append(
            _row(
                article,
                "archive",
                position=slot_index,
                cycle=None,
                eta=eta.isoformat(timespec="minutes") if eta else None,
            )
        )

    return {
        "generated_at": now.isoformat(timespec="seconds"),
        "window_hours": window,
        "per_cycle": per_cycle,
        "backlog_per_day": int(config.publish_backlog_per_day),
        "backlog_min_gap_hours": float(config.publish_backlog_min_gap_hours),
        "posted_last_24h": len(recent_posts),
        "news_waiting": len([item for item in news if item.id not in held]),
        "archive_waiting": len(live_archive),
        "held": len([row for row in rows if row["held"]]),
        "rows": rows[:limit],
    }
