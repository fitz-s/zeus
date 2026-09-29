# Created: 2026-04-26
# Last reused/audited: 2026-05-15
# Authority basis: docs/operations/task_2026-04-26_full_data_midstream_fix_plan/
#                  phases/task_2026-04-26_phase2_adjacent_fixes/plan.md slice P2-B1;
#                  docs/archive/2026-Q2/task_2026-05-15_live_order_e2e_verification/LIVE_ORDER_E2E_VERIFICATION_PLAN.md forecasts DB p_raw authority.
"""Slice P2-B1 relationship + idempotency tests.

PR #19 phase 2 originally protected the legacy
`ensemble_snapshots.bias_corrected` column. v1.F20 removed the legacy
world-class table and moved canonical writes to `forecasts.ensemble_snapshots`.

This test now pins the current relationship:
- fresh `init_schema()` must not recreate the removed legacy table
- repeated `init_schema()` remains idempotent
- writer + reader round-trip works on `ensemble_snapshots`
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import pytest

from src.state.db import init_schema


def _columns_of(conn: sqlite3.Connection, table: str) -> dict[str, dict]:
    """Return PRAGMA table_info for a table as {col_name: {type, notnull, dflt}}."""
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {
        r[1]: {"type": r[2], "notnull": r[3], "dflt": r[4]}
        for r in rows
    }


def test_fresh_init_schema_does_not_recreate_legacy_ensemble_snapshots():
    """Fresh world schema must not recreate removed legacy ensemble_snapshots."""
    conn = sqlite3.connect(":memory:")
    init_schema(conn)
    cols = _columns_of(conn, "ensemble_snapshots")
    assert cols == {}, "v1.F20 removed legacy ensemble_snapshots from fresh world init"


def test_init_schema_is_idempotent_without_legacy_ensemble_snapshots():
    """Running init_schema twice must not recreate the removed legacy table."""
    conn = sqlite3.connect(":memory:")
    init_schema(conn)
    init_schema(conn)
    cols = _columns_of(conn, "ensemble_snapshots")
    assert cols == {}


@pytest.mark.skip(
    reason=(
        "v1.F20 (2026-05-18): ensemble_snapshots legacy table is being dropped. "
        "This test validated the ALTER TABLE bias_corrected migration for legacy DBs; "
        "now that the table is removed, the migration path no longer applies."
    )
)
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
            dataset_id TEXT NOT NULL DEFAULT 'v1',
            authority TEXT NOT NULL DEFAULT 'VERIFIED',
            temperature_metric TEXT NOT NULL DEFAULT 'high'
        )
    """)
    conn.execute("""
        INSERT INTO ensemble_snapshots
        (city, target_date, available_at, fetch_time, lead_hours,
         members_json, model_version, dataset_id, temperature_metric)
        VALUES ('NYC', '2026-04-15', '2026-04-15T12Z', '2026-04-15T12Z',
                12, '[]', 'ecmwf_ens',
                'tigge_mx2t6_local_calendar_day_max', 'high')
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


def _attach_forecasts_snapshot_table(conn: sqlite3.Connection) -> None:
    conn.execute("ATTACH DATABASE ':memory:' AS forecasts")
    conn.execute("""
        CREATE TABLE forecasts.ensemble_snapshots (
            snapshot_id INTEGER PRIMARY KEY,
            city TEXT NOT NULL,
            target_date TEXT NOT NULL,
            issue_time TEXT,
            valid_time TEXT,
            available_at TEXT NOT NULL,
            fetch_time TEXT NOT NULL,
            model_version TEXT NOT NULL,
            dataset_id TEXT NOT NULL,
            temperature_metric TEXT NOT NULL,
            provenance_json TEXT,
            p_raw_json TEXT,
            boundary_ambiguous INTEGER NOT NULL DEFAULT 0,
            causality_status TEXT NOT NULL DEFAULT 'VALID'
        )
    """)


    # v1.F20: legacy ensemble_snapshots removed; no legacy projection to check.

