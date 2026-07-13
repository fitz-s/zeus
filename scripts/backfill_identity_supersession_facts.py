#!/usr/bin/env python3
# Created: 2026-07-13
# Last reused or audited: 2026-07-13
# Lifecycle: created=2026-07-13; last_reviewed=2026-07-13; last_reused=never
# Purpose: LX-F history conversion (docs/rebuild/local_ledger_excision_2026-07-12.md
#   Round-2 delta, duplicate_consolidator special handling). Before this packet,
#   src.state.position_duplicate_consolidator._merge_equivalent_rows recorded a
#   duplicate-position merge as a MANUAL_OVERRIDE_APPLIED event carrying
#   synthesized merged shares/cost_basis_usd/entry_price -- and mutated the
#   keeper row's economics to match. This script does NOT touch position_current
#   (the historical economics stay whatever they already are); it scans past
#   consolidator merges and emits the equivalent POSITION_IDENTITY_SUPERSEDED
#   FACT (keeper_position_id/absorbed_position_ids/evidence_refs) for each one,
#   so a future read-model reducer can dedup ALL history by this relation
#   instead of only the ones written after this packet landed.
# Reuse: DRY-RUN by default; --apply writes. Re-running after --apply is a
#   no-op (idempotency check below skips any keeper that already carries a
#   POSITION_IDENTITY_SUPERSEDED event naming the same absorbed_position_ids).
# Authority basis: docs/rebuild/local_ledger_excision_2026-07-12.md Round-2
#   delta "六站点特判" duplicate_consolidator entry.
"""Backfill POSITION_IDENTITY_SUPERSEDED facts from historical consolidator merges.

Scope: position_events rows written by
`src.state.position_duplicate_consolidator._merge_equivalent_rows` before this
packet -- identified by event_type='MANUAL_OVERRIDE_APPLIED',
source_module='src.state.position_duplicate_consolidator', and
payload_json.reason == the consolidator's _MERGED_REASON sentinel. For each
one this script emits ONE new POSITION_IDENTITY_SUPERSEDED event on the same
keeper position_id, carrying:

  - keeper_position_id: the historical event's own position_id (the merge
    keeper never changes identity across this conversion).
  - absorbed_position_ids: read verbatim from the historical payload.
  - evidence_refs: the dup-detection evidence already captured in the
    historical payload (token_id, chain_shares, db_total_shares renamed to
    db_total_shares_before for shape parity with the live builder, and
    per-row shares_before) -- never the historical payload's SYNTHESIZED
    economics keys (shares_after / cost_basis_usd_after), which this script
    deliberately drops.
  - occurred_at: the historical event's own occurred_at (the merge decision
    time, not the backfill run time) -- so a reducer replaying history sees
    the fact at the moment the merge was actually decided.

This script never mutates position_current and never rewrites the historical
MANUAL_OVERRIDE_APPLIED row (position_events is append-only) -- it only
appends new rows.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import uuid
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _historical_merge_events(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    from src.state.position_duplicate_consolidator import _MERGED_REASON

    rows = conn.execute(
        """
        SELECT event_id, position_id, occurred_at, phase_after, strategy_key,
               payload_json
          FROM position_events
         WHERE event_type = 'MANUAL_OVERRIDE_APPLIED'
           AND source_module = 'src.state.position_duplicate_consolidator'
         ORDER BY occurred_at ASC, event_id ASC
        """
    ).fetchall()
    out: list[sqlite3.Row] = []
    for row in rows:
        try:
            payload = json.loads(row["payload_json"] or "{}")
        except json.JSONDecodeError:
            continue
        if payload.get("reason") != _MERGED_REASON:
            continue
        out.append(row)
    return out


def _already_superseded(conn: sqlite3.Connection, *, keeper_position_id: str, absorbed_position_ids: list[str]) -> bool:
    """True iff keeper_position_id already carries a POSITION_IDENTITY_SUPERSEDED
    event naming this exact absorbed set (idempotency across re-runs)."""
    rows = conn.execute(
        """
        SELECT payload_json FROM position_events
         WHERE position_id = ? AND event_type = 'POSITION_IDENTITY_SUPERSEDED'
        """,
        (keeper_position_id,),
    ).fetchall()
    target = sorted(absorbed_position_ids)
    for row in rows:
        try:
            payload = json.loads(row["payload_json"] or "{}")
        except json.JSONDecodeError:
            continue
        if sorted(payload.get("absorbed_position_ids") or []) == target:
            return True
    return False


def _plan_backfill(conn: sqlite3.Connection) -> tuple[list[dict], list[dict]]:
    """Return (planned, skipped) without writing anything.

    planned: [{historical_event_id, keeper_position_id, absorbed_position_ids,
               evidence_refs, occurred_at, phase_after, strategy_key}, ...]
    skipped: [{historical_event_id, position_id, reason}, ...]
    """
    planned: list[dict] = []
    skipped: list[dict] = []
    for row in _historical_merge_events(conn):
        keeper_position_id = str(row["position_id"])
        try:
            payload = json.loads(row["payload_json"] or "{}")
        except json.JSONDecodeError:
            skipped.append(
                {
                    "historical_event_id": row["event_id"],
                    "position_id": keeper_position_id,
                    "reason": "unparseable historical payload_json",
                }
            )
            continue

        absorbed_position_ids = [str(pid) for pid in (payload.get("absorbed_position_ids") or [])]
        if not absorbed_position_ids:
            skipped.append(
                {
                    "historical_event_id": row["event_id"],
                    "position_id": keeper_position_id,
                    "reason": "historical payload has no absorbed_position_ids",
                }
            )
            continue

        if _already_superseded(
            conn, keeper_position_id=keeper_position_id, absorbed_position_ids=absorbed_position_ids
        ):
            skipped.append(
                {
                    "historical_event_id": row["event_id"],
                    "position_id": keeper_position_id,
                    "reason": "POSITION_IDENTITY_SUPERSEDED already recorded for this keeper/absorbed set",
                }
            )
            continue

        # Evidence-only projection of the historical payload -- deliberately
        # excludes the synthesized economics keys (shares_after,
        # cost_basis_usd_after) the pre-LX-F consolidator used to write.
        evidence_refs = {
            "token_id": payload.get("token_id"),
            "chain_shares": payload.get("chain_shares"),
            "db_total_shares_before": payload.get("db_total_shares"),
            "shares_before": payload.get("shares_before"),
        }

        planned.append(
            {
                "historical_event_id": row["event_id"],
                "keeper_position_id": keeper_position_id,
                "absorbed_position_ids": absorbed_position_ids,
                "evidence_refs": evidence_refs,
                "occurred_at": str(row["occurred_at"]),
                "phase_after": str(row["phase_after"] or ""),
                "strategy_key": str(row["strategy_key"] or ""),
            }
        )

    return planned, skipped


def _apply_backfill(conn: sqlite3.Connection, planned: list[dict]) -> int:
    from src.engine.lifecycle_events import (
        build_position_identity_superseded_canonical_write,
    )

    written = 0
    with conn:
        for item in planned:
            keeper_position_id = item["keeper_position_id"]
            seq_row = conn.execute(
                "SELECT COALESCE(MAX(sequence_no), 0) FROM position_events WHERE position_id = ?",
                (keeper_position_id,),
            ).fetchone()
            next_seq = int(seq_row[0]) + 1

            phase_after = item["phase_after"]
            if not phase_after:
                # Historical rows always carry phase_after (the consolidator's
                # builder set phase_before == phase_after == keeper's phase at
                # merge time); this branch is defensive only.
                current = conn.execute(
                    "SELECT phase FROM position_current WHERE position_id = ?",
                    (keeper_position_id,),
                ).fetchone()
                phase_after = str(current[0]) if current else "unknown"

            event = build_position_identity_superseded_canonical_write(
                keeper_position_id=keeper_position_id,
                absorbed_position_ids=item["absorbed_position_ids"],
                evidence_refs=item["evidence_refs"],
                occurred_at=item["occurred_at"],
                sequence_no=next_seq,
                phase_after=phase_after,
                strategy_key=item["strategy_key"],
            )
            # event_id derived from keeper+sequence could collide with a
            # concurrent writer between the SELECT above and this INSERT;
            # append a backfill-run suffix to keep the PRIMARY KEY unique
            # without relying on that race window being closed elsewhere.
            event["event_id"] = f"{event['event_id']}:backfill:{uuid.uuid4().hex[:8]}"
            conn.execute(
                """
                INSERT INTO position_events (
                    event_id, position_id, event_version, sequence_no, event_type,
                    occurred_at, phase_before, phase_after, strategy_key,
                    source_module, payload_json, env
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event["event_id"],
                    event["position_id"],
                    event["event_version"],
                    event["sequence_no"],
                    event["event_type"],
                    event["occurred_at"],
                    event["phase_before"],
                    event["phase_after"],
                    event["strategy_key"],
                    "scripts.backfill_identity_supersession_facts",
                    event["payload_json"],
                    "live",
                ),
            )
            written += 1
    return written


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--apply",
        action="store_true",
        help="Write the backfilled POSITION_IDENTITY_SUPERSEDED facts. "
        "Default is dry-run (report only).",
    )
    args = ap.parse_args(argv)

    from src.state.db import get_trade_connection_read_only

    read_conn = get_trade_connection_read_only()
    read_conn.row_factory = sqlite3.Row

    try:
        planned, skipped = _plan_backfill(read_conn)
    finally:
        read_conn.close()

    print(f"LX-F identity-supersession backfill (dry_run={not args.apply})")
    print(f"  historical merges to convert: {len(planned)}")
    print(f"  skipped (already converted / unrecoverable): {len(skipped)}")
    print()
    for item in planned:
        print(
            f"  BOOK  keeper={item['keeper_position_id']:40s} "
            f"absorbed={item['absorbed_position_ids']} occurred_at={item['occurred_at']}"
        )
    for item in skipped:
        print(
            f"  SKIP  position_id={item['position_id']:40s} "
            f"historical_event_id={item['historical_event_id']} reason={item['reason']}"
        )

    if not args.apply:
        print()
        print("Dry run only -- no writes performed. Re-run with --apply to write.")
        return 0

    from src.state.db import get_trade_connection

    write_conn = get_trade_connection(write_class="bulk")
    write_conn.row_factory = sqlite3.Row

    try:
        written = _apply_backfill(write_conn, planned)
    finally:
        write_conn.close()

    print(f"\nApplied {written} POSITION_IDENTITY_SUPERSEDED facts.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
