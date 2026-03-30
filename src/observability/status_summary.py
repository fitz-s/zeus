"""Status summary: written every cycle. Zeus is not a black box.

Blueprint v2 §10: 5-section health snapshot.
Written to state/status_summary.json for Venus/OpenClaw to read.
"""

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from src.config import STATE_DIR, settings
from src.state.portfolio import PortfolioState, load_portfolio, portfolio_heat

logger = logging.getLogger(__name__)

STATUS_PATH = STATE_DIR / "status_summary.json"


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
            "level": "GREEN",  # Will be updated by RiskGuard
        },
        "portfolio": {
            "open_positions": len(portfolio.positions),
            "total_exposure_usd": round(sum(p.size_usd for p in portfolio.positions), 2),
            "heat_pct": round(portfolio_heat(portfolio) * 100, 1),
            "bankroll": portfolio.bankroll,
            "positions": [
                {"trade_id": p.trade_id, "city": p.city, "direction": p.direction,
                 "size": p.size_usd, "edge": p.edge, "bin": p.bin_label[:30]}
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
