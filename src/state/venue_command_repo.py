# Created: 2026-04-26
# Last reused/audited: 2026-04-26
# Authority basis: docs/operations/task_2026-04-26_execution_state_truth_p1_command_bus/implementation_plan.md §P1.S1
"""Durable command journal — append-only repo API for venue_commands / venue_command_events.

Public API:
  insert_command(conn, *, ...) -> None
  append_event(conn, *, command_id, event_type, occurred_at, payload=None) -> str
  get_command(conn, command_id) -> Optional[dict]
  find_unresolved_commands(conn) -> Iterable[dict]
  find_command_by_idempotency_key(conn, key) -> Optional[dict]
  list_events(conn, command_id) -> list[dict]

Only this module may INSERT/UPDATE/DELETE on venue_command_events (NC-18).

Atomicity: mutating operations use SAVEPOINT-based context manager (not
`with conn:`). Project memory L30 (`feedback_with_conn_nested_savepoint_audit`):
Python sqlite3 `with conn:` commits + releases SAVEPOINTs, silently destroying
any outer SAVEPOINT a caller may have established. SAVEPOINTs nest correctly,
so callers can wrap repo calls inside their own transaction or savepoint without
losing rollback granularity. P1.S3 executor will rely on this.
"""
from __future__ import annotations

import contextlib
import datetime
import json
import sqlite3
import uuid
from typing import Iterable, Iterator, Optional


# ---------------------------------------------------------------------------
# State transition table (INV-28 / implementation_plan.md §P1.S1)
# Row key = current state; value = frozenset of legal event_types from that state.
# Column "from (initial)" is handled specially inside insert_command.
# ---------------------------------------------------------------------------

# Maps (current_state, event_type) -> state_after.
# Any pair absent from this dict is an illegal transition (raises ValueError).
_TRANSITIONS: dict[tuple[str, str], str] = {
    # from INTENT_CREATED
    ("INTENT_CREATED", "SUBMIT_REQUESTED"):   "SUBMITTING",
    ("INTENT_CREATED", "REVIEW_REQUIRED"):    "REVIEW_REQUIRED",

    # from SUBMITTING
    ("SUBMITTING", "SUBMIT_ACKED"):           "ACKED",
    ("SUBMITTING", "SUBMIT_REJECTED"):        "REJECTED",
    ("SUBMITTING", "SUBMIT_UNKNOWN"):         "UNKNOWN",
    ("SUBMITTING", "CANCEL_REQUESTED"):       "CANCEL_PENDING",
    ("SUBMITTING", "REVIEW_REQUIRED"):        "REVIEW_REQUIRED",

    # from ACKED
    ("ACKED", "PARTIAL_FILL_OBSERVED"):       "PARTIAL",
    ("ACKED", "FILL_CONFIRMED"):              "FILLED",
    ("ACKED", "CANCEL_REQUESTED"):            "CANCEL_PENDING",
    ("ACKED", "EXPIRED"):                     "EXPIRED",
    ("ACKED", "REVIEW_REQUIRED"):             "REVIEW_REQUIRED",

    # from UNKNOWN
    ("UNKNOWN", "SUBMIT_ACKED"):              "ACKED",
    ("UNKNOWN", "SUBMIT_REJECTED"):           "REJECTED",
    ("UNKNOWN", "PARTIAL_FILL_OBSERVED"):     "PARTIAL",
    ("UNKNOWN", "FILL_CONFIRMED"):            "FILLED",
    ("UNKNOWN", "CANCEL_REQUESTED"):          "CANCEL_PENDING",
    ("UNKNOWN", "EXPIRED"):                   "EXPIRED",
    ("UNKNOWN", "REVIEW_REQUIRED"):           "REVIEW_REQUIRED",

    # from PARTIAL
    ("PARTIAL", "PARTIAL_FILL_OBSERVED"):     "PARTIAL",
    ("PARTIAL", "FILL_CONFIRMED"):            "FILLED",
    ("PARTIAL", "CANCEL_REQUESTED"):          "CANCEL_PENDING",
    ("PARTIAL", "EXPIRED"):                   "EXPIRED",
    ("PARTIAL", "REVIEW_REQUIRED"):           "REVIEW_REQUIRED",

    # from FILLED
    ("FILLED", "REVIEW_REQUIRED"):            "REVIEW_REQUIRED",

    # from CANCEL_PENDING
    ("CANCEL_PENDING", "CANCEL_ACKED"):       "CANCELLED",
    ("CANCEL_PENDING", "EXPIRED"):            "EXPIRED",
    ("CANCEL_PENDING", "REVIEW_REQUIRED"):    "REVIEW_REQUIRED",
}


