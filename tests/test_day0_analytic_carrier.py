# Purpose: Verify analytic settlement-bin probabilities and historical carrier replay.
# Reuse: Run when changing Day0 mixture integration, units, rounding or carrier identity.
# Created: 2026-09-20
# Last reused/audited: 2026-09-20
# Authority basis: forecast_time_performance PLAN.md frozen Contract B.
"""Independent tests for the exact Day0 remaining-carrier operator."""

from __future__ import annotations

from math import erfc, erf, hypot, sqrt

import numpy as np
import pytest

from src.contracts.settlement_semantics import SettlementSemantics
from src.data.day0_hourly_vectors import (
    DAY0_REMAINING_CARRIER_OPERATOR,
    DAY0_REMAINING_CARRIER_OPERATOR_V1,
    DAY0_REMAINING_CARRIER_OPERATOR_V2,
    build_day0_remaining_probability_carrier,
)


def _sem(rule: str, unit: str = "C") -> SettlementSemantics:
    return SettlementSemantics(
        resolution_source="TEST",
        measurement_unit=unit,
        precision=1.0,
        rounding_rule=rule,
        finalization_time="12:00:00Z",
    )


def _call(*, metric="high", sem=None, n_point=31, n_samples=500, **extra):
    return build_day0_remaining_probability_carrier(
        future_extremes_c=extra.pop("future", [-2.3, -0.1, 2.4]),
        boundary_scenarios=extra.pop("scenarios", ((-1.5, 0.7), (None, 0.3))),
        metric=metric,
        path_error_sigma_c=extra.pop("path_sigma", 0.65),
        instrument_sigma_c=extra.pop("instrument_sigma", 0.35),
        bin_bounds_c=extra.pop("bounds", [(None, -1), (0, 0), (1, 2), (3, None)]),
        n_point=n_point,
        n_samples=n_samples,
        identity_inputs=extra.pop("identity", {"city": "Test", "unit": "C", "prior": "p1"}),
        settlement_semantics=sem or _sem("wmo_half_up"),
        **extra,
    )


def _normal_interval(mu: float, sigma: float, low: float, high: float) -> float:
    if low >= high:
        return 0.0
    def cdf(value: float) -> float:
        return 0.5 * (1.0 + erf((value - mu) / (sigma * sqrt(2.0))))

    return cdf(high) - cdf(low)


def test_default_is_v2_and_unknown_operator_rejects():
    assert DAY0_REMAINING_CARRIER_OPERATOR == DAY0_REMAINING_CARRIER_OPERATOR_V2
    assert _call()["operator"] == DAY0_REMAINING_CARRIER_OPERATOR_V2
    with pytest.raises(ValueError, match="unsupported Day0 remaining carrier operator"):
        _call(operator="future_operator")


def test_v1_regression_fixture_is_byte_stable():
    result = _call(
        metric="high",
        sem=SettlementSemantics.default_wu_celsius("FIX"),
        future=[-1.25, 0.4],
        scenarios=((-0.5, 0.6), (None, 0.4)),
        path_sigma=0.3,
        instrument_sigma=0.2,
        bounds=[(None, -1), (0, 0), (1, None)],
        n_point=7,
        n_samples=5,
        identity={"city": "Fixture", "unit": "C", "prior": "fixture-v1"},
        operator=DAY0_REMAINING_CARRIER_OPERATOR_V1,
    )
    assert result == {
        "q": [1.0 / 7.0, 11.0 / 14.0, 1.0 / 14.0],
        "samples": [
            [0.5, 0.0, 0.5],
            [0.0, 1.0, 0.0],
            [0.0, 0.5, 0.5],
            [0.0, 1.0, 0.0],
            [0.5, 0.5, 0.0],
        ],
        "content_identity": "aebe4e8e6a2d37ed058aead582c765666fbea4c9d0e1a38b987988a1cac29f58",
        "operator": DAY0_REMAINING_CARRIER_OPERATOR_V1,
        "sample_count": 5,
    }


def test_v2_changes_only_point_estimate_and_preserves_confidence_rows():
    kwargs = dict(
        metric="high",
        sem=SettlementSemantics.default_wu_celsius("FIX"),
        future=[-1.25, 0.4],
        scenarios=((-0.5, 0.6), (None, 0.4)),
        path_sigma=0.3,
        instrument_sigma=0.2,
        bounds=[(None, -1), (0, 0), (1, None)],
        n_point=7,
        n_samples=5,
        identity={"city": "Fixture", "unit": "C", "prior": "fixture-v1"},
    )
    v1 = _call(**kwargs, operator=DAY0_REMAINING_CARRIER_OPERATOR_V1)
    v2 = _call(**kwargs)
    assert v2["samples"] == v1["samples"]
    assert v2["sample_count"] == 5
    assert v2["q"] == pytest.approx([0.19750409169814265, 0.6071177814926272, 0.1953781268092301])
    assert v2["content_identity"] != v1["content_identity"]
    assert v2["operator"] == DAY0_REMAINING_CARRIER_OPERATOR_V2


