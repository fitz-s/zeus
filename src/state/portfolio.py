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
class ExitDecision:
    """Result of Position.evaluate_exit()."""
    should_exit: bool
    reason: str = ""
    urgency: str = "normal"  # "normal" or "immediate"


# Administrative exit reasons — excluded from P&L calculations
ADMIN_EXITS = frozenset({
    "GHOST_DUPLICATE", "PHANTOM_NOT_ON_CHAIN",
    "UNFILLED_ORDER", "SETTLED_NOT_IN_API", "EXIT_FAILED",
})


@dataclass
class Position:
    """A held trading position — stateful entity that owns its exit logic.

    INVARIANT: p_posterior and entry_price are ALWAYS in the native space of the
    direction. For buy_yes: P(YES) and YES market price. For buy_no: P(NO) and NO
    market price. This invariant is established once at entry and never flipped.

    Position knows HOW to exit itself. Monitor just calls evaluate_exit().
    """
    # Identity (immutable after creation)
    trade_id: str
    market_id: str
    city: str
    cluster: str
    target_date: str
    bin_label: str
    direction: str  # "buy_yes" or "buy_no"
    unit: str = "F"  # Blueprint v2: carried, never inferred

    # Probability (always in held-side space — flipped exactly once at creation)
    size_usd: float = 0.0
    entry_price: float = 0.0  # Native space
    p_posterior: float = 0.0  # Native space (p_held_side in blueprint)
    edge: float = 0.0
    shares: float = 0.0  # size_usd / entry_price
    cost_basis_usd: float = 0.0  # = size_usd
    bankroll_at_entry: float = 150.0
    entered_at: str = ""

    # Entry context (immutable snapshot — Blueprint v2 §2)
    entry_method: str = "ens_member_counting"
    signal_version: str = "v2"
    calibration_version: str = ""
    decision_snapshot_id: str = ""  # FK to ensemble_snapshots at decision time

    # Strategy + attribution
    strategy: str = ""  # "settlement_capture" | "shoulder_sell" | "center_buy" | "opening_inertia"
    edge_source: str = ""
    discovery_mode: str = ""
    market_hours_open: float = 0.0
    fill_quality: float = 0.0  # (exec_price - vwmp) / vwmp

    # Lifecycle state (Blueprint v2 §2)
    state: str = "holding"  # pending | entered | holding | day0_window | settled | voided | admin_closed
    exit_strategy: str = ""  # "buy_yes_standard" | "buy_no_conservative" (set from direction)

    # Chain reconciliation (Blueprint v2 §5)
    chain_state: str = "unknown"  # unknown | synced | local_only | chain_only | quarantined
    chain_shares: float = 0.0
    chain_verified_at: str = ""

    # Token IDs for CLOB orderbook queries
    token_id: str = ""
    no_token_id: str = ""
    condition_id: str = ""

    # Exit state (persisted across monitor cycles — Blueprint v2 §7)
    neg_edge_count: int = 0
    last_monitor_prob: float = 0.0
    last_monitor_edge: float = 0.0
    last_monitor_at: str = ""
    cal_std: float = 3.0  # Calibration std for this city-season (default °F)
    city_peak_hour: float = 15.0  # For Day0 phase detection

    # Anti-churn
    last_exit_at: str = ""
    exit_reason: str = ""
    admin_exit_reason: str = ""  # Separate from economic exit_reason

    # P&L (set on close)
    exit_price: float = 0.0
    pnl: float = 0.0

    def evaluate_exit(
        self,
        current_p_posterior: float,
        current_p_market: float,
        hours_to_settlement: Optional[float] = None,
        is_whale_sweep: bool = False,
        best_bid: Optional[float] = None,
        market_vig: float = 1.0,
    ) -> ExitDecision:
        """Position knows how to exit ITSELF. Monitor just calls this.

        All probabilities in native space (same as entry).
        """
        # Settlement imminent
        if hours_to_settlement is not None and hours_to_settlement < 1.0:
            return ExitDecision(True, "SETTLEMENT_IMMINENT", "immediate")

        # Whale toxicity
        if is_whale_sweep:
            return ExitDecision(True, "WHALE_TOXICITY", "immediate")

        # Micro-position hold (Layer 8: < $1 never sold)
        if self.size_usd < 1.0:
            return ExitDecision(False)

        # Vig extreme
        if market_vig > 1.08 or market_vig < 0.92:
            return ExitDecision(True, f"VIG_EXTREME (vig={market_vig:.3f})")

        # Direction-specific exit logic
        forward_edge = current_p_posterior - current_p_market

        if self.direction == "buy_no":
            return self._buy_no_exit(forward_edge, hours_to_settlement)
        else:
            return self._buy_yes_exit(forward_edge, best_bid)

    def _buy_yes_exit(
        self, forward_edge: float, best_bid: Optional[float] = None
    ) -> ExitDecision:
        """Standard 2-consecutive EDGE_REVERSAL with EV gate."""
        if forward_edge >= 0:
            self.neg_edge_count = 0
            return ExitDecision(False)

        self.neg_edge_count += 1
        if self.neg_edge_count < 2:
            return ExitDecision(False)

        # Layer 4: EV gate
        if best_bid is not None and self.entry_price > 0:
            shares = self.size_usd / self.entry_price
            if shares * best_bid <= shares * self.p_posterior:
                return ExitDecision(False)  # Selling worse than holding

        self.neg_edge_count = 0
        return ExitDecision(True, f"EDGE_REVERSAL (edge={forward_edge:.4f})")

    def _buy_no_exit(
        self, forward_edge: float, hours_to_settlement: Optional[float] = None
    ) -> ExitDecision:
        """Layer 1: Buy-no has ~87.5% base win rate. Different exit math.
        Blueprint v2 §7: threshold scales with cal_std (noisier cities need deeper reversal).
        """
        edge_threshold = -self.cal_std * 0.015  # Default: std=3.0°F → -0.045

        # Near-settlement hold (unless deeply negative)
        if hours_to_settlement is not None and hours_to_settlement < 4.0:
            if forward_edge < -0.20:
                return ExitDecision(True, f"BUY_NO_NEAR_EXIT (edge={forward_edge:.4f})")
            return ExitDecision(False)

        if forward_edge < edge_threshold:
            self.neg_edge_count += 1
        else:
            self.neg_edge_count = 0

        if self.neg_edge_count >= 2:
            self.neg_edge_count = 0
            return ExitDecision(True, f"BUY_NO_EDGE_EXIT (edge={forward_edge:.4f})")

        return ExitDecision(False)

    @property
    def is_admin_exit(self) -> bool:
        return (self.admin_exit_reason != ""
                or self.exit_reason in ADMIN_EXITS)


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
    """Add a position. Dedup: merge if same token+direction already open."""
    tid = pos.token_id if pos.direction == "buy_yes" else pos.no_token_id
    for existing in state.positions:
        existing_tid = existing.token_id if existing.direction == "buy_yes" else existing.no_token_id
        if tid and existing_tid == tid and existing.direction == pos.direction:
            # Merge: accumulate shares and cost
            logger.warning("DEDUP: merging duplicate %s %s into existing %s",
                           pos.direction, pos.bin_label, existing.trade_id)
            existing.size_usd += pos.size_usd
            existing.shares += pos.shares
            existing.cost_basis_usd += pos.cost_basis_usd
            return
    state.positions.append(pos)


