"""R3 M4 cancel/replace safety for live exits.

This module intentionally does not add command grammar.  The M4 typed surface
uses ``CancelOutcome`` values, then maps them onto the existing venue-command
events:

- CANCELED -> ``CANCEL_ACKED`` -> command state ``CANCELLED``
- NOT_CANCELED -> ``CANCEL_FAILED`` -> command state ``REVIEW_REQUIRED``
- UNKNOWN -> ``CANCEL_REPLACE_BLOCKED`` -> command state ``REVIEW_REQUIRED``

Future M5 exchange reconciliation owns proving absence and unblocking unknowns.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Callable, Literal, Mapping, Optional

from src.control.cutover_guard import CutoverPending, gate_for_intent
from src.execution.command_bus import IntentKind

CancelStatus = Literal["CANCELED", "NOT_CANCELED", "UNKNOWN"]

_EXIT_MUTEX_SCHEMA = """
CREATE TABLE IF NOT EXISTS exit_mutex_holdings (
  mutex_key TEXT PRIMARY KEY,
  command_id TEXT NOT NULL REFERENCES venue_commands(command_id) DEFERRABLE INITIALLY DEFERRED,
  acquired_at TEXT NOT NULL,
  released_at TEXT,
  release_reason TEXT
);
"""

_TERMINAL_REPLACEMENT_STATES = frozenset(
    {"CANCELLED", "FILLED", "EXPIRED", "REJECTED", "SUBMIT_REJECTED"}
)
_MUTEX_RELEASE_STATES = _TERMINAL_REPLACEMENT_STATES | frozenset(
    {"CANCELED", "CANCEL_CONFIRMED"}
)
_ACTIVE_REPLACEMENT_STATES = frozenset(
    {
        "INTENT_CREATED",
        "SNAPSHOT_BOUND",
        "SIGNED_PERSISTED",
        "POSTING",
        "POST_ACKED",
        "SUBMITTING",
        "ACKED",
        "UNKNOWN",
        "SUBMIT_UNKNOWN_SIDE_EFFECT",
        "PARTIAL",
        "CANCEL_PENDING",
        "REVIEW_REQUIRED",
        # Venue/order-fact vocabulary can appear in payloads or future rows;
        # keep it blocking here without adding command grammar.
        "RESTING",
        "CANCEL_REQUESTED",
        "CANCEL_UNKNOWN",
    }
)
_CANCEL_REQUESTABLE_STATES = frozenset(
    {
        "INTENT_CREATED",
        "POSTING",
        "POST_ACKED",
        "SUBMITTING",
        "ACKED",
        "UNKNOWN",
        "SUBMIT_UNKNOWN_SIDE_EFFECT",
        "PARTIAL",
    }
)


@dataclass(frozen=True)
class CancelOutcome:
    """Typed outcome for a venue cancel attempt."""

    status: CancelStatus
    reason: Optional[str]
    raw_response: dict[str, Any]


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _enum_value(value: Any) -> str:
    if value is None:
        return ""
    if hasattr(value, "value"):
        return str(value.value)
    return str(value)


def _capability_component(
    component: str,
    *,
    allowed: bool,
    reason: str,
    **details: Any,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "component": str(component),
        "allowed": bool(allowed),
        "reason": str(reason),
    }
    if details:
        payload["details"] = {
            key: _enum_value(value) if not isinstance(value, (bool, int, float)) else value
            for key, value in details.items()
        }
    return payload


def _build_cancel_execution_capability(
    *,
    command_id: str,
    venue_order_id: str,
    command_state: str,
    cutover: Any | None,
    freshness_time: str,
) -> dict[str, Any]:
    cutover_allowed = bool(getattr(cutover, "allow_cancel", False))
    cutover_state = _enum_value(getattr(cutover, "state", None))
    cutover_reason = getattr(cutover, "block_reason", None)
    identity_allowed = bool(str(command_id).strip() and str(venue_order_id).strip())
    requestable = command_state in _CANCEL_REQUESTABLE_STATES or command_state == "CANCEL_PENDING"
    components = [
        _capability_component(
            "cutover_guard",
            allowed=cutover_allowed,
            reason=str(cutover_reason or ("allowed" if cutover_allowed else "not_evaluated_or_blocked")),
            state=cutover_state,
        ),
        _capability_component(
            "cancel_command_identity",
            allowed=identity_allowed,
            reason="allowed" if identity_allowed else "missing_command_or_venue_order_id",
            command_id=command_id,
            venue_order_id=venue_order_id,
        ),
        _capability_component(
            "venue_order_cancelability",
            allowed=bool(identity_allowed and requestable),
            reason=(
                "missing_command_or_venue_order_id"
                if not identity_allowed
                else ("allowed" if requestable else f"state_not_cancel_requestable:{command_state}")
            ),
            command_state=command_state,
        ),
    ]
    proof: dict[str, Any] = {
        "schema_version": 1,
        "action": "CANCEL",
        "intent_kind": "CANCEL",
        "mode": "cancel",
        "allowed": all(bool(component.get("allowed")) for component in components),
        "freshness_time": freshness_time,
        "command_id": str(command_id),
        "venue_order_id": str(venue_order_id),
        "command_state": command_state,
        "components": components,
    }
    proof["capability_id"] = hashlib.sha256(
        json.dumps(proof, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:32]
    return proof


def _has_cancel_execution_capability(
    conn: sqlite3.Connection,
    *,
    command_id: str,
    venue_order_id: str,
) -> bool:
    row = conn.execute(
        """
        SELECT payload_json
          FROM venue_command_events
         WHERE command_id = ?
           AND event_type = 'CANCEL_REQUESTED'
         ORDER BY sequence_no DESC
         LIMIT 1
        """,
        (command_id,),
    ).fetchone()
    if row is None or not row[0]:
        return False
    try:
        payload = json.loads(row[0])
    except (TypeError, json.JSONDecodeError):
        return False
    capability = payload.get("execution_capability") if isinstance(payload, dict) else None
    if not isinstance(capability, dict):
        return False
    return (
        capability.get("action") == "CANCEL"
        and capability.get("intent_kind") == "CANCEL"
        and capability.get("command_id") == str(command_id)
        and capability.get("venue_order_id") == str(venue_order_id)
        and bool(capability.get("allowed"))
    )


def init_exit_mutex_schema(conn: sqlite3.Connection) -> None:
    """Create the M4 exit mutex table if absent."""

    conn.execute(_EXIT_MUTEX_SCHEMA)


def _mutex_key(position_id: int | str, token_id: str) -> str:
    position = str(position_id).strip()
    token = str(token_id).strip()
    if not position:
        raise ValueError("position_id is required")
    if not token:
        raise ValueError("token_id is required")
    return f"{position}:{token}"


class ExitMutex:
    """SQLite-backed single-holder mutex per ``(position_id, token_id)``.

    Rows persist after release so the command chain remains auditable.  A new
    acquire for a released key overwrites the holder fields and clears
    ``released_at``.
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        init_exit_mutex_schema(conn)

    def acquire(self, position_id: int | str, token_id: str, command_id: str) -> bool:
        key = _mutex_key(position_id, token_id)
        command = str(command_id).strip()
        if not command:
            raise ValueError("command_id is required")
        now = _utcnow()
        row = self.conn.execute(
            """
            SELECT command_id, released_at
              FROM exit_mutex_holdings
             WHERE mutex_key = ?
            """,
            (key,),
        ).fetchone()
        if row is None:
            try:
                self.conn.execute(
                    """
                    INSERT INTO exit_mutex_holdings (
                      mutex_key, command_id, acquired_at, released_at, release_reason
                    ) VALUES (?, ?, ?, NULL, NULL)
                    """,
                    (key, command, now),
                )
                return True
            except sqlite3.IntegrityError:
                row = self.conn.execute(
                    "SELECT command_id, released_at FROM exit_mutex_holdings WHERE mutex_key = ?",
                    (key,),
                ).fetchone()
                if row is None:
                    raise
        existing_command = str(row[0])
        released_at = row[1]
        if released_at is None:
            return existing_command == command
        cursor = self.conn.execute(
            """
            UPDATE exit_mutex_holdings
               SET command_id = ?, acquired_at = ?, released_at = NULL, release_reason = NULL
             WHERE mutex_key = ?
               AND command_id = ?
               AND released_at = ?
            """,
            (command, now, key, existing_command, released_at),
        )
        if cursor.rowcount == 1:
            return True

        # Another writer changed the released row between our read and write.
        # Fail closed unless the same command is now the active holder.
        row = self.conn.execute(
            "SELECT command_id, released_at FROM exit_mutex_holdings WHERE mutex_key = ?",
            (key,),
        ).fetchone()
        if row is None:
            return False
        return row[1] is None and str(row[0]) == command

    def release(
        self,
        position_id: int | str,
        token_id: str,
        command_id: str,
        *,
        reason: str = "released",
    ) -> None:
        key = _mutex_key(position_id, token_id)
        self.conn.execute(
            """
            UPDATE exit_mutex_holdings
               SET released_at = COALESCE(released_at, ?), release_reason = ?
             WHERE mutex_key = ?
               AND command_id = ?
               AND released_at IS NULL
            """,
            (_utcnow(), str(reason), key, str(command_id)),
        )

    def abandon_unpersisted(self, command_id: str) -> None:
        """Delete a pre-command acquire when command insertion never happened."""

        self.conn.execute(
            "DELETE FROM exit_mutex_holdings WHERE command_id = ? AND released_at IS NULL",
            (str(command_id),),
        )


