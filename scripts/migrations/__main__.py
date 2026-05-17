# Lifecycle: created=2026-05-17; last_reviewed=2026-05-17; last_reused=never
# Purpose: CLI entry point for scripts.migrations runner.
#   Usage: python -m scripts.migrations apply [--dry-run] [--target=NAME] [--db-path=PATH]
# Authority: docs/operations/task_2026-05-17_post_karachi_remediation/FIX_SEV1_BUNDLE.md §F23
"""Migration runner CLI.

Default target DB is zeus_trades.db via get_trade_connection().
Pass --db-path to run against an arbitrary SQLite file (e.g. for CI / tmp DB tests).
"""
import argparse
import sqlite3
import sys


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Apply pending Zeus DB schema migrations."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    apply_p = sub.add_parser("apply", help="Apply pending migrations.")
    apply_p.add_argument(
        "--dry-run",
        action="store_true",
        help="Print pending migrations without applying them.",
    )
    apply_p.add_argument(
        "--target",
        metavar="NAME",
        default=None,
        help="Apply only the migration with this stem name (e.g. 202605_add_...).",
    )
    apply_p.add_argument(
        "--db-path",
        metavar="PATH",
        default=None,
        help=(
            "Path to a SQLite database file. "
            "Defaults to zeus_trades.db via get_trade_connection()."
        ),
    )

    args = parser.parse_args(argv)

    if args.command == "apply":
        from scripts.migrations import apply_migrations

        if args.db_path:
            conn = sqlite3.connect(args.db_path)
            conn.row_factory = sqlite3.Row
        else:
            from src.state.db import get_trade_connection

            conn = get_trade_connection()

        try:
            applied = apply_migrations(conn, dry_run=args.dry_run, target=args.target)
        finally:
            conn.close()

        if not applied:
            print("No pending migrations.")
        return 0

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(_main())
