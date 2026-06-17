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

from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable, Optional

import numpy as np

from src.contracts.settlement_semantics import apply_settlement_rounding

if TYPE_CHECKING:
    from src.types import Bin


@dataclass(frozen=True)
class PreDay0LowCarryoverConditioning:
    """Soft LOW conditioning from a fresh late T-1 observation window.

    ``conditioned_member_mins`` is a probabilistic feature, not a hard fact:
    evaluator blends it with the unconditioned forecast vector by ``weight``.
    """

    conditioned_member_mins: np.ndarray
    effective_ceiling: float
    anchor_temp: float
    weight: float
    lead_hours_to_target_start: float
    observation_age_minutes: float
    low_age_minutes: float
    unit: str


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


def build_pre_day0_low_carryover_conditioning(
    *,
    member_mins: np.ndarray,
    window_low: float,
    current_temp: float,
    lead_hours_to_target_start: float,
    observation_age_minutes: float,
    low_age_minutes: float,
    unit: str,
    max_lead_hours: float = 4.0,
    max_observation_age_minutes: float = 120.0,
    max_weight: float = 0.70,
    min_weight: float = 0.05,
) -> Optional[PreDay0LowCarryoverConditioning]:
    """Build a soft carryover transform for tomorrow's LOW market.

    This captures the economically important case where the late T-1
    temperature has already fallen to a level likely to persist through local
    midnight. It intentionally does not produce deterministic absorption:
    target-day lows later in the day remain possible, and stale/far-from-midnight
    observations decay to no signal.
    """
    arr = np.asarray(member_mins, dtype=np.float64)
    if arr.ndim != 1 or arr.size == 0 or not np.all(np.isfinite(arr)):
        return None
    try:
        low = float(window_low)
        current = float(current_temp)
        lead = float(lead_hours_to_target_start)
        obs_age = max(0.0, float(observation_age_minutes))
        low_age = max(0.0, float(low_age_minutes))
    except (TypeError, ValueError):
        return None
    if not all(np.isfinite(v) for v in (low, current, lead, obs_age, low_age)):
        return None
    if lead <= 0.0 or lead > float(max_lead_hours):
        return None
    if obs_age > float(max_observation_age_minutes):
        return None

    u = str(unit or "").upper()
    unit_scale = 1.8 if u == "F" else 1.0

    # If the window low was not the latest print, let it decay toward current
    # temperature before applying the midnight carryover uncertainty buffer.
    low_recency_buffer = min(low_age / 60.0, 3.0) * 0.50 * unit_scale
    anchor = min(current, low + low_recency_buffer)

    # Conservative ceiling on target-day early LOW. The buffer grows with the
    # remaining time to midnight and publication age, but stays finite and soft.
    carryover_buffer = (
        0.50 * unit_scale
        + max(0.0, lead) * 0.60 * unit_scale
        + min(obs_age / 60.0, 2.0) * 0.20 * unit_scale
    )
    effective_ceiling = anchor + carryover_buffer

    lead_factor = max(0.0, min(1.0, 1.0 - lead / float(max_lead_hours)))
    freshness_factor = max(
        0.0,
        min(1.0, 1.0 - obs_age / float(max_observation_age_minutes)),
    )
    weight = min(float(max_weight), max(0.0, float(max_weight) * lead_factor * freshness_factor))
    if weight < float(min_weight):
        return None

    conditioned = np.minimum(arr, effective_ceiling)
    if np.allclose(conditioned, arr, rtol=0.0, atol=1e-9):
        return None
    return PreDay0LowCarryoverConditioning(
        conditioned_member_mins=conditioned,
        effective_ceiling=float(effective_ceiling),
        anchor_temp=float(anchor),
        weight=float(weight),
        lead_hours_to_target_start=float(lead),
        observation_age_minutes=float(obs_age),
        low_age_minutes=float(low_age),
        unit=u or "UNKNOWN",
    )


def blend_pre_day0_low_carryover_probabilities(
    *,
    base_p_raw: np.ndarray,
    carryover_p_raw: np.ndarray,
    weight: float,
) -> Optional[np.ndarray]:
    """Blend base forecast probability with the carryover-conditioned vector."""
    try:
        base = np.asarray(base_p_raw, dtype=np.float64)
        carry = np.asarray(carryover_p_raw, dtype=np.float64)
        w = float(weight)
    except (TypeError, ValueError):
        return None
    if base.shape != carry.shape or base.ndim != 1 or base.size == 0:
        return None
    if not (np.all(np.isfinite(base)) and np.all(np.isfinite(carry))):
        return None
    if np.any(base < 0.0) or np.any(carry < 0.0):
        return None
    if not np.isfinite(w) or w <= 0.0 or w >= 1.0:
        return None
    out = (1.0 - w) * base + w * carry
    total = float(out.sum())
    if not np.isfinite(total) or total <= 0.0:
        return None
    return out / total


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
