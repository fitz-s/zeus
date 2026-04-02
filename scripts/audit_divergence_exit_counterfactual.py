#!/usr/bin/env python3
"""Counterfactual audit for divergence-triggered exits."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.config import state_path
from src.data.market_scanner import _parse_temp_range


DB_PATH = PROJECT_ROOT / "state" / "zeus.db"


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _native_price(direction: str, yes_price: float) -> float:
    return yes_price if direction == "buy_yes" else 1.0 - yes_price


def _price_at_or_after(conn, token_id: str, when: datetime) -> tuple[float, str] | None:
    row = conn.execute(
        """
        SELECT price, timestamp
        FROM token_price_log
        WHERE token_id = ? AND datetime(timestamp) >= datetime(?)
        ORDER BY datetime(timestamp) ASC
        LIMIT 1
        """,
        (token_id, when.isoformat()),
    ).fetchone()
    if row is None:
        return None
    return float(row["price"]), row["timestamp"]


def _winning_price(conn, exit_row: dict) -> float | None:
    settlement = conn.execute(
        """
        SELECT winning_bin, settlement_value
        FROM settlements
        WHERE city = ? AND target_date = ?
        ORDER BY rowid DESC
        LIMIT 1
        """,
        (exit_row.get("city"), exit_row.get("target_date")),
    ).fetchone()
    if settlement is None:
        return None

    hit = False
    settlement_value = settlement["settlement_value"]
    bin_label = exit_row.get("bin_label", "")
    if settlement_value is not None:
        low, high = _parse_temp_range(bin_label)
        if low is None and high is not None:
            hit = settlement_value <= high
        elif high is None and low is not None:
            hit = settlement_value >= low
        elif low is not None and high is not None:
            hit = low <= settlement_value <= high
    else:
        hit = str(settlement["winning_bin"]) == str(bin_label)

    if exit_row.get("direction") == "buy_yes":
        return 1.0 if hit else 0.0
    return 0.0 if hit else 1.0


def _summarize(samples: list[float]) -> dict:
    if not samples:
        return {"count": 0, "avg": None}
    return {"count": len(samples), "avg": round(sum(samples) / len(samples), 4)}


def run_audit(mode: str = "paper") -> dict:
    positions_path = state_path("positions.json") if mode is None else PROJECT_ROOT / "state" / f"positions-{mode}.json"
    state = json.loads(positions_path.read_text())

    exits = [
        ex
        for ex in state.get("recent_exits", [])
        if ex.get("exit_trigger") == "MODEL_DIVERGENCE_PANIC"
        or str(ex.get("exit_reason", "")).startswith("Model-Market divergence score")
    ]

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    horizons = {
        "plus_1h": timedelta(hours=1),
        "plus_3h": timedelta(hours=3),
        "plus_6h": timedelta(hours=6),
    }

    aggregate = {name: [] for name in [*horizons, "settlement"]}
    by_strategy: dict[str, dict[str, list[float]]] = defaultdict(lambda: {name: [] for name in [*horizons, "settlement"]})
    coverage = {
        "with_held_token_id": 0,
        "with_any_future_tick": 0,
        "with_settlement_truth": 0,
    }
    analyzed = []

    for ex in exits:
        exit_dt = _parse_ts(ex.get("exited_at"))
        direction = ex.get("direction", "buy_yes")
        token_id = ex.get("token_id") if direction == "buy_yes" else ex.get("no_token_id")
        entry_price = float(ex.get("entry_price", 0.0) or 0.0)
        size_usd = float(ex.get("size_usd", 0.0) or 0.0)
        if exit_dt is None or entry_price <= 0 or size_usd <= 0:
            continue

        realized_pnl = float(ex.get("pnl", 0.0) or 0.0)
        shares = size_usd / entry_price
        row = {
            "trade_id": ex.get("trade_id"),
            "city": ex.get("city"),
            "strategy": ex.get("strategy"),
            "bin_label": ex.get("bin_label"),
            "direction": direction,
            "exit_divergence_score": ex.get("exit_divergence_score"),
            "actual_pnl": realized_pnl,
        }

        if token_id:
            coverage["with_held_token_id"] += 1
            saw_future_tick = False
            for label, delta in horizons.items():
                probe = _price_at_or_after(conn, token_id, exit_dt + delta)
                if probe is None:
                    continue
                saw_future_tick = True
                yes_price, ts = probe
                native = _native_price(direction, yes_price)
                pnl = round(shares * native - size_usd, 2)
                pnl_delta = round(pnl - realized_pnl, 2)
                row[label] = {"timestamp": ts, "native_price": native, "pnl": pnl, "pnl_delta": pnl_delta}
                aggregate[label].append(pnl_delta)
                by_strategy[ex.get("strategy", "")][label].append(pnl_delta)
            if saw_future_tick:
                coverage["with_any_future_tick"] += 1

        settlement_native = _winning_price(conn, ex)
        if settlement_native is not None:
            coverage["with_settlement_truth"] += 1
            settlement_pnl = round(shares * settlement_native - size_usd, 2)
            settlement_delta = round(settlement_pnl - realized_pnl, 2)
            row["settlement"] = {
                "native_price": settlement_native,
                "pnl": settlement_pnl,
                "pnl_delta": settlement_delta,
            }
            aggregate["settlement"].append(settlement_delta)
            by_strategy[ex.get("strategy", "")]["settlement"].append(settlement_delta)

        analyzed.append(row)

    conn.close()

    return {
        "mode": mode,
        "positions_source": str(positions_path),
        "divergence_exits_analyzed": len(analyzed),
        "coverage": coverage,
        "aggregate_pnl_delta": {name: _summarize(values) for name, values in aggregate.items()},
        "by_strategy_pnl_delta": {
            strategy: {name: _summarize(values) for name, values in groups.items()}
            for strategy, groups in by_strategy.items()
        },
        "sample": analyzed[:10],
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", default="paper")
    args = parser.parse_args()
    print(json.dumps(run_audit(mode=args.mode), ensure_ascii=False, indent=2))
