#!/usr/bin/env python3
"""Backfill outcome_fact from chronicle SETTLEMENT events.

The SD-2 fix ensures future settlements write to outcome_fact.
This script backfills the 19 historical settlements that pre-dated SD-2.

Linkage:
    chronicle.trade_id  ->  position_events_legacy.runtime_trade_id
    chronicle.trade_id is used as position_id in outcome_fact

Run:
    python3 scripts/backfill_outcome_fact.py
    python3 scripts/backfill_outcome_fact.py --dry-run
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "state" / "zeus.db"


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


def backfill(dry_run: bool = False) -> None:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    # Verify outcome_fact exists
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "outcome_fact" not in tables:
        print("ERROR: outcome_fact table does not exist", file=sys.stderr)
        sys.exit(1)

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


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Backfill outcome_fact from chronicle")
    parser.add_argument("--dry-run", action="store_true", help="Print what would be inserted without writing")
    args = parser.parse_args()
    backfill(dry_run=args.dry_run)
