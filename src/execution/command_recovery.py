# Created: 2026-04-26
# Last reused/audited: 2026-04-26
# Authority basis: docs/operations/task_2026-04-26_execution_state_truth_p1_command_bus/implementation_plan.md §P1.S4
"""Command recovery loop u2014 INV-31.

At cycle start, scans venue_commands for rows in IN_FLIGHT_STATES (SUBMITTING,
UNKNOWN, REVIEW_REQUIRED, CANCEL_PENDING) and reconciles each against venue
truth. Appends durable events that advance state per the u00a7P1.S4 resolution
table. P2/K4 will add chain-truth reconciliation for FILL_CONFIRMED.

Chain reconciliation (FILL_CONFIRMED via on-chain settlement evidence) is OUT
of scope for P1.S4 u2014 that requires deep chain-state integration. Deferred to
P2/K4 where chain authority is surfaced as a first-class seam.

Cross-DB note (per INV-30 caveat): venue_commands lives in zeus_trades.db.
When conn is not passed, this module opens its own trade connection via
get_trade_connection_with_world() and closes it in a try/finally. P1.S5
will add conn-threading from cycle_runner; for now self-contained.
"""
from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone
from typing import Optional

from src.execution.command_bus import (
    CommandState,
    CommandEventType,
    IN_FLIGHT_STATES,
    VenueCommand,
)
from src.state.venue_command_repo import (
    find_unresolved_commands,
    append_event,
)

logger = logging.getLogger(__name__)

# Venue status strings that indicate an order is no longer active
# (cancelled / expired at the venue).
_INACTIVE_STATUSES = frozenset({
    "CANCELLED", "CANCELED", "EXPIRED", "REJECTED", "FILLED",
})
# Statuses that mean the cancel was acknowledged (order is gone from the book).
_CANCEL_TERMINAL_STATUSES = frozenset({
    "CANCELLED", "CANCELED", "EXPIRED", "REJECTED",
})


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _reconcile_row(
    conn: sqlite3.Connection,
    cmd: VenueCommand,
    client,
) -> str:
    """Apply one resolution-table row.  Returns 'advanced', 'stayed', or 'error'.

    Raises nothing u2014 all exceptions are caught and logged; the loop counts them.
    """
    try:
        state = cmd.state

        # REVIEW_REQUIRED is operator-handoff: skip cleanly.
        if state == CommandState.REVIEW_REQUIRED:
            return "stayed"

        now = _now_iso()

        # ------------------------------------------------------------------ #
        # SUBMITTING without venue_order_id: the submit was never acked and   #
        # we have no venue_order_id to look up. We cannot distinguish         #
        # "never placed" from "placed but ack lost". Grammar does not allow   #
        # EXPIRED from SUBMITTING (_TRANSITIONS has no such edge); use        #
        # REVIEW_REQUIRED (legal from SUBMITTING) so operator can resolve.    #
        # ------------------------------------------------------------------ #
        if state == CommandState.SUBMITTING and not cmd.venue_order_id:
            append_event(
                conn,
                command_id=cmd.command_id,
                event_type=CommandEventType.REVIEW_REQUIRED.value,
                occurred_at=now,
                payload={"reason": "recovery_no_venue_order_id"},
            )
            logger.warning(
                "recovery: command %s SUBMITTING without venue_order_id -> REVIEW_REQUIRED",
                cmd.command_id,
            )
            return "advanced"

        # ------------------------------------------------------------------ #
        # States that require a venue lookup (need venue_order_id).           #
        # SUBMITTING+id, UNKNOWN, CANCEL_PENDING all fall here.               #
        # ------------------------------------------------------------------ #
        venue_order_id = cmd.venue_order_id

        if not venue_order_id:
            # UNKNOWN without a venue_order_id: operator must intervene.
            if state == CommandState.UNKNOWN:
                append_event(
                    conn,
                    command_id=cmd.command_id,
                    event_type=CommandEventType.REVIEW_REQUIRED.value,
                    occurred_at=now,
                    payload={"reason": "recovery_unknown_no_venue_order_id"},
                )
                logger.warning(
                    "recovery: command %s UNKNOWN without venue_order_id u2192 REVIEW_REQUIRED",
                    cmd.command_id,
                )
                return "advanced"
            # CANCEL_PENDING without a venue_order_id: can't verify at venue.
            if state == CommandState.CANCEL_PENDING:
                logger.warning(
                    "recovery: command %s CANCEL_PENDING without venue_order_id; skipping",
                    cmd.command_id,
                )
                return "stayed"
            return "stayed"

        # Venue lookup u2014 exceptions propagate to caller's per-row try/except.
        try:
            venue_resp = client.get_order(venue_order_id)
        except Exception as exc:
            # Network / auth error: leave in current state, retry next cycle.
            logger.warning(
                "recovery: venue lookup for command %s (venue_order_id=%s) raised: %s; "
                "leaving in %s",
                cmd.command_id, venue_order_id, exc, state.value,
            )
            return "error"

        # ------------------------------------------------------------------ #
        # SUBMITTING + venue_order_id                                         #
        # ------------------------------------------------------------------ #
        if state == CommandState.SUBMITTING:
            if venue_resp is not None:
                # Venue found the order u2014 ack it.
                append_event(
                    conn,
                    command_id=cmd.command_id,
                    event_type=CommandEventType.SUBMIT_ACKED.value,
                    occurred_at=now,
                    payload={"venue_order_id": venue_order_id, "venue_response": venue_resp},
                )
                logger.info(
                    "recovery: command %s SUBMITTING u2192 ACKED (venue found order %s)",
                    cmd.command_id, venue_order_id,
                )
                return "advanced"
            else:
                # Venue returned None (order not found).
                append_event(
                    conn,
                    command_id=cmd.command_id,
                    event_type=CommandEventType.EXPIRED.value,
                    occurred_at=now,
                    payload={"reason": "recovery_order_not_found_at_venue", "venue_order_id": venue_order_id},
                )
                logger.info(
                    "recovery: command %s SUBMITTING u2192 EXPIRED (order not found at venue)",
                    cmd.command_id,
                )
                return "advanced"

        # ------------------------------------------------------------------ #
        # UNKNOWN                                                             #
        # ------------------------------------------------------------------ #
        if state == CommandState.UNKNOWN:
            if venue_resp is not None:
                append_event(
                    conn,
                    command_id=cmd.command_id,
                    event_type=CommandEventType.SUBMIT_ACKED.value,
                    occurred_at=now,
                    payload={"venue_order_id": venue_order_id, "venue_response": venue_resp},
                )
                logger.info(
                    "recovery: command %s UNKNOWN u2192 ACKED (venue found order %s)",
                    cmd.command_id, venue_order_id,
                )
                return "advanced"
            else:
                # Cannot decide: never placed vs immediately cancelled.
                append_event(
                    conn,
                    command_id=cmd.command_id,
                    event_type=CommandEventType.REVIEW_REQUIRED.value,
                    occurred_at=now,
                    payload={"reason": "recovery_unknown_order_not_found_at_venue", "venue_order_id": venue_order_id},
                )
                logger.warning(
                    "recovery: command %s UNKNOWN u2192 REVIEW_REQUIRED (order not found; ambiguous)",
                    cmd.command_id,
                )
                return "advanced"

        # ------------------------------------------------------------------ #
        # CANCEL_PENDING                                                      #
        # ------------------------------------------------------------------ #
        if state == CommandState.CANCEL_PENDING:
            if venue_resp is None:
                # Order missing at venue u2014 cancel was processed.
                append_event(
                    conn,
                    command_id=cmd.command_id,
                    event_type=CommandEventType.CANCEL_ACKED.value,
                    occurred_at=now,
                    payload={"reason": "recovery_order_missing_at_venue", "venue_order_id": venue_order_id},
                )
                logger.info(
                    "recovery: command %s CANCEL_PENDING u2192 CANCELLED (order missing at venue)",
                    cmd.command_id,
                )
                return "advanced"
            venue_status = str(venue_resp.get("status") or "").upper()
            if venue_status in _CANCEL_TERMINAL_STATUSES:
                append_event(
                    conn,
                    command_id=cmd.command_id,
                    event_type=CommandEventType.CANCEL_ACKED.value,
                    occurred_at=now,
                    payload={"venue_order_id": venue_order_id, "venue_status": venue_status},
                )
                logger.info(
                    "recovery: command %s CANCEL_PENDING u2192 CANCELLED (venue status=%s)",
                    cmd.command_id, venue_status,
                )
                return "advanced"
            else:
                # Order still active; cancel ack pending.
                logger.info(
                    "recovery: command %s CANCEL_PENDING; venue status=%s; leaving (cancel ack pending)",
                    cmd.command_id, venue_status,
                )
                return "stayed"

        # Should not reach here for valid IN_FLIGHT_STATES.
        logger.warning("recovery: command %s state=%s not handled; skipping", cmd.command_id, state.value)
        return "stayed"

    except ValueError as exc:
        # Illegal grammar transition from append_event u2014 log and skip.
        logger.error(
            "recovery: command %s invalid transition: %s; skipping row",
            cmd.command_id, exc,
        )
        return "error"
    except Exception as exc:
        logger.error(
            "recovery: command %s raised %s: %s; skipping row",
            cmd.command_id, type(exc).__name__, exc,
        )
        return "error"


