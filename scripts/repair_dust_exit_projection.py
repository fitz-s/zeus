#!/usr/bin/env python3
# Lifecycle: created=2026-06-18; last_reviewed=2026-07-09; last_reused=2026-07-09
# Purpose: Repair restart-visible dust exit projections after canonical backoff evidence exists.
# Reuse: Run with --dry-run before --apply when live restart preflight reports dust projection reload risk.
# Created: 2026-06-18
# Last reused or audited: 2026-07-09
# Authority basis: AGENTS.md position/execution truth gate; scripts/AGENTS.md repair contract.
"""Repair dust pending-exit projections without placing or canceling orders."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.state.db import get_trade_connection, utc_iso_now
from src.state.ledger import append_many_and_project
from src.state.projection import CANONICAL_POSITION_CURRENT_COLUMNS, upsert_position_current


DUST_SHARE_LIMIT = 0.01
SOURCE_MODULE = "scripts.repair_dust_exit_projection"
REPAIR_REASON = "dust_backoff_projection_reload_repair"
SNAPSHOT_MIN_ORDER_DUST = "snapshot_min_order_dust"


@dataclass
class RepairCandidate:
    position_id: str
    city: str
    target_date: str
    bin_label: str
    shares: float
    current_phase: str
    order_status: str
    current_exit_reason: str
    target_exit_reason: str
    backoff_events: int
    latest_backoff_at: str | None
    latest_backoff_error: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "position_id": self.position_id,
            "city": self.city,
            "target_date": self.target_date,
            "bin_label": self.bin_label,
            "shares": self.shares,
            "current_phase": self.current_phase,
            "order_status": self.order_status,
            "current_exit_reason": self.current_exit_reason,
            "target_exit_reason": self.target_exit_reason,
            "backoff_events": self.backoff_events,
            "latest_backoff_at": self.latest_backoff_at,
            "latest_backoff_error": self.latest_backoff_error,
        }


def _row_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {key: row[key] for key in row.keys()}


def _latest_sequence_no(conn: sqlite3.Connection, position_id: str) -> int:
    row = conn.execute(
        "SELECT COALESCE(MAX(sequence_no), 0) AS seq FROM position_events WHERE position_id = ?",
        (position_id,),
    ).fetchone()
    return int(row["seq"] if row is not None else 0)


def _existing_repair_event(conn: sqlite3.Connection, position_id: str) -> bool:
    row = conn.execute(
        """
        SELECT 1
          FROM position_events
         WHERE position_id = ?
           AND event_type = 'EXIT_ORDER_REJECTED'
           AND idempotency_key = ?
         LIMIT 1
        """,
        (position_id, f"{position_id}:{REPAIR_REASON}"),
    ).fetchone()
    return row is not None


def repair_candidates(conn: sqlite3.Connection) -> list[RepairCandidate]:
    """Return projection rows safe to repair from existing backoff evidence."""
    rows = conn.execute(
        """
        WITH backoff_events AS (
            SELECT pe.position_id,
                   pe.sequence_no,
                   pe.occurred_at,
                   json_extract(pe.payload_json, '$.exit_reason') AS event_exit_reason,
                   json_extract(pe.payload_json, '$.error') AS event_error,
                   json_extract(pe.payload_json, '$.exit_block_class') AS event_block_class
              FROM position_events pe
             WHERE pe.event_type = 'EXIT_ORDER_REJECTED'
               AND json_extract(pe.payload_json, '$.status') = 'backoff_exhausted'
        ),
        backoff_counts AS (
            SELECT position_id,
                   COUNT(*) AS backoff_events,
                   MAX(occurred_at) AS latest_backoff_at
              FROM backoff_events
             GROUP BY position_id
        ),
        latest_backoff AS (
            SELECT be.*
              FROM backoff_events be
              JOIN (
                    SELECT position_id, MAX(sequence_no) AS sequence_no
                      FROM backoff_events
                     GROUP BY position_id
                   ) latest
                ON latest.position_id = be.position_id
               AND latest.sequence_no = be.sequence_no
        )
        SELECT pc.position_id,
               pc.city,
               pc.target_date,
               pc.bin_label,
               COALESCE(pc.chain_shares, pc.shares, 0) AS shares,
               COALESCE(pc.phase, '') AS current_phase,
               COALESCE(pc.order_status, '') AS order_status,
               COALESCE(pc.exit_reason, '') AS current_exit_reason,
               COALESCE(lb.event_exit_reason, '') AS target_exit_reason,
               COALESCE(bc.backoff_events, 0) AS backoff_events,
               bc.latest_backoff_at AS latest_backoff_at,
               COALESCE(lb.event_error, '') AS latest_backoff_error
          FROM position_current pc
          JOIN latest_backoff lb
            ON lb.position_id = pc.position_id
          JOIN backoff_counts bc
            ON bc.position_id = pc.position_id
         WHERE (
             pc.phase = 'pending_exit'
             AND pc.exit_reason = 'EXIT_CHAIN_DUST_STILL_HELD'
             AND lb.event_exit_reason = pc.exit_reason
             AND COALESCE(pc.chain_shares, pc.shares, 0) > 0
             AND COALESCE(pc.chain_shares, pc.shares, 0) <= ?
             AND COALESCE(pc.order_status, '') != 'backoff_exhausted'
           )
            OR (
            COALESCE(pc.chain_shares, pc.shares, 0) > 0
             AND COALESCE(pc.phase, '') NOT IN (
                    'settled',
                    'voided',
                    'admin_closed',
                    'quarantined',
                    'economically_closed'
                 )
             AND (
                    lb.event_block_class = ?
                    OR lb.event_error LIKE '%min_order_size%'
                    OR lb.event_exit_reason LIKE '%[DUST:%min_order_size%'
                 )
             AND NOT (
                    pc.phase = 'pending_exit'
                    AND COALESCE(pc.order_status, '') = 'backoff_exhausted'
                    AND COALESCE(pc.exit_reason, '') = COALESCE(lb.event_exit_reason, '')
                 )
           )
         GROUP BY pc.position_id
         ORDER BY pc.city, pc.target_date, pc.position_id
        """,
        (DUST_SHARE_LIMIT, SNAPSHOT_MIN_ORDER_DUST),
    ).fetchall()
    return [
        RepairCandidate(
            position_id=str(row["position_id"] or ""),
            city=str(row["city"] or ""),
            target_date=str(row["target_date"] or ""),
            bin_label=str(row["bin_label"] or ""),
            shares=float(row["shares"] or 0.0),
            current_phase=str(row["current_phase"] or ""),
            order_status=str(row["order_status"] or ""),
            current_exit_reason=str(row["current_exit_reason"] or ""),
            target_exit_reason=str(row["target_exit_reason"] or ""),
            backoff_events=int(row["backoff_events"] or 0),
            latest_backoff_at=row["latest_backoff_at"],
            latest_backoff_error=str(row["latest_backoff_error"] or ""),
        )
        for row in rows
    ]


def _projection_for_repair(
    conn: sqlite3.Connection,
    candidate: RepairCandidate,
    occurred_at: str,
) -> dict[str, Any]:
    row = conn.execute(
        "SELECT * FROM position_current WHERE position_id = ?",
        (candidate.position_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"position_current row missing for {candidate.position_id}")
    projection = _row_dict(row)
    missing = [col for col in CANONICAL_POSITION_CURRENT_COLUMNS if col not in projection]
    if missing:
        raise ValueError(f"position_current missing canonical columns: {missing}")
    projection["phase"] = "pending_exit"
    projection["order_status"] = "backoff_exhausted"
    projection["exit_reason"] = candidate.target_exit_reason or candidate.current_exit_reason
    projection["next_exit_retry_at"] = ""
    projection["exit_retry_count"] = 0
    projection["updated_at"] = occurred_at
    return {col: projection.get(col) for col in CANONICAL_POSITION_CURRENT_COLUMNS}


def apply_repair(
    conn: sqlite3.Connection,
    candidate: RepairCandidate,
    *,
    occurred_at: str,
) -> str:
    projection = _projection_for_repair(conn, candidate, occurred_at)
    if _existing_repair_event(conn, candidate.position_id):
        upsert_position_current(conn, projection)
        return "projection_refreshed"
    event_id = f"{candidate.position_id}:{REPAIR_REASON}"
    event = {
        "event_id": event_id,
        "position_id": candidate.position_id,
        "event_version": 1,
        "sequence_no": _latest_sequence_no(conn, candidate.position_id) + 1,
        "event_type": "EXIT_ORDER_REJECTED",
        "occurred_at": occurred_at,
        "phase_before": candidate.current_phase or projection.get("phase"),
        "phase_after": "pending_exit",
        "strategy_key": projection.get("strategy_key"),
        "decision_id": None,
        "snapshot_id": projection.get("decision_snapshot_id"),
        "order_id": projection.get("order_id"),
        "command_id": None,
        "caused_by": f"position_event:{candidate.position_id}:EXIT_ORDER_REJECTED",
        "idempotency_key": event_id,
        "venue_status": "backoff_exhausted",
        "source_module": SOURCE_MODULE,
        "env": "live",
        "payload_json": json.dumps(
            {
                "reason": REPAIR_REASON,
                "status": "backoff_exhausted",
                "exit_reason": projection.get("exit_reason"),
                "old_phase": candidate.current_phase,
                "new_phase": "pending_exit",
                "old_order_status": candidate.order_status,
                "new_order_status": "backoff_exhausted",
                "old_exit_reason": candidate.current_exit_reason,
                "chain_shares": candidate.shares,
                "dust_share_limit": DUST_SHARE_LIMIT,
                "exit_block_class": SNAPSHOT_MIN_ORDER_DUST,
                "latest_backoff_error": candidate.latest_backoff_error,
                "backoff_events": candidate.backoff_events,
                "latest_backoff_at": candidate.latest_backoff_at,
                "semantic_guard": "repair_projection_only_no_venue_action",
            },
            sort_keys=True,
        ),
    }
    append_many_and_project(conn, [event], projection)
    return "event_appended_and_projection_repaired"


def run(*, apply: bool) -> dict[str, Any]:
    conn = get_trade_connection(write_class="live" if apply else None)
    conn.row_factory = sqlite3.Row
    try:
        candidates = repair_candidates(conn)
        applied: list[dict[str, Any]] = []
        if apply:
            occurred_at = utc_iso_now()
            for candidate in candidates:
                result = apply_repair(conn, candidate, occurred_at=occurred_at)
                applied.append({**candidate.as_dict(), "result": result, "repaired_at": occurred_at})
        return {
            "ok": True,
            "apply": apply,
            "candidate_count": len(candidates),
            "candidates": [candidate.as_dict() for candidate in candidates],
            "applied": applied,
            "venue_action": False,
        }
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Write the projection repair.")
    parser.add_argument("--json", action="store_true", help="Emit JSON.")
    args = parser.parse_args(argv)
    result = run(apply=bool(args.apply))
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(
            "dust exit projection repair: "
            f"{'APPLY' if args.apply else 'DRY-RUN'} candidates={result['candidate_count']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
