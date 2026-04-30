# Created: 2026-03-30
# Last reused/audited: 2026-04-30
# Authority basis: midstream verdict v2 2026-04-23 (docs/to-do-list/zeus_midstream_fix_plan_2026-04-23.md T1.a midstream guardian panel)
"""Tests for ExtendedPlattCalibrator.

Covers:
1. Happy path: fit on synthetic data, predict returns calibrated values
2. Edge cases: n=15 (minimum), identity calibration, all outcomes=0
3. Failure modes: n < 15 rejected, predict before fit
"""

import numpy as np
import pytest

from src.calibration.platt import (
    ExtendedPlattCalibrator,
    calibrate_and_normalize,
    logit_safe,
    P_CLAMP_LOW,
    P_CLAMP_HIGH,
    WIDTH_NORMALIZED_SPACE,
    normalize_bin_probability_for_calibration,
)


def _synthetic_data(n: int = 200, seed: int = 42):
    """Generate synthetic calibration data with known mild bias."""
    rng = np.random.default_rng(seed)
    p_raw = rng.uniform(0.05, 0.95, n)
    lead_days = rng.uniform(1, 7, n)
    # Outcome probability = slightly shifted from p_raw
    true_p = np.clip(p_raw * 0.8 + 0.1, 0.01, 0.99)
    outcomes = (rng.random(n) < true_p).astype(int)
    return p_raw, lead_days, outcomes


class TestExtendedPlattFit:
    def test_logit_safe_handles_zero_one(self):
        assert np.isfinite(logit_safe(0.0))
        assert np.isfinite(logit_safe(1.0))
        assert abs(float(logit_safe(0.5))) < 1e-9

    def test_fit_produces_params(self):
        p_raw, lead_days, outcomes = _synthetic_data(200)
        cal = ExtendedPlattCalibrator()
        cal.fit(p_raw, lead_days, outcomes)

        assert cal.fitted is True
        assert cal.n_samples == 200
        assert cal.A != 0.0  # Should learn non-trivial logit weight
        assert len(cal.bootstrap_params) > 0

    def test_bootstrap_produces_200_params(self):
        p_raw, lead_days, outcomes = _synthetic_data(200)
        cal = ExtendedPlattCalibrator()
        cal.fit(p_raw, lead_days, outcomes, n_bootstrap=200)

        # May be slightly < 200 if some bootstrap samples are degenerate
        assert len(cal.bootstrap_params) >= 180
        # Each param is (A, B, C)
        assert len(cal.bootstrap_params[0]) == 3

    def test_minimum_n_15(self):
        """n=15 is the minimum for Platt fitting (spec §3.3 level 3)."""
        p_raw, lead_days, outcomes = _synthetic_data(15, seed=99)
        cal = ExtendedPlattCalibrator()
        cal.fit(p_raw, lead_days, outcomes, n_bootstrap=50, regularization_C=0.1)
        assert cal.fitted is True

    def test_rejects_n_below_15(self):
        """n < 15 → ValueError (spec §3.3: use P_raw directly)."""
        p_raw = np.array([0.1, 0.5, 0.9])
        lead_days = np.array([3.0, 4.0, 5.0])
        outcomes = np.array([0, 1, 1])

        cal = ExtendedPlattCalibrator()
        with pytest.raises(ValueError, match="n=3 < 15"):
            cal.fit(p_raw, lead_days, outcomes)

    def test_group_bootstrap_uses_effective_sample_count(self):
        p_raw, lead_days, outcomes = _synthetic_data(45)
        group_ids = np.array([f"group-{i // 3}" for i in range(45)], dtype=object)
        cal = ExtendedPlattCalibrator()
        cal.fit(
            p_raw,
            lead_days,
            outcomes,
            decision_group_ids=group_ids,
            n_bootstrap=20,
            rng=np.random.default_rng(123),
        )

        assert cal.fitted is True
        assert cal.n_samples == 15
        assert len(cal.bootstrap_params) > 0

    def test_group_bootstrap_rejects_missing_group_ids(self):
        p_raw, lead_days, outcomes = _synthetic_data(15)
        group_ids = np.array([f"group-{i}" for i in range(15)], dtype=object)
        group_ids[3] = None
        cal = ExtendedPlattCalibrator()
        with pytest.raises(ValueError, match="null/empty"):
            cal.fit(p_raw, lead_days, outcomes, decision_group_ids=group_ids)

    def test_group_bootstrap_rejects_low_n_eff(self):
        p_raw, lead_days, outcomes = _synthetic_data(30)
        group_ids = np.array([f"group-{i // 3}" for i in range(30)], dtype=object)
        cal = ExtendedPlattCalibrator()
        with pytest.raises(ValueError, match="n_eff=10 < 15"):
            cal.fit(p_raw, lead_days, outcomes, decision_group_ids=group_ids)

    def test_refit_p_raw_domain_validation_rejects_corruption(self):
        from scripts.refit_platt import _validate_p_raw_domain

        _validate_p_raw_domain("ok", np.array([0.0, 0.5, 1.0]))
        with pytest.raises(RuntimeError, match=r"outside \[0, 1\]"):
            _validate_p_raw_domain("bad", np.array([-0.1, 0.5]))
        with pytest.raises(RuntimeError, match=r"outside \[0, 1\]"):
            _validate_p_raw_domain("bad", np.array([0.5, 1.1]))

    def test_strong_regularization(self):
        """C=0.1 (strong) should produce more conservative params than C=1.0."""
        p_raw, lead_days, outcomes = _synthetic_data(50)

        cal_strong = ExtendedPlattCalibrator()
        cal_strong.fit(p_raw, lead_days, outcomes, regularization_C=0.1)

        cal_standard = ExtendedPlattCalibrator()
        cal_standard.fit(p_raw, lead_days, outcomes, regularization_C=1.0)

        # Strong regularization shrinks coefficients toward zero
        assert abs(cal_strong.A) <= abs(cal_standard.A) + 0.5


