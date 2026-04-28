# Created: 2026-04-27
# Last reused/audited: 2026-04-27
# Authority basis: docs/operations/task_2026-04-26_ultimate_plan/r3/slice_cards/U1.yaml
"""Append-only persistence for ExecutableMarketSnapshotV2.

The executable snapshot table is the U1 bridge from discovery facts to command
submission.  Rows are immutable: a later market read appends a new snapshot_id;
it never edits the evidence a prior venue_command cited.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from decimal import Decimal
from typing import Any, Optional

from src.contracts.executable_market_snapshot_v2 import ExecutableMarketSnapshotV2


SNAPSHOT_TABLE = "executable_market_snapshots"


def init_snapshot_schema(conn: sqlite3.Connection) -> None:
    """Create the U1 append-only executable-market snapshot table."""

    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS executable_market_snapshots (
          snapshot_id TEXT PRIMARY KEY,
          gamma_market_id TEXT NOT NULL,
          event_id TEXT NOT NULL,
          event_slug TEXT,
          condition_id TEXT NOT NULL,
          question_id TEXT NOT NULL,
          yes_token_id TEXT NOT NULL,
          no_token_id TEXT NOT NULL,
          selected_outcome_token_id TEXT,
          outcome_label TEXT CHECK (outcome_label IN ('YES','NO') OR outcome_label IS NULL),
          enable_orderbook INTEGER NOT NULL CHECK (enable_orderbook IN (0,1)),
          active INTEGER NOT NULL CHECK (active IN (0,1)),
          closed INTEGER NOT NULL CHECK (closed IN (0,1)),
          accepting_orders INTEGER CHECK (accepting_orders IN (0,1) OR accepting_orders IS NULL),
          market_start_at TEXT,
          market_end_at TEXT,
          market_close_at TEXT,
          sports_start_at TEXT,
          min_tick_size TEXT NOT NULL,
          min_order_size TEXT NOT NULL,
          fee_details_json TEXT NOT NULL,
          token_map_json TEXT NOT NULL,
          rfqe INTEGER CHECK (rfqe IN (0,1) OR rfqe IS NULL),
          neg_risk INTEGER NOT NULL CHECK (neg_risk IN (0,1)),
          orderbook_top_bid TEXT NOT NULL,
          orderbook_top_ask TEXT NOT NULL,
          orderbook_depth_json TEXT NOT NULL,
          raw_gamma_payload_hash TEXT NOT NULL,
          raw_clob_market_info_hash TEXT NOT NULL,
          raw_orderbook_hash TEXT NOT NULL,
          authority_tier TEXT NOT NULL CHECK (authority_tier IN ('GAMMA','DATA','CLOB','CHAIN')),
          captured_at TEXT NOT NULL,
          freshness_deadline TEXT NOT NULL,
          UNIQUE (snapshot_id)
        );
        CREATE INDEX IF NOT EXISTS idx_snapshots_condition_captured
          ON executable_market_snapshots (condition_id, captured_at DESC);
        CREATE TRIGGER IF NOT EXISTS no_update_executable_market_snapshots
        BEFORE UPDATE ON executable_market_snapshots
        BEGIN SELECT RAISE(ABORT, 'executable_market_snapshots is APPEND-ONLY (NC-NEW-B)'); END;
        CREATE TRIGGER IF NOT EXISTS no_delete_executable_market_snapshots
        BEFORE DELETE ON executable_market_snapshots
        BEGIN SELECT RAISE(ABORT, 'executable_market_snapshots is APPEND-ONLY (NC-NEW-B)'); END;
        """
    )


def insert_snapshot(conn: sqlite3.Connection, snapshot: ExecutableMarketSnapshotV2) -> None:
    """Persist one immutable executable market snapshot."""

    conn.execute(
        """
        INSERT INTO executable_market_snapshots (
          snapshot_id, gamma_market_id, event_id, event_slug, condition_id,
          question_id, yes_token_id, no_token_id, selected_outcome_token_id,
          outcome_label, enable_orderbook, active, closed, accepting_orders,
          market_start_at, market_end_at, market_close_at, sports_start_at,
          min_tick_size, min_order_size, fee_details_json, token_map_json,
          rfqe, neg_risk, orderbook_top_bid, orderbook_top_ask,
          orderbook_depth_json, raw_gamma_payload_hash,
          raw_clob_market_info_hash, raw_orderbook_hash, authority_tier,
          captured_at, freshness_deadline
        ) VALUES (
          :snapshot_id, :gamma_market_id, :event_id, :event_slug, :condition_id,
          :question_id, :yes_token_id, :no_token_id, :selected_outcome_token_id,
          :outcome_label, :enable_orderbook, :active, :closed, :accepting_orders,
          :market_start_at, :market_end_at, :market_close_at, :sports_start_at,
          :min_tick_size, :min_order_size, :fee_details_json, :token_map_json,
          :rfqe, :neg_risk, :orderbook_top_bid, :orderbook_top_ask,
          :orderbook_depth_json, :raw_gamma_payload_hash,
          :raw_clob_market_info_hash, :raw_orderbook_hash, :authority_tier,
          :captured_at, :freshness_deadline
        )
        """,
        _row_from_snapshot(snapshot),
    )


def get_snapshot(
    conn: sqlite3.Connection,
    snapshot_id: str,
) -> Optional[ExecutableMarketSnapshotV2]:
    """Return a snapshot by id or None when absent."""

    saved = conn.row_factory
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT * FROM executable_market_snapshots WHERE snapshot_id = ?",
            (snapshot_id,),
        ).fetchone()
    finally:
        conn.row_factory = saved
    return _snapshot_from_row(row) if row is not None else None


