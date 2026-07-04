#!/usr/bin/env python3
# Lifecycle: created=2026-07-04; last_reviewed=2026-07-04; last_reused=never
# Purpose: Reconcile position_current against venue chain truth (data-api
#   wallet position snapshot) per docs/rebuild/chain_mirror_state_model_2026-07-04.md.
# Reuse: Run without --apply first; use --apply only after operator review of
#   the dry-run JSON report.
# Authority basis: operator directive 2026-07-04 (root AGENTS.md §2
#   reconciliation order Chain > Chronicler > Portfolio); scripts/AGENTS.md
#   repair contract.
"""Chain-mirror reconciler: classify + optionally repair position_current.

Read-only against the venue (a single GET /positions data-api call via
PolymarketClient.get_positions_from_api() — the exact call that produced the
2026-07-04 divergence snapshot this script was built to close). NEVER submits
a venue order, NEVER submits a redeem transaction. Writes (only under
--apply) are scoped to two safe classes:
  (a) local rows whose held token is absent on chain AND the market has a
      VERIFIED settlement_outcomes row -> close to phase=settled via
      append_many_and_project (CLOSED_REDEEMED / CLOSED_WORTHLESS).
  (b) local rows whose held token is present on chain with a different size
      -> chain_shares corrected via append_many_and_project (CHAIN_SIZE_CORRECTED).
Every other class (missing local row, foreign token, open-but-absent) is
report-only in every run, --apply or not.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.state.chain_mirror_reconciler import load_chain_positions_by_asset, reconcile
from src.state.db import get_trade_connection, get_trade_connection_read_only


def run(*, apply: bool) -> dict:
    import sqlite3

    from src.data.polymarket_client import PolymarketClient
    from src.state.db import get_forecasts_connection_read_only

    raw_positions = PolymarketClient().get_positions_from_api() or []
    chain_by_asset = load_chain_positions_by_asset(raw_positions)

    conn_trades = (
        get_trade_connection(write_class="live") if apply else get_trade_connection_read_only()
    )
    conn_trades.row_factory = sqlite3.Row
    conn_forecasts = None
    try:
        # Always a genuinely read-only (mode=ro) settlement_outcomes read,
        # even under --apply — grading never writes to zeus-forecasts.db
        # (INV-37: single-DB writes only, zeus_trades.db).
        conn_forecasts = get_forecasts_connection_read_only()
        conn_forecasts.row_factory = sqlite3.Row
    except Exception:
        conn_forecasts = None

    try:
        report = reconcile(
            conn_trades,
            conn_forecasts,
            chain_by_asset,
            apply=apply,
        )
        if apply:
            conn_trades.commit()
        return report.to_json_dict()
    finally:
        conn_trades.close()
        if conn_forecasts is not None:
            conn_forecasts.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply", action="store_true",
        help="Write the safe repair classes (settlement closes, size corrections). "
             "Default is dry-run (report only, no writes).",
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON.")
    args = parser.parse_args(argv)

    result = run(apply=bool(args.apply))
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        mode = "APPLY" if args.apply else "DRY-RUN"
        print(f"chain-mirror reconcile: {mode} applied={result['applied']} counts={result['counts']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
