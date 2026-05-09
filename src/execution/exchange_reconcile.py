"""R3 M5 exchange reconciliation sweep.

This module reconciles read-only exchange observations against Zeus's durable
venue-command/fact journal.  It is intentionally not an execution actuator:
exchange-only state becomes an ``exchange_reconcile_findings`` row, not a new
``venue_commands`` row, and no live venue submit/cancel/redeem side effects are
performed here.
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from typing import Any, Literal, Mapping, Optional

from src.architecture.decorators import capability, protects
from src.state.venue_command_repo import trade_fact_has_positive_fill_economics

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
_TRADE_FACT_STATES = frozenset({"MATCHED", "MINED", "CONFIRMED", "RETRYING", "FAILED"})
_CONFIRMED_POSITION_FACT_STATES = frozenset({"CONFIRMED"})
_OPTIMISTIC_POSITION_FACT_STATES = frozenset({"MATCHED", "MINED"})

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


def init_exchange_reconcile_schema(conn: sqlite3.Connection) -> None:
    """Create the M5 findings table if absent."""

    conn.executescript(_SCHEMA)


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
    for trade in trades or []:
        raw = _raw(trade)
        venue_trade_id = _trade_id(raw)
        subject_id = venue_trade_id or _stable_subject("trade", raw)
        order_id = _trade_order_id(raw)
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
        if order_id and state in {"MATCHED", "MINED", "CONFIRMED"}:
            trade_order_ids.add(order_id)
        command = local_by_order.get(order_id or "")
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
        )
        if finding is not None:
            findings.append(finding)

    for order_id, command in local_by_order.items():
        if order_id in open_order_ids or order_id in trade_order_ids:
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
                    "trade_enumeration_available": trades_available,
                    "reason": "local_open_order_absent_from_exchange_open_orders",
                },
                recorded_at=observed,
            )
        )

    positions_available = callable(getattr(adapter, "get_positions", None))
    if positions_available:
        _assert_adapter_read_fresh(adapter, "positions", observed)
    positions = adapter.get_positions() if positions_available else []
    findings.extend(
        _record_position_drift_findings(
            conn,
            positions=positions,
            context=context,
            observed_at=observed,
        )
    )
    return findings


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
    tokens = sorted(set(exchange) | set(confirmed_journal))
    findings: list[ReconcileFinding] = []
    for token in tokens:
        exchange_size = exchange.get(token, Decimal("0"))
        confirmed_size = confirmed_journal.get(token, Decimal("0"))
        optimistic_size = optimistic_journal.get(token, Decimal("0"))
        if exchange_size == confirmed_size:
            continue
        if _has_recent_filled_suppression(conn, token, observed_at):
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
                    "journal_evidence_class": "confirmed_trade_facts",
                    "optimistic_evidence_class": "matched_or_mined_trade_facts",
                    "reason": "exchange_position_differs_from_confirmed_trade_facts",
                },
                recorded_at=observed_at,
            )
        )
    return findings


def _append_linkable_trade_fact_if_missing(
    conn: sqlite3.Connection,
    command: Mapping[str, Any],
    raw: Mapping[str, Any],
    trade_id: str,
    observed_at: datetime,
    *,
    state: str,
    context: ReconcileContext,
) -> ReconcileFinding | None:
    from src.state.venue_command_repo import append_event, append_trade_fact, get_command

    order_id = _trade_order_id(raw) or str(command["venue_order_id"])
    filled_size_raw = _first_present(raw, "filled_size", "size", "amount", default=None)
    fill_price_raw = _first_explicit_fill_price(raw)
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
            return None
        if state in {"MATCHED", "MINED", "CONFIRMED"} and not same_fill_economics:
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
    )
    if state in {"FAILED", "RETRYING"}:
        return None
    latest = get_command(conn, str(command["command_id"]))
    if latest is None:
        return None
    event = _fill_event_for_command(latest, filled_size, trade_state=state)
    if event is None:
        return None
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
        return None
    return None


def _first_explicit_fill_price(raw: Mapping[str, Any]) -> Any:
    return _first_present(raw, "avgPrice", "avg_price", "fillPrice", "fill_price", default=None)


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


def _local_order_is_open(conn: sqlite3.Connection, command: Mapping[str, Any]) -> bool:
    if str(command.get("state")) not in _OPEN_LOCAL_STATES:
        return False
    latest = _latest_order_fact(conn, str(command["venue_order_id"]))
    if latest is None:
        return True
    return str(latest.get("state")) in _OPEN_ORDER_FACT_STATES


def _latest_order_fact(conn: sqlite3.Connection, venue_order_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT *
          FROM venue_order_facts
         WHERE venue_order_id = ?
         ORDER BY local_sequence DESC, fact_id DESC
         LIMIT 1
        """,
        (venue_order_id,),
    ).fetchone()
    return dict(row) if row is not None else None


def _local_absence_kind(context: ReconcileContext) -> FindingKind:
    if context == "heartbeat_loss":
        return "heartbeat_suspected_cancel"
    if context == "cutover":
        return "cutover_wipe"
    return "local_orphan_order"


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
    placeholders = ", ".join("?" for _ in selected_states)
    rows = conn.execute(
        f"""
        SELECT c.token_id, c.side, tf.filled_size, tf.fill_price
          FROM venue_trade_facts tf
          JOIN venue_commands c ON c.command_id = tf.command_id
         WHERE tf.local_sequence = (
               SELECT MAX(newer.local_sequence)
                 FROM venue_trade_facts newer
                WHERE newer.trade_id = tf.trade_id
         )
           AND tf.state IN ({placeholders})
        """,
        selected_states,
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


def _trade_id(raw: Mapping[str, Any]) -> str | None:
    return _string_or_none(_first_present(raw, "trade_id", "tradeID", "id", default=None))


def _trade_order_id(raw: Mapping[str, Any]) -> str | None:
    return _string_or_none(
        _first_present(raw, "orderID", "orderId", "order_id", "maker_order_id", "taker_order_id", default=None)
    )


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
