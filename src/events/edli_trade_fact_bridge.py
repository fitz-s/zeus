"""Bridge authenticated execution trade facts back into EDLI live-order events."""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.events.live_order_aggregate import LiveOrderAggregateError, LiveOrderAggregateLedger
from src.events.live_order_reconcile import (
    append_reconcile_recovered_fill,
    append_user_trade_observed,
)

logger = logging.getLogger(__name__)


def append_confirmed_trade_facts_to_edli(
    conn: sqlite3.Connection,
    *,
    now: datetime | None = None,
    limit: int = 100,
    trade_db_path: str | Path | None = None,
) -> int:
    """Append missing EDLI UserTradeObserved events from confirmed WS trade facts.

    The source of authority remains the authenticated user channel: this bridge
    only consumes ``venue_trade_facts`` rows written as ``source='WS_USER'`` and
    ``state='CONFIRMED'``. It also requires the trade fact to bind to the EDLI
    execution command and the acknowledged venue order for the same aggregate.
    """

    _ensure_trades_attached_if_needed(conn, trade_db_path=trade_db_path)
    trade_schema = _schema_with_table(conn, "venue_trade_facts", preferred="trades")
    if trade_schema is None or not _table_exists(conn, "edli_live_order_events"):
        return 0
    venue_trade_facts = _q(trade_schema, "venue_trade_facts")
    venue_commands = _q(trade_schema, "venue_commands")

    rows = conn.execute(
        f"""
        WITH execution_commands AS (
            SELECT aggregate_id,
                   json_extract(payload_json, '$.event_id') AS event_id,
                   json_extract(payload_json, '$.final_intent_id') AS final_intent_id,
                   json_extract(payload_json, '$.execution_command_id') AS execution_command_id
              FROM edli_live_order_events
             WHERE event_type = 'ExecutionCommandCreated'
        ),
        submit_acks AS (
            SELECT aggregate_id,
                   json_extract(payload_json, '$.venue_order_id') AS venue_order_id
              FROM edli_live_order_events
             WHERE event_type = 'VenueSubmitAcknowledged'
        )
        SELECT exec.aggregate_id,
               exec.event_id,
               exec.final_intent_id,
               exec.execution_command_id,
               ack.venue_order_id AS acknowledged_venue_order_id,
               cmd.command_id,
               trade.trade_fact_id,
               trade.trade_id,
               trade.venue_order_id,
               trade.state,
               trade.filled_size,
               trade.fill_price,
               trade.tx_hash,
               trade.observed_at,
               trade.raw_payload_hash,
               trade.raw_payload_json
          FROM execution_commands exec
          JOIN submit_acks ack
            ON ack.aggregate_id = exec.aggregate_id
          JOIN {venue_commands} cmd
            ON cmd.decision_id = exec.execution_command_id
          JOIN {venue_trade_facts} trade
            ON trade.command_id = cmd.command_id
           AND trade.venue_order_id = ack.venue_order_id
         WHERE UPPER(COALESCE(trade.state, '')) = 'CONFIRMED'
           AND trade.source = 'WS_USER'
           AND CAST(COALESCE(trade.filled_size, '0') AS REAL) > 0
           AND CAST(COALESCE(trade.fill_price, '0') AS REAL) > 0
           AND NOT EXISTS (
                 SELECT 1
                   FROM edli_live_order_events existing
                  WHERE existing.aggregate_id = exec.aggregate_id
                    AND existing.event_type = 'UserTradeObserved'
                    AND json_extract(existing.payload_json, '$.trade_id') = trade.trade_id
                    AND json_extract(existing.payload_json, '$.fill_authority_state') = 'FILL_CONFIRMED'
               )
         ORDER BY trade.observed_at ASC, trade.trade_fact_id ASC
         LIMIT ?
        """,
        (max(0, limit),),
    ).fetchall()

    ledger = LiveOrderAggregateLedger(conn)
    appended = 0
    default_now = now or datetime.now(timezone.utc)
    for row in rows:
        observed_at = _parse_dt(_row_get(row, "observed_at"), default=default_now)
        message_hash = _message_hash(row)
        append_user_trade_observed(
            ledger,
            aggregate_id=str(_row_get(row, "aggregate_id")),
            event_id=str(_row_get(row, "event_id")),
            final_intent_id=str(_row_get(row, "final_intent_id")),
            source="polymarket_user_channel",
            trade_status="CONFIRMED",
            venue_order_id=str(_row_get(row, "venue_order_id")),
            occurred_at=observed_at,
            payload={
                "raw_user_channel_message_hash": message_hash,
                "trade_id": str(_row_get(row, "trade_id")),
                "filled_size": str(_row_get(row, "filled_size")),
                "fill_price": str(_row_get(row, "fill_price")),
                "avg_fill_price": str(_row_get(row, "fill_price")),
                "transaction_hash": _row_get(row, "tx_hash"),
                "source_trade_fact_id": int(_row_get(row, "trade_fact_id")),
                "source_trade_fact_authority": "venue_trade_facts:WS_USER:CONFIRMED",
            },
        )
        appended += 1
    return appended


