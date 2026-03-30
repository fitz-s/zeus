"""Exit trigger evaluation. Spec §6.3.

Six exit triggers. NO per-position stop loss (CLAUDE.md: this is how Rainstorm died).
EDGE_REVERSAL requires 2 consecutive confirmations before triggering.
"""

import logging
from dataclasses import dataclass
from typing import Optional

from src.state.portfolio import Position

logger = logging.getLogger(__name__)


@dataclass
class ExitSignal:
    """An exit signal for a held position."""
    trade_id: str
    trigger: str
    reason: str
    urgency: str = "normal"  # "normal" or "immediate"


# Confirmation state for EDGE_REVERSAL (needs 2 consecutive)
_reversal_counts: dict[str, int] = {}


def evaluate_exit_triggers(
    position: Position,
    current_p_posterior: float,
    current_p_market: float,
    hours_to_settlement: Optional[float] = None,
    market_vig: float = 1.0,
    is_whale_sweep: bool = False,
) -> Optional[ExitSignal]:
    """Evaluate all exit triggers for a position. Returns ExitSignal or None.

    Spec §6.3: Six triggers, checked in priority order.
    No stop loss — only edge-based and structural exits.
    """

    # 1. SETTLEMENT_IMMINENT: market about to settle
    if hours_to_settlement is not None and hours_to_settlement < 1.0:
        return ExitSignal(
            trade_id=position.trade_id,
            trigger="SETTLEMENT_IMMINENT",
            reason=f"Settlement in {hours_to_settlement:.1f}h — exit to avoid settlement risk",
            urgency="immediate",
        )

    # 2. WHALE_TOXICITY: adjacent bin sweep detected
    if is_whale_sweep:
        return ExitSignal(
            trade_id=position.trade_id,
            trigger="WHALE_TOXICITY",
            reason="Adjacent bin sweep detected — cancel/exit",
            urgency="immediate",
        )

    # 3. EDGE_REVERSAL: model now disagrees with our position (needs 2 confirmations)
    edge_reversed = _check_edge_reversal(position, current_p_posterior, current_p_market)
    if edge_reversed:
        return ExitSignal(
            trade_id=position.trade_id,
            trigger="EDGE_REVERSAL",
            reason="Edge reversed for 2 consecutive checks",
        )

    # 4. VIG_EXTREME: market vig became very unfavorable
    if market_vig > 1.08 or market_vig < 0.92:
        return ExitSignal(
            trade_id=position.trade_id,
            trigger="VIG_EXTREME",
            reason=f"Market vig={market_vig:.3f} outside [0.92, 1.08]",
        )

    # 5. EDGE_EVAPORATED: edge shrunk to negligible
    current_edge = _compute_current_edge(position, current_p_posterior, current_p_market)
    if abs(current_edge) < 0.005:
        return ExitSignal(
            trade_id=position.trade_id,
            trigger="EDGE_EVAPORATED",
            reason=f"Current edge={current_edge:.4f} < 0.005",
        )

    # 6. EXPIRY: position held too long without resolution
    # (caller provides hours_to_settlement; if > expiry_hours from config, skip)
    # This is handled by the monitor's scheduling, not here.

    return None


def _check_edge_reversal(
    position: Position,
    current_p_posterior: float,
    current_p_market: float,
) -> bool:
    """Check if edge has reversed. Needs 2 consecutive confirmations. Spec §6.3."""
    current_edge = _compute_current_edge(position, current_p_posterior, current_p_market)
    tid = position.trade_id

    if current_edge < 0:
        _reversal_counts[tid] = _reversal_counts.get(tid, 0) + 1
        if _reversal_counts[tid] >= 2:
            _reversal_counts.pop(tid, None)
            return True
    else:
        _reversal_counts.pop(tid, None)

    return False


def _compute_current_edge(
    position: Position,
    current_p_posterior: float,
    current_p_market: float,
) -> float:
    """Compute current edge for a held position."""
    if position.direction == "buy_yes":
        return current_p_posterior - current_p_market
    else:
        return (1.0 - current_p_posterior) - (1.0 - current_p_market)


def clear_reversal_state(trade_id: str) -> None:
    """Clear reversal confirmation state when position is closed."""
    _reversal_counts.pop(trade_id, None)
