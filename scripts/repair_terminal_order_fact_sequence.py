#!/usr/bin/env python3
# Lifecycle: created=2026-07-04; last_reviewed=2026-07-04; last_reused=never
# Purpose: Append-only repair for terminal ENTRY commands whose latest
#   venue_order_facts row regressed to an open/partial state after terminal proof.
# Reuse: Run with --json first; use --apply only after operator approval.
# Authority basis: AGENTS.md position/execution proof gates; scripts/AGENTS.md repair contract.
"""Repair stale latest order facts by re-appending existing terminal proof.

This script does not place, cancel, or query venue orders.  It copies an already
persisted terminal order fact for the same command/order to the end of the
append-only venue_order_facts stream so latest-fact consumers stop treating a
terminal command as an open rest.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.state.db import get_trade_connection, get_trade_connection_read_only, utc_iso_now
from src.state.venue_command_repo import append_order_fact


SOURCE_MODULE = "scripts.repair_terminal_order_fact_sequence"
REPAIR_REASON = "terminal_order_fact_sequence_repair"
TERMINAL_ENTRY_COMMAND_STATES = frozenset(
    {"CANCELLED", "CANCELED", "EXPIRED", "FILLED", "REJECTED", "SUBMIT_REJECTED"}
)
OPEN_OR_PARTIAL_ORDER_STATES = frozenset({"LIVE", "RESTING", "PARTIALLY_MATCHED"})
TERMINAL_NO_RESTING_ORDER_STATES = frozenset(
    {"MATCHED", "CANCEL_CONFIRMED", "EXPIRED", "VENUE_WIPED"}
)


@dataclass(frozen=True)
class RepairCandidate:
    command_id: str
    venue_order_id: str
    command_state: str
    latest_state: str
    latest_remaining_size: str | None
    latest_matched_size: str | None
    latest_fact_id: int
    terminal_state: str
    terminal_remaining_size: str | None
    terminal_matched_size: str | None
    terminal_fact_id: int
    terminal_observed_at: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "command_id": self.command_id,
            "venue_order_id": self.venue_order_id,
            "command_state": self.command_state,
            "latest_state": self.latest_state,
            "latest_remaining_size": self.latest_remaining_size,
            "latest_matched_size": self.latest_matched_size,
            "latest_fact_id": self.latest_fact_id,
            "terminal_state": self.terminal_state,
            "terminal_remaining_size": self.terminal_remaining_size,
            "terminal_matched_size": self.terminal_matched_size,
            "terminal_fact_id": self.terminal_fact_id,
            "terminal_observed_at": self.terminal_observed_at,
        }


def _placeholders(values: frozenset[str]) -> str:
    return ",".join("?" for _ in values)


def find_candidates(conn: sqlite3.Connection) -> list[RepairCandidate]:
    """Return terminal commands whose latest order fact contradicts terminal proof."""

    command_placeholders = _placeholders(TERMINAL_ENTRY_COMMAND_STATES)
    latest_placeholders = _placeholders(OPEN_OR_PARTIAL_ORDER_STATES)
    terminal_placeholders = _placeholders(TERMINAL_NO_RESTING_ORDER_STATES)
    rows = conn.execute(
        f"""
        WITH latest_order_facts AS (
            SELECT fact_id, venue_order_id, command_id, state, remaining_size,
                   matched_size, observed_at, local_sequence,
                   ROW_NUMBER() OVER (
                       PARTITION BY venue_order_id
                       ORDER BY COALESCE(local_sequence, 0) DESC,
                                COALESCE(observed_at, '') DESC,
                                fact_id DESC
                   ) AS rn
              FROM venue_order_facts
        ),
        terminal_order_facts AS (
            SELECT fact_id, venue_order_id, command_id, state, remaining_size,
                   matched_size, observed_at, local_sequence,
                   ROW_NUMBER() OVER (
                       PARTITION BY venue_order_id, command_id
                       ORDER BY COALESCE(local_sequence, 0) DESC,
                                COALESCE(observed_at, '') DESC,
                                fact_id DESC
                   ) AS rn
              FROM venue_order_facts
             WHERE UPPER(COALESCE(state, '')) IN ({terminal_placeholders})
               AND (
                    UPPER(COALESCE(state, '')) != 'MATCHED'
                 OR CAST(COALESCE(remaining_size, '0') AS REAL) = 0
               )
        )
        SELECT vc.command_id,
               vc.venue_order_id,
               UPPER(COALESCE(vc.state, '')) AS command_state,
               lof.state AS latest_state,
               lof.remaining_size AS latest_remaining_size,
               lof.matched_size AS latest_matched_size,
               lof.fact_id AS latest_fact_id,
               tof.state AS terminal_state,
               tof.remaining_size AS terminal_remaining_size,
               tof.matched_size AS terminal_matched_size,
               tof.fact_id AS terminal_fact_id,
               tof.observed_at AS terminal_observed_at
          FROM venue_commands vc
          JOIN latest_order_facts lof
            ON lof.venue_order_id = vc.venue_order_id
           AND lof.command_id = vc.command_id
           AND lof.rn = 1
          JOIN terminal_order_facts tof
            ON tof.venue_order_id = vc.venue_order_id
           AND tof.command_id = vc.command_id
           AND tof.rn = 1
         WHERE UPPER(COALESCE(vc.intent_kind, '')) = 'ENTRY'
           AND vc.venue_order_id IS NOT NULL
           AND TRIM(vc.venue_order_id) != ''
           AND UPPER(COALESCE(vc.state, '')) IN ({command_placeholders})
           AND UPPER(COALESCE(lof.state, '')) IN ({latest_placeholders})
         ORDER BY vc.updated_at DESC, vc.command_id
        """,
        tuple(sorted(TERMINAL_NO_RESTING_ORDER_STATES))
        + tuple(sorted(TERMINAL_ENTRY_COMMAND_STATES))
        + tuple(sorted(OPEN_OR_PARTIAL_ORDER_STATES)),
    ).fetchall()
    return [
        RepairCandidate(
            command_id=str(row["command_id"] or ""),
            venue_order_id=str(row["venue_order_id"] or ""),
            command_state=str(row["command_state"] or ""),
            latest_state=str(row["latest_state"] or ""),
            latest_remaining_size=row["latest_remaining_size"],
            latest_matched_size=row["latest_matched_size"],
            latest_fact_id=int(row["latest_fact_id"]),
            terminal_state=str(row["terminal_state"] or ""),
            terminal_remaining_size=row["terminal_remaining_size"],
            terminal_matched_size=row["terminal_matched_size"],
            terminal_fact_id=int(row["terminal_fact_id"]),
            terminal_observed_at=str(row["terminal_observed_at"] or ""),
        )
        for row in rows
    ]


def apply_candidate(
    conn: sqlite3.Connection,
    candidate: RepairCandidate,
    *,
    observed_at: str,
) -> int:
    payload = {
        "schema_version": 1,
        "reason": REPAIR_REASON,
        "source_module": SOURCE_MODULE,
        "command_id": candidate.command_id,
        "venue_order_id": candidate.venue_order_id,
        "copied_terminal_fact_id": candidate.terminal_fact_id,
        "superseded_latest_fact_id": candidate.latest_fact_id,
        "terminal_state": candidate.terminal_state,
        "latest_state_before_repair": candidate.latest_state,
        "semantic_guard": "append_only_no_venue_action_no_row_update",
    }
    payload_json = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return append_order_fact(
        conn,
        venue_order_id=candidate.venue_order_id,
        command_id=candidate.command_id,
        state=candidate.terminal_state,
        remaining_size=candidate.terminal_remaining_size,
        matched_size=candidate.terminal_matched_size,
        source="OPERATOR",
        observed_at=observed_at,
        raw_payload_hash=hashlib.sha256(payload_json.encode("utf-8")).hexdigest(),
        raw_payload_json=payload,
    )


def run(*, apply: bool) -> dict[str, Any]:
    conn = get_trade_connection(write_class="live") if apply else get_trade_connection_read_only()
    conn.row_factory = sqlite3.Row
    try:
        candidates = find_candidates(conn)
        applied: list[dict[str, Any]] = []
        if apply:
            observed_at = utc_iso_now()
            for candidate in candidates:
                fact_id = apply_candidate(conn, candidate, observed_at=observed_at)
                applied.append(
                    {
                        **candidate.as_dict(),
                        "appended_fact_id": fact_id,
                        "repaired_at": observed_at,
                    }
                )
            conn.commit()
        return {
            "ok": True,
            "apply": apply,
            "candidate_count": len(candidates),
            "candidates": [candidate.as_dict() for candidate in candidates],
            "applied": applied,
            "venue_action": False,
            "db_backup_created": False,
        }
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Append repair facts.")
    parser.add_argument("--json", action="store_true", help="Emit JSON.")
    args = parser.parse_args(argv)
    result = run(apply=bool(args.apply))
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(
            "terminal order-fact sequence repair: "
            f"{'APPLY' if args.apply else 'DRY-RUN'} candidates={result['candidate_count']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
