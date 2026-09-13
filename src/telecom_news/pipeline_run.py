"""In-process pipeline trigger for the control panel (M9c run-now).

Starts ``collect → process → publish → deliver`` (or a subset) in a background
thread so the HTTP request returns immediately. Only one run at a time per
process. Status + log tail live in memory (and a small JSON file under data/).
"""

from __future__ import annotations

import io
import json
import os
import threading
import traceback
from collections.abc import Callable
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_LOCK = threading.RLock()
_WORKER: threading.Thread | None = None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class RunSnapshot:
    status: str = "idle"  # idle | running | ok | error
    project_id: str = ""
    stage: str = "full"  # full | collect | process | publish | deliver | discover
    dry_run: bool = False
    limit: int | None = None
    max_posts: int | None = None
    started_at: str | None = None
    finished_at: str | None = None
    exit_code: int | None = None
    error: str | None = None
    log_tail: str = ""
    log_path: str | None = None
    # Stage-specific settings (the discovery scan takes several); keeping them
    # here beats overloading `dry_run` to mean something else per stage.
    options: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


_STATE = RunSnapshot()
_LOG_BUF = io.StringIO()


def get_run_status() -> dict[str, Any]:
    with _LOCK:
        snap = RunSnapshot(**asdict(_STATE))
        # live tail while running
        if _STATE.status == "running":
            snap.log_tail = _LOG_BUF.getvalue()[-8000:]
        return snap.to_dict()


def _persist(data_dir: Path) -> None:
    path = data_dir / "last_run.json"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(get_run_status(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    except OSError:
        pass


def _set(**kwargs: Any) -> None:
    global _STATE
    with _LOCK:
        for key, value in kwargs.items():
            if hasattr(_STATE, key):
                setattr(_STATE, key, value)


def is_running() -> bool:
    with _LOCK:
        return _STATE.status == "running"


def start_run(
    *,
    project_id: str,
    data_dir: Path,
    db_path: Path,
    stage: str = "full",
    dry_run: bool = False,
    limit: int | None = None,
    max_posts: int | None = None,
    options: dict[str, Any] | None = None,
    runner: Callable[..., int] | None = None,
) -> dict[str, Any]:
    """Start a background pipeline run. Raises ``RuntimeError`` if busy/invalid."""
    global _WORKER, _LOG_BUF, _STATE

    stage = (stage or "full").strip().lower()
    allowed = {"full", "collect", "process", "publish", "deliver", "discover"}
    if stage not in allowed:
        raise ValueError(f"stage must be one of {sorted(allowed)}")
    if limit is not None and limit < 1:
        raise ValueError("limit must be >= 1")
    if max_posts is not None and max_posts < 0:
        raise ValueError("max_posts must be >= 0")

    with _LOCK:
        if _STATE.status == "running":
            raise RuntimeError("pipeline already running")
        _LOG_BUF = io.StringIO()
        log_path = data_dir / "logs" / "run-now.log"
        _STATE = RunSnapshot(
            status="running",
            project_id=project_id,
            stage=stage,
            dry_run=bool(dry_run),
            limit=limit,
            max_posts=max_posts,
            options=dict(options or {}),
            started_at=_now(),
            finished_at=None,
            exit_code=None,
            error=None,
            log_tail="",
            log_path=str(log_path),
        )

    def _target() -> None:
        code = 1
        err: str | None = None
        try:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            # Point legacy single-tenant CLI at this project's DB for the duration.
            old_db = os.environ.get("TELECOM_NEWS_DB")
            os.environ["TELECOM_NEWS_DB"] = str(db_path)
            try:
                with redirect_stdout(_LOG_BUF), redirect_stderr(_LOG_BUF):
                    call = runner if runner is not None else _default_runner
                    code = int(
                        call(
                            stage=stage,
                            dry_run=dry_run,
                            limit=limit,
                            max_posts=max_posts,
                            options=dict(options or {}),
                        )
                    )
            finally:
                if old_db is None:
                    os.environ.pop("TELECOM_NEWS_DB", None)
                else:
                    os.environ["TELECOM_NEWS_DB"] = old_db
        except Exception as exc:  # noqa: BLE001 — surface to operator UI
            err = f"{type(exc).__name__}: {exc}"
            _LOG_BUF.write("\n" + traceback.format_exc())
            code = 1
        tail = _LOG_BUF.getvalue()
        try:
            log_path.write_text(tail, encoding="utf-8")
        except OSError:
            pass
        with _LOCK:
            _STATE.status = "ok" if code == 0 else "error"
            _STATE.exit_code = code
            _STATE.finished_at = _now()
            _STATE.error = err
            _STATE.log_tail = tail[-8000:]
        _persist(data_dir)

    thread = threading.Thread(target=_target, name="telecom-news-run-now", daemon=True)
    with _LOCK:
        _WORKER = thread
    thread.start()
    _persist(data_dir)
    return get_run_status()


def _default_runner(
    *,
    stage: str,
    dry_run: bool,
    limit: int | None,
    max_posts: int | None,
    options: dict[str, Any] | None = None,
) -> int:
    """Call CLI stage helpers (same code path as ``python -m telecom_news run``)."""
    from .cli import _cmd_collect, _cmd_deliver, _cmd_process, _cmd_publish, _cmd_run
    from .config import SOURCES

    if stage == "full":
        return _cmd_run(
            source_id=None,
            limit=limit,
            dry_run=dry_run,
            max_posts=max_posts,
            max_per_subscriber=None,
        )
    if stage == "collect":
        # One broken RSS must not paint the whole run-now red.
        attempted = 0
        failed_ids: list[str] = []
        for source in SOURCES.values():
            if not source.enabled:
                continue
            attempted += 1
            if _cmd_collect(source.id, limit) != 0:
                failed_ids.append(source.id)
        if failed_ids:
            import sys

            print(
                f"warning: collect finished with {len(failed_ids)}/{attempted} "
                f"source failure(s): {', '.join(failed_ids)}",
                file=sys.stderr,
            )
        if attempted == 0:
            return 0
        if len(failed_ids) == attempted:
            return 1
        return 0
    if stage == "process":
        return _cmd_process(limit)
    if stage == "publish":
        return _cmd_publish(max_posts, dry_run)
    if stage == "deliver":
        return _cmd_deliver(None, dry_run)
    if stage == "discover":
        # Proposals only: a scan never changes the source registry by itself.
        from .cli import _cmd_discover

        settings = options or {}
        return _cmd_discover(
            max_sites=limit or 12,
            use_search=bool(settings.get("use_search", True)),
            look_for=str(settings.get("look_for", "both")),
            recheck=bool(settings.get("recheck", False)),
        )
    raise ValueError(f"unknown stage {stage!r}")


def reset_for_tests() -> None:
    """Test helper: clear in-memory run state."""
    global _STATE, _WORKER, _LOG_BUF
    with _LOCK:
        _STATE = RunSnapshot()
        _WORKER = None
        _LOG_BUF = io.StringIO()
