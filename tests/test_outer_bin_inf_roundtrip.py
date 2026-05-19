# Created: 2026-05-19
# Last reused or audited: 2026-05-19
# Authority basis: PHASE_0_V4_ADDENDUM.md §R-4.4 §14.10 ±inf audit,
#   topology packet "phase0-pr4-decision-group-id"
"""R-4.4: ±inf roundtrip tests for outer bin logit handling (§14.10 audit).

LIVE TESTS (not xfail): These verify current P_CLAMP_LOW/HIGH behaviour at
the outer bin boundaries where p_raw may approach 0 or 1.

§14.10 finding: The clamping at P_CLAMP_LOW=0.01 / P_CLAMP_HIGH=0.99 prevents
±inf logit values from occurring in normal operation. This audit confirms:
  1. logit_safe never returns ±inf for any p_raw in [0, 1].
  2. logit_safe handles p_raw == 0.0 (clamps to P_CLAMP_LOW).
  3. logit_safe handles p_raw == 1.0 (clamps to P_CLAMP_HIGH).
  4. logit_safe handles p_raw exactly at P_CLAMP_LOW and P_CLAMP_HIGH.
  5. calibrate_and_normalize handles a vector with outer-bin p_raw near 0.
  6. calibrate_and_normalize output sums to 1.0 even with extreme inputs.
  7. normalize_bin_probability_for_calibration does not produce ±inf for
     valid finite bin widths.

Test plan (all LIVE, §14.10 is "largely closed" per critic):
    T1: logit_safe(0.0) is finite (clamped).
    T2: logit_safe(1.0) is finite (clamped).
    T3: logit_safe(p) is finite for p in linspace(0, 1, 1000).
    T4: logit_safe(P_CLAMP_LOW) == logit_safe(0.005) (boundary clamp).
    T5: logit_safe(P_CLAMP_HIGH) == logit_safe(0.995) (boundary clamp).
    T6: calibrate_and_normalize sums to 1.0 for a fitted calibrator with
        outer-bin inputs near 0 and 1.
    T7: normalize_bin_probability_for_calibration is finite for bin_width > 0.
"""

import numpy as np
import pytest

from src.calibration.platt import (
    P_CLAMP_HIGH,
    P_CLAMP_LOW,
    ExtendedPlattCalibrator,
    calibrate_and_normalize,
    logit_safe,
    normalize_bin_probability_for_calibration,
)


def test_logit_safe_zero_is_finite():
    """logit_safe(0.0) must be finite (clamped to P_CLAMP_LOW)."""
    result = logit_safe(0.0)
    assert np.isfinite(result), f"logit_safe(0.0) = {result} is not finite"


def test_logit_safe_one_is_finite():
    """logit_safe(1.0) must be finite (clamped to P_CLAMP_HIGH)."""
    result = logit_safe(1.0)
    assert np.isfinite(result), f"logit_safe(1.0) = {result} is not finite"


def test_logit_safe_all_finite_in_unit_interval():
    """logit_safe(p) must be finite for all p in [0, 1]."""
    ps = np.linspace(0.0, 1.0, 1000)
    results = logit_safe(ps)
    assert np.all(np.isfinite(results)), (
        f"logit_safe produced non-finite values at indices: "
        f"{np.where(~np.isfinite(results))[0]}"
    )


def test_logit_safe_clamps_at_p_clamp_low_boundary():
    """logit_safe at boundary value equals logit_safe just below boundary."""
    below_boundary = P_CLAMP_LOW / 2.0
    assert logit_safe(below_boundary) == pytest.approx(logit_safe(P_CLAMP_LOW))


def test_logit_safe_clamps_at_p_clamp_high_boundary():
    """logit_safe at boundary value equals logit_safe just above boundary."""
    above_boundary = P_CLAMP_HIGH + (1.0 - P_CLAMP_HIGH) / 2.0
    assert logit_safe(above_boundary) == pytest.approx(logit_safe(P_CLAMP_HIGH))


def test_calibrate_and_normalize_sums_to_one_with_extreme_inputs():
    """calibrate_and_normalize output sums to 1.0 even with outer-bin extremes."""
    rng = np.random.default_rng(42)
    # Build a minimal fitted calibrator
    n = 60
    p_raw_train = rng.uniform(0.05, 0.95, n)
    lead_days_train = rng.uniform(1, 14, n)
    outcomes_train = rng.integers(0, 2, n).astype(float)

    calibrator = ExtendedPlattCalibrator()
    calibrator.fit(p_raw_train, lead_days_train, outcomes_train, n_bootstrap=5)

    # Input vector with outer-bin values near 0 and 1
    p_raw_vector = np.array([0.001, 0.005, 0.5, 0.985, 0.999])
    result = calibrate_and_normalize(p_raw_vector, calibrator, lead_days=7.0)

    assert np.isfinite(result).all(), f"calibrate_and_normalize returned non-finite: {result}"
    assert result.sum() == pytest.approx(1.0, abs=1e-9), (
        f"calibrate_and_normalize does not sum to 1.0: {result.sum()}"
    )


def test_normalize_bin_probability_finite_for_valid_width():
    """normalize_bin_probability_for_calibration is finite for valid bin_width."""
    result = normalize_bin_probability_for_calibration(0.5, bin_width=2.0)
    assert np.isfinite(result), f"normalize_bin_probability returned non-finite: {result}"
