#!/usr/bin/env python3
"""Audit divergence-trigger exits against realized paper PnL."""

from __future__ import annotations

import json
import re
import sys
from collections import defaultdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.state.portfolio import divergence_hard_threshold, divergence_soft_threshold

POSITIONS_PATH = PROJECT_ROOT / "state" / "positions-paper.json"
SCORE_RE = re.compile(r"Model-Market divergence score ([0-9.]+) exceeds threshold")


def run_audit() -> dict:
    state = json.loads(POSITIONS_PATH.read_text())
    exits = [r for r in state.get("recent_exits", []) if not str(r.get("market_id", "")).startswith("mock_")]
    divergence = []
    for ex in exits:
        reason = ex.get("exit_reason", "")
        match = SCORE_RE.search(reason)
        if not match:
            continue
        score = float(match.group(1))
        divergence.append(
            {
                "trade_id": ex.get("trade_id"),
                "city": ex.get("city"),
                "strategy": ex.get("strategy"),
                "score": score,
                "pnl": float(ex.get("pnl", 0.0) or 0.0),
                "market_hours_open": ex.get("market_hours_open"),
                "bin_label": ex.get("bin_label"),
            }
        )

    by_score = defaultdict(lambda: {"count": 0, "sum_pnl": 0.0})
    by_strategy = defaultdict(lambda: {"count": 0, "sum_pnl": 0.0})
    for row in divergence:
        score_key = f"{row['score']:.2f}"
        by_score[score_key]["count"] += 1
        by_score[score_key]["sum_pnl"] += row["pnl"]
        by_strategy[row["strategy"]]["count"] += 1
        by_strategy[row["strategy"]]["sum_pnl"] += row["pnl"]

    hard_only = [row for row in divergence if row["score"] >= divergence_hard_threshold()]
    soft_or_hard_score_only = [row for row in divergence if row["score"] >= divergence_soft_threshold()]

    return {
        "total_real_recent_exits": len(exits),
        "divergence_exits": len(divergence),
        "divergence_exit_pct": round(len(divergence) / max(1, len(exits)) * 100, 1),
        "configured_thresholds": {
            "soft_threshold": divergence_soft_threshold(),
            "hard_threshold": divergence_hard_threshold(),
        },
        "historical_score_only_trigger_counts": {
            "hard_threshold_only": len(hard_only),
            "soft_or_hard_threshold_only": len(soft_or_hard_score_only),
        },
        "by_score": {
            key: {
                "count": payload["count"],
                "sum_pnl": round(payload["sum_pnl"], 2),
                "avg_pnl": round(payload["sum_pnl"] / payload["count"], 4),
            }
            for key, payload in sorted(by_score.items(), key=lambda item: float(item[0]))
        },
        "by_strategy": {
            key: {
                "count": payload["count"],
                "sum_pnl": round(payload["sum_pnl"], 2),
                "avg_pnl": round(payload["sum_pnl"] / payload["count"], 4),
            }
            for key, payload in sorted(by_strategy.items())
        },
        "worst_five": sorted(divergence, key=lambda row: row["pnl"])[:5],
        "best_five": sorted(divergence, key=lambda row: row["pnl"], reverse=True)[:5],
    }


if __name__ == "__main__":
    print(json.dumps(run_audit(), ensure_ascii=False, indent=2))
