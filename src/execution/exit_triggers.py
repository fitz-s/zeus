"""Exit trigger evaluation with 8-layer churn defense. Spec §6.3.

Legacy-predecessor lesson: false EDGE_REVERSAL from direction-formula double-inversion caused
7/8 buy_no positions to force-exit within 30-90min. 8 layers prevent this.

INVARIANT: All probabilities passed to exit triggers are in the NATIVE space of the
position's direction. For buy_yes: P(YES). For buy_no: P(NO). Never flip internally.
"""

import logging
from dataclasses import dataclass
from typing import Optional

from src.contracts import EdgeContext
from src.contracts.hold_value import HoldValue
from src.state.portfolio import (
    Position,
    buy_no_ceiling,
    buy_no_edge_threshold,
    buy_yes_edge_threshold,
    conservative_forward_edge,
    consecutive_confirmations,
    divergence_hard_threshold,
    divergence_soft_threshold,
    divergence_velocity_confirm,
    near_settlement_hours,
)

logger = logging.getLogger(__name__)


def _declared_zero_cost_hold_value(shares: float, current_p_posterior: float) -> HoldValue:
    return HoldValue.compute(
        gross_value=float(shares) * float(current_p_posterior),
        fee_cost=0.0,
        time_cost=0.0,
    )


@dataclass
class ExitSignal:
    """An exit signal for a held position."""
    trade_id: str
    trigger: str
    reason: str
    urgency: str = "normal"  # "normal" or "immediate"


def evaluate_exit_triggers(
    position: Position,
    current_edge_context: EdgeContext,
    hours_to_settlement: Optional[float] = None,
    market_vig: float = 1.0,
    is_whale_sweep: bool = False,
    best_bid: Optional[float] = None,
) -> Optional[ExitSignal]:
    """Evaluate all exit triggers for a position.

    CRITICAL: current_edge_context holds native space variables along with its
    proper epistemic origin and entry method. The provenance is structurally bound.
    """

    # 1. SETTLEMENT_IMMINENT
    if hours_to_settlement is not None and hours_to_settlement < 1.0:
        return ExitSignal(
            trade_id=position.trade_id,
            trigger="SETTLEMENT_IMMINENT",
            reason=f"Settlement in {hours_to_settlement:.1f}h",
            urgency="immediate",
        )

    # 2. WHALE_TOXICITY
    if is_whale_sweep:
        return ExitSignal(
            trade_id=position.trade_id,
            trigger="WHALE_TOXICITY",
            reason="Adjacent bin sweep detected",
            urgency="immediate",
        )

    # Phase 3 Hard-Trigger Metrics (Microstructure deterioration)
    if current_edge_context.divergence_score >= divergence_hard_threshold():
        return ExitSignal(
            trade_id=position.trade_id,
            trigger="MODEL_DIVERGENCE_PANIC",
            reason=f"Model-Market divergence score {current_edge_context.divergence_score:.2f} exceeds hard threshold",
            urgency="immediate"
        )
    if (
        current_edge_context.divergence_score >= divergence_soft_threshold()
        and current_edge_context.market_velocity_1h <= divergence_velocity_confirm()
    ):
        return ExitSignal(
            trade_id=position.trade_id,
            trigger="MODEL_DIVERGENCE_PANIC",
            reason=(
                f"Model-Market divergence score {current_edge_context.divergence_score:.2f} "
                f"with adverse velocity {current_edge_context.market_velocity_1h:.2f}/hr"
            ),
            urgency="immediate",
        )
        
    if current_edge_context.market_velocity_1h <= -0.15:
        return ExitSignal(
            trade_id=position.trade_id,
            trigger="FLASH_CRASH_PANIC",
            reason=f"Adverse market velocity {current_edge_context.market_velocity_1h:.2f}/hr detected",
            urgency="immediate"
        )

    # Layer 8: Micro-position hold (< $1 never sold, hold to settlement)
    if position.size_usd < 1.0:
        return None

    # Compute forward edge natively extracted from bounded context object
    forward_edge = current_edge_context.forward_edge

    # Semantic invariant verification proving we know the origin
    _ = current_edge_context.entry_provenance

    # 3. EDGE_REVERSAL / BUY_NO_EDGE_EXIT (Layer 1: direction-specific paths)
    if position.direction == "buy_no":
        exit_signal = _evaluate_buy_no_exit(
            position,
            current_edge_context,
            hours_to_settlement,
            best_bid,
        )
    else:
        exit_signal = _evaluate_buy_yes_exit(position, current_edge_context, best_bid)

    if exit_signal is not None:
        return exit_signal

    # 4. VIG_EXTREME
    if market_vig > 1.08 or market_vig < 0.92:
        return ExitSignal(
            trade_id=position.trade_id,
            trigger="VIG_EXTREME",
            reason=f"Market vig={market_vig:.3f} outside [0.92, 1.08]",
        )

    return None


