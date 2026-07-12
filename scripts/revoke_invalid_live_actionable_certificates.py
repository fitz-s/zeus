#!/usr/bin/env python3
"""Revoke invalid LIVE money-path certificate rows.

Default mode is dry-run. ``--apply`` writes non-destructive rows to the
world DB's owner-local fact_revocations table (decision_certificates is
world-owned, src/state/domains.py; DIQ packet,
docs/rebuild/quarantine_excision_2026-07-11.md) so immutable historical
certificates remain auditable but cannot be consumed as live executable
authority. Owner-local: no trade ATTACH is needed (predecessor
decision_integrity_quarantine always wrote cross-DB into the trade DB; this
reshape writes locally into the same DB decision_certificates lives in).
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

from src.state.db import ZEUS_WORLD_DB_PATH  # noqa: E402
from src.state.fact_revocation import (  # noqa: E402
    revoke_invalid_live_actionable_certificates,
    revoke_invalid_live_money_parent_modes,
)
from src.state.schema.fact_revocations_schema import ensure_table as _ensure_fact_revocations_table  # noqa: E402


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Write revocation rows")
    parser.add_argument("--world-db", default=None, metavar="PATH")
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
    if not world_db.exists():
        print(f"ERROR: world DB not found: {world_db}", file=sys.stderr)
        return 1

    dry_run = not args.apply
    lookback_hours = args.lookback_hours if args.lookback_hours > 0 else None
    lock_context = nullcontext()
    if not dry_run:
        from src.state.db_writer_lock import WriteClass, db_writer_lock

        lock_context = db_writer_lock(world_db, WriteClass.BULK)
    with lock_context:
        conn = sqlite3.connect(str(world_db))
        conn.row_factory = sqlite3.Row
        try:
            if not dry_run:
                _ensure_fact_revocations_table(conn)
            actionable_result = revoke_invalid_live_actionable_certificates(
                conn,
                dry_run=dry_run,
                lookback_hours=lookback_hours,
            )
            parent_mode_result = revoke_invalid_live_money_parent_modes(
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
    print(f"  lookback_hours : {lookback_hours if lookback_hours is not None else 'all'}")
    for label, result in (
        ("actionable_payload", actionable_result),
        ("parent_modes", parent_mode_result),
    ):
        print(f"  [{label}]")
        for key in (
            "checked_count",
            "candidates_found",
            "newly_revoked",
            "already_revoked",
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
