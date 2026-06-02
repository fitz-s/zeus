# Created: 2026-06-02
# Last reused or audited: 2026-06-02
# Authority basis: EMOS-CI shadow extension spec (/tmp/design_emos_ci.md §1/§4);
#   trade_score.py:48-52 robust_edge formula; operator CI-honesty law.
"""EMOS-CI shadow helpers: robust_edge formula + k_cov coverage solve.

Used by both _write_emos_shadow_ledger (event_reactor_adapter.py) and
scripts/score_emos_forward.py.

Public API:
  compute_robust_edge(q_posterior, q_5pct, cost, penalty=0.01) -> float
      Replication of trade_score.py:48-52 edge formula (NOT multiplied by
      p_fill_lcb — the clearing test is edge > 0, not score > 0).

  solve_k_cov(pit) -> float
      Smallest k >= 1.0 such that the PIT-inflated cov90 falls in [0.86, 0.94].
      Clamps to 1.0 when EMOS already covers or is over-dispersed, or n<20.

  _coverage_at_k(pit, k) -> float
      Helper: empirical cov90 of the central-90% band when sigma is inflated by k.
      PIT values are for k=1; at k>1 the effective PI thresholds shrink inward:
      PI_low = Φ(-1/k * 1.645) ... same as computing Φ(z/k) thresholds.
"""
from __future__ import annotations

import numpy as np
from scipy.stats import norm as _scipy_norm

# Constants matching validate_analytic_ci_coverage.py
_PI_LOW = 0.05
_PI_HIGH = 0.95
_COV90_LOW = 0.86
_COV90_HIGH = 0.94
_MIN_N = 20           # below this → k_cov = 1.0 (insufficient data)
_PENALTY = 0.01       # hard-coded in _robust_trade_score_from_generated_inputs (adapter:4525-4526)


def compute_robust_edge(
    q_posterior: float,
    q_5pct: float,
    cost: float,
    penalty: float = _PENALTY,
) -> float:
    """Replication of trade_score.py:48-52 edge formula (before p_fill_lcb multiplication).

    edge_bound = min(q_5pct - cost - penalty, q_posterior - cost - penalty)

    This is the value whose sign determines whether the trade clears (>0) or dies (<=0).
    The p_fill_lcb multiplier in trade_score.py does not affect the clearing test, so we
    record the raw edge_bound to make the 'would_clear' boolean unambiguous.

    Args:
        q_posterior: Live q posterior (q_by_condition[cond], post-evaluate_live_bins).
        q_5pct:      Live q LCB in probability space (lcb_by_direction[(cond,dir)]).
        cost:        Executable ask cost (native_costs[(cond,dir)][1].value).
        penalty:     Trade penalty; mirrors adapter:4525-4526 literal 0.01.

    Returns:
        float: edge_bound (may be negative — negative means trade does not clear).
    """
    return min(q_5pct - cost - penalty, q_posterior - cost - penalty)


def _coverage_at_k(pit: np.ndarray, k: float) -> float:
    """Empirical cov90 of the central-90% band when EMOS sigma is inflated by k.

    Under the inflated predictive N(mu, k*sigma), the PIT of each observation y is:
        PIT_k = Phi((y - mu) / (k*sigma)) = Phi(Phi_inv(PIT_1) / k)
    where PIT_1 = Phi((y-mu)/sigma) is the original (k=1) PIT stored in the ledger.

    Coverage = fraction of PIT_k values that fall in the central-90% band [0.05, 0.95].
    Inflating k (wider sigma) pulls extreme PITs toward 0.5, increasing coverage.

    Args:
        pit: 1-D array of PIT values (floats in [0,1]) for k=1 (original predictive).
        k:   Sigma inflation factor (>=1 for our use; >0 for generality).

    Returns:
        float: empirical coverage fraction under inflated sigma.
    """
    if k <= 0.0:
        raise ValueError(f"k must be positive, got {k}")
    if k == 1.0:
        return float(np.mean((pit >= _PI_LOW) & (pit <= _PI_HIGH)))
    # Transform original PIT to new PIT under k-inflated sigma
    arr = np.asarray(pit, dtype=float)
    arr_clipped = np.clip(arr, 1e-9, 1.0 - 1e-9)
    pit_k = _scipy_norm.cdf(_scipy_norm.ppf(arr_clipped) / k)
    return float(np.mean((pit_k >= _PI_LOW) & (pit_k <= _PI_HIGH)))


def solve_k_cov(pit: np.ndarray) -> float:
    """Smallest k >= 1.0 such that cov90(k) in [COV90_LOW=0.86, COV90_HIGH=0.94].

    Clamp rules (operator CI-honesty law):
      - n < MIN_N (20): return 1.0 (insufficient data, record as-is).
      - cov90(k=1) >= COV90_LOW (already covers or over-disperses): return 1.0.
        We NEVER tighten sigma (k < 1 is forbidden — could under-cover).
      - cov90(k=1) < COV90_LOW (under-covered): binary-search k in [1.0, 10.0]
        for the smallest k where cov90(k) enters [COV90_LOW, COV90_HIGH].
        If no such k is found in range, return k=10.0 (the search bound).

    Args:
        pit: 1-D array of PIT values in [0,1] for k=1 (the raw EMOS predictive).

    Returns:
        float: k_cov >= 1.0.
    """
    arr = np.asarray(pit, dtype=float)
    n = int(arr.size)
    if n < _MIN_N:
        return 1.0

    cov_k1 = _coverage_at_k(arr, 1.0)
    if cov_k1 >= _COV90_LOW:
        # Already covering (or over-dispersed) — do not tighten; return 1.0
        return 1.0

    # Under-covered: binary search for smallest k in [1.0, 10.0]
    k_lo, k_hi = 1.0, 10.0
    # Verify that k_hi is enough to cover; if not, return k_hi
    if _coverage_at_k(arr, k_hi) < _COV90_LOW:
        return k_hi

    # Binary search: find smallest k where cov90(k) >= COV90_LOW
    for _ in range(40):  # 40 iterations → precision < 10.0 / 2^40 ≈ negligible
        k_mid = (k_lo + k_hi) / 2.0
        cov_mid = _coverage_at_k(arr, k_mid)
        if cov_mid >= _COV90_LOW:
            k_hi = k_mid
        else:
            k_lo = k_mid
        if k_hi - k_lo < 1e-6:
            break

    return float(k_hi)
