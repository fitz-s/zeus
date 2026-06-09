"""Shared live-trade admission primitives.

These helpers express objective-level live-money constraints that are broader
than per-family ranking. They do not change q, price, FDR, Kelly, or venue state.
"""

from __future__ import annotations

import math


# Operator objective: real participating trades must settle with stable win-rate
# greater than 51% after costs. Positive-EV low-probability lottery legs remain
# valid research/shadow evidence, but they are not live-money entries.
LIVE_DIRECTION_WIN_RATE_FLOOR = 0.51

# A buy-NO on a single settlement bin is not a generic "not this exact value"
# lottery when the model itself assigns material YES mass to that bin. The
# production-safe proof is a NO-side conservative bound, ideally derived from
# YES_UCB. Until that is first-class everywhere, material-bin buy-NO must show a
# stronger LCB provenance than plain forecast bootstrap.
LIVE_BUY_NO_MATERIAL_YES_POSTERIOR = 0.20
LIVE_BUY_NO_MATERIAL_ALLOWED_LCB_SOURCES = frozenset({"EMOS_ANALYTIC", "SETTLEMENT_ISOTONIC", "YES_UCB_DERIVED"})


def live_win_rate_floor_rejection_reason(
    *,
    q_lcb: float | int | None,
    floor: float = LIVE_DIRECTION_WIN_RATE_FLOOR,
) -> str | None:
    """Return a live admission blocker when the direction LCB is below floor."""

    try:
        q_value = float(q_lcb)
        floor_value = float(floor)
    except (TypeError, ValueError):
        return f"ADMISSION_WIN_RATE_FLOOR:q_lcb=missing:min={float(floor):.4f}"
    if not math.isfinite(q_value):
        return f"ADMISSION_WIN_RATE_FLOOR:q_lcb=nonfinite:min={floor_value:.4f}"
    if not math.isfinite(floor_value) or floor_value <= 0.0 or floor_value >= 1.0:
        raise ValueError(f"live win-rate floor must be in (0, 1), got {floor!r}")
    if q_value < floor_value:
        return f"ADMISSION_WIN_RATE_FLOOR:q_lcb={q_value:.4f}:min={floor_value:.4f}"
    return None


def live_lcb_consistency_rejection_reason(
    *,
    q_direction: float | int | None,
    q_lcb: float | int | None,
) -> str | None:
    """Reject impossible conservative bounds before any ranking or sizing."""

    try:
        q_value = float(q_direction)
        q_lcb_value = float(q_lcb)
    except (TypeError, ValueError):
        return "ADMISSION_LCB_CONSISTENCY:inputs=missing"
    if not all(math.isfinite(v) for v in (q_value, q_lcb_value)):
        return "ADMISSION_LCB_CONSISTENCY:inputs=nonfinite"
    if q_value < 0.0 or q_value > 1.0:
        return f"ADMISSION_LCB_CONSISTENCY:q_direction={q_value:.4f}:range=[0,1]"
    if q_lcb_value < 0.0 or q_lcb_value > 1.0:
        return f"ADMISSION_LCB_CONSISTENCY:q_lcb={q_lcb_value:.4f}:range=[0,1]"
    if q_lcb_value > q_value:
        return f"ADMISSION_LCB_CONSISTENCY:q_lcb={q_lcb_value:.6f}:q_direction={q_value:.6f}"
    return None


def live_capital_efficiency_rejection_reason(
    *,
    q_lcb: float | int | None,
    execution_price: float | int | None,
    trade_score: float | int | None,
) -> str | None:
    """Reject only structurally non-positive conservative EV.

    The rule is direction-agnostic: ``q_lcb`` is already in the candidate's win
    direction, and ``execution_price`` is the fee-adjusted cost per share. Low
    maximum payout ROI and low robust EV/$ are ranking/sizing inputs, not fixed
    live blockers.
    """

    try:
        q_value = float(q_lcb)
        price = float(execution_price)
        score = float(trade_score)
    except (TypeError, ValueError):
        return "ADMISSION_CAPITAL_EFFICIENCY:inputs=missing"
    if not all(math.isfinite(v) for v in (q_value, price, score)):
        return "ADMISSION_CAPITAL_EFFICIENCY:inputs=nonfinite"
    if price <= 0.0 or price >= 1.0:
        return f"ADMISSION_CAPITAL_EFFICIENCY:price={price:.4f}:range=(0,1)"
    if q_value < 0.0 or q_value > 1.0:
        return f"ADMISSION_CAPITAL_EFFICIENCY:q_lcb={q_value:.4f}:range=[0,1]"
    conservative_ev_per_dollar = (q_value - price) / price
    if conservative_ev_per_dollar <= 0.0:
        return (
            "ADMISSION_CAPITAL_EFFICIENCY_LCB_EV:"
            f"ev_per_dollar={conservative_ev_per_dollar:.6f}:q_lcb={q_value:.6f}:price={price:.6f}"
        )
    return None


