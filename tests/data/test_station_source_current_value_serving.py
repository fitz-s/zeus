# Created: 2026-06-29
# Last audited: 2026-06-29
# Authority basis: operator "加数据" (add CWA/HKO station forecasts to the replacement fusion).
#   Station-calibrated sources carry their OWN provider cycle clock, independent of the gridded
#   freshness ceiling. read_current_instrument_values (single serving authority, registry #10)
#   must be able to include them by their own latest row — opt-in, so the 4 existing consumers
#   (seed_discovery, completeness, upgrade-trigger) keep byte-identical gridded-only behavior.
"""Station-source inclusion in the current-value serving authority (opt-in).

The bug this pins: a station row whose source_cycle_time is NEWER than the selected gridded
cycle ceiling is excluded by the 4 gridded passes (which serve source_cycle_time <= ceiling).
`include_station_sources=True` adds a station pass that serves cwa_*/hko_* by their own latest
single_runs row, regardless of the gridded ceiling. Default False leaves serving unchanged.
"""

from __future__ import annotations

import sqlite3

from src.data.replacement_current_value_serving import read_current_instrument_values

GRIDDED_CYCLE = "2026-06-28T18:00:00+00:00"   # the freshness ceiling passed by the materializer
STATION_CYCLE = "2026-06-29T06:22:00+00:00"   # station's OWN cycle — NEWER than the gridded ceiling
CAP = "2026-06-29T06:22:30+00:00"
TD = "2026-07-01"


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute(
        """
        CREATE TABLE raw_model_forecasts (
            raw_model_forecast_id INTEGER, model TEXT, forecast_value_c REAL,
            city TEXT, metric TEXT, target_date TEXT, lead_days INTEGER,
            source_cycle_time TEXT, endpoint TEXT, captured_at TEXT
        )
        """
    )
    return conn


def _insert(conn, rid, model, value, endpoint, *, cycle, captured, city="Taipei", lead=3):
    conn.execute(
        "INSERT INTO raw_model_forecasts VALUES (?,?,?,?,?,?,?,?,?,?)",
        (rid, model, value, city, "high", TD, lead, cycle, endpoint, captured),
    )


def test_station_source_excluded_by_default():
    conn = _conn()
    _insert(conn, 1, "ecmwf_ifs", 33.0, "single_runs", cycle=GRIDDED_CYCLE, captured="2026-06-29T00:00:00+00:00")
    _insert(conn, 2, "cwa_township", 35.0, "single_runs", cycle=STATION_CYCLE, captured=CAP)

    served = read_current_instrument_values(
        conn, city="Taipei", metric="high", target_date=TD, source_cycle_time_iso=GRIDDED_CYCLE
    )

    assert "ecmwf_ifs" in served
    assert "cwa_township" not in served  # newer-than-ceiling station row excluded by default


def test_station_source_served_when_opted_in():
    conn = _conn()
    _insert(conn, 1, "ecmwf_ifs", 33.0, "single_runs", cycle=GRIDDED_CYCLE, captured="2026-06-29T00:00:00+00:00")
    _insert(conn, 2, "cwa_township", 35.0, "single_runs", cycle=STATION_CYCLE, captured=CAP)

    served = read_current_instrument_values(
        conn, city="Taipei", metric="high", target_date=TD,
        source_cycle_time_iso=GRIDDED_CYCLE, include_station_sources=True,
    )

    assert served["ecmwf_ifs"].value_c == 33.0  # gridded serving unchanged
    assert served["cwa_township"].value_c == 35.0  # station served by its OWN cycle
    assert served["cwa_township"].served_via == "single_runs"


