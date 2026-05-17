# Created: 2026-04-26
# Last reused/audited: 2026-05-15
# Authority basis: docs/operations/task_2026-05-08_object_invariance_wave27/PLAN.md
#                  + docs/operations/task_2026-05-15_live_order_e2e_goal/LIVE_ORDER_E2E_GOAL_PLAN.md
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

    # Proof-backed operator/recovery clearance only. Payload validation below
    # rejects generic manual edits; the command must already be REVIEW_REQUIRED
    # and the caller must record positive no-side-effect proof.
    ("REVIEW_REQUIRED", "REVIEW_CLEARED_NO_VENUE_SIDE_EFFECT"): "REJECTED",
    ("REVIEW_REQUIRED", "REVIEW_CLEARED_NO_VENUE_EXPOSURE"):    "EXPIRED",
}

_PROVENANCE_SOURCES = frozenset(
    {"REST", "WS_USER", "WS_MARKET", "DATA_API", "CHAIN", "OPERATOR", "FAKE_VENUE"}
)
_PRE_SDK_REVIEW_REQUIRED_REASONS = frozenset({
    "pre_submit_collateral_reservation_failed",
    # Legacy pre-fix commands could fail before SDK submission, remain in
    # SUBMITTING, and then be moved to REVIEW_REQUIRED by recovery.
    "recovery_no_venue_order_id",
})
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
_POSITION_LOT_EXPOSURE_TRADE_STATES = {
    "OPTIMISTIC_EXPOSURE": frozenset({"MATCHED", "MINED"}),
    "CONFIRMED_EXPOSURE": frozenset({"CONFIRMED"}),
}
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


def _decimal_text_equal(left: Any, right: Any) -> bool:
    try:
        left_parsed = Decimal(str(left))
        right_parsed = Decimal(str(right))
    except (InvalidOperation, TypeError, ValueError):
        return False
    return (
        left_parsed.is_finite()
        and right_parsed.is_finite()
        and left_parsed == right_parsed
    )


def _finite_decimal_text(value: Any) -> str:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError("position lot shares must be a finite decimal") from exc
    if not parsed.is_finite():
        raise ValueError("position lot shares must be a finite decimal")
    return format(parsed, "f")


def _row_value(row: Mapping[str, Any] | sqlite3.Row, key: str) -> Any:
    if isinstance(row, sqlite3.Row):
        return row[key]
    return row.get(key)


def trade_fact_has_positive_fill_economics(row: Mapping[str, Any] | sqlite3.Row) -> bool:
    """Return whether a venue trade fact carries executable fill economics."""

    return _positive_finite_decimal_text(
        _row_value(row, "filled_size")
    ) and _positive_finite_decimal_text(_row_value(row, "fill_price"))


def _optimistic_source_trade_fact_ids_for_failed_trade(
    conn: sqlite3.Connection,
    *,
    trade_id: str,
) -> list[int]:
    with _row_factory_as(conn, sqlite3.Row):
        rows = conn.execute(
            """
            SELECT DISTINCT lot.source_trade_fact_id
              FROM position_lots lot
              JOIN venue_trade_facts tf
                ON tf.trade_fact_id = lot.source_trade_fact_id
             WHERE tf.trade_id = ?
               AND tf.state IN ('MATCHED', 'MINED')
               AND lot.state = 'OPTIMISTIC_EXPOSURE'
               AND NOT EXISTS (
                   SELECT 1
                     FROM position_lots quarantined
                     JOIN venue_trade_facts failed
                       ON failed.trade_fact_id = quarantined.source_trade_fact_id
                    WHERE quarantined.position_id = lot.position_id
                      AND quarantined.state = 'QUARANTINED'
                      AND failed.trade_id = tf.trade_id
                      AND failed.state = 'FAILED'
               )
             ORDER BY lot.source_trade_fact_id
            """,
            (trade_id,),
        ).fetchall()
    return [int(row["source_trade_fact_id"]) for row in rows]


