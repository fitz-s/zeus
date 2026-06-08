# Created: 2026-06-08
# Last reused or audited: 2026-06-08
# Authority basis: U0R_BAYES_SPEC.md §2 T2, §4 algorithm; U0R_PROOF_RESULT.md skeptic re-probe
#   ("3-cell recompute mu*, sigma, Brier match stored predictions to 5 decimal places").
# Purpose: PORT-FIDELITY. The production src/forecast/u0r_bayes.py reproduces the PROVEN C1
#   posterior on a known settlement cell (Paris/high/lead-1/2025-12-26) to 4 decimals.
#   Golden inputs are the captured walk-forward internals of run_u0r_bayes_fusion.py at that
#   cell (z_lik, the 25x5 common-window residual matrix, mu0, tau0, disagree). Self-contained:
#   no dependency on the offline proof JSONL / dataset / LIVE repo.
"""Port-fidelity: src U0R fusion == proven proof C1 (mu*=4.3137, sd=0.7259)."""

from __future__ import annotations

import numpy as np
import pytest

from src.forecast.u0r_bayes import (
    ModelInstrument,
    bayes_fuse,
    diag_cov,
    fuse_u0r_posterior,
    shrink_cov,
)

# ---- GOLDEN FIXTURE: Paris / high / lead 1 / target_date 2025-12-26 (first walk-forward cell) ----
# Captured from run_u0r_bayes_fusion.py internals; proof stored mu=4.3137 sd=0.7259 (C1 variant).
LIK_MODELS = ["gfs_global", "icon_global", "gem_global", "jma_seamless", "icon_eu"]
Z_LIK = [5.14193939, 3.7449697, 5.05709091, 3.83587879, 4.11163636]
N_TRAIN = [25, 25, 25, 25, 25]
MU0 = 4.69648485
TAU0 = 1.03080874
DISAGREE = 0.15789218
EXPECT_MU = 4.3137
EXPECT_SD = 0.7259

# 25x5 common-window bias-corrected residual matrix columns (x_s - b_hat_s - Y), per instrument.
RESID_COLS = {
    "gfs_global": [0.74193939, 0.44193939, -1.25806061, -1.75806061, 1.24193939, -0.25806061, 0.04193939, -2.05806061, -0.45806061, -0.45806061, 1.84193939, 0.34193939, -0.35806061, 2.24193939, -0.65806061, -2.55806061, 0.94193939, -0.55806061, -0.25806061, 0.74193939, 0.54193939, 1.94193939, 1.34193939, -0.25806061, 1.74193939],
    "icon_global": [0.1449697, -0.8550303, -0.0550303, 0.2449697, 1.0449697, 0.9449697, 0.6449697, -1.1550303, -0.6550303, -0.8550303, 0.5449697, 0.3449697, 0.1449697, 0.3449697, 0.0449697, -1.5550303, 0.5449697, -0.0550303, -0.2550303, 0.2449697, 1.0449697, 1.2449697, -0.1550303, -0.7550303, 0.4449697],
    "gem_global": [0.35709091, 0.75709091, -0.54290909, -0.04290909, -0.24290909, -1.64290909, 0.45709091, -2.44290909, -1.14290909, -0.14290909, 1.85709091, 1.05709091, -0.54290909, -0.14290909, -0.24290909, -0.74290909, -0.44290909, -1.14290909, -0.64290909, 1.05709091, 1.85709091, 1.65709091, -1.24290909, -1.64290909, 2.25709091],
    "jma_seamless": [1.23587879, -0.46412121, -0.86412121, -1.26412121, 0.73587879, -0.76412121, 1.03587879, -1.56412121, -1.26412121, -0.26412121, 0.63587879, 0.63587879, -0.56412121, 0.93587879, -0.16412121, -2.16412121, -0.16412121, -1.36412121, -1.06412121, 0.63587879, 1.53587879, 1.73587879, 0.73587879, -1.46412121, 2.03587879],
    "icon_eu": [0.01163636, -1.38836364, -0.28836364, -0.08836364, 1.11163636, 0.71163636, 0.31163636, -1.28836364, -0.78836364, -0.78836364, 1.21163636, 0.41163636, -0.48836364, -0.08836364, 0.21163636, -1.48836364, 0.81163636, -0.08836364, -1.08836364, 0.41163636, 1.31163636, 1.21163636, -0.48836364, -0.58836364, 0.71163636],
}


def _resid_matrix() -> np.ndarray:
    return np.array([RESID_COLS[m] for m in LIK_MODELS], dtype=float).T  # (25, 5)


def test_bayes_fuse_reproduces_proof_c1_with_captured_sigma() -> None:
    """The math core: shrink_cov(M) -> bayes_fuse reproduces the proven mu*/sd."""
    M = _resid_matrix()
    Sigma = shrink_cov(M)
    mu, sd = bayes_fuse(np.array(Z_LIK), Sigma, MU0, TAU0, DISAGREE)
    assert round(mu, 4) == EXPECT_MU
    assert round(sd, 4) == EXPECT_SD