def latest_snapshot_for_market(
    conn: sqlite3.Connection,
    condition_id: str,
    fresh_as_of: datetime,
) -> Optional[ExecutableMarketSnapshotV2]:
    """Return latest non-expired snapshot for a condition_id."""

    saved = conn.row_factory
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            """
            SELECT * FROM executable_market_snapshots
            WHERE condition_id = ?
              AND freshness_deadline >= ?
            ORDER BY captured_at DESC
            LIMIT 1
            """,
            (condition_id, _dt(fresh_as_of)),
        ).fetchone()
    finally:
        conn.row_factory = saved
    return _snapshot_from_row(row) if row is not None else None


def _row_from_snapshot(snapshot: ExecutableMarketSnapshotV2) -> dict[str, Any]:
    return {
        "snapshot_id": snapshot.snapshot_id,
        "gamma_market_id": snapshot.gamma_market_id,
        "event_id": snapshot.event_id,
        "event_slug": snapshot.event_slug,
        "condition_id": snapshot.condition_id,
        "question_id": snapshot.question_id,
        "yes_token_id": snapshot.yes_token_id,
        "no_token_id": snapshot.no_token_id,
        "selected_outcome_token_id": snapshot.selected_outcome_token_id,
        "outcome_label": snapshot.outcome_label,
        "enable_orderbook": int(snapshot.enable_orderbook),
        "active": int(snapshot.active),
        "closed": int(snapshot.closed),
        "accepting_orders": _nullable_bool(snapshot.accepting_orders),
        "market_start_at": _dt_or_none(snapshot.market_start_at),
        "market_end_at": _dt_or_none(snapshot.market_end_at),
        "market_close_at": _dt_or_none(snapshot.market_close_at),
        "sports_start_at": _dt_or_none(snapshot.sports_start_at),
        "min_tick_size": str(snapshot.min_tick_size),
        "min_order_size": str(snapshot.min_order_size),
        "fee_details_json": _json(snapshot.fee_details),
        "token_map_json": _json(snapshot.token_map_raw),
        "rfqe": _nullable_bool(snapshot.rfqe),
        "neg_risk": int(snapshot.neg_risk),
        "orderbook_top_bid": str(snapshot.orderbook_top_bid),
        "orderbook_top_ask": str(snapshot.orderbook_top_ask),
        "orderbook_depth_json": snapshot.orderbook_depth_jsonb,
        "raw_gamma_payload_hash": snapshot.raw_gamma_payload_hash,
        "raw_clob_market_info_hash": snapshot.raw_clob_market_info_hash,
        "raw_orderbook_hash": snapshot.raw_orderbook_hash,
        "authority_tier": snapshot.authority_tier,
        "captured_at": _dt(snapshot.captured_at),
        "freshness_deadline": _dt(snapshot.freshness_deadline),
    }


def _snapshot_from_row(row: sqlite3.Row) -> ExecutableMarketSnapshotV2:
    return ExecutableMarketSnapshotV2(
        snapshot_id=row["snapshot_id"],
        gamma_market_id=row["gamma_market_id"],
        event_id=row["event_id"],
        event_slug=row["event_slug"] or "",
        condition_id=row["condition_id"],
        question_id=row["question_id"],
        yes_token_id=row["yes_token_id"],
        no_token_id=row["no_token_id"],
        selected_outcome_token_id=row["selected_outcome_token_id"],
        outcome_label=row["outcome_label"],
        enable_orderbook=bool(row["enable_orderbook"]),
        active=bool(row["active"]),
        closed=bool(row["closed"]),
        accepting_orders=_bool_or_none(row["accepting_orders"]),
        market_start_at=_dt_parse(row["market_start_at"]),
        market_end_at=_dt_parse(row["market_end_at"]),
        market_close_at=_dt_parse(row["market_close_at"]),
        sports_start_at=_dt_parse(row["sports_start_at"]),
        min_tick_size=Decimal(row["min_tick_size"]),
        min_order_size=Decimal(row["min_order_size"]),
        fee_details=json.loads(row["fee_details_json"]),
        token_map_raw=json.loads(row["token_map_json"]),
        rfqe=_bool_or_none(row["rfqe"]),
        neg_risk=bool(row["neg_risk"]),
        orderbook_top_bid=Decimal(row["orderbook_top_bid"]),
        orderbook_top_ask=Decimal(row["orderbook_top_ask"]),
        orderbook_depth_jsonb=row["orderbook_depth_json"],
        raw_gamma_payload_hash=row["raw_gamma_payload_hash"],
        raw_clob_market_info_hash=row["raw_clob_market_info_hash"],
        raw_orderbook_hash=row["raw_orderbook_hash"],
        authority_tier=row["authority_tier"],
        captured_at=_dt_parse_required(row["captured_at"]),
        freshness_deadline=_dt_parse_required(row["freshness_deadline"]),
    )


def _nullable_bool(value: Optional[bool]) -> Optional[int]:
    if value is None:
        return None
    return int(bool(value))


def _bool_or_none(value: Any) -> Optional[bool]:
    if value is None:
        return None
    return bool(value)


def _dt(value: datetime) -> str:
    return value.isoformat()


def _dt_or_none(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat() if value is not None else None


def _dt_parse(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    return _dt_parse_required(value)


def _dt_parse_required(value: str) -> datetime:
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
