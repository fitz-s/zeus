#!/usr/bin/env python3
# Lifecycle: created=2026-05-16; last_reviewed=2026-05-16; last_reused=never
# Purpose: Operator CLI to advance a REDEEM_OPERATOR_REQUIRED row to
#   REDEEM_TX_HASHED after a manual Polymarket UI claim. Record-only — no
#   web3 write, no signing, no gas. Closes the F14 cascade-liveness gap when
#   PolymarketV2Adapter.redeem returns the REDEEM_DEFERRED_TO_R1 stub.
# Reuse: Invoke ONLY after manual Polymarket UI claim has produced an on-chain
#   tx_hash. NORMAL mode requires source state REDEEM_OPERATOR_REQUIRED;
#   --force mode allows any non-CONFIRMED state except REDEEM_SUBMITTED (in-
#   flight adapter window — double-redeem hazard) with mandatory --notes
#   ≥10 chars. Authority basis: SCAFFOLD_F14_F16.md §K.4 v5.
#
# Operator CLI to record a Polymarket-UI-completed redeem against a
# REDEEM_OPERATOR_REQUIRED row. Record-only: no web3 write, no signing,
# no gas. Operator claims tokens via Polymarket UI, copies the tx_hash,
# runs this CLI to advance state REDEEM_OPERATOR_REQUIRED → REDEEM_TX_HASHED.
#
# Two modes:
#   NORMAL (no --force):  source state must be REDEEM_OPERATOR_REQUIRED.
#   FORCE (--force):      source state may be any non-CONFIRMED state.
#     Requires --notes with ≥10 chars; audit event records actor_override=true.
#
# Idempotency contract (§K.4 v5):
#   - NORMAL on TX_HASHED with same hash → exit 0 no-op (already_recorded).
#   - NORMAL on TX_HASHED with DIFFERENT hash → exit 6 reject.
#   - All --force operations append audit event with prior_state/prior_tx_hash.
#
# Race contract (§K.4 v5):
#   scheduler redeem_submitter only operates on _SUBMITTABLE_STATES
#   (INTENT_CREATED, RETRYING); CLI in NORMAL mode only operates on
#   OPERATOR_REQUIRED; CLI in FORCE mode is operator-deliberate.
#   No state overlap → no race. Atomic conditional UPDATE in
#   _atomic_transition is the primitive.

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from pathlib import Path
from typing import Iterable

logger = logging.getLogger(__name__)

# 0x-prefixed 64 lowercase hex chars (standard Ethereum tx hash).
_TX_HASH_RE = re.compile(r"^0x[0-9a-fA-F]{64}$")

# Per §K.4 v5: FORCE-allowed states = ALL except REDEEM_CONFIRMED.
# REDEEM_SUBMITTED EXCLUDED (in-flight adapter window per round-3 critic P2).
_FORCE_ALLOWED_STATES = frozenset({
    "REDEEM_INTENT_CREATED",
    "REDEEM_RETRYING",
    "REDEEM_TX_HASHED",
    "REDEEM_OPERATOR_REQUIRED",
    "REDEEM_REVIEW_REQUIRED",
    "REDEEM_FAILED",
})

EXIT_OK = 0
EXIT_REJECT_WRONG_STATE = 2
EXIT_REJECT_MALFORMED_HASH = 3
EXIT_REJECT_MULTIPLE_ROWS = 4
EXIT_REJECT_ZERO_ROWS = 5
EXIT_REJECT_CONFLICTING_HASH = 6
EXIT_REJECT_FORCE_NO_NOTES = 7


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def _validate_tx_hash(tx_hash: str) -> None:
    if not _TX_HASH_RE.match(tx_hash):
        raise SystemExit(EXIT_REJECT_MALFORMED_HASH)


def _select_active_row(conn, condition_id: str):
    """Return (command_id, state, tx_hash) for the one non-CONFIRMED row.

    Per UNIQUE INDEX ux_settlement_commands_active_condition_asset
    (settlement_commands.py:53-55), at most ONE active row per
    (condition_id, market_id, payout_asset). If multiple, exit 4.
    """
    rows = conn.execute(
        """
        SELECT command_id, state, tx_hash
          FROM settlement_commands
         WHERE condition_id = ?
           AND state != 'REDEEM_CONFIRMED'
        """,
        (condition_id,),
    ).fetchall()
    if len(rows) == 0:
        logger.error(
            "[OPERATOR_RECORD_REJECT] condition_id=%s no non-CONFIRMED row found",
            condition_id,
        )
        raise SystemExit(EXIT_REJECT_ZERO_ROWS)
    if len(rows) > 1:
        logger.error(
            "[OPERATOR_RECORD_REJECT] condition_id=%s has %d active rows; "
            "data integrity violation (UNIQUE INDEX should prevent this)",
            condition_id, len(rows),
        )
        raise SystemExit(EXIT_REJECT_MULTIPLE_ROWS)
    r = rows[0]
    return r[0], r[1], r[2]


