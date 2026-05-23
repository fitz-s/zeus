# Created: 2026-05-22
# Last reused or audited: 2026-05-22
# Authority basis: docs/operations/task_2026-05-21_mainline_completion_authority/STRATEGY_TAXONOMY_DIRECTIVE.md §5
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
"""

from __future__ import annotations

import math
from collections.abc import Sequence


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
