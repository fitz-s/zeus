# Created: 2026-04-26
# Last reused/audited: 2026-05-16
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

import hashlib
import logging
import json
import sqlite3
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
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
_TERMINAL_NO_FILL_ORDER_FACT_STATES = frozenset({
    "CANCEL_CONFIRMED",
    "EXPIRED",
    "VENUE_WIPED",
})
_LIVE_TERMINAL_ORDER_FACT_SOURCES = frozenset({
    "REST",
    "WS_USER",
    "WS_MARKET",
    "DATA_API",
    "CHAIN",
})
_ACKED_ORDER_STATES = frozenset({
    CommandState.ACKED.value,
    CommandState.POST_ACKED.value,
})
_PARTIAL_REMAINDER_STATES = frozenset({
    CommandState.PARTIAL.value,
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
    row = conn.execute(
        f"SELECT COUNT(*) AS count FROM {table} WHERE command_id = ?",
        (command_id,),
    ).fetchone()
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


def _latest_terminal_order_fact_candidates(conn: sqlite3.Connection) -> list[dict]:
    if not _table_exists(conn, "venue_order_facts"):
        return []
    states = tuple(_TERMINAL_NO_FILL_ORDER_FACT_STATES)
    command_states = tuple(_ACKED_ORDER_STATES)
    sources = tuple(_LIVE_TERMINAL_ORDER_FACT_SOURCES)
    state_placeholders = ",".join("?" for _ in states)
    command_state_placeholders = ",".join("?" for _ in command_states)
    source_placeholders = ",".join("?" for _ in sources)
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
            fact.source AS order_fact_source,
            fact.raw_payload_hash AS order_fact_raw_payload_hash
          FROM venue_commands cmd
          JOIN latest_order_fact latest
            ON latest.command_id = cmd.command_id
          JOIN venue_order_facts fact
            ON fact.command_id = latest.command_id
           AND fact.local_sequence = latest.max_sequence
         WHERE cmd.intent_kind = 'ENTRY'
           AND cmd.state IN ({command_state_placeholders})
           AND fact.state IN ({state_placeholders})
           AND fact.source IN ({source_placeholders})
        """,
        (*command_states, *states, *sources),
    ).fetchall()
    return [_dict_row(row) for row in rows]


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
        where_sql = "position_id = ? AND order_id = ?"
        params = (position_id, order_id)
    elif has_position_id:
        where_sql = "position_id = ?"
        params = (position_id,)
    elif has_order_id:
        where_sql = "order_id = ?"
        params = (order_id,)
    else:
        raise ValueError("cannot locate position_current without position_id or order_id")
    row = conn.execute(
        f"SELECT * FROM position_current WHERE {where_sql} LIMIT 1",
        params,
    ).fetchone()
    if row is None:
        raise ValueError("terminal order fact has no matching position_current row")
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


def _resolve_m5_local_orphan_findings(
    conn: sqlite3.Connection,
    *,
    venue_order_id: str,
    resolved_at: str,
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
    from src.execution.exchange_reconcile import resolve_finding

    resolved = 0
    for row in rows:
        resolve_finding(
            conn,
            str(_dict_row(row)["finding_id"]),
            resolution="command_recovery_expired_partial_remainder",
            resolved_by="src.execution.command_recovery",
            resolved_at=resolved_at,
        )
        resolved += 1
    return resolved


def _payload_hash(payload: dict) -> str:
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _append_partial_remainder_terminal_order_fact(
    conn: sqlite3.Connection,
    *,
    command: dict,
    observed_at: str,
    matched_size: str,
) -> int:
    command_id = str(command.get("command_id") or "")
    venue_order_id = str(command.get("venue_order_id") or "")
    payload = {
        "reason": "partial_remainder_absent_from_exchange_open_orders",
        "proof_class": "confirmed_fill_plus_open_order_absence",
        "source_surface": "client.get_open_orders",
        "venue_order_id": venue_order_id,
        "command_id": command_id,
        "open_order_absent": True,
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
        try:
            if venue_order_id in open_order_ids:
                summary["stayed"] += 1
                continue
            now = _now_iso()
            fill_summary = _positive_fill_trade_fact_summary(conn, command_id)
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
            safe_command_id = "".join(ch if ch.isalnum() else "_" for ch in command_id)
            sp_name = f"sp_partial_remainder_{safe_command_id}"
            conn.execute(f"SAVEPOINT {sp_name}")
            try:
                order_fact_id = _append_partial_remainder_terminal_order_fact(
                    conn,
                    command=command,
                    observed_at=now,
                    matched_size=fill_summary["filled_size"],
                )
                resolved_findings = _resolve_m5_local_orphan_findings(
                    conn,
                    venue_order_id=venue_order_id,
                    resolved_at=now,
                )
                append_event(
                    conn,
                    command_id=command_id,
                    event_type=CommandEventType.EXPIRED.value,
                    occurred_at=now,
                    payload={
                        "reason": "partial_remainder_absent_from_exchange_open_orders",
                        "venue_order_id": venue_order_id,
                        "venue_order_fact_id": order_fact_id,
                        "proof_class": "confirmed_fill_plus_open_order_absence",
                        "positive_fill_trade_fact_count": fill_summary["count"],
                        "positive_fill_size": fill_summary["filled_size"],
                        "resolved_m5_local_orphan_findings": resolved_findings,
                    },
                )
                conn.execute(f"RELEASE SAVEPOINT {sp_name}")
            except Exception:
                conn.execute(f"ROLLBACK TO SAVEPOINT {sp_name}")
                conn.execute(f"RELEASE SAVEPOINT {sp_name}")
                raise
            logger.info(
                "recovery: command %s PARTIAL remainder -> EXPIRED "
                "(venue_order_id=%s; fill_trade_facts=%d; resolved_findings=%d)",
                command_id,
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

        terminal_summary = reconcile_terminal_order_facts(conn)
        summary["terminal_order_facts"] = terminal_summary
        summary["advanced"] += terminal_summary["advanced"]
        summary["stayed"] += terminal_summary["stayed"]
        summary["errors"] += terminal_summary["errors"]

        partial_summary = reconcile_partial_remainders(
            conn,
            client,
            updated_before=started_at,
        )
        summary["partial_remainders"] = partial_summary
        summary["advanced"] += partial_summary["advanced"]
        summary["stayed"] += partial_summary["stayed"]
        summary["errors"] += partial_summary["errors"]

    finally:
        if own_conn:
            conn.close()

    logger.info(
        "recovery: scanned=%d advanced=%d stayed=%d errors=%d",
        summary["scanned"], summary["advanced"], summary["stayed"], summary["errors"],
    )
    return summary
