# Created: 2026-04-27
# Last reused/audited: 2026-05-20
# Authority basis: docs/archive/2026-Q2/task_2026-05-17_live_order_survival/LIVE_ORDER_SURVIVAL_PLAN.md S5
#                  docs/operations/task_2026-04-26_ultimate_plan/r3/slice_cards/U1.yaml
"""Append-only persistence for ExecutableMarketSnapshot.

The executable snapshot table is the U1 bridge from discovery facts to command
submission.  Rows are immutable: a later market read appends a new snapshot_id;
it never edits the evidence a prior venue_command cited.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime
from decimal import Decimal
from typing import Any, Optional

from src.contracts.executable_market_snapshot import (
    ExecutableMarketSnapshot,
    ExecutableTradeabilityStatus,
)

SNAPSHOT_TABLE = "executable_market_snapshots"
SNAPSHOT_LATEST_TABLE = "executable_market_snapshot_latest"
SNAPSHOT_INVALIDATIONS_TABLE = "executable_market_snapshot_invalidations"
ABSENT_ORDERBOOK_SIDE = "ABSENT"


def _snapshot_table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ? LIMIT 1",
        (table,),
    ).fetchone()
    return row is not None


def init_snapshot_schema(
    conn: sqlite3.Connection,
    *,
    include_latest: bool = True,
) -> None:
    """Create executable-market snapshot tables.

    The append table has a legacy world-class ghost shell and a trade-class live
    copy. The compact latest mirror is live execution evidence and belongs only
    on the trade DB.
    """

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
          tradeability_status_json TEXT NOT NULL DEFAULT '{}',
          UNIQUE (snapshot_id)
        );
        CREATE INDEX IF NOT EXISTS idx_snapshots_condition_captured
          ON executable_market_snapshots (condition_id, captured_at DESC);
        CREATE INDEX IF NOT EXISTS idx_snapshots_selected_token_captured
          ON executable_market_snapshots (selected_outcome_token_id, captured_at DESC);
        CREATE INDEX IF NOT EXISTS idx_snapshots_yes_token_captured
          ON executable_market_snapshots (yes_token_id, captured_at DESC);
        CREATE INDEX IF NOT EXISTS idx_snapshots_no_token_captured
          ON executable_market_snapshots (no_token_id, captured_at DESC);
        CREATE TRIGGER IF NOT EXISTS no_update_executable_market_snapshots
        BEFORE UPDATE ON executable_market_snapshots
        BEGIN SELECT RAISE(ABORT, 'executable_market_snapshots is APPEND-ONLY (NC-NEW-B)'); END;
        CREATE TRIGGER IF NOT EXISTS no_delete_executable_market_snapshots
        BEFORE DELETE ON executable_market_snapshots
        BEGIN SELECT RAISE(ABORT, 'executable_market_snapshots is APPEND-ONLY (NC-NEW-B)'); END;
        """
    )
    if include_latest:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS executable_market_snapshot_latest (
              condition_id TEXT NOT NULL,
              selected_outcome_token_id TEXT NOT NULL,
              snapshot_id TEXT NOT NULL,
              gamma_market_id TEXT NOT NULL,
              event_id TEXT NOT NULL,
              event_slug TEXT,
              question_id TEXT NOT NULL,
              yes_token_id TEXT NOT NULL,
              no_token_id TEXT NOT NULL,
              outcome_label TEXT CHECK (outcome_label IN ('YES','NO') OR outcome_label IS NULL),
              active INTEGER NOT NULL CHECK (active IN (0,1)),
              closed INTEGER NOT NULL CHECK (closed IN (0,1)),
              accepting_orders INTEGER CHECK (accepting_orders IN (0,1) OR accepting_orders IS NULL),
              orderbook_top_bid TEXT NOT NULL,
              orderbook_top_ask TEXT NOT NULL,
              tradeability_status_json TEXT NOT NULL DEFAULT '{}',
              captured_at TEXT NOT NULL,
              freshness_deadline TEXT NOT NULL,
              PRIMARY KEY (condition_id, selected_outcome_token_id)
            );
            CREATE INDEX IF NOT EXISTS idx_snapshot_latest_condition_captured
              ON executable_market_snapshot_latest (condition_id, captured_at DESC);
            CREATE INDEX IF NOT EXISTS idx_snapshot_latest_selected_token_captured
              ON executable_market_snapshot_latest (selected_outcome_token_id, captured_at DESC);
            CREATE INDEX IF NOT EXISTS idx_snapshot_latest_yes_token_captured
              ON executable_market_snapshot_latest (yes_token_id, captured_at DESC);
            CREATE INDEX IF NOT EXISTS idx_snapshot_latest_no_token_captured
              ON executable_market_snapshot_latest (no_token_id, captured_at DESC);
            """
        )
        init_snapshot_invalidation_schema(conn)
    # PR 2: add microstructure transparency columns (idempotent ADD COLUMN).
    # spread_observed_window_ms deferred to follow-up PR (Finding #8 decision-a).
    import logging as _logging
    _pr2_logger = _logging.getLogger(__name__)
    for _ddl in (
        "ALTER TABLE executable_market_snapshots ADD COLUMN wide_spread_display_substitution INTEGER NOT NULL DEFAULT 0 CHECK (wide_spread_display_substitution IN (0,1))",
        "ALTER TABLE executable_market_snapshots ADD COLUMN depth_at_best_ask INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE executable_market_snapshots ADD COLUMN tradeability_status_json TEXT NOT NULL DEFAULT '{}'",
    ):
        try:
            conn.execute(_ddl)
        except Exception as _exc:
            if "duplicate column" not in str(_exc).lower():
                raise
            _pr2_logger.info(
                "PR2 migration: column already exists, skipping: %s",
                _ddl.split("ADD COLUMN ")[1].split()[0],
            )


def insert_snapshot(conn: sqlite3.Connection, snapshot: ExecutableMarketSnapshot) -> None:
    """Persist one immutable executable market snapshot."""

    row = _row_from_snapshot(snapshot)
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
          captured_at, freshness_deadline,
          wide_spread_display_substitution, depth_at_best_ask,
          tradeability_status_json
        ) VALUES (
          :snapshot_id, :gamma_market_id, :event_id, :event_slug, :condition_id,
          :question_id, :yes_token_id, :no_token_id, :selected_outcome_token_id,
          :outcome_label, :enable_orderbook, :active, :closed, :accepting_orders,
          :market_start_at, :market_end_at, :market_close_at, :sports_start_at,
          :min_tick_size, :min_order_size, :fee_details_json, :token_map_json,
          :rfqe, :neg_risk, :orderbook_top_bid, :orderbook_top_ask,
          :orderbook_depth_json, :raw_gamma_payload_hash,
          :raw_clob_market_info_hash, :raw_orderbook_hash, :authority_tier,
          :captured_at, :freshness_deadline,
          :wide_spread_display_substitution, :depth_at_best_ask,
          :tradeability_status_json
        )
        """,
        row,
    )
    _upsert_latest_snapshot(conn, row)