def reconcile_unresolved_commands(
    conn: Optional[sqlite3.Connection] = None,
    client=None,
) -> dict:
    """Scan unresolved venue_commands and apply reconciliation events.

    Returns a summary dict {"scanned": N, "advanced": M, "stayed": K, "errors": L}
    so cycle_runner can record it in the cycle summary.

    Each row in IN_FLIGHT_STATES is looked up at the venue (if it has a
    venue_order_id) and an event is appended per u00a7P1.S4. Rows in
    REVIEW_REQUIRED are skipped (operator-handoff). Rows without a
    venue_order_id and in SUBMITTING get an EXPIRED event since recovery
    cannot reconcile what was never acked.

    DB connection: if conn is None, opens get_trade_connection_with_world()
    internally (with a try/finally to close). P1.S5 will thread the trade
    conn from cycle_runner so this path becomes the fallback only.

    PolymarketClient: if client is None, lazily constructed here.
    """
    own_conn = False
    if conn is None:
        from src.state.db import get_trade_connection_with_world
        conn = get_trade_connection_with_world()
        own_conn = True

    if client is None:
        from src.data.polymarket_client import PolymarketClient
        client = PolymarketClient()

    summary = {"scanned": 0, "advanced": 0, "stayed": 0, "errors": 0}

    try:
        rows = find_unresolved_commands(conn)
        summary["scanned"] = len(rows)

        for row in rows:
            try:
                cmd = VenueCommand.from_row(row)
            except Exception as exc:
                logger.error(
                    "recovery: malformed row (command_id=%r): %s; skipping",
                    row.get("command_id"), exc,
                )
                summary["errors"] += 1
                continue

            outcome = _reconcile_row(conn, cmd, client)
            if outcome == "advanced":
                summary["advanced"] += 1
            elif outcome == "stayed":
                summary["stayed"] += 1
            else:
                summary["errors"] += 1

    finally:
        if own_conn:
            conn.close()

    logger.info(
        "recovery: scanned=%d advanced=%d stayed=%d errors=%d",
        summary["scanned"], summary["advanced"], summary["stayed"], summary["errors"],
    )
    return summary
