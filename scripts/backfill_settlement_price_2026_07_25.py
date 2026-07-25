#!/usr/bin/env python3
# Created: 2026-07-25
# Last reused or audited: 2026-07-25
# Authority basis: fix(state) settlement_price binary payout truth-repair packet
"""Backfill corrupt position_current.settlement_price rows to a binary payout.

Background
----------
src.state.chain_mirror_reconciler._apply_settlement_finding used to write
``finding.details["settlement_value"]`` -- a raw measured temperature from
zeus-forecasts.db ``settlement_outcomes`` -- straight into
``position_current.settlement_price``. That column is documented (see
src.engine.lifecycle_events:315-318) to equal exit_price on settled rows: a
[0.0, 1.0] payout fraction, never a temperature. The write-path bug is fixed
separately in chain_mirror_reconciler.py (settlement_price is now graded
1.0/0.0 from position_won, unconditionally, independent of exit_price). This
script repairs the rows already corrupted before that fix landed.

Strategy
--------
1. Read the trade DB read-only for ``position_current`` rows with
   ``settlement_price > 1.0`` (out of the valid [0.0, 1.0] payout band --
   the corruption signature).
2. Read zeus-forecasts.db ``settlement_outcomes`` read-only (a SEPARATE
   connection -- INV-37: no write transaction spans DBs) to look up the
   VERIFIED winning_bin per (city, target_date, temperature_metric).
3. Recompute won = grade_bin(bin_label, direction, winning_bin) -- the exact
   same pure grading function the chain-mirror reconciler uses -- and set
   settlement_price = 1.0 if won else 0.0.
4. exit_price is NEVER read or trusted as a grading input (2026-07-04 batch
   has 27 NULL + 5 contradictory exit_price rows) and is NEVER written by
   this script -- only settlement_price is touched.
5. Refuses (aborts the whole run, no partial apply) if any target row no
   longer satisfies settlement_price > 1.0 at write time, or if the
   candidate set becomes empty/unexpected between dry-run and apply.
6. Single-writer discipline: forecasts.db is read via its own read-only
   connection, closed before the trade DB write connection opens; the write
   itself is a single-DB (trade DB only) transaction under
   db_writer_lock(BULK). No cross-DB write transaction (INV-37).

Usage
-----
  # Dry-run (default -- no DB writes):
  python scripts/backfill_settlement_price_2026_07_25.py

  # Apply:
  python scripts/backfill_settlement_price_2026_07_25.py --apply
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Optional

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("backfill_settlement_price")

BACKFILL_TAG = "backfill_settlement_price_2026_07_25"


# ---------------------------------------------------------------------------
# Pure grading (unit-testable without a DB)
# ---------------------------------------------------------------------------

def recompute_settlement_price(
    *, bin_label: str, direction: str, winning_bin: str
) -> Optional[float]:
    """Recompute the binary settlement payout for one corrupt row.

    Reuses src.state.chain_mirror_reconciler.grade_bin -- the exact same
    pure win/lose grading the reconciler's live write path now uses. Returns
    None when ungradeable (unparseable/mismatched-unit bin comparison), the
    same fail-closed posture grade_bin itself documents; the caller must
    skip such a row rather than guess.
    """
    from src.state.chain_mirror_reconciler import grade_bin

    won = grade_bin(bin_label, direction, winning_bin)
    if won is None:
        return None
    return 1.0 if won else 0.0


# ---------------------------------------------------------------------------
# Core logic
# ---------------------------------------------------------------------------

def _find_corrupt_rows(trade_conn) -> list[dict]:
    """Trade-DB read: position_current rows with settlement_price out of [0,1]."""
    rows = trade_conn.execute(
        """
        SELECT position_id, city, target_date, temperature_metric, bin_label,
               direction, phase, settlement_price, exit_price
          FROM position_current
         WHERE settlement_price IS NOT NULL
           AND settlement_price > 1.0
        """
    ).fetchall()
    return [dict(r) for r in rows]


def _resolve_rows(candidates: list[dict], settlement_lookup: dict) -> list[dict]:
    """Pair each corrupt row with its recomputed payout, or a skip reason."""
    results = []
    for row in candidates:
        key = (
            str(row["city"] or ""),
            str(row["target_date"] or ""),
            str(row["temperature_metric"] or "high"),
        )
        settlement = settlement_lookup.get(key)
        if settlement is None:
            results.append({**row, "skip_reason": "no_settlement_outcomes_row"})
            continue
        if settlement.authority != "VERIFIED":
            results.append(
                {**row, "skip_reason": f"settlement_authority={settlement.authority!r}"}
            )
            continue
        new_price = recompute_settlement_price(
            bin_label=str(row["bin_label"] or ""),
            direction=str(row["direction"] or ""),
            winning_bin=settlement.winning_bin,
        )
        if new_price is None:
            results.append({**row, "skip_reason": "ungradeable_bin_comparison"})
            continue
        results.append(
            {
                **row,
                "new_settlement_price": new_price,
                "winning_bin": settlement.winning_bin,
            }
        )
    return results


def run(apply: bool) -> dict:
    from src.state.chain_mirror_reconciler import load_settlement_lookup
    from src.state.db import (
        get_forecasts_connection_read_only,
        get_trade_connection,
        get_trade_connection_read_only,
    )
    from src.state.db_writer_lock import WriteClass, db_writer_lock

    trade_ro = get_trade_connection_read_only()
    try:
        candidates = _find_corrupt_rows(trade_ro)
    finally:
        trade_ro.close()

    logger.info("Found %d position_current rows with settlement_price > 1.0", len(candidates))
    if not candidates:
        return {"candidates": 0, "resolved": 0, "skipped": 0, "applied": 0}

    forecasts_ro = get_forecasts_connection_read_only()
    try:
        settlement_lookup = load_settlement_lookup(forecasts_ro)
    finally:
        forecasts_ro.close()

    resolved_rows = _resolve_rows(candidates, settlement_lookup)
    resolved = [r for r in resolved_rows if "new_settlement_price" in r]
    skipped = [r for r in resolved_rows if "new_settlement_price" not in r]

    for r in resolved:
        logger.info(
            "  %s %s %s (%s, %s): settlement_price %.4f -> %s  [winning_bin=%s]",
            r["position_id"], r["city"], r["target_date"], r["temperature_metric"],
            r["direction"], r["settlement_price"], r["new_settlement_price"], r["winning_bin"],
        )
    for r in skipped:
        logger.warning(
            "  SKIP %s %s %s: %s (settlement_price=%.4f stays corrupt)",
            r["position_id"], r["city"], r["target_date"], r["skip_reason"], r["settlement_price"],
        )

    logger.info(
        "Dry-run results: candidates=%d resolved=%d skipped=%d",
        len(candidates), len(resolved), len(skipped),
    )

    if not apply:
        logger.info("DRY-RUN mode -- no writes. Pass --apply to commit.")
        return {
            "candidates": len(candidates),
            "resolved": len(resolved),
            "skipped": len(skipped),
            "applied": 0,
            "dry_run": True,
        }

    applied = 0
    logger.info("Acquiring BULK writer lock for zeus_trades.db...")
    trade_conn = get_trade_connection(write_class=WriteClass.BULK)
    try:
        with db_writer_lock(_trade_db_path(), WriteClass.BULK):
            for r in resolved:
                # Refuse the whole run rather than partially apply if a target
                # row no longer matches the corruption signature at write time
                # (TOCTOU between the dry-run read and this transaction).
                live = trade_conn.execute(
                    "SELECT settlement_price FROM position_current WHERE position_id = ?",
                    (r["position_id"],),
                ).fetchone()
                if live is None or live["settlement_price"] is None or float(live["settlement_price"]) <= 1.0:
                    raise RuntimeError(
                        "refusing backfill: position_id="
                        f"{r['position_id']!r} settlement_price no longer > 1.0 "
                        f"at write time (now={live['settlement_price'] if live else None!r})"
                    )
                cur = trade_conn.execute(
                    "UPDATE position_current SET settlement_price = ? "
                    "WHERE position_id = ? AND settlement_price > 1.0",
                    (r["new_settlement_price"], r["position_id"]),
                )
                if cur.rowcount != 1:
                    raise RuntimeError(
                        f"refusing backfill: position_id={r['position_id']!r} "
                        f"update matched {cur.rowcount} rows, expected 1"
                    )
                applied += 1
            trade_conn.commit()
    finally:
        trade_conn.close()

    logger.info("APPLY complete: applied=%d skipped=%d", applied, len(skipped))
    return {
        "candidates": len(candidates),
        "resolved": len(resolved),
        "skipped": len(skipped),
        "applied": applied,
        "dry_run": False,
    }


def _trade_db_path() -> Path:
    from src.state.db import _zeus_trade_db_path

    return _zeus_trade_db_path()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply", action="store_true",
        help="Commit changes (default: dry-run only, no writes)",
    )
    args = parser.parse_args()

    summary = run(apply=args.apply)
    mode = "APPLIED" if args.apply else "DRY-RUN"
    logger.info("%s summary: %s", mode, json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
