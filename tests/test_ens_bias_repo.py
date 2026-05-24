# Lifecycle: created=2026-05-24; last_reviewed=2026-05-24; last_reused=never
# Purpose: DB I/O tests: degC unit normalization, authority/contributor/causality/boundary filters, leakage cutoff, LOW metric, model_bias_ens_v2 store, read safety.
# Reuse: Inspect ens_bias_repo before reuse.
"""TDD tests for the ENS bias DB I/O layer (residual loader + model_bias_ens_v2 store).

In-memory fixture with the columns the loader reads. Residuals are normalized to
CANONICAL degC (members + settlement share the city's native unit, read from
members_unit) so cross-city/cluster hierarchical estimation is unit-consistent
and degF cities are not mis-scaled by 1.8x.
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

OPD = "ecmwf_opendata_mx2t3_local_calendar_day_max_v1"
TIG = "tigge_mx2t6_local_calendar_day_max_v1"


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute(
        """CREATE TABLE ensemble_snapshots_v2(
            city TEXT, target_date TEXT, temperature_metric TEXT, data_version TEXT,
            members_json TEXT, members_unit TEXT, lead_hours REAL, available_at TEXT,
            contributes_to_target_extrema INTEGER, boundary_ambiguous INTEGER,
            training_allowed INTEGER, causality_status TEXT, authority TEXT)"""
    )
    c.execute(
        """CREATE TABLE settlements_v2(
            city TEXT, target_date TEXT, temperature_metric TEXT, settlement_value REAL,
            authority TEXT)"""
    )
    return c


def _snap(conn, city, date, members, *, unit="C", dv=OPD, metric="high", lead=24.0,
          avail="2026-05-10T00:00:00Z", contributes=1, boundary=0, training=1,
          causality="OK", authority="VERIFIED"):
    conn.execute(
        "INSERT INTO ensemble_snapshots_v2 VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (city, date, metric, dv, json.dumps(members), unit, lead, avail,
         contributes, boundary, training, causality, authority),
    )


def _settle(conn, city, date, value, *, metric="high", authority="VERIFIED"):
    conn.execute("INSERT INTO settlements_v2 VALUES (?,?,?,?,?)",
                 (city, date, metric, value, authority))


# ---- Blocker 1: unit normalization to degC ----

def test_residual_degF_city_converted_to_celsius(conn):
    # 68F mean, 70F actual -> residual (68-70)/1.8 = -1.111 degC (NOT -2)
    _snap(conn, "San Francisco", "2026-05-10", [66.0, 68.0, 70.0], unit="degF")
    _settle(conn, "San Francisco", "2026-05-10", 70.0)
    res = load_bucket_residuals(conn, city="San Francisco", data_version=OPD)
    assert res == pytest.approx([(68.0 - 70.0) / 1.8])
    assert abs(res[0] - (-1.111)) < 0.01


def test_residual_celsius_city_unchanged(conn):
    _snap(conn, "Tokyo", "2026-05-10", [18.0, 19.0, 20.0], unit="C")
    _settle(conn, "Tokyo", "2026-05-10", 21.0)
    assert load_bucket_residuals(conn, city="Tokyo", data_version=OPD) == pytest.approx([-2.0])


def test_freshest_snapshot_wins(conn):
    _snap(conn, "Tokyo", "2026-05-11", [10.0], avail="2026-05-09T00:00:00Z")
    _snap(conn, "Tokyo", "2026-05-11", [15.0], avail="2026-05-11T06:00:00Z")
    _settle(conn, "Tokyo", "2026-05-11", 16.0)
    assert load_bucket_residuals(conn, city="Tokyo", data_version=OPD) == pytest.approx([-1.0])


def test_filters_data_version_and_lead(conn):
    _snap(conn, "Tokyo", "2026-05-12", [10.0], dv=TIG)
    _snap(conn, "Tokyo", "2026-05-13", [10.0], lead=200.0)
    _settle(conn, "Tokyo", "2026-05-12", 12.0)
    _settle(conn, "Tokyo", "2026-05-13", 12.0)
    assert load_bucket_residuals(conn, city="Tokyo", data_version=OPD, lead_max=48) == []


def test_season_month_filter(conn):
    _snap(conn, "Tokyo", "2026-05-10", [19.0])
    _snap(conn, "Tokyo", "2026-07-10", [19.0])
    _settle(conn, "Tokyo", "2026-05-10", 20.0)
    _settle(conn, "Tokyo", "2026-07-10", 25.0)
    assert load_bucket_residuals(conn, city="Tokyo", data_version=OPD,
                                 season_months=(3, 4, 5)) == pytest.approx([-1.0])


# ---- Blocker 2: authority / contributor / causality / boundary filters ----

def test_full_contributor_only_excludes_noncontributing(conn):
    _snap(conn, "Tokyo", "2026-05-10", [19.0], contributes=0)            # non-contributor
    _snap(conn, "Tokyo", "2026-05-11", [19.0], boundary=1)               # boundary ambiguous
    _snap(conn, "Tokyo", "2026-05-12", [19.0], training=0)               # not training-allowed
    _snap(conn, "Tokyo", "2026-05-13", [19.0], causality="DEGRADED")     # causality not OK
    _snap(conn, "Tokyo", "2026-05-14", [19.0])                            # clean
    for d in ("10", "11", "12", "13", "14"):
        _settle(conn, "Tokyo", f"2026-05-{d}", 20.0)
    res = load_bucket_residuals(conn, city="Tokyo", data_version=OPD,
                                contributor_policy="full_contributor_only")
    assert res == pytest.approx([-1.0]), "only the clean contributing row survives"


def test_unverified_authority_excluded(conn):
    _snap(conn, "Tokyo", "2026-05-10", [19.0], authority="UNVERIFIED")
    _settle(conn, "Tokyo", "2026-05-10", 20.0)
    assert load_bucket_residuals(conn, city="Tokyo", data_version=OPD,
                                 require_verified=True) == []


def test_diagnostic_policy_includes_all(conn):
    _snap(conn, "Tokyo", "2026-05-10", [19.0], contributes=0)
    _settle(conn, "Tokyo", "2026-05-10", 20.0)
    res = load_bucket_residuals(conn, city="Tokyo", data_version=OPD,
                                contributor_policy="all_for_diagnostic")
    assert res == pytest.approx([-1.0])


# ---- Blocker 7: leakage / training cutoff ----

def test_settled_before_cutoff_excludes_future(conn):
    _snap(conn, "Tokyo", "2026-05-10", [19.0])
    _snap(conn, "Tokyo", "2026-05-20", [19.0])
    _settle(conn, "Tokyo", "2026-05-10", 20.0)
    _settle(conn, "Tokyo", "2026-05-20", 20.0)
    res = load_bucket_residuals(conn, city="Tokyo", data_version=OPD,
                                settled_before="2026-05-15")
    assert res == pytest.approx([-1.0]), "only pre-cutoff target dates used"


# ---- Blocker 6: LOW metric ----

def test_low_metric_residual_sign(conn):
    # LOW: forecast min 6, actual 5 -> residual = +1 (forecast warm on the low)
    _snap(conn, "Tokyo", "2026-05-10", [5.0, 6.0, 7.0], metric="low")
    _settle(conn, "Tokyo", "2026-05-10", 5.0, metric="low")
    assert load_bucket_residuals(conn, city="Tokyo", data_version=OPD,
                                 metric="low") == pytest.approx([1.0])


# ---- Blocker 3 + 5: store roundtrip with lineage, read safety ----

def test_model_bias_ens_v2_roundtrip_with_lineage(conn):
    init_ens_bias_schema(conn)
    write_bias_model(
        conn, city="San Francisco", season="MAM", month=5, metric="high",
        live_data_version=OPD, prior_data_version=TIG,
        live_source_id="ecmwf_opendata", prior_source_id="tigge",
        bias_unit="C", posterior_bias_c=-3.2, posterior_sd_c=0.7,
        n_live=14, n_prior=238, n_paired=12, weight_live=0.6,
        paired_delta_c=-1.28, v0_c2=0.30, vo_c2=0.02,
        estimator="empirical_bayes_shrinkage_v1", training_cutoff="2026-05-20",
        contributor_policy="full_contributor_only",
    )
    row = read_bias_model(conn, city="San Francisco", season="MAM", month=5,
                          metric="high", live_data_version=OPD)
    assert row is not None
    assert row["posterior_bias_c"] == pytest.approx(-3.2)
    assert row["bias_unit"] == "C"
    assert row["n_paired"] == 12
    assert row["training_cutoff"] == "2026-05-20"


def test_read_bias_model_requires_exact_live_data_version(conn):
    init_ens_bias_schema(conn)
    write_bias_model(conn, city="Tokyo", season="MAM", month=5, metric="high",
                     live_data_version=OPD, prior_data_version=TIG,
                     live_source_id="ecmwf_opendata", prior_source_id="tigge",
                     bias_unit="C", posterior_bias_c=-1.0, posterior_sd_c=0.5,
                     n_live=20, n_prior=200, n_paired=10, weight_live=0.5,
                     paired_delta_c=0.0, v0_c2=0.3, vo_c2=0.02,
                     estimator="empirical_bayes_shrinkage_v1", training_cutoff=None,
                     contributor_policy="full_contributor_only")
    # missing live_data_version must NOT silently return an arbitrary row
    with pytest.raises(ValueError, match="live_data_version"):
        read_bias_model(conn, city="Tokyo", season="MAM", month=5, metric="high")
    # exact lookup works
    assert read_bias_model(conn, city="Tokyo", season="MAM", month=5, metric="high",
                           live_data_version=OPD) is not None


def test_to_c_handles_degf_degc_strings():
    from src.calibration.ens_bias_repo import _to_c
    assert _to_c(68.0, "degF") == 20.0
    assert _to_c(68.0, "F") == 20.0
    assert _to_c(20.0, "degC") == 20.0
    assert _to_c(20.0, "C") == 20.0
    import pytest as _pt
    with _pt.raises(ValueError):
        _to_c(20.0, "kelvin?")


def test_legacy_tigge_null_passthrough_includes_null_contributes(conn):
    # legacy TIGGE rows carry contributes_to_target_extrema=NULL; the prior loader
    # must include them under the legacy policy but NOT under full_contributor_only.
    TIGV = "tigge_mx2t6_local_calendar_day_max_v1"
    conn.execute("INSERT INTO ensemble_snapshots_v2 VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                 ("Tokyo","2026-05-10","high",TIGV, json.dumps([19.0]),"C",24.0,
                  "2026-05-10T00:00:00Z", None, 0, 1, "OK", "VERIFIED"))
    _settle(conn,"Tokyo","2026-05-10",20.0)
    assert load_bucket_residuals(conn, city="Tokyo", data_version=TIGV,
                                 contributor_policy="full_contributor_only") == []
    assert load_bucket_residuals(conn, city="Tokyo", data_version=TIGV,
                                 contributor_policy="legacy_tigge_null_passthrough") == pytest.approx([-1.0])
