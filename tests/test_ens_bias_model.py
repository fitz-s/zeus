# Lifecycle: created=2026-05-24; last_reviewed=2026-05-24; last_reused=never
# Purpose: Relationship + unit tests for the empirical-Bayes posterior shrinkage estimator, pre-MC application, train/serve guard, and disagreement gating.
# Reuse: Inspect ens_bias_model.posterior_bias/fit_bucket before reuse.
"""Relationship tests (cross-module invariant) for the hierarchical ENS bias estimator.

Invariant under test (forecast-product → live-correction boundary):
  The live OpenData forecast bias is estimated by Bayesian shrinkage of a TIGGE
  structural prior (complete seasonal/monthly coverage) toward the OpenData live
  settled-residual likelihood (sparse, ~1 month). The posterior:
    - collapses to the live mean when live evidence is abundant (w→1),
    - collapses to the TIGGE prior when no live evidence exists (w→0),
    - has variance no greater than either input (information cannot hurt),
    - down-weights the TIGGE prior (inflates V0) when the same-window paired
      OpenData−TIGGE delta is large (transfer uncertainty), so live evidence
      dominates wherever it exists.

These encode the operator's 2026-05-24 decision that neither TIGGE-only nor
OpenData-only is correct: TIGGE is a prior, OpenData is the likelihood, and the
TIGGE↔OpenData equivalence is treated as transfer *uncertainty*, not a binary.
"""

from __future__ import annotations

import math

import pytest

from src.calibration.ens_bias_model import (
    BiasPrior,
    LiveResidual,
    posterior_bias,
)


def _prior(mu_t: float = -1.0, v0: float = 0.25) -> BiasPrior:
    # TIGGE structural prior: mean cold bias -1.0 degC, estimation variance 0.25 (sd 0.5).
    return BiasPrior(mu_t=mu_t, v0=v0)


def test_posterior_dominated_by_live_when_n_large():
    """Abundant live evidence ⇒ posterior ≈ live mean (w→1), prior nearly ignored."""
    prior = _prior(mu_t=-1.0, v0=0.25)
    live = LiveResidual(e_bar=-1.9, n=600, sigma2=4.0)  # V_O = 4/600 ≈ 0.0067 ≪ v0
    post = posterior_bias(prior, live)
    assert post.weight_live > 0.95, "large-n live must dominate"
    assert abs(post.bias - (-1.9)) < 0.1, "posterior must track the live mean"
    assert post.n_live == 600


def test_posterior_falls_back_to_prior_when_no_live_data():
    """No live evidence ⇒ posterior == TIGGE prior mean (w→0), uncertainty = prior V0."""
    prior = _prior(mu_t=-1.0, v0=0.25)
    post = posterior_bias(prior, None)
    assert post.weight_live == pytest.approx(0.0)
    assert post.bias == pytest.approx(-1.0)
    assert post.sd == pytest.approx(0.5)  # sqrt(v0)
    assert post.n_live == 0


def test_posterior_variance_never_exceeds_either_input():
    """Information cannot hurt: V_post ≤ min(V0, V_O) (strict combine)."""
    prior = _prior(mu_t=-1.0, v0=0.25)
    live = LiveResidual(e_bar=-2.5, n=40, sigma2=9.0)  # V_O = 9/40 = 0.225
    post = posterior_bias(prior, live)
    v_o = 9.0 / 40
    v_post = post.sd ** 2
    assert v_post <= 0.25 + 1e-9
    assert v_post <= v_o + 1e-9
    # exact precision-addition identity
    assert v_post == pytest.approx(1.0 / (1.0 / 0.25 + 1.0 / v_o))


def test_large_paired_transfer_delta_inflates_prior_uncertainty():
    """When OpenData−TIGGE paired delta is large, the prior is less trustworthy:
    V0 inflates, so live evidence wins more even at moderate n (transfer uncertainty)."""
    prior = _prior(mu_t=-1.0, v0=0.25)
    live = LiveResidual(e_bar=-2.5, n=40, sigma2=9.0)
    post_trusted = posterior_bias(prior, live, paired_delta_abs=0.0)
    post_distrust = posterior_bias(prior, live, paired_delta_abs=1.5)
    assert post_distrust.weight_live > post_trusted.weight_live, (
        "large paired delta must shift weight toward live evidence"
    )
    # and the posterior moves closer to the live mean under distrust
    assert abs(post_distrust.bias - (-2.5)) < abs(post_trusted.bias - (-2.5))


def test_delta_g_group_correction_shifts_prior_mean():
    """A group-level transfer offset δ_g shifts the prior mean used for shrinkage."""
    prior = _prior(mu_t=-1.0, v0=0.25)
    post = posterior_bias(prior, None, delta_g=-0.5)
    assert post.bias == pytest.approx(-1.5)  # mu_t + delta_g, no live data


