# Created: 2026-06-12
# Last reused or audited: 2026-06-12
# Authority basis: docs/archive/2026-Q2/operations_historical/day0_multiangle_critique_2026-06-12.md §(3) +
#   Blind spot D. Antibody for the settlement-extreme undercapture audit core.
"""Antibody tests: scripts/audit_day0_extreme_undercapture.run_undercapture_audit.

Builds a SYNTHETIC fixture DB pair (world: observation_instants; forecasts:
settlement_outcomes) and asserts the core audit:
  - an undercapture day (fast-lane reconstructed extreme below the HIGH
    settlement) is classified UNDER (the loss class);
  - an exact day (reconstruction == settlement) is classified EXACT;
  - a missing-observation day is classified MISSING;
  - a LOW-metric undercapture (reconstructed min ABOVE settlement) is UNDER.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from src.config import City
from scripts.audit_day0_extreme_undercapture import (
    EXACT,
    MISSING,
    OVER,
    UNDER,
    run_undercapture_audit,
)


def _make_world_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.execute(
        """
        CREATE TABLE observation_instants (
            id INTEGER PRIMARY KEY,
            city TEXT, target_date TEXT, source TEXT,
            utc_timestamp TEXT, temp_current REAL,
            running_max REAL, running_min REAL,
            temp_unit TEXT, station_id TEXT
        )
        """
    )
    conn.commit()
    conn.close()


def _make_forecasts_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.execute(
        """
        CREATE TABLE settlement_outcomes (
            settlement_id INTEGER PRIMARY KEY,
            city TEXT, target_date TEXT, temperature_metric TEXT,
            settlement_value REAL, settlement_unit TEXT,
            settlement_station TEXT, authority TEXT
        )
        """
    )
    conn.commit()
    conn.close()


def _insert_obs(path: Path, rows: list[tuple]) -> None:
    conn = sqlite3.connect(path)
    conn.executemany(
        "INSERT INTO observation_instants "
        "(city, target_date, source, utc_timestamp, temp_current, temp_unit, station_id) "
        "VALUES (?,?,?,?,?,?,?)",
        rows,
    )
    conn.commit()
    conn.close()


def _insert_settlement(path: Path, rows: list[tuple]) -> None:
    conn = sqlite3.connect(path)
    conn.executemany(
        "INSERT INTO settlement_outcomes "
        "(city, target_date, temperature_metric, settlement_value, settlement_unit, "
        "settlement_station, authority) VALUES (?,?,?,?,?,?,?)",
        rows,
    )
    conn.commit()
    conn.close()


def _fixture_city() -> City:
    # Unmeasured city name -> faithfulness gate defaults True -> fast-eligible.
    return City(
        name="Testville",
        lat=41.0,
        lon=-87.0,
        timezone="America/Chicago",
        settlement_unit="F",
        cluster="test",
        wu_station="KTST",
        settlement_source_type="wu_icao",
    )


@pytest.fixture
def dbs(tmp_path: Path):
    world = tmp_path / "world.db"
    forecasts = tmp_path / "forecasts.db"
    _make_world_db(world)
    _make_forecasts_db(forecasts)
    return world, forecasts


def test_undercapture_day_detected_and_exact_day_passes(dbs):
    world, forecasts = dbs
    city = _fixture_city()

    # Day A (UNDERCAPTURE): settlement HIGH = 73, but observations only reach 71.
    # Reconstructed max (71) < settlement (73) -> UNDER (the loss class).
    _insert_obs(
        world,
        [
            ("Testville", "2026-06-01", "wu_icao_history", "2026-06-01T15:00Z", 70.0, "F", "KTST"),
            ("Testville", "2026-06-01", "wu_icao_history", "2026-06-01T17:00Z", 71.0, "F", "KTST"),
        ],
    )
    # Day B (EXACT): settlement HIGH = 80; observations reach exactly 80.
    _insert_obs(
        world,
        [
            ("Testville", "2026-06-02", "wu_icao_history", "2026-06-02T15:00Z", 78.0, "F", "KTST"),
            ("Testville", "2026-06-02", "wu_icao_history", "2026-06-02T17:00Z", 80.0, "F", "KTST"),
        ],
    )
    # Day C (MISSING): settlement exists, but no observation rows.
    _insert_settlement(
        forecasts,
        [
            ("Testville", "2026-06-01", "high", 73.0, "F", "KTST", "VERIFIED"),
            ("Testville", "2026-06-02", "high", 80.0, "F", "KTST", "VERIFIED"),
            ("Testville", "2026-06-03", "high", 75.0, "F", "KTST", "VERIFIED"),
        ],
    )

    report = run_undercapture_audit(
        days=3650,  # wide window so the synthetic dates are in range
        world_db_path=world,
        forecasts_db_path=forecasts,
        cities=[city],
        now=__import__("datetime").datetime(2026, 6, 5, tzinfo=__import__("datetime").timezone.utc),
    )

    assert "Testville" in report.per_city
    crow = report.per_city["Testville"]
    cls = {(c.target_date, c.metric): c.classification for c in crow.cells}
    assert cls[("2026-06-01", "high")] == UNDER
    assert cls[("2026-06-02", "high")] == EXACT
    assert cls[("2026-06-03", "high")] == MISSING
    assert crow.under == 1
    assert crow.exact == 1
    assert crow.missing == 1
    assert report.total_under == 1
    assert report.total_exact == 1


def test_low_metric_undercapture_is_under(dbs):
    """LOW market: reconstructed MIN ABOVE settlement = the fast lane missed the
    settling low -> UNDER (loss class)."""
    world, forecasts = dbs
    city = _fixture_city()

    # Observations bottom out at 52, but settlement LOW = 50 -> reconstructed min
    # (52) > settlement (50) -> UNDER.
    _insert_obs(
        world,
        [
            ("Testville", "2026-06-01", "wu_icao_history", "2026-06-01T06:00Z", 54.0, "F", "KTST"),
            ("Testville", "2026-06-01", "wu_icao_history", "2026-06-01T08:00Z", 52.0, "F", "KTST"),
        ],
    )
    _insert_settlement(
        forecasts,
        [("Testville", "2026-06-01", "low", 50.0, "F", "KTST", "VERIFIED")],
    )

    report = run_undercapture_audit(
        days=3650,
        world_db_path=world,
        forecasts_db_path=forecasts,
        cities=[city],
        now=__import__("datetime").datetime(2026, 6, 5, tzinfo=__import__("datetime").timezone.utc),
    )
    crow = report.per_city["Testville"]
    assert crow.under == 1
    cell = crow.cells[0]
    assert cell.classification == UNDER
    assert cell.metric == "low"


def test_over_capture_classified_over(dbs):
    """Reconstructed max ABOVE settlement (HIGH) is OVER, not the loss class."""
    world, forecasts = dbs
    city = _fixture_city()
    _insert_obs(
        world,
        [("Testville", "2026-06-01", "wu_icao_history", "2026-06-01T15:00Z", 90.0, "F", "KTST")],
    )
    _insert_settlement(
        forecasts,
        [("Testville", "2026-06-01", "high", 88.0, "F", "KTST", "VERIFIED")],
    )
    report = run_undercapture_audit(
        days=3650, world_db_path=world, forecasts_db_path=forecasts, cities=[city],
        now=__import__("datetime").datetime(2026, 6, 5, tzinfo=__import__("datetime").timezone.utc),
    )
    crow = report.per_city["Testville"]
    assert crow.over == 1
    assert crow.cells[0].classification == OVER


def test_disputed_settlement_excluded(dbs):
    """DISPUTED settlements are not trusted truth and never enter the audit."""
    world, forecasts = dbs
    city = _fixture_city()
    _insert_obs(
        world,
        [("Testville", "2026-06-01", "wu_icao_history", "2026-06-01T15:00Z", 70.0, "F", "KTST")],
    )
    _insert_settlement(
        forecasts,
        [("Testville", "2026-06-01", "high", 73.0, "F", "KTST", "DISPUTED")],
    )
    report = run_undercapture_audit(
        days=3650, world_db_path=world, forecasts_db_path=forecasts, cities=[city],
        now=__import__("datetime").datetime(2026, 6, 5, tzinfo=__import__("datetime").timezone.utc),
    )
    assert report.per_city["Testville"].days_audited == 0
    assert report.total_under == 0
