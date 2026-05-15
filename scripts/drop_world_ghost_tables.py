# Created: 2026-05-14
# Last reused or audited: 2026-05-14
# Authority basis: docs/operations/task_2026-05-14_k1_followups/PLAN.md §2 P3 D2
"""Operator-invoked ghost table cleanup for zeus-world.db.

BACKGROUND
----------
K1 (commit eba80d2b9d, 2026-05-11) moved 7 forecast-class tables from
zeus-world.db to zeus-forecasts.db.  The old copies on world.db were
declared LEGACY_ARCHIVED in architecture/db_table_ownership.yaml and are
excluded from assert_db_matches_registry set-equality checks.  They are
safe to drop once the operator confirms:

  1. zeus-forecasts.db has been populated and passes assert_db_matches_registry.
  2. No live read path touches world.db for these table names (verified by
     migration audits in INVESTIGATION.md §3.A and PLAN.md §3 D2).
  3. A backup of zeus-world.db was taken before running this script.

GHOST TABLES (world.db, LEGACY_ARCHIVED)
-----------------------------------------
  observations, settlements, settlements_v2, source_run,
  market_events_v2, ensemble_snapshots_v2, calibration_pairs_v2

USAGE
-----
  # Dry run (default — prints DROP statements, does NOT execute)
  python3 scripts/drop_world_ghost_tables.py

  # Execute (irreversible — take a backup first)
  python3 scripts/drop_world_ghost_tables.py --execute

  # Custom world DB path
  python3 scripts/drop_world_ghost_tables.py --execute --db /path/to/zeus-world.db

SAFETY GATES
------------
- Refuses to run unless each ghost table is LEGACY_ARCHIVED in the registry.
- Refuses to run on forecasts.db or trade.db (checks sqlite_master for
  known world-class-only sentinels).
- Requires explicit --execute flag; dry-run is default.
- Prints row counts before dropping so operator can verify data was migrated.

RETENTION
---------
D2 policy: 90-day retain window from K1 merge date (2026-05-11).
Earliest authorised execution date: 2026-08-09.
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path

# Ensure repo root is on sys.path so `from src.state...` imports work when
# the script is invoked directly (not via `python -m`).
_SCRIPT_DIR = Path(__file__).resolve().parent
_REPO_ROOT_FOR_IMPORT = _SCRIPT_DIR.parent
if str(_REPO_ROOT_FOR_IMPORT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT_FOR_IMPORT))

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_PLAN_K1_MERGE_DATE = date(2026, 5, 11)
_RETENTION_DAYS = 90
# Earliest date on which DROP is authorised (merge date + 90-day D2 retain window).
# Fixed P4-N3: original constant incorrectly used merge date directly.
_EARLIEST_DROP_DATE = _PLAN_K1_MERGE_DATE + timedelta(days=_RETENTION_DAYS)

# Ghost tables to drop from world.db — must all be LEGACY_ARCHIVED in registry.
_GHOST_TABLES = [
    "observations",
    "settlements",
    "settlements_v2",
    "source_run",
    "market_events_v2",
    "ensemble_snapshots_v2",
    "calibration_pairs_v2",
]

# Sentinel tables that must exist on world.db (proves it is the world DB,
# not forecasts.db or some other file).
_WORLD_SENTINELS = frozenset({
    # Tables that must exist on world.db (proves this is the world DB, not forecasts.db).
    # Selected from WORLD_CLASS tables in architecture/db_table_ownership.yaml.
    "data_coverage",
    "job_run",
    "zeus_meta",
})

# Default world DB path
_DEFAULT_WORLD_DB = _REPO_ROOT_FOR_IMPORT / "state" / "zeus-world.db"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _verify_registry() -> None:
    """Confirm all ghost tables are LEGACY_ARCHIVED in the table registry."""
    try:
        from src.state.table_registry import _REGISTRY, SchemaClass, DBIdentity
    except ImportError as exc:
        print(f"ERROR: could not import table registry: {exc}", file=sys.stderr)
        print("Run from the repo root with the Zeus venv active.", file=sys.stderr)
        sys.exit(1)

    for tbl in _GHOST_TABLES:
        world_key = (tbl, DBIdentity.WORLD)
        entry = _REGISTRY.get(world_key)
        if entry is None:
            print(
                f"ERROR: table '{tbl}' has no WORLD entry in registry. "
                "The ghost list in this script may be stale — review "
                "architecture/db_table_ownership.yaml before proceeding.",
                file=sys.stderr,
            )
            sys.exit(1)
        if entry.schema_class != SchemaClass.LEGACY_ARCHIVED:
            print(
                f"ERROR: table '{tbl}' on world DB has schema_class="
                f"'{entry.schema_class.value}', expected 'legacy_archived'. "
                "Do not drop tables that are still authoritative.",
                file=sys.stderr,
            )
            sys.exit(1)
    print("Registry check: all 7 ghost tables confirmed LEGACY_ARCHIVED. OK")


def _verify_db_is_world(conn: sqlite3.Connection, db_path: Path) -> None:
    """Verify the connected DB is zeus-world.db via sentinel tables."""
    tables_on_disk = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    missing_sentinels = _WORLD_SENTINELS - tables_on_disk
    if missing_sentinels:
        print(
            f"ERROR: {db_path} is missing world-DB sentinel tables: "
            f"{sorted(missing_sentinels)}.\n"
            "This does not look like zeus-world.db. Aborting.",
            file=sys.stderr,
        )
        sys.exit(1)
    print(f"DB identity check: sentinel tables present in {db_path}. OK")


def _retention_check() -> None:
    """Warn (but do not block) if still within the 90-day retain window."""
    today = date.today()
    elapsed = (today - _PLAN_K1_MERGE_DATE).days
    if today < _EARLIEST_DROP_DATE:
        remaining = (_EARLIEST_DROP_DATE - today).days
        print(
            f"WARNING: D2 policy requires a {_RETENTION_DAYS}-day retain window after K1 merge "
            f"({_PLAN_K1_MERGE_DATE}). Earliest authorised drop date: {_EARLIEST_DROP_DATE} "
            f"({remaining} days from today). Proceeding anyway — operator assumes responsibility.",
            file=sys.stderr,
        )
    else:
        print(f"Retention window check: {elapsed} days since K1 merge — window satisfied. OK")


def _print_row_counts(conn: sqlite3.Connection, tables: list[str]) -> None:
    """Print row counts for each ghost table (so operator can verify migration)."""
    print("\nRow counts before drop:")
    for tbl in tables:
        try:
            count = conn.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()[0]
            print(f"  {tbl}: {count} rows")
        except sqlite3.OperationalError:
            print(f"  {tbl}: (table does not exist on this DB — already dropped or never present)")


def _build_drop_statements(tables: list[str]) -> list[str]:
    return [f"DROP TABLE IF EXISTS {tbl};" for tbl in tables]


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Drop LEGACY_ARCHIVED ghost tables from zeus-world.db. "
            "Dry-run by default; pass --execute to apply."
        )
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        default=False,
        help="Actually execute DROP TABLE statements. Default: dry-run only.",
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=_DEFAULT_WORLD_DB,
        help=f"Path to zeus-world.db (default: {_DEFAULT_WORLD_DB})",
    )
    args = parser.parse_args()

    db_path: Path = args.db
    execute: bool = args.execute

    print("=" * 70)
    print("Zeus world-DB ghost table cleanup (K1 D2)")
    print("=" * 70)
    print(f"DB path : {db_path}")
    print(f"Mode    : {'EXECUTE (irreversible)' if execute else 'DRY-RUN (no writes)'}")
    print()

    # 1. Registry safety gate
    _verify_registry()

    # 2. Retention window advisory
    _retention_check()

    # 3. Connect and verify DB identity
    if not db_path.exists():
        print(f"ERROR: DB not found: {db_path}", file=sys.stderr)
        sys.exit(1)

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        _verify_db_is_world(conn, db_path)

        # 4. Row counts (so operator can confirm migration completeness)
        _print_row_counts(conn, _GHOST_TABLES)

        # 5. Build and print DROP statements
        drops = _build_drop_statements(_GHOST_TABLES)
        print("\nDROP statements:")
        for stmt in drops:
            print(f"  {stmt}")

        # 6. Execute or abort
        if not execute:
            print(
                "\nDry-run complete. No changes made.\n"
                "Re-run with --execute to apply."
            )
            return

        print("\nExecuting DROP TABLE statements...")
        with conn:
            for stmt in drops:
                conn.execute(stmt)
        print("Done. Ghost tables dropped from world.db.")
        print(
            "\nNext steps:\n"
            "  1. Run PRAGMA integrity_check on zeus-world.db.\n"
            "  2. Remove LEGACY_ARCHIVED entries from architecture/db_table_ownership.yaml\n"
            "     (or leave them as documentation; assert_db_matches_registry ignores them).\n"
            "  3. Update scripts/drop_world_ghost_tables.py status comment to COMPLETED."
        )
    finally:
        conn.close()


if __name__ == "__main__":
    main()
