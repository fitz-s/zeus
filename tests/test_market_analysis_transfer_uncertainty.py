# Created: 2026-05-04
# Last reused or audited: 2026-05-04
# Authority basis: architecture/math_defects_2_3_2_4_3_1_design_2026-05-05.md Phase α Issue 2.4

"""Tests for Phase α transfer-uncertainty layer (Issue 2.4).

Covers:
1. compute_transfer_logit_sigma helper: monotonicity, clamping, NaN guard
2. MarketAnalysis(transfer_logit_sigma=0.0) byte-identical to default (RNG no-op path)
3. CI widens monotonically with increasing transfer_logit_sigma
4. p_value near 0.5 preserved at zero-edge regardless of sigma
"""

import numpy as np
import pytest

from src.calibration.platt import ExtendedPlattCalibrator
from src.strategy.market_analysis import MarketAnalysis, compute_transfer_logit_sigma
from src.types import Bin


# ---------------------------------------------------------------------------
# Shared fixture helpers
# ---------------------------------------------------------------------------

def _make_bins() -> list[Bin]:
    return [
        Bin(low=None, high=32, label="32 or below", unit="F"),
        Bin(low=33, high=34, label="33-34", unit="F"),
        Bin(low=35, high=36, label="35-36", unit="F"),
        Bin(low=37, high=38, label="37-38", unit="F"),
        Bin(low=39, high=40, label="39-40", unit="F"),
        Bin(low=41, high=42, label="41-42", unit="F"),
        Bin(low=43, high=44, label="43-44", unit="F"),
        Bin(low=45, high=46, label="45-46", unit="F"),
        Bin(low=47, high=48, label="47-48", unit="F"),
        Bin(low=49, high=50, label="49-50", unit="F"),
        Bin(low=51, high=None, label="51 or higher", unit="F"),
    ]


def _make_fitted_calibrator(n_bootstrap: int = 10) -> ExtendedPlattCalibrator:
    """Minimal fitted Platt calibrator so has_platt=True inside bootstrap."""
    cal = ExtendedPlattCalibrator()
    rng = np.random.default_rng(0)
    n = 80
    p_raw = rng.uniform(0.05, 0.95, n)
    lead_days = np.full(n, 3.0)
    outcomes = (rng.uniform(size=n) < p_raw).astype(float)
    cal.fit(p_raw, lead_days, outcomes, n_bootstrap=n_bootstrap, rng=np.random.default_rng(1))
    assert cal.fitted and len(cal.bootstrap_params) >= 1
    return cal


def _make_ma(transfer_logit_sigma=None, *, rng_seed=42, calibrator=None) -> MarketAnalysis:
    """Construct a MarketAnalysis with a mispriced bin 5 (model=0.55, market=0.30)."""
    bins = _make_bins()
    n_bins = len(bins)
    # Put most mass on bin 5 (idx=5, "41-42"), calibrated above market
    p = np.array([0.04, 0.04, 0.05, 0.07, 0.10, 0.25, 0.18, 0.12, 0.08, 0.05, 0.02], dtype=float)
    p_market = np.array([0.08, 0.06, 0.07, 0.09, 0.12, 0.18, 0.14, 0.10, 0.08, 0.05, 0.03], dtype=float)
    # Normalise both to sum=1
    p = p / p.sum()
    p_market = p_market / p_market.sum()
    # Member maxes clustered around 42°F to match expected max bin
    members = np.full(51, 42.0, dtype=float) + np.linspace(-1.0, 1.0, 51)

    kwargs = dict(
        p_raw=p,
        p_cal=p,
        p_market=p_market,
        alpha=0.3,
        bins=bins,
        member_maxes=members,
        unit="F",
        rng_seed=rng_seed,
    )
    if calibrator is not None:
        kwargs["calibrator"] = calibrator
    if transfer_logit_sigma is not None:
        kwargs["transfer_logit_sigma"] = transfer_logit_sigma
    return MarketAnalysis(**kwargs)


# ---------------------------------------------------------------------------
# Helper function tests
# ---------------------------------------------------------------------------

def test_helper_brier_diff_to_sigma_monotonic():
    s1 = compute_transfer_logit_sigma(0.001, 4.0)
    s2 = compute_transfer_logit_sigma(0.005, 4.0)
    s3 = compute_transfer_logit_sigma(0.01, 4.0)
    assert s1 < s2 < s3


def test_helper_clamps_negative_brier_diff_to_zero():
    assert compute_transfer_logit_sigma(-0.001, 4.0) == 0.0


def test_helper_handles_nan():
    assert compute_transfer_logit_sigma(float("nan"), 4.0) == 0.0


