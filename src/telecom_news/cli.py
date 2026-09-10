"""CLI entry point (see ARCHITECTURE.md 3.11).

``python -m telecom_news --help`` lists commands. ``collect`` (M1–M2) fetches
one source and stores new articles; ``status`` (M2) shows article counters;
``process`` (M3) runs LLM relevance/category/summary over ``new`` articles.
The ``run`` command exists as an explicit placeholder: it does NOT pretend to
do anything that is not implemented yet (see docs/ROADMAP.md).
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from . import __version__

if TYPE_CHECKING:
    from .llm.client import LLMClient


def _not_implemented(command: str, milestone: str) -> int:
    print(
        f"'{command}' is not implemented yet — the functionality will be "
        f"provided in {milestone} (see docs/ROADMAP.md).",
        file=sys.stderr,
    )
    return 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="telecom_news",
        description=(
            "Daily monitoring and publishing of SMS/messaging industry news. "
            "M5: run executes one collect/process/publish cycle."
        ),
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")

    subparsers = parser.add_subparsers(dest="command")

    run_parser = subparsers.add_parser("run", help="Run collect, process and publish once (M5)")
    run_parser.add_argument(
        "--source", help="Only this source; omit to collect all enabled sources"
    )
    run_parser.add_argument(
        "--dry-run", action="store_true", help="Preview Telegram posts without sending them"
    )
    run_parser.add_argument("--limit", type=int, help="Maximum number of articles per stage")

    subparsers.add_parser("status", help="Show article counters by status")
    subparsers.add_parser("doctor", help="Check database and external dependencies (M7)")

    recover_parser = subparsers.add_parser("recover", help="Queue error articles for retry (M7)")
    recover_parser.add_argument(
        "--limit", type=int, help="Maximum number of error articles to reset"
    )

    backup_parser = subparsers.add_parser("backup", help="Create a consistent SQLite backup (M7)")
    backup_parser.add_argument(
        "--output", type=Path, help="Backup path (default: data/backups/news-<UTC>.db)"
    )

    restore_parser = subparsers.add_parser(
        "restore", help="Restore SQLite from a verified backup (M7)"
    )
    restore_parser.add_argument("--input", required=True, type=Path, help="Backup database path")

    collect_parser = subparsers.add_parser(
        "collect", help="Collect one source and store new articles (M1–M2: RSS only)"
    )
    collect_parser.add_argument(
        "--source", required=True, help="Source id from config (e.g. sinch-blog)"
    )
    collect_parser.add_argument(
        "--limit", type=int, default=None, help="Maximum number of articles to collect"
    )

    process_parser = subparsers.add_parser(
        "process", help="Run LLM relevance/category/summary over 'new' articles (M3)"
    )
    process_parser.add_argument(
        "--limit", type=int, default=None, help="Maximum number of articles to process"
    )

    publish_parser = subparsers.add_parser(
        "publish", help="Publish processed articles to Telegram (M4)"
    )
    publish_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview publication text without network or status changes",
    )
    publish_parser.add_argument(
        "--limit", type=int, default=None, help="Maximum number of articles to publish"
    )

    return parser


def _process_summary(processed: int, skipped: int, errors: int, unfinished: bool = False) -> str:
    suffix = " (run unfinished — LLM unavailable)" if unfinished else ""
    return f"Done: {processed} processed, {skipped} skipped, {errors} error(s){suffix}."


def _cmd_collect(source_id: str, limit: int | None, db_path: Path | None = None) -> int:
    """Collect one source, store new articles, print results (M2).

    Exit codes: 0 = success; 2 = usage error (unknown/disabled source,
    unsupported type, bad --limit); 1 = fetch/parse failure.
    """
    from .collectors import CollectorError, RssCollector
    from .config import SOURCE_TYPE_RSS, SOURCES, get_source, load_config
    from .processors import normalize_item
    from .processors.dedup import store_new
    from .storage.database import Database

    if limit is not None and limit < 1:
        print(f"error: --limit must be >= 1 (got {limit}).", file=sys.stderr)
        return 2
    source = get_source(source_id)
    if source is None:
        known = ", ".join(sorted(SOURCES)) or "(none declared)"
        print(f"error: unknown source '{source_id}'. Known sources: {known}.", file=sys.stderr)
        return 2
    if not source.enabled:
        print(f"error: source '{source_id}' is disabled.", file=sys.stderr)
        return 2
    if source.type != SOURCE_TYPE_RSS:
        print(
            f"error: source '{source_id}' has unsupported type '{source.type}' "
            "(only 'rss' is supported).",
            file=sys.stderr,
        )
        return 2
    collector = RssCollector(source_id=source.id, feed_url=source.url, language=source.language)
    db = Database(db_path or load_config().db_path)
    try:
        raw_items = collector.collect(limit=limit)
    except CollectorError as exc:
        db.record_source_health(source.id, success=False, error=str(exc))
        print(f"error: collect failed for source '{source_id}': {exc}", file=sys.stderr)
        return 1
    db.record_source_health(source.id, success=True, item_count=len(raw_items))
    stored_new = 0
    print(f"Collected {len(raw_items)} article(s) from '{source.id}' ({source.url}):")
    for position, raw in enumerate(raw_items, start=1):
        article = normalize_item(raw)
        article_id, is_new = store_new(db, article)
        stored_new += 1 if is_new else 0
        marker = "new" if is_new else "duplicate"
        published = article.published_at.isoformat() if article.published_at else "unknown date"
        print(f"\n[{position}] {article.title or '(no title)'} [{marker}, id={article_id}]")
        print(f"    url: {article.url}")
        print(
            f"    published: {published} | lang: {article.language} | hash: {article.content_hash}"
        )
    print(f"\nStored {stored_new} new, skipped {len(raw_items) - stored_new} duplicate(s).")
    return 0


def _cmd_status(db_path: Path | None = None) -> int:
    """Show article counters by status (M2)."""
    from .config import load_config
    from .storage.database import STATUSES, Database

    path = db_path or load_config().db_path
    if not path.exists():
        print(f"No database yet at {path} (run 'collect' first).")
        return 0
    counts = Database(path).count_by_status()
    print(f"Articles in {path}:")
    for status in STATUSES:
        print(f"  {status}: {counts.get(status, 0)}")
    for status in sorted(counts):
        if status not in STATUSES:
            print(f"  {status}: {counts[status]} (unexpected)")
    print(f"  total: {sum(counts.values())}")
    health = Database(path).source_health()
    if health:
        print("Source health:")
        for item in health:
            state = "ok" if item["last_error"] is None else f"error: {item['last_error']}"
            print(
                f"  {item['source_id']}: {state}; checked {item['last_checked_at']}; "
                f"items {item['last_item_count']}"
            )
    return 0


def _cmd_process(
    limit: int | None, db_path: Path | None = None, client: LLMClient | None = None
) -> int:
    """Process `new` articles with the LLM: relevance, category, summary (M3).

    Exit codes: 0 = run completed (per-article failures are stored as `error`
    and do not stop the run); 2 = usage error (bad --limit); 1 = the LLM
    service is unavailable (unprocessed articles stay `new`).
    """
    from .config import load_config
    from .llm.client import LLMClient, LLMResponseError, LLMUnavailableError
    from .processors.relevance import check_relevance
    from .processors.summarize import summarize
    from .storage.database import Database

    if limit is not None and limit < 1:
        print(f"error: --limit must be >= 1 (got {limit}).", file=sys.stderr)
        return 2
    config = load_config()
    db = Database(db_path or config.db_path)
    llm = client or LLMClient(
        base_url=config.lmstudio_base_url,
        model=config.lmstudio_model,
    )
    articles = db.get_unprocessed(status="new", limit=limit)
    if not articles:
        print("Nothing to process (no articles with status 'new').")
        return 0
    try:
        model_name = llm.ensure_model()
    except LLMUnavailableError as exc:
        print(f"error: LLM unavailable: {exc}", file=sys.stderr)
        return 1
    processed = skipped = errors = 0

    def _abort(exc: Exception) -> int:
        print(f"error: LLM unavailable, stopping: {exc}", file=sys.stderr)
        print(_process_summary(processed, skipped, errors, unfinished=True))
        return 1

    for article in articles:
        assert article.id is not None, "stored articles always have ids"
        try:
            relevance = check_relevance(llm, article)
        except LLMUnavailableError as exc:
            return _abort(exc)
        except LLMResponseError as exc:
            print(
                f"warning: article id={article.id}: bad relevance reply ({exc}); marked 'error'.",
                file=sys.stderr,
            )
            db.set_status(article.id, "error")
            errors += 1
            continue
        if not relevance.relevant:
            db.save_processing_result(
                article.id,
                relevance="irrelevant",
                category=None,
                llm_result={"relevant": False, "reason": relevance.reason},
                status="skipped",
            )
            skipped += 1
            print(f"[{article.id}] skipped (irrelevant): {article.title or '(no title)'}")
            continue
        try:
            summary = summarize(llm, article, target_lang=config.target_lang)
        except LLMUnavailableError as exc:
            return _abort(exc)
        except LLMResponseError as exc:
            print(
                f"warning: article id={article.id}: bad summary reply ({exc}); marked 'error'.",
                file=sys.stderr,
            )
            db.set_status(article.id, "error")
            errors += 1
            continue
        db.save_processing_result(
            article.id,
            relevance="relevant",
            category=relevance.category,
            llm_result={
                "relevant": True,
                "category": relevance.category,
                "reason": relevance.reason,
                "summary": summary.summary,
                "summary_language": summary.summary_language,
                "model": model_name,
            },
            status="processed",
        )
        processed += 1
        print(f"[{article.id}] processed ({relevance.category}): {article.title or '(no title)'}")
    print(_process_summary(processed, skipped, errors))
    return 0


def _cmd_publish(limit: int | None, dry_run: bool, db_path: Path | None = None) -> int:
    """Publish processed articles, or preview them without credentials/network."""
    from .config import load_config
    from .delivery.telegram import TelegramClient, TelegramError, format_post
    from .storage.database import Database

    if limit is not None and limit < 1:
        print(f"error: --limit must be >= 1 (got {limit}).", file=sys.stderr)
        return 2
    config = load_config()
    db = Database(db_path or config.db_path)
    articles = db.get_unprocessed(status="processed", limit=limit)
    if not articles:
        print("Nothing to publish (no articles with status 'processed').")
        return 0
    if not dry_run and (not config.telegram_bot_token or not config.telegram_chat_id):
        print(
            "error: TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are required (or use --dry-run).",
            file=sys.stderr,
        )
        return 2
    client = (
        None
        if dry_run
        else TelegramClient(
            config.telegram_bot_token,
            min_interval=config.telegram_min_interval,
        )
    )
    errors = 0
    published = 0
    for article in articles:
        try:
            text = format_post(article)
        except TelegramError as exc:
            errors += 1
            db.set_status(article.id or 0, "error")
            print(f"error: article id={article.id}: {exc}; marked 'error'.", file=sys.stderr)
            continue
        if dry_run:
            print(f"--- article id={article.id} ---")
            print(text)
            continue
        try:
            assert client is not None
            client.send_message(config.telegram_chat_id, text)
            assert article.id is not None
            if db.mark_published(article.id):
                published += 1
                print(f"[{article.id}] published: {article.title or '(no title)'}")
            else:
                print(f"warning: article id={article.id} was no longer processed", file=sys.stderr)
        except TelegramError as exc:
            errors += 1
            print(f"error: article id={article.id}: {exc}", file=sys.stderr)
        except (AssertionError, KeyError, TypeError, ValueError) as exc:
            errors += 1
            db.set_status(article.id or 0, "error")
            print(f"error: article id={article.id}: {exc}; marked 'error'.", file=sys.stderr)
    if dry_run:
        print(f"Dry run: {len(articles)} article(s) previewed.")
        return 0
    print(f"Done: {published} published, {errors} error(s).")
    return 1 if errors else 0


def _cmd_run(source_id: str | None, limit: int | None, dry_run: bool) -> int:
    """Run one pipeline cycle across enabled sources, then process and publish."""
    from .config import SOURCES

    source_ids = (
        [source_id] if source_id else [item.id for item in SOURCES.values() if item.enabled]
    )
    recovery_code = _cmd_recover()
    if recovery_code != 0:
        return recovery_code
    collection_failed = False
    for current_source in source_ids:
        if _cmd_collect(current_source, limit) != 0:
            collection_failed = True

    process_code = _cmd_process(limit)
    publish_code = _cmd_publish(limit, dry_run)
    return 1 if collection_failed or process_code != 0 or publish_code != 0 else 0


def _cmd_recover(limit: int | None = None) -> int:
    """Move error articles back to new so the next run can retry them."""
    from .config import load_config
    from .storage.database import Database

    if limit is not None and limit < 1:
        print(f"error: --limit must be >= 1 (got {limit}).", file=sys.stderr)
        return 2
    count = Database(load_config().db_path).reset_errors(limit)
    print(f"Recovered {count} error article(s) for retry.")
    return 0


def _cmd_backup(output: Path | None = None) -> int:
    """Create and verify a consistent SQLite backup."""
    from datetime import datetime, timezone

    from .config import load_config
    from .storage.database import Database

    config = load_config()
    destination = output or (
        config.data_dir / "backups" / f"news-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.db"
    )
    db = Database(config.db_path)
    db.backup_to(destination)
    integrity = Database(destination).integrity_check()
    if integrity != "ok":
        print(f"error: backup integrity check failed: {integrity}", file=sys.stderr)
        return 1
    print(f"Backup created: {destination} (integrity: {integrity})")
    return 0


def _cmd_restore(input_path: Path) -> int:
    """Restore a verified backup into the configured live database."""
    from .config import load_config
    from .storage.database import Database

    destination = load_config().db_path
    try:
        Database.restore_from(input_path, destination)
    except (FileNotFoundError, OSError, ValueError) as exc:
        print(f"error: restore failed: {exc}", file=sys.stderr)
        return 1
    print(f"Database restored: {destination}")
    return 0


def _cmd_doctor(db_path: Path | None = None) -> int:
    """Check local storage and all configured external dependencies (M7)."""
    import httpx

    from .collectors import CollectorError, RssCollector
    from .config import SOURCE_TYPE_RSS, SOURCES, load_config
    from .llm.client import LLMClient, LLMUnavailableError
    from .storage.database import Database

    config = load_config()
    path = db_path or config.db_path
    checks: list[tuple[str, bool, str]] = []

    try:
        db = Database(path)
        db.count_by_status()
        checks.append(("database", True, str(path)))
    except (OSError, sqlite3.Error) as exc:
        checks.append(("database", False, str(exc)))
        db = None

    if not config.telegram_bot_token or not config.telegram_chat_id:
        checks.append(("telegram config", False, "TELEGRAM_BOT_TOKEN/CHAT_ID missing"))
    else:
        try:
            url = f"https://api.telegram.org/bot{config.telegram_bot_token}/getMe"
            with httpx.Client(timeout=10.0) as client:
                response = client.get(url)
            data = response.json()
            ok = response.is_success and data.get("ok") is True
            detail = (
                "Bot API reachable" if ok else str(data.get("description", response.status_code))
            )
            checks.append(("telegram", ok, detail))
        except (httpx.HTTPError, ValueError) as exc:
            checks.append(("telegram", False, str(exc)))

    try:
        model = LLMClient(
            base_url=config.lmstudio_base_url,
            model=config.lmstudio_model,
            max_retries=1,
        ).ensure_model()
        checks.append(("lmstudio", True, model))
    except LLMUnavailableError as exc:
        checks.append(("lmstudio", False, str(exc)))

    if db is not None:
        for source in SOURCES.values():
            if not source.enabled:
                continue
            if source.type != SOURCE_TYPE_RSS:
                checks.append((f"source:{source.id}", False, "unsupported type"))
                continue
            try:
                items = RssCollector(
                    source_id=source.id, feed_url=source.url, language=source.language
                ).collect(limit=1)
                db.record_source_health(source.id, success=True, item_count=None)
                checks.append((f"source:{source.id}", True, f"{len(items)} item(s)"))
            except CollectorError as exc:
                db.record_source_health(source.id, success=False, error=str(exc))
                checks.append((f"source:{source.id}", False, str(exc)))

    failed = False
    for name, ok, detail in checks:
        print(f"[{'OK' if ok else 'FAIL'}] {name}: {detail}")
        failed = failed or not ok
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Returns a process exit code."""
    from .config import load_config
    from .logging_config import setup_logging

    config = load_config()
    setup_logging(config.log_level)

    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help()
        return 0
    if args.command == "run":
        return _cmd_run(args.source, args.limit, args.dry_run)
    if args.command == "status":
        return _cmd_status()
    if args.command == "doctor":
        return _cmd_doctor()
    if args.command == "recover":
        return _cmd_recover(args.limit)
    if args.command == "backup":
        return _cmd_backup(args.output)
    if args.command == "restore":
        return _cmd_restore(args.input)
    if args.command == "collect":
        return _cmd_collect(args.source, args.limit)
    if args.command == "process":
        return _cmd_process(args.limit)
    if args.command == "publish":
        return _cmd_publish(args.limit, args.dry_run)

    parser.print_help()
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
