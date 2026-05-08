# Created: 2026-05-08
# Last reused or audited: 2026-05-08
# Authority basis: docs/operations/task_2026-05-08_262_london_f_to_c/RUN.md
#   Fix #262 -- Cluster A: pre-2026 London markets posed in F; London now C.
#   Settlement check must convert F bin bounds to C before containment.
"""Antibody tests: F->C bin-unit conversion at settlement reconstruction time.

Root cause (fix #262, Cluster A, 317 rows): Polymarket 2025 London markets
were posed with F bins (e.g. "40-41F"). _parse_temp_range extracts the
numeric values (40.0, 41.0) without unit. _write_settlement_truth was then
comparing a C observation (e.g. 5C) against the raw F numbers -> always fails.

Fix: caller passes pm_bin_unit='F' when the question contains a F symbol.
_write_settlement_truth converts bin bounds via (F-32)*5/9 before containment.

Tests:
  T1: F bin + C city -> bin converted to C -> obs 5C in [4.44,5.0] -> VERIFIED
  T2: F bin + C city -> obs 10C outside converted bin -> obs_outside_bin
  T3: C bin + C city -> no conversion (control) -> bin check in C directly
  T4: F bin + F city -> no conversion (control) -> bin check in F directly
  T5: _detect_bin_unit returns 'F' for F-symbol questions
  T6: _detect_bin_unit returns 'C' for C-symbol questions
  T7: _detect_bin_unit returns None for no-symbol questions
  T8: _f_to_c arithmetic: (40-32)*5/9 = 4.444..., (41-32)*5/9 = 5.0
  T9: open-shoulder F bin + C city -> converted hi -> obs C contained (regression)
"""
from __future__ import annotations

import sqlite3

import pytest

from src.config import City
from src.ingest.harvester_truth_writer import (
    _detect_bin_unit,
    _f_to_c,
    _write_settlement_truth,
)


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


def _london_city() -> City:
    """London as currently configured: settlement_unit='C'."""
    return City(
        name="London",
        lat=51.5053,
        lon=0.0553,
        timezone="Europe/London",
        settlement_unit="C",
        cluster="London",
        wu_station="EGLC",
        country_code="GB",
        settlement_source_type="wu_icao",
    )


def _nyc_city() -> City:
    """NYC as a control: settlement_unit='F'."""
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


def _obs_c(val: float) -> dict:
    """Celsius observation row."""
    return {
        "id": 1,
        "source": "wu_icao_history",
        "high_temp": val,
        "low_temp": None,
        "unit": "C",
        "fetched_at": "2026-05-08T00:00:00Z",
        "station_id": "EGLC",
        "authority": "VERIFIED",
        "observation_field": "high_temp",
        "observed_temp": val,
    }


def _obs_f(val: float) -> dict:
    """Fahrenheit observation row."""
    return {
        "id": 1,
        "source": "wu_icao_history",
        "high_temp": val,
        "low_temp": None,
        "unit": "F",
        "fetched_at": "2026-05-08T00:00:00Z",
        "station_id": "KLGA",
        "authority": "VERIFIED",
        "observation_field": "high_temp",
        "observed_temp": val,
    }


# ---------------------------------------------------------------------------
# T1: F bin + C city -> obs 5C matches converted bin [4.44, 5.0] -> VERIFIED
# ---------------------------------------------------------------------------

def test_f_bin_c_city_obs_in_converted_range_is_verified():
    """RELATIONSHIP: F-original bin 40-41F converts to ~4.44-5.0C.

    London 2025 Gamma market bin [40, 41] was posed in F.
    Actual observation 5C = 41F, should be VERIFIED after conversion.
    This is the exact cluster A failure pattern from fix #262.
    """
    conn = _make_world_conn()
    city = _london_city()
    # bin 40-41F -> (40-32)*5/9 = 4.444C, (41-32)*5/9 = 5.0C
    result = _write_settlement_truth(
        conn, city, "2025-04-01", 40.0, 41.0,
        event_slug="london-high-2025-04-01",
        obs_row=_obs_c(5.0),  # 5C = 41F exactly
        pm_bin_unit="F",
    )
    assert result["authority"] == "VERIFIED", (
        f"Expected VERIFIED after F->C conversion, got {result['authority']!r}. "
        f"reason={result['reason']!r}. "
        "5C is 41F exactly, which is the top of bin [40-41F] = [4.44-5.0C]."
    )
    assert result["reason"] is None
    # P1 regression: winning_bin label must reflect converted C bounds, not original F values.
    # _canonical_bin_label(4.444, 5.0, "C") -> "4-5°C" (rounds to int)
    assert result["winning_bin"] == "4-5°C", (
        f"winning_bin label must use converted C bounds (fix #262 P1), "
        f"got {result['winning_bin']!r}. "
        "40-41°F bin in London (C city) should label as '4-5°C', not '40-41°C'."
    )


