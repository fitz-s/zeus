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
  find_unknown_command_by_economic_intent(conn, *, ...) -> Optional[dict]
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
import hashlib
import json
import sqlite3
import uuid
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable, Iterator, Mapping, Optional

from src.architecture.decorators import capability, protects

UNRESOLVED_SIDE_EFFECT_STATES: tuple[str, ...] = (
    "SUBMIT_UNKNOWN_SIDE_EFFECT",
    "UNKNOWN",
    "REVIEW_REQUIRED",
)


# ---------------------------------------------------------------------------
# State transition table (INV-28 / implementation_plan.md §P1.S1)
# Row key = current state; value = frozenset of legal event_types from that state.
# Column "from (initial)" is handled specially inside insert_command.
# ---------------------------------------------------------------------------

# Maps (current_state, event_type) -> state_after.
# Any pair absent from this dict is an illegal transition (raises ValueError).
_TRANSITIONS: dict[tuple[str, str], str] = {
    # from INTENT_CREATED
    ("INTENT_CREATED", "SNAPSHOT_BOUND"):      "SNAPSHOT_BOUND",
    ("INTENT_CREATED", "SUBMIT_REQUESTED"):   "SUBMITTING",
    ("INTENT_CREATED", "CANCEL_REQUESTED"):   "CANCEL_PENDING",
    ("INTENT_CREATED", "REVIEW_REQUIRED"):    "REVIEW_REQUIRED",

    # M1 grammar-additive pre-side-effect chain. Existing executor flows may
    # still use INTENT_CREATED -> SUBMITTING -> ACKED until M2+M3 wire the new
    # runtime semantics; these rows reserve the closed grammar without moving
    # order/trade facts out of U2.
    ("SNAPSHOT_BOUND", "SIGNED_PERSISTED"):    "SIGNED_PERSISTED",
    ("SNAPSHOT_BOUND", "REVIEW_REQUIRED"):     "REVIEW_REQUIRED",
    ("SIGNED_PERSISTED", "POSTING"):           "POSTING",
    ("SIGNED_PERSISTED", "REVIEW_REQUIRED"):   "REVIEW_REQUIRED",
    ("POSTING", "POST_ACKED"):                 "POST_ACKED",
    ("POSTING", "SUBMIT_ACKED"):               "ACKED",
    ("POSTING", "SUBMIT_REJECTED"):            "SUBMIT_REJECTED",
    ("POSTING", "SUBMIT_UNKNOWN"):             "UNKNOWN",
    ("POSTING", "SUBMIT_TIMEOUT_UNKNOWN"):     "SUBMIT_UNKNOWN_SIDE_EFFECT",
    ("POSTING", "CLOSED_MARKET_UNKNOWN"):      "SUBMIT_UNKNOWN_SIDE_EFFECT",
    ("POSTING", "CANCEL_REQUESTED"):           "CANCEL_PENDING",
    ("POSTING", "REVIEW_REQUIRED"):            "REVIEW_REQUIRED",
    ("POST_ACKED", "SUBMIT_ACKED"):            "ACKED",
    ("POST_ACKED", "PARTIAL_FILL_OBSERVED"):   "PARTIAL",
    ("POST_ACKED", "FILL_CONFIRMED"):          "FILLED",
    ("POST_ACKED", "CANCEL_REQUESTED"):        "CANCEL_PENDING",
    ("POST_ACKED", "EXPIRED"):                 "EXPIRED",
    ("POST_ACKED", "REVIEW_REQUIRED"):         "REVIEW_REQUIRED",

    # from SUBMITTING
    ("SUBMITTING", "SUBMIT_ACKED"):           "ACKED",
    ("SUBMITTING", "SUBMIT_REJECTED"):        "REJECTED",
    ("SUBMITTING", "SUBMIT_UNKNOWN"):         "UNKNOWN",
    ("SUBMITTING", "SUBMIT_TIMEOUT_UNKNOWN"): "SUBMIT_UNKNOWN_SIDE_EFFECT",
    ("SUBMITTING", "CLOSED_MARKET_UNKNOWN"):  "SUBMIT_UNKNOWN_SIDE_EFFECT",
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
    ("UNKNOWN", "SUBMIT_TIMEOUT_UNKNOWN"):     "SUBMIT_UNKNOWN_SIDE_EFFECT",
    ("UNKNOWN", "CLOSED_MARKET_UNKNOWN"):      "SUBMIT_UNKNOWN_SIDE_EFFECT",
    ("UNKNOWN", "PARTIAL_FILL_OBSERVED"):     "PARTIAL",
    ("UNKNOWN", "FILL_CONFIRMED"):            "FILLED",
    ("UNKNOWN", "CANCEL_REQUESTED"):          "CANCEL_PENDING",
    ("UNKNOWN", "EXPIRED"):                   "EXPIRED",
    ("UNKNOWN", "REVIEW_REQUIRED"):           "REVIEW_REQUIRED",

    # from SUBMIT_UNKNOWN_SIDE_EFFECT (M2 will own active resolution logic)
    ("SUBMIT_UNKNOWN_SIDE_EFFECT", "SUBMIT_ACKED"):          "ACKED",
    ("SUBMIT_UNKNOWN_SIDE_EFFECT", "SUBMIT_REJECTED"):       "SUBMIT_REJECTED",
    ("SUBMIT_UNKNOWN_SIDE_EFFECT", "PARTIAL_FILL_OBSERVED"): "PARTIAL",
    ("SUBMIT_UNKNOWN_SIDE_EFFECT", "FILL_CONFIRMED"):        "FILLED",
    ("SUBMIT_UNKNOWN_SIDE_EFFECT", "CANCEL_REQUESTED"):      "CANCEL_PENDING",
    ("SUBMIT_UNKNOWN_SIDE_EFFECT", "EXPIRED"):               "EXPIRED",
    ("SUBMIT_UNKNOWN_SIDE_EFFECT", "REVIEW_REQUIRED"):       "REVIEW_REQUIRED",

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
    ("CANCEL_PENDING", "CANCEL_FAILED"):      "REVIEW_REQUIRED",
    ("CANCEL_PENDING", "CANCEL_REPLACE_BLOCKED"): "REVIEW_REQUIRED",
    ("CANCEL_PENDING", "EXPIRED"):            "EXPIRED",
    ("CANCEL_PENDING", "REVIEW_REQUIRED"):    "REVIEW_REQUIRED",
}