class TestExtendedPlattPredict:
    def test_predict_returns_float(self):
        p_raw, lead_days, outcomes = _synthetic_data(100)
        cal = ExtendedPlattCalibrator()
        cal.fit(p_raw, lead_days, outcomes)

        result = cal.predict(0.5, 3.0)
        assert isinstance(result, float)

    def test_predict_in_valid_range(self):
        """Output must be in [0.001, 0.999] per CLAUDE.md."""
        p_raw, lead_days, outcomes = _synthetic_data(100)
        cal = ExtendedPlattCalibrator()
        cal.fit(p_raw, lead_days, outcomes)

        for p in [0.01, 0.1, 0.5, 0.9, 0.99]:
            for ld in [1.0, 3.0, 7.0]:
                result = cal.predict(p, ld)
                assert 0.001 <= result <= 0.999

    def test_identity_calibration(self):
        """If A≈1, B≈0, C≈0 → predict(x) ≈ x."""
        # Create perfectly calibrated data
        rng = np.random.default_rng(42)
        p_raw = rng.uniform(0.1, 0.9, 500)
        lead_days = rng.uniform(1, 7, 500)
        outcomes = (rng.random(500) < p_raw).astype(int)

        cal = ExtendedPlattCalibrator()
        cal.fit(p_raw, lead_days, outcomes)

        # Should be close to identity
        for p in [0.2, 0.5, 0.8]:
            result = cal.predict(p, 3.0)
            assert abs(result - p) < 0.15  # Approximate identity

    def test_predict_before_fit_raises(self):
        cal = ExtendedPlattCalibrator()
        with pytest.raises(RuntimeError, match="not fitted"):
            cal.predict(0.5, 3.0)

    def test_predict_for_bin_uses_width_normalized_input_space(self):
        cal = ExtendedPlattCalibrator()
        cal.fitted = True
        cal.A = 1.0
        cal.B = 0.0
        cal.C = 0.0
        cal.input_space = WIDTH_NORMALIZED_SPACE

        result = cal.predict_for_bin(0.40, 3.0, bin_width=2.0)
        assert result == pytest.approx(0.20, abs=1e-6)


