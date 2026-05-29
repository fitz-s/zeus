# Created: 2026-05-22
# Last reused or audited: 2026-05-29
# Authority basis: docs/operations/task_2026-05-21_mainline_completion_authority/STRATEGY_TAXONOMY_DIRECTIVE.md §5
#   + TRIBUNAL replay redesign §4/§5 (categorical group scoring: p_winner, RPS,
#   winner_rank, group integrity). A weather bin market is ONE categorical
#   distribution, not K independent binary events — these rules score the
#   full probability vector against the single winning bin.
"""Multinomial proper-scoring rules for calibration backtest.

§5 (center_buy): Add a multinomial proper-scoring backtest to validate
calibration quality. Two rules:
  LogScore(p, winner) = −log(p[winner])        (natural log, nats)
  Brier(p, winner)    = Σ_k (p_k − 1[k==winner])²

These are strictly proper scoring rules: the expected score under the true
distribution is minimized (LogScore) or maximized (Brier minimized) by
reporting the true probability. A well-calibrated model should produce
low expected log-loss and low expected Brier score.

Usage in backtest:
    ls = log_score(posteriors, winner_bin_index)
    bs = brier_score(posteriors, winner_bin_index)

TRIBUNAL group-scoring layer (added 2026-05-29): the functions below take the
SAME (p, winner) shape but expose the categorical-market vocabulary the replay
redesign requires. ``p_winner`` is the probability mass Zeus assigned at decision
time to the bin that actually settled — the primary forecast-quality question.
``ranked_probability_score`` exploits the fact that temperature bins are ORDERED
(distance between predicted and actual bin matters); the binary Brier/log rules
do not. ``validate_probability_group`` is the group-integrity gate: a vector that
is not a valid categorical distribution must never be scored as one.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

# Default tolerance for the categorical normalization check. A forecast vector
# whose mass sums outside [1 - tol, 1 + tol] is not a valid distribution.
PROBABILITY_SUM_TOLERANCE = 1e-6

# Clamp floor for log-loss so a winning bin that received exactly 0.0 mass yields
# a large-but-finite penalty instead of +inf. Mirrors sklearn's log_loss eps.
LOG_LOSS_EPS = 1e-12


def log_score(p: Sequence[float], winner: int) -> float:
    """Logarithmic scoring rule for a multinomial event.

    LogScore = −log(p[winner])

    Args:
        p: Probability vector for K bins. Must sum to ≈ 1. Each p_k ∈ (0, 1].
        winner: Index of the bin that resolved YES (0-indexed).

    Returns:
        Non-negative float in nats. Lower is better (strictly proper).

    Raises:
        IndexError: if winner is out of range.
        ValueError: if p[winner] ≤ 0 (undefined log).
    """
    pw = p[winner]
    if pw <= 0.0:
        raise ValueError(
            f"log_score: p[winner={winner}]={pw} ≤ 0; logarithm undefined."
        )
    return -math.log(pw)


def brier_score(p: Sequence[float], winner: int) -> float:
    """Brier scoring rule for a multinomial event.

    Brier = Σ_k (p_k − 1[k == winner])²

    Args:
        p: Probability vector for K bins. Must sum to ≈ 1. Each p_k ∈ [0, 1].
        winner: Index of the bin that resolved YES (0-indexed).

    Returns:
        Non-negative float in [0, 2]. Lower is better (strictly proper).

    Raises:
        IndexError: if winner is out of range.
    """
    K = len(p)
    total = 0.0
    for k in range(K):
        indicator = 1.0 if k == winner else 0.0
        diff = p[k] - indicator
        total += diff * diff
    # Guard: winner must be in range (triggers IndexError on p[winner] for free)
    _ = p[winner]
    return total


# ─────────────────────────────────────────────────────────────────────────────
# TRIBUNAL categorical group-scoring layer (2026-05-29)
#
# A weather bin market is one categorical distribution over K mutually-exclusive,
# collectively-exhaustive ordered bins. These functions score the WHOLE vector
# against the single settled bin. They share the (p, winner) shape with the proper
# rules above; ``winner`` is always the 0-indexed position of the settled bin in
# the SAME ordered grid the probability vector is laid out on.
# ─────────────────────────────────────────────────────────────────────────────


class ProbabilityGroupError(ValueError):
    """Raised when a probability vector is not a valid categorical distribution.

    A vector that does not pass group integrity (non-finite, negative, empty, or
    mass not summing to ~1) must NOT be scored as a categorical forecast — doing
    so would launder a malformed input into a numerically-valid-looking metric.
    """


def validate_probability_group(
    p: Sequence[float], *, tol: float = PROBABILITY_SUM_TOLERANCE
) -> None:
    """Group-integrity gate for a categorical probability vector.

    A valid group is: non-empty, every entry finite and >= 0, and total mass in
    ``[1 - tol, 1 + tol]``. Mutual-exclusivity / exhaustiveness of the BINS and
    "exactly one winner under normal policy" are settlement-side invariants
    (see ``src.contracts.settlement_object``), not vector-side — they are checked
    where the bin grid and settlement value are known, not here.

    Raises:
        ProbabilityGroupError: on any violation, with a specific message.
    """
    n = len(p)
    if n == 0:
        raise ProbabilityGroupError("probability group is empty (K=0)")
    total = 0.0
    for i, pi in enumerate(p):
        if not math.isfinite(pi):
            raise ProbabilityGroupError(
                f"probability group has non-finite entry p[{i}]={pi!r}"
            )
        if pi < 0.0:
            raise ProbabilityGroupError(
                f"probability group has negative entry p[{i}]={pi!r}"
            )
        total += pi
    if abs(total - 1.0) > tol:
        raise ProbabilityGroupError(
            f"probability group sums to {total!r}, outside [1±{tol}]; "
            f"not a normalized categorical distribution"
        )


def p_winner(p: Sequence[float], winner: int) -> float:
    """Probability mass assigned to the bin that settled YES.

    This is the primary forecast-quality scalar for a categorical market:
    "at decision time, how much mass did Zeus put on the eventual winner?"
    """
    return float(p[winner])


def categorical_log_loss(
    p: Sequence[float], winner: int, *, eps: float = LOG_LOSS_EPS
) -> float:
    """−log(p[winner]) with the winner mass clamped to ``[eps, 1]``.

    The clamped variant (vs ``log_score``) keeps replay robust to a winning bin
    that received exactly 0.0 mass — a real occurrence for tail outcomes — by
    emitting a large finite penalty instead of raising / +inf. Use ``log_score``
    when a hard 0-mass winner SHOULD be an error; use this for sweep aggregation.
    """
    pw = min(max(float(p[winner]), eps), 1.0)
    return -math.log(pw)


def multiclass_brier(p: Sequence[float], winner: int) -> float:
    """Multiclass Brier score Σ_k (p_k − 1[k==winner])². Alias for ``brier_score``
    under the categorical vocabulary; kept distinct so replay call sites read in
    group-scoring terms.
    """
    return brier_score(p, winner)


def ranked_probability_score(p: Sequence[float], winner: int) -> float:
    """Ordered Ranked Probability Score for an ORDERED bin grid.

    RPS = Σ_{k=1}^{K-1} (CDF_p(k) − CDF_y(k))²

    where CDF_p is the cumulative forecast mass and CDF_y is the cumulative
    one-hot (a step from 0→1 at the winner). Unlike Brier/log-loss, RPS rewards
    putting mass NEAR the winning bin: predicting an adjacent bin scores far
    better than predicting a distant one. This is the right primary metric for a
    monotone temperature ladder. Raw (un-normalized) per TRIBUNAL §5; divide by
    (K−1) externally if a [0,1]-scaled comparison across grids is needed.

    Requires ``winner`` in range; does not itself enforce group integrity — call
    ``validate_probability_group`` first when the vector is untrusted.
    """
    K = len(p)
    if not (0 <= winner < K):
        raise IndexError(f"winner={winner} out of range for K={K}")
    cum_p = 0.0
    cum_y = 0.0
    total = 0.0
    # Sum over the K-1 thresholds (the last cumulative pair is always (1,1)).
    for k in range(K - 1):
        cum_p += p[k]
        if k == winner:
            cum_y += 1.0
        diff = cum_p - cum_y
        total += diff * diff
    return total


def winner_rank(p: Sequence[float], winner: int) -> int:
    """1-based rank of the winning bin by probability (1 = argmax).

    rank = 1 + #{ j : p_j > p_winner }. Ties do NOT inflate the rank (a bin tied
    with the winner is not counted as strictly above it), so a winner sharing the
    top mass with others still ranks 1.
    """
    pw = float(p[winner])
    strictly_above = sum(1 for pj in p if float(pj) > pw)
    return 1 + strictly_above


def reciprocal_rank(p: Sequence[float], winner: int) -> float:
    """1 / winner_rank — the mean of this over a sweep is Mean Reciprocal Rank."""
    return 1.0 / winner_rank(p, winner)


def top_k_hit(p: Sequence[float], winner: int, k: int) -> bool:
    """True iff the winning bin is among the k highest-probability bins.

    Uses strict-greater counting so ties are treated generously (a winner tied at
    the k-th mass is a hit). ``k`` must be >= 1.
    """
    if k < 1:
        raise ValueError(f"top_k_hit: k must be >= 1, got {k}")
    return winner_rank(p, winner) <= k
