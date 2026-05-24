"""Bounded Bayesian factors for EDLI redemption."""

from __future__ import annotations

import math


def capped_likelihood_ratio(log_likelihood_ratio: float, *, cap: float) -> float:
    if cap <= 0:
        raise ValueError("cap must be positive")
    bounded = max(-cap, min(cap, float(log_likelihood_ratio)))
    return math.exp(bounded)
