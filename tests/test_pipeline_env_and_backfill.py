"""Scheduler environment, channel-rendition backfill and the sources count.

Three operational defects found on the operator's machine on 2026-09-13:
`run_pipeline.sh` ran without the Telegram credentials, adding a channel
language left older articles unpublishable, and `sources` under-reported how
many feeds the pipeline actually polls.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from telecom_news.cli import _cmd_process, _cmd_sources
from telecom_news.config import SOURCES
from telecom_news.models import Article
from telecom_news.storage import Database

REPO_ROOT = Path(__file__).resolve().parent.parent


class _FakeLLM:
    """Relevance says yes, summaries echo the requested language."""

    def __init__(self) -> None:
        self.summary_calls = 0

    def ensure_model(self) -> str:
        return "fake-model"

    def chat(self, messages: list[dict[str, str]], **kwargs) -> str:  # noqa: ANN003
        if "classifier" in messages[0]["content"]:
            return '{"relevant": true, "category": "vendor", "reason": "SMS news"}'
        self.summary_calls += 1
        return '{"summary": "Summary text."}'


# --- scheduler environment -------------------------------------------------


def _sandbox_pipeline(tmp_path: Path, script: str = "run_pipeline.sh") -> Path:
    """Copy of a scheduler script whose `.venv/bin/python` only reports its env."""
    root = tmp_path / "project"
    (root / "scripts").mkdir(parents=True)
    (root / ".venv" / "bin").mkdir(parents=True)
    (root / "scripts" / script).write_text(
        (REPO_ROOT / "scripts" / script).read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    stub = root / ".venv" / "bin" / "python"
    stub.write_text(
        "#!/usr/bin/env bash\n"
        'echo "CHAT_ID=${TELEGRAM_CHAT_ID:-unset}"\n'
        'echo "LANGS=${TELECOM_NEWS_TARGET_LANGS:-unset}"\n'
        'echo "ARGS=$*"\n',
        encoding="utf-8",
    )
    stub.chmod(0o755)
    (root / "scripts" / script).chmod(0o755)
    return root


def _run_pipeline(root: Path, env_file: Path | None, script: str = "run_pipeline.sh") -> str:
    env = {k: v for k, v in os.environ.items() if not k.startswith("TELECOM_NEWS")}
    env.pop("TELEGRAM_CHAT_ID", None)
    env.pop("TELEGRAM_BOT_TOKEN", None)
    # A path that does not exist means "no env file", which is how the guard in
    # the script is switched off.
    env["TELECOM_NEWS_ENV_FILE"] = str(env_file or (root / "no-such-env"))
    subprocess.run(["bash", str(root / "scripts" / script)], env=env, check=True, timeout=60)
    log = "discovery.log" if "discovery" in script else "pipeline.log"
    return (root / "data" / "logs" / log).read_text(encoding="utf-8")


def test_run_pipeline_loads_the_env_file(tmp_path: Path) -> None:
    """Without this the scheduled publish stage had no token and exited 2."""
    root = _sandbox_pipeline(tmp_path)
    env_file = tmp_path / "env"
    env_file.write_text(
        "TELEGRAM_BOT_TOKEN=secret\n"
        "TELEGRAM_CHAT_ID='-100999'\n"
        "TELECOM_NEWS_TARGET_LANGS=ru,en\n"
        "TELECOM_NEWS_LIMIT=7\n",
        encoding="utf-8",
    )
    log = _run_pipeline(root, env_file)
    assert "CHAT_ID=-100999" in log
    assert "LANGS=ru,en" in log
    assert "ARGS=-m telecom_news run --limit 7" in log


def test_run_pipeline_without_an_env_file_still_runs(tmp_path: Path) -> None:
    root = _sandbox_pipeline(tmp_path)
    log = _run_pipeline(root, env_file=None)
    assert "CHAT_ID=unset" in log
    assert "ARGS=-m telecom_news run --limit 10" in log  # documented default


# --- channel rendition backfill --------------------------------------------


def _published_ru_article(db_path: Path) -> int:
    db = Database(db_path)
    article_id, _ = db.upsert_by_hash(
        Article(
            url="https://example.com/published",
            content_hash="published-1",
            source_id="t",
            title="SMS aggregator deal",
            body="Body",
        )
    )
    db.save_processing_result(
        article_id,
        relevance="relevant",
        category="vendor",
        llm_result={"relevant": True, "category": "vendor", "renditions": ["ru"]},
        status="published",
    )
    db.save_rendition(article_id, "ru", title="Заголовок", summary="Саммари")
    return article_id


def test_process_backfills_a_newly_added_channel_language(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    monkeypatch.setenv("TELECOM_NEWS_TARGET_LANGS", "ru,en")
    db_path = tmp_path / "news.db"
    article_id = _published_ru_article(db_path)

    assert _cmd_process(None, db_path=db_path, client=_FakeLLM()) == 0
    out = capsys.readouterr().out
    assert "rendered missing 'en'" in out
    assert "Backfilled 1 missing channel rendition(s)." in out

    db = Database(db_path)
    assert db.rendition_languages(article_id) == ["en", "ru"]


def test_backfill_is_idempotent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    monkeypatch.setenv("TELECOM_NEWS_TARGET_LANGS", "ru,en")
    db_path = tmp_path / "news.db"
    _published_ru_article(db_path)

    llm = _FakeLLM()
    _cmd_process(None, db_path=db_path, client=llm)
    assert llm.summary_calls == 1
    capsys.readouterr()

    # Second run has nothing left to render and must not spend another LLM call.
    assert _cmd_process(None, db_path=db_path, client=llm) == 0
    assert llm.summary_calls == 1
    assert "Nothing to process" in capsys.readouterr().out


def test_single_language_channel_needs_no_backfill(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    monkeypatch.setenv("TELECOM_NEWS_TARGET_LANGS", "ru")
    db_path = tmp_path / "news.db"
    _published_ru_article(db_path)

    llm = _FakeLLM()
    assert _cmd_process(None, db_path=db_path, client=llm) == 0
    assert llm.summary_calls == 0
    assert "Nothing to process" in capsys.readouterr().out


def test_backfill_respects_the_publish_window(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    """Articles publish will never pick up must not cost an LLM call."""
    monkeypatch.setenv("TELECOM_NEWS_TARGET_LANGS", "ru,en")
    monkeypatch.setenv("PUBLISH_MAX_AGE_HOURS", "48")
    db_path = tmp_path / "news.db"
    article_id = _published_ru_article(db_path)
    with Database(db_path)._connect() as conn:
        conn.execute(
            "UPDATE articles SET published_at = '2020-01-01T00:00:00+00:00', "
            "fetched_at = '2020-01-01T00:00:00+00:00' WHERE id = ?",
            (article_id,),
        )

    llm = _FakeLLM()
    assert _cmd_process(None, db_path=db_path, client=llm) == 0
    assert llm.summary_calls == 0
    assert "Nothing to process" in capsys.readouterr().out


def test_backfill_is_capped_per_cycle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    monkeypatch.setenv("TELECOM_NEWS_TARGET_LANGS", "ru,en")
    monkeypatch.setenv("PUBLISH_MAX_PER_CYCLE", "2")
    db_path = tmp_path / "news.db"
    db = Database(db_path)
    for index in range(5):
        article_id, _ = db.upsert_by_hash(
            Article(
                url=f"https://example.com/{index}",
                content_hash=f"hash-{index}",
                source_id="t",
                title=f"SMS story {index}",
                body="Body",
            )
        )
        db.save_processing_result(
            article_id, relevance="relevant", category="vendor", llm_result={}, status="processed"
        )
        db.save_rendition(article_id, "ru", title="Заголовок", summary="Саммари")

    llm = _FakeLLM()
    assert _cmd_process(None, db_path=db_path, client=llm) == 0
    assert llm.summary_calls == 2
    assert "Backfilled 2 missing channel rendition(s)." in capsys.readouterr().out


# --- sources count ----------------------------------------------------------


def test_sources_reports_the_pipeline_registry_not_the_catalog_rows(capsys) -> None:
    assert _cmd_sources() == 0
    line = next(
        line for line in capsys.readouterr().out.splitlines() if "pipeline registry" in line
    )
    enabled = sum(1 for source in SOURCES.values() if source.enabled)
    assert f"{len(SOURCES)} news source(s), {enabled} enabled" in line
    # The catalog holds fewer news entries than the registry (hand-written
    # sources have no catalog row), so the two numbers must not be confused.
    assert enabled > 0


def test_run_discovery_loads_the_env_and_scans(tmp_path: Path) -> None:
    """The scheduled discovery pass needs the same configuration as the pipeline."""
    root = _sandbox_pipeline(tmp_path, script="run_discovery.sh")
    env_file = tmp_path / "env"
    env_file.write_text(
        "TELEGRAM_CHAT_ID='-100999'\nTELECOM_NEWS_TARGET_LANGS=ru,en\n", encoding="utf-8"
    )
    log = _run_pipeline(root, env_file, script="run_discovery.sh")
    assert "CHAT_ID=-100999" in log
    assert "ARGS=-m telecom_news discover --max-sites 25" in log
