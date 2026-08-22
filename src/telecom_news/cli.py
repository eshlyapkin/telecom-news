"""CLI entry point (M0 skeleton, see ARCHITECTURE.md 3.11).

``python -m telecom_news --help`` must work at this milestone. The ``run``
and ``status`` commands exist as explicit placeholders: they do NOT pretend to
do anything that is not implemented yet — each reports which milestone will
provide the real functionality (see docs/ROADMAP.md).
"""

from __future__ import annotations

import argparse
import sys

from . import __version__


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
            "M0 Foundation: CLI skeleton only."
        ),
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")

    subparsers = parser.add_subparsers(dest="command")

    run_parser = subparsers.add_parser(
        "run", help="Run the full pipeline (placeholder — implemented in M1–M4)"
    )
    run_parser.add_argument("--source", help="Source id to collect from (ignored until M1)")
    run_parser.add_argument("--dry-run", action="store_true", help="Do not publish to Telegram (ignored until M4)")
    run_parser.add_argument("--limit", type=int, help="Maximum number of articles (ignored until M1)")

    subparsers.add_parser(
        "status", help="Show article counters and source health (placeholder — implemented in M2)"
    )

    return parser


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
        # Article counters and source health require storage (M2).
        return _not_implemented("status", "milestone M2")

    parser.print_help()
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