def test_bias_sign_convention_matches_forecast_minus_actual():
    """bias = mean(forecast - actual); negative = forecast cold. Correction subtracts it,
    so a cold (negative) bias yields a positive temperature add downstream."""
    prior = _prior(mu_t=-1.2, v0=1e9)  # near-uninformative prior
    live = LiveResidual(e_bar=-1.2, n=300, sigma2=3.0)
    post = posterior_bias(prior, live)
    assert post.bias < 0  # cold
    # downstream correction: corrected = raw - bias = raw - (-1.2) = raw + 1.2 (warmer)
    assert (0.0 - post.bias) > 0


# --- pre-MC application + train/serve guard ---

def test_apply_bias_to_extrema_warms_cold_forecast():
    import numpy as np
    from src.calibration.ens_bias_model import apply_bias_to_extrema, PosteriorBias
    raw = np.array([18.0, 19.0, 20.0])
    post = PosteriorBias(bias=-1.5, sd=0.5, weight_live=0.9, n_live=80)
    corrected = apply_bias_to_extrema(raw, post)
    # corrected = raw - bias = raw - (-1.5) = raw + 1.5 (warmer)
    assert np.allclose(corrected, raw + 1.5)
    assert float(corrected.mean()) > float(raw.mean())


def test_train_serve_guard_blocks_live_correction_without_corrected_platt():
    from src.calibration.ens_bias_model import assert_bias_state_consistent
    # enabling live correction while Platt was fit on uncorrected pairs MUST raise
    with pytest.raises(ValueError, match="train/serve"):
        assert_bias_state_consistent(live_bias_enabled=True, platt_bias_corrected=False)


def test_train_serve_guard_allows_consistent_states():
    from src.calibration.ens_bias_model import assert_bias_state_consistent
    assert_bias_state_consistent(live_bias_enabled=True, platt_bias_corrected=True) is None
    assert_bias_state_consistent(live_bias_enabled=False, platt_bias_corrected=False) is None
    # correction off but corrected Platt present is benign (does not raise)
    assert_bias_state_consistent(live_bias_enabled=False, platt_bias_corrected=True) is None


def test_train_serve_guard_blocks_error_model_family_mismatch():
    from src.calibration.ens_bias_model import assert_bias_state_consistent
    # live serves full_transport_v1-corrected p_raw, but active Platt was fit on a
    # DIFFERENT family ('none') — out-of-domain even though bias_corrected=1 both.
    with pytest.raises(ValueError, match="error-model mismatch"):
        assert_bias_state_consistent(
            live_bias_enabled=True,
            platt_bias_corrected=True,
            live_error_model_family="full_transport_v1",
            active_platt_error_model_family="none",
        )
    # reverse mismatch (live none, Platt corrected family) also raises
    with pytest.raises(ValueError, match="error-model mismatch"):
        assert_bias_state_consistent(
            live_bias_enabled=True,
            platt_bias_corrected=True,
            live_error_model_family="none",
            active_platt_error_model_family="full_transport_v1",
        )


def test_train_serve_guard_allows_matching_error_model_family():
    from src.calibration.ens_bias_model import assert_bias_state_consistent
    # matching family is the green path
    assert_bias_state_consistent(
        live_bias_enabled=True,
        platt_bias_corrected=True,
        live_error_model_family="full_transport_v1",
        active_platt_error_model_family="full_transport_v1",
    ) is None
    # None/'none' compare equal (uncorrected family across representations)
    assert_bias_state_consistent(
        live_bias_enabled=True,
        platt_bias_corrected=True,
        live_error_model_family=None,
        active_platt_error_model_family="none",
    ) is None
    # family axis is not checked when correction is OFF (benign)
    assert_bias_state_consistent(
        live_bias_enabled=False,
        platt_bias_corrected=True,
        live_error_model_family="none",
        active_platt_error_model_family="full_transport_v1",
    ) is None


def test_posterior_flags_disagreement_and_widens_sd():
    # Chicago-like: tight prior cold, tight live neutral -> they disagree far more than
    # their combined SD explains -> posterior must flag disagreement + widen reported sd.
    from src.calibration.ens_bias_model import BiasPrior, LiveResidual, posterior_bias
    prior = BiasPrior(mu_t=-1.91, v0=0.02)
    live = LiveResidual(e_bar=+0.25, n=40, sigma2=0.8)   # V_O = 0.02
    post = posterior_bias(prior, live)
    assert post.disagreement_high is True
    # agreement case: prior and live close -> not flagged, tighter sd
    prior2 = BiasPrior(mu_t=-1.0, v0=0.02)
    live2 = LiveResidual(e_bar=-1.1, n=40, sigma2=0.8)
    post2 = posterior_bias(prior2, live2)
    assert post2.disagreement_high is False
    assert post.heterogeneity_var > post2.heterogeneity_var, "disagreement must add heterogeneity variance"
    assert post2.heterogeneity_var == 0.0
