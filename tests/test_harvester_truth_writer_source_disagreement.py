# Created: 2026-05-08
# Last reused/audited: 2026-05-08
# Authority basis: docs/operations/task_2026-05-08_post_merge_full_chain/TASK.md
#   Phase C — fix #263 SOURCE_DISAGREEMENT isolation layer
"""Antibody: _write_settlement_truth SOURCE_DISAGREEMENT quarantine reason.

Root cause (fix #263): when obs rounds to within ±tolerance of the nearest bin
edge, the disagreement is measurement/rounding variance, not a genuine
outside-bin observation. Previously all such rows were classified as
'harvester_live_obs_outside_bin', making it impossible to distinguish
source-family disagreement (one source passes, other just misses) from
observations genuinely far outside any bin.

Fix: if rounded obs is within ±tolerance of the nearest bin edge → emit
'harvester_source_disagreement_within_tolerance' (QUARANTINED).
If obs is far outside (> tolerance from nearest edge) → keep 'harvester_live_obs_outside_bin'.
null-bin rows remain 'harvester_live_no_bin_info' (precedence unchanged).

Test matrix
-----------
  T1: both agree + bin contains → VERIFIED, no quarantine
  T2: obs within tolerance of bin edge (just misses) → SOURCE_DISAGREEMENT
  T3: both outside bin (obs far from edge) → obs_outside_bin
  T4: both bins None → no_bin_info (precedence unchanged — regression guard)
  T5: obs within tolerance of lo edge (open-shoulder hi=None) → SOURCE_DISAGREEMENT
  T6: obs exactly at tolerance boundary → SOURCE_DISAGREEMENT (inclusive)
  T7: obs one unit beyond tolerance → obs_outside_bin
"""
from __future__ import annotations

import sqlite3

import pytest

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


