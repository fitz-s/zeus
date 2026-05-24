# Created: 2026-05-24
# Last reused/audited: 2026-05-24
# Authority basis: operator hierarchical-bias adjudication 2026-05-24 §9 (model_bias_ens_v2,
#   ENS-product residuals from ensemble_snapshots_v2 × settlements_v2).
"""TDD tests for the ENS bias DB I/O layer (residual loader + model_bias_ens_v2 store).

Uses an in-memory fixture with the minimal columns the loader reads, so the test
is isolated from the full forecasts schema. Residuals are computed in the city's
NATIVE unit (members and settlement share it), since the downstream correction is
applied to member extrema in native unit.
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from src.calibration.ens_bias_repo import (
    init_ens_bias_schema,
    load_bucket_residuals,
    read_bias_model,
    write_bias_model,
)


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute(
        """CREATE TABLE ensemble_snapshots_v2(
            city TEXT, target_date TEXT, temperature_metric TEXT, data_version TEXT,
            members_json TEXT, members_unit TEXT, lead_hours REAL, available_at TEXT,
            contributes_to_target_extrema INTEGER)"""
    )
    c.execute(
        """CREATE TABLE settlements_v2(
            city TEXT, target_date TEXT, temperature_metric TEXT, settlement_value REAL)"""
    )
    return c


def _snap(conn, city, date, members, *, unit="C", dv="ecmwf_opendata_mx2t3_local_calendar_day_max_v1",
          lead=24.0, avail="2026-05-10T00:00:00Z", contributes=1):
    conn.execute(
        "INSERT INTO ensemble_snapshots_v2 VALUES (?,?,?,?,?,?,?,?,?)",
        (city, date, "high", dv, json.dumps(members), unit, lead, avail, contributes),
    )


def _settle(conn, city, date, value):
    conn.execute("INSERT INTO settlements_v2 VALUES (?,?,?,?)", (city, date, "high", value))


def test_load_bucket_residuals_mean_minus_actual(conn):
    # ens mean = 19.0, actual = 21.0 -> residual = -2.0 (forecast cold), native unit
    _snap(conn, "Tokyo", "2026-05-10", [18.0, 19.0, 20.0])
    _settle(conn, "Tokyo", "2026-05-10", 21.0)
    res = load_bucket_residuals(conn, city="Tokyo",
                                data_version="ecmwf_opendata_mx2t3_local_calendar_day_max_v1")
    assert res == pytest.approx([-2.0])


def test_load_bucket_residuals_freshest_snapshot_wins(conn):
    # two snapshots same (city,date) — the later available_at must win
    _snap(conn, "Tokyo", "2026-05-11", [10.0, 10.0, 10.0], avail="2026-05-09T00:00:00Z")
    _snap(conn, "Tokyo", "2026-05-11", [15.0, 15.0, 15.0], avail="2026-05-11T06:00:00Z")
    _settle(conn, "Tokyo", "2026-05-11", 16.0)
    res = load_bucket_residuals(conn, city="Tokyo",
                                data_version="ecmwf_opendata_mx2t3_local_calendar_day_max_v1")
    assert res == pytest.approx([-1.0])  # 15 - 16, freshest used


def test_load_bucket_residuals_filters_data_version_and_lead(conn):
    _snap(conn, "Tokyo", "2026-05-12", [10.0], dv="tigge_mx2t6_local_calendar_day_max_v1")
    _snap(conn, "Tokyo", "2026-05-13", [10.0], lead=200.0)  # beyond lead_max
    _settle(conn, "Tokyo", "2026-05-12", 12.0)
    _settle(conn, "Tokyo", "2026-05-13", 12.0)
    res = load_bucket_residuals(conn, city="Tokyo",
                                data_version="ecmwf_opendata_mx2t3_local_calendar_day_max_v1",
                                lead_max=48)
    assert res == []  # wrong dv + over-lead both excluded


def test_load_bucket_residuals_season_month_filter(conn):
    _snap(conn, "Tokyo", "2026-05-10", [19.0])   # MAM
    _snap(conn, "Tokyo", "2026-07-10", [19.0])   # JJA
    _settle(conn, "Tokyo", "2026-05-10", 20.0)
    _settle(conn, "Tokyo", "2026-07-10", 25.0)
    res = load_bucket_residuals(conn, city="Tokyo",
                                data_version="ecmwf_opendata_mx2t3_local_calendar_day_max_v1",
                                season_months=(3, 4, 5))
    assert res == pytest.approx([-1.0])  # only the MAM row


def test_model_bias_ens_v2_roundtrip(conn):
    init_ens_bias_schema(conn)
    write_bias_model(conn, city="San Francisco", season="MAM", metric="high",
                     live_data_version="ecmwf_opendata_mx2t3_local_calendar_day_max_v1",
                     prior_data_version="tigge_mx2t6_local_calendar_day_max_v1",
                     posterior_bias_c=-3.2, posterior_sd_c=0.7, n_live=14, n_prior=238,
                     weight_live=0.6, estimator="empirical_bayes_shrinkage_v1")
    row = read_bias_model(conn, city="San Francisco", season="MAM", metric="high")
    assert row is not None
    assert row["posterior_bias_c"] == pytest.approx(-3.2)
    assert row["n_live"] == 14
    assert row["weight_live"] == pytest.approx(0.6)
