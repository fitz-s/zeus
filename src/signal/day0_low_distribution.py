# Created: 2026-06-17
# Last audited: 2026-06-17
# Authority basis: operator delta-package v2 (zeus_delta_only_upgrade_package_v2/
#   zeus_day0_low_physical_law_patch.diff) — Day0 LOW physical settlement preimage.
#   Mirror of the already-correct HIGH law (settle(max(H_obs, future_member_max))).
"""Day0 LOW physical distribution builder.

Physical law (LOW), for a target local day D at decision time tau:
    L_D            = settle(min_{t in D} T(t))                 -- the realized daily minimum
    L_obs(tau)     = min_{t <= tau, t in D} T(t)               -- observed low so far (a CEILING)
    L_future,j(tau)= min_{t > tau,  t in D} T_j(t)             -- model/member j's remaining min
    Y_j            = settle(min(L_obs(tau), L_future,j(tau)))  -- one settlement sample

The observed low is an UPPER BOUND on the day's minimum only. ``current_temp`` must NEVER enter
the settlement-value path of an extreme statistic — a daily minimum is a path extremum, not a
convex blend of the current temperature with the remaining forecast. ``current_temp`` may be
diagnostic / uncertainty context elsewhere, never a daily-min sample.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Callable

import numpy as np

from src.contracts.settlement_semantics import apply_settlement_rounding

if TYPE_CHECKING:
    from src.types import Bin


def build_day0_low_distribution(
    *,
    observed_low_so_far: float,
    future_member_mins: np.ndarray,
    round_fn: "Callable | None" = None,
    precision: float = 1.0,
    provenance: str = "",
) -> np.ndarray:
    """Physically-correct settlement samples for a Day0 LOW market.

    Each sample i = settle(min(observed_low_so_far, future_member_mins[i])). The observation is a
    ceiling: no sample may exceed observed_low_so_far. ``current_temp`` is deliberately absent.
    """
    _ = provenance  # carried for receipt provenance; not part of the value path
    arr = np.asarray(future_member_mins, dtype=np.float64)
    if arr.size == 0:
        raise ValueError("build_day0_low_distribution: future_member_mins must be non-empty")
    obs = float(observed_low_so_far)
    samples = np.minimum(obs, arr)
    return apply_settlement_rounding(samples, round_fn, precision)


def p_vector(
    bins: "list[Bin]",
    *,
    observed_low_so_far: float,
    future_member_mins: np.ndarray,
    round_fn: "Callable | None" = None,
    precision: float = 1.0,
    provenance: str = "",
) -> np.ndarray:
    """Normalized categorical probability vector over LOW bins from the physical samples.

    Pure helper (no signal construction). Open-low bins count samples at/below their finite high;
    open-high bins count samples at/above their finite low; interior bins count [low, high].
    """
    samples = build_day0_low_distribution(
        observed_low_so_far=observed_low_so_far,
        future_member_mins=future_member_mins,
        round_fn=round_fn,
        precision=precision,
        provenance=provenance,
    )
    probs = np.zeros(len(bins), dtype=np.float64)
    for i, b in enumerate(bins):
        if getattr(b, "is_open_low", False):
            probs[i] = float(np.sum(samples <= float(b.high)))
        elif getattr(b, "is_open_high", False):
            probs[i] = float(np.sum(samples >= float(b.low)))
        else:
            probs[i] = float(np.sum((samples >= float(b.low)) & (samples <= float(b.high))))
    total = probs.sum()
    if total == 0.0 or not np.isfinite(total):
        raise ValueError(
            "p_vector: no finite mass in any bin — distribution is degenerate. "
            f"samples={samples!r}"
        )
    return probs / total
