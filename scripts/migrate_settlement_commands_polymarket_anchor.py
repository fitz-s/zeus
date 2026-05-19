# Created: 2026-05-19
# Last reused or audited: 2026-05-19
# Authority basis: PR 3 WAVE_B_PR_3_6_FIELD_MAP.md row 17; wave_b_opus_critic_pr36.md B2
"""PR 3 migration: add polymarket_end_anchor_source to settlement_commands.

Adds a single TEXT NOT NULL column with DEFAULT 'gamma_explicit' to
settlement_commands in world.db. Also adds PR 6 settlement_commands columns
and wrap_unwrap_commands chain-finality columns in one idempotent pass.

Background:
- settlement_commands has NO market_end_at column (verified: src/execution/settlement_commands.py:33-53).
- All pre-PR-3 rows default to 'gamma_explicit' (per orchestrator B2 fix, path b).
  F1-fallback rows are minority and unrecoverable retroactively without cross-table joins.
- ALTER TABLE ADD COLUMN with DEFAULT handles the backfill implicitly in SQLite.

Usage:
    python scripts/migrate_settlement_commands_polymarket_anchor.py [--dry-run] [--db <path>]

Dry-run is the default. Pass --no-dry-run to apply.
"""

import argparse
import os
import sqlite3
import sys
from pathlib import Path


_SETTLEMENT_COMMANDS_ALTERS = [
    # PR 3
    (
        "settlement_commands",
        "polymarket_end_anchor_source",
        "ALTER TABLE settlement_commands ADD COLUMN polymarket_end_anchor_source TEXT NOT NULL DEFAULT 'gamma_explicit'",
    ),
    # PR 6
    (
        "settlement_commands",
        "zeus_submit_intent_time",
        "ALTER TABLE settlement_commands ADD COLUMN zeus_submit_intent_time TEXT",
    ),
    (
        "settlement_commands",
        "venue_ack_time",
        "ALTER TABLE settlement_commands ADD COLUMN venue_ack_time TEXT",
    ),
    (
        "settlement_commands",
        "clock_skew_estimate_ms_at_submit",
        "ALTER TABLE settlement_commands ADD COLUMN clock_skew_estimate_ms_at_submit INTEGER",
    ),
]

_WRAP_UNWRAP_ALTERS = [
    # PR 6
    (
        "wrap_unwrap_commands",
        "first_inclusion_block_time",
        "ALTER TABLE wrap_unwrap_commands ADD COLUMN first_inclusion_block_time TEXT",
    ),
    (
        "wrap_unwrap_commands",
        "finality_confirmed_time",
        "ALTER TABLE wrap_unwrap_commands ADD COLUMN finality_confirmed_time TEXT",
    ),
]


def _column_exists(conn: sqlite3.Connection, table: str, column: str) -> bool:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return any(row[1] == column for row in rows)


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    return row is not None


def migrate(conn: sqlite3.Connection, dry_run: bool) -> list[str]:
    applied: list[str] = []
    all_alters = _SETTLEMENT_COMMANDS_ALTERS + _WRAP_UNWRAP_ALTERS
    for table, column, sql in all_alters:
        if not _table_exists(conn, table):
            print(f"  SKIP {table}.{column}: table does not exist")
            continue
        if _column_exists(conn, table, column):
            print(f"  SKIP {table}.{column}: already exists")
            continue
        print(f"  {'DRY-RUN: would apply' if dry_run else 'APPLY'}: {sql}")
        if not dry_run:
            conn.execute(sql)
            applied.append(f"{table}.{column}")
    if not dry_run and applied:
        conn.commit()
    return applied


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", dest="dry_run", action="store_true", default=True)
    parser.add_argument("--no-dry-run", dest="dry_run", action="store_false")
    parser.add_argument(
        "--db",
        default=None,
        help="Path to world.db (default: auto-detect via src.state.db)",
    )
    args = parser.parse_args()

    if args.db:
        db_path = args.db
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
    else:
        # Auto-detect via Zeus state module
        repo_root = Path(__file__).parent.parent
        sys.path.insert(0, str(repo_root))
        from src.state.db import get_trade_connection_with_world
        conn = get_trade_connection_with_world()

    print(f"{'DRY-RUN' if args.dry_run else 'APPLY'} — settlement/wrap_unwrap PR 3+6 migrations")
    applied = migrate(conn, dry_run=args.dry_run)
    if args.dry_run:
        print("Dry-run complete. Pass --no-dry-run to apply.")
    else:
        print(f"Applied {len(applied)} column(s): {applied}")
    conn.close()


if __name__ == "__main__":
    main()
