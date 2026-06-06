#!/usr/bin/env python
# Created: 2026-06-01
# Last reused or audited: 2026-06-01
# Authority basis: EDLI live-order aggregate event-sourcing law
#   (src/events/live_order_aggregate.py), live-cap ledger
#   (src/events/live_cap.py), boot readiness gate
#   (src/main.py::_assert_edli_stage_readiness). INV-37 single-DB world write.
#
# Purpose
# -------
# Resolve a stuck EDLI live-order aggregate that is parked in PENDING_RECONCILE
# with its $5 LIVE_CAP reservation still RESERVED, which fail-closes the
# edli_live_canary boot readiness gate (EDLI_STAGE_UNRESOLVED_SUBMIT_UNKNOWN +
# EDLI_STAGE_LIVE_CAP_RESERVED) and crash-loops the daemon.
#
# This script is ONLY for aggregates that provably never reached the venue. A
# post-submit unknown with venue_call_started=true must be reconciled from
# authenticated venue/user-channel reads; venue_order_id=NULL alone is not enough
# evidence.
#
# This script uses the system's OWN forward-only event-sourcing mechanism — it
# APPENDS a Reconciled event then a CapTransitioned(RELEASED) event and calls
# the canonical LiveCapLedger.release(). It performs NO raw UPDATE/DELETE of
# existing aggregate or audit rows. It is idempotent: re-running it after the
# aggregate is already terminal is a no-op.
#
# Usage:
#   .venv/bin/python scripts/resolve_unresolved_edli_submit.py            # dry-run report
#   .venv/bin/python scripts/resolve_unresolved_edli_submit.py --apply    # apply resolution

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone

from src.state.db import get_world_connection, get_world_connection_read_only, world_write_lock
from src.events.live_order_aggregate import LiveOrderAggregateLedger
from src.events.live_order_reconcile import append_reconciled, RECONCILE_SOURCE
from src.events.live_cap import LiveCapLedger


RESOLUTION_REASON = "PRE_VENUE_DEPTH_INSUFFICIENT_NEVER_SUBMITTED"


def _submit_unknown_venue_call_started(conn, aggregate_id: str) -> bool:
    row = conn.execute(
        """
        SELECT payload_json FROM edli_live_order_events
        WHERE aggregate_id = ? AND event_type = 'SubmitUnknown'
        ORDER BY event_sequence DESC LIMIT 1
        """,
        (aggregate_id,),
    ).fetchone()
    if row is None:
        return False
    return bool(json.loads(row["payload_json"]).get("venue_call_started"))


def _stuck_aggregates(conn) -> list[str]:
    """Aggregates whose projection is pending_reconcile AND whose order never
    reached the venue (venue_order_id IS NULL across the whole event chain)."""
    rows = conn.execute(
        """
        SELECT aggregate_id
        FROM edli_live_order_projection
        WHERE pending_reconcile = 1
        """
    ).fetchall()
    stuck = []
    for r in rows:
        agg = r["aggregate_id"]
        # Confirm the order never obtained a venue_order_id (pre-venue rejection).
        # This is necessary but not sufficient: post-submit unknowns may also lack
        # a venue_order_id when the SDK raised after the venue call was started.
        if _submit_unknown_venue_call_started(conn, agg):
            continue
        ack = conn.execute(
            """
            SELECT 1 FROM edli_live_order_events
            WHERE aggregate_id = ?
              AND json_extract(payload_json, '$.venue_order_id') IS NOT NULL
            LIMIT 1
            """,
            (agg,),
        ).fetchone()
        if ack is None:
            stuck.append(agg)
    return stuck


def _latest_command_payload(conn, aggregate_id: str) -> dict:
    row = conn.execute(
        """
        SELECT payload_json FROM edli_live_order_events
        WHERE aggregate_id = ? AND event_type = 'ExecutionCommandCreated'
        ORDER BY event_sequence DESC LIMIT 1
        """,
        (aggregate_id,),
    ).fetchone()
    if row is None:
        raise SystemExit(f"NO ExecutionCommandCreated for {aggregate_id}")
    return json.loads(row["payload_json"])


def _latest_receipt_hash(conn, aggregate_id: str) -> str:
    """Reuse the execution_receipt_hash carried by the latest CapTransitioned /
    SubmitUnknown event so the new CapTransitioned binds to the same receipt."""
    row = conn.execute(
        """
        SELECT payload_json FROM edli_live_order_events
        WHERE aggregate_id = ?
          AND json_extract(payload_json, '$.execution_receipt_hash') IS NOT NULL
        ORDER BY event_sequence DESC LIMIT 1
        """,
        (aggregate_id,),
    ).fetchone()
    if row is None:
        raise SystemExit(f"NO execution_receipt_hash for {aggregate_id}")
    return str(json.loads(row["payload_json"])["execution_receipt_hash"])


