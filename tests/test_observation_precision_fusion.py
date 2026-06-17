# Created: 2026-06-17
# Last audited: 2026-06-17
# Authority basis: operator directive "处理多源数据整合" (multi-source data
#   integration). RED-on-revert tests for src/forecast/observation_precision_fusion.py.
#   σ-term provenance + covariance-shrink anti-double-count behaviour asserted
#   against the documented contract; no network, no DB.
"""Tests for multi-source day0 observation precision fusion.

Each test is a real assertion that goes RED if the module's behaviour regresses:
  - same-station correlated sources fuse to n_eff < 2 (shrink prevents double-count);
    independent-station sources fuse to n_eff closer to 2.
  - a staler source (larger Δt) gets lower τ → less weight in the fused value.
  - the fused value lies within [min, max] source value (convexity).
  - a single source returns itself with its own σ_eff.
  - F-unit vs C-unit cities use the correct plausible-move-rate scale for σ_lag.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from src.forecast.observation_precision_fusion import (
    DEFAULT_SENSOR_SIGMA,
    FusedObservation,
    ObsSourceReading,
    fuse_day0_observations,
    lag_sigma,
    rounding_sigma,
    station_mismatch_sigma,
    shrink_to_diagonal,
    _build_source_covariance,
)


# A fixed decision clock; sources are dated relative to it.
DECISION = datetime(2026, 6, 17, 18, 0, 0, tzinfo=timezone.utc)


def _fresh_iso(minutes_ago: float) -> str:
    return (DECISION - timedelta(minutes=minutes_ago)).isoformat()


# ---------------------------------------------------------------------------
# anti-double-count: same-station n_eff < 2 ; independent stations n_eff ≈ 2
# ---------------------------------------------------------------------------
def test_same_station_two_sources_neff_below_two():
    """MADIS + AviationWeather on the SAME ICAO station must NOT count as 2 draws."""
    sources = [
        ObsSourceReading(
            value=88.0, source_family="madis_hfmetar", station_id="KLGA",
            observation_available_at=_fresh_iso(5.0), is_settlement_faithful=True,
        ),
        ObsSourceReading(
            value=88.0, source_family="aviationweather_metar", station_id="KLGA",
            observation_available_at=_fresh_iso(5.0), is_settlement_faithful=False,
        ),
    ]
    fused = fuse_day0_observations(
        sources, decision_time=DECISION, city_name="NYC", city_unit="F"
    )
    assert isinstance(fused, FusedObservation)
    assert fused.n_eff < 2.0, f"same-station n_eff should be <2, got {fused.n_eff}"
    # not collapsed to a single draw either (a sliver of independent provider noise).
    assert fused.n_eff > 1.0, f"same-station n_eff should be >1, got {fused.n_eff}"


def test_independent_stations_neff_near_two():
    """Two sources on DIFFERENT physical stations carry ~independent information."""
    sources = [
        ObsSourceReading(
            value=88.0, source_family="madis_hfmetar", station_id="KLGA",
            observation_available_at=_fresh_iso(5.0), is_settlement_faithful=True,
        ),
        ObsSourceReading(
            value=89.0, source_family="aviationweather_metar", station_id="KNYC",
            observation_available_at=_fresh_iso(5.0), is_settlement_faithful=True,
        ),
    ]
    fused = fuse_day0_observations(
        sources, decision_time=DECISION, city_name="NYC", city_unit="F"
    )
    # independent → n_eff should be close to 2 (and strictly above the same-station case).
    assert fused.n_eff > 1.9, f"independent n_eff should be ~2, got {fused.n_eff}"
    assert fused.n_eff <= 2.0 + 1e-9


def test_independent_neff_strictly_exceeds_same_station_neff():
    """The defining anti-double-count contrast: independent > correlated information."""
    same = fuse_day0_observations(
        [
            ObsSourceReading(value=88.0, source_family="a", station_id="KLGA",
                             observation_available_at=_fresh_iso(5.0)),
            ObsSourceReading(value=88.0, source_family="b", station_id="KLGA",
                             observation_available_at=_fresh_iso(5.0)),
        ],
        decision_time=DECISION, city_name="NYC", city_unit="F",
    )
    indep = fuse_day0_observations(
        [
            ObsSourceReading(value=88.0, source_family="a", station_id="KLGA",
                             observation_available_at=_fresh_iso(5.0)),
            ObsSourceReading(value=88.0, source_family="b", station_id="KORD",
                             observation_available_at=_fresh_iso(5.0)),
        ],
        decision_time=DECISION, city_name="NYC", city_unit="F",
    )
    assert indep.n_eff > same.n_eff


# ---------------------------------------------------------------------------
# staleness: a staler source gets lower τ → less weight
# ---------------------------------------------------------------------------
def test_staler_source_gets_less_weight():
    """A very stale source (large Δt past budget) must pull the fused value toward
    the FRESH source's value (lower τ → less weight)."""
    fresh_val, stale_val = 90.0, 80.0
    sources = [
        ObsSourceReading(
            value=fresh_val, source_family="fresh", station_id="KLGA",
            observation_available_at=_fresh_iso(5.0),  # well within budget
        ),
        ObsSourceReading(
            value=stale_val, source_family="stale", station_id="KORD",  # diff station: independent
            observation_available_at=_fresh_iso(600.0),  # 10h stale → big σ_lag
        ),
    ]
    fused = fuse_day0_observations(
        sources, decision_time=DECISION, city_name="NYC", city_unit="F"
    )
    midpoint = (fresh_val + stale_val) / 2.0
    # fused value must be on the FRESH side of the naive midpoint.
    assert fused.value > midpoint, (
        f"fused {fused.value} should lean toward fresh {fresh_val}, not midpoint {midpoint}"
    )
    # and the staler source's σ_eff must exceed the fresh source's σ_eff.
    sig = dict(fused.per_source_sigma)
    assert sig["stale"] > sig["fresh"]


