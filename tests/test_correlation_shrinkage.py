# Created: 2026-05-21
# Last reused or audited: 2026-05-21
# Authority basis: docs/operations/task_2026-05-21_strategy_vnext_phase5_regime_correlation/PHASE_5_PLAN.md §Track1 acceptance tests

"""Phase 5 T1 acceptance tests for Ledoit-Wolf shrinkage estimator.

Four tests per plan §T1:
  1. test_intensity_converges_diagonal_target   — AR(1) convergence as n grows
  2. test_intensity_bounded_sparse              — n < p → δ* bounded away from 0
  3. test_known_diagonal_input                 — γ=0 clamp guard (δ*=0)
  4. test_n_equals_1_edge_case                 — n=1 → δ*=1.0 (full shrinkage)
"""

import numpy as np
import pytest

from src.strategy.correlation_shrinkage import (
    ShrinkageEstimate,
    ledoit_wolf_shrunk_correlation,
)


# ---------------------------------------------------------------------------
# RNG helper — seeded for reproducibility
# ---------------------------------------------------------------------------

def _ar1_residuals(
    n: int,
    p: int,
    rho: float = 0.3,
    rng: np.random.Generator | None = None,
) -> np.ndarray:
    """Generate AR(1) residuals of shape (n, p) with autocorrelation rho.

    Each column is an independent AR(1) series; columns share the same rho.
    True underlying correlation between columns is zero (diagonal true cov).
    """
    if rng is None:
        rng = np.random.default_rng(42)
    out = np.zeros((n, p))
    out[0] = rng.standard_normal(p)
    for t in range(1, n):
        out[t] = rho * out[t - 1] + np.sqrt(1 - rho ** 2) * rng.standard_normal(p)
    return out


# ---------------------------------------------------------------------------
# T1-1: δ* converges toward 0 as n grows (plan §T1 acceptance test 1)
# ---------------------------------------------------------------------------

def test_intensity_converges_diagonal_target() -> None:
    """AR(1), p=20; δ* is monotonically non-increasing as n ∈ {50,100,250,500,1000}.

    The true underlying covariance is diagonal (columns independent AR(1)).
    As n → ∞ the sample correlation S converges to the true diagonal target D,
    so the Ledoit-Wolf intensity δ* should converge toward 0.

    Plan §T1 acceptance criterion (verbatim):
      "δ* monotonically decreasing toward 0."
    We allow non-strict monotonicity (≤) to tolerate finite-sample noise; the
    trend direction must be downward from n=50 to n=1000.
    """
    rng = np.random.default_rng(2026_05_21)
    p = 20
    ns = [50, 100, 250, 500, 1000]
    intensities: list[float] = []
    for n in ns:
        residuals = _ar1_residuals(n, p, rng=rng)
        est = ledoit_wolf_shrunk_correlation(residuals)
        assert isinstance(est, ShrinkageEstimate)
        assert 0.0 <= est.intensity <= 1.0, f"intensity out of [0,1]: {est.intensity}"
        intensities.append(est.intensity)

    # Monotonically non-increasing across the series.
    for i in range(len(intensities) - 1):
        assert intensities[i] >= intensities[i + 1], (
            f"Intensity not non-increasing: [{ns[i]}]={intensities[i]:.4f} > "
            f"[{ns[i+1]}]={intensities[i+1]:.4f}"
        )

    # Final intensity at n=1000 must be strictly less than at n=50.
    assert intensities[-1] < intensities[0], (
        f"Intensity did not decrease from n=50 ({intensities[0]:.4f}) "
        f"to n=1000 ({intensities[-1]:.4f})"
    )


# ---------------------------------------------------------------------------
# T1-2: n < p → δ* bounded away from 0 (plan §T1 acceptance test 2)
# ---------------------------------------------------------------------------

def test_intensity_bounded_sparse() -> None:
    """n < p → δ* is bounded away from 0; sample correlation is unreliable.

    When n < p the sample covariance matrix is singular; the estimator should
    apply substantial shrinkage (δ* is notably positive, not near 0).

    Plan §T1: "n<p → δ* bounded away from 0 (matrix invertible)."
    We require δ* > 0.05 (a conservative lower bound; typical values are >> 0.5).
    """
    rng = np.random.default_rng(999)
    n, p = 10, 20  # n < p
    residuals = _ar1_residuals(n, p, rng=rng)
    est = ledoit_wolf_shrunk_correlation(residuals)
    assert est.n_observations == n
    assert est.p_dimensions == p
    assert est.intensity > 0.05, (
        f"Expected δ* > 0.05 for n={n} < p={p}; got {est.intensity:.4f}"
    )
    # Shrunk matrix must be closer to diagonal than sample.
    off_diag_mask = ~np.eye(p, dtype=bool)
    shrunk_off = np.abs(est.shrunk_correlation[off_diag_mask])
    sample_off = np.abs(est.sample_correlation[off_diag_mask])
    assert shrunk_off.mean() < sample_off.mean(), (
        "Shrunk off-diagonal mean should be less than sample off-diagonal mean "
        f"(shrunk={shrunk_off.mean():.4f}, sample={sample_off.mean():.4f})"
    )


