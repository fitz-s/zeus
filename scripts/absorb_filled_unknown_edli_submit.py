#!/usr/bin/env python
# Created: 2026-06-10
# Last reused or audited: 2026-06-10
# Authority basis: EDLI live-order aggregate event-sourcing law
#   (src/events/live_order_aggregate.py grammar: VenueSubmitAcknowledged is legal
#   after VenueSubmitAttempted; CapTransitioned CONSUMED requires the ack;
#   Reconciled is legal while projection.pending_reconcile=1), live-cap ledger
#   (src/events/live_cap.py consume), venue_commands grammar (INV-28:
#   SUBMITTING + SUBMIT_ACKED -> ACKED), INV-37 (no cross-DB tx needed: the two
#   writes are independent truths), consolidated overhaul #28c.
#
# Purpose
# -------
# Absorb a FILLED-but-ack-lost EDLI submit. Incident 2026-06-10 22:54Z
# (command 84fb2c4c / aggregate edli_evt_55c1b403...): the venue accepted and
# FILLED the order (on-chain 22:55:13Z) but persisting the ack hit "database is
# locked" (the K3.8 WAL-contention class), so the aggregate parked in
# PENDING_RECONCILE with its live-cap RESERVED and the boot readiness gate
# crash-looped the daemon (EDLI_STAGE_UNRESOLVED_SUBMIT_UNKNOWN +
# EDLI_STAGE_LIVE_CAP_RESERVED).
#
# The two existing resolution scripts cover ABSENCE truths only
# (never-reached-venue / authenticated-absence). This script covers the
# PRESENCE truth: an authenticated CLOB trade read proves the fill, so we
# append the LATE ACK + consume the cap (the money was spent — releasing it
# would license a double entry) + Reconciled with full fill provenance.
# Forward-only canonical appends; no raw UPDATE/DELETE of event rows;
# idempotent.
#
# Usage:
#   .venv/bin/python scripts/absorb_filled_unknown_edli_submit.py            # dry-run
#   .venv/bin/python scripts/absorb_filled_unknown_edli_submit.py --apply

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone

from src.data.polymarket_client import PolymarketClient
from src.events.live_cap import LiveCapLedger
from src.events.live_order_aggregate import LiveOrderAggregateLedger
from src.events.live_order_reconcile import RECONCILE_SOURCE, append_reconciled
from src.state.db import (
    get_trade_connection,
    get_world_connection,
    get_world_connection_read_only,
    world_write_lock,
)

UTC = timezone.utc
RESOLUTION_REASON = "LATE_ACK_FILL_ABSORBED_FROM_AUTHENTICATED_REST"


def _payload(conn, aggregate_id: str, event_type: str) -> dict:
    row = conn.execute(
        """SELECT payload_json FROM edli_live_order_events
        WHERE aggregate_id=? AND event_type=? ORDER BY event_sequence DESC LIMIT 1""",
        (aggregate_id, event_type),
    ).fetchone()
    return json.loads(row[0]) if row else {}