def _rollback_optimistic_lots_for_failed_trade(
    conn: sqlite3.Connection,
    *,
    trade_id: str,
    failed_trade_fact_id: int,
    state_changed_at: str | datetime.datetime,
) -> int:
    rolled_back = 0
    for source_trade_fact_id in _optimistic_source_trade_fact_ids_for_failed_trade(
        conn,
        trade_id=trade_id,
    ):
        rollback_optimistic_lot_for_failed_trade(
            conn,
            source_trade_fact_id=source_trade_fact_id,
            failed_trade_fact_id=failed_trade_fact_id,
            state_changed_at=state_changed_at,
        )
        rolled_back += 1
    return rolled_back


def _assert_position_lot_trade_fact_authority(
    conn: sqlite3.Connection,
    *,
    lot_state: str,
    shares: Any,
    entry_price_avg: Any,
    source_command_id: str | None,
    source_trade_fact_id: int | None,
) -> tuple[str | None, int | None]:
    """Validate active exposure lots preserve venue trade-fact authority."""

    allowed_trade_states = _POSITION_LOT_EXPOSURE_TRADE_STATES.get(lot_state)
    if allowed_trade_states is None:
        return source_command_id, source_trade_fact_id

    if source_trade_fact_id is None:
        raise ValueError(f"{lot_state} position lot requires source_trade_fact_id")
    try:
        fact_id = int(source_trade_fact_id)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{lot_state} source_trade_fact_id must be an integer") from exc
    if fact_id <= 0:
        raise ValueError(f"{lot_state} source_trade_fact_id must be positive")

    command_id = _require_nonempty(
        f"{lot_state} source_command_id",
        source_command_id,
    )
    with _row_factory_as(conn, sqlite3.Row):
        trade_fact = conn.execute(
            """
            SELECT
              tf.*,
              cmd.intent_kind AS source_command_intent_kind,
              cmd.side AS source_command_side
            FROM venue_trade_facts tf
            JOIN venue_commands cmd
              ON cmd.command_id = tf.command_id
            WHERE tf.trade_fact_id = ?
            """,
            (fact_id,),
        ).fetchone()
    if trade_fact is None:
        raise ValueError(
            f"{lot_state} source_trade_fact_id must reference venue_trade_facts and venue_commands"
        )

    trade_state = str(trade_fact["state"] or "")
    if trade_state not in allowed_trade_states:
        expected = ", ".join(sorted(allowed_trade_states))
        raise ValueError(
            f"{lot_state} requires trade fact state in {{{expected}}}; got {trade_state!r}"
        )
    if str(trade_fact["command_id"]) != command_id:
        raise ValueError(
            f"{lot_state} source_command_id must match source trade fact command_id"
        )
    intent_kind = str(trade_fact["source_command_intent_kind"] or "").upper()
    side = str(trade_fact["source_command_side"] or "").upper()
    if intent_kind != "ENTRY" or side != "BUY":
        raise ValueError(f"{lot_state} requires ENTRY BUY source command")
    if not trade_fact_has_positive_fill_economics(trade_fact):
        raise ValueError(
            f"{lot_state} source trade fact requires positive finite fill economics"
        )
    if not _decimal_text_equal(shares, trade_fact["filled_size"]):
        raise ValueError(
            f"{lot_state} shares must equal source trade fact filled_size"
        )
    if not _decimal_text_equal(entry_price_avg, trade_fact["fill_price"]):
        raise ValueError(
            f"{lot_state} entry_price_avg must equal source trade fact fill_price"
        )
    return command_id, fact_id


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
        _validate_review_clearance_payload(
            conn=conn,
            current_state=current_state,
            event_type=event_type,
            payload=payload,
            command_id=command_id,
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


def _validate_review_clearance_payload(
    *,
    conn: sqlite3.Connection,
    current_state: str,
    event_type: str,
    payload: Optional[dict],
    command_id: str,
) -> None:
    if event_type == "REVIEW_CLEARED_NO_VENUE_EXPOSURE":
        _validate_review_no_exposure_payload(
            conn=conn,
            current_state=current_state,
            payload=payload,
            command_id=command_id,
        )
        return
    if event_type != "REVIEW_CLEARED_NO_VENUE_SIDE_EFFECT":
        return
    if current_state != "REVIEW_REQUIRED":
        raise ValueError("review clearance is only legal from REVIEW_REQUIRED")
    if not isinstance(payload, dict):
        raise ValueError("review clearance requires structured proof payload")
    if payload.get("reason") != "review_cleared_no_venue_side_effect":
        raise ValueError("review clearance payload requires reason=review_cleared_no_venue_side_effect")
    if payload.get("command_id") != command_id:
        raise ValueError("review clearance payload command_id must match appended command")
    if payload.get("side_effect_boundary_crossed") is not False:
        raise ValueError("review clearance requires side_effect_boundary_crossed=false")
    if payload.get("sdk_submit_attempted") is not False:
        raise ValueError("review clearance requires sdk_submit_attempted=false")
    proof_class = payload.get("proof_class")
    if proof_class != "pre_sdk_no_side_effect":
        raise ValueError("review clearance proof_class is not supported")
    required_predicates = payload.get("required_predicates")
    if not isinstance(required_predicates, dict):
        raise ValueError("review clearance requires required_predicates")
    required_true = (
        "no_venue_order_id",
        "no_final_submission_envelope",
        "no_raw_response",
        "no_signed_order",
        "no_order_facts",
        "no_trade_facts",
        "no_submit_side_effect_events",
        "review_required_reason_pre_sdk",
    )
    missing = [name for name in required_true if required_predicates.get(name) is not True]
    if missing:
        raise ValueError(f"review clearance predicates are not proven true: {missing}")
    actual_predicates = _actual_review_clearance_predicates(conn, command_id)
    actual_failures = [name for name, ok in actual_predicates.items() if not ok]
    if actual_failures:
        raise ValueError(f"review clearance DB predicates failed: {actual_failures}")
    source = payload.get("source_proof")
    if not isinstance(source, dict):
        raise ValueError("pre-SDK review clearance requires source_proof")
    for key in ("source_commit", "source_function", "source_reason", "decision_id"):
        if not str(source.get(key) or "").strip():
            raise ValueError(f"pre-SDK review clearance source_proof missing {key}")
    if source.get("source_reason") != "pre_submit_collateral_reservation_failed":
        raise ValueError("pre-SDK review clearance requires collateral source_reason")
    command_decision_id = _actual_command_decision_id(conn, command_id)
    if source.get("decision_id") != command_decision_id:
        raise ValueError("pre-SDK review clearance decision_id does not match command")
    if not _review_clearance_decision_log_pre_sdk_proven(conn, command_decision_id):
        raise ValueError("pre-SDK review clearance requires decision_log collateral proof")
    actual_reason = _actual_review_required_reason(conn, command_id)
    review_proof = payload.get("review_required_proof")
    if not isinstance(review_proof, dict):
        raise ValueError("pre-SDK review clearance requires review_required_proof")
    if review_proof.get("reason") != actual_reason:
        raise ValueError("review clearance review_required_proof reason does not match DB")


def _validate_review_no_exposure_payload(
    *,
    conn: sqlite3.Connection,
    current_state: str,
    payload: Optional[dict],
    command_id: str,
) -> None:
    if current_state != "REVIEW_REQUIRED":
        raise ValueError("review no-exposure clearance is only legal from REVIEW_REQUIRED")
    if not isinstance(payload, dict):
        raise ValueError("review no-exposure clearance requires structured proof payload")
    if payload.get("reason") != "review_cleared_no_venue_exposure":
        raise ValueError("review no-exposure clearance payload requires reason=review_cleared_no_venue_exposure")
    if payload.get("command_id") != command_id:
        raise ValueError("review no-exposure clearance payload command_id must match appended command")
    if payload.get("proof_class") != "venue_absence_no_exposure":
        raise ValueError("review no-exposure clearance proof_class is not supported")
    if payload.get("side_effect_boundary_crossed") != "unknown":
        raise ValueError("review no-exposure clearance requires side_effect_boundary_crossed=unknown")
    if payload.get("sdk_submit_attempted") != "unknown":
        raise ValueError("review no-exposure clearance requires sdk_submit_attempted=unknown")
    required_predicates = payload.get("required_predicates")
    if not isinstance(required_predicates, dict):
        raise ValueError("review no-exposure clearance requires required_predicates")
    required_true = (
        "no_venue_order_id",
        "no_final_submission_envelope",
        "no_raw_response",
        "no_signed_order",
        "no_order_facts",
        "no_trade_facts",
        "no_submit_side_effect_events",
        "review_required_reason_recovery_no_venue_order_id",
    )
    missing = [name for name in required_true if required_predicates.get(name) is not True]
    if missing:
        raise ValueError(f"review no-exposure predicates are not proven true: {missing}")
    actual_predicates = _actual_review_clearance_predicates(conn, command_id)
    actual_failures = [
        name
        for name, ok in actual_predicates.items()
        if name != "review_required_reason_pre_sdk" and not ok
    ]
    if actual_failures:
        raise ValueError(f"review no-exposure DB predicates failed: {actual_failures}")
    actual_reason = _actual_review_required_reason(conn, command_id)
    if actual_reason != "recovery_no_venue_order_id":
        raise ValueError("review no-exposure clearance only supports recovery_no_venue_order_id")
    venue_proof = payload.get("venue_absence_proof")
    if not isinstance(venue_proof, dict):
        raise ValueError("review no-exposure clearance requires venue_absence_proof")
    if venue_proof.get("owner_scope") != "authenticated_funder":
        raise ValueError("review no-exposure clearance requires authenticated_funder owner_scope")
    observed_at = _review_clearance_parse_utc(venue_proof.get("observed_at"))
    cleared_at = _review_clearance_parse_utc(payload.get("cleared_at"))
    if observed_at is None or cleared_at is None:
        raise ValueError("review no-exposure clearance requires observed_at and cleared_at")
    age_seconds = (cleared_at - observed_at).total_seconds()
    if age_seconds < -5 or age_seconds > 60:
        raise ValueError("review no-exposure clearance venue proof is stale")
    if venue_proof.get("open_orders_checked") is not True:
        raise ValueError("review no-exposure clearance requires open_orders_checked=true")
    if venue_proof.get("trades_checked") is not True:
        raise ValueError("review no-exposure clearance requires trades_checked=true")
    if venue_proof.get("open_orders_query_complete") is not True:
        raise ValueError("review no-exposure clearance requires open_orders_query_complete=true")
    if venue_proof.get("trades_query_complete") is not True:
        raise ValueError("review no-exposure clearance requires trades_query_complete=true")
    if not str(venue_proof.get("pagination_scope") or "").strip():
        raise ValueError("review no-exposure clearance requires pagination_scope")
    if int(venue_proof.get("matching_open_order_count", -1)) != 0:
        raise ValueError("review no-exposure clearance found matching open orders")
    if int(venue_proof.get("matching_trade_count", -1)) != 0:
        raise ValueError("review no-exposure clearance found matching trades")
    with _row_factory_as(conn, sqlite3.Row):
        command = conn.execute(
            """
            SELECT decision_id, market_id, token_id, side, price, size, created_at
            FROM venue_commands
            WHERE command_id = ?
            """,
            (command_id,),
        ).fetchone()
    if command is None:
        raise ValueError("review no-exposure clearance command is missing")
    window_start = _review_clearance_parse_utc(venue_proof.get("time_window_start"))
    window_end = _review_clearance_parse_utc(venue_proof.get("time_window_end"))
    command_created_at = _review_clearance_parse_utc(command["created_at"])
    if window_start is None or window_end is None or command_created_at is None:
        raise ValueError("review no-exposure clearance requires a parseable command-to-read window")
    if window_start > command_created_at or window_end < observed_at:
        raise ValueError("review no-exposure clearance time window does not cover command through venue read")
    for key in ("decision_id", "market_id", "token_id", "side"):
        if str(venue_proof.get(key) or "") != str(command[key] or ""):
            raise ValueError(f"review no-exposure venue_absence_proof {key} does not match command")
    for key in ("price", "size"):
        try:
            proof_value = Decimal(str(venue_proof.get(key)))
            command_value = Decimal(str(command[key]))
        except (InvalidOperation, TypeError):
            raise ValueError(f"review no-exposure venue_absence_proof {key} is invalid")
        if proof_value != command_value:
            raise ValueError(f"review no-exposure venue_absence_proof {key} does not match command")
    source = payload.get("source_proof")
    if not isinstance(source, dict):
        raise ValueError("review no-exposure clearance requires source_proof")
    for key in ("source_commit", "source_function", "source_reason"):
        if not str(source.get(key) or "").strip():
            raise ValueError(f"review no-exposure source_proof missing {key}")
    if source.get("source_reason") != "recovery_no_venue_order_id":
        raise ValueError("review no-exposure source_reason must be recovery_no_venue_order_id")
    review_proof = payload.get("review_required_proof")
    if not isinstance(review_proof, dict):
        raise ValueError("review no-exposure clearance requires review_required_proof")
    if review_proof.get("reason") != actual_reason:
        raise ValueError("review no-exposure review_required_proof reason does not match DB")


def _review_clearance_parse_utc(value: object) -> datetime.datetime | None:
    if value in (None, ""):
        return None
    try:
        text = str(value)
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        parsed = datetime.datetime.fromisoformat(text)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime.timezone.utc)
    return parsed.astimezone(datetime.timezone.utc)


