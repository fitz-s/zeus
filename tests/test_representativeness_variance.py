# Created: 2026-06-17
# Last reused or audited: 2026-06-17
# Authority basis: operator spec zeus_source_access_validation_v3.xlsx GridCorrectionMath
#   rule 4 (x_station = T_interp + beta_alt*(z_station - z_interp) + b_grid; walk-forward
#   fit interface + inert cold-start) + rule 5 (sigma_repr^2 = g(d_eff,|dz|,regime),
#   monotone, ADDED to Sigma diagonal: Sigma_source = Sigma_model_residual + sigma_repr^2,
#   NEVER a hand weight). End-to-end uses the LIVE src/forecast/bayes_precision_fusion.py
#   (V*=(tau0^-2+1'Sigma^-1 1)^-1; mu*=V*(tau0^-2 mu0 + 1'Sigma^-1 z)) UNMODIFIED to prove
#   the Sigma-diagonal add lowers an instrument's fusion weight via Sigma^-1 alone.
"""RED-on-revert tests for sigma_repr^2 = g(...) + station correction (v3 rule 4/5),
incl. the Sigma-diagonal-vs-hand-weight proof against the live Bayes fusion."""
from __future__ import annotations

import numpy as np
import pytest

from src.forecast.bayes_precision_fusion import ModelInstrument, bayes_fuse, fuse_bayes_precision_posterior
from src.forecast.representativeness_variance import (
    COLD_START_REPR_VARIANCE,
    COLD_START_STATION_SHIFT,
    ReprResidualRow,
    ReprVarianceFit,
    StationShiftFit,
    StationShiftResidualRow,
    fit_representativeness_variance,
    fit_station_shift,
    representativeness_variance,
    sigma_with_representativeness,
    station_correction,
)


# ============================================================================
# Rule 4 — station/elevation correction + walk-forward fit interface + cold start
# ============================================================================
def test_cold_start_station_shift_applies_no_live_shift():
    """The cold-start (beta_alt=0, b_grid=0) is INERT: x_station == T_interp."""
    assert COLD_START_STATION_SHIFT.beta_alt == 0.0
    assert COLD_START_STATION_SHIFT.b_grid == 0.0
    assert COLD_START_STATION_SHIFT.fitted is False
    x = station_correction(T_interp=15.0, z_station=500.0, z_interp=100.0)
    assert x == pytest.approx(15.0, abs=1e-12)  # no lapse-rate shift applied by default


def test_station_correction_applies_beta_alt_times_delta_z():
    """With a fitted shift, x_station = T_interp + beta_alt*(z_station - z_interp) + b_grid."""
    fit = StationShiftFit(beta_alt=-0.0065, b_grid=0.2, n_train=40, fitted=True)
    # dz = 400 m -> beta_alt*dz = -2.6 ; + b_grid 0.2 -> -2.4 shift.
    x = station_correction(T_interp=15.0, z_station=500.0, z_interp=100.0, shift=fit)
    assert x == pytest.approx(15.0 + (-0.0065 * 400.0) + 0.2, abs=1e-9)


def test_fit_station_shift_recovers_known_slope():
    """The fit interface recovers a planted beta_alt/b_grid from settled residual rows."""
    true_beta, true_b = -0.0065, 0.3
    rows = [
        StationShiftResidualRow(settlement_residual=true_beta * dz + true_b, dz=dz)
        for dz in range(-200, 200, 5)  # >25 rows, real dz spread
    ]
    fit = fit_station_shift(rows)
    assert fit.fitted is True
    assert fit.beta_alt == pytest.approx(true_beta, rel=1e-6)
    assert fit.b_grid == pytest.approx(true_b, abs=1e-6)


def test_fit_station_shift_below_min_train_returns_inert_cold_start():
    """Below min_train the fit returns the inert cold-start, not a fabricated slope."""
    rows = [StationShiftResidualRow(settlement_residual=1.0, dz=100.0) for _ in range(10)]
    fit = fit_station_shift(rows, min_train=25)
    assert fit.fitted is False
    assert fit.beta_alt == 0.0 and fit.b_grid == 0.0


