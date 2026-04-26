# Created: 2026-04-26
# Last reused/audited: 2026-04-26
# Authority basis: docs/operations/task_2026-04-26_full_data_midstream_fix_plan/
#                  phases/task_2026-04-26_phase2_adjacent_fixes/plan.md slice P2-B1
"""Slice P2-B1 relationship + idempotency tests.

PR #19 phase 2 audit Q2: ensemble_snapshots schema declared by
init_schema did not include `bias_corrected` column, but
_store_snapshot_p_raw at evaluator.py:1928 writes
`UPDATE ensemble_snapshots SET p_raw_json = ?, bias_corrected = ?`.

Fresh init_schema DBs (CI, dev, in-memory test fixtures) lacked the
column → writer's silent-error swallow caused two runtime_guards
tests to fail with NULL p_raw_json instead of the expected value.
Production DB likely had it via undocumented ALTER TABLE migration.

P2-B1 fix:
1. Add column to CREATE TABLE ensemble_snapshots.
2. Add idempotent ALTER TABLE to migration block (safe for legacy
   DBs that already have the column).

This test pins both:
- column exists post-init_schema
- second init_schema call doesn't error (idempotency)
- writer + reader round-trip works
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.state.db import init_schema


def _columns_of(conn: sqlite3.Connection, table: str) -> dict[str, dict]:
    """Return PRAGMA table_info for a table as {col_name: {type, notnull, dflt}}."""
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {
        r[1]: {"type": r[2], "notnull": r[3], "dflt": r[4]}
        for r in rows
    }


def test_ensemble_snapshots_declares_bias_corrected_column():
    """CREATE TABLE must declare bias_corrected on a fresh init_schema DB."""
    conn = sqlite3.connect(":memory:")
    init_schema(conn)
    cols = _columns_of(conn, "ensemble_snapshots")
    assert "bias_corrected" in cols, (
        "ensemble_snapshots schema must declare bias_corrected column "
        "(P2-B1 fix; pre-fix this column existed only via legacy ALTER "
        "TABLE migration, breaking fresh init_schema DBs)."
    )
    info = cols["bias_corrected"]
    assert info["type"] == "INTEGER", f"expected INTEGER, got {info['type']!r}"
    assert info["notnull"] == 1, "bias_corrected must be NOT NULL"
    assert str(info["dflt"]) == "0", f"default must be 0, got {info['dflt']!r}"


def test_init_schema_is_idempotent_for_bias_corrected():
    """Running init_schema twice must not raise (idempotency on the migration)."""
    conn = sqlite3.connect(":memory:")
    init_schema(conn)
    # Second call hits the ALTER TABLE migration; OperationalError ("duplicate
    # column name") is caught by the surrounding try/except; verify no exception
    # escapes.
    init_schema(conn)
    cols = _columns_of(conn, "ensemble_snapshots")
    assert "bias_corrected" in cols


def test_legacy_db_without_column_gets_migrated():
    """Simulate a legacy DB that has the table but no bias_corrected column,
    then run init_schema → migration must add the column without breaking
    existing rows."""
    conn = sqlite3.connect(":memory:")
    # Create a stripped ensemble_snapshots table mimicking a legacy schema
    # (no bias_corrected). Note: this skips the column from the current
    # CREATE TABLE so we can test the ALTER TABLE migration path.
    conn.execute("""
        CREATE TABLE ensemble_snapshots (
            snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT,
            city TEXT NOT NULL,
            target_date TEXT NOT NULL,
            issue_time TEXT,
            valid_time TEXT,
            available_at TEXT NOT NULL,
            fetch_time TEXT NOT NULL,
            lead_hours REAL NOT NULL,
            members_json TEXT NOT NULL,
            p_raw_json TEXT,
            spread REAL,
            is_bimodal INTEGER,
            model_version TEXT NOT NULL,
            data_version TEXT NOT NULL DEFAULT 'v1',
            authority TEXT NOT NULL DEFAULT 'VERIFIED',
            temperature_metric TEXT NOT NULL DEFAULT 'high'
        )
    """)
    conn.execute("""
        INSERT INTO ensemble_snapshots
        (city, target_date, available_at, fetch_time, lead_hours,
         members_json, model_version)
        VALUES ('NYC', '2026-04-15', '2026-04-15T12Z', '2026-04-15T12Z',
                12, '[]', 'ecmwf_ens')
    """)
    init_schema(conn)
    cols = _columns_of(conn, "ensemble_snapshots")
    assert "bias_corrected" in cols, (
        "ALTER TABLE migration must add bias_corrected to legacy DBs."
    )
    # Pre-existing row defaults to 0
    row = conn.execute(
        "SELECT bias_corrected FROM ensemble_snapshots WHERE city = 'NYC'"
    ).fetchone()
    assert row[0] == 0, "legacy row must default bias_corrected to 0"


def test_store_snapshot_p_raw_round_trip_with_fresh_schema():
    """Integration: writer + reader work end-to-end on fresh init_schema DB.

    Pre-P2-B1, this would silently fail because UPDATE bias_corrected
    raised OperationalError caught by writer's except, leaving p_raw_json
    NULL. Post-fix, the round-trip works.
    """
    import json
    import numpy as np
    from src.engine.evaluator import _store_snapshot_p_raw

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    # Insert a snapshot row so we have something to UPDATE.
    conn.execute("""
        INSERT INTO ensemble_snapshots
        (city, target_date, available_at, fetch_time, lead_hours,
         members_json, model_version)
        VALUES ('NYC', '2026-04-15', '2026-04-15T12Z', '2026-04-15T12Z',
                12, '[]', 'ecmwf_ens')
    """)
    snapshot_id = conn.execute(
        "SELECT snapshot_id FROM ensemble_snapshots WHERE city = 'NYC'"
    ).fetchone()[0]
    conn.commit()

    p_raw = np.array([0.2, 0.3, 0.5])
    _store_snapshot_p_raw(conn, str(snapshot_id), p_raw, bias_corrected=True)
    row = conn.execute(
        "SELECT p_raw_json, bias_corrected FROM ensemble_snapshots "
        "WHERE snapshot_id = ?", (snapshot_id,),
    ).fetchone()
    assert row is not None
    assert row["p_raw_json"] is not None, (
        "p_raw_json must persist post-fix; pre-fix it would be NULL because "
        "the UPDATE raised on missing bias_corrected column."
    )
    assert json.loads(row["p_raw_json"]) == [0.2, 0.3, 0.5]
    assert row["bias_corrected"] == 1
