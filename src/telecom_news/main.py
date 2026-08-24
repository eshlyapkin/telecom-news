import argparse
import sys


def main() -> None:
    """Entry point for ``python -m telecom_news``.

    The CLI is intentionally minimal: it supports the ``--help`` flag and two
    placeholder sub‑commands ``run`` and ``status``.  Both sub‑commands print
    ``Not implemented`` and exit with a non‑zero status code.
    """
    parser = argparse.ArgumentParser(prog="telecom_news")
    subparsers = parser.add_subparsers(dest="command")

    # Placeholder sub‑commands
    subparsers.add_parser("run", help="Placeholder run command")
    subparsers.add_parser("status", help="Placeholder status command")

    args = parser.parse_args()

    if args.command == "run":
        print("Not implemented")
        sys.exit(1)
    elif args.command == "status":
        print("Not implemented")
        sys.exit(1)
    else:
        # No sub‑command provided – show help and exit successfully.
        parser.print_help()
        sys.exit(0)


if __name__ == "__main__":
    main()
