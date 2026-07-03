# Created: 2026-06-10
# Last reused or audited: 2026-06-11
# Authority basis: qkernel payoff-vector selection. The old rounded-mu doctrine
# ("buy_yes only forecast bin; buy_no only non-forecast bin") is not a live
# admission law. It blocked positive-utility YES candidates and forced inferior
# capital usage in Shanghai-style families. This module remains as a compatibility
# seam for callers that still ask for a direction-law reason, but it no longer
# emits bin-vs-mu vetoes. Forecast completeness, q_lcb reliability, market
# coherence, executable cost, and robust utility are enforced downstream by the
# qkernel decision path.
"""Direction admission compatibility helpers.

Live weather binaries are Arrow-Debreu payoff vectors. A rounded forecast center
is provenance, not proof that only one YES bin or all-but-one NO bins can carry
alpha. This module therefore validates units and geometry helpers for legacy
callers but does not reject a buy_yes/buy_no solely because ``mu`` settles into a
different bin. Bad candidates are rejected by q/payoff/edge/DeltaU/coherence, not
by a modal-bin heuristic.

Pure module: no I/O, no settings reads, no engine imports.
"""
from __future__ import annotations

import math
from typing import Callable

DIRECTION_LAW_REASON = "DIRECTION_NATIVE_SIDE_UNSUPPORTED"

# Legacy geometry helper knob retained for callers/tests that still compute distances.
# It is not used by live direction admission.
DIRECTION_LAW_SIGMA_K = 1.0

# Legacy boundary-zone helper retained for geometry calculations. It is not a buy_no
# live ban.
DIRECTION_LAW_BOUNDARY_ZONE_STEP_FRACTION = 0.25

# Settlement step per bin unit: C point bins cover 1 settled degree, F range bins
# cover 2 settled degrees (src/types/market.py Bin width law).
_SETTLEMENT_STEP_BY_UNIT = {"C": 1.0, "F": 2.0}


def celsius_to_unit(value_c: float, unit: str) -> float:
    """Convert a Celsius POINT value into the bin unit ("C" passthrough)."""
    if unit == "C":
        return float(value_c)
    if unit == "F":
        return float(value_c) * 9.0 / 5.0 + 32.0
    raise ValueError(f"direction law: unsupported bin unit {unit!r}")


def celsius_delta_to_unit(delta_c: float, unit: str) -> float:
    """Convert a Celsius DELTA (e.g. sigma) into the bin unit ("C" passthrough)."""
    if unit == "C":
        return float(delta_c)
    if unit == "F":
        return float(delta_c) * 9.0 / 5.0
    raise ValueError(f"direction law: unsupported bin unit {unit!r}")


def bin_forecast_distance(
    *,
    bin_low: float | None,
    bin_high: float | None,
    mu: float,
) -> float:
    """Distance from the forecast center to the bin, in bin units.

    0.0 when mu lies inside [low, high] (inclusive); otherwise the distance to the
    nearest PRESENT bound. Open-ended bins (low=None means "X or below",
    high=None means "X or higher") use their single bound, and mu beyond that
    bound is INSIDE the bin (distance 0).
    """
    if bin_low is None and bin_high is None:
        raise ValueError("direction law: bin cannot have both bounds unset")
    low = -math.inf if bin_low is None else float(bin_low)
    high = math.inf if bin_high is None else float(bin_high)
    if low > high:
        raise ValueError(f"direction law: bin low={low} > high={high}")
    if low <= mu <= high:
        return 0.0
    return (low - mu) if mu < low else (mu - high)


def direction_law_threshold(
    *,
    unit: str,
    predictive_sigma: float | None,
    sigma_k: float = DIRECTION_LAW_SIGMA_K,
) -> float:
    """T = max(1 settlement step, k x sigma); sigma None/non-finite -> 1 step only.

    The sigma term is licensed ONLY by a real fusion predictive sigma. A sigma
    derived from the q-distribution itself must NOT widen the band: the incident
    posterior's settlement-floored q had std ~3C, which would have re-admitted the
    very trade the law exists to kill. No sigma -> strictly conservative.
    """
    step = _SETTLEMENT_STEP_BY_UNIT.get(unit)
    if step is None:
        raise ValueError(f"direction law: unsupported bin unit {unit!r}")
    if predictive_sigma is None:
        return step
    sigma = float(predictive_sigma)
    if not math.isfinite(sigma) or sigma <= 0.0:
        return step
    return max(step, float(sigma_k) * sigma)


def direction_law_rejection_reason(
    *,
    direction: str,
    bin_low: float | None,
    bin_high: float | None,
    bin_unit: str,
    mu: float | None,
    predictive_sigma: float | None,
    sigma_k: float = DIRECTION_LAW_SIGMA_K,
    mu_settled: float | None = None,
    settle_value: Callable[[float], float] | None = None,
) -> str | None:
    """Return a direction rejection reason, or ``None`` when side is admissible.

    This compatibility function intentionally does **not** compare ``mu`` or
    ``mu_settled`` to the candidate bin. Rounded-center geometry is not live
    authority. Callers may still pass the old arguments while the qkernel path
    consumes the real authority: settlement-aware payoff vectors, qLCB
    reliability, coherence, executable cost, and robust utility.
    """
    if direction not in ("buy_yes", "buy_no"):
        return f"{DIRECTION_LAW_REASON}:direction={direction!r}"
    _ = (
        bin_low,
        bin_high,
        bin_unit,
        mu,
        predictive_sigma,
        sigma_k,
        mu_settled,
        settle_value,
    )
    return None