def append_rest_filled_orphan_trade_facts_to_edli(
    conn: sqlite3.Connection,
    *,
    now: datetime | None = None,
    grace_minutes: float = 15.0,
    limit: int = 50,
    trade_db_path: str | Path | None = None,
) -> int:
    """Recover fill orphans whose WS_USER CONFIRMED message never arrived.

    THE ORPHAN CLASS (HK 30°C 2026-06-12 incident): the user channel dropped
    for ~3h; a real venue fill exists only as a REST-sourced trade fact
    (state MATCHED) under a venue command in terminal FILLED/PARTIAL state.
    ``append_confirmed_trade_facts_to_edli`` requires WS_USER+CONFIRMED, so
    the fill can never reach FILL_CONFIRMED, the position is never
    materialised, and the P&L is never booked.

    Recovery contract (explicit reconcile authority, RECONCILE_SOURCE):
    - the trade fact has filled_size > 0 and fill_price > 0;
    - the owning venue command is in a terminal fill state (FILLED/PARTIAL);
    - the fact is OLDER than ``grace_minutes`` — the user channel had every
      chance to deliver first (within the window this bridge does nothing);
    - no UserTradeObserved event exists for the trade under ANY authority
      (the WS bridge always wins when it ran).
    Every recovered event carries the full provenance chain in its payload.
    """

    _ensure_trades_attached_if_needed(conn, trade_db_path=trade_db_path)
    trade_schema = _schema_with_table(conn, "venue_trade_facts", preferred="trades")
    if trade_schema is None or not _table_exists(conn, "edli_live_order_events"):
        return 0
    venue_trade_facts = _q(trade_schema, "venue_trade_facts")
    venue_commands = _q(trade_schema, "venue_commands")

    default_now = now or datetime.now(timezone.utc)
    grace_cutoff = default_now.timestamp() - max(0.0, float(grace_minutes)) * 60.0

    rows = conn.execute(
        f"""
        WITH execution_commands AS (
            SELECT aggregate_id,
                   json_extract(payload_json, '$.event_id') AS event_id,
                   json_extract(payload_json, '$.final_intent_id') AS final_intent_id,
                   json_extract(payload_json, '$.execution_command_id') AS execution_command_id
              FROM edli_live_order_events
             WHERE event_type = 'ExecutionCommandCreated'
        ),
        submit_acks AS (
            SELECT aggregate_id,
                   json_extract(payload_json, '$.venue_order_id') AS venue_order_id
              FROM edli_live_order_events
             WHERE event_type = 'VenueSubmitAcknowledged'
        )
        SELECT exec.aggregate_id,
               exec.event_id,
               exec.final_intent_id,
               exec.execution_command_id,
               cmd.command_id,
               cmd.state AS command_state,
               trade.trade_fact_id,
               trade.trade_id,
               trade.venue_order_id,
               trade.state,
               trade.source AS trade_source,
               trade.filled_size,
               trade.fill_price,
               trade.tx_hash,
               trade.observed_at,
               trade.raw_payload_hash,
               trade.raw_payload_json
          FROM execution_commands exec
          JOIN submit_acks ack
            ON ack.aggregate_id = exec.aggregate_id
          JOIN {venue_commands} cmd
            ON cmd.decision_id = exec.execution_command_id
          JOIN {venue_trade_facts} trade
            ON trade.command_id = cmd.command_id
           AND trade.venue_order_id = ack.venue_order_id
         WHERE UPPER(COALESCE(cmd.state, '')) IN ('FILLED', 'PARTIAL')
           AND CAST(COALESCE(trade.filled_size, '0') AS REAL) > 0
           AND CAST(COALESCE(trade.fill_price, '0') AS REAL) > 0
           AND NOT EXISTS (
                 SELECT 1
                   FROM {venue_trade_facts} ws
                  WHERE ws.trade_id = trade.trade_id
                    AND ws.source = 'WS_USER'
                    AND UPPER(COALESCE(ws.state, '')) = 'CONFIRMED'
               )
           AND NOT EXISTS (
                 SELECT 1
                   FROM edli_live_order_events existing
                  WHERE existing.aggregate_id = exec.aggregate_id
                    AND existing.event_type = 'UserTradeObserved'
                    AND json_extract(existing.payload_json, '$.trade_id') = trade.trade_id
               )
           AND NOT EXISTS (
                 -- Mirror of the ledger guard in _require_user_channel_submit_binding:
                 -- a terminal RECONCILED projection rejects every user-channel append,
                 -- so selecting such aggregates only manufactures a per-minute retry
                 -- loop (observed live 2026-06-12). The class must be unselectable.
                 SELECT 1
                   FROM edli_live_order_projection proj
                  WHERE proj.aggregate_id = exec.aggregate_id
                    AND proj.current_state = 'RECONCILED'
                    AND COALESCE(proj.pending_reconcile, 0) = 0
               )
         ORDER BY trade.observed_at ASC, trade.trade_fact_id ASC
         LIMIT ?
        """,
        (max(0, limit),),
    ).fetchall()

    ledger = LiveOrderAggregateLedger(conn)
    appended = 0
    skipped_invalid = 0
    for row in rows:
        observed_at = _parse_dt(_row_get(row, "observed_at"), default=default_now)
        if observed_at.timestamp() > grace_cutoff:
            continue  # still inside the user-channel grace window
        message_hash = _message_hash(row)
        try:
            _append_one_recovered_fill(ledger, row, observed_at, message_hash, grace_minutes)
        except LiveOrderAggregateError as exc:
            # Poison-pill immunity (task #13 shape): one ledger-rejected row must
            # never abort the batch — the remaining recoverable orphans would
            # starve behind it forever. Validation raises BEFORE any event insert,
            # so nothing partial was written for this row.
            skipped_invalid += 1
            logger.warning(
                "rest-filled orphan bridge: skipped ledger-rejected row aggregate=%s trade=%s: %s",
                _row_get(row, "aggregate_id"), _row_get(row, "trade_id"), exc,
            )
            continue
        appended += 1
    if skipped_invalid:
        logger.warning(
            "rest-filled orphan bridge: %d row(s) skipped as ledger-rejected this scan", skipped_invalid
        )
    return appended