def _review_clearance_table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table,),
    ).fetchone()
    return row is not None


def _review_clearance_fact_count(
    conn: sqlite3.Connection,
    table: str,
    command_id: str,
) -> int:
    if not _review_clearance_table_exists(conn, table):
        return 0
    row = conn.execute(
        f"SELECT COUNT(*) AS count FROM {table} WHERE command_id = ?",
        (command_id,),
    ).fetchone()
    if row is None:
        return 0
    if isinstance(row, sqlite3.Row):
        return int(row["count"] or 0)
    return int(row[0] or 0)


def _review_clearance_json_dict(raw: object) -> dict:
    if raw in (None, ""):
        return {}
    try:
        parsed = json.loads(str(raw))
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _actual_command_decision_id(
    conn: sqlite3.Connection,
    command_id: str,
) -> str:
    with _row_factory_as(conn, sqlite3.Row):
        row = conn.execute(
            "SELECT decision_id FROM venue_commands WHERE command_id = ?",
            (command_id,),
        ).fetchone()
    if row is None:
        return ""
    return str(row["decision_id"] or "")


def _review_clearance_decision_log_pre_sdk_proven(
    conn: sqlite3.Connection,
    decision_id: str,
) -> bool:
    if not decision_id or not _review_clearance_table_exists(conn, "decision_log"):
        return False
    with _row_factory_as(conn, sqlite3.Row):
        rows = conn.execute(
            """
            SELECT artifact_json
            FROM decision_log
            WHERE artifact_json LIKE ?
            ORDER BY id DESC
            LIMIT 20
            """,
            (f"%{decision_id}%",),
        ).fetchall()
    for row in rows:
        artifact = _review_clearance_json_dict(row["artifact_json"])
        for case in artifact.get("no_trade_cases") or []:
            if not isinstance(case, dict) or case.get("decision_id") != decision_id:
                continue
            reasons = case.get("rejection_reasons") or []
            if isinstance(reasons, str):
                reasons = [reasons]
            reason_text = " | ".join(str(reason) for reason in reasons)
            return (
                case.get("rejection_stage") == "EXECUTION_FAILED"
                and "execution_intent_rejected:" in reason_text
                and any(marker in reason_text for marker in _PRE_SDK_COLLATERAL_REASON_MARKERS)
            )
    return False


