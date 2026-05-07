#!/usr/bin/env python3
"""Backfill legacy outcome_fact from chronicle SETTLEMENT events.

This script writes legacy lifecycle projection rows only. Rows it creates do
not carry settlement authority, report authority, or learning eligibility.

Linkage:
    chronicle.trade_id  ->  position_events_legacy.runtime_trade_id
    chronicle.trade_id is used as position_id in outcome_fact

Run:
    python3 scripts/backfill_outcome_fact.py --dry-run
    python3 scripts/backfill_outcome_fact.py --apply --confirm-legacy-outcome-fact-backfill
"""
# Lifecycle: created=2026-04-07; last_reviewed=2026-05-06; last_reused=2026-05-06
# Purpose: Repair legacy outcome_fact lifecycle projections behind explicit apply guard.
# Reuse: Do not run without an active repair packet, dry-run evidence, DB backup, and rollback plan.
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

DEFAULT_DB_PATH = Path(__file__).parent.parent / "state" / "zeus.db"
LEGACY_OUTCOME_FACT_AUTHORITY_SCOPE = "legacy_lifecycle_projection_not_settlement_authority"


def _get_strategy_key(conn: sqlite3.Connection, trade_id: str) -> str | None:
    """Look up strategy_key from position_events_legacy."""
    row = conn.execute(
        "SELECT strategy FROM position_events_legacy WHERE runtime_trade_id = ? LIMIT 1",
        (trade_id,),
    ).fetchone()
    return row["strategy"] if row else None


def _get_entered_at(conn: sqlite3.Connection, trade_id: str) -> str | None:
    """Look up entry timestamp from position_events_legacy (earliest row)."""
    row = conn.execute(
        """
        SELECT timestamp FROM position_events_legacy
        WHERE runtime_trade_id = ?
        ORDER BY timestamp ASC LIMIT 1
        """,
        (trade_id,),
    ).fetchone()
    return row["timestamp"] if row else None


def backfill(*, dry_run: bool = True, db_path: Path = DEFAULT_DB_PATH) -> dict:
    if not db_path.exists():
        return {
            "status": "error_missing_db",
            "dry_run": dry_run,
            "db_path": str(db_path),
            "authority_scope": LEGACY_OUTCOME_FACT_AUTHORITY_SCOPE,
        }
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    print(
        "WARNING: outcome_fact is a legacy lifecycle projection "
        f"({LEGACY_OUTCOME_FACT_AUTHORITY_SCOPE}); it is not settlement authority."
    )

    # Verify outcome_fact exists
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "outcome_fact" not in tables:
        conn.close()
        return {
            "status": "error_missing_outcome_fact",
            "dry_run": dry_run,
            "db_path": str(db_path),
            "authority_scope": LEGACY_OUTCOME_FACT_AUTHORITY_SCOPE,
        }

    settlements = conn.execute(
        "SELECT trade_id, timestamp, details_json FROM chronicle WHERE event_type='SETTLEMENT'"
    ).fetchall()
    print(f"Found {len(settlements)} SETTLEMENT events in chronicle")

    already_exists = {r[0] for r in conn.execute("SELECT position_id FROM outcome_fact")}
    print(f"Already in outcome_fact: {len(already_exists)} rows")

    inserted = 0
    skipped = 0
    errors = 0

    for row in settlements:
        trade_id = row["trade_id"]
        settled_at = row["timestamp"]
        details = json.loads(row["details_json"]) if row["details_json"] else {}

        if trade_id in already_exists:
            skipped += 1
            continue

        pnl = details.get("pnl")
        outcome_val = details.get("outcome")  # 1=win, 0=loss typically
        if outcome_val is None:
            won = details.get("position_won") or details.get("won")
            outcome_val = 1 if won else 0

        decision_snapshot_id = str(details.get("decision_snapshot_id", "")) or None
        strategy_key = details.get("strategy") or _get_strategy_key(conn, trade_id)
        entered_at = _get_entered_at(conn, trade_id)

        if dry_run:
            print(
                f"  [DRY-RUN] would insert: position_id={trade_id} "
                f"pnl={pnl} outcome={outcome_val} settled_at={settled_at} "
                f"strategy_key={strategy_key}"
            )
            inserted += 1
            continue

        try:
            conn.execute(
                """
                INSERT OR IGNORE INTO outcome_fact (
                    position_id, strategy_key, entered_at, settled_at,
                    exit_reason, decision_snapshot_id, pnl, outcome
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    trade_id,
                    strategy_key,
                    entered_at,
                    settled_at,
                    "settlement",
                    decision_snapshot_id,
                    pnl,
                    outcome_val,
                ),
            )
            inserted += 1
        except Exception as exc:
            print(f"  ERROR inserting {trade_id}: {exc}", file=sys.stderr)
            errors += 1

    if not dry_run:
        conn.commit()

    print(f"\nResult: inserted={inserted} skipped={skipped} errors={errors}")
    if not dry_run and inserted > 0:
        total = conn.execute("SELECT COUNT(*) FROM outcome_fact").fetchone()[0]
        print(f"outcome_fact total rows now: {total}")
    summary = {
        "status": "dry_run" if dry_run else ("applied" if errors == 0 else "applied_with_errors"),
        "dry_run": dry_run,
        "db_path": str(db_path),
        "authority_scope": LEGACY_OUTCOME_FACT_AUTHORITY_SCOPE,
        "inserted": inserted,
        "skipped": skipped,
        "errors": errors,
    }
    conn.close()
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Backfill legacy outcome_fact lifecycle projections from chronicle")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", dest="dry_run", action="store_true", default=True, help="Print what would be inserted without writing")
    mode.add_argument("--apply", dest="dry_run", action="store_false", help="Write legacy outcome_fact rows")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH, help="Trade DB path")
    parser.add_argument(
        "--confirm-legacy-outcome-fact-backfill",
        action="store_true",
        help="Required with --apply; confirms operator-approved dry-run, backup, and rollback plan",
    )
    args = parser.parse_args()
    if not args.dry_run and not args.confirm_legacy_outcome_fact_backfill:
        parser.error("--apply requires --confirm-legacy-outcome-fact-backfill")
    result = backfill(dry_run=args.dry_run, db_path=args.db)
    if result["status"].startswith("error"):
        print(f"ERROR: {result['status']} at {result['db_path']}", file=sys.stderr)
        sys.exit(1)