def test_lag_sigma_zero_within_budget_grows_past_budget():
    """σ_lag is 0 within the staleness budget and grows linearly past it."""
    within = lag_sigma(
        decision_time=DECISION,
        observation_available_at=_fresh_iso(30.0),
        plausible_move_rate=4.5, budget_minutes=100.0,
    )
    past = lag_sigma(
        decision_time=DECISION,
        observation_available_at=_fresh_iso(220.0),  # 120 min past a 100 min budget = 2h excess
        plausible_move_rate=4.5, budget_minutes=100.0,
    )
    assert within == 0.0
    assert past == pytest.approx(4.5 * 2.0, rel=1e-6)  # rate · 2h excess


# ---------------------------------------------------------------------------
# convexity: fused value within [min, max]
# ---------------------------------------------------------------------------
def test_fused_value_within_source_envelope():
    sources = [
        ObsSourceReading(value=85.0, source_family="a", station_id="S1",
                         observation_available_at=_fresh_iso(5.0)),
        ObsSourceReading(value=91.0, source_family="b", station_id="S2",
                         observation_available_at=_fresh_iso(40.0)),
        ObsSourceReading(value=88.0, source_family="c", station_id="S3",
                         observation_available_at=_fresh_iso(70.0)),
    ]
    fused = fuse_day0_observations(
        sources, decision_time=DECISION, city_name="NYC", city_unit="F"
    )
    assert 85.0 <= fused.value <= 91.0


# ---------------------------------------------------------------------------
# single source returns itself with its own σ_eff
# ---------------------------------------------------------------------------
def test_single_source_returns_itself():
    src = ObsSourceReading(
        value=87.0, source_family="madis_hfmetar", station_id="KLGA",
        observation_available_at=_fresh_iso(5.0), is_settlement_faithful=True,
    )
    fused = fuse_day0_observations(
        [src], decision_time=DECISION, city_name="NYC", city_unit="F"
    )
    assert fused.value == pytest.approx(87.0)
    assert fused.n_eff == pytest.approx(1.0)
    # its σ_eff equals the single-source σ_eff (precision = 1/σ_eff²).
    assert fused.sigma_eff == pytest.approx(1.0 / fused.precision ** 0.5)
    assert "madis_hfmetar" in fused.per_source_sigma


# ---------------------------------------------------------------------------
# unit-dependent plausible-move-rate for σ_lag
# ---------------------------------------------------------------------------
def test_f_vs_c_units_use_correct_move_rate():
    """A stale F-city source uses 4.5°F/h; the same staleness in a C-city uses
    2.5°C/h — so the F-city σ_lag is the larger native-unit margin."""
    obs_at = _fresh_iso(400.0)  # 5h+ stale past budget for both
    f_lag = lag_sigma(
        decision_time=DECISION, observation_available_at=obs_at,
        plausible_move_rate=4.5, budget_minutes=100.0,
    )
    c_lag = lag_sigma(
        decision_time=DECISION, observation_available_at=obs_at,
        plausible_move_rate=2.5, budget_minutes=100.0,
    )
    assert f_lag > c_lag
    assert f_lag / c_lag == pytest.approx(4.5 / 2.5, rel=1e-6)


def test_default_move_rate_selected_by_unit():
    """fuse_day0_observations picks 4.5 (F) / 2.5 (C) when plausible_move_rate is None."""
    stale = ObsSourceReading(
        value=80.0, source_family="stale", station_id="S2",
        observation_available_at=_fresh_iso(700.0),
    )
    fresh = ObsSourceReading(
        value=90.0, source_family="fresh", station_id="S1",
        observation_available_at=_fresh_iso(5.0),
    )
    fused_f = fuse_day0_observations(
        [fresh, stale], decision_time=DECISION, city_name="", city_unit="F",
        budget_minutes=100.0,
    )
    fused_c = fuse_day0_observations(
        [fresh, stale], decision_time=DECISION, city_name="", city_unit="C",
        budget_minutes=100.0,
    )
    # both lean fresh, but the F city (faster move rate) inflates the stale σ_lag
    # MORE → leans even harder toward fresh than the C city.
    assert fused_f.value > fused_c.value


