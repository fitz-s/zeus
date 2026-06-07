"""Bridge authenticated execution trade facts back into EDLI live-order events."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.events.live_order_aggregate import LiveOrderAggregateLedger
from src.events.live_order_reconcile import append_user_trade_observed


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
