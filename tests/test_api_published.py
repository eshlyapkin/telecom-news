"""Published tab: what actually went out, per language, with the post preview."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

from telecom_news.api.app import create_app
from telecom_news.models import Article
from telecom_news.projects import DEFAULT_PROJECT_ID, ProjectRegistry
from telecom_news.storage.database import Database

NOW = datetime(2026, 9, 13, 9, 0, tzinfo=timezone.utc)
CHANNEL = "-100500"


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("TELECOM_NEWS_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("TELECOM_NEWS_DISABLED_SOURCES", raising=False)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", CHANNEL)
    monkeypatch.setenv("TELECOM_NEWS_TARGET_LANGS", "ru,en")

    db = Database(tmp_path / "news.db")
    article_id, _ = db.upsert_by_hash(
        Article(
            url="https://example.com/a1",
            content_hash="h1",
            source_id="cnews-telecom",
            title="Оператор обновил SMS-шлюз",
            body="Body",
            fetched_at=NOW,
            published_at=NOW,
        )
    )
    db.save_processing_result(
        article_id,
        relevance="relevant",
        category="network_protocol",
        llm_result={"relevant": True, "category": "network_protocol"},
        status="processed",
    )
    db.save_rendition(article_id, "ru", title="Оператор обновил SMS-шлюз", summary="Русский текст.")
    db.save_rendition(article_id, "en", title="Operator upgrades SMS gateway", summary="English.")
    db.record_delivery(CHANNEL, article_id, "ru", status="sent", message_id=11)
    db.record_delivery(CHANNEL, article_id, "en", status="sent", message_id=12)
    db.record_delivery("777", article_id, "ru", status="sent", message_id=13)  # subscriber
    db.mark_published(article_id)

    registry = ProjectRegistry(tmp_path / "projects" / "registry.json")
    registry.ensure_default()
    return TestClient(create_app(registry=registry))


def test_published_lists_both_renditions(client: TestClient) -> None:
    body = client.get(f"/api/projects/{DEFAULT_PROJECT_ID}/published").json()
    assert body["db_exists"] is True
    assert body["count"] == 1
    (post,) = body["posts"]
    assert post["category"] == "network_protocol"
    assert post["source_id"] == "cnews-telecom"

    by_lang = {r["lang"]: r for r in post["renditions"]}
    assert set(by_lang) == {"ru", "en"}
    assert by_lang["en"]["headline"] == "Operator upgrades SMS gateway"
    assert by_lang["ru"]["headline"] == "Оператор обновил SMS-шлюз"


def test_published_marks_channel_and_subscriber_sends(client: TestClient) -> None:
    (post,) = client.get(f"/api/projects/{DEFAULT_PROJECT_ID}/published").json()["posts"]
    by_lang = {r["lang"]: r for r in post["renditions"]}
    assert by_lang["en"]["channel_sent"] is True
    assert by_lang["en"]["message_id"] == 12
    assert by_lang["ru"]["subscriber_sends"] == 1  # the private chat, not the channel
    assert by_lang["en"]["subscriber_sends"] == 0


def test_preview_uses_the_rendition_headline(client: TestClient) -> None:
    """The English preview must not fall back to the Russian article title."""
    (post,) = client.get(f"/api/projects/{DEFAULT_PROJECT_ID}/published").json()["posts"]
    english = next(r for r in post["renditions"] if r["lang"] == "en")
    assert "Operator upgrades SMS gateway" in english["telegram_html"]
    assert "Оператор обновил" not in english["telegram_html"]
    assert "Category" in english["telegram_html"]  # English labels


def test_published_limit_is_validated(client: TestClient) -> None:
    assert client.get(f"/api/projects/{DEFAULT_PROJECT_ID}/published?limit=0").status_code == 400
    assert client.get(f"/api/projects/{DEFAULT_PROJECT_ID}/published?limit=201").status_code == 400


def test_published_unknown_project_is_404(client: TestClient) -> None:
    assert client.get("/api/projects/no-such/published").status_code == 404


def test_gui_has_a_published_tab(client: TestClient) -> None:
    html = client.get("/").text
    assert 'data-tab="published"' in html
    assert 'id="panel-published"' in html
    assert "refreshPublished" in client.get("/static/app.js").text
