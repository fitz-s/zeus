# Created: 2026-05-22
# Last reused or audited: 2026-05-22
# Authority basis: docs/operations/P0_FORECAST_EXTREMA_AUTHORITY_2026-05-22.md
# Lifecycle: created=2026-05-22; last_reviewed=2026-05-22; last_reused=never
# Purpose: Regression antibody for PR-A snapshot ORDER BY fix — contributing 00Z must beat latest 12Z.
# Reuse: Run when _snapshot_query_sql ORDER BY CASE, contributes_to_target_extrema semantics, or attribution logic changes.
"""
PR-A regression tests: snapshot selection ORDER BY must prefer
contributes_to_target_extrema=1 runs over the latest (post-peak) run.

Test structure
--------------
These tests execute the PRODUCTION SQL from
``src.data.executable_forecast_reader._snapshot_query_sql`` against an
in-memory SQLite fixture.  They do NOT re-declare the ORDER BY logic
— if the CASE expression is removed from the production function the
tests will fail, which is the point.

Fixture scenario (Taipei-style far-east city):
  - 00Z snapshot: contributes_to_target_extrema=1, attribution=FULLY_INSIDE,
    members≈[33.0, 33.5] → max=33.5  (warm, peak-capturing run)
  - 12Z snapshot: contributes_to_target_extrema=0, attribution=UNKNOWN,
    members≈[27.0, 27.6] → max=27.6  (cold, post-peak run, latest cycle)

PR-A must select the 00Z warm run, not the 12Z cold run.

Control scenario (Amsterdam-style):
  - 00Z snapshot: contributes_to_target_extrema=1, members≈[20.0]
  - 12Z snapshot: contributes_to_target_extrema=1, attribution=FULLY_INSIDE,
    members≈[24.0]  (latest, also contributing)

PR-A must still select the 12Z (latest among contributors) — no regression
on healthy data where the latest run already contributes.
"""
from __future__ import annotations

import json
import sqlite3

import pytest

# ---------------------------------------------------------------------------
# Production SQL source (must NOT be duplicated here — import the function)
# ---------------------------------------------------------------------------
from src.data.executable_forecast_reader import _snapshot_query_sql  # type: ignore[import]


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

_COMMON_COLS = (
    "snapshot_id, city, target_date, temperature_metric, dataset_id, "
    "source_id, source_transport, source_cycle_time, available_at, "
    "contributes_to_target_extrema, forecast_window_attribution_status, "
    "boundary_ambiguous, members_json"
)

_CREATE_TABLE = """
CREATE TABLE ensemble_snapshots (
    snapshot_id INTEGER PRIMARY KEY,
    city TEXT NOT NULL,
    target_date TEXT NOT NULL,
    temperature_metric TEXT NOT NULL,
    dataset_id TEXT NOT NULL,
    source_id TEXT NOT NULL,
    source_transport TEXT NOT NULL,
    source_cycle_time TEXT NOT NULL,
    available_at TEXT NOT NULL,
    contributes_to_target_extrema INTEGER,
    forecast_window_attribution_status TEXT,
    boundary_ambiguous INTEGER,
    members_json TEXT NOT NULL
)
"""

_DATA_VERSION = "ecmwf_opendata_mx2t3_local_calendar_day_max"
_SOURCE_ID = "ecmwf_open_data"
_SOURCE_TRANSPORT = "ensemble_snapshots_db_reader"
_TARGET_DATE = "2026-05-22"
_METRIC = "high"


