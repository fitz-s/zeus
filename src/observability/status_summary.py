"""Status summary: written every cycle. Zeus is not a black box.

Blueprint v2 §10: 5-section health snapshot.
Written to state/status_summary.json for Venus/OpenClaw to read.
"""

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from src.config import STATE_DIR, settings, state_path
from src.state.portfolio import PortfolioState, load_portfolio, portfolio_heat

logger = logging.getLogger(__name__)

STATUS_PATH = state_path("status_summary.json")


def _get_risk_level() -> str:
    """Read actual RiskGuard level instead of hardcoding GREEN."""
    try:
        from src.riskguard.riskguard import get_current_level
        return get_current_level().value
    except Exception:
        return "UNKNOWN"


def write_status(cycle_summary: dict = None) -> None:
    """Write 5-section health snapshot."""
    portfolio = load_portfolio()

    status = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "process": {
            "pid": os.getpid(),
            "mode": settings.mode,
            "version": "zeus_v2",
        },
        "risk": {
            "level": _get_risk_level(),
        },
        "portfolio": {
            "open_positions": len(portfolio.positions),
            "total_exposure_usd": round(sum(p.size_usd for p in portfolio.positions), 2),
            "heat_pct": round(portfolio_heat(portfolio) * 100, 1),
            "initial_bankroll": round(portfolio.initial_bankroll, 2),
            "realized_pnl": round(portfolio.realized_pnl, 2),
            "unrealized_pnl": round(portfolio.total_unrealized_pnl, 2),
            "total_pnl": round(portfolio.total_pnl, 2),
            "effective_bankroll": round(portfolio.effective_bankroll, 2),
            "bankroll": round(portfolio.effective_bankroll, 2),
            "positions": [
                {
                    "trade_id": p.trade_id,
                    "city": p.city,
                    "direction": p.direction,
                    "strategy": p.strategy,
                    "state": p.state,
                    "size_usd": p.size_usd,
                    "shares": p.effective_shares,
                    "entry_price": p.entry_price,
                    "edge": p.edge,
                    "bin_label": p.bin_label,
                    "decision_snapshot_id": p.decision_snapshot_id,
                    "mark_price": p.last_monitor_market_price,
                    "unrealized_pnl": round(p.unrealized_pnl, 2),
                }
                for p in portfolio.positions
            ],
        },
        "cycle": cycle_summary or {},
    }

    # Atomic write
    import tempfile
    fd, tmp = tempfile.mkstemp(dir=str(STATUS_PATH.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(status, f, indent=2)
        os.replace(tmp, str(STATUS_PATH))
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