def _cap_usage_id_for(conn, final_intent_id: str) -> str | None:
    row = conn.execute(
        """
        SELECT usage_id FROM edli_live_cap_usage
        WHERE final_intent_id = ? AND reservation_status = 'RESERVED'
        """,
        (final_intent_id,),
    ).fetchone()
    return str(row["usage_id"]) if row is not None else None


def _readiness_counts(conn) -> tuple[int, int]:
    unresolved = conn.execute(
        "SELECT COUNT(*) c FROM edli_live_order_projection WHERE pending_reconcile = 1"
    ).fetchone()["c"]
    reserved = conn.execute(
        "SELECT COUNT(*) c FROM edli_live_cap_usage WHERE reservation_status = 'RESERVED'"
    ).fetchone()["c"]
    return int(unresolved), int(reserved)


def resolve(apply: bool) -> int:
    ro = get_world_connection_read_only()
    try:
        before_unresolved, before_reserved = _readiness_counts(ro)
        stuck = _stuck_aggregates(ro)
        print(f"BEFORE: unresolved_submit={before_unresolved} reserved_cap={before_reserved}")
        print(f"pre-venue stuck aggregates (venue_order_id NULL): {len(stuck)}")
        for agg in stuck:
            cmd = _latest_command_payload(ro, agg)
            fid = cmd.get("final_intent_id")
            usage = _cap_usage_id_for(ro, fid)
            print(f"  - {agg[:80]}...  final_intent={str(fid)[:60]}...  cap_usage={usage}")
    finally:
        ro.close()

    if not stuck:
        print("Nothing to resolve (idempotent no-op).")
        return 0
    if not apply:
        print("\nDRY-RUN: re-run with --apply to append Reconciled + CapTransitioned(RELEASED).")
        return 0

    now = datetime.now(timezone.utc)
    conn = get_world_connection(write_class="live")
    conn.row_factory = __import__("sqlite3").Row
    try:
        with world_write_lock(conn):
            ledger = LiveOrderAggregateLedger(conn)
            cap_ledger = LiveCapLedger(conn)
            for agg in stuck:
                cmd = _latest_command_payload(conn, agg)
                event_id = cmd["event_id"]
                final_intent_id = cmd["final_intent_id"]
                execution_command_id = cmd["execution_command_id"]
                receipt_hash = _latest_receipt_hash(conn, agg)

                # Step 1: Reconciled (venue_reconcile authority): order never placed,
                # no fill, recommend cap release. Clears pending_reconcile.
                append_reconciled(
                    ledger,
                    aggregate_id=agg,
                    event_id=event_id,
                    final_intent_id=final_intent_id,
                    source=RECONCILE_SOURCE,
                    pending_reconcile=False,
                    occurred_at=now,
                    payload={
                        "execution_command_id": execution_command_id,
                        "venue_order_exists": False,
                        "cap_transition_recommendation": "RELEASED",
                        "reconcile_reason": RESOLUTION_REASON,
                    },
                )

                # Step 2: CapTransitioned -> RELEASED (allowed now that Reconciled
                # exists; aggregate law line 440-442). Terminal aggregate state.
                ledger.append_event(
                    aggregate_id=agg,
                    event_type="CapTransitioned",
                    payload={
                        "event_id": event_id,
                        "final_intent_id": final_intent_id,
                        "execution_command_id": execution_command_id,
                        "execution_receipt_hash": receipt_hash,
                        "to_status": "RELEASED",
                        "projection_status": "RELEASED",
                        "transition_reason": RESOLUTION_REASON,
                    },
                    occurred_at=now,
                    source_authority="explicit_reconcile",
                )

                # Step 3: release the held LIVE_CAP reservation via the canonical
                # ledger call (RESERVED -> RELEASED + free the day slot).
                usage = _cap_usage_id_for(conn, final_intent_id)
                if usage is not None:
                    cap_ledger.release(usage, RESOLUTION_REASON)
                print(f"RESOLVED {agg[:80]}...  cap_usage={usage}")
    finally:
        conn.close()

    ro = get_world_connection_read_only()
    try:
        after_unresolved, after_reserved = _readiness_counts(ro)
    finally:
        ro.close()
    print(f"AFTER: unresolved_submit={after_unresolved} reserved_cap={after_reserved}")
    ok = after_unresolved == 0 and after_reserved == 0
    print("RESULT:", "PASS (readiness counts 0/0)" if ok else "STILL BLOCKED")
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="apply the resolution (default: dry-run)")
    args = ap.parse_args()
    return resolve(apply=args.apply)


if __name__ == "__main__":
    sys.exit(main())