# ---------------------------------------------------------------------------
# σ-term provenance unit checks
# ---------------------------------------------------------------------------
def test_rounding_sigma_is_quantum_over_sqrt12():
    """σ_rounding = quantum/√12; the wmo_half_up preimage span is 1.0 → 1/√12."""
    s = rounding_sigma("wmo_half_up", half_step=0.5)
    assert s == pytest.approx(1.0 / (12.0 ** 0.5), rel=1e-9)
    # HKO truncation preimage span is also a full quantum (2·half_step = 1.0).
    s_hk = rounding_sigma("oracle_truncate", half_step=0.5)
    assert s_hk == pytest.approx(1.0 / (12.0 ** 0.5), rel=1e-9)


def test_station_mismatch_zero_for_faithful_nonzero_for_secondary():
    """Settlement-faithful primary → 0 mismatch; a secondary uses measured divergence."""
    faithful = station_mismatch_sigma("Houston", "F", is_settlement_faithful=True)
    secondary = station_mismatch_sigma("Houston", "F", is_settlement_faithful=False)
    assert faithful == 0.0
    # Houston max_abs_raw_delta = 1.008 in the measured config.
    assert secondary == pytest.approx(1.008, abs=1e-6)


def test_unknown_city_mismatch_uses_unit_default():
    """A city absent from the divergence config falls back to the unit default."""
    s = station_mismatch_sigma("Atlantis", "C", is_settlement_faithful=False)
    assert s == pytest.approx(1.0, abs=1e-9)  # C default


# ---------------------------------------------------------------------------
# covariance shrink mechanics
# ---------------------------------------------------------------------------
def test_shrink_delta_zero_when_diagonal():
    """A purely diagonal covariance has nothing to shrink (δ = 0)."""
    cov = np.diag([1.0, 2.0, 0.5])
    shrunk, delta = shrink_to_diagonal(cov)
    assert delta == pytest.approx(0.0, abs=1e-9)
    np.testing.assert_allclose(np.diag(shrunk), np.diag(cov), rtol=1e-6)


def test_shrink_delta_positive_with_offdiagonal():
    """Same-station off-diagonal mass → δ > 0."""
    sigmas = np.array([1.0, 1.0])
    cov = _build_source_covariance(sigmas, ["KLGA", "KLGA"])
    assert cov[0, 1] > 0.0  # correlated
    _, delta = shrink_to_diagonal(cov)
    assert delta > 0.0


# ---------------------------------------------------------------------------
# learned-covariance upgrade hook
# ---------------------------------------------------------------------------
def test_fitted_cov_replaces_constructed_covariance():
    """A supplied fitted_cov drives the GLS fuse directly (σ-component build bypassed)."""
    sources = [
        ObsSourceReading(value=86.0, source_family="a", station_id="KLGA",
                         observation_available_at=_fresh_iso(5.0)),
        ObsSourceReading(value=90.0, source_family="b", station_id="KLGA",
                         observation_available_at=_fresh_iso(5.0)),
    ]
    # diagonal fitted cov → treated as independent → n_eff ≈ 2 even though same station.
    fitted = np.diag([0.5, 0.5])
    fused = fuse_day0_observations(
        sources, decision_time=DECISION, city_name="NYC", city_unit="F",
        fitted_cov=fitted,
    )
    assert fused.provenance["fitted_cov_used"] is True
    assert fused.provenance["method"].startswith("FITTED_COV_GLS")
    assert fused.n_eff == pytest.approx(2.0, rel=1e-6)
    # equal diagonal variances → fused value is the simple mean.
    assert fused.value == pytest.approx(88.0, rel=1e-6)


def test_fitted_cov_shape_mismatch_raises():
    sources = [
        ObsSourceReading(value=86.0, source_family="a", station_id="KLGA",
                         observation_available_at=_fresh_iso(5.0)),
        ObsSourceReading(value=90.0, source_family="b", station_id="KLGA",
                         observation_available_at=_fresh_iso(5.0)),
    ]
    with pytest.raises(ValueError):
        fuse_day0_observations(
            sources, decision_time=DECISION, city_name="NYC", city_unit="F",
            fitted_cov=np.diag([0.5, 0.5, 0.5]),  # 3×3 for 2 sources
        )


# ---------------------------------------------------------------------------
# guards
# ---------------------------------------------------------------------------
def test_empty_sources_raises():
    with pytest.raises(ValueError):
        fuse_day0_observations([], decision_time=DECISION, city_name="NYC", city_unit="F")


def test_nonfinite_value_rejected():
    with pytest.raises(ValueError):
        ObsSourceReading(
            value=float("nan"), source_family="a", station_id="KLGA",
            observation_available_at=_fresh_iso(5.0),
        )
