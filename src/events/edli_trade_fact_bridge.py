"""Bridge authenticated execution trade facts back into EDLI live-order events."""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from src.events.live_cap import LiveCapLedger
from src.events.live_order_aggregate import LiveOrderAggregateError, LiveOrderAggregateLedger
from src.events.live_order_reconcile import (
    RECONCILE_SOURCE,
    append_reconciled,
    append_reconcile_recovered_fill,
    append_user_trade_observed,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TradeFactBridgeCandidate:
    """A bounded read-only discovery result, revalidated before any append."""

    aggregate_id: str
    execution_command_id: str
    trade_fact_id: int
    trade_id: str


def discover_confirmed_trade_fact_candidates(
    conn: sqlite3.Connection,
    *,
    trade_schema: str,
    event_schema: str,
    projection_schema: str,
    limit: int = 100,
    trade_db_path: str | Path | None = None,
) -> tuple[TradeFactBridgeCandidate, ...]:
    """Find WS_USER confirmed fills without taking a canonical writer lease."""

    return _discover_trade_fact_candidates(
        conn,
        kind="confirmed",
        trade_schema=trade_schema,
        event_schema=event_schema,
        projection_schema=projection_schema,
        limit=limit,
        trade_db_path=trade_db_path,
    )


def discover_rest_filled_orphan_trade_fact_candidates(
    conn: sqlite3.Connection,
    *,
    trade_schema: str,
    event_schema: str,
    projection_schema: str,
    limit: int = 50,
    trade_db_path: str | Path | None = None,
) -> tuple[TradeFactBridgeCandidate, ...]:
    """Find REST fill-orphan candidates without taking a canonical writer lease."""

    return _discover_trade_fact_candidates(
        conn,
        kind="rest_orphan",
        trade_schema=trade_schema,
        event_schema=event_schema,
        projection_schema=projection_schema,
        limit=limit,
        trade_db_path=trade_db_path,
    )


def _discover_trade_fact_candidates(
    conn: sqlite3.Connection,
    *,
    kind: str,
    trade_schema: str,
    event_schema: str,
    projection_schema: str,
    limit: int,
    trade_db_path: str | Path | None,
) -> tuple[TradeFactBridgeCandidate, ...]:
    """Run the expensive historical/window discovery query in a read-only phase."""

    _ensure_trades_attached_if_needed(conn, trade_db_path=trade_db_path)
    _require_schema_tables(
        conn,
        schema=trade_schema,
        tables=("venue_commands", "venue_trade_facts"),
    )
    _require_schema_tables(
        conn,
        schema=event_schema,
        tables=("edli_live_order_events",),
    )
    _require_schema_tables(
        conn,
        schema=projection_schema,
        tables=("edli_live_order_projection",),
    )
    venue_trade_facts = _q(trade_schema, "venue_trade_facts")
    venue_commands = _q(trade_schema, "venue_commands")
    events = _q(event_schema, "edli_live_order_events")
    projection = _q(projection_schema, "edli_live_order_projection")

    if kind == "confirmed":
        candidate_filter = """
            UPPER(COALESCE(trade.state, '')) = 'CONFIRMED'
            AND trade.source = 'WS_USER'
        """
        existing_filter = """
            COALESCE(
                json_extract(existing.payload_json, '$.trade_id'),
                json_extract(existing.payload_json, '$.authenticated_presence_proof.trade_id')
            ) = trade.trade_id
            AND json_extract(existing.payload_json, '$.fill_authority_state') = 'FILL_CONFIRMED'
        """
        command_filter = ""
        rank_order = "datetime(trade.observed_at) DESC, trade.trade_fact_id DESC"
    elif kind == "rest_orphan":
        candidate_filter = """
            UPPER(COALESCE(trade.state, '')) IN ('MATCHED', 'MINED', 'CONFIRMED')
            AND NOT EXISTS (
                SELECT 1
                  FROM {venue_trade_facts} ws
                 WHERE ws.trade_id = trade.trade_id
                   AND ws.source = 'WS_USER'
                   AND UPPER(COALESCE(ws.state, '')) = 'CONFIRMED'
            )
        """.format(venue_trade_facts=venue_trade_facts)
        existing_filter = "json_extract(existing.payload_json, '$.trade_id') = trade.trade_id"
        command_filter = """
            AND UPPER(COALESCE(cmd.state, '')) IN ('FILLED', 'PARTIAL')
            AND COALESCE(NULLIF(ack.venue_order_id, ''), NULLIF(cmd.venue_order_id, '')) IS NOT NULL
        """
        rank_order = """CASE UPPER(COALESCE(trade.state, ''))
                            WHEN 'CONFIRMED' THEN 3
                            WHEN 'MINED' THEN 2
                            WHEN 'MATCHED' THEN 1
                            ELSE 0
                        END DESC,
                        datetime(trade.observed_at) DESC,
                        trade.trade_fact_id DESC"""
    else:
        raise ValueError(f"unsupported trade-fact bridge candidate kind {kind!r}")

    rows = conn.execute(
        f"""
        WITH execution_commands AS (
            SELECT aggregate_id, event_id, final_intent_id,
                   execution_command_id, command_occurred_at
              FROM (
                    SELECT aggregate_id,
                           json_extract(payload_json, '$.event_id') AS event_id,
                           json_extract(payload_json, '$.final_intent_id') AS final_intent_id,
                           json_extract(payload_json, '$.execution_command_id') AS execution_command_id,
                           occurred_at AS command_occurred_at,
                           ROW_NUMBER() OVER (
                               PARTITION BY aggregate_id ORDER BY event_sequence DESC
                           ) AS command_rank
                      FROM {events}
                     WHERE event_type = 'ExecutionCommandCreated'
                   )
             WHERE command_rank = 1
        ),
        submit_acks AS (
            SELECT aggregate_id, venue_order_id
              FROM (
                    SELECT aggregate_id,
                           json_extract(payload_json, '$.venue_order_id') AS venue_order_id,
                           ROW_NUMBER() OVER (
                               PARTITION BY aggregate_id ORDER BY event_sequence DESC
                           ) AS ack_rank
                      FROM {events}
                     WHERE event_type = 'VenueSubmitAcknowledged'
                   )
             WHERE ack_rank = 1
        ),
        ranked_candidates AS (
            SELECT exec.aggregate_id, exec.execution_command_id,
                   trade.trade_fact_id, trade.trade_id,
                   ROW_NUMBER() OVER (
                       PARTITION BY exec.aggregate_id, trade.trade_id
                       ORDER BY {rank_order}
                   ) AS logical_fill_rank
              FROM execution_commands exec
              JOIN {venue_commands} cmd
                ON cmd.decision_id = exec.execution_command_id
              LEFT JOIN submit_acks ack
                ON ack.aggregate_id = exec.aggregate_id
              JOIN {venue_trade_facts} trade
                ON trade.command_id = cmd.command_id
               AND trade.venue_order_id = COALESCE(
                       NULLIF(ack.venue_order_id, ''), NULLIF(cmd.venue_order_id, '')
                   )
             WHERE {candidate_filter}
               AND CAST(COALESCE(trade.filled_size, '0') AS REAL) > 0
               AND CAST(COALESCE(trade.fill_price, '0') AS REAL) > 0
               {command_filter}
               AND NOT EXISTS (
                    SELECT 1
                      FROM {events} existing
                     WHERE existing.aggregate_id = exec.aggregate_id
                       AND existing.event_type = 'UserTradeObserved'
                       AND {existing_filter}
               )
               AND NOT EXISTS (
                    SELECT 1
                      FROM {projection} proj
                     WHERE proj.aggregate_id = exec.aggregate_id
                       AND proj.current_state = 'RECONCILED'
                       AND COALESCE(proj.pending_reconcile, 0) = 0
               )
        )
        SELECT aggregate_id, execution_command_id, trade_fact_id, trade_id
          FROM ranked_candidates
         WHERE logical_fill_rank = 1
         ORDER BY trade_fact_id ASC
         LIMIT ?
        """,
        (max(0, limit),),
    ).fetchall()
    return tuple(
        TradeFactBridgeCandidate(
            aggregate_id=str(_row_get(row, "aggregate_id")),
            execution_command_id=str(_row_get(row, "execution_command_id")),
            trade_fact_id=int(_row_get(row, "trade_fact_id")),
            trade_id=str(_row_get(row, "trade_id")),
        )
        for row in rows
    )


def _revalidate_confirmed_trade_fact_candidate(
    conn: sqlite3.Connection,
    candidate: TradeFactBridgeCandidate,
    *,
    trade_schema: str,
    event_schema: str,
) -> dict[str, Any] | None:
    row = _revalidate_trade_fact_candidate(
        conn,
        candidate,
        trade_schema=trade_schema,
        event_schema=event_schema,
        states=("CONFIRMED",),
        source="WS_USER",
    )
    if row is None:
        return None
    if _has_user_trade_observed(
        conn,
        event_schema=event_schema,
        aggregate_id=candidate.aggregate_id,
        trade_id=candidate.trade_id,
        require_confirmed_authority=True,
    ):
        return None
    return row


def _revalidate_rest_filled_orphan_trade_fact_candidate(
    conn: sqlite3.Connection,
    candidate: TradeFactBridgeCandidate,
    *,
    trade_schema: str,
    event_schema: str,
) -> dict[str, Any] | None:
    row = _revalidate_trade_fact_candidate(
        conn,
        candidate,
        trade_schema=trade_schema,
        event_schema=event_schema,
        states=("MATCHED", "MINED", "CONFIRMED"),
        source=None,
    )
    if row is None or str(row["command_state"] or "").upper() not in {"FILLED", "PARTIAL"}:
        return None
    venue_trade_facts = _q(trade_schema, "venue_trade_facts")
    ws_confirmed = conn.execute(
        f"""
        SELECT 1
          FROM {venue_trade_facts}
         WHERE trade_id = ?
           AND source = 'WS_USER'
           AND UPPER(COALESCE(state, '')) = 'CONFIRMED'
         LIMIT 1
        """,
        (candidate.trade_id,),
    ).fetchone()
    if ws_confirmed is not None:
        return None
    if _has_user_trade_observed(
        conn,
        event_schema=event_schema,
        aggregate_id=candidate.aggregate_id,
        trade_id=candidate.trade_id,
        require_confirmed_authority=False,
    ):
        return None
    return row


def _revalidate_trade_fact_candidate(
    conn: sqlite3.Connection,
    candidate: TradeFactBridgeCandidate,
    *,
    trade_schema: str,
    event_schema: str,
    states: tuple[str, ...],
    source: str | None,
) -> dict[str, Any] | None:
    """Re-read one candidate through aggregate and trade keys under the writer gate."""

    events = _q(event_schema, "edli_live_order_events")
    projection_schema = _schema_with_table(
        conn, "edli_live_order_projection", preferred=event_schema
    )
    if projection_schema is None:
        return None
    projection = _q(projection_schema, "edli_live_order_projection")
    execution = conn.execute(
        f"""
        SELECT json_extract(payload_json, '$.event_id') AS event_id,
               json_extract(payload_json, '$.final_intent_id') AS final_intent_id,
               json_extract(payload_json, '$.execution_command_id') AS execution_command_id,
               occurred_at AS command_occurred_at
          FROM {events}
         WHERE aggregate_id = ?
           AND event_type = 'ExecutionCommandCreated'
         ORDER BY event_sequence DESC
         LIMIT 1
        """,
        (candidate.aggregate_id,),
    ).fetchone()
    if execution is None or str(_row_get(execution, "execution_command_id") or "") != candidate.execution_command_id:
        return None
    projection_row = conn.execute(
        f"""
        SELECT current_state, pending_reconcile
          FROM {projection}
         WHERE aggregate_id = ?
         LIMIT 1
        """,
        (candidate.aggregate_id,),
    ).fetchone()
    if (
        projection_row is not None
        and str(_row_get(projection_row, "current_state") or "") == "RECONCILED"
        and not bool(_row_get(projection_row, "pending_reconcile"))
    ):
        return None
    ack = conn.execute(
        f"""
        SELECT json_extract(payload_json, '$.venue_order_id') AS venue_order_id
          FROM {events}
         WHERE aggregate_id = ?
           AND event_type = 'VenueSubmitAcknowledged'
         ORDER BY event_sequence DESC
         LIMIT 1
        """,
        (candidate.aggregate_id,),
    ).fetchone()
    venue_commands = _q(trade_schema, "venue_commands")
    command = conn.execute(
        f"""
        SELECT command_id, venue_order_id, state
          FROM {venue_commands}
         WHERE decision_id = ?
         LIMIT 1
        """,
        (candidate.execution_command_id,),
    ).fetchone()
    if command is None:
        return None
    venue_order_id = str(
        (_row_get(ack, "venue_order_id") if ack is not None else "")
        or _row_get(command, "venue_order_id")
        or ""
    )
    if not venue_order_id:
        return None
    venue_trade_facts = _q(trade_schema, "venue_trade_facts")
    placeholders = ", ".join("?" for _ in states)
    source_filter = "AND source = ?" if source is not None else ""
    latest = conn.execute(
        f"""
        SELECT trade_fact_id
          FROM {venue_trade_facts}
         WHERE command_id = ?
           AND venue_order_id = ?
           AND trade_id = ?
           AND UPPER(COALESCE(state, '')) IN ({placeholders})
           {source_filter}
         ORDER BY { _trade_fact_rank_order(states) }
         LIMIT 1
        """,
        (
            str(_row_get(command, "command_id")),
            venue_order_id,
            candidate.trade_id,
            *states,
            *((source,) if source is not None else ()),
        ),
    ).fetchone()
    if latest is None or int(_row_get(latest, "trade_fact_id")) != candidate.trade_fact_id:
        return None
    trade = conn.execute(
        f"""
        SELECT trade_fact_id, trade_id, venue_order_id, state, source AS trade_source,
               filled_size, fill_price, tx_hash, observed_at,
               raw_payload_hash, raw_payload_json
          FROM {venue_trade_facts}
         WHERE trade_fact_id = ?
           AND command_id = ?
           AND venue_order_id = ?
           AND trade_id = ?
           {source_filter}
         LIMIT 1
        """,
        (
            candidate.trade_fact_id,
            str(_row_get(command, "command_id")),
            venue_order_id,
            candidate.trade_id,
            *((source,) if source is not None else ()),
        ),
    ).fetchone()
    if trade is None:
        return None
    if (
        str(_row_get(trade, "state") or "").upper() not in states
        or float(_row_get(trade, "filled_size") or 0) <= 0
        or float(_row_get(trade, "fill_price") or 0) <= 0
    ):
        return None
    return {
        "aggregate_id": candidate.aggregate_id,
        "event_id": _row_get(execution, "event_id"),
        "final_intent_id": _row_get(execution, "final_intent_id"),
        "execution_command_id": candidate.execution_command_id,
        "command_occurred_at": _row_get(execution, "command_occurred_at"),
        "command_state": _row_get(command, "state"),
        "trade_fact_id": _row_get(trade, "trade_fact_id"),
        "trade_id": _row_get(trade, "trade_id"),
        "venue_order_id": _row_get(trade, "venue_order_id"),
        "state": _row_get(trade, "state"),
        "trade_source": _row_get(trade, "trade_source"),
        "filled_size": _row_get(trade, "filled_size"),
        "fill_price": _row_get(trade, "fill_price"),
        "tx_hash": _row_get(trade, "tx_hash"),
        "observed_at": _row_get(trade, "observed_at"),
        "raw_payload_hash": _row_get(trade, "raw_payload_hash"),
        "raw_payload_json": _row_get(trade, "raw_payload_json"),
    }


def _has_user_trade_observed(
    conn: sqlite3.Connection,
    *,
    event_schema: str,
    aggregate_id: str,
    trade_id: str,
    require_confirmed_authority: bool,
) -> bool:
    events = _q(event_schema, "edli_live_order_events")
    rows = conn.execute(
        f"""
        SELECT payload_json
          FROM {events}
         WHERE aggregate_id = ?
           AND event_type = 'UserTradeObserved'
        """,
        (aggregate_id,),
    ).fetchall()
    for row in rows:
        payload = json.loads(str(_row_get(row, "payload_json") or "{}"))
        observed_trade_id = payload.get("trade_id") or (
            payload.get("authenticated_presence_proof") or {}
        ).get("trade_id")
        if observed_trade_id != trade_id:
            continue
        if not require_confirmed_authority or payload.get("fill_authority_state") == "FILL_CONFIRMED":
            return True
    return False


def _trade_fact_rank_order(states: tuple[str, ...]) -> str:
    if states == ("CONFIRMED",):
        return "datetime(observed_at) DESC, trade_fact_id DESC"
    return """CASE UPPER(COALESCE(state, ''))
                  WHEN 'CONFIRMED' THEN 3
                  WHEN 'MINED' THEN 2
                  WHEN 'MATCHED' THEN 1
                  ELSE 0
              END DESC, datetime(observed_at) DESC, trade_fact_id DESC"""


def append_confirmed_trade_facts_to_edli(
    conn: sqlite3.Connection,
    *,
    now: datetime | None = None,
    limit: int = 100,
    trade_db_path: str | Path | None = None,
    candidates: Sequence[TradeFactBridgeCandidate] | None = None,
    absorbed_fill_aggregate_ids: Sequence[str] | None = None,
) -> int:
    """Append missing EDLI UserTradeObserved events from confirmed WS trade facts.

    The source of authority remains the authenticated user channel: this bridge
    only consumes ``venue_trade_facts`` rows written as ``source='WS_USER'`` and
    ``state='CONFIRMED'``. The trade fact must bind to the EDLI execution command
    and either its acknowledged venue order or the same canonical command's
    persisted venue order. The latter covers a matched submit response that
    returned an order id but omitted the trade id, so EDLI recorded
    ``SubmitUnknown`` while the authenticated user channel later proved the fill.
    """

    trade_schema, event_schema, projection_schema = _append_bridge_schemas(
        conn, trade_db_path=trade_db_path
    )
    if candidates is None:
        candidates = discover_confirmed_trade_fact_candidates(
            conn,
            trade_schema=trade_schema,
            event_schema=event_schema,
            projection_schema=projection_schema,
            limit=limit,
            trade_db_path=trade_db_path,
        )

    ledger = LiveOrderAggregateLedger(conn)
    appended = 0
    default_now = now or datetime.now(timezone.utc)
    for candidate in tuple(candidates)[: max(0, limit)]:
        row = _revalidate_confirmed_trade_fact_candidate(
            conn,
            candidate,
            trade_schema=trade_schema,
            event_schema=event_schema,
        )
        if row is None:
            continue
        observed_at = _parse_dt(_row_get(row, "observed_at"), default=default_now)
        command_occurred_at = _parse_dt(
            _row_get(row, "command_occurred_at"), default=default_now
        )
        message_hash = _message_hash(row)
        try:
            append_user_trade_observed(
                ledger,
                aggregate_id=str(_row_get(row, "aggregate_id")),
                event_id=str(_row_get(row, "event_id")),
                final_intent_id=str(_row_get(row, "final_intent_id")),
                source="polymarket_user_channel",
                trade_status="CONFIRMED",
                venue_order_id=str(_row_get(row, "venue_order_id")),
                occurred_at=max(observed_at, command_occurred_at),
                payload={
                    "raw_user_channel_message_hash": message_hash,
                    "trade_id": str(_row_get(row, "trade_id")),
                    "filled_size": str(_row_get(row, "filled_size")),
                    "fill_price": str(_row_get(row, "fill_price")),
                    "avg_fill_price": str(_row_get(row, "fill_price")),
                    "transaction_hash": _row_get(row, "tx_hash"),
                    "source_trade_observed_at": str(_row_get(row, "observed_at")),
                    "source_trade_fact_id": int(_row_get(row, "trade_fact_id")),
                    "source_trade_fact_authority": "venue_trade_facts:WS_USER:CONFIRMED",
                },
            )
        except LiveOrderAggregateError as exc:
            if "cannot append after terminal Reconciled projection" not in str(exc):
                raise
            logger.info(
                "confirmed trade bridge: terminal aggregate replay skipped aggregate=%s trade=%s",
                _row_get(row, "aggregate_id"),
                _row_get(row, "trade_id"),
            )
            continue
        appended += 1
    reconciled = _consume_absorbed_confirmed_fills(
        conn,
        trade_schema=trade_schema,
        event_schema=event_schema,
        projection_schema=projection_schema,
        cap_schema=event_schema,
        limit=limit,
        now=default_now,
        aggregate_ids=absorbed_fill_aggregate_ids,
    )
    if reconciled:
        logger.warning(
            "confirmed trade bridge: consumed %d stuck EDLI fill reservation(s)",
            reconciled,
        )
    return appended


def _consume_absorbed_confirmed_fills(
    conn: sqlite3.Connection,
    *,
    trade_schema: str,
    event_schema: str,
    projection_schema: str,
    cap_schema: str,
    limit: int,
    now: datetime,
    aggregate_ids: Sequence[str] | None = None,
    discover_only: bool = False,
) -> int | tuple[str, ...]:
    """Close pending EDLI state once the exact fill is already canonical."""

    required = (
        "venue_commands",
        "venue_trade_facts",
        "venue_order_facts",
        "position_current",
        "position_events",
    )
    _require_schema_tables(conn, schema=trade_schema, tables=required)
    _require_schema_tables(
        conn, schema=event_schema, tables=("edli_live_order_events",)
    )
    _require_schema_tables(
        conn, schema=projection_schema, tables=("edli_live_order_projection",)
    )
    _require_schema_tables(conn, schema=cap_schema, tables=("edli_live_cap_usage",))
    events = _q(event_schema, "edli_live_order_events")
    projection = _q(projection_schema, "edli_live_order_projection")
    cap_usage = _q(cap_schema, "edli_live_cap_usage")
    candidate_aggregate_ids = tuple(dict.fromkeys(aggregate_ids or ()))
    if aggregate_ids is not None and not candidate_aggregate_ids:
        return () if discover_only else 0
    aggregate_placeholders = ", ".join("?" for _ in candidate_aggregate_ids)
    aggregate_filter = (
        f"AND aggregate_id IN ({aggregate_placeholders})"
        if candidate_aggregate_ids
        else ""
    )
    commands = _q(trade_schema, "venue_commands")
    trades = _q(trade_schema, "venue_trade_facts")
    orders = _q(trade_schema, "venue_order_facts")
    positions = _q(trade_schema, "position_current")
    position_events = _q(trade_schema, "position_events")
    order_filter = (
        f"""
        WHERE fact.command_id IN (
            SELECT candidate_command.command_id
              FROM {projection} candidate_projection
              JOIN {cap_usage} candidate_usage
                ON candidate_usage.event_id = candidate_projection.event_id
               AND candidate_usage.final_intent_id = candidate_projection.final_intent_id
              JOIN {commands} candidate_command
                ON candidate_command.decision_id = candidate_usage.execution_command_id
             WHERE candidate_projection.aggregate_id IN ({aggregate_placeholders})
        )
        """
        if candidate_aggregate_ids
        else ""
    )
    rows = conn.execute(
        f"""
        WITH latest_plan AS (
            SELECT aggregate_id, payload_json
              FROM (
                    SELECT aggregate_id, payload_json,
                           ROW_NUMBER() OVER (
                               PARTITION BY aggregate_id ORDER BY event_sequence DESC
                           ) AS rank
                      FROM {events}
                     WHERE event_type = 'SubmitPlanBuilt'
                       {aggregate_filter}
                   )
             WHERE rank = 1
        ),
        latest_trade AS (
            SELECT aggregate_id, payload_json, source_authority
              FROM (
                    SELECT aggregate_id, payload_json, source_authority,
                           ROW_NUMBER() OVER (
                               PARTITION BY aggregate_id ORDER BY event_sequence DESC
                           ) AS rank
                      FROM {events}
                     WHERE event_type = 'UserTradeObserved'
                       {aggregate_filter}
                   )
             WHERE rank = 1
        ),
        latest_order AS (
            SELECT *
              FROM (
                    SELECT fact.*,
                           ROW_NUMBER() OVER (
                               PARTITION BY command_id ORDER BY local_sequence DESC
                           ) AS rank
                      FROM {orders} fact
                      {order_filter}
                   )
             WHERE rank = 1
        )
        SELECT projection.aggregate_id, projection.event_id,
               projection.final_intent_id, usage.usage_id,
               usage.execution_command_id, command.command_id,
               command.position_id, command.token_id, command.venue_order_id,
               trade.trade_fact_id, trade.trade_id,
               trade.filled_size, trade.fill_price, trade.source AS trade_source,
               position.condition_id, position.direction,
               position.chain_shares, position.phase,
               entry_fill.event_id AS entry_fill_event_id,
               json_extract(entry_fill.payload_json, '$.shares')
                   AS entry_filled_shares
          FROM {projection} projection
          JOIN latest_plan plan USING (aggregate_id)
          JOIN latest_trade observed USING (aggregate_id)
          JOIN {cap_usage} usage
            ON usage.event_id = projection.event_id
           AND usage.final_intent_id = projection.final_intent_id
           AND usage.reservation_status = 'RESERVED'
          JOIN {commands} command
            ON command.decision_id = usage.execution_command_id
          JOIN {trades} trade
            ON trade.trade_fact_id = CAST(
                json_extract(observed.payload_json, '$.source_trade_fact_id') AS INTEGER
            )
           AND trade.command_id = command.command_id
           AND trade.venue_order_id = command.venue_order_id
          JOIN latest_order order_fact
            ON order_fact.command_id = command.command_id
           AND order_fact.venue_order_id = command.venue_order_id
          JOIN {positions} position
            ON position.position_id = command.position_id
          JOIN {position_events} entry_fill
            ON entry_fill.event_id = (
                SELECT candidate_fill.event_id
                  FROM {position_events} candidate_fill
                 WHERE candidate_fill.position_id = command.position_id
                   AND candidate_fill.event_type = 'ENTRY_ORDER_FILLED'
                   AND (
                        candidate_fill.command_id = command.command_id
                        OR candidate_fill.order_id = command.venue_order_id
                   )
                 ORDER BY candidate_fill.sequence_no DESC
                 LIMIT 1
            )
         WHERE projection.current_state = 'USER_TRADE_OBSERVED'
           AND command.intent_kind = 'ENTRY'
           AND command.side = 'BUY'
           AND command.state = 'FILLED'
           AND trade.state = 'CONFIRMED'
           AND (
                (
                    trade.source = 'WS_USER'
                    AND observed.source_authority = 'user_channel'
                    AND json_extract(
                            observed.payload_json,
                            '$.source_trade_fact_authority'
                        ) = 'venue_trade_facts:WS_USER:CONFIRMED'
                )
                OR (
                    trade.source = 'REST'
                    AND observed.source_authority = 'explicit_reconcile'
                    AND json_extract(
                            observed.payload_json,
                            '$.source_trade_fact_authority'
                        ) = 'venue_trade_facts:REST:CONFIRMED'
                )
           )
           AND order_fact.state = 'MATCHED'
           AND order_fact.source IN ('REST', 'WS_USER', 'DATA_API', 'CHAIN')
           AND CAST(COALESCE(order_fact.remaining_size, '0') AS REAL) <= 0.01
           AND json_extract(observed.payload_json, '$.fill_authority_state') = 'FILL_CONFIRMED'
           AND json_extract(observed.payload_json, '$.venue_order_id') = command.venue_order_id
           AND json_extract(plan.payload_json, '$.token_id') = command.token_id
           AND json_extract(plan.payload_json, '$.condition_id') = position.condition_id
           AND json_extract(plan.payload_json, '$.direction') = position.direction
           AND command.token_id = CASE position.direction
                 WHEN 'buy_no' THEN position.no_token_id
                 WHEN 'buy_yes' THEN position.token_id
               END
           AND CAST(trade.filled_size AS REAL) + 0.01 >= command.size
           AND CAST(order_fact.matched_size AS REAL) + 0.01 >= command.size
           AND CAST(trade.fill_price AS REAL) <= command.price + 0.011
           AND ABS(
                   CAST(position.entry_price AS REAL)
                   - CAST(trade.fill_price AS REAL)
               ) <= 0.011
           AND position.fill_authority IN ('venue_confirmed_full', 'venue_confirmed_partial')
           AND position.phase != 'pending_entry'
           AND CAST(
                   json_extract(entry_fill.payload_json, '$.shares') AS REAL
               ) + 0.01 >= command.size
         ORDER BY projection.updated_at, projection.aggregate_id
         LIMIT ?
        """,
        (
            *candidate_aggregate_ids,
            *candidate_aggregate_ids,
            *candidate_aggregate_ids,
            max(0, limit),
        ),
    ).fetchall()
    if discover_only:
        return tuple(str(_row_get(row, "aggregate_id")) for row in rows)
    ledger = LiveOrderAggregateLedger(conn)
    cap_ledger = LiveCapLedger(conn, schema_initialized=True)
    for row in rows:
        proof = {
            "schema_version": 1,
            "proof_class": "canonical_confirmed_fill_already_absorbed",
            "command_id": str(_row_get(row, "command_id")),
            "position_id": str(_row_get(row, "position_id")),
            "venue_order_id": str(_row_get(row, "venue_order_id")),
            "trade_fact_id": int(_row_get(row, "trade_fact_id")),
            "trade_id": str(_row_get(row, "trade_id")),
            "token_id": str(_row_get(row, "token_id")),
            "condition_id": str(_row_get(row, "condition_id")),
            "direction": str(_row_get(row, "direction")),
            "filled_size": str(_row_get(row, "filled_size")),
            "fill_price": str(_row_get(row, "fill_price")),
            "trade_source": str(_row_get(row, "trade_source")),
            "chain_shares": str(_row_get(row, "chain_shares")),
            "position_phase": str(_row_get(row, "phase")),
            "entry_fill_event_id": str(_row_get(row, "entry_fill_event_id")),
            "entry_filled_shares": str(_row_get(row, "entry_filled_shares")),
        }
        proof["proof_hash"] = hashlib.sha256(
            json.dumps(proof, sort_keys=True).encode()
        ).hexdigest()
        projection_pending = bool(
            conn.execute(
                f"""
                SELECT pending_reconcile
                  FROM {projection}
                 WHERE aggregate_id = ?
                """,
                (str(_row_get(row, "aggregate_id")),),
            ).fetchone()[0]
        )
        if projection_pending:
            append_reconciled(
                ledger,
                aggregate_id=str(_row_get(row, "aggregate_id")),
                event_id=str(_row_get(row, "event_id")),
                final_intent_id=str(_row_get(row, "final_intent_id")),
                source=RECONCILE_SOURCE,
                pending_reconcile=False,
                occurred_at=now,
                payload={
                    "execution_command_id": str(
                        _row_get(row, "execution_command_id")
                    ),
                    "venue_order_exists": True,
                    "venue_trade_exists": True,
                    "cap_transition_recommendation": "CONSUMED",
                    "reconcile_reason": "CANONICAL_CONFIRMED_FILL_ALREADY_ABSORBED",
                    "canonical_confirmed_fill_proof": proof,
                },
            )
        cap_ledger.consume(
            str(_row_get(row, "usage_id")),
            final_intent_id=str(_row_get(row, "final_intent_id")),
            execution_command_id=str(_row_get(row, "execution_command_id")),
        )
        if not projection_pending:
            ledger.append_event(
                aggregate_id=str(_row_get(row, "aggregate_id")),
                event_type="CapTransitioned",
                payload={
                    "event_id": str(_row_get(row, "event_id")),
                    "final_intent_id": str(_row_get(row, "final_intent_id")),
                    "execution_command_id": str(
                        _row_get(row, "execution_command_id")
                    ),
                    "execution_receipt_hash": str(proof["proof_hash"]),
                    "to_status": "CONSUMED",
                    "projection_status": "CONSUMED",
                    "transition_reason": "CANONICAL_CONFIRMED_FILL_ALREADY_ABSORBED",
                    "canonical_confirmed_fill_proof": proof,
                },
                occurred_at=now,
                source_authority="live_cap_ledger",
            )
    return len(rows)


def discover_absorbed_confirmed_fill_aggregate_ids(
    conn: sqlite3.Connection,
    *,
    trade_schema: str,
    event_schema: str,
    projection_schema: str,
    cap_schema: str,
    limit: int = 100,
) -> tuple[str, ...]:
    """Discover cap-reconcile candidates before the canonical writer lease."""

    candidates = _consume_absorbed_confirmed_fills(
        conn,
        trade_schema=trade_schema,
        event_schema=event_schema,
        projection_schema=projection_schema,
        cap_schema=cap_schema,
        limit=limit,
        now=datetime.now(timezone.utc),
        discover_only=True,
    )
    return tuple(candidates)


def append_rest_filled_orphan_trade_facts_to_edli(
    conn: sqlite3.Connection,
    *,
    now: datetime | None = None,
    grace_minutes: float = 15.0,
    limit: int = 50,
    trade_db_path: str | Path | None = None,
    candidates: Sequence[TradeFactBridgeCandidate] | None = None,
    absorbed_fill_aggregate_ids: Sequence[str] | None = None,
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
    - a REST ``CONFIRMED`` fact is already explicit venue reconciliation and is
      recovered immediately;
    - a REST ``MATCHED``/``MINED`` fact is OLDER than ``grace_minutes`` — the
      user channel had every chance to deliver first (within the window this
      bridge does nothing);
    - no UserTradeObserved event exists for the trade under ANY authority
      (the WS bridge always wins when it ran).
    Every recovered event carries the full provenance chain in its payload.
    """

    default_now = now or datetime.now(timezone.utc)
    grace_cutoff = default_now.timestamp() - max(0.0, float(grace_minutes)) * 60.0
    trade_schema, event_schema, projection_schema = _append_bridge_schemas(
        conn, trade_db_path=trade_db_path
    )
    if candidates is None:
        candidates = discover_rest_filled_orphan_trade_fact_candidates(
            conn,
            trade_schema=trade_schema,
            event_schema=event_schema,
            projection_schema=projection_schema,
            limit=limit,
            trade_db_path=trade_db_path,
        )

    ledger = LiveOrderAggregateLedger(conn)
    appended = 0
    skipped_invalid = 0
    for candidate in tuple(candidates)[: max(0, limit)]:
        row = _revalidate_rest_filled_orphan_trade_fact_candidate(
            conn,
            candidate,
            trade_schema=trade_schema,
            event_schema=event_schema,
        )
        if row is None:
            continue
        observed_at = _parse_dt(_row_get(row, "observed_at"), default=default_now)
        rest_confirmed = (
            str(_row_get(row, "trade_source") or "").upper() == "REST"
            and str(_row_get(row, "state") or "").upper() == "CONFIRMED"
        )
        if not rest_confirmed and observed_at.timestamp() > grace_cutoff:
            continue  # still inside the user-channel grace window
        command_occurred_at = _parse_dt(
            _row_get(row, "command_occurred_at"), default=default_now
        )
        message_hash = _message_hash(row)
        try:
            _ensure_recovered_submit_binding(
                ledger,
                row,
                occurred_at=max(observed_at, command_occurred_at),
            )
            _append_one_recovered_fill(
                ledger,
                row,
                max(observed_at, command_occurred_at),
                message_hash,
                grace_minutes,
            )
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
    reconciled = _consume_absorbed_confirmed_fills(
        conn,
        trade_schema=trade_schema,
        event_schema=event_schema,
        projection_schema=projection_schema,
        cap_schema=event_schema,
        limit=limit,
        now=default_now,
        aggregate_ids=absorbed_fill_aggregate_ids,
    )
    if reconciled:
        logger.warning(
            "rest-filled orphan bridge: consumed %d stuck EDLI fill reservation(s)",
            reconciled,
        )
    if skipped_invalid:
        logger.warning(
            "rest-filled orphan bridge: %d row(s) skipped as ledger-rejected this scan", skipped_invalid
        )
    return appended


def _ensure_recovered_submit_binding(
    ledger: LiveOrderAggregateLedger,
    row: sqlite3.Row,
    *,
    occurred_at: datetime,
) -> None:
    """Restore submit events only when a terminal command and confirmed fill prove them."""

    aggregate_id = str(_row_get(row, "aggregate_id"))
    event_id = str(_row_get(row, "event_id"))
    final_intent_id = str(_row_get(row, "final_intent_id"))
    execution_command_id = str(_row_get(row, "execution_command_id"))
    venue_order_id = str(_row_get(row, "venue_order_id"))
    existing = {
        str(event[0])
        for event in ledger.conn.execute(
            """
            SELECT event_type
              FROM edli_live_order_events
             WHERE aggregate_id = ?
               AND event_type IN (
                   'VenueSubmitAttempted',
                   'VenueSubmitAcknowledged',
                   'SubmitUnknown'
               )
            """,
            (aggregate_id,),
        ).fetchall()
    }
    if not existing:
        ledger.append_event(
            aggregate_id=aggregate_id,
            event_type="VenueSubmitAttempted",
            payload={
                "event_id": event_id,
                "final_intent_id": final_intent_id,
                "execution_command_id": execution_command_id,
                "venue_call_started": True,
                "recovery_reason": "TERMINAL_COMMAND_CONFIRMED_FILL_PROVES_SUBMIT",
            },
            occurred_at=occurred_at,
            source_authority="existing_executor",
        )
    if "VenueSubmitAcknowledged" not in existing:
        ledger.append_event(
            aggregate_id=aggregate_id,
            event_type="VenueSubmitAcknowledged",
            payload={
                "event_id": event_id,
                "final_intent_id": final_intent_id,
                "execution_command_id": execution_command_id,
                "venue_order_id": venue_order_id,
                "venue_ack_received": True,
                "recovery_reason": "TERMINAL_COMMAND_CONFIRMED_FILL_PROVES_ACK",
            },
            occurred_at=occurred_at,
            source_authority="existing_executor",
        )


def _append_one_recovered_fill(ledger, row, observed_at, message_hash, grace_minutes) -> None:
    rest_confirmed = (
        str(_row_get(row, "trade_source") or "").upper() == "REST"
        and str(_row_get(row, "state") or "").upper() == "CONFIRMED"
    )
    recovery_basis = (
        "rest_confirmed_fill_fact;cmd_terminal_fill_state+rest_trade_fact"
        if rest_confirmed
        else (
            "ws_user_confirmed_missing_after_grace;"
            f"grace_minutes={float(grace_minutes):g};"
            "cmd_terminal_fill_state+rest_trade_fact"
        )
    )
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
            "source_trade_observed_at": str(_row_get(row, "observed_at")),
            "source_trade_fact_id": int(_row_get(row, "trade_fact_id")),
            "source_trade_fact_authority": (
                f"venue_trade_facts:{_row_get(row, 'trade_source')}:"
                f"{_row_get(row, 'state')}"
            ),
            "venue_command_state": str(_row_get(row, "command_state")),
            "recovery_basis": recovery_basis,
        },
    )


def _append_bridge_schemas(
    conn: sqlite3.Connection,
    *,
    trade_db_path: str | Path | None,
) -> tuple[str, str, str]:
    """Return the explicit writer-side schema roles for one bridge connection."""

    _ensure_trades_attached_if_needed(conn, trade_db_path=trade_db_path)
    attached = {str(row[1]) for row in conn.execute("PRAGMA database_list").fetchall()}
    trade_schema = "trades" if "trades" in attached else "main"
    event_schema = "main"
    projection_schema = "main"
    _require_schema_tables(
        conn,
        schema=trade_schema,
        tables=("venue_commands", "venue_trade_facts"),
    )
    _require_schema_tables(
        conn,
        schema=event_schema,
        tables=("edli_live_order_events",),
    )
    _require_schema_tables(
        conn,
        schema=projection_schema,
        tables=("edli_live_order_projection",),
    )
    return trade_schema, event_schema, projection_schema


def _require_schema_tables(
    conn: sqlite3.Connection,
    *,
    schema: str,
    tables: tuple[str, ...],
) -> None:
    attached = {str(row[1]) for row in conn.execute("PRAGMA database_list").fetchall()}
    if schema not in attached:
        raise RuntimeError(f"EDLI_BRIDGE_SCHEMA_MISSING:{schema}")
    missing = tuple(
        table for table in tables if not _table_exists(conn, table, schema=schema)
    )
    if missing:
        raise RuntimeError(
            f"EDLI_BRIDGE_TABLE_MISSING:{schema}:{','.join(missing)}"
        )


def _ensure_trades_attached_if_needed(
    conn: sqlite3.Connection,
    *,
    trade_db_path: str | Path | None,
) -> None:
    attached = {str(row[1]) for row in conn.execute("PRAGMA database_list").fetchall()}
    if "trades" in attached and _table_exists(conn, "venue_trade_facts", schema="trades"):
        return
    explicit_trade_db_path = trade_db_path is not None
    main_has_trade_facts = _table_exists(conn, "venue_trade_facts")
    main_db_path = _database_path(conn, "main")
    if trade_db_path is None:
        try:
            from src.state.db import _zeus_trade_db_path

            trade_db_path = _zeus_trade_db_path()
        except Exception:  # noqa: BLE001
            if main_has_trade_facts:
                return
            return
    if main_has_trade_facts and (
        (_database_path_is_memory(main_db_path) and not explicit_trade_db_path)
        or _same_database_path(main_db_path, trade_db_path)
    ):
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


def _database_path(conn: sqlite3.Connection, schema: str) -> str:
    try:
        for row in conn.execute("PRAGMA database_list").fetchall():
            name = str(row[1])
            if name == schema:
                return str(row[2] or "")
    except sqlite3.Error:
        return ""
    return ""


def _database_path_is_memory(path: str) -> bool:
    return path in {"", ":memory:"}


def _same_database_path(path: str, other: str | Path) -> bool:
    if not path:
        return False
    try:
        return Path(path).resolve() == Path(other).resolve()
    except Exception:  # noqa: BLE001
        return str(path) == str(other)


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
