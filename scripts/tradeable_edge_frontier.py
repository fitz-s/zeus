#!/usr/bin/env python3
# Created: 2026-05-23
# Last reused or audited: 2026-05-23
# Authority basis: FIX-4 (CORE-P0 spec) — tradeable-edge frontier telemetry;
#   p_market floor from src/engine/evaluator.py min_entry_price=0.05;
#   schema from opportunity_fact DDL in state/zeus_trades.db.
"""Tradeable-edge frontier summary: answers 'real edges or only phantoms?'

Reads opportunity_fact (state/zeus_trades.db, READ-ONLY) and prints a
per-strategy frontier table distinguishing:

  TRADEABLE-PRICED  — p_market >= 0.05 AND best_edge > 0
                      (above entry floor, positive edge before any gate)
  PHANTOM           — p_market < 0.05 AND p_cal >= PHANTOM_CAL_THRESHOLD
                      (model says probable, market prices near zero —
                       cold-bias / low-bin artifact)
  SUB-FLOOR         — p_market < 0.05 AND p_cal < PHANTOM_CAL_THRESHOLD
                      (or p_cal NULL — both model and market agree: unlikely)
  PRICE-MISSING     — p_market IS NULL (price evidence unavailable)

Rows in TRADEABLE-PRICED are further broken down by their rejection_stage
so the operator can see "how many real fills were blocked by ANTI_CHURN vs
SIGNAL_QUALITY vs RISK_REJECTED vs (no rejection = should_trade=1)".

Usage:
    python3 scripts/tradeable_edge_frontier.py
    python3 scripts/tradeable_edge_frontier.py --since 3d
    python3 scripts/tradeable_edge_frontier.py --since 2026-05-20
    python3 scripts/tradeable_edge_frontier.py --since 7d --phantom-cal-threshold 0.15
    python3 scripts/tradeable_edge_frontier.py --db state/zeus_trades.db

Exit 0 always (diagnostic-only; no gate logic).

K1 compliance: read-only access via get_trade_connection_read_only().
No mutation of any canonical surface.
"""
from __future__ import annotations

import argparse
import math
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

ENTRY_FLOOR = 0.05  # mirrors src/engine/evaluator.py min_entry_price
DEFAULT_PHANTOM_CAL_THRESHOLD = 0.10
DEFAULT_SINCE_DAYS = 3