def release_exit_mutex_for_command_state(
    conn: sqlite3.Connection,
    command_id: str,
    state: str,
) -> bool:
    """Release a held exit mutex when a command reaches a safe terminal state."""

    normalized = str(state).upper()
    if normalized not in _MUTEX_RELEASE_STATES:
        return False
    try:
        init_exit_mutex_schema(conn)
        cursor = conn.execute(
            """
            UPDATE exit_mutex_holdings
               SET released_at = COALESCE(released_at, ?), release_reason = ?
             WHERE command_id = ?
               AND released_at IS NULL
            """,
            (_utcnow(), normalized, str(command_id)),
        )
    except sqlite3.OperationalError as exc:
        if "no such table" in str(exc):
            return False
        raise
    return cursor.rowcount > 0


def _nonempty(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, (str, bytes)):
        return bool(value)
    if isinstance(value, Mapping):
        return bool(value)
    try:
        return bool(list(value))  # type: ignore[arg-type]
    except TypeError:
        return bool(value)


def _reason_from(value: Any, fallback: str) -> str:
    if value is None:
        return fallback
    if isinstance(value, Mapping):
        parts: list[str] = []
        for key, item in value.items():
            parts.append(f"{key}: {item}")
        return "; ".join(parts) if parts else fallback
    if isinstance(value, (list, tuple, set)):
        return ", ".join(str(item) for item in value) or fallback
    return str(value) or fallback