def _mem_db() -> sqlite3.Connection:
    """Return an in-memory connection with ensemble_snapshots created."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(_CREATE_TABLE)
    conn.commit()
    return conn


def _insert_snapshot(
    conn: sqlite3.Connection,
    *,
    snapshot_id: int,
    city: str,
    source_cycle_time: str,
    available_at: str,
    contributes: int | None,
    attribution: str | None,
    boundary_ambiguous: int,
    members: list[float],
) -> None:
    conn.execute(
        f"INSERT INTO ensemble_snapshots ({_COMMON_COLS}) VALUES "
        "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            snapshot_id,
            city,
            _TARGET_DATE,
            _METRIC,
            _DATA_VERSION,
            _SOURCE_ID,
            _SOURCE_TRANSPORT,
            source_cycle_time,
            available_at,
            contributes,
            attribution,
            boundary_ambiguous,
            json.dumps(members),
        ),
    )
    conn.commit()


def _select_via_production_sql(
    conn: sqlite3.Connection, city: str
) -> sqlite3.Row | None:
    """Execute the production snapshot-selection SQL against the fixture DB.

    Uses table="ensemble_snapshots" (unqualified) so the SQL references
    the plain table in the :memory: connection with no ATTACH prefix.
    source_run_id_present=False matches the no-source-run-filter variant.
    """
    sql = _snapshot_query_sql("ensemble_snapshots", source_run_id_present=False)
    # Parameters: city, target_date, temperature_metric, data_version,
    #             source_id, source_transport  (6 positional, no source_run_id)
    return conn.execute(
        sql,
        (city, _TARGET_DATE, _METRIC, _DATA_VERSION, _SOURCE_ID, _SOURCE_TRANSPORT),
    ).fetchone()


# ---------------------------------------------------------------------------
# Test 1: PR-A fix — cold post-peak 12Z is NOT selected when warm 00Z
#         contributes_to_target_extrema=1
# ---------------------------------------------------------------------------

class TestFarEastSelectionFix:
    """The warm contributing run must win over the cold latest run."""

    def test_warm_contributing_00z_selected_over_cold_noncontributing_12z(self):
        """snapshot_query_sql must rank contributes=1 ahead of latest-first.

        Fixture: two Taipei snapshots for 2026-05-22:
          sid=1: 00Z, contributes=1, attribution=FULLY_INSIDE, members=[33.0,33.5]
          sid=2: 12Z, contributes=0, attribution=UNKNOWN,      members=[27.0,27.6]

        OLD behaviour (latest-first): would pick sid=2 (12Z, cold, max=27.6).
        PR-A behaviour (contributing-first): must pick sid=1 (00Z, warm, max=33.5).
        """
        conn = _mem_db()
        _insert_snapshot(
            conn,
            snapshot_id=1,
            city="Taipei",
            source_cycle_time="2026-05-22T00:00:00+00:00",
            available_at="2026-05-22T06:00:00+00:00",
            contributes=1,
            attribution="FULLY_INSIDE_TARGET_LOCAL_DAY",
            boundary_ambiguous=0,
            members=[33.0, 33.5],
        )
        _insert_snapshot(
            conn,
            snapshot_id=2,
            city="Taipei",
            source_cycle_time="2026-05-22T12:00:00+00:00",
            available_at="2026-05-22T18:00:00+00:00",
            contributes=0,
            attribution="UNKNOWN",
            boundary_ambiguous=0,
            members=[27.0, 27.6],
        )

        selected = _select_via_production_sql(conn, "Taipei")

        assert selected is not None, "Expected a row to be selected"
        assert selected["snapshot_id"] == 1, (
            f"PR-A must select the 00Z contributing snapshot (sid=1, max=33.5), "
            f"but got sid={selected['snapshot_id']} cycle={selected['source_cycle_time']} "
            f"contributes={selected['contributes_to_target_extrema']}"
        )
        assert selected["contributes_to_target_extrema"] == 1, (
            "Selected snapshot must have contributes_to_target_extrema=1"
        )
        members = json.loads(selected["members_json"])
        assert max(members) == pytest.approx(33.5), (
            f"Selected snapshot members_json max should be 33.5 (warm); got {max(members)}"
        )

    def test_selected_snapshot_is_warm_not_cold(self):
        """Concise max-value assertion: new selection max > old selection max.

        Specifically: new max ~33.5 >> cold max ~27.6.
        """
        conn = _mem_db()
        _insert_snapshot(
            conn, snapshot_id=10, city="Seoul",
            source_cycle_time="2026-05-22T00:00:00+00:00",
            available_at="2026-05-22T06:00:00+00:00",
            contributes=1, attribution="FULLY_INSIDE_TARGET_LOCAL_DAY",
            boundary_ambiguous=0, members=[25.5, 26.2],
        )
        _insert_snapshot(
            conn, snapshot_id=11, city="Seoul",
            source_cycle_time="2026-05-22T12:00:00+00:00",
            available_at="2026-05-22T18:00:00+00:00",
            contributes=0, attribution="UNKNOWN",
            boundary_ambiguous=0, members=[19.0, 19.7],
        )

        selected = _select_via_production_sql(conn, "Seoul")

        assert selected is not None
        members = json.loads(selected["members_json"])
        selected_max = max(members)
        assert selected_max > 25.0, (
            f"PR-A should select the warm run (max>25°C); got max={selected_max} "
            f"(cold run sid=11 would give max≈19.7)"
        )


# ---------------------------------------------------------------------------
# Test 2: Control — no regression when latest run is already contributing
# ---------------------------------------------------------------------------

class TestControlNoRegression:
    """When the latest cycle already contributes=1, it must still be selected."""

    def test_latest_contributing_run_is_selected_when_both_contribute(self):
        """Amsterdam-style: 00Z contributes=1, 12Z contributes=1 (latest).

        PR-A must select 12Z (latest among contributors), matching OLD behaviour.
        This ensures the fix doesn't regress cities where latest run is correct.
        """
        conn = _mem_db()
        _insert_snapshot(
            conn,
            snapshot_id=100,
            city="Amsterdam",
            source_cycle_time="2026-05-22T00:00:00+00:00",
            available_at="2026-05-22T06:00:00+00:00",
            contributes=1,
            attribution="FULLY_INSIDE_TARGET_LOCAL_DAY",
            boundary_ambiguous=0,
            members=[20.0, 21.0],
        )
        _insert_snapshot(
            conn,
            snapshot_id=101,
            city="Amsterdam",
            source_cycle_time="2026-05-22T12:00:00+00:00",
            available_at="2026-05-22T18:00:00+00:00",
            contributes=1,
            attribution="FULLY_INSIDE_TARGET_LOCAL_DAY",
            boundary_ambiguous=0,
            members=[23.5, 24.5],
        )

        selected = _select_via_production_sql(conn, "Amsterdam")

        assert selected is not None, "Expected a row to be selected"
        assert selected["snapshot_id"] == 101, (
            f"When both runs contribute, the latest (12Z, sid=101) must be selected; "
            f"got sid={selected['snapshot_id']} cycle={selected['source_cycle_time']}"
        )
        assert selected["contributes_to_target_extrema"] == 1, (
            "Selected snapshot must have contributes_to_target_extrema=1"
        )

    def test_only_run_is_selected_regardless_of_contributes_flag(self):
        """When only one snapshot exists, it must be selected regardless of
        contributes_to_target_extrema value (no row = no forecast available).

        Ensures the ORDER BY CASE doesn't accidentally suppress a sole row.
        """
        conn = _mem_db()
        _insert_snapshot(
            conn,
            snapshot_id=200,
            city="Chicago",
            source_cycle_time="2026-05-22T12:00:00+00:00",
            available_at="2026-05-22T18:00:00+00:00",
            contributes=1,
            attribution="FULLY_INSIDE_TARGET_LOCAL_DAY",
            boundary_ambiguous=0,
            members=[70.0, 71.5],
        )

        selected = _select_via_production_sql(conn, "Chicago")

        assert selected is not None, "Sole row must always be selected"
        assert selected["snapshot_id"] == 200