def _actual_review_clearance_predicates(
    conn: sqlite3.Connection,
    command_id: str,
) -> dict[str, bool]:
    with _row_factory_as(conn, sqlite3.Row):
        command = conn.execute(
            """
            SELECT venue_order_id, envelope_id
            FROM venue_commands
            WHERE command_id = ?
            """,
            (command_id,),
        ).fetchone()
        events = conn.execute(
            """
            SELECT event_type, payload_json
            FROM venue_command_events
            WHERE command_id = ?
            ORDER BY sequence_no
            """,
            (command_id,),
        ).fetchall()
        envelope = None
        if command is not None and command["envelope_id"]:
            envelope = conn.execute(
                """
                SELECT raw_response_json, signed_order_blob, signed_order_hash
                FROM venue_submission_envelopes
                WHERE envelope_id = ?
                """,
                (command["envelope_id"],),
            ).fetchone()
    final_envelope_ids: list[str] = []
    unsafe_event_types = {
        "POST_ACKED",
        "SUBMIT_ACKED",
        "SUBMIT_UNKNOWN",
        "SUBMIT_TIMEOUT_UNKNOWN",
        "CLOSED_MARKET_UNKNOWN",
        "PARTIAL_FILL_OBSERVED",
        "FILL_CONFIRMED",
    }
    observed_event_types = set()
    latest_review_required_reason = ""
    for event in events:
        event_type = str(event["event_type"] or "")
        observed_event_types.add(event_type)
        payload = {}
        raw = event["payload_json"]
        if raw:
            try:
                parsed = json.loads(str(raw))
            except json.JSONDecodeError:
                parsed = {}
            if isinstance(parsed, dict):
                payload = parsed
        final_id = str(payload.get("final_submission_envelope_id") or "").strip()
        if final_id:
            final_envelope_ids.append(final_id)
        if event_type == "REVIEW_REQUIRED":
            latest_review_required_reason = str(payload.get("reason") or "").strip()
    return {
        "no_venue_order_id": command is not None and not str(command["venue_order_id"] or "").strip(),
        "no_final_submission_envelope": not final_envelope_ids,
        "no_raw_response": envelope is None or not str(envelope["raw_response_json"] or "").strip(),
        "no_signed_order": (
            envelope is None
            or (
                envelope["signed_order_blob"] in (None, b"", "")
                and not str(envelope["signed_order_hash"] or "").strip()
            )
        ),
        "no_order_facts": _review_clearance_fact_count(conn, "venue_order_facts", command_id) == 0,
        "no_trade_facts": _review_clearance_fact_count(conn, "venue_trade_facts", command_id) == 0,
        "no_submit_side_effect_events": not (observed_event_types & unsafe_event_types),
        "review_required_reason_pre_sdk": (
            latest_review_required_reason in _PRE_SDK_REVIEW_REQUIRED_REASONS
        ),
    }


