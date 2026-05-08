# Created: 2026-05-08
# Last reused/audited: 2026-05-08
# Authority basis: docs/operations/task_2026-05-08_obs_outside_bin_audit/RUN.md
#   Fix #231 — Cluster B: null-bin rows misclassified as obs_outside_bin.
"""Antibody: _write_settlement_truth null-bin misclassification.

Root cause (Cluster B, 181 rows): when pm_bin_lo=None AND pm_bin_hi=None,
`contained` stays False and reason was set to 'harvester_live_obs_outside_bin'.
This is wrong — the observation is not outside any bin; there simply is no bin.

Fix: check both-None first; emit 'harvester_live_no_bin_info' with QUARANTINED.

Tests:
  T1: both bins None → quarantine_reason='harvester_live_no_bin_info', NOT 'obs_outside_bin'
  T2: both bins None → authority stays QUARANTINED, settlement_value recorded
  T3: lo only → open-shoulder containment still works (regression guard)
  T4: hi only → open-shoulder containment still works (regression guard)
  T5: lo+hi range → normal containment VERIFIED (regression guard)
  T6: lo+hi range, obs outside → 'harvester_live_obs_outside_bin' (regression guard)
"""
from __future__ import annotations

import sqlite3

from src.config import City
from src.ingest.harvester_truth_writer import _write_settlement_truth


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_world_conn() -> sqlite3.Connection:
    """In-memory DB with minimal settlements + settlements_v2 schema."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS settlements (
            city TEXT, target_date TEXT, market_slug TEXT,
            winning_bin TEXT, settlement_value REAL, settlement_source TEXT,
            settled_at TEXT, authority TEXT, pm_bin_lo REAL, pm_bin_hi REAL,
            unit TEXT, settlement_source_type TEXT, temperature_metric TEXT,
            physical_quantity TEXT, observation_field TEXT, data_version TEXT,
            provenance_json TEXT,
            PRIMARY KEY (city, target_date, market_slug)
        );
        CREATE TABLE IF NOT EXISTS settlements_v2 (
            settlement_id INTEGER PRIMARY KEY,
            city TEXT NOT NULL, target_date TEXT NOT NULL,
            temperature_metric TEXT NOT NULL, market_slug TEXT,
            winning_bin TEXT, settlement_value REAL, settlement_source TEXT,
            settled_at TEXT, authority TEXT NOT NULL DEFAULT 'UNVERIFIED',
            provenance_json TEXT NOT NULL DEFAULT '{}',
            recorded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS market_events_v2 (
            event_id INTEGER PRIMARY KEY,
            market_slug TEXT NOT NULL, city TEXT NOT NULL,
            target_date TEXT NOT NULL, temperature_metric TEXT NOT NULL,
            condition_id TEXT, token_id TEXT, range_label TEXT,
            range_low REAL, range_high REAL, outcome TEXT,
            created_at TEXT, recorded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
    """)
    return conn


def _make_city(unit: str = "F") -> City:
    return City(
        name="NYC",
        lat=40.78,
        lon=-73.97,
        timezone="America/New_York",
        settlement_unit=unit,
        cluster="NYC",
        wu_station="KLGA",
        country_code="US",
        settlement_source_type="wu_icao",
    )


def _obs(val: float, unit: str = "F") -> dict:
    return {
        "id": 1,
        "source": "wu_icao_history",
        "high_temp": val,
        "low_temp": None,
        "unit": unit,
        "fetched_at": "2026-05-08T00:00:00Z",
        "station_id": "KLGA",
        "authority": "VERIFIED",
        "observation_field": "high_temp",
        "observed_temp": val,
    }


# ---------------------------------------------------------------------------
# T1: both bins None → reason is 'harvester_live_no_bin_info', not 'obs_outside_bin'
# ---------------------------------------------------------------------------

def test_null_bin_reason_is_no_bin_info_not_outside_bin():
    conn = _make_world_conn()
    city = _make_city("F")
    result = _write_settlement_truth(
        conn, city, "2026-01-02", None, None,
        event_slug="uma_backfill_nyc_2026-01-02_high",
        obs_row=_obs(30.0, "F"),
    )
    assert result["reason"] == "harvester_live_no_bin_info", (
        f"Expected 'harvester_live_no_bin_info', got {result['reason']!r} — "
        "null-bin rows must not be labelled as obs_outside_bin"
    )


# ---------------------------------------------------------------------------
# T2: both bins None → QUARANTINED authority, settlement_value recorded
# ---------------------------------------------------------------------------

def test_null_bin_authority_is_quarantined_with_value():
    conn = _make_world_conn()
    city = _make_city("F")
    result = _write_settlement_truth(
        conn, city, "2026-01-02", None, None,
        event_slug="uma_backfill_nyc_2026-01-02_high",
        obs_row=_obs(30.0, "F"),
    )
    assert result["authority"] == "QUARANTINED"
    assert result["settlement_value"] == 30.0  # obs recorded even when QUARANTINED


# ---------------------------------------------------------------------------
# T3: open-shoulder lo-only → contained when obs >= lo (regression)
# ---------------------------------------------------------------------------

def test_open_shoulder_lo_only_contained():
    conn = _make_world_conn()
    city = _make_city("F")
    result = _write_settlement_truth(
        conn, city, "2026-01-03", 60.0, None,
        event_slug="slug-lo-only",
        obs_row=_obs(62.0, "F"),
    )
    assert result["authority"] == "VERIFIED"
    assert result["reason"] is None


# ---------------------------------------------------------------------------
# T4: open-shoulder hi-only → contained when obs <= hi (regression)
# ---------------------------------------------------------------------------

def test_open_shoulder_hi_only_contained():
    conn = _make_world_conn()
    city = _make_city("F")
    result = _write_settlement_truth(
        conn, city, "2026-01-04", None, 40.0,
        event_slug="slug-hi-only",
        obs_row=_obs(36.0, "F"),
    )
    assert result["authority"] == "VERIFIED"
    assert result["reason"] is None


# ---------------------------------------------------------------------------
# T5: lo+hi range, obs inside → VERIFIED (regression)
# ---------------------------------------------------------------------------

def test_range_bin_obs_inside_is_verified():
    conn = _make_world_conn()
    city = _make_city("F")
    result = _write_settlement_truth(
        conn, city, "2026-01-05", 30.0, 31.0,
        event_slug="slug-range",
        obs_row=_obs(30.0, "F"),
    )
    assert result["authority"] == "VERIFIED"


# ---------------------------------------------------------------------------
# T6: lo+hi range, obs outside → 'harvester_live_obs_outside_bin' (regression)
# ---------------------------------------------------------------------------

def test_range_bin_obs_outside_is_obs_outside_bin():
    conn = _make_world_conn()
    city = _make_city("F")
    result = _write_settlement_truth(
        conn, city, "2026-01-06", 53.0, 54.0,
        event_slug="slug-outside",
        obs_row=_obs(36.0, "F"),
    )
    assert result["authority"] == "QUARANTINED"
    assert result["reason"] == "harvester_live_obs_outside_bin"
