# Created: 2026-05-21
# Last reused or audited: 2026-05-21
# Authority basis: docs/operations/task_2026-05-21_strategy_vnext_phase5_regime_correlation/PHASE_5_PLAN.md §Track1 + docs/reference/zeus_math_spec.md §15.4

"""Ledoit-Wolf shrinkage estimator for correlation matrices.

Implements the optimal shrinkage intensity over a diagonal target D as
specified in Zeus math spec §15.4 (Ledoit & Wolf 2003; Ledoit & Wolf 2004,
"Honey, I shrunk the sample covariance matrix").

Math spec §15.4 formula (verbatim):

    Σ_shrunk = (1 - δ*) · S + δ* · D

where D is the diagonal matrix formed from the diagonal entries of the
sample covariance S (retain variances, zero all off-diagonal covariances),
and the optimal intensity is:

    δ* = π / (γ × n)

- π = sum of asymptotic variances of the sample covariance entries
      (estimable from the data itself without additional assumptions)
- γ = squared Frobenius distance between sample covariance S and diagonal
      target D (measures how far the sample matrix is from diagonal)
- n = number of observations

Implementation clamp guards (applied in order before the main formula):
  1. n = 1: δ* = 1.0 (full shrinkage; sample correlation is undefined with
     a single observation, so we collapse entirely to the diagonal target).
  2. γ = 0 (S already diagonal): δ* = 0.0 (no shrinkage needed; divide-by-
     zero guard — target and sample coincide so MSE gain is exactly zero).
  3. Otherwise: δ* = clip(π / (γ × n), 0, 1) per math spec §15.4. The clip
     is necessary because raw π / (γ × n) can exceed 1 on near-diagonal
     inputs, which would violate the convex-combination property and produce
     a non-PSD estimate.

Implementation convention (per 06_PHASE_5_WEATHER_REGIME_CORRELATION.md):
  The math spec derives the formula in terms of a sample *covariance* matrix
  S. Zeus stores results as *correlation* matrices (covariance normalized by
  marginal standard deviations; entries ∈ [−1, 1]). The shrinkage formula,
  diagonal target, and δ* formula are identical in form for both conventions.
  ShrinkageEstimate fields use "correlation" naming to reflect implementation.
"""

from __future__ import annotations

import numpy as np
from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class ShrinkageEstimate:
    """Result of Ledoit-Wolf shrinkage estimation.

    Fields:
        sample_correlation: Raw sample correlation matrix S (shape p×p).
        shrunk_correlation: Shrunk estimate Σ_shrunk = (1-δ*)·S + δ*·D (shape p×p).
        intensity: Optimal shrinkage intensity δ* ∈ [0, 1].
        target_kind: Structured target used in shrinkage.
        n_observations: Number of observations (rows of residuals input).
        p_dimensions: Number of series (columns of residuals input).
    """

    sample_correlation: np.ndarray
    shrunk_correlation: np.ndarray
    intensity: float
    target_kind: Literal["diagonal", "identity", "constant_correlation", "single_factor"]
    n_observations: int
    p_dimensions: int


