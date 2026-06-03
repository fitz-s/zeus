# Created: 2026-06-03
# Last reused or audited: 2026-06-03
# Authority basis: UNIFICATION-DESIGN data-truth 2026-06-03
"""Archive ghost Platt/model_bias tables in zeus-forecasts.db.

Migration semantic policy:
  This migration performs ONLY ghost-table cleanup on zeus-forecasts.db.
  It does NOT touch any settlement table (settlements, settlement_outcomes,
  or any settlement-related schema). Settlement store convergence is handled
  entirely by W2's db.py/v2_schema.py changes (schema DDL + triggers).
  The three tables operated on (platt_models_v2, platt_models, model_bias)
  carry no live-path reads or writes (0-row ghosts or dead-config artifact).
  down() recreates empty ghost shells only for reversibility; no data at stake.

Background
----------
B3cont (2026-05) renamed platt_models → platt_models (canonical) inside
zeus-forecasts.db and left two 0-row ghost tables:
  - platt_models      (0 rows — legacy pre-rename ghost in forecasts DB)
  - model_bias        (0 rows — legacy ghost in forecasts DB)
  - platt_models_v2   (137 rows — dead-config artifact; 0 live src/ readers)

Steps (all inside one SAVEPOINT):
  1. ALTER TABLE platt_models_v2 RENAME TO platt_models_v2_archived_2026_06_03
     Guard: only if platt_models_v2 EXISTS and platt_models_v2_archived_2026_06_03 does NOT exist.
  2. DROP TABLE IF EXISTS platt_models
     Guard: assert row count == 0 before drop; abort SAVEPOINT if rows present.
  3. DROP TABLE IF EXISTS model_bias
     Guard: assert row count == 0 before drop; abort SAVEPOINT if rows present.

Reversibility
-------------
Call down(conn) to:
  - Rename platt_models_v2_archived_2026_06_03 back to platt_models_v2
  - Recreate empty platt_models and model_bias shells

Safety
------
This migration targets zeus-forecasts.db ONLY.
Never connect to zeus-world.db or zeus_trades.db.
"""
from __future__ import annotations

import sqlite3

TARGET_DB = "forecasts"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    return row is not None


def _row_count(conn: sqlite3.Connection, name: str) -> int:
    return conn.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]  # noqa: S608


# ---------------------------------------------------------------------------
# Dry-run plan printer (used by --dry-run CLI and tests)
# ---------------------------------------------------------------------------

def dry_run_plan(conn: sqlite3.Connection) -> None:
    """Print what up() would do, with row-count guards, without mutating anything."""
    v2_exists = _table_exists(conn, "platt_models_v2")
    v2_archived_exists = _table_exists(conn, "platt_models_v2_archived_2026_06_03")
    pm_exists = _table_exists(conn, "platt_models")
    mb_exists = _table_exists(conn, "model_bias")

    print("[dry-run] 202606_converge_settlement_store plan:")
    if v2_exists and not v2_archived_exists:
        n = _row_count(conn, "platt_models_v2")
        print(f"  STEP 1: RENAME platt_models_v2 → platt_models_v2_archived_2026_06_03  ({n} rows)")
    elif v2_archived_exists:
        print("  STEP 1: SKIP — platt_models_v2_archived_2026_06_03 already exists")
    else:
        print("  STEP 1: SKIP — platt_models_v2does not exist")

    if pm_exists:
        n = _row_count(conn, "platt_models")
        print(f"  STEP 2: DROP platt_models  ({n} rows — must be 0)")
    else:
        print("  STEP 2: SKIP — platt_models does not exist")

    if mb_exists:
        n = _row_count(conn, "model_bias")
        print(f"  STEP 3: DROP model_bias  ({n} rows — must be 0)")
    else:
        print("  STEP 3: SKIP — model_bias does not exist")


# ---------------------------------------------------------------------------
# Migration up / down
# ---------------------------------------------------------------------------