def test_fuse_u0r_posterior_end_to_end_reproduces_proof_c1() -> None:
    """The production API: anchor prior + 5 bias-corrected globals -> proven C1 posterior."""
    instruments = [
        ModelInstrument(
            model=m,
            z=Z_LIK[i],
            train_residuals=tuple(RESID_COLS[m]),
            n_train=N_TRAIN[i],
            is_regional=False,
        )
        for i, m in enumerate(LIK_MODELS)
    ]
    fused = fuse_u0r_posterior(
        anchor_z=MU0, anchor_tau0=TAU0, likelihood=instruments,
        disagree_var=DISAGREE, use_covariance=True,
    )
    assert fused.method == "T2_BAYES"
    assert round(fused.mu, 4) == EXPECT_MU
    assert round(fused.sd, 4) == EXPECT_SD
    assert fused.anchor_model == "ecmwf_ifs"
    assert fused.used_models == ("ecmwf_ifs",) + tuple(LIK_MODELS)


def test_c0_diagonal_branch_matches_proof_c0_construction() -> None:
    """use_covariance=False forces the C0 diagonal Sigma branch (proof C0 ablation)."""
    M = _resid_matrix()
    lown = [n < 25 for n in N_TRAIN]
    Sigma0 = diag_cov(M, lown)
    mu_expected, sd_expected = bayes_fuse(np.array(Z_LIK), Sigma0, MU0, TAU0, DISAGREE)
    instruments = [
        ModelInstrument(model=m, z=Z_LIK[i], train_residuals=tuple(RESID_COLS[m]),
                        n_train=N_TRAIN[i])
        for i, m in enumerate(LIK_MODELS)
    ]
    fused = fuse_u0r_posterior(
        anchor_z=MU0, anchor_tau0=TAU0, likelihood=instruments,
        disagree_var=DISAGREE, use_covariance=False,
    )
    assert fused.mu == pytest.approx(mu_expected, abs=1e-9)
    assert fused.sd == pytest.approx(sd_expected, abs=1e-9)


def test_port_matches_live_proof_engine_if_present() -> None:
    """If the offline proof engine is reachable, assert src functions are byte-equal to it
    on the captured cell (covers any future drift between port and proof)."""
    import importlib.util
    from pathlib import Path

    proof = Path(
        "/Users/leofitz/zeus/.omc/research/polyweather_eval/scripts/run_u0r_bayes_fusion.py"
    )
    if not proof.exists():
        pytest.skip("offline proof engine not present in this checkout")
    spec = importlib.util.spec_from_file_location("u0rproof_ref", proof)
    ref = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ref)

    M = _resid_matrix()
    # Covariance estimators must agree element-wise.
    np.testing.assert_allclose(shrink_cov(M), ref.shrink_cov(M), atol=1e-12)
    lown = [n < 25 for n in N_TRAIN]
    np.testing.assert_allclose(diag_cov(M, lown), ref.diag_cov(M, lown), atol=1e-12)
    # T2 posterior must agree.
    Sigma = shrink_cov(M)
    mu_src, sd_src = bayes_fuse(np.array(Z_LIK), Sigma, MU0, TAU0, DISAGREE)
    mu_ref, sd_ref = ref.bayes_fuse(np.array(Z_LIK), Sigma, MU0, TAU0, DISAGREE)
    assert mu_src == pytest.approx(mu_ref, abs=1e-12)
    assert sd_src == pytest.approx(sd_ref, abs=1e-12)


def test_anchor_fallback_when_all_extras_absent() -> None:
    """FAIL-SOFT: no likelihood instruments -> posterior IS the anchor prior."""
    fused = fuse_u0r_posterior(anchor_z=MU0, anchor_tau0=TAU0, likelihood=[], disagree_var=0.0)
    assert fused.method == "ANCHOR_FALLBACK"
    assert fused.mu == pytest.approx(MU0)
    assert fused.sd == pytest.approx(TAU0)
    assert fused.used_models == ("ecmwf_ifs",)


def test_dropped_global_fuses_with_remaining() -> None:
    """FAIL-SOFT: one global dropped -> fusion proceeds with the survivors (still T2)."""
    survivors = LIK_MODELS[:-1]  # drop icon_eu
    instruments = [
        ModelInstrument(model=m, z=Z_LIK[i], train_residuals=tuple(RESID_COLS[m]),
                        n_train=N_TRAIN[i])
        for i, m in enumerate(survivors)
    ]
    fused = fuse_u0r_posterior(anchor_z=MU0, anchor_tau0=TAU0, likelihood=instruments,
                               disagree_var=DISAGREE)
    assert fused.method == "T2_BAYES"
    assert fused.used_models == ("ecmwf_ifs",) + tuple(survivors)
    assert np.isfinite(fused.mu) and fused.sd > 0.0


def test_equal_weight_when_no_reliable_anchor_prior() -> None:
    """FAIL-SOFT: anchor absent (no >=MIN_TRAIN anchor) -> shrink-to-equal of corrected reps."""
    instruments = [
        ModelInstrument(model=m, z=Z_LIK[i], train_residuals=tuple(RESID_COLS[m]),
                        n_train=N_TRAIN[i])
        for i, m in enumerate(LIK_MODELS)
    ]
    fused = fuse_u0r_posterior(anchor_z=None, anchor_tau0=None, likelihood=instruments,
                               disagree_var=DISAGREE)
    assert fused.method == "EQUAL_WEIGHT"
    assert fused.anchor_model is None
    assert fused.mu == pytest.approx(float(np.mean(Z_LIK)), abs=1e-9)
