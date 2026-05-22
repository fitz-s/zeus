# Lifecycle: created=2026-05-22; last_reviewed=2026-05-22; last_reused=never
# Purpose: Relationship tests for split-conformal calibrated_bounds() — coverage guarantee §5/§8
# Reuse: synthetic data only; no external dependencies; safe to run in isolation
"""Relationship tests for src/calibration/bounds.py — calibrated probability bounds.

§0: Class 3 (calibrated statistical edge) MUST use calibrated bound, not raw posterior.
    p⁻ − a − phi > 0 for YES; 1 − p⁺ − b − phi > 0 for NO.
§5 (center_buy): p⁻_i = inf{ p_i : p_i in calibrated confidence/conformal set }.
§8 (shoulder_buy): conformal calibration: Pr(Y=1 | p⁻_u ≥ q) ≥ q.

Split conformal calibration is the chosen algorithm because:
- The required test Pr(Y=1 | p⁻ ≥ q) ≥ q is a frequentist marginal-coverage guarantee.
- Platt bootstrap-percentile bounds do NOT satisfy this property on finite samples.
- Split conformal provides exact finite-sample coverage by construction.

These tests are RELATIONSHIP tests, written BEFORE the implementation (TDD required).
"""

from __future__ import annotations

import random

import pytest


# ---------------------------------------------------------------------------
# Helpers — synthetic calibrated data generation
# ---------------------------------------------------------------------------

def _make_synthetic_sample(n: int = 500, seed: int = 42) -> tuple[list[float], list[int]]:
    """Generate synthetic (p_hat, outcome) pairs from a calibrated model.

    The data-generating process is: p_hat ~ Uniform(0.05, 0.95); Y ~ Bernoulli(p_hat).
    By construction, the model is perfectly calibrated: E[Y | p_hat] = p_hat.
    """
    rng = random.Random(seed)
    p_hats = [rng.uniform(0.05, 0.95) for _ in range(n)]
    outcomes = [1 if rng.random() < p else 0 for p in p_hats]
    return p_hats, outcomes


# ---------------------------------------------------------------------------
# Test 1: p⁻ ≤ p̂ ≤ p⁺ always holds (ordering invariant)
# ---------------------------------------------------------------------------

def test_bounds_ordering() -> None:
    """p⁻ ≤ p̂ ≤ p⁺ for all calibration inputs.

    Relationship: bounds must bracket the point estimate. A lower bound above
    p̂ would make every stochastic entry condition too aggressive; an upper bound
    below p̂ would make every NO condition too aggressive.
    """
    from src.calibration.bounds import calibrated_bounds

    p_hats, outcomes = _make_synthetic_sample(n=400, seed=1)
    cal_p_hats = p_hats[:200]
    cal_outcomes = outcomes[:200]
    test_p_hats = p_hats[200:]

    for p_hat in test_p_hats:
        lo, hi = calibrated_bounds(p_hat, cal_p_hats, cal_outcomes, alpha=0.1)
        assert lo <= p_hat, f"p⁻={lo} > p̂={p_hat}; lower bound must not exceed estimate"
        assert p_hat <= hi, f"p̂={p_hat} > p⁺={hi}; upper bound must not fall below estimate"
        assert lo <= hi, f"p⁻={lo} > p⁺={hi}; lower bound must not exceed upper bound"


# ---------------------------------------------------------------------------
# Test 2: Conformal marginal coverage Pr(Y=1 | p⁻ ≥ q) ≥ q
# ---------------------------------------------------------------------------

def test_conformal_coverage() -> None:
    """Pr(Y=1 | p⁻ ≥ q) ≥ q on a synthetic calibrated sample.

    Relationship: §8 requires conformal calibration guarantee. For p⁻ ≥ q,
    the empirical win rate must be at least q (the threshold). This is the
    defining property of conformal bounds, not achievable with bootstrap-percentile.

    Coverage check is done at several threshold levels q ∈ {0.4, 0.5, 0.6, 0.7}.
    We use a large synthetic sample for reliable estimates; tolerance = 0.03 to
    account for finite-sample variation.
    """
    from src.calibration.bounds import calibrated_bounds

    TOLERANCE = 0.03  # finite-sample slack

    p_hats, outcomes = _make_synthetic_sample(n=1000, seed=7)
    # Split 60/40: calibration set / test set
    n_cal = 600
    cal_p_hats = p_hats[:n_cal]
    cal_outcomes = outcomes[:n_cal]
    test_p_hats = p_hats[n_cal:]
    test_outcomes = outcomes[n_cal:]

    # Compute lower bounds for all test points
    lower_bounds = [
        calibrated_bounds(p, cal_p_hats, cal_outcomes, alpha=0.1)[0]
        for p in test_p_hats
    ]

    for q in [0.40, 0.50, 0.60, 0.70]:
        indices = [i for i, lb in enumerate(lower_bounds) if lb >= q]
        if len(indices) < 5:
            # Not enough data at this threshold to make a meaningful test
            continue
        empirical_rate = sum(test_outcomes[i] for i in indices) / len(indices)
        assert empirical_rate >= q - TOLERANCE, (
            f"Coverage failure at q={q}: empirical win rate = {empirical_rate:.3f} "
            f"< {q - TOLERANCE:.3f}. Conformal bound must satisfy Pr(Y=1 | p⁻≥q) ≥ q."
        )


