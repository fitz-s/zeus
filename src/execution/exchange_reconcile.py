# Created: 2026-05 (R3 M5)
# Last reused or audited: 2026-06-10
# Authority basis (operator external-close incident chain 2026-06-10): the operator
#   manually SOLD Zeus's position on the SHARED proxy wallet. When the order FILLED the
#   void-misbooking double-counted the same 66.25 economic claim (journal buy-claim +
#   voided-position terminal-holdings = expected_wallet 132.50 vs exchange 0), so
#   position_drift re-recorded forever. The K=1 absorption (a) books the external close
#   as a SELL exit fact consuming the journal buy-claim and (b) tags the dangling
#   terminal position chain_state=external_operator_closed so the closed-holdings view
#   stops contributing it. STRICTLY gated on an operator-acknowledged resolution row for
#   the SAME subject token. See _absorb_operator_external_close.
# Authority basis (2026-06-10 operator-acknowledged ghost antibody): an in-Zeus-domain
#   resting order the operator manually placed on the SHARED proxy wallet and
#   explicitly acknowledged (a prior finding resolved_by 'session_operator_confirmed'
#   or resolution prefix 'operator_manual') is record-and-resolved while unfilled
#   (size_matched == 0), so one acknowledged unwind cannot freeze the engine via the
#   risk_allocator reconcile_finding_threshold or the WS two-proofs M5 zero-findings
#   latch. Any matched size voids the acknowledgment (fail-closed, mirrors the
#   foreign-wallet matched-size tripwire). See _is_operator_acknowledged_resting_order.
# Authority basis: R3 M5 reconcile + 2026-06-04 M5 mutex-IO antibody. The adapter-
#   touching entrypoints (fresh_reconcile_snapshot, run_reconcile_sweep) assert
#   the world write mutex is NOT held before any venue read, so a future caller
#   that holds the lock across the reconcile sweep fails loud (WorldMutexIOViolation)
#   at the reconcile boundary instead of wedging the daemon (STEP-7 / #95 disease).
#   2026-06-09 foreign-wallet classification: the wallet is NOT exclusively Zeus's —
#   the operator places manual orders on the same proxy wallet (observed: 6 LIVE GTC
#   orders on AI-themed markets tripping reconcile_finding_threshold and freezing all
#   Zeus entries). A resting, zero-fill venue order on a market entirely outside
#   Zeus's domain (never in executable_market_snapshots NOR venue_commands) cannot be
#   a lost Zeus side effect; it is recorded for audit and immediately resolved instead
#   of arming the kill switch. Any matched size or any Zeus-domain market keeps the
#   strict fail-closed ghost path (credential-compromise tripwire intact).
"""R3 M5 exchange reconciliation sweep.

This module reconciles read-only exchange observations against Zeus's durable
venue-command/fact journal.  It is intentionally not an execution actuator:
exchange-only state becomes an ``exchange_reconcile_findings`` row, not a new
``venue_commands`` row, and no live venue submit/cancel/redeem side effects are
performed here.

LOCK DISCIPLINE (2026-06-04): the venue reads here (``get_open_orders`` /
``get_trades`` / ``get_positions`` / per-order ``get_order``) are BLOCKING
network/on-chain I/O.  The runtime callers pre-capture those surfaces OFF any
DB write lock via ``fresh_reconcile_snapshot`` and then reconcile against the
immutable snapshot, so no venue read happens while the zeus-world.db write
mutex is held.  The ``assert_no_world_mutex_held_for_io`` guard at the
adapter-touching entrypoints enforces that discipline structurally.
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
from src.state.db import assert_no_world_mutex_held_for_io
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
_CHAIN_CONFIRMED_HELD_PHASES = frozenset({"active", "day0_window"})
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
_CLOSED_POSITION_WALLET_HOLDING_PHASES = frozenset({"settled", "admin_closed", "voided"})
_CLOSED_POSITION_WALLET_HOLDING_CHAIN_STATES = frozenset({"synced", "exit_pending_missing"})
# A terminal position whose CTF tokens left the wallet via an operator-confirmed
# EXTERNAL close (the operator manually sold Zeus's position on the shared proxy
# wallet). The tokens are provably no longer on-chain, so this chain_state is
# DELIBERATELY excluded from _CLOSED_POSITION_WALLET_HOLDING_CHAIN_STATES: the
# closed-position-holdings view assumes tokens are still on-chain, and a position
# tagged here must NOT contribute an expected-wallet holding (that double-count is
# exactly the 2026-06-10 void-misbooking disease). The historical ``shares`` record
# is preserved — only the chain reality tag changes.
_EXTERNAL_OPERATOR_CLOSED_CHAIN_STATE = "external_operator_closed"
_REDEEM_TERMINAL_WALLET_CONTRADICTION_STATES = frozenset(
    {"REDEEM_CONFIRMED", "REDEEM_FAILED", "REDEEM_REVIEW_REQUIRED"}
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


def _canonical_trade_fact_cte(cte_name: str = "canonical_trade_fact") -> str:
    """Rank trade facts by proof strength before local_sequence recency."""

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

    # The snapshot capture below performs the BLOCKING venue reads. It MUST run
    # off any DB write lock so a stalled venue read never wedges a held world
    # txn (STEP-7 / #95 / M5 disease). Fail loud + located if a caller holds it.
    assert_no_world_mutex_held_for_io("m5.fresh_reconcile_snapshot")
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
    # Foreign-wallet ghost findings are resolvable from local evidence alone (no venue
    # read): run the migration pass here too so the kill switch clears on the next
    # 1-minute refresh instead of waiting for the next full ws-gap sweep.
    foreign_resolved = _resolve_foreign_wallet_ghost_findings(conn, observed_at=observed)
    foreign_resolved += _resolve_operator_acknowledged_ghost_findings(conn, observed_at=observed)
    token_ids = _unresolved_position_drift_tokens(conn)
    trade_ids = _unresolved_unrecorded_trade_ids(conn)
    if not token_ids and not trade_ids:
        return {
            "status": "not_required",
            "resolved": foreign_resolved,
            "remaining": len(list_unresolved_findings(conn)),
        }

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
            open_orders=snapshot.adapter.get_open_orders(),
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
    # Defence-in-depth: the sweep may issue per-order ``get_order`` venue reads
    # inside the local-order loop. Runtime callers pass a pre-captured snapshot
    # adapter (no live I/O), but a future caller handing a LIVE adapter while
    # holding the world write mutex would re-introduce the wedge — fail loud.
    assert_no_world_mutex_held_for_io("m5.run_reconcile_sweep")
    init_exchange_reconcile_schema(conn)
    observed = _coerce_dt(observed_at)

    findings: list[ReconcileFinding] = []
    _assert_adapter_read_fresh(adapter, "open_orders", observed)
    open_orders = _call_required(adapter, "get_open_orders")
    open_order_ids = {_order_id(item) for item in open_orders if _order_id(item)}
    local_by_order = _local_commands_by_order(conn)
    positions_available = callable(getattr(adapter, "get_positions", None))
    if positions_available:
        _assert_adapter_read_fresh(adapter, "positions", observed)
        positions = adapter.get_positions()
    else:
        positions = []
    exchange_positions = _exchange_positions_by_token(positions)

    for order in open_orders:
        order_id = _order_id(order)
        if not order_id:
            continue
        if order_id not in local_by_order:
            raw = _raw(order)
            recovered = _recover_live_ghost_sell_order_for_known_position(
                conn,
                raw,
                exchange_positions=exchange_positions,
                observed_at=observed,
            )
            if recovered is not None:
                local_by_order[order_id] = recovered
                continue
            if _is_foreign_wallet_resting_order(conn, raw):
                _record_foreign_wallet_ghost(
                    conn,
                    order_id=order_id,
                    raw=raw,
                    context=context,
                    observed_at=observed,
                )
                continue
            if _is_operator_acknowledged_resting_order(conn, order_id, raw):
                _record_operator_acknowledged_ghost(
                    conn,
                    order_id=order_id,
                    raw=raw,
                    context=context,
                    observed_at=observed,
                )
                continue
            findings.append(
                record_finding(
                    conn,
                    kind="exchange_ghost_order",
                    subject_id=order_id,
                    context=context,
                    evidence={
                        "exchange_order": raw,
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
            if context == "ws_gap" and not (set(candidate_order_ids) & set(local_by_order)):
                continue
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

    if positions_available:
        findings.extend(
            _record_position_drift_findings(
                conn,
                positions=positions,
                open_orders=open_orders,
                context=context,
                observed_at=observed,
            )
        )
    _resolve_foreign_wallet_ghost_findings(conn, observed_at=observed)
    _resolve_operator_acknowledged_ghost_findings(conn, observed_at=observed)
    _resolve_disappeared_ghost_order_findings(
        adapter, conn, open_order_ids, trades=trades if trades_available else None, observed_at=observed
    )
    reconcile_recorded_maker_fill_economics(conn, observed_at=observed)
    return findings


_FOREIGN_WALLET_GHOST_RESOLUTION = "foreign_wallet_order_market_outside_zeus_domain"


def _order_matched_size(raw: Mapping[str, Any]) -> Decimal:
    try:
        return Decimal(str(raw.get("size_matched") or "0"))
    except (InvalidOperation, ValueError):
        # Unparseable matched size cannot prove "no fill" — stay strict.
        return Decimal("1")


def _is_market_in_zeus_domain(conn: sqlite3.Connection, market_id: str) -> bool:
    """Whether Zeus has ever discovered or commanded this market.

    Fail-closed: if the snapshot surface is missing/empty (so domain membership
    cannot be proven either way), every market is treated as in-domain and the
    strict ghost path applies.
    """

    if not market_id:
        return True
    try:
        snapshot_total = conn.execute(
            "SELECT COUNT(*) FROM executable_market_snapshots"
        ).fetchone()
        if snapshot_total is None or int(snapshot_total[0]) == 0:
            return True
        in_snapshots = conn.execute(
            "SELECT 1 FROM executable_market_snapshots WHERE condition_id = ? LIMIT 1",
            (market_id,),
        ).fetchone()
    except sqlite3.OperationalError:
        return True
    if in_snapshots is not None:
        return True
    in_commands = conn.execute(
        "SELECT 1 FROM venue_commands WHERE market_id = ? LIMIT 1",
        (market_id,),
    ).fetchone()
    return in_commands is not None


def _is_foreign_wallet_resting_order(conn: sqlite3.Connection, raw: Mapping[str, Any]) -> bool:
    """A zero-fill open order on a market entirely outside Zeus's domain.

    The wallet is shared with the operator's manual trading (2026-06-09: manual
    GTC orders on AI-themed markets armed the kill switch and froze all Zeus
    entries). Such an order cannot be a lost Zeus side effect. Any matched size
    or any Zeus-domain market keeps the strict fail-closed ghost path.
    """

    market_id = str(raw.get("market") or "")
    if not market_id:
        return False
    if _order_matched_size(raw) != 0:
        return False
    return not _is_market_in_zeus_domain(conn, market_id)


def _record_foreign_wallet_ghost(
    conn: sqlite3.Connection,
    *,
    order_id: str,
    raw: Mapping[str, Any],
    context: ReconcileContext,
    observed_at: datetime,
) -> None:
    """Audit-record a foreign wallet order without arming the kill switch."""

    existing = conn.execute(
        """
        SELECT 1 FROM exchange_reconcile_findings
         WHERE kind = 'exchange_ghost_order'
           AND subject_id = ?
           AND resolution = ?
         LIMIT 1
        """,
        (order_id, _FOREIGN_WALLET_GHOST_RESOLUTION),
    ).fetchone()
    if existing is not None:
        return
    logger.warning(
        "foreign_wallet_order: venue order %s on market %s is outside Zeus's "
        "domain (operator manual activity on the shared wallet); recorded for "
        "audit, excluded from the reconcile kill switch",
        order_id,
        raw.get("market"),
    )
    finding = record_finding(
        conn,
        kind="exchange_ghost_order",
        subject_id=order_id,
        context=context,
        evidence={
            "exchange_order": dict(raw),
            "reason": "exchange_open_order_absent_from_venue_commands",
            "classification": "foreign_wallet_order",
        },
        recorded_at=observed_at,
    )
    resolve_finding(
        conn,
        finding.finding_id,
        resolution=_FOREIGN_WALLET_GHOST_RESOLUTION,
        resolved_by="src.execution.exchange_reconcile",
        resolved_at=observed_at,
    )


def _resolve_foreign_wallet_ghost_findings(
    conn: sqlite3.Connection,
    *,
    observed_at: datetime,
) -> int:
    """Resolve pre-existing unresolved ghost findings that are foreign wallet orders.

    Migration pass for findings recorded before the foreign-wallet
    classification existed (the 2026-06-09 kill-switch incident rows).
    """

    rows = conn.execute(
        """
        SELECT finding_id, evidence_json
          FROM exchange_reconcile_findings
         WHERE kind = 'exchange_ghost_order'
           AND resolved_at IS NULL
        """
    ).fetchall()
    resolved = 0
    for row in rows:
        try:
            evidence = json.loads(row["evidence_json"])
        except (TypeError, ValueError):
            continue
        raw = evidence.get("exchange_order") or {}
        if not isinstance(raw, Mapping):
            continue
        if not _is_foreign_wallet_resting_order(conn, raw):
            continue
        logger.warning(
            "foreign_wallet_order: resolving pre-classification ghost finding %s "
            "(market %s outside Zeus's domain, zero matched size)",
            row["finding_id"],
            raw.get("market"),
        )
        resolve_finding(
            conn,
            str(row["finding_id"]),
            resolution=_FOREIGN_WALLET_GHOST_RESOLUTION,
            resolved_by="src.execution.exchange_reconcile",
            resolved_at=observed_at,
        )
        resolved += 1
    return resolved


# An operator-acknowledged ghost is an in-Zeus-domain resting order the operator
# manually placed on the SHARED proxy wallet and explicitly declared (2026-06-10:
# the Milan-high manual unwind). Unlike a foreign-wallet order, this market IS in
# Zeus's domain, so the foreign-wallet classifier correctly does not apply. The
# acknowledgment is honored ONLY while the order stays UNFILLED (size_matched == 0):
# any fill on the shared wallet is never auto-suppressed — mirror the strictness of
# the foreign-wallet matched-size tripwire (credential-compromise / unexpected-fill
# kill switch stays armed).
_OPERATOR_ACK_GHOST_RESOLUTION = "operator_acknowledged_ghost_order_rollforward"
_OPERATOR_ACK_RESOLVED_BY = "session_operator_confirmed"
_OPERATOR_ACK_RESOLUTION_PREFIX = "operator_manual"


def _has_operator_acknowledgment(conn: sqlite3.Connection, order_id: str) -> bool:
    """Whether an operator has explicitly acknowledged this ghost subject.

    The acknowledgment is a pre-existing RESOLVED finding for the same subject_id
    whose resolution marks operator action: either resolved_by the operator-session
    marker, or a resolution text with the ``operator_manual`` prefix (the manually
    resolved row's shape), or the rollforward marker this antibody itself writes.
    Fail-closed: no acknowledgment row => not acknowledged => strict ghost path.
    """

    if not order_id:
        return False
    row = conn.execute(
        """
        SELECT 1
          FROM exchange_reconcile_findings
         WHERE kind = 'exchange_ghost_order'
           AND subject_id = ?
           AND resolved_at IS NOT NULL
           AND (
                resolved_by = ?
             OR resolution LIKE ? || '%'
             OR resolution = ?
           )
         LIMIT 1
        """,
        (
            order_id,
            _OPERATOR_ACK_RESOLVED_BY,
            _OPERATOR_ACK_RESOLUTION_PREFIX,
            _OPERATOR_ACK_GHOST_RESOLUTION,
        ),
    ).fetchone()
    return row is not None


def _is_operator_acknowledged_resting_order(
    conn: sqlite3.Connection, order_id: str, raw: Mapping[str, Any]
) -> bool:
    """An in-domain ghost the operator acknowledged AND that is still unfilled.

    Strictness mirrors the foreign-wallet rules: any matched size on the CURRENT
    exchange order voids the acknowledgment (a fill on the shared wallet is never
    auto-suppressed). An unparseable matched size is treated as non-zero by
    ``_order_matched_size`` and therefore also voids suppression — stay fail-closed.
    """

    if _order_matched_size(raw) != 0:
        return False
    return _has_operator_acknowledgment(conn, order_id)


def _record_operator_acknowledged_ghost(
    conn: sqlite3.Connection,
    *,
    order_id: str,
    raw: Mapping[str, Any],
    context: ReconcileContext,
    observed_at: datetime,
) -> None:
    """Record-and-immediately-resolve an operator-acknowledged in-domain ghost.

    Mirrors ``_record_foreign_wallet_ghost``: dedup against an existing
    rollforward-resolved row so repeated sweeps do not churn duplicate audit rows,
    then record one audit finding and resolve it in the same sweep. The
    record-and-resolve shape keeps the M5 ws-gap "zero unresolved findings"
    arithmetic and the governor unresolved-finding count both clean (the resolved
    row is excluded from the returned ``findings`` list AND from
    ``list_unresolved_findings``).
    """

    existing = conn.execute(
        """
        SELECT 1 FROM exchange_reconcile_findings
         WHERE kind = 'exchange_ghost_order'
           AND subject_id = ?
           AND resolution = ?
         LIMIT 1
        """,
        (order_id, _OPERATOR_ACK_GHOST_RESOLUTION),
    ).fetchone()
    if existing is not None:
        return
    logger.warning(
        "operator_acknowledged_ghost_order: venue order %s on Zeus-domain market %s "
        "is an operator-acknowledged unfilled resting order on the shared wallet "
        "(size_matched=0); recorded for audit, excluded from the reconcile kill "
        "switch until it fills",
        order_id,
        raw.get("market"),
    )
    finding = record_finding(
        conn,
        kind="exchange_ghost_order",
        subject_id=order_id,
        context=context,
        evidence={
            "exchange_order": dict(raw),
            "reason": "exchange_open_order_absent_from_venue_commands",
            "classification": "operator_acknowledged_ghost_order",
        },
        recorded_at=observed_at,
    )
    resolve_finding(
        conn,
        finding.finding_id,
        resolution=_OPERATOR_ACK_GHOST_RESOLUTION,
        resolved_by="src.execution.exchange_reconcile",
        resolved_at=observed_at,
    )


def _resolve_operator_acknowledged_ghost_findings(
    conn: sqlite3.Connection,
    *,
    observed_at: datetime,
) -> int:
    """Resolve pre-existing unresolved ghost findings the operator acknowledged.

    Migration / re-record pass: a re-recorded unresolved ghost row for an
    operator-acknowledged subject (the whack-a-mole row the live sweep produced
    after the manual resolution) is resolved from local evidence alone (no venue
    read), so the 1-minute refresh and the next sweep both clear it. Only honored
    while the recorded evidence shows the order still unfilled (size_matched == 0).
    """

    rows = conn.execute(
        """
        SELECT finding_id, subject_id, evidence_json
          FROM exchange_reconcile_findings
         WHERE kind = 'exchange_ghost_order'
           AND resolved_at IS NULL
        """
    ).fetchall()
    resolved = 0
    for row in rows:
        try:
            evidence = json.loads(row["evidence_json"])
        except (TypeError, ValueError):
            continue
        raw = evidence.get("exchange_order") or {}
        if not isinstance(raw, Mapping):
            continue
        if not _is_operator_acknowledged_resting_order(conn, str(row["subject_id"]), raw):
            continue
        logger.warning(
            "operator_acknowledged_ghost_order: resolving re-recorded ghost finding "
            "%s (subject %s acknowledged by operator, zero matched size)",
            row["finding_id"],
            row["subject_id"],
        )
        resolve_finding(
            conn,
            str(row["finding_id"]),
            resolution=_OPERATOR_ACK_GHOST_RESOLUTION,
            resolved_by="src.execution.exchange_reconcile",
            resolved_at=observed_at,
        )
        resolved += 1
    return resolved


def _recover_live_ghost_sell_order_for_known_position(
    conn: sqlite3.Connection,
    raw: Mapping[str, Any],
    *,
    exchange_positions: Mapping[str, Decimal],
    observed_at: datetime,
) -> dict[str, Any] | None:
    """Reconstruct a missing EXIT command for a live reducing SELL order.

    This is not a generic ghost-order suppressor. It only fires when the venue
    order is a live SELL for a token Zeus already owns, has positive matched
    size, and the live positions surface proves conservation:

        current_exchange_position + matched_sell_size == known_position_shares

    If any predicate is absent or contradictory, the caller records the normal
    ``exchange_ghost_order`` finding and the submit latch stays closed.
    """

    order_id = _order_id(raw)
    if not order_id:
        return None
    side = str(_first_present(raw, "side", default="")).upper()
    if side != "SELL":
        return None
    token_id = str(
        _first_present(raw, "asset_id", "asset", "token_id", "tokenId", default="")
        or ""
    ).strip()
    if not token_id:
        return None
    matched_size = _order_matched_size(raw)
    if matched_size <= Decimal("0"):
        return None
    exchange_size = exchange_positions.get(token_id)
    if exchange_size is None:
        return None
    original_size = _positive_decimal_or_none(
        _first_present(raw, "original_size", "size", default=None)
    )
    price = _positive_decimal_or_none(_first_present(raw, "price", default=None))
    if original_size is None or price is None:
        return None

    position = _known_position_for_reducing_ghost_sell(
        conn,
        token_id=token_id,
        exchange_size=exchange_size,
        matched_size=matched_size,
    )
    if position is None:
        return None
    position_map = dict(position)
    entry = _entry_command_for_reducing_ghost_sell(
        conn,
        token_id=token_id,
        position_id=str(position_map["position_id"]),
    )
    if entry is None:
        return None

    command_id = "recovered_exit:" + sha256(order_id.encode()).hexdigest()[:24]
    existing = conn.execute(
        "SELECT * FROM venue_commands WHERE command_id = ? OR venue_order_id = ? LIMIT 1",
        (command_id, order_id),
    ).fetchone()
    if existing is not None:
        return dict(existing)

    observed_text = observed_at.isoformat()
    decision_id = str(entry["decision_id"] or f"m5_recovered_exit:{order_id}")
    recovery_payload = {
        "schema_version": 1,
        "reason": "m5_live_ghost_sell_order_recovered_for_known_position",
        "source_module": "src.execution.exchange_reconcile",
        "venue_order_id": order_id,
        "token_id": token_id,
        "position_id": position_map["position_id"],
        "exchange_position_size": _decimal_text(exchange_size),
        "matched_sell_size": _decimal_text(matched_size),
        "known_position_shares": _decimal_text(_position_shares_for_recovery(position_map)),
        "source_entry_command_id": entry["command_id"],
    }
    conn.execute(
        """
        INSERT INTO venue_commands (
            command_id, snapshot_id, envelope_id, position_id, decision_id,
            idempotency_key, intent_kind, market_id, token_id, side, size, price,
            venue_order_id, state, created_at, updated_at, review_required_reason
        ) VALUES (?, ?, ?, ?, ?, ?, 'EXIT', ?, ?, 'SELL', ?, ?, ?, 'INTENT_CREATED', ?, ?, ?)
        """,
        (
            command_id,
            str(entry["snapshot_id"]),
            str(entry["envelope_id"]),
            str(position_map["position_id"]),
            decision_id,
            command_id,
            str(entry["market_id"] or raw.get("market") or token_id),
            token_id,
            float(original_size),
            float(price),
            order_id,
            observed_text,
            observed_text,
            "m5_live_ghost_sell_recovery",
        ),
    )
    conn.execute(
        """
        INSERT INTO venue_command_events (
            event_id, command_id, sequence_no, event_type, occurred_at, payload_json, state_after
        ) VALUES (?, ?, 1, 'INTENT_CREATED', ?, ?, 'INTENT_CREATED')
        """,
        (
            uuid.uuid4().hex[:16],
            command_id,
            observed_text,
            json.dumps(recovery_payload, sort_keys=True),
        ),
    )

    from src.state.venue_command_repo import append_event, append_order_fact

    append_event(
        conn,
        command_id=command_id,
        event_type="SUBMIT_REQUESTED",
        occurred_at=observed_text,
        payload=recovery_payload,
    )
    append_event(
        conn,
        command_id=command_id,
        event_type="SUBMIT_ACKED",
        occurred_at=observed_text,
        payload={"venue_order_id": order_id, **recovery_payload},
    )
    remaining_size = max(original_size - matched_size, Decimal("0"))
    append_order_fact(
        conn,
        venue_order_id=order_id,
        command_id=command_id,
        state="PARTIALLY_MATCHED",
        remaining_size=_decimal_text(remaining_size),
        matched_size=_decimal_text(matched_size),
        source="REST",
        observed_at=observed_at,
        venue_timestamp=observed_at,
        raw_payload_hash=_hash_payload({"exchange_order": dict(raw), **recovery_payload}),
        raw_payload_json={"exchange_order": dict(raw), **recovery_payload},
    )
    append_event(
        conn,
        command_id=command_id,
        event_type="PARTIAL_FILL_OBSERVED",
        occurred_at=observed_text,
        payload={
            "venue_order_id": order_id,
            "matched_size": _decimal_text(matched_size),
            "remaining_size": _decimal_text(remaining_size),
            **recovery_payload,
        },
    )
    _restore_position_to_pending_exit_for_recovered_sell(
        conn,
        position=position_map,
        venue_order_id=order_id,
        token_id=token_id,
        exchange_size=exchange_size,
        matched_size=matched_size,
        fill_price=price,
        observed_at=observed_at,
        command_id=command_id,
    )
    _resolve_open_ghost_order_findings_for_recovered_exit(
        conn,
        order_id=order_id,
        observed_at=observed_at,
    )
    logger.warning(
        "m5_recovered_live_ghost_sell_order: order=%s token=%s position=%s matched=%s exchange_size=%s",
        order_id,
        token_id,
        position_map["position_id"],
        matched_size,
        exchange_size,
    )
    row = conn.execute(
        "SELECT * FROM venue_commands WHERE command_id = ?",
        (command_id,),
    ).fetchone()
    return dict(row) if row is not None else None


def _resolve_open_ghost_order_findings_for_recovered_exit(
    conn: sqlite3.Connection,
    *,
    order_id: str,
    observed_at: datetime,
) -> int:
    rows = conn.execute(
        """
        SELECT finding_id
          FROM exchange_reconcile_findings
         WHERE kind = 'exchange_ghost_order'
           AND subject_id = ?
           AND resolved_at IS NULL
        """,
        (order_id,),
    ).fetchall()
    for row in rows:
        resolve_finding(
            conn,
            str(row["finding_id"]),
            resolution="exchange_ghost_order_recovered_as_exit_command",
            resolved_by="src.execution.exchange_reconcile",
            resolved_at=observed_at,
        )
    return len(rows)


def _position_shares_for_recovery(position: Mapping[str, Any]) -> Decimal:
    for key in ("chain_shares", "shares"):
        value = _positive_decimal_or_none(position.get(key))
        if value is not None:
            return value
    return Decimal("0")


def _known_position_for_reducing_ghost_sell(
    conn: sqlite3.Connection,
    *,
    token_id: str,
    exchange_size: Decimal,
    matched_size: Decimal,
) -> sqlite3.Row | None:
    if not _table_exists(conn, "position_current"):
        return None
    rows = conn.execute(
        """
        SELECT *
          FROM position_current
         WHERE (token_id = ? OR no_token_id = ?)
           AND phase IN ('active', 'day0_window', 'pending_exit', 'voided')
           AND COALESCE(shares, 0) > 0
         ORDER BY
           CASE phase
             WHEN 'pending_exit' THEN 0
             WHEN 'day0_window' THEN 1
             WHEN 'active' THEN 2
             WHEN 'voided' THEN 3
             ELSE 9
           END,
           updated_at DESC
        """,
        (token_id, token_id),
    ).fetchall()
    for row in rows:
        shares = _position_shares_for_recovery(dict(row))
        if shares <= Decimal("0"):
            continue
        if _position_size_matches(exchange_size + matched_size, shares):
            return row
    return None


def _entry_command_for_reducing_ghost_sell(
    conn: sqlite3.Connection,
    *,
    token_id: str,
    position_id: str,
) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT c.*
          FROM venue_commands c
         WHERE c.token_id = ?
           AND c.position_id = ?
           AND UPPER(COALESCE(c.intent_kind, '')) = 'ENTRY'
           AND UPPER(COALESCE(c.side, '')) = 'BUY'
           AND EXISTS (
                SELECT 1
                  FROM venue_trade_facts tf
                 WHERE tf.command_id = c.command_id
                   AND tf.state IN ('MATCHED', 'MINED', 'CONFIRMED')
                   AND CAST(COALESCE(tf.filled_size, '0') AS REAL) > 0
           )
         ORDER BY c.created_at DESC
         LIMIT 1
        """,
        (token_id, position_id),
    ).fetchone()