_PROVENANCE_SOURCES = frozenset(
    {"REST", "WS_USER", "WS_MARKET", "DATA_API", "CHAIN", "OPERATOR", "FAKE_VENUE"}
)
_ORDER_FACT_STATES = frozenset(
    {
        "LIVE",
        "RESTING",
        "MATCHED",
        "PARTIALLY_MATCHED",
        "CANCEL_REQUESTED",
        "CANCEL_CONFIRMED",
        "CANCEL_UNKNOWN",
        "CANCEL_FAILED",
        "EXPIRED",
        "VENUE_WIPED",
        "HEARTBEAT_CANCEL_SUSPECTED",
    }
)
_TRADE_FACT_STATES = frozenset({"MATCHED", "MINED", "CONFIRMED", "RETRYING", "FAILED"})
_TRADE_FILL_ECONOMICS_STATES = frozenset({"MATCHED", "MINED", "CONFIRMED"})
_POSITION_LOT_STATES = frozenset(
    {
        "OPTIMISTIC_EXPOSURE",
        "CONFIRMED_EXPOSURE",
        "EXIT_PENDING",
        "ECONOMICALLY_CLOSED_OPTIMISTIC",
        "ECONOMICALLY_CLOSED_CONFIRMED",
        "SETTLED",
        "QUARANTINED",
    }
)
_PROVENANCE_SUBJECT_TYPES = frozenset(
    {"command", "order", "trade", "lot", "settlement", "wrap_unwrap", "heartbeat"}
)


def _new_id() -> str:
    return uuid.uuid4().hex[:16]


def _row_to_dict(row: sqlite3.Row) -> dict:
    return dict(row)


def _positive_finite_decimal_text(value: Any) -> bool:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return False
    return parsed.is_finite() and parsed > 0


def _row_value(row: Mapping[str, Any] | sqlite3.Row, key: str) -> Any:
    if isinstance(row, sqlite3.Row):
        return row[key]
    return row.get(key)


def trade_fact_has_positive_fill_economics(row: Mapping[str, Any] | sqlite3.Row) -> bool:
    """Return whether a venue trade fact carries executable fill economics."""

    return _positive_finite_decimal_text(
        _row_value(row, "filled_size")
    ) and _positive_finite_decimal_text(_row_value(row, "fill_price"))


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


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        default=_payload_default,
        sort_keys=True,
        separators=(",", ":"),
    )


