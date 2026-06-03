# Created: 2026-06-03
# Last reused or audited: 2026-06-03
# Authority basis: W2 settlement-store convergence (HANDOFF_2026-06-02_emos_ci.md).
#   Closes the blind spot of tests/test_w2_settlement_unit_writer.py (#132): that suite
#   only exercises a FRESH init_schema_forecasts(:memory:) DB, where the triggers are
#   always present. It never asserts the triggers reach an EXISTING live-shaped DB. The
#   live forecasts DB was created before #132 (CREATE TABLE IF NOT EXISTS no-ops, so the
#   later-added CREATE TRIGGER IF NOT EXISTS statements never ran against it) → the antibody
#   was INERT in production and a stale daemon wrote 283 VERIFIED NULL-unit rows.
"""Deployment-level relationship tests for the settlement_unit VERIFIED guard.

These tests assert the property the #132 fresh-DB tests cannot: that the antibody
TRIGGER is actually deployed to an EXISTING (pre-trigger) settlement_outcomes table —
the live-DB category — via the migration, and that it then makes a NULL-unit VERIFIED
settlement uncommittable regardless of writer.

  RT-DEP-a  An EXISTING settlement_outcomes table WITHOUT the triggers (the live-DB
            shape pre-#132) lets a VERIFIED + settlement_unit=NULL row be inserted.
            This is the live defect, reproduced. (RED baseline — the gap exists.)

  RT-DEP-b  After running the migration up() on that same existing table, BOTH triggers
            exist in sqlite_master AND a VERIFIED + NULL-unit INSERT and UPDATE now ABORT
            with 'VERIFIED_SETTLEMENT_REQUIRES_UNIT'. (GREEN — antibody deployed.)

  RT-DEP-c  Migration is idempotent: a second up() is a no-op and both triggers match
            the canonical v2_schema definitions byte-for-byte (after whitespace norm),
            so fresh DBs and migrated DBs converge.

  RT-DEP-d  Cross-boundary relationship: a settlement written through the live writer
            (write_settlement_with_era_provenance) with the city's authoritative unit
            persists settlement_unit equal to cities_by_name[city].settlement_unit — for
            both an F city (Chicago) and a C city (Amsterdam). Ties the column to the
            authority source, not to the value.
"""
from __future__ import annotations

import importlib.util
import sqlite3
from datetime import date
from pathlib import Path

import pytest

from src.config import cities_by_name
from src.state.schema.v2_schema import _create_settlement_outcomes
from src.state.settlement_writers import (
    dispatch_era_basis,
    write_settlement_with_era_provenance,
)

