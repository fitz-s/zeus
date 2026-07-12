#!/usr/bin/env python3
# Created: 2026-07-12
# Last reused or audited: 2026-07-12
# Lifecycle: created=2026-07-12; last_reviewed=2026-07-12; last_reused=never
# Purpose: Repair position_current rows where a correctly BOOKED close (a
#   real EXIT_ORDER_FILLED fill, phase_after=economically_closed) was later
#   CLOBBERED to realized_pnl_usd=0.0 / exit_price=0.0 by the settlement
#   reprojection bug (Bug B reload-path, see
#   tests/state/test_settlement_preserves_booked_close_economics.py). Distinct
#   from scripts/backfill_close_economics.py, which targets rows where
#   realized_pnl_usd is NULL (never booked) -- these rows were booked, then
#   overwritten with a real (wrong) 0.0, so the NULL-scoped backfill script
#   cannot see them.
# Reuse: DRY-RUN by default; --apply writes. Re-running after --apply is a
#   no-op once repaired (WHERE realized_pnl_usd = 0.0 no longer matches a
#   repaired non-zero row; a genuinely $0.00 close is a no-op by design, see
#   below).
# Authority basis: live DB evidence, 2026-07-12 (~27 position_current rows in
#   the last 7d with this exact shape: EXIT_ORDER_FILLED payload pnl != 0.0,
#   current realized_pnl_usd/exit_price = 0.0 after settlement).
"""Repair settled positions whose booked close economics were clobbered to
0.0 by the settlement reload bug.

Scope (conservative, fail-closed skip over guessing):

  phase = 'settled' AND realized_pnl_usd = 0.0
    AND position_id NOT LIKE 'chain-only%'
    AND has an EXIT_ORDER_FILLED position_event with phase_after =
        'economically_closed' whose payload_json carries a numeric
        fill_price (the latest such event by sequence_no wins).

For each matching row, recompute realized_pnl_usd via the single shared
formula (src.state.close_economics.compute_realized_pnl_usd) using the row's
OWN shares/cost_basis_usd/entry_price and the event's fill_price, and restore
exit_price = fill_price.

A row whose recomputed value is within $0.005 of 0.0 is a genuine near-zero
close (not a clobber symptom distinguishable from a real $0.00) -- treated as
a no-op and reported, not repaired.

A row with no EXIT_ORDER_FILLED evidence (no such event, or the event's
payload has no numeric fill_price) is EXCLUDED with a reason -- never
guessed.

Standalone, read-only by default: opens the trades DB read-only for the scan.
--apply opens a normal writable trades connection for the UPDATE. Never
apply against a live DB from this script directly -- the operator runs
--apply after reviewing the dry-run report.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

_NOOP_EPSILON = 0.005


def _shares_for_row(row: sqlite3.Row) -> float:
    chain_shares = row["chain_shares"]
    if chain_shares is not None and float(chain_shares) > 0:
        return float(chain_shares)
    shares = row["shares"]
    return float(shares) if shares is not None else 0.0


def _latest_exit_fill_price(conn: sqlite3.Connection, position_id: str) -> float | None:
    """Return the numeric fill_price from the most recent EXIT_ORDER_FILLED
    event on this position whose phase_after is economically_closed, or None
    if no such event / no numeric fill_price exists."""
    rows = conn.execute(
        """
        SELECT payload_json
          FROM position_events
         WHERE position_id = ?
           AND event_type = 'EXIT_ORDER_FILLED'
           AND phase_after = 'economically_closed'
         ORDER BY sequence_no DESC
        """,
        (position_id,),
    ).fetchall()
    for row in rows:
        try:
            payload = json.loads(row["payload_json"] or "{}")
        except (TypeError, json.JSONDecodeError):
            continue
        fill_price = payload.get("fill_price")
        if fill_price is None:
            continue
        try:
            value = float(fill_price)
        except (TypeError, ValueError):
            continue
        if value == value and value not in (float("inf"), float("-inf")):  # finite
            return value
    return None


def _plan_repair(trades_conn: sqlite3.Connection):
    """Return (planned_updates, excluded, noops) without writing anything.

    planned_updates: list of dicts {position_id, realized_pnl_usd, exit_price,
        source} ready to apply.
    excluded: list of dicts {position_id, reason}.
    noops: list of dicts {position_id, recomputed_pnl, reason}.
    """
    from src.state.close_economics import compute_realized_pnl_usd

    rows = trades_conn.execute(
        """
        SELECT position_id, phase, shares, chain_shares, cost_basis_usd,
               entry_price, exit_price, realized_pnl_usd
          FROM position_current
         WHERE phase = 'settled'
           AND realized_pnl_usd = 0.0
           AND position_id NOT LIKE 'chain-only%'
        """
    ).fetchall()

    planned: list[dict] = []
    excluded: list[dict] = []
    noops: list[dict] = []
    for row in rows:
        position_id = str(row["position_id"])
        fill_price = _latest_exit_fill_price(trades_conn, position_id)
        if fill_price is None:
            excluded.append(
                {
                    "position_id": position_id,
                    "reason": (
                        "no EXIT_ORDER_FILLED event with phase_after="
                        "economically_closed and numeric fill_price"
                    ),
                }
            )
            continue

        shares = _shares_for_row(row)
        cost_basis = float(row["cost_basis_usd"] or 0.0)
        entry_price = row["entry_price"]
        realized_pnl = compute_realized_pnl_usd(
            shares=shares,
            exit_price=fill_price,
            cost_basis_usd=cost_basis,
            entry_price=float(entry_price) if entry_price is not None else None,
        )

        if abs(realized_pnl) < _NOOP_EPSILON:
            noops.append(
                {
                    "position_id": position_id,
                    "recomputed_pnl": realized_pnl,
                    "reason": f"recomputed pnl {realized_pnl:.4f} within ${_NOOP_EPSILON} of 0.0",
                }
            )
            continue

        planned.append(
            {
                "position_id": position_id,
                "realized_pnl_usd": realized_pnl,
                "exit_price": fill_price,
                "source": "exit_order_filled_event",
            }
        )

    return planned, excluded, noops


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--apply",
        action="store_true",
        help="Write the recomputed realized_pnl_usd/exit_price to position_current. "
        "Default is dry-run (report only).",
    )
    args = ap.parse_args()

    from src.state.db import get_trade_connection_read_only

    trades_conn = get_trade_connection_read_only()
    trades_conn.row_factory = sqlite3.Row

    planned, excluded, noops = _plan_repair(trades_conn)

    print(f"Settled clobbered-P&L repair (dry_run={not args.apply})")
    print(f"  eligible to repair: {len(planned)}")
    print(f"  no-op (recomputed ~0.0): {len(noops)}")
    print(f"  excluded (skipped, reason recorded): {len(excluded)}")
    print()
    for item in planned:
        print(
            f"  REPAIR  position_id={item['position_id']:40s} "
            f"realized_pnl_usd={item['realized_pnl_usd']:>10.2f} exit_price={item['exit_price']:.4f} "
            f"source={item['source']}"
        )
    for item in noops:
        print(f"  NOOP    position_id={item['position_id']:40s} reason={item['reason']}")
    for item in excluded:
        print(f"  SKIP    position_id={item['position_id']:40s} reason={item['reason']}")

    if not args.apply:
        print()
        print("Dry run only -- no writes performed. Re-run with --apply to write.")
        trades_conn.close()
        return 0

    trades_conn.close()

    from src.state.db import get_trade_connection

    write_conn = get_trade_connection()
    try:
        with write_conn:
            for item in planned:
                write_conn.execute(
                    "UPDATE position_current SET realized_pnl_usd = ?, exit_price = ? "
                    "WHERE position_id = ? AND phase = 'settled' AND realized_pnl_usd = 0.0",
                    (item["realized_pnl_usd"], item["exit_price"], item["position_id"]),
                )
        print(f"\nApplied {len(planned)} repairs.")
    finally:
        write_conn.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
