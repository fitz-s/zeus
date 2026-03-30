"""Portfolio state management. Spec §6.4.

Atomic JSON + SQL mirror. Positions are the source of truth.
Provides exposure queries for risk limit enforcement.
"""

import json
import logging
import os
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from src.config import STATE_DIR

logger = logging.getLogger(__name__)

POSITIONS_PATH = STATE_DIR / "positions.json"


@dataclass
class Position:
    trade_id: str
    market_id: str
    city: str
    cluster: str
    target_date: str
    bin_label: str
    direction: str  # "buy_yes" or "buy_no"
    size_usd: float
    entry_price: float
    p_posterior: float
    edge: float
    entered_at: str
    # Attribution (CLAUDE.md mandatory)
    edge_source: str = ""
    discovery_mode: str = ""
    market_hours_open: float = 0.0


@dataclass
class PortfolioState:
    positions: list[Position] = field(default_factory=list)
    bankroll: float = 150.0
    updated_at: str = ""


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


def remove_position(state: PortfolioState, trade_id: str) -> Optional[Position]:
    """Remove a position by trade_id. Returns the removed position or None."""
    for i, p in enumerate(state.positions):
        if p.trade_id == trade_id:
            return state.positions.pop(i)
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