def _new_id() -> str:
    return uuid.uuid4().hex[:16]


def _row_to_dict(row: sqlite3.Row) -> dict:
    return dict(row)


def _payload_default(value):
    """JSON-serialize datetime, date, bytes; let everything else raise.

    P1.S4 recovery loop will routinely attach datetime payloads (occurred_at
    snapshots, etc.). Coerce known unserializable types to ISO/hex strings;
    keep TypeError for genuinely unknown shapes so callers see the failure.
    """
    if isinstance(value, (datetime.datetime, datetime.date)):
        return value.isoformat()
    if isinstance(value, bytes):
        return value.hex()
    raise TypeError(
        f"Object of type {type(value).__name__} is not JSON serializable; "
        f"convert to a serializable shape before passing to append_event(payload=...)."
    )


@contextlib.contextmanager
def _savepoint_atomic(conn: sqlite3.Connection) -> Iterator[None]:
    """Atomic-region context manager that nests inside outer transactions.

    Unlike `with conn:` (which BEGINs/COMMITs at the statement level and
    silently RELEASEs an outer SAVEPOINT mid-flight — see project memory L30),
    SAVEPOINT/RELEASE/ROLLBACK TO compose. Callers can wrap repo calls inside
    their own SAVEPOINT or transaction; if they rollback the outer scope, the
    repo's writes roll back too.
    """
    sp_name = f"vcr_{uuid.uuid4().hex[:8]}"
    conn.execute(f"SAVEPOINT {sp_name}")
    try:
        yield
    except Exception:
        conn.execute(f"ROLLBACK TO SAVEPOINT {sp_name}")
        conn.execute(f"RELEASE SAVEPOINT {sp_name}")
        raise
    else:
        conn.execute(f"RELEASE SAVEPOINT {sp_name}")


@contextlib.contextmanager
def _row_factory_as(conn: sqlite3.Connection, factory) -> Iterator[None]:
    """Temporarily swap conn.row_factory; restore in `finally`.

    Encapsulates the swap pattern used by every read function so callers
    can't drift into ad-hoc swaps that forget to restore on exception.
    """
    saved = conn.row_factory
    conn.row_factory = factory
    try:
        yield
    finally:
        conn.row_factory = saved


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def insert_command(
    conn: sqlite3.Connection,
    *,
    command_id: str,
    position_id: str,
    decision_id: str,
    idempotency_key: str,
    intent_kind: str,
    market_id: str,
    token_id: str,
    side: str,
    size: float,
    price: float,
    created_at: str,
) -> None:
    """INSERT a new venue_commands row in INTENT_CREATED state.

    Atomically appends the INTENT_CREATED event in the same transaction,
    then updates last_event_id on the command row.

    Raises sqlite3.IntegrityError if idempotency_key already exists.
    """
    event_id = _new_id()

    with _savepoint_atomic(conn):
        conn.execute(
            """
            INSERT INTO venue_commands (
                command_id, position_id, decision_id, idempotency_key,
                intent_kind, market_id, token_id, side, size, price,
                venue_order_id, state, last_event_id, created_at, updated_at,
                review_required_reason
            ) VALUES (
                :command_id, :position_id, :decision_id, :idempotency_key,
                :intent_kind, :market_id, :token_id, :side, :size, :price,
                NULL, 'INTENT_CREATED', NULL, :created_at, :created_at,
                NULL
            )
            """,
            {
                "command_id": command_id,
                "position_id": position_id,
                "decision_id": decision_id,
                "idempotency_key": idempotency_key,
                "intent_kind": intent_kind,
                "market_id": market_id,
                "token_id": token_id,
                "side": side,
                "size": size,
                "price": price,
                "created_at": created_at,
            },
        )
        conn.execute(
            """
            INSERT INTO venue_command_events (
                event_id, command_id, sequence_no, event_type,
                occurred_at, payload_json, state_after
            ) VALUES (
                :event_id, :command_id, 1, 'INTENT_CREATED',
                :occurred_at, NULL, 'INTENT_CREATED'
            )
            """,
            {
                "event_id": event_id,
                "command_id": command_id,
                "occurred_at": created_at,
            },
        )
        conn.execute(
            "UPDATE venue_commands SET last_event_id = ? WHERE command_id = ?",
            (event_id, command_id),
        )


