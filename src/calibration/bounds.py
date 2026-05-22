# Created: 2026-05-22
# Last reused/audited: 2026-05-22
# Authority basis: docs/operations/task_2026-05-21_mainline_completion_authority/STRATEGY_TAXONOMY_DIRECTIVE.md §0, §5, §8
#                  + docs/reference/zeus_math_spec.md §6
"""Calibrated probability bounds p⁻ and p⁺ — shared by all stochastic Zeus strategies.

Algorithm: Split conformal calibration (Venn-Abers / conformal regression variant).

Choice rationale (conformal vs Platt bootstrap-percentile):
- Requirement (§8): Pr(Y=1 | p⁻ ≥ q) ≥ q — frequentist marginal-coverage guarantee.
- Platt bootstrap-percentile bounds (src/calibration/platt.py bootstrap_params) do NOT
  satisfy this property on finite samples; they provide parameter uncertainty, not
  outcome coverage.
- Split conformal provides exact finite-sample marginal coverage by construction:
  the nonconformity score for binary outcomes is the residual |Y − p̂|; the
  1−α quantile of calibration-set residuals gives the interval half-width.
- This is consistent with §5 "p⁻_i = inf{p_i : p_i in calibrated conformal set}".

Usage by strategies:
  - YES entry (center_buy, opening_inertia, imminent_open_capture, shoulder_buy):
      lo, _ = calibrated_bounds(p_hat, cal_p, cal_y, alpha)
      if lo - ask - phi(q, ask, fee_rate) > 0: enter
  - NO entry (center_sell NO side):
      _, hi = calibrated_bounds(p_hat, cal_p, cal_y, alpha)
      if 1 - hi - bid - phi(q, bid, fee_rate) > 0: enter
"""

from __future__ import annotations

import math
from typing import Sequence


def calibrated_bounds(
    p_hat: float,
    cal_p_hats: Sequence[float],
    cal_outcomes: Sequence[int],
    *,
    alpha: float = 0.10,
) -> tuple[float, float]:
    """Compute calibrated probability lower/upper bounds via split conformal.

    Given a point estimate p_hat and a held-out calibration set (cal_p_hats,
    cal_outcomes), returns (p⁻, p⁺) such that:
      - p⁻ ≤ p_hat ≤ p⁺  (ordering invariant)
      - Pr(Y=1 | p⁻ ≥ q) ≥ q approximately, by conformal construction

    Method — split conformal for binary regression:
      1. Compute calibration nonconformity scores: s_i = |y_i − p̂_i|.
      2. Compute the (1−alpha)-quantile q_alpha of {s_i}.
      3. Lower bound: max(0, p_hat − q_alpha).
         Upper bound: min(1, p_hat + q_alpha).

    This is the Shafer-Vovk split-conformal interval adapted to binary outcomes.
    Coverage guarantee: Pr(Y ∈ [p⁻, p⁺]) ≥ 1 − alpha for exchangeable data.

    Args:
        p_hat: Point probability estimate in [0, 1].
        cal_p_hats: Calibration set point estimates (parallel to cal_outcomes).
        cal_outcomes: Binary outcomes {0, 1} for calibration set.
        alpha: Miscoverage rate. Default 0.10 → 90% coverage intervals.
            Larger alpha → tighter (more aggressive) bounds.
            Smaller alpha → wider (more conservative) bounds.

    Returns:
        (p_lo, p_hi): lower and upper calibrated bounds, both in [0, 1],
            satisfying p_lo ≤ p_hat ≤ p_hi.

    Raises:
        ValueError: if alpha not in (0, 1), inputs are empty, p_hat or cal_p_hats
            are outside [0, 1] or non-finite, or cal_outcomes contain values outside {0, 1}.
    """
    import math as _math
    if not (0.0 < alpha < 1.0):
        raise ValueError(f"alpha must be in (0, 1), got {alpha}")
    if len(cal_p_hats) == 0:
        raise ValueError("cal_p_hats must be non-empty")
    if len(cal_p_hats) != len(cal_outcomes):
        raise ValueError(
            f"cal_p_hats length {len(cal_p_hats)} != cal_outcomes length {len(cal_outcomes)}"
        )
    p_hat_f = float(p_hat)
    if not _math.isfinite(p_hat_f) or not (0.0 <= p_hat_f <= 1.0):
        raise ValueError(f"p_hat must be finite and in [0, 1], got {p_hat}")
    for i, cp in enumerate(cal_p_hats):
        cp_f = float(cp)
        if not _math.isfinite(cp_f) or not (0.0 <= cp_f <= 1.0):
            raise ValueError(f"cal_p_hats[{i}]={cp} is not finite or not in [0, 1]")
    for i, y in enumerate(cal_outcomes):
        if y not in (0, 1, 0.0, 1.0):
            raise ValueError(f"cal_outcomes[{i}]={y} is not in {{0, 1}}")

    # Step 1: nonconformity scores on calibration set
    scores = [abs(float(y) - float(p)) for p, y in zip(cal_p_hats, cal_outcomes)]

    # Step 2: (1−alpha)-quantile of calibration scores
    # Per conformal protocol: q_alpha = ceil((n+1)*(1-alpha))/n quantile.
    # For simplicity and numerical correctness, use sorted-index approach.
    n = len(scores)
    sorted_scores = sorted(scores)
    # Index of the (1-alpha) quantile: ceil((n+1)*(1-alpha)) - 1 (0-based), capped at n-1
    q_idx = math.ceil((n + 1) * (1.0 - alpha)) - 1
    q_idx = max(0, min(q_idx, n - 1))
    q_alpha = sorted_scores[q_idx]

    # Step 3: interval [p_hat − q_alpha, p_hat + q_alpha] clipped to [0, 1]
    p_lo = max(0.0, p_hat - q_alpha)
    p_hi = min(1.0, p_hat + q_alpha)

    return p_lo, p_hi