def ledoit_wolf_shrunk_correlation(
    residuals: np.ndarray,
    target: Literal["diagonal", "identity", "constant_correlation", "single_factor"] = "diagonal",
) -> ShrinkageEstimate:
    """Compute Ledoit-Wolf shrunk correlation matrix over a diagonal target.

    Implements math spec §15.4 formula verbatim:

        Σ_shrunk = (1 - δ*) · S + δ* · D
        δ* = π / (γ × n)   [clipped to [0, 1]; γ=0 and n=1 handled as early returns]

    Args:
        residuals: Input array of shape (n, p). Rows are observations
                   (e.g., ensemble residuals or daily settlement anomalies);
                   columns are series (e.g., city temperature series).
                   Must have n ≥ 1 and p ≥ 1.
        target: Shrinkage target family. Currently only "diagonal" is
                implemented (diagonal of S; retains variances, zeros
                off-diagonal). Other values reserved for future extension.

    Returns:
        ShrinkageEstimate with the shrunk correlation matrix and diagnostics.

    Raises:
        ValueError: If target is not "diagonal" (only diagonal supported).
        ValueError: If residuals has fewer than 1 row or 1 column.
    """
    if target != "diagonal":
        raise ValueError(
            f"Only target='diagonal' is currently implemented; got {target!r}."
        )

    arr = np.asarray(residuals, dtype=float)
    if arr.ndim != 2:
        raise ValueError(f"residuals must be 2-D (n, p); got shape {arr.shape}.")

    n, p = arr.shape
    if n < 1 or p < 1:
        raise ValueError(f"residuals must have at least 1 row and 1 column; got {n}×{p}.")

    # --- Compute sample correlation matrix S ---
    # For n=1, numpy std is 0 along the single row; we still compute the
    # sample covariance structure but handle the n=1 clamp case immediately.
    if n == 1:
        # Single observation: sample correlation is undefined; full shrinkage
        # to diagonal (identity in correlation space) is the only safe choice.
        S = np.eye(p)
        D = np.eye(p)
        shrunk = D  # δ* = 1.0 → Σ_shrunk = D
        return ShrinkageEstimate(
            sample_correlation=S,
            shrunk_correlation=shrunk,
            intensity=1.0,
            target_kind=target,
            n_observations=n,
            p_dimensions=p,
        )

    # Center each column (de-mean).
    demeaned = arr - arr.mean(axis=0, keepdims=True)

    # Sample covariance matrix (ddof=1 for unbiased; consistent with Ledoit-Wolf).
    S_cov = np.cov(demeaned, rowvar=False)  # shape (p, p)
    if p == 1:
        S_cov = S_cov.reshape(1, 1)

    # Convert to correlation matrix S via normalisation by marginal std devs.
    # std_diag[i] = sqrt(S_cov[i,i]); for p=1 or constant series, guard zero.
    std_diag = np.sqrt(np.diag(S_cov))
    std_outer = np.outer(std_diag, std_diag)
    # Where both series have non-zero variance, compute Pearson r; else 0.
    nonzero = std_outer > 0
    S = np.where(nonzero, S_cov / np.where(nonzero, std_outer, 1.0), 0.0)
    # Force exact diagonal = 1.0 (numerical noise can push slightly off).
    np.fill_diagonal(S, 1.0)

    # --- Diagonal target D ---
    # D retains variances (diagonal entries of S), zeros all off-diagonal.
    # In correlation space, diag(S) = 1.0, so D = I.
    D = np.diag(np.diag(S))  # shape (p, p)

    # --- γ: squared Frobenius distance ‖S - D‖²_F ---
    diff = S - D
    gamma = float(np.sum(diff ** 2))

    # Clamp guard: S already diagonal → γ = 0 → δ* = 0 (no shrinkage needed).
    if gamma == 0.0:
        return ShrinkageEstimate(
            sample_correlation=S,
            shrunk_correlation=S.copy(),
            intensity=0.0,
            target_kind=target,
            n_observations=n,
            p_dimensions=p,
        )

    # --- π: sum of asymptotic variances of sample covariance entries ---
    # Ledoit-Wolf 2004 analytic estimator (Isserlis theorem / Wick's lemma for
    # multivariate normal):
    #   Avar[sqrt(n) · S_{kl}] = S_{kk} · S_{ll} + S_{kl}²
    # Therefore:
    #   π = (1/n) × Σ_{k,l} (S_{kk} · S_{ll} + S_{kl}²)
    # For a correlation matrix S_{kk} = 1 for all k, so S_{kk}·S_{ll} = 1,
    # giving π = (1/n) × (p² + ‖S‖²_F).
    # This is the oracle π consistent with Ledoit & Wolf 2003/2004 under
    # the normality assumption; it requires only S and n.
    pi_hat = float(p * p + np.sum(S ** 2)) / n

    # --- δ* = clip(π / (γ × n), 0, 1) per math spec §15.4 ---
    delta_star = float(np.clip(pi_hat / (gamma * n), 0.0, 1.0))

    # --- Σ_shrunk = (1 - δ*) · S + δ* · D ---
    shrunk = (1.0 - delta_star) * S + delta_star * D

    return ShrinkageEstimate(
        sample_correlation=S,
        shrunk_correlation=shrunk,
        intensity=delta_star,
        target_kind=target,
        n_observations=n,
        p_dimensions=p,
    )