def append_event(
    conn: sqlite3.Connection,
    *,
    command_id: str,
    event_type: str,
    occurred_at: str,
    payload: Optional[dict] = None,
) -> str:
    """Append a venue_command_events row and update venue_commands.state.

    Returns the new event_id. Atomic via savepoint (composable with outer
    transactions; see _savepoint_atomic).
    Raises ValueError on illegal grammar transition.
    Raises sqlite3.IntegrityError if (command_id, sequence_no) collides (shouldn't
    happen in normal usage but preserved for safety).
    Raises TypeError if payload contains non-JSON-serializable shapes that
    aren't datetime/date/bytes (which are coerced to ISO/hex automatically).
    """
    with _savepoint_atomic(conn):
        with _row_factory_as(conn, None):
            row = conn.execute(
                "SELECT state FROM venue_commands WHERE command_id = ?",
                (command_id,),
            ).fetchone()
        if row is None:
            raise ValueError(f"Unknown command_id: {command_id!r}")

        current_state = row[0]
        key = (current_state, event_type)
        if key not in _TRANSITIONS:
            raise ValueError(
                f"Illegal command-event grammar transition: "
                f"state={current_state!r} event={event_type!r}"
            )

        state_after = _TRANSITIONS[key]

        with _row_factory_as(conn, None):
            seq_row = conn.execute(
                "SELECT COALESCE(MAX(sequence_no), 0) + 1 AS next_seq "
                "FROM venue_command_events WHERE command_id = ?",
                (command_id,),
            ).fetchone()
        next_seq = seq_row[0]

        event_id = _new_id()
        payload_json = (
            json.dumps(payload, default=_payload_default)
            if payload is not None else None
        )

        conn.execute(
            """
            INSERT INTO venue_command_events (
                event_id, command_id, sequence_no, event_type,
                occurred_at, payload_json, state_after
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (event_id, command_id, next_seq, event_type,
             occurred_at, payload_json, state_after),
        )
        conn.execute(
            """
            UPDATE venue_commands
            SET state = ?, last_event_id = ?, updated_at = ?
            WHERE command_id = ?
            """,
            (state_after, event_id, occurred_at, command_id),
        )

    return event_id


def get_command(conn: sqlite3.Connection, command_id: str) -> Optional[dict]:
    """Return command row as dict, None if not found."""
    with _row_factory_as(conn, sqlite3.Row):
        row = conn.execute(
            "SELECT * FROM venue_commands WHERE command_id = ?",
            (command_id,),
        ).fetchone()
    return _row_to_dict(row) if row is not None else None


def find_unresolved_commands(conn: sqlite3.Connection) -> Iterable[dict]:
    """Yield commands in {SUBMITTING, UNKNOWN, REVIEW_REQUIRED}."""
    with _row_factory_as(conn, sqlite3.Row):
        rows = conn.execute(
            "SELECT * FROM venue_commands "
            "WHERE state IN ('SUBMITTING', 'UNKNOWN', 'REVIEW_REQUIRED')"
        ).fetchall()
    return [_row_to_dict(r) for r in rows]


def find_command_by_idempotency_key(
    conn: sqlite3.Connection, key: str
) -> Optional[dict]:
    """Lookup an existing command by idempotency_key."""
    with _row_factory_as(conn, sqlite3.Row):
        row = conn.execute(
            "SELECT * FROM venue_commands WHERE idempotency_key = ?",
            (key,),
        ).fetchone()
    return _row_to_dict(row) if row is not None else None


def list_events(conn: sqlite3.Connection, command_id: str) -> list[dict]:
    """Return all events for a command ordered by sequence_no ASC."""
    with _row_factory_as(conn, sqlite3.Row):
        rows = conn.execute(
            "SELECT * FROM venue_command_events "
            "WHERE command_id = ? ORDER BY sequence_no ASC",
            (command_id,),
        ).fetchall()
    return [_row_to_dict(r) for r in rows]
