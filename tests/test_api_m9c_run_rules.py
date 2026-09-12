"""M9c: run-now pipeline trigger + AI rules editor API."""

from __future__ import annotations

import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

from telecom_news.ai_rules import invalidate_cache, reset_rules, save_rules
from telecom_news.api.app import create_app
from telecom_news.models import Article
from telecom_news.pipeline_run import reset_for_tests
from telecom_news.processors.relevance import check_relevance
from telecom_news.projects import DEFAULT_PROJECT_ID, ProjectRegistry
from telecom_news.storage.database import Database


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("TELECOM_NEWS_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("TELECOM_NEWS_DISABLED_SOURCES", raising=False)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "-100")
    reset_for_tests()
    registry = ProjectRegistry(tmp_path / "projects" / "registry.json")
    registry.ensure_default()
    db = Database(tmp_path / "news.db")
    now = datetime(2026, 9, 12, 12, 0, tzinfo=timezone.utc)
    db.upsert_by_hash(
        Article(
            url="https://example.com/a1",
            content_hash="h1",
            source_id="sinch-blog",
            title="Sinch SMS API launch",
            body="body",
            status="new",
            fetched_at=now,
            published_at=now,
        )
    )
    app = create_app(registry=registry)
    with TestClient(app) as test_client:
        yield test_client
    reset_for_tests()


def test_ai_rules_get_defaults(client: TestClient) -> None:
    response = client.get("/api/ai-rules")
    assert response.status_code == 200
    data = response.json()
    assert data["overridden"] is False
    assert (
        "SMS" in data["rules"]["system_prompt"] or "sms" in data["rules"]["system_prompt"].lower()
    )
    assert "sms" in data["rules"]["messaging_terms"]


def test_ai_rules_put_and_reset(client: TestClient, tmp_path: Path) -> None:
    put = client.put(
        "/api/ai-rules",
        json={
            "system_prompt": "Custom classifier prompt for tests.",
            "messaging_terms": ["sms", "custom-term-xyz"],
            "off_topic_terms": ["video chat"],
            "force_llm_gate": True,
            "notes": "test note",
        },
    )
    assert put.status_code == 200
    body = put.json()
    assert body["overridden"] is True
    assert body["rules"]["force_llm_gate"] is True
    assert "custom-term-xyz" in body["rules"]["messaging_terms"]
    assert (tmp_path / "ai_rules.json").is_file()

    reset = client.post("/api/ai-rules/reset")
    assert reset.status_code == 200
    assert reset.json()["overridden"] is False
    assert not (tmp_path / "ai_rules.json").exists()


def test_ai_rules_affect_relevance_gate(client: TestClient, tmp_path: Path) -> None:
    """Saving force_llm_gate makes check_relevance skip the keyword guard."""
    reset_rules(tmp_path)
    invalidate_cache()

    class _Fake:
        def __init__(self) -> None:
            self.calls = 0

        def chat(self, messages, **kwargs):  # noqa: ANN001
            self.calls += 1
            return '{"relevant": true, "category": "vendor", "reason": "ok"}'

    # Body must NOT contain default messaging terms (e.g. the word "messaging").
    article = Article(
        url="https://example.com/x",
        source_id="t",
        title="Enterprise cloud platform launch",
        body="Quarterly earnings and data-center expansion only.",
    )
    fake = _Fake()
    gated = check_relevance(fake, article)
    assert gated.relevant is False
    assert fake.calls == 0

    save_rules(
        {
            "system_prompt": "test prompt",
            "messaging_terms": ["sms"],
            "off_topic_terms": ["video chat"],
            "force_llm_gate": True,
        },
        data_dir=tmp_path,
    )
    invalidate_cache()
    result = check_relevance(fake, article)
    assert result.relevant is True
    assert fake.calls == 1
    reset_rules(tmp_path)
    invalidate_cache()


def test_run_now_starts_and_completes(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    done = {"n": 0}

    def fake_runner(**kwargs):  # noqa: ANN003
        done["n"] += 1
        time.sleep(0.05)
        return 0

    monkeypatch.setattr(
        "telecom_news.pipeline_run._default_runner",
        fake_runner,
    )
    response = client.post(
        f"/api/projects/{DEFAULT_PROJECT_ID}/run",
        json={"stage": "process", "dry_run": True, "limit": 5},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "running"

    # second concurrent run → 409
    busy = client.post(
        f"/api/projects/{DEFAULT_PROJECT_ID}/run",
        json={"stage": "process"},
    )
    assert busy.status_code == 409

    # wait for finish
    for _ in range(40):
        status = client.get("/api/run/status").json()
        if status["status"] != "running":
            break
        time.sleep(0.05)
    assert status["status"] == "ok"
    assert status["exit_code"] == 0
    assert done["n"] == 1

    # ops status embeds run
    ops = client.get("/api/ops/status").json()
    assert ops["run"]["status"] == "ok"


def test_run_unknown_project(client: TestClient) -> None:
    response = client.post("/api/projects/no-such/run", json={})
    assert response.status_code == 404


def test_gui_mentions_run_and_rules(client: TestClient) -> None:
    html = client.get("/").text
    assert "Run now" in html
    assert "AI rules" in html
    assert "btn-run-submit" in html
