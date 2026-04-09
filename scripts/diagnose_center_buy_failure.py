#!/usr/bin/env python3
"""Reproducible diagnosis for center_buy paper-mode failure shape."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import Counter, defaultdict
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.config import STATE_DIR


DEFAULT_DB = STATE_DIR / "zeus-paper.db"
DEFAULT_POSITIONS_JSON = STATE_DIR / "positions-paper.json"


def _price_bucket(value: float | None) -> str:
    if value is None:
        return "missing"
    if value <= 0.01:
        return "<=0.01"
    if value <= 0.02:
        return "<=0.02"
    if value <= 0.05:
        return "<=0.05"
    return ">0.05"


def diagnose_center_buy(
    conn: sqlite3.Connection,
    *,
    strategy_key: str = "center_buy",
    positions_json_path: Path | None = None,
) -> dict:
    positions_json_path = positions_json_path or DEFAULT_POSITIONS_JSON

    latest_td_rows = conn.execute(
        """
        WITH latest AS (
            SELECT runtime_trade_id, MAX(trade_id) AS latest_trade_id
            FROM trade_decisions
            WHERE strategy = ?
              AND runtime_trade_id IS NOT NULL
            GROUP BY runtime_trade_id
        )
        SELECT td.runtime_trade_id,
               td.status,
               td.market_id,
               td.bin_label,
               td.direction,
               td.price AS entry_price,
               td.p_posterior,
               td.edge,
               td.timestamp AS entered_at,
               td.fill_price,
               td.settlement_edge_usd
        FROM latest
        JOIN trade_decisions td ON td.trade_id = latest.latest_trade_id
        ORDER BY td.trade_id
        """,
        (strategy_key,),
    ).fetchall()
    latest_by_trade_id = {str(row["runtime_trade_id"] or ""): dict(row) for row in latest_td_rows}

    settled_rows = conn.execute(
        """
        SELECT position_id,
               strategy_key,
               settled_at,
               pnl,
               outcome,
               exit_reason
        FROM outcome_fact
        WHERE strategy_key = ?
        ORDER BY settled_at
        """,
        (strategy_key,),
    ).fetchall()

    settled_count = 0
    pnl_total = 0.0
    win_count = 0
    loss_count = 0
    price_buckets = Counter()
    by_city = Counter()
    by_status = Counter()
    observed_entry_prices: list[float] = []
    rows: list[dict] = []

    for settled in settled_rows:
        trade_id = str(settled["position_id"] or "")
        latest = latest_by_trade_id.get(trade_id, {})
        entry_price = latest.get("entry_price")
        if entry_price is not None:
            entry_price = float(entry_price)
            observed_entry_prices.append(entry_price)
        pnl = float(settled["pnl"] or 0.0)
        settled_count += 1
        pnl_total += pnl
        if int(settled["outcome"] or 0) == 1:
            win_count += 1
        else:
            loss_count += 1
        price_buckets[_price_bucket(entry_price)] += 1
        by_city[str(latest.get("market_id") or latest.get("city") or "")] += 1
        status_label = str(latest.get("status") or "missing_trade_decision")
        by_status[status_label] += 1
        rows.append(
            {
                "trade_id": trade_id,
                "pnl": pnl,
                "outcome": int(settled["outcome"] or 0),
                "settled_at": str(settled["settled_at"] or ""),
                "exit_reason": str(settled["exit_reason"] or ""),
                "status": status_label,
                "market_id": str(latest.get("market_id") or ""),
                "bin_label": str(latest.get("bin_label") or ""),
                "direction": str(latest.get("direction") or ""),
                "entry_price": entry_price,
                "p_posterior": float(latest["p_posterior"]) if latest.get("p_posterior") is not None else None,
                "edge": float(latest["edge"]) if latest.get("edge") is not None else None,
            }
        )

    legacy_event_counts: dict[str, int] = {}
    try:
        legacy_rows = conn.execute(
            """
            SELECT event_type, COUNT(*) AS n
            FROM position_events_legacy
            WHERE strategy = ?
            GROUP BY event_type
            """,
            (strategy_key,),
        ).fetchall()
        legacy_event_counts = {str(row["event_type"] or ""): int(row["n"] or 0) for row in legacy_rows}
    except sqlite3.OperationalError:
        legacy_event_counts = {}

    open_positions_json_count = 0
    if positions_json_path.exists():
        data = json.loads(positions_json_path.read_text())
        open_positions_json_count = sum(
            1
            for row in data.get("positions", [])
            if (row.get("strategy_key") or row.get("strategy")) == strategy_key
        )

    diagnosis_hypotheses: list[str] = []
    if settled_count and loss_count == settled_count and observed_entry_prices and max(observed_entry_prices) <= 0.02:
        diagnosis_hypotheses.append("all_settled_losses_are_ultra_low_price_tail_bets")
    if legacy_event_counts.get("ORDER_REJECTED", 0) > 0:
        diagnosis_hypotheses.append("rejection_path_exists_and_should_be_separated_from_settlement_truth")
    if by_status.get("missing_trade_decision", 0) > 0:
        diagnosis_hypotheses.append("some_settlements_are_missing_latest_trade_decision_context")

    return {
        "strategy_key": strategy_key,
        "settled_summary": {
            "count": settled_count,
            "pnl_total": round(pnl_total, 2),
            "win_count": win_count,
            "loss_count": loss_count,
            "price_bucket_counts": dict(price_buckets),
            "latest_status_counts": dict(by_status),
            "rows": rows,
        },
        "latest_trade_decision_summary": {
            "count": len(latest_by_trade_id),
            "status_counts": dict(Counter(str(row["status"] or "") for row in latest_by_trade_id.values())),
        },
        "legacy_event_summary": legacy_event_counts,
        "open_positions_json_count": open_positions_json_count,
        "diagnosis_hypotheses": diagnosis_hypotheses,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Diagnose center_buy paper-mode failure shape")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--positions-json", type=Path, default=DEFAULT_POSITIONS_JSON)
    parser.add_argument("--strategy", default="center_buy")
    args = parser.parse_args()

    conn = sqlite3.connect(str(args.db))
    conn.row_factory = sqlite3.Row
    try:
        report = diagnose_center_buy(
            conn,
            strategy_key=args.strategy,
            positions_json_path=args.positions_json,
        )
    finally:
        conn.close()

    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
