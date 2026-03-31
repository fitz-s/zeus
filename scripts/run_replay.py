#!/usr/bin/env python3
"""Decision Replay Engine CLI.

Usage:
  # Audit: how would current logic have performed on historical data?
  .venv/bin/python scripts/run_replay.py --mode audit --start 2025-01-01 --end 2026-03-30

  # Counterfactual: what if London alpha was 0.70?
  .venv/bin/python scripts/run_replay.py --mode counterfactual --start 2025-06-01 --end 2025-09-01 \
    --override "alpha.London.JJA=0.70"

  # With multiple overrides:
  .venv/bin/python scripts/run_replay.py --mode counterfactual \
    --override "alpha.London.DJF=0.65" --override "alpha.London.JJA=0.70"
"""

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.state.db import get_connection, init_schema


def _parse_overrides(override_strs: list[str]) -> dict:
    """Parse override strings like 'alpha.London.JJA=0.70' into nested dict."""
    overrides = {}
    for s in override_strs:
        if "=" not in s:
            continue
        path, value = s.split("=", 1)
        parts = path.split(".")

        if parts[0] == "alpha" and len(parts) == 3:
            city, season = parts[1], parts[2]
            overrides.setdefault("alpha", {}).setdefault(city, {})[season] = float(value)
        else:
            print(f"Unknown override: {s}")

    return overrides


def main():
    parser = argparse.ArgumentParser(description="Zeus Decision Replay Engine")
    parser.add_argument("--mode", choices=["audit", "counterfactual", "walk_forward"],
                        default="audit", help="Replay mode")
    parser.add_argument("--start", required=True, help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end", required=True, help="End date (YYYY-MM-DD)")
    parser.add_argument("--override", action="append", default=[],
                        help="Parameter override (e.g., 'alpha.London.JJA=0.70')")

    args = parser.parse_args()

    # Ensure schema is up to date
    conn = get_connection()
    init_schema(conn)
    conn.close()

    overrides = _parse_overrides(args.override) if args.override else None

    from src.engine.replay import run_replay

    print(f"\n{'='*80}")
    print(f"Decision Replay Engine — {args.mode.upper()}")
    print(f"Date range: {args.start} to {args.end}")
    if overrides:
        print(f"Overrides: {overrides}")
    print(f"{'='*80}\n")

    summary = run_replay(
        start_date=args.start,
        end_date=args.end,
        mode=args.mode,
        overrides=overrides,
    )

    # Report
    print(f"Run ID:       {summary.run_id}")
    print(f"Settlements:  {summary.n_settlements} total, {summary.n_replayed} replayed "
          f"({summary.coverage_pct}% coverage)")
    print(f"Would trade:  {summary.n_would_trade} / {summary.n_replayed}")
    print(f"Win rate:     {summary.replay_win_rate:.1%}")
    print(f"Total PnL:    ${summary.replay_total_pnl:+.2f}")
    print()

    # Per-city breakdown
    print(f"{'City':15} {'Dates':>6} {'Trades':>7} {'PnL':>10} {'Win%':>6}")
    print("-" * 48)
    for city_name in sorted(summary.per_city.keys()):
        stats = summary.per_city[city_name]
        print(f"{city_name:15} {stats['n_dates']:>6} {stats['n_trades']:>7} "
              f"${stats['total_pnl']:>+8.2f} {stats['win_rate']:>5.1%}")

    print()
    print(f"Results stored in replay_results table (run_id={summary.run_id})")

    # Show sample decisions for interesting outcomes
    interesting = [o for o in summary.outcomes if o.replay_would_trade][:5]
    if interesting:
        print(f"\n{'='*80}")
        print("Sample replay decisions (would-trade):")
        print(f"{'='*80}")
        for o in interesting:
            traded_decs = [d for d in o.replay_decisions if d.should_trade]
            for d in traded_decs:
                won = o.replay_pnl > 0
                print(f"  {o.city:12} {o.target_date} {d.range_label[:30]:30} "
                      f"{d.direction:8} edge={d.edge:+.3f} p_post={d.p_posterior:.3f} "
                      f"{'✅' if won else '❌'} PnL=${o.replay_pnl:+.2f}")


if __name__ == "__main__":
    main()