def _do_record(
    conn,
    *,
    condition_id: str,
    tx_hash: str,
    force: bool,
    notes: str | None,
) -> dict:
    """Perform the atomic state transition. Returns outcome dict."""
    from src.execution.settlement_commands import (
        SettlementState,
        _atomic_transition,
    )

    _validate_tx_hash(tx_hash)
    command_id, current_state, current_tx_hash = _select_active_row(conn, condition_id)

    if force:
        if not notes or len(notes.strip()) < 10:
            logger.error(
                "[OPERATOR_RECORD_REJECT] --force requires --notes with ≥10 chars"
            )
            raise SystemExit(EXIT_REJECT_FORCE_NO_NOTES)
        if current_state not in _FORCE_ALLOWED_STATES:
            logger.error(
                "[OPERATOR_RECORD_REJECT] --force not allowed on state=%s "
                "(allowed: %s)",
                current_state, sorted(_FORCE_ALLOWED_STATES),
            )
            raise SystemExit(EXIT_REJECT_WRONG_STATE)
    else:
        # NORMAL mode: source must be OPERATOR_REQUIRED OR idempotent
        # TX_HASHED-with-matching-hash.
        if current_state == "REDEEM_TX_HASHED":
            if current_tx_hash == tx_hash:
                logger.info(
                    "[OPERATOR_RECORD] command_id=%s condition_id=%s "
                    "already_recorded_no_op tx_hash=%s",
                    command_id, condition_id, tx_hash,
                )
                return {
                    "command_id": command_id,
                    "condition_id": condition_id,
                    "result": "already_recorded_no_op",
                    "tx_hash": tx_hash,
                    "prior_state": current_state,
                }
            # different hash on TX_HASHED → reject, suggest --force
            logger.error(
                "[OPERATOR_RECORD_REJECT] command_id=%s condition_id=%s "
                "already TX_HASHED with prior_hash=%s; supplied_hash=%s. "
                "Use --force to override.",
                command_id, condition_id, current_tx_hash, tx_hash,
            )
            raise SystemExit(EXIT_REJECT_CONFLICTING_HASH)
        if current_state != "REDEEM_OPERATOR_REQUIRED":
            logger.error(
                "[OPERATOR_RECORD_REJECT] command_id=%s condition_id=%s "
                "state=%s; NORMAL mode requires REDEEM_OPERATOR_REQUIRED. "
                "Use --force with --notes to override.",
                command_id, condition_id, current_state,
            )
            raise SystemExit(EXIT_REJECT_WRONG_STATE)

    # Atomic transition
    transitioned = _atomic_transition(
        conn,
        command_id,
        from_state=current_state,
        to_state=SettlementState.REDEEM_TX_HASHED,
        tx_hash=tx_hash,
        submitted_at=_now_iso(),  # CLI invocation time per SCAFFOLD §K.4 v5
        payload={
            "actor": "operator",
            "actor_override": force,
            "prior_state": current_state,
            "prior_tx_hash": current_tx_hash,
            "notes": notes or "",
        },
        recorded_at=_now_iso(),
    )
    if not transitioned:
        # State raced (extremely unlikely given CLI serialization, but possible)
        logger.error(
            "[OPERATOR_RECORD_REJECT] command_id=%s atomic transition rowcount=0; "
            "state may have changed mid-CLI (re-query and retry)", command_id,
        )
        raise SystemExit(EXIT_REJECT_WRONG_STATE)
    conn.commit()

    level = logger.warning if force else logger.info
    level(
        "[OPERATOR_RECORD%s] command_id=%s condition_id=%s "
        "old=%s new=REDEEM_TX_HASHED tx_hash=%s",
        "_FORCE" if force else "", command_id, condition_id, current_state, tx_hash,
    )
    return {
        "command_id": command_id,
        "condition_id": condition_id,
        "result": "recorded_force" if force else "recorded",
        "tx_hash": tx_hash,
        "prior_state": current_state,
    }


def main(argv: Iterable[str] | None = None, *, conn=None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Record a Polymarket-UI-completed redeem against a "
            "REDEEM_OPERATOR_REQUIRED row in settlement_commands."
        ),
    )
    parser.add_argument("condition_id", help="Polymarket condition_id (0x... or short form).")
    parser.add_argument("tx_hash", help="On-chain tx hash from Polymarket UI claim (0x + 64 hex).")
    parser.add_argument(
        "--force", action="store_true",
        help="Allow override on any non-CONFIRMED state; requires --notes.",
    )
    parser.add_argument("--notes", default=None, help="Operator justification (≥10 chars with --force).")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    own_conn = conn is None
    if own_conn:
        from src.state.db import get_trade_connection
        conn = get_trade_connection(write_class="live")
    try:
        try:
            outcome = _do_record(
                conn,
                condition_id=args.condition_id,
                tx_hash=args.tx_hash,
                force=args.force,
                notes=args.notes,
            )
        except SystemExit as e:
            # propagate exit codes 2/3/4/5/6/7
            return int(e.code) if isinstance(e.code, int) else 1
        print(json.dumps(outcome, indent=2))
        return EXIT_OK
    finally:
        if own_conn:
            conn.close()


if __name__ == "__main__":
    sys.exit(main())
