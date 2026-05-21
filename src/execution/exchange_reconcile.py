"""R3 M5 exchange reconciliation sweep.

This module reconciles read-only exchange observations against Zeus's durable
venue-command/fact journal.  It is intentionally not an execution actuator:
exchange-only state becomes an ``exchange_reconcile_findings`` row, not a new
``venue_commands`` row, and no live venue submit/cancel/redeem side effects are
performed here.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from types import SimpleNamespace
from typing import Any, Literal, Mapping, Optional

from src.architecture.decorators import capability, protects
from src.state.portfolio import INACTIVE_RUNTIME_STATES
from src.state.venue_command_repo import trade_fact_has_positive_fill_economics

logger = logging.getLogger(__name__)

FindingKind = Literal[
    "exchange_ghost_order",
    "local_orphan_order",
    "unrecorded_trade",
    "position_drift",
    "heartbeat_suspected_cancel",
    "cutover_wipe",
]
ReconcileContext = Literal["periodic", "ws_gap", "heartbeat_loss", "cutover", "operator"]

_FINDING_KINDS = frozenset(
    {
        "exchange_ghost_order",
        "local_orphan_order",
        "unrecorded_trade",
        "position_drift",
        "heartbeat_suspected_cancel",
        "cutover_wipe",
    }
)
_CONTEXTS = frozenset({"periodic", "ws_gap", "heartbeat_loss", "cutover", "operator"})
_OPEN_LOCAL_STATES = frozenset(
    {
        "ACKED",
        "PARTIAL",
        "CANCEL_PENDING",
        "UNKNOWN",
        "SUBMIT_UNKNOWN_SIDE_EFFECT",
        "REVIEW_REQUIRED",
    }
)
_OPEN_ORDER_FACT_STATES = frozenset({"LIVE", "RESTING", "CANCEL_UNKNOWN"})
_OPEN_POINT_ORDER_STATES = _OPEN_ORDER_FACT_STATES | frozenset(
    {"OPEN", "PARTIAL", "PARTIALLY_MATCHED", "PARTIALLY_FILLED"}
)
_TRADE_FACT_STATES = frozenset({"MATCHED", "MINED", "CONFIRMED", "RETRYING", "FAILED"})
_CONFIRMED_POSITION_FACT_STATES = frozenset({"CONFIRMED"})
_OPTIMISTIC_POSITION_FACT_STATES = frozenset({"MATCHED", "MINED"})
_POSITION_DRIFT_ABS_TOLERANCE = Decimal("0.0001")
_POSITION_API_VISIBILITY_FLOOR = Decimal("0.01")
_ENTRY_FILL_PROJECTION_PHASES = frozenset({"pending_entry", "active", "day0_window"})
_EXIT_FILL_PROJECTION_PHASES = frozenset({"active", "day0_window", "pending_exit", "economically_closed"})
_TERMINAL_ORDER_FACT_STATES = frozenset({"MATCHED", "CANCEL_CONFIRMED", "EXPIRED", "VENUE_WIPED"})
_PENDING_EXIT_NON_CURRENT_ORDER_STATUSES = frozenset({"filled", "sell_filled"})
_REDEEM_PENDING_WALLET_HOLDING_STATES = frozenset(
    {
        "REDEEM_INTENT_CREATED",
        "REDEEM_SUBMITTED",
        "REDEEM_TX_HASHED",
        "REDEEM_RETRYING",
        "REDEEM_OPERATOR_REQUIRED",
    }
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS exchange_reconcile_findings (
  finding_id TEXT PRIMARY KEY,
  kind TEXT NOT NULL CHECK (kind IN (
    'exchange_ghost_order','local_orphan_order','unrecorded_trade',
    'position_drift','heartbeat_suspected_cancel','cutover_wipe'
  )),
  subject_id TEXT NOT NULL,
  context TEXT NOT NULL CHECK (context IN ('periodic','ws_gap','heartbeat_loss','cutover','operator')),
  evidence_json TEXT NOT NULL,
  recorded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  resolved_at TEXT,
  resolution TEXT,
  resolved_by TEXT
);
CREATE INDEX IF NOT EXISTS idx_findings_unresolved
  ON exchange_reconcile_findings (resolved_at)
  WHERE resolved_at IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS ux_findings_unresolved_subject
  ON exchange_reconcile_findings (kind, subject_id, context)
  WHERE resolved_at IS NULL;
"""


@dataclass(frozen=True)
class ReconcileFinding:
    finding_id: str
    kind: FindingKind
    subject_id: str
    context: ReconcileContext
    evidence_json: str
    recorded_at: datetime


@dataclass(frozen=True)
class FreshReconcileSnapshot:
    adapter: Any
    captured_surfaces: tuple[str, ...]
    unavailable_surfaces: tuple[str, ...]


def init_exchange_reconcile_schema(conn: sqlite3.Connection) -> None:
    """Create the M5 findings table if absent."""

    conn.executescript(_SCHEMA)


def fresh_reconcile_snapshot(
    adapter: Any,
    *,
    observed_at: datetime | str | None = None,
    trade_order_ids: set[str] | frozenset[str] | None = None,
) -> FreshReconcileSnapshot:
    """Capture venue read surfaces and attach explicit freshness evidence.

    ``run_reconcile_sweep`` intentionally refuses to infer absence from a raw
    adapter without read freshness. Live runtime adapters expose methods, not a
    prebuilt freshness map, so the runtime first snapshots successful reads and
    reconciles against that immutable evidence object.
    """

    observed = _coerce_dt(observed_at)
    captured: dict[str, Any] = {}
    unavailable: list[str] = []

    captured["open_orders"] = _call_required(adapter, "get_open_orders")
    local_order_ids = {str(order_id) for order_id in (trade_order_ids or set()) if str(order_id).strip()}
    open_order_ids = {_order_id(item) for item in captured["open_orders"] if _order_id(item)}
    missing_local_order_ids = sorted(local_order_ids - open_order_ids)
    get_order = getattr(adapter, "get_order", None)
    if callable(get_order) and missing_local_order_ids:
        captured["point_orders"] = {
            order_id: get_order(order_id)
            for order_id in missing_local_order_ids
        }
    for surface, method in (("trades", "get_trades"), ("positions", "get_positions")):
        fn = getattr(adapter, method, None)
        if not callable(fn):
            unavailable.append(surface)
            continue
        try:
            rows = list(fn() or [])
            if surface == "trades" and trade_order_ids is not None:
                rows = [
                    row for row in rows
                    if set(_trade_order_ids(_raw(row))) & set(trade_order_ids)
                ]
            captured[surface] = rows
        except Exception as exc:
            if exc.__class__.__name__ == "V2ReadUnavailable":
                unavailable.append(surface)
                continue
            raise

    freshness = {
        surface: {"ok": True, "fresh": True, "captured_at": observed.isoformat()}
        for surface in captured
    }
    snapshot = SimpleNamespace(read_freshness=freshness)
    snapshot.get_open_orders = lambda: list(captured["open_orders"])
    if "point_orders" in captured:
        snapshot.get_order = lambda order_id: captured["point_orders"].get(str(order_id))
    if "trades" in captured:
        snapshot.get_trades = lambda: list(captured["trades"])
    if "positions" in captured:
        snapshot.get_positions = lambda: list(captured["positions"])
    return FreshReconcileSnapshot(
        adapter=snapshot,
        captured_surfaces=tuple(sorted(captured)),
        unavailable_surfaces=tuple(sorted(unavailable)),
    )


def run_ws_gap_reconcile_and_clear(
    adapter: Any,
    conn: sqlite3.Connection,
    *,
    ws_guard: Any = None,
    observed_at: datetime | str | None = None,
) -> dict[str, Any]:
    """Run a fresh M5 sweep for a WS gap and clear the latch only on proof.

    A live open/PARTIAL order is not itself a reason to stay latched after M5:
    the fresh open-order/trade snapshot is the missing proof that the gap did
    not hide an unresolved side effect. Findings or missing trade enumeration
    keep the latch closed.
    """

    if ws_guard is None:
        from src.control import ws_gap_guard as ws_guard

    observed = _coerce_dt(observed_at)
    summary = ws_guard.summary(now=observed)
    if not bool(summary.get("m5_reconcile_required", False)):
        return {"status": "not_required", "findings": 0, "unresolved_findings": 0}

    local_order_ids = set(_local_open_order_ids(conn))
    snapshot = fresh_reconcile_snapshot(
        adapter,
        observed_at=observed,
        trade_order_ids=local_order_ids,
    )
    findings = run_reconcile_sweep(snapshot.adapter, conn, context="ws_gap", observed_at=observed)
    unresolved = list_unresolved_findings(conn)
    result = {
        "status": "blocked",
        "findings": len(findings),
        "unresolved_findings": len(unresolved),
        "captured_surfaces": list(snapshot.captured_surfaces),
        "unavailable_surfaces": list(snapshot.unavailable_surfaces),
    }
    if "trades" not in snapshot.captured_surfaces:
        result["reason"] = "trades_read_unavailable"
        return result
    if findings or unresolved:
        result["reason"] = "m5_findings_unresolved"
        return result

    conn.commit()
    ws_guard.clear_after_m5_reconcile(
        observed_at=observed,
        stale_after_seconds=int(summary.get("stale_after_seconds") or 0) or None,
        findings_count=len(findings),
        unresolved_findings_count=len(unresolved),
    )
    result["status"] = "cleared"
    result["reason"] = "m5_reconcile_complete"
    return result


def refresh_unresolved_reconcile_findings(
    adapter: Any,
    conn: sqlite3.Connection,
    *,
    observed_at: datetime | str | None = None,
    context: ReconcileContext = "ws_gap",
) -> dict[str, Any]:
    """Refresh only already-open position-drift findings from fresh venue truth.

    This is intentionally narrower than ``run_reconcile_sweep``.  When the WS
    latch has already cleared, risk can still remain reduce-only because late
    CONFIRMED trade facts arrived after the original M5 sweep.  A partial
    subject-scoped refresh must not reinterpret absent unrelated positions as
    global exchange absence.
    """

    _validate_context(context)
    init_exchange_reconcile_schema(conn)
    observed = _coerce_dt(observed_at)
    token_ids = _unresolved_position_drift_tokens(conn)
    trade_ids = _unresolved_unrecorded_trade_ids(conn)
    if not token_ids and not trade_ids:
        return {"status": "not_required", "resolved": 0, "remaining": 0}

    order_ids = _local_order_ids_for_tokens(conn, token_ids) | _order_ids_for_unrecorded_trade_findings(conn)
    snapshot = fresh_reconcile_snapshot(
        adapter,
        observed_at=observed,
        trade_order_ids=order_ids,
    )
    if "trades" not in snapshot.captured_surfaces:
        return {
            "status": "blocked",
            "reason": "trades_read_unavailable",
            "subject_count": len(token_ids) + len(trade_ids),
            "captured_surfaces": list(snapshot.captured_surfaces),
            "unavailable_surfaces": list(snapshot.unavailable_surfaces),
        }
    if token_ids and "positions" not in snapshot.captured_surfaces:
        return {
            "status": "blocked",
            "reason": "positions_read_unavailable",
            "subject_count": len(token_ids) + len(trade_ids),
            "captured_surfaces": list(snapshot.captured_surfaces),
            "unavailable_surfaces": list(snapshot.unavailable_surfaces),
        }

    local_by_order = _local_commands_by_order(conn)
    new_findings: list[ReconcileFinding] = []
    for trade in snapshot.adapter.get_trades():
        raw = _raw(trade)
        venue_trade_id = _trade_id(raw)
        subject_id = venue_trade_id or _stable_subject("trade", raw)
        state = _trade_state(raw)
        order_id, command = _local_command_for_trade(raw, local_by_order)
        if state is None:
            new_findings.append(
                record_finding(
                    conn,
                    kind="unrecorded_trade",
                    subject_id=subject_id,
                    context=context,
                    evidence={
                        "exchange_trade": raw,
                        "reason": "exchange_trade_unknown_trade_state",
                        "raw_state": _first_present(raw, "state", "status", default=None),
                    },
                    recorded_at=observed,
                )
            )
            continue
        if command is None or not order_id:
            new_findings.append(
                record_finding(
                    conn,
                    kind="unrecorded_trade",
                    subject_id=subject_id,
                    context=context,
                    evidence={
                        "exchange_trade": raw,
                        "reason": "exchange_trade_unlinked_to_local_command",
                        "candidate_order_ids": _trade_order_ids(raw),
                    },
                    recorded_at=observed,
                )
            )
            continue
        if not venue_trade_id:
            new_findings.append(
                record_finding(
                    conn,
                    kind="unrecorded_trade",
                    subject_id=subject_id,
                    context=context,
                    evidence={
                        "exchange_trade": raw,
                        "local_command": _command_evidence(command),
                        "reason": "exchange_trade_missing_venue_trade_identity",
                    },
                    recorded_at=observed,
                )
            )
            continue
        finding = _append_linkable_trade_fact_if_missing(
            conn,
            command,
            raw,
            venue_trade_id,
            observed,
            state=state,
            context=context,
            matched_order_id=order_id,
        )
        if finding is not None:
            new_findings.append(finding)

    repair_summary = reconcile_recorded_maker_fill_economics(conn, observed_at=observed)
    before_remaining = _unresolved_position_drift_count(conn, token_ids) + _unresolved_trade_count(conn, trade_ids)
    if token_ids:
        _resolve_position_drift_tokens_from_current_truth(
            conn,
            token_ids=token_ids,
            positions=snapshot.adapter.get_positions(),
            observed_at=observed,
        )
    remaining = _unresolved_position_drift_count(conn, token_ids) + _unresolved_trade_count(conn, trade_ids)
    resolved = max(0, before_remaining - remaining)
    return {
        "status": "resolved" if remaining == 0 and not new_findings else "blocked",
        "reason": "reconcile_finding_refresh_complete" if remaining == 0 else "reconcile_findings_remain",
        "subject_count": len(token_ids) + len(trade_ids),
        "resolved": resolved,
        "remaining": remaining,
        "new_findings": len(new_findings),
        "captured_surfaces": list(snapshot.captured_surfaces),
        "unavailable_surfaces": list(snapshot.unavailable_surfaces),
        "repair_summary": repair_summary,
    }


