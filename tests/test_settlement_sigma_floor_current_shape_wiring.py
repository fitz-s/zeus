# Created: 2026-09-13
# Last reused/audited: 2026-10-09
# Lifecycle: created=2026-09-13; last_reviewed=2026-10-09; last_reused=2026-10-09
# Authority basis: Root single-q law; finite_evidence_probability_symmetry/PLAN.md.
# Purpose: Historical settlement floors cannot change current-evidence q or bounds.
# Reuse: Run when changing current-evidence floor isolation or Day0 floor provenance.
"""Current-shape floor isolation with canonical source/ground/anchor fixtures.

The former floor-on/catch-all-cap expectations are obsolete under the current
single-q law. Preserve probability, bounds and truthful-provenance obligations
without reconstructing the deleted manual fusion or synthetic carrier doubles.
"""
from __future__ import annotations

import json
import math
from dataclasses import replace
from datetime import timedelta

import pytest

import src.config as cfg
import src.data.replacement_forecast_materializer as materializer_mod
from src.calibration import emos
from src.data.replacement_forecast_materializer import materialize_replacement_forecast_live
from tests.test_replacement_forecast_materializer import (
    _TemperatureBin,
    _conn,
    _current_baseline_data_version,
    _hko_dt as _dt,
    _hko_native_surfaces,
    _hko_request as _request,
    _hko_source_surface,
)
from tests.test_sigma_tau_calibration_serving_equivalence import (
    _REQUEST_KWARGS,
    _full_row,
    _install_current_evidence_fusion,
)

pytestmark = pytest.mark.usefixtures("_hko_source_surface")


@pytest.fixture
def floor_artifact(monkeypatch, tmp_path):
    """A real diagnostic artifact, isolated from every runtime state directory."""
    monkeypatch.setattr(cfg, "runtime_state_path", lambda name: tmp_path / name)
    path = tmp_path / "settlement_sigma_floor.json"
    monkeypatch.setattr(emos, "_SIGMA_FLOOR_PATH", path)
    monkeypatch.setattr(emos, "_sigma_floor_cache", None)
    monkeypatch.setattr(emos, "_sigma_floor_mtime_ns", None)

    def write(floor_c, metric):
        path.write_text(json.dumps({
            "_meta": {"method": "detrended-45d", "k_default": 0.8},
            "cells": {f"Hong Kong|SON|{metric}": {
                "sigma_floor_c": floor_c / 0.8, "n": 30, "window": "45d-cross-season",
            }},
        }))
        # Each test transition exercises the file loader, independently of its cache.
        monkeypatch.setattr(emos, "_sigma_floor_cache", None)
        monkeypatch.setattr(emos, "_sigma_floor_mtime_ns", None)
        return path

    return write


def _current_request(metric="high"):
    return replace(
        _request(**_REQUEST_KWARGS),
        temperature_metric=metric,
        baseline_data_version=_current_baseline_data_version(metric),
        bins=(
            _TemperatureBin("cool", upper_c=20.0, rounding_rule="oracle_truncate"),
            _TemperatureBin("warm", lower_c=21.0, upper_c=30.0, rounding_rule="oracle_truncate"),
            _TemperatureBin("hot", lower_c=31.0, rounding_rule="oracle_truncate"),
        ),
    )


def _materialize(conn, request):
    result = materialize_replacement_forecast_live(conn, request)
    assert result.ok, result.reason_codes
    row = _full_row(conn)
    assert row["q_lcb"] is not None and row["q_ucb"] is not None
    assert set(row["q_lcb"]) == set(row["q_ucb"]) == set(row["q"])
    assert sum(row["q"].values()) == pytest.approx(1.0)
    assert all(0.0 <= row["q_lcb"][key] <= value <= row["q_ucb"][key] <= 1.0
               for key, value in row["q"].items())
    return row


def _assert_neutral_floor(row, *, carrier=False):
    provenance = row["provenance"]
    assert provenance["settlement_sigma_floor_applied"] is False
    assert provenance["settlement_sigma_floor_c"] is None
    assert provenance["settlement_sigma_floor_catchall_capped"] == []
    assert provenance["settlement_sigma_floor_unavailable_reason"] == (
        "SETTLEMENT_SIGMA_FLOOR_NOT_APPLICABLE:day0_shared_carrier" if carrier
        else "CURRENT_EVIDENCE_SHAPE_NO_HISTORICAL_FLOOR"
    )


