"""EDLI live-order aggregate event log and projection."""

from __future__ import annotations

import math
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from src.decision_kernel.canonicalization import canonical_json, stable_hash
from src.events.day0_authority import (
    Day0AuthorityError,
    assert_live_day0_payload_authority,
)
from src.state.schema.edli_live_order_events_schema import LIVE_ORDER_EVENT_TYPES, ensure_tables


_LIVE_ENTRY_MIN_ENTRY_PRICE = 0.10
_DAY0_EVENT_TYPE = "DAY0_EXTREME_UPDATED"


PRE_SUBMIT_REQUIRED_FIELDS = (
    "event_id",
    "final_intent_id",
    "condition_id",
    "token_id",
    "side",
    "direction",
    "order_type",
    "time_in_force",
    "post_only",
    "checked_at",
    "quote_seen_at",
    "quote_age_ms",
    "book_hash",
    "current_best_bid",
    "current_best_ask",
    "limit_price",
    "size",
    "q_live",
    "q_lcb_5pct",
    "expected_edge",
    "min_entry_price",
    "min_expected_profit_usd",
    "min_submit_edge_density",
    "expected_edge_source_certificate_hash",
    "cost_basis_source_certificate_hash",
    "would_cross_book",
    "tick_size",
    "tick_aligned",
    "min_order_size",
    "size_ok",
    "neg_risk",
    "heartbeat_status",
    "user_ws_status",
    "venue_connectivity_status",
    "balance_allowance_status",
    "book_authority_id",
    "book_captured_at",
    "heartbeat_authority_id",
    "heartbeat_checked_at",
    "user_ws_authority_id",
    "user_ws_checked_at",
    "venue_connectivity_authority_id",
    "venue_connectivity_checked_at",
    "balance_allowance_authority_id",
    "balance_allowance_checked_at",
)

EVENT_STATE = {
    "DecisionProofAccepted": "DECISION_PROOF_ACCEPTED",
    "SubmitPlanBuilt": "SUBMIT_PLAN_BUILT",
    "PreSubmitRevalidated": "PRE_SUBMIT_REVALIDATED",
    "LiveCapReserved": "LIVE_CAP_RESERVED",
    "ExecutionCommandCreated": "EXECUTION_COMMAND_CREATED",
    "VenueSubmitAttempted": "VENUE_SUBMIT_ATTEMPTED",
    "VenueSubmitAcknowledged": "VENUE_SUBMIT_ACKED",
    "SubmitRejected": "SUBMIT_REJECTED",
    "SubmitUnknown": "PENDING_RECONCILE",
    "UserOrderObserved": "USER_ORDER_OBSERVED",
    "UserTradeObserved": "USER_TRADE_OBSERVED",
    "Reconciled": "RECONCILED",
    "CapTransitioned": "CAP_TRANSITIONED",
    "OrderLifecycleProjected": "ORDER_LIFECYCLE_PROJECTED",
}

PROFIT_AUDIT_TRIGGER_EVENTS = {
    "VenueSubmitAcknowledged",
    "SubmitRejected",
    "SubmitUnknown",
    "UserTradeObserved",
    "Reconciled",
    "CapTransitioned",
    "OrderLifecycleProjected",
}


class LiveOrderAggregateError(ValueError):
    """Raised when EDLI live-order aggregate append law is violated."""


@dataclass(frozen=True)
class LiveOrderAggregateEvent:
    aggregate_event_id: str
    aggregate_id: str
    event_sequence: int
    event_type: str
    parent_event_hash: str | None
    event_hash: str
    payload: dict[str, Any]
    payload_hash: str
    source_authority: str
    occurred_at: datetime


@dataclass(frozen=True)
class LiveOrderProjection:
    aggregate_id: str
    event_id: str
    final_intent_id: str | None
    current_state: str
    last_sequence: int
    last_event_type: str | None
    last_event_hash: str | None
    pending_reconcile: bool
    venue_order_id: str | None