# ---------------------------------------------------------------------------
# Test 3: Complement symmetry — lower bound for NO = 1 − upper bound for YES
# ---------------------------------------------------------------------------

def test_complement_symmetry() -> None:
    """1 − p⁺(p̂) ≈ p⁻(1 − p̂) — NO bound is the complement of YES bound.

    Relationship: §0 states YES entry uses p⁻, NO entry uses 1 − p⁺. These must
    be symmetric: buying NO on bin i is equivalent to buying YES on the complement
    probability (1 − p_i). The bounds must reflect this.
    """
    from src.calibration.bounds import calibrated_bounds

    p_hats, outcomes = _make_synthetic_sample(n=400, seed=99)
    cal_p_hats = p_hats[:200]
    cal_outcomes = outcomes[:200]

    # For a calibrated sample, complement outcomes
    cal_outcomes_complement = [1 - y for y in cal_outcomes]

    for p_hat in [0.2, 0.35, 0.5, 0.65, 0.8]:
        lo_yes, hi_yes = calibrated_bounds(p_hat, cal_p_hats, cal_outcomes, alpha=0.1)
        # p⁻ for NO side: use (1 - p_hat) against complement outcomes
        lo_no, _ = calibrated_bounds(
            1.0 - p_hat, [1.0 - p for p in cal_p_hats], cal_outcomes_complement, alpha=0.1
        )
        # 1 − p⁺_YES ≈ p⁻_NO: symmetric complement
        assert abs((1.0 - hi_yes) - lo_no) < 0.05, (
            f"At p̂={p_hat}: 1 − p⁺={1.0 - hi_yes:.4f} vs p⁻_NO={lo_no:.4f}; "
            "complement symmetry violated"
        )


# ---------------------------------------------------------------------------
# Test 4: Wider alpha → narrower confidence (more aggressive bound)
# ---------------------------------------------------------------------------

def test_alpha_width_monotone() -> None:
    """Larger alpha → tighter interval (less conservative bound).

    Relationship: alpha=0.1 (90% coverage) must give a narrower interval than
    alpha=0.05 (95% coverage). More confidence = wider interval = more conservative.
    """
    from src.calibration.bounds import calibrated_bounds

    p_hats, outcomes = _make_synthetic_sample(n=400, seed=3)
    cal_p_hats = p_hats[:200]
    cal_outcomes = outcomes[:200]

    p_hat = 0.6
    lo_tight, hi_tight = calibrated_bounds(p_hat, cal_p_hats, cal_outcomes, alpha=0.20)
    lo_wide, hi_wide = calibrated_bounds(p_hat, cal_p_hats, cal_outcomes, alpha=0.05)

    width_tight = hi_tight - lo_tight
    width_wide = hi_wide - lo_wide

    assert width_tight <= width_wide, (
        f"alpha=0.20 interval width {width_tight:.4f} should be <= "
        f"alpha=0.05 width {width_wide:.4f}"
    )


# ---------------------------------------------------------------------------
# Test 5: calibrated_bounds returns (float, float) in [0, 1]
# ---------------------------------------------------------------------------

def test_bounds_range() -> None:
    """Both bounds are in [0, 1]."""
    from src.calibration.bounds import calibrated_bounds

    p_hats, outcomes = _make_synthetic_sample(n=200, seed=5)

    for p_hat in [0.1, 0.3, 0.5, 0.7, 0.9]:
        lo, hi = calibrated_bounds(p_hat, p_hats, outcomes, alpha=0.1)
        assert 0.0 <= lo <= 1.0, f"lower bound {lo} outside [0, 1]"
        assert 0.0 <= hi <= 1.0, f"upper bound {hi} outside [0, 1]"
