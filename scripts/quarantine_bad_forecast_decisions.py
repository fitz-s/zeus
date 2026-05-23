#!/usr/bin/env python3
# Created: 2026-05-22
# Last reused or audited: 2026-05-22
# Authority basis: docs/operations/P0_FORECAST_EXTREMA_AUTHORITY_2026-05-22.md §PR-E
# Lifecycle: created=2026-05-22; last_reviewed=2026-05-22; last_reused=never
# Purpose: PR-E cleanup CLI — tag opportunity_fact rows backed by non-contributing forecasts (dry-run default).
# Reuse: Run after PR-A/E merge to quarantine legacy bad-forecast decisions. Safe to re-run (idempotent).
"""PR-E cleanup CLI: quarantine opportunity_fact rows backed by non-contributing snapshots.

Default mode is DRY-RUN — prints candidate counts without writing.
Pass --apply to write quarantine rows into decision_integrity_quarantine.

Safety:
  - NON-destructive: tags rows, never deletes.
  - Idempotent: safe to run multiple times (INSERT OR IGNORE).
  - Requires trade DB path to exist; refuses to run on missing DB.
  - Does NOT commit if --apply is omitted.

Usage:
    python scripts/quarantine_bad_forecast_decisions.py [--apply] [--trade-db PATH] [--forecasts-db PATH]
"""

from __future__ import annotations

import argparse
import pathlib
import sqlite3
import sys

_REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.state.db import ZEUS_FORECASTS_DB_PATH, _zeus_trade_db_path  # noqa: E402
from src.state.decision_integrity_quarantine import (  # noqa: E402
    quarantine_decisions_for_noncontributing_forecast,
)
from src.state.schema.decision_integrity_quarantine_schema import ensure_table  # noqa: E402


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--apply", action="store_true", help="Write quarantine rows (default: dry-run)")
    p.add_argument(
        "--trade-db",
        default=None,
        metavar="PATH",
        help="Override trade DB path (default: state/zeus_trades.db)",
    )
    p.add_argument(
        "--forecasts-db",
        default=None,
        metavar="PATH",
        help="Override forecasts DB path (default: state/zeus-forecasts.db)",
    )
    return p.parse_args()


def main() -> None:
    args = _parse_args()

    trade_db = pathlib.Path(args.trade_db) if args.trade_db else pathlib.Path(_zeus_trade_db_path())
    forecasts_db = pathlib.Path(args.forecasts_db) if args.forecasts_db else ZEUS_FORECASTS_DB_PATH

    if not trade_db.exists():
        print(f"ERROR: trade DB not found: {trade_db}", file=sys.stderr)
        sys.exit(1)
    if not forecasts_db.exists():
        print(f"ERROR: forecasts DB not found: {forecasts_db}", file=sys.stderr)
        sys.exit(1)

    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"[{mode}] quarantine_bad_forecast_decisions")
    print(f"  trade DB    : {trade_db}")
    print(f"  forecasts DB: {forecasts_db}")

    conn = sqlite3.connect(str(trade_db))
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("ATTACH DATABASE ? AS forecasts", (str(forecasts_db),))
        if args.apply:
            ensure_table(conn)
        result = quarantine_decisions_for_noncontributing_forecast(conn, dry_run=not args.apply)
        if args.apply:
            conn.commit()
    finally:
        conn.close()

    print()
    print(f"  candidates_found   : {result.get('candidates_found', 0)}")
    if args.apply:
        print(f"  newly_quarantined  : {result.get('newly_quarantined', 0)}")
        print(f"  already_quarantined: {result.get('already_quarantined', 0)}")
    else:
        print("  (dry-run: no rows written; pass --apply to commit)")

    if "error" in result:
        print(f"\nERROR: {result['error']}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