def close_position(
    state: PortfolioState, trade_id: str,
    exit_price: float, exit_reason: str,
) -> Optional[Position]:
    """Close a position with known exit price. Computes P&L.

    L4: closes ALL same-token positions (handles GHOST_DUPLICATE).
    """
    now = datetime.now(timezone.utc).isoformat()
    closed = None

    for i, p in enumerate(list(state.positions)):
        if p.trade_id == trade_id:
            pos = state.positions.pop(state.positions.index(p))
            pos.state = "settled"
            pos.exit_price = exit_price
            pos.exit_reason = exit_reason
            pos.last_exit_at = now
            # P&L: (exit - entry) * shares
            if pos.entry_price > 0:
                shares = pos.size_usd / pos.entry_price
                pos.pnl = round(shares * exit_price - pos.size_usd, 2)
            _track_exit(state, pos)
            closed = pos

    return closed


def void_position(
    state: PortfolioState, trade_id: str, reason: str,
) -> Optional[Position]:
    """Close with pnl=0 when real exit price is unknown. L3.

    Use for: UNFILLED_ORDER, SETTLED_NOT_IN_API, EXIT_FAILED.
    Does NOT affect loss counters (admin exit).
    """
    for i, p in enumerate(state.positions):
        if p.trade_id == trade_id:
            pos = state.positions.pop(i)
            pos.state = "voided"
            pos.exit_reason = reason
            pos.exit_price = 0.0
            pos.pnl = 0.0
            pos.last_exit_at = datetime.now(timezone.utc).isoformat()
            _track_exit(state, pos)
            return pos
    return None


def remove_position(
    state: PortfolioState, trade_id: str, exit_reason: str = ""
) -> Optional[Position]:
    """Legacy remove. Delegates to close_position with entry_price as exit."""
    for p in state.positions:
        if p.trade_id == trade_id:
            return close_position(state, trade_id, p.entry_price, exit_reason)
    return None


def _track_exit(state: PortfolioState, pos: Position) -> None:
    """Track exit for reentry/cooldown checks (Layers 5+6)."""
    state.recent_exits.append({
        "city": pos.city, "bin_label": pos.bin_label,
        "target_date": pos.target_date, "direction": pos.direction,
        "token_id": pos.token_id, "no_token_id": pos.no_token_id,
        "exit_reason": pos.exit_reason,
        "exited_at": pos.last_exit_at,
        "pnl": pos.pnl,
    })
    if len(state.recent_exits) > 50:
        state.recent_exits = state.recent_exits[-50:]


def realized_pnl(state: PortfolioState, exclude_admin: bool = True) -> float:
    """Total realized P&L from recent exits. L2: excludes admin exits."""
    total = 0.0
    for ex in state.recent_exits:
        pnl = ex.get("pnl", 0.0)
        if exclude_admin and ex.get("exit_reason") in ADMIN_EXITS:
            continue
        total += pnl
    return total


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