def init_snapshot_invalidation_schema(conn: sqlite3.Connection) -> None:
    """Create append-only market-channel invalidation facts for live snapshot readers."""

    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS executable_market_snapshot_invalidations (
          invalidation_id TEXT PRIMARY KEY,
          condition_id TEXT,
          token_id TEXT,
          reason TEXT NOT NULL,
          invalidated_at TEXT NOT NULL,
          created_at TEXT NOT NULL,
          CHECK (
            COALESCE(condition_id, '') <> ''
            OR COALESCE(token_id, '') <> ''
          )
        );
        CREATE INDEX IF NOT EXISTS idx_snapshot_invalidations_condition_time
          ON executable_market_snapshot_invalidations (condition_id, invalidated_at DESC);
        CREATE INDEX IF NOT EXISTS idx_snapshot_invalidations_token_time
          ON executable_market_snapshot_invalidations (token_id, invalidated_at DESC);
        """
    )


def record_snapshot_invalidation(
    conn: sqlite3.Connection,
    *,
    condition_id: str | None,
    token_id: str | None,
    reason: str,
    invalidated_at: datetime,
) -> int:
    """Append one venue market-action invalidation fact.

    ``executable_market_snapshots`` is immutable evidence. Market-channel
    lifecycle/tick messages invalidate old rows by appending this fact; readers
    fail closed until a later snapshot whose ``captured_at`` is after the
    invalidation exists.
    """

    clean_condition = str(condition_id or "").strip() or None
    clean_token = str(token_id or "").strip() or None
    if clean_condition is None and clean_token is None:
        return 0
    clean_reason = str(reason or "").strip() or "market_channel_action"
    invalidated_at_text = _dt(invalidated_at)
    invalidation_id = hashlib.sha256(
        "|".join(
            (
                clean_condition or "",
                clean_token or "",
                clean_reason,
                invalidated_at_text,
            )
        ).encode("utf-8")
    ).hexdigest()
    init_snapshot_invalidation_schema(conn)
    cur = conn.execute(
        """
        INSERT OR IGNORE INTO executable_market_snapshot_invalidations (
          invalidation_id, condition_id, token_id, reason, invalidated_at, created_at
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            invalidation_id,
            clean_condition,
            clean_token,
            clean_reason,
            invalidated_at_text,
            invalidated_at_text,
        ),
    )
    return int(cur.rowcount or 0)


