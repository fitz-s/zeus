#!/usr/bin/env python3
# Lifecycle: created=2026-04-30; last_reviewed=2026-04-30; last_reused=2026-04-30
# Purpose: Rebuild the compatibility strategy tracker from current truth surfaces.
# Reuse: Run after truth-file helper or tracker compatibility-surface changes.
"""Rebuild strategy tracker from current truth surfaces and archive mixed history."""

from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.config import runtime_state_path
from src.state.strategy_tracker import StrategyTracker, load_tracker, save_tracker
from src.state.truth_files import current_runtime_state, read_runtime_truth_json


def _history_path() -> Path:
    return PROJECT_ROOT / "state" / "strategy_tracker-live-history.json"


def _collect_regime_start(portfolio: dict) -> str:
    timestamps: list[str] = []
    for row in portfolio.get("positions", []):
        if row.get("entered_at"):
            timestamps.append(row["entered_at"])
    for row in portfolio.get("recent_exits", []):
        if str(row.get("market_id", "")).startswith("mock_"):
            continue
        if row.get("entered_at"):
            timestamps.append(row["entered_at"])
        if row.get("exited_at"):
            timestamps.append(row["exited_at"])
    return min(timestamps) if timestamps else ""


def run() -> dict:
    runtime_state = current_runtime_state()
    tracker_path = runtime_state_path("strategy_tracker.json")
    history_path = _history_path()

    existing_raw = json.loads(tracker_path.read_text()) if tracker_path.exists() else None
    if existing_raw is not None:
        history_payload = dict(existing_raw)
        history_accounting = history_payload.get("accounting", {})
        if not isinstance(history_accounting, dict):
            history_accounting = {}
        history_accounting.update({
            "accounting_scope": "full_history_archive",
            "tracker_role": "history_archive",
            "authority_mode": "non_authority_compatibility",
            "includes_legacy_history": True,
            "history_archive_path": str(history_path),
        })
        history_payload["accounting"] = history_accounting
        history_path.write_text(json.dumps(history_payload, ensure_ascii=False, indent=2))

    portfolio, _truth = read_runtime_truth_json("positions.json")
    tracker = StrategyTracker()

    for row in portfolio.get("positions", []):
        tracker.record_trade({**row, "status": row.get("state", "entered")})
    for row in portfolio.get("recent_exits", []):
        if str(row.get("market_id", "")).startswith("mock_"):
            continue
        tracker.record_trade(row)

    tracker.set_accounting_metadata(
        current_regime_started_at=_collect_regime_start(portfolio),
        includes_legacy_history=False,
        history_archive_path=str(history_path) if existing_raw is not None else "",
    )
    save_tracker(tracker, tracker_path)

    counts = {name: metrics.count() for name, metrics in tracker.strategies.items()}
    return {
        "runtime_state": runtime_state,
        "tracker_path": str(tracker_path),
        "history_archive_path": str(history_path) if existing_raw is not None else None,
        "current_regime_started_at": tracker.accounting["current_regime_started_at"],
        "includes_legacy_history": tracker.accounting["includes_legacy_history"],
        "strategy_trade_counts": counts,
        "total_trades": sum(counts.values()),
    }


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=2))