def parse_cancel_response(raw: Mapping[str, Any] | dict[str, Any] | None) -> CancelOutcome:
    """Normalize venue cancel payloads into the M4 typed outcome surface."""

    if raw is None:
        return CancelOutcome("UNKNOWN", "empty_cancel_response", {})
    response = dict(raw)
    canceled = response.get("canceled", response.get("cancelled"))
    not_canceled = response.get("not_canceled", response.get("not_cancelled"))

    if _nonempty(not_canceled):
        return CancelOutcome(
            "NOT_CANCELED",
            _reason_from(not_canceled, "not_canceled"),
            response,
        )
    if response.get("success") is False:
        reason = (
            response.get("errorCode")
            or response.get("error_code")
            or response.get("errorMessage")
            or response.get("error_message")
            or response.get("reason")
            or response.get("error")
            or "cancel_failed"
        )
        return CancelOutcome("NOT_CANCELED", str(reason), response)
    if response.get("error"):
        return CancelOutcome("NOT_CANCELED", str(response.get("error")), response)
    if _nonempty(canceled):
        return CancelOutcome("CANCELED", None, response)
    status = str(response.get("status") or "").upper()
    if status in {"CANCELED", "CANCELLED", "CANCEL_CONFIRMED"}:
        return CancelOutcome("CANCELED", None, response)
    if response.get("success") is True and status in {"", "OK", "SUCCESS"}:
        return CancelOutcome("CANCELED", None, response)
    return CancelOutcome("UNKNOWN", "unrecognized_cancel_response", response)