@pytest.mark.parametrize("metric", ("high", "low"))
@pytest.mark.parametrize("floor_c", (1.0, 5.0), ids=("slack", "would_bind"))
def test_current_shape_fitted_floor_cannot_change_q_or_bounds(monkeypatch, floor_artifact, metric, floor_c):
    """Both the old slack-floor identity and formerly binding floor are inert."""
    conn = _conn()
    request = _install_current_evidence_fusion(monkeypatch, conn, _current_request(metric))
    assert materializer_mod._replacement_settlement_sigma_floor_lookup(request, metric=metric) == (
        None, f"SETTLEMENT_SIGMA_FLOOR_ABSENT:Hong Kong|SON|{metric}",
    )
    bounds_calls = []
    original_bounds = materializer_mod._build_fused_q_bounds

    def observe_bounds(**kwargs):
        bounds_calls.append((kwargs["mu_star"], kwargs["predictive_sigma_c"]))
        return original_bounds(**kwargs)

    monkeypatch.setattr(materializer_mod, "_build_fused_q_bounds", observe_bounds)
    bare = _materialize(conn, request)
    assert bounds_calls and all(center == 25.0 and sigma == 2.0 for center, sigma in bounds_calls)
    bounds_calls.clear()

    floor_artifact(floor_c, metric)
    assert materializer_mod._replacement_settlement_sigma_floor_lookup(request, metric=metric) == (
        floor_c, None,
    )
    # Hold the actual canonical DB/body/ground/anchor/decision fixed; only the
    # fitted artifact changes. Do not mint a second source fixture for comparison.
    with_floor = _materialize(conn, request)
    assert bounds_calls and all(center == 25.0 and sigma == 2.0 for center, sigma in bounds_calls)
    assert with_floor == bare
    _assert_neutral_floor(with_floor)
    assert with_floor["provenance"]["replacement_sigma_basis"] == (
        "decision_time_current_ensemble_within_plus_provider_between"
    )


@pytest.mark.parametrize("metric", ("high", "low"))
def test_current_shape_open_ended_mass_uses_current_sigma(monkeypatch, floor_artifact, metric):
    """Open shoulders retain independently integrated mass without a fitted cap."""
    conn = _conn()
    request = _install_current_evidence_fusion(monkeypatch, conn, _current_request(metric))
    floor_artifact(5.0, metric)
    assert materializer_mod._replacement_settlement_sigma_floor_lookup(request, metric=metric) == (5.0, None)
    row = _materialize(conn, request)
    # HKO truncation gives preimages (-inf,21), [21,31), [31,inf).
    # This independent CDF oracle uses physical sigma=2, never fitted sigma=5.
    cdf = lambda value: 0.5 * (1.0 + math.erf((value - 25.0) / (2.0 * math.sqrt(2.0))))
    assert row["q"] == pytest.approx({
        "cool": cdf(21.0), "warm": cdf(31.0) - cdf(21.0), "hot": 1.0 - cdf(31.0),
    }, abs=1e-12)
    _assert_neutral_floor(row)


@pytest.mark.parametrize("artifact_state", ("absent", "fitted", "malformed"))
def test_current_shape_floor_lookup_is_unreachable_and_provenance_neutral(monkeypatch, floor_artifact, artifact_state):
    """Current evidence never claims applied or absent historical-floor authority."""
    conn = _conn()
    request = _install_current_evidence_fusion(monkeypatch, conn, _current_request())
    if artifact_state != "absent":
        path = floor_artifact(5.0, "high")
        if artifact_state == "malformed":
            path.write_text("not-json")
    resolved, _ = materializer_mod._replacement_settlement_sigma_floor_lookup(request, metric="high")
    assert resolved == (5.0 if artifact_state == "fitted" else None)

    def forbidden_lookup(*args, **kwargs):
        pytest.fail("current evidence consulted a historical settlement floor")

    monkeypatch.setattr(materializer_mod, "_replacement_settlement_sigma_floor_lookup", forbidden_lookup)
    _assert_neutral_floor(_materialize(conn, request))