def _append_one_recovered_fill(ledger, row, observed_at, message_hash, grace_minutes) -> None:
    append_reconcile_recovered_fill(
        ledger,
        aggregate_id=str(_row_get(row, "aggregate_id")),
        event_id=str(_row_get(row, "event_id")),
        final_intent_id=str(_row_get(row, "final_intent_id")),
        venue_order_id=str(_row_get(row, "venue_order_id")),
        occurred_at=observed_at,
        payload={
            "raw_user_channel_message_hash": message_hash,
            "trade_id": str(_row_get(row, "trade_id")),
            "filled_size": str(_row_get(row, "filled_size")),
            "fill_price": str(_row_get(row, "fill_price")),
            "avg_fill_price": str(_row_get(row, "fill_price")),
            "transaction_hash": _row_get(row, "tx_hash"),
            "source_trade_fact_id": int(_row_get(row, "trade_fact_id")),
            "source_trade_fact_authority": (
                f"venue_trade_facts:{_row_get(row, 'trade_source')}:"
                f"{_row_get(row, 'state')}"
            ),
            "venue_command_state": str(_row_get(row, "command_state")),
            "recovery_basis": (
                "ws_user_confirmed_missing_after_grace;"
                f"grace_minutes={float(grace_minutes):g};"
                "cmd_terminal_fill_state+rest_trade_fact"
            ),
        },
    )


def _ensure_trades_attached_if_needed(
    conn: sqlite3.Connection,
    *,
    trade_db_path: str | Path | None,
) -> None:
    attached = {str(row[1]) for row in conn.execute("PRAGMA database_list").fetchall()}
    if "trades" in attached and _table_exists(conn, "venue_trade_facts", schema="trades"):
        return
    if trade_db_path is None:
        try:
            from src.state.db import _zeus_trade_db_path

            trade_db_path = _zeus_trade_db_path()
        except Exception:  # noqa: BLE001
            return
    if "trades" not in attached:
        conn.execute("ATTACH DATABASE ? AS trades", (str(trade_db_path),))


def _schema_with_table(
    conn: sqlite3.Connection,
    table: str,
    *,
    preferred: str,
) -> str | None:
    attached = [str(row[1]) for row in conn.execute("PRAGMA database_list").fetchall()]
    for schema in (preferred, "main", *[name for name in attached if name not in {preferred, "main"}]):
        if schema not in attached and schema != "main":
            continue
        if _table_exists(conn, table, schema=schema):
            return schema
    return None


def _table_exists(conn: sqlite3.Connection, table: str, *, schema: str = "main") -> bool:
    if schema == "main":
        row = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
    else:
        row = conn.execute(
            f"SELECT 1 FROM {schema}.sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchone()
    return row is not None


def _q(schema: str, table: str) -> str:
    return table if schema == "main" else f"{schema}.{table}"


def _row_get(row: Any, key: str) -> Any:
    if isinstance(row, sqlite3.Row):
        return row[key]
    return row[key]


def _parse_dt(value: Any, *, default: datetime) -> datetime:
    if not value:
        return default
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return default
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _message_hash(row: sqlite3.Row) -> str:
    payload = {
        "trade_fact_id": _row_get(row, "trade_fact_id"),
        "trade_id": _row_get(row, "trade_id"),
        "venue_order_id": _row_get(row, "venue_order_id"),
        "state": _row_get(row, "state"),
        "filled_size": _row_get(row, "filled_size"),
        "fill_price": _row_get(row, "fill_price"),
        "observed_at": _row_get(row, "observed_at"),
        "raw_payload_hash": _row_get(row, "raw_payload_hash"),
    }
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()
    return f"auth-clob-trade:{digest}"