def _latest_event(conn: sqlite3.Connection, command_id: str) -> tuple[str, dict[str, Any] | None] | None:
    row = conn.execute(
        """
        SELECT event_type, payload_json
          FROM venue_command_events
         WHERE command_id = ?
         ORDER BY sequence_no DESC
         LIMIT 1
        """,
        (command_id,),
    ).fetchone()
    if row is None:
        return None
    payload = None
    if row[1]:
        payload = json.loads(row[1])
    return str(row[0]), payload


def _block_reason_for_command(
    conn: sqlite3.Connection,
    *,
    command_id: str,
    state: str,
    venue_order_id: str | None,
) -> str:
    latest = _latest_event(conn, command_id)
    if state == "REVIEW_REQUIRED" and latest and latest[0] == "CANCEL_REPLACE_BLOCKED":
        return f"cancel_unknown_requires_m5: command_id={command_id} order_id={venue_order_id or ''}"
    if state == "CANCEL_PENDING":
        return f"cancel_pending: command_id={command_id} order_id={venue_order_id or ''}"
    if state in {"UNKNOWN", "SUBMIT_UNKNOWN_SIDE_EFFECT"}:
        return f"submit_unknown_side_effect: command_id={command_id}"
    return f"active_prior_exit_sell: state={state} command_id={command_id}"


def can_submit_replacement_sell(
    conn: sqlite3.Connection,
    position_id: int | str,
    token_id: str,
    *,
    exclude_idempotency_key: str | None = None,
) -> tuple[bool, Optional[str]]:
    """Return whether a new sell may replace prior exit sells for this position/token."""

    try:
        rows = conn.execute(
            """
            SELECT command_id, state, venue_order_id, idempotency_key
              FROM venue_commands
             WHERE position_id = ?
               AND token_id = ?
               AND side = 'SELL'
               AND intent_kind = 'EXIT'
             ORDER BY updated_at DESC, created_at DESC
            """,
            (str(position_id), str(token_id)),
        ).fetchall()
    except sqlite3.OperationalError as exc:
        if "no such table" in str(exc):
            return False, "venue_commands_unavailable"
        raise
    for row in rows:
        if exclude_idempotency_key and str(row[3]) == str(exclude_idempotency_key):
            continue
        state = str(row[1]).upper()
        if state in _TERMINAL_REPLACEMENT_STATES:
            continue
        if state in _ACTIVE_REPLACEMENT_STATES or state not in _TERMINAL_REPLACEMENT_STATES:
            return False, _block_reason_for_command(
                conn,
                command_id=str(row[0]),
                state=state,
                venue_order_id=str(row[2]) if row[2] is not None else None,
            )
    return True, None