def test_current_shape_carrier_branch_floor_provenance_is_neutral(monkeypatch, floor_artifact):
    """Actual Day0 carrier q, bounds and 500 coherent draws stay floor-invariant."""
    import src.data.day0_observation_reader as day0_reader

    conn = _conn()
    conn.execute("""CREATE TABLE observation_prints (
        id INTEGER PRIMARY KEY, city TEXT, station_id TEXT, source_channel TEXT,
        publish_ts_utc TEXT, value_native REAL, unit TEXT,
        fetched_at_utc TEXT, raw_report TEXT
    )""")
    observed = _dt(17, 55).isoformat()
    conn.execute("""INSERT INTO observation_prints VALUES
        (1, 'Hong Kong', 'HKO', 'hko_rhrread_spot', ?, 25.7, 'C', ?, ?)""", (
        observed, observed, json.dumps({"recordTime": observed,
            "data": [{"place": "Hong Kong Observatory", "unit": "C", "value": 25.7}]}),
    ))
    likelihood_identity = {
        "semantics": "hko_provisional_monotonic_survival_beta_jeffreys_v1",
        "lookback_start": "2026-09-24", "lookback_end": "2026-10-01",
        "transition_count": 100, "retraction_count": 1,
        "median_update_seconds": 600.0, "projected_remaining_updates": 36,
    }
    likelihood = {**likelihood_identity, "boundary_survival_probability": 0.91,
                  "identity_hash": materializer_mod._json_hash(likelihood_identity)}
    # Same controlled downstream inputs as the existing HKO provisional test;
    # the current carrier constructor and its probability/sample math stay real.
    monkeypatch.setattr(day0_reader, "hko_provisional_revision_likelihood", lambda *a, **k: likelihood)
    monkeypatch.setattr(materializer_mod, "_day0_noaa_carrier_future_members",
                        lambda *a, **k: ((25.2, 25.5, 28.3), 0.5, _dt(18).isoformat(), (), None))
    request = replace(
        _current_request("low"), computed_at=_dt(18), expires_at=_dt(2) + timedelta(days=1),
        day0_observed_extreme_c=25.7, day0_observed_extreme_source="hko_hourly_accumulator",
        day0_observed_extreme_observation_time=observed, day0_observed_extreme_sample_count=12,
        bins=(
            _TemperatureBin("below24", upper_c=23.0, rounding_rule="oracle_truncate"),
            _TemperatureBin("target24", lower_c=24.0, upper_c=24.0, rounding_rule="oracle_truncate"),
            _TemperatureBin("25plus", lower_c=25.0, rounding_rule="oracle_truncate"),
        ),
    )
    request = _install_current_evidence_fusion(monkeypatch, conn, request)
    bare = _materialize(conn, request)
    provenance = bare["provenance"]
    assert provenance["q_shape"] == "day0_remaining_shared_carrier_v2"
    assert provenance["day0_remaining_carrier_operator"] == (
        "extreme_observed_then_noisy_future_analytic_gaussian_mixture_v2"
    )
    assert provenance["day0_remaining_carrier_content_identity"]
    assert provenance["day0_preliminary_report_survival_likelihood"] == likelihood
    assert provenance["day0_provisional_observation"]["support_truncation"] is False
    assert 0.1 < bare["q"]["target24"] < 0.25
    assert bare["q"]["25plus"] > 0.7
    assert "day0_remaining_carrier_probability_samples" not in provenance
    assert provenance["q_lcb_bootstrap_draws"] == provenance["day0_remaining_carrier_sample_count"] == 500
    samples = provenance["q_bootstrap_samples_by_bin"]
    assert set(samples) == set(bare["q"])
    assert all(len(draws) == 500 for draws in samples.values())
    assert all(sum(samples[key][index] for key in samples) == pytest.approx(1.0) for index in range(500))
    _assert_neutral_floor(bare, carrier=True)

    floor_artifact(5.0, "low")
    assert materializer_mod._replacement_settlement_sigma_floor_lookup(request, metric="low") == (5.0, None)
    with_floor = _materialize(conn, request)
    assert with_floor == bare
    _assert_neutral_floor(with_floor, carrier=True)
