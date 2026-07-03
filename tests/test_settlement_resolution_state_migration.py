# Created: 2026-06-29
# Last audited: 2026-06-29
# Authority basis: docs/operations/current/reports/state_vocabulary_canonical_redesign_2026-06-29.md
#   A8/A9 settlement-axis split; consult ruling thread 6a42bc3d (step 5a — schema + migration).

"""Schema + idempotent migration for the canonical A8 resolution_state column.

The event-level A8 lifecycle (SettlementResolutionState) gets a parallel nullable column
on settlement_outcomes — the consult's safe storage move: never redefine the corrupt
outcome_type in place, add resolution_state alongside. log_settlement's INSERT is NOT
changed here (no settlement-write risk); the column defaults NULL and readers fall back
to the legacy outcome_type mapping (zero behavior change) until a writer/backfill
populates it.
"""

from __future__ import annotations

import sqlite3

import pytest

from src.state.db import init_schema_forecasts


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def test_fresh_schema_has_resolution_state_column() -> None:
    conn = sqlite3.connect(":memory:")
    init_schema_forecasts(conn)
    assert "resolution_state" in _columns(conn, "settlement_outcomes")
    conn.close()


def test_resolution_state_check_accepts_valid_and_null_rejects_garbage() -> None:
    conn = sqlite3.connect(":memory:")
    init_schema_forecasts(conn)
    # valid lifecycle value + explicit NULL both allowed (UNVERIFIED avoids the
    # VERIFIED-requires-settlement_unit trigger; this test targets resolution_state only).
    conn.execute(
        "INSERT INTO settlement_outcomes (city, target_date, temperature_metric, authority, resolution_state) "
        "VALUES ('CityA','2026-06-29','high','UNVERIFIED','VENUE_RESOLVED')"
    )
    conn.execute(
        "INSERT INTO settlement_outcomes (city, target_date, temperature_metric, authority, resolution_state) "
        "VALUES ('CityB','2026-06-29','high','UNVERIFIED', NULL)"
    )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO settlement_outcomes (city, target_date, temperature_metric, authority, resolution_state) "
            "VALUES ('CityC','2026-06-29','high','UNVERIFIED','NOT_A_LIFECYCLE')"
        )
    conn.close()


def test_migration_adds_resolution_state_to_preexisting_table() -> None:
    # Simulate a pre-column DB; init must ADD resolution_state via the idempotent ALTER.
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE settlement_outcomes ("
        " settlement_id INTEGER PRIMARY KEY AUTOINCREMENT, city TEXT NOT NULL,"
        " target_date TEXT NOT NULL, temperature_metric TEXT NOT NULL,"
        " market_slug TEXT, winning_bin TEXT, settlement_value REAL, settlement_source TEXT,"
        " settled_at TEXT, authority TEXT NOT NULL DEFAULT 'UNVERIFIED',"
        " provenance_json TEXT NOT NULL DEFAULT '{}', recorded_at TEXT, outcome_type INTEGER,"
        " settlement_station TEXT, settlement_unit TEXT,"
        " UNIQUE(city, target_date, temperature_metric))"
    )
    assert "resolution_state" not in _columns(conn, "settlement_outcomes")
    init_schema_forecasts(conn)
    assert "resolution_state" in _columns(conn, "settlement_outcomes")
    conn.close()


def test_init_schema_forecasts_is_idempotent_for_resolution_state() -> None:
    # Running init twice must not raise (the ALTER's duplicate-column guard).
    conn = sqlite3.connect(":memory:")
    init_schema_forecasts(conn)
    init_schema_forecasts(conn)
    assert "resolution_state" in _columns(conn, "settlement_outcomes")
    conn.close()