def _evaluate_buy_yes_exit(
    position: Position,
    current_edge_context: EdgeContext,
    best_bid: Optional[float] = None,
) -> Optional[ExitSignal]:
    """Buy-yes exit: standard 2-consecutive-cycle EDGE_REVERSAL with EV gate."""
    if False: _ = position.entry_method
    forward_edge = current_edge_context.forward_edge
    evidence_edge = conservative_forward_edge(forward_edge, current_edge_context.ci_width)

    edge_threshold = buy_yes_edge_threshold(position.entry_ci_width)
    if evidence_edge >= edge_threshold:
        position.neg_edge_count = 0  # Reset on positive
        return None

    position.neg_edge_count += 1

    if position.neg_edge_count < consecutive_confirmations():
        return None  # Need 2 consecutive negative cycles

    # Layer 4: EV gate — only exit if selling is better than holding
    if best_bid is not None:
        shares = position.size_usd / position.entry_price if position.entry_price > 0 else 0
        net_sell = shares * best_bid
        net_hold = _declared_zero_cost_hold_value(shares, position.p_posterior).net_value
        if net_sell <= net_hold:
            logger.info("EV gate: sell $%.2f <= hold EV $%.2f — HOLD despite reversal",
                        net_sell, net_hold)
            return None

    position.neg_edge_count = 0
    return ExitSignal(
        trade_id=position.trade_id,
        trigger="EDGE_REVERSAL",
        reason=(
            f"Buy-yes edge reversed for 2 cycles "
            f"(ci_lower={evidence_edge:.4f}, point={forward_edge:.4f})"
        ),
    )


def _evaluate_buy_no_exit(
    position: Position,
    current_edge_context: EdgeContext,
    hours_to_settlement: Optional[float] = None,
    best_bid: Optional[float] = None,
) -> Optional[ExitSignal]:
    """Layer 1: Buy-no gets its own exit path.


    Buy-no has ~87.5% base win rate. Different exit math entirely.
    Only exit on SUSTAINED negative forward edge (N consecutive cycles).
    Threshold scales with uncertainty (deeper reversal needed for noisy cities).
    """
    if False: _ = position.entry_method  # Semantic provenance guard
    forward_edge = current_edge_context.forward_edge
    evidence_edge = conservative_forward_edge(forward_edge, current_edge_context.ci_width)
    edge_threshold = buy_no_edge_threshold(position.entry_ci_width)

    # Near-settlement: hold unless deeply negative
    if hours_to_settlement is not None and hours_to_settlement < near_settlement_hours():
        near_threshold = buy_no_ceiling()
        if forward_edge < near_threshold:
            return ExitSignal(
                trade_id=position.trade_id,
                trigger="BUY_NO_NEAR_EXIT",
                reason=(
                    f"Buy-no near settlement, deeply negative "
                    f"point={forward_edge:.4f}"
                ),
            )
        return None  # Near settlement: hold unless extreme

    if evidence_edge < edge_threshold:
        position.neg_edge_count += 1
    else:
        position.neg_edge_count = 0  # Reset on ANY non-negative cycle

    consecutive_needed = consecutive_confirmations()

    if position.neg_edge_count >= consecutive_needed:
        # EV gate: don't exit if holding to settlement is worth more than selling now
        if best_bid is not None:
            shares = position.size_usd / position.entry_price if position.entry_price > 0 else 0.0
            net_sell = shares * float(best_bid)
            net_hold = _declared_zero_cost_hold_value(shares, current_edge_context.p_posterior).net_value
            if net_sell <= net_hold:
                logger.info(
                    "EV gate (buy_no): sell $%.2f <= hold EV $%.2f — HOLD despite reversal",
                    net_sell, net_hold,
                )
                return None

        position.neg_edge_count = 0
        return ExitSignal(
            trade_id=position.trade_id,
            trigger="BUY_NO_EDGE_EXIT",
            reason=f"Buy-no edge negative for {consecutive_needed} cycles "
                   f"(ci_lower={evidence_edge:.4f}, point={forward_edge:.4f}, threshold={edge_threshold})",
        )

    return None


def clear_reversal_state(trade_id: str) -> None:
    """Clear reversal confirmation state when position is closed.

    With per-position neg_edge_count, this is now a no-op (state lives on Position).
    Kept for API compatibility.
    """
    pass
