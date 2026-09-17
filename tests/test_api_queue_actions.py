"""Queue admin actions: publish now, hold, remove, delete (panel)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

from telecom_news.api.app import create_app
from telecom_news.held_articles import load_held
from telecom_news.models import Article
from telecom_news.projects import ProjectRegistry
from telecom_news.storage.database import Database


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("TELECOM_NEWS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "chat")
    registry = ProjectRegistry(tmp_path / "projects" / "registry.json")
    registry.ensure_default()
    db = Database(tmp_path / "news.db")
    old = datetime.now(timezone.utc) - timedelta(days=200)
    article_id, _ = db.upsert_by_hash(
        Article(
            url="https://example.com/old",
            content_hash="h-old",
            source_id="test",
            title="IP-SM-GW interworking",
            body="body",
            category="network_protocol",
            published_at=old,
            fetched_at=old,
            llm_result={"summary": "Разбор", "summary_language": "ru"},
            status="processed",
        )
    )
    db.set_status(article_id, "processed")
    return TestClient(create_app(registry))


def test_the_plan_lists_the_waiting_article(client: TestClient) -> None:
    plan = client.get("/api/publication-plan").json()

    assert plan["archive_waiting"] == 1
    (row,) = plan["rows"]
    assert row["lane"] == "archive"
    assert row["eta"] is not None


def test_hold_and_resume_round_trip(client: TestClient, tmp_path: Path) -> None:
    assert client.post("/api/articles/1/hold").json()["held"] is True
    assert load_held(tmp_path) == {1}
    assert client.get("/api/publication-plan").json()["archive_waiting"] == 0

    assert client.delete("/api/articles/1/hold").json()["held"] is False
    assert load_held(tmp_path) == set()


def test_remove_keeps_the_row_so_collect_cannot_bring_it_back(
    client: TestClient, tmp_path: Path
) -> None:
    assert client.post("/api/articles/1/skip").json()["status"] == "skipped"

    db = Database(tmp_path / "news.db")
    assert db.get_article(1) is not None
    assert db.get_article(1).status == "skipped"
    assert client.get("/api/publication-plan").json()["rows"] == []


def test_delete_drops_the_row_and_its_hold(client: TestClient, tmp_path: Path) -> None:
    client.post("/api/articles/1/hold")

    assert client.delete("/api/articles/1").json()["deleted"] == 1

    assert Database(tmp_path / "news.db").get_article(1) is None
    assert load_held(tmp_path) == set()


def test_actions_on_an_unknown_article_are_404(client: TestClient) -> None:
    assert client.post("/api/articles/999/publish").status_code == 404
    assert client.post("/api/articles/999/hold").status_code == 404
    assert client.post("/api/articles/999/skip").status_code == 404
    assert client.delete("/api/articles/999").status_code == 404


def test_publish_now_posts_a_held_archive_article(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A hold parks an article in the lanes; it is not a lock against the operator."""
    sent: list[tuple[str, str]] = []

    class FakeClient:
        def __init__(self, token: str, **kwargs: object) -> None:
            pass

        def send_message(self, chat_id: str, text: str, **kwargs: object) -> dict:
            sent.append((chat_id, text))
            return {"ok": True, "result": {"message_id": len(sent)}}

    import telecom_news.delivery.telegram as telegram

    monkeypatch.setattr(telegram, "TelegramClient", FakeClient)
    client.post("/api/articles/1/hold")

    body = client.post("/api/articles/1/publish").json()

    assert body["published"] is True
    assert len(sent) == 1
    assert Database(tmp_path / "news.db").get_article(1).status == "published"