def snapshot_is_invalidated(
    conn: sqlite3.Connection,
    snapshot: ExecutableMarketSnapshot,
    *,
    checked_at: datetime | None = None,
) -> bool:
    """Return whether a later market-channel fact invalidates this snapshot."""

    return _snapshot_identity_invalidated(
        conn,
        condition_id=snapshot.condition_id,
        token_ids=(
            snapshot.selected_outcome_token_id,
            snapshot.yes_token_id,
            snapshot.no_token_id,
        ),
        captured_at=snapshot.captured_at,
        checked_at=checked_at,
    )


def snapshot_row_is_invalidated(
    conn: sqlite3.Connection,
    row: Any,
    *,
    checked_at: datetime | None = None,
) -> bool:
    """Return whether an append-only snapshot row is invalidated by a later fact."""

    captured_at_raw = _row_value(row, "captured_at")
    if not captured_at_raw:
        return False
    try:
        captured_at = _dt_parse_required(str(captured_at_raw))
    except (TypeError, ValueError):
        return False
    return _snapshot_identity_invalidated(
        conn,
        condition_id=str(_row_value(row, "condition_id") or ""),
        token_ids=(
            _row_value(row, "selected_outcome_token_id"),
            _row_value(row, "yes_token_id"),
            _row_value(row, "no_token_id"),
        ),
        captured_at=captured_at,
        checked_at=checked_at,
    )


def condition_buy_sides_fresh(
    conn: sqlite3.Connection,
    condition_id: str,
    fresh_at_iso: str,
) -> bool:
    """Return whether a condition has fresh, non-invalidated YES and NO books."""

    clean_condition_id = str(condition_id or "").strip()
    if not clean_condition_id:
        return False
    rows = _condition_buy_side_rows_from_table(
        conn,
        "executable_market_snapshot_latest",
        condition_id=clean_condition_id,
        fresh_at_iso=fresh_at_iso,
    )
    if not rows:
        rows = _condition_buy_side_rows_from_table(
            conn,
            "executable_market_snapshots",
            condition_id=clean_condition_id,
            fresh_at_iso=fresh_at_iso,
        )
    if not rows:
        return False

    yes_token_id = ""
    no_token_id = ""
    fresh_selected_tokens: set[str] = set()
    for row in rows:
        yes = str(_row_value(row, "yes_token_id") or "").strip()
        no = str(_row_value(row, "no_token_id") or "").strip()
        selected = str(_row_value(row, "selected_outcome_token_id") or "").strip()
        if yes and not yes_token_id:
            yes_token_id = yes
        if no and not no_token_id:
            no_token_id = no
        if selected:
            fresh_selected_tokens.add(selected)
    if not yes_token_id or not no_token_id:
        return False
    return yes_token_id in fresh_selected_tokens and no_token_id in fresh_selected_tokens


def _condition_buy_side_rows_from_table(
    conn: sqlite3.Connection,
    table_name: str,
    *,
    condition_id: str,
    fresh_at_iso: str,
) -> list[Any]:
    if not _snapshot_table_exists(conn, table_name):
        return []
    invalidation_filter = ""
    if _snapshot_table_exists(conn, SNAPSHOT_INVALIDATIONS_TABLE):
        invalidation_filter = f"""
          AND NOT EXISTS (
                SELECT 1
                  FROM {SNAPSHOT_INVALIDATIONS_TABLE} inv
                 WHERE inv.invalidated_at >= {table_name}.captured_at
                   AND (
                        inv.condition_id = {table_name}.condition_id
                        OR inv.token_id = {table_name}.selected_outcome_token_id
                        OR inv.token_id = {table_name}.yes_token_id
                        OR inv.token_id = {table_name}.no_token_id
                   )
          )
        """
    try:
        cur = conn.execute(
            f"""
            SELECT yes_token_id, no_token_id, selected_outcome_token_id
              FROM {table_name}
             WHERE condition_id = ?
               AND freshness_deadline >= ?
               {invalidation_filter}
             ORDER BY captured_at DESC, snapshot_id DESC
            """,
            (condition_id, fresh_at_iso),
        )
        names = [description[0] for description in cur.description]
        return [
            {name: row[name] for name in names}
            if isinstance(row, sqlite3.Row)
            else dict(zip(names, row))
            for row in cur.fetchall()
        ]
    except Exception:
        return []


