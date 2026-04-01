#!/usr/bin/env python3
"""Audit only divergence exits that occurred after the new split-threshold rule."""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

POSITIONS_PATH = PROJECT_ROOT / "state" / "positions-paper.json"

# Timestamp after the split soft/hard divergence rule was introduced.
POSTCHANGE_CUTOFF_UTC = datetime.fromisoformat("2026-04-01T07:00:00+00:00")


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def run_audit() -> dict:
    state = json.loads(POSITIONS_PATH.read_text())
    exits = []
    for ex in state.get("recent_exits", []):
        if str(ex.get("market_id", "")).startswith("mock_"):
            continue
        if ex.get("exit_trigger") != "MODEL_DIVERGENCE_PANIC":
            continue
        exited_at = _parse_ts(ex.get("exited_at"))
        if exited_at is None or exited_at < POSTCHANGE_CUTOFF_UTC:
            continue
        exits.append(ex)

    by_strategy = defaultdict(lambda: {"count": 0, "sum_pnl": 0.0})
    by_score_bucket = defaultdict(lambda: {"count": 0, "sum_pnl": 0.0})
    for ex in exits:
        strategy = ex.get("strategy") or "missing"
        score = float(ex.get("exit_divergence_score", 0.0) or 0.0)
        pnl = float(ex.get("pnl", 0.0) or 0.0)
        by_strategy[strategy]["count"] += 1
        by_strategy[strategy]["sum_pnl"] += pnl
        bucket = f"{score:.2f}"
        by_score_bucket[bucket]["count"] += 1
        by_score_bucket[bucket]["sum_pnl"] += pnl

    return {
        "postchange_cutoff_utc": POSTCHANGE_CUTOFF_UTC.isoformat(),
        "divergence_exits_after_rule_change": len(exits),
        "by_strategy": {
            key: {
                "count": payload["count"],
                "sum_pnl": round(payload["sum_pnl"], 2),
                "avg_pnl": round(payload["sum_pnl"] / payload["count"], 4) if payload["count"] else 0.0,
            }
            for key, payload in sorted(by_strategy.items())
        },
        "by_score_bucket": {
            key: {
                "count": payload["count"],
                "sum_pnl": round(payload["sum_pnl"], 2),
                "avg_pnl": round(payload["sum_pnl"] / payload["count"], 4) if payload["count"] else 0.0,
            }
            for key, payload in sorted(by_score_bucket.items(), key=lambda item: float(item[0]))
        },
        "sample": [
            {
                "trade_id": ex.get("trade_id"),
                "city": ex.get("city"),
                "strategy": ex.get("strategy"),
                "exit_divergence_score": ex.get("exit_divergence_score"),
                "exit_market_velocity_1h": ex.get("exit_market_velocity_1h"),
                "exit_forward_edge": ex.get("exit_forward_edge"),
                "pnl": ex.get("pnl"),
                "exited_at": ex.get("exited_at"),
            }
            for ex in exits[:20]
        ],
    }


if __name__ == "__main__":
    print(json.dumps(run_audit(), ensure_ascii=False, indent=2))