# ============================================================================
# Rule 5 — sigma_repr^2 = g(...) monotone, fit interface, cold start
# ============================================================================
def test_sigma_repr_increases_with_d_eff():
    """sigma_repr^2 is monotone non-decreasing in d_eff (all else equal)."""
    base = representativeness_variance(d_eff_m=0.0, dz_m=0.0)
    far = representativeness_variance(d_eff_m=15000.0, dz_m=0.0)  # 15 km (the cold-city case)
    mid = representativeness_variance(d_eff_m=5000.0, dz_m=0.0)
    assert base < mid < far


def test_sigma_repr_increases_with_abs_delta_z():
    """sigma_repr^2 is monotone non-decreasing in |z_station - z_interp|."""
    flat = representativeness_variance(d_eff_m=1000.0, dz_m=0.0)
    hilly = representativeness_variance(d_eff_m=1000.0, dz_m=300.0)
    assert hilly > flat
    # Sign-agnostic: only |dz| matters (dz^2).
    assert representativeness_variance(1000.0, 300.0) == pytest.approx(
        representativeness_variance(1000.0, -300.0)
    )


def test_sigma_repr_is_nonnegative_variance():
    """g is a variance: never negative, even at zero features."""
    assert representativeness_variance(0.0, 0.0) >= 0.0


def test_sigma_repr_regime_multipliers_widen_only():
    """Coastal/orography/urban regimes only WIDEN sigma_repr^2 (multiplier >= 1)."""
    flat = representativeness_variance(2000.0, 50.0)
    coastal = representativeness_variance(2000.0, 50.0, coastal=True)
    urban = representativeness_variance(2000.0, 50.0, urban=True)
    assert coastal >= flat
    assert urban >= flat


def test_fit_representativeness_variance_recovers_distance_slope():
    """The MLE-style fit recovers a planted distance variance slope from residuals."""
    # Plant residual std growing with d_eff: var = 0.25 + 0.04*(d_km)^2 (cold-start form).
    rng = np.random.default_rng(7)
    rows = []
    for _ in range(400):
        d_eff = rng.uniform(0, 10000)  # 0..10 km
        d_km = d_eff / 1000.0
        var = 0.25 + 0.04 * d_km * d_km
        resid = rng.normal(0.0, np.sqrt(var))
        rows.append(ReprResidualRow(settlement_residual=resid, d_eff_m=d_eff, dz_m=0.0))
    fit = fit_representativeness_variance(rows)
    assert fit.fitted is True
    # The fitted distance coefficient is positive and near the planted 0.04.
    assert fit.a_d > 0.0
    assert fit.a_d == pytest.approx(0.04, abs=0.03)


def test_fit_representativeness_variance_below_min_train_is_cold_start():
    rows = [ReprResidualRow(settlement_residual=1.0, d_eff_m=1000.0, dz_m=0.0) for _ in range(5)]
    fit = fit_representativeness_variance(rows, min_train=25)
    assert fit.fitted is False
    assert fit.a0 == COLD_START_REPR_VARIANCE.a0


def test_fit_coefficients_are_nonnegative():
    """Fitted g-coefficients are clamped >= 0 so g stays a widen-only variance."""
    rng = np.random.default_rng(1)
    rows = [
        ReprResidualRow(
            settlement_residual=rng.normal(0, 0.5),
            d_eff_m=rng.uniform(0, 3000),
            dz_m=rng.uniform(-100, 100),
        )
        for _ in range(200)
    ]
    fit = fit_representativeness_variance(rows)
    assert fit.a0 >= 0.0 and fit.a_d >= 0.0 and fit.a_z >= 0.0


