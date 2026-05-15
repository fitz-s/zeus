# Created: 2026-04-26
# Last reused/audited: 2026-05-15
# Authority basis: docs/operations/task_2026-04-26_execution_state_truth_p1_command_bus/implementation_plan.md §P1.S4
#                  + docs/operations/task_2026-05-15_live_order_e2e_goal/LIVE_ORDER_E2E_GOAL_PLAN.md
"""Command recovery loop — INV-31.

At cycle start, scans venue_commands for rows in IN_FLIGHT_STATES and
reconciles currently-supported side-effect states against venue truth. M2 owns
SUBMIT_UNKNOWN_SIDE_EFFECT resolution: lookup by known venue_order_id or by
idempotency-key capability, then convert found orders to ACKED/PARTIAL or
operator REVIEW_REQUIRED, or mark safe replay permitted via a terminal
SUBMIT_REJECTED payload after the window elapses. MATCHED/MINED/FILLED are
optimistic venue observations and stay PARTIAL. CONFIRMED order status is not
fill-economics authority on this command-only recovery path; fill finality must
flow through explicit venue trade/fill fact paths. Appends durable events that
advance state per the §P1.S4 resolution table. P2/K4 will add chain-truth
reconciliation for FILL_CONFIRMED.

Chain reconciliation (FILL_CONFIRMED via on-chain settlement evidence) is OUT
of scope for P1.S4 — that requires deep chain-state integration. Deferred to
P2/K4 where chain authority is surfaced as a first-class seam.

Cross-DB note (per INV-30 caveat): venue_commands lives in zeus_trades.db.
When conn is not passed, this module opens its own trade connection via
get_trade_connection_with_world() and closes it in a try/finally. P1.S5
will add conn-threading from cycle_runner; for now self-contained.
"""
from __future__ import annotations

import logging
import json
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
_SAFE_REPLAY_MIN_AGE_SECONDS = 15 * 60


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_ts(value: str) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def _age_seconds(cmd: VenueCommand, *, now: datetime) -> float | None:
    started_at = _parse_ts(cmd.updated_at) or _parse_ts(cmd.created_at)
    if started_at is None:
        return None
    return (now - started_at).total_seconds()


def _extract_order_id(venue_resp: dict | None, fallback: str | None = None) -> str | None:
    if not isinstance(venue_resp, dict):
        return fallback
    return (
        venue_resp.get("orderID")
        or venue_resp.get("orderId")
        or venue_resp.get("order_id")
        or venue_resp.get("id")
        or fallback
    )


def _lookup_unknown_side_effect_order(cmd: VenueCommand, client) -> tuple[str, dict | None]:
    """Return ('found'|'not_found'|'unavailable', venue_response)."""

    if cmd.venue_order_id:
        return "found", client.get_order(cmd.venue_order_id)
    finder = getattr(client, "find_order_by_idempotency_key", None)
    if callable(finder):
        found = finder(cmd.idempotency_key.value)
        if found is None:
            return "found", None
        if isinstance(found, dict):
            return "found", found
        logger.warning(
            "recovery: command %s idempotency-key lookup returned non-dict %s; "
            "treating lookup as unavailable",
            cmd.command_id, type(found).__name__,
        )
        return "unavailable", None
    return "unavailable", None


_PRE_SDK_COLLATERAL_REASON_MARKERS = (
    "pusd_allowance_insufficient",
    "pusd_insufficient",
    "collateral_snapshot_degraded",
    "collateral_snapshot_stale",
    "collateral_snapshot_future",
    "collateral_ledger_unconfigured",
    "ctf_allowance_insufficient",
    "ctf_tokens_insufficient",
)

_PRE_SDK_REVIEW_REQUIRED_REASONS = frozenset({
    "pre_submit_collateral_reservation_failed",
    # Legacy live rows before the 2026-05-15 fix could be left SUBMITTING
    # after pre-SDK collateral failure, then moved here by recovery.
    "recovery_no_venue_order_id",
})


def _dict_row(row) -> dict:
    if row is None:
        return {}
    try:
        return dict(row)
    except Exception:
        return {}


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table,),
    ).fetchone()
    return row is not None


