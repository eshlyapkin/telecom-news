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
    from datetime import datetime

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

    deliver_parser = subparsers.add_parser(
        "deliver", help="Send articles to subscribers per selected language (M8)"
    )
    deliver_parser.add_argument(
        "--dry-run", action="store_true", help="Preview subscriber messages without sending"
    )
    deliver_parser.add_argument(
        "--limit", type=int, default=None, help="Maximum messages per subscriber"
    )

    bot_parser = subparsers.add_parser(
        "bot", help="Poll Telegram for /start, /language, /status, /stop (M8)"
    )
    bot_parser.add_argument("--once", action="store_true", help="One getUpdates round, then exit")
    bot_parser.add_argument(
        "--poll-timeout", type=int, default=25, help="Long-polling timeout in seconds"
    )

    sources_parser = subparsers.add_parser(
        "sources", help="List the source catalog and, optionally, verify it live (D-016)"
    )
    sources_parser.add_argument(
        "--kind", choices=("news", "status", "all"), default="news", help="Which entries to show"
    )
    sources_parser.add_argument("--enabled-only", action="store_true", help="Only enabled entries")
    sources_parser.add_argument("--json", action="store_true", help="Print JSON")
    sources_parser.add_argument(
        "--verify", action="store_true", help="Fetch every enabled news source once (needs network)"
    )
    sources_parser.add_argument(
        "--verify-limit", type=int, default=None, help="Maximum number of sources to verify"
    )

    sources_sub = sources_parser.add_subparsers(dest="sources_command")
    import_parser = sources_sub.add_parser(
        "import", help="Rebuild sources_catalog.py from an exported CSV/XLSX table"
    )
    import_parser.add_argument("--csv", required=True, type=Path, help="Table with the endpoints")
    import_parser.add_argument(
        "--output", type=Path, help="Target module (default: sources_catalog.py)"
    )
    import_parser.add_argument("--dry-run", action="store_true", help="Print what would be written")

    subparsers.add_parser("status", help="Show article counters by status")
    subparsers.add_parser("doctor", help="Check database and external dependencies (M7)")

    diagnose_parser = subparsers.add_parser(
        "diagnose", help="Explain why nothing is being published (read-only)"
    )
    diagnose_parser.add_argument(
        "--runs", type=int, default=5, help="How many recent scheduler runs to analyze"
    )
    diagnose_parser.add_argument(
        "--offline", action="store_true", help="Skip the LM Studio and Telegram probes"
    )
    diagnose_parser.add_argument("--json", action="store_true", help="Print JSON instead of text")

    recover_parser = subparsers.add_parser(
        "recover", help="Queue error articles for retry (M7, bounded by attempts since D-015)"
    )
    recover_parser.add_argument(
        "--limit", type=int, help="Maximum number of error articles to reset"
    )
    recover_parser.add_argument(
        "--max-attempts",
        type=int,
        default=3,
        help="Skip articles that already failed this many times (0 = retry everything)",
    )

    backup_parser = subparsers.add_parser("backup", help="Create a consistent SQLite backup (M7)")
    backup_parser.add_argument(
        "--output", type=Path, help="Backup path (default: data/backups/news-<UTC>.db)"
    )
    backup_parser.add_argument(
        "--keep-days",
        type=int,
        default=14,
        help="Delete automatic backups older than this many days",
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
    collect_parser.add_argument(
        "--max-age-days",
        type=int,
        default=None,
        help="Ignore items older than N days (0 = keep everything; "
        "default: TELECOM_NEWS_MAX_ARTICLE_AGE_DAYS, 30)",
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


def _cmd_collect(
    source_id: str,
    limit: int | None,
    max_age_days: int | None = None,
    db_path: Path | None = None,
) -> int:
    """Collect one source, store new articles, print results (M2).

    Exit codes: 0 = success; 2 = usage error (unknown/disabled source,
    unsupported type, bad --limit); 1 = fetch/parse failure.
    """
    from .collectors import CollectorError, RssCollector
    from .config import SOURCE_TYPE_RSS, SOURCES, get_source, load_config
    from .processors import normalize_item
    from .processors.dedup import store_new
    from .processors.freshness import is_stale
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
    config = load_config()
    age_limit = config.article_max_age_days if max_age_days is None else max_age_days
    collector = RssCollector(source_id=source.id, feed_url=source.url, language=source.language)
    db = Database(db_path or config.db_path)
    try:
        raw_items = collector.collect(limit=limit)
    except CollectorError as exc:
        db.record_source_health(source.id, success=False, error=str(exc))
        print(f"error: collect failed for source '{source_id}': {exc}", file=sys.stderr)
        return 1
    db.record_source_health(source.id, success=True, item_count=len(raw_items))
    stored_new = 0
    stale = 0
    print(f"Collected {len(raw_items)} article(s) from '{source.id}' ({source.url}):")
    for position, raw in enumerate(raw_items, start=1):
        article = normalize_item(raw)
        if is_stale(article.published_at, age_limit):
            stale += 1
            assert article.published_at is not None
            print(
                f"\n[{position}] {article.title or '(no title)'} "
                f"[ignored: older than {age_limit} day(s)]"
            )
            print(f"    url: {article.url}")
            print(f"    published: {article.published_at.isoformat()} | lang: {article.language}")
            continue
        article_id, is_new = store_new(db, article)
        stored_new += 1 if is_new else 0
        marker = "new" if is_new else "duplicate"
        published = article.published_at.isoformat() if article.published_at else "unknown date"
        print(f"\n[{position}] {article.title or '(no title)'} [{marker}, id={article_id}]")
        print(f"    url: {article.url}")
        print(
            f"    published: {published} | lang: {article.language} | hash: {article.content_hash}"
        )
    duplicates = len(raw_items) - stored_new - stale
    print(f"\nStored {stored_new} new, skipped {duplicates} duplicate(s), ignored {stale} stale.")
    if stale and age_limit:
        print(
            f"Note: {stale} item(s) older than {age_limit} day(s) were not stored "
            "(TELECOM_NEWS_MAX_ARTICLE_AGE_DAYS, 0 = keep everything)."
        )
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


def _cmd_sources(
    kind: str = "news",
    enabled_only: bool = False,
    as_json: bool = False,
    verify: bool = False,
    verify_limit: int | None = None,
    db_path: Path | None = None,
) -> int:
    """Show the source catalog; ``--verify`` fetches each enabled news feed once.

    The catalog holds both editorial feeds and status-page endpoints (D-016):
    ``--kind news`` (default) lists what the pipeline actually reads, ``--kind
    status`` lists declared-but-unused operational endpoints.
    """
    from .config import SOURCES
    from .sources_catalog import CATALOG, catalog_counts, catalog_issues

    if verify_limit is not None and verify_limit < 1:
        print(f"error: --verify-limit must be >= 1 (got {verify_limit}).", file=sys.stderr)
        return 2
    entries = list(CATALOG)
    rows = [
        {
            "id": entry.id,
            "kind": entry.kind,
            "url": entry.url,
            "language": entry.language,
            "grade": entry.grade,
            "gate": entry.gate,
            "enabled": (SOURCES[entry.id].enabled if entry.id in SOURCES else entry.enabled),
            "verified": entry.verified,
            "topic": entry.topic,
        }
        for entry in entries
        if kind == "all" or entry.kind == kind
    ]
    if enabled_only:
        rows = [row for row in rows if row["enabled"]]
    enabled_count = sum(1 for row in rows if row["enabled"])
    issues = catalog_issues(entries)
    counts = catalog_counts(entries)

    if as_json:
        import json as json_module

        print(
            json_module.dumps(
                {"counts": counts, "issues": issues, "entries": rows, "in_pipeline": len(SOURCES)},
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        print(
            f"Catalog: {sum(counts['kind'].values())} endpoint(s) — "
            + ", ".join(f"{key}: {value}" for key, value in sorted(counts["kind"].items()))
        )
        print(
            "By language: "
            + ", ".join(f"{key}: {value}" for key, value in sorted(counts["language"].items()))
            + " | by grade: "
            + ", ".join(f"{key}: {value}" for key, value in sorted(counts["grade"].items()))
            + f" | pipeline registry: {len(SOURCES)} news source(s), {enabled_count} enabled"
        )
        if issues:
            print("Catalog issues:")
            for issue in issues:
                print(f"  ! {issue}")
        print(f"\n{'id':<26}{'kind':<8}{'lang':<6}{'grade':<7}{'gate':<8}{'on':<4}url")
        for row in rows:
            print(
                f"{row['id']:<26}{row['kind']:<8}{row['language']:<6}{row['grade']:<7}"
                f"{row['gate']:<8}{'yes' if row['enabled'] else 'no':<4}{row['url']}"
            )

    if not verify:
        return 1 if issues else 0
    return _verify_sources(limit=verify_limit, db_path=db_path)


def _verify_sources(limit: int | None = None, db_path: Path | None = None) -> int:
    """Fetch every enabled news source once and report reachability."""
    from .collectors import CollectorError, RssCollector
    from .config import SOURCES, load_config
    from .storage.database import Database

    db = Database(db_path or load_config().db_path)
    checked = failed = 0
    for source in SOURCES.values():
        if not source.enabled:
            continue
        if limit is not None and checked + failed >= limit:
            break
        try:
            items = RssCollector(
                source_id=source.id, feed_url=source.url, language=source.language
            ).collect(limit=1)
            db.record_source_health(source.id, success=True, item_count=len(items))
            checked += 1
            published = (
                items[0].published_at.isoformat() if items and items[0].published_at else "?"
            )
            print(f"[OK]   {source.id:<26} {len(items)} item(s), newest {published}")
        except CollectorError as exc:
            db.record_source_health(source.id, success=False, error=str(exc))
            failed += 1
            print(f"[FAIL] {source.id:<26} {exc}")
    print(f"\nVerified {checked} source(s): {checked} ok, {failed} failed.")
    return 1 if failed else 0


def _cmd_sources_import(csv_path: Path, output: Path | None = None, dry_run: bool = False) -> int:
    """Rebuild ``sources_catalog.py`` from the exported source research table."""
    from datetime import datetime, timezone

    from .config import SOURCES
    from .source_import import parse_rows, read_table, render_module
    from .sources_catalog import CATALOG

    target = output or Path(__file__).resolve().parent / "sources_catalog.py"
    try:
        rows = read_table(csv_path)
    except (FileNotFoundError, RuntimeError) as exc:
        print(f"error: cannot read the table: {exc}", file=sys.stderr)
        return 2
    # Only hand-written entries of config.py win over the table. Entries that
    # came from the catalog itself must not exclude their own re-import.
    catalog_ids = {entry.id for entry in CATALOG}
    curated_ids = set(SOURCES) - catalog_ids
    try:
        report = parse_rows(
            rows,
            existing_urls={source.url for source in SOURCES.values() if source.id in curated_ids},
            reserved_ids=curated_ids,
        )
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    news = [entry for entry in report.entries if entry.kind == "news"]
    status = [entry for entry in report.entries if entry.kind == "status"]
    per_sheet: dict[str, int] = {}
    for row in rows:
        sheet = str(row.get("__sheet") or "(csv)")
        per_sheet[sheet] = per_sheet.get(sheet, 0) + 1
    print(
        f"Parsed {len(rows)} row(s) from {len(per_sheet)} sheet(s): {len(news)} news feed(s), "
        f"{len(status)} status endpoint(s), {report.skipped_count} skipped."
    )
    for sheet, count in per_sheet.items():
        print(f"  {sheet}: {count} row(s)")
    for label, reason in report.skipped[:10]:
        print(f"  - skipped {label[:70]}: {reason[:80]}")
    if len(report.skipped) > 10:
        print(f"  - ... and {len(report.skipped) - 10} more")
    generated = render_module(
        report.entries,
        generated_from=str(csv_path),
        generated_at=datetime.now(timezone.utc).date().isoformat(),
    )
    if dry_run:
        print(f"\nDry run: would write {len(report.entries)} entries to {target}")
        print(generated[:1200] + ("\n…" if len(generated) > 1200 else ""))
        return 0
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(generated, encoding="utf-8")
    print(f"Catalog written: {target} ({len(report.entries)} entries)")
    print("Review the diff, then run: python -m telecom_news sources --kind all")
    return 0


def _cmd_diagnose(
    *,
    runs: int = 5,
    offline: bool = False,
    as_json: bool = False,
    db_path: Path | None = None,
    now: datetime | None = None,
) -> int:
    """Explain why nothing is being published (read-only, M7+).

    Gathers evidence from the SQLite store, source health and the scheduler
    log, optionally probes LM Studio and Telegram, then prints a verdict.
    Exit codes: 0 = no blocking problem found, 1 = a blocking problem found,
    2 = usage error.
    """
    import sqlite3
    from datetime import datetime, timezone

    from .config import SOURCES, load_config
    from .diagnostics import (
        Facts,
        analyze,
        parse_pipeline_log,
        probe_llm,
        probe_telegram,
        render,
        report_json,
    )
    from .storage.database import Database

    if runs < 1:
        print(f"error: --runs must be >= 1 (got {runs}).", file=sys.stderr)
        return 2
    config = load_config()
    path = db_path or config.db_path
    moment = now or datetime.now(timezone.utc)

    counts: dict[str, int] = {}
    parked_errors = 0
    last_published_at = None
    last_published_title = ""
    oldest_new_at = None
    oldest_processed_at = None
    health: list[dict] = []
    db_exists = path.exists()
    if db_exists:
        try:
            db = Database(path)
            counts = db.count_by_status()
            last = db.last_published()
            if last is not None:
                last_published_at = last.published_at_telegram or last.fetched_at
                last_published_title = last.title
            oldest_new = db.oldest_with_status("new")
            if oldest_new is not None:
                oldest_new_at = oldest_new.fetched_at or oldest_new.published_at
            oldest_processed = db.oldest_with_status("processed")
            if oldest_processed is not None:
                oldest_processed_at = oldest_processed.fetched_at or oldest_processed.published_at
            parked_errors = db.parked_errors(max_attempts=3)
            health = db.source_health()
        except (sqlite3.Error, OSError) as exc:
            print(f"warning: cannot read the database: {exc}", file=sys.stderr)
            db_exists = False
    failing = tuple(
        (str(item["source_id"]), str(item["last_error"]))
        for item in health
        if item.get("last_error")
    )

    log_path = config.logs_dir / "pipeline.log"
    log_modified_at = None
    log_runs = ()
    if log_path.exists():
        log_modified_at = datetime.fromtimestamp(log_path.stat().st_mtime, tz=timezone.utc)
        log_runs = tuple(parse_pipeline_log(log_path.read_text(errors="replace"), limit=runs))
    else:
        log_path = None

    llm_ok: bool | None = None
    llm_detail = ""
    telegram_ok: bool | None = None
    telegram_detail = ""
    if not offline:
        llm_ok, llm_detail = probe_llm(config.lmstudio_base_url)
        if config.telegram_bot_token:
            telegram_ok, telegram_detail = probe_telegram(config.telegram_bot_token)
        else:
            telegram_ok, telegram_detail = False, "TELEGRAM_BOT_TOKEN is empty"

    facts = Facts(
        now=moment,
        db_exists=db_exists,
        counts=counts,
        last_published_at=last_published_at,
        last_published_title=last_published_title,
        oldest_new_at=oldest_new_at,
        oldest_processed_at=oldest_processed_at,
        enabled_sources=sum(1 for source in SOURCES.values() if source.enabled),
        failing_sources=failing,
        parked_errors=parked_errors,
        telegram_configured=bool(config.telegram_bot_token and config.telegram_chat_id),
        log_path=log_path,
        log_modified_at=log_modified_at,
        runs=log_runs,
        llm_ok=llm_ok,
        llm_detail=llm_detail,
        telegram_ok=telegram_ok,
        telegram_detail=telegram_detail,
    )
    report = analyze(facts)
    print(report_json(report) if as_json else render(report, facts, show_runs=runs))
    return 1 if report.blocking else 0


def _cmd_process(
    limit: int | None, db_path: Path | None = None, client: LLMClient | None = None
) -> int:
    """Process `new` articles with the LLM: relevance, category, summary (M3).

    Exit codes: 0 = run completed (per-article failures are stored as `error`
    and do not stop the run); 2 = usage error (bad --limit); 1 = the LLM
    service is unavailable (unprocessed articles stay `new`).
    """
    from .config import get_source, load_config
    from .llm.client import LLMClient, LLMResponseError, LLMUnavailableError
    from .processors.relevance import check_relevance
    from .processors.renditions import ensure_rendition
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
        source = get_source(article.source_id)
        use_gate = source is None or source.relevance_gate != "llm"
        try:
            relevance = check_relevance(llm, article, use_gate=use_gate)
        except LLMUnavailableError as exc:
            return _abort(exc)
        except LLMResponseError as exc:
            attempts = db.mark_error(article.id)
            print(
                f"warning: article id={article.id}: bad relevance reply ({exc}); "
                f"marked 'error' (attempt {attempts}).",
                file=sys.stderr,
            )
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
        renditions = []
        for lang in config.target_langs:
            try:
                renditions.append(ensure_rendition(db, llm, article, lang, model_name=model_name))
            except LLMUnavailableError as exc:
                return _abort(exc)
            except LLMResponseError as exc:
                print(
                    f"warning: article id={article.id}: bad summary reply for '{lang}' ({exc}).",
                    file=sys.stderr,
                )
        if not renditions:
            attempts = db.mark_error(article.id)
            errors += 1
            print(
                f"warning: article id={article.id}: no rendition produced; "
                f"marked 'error' (attempt {attempts}).",
                file=sys.stderr,
            )
            continue
        primary = renditions[0]
        db.save_processing_result(
            article.id,
            relevance="relevant",
            category=relevance.category,
            llm_result={
                "relevant": True,
                "category": relevance.category,
                "reason": relevance.reason,
                "summary": primary.summary,
                "summary_language": primary.lang,
                "renditions": [rendition.lang for rendition in renditions],
                "model": model_name,
            },
            status="processed",
        )
        processed += 1
        langs = ",".join(rendition.lang for rendition in renditions)
        print(
            f"[{article.id}] processed ({relevance.category}, {langs}): "
            f"{article.title or '(no title)'}"
        )
    print(_process_summary(processed, skipped, errors))
    return 0


def _cmd_publish(limit: int | None, dry_run: bool, db_path: Path | None = None) -> int:
    """Publish processed articles to the configured channel(s), one post per language.

    Idempotency (M8): each successful ``(chat_id, article, lang)`` send is recorded
    in ``deliveries``, so a rerun sends exactly what is still missing (for example
    a second language whose first attempt failed) and never posts twice. An article
    becomes ``published`` once every channel target has received it.
    """
    from .config import load_config
    from .delivery.telegram import TelegramClient, TelegramError, format_post
    from .storage.database import Database

    if limit is not None and limit < 1:
        print(f"error: --limit must be >= 1 (got {limit}).", file=sys.stderr)
        return 2
    config = load_config()
    db = Database(db_path or config.db_path)
    targets = list(config.channel_chat_ids)
    if not targets and dry_run:
        targets = [(config.target_lang, "(dry-run)")]
    if not targets:
        print(
            "error: TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are required (or use --dry-run).",
            file=sys.stderr,
        )
        return 2
    candidates = db.recent_articles(statuses=("processed", "published"))
    sent = db.sent_deliveries()
    tracked_articles = {article_id for _chat_id, article_id, _lang in sent}
    client = (
        None
        if dry_run
        else TelegramClient(config.telegram_bot_token, min_interval=config.telegram_min_interval)
    )

    errors = 0
    published = 0
    previewed = 0
    show_flag = len(targets) > 1
    for article in candidates:
        assert article.id is not None, "stored articles always have ids"
        pending = [
            (lang, chat_id)
            for lang, chat_id in targets
            if (str(chat_id), article.id, lang) not in sent
        ]
        if not pending:
            continue
        if article.status == "published" and article.id not in tracked_articles:
            # Legacy rows: published before the `deliveries` table existed (pre-M8
            # database) have no delivery records at all. Sending them again would
            # repost old news into the channel, so they are recorded as delivered
            # instead. Articles published in a language nobody targets yet are not
            # affected: they carry at least one delivery row.
            for lang, chat_id in pending:
                db.record_delivery(str(chat_id), article.id, lang, status="sent")
            print(f"[{article.id}] already published before delivery tracking (no re-send)")
            continue
        if limit is not None and previewed >= limit:
            break
        previewed += 1
        completions: list[bool] = []
        for lang, chat_id in pending:
            # Renditions are produced during `process` for every channel language;
            # publish never calls the LLM (content and delivery stay separated).
            cached = db.get_rendition(article.id, lang)
            legacy = (article.llm_result or {}).get("summary")
            legacy_lang = (article.llm_result or {}).get("summary_language")
            if cached is not None:
                summary = str(cached.get("summary") or "")
            elif legacy and (legacy_lang or config.target_lang) == lang:
                # Row processed before the renditions table existed (pre-M8 DB).
                summary = str(legacy)
            else:
                summary = ""
            if not summary:
                errors += 1
                completions.append(False)
                print(
                    f"error: article id={article.id} has no '{lang}' rendition; "
                    "run 'process' first.",
                    file=sys.stderr,
                )
                if not db.rendition_languages(article.id):
                    db.mark_error(article.id)
                continue
            if dry_run:
                print(f"--- article id={article.id} lang={lang} -> {chat_id} ---")
                print(format_post(article, lang=lang, summary=summary, show_flag=show_flag))
                completions.append(True)
                continue
            try:
                assert client is not None
                result = client.send_message(
                    str(chat_id),
                    format_post(article, lang=lang, summary=summary, show_flag=show_flag),
                )
                message_id = (result.get("result") or {}).get("message_id")
                db.record_delivery(
                    str(chat_id), article.id, lang, status="sent", message_id=message_id
                )
                completions.append(True)
                print(f"[{article.id}] published ({lang}): {article.title or '(no title)'}")
            except TelegramError as exc:
                errors += 1
                completions.append(False)
                db.record_delivery(str(chat_id), article.id, lang, status="failed", error=str(exc))
                print(f"error: article id={article.id} [{lang}]: {exc}", file=sys.stderr)
        if not dry_run and completions and all(completions):
            if db.mark_published(article.id):
                published += 1
    if dry_run:
        print(f"Dry run: {previewed} article(s) previewed.")
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
    deliver_code = _cmd_deliver(limit, dry_run)
    failed = collection_failed or process_code != 0 or publish_code != 0 or deliver_code != 0
    return 1 if failed else 0


def _cmd_deliver(limit: int | None, dry_run: bool, db_path: Path | None = None) -> int:
    """Send processed articles to subscribers, one message per selected language.

    Renditions for languages nobody publishes to a channel are generated lazily
    here and cached in ``renditions`` (M8). Failures are recorded, retried in the
    next cycle (up to ``SUBSCRIBER_MAX_ATTEMPTS``) and never duplicate a message
    that already went out.
    """
    from datetime import datetime, timedelta, timezone

    from .config import load_config
    from .delivery.planner import plan_deliveries
    from .delivery.telegram import TelegramClient, TelegramError, format_post
    from .llm.client import LLMClient, LLMResponseError, LLMUnavailableError
    from .processors.renditions import ensure_rendition
    from .storage.database import Database

    if limit is not None and limit < 1:
        print(f"error: --limit must be >= 1 (got {limit}).", file=sys.stderr)
        return 2
    config = load_config()
    db = Database(db_path or config.db_path)
    subscribers = db.subscribers_with_languages()
    if not subscribers:
        print("No active subscribers yet — start the bot and press /start.")
        return 0
    now = datetime.now(timezone.utc)
    articles = db.recent_articles(
        statuses=("processed", "published"),
        since=now - timedelta(hours=config.subscriber_max_age_hours),
    )
    max_per_user = limit if limit is not None else config.subscriber_max_per_cycle
    plans = plan_deliveries(
        articles,
        subscribers,
        db.sent_deliveries(),
        now=now,
        max_age_hours=config.subscriber_max_age_hours,
        max_per_user=max_per_user,
        max_attempts=int(config.subscriber_max_attempts),
        attempts_of=lambda chat_id, article_id, lang: db.failed_delivery_attempts(
            chat_id=chat_id, article_id=article_id, lang=lang
        ),
    )
    if not plans:
        print("Nothing to deliver.")
        return 0
    if not dry_run and not config.telegram_bot_token:
        print("error: TELEGRAM_BOT_TOKEN is required (or use --dry-run).", file=sys.stderr)
        return 2
    client = (
        None
        if dry_run
        else TelegramClient(config.telegram_bot_token, min_interval=config.telegram_min_interval)
    )
    llm: LLMClient | None = None
    model_name = ""
    if not dry_run:
        llm = LLMClient(base_url=config.lmstudio_base_url, model=config.lmstudio_model)
        try:
            model_name = llm.ensure_model()
        except LLMUnavailableError as exc:
            print(
                f"error: LLM unavailable, cannot render subscriber languages: {exc}",
                file=sys.stderr,
            )
            return 1
    delivered = failed = blocked = 0
    for plan in plans:
        article_id = plan.article.id
        assert article_id is not None
        if dry_run:
            cached = db.get_rendition(article_id, plan.lang)
            print(f"--- chat={plan.chat_id} lang={plan.lang} article={article_id} ---")
            if cached:
                print(
                    format_post(
                        plan.article,
                        lang=plan.lang,
                        summary=str(cached["summary"]),
                        show_flag=len(subscribers[plan.chat_id]) > 1,
                    )
                )
            else:
                print(f"  (rendition for '{plan.lang}' is not generated yet)")
            continue
        assert client is not None and llm is not None
        try:
            rendition = ensure_rendition(db, llm, plan.article, plan.lang, model_name=model_name)
            text = format_post(
                plan.article,
                lang=plan.lang,
                summary=rendition.summary,
                show_flag=len(subscribers[plan.chat_id]) > 1,
            )
            result = client.send_message(plan.chat_id, text)
            message_id = (result.get("result") or {}).get("message_id")
            db.record_delivery(
                plan.chat_id, article_id, plan.lang, status="sent", message_id=message_id
            )
            delivered += 1
            print(f"[{article_id}] delivered to {plan.chat_id} ({plan.lang})")
        except LLMUnavailableError as exc:
            print(f"error: LLM unavailable: {exc}", file=sys.stderr)
            return 1
        except (TelegramError, LLMResponseError) as exc:
            failed += 1
            db.record_delivery(plan.chat_id, article_id, plan.lang, status="failed", error=str(exc))
            if isinstance(exc, TelegramError) and exc.blocked_by_user:
                db.set_subscriber_status(plan.chat_id, "blocked")
                blocked += 1
            print(
                f"warning: article id={article_id} -> {plan.chat_id} ({plan.lang}): {exc}",
                file=sys.stderr,
            )
    if dry_run:
        print(f"Dry run: {len(plans)} message(s) planned.")
        return 0
    print(f"Done: {delivered} delivered, {failed} failed, {blocked} subscriber(s) blocked.")
    # Per-message failures are retried in the next cycle and must not fail the run
    # (one unreachable subscriber cannot stop the whole fan-out).
    return 0


def _cmd_bot(once: bool = False, poll_timeout: int = 25, db_path: Path | None = None) -> int:
    """Run the subscriber bot: handle /start, /language, /status, /stop.

    Single long-polling process; a second instance exits immediately because the
    lock file is held. ``--once`` performs one ``getUpdates`` round and is useful
    from a scheduler when a long-lived process is not available.
    """
    from .bot import poll_once, run_bot
    from .config import load_config
    from .delivery.telegram import TelegramClient, TelegramError
    from .storage.database import Database

    config = load_config()
    if not config.telegram_bot_token:
        print("error: TELEGRAM_BOT_TOKEN is required for the bot.", file=sys.stderr)
        return 2
    db = Database(db_path or config.db_path)
    client = TelegramClient(config.telegram_bot_token, min_interval=config.telegram_min_interval)
    lock_file = None
    try:
        import fcntl

        config.data_dir.mkdir(parents=True, exist_ok=True)
        lock_file = open(config.data_dir / "bot.lock", "w")  # noqa: SIM115 - held for the run
        try:
            fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            print("Bot is already running (data/bot.lock is held); nothing to do.")
            return 0
    except ImportError:  # pragma: no cover - non-POSIX platform
        lock_file = None
    try:
        if once:
            stats = poll_once(db, client, poll_timeout=poll_timeout)
        else:
            from datetime import datetime, timezone

            started = datetime.now(timezone.utc).isoformat(timespec="seconds")
            print(f"Bot polling for commands since {started} (Ctrl-C to stop)…", flush=True)
            stats = run_bot(db, client, poll_timeout=poll_timeout)
    except KeyboardInterrupt:
        print("Bot stopped.")
        return 0
    except TelegramError as exc:
        print(f"error: bot polling failed: {exc}", file=sys.stderr)
        return 1
    finally:
        if lock_file is not None:
            lock_file.close()
    print(
        f"Bot: {stats.updates} update(s), {len(stats.actions)} action(s), {stats.errors} error(s)."
    )
    return 0


def _cmd_recover(limit: int | None = None, max_attempts: int = 3) -> int:
    """Move retryable error articles back to ``new`` for the next run.

    Articles that already failed ``max_attempts`` times stay parked in ``error``
    (D-015): otherwise every run requeues the same broken rows, they sort first
    by id, and fresh articles never reach the model. Use
    ``--max-attempts 0`` (or a large number) to force a full retry.
    """
    from .config import load_config
    from .storage.database import Database

    if limit is not None and limit < 1:
        print(f"error: --limit must be >= 1 (got {limit}).", file=sys.stderr)
        return 2
    if max_attempts < 0:
        print(f"error: --max-attempts must be >= 0 (got {max_attempts}).", file=sys.stderr)
        return 2
    db = Database(load_config().db_path)
    count = db.reset_errors(limit, max_attempts=max_attempts or None)
    parked = db.parked_errors(max_attempts=max_attempts or 3)
    print(f"Recovered {count} error article(s) for retry.")
    if parked:
        print(
            f"{parked} article(s) stay in 'error': they reached the retry limit "
            f"({max_attempts or 3}). Inspect them and reset manually if needed."
        )
    return 0


def _prune_backups(directory: Path, keep_days: int) -> int:
    """Delete timestamped automatic backups older than ``keep_days``."""
    import time

    if keep_days < 0:
        raise ValueError("--keep-days must be >= 0")
    cutoff = time.time() - keep_days * 86400
    removed = 0
    for path in directory.glob("news-*.db"):
        if path.is_file() and path.stat().st_mtime < cutoff:
            path.unlink()
            removed += 1
    return removed


def _cmd_backup(output: Path | None = None, keep_days: int = 14) -> int:
    """Create, verify and prune consistent SQLite backups."""
    from datetime import datetime, timezone

    from .config import load_config
    from .storage.database import Database

    if keep_days < 0:
        print("error: --keep-days must be >= 0", file=sys.stderr)
        return 2
    config = load_config()
    backup_dir = output.parent if output else config.data_dir / "backups"
    destination = output or (backup_dir / f"news-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.db")
    db = Database(config.db_path)
    db.backup_to(destination)
    integrity = Database(destination).integrity_check()
    if integrity != "ok":
        print(f"error: backup integrity check failed: {integrity}", file=sys.stderr)
        return 1
    removed = _prune_backups(backup_dir, keep_days)
    print(f"Backup created: {destination} (integrity: {integrity}; pruned: {removed})")
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
    import tempfile

    import httpx

    from .collectors import CollectorError, RssCollector
    from .config import SOURCE_TYPE_RSS, SOURCES, load_config
    from .llm.client import LLMClient, LLMUnavailableError
    from .storage.database import Database

    config = load_config()
    path = db_path or config.db_path
    checks: list[tuple[str, bool, str]] = []

    for name, directory in (("logs", config.logs_dir), ("backups", config.data_dir / "backups")):
        try:
            directory.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=directory, prefix=".doctor-", delete=True):
                pass
            checks.append((name, True, str(directory)))
        except OSError as exc:
            checks.append((name, False, str(exc)))

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
    from .logging_config import setup_logging

    try:
        from .config import load_config

        config = load_config()
    except ValueError as exc:
        # Importing .config validates the registry (duplicates, unknown ids in
        # TELECOM_NEWS_DISABLED_SOURCES); a typo must not dump a traceback.
        print(f"error: invalid configuration: {exc}", file=sys.stderr)
        return 2
    setup_logging(config.log_level)

    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help()
        return 0
    if args.command == "run":
        return _cmd_run(args.source, args.limit, args.dry_run)
    if args.command == "sources":
        if args.sources_command == "import":
            return _cmd_sources_import(args.csv, args.output, args.dry_run)
        return _cmd_sources(args.kind, args.enabled_only, args.json, args.verify, args.verify_limit)
    if args.command == "status":
        return _cmd_status()
    if args.command == "doctor":
        return _cmd_doctor()
    if args.command == "diagnose":
        return _cmd_diagnose(runs=args.runs, offline=args.offline, as_json=args.json)
    if args.command == "recover":
        return _cmd_recover(args.limit, args.max_attempts)
    if args.command == "backup":
        return _cmd_backup(args.output, args.keep_days)
    if args.command == "restore":
        return _cmd_restore(args.input)
    if args.command == "collect":
        return _cmd_collect(args.source, args.limit, args.max_age_days)
    if args.command == "process":
        return _cmd_process(args.limit)
    if args.command == "publish":
        return _cmd_publish(args.limit, args.dry_run)
    if args.command == "deliver":
        return _cmd_deliver(args.limit, args.dry_run)
    if args.command == "bot":
        return _cmd_bot(args.once, args.poll_timeout)

    parser.print_help()
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
