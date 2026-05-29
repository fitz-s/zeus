#!/usr/bin/env python3
# Created: 2026-05-22
# Last reused or audited: 2026-05-23
# Authority basis: docs/operations/P0_FORECAST_EXTREMA_AUTHORITY_2026-05-22.md §PR-E
# Lifecycle: created=2026-05-22; last_reviewed=2026-05-23; last_reused=never
# Purpose: PR-E cleanup CLI — tag all fact-table rows backed by non-contributing
#          forecasts (dry-run default). Wires all 6 quarantine functions with
#          correct per-DB connections per K1 DB split.
# Reuse: Run after PR-A/E merge to quarantine legacy bad-forecast decisions. Safe to re-run (idempotent).
"""PR-E cleanup CLI: quarantine fact-table rows backed by non-contributing snapshots.

Default mode is DRY-RUN — prints candidate counts without writing.
Pass --apply to write quarantine rows into decision_integrity_quarantine.

K1 DB split routing:
  - opportunity_fact, decision_events, probability_trace_fact,
    selection_family_fact, selection_hypothesis_fact  → world DB
    (ATTACHes forecasts as 'forecasts'; writes into trade.decision_integrity_quarantine)
  - calibration_pairs → forecasts DB
    (ATTACHes trade as 'trade'; writes into trade.decision_integrity_quarantine)

Safety:
  - NON-destructive: tags rows, never deletes.
  - Idempotent: safe to run multiple times (INSERT OR IGNORE).
  - Requires all three DB paths to exist; refuses to run on missing DB.
  - Does NOT commit if --apply is omitted.

Usage:
    python scripts/quarantine_bad_forecast_decisions.py [--apply]
    python scripts/quarantine_bad_forecast_decisions.py [--apply] \\
        [--world-db PATH] [--trade-db PATH] [--forecasts-db PATH]
"""

from __future__ import annotations

import argparse
import pathlib
import sqlite3
import sys

_REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.state.db import ZEUS_FORECASTS_DB_PATH, ZEUS_WORLD_DB_PATH, _zeus_trade_db_path  # noqa: E402
from src.state.decision_integrity_quarantine import (  # noqa: E402
    quarantine_calibration_pairs_for_noncontributing_forecast,
    quarantine_decision_events_for_noncontributing_forecast,
    quarantine_decisions_for_noncontributing_forecast,
    quarantine_probability_trace_fact_for_noncontributing_forecast,
    quarantine_selection_family_fact_for_noncontributing_forecast,
    quarantine_selection_hypothesis_fact_for_noncontributing_forecast,
)


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--apply", action="store_true", help="Write quarantine rows (default: dry-run)")
    p.add_argument(
        "--world-db",
        default=None,
        metavar="PATH",
        help="Override world DB path (default: state/zeus-world.db)",
    )
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