# ---------------------------------------------------------------------------
# T1-3: diagonal input → γ=0 clamp guard → δ*=0 (plan §T1 acceptance test 3)
# ---------------------------------------------------------------------------

def test_known_diagonal_input() -> None:
    """Diagonal input S → γ=0 → δ*=0 (no divide-by-zero; clamp guard fires).

    When the input residuals produce a perfectly diagonal sample correlation
    (e.g., independent Gaussian columns), γ = ‖S - D‖²_F = 0.  The estimator
    MUST clamp δ* = 0 rather than computing 0/0.

    Plan §T1 acceptance test 3 docstring requirement:
      "γ=0 edge; estimator MUST clamp δ*=0 (no divide-by-zero);
       test docstring documents the clamp guard."

    Note: perfectly diagonal S only arises exactly on synthetic data where
    each column is drawn independently.  We construct such data explicitly
    using identity structure.
    """
    rng = np.random.default_rng(7)
    n, p = 500, 5
    # Independent standard-normal columns → sample correlation ≈ identity.
    # To guarantee exact γ=0 we pass an analytically diagonal matrix via
    # construction: each column is a separate draw, so off-diagonal entries
    # of np.cov will be very small but not exactly 0.
    # Instead, we bypass the data path and test the γ=0 guard directly by
    # passing residuals where each column is literally the same scaling
    # of the identity rows: this produces exact identity S.
    # We use the standard iid N(0,1) approach and rely on p=5, n=500 being
    # close enough; then also test the exact case with orthogonal matrix rows.
    residuals_iid = rng.standard_normal((n, p))
    est_iid = ledoit_wolf_shrunk_correlation(residuals_iid)
    # iid columns → very small γ → small δ*; no crash is the primary assertion.
    assert 0.0 <= est_iid.intensity <= 1.0

    # Exact diagonal test: manually feed δ*=0 path by using residuals whose
    # sample correlation is forced to be the identity (zero off-diagonal).
    # Build residuals with exact orthogonal columns.
    # Use Hadamard-like construction: column i = e_i * constant.
    exact_residuals = np.zeros((p, p))
    for i in range(p):
        exact_residuals[i, i] = 1.0
    # This is a (p, p) "identity" data matrix (each row has one non-zero entry).
    # Demeaned this collapses: mean of column i = 1/p; demeaned = -1/p except
    # position i where it is (p-1)/p.  Off-diagonal cov entries are:
    # sum_k (x_k,i - mean_i)(x_k,j - mean_j) = (p-1)/p * (-1/p) * p * 2 / (p-1)
    # which is generally non-zero.  Use a simple explicit diagonal case instead.
    # The cleanest exact γ=0 test: provide single-column data (p=1).
    residuals_p1 = rng.standard_normal((50, 1))
    est_p1 = ledoit_wolf_shrunk_correlation(residuals_p1)
    # p=1 → S = [[1]], D = [[1]], γ = 0 → clamp guard fires → δ*=0.
    assert est_p1.intensity == pytest.approx(0.0), (
        f"Expected δ*=0 for p=1 (γ=0 case); got {est_p1.intensity}"
    )
    assert est_p1.shrunk_correlation.shape == (1, 1)
    # Shrunk = sample when δ*=0.
    np.testing.assert_allclose(est_p1.shrunk_correlation, est_p1.sample_correlation, atol=1e-12)


# ---------------------------------------------------------------------------
# T1-4: n=1 → δ*=1.0 full shrinkage (plan §T1 acceptance test 4)
# ---------------------------------------------------------------------------

def test_n_equals_1_edge_case() -> None:
    """n=1 → δ*=1.0 (full shrinkage to diagonal target).

    A single observation makes the sample correlation undefined.  The estimator
    returns δ*=1.0 (collapse entirely to diagonal) and Σ_shrunk = I_p.

    Plan §T1 acceptance test 4.
    """
    p = 5
    residuals = np.array([[1.0, -2.0, 0.5, 3.0, -1.0]])  # shape (1, 5)
    est = ledoit_wolf_shrunk_correlation(residuals)
    assert est.n_observations == 1
    assert est.p_dimensions == p
    assert est.intensity == pytest.approx(1.0), (
        f"Expected δ*=1.0 for n=1; got {est.intensity}"
    )
    # Shrunk must be identity (diagonal of ones, zeros off-diagonal).
    np.testing.assert_allclose(est.shrunk_correlation, np.eye(p), atol=1e-12)
    # Target kind must propagate correctly.
    assert est.target_kind == "diagonal"
