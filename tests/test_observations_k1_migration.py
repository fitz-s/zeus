# Created: 2026-05-01
# Last reused/audited: 2026-05-01
# Authority basis: Operator directive 2026-05-01 — antibody for Invariant C
#   (observations schema migrated to K1 dual-atom shape).
"""Antibody for Invariant C — observations K1 migration.

Coverage:
  1. Migration adds the 16 K1 columns to a legacy-shaped table.
  2. Re-running is a no-op (idempotency).
  3. Backfill pivots legacy single-atom rows into high_*/low_* columns.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from scripts.migrate_observations_k1 import (
    REQUIRED_K1_COLUMNS,
    existing_columns,
    plan_migration,
    run_migration,
)


@pytest.fixture
def legacy_db(tmp_path: Path) -> Path:
    """Build a fresh sqlite file with the legacy single-atom observations shape."""
    path = tmp_path / "legacy_obs.db"
    conn = sqlite3.connect(str(path))
    conn.execute("""
        CREATE TABLE observations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            city TEXT NOT NULL,
            target_date TEXT NOT NULL,
            source TEXT NOT NULL,
            high_temp REAL,
            low_temp REAL,
            unit TEXT NOT NULL,
            station_id TEXT,
            fetched_at TEXT,
            raw_value REAL,
            raw_unit TEXT,
            target_unit TEXT,
            value_type TEXT,
            fetch_utc TEXT,
            local_time TEXT,
            collection_window_start_utc TEXT,
            collection_window_end_utc TEXT,
            timezone TEXT,
            utc_offset_minutes INTEGER,
            dst_active INTEGER,
            is_ambiguous_local_hour INTEGER,
            is_missing_local_hour INTEGER,
            hemisphere TEXT,
            season TEXT,
            month INTEGER,
            rebuild_run_id TEXT,
            data_source_version TEXT,
            authority TEXT NOT NULL DEFAULT 'UNVERIFIED',
            provenance_metadata TEXT,
            UNIQUE(city, target_date, source)
        )
    """)
    # Two legacy rows: one high, one low — pivotable.
    conn.execute(
        "INSERT INTO observations (city,target_date,source,unit,raw_value,raw_unit,"
        "target_unit,value_type,fetch_utc,local_time,provenance_metadata,authority) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        ("London", "2026-04-21", "wu_pws_history", "C", 18.5, "C", "C", "high",
         "2026-04-22T00:00:00+00:00", "2026-04-21T15:00:00+00:00",
         '{"writer": "test"}', "VERIFIED"),
    )
    conn.execute(
        "INSERT INTO observations (city,target_date,source,unit,raw_value,raw_unit,"
        "target_unit,value_type,fetch_utc,local_time,provenance_metadata,authority) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        ("London", "2026-04-21", "wu_pws_history_low", "C", 9.0, "C", "C", "low",
         "2026-04-22T00:00:00+00:00", "2026-04-21T05:00:00+00:00",
         '{"writer": "test"}', "VERIFIED"),
    )
    conn.commit()
    conn.close()
    return path


def test_migration_adds_all_k1_columns(legacy_db: Path):
    summary = run_migration(db_path=legacy_db)
    assert summary["status"] == "migrated"
    # Connect after-the-fact, confirm every required column landed.
    with sqlite3.connect(str(legacy_db)) as conn:
        cols = existing_columns(conn)
    for col, _ in REQUIRED_K1_COLUMNS:
        assert col in cols, f"Missing column after migration: {col}"


def test_migration_is_idempotent(legacy_db: Path):
    first = run_migration(db_path=legacy_db)
    assert first["status"] == "migrated"
    second = run_migration(db_path=legacy_db)
    assert second["status"] == "noop_already_migrated"
    assert second["altered"] == []


def test_migration_pivots_legacy_rows(legacy_db: Path):
    summary = run_migration(db_path=legacy_db)
    assert summary["status"] == "migrated"
    # The fixture has 1 high + 1 low row.
    backfill = summary["backfill"]
    assert backfill["backfilled_high"] == 1
    assert backfill["backfilled_low"] == 1
    with sqlite3.connect(str(legacy_db)) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT city, source, value_type, raw_value, high_raw_value, low_raw_value "
            "FROM observations ORDER BY value_type"
        ).fetchall()
    by_type = {r["value_type"]: r for r in rows}
    assert by_type["high"]["high_raw_value"] == 18.5
    assert by_type["high"]["low_raw_value"] is None  # untouched
    assert by_type["low"]["low_raw_value"] == 9.0
    assert by_type["low"]["high_raw_value"] is None  # untouched


def test_dry_run_does_not_mutate(legacy_db: Path):
    pre_cols = None
    with sqlite3.connect(str(legacy_db)) as conn:
        pre_cols = existing_columns(conn)
    summary = run_migration(db_path=legacy_db, dry_run=True)
    assert summary["status"] == "dry_run"
    with sqlite3.connect(str(legacy_db)) as conn:
        post_cols = existing_columns(conn)
    assert post_cols == pre_cols, "Dry-run must not mutate schema"


def test_plan_lists_missing_columns(legacy_db: Path):
    with sqlite3.connect(str(legacy_db)) as conn:
        plan = plan_migration(conn)
    plan_cols = {c for c, _ in plan}
    expected = {c for c, _ in REQUIRED_K1_COLUMNS}
    assert plan_cols == expected