def test_station_latest_row_wins_when_opted_in():
    conn = _conn()
    _insert(conn, 1, "cwa_township", 34.0, "single_runs", cycle="2026-06-29T00:00:00+00:00", captured="2026-06-29T00:30:00+00:00")
    _insert(conn, 2, "cwa_township", 35.0, "single_runs", cycle=STATION_CYCLE, captured=CAP)

    served = read_current_instrument_values(
        conn, city="Taipei", metric="high", target_date=TD,
        source_cycle_time_iso=GRIDDED_CYCLE, include_station_sources=True,
    )

    assert served["cwa_township"].value_c == 35.0  # freshest captured wins
    assert served["cwa_township"].raw_model_forecast_id == 2


def test_hko_prefix_also_served_when_opted_in():
    conn = _conn()
    _insert(conn, 3, "hko_fnd", 32.0, "single_runs", cycle=STATION_CYCLE, captured=CAP, city="Hong Kong")

    served = read_current_instrument_values(
        conn, city="Hong Kong", metric="high", target_date=TD,
        source_cycle_time_iso=GRIDDED_CYCLE, include_station_sources=True,
    )

    assert served["hko_fnd"].value_c == 32.0


def test_gridded_only_db_unchanged_by_flag():
    # No station rows present: the flag must not alter gridded-only output at all.
    conn = _conn()
    _insert(conn, 1, "ecmwf_ifs", 33.0, "single_runs", cycle=GRIDDED_CYCLE, captured="2026-06-29T00:00:00+00:00")

    off = read_current_instrument_values(
        conn, city="Taipei", metric="high", target_date=TD, source_cycle_time_iso=GRIDDED_CYCLE
    )
    on = read_current_instrument_values(
        conn, city="Taipei", metric="high", target_date=TD,
        source_cycle_time_iso=GRIDDED_CYCLE, include_station_sources=True,
    )
    assert set(off) == set(on) == {"ecmwf_ifs"}


# Steady-state mixed case: the ceiling-bound passes (lines ~216-219) ALREADY claimed `out[model]`
# with a <= ceiling row before the station block runs. The station override must ALWAYS replace
# that slot with the model's true freshest (no-ceiling) row — this is the case the cold-start-only
# tests above never exercised (there, cwa_township was ABSENT from `out` when the station block
# ran, so "skip if already present" never actually got tested against a populated slot).
STALE_STATION_CYCLE = "2026-06-28T12:00:00+00:00"  # <= GRIDDED_CYCLE ceiling: claimed by ceiling pass first
STALE_CAP = "2026-06-28T12:00:30+00:00"


def test_station_source_override_replaces_stale_ceiling_bound_row_when_opted_in():
    conn = _conn()
    _insert(conn, 1, "cwa_township", 34.0, "single_runs", cycle=STALE_STATION_CYCLE, captured=STALE_CAP)
    _insert(conn, 2, "cwa_township", 36.0, "single_runs", cycle=STATION_CYCLE, captured=CAP)

    served = read_current_instrument_values(
        conn, city="Taipei", metric="high", target_date=TD,
        source_cycle_time_iso=GRIDDED_CYCLE, include_station_sources=True,
    )

    # True freshest (36.0, rid=2) must win, NOT the stale ceiling-bound row (34.0, rid=1) that the
    # gridded passes already parked in `out` before the station block ran.
    assert served["cwa_township"].value_c == 36.0
    assert served["cwa_township"].raw_model_forecast_id == 2


def test_station_source_ceiling_bound_unchanged_by_flag_in_mixed_case():
    # Same mixed seed as above, but include_station_sources=False: the station block must not run
    # at all, so a station model's gridded ceiling-bound serving stays byte-identical to before
    # this fix.
    conn = _conn()
    _insert(conn, 1, "cwa_township", 34.0, "single_runs", cycle=STALE_STATION_CYCLE, captured=STALE_CAP)
    _insert(conn, 2, "cwa_township", 36.0, "single_runs", cycle=STATION_CYCLE, captured=CAP)

    served = read_current_instrument_values(
        conn, city="Taipei", metric="high", target_date=TD,
        source_cycle_time_iso=GRIDDED_CYCLE, include_station_sources=False,
    )

    assert served["cwa_township"].value_c == 34.0
    assert served["cwa_township"].raw_model_forecast_id == 1
