"""Portfolio rotation math for capital-constrained live entry.

The live reactor can see positive-score candidates while Kelly returns size=0
because raw/correlation heat is already saturated by open positions. This file
does not special-case cities or strategies. It compares one universal choice:
keep a held position to settlement, or sell it at the current held-side bid and
redeploy that released cash into a rejected-but-positive candidate.

The module is pure math: no DB, no CLOB, no side effects.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from src.contracts.execution_price import polymarket_fee
from src.strategy.live_inference.live_admission import (
    LIVE_DIRECTION_WIN_RATE_FLOOR,
    live_win_rate_floor_rejection_reason,
)


def _finite(value: float | int | None) -> bool:
    if value is None:
        return False
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def _price_dependent_fee_or_zero(price: float, fee_rate: float) -> float:
    if fee_rate <= 0.0 or price <= 0.0 or price >= 1.0:
        return 0.0
    return polymarket_fee(price, fee_rate)


def _require_probability(value: float, *, field_name: str) -> float:
    if not _finite(value):
        raise ValueError(f"{field_name} must be finite")
    out = float(value)
    if out < 0.0 or out > 1.0:
        raise ValueError(f"{field_name} must be in [0, 1], got {out!r}")
    return out


def _require_positive_unit_cost(value: float, *, field_name: str) -> float:
    if not _finite(value):
        raise ValueError(f"{field_name} must be finite")
    out = float(value)
    if out <= 0.0 or out >= 1.0:
        raise ValueError(f"{field_name} must be in (0, 1), got {out!r}")
    return out


@dataclass(frozen=True)
class RotationHold:
    """One currently held position in held-side probability/price space."""

    position_id: str
    city: str
    target_date: str
    metric: str
    bin_label: str
    direction: str
    shares: float
    held_probability: float
    held_side_best_bid: float
    token_id: str = ""
    condition_id: str = ""

    def __post_init__(self) -> None:
        if not str(self.position_id).strip():
            raise ValueError("position_id is required")
        if not _finite(self.shares) or float(self.shares) <= 0.0:
            raise ValueError("shares must be finite and > 0")
        _require_probability(self.held_probability, field_name="held_probability")
        _require_probability(self.held_side_best_bid, field_name="held_side_best_bid")


@dataclass(frozen=True)
class RotationCandidate:
    """One budget-rejected candidate in held-side probability/cost space."""

    event_id: str
    city: str
    target_date: str
    metric: str
    bin_label: str
    direction: str
    q_lcb: float
    fee_adjusted_cost: float
    trade_score: float
    p_fill_lcb: float | None = None
    token_id: str = ""
    condition_id: str = ""
    rejection_reason: str = ""

    def __post_init__(self) -> None:
        if not str(self.event_id).strip():
            raise ValueError("event_id is required")
        _require_probability(self.q_lcb, field_name="q_lcb")
        _require_positive_unit_cost(self.fee_adjusted_cost, field_name="fee_adjusted_cost")
        if not _finite(self.trade_score):
            raise ValueError("trade_score must be finite")
        if self.p_fill_lcb is not None:
            _require_probability(self.p_fill_lcb, field_name="p_fill_lcb")


@dataclass(frozen=True)
class RotationDecision:
    hold: RotationHold
    candidate: RotationCandidate
    action: str
    reason: str
    sell_value_usd: float
    hold_future_value_usd: float
    candidate_future_value_usd: float
    net_improvement_usd: float
    net_improvement_ratio: float
    fill_lcb_used: float


def held_net_sell_value(hold: RotationHold, *, fee_rate: float) -> float:
    """Cash released by selling the held-side shares at the current bid."""
    bid = _require_probability(hold.held_side_best_bid, field_name="held_side_best_bid")
    fee_per_share = _price_dependent_fee_or_zero(bid, float(fee_rate))
    return max(0.0, float(hold.shares) * (bid - fee_per_share))


def held_future_value(hold: RotationHold) -> float:
    """Expected settlement value if the position is kept."""
    p = _require_probability(hold.held_probability, field_name="held_probability")
    return float(hold.shares) * p


def candidate_future_value(
    candidate: RotationCandidate,
    *,
    released_cash_usd: float,
    require_fill_lcb: bool = True,
) -> tuple[float, float]:
    """Expected future value after selling and redeploying released cash.

    If a fill lower-bound is present, the failed-fill branch keeps released
    cash. That makes the math explicit: a non-atomic sell-then-buy can still be
    better than holding only when the replacement value beats the abandoned
    hold value after fill uncertainty.
    """
    if not _finite(released_cash_usd) or float(released_cash_usd) <= 0.0:
        return 0.0, 0.0
    q_lcb = _require_probability(candidate.q_lcb, field_name="q_lcb")
    cost = _require_positive_unit_cost(
        candidate.fee_adjusted_cost,
        field_name="fee_adjusted_cost",
    )
    if candidate.p_fill_lcb is None:
        if require_fill_lcb:
            return 0.0, 0.0
        fill_lcb = 1.0
    else:
        fill_lcb = _require_probability(candidate.p_fill_lcb, field_name="p_fill_lcb")
    deployed_shares = float(released_cash_usd) / cost
    filled_future = deployed_shares * q_lcb
    future = fill_lcb * filled_future + (1.0 - fill_lcb) * float(released_cash_usd)
    return future, fill_lcb


def evaluate_rotation(
    hold: RotationHold,
    candidate: RotationCandidate,
    *,
    fee_rate: float,
    min_net_improvement_usd: float = 0.0,
    min_net_improvement_ratio: float = 0.0,
    require_fill_lcb: bool = True,
    min_candidate_q_lcb_for_live: float = LIVE_DIRECTION_WIN_RATE_FLOOR,
) -> RotationDecision:
    """Compare one held position against one replacement candidate."""
    if hold.token_id and candidate.token_id and hold.token_id == candidate.token_id:
        sell_value = held_net_sell_value(hold, fee_rate=fee_rate)
        hold_value = held_future_value(hold)
        return RotationDecision(
            hold=hold,
            candidate=candidate,
            action="HOLD",
            reason="SAME_TOKEN",
            sell_value_usd=sell_value,
            hold_future_value_usd=hold_value,
            candidate_future_value_usd=0.0,
            net_improvement_usd=-hold_value,
            net_improvement_ratio=-1.0,
            fill_lcb_used=0.0,
        )
    sell_value = held_net_sell_value(hold, fee_rate=fee_rate)
    hold_value = held_future_value(hold)
    win_rate_reason = live_win_rate_floor_rejection_reason(
        q_lcb=candidate.q_lcb,
        floor=min_candidate_q_lcb_for_live,
    )
    if win_rate_reason is not None:
        return RotationDecision(
            hold=hold,
            candidate=candidate,
            action="HOLD",
            reason=win_rate_reason,
            sell_value_usd=sell_value,
            hold_future_value_usd=hold_value,
            candidate_future_value_usd=0.0,
            net_improvement_usd=-hold_value,
            net_improvement_ratio=-1.0,
            fill_lcb_used=0.0,
        )
    replacement_value, fill_lcb = candidate_future_value(
        candidate,
        released_cash_usd=sell_value,
        require_fill_lcb=require_fill_lcb,
    )
    improvement = replacement_value - hold_value
    ratio_base = max(abs(hold_value), 1e-9)
    improvement_ratio = improvement / ratio_base
    if improvement <= 0.0:
        action = "HOLD"
        reason = "HOLD_VALUE_DOMINANT"
    elif improvement < float(min_net_improvement_usd):
        action = "HOLD"
        reason = "IMPROVEMENT_BELOW_DOLLAR_HURDLE"
    elif improvement_ratio < float(min_net_improvement_ratio):
        action = "HOLD"
        reason = "IMPROVEMENT_BELOW_RATIO_HURDLE"
    else:
        action = "ROTATE"
        reason = "ROTATION_REPLACE_CANDIDATE"
    return RotationDecision(
        hold=hold,
        candidate=candidate,
        action=action,
        reason=reason,
        sell_value_usd=sell_value,
        hold_future_value_usd=hold_value,
        candidate_future_value_usd=replacement_value,
        net_improvement_usd=improvement,
        net_improvement_ratio=improvement_ratio,
        fill_lcb_used=fill_lcb,
    )


def best_rotation(
    holds: list[RotationHold] | tuple[RotationHold, ...],
    candidates: list[RotationCandidate] | tuple[RotationCandidate, ...],
    *,
    fee_rate: float,
    min_net_improvement_usd: float = 0.0,
    min_net_improvement_ratio: float = 0.0,
    require_fill_lcb: bool = True,
    min_candidate_q_lcb_for_live: float = LIVE_DIRECTION_WIN_RATE_FLOOR,
) -> RotationDecision | None:
    """Return the strongest universal rotation, or None when no trade improves."""
    best: RotationDecision | None = None
    for hold in holds:
        for candidate in candidates:
            decision = evaluate_rotation(
                hold,
                candidate,
                fee_rate=fee_rate,
                min_net_improvement_usd=min_net_improvement_usd,
                min_net_improvement_ratio=min_net_improvement_ratio,
                require_fill_lcb=require_fill_lcb,
                min_candidate_q_lcb_for_live=min_candidate_q_lcb_for_live,
            )
            if decision.action != "ROTATE":
                continue
            if best is None or decision.net_improvement_usd > best.net_improvement_usd:
                best = decision
    return best