def _make_city_f() -> City:
    return City(
        name="NYC",
        lat=40.78,
        lon=-73.97,
        timezone="America/New_York",
        settlement_unit="F",
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
# T1: both agree + bin contains → VERIFIED, reason None
# ---------------------------------------------------------------------------

def test_agree_bin_contains_is_verified():
    """Baseline: obs inside bin → VERIFIED, no quarantine reason."""
    conn = _make_world_conn()
    city = _make_city_f()
    result = _write_settlement_truth(
        conn, city, "2026-01-10", 44.0, 46.0,
        event_slug="slug-agree",
        obs_row=_obs(45.0, "F"),
    )
    assert result["authority"] == "VERIFIED"
    assert result["reason"] is None


# ---------------------------------------------------------------------------
# T2: obs within tolerance of bin edge → SOURCE_DISAGREEMENT
# ---------------------------------------------------------------------------

def test_obs_within_tolerance_of_bin_edge_is_source_disagreement():
    """Obs just misses bin edge by ≤1°F → SOURCE_DISAGREEMENT, QUARANTINED."""
    conn = _make_world_conn()
    city = _make_city_f()
    # Bin is [44, 46]. Obs rounds to 43 — 1°F below lo edge. Distance = 1.0 ≤ tol=1.0.
    result = _write_settlement_truth(
        conn, city, "2026-01-11", 44.0, 46.0,
        event_slug="slug-disagree",
        obs_row=_obs(43.0, "F"),
    )
    assert result["reason"] == "harvester_source_disagreement_within_tolerance", (
        f"Expected SOURCE_DISAGREEMENT, got {result['reason']!r} — "
        "obs within tolerance of bin edge must not be labelled obs_outside_bin"
    )
    assert result["authority"] == "QUARANTINED"


# ---------------------------------------------------------------------------
# T3: obs far outside bin (> tolerance from edge) → obs_outside_bin
# ---------------------------------------------------------------------------

def test_obs_far_outside_bin_is_obs_outside_bin():
    """Obs genuinely far outside bin → 'harvester_live_obs_outside_bin'."""
    conn = _make_world_conn()
    city = _make_city_f()
    # Bin is [44, 46]. Obs rounds to 40 — 4°F below lo edge. Distance = 4.0 > tol=1.0.
    result = _write_settlement_truth(
        conn, city, "2026-01-12", 44.0, 46.0,
        event_slug="slug-far-outside",
        obs_row=_obs(40.0, "F"),
    )
    assert result["reason"] == "harvester_live_obs_outside_bin", (
        f"Expected obs_outside_bin for obs far from edge, got {result['reason']!r}"
    )
    assert result["authority"] == "QUARANTINED"


# ---------------------------------------------------------------------------
# T4: both bins None → no_bin_info (precedence regression guard)
# ---------------------------------------------------------------------------

def test_null_bin_still_emits_no_bin_info_not_disagreement():
    """null-bin rows → 'harvester_live_no_bin_info' (not SOURCE_DISAGREEMENT)."""
    conn = _make_world_conn()
    city = _make_city_f()
    result = _write_settlement_truth(
        conn, city, "2026-01-13", None, None,
        event_slug="slug-null-bin",
        obs_row=_obs(45.0, "F"),
    )
    assert result["reason"] == "harvester_live_no_bin_info", (
        f"null-bin must remain no_bin_info, got {result['reason']!r}"
    )


# ---------------------------------------------------------------------------
# T5: open-shoulder hi=None, obs within tolerance of lo → SOURCE_DISAGREEMENT
# ---------------------------------------------------------------------------

def test_open_shoulder_lo_only_within_tolerance_is_disagreement():
    """Open-shoulder bin (lo=44, hi=None), obs=43 just below → SOURCE_DISAGREEMENT."""
    conn = _make_world_conn()
    city = _make_city_f()
    # lo=44°F open-shoulder. Obs=43°F rounds to 43. Distance to lo edge = 1.0 ≤ tol.
    result = _write_settlement_truth(
        conn, city, "2026-01-14", 44.0, None,
        event_slug="slug-open-shoulder-disagree",
        obs_row=_obs(43.0, "F"),
    )
    assert result["reason"] == "harvester_source_disagreement_within_tolerance", (
        f"Expected SOURCE_DISAGREEMENT, got {result['reason']!r}"
    )


# ---------------------------------------------------------------------------
# T6: obs exactly at tolerance boundary → SOURCE_DISAGREEMENT (inclusive)
# ---------------------------------------------------------------------------

def test_obs_exactly_at_tolerance_boundary_is_disagreement():
    """Obs exactly 1°F from bin edge → SOURCE_DISAGREEMENT (boundary inclusive)."""
    conn = _make_world_conn()
    city = _make_city_f()
    # Bin [50, 52]. Obs=49 → distance=1.0 exactly. Tolerance=1.0 → ≤ → disagreement.
    result = _write_settlement_truth(
        conn, city, "2026-01-15", 50.0, 52.0,
        event_slug="slug-boundary",
        obs_row=_obs(49.0, "F"),
    )
    assert result["reason"] == "harvester_source_disagreement_within_tolerance", (
        f"Obs at exact tolerance boundary should be SOURCE_DISAGREEMENT, got {result['reason']!r}"
    )


# ---------------------------------------------------------------------------
# T7: obs one unit beyond tolerance → obs_outside_bin
# ---------------------------------------------------------------------------

def test_obs_beyond_tolerance_is_obs_outside_bin():
    """Obs 1°F beyond tolerance from bin edge → obs_outside_bin."""
    conn = _make_world_conn()
    city = _make_city_f()
    # Bin [50, 52]. Obs=48.4 rounds to 48 → distance=2.0 > tol=1.0 → obs_outside_bin.
    result = _write_settlement_truth(
        conn, city, "2026-01-16", 50.0, 52.0,
        event_slug="slug-beyond-tol",
        obs_row=_obs(48.4, "F"),
    )
    assert result["reason"] == "harvester_live_obs_outside_bin", (
        f"Obs beyond tolerance should be obs_outside_bin, got {result['reason']!r}"
    )
