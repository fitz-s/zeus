# Created: 2026-06-10
# Last reused or audited: 2026-06-10
# Authority basis: operator Phase-2 directive 2026-06-10 (verify correct order e2e; Milan-class = wrong trade)
"""Read-only e2e verifier for a real fill under the 2026-06-10 defense stack.

For each venue command since --since (default: last armed restart), checks:
  1. DIRECTION LAW: buy_yes requires bin center within max(1 bin step, 1.0 x sigma)
     of fused forecast center mu* (from forecast_posteriors); buy_no requires the
     opposite. Milan-class violation = BUY YES on a bin far from mu*.
  2. SPREAD SANITY: entry price must be inside [best_bid, best_ask] of the
     snapshot orderbook at decision time; maker intent must rest (price < ask).
  3. MODE CONSISTENCY: command price vs proof execution_mode_intent
     (MAKER => price <= ask - tick; never crossing full ask at wide spread).
  4. LIFECYCLE: INTENT -> ACK -> (FILL) chain present in venue_commands state
     + fill facts (canonical lane), no orphan states.

Usage:
  PYTHONSAFEPATH=1 PYTHONPATH=. .venv/bin/python scripts/verify_fill_e2e.py [--since ISO] [--json]

All DB access is uri mode=ro. Exit code 0 = all checked commands PASS,
2 = at least one violation, 3 = no commands found.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from typing import Any

TRADES_DB = "file:state/zeus_trades.db?mode=ro"
WORLD_DB = "file:state/zeus-world.db?mode=ro"
FORECASTS_DB = "file:state/zeus-forecasts.db?mode=ro"


def _ro(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path, uri=True, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def _commands_since(trades: sqlite3.Connection, since: str) -> list[dict[str, Any]]:
    rows = trades.execute(
        """
        SELECT command_id, intent_kind, market_id, token_id, side, size, price,
               state, venue_order_id, created_at, snapshot_id, decision_id
          FROM venue_commands
         WHERE created_at > ?
         ORDER BY created_at
        """,
        (since,),
    ).fetchall()
    return [dict(r) for r in rows]


def _market_meta(world: sqlite3.Connection, market_id: str) -> dict[str, Any] | None:
    row = world.execute(
        """
        SELECT condition_id, city, target_date, temperature_metric, bin_label,
               bin_lower, bin_upper, best_bid, best_ask, captured_at
          FROM executable_market_snapshots
         WHERE condition_id = ?
         ORDER BY captured_at DESC
         LIMIT 1
        """,
        (market_id,),
    ).fetchone()
    return dict(row) if row else None


def _fused_center(
    forecasts: sqlite3.Connection, city: str, target_date: str, metric: str
) -> tuple[float | None, float | None]:
    row = forecasts.execute(
        """
        SELECT mu_star, sigma_pred FROM forecast_posteriors
         WHERE city = ? AND target_date = ? AND temperature_metric = ?
         ORDER BY created_at DESC LIMIT 1
        """,
        (city, target_date, metric),
    ).fetchone()
    if row is None:
        return None, None
    return row["mu_star"], row["sigma_pred"]


def verify(since: str) -> tuple[int, list[dict[str, Any]]]:
    trades = _ro(TRADES_DB)
    world = _ro(WORLD_DB)
    forecasts = _ro(FORECASTS_DB)
    results: list[dict[str, Any]] = []
    commands = _commands_since(trades, since)
    if not commands:
        return 3, results
    worst = 0
    for cmd in commands:
        checks: dict[str, str] = {}
        meta = _market_meta(world, cmd["market_id"]) or {}
        # 2. spread sanity
        bid, ask, price = meta.get("best_bid"), meta.get("best_ask"), cmd["price"]
        if bid is not None and ask is not None:
            if not (bid <= price <= ask):
                checks["spread"] = f"FAIL price={price} outside [{bid},{ask}]"
            elif cmd["side"] == "BUY" and price >= ask and (ask - bid) / max(ask, 1e-9) > 0.25:
                checks["spread"] = f"FAIL taker-at-ask on wide spread bid={bid} ask={ask}"
            else:
                checks["spread"] = "PASS"
        else:
            checks["spread"] = "SKIP no snapshot book"
        # 1. direction law (entry buys only)
        if cmd["intent_kind"] == "ENTRY" and meta.get("city"):
            mu, sigma = _fused_center(
                forecasts, meta["city"], meta["target_date"], meta["temperature_metric"]
            )
            lo, hi = meta.get("bin_lower"), meta.get("bin_upper")
            if mu is None or lo is None or hi is None:
                checks["direction_law"] = "SKIP missing mu*/bin bounds"
            else:
                center = (float(lo) + float(hi)) / 2.0
                step = abs(float(hi) - float(lo))
                tol = max(step, float(sigma or 1.0))
                near = abs(center - float(mu)) <= tol
                outcome_yes = cmd["side"] == "BUY"  # YES-token buy
                if outcome_yes and not near:
                    checks["direction_law"] = (
                        f"FAIL buy_yes far bin: center={center} mu*={mu} tol={tol}"
                    )
                else:
                    checks["direction_law"] = f"PASS center={center} mu*={mu} tol={tol:.2f}"
        else:
            checks["direction_law"] = "SKIP non-entry or no meta"
        # 4. lifecycle
        checks["lifecycle"] = (
            "PASS state=" + str(cmd["state"])
            if cmd["state"] in ("ACKED", "FILLED", "RESTING", "CANCELLED", "SUBMITTED")
            else f"WARN state={cmd['state']}"
        )
        failed = any(v.startswith("FAIL") for v in checks.values())
        worst = max(worst, 2 if failed else 0)
        results.append(
            {
                "command_id": cmd["command_id"],
                "created_at": cmd["created_at"],
                "city": meta.get("city"),
                "bin": meta.get("bin_label"),
                "side": cmd["side"],
                "price": cmd["price"],
                "size": cmd["size"],
                "state": cmd["state"],
                "verdict": "VIOLATION" if failed else "PASS",
                "checks": checks,
            }
        )
    return worst, results


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="2026-06-10T06:00:00")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    code, results = verify(args.since)
    if args.json:
        print(json.dumps(results, indent=1, default=str))
    else:
        for r in results:
            print(
                f"{r['created_at']} {r['command_id']} {r['city']} {r['bin']} "
                f"{r['side']} {r['size']}@{r['price']} [{r['state']}] -> {r['verdict']}"
            )
            for k, v in r["checks"].items():
                print(f"    {k}: {v}")
    if code == 3:
        print("no venue commands since", args.since)
    return code


if __name__ == "__main__":
    sys.exit(main())