@capability("on_chain_mutation", lease=True)
@protects("INV-21", "INV-04")
def run_reconcile_sweep(
    adapter: Any,
    conn: sqlite3.Connection,
    *,
    context: ReconcileContext,
    observed_at: datetime | str | None = None,
) -> list[ReconcileFinding]:
    """Diff exchange truth against the local journal and write findings.

    ``adapter`` is read only: this function calls enumeration methods only
    (``get_open_orders``, optional ``get_trades``, optional ``get_positions``).
    Missing/unlinkable venue state is recorded as a finding.  Linkable missing
    exchange trades are appended as U2 trade facts because those facts have a
    known command foreign key and are journal truth, not new command authority.
    """

    _validate_context(context)
    init_exchange_reconcile_schema(conn)
    observed = _coerce_dt(observed_at)

    findings: list[ReconcileFinding] = []
    _assert_adapter_read_fresh(adapter, "open_orders", observed)
    open_orders = _call_required(adapter, "get_open_orders")
    open_order_ids = {_order_id(item) for item in open_orders if _order_id(item)}
    local_by_order = _local_commands_by_order(conn)

    for order in open_orders:
        order_id = _order_id(order)
        if not order_id:
            continue
        if order_id not in local_by_order:
            findings.append(
                record_finding(
                    conn,
                    kind="exchange_ghost_order",
                    subject_id=order_id,
                    context=context,
                    evidence={
                        "exchange_order": _raw(order),
                        "reason": "exchange_open_order_absent_from_venue_commands",
                    },
                    recorded_at=observed,
                )
            )

    trades_available = callable(getattr(adapter, "get_trades", None))
    if trades_available:
        _assert_adapter_read_fresh(adapter, "trades", observed)
    trades = adapter.get_trades() if trades_available else []
    trade_order_ids: set[str] = set()
    trade_fills_by_order_id: dict[str, Decimal] = {}
    for trade in trades or []:
        raw = _raw(trade)
        venue_trade_id = _trade_id(raw)
        subject_id = venue_trade_id or _stable_subject("trade", raw)
        order_id, command = _local_command_for_trade(raw, local_by_order)
        candidate_order_ids = _trade_order_ids(raw)
        state = _trade_state(raw)
        if state is None:
            findings.append(
                record_finding(
                    conn,
                    kind="unrecorded_trade",
                    subject_id=subject_id,
                    context=context,
                    evidence={
                        "exchange_trade": raw,
                        "reason": "exchange_trade_unknown_trade_state",
                        "raw_state": _first_present(raw, "state", "status", default=None),
                    },
                    recorded_at=observed,
                )
            )
            continue
        if state in {"MATCHED", "MINED", "CONFIRMED"} and command is not None and order_id:
            trade_order_ids.add(order_id)
            try:
                filled = _decimal(_trade_filled_size(raw, order_id))
            except (InvalidOperation, ValueError):
                filled = Decimal("0")
            if filled.is_finite() and filled > Decimal("0"):
                trade_fills_by_order_id[order_id] = trade_fills_by_order_id.get(order_id, Decimal("0")) + filled
        if command is None:
            findings.append(
                record_finding(
                    conn,
                    kind="unrecorded_trade",
                    subject_id=subject_id,
                    context=context,
                    evidence={
                        "exchange_trade": raw,
                        "reason": "exchange_trade_unlinked_to_local_command",
                        "candidate_order_ids": candidate_order_ids,
                    },
                    recorded_at=observed,
                )
            )
            continue
        if not venue_trade_id:
            findings.append(
                record_finding(
                    conn,
                    kind="unrecorded_trade",
                    subject_id=subject_id,
                    context=context,
                    evidence={
                        "exchange_trade": raw,
                        "local_command": _command_evidence(command),
                        "reason": "exchange_trade_missing_venue_trade_identity",
                    },
                    recorded_at=observed,
                )
            )
            continue
        finding = _append_linkable_trade_fact_if_missing(
            conn,
            command,
            raw,
            venue_trade_id,
            observed,
            state=state,
            context=context,
            matched_order_id=order_id,
        )
        if finding is not None:
            findings.append(finding)

    for order_id, command in local_by_order.items():
        if order_id in open_order_ids:
            continue
        point_order = _point_order_lookup(adapter, order_id)
        point_order_status = _order_state(point_order)
        if point_order_status in _OPEN_POINT_ORDER_STATES:
            continue
        if context == "ws_gap" and _trade_fill_covers_local_command(
            command, trade_fills_by_order_id.get(order_id)
        ):
            continue
        if context != "ws_gap" and order_id in trade_order_ids:
            continue
        if not _local_order_is_open(conn, command):
            continue
        findings.append(
            record_finding(
                conn,
                kind=_local_absence_kind(context),
                subject_id=order_id,
                context=context,
                evidence={
                    "local_command": _command_evidence(command),
                    "latest_order_fact": _latest_order_fact(conn, order_id),
                    "exchange_open_order_ids": sorted(open_order_ids),
                    "point_order": _raw(point_order) if point_order is not None else None,
                    "point_order_status": point_order_status,
                    "point_order_surface": "get_order" if point_order is not None else None,
                    "trade_enumeration_available": trades_available,
                    "reason": "local_open_order_absent_from_exchange_open_orders",
                },
                recorded_at=observed,
            )
        )

    positions_available = callable(getattr(adapter, "get_positions", None))
    if positions_available:
        _assert_adapter_read_fresh(adapter, "positions", observed)
        positions = adapter.get_positions()
        findings.extend(
            _record_position_drift_findings(
                conn,
                positions=positions,
                context=context,
                observed_at=observed,
            )
        )
    _resolve_disappeared_ghost_order_findings(
        adapter, conn, open_order_ids, trades=trades if trades_available else None, observed_at=observed
    )
    reconcile_recorded_maker_fill_economics(conn, observed_at=observed)
    return findings


def reconcile_recorded_maker_fill_economics(
    conn: sqlite3.Connection,
    *,
    observed_at: datetime | str | None = None,
) -> dict[str, int]:
    """Repair recorded trade facts whose raw maker leg contradicts top-level trade economics.

    The venue user stream emits a trade-level top-line from the taker side while
    Zeus can be the maker.  The immutable raw payload already contains the
    command-owned maker order.  This repair appends a corrected fact instead of
    rewriting the old row, then replays the entry-fill projection from the
    latest fact chain.
    """

    summary = {
        "scanned": 0,
        "corrected": 0,
        "projected": 0,
        "stayed": 0,
        "errors": 0,
    }
    if not _table_exists(conn, "venue_trade_facts") or not _table_exists(conn, "venue_commands"):
        return summary
    observed = _coerce_dt(observed_at)
    rows = conn.execute(
        """
        WITH latest_trade_fact AS (
            SELECT trade_id, MAX(local_sequence) AS local_sequence
              FROM venue_trade_facts
             GROUP BY trade_id
        )
        SELECT
            tf.*,
            cmd.snapshot_id AS cmd_snapshot_id,
            cmd.envelope_id AS cmd_envelope_id,
            cmd.position_id AS cmd_position_id,
            cmd.decision_id AS cmd_decision_id,
            cmd.idempotency_key AS cmd_idempotency_key,
            cmd.intent_kind AS cmd_intent_kind,
            cmd.market_id AS cmd_market_id,
            cmd.token_id AS cmd_token_id,
            cmd.side AS cmd_side,
            cmd.size AS cmd_size,
            cmd.price AS cmd_price,
            cmd.venue_order_id AS cmd_venue_order_id,
            cmd.state AS cmd_state,
            cmd.created_at AS cmd_created_at,
            cmd.updated_at AS cmd_updated_at
          FROM venue_trade_facts tf
          JOIN latest_trade_fact latest
            ON latest.trade_id = tf.trade_id
           AND latest.local_sequence = tf.local_sequence
          JOIN venue_commands cmd
            ON cmd.command_id = tf.command_id
         WHERE tf.state IN ('MATCHED', 'MINED', 'CONFIRMED')
           AND COALESCE(tf.raw_payload_json, '') LIKE '%maker_orders%'
         ORDER BY tf.observed_at, tf.trade_fact_id
        """
    ).fetchall()
    for row in rows:
        summary["scanned"] += 1
        fact = dict(row)
        try:
            command = _command_from_prefixed_trade_fact_row(fact)
            raw = _json_mapping(fact.get("raw_payload_json"))
            order_id = str(command.get("venue_order_id") or fact.get("venue_order_id") or "")
            if _selected_maker_order(raw, order_id) is None:
                summary["stayed"] += 1
                continue
            corrected_size_raw = _trade_filled_size(raw, order_id)
            corrected_price_raw = _trade_fill_price(raw, order_id)
            missing = _missing_trade_fill_economics(
                state=str(fact.get("state") or ""),
                filled_size=corrected_size_raw,
                fill_price=corrected_price_raw,
            )
            if missing:
                summary["errors"] += 1
                continue
            corrected_size = str(corrected_size_raw)
            corrected_price = str(corrected_price_raw)
            if not _same_trade_fill_economics(
                fact,
                filled_size=corrected_size,
                fill_price=corrected_price,
            ):
                _append_maker_fill_economic_correction(
                    conn,
                    fact=fact,
                    command=command,
                    raw=raw,
                    venue_order_id=order_id,
                    filled_size=corrected_size,
                    fill_price=corrected_price,
                    observed_at=observed,
                )
                summary["corrected"] += 1
            _ensure_entry_fill_position_event(
                conn,
                command=command,
                venue_order_id=order_id,
                filled_size=corrected_size,
                fill_price=corrected_price,
                observed_at=observed,
                order_fact_source=str(fact.get("source") or "REST"),
            )
            summary["projected"] += 1
        except Exception:
            summary["errors"] += 1
            logger.exception(
                "exchange_reconcile: maker fill economics repair failed for trade_fact_id=%s",
                fact.get("trade_fact_id"),
            )
    exit_summary = _reconcile_recorded_exit_fill_projections(conn, observed_at=observed)
    if exit_summary["projected"]:
        summary["exit_projected"] = exit_summary["projected"]
    summary["stayed"] += exit_summary["stayed"]
    summary["errors"] += exit_summary["errors"]
    return summary


