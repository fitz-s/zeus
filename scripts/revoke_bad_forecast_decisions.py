#!/usr/bin/env python3
# Created: 2026-05-22
# Last reused or audited: 2026-07-12
# Authority basis: docs/archive/2026-Q2/operations_historical/P0_FORECAST_EXTREMA_AUTHORITY_2026-05-22.md §PR-E;
#   docs/rebuild/quarantine_excision_2026-07-11.md DIQ packet (owner-local reshape).
# Lifecycle: created=2026-05-22; last_reviewed=2026-07-12; last_reused=never
# Purpose: PR-E cleanup CLI — tag all fact-table rows backed by non-contributing
#          forecasts (dry-run default). Wires all 6 revocation functions with
#          correct per-DB connections per K1 DB split, owner-local (DIQ packet).
# Reuse: Run after PR-A/E merge to revoke legacy bad-forecast decisions. Safe to re-run (idempotent).
"""PR-E cleanup CLI: revoke fact-table rows backed by non-contributing snapshots.

Default mode is DRY-RUN — prints candidate counts without writing.
Pass --apply to write revocation rows into each owning DB's local
fact_revocations table (DIQ packet: owner-local, not a single central table).

K1 DB split routing (owner-local, src/state/domains.py):
  - decision_events, probability_trace_fact, selection_family_fact,
    selection_hypothesis_fact  → world DB (world-owned; local write, no ATTACH)
  - opportunity_fact           → trade DB (ATTACHed from the world connection;
    trade-owned, cross-DB write — the one table this CLI's world pass still
    needs an ATTACH for)
  - calibration_pairs          → forecasts DB (forecasts-owned; local write,
    no ATTACH — simpler than the predecessor, which always ATTACHed trade)

Safety:
  - NON-destructive: tags rows, never deletes.
  - Idempotent: safe to run multiple times (INSERT OR IGNORE).
  - Requires all three DB paths to exist; refuses to run on missing DB.
  - Does NOT commit if --apply is omitted.

Usage:
    python scripts/revoke_bad_forecast_decisions.py [--apply]
    python scripts/revoke_bad_forecast_decisions.py [--apply] \\
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
from src.state.fact_revocation import (  # noqa: E402
    revoke_calibration_pairs_for_noncontributing_forecast,
    revoke_decision_events_for_noncontributing_forecast,
    revoke_decisions_for_noncontributing_forecast,
    revoke_probability_trace_fact_for_noncontributing_forecast,
    revoke_selection_family_fact_for_noncontributing_forecast,
    revoke_selection_hypothesis_fact_for_noncontributing_forecast,
)
from src.state.schema.fact_revocations_schema import (  # noqa: E402
    ensure_table as _ensure_fact_revocations_table,
    ensure_table_in_schema as _ensure_fact_revocations_table_in_schema,
)


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--apply", action="store_true", help="Write revocation rows (default: dry-run)")
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
    """Revoke world-side tables: decision_events, probability_trace_fact,
    selection_family_fact, selection_hypothesis_fact (world-owned, local
    write) plus opportunity_fact (trade-owned, cross-DB write via ATTACH).

    Opens world DB as main; ATTACHes forecasts as 'forecasts' (for snapshot
    join); ATTACHes trade as 'trade' (owner-local write target for the one
    trade-owned table in this pass, opportunity_fact).
    """
    conn = _connect_rw(world_db)
    try:
        conn.execute("ATTACH DATABASE ? AS forecasts", (str(forecasts_db),))
        conn.execute("ATTACH DATABASE ? AS trade", (str(trade_db),))
        if dry_run is False:
            # World-owned tables' revocation record is LOCAL (main schema) —
            # owner-local, no ghost-table risk since it never needs to guess a
            # schema (contrast the predecessor's trade-qualified DDL dance).
            _ensure_fact_revocations_table(conn)
            # opportunity_fact is trade-owned: create the revocation table in
            # the ATTACHed 'trade' schema explicitly (never on world's main).
            _ensure_fact_revocations_table_in_schema(conn, "trade")
        world_local_fns = [
            ("decision_events", revoke_decision_events_for_noncontributing_forecast),
            ("probability_trace_fact", revoke_probability_trace_fact_for_noncontributing_forecast),
            ("selection_family_fact", revoke_selection_family_fact_for_noncontributing_forecast),
            ("selection_hypothesis_fact", revoke_selection_hypothesis_fact_for_noncontributing_forecast),
        ]
        results = {}
        for tname, fn in world_local_fns:
            results[tname] = fn(conn, dry_run=dry_run)  # target_schema="main" default
        results["opportunity_fact"] = revoke_decisions_for_noncontributing_forecast(
            conn, dry_run=dry_run, target_schema="trade"
        )
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
    """Revoke forecasts-side tables: calibration_pairs.

    Opens forecasts DB as main (ensemble_snapshots co-located). calibration_pairs
    is forecasts-owned (src/state/domains.py) so its owner-local revocation
    record is written LOCALLY — no trade ATTACH needed (DIQ packet
    simplification vs the predecessor, which always ATTACHed trade).
    ``trade_db`` is accepted for CLI-signature compatibility but unused.
    """
    del trade_db
    conn = _connect_rw(forecasts_db)
    try:
        if dry_run is False:
            _ensure_fact_revocations_table(conn)
        result = revoke_calibration_pairs_for_noncontributing_forecast(conn, dry_run=dry_run)
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
    print(f"[{mode}] revoke_bad_forecast_decisions")
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
        print(f"    newly_revoked      : {result.get('newly_revoked', 0)}")
        print(f"    already_revoked    : {result.get('already_revoked', 0)}")
    if "error" in result:
        print(f"    ERROR: {result['error']}", file=sys.stderr)


if __name__ == "__main__":
    main()
