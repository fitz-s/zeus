"""Per-strategy P&L tracking. Document 5: F3.

Four strategies, independently tracked. RiskGuard monitors per-strategy,
not per-portfolio. Strategy C's degradation should NOT halt Strategy A.
"""

import json
import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import numpy as np

from src.config import STATE_DIR

logger = logging.getLogger(__name__)

STRATEGIES = ["settlement_capture", "shoulder_sell", "center_buy", "opening_inertia"]


class StrategyMetrics:
    """Metrics for a single strategy."""

    def __init__(self):
        self.trades: list[dict] = []

    def record(self, trade: dict) -> None:
        self.trades.append(trade)

    def win_rate(self) -> float:
        settled = [t for t in self.trades if t.get("pnl") is not None]
        if not settled:
            return 0.5
        wins = sum(1 for t in settled if t["pnl"] > 0)
        return wins / len(settled)

    def cumulative_pnl(self) -> float:
        return sum(t.get("pnl", 0) for t in self.trades if t.get("pnl") is not None)

    def edge_trend(self, window_days: int = 30) -> float:
        """Linear regression slope of edge magnitude over time. Negative = shrinking."""
        cutoff = (datetime.now(timezone.utc) - timedelta(days=window_days)).isoformat()
        recent = [t for t in self.trades if t.get("entered_at", "") >= cutoff and "edge" in t]
        if len(recent) < 5:
            return 0.0
        edges = [abs(t["edge"]) for t in recent]
        x = np.arange(len(edges))
        if np.std(x) == 0:
            return 0.0
        slope = np.polyfit(x, edges, 1)[0]
        return float(slope)

    def fill_rate(self) -> float:
        if not self.trades:
            return 1.0
        filled = sum(1 for t in self.trades if t.get("status") == "filled")
        return filled / len(self.trades)

    def count(self) -> int:
        return len(self.trades)


class StrategyTracker:
    """Track all four strategies independently."""

    def __init__(self):
        self.strategies: dict[str, StrategyMetrics] = {
            s: StrategyMetrics() for s in STRATEGIES
        }

    def record_trade(self, trade: dict) -> None:
        strategy = trade.get("strategy") or trade.get("edge_source", "")
        if strategy not in self.strategies:
            strategy = "opening_inertia"  # Default bucket
        self.strategies[strategy].record(trade)

    def edge_compression_check(self, window_days: int = 30) -> list[str]:
        """Per-strategy edge trend. Returns list of alerts."""
        alerts = []
        for name, metrics in self.strategies.items():
            if metrics.count() < 10:
                continue
            slope = metrics.edge_trend(window_days)
            if slope < -0.001:
                alerts.append(f"EDGE_COMPRESSION: {name} edge shrinking at {slope:.4f}/day")
        return alerts

    def summary(self) -> dict:
        return {
            name: {
                "trades": m.count(),
                "win_rate": round(m.win_rate(), 3),
                "pnl": round(m.cumulative_pnl(), 2),
                "fill_rate": round(m.fill_rate(), 3),
            }
            for name, m in self.strategies.items()
        }
