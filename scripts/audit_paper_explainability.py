#!/usr/bin/env python3
"""Audit current paper-trading explainability from positions and recent exits."""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

POSITIONS_PATH = PROJECT_ROOT / "state" / "positions-paper.json"


def run_audit() -> dict:
    if not POSITIONS_PATH.exists():
        return {"error": "positions-paper.json missing"}

    state = json.loads(POSITIONS_PATH.read_text())
    positions = state.get("positions", [])
    recent_exits = state.get("recent_exits", [])
    real_recent_exits = [ex for ex in recent_exits if not str(ex.get("market_id", "")).startswith("mock_")]

    def has_semantic_snapshots(row: dict) -> bool:
        return bool(
            row.get("settlement_semantics_json")
            and row.get("epistemic_context_json")
            and row.get("edge_context_json")
        )

    strategy_realized = defaultdict(float)
    exit_reason_pnl = defaultdict(float)
    for ex in real_recent_exits:
        strategy_realized[ex.get("strategy") or "missing"] += float(ex.get("pnl", 0.0) or 0.0)
        exit_reason_pnl[ex.get("exit_reason") or "missing"] += float(ex.get("pnl", 0.0) or 0.0)

    unexplained_exits = []
    for ex in real_recent_exits:
        missing = [
            field
            for field in [
                "strategy",
                "discovery_mode",
                "entry_method",
                "selected_method",
                "decision_snapshot_id",
                "exit_reason",
            ]
            if not ex.get(field)
        ]
        if ex.get("market_hours_open") is None:
            missing.append("market_hours_open")
        if ex.get("fill_quality") is None:
            missing.append("fill_quality")
        if not has_semantic_snapshots(ex):
            missing.append("semantic_snapshots")
        if missing:
            unexplained_exits.append(
                {
                    "trade_id": ex.get("trade_id"),
                    "market_id": ex.get("market_id"),
                    "strategy": ex.get("strategy"),
                    "pnl": ex.get("pnl"),
                    "missing": missing,
                }
            )

    open_position_blind_spots = [
        {
            "trade_id": pos.get("trade_id"),
            "market_id": pos.get("market_id"),
            "strategy": pos.get("strategy"),
            "missing": [
                field
                for field in [
                    "strategy",
                    "discovery_mode",
                    "entry_method",
                    "selected_method",
                    "decision_snapshot_id",
                    "market_hours_open",
                    "fill_quality",
                ]
                if not pos.get(field) and pos.get(field) != 0.0
            ],
        }
        for pos in positions
        if any(
            not pos.get(field) and pos.get(field) != 0.0
            for field in [
                "strategy",
                "discovery_mode",
                "entry_method",
                "selected_method",
                "decision_snapshot_id",
                "market_hours_open",
                "fill_quality",
            ]
        )
    ]

    ranked_exits = sorted(real_recent_exits, key=lambda ex: float(ex.get("pnl", 0.0) or 0.0))
    return {
        "recent_exit_summary": {
            "recent_exits_total": len(recent_exits),
            "recent_exits_non_mock": len(real_recent_exits),
            "recent_exits_with_strategy": sum(1 for ex in real_recent_exits if ex.get("strategy")),
            "recent_exits_with_selected_method": sum(1 for ex in real_recent_exits if ex.get("selected_method")),
            "recent_exits_with_market_hours_open": sum(1 for ex in real_recent_exits if ex.get("market_hours_open") is not None),
            "recent_exits_with_fill_quality": sum(1 for ex in real_recent_exits if ex.get("fill_quality") is not None),
            "recent_exits_with_semantic_snapshots": sum(1 for ex in real_recent_exits if has_semantic_snapshots(ex)),
            "recent_exits_unexplained": len(unexplained_exits),
        },
        "open_position_summary": {
            "open_positions_total": len(positions),
            "open_positions_with_semantic_snapshots": sum(1 for pos in positions if has_semantic_snapshots(pos)),
            "open_positions_with_market_hours_open": sum(1 for pos in positions if pos.get("market_hours_open") is not None),
            "open_positions_with_fill_quality": sum(1 for pos in positions if pos.get("fill_quality") is not None),
            "open_positions_with_selected_method": sum(1 for pos in positions if pos.get("selected_method")),
            "open_positions_with_blind_spots": len(open_position_blind_spots),
        },
        "strategy_realized_pnl": dict(sorted(strategy_realized.items(), key=lambda item: item[1], reverse=True)),
        "exit_reason_pnl": dict(sorted(exit_reason_pnl.items(), key=lambda item: item[1], reverse=True)),
        "top_losses": ranked_exits[:5],
        "top_wins": list(reversed(ranked_exits[-5:])),
        "unexplained_exits": unexplained_exits[:20],
        "open_position_blind_spots": open_position_blind_spots[:20],
    }


if __name__ == "__main__":
    print(json.dumps(run_audit(), ensure_ascii=False, indent=2))