def _restore_position_to_pending_exit_for_recovered_sell(
    conn: sqlite3.Connection,
    *,
    position: Mapping[str, Any],
    venue_order_id: str,
    token_id: str,
    exchange_size: Decimal,
    matched_size: Decimal,
    fill_price: Decimal,
    observed_at: datetime,
    command_id: str,
) -> None:
    position_id = str(position["position_id"])
    phase_before = str(position.get("phase") or "")
    entry_price = _positive_decimal_or_none(position.get("entry_price")) or Decimal("0")
    remaining_cost_basis = exchange_size * entry_price
    realized_pnl = matched_size * (fill_price - entry_price)
    observed_text = observed_at.isoformat()
    conn.execute(
        """
        UPDATE position_current
           SET phase = 'pending_exit',
               shares = ?,
               chain_shares = ?,
               cost_basis_usd = ?,
               realized_pnl_usd = COALESCE(realized_pnl_usd, 0) + ?,
               exit_price = ?,
               exit_reason = ?,
               order_id = ?,
               order_status = 'sell_pending_confirmation',
               chain_state = 'synced',
               updated_at = ?
         WHERE position_id = ?
        """,
        (
            float(exchange_size),
            float(exchange_size),
            float(remaining_cost_basis),
            float(realized_pnl),
            float(fill_price),
            "M5_LIVE_GHOST_SELL_RECOVERY",
            venue_order_id,
            observed_text,
            position_id,
        ),
    )
    seq_row = conn.execute(
        "SELECT COALESCE(MAX(sequence_no), 0) + 1 FROM position_events WHERE position_id = ?",
        (position_id,),
    ).fetchone()
    sequence_no = int((seq_row[0] if seq_row else 1) or 1)
    payload = {
        "schema_version": 1,
        "reason": "m5_live_ghost_sell_order_recovered_for_known_position",
        "token_id": token_id,
        "venue_order_id": venue_order_id,
        "command_id": command_id,
        "exchange_position_size": _decimal_text(exchange_size),
        "matched_sell_size": _decimal_text(matched_size),
        "fill_price": _decimal_text(fill_price),
        "phase_before": phase_before,
        "phase_after": "pending_exit",
        "source_module": "src.execution.exchange_reconcile",
    }
    conn.execute(
        """
        INSERT INTO position_events (
            event_id, position_id, event_version, sequence_no, event_type,
            occurred_at, phase_before, phase_after, strategy_key, decision_id,
            snapshot_id, order_id, command_id, caused_by, idempotency_key,
            venue_status, source_module, payload_json, env
        ) VALUES (?, ?, 1, ?, 'EXIT_INTENT', ?, ?, 'pending_exit', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            f"{position_id}:m5_recovered_exit:{sequence_no}",
            position_id,
            sequence_no,
            observed_text,
            phase_before,
            str(position.get("strategy_key") or "opening_inertia"),
            str(position.get("decision_id") or ""),
            str(position.get("decision_snapshot_id") or ""),
            venue_order_id,
            command_id,
            "m5_live_ghost_sell_recovery",
            f"{position_id}:m5_recovered_exit:{sequence_no}",
            "sell_pending_confirmation",
            "src.execution.exchange_reconcile",
            json.dumps(payload, sort_keys=True),
            str(position.get("env") or "live"),
        ),
    )


# ---- Operator external-close absorption (the variant-3 antibody) ----------------------
#
# 2026-06-10 incident chain: the operator manually SOLD Zeus's Milan position on the
# shared proxy wallet. While the order rested -> ghost suppression (works). When it
# FILLED -> position_drift (correct). chain_sync then VOIDED the position, but the void
# created a "terminal_position_current_chain_holdings" entry (66.25) WITHOUT consuming the
# journal buy-claim (66.25) with an offsetting sell fact. The drift detector's
# expected_wallet then DOUBLE-COUNTS the same 66.25 economic claim (journal 66.25 +
# closed-holdings 66.25 = 132.50) vs exchange 0 -> position_drift re-records forever.
#
# K=1 mechanism (make the CATEGORY impossible, not the instance): when a position's
# tokens leave the wallet via an OPERATOR-CONFIRMED external fill, converge the books by
#   (a) booking the external close as an exit FACT (a SELL venue_trade_fact, size = the
#       journal's net long, price = the operator's documented limit, price_basis=
#       operator_limit) that CONSUMES the journal buy-claim -> journal nets to 0; and
#   (b) tagging the dangling terminal position's chain_state EXTERNAL_OPERATOR_CLOSED so
#       the closed-position-holdings view (which assumes tokens are still on-chain) no
#       longer contributes that 66.25 -> single-count.
# After absorption expected_wallet == 0 == exchange -> no finding on re-sweep.
#
# STRICTNESS (mirrors the operator-acknowledged-ghost antibody): absorption requires an
# operator-acknowledged RESOLUTION row for the SAME subject token (resolved_by LIKE
# 'session_operator_confirmed%' OR resolution LIKE 'operator_manual%'). Never automatic
# for unexplained drifts — an unacknowledged drift stays fail-closed and arms the latch.
_OPERATOR_EXTERNAL_CLOSE_RESOLUTION = "position_drift_operator_external_close_absorbed"
_OPERATOR_EXTERNAL_CLOSE_PRICE_BASIS = "operator_limit"
_OPERATOR_ACK_DRIFT_RESOLVED_BY_PREFIX = "session_operator_confirmed"
_OPERATOR_ACK_DRIFT_RESOLUTION_PREFIX = "operator_manual"


def _operator_acknowledged_drift_resolution(
    conn: sqlite3.Connection, token_id: str
) -> Mapping[str, Any] | None:
    """The operator-acknowledged drift resolution row for ``token_id``, if any.

    Fail-closed: a token is eligible for external-close absorption ONLY when the
    operator has explicitly acknowledged THIS subject — a prior RESOLVED position_drift
    finding whose ``resolved_by`` starts with the operator-session marker or whose
    ``resolution`` carries the ``operator_manual`` prefix. No such row => not eligible =>
    strict drift path. The stopgap auto-resolver's marker
    (``session_operator_confirmed_stopgap``) matches the prefix, which is intentional:
    those rows attest the same operator-confirmed external close.
    """

    if not token_id:
        return None
    row = conn.execute(
        """
        SELECT finding_id, resolution, resolved_by, evidence_json
          FROM exchange_reconcile_findings
         WHERE kind = 'position_drift'
           AND subject_id = ?
           AND resolved_at IS NOT NULL
           AND (
                resolved_by LIKE ? || '%'
             OR resolution LIKE ? || '%'
           )
         ORDER BY resolved_at ASC
         LIMIT 1
        """,
        (
            token_id,
            _OPERATOR_ACK_DRIFT_RESOLVED_BY_PREFIX,
            _OPERATOR_ACK_DRIFT_RESOLUTION_PREFIX,
        ),
    ).fetchone()
    return dict(row) if row is not None else None


def _operator_external_close_price(
    conn: sqlite3.Connection, token_id: str, ack_row: Mapping[str, Any] | None
) -> Decimal:
    """The price to book the external close at (price_basis=operator_limit).

    Authority order: the operator's documented limit on the open ENTRY command for this
    token (the position's own price), else a positive price parsed from the
    acknowledged-order evidence, else the conservative 0 (proceeds unknown — the size
    consumes the journal regardless; price only feeds realized economics, never the
    wallet-size reconciliation that drives the latch).
    """

    row = conn.execute(
        """
        SELECT price
          FROM venue_commands
         WHERE token_id = ?
           AND price IS NOT NULL
         ORDER BY created_at DESC
         LIMIT 1
        """,
        (token_id,),
    ).fetchone()
    if row is not None:
        price = _positive_decimal_or_none(row["price"])
        if price is not None:
            return price
    if ack_row is not None:
        evidence = _json_mapping(ack_row.get("evidence_json"))
        order = evidence.get("exchange_order")
        if isinstance(order, Mapping):
            price = _positive_decimal_or_none(order.get("price"))
            if price is not None:
                return price
    return Decimal("0")


def _absorb_operator_external_close(
    conn: sqlite3.Connection,
    *,
    token_id: str,
    exchange_size: Decimal,
    confirmed_size: Decimal,
    closed_position_size: Decimal,
    observed_at: datetime,
) -> bool:
    """Converge the books for an operator-confirmed external close. K=1.

    Returns True iff this token was a double-count external-close drift (operator-
    acknowledged, exchange below expected, a positive journal long and/or a dangling
    voided-position holding) and the absorption booked the offsetting state. Idempotent:
    once booked the journal nets to 0 and the holdings are untagged, so the drift
    condition no longer triggers and re-sweep does not re-absorb.
    """

    ack_row = _operator_acknowledged_drift_resolution(conn, token_id)
    if ack_row is None:
        return False
    # The external-close shape: the operator removed the tokens, so the exchange wallet
    # is BELOW the journal-confirmed long. A drift where the exchange holds MORE than the
    # journal is a different disease (unrecorded acquisition) and is never absorbed here.
    journal_long = _nonnegative_wallet_size(confirmed_size)
    if journal_long <= Decimal("0") and closed_position_size <= Decimal("0"):
        return False
    if exchange_size >= journal_long:
        return False

    booked = False
    # (a) Book the external close as a SELL exit fact consuming the journal buy-claim.
    if journal_long > Decimal("0"):
        booked = _book_external_operator_close_exit_fact(
            conn,
            token_id=token_id,
            close_size=journal_long,
            close_price=_operator_external_close_price(conn, token_id, ack_row),
            observed_at=observed_at,
        ) or booked
    # (b) Untag the dangling terminal-position chain holdings so they stop double-counting.
    if closed_position_size > Decimal("0"):
        booked = _tag_external_operator_closed_position_holdings(
            conn, token_id=token_id, observed_at=observed_at
        ) or booked
    return booked


def _book_external_operator_close_exit_fact(
    conn: sqlite3.Connection,
    *,
    token_id: str,
    close_size: Decimal,
    close_price: Decimal,
    observed_at: datetime,
) -> bool:
    """Append a synthetic SELL exit trade fact that consumes the journal expectation.

    The fact is keyed to a synthetic EXIT/SELL command (reusing the open ENTRY command's
    snapshot/envelope provenance FKs) so ``_journal_positions_by_token`` nets the buy
    claim to zero. Append-only: never rewrites the original buy fact. Idempotent on the
    deterministic command_id / trade_id, so a re-sweep does not double-book.
    """

    from src.state.venue_command_repo import append_trade_fact

    entry = conn.execute(
        """
        SELECT command_id, snapshot_id, envelope_id, position_id, decision_id,
               market_id, venue_order_id, created_at
          FROM venue_commands
         WHERE token_id = ?
           AND UPPER(COALESCE(intent_kind, '')) = 'ENTRY'
           AND UPPER(COALESCE(side, '')) = 'BUY'
         ORDER BY created_at DESC
         LIMIT 1
        """,
        (token_id,),
    ).fetchone()
    if entry is None:
        return False
    command_id = "external_operator_close:" + sha256(token_id.encode()).hexdigest()[:24]
    trade_id = "external_operator_close_fact:" + sha256(token_id.encode()).hexdigest()[:24]
    existing = conn.execute(
        "SELECT 1 FROM venue_trade_facts WHERE trade_id = ? LIMIT 1",
        (trade_id,),
    ).fetchone()
    if existing is not None:
        return False
    # Insert the synthetic EXIT/SELL command via a DIRECT write, NOT insert_command():
    # insert_command is the U1 pre-side-effect SUBMISSION gate (snapshot freshness, tick
    # alignment, price-in-(0,1)) — entirely inappropriate for a reconciliation correction
    # and would CRASH the sweep on a long-stale snapshot. This row is journal-only truth
    # (a SELL side for _journal_positions_by_token to net the buy claim), never submitted.
    # It reuses the entry command's valid snapshot/envelope FKs.
    if conn.execute(
        "SELECT 1 FROM venue_commands WHERE command_id = ? LIMIT 1", (command_id,)
    ).fetchone() is None:
        conn.execute(
            """
            INSERT INTO venue_commands (
                command_id, snapshot_id, envelope_id, position_id, decision_id,
                idempotency_key, intent_kind, market_id, token_id, side, size, price,
                venue_order_id, state, created_at, updated_at, review_required_reason
            ) VALUES (?, ?, ?, ?, ?, ?, 'EXIT', ?, ?, 'SELL', ?, ?, ?, 'FILLED', ?, ?, ?)
            """,
            (
                command_id,
                str(entry["snapshot_id"]),
                str(entry["envelope_id"]),
                str(entry["position_id"] or ""),
                str(entry["decision_id"] or f"dec-{command_id}"),
                command_id,
                str(entry["market_id"] or token_id),
                token_id,
                float(close_size),
                float(close_price),
                str(entry["venue_order_id"] or "") or None,
                observed_at.isoformat(),
                observed_at.isoformat(),
                "operator_external_close_absorption",
            ),
        )
    payload = {
        "schema_version": 1,
        "reason": "operator_external_close_absorption",
        "source_module": "src.execution.exchange_reconcile",
        "token_id": token_id,
        "close_size": str(close_size),
        "close_price": str(close_price),
        "price_basis": _OPERATOR_EXTERNAL_CLOSE_PRICE_BASIS,
        "classification": "external_operator_close",
        "source_entry_command_id": entry["command_id"],
    }
    append_trade_fact(
        conn,
        trade_id=trade_id,
        venue_order_id=str(entry["venue_order_id"] or ""),
        command_id=command_id,
        state="CONFIRMED",
        filled_size=str(close_size),
        fill_price=str(close_price),
        source="OPERATOR",
        observed_at=observed_at,
        venue_timestamp=observed_at,
        raw_payload_hash=_hash_payload(payload),
        raw_payload_json=payload,
    )
    logger.warning(
        "operator_external_close: booked external close exit fact for token %s "
        "(size=%s price=%s price_basis=%s) consuming the journal buy-claim",
        token_id,
        close_size,
        close_price,
        _OPERATOR_EXTERNAL_CLOSE_PRICE_BASIS,
    )
    return True


def _tag_external_operator_closed_position_holdings(
    conn: sqlite3.Connection,
    *,
    token_id: str,
    observed_at: datetime,
) -> bool:
    """Tag terminal positions holding ``token_id`` as externally closed (single-count).

    The void misbooking left a terminal position with chain_state in
    _CLOSED_POSITION_WALLET_HOLDING_CHAIN_STATES, which the closed-position-holdings view
    reads as an on-chain expected wallet holding. After an operator external close the
    tokens are GONE, so the chain reality tag is corrected to
    EXTERNAL_OPERATOR_CLOSED — DELIBERATELY outside the holdings set. The historical
    ``shares`` record is preserved. Returns True iff any row was corrected.
    """

    if not _table_exists(conn, "position_current"):
        return False
    phase_placeholders = ", ".join("?" for _ in _CLOSED_POSITION_WALLET_HOLDING_PHASES)
    chain_placeholders = ", ".join("?" for _ in _CLOSED_POSITION_WALLET_HOLDING_CHAIN_STATES)
    cursor = conn.execute(
        f"""
        UPDATE position_current
           SET chain_state = ?,
               chain_shares = 0,
               updated_at = ?
         WHERE (token_id = ? OR no_token_id = ?)
           AND phase IN ({phase_placeholders})
           AND chain_state IN ({chain_placeholders})
        """,
        (
            _EXTERNAL_OPERATOR_CLOSED_CHAIN_STATE,
            observed_at.isoformat(),
            token_id,
            token_id,
            *tuple(sorted(_CLOSED_POSITION_WALLET_HOLDING_PHASES)),
            *tuple(sorted(_CLOSED_POSITION_WALLET_HOLDING_CHAIN_STATES)),
        ),
    )
    if cursor.rowcount > 0:
        logger.warning(
            "operator_external_close: tagged %d terminal position(s) for token %s "
            "chain_state=%s (tokens left wallet via operator external close; "
            "removed from expected-wallet closed-holdings to stop the double-count)",
            cursor.rowcount,
            token_id,
            _EXTERNAL_OPERATOR_CLOSED_CHAIN_STATE,
        )
        return True
    return False


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
        "WITH " + _canonical_trade_fact_cte() + """
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
          FROM canonical_trade_fact tf
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
        "WITH " + _canonical_trade_fact_cte() + """
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
          FROM canonical_trade_fact tf
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
    open_orders: list[Any] | None = None,
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
    closed_position_holdings = _closed_position_token_holdings_by_token(conn)
    chain_confirmed_active_holdings = _chain_confirmed_active_holdings_by_token(conn)
    open_sell_locked = _live_open_sell_locked_tokens_by_token(conn, open_orders=open_orders)
    tokens = sorted(
        set(exchange)
        | set(confirmed_journal)
        | set(settlement_holdings)
        | set(closed_position_holdings)
        | set(open_sell_locked)
    )
    findings: list[ReconcileFinding] = []
    for token in tokens:
        # ONE-TRUTH (rule 4): a token already in the suppression registry (chain-only / settled)
        # is not a system open-position drift. Resolve any open finding and never gate the latch.
        if _token_is_suppressed_external(conn, token):
            _resolve_open_position_drift_findings(
                conn,
                token,
                resolution="position_drift_token_suppressed_external",
                resolved_at=observed_at,
            )
            continue
        # CHAIN-CONFIRMED ACTIVE HOLDING (2026-06-16 ws_gap journal-gap antibody): see
        # _chain_confirmed_active_holdings_by_token. A ws_gap-era fill confirmed ONLY on-chain
        # (chain_state='synced') but never journaled leaves the exchange position unexplained
        # by the confirmed-trade-facts journal, re-recording this drift every sweep and latching
        # submit closed forever. Both sides are the data-api /positions surface (the persisted
        # chain-reconciler snapshot vs the FRESH exchange read), not two oracles — but a real
        # reduction/loss surfaces FIRST in the fresh read, so equality means the position is
        # still present at its last chain-confirmed size → not a drift (a loss breaks the match).
        _chain_confirmed_size = chain_confirmed_active_holdings.get(token, Decimal("0"))
        if _chain_confirmed_size > Decimal("0") and _position_size_matches(
            exchange.get(token, Decimal("0")), _chain_confirmed_size
        ):
            _resolve_open_position_drift_findings(
                conn,
                token,
                resolution="position_drift_chain_confirmed_active_holding",
                resolved_at=observed_at,
            )
            continue
        exchange_size = exchange.get(token, Decimal("0"))
        confirmed_size = confirmed_journal.get(token, Decimal("0"))
        confirmed_wallet_size = _nonnegative_wallet_size(confirmed_size)
        open_sell_locked_size = open_sell_locked.get(token, Decimal("0"))
        available_wallet_size = _nonnegative_wallet_size(confirmed_wallet_size - open_sell_locked_size)
        optimistic_size = optimistic_journal.get(token, Decimal("0"))
        settlement_size = settlement_holdings.get(token, Decimal("0"))
        closed_position_size = (
            Decimal("0") if settlement_size > Decimal("0") else closed_position_holdings.get(token, Decimal("0"))
        )
        expected_wallet_size = available_wallet_size + settlement_size + closed_position_size
        if _position_size_matches(exchange_size, available_wallet_size):
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
        if closed_position_size > Decimal("0") and _position_size_matches(
            exchange_size,
            expected_wallet_size,
        ):
            _resolve_open_position_drift_findings(
                conn,
                token,
                resolution="position_drift_closed_position_token_holding",
                resolved_at=observed_at,
            )
            continue
        if _position_size_hidden_by_visibility_floor(exchange_size, confirmed_wallet_size):
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
        if closed_position_size > Decimal("0") and _position_size_hidden_by_visibility_floor(
            exchange_size,
            expected_wallet_size,
        ):
            _resolve_open_position_drift_findings(
                conn,
                token,
                resolution="position_drift_closed_position_visibility_floor",
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
        # Variant-3 antibody: an operator-confirmed EXTERNAL close (the operator manually
        # sold Zeus's tokens off the shared wallet) converges the books here instead of
        # re-recording the void-misbooking double-count forever. Strictly gated on an
        # operator-acknowledged resolution row for THIS subject (see
        # _operator_acknowledged_drift_resolution). Idempotent on re-sweep.
        if _absorb_operator_external_close(
            conn,
            token_id=token,
            exchange_size=exchange_size,
            confirmed_size=confirmed_size,
            closed_position_size=closed_position_size,
            observed_at=observed_at,
        ):
            _resolve_open_position_drift_findings(
                conn,
                token,
                resolution=_OPERATOR_EXTERNAL_CLOSE_RESOLUTION,
                resolved_at=observed_at,
            )
            continue
        # TERMINAL-CHAIN-CLOSED PHANTOM (2026-06-13, settled-external absorber completion):
        # the swept-winner external close is proven ON-CHAIN — venue size 0 against a
        # terminal (voided/settled/admin_closed) chain-holdings row, with no live sell lock.
        # Task #31's calendar absorber lives only on the refresh path AND is blind during the
        # window before the market's target local day is +24h past; that blind window froze
        # the Denver latch 2026-06-13. Absorb directly from the on-chain evidence here on the
        # FULL-SWEEP path so the finding is never re-recorded and the latch is never frozen.
        # A non-terminal disappearance (no terminal chain-holdings row) never matches and
        # still routes to the operator-ack path — the theft/bug surface is preserved.
        if _absorb_terminal_chain_closed_phantom(
            conn,
            token_id=token,
            exchange_size=exchange_size,
            closed_position_size=closed_position_size,
            open_sell_locked_size=open_sell_locked_size,
            observed_at=observed_at,
            settled_terminal=_day_end_terminal_evidence_for_token(conn, token, observed_at),
            confirmed_wallet_size=confirmed_wallet_size,
        ):
            _resolve_open_position_drift_findings(
                conn,
                token,
                resolution=_TERMINAL_CHAIN_CLOSED_RESOLUTION,
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
                    "confirmed_wallet_size": str(confirmed_wallet_size),
                    "open_sell_locked_size": str(open_sell_locked_size),
                    "optimistic_journal_size": str(optimistic_size),
                    "settlement_command_token_size": str(settlement_size),
                    "closed_position_token_size": str(closed_position_size),
                    "expected_wallet_size": str(expected_wallet_size),
                    "journal_evidence_class": "confirmed_trade_facts",
                    "settlement_evidence_class": "unconfirmed_redeem_settlement_commands",
                    "closed_position_evidence_class": "terminal_position_current_chain_holdings",
                    "optimistic_evidence_class": "matched_or_mined_trade_facts",
                    "reason": (
                        "exchange_position_differs_from_expected_wallet_facts"
                        if settlement_size > Decimal("0") or closed_position_size > Decimal("0")
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


_SETTLED_EXTERNAL_TERMINAL_BUFFER_HOURS = 24.0
_SETTLED_EXTERNAL_RESOLUTION = "position_drift_settled_external_suppressed"
# A swept winner whose terminal CLOSE is already proven ON-CHAIN: the position
# terminally closed locally (a position_current row with phase in
# {settled,admin_closed,voided} and chain_state in {synced,exit_pending_missing} —
# the closed-position-holdings view) AND its CTF tokens have left the wallet
# (exchange size 0). That pair is itself terminal-close proof and does NOT depend
# on the market-calendar +24h buffer, which is blind during the window between the
# external sweep and the calendar tick (the 2026-06-13 latch-freeze regression).
_TERMINAL_CHAIN_CLOSED_RESOLUTION = "position_drift_terminal_chain_closed_phantom_suppressed"


def _absorb_terminal_chain_closed_phantom(
    conn: sqlite3.Connection,
    *,
    token_id: str,
    exchange_size: Decimal,
    closed_position_size: Decimal,
    open_sell_locked_size: Decimal,
    observed_at: datetime,
    settled_terminal: Mapping[str, str] | None = None,
    confirmed_wallet_size: Decimal = Decimal("0"),
) -> bool:
    """Recognize a terminal-chain-closed swept-winner phantom from evidence in hand.

    K=1 (make the CATEGORY impossible, not the instance): the operator's standing
    third-party auto-redeemer sweeps every settled winner off the shared wallet. After
    the sweep the venue reports exchange size 0, while a terminal local position
    (phase voided/settled/admin_closed, chain_state synced/exit_pending_missing — the
    closed-position-holdings view) still asserts an expected on-chain CTF holding. That
    dangling terminal-holding double-counts against a venue-zero balance and re-records
    position_drift forever, freezing the M5 submit latch.

    The settled-external absorber (task #31, _market_calendar_terminal_evidence) recognizes
    this only via the calendar AND only once the market's target local day is >= 24h past,
    AND it lived ONLY on the 1-minute refresh path — never on the full sweep that
    run_ws_gap_reconcile_and_clear actually runs. The 2026-06-13 Denver freeze fell in both
    gaps: the full-sweep path had no settled absorber at all, so the phantom (5bbc2be2) was
    re-recorded every sweep and the latch stayed frozen.

    This absorber closes both gaps. It runs on BOTH paths, and it pairs TWO independent
    terminal signals so it stays strictly fail-closed:
      (1) ON-CHAIN terminal close: venue size 0 against a terminal (voided/settled/
          admin_closed) chain-holding row, no live sell lock — the tokens are provably gone
          from the wallet; AND
      (2) MARKET settledness: the token's market is calendar-terminal (its target local day
          has ENDED — buffer_hours=0, because signal (1) already proves the tokens left, so
          the venue-lag margin the +24h buffer guards against is redundant).

    Requiring (2) is what distinguishes a SETTLED-winner sweep (market resolved; the
    third-party redeemer claimed it) from an OPERATOR-MANUAL open-market sale (market still
    open — no calendar evidence). The latter has no ``settled_terminal`` and stays
    fail-closed on the strict operator-ack path. (See test_reconcile_operator_external_close:
    its ``condition-m5`` market is absent from the registry, so settled_terminal is None.)

    On match: register token_suppression('settled_position') with terminal-close evidence so
    the suppression door (_token_is_suppressed_external) keeps it resolved on every future
    sweep. Idempotent (the suppression door short-circuits the next sweep). Books NO synthetic
    money: settlement P&L stays with the settlement organs and the Confirm-pending-deposit
    check; only the drift/latch accounting is corrected.
    """

    # (1) On-chain terminal close.
    if exchange_size > Decimal("0"):
        return False
    if open_sell_locked_size > Decimal("0"):
        return False
    if closed_position_size <= Decimal("0"):
        return False
    # (2) Market settledness (day-end calendar evidence). Fail-closed when absent: an
    # open-market disappearance is NOT a settled sweep and stays on the operator-ack path.
    if not settled_terminal:
        return False

    from src.state.db import record_token_suppression  # noqa: PLC0415

    record_token_suppression(
        conn,
        token_id=token_id,
        suppression_reason="settled_position",
        source_module="exchange_reconcile.terminal_chain_closed_phantom_absorber",
        condition_id=(settled_terminal.get("condition_id") or None),
        evidence={
            "absorber": "terminal_chain_closed_phantom",
            "exchange_size": str(exchange_size),
            "closed_position_token_size": str(closed_position_size),
            "confirmed_wallet_size": str(confirmed_wallet_size),
            "open_sell_locked_size": str(open_sell_locked_size),
            "closed_position_evidence_class": "terminal_position_current_chain_holdings",
            "reason": "venue_zero_against_terminal_chain_holding_on_settled_market_is_external_close",
            **dict(settled_terminal),
        },
    )
    logger.warning(
        "terminal_chain_closed_phantom: token %s on settled market %s has a terminal "
        "chain-holding (%s) but venue size %s and no open sell lock — the swept-winner "
        "external close is proven on-chain on a day-ended market; registered "
        "token_suppression('settled_position') and resolving the drift finding "
        "(day-end sufficient, no +24h wait, no synthetic money booked)",
        token_id,
        settled_terminal.get("market_slug") or settled_terminal.get("condition_id") or "?",
        closed_position_size,
        exchange_size,
    )
    return True


def _condition_ids_for_tokens(
    conn: sqlite3.Connection,
    tokens: tuple[str, ...],
) -> dict[str, str]:
    """token_id -> condition_id via executable_market_snapshots (local, same conn).

    The canonical market registry stores ONE token per row (the YES side), so a NO-side
    holding can never be matched by token alone — exactly how the HK 06-09 NO x19 sweep
    stayed an unresolvable drift for 11h. The snapshot table carries both sides
    (yes/no/selected token columns); any side maps to the market's condition_id.
    Fail-soft: missing table / no rows -> empty mapping.
    """
    if not tokens or not _table_exists(conn, "executable_market_snapshots"):
        return {}
    placeholders = ", ".join("?" for _ in tokens)
    try:
        rows = conn.execute(
            f"""
            SELECT yes_token_id, no_token_id, selected_outcome_token_id, condition_id
              FROM executable_market_snapshots
             WHERE yes_token_id IN ({placeholders})
                OR no_token_id IN ({placeholders})
                OR selected_outcome_token_id IN ({placeholders})
            """,
            (*tokens, *tokens, *tokens),
        ).fetchall()
    except Exception:  # noqa: BLE001 — fail-soft: token simply stays unmapped
        return {}
    wanted = set(tokens)
    out: dict[str, str] = {}
    for row in rows:
        condition = str(row["condition_id"] or "")
        if not condition:
            continue
        for col in ("yes_token_id", "no_token_id", "selected_outcome_token_id"):
            value = str(row[col] or "")
            if value in wanted:
                out.setdefault(value, condition)
    return out


def _market_calendar_terminal_evidence(
    token_ids: tuple[str, ...] | frozenset[str] | set[str],
    *,
    observed_at: datetime,
    conditions_by_token: Mapping[str, str] | None = None,
    buffer_hours: float = _SETTLED_EXTERNAL_TERMINAL_BUFFER_HOURS,
) -> dict[str, dict[str, str]]:
    """token_id -> market-calendar terminal evidence, for tokens whose market's target
    local day ended >= ``buffer_hours`` ago (default _SETTLED_EXTERNAL_TERMINAL_BUFFER_HOURS).

    Authority: the canonical market registry (zeus-forecasts market_events: slug, city,
    target_date) + the city timezone from src.config — never a slug parse, never a venue
    call. A market this far past its question date is settled at the venue; tokens for it
    are no longer an open trading concern. Matching is by token_id OR by the token's
    condition_id (``conditions_by_token``, from executable_market_snapshots): the
    registry stores only the YES-side token per row, so NO-side holdings are reachable
    only through the condition bridge. FAIL-CLOSED: registry unreadable, token unmatched,
    or timezone unknown -> the token is simply not classified terminal (the drift finding
    stays open and the operator-ack path remains the only door).

    ``buffer_hours`` is the venue-lag safety margin the calendar absorber adds before a
    market-day-end alone is trusted as terminal. The terminal-chain-closed-phantom absorber
    passes ``buffer_hours=0``: when the on-chain terminal close is ALSO proven (venue 0 vs a
    terminal voided/settled chain-holding), the venue-lag margin is redundant — the chain has
    already proven the tokens are gone, so day-end is sufficient.

    Read-only, short-lived connection (three-phase contract: no connection outlives the
    lookup, nothing is held across any other I/O).
    """
    tokens = tuple(sorted({str(t) for t in token_ids if str(t).strip()}))
    if not tokens:
        return {}
    condition_map = {
        str(token): str(condition)
        for token, condition in (conditions_by_token or {}).items()
        if str(condition).strip()
    }
    conditions = tuple(sorted(set(condition_map.values())))
    try:
        from datetime import time as _time, timedelta as _timedelta  # noqa: PLC0415
        from zoneinfo import ZoneInfo  # noqa: PLC0415

        from src.config import cities_by_name  # noqa: PLC0415
        from src.state.db import ZEUS_FORECASTS_DB_PATH  # noqa: PLC0415

        token_ph = ", ".join("?" for _ in tokens)
        condition_ph = ", ".join("?" for _ in conditions) if conditions else "''"
        ro = sqlite3.connect(f"file:{ZEUS_FORECASTS_DB_PATH}?mode=ro", uri=True, timeout=5.0)
        try:
            ro.row_factory = sqlite3.Row
            rows = ro.execute(
                f"""
                SELECT token_id, market_slug, city, target_date, condition_id
                  FROM market_events
                 WHERE token_id IN ({token_ph})
                    OR condition_id IN ({condition_ph})
                """,
                (*tokens, *conditions),
            ).fetchall()
        finally:
            ro.close()
        evidence_by_token: dict[str, dict[str, str]] = {}
        evidence_by_condition: dict[str, dict[str, str]] = {}
        for row in rows:
            city_cfg = cities_by_name.get(str(row["city"]))
            if city_cfg is None:
                continue
            try:
                target = datetime.fromisoformat(str(row["target_date"])).date()
                tz = ZoneInfo(str(city_cfg.timezone))
            except Exception:  # noqa: BLE001 — fail-closed per token
                continue
            local_day_end = datetime.combine(target + _timedelta(days=1), _time(0, 0), tzinfo=tz)
            terminal_after = local_day_end.astimezone(timezone.utc) + _timedelta(
                hours=buffer_hours
            )
            if observed_at.astimezone(timezone.utc) < terminal_after:
                continue
            evidence = {
                "market_slug": str(row["market_slug"]),
                "city": str(row["city"]),
                "target_date": str(row["target_date"]),
                "condition_id": str(row["condition_id"] or ""),
                "terminal_after_utc": terminal_after.isoformat(),
            }
            evidence_by_token[str(row["token_id"])] = evidence
            if evidence["condition_id"]:
                evidence_by_condition[evidence["condition_id"]] = evidence
        out: dict[str, dict[str, str]] = {}
        for token in tokens:
            direct = evidence_by_token.get(token)
            if direct is not None:
                out[token] = direct
                continue
            bridged = evidence_by_condition.get(condition_map.get(token, ""))
            if bridged is not None:
                out[token] = {**bridged, "matched_via": "condition_id_bridge"}
        return out
    except Exception as exc:  # noqa: BLE001 — fail-closed: nothing classified terminal
        logger.debug("market-calendar terminal lookup unavailable (fail-closed): %s", exc)
        return {}


def _day_end_terminal_evidence_for_token(
    conn: sqlite3.Connection,
    token_id: str,
    observed_at: datetime,
) -> dict[str, str] | None:
    """Day-end (zero venue-lag buffer) market-calendar terminal evidence for one token.

    Used by the terminal-chain-closed-phantom absorber on the full-sweep path: the on-chain
    terminal close is already proven, so the market only needs to have RESOLVED (its target
    local day has ended) to confirm a settled-winner sweep rather than an open-market
    operator sale. Resolves the condition_id bridge first (NO-side holdings), then asks the
    canonical registry with buffer_hours=0. Fail-closed: returns None when the market is not
    in the registry / not yet day-ended / timezone unknown."""

    evidence = _market_calendar_terminal_evidence(
        (token_id,),
        observed_at=observed_at,
        conditions_by_token=_condition_ids_for_tokens(conn, (token_id,)),
        buffer_hours=0.0,
    )
    return evidence.get(token_id)


def _resolve_position_drift_tokens_from_current_truth(
    conn: sqlite3.Connection,
    *,
    token_ids: tuple[str, ...] | frozenset[str] | set[str],
    positions: list[Any],
    open_orders: list[Any] | None = None,
    observed_at: datetime,
) -> None:
    conditions_by_token = _condition_ids_for_tokens(
        conn, tuple(sorted(str(item) for item in token_ids))
    )
    calendar_terminal = _market_calendar_terminal_evidence(
        token_ids,
        observed_at=observed_at,
        conditions_by_token=conditions_by_token,
    )
    # Day-end (zero venue-lag buffer) variant for the terminal-chain-closed-phantom absorber:
    # the on-chain terminal close is already proven, so the market only needs to have RESOLVED
    # (target local day ended), not aged the extra +24h venue-lag margin.
    day_end_terminal = _market_calendar_terminal_evidence(
        token_ids,
        observed_at=observed_at,
        conditions_by_token=conditions_by_token,
        buffer_hours=0.0,
    )
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
    closed_position_holdings = _closed_position_token_holdings_by_token(conn)
    chain_confirmed_active_holdings = _chain_confirmed_active_holdings_by_token(conn)
    open_sell_locked = _live_open_sell_locked_tokens_by_token(conn, open_orders=open_orders)
    for token in sorted(str(item) for item in token_ids):
        # ONE-TRUTH (rule 4): honor the suppression registry — a chain-only / settled token is
        # not a system drift; resolve its finding so the latch can clear.
        if _token_is_suppressed_external(conn, token):
            _resolve_open_position_drift_findings(
                conn,
                token,
                resolution="position_drift_token_suppressed_external",
                resolved_at=observed_at,
            )
            continue
        # CHAIN-CONFIRMED ACTIVE HOLDING (2026-06-16 ws_gap journal-gap antibody): see
        # _chain_confirmed_active_holdings_by_token. The persisted chain-reconciler /positions
        # read (chain_state='synced') matched against the FRESH exchange /positions read: a real
        # loss surfaces first in the fresh read, so equality → position still present → resolve.
        _chain_confirmed_size = chain_confirmed_active_holdings.get(token, Decimal("0"))
        if _chain_confirmed_size > Decimal("0") and _position_size_matches(
            exchange.get(token, Decimal("0")), _chain_confirmed_size
        ):
            _resolve_open_position_drift_findings(
                conn,
                token,
                resolution="position_drift_chain_confirmed_active_holding",
                resolved_at=observed_at,
            )
            continue
        exchange_size = exchange.get(token, Decimal("0"))
        confirmed_size = confirmed_journal.get(token, Decimal("0"))
        confirmed_wallet_size = _nonnegative_wallet_size(confirmed_size)
        open_sell_locked_size = open_sell_locked.get(token, Decimal("0"))
        available_wallet_size = _nonnegative_wallet_size(confirmed_wallet_size - open_sell_locked_size)
        optimistic_size = optimistic_journal.get(token, Decimal("0"))
        settlement_size = settlement_holdings.get(token, Decimal("0"))
        closed_position_size = (
            Decimal("0") if settlement_size > Decimal("0") else closed_position_holdings.get(token, Decimal("0"))
        )
        expected_wallet_size = available_wallet_size + settlement_size + closed_position_size
        if _position_size_matches(exchange_size, available_wallet_size):
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
        if closed_position_size > Decimal("0") and _position_size_matches(
            exchange_size,
            expected_wallet_size,
        ):
            _resolve_open_position_drift_findings(
                conn,
                token,
                resolution="position_drift_closed_position_token_holding",
                resolved_at=observed_at,
            )
            continue
        if _position_size_hidden_by_visibility_floor(exchange_size, confirmed_wallet_size):
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
        if closed_position_size > Decimal("0") and _position_size_hidden_by_visibility_floor(
            exchange_size,
            expected_wallet_size,
        ):
            _resolve_open_position_drift_findings(
                conn,
                token,
                resolution="position_drift_closed_position_visibility_floor",
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
        # TERMINAL-CHAIN-CLOSED PHANTOM (2026-06-13): on-chain proof of the swept-winner
        # external close — venue size 0 against a terminal (voided/settled/admin_closed)
        # chain-holdings row, no live sell lock. Takes precedence over the calendar branch
        # below because the on-chain evidence is direct and immediate, closing the blind
        # window before the market's target local day is +24h past (the Denver latch freeze
        # 2026-06-13). Same money-neutral, suppression-door-idempotent contract.
        if _absorb_terminal_chain_closed_phantom(
            conn,
            token_id=token,
            exchange_size=exchange_size,
            closed_position_size=closed_position_size,
            open_sell_locked_size=open_sell_locked_size,
            observed_at=observed_at,
            settled_terminal=day_end_terminal.get(token),
            confirmed_wallet_size=confirmed_wallet_size,
        ):
            _resolve_open_position_drift_findings(
                conn,
                token,
                resolution=_TERMINAL_CHAIN_CLOSED_RESOLUTION,
                resolved_at=observed_at,
            )
            continue
        # SETTLED-CLASS EXTERNAL CLOSE (2026-06-11, redeem-abandonment follow-through):
        # the operator's standing third-party auto-redeemer sweeps EVERY settled position
        # off the shared wallet, so "venue 0 + confirmed journal long + market's target
        # local day over by 24h+" is the EXPECTED terminal state, not a drift. The duty
        # of registering settled winners in token_suppression used to live in the
        # harvester and DIED with the abandoned redeem subsystem — leaving each swept
        # winner as a permanent latch-closing finding (HK 06-09: 11h submit freeze).
        # Auto-register the suppression with market-calendar evidence; the suppression
        # door above keeps it resolved on every future sweep. A NON-terminal
        # disappearance never matches here and still requires the operator-ack path
        # below (the theft/bug surface is preserved). Money truth is untouched: no
        # synthetic exit is booked — settlement P&L stays with the settlement organs +
        # the Confirm-pending-deposit check.
        settled_terminal = calendar_terminal.get(token)
        if (
            settled_terminal is not None
            and exchange_size <= Decimal("0")
            and confirmed_wallet_size > Decimal("0")
            and open_sell_locked_size <= Decimal("0")
        ):
            from src.state.db import record_token_suppression  # noqa: PLC0415

            record_token_suppression(
                conn,
                token_id=token,
                suppression_reason="settled_position",
                source_module="exchange_reconcile.settled_external_absorber",
                condition_id=settled_terminal.get("condition_id") or None,
                evidence={
                    "absorber": "settled_external_close",
                    "journal_size": str(confirmed_wallet_size),
                    "exchange_size": str(exchange_size),
                    **settled_terminal,
                },
            )
            _resolve_open_position_drift_findings(
                conn,
                token,
                resolution=_SETTLED_EXTERNAL_RESOLUTION,
                resolved_at=observed_at,
            )
            continue
        # Variant-3 antibody (refresh path): converge the operator external-close
        # double-count from current truth too, so the 1-minute refresh clears the latch
        # without waiting for the next full sweep. Same strict operator-ack gate.
        if _absorb_operator_external_close(
            conn,
            token_id=token,
            exchange_size=exchange_size,
            confirmed_size=confirmed_size,
            closed_position_size=closed_position_size,
            observed_at=observed_at,
        ):
            _resolve_open_position_drift_findings(
                conn,
                token,
                resolution=_OPERATOR_EXTERNAL_CLOSE_RESOLUTION,
                resolved_at=observed_at,
            )


def _position_size_matches(left: Decimal, right: Decimal) -> bool:
    return abs(left - right) <= _POSITION_DRIFT_ABS_TOLERANCE


def _nonnegative_wallet_size(value: Decimal) -> Decimal:
    """Wallet-token balances cannot be negative even when old local facts are incomplete."""

    return max(value, Decimal("0"))


def _live_open_sell_locked_tokens_by_token(
    conn: sqlite3.Connection,
    *,
    open_orders: list[Any] | None,
) -> dict[str, Decimal]:
    """CTF shares locked by venue-live SELL orders are absent from wallet balances."""

    if not open_orders or not _table_exists(conn, "venue_commands"):
        return {}
    open_order_ids = {_order_id(order) for order in open_orders if _order_id(order)}
    if not open_order_ids:
        return {}
    local_states = tuple(sorted(_OPEN_LOCAL_STATES))
    order_ids = tuple(sorted(open_order_ids))
    state_placeholders = ", ".join("?" for _ in local_states)
    order_placeholders = ", ".join("?" for _ in order_ids)
    rows = conn.execute(
        f"""
        SELECT command_id, token_id, size
          FROM venue_commands
         WHERE UPPER(COALESCE(intent_kind, '')) = 'EXIT'
           AND UPPER(COALESCE(side, '')) = 'SELL'
           AND state IN ({state_placeholders})
           AND venue_order_id IN ({order_placeholders})
        """,
        (*local_states, *order_ids),
    ).fetchall()
    out: dict[str, Decimal] = {}
    for row in rows:
        token = str(row["token_id"] or "").strip()
        if not token:
            continue
        try:
            requested = _decimal(row["size"])
        except (InvalidOperation, ValueError):
            continue
        filled = _canonical_filled_size_for_command(conn, str(row["command_id"]))
        locked = requested - filled
        if locked <= Decimal("0"):
            continue
        out[token] = out.get(token, Decimal("0")) + locked
    return out


def _canonical_filled_size_for_command(conn: sqlite3.Connection, command_id: str) -> Decimal:
    if not command_id or not _table_exists(conn, "venue_trade_facts"):
        return Decimal("0")
    rows = conn.execute(
        "WITH " + _canonical_trade_fact_cte() + """
        SELECT filled_size
          FROM canonical_trade_fact
         WHERE command_id = ?
           AND state IN ('MATCHED', 'MINED', 'CONFIRMED')
        """,
        (command_id,),
    ).fetchall()
    total = Decimal("0")
    for row in rows:
        try:
            filled = _decimal(row["filled_size"])
        except (InvalidOperation, ValueError):
            continue
        if filled > Decimal("0"):
            total += filled
    return total


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
            point_order_split = _point_order_aggregate_exact_trade_split_has_authority(
                latest_fact,
                raw=raw,
                venue_order_id=order_id,
                state=state,
                filled_size=filled_size,
                fill_price=fill_price,
            )
            if not point_order_split and not _confirmed_price_revision_has_authority(
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
    projection_position_id = str(current.get("position_id") or position_id).strip()
    if projection_position_id:
        position_id = projection_position_id
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
        "WITH " + _canonical_trade_fact_cte() + """
        SELECT tf.state, tf.filled_size, tf.fill_price
          FROM canonical_trade_fact tf
         WHERE tf.command_id = ?
           AND tf.state IN ('MATCHED', 'MINED', 'CONFIRMED')
        """,
        (command_id,),
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
    command_id = str(command.get("command_id") or "")
    if command_id:
        for event in events:
            if event.get("event_type") == "EXIT_ORDER_FILLED":
                event["command_id"] = command_id
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
        "WITH " + _canonical_trade_fact_cte() + """
        SELECT tf.state, tf.filled_size, tf.fill_price
          FROM canonical_trade_fact tf
         WHERE tf.command_id = ?
           AND tf.state IN ('MATCHED', 'MINED', 'CONFIRMED')
        """,
        (command_id,),
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
        "WITH " + _canonical_trade_fact_cte() + """
        SELECT tf.*
          FROM canonical_trade_fact tf
         WHERE tf.command_id = ?
           AND tf.state IN ('MATCHED', 'MINED', 'CONFIRMED')
         ORDER BY tf.observed_at, tf.trade_fact_id
        """,
        (str(command.get("command_id") or ""),),
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
        rows = conn.execute(
            """
            SELECT state, remaining_size, matched_size
              FROM venue_order_facts
             WHERE command_id = ?
               AND (? = '' OR venue_order_id = ?)
             ORDER BY local_sequence ASC, fact_id ASC
            """,
            (command_id, venue_order_id, venue_order_id),
        ).fetchall()
        if rows:
            from src.execution.order_truth_reducer import TERMINAL_FILLED, VenueOrderTruthReducer

            reduced = VenueOrderTruthReducer.reduce(
                order_facts=rows,
                trade_filled_size=shares,
                command_size=command.get("size"),
                command_state=str(command.get("state") or ""),
            )
            if reduced.proof_class == TERMINAL_FILLED:
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
        "WITH " + _canonical_trade_fact_cte() + """
        SELECT *
          FROM canonical_trade_fact
         WHERE trade_id = ?
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


def _point_order_aggregate_exact_trade_split_has_authority(
    fact: Mapping[str, Any],
    *,
    raw: Mapping[str, Any],
    venue_order_id: str | None,
    state: str,
    filled_size: str,
    fill_price: str,
) -> bool:
    """Allow exact venue-trade rows to replace an earlier point-order aggregate.

    A matched point-order proof can only say "this order matched N shares"; CLOB
    trade history can later split that fill across multiple trade ids. The
    exact child row is authoritative only when it is the same order, same price,
    and no larger than the prior aggregate.
    """

    if state not in {"MATCHED", "MINED", "CONFIRMED"}:
        return False
    raw_payload = _json_mapping(fact.get("raw_payload_json"))
    proof_class = str(raw_payload.get("proof_class") or "")
    reason = str(raw_payload.get("reason") or "")
    if proof_class != "point_order_matched_fill" and reason != "acked_order_point_order_matched":
        return False
    if _selected_maker_order(raw, venue_order_id) is None and not _taker_order_price_applies(raw, venue_order_id):
        return False
    if not _same_decimal_value(fact.get("fill_price"), fill_price):
        return False
    try:
        prior_size = _decimal(fact.get("filled_size"))
        incoming_size = _decimal(filled_size)
    except (InvalidOperation, ValueError):
        return False
    return incoming_size > Decimal("0") and incoming_size <= prior_size


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
                OR (
                    COALESCE(pc.phase, '') IN ({inactive_placeholders})
                    AND UPPER(COALESCE(c.intent_kind, '')) = 'EXIT'
                    AND UPPER(COALESCE(c.side, '')) = 'SELL'
                )
           )
        """,
        (*selected_states, *inactive_phases, *non_current_exit_statuses, *inactive_phases),
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


def _settlement_command_terminal_tokens(conn: sqlite3.Connection) -> frozenset[str]:
    if not _table_exists(conn, "settlement_commands"):
        return frozenset()
    terminal_states = tuple(sorted(_REDEEM_TERMINAL_WALLET_CONTRADICTION_STATES))
    state_placeholders = ", ".join("?" for _ in terminal_states)
    rows = conn.execute(
        f"""
        SELECT token_amounts_json
          FROM settlement_commands
         WHERE state IN ({state_placeholders})
           AND TRIM(COALESCE(token_amounts_json, '')) != ''
        """,
        terminal_states,
    ).fetchall()
    tokens: set[str] = set()
    for row in rows:
        payload = _json_mapping(row["token_amounts_json"])
        for token, raw_amount in payload.items():
            token_id = str(token).strip()
            if not token_id:
                continue
            amount = _positive_decimal_or_none(raw_amount)
            if amount is None:
                continue
            tokens.add(token_id)
    return frozenset(tokens)


def _closed_position_token_holdings_by_token(conn: sqlite3.Connection) -> dict[str, Decimal]:
    """Expected wallet CTF holdings from terminal local positions still on-chain.

    Some historical positions have already left active exposure (`settled`,
    `admin_closed`, or recovery `voided`) while the chain/wallet surface still
    reports their CTF token balance. They are not active trade exposure, but they
    are legitimate expected wallet holdings until a redeem command is created and
    confirmed. Terminal redeem commands are excluded so a claimed/rejected redeem
    cannot mask real exchange drift.
    """

    if not _table_exists(conn, "position_current"):
        return {}
    terminal_redeem_tokens = _settlement_command_terminal_tokens(conn)
    phase_placeholders = ", ".join("?" for _ in _CLOSED_POSITION_WALLET_HOLDING_PHASES)
    chain_placeholders = ", ".join("?" for _ in _CLOSED_POSITION_WALLET_HOLDING_CHAIN_STATES)
    rows = conn.execute(
        f"""
        SELECT position_id, token_id, no_token_id, direction, shares, order_id
          FROM position_current
         WHERE phase IN ({phase_placeholders})
           AND chain_state IN ({chain_placeholders})
           AND COALESCE(shares, 0) > 0
        """,
        (
            *tuple(sorted(_CLOSED_POSITION_WALLET_HOLDING_PHASES)),
            *tuple(sorted(_CLOSED_POSITION_WALLET_HOLDING_CHAIN_STATES)),
        ),
    ).fetchall()
    # DEDUPE BY ON-CHAIN HOLDING (2026-06-16 intra-Zeus double-count antibody): the
    # wallet holds a token's CTF balance ONCE regardless of how many position_current
    # lifecycle rows Zeus recorded for the same fill. Multiple terminal rows that share
    # one venue order_id are the SAME on-chain holding (observed: token
    # 9491..517 booked under three position_ids — two voided, all 5.07 shares, one
    # order 0x5ce1.. — summed to expected_wallet 10.14 vs exchange 5.07, freezing the
    # M5 latch forever). Collapse a (token, order_id) group to its single
    # representative holding (max share = the full fill); rows on DISTINCT orders are
    # distinct fills and still sum (token 1139..946: two orders, 6+6 = 12.0 preserved).
    # A row with no order_id cannot be proven a duplicate, so it is treated as its own
    # distinct holding (fail toward over-counting → keep the finding rather than mask
    # real drift). Mirrors the on-chain truth the exchange position reports.
    holdings_by_order: dict[str, dict[str, Decimal]] = {}
    for row in rows:
        direction = str(row["direction"] or "").strip().lower()
        token = row["no_token_id"] if direction == "buy_no" else row["token_id"]
        token_id = str(token or "").strip()
        if not token_id or token_id in terminal_redeem_tokens:
            continue
        amount = _positive_decimal_or_none(row["shares"])
        if amount is None:
            continue
        order_id = str(row["order_id"] or "").strip()
        # NULL/empty order_id → unique per position row so it is never collapsed.
        group_key = order_id if order_id else f"__no_order__:{row['position_id']}"
        token_groups = holdings_by_order.setdefault(token_id, {})
        token_groups[group_key] = max(token_groups.get(group_key, Decimal("0")), amount)
    out: dict[str, Decimal] = {}
    for token_id, groups in holdings_by_order.items():
        out[token_id] = sum(groups.values(), Decimal("0"))
    return out


def _chain_confirmed_active_holdings_by_token(conn: sqlite3.Connection) -> dict[str, Decimal]:
    """On-chain-confirmed CTF holdings for ACTIVE positions — VENUE truth, not the journal.

    The position_drift absorbers above use the M5 confirmed-trade-facts journal as their
    wallet-truth basis. But a fill that arrives during a user-channel ws_gap is confirmed
    ONLY by the on-chain CTF balance — the chain reconciler (src/state/chain_reconciliation
    ``reconcile``: "chain is truth") reads balanceOf and sets ``chain_state='synced'`` with
    the backed ``chain_shares`` — and is NEVER written as a journaled trade. Such a position
    leaves the exchange position permanently unexplained by the journal (0), so the recorder
    re-records the same position_drift on every M5 sweep and the submit latch never clears.

    Observed 2026-06-16: Seoul buy_no 10.86 (finding 3c7427cf), ``chain_state=synced`` /
    ``chain_shares=10.86`` vs ``confirmed_journal=0`` — froze ALL new submits for hours.

    The persisted ``chain_shares`` (the chain reconciler's data-api /positions read,
    ``chain_state='synced'``) is matched against the FRESH exchange /positions read at sweep
    time. The two are the same surface (snapshot vs fresh), not independent oracles — but a
    real reduction/loss surfaces FIRST in the fresh read, so equality means the position is
    still present at its last chain-confirmed size and there is no unexplained exposure (a
    loss/theft would LOWER the fresh read, break the equality, and keep the finding). Keyed by
    the HELD outcome token (no_token_id for buy_no, token_id otherwise) and deduped by
    (token, order_id) like the terminal helper, so lifecycle rows of one fill never double-count.
    """

    if not _table_exists(conn, "position_current"):
        return {}
    rows = conn.execute(
        f"""
        SELECT position_id, token_id, no_token_id, direction, chain_shares, order_id
          FROM position_current
         WHERE phase IN ({", ".join("?" for _ in _CHAIN_CONFIRMED_HELD_PHASES)})
           AND chain_state = 'synced'
           AND COALESCE(chain_shares, 0) > 0
        """,
        tuple(sorted(_CHAIN_CONFIRMED_HELD_PHASES)),
    ).fetchall()
    holdings_by_order: dict[str, dict[str, Decimal]] = {}
    for row in rows:
        direction = str(row["direction"] or "").strip().lower()
        token = row["no_token_id"] if direction == "buy_no" else row["token_id"]
        token_id = str(token or "").strip()
        if not token_id:
            continue
        amount = _positive_decimal_or_none(row["chain_shares"])
        if amount is None:
            continue
        order_id = str(row["order_id"] or "").strip()
        group_key = order_id if order_id else f"__no_order__:{row['position_id']}"
        token_groups = holdings_by_order.setdefault(token_id, {})
        token_groups[group_key] = max(token_groups.get(group_key, Decimal("0")), amount)
    out: dict[str, Decimal] = {}
    for token_id, groups in holdings_by_order.items():
        out[token_id] = sum(groups.values(), Decimal("0"))
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


def _token_is_suppressed_external(conn: sqlite3.Connection, token_id: str) -> bool:
    """True iff the token is in the token_suppression registry (chain-only / settled holding).

    ONE-TRUTH (rule 4 — stop multi-system infighting): token_suppression is the single registry
    of tokens the system does NOT own as an open trading concern. chain_reconciliation quarantines
    chain-only / operator-manual holdings there ('chain_only_quarantined'); the harvester suppresses
    settled winners after redeem ('settled_position'). A suppressed token is provably not a system
    open-position drift, so it MUST NOT be re-flagged as position_drift and gate the submit latch —
    exactly the conflict that halted live trading on the operator's manual chain positions. Such a
    token resolves naturally on settlement (win -> redeem); the reconciler just stops fighting it."""
    if not _table_exists(conn, "token_suppression"):
        return False
    return (
        conn.execute(
            "SELECT 1 FROM token_suppression WHERE token_id = ? LIMIT 1",
            (str(token_id),),
        ).fetchone()
        is not None
    )


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
