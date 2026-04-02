#!/usr/bin/env python3
"""Counterfactual audit for the new divergence rule against historical exits."""

from __future__ import annotations

import json
import re
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.state.portfolio import divergence_hard_threshold, divergence_soft_threshold, divergence_velocity_confirm

POSITIONS_PATH = PROJECT_ROOT / "state" / "positions-paper.json"
DB_PATH = PROJECT_ROOT / "state" / "zeus.db"
SCORE_RE = re.compile(r"Model-Market divergence score ([0-9.]+)")


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _native_price(direction: str, yes_price: float) -> float:
    return yes_price if direction == "buy_yes" else 1.0 - yes_price


def _velocity_1h(conn, token_id: str, direction: str, when: datetime) -> float | None:
    current = conn.execute(
        """
        SELECT price, timestamp
        FROM token_price_log
        WHERE token_id = ? AND datetime(timestamp) >= datetime(?)
        ORDER BY datetime(timestamp) ASC
        LIMIT 1
        """,
        (token_id, when.isoformat()),
    ).fetchone()
    previous = conn.execute(
        """
        SELECT price, timestamp
        FROM token_price_log
        WHERE token_id = ? AND datetime(timestamp) <= datetime(?)
        ORDER BY datetime(timestamp) DESC
        LIMIT 1
        """,
        (token_id, (when - timedelta(hours=1)).isoformat()),
    ).fetchone()
    if current is None or previous is None:
        return None
    return _native_price(direction, float(current["price"])) - _native_price(direction, float(previous["price"]))


def run_audit() -> dict:
    state = json.loads(POSITIONS_PATH.read_text())
    exits = [
        ex
        for ex in state.get("recent_exits", [])
        if not str(ex.get("market_id", "")).startswith("mock_")
        and str(ex.get("exit_reason", "")).startswith("Model-Market divergence score")
    ]

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    analyzed = []
    triggered = 0
    by_bucket = defaultdict(lambda: {"count": 0, "would_trigger": 0})
    for ex in exits:
        reason = str(ex.get("exit_reason", ""))
        match = SCORE_RE.search(reason)
        exit_dt = _parse_ts(ex.get("exited_at"))
        token_id = ex.get("token_id")
        direction = ex.get("direction", "buy_yes")
        if match is None or exit_dt is None or not token_id:
            continue

        score = float(match.group(1))
        velocity = _velocity_1h(conn, token_id, direction, exit_dt)
        would_trigger = False
        if score >= divergence_hard_threshold():
            would_trigger = True
        elif score >= divergence_soft_threshold() and velocity is not None and velocity <= divergence_velocity_confirm():
            would_trigger = True

        bucket = f"{score:.2f}"
        by_bucket[bucket]["count"] += 1
        if would_trigger:
            by_bucket[bucket]["would_trigger"] += 1
            triggered += 1

        analyzed.append(
            {
                "trade_id": ex.get("trade_id"),
                "city": ex.get("city"),
                "strategy": ex.get("strategy"),
                "score": score,
                "velocity_1h": round(velocity, 4) if velocity is not None else None,
                "would_trigger_under_new_rule": would_trigger,
                "pnl": float(ex.get("pnl", 0.0) or 0.0),
            }
        )

    conn.close()
    return {
        "historical_divergence_exits": len(analyzed),
        "would_still_trigger_under_new_rule": triggered,
        "retention_pct": round(triggered / max(1, len(analyzed)) * 100, 1),
        "thresholds": {
            "soft": divergence_soft_threshold(),
            "hard": divergence_hard_threshold(),
            "velocity_confirm": divergence_velocity_confirm(),
        },
        "by_score_bucket": {
            key: {
                "count": payload["count"],
                "would_trigger": payload["would_trigger"],
            }
            for key, payload in sorted(by_bucket.items(), key=lambda item: float(item[0]))
        },
        "sample": analyzed[:20],
    }


if __name__ == "__main__":
    print(json.dumps(run_audit(), ensure_ascii=False, indent=2))
