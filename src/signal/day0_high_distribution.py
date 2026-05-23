# Created: 2026-05-22
# Last reused or audited: 2026-05-22
# Authority basis: docs/operations/P0_FORECAST_EXTREMA_AUTHORITY_2026-05-22.md §Physical law + §PR-B
"""Day0 HIGH physical distribution builder.

Physical law (HIGH):
    H_D = settle(max_{t in local day} T(t))
    At decision τ: H_j = settle(max(H_obs_so_far, max_{t>τ} T_j(t)))

Observation is a LOWER BOUND only.  current_temp must NEVER lower the future max.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Callable

import numpy as np

from src.contracts.settlement_semantics import apply_settlement_rounding

if TYPE_CHECKING:
    from src.types import Bin


def build_day0_high_distribution(
    *,
    observed_high_so_far: float,
    future_member_maxes: np.ndarray,
    round_fn: "Callable | None" = None,
    precision: float = 1.0,
    provenance: str = "",
) -> np.ndarray:
    """Compute physically-correct settlement samples for a Day0 HIGH market.

    Each sample = settle(max(observed_high_so_far, future_member_max[i])).
    Observation is a floor: no sample may fall below observed_high_so_far.
    current_temp does NOT enter the value path.

    Args:
        observed_high_so_far: intraday observed high (lower bound for the day's max).
        future_member_maxes: per-member forecast maxes for the remaining window.
            Shape (n_members,). Must be non-empty.
        round_fn: settlement rounding override (e.g. oracle_truncate for HKO).
            None → WMO asymmetric half-up.
        precision: settlement precision (1.0 = integer, 0.1 = one decimal).
        provenance: metadata string (observation source, snapshot id, etc.).
            Stored in returned array's metadata attribute when debug is needed;
            not used in arithmetic.

    Returns:
        np.ndarray shape (n_members,) of settlement-rounded HIGH samples.

    Raises:
        ValueError: if future_member_maxes is empty.
    """
    arr = np.asarray(future_member_maxes, dtype=np.float64)
    if arr.size == 0:
        raise ValueError(
            "build_day0_high_distribution: future_member_maxes must be non-empty"
        )
    obs = float(observed_high_so_far)
    samples = np.maximum(obs, arr)
    return apply_settlement_rounding(samples, round_fn, precision)


def p_vector(
    bins: "list[Bin]",
    *,
    observed_high_so_far: float,
    future_member_maxes: np.ndarray,
    round_fn: "Callable | None" = None,
    precision: float = 1.0,
    provenance: str = "",
) -> np.ndarray:
    """Return a normalized categorical probability vector over bins.

    Uses build_day0_high_distribution for samples, then assigns each sample
    to its bin.  Handles open-low / open-high / closed bins.

    Args:
        bins: list of Bin objects covering the full market outcome space.
        observed_high_so_far: see build_day0_high_distribution.
        future_member_maxes: see build_day0_high_distribution.
        round_fn: see build_day0_high_distribution.
        precision: see build_day0_high_distribution.
        provenance: see build_day0_high_distribution.

    Returns:
        np.ndarray shape (n_bins,), sum = 1.0.

    Raises:
        ValueError: if no finite mass in any bin (degenerate distribution).
    """
    samples = build_day0_high_distribution(
        observed_high_so_far=observed_high_so_far,
        future_member_maxes=future_member_maxes,
        round_fn=round_fn,
        precision=precision,
        provenance=provenance,
    )
    probs = np.zeros(len(bins), dtype=np.float64)
    for i, b in enumerate(bins):
        if b.is_open_low:
            probs[i] = float(np.sum(samples <= float(b.high)))
        elif b.is_open_high:
            probs[i] = float(np.sum(samples >= float(b.low)))
        else:
            lo = float(b.low)
            hi = float(b.high)
            probs[i] = float(np.sum((samples >= lo) & (samples <= hi)))
    total = probs.sum()
    if total == 0.0 or not np.isfinite(total):
        raise ValueError(
            "p_vector: no finite mass in any bin — distribution is degenerate. "
            f"samples={samples!r}, bins={bins!r}"
        )
    return probs / total
