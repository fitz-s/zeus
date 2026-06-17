# Created: 2026-06-16
# Last reused or audited: 2026-06-16
# Authority basis: timing-semantics fix M1 (settled_at = observation event time); src/execution/harvester.py:1453-1523
"""M1 timing-invariant tests for _write_settlement_truth.

The M1 invariant (harvester.py:1453-1523):
  - settled_at is derived from obs_row["observation_local_time"] — the REAL station
    observation instant, NEVER the cron write clock (recorded_at = datetime.now()).
  - When observation_local_time is missing → settled_at is honest-NULL and the row
    is forced QUARANTINED even if the observation value is bin-contained.
  - Line ~1520 guard: if settlement_time_missing and authority == "VERIFIED":
        authority = "QUARANTINED"; reason = "harvester_live_no_observation_time"

Tests:
  M1-case1: obs_row=None → authority QUARANTINED, persisted settled_at IS NULL,
             reason "harvester_live_no_obs".
  M1-case2: obs_row with bin-contained value AND observation_local_time set →
             authority VERIFIED, persisted settled_at equals observation_local_time
             (not the now() clock).
  M1-case3: obs_row with bin-contained value BUT observation_local_time=None →
             authority forced QUARANTINED (the critical M1 guard),
             persisted settled_at IS NULL, reason "harvester_live_no_observation_time".
"""
from __future__ import annotations

import sqlite3

import pytest

from src.config import City
from src.execution.harvester import _write_settlement_truth


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_settlements_conn() -> sqlite3.Connection:
    """In-memory DB with the settlements table schema required by _write_settlement_truth."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS settlements (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            city TEXT NOT NULL,
            target_date TEXT NOT NULL,
            market_slug TEXT,
            winning_bin TEXT,
            settlement_value REAL,
            settlement_source TEXT,
            settled_at TEXT,
            authority TEXT NOT NULL DEFAULT 'UNVERIFIED'
                CHECK (authority IN ('VERIFIED', 'UNVERIFIED', 'QUARANTINED')),
            pm_bin_lo REAL,
            pm_bin_hi REAL,
            unit TEXT,
            settlement_source_type TEXT,
            temperature_metric TEXT
                CHECK (temperature_metric IS NULL OR temperature_metric IN ('high','low')),
            physical_quantity TEXT,
            observation_field TEXT
                CHECK (observation_field IS NULL OR observation_field IN ('high_temp','low_temp')),
            data_version TEXT,
            provenance_json TEXT,
            UNIQUE(city, target_date, temperature_metric)
        );
    """)
    conn.commit()
    return conn


def _make_city() -> City:
    """Minimal City object sufficient for SettlementSemantics.for_city() + provenance."""
    return City(
        name="TestCity",
        lat=40.0,
        lon=-74.0,
        timezone="America/New_York",
        settlement_unit="F",
        cluster="US",
        wu_station="KJFK",
        settlement_source="wu_icao",
        settlement_source_type="wu_icao",
    )


def _make_obs_row(*, observation_local_time, high_temp_value) -> dict:
    """Build an obs_row with the fields _write_settlement_truth reads.

    _write_settlement_truth reads from obs_row:
      - obs_row.get("observation_local_time")  → settled_at
      - obs_row.get(metric_identity.observation_field)  → for "high" this is "high_temp"
      - obs_row.get("source"), "id", "fetched_at"       → provenance
    """
    return {
        "observation_local_time": observation_local_time,
        "high_temp": high_temp_value,
        "source": "wu_icao_test",
        "id": 42,
        "fetched_at": "2026-06-16T10:00:00Z",
    }


def _select_settlement(conn: sqlite3.Connection, city: str, target_date: str) -> sqlite3.Row:
    return conn.execute(
        "SELECT settled_at, authority, provenance_json FROM settlements "
        "WHERE city = ? AND target_date = ?",
        (city, target_date),
    ).fetchone()


# ---------------------------------------------------------------------------
# M1-case1: obs_row=None → QUARANTINED, settled_at NULL, reason harvester_live_no_obs
# ---------------------------------------------------------------------------