def request_cancel_for_command(
    conn: sqlite3.Connection,
    command_id: str,
    cancel_order: Callable[[str], Mapping[str, Any] | dict[str, Any] | None],
    *,
    observed_at: str | None = None,
) -> CancelOutcome:
    """Append cancel intent, call the provided cancel function, and map outcome.

    The callable is injected so tests never contact a live venue.  Exceptions
    after the cancel call may have crossed a venue side-effect boundary, so M4
    records ``UNKNOWN`` through ``CANCEL_REPLACE_BLOCKED`` and leaves M5 to
    prove absence before any replacement sell.
    """

    from src.state.venue_command_repo import append_event, get_command

    when = observed_at or _utcnow()
    cmd = get_command(conn, command_id)
    if cmd is None:
        raise ValueError(f"Unknown command_id: {command_id!r}")
    venue_order_id = str(cmd.get("venue_order_id") or "")
    if not venue_order_id:
        outcome = CancelOutcome("UNKNOWN", "missing_venue_order_id", {})
        _append_cancel_unknown(
            conn,
            command_id,
            outcome,
            when,
            execution_capability=_build_cancel_execution_capability(
                command_id=command_id,
                venue_order_id=venue_order_id,
                command_state=str(cmd.get("state") or "").upper(),
                cutover=None,
                freshness_time=when,
            ),
        )
        return outcome

    cutover = gate_for_intent(IntentKind.CANCEL)
    if not cutover.allow_cancel:
        raise CutoverPending(cutover.block_reason or cutover.state.value)

    command_state = str(cmd.get("state") or "").upper()
    if command_state != "CANCEL_PENDING":
        execution_capability = _build_cancel_execution_capability(
            command_id=command_id,
            venue_order_id=venue_order_id,
            command_state=command_state,
            cutover=cutover,
            freshness_time=when,
        )
        append_event(
            conn,
            command_id=command_id,
            event_type="CANCEL_REQUESTED",
            occurred_at=when,
            payload={
                "venue_order_id": venue_order_id,
                "execution_capability": execution_capability,
            },
        )
    elif not _has_cancel_execution_capability(
        conn,
        command_id=command_id,
        venue_order_id=venue_order_id,
    ):
        outcome = CancelOutcome("UNKNOWN", "missing_cancel_capability_proof", {})
        _append_cancel_unknown(conn, command_id, outcome, when)
        return outcome
    try:
        raw = cancel_order(venue_order_id)
        outcome = parse_cancel_response(raw)
    except Exception as exc:  # cancel may have succeeded at venue before timeout
        outcome = CancelOutcome(
            "UNKNOWN",
            f"post_cancel_exception_possible_side_effect: {exc}",
            {"exception_type": type(exc).__name__, "exception_message": str(exc)},
        )
    if outcome.status == "CANCELED":
        append_event(
            conn,
            command_id=command_id,
            event_type="CANCEL_ACKED",
            occurred_at=when,
            payload={"venue_order_id": venue_order_id, "cancel_outcome": outcome.raw_response},
        )
    elif outcome.status == "NOT_CANCELED":
        append_event(
            conn,
            command_id=command_id,
            event_type="CANCEL_FAILED",
            occurred_at=when,
            payload={
                "venue_order_id": venue_order_id,
                "reason": outcome.reason,
                "cancel_outcome": outcome.raw_response,
            },
        )
    else:
        _append_cancel_unknown(conn, command_id, outcome, when)
    return outcome


def _append_cancel_unknown(
    conn: sqlite3.Connection,
    command_id: str,
    outcome: CancelOutcome,
    observed_at: str,
    *,
    execution_capability: Mapping[str, Any] | None = None,
) -> None:
    from src.state.venue_command_repo import append_event, get_command

    cmd = get_command(conn, command_id)
    if cmd is None:
        raise ValueError(f"Unknown command_id: {command_id!r}")
    state = str(cmd.get("state") or "").upper()
    if state != "CANCEL_PENDING":
        payload: dict[str, Any] = {"venue_order_id": cmd.get("venue_order_id") or ""}
        if execution_capability is not None:
            payload["execution_capability"] = dict(execution_capability)
        append_event(
            conn,
            command_id=command_id,
            event_type="CANCEL_REQUESTED",
            occurred_at=observed_at,
            payload=payload,
        )
    append_event(
        conn,
        command_id=command_id,
        event_type="CANCEL_REPLACE_BLOCKED",
        occurred_at=observed_at,
        payload={
            "reason": outcome.reason or "cancel_unknown",
            "cancel_outcome": outcome.raw_response,
            "requires_m5_reconcile": True,
            "semantic_cancel_status": "CANCEL_UNKNOWN",
        },
    )


def remaining_exit_shares(conn: sqlite3.Connection, command_id: str) -> Decimal | None:
    """Return latest known remaining order size for a partially filled exit command."""

    row = conn.execute(
        """
        SELECT remaining_size
          FROM venue_order_facts
         WHERE command_id = ?
           AND remaining_size IS NOT NULL
         ORDER BY local_sequence DESC
         LIMIT 1
        """,
        (str(command_id),),
    ).fetchone()
    if row is None or row[0] is None:
        return None
    return Decimal(str(row[0]))
