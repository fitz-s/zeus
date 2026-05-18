# Lifecycle: created=2026-05-17; last_reviewed=2026-05-18; last_reused=2026-05-18
# Purpose: CLI entry point for scripts.migrations runner.
#   Usage: python -m scripts.migrations apply [--dry-run] [--target=NAME] [--db-path=PATH]
# Authority: docs/operations/task_2026-05-17_post_karachi_remediation/FIX_SEV1_BUNDLE.md §F23
"""Migration runner CLI.

When --target names a migration with TARGET_DB metadata, the runner opens that
canonical database by default. Pass --db-path together with --db-identity for
CI/temp DB tests.
"""
import argparse
import sqlite3
import sys


def _canonical_connection(db_identity: str) -> sqlite3.Connection:
    from src.state.db import (
        get_forecasts_connection,
        get_trade_connection,
        get_world_connection,
    )

    if db_identity == "trade":
        return get_trade_connection()
    if db_identity == "world":
        return get_world_connection()
    if db_identity == "forecasts":
        return get_forecasts_connection()
    raise ValueError(f"unknown db_identity={db_identity!r}")


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
            "Path to a SQLite database file. Requires --db-identity unless "
            "--target declares TARGET_DB."
        ),
    )
    apply_p.add_argument(
        "--db-identity",
        choices=("trade", "world", "forecasts"),
        default=None,
        help=(
            "Identity of the opened DB. Defaults to the target migration's "
            "TARGET_DB, or trade when applying pending trade migrations."
        ),
    )

    args = parser.parse_args(argv)

    if args.command == "apply":
        from scripts.migrations import apply_migrations, target_db_for_migration

        target_db = target_db_for_migration(args.target) if args.target else None
        db_identity = args.db_identity or target_db or "trade"

        if args.db_path:
            conn = sqlite3.connect(args.db_path)
            conn.row_factory = sqlite3.Row
        else:
            conn = _canonical_connection(db_identity)

        try:
            applied = apply_migrations(
                conn,
                dry_run=args.dry_run,
                target=args.target,
                db_identity=db_identity,
            )
        finally:
            conn.close()

        if not applied:
            print("No pending migrations.")
        return 0

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(_main())