def _reconcile_recorded_exit_fill_projections(
    conn: sqlite3.Connection,
    *,
    observed_at: datetime,
) -> dict[str, int]:
    """Project already-recorded confirmed exit fills into lifecycle state.

    command_recovery calls reconcile_recorded_maker_fill_economics every cycle
    as the local recorded-trade repair hook.  This keeps confirmed exit
    self-healing local: the daemon needs only the command, trade fact, and
    position projection already in SQLite, not a fresh full venue resweep.
    """

    summary = {"scanned": 0, "projected": 0, "stayed": 0, "errors": 0}
    rows = conn.execute(
        """
        WITH latest_trade_fact AS (
            SELECT trade_id, MAX(local_sequence) AS local_sequence
              FROM venue_trade_facts
             GROUP BY trade_id
        )
        SELECT
            tf.*,
            cmd.snapshot_id AS cmd_snapshot_id,
            cmd.envelope_id AS cmd_envelope_id,
            cmd.position_id AS cmd_position_id,
            cmd.decision_id AS cmd_decision_id,
            cmd.idempotency_key AS cmd_idempotency_key,
            cmd.intent_kind AS cmd_intent_kind,
            cmd.market_id AS cmd_market_id,
            cmd.token_id AS cmd_token_id,
            cmd.side AS cmd_side,
            cmd.size AS cmd_size,
            cmd.price AS cmd_price,
            cmd.venue_order_id AS cmd_venue_order_id,
            cmd.state AS cmd_state,
            cmd.created_at AS cmd_created_at,
            cmd.updated_at AS cmd_updated_at,
            pc.phase AS position_phase
          FROM venue_trade_facts tf
          JOIN latest_trade_fact latest
            ON latest.trade_id = tf.trade_id
           AND latest.local_sequence = tf.local_sequence
          JOIN venue_commands cmd
            ON cmd.command_id = tf.command_id
          JOIN position_current pc
            ON pc.position_id = cmd.position_id
         WHERE tf.state = 'CONFIRMED'
           AND UPPER(COALESCE(cmd.intent_kind, '')) = 'EXIT'
           AND UPPER(COALESCE(cmd.side, '')) = 'SELL'
           AND pc.phase IN ('active', 'day0_window', 'pending_exit', 'economically_closed')
         ORDER BY tf.observed_at, tf.trade_fact_id
        """
    ).fetchall()
    for row in rows:
        summary["scanned"] += 1
        fact = dict(row)
        try:
            command = _command_from_prefixed_trade_fact_row(fact)
            command_size = _positive_decimal_or_none(command.get("size"))
            if command_size is None:
                summary["stayed"] += 1
                continue
            fill_economics = _exit_fill_economics_for_command(
                conn,
                command_id=str(command.get("command_id") or ""),
                fallback_filled_size=str(fact.get("filled_size") or "0"),
                fallback_fill_price=str(fact.get("fill_price") or "0"),
            )
            if fill_economics is None:
                summary["stayed"] += 1
                continue
            confirmed_shares, _ = fill_economics
            if confirmed_shares < command_size:
                summary["stayed"] += 1
                continue
            before = conn.total_changes
            _ensure_exit_fill_position_event(
                conn,
                command=command,
                venue_order_id=str(command.get("venue_order_id") or fact.get("venue_order_id") or ""),
                filled_size=str(fact.get("filled_size") or "0"),
                fill_price=str(fact.get("fill_price") or "0"),
                observed_at=_coerce_dt(fact.get("observed_at") or observed_at),
                command_event="FILL_CONFIRMED",
            )
            if conn.total_changes > before:
                summary["projected"] += 1
            else:
                summary["stayed"] += 1
        except Exception:
            summary["errors"] += 1
            logger.exception(
                "exchange_reconcile: recorded exit fill projection repair failed for trade_fact_id=%s",
                fact.get("trade_fact_id"),
            )
    return summary


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table,),
    ).fetchone()
    return row is not None


def _json_mapping(raw: object) -> dict[str, Any]:
    if isinstance(raw, Mapping):
        return dict(raw)
    if raw in (None, ""):
        return {}
    try:
        parsed = json.loads(str(raw))
    except (TypeError, json.JSONDecodeError):
        return {}
    return dict(parsed) if isinstance(parsed, Mapping) else {}


def _command_from_prefixed_trade_fact_row(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "command_id": row.get("command_id"),
        "snapshot_id": row.get("cmd_snapshot_id"),
        "envelope_id": row.get("cmd_envelope_id"),
        "position_id": row.get("cmd_position_id"),
        "decision_id": row.get("cmd_decision_id"),
        "idempotency_key": row.get("cmd_idempotency_key"),
        "intent_kind": row.get("cmd_intent_kind"),
        "market_id": row.get("cmd_market_id"),
        "token_id": row.get("cmd_token_id"),
        "side": row.get("cmd_side"),
        "size": row.get("cmd_size"),
        "price": row.get("cmd_price"),
        "venue_order_id": row.get("cmd_venue_order_id"),
        "state": row.get("cmd_state"),
        "created_at": row.get("cmd_created_at"),
        "updated_at": row.get("cmd_updated_at"),
    }


def _append_maker_fill_economic_correction(
    conn: sqlite3.Connection,
    *,
    fact: Mapping[str, Any],
    command: Mapping[str, Any],
    raw: Mapping[str, Any],
    venue_order_id: str,
    filled_size: str,
    fill_price: str,
    observed_at: datetime,
) -> int:
    from src.state.venue_command_repo import append_trade_fact

    payload = dict(raw)
    payload["zeus_repair"] = {
        "schema_version": 1,
        "reason": "maker_leg_economics_selected_for_command_order",
        "source_trade_fact_id": fact.get("trade_fact_id"),
        "source_filled_size": fact.get("filled_size"),
        "source_fill_price": fact.get("fill_price"),
        "corrected_filled_size": filled_size,
        "corrected_fill_price": fill_price,
        "command_id": command.get("command_id"),
        "venue_order_id": venue_order_id,
        "source_module": "src.execution.exchange_reconcile",
    }
    return append_trade_fact(
        conn,
        trade_id=str(fact["trade_id"]),
        venue_order_id=venue_order_id,
        command_id=str(command["command_id"]),
        state=str(fact["state"]),
        filled_size=filled_size,
        fill_price=fill_price,
        source=str(fact.get("source") or "WS_USER"),
        observed_at=observed_at,
        venue_timestamp=fact.get("venue_timestamp"),
        raw_payload_hash=_hash_payload(payload),
        raw_payload_json=payload,
        fee_paid_micro=fact.get("fee_paid_micro"),
        tx_hash=fact.get("tx_hash"),
        block_number=fact.get("block_number"),
        confirmation_count=fact.get("confirmation_count"),
    )


def _prior_terminal_zero_remainder_order_fact(
    conn: sqlite3.Connection,
    *,
    command_id: str,
    venue_order_id: str,
) -> sqlite3.Row | None:
    row = conn.execute(
        """
        SELECT state, remaining_size, matched_size
          FROM venue_order_facts
         WHERE command_id = ?
           AND venue_order_id = ?
           AND state IN ('MATCHED', 'CANCEL_CONFIRMED', 'EXPIRED', 'VENUE_WIPED')
         ORDER BY local_sequence DESC, fact_id DESC
         LIMIT 1
        """,
        (command_id, venue_order_id),
    ).fetchone()
    if row is None or not _same_decimal_value(row["remaining_size"], "0"):
        return None
    return row


def _ensure_entry_fill_order_fact(
    conn: sqlite3.Connection,
    *,
    command: Mapping[str, Any],
    venue_order_id: str,
    filled_size: str,
    observed_at: datetime,
    source: str,
) -> None:
    if not _table_exists(conn, "venue_order_facts"):
        return
    filled_dec = _positive_decimal_or_none(filled_size)
    if filled_dec is None:
        return
    command_size = _positive_decimal_or_none(command.get("size"))
    command_id = str(command.get("command_id") or "")
    latest = conn.execute(
        """
        SELECT state, remaining_size, matched_size
          FROM venue_order_facts
         WHERE command_id = ?
         ORDER BY local_sequence DESC, fact_id DESC
         LIMIT 1
        """,
        (command_id,),
    ).fetchone()
    prior_terminal = _prior_terminal_zero_remainder_order_fact(
        conn,
        command_id=command_id,
        venue_order_id=venue_order_id,
    )
    from src.execution.order_truth_reducer import VenueOrderTruthReducer

    reducer_facts = [row for row in (latest, prior_terminal) if row is not None]
    reduced = VenueOrderTruthReducer.reduce(
        order_facts=reducer_facts,
        trade_filled_size=filled_dec,
        command_size=command_size,
        command_state=str(command.get("state") or ""),
    )
    state = reduced.state
    remaining_text = (
        _decimal_text(reduced.remaining_size)
        if reduced.remaining_size is not None
        else None
    )
    matched_text = _decimal_text(reduced.matched_size)
    latest_remaining_matches = (
        latest is not None
        and (
            (latest["remaining_size"] is None and remaining_text is None)
            or _same_decimal_value(latest["remaining_size"], remaining_text)
        )
    )
    if latest is not None and (
        str(latest["state"] or "") == state
        and latest_remaining_matches
        and _same_decimal_value(latest["matched_size"], matched_text)
    ):
        return

    from src.state.venue_command_repo import append_order_fact

    payload = {
        "schema_version": 1,
        "reason": "m5_exchange_reconcile_entry_fill_order_fact",
        "source_module": "src.execution.exchange_reconcile",
        "command_id": str(command.get("command_id") or ""),
        "venue_order_id": venue_order_id,
        "state": state,
        "remaining_size": remaining_text,
        "matched_size": matched_text,
        "order_truth_proof_class": reduced.proof_class,
        "order_truth_source_state": reduced.source_state,
    }
    append_order_fact(
        conn,
        venue_order_id=venue_order_id,
        command_id=str(command.get("command_id") or ""),
        state=state,
        remaining_size=remaining_text,
        matched_size=matched_text,
        source=source,
        observed_at=observed_at,
        venue_timestamp=observed_at,
        raw_payload_hash=_hash_payload(payload),
        raw_payload_json=payload,
    )


def record_finding(
    conn: sqlite3.Connection,
    *,
    kind: FindingKind,
    subject_id: str,
    context: ReconcileContext,
    evidence: Mapping[str, Any],
    recorded_at: datetime | str | None = None,
) -> ReconcileFinding:
    """Insert or return the unresolved finding for ``(kind, subject, context)``."""

    init_exchange_reconcile_schema(conn)
    kind = _validate_kind(kind)
    context = _validate_context(context)
    subject = _require_nonempty("subject_id", subject_id)
    evidence_json = _canonical_json(dict(evidence))
    recorded = _coerce_dt(recorded_at)
    row = _find_unresolved_row(conn, kind=kind, subject_id=subject, context=context)
    if row is not None:
        return _finding_from_row(row)
    try:
        finding_id = str(uuid.uuid4())
        conn.execute(
            """
            INSERT INTO exchange_reconcile_findings (
              finding_id, kind, subject_id, context, evidence_json, recorded_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (finding_id, kind, subject, context, evidence_json, recorded.isoformat()),
        )
    except sqlite3.IntegrityError:
        row = _find_unresolved_row(conn, kind=kind, subject_id=subject, context=context)
        if row is None:
            raise
        return _finding_from_row(row)
    row = _row_by_id(conn, finding_id)
    if row is None:  # pragma: no cover - defensive SQLite invariant.
        raise RuntimeError(f"finding {finding_id!r} disappeared after insert")
    return _finding_from_row(row)


def list_unresolved_findings(
    conn: sqlite3.Connection,
    *,
    kind: FindingKind | None = None,
) -> list[ReconcileFinding]:
    init_exchange_reconcile_schema(conn)
    if kind is None:
        rows = conn.execute(
            """
            SELECT * FROM exchange_reconcile_findings
             WHERE resolved_at IS NULL
             ORDER BY recorded_at, finding_id
            """
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT * FROM exchange_reconcile_findings
             WHERE resolved_at IS NULL
               AND kind = ?
             ORDER BY recorded_at, finding_id
            """,
            (_validate_kind(kind),),
        ).fetchall()
    return [_finding_from_row(row) for row in rows]


def resolve_finding(
    conn: sqlite3.Connection,
    finding_id: str,
    *,
    resolution: str,
    resolved_by: str,
    resolved_at: datetime | str | None = None,
) -> None:
    init_exchange_reconcile_schema(conn)
    finding = _require_nonempty("finding_id", finding_id)
    resolution = _require_nonempty("resolution", resolution)
    resolved_by = _require_nonempty("resolved_by", resolved_by)
    row = _row_by_id(conn, finding)
    if row is None:
        raise ValueError(f"unknown reconcile finding: {finding!r}")
    if row["resolved_at"] is not None:
        if row["resolution"] == resolution and row["resolved_by"] == resolved_by:
            return
        raise ValueError(f"reconcile finding already resolved: {finding!r}")
    conn.execute(
        """
        UPDATE exchange_reconcile_findings
           SET resolved_at = ?, resolution = ?, resolved_by = ?
         WHERE finding_id = ?
           AND resolved_at IS NULL
        """,
        (_coerce_dt(resolved_at).isoformat(), resolution, resolved_by, finding),
    )