def _snapshot_identity_invalidated(
    conn: sqlite3.Connection,
    *,
    condition_id: str | None,
    token_ids: tuple[Any, ...],
    captured_at: datetime,
    checked_at: datetime | None,
) -> bool:
    if not _snapshot_table_exists(conn, SNAPSHOT_INVALIDATIONS_TABLE):
        return False
    clean_condition = str(condition_id or "").strip()
    clean_tokens = tuple(
        dict.fromkeys(str(token_id or "").strip() for token_id in token_ids if str(token_id or "").strip())
    )
    predicates: list[str] = []
    params: list[object] = [_dt(captured_at)]
    if checked_at is not None:
        params.append(_dt(checked_at))
    if clean_condition:
        predicates.append("condition_id = ?")
        params.append(clean_condition)
    for token_id in clean_tokens:
        predicates.append("token_id = ?")
        params.append(token_id)
    if not predicates:
        return False
    upper_bound = "AND invalidated_at <= ?" if checked_at is not None else ""
    row = conn.execute(
        f"""
        SELECT 1
          FROM executable_market_snapshot_invalidations
         WHERE invalidated_at >= ?
           {upper_bound}
           AND ({' OR '.join(predicates)})
         LIMIT 1
        """,
        tuple(params),
    ).fetchone()
    return row is not None


def _upsert_latest_snapshot(conn: sqlite3.Connection, row: dict[str, Any]) -> None:
    """Update the compact latest-state mirror after appending immutable evidence."""

    if not str(row.get("selected_outcome_token_id") or "").strip():
        return
    if not _snapshot_table_exists(conn, SNAPSHOT_LATEST_TABLE):
        return
    conn.execute(
        """
        INSERT INTO executable_market_snapshot_latest (
          condition_id, selected_outcome_token_id, snapshot_id, gamma_market_id,
          event_id, event_slug, question_id, yes_token_id, no_token_id,
          outcome_label, active, closed, accepting_orders, orderbook_top_bid,
          orderbook_top_ask, tradeability_status_json, captured_at,
          freshness_deadline
        ) VALUES (
          :condition_id, :selected_outcome_token_id, :snapshot_id, :gamma_market_id,
          :event_id, :event_slug, :question_id, :yes_token_id, :no_token_id,
          :outcome_label, :active, :closed, :accepting_orders, :orderbook_top_bid,
          :orderbook_top_ask, :tradeability_status_json, :captured_at,
          :freshness_deadline
        )
        ON CONFLICT(condition_id, selected_outcome_token_id) DO UPDATE SET
          snapshot_id = excluded.snapshot_id,
          gamma_market_id = excluded.gamma_market_id,
          event_id = excluded.event_id,
          event_slug = excluded.event_slug,
          question_id = excluded.question_id,
          yes_token_id = excluded.yes_token_id,
          no_token_id = excluded.no_token_id,
          outcome_label = excluded.outcome_label,
          active = excluded.active,
          closed = excluded.closed,
          accepting_orders = excluded.accepting_orders,
          orderbook_top_bid = excluded.orderbook_top_bid,
          orderbook_top_ask = excluded.orderbook_top_ask,
          tradeability_status_json = excluded.tradeability_status_json,
          captured_at = excluded.captured_at,
          freshness_deadline = excluded.freshness_deadline
        WHERE excluded.captured_at >= executable_market_snapshot_latest.captured_at
        """,
        row,
    )


def get_snapshot(
    conn: sqlite3.Connection,
    snapshot_id: str,
) -> Optional[ExecutableMarketSnapshot]:
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
) -> Optional[ExecutableMarketSnapshot]:
    """Return latest non-expired snapshot for a condition_id."""

    saved = conn.row_factory
    conn.row_factory = sqlite3.Row
    fresh_as_of_text = _dt(fresh_as_of)
    try:
        try:
            latest = conn.execute(
                """
                SELECT snapshot_id
                FROM executable_market_snapshot_latest
                WHERE condition_id = ?
                  AND freshness_deadline >= ?
                ORDER BY captured_at DESC
                LIMIT 1
                """,
                (condition_id, fresh_as_of_text),
            ).fetchone()
        except Exception:
            latest = None
        if latest is not None:
            row = conn.execute(
                "SELECT * FROM executable_market_snapshots WHERE snapshot_id = ?",
                (latest["snapshot_id"],),
            ).fetchone()
            if row is not None:
                snapshot = _snapshot_from_row(row)
                if not snapshot_is_invalidated(conn, snapshot, checked_at=fresh_as_of):
                    return snapshot
        rows = conn.execute(
            """
            SELECT * FROM executable_market_snapshots
            WHERE condition_id = ?
              AND freshness_deadline >= ?
            ORDER BY captured_at DESC
            """,
            (condition_id, fresh_as_of_text),
        ).fetchall()
    finally:
        conn.row_factory = saved
    for row in rows:
        snapshot = _snapshot_from_row(row)
        if not snapshot_is_invalidated(conn, snapshot, checked_at=fresh_as_of):
            return snapshot
    return None


