"""Portfolio state management. Spec §6.4.

Atomic JSON + SQL mirror. Positions are the source of truth.
Provides exposure queries for risk limit enforcement.
"""

import json
import logging
import os
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from src.config import STATE_DIR

logger = logging.getLogger(__name__)

POSITIONS_PATH = STATE_DIR / "positions.json"


@dataclass
class Position:
    """A held trading position.

    INVARIANT: p_posterior and entry_price are ALWAYS in the native space of the
    direction. For buy_yes: P(YES) and YES market price. For buy_no: P(NO) and NO
    market price. This invariant is established once at entry and never flipped.
    """
    trade_id: str
    market_id: str
    city: str
    cluster: str
    target_date: str
    bin_label: str
    direction: str  # "buy_yes" or "buy_no"
    size_usd: float
    entry_price: float  # Native space: YES price for buy_yes, NO price for buy_no
    p_posterior: float   # Native space: P(YES) for buy_yes, P(NO) for buy_no
    edge: float
    entered_at: str
    # Token IDs for CLOB orderbook queries (exit VWMP refresh)
    token_id: str = ""
    no_token_id: str = ""
    # Attribution (CLAUDE.md mandatory)
    edge_source: str = ""
    discovery_mode: str = ""
    market_hours_open: float = 0.0
    # Churn defense: per-position state
    neg_edge_count: int = 0  # Layer 1: consecutive negative edge cycles
    last_exit_at: str = ""   # Layer 5: reentry block timestamp
    exit_reason: str = ""    # Layer 5+6: why position was closed


@dataclass
class PortfolioState:
    positions: list[Position] = field(default_factory=list)
    bankroll: float = 150.0
    updated_at: str = ""
    # Layer 5+6: recently closed positions for reentry/cooldown checks
    recent_exits: list[dict] = field(default_factory=list)


def load_portfolio(path: Optional[Path] = None) -> PortfolioState:
    """Load portfolio from JSON file. Returns empty state if file missing."""
    path = path or POSITIONS_PATH
    if not path.exists():
        return PortfolioState()

    with open(path) as f:
        data = json.load(f)

    positions = [Position(**p) for p in data.get("positions", [])]
    return PortfolioState(
        positions=positions,
        bankroll=data.get("bankroll", 150.0),
        updated_at=data.get("updated_at", ""),
    )


def save_portfolio(state: PortfolioState, path: Optional[Path] = None) -> None:
    """Atomic write: write to tmp, then os.replace(). Spec: atomic write pattern."""
    path = path or POSITIONS_PATH
    path.parent.mkdir(parents=True, exist_ok=True)

    state.updated_at = datetime.now(timezone.utc).isoformat()
    data = {
        "positions": [asdict(p) for p in state.positions],
        "bankroll": state.bankroll,
        "updated_at": state.updated_at,
    }

    # Atomic write pattern per OpenClaw conventions
    fd, tmp_path = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp_path, str(path))
    except Exception:
        os.unlink(tmp_path)
        raise


def add_position(state: PortfolioState, pos: Position) -> None:
    """Add a position to the portfolio."""
    state.positions.append(pos)


def remove_position(
    state: PortfolioState, trade_id: str, exit_reason: str = ""
) -> Optional[Position]:
    """Remove a position by trade_id. Track in recent_exits for reentry blocks."""
    for i, p in enumerate(state.positions):
        if p.trade_id == trade_id:
            pos = state.positions.pop(i)
            pos.exit_reason = exit_reason
            pos.last_exit_at = datetime.now(timezone.utc).isoformat()
            # Layer 5+6: track for reentry/cooldown
            state.recent_exits.append({
                "city": pos.city, "bin_label": pos.bin_label,
                "target_date": pos.target_date, "direction": pos.direction,
                "token_id": pos.token_id, "no_token_id": pos.no_token_id,
                "exit_reason": exit_reason,
                "exited_at": pos.last_exit_at,
            })
            # Keep only last 50 exits
            if len(state.recent_exits) > 50:
                state.recent_exits = state.recent_exits[-50:]
            return pos
    return None


def portfolio_heat(state: PortfolioState) -> float:
    """Total portfolio exposure as fraction of bankroll."""
    if state.bankroll <= 0:
        return 0.0
    total = sum(p.size_usd for p in state.positions)
    return total / state.bankroll


def city_exposure(state: PortfolioState, city: str) -> float:
    """Exposure to a specific city as fraction of bankroll."""
    if state.bankroll <= 0:
        return 0.0
    total = sum(p.size_usd for p in state.positions if p.city == city)
    return total / state.bankroll


def cluster_exposure(state: PortfolioState, cluster: str) -> float:
    """Exposure to a cluster/region as fraction of bankroll."""
    if state.bankroll <= 0:
        return 0.0
    total = sum(p.size_usd for p in state.positions if p.cluster == cluster)
    return total / state.bankroll


# --- Churn defense: Layers 5, 6, 7 ---

def is_reentry_blocked(
    state: PortfolioState, city: str, bin_label: str,
    target_date: str, minutes: int = 20,
) -> bool:
    """Layer 5: Block re-entry into a range recently exited via reversal."""
    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat()
    reversal_reasons = {
        "EDGE_REVERSAL", "BUY_NO_EDGE_EXIT", "ENSEMBLE_CONFLICT",
        "DAY0_OBSERVATION_REVERSAL",
    }
    for ex in state.recent_exits:
        if (ex["city"] == city and ex["bin_label"] == bin_label
                and ex["target_date"] == target_date
                and ex["exit_reason"] in reversal_reasons
                and ex["exited_at"] >= cutoff):
            return True
    return False


def is_token_on_cooldown(state: PortfolioState, token_id: str, hours: float = 1.0) -> bool:
    """Layer 6: Block rebuy of tokens voided within the last hour."""
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    voided_reasons = {"UNFILLED_ORDER", "EXIT_FAILED"}
    for ex in state.recent_exits:
        if ((ex["token_id"] == token_id or ex["no_token_id"] == token_id)
                and ex["exit_reason"] in voided_reasons
                and ex["exited_at"] >= cutoff):
            return True
    return False


def has_same_city_range_open(state: PortfolioState, city: str, bin_label: str) -> bool:
    """Layer 7: Block same city+range across different dates."""
    return any(p.city == city and p.bin_label == bin_label for p in state.positions)