def _record_position_drift_findings(
    conn: sqlite3.Connection,
    *,
    positions: list[Any],
    context: ReconcileContext,
    observed_at: datetime,
) -> list[ReconcileFinding]:
    exchange = _exchange_positions_by_token(positions)
    confirmed_journal = _journal_positions_by_token(
        conn,
        states=_CONFIRMED_POSITION_FACT_STATES,
    )
    optimistic_journal = _journal_positions_by_token(
        conn,
        states=_OPTIMISTIC_POSITION_FACT_STATES,
    )
    settlement_holdings = _settlement_command_token_holdings_by_token(conn)
    tokens = sorted(set(exchange) | set(confirmed_journal) | set(settlement_holdings))
    findings: list[ReconcileFinding] = []
    for token in tokens:
        exchange_size = exchange.get(token, Decimal("0"))
        confirmed_size = confirmed_journal.get(token, Decimal("0"))
        optimistic_size = optimistic_journal.get(token, Decimal("0"))
        settlement_size = settlement_holdings.get(token, Decimal("0"))
        expected_wallet_size = confirmed_size + settlement_size
        if _position_size_matches(exchange_size, confirmed_size):
            _resolve_open_position_drift_findings(
                conn,
                token,
                resolution="position_drift_cleared",
                resolved_at=observed_at,
            )
            continue
        if settlement_size > Decimal("0") and _position_size_matches(
            exchange_size,
            expected_wallet_size,
        ):
            _resolve_open_position_drift_findings(
                conn,
                token,
                resolution="position_drift_settlement_command_token_holding",
                resolved_at=observed_at,
            )
            continue
        if _position_size_hidden_by_visibility_floor(exchange_size, confirmed_size):
            _resolve_open_position_drift_findings(
                conn,
                token,
                resolution="position_drift_below_position_api_visibility_floor",
                resolved_at=observed_at,
            )
            continue
        if settlement_size > Decimal("0") and _position_size_hidden_by_visibility_floor(
            exchange_size,
            expected_wallet_size,
        ):
            _resolve_open_position_drift_findings(
                conn,
                token,
                resolution="position_drift_settlement_command_visibility_floor",
                resolved_at=observed_at,
            )
            continue
        if _pending_exit_optimistic_sell_offsets_confirmed_position(
            conn,
            token_id=token,
            exchange_size=exchange_size,
            confirmed_size=confirmed_size,
            optimistic_size=optimistic_size,
        ):
            _resolve_open_position_drift_findings(
                conn,
                token,
                resolution="position_drift_pending_exit_offset",
                resolved_at=observed_at,
            )
            continue
        if _has_recent_filled_suppression(conn, token, observed_at):
            _resolve_open_position_drift_findings(
                conn,
                token,
                resolution="position_drift_recent_fill_suppressed",
                resolved_at=observed_at,
            )
            continue
        findings.append(
            record_finding(
                conn,
                kind="position_drift",
                subject_id=token,
                context=context,
                evidence={
                    "token_id": token,
                    "exchange_size": str(exchange_size),
                    "journal_size": str(confirmed_size),
                    "confirmed_journal_size": str(confirmed_size),
                    "optimistic_journal_size": str(optimistic_size),
                    "settlement_command_token_size": str(settlement_size),
                    "expected_wallet_size": str(expected_wallet_size),
                    "journal_evidence_class": "confirmed_trade_facts",
                    "settlement_evidence_class": "unconfirmed_redeem_settlement_commands",
                    "optimistic_evidence_class": "matched_or_mined_trade_facts",
                    "reason": (
                        "exchange_position_differs_from_expected_wallet_facts"
                        if settlement_size > Decimal("0")
                        else "exchange_position_differs_from_confirmed_trade_facts"
                    ),
                },
                recorded_at=observed_at,
            )
        )
    return findings


def _unresolved_position_drift_tokens(conn: sqlite3.Connection) -> tuple[str, ...]:
    rows = conn.execute(
        """
        SELECT DISTINCT subject_id
          FROM exchange_reconcile_findings
         WHERE kind = 'position_drift'
           AND resolved_at IS NULL
           AND TRIM(COALESCE(subject_id, '')) != ''
         ORDER BY subject_id
        """
    ).fetchall()
    return tuple(str(row["subject_id"]) for row in rows)


def _unresolved_unrecorded_trade_ids(conn: sqlite3.Connection) -> tuple[str, ...]:
    rows = conn.execute(
        """
        SELECT DISTINCT subject_id
          FROM exchange_reconcile_findings
         WHERE kind = 'unrecorded_trade'
           AND resolved_at IS NULL
           AND TRIM(COALESCE(subject_id, '')) != ''
         ORDER BY subject_id
        """
    ).fetchall()
    return tuple(str(row["subject_id"]) for row in rows)


def _unresolved_position_drift_count(
    conn: sqlite3.Connection,
    token_ids: tuple[str, ...] | frozenset[str] | set[str],
) -> int:
    if not token_ids:
        return 0
    selected = tuple(sorted(str(token) for token in token_ids))
    placeholders = ", ".join("?" for _ in selected)
    row = conn.execute(
        f"""
        SELECT COUNT(*) AS count
          FROM exchange_reconcile_findings
         WHERE kind = 'position_drift'
           AND resolved_at IS NULL
           AND subject_id IN ({placeholders})
        """,
        selected,
    ).fetchone()
    return int(row["count"] or 0)


def _unresolved_trade_count(
    conn: sqlite3.Connection,
    trade_ids: tuple[str, ...] | frozenset[str] | set[str],
) -> int:
    if not trade_ids:
        return 0
    selected = tuple(sorted(str(trade_id) for trade_id in trade_ids))
    placeholders = ", ".join("?" for _ in selected)
    row = conn.execute(
        f"""
        SELECT COUNT(*) AS count
          FROM exchange_reconcile_findings
         WHERE kind = 'unrecorded_trade'
           AND resolved_at IS NULL
           AND subject_id IN ({placeholders})
        """,
        selected,
    ).fetchone()
    return int(row["count"] or 0)


def _local_order_ids_for_tokens(
    conn: sqlite3.Connection,
    token_ids: tuple[str, ...] | frozenset[str] | set[str],
) -> frozenset[str]:
    if not token_ids:
        return frozenset()
    selected = tuple(sorted(str(token) for token in token_ids))
    placeholders = ", ".join("?" for _ in selected)
    rows = conn.execute(
        f"""
        SELECT DISTINCT venue_order_id
          FROM venue_commands
         WHERE token_id IN ({placeholders})
           AND venue_order_id IS NOT NULL
           AND TRIM(venue_order_id) != ''
        """,
        selected,
    ).fetchall()
    return frozenset(str(row["venue_order_id"]) for row in rows)


def _order_ids_for_unrecorded_trade_findings(conn: sqlite3.Connection) -> frozenset[str]:
    rows = conn.execute(
        """
        SELECT evidence_json
          FROM exchange_reconcile_findings
         WHERE kind = 'unrecorded_trade'
           AND resolved_at IS NULL
        """
    ).fetchall()
    order_ids: set[str] = set()
    for row in rows:
        evidence = _json_mapping(row["evidence_json"])
        local_command = evidence.get("local_command")
        if isinstance(local_command, Mapping):
            venue_order_id = _string_or_none(local_command.get("venue_order_id"))
            if venue_order_id:
                order_ids.add(venue_order_id)
        for candidate in evidence.get("candidate_order_ids") or []:
            value = _string_or_none(candidate)
            if value:
                order_ids.add(value)
        exchange_trade = evidence.get("exchange_trade")
        if isinstance(exchange_trade, Mapping):
            order_ids.update(_trade_order_ids(exchange_trade))
    return frozenset(order_ids)


def _resolve_position_drift_tokens_from_current_truth(
    conn: sqlite3.Connection,
    *,
    token_ids: tuple[str, ...] | frozenset[str] | set[str],
    positions: list[Any],
    observed_at: datetime,
) -> None:
    exchange = _exchange_positions_by_token(positions)
    confirmed_journal = _journal_positions_by_token(
        conn,
        states=_CONFIRMED_POSITION_FACT_STATES,
    )
    optimistic_journal = _journal_positions_by_token(
        conn,
        states=_OPTIMISTIC_POSITION_FACT_STATES,
    )
    settlement_holdings = _settlement_command_token_holdings_by_token(conn)
    for token in sorted(str(item) for item in token_ids):
        exchange_size = exchange.get(token, Decimal("0"))
        confirmed_size = confirmed_journal.get(token, Decimal("0"))
        optimistic_size = optimistic_journal.get(token, Decimal("0"))
        settlement_size = settlement_holdings.get(token, Decimal("0"))
        expected_wallet_size = confirmed_size + settlement_size
        if _position_size_matches(exchange_size, confirmed_size):
            _resolve_open_position_drift_findings(
                conn,
                token,
                resolution="position_drift_cleared",
                resolved_at=observed_at,
            )
            continue
        if settlement_size > Decimal("0") and _position_size_matches(
            exchange_size,
            expected_wallet_size,
        ):
            _resolve_open_position_drift_findings(
                conn,
                token,
                resolution="position_drift_settlement_command_token_holding",
                resolved_at=observed_at,
            )
            continue
        if _position_size_hidden_by_visibility_floor(exchange_size, confirmed_size):
            _resolve_open_position_drift_findings(
                conn,
                token,
                resolution="position_drift_below_position_api_visibility_floor",
                resolved_at=observed_at,
            )
            continue
        if settlement_size > Decimal("0") and _position_size_hidden_by_visibility_floor(
            exchange_size,
            expected_wallet_size,
        ):
            _resolve_open_position_drift_findings(
                conn,
                token,
                resolution="position_drift_settlement_command_visibility_floor",
                resolved_at=observed_at,
            )
            continue
        if _pending_exit_optimistic_sell_offsets_confirmed_position(
            conn,
            token_id=token,
            exchange_size=exchange_size,
            confirmed_size=confirmed_size,
            optimistic_size=optimistic_size,
        ):
            _resolve_open_position_drift_findings(
                conn,
                token,
                resolution="position_drift_pending_exit_offset",
                resolved_at=observed_at,
            )
            continue
        if _has_recent_filled_suppression(conn, token, observed_at):
            _resolve_open_position_drift_findings(
                conn,
                token,
                resolution="position_drift_recent_fill_suppressed",
                resolved_at=observed_at,
            )


def _position_size_matches(left: Decimal, right: Decimal) -> bool:
    return abs(left - right) <= _POSITION_DRIFT_ABS_TOLERANCE


def _position_size_hidden_by_visibility_floor(left: Decimal, right: Decimal) -> bool:
    if min(abs(left), abs(right)) != Decimal("0"):
        return False
    return abs(left - right) <= _POSITION_API_VISIBILITY_FLOOR


def _resolve_open_position_drift_findings(
    conn: sqlite3.Connection,
    token_id: str,
    *,
    resolution: str,
    resolved_at: datetime,
) -> None:
    rows = conn.execute(
        """
        SELECT finding_id
          FROM exchange_reconcile_findings
         WHERE kind = 'position_drift'
           AND subject_id = ?
           AND resolved_at IS NULL
        """,
        (token_id,),
    ).fetchall()
    for row in rows:
        resolve_finding(
            conn,
            str(row["finding_id"]),
            resolution=resolution,
            resolved_by="src.execution.exchange_reconcile",
            resolved_at=resolved_at,
        )


_GHOST_PROOF_TERMINAL_STATES = frozenset(
    {"CANCELLED", "CANCELED", "EXPIRED", "REJECTED", "SUBMIT_REJECTED", "FILLED"}
)


def _ghost_proof_a_point_order_terminal(
    adapter: Any, order_id: str
) -> tuple[bool, str]:
    """(a) point-order terminal status via get_order().

    Returns (proven, resolution_string). Proven iff the adapter has get_order
    and the returned status is a terminal state. FILLED counts as proof that
    the order is gone (no cancel-semantic confusion).
    """
    get_order_fn = getattr(adapter, "get_order", None)
    if not callable(get_order_fn):
        return False, ""
    try:
        point_order = get_order_fn(order_id)
    except Exception:
        return False, ""
    state = _order_state(point_order)
    if state is None:
        return False, ""
    if state in _GHOST_PROOF_TERMINAL_STATES:
        return True, f"exchange_ghost_order_terminal_point_order_{state.lower()}"
    return False, ""


def _ghost_proof_c_linked_trade_fact(
    conn: sqlite3.Connection, order_id: str
) -> tuple[bool, str]:
    """(c) venue_trade_facts row already present for this order_id.

    A fact row means the order did transact; finding can be resolved.
    """
    if not _table_exists(conn, "venue_trade_facts"):
        return False, ""
    row = conn.execute(
        "SELECT 1 FROM venue_trade_facts WHERE venue_order_id = ? LIMIT 1",
        (order_id,),
    ).fetchone()
    if row is not None:
        return True, "exchange_ghost_order_linked_trade_fact_present"
    return False, ""


def _ghost_proof_d_no_token_exposure(
    conn: sqlite3.Connection, order_id: str
) -> tuple[bool, str]:
    """(d) position_current shows no resulting token exposure.

    A ghost order that left no active position means the fill didn't create
    risk we need to track — cancellation-equivalent for reconcile purposes.
    Specifically: if no position_current row references this order_id with a
    non-zero shares value, the order produced no tracked exposure.
    """
    if not _table_exists(conn, "position_current"):
        return False, ""
    row = conn.execute(
        """
        SELECT 1
          FROM position_current
         WHERE order_id = ?
           AND COALESCE(shares, 0) > 0
         LIMIT 1
        """,
        (order_id,),
    ).fetchone()
    if row is None:
        return True, "exchange_ghost_order_no_token_exposure_after_disappearance"
    return False, ""


def _ghost_proof_b_no_matching_trade(
    adapter: Any, order_id: str, existing_trades: list[Any] | None
) -> tuple[bool, str]:
    """(b) fresh get_trades enumeration found no trade matching this order_id.

    If trades surface is available and no matching trade exists, the order
    was canceled/expired rather than filled — cancellation-equivalent.
    Uses trades already fetched during the sweep when available.
    """
    if existing_trades is not None:
        trade_list = existing_trades
    else:
        get_trades_fn = getattr(adapter, "get_trades", None)
        if not callable(get_trades_fn):
            return False, ""
        try:
            trade_list = list(get_trades_fn() or [])
        except Exception:
            return False, ""

    for trade in trade_list:
        raw = _raw(trade)
        for matched_id in _trade_order_ids(raw):
            if matched_id == order_id:
                return False, ""
    return True, "exchange_ghost_order_no_matching_trade_in_enumeration"


