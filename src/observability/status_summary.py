"""Status summary: written every cycle. Zeus is not a black box.

Blueprint v2 §10: 5-section health snapshot.
Written to the mode-qualified truth file for Venus/OpenClaw to read.
"""

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from src.config import STATE_DIR, settings, state_path
from src.control.control_plane import get_edge_threshold_multiplier, is_entries_paused, strategy_gates
from src.state.db import get_connection, query_execution_event_summary
from src.state.portfolio import ADMIN_EXITS, PortfolioState, load_portfolio, portfolio_heat
from src.state.truth_files import annotate_truth_payload

logger = logging.getLogger(__name__)

STATUS_PATH = state_path("status_summary.json")


def _get_risk_level() -> str:
    """Read actual RiskGuard level instead of hardcoding GREEN."""
    try:
        from src.riskguard.riskguard import get_current_level
        return get_current_level().value
    except Exception:
        return "UNKNOWN"


def _get_risk_details() -> dict:
    try:
        import sqlite3

        conn = sqlite3.connect(str(state_path("risk_state.db")))
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT details_json FROM risk_state ORDER BY checked_at DESC LIMIT 1"
        ).fetchone()
        conn.close()
        if row is None or not row["details_json"]:
            return {}
        details = json.loads(row["details_json"])
        return details if isinstance(details, dict) else {}
    except Exception:
        return {}


def write_status(cycle_summary: dict = None) -> None:
    """Write 5-section health snapshot."""
    portfolio = load_portfolio()
    generated_at = datetime.now(timezone.utc).isoformat()
    if cycle_summary is None and STATUS_PATH.exists():
        try:
            with open(STATUS_PATH) as f:
                prior = json.load(f)
            cycle_summary = prior.get("cycle", {})
        except Exception:
            cycle_summary = {}

    strategy_summary: dict[str, dict] = {}
    for pos in portfolio.positions:
        strategy = pos.strategy or "unclassified"
        bucket = strategy_summary.setdefault(
            strategy,
            {
                "open_positions": 0,
                "open_exposure_usd": 0.0,
                "realized_pnl": 0.0,
                "unrealized_pnl": 0.0,
            },
        )
        bucket["open_positions"] += 1
        bucket["open_exposure_usd"] += float(pos.size_usd)
        bucket["unrealized_pnl"] += float(pos.unrealized_pnl)

    for exit_row in portfolio.recent_exits:
        if exit_row.get("exit_reason") in ADMIN_EXITS:
            continue
        strategy = exit_row.get("strategy") or "unclassified"
        bucket = strategy_summary.setdefault(
            strategy,
            {
                "open_positions": 0,
                "open_exposure_usd": 0.0,
                "realized_pnl": 0.0,
                "unrealized_pnl": 0.0,
            },
        )
        bucket["realized_pnl"] += float(exit_row.get("pnl", 0.0) or 0.0)

    for bucket in strategy_summary.values():
        bucket["open_exposure_usd"] = round(bucket["open_exposure_usd"], 2)
        bucket["realized_pnl"] = round(bucket["realized_pnl"], 2)
        bucket["unrealized_pnl"] = round(bucket["unrealized_pnl"], 2)
        bucket["total_pnl"] = round(bucket["realized_pnl"] + bucket["unrealized_pnl"], 2)

    chain_state_counts: dict[str, int] = {}
    exit_state_counts: dict[str, int] = {}
    for pos in portfolio.positions:
        chain_key = str(pos.chain_state or "unknown")
        chain_state_counts[chain_key] = chain_state_counts.get(chain_key, 0) + 1
        exit_key = str(pos.exit_state or "none")
        exit_state_counts[exit_key] = exit_state_counts.get(exit_key, 0) + 1

    status = {
        "timestamp": generated_at,
        "process": {
            "pid": os.getpid(),
            "mode": settings.mode,
            "version": "zeus_v2",
        },
        "control": {
            "entries_paused": is_entries_paused(),
            "edge_threshold_multiplier": get_edge_threshold_multiplier(),
            "strategy_gates": strategy_gates(),
        },
        "risk": {
            "level": _get_risk_level(),
            "details": _get_risk_details(),
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
                    "chain_state": p.chain_state,
                    "exit_state": p.exit_state,
                    "entry_fill_verified": p.entry_fill_verified,
                    "admin_exit_reason": p.admin_exit_reason,
                    "size_usd": p.size_usd,
                    "shares": p.effective_shares,
                    "entry_price": p.entry_price,
                    "edge": p.edge,
                    "bin_label": p.bin_label,
                    "decision_snapshot_id": p.decision_snapshot_id,
                    "day0_entered_at": p.day0_entered_at,
                    "mark_price": p.last_monitor_market_price,
                    "unrealized_pnl": round(p.unrealized_pnl, 2),
                }
                for p in portfolio.positions
            ],
        },
        "runtime": {
            "chain_state_counts": chain_state_counts,
            "exit_state_counts": exit_state_counts,
            "unverified_entries": sum(
                1 for pos in portfolio.positions
                if pos.state == "pending_tracked" or not pos.entry_fill_verified
            ),
            "day0_positions": sum(1 for pos in portfolio.positions if pos.state == "day0_window"),
        },
        "strategy": strategy_summary,
        "execution": {},
        "cycle": cycle_summary or {},
    }
    try:
        conn = get_connection()
        status["execution"] = query_execution_event_summary(conn)
        conn.close()
    except Exception:
        status["execution"] = {"error": "execution_summary_unavailable"}
    status = annotate_truth_payload(status, STATUS_PATH, mode=settings.mode, generated_at=generated_at)

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
