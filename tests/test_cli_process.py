"""CLI process against a temporary database (M3). LLM is faked, no network."""

from __future__ import annotations

from pathlib import Path

from telecom_news.cli import _cmd_process
from telecom_news.llm.client import LLMUnavailableError
from telecom_news.models import Article
from telecom_news.storage import Database


class _FakeLLM:
    """Replies by prompt kind; 'Irrelevant' titles are judged irrelevant."""

    def ensure_model(self) -> str:
        return "fake-model"

    def chat(self, messages: list[dict[str, str]], **kwargs) -> str:
        system = messages[0]["content"]
        user = messages[1]["content"]
        if "classifier" in system:
            if "Irrelevant" in user:
                return '{"relevant": false, "reason": "general telecom"}'
            return '{"relevant": true, "category": "vendor", "reason": "SMS news"}'
        return '{"summary": "Тестовое саммари."}'


def _seed(db: Database) -> None:
    db.upsert_by_hash(
        Article(
            url="https://example.com/a",
            source_id="t",
            title="Relevant SMS news",
            body="Body A",
        )
    )
    db.upsert_by_hash(
        Article(
            url="https://example.com/b",
            source_id="t",
            title="Irrelevant SMS fiber news",
            body="Body B",
        )
    )


def test_process_transitions_and_persists_llm_result(tmp_path: Path, capsys) -> None:
    db_path = tmp_path / "news.db"
    _seed(Database(db_path))

    assert _cmd_process(None, db_path=db_path, client=_FakeLLM()) == 0
    assert "1 processed, 1 skipped, 0 error(s)" in capsys.readouterr().out

    db = Database(db_path)
    assert db.count_by_status() == {
        "new": 0,
        "processed": 1,
        "published": 0,
        "skipped": 1,
        "error": 0,
    }
    (processed,) = db.get_unprocessed(status="processed")
    assert processed.relevance == "relevant"
    assert processed.category == "vendor"
    assert processed.llm_result is not None
    assert processed.llm_result["summary"] == "Тестовое саммари."
    assert processed.llm_result["summary_language"] == "ru"
    assert processed.llm_result["model"] == "fake-model"
    (skipped,) = db.get_unprocessed(status="skipped")
    assert skipped.relevance == "irrelevant"


def test_process_nothing_to_do(tmp_path: Path, capsys) -> None:
    assert _cmd_process(None, db_path=tmp_path / "news.db", client=_FakeLLM()) == 0
    assert "Nothing to process" in capsys.readouterr().out


def test_process_limit(tmp_path: Path) -> None:
    db_path = tmp_path / "news.db"
    _seed(Database(db_path))
    assert _cmd_process(1, db_path=db_path, client=_FakeLLM()) == 0
    assert Database(db_path).count_by_status()["new"] == 1


def test_process_bad_limit(capsys) -> None:
    assert _cmd_process(0, client=_FakeLLM()) == 2
    assert "--limit must be >= 1" in capsys.readouterr().err


def test_unavailable_llm_aborts_and_keeps_new(tmp_path: Path, capsys) -> None:
    class _Broken:
        def ensure_model(self) -> str:
            raise LLMUnavailableError("connection refused")

    db_path = tmp_path / "news.db"
    _seed(Database(db_path))
    assert _cmd_process(None, db_path=db_path, client=_Broken()) == 1
    assert "LLM unavailable" in capsys.readouterr().err
    assert Database(db_path).count_by_status()["new"] == 2


def test_unavailable_mid_run_keeps_rest_new(tmp_path: Path) -> None:
    class _Flaky(_FakeLLM):
        def __init__(self) -> None:
            self.calls = 0

        def chat(self, messages: list[dict[str, str]], **kwargs) -> str:
            self.calls += 1
            if self.calls > 2:  # relevance + summary of the 1st article pass
                raise LLMUnavailableError("died mid-run")
            return super().chat(messages, **kwargs)

    db_path = tmp_path / "news.db"
    _seed(Database(db_path))
    assert _cmd_process(None, db_path=db_path, client=_Flaky()) == 1
    counts = Database(db_path).count_by_status()
    assert counts["processed"] == 1
    assert counts["new"] == 1


def test_response_error_marks_article_and_continues(tmp_path: Path, capsys) -> None:
    class _Garbled(_FakeLLM):
        def chat(self, messages: list[dict[str, str]], **kwargs) -> str:
            if "Irrelevant" in messages[1]["content"]:
                return "not json at all"
            return super().chat(messages, **kwargs)

    db_path = tmp_path / "news.db"
    _seed(Database(db_path))
    assert _cmd_process(None, db_path=db_path, client=_Garbled()) == 0
    counts = Database(db_path).count_by_status()
    assert counts["processed"] == 1
    assert counts["error"] == 1
    assert "marked 'error'" in capsys.readouterr().err