def _has_event(conn, aggregate_id: str, event_type: str) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM edli_live_order_events WHERE aggregate_id=? AND event_type=? LIMIT 1",
            (aggregate_id, event_type),
        ).fetchone()
        is not None
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--token-id", required=True, help="family token of the stuck submit")
    parser.add_argument("--command-id", required=True, help="venue_commands command_id stuck SUBMITTING")
    parser.add_argument("--expected-size", required=True)
    parser.add_argument("--expected-price", required=True)
    args = parser.parse_args()

    ro = get_world_connection_read_only()
    rows = ro.execute(
        "SELECT aggregate_id FROM edli_live_order_projection WHERE pending_reconcile=1"
    ).fetchall()
    if len(rows) != 1:
        print(f"expected exactly 1 pending_reconcile aggregate, found {len(rows)} — refusing")
        return 1
    aggregate_id = str(rows[0][0])
    unknown = _payload(ro, aggregate_id, "SubmitUnknown")
    command = _payload(ro, aggregate_id, "ExecutionCommandCreated")
    cap_reserved = _payload(ro, aggregate_id, "LiveCapReserved")
    ro.close()
    if not bool(unknown.get("venue_call_started")):
        print("SubmitUnknown.venue_call_started is not true — this is the ABSENCE class; "
              "use resolve_unresolved_edli_submit.py / resolve_edli_unknown_by_authenticated_absence.py")
        return 1
    event_id = str(command["event_id"])
    final_intent_id = str(command["final_intent_id"])
    execution_command_id = str(command["execution_command_id"])
    usage_id = str(cap_reserved["usage_id"])
    receipt_hash = str(unknown.get("execution_receipt_hash") or "")
    if not receipt_hash:
        print("SubmitUnknown carries no execution_receipt_hash — refusing")
        return 1
    print(f"aggregate: {aggregate_id}")
    print(f"usage_id : {usage_id}")

    # --- VENUE TRUTH (authenticated REST) ----------------------------------
    adapter = PolymarketClient()._ensure_v2_adapter()
    trades = adapter.get_trades()
    matches = []
    for t in trades:
        raw = getattr(t, "raw", None) or {}
        if (
            str(raw.get("asset_id")) == args.token_id
            and str(raw.get("side")) == "BUY"
            and str(raw.get("size")) == str(args.expected_size)
            and str(raw.get("price")) == str(args.expected_price)
        ):
            matches.append(raw)
    if len(matches) != 1:
        print(f"expected exactly 1 venue trade matching token/size/price, found {len(matches)} — refusing")
        for m in matches:
            print("  candidate:", {k: m.get(k) for k in ("id", "taker_order_id", "match_time", "status")})
        return 1
    trade = matches[0]
    venue_order_id = str(trade["taker_order_id"])
    raw_hash = hashlib.sha256(
        json.dumps(trade, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    match_time = datetime.fromtimestamp(int(trade["match_time"]), tz=UTC)
    print(
        f"venue truth: trade={trade['id']} order={venue_order_id} status={trade['status']} "
        f"size={trade['size']}@{trade['price']} match_time={match_time.isoformat()} "
        f"tx={trade.get('transaction_hash')}"
    )
    if str(trade.get("status")) not in {"CONFIRMED", "MINED", "MATCHED"}:
        print("venue trade status not a fill state — refusing")
        return 1

    if not args.apply:
        print("DRY-RUN: would append VenueSubmitAcknowledged (late ack) + CapTransitioned "
              "CONSUMED + Reconciled(pending_reconcile=False) on the aggregate; consume "
              f"live-cap {usage_id}; append SUBMIT_ACKED on venue_commands {args.command_id}.")
        return 0

    now = datetime.now(UTC)
    fill_provenance = {
        "venue_order_id": venue_order_id,
        "venue_trade_id": str(trade["id"]),
        "transaction_hash": str(trade.get("transaction_hash") or ""),
        "fill_size": str(trade["size"]),
        "fill_price": str(trade["price"]),
        "venue_trade_status": str(trade["status"]),
        "match_time": match_time.isoformat(),
        "raw_trade_hash": raw_hash,
        "resolution_reason": RESOLUTION_REASON,
        "transport": "authenticated_rest",
    }

    conn = get_world_connection()
    with world_write_lock(conn):
        try:
            ledger = LiveOrderAggregateLedger(conn)
            if not _has_event(conn, aggregate_id, "VenueSubmitAcknowledged"):
                ledger.append_event(
                    aggregate_id=aggregate_id,
                    event_type="VenueSubmitAcknowledged",
                    payload={
                        "event_id": event_id,
                        "final_intent_id": final_intent_id,
                        "execution_command_id": execution_command_id,
                        "execution_receipt_hash": receipt_hash,
                        "venue_order_id": venue_order_id,
                        "venue_ack_received": False,
                        "late_ack": True,
                        "raw_response_hash": raw_hash,
                        "ack_source": "authenticated_rest_trade_read",
                    },
                    occurred_at=now,
                    source_authority="explicit_reconcile",
                )
                print("appended VenueSubmitAcknowledged (late ack)")
            cap = LiveCapLedger(conn)
            status = conn.execute(
                "SELECT reservation_status FROM edli_live_cap_usage WHERE usage_id=?",
                (usage_id,),
            ).fetchone()[0]
            if status == "RESERVED":
                cap.consume(
                    usage_id,
                    final_intent_id=final_intent_id,
                    execution_command_id=execution_command_id,
                )
                ledger.append_event(
                    aggregate_id=aggregate_id,
                    event_type="CapTransitioned",
                    payload={
                        "event_id": event_id,
                        "final_intent_id": final_intent_id,
                        "execution_command_id": execution_command_id,
                        "execution_receipt_hash": receipt_hash,
                        "usage_id": usage_id,
                        "from_status": "PENDING_RECONCILE",
                        "to_status": "CONSUMED",
                        "transition_reason": RESOLUTION_REASON,
                    },
                    occurred_at=now,
                    source_authority="explicit_reconcile",
                )
                print("cap CONSUMED (the fill spent the reservation — never released)")
            append_reconciled(
                ledger,
                aggregate_id=aggregate_id,
                event_id=event_id,
                final_intent_id=final_intent_id,
                source=RECONCILE_SOURCE,
                pending_reconcile=False,
                occurred_at=now,
                payload=fill_provenance,
            )
            print("appended Reconciled(pending_reconcile=False) with fill provenance")
        except BaseException:
            raise
    conn.close()

    tconn = get_trade_connection(write_class="live")
    try:
        state = tconn.execute(
            "SELECT state FROM venue_commands WHERE command_id=?", (args.command_id,)
        ).fetchone()
        if state and state[0] == "SUBMITTING":
            from src.state.venue_command_repo import append_event as append_command_event

            append_command_event(
                tconn,
                command_id=args.command_id,
                event_type="SUBMIT_ACKED",
                occurred_at=now.isoformat(),
                payload={
                    "venue_order_id": venue_order_id,
                    "late_ack": True,
                    "ack_source": "authenticated_rest_trade_read",
                    **fill_provenance,
                },
            )
            tconn.commit()
            print(f"venue_commands {args.command_id}: SUBMITTING -> ACKED (venue_order_id bound); "
                  "the reconcile sweep completes the fill facts from venue truth")
        else:
            print(f"venue_commands {args.command_id} state={state[0] if state else None} — no trades-side action")
    finally:
        tconn.close()
    print("DONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