def _coerce_payload_json(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return _canonical_json(value)


def _payload_hash(payload: Any) -> str:
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def _validate_sha256_hex(field: str, value: str) -> str:
    if not isinstance(value, str) or len(value) != 64:
        raise ValueError(f"{field} must be a sha256 hex string")
    try:
        int(value, 16)
    except ValueError as exc:
        raise ValueError(f"{field} must be a sha256 hex string") from exc
    return value.lower()


def _require_nonempty(field: str, value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} is required")
    return value.strip()


def _validate_source(source: str) -> str:
    source = _require_nonempty("source", source)
    if source not in _PROVENANCE_SOURCES:
        raise ValueError(
            f"source={source!r} is not valid; expected one of {sorted(_PROVENANCE_SOURCES)}"
        )
    return source


def _validate_observed_at(observed_at: str | datetime.datetime | None) -> str:
    if observed_at is None:
        raise ValueError("observed_at is required")
    if isinstance(observed_at, datetime.datetime):
        return observed_at.isoformat()
    return _require_nonempty("observed_at", str(observed_at))


def _max_local_sequence(
    conn: sqlite3.Connection,
    table: str,
    where_sql: str,
    params: tuple[Any, ...],
) -> int:
    with _row_factory_as(conn, None):
        row = conn.execute(
            f"SELECT COALESCE(MAX(local_sequence), 0) FROM {table} WHERE {where_sql}",
            params,
        ).fetchone()
    return int(row[0] if row else 0)


def _coerce_local_sequence(
    conn: sqlite3.Connection,
    *,
    table: str,
    where_sql: str,
    params: tuple[Any, ...],
    local_sequence: int | None,
) -> int:
    current_max = _max_local_sequence(conn, table, where_sql, params)
    if local_sequence is None:
        return current_max + 1
    try:
        seq = int(local_sequence)
    except (TypeError, ValueError) as exc:
        raise ValueError("local_sequence must be an integer") from exc
    if seq <= current_max:
        raise ValueError(
            f"local_sequence must be monotonic for subject; got {seq}, current max {current_max}"
        )
    return seq


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

def insert_submission_envelope(
    conn: sqlite3.Connection,
    envelope,
    *,
    envelope_id: str | None = None,
) -> str:
    """Persist a Z2 VenueSubmissionEnvelope in the U2 append-only table.

    The caller may provide a stable envelope_id to bind a venue command to
    the exact pre-side-effect evidence row. If omitted, the id is a
    deterministic hash of the envelope payload.
    """

    from src.contracts.venue_submission_envelope import VenueSubmissionEnvelope

    if not isinstance(envelope, VenueSubmissionEnvelope):
        raise TypeError("envelope must be a VenueSubmissionEnvelope")
    envelope_id_value = (
        _require_nonempty("envelope_id", envelope_id)
        if envelope_id is not None
        else hashlib.sha256(envelope.to_json().encode("utf-8")).hexdigest()
    )
    _validate_sha256_hex(
        "canonical_pre_sign_payload_hash",
        envelope.canonical_pre_sign_payload_hash,
    )
    _validate_sha256_hex("raw_request_hash", envelope.raw_request_hash)
    if envelope.signed_order_hash is not None:
        _validate_sha256_hex("signed_order_hash", envelope.signed_order_hash)

    with _savepoint_atomic(conn):
        conn.execute(
            """
            INSERT INTO venue_submission_envelopes (
              envelope_id, schema_version, sdk_package, sdk_version, host,
              chain_id, funder_address, condition_id, question_id,
              yes_token_id, no_token_id, selected_outcome_token_id,
              outcome_label, side, price, size, order_type, post_only,
              tick_size, min_order_size, neg_risk, fee_details_json,
              canonical_pre_sign_payload_hash, signed_order_blob,
              signed_order_hash, raw_request_hash, raw_response_json,
              order_id, trade_ids_json, transaction_hashes_json,
              error_code, error_message, captured_at
            ) VALUES (
              :envelope_id, :schema_version, :sdk_package, :sdk_version, :host,
              :chain_id, :funder_address, :condition_id, :question_id,
              :yes_token_id, :no_token_id, :selected_outcome_token_id,
              :outcome_label, :side, :price, :size, :order_type, :post_only,
              :tick_size, :min_order_size, :neg_risk, :fee_details_json,
              :canonical_pre_sign_payload_hash, :signed_order_blob,
              :signed_order_hash, :raw_request_hash, :raw_response_json,
              :order_id, :trade_ids_json, :transaction_hashes_json,
              :error_code, :error_message, :captured_at
            )
            """,
            {
                "envelope_id": envelope_id_value,
                "schema_version": envelope.schema_version,
                "sdk_package": envelope.sdk_package,
                "sdk_version": envelope.sdk_version,
                "host": envelope.host,
                "chain_id": envelope.chain_id,
                "funder_address": envelope.funder_address,
                "condition_id": envelope.condition_id,
                "question_id": envelope.question_id,
                "yes_token_id": envelope.yes_token_id,
                "no_token_id": envelope.no_token_id,
                "selected_outcome_token_id": envelope.selected_outcome_token_id,
                "outcome_label": envelope.outcome_label,
                "side": envelope.side,
                "price": str(envelope.price),
                "size": str(envelope.size),
                "order_type": envelope.order_type,
                "post_only": int(envelope.post_only),
                "tick_size": str(envelope.tick_size),
                "min_order_size": str(envelope.min_order_size),
                "neg_risk": int(envelope.neg_risk),
                "fee_details_json": _canonical_json(envelope.fee_details),
                "canonical_pre_sign_payload_hash": envelope.canonical_pre_sign_payload_hash,
                "signed_order_blob": envelope.signed_order,
                "signed_order_hash": envelope.signed_order_hash,
                "raw_request_hash": envelope.raw_request_hash,
                "raw_response_json": envelope.raw_response_json,
                "order_id": envelope.order_id,
                "trade_ids_json": _canonical_json(list(envelope.trade_ids)),
                "transaction_hashes_json": _canonical_json(list(envelope.transaction_hashes)),
                "error_code": envelope.error_code,
                "error_message": envelope.error_message,
                "captured_at": envelope.captured_at,
            },
        )
    return envelope_id_value


@capability("venue_command_write", lease=False)
@protects("INV-21", "INV-04")
def insert_command(
    conn: sqlite3.Connection,
    *,
    command_id: str,
    envelope_id: str | None = None,
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
    snapshot_id: str | None = None,
    snapshot_checked_at: str | datetime.datetime | None = None,
    expected_min_tick_size=None,
    expected_min_order_size=None,
    expected_neg_risk: bool | None = None,
    venue_order_id: str | None = None,
    reason: str | None = None,
) -> None:
    """INSERT a new venue_commands row in INTENT_CREATED state.

    Atomically appends the INTENT_CREATED event in the same transaction,
    then updates last_event_id on the command row.

    Raises sqlite3.IntegrityError if idempotency_key already exists.
    Raises ValueError if intent_kind / side are not in their closed enum
    grammar (post-critic MAJOR-1: pre-fix the repo persisted any string;
    now it rejects "GIBBERISH" / "LONG" / etc. before INSERT). Defers the
    full enum object to command_bus to avoid a circular import.
    """
    # MAJOR-1: enum-grammar validation at the repo seam. Imported lazily so
    # this module stays import-light and the type module doesn't have to
    # depend on the repo.
    from src.execution.command_bus import IntentKind as _IntentKind
    if intent_kind not in {k.value for k in _IntentKind}:
        raise ValueError(
            f"intent_kind={intent_kind!r} is not a valid IntentKind; "
            f"expected one of {sorted(k.value for k in _IntentKind)}"
        )
    if side not in ("BUY", "SELL"):
        raise ValueError(
            f"side={side!r} must be 'BUY' or 'SELL'"
        )

    snapshot_id_value = snapshot_id.strip() if isinstance(snapshot_id, str) else snapshot_id
    _assert_snapshot_gate(
        conn,
        snapshot_id=snapshot_id_value,
        token_id=token_id,
        price=price,
        size=size,
        checked_at=snapshot_checked_at,
        expected_min_tick_size=expected_min_tick_size,
        expected_min_order_size=expected_min_order_size,
        expected_neg_risk=expected_neg_risk,
    )
    envelope_id_value = _assert_envelope_gate(
        conn,
        envelope_id=envelope_id,
        snapshot_id=snapshot_id_value,
        token_id=token_id,
        side=side,
        price=price,
        size=size,
    )

    event_id = _new_id()

    with _savepoint_atomic(conn):
        conn.execute(
            """
            INSERT INTO venue_commands (
                command_id, snapshot_id, envelope_id, position_id, decision_id, idempotency_key,
                intent_kind, market_id, token_id, side, size, price,
                venue_order_id, state, last_event_id, created_at, updated_at,
                review_required_reason
            ) VALUES (
                :command_id, :snapshot_id, :envelope_id, :position_id, :decision_id, :idempotency_key,
                :intent_kind, :market_id, :token_id, :side, :size, :price,
                :venue_order_id, 'INTENT_CREATED', NULL, :created_at, :created_at,
                NULL
            )
            """,
            {
                "command_id": command_id,
                "snapshot_id": snapshot_id_value,
                "envelope_id": envelope_id_value,
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
                "venue_order_id": venue_order_id,
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
        _append_command_provenance_event(
            conn,
            command_id=command_id,
            event_type="INTENT_CREATED",
            occurred_at=created_at,
            payload={
                "state_after": "INTENT_CREATED",
                "snapshot_id": snapshot_id_value,
                "envelope_id": envelope_id_value,
                "intent_kind": intent_kind,
                "market_id": market_id,
                "token_id": token_id,
                "side": side,
                "size": size,
                "price": price,
                "venue_order_id": venue_order_id,
                "reason": reason,
            },
        )


def _assert_envelope_gate(
    conn: sqlite3.Connection,
    *,
    envelope_id: str | None,
    snapshot_id: str | None,
    token_id: str,
    side: str,
    price: float,
    size: float,
) -> str:
    if not isinstance(envelope_id, str) or not envelope_id.strip():
        raise ValueError("venue command requires provenance envelope_id")
    envelope_id = envelope_id.strip()
    try:
        with _row_factory_as(conn, sqlite3.Row):
            row = conn.execute(
                """
                SELECT selected_outcome_token_id, side, price, size,
                       condition_id, question_id, yes_token_id, no_token_id
                FROM venue_submission_envelopes
                WHERE envelope_id = ?
                """,
                (envelope_id,),
            ).fetchone()
    except sqlite3.OperationalError as exc:
        raise ValueError("venue_submission_envelopes table is unavailable") from exc
    if row is None:
        raise ValueError(f"venue command envelope_id {envelope_id!r} is not persisted")
    if str(row["selected_outcome_token_id"]) != str(token_id):
        raise ValueError(
            "venue command token_id does not match provenance envelope selected_outcome_token_id"
        )
    if str(row["side"]) != str(side):
        raise ValueError("venue command side does not match provenance envelope side")
    if _decimal(row["price"]) != _decimal(price):
        raise ValueError("venue command price does not match provenance envelope price")
    if _decimal(row["size"]) != _decimal(size):
        raise ValueError("venue command size does not match provenance envelope size")
    if isinstance(snapshot_id, str) and snapshot_id.strip():
        with _row_factory_as(conn, sqlite3.Row):
            snapshot_row = conn.execute(
                """
                SELECT condition_id, question_id, yes_token_id, no_token_id
                FROM executable_market_snapshots
                WHERE snapshot_id = ?
                """,
                (snapshot_id.strip(),),
            ).fetchone()
        if snapshot_row is not None:
            for field in ("condition_id", "question_id", "yes_token_id", "no_token_id"):
                if str(row[field]) != str(snapshot_row[field]):
                    raise ValueError(
                        f"provenance envelope {field} does not match executable snapshot"
                    )
    return envelope_id


def _decimal(value: Any) -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"cannot compare decimal value {value!r}") from exc


def _assert_snapshot_gate(
    conn: sqlite3.Connection,
    *,
    snapshot_id: str | None,
    token_id: str,
    price: float,
    size: float,
    checked_at: str | datetime.datetime | None,
    expected_min_tick_size,
    expected_min_order_size,
    expected_neg_risk: bool | None,
) -> None:
    """U1 single insertion-point freshness/tradability gate."""

    from src.contracts.executable_market_snapshot_v2 import (
        StaleMarketSnapshotError,
        assert_snapshot_executable,
    )
    from src.state.snapshot_repo import get_snapshot

    if not isinstance(snapshot_id, str) or not snapshot_id.strip():
        raise StaleMarketSnapshotError("venue command requires executable market snapshot_id")
    snapshot_id = snapshot_id.strip()
    try:
        snapshot = get_snapshot(conn, snapshot_id)
    except sqlite3.OperationalError as exc:
        raise StaleMarketSnapshotError(
            "executable_market_snapshots table is unavailable; cannot validate venue command"
        ) from exc
    assert_snapshot_executable(
        snapshot,
        token_id=token_id,
        price=price,
        size=size,
        now=_coerce_snapshot_checked_at(checked_at),
        expected_min_tick_size=expected_min_tick_size,
        expected_min_order_size=expected_min_order_size,
        expected_neg_risk=expected_neg_risk,
    )


def _coerce_snapshot_checked_at(
    value: str | datetime.datetime | None,
) -> datetime.datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime.datetime):
        return value
    return datetime.datetime.fromisoformat(str(value).replace("Z", "+00:00"))


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
        venue_order_id = _venue_order_id_from_payload(payload)
        if venue_order_id:
            conn.execute(
                """
                UPDATE venue_commands
                SET venue_order_id = ?
                WHERE command_id = ?
                """,
                (venue_order_id, command_id),
            )
        _append_command_provenance_event(
            conn,
            command_id=command_id,
            event_type=event_type,
            occurred_at=occurred_at,
            payload={"state_after": state_after, "payload": payload},
        )
        from src.execution.command_bus import TERMINAL_STATES as _TERMINAL_COMMAND_STATES
        if state_after in {state.value for state in _TERMINAL_COMMAND_STATES}:
            from src.state.collateral_ledger import release_reservation_for_command_state
            from src.execution.exit_safety import release_exit_mutex_for_command_state

            release_reservation_for_command_state(conn, command_id, state_after)
            release_exit_mutex_for_command_state(conn, command_id, state_after)

    return event_id


def _venue_order_id_from_payload(payload: Optional[dict]) -> str | None:
    if not isinstance(payload, dict):
        return None
    for key in ("venue_order_id", "orderID", "orderId", "order_id", "id"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _append_command_provenance_event(
    conn: sqlite3.Connection,
    *,
    command_id: str,
    event_type: str,
    occurred_at: str,
    payload: dict[str, Any],
) -> int:
    return append_provenance_event(
        conn,
        subject_type="command",
        subject_id=command_id,
        event_type=event_type,
        payload_hash=_payload_hash(payload),
        payload_json=payload,
        source="OPERATOR",
        observed_at=occurred_at,
    )


def append_provenance_event(
    conn: sqlite3.Connection,
    *,
    subject_type: str,
    subject_id: str,
    event_type: str,
    payload_hash: str,
    payload_json: Any = None,
    source: str,
    observed_at: str | datetime.datetime | None,
    venue_timestamp: str | datetime.datetime | None = None,
    local_sequence: int | None = None,
) -> int:
    """Append an immutable U2 provenance-envelope event."""

    subject_type = _require_nonempty("subject_type", subject_type)
    if subject_type not in _PROVENANCE_SUBJECT_TYPES:
        raise ValueError(
            f"subject_type={subject_type!r} is not valid; expected {sorted(_PROVENANCE_SUBJECT_TYPES)}"
        )
    subject_id = _require_nonempty("subject_id", subject_id)
    event_type = _require_nonempty("event_type", event_type)
    source = _validate_source(source)
    observed_at_s = _validate_observed_at(observed_at)
    venue_timestamp_s = (
        _validate_observed_at(venue_timestamp) if venue_timestamp is not None else None
    )
    payload_hash = _validate_sha256_hex("payload_hash", payload_hash)
    payload_json_s = _coerce_payload_json(payload_json)

    with _savepoint_atomic(conn):
        seq = _coerce_local_sequence(
            conn,
            table="provenance_envelope_events",
            where_sql="subject_type = ? AND subject_id = ?",
            params=(subject_type, subject_id),
            local_sequence=local_sequence,
        )
        cur = conn.execute(
            """
            INSERT INTO provenance_envelope_events (
                subject_type, subject_id, event_type, payload_hash,
                payload_json, source, observed_at, venue_timestamp, local_sequence
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                subject_type,
                subject_id,
                event_type,
                payload_hash,
                payload_json_s,
                source,
                observed_at_s,
                venue_timestamp_s,
                seq,
            ),
        )
    return int(cur.lastrowid)


def append_order_fact(
    conn: sqlite3.Connection,
    *,
    venue_order_id: str,
    command_id: str,
    state: str,
    remaining_size: str | None = None,
    matched_size: str | None = None,
    source: str,
    observed_at: str | datetime.datetime | None,
    raw_payload_hash: str,
    raw_payload_json: Any = None,
    venue_timestamp: str | datetime.datetime | None = None,
    local_sequence: int | None = None,
) -> int:
    venue_order_id = _require_nonempty("venue_order_id", venue_order_id)
    command_id = _require_nonempty("command_id", command_id)
    state = _require_nonempty("state", state)
    if state not in _ORDER_FACT_STATES:
        raise ValueError(f"order fact state={state!r} is invalid")
    source = _validate_source(source)
    observed_at_s = _validate_observed_at(observed_at)
    venue_timestamp_s = (
        _validate_observed_at(venue_timestamp) if venue_timestamp is not None else None
    )
    raw_payload_hash = _validate_sha256_hex("raw_payload_hash", raw_payload_hash)
    raw_payload_json_s = _coerce_payload_json(raw_payload_json)

    with _savepoint_atomic(conn):
        seq = _coerce_local_sequence(
            conn,
            table="venue_order_facts",
            where_sql="venue_order_id = ?",
            params=(venue_order_id,),
            local_sequence=local_sequence,
        )
        cur = conn.execute(
            """
            INSERT INTO venue_order_facts (
                venue_order_id, command_id, state, remaining_size, matched_size,
                source, observed_at, venue_timestamp, local_sequence,
                raw_payload_hash, raw_payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                venue_order_id,
                command_id,
                state,
                str(remaining_size) if remaining_size is not None else None,
                str(matched_size) if matched_size is not None else None,
                source,
                observed_at_s,
                venue_timestamp_s,
                seq,
                raw_payload_hash,
                raw_payload_json_s,
            ),
        )
        fact_id = int(cur.lastrowid)
        append_provenance_event(
            conn,
            subject_type="order",
            subject_id=venue_order_id,
            event_type=state,
            payload_hash=raw_payload_hash,
            payload_json={
                "fact_id": fact_id,
                "command_id": command_id,
                "remaining_size": remaining_size,
                "matched_size": matched_size,
                "raw_payload": raw_payload_json,
            },
            source=source,
            observed_at=observed_at_s,
            venue_timestamp=venue_timestamp_s,
        )
    return fact_id


def append_trade_fact(
    conn: sqlite3.Connection,
    *,
    trade_id: str,
    venue_order_id: str,
    command_id: str,
    state: str,
    filled_size: str,
    fill_price: str,
    source: str,
    observed_at: str | datetime.datetime | None,
    raw_payload_hash: str,
    raw_payload_json: Any = None,
    fee_paid_micro: int | None = None,
    tx_hash: str | None = None,
    block_number: int | None = None,
    confirmation_count: int | None = None,
    venue_timestamp: str | datetime.datetime | None = None,
    local_sequence: int | None = None,
) -> int:
    trade_id = _require_nonempty("trade_id", trade_id)
    venue_order_id = _require_nonempty("venue_order_id", venue_order_id)
    command_id = _require_nonempty("command_id", command_id)
    state = _require_nonempty("state", state)
    if state not in _TRADE_FACT_STATES:
        raise ValueError(f"trade fact state={state!r} is invalid")
    if state in _TRADE_FILL_ECONOMICS_STATES and not (
        _positive_finite_decimal_text(filled_size)
        and _positive_finite_decimal_text(fill_price)
    ):
        raise ValueError(
            f"{state} trade fact requires positive finite fill economics"
        )
    source = _validate_source(source)
    observed_at_s = _validate_observed_at(observed_at)
    venue_timestamp_s = (
        _validate_observed_at(venue_timestamp) if venue_timestamp is not None else None
    )
    raw_payload_hash = _validate_sha256_hex("raw_payload_hash", raw_payload_hash)
    raw_payload_json_s = _coerce_payload_json(raw_payload_json)

    with _savepoint_atomic(conn):
        seq = _coerce_local_sequence(
            conn,
            table="venue_trade_facts",
            where_sql="trade_id = ?",
            params=(trade_id,),
            local_sequence=local_sequence,
        )
        cur = conn.execute(
            """
            INSERT INTO venue_trade_facts (
                trade_id, venue_order_id, command_id, state, filled_size,
                fill_price, fee_paid_micro, tx_hash, block_number,
                confirmation_count, source, observed_at, venue_timestamp,
                local_sequence, raw_payload_hash, raw_payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                trade_id,
                venue_order_id,
                command_id,
                state,
                str(filled_size),
                str(fill_price),
                fee_paid_micro,
                tx_hash,
                block_number,
                0 if confirmation_count is None else int(confirmation_count),
                source,
                observed_at_s,
                venue_timestamp_s,
                seq,
                raw_payload_hash,
                raw_payload_json_s,
            ),
        )
        fact_id = int(cur.lastrowid)
        append_provenance_event(
            conn,
            subject_type="trade",
            subject_id=trade_id,
            event_type=state,
            payload_hash=raw_payload_hash,
            payload_json={
                "trade_fact_id": fact_id,
                "command_id": command_id,
                "venue_order_id": venue_order_id,
                "filled_size": str(filled_size),
                "fill_price": str(fill_price),
                "tx_hash": tx_hash,
                "raw_payload": raw_payload_json,
            },
            source=source,
            observed_at=observed_at_s,
            venue_timestamp=venue_timestamp_s,
        )
    return fact_id


def append_position_lot(
    conn: sqlite3.Connection,
    *,
    position_id: int,
    state: str,
    shares: int,
    entry_price_avg: str,
    captured_at: str | datetime.datetime,
    state_changed_at: str | datetime.datetime,
    exit_price_avg: str | None = None,
    source_command_id: str | None = None,
    source_trade_fact_id: int | None = None,
    source: str = "OPERATOR",
    observed_at: str | datetime.datetime | None = None,
    raw_payload_hash: str | None = None,
    raw_payload_json: Any = None,
    venue_timestamp: str | datetime.datetime | None = None,
    local_sequence: int | None = None,
) -> int:
    state = _require_nonempty("state", state)
    if state not in _POSITION_LOT_STATES:
        raise ValueError(f"position lot state={state!r} is invalid")
    source = _validate_source(source)
    captured_at_s = _validate_observed_at(captured_at)
    state_changed_at_s = _validate_observed_at(state_changed_at)
    observed_at_s = _validate_observed_at(observed_at or state_changed_at_s)
    venue_timestamp_s = (
        _validate_observed_at(venue_timestamp) if venue_timestamp is not None else None
    )
    payload_for_hash = raw_payload_json if raw_payload_json is not None else {
        "position_id": position_id,
        "state": state,
        "shares": shares,
        "entry_price_avg": entry_price_avg,
        "exit_price_avg": exit_price_avg,
        "source_command_id": source_command_id,
        "source_trade_fact_id": source_trade_fact_id,
    }
    if raw_payload_hash is None:
        raw_payload_hash = _payload_hash(payload_for_hash)
    raw_payload_hash = _validate_sha256_hex("raw_payload_hash", raw_payload_hash)
    raw_payload_json_s = _coerce_payload_json(payload_for_hash)

    with _savepoint_atomic(conn):
        seq = _coerce_local_sequence(
            conn,
            table="position_lots",
            where_sql="position_id = ?",
            params=(int(position_id),),
            local_sequence=local_sequence,
        )
        cur = conn.execute(
            """
            INSERT INTO position_lots (
                position_id, state, shares, entry_price_avg, exit_price_avg,
                source_command_id, source_trade_fact_id, captured_at,
                state_changed_at, source, observed_at, venue_timestamp,
                local_sequence, raw_payload_hash, raw_payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(position_id),
                state,
                int(shares),
                str(entry_price_avg),
                str(exit_price_avg) if exit_price_avg is not None else None,
                source_command_id,
                source_trade_fact_id,
                captured_at_s,
                state_changed_at_s,
                source,
                observed_at_s,
                venue_timestamp_s,
                seq,
                raw_payload_hash,
                raw_payload_json_s,
            ),
        )
        lot_id = int(cur.lastrowid)
        append_provenance_event(
            conn,
            subject_type="lot",
            subject_id=str(lot_id),
            event_type=state,
            payload_hash=raw_payload_hash,
            payload_json={"lot_id": lot_id, "raw_payload": payload_for_hash},
            source=source,
            observed_at=observed_at_s,
            venue_timestamp=venue_timestamp_s,
        )
    return lot_id


def load_calibration_trade_facts(
    conn: sqlite3.Connection,
    *,
    states: Iterable[str] | None = None,
) -> list[dict]:
    """Return only CONFIRMED trade facts for calibration/retraining.

    U2/NC-NEW-H: MATCHED and MINED are execution observations, not settled
    training truth. Explicitly asking for any state except CONFIRMED fails
    closed instead of returning polluted calibration inputs.
    """

    requested = tuple(states) if states is not None else ("CONFIRMED",)
    if any(state != "CONFIRMED" for state in requested):
        raise ValueError("calibration training may consume only CONFIRMED venue_trade_facts")
    with _row_factory_as(conn, sqlite3.Row):
        rows = conn.execute(
            "SELECT * FROM venue_trade_facts WHERE state = 'CONFIRMED' ORDER BY observed_at, trade_fact_id"
        ).fetchall()
    invalid = [
        str(row["trade_fact_id"] or row["trade_id"] or "?")
        for row in rows
        if not trade_fact_has_positive_fill_economics(row)
    ]
    if invalid:
        raise ValueError(
            "confirmed calibration trade facts missing positive finite fill economics: "
            + ", ".join(invalid[:5])
        )
    return [_row_to_dict(row) for row in rows]


def rollback_optimistic_lot_for_failed_trade(
    conn: sqlite3.Connection,
    *,
    source_trade_fact_id: int,
    failed_trade_fact_id: int,
    state_changed_at: str | datetime.datetime,
) -> int:
    """Append a QUARANTINED lot when a previously matched trade fails."""

    with _row_factory_as(conn, sqlite3.Row):
        lot = conn.execute(
            """
            SELECT *
            FROM position_lots
            WHERE source_trade_fact_id = ? AND state = 'OPTIMISTIC_EXPOSURE'
            ORDER BY lot_id DESC
            LIMIT 1
            """,
            (source_trade_fact_id,),
        ).fetchone()
        failed = conn.execute(
            "SELECT * FROM venue_trade_facts WHERE trade_fact_id = ? AND state = 'FAILED'",
            (failed_trade_fact_id,),
        ).fetchone()
    if lot is None:
        raise ValueError("no OPTIMISTIC_EXPOSURE lot found for failed trade rollback")
    if failed is None:
        raise ValueError("failed_trade_fact_id must reference a FAILED trade fact")
    return append_position_lot(
        conn,
        position_id=int(lot["position_id"]),
        state="QUARANTINED",
        shares=int(lot["shares"]),
        entry_price_avg=str(lot["entry_price_avg"]),
        exit_price_avg=lot["exit_price_avg"],
        source_command_id=lot["source_command_id"],
        source_trade_fact_id=failed_trade_fact_id,
        captured_at=lot["captured_at"],
        state_changed_at=state_changed_at,
        source="CHAIN",
        observed_at=failed["observed_at"],
        raw_payload_json={
            "reason": "failed_trade_rollback",
            "source_trade_fact_id": source_trade_fact_id,
            "failed_trade_fact_id": failed_trade_fact_id,
        },
    )


def get_command(conn: sqlite3.Connection, command_id: str) -> Optional[dict]:
    """Return command row as dict, None if not found."""
    with _row_factory_as(conn, sqlite3.Row):
        row = conn.execute(
            "SELECT * FROM venue_commands WHERE command_id = ?",
            (command_id,),
        ).fetchone()
    return _row_to_dict(row) if row is not None else None


def find_unresolved_commands(conn: sqlite3.Connection) -> Iterable[dict]:
    """Yield commands in IN_FLIGHT_STATES.

    Filter set must remain in lockstep with command_bus.IN_FLIGHT_STATES
    (asserted by tests/test_command_bus_types.py
    test_inflight_states_match_repo_unresolved_filter). Post-reviewer
    MEDIUM-2 (2026-04-26): CANCEL_PENDING added so a process restart
    between CANCEL_REQUESTED and CANCEL_ACKED gets reconciled.
    """
    from src.execution.command_bus import IN_FLIGHT_STATES as _IN_FLIGHT_STATES

    values = tuple(state.value for state in _IN_FLIGHT_STATES)
    placeholders = ",".join("?" for _ in values)
    with _row_factory_as(conn, sqlite3.Row):
        rows = conn.execute(
            "SELECT * FROM venue_commands "
            f"WHERE state IN ({placeholders})",
            values,
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


def find_unknown_command_by_economic_intent(
    conn: sqlite3.Connection,
    *,
    intent_kind: str,
    token_id: str,
    side: str,
    price: float,
    size: float,
    exclude_idempotency_key: str | None = None,
) -> Optional[dict]:
    """Find an unresolved command with the same economics.

    M2 duplicate defense: an actor can change ``decision_id`` and therefore
    derive a different idempotency_key for the same order shape.  While a
    prior post-side-effect submit is still unresolved, the economic intent
    itself (token, side, price, size, intent kind) blocks replacement submits.
    Recovery/operator-handoff states preserve the same unresolved economic
    object; they are not allocation or retry clearance.
    """

    with _row_factory_as(conn, sqlite3.Row):
        rows = conn.execute(
            """
            SELECT *
            FROM venue_commands
            WHERE state IN (?, ?, ?)
              AND intent_kind = ?
              AND token_id = ?
              AND side = ?
            ORDER BY updated_at DESC, created_at DESC
            """,
            (*UNRESOLVED_SIDE_EFFECT_STATES, intent_kind, token_id, side),
        ).fetchall()
    wanted_price = _economic_decimal(price)
    wanted_size = _economic_decimal(size)
    for row in rows:
        row_dict = _row_to_dict(row)
        if exclude_idempotency_key and row_dict.get("idempotency_key") == exclude_idempotency_key:
            continue
        if (
            _economic_decimal(row_dict["price"]) == wanted_price
            and _economic_decimal(row_dict["size"]) == wanted_size
        ):
            return row_dict
    return None


def _economic_decimal(value: Any) -> Decimal:
    """Canonicalize order economics using IdempotencyKey precision.

    IdempotencyKey.from_inputs formats price and size to 4 decimals.  The
    M2 same-economic-intent guard must use the same tolerance so float
    representation noise (for example 0.3 vs 0.1 + 0.2) cannot bypass the
    duplicate-submit block by changing only binary-float spelling.
    """

    return _decimal(value).quantize(Decimal("0.0001"))


def list_events(conn: sqlite3.Connection, command_id: str) -> list[dict]:
    """Return all events for a command ordered by sequence_no ASC."""
    with _row_factory_as(conn, sqlite3.Row):
        rows = conn.execute(
            "SELECT * FROM venue_command_events "
            "WHERE command_id = ? ORDER BY sequence_no ASC",
            (command_id,),
        ).fetchall()
    return [_row_to_dict(r) for r in rows]
