"""M9d: source add/delete API + the overlays behind it.

The registry is a process-global dict (``config.SOURCES``), so every test here
restores the baseline on the way out — otherwise a custom feed added by one test
leaks into the catalog assertions of another module.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

from telecom_news import config as config_mod
from telecom_news.api.app import create_app
from telecom_news.custom_sources import add_source, delete_source, slug_from_url
from telecom_news.projects import ProjectRegistry


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("TELECOM_NEWS_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("TELECOM_NEWS_DISABLED_SOURCES", raising=False)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "-100")
    registry = ProjectRegistry(tmp_path / "projects" / "registry.json")
    registry.ensure_default()
    app = create_app(registry=registry)
    try:
        yield TestClient(app)
    finally:
        config_mod.reset_sources_to_baseline()


def test_add_source_registers_feed(client: TestClient, tmp_path: Path) -> None:
    response = client.post(
        "/api/sources",
        json={
            "url": "https://example.com/feed/",
            "id": "example-feed",
            "language": "en",
            "relevance_gate": "llm",
        },
    )
    assert response.status_code == 201
    created = response.json()
    assert created["id"] == "example-feed"
    assert created["custom"] is True
    assert created["relevance_gate"] == "llm"

    # live registry, listing and the overlay file all agree
    assert config_mod.SOURCES["example-feed"].url == "https://example.com/feed/"
    listing = client.get("/api/sources").json()
    row = next(item for item in listing["sources"] if item["id"] == "example-feed")
    assert row["custom"] is True and row["enabled"] is True
    assert listing["custom"] == 1
    stored = json.loads((tmp_path / "custom_sources.json").read_text(encoding="utf-8"))
    assert stored["sources"][0]["url"] == "https://example.com/feed/"


def test_add_source_derives_id_from_url(client: TestClient) -> None:
    created = client.post("/api/sources", json={"url": "https://www.example.org/blog/feed"}).json()
    assert created["id"] == "example-org-blog"
    assert created["language"] == "en"
    assert created["relevance_gate"] == "strict"


def test_add_source_rejects_bad_input(client: TestClient) -> None:
    bad_url = client.post("/api/sources", json={"url": "not-a-url"})
    assert bad_url.status_code == 400
    assert "http" in bad_url.json()["detail"]

    bad_lang = client.post(
        "/api/sources", json={"url": "https://example.com/feed/", "language": "de"}
    )
    assert bad_lang.status_code == 400

    bad_gate = client.post(
        "/api/sources", json={"url": "https://example.com/feed/", "relevance_gate": "maybe"}
    )
    assert bad_gate.status_code == 400

    taken = client.post(
        "/api/sources", json={"url": "https://example.com/feed/", "id": "sinch-blog"}
    )
    assert taken.status_code == 400
    assert "already exists" in taken.json()["detail"]


def test_add_source_rejects_duplicate_url(client: TestClient) -> None:
    first = client.post("/api/sources", json={"url": "https://example.com/feed/", "id": "one"})
    assert first.status_code == 201
    again = client.post("/api/sources", json={"url": "https://example.com/feed/", "id": "two"})
    assert again.status_code == 400
    assert "already registered" in again.json()["detail"]


def test_delete_custom_source_drops_the_entry(client: TestClient, tmp_path: Path) -> None:
    client.post("/api/sources", json={"url": "https://example.com/feed/", "id": "example-feed"})
    deleted = client.delete("/api/sources/example-feed")
    assert deleted.status_code == 200
    assert deleted.json() == {"id": "example-feed", "deleted": True, "kind": "custom"}

    assert "example-feed" not in config_mod.SOURCES
    stored = json.loads((tmp_path / "custom_sources.json").read_text(encoding="utf-8"))
    assert stored["sources"] == []
    assert not (tmp_path / "removed_sources.json").exists()


def test_delete_builtin_source_is_a_soft_delete(client: TestClient, tmp_path: Path) -> None:
    deleted = client.delete("/api/sources/sinch-blog")
    assert deleted.status_code == 200
    assert deleted.json()["kind"] == "builtin"

    assert "sinch-blog" not in config_mod.SOURCES
    stored = json.loads((tmp_path / "removed_sources.json").read_text(encoding="utf-8"))
    assert stored["removed"] == ["sinch-blog"]
    ids = {row["id"] for row in client.get("/api/sources").json()["sources"]}
    assert "sinch-blog" not in ids

    # the declaration itself survives: a fresh baseline still has it
    assert "sinch-blog" in config_mod._BASELINE_SOURCES


def test_readding_a_deleted_builtin_restores_it(client: TestClient, tmp_path: Path) -> None:
    client.delete("/api/sources/sinch-blog")
    restored = client.post(
        "/api/sources",
        json={"url": "https://sinch.com/blog/feed/", "id": "sinch-blog", "language": "en"},
    )
    assert restored.status_code == 201
    assert "sinch-blog" in config_mod.SOURCES
    stored = json.loads((tmp_path / "removed_sources.json").read_text(encoding="utf-8"))
    assert stored["removed"] == []


def test_delete_unknown_source_is_404(client: TestClient) -> None:
    assert client.delete("/api/sources/no-such-feed").status_code == 404


def test_toggle_still_works_for_custom_sources(client: TestClient) -> None:
    client.post("/api/sources", json={"url": "https://example.com/feed/", "id": "example-feed"})
    off = client.patch("/api/sources/example-feed", json={"enabled": False})
    assert off.status_code == 200
    assert off.json()["enabled"] is False
    assert config_mod.SOURCES["example-feed"].enabled is False

    row = next(
        item
        for item in client.get("/api/sources").json()["sources"]
        if item["id"] == "example-feed"
    )
    assert row["enabled"] is False and row["disabled_by_file"] is True


def test_deleting_a_disabled_source_clears_the_toggle(client: TestClient, tmp_path: Path) -> None:
    """A stale disable must not make a re-added id come back switched off."""
    client.post("/api/sources", json={"url": "https://example.com/feed/", "id": "example-feed"})
    client.patch("/api/sources/example-feed", json={"enabled": False})
    client.delete("/api/sources/example-feed")

    disabled = json.loads((tmp_path / "disabled_sources.json").read_text(encoding="utf-8"))
    assert "example-feed" not in disabled["disabled"]

    again = client.post(
        "/api/sources", json={"url": "https://example.com/feed/", "id": "example-feed"}
    )
    assert again.json()["enabled"] is True


def test_health_reports_version_and_channel_targets(client: TestClient) -> None:
    from telecom_news import __version__

    body = client.get("/api/health").json()
    assert body["version"] == __version__
    assert body["target_langs"] == ["ru"]
    assert body["channel_targets"] == [{"lang": "ru", "chat_id": "-100"}]


def test_gui_offers_add_and_delete(client: TestClient) -> None:
    html = client.get("/").text
    assert "Add source" in html
    assert "btn-add-source" in html
    assert 'id="src-url"' in html
    js = client.get("/static/app.js").text
    assert "source-delete" in js
    assert 'method: "DELETE"' in js


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://www.example.com/feed/", "example-com"),
        ("https://example.com/rss", "example-com"),
        ("https://news.example.co.uk/telecom/feed", "news-example-co-uk-telecom"),
        ("https://example.com/feed/2024/", "example-com"),
    ],
)
def test_slug_from_url(url: str, expected: str) -> None:
    assert slug_from_url(url) == expected


def test_slug_from_url_avoids_collisions() -> None:
    assert slug_from_url("https://example.com/feed/", taken={"example-com"}) == "example-com-2"


def test_add_and_delete_without_the_api(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The overlay functions are usable from the CLI/tests, not only over HTTP."""
    monkeypatch.setenv("TELECOM_NEWS_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("TELECOM_NEWS_DISABLED_SOURCES", raising=False)
    try:
        created = add_source(
            "https://example.net/feed/", language="ru", relevance_gate="llm", data_dir=tmp_path
        )
        assert config_mod.SOURCES[created["id"]].language == "ru"
        delete_source(created["id"], data_dir=tmp_path)
        assert created["id"] not in config_mod.SOURCES
    finally:
        config_mod.reset_sources_to_baseline()


def test_saved_rules_never_shrink_the_built_in_terms(client: TestClient, tmp_path: Path) -> None:
    """M9d: an operator term list widens the defaults instead of replacing them.

    A hand-curated ``ai_rules.json`` without "чат-бот" / "мессендж" used to
    disable the Cyrillic half of the keyword gate (D-011), so every Russian
    messaging article was dropped before the LLM ever saw it.
    """
    from telecom_news.ai_rules import invalidate_cache, load_rules, reset_rules
    from telecom_news.models import Article
    from telecom_news.processors.relevance import has_messaging_signal

    put = client.put(
        "/api/ai-rules",
        json={"messaging_terms": ["sms", "smpp"], "off_topic_terms": ["video conferencing"]},
    )
    assert put.status_code == 200
    body = put.json()
    assert body["defaults_always_merged"] is True

    terms = body["rules"]["messaging_terms"]
    assert "smpp" in terms  # operator addition kept
    assert "чат-бот" in terms and "мессендж" in terms  # defaults kept
    assert "видеозвон" in body["rules"]["off_topic_terms"]

    invalidate_cache()
    assert load_rules(tmp_path).messaging_terms == terms
    russian = Article(
        url="https://example.com/ru",
        source_id="cnews-telecom",
        title="Банк запустил чат-бота для сообщений клиентам",
        body="Подробности в пресс-релизе.",
    )
    assert has_messaging_signal(russian) is True

    reset_rules(tmp_path)
    invalidate_cache()


def test_a_sitemap_source_can_be_added(client: TestClient, tmp_path: Path) -> None:
    """Outlets with no feed are added as a sitemap source (D-026)."""
    created = client.post(
        "/api/sources",
        json={
            "url": "https://outlet.example/news-sitemap.xml",
            "id": "outlet-example",
            "type": "sitemap",
            "language": "en",
        },
    )
    assert created.status_code == 201
    assert created.json()["type"] == "sitemap"
    assert config_mod.SOURCES["outlet-example"].type == "sitemap"

    row = next(
        item
        for item in client.get("/api/sources").json()["sources"]
        if item["id"] == "outlet-example"
    )
    assert row["type"] == "sitemap"
    stored = json.loads((tmp_path / "custom_sources.json").read_text(encoding="utf-8"))
    assert stored["sources"][0]["type"] == "sitemap"


def test_an_unknown_source_type_is_rejected(client: TestClient) -> None:
    response = client.post(
        "/api/sources", json={"url": "https://outlet.example/x", "type": "carrier-pigeon"}
    )
    assert response.status_code == 400
    assert "source type" in response.json()["detail"]