def executable_snapshot_from_row(row: sqlite3.Row) -> ExecutableMarketSnapshot:
    """Public wrapper so callers outside this module can hydrate a snapshot row
    without importing the private ``_snapshot_from_row`` symbol."""
    return _snapshot_from_row(row)


def _row_from_snapshot(snapshot: ExecutableMarketSnapshot) -> dict[str, Any]:
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
        "orderbook_top_bid": _decimal_or_absent_text(snapshot.orderbook_top_bid),
        "orderbook_top_ask": _decimal_or_absent_text(snapshot.orderbook_top_ask),
        "orderbook_depth_json": snapshot.orderbook_depth_jsonb,
        "raw_gamma_payload_hash": snapshot.raw_gamma_payload_hash,
        "raw_clob_market_info_hash": snapshot.raw_clob_market_info_hash,
        "raw_orderbook_hash": snapshot.raw_orderbook_hash,
        "authority_tier": snapshot.authority_tier,
        "captured_at": _dt(snapshot.captured_at),
        "freshness_deadline": _dt(snapshot.freshness_deadline),
        # PR 2 microstructure fields
        "wide_spread_display_substitution": int(snapshot.wide_spread_display_substitution),
        "depth_at_best_ask": snapshot.depth_at_best_ask,
        "tradeability_status_json": _json(snapshot.tradeability_status.to_json_dict())
        if snapshot.tradeability_status is not None
        else "{}",
    }


def _snapshot_from_row(row: sqlite3.Row) -> ExecutableMarketSnapshot:
    return ExecutableMarketSnapshot(
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
        orderbook_top_bid=_decimal_or_absent(row["orderbook_top_bid"]),
        orderbook_top_ask=_decimal_or_absent(row["orderbook_top_ask"]),
        orderbook_depth_jsonb=row["orderbook_depth_json"],
        raw_gamma_payload_hash=row["raw_gamma_payload_hash"],
        raw_clob_market_info_hash=row["raw_clob_market_info_hash"],
        raw_orderbook_hash=row["raw_orderbook_hash"],
        authority_tier=row["authority_tier"],
        captured_at=_dt_parse_required(row["captured_at"]),
        freshness_deadline=_dt_parse_required(row["freshness_deadline"]),
        # PR 2 microstructure fields — default 0 for pre-PR2 legacy rows
        wide_spread_display_substitution=bool(row["wide_spread_display_substitution"] or 0),
        depth_at_best_ask=int(row["depth_at_best_ask"] or 0),
        tradeability_status=_tradeability_status_from_row(row),
    )


def _tradeability_status_from_row(row: sqlite3.Row) -> ExecutableTradeabilityStatus:
    try:
        raw = row["tradeability_status_json"]
    except (IndexError, KeyError):
        raw = None
    payload: dict[str, Any] = {}
    if raw:
        try:
            parsed = json.loads(str(raw))
        except (TypeError, ValueError, json.JSONDecodeError):
            parsed = {}
        if isinstance(parsed, dict):
            payload = parsed
    if payload:
        return ExecutableTradeabilityStatus.from_mapping(payload)
    return ExecutableTradeabilityStatus.from_legacy_snapshot_flags(
        active=bool(row["active"]),
        closed=bool(row["closed"]),
        accepting_orders=_bool_or_none(row["accepting_orders"]),
        enable_orderbook=bool(row["enable_orderbook"]),
    )


def _nullable_bool(value: Optional[bool]) -> Optional[int]:
    if value is None:
        return None
    return int(bool(value))


def _bool_or_none(value: Any) -> Optional[bool]:
    if value is None:
        return None
    return bool(value)


def _row_value(row: Any, key: str) -> Any:
    if isinstance(row, dict):
        return row.get(key)
    try:
        return row[key]
    except (IndexError, KeyError, TypeError):
        return None


def _decimal_or_absent_text(value: Decimal | None) -> str:
    if value is None:
        return ABSENT_ORDERBOOK_SIDE
    return str(value)


def _decimal_or_absent(value: Any) -> Decimal | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.upper() == ABSENT_ORDERBOOK_SIDE:
        return None
    return Decimal(text)


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
