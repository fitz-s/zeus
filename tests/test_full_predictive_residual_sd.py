# Created: 2026-06-03
# Last reused or audited: 2026-06-03
# Authority basis: operator pre-arm blocker 2026-06-03 (#89 coverage license DENIED) —
#   the live sigma_repr currently reads model_bias_ens.residual_sd_c, which is the IN-SAMPLE
#   daily-residual std ONLY. That under-states the honest FORWARD predictive uncertainty
#   when a fit-window (May) mean correction is applied to an out-of-window (June) serve
#   period: the estimated mean bias itself is uncertain (drift between fit and serve). The
#   honest forward sigma is the leave-one-day-out predictive std, which inflates the in-sample
#   residual by the mean-estimation variance: sigma_pred^2 = sigma_resid^2 * (1 + 1/n).
#   This pins the producer-side estimator contract.
# Lifecycle: created=2026-06-03; last_reviewed=2026-06-03; last_reused=never
# Purpose: producer antibody — full predictive sigma (not in-sample-only) for the q_lcb inflater.
# Reuse: inspect src/calibration/ens_error_model.py:full_predictive_residual_sd and
#   scripts/write_promoted_edli_bias.py / scripts/write_d7_rolling_edli_bias.py before
#   re-running; verify total_residual_sd_c column exists in model_bias_ens rows.
"""Full predictive residual sigma combines in-sample scatter with mean-estimation drift.

The estimator ``full_predictive_residual_sd(residuals)`` returns the honest predictive std
for ONE new (out-of-window) day given the per-day fit residuals:

    sigma_pred = sqrt( var_resid * (1 + 1/n) )

where var_resid is the sample variance of the per-day residuals (forecast - settlement). The
(1 + 1/n) factor is the textbook predictive-interval inflation: a future observation has the
residual scatter PLUS the variance of the estimated mean (var_resid / n). This is the honest
LOWER bound on the between-period heterogeneity that an in-sample-only std drops entirely.

Contract:
  1. full_predictive_residual_sd > in-sample std for any finite n (strictly wider).
  2. As n -> large, the two converge (the mean-estimation term vanishes).
  3. A high-heterogeneity (high-variance) residual set gets a larger ABSOLUTE inflation.
  4. Degenerate inputs (n<2, all-equal) fall back safely (>= in-sample std, never NaN/negative).
"""
from __future__ import annotations

import math

import pytest

from src.calibration.ens_error_model import (
    full_predictive_residual_sd,
    predictive_heterogeneity_var,
)


def _insample_std(xs: list[float]) -> float:
    import statistics
    return statistics.stdev(xs)


class TestFullPredictiveResidualSd:
    def test_strictly_wider_than_in_sample_for_finite_n(self):
        residuals = [-2.0, -1.0, 0.0, 1.0, 2.0]  # in-sample std = sqrt(2.5) ≈ 1.581
        in_sample = _insample_std(residuals)
        pred = full_predictive_residual_sd(residuals)
        assert pred > in_sample, (
            f"predictive sigma {pred:.6f} not wider than in-sample {in_sample:.6f}"
        )
        # Exact identity: sigma_pred = sigma_resid * sqrt(1 + 1/n).
        n = len(residuals)
        expected = in_sample * math.sqrt(1.0 + 1.0 / n)
        assert pred == pytest.approx(expected, rel=1e-9)

    def test_converges_to_in_sample_as_n_grows(self):
        # n=5 inflation factor sqrt(1.2)=1.0954; n=500 -> sqrt(1.002)=1.001.
        small = [-2.0, -1.0, 0.0, 1.0, 2.0]
        large = [(-2.0 + 4.0 * i / 499) for i in range(500)]
        infl_small = full_predictive_residual_sd(small) / _insample_std(small)
        infl_large = full_predictive_residual_sd(large) / _insample_std(large)
        assert infl_small > infl_large
        assert infl_large == pytest.approx(1.0, abs=2e-3)

    def test_high_variance_set_gets_larger_absolute_inflation(self):
        # Same n, different spread: the high-variance city gets a larger ABSOLUTE widening
        # (the inflation is multiplicative on the in-sample std).
        low_var = [-0.5, -0.25, 0.0, 0.25, 0.5]
        high_var = [-3.0, -1.5, 0.0, 1.5, 3.0]
        infl_low = full_predictive_residual_sd(low_var) - _insample_std(low_var)
        infl_high = full_predictive_residual_sd(high_var) - _insample_std(high_var)
        assert infl_high > infl_low

    def test_degenerate_inputs_safe(self):
        # n<2 -> cannot form a sample variance; must not raise, must not return negative/NaN.
        assert full_predictive_residual_sd([1.5]) >= 0.0
        assert full_predictive_residual_sd([]) >= 0.0
        # all-equal -> zero variance -> predictive sigma 0 (honest: no scatter observed).
        assert full_predictive_residual_sd([2.0, 2.0, 2.0]) == pytest.approx(0.0, abs=1e-12)

    def test_heterogeneity_var_is_mean_estimation_variance(self):
        # predictive_heterogeneity_var = var_resid / n (the between-period mean-drift term that
        # is added in quadrature to the in-sample residual variance).
        residuals = [-2.0, -1.0, 0.0, 1.0, 2.0]
        import statistics
        var_resid = statistics.variance(residuals)
        n = len(residuals)
        het = predictive_heterogeneity_var(residuals)
        assert het == pytest.approx(var_resid / n, rel=1e-9)
        # And total = sqrt(var_resid + het) must equal full_predictive_residual_sd.
        total = math.sqrt(var_resid + het)
        assert total == pytest.approx(full_predictive_residual_sd(residuals), rel=1e-9)