# ---------------------------------------------------------------------------
# T2: F bin + C city -> obs 10C outside converted bin -> obs_outside_bin
# ---------------------------------------------------------------------------

def test_f_bin_c_city_obs_outside_converted_range_is_quarantined():
    """RELATIONSHIP: obs 10C is outside converted bin [4.44, 5.0C] -> quarantine."""
    conn = _make_world_conn()
    city = _london_city()
    result = _write_settlement_truth(
        conn, city, "2025-04-02", 40.0, 41.0,
        event_slug="london-high-2025-04-02",
        obs_row=_obs_c(10.0),  # 10C >> 5.0C hi-bound
        pm_bin_unit="F",
    )
    assert result["authority"] == "QUARANTINED"
    assert result["reason"] == "harvester_live_obs_outside_bin"


# ---------------------------------------------------------------------------
# T3: C bin + C city -> no conversion, bin check in C directly (control)
# ---------------------------------------------------------------------------

def test_c_bin_c_city_no_conversion_control():
    """CONTROL: C-original bin + C city -> containment in C, no conversion."""
    conn = _make_world_conn()
    city = _london_city()
    # bin [4, 5]C, obs 5C -> inside -> VERIFIED (no conversion)
    result = _write_settlement_truth(
        conn, city, "2025-04-03", 4.0, 5.0,
        event_slug="london-high-2025-04-03",
        obs_row=_obs_c(5.0),
        pm_bin_unit="C",
    )
    assert result["authority"] == "VERIFIED"
    assert result["reason"] is None


# ---------------------------------------------------------------------------
# T4: F bin + F city -> no conversion, containment in F (control)
# ---------------------------------------------------------------------------

def test_f_bin_f_city_no_conversion_control():
    """CONTROL: F bin + F city -> no conversion -> containment in F directly."""
    conn = _make_world_conn()
    city = _nyc_city()
    # bin [40, 41]F, obs 41F -> inside -> VERIFIED
    result = _write_settlement_truth(
        conn, city, "2025-04-04", 40.0, 41.0,
        event_slug="nyc-high-2025-04-04",
        obs_row=_obs_f(41.0),
        pm_bin_unit="F",
    )
    assert result["authority"] == "VERIFIED"
    assert result["reason"] is None


# ---------------------------------------------------------------------------
# T5-T7: _detect_bin_unit unit tests
# ---------------------------------------------------------------------------

def test_detect_bin_unit_f_symbol():
    """_detect_bin_unit returns 'F' for F-symbol questions."""
    assert _detect_bin_unit("Will London high be 40-41\xb0F on April 1?") == "F"
    assert _detect_bin_unit("42\xb0F or higher") == "F"
    assert _detect_bin_unit("35\xb0F or below") == "F"


def test_detect_bin_unit_c_symbol():
    """_detect_bin_unit returns 'C' for C-symbol questions."""
    assert _detect_bin_unit("Will London high be 4-5\xb0C on April 1?") == "C"
    assert _detect_bin_unit("17\xb0C or higher") == "C"


def test_detect_bin_unit_no_symbol():
    """_detect_bin_unit returns None when no degree symbol present."""
    assert _detect_bin_unit("Will London high be 40 on April 1?") is None
    assert _detect_bin_unit("") is None
    assert _detect_bin_unit("no temp here") is None


# ---------------------------------------------------------------------------
# T8: _f_to_c arithmetic
# ---------------------------------------------------------------------------

