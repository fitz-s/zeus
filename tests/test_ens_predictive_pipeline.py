# Lifecycle: created=2026-05-24; last_reviewed=2026-05-24; last_reused=never
# Purpose: Tests for the capstone DB-wired pipeline: load_paired_delta + fit_city_predictive_error.
# Reuse: Inspect ens_error_model.fit_city_predictive_error + ens_bias_repo before reuse.
"""TDD for the capstone DB-wired pipeline: paired-Δ loader + fit_city_predictive_error.

Ties together (all already unit-tested): load_bucket_residuals (F50 prior + F25 live),
load_paired_delta (Δ = F25 - F50 same date), transport_bias_prior (0.5->0.25 bridge),
posterior_bias (prior x live), predictive_error_from_posterior (location+scale+gate).
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from src.calibration.ens_bias_repo import load_paired_delta
from src.calibration.ens_error_model import PredictiveErrorModel, fit_city_predictive_error

OPD = "ecmwf_opendata_mx2t3_local_calendar_day_max_v1"
TIG = "tigge_mx2t6_local_calendar_day_max_v1"


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute(
        """CREATE TABLE ensemble_snapshots(
            city TEXT, target_date TEXT, temperature_metric TEXT, dataset_id TEXT,
            members_json TEXT, members_unit TEXT, lead_hours REAL, available_at TEXT,
            contributes_to_target_extrema INTEGER, boundary_ambiguous INTEGER,
            training_allowed INTEGER, causality_status TEXT, authority TEXT)"""
    )
    c.execute(
        """CREATE TABLE settlement_outcomes(city TEXT, target_date TEXT, temperature_metric TEXT,
            settlement_value REAL, authority TEXT)"""
    )
    return c


def _snap(conn, city, date, members, dv, *, unit="degC", contributes=1):
    conn.execute(
        "INSERT INTO ensemble_snapshots VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (city, date, "high", dv, json.dumps(members), unit, 24.0, "2026-05-10T00:00:00Z",
         contributes, 0, 1, "OK", "VERIFIED"),
    )


def test_load_paired_delta_f25_minus_f50_same_date(conn):
    # date with both products: F25 mean=20, F50 mean=18 -> Δ=+2 (0.25 warmer than 0.5)
    _snap(conn, "Tokyo", "2026-05-10", [19.0, 20.0, 21.0], OPD)   # F25 mean 20
    _snap(conn, "Tokyo", "2026-05-10", [17.0, 18.0, 19.0], TIG, contributes=None)  # F50 mean 18 (legacy NULL)
    _snap(conn, "Tokyo", "2026-05-11", [25.0], OPD)              # F25 only -> no pair
    d = load_paired_delta(conn, city="Tokyo", live_data_version=OPD, prior_data_version=TIG)
    assert d == pytest.approx([2.0])


def test_fit_city_predictive_error_applies_transport(conn):
    # F50 prior cold (-2), F25 warmer than F50 by ~+1.5 -> transported prior ~ -0.5
    for i, d in enumerate(["2026-05-08", "2026-05-09", "2026-05-10"]):
        _snap(conn, "Tokyo", d, [18.0 + i], TIG, contributes=None)     # F50
        _snap(conn, "Tokyo", d, [19.5 + i], OPD)                        # F25 = F50 + 1.5
        conn.execute("INSERT INTO settlement_outcomes VALUES (?,?,?,?,?)", ("Tokyo", d, "high", 20.0 + i, "VERIFIED"))
    em = fit_city_predictive_error(conn, city="Tokyo", live_data_version=OPD,
                                   prior_data_version=TIG, season_months=(3, 4, 5),
                                   min_live_n=1)
    assert isinstance(em, PredictiveErrorModel)
    # transport must have shifted the prior toward the F25 lineage (warmer than raw F50)
    assert em.bias_c > -2.0