def _resolve_disappeared_ghost_order_findings(
    adapter: Any,
    conn: sqlite3.Connection,
    open_order_ids: set[str],
    *,
    trades: list[Any] | None = None,
    observed_at: datetime,
) -> int:
    """Resolve `exchange_ghost_order` findings whose subject is no longer in
    the live ``open_order_ids`` snapshot — but ONLY when backed by at least
    one proof that the disappearance is terminal (cancel/fill/expire) rather
    than a read-miss (pagination, venue lag, trade surface migration).

    Proof hierarchy (first match wins, cheapest first):
      (a) get_order(subject_id) returns a terminal status
          (CANCELLED/EXPIRED/REJECTED/FILLED/SUBMIT_REJECTED)
      (c) venue_trade_facts has a row with venue_order_id = subject_id
      (d) position_current has no row with order_id = subject_id and shares > 0
      (b) get_trades enumeration found no trade matching subject_id

    If NONE of (a)–(d) hold, the finding stays unresolved (kill-switch / reduce-
    only stays armed fail-closed). This prevents a venue read-miss from silently
    "resolving" real exposure.

    Operator resolution (e) is handled externally via resolve_finding(...,
    resolved_by='operator') and does not enter this auto-resolver.
    """
    rows = conn.execute(
        """
        SELECT finding_id, subject_id
          FROM exchange_reconcile_findings
         WHERE kind = 'exchange_ghost_order'
           AND resolved_at IS NULL
        """
    ).fetchall()
    resolved = 0
    for row in rows:
        subject = str(row["subject_id"])
        if subject in open_order_ids:
            continue

        # Attempt each proof in order; bail on first hit.
        proven, resolution = _ghost_proof_a_point_order_terminal(adapter, subject)
        if not proven:
            proven, resolution = _ghost_proof_c_linked_trade_fact(conn, subject)
        if not proven:
            proven, resolution = _ghost_proof_d_no_token_exposure(conn, subject)
        if not proven:
            proven, resolution = _ghost_proof_b_no_matching_trade(adapter, subject, trades)

        if not proven:
            logger.warning(
                "ghost_order_unproven_disappearance: subject=%s finding=%s — "
                "kill_switch stays armed; check venue read freshness or use "
                "operator resolution.",
                subject,
                row["finding_id"],
            )
            continue

        resolve_finding(
            conn,
            str(row["finding_id"]),
            resolution=resolution,
            resolved_by="src.execution.exchange_reconcile",
            resolved_at=observed_at,
        )
        resolved += 1
    return resolved


def _resolve_open_trade_findings(
    conn: sqlite3.Connection,
    trade_id: str,
    *,
    resolution: str,
    resolved_at: datetime,
) -> None:
    rows = conn.execute(
        """
        SELECT finding_id
          FROM exchange_reconcile_findings
         WHERE kind = 'unrecorded_trade'
           AND subject_id = ?
           AND resolved_at IS NULL
        """,
        (trade_id,),
    ).fetchall()
    for row in rows:
        resolve_finding(
            conn,
            str(row["finding_id"]),
            resolution=resolution,
            resolved_by="src.execution.exchange_reconcile",
            resolved_at=resolved_at,
        )


def _pending_exit_optimistic_sell_offsets_confirmed_position(
    conn: sqlite3.Connection,
    *,
    token_id: str,
    exchange_size: Decimal,
    confirmed_size: Decimal,
    optimistic_size: Decimal,
) -> bool:
    if optimistic_size >= Decimal("0"):
        return False
    if not _position_size_matches(exchange_size, confirmed_size + optimistic_size):
        return False
    row = conn.execute(
        """
        SELECT 1
          FROM position_current pc
          JOIN venue_commands cmd
            ON cmd.position_id = pc.position_id
          JOIN venue_trade_facts tf
            ON tf.command_id = cmd.command_id
         WHERE pc.token_id = ?
           AND pc.phase = 'pending_exit'
           AND cmd.intent_kind = 'EXIT'
           AND cmd.side = 'SELL'
           AND tf.state IN ('MATCHED', 'MINED')
           AND CAST(COALESCE(tf.filled_size, '0') AS REAL) > 0
           AND tf.local_sequence = (
                SELECT MAX(newer.local_sequence)
                  FROM venue_trade_facts newer
                 WHERE newer.trade_id = tf.trade_id
           )
         LIMIT 1
        """,
        (token_id,),
    ).fetchone()
    return row is not None


def _append_linkable_trade_fact_if_missing(
    conn: sqlite3.Connection,
    command: Mapping[str, Any],
    raw: Mapping[str, Any],
    trade_id: str,
    observed_at: datetime,
    *,
    state: str,
    context: ReconcileContext,
    matched_order_id: str | None = None,
) -> ReconcileFinding | None:
    from src.state.venue_command_repo import append_event, append_trade_fact, get_command

    order_id = matched_order_id or _trade_order_id(raw) or str(command["venue_order_id"])
    filled_size_raw = _trade_filled_size(raw, order_id)
    fill_price_raw = _trade_fill_price(raw, order_id)
    missing = _missing_trade_fill_economics(
        state=state,
        filled_size=filled_size_raw,
        fill_price=fill_price_raw,
    )
    if missing:
        return record_finding(
            conn,
            kind="unrecorded_trade",
            subject_id=trade_id,
            context=context,
            evidence={
                "exchange_trade": dict(raw),
                "local_command": _command_evidence(command),
                "reason": "exchange_trade_missing_fill_economics",
                "missing": list(missing),
            },
            recorded_at=observed_at,
        )
    filled_size = str(filled_size_raw if filled_size_raw is not None else "0")
    fill_price = str(fill_price_raw if fill_price_raw is not None else "0")
    latest_fact = _latest_trade_fact_for_trade_id(conn, trade_id)
    if latest_fact is not None:
        identity_mismatch = _trade_fact_identity_mismatch(
            latest_fact,
            command=command,
            venue_order_id=order_id,
        )
        if identity_mismatch:
            return record_finding(
                conn,
                kind="unrecorded_trade",
                subject_id=trade_id,
                context=context,
                evidence={
                    "exchange_trade": dict(raw),
                    "local_command": _command_evidence(command),
                    "existing_trade_fact": {
                        "trade_fact_id": latest_fact.get("trade_fact_id"),
                        "command_id": latest_fact.get("command_id"),
                        "venue_order_id": latest_fact.get("venue_order_id"),
                        "state": latest_fact.get("state"),
                    },
                    "reason": "exchange_trade_identity_conflict",
                    "mismatch": identity_mismatch,
                },
                recorded_at=observed_at,
            )
        same_fill_economics = _same_trade_fill_economics(
            latest_fact,
            filled_size=filled_size,
            fill_price=fill_price,
        )
        if same_fill_economics and str(latest_fact.get("state") or "") == state:
            _resolve_open_trade_findings(
                conn,
                trade_id,
                resolution="unrecorded_trade_linked",
                resolved_at=observed_at,
            )
            if state == "CONFIRMED":
                _resolve_open_trade_findings(
                    conn,
                    _finality_subject(trade_id),
                    resolution="trade_finality_confirmed",
                    resolved_at=observed_at,
                )
            existing_event = _fill_event_for_command(command, filled_size, trade_state=state)
            if existing_event is not None:
                try:
                    append_event(
                        conn,
                        command_id=str(command["command_id"]),
                        event_type=existing_event,
                        occurred_at=observed_at.isoformat(),
                        payload={
                            "venue_order_id": order_id,
                            "trade_id": trade_id,
                            "filled_size": filled_size,
                            "fill_price": fill_price,
                            "source": "M5_EXCHANGE_RECONCILE",
                        },
                    )
                except ValueError:
                    existing_event = None
            elif str(command.get("state") or "") == "FILLED" and state == "CONFIRMED":
                existing_event = "FILL_CONFIRMED"
            _ensure_entry_fill_position_event(
                conn,
                command=command,
                venue_order_id=order_id,
                filled_size=filled_size,
                fill_price=fill_price,
                observed_at=observed_at,
                command_event=existing_event,
                order_fact_source=str(latest_fact.get("source") or "REST"),
            )
            _ensure_exit_fill_position_event(
                conn,
                command=command,
                venue_order_id=order_id,
                filled_size=filled_size,
                fill_price=fill_price,
                observed_at=observed_at,
                command_event=existing_event,
            )
            return _record_nonfinal_full_exit_fill_finality_finding(
                conn,
                trade_id=trade_id,
                command=command,
                raw=raw,
                state=state,
                filled_size=filled_size,
                observed_at=observed_at,
                context=context,
            )
        if state in {"MATCHED", "MINED", "CONFIRMED"} and not same_fill_economics:
            if not _confirmed_price_revision_has_authority(
                latest_fact,
                raw=raw,
                venue_order_id=order_id,
                state=state,
                filled_size=filled_size,
            ):
                return record_finding(
                    conn,
                    kind="unrecorded_trade",
                    subject_id=trade_id,
                    context=context,
                    evidence={
                        "exchange_trade": dict(raw),
                        "local_command": _command_evidence(command),
                        "existing_trade_fact": {
                            "trade_fact_id": latest_fact.get("trade_fact_id"),
                            "state": latest_fact.get("state"),
                            "filled_size": latest_fact.get("filled_size"),
                            "fill_price": latest_fact.get("fill_price"),
                        },
                        "reason": "exchange_trade_lifecycle_regression_or_economic_drift",
                        "incoming_state": state,
                        "incoming_filled_size": filled_size,
                        "incoming_fill_price": fill_price,
                    },
                    recorded_at=observed_at,
                )
        if not _trade_lifecycle_transition_allowed(str(latest_fact.get("state") or ""), state):
            return record_finding(
                conn,
                kind="unrecorded_trade",
                subject_id=trade_id,
                context=context,
                evidence={
                    "exchange_trade": dict(raw),
                    "local_command": _command_evidence(command),
                    "existing_trade_fact": {
                        "trade_fact_id": latest_fact.get("trade_fact_id"),
                        "state": latest_fact.get("state"),
                        "filled_size": latest_fact.get("filled_size"),
                        "fill_price": latest_fact.get("fill_price"),
                    },
                    "reason": "exchange_trade_lifecycle_regression_or_economic_drift",
                    "incoming_state": state,
                    "incoming_filled_size": filled_size,
                    "incoming_fill_price": fill_price,
                },
                recorded_at=observed_at,
            )
    append_trade_fact(
        conn,
        trade_id=trade_id,
        venue_order_id=order_id,
        command_id=str(command["command_id"]),
        state=state,
        filled_size=filled_size,
        fill_price=fill_price,
        source="REST",
        observed_at=observed_at,
        venue_timestamp=_first_present(raw, "timestamp", "created_at", "createdAt", default=None),
        raw_payload_hash=_hash_payload(raw),
        raw_payload_json=dict(raw),
        tx_hash=_first_present(raw, "transaction_hash", "tx_hash", default=None),
    )
    _resolve_open_trade_findings(
        conn,
        trade_id,
        resolution="unrecorded_trade_linked",
        resolved_at=observed_at,
    )
    if state == "CONFIRMED":
        _resolve_open_trade_findings(
            conn,
            _finality_subject(trade_id),
            resolution="trade_finality_confirmed",
            resolved_at=observed_at,
        )
    if state in {"FAILED", "RETRYING"}:
        return None
    finality_finding = _record_nonfinal_full_exit_fill_finality_finding(
        conn,
        trade_id=trade_id,
        command=command,
        raw=raw,
        state=state,
        filled_size=filled_size,
        observed_at=observed_at,
        context=context,
    )
    latest = get_command(conn, str(command["command_id"]))
    if latest is None:
        return finality_finding
    event = _fill_event_for_command(latest, filled_size, trade_state=state)
    if event is None:
        return finality_finding
    try:
        append_event(
            conn,
            command_id=str(latest["command_id"]),
            event_type=event,
            occurred_at=observed_at.isoformat(),
            payload={
                "venue_order_id": order_id,
                "trade_id": trade_id,
                "filled_size": filled_size,
                "fill_price": fill_price,
                "source": "M5_EXCHANGE_RECONCILE",
            },
        )
    except ValueError:
        # The fact is still append-only venue truth.  Illegal command-state
        # transitions stay fail-closed by not inventing grammar or forcing a
        # local command mutation.
        return finality_finding
    _ensure_entry_fill_position_event(
        conn,
        command=latest,
        venue_order_id=order_id,
        filled_size=filled_size,
        fill_price=fill_price,
        observed_at=observed_at,
        command_event=event,
        order_fact_source="REST",
    )
    _ensure_exit_fill_position_event(
        conn,
        command=latest,
        venue_order_id=order_id,
        filled_size=filled_size,
        fill_price=fill_price,
        observed_at=observed_at,
        command_event=event,
    )
    return finality_finding