def _actual_review_required_reason(
    conn: sqlite3.Connection,
    command_id: str,
) -> str:
    with _row_factory_as(conn, sqlite3.Row):
        row = conn.execute(
            """
            SELECT payload_json
            FROM venue_command_events
            WHERE command_id = ?
              AND event_type = 'REVIEW_REQUIRED'
            ORDER BY sequence_no DESC
            LIMIT 1
            """,
            (command_id,),
        ).fetchone()
    if row is None or not row["payload_json"]:
        return ""
    try:
        payload = json.loads(str(row["payload_json"]))
    except json.JSONDecodeError:
        return ""
    if not isinstance(payload, dict):
        return ""
    return str(payload.get("reason") or "").strip()


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
        if state == "FAILED":
            _rollback_optimistic_lots_for_failed_trade(
                conn,
                trade_id=trade_id,
                failed_trade_fact_id=fact_id,
                state_changed_at=observed_at_s,
            )
    return fact_id


def append_position_lot(
    conn: sqlite3.Connection,
    *,
    position_id: int,
    state: str,
    shares: int | float | str | Decimal,
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
    shares_text = _finite_decimal_text(shares)
    venue_timestamp_s = (
        _validate_observed_at(venue_timestamp) if venue_timestamp is not None else None
    )
    source_command_id, source_trade_fact_id = _assert_position_lot_trade_fact_authority(
        conn,
        lot_state=state,
        shares=shares_text,
        entry_price_avg=entry_price_avg,
        source_command_id=source_command_id,
        source_trade_fact_id=source_trade_fact_id,
    )
    payload_for_hash = raw_payload_json if raw_payload_json is not None else {
        "position_id": position_id,
        "state": state,
        "shares": shares_text,
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
                shares_text,
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
    existing = conn.execute(
        """
        SELECT lot_id
          FROM position_lots
         WHERE position_id = ?
           AND state = 'QUARANTINED'
           AND source_trade_fact_id = ?
         ORDER BY lot_id DESC
         LIMIT 1
        """,
        (lot["position_id"], failed_trade_fact_id),
    ).fetchone()
    if existing is not None:
        return int(existing[0])
    return append_position_lot(
        conn,
        position_id=int(lot["position_id"]),
        state="QUARANTINED",
        shares=str(lot["shares"]),
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