class LiveOrderAggregateLedger:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        ensure_tables(conn)

    def append_event(
        self,
        *,
        aggregate_id: str,
        event_type: str,
        payload: dict[str, Any],
        occurred_at: datetime,
        source_authority: str,
        expected_parent_event_hash: str | None = None,
    ) -> LiveOrderAggregateEvent:
        if not aggregate_id:
            raise LiveOrderAggregateError("aggregate_id is required")
        if event_type not in LIVE_ORDER_EVENT_TYPES:
            raise LiveOrderAggregateError(f"unsupported live-order event_type: {event_type!r}")
        if not payload.get("event_id"):
            raise LiveOrderAggregateError("live-order event payload requires event_id")
        latest = self._latest_row(aggregate_id)
        if latest is not None and payload.get("event_id") != _payload(latest).get("event_id"):
            raise LiveOrderAggregateError("live-order aggregate event_id drift")
        self._validate_event_append(
            aggregate_id=aggregate_id,
            event_type=event_type,
            payload=payload,
            latest=latest,
            occurred_at=occurred_at,
        )
        parent_hash = latest["event_hash"] if latest is not None else None
        next_sequence = int(latest["event_sequence"]) + 1 if latest is not None else 1
        if expected_parent_event_hash is not None and expected_parent_event_hash != parent_hash:
            raise LiveOrderAggregateError("live-order aggregate parent hash mismatch")
        payload_json = canonical_json(payload)
        payload_hash = stable_hash(payload)
        event_hash = stable_hash(
            {
                "aggregate_id": aggregate_id,
                "event_sequence": next_sequence,
                "event_type": event_type,
                "parent_event_hash": parent_hash,
                "payload_hash": payload_hash,
                "source_authority": source_authority,
                "occurred_at": _dt(occurred_at),
            }
        )
        aggregate_event_id = "edli_live_order_event:" + event_hash[:32]
        existing = self.conn.execute(
            """
            SELECT *
            FROM edli_live_order_events
            WHERE aggregate_id = ? AND event_sequence = ?
            """,
            (aggregate_id, next_sequence),
        ).fetchone()
        if existing is not None:
            if existing["event_hash"] != event_hash:
                raise LiveOrderAggregateError("live-order aggregate sequence collision")
            return _event_from_row(existing)
        needs_user_dedup = event_type in {"UserOrderObserved", "UserTradeObserved"}
        if needs_user_dedup:
            self.conn.execute("SAVEPOINT edli_live_order_user_dedup_append")
        try:
            if needs_user_dedup:
                self._reserve_user_channel_message_hash(
                    aggregate_id=aggregate_id,
                    event_type=event_type,
                    payload=payload,
                    occurred_at=occurred_at,
                )
            self.conn.execute(
                """
                INSERT INTO edli_live_order_events (
                    aggregate_event_id, aggregate_id, event_sequence, event_type,
                    parent_event_hash, event_hash, payload_json, payload_hash,
                    source_authority, occurred_at, created_at, schema_version
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
                """,
                (
                    aggregate_event_id,
                    aggregate_id,
                    next_sequence,
                    event_type,
                    parent_hash,
                    event_hash,
                    payload_json,
                    payload_hash,
                    source_authority,
                    _dt(occurred_at),
                    _dt(datetime.now(timezone.utc)),
                ),
            )
        except Exception:
            if needs_user_dedup:
                self.conn.execute("ROLLBACK TO SAVEPOINT edli_live_order_user_dedup_append")
                self.conn.execute("RELEASE SAVEPOINT edli_live_order_user_dedup_append")
            raise
        if needs_user_dedup:
            self.conn.execute("RELEASE SAVEPOINT edli_live_order_user_dedup_append")
        self.rebuild_projection(aggregate_id)
        if event_type in PROFIT_AUDIT_TRIGGER_EVENTS:
            from src.events.live_profit_audit import record_edli_live_profit_audit_from_aggregate

            record_edli_live_profit_audit_from_aggregate(self.conn, aggregate_id)
        return self.get_event(aggregate_event_id)

    def rebuild_projection(self, aggregate_id: str) -> LiveOrderProjection:
        rows = self.conn.execute(
            """
            SELECT *
            FROM edli_live_order_events
            WHERE aggregate_id = ?
            ORDER BY event_sequence ASC
            """,
            (aggregate_id,),
        ).fetchall()
        if not rows:
            raise LiveOrderAggregateError("cannot rebuild projection for empty aggregate")
        event_id = str(_payload(rows[0])["event_id"])
        final_intent_id: str | None = None
        venue_order_id: str | None = None
        # H2_E2E (REAUDIT_0_1.md §2/§4): the live-order projection's posterior
        # trace is reconstructed from the DecisionProofAccepted event payload's
        # decision_audit block (event_reactor_adapter.py:2818-2819 writes the
        # receipt's posterior_id there), so the projection is SQL-reconstructable
        # to the driving posterior WITHOUT JSON_EXTRACT and WITHOUT a cross-table
        # join. Observability only and fail-soft: None on canonical orders /
        # absent block — never changes order state. Sticky once set so a later
        # reconcile event (no decision_audit) does not clear it.
        posterior_id: int | None = None
        probability_authority: str | None = None
        pending_reconcile = False
        current_state = "UNKNOWN"
        for row in rows:
            payload = _payload(row)
            if payload.get("event_id") != event_id:
                raise LiveOrderAggregateError("aggregate event_id drift")
            if payload.get("final_intent_id") is not None:
                final_intent_id = str(payload["final_intent_id"])
            if payload.get("venue_order_id") is not None:
                venue_order_id = str(payload["venue_order_id"])
            _audit = payload.get("decision_audit")
            if isinstance(_audit, dict):
                _pid = _optional_posterior_id(_audit.get("posterior_id"))
                if _pid is not None:
                    posterior_id = _pid
                _auth = _audit.get("probability_authority")
                if _auth is not None:
                    probability_authority = str(_auth)
            event_type = str(row["event_type"])
            if event_type == "SubmitUnknown":
                current_state = EVENT_STATE[event_type]
                pending_reconcile = True
            elif event_type == "CapTransitioned":
                to_status = str(payload.get("to_status") or "")
                if to_status == "PENDING_RECONCILE":
                    current_state = "PENDING_RECONCILE"
                    pending_reconcile = True
                elif to_status == "CONSUMED":
                    # CONSUMED is capital-ledger state, not order lifecycle state.
                    # A successful submit remains a live/acked order until venue
                    # facts or user-channel events prove a lifecycle transition.
                    if current_state == "UNKNOWN":
                        current_state = "VENUE_SUBMIT_ACKED"
                else:
                    current_state = EVENT_STATE[event_type]
            elif event_type == "Reconciled":
                current_state = EVENT_STATE[event_type]
                pending_reconcile = bool(payload.get("pending_reconcile", False))
            elif event_type == "OrderLifecycleProjected":
                current_state = str(payload.get("order_lifecycle_state") or EVENT_STATE[event_type])
                pending_reconcile = bool(payload.get("pending_reconcile", False))
            else:
                current_state = EVENT_STATE[event_type]
        last = rows[-1]
        self.conn.execute(
            """
            INSERT INTO edli_live_order_projection (
                aggregate_id, event_id, final_intent_id, current_state,
                last_sequence, last_event_type, last_event_hash,
                pending_reconcile, venue_order_id, updated_at, schema_version,
                posterior_id, probability_authority
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
            ON CONFLICT(aggregate_id) DO UPDATE SET
                event_id = excluded.event_id,
                final_intent_id = excluded.final_intent_id,
                current_state = excluded.current_state,
                last_sequence = excluded.last_sequence,
                last_event_type = excluded.last_event_type,
                last_event_hash = excluded.last_event_hash,
                pending_reconcile = excluded.pending_reconcile,
                venue_order_id = excluded.venue_order_id,
                updated_at = excluded.updated_at,
                schema_version = excluded.schema_version,
                -- H2_E2E: COALESCE so a later rebuild from events that lack a
                -- decision_audit block (e.g. a reconcile-only re-projection)
                -- never clears an already-recorded posterior link. NULL on
                -- canonical orders. Observability only.
                posterior_id = COALESCE(excluded.posterior_id, edli_live_order_projection.posterior_id),
                probability_authority = COALESCE(excluded.probability_authority, edli_live_order_projection.probability_authority)
            """,
            (
                aggregate_id,
                event_id,
                final_intent_id,
                current_state,
                int(last["event_sequence"]),
                str(last["event_type"]),
                str(last["event_hash"]),
                1 if pending_reconcile else 0,
                venue_order_id,
                _dt(datetime.now(timezone.utc)),
                posterior_id,
                probability_authority,
            ),
        )
        return self.get_projection(aggregate_id)

    def get_event(self, aggregate_event_id: str) -> LiveOrderAggregateEvent:
        row = self.conn.execute(
            "SELECT * FROM edli_live_order_events WHERE aggregate_event_id = ?",
            (aggregate_event_id,),
        ).fetchone()
        if row is None:
            raise LiveOrderAggregateError("live-order aggregate event not found")
        return _event_from_row(row)

    def get_projection(self, aggregate_id: str) -> LiveOrderProjection:
        row = self.conn.execute(
            "SELECT * FROM edli_live_order_projection WHERE aggregate_id = ?",
            (aggregate_id,),
        ).fetchone()
        if row is None:
            raise LiveOrderAggregateError("live-order projection not found")
        return LiveOrderProjection(
            aggregate_id=str(row["aggregate_id"]),
            event_id=str(row["event_id"]),
            final_intent_id=row["final_intent_id"],
            current_state=str(row["current_state"]),
            last_sequence=int(row["last_sequence"]),
            last_event_type=row["last_event_type"],
            last_event_hash=row["last_event_hash"],
            pending_reconcile=bool(row["pending_reconcile"]),
            venue_order_id=row["venue_order_id"],
        )

    def latest_event_of_type(
        self,
        aggregate_id: str,
        event_type: str,
    ) -> LiveOrderAggregateEvent | None:
        row = self.conn.execute(
            """
            SELECT *
            FROM edli_live_order_events
            WHERE aggregate_id = ? AND event_type = ?
            ORDER BY event_sequence DESC
            LIMIT 1
            """,
            (aggregate_id, event_type),
        ).fetchone()
        return _event_from_row(row) if row is not None else None

    def _latest_row(self, aggregate_id: str) -> sqlite3.Row | None:
        return self.conn.execute(
            """
            SELECT *
            FROM edli_live_order_events
            WHERE aggregate_id = ?
            ORDER BY event_sequence DESC
            LIMIT 1
            """,
            (aggregate_id,),
        ).fetchone()

    def _validate_event_append(
        self,
        *,
        aggregate_id: str,
        event_type: str,
        payload: dict[str, Any],
        latest: sqlite3.Row | None,
        occurred_at: datetime,
    ) -> None:
        if event_type == "PreSubmitRevalidated":
            _validate_pre_submit_revalidation_payload(payload)
            return
        if event_type == "ExecutionCommandCreated":
            if latest is None or latest["event_type"] not in {"PreSubmitRevalidated", "LiveCapReserved"}:
                raise LiveOrderAggregateError(
                    "ExecutionCommandCreated requires preceding PreSubmitRevalidated or LiveCapReserved event"
                )
            revalidation_row = latest
            if latest["event_type"] == "LiveCapReserved":
                revalidation_row = self._latest_row_of_type(str(latest["aggregate_id"]), "PreSubmitRevalidated")
                if revalidation_row is None:
                    raise LiveOrderAggregateError("ExecutionCommandCreated requires preceding PreSubmitRevalidated event")
            revalidation = _payload(revalidation_row)
            _validate_pre_submit_revalidation_payload(revalidation)
            if payload.get("final_intent_id") != revalidation.get("final_intent_id"):
                raise LiveOrderAggregateError("ExecutionCommandCreated final_intent_id must match pre-submit revalidation")
            if payload.get("event_id") != revalidation.get("event_id"):
                raise LiveOrderAggregateError("ExecutionCommandCreated event_id must match pre-submit revalidation")
            if payload.get("pre_submit_event_hash") != revalidation_row["event_hash"]:
                raise LiveOrderAggregateError("ExecutionCommandCreated pre_submit_event_hash must match PreSubmitRevalidated event")
            live_cap_row = self._latest_row_of_type(str(latest["aggregate_id"]), "LiveCapReserved")
            if live_cap_row is None:
                raise LiveOrderAggregateError("ExecutionCommandCreated requires preceding LiveCapReserved event")
            live_cap = _payload(live_cap_row)
            if payload.get("final_intent_id") != live_cap.get("final_intent_id"):
                raise LiveOrderAggregateError("ExecutionCommandCreated final_intent_id must match live cap reservation")
            if payload.get("event_id") != live_cap.get("event_id"):
                raise LiveOrderAggregateError("ExecutionCommandCreated event_id must match live cap reservation")
            if payload.get("live_cap_reserved_event_hash") != live_cap_row["event_hash"]:
                raise LiveOrderAggregateError("ExecutionCommandCreated live_cap_reserved_event_hash must match LiveCapReserved event")
            return
        if event_type == "VenueSubmitAttempted":
            command_row = self._require_latest_row_of_type(aggregate_id, "ExecutionCommandCreated", event_type)
            self._require_command_binding(event_type, payload, command_row)
            if self._latest_row_of_type_after(
                aggregate_id,
                "VenueSubmitAttempted",
                int(command_row["event_sequence"]),
            ) is not None:
                raise LiveOrderAggregateError("VenueSubmitAttempted already exists for current command")
            return
        if event_type == "VenueSubmitAcknowledged":
            command_row = self._require_latest_row_of_type(aggregate_id, "ExecutionCommandCreated", event_type)
            self._latest_row_of_type_after(
                aggregate_id,
                "VenueSubmitAttempted",
                int(command_row["event_sequence"]),
                event_type,
            )
            self._require_command_binding(event_type, payload, command_row)
            if not str(payload.get("venue_order_id") or "").strip():
                raise LiveOrderAggregateError("VenueSubmitAcknowledged requires venue_order_id")
            return
        if event_type == "SubmitRejected":
            command_row = self._require_latest_row_of_type(aggregate_id, "ExecutionCommandCreated", event_type)
            if not _is_pre_submit_rejection_payload(payload):
                self._latest_row_of_type_after(
                    aggregate_id,
                    "VenueSubmitAttempted",
                    int(command_row["event_sequence"]),
                    event_type,
                )
            self._require_command_binding(event_type, payload, command_row)
            if not str(payload.get("reason_code") or payload.get("reject_reason") or "").strip():
                raise LiveOrderAggregateError("SubmitRejected requires reason_code")
            return
        if event_type == "SubmitUnknown":
            command_row = self._require_latest_row_of_type(aggregate_id, "ExecutionCommandCreated", event_type)
            self._latest_row_of_type_after(
                aggregate_id,
                "VenueSubmitAttempted",
                int(command_row["event_sequence"]),
                event_type,
            )
            self._require_command_binding(event_type, payload, command_row)
            return
        if event_type in {"UserOrderObserved", "UserTradeObserved"}:
            command_row = self._require_latest_row_of_type(aggregate_id, "ExecutionCommandCreated", event_type)
            self._require_user_channel_submit_binding(aggregate_id, event_type, payload, command_row, occurred_at)
            return
        if event_type == "Reconciled":
            self._require_latest_row_of_type(aggregate_id, "ExecutionCommandCreated", event_type)
            projection = self.conn.execute(
                "SELECT pending_reconcile FROM edli_live_order_projection WHERE aggregate_id = ?",
                (aggregate_id,),
            ).fetchone()
            if self._latest_row_of_type(aggregate_id, "SubmitUnknown") is None and not (
                projection is not None and bool(projection["pending_reconcile"])
            ):
                raise LiveOrderAggregateError("Reconciled requires SubmitUnknown or pending_reconcile projection")
            return
        if event_type == "CapTransitioned":
            command_row = self._require_latest_row_of_type(aggregate_id, "ExecutionCommandCreated", event_type)
            self._require_command_binding(event_type, payload, command_row)
            if not str(payload.get("execution_receipt_hash") or "").strip():
                raise LiveOrderAggregateError("CapTransitioned requires execution_receipt_hash")
            to_status = str(payload.get("to_status") or "")
            reason = str(payload.get("transition_reason") or payload.get("reason_code") or "")
            command_sequence = int(command_row["event_sequence"])
            if to_status == "PENDING_RECONCILE" and self._latest_row_of_type_after(
                aggregate_id,
                "SubmitUnknown",
                command_sequence,
            ) is None:
                raise LiveOrderAggregateError("CapTransitioned PENDING_RECONCILE requires SubmitUnknown")
            if to_status == "CONSUMED" and self._latest_row_of_type_after(
                aggregate_id,
                "VenueSubmitAcknowledged",
                command_sequence,
            ) is None:
                raise LiveOrderAggregateError("CapTransitioned CONSUMED requires VenueSubmitAcknowledged")
            if to_status == "RELEASED" and reason != "SUBMIT_DISABLED":
                if self._latest_row_of_type_after(
                    aggregate_id,
                    "SubmitRejected",
                    command_sequence,
                ) is None and self._latest_row_of_type(aggregate_id, "Reconciled") is None:
                    raise LiveOrderAggregateError("CapTransitioned RELEASED requires SubmitRejected or Reconciled")
            return
        if event_type == "OrderLifecycleProjected":
            command_row = self._require_latest_row_of_type(aggregate_id, "ExecutionCommandCreated", event_type)
            self._require_command_binding(event_type, payload, command_row)
            lifecycle_state = str(payload.get("order_lifecycle_state") or "")
            if lifecycle_state != "TERMINAL_NO_FILL":
                raise LiveOrderAggregateError("OrderLifecycleProjected requires TERMINAL_NO_FILL lifecycle state")
            if payload.get("exposure_created") is not False:
                raise LiveOrderAggregateError("OrderLifecycleProjected TERMINAL_NO_FILL requires exposure_created=false")
            if not str(payload.get("venue_order_id") or "").strip():
                raise LiveOrderAggregateError("OrderLifecycleProjected requires venue_order_id")
            if self._latest_row_of_type(aggregate_id, "UserTradeObserved") is not None:
                raise LiveOrderAggregateError("OrderLifecycleProjected cannot terminal-no-fill after UserTradeObserved")
            return

    def _latest_row_of_type(self, aggregate_id: str, event_type: str) -> sqlite3.Row | None:
        return self.conn.execute(
            """
            SELECT *
            FROM edli_live_order_events
            WHERE aggregate_id = ? AND event_type = ?
            ORDER BY event_sequence DESC
            LIMIT 1
            """,
            (aggregate_id, event_type),
        ).fetchone()

    def _latest_row_of_type_after(
        self,
        aggregate_id: str,
        event_type: str,
        min_event_sequence: int,
        requiring_event_type: str | None = None,
    ) -> sqlite3.Row | None:
        row = self.conn.execute(
            """
            SELECT *
            FROM edli_live_order_events
            WHERE aggregate_id = ?
              AND event_type = ?
              AND event_sequence > ?
            ORDER BY event_sequence DESC
            LIMIT 1
            """,
            (aggregate_id, event_type, min_event_sequence),
        ).fetchone()
        if row is None and requiring_event_type is not None:
            raise LiveOrderAggregateError(
                f"{requiring_event_type} requires preceding {event_type}"
            )
        return row

    def _require_latest_row_of_type(self, aggregate_id: str, required_type: str, event_type: str) -> sqlite3.Row:
        row = self._latest_row_of_type(aggregate_id, required_type)
        if row is None:
            raise LiveOrderAggregateError(f"{event_type} requires preceding {required_type}")
        return row

    def _require_command_binding(self, event_type: str, payload: dict[str, Any], command_row: sqlite3.Row) -> None:
        command = _payload(command_row)
        if payload.get("event_id") != command.get("event_id"):
            raise LiveOrderAggregateError(f"{event_type} event_id must match ExecutionCommandCreated")
        if payload.get("final_intent_id") != command.get("final_intent_id"):
            raise LiveOrderAggregateError(f"{event_type} final_intent_id must match ExecutionCommandCreated")
        command_id = command.get("execution_command_id")
        if payload.get("execution_command_id") is not None and payload.get("execution_command_id") != command_id:
            raise LiveOrderAggregateError(f"{event_type} execution_command_id must match ExecutionCommandCreated")

    def _require_user_channel_submit_binding(
        self,
        aggregate_id: str,
        event_type: str,
        payload: dict[str, Any],
        command_row: sqlite3.Row,
        occurred_at: datetime,
    ) -> None:
        self._require_command_binding(event_type, payload, command_row)
        projection = self.conn.execute(
            "SELECT current_state, pending_reconcile FROM edli_live_order_projection WHERE aggregate_id = ?",
            (aggregate_id,),
        ).fetchone()
        if projection is not None and projection["current_state"] == "RECONCILED" and not bool(projection["pending_reconcile"]):
            raise LiveOrderAggregateError("user-channel event cannot append after terminal Reconciled projection")
        if _dt(occurred_at) < str(command_row["occurred_at"]):
            raise LiveOrderAggregateError("user-channel event occurred_at precedes ExecutionCommandCreated")
        if not any(
            self._latest_row_of_type(aggregate_id, submit_type) is not None
            for submit_type in ("VenueSubmitAttempted", "VenueSubmitAcknowledged", "SubmitUnknown")
        ):
            raise LiveOrderAggregateError(f"{event_type} requires venue submit attempt, acknowledgement, or unknown")
        venue_order_id = str(payload.get("venue_order_id") or "").strip()
        if not venue_order_id:
            raise LiveOrderAggregateError(f"{event_type} requires venue_order_id")
        ack_row = self._latest_row_of_type(aggregate_id, "VenueSubmitAcknowledged")
        unknown_row = self._latest_row_of_type(aggregate_id, "SubmitUnknown")
        bound_order_id = None
        if ack_row is not None:
            bound_order_id = _payload(ack_row).get("venue_order_id")
        elif unknown_row is not None:
            bound_order_id = _payload(unknown_row).get("venue_order_id")
        if bound_order_id and venue_order_id != str(bound_order_id):
            raise LiveOrderAggregateError(f"{event_type} venue_order_id must match submitted order")
        message_hash = str(payload.get("raw_user_channel_message_hash") or "").strip()
        if not message_hash:
            raise LiveOrderAggregateError(f"{event_type} requires raw_user_channel_message_hash")
        duplicate = self.conn.execute(
            """
            SELECT 1
            FROM edli_live_order_events
            WHERE aggregate_id = ?
              AND event_type IN ('UserOrderObserved', 'UserTradeObserved')
              AND json_extract(payload_json, '$.raw_user_channel_message_hash') = ?
            LIMIT 1
            """,
            (aggregate_id, message_hash),
        ).fetchone()
        if duplicate is not None:
            raise LiveOrderAggregateError("duplicate user-channel message hash for aggregate")

    def _reserve_user_channel_message_hash(
        self,
        *,
        aggregate_id: str,
        event_type: str,
        payload: dict[str, Any],
        occurred_at: datetime,
    ) -> None:
        message_hash = str(payload.get("raw_user_channel_message_hash") or "").strip()
        venue_order_id = str(payload.get("venue_order_id") or "").strip()
        message_type = "order" if event_type == "UserOrderObserved" else "trade"
        existing = self.conn.execute(
            """
            SELECT aggregate_id, venue_order_id, message_type
            FROM edli_user_channel_message_dedup
            WHERE message_hash = ?
            """,
            (message_hash,),
        ).fetchone()
        if existing is not None:
            if (
                str(existing["aggregate_id"]) != aggregate_id
                or str(existing["venue_order_id"]) != venue_order_id
                or str(existing["message_type"]) != message_type
            ):
                raise LiveOrderAggregateError("EDLI_USER_CHANNEL_MESSAGE_HASH_DRIFT")
            raise LiveOrderAggregateError("duplicate user-channel message hash for aggregate")
        self.conn.execute(
            """
            INSERT INTO edli_user_channel_message_dedup (
                message_hash, aggregate_id, venue_order_id, message_type,
                observed_at, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                message_hash,
                aggregate_id,
                venue_order_id,
                message_type,
                _dt(occurred_at),
                _dt(datetime.now(timezone.utc)),
            ),
        )


def _event_from_row(row: sqlite3.Row) -> LiveOrderAggregateEvent:
    return LiveOrderAggregateEvent(
        aggregate_event_id=str(row["aggregate_event_id"]),
        aggregate_id=str(row["aggregate_id"]),
        event_sequence=int(row["event_sequence"]),
        event_type=str(row["event_type"]),
        parent_event_hash=row["parent_event_hash"],
        event_hash=str(row["event_hash"]),
        payload=_payload(row),
        payload_hash=str(row["payload_hash"]),
        source_authority=str(row["source_authority"]),
        occurred_at=datetime.fromisoformat(str(row["occurred_at"])),
    )


def _payload(row: sqlite3.Row) -> dict[str, Any]:
    import json

    payload = json.loads(str(row["payload_json"]))
    if not isinstance(payload, dict):
        raise LiveOrderAggregateError("live-order event payload must be an object")
    return payload


def _is_pre_submit_rejection_payload(payload: dict[str, Any]) -> bool:
    return (
        payload.get("pre_submit_rejection") is True
        and payload.get("submit_status") == "PRE_SUBMIT_ERROR"
        and payload.get("venue_call_started") is False
    )


def _optional_posterior_id(value: Any) -> int | None:
    """Fail-soft coercion of a payload posterior_id to int (None on any failure).

    H2_E2E: the authority builder emits posterior_id as a string in some paths
    (event_reactor_adapter.py:5778) and as an int via the receipt in others, so
    coerce defensively. Returns None for None / empty / non-numeric — the
    posterior trace is observability only and must never raise in the projection
    rebuild (which runs on every live-order event append).
    """
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _validate_pre_submit_revalidation_payload(payload: dict[str, Any]) -> None:
    missing = [field for field in PRE_SUBMIT_REQUIRED_FIELDS if field not in payload]
    if missing:
        raise LiveOrderAggregateError("PreSubmitRevalidated missing required fields: " + ",".join(missing))
    # would_cross_book must be false for post-only MAKER orders (a crossing post-only
    # would take, violating maker intent / venue post-only rejection). A TAKER
    # (FOK/FAK, post_only is False) is designed to cross to fill immediately, so a
    # crossing book is expected and must not be rejected here.
    if payload.get("post_only") is not False:  # True or missing/None → maker-or-unknown → enforce
        if payload.get("would_cross_book") is not False:
            raise LiveOrderAggregateError("PreSubmitRevalidated requires would_cross_book=false")
    if payload.get("tick_aligned") is not True:
        raise LiveOrderAggregateError("PreSubmitRevalidated requires tick_aligned=true")
    if payload.get("size_ok") is not True:
        raise LiveOrderAggregateError("PreSubmitRevalidated requires size_ok=true")
    if payload.get("heartbeat_status") != "OK":
        raise LiveOrderAggregateError("PreSubmitRevalidated requires heartbeat_status=OK")
    if payload.get("user_ws_status") != "OK":
        raise LiveOrderAggregateError("PreSubmitRevalidated requires user_ws_status=OK")
    if payload.get("venue_connectivity_status") != "OK":
        raise LiveOrderAggregateError("PreSubmitRevalidated requires venue_connectivity_status=OK")
    if payload.get("balance_allowance_status") != "OK":
        raise LiveOrderAggregateError("PreSubmitRevalidated requires balance_allowance_status=OK")
    for provenance_field in (
        "book_authority_id",
        "book_captured_at",
        "heartbeat_authority_id",
        "heartbeat_checked_at",
        "user_ws_authority_id",
        "user_ws_checked_at",
        "venue_connectivity_authority_id",
        "venue_connectivity_checked_at",
        "balance_allowance_authority_id",
        "balance_allowance_checked_at",
    ):
        if not str(payload.get(provenance_field) or "").strip():
            raise LiveOrderAggregateError(f"PreSubmitRevalidated requires {provenance_field}")
    quote_age_ms = _non_negative_number(payload.get("quote_age_ms"), "quote_age_ms")
    max_quote_age_ms = _non_negative_number(payload.get("max_quote_age_ms", quote_age_ms), "max_quote_age_ms")
    if quote_age_ms > max_quote_age_ms:
        raise LiveOrderAggregateError("PreSubmitRevalidated quote_age_ms exceeds max_quote_age_ms")
    _positive_number(payload.get("tick_size"), "tick_size")
    _positive_number(payload.get("min_order_size"), "min_order_size")
    _non_negative_number(payload.get("current_best_bid"), "current_best_bid")
    _non_negative_number(payload.get("current_best_ask"), "current_best_ask")
    limit_price = _positive_number(payload.get("limit_price"), "limit_price")
    q_live = _probability_number(payload.get("q_live"), "q_live")
    q_lcb = _probability_number(payload.get("q_lcb_5pct"), "q_lcb_5pct")
    if q_lcb > q_live:
        raise LiveOrderAggregateError("PreSubmitRevalidated requires q_lcb_5pct <= q_live")
    expected_edge = _positive_number(payload.get("expected_edge"), "expected_edge")
    size = _positive_number(payload.get("size"), "size")
    min_entry_price = _non_negative_number(
        payload.get("min_entry_price"), "min_entry_price"
    )
    min_expected_profit_usd = _non_negative_number(
        payload.get("min_expected_profit_usd"), "min_expected_profit_usd"
    )
    min_submit_edge_density = _non_negative_number(
        payload.get("min_submit_edge_density"), "min_submit_edge_density"
    )
    if min_entry_price + 1e-12 < _LIVE_ENTRY_MIN_ENTRY_PRICE:
        raise LiveOrderAggregateError("PreSubmitRevalidated min_entry_price below live floor")
    if limit_price + 1e-12 < max(min_entry_price, _LIVE_ENTRY_MIN_ENTRY_PRICE):
        raise LiveOrderAggregateError("PreSubmitRevalidated entry price below strategy floor")
    if not str(payload.get("expected_edge_source_certificate_hash") or "").strip():
        raise LiveOrderAggregateError("PreSubmitRevalidated requires expected_edge_source_certificate_hash")
    if not str(payload.get("cost_basis_source_certificate_hash") or "").strip():
        raise LiveOrderAggregateError("PreSubmitRevalidated requires cost_basis_source_certificate_hash")
    submit_edge = q_lcb - limit_price
    if submit_edge <= 0.0:
        raise LiveOrderAggregateError("PreSubmitRevalidated requires positive submit q_lcb-minus-limit")
    if expected_edge > submit_edge + 1e-6:
        raise LiveOrderAggregateError("PreSubmitRevalidated expected_edge exceeds submit q_lcb-minus-limit")
    submit_expected_profit_usd = submit_edge * size
    if submit_expected_profit_usd + 1e-9 < min_expected_profit_usd:
        raise LiveOrderAggregateError("PreSubmitRevalidated expected profit below strategy floor")
    submit_edge_density = submit_edge / limit_price
    if submit_edge_density + 1e-9 < min_submit_edge_density:
        raise LiveOrderAggregateError("PreSubmitRevalidated submit edge density below strategy floor")
    _validate_pre_submit_probability_authority(payload, q_live=q_live, q_lcb=q_lcb)
    # GATE#85 fix (2026-06-01): taker orders (post_only is False, FOK/FAK) are exempt
    # from the post_only=True and GTC/GTD invariants — those are maker-only constraints.
    # Explicit post_only=False signals taker intent; missing/None → fail-closed as maker.
    # Mirrors the would_cross_book conditioning at lines 601-603.
    if payload.get("post_only") is not False:  # True or missing/None → maker-or-unknown
        # For maker (post_only=True), enforce both the flag and TIF constraints.
        # For unknown (None/missing), also enforce — fail-closed.
        if payload.get("post_only") is not True:
            raise LiveOrderAggregateError("PreSubmitRevalidated requires post_only=true for current EDLI executor law")
        if payload.get("time_in_force") not in {"GTC", "GTD"}:
            raise LiveOrderAggregateError("PreSubmitRevalidated post_only requires GTC/GTD time_in_force")


def _positive_number(value: Any, name: str) -> float:
    number = _non_negative_number(value, name)
    if number <= 0:
        raise LiveOrderAggregateError(f"PreSubmitRevalidated requires positive {name}")
    return number


def _probability_number(value: Any, name: str) -> float:
    number = _non_negative_number(value, name)
    if number > 1:
        raise LiveOrderAggregateError(f"PreSubmitRevalidated requires probability {name}")
    return number


def _validate_pre_submit_probability_authority(
    payload: dict[str, Any],
    *,
    q_live: float,
    q_lcb: float,
) -> None:
    event_type = str(payload.get("event_type") or "").strip()
    if event_type == _DAY0_EVENT_TYPE:
        _validate_day0_submit_observation_authority(payload)
        if payload.get("qkernel_execution_economics") not in (None, ""):
            raise LiveOrderAggregateError(
                "PreSubmitRevalidated day0 must not carry qkernel_execution_economics"
            )
        return
    _validate_qkernel_submit_probability(payload, q_live=q_live, q_lcb=q_lcb)


def _validate_day0_submit_observation_authority(payload: dict[str, Any]) -> None:
    try:
        assert_live_day0_payload_authority(payload)
    except Day0AuthorityError as exc:
        raise LiveOrderAggregateError(
            "PreSubmitRevalidated day0 observation authority required:"
            + str(exc)
        ) from None


def _validate_qkernel_submit_probability(payload: dict[str, Any], *, q_live: float, q_lcb: float) -> None:
    economics = payload.get("qkernel_execution_economics")
    if economics in (None, ""):
        raise LiveOrderAggregateError("PreSubmitRevalidated requires qkernel_execution_economics")
    if not isinstance(economics, dict):
        raise LiveOrderAggregateError("PreSubmitRevalidated requires object qkernel_execution_economics")
    if str(payload.get("selection_authority_applied") or "").strip() != "qkernel_spine":
        raise LiveOrderAggregateError("PreSubmitRevalidated requires qkernel selection authority")
    if str(economics.get("source") or "").strip() != "qkernel_spine":
        raise LiveOrderAggregateError("PreSubmitRevalidated qkernel source must be qkernel_spine")
    route_id = str(economics.get("route_id") or "").upper()
    route_type = str(economics.get("route_type") or "").lower()
    if route_type != "direct" and not route_id.startswith("DIRECT_"):
        return
    if economics.get("direction_law_ok") is not True:
        raise LiveOrderAggregateError("PreSubmitRevalidated qkernel direction_law_ok must be true")
    if economics.get("coherence_allows") is not True:
        raise LiveOrderAggregateError("PreSubmitRevalidated qkernel coherence_allows must be true")
    selection_guard_basis = str(economics.get("selection_guard_basis") or "").strip()
    if not selection_guard_basis:
        raise LiveOrderAggregateError("PreSubmitRevalidated qkernel selection_guard_basis missing")
    if selection_guard_basis == "SIDE_NOT_ARMED":
        raise LiveOrderAggregateError("PreSubmitRevalidated qkernel selection_guard_basis blocks side")
    if economics.get("selection_guard_abstained") is not False:
        raise LiveOrderAggregateError("PreSubmitRevalidated qkernel selection_guard_abstained must be false")
    selection_guard_q_safe = _positive_number(
        economics.get("selection_guard_q_safe"),
        "qkernel_execution_economics.selection_guard_q_safe",
    )
    if selection_guard_q_safe > 1.0:
        raise LiveOrderAggregateError(
            "PreSubmitRevalidated qkernel selection_guard_q_safe requires probability"
        )
    route = economics.get("route") if isinstance(economics.get("route"), dict) else {}
    native_side = _native_curve_side_for_direction(str(payload.get("direction") or ""))
    qkernel_side = str(route.get("side") or economics.get("side") or "").upper()
    if qkernel_side and native_side is not None and qkernel_side != native_side:
        raise LiveOrderAggregateError("PreSubmitRevalidated qkernel side must match submit direction")
    payoff_q_point = _probability_number(economics.get("payoff_q_point"), "qkernel_execution_economics.payoff_q_point")
    payoff_q_lcb = _probability_number(economics.get("payoff_q_lcb"), "qkernel_execution_economics.payoff_q_lcb")
    if not math.isclose(payoff_q_point, q_live, rel_tol=1e-9, abs_tol=1e-6):
        raise LiveOrderAggregateError("PreSubmitRevalidated qkernel payoff_q_point mismatches submit q_live")
    if not math.isclose(payoff_q_lcb, q_lcb, rel_tol=1e-9, abs_tol=1e-6):
        raise LiveOrderAggregateError("PreSubmitRevalidated qkernel payoff_q_lcb mismatches submit q_lcb_5pct")
    cost = _positive_number(economics.get("cost"), "qkernel_execution_economics.cost")
    edge_lcb = _positive_number(economics.get("edge_lcb"), "qkernel_execution_economics.edge_lcb")
    optimal_delta_u = _positive_number(
        economics.get("optimal_delta_u"),
        "qkernel_execution_economics.optimal_delta_u",
    )
    _ = optimal_delta_u
    false_edge_rate = _positive_number(
        economics.get("false_edge_rate"),
        "qkernel_execution_economics.false_edge_rate",
    )
    if false_edge_rate > 1.0:
        raise LiveOrderAggregateError(
            "PreSubmitRevalidated qkernel false_edge_rate requires probability"
        )
    if not math.isclose(payoff_q_lcb, cost + edge_lcb, rel_tol=1e-9, abs_tol=1e-9):
        raise LiveOrderAggregateError("PreSubmitRevalidated qkernel payoff edge inconsistent")
    limit_price = _positive_number(payload.get("limit_price"), "limit_price")
    if limit_price > cost + 1e-6:
        raise LiveOrderAggregateError("PreSubmitRevalidated submit price worse than qkernel cost")
    expected_edge = _positive_number(payload.get("expected_edge"), "expected_edge")
    if expected_edge > edge_lcb + 1e-6:
        raise LiveOrderAggregateError("PreSubmitRevalidated expected_edge exceeds qkernel edge_lcb")


def _native_curve_side_for_direction(direction: str) -> str | None:
    normalized = str(direction or "").strip().lower()
    if normalized.endswith("_yes"):
        return "YES"
    if normalized.endswith("_no"):
        return "NO"
    return None


def _non_negative_number(value: Any, name: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise LiveOrderAggregateError(f"PreSubmitRevalidated requires numeric {name}") from None
    if number < 0:
        raise LiveOrderAggregateError(f"PreSubmitRevalidated requires non-negative {name}")
    return number


def _dt(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()
