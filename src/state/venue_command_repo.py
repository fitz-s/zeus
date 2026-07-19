# Created: 2026-04-26
# Last reused/audited: 2026-07-19
# Authority basis: docs/operations/task_2026-05-08_object_invariance_wave27/PLAN.md
#                  + docs/archive/2026-Q2/task_2026-05-15_live_order_e2e_goal/LIVE_ORDER_E2E_GOAL_PLAN.md
#                  + docs/archive/2026-Q2/task_2026-05-17_live_order_survival/LIVE_ORDER_SURVIVAL_PLAN.md S5
"""Durable command journal — append-only repo API for venue_commands / venue_command_events.

Public API:
  insert_command(conn, *, ...) -> None
  append_event(conn, *, command_id, event_type, occurred_at, payload=None) -> str
  get_command(conn, command_id) -> Optional[dict]
  find_unresolved_commands(conn) -> Iterable[dict]
  find_command_by_idempotency_key(conn, key) -> Optional[dict]
  find_unknown_command_by_economic_intent(conn, *, ...) -> Optional[dict]
  resolve_position_lot_id_for_command(conn, command) -> Optional[int]
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
import os
import sqlite3
import uuid
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable, Iterator, Mapping, Optional

from src.architecture.decorators import capability, protects
from src.contracts.freshness_registry import FreshnessLevel, registry as _freshness_registry

UNRESOLVED_SIDE_EFFECT_STATES: tuple[str, ...] = (
    "SUBMIT_UNKNOWN_SIDE_EFFECT",
    "UNKNOWN",
    "REVIEW_REQUIRED",
)


def _strict_live_entry_q_version_required() -> bool:
    return (
        str(os.environ.get("ZEUS_ENTRY_Q_VERSION_STRICT", "")).lower()
        in {"1", "true", "yes", "on"}
        or str(os.environ.get("ZEUS_MODE", "")).strip().lower() == "live"
        or str(os.environ.get("XPC_SERVICE_NAME", "")) == "com.zeus.live-trading"
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
    ("INTENT_CREATED", "SUBMIT_REJECTED"):    "SUBMIT_REJECTED",
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
    ("SUBMITTING", "EXPIRED"):                "EXPIRED",
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
    ("FILLED", "PARTIAL_FILL_OBSERVED"):      "PARTIAL",
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
    ("REVIEW_REQUIRED", "REVIEW_CLEARED_VENUE_ORDER_LIVE"):     "ACKED",
    ("REVIEW_REQUIRED", "PARTIAL_FILL_OBSERVED"):                "PARTIAL",
    ("REVIEW_REQUIRED", "FILL_CONFIRMED"):                      "FILLED",
}

_PROVENANCE_SOURCES = frozenset(
    {"REST", "WS_USER", "WS_MARKET", "DATA_API", "CHAIN", "OPERATOR", "FAKE_VENUE"}
)
_ENTRY_SUBMIT_REQUIRED_COMPONENTS = frozenset(
    {"entry_economics", "entry_actionable_certificate"}
)
_ENTRY_SUBMIT_ECONOMICS_DETAIL_FIELDS = (
    "q_live",
    "q_lcb_5pct",
    "expected_edge",
    "min_entry_price",
    "limit_price",
    "submit_edge",
    "expected_profit_usd",
    "min_expected_profit_usd",
    "submit_edge_density",
    "min_submit_edge_density",
    "shares",
    "qkernel_side",
)
_PRE_SDK_REVIEW_REQUIRED_REASONS = frozenset({
    "pre_submit_collateral_reservation_failed",
    # Legacy pre-fix commands could fail before SDK submission, remain in
    # SUBMITTING, and then be moved to REVIEW_REQUIRED by recovery.
    "recovery_no_venue_order_id",
})
_NO_VENUE_EXPOSURE_REVIEW_REASONS = frozenset({
    "recovery_no_venue_order_id",
    "recovery_no_venue_order_id_lookup_unavailable",
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
    }
)  # HEARTBEAT_CANCEL_SUSPECTED removed 2026-06-29: 0 live rows, no writer (dead value).
# DB CHECK in db.py still permits it; it is narrowed out in the CHECK-narrowing step.
_TERMINAL_NO_RESTING_ORDER_FACT_STATES = frozenset(
    {"MATCHED", "CANCEL_CONFIRMED", "EXPIRED", "VENUE_WIPED"}
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
        # T5 (docs/rebuild/quarantine_excision_2026-07-11.md): 'QUARANTINED'
        # removed from the WRITE-time contract — rollback_optimistic_lot_for_
        # failed_trade now appends ECONOMICALLY_CLOSED_OPTIMISTIC. The T5
        # schema migration has run and the DB CHECK no longer admits the
        # literal.
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


def _decimal_text_is_zero(value: Any) -> bool:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return False
    return parsed.is_finite() and parsed == 0


def _decimal_or_none(value: Any) -> Decimal | None:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return parsed if parsed.is_finite() else None


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


def _prior_terminal_no_resting_order_fact(
    conn: sqlite3.Connection,
    *,
    venue_order_id: str,
    command_id: str,
) -> sqlite3.Row | None:
    row = conn.execute(
        """
        SELECT fact_id, state, remaining_size, matched_size
          FROM venue_order_facts
         WHERE venue_order_id = ?
           AND command_id = ?
           AND state IN ('MATCHED', 'CANCEL_CONFIRMED', 'EXPIRED', 'VENUE_WIPED')
         ORDER BY local_sequence DESC, fact_id DESC
         LIMIT 1
        """,
        (venue_order_id, command_id),
    ).fetchone()
    if row is None:
        return None
    if str(row["state"] or "") == "MATCHED" and not _decimal_text_is_zero(row["remaining_size"]):
        return None
    return row


def _terminal_partial_correction_proven(
    conn: sqlite3.Connection,
    *,
    venue_order_id: str,
    command_id: str,
    state: str,
    remaining_size: str | None,
    matched_size: str | None,
    raw_payload_json: Any,
) -> bool:
    """Allow only an exact terminal-partial correction of a false full-fill fact."""

    if (
        state != "PARTIALLY_MATCHED"
        or not _decimal_text_is_zero(remaining_size)
        or not _positive_finite_decimal_text(matched_size)
        or not isinstance(raw_payload_json, Mapping)
        or raw_payload_json.get("proof_class") != "terminal_partial_order_fact"
    ):
        return False
    proof_fact_id = raw_payload_json.get("latest_order_fact_id")
    if proof_fact_id in (None, ""):
        proof_fact = conn.execute(
            """
            SELECT state, remaining_size, matched_size
              FROM venue_order_facts
             WHERE command_id = ?
               AND venue_order_id = ?
               AND state = 'MATCHED'
             ORDER BY local_sequence DESC, fact_id DESC
             LIMIT 1
            """,
            (command_id, venue_order_id),
        ).fetchone()
    else:
        proof_fact = conn.execute(
            """
            SELECT state, remaining_size, matched_size
              FROM venue_order_facts
             WHERE fact_id = ?
               AND command_id = ?
               AND venue_order_id = ?
            """,
            (proof_fact_id, command_id, venue_order_id),
        ).fetchone()
    if (
        proof_fact is None
        or str(proof_fact["state"] or "") != "MATCHED"
        or not _decimal_text_is_zero(proof_fact["remaining_size"])
        or not _decimal_text_equal(proof_fact["matched_size"], matched_size)
    ):
        return False
    predicates = raw_payload_json.get("required_predicates")
    required = {
        "terminal_order_remainder_zero",
        "canonical_trade_facts_match_terminal_order_fact",
        "cumulative_fill_below_requested_size",
    }
    if not isinstance(predicates, Mapping) or any(
        predicates.get(name) is not True for name in required
    ):
        return False
    command = conn.execute(
        """
        SELECT state, intent_kind, side, size, venue_order_id
          FROM venue_commands
         WHERE command_id = ?
        """,
        (command_id,),
    ).fetchone()
    if (
        command is None
        or str(command["state"] or "").upper() != "PARTIAL"
        or str(command["intent_kind"] or "").upper() != "ENTRY"
        or str(command["side"] or "").upper() != "BUY"
        or str(command["venue_order_id"] or "") != venue_order_id
    ):
        return False
    requested = _decimal_or_none(command["size"])
    matched = _decimal_or_none(matched_size)
    return (
        requested is not None
        and requested > 0
        and matched is not None
        and matched > 0
        and requested - matched > Decimal("0.01")
    )


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
                   -- T5 (docs/rebuild/quarantine_excision_2026-07-11.md):
                   -- rollback_optimistic_lot_for_failed_trade appends
                   -- ECONOMICALLY_CLOSED_OPTIMISTIC now, not QUARANTINED —
                   -- this idempotency check matches the same state.
                   SELECT 1
                     FROM position_lots reversed
                     JOIN venue_trade_facts failed
                       ON failed.trade_fact_id = reversed.source_trade_fact_id
                    WHERE reversed.position_id = lot.position_id
                      AND reversed.state = 'ECONOMICALLY_CLOSED_OPTIMISTIC'
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


def resolve_position_lot_id_for_command(
    conn: sqlite3.Connection,
    command: Mapping[str, Any],
) -> int | None:
    """Resolve the integer lot identity for commands keyed by runtime ids.

    Live venue commands store a runtime position id for operator correlation,
    while the current ``position_lots`` schema still keys exposure by the
    integer ``trade_decisions.trade_id``. Prefer the explicit
    ``runtime_trade_id`` bridge; accept numeric compatibility fields only when
    they point at a compatible ``trade_decisions`` row.
    """

    for key in ("position_id", "decision_id"):
        parsed = _trade_decision_id_for_runtime_id(conn, command.get(key))
        if parsed is not None:
            return parsed

    position_id = command.get("position_id")
    parsed_position_id = _parse_positive_int(position_id)
    if parsed_position_id is not None and _trade_decision_id_is_compatible(
        conn,
        parsed_position_id,
        runtime_trade_id=position_id,
    ):
        return parsed_position_id

    decision_id = command.get("decision_id")
    parsed_decision_id = _parse_positive_int(decision_id)
    if parsed_decision_id is not None and _trade_decision_id_is_compatible(
        conn,
        parsed_decision_id,
        runtime_trade_id=position_id,
    ):
        return parsed_decision_id
    return None


def _parse_positive_int(value: Any) -> int | None:
    try:
        parsed = int(str(value))
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _trade_decision_id_for_runtime_id(
    conn: sqlite3.Connection,
    runtime_trade_id: Any,
) -> int | None:
    runtime_id = str(runtime_trade_id or "").strip()
    if not runtime_id:
        return None
    try:
        row = conn.execute(
            """
            SELECT trade_id
              FROM trade_decisions
             WHERE runtime_trade_id = ?
             ORDER BY trade_id DESC
             LIMIT 1
            """,
            (runtime_id,),
        ).fetchone()
    except Exception:
        return None
    if row is None:
        return None
    return _parse_positive_int(row["trade_id"] if hasattr(row, "keys") else row[0])


def _trade_decision_id_is_compatible(
    conn: sqlite3.Connection,
    trade_decision_id: int,
    *,
    runtime_trade_id: Any,
) -> bool:
    try:
        row = conn.execute(
            """
            SELECT runtime_trade_id
              FROM trade_decisions
             WHERE trade_id = ?
             LIMIT 1
            """,
            (int(trade_decision_id),),
        ).fetchone()
    except Exception:
        return False
    if row is None:
        return False
    row_runtime = row["runtime_trade_id"] if hasattr(row, "keys") else row[0]
    row_runtime_s = str(row_runtime or "").strip()
    expected_runtime_s = str(runtime_trade_id or "").strip()
    return not row_runtime_s or not expected_runtime_s or row_runtime_s == expected_runtime_s


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


def record_position_decision_attribution(
    conn: sqlite3.Connection,
    *,
    position_id: str,
    command_id: str,
    decision_certificate_hash: str,
    intent_kind: str,
    created_at: str,
) -> None:
    """Append the permanent position -> decision-certificate-hash fact.

    LX-E packet (docs/rebuild/local_ledger_excision_2026-07-12.md Round-2 delta
    §(c)): the certificate link that used to be INFERRED at settlement time from
    the (condition_id, direction) -> latest edli_live_profit_audit row (14 such
    pairs are ambiguous) is instead recorded HERE, at command creation, when the
    real decision certificate hash is known with certainty.

    Append-only: UNIQUE(position_id) + ON CONFLICT DO NOTHING — a position's first
    attribution fact is never overwritten by a later call. Idempotent no-op on a
    retried/duplicate call for the same position. ``ensure_table`` is called here
    (not only at DB init) so this self-heals on any trade-shaped connection that
    predates the LX-E migration (e.g. test fixtures built via ``init_schema()``
    rather than ``init_schema_trade_only``).
    """
    from src.state.schema.position_decision_attribution_schema import ensure_table

    ensure_table(conn)
    conn.execute(
        """
        INSERT INTO position_decision_attribution (
            attribution_id, position_id, command_id, decision_certificate_hash,
            resolution, resolution_reason, source, intent_kind, created_at,
            schema_version
        ) VALUES (?, ?, ?, ?, 'ATTRIBUTED', NULL, 'LIVE_DECISION', ?, ?, 1)
        ON CONFLICT(position_id) DO NOTHING
        """,
        (_new_id(), position_id, command_id, decision_certificate_hash, intent_kind, created_at),
    )


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
    q_version: str | None = None,
    snapshot_checked_at: str | datetime.datetime | None = None,
    expected_min_tick_size=None,
    expected_min_order_size=None,
    expected_neg_risk: bool | None = None,
    venue_order_id: str | None = None,
    reason: str | None = None,
    decision_certificate_hash: str | None = None,
) -> None:
    """INSERT a new venue_commands row in INTENT_CREATED state.

    Atomically appends the INTENT_CREATED event in the same transaction,
    then updates last_event_id on the command row.

    Raises sqlite3.IntegrityError if idempotency_key already exists.
    Raises ValueError if intent_kind / side are not in their closed enum
    grammar (post-critic MAJOR-1: pre-fix the repo persisted any string;
    now it rejects "GIBBERISH" / "LONG" / etc. before INSERT). Defers the
    full enum object to command_bus to avoid a circular import.

    q_version (SCH-W1.2-ORDER-STATE): the forecast_posteriors.posterior_identity_hash
    of the q this command's decision was made against, write-once at creation.
    Required for live-mode ENTRY commands at this repo boundary; nullable only for
    non-entry commands, offline fixtures/replay, and direct recovery/backfill
    writes that intentionally bypass this function. Never re-stamped after insert.

    decision_certificate_hash (LX-E packet, 2026-07-13): when given, appends the
    permanent position -> decision_certificate_hash fact to
    position_decision_attribution in the SAME transaction as this command insert
    (record_position_decision_attribution). Only ENTRY commands carry a resolvable
    ActionableTradeCertificate hash at this repo boundary; EXIT/other commands pass
    None (no row is written for those — attribution is a property of the
    position's entry decision, not every command against it).
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
    q_version_value = q_version.strip() or None if isinstance(q_version, str) else q_version
    if (
        intent_kind == _IntentKind.ENTRY.value
        and q_version_value is None
        and _strict_live_entry_q_version_required()
    ):
        raise ValueError("ENTRY venue command requires non-empty q_version")
    _assert_snapshot_gate(
        conn,
        snapshot_id=snapshot_id_value,
        token_id=token_id,
        side=side,
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
                review_required_reason, q_version
            ) VALUES (
                :command_id, :snapshot_id, :envelope_id, :position_id, :decision_id, :idempotency_key,
                :intent_kind, :market_id, :token_id, :side, :size, :price,
                :venue_order_id, 'INTENT_CREATED', NULL, :created_at, :created_at,
                NULL, :q_version
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
                "q_version": q_version_value,
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
        if decision_certificate_hash:
            record_position_decision_attribution(
                conn,
                position_id=position_id,
                command_id=command_id,
                decision_certificate_hash=decision_certificate_hash,
                intent_kind=intent_kind,
                created_at=created_at,
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
    side: str,
    price: float,
    size: float,
    checked_at: str | datetime.datetime | None,
    expected_min_tick_size,
    expected_min_order_size,
    expected_neg_risk: bool | None,
) -> None:
    """U1 single insertion-point freshness/tradability gate."""

    from src.contracts.executable_market_snapshot import (
        StaleMarketSnapshotError,
        assert_snapshot_executable,
    )
    from src.state.snapshot_repo import get_snapshot, snapshot_is_invalidated

    if not isinstance(snapshot_id, str) or not snapshot_id.strip():
        raise StaleMarketSnapshotError("venue command requires executable market snapshot_id")
    snapshot_id = snapshot_id.strip()
    try:
        snapshot = get_snapshot(conn, snapshot_id)
    except sqlite3.OperationalError as exc:
        raise StaleMarketSnapshotError(
            "executable_market_snapshots table is unavailable; cannot validate venue command"
        ) from exc
    checked_at = _coerce_snapshot_checked_at(checked_at)
    if snapshot is not None and snapshot_is_invalidated(conn, snapshot, checked_at=checked_at):
        raise StaleMarketSnapshotError(
            f"ExecutableMarketSnapshot {snapshot.snapshot_id} was invalidated before submit"
        )
    assert_snapshot_executable(
        snapshot,
        token_id=token_id,
        side=side,
        price=price,
        size=size,
        now=checked_at,
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
                "SELECT state, intent_kind FROM venue_commands WHERE command_id = ?",
                (command_id,),
            ).fetchone()
        if row is None:
            raise ValueError(f"Unknown command_id: {command_id!r}")

        current_state = row[0]
        intent_kind = row[1]
        key = (current_state, event_type)
        if key not in _TRANSITIONS:
            raise ValueError(
                f"Illegal command-event grammar transition: "
                f"state={current_state!r} event={event_type!r}"
            )
        _validate_entry_submit_payload(
            intent_kind=intent_kind,
            event_type=event_type,
            payload=payload,
        )
        _validate_review_clearance_payload(
            conn=conn,
            current_state=current_state,
            event_type=event_type,
            payload=payload,
            command_id=command_id,
        )
        _validate_terminal_partial_command_correction_payload(
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
            from src.state.collateral_ledger import (
                _CONVERT_STATES as _COLLATERAL_CONVERT_STATES,
                convert_reservation_on_fill,
                release_reservation_for_command_state,
            )
            from src.execution.exit_safety import release_exit_mutex_for_command_state

            # SCH-W1.1-CAS-LEDGER terminalization-centrality invariant: this is
            # the SOLE seam where a reservation-bearing command reaches a
            # terminal state and converts/releases collateral. Fill-class
            # terminals (may carry a nonzero matched_size, per _CONVERT_STATES)
            # convert the filled portion and release the remainder in one
            # idempotent write; the remaining zero-fill terminals use the
            # existing unconditional release.
            if str(state_after).upper() in _COLLATERAL_CONVERT_STATES:
                convert_reservation_on_fill(conn, command_id, state_after)
            else:
                release_reservation_for_command_state(conn, command_id, state_after)
            release_exit_mutex_for_command_state(conn, command_id, state_after)
        elif state_after == "REVIEW_REQUIRED":
            # REVIEW_REQUIRED remains a durable proof/recovery blocker for new
            # replacement sells, but it must not keep the short-lived exit
            # mutex held across restarts. The venue command row itself owns the
            # unresolved-side-effect guard.
            from src.execution.exit_safety import release_exit_mutex_for_command_state

            release_exit_mutex_for_command_state(conn, command_id, state_after)

    return event_id


def _validate_entry_submit_payload(
    *,
    intent_kind: str,
    event_type: str,
    payload: Optional[dict],
) -> None:
    if intent_kind != "ENTRY" or event_type != "SUBMIT_REQUESTED":
        return
    if not isinstance(payload, dict):
        raise ValueError("ENTRY SUBMIT_REQUESTED requires execution_capability payload")
    capability = payload.get("execution_capability")
    if not isinstance(capability, dict):
        raise ValueError("ENTRY SUBMIT_REQUESTED requires execution_capability")
    if capability.get("allowed") is not True:
        raise ValueError("ENTRY SUBMIT_REQUESTED requires allowed execution_capability")
    components = capability.get("components")
    if not isinstance(components, list):
        raise ValueError("ENTRY SUBMIT_REQUESTED requires execution_capability.components")
    by_name = {
        str(component.get("component") or ""): component
        for component in components
        if isinstance(component, dict)
    }
    missing = sorted(_ENTRY_SUBMIT_REQUIRED_COMPONENTS.difference(by_name))
    if missing:
        raise ValueError(
            "ENTRY SUBMIT_REQUESTED missing live submit proof components: "
            + ",".join(missing)
        )
    for name in sorted(_ENTRY_SUBMIT_REQUIRED_COMPONENTS):
        component = by_name[name]
        if component.get("allowed") is not True:
            raise ValueError(f"ENTRY SUBMIT_REQUESTED {name} component is not allowed")
    economics = by_name["entry_economics"]
    details = economics.get("details")
    if not isinstance(details, dict):
        raise ValueError("ENTRY SUBMIT_REQUESTED entry_economics requires details")
    detail_missing = [
        field
        for field in _ENTRY_SUBMIT_ECONOMICS_DETAIL_FIELDS
        if details.get(field) in (None, "")
    ]
    if detail_missing:
        raise ValueError(
            "ENTRY SUBMIT_REQUESTED entry_economics missing details: "
            + ",".join(detail_missing)
        )


def _validate_terminal_partial_command_correction_payload(
    *,
    conn: sqlite3.Connection,
    current_state: str,
    event_type: str,
    payload: Optional[dict],
    command_id: str,
) -> None:
    """Allow a false full-fill command to return to proven terminal PARTIAL."""

    if event_type != "PARTIAL_FILL_OBSERVED" or current_state not in {
        "FILLED",
        "REVIEW_REQUIRED",
    }:
        return
    if not isinstance(payload, dict):
        raise ValueError("terminal partial command correction requires proof payload")
    if (
        payload.get("reason") != "terminal_partial_order_fact_corrected"
        or payload.get("proof_class") != "terminal_partial_order_fact"
        or payload.get("command_id") != command_id
    ):
        raise ValueError("terminal partial command correction proof is invalid")
    required = payload.get("required_predicates")
    names = (
        "terminal_order_remainder_zero",
        "canonical_trade_facts_match_terminal_order_fact",
        "cumulative_fill_below_requested_size",
    )
    if not isinstance(required, Mapping) or any(
        required.get(name) is not True for name in names
    ):
        raise ValueError("terminal partial command correction predicates are incomplete")
    with _row_factory_as(conn, sqlite3.Row):
        command = conn.execute(
            """
            SELECT intent_kind, side, size, venue_order_id
              FROM venue_commands
             WHERE command_id = ?
            """,
            (command_id,),
        ).fetchone()
        proof_fact_id = payload.get("latest_order_fact_id")
        if proof_fact_id in (None, ""):
            proof_order = conn.execute(
                """
                SELECT state, remaining_size, matched_size
                  FROM venue_order_facts
                 WHERE command_id = ?
                   AND venue_order_id = ?
                   AND state IN ('MATCHED', 'PARTIALLY_MATCHED', 'PARTIAL')
                 ORDER BY local_sequence DESC, fact_id DESC
                 LIMIT 1
                """,
                (command_id, str(payload.get("venue_order_id") or "")),
            ).fetchone()
        else:
            proof_order = conn.execute(
                """
                SELECT state, remaining_size, matched_size
                  FROM venue_order_facts
                 WHERE fact_id = ?
                   AND command_id = ?
                   AND venue_order_id = ?
                """,
                (
                    proof_fact_id,
                    command_id,
                    str(payload.get("venue_order_id") or ""),
                ),
            ).fetchone()
    venue_order_id = str(payload.get("venue_order_id") or "")
    if command is None:
        raise ValueError("terminal partial command correction command is missing")
    if (
        str(command["intent_kind"] or "").upper() != "ENTRY"
        or str(command["side"] or "").upper() != "BUY"
        or str(command["venue_order_id"] or "") != venue_order_id
    ):
        raise ValueError("terminal partial command correction command identity does not match")
    if proof_order is None:
        raise ValueError("terminal partial command correction order fact is missing")
    if str(proof_order["state"] or "").upper() not in {
        "MATCHED",
        "PARTIALLY_MATCHED",
        "PARTIAL",
    }:
        raise ValueError("terminal partial command correction order state is invalid")
    if not _decimal_text_is_zero(proof_order["remaining_size"]):
        raise ValueError("terminal partial command correction order remainder is not zero")
    if not _decimal_text_equal(
        proof_order["matched_size"], payload.get("filled_size")
    ):
        raise ValueError(
            "terminal partial command correction order size does not match: "
            f"order={proof_order['matched_size']} trade={payload.get('filled_size')}"
        )
    requested = _decimal_or_none(command["size"])
    filled = _decimal_or_none(payload.get("filled_size"))
    if (
        requested is None
        or filled is None
        or requested <= 0
        or filled <= 0
        or requested - filled <= Decimal("0.01")
        or not _decimal_text_equal(requested, payload.get("requested_size"))
    ):
        raise ValueError("terminal partial command correction is not a short fill")
    actual = _actual_review_confirmed_fill_predicates(conn, command_id, payload)
    if not (
        actual.get("positive_trade_facts")
        and actual.get("matched_order_fact_positive")
    ):
        raise ValueError("terminal partial command correction trade proof does not match")


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
    if event_type == "REVIEW_CLEARED_VENUE_ORDER_LIVE":
        _validate_review_venue_order_live_payload(
            conn=conn,
            current_state=current_state,
            payload=payload,
            command_id=command_id,
        )
        return
    if current_state == "REVIEW_REQUIRED" and event_type == "FILL_CONFIRMED":
        _validate_review_confirmed_fill_payload(
            conn=conn,
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


def _validate_review_venue_order_live_payload(
    *,
    conn: sqlite3.Connection,
    current_state: str,
    payload: Optional[dict],
    command_id: str,
) -> None:
    if current_state != "REVIEW_REQUIRED":
        raise ValueError("review live-order clearance is only legal from REVIEW_REQUIRED")
    if not isinstance(payload, dict):
        raise ValueError("review live-order clearance requires structured proof payload")
    if payload.get("reason") != "review_cleared_venue_order_live":
        raise ValueError("review live-order clearance payload requires reason=review_cleared_venue_order_live")
    if payload.get("command_id") != command_id:
        raise ValueError("review live-order clearance payload command_id must match appended command")
    proof_class = payload.get("proof_class")
    if proof_class not in {
        "cancel_unknown_venue_order_live",
        "acked_submit_venue_order_live",
        "recovery_no_venue_order_id_live_order",
    }:
        raise ValueError("review live-order clearance proof_class is not supported")
    if proof_class == "cancel_unknown_venue_order_live":
        if payload.get("side_effect_boundary_crossed") != "unknown":
            raise ValueError("review live-order clearance requires side_effect_boundary_crossed=unknown")
        if payload.get("sdk_cancel_attempted") != "unknown":
            raise ValueError("review live-order clearance requires sdk_cancel_attempted=unknown")
    elif proof_class == "acked_submit_venue_order_live":
        if payload.get("side_effect_boundary_crossed") is not True:
            raise ValueError("post-ACK live-order clearance requires side_effect_boundary_crossed=true")
        if payload.get("sdk_submit_attempted") is not True:
            raise ValueError("post-ACK live-order clearance requires sdk_submit_attempted=true")
    else:
        if payload.get("side_effect_boundary_crossed") is not True:
            raise ValueError("no-venue live-order clearance requires side_effect_boundary_crossed=true")
        if payload.get("sdk_submit_attempted") is not True:
            raise ValueError("no-venue live-order clearance requires sdk_submit_attempted=true")
    required_predicates = payload.get("required_predicates")
    if not isinstance(required_predicates, dict):
        raise ValueError("review live-order clearance requires required_predicates")
    if proof_class == "cancel_unknown_venue_order_live":
        required_true = (
            "latest_event_is_cancel_replace_blocked",
            "semantic_cancel_status_cancel_unknown",
            "requires_m5_reconcile",
            "venue_order_id_present",
            "venue_order_id_matches_point_read",
            "point_order_status_live",
            "point_order_matched_size_not_positive",
            "no_trade_facts",
        )
    else:
        if proof_class == "acked_submit_venue_order_live":
            required_true = (
                "latest_event_is_review_required",
                "review_reason_post_ack_persistence_failure",
                "venue_order_id_present",
                "venue_order_id_matches_live_proof",
                "authenticated_live_order_seen",
                "latest_order_fact_live",
                "point_order_matched_size_not_positive",
                "no_trade_facts",
            )
        else:
            required_true = (
                "latest_event_is_review_required",
                "review_reason_recovery_no_venue_order_id",
                "venue_order_id_absent_before_recovery",
                "proof_venue_order_id_present",
                "unique_matching_open_order",
                "matching_open_order_matches_command",
                "authenticated_live_order_seen",
                "point_order_matched_size_not_positive",
                "no_matching_trades",
                "no_trade_facts",
            )
    missing = [name for name in required_true if required_predicates.get(name) is not True]
    if missing:
        raise ValueError(f"review live-order clearance predicates are not proven true: {missing}")
    actual_predicates = _actual_review_venue_order_live_predicates(conn, command_id, payload)
    actual_failures = [name for name in required_true if not actual_predicates.get(name)]
    if actual_failures:
        raise ValueError(f"review live-order clearance DB predicates failed: {actual_failures}")
    proof = payload.get("venue_order_live_proof")
    if not isinstance(proof, dict):
        raise ValueError("review live-order clearance requires venue_order_live_proof")
    for key in ("source", "observed_at", "venue_order_id"):
        if not str(proof.get(key) or "").strip():
            raise ValueError(f"review live-order clearance proof missing {key}")
    if proof_class == "cancel_unknown_venue_order_live" and not str(proof.get("point_order_status") or "").strip():
        raise ValueError("review live-order clearance proof missing point_order_status")
    allowed_sources = (
        {"authenticated_clob_point_order_read"}
        if proof_class == "cancel_unknown_venue_order_live"
        else (
            {"authenticated_clob_user_or_point_order_read"}
            if proof_class == "acked_submit_venue_order_live"
            else {"authenticated_clob_user_open_orders_read"}
        )
    )
    if proof.get("source") not in allowed_sources:
        raise ValueError("review live-order clearance requires authenticated point order proof")
    source = payload.get("source_proof")
    if not isinstance(source, dict):
        raise ValueError("review live-order clearance requires source_proof")
    if source.get("source_function") not in {"command_recovery._reconcile_row", "operator_review"}:
        raise ValueError("review live-order clearance source_function is not supported")
    expected_source_reason = (
        "cancel_unknown_venue_order_live"
        if proof_class == "cancel_unknown_venue_order_live"
        else (
            "acked_submit_venue_order_live"
            if proof_class == "acked_submit_venue_order_live"
            else "recovery_no_venue_order_id_live_order"
        )
    )
    if source.get("source_reason") != expected_source_reason:
        raise ValueError("review live-order clearance source_reason is not supported")


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
    proof_class = payload.get("proof_class")
    if proof_class == "cancel_unknown_terminal_no_fill":
        _validate_review_cancel_unknown_no_fill_payload(
            conn=conn,
            current_state=current_state,
            payload=payload,
            command_id=command_id,
        )
        return
    if proof_class == "acked_submit_terminal_no_fill":
        _validate_review_acked_submit_terminal_no_fill_payload(
            conn=conn,
            current_state=current_state,
            payload=payload,
            command_id=command_id,
        )
        return
    if proof_class != "venue_absence_no_exposure":
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
    if actual_reason not in _NO_VENUE_EXPOSURE_REVIEW_REASONS:
        raise ValueError("review no-exposure clearance only supports no-venue-order-id recovery")
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
    if age_seconds < -5 or _freshness_registry.evaluate("venue_clearance", age_seconds) >= FreshnessLevel.STALE:
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


def _latest_payload_is_cancel_unknown(payload: dict) -> bool:
    if (
        str(payload.get("semantic_cancel_status") or "").upper() == "CANCEL_UNKNOWN"
        and payload.get("requires_m5_reconcile") is True
    ):
        return True
    return str(payload.get("reason") or "") == "post_cancel_unknown_possible_side_effect"


_POINT_ORDER_LIVE_DATA_KEYS = (
    "size",
    "original_size",
    "originalSize",
    "size_matched",
    "sizeMatched",
    "matched",
    "matched_size",
    "matchedSize",
    "matched_amount",
    "price",
    "side",
    "remaining",
    "remaining_size",
    "remainingSize",
)


def _point_order_has_live_data(point_order: object) -> bool:
    if not isinstance(point_order, dict):
        return False
    return any(point_order.get(key) not in (None, "") for key in _POINT_ORDER_LIVE_DATA_KEYS)


def _validate_review_cancel_unknown_no_fill_payload(
    *,
    conn: sqlite3.Connection,
    current_state: str,
    payload: dict,
    command_id: str,
) -> None:
    if current_state != "REVIEW_REQUIRED":
        raise ValueError("cancel-unknown no-fill clearance is only legal from REVIEW_REQUIRED")
    required_predicates = payload.get("required_predicates")
    if not isinstance(required_predicates, dict):
        raise ValueError("cancel-unknown no-fill clearance requires required_predicates")
    required_true = (
        "latest_event_is_cancel_replace_blocked",
        "semantic_cancel_status_cancel_unknown",
        "requires_m5_reconcile",
        "venue_order_id_present",
        "point_order_terminal_no_fill",
        "point_order_matched_size_zero",
        "no_trade_facts",
        "no_matching_open_orders",
        "no_matching_trades",
    )
    missing = [name for name in required_true if required_predicates.get(name) is not True]
    if missing:
        raise ValueError(f"cancel-unknown no-fill predicates are not proven true: {missing}")
    point_order_matches = required_predicates.get("venue_order_id_matches_point_read") is True
    point_order_absent = required_predicates.get("point_order_absent") is True
    point_order_no_live_record = required_predicates.get("point_order_no_live_record") is True
    if not point_order_matches and not point_order_absent and not point_order_no_live_record:
        raise ValueError(
            "cancel-unknown no-fill clearance requires point order match, authenticated absence, "
            "or authenticated no-live-record proof"
        )
    if payload.get("side_effect_boundary_crossed") != "unknown":
        raise ValueError("cancel-unknown no-fill clearance requires side_effect_boundary_crossed=unknown")
    if payload.get("sdk_submit_attempted") != "unknown":
        raise ValueError("cancel-unknown no-fill clearance requires sdk_submit_attempted=unknown")
    with _row_factory_as(conn, sqlite3.Row):
        command = conn.execute(
            """
            SELECT command_id, position_id, decision_id, market_id, token_id, side, price, size, created_at, venue_order_id
              FROM venue_commands
             WHERE command_id = ?
            """,
            (command_id,),
        ).fetchone()
        current = conn.execute(
            """
            SELECT phase, shares, cost_basis_usd
              FROM position_current
             WHERE position_id = (SELECT position_id FROM venue_commands WHERE command_id = ?)
             LIMIT 1
            """,
            (command_id,),
        ).fetchone()
        latest_event = conn.execute(
            """
            SELECT event_type, payload_json
              FROM venue_command_events
             WHERE command_id = ?
             ORDER BY sequence_no DESC
             LIMIT 1
            """,
            (command_id,),
        ).fetchone()
        fact = conn.execute(
            """
            SELECT fact_id, venue_order_id, state, matched_size, local_sequence
              FROM venue_order_facts
             WHERE fact_id = ?
               AND command_id = ?
            """,
            (payload.get("terminal_order_fact_id"), command_id),
        ).fetchone()
        latest_fact = conn.execute(
            """
            SELECT fact_id, venue_order_id, state, matched_size, local_sequence
              FROM venue_order_facts
             WHERE command_id = ?
             ORDER BY local_sequence DESC
             LIMIT 1
            """,
            (command_id,),
        ).fetchone()
    if command is None or not str(command["venue_order_id"] or "").strip():
        raise ValueError("cancel-unknown no-fill clearance requires command venue_order_id")
    if current is None:
        raise ValueError("cancel-unknown no-fill clearance requires position_current")
    # voided is the projection lane's own zero-fill terminal for a canceled
    # entry (live 2026-07-05: venue-canceled maker rests projected to voided
    # before command recovery ran) — equally zero-exposure as pending_entry.
    if str(current["phase"] or "") not in ("pending_entry", "voided"):
        raise ValueError("cancel-unknown no-fill clearance requires zero-exposure projection")
    try:
        shares = Decimal(str(current["shares"] or "0"))
        cost_basis = Decimal(str(current["cost_basis_usd"] or "0"))
    except (InvalidOperation, TypeError) as exc:
        raise ValueError("cancel-unknown no-fill position_current exposure is invalid") from exc
    if shares != Decimal("0") or cost_basis != Decimal("0"):
        raise ValueError("cancel-unknown no-fill clearance requires zero local exposure")
    if latest_event is None or latest_event["event_type"] != "CANCEL_REPLACE_BLOCKED":
        raise ValueError("cancel-unknown no-fill clearance requires latest CANCEL_REPLACE_BLOCKED")
    try:
        latest_payload = json.loads(str(latest_event["payload_json"] or "{}"))
    except json.JSONDecodeError as exc:
        raise ValueError("cancel-unknown no-fill latest payload is invalid") from exc
    if not isinstance(latest_payload, dict):
        raise ValueError("cancel-unknown no-fill latest payload is invalid")
    if not _latest_payload_is_cancel_unknown(latest_payload):
        raise ValueError("cancel-unknown no-fill latest payload must be CANCEL_UNKNOWN")
    if fact is None:
        raise ValueError("cancel-unknown no-fill clearance requires terminal order fact")
    if latest_fact is None or int(fact["fact_id"]) != int(latest_fact["fact_id"]):
        raise ValueError("cancel-unknown no-fill terminal order fact must be latest")
    if str(fact["venue_order_id"] or "") != str(command["venue_order_id"] or ""):
        raise ValueError("cancel-unknown no-fill terminal order fact venue_order_id mismatch")
    if str(fact["state"] or "") not in {"CANCEL_CONFIRMED", "EXPIRED", "VENUE_WIPED"}:
        raise ValueError("cancel-unknown no-fill terminal order fact state is invalid")
    try:
        if Decimal(str(fact["matched_size"] or "0")) != Decimal("0"):
            raise ValueError("cancel-unknown no-fill terminal order fact matched_size must be zero")
    except (InvalidOperation, TypeError) as exc:
        raise ValueError("cancel-unknown no-fill terminal order fact matched_size is invalid") from exc
    if _review_clearance_fact_count(conn, "venue_trade_facts", command_id) != 0:
        raise ValueError("cancel-unknown no-fill clearance found trade facts")
    venue_proof = payload.get("venue_absence_proof")
    if not isinstance(venue_proof, dict):
        raise ValueError("cancel-unknown no-fill clearance requires venue_absence_proof")
    if point_order_absent and (
        str(venue_proof.get("point_order_status") or "").upper() != "NOT_FOUND"
        or venue_proof.get("point_order") is not None
    ):
        raise ValueError("cancel-unknown no-fill point_order_absent proof is invalid")
    if point_order_no_live_record:
        point_order = venue_proof.get("point_order")
        status = str(venue_proof.get("point_order_status") or "").upper()
        if status not in {"UNKNOWN", "NOT_FOUND", ""}:
            raise ValueError("cancel-unknown no-fill point_order_no_live_record status is invalid")
        if not isinstance(point_order, dict):
            raise ValueError("cancel-unknown no-fill point_order_no_live_record requires point_order payload")
        point_order_id = str(
            point_order.get("orderID")
            or point_order.get("orderId")
            or point_order.get("order_id")
            or point_order.get("id")
            or ""
        )
        if point_order_id and point_order_id != str(command["venue_order_id"] or ""):
            raise ValueError("cancel-unknown no-fill point_order_no_live_record order id mismatch")
        if _point_order_has_live_data(point_order):
            raise ValueError("cancel-unknown no-fill point_order_no_live_record contains live order data")
    if venue_proof.get("owner_scope") != "authenticated_funder":
        raise ValueError("cancel-unknown no-fill clearance requires authenticated_funder owner_scope")
    for key in ("open_orders_checked", "trades_checked", "open_orders_query_complete", "trades_query_complete"):
        if venue_proof.get(key) is not True:
            raise ValueError(f"cancel-unknown no-fill clearance requires {key}=true")
    if not str(venue_proof.get("pagination_scope") or "").strip():
        raise ValueError("cancel-unknown no-fill clearance requires pagination_scope")
    if int(venue_proof.get("matching_open_order_count", -1)) != 0:
        raise ValueError("cancel-unknown no-fill clearance found matching open orders")
    if int(venue_proof.get("matching_trade_count", -1)) != 0:
        raise ValueError("cancel-unknown no-fill clearance found matching trades")
    for key in ("command_id", "market_id", "token_id", "side"):
        if str(venue_proof.get(key) or "") != str(command[key] or ""):
            raise ValueError(f"cancel-unknown no-fill venue_absence_proof {key} does not match command")
    for key in ("price", "size"):
        try:
            proof_value = Decimal(str(venue_proof.get(key)))
            command_value = Decimal(str(command[key]))
        except (InvalidOperation, TypeError):
            raise ValueError(f"cancel-unknown no-fill venue_absence_proof {key} is invalid")
        if proof_value != command_value:
            raise ValueError(f"cancel-unknown no-fill venue_absence_proof {key} does not match command")
    observed_at = _review_clearance_parse_utc(venue_proof.get("observed_at"))
    cleared_at = _review_clearance_parse_utc(payload.get("cleared_at"))
    window_start = _review_clearance_parse_utc(venue_proof.get("time_window_start"))
    window_end = _review_clearance_parse_utc(venue_proof.get("time_window_end"))
    command_created_at = _review_clearance_parse_utc(command["created_at"])
    if (
        observed_at is None
        or cleared_at is None
        or window_start is None
        or window_end is None
        or command_created_at is None
    ):
        raise ValueError("cancel-unknown no-fill clearance requires parseable proof times")
    age_seconds = (cleared_at - observed_at).total_seconds()
    if age_seconds < -5 or _freshness_registry.evaluate("venue_clearance", age_seconds) >= FreshnessLevel.STALE:
        raise ValueError("cancel-unknown no-fill venue proof is stale")
    if window_start > command_created_at or window_end < observed_at:
        raise ValueError("cancel-unknown no-fill time window does not cover command through venue read")
    source = payload.get("source_proof")
    if not isinstance(source, dict):
        raise ValueError("cancel-unknown no-fill clearance requires source_proof")
    for key in ("source_commit", "source_function", "source_reason"):
        if not str(source.get(key) or "").strip():
            raise ValueError(f"cancel-unknown no-fill source_proof missing {key}")
    if source.get("source_reason") not in {
        "cancel_unknown_point_order_terminal_no_fill",
        "cancel_unknown_point_order_absent_terminal_no_fill",
        "cancel_unknown_point_order_no_live_record_terminal_no_fill",
    }:
        raise ValueError("cancel-unknown no-fill source_reason is unsupported")


def _validate_review_acked_submit_terminal_no_fill_payload(
    *,
    conn: sqlite3.Connection,
    current_state: str,
    payload: dict,
    command_id: str,
) -> None:
    if current_state != "REVIEW_REQUIRED":
        raise ValueError("acked-submit no-fill clearance is only legal from REVIEW_REQUIRED")
    if payload.get("side_effect_boundary_crossed") is not True:
        raise ValueError("acked-submit no-fill clearance requires side_effect_boundary_crossed=true")
    if payload.get("sdk_submit_attempted") is not True:
        raise ValueError("acked-submit no-fill clearance requires sdk_submit_attempted=true")
    required_predicates = payload.get("required_predicates")
    if not isinstance(required_predicates, dict):
        raise ValueError("acked-submit no-fill clearance requires required_predicates")
    required_true = (
        "latest_event_is_review_required",
        "review_reason_post_ack_persistence_failure",
        "venue_order_id_present",
        "terminal_order_fact_latest",
        "terminal_order_fact_no_fill",
        "no_trade_facts",
        "no_matching_open_orders",
        "no_matching_trades",
        "no_positive_position_projection",
    )
    missing = [name for name in required_true if required_predicates.get(name) is not True]
    if missing:
        raise ValueError(f"acked-submit no-fill predicates are not proven true: {missing}")
    with _row_factory_as(conn, sqlite3.Row):
        command = conn.execute(
            """
            SELECT command_id, position_id, decision_id, market_id, token_id, side, price, size, created_at, venue_order_id
              FROM venue_commands
             WHERE command_id = ?
            """,
            (command_id,),
        ).fetchone()
        current = conn.execute(
            """
            SELECT phase, shares, cost_basis_usd
              FROM position_current
             WHERE position_id = (SELECT position_id FROM venue_commands WHERE command_id = ?)
             LIMIT 1
            """,
            (command_id,),
        ).fetchone()
        latest_event = conn.execute(
            """
            SELECT event_type, payload_json
              FROM venue_command_events
             WHERE command_id = ?
             ORDER BY sequence_no DESC
             LIMIT 1
            """,
            (command_id,),
        ).fetchone()
        fact = conn.execute(
            """
            SELECT fact_id, venue_order_id, state, matched_size, local_sequence
              FROM venue_order_facts
             WHERE fact_id = ?
               AND command_id = ?
            """,
            (payload.get("terminal_order_fact_id"), command_id),
        ).fetchone()
        latest_fact = conn.execute(
            """
            SELECT fact_id, venue_order_id, state, matched_size, local_sequence
              FROM venue_order_facts
             WHERE command_id = ?
             ORDER BY local_sequence DESC
             LIMIT 1
            """,
            (command_id,),
        ).fetchone()
    if command is None or not str(command["venue_order_id"] or "").strip():
        raise ValueError("acked-submit no-fill clearance requires command venue_order_id")
    if latest_event is None or latest_event["event_type"] != "REVIEW_REQUIRED":
        raise ValueError("acked-submit no-fill clearance requires latest REVIEW_REQUIRED")
    try:
        latest_payload = json.loads(str(latest_event["payload_json"] or "{}"))
    except json.JSONDecodeError as exc:
        raise ValueError("acked-submit no-fill latest payload is invalid") from exc
    if not isinstance(latest_payload, dict):
        raise ValueError("acked-submit no-fill latest payload is invalid")
    latest_reason = latest_payload.get("reason")
    allowed_reasons = {
        "entry_ack_persistence_failed_after_side_effect",
        "exit_ack_persistence_failed_after_side_effect",
    }
    if latest_reason not in allowed_reasons:
        raise ValueError("acked-submit no-fill clearance only supports post-ACK persistence failures")
    if fact is None:
        raise ValueError("acked-submit no-fill clearance requires terminal order fact")
    if latest_fact is None or int(fact["fact_id"]) != int(latest_fact["fact_id"]):
        raise ValueError("acked-submit no-fill terminal order fact must be latest")
    if str(fact["venue_order_id"] or "") != str(command["venue_order_id"] or ""):
        raise ValueError("acked-submit no-fill terminal order fact venue_order_id mismatch")
    if str(fact["state"] or "") not in {"CANCEL_CONFIRMED", "EXPIRED", "VENUE_WIPED"}:
        raise ValueError("acked-submit no-fill terminal order fact state is invalid")
    try:
        if Decimal(str(fact["matched_size"] or "0")) != Decimal("0"):
            raise ValueError("acked-submit no-fill terminal order fact matched_size must be zero")
    except (InvalidOperation, TypeError) as exc:
        raise ValueError("acked-submit no-fill terminal order fact matched_size is invalid") from exc
    if _review_clearance_fact_count(conn, "venue_trade_facts", command_id) != 0:
        raise ValueError("acked-submit no-fill clearance found trade facts")
    if current is not None:
        try:
            shares = Decimal(str(current["shares"] or "0"))
            cost_basis = Decimal(str(current["cost_basis_usd"] or "0"))
        except (InvalidOperation, TypeError) as exc:
            raise ValueError("acked-submit no-fill position exposure is invalid") from exc
        if shares != Decimal("0") or cost_basis != Decimal("0"):
            raise ValueError("acked-submit no-fill clearance requires no positive position projection")
    venue_proof = payload.get("venue_absence_proof")
    if not isinstance(venue_proof, dict):
        raise ValueError("acked-submit no-fill clearance requires venue_absence_proof")
    if venue_proof.get("owner_scope") != "authenticated_funder":
        raise ValueError("acked-submit no-fill clearance requires authenticated_funder owner_scope")
    for key in ("open_orders_checked", "trades_checked", "open_orders_query_complete", "trades_query_complete"):
        if venue_proof.get(key) is not True:
            raise ValueError(f"acked-submit no-fill clearance requires {key}=true")
    if not str(venue_proof.get("pagination_scope") or "").strip():
        raise ValueError("acked-submit no-fill clearance requires pagination_scope")
    if int(venue_proof.get("matching_open_order_count", -1)) != 0:
        raise ValueError("acked-submit no-fill clearance found matching open orders")
    if int(venue_proof.get("matching_trade_count", -1)) != 0:
        raise ValueError("acked-submit no-fill clearance found matching trades")
    for key in ("command_id", "market_id", "token_id", "side"):
        if str(venue_proof.get(key) or "") != str(command[key] or ""):
            raise ValueError(f"acked-submit no-fill venue_absence_proof {key} does not match command")
    for key in ("price", "size"):
        try:
            proof_value = Decimal(str(venue_proof.get(key)))
            command_value = Decimal(str(command[key]))
        except (InvalidOperation, TypeError) as exc:
            raise ValueError(f"acked-submit no-fill venue_absence_proof {key} is invalid") from exc
        if proof_value != command_value:
            raise ValueError(f"acked-submit no-fill venue_absence_proof {key} does not match command")
    observed_at = _review_clearance_parse_utc(venue_proof.get("observed_at"))
    cleared_at = _review_clearance_parse_utc(payload.get("cleared_at"))
    window_start = _review_clearance_parse_utc(venue_proof.get("time_window_start"))
    window_end = _review_clearance_parse_utc(venue_proof.get("time_window_end"))
    command_created_at = _review_clearance_parse_utc(command["created_at"])
    if (
        observed_at is None
        or cleared_at is None
        or window_start is None
        or window_end is None
        or command_created_at is None
    ):
        raise ValueError("acked-submit no-fill clearance requires parseable proof times")
    age_seconds = (cleared_at - observed_at).total_seconds()
    if age_seconds < -5 or _freshness_registry.evaluate("venue_clearance", age_seconds) >= FreshnessLevel.STALE:
        raise ValueError("acked-submit no-fill venue proof is stale")
    if window_start > command_created_at or window_end < observed_at:
        raise ValueError("acked-submit no-fill time window does not cover command through venue read")
    source = payload.get("source_proof")
    if not isinstance(source, dict):
        raise ValueError("acked-submit no-fill clearance requires source_proof")
    for key in ("source_commit", "source_function", "source_reason"):
        if not str(source.get(key) or "").strip():
            raise ValueError(f"acked-submit no-fill source_proof missing {key}")
    if source.get("source_function") != "command_recovery._reconcile_row":
        raise ValueError("acked-submit no-fill source_function is not supported")
    if source.get("source_reason") != "acked_submit_terminal_no_fill":
        raise ValueError("acked-submit no-fill source_reason is unsupported")
    review_proof = payload.get("review_required_proof")
    if not isinstance(review_proof, dict) or review_proof.get("reason") != latest_reason:
        raise ValueError("acked-submit no-fill review reason mismatch")


def _validate_review_confirmed_fill_payload(
    *,
    conn: sqlite3.Connection,
    payload: Optional[dict],
    command_id: str,
) -> None:
    if not isinstance(payload, dict):
        raise ValueError("review confirmed-fill clearance requires structured proof payload")
    if payload.get("reason") != "review_cleared_confirmed_fill":
        raise ValueError("review confirmed-fill clearance payload requires reason=review_cleared_confirmed_fill")
    if payload.get("command_id") != command_id:
        raise ValueError("review confirmed-fill clearance payload command_id must match appended command")
    proof_class = payload.get("proof_class")
    if proof_class not in {
        "prior_fill_confirmed_event_with_positive_trade_fact",
        "cancel_unknown_confirmed_trade_with_positive_trade_fact",
        "recovery_no_venue_order_id_confirmed_trade",
        "matched_submit_missing_trade_id_confirmed_trade",
        "matched_cancel_with_confirmed_held_projection",
        "authenticated_trade_fact_full_fill",
        "authenticated_trade_fact_full_fill_with_held_projection",
        "review_required_matched_order_fact_with_positive_trade_fact",
        "review_required_terminal_order_fact_with_held_projection",
    }:
        raise ValueError("review confirmed-fill clearance proof_class is not supported")
    required_predicates = payload.get("required_predicates")
    if not isinstance(required_predicates, dict):
        raise ValueError("review confirmed-fill clearance requires required_predicates")
    if proof_class == "cancel_unknown_confirmed_trade_with_positive_trade_fact":
        required_true = (
            "latest_event_is_cancel_replace_blocked",
            "semantic_cancel_status_cancel_unknown",
            "requires_m5_reconcile",
            "positive_trade_fact",
        )
    elif proof_class == "recovery_no_venue_order_id_confirmed_trade":
        required_true = (
            "latest_event_is_review_required",
            "review_reason_recovery_no_venue_order_id",
            "positive_trade_fact",
            "maker_order_token_matches_command",
            "maker_order_not_open",
            "venue_size_quantization_residual_lt_0_01",
        )
    elif proof_class == "matched_submit_missing_trade_id_confirmed_trade":
        required_true = (
            "latest_event_is_review_required",
            "review_reason_matched_submit_missing_trade_id",
            "positive_trade_fact",
            "maker_order_token_matches_command",
            "bound_venue_order_id_matches_trade",
            "maker_order_not_open",
            "venue_size_quantization_residual_lt_0_01",
        )
    elif proof_class == "matched_cancel_with_confirmed_held_projection":
        required_true = (
            "latest_event_is_cancel_replace_blocked",
            "cancel_response_not_canceled_because_matched",
            "positive_trade_facts",
            "residual_size_is_dust",
            "active_projection_matches_confirmed_fill",
        )
    elif proof_class == "authenticated_trade_fact_full_fill":
        required_true = (
            "command_state_review_required",
            "latest_event_is_review_boundary",
            "authenticated_confirmed_trade_facts",
            "bound_venue_order_id_matches_trade",
            "trade_facts_cover_command_or_leave_only_dust",
            "source_fill_time_valid",
        )
    elif proof_class == "authenticated_trade_fact_full_fill_with_held_projection":
        required_true = (
            "command_state_review_required",
            "latest_event_is_review_boundary",
            "authenticated_confirmed_trade_facts",
            "trade_facts_cover_command_or_leave_only_dust",
            "active_projection_matches_confirmed_fill",
            "source_fill_time_valid",
        )
    elif proof_class == "review_required_matched_order_fact_with_positive_trade_fact":
        required_true = (
            "command_state_review_required",
            "latest_event_is_review_boundary",
            "positive_trade_fact",
            "matched_order_fact_positive",
            "trade_facts_cover_command_or_leave_only_dust",
        )
    elif proof_class == "review_required_terminal_order_fact_with_held_projection":
        required_true = (
            "command_state_review_required",
            "latest_event_is_review_boundary",
            "matched_order_fact_positive",
            "residual_size_is_dust",
            "active_projection_matches_confirmed_fill",
        )
    else:
        required_true = (
            "latest_event_is_review_required",
            "review_reason_supported",
            "prior_fill_confirmed_event",
            "positive_trade_fact",
        )
    missing = [name for name in required_true if required_predicates.get(name) is not True]
    if missing:
        raise ValueError(f"review confirmed-fill predicates are not proven true: {missing}")
    actual = _actual_review_confirmed_fill_predicates(conn, command_id, payload)
    actual_failures = [name for name in required_true if not actual.get(name)]
    if actual_failures:
        raise ValueError(f"review confirmed-fill DB predicates failed: {actual_failures}")
    source = payload.get("source_proof")
    if not isinstance(source, dict):
        raise ValueError("review confirmed-fill clearance requires source_proof")
    if source.get("source_function") not in {
        "PolymarketUserChannelIngestor._handle_trade",
        "operator_review",
        "command_recovery._review_required_cancel_unknown_live_order_recovery",
        "command_recovery._reconcile_row",
        "command_recovery.reconcile_matched_cancel_review_required_entries",
        "command_recovery.reconcile_matched_order_facts",
    }:
        raise ValueError("review confirmed-fill clearance source_function is not supported")
    if not str(source.get("source_commit") or "").strip():
        raise ValueError("review confirmed-fill clearance requires source_commit")


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


def _actual_review_confirmed_fill_predicates(
    conn: sqlite3.Connection,
    command_id: str,
    payload: dict,
) -> dict[str, bool]:
    trade_id = str(payload.get("trade_id") or "")
    venue_order_id = str(payload.get("venue_order_id") or "")
    filled_size = str(payload.get("filled_size") or "")
    fill_price = str(payload.get("fill_price") or "")
    with _row_factory_as(conn, sqlite3.Row):
        events = conn.execute(
            """
            SELECT sequence_no, event_type, payload_json
            FROM venue_command_events
            WHERE command_id = ?
            ORDER BY sequence_no
            """,
            (command_id,),
        ).fetchall()
        trade_fact = conn.execute(
            """
            WITH canonical_trade_fact AS (
                SELECT ranked.*
                  FROM (
                        SELECT scored.*,
                               ROW_NUMBER() OVER (
                                   PARTITION BY command_id, trade_id
                                   ORDER BY proof_rank DESC, local_sequence DESC
                               ) AS canonical_rank
                          FROM (
                                SELECT fact.*,
                                       CASE
                                           WHEN UPPER(COALESCE(fact.state, '')) = 'CONFIRMED'
                                                AND CAST(COALESCE(fact.filled_size, '0') AS REAL) > 0
                                           THEN 500
                                           WHEN UPPER(COALESCE(fact.state, '')) = 'MINED'
                                                AND CAST(COALESCE(fact.filled_size, '0') AS REAL) > 0
                                           THEN 450
                                           WHEN UPPER(COALESCE(fact.state, '')) = 'MATCHED'
                                                AND CAST(COALESCE(fact.filled_size, '0') AS REAL) > 0
                                           THEN 400
                                           WHEN CAST(COALESCE(fact.filled_size, '0') AS REAL) > 0
                                           THEN 300
                                           ELSE 100
                                       END AS proof_rank
                                  FROM venue_trade_facts fact
                               ) scored
                       ) ranked
                 WHERE ranked.canonical_rank = 1
            )
            SELECT *
            FROM canonical_trade_fact
            WHERE command_id = ?
              AND trade_id = ?
              AND venue_order_id = ?
              AND state IN ('MATCHED', 'MINED', 'CONFIRMED')
              AND CAST(COALESCE(filled_size, '0') AS REAL) > 0
            """,
            (command_id, trade_id, venue_order_id),
        ).fetchone()
        aggregate_trade_rows = conn.execute(
            """
            WITH canonical_trade_fact AS (
                SELECT ranked.*
                  FROM (
                        SELECT scored.*,
                               ROW_NUMBER() OVER (
                                   PARTITION BY command_id, trade_id
                                   ORDER BY proof_rank DESC, local_sequence DESC
                               ) AS canonical_rank
                          FROM (
                                SELECT fact.*,
                                       CASE
                                           WHEN UPPER(COALESCE(fact.state, '')) = 'CONFIRMED'
                                                AND CAST(COALESCE(fact.filled_size, '0') AS REAL) > 0
                                           THEN 500
                                           WHEN UPPER(COALESCE(fact.state, '')) = 'MINED'
                                                AND CAST(COALESCE(fact.filled_size, '0') AS REAL) > 0
                                           THEN 450
                                           WHEN UPPER(COALESCE(fact.state, '')) = 'MATCHED'
                                                AND CAST(COALESCE(fact.filled_size, '0') AS REAL) > 0
                                           THEN 400
                                           WHEN CAST(COALESCE(fact.filled_size, '0') AS REAL) > 0
                                           THEN 300
                                           ELSE 100
                                       END AS proof_rank
                                  FROM venue_trade_facts fact
                               ) scored
                       ) ranked
                 WHERE ranked.canonical_rank = 1
            ),
            economic_trade_fact AS (
                SELECT fact.*
                  FROM canonical_trade_fact fact
                 WHERE NOT (
                        TRIM(COALESCE(fact.tx_hash, '')) != ''
                    AND LOWER(TRIM(COALESCE(fact.trade_id, '')))
                        = LOWER(TRIM(fact.tx_hash))
                    AND EXISTS (
                            SELECT 1
                              FROM canonical_trade_fact exact
                             WHERE exact.command_id = fact.command_id
                               AND LOWER(TRIM(COALESCE(exact.tx_hash, '')))
                                   = LOWER(TRIM(fact.tx_hash))
                               AND LOWER(TRIM(COALESCE(exact.trade_id, '')))
                                   != LOWER(TRIM(COALESCE(fact.trade_id, '')))
                               AND UPPER(COALESCE(exact.state, ''))
                                   IN ('MATCHED', 'MINED', 'CONFIRMED')
                               AND CAST(COALESCE(exact.filled_size, '0') AS REAL) > 0
                        )
                    )
                   AND NOT EXISTS (
                           SELECT 1
                             FROM venue_trade_facts source_fact
                            WHERE source_fact.trade_fact_id = CASE
                                      WHEN json_valid(fact.raw_payload_json)
                                      THEN CAST(json_extract(
                                          fact.raw_payload_json,
                                          '$.raw_fill_payload.source_trade_fact_id'
                                      ) AS INTEGER)
                                  END
                              AND source_fact.command_id = fact.command_id
                              AND source_fact.venue_order_id = fact.venue_order_id
                              AND UPPER(COALESCE(source_fact.state, ''))
                                  IN ('MATCHED', 'MINED', 'CONFIRMED')
                              AND CAST(COALESCE(source_fact.filled_size, '0') AS REAL) > 0
                        )
            )
            SELECT filled_size, source, state, observed_at, venue_timestamp
              FROM economic_trade_fact
             WHERE command_id = ?
               AND venue_order_id = ?
               AND state IN ('MATCHED', 'MINED', 'CONFIRMED')
               AND CAST(COALESCE(filled_size, '0') AS REAL) > 0
            """,
            (command_id, venue_order_id),
        ).fetchall()
        order_fact = conn.execute(
            """
            SELECT state, remaining_size, matched_size
              FROM venue_order_facts
             WHERE command_id = ?
               AND venue_order_id = ?
             ORDER BY
               CASE
                 WHEN UPPER(COALESCE(state, '')) IN ('MATCHED', 'FILLED')
                      AND CAST(COALESCE(matched_size, '0') AS REAL) > 0
                      AND CAST(COALESCE(remaining_size, '0') AS REAL) = 0
                 THEN 600
                 WHEN UPPER(COALESCE(state, '')) IN ('PARTIALLY_MATCHED', 'PARTIAL')
                      AND CAST(COALESCE(matched_size, '0') AS REAL) > 0
                 THEN 400
                 WHEN UPPER(COALESCE(state, '')) IN ('LIVE', 'OPEN', 'RESTING')
                 THEN 200
                 ELSE 100
               END DESC,
               local_sequence DESC
             LIMIT 1
            """,
            (command_id, venue_order_id),
        ).fetchone()
        command = conn.execute(
            "SELECT position_id, state, venue_order_id, size FROM venue_commands WHERE command_id = ?",
            (command_id,),
        ).fetchone()
        position_rows = conn.execute(
            """
            SELECT phase, chain_state, shares, chain_shares
              FROM position_current
             WHERE order_id = ?
                OR position_id = ?
             ORDER BY updated_at DESC
            """,
            (
                venue_order_id,
                str(command["position_id"] or "") if command is not None else "",
            ),
        ).fetchall()
    latest_event_type = str(events[-1]["event_type"] or "") if events else ""
    command_state = str(command["state"] or "") if command is not None else ""
    prior_fill_confirmed = False
    for event in events[:-1]:
        if str(event["event_type"] or "") != "FILL_CONFIRMED":
            continue
        event_payload = _review_clearance_json_dict(event["payload_json"])
        if (
            str(event_payload.get("trade_id") or "") == trade_id
            and str(event_payload.get("venue_order_id") or "") == venue_order_id
        ):
            prior_fill_confirmed = True
            break
    latest_payload = _review_clearance_json_dict(events[-1]["payload_json"]) if events else {}
    review_reason = _actual_review_required_reason(conn, command_id)
    positive_trade_fact = (
        trade_fact is not None
        and trade_fact_has_positive_fill_economics(trade_fact)
        and _decimal_text_equal(filled_size, trade_fact["filled_size"])
        and _decimal_text_equal(fill_price, trade_fact["fill_price"])
    )
    aggregate_filled = Decimal("0")
    aggregate_count = 0
    aggregate_authenticated_confirmed = True
    aggregate_source_times: list[datetime.datetime] = []
    for row in aggregate_trade_rows:
        size = _decimal_or_none(row["filled_size"])
        if size is None or size <= 0:
            continue
        aggregate_filled += size
        aggregate_count += 1
        aggregate_authenticated_confirmed = aggregate_authenticated_confirmed and (
            str(row["source"] or "").upper() == "WS_USER"
            and str(row["state"] or "").upper() == "CONFIRMED"
        )
        source_time = _review_clearance_parse_utc(
            row["venue_timestamp"] or row["observed_at"]
        )
        if source_time is not None:
            aggregate_source_times.append(source_time)
    payload_filled = _decimal_or_none(filled_size)
    order_residual = _decimal_or_none(order_fact["remaining_size"]) if order_fact is not None else None
    residual_is_dust = (
        order_residual is not None
        and Decimal("0") <= order_residual <= Decimal("0.011")
    )
    active_projection_matches = False
    if payload_filled is not None:
        for row in position_rows:
            if str(row["phase"] or "") not in {"active", "day0_window", "pending_exit"}:
                continue
            if str(row["chain_state"] or "") not in {"synced", "chain_present", "exit_pending_missing"}:
                continue
            chain_shares = _decimal_or_none(row["chain_shares"])
            if chain_shares is not None:
                if abs(chain_shares - payload_filled) > Decimal("0.02"):
                    continue
                active_projection_matches = True
                break
            shares = _decimal_or_none(row["shares"])
            if shares is None or abs(shares - payload_filled) > Decimal("0.01"):
                continue
            active_projection_matches = True
            break
    cancel_outcome = latest_payload.get("cancel_outcome")
    cancel_outcome = cancel_outcome if isinstance(cancel_outcome, dict) else {}
    cancel_text = " ".join(
        str(value or "")
        for value in (
            latest_payload.get("reason"),
            latest_payload.get("semantic_cancel_status"),
            cancel_outcome.get("status"),
            cancel_outcome.get("errorMsg"),
            cancel_outcome.get("errorMessage"),
            cancel_outcome.get("message"),
        )
    ).lower()
    aggregate_positive_trade_facts = (
        aggregate_count > 0
        and payload_filled is not None
        and abs(aggregate_filled - payload_filled) <= Decimal("0.000001")
    )
    command_size = _decimal_or_none(command["size"]) if command is not None else None
    trade_facts_cover_command_or_leave_only_dust = (
        aggregate_count > 0
        and command_size is not None
        and abs(command_size - aggregate_filled) <= Decimal("0.011")
    )
    cleared_at = _review_clearance_parse_utc(payload.get("cleared_at"))
    source_fill_time_valid = (
        aggregate_count > 0
        and len(aggregate_source_times) == aggregate_count
        and cleared_at is not None
        and cleared_at == max(aggregate_source_times)
    )
    order_fact_matched = _decimal_or_none(order_fact["matched_size"]) if order_fact is not None else None
    order_fact_remaining = _decimal_or_none(order_fact["remaining_size"]) if order_fact is not None else None
    matched_order_fact_positive = (
        order_fact is not None
        and payload_filled is not None
        and order_fact_matched is not None
        and order_fact_matched > 0
        and abs(order_fact_matched - payload_filled) <= Decimal("0.000001")
        and order_fact_remaining is not None
        and Decimal("0") <= order_fact_remaining <= Decimal("0.011")
    )
    required_predicates = payload.get("required_predicates")
    if not isinstance(required_predicates, dict):
        required_predicates = {}
    return {
        "command_state_review_required": command_state == "REVIEW_REQUIRED",
        "latest_event_is_review_boundary": latest_event_type in {
            "REVIEW_REQUIRED",
            "CANCEL_FAILED",
            "CANCEL_REPLACE_BLOCKED",
        },
        "latest_event_is_review_required": latest_event_type == "REVIEW_REQUIRED",
        "latest_event_is_cancel_replace_blocked": latest_event_type == "CANCEL_REPLACE_BLOCKED",
        "semantic_cancel_status_cancel_unknown": _latest_payload_is_cancel_unknown(latest_payload),
        "requires_m5_reconcile": _latest_payload_is_cancel_unknown(latest_payload),
        "review_reason_supported": review_reason == "ws_trade_lifecycle_regression_or_economic_drift",
        "review_reason_recovery_no_venue_order_id": review_reason == "recovery_no_venue_order_id",
        "review_reason_matched_submit_missing_trade_id": (
            review_reason == "matched_submit_missing_trade_id"
        ),
        "prior_fill_confirmed_event": prior_fill_confirmed,
        "positive_trade_fact": positive_trade_fact,
        "matched_order_fact_positive": matched_order_fact_positive,
        "positive_trade_facts": aggregate_positive_trade_facts,
        "authenticated_confirmed_trade_facts": (
            aggregate_count > 0 and aggregate_authenticated_confirmed
        ),
        "trade_facts_cover_command_or_leave_only_dust": (
            trade_facts_cover_command_or_leave_only_dust
        ),
        "source_fill_time_valid": source_fill_time_valid,
        "cancel_response_not_canceled_because_matched": (
            "not_canceled" in cancel_text and "matched" in cancel_text
        ),
        "residual_size_is_dust": residual_is_dust,
        "active_projection_matches_confirmed_fill": active_projection_matches,
        "maker_order_token_matches_command": required_predicates.get("maker_order_token_matches_command") is True,
        "bound_venue_order_id_matches_trade": (
            command is not None
            and str(command["venue_order_id"] or "") == venue_order_id
        ),
        "maker_order_not_open": required_predicates.get("maker_order_not_open") is True,
        "venue_size_quantization_residual_lt_0_01": (
            required_predicates.get("venue_size_quantization_residual_lt_0_01") is True
        ),
    }


def _optional_decimal_positive(value: object) -> bool:
    if value in (None, ""):
        return False
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return False
    return parsed.is_finite() and parsed > 0


def _actual_review_venue_order_live_predicates(
    conn: sqlite3.Connection,
    command_id: str,
    payload: dict,
) -> dict[str, bool]:
    with _row_factory_as(conn, sqlite3.Row):
        command = conn.execute(
            """
            SELECT venue_order_id, token_id, side, price, size
            FROM venue_commands
            WHERE command_id = ?
            """,
            (command_id,),
        ).fetchone()
        latest = conn.execute(
            """
            SELECT event_type, payload_json
            FROM venue_command_events
            WHERE command_id = ?
            ORDER BY sequence_no DESC
            LIMIT 1
            """,
            (command_id,),
        ).fetchone()
    latest_payload: dict[str, Any] = {}
    if latest is not None and latest["payload_json"]:
        try:
            parsed = json.loads(str(latest["payload_json"]))
        except json.JSONDecodeError:
            parsed = {}
        if isinstance(parsed, dict):
            latest_payload = parsed
    proof = payload.get("venue_order_live_proof")
    proof = proof if isinstance(proof, dict) else {}
    point_order = proof.get("point_order")
    point_order = point_order if isinstance(point_order, dict) else {}
    latest_order_fact = proof.get("latest_order_fact")
    latest_order_fact = latest_order_fact if isinstance(latest_order_fact, dict) else {}
    matching_open_orders = proof.get("matching_open_orders")
    matching_open_orders = matching_open_orders if isinstance(matching_open_orders, list) else []
    command_venue_order_id = str(command["venue_order_id"] or "").strip() if command is not None else ""
    command_token_id = str(command["token_id"] or "").strip() if command is not None else ""
    command_side = str(command["side"] or "").upper().strip() if command is not None else ""
    command_price = command["price"] if command is not None else None
    command_size = command["size"] if command is not None else None
    matching_open_order_id_matches = any(
        _venue_order_id_from_payload(order if isinstance(order, dict) else {}) == command_venue_order_id
        for order in matching_open_orders
    )
    proof_venue_order_id = str(
        proof.get("venue_order_id")
        or point_order.get("orderID")
        or point_order.get("orderId")
        or point_order.get("order_id")
        or point_order.get("id")
        or ""
    ).strip()
    point_status = str(
        proof.get("point_order_status")
        or point_order.get("status")
        or point_order.get("state")
        or ""
    ).upper()
    latest_fact_state = str(latest_order_fact.get("state") or "").upper()
    latest_fact_venue_order_id = str(latest_order_fact.get("venue_order_id") or "").strip()
    matching_open_order_seen = bool(matching_open_orders) and matching_open_order_id_matches
    matched_size = (
        proof.get("matched_size")
        if proof.get("matched_size") not in (None, "")
        else (
            point_order.get("matched_size")
            or point_order.get("matched")
            or point_order.get("matched_amount")
            or point_order.get("filled_size")
        )
    )
    latest_reason = str(latest_payload.get("reason") or "")
    proof_order_token = str(
        point_order.get("asset_id")
        or point_order.get("token_id")
        or point_order.get("tokenID")
        or point_order.get("assetId")
        or ""
    ).strip()
    proof_order_side = str(point_order.get("side") or "").upper().strip()
    proof_order_size = (
        point_order.get("original_size")
        or point_order.get("size")
        or point_order.get("matched_amount")
    )
    proof_order_identity_matches_command = (
        bool(proof_venue_order_id)
        and bool(command_token_id)
        and proof_order_token == command_token_id
        and proof_order_side == command_side
        and _decimal_text_equal(point_order.get("price"), command_price)
        and _decimal_text_equal(proof_order_size, command_size)
    )
    return {
        "latest_event_is_cancel_replace_blocked": (
            latest is not None
            and latest["event_type"] == "CANCEL_REPLACE_BLOCKED"
        ),
        "latest_event_is_review_required": (
            latest is not None
            and latest["event_type"] == "REVIEW_REQUIRED"
        ),
        "review_reason_post_ack_persistence_failure": latest_reason in {
            "entry_ack_persistence_failed_after_side_effect",
            "exit_ack_persistence_failed_after_side_effect",
        },
        "review_reason_recovery_no_venue_order_id": latest_reason == "recovery_no_venue_order_id",
        "semantic_cancel_status_cancel_unknown": _latest_payload_is_cancel_unknown(latest_payload),
        "requires_m5_reconcile": _latest_payload_is_cancel_unknown(latest_payload),
        "venue_order_id_present": bool(command_venue_order_id),
        "venue_order_id_absent_before_recovery": not command_venue_order_id,
        "proof_venue_order_id_present": bool(proof_venue_order_id),
        "venue_order_id_matches_point_read": (
            bool(command_venue_order_id)
            and command_venue_order_id == proof_venue_order_id
        ),
        "venue_order_id_matches_live_proof": (
            bool(command_venue_order_id)
            and (
                command_venue_order_id == proof_venue_order_id
                or matching_open_order_id_matches
                or latest_fact_venue_order_id == command_venue_order_id
            )
        ),
        "point_order_status_live": point_status in {"LIVE", "OPEN", "RESTING"},
        "latest_order_fact_live": (
            bool(command_venue_order_id)
            and latest_fact_venue_order_id == command_venue_order_id
            and latest_fact_state in {"LIVE", "OPEN", "RESTING"}
            and not _optional_decimal_positive(latest_order_fact.get("matched_size"))
        ),
        "authenticated_live_order_seen": (
            matching_open_order_seen
            or (
                bool(command_venue_order_id)
                and latest_fact_venue_order_id == command_venue_order_id
                and latest_fact_state in {"LIVE", "OPEN", "RESTING"}
            )
            or point_status in {"LIVE", "OPEN", "RESTING"}
        ),
        "unique_matching_open_order": proof.get("matching_open_order_count") == 1,
        "matching_open_order_matches_command": proof_order_identity_matches_command,
        "no_matching_trades": proof.get("matching_trade_count") == 0,
        "point_order_matched_size_not_positive": not _optional_decimal_positive(matched_size),
        "no_trade_facts": _review_clearance_fact_count(conn, "venue_trade_facts", command_id) == 0,
    }


def _venue_order_id_from_payload(payload: Optional[dict]) -> str | None:
    if not isinstance(payload, dict):
        return None
    for key in ("venue_order_id", "orderID", "orderId", "order_id", "id"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def repair_command_position_link_if_orphaned(
    conn: sqlite3.Connection,
    *,
    command_id: str,
    canonical_position_id: str,
    occurred_at: str,
    reason: str,
) -> bool:
    """Relink a command journal row to an existing canonical position.

    This is intentionally narrower than a generic position_id edit. It only
    repairs the EDLI bridge shape where a command still points at a pre-bridge
    short id that has no ``position_current`` row, while the canonical EDLI
    ``position_current`` row already exists. If the command already points at a
    different real position, the function refuses to overwrite it.
    """

    command_id = _require_nonempty("command_id", command_id)
    canonical_position_id = _require_nonempty("canonical_position_id", canonical_position_id)
    occurred_at = _validate_observed_at(occurred_at)
    reason = _require_nonempty("reason", reason)

    with _row_factory_as(conn, sqlite3.Row):
        command = conn.execute(
            """
            SELECT command_id, position_id, state
              FROM venue_commands
             WHERE command_id = ?
             LIMIT 1
            """,
            (command_id,),
        ).fetchone()
        if command is None:
            return False

        current_position_id = str(command["position_id"] or "")
        if current_position_id == canonical_position_id:
            return False

        canonical_exists = conn.execute(
            "SELECT 1 FROM position_current WHERE position_id = ? LIMIT 1",
            (canonical_position_id,),
        ).fetchone()
        if canonical_exists is None:
            raise ValueError(
                "command position-link repair requires canonical position_current row"
            )

        current_exists = None
        if current_position_id:
            current_exists = conn.execute(
                "SELECT 1 FROM position_current WHERE position_id = ? LIMIT 1",
                (current_position_id,),
            ).fetchone()
        if current_exists is not None:
            raise ValueError(
                "command position-link repair refuses to overwrite a command "
                "that already points at an existing position_current row"
            )

        updated = conn.execute(
            """
            UPDATE venue_commands
               SET position_id = ?,
                   updated_at = ?
             WHERE command_id = ?
               AND position_id = ?
            """,
            (
                canonical_position_id,
                occurred_at,
                command_id,
                current_position_id,
            ),
        ).rowcount

    if not updated:
        return False

    append_provenance_event(
        conn,
        subject_type="command",
        subject_id=command_id,
        event_type="POSITION_LINK_REPAIRED",
        payload_hash=_payload_hash(
            {
                "canonical_position_id": canonical_position_id,
                "previous_position_id": current_position_id,
                "reason": reason,
            }
        ),
        payload_json={
            "canonical_position_id": canonical_position_id,
            "previous_position_id": current_position_id,
            "reason": reason,
        },
        source="WS_USER",
        observed_at=occurred_at,
    )
    return True


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
        if state not in _TERMINAL_NO_RESTING_ORDER_FACT_STATES:
            prior_terminal = _prior_terminal_no_resting_order_fact(
                conn,
                venue_order_id=venue_order_id,
                command_id=command_id,
            )
            terminal_partial_correction = _terminal_partial_correction_proven(
                conn,
                venue_order_id=venue_order_id,
                command_id=command_id,
                state=state,
                remaining_size=remaining_size,
                matched_size=matched_size,
                raw_payload_json=raw_payload_json,
            )
            if prior_terminal is not None and not terminal_partial_correction:
                from src.execution.order_truth_reducer import (
                    TERMINAL_FILLED,
                    TERMINAL_PARTIAL,
                    TERMINAL_NO_FILL,
                    VenueOrderTruthReducer,
                )
                from src.state.canonical_projections import is_open_order_fact

                reduced = VenueOrderTruthReducer.reduce(
                    order_facts=[
                        {
                            "state": state,
                            "remaining_size": remaining_size,
                            "matched_size": matched_size,
                        },
                        prior_terminal,
                    ],
                    trade_filled_size="0",
                    open_order_present=is_open_order_fact(state),
                )
                if reduced.proof_class in {TERMINAL_FILLED, TERMINAL_NO_FILL, TERMINAL_PARTIAL}:
                    return int(prior_terminal["fact_id"])
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
    """Append an ECONOMICALLY_CLOSED_OPTIMISTIC lot when a previously matched
    trade fails — the optimistic exposure it estimated never became real and
    is now closed/reversed (T5, docs/rebuild/quarantine_excision_2026-07-11.md:
    position_lots holds only active-exposure or closed-exposure values, never
    a review/quarantine scar — see src.contracts.canonical_lifecycle module
    docstring: "closure/exit/settlement/quarantine are derived elsewhere, not
    lot states"). Reuses an already-CHECK-permitted lot state so this needs no
    schema migration.
    """

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
           AND state = 'ECONOMICALLY_CLOSED_OPTIMISTIC'
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
        state="ECONOMICALLY_CLOSED_OPTIMISTIC",
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
        has_envelopes = _review_clearance_table_exists(conn, "venue_submission_envelopes")
        has_snapshots = _review_clearance_table_exists(conn, "executable_market_snapshots")
        env_select = (
            """
            env.condition_id AS env_condition_id,
            env.yes_token_id AS env_yes_token_id,
            env.no_token_id AS env_no_token_id,
            env.selected_outcome_token_id AS env_selected_outcome_token_id,
            env.outcome_label AS env_outcome_label,
            """
            if has_envelopes
            else ""
        )
        env_join = (
            "LEFT JOIN venue_submission_envelopes env ON env.envelope_id = cmd.envelope_id"
            if has_envelopes
            else ""
        )
        snapshot_select = (
            """
            snap.condition_id AS snapshot_condition_id,
            snap.yes_token_id AS snapshot_yes_token_id,
            snap.no_token_id AS snapshot_no_token_id,
            snap.selected_outcome_token_id AS snapshot_selected_outcome_token_id,
            snap.outcome_label AS snapshot_outcome_label,
            """
            if has_snapshots
            else ""
        )
        snapshot_join = (
            "LEFT JOIN executable_market_snapshots snap ON snap.snapshot_id = cmd.snapshot_id"
            if has_snapshots
            else ""
        )
        rows = conn.execute(
            f"""
            SELECT
              cmd.*,
              {env_select}
              {snapshot_select}
              cmd.command_id AS command_id
            FROM venue_commands cmd
            {env_join}
            {snapshot_join}
            WHERE cmd.state IN ({placeholders})
            """,
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
