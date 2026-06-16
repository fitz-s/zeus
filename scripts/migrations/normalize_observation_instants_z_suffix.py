# Created: 2026-06-16
# Last reused or audited: 2026-06-16
# Authority basis: ZEUS_TIMING_COMPLETE_PLAN_2026-06-16.md §C3 + §Part V ANTIBODY 2.
#   observation_instants has 498 rows (of 2,770,000+) whose utc_timestamp ends in 'Z'
#   instead of '+00:00'. SQLite string-sorts 'Z' > '+', so these 498 rows sort AFTER
#   the +00:00 majority in any ORDER BY utc_timestamp query even though they denote the
#   same instant. This migration normalises those rows to '+00:00' form so all rows
#   sort correctly together. The change is pure string rewrite (same UTC instant) and
#   does NOT alter observation values or natural keys.
#
# DB target: zeus-world.db (observation_instants is a world-class table; INV-37 requires
#   intra-DB SAVEPOINT for multi-statement truth rewrites; no cross-DB ATTACH needed).
# Runner interface: python scripts/migrations/normalize_observation_instants_z_suffix.py [--execute]
#   Dry-run (default): prints the count of Z-suffix rows without mutating anything.
#   --execute: runs the UPDATE inside a SAVEPOINT and then asserts count == 0.
# Idempotent: re-running after a successful apply is a no-op (0 rows match WHERE … LIKE '%Z').
# IMPORTANT: Do NOT run this against the live DB until a DB copy has been verified first.
#   The script is safe to re-run (idempotent) but the operator must decide the timing.
"""Normalise observation_instants.utc_timestamp 'Z' → '+00:00' (498 rows, idempotent).

Runs in dry-run mode by default (prints row count, no mutation).
Pass --execute to apply.  Pass --db-path <path> to override the default.
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path


# Default DB path (operator can override via --db-path).
# Resolved relative to this script's repo root so the script is portable across
# worktrees without hard-coding an absolute host path.
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_DEFAULT_DB = _REPO_ROOT / "zeus-world.db"

_TABLE = "observation_instants"
_COLUMN = "utc_timestamp"

# Detect rows whose timestamp ends in the bare 'Z' suffix.
_WHERE_Z = f"{_COLUMN} LIKE '%Z'"

# The rewrite: replace trailing 'Z' with '+00:00'.
# substr(col, 1, length(col)-1) strips the final character; '||' appends '+00:00'.
_REWRITE_EXPR = f"substr({_COLUMN}, 1, length({_COLUMN}) - 1) || '+00:00'"


def _count_z_rows(conn: sqlite3.Connection) -> int:
    row = conn.execute(
        f"SELECT COUNT(*) FROM {_TABLE} WHERE {_WHERE_Z}"
    ).fetchone()
    return int(row[0])


def _assert_zero_z_rows(conn: sqlite3.Connection) -> None:
    remaining = _count_z_rows(conn)
    if remaining != 0:
        raise AssertionError(
            f"Post-migration assertion failed: {remaining} rows still end in 'Z' "
            f"in {_TABLE}.{_COLUMN}"
        )


def _table_exists(conn: sqlite3.Connection) -> bool:
    return (
        conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name=?",
            (_TABLE,),
        ).fetchone()[0]
        > 0
    )


def run(db_path: Path, *, execute: bool = False) -> None:
    """Dry-run (execute=False) or apply (execute=True) the Z-suffix normalisation."""
    if not db_path.exists():
        print(f"ERROR: DB not found: {db_path}", file=sys.stderr)
        sys.exit(1)

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    if not _table_exists(conn):
        print(f"Table '{_TABLE}' does not exist in {db_path} — nothing to do.")
        conn.close()
        return

    z_count = _count_z_rows(conn)

    if not execute:
        print(f"[DRY-RUN] {db_path}")
        print(f"  Table:        {_TABLE}")
        print(f"  Z-suffix rows: {z_count}")
        print(
            "  Would UPDATE: "
            f"SET {_COLUMN} = {_REWRITE_EXPR} WHERE {_WHERE_Z}"
        )
        print("  (Pass --execute to apply)")
        conn.close()
        return

    if z_count == 0:
        print(f"[NOOP] No Z-suffix rows in {_TABLE}.{_COLUMN} — already normalised.")
        conn.close()
        return

    # Apply inside a SAVEPOINT so the whole rewrite is atomic (INV-37).
    print(f"[EXECUTE] Normalising {z_count} Z-suffix rows in {_TABLE}.{_COLUMN} …")
    conn.execute("SAVEPOINT normalize_z_suffix")
    try:
        cursor = conn.execute(
            f"UPDATE {_TABLE} SET {_COLUMN} = {_REWRITE_EXPR} WHERE {_WHERE_Z}"
        )
        updated = cursor.rowcount
        # Post-migration assertion: must be 0 Z-suffix rows now.
        _assert_zero_z_rows(conn)
        conn.execute("RELEASE SAVEPOINT normalize_z_suffix")
        conn.commit()
        print(f"  Updated {updated} rows. Post-migration assertion passed (0 Z-suffix rows).")
    except Exception:
        conn.execute("ROLLBACK TO SAVEPOINT normalize_z_suffix")
        conn.close()
        raise

    conn.close()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--db-path",
        type=Path,
        default=_DEFAULT_DB,
        help=f"Path to zeus-world.db (default: {_DEFAULT_DB})",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        default=False,
        help="Apply the migration (default: dry-run only)",
    )
    args = parser.parse_args(argv)
    run(args.db_path, execute=args.execute)


if __name__ == "__main__":
    main()