def test_f_to_c_arithmetic():
    """_f_to_c: (F - 32) * 5/9."""
    assert abs(_f_to_c(40.0) - 4.444444) < 1e-4   # (40-32)*5/9 = 4.444...
    assert abs(_f_to_c(41.0) - 5.0) < 1e-9         # (41-32)*5/9 = 5.0 exactly
    assert abs(_f_to_c(32.0) - 0.0) < 1e-9         # freezing point
    assert abs(_f_to_c(212.0) - 100.0) < 1e-9      # boiling point


# ---------------------------------------------------------------------------
# T9: open-shoulder F bin + C city -> converted hi -> obs C contained (regression)
# ---------------------------------------------------------------------------

def test_open_shoulder_f_bin_c_city_hi_only_contained():
    """REGRESSION: open-shoulder 'X F or below' converts hi bound to C."""
    conn = _make_world_conn()
    city = _london_city()
    # hi-only bin: None, 41F -> converted: None, 5.0C
    # obs 3C <= 5.0C -> contained -> VERIFIED
    result = _write_settlement_truth(
        conn, city, "2025-04-05", None, 41.0,
        event_slug="london-high-2025-04-05",
        obs_row=_obs_c(3.0),
        pm_bin_unit="F",
    )
    assert result["authority"] == "VERIFIED", (
        f"Expected VERIFIED for open-shoulder hi-only after F->C conversion, "
        f"got {result['authority']!r}. reason={result['reason']!r}"
    )


# ---------------------------------------------------------------------------
# T10-T12: Integer-snap containment (fix #264)
# ---------------------------------------------------------------------------

def test_f_bin_integer_snap_47_48_obs_9_is_verified():
    """ANTIBODY fix #264: [47,48]F -> {8,9}C integer set. obs=9 IS contained.

    Float containment: 9 <= floor(48*5/9 - 32*5/9) = 9 <= 8.888 -> False (BUG).
    Integer-snap: floor(8.888+0.5)=9, {8,9}, 9 in {8,9} -> True (CORRECT).
    """
    conn = _make_world_conn()
    city = _london_city()
    result = _write_settlement_truth(
        conn, city, "2025-01-25", 47.0, 48.0,
        event_slug="london-high-2025-01-25",
        obs_row=_obs_c(9.0),
        pm_bin_unit="F",
    )
    assert result["authority"] == "VERIFIED", (
        f"Expected VERIFIED: [47,48]F -> {{8,9}}C integer set, obs=9 is in set. "
        f"Got {result['authority']!r}, reason={result['reason']!r}. (fix #264)"
    )
    assert result["winning_bin"] == "8-9°C", (
        f"winning_bin should be '8-9°C' after integer-snap, got {result['winning_bin']!r}"
    )


def test_f_bin_integer_snap_40_41_obs_5_is_verified():
    """REGRESSION fix #264: [40,41]F -> {4,5}C. obs=5 in set -> VERIFIED."""
    conn = _make_world_conn()
    city = _london_city()
    result = _write_settlement_truth(
        conn, city, "2025-04-06", 40.0, 41.0,
        event_slug="london-high-2025-04-06",
        obs_row=_obs_c(5.0),
        pm_bin_unit="F",
    )
    assert result["authority"] == "VERIFIED", (
        f"Expected VERIFIED: [40,41]F -> {{4,5}}C, obs=5 in set. "
        f"Got {result['authority']!r}, reason={result['reason']!r}."
    )
    assert result["winning_bin"] == "4-5°C"


def test_f_bin_integer_snap_40_41_obs_6_is_outside():
    """BOUNDARY fix #264: [40,41]F -> {4,5}C. obs=6 not in set -> QUARANTINED."""
    conn = _make_world_conn()
    city = _london_city()
    result = _write_settlement_truth(
        conn, city, "2025-04-07", 40.0, 41.0,
        event_slug="london-high-2025-04-07",
        obs_row=_obs_c(6.0),
        pm_bin_unit="F",
    )
    assert result["authority"] == "QUARANTINED", (
        f"Expected QUARANTINED: [40,41]F -> {{4,5}}C, obs=6 not in set. "
        f"Got {result['authority']!r}."
    )
    assert result["reason"] in (
        "harvester_source_disagreement_within_tolerance",
        "harvester_live_obs_outside_bin",
    )
