#!/usr/bin/env python3
# Created: 2026-06-23
# Last audited: 2026-06-23
# Authority basis: chatgpt-consult round-3 required check — "replay the settled rows as-of
#   (settled_at < decision_time) and report old q_lcb edge vs q_exec_lcb edge, especially the
#   5-15% false-edge band and the 15%+ band." Real-chain validation of the q_exec_lcb deflation.
#
# As-of WALK-FORWARD (no leakage): for each settled row, fit q_exec_lcb blocks ONLY on rows whose
# settlement is strictly before this row's decision time, then compare model_q_lcb-edge vs
# q_exec_lcb-edge. Historical rows lack actual_exec_class, so this validates the side-conditioned
# root calibration only (the core deflation hypothesis); maker-vs-taker conditioning accrues live.
#
# Usage: python scripts/q_exec_lcb_backtest.py [--db PATH]
import argparse
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src.decision.q_exec_lcb import ExecutionOutcomeFact, build_exec_blocks, q_exec_lcb  # noqa: E402

_DB = os.environ.get(
    "ZEUS_WORLD_DB",
    os.path.join(os.environ.get("ZEUS_PRIMARY_ROOT", "/Users/leofitz/zeus"), "state", "zeus-world.db"),
)


def _band(edge):
    if edge < 0:
        return "neg"
    if edge < 0.05:
        return "0-5%"
    if edge < 0.15:
        return "5-15%"
    return "15%+"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=_DB)
    ap.add_argument("--days", type=int, default=60)
    args = ap.parse_args()
    conn = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    rows = conn.execute(
        """
        SELECT direction, q_live, q_lcb_5pct, avg_fill_price, won,
               settled_at, COALESCE(decision_posterior_computed_at, settled_at) AS decision_time
        FROM settlement_attribution
        WHERE settled_at > datetime('now', ?) AND q_lcb_5pct IS NOT NULL
          AND avg_fill_price IS NOT NULL AND q_live IS NOT NULL
        ORDER BY decision_time
        """,
        (f"-{args.days} days",),
    ).fetchall()
    conn.close()
    if not rows:
        print("no rows with q_lcb + q_live + fill")
        return 0

    # Build the full fact list (one per row) for as-of filtering.
    facts_all = [
        ExecutionOutcomeFact(
            decision_time=str(r[6]), settled_at=str(r[5]), side=str(r[0]),
            actual_exec_class="TAKER_CROSS",  # placeholder: historical exec_class unavailable -> root/side calibration
            raw_side_prob=float(r[1]), model_q_lcb=float(r[2]), fill_price=float(r[3]), won=int(r[4]),
        )
        for r in rows
    ]

    # As-of walk-forward: for each row, fit on rows SETTLED before this row's decision time.
    model_band = {}   # band -> [n, wins]
    exec_band = {}    # band (under q_exec_lcb edge) -> [n, wins]  (admitted = exec_edge > 0)
    flips = 0         # admitted under model but NOT under q_exec_lcb
    for i, f in enumerate(facts_all):
        prior = [g for g in facts_all if g.settled_at < f.decision_time]
        blocks = build_exec_blocks(prior)
        qx = q_exec_lcb(
            model_q_lcb=f.model_q_lcb, raw_side_prob=f.raw_side_prob,
            exec_class=f.actual_exec_class, side=f.side, blocks=blocks,
        )
        m_edge = f.model_q_lcb - f.fill_price
        x_edge = qx - f.fill_price
        mb = _band(m_edge)
        model_band.setdefault(mb, [0, 0])
        model_band[mb][0] += 1
        model_band[mb][1] += f.won
        # admitted under q_exec_lcb iff x_edge > 0 (honest after-cost gate, no floor)
        if x_edge > 0:
            xb = _band(x_edge)
            exec_band.setdefault(xb, [0, 0])
            exec_band[xb][0] += 1
            exec_band[xb][1] += f.won
        if m_edge > 0 and x_edge <= 0:
            flips += 1

    n = len(facts_all)
    print(f"=== q_exec_lcb as-of walk-forward backtest (n={n} settled rows w/ q_lcb+q_live+fill) ===")
    print(f"admitted under model q_lcb (edge>0): {sum(v[0] for b,v in model_band.items() if b!='neg')}")
    print(f"admitted under q_exec_lcb  (edge>0): {sum(v[0] for v in exec_band.values())}")
    print(f"candidates DE-ADMITTED by q_exec_lcb (model edge>0 -> exec edge<=0): {flips}")
    print("\n-- by MODEL q_lcb edge band: n, wins, win-rate --")
    for b in ("neg", "0-5%", "5-15%", "15%+"):
        if b in model_band:
            nn, w = model_band[b]
            print(f"  {b:<6} n={nn:<4} wins={w:<4} wr={w/nn:.3f}")
    print("\n-- by q_exec_lcb edge band (admitted only): n, wins, win-rate --")
    for b in ("0-5%", "5-15%", "15%+"):
        if b in exec_band:
            nn, w = exec_band[b]
            print(f"  {b:<6} n={nn:<4} wins={w:<4} wr={w/nn:.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
