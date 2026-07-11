# Created: 2026-06-24
# Last reused/audited: 2026-06-24
# Authority basis: M1 timing-semantics fix (settled_at = observation availability time, NOT batch
#   wall-clock) — mirrors src/execution/harvester.py:1485-1495 + tests/test_harvester_m1_settled_at_invariant.py.
#   Root cause of the corrupt settlement_outcomes.settled_at (bulk-backfill constant, ~21-day lag) that
#   silently leaked every no-leak settlement walk-forward (PR #419 review finding).
"""M1 invariant for the RECONSTRUCTION writer src/ingest/harvester_truth_writer._write_settlement_truth.

Invariant:
  - settled_at = obs_row["fetched_at"] (the settling observation's availability time), NEVER now().
  - recorded_at = the separate now() write time (NOT aliased to settled_at).
  - obs_row present but fetched_at is None -> settled_at NULL -> authority forced DISPUTED
    (reason 'harvester_truth_no_settlement_time'), even when the value is bin-contained.
Antibody: reverting to `settled_at = datetime.now()` re-introduces the backfill-style corruption that
made target_date<d walk-forward leak settlement availability.
"""
from __future__ import annotations

import sqlite3

from src.config import City
from src.ingest.harvester_truth_writer import _write_settlement_truth


def _make_world_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS settlements (
            city TEXT, target_date TEXT, market_slug TEXT,
            winning_bin TEXT, settlement_value REAL, settlement_source TEXT,
            settled_at TEXT, authority TEXT, pm_bin_lo REAL, pm_bin_hi REAL,
            unit TEXT, settlement_source_type TEXT, temperature_metric TEXT,
            physical_quantity TEXT, observation_field TEXT, data_version TEXT,
            provenance_json TEXT,
            PRIMARY KEY (city, target_date, market_slug)
        );
        CREATE TABLE IF NOT EXISTS settlement_outcomes (
            settlement_id INTEGER PRIMARY KEY,
            city TEXT NOT NULL, target_date TEXT NOT NULL,
            temperature_metric TEXT NOT NULL, market_slug TEXT,
            winning_bin TEXT, settlement_value REAL, settlement_source TEXT,
            settled_at TEXT, authority TEXT NOT NULL DEFAULT 'UNVERIFIED',
            provenance_json TEXT NOT NULL DEFAULT '{}',
            recorded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS market_events (
            event_id INTEGER PRIMARY KEY,
            market_slug TEXT NOT NULL, city TEXT NOT NULL,
            target_date TEXT NOT NULL, temperature_metric TEXT NOT NULL,
            condition_id TEXT, token_id TEXT, range_label TEXT,
            range_low REAL, range_high REAL, outcome TEXT,
            created_at TEXT, recorded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        """
    )
    return conn


def _make_city(unit: str = "F") -> City:
    return City(
        name="NYC", lat=40.78, lon=-73.97, timezone="America/New_York",
        settlement_unit=unit, cluster="NYC", wu_station="KLGA",
        country_code="US", settlement_source_type="wu_icao",
    )


_OBS_FETCH = "2026-05-08T00:00:00+00:00"


def _obs(val: float, unit: str = "F", fetched_at=_OBS_FETCH) -> dict:
    return {
        "id": 1, "source": "wu_icao_history", "high_temp": val, "low_temp": None,
        "unit": unit, "fetched_at": fetched_at, "station_id": "KLGA",
        "authority": "VERIFIED", "observation_field": "high_temp", "observed_temp": val,
    }


def test_verified_settled_at_is_obs_fetch_time_not_now():
    # bin-contained -> VERIFIED. settled_at MUST equal the obs fetch time, NOT the batch wall-clock.
    conn = _make_world_conn()
    result = _write_settlement_truth(
        conn, _make_city("F"), "2026-05-07", 30.0, 31.0,
        event_slug="slug-verified", obs_row=_obs(30.0, "F"),
    )
    assert result["authority"] == "VERIFIED"
    row = conn.execute(
        "SELECT settled_at FROM settlements WHERE market_slug='slug-verified'"
    ).fetchone()
    assert row["settled_at"] == _OBS_FETCH, (
        f"settled_at must be the obs availability time {_OBS_FETCH}, got {row['settled_at']!r} "
        "(regression to datetime.now() = the settlement-availability leak)"
    )


def test_recorded_at_is_separate_write_time_not_settled_at():
    # recorded_at (in provenance/outcome) is the now() write time, distinct from settled_at.
    conn = _make_world_conn()
    result = _write_settlement_truth(
        conn, _make_city("F"), "2026-05-07", 30.0, 31.0,
        event_slug="slug-rec", obs_row=_obs(30.0, "F"),
    )
    # reconstructed_at in provenance is the real write time -> must NOT equal the obs fetch time.
    import json
    prov = json.loads(
        conn.execute("SELECT provenance_json FROM settlements WHERE market_slug='slug-rec'").fetchone()[0]
    )
    assert prov["reconstructed_at"] != _OBS_FETCH
    assert str(prov["reconstructed_at"]).startswith("202")  # a real ISO now() stamp


def test_missing_obs_fetch_time_forces_dispute():
    # obs present + value bin-contained, but fetched_at=None -> no genuine settlement time -> DISPUTED.
    conn = _make_world_conn()
    result = _write_settlement_truth(
        conn, _make_city("F"), "2026-05-07", 30.0, 31.0,
        event_slug="slug-noft", obs_row=_obs(30.0, "F", fetched_at=None),
    )
    assert result["authority"] == "DISPUTED"
    assert result["reason"] == "harvester_truth_no_settlement_time"
    row = conn.execute(
        "SELECT settled_at FROM settlements WHERE market_slug='slug-noft'"
    ).fetchone()
    assert row["settled_at"] is None