def _parse_since(since_arg: str) -> str:
    """Return ISO datetime string (UTC) for the --since boundary.

    Accepts:
      - relative: "3d", "7d", "24h", "48h"
      - ISO date: "2026-05-20"
      - ISO datetime: "2026-05-20T00:00:00"
    """
    arg = since_arg.strip()
    if arg.endswith("d") and arg[:-1].isdigit():
        days = int(arg[:-1])
        dt = datetime.now(timezone.utc) - timedelta(days=days)
        return dt.isoformat()
    if arg.endswith("h") and arg[:-1].isdigit():
        hours = int(arg[:-1])
        dt = datetime.now(timezone.utc) - timedelta(hours=hours)
        return dt.isoformat()
    # Assume date or datetime string — pass through; sqlite TEXT comparison works.
    return arg


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    s = sorted(values)
    n = len(s)
    if n % 2 == 1:
        return s[n // 2]
    return (s[n // 2 - 1] + s[n // 2]) / 2.0


def _fmt(v: float | None, fmt: str = ".3f") -> str:
    if v is None:
        return "—"
    return format(v, fmt)


def run(
    db_path: str | Path | None,
    since_iso: str,
    phantom_cal_threshold: float,
    top_n: int,
) -> None:
    # ------------------------------------------------------------------ #
    # DB connection
    # ------------------------------------------------------------------ #
    if db_path is not None:
        import sqlite3

        path = Path(db_path).expanduser().resolve()
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
    else:
        from src.state.db import get_trade_connection_read_only

        conn = get_trade_connection_read_only()
        conn.row_factory = sqlite3.Row

    try:
        _run_query_and_print(conn, since_iso, phantom_cal_threshold, top_n)
    finally:
        conn.close()


def _run_query_and_print(
    conn: Any,
    since_iso: str,
    phantom_cal_threshold: float,
    top_n: int,
) -> None:
    # ------------------------------------------------------------------ #
    # Fetch all relevant rows in window
    # ------------------------------------------------------------------ #
    sql = """
        SELECT
            COALESCE(strategy_key, 'unclassified') AS strategy_key,
            city,
            target_date,
            range_label,
            direction,
            p_raw,
            p_cal,
            p_market,
            best_edge,
            rejection_stage,
            should_trade,
            recorded_at
        FROM opportunity_fact
        WHERE recorded_at >= ?
        ORDER BY strategy_key, recorded_at
    """
    rows = conn.execute(sql, (since_iso,)).fetchall()

    if not rows:
        print(f"No rows in opportunity_fact since {since_iso}.")
        return

    # ------------------------------------------------------------------ #
    # Classify into buckets per-strategy
    # ------------------------------------------------------------------ #
    from collections import defaultdict

    # strategy → bucket → list of row-dicts
    tradeable: dict[str, list[dict]] = defaultdict(list)
    phantom: dict[str, int] = defaultdict(int)
    sub_floor: dict[str, int] = defaultdict(int)
    price_missing: dict[str, int] = defaultdict(int)

    total = 0
    for row in rows:
        total += 1
        strat = row["strategy_key"]
        p_mkt = row["p_market"]
        p_cal = row["p_cal"]
        edge = row["best_edge"]

        if p_mkt is None:
            price_missing[strat] += 1
        elif p_mkt >= ENTRY_FLOOR and (edge is not None and edge > 0):
            tradeable[strat].append(dict(row))
        elif p_mkt < ENTRY_FLOOR:
            if p_cal is not None and p_cal >= phantom_cal_threshold:
                phantom[strat] += 1
            else:
                sub_floor[strat] += 1
        else:
            # p_market >= floor but edge <= 0 or None → genuine low/no-edge
            sub_floor[strat] += 1

    all_strategies = sorted(
        set(list(tradeable.keys()) + list(phantom.keys()) + list(sub_floor.keys()) + list(price_missing.keys()))
    )

    # ------------------------------------------------------------------ #
    # Print header
    # ------------------------------------------------------------------ #
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"\n=== TRADEABLE-EDGE FRONTIER  [{now_str}] ===")
    print(f"    window since : {since_iso}")
    print(f"    entry floor  : p_market >= {ENTRY_FLOOR}")
    print(f"    phantom cal  : p_cal >= {phantom_cal_threshold}")
    print(f"    total rows   : {total:,}")
    print()

    # ------------------------------------------------------------------ #
    # Per-strategy table
    # ------------------------------------------------------------------ #
    TRADEABLE_HDR = "strategy_key             | tradeable | max_edge | med_edge | phantom | sub_floor | price_miss | top_candidate"
    print(TRADEABLE_HDR)
    print("-" * len(TRADEABLE_HDR))

    for strat in all_strategies:
        t_rows = tradeable.get(strat, [])
        edges = [r["best_edge"] for r in t_rows if r["best_edge"] is not None]
        max_edge = max(edges) if edges else None
        med_edge = _median(edges)

        # Top genuine candidate: highest edge in tradeable bucket
        top_rows = sorted(t_rows, key=lambda r: r["best_edge"] or 0.0, reverse=True)[:top_n]
        if top_rows:
            tr = top_rows[0]
            cand = (
                f"{tr['city']}/{tr['target_date']}/{tr['range_label']} "
                f"e={_fmt(tr['best_edge'])} p_mkt={_fmt(tr['p_market'])} "
                f"p_cal={_fmt(tr['p_cal'])} [{tr['rejection_stage'] or 'TRADE'}]"
            )
        else:
            cand = "—"

        strat_label = strat[:24].ljust(24)
        print(
            f"{strat_label} | {len(t_rows):>9} | {_fmt(max_edge):>8} | {_fmt(med_edge):>8} "
            f"| {phantom.get(strat, 0):>7} | {sub_floor.get(strat, 0):>9} "
            f"| {price_missing.get(strat, 0):>10} | {cand}"
        )

    print()

    # ------------------------------------------------------------------ #
    # Per-strategy TRADEABLE detail: rejection_stage breakdown
    # ------------------------------------------------------------------ #
    has_tradeable = any(tradeable.values())
    if has_tradeable:
        print("--- TRADEABLE-PRICED breakdown by rejection_stage ---")
        for strat in all_strategies:
            t_rows = tradeable.get(strat, [])
            if not t_rows:
                continue
            stage_counts: dict[str, int] = defaultdict(int)
            for r in t_rows:
                stage = r["rejection_stage"] or "TRADE (no rejection)"
                stage_counts[stage] += 1
            print(f"\n  {strat}:")
            for stage, cnt in sorted(stage_counts.items(), key=lambda x: -x[1]):
                pct = 100.0 * cnt / len(t_rows)
                print(f"    {stage:<40} {cnt:>4} ({pct:.0f}%)")

            # Top-N genuine candidates for this strategy
            top_rows = sorted(t_rows, key=lambda r: r["best_edge"] or 0.0, reverse=True)[:top_n]
            if top_rows:
                print(f"  top-{top_n} by edge:")
                for tr in top_rows:
                    stage_label = tr["rejection_stage"] or "TRADE"
                    print(
                        f"    {tr['city']}/{tr['target_date']}/{tr['range_label']}  "
                        f"dir={tr['direction']}  e={_fmt(tr['best_edge'])}  "
                        f"p_mkt={_fmt(tr['p_market'])}  p_cal={_fmt(tr['p_cal'])}  "
                        f"stage=[{stage_label}]  at={tr['recorded_at'][:16]}"
                    )
        print()

    # ------------------------------------------------------------------ #
    # Diagnostic verdict
    # ------------------------------------------------------------------ #
    total_tradeable = sum(len(v) for v in tradeable.values())
    total_phantom = sum(phantom.values())
    total_sub_floor = sum(sub_floor.values())
    total_missing = sum(price_missing.values())

    print("--- DIAGNOSTIC ---")
    print(f"  TRADEABLE-PRICED (p≥0.05, edge>0) : {total_tradeable:>5}  ({100*total_tradeable/total:.1f}%)")
    print(f"  PHANTOM (p<0.05, p_cal≥{phantom_cal_threshold}) : {total_phantom:>5}  ({100*total_phantom/total:.1f}%)")
    print(f"  SUB-FLOOR genuine low              : {total_sub_floor:>5}  ({100*total_sub_floor/total:.1f}%)")
    print(f"  PRICE-MISSING                      : {total_missing:>5}  ({100*total_missing/total:.1f}%)")

    if total_tradeable == 0:
        verdict = "NO tradeable-priced edges — all above-floor opportunities absent or phantom."
    elif total_tradeable > 0:
        filled = sum(1 for rows in tradeable.values() for r in rows if r["should_trade"] == 1)
        verdict = (
            f"{total_tradeable} tradeable-priced edge(s) found; "
            f"{filled} marked should_trade=1 (filled or attempted), "
            f"{total_tradeable - filled} blocked by downstream gates."
        )
    else:
        verdict = "Unknown."
    print(f"\n  VERDICT: {verdict}")
    print()


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Tradeable-edge frontier summary (read-only on zeus_trades.db)."
    )
    p.add_argument(
        "--since",
        default=f"{DEFAULT_SINCE_DAYS}d",
        help=(
            "Time window: relative ('3d', '24h') or ISO date/datetime "
            f"(default: {DEFAULT_SINCE_DAYS}d)"
        ),
    )
    p.add_argument(
        "--db",
        default=None,
        dest="db_path",
        help="Path to zeus_trades.db (default: auto-detect via src.state.db)",
    )
    p.add_argument(
        "--phantom-cal-threshold",
        type=float,
        default=DEFAULT_PHANTOM_CAL_THRESHOLD,
        help=f"p_cal threshold for PHANTOM classification (default: {DEFAULT_PHANTOM_CAL_THRESHOLD})",
    )
    p.add_argument(
        "--top-n",
        type=int,
        default=3,
        help="Top-N genuine candidates to show per strategy (default: 3)",
    )
    return p


if __name__ == "__main__":
    parser = _build_parser()
    args = parser.parse_args()
    since_iso = _parse_since(args.since)
    run(
        db_path=args.db_path,
        since_iso=since_iso,
        phantom_cal_threshold=args.phantom_cal_threshold,
        top_n=args.top_n,
    )
