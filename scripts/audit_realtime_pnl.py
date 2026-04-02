#!/usr/bin/env python3
"""Audit consistency of real-time PnL surfaces.

Compares the source-of-truth portfolio state with status_summary and RiskGuard's
latest persisted details. This script is meant to be cheap enough for recurring
operator checks.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.config import settings, state_path
from src.state.portfolio import load_portfolio

STATUS_PATH = state_path("status_summary.json")
RISK_PATH = state_path("risk_state.db")


def run_audit() -> dict:
    portfolio = load_portfolio()

    source = {
        "realized_pnl": round(portfolio.realized_pnl, 2),
        "unrealized_pnl": round(portfolio.total_unrealized_pnl, 2),
        "total_pnl": round(portfolio.total_pnl, 2),
        "effective_bankroll": round(portfolio.effective_bankroll, 2),
        "open_positions": len(portfolio.positions),
        "recent_exits": len(portfolio.recent_exits),
    }

    status = {}
    if STATUS_PATH.exists():
        payload = json.loads(STATUS_PATH.read_text())
        status = payload.get("portfolio", {})

    risk = {}
    if RISK_PATH.exists():
        conn = sqlite3.connect(RISK_PATH)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT level, details_json, checked_at FROM risk_state ORDER BY checked_at DESC LIMIT 1"
        ).fetchone()
        conn.close()
        if row is not None:
            risk = json.loads(row["details_json"])
            risk["level"] = row["level"]
            risk["checked_at"] = row["checked_at"]

    comparisons = {
        "status_matches_source": {
            "realized_pnl": status.get("realized_pnl") == source["realized_pnl"],
            "unrealized_pnl": status.get("unrealized_pnl") == source["unrealized_pnl"],
            "total_pnl": status.get("total_pnl") == source["total_pnl"],
            "effective_bankroll": status.get("effective_bankroll") == source["effective_bankroll"],
            "open_positions": status.get("open_positions") == source["open_positions"],
        },
        "risk_matches_source": {
            "realized_pnl": risk.get("realized_pnl") == source["realized_pnl"],
            "unrealized_pnl": risk.get("unrealized_pnl") == source["unrealized_pnl"],
            "total_pnl": risk.get("total_pnl") == source["total_pnl"],
            "effective_bankroll": risk.get("effective_bankroll") == source["effective_bankroll"],
        },
    }

    return {
        "mode": settings.mode,
        "source": source,
        "status_portfolio": status,
        "risk_details": risk,
        "comparisons": comparisons,
    }


if __name__ == "__main__":
    print(json.dumps(run_audit(), ensure_ascii=False, indent=2))