def test_M1_case1_no_obs_row_quarantined_null_settled_at():
    """When obs_row is None, the row is QUARANTINED with NULL settled_at.
    Reason must be 'harvester_live_no_obs' (the obs-absent branch)."""
    conn = _make_settlements_conn()
    city = _make_city()

    result = _write_settlement_truth(
        conn,
        city,
        target_date="2026-06-16",
        pm_bin_lo=75.0,
        pm_bin_hi=79.0,
        obs_row=None,
    )
    conn.commit()

    # Returned dict
    assert result["authority"] == "QUARANTINED", (
        f"Expected QUARANTINED, got {result['authority']}"
    )
    assert result["reason"] == "harvester_live_no_obs", (
        f"Expected reason 'harvester_live_no_obs', got {result['reason']!r}"
    )

    # Persisted row
    row = _select_settlement(conn, "TestCity", "2026-06-16")
    assert row is not None, "No row written to settlements"
    assert row["settled_at"] is None, (
        f"Expected NULL settled_at, got {row['settled_at']!r}"
    )
    assert row["authority"] == "QUARANTINED"


# ---------------------------------------------------------------------------
# M1-case2: bin-contained obs WITH observation_local_time → VERIFIED, settled_at = obs time
# ---------------------------------------------------------------------------

def test_M1_case2_verified_settled_at_equals_obs_time_not_now():
    """When obs_row is present with a bin-contained value AND observation_local_time,
    the row is VERIFIED and persisted settled_at equals observation_local_time —
    NOT the now() clock (recorded_at)."""
    conn = _make_settlements_conn()
    city = _make_city()

    obs_time = "2026-06-16T15:00:00"  # real observation instant
    # high_temp=77.0 → WU WMO half-up rounds to 77 → contained in [75, 79]
    obs_row = _make_obs_row(observation_local_time=obs_time, high_temp_value=77.0)

    result = _write_settlement_truth(
        conn,
        city,
        target_date="2026-06-16",
        pm_bin_lo=75.0,
        pm_bin_hi=79.0,
        obs_row=obs_row,
    )
    conn.commit()

    # Returned dict
    assert result["authority"] == "VERIFIED", (
        f"Expected VERIFIED, got {result['authority']!r} (reason={result.get('reason')!r})"
    )
    assert result["reason"] is None, (
        f"Expected no reason on VERIFIED, got {result['reason']!r}"
    )

    # Persisted row: settled_at must equal the observation time, not now()
    row = _select_settlement(conn, "TestCity", "2026-06-16")
    assert row is not None, "No row written to settlements"
    assert row["settled_at"] == obs_time, (
        f"M1 VIOLATION: settled_at={row['settled_at']!r} != obs_time={obs_time!r}. "
        "settled_at was set to the cron wall-clock instead of the observation instant."
    )
    assert row["authority"] == "VERIFIED"


# ---------------------------------------------------------------------------
# M1-case3 (CRITICAL): bin-contained obs BUT observation_local_time=None → QUARANTINED
# This is the line-1520 guard — the whole point of M1.
# ---------------------------------------------------------------------------

def test_M1_case3_bin_contained_but_no_obs_time_forced_quarantined():
    """CRITICAL M1 GUARD (harvester.py:1520):
    Even when the observation value is bin-contained and would otherwise be VERIFIED,
    if observation_local_time is None then settled_at is NULL and authority is forced
    to QUARANTINED. The cron clock is NEVER substituted.

    This test will FAIL if the M1 guard is removed or regressed."""
    conn = _make_settlements_conn()
    city = _make_city()

    # Same bin-contained value as case2, but observation_local_time is missing
    obs_row = _make_obs_row(observation_local_time=None, high_temp_value=77.0)

    result = _write_settlement_truth(
        conn,
        city,
        target_date="2026-06-16",
        pm_bin_lo=75.0,
        pm_bin_hi=79.0,
        obs_row=obs_row,
    )
    conn.commit()

    # Returned dict: must be forced QUARANTINED by the M1 guard
    assert result["authority"] == "QUARANTINED", (
        f"M1 GUARD FAILURE: authority={result['authority']!r}. "
        "A bin-contained value with no observation_local_time must be QUARANTINED "
        "(harvester.py:1520: 'if settlement_time_missing and authority == VERIFIED')."
    )
    assert result["reason"] == "harvester_live_no_observation_time", (
        f"Expected reason 'harvester_live_no_observation_time', got {result['reason']!r}"
    )

    # Persisted row: settled_at must be NULL (never the now() clock)
    row = _select_settlement(conn, "TestCity", "2026-06-16")
    assert row is not None, "No row written to settlements"
    assert row["settled_at"] is None, (
        f"M1 VIOLATION: settled_at={row['settled_at']!r} is not NULL. "
        "The cron write clock was substituted for the missing observation time."
    )
    assert row["authority"] == "QUARANTINED"
