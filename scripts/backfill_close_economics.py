#!/usr/bin/env python3
# Created: 2026-07-08
# Last reused or audited: 2026-07-08
# Lifecycle: created=2026-07-08; last_reviewed=2026-07-08; last_reused=never
# Purpose: Backfill realized_pnl_usd/exit_price on historical position_current
#   rows that closed BEFORE the R0-a close-economics unification (or before the
#   Bug A/B fix at commit 4502173671) and were therefore left with NULL
#   realized_pnl_usd -- so forward settled coverage reaches 100% gradeable.
# Reuse: DRY-RUN by default; --apply writes. Re-running after --apply is a
#   no-op (WHERE realized_pnl_usd IS NULL only matches unbacked rows).
# Authority basis: docs/rebuild/EXECUTION_MASTER_2026-07-07.md §E R0-a
#   docs/rebuild/whole_system_first_principles_2026-07-07.md §2.6
"""Backfill realized_pnl_usd on historical closed positions left NULL by
Bug A/B (pre-R0-a).

Scope (conservative, fail-closed skip over guessing):

  1. Rows with phase IN (economically_closed, settled), realized_pnl_usd IS
     NULL, and exit_price IS NOT NULL: the exit-fill/economic-close paths
     (command_recovery, exchange_reconcile) always persisted exit_price
     correctly even pre-fix (only "pnl" was missing -- see Bug A). Compute
     realized_pnl_usd directly via close_economics.compute_realized_pnl_usd
     from the row's own shares/cost_basis_usd/entry_price.

  2. Rows with phase == settled, realized_pnl_usd IS NULL, AND exit_price IS
     NULL: this is the chain_mirror_reconciler shape (Bug B) -- pre-fix it
     wrote neither realized_pnl_usd nor exit_price into the durable
     projection. Grade the row against settlement_outcomes (same
     city/target_date/temperature_metric lookup and grade_bin() helper
     chain_mirror_reconciler.classify_local_position itself uses) to recover
     won/lost, derive exit_price = 1.0/0.0, then compute realized_pnl_usd.

  3. Anything that does not resolve to a definite exit_price under (1) or (2)
     (no settlement_outcomes match, or an UNGRADEABLE bin) is EXCLUDED and
     reported by reason -- never guessed.

Standalone, read-only by default: opens the trades DB read-only for the scan
and (only under --apply) the forecasts DB read-only for the settlement
lookup and a normal writable trades connection for the UPDATE. No option in
this script touches state/*.db in write mode unless --apply is passed.
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

_TARGET_PHASES = ("economically_closed", "settled")


def _shares_for_row(row: sqlite3.Row) -> float:
    # Mirrors chain_mirror_reconciler._apply_settlement_finding's precedence
    # (chain_shares first, falling back to shares) -- the safer default for a
    # row that has actually reached a close phase, regardless of which path
    # originally wrote it.
    chain_shares = row["chain_shares"]
    if chain_shares is not None and float(chain_shares) > 0:
        return float(chain_shares)
    shares = row["shares"]
    return float(shares) if shares is not None else 0.0


def _plan_backfill(trades_conn: sqlite3.Connection, forecasts_conn: sqlite3.Connection | None):
    """Return (planned_updates, excluded) without writing anything.

    planned_updates: list of dicts {position_id, phase, realized_pnl_usd,
        exit_price, source} ready to apply.
    excluded: list of dicts {position_id, phase, reason}.
    """
    from src.state.close_economics import compute_realized_pnl_usd

    rows = trades_conn.execute(
        f"""
        SELECT position_id, phase, shares, chain_shares, cost_basis_usd,
               entry_price, exit_price, city, target_date, temperature_metric,
               bin_label, direction
          FROM position_current
         WHERE phase IN ({", ".join("?" for _ in _TARGET_PHASES)})
           AND realized_pnl_usd IS NULL
        """,
        _TARGET_PHASES,
    ).fetchall()

    settlement_lookup = {}
    if forecasts_conn is not None:
        from src.state.chain_mirror_reconciler import load_settlement_lookup

        settlement_lookup = load_settlement_lookup(forecasts_conn)

    planned: list[dict] = []
    excluded: list[dict] = []
    for row in rows:
        position_id = str(row["position_id"])
        phase = str(row["phase"])
        cost_basis = float(row["cost_basis_usd"] or 0.0)
        entry_price = row["entry_price"]
        shares = _shares_for_row(row)

        if row["exit_price"] is not None:
            # Case (1): exit_price already durable (Bug A shape).
            exit_price = float(row["exit_price"])
            source = "exit_price_column"
        elif phase == "settled":
            # Case (2): chain-mirror Bug B shape -- grade against settlement_outcomes.
            from src.state.chain_mirror_reconciler import grade_bin

            key = (
                str(row["city"] or ""),
                str(row["target_date"] or ""),
                str(row["temperature_metric"] or "high"),
            )
            fact = settlement_lookup.get(key)
            if fact is None or fact.authority != "VERIFIED":
                excluded.append(
                    {
                        "position_id": position_id,
                        "phase": phase,
                        "reason": f"no VERIFIED settlement_outcomes row for {key}",
                    }
                )
                continue
            won = grade_bin(str(row["bin_label"] or ""), str(row["direction"] or ""), fact.winning_bin)
            if won is None:
                excluded.append(
                    {
                        "position_id": position_id,
                        "phase": phase,
                        "reason": (
                            f"bin_label={row['bin_label']!r} not comparable to "
                            f"winning_bin={fact.winning_bin!r} (UNGRADEABLE)"
                        ),
                    }
                )
                continue
            exit_price = 1.0 if won else 0.0
            source = "settlement_outcomes_grade"
        else:
            excluded.append(
                {
                    "position_id": position_id,
                    "phase": phase,
                    "reason": "economically_closed row with NULL exit_price -- no recoverable close price",
                }
            )
            continue

        realized_pnl = compute_realized_pnl_usd(
            shares=shares,
            exit_price=exit_price,
            cost_basis_usd=cost_basis,
            entry_price=float(entry_price) if entry_price is not None else None,
        )
        planned.append(
            {
                "position_id": position_id,
                "phase": phase,
                "realized_pnl_usd": realized_pnl,
                "exit_price": exit_price,
                "source": source,
            }
        )

    return planned, excluded


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--apply",
        action="store_true",
        help="Write the computed realized_pnl_usd (and, for case-2 rows, exit_price) "
        "to position_current. Default is dry-run (report only).",
    )
    args = ap.parse_args()

    from src.state.db import get_forecasts_connection_read_only, get_trade_connection_read_only

    trades_conn = get_trade_connection_read_only()
    trades_conn.row_factory = sqlite3.Row
    try:
        forecasts_conn = get_forecasts_connection_read_only()
        forecasts_conn.row_factory = sqlite3.Row
    except Exception as exc:  # pragma: no cover -- environment-dependent
        print(f"WARNING: could not open forecasts DB for settlement lookup: {exc}")
        forecasts_conn = None

    planned, excluded = _plan_backfill(trades_conn, forecasts_conn)

    print(f"R0-a close-economics backfill (dry_run={not args.apply})")
    print(f"  eligible to book: {len(planned)}")
    print(f"  excluded (skipped, reason recorded): {len(excluded)}")
    print()
    for item in planned:
        print(
            f"  BOOK  position_id={item['position_id']:40s} phase={item['phase']:20s} "
            f"realized_pnl_usd={item['realized_pnl_usd']:>10.2f} exit_price={item['exit_price']:.2f} "
            f"source={item['source']}"
        )
    for item in excluded:
        print(f"  SKIP  position_id={item['position_id']:40s} phase={item['phase']:20s} reason={item['reason']}")

    if not args.apply:
        print()
        print("Dry run only -- no writes performed. Re-run with --apply to write.")
        trades_conn.close()
        if forecasts_conn is not None:
            forecasts_conn.close()
        return 0

    trades_conn.close()
    if forecasts_conn is not None:
        forecasts_conn.close()

    from src.state.db import get_trade_connection

    write_conn = get_trade_connection()
    try:
        with write_conn:
            for item in planned:
                write_conn.execute(
                    "UPDATE position_current SET realized_pnl_usd = ?, exit_price = COALESCE(exit_price, ?) "
                    "WHERE position_id = ? AND realized_pnl_usd IS NULL",
                    (item["realized_pnl_usd"], item["exit_price"], item["position_id"]),
                )
        print(f"\nApplied {len(planned)} updates.")
    finally:
        write_conn.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
