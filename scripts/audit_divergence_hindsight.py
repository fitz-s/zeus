#!/usr/bin/env python3
"""Audit whether divergence-trigger exits were early or timely.

For each real recent exit triggered by MODEL_DIVERGENCE_PANIC, compare realized
exit price against later held-side prices from token_price_log.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

POSITIONS_PATH = PROJECT_ROOT / "state" / "positions-paper.json"
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


def _find_price_at_or_after(conn, token_id: str, when: datetime) -> tuple[float, str] | None:
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

    horizons = {
        "plus_1h": timedelta(hours=1),
        "plus_4h": timedelta(hours=4),
        "plus_12h": timedelta(hours=12),
    }

    analyzed = []
    aggregate = {key: {"count": 0, "sum_native_delta": 0.0, "sum_pnl_delta": 0.0} for key in horizons}
    aggregate["last_tick"] = {"count": 0, "sum_native_delta": 0.0, "sum_pnl_delta": 0.0}

    for ex in exits:
        exit_dt = _parse_ts(ex.get("exited_at"))
        token_id = ex.get("token_id")
        if exit_dt is None or not token_id:
            continue

        shares = 0.0
        entry_price = float(ex.get("entry_price", 0.0) or 0.0)
        size_usd = float(ex.get("size_usd", 0.0) or 0.0)
        if entry_price > 0:
            shares = size_usd / entry_price

        realized_native = float(ex.get("exit_price", 0.0) or 0.0)
        direction = ex.get("direction", "buy_yes")
        row_payload = {
            "trade_id": ex.get("trade_id"),
            "city": ex.get("city"),
            "strategy": ex.get("strategy"),
            "exit_reason": ex.get("exit_reason"),
            "realized_pnl": float(ex.get("pnl", 0.0) or 0.0),
            "realized_native_price": realized_native,
        }

        for label, delta in horizons.items():
            probe = _find_price_at_or_after(conn, token_id, exit_dt + delta)
            if probe is None:
                continue
            yes_price, ts = probe
            native = _native_price(direction, yes_price)
            pnl = round(shares * native - size_usd, 2) if shares > 0 else 0.0
            row_payload[label] = {
                "timestamp": ts,
                "native_price": native,
                "native_delta": round(native - realized_native, 4),
                "pnl_delta": round(pnl - float(ex.get("pnl", 0.0) or 0.0), 2),
            }
            aggregate[label]["count"] += 1
            aggregate[label]["sum_native_delta"] += native - realized_native
            aggregate[label]["sum_pnl_delta"] += pnl - float(ex.get("pnl", 0.0) or 0.0)

        last_tick = conn.execute(
            """
            SELECT price, timestamp
            FROM token_price_log
            WHERE token_id = ?
            ORDER BY datetime(timestamp) DESC
            LIMIT 1
            """,
            (token_id,),
        ).fetchone()
        if last_tick is not None:
            native = _native_price(direction, float(last_tick["price"]))
            pnl = round(shares * native - size_usd, 2) if shares > 0 else 0.0
            row_payload["last_tick"] = {
                "timestamp": last_tick["timestamp"],
                "native_price": native,
                "native_delta": round(native - realized_native, 4),
                "pnl_delta": round(pnl - float(ex.get("pnl", 0.0) or 0.0), 2),
            }
            aggregate["last_tick"]["count"] += 1
            aggregate["last_tick"]["sum_native_delta"] += native - realized_native
            aggregate["last_tick"]["sum_pnl_delta"] += pnl - float(ex.get("pnl", 0.0) or 0.0)

        analyzed.append(row_payload)

    conn.close()

    summary = {}
    for label, payload in aggregate.items():
        count = payload["count"]
        summary[label] = {
            "count": count,
            "avg_native_delta": round(payload["sum_native_delta"] / count, 4) if count else None,
            "avg_pnl_delta": round(payload["sum_pnl_delta"] / count, 2) if count else None,
        }

    return {
        "divergence_exits_analyzed": len(analyzed),
        "aggregate": summary,
        "sample": analyzed[:10],
    }


if __name__ == "__main__":
    print(json.dumps(run_audit(), ensure_ascii=False, indent=2))