def _json_dict(raw: object) -> dict:
    if raw in (None, ""):
        return {}
    try:
        value = json.loads(str(raw))
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def _review_required_command(conn: sqlite3.Connection, command_id: str) -> dict:
    row = conn.execute(
        "SELECT * FROM venue_commands WHERE command_id = ?",
        (command_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"unknown command_id: {command_id}")
    command = _dict_row(row)
    if command.get("state") != CommandState.REVIEW_REQUIRED.value:
        raise ValueError(
            "review clearance is only legal for REVIEW_REQUIRED commands; "
            f"got {command.get('state')!r}"
        )
    return command


def _command_events(conn: sqlite3.Connection, command_id: str) -> list[dict]:
    return [
        _dict_row(row)
        for row in conn.execute(
            """
            SELECT *
            FROM venue_command_events
            WHERE command_id = ?
            ORDER BY sequence_no
            """,
            (command_id,),
        ).fetchall()
    ]


def _latest_review_required_payload(events: list[dict]) -> dict:
    for event in reversed(events):
        if event.get("event_type") != CommandEventType.REVIEW_REQUIRED.value:
            continue
        return _json_dict(event.get("payload_json"))
    return {}


def _command_envelope(conn: sqlite3.Connection, envelope_id: str | None) -> dict:
    if not envelope_id:
        return {}
    row = conn.execute(
        "SELECT * FROM venue_submission_envelopes WHERE envelope_id = ?",
        (envelope_id,),
    ).fetchone()
    return _dict_row(row)


def _count_facts(conn: sqlite3.Connection, table: str, command_id: str) -> int:
    if not _table_exists(conn, table):
        return 0
    row = conn.execute(
        f"SELECT COUNT(*) AS count FROM {table} WHERE command_id = ?",
        (command_id,),
    ).fetchone()
    if row is None:
        return 0
    data = _dict_row(row)
    return int(data.get("count", 0) or 0)


def _review_no_side_effect_predicates(
    conn: sqlite3.Connection,
    command: dict,
) -> tuple[dict, list[str]]:
    command_id = str(command["command_id"])
    events = _command_events(conn, command_id)
    review_required_payload = _latest_review_required_payload(events)
    review_required_reason = str(review_required_payload.get("reason") or "").strip()
    payloads = [_json_dict(event.get("payload_json")) for event in events]
    final_envelope_ids = [
        payload.get("final_submission_envelope_id")
        for payload in payloads
        if str(payload.get("final_submission_envelope_id") or "").strip()
    ]
    event_types = {str(event.get("event_type") or "") for event in events}
    unsafe_event_types = {
        "POST_ACKED",
        "SUBMIT_ACKED",
        "SUBMIT_UNKNOWN",
        "SUBMIT_TIMEOUT_UNKNOWN",
        "CLOSED_MARKET_UNKNOWN",
        "PARTIAL_FILL_OBSERVED",
        "FILL_CONFIRMED",
    }
    envelope = _command_envelope(conn, command.get("envelope_id"))
    order_fact_count = _count_facts(conn, "venue_order_facts", command_id)
    trade_fact_count = _count_facts(conn, "venue_trade_facts", command_id)
    predicates = {
        "no_venue_order_id": not str(command.get("venue_order_id") or "").strip(),
        "no_final_submission_envelope": not final_envelope_ids,
        "no_raw_response": not str(envelope.get("raw_response_json") or "").strip(),
        "no_signed_order": (
            envelope.get("signed_order_blob") in (None, b"", "")
            and not str(envelope.get("signed_order_hash") or "").strip()
        ),
        "no_order_facts": order_fact_count == 0,
        "no_trade_facts": trade_fact_count == 0,
        "no_submit_side_effect_events": not (event_types & unsafe_event_types),
        "review_required_reason_pre_sdk": (
            review_required_reason in _PRE_SDK_REVIEW_REQUIRED_REASONS
        ),
    }
    failures = [name for name, ok in predicates.items() if not ok]
    return predicates, failures


def _decision_log_pre_sdk_proof(conn: sqlite3.Connection, decision_id: str) -> dict | None:
    if not _table_exists(conn, "decision_log"):
        return None
    rows = conn.execute(
        """
        SELECT id, mode, started_at, completed_at, artifact_json
        FROM decision_log
        WHERE artifact_json LIKE ?
        ORDER BY id DESC
        LIMIT 20
        """,
        (f"%{decision_id}%",),
    ).fetchall()
    for row in rows:
        record = _dict_row(row)
        artifact = _json_dict(record.get("artifact_json"))
        for case in artifact.get("no_trade_cases") or []:
            if not isinstance(case, dict) or case.get("decision_id") != decision_id:
                continue
            reasons = case.get("rejection_reasons") or []
            if isinstance(reasons, str):
                reasons = [reasons]
            reason_text = " | ".join(str(reason) for reason in reasons)
            if case.get("rejection_stage") != "EXECUTION_FAILED":
                return None
            if "execution_intent_rejected:" not in reason_text:
                return None
            if not any(marker in reason_text for marker in _PRE_SDK_COLLATERAL_REASON_MARKERS):
                return None
            return {
                "decision_log_id": record.get("id"),
                "mode": record.get("mode"),
                "started_at": record.get("started_at"),
                "completed_at": record.get("completed_at"),
                "rejection_stage": case.get("rejection_stage"),
                "rejection_reasons": list(reasons),
                "city": case.get("city"),
                "target_date": case.get("target_date"),
                "range_label": case.get("range_label"),
            }
    return None


def clear_review_required_no_venue_side_effect(
    conn: sqlite3.Connection,
    command_id: str,
    *,
    source_commit: str,
    source_function: str,
    source_reason: str,
    reviewed_by: str = "operator",
    occurred_at: str | None = None,
) -> dict:
    """Terminalize a REVIEW_REQUIRED command only with positive no-side-effect proof.

    This is not a generic state editor. It requires DB predicates proving no
    venue identity/facts/final envelope exist and decision-log evidence that
    the command's decision failed at a pre-SDK collateral boundary.
    """

    command = _review_required_command(conn, command_id)
    predicates, predicate_failures = _review_no_side_effect_predicates(conn, command)
    if predicate_failures:
        raise ValueError(
            "review clearance predicates failed: " + ", ".join(sorted(predicate_failures))
        )
    decision_id = str(command.get("decision_id") or "")
    decision_proof = _decision_log_pre_sdk_proof(conn, decision_id)
    if decision_proof is None:
        raise ValueError(
            "review clearance requires decision_log EXECUTION_FAILED "
            "execution_intent_rejected collateral proof"
        )
    if not str(source_commit or "").strip():
        raise ValueError("source_commit is required")
    if source_function not in {"_live_order", "execute_exit_order"}:
        raise ValueError("source_function must identify the executor boundary")
    if not str(source_reason or "").strip():
        raise ValueError("source_reason is required")
    now = occurred_at or _now_iso()
    payload = {
        "schema_version": 1,
        "reason": "review_cleared_no_venue_side_effect",
        "command_id": command_id,
        "decision_id": decision_id,
        "proof_class": "pre_sdk_no_side_effect",
        "side_effect_boundary_crossed": False,
        "sdk_submit_attempted": False,
        "required_predicates": predicates,
        "source_proof": {
            "source_commit": source_commit,
            "source_function": source_function,
            "source_reason": source_reason,
            "decision_id": decision_id,
            "deployed_source_boundary": (
                "collateral reservation occurs before PolymarketClient construction "
                "and before place_limit_order"
            ),
        },
        "review_required_proof": {
            "reason": _latest_review_required_payload(
                _command_events(conn, command_id)
            ).get("reason"),
            "allowed_reasons": sorted(_PRE_SDK_REVIEW_REQUIRED_REASONS),
        },
        "decision_log_proof": decision_proof,
        "reviewed_by": reviewed_by,
        "cleared_at": now,
    }
    append_event(
        conn,
        command_id=command_id,
        event_type=CommandEventType.REVIEW_CLEARED_NO_VENUE_SIDE_EFFECT.value,
        occurred_at=now,
        payload=payload,
    )
    return payload


def _reconcile_row(
    conn: sqlite3.Connection,
    cmd: VenueCommand,
    client,
) -> str:
    """Apply one resolution-table row.  Returns 'advanced', 'stayed', or 'error'.

    Raises nothing — all exceptions are caught and logged; the loop counts them.
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
        # M2: SUBMIT_UNKNOWN_SIDE_EFFECT                                      #
        # ------------------------------------------------------------------ #
        if state == CommandState.SUBMIT_UNKNOWN_SIDE_EFFECT:
            lookup_status, venue_resp = _lookup_unknown_side_effect_order(cmd, client)
            if lookup_status == "unavailable":
                logger.warning(
                    "recovery: command %s SUBMIT_UNKNOWN_SIDE_EFFECT cannot be resolved; "
                    "client lacks idempotency-key lookup and no venue_order_id is known",
                    cmd.command_id,
                )
                return "error"

            venue_order_id = _extract_order_id(venue_resp, cmd.venue_order_id)
            if venue_resp is not None:
                venue_status = str(venue_resp.get("status") or "").upper()
                payload = {
                    "venue_order_id": venue_order_id,
                    "venue_status": venue_status,
                    "venue_response": venue_resp,
                    "idempotency_key": cmd.idempotency_key.value,
                }
                if venue_status == "CONFIRMED":
                    append_event(
                        conn,
                        command_id=cmd.command_id,
                        event_type=CommandEventType.REVIEW_REQUIRED.value,
                        occurred_at=now,
                        payload={
                            **payload,
                            "reason": "recovery_confirmed_requires_trade_fact",
                            "semantic_guard": (
                                "order_status_confirmed_is_not_fill_economics_authority"
                            ),
                        },
                    )
                    logger.warning(
                        "recovery: command %s SUBMIT_UNKNOWN_SIDE_EFFECT -> REVIEW_REQUIRED "
                        "(venue status=%s order %s; explicit trade fact required)",
                        cmd.command_id, venue_status, venue_order_id,
                    )
                    return "advanced"
                if venue_status in {"FILLED", "MATCHED", "MINED", "PARTIAL", "PARTIALLY_MATCHED", "PARTIALLY_FILLED"}:
                    append_event(
                        conn,
                        command_id=cmd.command_id,
                        event_type=CommandEventType.PARTIAL_FILL_OBSERVED.value,
                        occurred_at=now,
                        payload=payload,
                    )
                    logger.info(
                        "recovery: command %s SUBMIT_UNKNOWN_SIDE_EFFECT -> PARTIAL "
                        "(venue status=%s order %s)",
                        cmd.command_id, venue_status, venue_order_id,
                    )
                    return "advanced"
                if venue_status == "REJECTED":
                    append_event(
                        conn,
                        command_id=cmd.command_id,
                        event_type=CommandEventType.SUBMIT_REJECTED.value,
                        occurred_at=now,
                        payload={**payload, "reason": "recovery_venue_rejected"},
                    )
                    logger.info(
                        "recovery: command %s SUBMIT_UNKNOWN_SIDE_EFFECT -> SUBMIT_REJECTED "
                        "(venue status=%s)",
                        cmd.command_id, venue_status,
                    )
                    return "advanced"
                append_event(
                    conn,
                    command_id=cmd.command_id,
                    event_type=CommandEventType.SUBMIT_ACKED.value,
                    occurred_at=now,
                    payload=payload,
                )
                logger.info(
                    "recovery: command %s SUBMIT_UNKNOWN_SIDE_EFFECT -> ACKED "
                    "(venue status=%s order %s)",
                    cmd.command_id, venue_status, venue_order_id,
                )
                return "advanced"

            age = _age_seconds(cmd, now=datetime.now(timezone.utc))
            if age is None or age < _SAFE_REPLAY_MIN_AGE_SECONDS:
                logger.info(
                    "recovery: command %s SUBMIT_UNKNOWN_SIDE_EFFECT not found but age=%s; "
                    "staying until safe replay window elapses",
                    cmd.command_id, age,
                )
                return "stayed"
            append_event(
                conn,
                command_id=cmd.command_id,
                event_type=CommandEventType.SUBMIT_REJECTED.value,
                occurred_at=now,
                payload={
                    "reason": "safe_replay_permitted_no_order_found",
                    "safe_replay_permitted": True,
                    "previous_unknown_command_id": cmd.command_id,
                    "idempotency_key": cmd.idempotency_key.value,
                    "age_seconds": age,
                },
            )
            logger.warning(
                "recovery: command %s SUBMIT_UNKNOWN_SIDE_EFFECT -> SUBMIT_REJECTED "
                "(safe replay permitted; idempotency key not found after %.1fs)",
                cmd.command_id, age,
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
                    "recovery: command %s UNKNOWN without venue_order_id -> REVIEW_REQUIRED",
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

        # Venue lookup — exceptions propagate to caller's per-row try/except.
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
                # Inspect venue status — pre-fix code unconditionally emitted
                # SUBMIT_ACKED even when status="REJECTED" (HIGH-2).
                venue_status = str(venue_resp.get("status") or "").upper()
                if venue_status == "REJECTED":
                    append_event(
                        conn,
                        command_id=cmd.command_id,
                        event_type=CommandEventType.SUBMIT_REJECTED.value,
                        occurred_at=now,
                        payload={"reason": "recovery_venue_rejected", "venue_order_id": venue_order_id, "venue_status": venue_status},
                    )
                    logger.info(
                        "recovery: command %s SUBMITTING -> REJECTED (venue status=%s)",
                        cmd.command_id, venue_status,
                    )
                    return "advanced"
                if venue_status in {"CANCELLED", "CANCELED", "EXPIRED"}:
                    # Terminal-but-no-fill ambiguity — operator review.
                    append_event(
                        conn,
                        command_id=cmd.command_id,
                        event_type=CommandEventType.REVIEW_REQUIRED.value,
                        occurred_at=now,
                        payload={"reason": "recovery_venue_terminal_no_fill", "venue_order_id": venue_order_id, "venue_status": venue_status},
                    )
                    logger.warning(
                        "recovery: command %s SUBMITTING -> REVIEW_REQUIRED (venue terminal status=%s)",
                        cmd.command_id, venue_status,
                    )
                    return "advanced"
                # Live / matched / active — ack it.
                append_event(
                    conn,
                    command_id=cmd.command_id,
                    event_type=CommandEventType.SUBMIT_ACKED.value,
                    occurred_at=now,
                    payload={"venue_order_id": venue_order_id, "venue_status": venue_status, "venue_response": venue_resp},
                )
                logger.info(
                    "recovery: command %s SUBMITTING -> ACKED (venue status=%s order %s)",
                    cmd.command_id, venue_status, venue_order_id,
                )
                return "advanced"
            else:
                # Venue returned None (order not found). Pre-fix emitted
                # EXPIRED which is grammar-illegal from SUBMITTING (HIGH-1
                # symmetric fix). Use REVIEW_REQUIRED for consistency with
                # the no-venue_order_id branch — operator distinguishes
                # "never placed" from "ack lost".
                append_event(
                    conn,
                    command_id=cmd.command_id,
                    event_type=CommandEventType.REVIEW_REQUIRED.value,
                    occurred_at=now,
                    payload={"reason": "recovery_order_not_found_at_venue", "venue_order_id": venue_order_id},
                )
                logger.warning(
                    "recovery: command %s SUBMITTING -> REVIEW_REQUIRED (order not found at venue)",
                    cmd.command_id,
                )
                return "advanced"

        # ------------------------------------------------------------------ #
        # UNKNOWN                                                             #
        # ------------------------------------------------------------------ #
        if state == CommandState.UNKNOWN:
            if venue_resp is not None:
                # Same status-aware branching as SUBMITTING (HIGH-2 symmetric).
                venue_status = str(venue_resp.get("status") or "").upper()
                if venue_status == "REJECTED":
                    append_event(
                        conn,
                        command_id=cmd.command_id,
                        event_type=CommandEventType.SUBMIT_REJECTED.value,
                        occurred_at=now,
                        payload={"reason": "recovery_venue_rejected", "venue_order_id": venue_order_id, "venue_status": venue_status},
                    )
                    logger.info(
                        "recovery: command %s UNKNOWN -> REJECTED (venue status=%s)",
                        cmd.command_id, venue_status,
                    )
                    return "advanced"
                if venue_status in {"CANCELLED", "CANCELED", "EXPIRED"}:
                    append_event(
                        conn,
                        command_id=cmd.command_id,
                        event_type=CommandEventType.REVIEW_REQUIRED.value,
                        occurred_at=now,
                        payload={"reason": "recovery_venue_terminal_no_fill", "venue_order_id": venue_order_id, "venue_status": venue_status},
                    )
                    logger.warning(
                        "recovery: command %s UNKNOWN -> REVIEW_REQUIRED (venue terminal status=%s)",
                        cmd.command_id, venue_status,
                    )
                    return "advanced"
                append_event(
                    conn,
                    command_id=cmd.command_id,
                    event_type=CommandEventType.SUBMIT_ACKED.value,
                    occurred_at=now,
                    payload={"venue_order_id": venue_order_id, "venue_status": venue_status, "venue_response": venue_resp},
                )
                logger.info(
                    "recovery: command %s UNKNOWN -> ACKED (venue status=%s order %s)",
                    cmd.command_id, venue_status, venue_order_id,
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
    venue_order_id) and an event is appended per §P1.S4. Rows in
    REVIEW_REQUIRED are skipped (operator-handoff). Rows without a
    venue_order_id and in SUBMITTING get a REVIEW_REQUIRED event since recovery
    cannot distinguish never-placed from ack-lost side effects.

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