def _finality_subject(trade_id: str) -> str:
    return f"finality:{trade_id}"


def _record_nonfinal_full_exit_fill_finality_finding(
    conn: sqlite3.Connection,
    *,
    trade_id: str,
    command: Mapping[str, Any],
    raw: Mapping[str, Any],
    state: str,
    filled_size: str,
    observed_at: datetime,
    context: ReconcileContext,
) -> ReconcileFinding | None:
    if state not in {"MATCHED", "MINED"}:
        return None
    if str(command.get("intent_kind") or "").upper() != "EXIT":
        return None
    if str(command.get("side") or "").upper() != "SELL":
        return None
    filled = _positive_decimal_or_none(filled_size)
    if filled is None or not _trade_fill_covers_local_command(command, filled):
        return None
    return record_finding(
        conn,
        kind="unrecorded_trade",
        subject_id=_finality_subject(trade_id),
        context=context,
        evidence={
            "exchange_trade": dict(raw),
            "local_command": _command_evidence(command),
            "reason": "exchange_trade_full_size_nonfinal_exit_fill_waiting_confirmation",
            "trade_id": trade_id,
            "incoming_state": state,
            "filled_size": filled_size,
            "required_state": "CONFIRMED",
            "action": "poll_or_refresh_until_CONFIRMED_before_economic_close",
        },
        recorded_at=observed_at,
    )


def _ensure_entry_fill_position_event(
    conn: sqlite3.Connection,
    *,
    command: Mapping[str, Any],
    venue_order_id: str,
    filled_size: str,
    fill_price: str,
    observed_at: datetime,
    command_event: str | None = None,
    order_fact_source: str = "REST",
) -> None:
    if str(command.get("intent_kind") or "").upper() != "ENTRY":
        return
    if str(command.get("side") or "").upper() != "BUY":
        return
    position_id = str(command.get("position_id") or "").strip()
    if not position_id:
        return
    row = conn.execute(
        """
        SELECT *
          FROM position_current
         WHERE position_id = ? OR order_id = ?
         ORDER BY updated_at DESC
         LIMIT 1
        """,
        (position_id, venue_order_id),
    ).fetchone()
    if row is None:
        return

    current = dict(row)
    phase = str(current.get("phase") or "")
    if phase not in _ENTRY_FILL_PROJECTION_PHASES:
        logger.info(
            "exchange_reconcile: skip entry fill projection for downstream phase position_id=%s phase=%s order_id=%s",
            position_id,
            phase,
            venue_order_id,
        )
        return
    runtime_state = "day0_window" if phase == "day0_window" else "entered"
    fill_economics = _entry_fill_economics_for_command(
        conn,
        command_id=str(command.get("command_id") or ""),
        fallback_filled_size=filled_size,
        fallback_fill_price=fill_price,
    )
    if fill_economics is None:
        return
    shares_dec, entry_price_dec, cost_basis_dec = fill_economics
    shares = _decimal_text(shares_dec)
    entry_price = _decimal_text(entry_price_dec)
    cost_basis = _decimal_text(cost_basis_dec)
    order_status = "filled" if _entry_fill_covers_command(conn, command, shares_dec) else "partial"
    if command_event == "PARTIAL_FILL_OBSERVED":
        order_status = "partial"
    _ensure_entry_fill_order_fact(
        conn,
        command=command,
        venue_order_id=venue_order_id,
        filled_size=shares,
        observed_at=observed_at,
        source=order_fact_source,
    )
    occurred_at = observed_at.isoformat()
    position = SimpleNamespace(
        **{
            **current,
            "trade_id": position_id,
            "state": runtime_state,
            "exit_state": current.get("exit_state") or "",
            "chain_state": current.get("chain_state") or "synced",
            "env": current.get("env") or "live",
            "order_id": venue_order_id,
            "entry_order_id": venue_order_id,
            "order_status": order_status,
            "entered_at": current.get("entered_at") or occurred_at,
            "order_posted_at": current.get("order_posted_at") or occurred_at,
            "shares": shares,
            "entry_price": entry_price,
            "cost_basis_usd": cost_basis,
            "size_usd": current.get("size_usd") or cost_basis,
            "strategy_key": current.get("strategy_key") or current.get("strategy") or "unknown_strategy",
            "unit": current.get("unit") or "F",
        }
    )
    existing = conn.execute(
        """
        SELECT 1
          FROM position_events
         WHERE position_id = ?
           AND event_type = 'ENTRY_ORDER_FILLED'
           AND order_id = ?
         LIMIT 1
        """,
        (position_id, venue_order_id),
    ).fetchone()
    if existing is not None:
        from src.engine.lifecycle_events import build_position_current_projection

        projection = build_position_current_projection(position)
        _apply_entry_fill_projection_and_execution_fact(
            conn,
            events=[],
            projection=projection,
            position=position,
            command=command,
            observed_at=observed_at,
            order_status=order_status,
            shares=shares_dec,
            entry_price=entry_price_dec,
            upsert_only=True,
        )
        return
    seq_row = conn.execute(
        "SELECT COALESCE(MAX(sequence_no), 0) FROM position_events WHERE position_id = ?",
        (position_id,),
    ).fetchone()
    sequence_no = int((seq_row[0] if seq_row else 0) or 0) + 1

    from src.engine.lifecycle_events import build_entry_fill_only_canonical_write

    events, projection = build_entry_fill_only_canonical_write(
        position,
        sequence_no=sequence_no,
        source_module="src.execution.exchange_reconcile",
    )
    _apply_entry_fill_projection_and_execution_fact(
        conn,
        events=events,
        projection=projection,
        position=position,
        command=command,
        observed_at=observed_at,
        order_status=order_status,
        shares=shares_dec,
        entry_price=entry_price_dec,
        upsert_only=False,
    )


def _entry_fill_economics_for_command(
    conn: sqlite3.Connection,
    *,
    command_id: str,
    fallback_filled_size: str,
    fallback_fill_price: str,
) -> tuple[Decimal, Decimal, Decimal] | None:
    """Aggregate latest authoritative trade facts for an entry command."""

    rows = conn.execute(
        """
        SELECT tf.state, tf.filled_size, tf.fill_price
          FROM venue_trade_facts tf
          JOIN (
                SELECT trade_id, MAX(local_sequence) AS local_sequence
                  FROM venue_trade_facts
                 WHERE command_id = ?
                 GROUP BY trade_id
               ) latest
            ON latest.trade_id = tf.trade_id
           AND latest.local_sequence = tf.local_sequence
         WHERE tf.command_id = ?
           AND tf.state IN ('MATCHED', 'MINED', 'CONFIRMED')
        """,
        (command_id, command_id),
    ).fetchall()
    shares = Decimal("0")
    cost_basis = Decimal("0")
    for row in rows:
        filled = _positive_decimal_or_none(row["filled_size"])
        price = _positive_decimal_or_none(row["fill_price"])
        if filled is None or price is None:
            continue
        shares += filled
        cost_basis += filled * price
    if shares > Decimal("0") and cost_basis > Decimal("0"):
        return shares, cost_basis / shares, cost_basis

    fallback_shares = _positive_decimal_or_none(fallback_filled_size)
    fallback_price = _positive_decimal_or_none(fallback_fill_price)
    if fallback_shares is None or fallback_price is None:
        return None
    return fallback_shares, fallback_price, fallback_shares * fallback_price