@pytest.mark.parametrize("metric", ["high", "low"])
@pytest.mark.parametrize("rule", ["wmo_half_up", "floor", "ceil"])
def test_exact_operator_matches_independent_member_scenario_reference(metric, rule):
    sem = _sem(rule)
    future = [-2.3, -0.1, 2.4]
    scenarios = ((-1.5, 0.7), (None, 0.3))
    bounds = [(None, -1), (0, 0), (1, 2), (3, None)]
    sigma = hypot(0.65, 0.35)
    lo_offset, hi_offset = {
        "wmo_half_up": (-0.5, 0.5),
        "floor": (0.0, 1.0),
        "ceil": (-1.0, 0.0),
    }[rule]

    expected = np.zeros(len(bounds))
    for mu in future:
        member = np.zeros(len(bounds))
        for boundary, weight in scenarios:
            component = np.zeros(len(bounds))
            for index, (low, high) in enumerate(bounds):
                lo = -np.inf if low is None else low + lo_offset
                hi = np.inf if high is None else high + hi_offset
                if boundary is None:
                    component[index] = _normal_interval(mu, sigma, lo, hi)
                    continue
                rounded = float(sem.round_values([boundary])[0])
                in_bin = (low is None or rounded >= low) and (high is None or rounded <= high)
                if metric == "high":
                    component[index] = _normal_interval(mu, sigma, max(lo, boundary), hi)
                    if in_bin:
                        component[index] += _normal_interval(mu, sigma, -np.inf, boundary)
                else:
                    component[index] = _normal_interval(mu, sigma, lo, min(hi, boundary))
                    if in_bin:
                        component[index] += _normal_interval(mu, sigma, boundary, np.inf)
            member += weight * component
        expected += member
    expected /= len(future)
    expected /= expected.sum()
    actual = _call(
        metric=metric,
        sem=sem,
        future=future,
        scenarios=scenarios,
        bounds=bounds,
        path_sigma=0.65,
        instrument_sigma=0.35,
    )
    assert actual["q"] == pytest.approx(expected, abs=2e-12)
    assert sum(actual["q"]) == pytest.approx(1.0)


@pytest.mark.parametrize("metric", ["high", "low"])
def test_sigma_zero_uses_discrete_rounding_and_mixture_weights(metric):
    actual = _call(
        metric=metric,
        sem=_sem("wmo_half_up"),
        future=[-1.5, 0.5],
        scenarios=((-1.5, 0.25), (None, 0.75)),
        path_sigma=0.0,
        instrument_sigma=0.0,
        bounds=[(None, -1), (0, 0), (1, None)],
        n_point=1,
        n_samples=5,
    )
    expected = [0.5, 0.0, 0.5] if metric == "high" else [0.625, 0.0, 0.375]
    assert actual["q"] == pytest.approx(expected)


def test_fahrenheit_native_bins_and_negative_boundary_ties_are_supported():
    actual = _call(
        metric="high",
        sem=_sem("wmo_half_up", "F"),
        future=[-1.5, 0.5],
        scenarios=((-1.5, 1.0),),
        bounds=[(None, -2), (-1, -1), (0, 0), (1, None)],
        path_sigma=0.0,
        instrument_sigma=0.0,
        identity={"city": "F", "unit": "F", "prior": "p"},
        n_point=2,
        n_samples=5,
    )
    # WMO floor(x + .5): -1.5 -> -1 and max(-1.5, -1.5) remains -1.
    assert actual["q"] == pytest.approx([0.0, 0.5, 0.0, 0.5])


