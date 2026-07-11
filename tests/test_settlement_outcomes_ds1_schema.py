# Created: 2026-05-29
# Last reused or audited: 2026-05-29
# Authority basis: TRIBUNAL P2 D-S1 (HANDOFF_STAT_REFACTOR_2026-05-29 §4). settlement_outcomes
#   gains first-class nullable settlement_station + settlement_unit columns so the pairing
#   contract derives station/unit from VERIFIED columns instead of the heuristic URL parse
#   (un-blocks Hong Kong) and the forecast's unverifiable unit CLAIM (de-tautologizes the gate).
# Lifecycle: created=2026-05-29; last_reviewed=2026-05-29; last_reused=never
# Purpose: Schema + migration contract for D-S1 settlement_outcomes columns — canonical schema carries both nullable columns, migration adds them idempotently.
# Reuse: Run after any change to init_schema_forecasts settlement_outcomes DDL or the D-S1 migration script.
"""Schema + migration contract for the D-S1 settlement_outcomes columns.

Two structural guarantees:
  - the CANONICAL schema (init_schema_forecasts) carries both nullable columns, with a CHECK
    that constrains settlement_unit to the same {'F','C'} vocabulary as ensemble_snapshots
    (so assert_same_target compares like-for-like and a real F/C mismatch is caught);
  - the migration ADDs the columns to a pre-D-S1 settlement_outcomes idempotently, preserving
    every existing row (operator runs it on the live forecasts DB; columns start NULL).
"""

from __future__ import annotations

import importlib
import sqlite3

import pytest

from src.state.db import init_schema_forecasts

_MIGRATION = importlib.import_module(
    "scripts.migrations.202605_add_settlement_outcomes_station_unit"
)

# The pre-D-S1 canonical DDL (settlement_outcomes WITHOUT the two new columns) — what a live
# forecasts DB carries before the migration runs.
_LEGACY_DDL = """
    CREATE TABLE settlement_outcomes (
        settlement_id INTEGER PRIMARY KEY AUTOINCREMENT,
        city TEXT NOT NULL,
        target_date TEXT NOT NULL,
        temperature_metric TEXT NOT NULL
            CHECK (temperature_metric IN ('high', 'low')),
        market_slug TEXT,
        winning_bin TEXT,
        settlement_value REAL,
        settlement_source TEXT,
        settled_at TEXT,
        authority TEXT NOT NULL DEFAULT 'UNVERIFIED'
            CHECK (authority IN ('VERIFIED', 'UNVERIFIED', 'DISPUTED')),
        provenance_json TEXT NOT NULL DEFAULT '{}',
        recorded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        outcome_type INTEGER,
        UNIQUE(city, target_date, temperature_metric)
    )
"""


def _cols(conn: sqlite3.Connection, table: str) -> set[str]:
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}


def _legacy_conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.execute(_LEGACY_DDL)
    return c


# --- canonical schema ---------------------------------------------------------------


def test_canonical_schema_has_ds1_columns():
    c = sqlite3.connect(":memory:")
    init_schema_forecasts(c)
    cols = _cols(c, "settlement_outcomes")
    assert "settlement_station" in cols
    assert "settlement_unit" in cols


def test_settlement_columns_are_nullable():
    c = sqlite3.connect(":memory:")
    init_schema_forecasts(c)
    # both columns omitted -> NULL accepted (the un-backfilled state)
    c.execute(
        "INSERT INTO settlement_outcomes (city, target_date, temperature_metric) "
        "VALUES ('X', '2026-05-20', 'high')"
    )
    row = c.execute(
        "SELECT settlement_station, settlement_unit FROM settlement_outcomes"
    ).fetchone()
    assert row == (None, None)


def test_settlement_unit_check_accepts_f_c_and_null_rejects_other():
    c = sqlite3.connect(":memory:")
    init_schema_forecasts(c)
    c.execute(
        "INSERT INTO settlement_outcomes (city, target_date, temperature_metric, settlement_unit) "
        "VALUES ('A', '2026-05-20', 'high', 'F')"
    )
    c.execute(
        "INSERT INTO settlement_outcomes (city, target_date, temperature_metric, settlement_unit) "
        "VALUES ('B', '2026-05-20', 'high', 'C')"
    )
    c.execute(
        "INSERT INTO settlement_outcomes (city, target_date, temperature_metric, settlement_unit) "
        "VALUES ('C', '2026-05-20', 'high', NULL)"
    )
    with pytest.raises(sqlite3.IntegrityError):
        c.execute(
            "INSERT INTO settlement_outcomes (city, target_date, temperature_metric, settlement_unit) "
            "VALUES ('D', '2026-05-20', 'high', 'degF')"
        )


# --- migration ----------------------------------------------------------------------


def test_migration_adds_both_columns_to_legacy_table():
    c = _legacy_conn()
    assert "settlement_station" not in _cols(c, "settlement_outcomes")
    assert "settlement_unit" not in _cols(c, "settlement_outcomes")
    _MIGRATION.up(c)
    cols = _cols(c, "settlement_outcomes")
    assert {"settlement_station", "settlement_unit"}.issubset(cols)


def test_migration_idempotent():
    c = _legacy_conn()
    _MIGRATION.up(c)
    _MIGRATION.up(c)  # second apply must be a no-op, not an error
    cols = _cols(c, "settlement_outcomes")
    assert {"settlement_station", "settlement_unit"}.issubset(cols)


def test_migration_on_canonical_schema_is_noop():
    """Running the migration against a DB that already has the columns (fresh canonical) no-ops."""
    c = sqlite3.connect(":memory:")
    init_schema_forecasts(c)
    _MIGRATION.up(c)  # columns already present -> no-op
    assert {"settlement_station", "settlement_unit"}.issubset(_cols(c, "settlement_outcomes"))


def test_migration_preserves_existing_rows():
    c = _legacy_conn()
    c.execute(
        "INSERT INTO settlement_outcomes "
        "(city, target_date, temperature_metric, settlement_value, authority) "
        "VALUES ('Chicago', '2026-05-20', 'high', 77.0, 'VERIFIED')"
    )
    _MIGRATION.up(c)
    row = c.execute(
        "SELECT city, settlement_value, settlement_station, settlement_unit "
        "FROM settlement_outcomes WHERE city='Chicago'"
    ).fetchone()
    assert row == ("Chicago", 77.0, None, None)  # row survives, new columns default NULL


def test_migration_added_unit_column_enforces_check():
    """After the migration the new settlement_unit column carries the same {'F','C'} CHECK."""
    c = _legacy_conn()
    _MIGRATION.up(c)
    c.execute(
        "INSERT INTO settlement_outcomes (city, target_date, temperature_metric, settlement_unit) "
        "VALUES ('Z', '2026-05-20', 'high', 'C')"
    )
    with pytest.raises(sqlite3.IntegrityError):
        c.execute(
            "INSERT INTO settlement_outcomes (city, target_date, temperature_metric, settlement_unit) "
            "VALUES ('Q', '2026-05-20', 'high', 'kelvin')"
        )


def test_compute_receipts_reports_column_presence():
    legacy = _legacy_conn()
    r_legacy = _MIGRATION.compute_receipts(legacy)
    assert r_legacy["has_settlement_station"] is False
    assert r_legacy["has_settlement_unit"] is False
    _MIGRATION.up(legacy)
    r_after = _MIGRATION.compute_receipts(legacy)
    assert r_after["has_settlement_station"] is True
    assert r_after["has_settlement_unit"] is True
