# Created: 2026-06-10
# Last reused or audited: 2026-06-10
# Authority basis: operator direction doctrine "buy_yes <=> bin ~= forecast" made
#   code after incident 0b5c305e26524042 (Milan 24C first fill, 2026-06-10T02:58Z;
#   docs/evidence/2026_06_10_milan_24c_first_fill_rootcause.md FIX A). The doctrine
#   previously existed ONLY as operator law; this module makes a far-tail buy_yes
#   (and a forecast-adjacent buy_no) UNCONSTRUCTABLE at candidate admission.
"""Direction law: buy_yes only forecast-adjacent, buy_no only forecast-distant.

The incident category: the candidate-selection objective max(q_lcb - price) peaks
where the model disagrees most with the market, which - whenever the q_lcb carrier
is corrupt or unlicensed - is exactly where the model is most likely WRONG. The
direction law is the structural cut that survives any q_lcb pathology: a buy_yes
whose bin is far from our own posterior center contradicts our own belief and is
rejected DETERMINISTICALLY, before ranking, FDR, sizing, or execution.

Law (operator doctrine, both halves):
  buy_yes admissible  iff  distance(bin, mu*) <= T
  buy_no  admissible  iff  distance(bin, mu*) >  T
  T = max(1 x settlement_step, k x predictive_sigma)   (k conservative, default 1.0)

distance(bin, mu*) = 0 when mu* lies inside [low, high]; otherwise the distance to
the NEAREST present bound (open-ended ">= X" / "<= X" bins use their single bound).

mu*/sigma provenance (Fitz #4): the replacement-path posterior carries
provenance_json.anchor_value_c (the fused center) and
provenance_json.bayes_precision_fusion.predictive_sigma_c - both in Celsius. Legacy rows
without fusion fall back to the q-distribution mean over the family bins (computed
by the caller in the bin-native unit); the sigma fallback is None, which makes the
threshold strictly conservative (T = 1 settlement step).

Pure module: no I/O, no settings reads, no engine imports.
"""
from __future__ import annotations

import math

DIRECTION_LAW_REASON = "DIRECTION_LAW_BIN_FORECAST_MISMATCH"

# Conservative sigma multiplier (operator: "k conservative"). At k=1.0 the
# admissible YES band is +-1 predictive sigma around the fused center (never
# narrower than one settlement step). Incident check: |24 - 26.42| = 2.42 >
# max(1, 1.0 x 1.263) = 1.263 -> rejected.
DIRECTION_LAW_SIGMA_K = 1.0

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
) -> str | None:
    """Return the deterministic rejection reason, or None when admissible.

    ``mu`` and ``predictive_sigma`` must already be in the bin unit (callers
    convert via celsius_to_unit / celsius_delta_to_unit). ``mu`` None or
    non-finite is fail-CLOSED for buy_yes (a YES bet with no forecast center is
    the incident category) and fail-OPEN for buy_no (the legacy buy_no surface
    must not be broken by a missing center; its own conservative-evidence and
    capital-efficiency gates still apply).
    """
    if direction not in ("buy_yes", "buy_no"):
        return None
    if mu is None or not math.isfinite(float(mu)):
        if direction == "buy_yes":
            return f"{DIRECTION_LAW_REASON}:mu=missing:direction=buy_yes"
        return None
    distance = bin_forecast_distance(bin_low=bin_low, bin_high=bin_high, mu=float(mu))
    threshold = direction_law_threshold(
        unit=bin_unit, predictive_sigma=predictive_sigma, sigma_k=sigma_k
    )
    if direction == "buy_yes" and distance > threshold:
        return (
            f"{DIRECTION_LAW_REASON}:direction=buy_yes:"
            f"distance={distance:.4f}:threshold={threshold:.4f}:mu={float(mu):.4f}"
        )
    if direction == "buy_no" and distance <= threshold:
        return (
            f"{DIRECTION_LAW_REASON}:direction=buy_no:"
            f"distance={distance:.4f}:threshold={threshold:.4f}:mu={float(mu):.4f}"
        )
    return None
