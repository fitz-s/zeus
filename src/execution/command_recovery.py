# Lifecycle: created=2026-04-26; last_reviewed=2026-05-21; last_reused=2026-05-21
# Purpose: Command recovery loop for unresolved venue command side effects.
# Reuse: Run when command recovery, venue order payload normalization, or unknown side-effect resolution changes.
# Authority basis: docs/operations/task_2026-04-26_execution_state_truth_p1_command_bus/implementation_plan.md §P1.S4
#                  + docs/operations/task_2026-05-15_live_order_e2e_goal/LIVE_ORDER_E2E_GOAL_PLAN.md
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
get_trade_connection_with_world() and closes it in a try/finally. The live
cycle path passes its already-open trade/world connection to avoid a second
connection inside the same cycle.
"""
from __future__ import annotations

import hashlib
import logging
import json
import sqlite3
from collections.abc import Mapping
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from types import SimpleNamespace
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
    append_order_fact,
    append_trade_fact,
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
_SAFE_REPLAY_MIN_AGE_SECONDS = 15 * 60


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
        or raw.get("matched")
        or raw.get("matched_amount")
        or raw.get("filled_size")
        or "0"
    )
    return str(value)


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
    if not _table_exists(conn, table):
        return set()
    return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})")}


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


def _float_or_none(value: object) -> float | None:
    parsed = _decimal_or_none(value)
    return float(parsed) if parsed is not None else None


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
                CommandState.CANCELLED.value,
                CommandState.EXPIRED.value,
            }
        )
    )
    sources = tuple(sorted(_LIVE_TERMINAL_ORDER_FACT_SOURCES))
    rows = conn.execute(
        """
        WITH latest_order_fact AS (
            SELECT command_id, MAX(local_sequence) AS max_sequence
              FROM venue_order_facts
             GROUP BY command_id
        )
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
          JOIN latest_order_fact latest
            ON latest.command_id = cmd.command_id
          JOIN venue_order_facts fact
            ON fact.command_id = latest.command_id
           AND fact.local_sequence = latest.max_sequence
          LEFT JOIN position_current pc
            ON pc.position_id = cmd.position_id
          LEFT JOIN venue_submission_envelopes env
            ON env.envelope_id = cmd.envelope_id
          LEFT JOIN executable_market_snapshots snap
            ON snap.snapshot_id = cmd.snapshot_id
         WHERE cmd.intent_kind = 'ENTRY'
           AND cmd.state IN (?, ?, ?, ?)
           AND (
                cmd.state IN ('ACKED', 'POST_ACKED')
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
        """,
        (*command_states, *states, *sources),
    ).fetchall()
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
    rows = conn.execute(
        """
        WITH latest_order_fact AS (
            SELECT command_id, MAX(local_sequence) AS max_sequence
              FROM venue_order_facts
             GROUP BY command_id
        )
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
          JOIN latest_order_fact latest
            ON latest.command_id = cmd.command_id
          JOIN venue_order_facts fact
            ON fact.command_id = latest.command_id
           AND fact.local_sequence = latest.max_sequence
         WHERE finding.kind = 'local_orphan_order'
           AND finding.resolved_at IS NULL
           AND cmd.intent_kind = 'ENTRY'
           AND cmd.state IN (?, ?)
         ORDER BY finding.recorded_at, finding.finding_id
        """,
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
    rows = conn.execute(
        f"""
        WITH latest_order_fact AS (
            SELECT command_id, MAX(local_sequence) AS max_sequence
              FROM venue_order_facts
             GROUP BY command_id
        )
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
          JOIN position_current pc
            ON pc.position_id = cmd.position_id
          JOIN latest_order_fact latest
            ON latest.command_id = cmd.command_id
          JOIN venue_order_facts fact
            ON fact.command_id = latest.command_id
           AND fact.local_sequence = latest.max_sequence
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
        """,
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
    fact_states = ("LIVE", "RESTING", "MATCHED", "PARTIALLY_MATCHED")
    rows = conn.execute(
        """
        WITH latest_order_fact AS (
            SELECT command_id, MAX(local_sequence) AS max_sequence
              FROM venue_order_facts
             GROUP BY command_id
        )
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
          JOIN latest_order_fact latest
            ON latest.command_id = cmd.command_id
          JOIN venue_order_facts fact
            ON fact.command_id = latest.command_id
           AND fact.local_sequence = latest.max_sequence
         WHERE cmd.intent_kind IN ('ENTRY', 'EXIT')
           AND cmd.state IN (?, ?)
           AND fact.state IN (?, ?, ?, ?)
           AND fact.source IN (?, ?, ?, ?, ?)
           AND cmd.venue_order_id IS NOT NULL
        """,
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
    rows = conn.execute(
        f"""
        WITH latest_order_fact AS (
            SELECT command_id, MAX(local_sequence) AS max_sequence
              FROM venue_order_facts
             GROUP BY command_id
        )
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
          JOIN latest_order_fact latest
            ON latest.command_id = cmd.command_id
          JOIN venue_order_facts fact
            ON fact.command_id = latest.command_id
           AND fact.local_sequence = latest.max_sequence
         WHERE cmd.intent_kind = 'ENTRY'
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
        """,
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
    rows = conn.execute(
        """
        WITH latest_trade_fact AS (
            SELECT command_id, trade_id, MAX(local_sequence) AS max_sequence
              FROM venue_trade_facts
             WHERE command_id = ?
               AND venue_order_id = ?
             GROUP BY command_id, trade_id
        )
        SELECT fact.filled_size
          FROM venue_trade_facts fact
          JOIN latest_trade_fact latest
            ON latest.command_id = fact.command_id
           AND latest.trade_id = fact.trade_id
           AND latest.max_sequence = fact.local_sequence
         WHERE fact.command_id = ?
           AND fact.venue_order_id = ?
           AND fact.state IN ('MATCHED', 'MINED', 'CONFIRMED')
        """,
        (command_id, venue_order_id, command_id, venue_order_id),
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


def _point_order_matched_size(point_order: dict | None, *, fallback: object = None) -> str:
    value = _first_present(
        point_order,
        "matched_size",
        "matchedSize",
        "size_matched",
        "sizeMatched",
        "takingAmount",
        "taking_amount",
    )
    if value not in (None, ""):
        return str(value)
    return str(fallback or "0")


def _point_order_fill_price(point_order: dict | None, *, fallback: object = None) -> str:
    making = _decimal_or_none(_first_present(point_order, "makingAmount", "making_amount"))
    taking = _decimal_or_none(_first_present(point_order, "takingAmount", "taking_amount"))
    if making is not None and taking is not None and making > 0 and taking > 0:
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


def _decimal_text(value: Decimal) -> str:
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _positive_fill_trade_fact_summary(conn: sqlite3.Connection, command_id: str) -> dict:
    if not _table_exists(conn, "venue_trade_facts"):
        return {"count": 0, "filled_size": "0"}
    rows = conn.execute(
        """
        WITH latest_trade_fact AS (
            SELECT trade_id, MAX(local_sequence) AS max_sequence
              FROM venue_trade_facts
             WHERE command_id = ?
             GROUP BY trade_id
        )
        SELECT fact.filled_size
          FROM venue_trade_facts fact
          JOIN latest_trade_fact latest
            ON latest.trade_id = fact.trade_id
           AND latest.max_sequence = fact.local_sequence
         WHERE fact.command_id = ?
           AND fact.state IN ('MATCHED', 'MINED', 'CONFIRMED')
        """,
        (command_id, command_id),
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
    rows = conn.execute(
        """
        WITH latest_trade_fact AS (
            SELECT command_id, trade_id, MAX(local_sequence) AS max_sequence
              FROM venue_trade_facts
             GROUP BY command_id, trade_id
        ),
        entry_fill AS (
            SELECT fact.command_id,
                   COUNT(*) AS fill_fact_count,
                   SUM(CAST(fact.filled_size AS REAL)) AS filled_size,
                   SUM(CAST(fact.filled_size AS REAL) * CAST(fact.fill_price AS REAL))
                       / SUM(CAST(fact.filled_size AS REAL)) AS fill_price,
                   MAX(fact.observed_at) AS observed_at,
                   GROUP_CONCAT(DISTINCT fact.state) AS fill_states,
                   MAX(fact.trade_fact_id) AS trade_fact_id
              FROM venue_trade_facts fact
              JOIN latest_trade_fact latest
                ON latest.command_id = fact.command_id
               AND latest.trade_id = fact.trade_id
               AND latest.max_sequence = fact.local_sequence
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
         ORDER BY entry_fill.observed_at, cmd.command_id
        """
    ).fetchall()
    return [_dict_row(row) for row in rows]


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
    rows = conn.execute(
        """
        WITH latest_order_fact AS (
            SELECT command_id, venue_order_id, MAX(local_sequence) AS max_sequence
              FROM venue_order_facts
             GROUP BY command_id, venue_order_id
        ),
        live_order AS (
            SELECT fact.command_id,
                   fact.venue_order_id,
                   fact.fact_id AS order_fact_id,
                   fact.state AS order_fact_state,
                   fact.remaining_size AS order_fact_remaining_size,
                   fact.matched_size AS order_fact_matched_size,
                   fact.source AS order_fact_source,
                   fact.observed_at AS order_fact_observed_at
              FROM venue_order_facts fact
              JOIN latest_order_fact latest
                ON latest.command_id = fact.command_id
               AND latest.venue_order_id = fact.venue_order_id
               AND latest.max_sequence = fact.local_sequence
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
                 FROM venue_trade_facts trade_fact
                WHERE trade_fact.command_id = cmd.command_id
                  AND trade_fact.state IN ('MATCHED', 'MINED', 'CONFIRMED')
                  AND CAST(COALESCE(trade_fact.filled_size, '0') AS REAL) > 0
           )
         ORDER BY live_order.order_fact_observed_at, cmd.command_id
        """
    ).fetchall()
    return [_dict_row(row) for row in rows]


def _decision_log_trade_case_for_command(conn: sqlite3.Connection, command: dict) -> tuple[dict, int | None]:
    if not _table_exists(conn, "decision_log"):
        return {}, None
    position_id = str(command.get("position_id") or "")
    decision_id = str(command.get("decision_id") or "")
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
                return case, int(record.get("id") or 0)
    return {}, None


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
) -> None:
    from src.engine.lifecycle_events import build_position_current_projection
    from src.state.ledger import append_many_and_project
    from src.state.projection import upsert_position_current

    trade_case, decision_log_id = _decision_log_trade_case_for_command(conn, candidate)
    if not trade_case:
        raise ValueError("filled entry projection repair requires matching decision_log trade_case")
    position = _filled_entry_recovery_position(
        candidate,
        trade_case,
        decision_log_id=decision_log_id,
    )
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
        return
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


def _append_live_entry_projection_repair(
    conn: sqlite3.Connection,
    *,
    candidate: dict,
) -> None:
    from src.engine.lifecycle_events import build_position_current_projection
    from src.state.ledger import append_many_and_project
    from src.state.projection import upsert_position_current

    trade_case, decision_log_id = _decision_log_trade_case_for_command(conn, candidate)
    if not trade_case:
        raise ValueError("live entry projection repair requires matching decision_log trade_case")
    position = _live_entry_recovery_position(
        candidate,
        trade_case,
        decision_log_id=decision_log_id,
    )
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
        return
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


def reconcile_live_entry_projection_repairs(conn: sqlite3.Connection) -> dict:
    """Repair open ACKED ENTRY command truth when initial pending projection failed."""

    summary = {"scanned": 0, "advanced": 0, "stayed": 0, "errors": 0}
    for candidate in _latest_unprojected_live_entry_candidates(conn):
        summary["scanned"] += 1
        command_id = str(candidate.get("command_id") or "")
        conn.execute("SAVEPOINT sp_live_entry_projection_repair")
        try:
            _append_live_entry_projection_repair(conn, candidate=candidate)
            conn.execute("RELEASE SAVEPOINT sp_live_entry_projection_repair")
            summary["advanced"] += 1
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


def reconcile_filled_entry_projection_repairs(conn: sqlite3.Connection) -> dict:
    """Repair filled ENTRY command truth when initial position projection failed."""

    summary = {"scanned": 0, "advanced": 0, "stayed": 0, "errors": 0}
    for candidate in _latest_unprojected_filled_entry_candidates(conn):
        summary["scanned"] += 1
        command_id = str(candidate.get("command_id") or "")
        safe_command_id = "".join(ch if ch.isalnum() else "_" for ch in command_id)
        sp_name = f"sp_filled_entry_projection_{safe_command_id}"
        conn.execute(f"SAVEPOINT {sp_name}")
        try:
            _append_filled_entry_projection_repair(conn, candidate=candidate)
            conn.execute(f"RELEASE SAVEPOINT {sp_name}")
            summary["advanced"] += 1
        except Exception as exc:
            conn.execute(f"ROLLBACK TO SAVEPOINT {sp_name}")
            conn.execute(f"RELEASE SAVEPOINT {sp_name}")
            logger.error(
                "recovery: filled entry projection repair failed for command %s: %s",
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
    rows = conn.execute(
        """
        WITH latest_trade_fact AS (
            SELECT command_id, trade_id, MAX(local_sequence) AS max_sequence
              FROM venue_trade_facts
             GROUP BY command_id, trade_id
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
              FROM venue_trade_facts fact
              JOIN latest_trade_fact latest
                ON latest.command_id = fact.command_id
               AND latest.trade_id = fact.trade_id
               AND latest.max_sequence = fact.local_sequence
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
          JOIN position_current pc
            ON pc.position_id = cmd.position_id
          JOIN venue_trade_facts fact
            ON fact.command_id = cmd.command_id
          JOIN latest_trade_fact latest
            ON latest.command_id = fact.command_id
           AND latest.trade_id = fact.trade_id
           AND latest.max_sequence = fact.local_sequence
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
    ).fetchall()
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
        "position_lots",
        "execution_fact",
        "trade_decisions",
    }
    if not all(_table_exists(conn, table) for table in required):
        return []
    rows = conn.execute(
        """
        WITH latest_trade_fact AS (
            SELECT command_id, trade_id, MAX(local_sequence) AS max_sequence
              FROM venue_trade_facts
             GROUP BY command_id, trade_id
        ),
        latest_order_fact AS (
            SELECT command_id, MAX(local_sequence) AS max_sequence
              FROM venue_order_facts
             GROUP BY command_id
        ),
        latest_order AS (
            SELECT fact.command_id,
                   fact.venue_order_id,
                   fact.remaining_size,
                   fact.matched_size,
                   fact.state
              FROM venue_order_facts fact
              JOIN latest_order_fact latest
                ON latest.command_id = fact.command_id
               AND latest.max_sequence = fact.local_sequence
            WHERE fact.state IN ('MATCHED', 'FILLED')
               AND CAST(COALESCE(fact.remaining_size, '0') AS REAL) = 0
               AND CAST(COALESCE(fact.matched_size, '0') AS REAL) > 0
            UNION ALL
            SELECT fact.command_id,
                   fact.venue_order_id,
                   fact.remaining_size,
                   fact.matched_size,
                   fact.state
              FROM venue_order_facts fact
              JOIN latest_order_fact latest
                ON latest.command_id = fact.command_id
               AND latest.max_sequence = fact.local_sequence
             WHERE fact.state IN ('PARTIALLY_MATCHED', 'EXPIRED', 'CANCEL_CONFIRMED')
               AND CAST(COALESCE(fact.matched_size, '0') AS REAL) > 0
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
              FROM venue_trade_facts fact
              JOIN latest_trade_fact latest
                ON latest.command_id = fact.command_id
               AND latest.trade_id = fact.trade_id
               AND latest.max_sequence = fact.local_sequence
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
           AND EXISTS (
               SELECT 1
                 FROM position_lots lot
                WHERE lot.source_command_id = cmd.command_id
                  AND lot.state IN ('OPTIMISTIC_EXPOSURE', 'CONFIRMED_EXPOSURE')
           )
           AND EXISTS (
               SELECT 1
                 FROM trade_decisions td
                WHERE td.runtime_trade_id = cmd.position_id
                   OR CAST(td.trade_id AS TEXT) = cmd.position_id
                   OR CAST(td.trade_id AS TEXT) = cmd.decision_id
           )
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
    ).fetchall()
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
    remaining = _positive_decimal_or_none(candidate.get("order_fact_remaining_size"))
    order_state = str(candidate.get("order_fact_state") or "").upper()
    command_state = str(candidate.get("cmd_state") or candidate.get("state") or "").upper()
    if (
        command_state == CommandState.FILLED.value
        and remaining is None
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
        conn.execute(f"SAVEPOINT {sp_name}")
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
    rows = conn.execute(
        f"""
        WITH latest_trade_fact AS (
            SELECT command_id, trade_id, MAX(local_sequence) AS max_sequence
              FROM venue_trade_facts
             GROUP BY command_id, trade_id
        ),
        exit_fill AS (
            SELECT fact.command_id,
                   COUNT(*) AS fill_fact_count,
                   SUM(CAST(COALESCE(fact.filled_size, '0') AS REAL)) AS filled_size,
                   GROUP_CONCAT(DISTINCT fact.state) AS fill_states,
                   MAX(fact.observed_at) AS observed_at
              FROM venue_trade_facts fact
              JOIN latest_trade_fact latest
                ON latest.command_id = fact.command_id
               AND latest.trade_id = fact.trade_id
               AND latest.max_sequence = fact.local_sequence
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
        """,
        (
            *sorted(_EXIT_PENDING_PROJECTION_TRADE_STATES),
            *sorted(_EXIT_PENDING_PROJECTION_COMMAND_STATES),
        ),
    ).fetchall()
    return [_dict_row(row) for row in rows]


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

    MATCHED/MINED exit trade facts prove a sell-side venue side effect, but not
    economic-close finality.  The canonical position projection must therefore
    leave P&L untouched while preventing reload from treating the row as a
    normal active position eligible for another full sell attempt.
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


def _latest_terminal_remainder_order_fact_exists(
    conn: sqlite3.Connection,
    *,
    command_id: str,
) -> bool:
    if not _table_exists(conn, "venue_order_facts"):
        return False
    row = conn.execute(
        """
        SELECT state, remaining_size, matched_size, source
          FROM venue_order_facts
         WHERE command_id = ?
         ORDER BY local_sequence DESC, fact_id DESC
         LIMIT 1
        """,
        (command_id,),
    ).fetchone()
    data = _dict_row(row)
    return (
        str(data.get("state") or "") in _TERMINAL_NO_FILL_ORDER_FACT_STATES
        and str(data.get("source") or "") in _LIVE_TERMINAL_ORDER_FACT_SOURCES
        and _decimal_is_zero(data.get("remaining_size"))
        and _decimal_is_positive(data.get("matched_size"))
    )


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
        _append_live_entry_projection_repair(conn, candidate={**command, **order_fact})
        current = _position_current_for_terminal_order(conn, command=command, order_id=order_id)
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


def _entry_projection_is_pending_zero_exposure(
    conn: sqlite3.Connection,
    *,
    command: dict,
    order_id: str,
) -> bool:
    try:
        current = _position_current_for_terminal_order(conn, command=command, order_id=order_id)
    except (MissingPositionCurrentForTerminalOrder, ValueError):
        return False
    return (
        str(current.get("phase") or "") == "pending_entry"
        and _decimal_is_zero(current.get("shares"))
        and _decimal_is_zero(current.get("cost_basis_usd"))
    )


def reconcile_terminal_order_facts(conn: sqlite3.Connection) -> dict:
    """Close ACKED entry commands whose latest venue order fact is terminal no-fill."""

    summary = {"scanned": 0, "advanced": 0, "stayed": 0, "errors": 0}
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
            )
            if not _positive_decimal_or_none(matched_size):
                summary["stayed"] += 1
                continue
            fill_price = _point_order_fill_price(point_order, fallback=row.get("price"))
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
    """Finalize PARTIAL entry commands when order truth says the remainder is gone."""

    summary = {"scanned": 0, "advanced": 0, "stayed": 0, "errors": 0}
    for row in _latest_completed_partial_order_fact_candidates(conn):
        summary["scanned"] += 1
        command_id = str(row.get("command_id") or "")
        order_id = str(row.get("order_fact_venue_order_id") or row.get("venue_order_id") or "")
        try:
            payload = {
                "reason": "partial_entry_order_fact_completed",
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
                    occurred_at=_now_iso(),
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
                "recovery: completed partial order fact reconciliation failed for command %s: %s",
                command_id,
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
    rows = conn.execute(
        f"""
        SELECT *
          FROM venue_commands
         WHERE intent_kind = 'ENTRY'
           AND state IN ({state_placeholders})
           AND COALESCE(venue_order_id, '') != ''
           AND (? IS NULL OR updated_at < ?)
         ORDER BY updated_at, command_id
        """,
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
        if not _raw_matches_command_exposure(raw, command):
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
) -> tuple[int, dict]:
    command_id = str(command.get("command_id") or "")
    venue_order_id = str(command.get("venue_order_id") or "")
    fact_state = _terminal_fact_state_for_venue_status(
        venue_status,
        venue_resp_present=point_order is not None,
    )
    if fact_state is None:
        raise ValueError(f"venue status is not terminal no-fill: {venue_status!r}")
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
        "required_predicates": {
            "point_order_terminal_no_fill": True,
            "point_order_matched_size_zero": True,
            "no_local_trade_facts": _trade_fact_count(conn, command_id) == 0,
            "no_matching_open_orders": len(matching_open_orders) == 0,
            "no_matching_trades": len(matching_trades) == 0,
        },
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
    rows = conn.execute(
        f"""
        WITH latest_order_fact AS (
            SELECT command_id, MAX(local_sequence) AS max_sequence
              FROM venue_order_facts
             GROUP BY command_id
        )
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
          JOIN latest_order_fact latest
            ON latest.command_id = cmd.command_id
          JOIN venue_order_facts fact
            ON fact.command_id = latest.command_id
           AND fact.local_sequence = latest.max_sequence
          JOIN position_current pc
            ON pc.position_id = cmd.position_id
         WHERE cmd.intent_kind = 'ENTRY'
           AND cmd.state IN ({state_placeholders})
           AND COALESCE(cmd.venue_order_id, '') != ''
           AND pc.phase = 'pending_entry'
           AND CAST(COALESCE(pc.shares, '0') AS REAL) = 0
           AND CAST(COALESCE(pc.cost_basis_usd, '0') AS REAL) = 0
           AND fact.state IN ('LIVE', 'RESTING')
           AND CAST(COALESCE(fact.matched_size, '0') AS REAL) = 0
         ORDER BY cmd.updated_at, cmd.command_id
        """,
        command_states,
    ).fetchall()
    return [_dict_row(row) for row in rows]


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
            matched_size = _point_order_matched_size(point_order, fallback=row.get("order_fact_matched_size") or "0")
            if _is_positive_decimal(matched_size):
                summary["stayed"] += 1
                continue
            matching_open_orders = _matching_open_orders_for_command(client, row, open_orders=open_orders)
            matching_trades = _matching_trades_for_command(client, row, trades=trades)
            if matching_open_orders or matching_trades:
                summary["stayed"] += 1
                continue
            _append_point_order_terminal_no_fill_fact(
                conn,
                command=row,
                observed_at=_now_iso(),
                venue_status=venue_status,
                point_order=point_order,
                matching_open_orders=matching_open_orders,
                matching_trades=matching_trades,
                source_reason="acked_point_order_terminal_no_fill",
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
            if _latest_terminal_remainder_order_fact_exists(conn, command_id=command_id):
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
                if point_status == "FILLED":
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
                        "recovery: command %s PARTIAL absent from open orders but point order is FILLED "
                        "without complete trade-fact authority -> REVIEW_REQUIRED",
                        command_id,
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


def _latest_cancel_unknown_payload(events: list[dict]) -> dict | None:
    latest_event_type, latest_payload = _latest_event_payload(events)
    if latest_event_type != CommandEventType.CANCEL_REPLACE_BLOCKED.value:
        return None
    if str(latest_payload.get("semantic_cancel_status") or "").upper() != "CANCEL_UNKNOWN":
        return None
    if latest_payload.get("requires_m5_reconcile") is not True:
        return None
    return latest_payload


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
    order = _venue_order_payload(raw_order) or {}
    if not order:
        return "stayed"
    order_id = _extract_order_id(order)
    status = _order_status(order)
    matched_size = _order_matched_size(order)
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
            and not _is_positive_decimal(_point_order_matched_size(order, fallback=matched_size))
            and _trade_fact_count(conn, cmd.command_id) == 0
        ):
            command = _dict_row(
                conn.execute(
                    "SELECT * FROM venue_commands WHERE command_id = ?",
                    (cmd.command_id,),
                ).fetchone()
            )
            if not _entry_projection_is_pending_zero_exposure(
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
    return payload


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
        if _raw_matches_command_exposure(raw, command)
    ]
    matching_trades = []
    for trade in trades:
        raw = _raw_payload(trade)
        if not _raw_matches_command_exposure(raw, command):
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
        """
        SELECT *
        FROM venue_trade_facts
        WHERE command_id = ?
          AND trade_id = ?
          AND venue_order_id = ?
          AND state IN ('MATCHED', 'MINED', 'CONFIRMED')
        ORDER BY local_sequence DESC, trade_fact_id DESC
        LIMIT 1
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
            return _review_required_cancel_unknown_live_order_recovery(conn, cmd, client)

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
    internally (with a try/finally to close). CycleRunner passes the per-cycle
    trade/world connection; the internal-open path remains the external-caller
    fallback.

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
    started_at = _now_iso()

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

        live_entry_repair_summary = reconcile_live_entry_projection_repairs(conn)
        summary["live_entry_projection_repair"] = live_entry_repair_summary
        summary["advanced"] += live_entry_repair_summary["advanced"]
        summary["stayed"] += live_entry_repair_summary["stayed"]
        summary["errors"] += live_entry_repair_summary["errors"]

        filled_entry_repair_summary = reconcile_filled_entry_projection_repairs(conn)
        summary["filled_entry_projection_repair"] = filled_entry_repair_summary
        summary["advanced"] += filled_entry_repair_summary["advanced"]
        summary["stayed"] += filled_entry_repair_summary["stayed"]
        summary["errors"] += filled_entry_repair_summary["errors"]

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
        )
        summary["recorded_maker_fill_economics"] = maker_fill_summary
        summary["advanced"] += maker_fill_summary["corrected"]
        summary["errors"] += maker_fill_summary["errors"]

    finally:
        if own_conn:
            conn.close()

    logger.info(
        "recovery: scanned=%d advanced=%d stayed=%d errors=%d",
        summary["scanned"], summary["advanced"], summary["stayed"], summary["errors"],
    )
    return summary