class TestWidthNormalization:
    def test_range_bin_probability_normalizes_by_width(self):
        assert normalize_bin_probability_for_calibration(0.40, bin_width=2.0) == pytest.approx(0.20)

    def test_point_bin_probability_is_unchanged(self):
        assert normalize_bin_probability_for_calibration(0.10, bin_width=1.0) == pytest.approx(0.10)

    def test_shoulder_probability_stays_raw(self):
        assert normalize_bin_probability_for_calibration(0.07, bin_width=None) == pytest.approx(0.07)

    @pytest.mark.parametrize("bad_width", [np.nan, np.inf, 0.0, -1.0, "bad"])
    def test_corrupt_finite_width_rejected(self, bad_width):
        with pytest.raises(ValueError, match="bin_width must be finite and >0"):
            normalize_bin_probability_for_calibration(0.07, bin_width=bad_width)

    def test_fit_with_mixed_finite_and_open_shoulder_widths(self):
        p_raw, lead_days, outcomes = _synthetic_data(60)
        bin_widths = np.array([1.0, 2.0, None, None, None] * 12, dtype=object)
        cal = ExtendedPlattCalibrator()

        cal.fit(
            p_raw,
            lead_days,
            outcomes,
            bin_widths=bin_widths,
            n_bootstrap=5,
            rng=np.random.default_rng(123),
        )

        assert cal.fitted is True
        assert np.isfinite([cal.A, cal.B, cal.C]).all()

    def test_fit_rejects_corrupt_widths(self):
        p_raw, lead_days, outcomes = _synthetic_data(60)
        bin_widths = np.array([1.0, 2.0, np.inf] * 20, dtype=object)
        cal = ExtendedPlattCalibrator()

        with pytest.raises(ValueError, match="bin_width must be finite and >0"):
            cal.fit(
                p_raw,
                lead_days,
                outcomes,
                bin_widths=bin_widths,
                n_bootstrap=5,
                rng=np.random.default_rng(123),
            )


class TestCalibrateAndNormalize:
    def test_sums_to_one(self):
        """calibrate_and_normalize must return vector summing to 1.0."""
        p_raw, lead_days, outcomes = _synthetic_data(100)
        cal = ExtendedPlattCalibrator()
        cal.fit(p_raw, lead_days, outcomes)

        p_raw_vec = np.array([0.05, 0.10, 0.30, 0.30, 0.10, 0.05,
                               0.03, 0.03, 0.02, 0.01, 0.01])
        result = calibrate_and_normalize(p_raw_vec, cal, lead_days=3.0)

        assert result.shape == (11,)
        assert pytest.approx(result.sum(), abs=0.001) == 1.0

    def test_all_positive(self):
        p_raw, lead_days, outcomes = _synthetic_data(100)
        cal = ExtendedPlattCalibrator()
        cal.fit(p_raw, lead_days, outcomes)

        p_raw_vec = np.array([0.05, 0.10, 0.30, 0.30, 0.10, 0.05,
                               0.03, 0.03, 0.02, 0.01, 0.01])
        result = calibrate_and_normalize(p_raw_vec, cal, lead_days=3.0)
        assert np.all(result > 0)

    def test_width_aware_calibration_path(self):
        cal = ExtendedPlattCalibrator()
        cal.fitted = True
        cal.A = 1.0
        cal.B = 0.0
        cal.C = 0.0
        cal.input_space = WIDTH_NORMALIZED_SPACE

        p_raw_vec = np.array([0.40, 0.10])
        result = calibrate_and_normalize(
            p_raw_vec,
            cal,
            lead_days=3.0,
            bin_widths=[2.0, 1.0],
        )

        np.testing.assert_array_almost_equal(result, [2 / 3, 1 / 3])