def test_helper_handles_none():
    assert compute_transfer_logit_sigma(None, 4.0) == 0.0


def test_helper_zero_brier_diff_returns_zero():
    assert compute_transfer_logit_sigma(0.0, 4.0) == 0.0


# ---------------------------------------------------------------------------
# RNG no-op / byte-identical default
# ---------------------------------------------------------------------------

def test_transfer_sigma_zero_matches_legacy_3layer():
    """transfer_logit_sigma=0.0 must produce byte-identical CI/p_value as default.

    This verifies the guarded `if self._transfer_logit_sigma > 0.0` path:
    the rng.normal(0, 0) call is NEVER made, so RNG state is not consumed.
    """
    n_bootstrap = 200
    bin_idx = 5  # "41-42" bin

    ma_default = _make_ma()                         # no kwarg → default 0.0
    ma_explicit = _make_ma(transfer_logit_sigma=0.0)  # explicit 0.0

    ci_lo_d, ci_hi_d, pv_d = ma_default._bootstrap_bin(bin_idx, n_bootstrap)
    ci_lo_e, ci_hi_e, pv_e = ma_explicit._bootstrap_bin(bin_idx, n_bootstrap)

    assert ci_lo_d == ci_lo_e, f"ci_lower diverged: {ci_lo_d} vs {ci_lo_e}"
    assert ci_hi_d == ci_hi_e, f"ci_upper diverged: {ci_hi_d} vs {ci_hi_e}"
    assert pv_d == pv_e, f"p_value diverged: {pv_d} vs {pv_e}"


# ---------------------------------------------------------------------------
# CI monotonically widens with sigma
# ---------------------------------------------------------------------------

def test_transfer_sigma_widens_ci_monotonically():
    """Larger transfer_logit_sigma must produce wider CI (requires Platt branch).

    transfer_logit_sigma only fires inside the `if has_platt:` block — the
    additive noise is in the logit-space z computation. A fitted calibrator
    is required for has_platt=True.
    """
    n_bootstrap = 500
    bin_idx = 5
    cal = _make_fitted_calibrator()

    ci_lo_0, ci_hi_0, _ = _make_ma(transfer_logit_sigma=0.0, calibrator=cal)._bootstrap_bin(bin_idx, n_bootstrap)
    ci_lo_1, ci_hi_1, _ = _make_ma(transfer_logit_sigma=0.1, calibrator=cal)._bootstrap_bin(bin_idx, n_bootstrap)
    ci_lo_2, ci_hi_2, _ = _make_ma(transfer_logit_sigma=0.2, calibrator=cal)._bootstrap_bin(bin_idx, n_bootstrap)

    width_0 = ci_hi_0 - ci_lo_0
    width_1 = ci_hi_1 - ci_lo_1
    width_2 = ci_hi_2 - ci_lo_2

    assert width_2 > width_1, f"σ=0.2 width {width_2:.4f} not > σ=0.1 width {width_1:.4f}"
    assert width_1 > width_0, f"σ=0.1 width {width_1:.4f} not > σ=0.0 width {width_0:.4f}"


# ---------------------------------------------------------------------------
# p_value near 0.5 at zero-edge
# ---------------------------------------------------------------------------

def test_transfer_sigma_preserves_p_value_sign_at_zero_edge():
    """When model == market on a bin, p_value should be near 0.5 regardless of sigma.

    Members are split half below / half above the bin boundary so that resampling
    produces p_posterior ≈ 0.5, matching p_market = 0.5 → edges ≈ 0 → p_value ≈ 0.5.
    """
    bins = [
        Bin(low=None, high=32, label="32 or below", unit="F"),
        Bin(low=33, high=None, label="33 or higher", unit="F"),
    ]
    p = np.array([0.50, 0.50])
    p_market = np.array([0.50, 0.50])
    # 26 members at 30°F (settle to bin 0) + 25 at 35°F (settle to bin 1) → ~50/50 split
    members = np.concatenate([np.full(26, 30.0), np.full(25, 35.0)])

    epsilon = 0.15  # generous tolerance — bootstrap variance at exact zero

    for sigma in (0.0, 0.1, 0.3):
        ma = MarketAnalysis(
            p_raw=p,
            p_cal=p,
            p_market=p_market,
            alpha=0.0,
            bins=bins,
            member_maxes=members,
            unit="F",
            rng_seed=7,
            transfer_logit_sigma=sigma,
        )
        _, _, pv = ma._bootstrap_bin(0, 300)
        assert abs(pv - 0.5) < epsilon, (
            f"sigma={sigma}: p_value={pv:.3f} too far from 0.5 (epsilon={epsilon})"
        )
