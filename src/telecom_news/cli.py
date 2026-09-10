"""CLI entry point (see ARCHITECTURE.md 3.11).

``python -m telecom_news --help`` lists commands. ``collect`` (M1–M2) fetches
one source and stores new articles; ``status`` (M2) shows article counters;
``process`` (M3) runs LLM relevance/category/summary over ``new`` articles.
The ``run`` command exists as an explicit placeholder: it does NOT pretend to
do anything that is not implemented yet (see docs/ROADMAP.md).
"""

from __future__ import annotations

import argparse
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
            "M3: collect/status/process work; run is a placeholder."
        ),
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")

    subparsers = parser.add_subparsers(dest="command")

    run_parser = subparsers.add_parser(
        "run", help="Run the full pipeline (placeholder — implemented in M1–M4)"
    )
    run_parser.add_argument("--source", help="Source id to collect from (ignored until run lands)")
    run_parser.add_argument(
        "--dry-run", action="store_true", help="Do not publish to Telegram (ignored until M4)"
    )
    run_parser.add_argument(
        "--limit", type=int, help="Maximum number of articles (ignored until run lands)"
    )

    subparsers.add_parser("status", help="Show article counters by status")

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
    try:
        raw_items = collector.collect(limit=limit)
    except CollectorError as exc:
        print(f"error: collect failed for source '{source_id}': {exc}", file=sys.stderr)
        return 1
    db = Database(db_path or load_config().db_path)
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
        # Full pipeline (collect -> normalize -> store/dedup -> LLM -> publish)
        # is built across M1–M4.
        return _not_implemented("run", "milestones M1–M4")
    if args.command == "status":
        return _cmd_status()
    if args.command == "collect":
        return _cmd_collect(args.source, args.limit)
    if args.command == "process":
        return _cmd_process(args.limit)

    parser.print_help()
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
