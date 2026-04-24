"""K4.5 unit tests for src/data/rebuild_validators.py.

All tests use :memory: SQLite so they never touch the production DB.
"""
from __future__ import annotations

import math
import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

PROJECT_ROOT = Path(__file__).parent.parent


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_conn() -> sqlite3.Connection:
    """In-memory DB with availability_fact table."""
    import sys
    sys.path.insert(0, str(PROJECT_ROOT))
    from src.state.db import init_schema

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    init_schema(conn)
    return conn


def _make_city(name="NYC", settlement_unit="F", lat=40.6, lon=-73.8):
    """Lightweight City-like object."""
    from dataclasses import make_dataclass
    import sys
    sys.path.insert(0, str(PROJECT_ROOT))
    from src.config import City
    return City(
        name=name,
        lat=lat,
        lon=lon,
        timezone="America/New_York",
        settlement_unit=settlement_unit,
        cluster=name,
        wu_station="KJFK",
    )


def _make_members(count: int = 51, base_value: float = 85.0) -> list[dict]:
    """Return a list of member dicts with value_native_unit."""
    return [{"value_native_unit": base_value + i * 0.1} for i in range(count)]


# ---------------------------------------------------------------------------
# test_validator_rejects_impossible_kelvin
# A raw value in the Kelvin dead zone [MIN_KELVIN_EARTH - epsilon] should raise.
# ---------------------------------------------------------------------------

def test_validator_rejects_impossible_kelvin():
    """Values above 200 but outside [180, 335] Kelvin plausible range raise ImpossibleTemperatureError."""
    import sys
    sys.path.insert(0, str(PROJECT_ROOT))
    from src.data.rebuild_validators import (
        ImpossibleTemperatureError,
        validate_ensemble_snapshot_for_calibration,
    )

    conn = _make_conn()
    city = _make_city(settlement_unit="C")

    # 150 K is below MIN_KELVIN_VALID (180). Above KELVIN_DETECTION_THRESHOLD (200)? No.
    # Actually 150 < 200, so it doesn't trigger Kelvin path. But >MAX_CELSIUS_EARTH (60)? No.
    # This should pass cleanly as a Celsius value.
    # Per spec: test_validator_rejects_impossible_kelvin should use a value > 200 that is
    # outside [180, 335]. E.g. 350.0 K.
    bad_members = [{"value_native_unit": 350.0}] * 51  # 350 K > 335 (MAX_KELVIN_EARTH)
    with pytest.raises(ImpossibleTemperatureError):
        validate_ensemble_snapshot_for_calibration(bad_members, city, conn, target_date="2025-07-01")


# ---------------------------------------------------------------------------
# test_validator_rejects_kelvin_unit_150 (K-declared dead-zone closure)
# An obs row with unit='K' and raw=150 (below MIN_KELVIN_VALID=180) must raise.
# ---------------------------------------------------------------------------

def test_validator_rejects_kelvin_unit_150():
    """Obs row unit='K', raw=150 (below MIN_KELVIN_VALID=180) -> ImpossibleTemperatureError."""
    import sys
    sys.path.insert(0, str(PROJECT_ROOT))
    from src.data.rebuild_validators import (
        ImpossibleTemperatureError,
        validate_observation_for_settlement,
    )

    conn = _make_conn()
    city = _make_city(settlement_unit="F")
    obs = {"high_temp": 150.0, "unit": "K", "target_date": "2025-07-01"}
    with pytest.raises(ImpossibleTemperatureError):
        validate_observation_for_settlement(obs, city, conn)


# ---------------------------------------------------------------------------
# test_validator_rejects_nan_members
# ---------------------------------------------------------------------------

def test_validator_rejects_nan_members():
    """Members list with one NaN value raises ImpossibleTemperatureError."""
    import sys
    sys.path.insert(0, str(PROJECT_ROOT))
    from src.data.rebuild_validators import (
        ImpossibleTemperatureError,
        validate_ensemble_snapshot_for_calibration,
    )

    conn = _make_conn()
    city = _make_city(settlement_unit="F")

    # 51 members but one is NaN
    members = [{"value_native_unit": 85.0 + i * 0.1} for i in range(50)]
    members.append({"value_native_unit": float("nan")})
    assert len(members) == 51

    with pytest.raises(ImpossibleTemperatureError):
        validate_ensemble_snapshot_for_calibration(members, city, conn, target_date="2025-07-01")


# ---------------------------------------------------------------------------
# test_validator_rejects_10_members
# ---------------------------------------------------------------------------

def test_validator_rejects_10_members():
    """Only 10 members instead of 51 raises EnsembleIntegrityError."""
    import sys
    sys.path.insert(0, str(PROJECT_ROOT))
    from src.data.rebuild_validators import (
        EnsembleIntegrityError,
        validate_ensemble_snapshot_for_calibration,
    )

    conn = _make_conn()
    city = _make_city(settlement_unit="F")
    members = _make_members(count=10)

    with pytest.raises(EnsembleIntegrityError) as exc_info:
        validate_ensemble_snapshot_for_calibration(members, city, conn, target_date="2025-07-01")
    assert exc_info.value.actual_count == 10
    assert exc_info.value.expected_count == 51


# ---------------------------------------------------------------------------
# test_rebuild_ensemble_kelvin_conversion_celsius_city
# ---------------------------------------------------------------------------

def test_rebuild_ensemble_kelvin_conversion_celsius_city():
    """305.0 K input for Celsius city -> ~31.85 C returned (no rejection)."""
    import sys
    sys.path.insert(0, str(PROJECT_ROOT))
    from src.data.rebuild_validators import validate_ensemble_snapshot_for_calibration

    conn = _make_conn()
    city = _make_city(name="Paris", settlement_unit="C", lat=48.9, lon=2.4)

    # 51 members all at 305.0 K
    members = [{"value_native_unit": 305.0}] * 51
    result = validate_ensemble_snapshot_for_calibration(
        members, city, conn, target_date="2025-07-01"
    )
    assert len(result) == 51
    expected_c = 305.0 - 273.15  # ~31.85
    for val in result:
        assert abs(val - expected_c) < 0.01, f"Expected ~{expected_c}, got {val}"


# ---------------------------------------------------------------------------
# test_validator_rejection_logs_to_availability_fact
# ---------------------------------------------------------------------------

def test_validator_rejection_logs_to_availability_fact():
    """Trigger a rejection, SELECT from availability_fact, assert exactly one row exists."""
    import sys
    sys.path.insert(0, str(PROJECT_ROOT))
    from src.data.rebuild_validators import (
        EnsembleIntegrityError,
        validate_ensemble_snapshot_for_calibration,
    )

    conn = _make_conn()
    city = _make_city(name="NYC", settlement_unit="F")

    # Only 5 members -> EnsembleIntegrityError -> logs to availability_fact
    members = _make_members(count=5)
    with pytest.raises(EnsembleIntegrityError):
        validate_ensemble_snapshot_for_calibration(
            members, city, conn, target_date="2025-07-01"
        )

    rows = conn.execute(
        "SELECT * FROM availability_fact WHERE scope_key LIKE 'NYC%'"
    ).fetchall()
    assert len(rows) == 1, f"Expected 1 availability_fact row, got {len(rows)}"
    assert rows[0]["failure_type"] == "EnsembleIntegrityError"
