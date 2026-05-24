# Lifecycle: created=2026-05-24; last_reviewed=2026-05-24; last_reused=never
# Purpose: Unit tests for the predictive-error layer: SNR correction-strength gate, residual scale, bucket fit.
# Reuse: Inspect ens_error_model before reuse.
"""TDD tests for the universal predictive-error layer.

Extends the #334 mean-bias (location) estimator with a UNIVERSAL scale + confidence
gate, so the SAME parameters handle both failure modes with zero city-specific logic:
  - SF  (large consistent bias, high residual): gate passes (λ→1) AND wide residual
        => correction applied + predictive distribution widened.
  - Chicago (prior↔live disagreement / low SNR): gate λ→0 => no confident point shift.

correction_strength λ from signal-to-noise z = |bias| / sqrt(bias_sd^2 + heterogeneity):
  z < 1      -> λ = 0     (do not shift; uncertainty dominates)
  1 <= z < 2 -> λ = z-1   (partial)
  z >= 2     -> λ = 1     (full)
"""

from __future__ import annotations

import math

import pytest

from src.calibration.ens_bias_model import BiasPrior, LiveResidual, posterior_bias
from src.calibration.ens_error_model import (
    PredictiveErrorModel,
    correction_strength,
    predictive_error_from_posterior,
)


# ---- correction_strength SNR gate (universal) ----

def test_gate_zero_below_snr_1():
    # |bias|=0.5, uncertainty sd=1.0 -> z=0.5 < 1 -> no shift
    assert correction_strength(bias=-0.5, bias_sd=1.0, heterogeneity_var=0.0) == pytest.approx(0.0)


def test_gate_partial_between_1_and_2():
    # z=1.5 -> lambda = 0.5
    assert correction_strength(bias=-1.5, bias_sd=1.0, heterogeneity_var=0.0) == pytest.approx(0.5)


def test_gate_full_at_snr_2_or_more():
    # z=3 -> lambda = 1.0 (clamped)
    assert correction_strength(bias=-3.0, bias_sd=1.0, heterogeneity_var=0.0) == pytest.approx(1.0)


def test_gate_heterogeneity_reduces_strength():
    # same bias, but large heterogeneity (disagreement) lowers z -> smaller lambda
    strong = correction_strength(bias=-2.0, bias_sd=0.3, heterogeneity_var=0.0)
    weak = correction_strength(bias=-2.0, bias_sd=0.3, heterogeneity_var=9.0)
    assert weak < strong
    assert weak == pytest.approx(0.0)  # z = 2/sqrt(0.09+9) ~ 0.66 < 1


# ---- end-to-end via #334 posterior (SF vs Chicago, SAME code path) ----

def test_sf_like_high_confident_bias_gets_full_correction():
    # large consistent cold bias, tight, no disagreement -> lambda ~ 1
    prior = BiasPrior(mu_t=-5.5, v0=0.3)
    live = LiveResidual(e_bar=-6.0, n=40, sigma2=2.0)
    post = posterior_bias(prior, live)
    em = predictive_error_from_posterior(post, residual_sd_c=2.0)
    assert em.correction_strength > 0.8
    assert em.disagreement_high is False
    assert em.total_residual_sd_c >= 2.0  # at least the residual scale


def test_chicago_like_disagreement_gates_correction_to_zero():
    # prior cold, live neutral -> disagreement -> heterogeneity large -> lambda 0
    prior = BiasPrior(mu_t=-1.91, v0=0.02)
    live = LiveResidual(e_bar=+0.25, n=40, sigma2=0.8)
    post = posterior_bias(prior, live)
    em = predictive_error_from_posterior(post, residual_sd_c=1.5)
    assert em.disagreement_high is True
    assert em.correction_strength == pytest.approx(0.0), "disagreement must veto the point shift"
    # predictive sd still reflects heterogeneity (widened -> haircut / no-bet downstream)
    assert em.total_residual_sd_c > 1.5


def test_total_residual_sd_combines_residual_and_heterogeneity():
    prior = BiasPrior(mu_t=-1.0, v0=0.02)
    live = LiveResidual(e_bar=+1.0, n=40, sigma2=0.8)  # disagreement -> het>0
    post = posterior_bias(prior, live)
    em = predictive_error_from_posterior(post, residual_sd_c=1.0)
    assert em.total_residual_sd_c == pytest.approx(math.sqrt(1.0**2 + post.heterogeneity_var))


def test_effective_bias_is_strength_times_posterior():
    prior = BiasPrior(mu_t=-1.5, v0=0.5)
    live = LiveResidual(e_bar=-1.5, n=50, sigma2=1.0)
    post = posterior_bias(prior, live)
    em = predictive_error_from_posterior(post, residual_sd_c=1.0)
    assert em.effective_bias_c == pytest.approx(em.correction_strength * post.bias)


# ---- bucket fit: location (fit_bucket) + scale (residual_sd) ----

def test_fit_predictive_error_bucket_scale_tracks_spread():
    from src.calibration.ens_error_model import fit_predictive_error_bucket
    import random
    r = random.Random(0)
    tig = [r.gauss(-1.0, 1.0) for _ in range(200)]
    tight = fit_predictive_error_bucket(tig, [r.gauss(-1.5, 0.5) for _ in range(40)])
    wide = fit_predictive_error_bucket(tig, [r.gauss(-1.5, 3.0) for _ in range(40)])
    assert wide.residual_sd_c > tight.residual_sd_c, "scale must track live forecast-error spread"
    assert tight.residual_sd_c >= 0.5  # floored at >= sensor-ish level


def test_fit_predictive_error_bucket_falls_back_to_prior_scale_when_live_sparse():
    from src.calibration.ens_error_model import fit_predictive_error_bucket
    import random
    r = random.Random(1)
    tig = [r.gauss(-1.0, 2.0) for _ in range(200)]
    em = fit_predictive_error_bucket(tig, [-3.0, -3.0], min_live_n=20)  # sparse live -> prior scale
    from src.calibration.ens_bias_model import robust_mean
    assert em.bias_c == pytest.approx(robust_mean(tig), abs=0.5)  # bias falls to TIGGE prior (live dropped)
    assert em.residual_sd_c > 1.0, "sparse live -> use TIGGE spread (~2C), not a tiny live std"