def live_buy_no_conservative_evidence_rejection_reason(
    *,
    direction: str | None,
    q_direction: float | int | None,
    q_lcb: float | int | None,
    execution_price: float | int | None,
    q_lcb_calibration_source: str | None,
    same_bin_yes_posterior: float | int | None = None,
    material_yes_posterior: float = LIVE_BUY_NO_MATERIAL_YES_POSTERIOR,
    allowed_lcb_sources: frozenset[str] = LIVE_BUY_NO_MATERIAL_ALLOWED_LCB_SOURCES,
) -> str | None:
    """Block material-bin buy-NO without evidence-backed conservative NO LCB.

    ``q_direction`` is the candidate-direction posterior. ``same_bin_yes_posterior``
    must be supplied from an independently materialized YES-bin probability; this
    guard must never infer YES from a NO candidate by complement arithmetic. The
    guard is deliberately one-way: it never creates a trade and it does not touch
    buy-YES.
    """

    if direction != "buy_no":
        return None
    if same_bin_yes_posterior is None:
        return "ADMISSION_BUY_NO_INDEPENDENT_YES_POSTERIOR_MISSING"
    try:
        q_value = float(q_direction)
        q_lcb_value = float(q_lcb)
        price = float(execution_price)
        material_floor = float(material_yes_posterior)
        yes_posterior = float(same_bin_yes_posterior)
    except (TypeError, ValueError):
        return "ADMISSION_BUY_NO_CONSERVATIVE_EVIDENCE:inputs=missing"
    if not all(math.isfinite(v) for v in (q_value, q_lcb_value, price, material_floor, yes_posterior)):
        return "ADMISSION_BUY_NO_CONSERVATIVE_EVIDENCE:inputs=nonfinite"
    if q_value < 0.0 or q_value > 1.0:
        return f"ADMISSION_BUY_NO_CONSERVATIVE_EVIDENCE:q_direction={q_value:.4f}:range=[0,1]"
    if q_lcb_value < 0.0 or q_lcb_value > 1.0:
        return f"ADMISSION_BUY_NO_CONSERVATIVE_EVIDENCE:q_lcb={q_lcb_value:.4f}:range=[0,1]"
    if price <= 0.0 or price >= 1.0:
        return f"ADMISSION_BUY_NO_CONSERVATIVE_EVIDENCE:price={price:.4f}:range=(0,1)"
    if yes_posterior < 0.0 or yes_posterior > 1.0:
        return f"ADMISSION_BUY_NO_CONSERVATIVE_EVIDENCE:yes_posterior={yes_posterior:.4f}:range=[0,1]"
    if material_floor <= 0.0 or material_floor >= 1.0:
        raise ValueError("buy-NO material-bin posterior floor must be in (0, 1)")

    if yes_posterior >= material_floor:
        source = str(q_lcb_calibration_source or "").strip()
        if source not in allowed_lcb_sources:
            conservative_edge = q_lcb_value - price
            confidence_gap = max(0.0, q_value - q_lcb_value)
            if conservative_edge > confidence_gap:
                return None
            return (
                "ADMISSION_BUY_NO_CONSERVATIVE_EVIDENCE_MISSING:"
                f"yes_posterior={yes_posterior:.6f}:max={material_floor:.6f}:"
                f"no_q_lcb={q_lcb_value:.6f}:price={price:.6f}:"
                f"conservative_edge={conservative_edge:.6f}:confidence_gap={confidence_gap:.6f}:"
                f"source={source or 'missing'}"
            )
    return None
