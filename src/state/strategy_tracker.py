"""Derived strategy attribution tracking.

This file is an attribution surface only. It is not runtime authority and it is
not RiskGuard input authority.
"""

import logging
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

import numpy as np

from src.config import STATE_DIR, state_path
from src.state.truth_files import annotate_truth_payload

logger = logging.getLogger(__name__)

STRATEGIES = ["settlement_capture", "shoulder_sell", "center_buy", "opening_inertia"]
TRACKER_PATH = state_path("strategy_tracker.json")
_TRACKER_SINGLETON: "StrategyTracker | None" = None


def _default_accounting(path: Path | None = None) -> dict[str, Any]:
    status_path = state_path("status_summary.json")
    return {
        "accounting_scope": "current_regime",
        "performance_headline_authority": str(status_path),
        "tracker_role": "attribution_surface",
        "includes_legacy_history": False,
        "current_regime_started_at": "",
        "history_archive_path": "",
    }


class StrategyMetrics:
    """Metrics for a single strategy."""

    def __init__(self):
        self.trades: list[dict] = []

    def record(self, trade: dict) -> None:
        trade_id = trade.get("trade_id")
        if trade_id:
            for existing in self.trades:
                if existing.get("trade_id") == trade_id:
                    existing.update(trade)
                    return
        self.trades.append(dict(trade))

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

    def count(self) -> int:
        return len(self.trades)

    def to_dict(self) -> dict:
        return {"trades": list(self.trades)}

    @classmethod
    def from_dict(cls, data: dict) -> "StrategyMetrics":
        inst = cls()
        inst.trades = list(data.get("trades", []))
        return inst


class StrategyTracker:
    """Track all four strategies independently."""

    def __init__(self):
        self.strategies: dict[str, StrategyMetrics] = {
            s: StrategyMetrics() for s in STRATEGIES
        }
        self.accounting: dict[str, Any] = _default_accounting()

    def record_trade(self, trade: dict) -> None:
        strategy = trade.get("strategy") or trade.get("edge_source", "")
        if not strategy:
            return
        if strategy not in self.strategies:
            strategy = "opening_inertia"  # Default bucket
        self.strategies[strategy].record(trade)

    def record_entry(self, position: Any) -> None:
        self.record_trade(_position_like_payload(position, status="entered"))

    def record_exit(self, position: Any) -> None:
        self.record_trade(_position_like_payload(position, status="exited"))

    def record_settlement(self, position: Any) -> None:
        self.record_trade(_position_like_payload(position, status="settled"))

    def record_chronicle_event(self, event_type: str, details: dict) -> None:
        """Record a chronicle event without re-deriving attribution downstream."""
        if event_type not in {"SETTLEMENT", "EXIT", "ENTRY"}:
            return
        self.record_trade({
            "strategy": details.get("strategy", ""),
            "edge_source": details.get("edge_source", ""),
            "pnl": details.get("pnl"),
            "status": details.get("status", "filled"),
            "entered_at": details.get("entered_at", ""),
            "edge": details.get("edge", 0.0),
        })

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
                "pnl": round(m.cumulative_pnl(), 2),
            }
            for name, m in self.strategies.items()
        }

    def set_accounting_metadata(
        self,
        *,
        current_regime_started_at: str = "",
        includes_legacy_history: bool = False,
        history_archive_path: str = "",
    ) -> None:
        self.accounting = _default_accounting()
        self.accounting.update({
            "current_regime_started_at": current_regime_started_at,
            "includes_legacy_history": includes_legacy_history,
            "history_archive_path": history_archive_path,
        })

    def to_dict(self) -> dict:
        return {
            "strategies": {
                name: metrics.to_dict()
                for name, metrics in self.strategies.items()
            },
            "accounting": dict(self.accounting),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "StrategyTracker":
        tracker = cls()
        strategies = data.get("strategies", {})
        for name in STRATEGIES:
            tracker.strategies[name] = StrategyMetrics.from_dict(strategies.get(name, {}))
        accounting = data.get("accounting", {})
        if isinstance(accounting, dict):
            tracker.accounting.update(accounting)
        return tracker


def _position_like_payload(position: Any, *, status: str) -> dict:
    getter = position.get if isinstance(position, dict) else lambda key, default=None: getattr(position, key, default)
    return {
        "trade_id": getter("trade_id", "") or getter("token_id", ""),
        "strategy": getter("strategy", ""),
        "edge_source": getter("edge_source", ""),
        "pnl": getter("pnl", None),
        "status": status,
        "entered_at": getter("entered_at", "") or getter("opened_at", ""),
        "edge": getter("edge", 0.0),
        "direction": getter("direction", ""),
        "city": getter("city", ""),
        "target_date": getter("target_date", ""),
    }


def load_tracker(path: Optional[Path] = None) -> StrategyTracker:
    path = path or TRACKER_PATH
    if not path.exists():
        return StrategyTracker()
    try:
        import json

        with open(path) as f:
            data = json.load(f)
        if data.get("truth", {}).get("deprecated") is True:
            raise RuntimeError(
                f"{path} is a deprecated legacy truth file. "
                "Use the mode-suffixed strategy tracker instead."
            )
        return StrategyTracker.from_dict(data)
    except RuntimeError:
        raise
    except Exception as exc:
        logger.warning("Strategy tracker load failed: %s", exc)
        return StrategyTracker()


def save_tracker(tracker: StrategyTracker, path: Optional[Path] = None) -> None:
    path = path or TRACKER_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    import json
    generated_at = datetime.now(timezone.utc).isoformat()
    payload = annotate_truth_payload(
        tracker.to_dict(),
        path,
        generated_at=generated_at,
    )

    fd, tmp_path = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(payload, f, indent=2)
        os.replace(tmp_path, str(path))
    except Exception:
        os.unlink(tmp_path)
        raise


def get_tracker() -> StrategyTracker:
    global _TRACKER_SINGLETON
    if _TRACKER_SINGLETON is None:
        _TRACKER_SINGLETON = load_tracker()
    return _TRACKER_SINGLETON
