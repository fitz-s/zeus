# Created: 2026-06-22
# Last audited: 2026-06-22
# Authority basis: docs/evidence/live_order_pathology/2026-06-22_wellington_duplicate_blocker.md
#
# ONE-SHOT operator reconcile for the Wellington buy-YES zombie aggregate
# (edli_evt_ad064baf...) that is duplicate-blocking the Wellington
# buy-NO 17C / 2026-06-24 trade.
#
# Venue truth: NO real order exists at Polymarket (no venue_command row,
# no venue_order_id).  This clears local state only.
#
# Run from /Users/leofitz/zeus (the main repo, NOT a worktree):
#   python3 scripts/reconcile_wellington_zombie_2026_06_22.py [--dry-run]
#
# Requires: venv active (same one the live daemon uses).
#
# What it does:
#   1. Appends SubmitRejected (pre-submit form) to the zombie aggregate so
#      _TERMINAL_EVENT_SQL fires and the duplicate lock releases.
#   2. Calls LiveCapLedger.release() to free the $6.73 phantom cap reservation.
#
# Safety checks:
#   - Confirms the aggregate is still non-terminal before writing.
#   - Confirms venue_order_id IS NULL (no real venue order).
#   - Confirms edli_live_cap_usage is still RESERVED (not already released).
#   - Dry-run by default; pass --commit to write.

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))

from src.events.live_cap import LiveCapLedger
from src.events.live_order_aggregate import LiveOrderAggregateLedger

WORLD_DB = REPO_ROOT / "state" / "zeus-world.db"

AGGREGATE_ID = (
    "edli_evt_ad064bafc031a30d8a8dac21573f9b3a6808b9c1e4713c6985c02807d9f44449"
    ":edli_intent:edli_evt_ad064bafc031a30d8a8dac21573f9b3a6808b9c1e4713c6985c02807d9f44449"
    ":66056803750256461525842897441761005522616620854119790748180393208853879803996"
)

SUBMIT_REJECTED_PAYLOAD = {
    # command binding — must match ExecutionCommandCreated payload exactly
    "event_id": "edli_evt_ad064bafc031a30d8a8dac21573f9b3a6808b9c1e4713c6985c02807d9f44449",
    "final_intent_id": (
        "edli_intent:edli_evt_ad064bafc031a30d8a8dac21573f9b3a6808b9c1e4713c6985c02807d9f44449"
        ":66056803750256461525842897441761005522616620854119790748180393208853879803996"
    ),
    "execution_command_id": (
        "edli_exec_cmd:edli_evt_ad064bafc031a30d8a8dac21573f9b3a6808b9c1e4713c6985c02807d9f44449"
        ":edli_intent:edli_evt_ad064bafc031a30d8a8dac21573f9b3a6808b9c1e4713c6985c02807d9f44449"
        ":66056803750256461525842897441761005522616620854119790748180393208853879803996"
        ":66056803750256461525842897441761005522616620854119790748180393208853879803996"
        ":buy_yes"
    ),
    # pre-submit rejection markers (_is_pre_submit_rejection_payload)
    "pre_submit_rejection": True,
    "submit_status": "PRE_SUBMIT_ERROR",
    "venue_call_started": False,
    # reason required by SubmitRejected validation
    "reason_code": "MANUAL_RECONCILE_ZOMBIE_PRE_SUBMIT",
}

CAP_USAGE_ID = "edli_live_cap:59b1c9975d7c894ad7a9cc123f321ab5"


def _die(msg: str) -> None:
    print(f"ABORT: {msg}", file=sys.stderr)
    sys.exit(1)


def run(*, commit: bool) -> None:
    mode = "COMMIT" if commit else "DRY-RUN"
    print(f"--- Wellington zombie reconcile [{mode}] ---")

    conn = sqlite3.connect(str(WORLD_DB))
    conn.row_factory = sqlite3.Row

    # Safety check 1: aggregate must still be non-terminal
    terminal_sql = """
        SELECT 1 FROM edli_live_order_events
        WHERE aggregate_id = ?
          AND (
            event_type = 'SubmitRejected'
            OR event_type = 'UserTradeObserved'
            OR (event_type = 'CapTransitioned'
                AND json_extract(payload_json, '$.to_status') = 'RELEASED')
            OR (event_type = 'Reconciled'
                AND COALESCE(json_extract(payload_json, '$.pending_reconcile'), 0) = 0)
          )
        LIMIT 1
    """
    already_terminal = conn.execute(terminal_sql, (AGGREGATE_ID,)).fetchone()
    if already_terminal:
        print("INFO: Aggregate already has a terminal event — nothing to do.")
        conn.close()
        return

    # Safety check 2: venue_order_id must still be NULL
    proj = conn.execute(
        "SELECT venue_order_id, current_state FROM edli_live_order_projection WHERE aggregate_id = ?",
        (AGGREGATE_ID,),
    ).fetchone()
    if proj is None:
        _die("Aggregate not found in edli_live_order_projection — stale script?")
    if proj["venue_order_id"] is not None:
        _die(
            f"venue_order_id is NOT NULL ({proj['venue_order_id']}) — "
            "real venue order exists; do NOT reconcile blindly"
        )
    print(f"  current_state={proj['current_state']}, venue_order_id=NULL [OK]")

    # Safety check 3: cap usage must still be RESERVED
    cap_row = conn.execute(
        "SELECT reservation_status FROM edli_live_cap_usage WHERE usage_id = ?",
        (CAP_USAGE_ID,),
    ).fetchone()
    if cap_row is None:
        print("  WARN: cap_usage row not found — may already be cleaned up; continuing")
        cap_needs_release = False
    elif cap_row["reservation_status"] != "RESERVED":
        print(
            f"  WARN: cap_usage already in status={cap_row['reservation_status']} — "
            "skipping release step"
        )
        cap_needs_release = False
    else:
        print(f"  cap_usage reservation_status=RESERVED [will release]")
        cap_needs_release = True

    print()
    print("  Step A: appending SubmitRejected (pre-submit form)")
    print(f"  payload: {json.dumps(SUBMIT_REJECTED_PAYLOAD, indent=4)}")

    if commit:
        ledger = LiveOrderAggregateLedger(conn)
        ev = ledger.append_event(
            aggregate_id=AGGREGATE_ID,
            event_type="SubmitRejected",
            payload=SUBMIT_REJECTED_PAYLOAD,
            occurred_at=datetime.now(timezone.utc),
            source_authority="manual_operator_reconcile",
        )
        print(f"  SubmitRejected appended: event_hash={ev.event_hash}")
    else:
        print("  [dry-run] would append SubmitRejected")

    if cap_needs_release:
        print()
        print(f"  Step B: releasing cap usage {CAP_USAGE_ID}")
        if commit:
            LiveCapLedger(conn).release(CAP_USAGE_ID, reason="MANUAL_RECONCILE_ZOMBIE")
            print("  cap_usage released")
        else:
            print("  [dry-run] would release cap_usage")

    if commit:
        conn.commit()
        print()
        print("DONE — zombie aggregate terminated, cap released.")
        print("The Wellington buy-NO duplicate lock is now clear.")
        print("The live daemon will pick up Wellington on its next cycle.")
    else:
        conn.rollback()
        print()
        print("DRY-RUN complete — no changes written.")
        print("Re-run with --commit to apply.")

    conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Reconcile Wellington zombie aggregate (2026-06-22)"
    )
    parser.add_argument(
        "--commit",
        action="store_true",
        default=False,
        help="Actually write changes (default: dry-run only)",
    )
    args = parser.parse_args()
    run(commit=args.commit)


if __name__ == "__main__":
    main()