class _RowGuardAbort(RuntimeError):
    """Raised when a row-count guard fires; SAVEPOINT already rolled back+released."""


def up(conn: sqlite3.Connection) -> None:
    """Apply: archive platt_models_v2 + drop 0-row ghost tables.

    All steps run inside a single SAVEPOINT so any guard failure is atomic.
    Does NOT commit; the caller (migration runner) commits after recording the
    ledger entry.
    """
    conn.execute("SAVEPOINT converge_settlement_store")
    try:
        # STEP 1: rename platt_models_v2 → archived name
        if _table_exists(conn, "platt_models_v2"):
            if _table_exists(conn, "platt_models_v2_archived_2026_06_03"):
                # Already archived — idempotent skip
                pass
            else:
                conn.execute(
                    "ALTER TABLE platt_models_v2 RENAME TO platt_models_v2_archived_2026_06_03"
                )

        # STEP 2: drop platt_models (ghost — must be 0 rows)
        if _table_exists(conn, "platt_models"):
            n = _row_count(conn, "platt_models")
            if n != 0:
                conn.execute("ROLLBACK TO SAVEPOINT converge_settlement_store")
                conn.execute("RELEASE SAVEPOINT converge_settlement_store")
                raise _RowGuardAbort(
                    f"ABORT: platt_models has {n} rows (expected 0). "
                    "Migration refused — investigate before proceeding."
                )
            conn.execute("DROP TABLE platt_models")

        # STEP 3: drop model_bias (ghost — must be 0 rows)
        if _table_exists(conn, "model_bias"):
            n = _row_count(conn, "model_bias")
            if n != 0:
                conn.execute("ROLLBACK TO SAVEPOINT converge_settlement_store")
                conn.execute("RELEASE SAVEPOINT converge_settlement_store")
                raise _RowGuardAbort(
                    f"ABORT: model_bias has {n} rows (expected 0). "
                    "Migration refused — investigate before proceeding."
                )
            conn.execute("DROP TABLE model_bias")

        conn.execute("RELEASE SAVEPOINT converge_settlement_store")
    except _RowGuardAbort:
        # SAVEPOINT already rolled back + released inside the guard block
        raise RuntimeError(str(__import__("sys").exc_info()[1])) from None
    except Exception:
        conn.execute("ROLLBACK TO SAVEPOINT converge_settlement_store")
        conn.execute("RELEASE SAVEPOINT converge_settlement_store")
        raise


def down(conn: sqlite3.Connection) -> None:
    """Reverse: rename archived table back + recreate empty ghost shells."""
    conn.execute("SAVEPOINT converge_settlement_store_down")
    try:
        # Reverse STEP 1
        if _table_exists(conn, "platt_models_v2_archived_2026_06_03"):
            if not _table_exists(conn, "platt_models_v2"):
                conn.execute(
                    "ALTER TABLE platt_models_v2_archived_2026_06_03 RENAME TO platt_models_v2"
                )

        # Reverse STEP 2 — recreate empty ghost shell
        if not _table_exists(conn, "platt_models"):
            conn.execute(
                """CREATE TABLE platt_models (
                    id INTEGER PRIMARY KEY,
                    temperature_metric TEXT,
                    cluster TEXT,
                    season TEXT,
                    data_version TEXT,
                    input_space TEXT,
                    is_active TEXT
                )"""
            )

        # Reverse STEP 3 — recreate empty ghost shell
        if not _table_exists(conn, "model_bias"):
            conn.execute(
                """CREATE TABLE model_bias (
                    city TEXT,
                    season TEXT,
                    source TEXT,
                    bias REAL,
                    mae REAL,
                    n_samples INTEGER,
                    discount_factor REAL
                )"""
            )

        conn.execute("RELEASE SAVEPOINT converge_settlement_store_down")
    except Exception:
        conn.execute("ROLLBACK TO SAVEPOINT converge_settlement_store_down")
        conn.execute("RELEASE SAVEPOINT converge_settlement_store_down")
        raise
