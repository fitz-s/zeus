#!/usr/bin/env python3
"""
Build an equity curve for Zeus paper trading.
Timeline: initial $150 -> each realized trade P&L -> open positions marked to market.
"""
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

BASE = Path.home() / ".openclaw" / "workspace-venus"
ZEUS_DB = BASE / "zeus" / "state" / "zeus.db"
STRACKER = BASE / "zeus" / "state" / "strategy_tracker.json"
OUT = BASE / "zeus" / "equity_curve.png"

INITIAL = 150.0

# ── Load strategy tracker ────────────────────────────────────────────────────
with open(STRACKER) as f:
    tracker = json.load(f)

all_trades = []
for strat_name, strat_data in tracker.get("strategies", {}).items():
    for t in strat_data.get("trades", []):
        all_trades.append(t)

trade_pnl = {t["trade_id"]: t for t in all_trades}
realized_ids = {t["trade_id"] for t in all_trades if t["status"] == "exited"}

# ── Load chronicle ────────────────────────────────────────────────────────────
conn = sqlite3.connect(str(ZEUS_DB))
conn.row_factory = sqlite3.Row
cur = conn.cursor()

chronicle = cur.execute(
    "SELECT id, event_type, trade_id, timestamp, details_json FROM chronicle ORDER BY id"
).fetchall()

# ── Build ordered list of events we can pin to a time ─────────────────────────
# Each entry: (datetime, description, realized_pnl_delta, unrealized_mtm_delta, cumulative_realized)
events = []

# Start
start_dt = datetime(2026, 3, 30, 8, 0, tzinfo=timezone.utc)
events.append((start_dt, "Start", 0.0, 0.0, 0.0))

for row in chronicle:
    tid = row["trade_id"]
    ev_type = row["event_type"]
    ts_str = row["timestamp"]
    ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
    details = json.loads(row["details_json"]) if row["details_json"] else {}

    if ev_type == "ENTRY":
        if tid in trade_pnl and tid in realized_ids:
            pnl = trade_pnl[tid]["pnl"]
            events.append((ts, f"+${pnl:.2f} ({tid[:8]})", pnl, 0.0, None))
        elif tid in trade_pnl and tid not in realized_ids:
            # open position — record as entry marker but no realized pnl yet
            events.append((ts, f"OPEN {tid[:8]}", 0.0, 0.0, None))

# ── Sort and compute running realized P&L ────────────────────────────────────
events.sort(key=lambda x: x[0])

running_realized = 0.0
equity_curve = []  # (datetime, equity, label)

for ts, desc, pnl_delta, mtm_delta, _ in events:
    if pnl_delta != 0.0:
        running_realized += pnl_delta
    equity = INITIAL + running_realized
    equity_curve.append((ts, equity, desc))

# Current unrealized from status_summary (open positions)
UNREALIZED_NOW = 13.29
CURRENT_DT = datetime(2026, 3, 31, 6, 58, tzinfo=timezone.utc)
final_equity = INITIAL + running_realized + UNREALIZED_NOW
equity_curve.append((CURRENT_DT, final_equity, f"Current ${final_equity:.2f}"))

# ── Plot ─────────────────────────────────────────────────────────────────────
fig, ax = plt.subplots(figsize=(13, 7))

times = [p[0] for p in equity_curve]
values = [p[1] for p in equity_curve]

# Step chart
ax.step(times, values, where="post", color="#00D4AA", linewidth=2.5)

# Fill
ax.fill_between(times, INITIAL, values, step="post", alpha=0.12, color="#00D4AA")

# Markers for realized trade events
for ts, eq, desc in equity_curve:
    if desc.startswith("+"):
        ax.axvline(ts, color="#00D4AA", linewidth=0.8, linestyle="--", alpha=0.35)
        ax.scatter([ts], [eq], color="#00D4AA", zorder=5, s=30)

# Initial / final / unrealized lines
ax.axhline(INITIAL, color="red", linewidth=1.2, linestyle="--", alpha=0.6, label=f"Initial ${INITIAL:.2f}")
ax.axhline(INITIAL + running_realized, color="steelblue", linewidth=1.2,
           linestyle=":", alpha=0.7, label=f"Realised only ${INITIAL + running_realized:.2f}")
ax.axhline(final_equity, color="#00D4AA", linewidth=1.2, linestyle="-",
           alpha=0.8, label=f"Total w/ unrealized ${final_equity:.2f}")

# Annotations
ax.annotate(f"Start\n${INITIAL:.2f}",
            xy=(times[0], INITIAL), xytext=(times[0], INITIAL - 28),
            fontsize=9, color="red",
            arrowprops=dict(arrowstyle="->", color="red", alpha=0.6))

ax.annotate(f"Total\n${final_equity:.2f}",
            xy=(CURRENT_DT, final_equity), xytext=(CURRENT_DT, final_equity + 22),
            fontsize=9, color="#00D4AA",
            arrowprops=dict(arrowstyle="->", color="#00D4AA", alpha=0.8))

# Annotate big trade
big = next((e for e in equity_curve if "+$164" in e[2]), None)
if big:
    ax.annotate(f"SF win\n+${164.44:.2f}",
                xy=(big[0], big[1]), xytext=(big[0], big[1] + 18),
                fontsize=8, color="orange",
                arrowprops=dict(arrowstyle="->", color="orange", alpha=0.8))

ax.set_title("Zeus Paper Trading — Equity Curve\n2026-03-30 → 2026-03-31",
             fontsize=14, fontweight="bold")
ax.set_ylabel("Bankroll (USD)", fontsize=11)
ax.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d %H:%M"))
plt.xticks(rotation=30, fontsize=8)
ax.grid(True, alpha=0.2)
ax.legend(loc="upper left", fontsize=9)

ret = (final_equity / INITIAL - 1) * 100
summary = (f"Initial: ${INITIAL:.2f}  |  "
           f"Realised P&L: ${running_realized:.2f}  |  "
           f"Unrealised: ${UNREALIZED_NOW:.2f}  |  "
           f"Total: ${final_equity:.2f}  |  "
           f"Return: {ret:+.1f}%")
ax.text(0.5, -0.13, summary, transform=ax.transAxes,
        ha="center", fontsize=10, color="dimgray")

plt.tight_layout()
plt.savefig(str(OUT), dpi=150, bbox_inches="tight")
print(f"Saved → {OUT}")

# Print the equity table
print("\nEquity timeline:")
for ts, eq, desc in equity_curve:
    print(f"  {ts.strftime('%Y-%m-%d %H:%M')}  ${eq:8.2f}  {desc}")
print(f"\n  Return: {ret:+.1f}%  |  Realised: ${running_realized:.2f}  |  Unrealised: ${UNREALIZED_NOW:.2f}  |  Total: ${final_equity:.2f}")
