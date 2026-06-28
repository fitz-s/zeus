#!/usr/bin/env python3
"""Quarantine invalid LIVE money-path certificate rows.

Default mode is dry-run.  ``--apply`` writes non-destructive rows to
trade.decision_integrity_quarantine so immutable historical certificates remain
auditable but cannot be consumed as live executable authority.
"""

from __future__ import annotations

import argparse
from contextlib import nullcontext
import pathlib
import sqlite3
import sys

_REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.state.db import ZEUS_WORLD_DB_PATH, _zeus_trade_db_path  # noqa: E402
from src.state.decision_integrity_quarantine import (  # noqa: E402
    quarantine_invalid_live_actionable_certificates,
    quarantine_invalid_live_money_parent_modes,
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Write quarantine rows")
    parser.add_argument("--world-db", default=None, metavar="PATH")
    parser.add_argument("--trade-db", default=None, metavar="PATH")
    parser.add_argument(
        "--lookback-hours",
        type=float,
        default=48.0,
        help="Only inspect LIVE actionables newer than this many hours; use <=0 for all",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    world_db = pathlib.Path(args.world_db) if args.world_db else pathlib.Path(ZEUS_WORLD_DB_PATH)
    trade_db = pathlib.Path(args.trade_db) if args.trade_db else pathlib.Path(_zeus_trade_db_path())
    for label, path in (("world", world_db), ("trade", trade_db)):
        if not path.exists():
            print(f"ERROR: {label} DB not found: {path}", file=sys.stderr)
            return 1

    dry_run = not args.apply
    lookback_hours = args.lookback_hours if args.lookback_hours > 0 else None
    lock_context = nullcontext()
    if not dry_run:
        from src.state.db_writer_lock import WriteClass, db_writer_lock

        lock_context = db_writer_lock(trade_db, WriteClass.BULK)
    with lock_context:
        conn = sqlite3.connect(str(world_db))
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("ATTACH DATABASE ? AS trade", (str(trade_db),))
            if not dry_run:
                conn.execute(
                    """
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
                    """
                )
                conn.execute(
                    """
                    CREATE INDEX IF NOT EXISTS
                        trade.idx_decision_integrity_quarantine_table_row
                        ON decision_integrity_quarantine(table_name, row_id)
                    """
                )
                conn.execute(
                    """
                    CREATE INDEX IF NOT EXISTS
                        trade.idx_decision_integrity_quarantine_reason
                        ON decision_integrity_quarantine(reason_code, recorded_at)
                    """
                )
            actionable_result = quarantine_invalid_live_actionable_certificates(
                conn,
                dry_run=dry_run,
                lookback_hours=lookback_hours,
            )
            parent_mode_result = quarantine_invalid_live_money_parent_modes(
                conn,
                dry_run=dry_run,
                lookback_hours=lookback_hours,
            )
            if not dry_run:
                conn.commit()
        finally:
            conn.close()

    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"[{mode}] quarantine_invalid_live_money_certificates")
    print(f"  world DB       : {world_db}")
    print(f"  trade DB       : {trade_db}")
    print(f"  lookback_hours : {lookback_hours if lookback_hours is not None else 'all'}")
    for label, result in (
        ("actionable_payload", actionable_result),
        ("parent_modes", parent_mode_result),
    ):
        print(f"  [{label}]")
        for key in (
            "checked_count",
            "candidates_found",
            "newly_quarantined",
            "already_quarantined",
        ):
            print(f"    {key:19}: {result.get(key, 0)}")
    errors = [
        (label, result["error"])
        for label, result in (
            ("actionable_payload", actionable_result),
            ("parent_modes", parent_mode_result),
        )
        if "error" in result
    ]
    if errors:
        for label, error in errors:
            print(f"  ERROR[{label}]: {error}", file=sys.stderr)
        return 1
    if dry_run:
        print("  (dry-run: no rows written; pass --apply to commit)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
