"""The Queue must answer "what goes out next, and when" (D-031, panel)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from telecom_news import held_articles
from telecom_news.config import load_config
from telecom_news.models import Article
from telecom_news.publication_plan import build_plan, evergreen_slots
from telecom_news.storage.database import Database

NOW = datetime(2026, 9, 18, 12, 0, tzinfo=timezone.utc)


def test_slots_keep_the_gap_between_posts() -> None:
    slots = evergreen_slots(count=3, recent_posts=[], now=NOW, per_day=6, gap_hours=2)

    assert slots == [NOW, NOW + timedelta(hours=2), NOW + timedelta(hours=4)]


def test_the_first_slot_waits_out_a_recent_post() -> None:
    slots = evergreen_slots(
        count=1, recent_posts=[NOW - timedelta(minutes=30)], now=NOW, per_day=6, gap_hours=2
    )

    assert slots == [NOW + timedelta(hours=1, minutes=30)]


def test_a_full_quota_waits_for_the_rolling_window_not_midnight() -> None:
    """Six posts in the last 24 h: the seventh waits for the oldest to age out."""
    posted = [NOW - timedelta(hours=h) for h in (23, 20, 17, 14, 11, 8)]

    (slot,) = evergreen_slots(count=1, recent_posts=posted, now=NOW, per_day=6, gap_hours=2)

    assert slot == NOW - timedelta(hours=23) + timedelta(hours=24)


def test_no_slots_when_the_lane_is_off() -> None:
    assert evergreen_slots(count=5, recent_posts=[], now=NOW, per_day=0, gap_hours=2) == []


def _article(n: int, *, hours_ago: float, category: str) -> Article:
    moment = datetime.now(timezone.utc) - timedelta(hours=hours_ago)
    return Article(
        url=f"https://example.com/{n}",
        content_hash=f"h{n}",
        source_id="test",
        title=f"Title {n}",
        body="body",
        category=category,
        status="processed",
        published_at=moment,
        fetched_at=moment,
    )


@pytest.fixture()
def seeded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Database, Path]:
    monkeypatch.setenv("TELECOM_NEWS_DATA_DIR", str(tmp_path))
    db = Database(tmp_path / "news.db")
    for article in (
        _article(1, hours_ago=1, category="vendor"),  # news
        _article(2, hours_ago=500, category="regulation"),  # archive, lowest rank
        _article(3, hours_ago=500, category="network_protocol"),  # archive, first
    ):
        article_id, _ = db.upsert_by_hash(article)
        db.set_status(article_id, "processed")
    return db, tmp_path


def test_the_plan_splits_the_lanes_and_leads_with_technical_material(seeded) -> None:
    db, data_dir = seeded

    plan = build_plan(db=db, data_dir=data_dir, config=load_config())

    assert (plan["news_waiting"], plan["archive_waiting"]) == (1, 2)
    lanes = [(row["id"], row["lane"]) for row in plan["rows"]]
    assert lanes == [(1, "news"), (3, "archive"), (2, "archive")]
    news, first, second = plan["rows"]
    assert news["cycle"] == 1 and news["eta"] is None
    assert first["eta"] is not None and second["eta"] is not None
    assert first["eta"] < second["eta"]  # the gap separates them


def test_a_held_article_keeps_its_place_in_the_list_but_takes_no_slot(seeded) -> None:
    db, data_dir = seeded
    held_articles.hold(data_dir, 3)

    plan = build_plan(db=db, data_dir=data_dir, config=load_config())

    rows = {row["id"]: row for row in plan["rows"]}
    assert rows[3]["held"] is True
    assert rows[3]["eta"] is None
    assert rows[2]["eta"] is not None  # the queue moves on without it
    assert plan["archive_waiting"] == 1
    assert plan["held"] == 1
