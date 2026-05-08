# Created: 2026-05-07
# Last reused or audited: 2026-05-07
# Authority basis: PR #87 fix — selection_coverage lane omitted from
#   backtest_runs + backtest_outcome_comparison CHECK constraints in
#   src/state/db.py:2267,2283.
"""Migrate backtest DB: add 'selection_coverage' to lane CHECK constraints.

SQLite does not support ALTER TABLE … MODIFY CONSTRAINT, so this migration
uses the recommended pattern: rename → recreate → copy → drop → reindex.

Tables affected
---------------
- backtest_runs (13 rows as of 2026-05-07)
- backtest_outcome_comparison (36 k rows as of 2026-05-07)

Both FKs and indexes are preserved.

Idempotency
-----------
If 'selection_coverage' is already in the CHECK (i.e. run after a fresh
CREATE TABLE), the migration detects it via sqlite_master inspection and
exits as a no-op.

Usage
-----
    python scripts/migrate_backtest_runs_lane_constraint_2026_05_07.py [--dry-run] [--db PATH]

Dry-run prints SQL that would run without touching the DB.
"""
from __future__ import annotations

import argparse
import logging
import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

logger = logging.getLogger(__name__)

DEFAULT_DB = PROJECT_ROOT / "state" / "zeus_backtest.db"

# New DDL matching src/state/db.py after the fix.
CREATE_BACKTEST_RUNS_NEW = """
CREATE TABLE backtest_runs (
    run_id TEXT PRIMARY KEY,
    lane TEXT NOT NULL CHECK (
        lane IN ('wu_settlement_sweep', 'trade_history_audit', 'selection_coverage')
    ),
    started_at TEXT NOT NULL,
    completed_at TEXT,
    status TEXT NOT NULL,
    authority_scope TEXT NOT NULL CHECK (
        authority_scope = 'diagnostic_non_promotion'
    ),
    config_json TEXT NOT NULL,
    summary_json TEXT NOT NULL
)
"""

CREATE_BACKTEST_OUTCOME_COMPARISON_NEW = """
CREATE TABLE backtest_outcome_comparison (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    lane TEXT NOT NULL CHECK (
        lane IN ('wu_settlement_sweep', 'trade_history_audit', 'selection_coverage')
    ),
    subject_id TEXT NOT NULL,
    subject_kind TEXT NOT NULL,
    city TEXT,
    target_date TEXT,
    range_label TEXT,
    direction TEXT,
    settlement_value REAL,
    settlement_unit TEXT,
    derived_wu_outcome INTEGER,
    actual_trade_outcome INTEGER,
    actual_pnl REAL,
    truth_source TEXT NOT NULL,
    divergence_status TEXT NOT NULL CHECK (
        divergence_status IN (
            'not_applicable',
            'match',
            'wu_win_trade_loss',
            'wu_loss_trade_win',
            'trade_unresolved',
            'wu_missing',
            'bin_unparseable',
            'ambiguous_subject',
            'orphan_trade_decision',
            'scored',
            'no_snapshot',
            'no_day0_nowcast_excluded',
            'invalid_p_raw_json',
            'empty_p_raw',
            'label_count_mismatch',
            'no_clob_best_bid',
            'fdr_scan_failed',
            'no_hypotheses'
        )
    ),
    decision_reference_source TEXT,
    forecast_reference_id TEXT,
    evidence_json TEXT NOT NULL,
    missing_reason_json TEXT NOT NULL,
    authority_scope TEXT NOT NULL DEFAULT 'diagnostic_non_promotion'
        CHECK (authority_scope = 'diagnostic_non_promotion'),
    created_at TEXT NOT NULL,
    FOREIGN KEY (run_id) REFERENCES backtest_runs(run_id)
)
"""

CREATE_INDEXES = [
    "CREATE INDEX idx_backtest_outcome_lane_city_date ON backtest_outcome_comparison(lane, city, target_date)",
    "CREATE INDEX idx_backtest_outcome_subject ON backtest_outcome_comparison(subject_id)",
    "CREATE INDEX idx_backtest_outcome_divergence ON backtest_outcome_comparison(divergence_status)",
    "CREATE INDEX idx_backtest_outcome_run ON backtest_outcome_comparison(run_id)",
]


def _check_needs_migration(conn: sqlite3.Connection) -> bool:
    """Return True if either table is missing 'selection_coverage' lane or 'scored' divergence_status."""
    runs_row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='backtest_runs'"
    ).fetchone()
    if runs_row is None:
        return False  # table doesn't exist yet; init_backtest_schema will create it correctly
    if "selection_coverage" not in runs_row[0]:
        return True
    oc_row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='backtest_outcome_comparison'"
    ).fetchone()
    if oc_row is None:
        return False
    return "'scored'" not in oc_row[0]


def _migrate_table(
    conn: sqlite3.Connection,
    old_name: str,
    new_ddl: str,
    dry_run: bool,
) -> int:
    """Rename old → _old_bak, create new, copy data, drop backup. Returns row count."""
    bak = f"{old_name}_bak_migration_20260507"
    stmts = [
        f"ALTER TABLE {old_name} RENAME TO {bak}",
        new_ddl.strip(),
        f"INSERT INTO {old_name} SELECT * FROM {bak}",
        f"DROP TABLE {bak}",
    ]
    if dry_run:
        for s in stmts:
            logger.info("[DRY-RUN] %s", s[:120])
        return 0
    for s in stmts:
        conn.execute(s)
    return conn.execute(f"SELECT COUNT(*) FROM {old_name}").fetchone()[0]


def run_migration(db_path: Path = DEFAULT_DB, dry_run: bool = False) -> dict:
    if not db_path.exists():
        return {"status": "noop_db_not_found", "db": str(db_path)}

    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=OFF")  # required for rename+recreate

    try:
        if not _check_needs_migration(conn):
            return {"status": "noop_already_migrated", "db": str(db_path)}

        logger.info("Migration needed — starting transaction.")

        if not dry_run:
            conn.execute("BEGIN IMMEDIATE")

        # 1. backtest_runs (no FK dependencies from it; outcome_comparison refs it)
        runs_count = _migrate_table(conn, "backtest_runs", CREATE_BACKTEST_RUNS_NEW, dry_run)

        # 2. backtest_outcome_comparison (has FK → backtest_runs; FK is OFF so safe)
        oc_count = _migrate_table(
            conn, "backtest_outcome_comparison", CREATE_BACKTEST_OUTCOME_COMPARISON_NEW, dry_run
        )

        # 3. Recreate indexes (dropped with the old table)
        if not dry_run:
            for idx_sql in CREATE_INDEXES:
                conn.execute(idx_sql)
            conn.execute("COMMIT")
            conn.execute("PRAGMA foreign_keys=ON")

        return {
            "status": "dry_run" if dry_run else "migrated",
            "db": str(db_path),
            "backtest_runs_rows": runs_count,
            "backtest_outcome_comparison_rows": oc_count,
        }
    except Exception:
        if not dry_run:
            conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Print SQL; do not modify DB.")
    parser.add_argument("--db", default=str(DEFAULT_DB), help="Path to zeus_backtest.db.")
    args = parser.parse_args()

    result = run_migration(db_path=Path(args.db), dry_run=args.dry_run)
    logger.info("Result: %s", result)
    if result["status"] not in ("noop_db_not_found", "noop_already_migrated", "dry_run", "migrated"):
        sys.exit(1)


if __name__ == "__main__":
    main()
