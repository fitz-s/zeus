#!/usr/bin/env python3
# Created: 2026-06-23
# Last audited: 2026-06-23
# Authority basis: standing mission — "EVERY real chain decision audited with reality";
#   "make sure they are profitable"; real settled evidence only (no test/replay).
#
# Continuous real-chain profitability audit. Reads settlement_attribution (the immutable
# settlement-graded record of EVERY decision that reached settlement) and reports the realized
# per-contract P&L decomposition: overall, by category, by direction, by belief-freshness, and by
# q_lcb claimed-edge band. This is the repeatable forward-verification gate behind the one-off
# 2026-06-23 audit (docs/evidence/live_order_pathology/2026-06-23_realchain_profitability_audit.md):
# run it any time to see whether the live book is genuinely profitable on settled reality.
#
# Per-contract realized P&L = won ? (1 - avg_fill_price) : (-avg_fill_price). A bin contract pays
# $1.00 on win, $0 on loss; the fill price is the all-in cost paid. Net > 0 => the settled book is
# profitable. Breakeven win-rate at a given fill F is exactly F.
#
# Usage: python scripts/realchain_profitability_audit.py [--days N] [--db PATH]
import argparse
import os
import sqlite3
import sys

_DEFAULT_WORLD_DB = os.environ.get(
    "ZEUS_WORLD_DB",
    os.path.join(os.environ.get("ZEUS_PRIMARY_ROOT", "/Users/leofitz/zeus"), "state", "zeus-world.db"),
)

_PNL = "SUM(CASE WHEN won=1 THEN 1.0-avg_fill_price ELSE -avg_fill_price END)"
_BASE = (
    "FROM settlement_attribution "
    "WHERE settled_at > datetime('now', ?) AND avg_fill_price IS NOT NULL"
)


def _rows(conn: sqlite3.Connection, select: str, group: str, since: str) -> list:
    sql = f"SELECT {select} {_BASE} GROUP BY {group} ORDER BY {_PNL}"
    return list(conn.execute(sql, (since,)).fetchall())


def _fmt(rows: list, label: str) -> str:
    out = [f"\n== {label} =="]
    for r in rows:
        key, n, wr, fill, pnl = r
        out.append(
            f"  {str(key):<28} n={n:<4} wr={wr if wr is not None else 0:<6.3f} "
            f"fill={fill if fill is not None else 0:<6.3f} net_pnl={pnl if pnl is not None else 0:+.3f}"
        )
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser(description="Real-chain settled profitability audit")
    ap.add_argument("--days", type=int, default=14, help="lookback window in days (default 14)")
    ap.add_argument("--db", default=_DEFAULT_WORLD_DB, help="path to zeus-world.db")
    args = ap.parse_args()
    if not os.path.exists(args.db):
        print(f"ERROR: world db not found: {args.db}", file=sys.stderr)
        return 2
    since = f"-{int(args.days)} days"
    conn = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    try:
        overall = conn.execute(
            f"SELECT COUNT(*), AVG(won), AVG(avg_fill_price), {_PNL}, "
            f"AVG(CASE WHEN won=1 THEN 1.0-avg_fill_price ELSE -avg_fill_price END) {_BASE}",
            (since,),
        ).fetchone()
        n, wr, fill, net, avg_edge = overall
        if not n:
            print(f"No settled decisions with a fill in the last {args.days}d.")
            return 0
        verdict = "PROFITABLE" if (net or 0) > 0 else "NOT PROFITABLE"
        print(f"=== Real-chain settled profitability (last {args.days}d) — {verdict} ===")
        print(
            f"  n={n} win_rate={wr:.3f} breakeven={fill:.3f} "
            f"net_per_contract_pnl={net:+.3f} avg_edge_per_contract={avg_edge:+.4f}"
        )
        edge_band = (
            "CASE WHEN q_lcb_5pct IS NULL THEN 'no_q_lcb' "
            "WHEN q_lcb_5pct-avg_fill_price < 0 THEN 'neg_edge' "
            "WHEN q_lcb_5pct-avg_fill_price < 0.05 THEN '0-5%' "
            "WHEN q_lcb_5pct-avg_fill_price < 0.15 THEN '5-15%' ELSE '15%+' END"
        )
        sel = "{k}, COUNT(*), AVG(won), AVG(avg_fill_price), " + _PNL
        print(_fmt(_rows(conn, sel.format(k="category"), "category", since), "by category"))
        print(_fmt(_rows(conn, sel.format(k="direction"), "direction", since), "by direction"))
        print(_fmt(
            _rows(conn, sel.format(k="fresher_cycle_existed_at_decision"),
                  "fresher_cycle_existed_at_decision", since),
            "by belief-staleness at decision (1=fresher cycle existed)",
        ))
        print(_fmt(
            _rows(conn, sel.format(k=edge_band), edge_band, since),
            "by q_lcb claimed-edge band (q_lcb - fill)",
        ))
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