@pytest.mark.parametrize("metric", ["high", "low"])
@pytest.mark.parametrize("boundary", [None, 68.5])
def test_fahrenheit_nonzero_sigma_native_and_celsius_oracles_agree(metric, boundary):
    """Native F integration equals an independently converted C calculation."""
    sem = _sem("wmo_half_up", "F")
    bounds = [(None, 67), (68, 68), (69, None)]
    sigma_f = 1.8
    mu_f = 68.0
    actual = _call(
        metric=metric,
        sem=sem,
        future=[mu_f],
        scenarios=((boundary, 1.0),),
        path_sigma=0.0,
        instrument_sigma=sigma_f,
        bounds=bounds,
        identity={"city": "F", "unit": "F", "prior": "native-oracle"},
    )

    def interval(mu, sigma, low, high):
        return _normal_interval(mu, sigma, low, high)

    native = np.zeros(3)
    converted = np.zeros(3)
    scale = 5.0 / 9.0
    offset = -32.0 * scale
    for index, (low, high) in enumerate(bounds):
        low_f = -np.inf if low is None else low - 0.5
        high_f = np.inf if high is None else high + 0.5
        low_c = -np.inf if low is None else (low - 32.0) * scale - 0.5 * scale
        high_c = np.inf if high is None else (high - 32.0) * scale + 0.5 * scale
        if boundary is None:
            native[index] = interval(mu_f, sigma_f, low_f, high_f)
            converted[index] = interval(mu_f * scale + offset, sigma_f * scale, low_c, high_c)
            continue
        rounded_boundary = float(sem.round_values([boundary])[0])
        in_bin = (low is None or rounded_boundary >= low) and (high is None or rounded_boundary <= high)
        boundary_c = boundary * scale + offset
        if metric == "high":
            native[index] = interval(mu_f, sigma_f, max(low_f, boundary), high_f)
            converted[index] = interval(mu_f * scale + offset, sigma_f * scale, max(low_c, boundary_c), high_c)
            if in_bin:
                native[index] += interval(mu_f, sigma_f, -np.inf, boundary)
                converted[index] += interval(mu_f * scale + offset, sigma_f * scale, -np.inf, boundary_c)
        else:
            native[index] = interval(mu_f, sigma_f, low_f, min(high_f, boundary))
            converted[index] = interval(mu_f * scale + offset, sigma_f * scale, low_c, min(high_c, boundary_c))
            if in_bin:
                native[index] += interval(mu_f, sigma_f, boundary, np.inf)
                converted[index] += interval(mu_f * scale + offset, sigma_f * scale, boundary_c, np.inf)
    native /= native.sum()
    converted /= converted.sum()
    assert native == pytest.approx(converted, abs=2e-14)
    assert actual["q"] == pytest.approx(native, abs=2e-14)


def test_v2_point_is_independent_of_n_point_but_clock_only_identity_is_stable():
    common = dict(
        future=[1.0, 2.0, 3.0],
        scenarios=((2.5, 0.8), (None, 0.2)),
        identity={"city": "Clock", "unit": "C", "prior": "same"},
    )
    first = _call(**common, n_point=7)
    second = _call(**common, n_point=700)
    assert second["q"] == pytest.approx(first["q"], abs=0.0)
    assert second["content_identity"] != first["content_identity"]
    later = _call(
        future=common["future"],
        scenarios=common["scenarios"],
        n_point=7,
        identity={"city": "Clock", "unit": "C", "prior": "same", "decision_time_utc": "later"},
    )
    assert later["content_identity"] == first["content_identity"]
    assert later["q"] == first["q"]


def test_far_tail_is_finite_and_nonnegative():
    actual = _call(
        future=[-100.0, -99.0],
        scenarios=((None, 1.0),),
        bounds=[(None, -101), (-100, -100), (-99, -99), (-98, None)],
        path_sigma=0.01,
        instrument_sigma=0.01,
    )
    assert np.isfinite(actual["q"]).all()
    assert (np.asarray(actual["q"]) >= 0).all()
    assert sum(actual["q"]) == pytest.approx(1.0)


@pytest.mark.parametrize(
    ("future", "bounds"),
    [
        ([-0.5], [(None, 7), (8, 23), (24, None)]),
        ([0.5], [(None, -24), (-23, -8), (-7, None)]),
    ],
)
def test_extreme_wmo_tail_mass_matches_erfc_reference(future, bounds):
    """Interior [8σ,24σ] and mirrored lower-tail mass remains measurable."""
    actual = _call(
        future=future,
        scenarios=((None, 1.0),),
        bounds=bounds,
        path_sigma=0.0,
        instrument_sigma=1.0,
    )
    expected_tail = 0.5 * (erfc(8.0 / sqrt(2.0)) - erfc(24.0 / sqrt(2.0)))
    assert expected_tail > 0.0
    assert actual["q"][1] > 0.0
    assert actual["q"][1] == pytest.approx(expected_tail, rel=2e-12, abs=0.0)