def _ensure_exit_fill_position_event(
    conn: sqlite3.Connection,
    *,
    command: Mapping[str, Any],
    venue_order_id: str,
    filled_size: str,
    fill_price: str,
    observed_at: datetime,
    command_event: str | None = None,
) -> None:
    if command_event != "FILL_CONFIRMED":
        return
    if str(command.get("intent_kind") or "").upper() != "EXIT":
        return
    if str(command.get("side") or "").upper() != "SELL":
        return
    position_id = str(command.get("position_id") or "").strip()
    if not position_id:
        return
    row = conn.execute(
        """
        SELECT *
          FROM position_current
         WHERE position_id = ?
         ORDER BY updated_at DESC
         LIMIT 1
        """,
        (position_id,),
    ).fetchone()
    if row is None:
        return

    current = dict(row)
    phase = str(current.get("phase") or "")
    if phase not in _EXIT_FILL_PROJECTION_PHASES:
        logger.info(
            "exchange_reconcile: skip exit fill projection for incompatible phase position_id=%s phase=%s order_id=%s",
            position_id,
            phase,
            venue_order_id,
        )
        return
    fill_economics = _exit_fill_economics_for_command(
        conn,
        command_id=str(command.get("command_id") or ""),
        fallback_filled_size=filled_size,
        fallback_fill_price=fill_price,
    )
    if fill_economics is None:
        return
    shares_dec, exit_price_dec = fill_economics
    occurred_at = observed_at.isoformat()
    position = SimpleNamespace(
        **{
            **current,
            "trade_id": position_id,
            "state": "economically_closed",
            "exit_state": "sell_filled",
            "pre_exit_state": phase,
            "chain_state": current.get("chain_state") or "synced",
            "env": current.get("env") or "live",
            "order_id": current.get("order_id") or "",
            "order_status": "sell_filled",
            "last_exit_order_id": venue_order_id,
            "last_exit_at": occurred_at,
            "exit_price": _decimal_text(exit_price_dec),
            "exit_reason": "M5_EXCHANGE_RECONCILE",
            "shares": current.get("shares") or _decimal_text(shares_dec),
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
        from src.engine.lifecycle_events import build_position_current_projection

        projection = build_position_current_projection(position)
        _apply_exit_fill_projection_and_execution_fact(
            conn,
            events=[],
            projection=projection,
            position=position,
            command=command,
            observed_at=observed_at,
            shares=shares_dec,
            exit_price=exit_price_dec,
            upsert_only=True,
        )
        return
    seq_row = conn.execute(
        "SELECT COALESCE(MAX(sequence_no), 0) FROM position_events WHERE position_id = ?",
        (position_id,),
    ).fetchone()
    sequence_no = int((seq_row[0] if seq_row else 0) or 0) + 1

    from src.engine.lifecycle_events import build_economic_close_canonical_write

    events, projection = build_economic_close_canonical_write(
        position,
        sequence_no=sequence_no,
        phase_before="pending_exit",
        source_module="src.execution.exchange_reconcile",
    )
    _apply_exit_fill_projection_and_execution_fact(
        conn,
        events=events,
        projection=projection,
        position=position,
        command=command,
        observed_at=observed_at,
        shares=shares_dec,
        exit_price=exit_price_dec,
        upsert_only=False,
    )


def _exit_fill_economics_for_command(
    conn: sqlite3.Connection,
    *,
    command_id: str,
    fallback_filled_size: str,
    fallback_fill_price: str,
) -> tuple[Decimal, Decimal] | None:
    rows = conn.execute(
        """
        SELECT tf.state, tf.filled_size, tf.fill_price
          FROM venue_trade_facts tf
          JOIN (
                SELECT trade_id, MAX(local_sequence) AS local_sequence
                  FROM venue_trade_facts
                 WHERE command_id = ?
                 GROUP BY trade_id
               ) latest
            ON latest.trade_id = tf.trade_id
           AND latest.local_sequence = tf.local_sequence
         WHERE tf.command_id = ?
           AND tf.state = 'CONFIRMED'
        """,
        (command_id, command_id),
    ).fetchall()
    shares = Decimal("0")
    proceeds = Decimal("0")
    for row in rows:
        filled = _positive_decimal_or_none(row["filled_size"])
        price = _positive_decimal_or_none(row["fill_price"])
        if filled is None or price is None:
            continue
        shares += filled
        proceeds += filled * price
    if shares > Decimal("0") and proceeds > Decimal("0"):
        return shares, proceeds / shares

    fallback_shares = _positive_decimal_or_none(fallback_filled_size)
    fallback_price = _positive_decimal_or_none(fallback_fill_price)
    if fallback_shares is None or fallback_price is None:
        return None
    return fallback_shares, fallback_price


def _apply_entry_fill_projection_and_execution_fact(
    conn: sqlite3.Connection,
    *,
    events: list[dict],
    projection: dict,
    position: SimpleNamespace,
    command: Mapping[str, Any],
    observed_at: datetime,
    order_status: str,
    shares: Decimal,
    entry_price: Decimal,
    upsert_only: bool,
) -> None:
    from src.state.db import append_many_and_project, log_execution_fact
    from src.state.projection import upsert_position_current

    sp_name = f"sp_entry_fill_{uuid.uuid4().hex[:12]}"
    conn.execute(f"SAVEPOINT {sp_name}")
    try:
        if upsert_only:
            upsert_position_current(conn, projection)
        else:
            append_many_and_project(conn, events, projection)
        position_id = str(getattr(position, "trade_id", "") or "")
        submitted_price = _float_or_none(command.get("price"))
        fill_price = _float_or_none(entry_price)
        filled_shares = _float_or_none(shares)
        terminal_status = "filled" if order_status == "filled" else "partial"
        venue_status = "FILLED" if terminal_status == "filled" else "PARTIAL"
        log_execution_fact(
            conn,
            intent_id=f"{position_id}:entry",
            position_id=position_id,
            decision_id=str(command.get("decision_id") or "") or None,
            command_id=str(command.get("command_id") or "") or None,
            order_role="entry",
            strategy_key=str(getattr(position, "strategy_key", "") or "") or None,
            posted_at=(
                str(getattr(position, "order_posted_at", "") or "")
                or str(command.get("created_at") or "")
                or None
            ),
            filled_at=observed_at.isoformat(),
            submitted_price=submitted_price,
            fill_price=fill_price,
            shares=filled_shares,
            venue_status=venue_status,
            terminal_exec_status=terminal_status,
        )
        _append_entry_position_lots_for_command(conn, command=command, observed_at=observed_at)
        conn.execute(f"RELEASE SAVEPOINT {sp_name}")
    except Exception:
        conn.execute(f"ROLLBACK TO SAVEPOINT {sp_name}")
        conn.execute(f"RELEASE SAVEPOINT {sp_name}")
        raise


def _apply_exit_fill_projection_and_execution_fact(
    conn: sqlite3.Connection,
    *,
    events: list[dict],
    projection: dict,
    position: SimpleNamespace,
    command: Mapping[str, Any],
    observed_at: datetime,
    shares: Decimal,
    exit_price: Decimal,
    upsert_only: bool,
) -> None:
    from src.state.db import append_many_and_project, log_execution_fact
    from src.state.projection import upsert_position_current

    sp_name = f"sp_exit_fill_{uuid.uuid4().hex[:12]}"
    conn.execute(f"SAVEPOINT {sp_name}")
    try:
        if upsert_only:
            upsert_position_current(conn, projection)
        else:
            append_many_and_project(conn, events, projection)
        position_id = str(getattr(position, "trade_id", "") or "")
        log_execution_fact(
            conn,
            intent_id=f"{position_id}:exit",
            position_id=position_id,
            decision_id=str(command.get("decision_id") or "") or None,
            command_id=str(command.get("command_id") or "") or None,
            order_role="exit",
            strategy_key=str(getattr(position, "strategy_key", "") or "") or None,
            posted_at=str(command.get("created_at") or "") or None,
            filled_at=observed_at.isoformat(),
            submitted_price=_float_or_none(command.get("price")),
            fill_price=_float_or_none(exit_price),
            shares=_float_or_none(shares),
            venue_status="FILLED",
            terminal_exec_status="filled",
        )
        conn.execute(f"RELEASE SAVEPOINT {sp_name}")
    except Exception:
        conn.execute(f"ROLLBACK TO SAVEPOINT {sp_name}")
        conn.execute(f"RELEASE SAVEPOINT {sp_name}")
        raise


def _append_entry_position_lots_for_command(
    conn: sqlite3.Connection,
    *,
    command: Mapping[str, Any],
    observed_at: datetime,
) -> None:
    if str(command.get("intent_kind") or "").upper() != "ENTRY":
        return
    if str(command.get("side") or "").upper() != "BUY":
        return
    from src.state.venue_command_repo import append_position_lot, resolve_position_lot_id_for_command

    position_lot_id = resolve_position_lot_id_for_command(conn, command)
    if position_lot_id is None:
        return
    rows = conn.execute(
        """
        SELECT tf.*
          FROM venue_trade_facts tf
          JOIN (
                SELECT trade_id, MAX(local_sequence) AS local_sequence
                  FROM venue_trade_facts
                 WHERE command_id = ?
                 GROUP BY trade_id
               ) latest
            ON latest.trade_id = tf.trade_id
           AND latest.local_sequence = tf.local_sequence
         WHERE tf.command_id = ?
           AND tf.state IN ('MATCHED', 'MINED', 'CONFIRMED')
         ORDER BY tf.observed_at, tf.trade_fact_id
        """,
        (str(command.get("command_id") or ""), str(command.get("command_id") or "")),
    ).fetchall()
    for row in rows:
        if _positive_decimal_or_none(row["filled_size"]) is None:
            continue
        if _positive_decimal_or_none(row["fill_price"]) is None:
            continue
        existing = conn.execute(
            """
            SELECT 1
              FROM position_lots
             WHERE source_trade_fact_id = ?
             LIMIT 1
            """,
            (int(row["trade_fact_id"]),),
        ).fetchone()
        if existing is not None:
            continue
        state = "CONFIRMED_EXPOSURE" if str(row["state"]) == "CONFIRMED" else "OPTIMISTIC_EXPOSURE"
        append_position_lot(
            conn,
            position_id=position_lot_id,
            state=state,
            shares=str(row["filled_size"]),
            entry_price_avg=str(row["fill_price"]),
            source_command_id=str(command["command_id"]),
            source_trade_fact_id=int(row["trade_fact_id"]),
            captured_at=row["observed_at"] or observed_at,
            state_changed_at=row["observed_at"] or observed_at,
            source=str(row["source"] or "REST"),
            observed_at=row["observed_at"] or observed_at,
            venue_timestamp=row["venue_timestamp"],
            raw_payload_json={
                "source": "exchange_reconcile_entry_fill_materialization",
                "command_id": str(command["command_id"]),
                "trade_fact_id": int(row["trade_fact_id"]),
                "trade_id": str(row["trade_id"]),
                "market_id": str(command.get("market_id") or ""),
                "token_id": str(command.get("token_id") or ""),
            },
        )


def _local_command_for_trade(
    raw: Mapping[str, Any],
    local_by_order: Mapping[str, dict[str, Any]],
) -> tuple[str | None, dict[str, Any] | None]:
    for order_id in _trade_order_ids(raw):
        command = local_by_order.get(order_id)
        if command is not None:
            return order_id, command
    return None, None


def _selected_maker_order(raw: Mapping[str, Any], order_id: str | None) -> Mapping[str, Any] | None:
    if not order_id:
        return None
    for maker in raw.get("maker_orders") or []:
        if not isinstance(maker, Mapping):
            continue
        maker_order_id = _string_or_none(
            _first_present(maker, "order_id", "orderID", "orderId", default=None)
        )
        if maker_order_id == order_id:
            return maker
    return None


def _entry_fill_covers_command(
    conn: sqlite3.Connection,
    command: Mapping[str, Any],
    shares: Decimal,
) -> bool:
    command_id = str(command.get("command_id") or "").strip()
    venue_order_id = str(command.get("venue_order_id") or "").strip()
    if command_id and _table_exists(conn, "venue_order_facts"):
        row = conn.execute(
            """
            SELECT state, remaining_size, matched_size
              FROM venue_order_facts
             WHERE command_id = ?
               AND (? = '' OR venue_order_id = ?)
             ORDER BY local_sequence DESC, fact_id DESC
             LIMIT 1
            """,
            (command_id, venue_order_id, venue_order_id),
        ).fetchone()
        if row is not None:
            state = str(row["state"] or "").upper()
            matched = _positive_decimal_or_none(row["matched_size"])
            try:
                remaining_zero = _decimal(row["remaining_size"]) == Decimal("0")
            except ValueError:
                remaining_zero = False
            if state == "MATCHED" and remaining_zero and matched is not None:
                return True

    target = _positive_decimal_or_none(command.get("size"))
    if target is None:
        return str(command.get("state") or "").upper() == "FILLED"
    return shares >= target


def _trade_filled_size(raw: Mapping[str, Any], order_id: str | None) -> Any:
    maker = _selected_maker_order(raw, order_id)
    if maker is not None:
        return _first_present(
            maker,
            "matched_amount",
            "matchedAmount",
            "filled_size",
            "size",
            "amount",
            default=None,
        )
    return _first_present(raw, "filled_size", "size", "amount", default=None)


def _trade_fill_price(raw: Mapping[str, Any], order_id: str | None) -> Any:
    maker = _selected_maker_order(raw, order_id)
    if maker is not None:
        return _first_present(maker, "avgPrice", "avg_price", "fillPrice", "fill_price", "price", default=None)
    if _taker_order_price_applies(raw, order_id):
        return _first_present(raw, "avgPrice", "avg_price", "fillPrice", "fill_price", "price", default=None)
    return _first_explicit_fill_price(raw)


def _first_explicit_fill_price(raw: Mapping[str, Any]) -> Any:
    return _first_present(raw, "avgPrice", "avg_price", "fillPrice", "fill_price", default=None)


def _taker_order_price_applies(raw: Mapping[str, Any], order_id: str | None) -> bool:
    if not order_id:
        return False
    taker_order_id = _string_or_none(_first_present(raw, "taker_order_id", "takerOrderId", default=None))
    return taker_order_id == order_id


def _missing_trade_fill_economics(
    *,
    state: str,
    filled_size: Any,
    fill_price: Any,
) -> tuple[str, ...]:
    if state not in {"MATCHED", "MINED", "CONFIRMED"}:
        return ()
    missing: list[str] = []
    if not _positive_decimal(filled_size):
        missing.append("filled_size")
    if not _positive_decimal(fill_price):
        missing.append("fill_price")
    return tuple(missing)


def _positive_decimal(value: Any) -> bool:
    if value is None or value == "":
        return False
    try:
        decimal = _decimal(value)
    except (InvalidOperation, ValueError):
        return False
    return decimal.is_finite() and decimal > Decimal("0")


def _positive_decimal_or_none(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        decimal = _decimal(value)
    except (InvalidOperation, ValueError):
        return None
    if not decimal.is_finite() or decimal <= Decimal("0"):
        return None
    return decimal


def _decimal_text(value: Decimal) -> str:
    return str(value)


def _float_or_none(value: Any) -> float | None:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if numeric != numeric or numeric in (float("inf"), float("-inf")):
        return None
    return numeric


def _latest_trade_fact_for_trade_id(conn: sqlite3.Connection, trade_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT *
          FROM venue_trade_facts
         WHERE trade_id = ?
         ORDER BY local_sequence DESC, trade_fact_id DESC
         LIMIT 1
        """,
        (trade_id,),
    ).fetchone()
    return dict(row) if row is not None else None


def _trade_fact_identity_mismatch(
    fact: Mapping[str, Any],
    *,
    command: Mapping[str, Any],
    venue_order_id: str,
) -> list[str]:
    mismatch: list[str] = []
    if str(fact.get("command_id") or "") != str(command.get("command_id") or ""):
        mismatch.append("command_id")
    if str(fact.get("venue_order_id") or "") != str(venue_order_id or ""):
        mismatch.append("venue_order_id")
    return mismatch


def _same_trade_fill_economics(
    fact: Mapping[str, Any],
    *,
    filled_size: str,
    fill_price: str,
) -> bool:
    return (
        _same_decimal_value(fact.get("filled_size"), filled_size)
        and _same_decimal_value(fact.get("fill_price"), fill_price)
    )


def _same_decimal_value(left: Any, right: Any) -> bool:
    try:
        return _decimal(left) == _decimal(right)
    except (InvalidOperation, ValueError):
        return False


def _confirmed_price_revision_has_authority(
    fact: Mapping[str, Any],
    *,
    raw: Mapping[str, Any],
    venue_order_id: str | None,
    state: str,
    filled_size: str,
) -> bool:
    previous = str(fact.get("state") or "")
    if state != "CONFIRMED" or previous not in {"MATCHED", "MINED"}:
        return False
    if not _trade_lifecycle_transition_allowed(previous, state):
        return False
    if not _same_decimal_value(fact.get("filled_size"), filled_size):
        return False
    return (
        _taker_order_price_applies(raw, venue_order_id)
        or _selected_maker_order(raw, venue_order_id) is not None
        or _first_explicit_fill_price(raw) is not None
    )


def _trade_lifecycle_transition_allowed(previous: str, current: str) -> bool:
    if previous == current:
        return False
    allowed = {
        "RETRYING": {"MATCHED", "MINED", "CONFIRMED", "FAILED"},
        "MATCHED": {"MINED", "CONFIRMED", "FAILED"},
        "MINED": {"CONFIRMED", "FAILED"},
        "CONFIRMED": set(),
        "FAILED": set(),
    }
    return current in allowed.get(previous, set())


def _fill_event_for_command(
    command: Mapping[str, Any],
    filled_size: str,
    *,
    trade_state: str,
) -> str | None:
    state = str(command.get("state") or "")
    if state in {"FILLED", "CANCELLED", "EXPIRED", "REJECTED", "SUBMIT_REJECTED"}:
        return None
    if trade_state in {"FAILED", "RETRYING"}:
        return None
    if trade_state != "CONFIRMED":
        return "PARTIAL_FILL_OBSERVED"
    size = _decimal(command.get("size", 0))
    filled = _decimal(filled_size)
    if filled >= size:
        return "FILL_CONFIRMED"
    return "PARTIAL_FILL_OBSERVED"


def _local_commands_by_order(conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT *
          FROM venue_commands
         WHERE venue_order_id IS NOT NULL
           AND TRIM(venue_order_id) != ''
        """
    ).fetchall()
    return {str(row["venue_order_id"]): dict(row) for row in rows}


def _local_open_order_ids(conn: sqlite3.Connection) -> tuple[str, ...]:
    local_by_order = _local_commands_by_order(conn)
    return tuple(
        order_id
        for order_id, command in local_by_order.items()
        if _local_order_is_open(conn, command)
    )


def _local_order_is_open(conn: sqlite3.Connection, command: Mapping[str, Any]) -> bool:
    if str(command.get("state")) not in _OPEN_LOCAL_STATES:
        return False
    latest = _latest_order_fact(conn, str(command["venue_order_id"]))
    if latest is None:
        return True
    return str(latest.get("state")) in _OPEN_ORDER_FACT_STATES


def _latest_order_fact(conn: sqlite3.Connection, venue_order_id: str) -> dict[str, Any] | None:
    rows = conn.execute(
        """
        SELECT *
          FROM venue_order_facts
         WHERE venue_order_id = ?
         ORDER BY local_sequence DESC, fact_id DESC
        """,
        (venue_order_id,),
    ).fetchall()
    facts = [dict(row) for row in rows]
    if not facts:
        return None

    from src.execution.order_truth_reducer import VenueOrderTruthReducer

    reduced = VenueOrderTruthReducer.reduce(order_facts=facts)
    reduced_state = str(reduced.state or "").upper()
    for fact in facts:
        fact_state = str(fact.get("state") or "").upper()
        if fact_state != reduced_state:
            continue
        try:
            remaining = _decimal(fact.get("remaining_size"))
        except ValueError:
            remaining = None
        try:
            matched = _decimal(fact.get("matched_size"))
        except ValueError:
            matched = Decimal("0")
        if reduced.remaining_size is not None and remaining != reduced.remaining_size:
            continue
        if matched != reduced.matched_size:
            continue
        return fact
    return facts[0]


def _local_absence_kind(context: ReconcileContext) -> FindingKind:
    if context == "heartbeat_loss":
        return "heartbeat_suspected_cancel"
    if context == "cutover":
        return "cutover_wipe"
    return "local_orphan_order"


def _trade_fill_covers_local_command(command: Mapping[str, Any], filled: Decimal | None) -> bool:
    if filled is None:
        return False
    try:
        requested = _decimal(command.get("size"))
    except (InvalidOperation, ValueError):
        return False
    return requested > Decimal("0") and filled >= requested


def _exchange_positions_by_token(positions: list[Any]) -> dict[str, Decimal]:
    out: dict[str, Decimal] = {}
    for position in positions or []:
        raw = _raw(position)
        token = _first_present(raw, "asset", "token_id", "tokenId", "asset_id", default=None)
        if token is None or str(token).strip() == "":
            continue
        key = str(token).strip()
        out[key] = out.get(key, Decimal("0")) + _decimal(
            _first_present(raw, "size", "balance", "amount", default="0")
        )
    return out


def _journal_positions_by_token(
    conn: sqlite3.Connection,
    *,
    states: frozenset[str],
) -> dict[str, Decimal]:
    if not states:
        return {}
    selected_states = tuple(sorted(states))
    state_placeholders = ", ".join("?" for _ in selected_states)
    inactive_phases = tuple(sorted(INACTIVE_RUNTIME_STATES))
    inactive_placeholders = ", ".join("?" for _ in inactive_phases)
    non_current_exit_statuses = tuple(sorted(_PENDING_EXIT_NON_CURRENT_ORDER_STATUSES))
    non_current_exit_status_placeholders = ", ".join("?" for _ in non_current_exit_statuses)
    rows = conn.execute(
        f"""
        SELECT c.token_id, c.side, tf.filled_size, tf.fill_price
          FROM venue_trade_facts tf
          JOIN venue_commands c ON c.command_id = tf.command_id
          LEFT JOIN position_current pc ON pc.position_id = c.position_id
         WHERE tf.local_sequence = (
               SELECT MAX(newer.local_sequence)
                 FROM venue_trade_facts newer
                WHERE newer.trade_id = tf.trade_id
         )
           AND tf.state IN ({state_placeholders})
           AND (
                c.position_id IS NULL
                OR c.position_id = ''
                OR pc.position_id IS NULL
                OR (
                    COALESCE(pc.phase, '') NOT IN ({inactive_placeholders})
                    AND NOT (
                        pc.phase = 'pending_exit'
                        AND pc.chain_state = 'exit_pending_missing'
                        AND LOWER(COALESCE(pc.order_status, '')) IN ({non_current_exit_status_placeholders})
                    )
                )
           )
        """,
        (*selected_states, *inactive_phases, *non_current_exit_statuses),
    ).fetchall()
    out: dict[str, Decimal] = {}
    for row in rows:
        if not trade_fact_has_positive_fill_economics(row):
            continue
        token = str(row["token_id"])
        signed = _decimal(row["filled_size"])
        if str(row["side"]).upper() == "SELL":
            signed = -signed
        out[token] = out.get(token, Decimal("0")) + signed
    return out


def _settlement_command_token_holdings_by_token(conn: sqlite3.Connection) -> dict[str, Decimal]:
    """Expected wallet CTF holdings from redeem commands not yet confirmed.

    ``_journal_positions_by_token`` is an active-exposure view. M5 compares
    against the venue wallet position surface, so settled positions that have
    queued/operator-gated redeem commands remain expected wallet holdings while
    the redeem command is still pending. Failed or review-required commands do
    not attest to an active redeem path and must not mask real wallet drift.
    """

    if not _table_exists(conn, "settlement_commands"):
        return {}
    pending_states = tuple(sorted(_REDEEM_PENDING_WALLET_HOLDING_STATES))
    state_placeholders = ", ".join("?" for _ in pending_states)
    rows = conn.execute(
        f"""
        SELECT token_amounts_json
          FROM settlement_commands
         WHERE state IN ({state_placeholders})
           AND TRIM(COALESCE(token_amounts_json, '')) != ''
        """,
        pending_states,
    ).fetchall()
    out: dict[str, Decimal] = {}
    for row in rows:
        payload = _json_mapping(row["token_amounts_json"])
        for token, raw_amount in payload.items():
            token_id = str(token).strip()
            if not token_id:
                continue
            amount = _positive_decimal_or_none(raw_amount)
            if amount is None:
                continue
            out[token_id] = out.get(token_id, Decimal("0")) + amount
    return out


def _has_recent_filled_suppression(
    conn: sqlite3.Connection,
    token_id: str,
    observed_at: datetime,
    *,
    seconds: int = 300,
) -> bool:
    rows = conn.execute(
        """
        SELECT updated_at
          FROM venue_commands
         WHERE token_id = ?
           AND state = 'FILLED'
        """,
        (token_id,),
    ).fetchall()
    for row in rows:
        try:
            updated = _coerce_dt(row["updated_at"])
        except ValueError:
            continue
        if abs((observed_at - updated).total_seconds()) <= seconds:
            return True
    return False


def _row_by_id(conn: sqlite3.Connection, finding_id: str) -> Optional[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM exchange_reconcile_findings WHERE finding_id = ?",
        (finding_id,),
    ).fetchone()


def _find_unresolved_row(
    conn: sqlite3.Connection,
    *,
    kind: FindingKind,
    subject_id: str,
    context: ReconcileContext,
) -> Optional[sqlite3.Row]:
    return conn.execute(
        """
        SELECT * FROM exchange_reconcile_findings
         WHERE kind = ?
           AND subject_id = ?
           AND context = ?
           AND resolved_at IS NULL
         ORDER BY recorded_at, finding_id
         LIMIT 1
        """,
        (kind, subject_id, context),
    ).fetchone()


def _finding_from_row(row: sqlite3.Row) -> ReconcileFinding:
    return ReconcileFinding(
        finding_id=str(row["finding_id"]),
        kind=_validate_kind(str(row["kind"])),
        subject_id=str(row["subject_id"]),
        context=_validate_context(str(row["context"])),
        evidence_json=str(row["evidence_json"]),
        recorded_at=_coerce_dt(row["recorded_at"]),
    )


def _call_required(adapter: Any, method: str) -> list[Any]:
    fn = getattr(adapter, method, None)
    if not callable(fn):
        raise AttributeError(f"adapter must expose {method}() for M5 reconciliation")
    result = fn()
    return list(result or [])


def _call_optional(adapter: Any, method: str) -> list[Any]:
    fn = getattr(adapter, method, None)
    if not callable(fn):
        return []
    return list(fn() or [])


def _assert_adapter_read_fresh(adapter: Any, surface: str, observed_at: datetime) -> None:
    freshness = getattr(adapter, "read_freshness", None)
    if not isinstance(freshness, Mapping):
        raise ValueError(f"{surface} venue read freshness is unavailable")
    value = freshness.get(surface)
    if value is True:
        return
    if isinstance(value, Mapping):
        has_ok = "ok" in value
        has_fresh = "fresh" in value
        if has_ok and value["ok"] is not True:
            raise ValueError(f"{surface} venue read is not fresh/successful")
        if not has_fresh or value["fresh"] is not True:
            raise ValueError(f"{surface} venue read is not fresh/successful")
        captured_at = value.get("captured_at") or value.get("observed_at")
        if captured_at is not None and _coerce_dt(captured_at) > observed_at:
            raise ValueError(f"{surface} venue read freshness timestamp is in the future")
        return
    raise ValueError(f"{surface} venue read is not fresh/successful")


def _raw(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    raw = getattr(value, "raw", None)
    if isinstance(raw, Mapping):
        return dict(raw)
    return dict(getattr(value, "__dict__", {}) or {})


def _order_id(value: Any) -> str | None:
    raw = _raw(value)
    direct = getattr(value, "order_id", None)
    if direct:
        return str(direct)
    return _string_or_none(_first_present(raw, "orderID", "orderId", "order_id", "id", default=None))


def _point_order_lookup(adapter: Any, order_id: str) -> Any | None:
    fn = getattr(adapter, "get_order", None)
    if not callable(fn):
        return None
    return fn(order_id)


def _order_state(value: Any | None) -> str | None:
    if value is None:
        return None
    raw = _raw(value)
    direct = getattr(value, "status", None)
    state = direct if direct is not None else _first_present(raw, "status", "state", "order_status", default=None)
    if state is None:
        return None
    text = str(state).strip().upper()
    return text or None


def _trade_id(raw: Mapping[str, Any]) -> str | None:
    return _string_or_none(_first_present(raw, "trade_id", "tradeID", "id", default=None))


def _trade_order_id(raw: Mapping[str, Any]) -> str | None:
    ids = _trade_order_ids(raw)
    return ids[0] if ids else None


def _trade_order_ids(raw: Mapping[str, Any]) -> list[str]:
    candidates: list[str] = []
    for key in ("orderID", "orderId", "order_id", "maker_order_id", "taker_order_id"):
        value = _string_or_none(_first_present(raw, key, default=None))
        if value:
            candidates.append(value)
    for maker in raw.get("maker_orders") or []:
        if not isinstance(maker, Mapping):
            continue
        value = _string_or_none(
            _first_present(maker, "order_id", "orderID", "orderId", default=None)
        )
        if value:
            candidates.append(value)
    return list(dict.fromkeys(candidates))


def _trade_state(raw: Mapping[str, Any]) -> str | None:
    raw_state = _first_present(raw, "state", "status", default=None)
    if raw_state is None:
        return None
    state = str(raw_state).upper()
    return state if state in _TRADE_FACT_STATES else None


def _first_present(raw: Mapping[str, Any], *keys: str, default: Any = None) -> Any:
    for key in keys:
        if key in raw and raw[key] is not None:
            return raw[key]
    return default


def _string_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _stable_subject(prefix: str, raw: Mapping[str, Any]) -> str:
    return f"{prefix}:{_hash_payload(raw)[:16]}"


def _command_evidence(command: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "command_id": command.get("command_id"),
        "venue_order_id": command.get("venue_order_id"),
        "state": command.get("state"),
        "position_id": command.get("position_id"),
        "token_id": command.get("token_id"),
        "side": command.get("side"),
        "size": command.get("size"),
        "updated_at": command.get("updated_at"),
    }


def _validate_kind(kind: str) -> FindingKind:
    if kind not in _FINDING_KINDS:
        raise ValueError(f"invalid reconcile finding kind: {kind!r}")
    return kind  # type: ignore[return-value]


def _validate_context(context: str) -> ReconcileContext:
    if context not in _CONTEXTS:
        raise ValueError(f"invalid reconcile context: {context!r}")
    return context  # type: ignore[return-value]


def _require_nonempty(field: str, value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} is required")
    return value.strip()


def _coerce_dt(value: datetime | str | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    text = str(value)
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"invalid datetime {text!r}") from exc
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def _decimal(value: Any) -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"cannot parse decimal value {value!r}") from exc


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _hash_payload(value: Mapping[str, Any]) -> str:
    return sha256(_canonical_json(dict(value)).encode("utf-8")).hexdigest()