def _connect_rw(path: pathlib.Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


def _run_world_tables(
    world_db: pathlib.Path,
    trade_db: pathlib.Path,
    forecasts_db: pathlib.Path,
    *,
    dry_run: bool,
) -> dict:
    """Quarantine world-side tables: opportunity_fact, decision_events,
    probability_trace_fact, selection_family_fact, selection_hypothesis_fact.

    Opens world DB as main; ATTACHes forecasts as 'forecasts' (for snapshot join);
    ATTACHes trade as 'trade' (for writing decision_integrity_quarantine).
    """
    conn = _connect_rw(world_db)
    try:
        conn.execute("ATTACH DATABASE ? AS forecasts", (str(forecasts_db),))
        conn.execute("ATTACH DATABASE ? AS trade", (str(trade_db),))
        if dry_run is False:
            # Create quarantine table in TRADE schema (trade-qualified DDL).
            # Do NOT call ensure_table(conn) here — conn has world DB as main, so
            # ensure_table's unqualified CREATE TABLE would write a ghost table into
            # world's sqlite_master, causing the reader fallback branch in
            # evidence_report.py to read the empty world ghost instead of the real
            # trade table (silent quarantine-exclusion no-op in production).
            conn.execute("""
                CREATE TABLE IF NOT EXISTS trade.decision_integrity_quarantine (
                    id                       INTEGER PRIMARY KEY AUTOINCREMENT,
                    table_name               TEXT NOT NULL,
                    row_id                   TEXT NOT NULL,
                    reason_code              TEXT NOT NULL,
                    forecast_snapshot_id     TEXT,
                    recorded_at              TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    meta_json                TEXT NOT NULL DEFAULT '{}',
                    UNIQUE(table_name, row_id, reason_code)
                )
            """)
            # SQLite CREATE INDEX syntax: schema prefix goes on the index NAME only;
            # the table name in ON clause must be unqualified (SQLite resolves it via
            # the schema prefix on the index name). i.e. trade.idx_name ON table (not
            # ON trade.table). Both statements target trade.decision_integrity_quarantine.
            conn.execute("""
                CREATE INDEX IF NOT EXISTS
                    trade.idx_decision_integrity_quarantine_table_row
                    ON decision_integrity_quarantine(table_name, row_id)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS
                    trade.idx_decision_integrity_quarantine_reason
                    ON decision_integrity_quarantine(reason_code, recorded_at)
            """)
        fns = [
            ("opportunity_fact", quarantine_decisions_for_noncontributing_forecast),
            ("decision_events", quarantine_decision_events_for_noncontributing_forecast),
            ("probability_trace_fact", quarantine_probability_trace_fact_for_noncontributing_forecast),
            ("selection_family_fact", quarantine_selection_family_fact_for_noncontributing_forecast),
            ("selection_hypothesis_fact", quarantine_selection_hypothesis_fact_for_noncontributing_forecast),
        ]
        results = {}
        for tname, fn in fns:
            results[tname] = fn(conn, dry_run=dry_run)
        if dry_run is False:
            conn.commit()
    finally:
        conn.close()
    return results


def _run_forecasts_tables(
    forecasts_db: pathlib.Path,
    trade_db: pathlib.Path,
    *,
    dry_run: bool,
) -> dict:
    """Quarantine forecasts-side tables: calibration_pairs.

    Opens forecasts DB as main (ensemble_snapshots co-located);
    ATTACHes trade as 'trade' for writing decision_integrity_quarantine.
    """
    conn = _connect_rw(forecasts_db)
    try:
        conn.execute("ATTACH DATABASE ? AS trade", (str(trade_db),))
        if dry_run is False:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS trade.decision_integrity_quarantine (
                    id                       INTEGER PRIMARY KEY AUTOINCREMENT,
                    table_name               TEXT NOT NULL,
                    row_id                   TEXT NOT NULL,
                    reason_code              TEXT NOT NULL,
                    forecast_snapshot_id     TEXT,
                    recorded_at              TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    meta_json                TEXT NOT NULL DEFAULT '{}',
                    UNIQUE(table_name, row_id, reason_code)
                )
            """)
            # SQLite: schema prefix on index name only; ON clause table is unqualified.
            conn.execute("""
                CREATE INDEX IF NOT EXISTS
                    trade.idx_decision_integrity_quarantine_table_row
                    ON decision_integrity_quarantine(table_name, row_id)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS
                    trade.idx_decision_integrity_quarantine_reason
                    ON decision_integrity_quarantine(reason_code, recorded_at)
            """)
        result = quarantine_calibration_pairs_for_noncontributing_forecast(conn, dry_run=dry_run)
        if dry_run is False:
            conn.commit()
    finally:
        conn.close()
    return {"calibration_pairs": result}


def main() -> None:
    args = _parse_args()

    world_db = pathlib.Path(args.world_db) if args.world_db else pathlib.Path(ZEUS_WORLD_DB_PATH)
    trade_db = pathlib.Path(args.trade_db) if args.trade_db else pathlib.Path(_zeus_trade_db_path())
    forecasts_db = pathlib.Path(args.forecasts_db) if args.forecasts_db else pathlib.Path(ZEUS_FORECASTS_DB_PATH)

    for label, path in [("world", world_db), ("trade", trade_db), ("forecasts", forecasts_db)]:
        if not path.exists():
            print(f"ERROR: {label} DB not found: {path}", file=sys.stderr)
            sys.exit(1)

    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"[{mode}] quarantine_bad_forecast_decisions")
    print(f"  world DB    : {world_db}")
    print(f"  trade DB    : {trade_db}")
    print(f"  forecasts DB: {forecasts_db}")
    print()

    dry_run = not args.apply
    had_error = False

    # World-side tables.
    world_results = _run_world_tables(world_db, trade_db, forecasts_db, dry_run=dry_run)
    for tname, result in world_results.items():
        _print_result(tname, result, apply=args.apply)
        if "error" in result:
            had_error = True

    # Forecasts-side tables.
    forecasts_results = _run_forecasts_tables(forecasts_db, trade_db, dry_run=dry_run)
    for tname, result in forecasts_results.items():
        _print_result(tname, result, apply=args.apply)
        if "error" in result:
            had_error = True

    if not args.apply:
        print("\n  (dry-run: no rows written; pass --apply to commit)")

    if had_error:
        sys.exit(1)


def _print_result(tname: str, result: dict, *, apply: bool) -> None:
    print(f"  [{tname}]")
    print(f"    candidates_found   : {result.get('candidates_found', 0)}")
    if apply:
        print(f"    newly_quarantined  : {result.get('newly_quarantined', 0)}")
        print(f"    already_quarantined: {result.get('already_quarantined', 0)}")
    if "error" in result:
        print(f"    ERROR: {result['error']}", file=sys.stderr)


if __name__ == "__main__":
    main()
