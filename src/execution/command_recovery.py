# Lifecycle: created=2026-04-26; last_reviewed=2026-05-21; last_reused=2026-06-11
# Purpose: Command recovery loop for unresolved venue command side effects.
# Reuse: Run when command recovery, venue order payload normalization, or unknown side-effect resolution changes.
# Authority basis: docs/operations/task_2026-04-26_execution_state_truth_p1_command_bus/implementation_plan.md §P1.S4
#                  + docs/archive/2026-Q2/task_2026-05-15_live_order_e2e_goal/LIVE_ORDER_E2E_GOAL_PLAN.md
#                  + 2026-06-11 dependency_db_locked incident: the scheduled EDLI lane
#                    (conn is None) now runs the per-pass short-connection three-phase
#                    flow (src/execution/venue_sync_contract.py) so no DB connection is
#                    held across venue REST I/O. Legacy caller-owned-conn path unchanged.
"""Command recovery loop — INV-31.

At cycle start, scans venue_commands for rows in IN_FLIGHT_STATES and
reconciles currently-supported side-effect states against venue truth. M2 owns
SUBMIT_UNKNOWN_SIDE_EFFECT resolution: lookup by known venue_order_id or by
idempotency-key capability, then convert found orders to ACKED/PARTIAL/FILLED or
operator REVIEW_REQUIRED, or mark safe replay permitted via a terminal
SUBMIT_REJECTED payload after the window elapses. MATCHED/MINED trade facts are
optimistic venue observations; a PARTIAL entry command advances to FILLED only
when order truth says the remainder is gone and positive fill facts already
exist. Appends durable events that advance state per the §P1.S4 resolution
table. P2/K4 will add chain-truth reconciliation for FILL_CONFIRMED.

Chain reconciliation (FILL_CONFIRMED via on-chain settlement evidence) is OUT
of scope for P1.S4 — that requires deep chain-state integration. Deferred to
P2/K4 where chain authority is surfaced as a first-class seam.

Cross-DB note (per INV-30 caveat): venue_commands lives in zeus_trades.db.
When conn is not passed, this module opens its own trade connection via
get_trade_connection_with_world_required() and closes it in a try/finally. The live
cycle path passes its already-open trade/world connection to avoid a second
connection inside the same cycle.
"""
from __future__ import annotations

import hashlib
import logging
import json
import re
import sqlite3
import time
from dataclasses import replace
from collections.abc import Mapping
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from types import SimpleNamespace
from typing import Optional

from src.execution.command_bus import (
    CommandState,
    CommandEventType,
    IN_FLIGHT_STATES,
    IntentKind,
    VenueCommand,
)
from src.decision_kernel.canonicalization import canonical_json, stable_hash
from src.state.venue_command_repo import (
    find_unresolved_commands,
    append_event,
    append_order_fact,
    append_trade_fact,
    UNRESOLVED_SIDE_EFFECT_STATES,
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
_LIVE_ORDER_STATUSES = frozenset({"LIVE", "OPEN", "RESTING"})
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
_TERMINAL_NO_FILL_ORDER_FACT_STATES = frozenset({
    "CANCEL_CONFIRMED",
    "EXPIRED",
    "VENUE_WIPED",
})
_TERMINAL_NO_FILL_VENUE_STATUSES = frozenset({
    "CANCELLED",
    "CANCELED",
    "EXPIRED",
    "REJECTED",
})
_LIVE_TERMINAL_ORDER_FACT_SOURCES = frozenset({
    "REST",
    "WS_USER",
    "WS_MARKET",
    "DATA_API",
    "CHAIN",
})
_CANONICAL_STRATEGY_KEYS = frozenset({
    "settlement_capture",
    "shoulder_sell",
    "center_buy",
    "opening_inertia",
})
_LEGACY_STRATEGY_KEY_ALIASES = {
    "imminent_open_capture": "opening_inertia",
}
_ACKED_ORDER_STATES = frozenset({
    CommandState.ACKED.value,
    CommandState.POST_ACKED.value,
})
_PARTIAL_REMAINDER_STATES = frozenset({
    CommandState.PARTIAL.value,
    CommandState.FILLED.value,
})
_EXIT_PENDING_PROJECTION_COMMAND_STATES = frozenset({
    CommandState.ACKED.value,
    CommandState.POST_ACKED.value,
    CommandState.PARTIAL.value,
    CommandState.FILLED.value,
})
_EXIT_PENDING_PROJECTION_TRADE_STATES = frozenset({
    "MATCHED",
    "MINED",
})
_EXIT_LIFECYCLE_REPAIR_COMMAND_STATES = frozenset({
    CommandState.ACKED.value,
    CommandState.POST_ACKED.value,
    CommandState.PARTIAL.value,
})
_EXIT_LIVE_ORDER_FACT_STATES = frozenset({
    "LIVE",
    "OPEN",
    "RESTING",
    "PARTIALLY_MATCHED",
    "PARTIAL",
})
_EXIT_FILL_ORDER_FACT_STATES = frozenset({
    "MATCHED",
    "FILLED",
})
_EXIT_LIVE_ORDER_RESTORE_PHASES = frozenset({
    "active",
    "day0_window",
    "quarantined",
})
_SHIFT_BIN_EXIT_ACTIVE_STATUSES = frozenset({
    "EXIT_SUBMITTED",
    "EXIT_UNKNOWN",
    "EXIT_PARTIAL",
})
_SHIFT_BIN_ENTRY_TERMINAL_NO_POSITION_STATES = frozenset({
    "CANCELED",
    "CANCELLED",
    "EXPIRED",
    "FAILED",
    "REJECTED",
    "SUBMIT_REJECTED",
    "VOIDED",
})
_REBALANCE_LIVE_EXPOSURE_PHASES = frozenset({
    "",
    "open",
    "pending",
    "pending_entry",
    "pending_tracked",
    "active",
    "entered",
    "holding",
    "day0_window",
    "pending_exit",
    "acked",
    "live",
    "partial",
    "partially_filled",
    "filled",
    "submitted",
    "submit_unknown_side_effect",
    "unknown",
    "review_required",
})
_SAFE_REPLAY_MIN_AGE_SECONDS = 15 * 60
_POST_ACK_PERSISTENCE_REVIEW_REASONS = frozenset({
    "entry_ack_persistence_failed_after_side_effect",
    "exit_ack_persistence_failed_after_side_effect",
})


def _canonical_order_truth_cte(
    *,
    cte_name: str = "canonical_order_truth",
    partition_by_venue_order: bool = False,
) -> str:
    """SQL CTE that prevents weaker later order facts from demoting truth.

    Recovery used to treat ``MAX(local_sequence)`` as authority. That is unsafe
    when a later RESTING/PARTIAL observation arrives after an earlier terminal
    fill/no-fill proof. The reducer ranks proof strength first, recency second.
    """

    partition = "command_id, venue_order_id" if partition_by_venue_order else "command_id"
    return f"""
        {cte_name} AS (
            SELECT ranked.*
              FROM (
                    SELECT scored.*,
                           ROW_NUMBER() OVER (
                               PARTITION BY {partition}
                               ORDER BY proof_rank DESC, local_sequence DESC
                           ) AS canonical_rank
                      FROM (
                            SELECT fact.*,
                                   CASE
                                       WHEN UPPER(COALESCE(fact.state, '')) IN ('MATCHED', 'FILLED')
                                            AND CAST(COALESCE(fact.matched_size, '0') AS REAL) > 0
                                            AND CAST(COALESCE(fact.remaining_size, '0') AS REAL) = 0
                                       THEN 600
                                       WHEN UPPER(COALESCE(fact.state, '')) IN ('CANCEL_CONFIRMED', 'EXPIRED', 'VENUE_WIPED')
                                            AND CAST(COALESCE(fact.matched_size, '0') AS REAL) > 0
                                       THEN 550
                                       WHEN UPPER(COALESCE(fact.state, '')) IN ('PARTIALLY_MATCHED', 'PARTIAL')
                                            AND CAST(COALESCE(fact.matched_size, '0') AS REAL) > 0
                                       THEN 400
                                       WHEN UPPER(COALESCE(fact.state, '')) IN ('CANCEL_CONFIRMED', 'EXPIRED', 'VENUE_WIPED')
                                            AND CAST(COALESCE(fact.matched_size, '0') AS REAL) = 0
                                       THEN 300
                                       WHEN UPPER(COALESCE(fact.state, '')) IN ('LIVE', 'OPEN', 'RESTING')
                                       THEN 200
                                       ELSE 100
                                   END AS proof_rank
                              FROM venue_order_facts fact
                           ) scored
                   ) ranked
             WHERE ranked.canonical_rank = 1
        )
    """


def _canonical_trade_fact_cte(cte_name: str = "canonical_trade_fact") -> str:
    """SQL CTE that prevents weaker later trade facts from hiding fills."""

    return f"""
        {cte_name} AS (
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
    """


class MissingPositionCurrentForTerminalOrder(ValueError):
    """Raised when terminal order facts arrive before the entry projection."""


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


def _venue_order_payload(value: object | None) -> dict | None:
    """Normalize live adapter order objects to a JSON-safe venue payload."""

    if value is None:
        return None
    if isinstance(value, Mapping):
        payload = dict(value)
    else:
        raw = getattr(value, "raw", None)
        if isinstance(raw, Mapping):
            payload = dict(raw)
        else:
            payload = dict(getattr(value, "__dict__", {}) or {})
    status = getattr(value, "status", None)
    if status not in (None, "") and not (payload.get("status") or payload.get("state")):
        payload["status"] = str(status)
    order_id = getattr(value, "order_id", None)
    if order_id not in (None, "") and not _extract_order_id(payload):
        payload["orderID"] = str(order_id)
    if not (_extract_order_id(payload) or payload.get("status") or payload.get("state")):
        return None
    return payload


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


def _order_status(raw: dict) -> str:
    return str(raw.get("status") or raw.get("state") or "").upper()


def _order_matched_size(raw: dict) -> str:
    value = (
        raw.get("matched_size")
        or raw.get("size_matched")
        or raw.get("matched")
        or raw.get("matched_amount")
        or raw.get("filled_size")
        or "0"
    )
    return str(value)


def _explicit_point_order_matched_size(point_order: dict | None) -> str | None:
    """Matched/filled size only when the venue point-order payload says it explicitly.

    Do not infer from making/taking amounts here: for canceled/expired orders those
    fields can describe original order amounts, not executed exposure. Terminal
    no-fill release needs a positive zero-fill proof, not a default.
    """

    value = _first_present(
        point_order,
        "matched_size",
        "matchedSize",
        "size_matched",
        "sizeMatched",
        "matched",
        "matched_amount",
        "matchedAmount",
        "filled_size",
        "filledSize",
    )
    if value in (None, ""):
        return None
    return str(value)


def _terminal_point_order_zero_fill_proven(
    conn: sqlite3.Connection,
    *,
    command_id: str,
    point_order: dict | None,
) -> tuple[bool, str]:
    """Return whether a terminal venue point-order proves zero fill.

    A terminal venue status alone is not enough: CLOB orders can be canceled or
    expire after partial matching. We release duplicate locks only when the point
    order explicitly reports matched/filled size == 0 and both local trade facts
    and point-order trade ids are absent.
    """

    matched_size = _explicit_point_order_matched_size(point_order)
    if matched_size is None:
        return False, "terminal_matched_size_missing"
    if not _decimal_is_zero(matched_size):
        return False, "terminal_matched_size_positive_or_invalid"
    if _fill_trade_fact_count(conn, command_id) > 0:
        return False, "terminal_positive_trade_fact_exists"
    if _point_order_trade_ids(point_order):
        return False, "terminal_point_order_trade_ids_present"
    return True, "terminal_zero_fill_proven"


def _is_positive_decimal(value: object) -> bool:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return False
    return parsed.is_finite() and parsed > 0


def _lookup_unknown_side_effect_order(
    cmd: VenueCommand,
    client,
) -> tuple[str, dict | None, dict | None, str]:
    """Return (status, venue_response, proof, lookup_method).

    status is one of ``found`` or ``unavailable``.  A ``found`` status with a
    ``None`` response means the venue read completed and found no matching
    exposure, allowing the age-gated safe-replay path below to terminalize the
    command.
    """

    if cmd.venue_order_id:
        return (
            "found",
            _venue_order_payload(client.get_order(cmd.venue_order_id)),
            None,
            "venue_order_id",
        )
    finder = getattr(client, "find_order_by_idempotency_key", None)
    if callable(finder):
        found = finder(cmd.idempotency_key.value)
        if found is None:
            return "found", None, None, "idempotency_key"
        payload = _venue_order_payload(found)
        if payload is not None:
            return "found", payload, None, "idempotency_key"
        logger.warning(
            "recovery: command %s idempotency-key lookup returned non-order %s; "
            "treating lookup as unavailable",
            cmd.command_id, type(found).__name__,
        )
        return "unavailable", None, None, "idempotency_key"
    venue_status, venue_resp, proof = _lookup_unknown_side_effect_by_venue_reads(cmd, client)
    return venue_status, venue_resp, proof, "authenticated_venue_absence"


def _client_read_items(client, method_name: str) -> SimpleNamespace:
    adapter_factory = getattr(client, "_ensure_v2_adapter", None)
    if callable(adapter_factory):
        adapter = adapter_factory()
        adapter_reader = getattr(adapter, method_name, None)
        if callable(adapter_reader):
            return SimpleNamespace(
                items=list(adapter_reader() or []),
                query_complete=True,
                pagination_scope=f"{adapter.__class__.__name__}.{method_name}:all_pages",
            )
    reader = getattr(client, method_name, None)
    if callable(reader):
        query_complete = bool(
            getattr(client, "venue_reads_are_complete", False)
            or getattr(reader, "venue_reads_are_complete", False)
        )
        return SimpleNamespace(
            items=list(reader() or []),
            query_complete=query_complete,
            pagination_scope=(
                f"{client.__class__.__name__}.{method_name}:declared_complete"
                if query_complete
                else f"{client.__class__.__name__}.{method_name}:single_call_unverified"
            ),
        )
    raise AttributeError(f"client does not expose {method_name}")


def _command_mapping(cmd: VenueCommand) -> dict:
    return {
        "command_id": cmd.command_id,
        "decision_id": cmd.decision_id,
        "market_id": cmd.market_id,
        "token_id": cmd.token_id,
        "side": cmd.side,
        "price": cmd.price,
        "size": cmd.size,
        "created_at": cmd.created_at,
    }


def _raw_matches_command_order_identity(raw: dict, command: dict) -> bool:
    token_id = str(command.get("token_id") or "")
    if not token_id or not _raw_mentions_token(raw, token_id):
        return False
    raw_side = str(raw.get("side") or "").upper()
    side = str(command.get("side") or "").upper()
    if raw_side and side and raw_side != side:
        return False
    if not _decimal_matches(raw.get("price"), command.get("price")):
        return False
    raw_size = raw.get("size") or raw.get("original_size") or raw.get("matched_amount")
    return _decimal_matches(raw_size, command.get("size"))


def _lookup_unknown_side_effect_by_venue_reads(
    cmd: VenueCommand,
    client,
) -> tuple[str, dict | None, dict | None]:
    try:
        open_read = _client_read_items(client, "get_open_orders")
        trade_read = _client_read_items(client, "get_trades")
    except Exception as exc:
        logger.warning(
            "recovery: command %s authenticated venue absence read unavailable: %s",
            cmd.command_id, exc,
        )
        return "unavailable", None, None
    open_orders = open_read.items
    trades = trade_read.items

    command = _command_mapping(cmd)
    created_epoch = _epoch_seconds(cmd.created_at) or 0.0
    matching_open = []
    exact_open = []
    for item in open_orders:
        raw = _raw_payload(item)
        if not _raw_matches_command_exposure(raw, command):
            continue
        matching_open.append(raw)
        if _raw_matches_command_order_identity(raw, command):
            exact_open.append(raw)

    matching_trades = []
    for item in trades:
        raw = _raw_payload(item)
        if not _raw_matches_command_exposure(raw, command):
            continue
        trade_epoch = _epoch_seconds(raw.get("match_time") or raw.get("last_update"))
        if trade_epoch is not None and trade_epoch < created_epoch:
            continue
        matching_trades.append(raw)

    proof = {
        "schema_version": 1,
        "source": "authenticated_clob_user_read",
        "owner_scope": "authenticated_funder",
        "observed_at": _now_iso(),
        "command_id": cmd.command_id,
        "decision_id": cmd.decision_id,
        "market_id": cmd.market_id,
        "token_id": cmd.token_id,
        "side": cmd.side,
        "price": str(Decimal(str(cmd.price))),
        "size": str(Decimal(str(cmd.size))),
        "open_orders_checked": True,
        "trades_checked": True,
        "open_orders_query_complete": bool(open_read.query_complete),
        "trades_query_complete": bool(trade_read.query_complete),
        "pagination_scope": {
            "open_orders": open_read.pagination_scope,
            "trades": trade_read.pagination_scope,
        },
        "time_window_start": cmd.created_at,
        "time_window_end": _now_iso(),
        "open_order_count": len(open_orders),
        "trade_count": len(trades),
        "matching_open_order_count": len(matching_open),
        "matching_trade_count": len(matching_trades),
        "matching_open_orders": [_summarize_venue_match(raw) for raw in matching_open[:10]],
        "matching_trades": [_summarize_venue_match(raw) for raw in matching_trades[:10]],
    }

    if len(exact_open) == 1:
        payload = dict(exact_open[0])
        payload.setdefault("status", payload.get("state") or "LIVE")
        return "found", payload, proof
    if matching_open or matching_trades:
        return "unavailable", None, proof
    if not (open_read.query_complete and trade_read.query_complete):
        return "unavailable", None, proof
    return "found", None, proof


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

_GEOBLOCK_403_MARKERS = (
    "status_code=403",
    "Trading restricted in your region",
    "geoblock",
)
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


def _attached_table_exists(conn: sqlite3.Connection, schema: str, table: str) -> bool:
    row = conn.execute(
        f"SELECT 1 FROM {schema}.sqlite_master WHERE type = 'table' AND name = ?",
        (table,),
    ).fetchone()
    return row is not None


def _table_has_columns(conn: sqlite3.Connection, table_ref: str, required: set[str]) -> bool:
    return required.issubset(_table_columns(conn, table_ref))


def _maybe_attach_world_for_recovery(conn: sqlite3.Connection) -> None:
    attached = {str(row[1]) for row in conn.execute("PRAGMA database_list").fetchall()}
    if "world" in attached:
        return
    main_path = ""
    for row in conn.execute("PRAGMA database_list").fetchall():
        if str(row[1]) == "main":
            main_path = str(row[2] or "")
            break
    if not main_path or Path(main_path).name != "zeus_trades.db":
        return
    try:
        from src.state.db import ZEUS_WORLD_DB_PATH

        if ZEUS_WORLD_DB_PATH.exists():
            conn.execute("ATTACH DATABASE ? AS world", (str(ZEUS_WORLD_DB_PATH),))
    except sqlite3.OperationalError:
        logger.debug("command recovery could not attach world DB", exc_info=True)


def _maybe_attach_forecasts_for_recovery(conn: sqlite3.Connection) -> None:
    attached = {str(row[1]) for row in conn.execute("PRAGMA database_list").fetchall()}
    if "forecasts" in attached:
        return
    main_path = ""
    for row in conn.execute("PRAGMA database_list").fetchall():
        if str(row[1]) == "main":
            main_path = str(row[2] or "")
            break
    if not main_path or Path(main_path).name != "zeus_trades.db":
        return
    try:
        from src.state.db import ZEUS_FORECASTS_DB_PATH

        if ZEUS_FORECASTS_DB_PATH.exists():
            conn.execute("ATTACH DATABASE ? AS forecasts", (str(ZEUS_FORECASTS_DB_PATH),))
    except sqlite3.OperationalError:
        logger.debug("command recovery could not attach forecasts DB", exc_info=True)


def _edli_live_order_events_ref(conn: sqlite3.Connection) -> str | None:
    _maybe_attach_world_for_recovery(conn)
    attached = {str(row[1]) for row in conn.execute("PRAGMA database_list").fetchall()}
    if "world" in attached and _attached_table_exists(conn, "world", "edli_live_order_events"):
        return "world.edli_live_order_events"
    if _table_exists(conn, "edli_live_order_events"):
        return "edli_live_order_events"
    return None


def _edli_live_order_projection_ref(conn: sqlite3.Connection) -> str | None:
    _maybe_attach_world_for_recovery(conn)
    attached = {str(row[1]) for row in conn.execute("PRAGMA database_list").fetchall()}
    if "world" in attached and _attached_table_exists(conn, "world", "edli_live_order_projection"):
        return "world.edli_live_order_projection"
    if _table_exists(conn, "edli_live_order_projection"):
        return "edli_live_order_projection"
    return None


def _edli_live_cap_ref(conn: sqlite3.Connection, table: str) -> str | None:
    _maybe_attach_world_for_recovery(conn)
    attached = {str(row[1]) for row in conn.execute("PRAGMA database_list").fetchall()}
    if "world" in attached and _attached_table_exists(conn, "world", table):
        return f"world.{table}"
    if _table_exists(conn, table):
        return table
    return None


def _decision_certificates_ref(conn: sqlite3.Connection) -> str | None:
    _maybe_attach_world_for_recovery(conn)
    attached = {str(row[1]) for row in conn.execute("PRAGMA database_list").fetchall()}
    if "world" in attached and _attached_table_exists(conn, "world", "decision_certificates"):
        return "world.decision_certificates"
    if _table_exists(conn, "decision_certificates"):
        return "decision_certificates"
    return None


def _decision_integrity_quarantine_ref(conn: sqlite3.Connection) -> str | None:
    attached = {str(row[1]) for row in conn.execute("PRAGMA database_list").fetchall()}
    if "trade" in attached and _attached_table_exists(conn, "trade", "decision_integrity_quarantine"):
        return "trade.decision_integrity_quarantine"
    if "main" in attached and _table_exists(conn, "decision_integrity_quarantine"):
        return "decision_integrity_quarantine"
    return None


def _market_events_ref(conn: sqlite3.Connection) -> str | None:
    _maybe_attach_forecasts_for_recovery(conn)
    _maybe_attach_world_for_recovery(conn)
    attached = {str(row[1]) for row in conn.execute("PRAGMA database_list").fetchall()}
    required = {
        "city",
        "target_date",
        "range_label",
        "outcome",
        "temperature_metric",
        "condition_id",
    }
    if (
        "forecasts" in attached
        and _attached_table_exists(conn, "forecasts", "market_events")
        and _table_has_columns(conn, "forecasts.market_events", required)
    ):
        return "forecasts.market_events"
    if (
        "world" in attached
        and _attached_table_exists(conn, "world", "market_events")
        and _table_has_columns(conn, "world.market_events", required)
    ):
        return "world.market_events"
    if _table_exists(conn, "market_events") and _table_has_columns(conn, "market_events", required):
        return "market_events"
    return None


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


def _command_snapshot(conn: sqlite3.Connection, snapshot_id: str | None) -> dict:
    if not snapshot_id:
        return {}
    row = conn.execute(
        "SELECT * FROM executable_market_snapshots WHERE snapshot_id = ?",
        (snapshot_id,),
    ).fetchone()
    return _dict_row(row)


def _hydrate_command_execution_identity(conn: sqlite3.Connection, command: dict) -> dict:
    """Add immutable envelope/snapshot identity aliases to a bare command row."""

    hydrated = dict(command)
    envelope = _command_envelope(conn, str(command.get("envelope_id") or "").strip())
    snapshot = _command_snapshot(conn, str(command.get("snapshot_id") or "").strip())
    for prefix, source in (("env", envelope), ("snapshot", snapshot)):
        if not source:
            continue
        for column, alias in (
            ("condition_id", f"{prefix}_condition_id"),
            ("yes_token_id", f"{prefix}_yes_token_id"),
            ("no_token_id", f"{prefix}_no_token_id"),
            ("selected_outcome_token_id", f"{prefix}_selected_outcome_token_id"),
            ("outcome_label", f"{prefix}_outcome_label"),
        ):
            if hydrated.get(alias) in (None, "") and source.get(column) not in (None, ""):
                hydrated[alias] = source.get(column)
        if prefix == "snapshot":
            for column, alias in (
                ("event_slug", "snapshot_event_slug"),
                ("gamma_market_id", "snapshot_gamma_market_id"),
            ):
                if hydrated.get(alias) in (None, "") and source.get(column) not in (None, ""):
                    hydrated[alias] = source.get(column)
    return hydrated


def _count_facts(conn: sqlite3.Connection, table: str, command_id: str) -> int:
    if not _table_exists(conn, table):
        return 0
    if table == "venue_order_facts":
        row = conn.execute(
            "SELECT COUNT(*) AS count FROM venue_order_facts WHERE command_id = ?",
            (command_id,),
        ).fetchone()
    elif table == "venue_trade_facts":
        row = conn.execute(
            "SELECT COUNT(*) AS count FROM venue_trade_facts WHERE command_id = ?",
            (command_id,),
        ).fetchone()
    else:
        raise ValueError(f"unsupported fact table: {table}")
    if row is None:
        return 0
    data = _dict_row(row)
    return int(data.get("count", 0) or 0)


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    try:
        if "." in table:
            schema, table_name = table.split(".", 1)
            if not _attached_table_exists(conn, schema, table_name):
                return set()
            rows = conn.execute(f"PRAGMA {schema}.table_info({table_name})").fetchall()
        else:
            if not _table_exists(conn, table):
                return set()
            rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
        return {str(row[1]) for row in rows}
    except sqlite3.OperationalError:
        return set()


def _count_position_rows_for_command(conn: sqlite3.Connection, command: dict) -> dict[str, int]:
    command_id = str(command.get("command_id") or "")
    position_id = str(command.get("position_id") or "")
    counts = {"position_events": 0, "position_current": 0}
    event_cols = _table_columns(conn, "position_events")
    if "command_id" in event_cols:
        row = conn.execute(
            "SELECT COUNT(*) AS count FROM position_events WHERE command_id = ?",
            (command_id,),
        ).fetchone()
        counts["position_events"] = int((_dict_row(row).get("count") if row else 0) or 0)
    current_cols = _table_columns(conn, "position_current")
    if position_id and "position_id" in current_cols:
        row = conn.execute(
            "SELECT COUNT(*) AS count FROM position_current WHERE position_id = ?",
            (position_id,),
        ).fetchone()
        counts["position_current"] = int((_dict_row(row).get("count") if row else 0) or 0)
    return counts


def _decimal_is_zero(value: object) -> bool:
    if value in (None, ""):
        return False
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return False
    return parsed.is_finite() and parsed == 0


def _decimal_is_positive(value: object) -> bool:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return False
    return parsed.is_finite() and parsed > 0


def _decimal_or_none(value: object) -> Decimal | None:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return parsed if parsed.is_finite() else None


def _positive_decimal_or_none(value: object) -> Decimal | None:
    parsed = _decimal_or_none(value)
    return parsed if parsed is not None and parsed > 0 else None


def _positive_probability_or_none(value: object) -> float | None:
    parsed = _decimal_or_none(value)
    if parsed is None or parsed <= 0 or parsed > 1:
        return None
    return float(parsed)


def _float_or_none(value: object) -> float | None:
    parsed = _decimal_or_none(value)
    return float(parsed) if parsed is not None else None


def _family_rebalance_intents_table_ref(conn: sqlite3.Connection) -> str | None:
    """Return the reachable family_rebalance_intents table for this connection."""

    if _table_exists(conn, "family_rebalance_intents"):
        return "family_rebalance_intents"
    _maybe_attach_world_for_recovery(conn)
    try:
        if _attached_table_exists(conn, "world", "family_rebalance_intents"):
            return "world.family_rebalance_intents"
    except sqlite3.Error:
        return None
    return None


def _live_position_exposure_for_token_usd(
    conn: sqlite3.Connection,
    *,
    token_id: str,
) -> Decimal | None:
    """Read live/in-flight exposure for a held token.

    ``None`` means ambiguous schema/read failure and must not release a lease.
    ``Decimal(0)`` means no positive live/in-flight position row remains.
    """

    token = str(token_id or "").strip()
    if not token:
        return None
    cols = _table_columns(conn, "position_current")
    token_cols = [c for c in ("token_id", "no_token_id") if c in cols]
    cost_cols = [c for c in ("chain_cost_basis_usd", "cost_basis_usd", "size_usd") if c in cols]
    if "phase" not in cols or not token_cols or not cost_cols:
        return None
    phase_sql = ",".join("?" for _ in _REBALANCE_LIVE_EXPOSURE_PHASES)
    token_sql = " OR ".join(f"NULLIF({c}, '') = ?" for c in token_cols)
    positive_sql = " OR ".join(f"COALESCE({c}, 0) > 0" for c in cost_cols)
    select_sql = ", ".join(cost_cols)
    params: list[object] = [
        *sorted(_REBALANCE_LIVE_EXPOSURE_PHASES),
        *(token for _ in token_cols),
    ]
    try:
        rows = conn.execute(
            f"""
            SELECT {select_sql}
              FROM position_current
             WHERE phase IN ({phase_sql})
               AND ({token_sql})
               AND ({positive_sql})
             ORDER BY updated_at DESC
            """,
            tuple(params),
        ).fetchall()
    except sqlite3.Error:
        return None
    max_exposure = Decimal("0")
    for row in rows:
        data = _dict_row(row)
        if not data and not isinstance(row, sqlite3.Row):
            data = {cost_cols[i]: row[i] for i in range(min(len(cost_cols), len(row)))}
        for col in cost_cols:
            parsed = _decimal_or_none(data.get(col))
            if parsed is not None and parsed > max_exposure:
                max_exposure = parsed
    return max_exposure


def release_closed_shift_bin_exit_leases(
    conn: sqlite3.Connection,
    *,
    observed_at: str | datetime | None = None,
) -> dict:
    """Release SHIFT_BIN exit leases whose old leg is already economically closed.

    Close-before-open requires the family to stay locked while the old leg has live
    exposure. Once canonical ``position_current`` no longer has positive live exposure
    for the held token, the lease has served its purpose. Keeping it active until a
    later candidate happens to recapture ``may_submit`` deadlocks redecision and fresh
    entries for the family.
    """

    summary = {"scanned": 0, "advanced": 0, "stayed": 0, "errors": 0}
    table_ref = _family_rebalance_intents_table_ref(conn)
    if table_ref is None:
        return summary
    required = {"intent_id", "operation", "status", "held_token_id"}
    if not _table_has_columns(conn, table_ref, required):
        return summary
    now_iso = (
        observed_at.isoformat()
        if isinstance(observed_at, datetime)
        else str(observed_at or _now_iso())
    )
    statuses = tuple(sorted(_SHIFT_BIN_EXIT_ACTIVE_STATUSES))
    placeholders = ",".join("?" for _ in statuses)
    try:
        rows = conn.execute(
            f"""
            SELECT intent_id, status, held_token_id
              FROM {table_ref}
             WHERE operation = 'SHIFT_BIN'
               AND status IN ({placeholders})
             ORDER BY updated_at ASC
            """,
            statuses,
        ).fetchall()
    except sqlite3.Error:
        summary["errors"] += 1
        return summary

    for row in rows:
        summary["scanned"] += 1
        data = _dict_row(row)
        intent_id = str(data.get("intent_id") or "")
        held_token_id = str(data.get("held_token_id") or "")
        exposure = _live_position_exposure_for_token_usd(conn, token_id=held_token_id)
        if not intent_id or exposure is None:
            summary["stayed"] += 1
            continue
        if exposure > 0:
            summary["stayed"] += 1
            continue
        try:
            cur = conn.execute(
                f"""
                UPDATE {table_ref}
                   SET status = 'EXIT_ONLY_COMPLETE',
                       updated_at = ?,
                       abort_reason = COALESCE(
                           abort_reason,
                           'SHIFT_BIN_OLD_LEG_ECONOMICALLY_CLOSED_BY_COMMAND_RECOVERY'
                       )
                 WHERE intent_id = ?
                   AND operation = 'SHIFT_BIN'
                   AND status IN ({placeholders})
                """,
                (now_iso, intent_id, *statuses),
            )
        except sqlite3.Error:
            summary["errors"] += 1
            continue
        if int(cur.rowcount or 0) > 0:
            summary["advanced"] += 1
        else:
            summary["stayed"] += 1
    return summary


def release_stale_rebalance_entry_leases(
    conn: sqlite3.Connection,
    *,
    observed_at: str | datetime | None = None,
    min_age_seconds: int = 20 * 60,
) -> dict:
    """Release stale rebalance entry leases that no longer protect venue state.

    These leases sit after the old-leg close decision and before/around the
    counter-entry submit. If the counter-entry order later cancels or expires with
    no selected-token exposure, keeping the lease active suppresses the next fresh
    redecision forever. A FILLED command without a position projection is not
    released here; that is ambiguous live exposure and must be repaired by fill
    projection/reconciliation first.
    """

    summary = {
        "advanced": 0,
        "stayed": 0,
        "planned_fill_up_released": 0,
        "shift_entry_scanned": 0,
        "shift_entry_advanced": 0,
        "shift_entry_stayed": 0,
        "errors": 0,
    }
    table_ref = _family_rebalance_intents_table_ref(conn)
    if table_ref is None:
        return summary
    required = {
        "intent_id",
        "event_id",
        "operation",
        "status",
        "selected_token_id",
        "new_entry_command_id",
        "updated_at",
    }
    if not _table_has_columns(conn, table_ref, required):
        return summary
    now_iso = (
        observed_at.isoformat()
        if isinstance(observed_at, datetime)
        else str(observed_at or _now_iso())
    )

    try:
        cur = conn.execute(
            f"""
            UPDATE {table_ref}
               SET status = 'ABORTED',
                   updated_at = ?,
                   abort_reason = COALESCE(
                       abort_reason,
                       'FILL_UP_PLANNED_STALE_NO_DURABLE_COMMAND_RECOVERED'
                   )
             WHERE operation = 'FILL_UP'
               AND status = 'PLANNED'
               AND COALESCE(new_entry_command_id, '') = ''
               AND datetime(updated_at) <= datetime(?, ?)
            """,
            (now_iso, now_iso, f"-{int(min_age_seconds)} seconds"),
        )
        summary["planned_fill_up_released"] = int(cur.rowcount or 0)
    except sqlite3.Error:
        summary["errors"] += 1

    try:
        rows = conn.execute(
            f"""
            SELECT intent_id, event_id, selected_token_id, updated_at
              FROM {table_ref}
             WHERE operation = 'SHIFT_BIN'
               AND status = 'ENTRY_SUBMITTED'
             ORDER BY updated_at ASC
            """
        ).fetchall()
    except sqlite3.Error:
        summary["errors"] += 1
        return summary

    terminal_placeholders = ",".join("?" for _ in _SHIFT_BIN_ENTRY_TERMINAL_NO_POSITION_STATES)
    for row in rows:
        summary["shift_entry_scanned"] += 1
        data = _dict_row(row)
        intent_id = str(data.get("intent_id") or "").strip()
        event_id = str(data.get("event_id") or "").strip()
        selected_token_id = str(data.get("selected_token_id") or "").strip()
        updated_at = str(data.get("updated_at") or "").strip()
        if not intent_id or not selected_token_id:
            summary["shift_entry_stayed"] += 1
            continue

        exposure = _live_position_exposure_for_token_usd(conn, token_id=selected_token_id)
        if exposure is None:
            summary["shift_entry_stayed"] += 1
            continue
        if exposure > 0:
            try:
                cur = conn.execute(
                    f"""
                    UPDATE {table_ref}
                       SET status = 'COMPLETE',
                           updated_at = ?,
                           abort_reason = COALESCE(
                               abort_reason,
                               'SHIFT_BIN_COUNTER_ENTRY_POSITION_PRESENT_BY_COMMAND_RECOVERY'
                           )
                     WHERE intent_id = ?
                       AND operation = 'SHIFT_BIN'
                       AND status = 'ENTRY_SUBMITTED'
                    """,
                    (now_iso, intent_id),
                )
            except sqlite3.Error:
                summary["errors"] += 1
                continue
            if int(cur.rowcount or 0) > 0:
                summary["shift_entry_advanced"] += 1
            else:
                summary["shift_entry_stayed"] += 1
            continue

        command_row = None
        if _table_exists(conn, "venue_commands"):
            try:
                params: list[object] = [selected_token_id]
                event_filter = ""
                if event_id:
                    event_filter = " AND decision_id LIKE ?"
                    params.append(f"edli_exec_cmd:{event_id}:%")
                command_row = conn.execute(
                    f"""
                    SELECT command_id, state
                      FROM venue_commands
                     WHERE intent_kind = 'ENTRY'
                       AND token_id = ?
                       {event_filter}
                     ORDER BY updated_at DESC, created_at DESC
                     LIMIT 1
                    """,
                    tuple(params),
                ).fetchone()
            except sqlite3.Error:
                summary["errors"] += 1
                summary["shift_entry_stayed"] += 1
                continue

        release_reason: str | None = None
        if command_row is not None:
            command = _dict_row(command_row)
            state = str(command.get("state") or "").strip().upper()
            if state in _SHIFT_BIN_ENTRY_TERMINAL_NO_POSITION_STATES:
                release_reason = (
                    "SHIFT_BIN_ENTRY_TERMINAL_NO_POSITION_BY_COMMAND_RECOVERY:"
                    f"state={state}"
                )
        elif updated_at:
            try:
                age_row = conn.execute(
                    "SELECT datetime(?) <= datetime(?, ?)",
                    (updated_at, now_iso, f"-{int(min_age_seconds)} seconds"),
                ).fetchone()
                is_stale = bool(age_row[0]) if age_row is not None else False
            except sqlite3.Error:
                is_stale = False
            if is_stale:
                release_reason = "SHIFT_BIN_ENTRY_STALE_NO_DURABLE_COMMAND_RECOVERED"

        if release_reason is None:
            summary["shift_entry_stayed"] += 1
            continue

        try:
            cur = conn.execute(
                f"""
                UPDATE {table_ref}
                   SET status = 'ABORTED',
                       updated_at = ?,
                       abort_reason = COALESCE(abort_reason, ?)
                 WHERE intent_id = ?
                   AND operation = 'SHIFT_BIN'
                   AND status = 'ENTRY_SUBMITTED'
                """,
                (now_iso, release_reason, intent_id),
            )
        except sqlite3.Error:
            summary["errors"] += 1
            continue
        if int(cur.rowcount or 0) > 0:
            summary["shift_entry_advanced"] += 1
        else:
            summary["shift_entry_stayed"] += 1
    summary["advanced"] = int(summary["planned_fill_up_released"]) + int(summary["shift_entry_advanced"])
    summary["stayed"] = int(summary["shift_entry_stayed"])
    return summary


def _position_strategy_key(conn: sqlite3.Connection, position_id: str) -> str | None:
    if not _table_exists(conn, "position_current"):
        return None
    row = conn.execute(
        "SELECT strategy_key FROM position_current WHERE position_id = ?",
        (position_id,),
    ).fetchone()
    return str(row["strategy_key"] or "") if row and row["strategy_key"] else None


def _latest_terminal_order_fact_candidates(conn: sqlite3.Connection) -> list[dict]:
    if not _table_exists(conn, "venue_order_facts"):
        return []
    states = tuple(sorted(_TERMINAL_NO_FILL_ORDER_FACT_STATES))
    command_states = tuple(
        sorted(
            {
                *_ACKED_ORDER_STATES,
                CommandState.CANCEL_PENDING.value,
                CommandState.CANCELLED.value,
                CommandState.EXPIRED.value,
            }
        )
    )
    sources = tuple(sorted(_LIVE_TERMINAL_ORDER_FACT_SOURCES))
    sql = "WITH " + _canonical_order_truth_cte() + """
        SELECT
            cmd.*,
            fact.fact_id AS order_fact_id,
            fact.state AS order_fact_state,
            fact.observed_at AS order_fact_observed_at,
            fact.venue_order_id AS order_fact_venue_order_id,
            fact.remaining_size AS order_fact_remaining_size,
            fact.matched_size AS order_fact_matched_size,
            fact.source AS order_fact_source,
            fact.raw_payload_hash AS order_fact_raw_payload_hash,
            env.condition_id AS env_condition_id,
            env.yes_token_id AS env_yes_token_id,
            env.no_token_id AS env_no_token_id,
            env.selected_outcome_token_id AS env_selected_outcome_token_id,
            env.outcome_label AS env_outcome_label,
            snap.condition_id AS snapshot_condition_id,
            snap.yes_token_id AS snapshot_yes_token_id,
            snap.no_token_id AS snapshot_no_token_id,
            snap.selected_outcome_token_id AS snapshot_selected_outcome_token_id,
            snap.outcome_label AS snapshot_outcome_label
          FROM venue_commands cmd
          JOIN canonical_order_truth fact
            ON fact.command_id = cmd.command_id
          LEFT JOIN position_current pc
            ON pc.position_id = cmd.position_id
          LEFT JOIN venue_submission_envelopes env
            ON env.envelope_id = cmd.envelope_id
          LEFT JOIN executable_market_snapshots snap
            ON snap.snapshot_id = cmd.snapshot_id
         WHERE cmd.intent_kind = 'ENTRY'
           AND cmd.state IN (?, ?, ?, ?, ?)
           AND (
                cmd.state IN ('ACKED', 'POST_ACKED', 'CANCEL_PENDING')
                OR pc.position_id IS NULL
                OR (
                    cmd.state IN ('CANCELLED', 'EXPIRED')
                    AND pc.phase = 'pending_entry'
                    AND CAST(COALESCE(pc.shares, '0') AS REAL) = 0
                    AND CAST(COALESCE(pc.cost_basis_usd, '0') AS REAL) = 0
                )
           )
           AND fact.state IN (?, ?, ?)
           AND fact.source IN (?, ?, ?, ?, ?)
        """
    rows = conn.execute(
        sql,
        (*command_states, *states, *sources),
    ).fetchall()
    return [_dict_row(row) for row in rows]


def _cancel_ack_terminal_no_fill_fact_candidates(conn: sqlite3.Connection) -> list[dict]:
    if not (
        _table_exists(conn, "venue_order_facts")
        and _table_exists(conn, "venue_command_events")
        and _table_exists(conn, "position_current")
    ):
        return []
    sql = "WITH " + _canonical_order_truth_cte() + """
        SELECT
            cmd.command_id AS command_id,
            cmd.venue_order_id AS venue_order_id,
            cmd.state AS command_state,
            cmd.size AS command_size,
            cmd.position_id AS position_id,
            pc.position_id AS projected_position_id,
            pc.phase AS projected_phase,
            terminal_event.occurred_at AS terminal_event_occurred_at,
            fact.fact_id AS latest_order_fact_id,
            fact.state AS latest_order_fact_state,
            fact.remaining_size AS latest_order_fact_remaining_size,
            fact.matched_size AS latest_order_fact_matched_size,
            fact.source AS latest_order_fact_source
          FROM venue_commands cmd
          LEFT JOIN position_current pc
            ON pc.position_id = cmd.position_id
          JOIN canonical_order_truth fact
            ON fact.command_id = cmd.command_id
           AND fact.venue_order_id = cmd.venue_order_id
          JOIN (
                SELECT command_id, MAX(occurred_at) AS occurred_at
                  FROM venue_command_events
                 WHERE event_type IN ('CANCEL_ACKED', 'EXPIRED')
                 GROUP BY command_id
          ) terminal_event
            ON terminal_event.command_id = cmd.command_id
         WHERE cmd.intent_kind = 'ENTRY'
           AND cmd.state IN ('CANCELLED', 'EXPIRED')
           AND cmd.venue_order_id IS NOT NULL
           AND cmd.venue_order_id != ''
           AND (
                pc.position_id IS NULL
                OR (
                    pc.phase = 'pending_entry'
                    AND CAST(COALESCE(pc.shares, '0') AS REAL) = 0
                    AND CAST(COALESCE(pc.cost_basis_usd, '0') AS REAL) = 0
                )
           )
           AND CAST(COALESCE(fact.matched_size, '0') AS REAL) = 0
           AND NOT EXISTS (
                SELECT 1
                  FROM venue_trade_facts trade
                 WHERE trade.command_id = cmd.command_id
                   AND CAST(COALESCE(trade.filled_size, '0') AS REAL) > 0
           )
           AND NOT EXISTS (
                SELECT 1
                  FROM venue_order_facts terminal_fact
                 WHERE terminal_fact.command_id = cmd.command_id
                   AND terminal_fact.venue_order_id = cmd.venue_order_id
                   AND terminal_fact.state IN ('CANCEL_CONFIRMED', 'EXPIRED', 'VENUE_WIPED')
                   AND CAST(COALESCE(terminal_fact.matched_size, '0') AS REAL) = 0
           )
         ORDER BY terminal_event.occurred_at, cmd.command_id
        """
    rows = conn.execute(sql).fetchall()
    return [_dict_row(row) for row in rows]


def _local_orphan_no_fill_candidates(conn: sqlite3.Connection) -> list[dict]:
    if not _table_exists(conn, "exchange_reconcile_findings"):
        return []
    if not _table_exists(conn, "venue_order_facts"):
        return []
    command_states = tuple(_ACKED_ORDER_STATES)
    if len(command_states) != 2:
        raise RuntimeError(
            "update local-orphan no-fill query when _ACKED_ORDER_STATES changes"
        )
    sql = "WITH " + _canonical_order_truth_cte() + """
        SELECT
            finding.finding_id AS finding_id,
            finding.evidence_json AS finding_evidence_json,
            finding.recorded_at AS finding_recorded_at,
            cmd.*,
            fact.fact_id AS order_fact_id,
            fact.state AS order_fact_state,
            fact.observed_at AS order_fact_observed_at,
            fact.venue_order_id AS order_fact_venue_order_id,
            fact.remaining_size AS order_fact_remaining_size,
            fact.matched_size AS order_fact_matched_size,
            fact.source AS order_fact_source
          FROM exchange_reconcile_findings finding
          JOIN venue_commands cmd
            ON cmd.venue_order_id = finding.subject_id
          LEFT JOIN position_current pc
            ON pc.position_id = cmd.position_id
          LEFT JOIN canonical_order_truth fact
            ON fact.command_id = cmd.command_id
           AND fact.venue_order_id = cmd.venue_order_id
         WHERE finding.kind = 'local_orphan_order'
           AND finding.resolved_at IS NULL
           AND cmd.intent_kind = 'ENTRY'
           AND cmd.state IN (?, ?)
           AND (
                fact.fact_id IS NOT NULL
                OR (
                    pc.phase = 'pending_entry'
                    AND CAST(COALESCE(pc.shares, '0') AS REAL) = 0
                    AND CAST(COALESCE(pc.cost_basis_usd, '0') AS REAL) = 0
                )
           )
         ORDER BY finding.recorded_at, finding.finding_id
        """
    rows = conn.execute(
        sql,
        command_states,
    ).fetchall()
    return [_dict_row(row) for row in rows]


def _stale_local_orphan_terminal_no_fill_candidates(conn: sqlite3.Connection) -> list[dict]:
    if not _table_exists(conn, "exchange_reconcile_findings"):
        return []
    if not _table_exists(conn, "venue_order_facts"):
        return []
    states = tuple(sorted(_TERMINAL_NO_FILL_ORDER_FACT_STATES))
    sources = tuple(sorted(_LIVE_TERMINAL_ORDER_FACT_SOURCES))
    state_placeholders = ",".join("?" for _ in states)
    source_placeholders = ",".join("?" for _ in sources)
    sql = "WITH " + _canonical_order_truth_cte() + f"""
        SELECT
            finding.finding_id AS finding_id,
            finding.subject_id AS finding_subject_id,
            cmd.command_id AS command_id,
            cmd.venue_order_id AS venue_order_id,
            cmd.state AS command_state,
            pc.phase AS position_phase,
            pc.shares AS position_shares,
            pc.cost_basis_usd AS position_cost_basis_usd,
            fact.fact_id AS order_fact_id,
            fact.state AS order_fact_state,
            fact.observed_at AS order_fact_observed_at,
            fact.venue_order_id AS order_fact_venue_order_id,
            fact.matched_size AS order_fact_matched_size,
            fact.source AS order_fact_source
          FROM exchange_reconcile_findings finding
          JOIN venue_commands cmd
            ON cmd.venue_order_id = finding.subject_id
          LEFT JOIN position_current pc
            ON pc.position_id = cmd.position_id
          JOIN canonical_order_truth fact
            ON fact.command_id = cmd.command_id
         WHERE finding.kind = 'local_orphan_order'
           AND finding.resolved_at IS NULL
           AND cmd.intent_kind = 'ENTRY'
           AND cmd.state IN ('CANCELLED', 'EXPIRED')
           AND pc.phase = 'voided'
           AND CAST(COALESCE(pc.shares, '0') AS REAL) = 0
           AND CAST(COALESCE(pc.cost_basis_usd, '0') AS REAL) = 0
           AND fact.state IN ({state_placeholders})
           AND fact.source IN ({source_placeholders})
           AND CAST(COALESCE(fact.matched_size, '0') AS REAL) = 0
         ORDER BY finding.recorded_at, finding.finding_id
        """
    rows = conn.execute(
        sql,
        (*states, *sources),
    ).fetchall()
    return [_dict_row(row) for row in rows]


def _json_dict(raw: object) -> dict:
    if isinstance(raw, dict):
        return raw
    if raw in (None, ""):
        return {}
    try:
        parsed = json.loads(str(raw))
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _finding_proves_trade_enumeration(evidence: dict) -> bool:
    return evidence.get("trade_enumeration_available") is True


def _terminal_fact_state_for_venue_status(status: str, *, venue_resp_present: bool) -> str | None:
    normalized = str(status or "").upper()
    if normalized in {"CANCELLED", "CANCELED"}:
        return "CANCEL_CONFIRMED"
    if normalized in {"EXPIRED", "REJECTED"}:
        return "EXPIRED"
    if not venue_resp_present:
        return "VENUE_WIPED"
    return None


def _fill_trade_fact_count(conn: sqlite3.Connection, command_id: str) -> int:
    if not _table_exists(conn, "venue_trade_facts"):
        return 0
    row = conn.execute(
        """
        SELECT COUNT(*) AS count
          FROM venue_trade_facts
         WHERE command_id = ?
           AND state IN ('MATCHED', 'MINED', 'CONFIRMED')
           AND CAST(COALESCE(filled_size, '0') AS REAL) > 0
        """,
        (command_id,),
    ).fetchone()
    return int((_dict_row(row).get("count") if row else 0) or 0)


def _trade_fact_count(conn: sqlite3.Connection, command_id: str) -> int:
    if not _table_exists(conn, "venue_trade_facts"):
        return 0
    row = conn.execute(
        """
        SELECT COUNT(*) AS count
          FROM venue_trade_facts
         WHERE command_id = ?
        """,
        (command_id,),
    ).fetchone()
    return int((_dict_row(row).get("count") if row else 0) or 0)


def _latest_matched_order_fact_candidates(conn: sqlite3.Connection) -> list[dict]:
    if not _table_exists(conn, "venue_order_facts"):
        return []
    command_states = tuple(_ACKED_ORDER_STATES)
    sources = tuple(sorted(_LIVE_TERMINAL_ORDER_FACT_SOURCES))
    fact_states = ("LIVE", "RESTING", "MATCHED", "PARTIALLY_MATCHED", "FILLED")
    sql = "WITH " + _canonical_order_truth_cte() + """
        SELECT
            cmd.*,
            fact.fact_id AS order_fact_id,
            fact.state AS order_fact_state,
            fact.observed_at AS order_fact_observed_at,
            fact.venue_order_id AS order_fact_venue_order_id,
            fact.remaining_size AS order_fact_remaining_size,
            fact.matched_size AS order_fact_matched_size,
            fact.source AS order_fact_source
          FROM venue_commands cmd
          JOIN canonical_order_truth fact
            ON fact.command_id = cmd.command_id
         WHERE cmd.intent_kind IN ('ENTRY', 'EXIT')
           AND cmd.state IN (?, ?)
           AND fact.state IN (?, ?, ?, ?, ?)
           AND fact.source IN (?, ?, ?, ?, ?)
           AND cmd.venue_order_id IS NOT NULL
        """
    rows = conn.execute(
        sql,
        (*command_states, *fact_states, *sources),
    ).fetchall()
    return [_dict_row(row) for row in rows]


def _latest_completed_partial_order_fact_candidates(conn: sqlite3.Connection) -> list[dict]:
    if not _table_exists(conn, "venue_order_facts") or not _table_exists(conn, "venue_trade_facts"):
        return []
    sources = tuple(sorted(_LIVE_TERMINAL_ORDER_FACT_SOURCES))
    if not sources:
        return []
    source_placeholders = ", ".join("?" for _ in sources)
    sql = "WITH " + _canonical_order_truth_cte() + f"""
        SELECT
            cmd.*,
            fact.fact_id AS order_fact_id,
            fact.state AS order_fact_state,
            fact.observed_at AS order_fact_observed_at,
            fact.venue_order_id AS order_fact_venue_order_id,
            fact.remaining_size AS order_fact_remaining_size,
            fact.matched_size AS order_fact_matched_size,
            fact.source AS order_fact_source
          FROM venue_commands cmd
          JOIN canonical_order_truth fact
            ON fact.command_id = cmd.command_id
         WHERE cmd.intent_kind IN ('ENTRY', 'EXIT')
           AND cmd.state = 'PARTIAL'
           AND cmd.venue_order_id IS NOT NULL
           AND cmd.venue_order_id != ''
           AND fact.venue_order_id = cmd.venue_order_id
           AND fact.state = 'MATCHED'
           AND fact.source IN ({source_placeholders})
           AND EXISTS (
               SELECT 1
                 FROM venue_trade_facts trade
                WHERE trade.command_id = cmd.command_id
                  AND trade.venue_order_id = cmd.venue_order_id
                  AND trade.state IN ('MATCHED', 'MINED', 'CONFIRMED')
           )
         ORDER BY fact.observed_at, fact.fact_id
        """
    rows = conn.execute(
        sql,
        sources,
    ).fetchall()
    candidates: list[dict] = []
    for row in rows:
        candidate = _dict_row(row)
        if not _decimal_is_zero(candidate.get("order_fact_remaining_size")):
            continue
        if not _decimal_is_positive(candidate.get("order_fact_matched_size")):
            continue
        if not _trade_facts_match_order_fact_size(
            conn,
            command_id=str(candidate.get("command_id") or ""),
            venue_order_id=str(candidate.get("venue_order_id") or ""),
            matched_size=candidate.get("order_fact_matched_size"),
        ):
            continue
        candidates.append(candidate)
    return candidates


def _trade_facts_match_order_fact_size(
    conn: sqlite3.Connection,
    *,
    command_id: str,
    venue_order_id: str,
    matched_size: object,
) -> bool:
    if not command_id or not venue_order_id:
        return False
    expected = _positive_decimal_or_none(matched_size)
    if expected is None:
        return False
    sql = "WITH " + _canonical_trade_fact_cte() + """
        SELECT fact.filled_size
          FROM canonical_trade_fact fact
         WHERE fact.command_id = ?
           AND fact.venue_order_id = ?
           AND fact.state IN ('MATCHED', 'MINED', 'CONFIRMED')
        """
    rows = conn.execute(
        sql,
        (command_id, venue_order_id),
    ).fetchall()
    filled = Decimal("0")
    count = 0
    for row in rows:
        size = _positive_decimal_or_none(_dict_row(row).get("filled_size"))
        if size is None:
            continue
        filled += size
        count += 1
    return count > 0 and abs(filled - expected) <= Decimal("0.000001")


def _first_present(raw: dict | None, *keys: str):
    if not isinstance(raw, dict):
        return None
    for key in keys:
        value = raw.get(key)
        if value not in (None, ""):
            return value
    return None


def _normalised_order_side(value: object) -> str:
    return str(value or "").strip().upper()


def _point_order_has_live_order_data(point_order: dict | None) -> bool:
    if not isinstance(point_order, dict):
        return False
    return any(
        _first_present(point_order, key) not in (None, "")
        for key in _POINT_ORDER_LIVE_DATA_KEYS
    )


def _point_order_no_live_record(point_order: dict | None, *, expected_order_id: str) -> bool:
    if not isinstance(point_order, dict):
        return False
    status = str(point_order.get("status") or point_order.get("state") or "").upper()
    order_id = str(_extract_order_id(point_order) or "")
    return (
        status in {"UNKNOWN", "NOT_FOUND", ""}
        and not _point_order_has_live_order_data(point_order)
        and (not order_id or order_id == expected_order_id)
    )


def _string_sequence_from_value(value: object) -> tuple[str, ...]:
    if value in (None, ""):
        return ()
    if isinstance(value, (str, int, float)):
        text = str(value).strip()
        return (text,) if text else ()
    if isinstance(value, dict):
        for key in ("id", "trade_id", "tradeID", "tradeId", "hash", "tx_hash", "transactionHash"):
            item = value.get(key)
            if item not in (None, ""):
                text = str(item).strip()
                return (text,) if text else ()
        return ()
    if isinstance(value, (list, tuple)):
        items: list[str] = []
        for item in value:
            items.extend(_string_sequence_from_value(item))
        return tuple(items)
    return ()


def _point_order_trade_ids(point_order: dict | None) -> tuple[str, ...]:
    for key in ("tradeIDs", "tradeIds", "trade_ids", "associate_trades", "trades"):
        values = _string_sequence_from_value(_first_present(point_order, key))
        if values:
            return values
    return ()


def _point_order_transaction_hashes(point_order: dict | None) -> tuple[str, ...]:
    for key in ("transactionsHashes", "transactionHashes", "transaction_hashes", "txHashes", "tx_hashes"):
        values = _string_sequence_from_value(_first_present(point_order, key))
        if values:
            return values
    return ()


def _point_order_matched_size(
    point_order: dict | None,
    *,
    fallback: object = None,
    side: str | None = None,
) -> str:
    value = _first_present(
        point_order,
        "matched_size",
        "matchedSize",
        "size_matched",
        "sizeMatched",
    )
    if value not in (None, ""):
        return str(value)
    side_value = _normalised_order_side(side) or _normalised_order_side(_first_present(point_order, "side"))
    amount_keys = (
        ("makingAmount", "making_amount")
        if side_value == "SELL"
        else ("takingAmount", "taking_amount")
    )
    value = _first_present(point_order, *amount_keys)
    if value not in (None, ""):
        return str(value)
    return str(fallback or "0")


def _point_order_fill_price(
    point_order: dict | None,
    *,
    fallback: object = None,
    side: str | None = None,
) -> str:
    making = _decimal_or_none(_first_present(point_order, "makingAmount", "making_amount"))
    taking = _decimal_or_none(_first_present(point_order, "takingAmount", "taking_amount"))
    if making is not None and taking is not None and making > 0 and taking > 0:
        side_value = _normalised_order_side(side) or _normalised_order_side(_first_present(point_order, "side"))
        if side_value == "SELL":
            return _decimal_text(taking / making)
        return _decimal_text(making / taking)
    value = _first_present(point_order, "avgPrice", "avg_price", "fillPrice", "fill_price", "price")
    if _positive_decimal_or_none(value) is not None:
        return str(value)
    return str(fallback or "")


def _venue_status_is_fully_matched(venue_status: str) -> bool:
    return str(venue_status or "").upper() in {"MATCHED", "FILLED"}


def _matched_remaining_size(command: dict, matched_size: str, *, venue_status: str = "") -> str:
    matched = _decimal_or_none(matched_size)
    if _venue_status_is_fully_matched(venue_status) and matched is not None and matched > 0:
        return "0"
    command_size = _decimal_or_none(command.get("size"))
    if command_size is None or matched is None or matched >= command_size:
        return "0"
    return _decimal_text(command_size - matched)


def _matched_event_type(command: dict, matched_size: str, *, venue_status: str = "") -> str:
    if str(command.get("intent_kind") or "").upper() == "EXIT":
        matched = _decimal_or_none(matched_size)
        command_size = _decimal_or_none(command.get("size"))
        if (
            _venue_status_is_fully_matched(venue_status)
            and matched is not None
            and matched > 0
        ):
            return CommandEventType.FILL_CONFIRMED.value
        if command_size is not None and matched is not None and matched >= command_size:
            return CommandEventType.FILL_CONFIRMED.value
        return CommandEventType.PARTIAL_FILL_OBSERVED.value
    command_size = _decimal_or_none(command.get("size"))
    matched = _decimal_or_none(matched_size)
    if _venue_status_is_fully_matched(venue_status) and matched is not None and matched > 0:
        return CommandEventType.FILL_CONFIRMED.value
    if command_size is not None and matched is not None and matched < command_size:
        return CommandEventType.PARTIAL_FILL_OBSERVED.value
    return CommandEventType.FILL_CONFIRMED.value


def _matched_order_fact_state(*, event_type: str, venue_status: str, remaining_size: str) -> str:
    if event_type == CommandEventType.FILL_CONFIRMED.value:
        return "MATCHED"
    if str(venue_status or "").upper() in {"MATCHED", "FILLED", "MINED"}:
        return "MATCHED"
    if _decimal_is_zero(remaining_size):
        return "MATCHED"
    return "PARTIALLY_MATCHED"


def _coerce_iso_datetime(value: str) -> datetime:
    text = str(value or _now_iso())
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return datetime.now(timezone.utc)


def _append_matched_order_fill_projection(
    conn: sqlite3.Connection,
    *,
    command: dict,
    venue_order_id: str,
    matched_size: str,
    fill_price: str,
    observed_at: str,
) -> None:
    try:
        from src.execution.exchange_reconcile import _ensure_entry_fill_position_event

        _ensure_entry_fill_position_event(
            conn,
            command=command,
            venue_order_id=venue_order_id,
            filled_size=matched_size,
            fill_price=fill_price,
            observed_at=_coerce_iso_datetime(observed_at),
        )
    except Exception:
        logger.exception(
            "recovery: entry fill projection failed for command %s order %s",
            command.get("command_id"),
            venue_order_id,
        )


def _append_exit_order_fill_projection(
    conn: sqlite3.Connection,
    *,
    command: dict,
    venue_order_id: str,
    matched_size: str,
    fill_price: str,
    observed_at: str,
    event_type: str,
) -> None:
    if event_type != CommandEventType.FILL_CONFIRMED.value:
        return
    if str(command.get("intent_kind") or "").upper() != "EXIT":
        return
    try:
        from src.execution.exchange_reconcile import _ensure_exit_fill_position_event

        _ensure_exit_fill_position_event(
            conn,
            command=command,
            venue_order_id=venue_order_id,
            filled_size=matched_size,
            fill_price=fill_price,
            observed_at=_coerce_iso_datetime(observed_at),
            command_event=event_type,
        )
        conn.execute(
            """
            UPDATE position_current
               SET order_status = 'sell_filled',
                   exit_price = COALESCE(exit_price, ?),
                   updated_at = ?
             WHERE position_id = ?
               AND phase = 'economically_closed'
            """,
            (
                float(_positive_decimal_or_none(fill_price) or Decimal("0")),
                observed_at,
                str(command.get("position_id") or ""),
            ),
        )
    except Exception:
        logger.exception(
            "recovery: exit fill projection failed for command %s order %s",
            command.get("command_id"),
            venue_order_id,
        )


def _decimal_text(value: Decimal) -> str:
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _positive_fill_trade_fact_summary(conn: sqlite3.Connection, command_id: str) -> dict:
    if not _table_exists(conn, "venue_trade_facts"):
        return {"count": 0, "filled_size": "0"}
    sql = "WITH " + _canonical_trade_fact_cte() + """
        SELECT fact.filled_size
          FROM canonical_trade_fact fact
         WHERE fact.command_id = ?
           AND fact.state IN ('MATCHED', 'MINED', 'CONFIRMED')
        """
    rows = conn.execute(
        sql,
        (command_id,),
    ).fetchall()
    count = 0
    filled = Decimal("0")
    for row in rows:
        raw = _dict_row(row).get("filled_size")
        try:
            size = Decimal(str(raw))
        except (InvalidOperation, TypeError, ValueError):
            continue
        if not size.is_finite() or size <= 0:
            continue
        count += 1
        filled += size
    return {"count": count, "filled_size": _decimal_text(filled)}


def _latest_review_cancel_blocked_payload(conn: sqlite3.Connection, command_id: str) -> dict:
    if not _table_exists(conn, "venue_command_events"):
        return {}
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
    event = _dict_row(row) if row is not None else {}
    if str(event.get("event_type") or "") != CommandEventType.CANCEL_REPLACE_BLOCKED.value:
        return {}
    return _json_mapping(event.get("payload_json"))


def _cancel_blocked_by_matched_order(payload: Mapping[str, object]) -> bool:
    cancel_outcome = payload.get("cancel_outcome")
    cancel_outcome = cancel_outcome if isinstance(cancel_outcome, Mapping) else {}
    text = " ".join(
        str(value or "")
        for value in (
            payload.get("reason"),
            payload.get("semantic_cancel_status"),
            cancel_outcome.get("status"),
            cancel_outcome.get("errorMsg"),
            cancel_outcome.get("errorMessage"),
            cancel_outcome.get("message"),
        )
    ).lower()
    return "not_canceled" in text and "matched" in text


def _latest_order_fact_for_command_order(
    conn: sqlite3.Connection,
    *,
    command_id: str,
    venue_order_id: str,
) -> dict:
    if not _table_exists(conn, "venue_order_facts"):
        return {}
    sql = "WITH " + _canonical_order_truth_cte() + """
        SELECT *
          FROM canonical_order_truth
         WHERE command_id = ?
           AND venue_order_id = ?
         LIMIT 1
    """
    row = conn.execute(sql, (command_id, venue_order_id)).fetchone()
    return _dict_row(row) if row is not None else {}


def _matched_cancel_residual_is_dust(command: Mapping[str, object], order_fact: Mapping[str, object], filled_size: str) -> bool:
    residual = _decimal_or_none(order_fact.get("remaining_size"))
    if residual is None:
        command_size = _decimal_or_none(command.get("size"))
        filled = _decimal_or_none(filled_size)
        if command_size is None or filled is None:
            return False
        residual = max(command_size - filled, Decimal("0"))
    return Decimal("0") <= residual <= Decimal("0.011")


def _active_projection_matches_confirmed_fill(
    conn: sqlite3.Connection,
    *,
    command: Mapping[str, object],
    venue_order_id: str,
    filled_size: str,
) -> bool:
    if not _table_exists(conn, "position_current"):
        return False
    rows = conn.execute(
        """
        SELECT phase, chain_state, shares, chain_shares, order_id
          FROM position_current
         WHERE order_id = ?
            OR position_id = ?
         ORDER BY updated_at DESC
        """,
        (venue_order_id, str(command.get("position_id") or "")),
    ).fetchall()
    filled = _positive_decimal_or_none(filled_size)
    if filled is None:
        return False
    for row in rows:
        current = _dict_row(row)
        if str(current.get("phase") or "") not in {"active", "day0_window", "pending_exit"}:
            continue
        if str(current.get("chain_state") or "") not in {"synced", "chain_present", "exit_pending_missing"}:
            continue
        chain_shares = _positive_decimal_or_none(current.get("chain_shares"))
        if chain_shares is not None:
            if abs(chain_shares - filled) > Decimal("0.02"):
                continue
            return True
        shares = _positive_decimal_or_none(current.get("shares"))
        if shares is None or abs(shares - filled) > Decimal("0.01"):
            continue
        return True
    return False


def _json_mapping(raw: object) -> dict:
    if isinstance(raw, dict):
        return raw
    if raw in (None, ""):
        return {}
    try:
        parsed = json.loads(str(raw))
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _edli_certificate_matches_token(payload: dict, *, semantic_key: str, token_id: str) -> bool:
    if not token_id:
        return True
    for key in ("token_id", "selected_outcome_token_id", "no_token_id", "yes_token_id"):
        value = str(payload.get(key) or "").strip()
        if value:
            return value == token_id
    final_intent_id = str(payload.get("final_intent_id") or "").strip()
    return token_id in semantic_key or token_id in final_intent_id


def _market_token_strings_from_payload(payload: object) -> set[str]:
    tokens: set[str] = set()
    if isinstance(payload, Mapping):
        for key in ("tokens", "clobTokenIds", "clob_token_ids", "outcomeTokens"):
            tokens.update(_market_token_strings_from_payload(payload.get(key)))
        for key in (
            "token_id",
            "tokenId",
            "yes_token_id",
            "no_token_id",
            "yesTokenId",
            "noTokenId",
            "asset_id",
            "assetId",
        ):
            value = payload.get(key)
            if value not in (None, "") and not isinstance(value, (Mapping, list, tuple)):
                tokens.add(str(value))
    elif isinstance(payload, str):
        stripped = payload.strip()
        if stripped[:1] in "[{":
            try:
                tokens.update(_market_token_strings_from_payload(json.loads(stripped)))
            except json.JSONDecodeError:
                if stripped:
                    tokens.add(stripped)
        elif stripped:
            tokens.add(stripped)
    elif isinstance(payload, (list, tuple)):
        for item in payload:
            tokens.update(_market_token_strings_from_payload(item))
    return tokens


_TEMPERATURE_BIN_QUESTION_RE = re.compile(
    r"-?\d+(?:\.\d+)?\s*(?:[-–]\s*-?\d+(?:\.\d+)?\s*)?°[FfCc]"
    r"(?:\s+or\s+(?:below|lower|higher|above|more)|\s+on\b|$)"
)


def _is_parseable_temperature_bin_label(label: str) -> bool:
    return bool(_TEMPERATURE_BIN_QUESTION_RE.search(str(label or "").strip()))


def _clob_market_identity_from_payload(
    payload: object,
    *,
    condition_id: str,
    yes_token_id: str,
    no_token_id: str,
) -> dict:
    raw = getattr(payload, "raw", payload)
    if not isinstance(raw, Mapping) or not raw:
        return {}
    raw_condition = str(
        raw.get("condition_id")
        or raw.get("conditionId")
        or raw.get("market")
        or ""
    ).strip()
    if condition_id and raw_condition and raw_condition != condition_id:
        return {}
    required_tokens = {str(yes_token_id or "").strip(), str(no_token_id or "").strip()} - {""}
    if required_tokens:
        payload_tokens = _market_token_strings_from_payload(raw)
        if required_tokens - payload_tokens:
            return {}
    for key in ("question", "groupItemTitle", "title", "name"):
        label = str(raw.get(key) or "").strip()
        if label and _is_parseable_temperature_bin_label(label):
            return {
                "bin_label": label,
                "range_label": label,
                "market_metadata_source": "clob_market_info",
            }
    return {}


def _clob_market_identity_for_command(
    client,
    *,
    condition_id: str,
    yes_token_id: str,
    no_token_id: str,
) -> dict:
    if client is None or not condition_id:
        return {}
    getter = getattr(client, "get_clob_market_info", None)
    if not callable(getter):
        return {}
    try:
        payload = getter(condition_id)
    except Exception:
        logger.debug(
            "recovery: CLOB market identity lookup failed for condition_id=%s",
            condition_id,
            exc_info=True,
        )
        return {}
    return _clob_market_identity_from_payload(
        payload,
        condition_id=condition_id,
        yes_token_id=yes_token_id,
        no_token_id=no_token_id,
    )


def _latest_unprojected_filled_entry_candidates(conn: sqlite3.Connection) -> list[dict]:
    required = {
        "venue_commands",
        "venue_trade_facts",
        "position_current",
        "venue_submission_envelopes",
        "executable_market_snapshots",
    }
    if not all(_table_exists(conn, table) for table in required):
        return []
    sql = "WITH " + _canonical_trade_fact_cte() + """,
        entry_fill AS (
            SELECT fact.command_id,
                   COUNT(*) AS fill_fact_count,
                   SUM(CAST(fact.filled_size AS REAL)) AS filled_size,
                   SUM(CAST(fact.filled_size AS REAL) * CAST(fact.fill_price AS REAL))
                       / SUM(CAST(fact.filled_size AS REAL)) AS fill_price,
                   MAX(fact.observed_at) AS observed_at,
                   GROUP_CONCAT(DISTINCT fact.state) AS fill_states,
                   MAX(fact.trade_fact_id) AS trade_fact_id
              FROM canonical_trade_fact fact
             WHERE fact.state IN ('MATCHED', 'MINED', 'CONFIRMED')
               AND CAST(COALESCE(fact.filled_size, '0') AS REAL) > 0
               AND CAST(COALESCE(fact.fill_price, '0') AS REAL) > 0
             GROUP BY fact.command_id
        )
        SELECT cmd.*,
               entry_fill.fill_fact_count AS fill_fact_count,
               entry_fill.filled_size AS fill_filled_size,
               entry_fill.fill_price AS fill_price,
               entry_fill.observed_at AS fill_observed_at,
               entry_fill.fill_states AS fill_states,
               entry_fill.trade_fact_id AS source_trade_fact_id,
               env.condition_id AS env_condition_id,
               env.yes_token_id AS env_yes_token_id,
               env.no_token_id AS env_no_token_id,
               env.selected_outcome_token_id AS env_selected_outcome_token_id,
               env.outcome_label AS env_outcome_label,
               snap.condition_id AS snapshot_condition_id,
               snap.yes_token_id AS snapshot_yes_token_id,
               snap.no_token_id AS snapshot_no_token_id,
               snap.selected_outcome_token_id AS snapshot_selected_outcome_token_id,
               snap.outcome_label AS snapshot_outcome_label
          FROM venue_commands cmd
          JOIN entry_fill
            ON entry_fill.command_id = cmd.command_id
          LEFT JOIN position_current pc
            ON pc.position_id = cmd.position_id
          LEFT JOIN venue_submission_envelopes env
            ON env.envelope_id = cmd.envelope_id
          LEFT JOIN executable_market_snapshots snap
            ON snap.snapshot_id = cmd.snapshot_id
         WHERE cmd.intent_kind = 'ENTRY'
           AND cmd.side = 'BUY'
           AND cmd.state = 'FILLED'
           AND cmd.venue_order_id IS NOT NULL
           AND cmd.venue_order_id != ''
           AND pc.position_id IS NULL
           AND NOT EXISTS (
               SELECT 1
                 FROM position_current existing_pc
                WHERE existing_pc.position_id != cmd.position_id
                  AND COALESCE(existing_pc.order_id, '') != ''
                  AND lower(existing_pc.order_id) = lower(cmd.venue_order_id)
                  AND (
                      COALESCE(existing_pc.token_id, '') = cmd.token_id
                      OR COALESCE(existing_pc.no_token_id, '') = cmd.token_id
                      OR (
                          COALESCE(existing_pc.condition_id, '') != ''
                          AND COALESCE(existing_pc.condition_id, '') = COALESCE(env.condition_id, snap.condition_id, cmd.market_id, '')
                      )
                  )
           )
         ORDER BY entry_fill.observed_at, cmd.command_id
        """
    rows = conn.execute(
        sql
    ).fetchall()
    return [_dict_row(row) for row in rows]


def _filled_entry_position_link_repair_candidates(conn: sqlite3.Connection) -> list[dict]:
    required = {
        "venue_commands",
        "venue_trade_facts",
        "position_current",
        "venue_submission_envelopes",
        "executable_market_snapshots",
    }
    if not all(_table_exists(conn, table) for table in required):
        return []
    sql = "WITH " + _canonical_trade_fact_cte() + """,
        entry_fill AS (
            SELECT fact.command_id,
                   COUNT(*) AS fill_fact_count,
                   SUM(CAST(fact.filled_size AS REAL)) AS filled_size,
                   MAX(fact.observed_at) AS observed_at
              FROM canonical_trade_fact fact
             WHERE fact.state IN ('MATCHED', 'MINED', 'CONFIRMED')
               AND CAST(COALESCE(fact.filled_size, '0') AS REAL) > 0
             GROUP BY fact.command_id
        )
        SELECT cmd.command_id,
               cmd.position_id,
               cmd.venue_order_id,
               cmd.token_id,
               cmd.state,
               entry_fill.fill_fact_count,
               entry_fill.filled_size,
               entry_fill.observed_at AS fill_observed_at,
               existing_pc.position_id AS canonical_position_id,
               existing_pc.phase AS canonical_phase
          FROM venue_commands cmd
          JOIN entry_fill
            ON entry_fill.command_id = cmd.command_id
          LEFT JOIN position_current pc
            ON pc.position_id = cmd.position_id
          LEFT JOIN venue_submission_envelopes env
            ON env.envelope_id = cmd.envelope_id
          LEFT JOIN executable_market_snapshots snap
            ON snap.snapshot_id = cmd.snapshot_id
          JOIN position_current existing_pc
            ON existing_pc.position_id != cmd.position_id
           AND COALESCE(existing_pc.order_id, '') != ''
           AND lower(existing_pc.order_id) = lower(cmd.venue_order_id)
           AND (
               COALESCE(existing_pc.token_id, '') = cmd.token_id
               OR COALESCE(existing_pc.no_token_id, '') = cmd.token_id
               OR (
                   COALESCE(existing_pc.condition_id, '') != ''
                   AND COALESCE(existing_pc.condition_id, '') = COALESCE(env.condition_id, snap.condition_id, cmd.market_id, '')
               )
           )
         WHERE cmd.intent_kind = 'ENTRY'
           AND cmd.side = 'BUY'
           AND cmd.state IN ('FILLED', 'PARTIAL')
           AND cmd.venue_order_id IS NOT NULL
           AND cmd.venue_order_id != ''
           AND pc.position_id IS NULL
         ORDER BY entry_fill.observed_at, cmd.command_id, existing_pc.updated_at DESC
        """
    rows = [_dict_row(row) for row in conn.execute(sql).fetchall()]
    by_command: dict[str, list[dict]] = {}
    for row in rows:
        by_command.setdefault(str(row.get("command_id") or ""), []).append(row)
    candidates: list[dict] = []
    for command_id, matches in by_command.items():
        unique_positions = {
            str(match.get("canonical_position_id") or "")
            for match in matches
            if str(match.get("canonical_position_id") or "")
        }
        first = dict(matches[0])
        first["canonical_match_count"] = len(unique_positions)
        if len(unique_positions) == 1:
            first["canonical_position_id"] = next(iter(unique_positions))
        candidates.append(first)
    return candidates


def _latest_unprojected_live_entry_candidates(conn: sqlite3.Connection) -> list[dict]:
    required = {
        "venue_commands",
        "venue_order_facts",
        "venue_trade_facts",
        "position_current",
        "venue_submission_envelopes",
        "executable_market_snapshots",
    }
    if not all(_table_exists(conn, table) for table in required):
        return []
    sql = "WITH " + _canonical_order_truth_cte(partition_by_venue_order=True) + """,
        live_order AS (
            SELECT fact.command_id,
                   fact.venue_order_id,
                   fact.fact_id AS order_fact_id,
                   fact.state AS order_fact_state,
                   fact.remaining_size AS order_fact_remaining_size,
                   fact.matched_size AS order_fact_matched_size,
                   fact.source AS order_fact_source,
                   fact.observed_at AS order_fact_observed_at
              FROM canonical_order_truth fact
             WHERE fact.state IN ('LIVE', 'OPEN', 'RESTING')
               AND CAST(COALESCE(fact.remaining_size, '0') AS REAL) > 0
               AND CAST(COALESCE(fact.matched_size, '0') AS REAL) = 0
        )
        SELECT cmd.*,
               live_order.order_fact_id AS order_fact_id,
               live_order.order_fact_state AS order_fact_state,
               live_order.order_fact_remaining_size AS order_fact_remaining_size,
               live_order.order_fact_matched_size AS order_fact_matched_size,
               live_order.order_fact_source AS order_fact_source,
               live_order.order_fact_observed_at AS order_fact_observed_at,
               env.condition_id AS env_condition_id,
               env.yes_token_id AS env_yes_token_id,
               env.no_token_id AS env_no_token_id,
               env.selected_outcome_token_id AS env_selected_outcome_token_id,
               env.outcome_label AS env_outcome_label,
               snap.condition_id AS snapshot_condition_id,
               snap.yes_token_id AS snapshot_yes_token_id,
               snap.no_token_id AS snapshot_no_token_id,
               snap.selected_outcome_token_id AS snapshot_selected_outcome_token_id,
               snap.outcome_label AS snapshot_outcome_label
          FROM venue_commands cmd
          JOIN live_order
            ON live_order.command_id = cmd.command_id
           AND live_order.venue_order_id = cmd.venue_order_id
          LEFT JOIN position_current pc
            ON pc.position_id = cmd.position_id
          LEFT JOIN venue_submission_envelopes env
            ON env.envelope_id = cmd.envelope_id
          LEFT JOIN executable_market_snapshots snap
            ON snap.snapshot_id = cmd.snapshot_id
         WHERE cmd.intent_kind = 'ENTRY'
           AND cmd.side = 'BUY'
           AND cmd.state IN ('ACKED', 'POST_ACKED')
           AND cmd.venue_order_id IS NOT NULL
           AND cmd.venue_order_id != ''
           AND pc.position_id IS NULL
           AND NOT EXISTS (
               SELECT 1
                 FROM position_current existing_pc
                WHERE existing_pc.position_id != cmd.position_id
                  AND COALESCE(existing_pc.order_id, '') != ''
                  AND lower(existing_pc.order_id) = lower(cmd.venue_order_id)
                  AND (
                      COALESCE(existing_pc.token_id, '') = cmd.token_id
                      OR COALESCE(existing_pc.no_token_id, '') = cmd.token_id
                      OR (
                          COALESCE(existing_pc.condition_id, '') != ''
                          AND COALESCE(existing_pc.condition_id, '') = COALESCE(env.condition_id, snap.condition_id, cmd.market_id, '')
                      )
                  )
           )
           AND NOT EXISTS (
               SELECT 1
                 FROM venue_trade_facts trade_fact
                WHERE trade_fact.command_id = cmd.command_id
                  AND trade_fact.state IN ('MATCHED', 'MINED', 'CONFIRMED')
                  AND CAST(COALESCE(trade_fact.filled_size, '0') AS REAL) > 0
           )
         ORDER BY live_order.order_fact_observed_at, cmd.command_id
        """
    rows = conn.execute(sql).fetchall()
    return [_dict_row(row) for row in rows]


def _decision_log_trade_case_for_command(
    conn: sqlite3.Connection,
    command: dict,
    *,
    client=None,
) -> tuple[dict, int | None]:
    decision_id = str(command.get("decision_id") or "")
    if _edli_event_id_from_decision_id(decision_id):
        # EDLI commands are live-money decisions with certificate authority.  Do
        # not hydrate them from legacy decision_log/snapshot identity: that path
        # can manufacture ens_member_counting rows with zero posterior/economics
        # for orders that should instead remain unresolved/quarantined.
        edli_case = _edli_trade_case_for_command(conn, command, client=client)
        return (edli_case, None) if edli_case else ({}, None)

    if not _table_exists(conn, "decision_log"):
        return {}, None
    position_id = str(command.get("position_id") or "")
    token_id = str(command.get("token_id") or "")
    like_terms = [term for term in (position_id, decision_id, token_id) if term]
    if not like_terms:
        return {}, None
    patterns = [f"%{term}%" for term in like_terms]
    patterns.extend([None] * (3 - len(patterns)))
    rows = conn.execute(
        """
        SELECT id, artifact_json
          FROM decision_log
         WHERE (? IS NOT NULL AND artifact_json LIKE ?)
            OR (? IS NOT NULL AND artifact_json LIKE ?)
            OR (? IS NOT NULL AND artifact_json LIKE ?)
         ORDER BY id DESC
         LIMIT 25
        """,
        (
            patterns[0], patterns[0],
            patterns[1], patterns[1],
            patterns[2], patterns[2],
        ),
    ).fetchall()
    for row in rows:
        record = _dict_row(row)
        artifact = _json_mapping(record.get("artifact_json"))
        cases = artifact.get("trade_cases")
        if not isinstance(cases, list):
            continue
        for case in cases:
            if not isinstance(case, dict):
                continue
            if (
                (position_id and str(case.get("trade_id") or "") == position_id)
                or (decision_id and str(case.get("decision_id") or "") == decision_id)
                or (token_id and str(case.get("token_id") or "") == token_id)
            ):
                if _positive_probability_or_none(case.get("p_posterior")) is None:
                    edli_case = _edli_trade_case_for_command(conn, command, client=client)
                    edli_posterior = _positive_probability_or_none(
                        edli_case.get("p_posterior") if edli_case else None
                    )
                    if edli_posterior is not None:
                        enriched = dict(case)
                        enriched["p_posterior"] = edli_posterior
                        return enriched, int(record.get("id") or 0)
                return case, int(record.get("id") or 0)
    # Non-EDLI chain/venue facts still need projection repair from immutable
    # command/snapshot identity. EDLI commands are handled above and require
    # certificate authority.
    return _snapshot_trade_case_for_command(conn, command, client=client), None


def _edli_event_id_from_decision_id(decision_id: str) -> str:
    parts = str(decision_id or "").split(":")
    if len(parts) >= 2 and parts[0] == "edli_exec_cmd":
        return parts[1]
    return ""


def _edli_certificate_payload(
    conn: sqlite3.Connection,
    *,
    certificate_type: str,
    event_id: str,
    token_id: str,
) -> dict:
    ref = _decision_certificates_ref(conn)
    if ref is None or not event_id:
        return {}
    rows = conn.execute(
        f"""
        SELECT semantic_key, payload_json
          FROM {ref}
         WHERE certificate_type = ?
           AND semantic_key LIKE ?
         ORDER BY created_at DESC
         LIMIT 50
        """,
        (certificate_type, f"%{event_id}%"),
    ).fetchall()
    for row in rows:
        record = _dict_row(row)
        payload = _json_mapping(record.get("payload_json"))
        if not _edli_certificate_matches_token(
            payload,
            semantic_key=str(record.get("semantic_key") or ""),
            token_id=token_id,
        ):
            continue
        return payload
    return {}


def _decision_certificate_is_quarantined(
    conn: sqlite3.Connection,
    *,
    certificate_hash: str,
) -> bool:
    certificate_hash = str(certificate_hash or "").strip()
    if not certificate_hash:
        return False
    q_ref = _decision_integrity_quarantine_ref(conn)
    if q_ref is None:
        return False
    try:
        from src.state.decision_integrity_quarantine import (
            DECISION_CERTIFICATES_TABLE,
            REASON_INVALID_LIVE_ACTIONABLE,
            REASON_INVALID_LIVE_PARENT_MODE,
        )
    except Exception:  # pragma: no cover - import fallback for degraded recovery shells
        DECISION_CERTIFICATES_TABLE = "decision_certificates"
        REASON_INVALID_LIVE_ACTIONABLE = "QUARANTINED_INVALID_LIVE_ACTIONABLE_CERTIFICATE"
        REASON_INVALID_LIVE_PARENT_MODE = "QUARANTINED_INVALID_LIVE_MONEY_PARENT_MODE"
    reason_codes = (REASON_INVALID_LIVE_ACTIONABLE, REASON_INVALID_LIVE_PARENT_MODE)
    placeholders = ",".join("?" for _ in reason_codes)
    row = conn.execute(
        f"""
        SELECT 1
          FROM {q_ref}
         WHERE table_name = ?
           AND row_id = ?
           AND reason_code IN ({placeholders})
         LIMIT 1
        """,
        (DECISION_CERTIFICATES_TABLE, certificate_hash, *reason_codes),
    ).fetchone()
    return row is not None


def _verified_edli_actionable_payload(
    conn: sqlite3.Connection,
    *,
    event_id: str,
    token_id: str,
) -> dict:
    """Return EDLI Actionable payload only when it is live-consumable now."""

    ref = _decision_certificates_ref(conn)
    if ref is None or not event_id:
        return {}
    rows = conn.execute(
        f"""
        SELECT semantic_key, certificate_hash, payload_json
          FROM {ref}
         WHERE certificate_type = 'ActionableTradeCertificate'
           AND semantic_key LIKE ?
         ORDER BY created_at DESC
         LIMIT 50
        """,
        (f"%{event_id}%",),
    ).fetchall()
    for row in rows:
        record = _dict_row(row)
        payload = _json_mapping(record.get("payload_json"))
        if not _edli_certificate_matches_token(
            payload,
            semantic_key=str(record.get("semantic_key") or ""),
            token_id=token_id,
        ):
            continue
        cert_hash = str(record.get("certificate_hash") or "").strip()
        if _decision_certificate_is_quarantined(conn, certificate_hash=cert_hash):
            logger.warning(
                "recovery: EDLI Actionable certificate is quarantined; refusing "
                "entry projection authority event_id=%s token_id=%s certificate_hash=%s",
                event_id,
                token_id,
                cert_hash,
            )
            return {}
        try:
            from src.decision_kernel.verifier import _verify_actionable_payload

            _verify_actionable_payload(type("_PayloadCarrier", (), {"payload": payload})())
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "recovery: EDLI Actionable certificate fails current verifier; refusing "
                "entry projection authority event_id=%s token_id=%s certificate_hash=%s error=%s",
                event_id,
                token_id,
                cert_hash,
                exc,
            )
            return {}
        return payload
    return {}


def _market_event_identity_for_condition(conn: sqlite3.Connection, condition_id: str) -> dict:
    ref = _market_events_ref(conn)
    if ref is None or not condition_id:
        return {}
    row = conn.execute(
        f"""
        SELECT city, target_date, range_label, outcome, temperature_metric
         FROM {ref}
         WHERE condition_id = ?
         ORDER BY rowid DESC
         LIMIT 1
        """,
        (condition_id,),
    ).fetchone()
    return _dict_row(row)


def _direction_from_command_tokens(command: dict) -> str:
    selected_token_id = str(command.get("token_id") or "").strip()
    yes_token_id = str(command.get("env_yes_token_id") or command.get("snapshot_yes_token_id") or "").strip()
    no_token_id = str(command.get("env_no_token_id") or command.get("snapshot_no_token_id") or "").strip()
    if selected_token_id and selected_token_id == yes_token_id:
        return "buy_yes"
    if selected_token_id and selected_token_id == no_token_id:
        return "buy_no"
    outcome_label = str(
        command.get("env_outcome_label") or command.get("snapshot_outcome_label") or ""
    ).strip().upper()
    if outcome_label == "YES":
        return "buy_yes"
    if outcome_label == "NO":
        return "buy_no"
    decision_tail = str(command.get("decision_id") or "").rsplit(":", 1)[-1].strip().lower()
    if decision_tail in {"buy_yes", "buy_no"}:
        return decision_tail
    return ""


def _snapshot_trade_case_for_command(conn: sqlite3.Connection, command: dict, *, client=None) -> dict:
    """Recover non-Day0 entry identity from immutable command envelope/snapshot rows.

    This is the third repair authority after decision_log and EDLI certificates.
    It only fires when the command's pre-submit envelope / executable snapshot
    prove token identity and forecasts.market_events proves market identity.
    Same-UTC-day commands stay fail-closed so Day0 settlement_capture fills are
    not misclassified as opening_inertia.
    """

    command = _hydrate_command_execution_identity(conn, command)
    condition_id = str(
        command.get("env_condition_id")
        or command.get("snapshot_condition_id")
        or command.get("market_id")
        or ""
    ).strip()
    yes_token_id = str(command.get("env_yes_token_id") or command.get("snapshot_yes_token_id") or "").strip()
    no_token_id = str(command.get("env_no_token_id") or command.get("snapshot_no_token_id") or "").strip()
    selected_token_id = str(command.get("token_id") or "").strip()
    direction = _direction_from_command_tokens(command)
    if not (
        condition_id
        and yes_token_id
        and no_token_id
        and selected_token_id
        and direction in {"buy_yes", "buy_no"}
    ):
        return {}
    expected_selected = no_token_id if direction == "buy_no" else yes_token_id
    if selected_token_id != expected_selected:
        return {}

    market_event = _market_event_identity_for_condition(conn, condition_id)
    city = str(market_event.get("city") or "").strip()
    target_date = str(market_event.get("target_date") or "").strip()
    bin_label = str(market_event.get("range_label") or market_event.get("outcome") or "").strip()
    metric = str(market_event.get("temperature_metric") or "").strip().lower()
    created_at = str(command.get("created_at") or "").strip()
    if not (city and target_date and bin_label and metric in {"high", "low"}):
        if not bin_label:
            clob_identity = _clob_market_identity_for_command(
                client,
                condition_id=condition_id,
                yes_token_id=yes_token_id,
                no_token_id=no_token_id,
            )
            bin_label = str(clob_identity.get("bin_label") or "").strip()
        if not (city and target_date and bin_label and metric in {"high", "low"}):
            return {}
    if created_at[:10] and target_date <= created_at[:10]:
        return {}

    strategy_key = "opening_inertia" if direction == "buy_no" else "center_buy"
    return {
        "trade_id": str(command.get("position_id") or ""),
        "decision_id": str(command.get("decision_id") or ""),
        "token_id": yes_token_id,
        "no_token_id": no_token_id,
        "city": city,
        "target_date": target_date,
        "bin_label": bin_label,
        "range_label": bin_label,
        "direction": direction,
        "strategy_key": strategy_key,
        "strategy": strategy_key,
        "temperature_metric": metric,
        "unit": "",
        "selected_method": "ens_member_counting",
        "entry_method": "ens_member_counting",
        "edge_source": strategy_key,
        "discovery_mode": "opening_hunt",
        "cluster": city,
        "p_posterior": 0.0,
        "decision_snapshot_id": str(command.get("snapshot_id") or ""),
        "size_usd": None,
    }


def _event_bound_strategy_key_from_payload(payload: dict) -> str:
    strategy = str(payload.get("strategy_key") or "").strip()
    if strategy:
        return strategy
    event_type = str(payload.get("event_type") or "").strip()
    direction = str(payload.get("direction") or "").strip().lower()
    if event_type == "FORECAST_SNAPSHOT_READY":
        return "opening_inertia" if direction == "buy_no" else "center_buy"
    if event_type == "DAY0_EXTREME_UPDATED":
        return "settlement_capture"
    return ""


def _edli_trade_case_for_command(conn: sqlite3.Connection, command: dict, *, client=None) -> dict:
    """Recover a trade_case from EDLI certificates when legacy decision_log is absent."""

    decision_id = str(command.get("decision_id") or "")
    event_id = _edli_event_id_from_decision_id(decision_id)
    selected_token_id = str(command.get("token_id") or "").strip()
    condition_id = str(
        command.get("env_condition_id")
        or command.get("snapshot_condition_id")
        or ""
    ).strip()
    actionable = _verified_edli_actionable_payload(
        conn,
        event_id=event_id,
        token_id=selected_token_id,
    )
    final_intent = _edli_certificate_payload(
        conn,
        certificate_type="FinalIntentCertificate",
        event_id=event_id,
        token_id=selected_token_id,
    )
    if not actionable:
        return {}
    qkernel_payload = actionable.get("qkernel_execution_economics")
    selection_authority = str(actionable.get("selection_authority_applied") or "").strip()
    qkernel_certified = selection_authority == "qkernel_spine" and isinstance(qkernel_payload, dict)
    source_context = _json_mapping(final_intent.get("decision_source_context"))
    market_event = _market_event_identity_for_condition(
        conn,
        condition_id or str(actionable.get("condition_id") or final_intent.get("condition_id") or ""),
    )
    city = str(
        actionable.get("city")
        or final_intent.get("city")
        or source_context.get("city")
        or market_event.get("city")
        or ""
    ).strip()
    target_date = str(
        actionable.get("target_date")
        or final_intent.get("target_date")
        or source_context.get("target_date")
        or source_context.get("target_local_date")
        or market_event.get("target_date")
        or ""
    ).strip()
    bin_label = str(
        actionable.get("bin_label")
        or final_intent.get("bin_label")
        or market_event.get("range_label")
        or market_event.get("outcome")
        or ""
    ).strip()
    direction = str(actionable.get("direction") or final_intent.get("direction") or "").strip().lower()
    yes_token_id = str(command.get("env_yes_token_id") or command.get("snapshot_yes_token_id") or "").strip()
    no_token_id = str(command.get("env_no_token_id") or command.get("snapshot_no_token_id") or "").strip()
    if not bin_label:
        clob_identity = _clob_market_identity_for_command(
            client,
            condition_id=condition_id,
            yes_token_id=yes_token_id,
            no_token_id=no_token_id,
        )
        bin_label = str(clob_identity.get("bin_label") or "").strip()
    strategy_key = _canonical_projection_strategy_key(
        _event_bound_strategy_key_from_payload(actionable or final_intent)
    )
    metric = str(
        actionable.get("temperature_metric")
        or actionable.get("metric")
        or final_intent.get("temperature_metric")
        or final_intent.get("metric")
        or source_context.get("temperature_metric")
        or source_context.get("metric")
        or ""
    ).strip().lower()
    unit = str(
        actionable.get("unit")
        or final_intent.get("unit")
        or source_context.get("settlement_unit")
        or source_context.get("unit")
        or ""
    ).strip().upper()
    if not (
        city
        and target_date
        and bin_label
        and direction in {"buy_yes", "buy_no"}
        and strategy_key in _CANONICAL_STRATEGY_KEYS
        and metric in {"high", "low"}
        and unit in {"F", "C"}
        and yes_token_id
        and no_token_id
    ):
        return {}
    method = "qkernel_spine" if qkernel_certified else "venue_fact_recovery"
    discovery_mode = "update_reaction" if qkernel_certified else "venue_fact_recovery"
    return {
        "trade_id": str(command.get("position_id") or ""),
        "decision_id": decision_id,
        "token_id": yes_token_id,
        "no_token_id": no_token_id,
        "city": city,
        "target_date": target_date,
        "bin_label": bin_label,
        "range_label": bin_label,
        "direction": direction,
        "strategy_key": strategy_key,
        "strategy": strategy_key,
        "temperature_metric": metric,
        "unit": unit,
        "selected_method": method,
        "entry_method": method,
        "edge_source": strategy_key,
        "discovery_mode": discovery_mode,
        "cluster": city,
        "p_posterior": actionable.get("q_live") or 0.0,
        "decision_snapshot_id": (
            actionable.get("causal_snapshot_id")
            or final_intent.get("causal_snapshot_id")
            or source_context.get("snapshot_id")
            or command.get("snapshot_id")
            or ""
        ),
        "size_usd": None,
    }


def _case_unit(case: dict) -> str:
    settlement = _json_mapping(case.get("settlement_semantics_json"))
    epistemic = _json_mapping(case.get("epistemic_context_json"))
    forecast_context = _json_mapping(epistemic.get("forecast_context"))
    for value in (
        case.get("unit"),
        settlement.get("measurement_unit"),
        forecast_context.get("unit"),
    ):
        unit = str(value or "").strip().upper()
        if unit in {"F", "C"}:
            return unit
    label = str(case.get("range_label") or case.get("bin_label") or "")
    if "°C" in label or " C" in label or label.endswith("C"):
        return "C"
    if "°F" in label or " F" in label or label.endswith("F"):
        return "F"
    raise ValueError("filled entry projection repair requires unit provenance")


def _case_temperature_metric(case: dict) -> str:
    epistemic = _json_mapping(case.get("epistemic_context_json"))
    forecast_context = _json_mapping(epistemic.get("forecast_context"))
    for value in (
        case.get("temperature_metric"),
        forecast_context.get("temperature_metric"),
    ):
        metric = str(value or "").strip().lower()
        if metric in {"high", "low"}:
            return metric
    label = str(case.get("range_label") or case.get("bin_label") or "").lower()
    if "lowest" in label or " low " in label:
        return "low"
    if "highest" in label or " high " in label:
        return "high"
    raise ValueError("filled entry projection repair requires temperature_metric provenance")


def _canonical_projection_strategy_key(strategy_key: str) -> str:
    normalized = str(strategy_key or "").strip()
    if normalized in _CANONICAL_STRATEGY_KEYS:
        return normalized
    return _LEGACY_STRATEGY_KEY_ALIASES.get(normalized, normalized)


def _entry_recovery_position(
    candidate: dict,
    trade_case: dict,
    *,
    decision_log_id: int | None,
    filled: bool,
) -> SimpleNamespace:
    position_id = str(candidate.get("position_id") or "").strip()
    command_id = str(candidate.get("command_id") or "").strip()
    venue_order_id = str(candidate.get("venue_order_id") or "").strip()
    selected_token_id = str(candidate.get("token_id") or "").strip()
    condition_id = str(
        candidate.get("env_condition_id")
        or candidate.get("snapshot_condition_id")
        or trade_case.get("market_id")
        or ""
    ).strip()
    token_id = str(
        candidate.get("env_yes_token_id")
        or candidate.get("snapshot_yes_token_id")
        or trade_case.get("token_id")
        or ""
    ).strip()
    no_token_id = str(
        candidate.get("env_no_token_id")
        or candidate.get("snapshot_no_token_id")
        or trade_case.get("no_token_id")
        or ""
    ).strip()
    kind = "filled entry" if filled else "live entry"
    if not position_id or not command_id or not venue_order_id:
        raise ValueError(f"{kind} projection repair requires position, command, and order ids")
    if not condition_id or not selected_token_id or not token_id or not no_token_id:
        raise ValueError(f"{kind} projection repair requires CTF condition/token identity")
    if str(trade_case.get("trade_id") or "") not in {position_id, ""}:
        raise ValueError(f"{kind} projection repair decision_log trade_id does not match venue command position_id")
    if str(trade_case.get("token_id") or token_id) != token_id:
        raise ValueError(f"{kind} projection repair decision_log token_id does not match YES token identity")

    shares_dec = _positive_decimal_or_none(candidate.get("fill_filled_size"))
    fill_price_dec = _positive_decimal_or_none(candidate.get("fill_price"))
    if filled and (shares_dec is None or fill_price_dec is None):
        raise ValueError(f"{kind} projection repair requires positive fill economics")
    if not filled:
        shares_dec = Decimal("0")
        fill_price_dec = Decimal("0")
    cost_basis_dec = shares_dec * fill_price_dec
    observed_at = str(
        candidate.get("fill_observed_at")
        or candidate.get("order_fact_observed_at")
        or candidate.get("updated_at")
        or _now_iso()
    )
    command_size = _decimal_or_none(candidate.get("size"))
    command_price = _decimal_or_none(candidate.get("price"))
    command_notional = (
        command_size * command_price
        if command_size is not None and command_price is not None
        else None
    )
    size_usd = _decimal_or_none(trade_case.get("size_usd")) or command_notional or cost_basis_dec
    bin_label = str(trade_case.get("bin_label") or trade_case.get("range_label") or "").strip()
    city = str(trade_case.get("city") or "").strip()
    target_date = str(trade_case.get("target_date") or "").strip()
    direction = str(trade_case.get("direction") or "").strip()
    strategy_key = _canonical_projection_strategy_key(
        str(trade_case.get("strategy_key") or trade_case.get("strategy") or "").strip()
    )
    if not city or not target_date or not bin_label or direction not in {"buy_yes", "buy_no"}:
        raise ValueError(f"{kind} projection repair requires decision_log market identity")
    expected_selected = no_token_id if direction == "buy_no" else token_id
    if selected_token_id != expected_selected:
        raise ValueError("venue command selected token does not match decision direction")
    for surface_name, selected in (
        ("submission envelope", candidate.get("env_selected_outcome_token_id")),
        ("executable snapshot", candidate.get("snapshot_selected_outcome_token_id")),
    ):
        normalized = str(selected or "").strip()
        if normalized and normalized != selected_token_id:
            raise ValueError(f"{surface_name} selected token does not match venue command token")
    if strategy_key not in _CANONICAL_STRATEGY_KEYS:
        raise ValueError(f"{kind} projection repair requires valid strategy_key")
    p_posterior = _decimal_or_none(trade_case.get("p_posterior")) or Decimal("0")
    edge_context = _json_mapping(trade_case.get("edge_context_json"))
    return SimpleNamespace(
        trade_id=position_id,
        state="entered" if filled else "pending_tracked",
        exit_state="",
        chain_state="unknown" if filled else "local_only",
        env="live",
        market_id=condition_id,
        city=city,
        cluster=str(trade_case.get("cluster") or city),
        target_date=target_date,
        bin_label=bin_label,
        direction=direction,
        unit=_case_unit(trade_case),
        size_usd=float(size_usd),
        shares=float(shares_dec),
        cost_basis_usd=float(cost_basis_dec),
        entry_price=float(fill_price_dec),
        p_posterior=float(p_posterior),
        last_monitor_prob=None,
        last_monitor_edge=None,
        last_monitor_market_price=None,
        decision_snapshot_id=str(
            trade_case.get("decision_snapshot_id")
            or edge_context.get("decision_snapshot_id")
            or candidate.get("snapshot_id")
            or ""
        ),
        entry_method=str(trade_case.get("selected_method") or trade_case.get("entry_method") or ""),
        strategy_key=strategy_key,
        strategy=strategy_key,
        edge_source=str(trade_case.get("edge_source") or strategy_key),
        discovery_mode=str(trade_case.get("discovery_mode") or "opening_hunt"),
        token_id=token_id,
        no_token_id=no_token_id,
        condition_id=condition_id,
        order_id=venue_order_id,
        entry_order_id=venue_order_id,
        order_status="filled" if filled else "pending",
        entered_at=observed_at,
        order_posted_at=str(candidate.get("created_at") or observed_at),
        updated_at=observed_at,
        temperature_metric=_case_temperature_metric(trade_case),
        source_trade_fact_id=candidate.get("source_trade_fact_id"),
        fill_states=candidate.get("fill_states"),
        fill_fact_count=candidate.get("fill_fact_count"),
        command_id=command_id,
        decision_id=str(candidate.get("decision_id") or ""),
        decision_log_id=decision_log_id,
        executable_snapshot_id=str(candidate.get("snapshot_id") or ""),
        source_order_fact_id=candidate.get("order_fact_id"),
        order_fact_state=candidate.get("order_fact_state"),
        order_fact_source=candidate.get("order_fact_source"),
    )


def _filled_entry_recovery_position(
    candidate: dict,
    trade_case: dict,
    *,
    decision_log_id: int | None,
) -> SimpleNamespace:
    return _entry_recovery_position(
        candidate,
        trade_case,
        decision_log_id=decision_log_id,
        filled=True,
    )


def _live_entry_recovery_position(
    candidate: dict,
    trade_case: dict,
    *,
    decision_log_id: int | None,
) -> SimpleNamespace:
    return _entry_recovery_position(
        candidate,
        trade_case,
        decision_log_id=decision_log_id,
        filled=False,
    )


def _existing_order_token_projection(
    conn: sqlite3.Connection,
    *,
    position: SimpleNamespace,
) -> dict | None:
    if not _table_exists(conn, "position_current"):
        return None
    order_id = str(position.order_id or "").strip()
    if not order_id:
        return None
    position_id = str(position.trade_id or "").strip()
    selected_token_id = (
        str(position.no_token_id or "").strip()
        if str(position.direction or "") == "buy_no"
        else str(position.token_id or "").strip()
    )
    condition_id = str(position.condition_id or "").strip()
    if not selected_token_id and not condition_id:
        return None
    row = conn.execute(
        """
        SELECT position_id, phase, direction, token_id, no_token_id, condition_id, order_id
          FROM position_current
         WHERE position_id != ?
           AND COALESCE(order_id, '') != ''
           AND lower(order_id) = lower(?)
           AND (
               (? != '' AND (COALESCE(token_id, '') = ? OR COALESCE(no_token_id, '') = ?))
               OR (? != '' AND COALESCE(condition_id, '') = ?)
           )
         LIMIT 1
        """,
        (
            position_id,
            order_id,
            selected_token_id,
            selected_token_id,
            selected_token_id,
            condition_id,
            condition_id,
        ),
    ).fetchone()
    return _dict_row(row) if row is not None else None


def _entry_recovery_event(
    position: SimpleNamespace,
    *,
    sequence_no: int,
    event_type: str,
    occurred_at: str,
    phase_before: str | None,
    phase_after: str,
    order_id: str | None,
    reason: str = "terminal_filled_entry_trade_fact_projection_repair",
    proof_class: str = "filled_entry_command_trade_fact_without_position_current",
) -> dict:
    command_id = str(position.command_id)
    position_id = str(position.trade_id)
    slug = event_type.lower()
    payload = {
        "reason": reason,
        "proof_class": proof_class,
        "command_id": command_id,
        "venue_order_id": position.order_id,
        "source_trade_fact_id": position.source_trade_fact_id,
        "source_order_fact_id": position.source_order_fact_id,
        "order_fact_state": position.order_fact_state,
        "order_fact_source": position.order_fact_source,
        "fill_states": position.fill_states,
        "fill_fact_count": position.fill_fact_count,
        "decision_log_id": position.decision_log_id,
        "executable_snapshot_id": position.executable_snapshot_id,
        "condition_id": position.condition_id,
        "token_id": position.token_id,
    }
    return {
        "event_id": f"{position_id}:recovered_{slug}:{command_id}",
        "position_id": position_id,
        "event_version": 1,
        "sequence_no": sequence_no,
        "event_type": event_type,
        "occurred_at": occurred_at,
        "phase_before": phase_before,
        "phase_after": phase_after,
        "strategy_key": position.strategy_key,
        "decision_id": position.decision_id,
        "snapshot_id": position.decision_snapshot_id or None,
        "order_id": order_id,
        "command_id": command_id,
        "caused_by": f"venue_trade_fact:{position.source_trade_fact_id}",
        "idempotency_key": f"{position_id}:recovered:{slug}:{command_id}",
        "venue_status": str(position.fill_states or "FILLED"),
        "source_module": "src.execution.command_recovery",
        "env": position.env,
        "payload_json": json.dumps(payload, sort_keys=True, default=str),
    }


def _log_filled_entry_execution_fact(
    conn: sqlite3.Connection,
    *,
    position: SimpleNamespace,
    candidate: dict,
) -> None:
    """Keep recovered position truth and execution_fact on the same fill facts."""

    from src.state.db import log_execution_fact

    position_id = str(position.trade_id)
    filled_at = str(candidate.get("fill_observed_at") or position.entered_at or _now_iso())
    posted_at = str(position.order_posted_at or candidate.get("created_at") or filled_at)
    log_execution_fact(
        conn,
        intent_id=f"{position_id}:entry",
        position_id=position_id,
        decision_id=str(position.decision_id or "") or None,
        command_id=str(position.command_id or "") or None,
        order_role="entry",
        strategy_key=str(position.strategy_key or "") or None,
        posted_at=posted_at,
        filled_at=filled_at,
        submitted_price=_float_or_none(candidate.get("price")),
        fill_price=_float_or_none(position.entry_price),
        shares=_float_or_none(position.shares),
        venue_status=str(position.fill_states or "FILLED"),
        terminal_exec_status="filled",
    )


def _append_filled_entry_projection_repair(
    conn: sqlite3.Connection,
    *,
    candidate: dict,
    client=None,
) -> bool:
    from src.engine.lifecycle_events import build_position_current_projection
    from src.state.ledger import append_many_and_project
    from src.state.projection import upsert_position_current

    candidate = _hydrate_command_execution_identity(conn, candidate)
    trade_case, decision_log_id = _decision_log_trade_case_for_command(conn, candidate, client=client)
    if not trade_case:
        logger.info(
            "recovery: filled entry projection repair skipped command %s: "
            "missing decision_log trade_case",
            candidate.get("command_id"),
        )
        return False
    position = _filled_entry_recovery_position(
        candidate,
        trade_case,
        decision_log_id=decision_log_id,
    )
    existing_order_projection = _existing_order_token_projection(conn, position=position)
    if existing_order_projection is not None:
        logger.warning(
            "recovery: filled entry projection repair skipped duplicate order/token projection "
            "for command %s position %s; existing position %s phase=%s",
            position.command_id,
            position.trade_id,
            existing_order_projection.get("position_id"),
            existing_order_projection.get("phase"),
        )
        return False
    projection = build_position_current_projection(position)
    position_id = str(position.trade_id)
    existing_fill = conn.execute(
        """
        SELECT 1
          FROM position_events
         WHERE position_id = ?
           AND event_type = 'ENTRY_ORDER_FILLED'
         LIMIT 1
        """,
        (position_id,),
    ).fetchone()
    if existing_fill is not None:
        upsert_position_current(conn, projection)
        _log_filled_entry_execution_fact(conn, position=position, candidate=candidate)
        return True
    if _latest_position_sequence(conn, position_id) != 0:
        raise ValueError(
            "filled entry projection repair refuses partial position_events without ENTRY_ORDER_FILLED"
        )
    filled_at = str(candidate.get("fill_observed_at") or _now_iso())
    posted_at = str(candidate.get("created_at") or filled_at)
    events = [
        _entry_recovery_event(
            position,
            sequence_no=1,
            event_type="POSITION_OPEN_INTENT",
            occurred_at=posted_at,
            phase_before=None,
            phase_after="pending_entry",
            order_id=None,
        ),
        _entry_recovery_event(
            position,
            sequence_no=2,
            event_type="ENTRY_ORDER_POSTED",
            occurred_at=posted_at,
            phase_before="pending_entry",
            phase_after="pending_entry",
            order_id=position.order_id,
        ),
        _entry_recovery_event(
            position,
            sequence_no=3,
            event_type="ENTRY_ORDER_FILLED",
            occurred_at=filled_at,
            phase_before="pending_entry",
            phase_after="active",
            order_id=position.order_id,
        ),
    ]
    append_many_and_project(conn, events, projection)
    _log_filled_entry_execution_fact(conn, position=position, candidate=candidate)
    return True


def _append_live_entry_projection_repair(
    conn: sqlite3.Connection,
    *,
    candidate: dict,
    client=None,
) -> bool:
    from src.engine.lifecycle_events import build_position_current_projection
    from src.state.ledger import append_many_and_project
    from src.state.projection import upsert_position_current

    candidate = _hydrate_command_execution_identity(conn, candidate)
    trade_case, decision_log_id = _decision_log_trade_case_for_command(conn, candidate, client=client)
    if not trade_case:
        logger.info(
            "recovery: live entry projection repair skipped command %s: "
            "missing EDLI/decision trade_case",
            candidate.get("command_id"),
        )
        return False
    position = _live_entry_recovery_position(
        candidate,
        trade_case,
        decision_log_id=decision_log_id,
    )
    existing_order_projection = _existing_order_token_projection(conn, position=position)
    if existing_order_projection is not None:
        logger.warning(
            "recovery: live entry projection repair skipped duplicate order/token projection "
            "for command %s position %s; existing position %s phase=%s",
            position.command_id,
            position.trade_id,
            existing_order_projection.get("position_id"),
            existing_order_projection.get("phase"),
        )
        return False
    projection = build_position_current_projection(position)
    position_id = str(position.trade_id)
    existing_posted = conn.execute(
        """
        SELECT 1
          FROM position_events
         WHERE position_id = ?
           AND event_type = 'ENTRY_ORDER_POSTED'
         LIMIT 1
        """,
        (position_id,),
    ).fetchone()
    if existing_posted is not None:
        upsert_position_current(conn, projection)
        return True
    if _latest_position_sequence(conn, position_id) != 0:
        raise ValueError(
            "live entry projection repair refuses partial position_events without ENTRY_ORDER_POSTED"
        )
    posted_at = str(candidate.get("created_at") or candidate.get("order_fact_observed_at") or _now_iso())
    events = [
        _entry_recovery_event(
            position,
            sequence_no=1,
            event_type="POSITION_OPEN_INTENT",
            occurred_at=posted_at,
            phase_before=None,
            phase_after="pending_entry",
            order_id=None,
            reason="live_entry_order_fact_projection_repair",
            proof_class="live_entry_command_order_fact_without_position_current",
        ),
        _entry_recovery_event(
            position,
            sequence_no=2,
            event_type="ENTRY_ORDER_POSTED",
            occurred_at=posted_at,
            phase_before="pending_entry",
            phase_after="pending_entry",
            order_id=position.order_id,
            reason="live_entry_order_fact_projection_repair",
            proof_class="live_entry_command_order_fact_without_position_current",
        ),
    ]
    append_many_and_project(conn, events, projection)
    return True


def reconcile_live_entry_projection_repairs(conn: sqlite3.Connection, client=None) -> dict:
    """Repair open ACKED ENTRY command truth when initial pending projection failed."""

    summary = {"scanned": 0, "advanced": 0, "stayed": 0, "errors": 0}
    for candidate in _latest_unprojected_live_entry_candidates(conn):
        summary["scanned"] += 1
        command_id = str(candidate.get("command_id") or "")
        conn.execute("SAVEPOINT sp_live_entry_projection_repair")
        try:
            advanced = _append_live_entry_projection_repair(conn, candidate=candidate, client=client)
            conn.execute("RELEASE SAVEPOINT sp_live_entry_projection_repair")
            if advanced:
                summary["advanced"] += 1
            else:
                summary["stayed"] += 1
        except Exception as exc:
            conn.execute("ROLLBACK TO SAVEPOINT sp_live_entry_projection_repair")
            conn.execute("RELEASE SAVEPOINT sp_live_entry_projection_repair")
            logger.error(
                "recovery: live entry projection repair failed for command %s: %s",
                command_id,
                exc,
            )
            summary["errors"] += 1
    return summary


def ensure_live_entry_projection_for_command(
    conn: sqlite3.Connection,
    *,
    command_id: str,
    client=None,
) -> dict:
    """Project one ACKED live entry order into pending_entry immediately.

    The periodic recovery loop is a crash/backfill lane. A newly ACKED live
    order must not wait for that loop before it becomes visible to continuous
    redecision.
    """

    command_id = str(command_id or "").strip()
    if not command_id:
        raise ValueError("live entry projection requires command_id")
    if not _table_exists(conn, "venue_commands"):
        raise ValueError("live entry projection requires venue_commands")
    current = conn.execute(
        """
        SELECT cmd.state, cmd.intent_kind, cmd.side, pc.position_id AS projected_position_id
          FROM venue_commands cmd
          LEFT JOIN position_current pc
            ON pc.position_id = cmd.position_id
         WHERE cmd.command_id = ?
         LIMIT 1
        """,
        (command_id,),
    ).fetchone()
    if current is None:
        raise ValueError(f"live entry projection command not found: {command_id}")
    current_map = _dict_row(current)
    if current_map.get("projected_position_id"):
        return {"scanned": 0, "advanced": 0, "stayed": 1, "errors": 0}
    if (
        str(current_map.get("intent_kind") or "").upper() != "ENTRY"
        or str(current_map.get("side") or "").upper() != "BUY"
        or str(current_map.get("state") or "").upper() not in {"ACKED", "POST_ACKED"}
    ):
        return {"scanned": 0, "advanced": 0, "stayed": 1, "errors": 0}

    candidates = [
        candidate
        for candidate in _latest_unprojected_live_entry_candidates(conn)
        if str(candidate.get("command_id") or "") == command_id
    ]
    summary = {"scanned": len(candidates), "advanced": 0, "stayed": 0, "errors": 0}
    if not candidates:
        raise ValueError(
            f"ACKED live entry command {command_id} has no open-order projection candidate"
        )
    for candidate in candidates:
        try:
            advanced = _append_live_entry_projection_repair(conn, candidate=candidate, client=client)
            if advanced:
                summary["advanced"] += 1
            else:
                summary["stayed"] += 1
        except Exception:
            summary["errors"] += 1
            raise
    return summary


def _canonical_payload_hash(payload: Mapping[str, object]) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()


def _edli_confirmed_legacy_command_candidates(conn: sqlite3.Connection) -> list[dict]:
    """Find legacy venue_commands stranded before terminalization despite EDLI fill proof."""

    events_ref = _edli_live_order_events_ref(conn)
    if events_ref is None or not _table_exists(conn, "venue_commands"):
        return []
    sql = f"""
        WITH ack AS (
            SELECT json_extract(payload_json, '$.execution_command_id') AS execution_command_id,
                   json_extract(payload_json, '$.final_intent_id') AS final_intent_id,
                   json_extract(payload_json, '$.venue_order_id') AS venue_order_id,
                   json_extract(payload_json, '$.recovered_trade_id') AS recovered_trade_id,
                   json_extract(payload_json, '$.transaction_hash') AS ack_transaction_hash,
                   occurred_at AS acked_at,
                   payload_json AS ack_payload_json,
                   rowid AS ack_rowid
              FROM {events_ref}
             WHERE event_type = 'VenueSubmitAcknowledged'
        ),
        trade AS (
            SELECT json_extract(payload_json, '$.final_intent_id') AS final_intent_id,
                   json_extract(payload_json, '$.venue_order_id') AS venue_order_id,
                   json_extract(payload_json, '$.trade_id') AS trade_id,
                   json_extract(payload_json, '$.trade_status') AS trade_status,
                   json_extract(payload_json, '$.filled_size') AS filled_size,
                   json_extract(payload_json, '$.fill_price') AS fill_price,
                   json_extract(payload_json, '$.avg_fill_price') AS avg_fill_price,
                   json_extract(payload_json, '$.fees') AS fees,
                   json_extract(payload_json, '$.transaction_hash') AS trade_transaction_hash,
                   occurred_at AS filled_at,
                   payload_json AS trade_payload_json,
                   rowid AS trade_rowid
              FROM {events_ref}
             WHERE event_type = 'UserTradeObserved'
               AND json_extract(payload_json, '$.fill_authority_state') = 'FILL_CONFIRMED'
        ),
        ranked AS (
            SELECT cmd.command_id,
                   cmd.state AS command_state,
                   cmd.venue_order_id AS command_venue_order_id,
                   cmd.size AS command_size,
                   cmd.price AS command_price,
                   ack.execution_command_id,
                   ack.final_intent_id,
                   ack.venue_order_id AS ack_venue_order_id,
                   ack.recovered_trade_id,
                   ack.ack_transaction_hash,
                   ack.acked_at,
                   ack.ack_payload_json,
                   trade.venue_order_id AS trade_venue_order_id,
                   trade.trade_id,
                   trade.trade_status,
                   trade.filled_size,
                   trade.fill_price,
                   trade.avg_fill_price,
                   trade.fees,
                   trade.trade_transaction_hash,
                   trade.filled_at,
                   trade.trade_payload_json,
                   ROW_NUMBER() OVER (
                       PARTITION BY cmd.command_id
                       ORDER BY trade.trade_rowid DESC, ack.ack_rowid DESC
                   ) AS rn
              FROM venue_commands cmd
              JOIN ack
                ON ack.execution_command_id = cmd.decision_id
              JOIN trade
                ON trade.final_intent_id = ack.final_intent_id
               AND trade.venue_order_id = ack.venue_order_id
             WHERE cmd.intent_kind = 'ENTRY'
               AND cmd.side = 'BUY'
               AND cmd.state IN ('SUBMITTING', 'UNKNOWN', 'SUBMIT_UNKNOWN_SIDE_EFFECT', 'ACKED', 'POST_ACKED', 'REVIEW_REQUIRED')
               AND COALESCE(ack.venue_order_id, '') != ''
               AND COALESCE(trade.trade_id, '') != ''
               AND CAST(COALESCE(trade.filled_size, '0') AS REAL) > 0
               AND CAST(COALESCE(trade.fill_price, trade.avg_fill_price, '0') AS REAL) > 0
               AND (COALESCE(cmd.venue_order_id, '') = '' OR cmd.venue_order_id = ack.venue_order_id)
               AND NOT EXISTS (
                   SELECT 1
                     FROM venue_trade_facts fact
                    WHERE fact.command_id = cmd.command_id
                      AND fact.trade_id = trade.trade_id
               )
        )
        SELECT *
          FROM ranked
         WHERE rn = 1
         ORDER BY filled_at, command_id
    """
    return [_dict_row(row) for row in conn.execute(sql).fetchall()]


def _append_edli_confirmed_legacy_command_repair(
    conn: sqlite3.Connection,
    *,
    candidate: dict,
) -> None:
    command_id = str(candidate.get("command_id") or "")
    venue_order_id = str(candidate.get("ack_venue_order_id") or candidate.get("trade_venue_order_id") or "")
    trade_id = str(candidate.get("trade_id") or candidate.get("recovered_trade_id") or "")
    filled_size = str(candidate.get("filled_size") or "")
    fill_price = str(candidate.get("fill_price") or candidate.get("avg_fill_price") or "")
    acked_at = str(candidate.get("acked_at") or candidate.get("filled_at") or _now_iso())
    filled_at = str(candidate.get("filled_at") or acked_at)
    if not command_id or not venue_order_id or not trade_id or not filled_size or not fill_price:
        raise ValueError("EDLI confirmed command repair requires command/order/trade/fill identity")

    ack_payload = {
        "venue_order_id": venue_order_id,
        "venue_status": "MATCHED",
        "source": "edli_live_order_reconcile",
        "edli_execution_command_id": candidate.get("execution_command_id"),
        "edli_final_intent_id": candidate.get("final_intent_id"),
        "recovered_trade_id": trade_id,
        "recovered_from": "edli_confirmed_fill",
    }
    current_state = str(candidate.get("command_state") or "")
    if current_state in {
        CommandState.SUBMITTING.value,
        CommandState.UNKNOWN.value,
        CommandState.SUBMIT_UNKNOWN_SIDE_EFFECT.value,
        CommandState.POST_ACKED.value,
    }:
        append_event(
            conn,
            command_id=command_id,
            event_type=CommandEventType.SUBMIT_ACKED.value,
            occurred_at=acked_at,
            payload=ack_payload,
        )

    order_payload = {
        "source": "edli_live_order_reconcile",
        "venue_order_id": venue_order_id,
        "trade_id": trade_id,
        "ack_payload": _json_dict(candidate.get("ack_payload_json")),
        "trade_payload": _json_dict(candidate.get("trade_payload_json")),
    }
    append_order_fact(
        conn,
        venue_order_id=venue_order_id,
        command_id=command_id,
        state="MATCHED",
        remaining_size="0",
        matched_size=filled_size,
        source="REST",
        observed_at=filled_at,
        venue_timestamp=filled_at,
        raw_payload_hash=_canonical_payload_hash(order_payload),
        raw_payload_json=order_payload,
    )
    trade_payload = {
        "source": "edli_live_order_reconcile",
        "venue_order_id": venue_order_id,
        "trade_id": trade_id,
        "trade_status": candidate.get("trade_status"),
        "filled_size": filled_size,
        "fill_price": fill_price,
        "edli_final_intent_id": candidate.get("final_intent_id"),
        "raw": _json_dict(candidate.get("trade_payload_json")),
    }
    append_trade_fact(
        conn,
        trade_id=trade_id,
        venue_order_id=venue_order_id,
        command_id=command_id,
        state="CONFIRMED",
        filled_size=filled_size,
        fill_price=fill_price,
        source="REST",
        observed_at=filled_at,
        venue_timestamp=filled_at,
        raw_payload_hash=_canonical_payload_hash(trade_payload),
        raw_payload_json=trade_payload,
        fee_paid_micro=None,
        tx_hash=str(candidate.get("trade_transaction_hash") or candidate.get("ack_transaction_hash") or "") or None,
    )
    append_event(
        conn,
        command_id=command_id,
        event_type=CommandEventType.FILL_CONFIRMED.value,
        occurred_at=filled_at,
        payload={
            "venue_order_id": venue_order_id,
            "venue_status": "CONFIRMED",
            "trade_id": trade_id,
            "filled_size": filled_size,
            "fill_price": fill_price,
            "source": "edli_live_order_reconcile",
            "edli_final_intent_id": candidate.get("final_intent_id"),
        },
    )


def reconcile_edli_confirmed_legacy_command_repairs(conn: sqlite3.Connection) -> dict:
    """Terminalize legacy command rows when EDLI aggregate already has confirmed fill proof."""

    summary = {"scanned": 0, "advanced": 0, "stayed": 0, "errors": 0}
    for candidate in _edli_confirmed_legacy_command_candidates(conn):
        summary["scanned"] += 1
        command_id = str(candidate.get("command_id") or "")
        conn.execute("SAVEPOINT edli_confirmed_command_repair")
        try:
            _append_edli_confirmed_legacy_command_repair(conn, candidate=candidate)
            verified = conn.execute(
                "SELECT state, venue_order_id FROM venue_commands WHERE command_id = ?",
                (command_id,),
            ).fetchone()
            if verified is None or str(verified["state"] or "") != CommandState.FILLED.value:
                raise RuntimeError("EDLI confirmed command repair did not terminalize command")
            conn.execute("RELEASE SAVEPOINT edli_confirmed_command_repair")
            summary["advanced"] += 1
        except Exception as exc:
            conn.execute("ROLLBACK TO SAVEPOINT edli_confirmed_command_repair")
            conn.execute("RELEASE SAVEPOINT edli_confirmed_command_repair")
            logger.error(
                "recovery: EDLI confirmed command repair failed for command %s: %s",
                command_id,
                exc,
            )
            summary["errors"] += 1
    return summary


def reconcile_filled_entry_projection_repairs(conn: sqlite3.Connection, client=None) -> dict:
    """Repair filled ENTRY command truth when initial position projection failed."""

    summary = {"scanned": 0, "advanced": 0, "stayed": 0, "errors": 0}
    for candidate in _latest_unprojected_filled_entry_candidates(conn):
        summary["scanned"] += 1
        command_id = str(candidate.get("command_id") or "")
        safe_command_id = "".join(ch if ch.isalnum() else "_" for ch in command_id)
        sp_name = f"sp_filled_entry_projection_{safe_command_id}"
        conn.execute("SAVEPOINT " + sp_name)
        try:
            advanced = _append_filled_entry_projection_repair(
                conn,
                candidate=candidate,
                client=client,
            )
            conn.execute("RELEASE SAVEPOINT " + sp_name)
            if advanced:
                summary["advanced"] += 1
            else:
                summary["stayed"] += 1
        except Exception as exc:
            conn.execute("ROLLBACK TO SAVEPOINT " + sp_name)
            conn.execute("RELEASE SAVEPOINT " + sp_name)
            logger.error(
                "recovery: filled entry projection repair failed for command %s: %s",
                command_id,
                exc,
            )
            summary["errors"] += 1
    return summary


_EDLI_ENTRY_POSTERIOR_REPAIR_PHASES = ("pending_entry", "active", "day0_window", "pending_exit")
_HARD_TERMINAL_REPAIR_PHASES = ("voided", "settled", "economically_closed", "admin_closed")
_RUNTIME_OPEN_REPAIR_PHASES = ("pending_entry", "active", "day0_window", "pending_exit")


def _edli_entry_posterior_repair_candidates(conn: sqlite3.Connection) -> list[dict]:
    if not all(_table_exists(conn, table) for table in ("position_current", "venue_commands")):
        return []
    if _decision_certificates_ref(conn) is None:
        return []
    phase_placeholders = ", ".join("?" for _ in _EDLI_ENTRY_POSTERIOR_REPAIR_PHASES)
    rows = conn.execute(
        f"""
        SELECT pc.position_id,
               pc.p_posterior AS current_p_posterior,
               pc.entry_method AS current_entry_method,
               pc.phase,
               pc.order_id AS position_order_id,
               cmd.*
          FROM position_current pc
          JOIN venue_commands cmd
            ON cmd.position_id = pc.position_id
            OR (
                COALESCE(pc.order_id, '') != ''
                AND COALESCE(cmd.venue_order_id, '') != ''
                AND lower(pc.order_id) = lower(cmd.venue_order_id)
            )
         WHERE pc.phase IN ({phase_placeholders})
           AND cmd.intent_kind = 'ENTRY'
           AND cmd.side = 'BUY'
           AND cmd.decision_id LIKE 'edli_exec_cmd:%'
           AND (
               pc.p_posterior IS NULL
               OR CAST(pc.p_posterior AS REAL) <= 0
               OR COALESCE(pc.entry_method, '') != 'qkernel_spine'
           )
         ORDER BY pc.updated_at DESC, cmd.created_at DESC
        """,
        tuple(_EDLI_ENTRY_POSTERIOR_REPAIR_PHASES),
    ).fetchall()
    candidates: list[dict] = []
    seen: set[str] = set()
    for row in rows:
        candidate = _dict_row(row)
        position_id = str(candidate.get("position_id") or "")
        if not position_id or position_id in seen:
            continue
        seen.add(position_id)
        candidates.append(candidate)
    return candidates


def reconcile_edli_entry_posterior_projection_repairs(
    conn: sqlite3.Connection,
    client=None,
) -> dict:
    """Backfill EDLI entry authority for positions from their Actionable certificate.

    Live order projection can create a visible pending/active row before the
    confirmed fill bridge runs. That row is only safe for monitoring if its
    entry posterior and entry method are the same qkernel authority that
    justified submission. This lane is idempotent and strictly evidence-bound:
    no EDLI Actionable certificate qkernel authority, no update.
    """

    summary = {"scanned": 0, "advanced": 0, "stayed": 0, "errors": 0}
    for candidate in _edli_entry_posterior_repair_candidates(conn):
        summary["scanned"] += 1
        position_id = str(candidate.get("position_id") or "")
        sp_name = "sp_edli_entry_posterior_" + "".join(
            ch if ch.isalnum() else "_" for ch in position_id
        )[:80]
        conn.execute("SAVEPOINT " + sp_name)
        try:
            hydrated = _hydrate_command_execution_identity(conn, candidate)
            trade_case, _decision_log_id = _decision_log_trade_case_for_command(
                conn,
                hydrated,
                client=client,
            )
            posterior = _positive_probability_or_none(trade_case.get("p_posterior"))
            entry_method = str(trade_case.get("entry_method") or "").strip()
            if posterior is None or entry_method != "qkernel_spine":
                conn.execute("RELEASE SAVEPOINT " + sp_name)
                summary["stayed"] += 1
                continue
            cursor = conn.execute(
                """
                UPDATE position_current
                   SET p_posterior = ?,
                       entry_method = ?,
                       updated_at = ?
                 WHERE position_id = ?
                   AND (
                       p_posterior IS NULL
                       OR CAST(p_posterior AS REAL) <= 0
                       OR COALESCE(entry_method, '') != 'qkernel_spine'
                   )
                """,
                (posterior, entry_method, _now_iso(), position_id),
            )
            if cursor.rowcount > 0:
                summary["advanced"] += 1
            else:
                summary["stayed"] += 1
            conn.execute("RELEASE SAVEPOINT " + sp_name)
        except Exception as exc:
            conn.execute("ROLLBACK TO SAVEPOINT " + sp_name)
            conn.execute("RELEASE SAVEPOINT " + sp_name)
            logger.error(
                "recovery: EDLI entry posterior projection repair failed for position %s: %s",
                position_id,
                exc,
            )
            summary["errors"] += 1
    return summary


_INVALID_ENTRY_AUTHORITY_OPEN_PHASES = ("pending_entry", "active", "day0_window", "pending_exit")
INVALID_ENTRY_AUTHORITY_QUARANTINE_REASON = "invalid_entry_actionable_certificate_authority"


def _invalid_open_entry_authority_candidates(conn: sqlite3.Connection) -> list[dict]:
    if not all(_table_exists(conn, table) for table in ("position_current", "venue_commands")):
        return []
    if _decision_certificates_ref(conn) is None:
        return []
    phase_placeholders = ", ".join("?" for _ in _INVALID_ENTRY_AUTHORITY_OPEN_PHASES)
    rows = conn.execute(
        f"""
        WITH latest_entry AS (
            SELECT
                pc.*,
                cmd.command_id AS entry_command_id,
                cmd.decision_id AS entry_decision_id,
                cmd.token_id AS entry_selected_token_id,
                cmd.state AS entry_command_state,
                cmd.venue_order_id AS entry_venue_order_id,
                ROW_NUMBER() OVER (
                    PARTITION BY pc.position_id
                    ORDER BY datetime(cmd.created_at) DESC, cmd.command_id DESC
                ) AS rn
              FROM position_current pc
              JOIN venue_commands cmd
                ON cmd.position_id = pc.position_id
             WHERE pc.phase IN ({phase_placeholders})
               AND cmd.intent_kind = 'ENTRY'
               AND cmd.side = 'BUY'
        )
        SELECT *
          FROM latest_entry
         WHERE rn = 1
        """,
        tuple(_INVALID_ENTRY_AUTHORITY_OPEN_PHASES),
    ).fetchall()
    out: list[dict] = []
    for row in rows:
        candidate = _dict_row(row)
        event_id = _edli_event_id_from_decision_id(str(candidate.get("entry_decision_id") or ""))
        if not event_id:
            continue
        payload = _verified_edli_actionable_payload(
            conn,
            event_id=event_id,
            token_id=str(candidate.get("entry_selected_token_id") or ""),
        )
        if payload:
            continue
        candidate["entry_event_id"] = event_id
        candidate["quarantine_reason"] = INVALID_ENTRY_AUTHORITY_QUARANTINE_REASON
        out.append(candidate)
    return out


def _quarantine_open_entry_invalid_authority(
    conn: sqlite3.Connection,
    *,
    candidate: Mapping[str, Any],
) -> bool:
    from src.state.ledger import append_many_and_project

    position_id = str(candidate.get("position_id") or "").strip()
    if not position_id:
        return False
    idempotency_key = f"{position_id}:invalid_entry_authority_quarantine"
    if (
        _table_exists(conn, "position_events")
        and conn.execute(
            "SELECT 1 FROM position_events WHERE idempotency_key = ? LIMIT 1",
            (idempotency_key,),
        ).fetchone()
        is not None
    ):
        return False
    now = _now_iso()
    phase_before = str(candidate.get("phase") or "")
    sequence_no = _latest_position_sequence(conn, position_id) + 1
    projection_cols = _table_columns(conn, "position_current")
    projection = {
        column: candidate.get(column)
        for column in projection_cols
        if column in candidate
    }
    projection.update(
        {
            "position_id": position_id,
            "phase": "quarantined",
            "trade_id": candidate.get("trade_id") or position_id,
            "chain_state": "entry_authority_quarantined",
            "exit_reason": INVALID_ENTRY_AUTHORITY_QUARANTINE_REASON,
            "updated_at": now,
        }
    )
    payload = {
        "schema_version": 1,
        "reason": INVALID_ENTRY_AUTHORITY_QUARANTINE_REASON,
        "proof_class": "open_position_entry_actionable_certificate_not_current_valid",
        "position_id": position_id,
        "phase_before": phase_before,
        "phase_after": "quarantined",
        "entry_command_id": str(candidate.get("entry_command_id") or ""),
        "entry_decision_id": str(candidate.get("entry_decision_id") or ""),
        "entry_event_id": str(candidate.get("entry_event_id") or ""),
        "entry_selected_token_id": str(candidate.get("entry_selected_token_id") or ""),
        "entry_command_state": str(candidate.get("entry_command_state") or ""),
        "entry_venue_order_id": str(candidate.get("entry_venue_order_id") or ""),
        "source_proof": {
            "source_function": "command_recovery.reconcile_invalid_open_entry_authority_quarantines",
            "source_reason": "EDLI entry certificate is quarantined, missing, or fails current verifier",
        },
    }
    event = {
        "event_id": f"{position_id}:invalid_entry_authority_quarantined:{sequence_no}",
        "position_id": position_id,
        "event_version": 1,
        "sequence_no": sequence_no,
        "event_type": "REVIEW_REQUIRED",
        "occurred_at": now,
        "phase_before": phase_before or None,
        "phase_after": "quarantined",
        "strategy_key": str(candidate.get("strategy_key") or "center_buy"),
        "decision_id": str(candidate.get("entry_decision_id") or ""),
        "snapshot_id": candidate.get("decision_snapshot_id"),
        "order_id": candidate.get("order_id") or candidate.get("entry_venue_order_id"),
        "command_id": str(candidate.get("entry_command_id") or ""),
        "caused_by": INVALID_ENTRY_AUTHORITY_QUARANTINE_REASON,
        "idempotency_key": idempotency_key,
        "venue_status": str(candidate.get("entry_command_state") or ""),
        "source_module": "src.execution.command_recovery",
        "env": _latest_position_env(conn, position_id),
        "payload_json": json.dumps(payload, sort_keys=True),
    }
    append_many_and_project(conn, [event], projection)
    return True


def reconcile_invalid_open_entry_authority_quarantines(conn: sqlite3.Connection) -> dict:
    """Quarantine open positions whose EDLI entry certificate is no longer live-valid."""

    summary = {"scanned": 0, "advanced": 0, "stayed": 0, "errors": 0}
    for candidate in _invalid_open_entry_authority_candidates(conn):
        summary["scanned"] += 1
        position_id = str(candidate.get("position_id") or "")
        sp_name = "sp_invalid_entry_authority_" + "".join(
            ch if ch.isalnum() else "_" for ch in position_id
        )[:80]
        conn.execute("SAVEPOINT " + sp_name)
        try:
            advanced = _quarantine_open_entry_invalid_authority(conn, candidate=candidate)
            conn.execute("RELEASE SAVEPOINT " + sp_name)
            if advanced:
                summary["advanced"] += 1
            else:
                summary["stayed"] += 1
        except Exception as exc:
            conn.execute("ROLLBACK TO SAVEPOINT " + sp_name)
            conn.execute("RELEASE SAVEPOINT " + sp_name)
            logger.error(
                "recovery: invalid open entry authority quarantine failed for position %s: %s",
                position_id,
                exc,
            )
            summary["errors"] += 1
    return summary


def _hard_terminal_projection_repair_candidates(conn: sqlite3.Connection) -> list[dict]:
    if not all(_table_exists(conn, table) for table in ("position_current", "position_events")):
        return []
    open_placeholders = ", ".join("?" for _ in _RUNTIME_OPEN_REPAIR_PHASES)
    terminal_placeholders = ", ".join("?" for _ in _HARD_TERMINAL_REPAIR_PHASES)
    rows = conn.execute(
        f"""
        WITH latest_terminal AS (
            SELECT
                position_id,
                event_type,
                phase_before,
                phase_after,
                sequence_no,
                occurred_at,
                payload_json,
                ROW_NUMBER() OVER (
                    PARTITION BY position_id
                    ORDER BY sequence_no DESC, datetime(occurred_at) DESC
                ) AS rn
              FROM position_events
             WHERE LOWER(COALESCE(phase_after, '')) IN ({terminal_placeholders})
        )
        SELECT
            pc.position_id,
            pc.phase AS current_phase,
            pc.chain_shares,
            pc.shares,
            pc.city,
            pc.target_date,
            pc.temperature_metric,
            pc.bin_label,
            pc.direction,
            lt.event_type,
            lt.phase_before,
            lt.phase_after,
            lt.sequence_no,
            lt.occurred_at,
            lt.payload_json
          FROM position_current pc
          JOIN latest_terminal lt
            ON lt.position_id = pc.position_id
           AND lt.rn = 1
         WHERE pc.phase IN ({open_placeholders})
           AND LOWER(COALESCE(lt.phase_after, '')) != LOWER(COALESCE(pc.phase, ''))
         ORDER BY datetime(lt.occurred_at) DESC, lt.sequence_no DESC
        """,
        tuple(_HARD_TERMINAL_REPAIR_PHASES) + tuple(_RUNTIME_OPEN_REPAIR_PHASES),
    ).fetchall()
    return [_dict_row(row) for row in rows]


def reconcile_hard_terminal_position_projection_repairs(conn: sqlite3.Connection) -> dict:
    """Restore ``position_current.phase`` when a durable terminal event already exists.

    This repairs projection drift only. It does not invent a terminal event and
    it does not touch venue/chain state; the latest position_events terminal row
    is the authority.
    """

    summary = {"scanned": 0, "advanced": 0, "stayed": 0, "errors": 0}
    for candidate in _hard_terminal_projection_repair_candidates(conn):
        summary["scanned"] += 1
        position_id = str(candidate.get("position_id") or "")
        terminal_phase = str(candidate.get("phase_after") or "").strip().lower()
        if terminal_phase not in _HARD_TERMINAL_REPAIR_PHASES:
            summary["stayed"] += 1
            continue
        sp_name = "sp_hard_terminal_projection_" + "".join(
            ch if ch.isalnum() else "_" for ch in position_id
        )[:80]
        conn.execute("SAVEPOINT " + sp_name)
        try:
            cursor = conn.execute(
                f"""
                UPDATE position_current
                   SET phase = ?,
                       updated_at = ?
                 WHERE position_id = ?
                   AND phase IN ({", ".join("?" for _ in _RUNTIME_OPEN_REPAIR_PHASES)})
                """,
                (terminal_phase, _now_iso(), position_id, *_RUNTIME_OPEN_REPAIR_PHASES),
            )
            conn.execute("RELEASE SAVEPOINT " + sp_name)
            if cursor.rowcount > 0:
                summary["advanced"] += 1
            else:
                summary["stayed"] += 1
        except Exception as exc:
            conn.execute("ROLLBACK TO SAVEPOINT " + sp_name)
            conn.execute("RELEASE SAVEPOINT " + sp_name)
            logger.error(
                "recovery: hard-terminal projection repair failed for position %s: %s",
                position_id,
                exc,
            )
            summary["errors"] += 1
    return summary


def reconcile_filled_entry_position_link_repairs(conn: sqlite3.Connection) -> dict:
    """Relink filled ENTRY commands to an already-materialized position row."""

    from src.state.venue_command_repo import repair_command_position_link_if_orphaned

    summary = {"scanned": 0, "advanced": 0, "stayed": 0, "errors": 0}
    for candidate in _filled_entry_position_link_repair_candidates(conn):
        summary["scanned"] += 1
        command_id = str(candidate.get("command_id") or "")
        canonical_position_id = str(candidate.get("canonical_position_id") or "")
        if int(candidate.get("canonical_match_count") or 0) != 1 or not canonical_position_id:
            logger.warning(
                "recovery: filled entry position-link repair skipped command %s: "
                "ambiguous canonical matches=%s",
                command_id,
                candidate.get("canonical_match_count"),
            )
            summary["stayed"] += 1
            continue
        safe_command_id = "".join(ch if ch.isalnum() else "_" for ch in command_id)
        sp_name = f"sp_filled_entry_link_{safe_command_id}"
        conn.execute("SAVEPOINT " + sp_name)
        try:
            advanced = repair_command_position_link_if_orphaned(
                conn,
                command_id=command_id,
                canonical_position_id=canonical_position_id,
                occurred_at=str(candidate.get("fill_observed_at") or _now_iso()),
                reason="filled_entry_existing_order_token_projection",
            )
            conn.execute("RELEASE SAVEPOINT " + sp_name)
            if advanced:
                summary["advanced"] += 1
            else:
                summary["stayed"] += 1
        except Exception as exc:
            conn.execute("ROLLBACK TO SAVEPOINT " + sp_name)
            conn.execute("RELEASE SAVEPOINT " + sp_name)
            logger.error(
                "recovery: filled entry position-link repair failed for command %s: %s",
                command_id,
                exc,
            )
            summary["errors"] += 1
    return summary


def _filled_entry_lot_materialization_candidates(conn: sqlite3.Connection) -> list[dict]:
    required = {
        "venue_commands",
        "venue_trade_facts",
        "position_current",
        "position_lots",
        "trade_decisions",
    }
    if not all(_table_exists(conn, table) for table in required):
        return []
    sql = "WITH " + _canonical_trade_fact_cte() + """,
        entry_fill AS (
            SELECT fact.command_id,
                   COUNT(*) AS fill_fact_count,
                   SUM(CAST(fact.filled_size AS REAL)) AS filled_size,
                   SUM(CAST(fact.filled_size AS REAL) * CAST(fact.fill_price AS REAL))
                       / SUM(CAST(fact.filled_size AS REAL)) AS fill_price,
                   MAX(fact.observed_at) AS observed_at,
                   MAX(fact.venue_timestamp) AS venue_timestamp,
                   GROUP_CONCAT(DISTINCT fact.state) AS fill_states,
                   MAX(fact.trade_fact_id) AS trade_fact_id
              FROM canonical_trade_fact fact
             WHERE fact.state IN ('MATCHED', 'MINED', 'CONFIRMED')
               AND CAST(COALESCE(fact.filled_size, '0') AS REAL) > 0
               AND CAST(COALESCE(fact.fill_price, '0') AS REAL) > 0
             GROUP BY fact.command_id
        )
        SELECT cmd.command_id,
               cmd.position_id,
               cmd.decision_id,
               cmd.intent_kind,
               cmd.state AS cmd_state,
               cmd.side,
               cmd.market_id,
               cmd.token_id,
               cmd.size AS cmd_size,
               cmd.price AS cmd_price,
               cmd.created_at AS cmd_created_at,
               fact.trade_fact_id,
               fact.trade_id,
               fact.state AS trade_state,
               fact.filled_size,
               fact.fill_price,
               fact.source,
               fact.observed_at,
               fact.venue_timestamp
          FROM venue_commands cmd
          LEFT JOIN position_current pc
            ON pc.position_id = cmd.position_id
          JOIN canonical_trade_fact fact
            ON fact.command_id = cmd.command_id
          LEFT JOIN position_lots lot
            ON lot.source_trade_fact_id = fact.trade_fact_id
         WHERE cmd.intent_kind = 'ENTRY'
           AND cmd.side = 'BUY'
           AND cmd.state = 'FILLED'
           AND fact.state IN ('MATCHED', 'MINED', 'CONFIRMED')
           AND CAST(COALESCE(fact.filled_size, '0') AS REAL) > 0
           AND CAST(COALESCE(fact.fill_price, '0') AS REAL) > 0
           AND lot.lot_id IS NULL
           AND EXISTS (
               SELECT 1
                 FROM trade_decisions td
                WHERE td.runtime_trade_id = cmd.position_id
                   OR CAST(td.trade_id AS TEXT) = cmd.position_id
                   OR CAST(td.trade_id AS TEXT) = cmd.decision_id
           )
           AND NOT EXISTS (
               SELECT 1
                 FROM position_lots trade_lot
                 JOIN venue_trade_facts lot_fact
                   ON lot_fact.trade_fact_id = trade_lot.source_trade_fact_id
                WHERE lot_fact.command_id = fact.command_id
                  AND lot_fact.trade_id = fact.trade_id
                  AND trade_lot.state IN ('OPTIMISTIC_EXPOSURE', 'CONFIRMED_EXPOSURE')
           )
         ORDER BY fact.observed_at, fact.trade_fact_id
        """
    rows = conn.execute(sql).fetchall()
    return [_dict_row(row) for row in rows]


def _append_filled_entry_position_lot_repair(
    conn: sqlite3.Connection,
    *,
    candidate: dict,
) -> bool:
    from src.state.venue_command_repo import append_position_lot, resolve_position_lot_id_for_command

    command_id = str(candidate.get("command_id") or "")
    command = {
        "command_id": command_id,
        "position_id": candidate.get("position_id"),
        "decision_id": candidate.get("decision_id"),
        "intent_kind": candidate.get("intent_kind"),
        "side": candidate.get("side"),
        "market_id": candidate.get("market_id"),
        "token_id": candidate.get("token_id"),
    }
    position_lot_id = resolve_position_lot_id_for_command(conn, command)
    if position_lot_id is None:
        return False
    trade_state = str(candidate.get("trade_state") or "")
    lot_state = "CONFIRMED_EXPOSURE" if trade_state == "CONFIRMED" else "OPTIMISTIC_EXPOSURE"
    observed_at = str(candidate.get("observed_at") or _now_iso())
    append_position_lot(
        conn,
        position_id=position_lot_id,
        state=lot_state,
        shares=str(candidate["filled_size"]),
        entry_price_avg=str(candidate["fill_price"]),
        source_command_id=command_id,
        source_trade_fact_id=int(candidate["trade_fact_id"]),
        captured_at=observed_at,
        state_changed_at=observed_at,
        source=str(candidate.get("source") or "REST"),
        observed_at=observed_at,
        venue_timestamp=candidate.get("venue_timestamp"),
        raw_payload_json={
            "source": "command_recovery_filled_entry_position_lot_repair",
            "proof_class": "filled_entry_command_trade_fact_without_position_lot",
            "command_id": command_id,
            "position_id": str(candidate.get("position_id") or ""),
            "trade_fact_id": int(candidate["trade_fact_id"]),
            "trade_id": str(candidate.get("trade_id") or ""),
            "trade_state": trade_state,
            "market_id": str(candidate.get("market_id") or ""),
            "token_id": str(candidate.get("token_id") or ""),
        },
    )
    _log_filled_entry_trade_candidate_execution_fact(conn, candidate=candidate)
    return True


def _filled_entry_execution_fact_repair_candidates(conn: sqlite3.Connection) -> list[dict]:
    required = {
        "venue_commands",
        "venue_order_facts",
        "venue_trade_facts",
        "execution_fact",
    }
    if not all(_table_exists(conn, table) for table in required):
        return []
    sql = (
        "WITH "
        + _canonical_trade_fact_cte()
        + ",\n"
        + _canonical_order_truth_cte()
        + """,
        latest_order AS (
            SELECT truth.command_id,
                   truth.venue_order_id,
                   truth.remaining_size,
                   truth.matched_size,
                   truth.state
              FROM canonical_order_truth truth
             WHERE NOT EXISTS (
                   SELECT 1
                     FROM canonical_order_truth stronger
                    WHERE stronger.command_id = truth.command_id
                      AND stronger.venue_order_id = truth.venue_order_id
                      AND (
                          stronger.proof_rank > truth.proof_rank
                          OR (
                              stronger.proof_rank = truth.proof_rank
                              AND stronger.local_sequence > truth.local_sequence
                          )
                      )
             )
        ),
        entry_fill AS (
            SELECT fact.command_id,
                   COUNT(*) AS fill_fact_count,
                   SUM(CAST(fact.filled_size AS REAL)) AS filled_size,
                   SUM(CAST(fact.filled_size AS REAL) * CAST(fact.fill_price AS REAL))
                       / SUM(CAST(fact.filled_size AS REAL)) AS fill_price,
                   MAX(fact.observed_at) AS observed_at,
                   MAX(fact.venue_timestamp) AS venue_timestamp,
                   GROUP_CONCAT(DISTINCT fact.state) AS fill_states,
                   MAX(fact.trade_fact_id) AS trade_fact_id
              FROM canonical_trade_fact fact
             WHERE fact.state IN ('MATCHED', 'MINED', 'CONFIRMED')
               AND CAST(COALESCE(fact.filled_size, '0') AS REAL) > 0
               AND CAST(COALESCE(fact.fill_price, '0') AS REAL) > 0
             GROUP BY fact.command_id
        )
        SELECT cmd.command_id,
               cmd.position_id,
               cmd.decision_id,
               cmd.intent_kind,
               cmd.state AS cmd_state,
               cmd.side,
               cmd.market_id,
               cmd.token_id,
               cmd.size AS cmd_size,
               cmd.price AS cmd_price,
               cmd.created_at AS cmd_created_at,
               latest_order.matched_size AS order_fact_matched_size,
               latest_order.remaining_size AS order_fact_remaining_size,
               latest_order.state AS order_fact_state,
               entry_fill.trade_fact_id,
               NULL AS trade_id,
               entry_fill.fill_states AS trade_state,
               entry_fill.filled_size,
               entry_fill.fill_price,
               'REST' AS source,
               entry_fill.observed_at,
               entry_fill.venue_timestamp,
               ef.command_id AS ef_command_id,
               ef.shares AS ef_shares,
               ef.fill_price AS ef_fill_price,
               ef.terminal_exec_status AS ef_terminal_exec_status
          FROM venue_commands cmd
          JOIN latest_order
            ON latest_order.command_id = cmd.command_id
           AND latest_order.venue_order_id = cmd.venue_order_id
          JOIN entry_fill
            ON entry_fill.command_id = cmd.command_id
          LEFT JOIN execution_fact ef
            ON ef.intent_id = cmd.position_id || ':entry'
           AND ef.order_role = 'entry'
         WHERE cmd.intent_kind = 'ENTRY'
           AND cmd.side = 'BUY'
           AND cmd.state IN ('FILLED', 'PARTIAL', 'EXPIRED')
           AND ABS(CAST(entry_fill.filled_size AS REAL) - CAST(latest_order.matched_size AS REAL)) <= 0.000001
           AND (
               ef.intent_id IS NULL
               OR COALESCE(ef.command_id, '') != cmd.command_id
               OR ABS(COALESCE(CAST(ef.shares AS REAL), 0.0) - CAST(entry_fill.filled_size AS REAL)) > 0.000001
               OR ABS(COALESCE(CAST(ef.fill_price AS REAL), 0.0) - CAST(entry_fill.fill_price AS REAL)) > 0.000001
               OR COALESCE(ef.terminal_exec_status, '') != CASE
                   WHEN cmd.state = 'FILLED'
                    AND CAST(COALESCE(latest_order.remaining_size, '0') AS REAL) = 0
                    AND latest_order.state IN ('MATCHED', 'FILLED')
                   THEN 'filled'
                   ELSE 'partial'
               END
           )
         ORDER BY entry_fill.observed_at, entry_fill.trade_fact_id
        """
    )
    rows = conn.execute(sql).fetchall()
    return [_dict_row(row) for row in rows]


def _log_filled_entry_trade_candidate_execution_fact(
    conn: sqlite3.Connection,
    *,
    candidate: dict,
) -> None:
    from src.state.db import log_execution_fact

    terminal_status = _entry_execution_fact_terminal_status(candidate)
    position_id = str(candidate.get("position_id") or "")
    observed_at = str(candidate.get("observed_at") or _now_iso())
    log_execution_fact(
        conn,
        intent_id=f"{position_id}:entry",
        position_id=position_id,
        decision_id=str(candidate.get("decision_id") or "") or None,
        command_id=str(candidate.get("command_id") or "") or None,
        order_role="entry",
        strategy_key=_position_strategy_key(conn, position_id),
        posted_at=str(candidate.get("cmd_created_at") or "") or None,
        filled_at=observed_at,
        submitted_price=_float_or_none(candidate.get("cmd_price") or candidate.get("price")),
        fill_price=_float_or_none(candidate.get("fill_price")),
        shares=_float_or_none(candidate.get("filled_size")),
        venue_status="FILLED" if terminal_status == "filled" else "PARTIAL",
        terminal_exec_status=terminal_status,
    )


def _entry_execution_fact_terminal_status(candidate: Mapping[str, object]) -> str:
    remaining = _decimal_or_none(candidate.get("order_fact_remaining_size"))
    order_state = str(candidate.get("order_fact_state") or "").upper()
    command_state = str(candidate.get("cmd_state") or candidate.get("state") or "").upper()
    if (
        command_state == CommandState.FILLED.value
        and remaining == 0
        and order_state in {"MATCHED", "FILLED"}
    ):
        return "filled"
    return "partial"


def reconcile_filled_entry_execution_fact_repairs(conn: sqlite3.Connection) -> dict:
    """Repair stale execution_fact rows when filled entry lot truth already exists."""

    summary = {"scanned": 0, "advanced": 0, "stayed": 0, "errors": 0}
    for candidate in _filled_entry_execution_fact_repair_candidates(conn):
        summary["scanned"] += 1
        command_id = str(candidate.get("command_id") or "")
        fact_id = str(candidate.get("trade_fact_id") or "")
        conn.execute("SAVEPOINT filled_entry_execfact_repair")
        try:
            _log_filled_entry_trade_candidate_execution_fact(conn, candidate=candidate)
            verified = conn.execute(
                """
                SELECT terminal_exec_status, venue_status, command_id
                  FROM execution_fact
                 WHERE intent_id = ?
                   AND order_role = 'entry'
                 LIMIT 1
                """,
                (f"{candidate.get('position_id')}:entry",),
            ).fetchone()
            expected_status = _entry_execution_fact_terminal_status(candidate)
            expected_venue_status = "FILLED" if expected_status == "filled" else "PARTIAL"
            if verified is None:
                raise RuntimeError("filled entry execution_fact repair missing post-write row")
            if (
                str(verified["command_id"] or "") != command_id
                or str(verified["terminal_exec_status"] or "") != expected_status
                or str(verified["venue_status"] or "") != expected_venue_status
            ):
                raise RuntimeError(
                    "filled entry execution_fact repair postcondition failed "
                    f"command_id={command_id} persisted_command={verified['command_id']!r} "
                    f"venue_status={verified['venue_status']!r} "
                    f"terminal_exec_status={verified['terminal_exec_status']!r}"
                )
            conn.execute("RELEASE SAVEPOINT filled_entry_execfact_repair")
            summary["advanced"] += 1
        except Exception as exc:
            conn.execute("ROLLBACK TO SAVEPOINT filled_entry_execfact_repair")
            conn.execute("RELEASE SAVEPOINT filled_entry_execfact_repair")
            logger.error(
                "recovery: filled entry execution fact repair failed for command %s trade_fact %s: %s",
                command_id,
                fact_id,
                exc,
            )
            summary["errors"] += 1
    return summary


def reconcile_filled_entry_position_lot_repairs(conn: sqlite3.Connection) -> dict:
    """Repair filled ENTRY commands whose trade facts never materialized lots."""

    summary = {"scanned": 0, "advanced": 0, "stayed": 0, "errors": 0}
    for candidate in _filled_entry_lot_materialization_candidates(conn):
        summary["scanned"] += 1
        command_id = str(candidate.get("command_id") or "")
        fact_id = str(candidate.get("trade_fact_id") or "")
        safe_command_id = "".join(ch if ch.isalnum() else "_" for ch in command_id)
        sp_name = f"sp_filled_entry_lot_{safe_command_id}_{fact_id}"
        conn.execute("SAVEPOINT " + sp_name)
        try:
            advanced = _append_filled_entry_position_lot_repair(conn, candidate=candidate)
            conn.execute(f"RELEASE SAVEPOINT {sp_name}")
            if advanced:
                summary["advanced"] += 1
            else:
                summary["stayed"] += 1
        except Exception as exc:
            conn.execute(f"ROLLBACK TO SAVEPOINT {sp_name}")
            conn.execute(f"RELEASE SAVEPOINT {sp_name}")
            logger.error(
                "recovery: filled entry lot repair failed for command %s trade_fact %s: %s",
                command_id,
                fact_id,
                exc,
            )
            summary["errors"] += 1
    return summary


def _command_fill_coverage_state(command: dict, fill_summary: dict) -> str:
    command_size = _decimal_or_none(command.get("size"))
    filled_size = _decimal_or_none(fill_summary.get("filled_size"))
    if command_size is None or command_size <= 0:
        return "unknown"
    if filled_size is None or filled_size <= 0:
        return "none"
    if filled_size >= command_size:
        return "complete"
    return "partial"


def _exit_pending_projection_candidates(conn: sqlite3.Connection) -> list[dict]:
    if not (
        _table_exists(conn, "venue_commands")
        and _table_exists(conn, "venue_trade_facts")
        and _table_exists(conn, "position_current")
    ):
        return []
    current_cols = _table_columns(conn, "position_current")
    if not current_cols:
        return []
    pc_select = ",\n               ".join(f"pc.{col} AS pc_{col}" for col in current_cols)
    placeholders = ", ".join("?" for _ in _EXIT_PENDING_PROJECTION_COMMAND_STATES)
    trade_placeholders = ", ".join("?" for _ in _EXIT_PENDING_PROJECTION_TRADE_STATES)
    sql = "WITH " + _canonical_trade_fact_cte() + f""",
        exit_fill AS (
            SELECT fact.command_id,
                   COUNT(*) AS fill_fact_count,
                   SUM(CAST(COALESCE(fact.filled_size, '0') AS REAL)) AS filled_size,
                   SUM(CAST(COALESCE(fact.filled_size, '0') AS REAL)
                       * CAST(COALESCE(fact.fill_price, '0') AS REAL)) AS fill_notional,
                   GROUP_CONCAT(DISTINCT fact.state) AS fill_states,
                   MAX(fact.observed_at) AS observed_at
              FROM canonical_trade_fact fact
             WHERE fact.state IN ({trade_placeholders})
               AND CAST(COALESCE(fact.filled_size, '0') AS REAL) > 0
             GROUP BY fact.command_id
        )
        SELECT cmd.command_id AS cmd_command_id,
               cmd.position_id AS cmd_position_id,
               cmd.decision_id AS cmd_decision_id,
               cmd.snapshot_id AS cmd_snapshot_id,
               cmd.venue_order_id AS cmd_venue_order_id,
               cmd.state AS cmd_state,
               cmd.size AS cmd_size,
               cmd.price AS cmd_price,
               cmd.updated_at AS cmd_updated_at,
               exit_fill.fill_fact_count AS fill_fact_count,
               exit_fill.filled_size AS fill_filled_size,
               CASE
                   WHEN exit_fill.filled_size > 0
                   THEN exit_fill.fill_notional / exit_fill.filled_size
                   ELSE NULL
               END AS fill_avg_price,
               exit_fill.fill_states AS fill_states,
               exit_fill.observed_at AS fill_observed_at,
               {pc_select}
          FROM venue_commands cmd
          JOIN exit_fill
            ON exit_fill.command_id = cmd.command_id
          JOIN position_current pc
            ON pc.position_id = cmd.position_id
         WHERE cmd.intent_kind = 'EXIT'
           AND cmd.venue_order_id IS NOT NULL
           AND cmd.venue_order_id != ''
           AND cmd.state IN ({placeholders})
           AND pc.phase IN ('active', 'day0_window', 'pending_exit')
         ORDER BY exit_fill.observed_at, cmd.command_id
        """
    rows = conn.execute(
        sql,
        (
            *sorted(_EXIT_PENDING_PROJECTION_TRADE_STATES),
            *sorted(_EXIT_PENDING_PROJECTION_COMMAND_STATES),
        ),
    ).fetchall()
    return [_dict_row(row) for row in rows]


def _exit_close_target_size(candidate: dict, current: dict) -> Decimal | None:
    sizes = [
        _positive_decimal_or_none(candidate.get("cmd_size")),
        _positive_decimal_or_none(current.get("chain_shares")),
        _positive_decimal_or_none(current.get("shares")),
    ]
    sizes = [size for size in sizes if size is not None]
    if not sizes:
        return None
    return max(sizes)


def _exit_trade_fact_covers_full_close(candidate: dict, current: dict) -> bool:
    filled_size = _positive_decimal_or_none(candidate.get("fill_filled_size"))
    fill_price = _positive_decimal_or_none(candidate.get("fill_avg_price"))
    target_size = _exit_close_target_size(candidate, current)
    return (
        filled_size is not None
        and fill_price is not None
        and target_size is not None
        and filled_size >= target_size
    )


def _append_exit_filled_projection(
    conn: sqlite3.Connection,
    *,
    candidate: dict,
    current: dict,
    occurred_at: str,
) -> None:
    from src.engine.lifecycle_events import build_economic_close_canonical_write, build_position_current_projection
    from src.state.db import append_many_and_project, log_execution_fact
    from src.state.projection import upsert_position_current

    position_id = str(current.get("position_id") or "")
    command_id = str(candidate.get("cmd_command_id") or "")
    venue_order_id = str(candidate.get("cmd_venue_order_id") or "")
    phase_before = str(current.get("phase") or "")
    if not position_id or not command_id or not venue_order_id:
        raise ValueError("exit fill projection requires position, command, and venue order ids")
    if phase_before not in {"active", "day0_window", "pending_exit"}:
        raise ValueError(
            "exit fill projection only repairs active/day0/pending_exit positions; "
            f"got phase={phase_before!r}"
        )
    filled_size = _positive_decimal_or_none(candidate.get("fill_filled_size"))
    fill_price = _positive_decimal_or_none(candidate.get("fill_avg_price"))
    if filled_size is None or fill_price is None:
        raise ValueError("exit fill projection requires positive fill size and price")

    position = SimpleNamespace(
        **{
            **current,
            "trade_id": position_id,
            "state": "economically_closed",
            "exit_state": "sell_filled",
            "pre_exit_state": phase_before,
            "chain_state": current.get("chain_state") or "synced",
            "env": _latest_position_env(conn, position_id),
            "order_id": current.get("order_id") or "",
            "order_status": "sell_filled",
            "last_exit_order_id": venue_order_id,
            "last_exit_at": occurred_at,
            "exit_price": _decimal_text(fill_price),
            "exit_reason": current.get("exit_reason") or "COMMAND_RECOVERY_EXIT_FILL",
            "shares": current.get("shares") or _decimal_text(filled_size),
            "strategy_key": current.get("strategy_key") or current.get("strategy") or "unknown_strategy",
            "unit": current.get("unit") or "F",
        }
    )
    existing = conn.execute(
        """
        SELECT 1
          FROM position_events
         WHERE position_id = ?
           AND event_type = 'EXIT_ORDER_FILLED'
           AND order_id = ?
         LIMIT 1
        """,
        (position_id, venue_order_id),
    ).fetchone()
    if existing is not None:
        projection = build_position_current_projection(position)
        upsert_position_current(conn, projection)
    else:
        sequence_no = _latest_position_sequence(conn, position_id) + 1
        events, projection = build_economic_close_canonical_write(
            position,
            sequence_no=sequence_no,
            phase_before="pending_exit",
            source_module="src.execution.command_recovery",
        )
        for event in events:
            if event.get("event_type") == "EXIT_ORDER_FILLED":
                event["command_id"] = command_id
        append_many_and_project(conn, events, projection)

    log_execution_fact(
        conn,
        intent_id=f"{position_id}:exit",
        position_id=position_id,
        decision_id=str(candidate.get("cmd_decision_id") or "") or None,
        command_id=command_id,
        order_role="exit",
        strategy_key=str(getattr(position, "strategy_key", "") or "") or None,
        posted_at=str(candidate.get("cmd_updated_at") or "") or None,
        filled_at=occurred_at,
        submitted_price=_float_or_none(candidate.get("cmd_price")),
        fill_price=_float_or_none(fill_price),
        shares=_float_or_none(filled_size),
        venue_status="FILLED",
        terminal_exec_status="filled",
    )
    conn.execute(
        """
        UPDATE position_current
           SET order_status = 'sell_filled',
               exit_price = COALESCE(exit_price, ?),
               updated_at = ?
         WHERE position_id = ?
           AND phase = 'economically_closed'
        """,
        (float(fill_price), occurred_at, position_id),
    )


def _append_exit_pending_projection(
    conn: sqlite3.Connection,
    *,
    candidate: dict,
    occurred_at: str,
) -> None:
    from src.state.ledger import append_many_and_project
    from src.state.lifecycle_manager import fold_lifecycle_phase
    from src.state.projection import upsert_position_current

    current_cols = _table_columns(conn, "position_current")
    current = {
        col: candidate.get(f"pc_{col}")
        for col in current_cols
    }
    if _exit_trade_fact_covers_full_close(candidate, current):
        _append_exit_filled_projection(
            conn,
            candidate=candidate,
            current=current,
            occurred_at=occurred_at,
        )
        return
    position_id = str(current.get("position_id") or "")
    command_id = str(candidate.get("cmd_command_id") or "")
    venue_order_id = str(candidate.get("cmd_venue_order_id") or "")
    phase_before = str(current.get("phase") or "")
    if not position_id or not command_id or not venue_order_id:
        raise ValueError("exit pending projection requires position, command, and venue order ids")
    if phase_before not in {"active", "day0_window", "pending_exit"}:
        raise ValueError(
            "exit pending projection only repairs active/day0/pending_exit positions; "
            f"got phase={phase_before!r}"
        )
    phase_after = fold_lifecycle_phase(phase_before, "pending_exit").value
    fill_states = str(candidate.get("fill_states") or "").strip()
    event_id = f"{position_id}:exit_order_posted:{command_id}"
    projection = dict(current)
    projection.update(
        {
            "phase": phase_after,
            "order_id": venue_order_id,
            "order_status": "sell_pending_confirmation",
            "updated_at": occurred_at,
        }
    )
    existing = conn.execute(
        "SELECT 1 FROM position_events WHERE idempotency_key = ? LIMIT 1",
        (event_id,),
    ).fetchone()
    if existing is not None:
        # Append-first recovery: if the event already exists but projection is
        # stale/torn, do not append a duplicate event; fold the projection.
        upsert_position_current(conn, projection)
        return
    event = {
        "event_id": event_id,
        "position_id": position_id,
        "event_version": 1,
        "sequence_no": _latest_position_sequence(conn, position_id) + 1,
        "event_type": "EXIT_ORDER_POSTED",
        "occurred_at": occurred_at,
        "phase_before": phase_before,
        "phase_after": phase_after,
        "strategy_key": current.get("strategy_key"),
        "decision_id": candidate.get("cmd_decision_id"),
        "snapshot_id": current.get("decision_snapshot_id") or candidate.get("cmd_snapshot_id"),
        "order_id": venue_order_id,
        "command_id": command_id,
        "caused_by": f"venue_command:{command_id}",
        "idempotency_key": event_id,
        "venue_status": fill_states or candidate.get("cmd_state"),
        "source_module": "src.execution.command_recovery",
        "env": _latest_position_env(conn, position_id),
        "payload_json": json.dumps(
            {
                "reason": "exit_trade_fact_pending_exit_projection",
                "proof_class": "exit_command_positive_trade_fact",
                "command_id": command_id,
                "venue_order_id": venue_order_id,
                "command_state": candidate.get("cmd_state"),
                "fill_fact_count": candidate.get("fill_fact_count"),
                "filled_size": candidate.get("fill_filled_size"),
                "fill_states": fill_states,
                "economic_close_written": False,
                "semantic_guard": "matched_or_mined_exit_is_pending_not_economic_close",
            },
            sort_keys=True,
            default=str,
        ),
    }
    append_many_and_project(conn, [event], projection)


def reconcile_exit_pending_projections(conn: sqlite3.Connection) -> dict:
    """Repair restart-visible exit side effects into canonical pending_exit.

    Full-size MATCHED/MINED exit trade facts are economic-close proof. Partial
    positive exit facts still project pending_exit so reload cannot attempt a
    second full sell while the remainder is unresolved.
    """

    summary = {"scanned": 0, "advanced": 0, "stayed": 0, "errors": 0}
    for candidate in _exit_pending_projection_candidates(conn):
        summary["scanned"] += 1
        command_id = str(candidate.get("cmd_command_id") or "")
        safe_command_id = "".join(ch if ch.isalnum() else "_" for ch in command_id)
        sp_name = f"sp_exit_pending_{safe_command_id}"
        try:
            occurred_at = str(candidate.get("fill_observed_at") or candidate.get("cmd_updated_at") or _now_iso())
            conn.execute(f"SAVEPOINT {sp_name}")
            try:
                _append_exit_pending_projection(
                    conn,
                    candidate=candidate,
                    occurred_at=occurred_at,
                )
                conn.execute(f"RELEASE SAVEPOINT {sp_name}")
            except Exception:
                conn.execute(f"ROLLBACK TO SAVEPOINT {sp_name}")
                conn.execute(f"RELEASE SAVEPOINT {sp_name}")
                raise
            logger.info(
                "recovery: exit command %s positive trade fact -> pending_exit projection",
                command_id,
            )
            summary["advanced"] += 1
        except Exception as exc:
            logger.error(
                "recovery: exit pending projection failed for command %s: %s",
                command_id,
                exc,
            )
            summary["errors"] += 1
    return summary


def _latest_terminal_remainder_order_fact(
    conn: sqlite3.Connection,
    *,
    command_id: str,
) -> dict | None:
    if not _table_exists(conn, "venue_order_facts"):
        return None
    row = conn.execute(
        "WITH " + _canonical_order_truth_cte() + """
        SELECT fact_id, state, remaining_size, matched_size, source, observed_at
          FROM canonical_order_truth
         WHERE command_id = ?
        """,
        (command_id,),
    ).fetchone()
    data = _dict_row(row)
    if (
        str(data.get("state") or "") in _TERMINAL_NO_FILL_ORDER_FACT_STATES
        and str(data.get("source") or "") in _LIVE_TERMINAL_ORDER_FACT_SOURCES
        and _decimal_is_positive(data.get("matched_size"))
    ):
        return data
    return None


def _latest_terminal_remainder_order_fact_exists(
    conn: sqlite3.Connection,
    *,
    command_id: str,
) -> bool:
    return _latest_terminal_remainder_order_fact(conn, command_id=command_id) is not None


def _latest_position_env(conn: sqlite3.Connection, position_id: str) -> str:
    cols = _table_columns(conn, "position_events")
    if "env" not in cols or "position_id" not in cols:
        return "live"
    row = conn.execute(
        """
        SELECT env
          FROM position_events
         WHERE position_id = ?
         ORDER BY sequence_no DESC, rowid DESC
         LIMIT 1
        """,
        (position_id,),
    ).fetchone()
    value = _dict_row(row).get("env") if row is not None else None
    return str(value or "live")


def _latest_position_sequence(conn: sqlite3.Connection, position_id: str) -> int:
    cols = _table_columns(conn, "position_events")
    if "position_id" not in cols or "sequence_no" not in cols:
        return 0
    row = conn.execute(
        "SELECT COALESCE(MAX(sequence_no), 0) AS max_sequence FROM position_events WHERE position_id = ?",
        (position_id,),
    ).fetchone()
    return int((_dict_row(row).get("max_sequence") if row else 0) or 0)


def _position_current_for_terminal_order(
    conn: sqlite3.Connection,
    *,
    command: dict,
    order_id: str,
) -> dict:
    if not _table_exists(conn, "position_current"):
        raise ValueError("position_current table missing")
    cols = _table_columns(conn, "position_current")
    position_id = str(command.get("position_id") or "")
    has_position_id = position_id and "position_id" in cols
    has_order_id = order_id and "order_id" in cols
    if has_position_id and has_order_id:
        row = conn.execute(
            "SELECT * FROM position_current WHERE position_id = ? AND order_id = ? LIMIT 1",
            (position_id, order_id),
        ).fetchone()
    elif has_position_id:
        row = conn.execute(
            "SELECT * FROM position_current WHERE position_id = ? LIMIT 1",
            (position_id,),
        ).fetchone()
    elif has_order_id:
        row = conn.execute(
            "SELECT * FROM position_current WHERE order_id = ? LIMIT 1",
            (order_id,),
        ).fetchone()
    else:
        raise ValueError("cannot locate position_current without position_id or order_id")
    if row is None:
        raise MissingPositionCurrentForTerminalOrder(
            "terminal order fact has no matching position_current row"
        )
    return _dict_row(row)


_WEATHER_EVENT_SLUG_RE = re.compile(
    r"^(?P<metric>highest|lowest)-temperature-in-(?P<city>.+)-on-"
    r"(?P<month>[a-z]+)-(?P<day>\d{1,2})-(?P<year>\d{4})$"
)
_MONTH_NAME_TO_NUMBER = {
    "january": "01",
    "february": "02",
    "march": "03",
    "april": "04",
    "may": "05",
    "june": "06",
    "july": "07",
    "august": "08",
    "september": "09",
    "october": "10",
    "november": "11",
    "december": "12",
}


def _weather_identity_from_snapshot_slug(command: dict) -> dict:
    slug = str(command.get("snapshot_event_slug") or "").strip().lower()
    match = _WEATHER_EVENT_SLUG_RE.match(slug)
    if not match:
        return {}
    month = _MONTH_NAME_TO_NUMBER.get(match.group("month"))
    if not month:
        return {}
    city = " ".join(part.capitalize() for part in match.group("city").split("-") if part)
    return {
        "city": city,
        "cluster": city,
        "target_date": f"{match.group('year')}-{month}-{int(match.group('day')):02d}",
        "temperature_metric": "high" if match.group("metric") == "highest" else "low",
        "bin_label": slug,
    }


def _strategy_key_for_terminal_no_fill_direction(direction: str) -> str:
    if direction == "buy_no":
        return "opening_inertia"
    if direction == "buy_yes":
        return "center_buy"
    raise ValueError("terminal no-fill void projection requires proven buy_yes/buy_no direction")


def _append_zero_exposure_entry_void_projection(
    conn: sqlite3.Connection,
    *,
    command: dict,
    order_fact: dict,
    occurred_at: str,
) -> None:
    """Append a closed zero-exposure projection for a canceled unprojected entry.

    This is deliberately narrower than live-entry projection repair: it never
    creates an open position and is used only after terminal no-fill venue truth.
    """

    from src.state.ledger import append_many_and_project

    command = _hydrate_command_execution_identity(conn, command)
    position_id = str(command.get("position_id") or "").strip()
    command_id = str(command.get("command_id") or "").strip()
    order_id = str(order_fact.get("order_fact_venue_order_id") or command.get("venue_order_id") or "").strip()
    if not position_id or not command_id or not order_id:
        raise ValueError("zero-exposure void projection requires position, command, and order ids")
    if _latest_position_sequence(conn, position_id) != 0:
        raise ValueError("zero-exposure void projection refuses partial position_events")
    direction = _direction_from_command_tokens(command)
    strategy_key = _strategy_key_for_terminal_no_fill_direction(direction)
    weather_identity = _weather_identity_from_snapshot_slug(command)
    selected_token_id = str(command.get("token_id") or "").strip()
    yes_token_id = str(command.get("env_yes_token_id") or command.get("snapshot_yes_token_id") or "").strip()
    no_token_id = str(command.get("env_no_token_id") or command.get("snapshot_no_token_id") or "").strip()
    condition_id = str(
        command.get("env_condition_id")
        or command.get("snapshot_condition_id")
        or command.get("market_id")
        or ""
    ).strip()
    expected_selected = no_token_id if direction == "buy_no" else yes_token_id
    if not condition_id or not yes_token_id or not no_token_id or not selected_token_id:
        raise ValueError("zero-exposure void projection requires CTF condition/token identity")
    if selected_token_id != expected_selected:
        raise ValueError("zero-exposure void projection selected token does not match direction")

    temperature_metric = str(weather_identity.get("temperature_metric") or "").strip()
    if temperature_metric not in {"high", "low"}:
        raise ValueError("zero-exposure void projection requires weather event metric")
    bin_label = str(
        weather_identity.get("bin_label")
        or command.get("snapshot_event_slug")
        or command.get("snapshot_outcome_label")
        or command.get("env_outcome_label")
        or ""
    )
    projection = {
        "position_id": position_id,
        "phase": "voided",
        "trade_id": position_id,
        "market_id": condition_id,
        "city": weather_identity.get("city") or "",
        "cluster": weather_identity.get("cluster") or weather_identity.get("city") or "",
        "target_date": weather_identity.get("target_date") or "",
        "bin_label": bin_label,
        "direction": direction,
        "unit": None,
        "size_usd": 0.0,
        "shares": 0.0,
        "cost_basis_usd": 0.0,
        "entry_price": 0.0,
        "p_posterior": 0.0,
        "entry_ci_width": 0.0,
        "exit_retry_count": 0,
        "next_exit_retry_at": None,
        "last_monitor_prob": None,
        "last_monitor_prob_is_fresh": None,
        "last_monitor_edge": None,
        "last_monitor_market_price": None,
        "last_monitor_market_price_is_fresh": None,
        "decision_snapshot_id": str(command.get("snapshot_id") or ""),
        "entry_method": "zero_fill_terminal_no_fill",
        "strategy_key": strategy_key,
        "edge_source": strategy_key,
        "discovery_mode": "terminal_no_fill_recovery",
        "chain_state": "local_only",
        "token_id": yes_token_id,
        "no_token_id": no_token_id,
        "condition_id": condition_id,
        "order_id": order_id,
        "order_status": "canceled",
        "updated_at": occurred_at,
        "temperature_metric": temperature_metric,
        "fill_authority": None,
        "recovery_authority": "terminal_no_fill_cancel_ack",
        "chain_shares": 0.0,
        "chain_avg_price": None,
        "chain_cost_basis_usd": None,
        "chain_seen_at": None,
        "chain_absence_at": None,
        "realized_pnl_usd": None,
        "exit_price": None,
        "settlement_price": None,
        "settled_at": None,
        "exit_reason": "ENTRY_TERMINAL_NO_FILL",
    }
    event_id = f"{position_id}:entry_order_voided:{command_id}"
    event = {
        "event_id": event_id,
        "position_id": position_id,
        "event_version": 1,
        "sequence_no": 1,
        "event_type": "ENTRY_ORDER_VOIDED",
        "occurred_at": occurred_at,
        "phase_before": None,
        "phase_after": "voided",
        "strategy_key": strategy_key,
        "decision_id": command.get("decision_id"),
        "snapshot_id": command.get("snapshot_id"),
        "order_id": order_id,
        "command_id": command_id,
        "caused_by": f"venue_order_fact:{order_fact.get('order_fact_id')}",
        "idempotency_key": event_id,
        "venue_status": order_fact.get("order_fact_state"),
        "source_module": "src.execution.command_recovery",
        "env": "live",
        "payload_json": json.dumps(
            {
                "reason": "venue_terminal_no_fill_without_prior_projection",
                "proof_class": "cancel_ack_plus_zero_fill_no_position_projection",
                "command_id": command_id,
                "venue_order_id": order_id,
                "order_fact_id": order_fact.get("order_fact_id"),
                "order_fact_state": order_fact.get("order_fact_state"),
                "remaining_size": order_fact.get("order_fact_remaining_size"),
                "matched_size": order_fact.get("order_fact_matched_size"),
                "source": order_fact.get("order_fact_source"),
                "snapshot_event_slug": command.get("snapshot_event_slug"),
                "semantic_guard": "closed_zero_exposure_projection_only",
            },
            sort_keys=True,
            default=str,
        ),
    }
    append_many_and_project(conn, [event], projection)


def _append_entry_order_voided_projection(
    conn: sqlite3.Connection,
    *,
    command: dict,
    order_fact: dict,
    occurred_at: str,
) -> None:
    from src.state.ledger import append_many_and_project

    order_id = str(order_fact.get("order_fact_venue_order_id") or command.get("venue_order_id") or "")
    try:
        current = _position_current_for_terminal_order(conn, command=command, order_id=order_id)
    except MissingPositionCurrentForTerminalOrder:
        try:
            _append_live_entry_projection_repair(conn, candidate={**command, **order_fact})
            current = _position_current_for_terminal_order(conn, command=command, order_id=order_id)
        except Exception:
            _append_zero_exposure_entry_void_projection(
                conn,
                command=command,
                order_fact=order_fact,
                occurred_at=occurred_at,
            )
            return
    position_id = str(current.get("position_id") or "")
    if not position_id:
        raise ValueError("position_current row missing position_id")
    current_phase = str(current.get("phase") or "")
    if current_phase == "voided":
        return
    if current_phase != "pending_entry":
        raise ValueError(
            "terminal no-fill order fact can only void pending_entry positions; "
            f"got phase={current_phase!r}"
        )
    if not _decimal_is_zero(current.get("shares")) or not _decimal_is_zero(current.get("cost_basis_usd")):
        raise ValueError("terminal no-fill order fact cannot void non-zero-share position")

    next_sequence = _latest_position_sequence(conn, position_id) + 1
    event_id = f"{position_id}:entry_order_voided:{command['command_id']}"
    idempotency_key = event_id
    payload = {
        "reason": "venue_terminal_no_fill",
        "command_id": command["command_id"],
        "venue_order_id": order_id,
        "order_fact_id": order_fact.get("order_fact_id"),
        "order_fact_state": order_fact.get("order_fact_state"),
        "remaining_size": order_fact.get("order_fact_remaining_size"),
        "matched_size": order_fact.get("order_fact_matched_size"),
        "source": order_fact.get("order_fact_source"),
    }
    event = {
        "event_id": event_id,
        "position_id": position_id,
        "event_version": 1,
        "sequence_no": next_sequence,
        "event_type": "ENTRY_ORDER_VOIDED",
        "occurred_at": occurred_at,
        "phase_before": "pending_entry",
        "phase_after": "voided",
        "strategy_key": current.get("strategy_key"),
        "decision_id": command.get("decision_id"),
        "snapshot_id": current.get("decision_snapshot_id") or command.get("snapshot_id"),
        "order_id": order_id,
        "command_id": command["command_id"],
        "caused_by": f"venue_order_fact:{order_fact.get('order_fact_id')}",
        "idempotency_key": idempotency_key,
        "venue_status": order_fact.get("order_fact_state"),
        "source_module": "src.execution.command_recovery",
        "env": _latest_position_env(conn, position_id),
        "payload_json": json.dumps(payload, sort_keys=True, default=str),
    }
    projection = dict(current)
    projection.update(
        {
            "phase": "voided",
            "shares": 0.0,
            "cost_basis_usd": 0.0,
            "entry_price": 0.0,
            "order_id": order_id,
            "order_status": "canceled",
            "updated_at": occurred_at,
        }
    )
    append_many_and_project(conn, [event], projection)


def _terminal_no_fill_continuation_from_row(row: dict) -> dict:
    condition_id = str(
        row.get("env_condition_id")
        or row.get("snapshot_condition_id")
        or row.get("condition_id")
        or ""
    ).strip()
    token_id = str(
        row.get("token_id")
        or row.get("env_selected_outcome_token_id")
        or row.get("snapshot_selected_outcome_token_id")
        or ""
    ).strip()
    return {
        "command_id": str(row.get("command_id") or ""),
        "position_id": str(row.get("position_id") or ""),
        "venue_order_id": str(
            row.get("order_fact_venue_order_id")
            or row.get("venue_order_id")
            or ""
        ),
        "condition_id": condition_id,
        "token_id": token_id,
        "reason": "venue_terminal_no_fill",
    }


def _ensure_entry_projection_is_pending_zero_exposure(
    conn: sqlite3.Connection,
    *,
    command: dict,
    order_id: str,
) -> bool:
    try:
        current = _position_current_for_terminal_order(conn, command=command, order_id=order_id)
    except MissingPositionCurrentForTerminalOrder:
        try:
            _append_live_entry_projection_repair(
                conn,
                candidate={
                    **command,
                    "order_fact_venue_order_id": order_id,
                    "order_fact_observed_at": command.get("updated_at") or _now_iso(),
                },
            )
            current = _position_current_for_terminal_order(
                conn,
                command=command,
                order_id=order_id,
            )
        except Exception as exc:
            logger.info(
                "recovery: command %s terminal no-fill projection repair unavailable: %s",
                command.get("command_id"),
                exc,
            )
            return False
    except ValueError:
        return False
    return (
        str(current.get("phase") or "") == "pending_entry"
        and _decimal_is_zero(current.get("shares"))
        and _decimal_is_zero(current.get("cost_basis_usd"))
    )


def reconcile_terminal_order_facts(
    conn: sqlite3.Connection,
    *,
    collect_continuations: bool = False,
) -> dict:
    """Close ACKED entry commands whose latest venue order fact is terminal no-fill."""

    summary = {"scanned": 0, "advanced": 0, "stayed": 0, "errors": 0}
    continuations: list[dict] = []
    for row in _latest_terminal_order_fact_candidates(conn):
        summary["scanned"] += 1
        command_id = str(row.get("command_id") or "")
        command_order_id = str(row.get("venue_order_id") or "")
        order_id = str(row.get("order_fact_venue_order_id") or "")
        try:
            if not order_id or not command_order_id:
                logger.warning("terminal order fact candidate %s has no venue order id", command_id)
                summary["errors"] += 1
                continue
            if order_id != command_order_id:
                logger.error(
                    "terminal order fact candidate %s order id mismatch: command=%s fact=%s",
                    command_id, command_order_id, order_id,
                )
                summary["errors"] += 1
                continue
            if not _decimal_is_zero(row.get("order_fact_matched_size")):
                logger.info(
                    "terminal order fact candidate %s has matched_size=%s; leaving for fill reconciliation",
                    command_id, row.get("order_fact_matched_size"),
                )
                summary["stayed"] += 1
                continue
            if _fill_trade_fact_count(conn, command_id) > 0:
                logger.info(
                    "terminal order fact candidate %s has fill trade facts; leaving for fill reconciliation",
                    command_id,
                )
                summary["stayed"] += 1
                continue
            occurred_at = str(row.get("order_fact_observed_at") or _now_iso())
            safe_command_id = "".join(ch if ch.isalnum() else "_" for ch in command_id)
            sp_name = f"sp_terminal_order_fact_{safe_command_id}"
            conn.execute(f"SAVEPOINT {sp_name}")
            try:
                resolved_findings = _resolve_m5_local_orphan_findings(
                    conn,
                    venue_order_id=order_id,
                    resolved_at=occurred_at,
                    resolution="command_recovery_terminal_no_fill",
                )
                command_state = str(row.get("state") or "")
                if command_state in _ACKED_ORDER_STATES:
                    append_event(
                        conn,
                        command_id=command_id,
                        event_type=CommandEventType.EXPIRED.value,
                        occurred_at=occurred_at,
                        payload={
                            "reason": "venue_terminal_no_fill",
                            "venue_order_id": order_id,
                            "venue_order_fact_id": row.get("order_fact_id"),
                            "venue_order_fact_state": row.get("order_fact_state"),
                            "matched_size": row.get("order_fact_matched_size"),
                            "remaining_size": row.get("order_fact_remaining_size"),
                            "source": row.get("order_fact_source"),
                            "resolved_m5_local_orphan_findings": resolved_findings,
                        },
                    )
                elif command_state == CommandState.CANCEL_PENDING.value:
                    append_event(
                        conn,
                        command_id=command_id,
                        event_type=CommandEventType.CANCEL_ACKED.value,
                        occurred_at=occurred_at,
                        payload={
                            "reason": "venue_terminal_no_fill",
                            "venue_order_id": order_id,
                            "venue_order_fact_id": row.get("order_fact_id"),
                            "venue_order_fact_state": row.get("order_fact_state"),
                            "matched_size": row.get("order_fact_matched_size"),
                            "remaining_size": row.get("order_fact_remaining_size"),
                            "source": row.get("order_fact_source"),
                            "resolved_m5_local_orphan_findings": resolved_findings,
                        },
                    )
                _append_entry_order_voided_projection(
                    conn,
                    command=row,
                    order_fact=row,
                    occurred_at=occurred_at,
                )
                conn.execute(f"RELEASE SAVEPOINT {sp_name}")
            except Exception:
                conn.execute(f"ROLLBACK TO SAVEPOINT {sp_name}")
                conn.execute(f"RELEASE SAVEPOINT {sp_name}")
                raise
            summary["advanced"] += 1
            if collect_continuations:
                continuations.append(_terminal_no_fill_continuation_from_row(row))
            logger.info(
                "recovery: command %s ACKED terminal order fact %s -> EXPIRED and ENTRY_ORDER_VOIDED",
                command_id,
                row.get("order_fact_state"),
            )
        except Exception as exc:
            logger.error(
                "recovery: terminal order fact reconciliation failed for command %s: %s",
                command_id,
                exc,
            )
            summary["errors"] += 1
    if collect_continuations:
        summary["continuations"] = continuations
    return summary


def reconcile_matched_order_facts(conn: sqlite3.Connection, client) -> dict:
    """Recover ACKED command fill facts when point-order truth says the order matched."""

    summary = {"scanned": 0, "advanced": 0, "stayed": 0, "errors": 0}
    get_order = getattr(client, "get_order", None)
    if not callable(get_order):
        return summary
    for row in _latest_matched_order_fact_candidates(conn):
        summary["scanned"] += 1
        command_id = str(row.get("command_id") or "")
        command_order_id = str(row.get("venue_order_id") or "")
        order_id = str(row.get("order_fact_venue_order_id") or command_order_id)
        try:
            if not order_id or not command_order_id or order_id != command_order_id:
                summary["errors"] += 1
                continue
            if _fill_trade_fact_count(conn, command_id) > 0:
                summary["stayed"] += 1
                continue
            try:
                point_order = _venue_order_payload(get_order(order_id))
            except Exception as exc:
                logger.warning(
                    "recovery: matched order point lookup failed for command %s order %s: %s",
                    command_id,
                    order_id,
                    exc,
                )
                summary["errors"] += 1
                continue
            if point_order is None:
                summary["stayed"] += 1
                continue
            venue_status = str(_first_present(point_order, "status", "state") or "").upper()
            if venue_status not in {"MATCHED", "FILLED", "MINED", "PARTIAL", "PARTIALLY_MATCHED", "PARTIALLY_FILLED"}:
                summary["stayed"] += 1
                continue
            matched_size = _point_order_matched_size(
                point_order,
                fallback=row.get("order_fact_matched_size") or row.get("size") or "0",
                side=row.get("side"),
            )
            if not _positive_decimal_or_none(matched_size):
                summary["stayed"] += 1
                continue
            fill_price = _point_order_fill_price(point_order, fallback=row.get("price"), side=row.get("side"))
            if not _positive_decimal_or_none(fill_price):
                summary["errors"] += 1
                continue
            trade_id = next(iter(_point_order_trade_ids(point_order)), None)
            if not trade_id:
                summary["errors"] += 1
                continue
            tx_hash = next(iter(_point_order_transaction_hashes(point_order)), None)
            event_type = _matched_event_type(row, matched_size, venue_status=venue_status)
            remaining_size = _matched_remaining_size(row, matched_size, venue_status=venue_status)
            order_fact_state = _matched_order_fact_state(
                event_type=event_type,
                venue_status=venue_status,
                remaining_size=remaining_size,
            )
            observed_at = _now_iso()
            payload = {
                "reason": "acked_order_point_order_matched",
                "proof_class": "point_order_matched_fill",
                "venue_order_id": order_id,
                "command_id": command_id,
                "venue_status": venue_status,
                "matched_size": matched_size,
                "remaining_size": remaining_size,
                "fill_price": fill_price,
                "trade_id": trade_id,
                "tx_hash": tx_hash,
                "point_order": point_order,
                "latest_order_fact_id": row.get("order_fact_id"),
                "latest_order_fact_state": row.get("order_fact_state"),
            }
            safe_command_id = "".join(ch if ch.isalnum() else "_" for ch in command_id)
            sp_name = f"sp_matched_order_fact_{safe_command_id}"
            conn.execute(f"SAVEPOINT {sp_name}")
            try:
                append_order_fact(
                    conn,
                    venue_order_id=order_id,
                    command_id=command_id,
                    state=order_fact_state,
                    remaining_size=remaining_size,
                    matched_size=matched_size,
                    source="REST",
                    observed_at=observed_at,
                    venue_timestamp=observed_at,
                    raw_payload_hash=_payload_hash(payload),
                    raw_payload_json=payload,
                )
                append_trade_fact(
                    conn,
                    trade_id=trade_id,
                    venue_order_id=order_id,
                    command_id=command_id,
                    state="MATCHED",
                    filled_size=matched_size,
                    fill_price=fill_price,
                    source="REST",
                    observed_at=observed_at,
                    venue_timestamp=observed_at,
                    tx_hash=tx_hash,
                    raw_payload_hash=_payload_hash({**payload, "fact_type": "trade"}),
                    raw_payload_json=payload,
                )
                append_event(
                    conn,
                    command_id=command_id,
                    event_type=event_type,
                    occurred_at=observed_at,
                    payload=payload,
                )
                if str(row.get("intent_kind") or "").upper() == "ENTRY":
                    _append_matched_order_fill_projection(
                        conn,
                        command=row,
                        venue_order_id=order_id,
                        matched_size=matched_size,
                        fill_price=fill_price,
                        observed_at=observed_at,
                    )
                _append_exit_order_fill_projection(
                    conn,
                    command=row,
                    venue_order_id=order_id,
                    matched_size=matched_size,
                    fill_price=fill_price,
                    observed_at=observed_at,
                    event_type=event_type,
                )
                conn.execute(f"RELEASE SAVEPOINT {sp_name}")
            except Exception:
                conn.execute(f"ROLLBACK TO SAVEPOINT {sp_name}")
                conn.execute(f"RELEASE SAVEPOINT {sp_name}")
                raise
            summary["advanced"] += 1
        except Exception as exc:
            logger.error(
                "recovery: matched order fact reconciliation failed for command %s: %s",
                command_id,
                exc,
            )
            summary["errors"] += 1
    return summary


def reconcile_completed_partial_order_facts(conn: sqlite3.Connection) -> dict:
    """Finalize PARTIAL commands when order truth says the remainder is gone."""

    summary = {"scanned": 0, "advanced": 0, "stayed": 0, "errors": 0}
    for row in _latest_completed_partial_order_fact_candidates(conn):
        summary["scanned"] += 1
        command_id = str(row.get("command_id") or "")
        order_id = str(row.get("order_fact_venue_order_id") or row.get("venue_order_id") or "")
        try:
            intent_kind = str(row.get("intent_kind") or "").upper()
            observed_at = str(row.get("order_fact_observed_at") or _now_iso())
            payload = {
                "reason": (
                    "partial_exit_order_fact_completed"
                    if intent_kind == "EXIT"
                    else "partial_entry_order_fact_completed"
                ),
                "proof_class": "completed_partial_order_fact",
                "venue_order_id": order_id,
                "command_id": command_id,
                "matched_size": str(row.get("order_fact_matched_size") or ""),
                "remaining_size": str(row.get("order_fact_remaining_size") or ""),
                "latest_order_fact_id": row.get("order_fact_id"),
                "latest_order_fact_state": row.get("order_fact_state"),
                "latest_order_fact_source": row.get("order_fact_source"),
                "latest_order_fact_observed_at": row.get("order_fact_observed_at"),
            }
            safe_command_id = "".join(ch if ch.isalnum() else "_" for ch in command_id)
            sp_name = f"sp_completed_partial_order_{safe_command_id}"
            conn.execute(f"SAVEPOINT {sp_name}")
            try:
                append_event(
                    conn,
                    command_id=command_id,
                    event_type=CommandEventType.FILL_CONFIRMED.value,
                    occurred_at=observed_at,
                    payload=payload,
                )
                _append_exit_order_fill_projection(
                    conn,
                    command=row,
                    venue_order_id=order_id,
                    matched_size=str(row.get("order_fact_matched_size") or ""),
                    fill_price=str(row.get("price") or ""),
                    observed_at=observed_at,
                    event_type=CommandEventType.FILL_CONFIRMED.value,
                )
                conn.execute(f"RELEASE SAVEPOINT {sp_name}")
            except Exception:
                conn.execute(f"ROLLBACK TO SAVEPOINT {sp_name}")
                conn.execute(f"RELEASE SAVEPOINT {sp_name}")
                raise
            summary["advanced"] += 1
        except Exception as exc:
            logger.error(
                "recovery: completed partial order fact reconciliation failed for command %s: %s",
                command_id,
                exc,
            )
            summary["errors"] += 1
    return summary


def _exit_lifecycle_alignment_candidates(conn: sqlite3.Connection) -> list[dict]:
    if not (
        _table_exists(conn, "venue_commands")
        and _table_exists(conn, "venue_order_facts")
        and _table_exists(conn, "position_current")
    ):
        return []
    command_placeholders = ", ".join("?" for _ in _EXIT_LIFECYCLE_REPAIR_COMMAND_STATES)
    sql = "WITH " + _canonical_order_truth_cte(cte_name="latest_order") + f"""
        SELECT
            cmd.*,
            pc.phase AS position_phase,
            pc.strategy_key AS position_strategy_key,
            pc.chain_shares AS position_chain_shares,
            pc.shares AS position_shares,
            latest_order.fact_id AS order_fact_id,
            latest_order.state AS order_fact_state,
            latest_order.remaining_size AS order_fact_remaining_size,
            latest_order.matched_size AS order_fact_matched_size,
            latest_order.source AS order_fact_source,
            latest_order.observed_at AS order_fact_observed_at,
            latest_order.raw_payload_json AS order_fact_raw_payload_json
          FROM venue_commands cmd
          JOIN position_current pc
            ON pc.position_id = cmd.position_id
          JOIN latest_order
            ON latest_order.command_id = cmd.command_id
           AND latest_order.venue_order_id = cmd.venue_order_id
         WHERE cmd.intent_kind = 'EXIT'
           AND COALESCE(cmd.venue_order_id, '') != ''
           AND cmd.state IN ({command_placeholders})
           AND (
                pc.phase != 'pending_exit'
                OR UPPER(COALESCE(latest_order.state, '')) IN ('MATCHED', 'FILLED')
           )
         ORDER BY datetime(latest_order.observed_at), cmd.command_id
    """
    return [
        _dict_row(row)
        for row in conn.execute(sql, tuple(sorted(_EXIT_LIFECYCLE_REPAIR_COMMAND_STATES))).fetchall()
    ]


def _order_fact_point_payload(candidate: Mapping[str, object]) -> dict | None:
    raw = _json_mapping(candidate.get("order_fact_raw_payload_json"))
    nested = raw.get("submit_result")
    if isinstance(nested, Mapping):
        payload = _venue_order_payload(nested)
        if payload is not None:
            return payload
    return _venue_order_payload(raw)


def _restore_exit_order_pending_projection(
    conn: sqlite3.Connection,
    *,
    candidate: Mapping[str, object],
    occurred_at: str,
) -> bool:
    position_id = str(candidate.get("position_id") or "").strip()
    command_id = str(candidate.get("command_id") or "").strip()
    venue_order_id = str(candidate.get("venue_order_id") or "").strip()
    phase_before = str(candidate.get("position_phase") or "").strip()
    if not position_id or not command_id or not venue_order_id:
        return False
    if phase_before == "pending_exit":
        return False
    if phase_before not in _EXIT_LIVE_ORDER_RESTORE_PHASES:
        return False
    # A live venue EXIT order is stronger current money-path truth than a stale
    # quarantined projection. This mirrors M5 ghost-sell recovery, which must
    # restore the position to pending_exit so the live order can be reconciled.
    phase_after = "pending_exit"
    event_key = f"{position_id}:exit_order_recovered:{command_id}"
    existing = conn.execute(
        "SELECT 1 FROM position_events WHERE idempotency_key = ? LIMIT 1",
        (event_key,),
    ).fetchone()
    conn.execute(
        """
        UPDATE position_current
           SET phase = ?,
               order_id = ?,
               order_status = 'sell_pending_confirmation',
               exit_reason = COALESCE(exit_reason, 'COMMAND_RECOVERY_RESTING_EXIT_ORDER'),
               updated_at = ?
         WHERE position_id = ?
        """,
        (phase_after, venue_order_id, occurred_at, position_id),
    )
    if existing is not None:
        return True
    seq = _latest_position_sequence(conn, position_id) + 1
    payload = {
        "reason": "resting_exit_order_restored_pending_exit_projection",
        "proof_class": "live_exit_order_fact_with_non_pending_exit_position",
        "command_id": command_id,
        "venue_order_id": venue_order_id,
        "command_state": candidate.get("state"),
        "order_fact_id": candidate.get("order_fact_id"),
        "order_fact_state": candidate.get("order_fact_state"),
        "order_fact_remaining_size": candidate.get("order_fact_remaining_size"),
        "phase_before": phase_before,
        "phase_after": phase_after,
    }
    conn.execute(
        """
        INSERT INTO position_events (
            event_id, position_id, event_version, sequence_no, event_type,
            occurred_at, phase_before, phase_after, strategy_key, decision_id,
            snapshot_id, order_id, command_id, caused_by, idempotency_key,
            venue_status, source_module, payload_json, env
        ) VALUES (?, ?, 1, ?, 'EXIT_ORDER_POSTED', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            event_key,
            position_id,
            seq,
            occurred_at,
            phase_before,
            phase_after,
            str(candidate.get("position_strategy_key") or "opening_inertia"),
            str(candidate.get("decision_id") or ""),
            str(candidate.get("snapshot_id") or ""),
            venue_order_id,
            command_id,
            f"venue_command:{command_id}",
            event_key,
            str(candidate.get("order_fact_state") or candidate.get("state") or ""),
            "src.execution.command_recovery",
            json.dumps(payload, sort_keys=True, default=str),
            _latest_position_env(conn, position_id),
        ),
    )
    return True


def _append_missing_exit_trade_fact_from_order_fact(
    conn: sqlite3.Connection,
    *,
    candidate: Mapping[str, object],
    point_order: dict,
    matched_size: str,
    fill_price: str,
    observed_at: str,
) -> None:
    command_id = str(candidate.get("command_id") or "")
    venue_order_id = str(candidate.get("venue_order_id") or "")
    if _fill_trade_fact_count(conn, command_id) > 0:
        return
    trade_ids = _point_order_trade_ids(point_order)
    tx_hashes = _point_order_transaction_hashes(point_order)
    tx_hash = next(iter(tx_hashes), "")
    trade_id = next(iter(trade_ids), "")
    if not trade_id:
        trade_id = tx_hash or f"order_fact:{candidate.get('order_fact_id') or command_id}"
    payload = {
        "reason": "exit_order_fact_matched_missing_trade_fact_repair",
        "proof_class": "matched_exit_order_fact_with_fill_economics",
        "command_id": command_id,
        "venue_order_id": venue_order_id,
        "order_fact_id": candidate.get("order_fact_id"),
        "order_fact_state": candidate.get("order_fact_state"),
        "matched_size": matched_size,
        "fill_price": fill_price,
        "tx_hash": tx_hash,
        "point_order": point_order,
    }
    append_trade_fact(
        conn,
        trade_id=trade_id,
        venue_order_id=venue_order_id,
        command_id=command_id,
        state="MATCHED",
        filled_size=matched_size,
        fill_price=fill_price,
        source=str(candidate.get("order_fact_source") or "REST"),
        observed_at=observed_at,
        venue_timestamp=observed_at,
        tx_hash=tx_hash or None,
        raw_payload_hash=_payload_hash({**payload, "fact_type": "trade"}),
        raw_payload_json=payload,
    )


def _repair_exit_matched_order_fact_projection(
    conn: sqlite3.Connection,
    *,
    candidate: Mapping[str, object],
    occurred_at: str,
) -> bool:
    command_id = str(candidate.get("command_id") or "")
    venue_order_id = str(candidate.get("venue_order_id") or "")
    point_order = _order_fact_point_payload(candidate)
    matched_size = _point_order_matched_size(
        point_order,
        fallback=candidate.get("order_fact_matched_size"),
        side=candidate.get("side"),
    )
    fill_price = _point_order_fill_price(point_order, fallback=candidate.get("price"), side=candidate.get("side"))
    if _positive_decimal_or_none(matched_size) is None or _positive_decimal_or_none(fill_price) is None:
        return False
    _append_missing_exit_trade_fact_from_order_fact(
        conn,
        candidate=candidate,
        point_order=point_order or {},
        matched_size=matched_size,
        fill_price=fill_price,
        observed_at=occurred_at,
    )
    if str(candidate.get("state") or "") != CommandState.FILLED.value:
        append_event(
            conn,
            command_id=command_id,
            event_type=CommandEventType.FILL_CONFIRMED.value,
            occurred_at=occurred_at,
            payload={
                "reason": "exit_order_fact_matched_projection_repair",
                "proof_class": "matched_exit_order_fact_with_trade_economics",
                "venue_order_id": venue_order_id,
                "command_id": command_id,
                "matched_size": matched_size,
                "fill_price": fill_price,
                "latest_order_fact_id": candidate.get("order_fact_id"),
                "latest_order_fact_state": candidate.get("order_fact_state"),
            },
        )
    updated_command = dict(candidate)
    updated_command["state"] = CommandState.FILLED.value
    _append_exit_order_fill_projection(
        conn,
        command=updated_command,
        venue_order_id=venue_order_id,
        matched_size=matched_size,
        fill_price=fill_price,
        observed_at=occurred_at,
        event_type=CommandEventType.FILL_CONFIRMED.value,
    )
    return True


def reconcile_exit_lifecycle_alignment_repairs(conn: sqlite3.Connection) -> dict:
    """Repair EXIT command/projection disagreements visible at live restart."""

    summary = {"scanned": 0, "advanced": 0, "stayed": 0, "errors": 0}
    for candidate in _exit_lifecycle_alignment_candidates(conn):
        summary["scanned"] += 1
        command_id = str(candidate.get("command_id") or "")
        fact_state = str(candidate.get("order_fact_state") or "").upper()
        safe_command_id = "".join(ch if ch.isalnum() else "_" for ch in command_id)
        sp_name = f"sp_exit_lifecycle_align_{safe_command_id}"
        occurred_at = str(candidate.get("order_fact_observed_at") or candidate.get("updated_at") or _now_iso())
        try:
            conn.execute(f"SAVEPOINT {sp_name}")
            advanced = False
            if fact_state in _EXIT_FILL_ORDER_FACT_STATES:
                advanced = _repair_exit_matched_order_fact_projection(
                    conn,
                    candidate=candidate,
                    occurred_at=occurred_at,
                )
            elif fact_state in _EXIT_LIVE_ORDER_FACT_STATES:
                advanced = _restore_exit_order_pending_projection(
                    conn,
                    candidate=candidate,
                    occurred_at=occurred_at,
                )
            conn.execute(f"RELEASE SAVEPOINT {sp_name}")
            if advanced:
                summary["advanced"] += 1
            else:
                summary["stayed"] += 1
        except Exception as exc:
            conn.execute(f"ROLLBACK TO SAVEPOINT {sp_name}")
            conn.execute(f"RELEASE SAVEPOINT {sp_name}")
            logger.error(
                "recovery: exit lifecycle alignment repair failed for command %s: %s",
                command_id,
                exc,
            )
            summary["errors"] += 1
    return summary


def _matched_cancel_review_required_candidates(conn: sqlite3.Connection) -> list[dict]:
    if not _table_exists(conn, "venue_commands"):
        return []
    rows = conn.execute(
        """
        SELECT *
          FROM venue_commands
         WHERE state = 'REVIEW_REQUIRED'
           AND intent_kind = 'ENTRY'
           AND venue_order_id IS NOT NULL
           AND venue_order_id != ''
         ORDER BY updated_at, command_id
        """
    ).fetchall()
    return [_dict_row(row) for row in rows]


def reconcile_matched_cancel_review_required_entries(conn: sqlite3.Connection) -> dict:
    """Clear REVIEW_REQUIRED entries when matched-cancel facts prove held exposure.

    This handles the live shape where a maker rest partially/near-fully fills,
    cancel-replace receives a venue NOT_CANCELED / matched-order response, and
    the command is left in REVIEW_REQUIRED even though canonical trade facts and
    position_current already show a held, chain-synced position. The pass is
    intentionally DB-only and proof-gated; REVIEW_REQUIRED rows without held
    exposure evidence stay operator-visible.
    """

    summary = {"scanned": 0, "advanced": 0, "stayed": 0, "errors": 0}
    for command in _matched_cancel_review_required_candidates(conn):
        summary["scanned"] += 1
        command_id = str(command.get("command_id") or "")
        venue_order_id = str(command.get("venue_order_id") or "")
        try:
            latest_payload = _latest_review_cancel_blocked_payload(conn, command_id)
            if not _cancel_blocked_by_matched_order(latest_payload):
                summary["stayed"] += 1
                continue
            trade_summary = _positive_fill_trade_fact_summary(conn, command_id)
            if int(trade_summary.get("count") or 0) <= 0:
                summary["stayed"] += 1
                continue
            filled_size = str(trade_summary.get("filled_size") or "0")
            order_fact = _latest_order_fact_for_command_order(
                conn,
                command_id=command_id,
                venue_order_id=venue_order_id,
            )
            if not order_fact:
                summary["stayed"] += 1
                continue
            if not _matched_cancel_residual_is_dust(command, order_fact, filled_size):
                summary["stayed"] += 1
                continue
            if not _active_projection_matches_confirmed_fill(
                conn,
                command=command,
                venue_order_id=venue_order_id,
                filled_size=filled_size,
            ):
                summary["stayed"] += 1
                continue

            observed_at = str(order_fact.get("observed_at") or _now_iso())
            payload = {
                "reason": "review_cleared_confirmed_fill",
                "proof_class": "matched_cancel_with_confirmed_held_projection",
                "command_id": command_id,
                "venue_order_id": venue_order_id,
                "filled_size": filled_size,
                "latest_order_fact_id": order_fact.get("fact_id"),
                "latest_order_fact_state": order_fact.get("state"),
                "latest_order_fact_remaining_size": order_fact.get("remaining_size"),
                "latest_order_fact_matched_size": order_fact.get("matched_size"),
                "latest_cancel_payload": latest_payload,
                "required_predicates": {
                    "latest_event_is_cancel_replace_blocked": True,
                    "cancel_response_not_canceled_because_matched": True,
                    "positive_trade_facts": True,
                    "residual_size_is_dust": True,
                    "active_projection_matches_confirmed_fill": True,
                },
                "source_proof": {
                    "source_commit": "runtime",
                    "source_function": (
                        "command_recovery."
                        "reconcile_matched_cancel_review_required_entries"
                    ),
                    "source_reason": "matched_cancel_review_required_clearance",
                },
            }
            safe_command_id = "".join(ch if ch.isalnum() else "_" for ch in command_id)
            sp_name = f"sp_matched_cancel_review_{safe_command_id}"
            conn.execute(f"SAVEPOINT {sp_name}")
            try:
                append_event(
                    conn,
                    command_id=command_id,
                    event_type=CommandEventType.FILL_CONFIRMED.value,
                    occurred_at=observed_at,
                    payload=payload,
                )
                conn.execute(f"RELEASE SAVEPOINT {sp_name}")
            except Exception:
                conn.execute(f"ROLLBACK TO SAVEPOINT {sp_name}")
                conn.execute(f"RELEASE SAVEPOINT {sp_name}")
                raise
            summary["advanced"] += 1
        except Exception as exc:
            logger.error(
                "recovery: matched-cancel REVIEW_REQUIRED recovery failed for command %s: %s",
                command_id,
                exc,
            )
            summary["errors"] += 1
    return summary


def _spurious_model_divergence_pending_exit_candidates(conn: sqlite3.Connection) -> list[dict]:
    if not (
        _table_exists(conn, "position_current")
        and _table_exists(conn, "position_events")
        and _table_exists(conn, "venue_commands")
    ):
        return []
    rows = conn.execute(
        """
        WITH latest_event AS (
            SELECT pe.*
              FROM position_events pe
             WHERE pe.sequence_no = (
                   SELECT MAX(newer.sequence_no)
                     FROM position_events newer
                    WHERE newer.position_id = pe.position_id
             )
        )
        SELECT pc.*,
               latest_event.event_id AS latest_event_id,
               latest_event.event_type AS latest_event_type,
               latest_event.sequence_no AS latest_sequence_no,
               latest_event.phase_before AS latest_phase_before,
               latest_event.payload_json AS latest_payload_json
          FROM position_current pc
          JOIN latest_event
            ON latest_event.position_id = pc.position_id
         WHERE pc.phase = 'pending_exit'
           AND pc.exit_reason LIKE 'MODEL_DIVERGENCE_PANIC%'
           AND latest_event.event_type IN ('EXIT_INTENT', 'EXIT_ORDER_REJECTED')
           AND latest_event.payload_json LIKE '%MODEL_DIVERGENCE_PANIC%'
           AND NOT EXISTS (
               SELECT 1
                 FROM venue_commands vc
                WHERE vc.position_id = pc.position_id
                  AND UPPER(COALESCE(vc.intent_kind, '')) = 'EXIT'
           )
         ORDER BY pc.updated_at
        """
    ).fetchall()
    return [_dict_row(row) for row in rows]


def repair_spurious_model_divergence_pending_exits(conn: sqlite3.Connection) -> dict:
    """Release pending_exit rows caused by the forbidden buy-NO zero-probability bug.

    This is deliberately narrow: it does not touch any position with an EXIT
    command, any non-MODEL_DIVERGENCE reason, or any pending_exit whose latest
    event does not itself carry the panic reason.
    """

    summary = {"scanned": 0, "advanced": 0, "stayed": 0, "errors": 0}
    for row in _spurious_model_divergence_pending_exit_candidates(conn):
        summary["scanned"] += 1
        position_id = str(row.get("position_id") or "")
        phase_before = str(row.get("phase") or "pending_exit")
        phase_after = str(row.get("latest_phase_before") or "active")
        if phase_after not in {"active", "day0_window"}:
            phase_after = "active"
        now = _now_iso()
        safe_position_id = "".join(ch if ch.isalnum() else "_" for ch in position_id)
        sp_name = f"sp_spurious_model_divergence_release_{safe_position_id}"
        payload = {
            "schema_version": 1,
            "reason": "spurious_model_divergence_pending_exit_released",
            "proof_class": "no_exit_command_model_divergence_panic_from_missing_buy_no_authority",
            "position_id": position_id,
            "phase_before": phase_before,
            "phase_after": phase_after,
            "exit_reason": str(row.get("exit_reason") or ""),
            "latest_event_id": str(row.get("latest_event_id") or ""),
            "latest_event_type": str(row.get("latest_event_type") or ""),
            "required_predicates": {
                "position_phase_pending_exit": True,
                "exit_reason_model_divergence_panic": True,
                "latest_event_carries_model_divergence_panic": True,
                "no_exit_command_for_position": True,
            },
            "source_proof": {
                "source_function": "command_recovery.repair_spurious_model_divergence_pending_exits",
                "source_reason": "buy_no_monitor_probability_zero_bug",
            },
        }
        try:
            conn.execute(f"SAVEPOINT {sp_name}")
            next_sequence = int(row.get("latest_sequence_no") or 0) + 1
            conn.execute(
                """
                INSERT INTO position_events (
                    event_id, position_id, event_version, sequence_no,
                    event_type, occurred_at, phase_before, phase_after,
                    strategy_key, decision_id, snapshot_id, order_id,
                    command_id, caused_by, idempotency_key, venue_status,
                    source_module, payload_json, env
                ) VALUES (?, ?, 1, ?, 'MANUAL_OVERRIDE_APPLIED', ?, ?, ?, ?, ?, ?, NULL,
                          NULL, ?, ?, 'spurious_panic_released', ?, ?, 'live')
                """,
                (
                    f"{position_id}:spurious_model_divergence_release:{next_sequence}",
                    position_id,
                    next_sequence,
                    now,
                    phase_before,
                    phase_after,
                    str(row.get("strategy_key") or "unknown"),
                    str(row.get("decision_snapshot_id") or ""),
                    str(row.get("decision_snapshot_id") or ""),
                    str(row.get("latest_event_id") or ""),
                    f"{position_id}:spurious_model_divergence_release:{next_sequence}",
                    "src.execution.command_recovery",
                    json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str),
                ),
            )
            conn.execute(
                """
                UPDATE position_current
                   SET phase = ?,
                       exit_reason = NULL,
                       last_monitor_prob = NULL,
                       last_monitor_edge = NULL,
                       updated_at = ?
                 WHERE position_id = ?
                   AND phase = 'pending_exit'
                   AND exit_reason LIKE 'MODEL_DIVERGENCE_PANIC%'
                   AND NOT EXISTS (
                       SELECT 1
                         FROM venue_commands vc
                        WHERE vc.position_id = position_current.position_id
                          AND UPPER(COALESCE(vc.intent_kind, '')) = 'EXIT'
                   )
                """,
                (phase_after, now, position_id),
            )
            if conn.total_changes <= 0:
                raise RuntimeError("position_current update did not affect a row")
            conn.execute(f"RELEASE SAVEPOINT {sp_name}")
            summary["advanced"] += 1
        except Exception as exc:
            conn.execute(f"ROLLBACK TO SAVEPOINT {sp_name}")
            conn.execute(f"RELEASE SAVEPOINT {sp_name}")
            logger.error(
                "recovery: spurious model-divergence pending_exit repair failed for %s: %s",
                position_id,
                exc,
            )
            summary["errors"] += 1
    return summary


def _structural_win_pending_exit_candidates(conn: sqlite3.Connection) -> list[dict]:
    if not (
        _table_exists(conn, "position_current")
        and _table_exists(conn, "position_events")
        and _table_exists(conn, "venue_commands")
    ):
        return []
    rows = conn.execute(
        """
        WITH latest_bad_exit AS (
            SELECT pe.*
              FROM position_events pe
             WHERE pe.event_type = 'EXIT_INTENT'
               AND pe.phase_after = 'pending_exit'
               AND pe.payload_json LIKE '%CI_SEPARATED_REVERSAL%'
               AND pe.sequence_no = (
                   SELECT MAX(newer.sequence_no)
                     FROM position_events newer
                    WHERE newer.position_id = pe.position_id
                      AND newer.event_type = 'EXIT_INTENT'
                      AND newer.phase_after = 'pending_exit'
                      AND newer.payload_json LIKE '%CI_SEPARATED_REVERSAL%'
               )
        )
        SELECT pc.*,
               latest_bad_exit.event_id AS latest_exit_event_id,
               latest_bad_exit.sequence_no AS latest_exit_sequence_no,
               latest_bad_exit.phase_before AS latest_exit_phase_before,
               latest_bad_exit.payload_json AS latest_exit_payload_json,
               (
                   SELECT MAX(any_event.sequence_no)
                     FROM position_events any_event
                    WHERE any_event.position_id = pc.position_id
               ) AS latest_any_sequence_no
          FROM position_current pc
          JOIN latest_bad_exit
            ON latest_bad_exit.position_id = pc.position_id
         WHERE pc.phase = 'pending_exit'
           AND COALESCE(pc.chain_state, '') = 'synced'
           AND COALESCE(pc.shares, 0) > 0
           AND NOT EXISTS (
               SELECT 1
                 FROM venue_commands vc
                WHERE vc.position_id = pc.position_id
                  AND UPPER(COALESCE(vc.intent_kind, '')) = 'EXIT'
                  AND COALESCE(vc.venue_order_id, '') <> ''
           )
         ORDER BY pc.updated_at
        """
    ).fetchall()
    return [_dict_row(row) for row in rows]


def repair_structural_win_pending_exits(conn: sqlite3.Connection) -> dict:
    """Release false pending_exit rows when live hard facts prove held-side win.

    This is not a generic undo. It requires all of:
      * pending_exit came from a CI_SEPARATED_REVERSAL EXIT_INTENT,
      * chain projection still says the held shares are synced,
      * no EXIT command has a venue order id,
      * current hard-fact evaluation returns HOLD_STRUCTURAL_WIN.
    """

    summary = {"scanned": 0, "advanced": 0, "stayed": 0, "errors": 0}
    candidates = _structural_win_pending_exit_candidates(conn)
    if not candidates:
        return summary
    try:
        from src.config import runtime_cities_by_name
        from src.execution.day0_hard_fact_exit import evaluate_hard_fact_exit
    except Exception as exc:  # noqa: BLE001
        logger.warning("recovery: structural-win pending_exit repair unavailable: %s", exc)
        summary["errors"] += len(candidates)
        return summary

    cities = runtime_cities_by_name()
    now_dt = datetime.now(timezone.utc)
    for row in candidates:
        summary["scanned"] += 1
        position_id = str(row.get("position_id") or "")
        city_name = str(row.get("city") or "")
        city = cities.get(city_name)
        if city is None:
            summary["stayed"] += 1
            continue
        pos = SimpleNamespace(**row)
        setattr(pos, "trade_id", position_id)
        try:
            verdict = evaluate_hard_fact_exit(
                position=pos,
                city=city,
                now=now_dt,
                world_conn=conn,
                durable_only=True,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "recovery: structural-win pending_exit hard-fact proof failed for %s: %s",
                position_id,
                exc,
            )
            summary["errors"] += 1
            continue
        if verdict is None or verdict.action != "HOLD_STRUCTURAL_WIN":
            summary["stayed"] += 1
            continue

        phase_before = str(row.get("phase") or "pending_exit")
        phase_after = str(row.get("latest_exit_phase_before") or "day0_window")
        if phase_after not in {"active", "day0_window"}:
            phase_after = "day0_window"
        now = _now_iso()
        safe_position_id = "".join(ch if ch.isalnum() else "_" for ch in position_id)
        sp_name = f"sp_structural_win_pending_exit_release_{safe_position_id}"
        next_sequence = int(row.get("latest_any_sequence_no") or row.get("latest_exit_sequence_no") or 0) + 1
        payload = {
            "schema_version": 1,
            "reason": "structural_win_pending_exit_released",
            "proof_class": "day0_hard_fact_structural_win_no_exit_venue_order",
            "position_id": position_id,
            "phase_before": phase_before,
            "phase_after": phase_after,
            "latest_exit_event_id": str(row.get("latest_exit_event_id") or ""),
            "latest_exit_payload_json": str(row.get("latest_exit_payload_json") or ""),
            "hard_fact": {
                "action": verdict.action,
                "reason": verdict.reason,
                "metric": verdict.metric,
                "rounded_extreme": verdict.rounded_extreme,
                "source": verdict.source,
            },
            "required_predicates": {
                "position_phase_pending_exit": True,
                "chain_state_synced": True,
                "shares_positive": True,
                "latest_exit_intent_ci_separated_reversal": True,
                "no_exit_command_with_venue_order_id": True,
                "hard_fact_hold_structural_win": True,
            },
            "source_proof": {
                "source_function": "command_recovery.repair_structural_win_pending_exits",
                "source_reason": "day0_absorbing_hard_fact_dominates_estimator_reversal",
            },
        }
        try:
            conn.execute(f"SAVEPOINT {sp_name}")
            conn.execute(
                """
                INSERT INTO position_events (
                    event_id, position_id, event_version, sequence_no,
                    event_type, occurred_at, phase_before, phase_after,
                    strategy_key, decision_id, snapshot_id, order_id,
                    command_id, caused_by, idempotency_key, venue_status,
                    source_module, payload_json, env
                ) VALUES (?, ?, 1, ?, 'MANUAL_OVERRIDE_APPLIED', ?, ?, ?, ?, ?, ?, NULL,
                          NULL, ?, ?, 'structural_win_pending_exit_released', ?, ?, 'live')
                """,
                (
                    f"{position_id}:structural_win_pending_exit_release:{next_sequence}",
                    position_id,
                    next_sequence,
                    now,
                    phase_before,
                    phase_after,
                    str(row.get("strategy_key") or "unknown"),
                    str(row.get("decision_snapshot_id") or ""),
                    str(row.get("decision_snapshot_id") or ""),
                    str(row.get("latest_exit_event_id") or ""),
                    f"{position_id}:structural_win_pending_exit_release:{next_sequence}",
                    "src.execution.command_recovery",
                    json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str),
                ),
            )
            cursor = conn.execute(
                """
                UPDATE position_current
                   SET phase = ?,
                       exit_reason = NULL,
                       last_monitor_prob = 1.0,
                       last_monitor_edge = NULL,
                       updated_at = ?
                 WHERE position_id = ?
                   AND phase = 'pending_exit'
                   AND COALESCE(chain_state, '') = 'synced'
                   AND NOT EXISTS (
                       SELECT 1
                         FROM venue_commands vc
                        WHERE vc.position_id = position_current.position_id
                          AND UPPER(COALESCE(vc.intent_kind, '')) = 'EXIT'
                          AND COALESCE(vc.venue_order_id, '') <> ''
                   )
                """,
                (phase_after, now, position_id),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("position_current update did not affect exactly one row")
            conn.execute(f"RELEASE SAVEPOINT {sp_name}")
            summary["advanced"] += 1
        except Exception as exc:
            conn.execute(f"ROLLBACK TO SAVEPOINT {sp_name}")
            conn.execute(f"RELEASE SAVEPOINT {sp_name}")
            logger.error(
                "recovery: structural-win pending_exit repair failed for %s: %s",
                position_id,
                exc,
            )
            summary["errors"] += 1
    return summary


def _confirmed_phantom_void_candidates(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        """
        WITH latest_void AS (
            SELECT pe.*
              FROM position_events pe
             WHERE pe.event_type = 'ADMIN_VOIDED'
               AND pe.payload_json LIKE '%PHANTOM_NOT_ON_CHAIN%'
               AND pe.sequence_no = (
                   SELECT MAX(newer.sequence_no)
                     FROM position_events newer
                    WHERE newer.position_id = pe.position_id
                      AND newer.event_type = 'ADMIN_VOIDED'
                      AND newer.payload_json LIKE '%PHANTOM_NOT_ON_CHAIN%'
               )
        )
        SELECT pc.*,
               latest_void.event_id AS latest_void_event_id,
               latest_void.sequence_no AS latest_void_sequence_no,
               latest_void.payload_json AS latest_void_payload_json,
               (
                   SELECT MAX(any_event.sequence_no)
                     FROM position_events any_event
                    WHERE any_event.position_id = pc.position_id
               ) AS latest_any_sequence_no
          FROM position_current pc
          JOIN latest_void
            ON latest_void.position_id = pc.position_id
         WHERE pc.phase = 'voided'
           AND COALESCE(pc.exit_reason, '') = 'PHANTOM_NOT_ON_CHAIN'
           AND COALESCE(pc.shares, 0) > 0
           AND COALESCE(pc.fill_authority, '') <> ''
         ORDER BY pc.updated_at
        """
    ).fetchall()
    return [_dict_row(row) for row in rows]


def repair_confirmed_phantom_voids(conn: sqlite3.Connection) -> dict:
    """Recover confirmed fills that chain reconciliation wrongly voided as phantom.

    A venue-confirmed fill is real economic history. If its held token later
    disappears from chain without an attributed exit/settlement/redeem record,
    the correct state is REVIEW_REQUIRED/quarantined, not ADMIN_VOIDED.
    """

    summary = {"scanned": 0, "advanced": 0, "stayed": 0, "errors": 0}
    candidates = _confirmed_phantom_void_candidates(conn)
    if not candidates:
        return summary
    try:
        from src.state.chain_reconciliation import (
            CONFIRMED_CHAIN_ABSENCE_CHAIN_STATE,
            CONFIRMED_CHAIN_ABSENCE_REVIEW_REASON,
        )
        from src.state.portfolio import has_verified_trade_fill
    except Exception as exc:  # noqa: BLE001
        logger.warning("recovery: confirmed phantom-void repair unavailable: %s", exc)
        summary["errors"] += len(candidates)
        return summary

    for row in candidates:
        summary["scanned"] += 1
        if not has_verified_trade_fill(row):
            summary["stayed"] += 1
            continue
        position_id = str(row.get("position_id") or "")
        if not position_id:
            summary["stayed"] += 1
            continue
        direction = str(row.get("direction") or "")
        held_token_id = (
            str(row.get("no_token_id") or "")
            if direction == "buy_no"
            else str(row.get("token_id") or "")
        )
        now = _now_iso()
        next_sequence = int(row.get("latest_any_sequence_no") or row.get("latest_void_sequence_no") or 0) + 1
        safe_position_id = "".join(ch if ch.isalnum() else "_" for ch in position_id)
        sp_name = f"sp_confirmed_phantom_void_repair_{safe_position_id}"
        payload = {
            "schema_version": 1,
            "reason": CONFIRMED_CHAIN_ABSENCE_REVIEW_REASON,
            "proof_class": "confirmed_fill_phantom_void_reclassified_to_review",
            "position_id": position_id,
            "phase_before": "voided",
            "phase_after": "quarantined",
            "latest_void_event_id": str(row.get("latest_void_event_id") or ""),
            "latest_void_payload_json": str(row.get("latest_void_payload_json") or ""),
            "held_token_id": held_token_id,
            "token_id": str(row.get("token_id") or ""),
            "no_token_id": str(row.get("no_token_id") or ""),
            "fill_authority": str(row.get("fill_authority") or ""),
            "shares": row.get("shares"),
            "chain_shares": row.get("chain_shares"),
            "source_proof": {
                "source_function": "command_recovery.repair_confirmed_phantom_voids",
                "source_reason": "venue_confirmed_fill_cannot_be_phantom_without_close_attribution",
            },
        }
        try:
            conn.execute(f"SAVEPOINT {sp_name}")
            conn.execute(
                """
                INSERT INTO position_events (
                    event_id, position_id, event_version, sequence_no,
                    event_type, occurred_at, phase_before, phase_after,
                    strategy_key, decision_id, snapshot_id, order_id,
                    command_id, caused_by, idempotency_key, venue_status,
                    source_module, payload_json, env
                ) VALUES (?, ?, 1, ?, 'REVIEW_REQUIRED', ?, 'voided', 'quarantined',
                          ?, NULL, ?, ?, NULL, ?, ?, 'review_required', ?, ?, 'live')
                """,
                (
                    f"{position_id}:confirmed_phantom_void_repair:{next_sequence}",
                    position_id,
                    next_sequence,
                    now,
                    str(row.get("strategy_key") or "unknown"),
                    str(row.get("decision_snapshot_id") or ""),
                    str(row.get("order_id") or ""),
                    CONFIRMED_CHAIN_ABSENCE_REVIEW_REASON,
                    f"{position_id}:confirmed_phantom_void_repair:{next_sequence}",
                    "src.execution.command_recovery",
                    json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str),
                ),
            )
            cursor = conn.execute(
                """
                UPDATE position_current
                   SET phase = 'quarantined',
                       chain_state = ?,
                       exit_reason = ?,
                       chain_absence_at = CASE
                           WHEN COALESCE(chain_absence_at, '') = '' THEN ?
                           ELSE chain_absence_at
                       END,
                       updated_at = ?
                 WHERE position_id = ?
                   AND phase = 'voided'
                   AND COALESCE(exit_reason, '') = 'PHANTOM_NOT_ON_CHAIN'
                """,
                (
                    CONFIRMED_CHAIN_ABSENCE_CHAIN_STATE,
                    CONFIRMED_CHAIN_ABSENCE_REVIEW_REASON,
                    now,
                    now,
                    position_id,
                ),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("position_current update did not affect exactly one row")
            conn.execute(f"RELEASE SAVEPOINT {sp_name}")
            summary["advanced"] += 1
        except Exception as exc:  # noqa: BLE001
            conn.execute(f"ROLLBACK TO SAVEPOINT {sp_name}")
            conn.execute(f"RELEASE SAVEPOINT {sp_name}")
            logger.error(
                "recovery: confirmed phantom-void repair failed for %s: %s",
                position_id,
                exc,
            )
            summary["errors"] += 1
    return summary


def _partial_remainder_candidates(
    conn: sqlite3.Connection,
    *,
    updated_before: str | None = None,
) -> list[dict]:
    state_placeholders = ",".join("?" for _ in _PARTIAL_REMAINDER_STATES)
    sql = f"""
        SELECT *
          FROM venue_commands
         WHERE intent_kind = 'ENTRY'
           AND state IN ({state_placeholders})
           AND COALESCE(venue_order_id, '') != ''
           AND (? IS NULL OR updated_at < ?)
         ORDER BY updated_at, command_id
        """
    rows = conn.execute(
        sql,
        (*tuple(_PARTIAL_REMAINDER_STATES), updated_before, updated_before),
    ).fetchall()
    return [_dict_row(row) for row in rows]


def _open_order_id(order: object) -> str | None:
    raw = order if isinstance(order, dict) else getattr(order, "raw", None)
    raw_dict = raw if isinstance(raw, dict) else {}
    fallback = getattr(order, "order_id", None)
    return _extract_order_id(raw_dict, fallback=fallback)


def _client_open_order_ids(client) -> set[str]:
    get_open_orders = getattr(client, "get_open_orders", None)
    if not callable(get_open_orders):
        raise RuntimeError("client lacks get_open_orders; partial remainder absence is unknown")
    return {
        order_id
        for order_id in (_open_order_id(order) for order in (get_open_orders() or []))
        if order_id
    }


def _client_trades(client) -> list:
    get_trades = getattr(client, "get_trades", None)
    if callable(get_trades):
        return list(get_trades() or [])
    ensure_adapter = getattr(client, "_ensure_v2_adapter", None)
    if callable(ensure_adapter):
        adapter = ensure_adapter()
        adapter_get_trades = getattr(adapter, "get_trades", None)
        if callable(adapter_get_trades):
            return list(adapter_get_trades() or [])
    raise RuntimeError("client lacks get_trades; terminal no-fill proof is unknown")


def _client_open_orders(client) -> list:
    get_open_orders = getattr(client, "get_open_orders", None)
    if not callable(get_open_orders):
        raise RuntimeError("client lacks get_open_orders; terminal no-fill proof is unknown")
    return list(get_open_orders() or [])


def _matching_open_orders_for_command(
    client,
    command: dict,
    *,
    open_orders: list | None = None,
) -> list[dict]:
    venue_order_id = str(command.get("venue_order_id") or "")
    matches: list[dict] = []
    for order in (_client_open_orders(client) if open_orders is None else open_orders):
        raw = _raw_payload(order)
        order_id = _open_order_id(order) or _extract_order_id(raw)
        if (venue_order_id and order_id == venue_order_id) or _raw_matches_command_exposure(raw, command):
            matches.append(_summarize_venue_match(raw))
    return matches


def _matching_trades_for_command(
    client,
    command: dict,
    *,
    trades: list | None = None,
) -> list[dict]:
    created_epoch = _epoch_seconds(command.get("created_at")) or 0.0
    matches: list[dict] = []
    for trade in (_client_trades(client) if trades is None else trades):
        raw = _raw_payload(trade)
        if not _raw_matches_command_submit_trade_identity(raw, command):
            continue
        trade_epoch = _epoch_seconds(raw.get("match_time") or raw.get("last_update"))
        if trade_epoch is not None and trade_epoch < created_epoch:
            continue
        matches.append(_summarize_venue_match(raw))
    return matches


def _resolve_m5_local_orphan_findings(
    conn: sqlite3.Connection,
    *,
    venue_order_id: str,
    resolved_at: str,
    resolution: str,
) -> int:
    if not _table_exists(conn, "exchange_reconcile_findings"):
        return 0
    rows = conn.execute(
        """
        SELECT finding_id
          FROM exchange_reconcile_findings
         WHERE kind = 'local_orphan_order'
           AND subject_id = ?
           AND resolved_at IS NULL
         ORDER BY recorded_at, finding_id
        """,
        (venue_order_id,),
    ).fetchall()
    if not rows:
        return 0

    resolved = 0
    for row in rows:
        conn.execute(
            """
            UPDATE exchange_reconcile_findings
               SET resolved_at = ?, resolution = ?, resolved_by = ?
             WHERE finding_id = ?
               AND resolved_at IS NULL
            """,
            (
                resolved_at,
                resolution,
                "src.execution.command_recovery",
                str(_dict_row(row)["finding_id"]),
            ),
        )
        resolved += 1
    return resolved


def _resolve_m5_exchange_ghost_findings(
    conn: sqlite3.Connection,
    *,
    venue_order_id: str,
    resolved_at: str,
    resolution: str,
) -> int:
    if not _table_exists(conn, "exchange_reconcile_findings"):
        return 0
    rows = conn.execute(
        """
        SELECT finding_id
          FROM exchange_reconcile_findings
         WHERE kind = 'exchange_ghost_order'
           AND subject_id = ?
           AND resolved_at IS NULL
         ORDER BY recorded_at, finding_id
        """,
        (venue_order_id,),
    ).fetchall()
    if not rows:
        return 0

    resolved = 0
    for row in rows:
        conn.execute(
            """
            UPDATE exchange_reconcile_findings
               SET resolved_at = ?, resolution = ?, resolved_by = ?
             WHERE finding_id = ?
               AND resolved_at IS NULL
            """,
            (
                resolved_at,
                resolution,
                "src.execution.command_recovery",
                str(_dict_row(row)["finding_id"]),
            ),
        )
        resolved += 1
    return resolved


def _payload_hash(payload: dict) -> str:
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _append_local_orphan_terminal_order_fact(
    conn: sqlite3.Connection,
    *,
    command: dict,
    observed_at: str,
    venue_status: str,
    venue_resp: dict | None,
) -> int:
    command_id = str(command.get("command_id") or "")
    venue_order_id = str(command.get("venue_order_id") or "")
    fact_state = _terminal_fact_state_for_venue_status(
        venue_status,
        venue_resp_present=venue_resp is not None,
    )
    if fact_state is None:
        raise ValueError(f"venue status is not terminal no-fill: {venue_status!r}")
    payload = {
        "reason": "m5_local_orphan_order_terminal_no_fill",
        "proof_class": "local_orphan_open_order_absence_plus_zero_fill",
        "finding_id": command.get("finding_id"),
        "venue_order_id": venue_order_id,
        "command_id": command_id,
        "venue_status": str(venue_status or "NOT_FOUND"),
        "venue_response": venue_resp,
        "latest_order_fact_id": command.get("order_fact_id"),
        "latest_order_fact_state": command.get("order_fact_state"),
        "latest_order_fact_matched_size": command.get("order_fact_matched_size"),
        "trade_enumeration_available": True,
    }
    return append_order_fact(
        conn,
        venue_order_id=venue_order_id,
        command_id=command_id,
        state=fact_state,
        remaining_size="0",
        matched_size="0",
        source="REST",
        observed_at=observed_at,
        venue_timestamp=observed_at,
        raw_payload_hash=_payload_hash(payload),
        raw_payload_json=payload,
    )


def _append_point_order_terminal_no_fill_fact(
    conn: sqlite3.Connection,
    *,
    command: dict,
    observed_at: str,
    venue_status: str,
    point_order: dict | None,
    matching_open_orders: list[dict],
    matching_trades: list[dict],
    source_reason: str,
    venue_resp_present_for_terminal_state: bool | None = None,
) -> tuple[int, dict]:
    command_id = str(command.get("command_id") or "")
    venue_order_id = str(command.get("venue_order_id") or "")
    venue_resp_present = (
        point_order is not None
        if venue_resp_present_for_terminal_state is None
        else venue_resp_present_for_terminal_state
    )
    fact_state = _terminal_fact_state_for_venue_status(
        venue_status,
        venue_resp_present=venue_resp_present,
    )
    if fact_state is None:
        raise ValueError(f"venue status is not terminal no-fill: {venue_status!r}")
    required_predicates = {
        "point_order_terminal_no_fill": True,
        "point_order_matched_size_zero": True,
        "no_local_trade_facts": _trade_fact_count(conn, command_id) == 0,
        "no_matching_open_orders": len(matching_open_orders) == 0,
        "no_matching_trades": len(matching_trades) == 0,
    }
    if source_reason == "cancel_unknown_point_order_no_live_record_terminal_no_fill":
        required_predicates["point_order_no_live_record"] = True
    payload = {
        "reason": "point_order_terminal_no_fill",
        "proof_class": "point_order_terminal_no_fill_plus_open_trade_absence",
        "source_reason": source_reason,
        "venue_order_id": venue_order_id,
        "command_id": command_id,
        "venue_status": str(venue_status or "NOT_FOUND"),
        "point_order": point_order,
        "remaining_size": "0",
        "matched_size": "0",
        "required_predicates": required_predicates,
        "matching_open_orders": matching_open_orders[:10],
        "matching_trades": matching_trades[:10],
    }
    fact_id = append_order_fact(
        conn,
        venue_order_id=venue_order_id,
        command_id=command_id,
        state=fact_state,
        remaining_size="0",
        matched_size="0",
        source="REST",
        observed_at=observed_at,
        venue_timestamp=observed_at,
        raw_payload_hash=_payload_hash(payload),
        raw_payload_json=payload,
    )
    return fact_id, payload


def reconcile_cancel_ack_terminal_no_fill_facts(conn: sqlite3.Connection) -> dict:
    """Materialize terminal no-fill order facts from already-acked cancels.

    A CANCEL_ACKED command event is venue-side evidence that the entry order left
    the book. If the local command has no positive trade facts and its
    pending_entry projection has zero exposure, the missing terminal order fact is
    a stale read-model gap. Append that fact so the existing terminal-order-fact
    reducer can void the pending entry projection.
    """

    summary = {"scanned": 0, "advanced": 0, "stayed": 0, "errors": 0}
    for row in _cancel_ack_terminal_no_fill_fact_candidates(conn):
        summary["scanned"] += 1
        command_id = str(row.get("command_id") or "")
        venue_order_id = str(row.get("venue_order_id") or "")
        command_state = str(row.get("command_state") or "")
        fact_state = _terminal_fact_state_for_venue_status(
            command_state,
            venue_resp_present=True,
        )
        if fact_state is None:
            summary["stayed"] += 1
            continue
        occurred_at = str(row.get("terminal_event_occurred_at") or _now_iso())
        has_projection = bool(str(row.get("projected_position_id") or "").strip())
        remaining_size = str(
            row.get("latest_order_fact_remaining_size")
            or row.get("command_size")
            or "0"
        )
        payload = {
            "reason": "cancel_ack_terminal_no_fill",
            "proof_class": (
                "cancel_ack_plus_zero_pending_projection"
                if has_projection
                else "cancel_ack_plus_zero_unprojected_entry"
            ),
            "command_id": command_id,
            "venue_order_id": venue_order_id,
            "command_state": command_state,
            "terminal_fact_state": fact_state,
            "latest_order_fact_id": row.get("latest_order_fact_id"),
            "latest_order_fact_state": row.get("latest_order_fact_state"),
            "latest_order_fact_source": row.get("latest_order_fact_source"),
            "remaining_size": remaining_size,
            "matched_size": "0",
            "required_predicates": {
                "entry_command_terminal": True,
                "cancel_or_expire_event_observed": True,
                "latest_order_fact_matches_command_order": True,
                "latest_order_fact_no_fill": True,
                "pending_entry_projection_zero_exposure": has_projection,
                "position_projection_absent": not has_projection,
                "no_positive_trade_facts": True,
                "no_existing_terminal_order_fact": True,
            },
        }
        safe_command_id = "".join(ch if ch.isalnum() else "_" for ch in command_id)
        sp_name = f"sp_cancel_ack_no_fill_fact_{safe_command_id}"
        conn.execute(f"SAVEPOINT {sp_name}")
        try:
            append_order_fact(
                conn,
                venue_order_id=venue_order_id,
                command_id=command_id,
                state=fact_state,
                remaining_size=remaining_size,
                matched_size="0",
                source="REST",
                observed_at=occurred_at,
                venue_timestamp=occurred_at,
                raw_payload_hash=_payload_hash(payload),
                raw_payload_json=payload,
            )
            conn.execute(f"RELEASE SAVEPOINT {sp_name}")
            summary["advanced"] += 1
        except Exception as exc:
            conn.execute(f"ROLLBACK TO SAVEPOINT {sp_name}")
            conn.execute(f"RELEASE SAVEPOINT {sp_name}")
            logger.error(
                "recovery: cancel-ack terminal no-fill fact failed for command %s: %s",
                command_id,
                exc,
            )
            summary["errors"] += 1
    return summary


def reconcile_local_orphan_no_fill_findings(conn: sqlite3.Connection, client) -> dict:
    """Convert proven no-fill local-orphan findings into terminal order facts."""

    summary = {"scanned": 0, "advanced": 0, "stayed": 0, "errors": 0}
    for row in _local_orphan_no_fill_candidates(conn):
        summary["scanned"] += 1
        command_id = str(row.get("command_id") or "")
        venue_order_id = str(row.get("venue_order_id") or "")
        try:
            evidence = _json_dict(row.get("finding_evidence_json"))
            if not _finding_proves_trade_enumeration(evidence):
                summary["stayed"] += 1
                continue
            if row.get("order_fact_id") is not None:
                if str(row.get("order_fact_source") or "") not in _LIVE_TERMINAL_ORDER_FACT_SOURCES:
                    summary["stayed"] += 1
                    continue
                if not _decimal_is_zero(row.get("order_fact_matched_size")):
                    summary["stayed"] += 1
                    continue
            if _fill_trade_fact_count(conn, command_id) > 0:
                summary["stayed"] += 1
                continue
            get_order = getattr(client, "get_order", None)
            if not callable(get_order):
                logger.warning("recovery: client lacks get_order for local orphan %s", venue_order_id)
                summary["errors"] += 1
                continue
            try:
                venue_payload = _venue_order_payload(get_order(venue_order_id))
            except Exception as exc:
                logger.warning(
                    "recovery: local orphan venue lookup for command %s (venue_order_id=%s) raised: %s",
                    command_id,
                    venue_order_id,
                    exc,
                )
                summary["errors"] += 1
                continue
            venue_status = (
                str((venue_payload or {}).get("status") or (venue_payload or {}).get("state") or "NOT_FOUND")
                .upper()
            )
            if _terminal_fact_state_for_venue_status(
                venue_status,
                venue_resp_present=venue_payload is not None,
            ) is None:
                summary["stayed"] += 1
                continue
            _append_local_orphan_terminal_order_fact(
                conn,
                command=row,
                observed_at=_now_iso(),
                venue_status=venue_status,
                venue_resp=venue_payload,
            )
            summary["advanced"] += 1
            logger.info("recovery: local orphan no-fill %s -> terminal order fact", venue_order_id)
        except Exception as exc:
            logger.error(
                "recovery: local orphan no-fill reconciliation failed for command %s: %s",
                command_id,
                exc,
            )
            summary["errors"] += 1
    return summary


def reconcile_stale_terminal_no_fill_findings(conn: sqlite3.Connection) -> dict:
    """Resolve local-orphan findings after canonical terminal no-fill recovery."""

    summary = {"scanned": 0, "advanced": 0, "stayed": 0, "errors": 0}
    for row in _stale_local_orphan_terminal_no_fill_candidates(conn):
        summary["scanned"] += 1
        command_id = str(row.get("command_id") or "")
        venue_order_id = str(row.get("venue_order_id") or "")
        fact_order_id = str(row.get("order_fact_venue_order_id") or "")
        try:
            if not venue_order_id or venue_order_id != fact_order_id:
                summary["errors"] += 1
                logger.error(
                    "recovery: stale local-orphan terminal no-fill candidate %s order mismatch "
                    "(command=%s fact=%s)",
                    command_id,
                    venue_order_id,
                    fact_order_id,
                )
                continue
            if _trade_fact_count(conn, command_id) > 0:
                summary["stayed"] += 1
                continue
            resolved = _resolve_m5_local_orphan_findings(
                conn,
                venue_order_id=venue_order_id,
                resolved_at=str(row.get("order_fact_observed_at") or _now_iso()),
                resolution="command_recovery_terminal_no_fill",
            )
            summary["advanced"] += resolved
        except Exception as exc:
            logger.error(
                "recovery: stale local-orphan terminal no-fill finding failed for command %s: %s",
                command_id,
                exc,
            )
            summary["errors"] += 1
    return summary


def _terminal_point_order_candidates(conn: sqlite3.Connection) -> list[dict]:
    if not _table_exists(conn, "venue_order_facts"):
        return []
    command_states = tuple(sorted(_ACKED_ORDER_STATES))
    if not command_states:
        return []
    state_placeholders = ",".join("?" for _ in command_states)
    sql = "WITH " + _canonical_order_truth_cte() + f"""
        SELECT
            cmd.*,
            pc.phase AS position_phase,
            pc.city AS position_city,
            pc.target_date AS position_target_date,
            pc.temperature_metric AS position_temperature_metric,
            pc.strategy_key AS position_strategy_key,
            pc.chain_shares AS position_chain_shares,
            pc.shares AS position_shares,
            fact.fact_id AS order_fact_id,
            fact.state AS order_fact_state,
            fact.observed_at AS order_fact_observed_at,
            fact.venue_order_id AS order_fact_venue_order_id,
            fact.remaining_size AS order_fact_remaining_size,
            fact.matched_size AS order_fact_matched_size,
            fact.source AS order_fact_source
          FROM venue_commands cmd
          JOIN canonical_order_truth fact
            ON fact.command_id = cmd.command_id
          JOIN position_current pc
            ON pc.position_id = cmd.position_id
         WHERE cmd.intent_kind IN ('ENTRY', 'EXIT')
           AND cmd.state IN ({state_placeholders})
           AND COALESCE(cmd.venue_order_id, '') != ''
           AND (
                (
                    cmd.intent_kind = 'ENTRY'
                    AND pc.phase = 'pending_entry'
                    AND CAST(COALESCE(pc.shares, '0') AS REAL) = 0
                    AND CAST(COALESCE(pc.cost_basis_usd, '0') AS REAL) = 0
                )
                OR (
                    cmd.intent_kind = 'EXIT'
                    AND pc.phase = 'pending_exit'
                    AND CAST(COALESCE(pc.chain_shares, pc.shares, '0') AS REAL) > 0
                )
           )
           AND fact.state IN ('LIVE', 'RESTING')
           AND CAST(COALESCE(fact.matched_size, '0') AS REAL) = 0
         ORDER BY cmd.updated_at, cmd.command_id
        """
    rows = conn.execute(
        sql,
        command_states,
    ).fetchall()
    return [_dict_row(row) for row in rows]


def _phase_after_terminal_exit_no_fill(command: Mapping[str, object], *, observed_at: str) -> str:
    city = str(command.get("position_city") or "").strip()
    target_date = str(command.get("position_target_date") or "").strip()
    try:
        from src.config import runtime_cities_by_name
        from src.strategy.market_phase import settlement_day_entry_utc

        city_cfg = runtime_cities_by_name().get(city)
        tz_name = str(getattr(city_cfg, "timezone", "") or "")
        as_of = _parse_utc(observed_at)
        day0_start = settlement_day_entry_utc(
            target_local_date=date.fromisoformat(target_date),
            city_timezone=tz_name,
        )
        return "day0_window" if as_of >= day0_start else "active"
    except Exception:
        return "day0_window"


def _release_pending_exit_after_terminal_no_fill(
    conn: sqlite3.Connection,
    *,
    command: Mapping[str, object],
    observed_at: str,
    order_fact_id: int,
    terminal_payload: Mapping[str, object],
) -> bool:
    if str(command.get("intent_kind") or "").upper() != "EXIT":
        return False
    position_id = str(command.get("position_id") or "").strip()
    command_id = str(command.get("command_id") or "").strip()
    venue_order_id = str(command.get("venue_order_id") or "").strip()
    if not position_id or not command_id:
        return False
    current = conn.execute(
        """
        SELECT phase, strategy_key
          FROM position_current
         WHERE position_id = ?
         LIMIT 1
        """,
        (position_id,),
    ).fetchone()
    if current is None or str(current[0] or "") != "pending_exit":
        return False
    phase_after = _phase_after_terminal_exit_no_fill(command, observed_at=observed_at)
    event_key = f"{position_id}:exit_terminal_no_fill:{command_id}"
    existing = conn.execute(
        "SELECT 1 FROM position_events WHERE idempotency_key = ? LIMIT 1",
        (event_key,),
    ).fetchone()
    conn.execute(
        """
        UPDATE position_current
           SET phase = ?,
               order_id = NULL,
               order_status = 'filled',
               exit_reason = 'EXIT_ORDER_TERMINAL_NO_FILL_RELEASED',
               updated_at = ?
         WHERE position_id = ?
           AND phase = 'pending_exit'
        """,
        (phase_after, observed_at, position_id),
    )
    if existing is not None:
        return True
    seq = _latest_position_sequence(conn, position_id) + 1
    payload = {
        "reason": "exit_order_terminal_no_fill_released_pending_exit",
        "proof_class": "exit_point_order_terminal_no_fill_plus_open_trade_absence",
        "command_id": command_id,
        "venue_order_id": venue_order_id,
        "venue_command_state": command.get("state"),
        "venue_order_fact_id": order_fact_id,
        "phase_before": "pending_exit",
        "phase_after": phase_after,
        "terminal_order_fact": dict(terminal_payload),
    }
    conn.execute(
        """
        INSERT INTO position_events (
            event_id, position_id, event_version, sequence_no, event_type,
            occurred_at, phase_before, phase_after, strategy_key, decision_id,
            snapshot_id, order_id, command_id, caused_by, idempotency_key,
            venue_status, source_module, payload_json, env
        ) VALUES (?, ?, 1, ?, 'EXIT_ORDER_VOIDED', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            event_key,
            position_id,
            seq,
            observed_at,
            "pending_exit",
            phase_after,
            str(command.get("position_strategy_key") or current[1] or "opening_inertia"),
            str(command.get("decision_id") or ""),
            str(command.get("snapshot_id") or ""),
            venue_order_id,
            command_id,
            f"venue_command:{command_id}",
            event_key,
            "TERMINAL_NO_FILL",
            "src.execution.command_recovery",
            json.dumps(payload, sort_keys=True, default=str),
            _latest_position_env(conn, position_id),
        ),
    )
    return True


def reconcile_terminal_point_orders(conn: sqlite3.Connection, client) -> dict:
    """Append terminal no-fill facts when CLOB point truth closes stale ACKED entries."""

    summary = {"scanned": 0, "advanced": 0, "stayed": 0, "errors": 0}
    get_order = getattr(client, "get_order", None)
    if not callable(get_order):
        return summary
    candidates = _terminal_point_order_candidates(conn)
    if not candidates:
        return summary
    try:
        open_orders = _client_open_orders(client)
        trades = _client_trades(client)
    except Exception as exc:
        logger.error("recovery: terminal point-order account truth enumeration failed: %s", exc)
        summary["errors"] += len(candidates)
        return summary
    for row in candidates:
        summary["scanned"] += 1
        command_id = str(row.get("command_id") or "")
        venue_order_id = str(row.get("venue_order_id") or "")
        try:
            if _trade_fact_count(conn, command_id) > 0:
                summary["stayed"] += 1
                continue
            point_order = _venue_order_payload(get_order(venue_order_id))
            venue_status = (
                str((point_order or {}).get("status") or (point_order or {}).get("state") or "NOT_FOUND")
                .upper()
            )
            fact_state = _terminal_fact_state_for_venue_status(
                venue_status,
                venue_resp_present=point_order is not None,
            )
            if fact_state is None:
                summary["stayed"] += 1
                continue
            no_fill_proven, no_fill_reason = _terminal_point_order_zero_fill_proven(
                conn,
                command_id=command_id,
                point_order=point_order,
            )
            if not no_fill_proven:
                logger.info(
                    "recovery: terminal point-order candidate %s stayed; no zero-fill proof (%s)",
                    command_id,
                    no_fill_reason,
                )
                summary["stayed"] += 1
                continue
            matching_open_orders = _matching_open_orders_for_command(client, row, open_orders=open_orders)
            matching_trades = _matching_trades_for_command(client, row, trades=trades)
            if matching_open_orders or matching_trades:
                summary["stayed"] += 1
                continue
            observed_at = _now_iso()
            order_fact_id, terminal_payload = _append_point_order_terminal_no_fill_fact(
                conn,
                command=row,
                observed_at=observed_at,
                venue_status=venue_status,
                point_order=point_order,
                matching_open_orders=matching_open_orders,
                matching_trades=matching_trades,
                source_reason="acked_point_order_terminal_no_fill",
            )
            if str(row.get("intent_kind") or "").upper() == "EXIT":
                append_event(
                    conn,
                    command_id=command_id,
                    event_type=CommandEventType.EXPIRED.value,
                    occurred_at=observed_at,
                    payload={
                        "reason": "exit_point_order_terminal_no_fill",
                        "venue_order_id": venue_order_id,
                        "order_fact_id": order_fact_id,
                        "proof_class": "exit_terminal_no_fill_recovery",
                    },
                )
                _release_pending_exit_after_terminal_no_fill(
                    conn,
                    command=row,
                    observed_at=observed_at,
                    order_fact_id=order_fact_id,
                    terminal_payload=terminal_payload,
                )
            summary["advanced"] += 1
        except Exception as exc:
            logger.error(
                "recovery: terminal point-order reconciliation failed for command %s: %s",
                command_id,
                exc,
            )
            summary["errors"] += 1
    return summary


def _append_partial_remainder_terminal_order_fact(
    conn: sqlite3.Connection,
    *,
    command: dict,
    observed_at: str,
    matched_size: str,
    point_order_status: str,
    point_order: dict | None,
) -> int:
    command_id = str(command.get("command_id") or "")
    venue_order_id = str(command.get("venue_order_id") or "")
    payload = {
        "reason": "partial_remainder_absent_from_exchange_open_orders",
        "proof_class": "confirmed_fill_plus_point_order_terminal_remainder",
        "source_surface": "client.get_open_orders+client.get_order",
        "venue_order_id": venue_order_id,
        "command_id": command_id,
        "open_order_absent": True,
        "point_order_status": point_order_status,
        "point_order": point_order,
        "remaining_size": "0",
        "matched_size": matched_size,
    }
    return append_order_fact(
        conn,
        venue_order_id=venue_order_id,
        command_id=command_id,
        state="EXPIRED",
        remaining_size="0",
        matched_size=matched_size,
        source="REST",
        observed_at=observed_at,
        raw_payload_hash=_payload_hash(payload),
        raw_payload_json=payload,
    )


def _point_order_terminal_for_partial_remainder(client, venue_order_id: str) -> tuple[bool, str, dict | None]:
    get_order = getattr(client, "get_order", None)
    if not callable(get_order):
        raise RuntimeError("client lacks get_order; partial remainder terminal proof is unknown")
    raw = _venue_order_payload(get_order(venue_order_id))
    if raw is None:
        return True, "NOT_FOUND", None
    status = str(raw.get("status") or raw.get("state") or "").upper()
    if status in _TERMINAL_NO_FILL_VENUE_STATUSES:
        return True, status, raw
    # GONE-ORDER TERMINAL PROOF (2026-06-16): a PARTIAL whose remainder is already
    # confirmed ABSENT from client.get_open_orders (the only candidates that reach
    # this function — see reconcile_partial_remainders) and whose client.get_order
    # returns UNKNOWN/empty (the venue has NO live record: OrderState(status='UNKNOWN',
    # raw={}) -> _venue_order_payload synthesizes only {'status':'UNKNOWN','orderID':..}
    # with no size/matched/price fields) is GONE — the unfilled remainder was cancelled/
    # purged and the venue retains no order. Absent-from-open-orders + no-live-record is
    # terminal proof; the recorded matched_size (the real partial fill) is preserved on
    # the EXPIRED fact. Without this, such orders sit PARTIALLY_MATCHED forever (open ->
    # HOLD_REST_IN_PROGRESS blocks the family; NOT terminal_unfilled -> never escalates),
    # zero new ENTRY orders despite a healthy +edge decision lane (live 2026-06-16). A
    # LIVE/RESTING/PARTIALLY_MATCHED/MATCHED/FILLED status (a real live/fill record) is
    # NOT terminalized here — only the no-live-record UNKNOWN/absent case.
    if _point_order_no_live_record(raw, expected_order_id=venue_order_id):
        return True, status or "NOT_FOUND", raw
    return False, status or "UNKNOWN", raw


def reconcile_partial_remainders(
    conn: sqlite3.Connection,
    client,
    *,
    updated_before: str | None = None,
) -> dict:
    """Terminalize filled command remainders when the venue open-order surface is empty.

    ``PARTIAL`` means Zeus has observed at least some fill. If the exchange's
    fresh open-order enumeration no longer contains the order, only the
    unfilled remainder has disappeared. The command may become ``EXPIRED``,
    but the filled position exposure must remain intact and must continue to
    flow through venue_trade_facts/position projections.
    """

    summary = {"scanned": 0, "advanced": 0, "stayed": 0, "errors": 0}
    candidates = _partial_remainder_candidates(conn, updated_before=updated_before)
    if not candidates:
        return summary
    try:
        open_order_ids = _client_open_order_ids(client)
    except Exception as exc:
        logger.warning(
            "recovery: partial remainder open-order enumeration failed: %s",
            exc,
        )
        summary["errors"] = len(candidates)
        return summary

    for command in candidates:
        summary["scanned"] += 1
        command_id = str(command.get("command_id") or "")
        venue_order_id = str(command.get("venue_order_id") or "")
        command_state = str(command.get("state") or "")
        try:
            fill_summary = _positive_fill_trade_fact_summary(conn, command_id)
            fill_coverage = _command_fill_coverage_state(command, fill_summary)
            if command_state == CommandState.FILLED.value and fill_coverage != "partial":
                summary["stayed"] += 1
                continue
            existing_terminal_remainder = _latest_terminal_remainder_order_fact(
                conn,
                command_id=command_id,
            )
            if existing_terminal_remainder is not None:
                if command_state == CommandState.PARTIAL.value:
                    append_event(
                        conn,
                        command_id=command_id,
                        event_type=CommandEventType.EXPIRED.value,
                        occurred_at=str(existing_terminal_remainder.get("observed_at") or _now_iso()),
                        payload={
                            "reason": "existing_terminal_remainder_order_fact",
                            "venue_order_id": venue_order_id,
                            "venue_order_fact_id": existing_terminal_remainder.get("fact_id"),
                            "venue_order_fact_state": existing_terminal_remainder.get("state"),
                            "proof_class": "canonical_terminal_remainder_order_fact",
                            "positive_fill_size": existing_terminal_remainder.get("matched_size"),
                            "remaining_size": existing_terminal_remainder.get("remaining_size"),
                        },
                    )
                    summary["advanced"] += 1
                else:
                    summary["stayed"] += 1
                continue
            if venue_order_id in open_order_ids:
                summary["stayed"] += 1
                continue
            now = _now_iso()
            point_terminal, point_status, point_order = _point_order_terminal_for_partial_remainder(
                client,
                venue_order_id,
            )
            if not point_terminal:
                # MATCHED is treated like FILLED here (GATE #84 follow-up, 2026-06-22):
                # a partial remainder ABSENT from open orders whose point order reports
                # MATCHED means the remainder filled at the venue but the fill fact has
                # not yet arrived. MATCHED is not a terminal no-fill status (it carries a
                # live/fill record, so it is not terminalized in
                # _point_order_terminal_for_partial_remainder), so without this branch it
                # looped "staying" forever and the PARTIALLY_MATCHED order fact kept the
                # family's entry lane blocked (unexpired_family_rest=True). Route it to
                # REVIEW_REQUIRED for fill-fact reconciliation, identical to FILLED.
                if point_status in ("FILLED", "MATCHED"):
                    append_event(
                        conn,
                        command_id=command_id,
                        event_type=CommandEventType.REVIEW_REQUIRED.value,
                        occurred_at=now,
                        payload={
                            "reason": "partial_remainder_point_order_filled_without_full_trade_fact",
                            "venue_order_id": venue_order_id,
                            "point_order_status": point_status,
                            "point_order": point_order,
                            "proof_class": "point_order_filled_requires_complete_fill_fact_authority",
                        },
                    )
                    logger.warning(
                        "recovery: command %s PARTIAL absent from open orders but point order is %s "
                        "without complete trade-fact authority -> REVIEW_REQUIRED",
                        command_id,
                        point_status,
                    )
                    summary["advanced"] += 1
                else:
                    logger.info(
                        "recovery: command %s PARTIAL absent from open orders but point order status=%s; staying",
                        command_id,
                        point_status,
                    )
                    summary["stayed"] += 1
                continue
            if fill_summary["count"] <= 0:
                append_event(
                    conn,
                    command_id=command_id,
                    event_type=CommandEventType.REVIEW_REQUIRED.value,
                    occurred_at=now,
                    payload={
                        "reason": "partial_remainder_absent_without_trade_fact",
                        "venue_order_id": venue_order_id,
                        "proof_class": "open_order_absence_without_fill_fact_authority",
                    },
                )
                logger.warning(
                    "recovery: command %s PARTIAL absent from open orders but has no "
                    "positive fill trade fact -> REVIEW_REQUIRED",
                    command_id,
                )
                summary["advanced"] += 1
                continue
            conn.execute("SAVEPOINT sp_partial_remainder_repair")
            try:
                order_fact_id = _append_partial_remainder_terminal_order_fact(
                    conn,
                    command=command,
                    observed_at=now,
                    matched_size=fill_summary["filled_size"],
                    point_order_status=point_status,
                    point_order=point_order,
                )
                resolved_findings = _resolve_m5_local_orphan_findings(
                    conn,
                    venue_order_id=venue_order_id,
                    resolved_at=now,
                    resolution="command_recovery_expired_partial_remainder",
                )
                if command_state == CommandState.PARTIAL.value:
                    append_event(
                        conn,
                        command_id=command_id,
                        event_type=CommandEventType.EXPIRED.value,
                        occurred_at=now,
                        payload={
                            "reason": "partial_remainder_absent_from_exchange_open_orders",
                            "venue_order_id": venue_order_id,
                            "venue_order_fact_id": order_fact_id,
                            "proof_class": "confirmed_fill_plus_point_order_terminal_remainder",
                            "point_order_status": point_status,
                            "positive_fill_trade_fact_count": fill_summary["count"],
                            "positive_fill_size": fill_summary["filled_size"],
                            "resolved_m5_local_orphan_findings": resolved_findings,
                        },
                    )
                conn.execute("RELEASE SAVEPOINT sp_partial_remainder_repair")
            except Exception:
                conn.execute("ROLLBACK TO SAVEPOINT sp_partial_remainder_repair")
                conn.execute("RELEASE SAVEPOINT sp_partial_remainder_repair")
                raise
            logger.info(
                "recovery: command %s %s partial remainder terminalized "
                "(venue_order_id=%s; fill_trade_facts=%d; resolved_findings=%d)",
                command_id,
                command_state,
                venue_order_id,
                fill_summary["count"],
                resolved_findings,
            )
            summary["advanced"] += 1
        except Exception as exc:
            logger.error(
                "recovery: partial remainder reconciliation failed for command %s: %s",
                command_id,
                exc,
            )
            summary["errors"] += 1
    return summary


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


def _submit_unknown_command(conn: sqlite3.Connection, command_id: str) -> dict:
    row = conn.execute(
        "SELECT * FROM venue_commands WHERE command_id = ?",
        (command_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"unknown command_id: {command_id}")
    command = _dict_row(row)
    if command.get("state") != CommandState.SUBMIT_UNKNOWN_SIDE_EFFECT.value:
        raise ValueError(
            "geoblock clearance is only legal for SUBMIT_UNKNOWN_SIDE_EFFECT commands; "
            f"got {command.get('state')!r}"
        )
    return command


def _latest_event_payload(events: list[dict]) -> tuple[str, dict]:
    if not events:
        return "", {}
    latest = events[-1]
    return str(latest.get("event_type") or ""), _json_dict(latest.get("payload_json"))


def _payload_is_cancel_unknown(latest_payload: dict) -> bool:
    if (
        str(latest_payload.get("semantic_cancel_status") or "").upper() == "CANCEL_UNKNOWN"
        and latest_payload.get("requires_m5_reconcile") is True
    ):
        return True
    return str(latest_payload.get("reason") or "") == "post_cancel_unknown_possible_side_effect"


def _latest_cancel_unknown_payload(events: list[dict]) -> dict | None:
    latest_event_type, latest_payload = _latest_event_payload(events)
    if latest_event_type != CommandEventType.CANCEL_REPLACE_BLOCKED.value:
        return None
    if not _payload_is_cancel_unknown(latest_payload):
        return None
    return latest_payload


def _latest_maker_rest_cancel_requested_payload(events: list[dict]) -> dict | None:
    latest_event_type, latest_payload = _latest_event_payload(events)
    if latest_event_type != CommandEventType.CANCEL_REQUESTED.value:
        return None
    if str(latest_payload.get("source") or "") != "maker_rest_escalation":
        return None
    return latest_payload


def _trade_matches_venue_order_id(raw: dict, venue_order_id: str) -> bool:
    if str(raw.get("taker_order_id") or raw.get("order_id") or raw.get("orderID") or "") == venue_order_id:
        return True
    for maker in raw.get("maker_orders") or ():
        if isinstance(maker, dict) and str(
            maker.get("order_id") or maker.get("orderID") or maker.get("id") or ""
        ) == venue_order_id:
            return True
    return False


def _confirmed_trade_for_order_id(client, venue_order_id: str) -> dict | None:
    try:
        trades = _client_read_items(client, "get_trades").items
    except Exception as exc:
        logger.warning(
            "recovery: cancel-unknown trade lookup unavailable for order %s: %s",
            venue_order_id,
            exc,
        )
        return None
    for item in trades:
        raw = _raw_payload(item)
        if not _trade_matches_venue_order_id(raw, venue_order_id):
            continue
        status = str(raw.get("status") or raw.get("state") or "").upper()
        if status != "CONFIRMED":
            continue
        if not _positive_decimal_or_none(raw.get("size") or raw.get("matched_amount")):
            continue
        if not _positive_decimal_or_none(raw.get("price")):
            continue
        if not str(raw.get("id") or raw.get("trade_id") or "").strip():
            continue
        return raw
    return None


def _selected_maker_order_for_trade(trade: dict, venue_order_id: str) -> dict | None:
    for maker in trade.get("maker_orders") or ():
        if not isinstance(maker, dict):
            continue
        maker_order_id = str(
            maker.get("order_id")
            or maker.get("orderID")
            or maker.get("orderId")
            or maker.get("id")
            or ""
        )
        if maker_order_id == venue_order_id:
            return maker
    return None


def _confirmed_trade_command_leg(trade: dict, venue_order_id: str) -> tuple[str, str]:
    maker = _selected_maker_order_for_trade(trade, venue_order_id)
    if maker is not None:
        size = (
            maker.get("matched_amount")
            or maker.get("matchedAmount")
            or maker.get("filled_size")
            or maker.get("size")
            or maker.get("amount")
            or ""
        )
        price = (
            maker.get("avgPrice")
            or maker.get("avg_price")
            or maker.get("fillPrice")
            or maker.get("fill_price")
            or maker.get("price")
            or ""
        )
        return str(size), str(price)
    return str(trade.get("size") or trade.get("matched_amount") or ""), str(trade.get("price") or "")


def _append_cancel_unknown_confirmed_trade_fill(
    conn: sqlite3.Connection,
    *,
    command: dict,
    point_order: dict,
    trade: dict,
    observed_at: str,
) -> None:
    command_id = str(command.get("command_id") or "")
    venue_order_id = str(command.get("venue_order_id") or "")
    trade_id = str(trade.get("id") or trade.get("trade_id") or "")
    filled_size, fill_price = _confirmed_trade_command_leg(trade, venue_order_id)
    tx_hash = str(trade.get("transaction_hash") or trade.get("tx_hash") or "") or None
    venue_status = _order_status(point_order)
    matched_size = _point_order_matched_size(
        point_order,
        fallback=filled_size,
        side=command.get("side"),
    )
    remaining_size = _matched_remaining_size(command, matched_size, venue_status=venue_status)
    event_type = _matched_event_type(command, matched_size, venue_status=venue_status)
    payload = {
        "schema_version": 1,
        "reason": "review_cleared_confirmed_fill",
        "command_id": command_id,
        "decision_id": str(command.get("decision_id") or ""),
        "venue_order_id": venue_order_id,
        "trade_id": trade_id,
        "filled_size": filled_size,
        "fill_price": fill_price,
        "proof_class": "cancel_unknown_confirmed_trade_with_positive_trade_fact",
        "venue_order_proof": {
            "source": "authenticated_clob_point_order_read",
            "observed_at": observed_at,
            "venue_status": venue_status,
            "matched_size": matched_size,
            "remaining_size": remaining_size,
            "point_order": point_order,
        },
        "trade_fact_proof": {
            "source": "authenticated_clob_user_trades",
            "observed_at": observed_at,
            "trade_status": str(trade.get("status") or trade.get("state") or ""),
            "trade": trade,
        },
        "required_predicates": {
            "latest_event_is_cancel_replace_blocked": True,
            "semantic_cancel_status_cancel_unknown": True,
            "requires_m5_reconcile": True,
            "positive_trade_fact": True,
        },
        "source_proof": {
            "source_commit": "runtime",
            "source_function": "command_recovery._review_required_cancel_unknown_live_order_recovery",
            "source_reason": "cancel_unknown_confirmed_trade_fill",
        },
        "reviewed_by": "command_recovery",
        "cleared_at": observed_at,
    }
    append_order_fact(
        conn,
        venue_order_id=venue_order_id,
        command_id=command_id,
        state=_matched_order_fact_state(
            event_type=event_type,
            venue_status=venue_status,
            remaining_size=remaining_size,
        ),
        remaining_size=remaining_size,
        matched_size=matched_size,
        source="REST",
        observed_at=observed_at,
        venue_timestamp=observed_at,
        raw_payload_hash=_payload_hash({**payload, "fact_type": "order"}),
        raw_payload_json=payload,
    )
    append_trade_fact(
        conn,
        trade_id=trade_id,
        venue_order_id=venue_order_id,
        command_id=command_id,
        state="CONFIRMED",
        filled_size=filled_size,
        fill_price=fill_price,
        source="REST",
        observed_at=observed_at,
        venue_timestamp=observed_at,
        tx_hash=tx_hash,
        raw_payload_hash=_payload_hash({**payload, "fact_type": "trade"}),
        raw_payload_json=payload,
    )
    append_event(
        conn,
        command_id=command_id,
        event_type=event_type,
        occurred_at=observed_at,
        payload=payload,
    )
    if str(command.get("intent_kind") or "").upper() == "ENTRY":
        _append_matched_order_fill_projection(
            conn,
            command=command,
            venue_order_id=venue_order_id,
            matched_size=filled_size,
            fill_price=fill_price,
            observed_at=observed_at,
        )
    else:
        _append_exit_order_fill_projection(
            conn,
            command=command,
            venue_order_id=venue_order_id,
            matched_size=filled_size,
            fill_price=fill_price,
            observed_at=observed_at,
            event_type=event_type,
        )


def _review_required_cancel_unknown_live_order_recovery(
    conn: sqlite3.Connection,
    cmd: VenueCommand,
    client,
) -> str:
    events = _command_events(conn, cmd.command_id)
    if _latest_cancel_unknown_payload(events) is None:
        return "stayed"
    venue_order_id = str(cmd.venue_order_id or "").strip()
    if not venue_order_id:
        return "stayed"
    try:
        raw_order = client.get_order(venue_order_id)
    except Exception as exc:
        logger.warning(
            "recovery: review-required cancel-unknown point lookup for command %s "
            "(venue_order_id=%s) raised: %s",
            cmd.command_id,
            venue_order_id,
            exc,
        )
        return "error"
    order = _venue_order_payload(raw_order)
    point_order_no_live_record = _point_order_no_live_record(
        order,
        expected_order_id=venue_order_id,
    )
    if order is None or point_order_no_live_record:
        point_order_status = (
            str((order or {}).get("status") or (order or {}).get("state") or "NOT_FOUND")
            .upper()
        )
        source_reason = (
            "cancel_unknown_point_order_no_live_record_terminal_no_fill"
            if point_order_no_live_record
            else "cancel_unknown_point_order_absent_terminal_no_fill"
        )
        resolution = (
            "command_recovery_point_order_no_live_record_no_fill"
            if point_order_no_live_record
            else "command_recovery_point_order_absent_no_fill"
        )
        command = _dict_row(
            conn.execute(
                "SELECT * FROM venue_commands WHERE command_id = ?",
                (cmd.command_id,),
            ).fetchone()
        )
        if not _ensure_entry_projection_is_pending_zero_exposure(
            conn,
            command=command,
            order_id=venue_order_id,
        ):
            logger.info(
                "recovery: command %s REVIEW_REQUIRED cancel-unknown stayed "
                "(point order %s but entry projection is not zero-exposure pending)",
                cmd.command_id,
                "has no live record" if point_order_no_live_record else "absent",
            )
            return "stayed"
        matching_open_orders = _matching_open_orders_for_command(client, command)
        matching_trades = _matching_trades_for_command(client, command)
        if matching_open_orders:
            logger.info(
                "recovery: command %s REVIEW_REQUIRED cancel-unknown stayed "
                "(point order %s but account open order still matches: open_orders=%s)",
                cmd.command_id,
                "has no live record" if point_order_no_live_record else "absent",
                len(matching_open_orders),
            )
            return "stayed"
        if matching_trades:
            trade = _confirmed_trade_for_order_id(client, venue_order_id)
            if trade is not None:
                now = _now_iso()
                safe_command_id = "".join(ch if ch.isalnum() else "_" for ch in cmd.command_id)
                sp_name = f"sp_cancel_unknown_confirmed_trade_{safe_command_id}"
                conn.execute(f"SAVEPOINT {sp_name}")
                try:
                    _append_cancel_unknown_confirmed_trade_fill(
                        conn,
                        command=command,
                        point_order=order or {
                            "orderID": venue_order_id,
                            "status": point_order_status,
                        },
                        trade=trade,
                        observed_at=now,
                    )
                    conn.execute(f"RELEASE SAVEPOINT {sp_name}")
                except Exception:
                    conn.execute(f"ROLLBACK TO SAVEPOINT {sp_name}")
                    conn.execute(f"RELEASE SAVEPOINT {sp_name}")
                    raise
                logger.info(
                    "recovery: command %s REVIEW_REQUIRED cancel-unknown -> trade fill "
                    "(venue_order_id=%s point_order_status=%s)",
                    cmd.command_id,
                    venue_order_id,
                    point_order_status,
                )
                return "advanced"
            logger.info(
                "recovery: command %s REVIEW_REQUIRED cancel-unknown stayed "
                "(point order %s but only exposure-level trades matched: trades=%s)",
                cmd.command_id,
                "has no live record" if point_order_no_live_record else "absent",
                len(matching_trades),
            )
            return "stayed"
        if _trade_fact_count(conn, cmd.command_id) != 0:
            logger.info(
                "recovery: command %s REVIEW_REQUIRED cancel-unknown stayed "
                "(point order %s but local trade facts exist)",
                cmd.command_id,
                "has no live record" if point_order_no_live_record else "absent",
            )
            return "stayed"
        now = _now_iso()
        safe_command_id = "".join(ch if ch.isalnum() else "_" for ch in cmd.command_id)
        sp_name = f"sp_cancel_unknown_no_live_exposure_{safe_command_id}"
        conn.execute(f"SAVEPOINT {sp_name}")
        try:
            fact_id, fact_payload = _append_point_order_terminal_no_fill_fact(
                conn,
                command=command,
                observed_at=now,
                venue_status=point_order_status,
                point_order=order,
                matching_open_orders=matching_open_orders,
                matching_trades=matching_trades,
                source_reason=source_reason,
                venue_resp_present_for_terminal_state=False,
            )
            resolved_findings = _resolve_m5_local_orphan_findings(
                conn,
                venue_order_id=venue_order_id,
                resolved_at=now,
                resolution=resolution,
            )
            point_order_presence_predicate = {
                "point_order_no_live_record": True,
            } if point_order_no_live_record else {
                "point_order_absent": True,
            }
            payload = {
                "schema_version": 1,
                "reason": "review_cleared_no_venue_exposure",
                "command_id": cmd.command_id,
                "venue_order_id": venue_order_id,
                "proof_class": "cancel_unknown_terminal_no_fill",
                "side_effect_boundary_crossed": "unknown",
                "sdk_submit_attempted": "unknown",
                "required_predicates": {
                    "latest_event_is_cancel_replace_blocked": True,
                    "semantic_cancel_status_cancel_unknown": True,
                    "requires_m5_reconcile": True,
                    "venue_order_id_present": True,
                    **point_order_presence_predicate,
                    "point_order_terminal_no_fill": True,
                    "point_order_matched_size_zero": True,
                    "no_trade_facts": True,
                    "no_matching_open_orders": True,
                    "no_matching_trades": True,
                },
                "terminal_order_fact_id": fact_id,
                "terminal_order_fact": fact_payload,
                "resolved_m5_local_orphan_findings": resolved_findings,
                "venue_absence_proof": {
                    "source": "authenticated_clob_user_read",
                    "owner_scope": "authenticated_funder",
                    "observed_at": now,
                    "command_id": cmd.command_id,
                    "decision_id": str(command.get("decision_id") or ""),
                    "market_id": str(command.get("market_id") or ""),
                    "token_id": str(command.get("token_id") or ""),
                    "side": str(command.get("side") or ""),
                    "price": str(Decimal(str(command.get("price")))),
                    "size": str(Decimal(str(command.get("size")))),
                    "time_window_start": command.get("created_at"),
                    "time_window_end": now,
                    "open_orders_checked": True,
                    "trades_checked": True,
                    "open_orders_query_complete": True,
                    "trades_query_complete": True,
                    "pagination_scope": "sdk_get_trades_returned_all_visible_user_trades",
                    "matching_open_order_count": 0,
                    "matching_trade_count": 0,
                    "matching_open_orders": [],
                    "matching_trades": [],
                    "point_order_status": point_order_status,
                    "point_order": order,
                },
                "source_proof": {
                    "source_commit": "runtime",
                    "source_function": "command_recovery._review_required_cancel_unknown_live_order_recovery",
                    "source_reason": source_reason,
                },
                "review_required_proof": {
                    "reason": "cancel_unknown_requires_m5",
                },
                "reviewed_by": "command_recovery",
                "cleared_at": now,
            }
            append_event(
                conn,
                command_id=cmd.command_id,
                event_type=CommandEventType.REVIEW_CLEARED_NO_VENUE_EXPOSURE.value,
                occurred_at=now,
                payload=payload,
            )
            _append_entry_order_voided_projection(
                conn,
                command=command,
                order_fact={
                    **command,
                    "order_fact_id": fact_id,
                    "order_fact_state": "VENUE_WIPED",
                    "order_fact_observed_at": now,
                    "order_fact_venue_order_id": venue_order_id,
                    "order_fact_remaining_size": "0",
                    "order_fact_matched_size": "0",
                    "order_fact_source": "REST",
                },
                occurred_at=now,
            )
            conn.execute(f"RELEASE SAVEPOINT {sp_name}")
        except Exception:
            conn.execute(f"ROLLBACK TO SAVEPOINT {sp_name}")
            conn.execute(f"RELEASE SAVEPOINT {sp_name}")
            raise
        logger.info(
            "recovery: command %s REVIEW_REQUIRED cancel-unknown -> EXPIRED "
            "(venue_order_id=%s point_order_status=%s no_live_record=%s)",
            cmd.command_id,
            venue_order_id,
            point_order_status,
            point_order_no_live_record,
        )
        return "advanced"
    order = order or {}
    order_id = _extract_order_id(order)
    status = _order_status(order)
    matched_size = _order_matched_size(order)
    if order_id == venue_order_id and status in {"MATCHED", "FILLED"} and _is_positive_decimal(matched_size):
        trade = _confirmed_trade_for_order_id(client, venue_order_id)
        if trade is None:
            logger.info(
                "recovery: command %s REVIEW_REQUIRED cancel-unknown stayed "
                "(matched point order but confirmed trade fact not visible)",
                cmd.command_id,
            )
            return "stayed"
        command = _dict_row(
            conn.execute(
                "SELECT * FROM venue_commands WHERE command_id = ?",
                (cmd.command_id,),
            ).fetchone()
        )
        now = _now_iso()
        safe_command_id = "".join(ch if ch.isalnum() else "_" for ch in cmd.command_id)
        sp_name = f"sp_cancel_unknown_confirmed_fill_{safe_command_id}"
        conn.execute(f"SAVEPOINT {sp_name}")
        try:
            _append_cancel_unknown_confirmed_trade_fill(
                conn,
                command=command,
                point_order=order,
                trade=trade,
                observed_at=now,
            )
            conn.execute(f"RELEASE SAVEPOINT {sp_name}")
        except Exception:
            conn.execute(f"ROLLBACK TO SAVEPOINT {sp_name}")
            conn.execute(f"RELEASE SAVEPOINT {sp_name}")
            raise
        logger.info(
            "recovery: command %s REVIEW_REQUIRED cancel-unknown -> FILLED "
            "(venue_order_id=%s status=%s matched_size=%s)",
            cmd.command_id,
            venue_order_id,
            status,
            matched_size,
        )
        return "advanced"
    if (
        order_id != venue_order_id
        or status not in _LIVE_ORDER_STATUSES
        or _is_positive_decimal(matched_size)
    ):
        fact_state = _terminal_fact_state_for_venue_status(
            status,
            venue_resp_present=True,
        )
        if (
            order_id == venue_order_id
            and fact_state is not None
            and not _is_positive_decimal(
                _point_order_matched_size(order, fallback=matched_size, side=getattr(cmd, "side", None))
            )
            and _trade_fact_count(conn, cmd.command_id) == 0
        ):
            command = _dict_row(
                conn.execute(
                    "SELECT * FROM venue_commands WHERE command_id = ?",
                    (cmd.command_id,),
                ).fetchone()
            )
            if not _ensure_entry_projection_is_pending_zero_exposure(
                conn,
                command=command,
                order_id=venue_order_id,
            ):
                logger.info(
                    "recovery: command %s REVIEW_REQUIRED cancel-unknown stayed "
                    "(terminal no-fill point order but entry projection is not zero-exposure pending)",
                    cmd.command_id,
                )
                return "stayed"
            matching_open_orders = _matching_open_orders_for_command(client, command)
            matching_trades = _matching_trades_for_command(client, command)
            if not matching_open_orders and not matching_trades:
                now = _now_iso()
                safe_command_id = "".join(ch if ch.isalnum() else "_" for ch in cmd.command_id)
                sp_name = f"sp_cancel_unknown_no_fill_{safe_command_id}"
                conn.execute(f"SAVEPOINT {sp_name}")
                try:
                    fact_id, fact_payload = _append_point_order_terminal_no_fill_fact(
                        conn,
                        command=command,
                        observed_at=now,
                        venue_status=status,
                        point_order=order,
                        matching_open_orders=matching_open_orders,
                        matching_trades=matching_trades,
                        source_reason="cancel_unknown_point_order_terminal_no_fill",
                    )
                    resolved_findings = _resolve_m5_local_orphan_findings(
                        conn,
                        venue_order_id=venue_order_id,
                        resolved_at=now,
                        resolution="command_recovery_terminal_no_fill",
                    )
                    payload = {
                        "schema_version": 1,
                        "reason": "review_cleared_no_venue_exposure",
                        "command_id": cmd.command_id,
                        "venue_order_id": venue_order_id,
                        "proof_class": "cancel_unknown_terminal_no_fill",
                        "side_effect_boundary_crossed": "unknown",
                        "sdk_submit_attempted": "unknown",
                        "required_predicates": {
                            "latest_event_is_cancel_replace_blocked": True,
                            "semantic_cancel_status_cancel_unknown": True,
                            "requires_m5_reconcile": True,
                            "venue_order_id_present": True,
                            "venue_order_id_matches_point_read": True,
                            "point_order_terminal_no_fill": True,
                            "point_order_matched_size_zero": True,
                            "no_trade_facts": True,
                            "no_matching_open_orders": True,
                            "no_matching_trades": True,
                        },
                        "terminal_order_fact_id": fact_id,
                        "terminal_order_fact": fact_payload,
                        "resolved_m5_local_orphan_findings": resolved_findings,
                        "venue_absence_proof": {
                            "source": "authenticated_clob_user_read",
                            "owner_scope": "authenticated_funder",
                            "observed_at": now,
                            "command_id": cmd.command_id,
                            "decision_id": str(command.get("decision_id") or ""),
                            "market_id": str(command.get("market_id") or ""),
                            "token_id": str(command.get("token_id") or ""),
                            "side": str(command.get("side") or ""),
                            "price": str(Decimal(str(command.get("price")))),
                            "size": str(Decimal(str(command.get("size")))),
                            "time_window_start": command.get("created_at"),
                            "time_window_end": now,
                            "open_orders_checked": True,
                            "trades_checked": True,
                            "open_orders_query_complete": True,
                            "trades_query_complete": True,
                            "pagination_scope": "sdk_get_trades_returned_all_visible_user_trades",
                            "matching_open_order_count": 0,
                            "matching_trade_count": 0,
                            "matching_open_orders": [],
                            "matching_trades": [],
                            "point_order_status": status,
                            "point_order": order,
                        },
                        "source_proof": {
                            "source_commit": "runtime",
                            "source_function": "command_recovery._review_required_cancel_unknown_live_order_recovery",
                            "source_reason": "cancel_unknown_point_order_terminal_no_fill",
                        },
                        "review_required_proof": {
                            "reason": "cancel_unknown_requires_m5",
                        },
                        "reviewed_by": "command_recovery",
                        "cleared_at": now,
                    }
                    append_event(
                        conn,
                        command_id=cmd.command_id,
                        event_type=CommandEventType.REVIEW_CLEARED_NO_VENUE_EXPOSURE.value,
                        occurred_at=now,
                        payload=payload,
                    )
                    _append_entry_order_voided_projection(
                        conn,
                        command=command,
                        order_fact={
                            **command,
                            "order_fact_id": fact_id,
                            "order_fact_state": fact_state,
                            "order_fact_observed_at": now,
                            "order_fact_venue_order_id": venue_order_id,
                            "order_fact_remaining_size": "0",
                            "order_fact_matched_size": "0",
                            "order_fact_source": "REST",
                        },
                        occurred_at=now,
                    )
                    conn.execute(f"RELEASE SAVEPOINT {sp_name}")
                except Exception:
                    conn.execute(f"ROLLBACK TO SAVEPOINT {sp_name}")
                    conn.execute(f"RELEASE SAVEPOINT {sp_name}")
                    raise
                logger.info(
                    "recovery: command %s REVIEW_REQUIRED cancel-unknown -> EXPIRED "
                    "(venue_order_id=%s status=%s)",
                    cmd.command_id,
                    venue_order_id,
                    status,
                )
                return "advanced"
        logger.info(
            "recovery: command %s REVIEW_REQUIRED cancel-unknown stayed "
            "(point_order_id=%s status=%s matched_size=%s)",
            cmd.command_id,
            order_id,
            status or "UNKNOWN",
            matched_size,
        )
        return "stayed"
    now = _now_iso()
    payload = {
        "schema_version": 1,
        "reason": "review_cleared_venue_order_live",
        "command_id": cmd.command_id,
        "venue_order_id": venue_order_id,
        "proof_class": "cancel_unknown_venue_order_live",
        "side_effect_boundary_crossed": "unknown",
        "sdk_cancel_attempted": "unknown",
        "required_predicates": {
            "latest_event_is_cancel_replace_blocked": True,
            "semantic_cancel_status_cancel_unknown": True,
            "requires_m5_reconcile": True,
            "venue_order_id_present": True,
            "venue_order_id_matches_point_read": True,
            "point_order_status_live": True,
            "point_order_matched_size_not_positive": True,
            "no_trade_facts": _count_facts(conn, "venue_trade_facts", cmd.command_id) == 0,
        },
        "venue_order_live_proof": {
            "source": "authenticated_clob_point_order_read",
            "observed_at": now,
            "venue_order_id": venue_order_id,
            "point_order_status": status,
            "matched_size": matched_size,
            "point_order": order,
        },
        "source_proof": {
            "source_function": "command_recovery._reconcile_row",
            "source_reason": "cancel_unknown_venue_order_live",
        },
        "reviewed_by": "command_recovery",
        "cleared_at": now,
    }
    append_event(
        conn,
        command_id=cmd.command_id,
        event_type=CommandEventType.REVIEW_CLEARED_VENUE_ORDER_LIVE.value,
        occurred_at=now,
        payload=payload,
    )
    logger.info(
        "recovery: command %s REVIEW_REQUIRED cancel-unknown -> ACKED "
        "(venue_order_id=%s status=%s)",
        cmd.command_id,
        venue_order_id,
        status,
    )
    return "advanced"


def _latest_order_fact_for_command(
    conn: sqlite3.Connection,
    command_id: str,
) -> dict | None:
    if not _table_exists(conn, "venue_order_facts"):
        return None
    row = conn.execute(
        """
        SELECT *
          FROM venue_order_facts
         WHERE command_id = ?
         ORDER BY local_sequence DESC
         LIMIT 1
        """,
        (command_id,),
    ).fetchone()
    return _dict_row(row) if row is not None else None


def _order_fact_is_terminal_no_fill(fact: dict | None, venue_order_id: str) -> bool:
    if not fact:
        return False
    if str(fact.get("venue_order_id") or "") != str(venue_order_id or ""):
        return False
    if str(fact.get("state") or "") not in _TERMINAL_NO_FILL_ORDER_FACT_STATES:
        return False
    return _decimal_is_zero(fact.get("matched_size"))


def _no_positive_position_projection(conn: sqlite3.Connection, command: dict) -> bool:
    position_id = str(command.get("position_id") or "")
    if not position_id or not _table_exists(conn, "position_current"):
        return True
    row = conn.execute(
        """
        SELECT shares, cost_basis_usd
          FROM position_current
         WHERE position_id = ?
         LIMIT 1
        """,
        (position_id,),
    ).fetchone()
    if row is None:
        return True
    current = _dict_row(row)
    try:
        shares = Decimal(str(current.get("shares") or "0"))
        cost_basis = Decimal(str(current.get("cost_basis_usd") or "0"))
    except (InvalidOperation, TypeError, ValueError):
        return False
    return shares == Decimal("0") and cost_basis == Decimal("0")


def _review_required_post_ack_terminal_no_fill_recovery(
    conn: sqlite3.Connection,
    cmd: VenueCommand,
    client,
) -> str:
    events = _command_events(conn, cmd.command_id)
    latest_reason = _latest_review_required_payload(events).get("reason")
    if latest_reason not in _POST_ACK_PERSISTENCE_REVIEW_REASONS:
        return "stayed"
    venue_order_id = str(cmd.venue_order_id or "").strip()
    if not venue_order_id:
        return "stayed"
    command = _dict_row(
        conn.execute(
            "SELECT * FROM venue_commands WHERE command_id = ?",
            (cmd.command_id,),
        ).fetchone()
    )
    latest_fact = _latest_order_fact_for_command(conn, cmd.command_id)
    try:
        open_orders = [_raw_payload(order) for order in _client_open_orders(client)]
        trades = [_raw_payload(trade) for trade in _client_trades(client)]
    except Exception as exc:  # noqa: BLE001 - recovery should retry later on venue read failures.
        if (
            latest_fact is not None
            and str(latest_fact.get("venue_order_id") or "") == venue_order_id
            and str(latest_fact.get("state") or "").upper() in _LIVE_ORDER_STATUSES
            and _decimal_is_zero(latest_fact.get("matched_size"))
            and _trade_fact_count(conn, cmd.command_id) == 0
        ):
            logger.info(
                "recovery: command %s REVIEW_REQUIRED post-ACK account read failed; "
                "continuing with authenticated local live order fact: %s",
                cmd.command_id,
                exc,
            )
            open_orders = []
            trades = []
        else:
            logger.warning(
                "recovery: command %s REVIEW_REQUIRED post-ACK no-fill proof read failed: %s",
                cmd.command_id,
                exc,
            )
            return "error"

    matching_open_orders = _matching_open_orders_for_command(
        client,
        command,
        open_orders=open_orders,
    )
    matching_trades = _matching_trades_for_command(
        client,
        command,
        trades=trades,
    )
    if matching_trades:
        logger.info(
            "recovery: command %s REVIEW_REQUIRED post-ACK stayed "
            "(matching_open=%s matching_trades=%s)",
            cmd.command_id,
            len(matching_open_orders),
            len(matching_trades),
        )
        return "stayed"

    now = _now_iso()
    latest_fact = _latest_order_fact_for_command(conn, cmd.command_id)
    point_order: dict | None = None
    point_order_status = ""
    point_order_matched = "0"
    try:
        point_order = _venue_order_payload(client.get_order(venue_order_id)) or None
    except Exception:
        point_order = None
    if point_order:
        point_order_status = _order_status(point_order)
        point_order_matched = _point_order_matched_size(point_order, side=command.get("side"))

    latest_fact_is_live = (
        latest_fact is not None
        and str(latest_fact.get("venue_order_id") or "") == venue_order_id
        and str(latest_fact.get("state") or "").upper() in _LIVE_ORDER_STATUSES
        and _decimal_is_zero(latest_fact.get("matched_size"))
    )
    order_is_live = (
        bool(matching_open_orders)
        or latest_fact_is_live
        or str(point_order_status or "").upper() in _LIVE_ORDER_STATUSES
    )
    if (
        order_is_live
        and _decimal_is_zero(point_order_matched)
        and _trade_fact_count(conn, cmd.command_id) == 0
    ):
        payload = {
            "schema_version": 1,
            "reason": "review_cleared_venue_order_live",
            "command_id": cmd.command_id,
            "venue_order_id": venue_order_id,
            "proof_class": "acked_submit_venue_order_live",
            "side_effect_boundary_crossed": True,
            "sdk_submit_attempted": True,
            "required_predicates": {
                "latest_event_is_review_required": True,
                "review_reason_post_ack_persistence_failure": True,
                "venue_order_id_present": True,
                "venue_order_id_matches_live_proof": True,
                "authenticated_live_order_seen": True,
                "latest_order_fact_live": bool(latest_fact_is_live),
                "point_order_status_live": str(point_order_status or "").upper() in _LIVE_ORDER_STATUSES,
                "matching_open_order_seen": bool(matching_open_orders),
                "point_order_matched_size_not_positive": True,
                "no_trade_facts": True,
            },
            "venue_order_live_proof": {
                "source": "authenticated_clob_user_or_point_order_read",
                "owner_scope": "authenticated_funder",
                "observed_at": now,
                "venue_order_id": venue_order_id,
                "latest_order_fact": latest_fact,
                "matching_open_order_count": len(matching_open_orders),
                "matching_open_orders": matching_open_orders,
                "point_order_status": point_order_status,
                "point_order_matched_size": point_order_matched,
                "point_order": point_order,
            },
            "source_proof": {
                "source_commit": "runtime",
                "source_function": "command_recovery._reconcile_row",
                "source_reason": "acked_submit_venue_order_live",
            },
            "review_required_proof": {
                "reason": latest_reason,
            },
            "reviewed_by": "command_recovery",
            "cleared_at": now,
        }
        append_event(
            conn,
            command_id=cmd.command_id,
            event_type=CommandEventType.REVIEW_CLEARED_VENUE_ORDER_LIVE.value,
            occurred_at=now,
            payload=payload,
        )
        logger.info(
            "recovery: command %s REVIEW_REQUIRED post-ACK -> ACKED "
            "(venue_order_id=%s live order still present)",
            cmd.command_id,
            venue_order_id,
        )
        return "advanced"

    if point_order:
        fact_state = _terminal_fact_state_for_venue_status(
            point_order_status,
            venue_resp_present=True,
        )
        if (
            fact_state is not None
            and _decimal_is_zero(point_order_matched)
            and not _order_fact_is_terminal_no_fill(latest_fact, venue_order_id)
        ):
            fact_id, _fact_payload = _append_point_order_terminal_no_fill_fact(
                conn,
                command=command,
                observed_at=now,
                venue_status=point_order_status,
                point_order=point_order,
                matching_open_orders=matching_open_orders,
                matching_trades=matching_trades,
                source_reason="acked_submit_point_order_terminal_no_fill",
            )
            latest_fact = _latest_order_fact_for_command(conn, cmd.command_id)
            if latest_fact is not None:
                latest_fact["fact_id"] = latest_fact.get("fact_id") or fact_id

    if not _order_fact_is_terminal_no_fill(latest_fact, venue_order_id):
        logger.info(
            "recovery: command %s REVIEW_REQUIRED post-ACK stayed "
            "(latest order fact is not terminal no-fill)",
            cmd.command_id,
        )
        return "stayed"
    if _trade_fact_count(conn, cmd.command_id) != 0:
        logger.info(
            "recovery: command %s REVIEW_REQUIRED post-ACK stayed (trade facts exist)",
            cmd.command_id,
        )
        return "stayed"
    if not _no_positive_position_projection(conn, command):
        logger.info(
            "recovery: command %s REVIEW_REQUIRED post-ACK stayed "
            "(positive position projection exists)",
            cmd.command_id,
        )
        return "stayed"

    terminal_fact_id = latest_fact.get("fact_id")
    payload = {
        "schema_version": 1,
        "reason": "review_cleared_no_venue_exposure",
        "command_id": cmd.command_id,
        "venue_order_id": venue_order_id,
        "proof_class": "acked_submit_terminal_no_fill",
        "side_effect_boundary_crossed": True,
        "sdk_submit_attempted": True,
        "required_predicates": {
            "latest_event_is_review_required": True,
            "review_reason_post_ack_persistence_failure": True,
            "venue_order_id_present": True,
            "terminal_order_fact_latest": True,
            "terminal_order_fact_no_fill": True,
            "no_trade_facts": True,
            "no_matching_open_orders": True,
            "no_matching_trades": True,
            "no_positive_position_projection": True,
        },
        "terminal_order_fact_id": terminal_fact_id,
        "terminal_order_fact": {
            "venue_order_id": latest_fact.get("venue_order_id"),
            "state": latest_fact.get("state"),
            "matched_size": latest_fact.get("matched_size"),
            "remaining_size": latest_fact.get("remaining_size"),
            "source": latest_fact.get("source"),
            "observed_at": latest_fact.get("observed_at"),
            "local_sequence": latest_fact.get("local_sequence"),
        },
        "venue_absence_proof": {
            "source": "authenticated_clob_user_read",
            "owner_scope": "authenticated_funder",
            "observed_at": now,
            "command_id": cmd.command_id,
            "decision_id": str(command.get("decision_id") or ""),
            "market_id": str(command.get("market_id") or ""),
            "token_id": str(command.get("token_id") or ""),
            "side": str(command.get("side") or ""),
            "price": str(Decimal(str(command.get("price")))),
            "size": str(Decimal(str(command.get("size")))),
            "time_window_start": command.get("created_at"),
            "time_window_end": now,
            "open_orders_checked": True,
            "trades_checked": True,
            "open_orders_query_complete": True,
            "trades_query_complete": True,
            "pagination_scope": "sdk_get_trades_returned_all_visible_user_trades",
            "matching_open_order_count": 0,
            "matching_trade_count": 0,
            "matching_open_orders": [],
            "matching_trades": [],
            "point_order_status": point_order_status,
            "point_order_matched_size": point_order_matched,
            "point_order": point_order,
        },
        "source_proof": {
            "source_commit": "runtime",
            "source_function": "command_recovery._reconcile_row",
            "source_reason": "acked_submit_terminal_no_fill",
        },
        "review_required_proof": {
            "reason": latest_reason,
        },
        "reviewed_by": "command_recovery",
        "cleared_at": now,
    }
    safe_command_id = "".join(ch if ch.isalnum() else "_" for ch in cmd.command_id)
    sp_name = f"sp_post_ack_no_fill_{safe_command_id}"
    conn.execute(f"SAVEPOINT {sp_name}")
    try:
        append_event(
            conn,
            command_id=cmd.command_id,
            event_type=CommandEventType.REVIEW_CLEARED_NO_VENUE_EXPOSURE.value,
            occurred_at=now,
            payload=payload,
        )
        try:
            current = _position_current_for_terminal_order(
                conn,
                command=command,
                order_id=venue_order_id,
            )
        except (MissingPositionCurrentForTerminalOrder, ValueError):
            current = None
        if (
            current is not None
            and str(current.get("phase") or "") == "pending_entry"
            and _decimal_is_zero(current.get("shares"))
            and _decimal_is_zero(current.get("cost_basis_usd"))
        ):
            _append_entry_order_voided_projection(
                conn,
                command=command,
                order_fact={
                    **command,
                    "order_fact_id": terminal_fact_id,
                    "order_fact_state": latest_fact.get("state"),
                    "order_fact_observed_at": latest_fact.get("observed_at") or now,
                    "order_fact_venue_order_id": venue_order_id,
                    "order_fact_remaining_size": latest_fact.get("remaining_size") or "0",
                    "order_fact_matched_size": latest_fact.get("matched_size") or "0",
                    "order_fact_source": latest_fact.get("source") or "REST",
                },
                occurred_at=now,
            )
        conn.execute(f"RELEASE SAVEPOINT {sp_name}")
    except Exception:
        conn.execute(f"ROLLBACK TO SAVEPOINT {sp_name}")
        conn.execute(f"RELEASE SAVEPOINT {sp_name}")
        raise
    logger.info(
        "recovery: command %s REVIEW_REQUIRED post-ACK -> EXPIRED "
        "(venue_order_id=%s terminal_fact=%s)",
        cmd.command_id,
        venue_order_id,
        terminal_fact_id,
    )
    return "advanced"


def _geoblock_403_predicates(
    conn: sqlite3.Connection,
    command: dict,
) -> tuple[dict, list[str]]:
    command_id = str(command["command_id"])
    events = _command_events(conn, command_id)
    latest_event_type, latest_payload = _latest_event_payload(events)
    payloads = [_json_dict(event.get("payload_json")) for event in events]
    final_envelope_ids = [
        payload.get("final_submission_envelope_id")
        for payload in payloads
        if str(payload.get("final_submission_envelope_id") or "").strip()
    ]
    envelope = _command_envelope(conn, command.get("envelope_id"))
    position_counts = _count_position_rows_for_command(conn, command)
    exception_message = str(latest_payload.get("exception_message") or "")
    predicates = {
        "latest_event_is_submit_timeout_unknown": (
            latest_event_type == CommandEventType.SUBMIT_TIMEOUT_UNKNOWN.value
        ),
        "payload_reason_post_submit_exception": (
            latest_payload.get("reason") == "post_submit_exception_possible_side_effect"
        ),
        "exception_type_polyapi": latest_payload.get("exception_type") == "PolyApiException",
        "exception_message_geoblock_403": all(
            marker in exception_message for marker in _GEOBLOCK_403_MARKERS
        ),
        "no_venue_order_id": not str(command.get("venue_order_id") or "").strip(),
        "no_final_submission_envelope": not final_envelope_ids,
        "no_envelope_order_id": not str(envelope.get("order_id") or "").strip(),
        "no_raw_response": not str(envelope.get("raw_response_json") or "").strip(),
        "no_signed_order": (
            envelope.get("signed_order_blob") in (None, b"", "")
            and not str(envelope.get("signed_order_hash") or "").strip()
        ),
        "no_order_facts": _count_facts(conn, "venue_order_facts", command_id) == 0,
        "no_trade_facts": _count_facts(conn, "venue_trade_facts", command_id) == 0,
        "no_position_events": position_counts["position_events"] == 0,
        "no_position_current": position_counts["position_current"] == 0,
    }
    failures = [name for name, ok in predicates.items() if not ok]
    return predicates, failures


def _invalid_amount_400_predicates(
    conn: sqlite3.Connection,
    command: dict,
) -> tuple[dict, list[str]]:
    command_id = str(command["command_id"])
    events = _command_events(conn, command_id)
    latest_event_type, latest_payload = _latest_event_payload(events)
    payloads = [_json_dict(event.get("payload_json")) for event in events]
    final_envelope_ids = [
        payload.get("final_submission_envelope_id")
        for payload in payloads
        if str(payload.get("final_submission_envelope_id") or "").strip()
    ]
    envelope = _command_envelope(conn, command.get("envelope_id"))
    position_counts = _count_position_rows_for_command(conn, command)
    exception_message = str(latest_payload.get("exception_message") or "")
    from src.execution.executor import _is_polymarket_invalid_amount_400_message

    predicates = {
        "latest_event_is_submit_timeout_unknown": (
            latest_event_type == CommandEventType.SUBMIT_TIMEOUT_UNKNOWN.value
        ),
        "payload_reason_post_submit_exception": (
            latest_payload.get("reason") == "post_submit_exception_possible_side_effect"
        ),
        "exception_type_polyapi": latest_payload.get("exception_type") == "PolyApiException",
        "exception_message_invalid_amount_400": (
            _is_polymarket_invalid_amount_400_message(exception_message)
        ),
        "no_venue_order_id": not str(command.get("venue_order_id") or "").strip(),
        "no_final_submission_envelope": not final_envelope_ids,
        "no_envelope_order_id": not str(envelope.get("order_id") or "").strip(),
        "no_raw_response": not str(envelope.get("raw_response_json") or "").strip(),
        "no_signed_order": (
            envelope.get("signed_order_blob") in (None, b"", "")
            and not str(envelope.get("signed_order_hash") or "").strip()
        ),
        "no_order_facts": _count_facts(conn, "venue_order_facts", command_id) == 0,
        "no_trade_facts": _count_facts(conn, "venue_trade_facts", command_id) == 0,
        "no_position_events": position_counts["position_events"] == 0,
        "no_position_current": position_counts["position_current"] == 0,
    }
    failures = [name for name, ok in predicates.items() if not ok]
    return predicates, failures


def _terminalize_submit_unknown_invalid_amount_400_if_proven(
    conn: sqlite3.Connection,
    command: dict,
    *,
    occurred_at: str,
) -> dict | None:
    if not command:
        return None
    predicates, predicate_failures = _invalid_amount_400_predicates(conn, command)
    if predicate_failures:
        return None
    command_id = str(command["command_id"])
    payload = {
        "schema_version": 1,
        "reason": "venue_rejected_invalid_amount_400",
        "command_id": command_id,
        "decision_id": str(command.get("decision_id") or ""),
        "proof_class": "deterministic_venue_invalid_amount_400",
        "side_effect_boundary_crossed": True,
        "venue_order_created": False,
        "required_predicates": predicates,
        "idempotency_key": str(command.get("idempotency_key") or ""),
    }
    append_event(
        conn,
        command_id=command_id,
        event_type=CommandEventType.SUBMIT_REJECTED.value,
        occurred_at=occurred_at,
        payload=payload,
    )
    _reconcile_edli_pending_no_order_if_proven(
        conn,
        execution_command_id=str(command.get("decision_id") or ""),
        occurred_at=occurred_at,
        reason="venue_rejected_invalid_amount_400",
        proof_class="deterministic_venue_invalid_amount_400",
        command_id=command_id,
        required_predicates=predicates,
    )
    return payload


# =============================================================================
# Abandoned never-submitted EDLI ghost reconcile (live_order_pathology 2026-06-22)
# =============================================================================
# A live decision can build an EDLI order aggregate all the way to
# ``ExecutionCommandCreated`` (the executor accepted the command INTERNALLY) and
# then be interrupted — daemon restart / SQLite lock — BEFORE the venue submit.
# The aggregate then stalls with NO subsequent event: no VenueSubmitAttempted, no
# SubmitRejected, no SubmitUnknown, no ack, no user event, no Reconciled. Its
# ``edli_live_order_projection.venue_order_id`` is NULL and there is ZERO
# ``venue_commands`` row for its execution_command_id — i.e. the order NEVER
# reached the venue and has $0 capital at risk.
#
# Such a ghost is NON-TERMINAL per ``event_reactor_adapter._TERMINAL_EVENT_SQL``,
# so ``_locked_live_opportunity_active_order_reason`` treats it as an ACTIVE order
# and permanently SUPPRESSES every new submit on the same weather family (the
# duplicate-suppression lock has no direction filter — a stuck buy_YES ghost
# blocks a live buy_NO on the same family forever). No other reconcile path
# terminalizes it: the SUBMIT_UNKNOWN reconcile (_reconcile_edli_pending_no_order_if_proven)
# requires ``pending_reconcile=1`` (a SubmitUnknown the ghost never reached), and
# ``append_reconciled`` requires venue-reconcile truth a never-submitted order
# has none of.
#
# The LEGAL terminalization (confirmed in live_order_aggregate._validate_event_append
# for event_type=="SubmitRejected") is a PRE-SUBMIT SubmitRejected appended directly
# after ExecutionCommandCreated WITHOUT a VenueSubmitAttempted: it is accepted iff
# ``_is_pre_submit_rejection_payload(payload)`` is True (pre_submit_rejection=True,
# submit_status="PRE_SUBMIT_ERROR", venue_call_started=False), the command-binding
# fields match the ExecutionCommandCreated event, and a non-empty reason_code is set.
# A SubmitRejected event makes the aggregate TERMINAL per _TERMINAL_EVENT_SQL, so the
# family duplicate lock RELEASES and the family re-enters the normal decision pipeline
# (still fully re-certified downstream — no gate bypassed).
#
# VENUE-TRUTH GUARD (money-path, fail-closed): a ghost is terminalized ONLY when
# the CURRENT command has NO venue presence whatsoever — venue_order_id IS NULL
# in the projection, NO venue_commands row for its execution_command_id, and
# NONE of {VenueSubmitAttempted, VenueSubmitAcknowledged, SubmitUnknown,
# UserOrderObserved, UserTradeObserved, SubmitRejected, Reconciled,
# CapTransitioned} events exist AFTER the current ExecutionCommandCreated. Older
# rejected attempts on the same aggregate do not prove venue presence for the
# current command. A real resting / filled / in-flight order is NEVER
# terminalized.

# The same safe-replay grace the SUBMIT_UNKNOWN path uses: an aggregate is only a
# terminalizable ghost once it has sat at ExecutionCommandCreated for longer than
# this window (a crash mid-cycle is normal; we never race a still-completing submit).
_ABANDONED_GHOST_GRACE_SECONDS = _SAFE_REPLAY_MIN_AGE_SECONDS

# Events that prove the aggregate progressed past the never-submitted boundary (any
# venue contact, any user-channel fact, or any later terminal/cap event). If ANY of
# these exist on the aggregate it is NOT a never-submitted ghost — fail closed.
_GHOST_DISQUALIFYING_EVENT_TYPES = (
    "VenueSubmitAttempted",
    "VenueSubmitAcknowledged",
    "SubmitUnknown",
    "UserOrderObserved",
    "UserTradeObserved",
    "SubmitRejected",
    "Reconciled",
    "CapTransitioned",
)


def _abandoned_unsubmitted_ghost_candidates(
    conn: sqlite3.Connection,
    *,
    events_ref: str,
    projection_ref: str,
    cutoff_iso: str,
) -> list[dict]:
    """Aggregates stalled at ExecutionCommandCreated that never reached the venue.

    Returns one row per ghost with its aggregate_id + execution_command_id (read
    from the latest ExecutionCommandCreated event payload). Strictly read-only.

    Predicates (ALL must hold):
      * projection current_state = EXECUTION_COMMAND_CREATED and last_event_type =
        ExecutionCommandCreated, with the joined command row matching
        projection.last_sequence (the aggregate's CURRENT event is the command);
      * projection.venue_order_id IS NULL;
      * the ExecutionCommandCreated event occurred before ``cutoff_iso`` (grace);
      * no disqualifying (venue / user / later-terminal / cap) event exists after
        the current command row — defense-in-depth even though current_state
        already implies it;
      * no venue_commands row links to the execution_command_id (decision_id key).
    """
    if not _table_exists(conn, "venue_commands"):
        logger.warning(
            "recovery: abandoned-unsubmitted-ghost pass skipped because "
            "venue_commands is not visible on this connection"
        )
        return []
    disqualifier_placeholders = ",".join("?" for _ in _GHOST_DISQUALIFYING_EVENT_TYPES)
    rows = conn.execute(
        f"""
        SELECT proj.aggregate_id AS aggregate_id,
               json_extract(cmd_ev.payload_json, '$.execution_command_id') AS execution_command_id,
               cmd_ev.occurred_at AS command_occurred_at
        FROM {projection_ref} proj
        JOIN {events_ref} cmd_ev
          ON cmd_ev.aggregate_id = proj.aggregate_id
         AND cmd_ev.event_type = 'ExecutionCommandCreated'
         AND cmd_ev.event_sequence = proj.last_sequence
        WHERE proj.current_state = 'EXECUTION_COMMAND_CREATED'
          AND proj.last_event_type = 'ExecutionCommandCreated'
          AND COALESCE(proj.venue_order_id, '') = ''
          AND cmd_ev.occurred_at < ?
          AND NOT EXISTS (
              SELECT 1 FROM {events_ref} later
              WHERE later.aggregate_id = proj.aggregate_id
                AND later.event_type IN ({disqualifier_placeholders})
                AND later.event_sequence > cmd_ev.event_sequence
          )
        ORDER BY cmd_ev.occurred_at, proj.aggregate_id
        """,
        (cutoff_iso, *_GHOST_DISQUALIFYING_EVENT_TYPES),
    ).fetchall()
    candidates: list[dict] = []
    for row in rows:
        record = _dict_row(row)
        execution_command_id = str(record.get("execution_command_id") or "").strip()
        if not execution_command_id:
            # An ExecutionCommandCreated with no execution_command_id cannot be
            # safely cap-released or command-bound; skip (fail closed).
            continue
        # VENUE-TRUTH GUARD: any venue_commands row for this execution_command_id
        # means the order reached the command bus — never terminalize.
        venue_cmd = conn.execute(
            "SELECT 1 FROM venue_commands WHERE decision_id = ? LIMIT 1",
            (execution_command_id,),
        ).fetchone()
        if venue_cmd is not None:
            continue
        record["execution_command_id"] = execution_command_id
        candidates.append(record)
    return candidates


def _terminalize_abandoned_unsubmitted_ghost(
    conn: sqlite3.Connection,
    *,
    events_ref: str,
    projection_ref: str,
    cap_usage_ref: str,
    day_slots_ref: str | None,
    rate_window_ref: str | None,
    aggregate_id: str,
    execution_command_id: str,
    occurred_at: str,
) -> bool:
    """Append a PRE-SUBMIT SubmitRejected terminal + release the cap reservation.

    Returns True when the ghost was terminalized, False when a final guard
    (re-read venue truth) refuses. Caller wraps this in a SAVEPOINT.
    """
    # Re-read the ExecutionCommandCreated event for the exact command-binding
    # fields (event_id / final_intent_id / execution_command_id) the
    # _require_command_binding check enforces. Fail closed if absent.
    command_event = _latest_edli_event(conn, events_ref, aggregate_id, "ExecutionCommandCreated")
    command_payload = _edli_payload(command_event)
    event_id = str(command_payload.get("event_id") or "").strip()
    final_intent_id = str(command_payload.get("final_intent_id") or "").strip()
    bound_command_id = str(command_payload.get("execution_command_id") or "").strip()
    if not event_id or not final_intent_id or bound_command_id != execution_command_id:
        return False

    # FINAL venue-truth guard (re-read the projection under the write lock): the
    # latest event must still be ExecutionCommandCreated and venue_order_id NULL.
    projection = conn.execute(
        f"""
        SELECT current_state, last_event_type, venue_order_id
        FROM {projection_ref}
        WHERE aggregate_id = ?
        """,
        (aggregate_id,),
    ).fetchone()
    if projection is None:
        return False
    if str(projection["current_state"]) != "EXECUTION_COMMAND_CREATED":
        return False
    if str(projection["last_event_type"] or "") != "ExecutionCommandCreated":
        return False
    if str(projection["venue_order_id"] or "").strip():
        return False

    # The PRE-SUBMIT SubmitRejected payload the immutable ledger accepts directly
    # after ExecutionCommandCreated (no VenueSubmitAttempted required), per
    # live_order_aggregate._validate_event_append + _is_pre_submit_rejection_payload
    # + _require_command_binding. reason_code is mandatory.
    submit_rejected_payload = {
        "schema_version": 1,
        "event_id": event_id,
        "final_intent_id": final_intent_id,
        "execution_command_id": execution_command_id,
        "execution_receipt_hash": str(command_payload.get("execution_receipt_hash") or ""),
        "reason_code": "ABANDONED_UNSUBMITTED_GHOST_RECONCILE",
        "submit_status": "PRE_SUBMIT_ERROR",
        "venue_call_started": False,
        "venue_ack_received": False,
        "pre_submit_rejection": True,
        "proof_class": "command_created_never_submitted_no_venue_presence",
        "required_predicates": {
            "current_event_is_execution_command_created": True,
            "no_venue_submit_attempt": True,
            "no_venue_order_id": True,
            "no_venue_commands_row": True,
            "aged_past_grace_seconds": _ABANDONED_GHOST_GRACE_SECONDS,
        },
        "reviewed_by": "command_recovery",
        "cleared_at": occurred_at,
    }
    _append_edli_event_qualified(
        conn,
        events_ref=events_ref,
        aggregate_id=aggregate_id,
        event_type="SubmitRejected",
        payload=submit_rejected_payload,
        occurred_at=occurred_at,
        source_authority="existing_executor",
    )

    # Release the still-RESERVED cap reservation keyed by execution_command_id.
    cap = conn.execute(
        f"""
        SELECT usage_id, reservation_status
        FROM {cap_usage_ref}
        WHERE execution_command_id = ?
        ORDER BY created_at DESC
        LIMIT 1
        """,
        (execution_command_id,),
    ).fetchone()
    if cap is not None and str(cap["reservation_status"]) == "RESERVED":
        usage_id = str(cap["usage_id"])
        conn.execute(
            f"UPDATE {cap_usage_ref} SET reservation_status = 'RELEASED' WHERE usage_id = ?",
            (usage_id,),
        )
        if day_slots_ref:
            conn.execute(f"DELETE FROM {day_slots_ref} WHERE usage_id = ?", (usage_id,))
        if rate_window_ref:
            conn.execute(f"DELETE FROM {rate_window_ref} WHERE usage_id = ?", (usage_id,))

    _rebuild_edli_projection_qualified(
        conn,
        events_ref=events_ref,
        projection_ref=projection_ref,
        aggregate_id=aggregate_id,
    )
    return True


def reconcile_abandoned_unsubmitted_ghosts(
    conn: sqlite3.Connection,
    *,
    updated_before: str | None = None,
) -> dict[str, int]:
    """Terminalize EDLI order aggregates abandoned at ExecutionCommandCreated.

    THE GHOST CLASS THIS KILLS (live_order_pathology 2026-06-22): an aggregate
    that reached ExecutionCommandCreated and was then interrupted before the venue
    submit. It is non-terminal, so the duplicate-suppression family lock
    (event_reactor_adapter._locked_live_opportunity_active_order_reason) suppresses
    every new submit on the same weather family forever — even an opposite-direction
    trade — while $0 sits at the venue.

    For each such ghost (after a grace window, with NO venue presence) this appends
    a PRE-SUBMIT SubmitRejected — the legal terminal directly after
    ExecutionCommandCreated, accepted by the immutable ledger's _validate_event_append
    — and releases its still-RESERVED cap reservation. That makes the aggregate
    TERMINAL per _TERMINAL_EVENT_SQL, releasing the family lock so the next decision
    cycle re-enters the family through the full (un-bypassed) certification pipeline.

    Wired into both recovery lanes (boot / periodic) so a daemon restart self-heals
    any ghost it created on its way down. DB-only pass; never calls the venue.
    Returns {"scanned", "advanced", "stayed", "errors"}.
    """
    summary = {"scanned": 0, "advanced": 0, "stayed": 0, "errors": 0}
    events_ref = _edli_live_order_events_ref(conn)
    projection_ref = _edli_live_order_projection_ref(conn)
    cap_usage_ref = _edli_live_cap_ref(conn, "edli_live_cap_usage")
    if not events_ref or not projection_ref or not cap_usage_ref:
        return summary
    day_slots_ref = _edli_live_cap_ref(conn, "edli_live_cap_day_slots")
    rate_window_ref = _edli_live_cap_ref(conn, "edli_live_cap_rate_window")

    now = datetime.now(timezone.utc)
    explicit_cutoff = _parse_ts(updated_before) if updated_before else None
    grace_cutoff = now - timedelta(seconds=_ABANDONED_GHOST_GRACE_SECONDS)
    # Honor an explicit caller cutoff only when it is at least as strict (no later
    # than) the grace cutoff — we never terminalize a ghost younger than the grace.
    cutoff = min(explicit_cutoff, grace_cutoff) if explicit_cutoff is not None else grace_cutoff
    cutoff_iso = cutoff.astimezone(timezone.utc).isoformat()

    candidates = _abandoned_unsubmitted_ghost_candidates(
        conn,
        events_ref=events_ref,
        projection_ref=projection_ref,
        cutoff_iso=cutoff_iso,
    )
    occurred_at = _now_iso()
    for candidate in candidates:
        summary["scanned"] += 1
        aggregate_id = str(candidate.get("aggregate_id") or "")
        execution_command_id = str(candidate.get("execution_command_id") or "")
        conn.execute("SAVEPOINT abandoned_unsubmitted_ghost_reconcile")
        try:
            advanced = _terminalize_abandoned_unsubmitted_ghost(
                conn,
                events_ref=events_ref,
                projection_ref=projection_ref,
                cap_usage_ref=cap_usage_ref,
                day_slots_ref=day_slots_ref,
                rate_window_ref=rate_window_ref,
                aggregate_id=aggregate_id,
                execution_command_id=execution_command_id,
                occurred_at=occurred_at,
            )
            conn.execute("RELEASE SAVEPOINT abandoned_unsubmitted_ghost_reconcile")
            if advanced:
                summary["advanced"] += 1
            else:
                summary["stayed"] += 1
        except Exception as exc:
            conn.execute("ROLLBACK TO SAVEPOINT abandoned_unsubmitted_ghost_reconcile")
            conn.execute("RELEASE SAVEPOINT abandoned_unsubmitted_ghost_reconcile")
            logger.error(
                "recovery: abandoned-unsubmitted-ghost reconcile failed for %s (cmd=%s): %s",
                aggregate_id,
                execution_command_id,
                exc,
            )
            summary["errors"] += 1
    return summary


def reconcile_stale_intent_created_no_submit(
    conn: sqlite3.Connection,
    *,
    updated_before: str | None = None,
) -> dict[str, int]:
    """Terminalize pre-submit command shells that never crossed the venue boundary.

    A crash/SQLite lock can occur after ``insert_command`` appends INTENT_CREATED
    but before SUBMIT_REQUESTED is appended or any venue call is made.  Such a
    row is not an unresolved venue side effect, but leaving it active misleads
    operators and downstream projections.  The predicates here are intentionally
    local and strict: no submit event, no venue order id, no order/trade facts,
    and no position projection.
    """

    summary = {"scanned": 0, "advanced": 0, "stayed": 0, "errors": 0}
    cutoff = str(updated_before or _now_iso())
    rows = conn.execute(
        """
        SELECT cmd.*
          FROM venue_commands cmd
         WHERE cmd.state = 'INTENT_CREATED'
           AND COALESCE(cmd.venue_order_id, '') = ''
           AND cmd.updated_at < ?
           AND NOT EXISTS (
                SELECT 1
                  FROM venue_command_events ev
                 WHERE ev.command_id = cmd.command_id
                   AND ev.event_type != 'INTENT_CREATED'
           )
           AND NOT EXISTS (
                SELECT 1 FROM venue_order_facts fact
                 WHERE fact.command_id = cmd.command_id
           )
           AND NOT EXISTS (
                SELECT 1 FROM venue_trade_facts fact
                 WHERE fact.command_id = cmd.command_id
           )
           AND NOT EXISTS (
                SELECT 1 FROM position_current pc
                 WHERE pc.position_id = cmd.position_id
           )
         ORDER BY cmd.updated_at, cmd.command_id
        """,
        (cutoff,),
    ).fetchall()
    summary["scanned"] = len(rows)
    for row in rows:
        command = _dict_row(row)
        command_id = str(command.get("command_id") or "")
        try:
            payload = {
                "schema_version": 1,
                "reason": "pre_venue_intent_abandoned_before_submit",
                "command_id": command_id,
                "decision_id": str(command.get("decision_id") or ""),
                "proof_class": "local_command_journal_no_submit_boundary",
                "side_effect_boundary_crossed": False,
                "venue_order_created": False,
                "safe_replay_permitted": True,
                "required_predicates": {
                    "state_is_intent_created": True,
                    "no_submit_requested_event": True,
                    "no_venue_order_id": True,
                    "no_order_facts": True,
                    "no_trade_facts": True,
                    "no_position_current": True,
                    "updated_before_recovery_started_at": cutoff,
                },
                "reviewed_by": "command_recovery",
                "cleared_at": cutoff,
            }
            append_event(
                conn,
                command_id=command_id,
                event_type=CommandEventType.SUBMIT_REJECTED.value,
                occurred_at=cutoff,
                payload=payload,
            )
            summary["advanced"] += 1
        except Exception:
            summary["errors"] += 1
            logger.exception(
                "recovery: stale INTENT_CREATED terminalization failed for command %s",
                command_id,
            )
    return summary


def _latest_edli_event(conn: sqlite3.Connection, events_ref: str, aggregate_id: str, event_type: str | None = None) -> dict:
    if event_type is None:
        row = conn.execute(
            f"""
            SELECT *
            FROM {events_ref}
            WHERE aggregate_id = ?
            ORDER BY event_sequence DESC
            LIMIT 1
            """,
            (aggregate_id,),
        ).fetchone()
    else:
        row = conn.execute(
            f"""
            SELECT *
            FROM {events_ref}
            WHERE aggregate_id = ? AND event_type = ?
            ORDER BY event_sequence DESC
            LIMIT 1
            """,
            (aggregate_id, event_type),
        ).fetchone()
    return _dict_row(row)


def _edli_payload(row: Mapping[str, object] | None) -> dict:
    if not row:
        return {}
    return _json_dict(_dict_row(row).get("payload_json"))


def _append_edli_event_qualified(
    conn: sqlite3.Connection,
    *,
    events_ref: str,
    aggregate_id: str,
    event_type: str,
    payload: dict,
    occurred_at: str,
    source_authority: str,
) -> dict:
    latest = _latest_edli_event(conn, events_ref, aggregate_id)
    if not latest:
        raise ValueError("EDLI recovery requires existing aggregate event")
    parent_hash = str(latest.get("event_hash") or "")
    next_sequence = int(latest.get("event_sequence") or 0) + 1
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
            "occurred_at": occurred_at,
        }
    )
    aggregate_event_id = "edli_live_order_event:" + event_hash[:32]
    conn.execute(
        f"""
        INSERT INTO {events_ref} (
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
            occurred_at,
            _now_iso(),
        ),
    )
    return {
        "aggregate_event_id": aggregate_event_id,
        "event_hash": event_hash,
        "event_sequence": next_sequence,
    }


def _rebuild_edli_projection_qualified(
    conn: sqlite3.Connection,
    *,
    events_ref: str,
    projection_ref: str,
    aggregate_id: str,
) -> None:
    rows = conn.execute(
        f"""
        SELECT *
        FROM {events_ref}
        WHERE aggregate_id = ?
        ORDER BY event_sequence ASC
        """,
        (aggregate_id,),
    ).fetchall()
    if not rows:
        raise ValueError("cannot rebuild EDLI projection for empty aggregate")
    event_id = str(_edli_payload(rows[0]).get("event_id") or "")
    final_intent_id = None
    venue_order_id = None
    pending_reconcile = False
    current_state = "UNKNOWN"
    state_by_type = {
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
    for row in rows:
        payload = _edli_payload(row)
        if payload.get("final_intent_id") is not None:
            final_intent_id = str(payload["final_intent_id"])
        if payload.get("venue_order_id") is not None:
            venue_order_id = str(payload["venue_order_id"])
        current_event_type = str(row["event_type"])
        if current_event_type == "SubmitUnknown":
            current_state = "PENDING_RECONCILE"
            pending_reconcile = True
        elif current_event_type == "CapTransitioned" and str(payload.get("to_status") or "") == "PENDING_RECONCILE":
            current_state = "PENDING_RECONCILE"
            pending_reconcile = True
        elif current_event_type == "Reconciled":
            current_state = "RECONCILED"
            pending_reconcile = bool(payload.get("pending_reconcile", False))
        else:
            current_state = state_by_type.get(current_event_type, current_event_type)
    last = rows[-1]
    conn.execute(
        f"""
        INSERT INTO {projection_ref} (
            aggregate_id, event_id, final_intent_id, current_state,
            last_sequence, last_event_type, last_event_hash,
            pending_reconcile, venue_order_id, updated_at, schema_version
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
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
            schema_version = excluded.schema_version
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
            _now_iso(),
        ),
    )


def _reconcile_edli_pending_no_order_if_proven(
    conn: sqlite3.Connection,
    *,
    execution_command_id: str,
    occurred_at: str,
    reason: str,
    proof_class: str,
    command_id: str | None = None,
    required_predicates: Mapping[str, object] | None = None,
) -> bool:
    if not execution_command_id:
        return False
    events_ref = _edli_live_order_events_ref(conn)
    projection_ref = _edli_live_order_projection_ref(conn)
    cap_usage_ref = _edli_live_cap_ref(conn, "edli_live_cap_usage")
    day_slots_ref = _edli_live_cap_ref(conn, "edli_live_cap_day_slots")
    rate_window_ref = _edli_live_cap_ref(conn, "edli_live_cap_rate_window")
    if not events_ref or not projection_ref or not cap_usage_ref:
        return False
    row = conn.execute(
        f"""
        SELECT aggregate_id, payload_json
        FROM {events_ref}
        WHERE event_type = 'SubmitUnknown'
          AND json_extract(payload_json, '$.execution_command_id') = ?
        ORDER BY event_sequence DESC
        LIMIT 1
        """,
        (execution_command_id,),
    ).fetchone()
    if row is None:
        return False
    aggregate_id = str(row["aggregate_id"])
    submit_unknown_payload = _json_dict(row["payload_json"])
    if submit_unknown_payload.get("side_effect_known") is True:
        return False
    unsafe_count = conn.execute(
        f"""
        SELECT COUNT(*)
        FROM {events_ref}
        WHERE aggregate_id = ?
          AND event_type IN ('VenueSubmitAcknowledged', 'UserOrderObserved', 'UserTradeObserved')
        """,
        (aggregate_id,),
    ).fetchone()[0]
    if int(unsafe_count) != 0:
        return False
    projection = conn.execute(
        f"""
        SELECT pending_reconcile, venue_order_id
        FROM {projection_ref}
        WHERE aggregate_id = ?
        """,
        (aggregate_id,),
    ).fetchone()
    if projection is None or not bool(projection["pending_reconcile"]):
        return False
    if str(projection["venue_order_id"] or "").strip():
        return False
    cap = conn.execute(
        f"""
        SELECT usage_id, event_id, final_intent_id, reservation_status
        FROM {cap_usage_ref}
        WHERE execution_command_id = ?
        ORDER BY created_at DESC
        LIMIT 1
        """,
        (execution_command_id,),
    ).fetchone()
    if cap is None or str(cap["reservation_status"]) != "RESERVED":
        return False
    usage_id = str(cap["usage_id"])
    event_id = str(cap["event_id"])
    final_intent_id = str(cap["final_intent_id"] or submit_unknown_payload.get("final_intent_id") or "")
    reconcile_payload = {
        "schema_version": 1,
        "event_id": event_id,
        "final_intent_id": final_intent_id,
        "source_authority": "venue_reconcile",
        "pending_reconcile": False,
        "venue_order_exists": False,
        "cap_transition_recommendation": "RELEASED",
        "reason": reason,
        "proof_class": proof_class,
        "execution_command_id": execution_command_id,
        "command_id": command_id,
        "required_predicates": dict(required_predicates or {}),
    }
    reconciled = _append_edli_event_qualified(
        conn,
        events_ref=events_ref,
        aggregate_id=aggregate_id,
        event_type="Reconciled",
        payload=reconcile_payload,
        occurred_at=occurred_at,
        source_authority="explicit_reconcile",
    )
    conn.execute(
        f"UPDATE {cap_usage_ref} SET reservation_status = 'RELEASED' WHERE usage_id = ?",
        (usage_id,),
    )
    if day_slots_ref:
        conn.execute(f"DELETE FROM {day_slots_ref} WHERE usage_id = ?", (usage_id,))
    if rate_window_ref:
        conn.execute(f"DELETE FROM {rate_window_ref} WHERE usage_id = ?", (usage_id,))
    _append_edli_event_qualified(
        conn,
        events_ref=events_ref,
        aggregate_id=aggregate_id,
        event_type="CapTransitioned",
        payload={
            "schema_version": 1,
            "event_id": event_id,
            "final_intent_id": final_intent_id,
            "execution_command_id": execution_command_id,
            "execution_receipt_hash": str(submit_unknown_payload.get("execution_receipt_hash") or reconciled["event_hash"]),
            "to_status": "RELEASED",
            "projection_status": "RELEASED",
            "transition_reason": reason,
            "reconciled_event_hash": reconciled["event_hash"],
        },
        occurred_at=occurred_at,
        source_authority="live_cap_ledger",
    )
    _rebuild_edli_projection_qualified(
        conn,
        events_ref=events_ref,
        projection_ref=projection_ref,
        aggregate_id=aggregate_id,
    )
    return True


# Terminal CommandEventType used to discharge an unresolved venue_commands row
# once the event-sourced EDLI ledger has AUTHENTICATED-ABSENCE-PROVEN that the
# venue holds no open order and no trade for the command's token. The submit
# side-effect boundary WAS crossed (that is why the row is
# SUBMIT_UNKNOWN_SIDE_EFFECT / UNKNOWN), so the REVIEW_CLEARED_NO_VENUE_EXPOSURE
# path — whose no_submit_side_effect_events predicate forbids a prior
# SUBMIT_TIMEOUT_UNKNOWN / CLOSED_MARKET_UNKNOWN event — is BY DESIGN not legal
# here. The grammar's direct SUBMIT_UNKNOWN_SIDE_EFFECT -> SUBMIT_REJECTED edge
# (venue_command_repo._TRANSITIONS) is the canonical terminalization for
# "post-submit unknown, venue authenticated-confirmed no order and no trade",
# mirroring the live-venue safe_replay_permitted_no_order_found path in
# _reconcile_row. SUBMIT_REJECTED sits OUTSIDE _UNRESOLVED_SIDE_EFFECT_STATES so
# the governor's count_unknown_side_effects drops and the kill switch clears.
_EDLI_ABSENCE_SYNC_SOURCE_FUNCTION = "command_recovery._reconcile_venue_command_absence_sync"


def _edli_reconciled_absence_for_decision(
    conn: sqlite3.Connection,
    *,
    events_ref: str,
    decision_id: str,
) -> tuple[str, dict | None]:
    """Resolve the unique EDLI authenticated-absence proof for a command.

    The canonical EDLI <-> venue_commands link is
    ``Reconciled.payload.execution_command_id == venue_commands.decision_id``
    (the same join key used by _reconcile_edli_pre_venue_unknown_thresholds).

    Returns ``(status, reconcile_payload)`` where status is one of:
      * ``"absent"``  — exactly one Reconciled event whose authenticated absence
                         proof proves venue_order_exists=false AND
                         venue_trade_exists=false AND no matching open
                         order/trade for the token. ``reconcile_payload`` is set.
      * ``"exposure"``— a matching Reconciled event reports (or its proof shows)
                         venue exposure; FAIL-CLOSED, never terminalize.
      * ``"absent_none"`` — no Reconciled event links to this decision_id.
      * ``"ambiguous"``  — more than one distinct EDLI aggregate links to this
                         decision_id; FAIL-CLOSED, cannot pick one.

    Reads only; never re-queries the venue and never writes the world ledger.
    """

    if not decision_id:
        return "absent_none", None
    rows = conn.execute(
        f"""
        SELECT aggregate_id, payload_json
        FROM {events_ref}
        WHERE event_type = 'Reconciled'
          AND json_extract(payload_json, '$.execution_command_id') = ?
        ORDER BY event_sequence DESC
        """,
        (decision_id,),
    ).fetchall()
    if not rows:
        return "absent_none", None
    distinct_aggregates = {str(_dict_row(row).get("aggregate_id") or "") for row in rows}
    if len(distinct_aggregates) != 1:
        return "ambiguous", None
    payload = _json_dict(_dict_row(rows[0]).get("payload_json"))
    proof = payload.get("authenticated_absence_proof")
    proof = proof if isinstance(proof, dict) else {}
    # Authenticated absence requires BOTH the reconcile verdict and the proof's
    # own matching-exposure counts to be zero. Any positive value, a missing
    # proof, or a non-absence verdict fails closed.
    venue_order_exists = payload.get("venue_order_exists")
    venue_trade_exists = payload.get("venue_trade_exists")
    if venue_order_exists is True or venue_trade_exists is True:
        return "exposure", None
    if not proof:
        return "absent_none", None
    if str(payload.get("reconcile_reason") or "") != "AUTHENTICATED_CLOB_ABSENCE_NO_OPEN_ORDER_OR_TRADE":
        return "absent_none", None
    try:
        matching_open = int(proof.get("matching_open_order_count", -1))
        matching_trade = int(proof.get("matching_trade_count", -1))
    except (TypeError, ValueError):
        return "absent_none", None
    if matching_open != 0 or matching_trade != 0:
        return "exposure", None
    if venue_order_exists is not False or venue_trade_exists is not False:
        return "absent_none", None
    return "absent", payload


def _reconcile_venue_command_absence_sync(conn: sqlite3.Connection) -> dict:
    """Discharge unresolved venue_commands rows already absence-proven by EDLI.

    #123 / M2 gap: the EDLI event-sourced ledger (zeus-world.db) can
    authenticated-absence-prove a stuck post-submit unknown (Reconciled +
    CapTransitioned(RELEASED)), yet the matching venue_commands row
    (zeus_trades.db) is never moved out of SUBMIT_UNKNOWN_SIDE_EFFECT /
    UNKNOWN. The two systems are not synced, so the portfolio governor — which
    counts venue_commands, not the EDLI ledger — stays latched forever.

    For each venue_commands row in _UNRESOLVED_SIDE_EFFECT_STATES with no
    venue_order_id, this pass reads the EDLI Reconciled authenticated absence
    proof (READ-only, no venue re-query) and, ONLY when it proves
    venue_order_exists=false AND venue_trade_exists=false with zero matching
    open orders/trades, appends the canonical terminal
    SUBMIT_UNKNOWN_SIDE_EFFECT -> SUBMIT_REJECTED event citing the proof hash.

    FAIL-CLOSED: no proof, ambiguous link (>1 aggregate), any matching venue
    exposure, or a present venue_order_id -> the row is left UNCHANGED. Absence
    is NEVER inferred from local rows; only the authenticated EDLI proof can
    discharge a row.

    INV-37 (cross-DB): venue_commands lives in zeus_trades.db; the absence proof
    lives in zeus-world.db. The world DB is ATTACHed onto the single trade
    connection (_maybe_attach_world_for_recovery, never an independent
    connection) and every row's terminal write is wrapped in its own SAVEPOINT,
    matching the existing cross-DB discipline in this module.
    """

    summary = {"scanned": 0, "advanced": 0, "stayed": 0, "errors": 0}
    events_ref = _edli_live_order_events_ref(conn)
    if not events_ref:
        return summary
    # Scope to the post-submit-unknown states whose grammar exposes the direct
    # SUBMIT_REJECTED terminal edge (venue_command_repo._TRANSITIONS). The third
    # member of _UNRESOLVED_SIDE_EFFECT_STATES, REVIEW_REQUIRED, is an
    # operator/recovery handoff with its OWN proof-gated clearance events
    # (_review_required_cancel_unknown_live_order_recovery / the
    # REVIEW_CLEARED_* helpers) and has NO SUBMIT_REJECTED edge — terminalizing
    # it here would be both grammar-illegal and a domain violation, so it is
    # deliberately left to its existing owner.
    states = tuple(
        state
        for state in sorted(UNRESOLVED_SIDE_EFFECT_STATES)
        if state in (
            CommandState.SUBMIT_UNKNOWN_SIDE_EFFECT.value,
            CommandState.UNKNOWN.value,
        )
    )
    if not states:
        return summary
    placeholders = ",".join("?" for _ in states)
    rows = conn.execute(
        f"""
        SELECT command_id, decision_id, market_id, token_id, side, price, size,
               created_at, venue_order_id, state
        FROM venue_commands
        WHERE state IN ({placeholders})
          AND COALESCE(venue_order_id, '') = ''
        ORDER BY created_at, command_id
        """,
        states,
    ).fetchall()
    for row in rows:
        command = _dict_row(row)
        command_id = str(command.get("command_id") or "")
        decision_id = str(command.get("decision_id") or "")
        summary["scanned"] += 1
        safe_command_id = "".join(ch if ch.isalnum() else "_" for ch in command_id)
        sp_name = f"sp_edli_absence_sync_{safe_command_id}"
        conn.execute(f"SAVEPOINT {sp_name}")
        try:
            status, reconcile_payload = _edli_reconciled_absence_for_decision(
                conn,
                events_ref=events_ref,
                decision_id=decision_id,
            )
            if status != "absent" or reconcile_payload is None:
                conn.execute(f"RELEASE SAVEPOINT {sp_name}")
                if status == "exposure":
                    logger.warning(
                        "recovery: command %s EDLI absence sync REFUSED — authenticated "
                        "proof reports venue exposure; leaving %s (fail-closed)",
                        command_id, command.get("state"),
                    )
                elif status == "ambiguous":
                    logger.warning(
                        "recovery: command %s EDLI absence sync skipped — ambiguous "
                        "EDLI link for decision_id=%s; leaving %s",
                        command_id, decision_id, command.get("state"),
                    )
                summary["stayed"] += 1
                continue
            proof = reconcile_payload.get("authenticated_absence_proof")
            proof = proof if isinstance(proof, dict) else {}
            proof_token = str(proof.get("token_id") or "")
            command_token = str(command.get("token_id") or "")
            if not proof_token or proof_token != command_token:
                conn.execute(f"RELEASE SAVEPOINT {sp_name}")
                logger.warning(
                    "recovery: command %s EDLI absence sync skipped — proof token_id "
                    "does not match command token_id; leaving %s (fail-closed)",
                    command_id, command.get("state"),
                )
                summary["stayed"] += 1
                continue
            now = _now_iso()
            payload = {
                "schema_version": 1,
                "reason": "edli_authenticated_absence_no_venue_order_or_trade",
                "command_id": command_id,
                "decision_id": decision_id,
                "proof_class": "edli_authenticated_clob_absence",
                "side_effect_boundary_crossed": "unknown",
                "venue_order_created": False,
                "safe_replay_permitted": True,
                "previous_unknown_command_id": command_id,
                "required_predicates": {
                    "edli_reconcile_reason_authenticated_absence": True,
                    "edli_venue_order_exists_false": True,
                    "edli_venue_trade_exists_false": True,
                    "edli_zero_matching_open_orders": True,
                    "edli_zero_matching_trades": True,
                    "edli_proof_token_matches_command": True,
                    "command_has_no_venue_order_id": True,
                },
                "edli_absence_proof": {
                    "aggregate_id": str(proof.get("aggregate_id") or ""),
                    "execution_command_id": str(
                        reconcile_payload.get("execution_command_id") or ""
                    ),
                    "reconcile_reason": str(reconcile_payload.get("reconcile_reason") or ""),
                    "venue_order_exists": reconcile_payload.get("venue_order_exists"),
                    "venue_trade_exists": reconcile_payload.get("venue_trade_exists"),
                    "token_id": proof_token,
                    "matching_open_order_count": int(proof.get("matching_open_order_count", 0)),
                    "matching_trade_count": int(proof.get("matching_trade_count", 0)),
                    "open_orders_query_complete": proof.get("open_orders_query_complete"),
                    "trades_query_complete": proof.get("trades_query_complete"),
                    "observed_at": proof.get("observed_at"),
                    "proof_hash": str(proof.get("proof_hash") or ""),
                },
                "source_proof": {
                    "source_commit": "runtime",
                    "source_function": _EDLI_ABSENCE_SYNC_SOURCE_FUNCTION,
                    "source_reason": "edli_authenticated_clob_absence",
                },
                "reviewed_by": "command_recovery",
                "cleared_at": now,
            }
            append_event(
                conn,
                command_id=command_id,
                event_type=CommandEventType.SUBMIT_REJECTED.value,
                occurred_at=now,
                payload=payload,
            )
            conn.execute(f"RELEASE SAVEPOINT {sp_name}")
            summary["advanced"] += 1
            logger.warning(
                "recovery: command %s %s -> SUBMIT_REJECTED (EDLI authenticated "
                "absence; proof_hash=%s; idempotency replay permitted)",
                command_id, command.get("state"), proof.get("proof_hash"),
            )
        except Exception as exc:
            conn.execute(f"ROLLBACK TO SAVEPOINT {sp_name}")
            conn.execute(f"RELEASE SAVEPOINT {sp_name}")
            logger.error(
                "recovery: command %s EDLI absence sync failed: %s; leaving row",
                command_id, exc,
            )
            summary["errors"] += 1
    return summary


def reconcile_edli_acknowledged_venue_command_sync(conn: sqlite3.Connection) -> dict:
    """Mirror ACKED venue_commands back into the EDLI live-order ledger.

    A submit can succeed in the trade-side command journal while the world-side
    EDLI aggregate only records ``VenueSubmitAttempted``. The order is real, so
    releasing the cap would be wrong; recovery appends the missing
    ``VenueSubmitAcknowledged`` and ``CapTransitioned(CONSUMED)`` events and
    marks the existing cap reservation CONSUMED.
    """

    summary = {"scanned": 0, "advanced": 0, "stayed": 0, "errors": 0}
    events_ref = _edli_live_order_events_ref(conn)
    projection_ref = _edli_live_order_projection_ref(conn)
    cap_usage_ref = _edli_live_cap_ref(conn, "edli_live_cap_usage")
    if not events_ref or not projection_ref or not cap_usage_ref or not _table_exists(conn, "venue_commands"):
        return summary
    rows = conn.execute(
        f"""
        SELECT proj.aggregate_id,
               proj.event_id,
               proj.final_intent_id,
               plan.payload_json AS plan_payload_json,
               attempted.payload_json AS attempted_payload_json,
               usage.usage_id,
               usage.execution_command_id,
               cmd.command_id,
               cmd.decision_id,
               cmd.token_id,
               cmd.state,
               cmd.venue_order_id,
               cmd.updated_at AS command_updated_at
        FROM {projection_ref} proj
        JOIN {events_ref} plan
          ON plan.aggregate_id = proj.aggregate_id
         AND plan.event_type = 'SubmitPlanBuilt'
        JOIN {events_ref} attempted
          ON attempted.aggregate_id = proj.aggregate_id
         AND attempted.event_type = 'VenueSubmitAttempted'
        JOIN {cap_usage_ref} usage
          ON usage.event_id = proj.event_id
         AND usage.final_intent_id = proj.final_intent_id
         AND usage.reservation_status = 'RESERVED'
        JOIN venue_commands cmd
          ON cmd.decision_id = usage.execution_command_id
        WHERE proj.current_state = 'VENUE_SUBMIT_ATTEMPTED'
          AND proj.last_event_type = 'VenueSubmitAttempted'
          AND COALESCE(proj.venue_order_id, '') = ''
          AND cmd.intent_kind = 'ENTRY'
          AND cmd.state IN ('ACKED', 'POST_ACKED')
          AND COALESCE(cmd.venue_order_id, '') != ''
          AND NOT EXISTS (
              SELECT 1 FROM {events_ref} ack
              WHERE ack.aggregate_id = proj.aggregate_id
                AND ack.event_type = 'VenueSubmitAcknowledged'
          )
          AND NOT EXISTS (
              SELECT 1 FROM {events_ref} cap
              WHERE cap.aggregate_id = proj.aggregate_id
                AND cap.event_type = 'CapTransitioned'
          )
        ORDER BY attempted.occurred_at, proj.aggregate_id
        """
    ).fetchall()
    summary["scanned"] = len(rows)
    occurred_at = _now_iso()
    for row in rows:
        record = _dict_row(row)
        aggregate_id = str(record.get("aggregate_id") or "")
        command_id = str(record.get("command_id") or "")
        venue_order_id = str(record.get("venue_order_id") or "").strip()
        safe_command_id = "".join(ch if ch.isalnum() else "_" for ch in command_id) or "unknown"
        sp_name = f"sp_edli_ack_sync_{safe_command_id}"
        conn.execute(f"SAVEPOINT {sp_name}")
        try:
            plan_payload = _json_dict(record.get("plan_payload_json"))
            attempted_payload = _json_dict(record.get("attempted_payload_json"))
            event_id = str(record.get("event_id") or plan_payload.get("event_id") or "")
            final_intent_id = str(record.get("final_intent_id") or plan_payload.get("final_intent_id") or "")
            execution_command_id = str(record.get("execution_command_id") or "")
            command_decision_id = str(record.get("decision_id") or "")
            plan_token = str(plan_payload.get("token_id") or "")
            command_token = str(record.get("token_id") or "")
            attempted_command_id = str(attempted_payload.get("execution_command_id") or "")
            if (
                not aggregate_id
                or not event_id
                or not final_intent_id
                or not execution_command_id
                or execution_command_id != command_decision_id
                or attempted_command_id != execution_command_id
                or not plan_token
                or plan_token != command_token
                or not venue_order_id
            ):
                conn.execute(f"RELEASE SAVEPOINT {sp_name}")
                summary["stayed"] += 1
                continue

            receipt_hash = stable_hash(
                {
                    "source": "command_recovery_edli_ack_sync",
                    "command_id": command_id,
                    "execution_command_id": execution_command_id,
                    "venue_order_id": venue_order_id,
                    "command_updated_at": str(record.get("command_updated_at") or ""),
                }
            )
            ack_event = _append_edli_event_qualified(
                conn,
                events_ref=events_ref,
                aggregate_id=aggregate_id,
                event_type="VenueSubmitAcknowledged",
                payload={
                    "schema_version": 1,
                    "event_id": event_id,
                    "final_intent_id": final_intent_id,
                    "execution_command_id": execution_command_id,
                    "execution_receipt_hash": receipt_hash,
                    "venue_order_id": venue_order_id,
                    "venue_ack_received": True,
                    "raw_response_hash": "",
                    "recovery_reason": "TRADE_COMMAND_ACKED_WORLD_LEDGER_STALLED",
                    "command_id": command_id,
                    "source_proof": {
                        "source_function": "command_recovery.reconcile_edli_acknowledged_venue_command_sync",
                        "trade_command_state": str(record.get("state") or ""),
                        "trade_command_venue_order_id": venue_order_id,
                    },
                },
                occurred_at=occurred_at,
                source_authority="existing_executor",
            )
            updated = conn.execute(
                f"""
                UPDATE {cap_usage_ref}
                SET reservation_status = 'CONSUMED',
                    final_intent_id = ?,
                    execution_command_id = ?
                WHERE usage_id = ?
                  AND reservation_status = 'RESERVED'
                """,
                (final_intent_id, execution_command_id, str(record.get("usage_id") or "")),
            )
            if updated.rowcount != 1:
                raise ValueError("EDLI ACK sync failed to consume reserved cap")
            _append_edli_event_qualified(
                conn,
                events_ref=events_ref,
                aggregate_id=aggregate_id,
                event_type="CapTransitioned",
                payload={
                    "schema_version": 1,
                    "event_id": event_id,
                    "final_intent_id": final_intent_id,
                    "execution_command_id": execution_command_id,
                    "execution_receipt_hash": receipt_hash,
                    "to_status": "CONSUMED",
                    "projection_status": "CONSUMED",
                    "transition_reason": "TRADE_COMMAND_ACKED_WORLD_LEDGER_STALLED",
                    "acknowledged_event_hash": ack_event["event_hash"],
                    "venue_order_id": venue_order_id,
                    "command_id": command_id,
                },
                occurred_at=occurred_at,
                source_authority="live_cap_ledger",
            )
            _rebuild_edli_projection_qualified(
                conn,
                events_ref=events_ref,
                projection_ref=projection_ref,
                aggregate_id=aggregate_id,
            )
            conn.execute(f"RELEASE SAVEPOINT {sp_name}")
            summary["advanced"] += 1
            logger.warning(
                "recovery: EDLI ACK sync consumed cap for aggregate=%s command=%s venue_order_id=%s",
                aggregate_id,
                command_id,
                venue_order_id,
            )
        except Exception as exc:
            conn.execute(f"ROLLBACK TO SAVEPOINT {sp_name}")
            conn.execute(f"RELEASE SAVEPOINT {sp_name}")
            logger.error("recovery: EDLI ACK sync failed for command %s: %s", command_id, exc)
            summary["errors"] += 1
    return summary


def _reconcile_edli_pre_venue_unknown_thresholds(conn: sqlite3.Connection) -> dict:
    summary = {"scanned": 0, "advanced": 0, "stayed": 0, "errors": 0}
    events_ref = _edli_live_order_events_ref(conn)
    projection_ref = _edli_live_order_projection_ref(conn)
    cap_usage_ref = _edli_live_cap_ref(conn, "edli_live_cap_usage")
    if not events_ref or not projection_ref or not cap_usage_ref:
        return summary
    rows = conn.execute(
        f"""
        SELECT proj.aggregate_id,
               json_extract(unknown.payload_json, '$.execution_command_id') AS execution_command_id,
               unknown.payload_json AS unknown_payload_json
        FROM {projection_ref} proj
        JOIN {events_ref} unknown
          ON unknown.aggregate_id = proj.aggregate_id
         AND unknown.event_type = 'SubmitUnknown'
        WHERE proj.pending_reconcile = 1
          AND COALESCE(proj.venue_order_id, '') = ''
          AND json_extract(unknown.payload_json, '$.reason_code') = 'EXECUTOR_SUBMIT_UNKNOWN:unknown_side_effect_threshold'
          AND NOT EXISTS (
              SELECT 1
              FROM venue_commands cmd
              WHERE cmd.decision_id = json_extract(unknown.payload_json, '$.execution_command_id')
          )
        ORDER BY unknown.occurred_at
        """
    ).fetchall()
    for row in rows:
        summary["scanned"] += 1
        execution_command_id = str(row["execution_command_id"] or "")
        conn.execute("SAVEPOINT edli_pre_venue_unknown_threshold_reconcile")
        try:
            advanced = _reconcile_edli_pending_no_order_if_proven(
                conn,
                execution_command_id=execution_command_id,
                occurred_at=_now_iso(),
                reason="pre_venue_risk_allocator_block_misclassified_unknown",
                proof_class="pre_venue_no_command_no_venue_order",
                command_id=None,
                required_predicates={
                    "reason_code": "EXECUTOR_SUBMIT_UNKNOWN:unknown_side_effect_threshold",
                    "no_venue_command": True,
                    "no_projection_venue_order_id": True,
                    "pending_reconcile": True,
                },
            )
            conn.execute("RELEASE SAVEPOINT edli_pre_venue_unknown_threshold_reconcile")
            if advanced:
                summary["advanced"] += 1
            else:
                summary["stayed"] += 1
        except Exception as exc:
            conn.execute("ROLLBACK TO SAVEPOINT edli_pre_venue_unknown_threshold_reconcile")
            conn.execute("RELEASE SAVEPOINT edli_pre_venue_unknown_threshold_reconcile")
            logger.error(
                "recovery: EDLI pre-venue unknown-threshold reconcile failed for %s: %s",
                execution_command_id,
                exc,
            )
            summary["errors"] += 1
    return summary


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


def _raw_payload(item) -> dict:
    raw = getattr(item, "raw", item)
    return raw if isinstance(raw, dict) else {}


def _epoch_seconds(value) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        pass
    try:
        text = str(value)
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        return datetime.fromisoformat(text).timestamp()
    except ValueError:
        return None


def _raw_mentions_token(raw: dict, token_id: str) -> bool:
    token_fields = (
        "asset_id",
        "token_id",
        "selected_outcome_token_id",
        "outcome_token_id",
    )
    if any(str(raw.get(field) or "") == token_id for field in token_fields):
        return True
    for maker in raw.get("maker_orders") or []:
        if isinstance(maker, dict) and any(str(maker.get(field) or "") == token_id for field in token_fields):
            return True
    return False


def _decimal_matches(left, right) -> bool:
    try:
        return Decimal(str(left)) == Decimal(str(right))
    except (InvalidOperation, TypeError):
        return False


def _raw_matches_command_exposure(raw: dict, command: dict) -> bool:
    token_id = str(command.get("token_id") or "")
    if _raw_mentions_token(raw, token_id):
        return True
    market_id = str(command.get("market_id") or "")
    raw_market = str(raw.get("market") or raw.get("market_id") or raw.get("condition_id") or "")
    if market_id and raw_market != market_id:
        return False
    side = str(command.get("side") or "").upper()
    raw_side = str(raw.get("side") or "").upper()
    if side and raw_side and raw_side != side:
        return False
    if not _decimal_matches(raw.get("price"), command.get("price")):
        return False
    raw_size = raw.get("size") or raw.get("original_size") or raw.get("matched_amount")
    return _decimal_matches(raw_size, command.get("size"))


def _raw_matches_command_submit_identity(raw: dict, command: dict) -> bool:
    token_id = str(command.get("token_id") or "")
    if not token_id or not _raw_mentions_token(raw, token_id):
        return False
    raw_side = str(raw.get("side") or "").upper()
    if raw_side != str(command.get("side") or "").upper():
        return False
    if not _decimal_matches(raw.get("price"), command.get("price")):
        return False
    raw_size = raw.get("original_size") or raw.get("size") or raw.get("matched_amount")
    if not _decimal_matches(raw_size, command.get("size")):
        return False
    status = _order_status(raw)
    return not status or status in _LIVE_ORDER_STATUSES


def _raw_matches_command_submit_trade_identity(raw: dict, command: dict) -> bool:
    if _review_required_trade_maker_match(command, raw) is not None:
        return True
    token_id = str(command.get("token_id") or "")
    if not token_id or not _raw_mentions_token(raw, token_id):
        return False
    raw_side = str(raw.get("side") or "").upper()
    if not raw_side or raw_side != str(command.get("side") or "").upper():
        return False
    if not _decimal_matches(raw.get("price"), command.get("price")):
        return False
    raw_size = raw.get("original_size") or raw.get("size") or raw.get("matched_amount")
    return _decimal_matches(raw_size, command.get("size"))


def _summarize_venue_match(raw: dict) -> dict:
    return {
        "id": raw.get("id") or raw.get("order_id") or raw.get("taker_order_id"),
        "status": raw.get("status") or raw.get("state"),
        "asset_id": raw.get("asset_id") or raw.get("token_id"),
        "price": raw.get("price"),
        "size": raw.get("size") or raw.get("original_size") or raw.get("matched_amount"),
        "match_time": raw.get("match_time"),
        "last_update": raw.get("last_update"),
    }


def build_review_required_no_venue_exposure_proof(
    conn: sqlite3.Connection,
    command_id: str,
    adapter,
    *,
    observed_at: str | None = None,
) -> dict:
    """Read venue surfaces and prove a REVIEW_REQUIRED command has no exposure."""

    command = _review_required_command(conn, command_id)
    events = _command_events(conn, command_id)
    review_reason = _latest_review_required_payload(events).get("reason")
    if review_reason != "recovery_no_venue_order_id":
        raise ValueError("no-exposure proof only supports recovery_no_venue_order_id")
    token_id = str(command.get("token_id") or "")
    created_epoch = _epoch_seconds(command.get("created_at")) or 0.0
    now = observed_at or _now_iso()
    open_orders = list(adapter.get_open_orders())
    trades = list(adapter.get_trades())
    matching_open = [
        _summarize_venue_match(raw)
        for raw in (_raw_payload(order) for order in open_orders)
        if _raw_matches_command_submit_identity(raw, command)
    ]
    matching_trades = []
    for trade in trades:
        raw = _raw_payload(trade)
        if not _raw_matches_command_submit_trade_identity(raw, command):
            continue
        trade_epoch = _epoch_seconds(raw.get("match_time") or raw.get("last_update"))
        if trade_epoch is not None and trade_epoch < created_epoch:
            continue
        matching_trades.append(_summarize_venue_match(raw))
    return {
        "schema_version": 1,
        "source": "authenticated_clob_user_read",
        "owner_scope": "authenticated_funder",
        "observed_at": now,
        "command_id": command_id,
        "decision_id": str(command.get("decision_id") or ""),
        "market_id": str(command.get("market_id") or ""),
        "token_id": token_id,
        "side": str(command.get("side") or ""),
        "price": str(Decimal(str(command.get("price")))),
        "size": str(Decimal(str(command.get("size")))),
        "open_orders_checked": True,
        "trades_checked": True,
        "open_orders_query_complete": True,
        "trades_query_complete": True,
        "pagination_scope": "sdk_get_trades_returned_all_visible_user_trades",
        "time_window_start": command.get("created_at"),
        "time_window_end": now,
        "open_order_count": len(open_orders),
        "trade_count": len(trades),
        "matching_open_order_count": len(matching_open),
        "matching_trade_count": len(matching_trades),
        "matching_open_orders": matching_open[:10],
        "matching_trades": matching_trades[:10],
    }


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


def _review_no_exposure_predicates(
    conn: sqlite3.Connection,
    command: dict,
) -> tuple[dict, list[str]]:
    predicates, _failures = _review_no_side_effect_predicates(conn, command)
    predicates.pop("review_required_reason_pre_sdk", None)
    latest_reason = _latest_review_required_payload(
        _command_events(conn, str(command["command_id"]))
    ).get("reason")
    predicates["review_required_reason_recovery_no_venue_order_id"] = (
        latest_reason == "recovery_no_venue_order_id"
    )
    failures = [name for name, ok in predicates.items() if not ok]
    return predicates, failures


def clear_review_required_no_venue_exposure(
    conn: sqlite3.Connection,
    command_id: str,
    *,
    venue_absence_proof: dict,
    source_commit: str,
    source_function: str,
    reviewed_by: str = "operator",
    occurred_at: str | None = None,
) -> dict:
    """Terminalize recovery_no_venue_order_id only after fresh venue absence proof."""

    command = _review_required_command(conn, command_id)
    predicates, predicate_failures = _review_no_exposure_predicates(conn, command)
    if predicate_failures:
        raise ValueError(
            "review no-exposure predicates failed: " + ", ".join(sorted(predicate_failures))
        )
    latest_reason = _latest_review_required_payload(_command_events(conn, command_id)).get("reason")
    if latest_reason != "recovery_no_venue_order_id":
        raise ValueError("review no-exposure clearance only supports recovery_no_venue_order_id")
    if int(venue_absence_proof.get("matching_open_order_count", -1)) != 0:
        raise ValueError("review no-exposure clearance found matching open orders")
    if int(venue_absence_proof.get("matching_trade_count", -1)) != 0:
        raise ValueError("review no-exposure clearance found matching trades")
    if not str(source_commit or "").strip():
        raise ValueError("source_commit is required")
    if source_function not in {"command_recovery._reconcile_row", "operator_review"}:
        raise ValueError("source_function must identify the recovery/operator boundary")
    now = occurred_at or _now_iso()
    decision_id = str(command.get("decision_id") or "")
    payload = {
        "schema_version": 1,
        "reason": "review_cleared_no_venue_exposure",
        "command_id": command_id,
        "decision_id": decision_id,
        "proof_class": "venue_absence_no_exposure",
        "side_effect_boundary_crossed": "unknown",
        "sdk_submit_attempted": "unknown",
        "required_predicates": predicates,
        "venue_absence_proof": venue_absence_proof,
        "source_proof": {
            "source_commit": source_commit,
            "source_function": source_function,
            "source_reason": "recovery_no_venue_order_id",
        },
        "review_required_proof": {
            "reason": latest_reason,
            "allowed_reasons": ["recovery_no_venue_order_id"],
        },
        "reviewed_by": reviewed_by,
        "cleared_at": now,
    }
    append_event(
        conn,
        command_id=command_id,
        event_type=CommandEventType.REVIEW_CLEARED_NO_VENUE_EXPOSURE.value,
        occurred_at=now,
        payload=payload,
    )
    return payload


def _review_required_no_venue_live_order_recovery(
    conn: sqlite3.Connection,
    cmd: VenueCommand,
    client,
) -> str:
    events = _command_events(conn, cmd.command_id)
    latest_reason = _latest_review_required_payload(events).get("reason")
    if latest_reason != "recovery_no_venue_order_id":
        return "stayed"
    if str(cmd.venue_order_id or "").strip():
        return "stayed"
    command = _dict_row(
        conn.execute(
            "SELECT * FROM venue_commands WHERE command_id = ?",
            (cmd.command_id,),
        ).fetchone()
    )
    try:
        open_orders = [_raw_payload(order) for order in _client_open_orders(client)]
        trades = [_raw_payload(trade) for trade in _client_trades(client)]
    except Exception as exc:  # noqa: BLE001 - recovery should retry on venue read failure.
        logger.warning(
            "recovery: command %s REVIEW_REQUIRED no-venue live-order proof read failed: %s",
            cmd.command_id,
            exc,
        )
        return "error"

    matching_open_orders = [
        raw
        for raw in open_orders
        if _raw_matches_command_submit_identity(raw, command)
    ]
    if len(matching_open_orders) != 1:
        if len(matching_open_orders) > 1:
            logger.warning(
                "recovery: command %s REVIEW_REQUIRED no-venue stayed; "
                "ambiguous matching open orders=%d",
                cmd.command_id,
                len(matching_open_orders),
            )
        return "stayed"

    matching_trades = _matching_trades_for_command(
        client,
        command,
        trades=trades,
    )
    if matching_trades:
        logger.info(
            "recovery: command %s REVIEW_REQUIRED no-venue stayed; "
            "matching trades=%d require fill authority",
            cmd.command_id,
            len(matching_trades),
        )
        return "stayed"

    order = dict(matching_open_orders[0])
    venue_order_id = str(_extract_order_id(order) or "").strip()
    status = _order_status(order) or "LIVE"
    matched_size = _point_order_matched_size(order, side=command.get("side"))
    if not venue_order_id or status not in _LIVE_ORDER_STATUSES or not _decimal_is_zero(matched_size):
        logger.info(
            "recovery: command %s REVIEW_REQUIRED no-venue stayed; "
            "order_id=%s status=%s matched_size=%s",
            cmd.command_id,
            venue_order_id or "<missing>",
            status or "UNKNOWN",
            matched_size,
        )
        return "stayed"

    now = _now_iso()
    order_summary = _summarize_venue_match(order)
    payload = {
        "schema_version": 1,
        "reason": "review_cleared_venue_order_live",
        "command_id": cmd.command_id,
        "venue_order_id": venue_order_id,
        "proof_class": "recovery_no_venue_order_id_live_order",
        "side_effect_boundary_crossed": True,
        "sdk_submit_attempted": True,
        "required_predicates": {
            "latest_event_is_review_required": True,
            "review_reason_recovery_no_venue_order_id": True,
            "venue_order_id_absent_before_recovery": True,
            "proof_venue_order_id_present": True,
            "unique_matching_open_order": True,
            "matching_open_order_matches_command": True,
            "authenticated_live_order_seen": True,
            "point_order_matched_size_not_positive": True,
            "no_matching_trades": True,
            "no_trade_facts": _count_facts(conn, "venue_trade_facts", cmd.command_id) == 0,
        },
        "venue_order_live_proof": {
            "source": "authenticated_clob_user_open_orders_read",
            "owner_scope": "authenticated_funder",
            "observed_at": now,
            "venue_order_id": venue_order_id,
            "point_order_status": status,
            "matched_size": matched_size,
            "matching_open_order_count": 1,
            "matching_trade_count": 0,
            "matching_open_orders": [order_summary],
            "point_order": order,
        },
        "source_proof": {
            "source_function": "command_recovery._reconcile_row",
            "source_reason": "recovery_no_venue_order_id_live_order",
        },
        "reviewed_by": "command_recovery",
        "cleared_at": now,
    }
    safe_command_id = "".join(ch if ch.isalnum() else "_" for ch in cmd.command_id)
    sp_name = f"sp_no_venue_live_order_{safe_command_id}"
    conn.execute(f"SAVEPOINT {sp_name}")
    try:
        append_event(
            conn,
            command_id=cmd.command_id,
            event_type=CommandEventType.REVIEW_CLEARED_VENUE_ORDER_LIVE.value,
            occurred_at=now,
            payload=payload,
        )
        append_order_fact(
            conn,
            venue_order_id=venue_order_id,
            command_id=cmd.command_id,
            state="RESTING" if status == "RESTING" else "LIVE",
            remaining_size=str(order.get("size") or order.get("remaining_size") or command.get("size") or ""),
            matched_size=matched_size,
            source="REST",
            observed_at=now,
            venue_timestamp=now,
            raw_payload_hash=_canonical_payload_hash(
                {
                    "source": "command_recovery_no_venue_live_order",
                    "command_id": cmd.command_id,
                    "venue_order_id": venue_order_id,
                    "exchange_order": order,
                }
            ),
            raw_payload_json={
                "source": "command_recovery_no_venue_live_order",
                "command_id": cmd.command_id,
                "venue_order_id": venue_order_id,
                "exchange_order": order,
            },
        )
        _resolve_m5_exchange_ghost_findings(
            conn,
            venue_order_id=venue_order_id,
            resolved_at=now,
            resolution="command_recovery_no_venue_live_order_adopted",
        )
        conn.execute(f"RELEASE SAVEPOINT {sp_name}")
    except Exception:
        conn.execute(f"ROLLBACK TO SAVEPOINT {sp_name}")
        conn.execute(f"RELEASE SAVEPOINT {sp_name}")
        raise
    logger.info(
        "recovery: command %s REVIEW_REQUIRED no-venue -> ACKED "
        "(venue_order_id=%s status=%s)",
        cmd.command_id,
        venue_order_id,
        status,
    )
    return "advanced"


def clear_review_required_confirmed_fill(
    conn: sqlite3.Connection,
    command_id: str,
    *,
    source_commit: str,
    source_function: str = "PolymarketUserChannelIngestor._handle_trade",
    reviewed_by: str = "operator",
    occurred_at: str | None = None,
) -> dict:
    """Restore FILLED only when REVIEW_REQUIRED followed an already confirmed fill."""

    command = _review_required_command(conn, command_id)
    events = _command_events(conn, command_id)
    latest_reason = _latest_review_required_payload(events).get("reason")
    if latest_reason != "ws_trade_lifecycle_regression_or_economic_drift":
        raise ValueError("confirmed-fill review clearance only supports WS lifecycle/economic drift reviews")
    fill_payload = None
    for event in reversed(events):
        if event.get("event_type") == CommandEventType.FILL_CONFIRMED.value:
            fill_payload = _json_dict(event.get("payload_json"))
            break
    if fill_payload is None:
        raise ValueError("confirmed-fill review clearance requires a prior FILL_CONFIRMED event")
    trade_id = str(fill_payload.get("trade_id") or "")
    venue_order_id = str(fill_payload.get("venue_order_id") or command.get("venue_order_id") or "")
    row = conn.execute(
        "WITH " + _canonical_trade_fact_cte() + """
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
    if row is None:
        raise ValueError("confirmed-fill review clearance requires a matching positive venue_trade_facts row")
    trade_fact = _dict_row(row)
    if not str(source_commit or "").strip():
        raise ValueError("source_commit is required")
    now = occurred_at or _now_iso()
    payload = {
        "schema_version": 1,
        "reason": "review_cleared_confirmed_fill",
        "command_id": command_id,
        "decision_id": str(command.get("decision_id") or ""),
        "venue_order_id": venue_order_id,
        "trade_id": trade_id,
        "filled_size": str(trade_fact.get("filled_size") or ""),
        "fill_price": str(trade_fact.get("fill_price") or ""),
        "proof_class": "prior_fill_confirmed_event_with_positive_trade_fact",
        "required_predicates": {
            "latest_event_is_review_required": bool(events and events[-1].get("event_type") == CommandEventType.REVIEW_REQUIRED.value),
            "review_reason_supported": latest_reason == "ws_trade_lifecycle_regression_or_economic_drift",
            "prior_fill_confirmed_event": True,
            "positive_trade_fact": True,
        },
        "prior_fill_confirmed_event": fill_payload,
        "trade_fact_proof": {
            "trade_fact_id": trade_fact.get("trade_fact_id"),
            "state": trade_fact.get("state"),
            "source": trade_fact.get("source"),
            "observed_at": trade_fact.get("observed_at"),
        },
        "review_required_proof": {
            "reason": latest_reason,
        },
        "source_proof": {
            "source_commit": source_commit,
            "source_function": source_function,
            "source_reason": "ws_trade_lifecycle_regression_or_economic_drift",
        },
        "reviewed_by": reviewed_by,
        "cleared_at": now,
    }
    append_event(
        conn,
        command_id=command_id,
        event_type=CommandEventType.FILL_CONFIRMED.value,
        occurred_at=now,
        payload=payload,
    )
    return payload


def _review_required_no_venue_exposure_recovery(
    conn: sqlite3.Connection,
    cmd: VenueCommand,
    client,
) -> str:
    events = _command_events(conn, cmd.command_id)
    latest_reason = _latest_review_required_payload(events).get("reason")
    if latest_reason != "recovery_no_venue_order_id":
        return "stayed"
    try:
        now = _now_iso()
        proof = build_review_required_no_venue_exposure_proof(
            conn,
            cmd.command_id,
            client,
            observed_at=now,
        )
        clear_review_required_no_venue_exposure(
            conn,
            cmd.command_id,
            venue_absence_proof=proof,
            source_commit="runtime",
            source_function="command_recovery._reconcile_row",
            reviewed_by="command_recovery",
            occurred_at=now,
        )
    except ValueError as exc:
        logger.info(
            "recovery: command %s REVIEW_REQUIRED no-venue-exposure stayed: %s",
            cmd.command_id,
            exc,
        )
        return "stayed"
    except Exception as exc:  # noqa: BLE001 - recovery loops count errors and continue.
        logger.warning(
            "recovery: command %s REVIEW_REQUIRED no-venue-exposure proof failed: %s",
            cmd.command_id,
            exc,
        )
        return "error"
    logger.info(
        "recovery: command %s REVIEW_REQUIRED recovery_no_venue_order_id -> EXPIRED",
        cmd.command_id,
    )
    return "advanced"


def _review_required_trade_maker_match(command: dict, trade: dict) -> dict | None:
    token_id = str(command.get("token_id") or "")
    side = str(command.get("side") or "").upper()
    command_price = command.get("price")
    command_size = _decimal_or_none(command.get("size"))
    if not token_id or command_size is None:
        return None
    for maker in trade.get("maker_orders") or []:
        if not isinstance(maker, dict):
            continue
        if str(maker.get("asset_id") or maker.get("token_id") or "") != token_id:
            continue
        if side and str(maker.get("side") or "").upper() != side:
            continue
        if not _decimal_matches(maker.get("price"), command_price):
            continue
        matched = _positive_decimal_or_none(maker.get("matched_amount") or maker.get("size"))
        if matched is None:
            continue
        residual = command_size - matched
        if residual < 0:
            residual = Decimal("0")
        if residual >= Decimal("0.01"):
            continue
        order_id = str(maker.get("order_id") or maker.get("id") or "").strip()
        if not order_id:
            continue
        return {
            "order_id": order_id,
            "matched_size": _decimal_text(matched),
            "fill_price": str(maker.get("price") or command_price),
            "maker_order": maker,
        }
    return None


def _review_required_confirmed_trade_match(
    command: dict,
    client,
) -> tuple[dict, dict, list[dict]] | None:
    try:
        open_orders = [_raw_payload(order) for order in list(client.get_open_orders() or [])]
        trades = [_raw_payload(trade) for trade in list(client.get_trades() or [])]
    except Exception:
        logger.debug("recovery: confirmed-trade review proof venue read failed", exc_info=True)
        raise
    open_order_ids = {
        str(order.get("id") or order.get("order_id") or order.get("orderID") or "")
        for order in open_orders
    }
    created_epoch = _epoch_seconds(command.get("created_at")) or 0.0
    matches: list[tuple[dict, dict]] = []
    for trade in trades:
        trade_epoch = _epoch_seconds(trade.get("match_time") or trade.get("last_update"))
        if trade_epoch is not None and trade_epoch < created_epoch:
            continue
        if str(trade.get("status") or trade.get("state") or "").upper() != "CONFIRMED":
            continue
        maker_match = _review_required_trade_maker_match(command, trade)
        if maker_match is None:
            continue
        if maker_match["order_id"] in open_order_ids:
            continue
        matches.append((trade, maker_match))
    if len(matches) != 1:
        return None
    trade, maker_match = matches[0]
    return trade, maker_match, open_orders


def _review_required_confirmed_trade_recovery(
    conn: sqlite3.Connection,
    cmd: VenueCommand,
    client,
) -> str:
    events = _command_events(conn, cmd.command_id)
    latest_reason = _latest_review_required_payload(events).get("reason")
    if latest_reason != "recovery_no_venue_order_id":
        return "stayed"
    command = _dict_row(
        conn.execute(
            "SELECT * FROM venue_commands WHERE command_id = ?",
            (cmd.command_id,),
        ).fetchone()
    )
    try:
        match = _review_required_confirmed_trade_match(command, client)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "recovery: command %s REVIEW_REQUIRED confirmed-trade proof failed: %s",
            cmd.command_id,
            exc,
        )
        return "error"
    if match is None:
        return "stayed"
    trade, maker_match, open_orders = match
    now = _now_iso()
    venue_order_id = str(maker_match["order_id"])
    trade_id = str(trade.get("id") or trade.get("trade_id") or "")
    filled_size = str(maker_match["matched_size"])
    fill_price = str(maker_match["fill_price"])
    tx_hash = str(trade.get("transaction_hash") or trade.get("tx_hash") or "") or None
    if not trade_id:
        return "stayed"
    payload = {
        "schema_version": 1,
        "reason": "review_cleared_confirmed_fill",
        "command_id": cmd.command_id,
        "decision_id": str(command.get("decision_id") or ""),
        "venue_order_id": venue_order_id,
        "trade_id": trade_id,
        "filled_size": filled_size,
        "fill_price": fill_price,
        "proof_class": "recovery_no_venue_order_id_confirmed_trade",
        "side_effect_boundary_crossed": True,
        "sdk_submit_attempted": "unknown",
        "required_predicates": {
            "latest_event_is_review_required": True,
            "review_reason_recovery_no_venue_order_id": True,
            "positive_trade_fact": True,
            "maker_order_token_matches_command": True,
            "maker_order_not_open": True,
            "venue_size_quantization_residual_lt_0_01": True,
        },
        "trade_fact_proof": {
            "source": "authenticated_clob_user_trades",
            "observed_at": now,
            "trade_status": str(trade.get("status") or trade.get("state") or ""),
            "trade": trade,
            "maker_order": maker_match["maker_order"],
            "open_order_ids_checked": [
                str(order.get("id") or order.get("order_id") or order.get("orderID") or "")
                for order in open_orders
            ],
        },
        "review_required_proof": {
            "reason": latest_reason,
        },
        "source_proof": {
            "source_commit": "runtime",
            "source_function": "command_recovery._reconcile_row",
            "source_reason": "recovery_no_venue_order_id_confirmed_trade",
        },
        "reviewed_by": "command_recovery",
        "cleared_at": now,
    }
    append_order_fact(
        conn,
        venue_order_id=venue_order_id,
        command_id=cmd.command_id,
        state="MATCHED",
        remaining_size="0",
        matched_size=filled_size,
        source="REST",
        observed_at=now,
        venue_timestamp=now,
        raw_payload_hash=_canonical_payload_hash({**payload, "fact_type": "order"}),
        raw_payload_json=payload,
    )
    append_trade_fact(
        conn,
        trade_id=trade_id,
        venue_order_id=venue_order_id,
        command_id=cmd.command_id,
        state="CONFIRMED",
        filled_size=filled_size,
        fill_price=fill_price,
        source="REST",
        observed_at=now,
        venue_timestamp=now,
        tx_hash=tx_hash,
        raw_payload_hash=_canonical_payload_hash({**payload, "fact_type": "trade"}),
        raw_payload_json=payload,
    )
    append_event(
        conn,
        command_id=cmd.command_id,
        event_type=CommandEventType.FILL_CONFIRMED.value,
        occurred_at=now,
        payload=payload,
    )
    _append_matched_order_fill_projection(
        conn,
        command={**command, "venue_order_id": venue_order_id},
        venue_order_id=venue_order_id,
        matched_size=filled_size,
        fill_price=fill_price,
        observed_at=now,
    )
    logger.info(
        "recovery: command %s REVIEW_REQUIRED recovery_no_venue_order_id -> FILLED "
        "(venue_order_id=%s trade_id=%s)",
        cmd.command_id,
        venue_order_id,
        trade_id,
    )
    return "advanced"


def clear_submit_unknown_geoblock_403(
    conn: sqlite3.Connection,
    command_id: str,
    *,
    reviewed_by: str = "operator",
    occurred_at: str | None = None,
) -> dict:
    """Terminalize a deterministic CLOB geoblock 403 as rejected.

    This is intentionally narrower than general unknown-side-effect recovery.
    It only handles synchronous Polymarket ``PolyApiException`` geoblock 403
    responses with no persisted venue identity, final envelope, order facts,
    trade facts, or position records. Timeouts, 5xx, connection failures, and
    ambiguous SDK exceptions remain unresolved.
    """

    command = _submit_unknown_command(conn, command_id)
    predicates, predicate_failures = _geoblock_403_predicates(conn, command)
    if predicate_failures:
        raise ValueError(
            "geoblock 403 terminalization predicates failed: "
            + ", ".join(sorted(predicate_failures))
        )
    now = occurred_at or _now_iso()
    payload = {
        "schema_version": 1,
        "reason": "venue_rejected_geoblock_403",
        "command_id": command_id,
        "decision_id": str(command.get("decision_id") or ""),
        "proof_class": "deterministic_venue_geoblock_403",
        "side_effect_boundary_crossed": True,
        "venue_order_created": False,
        "required_predicates": predicates,
        "idempotency_key": str(command.get("idempotency_key") or ""),
        "reviewed_by": reviewed_by,
        "cleared_at": now,
    }
    append_event(
        conn,
        command_id=command_id,
        event_type=CommandEventType.SUBMIT_REJECTED.value,
        occurred_at=now,
        payload=payload,
    )
    return payload


def _recover_no_venue_order_id_submit(
    conn: sqlite3.Connection,
    cmd: VenueCommand,
    client,
    *,
    now: str,
) -> str:
    """Resolve a post-HTTP submit row that has no venue order id.

    A deterministic synchronous venue rejection can fail to persist its terminal
    event if the trade DB is locked after the HTTP response. Treat that row like
    unknown-side-effect recovery: prove whether the idempotency key exists at
    the venue, adopt it if found, otherwise release after the safe replay window.
    """

    lookup_status, venue_resp, venue_absence_proof, lookup_method = (
        _lookup_unknown_side_effect_order(cmd, client)
    )
    if lookup_status == "unavailable":
        append_event(
            conn,
            command_id=cmd.command_id,
            event_type=CommandEventType.REVIEW_REQUIRED.value,
            occurred_at=now,
            payload={
                "reason": "recovery_no_venue_order_id_lookup_unavailable",
                "lookup_method": lookup_method,
            },
        )
        logger.warning(
            "recovery: command %s SUBMITTING without venue_order_id -> REVIEW_REQUIRED "
            "(lookup unavailable)",
            cmd.command_id,
        )
        return "advanced"

    venue_order_id = _extract_order_id(venue_resp, cmd.venue_order_id)
    if venue_resp is not None:
        venue_status = _order_status(venue_resp)
        payload = {
            "venue_order_id": venue_order_id,
            "venue_status": venue_status,
            "venue_response": venue_resp,
            "idempotency_key": cmd.idempotency_key.value,
            "lookup_method": lookup_method,
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
                "recovery: command %s SUBMITTING/no-venue-id -> REVIEW_REQUIRED "
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
                "recovery: command %s SUBMITTING/no-venue-id -> PARTIAL "
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
                payload={**payload, "reason": "recovery_venue_rejected_no_venue_order_id"},
            )
            logger.info(
                "recovery: command %s SUBMITTING/no-venue-id -> REJECTED (venue status=%s)",
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
            "recovery: command %s SUBMITTING/no-venue-id -> ACKED "
            "(venue status=%s order %s)",
            cmd.command_id, venue_status, venue_order_id,
        )
        return "advanced"

    age = _age_seconds(cmd, now=datetime.now(timezone.utc))
    if age is None or age < _SAFE_REPLAY_MIN_AGE_SECONDS:
        logger.info(
            "recovery: command %s SUBMITTING/no-venue-id not found but age=%s; "
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
            "lookup_method": lookup_method,
            "venue_absence_proof": venue_absence_proof,
            "recovered_from_state": "SUBMITTING",
        },
    )
    logger.warning(
        "recovery: command %s SUBMITTING/no-venue-id -> SUBMIT_REJECTED "
        "(safe replay permitted; idempotency key not found after %.1fs)",
        cmd.command_id, age,
    )
    return "advanced"


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

        if state == CommandState.REVIEW_REQUIRED:
            outcome = _review_required_post_ack_terminal_no_fill_recovery(conn, cmd, client)
            if outcome != "stayed":
                return outcome
            outcome = _review_required_cancel_unknown_live_order_recovery(conn, cmd, client)
            if outcome != "stayed":
                return outcome
            outcome = _review_required_confirmed_trade_recovery(conn, cmd, client)
            if outcome != "stayed":
                return outcome
            outcome = _review_required_no_venue_live_order_recovery(conn, cmd, client)
            if outcome != "stayed":
                return outcome
            return _review_required_no_venue_exposure_recovery(conn, cmd, client)

        now = _now_iso()

        # ------------------------------------------------------------------ #
        # SUBMITTING without venue_order_id: no ACK was persisted. This may be #
        # an ack-lost live order or a deterministic venue reject whose terminal#
        # SUBMIT_REJECTED write hit a transient DB lock. Use the same          #
        # idempotency/absence proof path as unknown-side-effect recovery.      #
        # ------------------------------------------------------------------ #
        if state == CommandState.SUBMITTING and not cmd.venue_order_id:
            return _recover_no_venue_order_id_submit(conn, cmd, client, now=now)

        # ------------------------------------------------------------------ #
        # M2: SUBMIT_UNKNOWN_SIDE_EFFECT                                      #
        # ------------------------------------------------------------------ #
        if state == CommandState.SUBMIT_UNKNOWN_SIDE_EFFECT:
            command = _dict_row(
                conn.execute(
                    "SELECT * FROM venue_commands WHERE command_id = ?",
                    (cmd.command_id,),
                ).fetchone()
            )
            invalid_amount_payload = _terminalize_submit_unknown_invalid_amount_400_if_proven(
                conn,
                command,
                occurred_at=now,
            )
            if invalid_amount_payload is not None:
                logger.info(
                    "recovery: command %s SUBMIT_UNKNOWN_SIDE_EFFECT -> "
                    "SUBMIT_REJECTED (deterministic invalid amount 400)",
                    cmd.command_id,
                )
                return "advanced"

            lookup_status, venue_resp, venue_absence_proof, lookup_method = (
                _lookup_unknown_side_effect_order(cmd, client)
            )
            if lookup_status == "unavailable":
                logger.warning(
                    "recovery: command %s SUBMIT_UNKNOWN_SIDE_EFFECT cannot be resolved; "
                    "client lacks a complete venue lookup surface or venue reads found "
                    "ambiguous matching exposure",
                    cmd.command_id,
                )
                return "error"

            venue_order_id = _extract_order_id(venue_resp, cmd.venue_order_id)
            if venue_resp is not None:
                venue_status = _order_status(venue_resp)
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
                    "lookup_method": lookup_method,
                    "venue_absence_proof": venue_absence_proof,
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
            venue_resp = _venue_order_payload(client.get_order(venue_order_id))
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
                venue_status = _order_status(venue_resp)
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
                    no_fill_proven, no_fill_reason = _terminal_point_order_zero_fill_proven(
                        conn,
                        command_id=cmd.command_id,
                        point_order=venue_resp,
                    )
                    if not no_fill_proven:
                        append_event(
                            conn,
                            command_id=cmd.command_id,
                            event_type=CommandEventType.REVIEW_REQUIRED.value,
                            occurred_at=now,
                            payload={
                                "reason": no_fill_reason,
                                "venue_order_id": venue_order_id,
                                "venue_status": venue_status,
                                "venue_response": venue_resp,
                            },
                        )
                        logger.warning(
                            "recovery: command %s SUBMITTING terminal status=%s lacks zero-fill proof -> REVIEW_REQUIRED (%s)",
                            cmd.command_id,
                            venue_status,
                            no_fill_reason,
                        )
                        return "advanced"
                    append_event(
                        conn,
                        command_id=cmd.command_id,
                        event_type=CommandEventType.EXPIRED.value,
                        occurred_at=now,
                        payload={
                            "reason": "recovery_venue_terminal_no_fill",
                            "venue_order_id": venue_order_id,
                            "venue_status": venue_status,
                            "zero_fill_proof": no_fill_reason,
                        },
                    )
                    logger.info(
                        "recovery: command %s SUBMITTING -> EXPIRED (venue terminal zero-fill status=%s)",
                        cmd.command_id,
                        venue_status,
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
                venue_status = _order_status(venue_resp)
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
                    no_fill_proven, no_fill_reason = _terminal_point_order_zero_fill_proven(
                        conn,
                        command_id=cmd.command_id,
                        point_order=venue_resp,
                    )
                    if not no_fill_proven:
                        append_event(
                            conn,
                            command_id=cmd.command_id,
                            event_type=CommandEventType.REVIEW_REQUIRED.value,
                            occurred_at=now,
                            payload={
                                "reason": no_fill_reason,
                                "venue_order_id": venue_order_id,
                                "venue_status": venue_status,
                                "venue_response": venue_resp,
                            },
                        )
                        logger.warning(
                            "recovery: command %s UNKNOWN terminal status=%s lacks zero-fill proof -> REVIEW_REQUIRED (%s)",
                            cmd.command_id,
                            venue_status,
                            no_fill_reason,
                        )
                        return "advanced"
                    append_event(
                        conn,
                        command_id=cmd.command_id,
                        event_type=CommandEventType.EXPIRED.value,
                        occurred_at=now,
                        payload={
                            "reason": "recovery_venue_terminal_no_fill",
                            "venue_order_id": venue_order_id,
                            "venue_status": venue_status,
                            "zero_fill_proof": no_fill_reason,
                        },
                    )
                    logger.info(
                        "recovery: command %s UNKNOWN -> EXPIRED (venue terminal zero-fill status=%s)",
                        cmd.command_id,
                        venue_status,
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
            venue_status = _order_status(venue_resp)
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
            if (
                cmd.intent_kind == IntentKind.ENTRY
                and venue_status in _LIVE_ORDER_STATUSES
                and _decimal_is_zero(_explicit_point_order_matched_size(venue_resp))
            ):
                events = _command_events(conn, cmd.command_id)
                cancel_requested_payload = _latest_maker_rest_cancel_requested_payload(events)
                if cancel_requested_payload is not None:
                    append_event(
                        conn,
                        command_id=cmd.command_id,
                        event_type=CommandEventType.CANCEL_REPLACE_BLOCKED.value,
                        occurred_at=now,
                        payload={
                            "venue_order_id": venue_order_id,
                            "reason": "post_cancel_unknown_possible_side_effect",
                            "requires_m5_reconcile": True,
                            "semantic_cancel_status": "CANCEL_UNKNOWN",
                            "cancel_outcome": {
                                "status": "LIVE_AFTER_CANCEL_REQUEST",
                                "venue_status": venue_status,
                                "source": "command_recovery_cancel_pending_live_read",
                                "latest_cancel_requested_payload": cancel_requested_payload,
                            },
                        },
                    )
                    review_cmd = replace(cmd, state=CommandState.REVIEW_REQUIRED)
                    outcome = _review_required_cancel_unknown_live_order_recovery(
                        conn,
                        review_cmd,
                        client,
                    )
                    if outcome == "error":
                        return "error"
                    return "advanced"
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
    *,
    scope: str = "full",
) -> dict:
    """Scan unresolved venue_commands and apply reconciliation events.

    Returns a summary dict {"scanned": N, "advanced": M, "stayed": K, "errors": L}
    so cycle_runner can record it in the cycle summary.

    Each row in IN_FLIGHT_STATES is looked up at the venue (if it has a
    venue_order_id) and an event is appended per §P1.S4. Rows in
    REVIEW_REQUIRED are skipped (operator-handoff). Rows without a
    venue_order_id and in SUBMITTING get a REVIEW_REQUIRED event since recovery
    cannot distinguish never-placed from ack-lost side effects.

    ``scope="full"`` keeps the historical complete sweep. ``scope="live_tick"``
    is for the live order daemon's frequent scheduler cadence: it reconciles the
    critical in-flight command surface, then leaves heavier projection, partial,
    and maker-fill maintenance for the full sweep / sidecar owner so the trading
    reactor is not starved by a boot-time recovery storm.

    DB connection: if conn is None, opens get_trade_connection_with_world_required()
    internally (with a try/finally to close). CycleRunner passes the per-cycle
    trade/world connection; the internal-open path remains the external-caller
    fallback.

    PolymarketClient: if client is None, lazily constructed here.

    CONNECTION TOPOLOGY (dependency_db_locked antibody, 2026-06-11):

      * ``conn`` PROVIDED (legacy cycle_runner lane + all INV-31 anchor tests):
        the caller owns the connection lifetime and threads its own per-cycle
        trade/world connection through every pass — the historical shape. This
        path is byte-identical to before.

      * ``conn is None`` (the EDLI scheduled-job lane, #28c): runs the
        per-pass SHORT-CONNECTION three-phase flow via
        ``src.execution.venue_sync_contract``. No single connection is held
        across venue REST I/O, and no connection spans more than one pass — so
        the sweep can never again pin the zeus_trades WAL write lock across a
        multi-minute venue-read sweep and starve other writers into the
        DATA_DEGRADED / RISK_GUARD_BLOCKED cascade observed since ~03:36Z on
        2026-06-11. Reconciliation SEMANTICS are unchanged: each pass body runs
        verbatim against a venue snapshot captured off-lock.
    """
    if scope not in {"full", "live_tick"}:
        raise ValueError(f"unsupported command recovery scope: {scope!r}")
    if conn is not None and scope != "full":
        raise ValueError("non-full command recovery scopes require conn=None")

    if client is None:
        from src.data.polymarket_client import PolymarketClient
        client = PolymarketClient()

    summary = {"scanned": 0, "advanced": 0, "stayed": 0, "errors": 0}
    started_at = _now_iso()

    if conn is None:
        # Scheduled-job lane: per-pass short connections, no conn across network.
        _reconcile_passes_short_conn(client, summary, started_at, scope=scope)
        logger.info(
            "recovery: scanned=%d advanced=%d stayed=%d errors=%d",
            summary["scanned"], summary["advanced"], summary["stayed"], summary["errors"],
        )
        return summary

    # Legacy caller-owned-connection lane (unchanged).
    _reconcile_passes_inline(conn, client, summary, started_at)
    logger.info(
        "recovery: scanned=%d advanced=%d stayed=%d errors=%d",
        summary["scanned"], summary["advanced"], summary["stayed"], summary["errors"],
    )
    return summary


def _reconcile_passes_inline(
    conn: sqlite3.Connection,
    client,
    summary: dict,
    started_at: str,
) -> None:
    """Legacy caller-owned-connection pass sequence (BYTE-IDENTICAL to pre-2026-06-11).

    The caller (cycle_runner or an INV-31 anchor test) owns ``conn`` and threads
    it through every pass exactly as before. No behavioural change; extracted
    verbatim from the old ``reconcile_unresolved_commands`` body so the scheduled
    lane can take the short-connection path without disturbing this one.
    """
    if True:  # preserve original indentation of the extracted body verbatim
        edli_confirmed_command_summary = reconcile_edli_confirmed_legacy_command_repairs(conn)
        summary["edli_confirmed_legacy_command_repair"] = edli_confirmed_command_summary
        summary["advanced"] += edli_confirmed_command_summary["advanced"]
        summary["stayed"] += edli_confirmed_command_summary["stayed"]
        summary["errors"] += edli_confirmed_command_summary["errors"]

        stale_intent_summary = reconcile_stale_intent_created_no_submit(
            conn,
            updated_before=started_at,
        )
        summary["stale_intent_created_no_submit"] = stale_intent_summary
        summary["advanced"] += stale_intent_summary["advanced"]
        summary["stayed"] += stale_intent_summary["stayed"]
        summary["errors"] += stale_intent_summary["errors"]

        abandoned_ghost_summary = reconcile_abandoned_unsubmitted_ghosts(
            conn,
            updated_before=started_at,
        )
        summary["abandoned_unsubmitted_ghosts"] = abandoned_ghost_summary
        summary["advanced"] += abandoned_ghost_summary["advanced"]
        summary["stayed"] += abandoned_ghost_summary["stayed"]
        summary["errors"] += abandoned_ghost_summary["errors"]

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

        local_orphan_summary = reconcile_local_orphan_no_fill_findings(conn, client)
        summary["local_orphan_no_fill_findings"] = local_orphan_summary
        summary["advanced"] += local_orphan_summary["advanced"]
        summary["stayed"] += local_orphan_summary["stayed"]
        summary["errors"] += local_orphan_summary["errors"]

        terminal_point_summary = reconcile_terminal_point_orders(conn, client)
        summary["terminal_point_orders"] = terminal_point_summary
        summary["advanced"] += terminal_point_summary["advanced"]
        summary["stayed"] += terminal_point_summary["stayed"]
        summary["errors"] += terminal_point_summary["errors"]

        cancel_ack_terminal_summary = reconcile_cancel_ack_terminal_no_fill_facts(conn)
        summary["cancel_ack_terminal_no_fill_facts"] = cancel_ack_terminal_summary
        summary["advanced"] += cancel_ack_terminal_summary["advanced"]
        summary["stayed"] += cancel_ack_terminal_summary["stayed"]
        summary["errors"] += cancel_ack_terminal_summary["errors"]

        terminal_summary = reconcile_terminal_order_facts(conn)
        summary["terminal_order_facts"] = terminal_summary
        summary["advanced"] += terminal_summary["advanced"]
        summary["stayed"] += terminal_summary["stayed"]
        summary["errors"] += terminal_summary["errors"]

        stale_terminal_summary = reconcile_stale_terminal_no_fill_findings(conn)
        summary["stale_terminal_no_fill_findings"] = stale_terminal_summary
        summary["advanced"] += stale_terminal_summary["advanced"]
        summary["stayed"] += stale_terminal_summary["stayed"]
        summary["errors"] += stale_terminal_summary["errors"]

        matched_summary = reconcile_matched_order_facts(conn, client)
        summary["matched_order_facts"] = matched_summary
        summary["advanced"] += matched_summary["advanced"]
        summary["stayed"] += matched_summary["stayed"]
        summary["errors"] += matched_summary["errors"]

        completed_partial_summary = reconcile_completed_partial_order_facts(conn)
        summary["completed_partial_order_facts"] = completed_partial_summary
        summary["advanced"] += completed_partial_summary["advanced"]
        summary["stayed"] += completed_partial_summary["stayed"]
        summary["errors"] += completed_partial_summary["errors"]

        matched_cancel_review_summary = reconcile_matched_cancel_review_required_entries(conn)
        summary["matched_cancel_review_required_entries"] = matched_cancel_review_summary
        summary["advanced"] += matched_cancel_review_summary["advanced"]
        summary["errors"] += matched_cancel_review_summary["errors"]

        edli_pre_venue_summary = _reconcile_edli_pre_venue_unknown_thresholds(conn)
        summary["edli_pre_venue_unknown_thresholds"] = edli_pre_venue_summary
        summary["advanced"] += edli_pre_venue_summary["advanced"]
        summary["stayed"] += edli_pre_venue_summary["stayed"]
        summary["errors"] += edli_pre_venue_summary["errors"]

        edli_absence_sync_summary = _reconcile_venue_command_absence_sync(conn)
        summary["venue_command_absence_sync"] = edli_absence_sync_summary
        summary["advanced"] += edli_absence_sync_summary["advanced"]
        summary["stayed"] += edli_absence_sync_summary["stayed"]
        summary["errors"] += edli_absence_sync_summary["errors"]

        edli_ack_sync_summary = reconcile_edli_acknowledged_venue_command_sync(conn)
        summary["edli_acknowledged_venue_command_sync"] = edli_ack_sync_summary
        summary["advanced"] += edli_ack_sync_summary["advanced"]
        summary["stayed"] += edli_ack_sync_summary["stayed"]
        summary["errors"] += edli_ack_sync_summary["errors"]

        live_entry_repair_summary = reconcile_live_entry_projection_repairs(conn, client=client)
        summary["live_entry_projection_repair"] = live_entry_repair_summary
        summary["advanced"] += live_entry_repair_summary["advanced"]
        summary["stayed"] += live_entry_repair_summary["stayed"]
        summary["errors"] += live_entry_repair_summary["errors"]

        filled_entry_link_summary = reconcile_filled_entry_position_link_repairs(conn)
        summary["filled_entry_position_link_repair"] = filled_entry_link_summary
        summary["advanced"] += filled_entry_link_summary["advanced"]
        summary["stayed"] += filled_entry_link_summary["stayed"]
        summary["errors"] += filled_entry_link_summary["errors"]

        filled_entry_repair_summary = reconcile_filled_entry_projection_repairs(conn, client=client)
        summary["filled_entry_projection_repair"] = filled_entry_repair_summary
        summary["advanced"] += filled_entry_repair_summary["advanced"]
        summary["stayed"] += filled_entry_repair_summary["stayed"]
        summary["errors"] += filled_entry_repair_summary["errors"]

        edli_entry_posterior_summary = reconcile_edli_entry_posterior_projection_repairs(
            conn,
            client=client,
        )
        summary["edli_entry_posterior_projection_repair"] = edli_entry_posterior_summary
        summary["advanced"] += edli_entry_posterior_summary["advanced"]
        summary["stayed"] += edli_entry_posterior_summary["stayed"]
        summary["errors"] += edli_entry_posterior_summary["errors"]

        invalid_entry_authority_summary = reconcile_invalid_open_entry_authority_quarantines(conn)
        summary["invalid_open_entry_authority_quarantine"] = invalid_entry_authority_summary
        summary["advanced"] += invalid_entry_authority_summary["advanced"]
        summary["stayed"] += invalid_entry_authority_summary["stayed"]
        summary["errors"] += invalid_entry_authority_summary["errors"]

        hard_terminal_projection_summary = reconcile_hard_terminal_position_projection_repairs(conn)
        summary["hard_terminal_position_projection_repair"] = hard_terminal_projection_summary
        summary["advanced"] += hard_terminal_projection_summary["advanced"]
        summary["stayed"] += hard_terminal_projection_summary["stayed"]
        summary["errors"] += hard_terminal_projection_summary["errors"]

        filled_entry_lot_summary = reconcile_filled_entry_position_lot_repairs(conn)
        summary["filled_entry_position_lot_repair"] = filled_entry_lot_summary
        summary["advanced"] += filled_entry_lot_summary["advanced"]
        summary["stayed"] += filled_entry_lot_summary["stayed"]
        summary["errors"] += filled_entry_lot_summary["errors"]

        filled_entry_execution_fact_summary = reconcile_filled_entry_execution_fact_repairs(conn)
        summary["filled_entry_execution_fact_repair"] = filled_entry_execution_fact_summary
        summary["advanced"] += filled_entry_execution_fact_summary["advanced"]
        summary["stayed"] += filled_entry_execution_fact_summary["stayed"]
        summary["errors"] += filled_entry_execution_fact_summary["errors"]

        exit_pending_summary = reconcile_exit_pending_projections(conn)
        summary["exit_pending_projections"] = exit_pending_summary
        summary["advanced"] += exit_pending_summary["advanced"]
        summary["stayed"] += exit_pending_summary["stayed"]
        summary["errors"] += exit_pending_summary["errors"]

        exit_lifecycle_alignment_summary = reconcile_exit_lifecycle_alignment_repairs(conn)
        summary["exit_lifecycle_alignment_repair"] = exit_lifecycle_alignment_summary
        summary["advanced"] += exit_lifecycle_alignment_summary["advanced"]
        summary["stayed"] += exit_lifecycle_alignment_summary["stayed"]
        summary["errors"] += exit_lifecycle_alignment_summary["errors"]

        spurious_panic_summary = repair_spurious_model_divergence_pending_exits(conn)
        summary["spurious_model_divergence_pending_exit_repair"] = spurious_panic_summary
        summary["advanced"] += spurious_panic_summary["advanced"]
        summary["stayed"] += spurious_panic_summary["stayed"]
        summary["errors"] += spurious_panic_summary["errors"]

        structural_win_exit_summary = repair_structural_win_pending_exits(conn)
        summary["structural_win_pending_exit_repair"] = structural_win_exit_summary
        summary["advanced"] += structural_win_exit_summary["advanced"]
        summary["stayed"] += structural_win_exit_summary["stayed"]
        summary["errors"] += structural_win_exit_summary["errors"]

        confirmed_phantom_void_summary = repair_confirmed_phantom_voids(conn)
        summary["confirmed_phantom_void_repair"] = confirmed_phantom_void_summary
        summary["advanced"] += confirmed_phantom_void_summary["advanced"]
        summary["stayed"] += confirmed_phantom_void_summary["stayed"]
        summary["errors"] += confirmed_phantom_void_summary["errors"]

        partial_summary = reconcile_partial_remainders(
            conn,
            client,
            updated_before=started_at,
        )
        summary["partial_remainders"] = partial_summary
        summary["advanced"] += partial_summary["advanced"]
        summary["stayed"] += partial_summary["stayed"]
        summary["errors"] += partial_summary["errors"]

        from src.execution.exchange_reconcile import reconcile_recorded_maker_fill_economics

        maker_fill_summary = reconcile_recorded_maker_fill_economics(
            conn,
            observed_at=started_at,
            live_tick_scope=True,
        )
        summary["recorded_maker_fill_economics"] = maker_fill_summary
        summary["advanced"] += maker_fill_summary["corrected"]
        summary["errors"] += maker_fill_summary["errors"]

        closed_shift_summary = release_closed_shift_bin_exit_leases(
            conn,
            observed_at=started_at,
        )
        summary["closed_shift_bin_exit_leases"] = closed_shift_summary
        summary["advanced"] += closed_shift_summary["advanced"]
        summary["stayed"] += closed_shift_summary["stayed"]
        summary["errors"] += closed_shift_summary["errors"]

        stale_rebalance_entry_summary = release_stale_rebalance_entry_leases(
            conn,
            observed_at=started_at,
        )
        summary["stale_rebalance_entry_leases"] = stale_rebalance_entry_summary
        summary["advanced"] += stale_rebalance_entry_summary["advanced"]
        summary["stayed"] += stale_rebalance_entry_summary["stayed"]
        summary["errors"] += stale_rebalance_entry_summary["errors"]


def _accumulate(
    summary: dict,
    key: str,
    pass_summary: dict,
    *,
    advanced_key: str = "advanced",
    fold_stayed: bool = True,
) -> None:
    """Fold a pass summary into the running total exactly as the legacy body did.

    ``fold_stayed`` mirrors the legacy asymmetry: every pass folded ``stayed``
    EXCEPT the final ``reconcile_recorded_maker_fill_economics`` pass, which only
    contributed ``corrected`` -> advanced and ``errors``.
    """
    summary[key] = pass_summary
    summary["advanced"] += pass_summary[advanced_key]
    if fold_stayed:
        summary["stayed"] += pass_summary.get("stayed", 0)
    summary["errors"] += pass_summary["errors"]


def _collect_recovery_priming_keys(conn: sqlite3.Connection, *, scope: str = "full") -> dict:
    """SNAPSHOT phase helper: gather every venue-read key the apply passes will need.

    Runs only read queries (the per-pass candidate selects + the in-flight scan)
    on a short-lived connection and returns the union of venue_order_ids,
    idempotency keys, and condition ids. Over-collection is safe; the apply-phase
    venue snapshot raises a located ``SnapshotMissError`` only if a needed key was
    NOT collected here.
    """
    order_ids: set[str] = set()
    idem_keys: set[str] = set()
    condition_ids: set[str] = set()

    def _harvest(rows) -> None:
        for row in rows or []:
            mapping = row if isinstance(row, dict) else _dict_row(row)
            for col in ("venue_order_id", "order_fact_venue_order_id"):
                val = str(mapping.get(col) or "").strip()
                if val:
                    order_ids.add(val)
            idem = str(mapping.get("idempotency_key") or "").strip()
            if idem:
                idem_keys.add(idem)
            for col in ("env_condition_id", "snapshot_condition_id"):
                cid = str(mapping.get(col) or "").strip()
                if cid:
                    condition_ids.add(cid)

    # The in-flight scan (per-row _reconcile_row venue lookups).
    try:
        _harvest(find_unresolved_commands(conn))
    except Exception:  # noqa: BLE001 — a missing table just means no candidates
        logger.debug("recovery: priming scan find_unresolved_commands failed", exc_info=True)
    if scope == "live_tick":
        for candidate_fn in (
            _local_orphan_no_fill_candidates,
            _terminal_point_order_candidates,
        ):
            try:
                _harvest(candidate_fn(conn))
            except Exception:  # noqa: BLE001
                logger.debug("recovery: priming candidate %s failed", candidate_fn.__name__, exc_info=True)
        try:
            _harvest(_partial_remainder_candidates(conn, updated_before=None))
        except Exception:  # noqa: BLE001
            logger.debug("recovery: priming candidate _partial_remainder_candidates failed", exc_info=True)
        return {
            "order_ids": order_ids,
            "idempotency_keys": idem_keys,
            "condition_ids": condition_ids,
        }

    # Each client-taking pass's candidate query.
    for candidate_fn in (
        _local_orphan_no_fill_candidates,
        _terminal_point_order_candidates,
        _latest_matched_order_fact_candidates,
        _latest_unprojected_live_entry_candidates,
        _latest_unprojected_filled_entry_candidates,
    ):
        try:
            _harvest(candidate_fn(conn))
        except Exception:  # noqa: BLE001
            logger.debug("recovery: priming candidate %s failed", candidate_fn.__name__, exc_info=True)
    try:
        _harvest(_partial_remainder_candidates(conn, updated_before=None))
    except Exception:  # noqa: BLE001
        logger.debug("recovery: priming candidate _partial_remainder_candidates failed", exc_info=True)

    return {
        "order_ids": order_ids,
        "idempotency_keys": idem_keys,
        "condition_ids": condition_ids,
    }


def _reconcile_passes_short_conn(client, summary: dict, started_at: str, *, scope: str = "full") -> None:
    """Scheduled-job lane: per-pass short connections, no connection across network.

    Three structural phases (``src.execution.venue_sync_contract``):

      1. SNAPSHOT  — one short read connection collects every venue-read key the
         client-taking passes will need (candidate order ids, idempotency keys,
         condition ids), then closes.
      2. NETWORK   — with NO connection in scope, capture the venue read surface
         (open orders, trades, per-order point reads) into an immutable
         ``VenueReadSnapshot``. This is where ALL blocking venue REST I/O happens.
      3. APPLY     — every pass runs on its OWN short-lived connection inside one
         bounded ``BEGIN IMMEDIATE ... COMMIT``. Client-taking passes receive the
         venue SNAPSHOT (zero live network), so the write lock is held only for
         that pass's writes and released the instant the pass returns.

    Each pass body is the SAME function the legacy lane calls — reconciliation
    grammar, REVIEW_REQUIRED handoffs, INV-31 invariants and savepoint discipline
    are byte-for-byte unchanged. Only the connection topology differs.
    """
    from src.execution.venue_sync_contract import (
        assert_no_open_connection,
        capture_venue_read_snapshot,
        default_trade_conn_factory,
        open_tracked,
        run_db_only_pass,
        run_three_phase,
    )

    conn_factory = default_trade_conn_factory
    lock_retry_delays = (2.0, 5.0, 10.0)

    def _run_pass_with_lock_retry(label: str, fn):
        for attempt in range(len(lock_retry_delays) + 1):
            try:
                return fn()
            except sqlite3.OperationalError as exc:
                if not str(exc).startswith("database is locked") or attempt >= len(lock_retry_delays):
                    raise
                delay = lock_retry_delays[attempt]
                logger.warning(
                    "recovery: pass %s hit database lock; retrying in %.1fs (attempt %d/%d)",
                    label,
                    delay,
                    attempt + 1,
                    len(lock_retry_delays) + 1,
                )
                time.sleep(delay)

    def _client_pass(
        label, pass_fn, summary_key, *,
        advanced_key="advanced", fold_stayed=True, client_kw=False, **pass_kwargs,
    ):
        """Run a client-taking pass as snapshot -> (shared) network -> apply.

        ``client_kw=True`` passes the venue snapshot as the ``client=`` keyword
        (the projection-repair passes' signature), else positionally.
        """

        def _snapshot(conn):
            # The candidate rows the apply pass re-queries are already primed in
            # the shared venue snapshot; this phase satisfies the contract's
            # open/close discipline and confirms no conn leaks into the network.
            return None

        def _network(_snap):
            return venue_snapshot

        def _apply(conn, snap_client):
            if client_kw:
                ps = pass_fn(conn, client=snap_client, **pass_kwargs)
            else:
                ps = pass_fn(conn, snap_client, **pass_kwargs)
            _accumulate(summary, summary_key, ps, advanced_key=advanced_key, fold_stayed=fold_stayed)
            return ps

        return _run_pass_with_lock_retry(
            label,
            lambda: run_three_phase(
                _snapshot, _network, _apply,
                conn_factory=conn_factory, label=f"recovery.{label}",
            ),
        )

    def _db_pass(label, pass_fn, summary_key, *, advanced_key="advanced", fold_stayed=True, **pass_kwargs):
        def _apply(conn):
            ps = pass_fn(conn, **pass_kwargs)
            _accumulate(summary, summary_key, ps, advanced_key=advanced_key, fold_stayed=fold_stayed)
            return ps

        return _run_pass_with_lock_retry(
            label,
            lambda: run_db_only_pass(_apply, conn_factory=conn_factory, label=f"recovery.{label}"),
        )

    # -- PHASE 1: SNAPSHOT (collect priming keys on a short read connection) ----
    with open_tracked(conn_factory, label="recovery.priming:snapshot") as conn:
        priming = _collect_recovery_priming_keys(conn, scope=scope)

    # -- PHASE 2: NETWORK (no connection in scope) -----------------------------
    assert_no_open_connection("recovery.capture_venue_snapshot")
    venue_snapshot = capture_venue_read_snapshot(
        client,
        order_ids=priming["order_ids"],
        idempotency_keys=priming["idempotency_keys"],
        condition_ids=priming["condition_ids"],
    )

    # -- PHASE 3: APPLY (each pass on its own short bounded write connection) ---
    # Order mirrors the legacy inline body exactly.
    _db_pass("edli_confirmed_legacy_command_repair",
             reconcile_edli_confirmed_legacy_command_repairs,
             "edli_confirmed_legacy_command_repair")

    _db_pass("stale_intent_created_no_submit",
             reconcile_stale_intent_created_no_submit,
             "stale_intent_created_no_submit",
             updated_before=started_at)

    _db_pass("abandoned_unsubmitted_ghosts",
             reconcile_abandoned_unsubmitted_ghosts,
             "abandoned_unsubmitted_ghosts",
             updated_before=started_at)

    # In-flight per-row scan (find_unresolved_commands + _reconcile_row).
    def _scan_inflight(conn, snap_client):
        ps = {"scanned": 0, "advanced": 0, "stayed": 0, "errors": 0}
        rows = find_unresolved_commands(conn)
        ps["scanned"] = len(rows)
        for row in rows:
            try:
                cmd = VenueCommand.from_row(row)
            except Exception as exc:
                logger.error(
                    "recovery: malformed row (command_id=%r): %s; skipping",
                    row.get("command_id"), exc,
                )
                ps["errors"] += 1
                continue
            outcome = _reconcile_row(conn, cmd, snap_client)
            if outcome == "advanced":
                ps["advanced"] += 1
            elif outcome == "stayed":
                ps["stayed"] += 1
            else:
                ps["errors"] += 1
        return ps

    def _apply_inflight(conn, snap_client):
        ps = _scan_inflight(conn, snap_client)
        summary["scanned"] = ps["scanned"]
        summary["advanced"] += ps["advanced"]
        summary["stayed"] += ps["stayed"]
        summary["errors"] += ps["errors"]
        return ps

    _run_pass_with_lock_retry(
        "inflight_scan",
        lambda: run_three_phase(
            lambda conn: None,
            lambda _snap: venue_snapshot,
            _apply_inflight,
            conn_factory=conn_factory, label="recovery.inflight_scan",
        ),
    )

    if scope == "live_tick":
        # Keep the high-cadence live tick light, but do not defer terminal
        # no-fill release for zero-exposure pending entries. A confirmed local
        # CANCEL_ACKED fact, or a pre-primed point-order terminal read, is enough
        # to clear the duplicate lock; waiting for the full sweep leaves stale
        # ghosts in the live money path.
        # Confirmed maker/exit fills are even higher priority: they close live
        # exposure and unblock close-before-open redecision leases, so run them
        # before broader terminal-order maintenance that may contend on locks.
        from src.execution.exchange_reconcile import reconcile_recorded_maker_fill_economics

        _db_pass(
            "recorded_maker_fill_economics",
            reconcile_recorded_maker_fill_economics,
            "recorded_maker_fill_economics",
            advanced_key="corrected",
            fold_stayed=False,
            observed_at=started_at,
        )
        _db_pass(
            "closed_shift_bin_exit_leases",
            release_closed_shift_bin_exit_leases,
            "closed_shift_bin_exit_leases",
            observed_at=started_at,
        )
        _db_pass(
            "stale_rebalance_entry_leases",
            release_stale_rebalance_entry_leases,
            "stale_rebalance_entry_leases",
            observed_at=started_at,
        )
        _client_pass("local_orphan_no_fill_findings",
                     reconcile_local_orphan_no_fill_findings,
                     "local_orphan_no_fill_findings")
        _client_pass("terminal_point_orders",
                     reconcile_terminal_point_orders,
                     "terminal_point_orders")
        _db_pass("cancel_ack_terminal_no_fill_facts",
                 reconcile_cancel_ack_terminal_no_fill_facts,
                 "cancel_ack_terminal_no_fill_facts")
        _db_pass(
            "terminal_order_facts",
            reconcile_terminal_order_facts,
            "terminal_order_facts",
            collect_continuations=True,
        )
        terminal_order_facts = summary.get("terminal_order_facts")
        if isinstance(terminal_order_facts, dict):
            summary["terminal_no_fill_continuations"] = list(
                terminal_order_facts.get("continuations") or []
            )
        _db_pass("edli_acknowledged_venue_command_sync",
                 reconcile_edli_acknowledged_venue_command_sync,
                 "edli_acknowledged_venue_command_sync")
        _db_pass(
            "exit_lifecycle_alignment_repair",
            reconcile_exit_lifecycle_alignment_repairs,
            "exit_lifecycle_alignment_repair",
        )
        _client_pass(
            "partial_remainders",
            reconcile_partial_remainders,
            "partial_remainders",
            updated_before=started_at,
        )
        summary["scope"] = "live_tick"
        summary["deferred_full_sweep"] = True
        return

    _client_pass("local_orphan_no_fill_findings",
                 reconcile_local_orphan_no_fill_findings, "local_orphan_no_fill_findings")
    _client_pass("terminal_point_orders",
                 reconcile_terminal_point_orders, "terminal_point_orders")
    _db_pass("cancel_ack_terminal_no_fill_facts",
             reconcile_cancel_ack_terminal_no_fill_facts, "cancel_ack_terminal_no_fill_facts")
    _db_pass("terminal_order_facts", reconcile_terminal_order_facts, "terminal_order_facts")
    _db_pass("stale_terminal_no_fill_findings",
             reconcile_stale_terminal_no_fill_findings, "stale_terminal_no_fill_findings")
    _client_pass("matched_order_facts", reconcile_matched_order_facts, "matched_order_facts")
    _db_pass("completed_partial_order_facts",
             reconcile_completed_partial_order_facts, "completed_partial_order_facts")
    _db_pass("matched_cancel_review_required_entries",
             reconcile_matched_cancel_review_required_entries,
             "matched_cancel_review_required_entries",
             fold_stayed=False)
    _db_pass("edli_pre_venue_unknown_thresholds",
             _reconcile_edli_pre_venue_unknown_thresholds, "edli_pre_venue_unknown_thresholds")
    _db_pass("venue_command_absence_sync",
             _reconcile_venue_command_absence_sync, "venue_command_absence_sync")
    _db_pass("edli_acknowledged_venue_command_sync",
             reconcile_edli_acknowledged_venue_command_sync,
             "edli_acknowledged_venue_command_sync")
    _client_pass("live_entry_projection_repair",
                 reconcile_live_entry_projection_repairs, "live_entry_projection_repair", client_kw=True)
    _db_pass("filled_entry_position_link_repair",
             reconcile_filled_entry_position_link_repairs, "filled_entry_position_link_repair")
    _client_pass("filled_entry_projection_repair",
                 reconcile_filled_entry_projection_repairs, "filled_entry_projection_repair", client_kw=True)
    _client_pass("edli_entry_posterior_projection_repair",
                 reconcile_edli_entry_posterior_projection_repairs,
                 "edli_entry_posterior_projection_repair",
                 client_kw=True)
    _db_pass("invalid_open_entry_authority_quarantine",
             reconcile_invalid_open_entry_authority_quarantines,
             "invalid_open_entry_authority_quarantine")
    _db_pass("hard_terminal_position_projection_repair",
             reconcile_hard_terminal_position_projection_repairs,
             "hard_terminal_position_projection_repair")
    _db_pass("filled_entry_position_lot_repair",
             reconcile_filled_entry_position_lot_repairs, "filled_entry_position_lot_repair")
    _db_pass("filled_entry_execution_fact_repair",
             reconcile_filled_entry_execution_fact_repairs, "filled_entry_execution_fact_repair")
    _db_pass("exit_pending_projections",
             reconcile_exit_pending_projections, "exit_pending_projections")
    _db_pass("exit_lifecycle_alignment_repair",
             reconcile_exit_lifecycle_alignment_repairs, "exit_lifecycle_alignment_repair")
    _db_pass("spurious_model_divergence_pending_exit_repair",
             repair_spurious_model_divergence_pending_exits,
             "spurious_model_divergence_pending_exit_repair")
    _db_pass("structural_win_pending_exit_repair",
             repair_structural_win_pending_exits,
             "structural_win_pending_exit_repair")
    _db_pass("confirmed_phantom_void_repair",
             repair_confirmed_phantom_voids, "confirmed_phantom_void_repair")
    _client_pass("partial_remainders",
                 reconcile_partial_remainders, "partial_remainders", updated_before=started_at)

    from src.execution.exchange_reconcile import reconcile_recorded_maker_fill_economics

    _db_pass("recorded_maker_fill_economics",
             reconcile_recorded_maker_fill_economics, "recorded_maker_fill_economics",
             advanced_key="corrected", fold_stayed=False, observed_at=started_at)
    _db_pass("closed_shift_bin_exit_leases",
             release_closed_shift_bin_exit_leases, "closed_shift_bin_exit_leases",
             observed_at=started_at)
    _db_pass("stale_rebalance_entry_leases",
             release_stale_rebalance_entry_leases, "stale_rebalance_entry_leases",
             observed_at=started_at)