@pytest.mark.parametrize("metric", ["high", "low"])
@pytest.mark.parametrize("rule", ["wmo_half_up", "floor", "ceil", "oracle_truncate"])
def test_sigma_zero_rounding_matrix_has_exact_shoulders_and_none_scenario(metric, rule):
    """Discrete sigma=0 outcomes follow an independent rounding hand calculation."""
    future = [0.49, 1.51]
    boundary = 0.5
    scenarios = ((boundary, 0.4), (None, 0.6))
    bounds = [(None, 0), (1, 1), (2, None)]
    actual = _call(
        metric=metric,
        sem=_sem(rule),
        future=future,
        scenarios=scenarios,
        bounds=bounds,
        path_sigma=0.0,
        instrument_sigma=0.0,
    )

    def hand_round(value):
        if rule == "wmo_half_up":
            return np.floor(value + 0.5)
        if rule in {"floor", "oracle_truncate"}:
            return np.floor(value)
        return np.ceil(value)

    expected = np.zeros(3)
    for member in future:
        for scenario_boundary, weight in scenarios:
            final = member if scenario_boundary is None else (
                max(member, scenario_boundary)
                if metric == "high"
                else min(member, scenario_boundary)
            )
            settled = hand_round(final)
            for index, (low, high) in enumerate(bounds):
                if (low is None or settled >= low) and (high is None or settled <= high):
                    expected[index] += weight / len(future)
                    break
    assert actual["q"] == pytest.approx(expected, abs=0.0)


@pytest.mark.parametrize("metric", ["high", "low"])
def test_fahrenheit_negative_half_boundary_nonzero_sigma_matches_native_oracle(metric):
    """Negative Fahrenheit half-step atoms use native WMO tie classification."""
    sem = _sem("wmo_half_up", "F")
    mu = -4.0
    boundary = -3.5
    sigma = 1.8
    bounds = [(None, -5), (-4, -4), (-3, None)]
    actual = _call(
        metric=metric,
        sem=sem,
        future=[mu],
        scenarios=((boundary, 1.0),),
        path_sigma=0.0,
        instrument_sigma=sigma,
        bounds=bounds,
        identity={"city": "F-negative", "unit": "F", "prior": "native-negative"},
    )
    expected = np.zeros(3)
    for index, (low, high) in enumerate(bounds):
        lower = -np.inf if low is None else low - 0.5
        upper = np.inf if high is None else high + 0.5
        rounded_boundary = np.floor(boundary + 0.5)
        in_bin = (low is None or rounded_boundary >= low) and (high is None or rounded_boundary <= high)
        if metric == "high":
            expected[index] = _normal_interval(mu, sigma, max(lower, boundary), upper)
            if in_bin:
                expected[index] += _normal_interval(mu, sigma, -np.inf, boundary)
        else:
            expected[index] = _normal_interval(mu, sigma, lower, min(upper, boundary))
            if in_bin:
                expected[index] += _normal_interval(mu, sigma, boundary, np.inf)
    expected /= expected.sum()
    assert actual["q"] == pytest.approx(expected, abs=2e-14)


@pytest.mark.parametrize("metric", ["high", "low"])
def test_exact_point_tracks_large_independent_bruteforce_mc(metric):
    future = np.asarray([-2.3, -0.1, 2.4])
    boundaries = np.asarray([-1.5, 0.0])
    weights = np.asarray([0.7, 0.3])
    bounds = [(None, -1), (0, 0), (1, 2), (3, None)]
    sigma = hypot(0.65, 0.35)
    actual = _call(
        metric=metric,
        sem=_sem("wmo_half_up"),
        future=future.tolist(),
        scenarios=tuple(zip(boundaries.tolist(), weights.tolist())),
        bounds=bounds,
        path_sigma=0.65,
        instrument_sigma=0.35,
    )

    rng = np.random.default_rng(20260920)
    rows = 300_000
    estimate = np.zeros(len(bounds))
    for member in future:
        draws = member + rng.normal(0.0, sigma, rows)
        choices = rng.choice(len(boundaries), rows, p=weights)
        selected = boundaries[choices]
        final = np.maximum(draws, selected) if metric == "high" else np.minimum(draws, selected)
        settled = _sem("wmo_half_up").round_values(final)
        estimate += np.asarray([
            np.mean((settled >= low if low is not None else True)
                   & (settled <= high if high is not None else True))
            for low, high in bounds
        ])
    estimate /= len(future)
    assert actual["q"] == pytest.approx(estimate, abs=0.0025)