_REPO_ROOT = Path(__file__).resolve().parent.parent
_MIGRATION_PATH = (
    _REPO_ROOT
    / "scripts"
    / "migrations"
    / "202606_install_settlement_unit_verified_triggers.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location(
        "_mig_install_settlement_unit_triggers", _MIGRATION_PATH
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# The EXISTING live-DB table shape: settlement_outcomes WITH the settlement_unit column
# (added by the D-S1 column migration) but WITHOUT the W2 triggers. This reproduces the
# state of state/zeus-forecasts.db before this migration runs.
_PRE_TRIGGER_TABLE_DDL = """
CREATE TABLE settlement_outcomes (
    settlement_id INTEGER PRIMARY KEY AUTOINCREMENT,
    city TEXT NOT NULL,
    target_date TEXT NOT NULL,
    temperature_metric TEXT NOT NULL CHECK (temperature_metric IN ('high', 'low')),
    market_slug TEXT,
    winning_bin TEXT,
    settlement_value REAL,
    settlement_source TEXT,
    settled_at TEXT,
    authority TEXT NOT NULL DEFAULT 'UNVERIFIED'
        CHECK (authority IN ('VERIFIED', 'UNVERIFIED', 'QUARANTINED')),
    provenance_json TEXT NOT NULL DEFAULT '{}',
    recorded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    outcome_type INTEGER,
    settlement_station TEXT,
    settlement_unit TEXT CHECK (settlement_unit IS NULL OR settlement_unit IN ('F', 'C')),
    UNIQUE(city, target_date, temperature_metric)
)
"""


def _existing_pre_trigger_conn() -> sqlite3.Connection:
    """A settlement_outcomes table in the EXISTING (no-trigger) live-DB shape."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(_PRE_TRIGGER_TABLE_DDL)
    conn.commit()
    return conn


def _trigger_names(conn: sqlite3.Connection) -> set[str]:
    return {
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='trigger' "
            "AND tbl_name='settlement_outcomes'"
        ).fetchall()
    }


def _insert_verified(conn: sqlite3.Connection, *, unit, target_date="2026-06-01"):
    conn.execute(
        """
        INSERT INTO settlement_outcomes
          (city, target_date, temperature_metric, market_slug, authority, settlement_unit)
        VALUES (?, ?, 'high', ?, 'VERIFIED', ?)
        """,
        ("Chicago", target_date, "m-chicago-high", unit),
    )


# ---------------------------------------------------------------------------
# RT-DEP-a: the live defect reproduced — no trigger on an existing table
# ---------------------------------------------------------------------------

def test_existing_table_without_trigger_admits_null_verified():
    """RED baseline: the pre-trigger live-DB shape lets VERIFIED + NULL unit through."""
    conn = _existing_pre_trigger_conn()
    assert _trigger_names(conn) == set(), "fixture must start with NO triggers"
    # This is the exact live defect: a VERIFIED settlement with NULL unit commits.
    _insert_verified(conn, unit=None)
    conn.commit()
    row = conn.execute(
        "SELECT authority, settlement_unit FROM settlement_outcomes"
    ).fetchone()
    assert row["authority"] == "VERIFIED"
    assert row["settlement_unit"] is None  # the defect: NULL unit persisted


# ---------------------------------------------------------------------------
# RT-DEP-b: migration deploys the antibody to the existing table
# ---------------------------------------------------------------------------

def test_migration_installs_triggers_and_blocks_null_verified():
    """GREEN: after up(), both triggers exist and NULL-unit VERIFIED writes ABORT."""
    mig = _load_migration()
    conn = _existing_pre_trigger_conn()

    # Pre: receipts report both triggers absent.
    receipts = mig.compute_receipts(conn)
    assert receipts["table_exists"] is True
    assert receipts["triggers"] == {
        "_settlement_outcomes_verified_unit_check": "absent",
        "_settlement_outcomes_verified_unit_check_update": "absent",
    }

    mig.up(conn)
    conn.commit()

    # Both triggers now present on the existing table.
    assert _trigger_names(conn) == {
        "_settlement_outcomes_verified_unit_check",
        "_settlement_outcomes_verified_unit_check_update",
    }

    # INSERT path: VERIFIED + NULL unit now ABORTS.
    with pytest.raises((sqlite3.IntegrityError, sqlite3.OperationalError)) as exc_info:
        _insert_verified(conn, unit=None)
        conn.commit()
    assert "VERIFIED_SETTLEMENT_REQUIRES_UNIT" in str(exc_info.value)

    # UPDATE path: INSERT(UNVERIFIED, NULL) then UPDATE->VERIFIED also ABORTS.
    conn.execute(
        """
        INSERT INTO settlement_outcomes
          (city, target_date, temperature_metric, market_slug, authority, settlement_unit)
        VALUES ('Chicago', '2026-06-02', 'high', 'm', 'UNVERIFIED', NULL)
        """
    )
    conn.commit()
    with pytest.raises((sqlite3.IntegrityError, sqlite3.OperationalError)) as exc_info:
        conn.execute(
            "UPDATE settlement_outcomes SET authority='VERIFIED' "
            "WHERE city='Chicago' AND target_date='2026-06-02'"
        )
        conn.commit()
    assert "VERIFIED_SETTLEMENT_REQUIRES_UNIT" in str(exc_info.value)

    # A VERIFIED row WITH a unit still commits (guard is targeted, not blanket).
    _insert_verified(conn, unit="F", target_date="2026-06-03")
    conn.commit()
    assert conn.execute(
        "SELECT settlement_unit FROM settlement_outcomes "
        "WHERE target_date='2026-06-03'"
    ).fetchone()[0] == "F"


# ---------------------------------------------------------------------------
# RT-DEP-c: idempotent + converges with the fresh-DB (v2_schema) shape
# ---------------------------------------------------------------------------

def test_migration_idempotent_and_converges_with_fresh_schema():
    """Second up() is a no-op; migrated triggers match v2_schema fresh-DB triggers."""
    mig = _load_migration()

    # Migrated (existing-table) DB.
    migrated = _existing_pre_trigger_conn()
    mig.up(migrated)
    migrated.commit()
    # Idempotent: a second up() changes nothing and does not raise.
    mig.up(migrated)
    migrated.commit()
    status = mig._trigger_status(migrated)
    assert all(s == "present" for s in status.values())

    # Fresh DB built by the canonical schema function.
    fresh = sqlite3.connect(":memory:")
    _create_settlement_outcomes(fresh)
    fresh.commit()

    def _norm(conn, name):
        row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='trigger' AND name=?", (name,)
        ).fetchone()
        return " ".join((row[0] or "").split()) if row else None

    for name in (
        "_settlement_outcomes_verified_unit_check",
        "_settlement_outcomes_verified_unit_check_update",
    ):
        assert _norm(migrated, name) == _norm(fresh, name), (
            f"trigger {name} differs between migrated and fresh DB — they must converge"
        )


# ---------------------------------------------------------------------------
# RT-DEP-d: cross-boundary — live writer persists the city's authoritative unit
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("city_name,expected_unit", [("Chicago", "F"), ("Amsterdam", "C")])
def test_live_writer_persists_city_authoritative_unit(city_name, expected_unit):
    """A settlement written via the live era-writer carries the city's authoritative unit.

    Relationship asserted across the writer boundary: the persisted settlement_unit COLUMN
    equals cities_by_name[city].settlement_unit (the authority), not an inferred value.
    """
    city = cities_by_name[city_name]
    assert city.settlement_unit == expected_unit, "fixture precondition"

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    _create_settlement_outcomes(conn)  # fresh DB → triggers active
    conn.commit()

    settlement = {
        "city": city.name,
        "target_date": "2026-06-01",
        "temperature_metric": "high",
        "market_slug": f"highest-temperature-in-{city_name.lower()}-on-june-1-2026",
        "winning_bin": "bin",
        "settlement_value": 84.0 if expected_unit == "F" else 22.0,
        "settlement_source": city.settlement_source,
        "settled_at": "2026-06-03T18:46:17+00:00",
        "authority": "VERIFIED",
        "provenance": {"writer": "test", "unit": city.settlement_unit},
        "recorded_at": "2026-06-03T18:46:17+00:00",
        "settlement_unit": city.settlement_unit,
    }
    era = dispatch_era_basis(date(2026, 6, 1))
    assert era.is_admittable()
    result = write_settlement_with_era_provenance(settlement, era.era_basis, conn=conn)
    assert result["status"] == "written", result

    row = conn.execute(
        "SELECT settlement_unit FROM settlement_outcomes WHERE city=?", (city.name,)
    ).fetchone()
    assert row is not None
    assert row["settlement_unit"] == city.settlement_unit == expected_unit
