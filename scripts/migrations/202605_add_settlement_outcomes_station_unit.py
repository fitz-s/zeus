# Created: 2026-05-29
# Last reused or audited: 2026-05-29
# Lifecycle: created=2026-05-29; last_reviewed=2026-05-29; last_reused=never
# Authority basis: TRIBUNAL P2 D-S1 (HANDOFF_STAT_REFACTOR_2026-05-29 §4). settlement_outcomes
#   gains first-class nullable settlement_station + settlement_unit columns so the forecast↔
#   settlement pairing contract (src/contracts/residual_key.py) derives station/unit from
#   VERIFIED columns instead of the heuristic URL parse (un-blocks Hong Kong, whose climat.htm
#   settlement URL carries no parseable station) and the forecast's unverifiable unit CLAIM
#   (which made the pairing gate's unit dimension tautological — unable to catch a degC/degF
#   mis-scale). The canonical fresh-DB shape is in src/state/schema/v2_schema.py
#   (_create_settlement_outcomes); this migration brings an EXISTING forecasts DB up to it.
# DB target: zeus-forecasts.db (settlement_outcomes is a K1 forecast-class table; single-DB —
#   no cross-DB ATTACH required; the intra-DB SAVEPOINT provides the atomicity envelope INV-37
#   mandates for a multi-statement schema change).
# Runner interface: def up(conn: sqlite3.Connection) -> None
# Standalone operator receipts: python scripts/migrations/202605_add_settlement_outcomes_station_unit.py [--execute]
# Purpose: Add nullable settlement_station + settlement_unit columns to settlement_outcomes so pairing derives station/unit from VERIFIED columns instead of heuristics.
# Reuse: Run dry-run first; verify target DB is zeus-forecasts.db and is backed up; idempotent on re-run.
"""Add nullable settlement_station + settlement_unit columns to settlement_outcomes (D-S1).

Migration semantic policy:
  Both columns are NULLABLE and added with NO backfill. Existing rows keep NULL; the pairing
  contract falls back to its prior heuristic (station from settlement_source URL, unit from the
  forecast's claim) on NULL, so this migration changes NO behaviour on un-backfilled rows — it
  only makes the VERIFIED truth EXPRESSIBLE. Backfilling the columns (per-city station code,
  settlement unit) is a separate operator data task, not part of this schema change.

  settlement_unit carries a CHECK mirroring ensemble_snapshots' {'F','C'} vocabulary
  (CHECK (settlement_unit IS NULL OR settlement_unit IN ('F','C'))) so that, once backfilled,
  assert_same_target compares forecast-claimed unit against settlement-verified unit
  like-for-like and a genuine F/C disagreement is refused (loud) rather than silently coerced.

Idempotent: adds only the columns that are missing. Re-running after a successful apply (or
against a fresh canonical DB that already has both columns) is a no-op. Dry-run prints which
columns are present without mutating anything.
"""
from __future__ import annotations

import sqlite3

TARGET_DB = "forecasts"

_TABLE = "settlement_outcomes"

# (column_name, ADD COLUMN type+constraint clause). settlement_unit's CHECK is addable because
# it references only its own column and NULL (the value existing rows get) satisfies it.
_NEW_COLUMNS: tuple[tuple[str, str], ...] = (
    ("settlement_station", "TEXT"),
    ("settlement_unit", "TEXT CHECK (settlement_unit IS NULL OR settlement_unit IN ('F', 'C'))"),
)


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return (
        conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name=?",
            (name,),
        ).fetchone()[0]
        > 0
    )


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}


def _row_count(conn: sqlite3.Connection, name: str) -> int:
    if not _table_exists(conn, name):
        return -1
    return conn.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]


def compute_receipts(conn: sqlite3.Connection) -> dict[str, object]:
    """Pre-migration receipts (read-only): row count + which D-S1 columns already exist."""
    cols = _columns(conn, _TABLE) if _table_exists(conn, _TABLE) else set()
    return {
        "table_exists": _table_exists(conn, _TABLE),
        "settlement_outcomes_rows": _row_count(conn, _TABLE),
        "has_settlement_station": "settlement_station" in cols,
        "has_settlement_unit": "settlement_unit" in cols,
    }


def up(conn: sqlite3.Connection) -> None:
    """Add the missing D-S1 columns to settlement_outcomes, atomically and idempotently.

    Steps (inside one SAVEPOINT for all-or-nothing atomicity):
      1. Pre-flight: the table must exist (a fresh DB should have been created via
         init_schema_forecasts, which already includes both columns → this is then a no-op).
      2. For each of (settlement_station, settlement_unit), ADD COLUMN only if absent.
         Existing rows take NULL; the unit CHECK passes for NULL.
    """
    if not _table_exists(conn, _TABLE):
        raise AssertionError(
            f"{_TABLE} does not exist — run init_schema_forecasts first; this migration only "
            f"adds columns to an existing table."
        )

    existing = _columns(conn, _TABLE)
    missing = [(name, clause) for (name, clause) in _NEW_COLUMNS if name not in existing]
    if not missing:
        return  # idempotent no-op: both columns already present

    conn.execute("SAVEPOINT add_settlement_station_unit")
    try:
        for name, clause in missing:
            conn.execute(f"ALTER TABLE {_TABLE} ADD COLUMN {name} {clause}")
    except Exception:
        conn.execute("ROLLBACK TO SAVEPOINT add_settlement_station_unit")
        conn.execute("RELEASE SAVEPOINT add_settlement_station_unit")
        raise
    conn.execute("RELEASE SAVEPOINT add_settlement_station_unit")


def _standalone(argv: list[str] | None = None) -> int:
    """Operator entry point: dry-run receipts by default; --execute to apply."""
    import argparse
    import sys
    from pathlib import Path

    _REPO_ROOT = Path(__file__).resolve().parents[2]
    if str(_REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(_REPO_ROOT))

    parser = argparse.ArgumentParser(
        description="Add settlement_station + settlement_unit columns to settlement_outcomes (D-S1)."
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Apply the ALTER TABLE (default: dry-run column-presence receipts only).",
    )
    parser.add_argument(
        "--db-path",
        default=None,
        help="Path to zeus-forecasts.db (default: canonical forecasts connection).",
    )
    args = parser.parse_args(argv)

    if args.db_path:
        conn = sqlite3.connect(args.db_path)  # WRITER_LOCK_DEFER_REVIEW=2026-05-29 operator-invoked migration; daemon lock unavailable in standalone path
    else:
        from src.state.db import get_forecasts_connection

        conn = get_forecasts_connection(write_class="bulk")
    try:
        receipts = compute_receipts(conn)
        print("settlement_outcomes D-S1 column-add — PRE-MIGRATION RECEIPTS")
        for k, v in receipts.items():
            print(f"  {k}: {v}")
        if not args.execute:
            print("\nDRY-RUN (no changes applied). Re-run with --execute to apply.")
            return 0
        up(conn)
        conn.commit()
        post = _columns(conn, _TABLE)
        print("\nAPPLIED. POST-MIGRATION RECEIPTS")
        print(f"  has_settlement_station: {'settlement_station' in post}")
        print(f"  has_settlement_unit: {'settlement_unit' in post}")
        print(f"  settlement_outcomes rows (unchanged): {_row_count(conn, _TABLE)}")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(_standalone())