# ============================================================================
# Sigma-diagonal add (NOT a hand weight): the exact mechanism
# ============================================================================
def test_sigma_with_representativeness_is_plus_on_diagonal():
    """Sigma_source = Sigma_model_residual + sigma_repr^2 — an additive diagonal term."""
    out = sigma_with_representativeness(sigma_model_residual_sq=1.0, sigma_repr_sq=0.75)
    assert out == pytest.approx(1.75)


def test_sigma_repr_lowers_fusion_weight_via_sigma_inverse_not_hand_weight():
    """END-TO-END: a larger sigma_repr ADDED to an instrument's Sigma diagonal lowers
    that instrument's pull on mu* through the LIVE fusion's own Sigma^-1 — with NO
    manual weight anywhere.

    Construct two instruments z=[z0, z1] disagreeing with the anchor prior. Build Sigma
    as pure diagonal model-residual variance, then ADD sigma_repr^2 to instrument 0's
    diagonal only. The instrument whose variance grew must move mu* LESS. We never touch
    a weight: only the Sigma diagonal changes, and bayes_fuse derives the weights.
    """
    mu0, tau0 = 10.0, 1.0           # anchor prior centred at 10
    z = np.array([14.0, 14.0])       # both instruments pull toward 14
    sigma_model_resid = np.array([1.0, 1.0])  # equal instrument variance to start

    # Baseline: equal diagonal Sigma -> both instruments pull equally.
    Sigma_base = np.diag(sigma_model_resid)
    mu_base, _ = bayes_fuse(z, Sigma_base, mu0, tau0, extra_var=0.0)

    # Now ADD a large sigma_repr^2 to instrument 0 ONLY via the module's diagonal add.
    sigma_repr_0 = representativeness_variance(d_eff_m=15000.0, dz_m=400.0, orography=True)
    diag0 = sigma_with_representativeness(sigma_model_resid[0], sigma_repr_0)
    Sigma_repr = np.diag([diag0, sigma_model_resid[1]])
    mu_repr, _ = bayes_fuse(z, Sigma_repr, mu0, tau0, extra_var=0.0)

    # The added representativeness variance pulls mu* BACK toward the anchor (10): the
    # noisier instrument 0 lost influence. Strictly less far from the prior than baseline.
    assert mu_repr < mu_base
    assert abs(mu_repr - mu0) < abs(mu_base - mu0)

    # PROOF it is variance, not a hand weight: do the SAME thing through the live
    # production fuse path with an instrument whose train_residuals encode the larger
    # variance, and confirm the same directional effect (mu* nearer the anchor).
    base_inst = [
        ModelInstrument(model="gfs_global", z=14.0, train_residuals=tuple(np.full(40, 0.0)), n_train=40),
        ModelInstrument(model="icon_global", z=14.0, train_residuals=tuple(np.full(40, 0.0)), n_train=40),
    ]
    # Inflate instrument 0's residual spread by sqrt(diag0) so its diagonal grows the
    # SAME way (the production diag_cov reads variance from train_residuals).
    rng = np.random.default_rng(3)
    wide = tuple(rng.normal(0.0, np.sqrt(diag0), 40))
    narrow = tuple(rng.normal(0.0, np.sqrt(sigma_model_resid[1]), 40))
    repr_inst = [
        ModelInstrument(model="gfs_global", z=14.0, train_residuals=wide, n_train=40),
        ModelInstrument(model="icon_global", z=14.0, train_residuals=narrow, n_train=40),
    ]
    post_base = fuse_bayes_precision_posterior(anchor_z=mu0, anchor_tau0=tau0, likelihood=base_inst, use_covariance=False)
    post_repr = fuse_bayes_precision_posterior(anchor_z=mu0, anchor_tau0=tau0, likelihood=repr_inst, use_covariance=False)
    # Wider instrument-0 variance -> posterior pulled back toward the anchor prior.
    assert abs(post_repr.mu - mu0) < abs(post_base.mu - mu0)
