"""Order executor: limit-order-only execution engine. Spec §6.4.

Live entry execution uses FinalExecutionIntent through the venue adapter.

Key rules:
- Limit orders ONLY (never market orders)
- Mode-based timeouts: Opening Hunt 4h, Update Reaction 1h, Day0 15min
- Whale toxicity detection: cancel on adjacent bin sweeps
- Share quantization: BUY rounds UP, SELL rounds DOWN (0.01 increments)
- Dynamic limit: if within 5% of best ask, jump to ask for guaranteed fill
"""

import hashlib
import json
import logging
import math
import os
import sqlite3
import time
import uuid
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_CEILING, ROUND_FLOOR
from typing import Any, Mapping, Optional

from src.config import get_mode, settings
from src.riskguard.discord_alerts import alert_trade
from src.contracts.slippage_bps import SlippageBps
from src.contracts import (
    DecisionSourceContext,
    HeldSideProbability,
    NativeSidePrice,
    compute_native_limit_price,
    ExecutionIntent,
    EdgeContext,
    FinalExecutionIntent,
    Direction,
    simulate_clob_sweep,
)
from src.contracts.execution_price import (
    ExecutionPrice,
    ExecutionPriceContractError,
)
from src.types import BinEdge
from src.architecture.decorators import capability, protects
from src.state.db import (
    get_trade_connection_with_world_required,
)
from src.state.lifecycle_manager import LifecyclePhase, TERMINAL_STATES

logger = logging.getLogger(__name__)

_LIVE_ENTRY_MIN_EXPECTED_PROFIT_USD = 0.05
_LIVE_ENTRY_MIN_SUBMIT_EDGE_DENSITY = 0.02


# Mode-based fill timeout (seconds). Spec §6.4.
MODE_TIMEOUTS = {
    "opening_hunt": 4 * 3600,
    "update_reaction": 1 * 3600,
    "day0_capture": 15 * 60,
    # imminent_open_capture: mirrors day0_capture (0-24h window, fast-resolve).
    # Scheduler registers this mode in main.py but cycle_runtime._mode_timeout_seconds
    # raised "Unknown execution mode" before this entry existed — every candidate
    # found by the imminent mode died at the execute_intent boundary. This was
    # the dominant root cause of 0 entry orders submitted during the 2026-05-19
    # alpha-loss session. Authority: operator code-review-may19 P1-1.
    "imminent_open_capture": 15 * 60,
}


def _assert_cutover_allows_submit(intent_kind) -> dict:
    """Fail before command persistence or SDK contact when cutover is not live."""
    from src.control.cutover_guard import assert_submit_allowed

    assert_submit_allowed(intent_kind)
    return _capability_component("cutover_guard", intent_kind=str(getattr(intent_kind, "value", intent_kind)))


def _assert_heartbeat_allows_submit(order_type: str = "GTC") -> dict:
    """Fail before command persistence or SDK contact when heartbeat is unhealthy."""
    from src.control.heartbeat_supervisor import assert_heartbeat_allows_order_type

    assert_heartbeat_allows_order_type(order_type)
    return _capability_component("heartbeat_supervisor", order_type=order_type)


def _assert_ws_gap_allows_submit(market_id: str | None = None) -> dict:
    """Fail before command persistence or SDK contact when M3 user WS is gapped."""
    from src.control.ws_gap_guard import assert_ws_allows_submit

    assert_ws_allows_submit(market_id)
    return _capability_component("ws_gap_guard", market_id=market_id or "")


def _assert_risk_allocator_allows_submit(intent: ExecutionIntent):
    """Fail before command persistence or SDK contact when A2 allocator denies risk."""
    from src.risk_allocator import assert_global_allocation_allows

    return assert_global_allocation_allows(intent)


def _assert_risk_allocator_allows_exit_submit():
    """Fail before exit command persistence/SDK contact when A2 kill switch is armed."""
    from src.risk_allocator import assert_global_submit_allows

    return assert_global_submit_allows(reduce_only=True)


def _select_risk_allocator_order_type(conn: sqlite3.Connection, snapshot_id: str) -> str:
    """Select the concrete venue order type from A2 governor + snapshot evidence.

    This is read-only and must run before venue-command persistence so degraded
    states can force FOK/FAK-family submission rather than merely reporting an
    advisory maker/taker mode.
    """

    from src.risk_allocator import select_global_order_type
    from src.state.snapshot_repo import get_snapshot

    snapshot = get_snapshot(conn, snapshot_id) if snapshot_id else None
    return select_global_order_type(snapshot)


def _risk_allocator_order_type_allows_intent(
    *,
    selected_order_type: str,
    intent_order_type: str,
) -> bool:
    selected = str(selected_order_type or "").strip().upper()
    intended = str(intent_order_type or "").strip().upper()
    if not intended or selected == intended:
        return True
    resting = {"GTC", "GTD"}
    immediate = {"FOK", "FAK"}
    if selected in resting and intended in immediate:
        return True
    return False


def _exit_order_type(selected_order_type: str) -> str:
    """Role-scoped exit order-type: an exit is IOC, never all-or-nothing.

    The global allocator returns ``FOK`` for a TAKER decision (governor.
    select_global_order_type), but its own docstring states the intended
    semantics is "immediate-or-cancel" — which is ``FAK`` (fill-and-kill /
    IOC), not ``FOK`` (fill-or-kill / atomic). For an EXIT that distinction is
    money-path-critical: once we have DECIDED to exit, a partial fill out beats
    zero fill. FOK on a thin/dying book means the whole sell is killed, the
    position never realizes, and recoverable value bleeds to ~0 (live evidence
    2026-06-24: Houston 92-93F NO, exit_retry_count=6, market 0.356->0.076,
    every retry "order couldn't be fully filled. FOK orders are fully filled or
    killed").

    The exit lifecycle re-derives shares from chain truth each retry and parks
    sub-min remainders as dust, so FAK partial fills converge. Resting types
    (GTC/GTD — a maker-resting exit on a deep book) are returned unchanged; only
    the FOK all-or-nothing hazard is rewritten. Taker ENTRY semantics are NOT
    affected — this coercion is applied only on the exit submit seam.
    """

    normalized = str(selected_order_type or "").strip().upper()
    if normalized == "FOK":
        return "FAK"
    return normalized


_ENTRY_DUPLICATE_NON_OPEN_PHASES = frozenset(
    set(TERMINAL_STATES) | {LifecyclePhase.ECONOMICALLY_CLOSED.value}
)
_ENTRY_DUPLICATE_OPEN_COMMAND_STATES = frozenset(
    {
        "INTENT_CREATED",
        "SNAPSHOT_BOUND",
        "SIGNED_PERSISTED",
        "POSTING",
        "POST_ACKED",
        "SUBMITTING",
        "ACKED",
        "PARTIAL",
        "UNKNOWN",
        "SUBMIT_UNKNOWN_SIDE_EFFECT",
        "REVIEW_REQUIRED",
        "CANCEL_PENDING",
    }
)
_ENTRY_DUPLICATE_TERMINAL_NO_EXPOSURE_COMMAND_STATES = frozenset(
    {"REJECTED", "SUBMIT_REJECTED", "CANCELLED", "EXPIRED"}
)
_ENTRY_DUPLICATE_TERMINAL_NO_FILL_ORDER_STATES = frozenset(
    {"CANCEL_CONFIRMED", "EXPIRED", "VENUE_WIPED"}
)
_ENTRY_SAME_TOKEN_COOLDOWN_SECONDS = 30 * 60
_ENTRY_TERMINAL_NO_FILL_PRICE_CHANGE_EPS = Decimal("0.0001")
_ENTRY_TERMINAL_NO_FILL_SIZE_CHANGE_EPS = Decimal("0.000001")
_ENTRY_TAKER_MIN_FEE_ADJUSTED_EDGE = Decimal("0.03")
_ENTRY_TAKER_MIN_INCREMENTAL_PROFIT_USD = Decimal("0.05")
_ENTRY_TAKER_MIN_CONFIDENCE = Decimal("0.60")
_ENTRY_TAKER_MIN_PROFIT_RATIO = Decimal("1.20")


def _quote_sql_identifier(identifier: str) -> str:
    if not identifier or not all(ch.isalnum() or ch == "_" for ch in identifier):
        raise ValueError(f"unsafe sqlite identifier: {identifier!r}")
    return '"' + identifier.replace('"', '""') + '"'


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type IN ('table', 'view') AND name = ?",
        (table,),
    ).fetchone()
    return row is not None


def _table_column_names(conn: sqlite3.Connection, table: str) -> set[str]:
    try:
        quoted = _quote_sql_identifier(table)
        return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({quoted})")}
    except sqlite3.Error:
        return set()


def _entry_has_positive_trade_fact(
    conn: sqlite3.Connection,
    *,
    command_id: str = "",
    position_id: str = "",
    order_id: str = "",
) -> bool:
    if not _table_exists(conn, "venue_trade_facts"):
        return False
    if command_id:
        row = conn.execute(
            """
            SELECT 1
              FROM venue_trade_facts
             WHERE CAST(filled_size AS REAL) > 0
               AND command_id = ?
             LIMIT 1
            """,
            (command_id,),
        ).fetchone()
        return row is not None
    if not _table_exists(conn, "venue_commands"):
        return False
    row = conn.execute(
        """
        SELECT 1
          FROM venue_trade_facts vtf
          JOIN venue_commands vc ON vc.command_id = vtf.command_id
         WHERE CAST(vtf.filled_size AS REAL) > 0
           AND (
                (? != '' AND vc.position_id = ?)
                OR (? != '' AND vc.venue_order_id = ?)
           )
         LIMIT 1
        """,
        (position_id, position_id, order_id, order_id),
    ).fetchone()
    return row is not None


def _latest_entry_command_for_duplicate_position(
    conn: sqlite3.Connection,
    *,
    position_id: str,
    order_id: str,
) -> dict | None:
    if not _table_exists(conn, "venue_commands"):
        return None
    row = conn.execute(
        """
        SELECT command_id, state, venue_order_id
          FROM venue_commands
         WHERE intent_kind = 'ENTRY'
           AND (
                position_id = ?
                OR (? != '' AND venue_order_id = ?)
           )
         ORDER BY updated_at DESC, created_at DESC
         LIMIT 1
        """,
        (position_id, order_id, order_id),
    ).fetchone()
    if row is None:
        return None
    if isinstance(row, sqlite3.Row):
        return {key: row[key] for key in row.keys()}
    return {"command_id": row[0], "state": row[1], "venue_order_id": row[2]}


def _entry_command_has_terminal_no_fill_order_fact(
    conn: sqlite3.Connection,
    command_id: str,
) -> bool:
    if not command_id or not _table_exists(conn, "venue_order_facts"):
        return False
    row = conn.execute(
        """
        SELECT state, matched_size
          FROM venue_order_facts
         WHERE command_id = ?
         ORDER BY local_sequence DESC, observed_at DESC
         LIMIT 1
        """,
        (command_id,),
    ).fetchone()
    if row is None:
        return False
    state = str(row["state"] if isinstance(row, sqlite3.Row) else row[0] or "").upper()
    if state not in _ENTRY_DUPLICATE_TERMINAL_NO_FILL_ORDER_STATES:
        return False
    matched_size = row["matched_size"] if isinstance(row, sqlite3.Row) else row[1]
    try:
        return Decimal(str(matched_size or "0")) == Decimal("0")
    except (InvalidOperation, ValueError):
        return False


def _entry_terminal_command_has_no_fill_exposure(
    conn: sqlite3.Connection,
    *,
    command_id: str,
    state: str,
) -> bool:
    state_text = str(state or "").upper()
    if state_text not in _ENTRY_DUPLICATE_TERMINAL_NO_EXPOSURE_COMMAND_STATES:
        return False
    if _entry_has_positive_trade_fact(conn, command_id=command_id):
        return False
    if state_text in {"CANCELLED", "EXPIRED"}:
        return _entry_command_has_terminal_no_fill_order_fact(conn, command_id)
    return True


def _pending_entry_terminal_no_fill_allows_entry(
    conn: sqlite3.Connection,
    row: sqlite3.Row | tuple,
) -> bool:
    phase = str(row["phase"] if isinstance(row, sqlite3.Row) else row[1] or "").lower()
    if phase != "pending_entry":
        return False
    position_id = str(row["position_id"] if isinstance(row, sqlite3.Row) else row[0] or "")
    order_id = str(row["order_id"] if isinstance(row, sqlite3.Row) else row[2] or "")
    try:
        shares = Decimal(str(row["shares"] if isinstance(row, sqlite3.Row) else row[3] or "0"))
        cost_basis = Decimal(str(row["cost_basis_usd"] if isinstance(row, sqlite3.Row) else row[4] or "0"))
    except (InvalidOperation, ValueError):
        return False
    if shares != Decimal("0") or cost_basis != Decimal("0"):
        return False
    command = _latest_entry_command_for_duplicate_position(
        conn,
        position_id=position_id,
        order_id=order_id,
    )
    if command is None:
        return False
    command_id = str(command.get("command_id") or "")
    state = str(command.get("state") or "").upper()
    if state not in _ENTRY_DUPLICATE_TERMINAL_NO_EXPOSURE_COMMAND_STATES:
        return False
    if _entry_has_positive_trade_fact(conn, position_id=position_id, order_id=order_id):
        return False
    return _entry_terminal_command_has_no_fill_exposure(
        conn,
        command_id=command_id,
        state=state,
    )


def _attached_schema_names(conn: sqlite3.Connection) -> tuple[str, ...]:
    try:
        rows = conn.execute("PRAGMA database_list").fetchall()
    except sqlite3.Error:
        return ("main",)
    names: list[str] = []
    for row in rows:
        try:
            name = row["name"] if isinstance(row, sqlite3.Row) else row[1]
        except (IndexError, KeyError, TypeError):
            continue
        text = str(name or "").strip()
        if text:
            names.append(text)
    return tuple(dict.fromkeys(names)) or ("main",)


def _table_exists_in_schema(conn: sqlite3.Connection, schema: str, table: str) -> bool:
    schema_sql = _quote_sql_identifier(schema)
    row = conn.execute(
        f"SELECT 1 FROM {schema_sql}.sqlite_master WHERE type IN ('table', 'view') AND name = ?",
        (table,),
    ).fetchone()
    return row is not None


def _main_database_filename(conn: sqlite3.Connection) -> str:
    try:
        rows = conn.execute("PRAGMA database_list").fetchall()
    except sqlite3.Error:
        return ""
    for row in rows:
        try:
            name = row["name"] if isinstance(row, sqlite3.Row) else row[1]
            path = row["file"] if isinstance(row, sqlite3.Row) else row[2]
        except (IndexError, KeyError, TypeError):
            continue
        if str(name or "").strip() == "main":
            return os.path.basename(str(path or "").strip())
    return ""


def _attach_world_for_trade_certificate_read(conn: sqlite3.Connection) -> str | None:
    """Expose the canonical world certificate ledger to trade-main connections."""

    if "world" in _attached_schema_names(conn):
        return None
    if _main_database_filename(conn) != "zeus_trades.db":
        return None
    try:
        from src.state.db import ZEUS_WORLD_DB_PATH

        conn.execute("ATTACH DATABASE ? AS world", (str(ZEUS_WORLD_DB_PATH),))
    except sqlite3.Error as exc:
        return str(exc)
    return None


def _entry_control_pause_component(conn: sqlite3.Connection) -> dict:
    """Read the single durable entries-paused authority at the submit boundary.

    ``control_overrides`` tables in trade DB are legacy archived ghosts; they
    must not be consumed as live submit authority.  The control plane writes and
    resumes through world DB, so the executor opens that authority directly.
    """

    try:
        from src.state.db import get_world_connection, query_control_override_state

        world_conn = get_world_connection()
        try:
            state = query_control_override_state(world_conn)
        finally:
            world_conn.close()
    except Exception as exc:  # noqa: BLE001
        return {
            "component": "entries_pause_control_override",
            "allowed": False,
            "reason": f"entries_pause_control_unreadable:{type(exc).__name__}",
            "authority_schema": "world",
        }

    if state.get("status") != "ok":
        return {
            "component": "entries_pause_control_override",
            "allowed": False,
            "reason": f"entries_pause_control_unreadable:{state.get('status', 'unknown')}",
            "authority_schema": "world",
        }
    if bool(state.get("entries_paused", False)):
        return {
            "component": "entries_pause_control_override",
            "allowed": False,
            "reason": str(state.get("entries_pause_reason") or "entries_paused"),
            "issued_by": str(state.get("entries_pause_source") or ""),
            "authority_schema": "world",
        }
    return {
        "component": "entries_pause_control_override",
        "allowed": True,
        "reason": "allowed",
        "authority_schema": "world",
    }


def _proof_decimal(proof: Any, key: str) -> Decimal | None:
    if not isinstance(proof, dict):
        return None
    raw = proof.get(key)
    if raw in (None, ""):
        return None
    try:
        value = Decimal(str(raw))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return value if value.is_finite() else None


def _proof_bool(proof: Any, key: str) -> bool | None:
    if not isinstance(proof, dict):
        return None
    raw = proof.get(key)
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, str):
        normalized = raw.strip().lower()
        if normalized in {"true", "1", "yes"}:
            return True
        if normalized in {"false", "0", "no"}:
            return False
    return None


def _entry_taker_quality_component(
    *,
    effective_order_type: str,
    post_only: bool,
    intent_order_type: str | None = None,
    taker_quality_proof: Any = None,
) -> dict:
    """Final live-entry policy: takers need explicit edge-vs-maker proof."""

    order_type = str(effective_order_type or "").strip().upper()
    if post_only:
        if order_type not in {"GTC", "GTD"}:
            return {
                "component": "entry_taker_quality",
                "allowed": False,
                "reason": "entry_resting_order_type_required",
                "order_type": order_type,
                "intent_order_type": "" if intent_order_type is None else str(intent_order_type),
                "post_only": True,
            }
        return {
            "component": "entry_taker_quality",
            "allowed": True,
            "reason": "maker_resting_allowed",
            "order_type": order_type,
            "intent_order_type": "" if intent_order_type is None else str(intent_order_type),
            "post_only": True,
        }
    if order_type not in {"FOK", "FAK"}:
        return {
            "component": "entry_taker_quality",
            "allowed": False,
            "reason": "entry_taker_requires_fok_or_fak",
            "order_type": order_type,
            "intent_order_type": "" if intent_order_type is None else str(intent_order_type),
            "post_only": False,
        }
    if not isinstance(taker_quality_proof, dict):
        return {
            "component": "entry_taker_quality",
            "allowed": False,
            "reason": "missing_taker_quality_proof",
            "order_type": order_type,
            "intent_order_type": "" if intent_order_type is None else str(intent_order_type),
            "post_only": False,
        }
    proof_passed = _proof_bool(taker_quality_proof, "passed")
    taker_edge = _proof_decimal(taker_quality_proof, "taker_fee_adjusted_edge")
    taker_profit = _proof_decimal(taker_quality_proof, "taker_expected_profit_usd")
    maker_profit = _proof_decimal(taker_quality_proof, "maker_expected_profit_usd")
    incremental_profit = _proof_decimal(taker_quality_proof, "incremental_expected_profit_usd")
    confidence = _proof_decimal(taker_quality_proof, "model_confidence")
    missing = [
        name
        for name, value in (
            ("taker_fee_adjusted_edge", taker_edge),
            ("taker_expected_profit_usd", taker_profit),
            ("maker_expected_profit_usd", maker_profit),
            ("incremental_expected_profit_usd", incremental_profit),
            ("model_confidence", confidence),
            ("passed", None if proof_passed is None else Decimal("1")),
        )
        if value is None
    ]
    if missing:
        return {
            "component": "entry_taker_quality",
            "allowed": False,
            "reason": "invalid_taker_quality_proof",
            "missing": ",".join(missing),
            "order_type": order_type,
            "post_only": False,
        }
    if proof_passed is not True:
        return {
            "component": "entry_taker_quality",
            "allowed": False,
            "reason": "taker_quality_proof_not_passed",
            "order_type": order_type,
            "post_only": False,
        }
    required_profit = max(
        maker_profit * _ENTRY_TAKER_MIN_PROFIT_RATIO,
        maker_profit + _ENTRY_TAKER_MIN_INCREMENTAL_PROFIT_USD,
    )
    if taker_edge < _ENTRY_TAKER_MIN_FEE_ADJUSTED_EDGE:
        reason = "taker_fee_adjusted_edge_below_floor"
    elif incremental_profit < _ENTRY_TAKER_MIN_INCREMENTAL_PROFIT_USD:
        reason = "taker_incremental_profit_below_floor"
    elif taker_profit < required_profit:
        reason = "taker_profit_not_significantly_above_maker"
    elif confidence < _ENTRY_TAKER_MIN_CONFIDENCE:
        reason = "model_confidence_below_taker_floor"
    else:
        reason = ""
    if reason:
        return {
            "component": "entry_taker_quality",
            "allowed": False,
            "reason": reason,
            "order_type": order_type,
            "post_only": False,
            "taker_fee_adjusted_edge": str(taker_edge),
            "taker_expected_profit_usd": str(taker_profit),
            "maker_expected_profit_usd": str(maker_profit),
            "incremental_expected_profit_usd": str(incremental_profit),
            "model_confidence": str(confidence),
        }
    return {
        "component": "entry_taker_quality",
        "allowed": True,
        "reason": "taker_quality_passed",
        "order_type": order_type,
        "intent_order_type": "" if intent_order_type is None else str(intent_order_type),
        "post_only": False,
        "taker_fee_adjusted_edge": str(taker_edge),
        "taker_expected_profit_usd": str(taker_profit),
        "maker_expected_profit_usd": str(maker_profit),
        "incremental_expected_profit_usd": str(incremental_profit),
        "model_confidence": str(confidence),
    }


def _float_field(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(parsed):
        return None
    return parsed


def _entry_economics_component(intent: ExecutionIntent, *, shares: float) -> dict:
    """Executor-side live ENTRY submit proof.

    Upstream qkernel/family selection owns probability math. The executor's job
    is fail-closed consumption: an ENTRY cannot reach the venue unless the final
    intent carries the selected-side q/q_lcb and proves the submit price still
    has positive conservative edge after the exact submitted share count.
    """

    q_live = _float_field(getattr(intent, "q_live", None))
    q_lcb = _float_field(getattr(intent, "q_lcb_5pct", None))
    expected_edge = _float_field(getattr(intent, "expected_edge", None))
    min_entry_price = _float_field(getattr(intent, "min_entry_price", None))
    min_expected_profit = _float_field(getattr(intent, "min_expected_profit_usd", None))
    min_edge_density = _float_field(getattr(intent, "min_submit_edge_density", None))
    limit_price = _float_field(getattr(intent, "limit_price", None))
    submitted_shares = _float_field(shares)
    missing = [
        name
        for name, value in (
            ("q_live", q_live),
            ("q_lcb_5pct", q_lcb),
            ("expected_edge", expected_edge),
            ("min_entry_price", min_entry_price),
            ("min_expected_profit_usd", min_expected_profit),
            ("min_submit_edge_density", min_edge_density),
            ("limit_price", limit_price),
            ("shares", submitted_shares),
        )
        if value is None
    ]
    economics = getattr(intent, "qkernel_execution_economics", None)
    if not isinstance(economics, Mapping):
        missing.append("qkernel_execution_economics")
    if missing:
        return _capability_component(
            "entry_economics",
            allowed=False,
            reason="missing_entry_economics",
            missing=",".join(missing),
        )
    assert q_live is not None
    assert q_lcb is not None
    assert expected_edge is not None
    assert min_entry_price is not None
    assert min_expected_profit is not None
    assert min_edge_density is not None
    assert limit_price is not None
    assert submitted_shares is not None
    if not (0.0 <= q_lcb <= q_live <= 1.0):
        return _capability_component(
            "entry_economics",
            allowed=False,
            reason="invalid_probability_order",
            q_live=q_live,
            q_lcb_5pct=q_lcb,
        )
    if not (0.0 < limit_price < 1.0 and submitted_shares > 0.0):
        return _capability_component(
            "entry_economics",
            allowed=False,
            reason="invalid_price_or_size",
            limit_price=limit_price,
            shares=submitted_shares,
        )
    submit_edge = q_lcb - limit_price
    expected_profit = submit_edge * submitted_shares
    edge_density = submit_edge / limit_price
    effective_min_expected_profit = max(
        min_expected_profit,
        _LIVE_ENTRY_MIN_EXPECTED_PROFIT_USD,
    )
    effective_min_edge_density = max(
        min_edge_density,
        _LIVE_ENTRY_MIN_SUBMIT_EDGE_DENSITY,
    )
    if min_entry_price < 0.0:
        reason = "min_entry_price_negative"
    elif limit_price <= min_entry_price + 1e-9:
        reason = "entry_price_below_live_floor"
    elif min_expected_profit + 1e-9 < _LIVE_ENTRY_MIN_EXPECTED_PROFIT_USD:
        reason = "min_expected_profit_below_live_floor"
    elif min_edge_density + 1e-9 < _LIVE_ENTRY_MIN_SUBMIT_EDGE_DENSITY:
        reason = "min_submit_edge_density_below_live_floor"
    elif expected_edge <= 0.0:
        reason = "expected_edge_non_positive"
    elif submit_edge <= 0.0:
        reason = "submit_q_lcb_minus_limit_non_positive"
    elif expected_edge > submit_edge + 1e-6:
        reason = "expected_edge_exceeds_submit_edge"
    elif expected_profit + 1e-9 < effective_min_expected_profit:
        reason = "expected_profit_below_floor"
    elif edge_density + 1e-9 < effective_min_edge_density:
        reason = "submit_edge_density_below_floor"
    else:
        reason = ""
    if reason:
        return _capability_component(
            "entry_economics",
            allowed=False,
            reason=reason,
            q_live=q_live,
            q_lcb_5pct=q_lcb,
            expected_edge=expected_edge,
            limit_price=limit_price,
            submit_edge=submit_edge,
            expected_profit_usd=expected_profit,
            min_entry_price=min_entry_price,
            min_expected_profit_usd=min_expected_profit,
            live_min_expected_profit_usd=_LIVE_ENTRY_MIN_EXPECTED_PROFIT_USD,
            submit_edge_density=edge_density,
            min_submit_edge_density=min_edge_density,
            live_min_submit_edge_density=_LIVE_ENTRY_MIN_SUBMIT_EDGE_DENSITY,
            shares=submitted_shares,
        )
    direction = str(getattr(intent, "direction", "") or "")
    expected_side = "YES" if direction == "buy_yes" else "NO" if direction == "buy_no" else ""
    econ_side = str(economics.get("side") or "").upper()
    econ_source = str(economics.get("source") or "").strip()
    econ_cost = _float_field(economics.get("cost"))
    econ_edge_lcb = _float_field(economics.get("edge_lcb"))
    econ_optimal_delta_u = _float_field(economics.get("optimal_delta_u"))
    econ_false_edge_rate = _float_field(economics.get("false_edge_rate"))
    payoff_q_point = _float_field(economics.get("payoff_q_point"))
    payoff_q_lcb = _float_field(economics.get("payoff_q_lcb"))
    try:
        from src.strategy.fdr_filter import DEFAULT_FDR_ALPHA

        max_false_edge_rate = float(DEFAULT_FDR_ALPHA)
    except Exception:  # noqa: BLE001
        max_false_edge_rate = 0.05
    if econ_source != "qkernel_spine":
        reason = "qkernel_source_missing"
    elif expected_side and econ_side != expected_side:
        reason = "qkernel_side_mismatch"
    elif econ_cost is None:
        reason = "qkernel_cost_missing"
    elif limit_price > econ_cost + 1e-6:
        reason = "submit_price_worse_than_qkernel_cost"
    elif econ_edge_lcb is None or econ_edge_lcb <= 0.0:
        reason = "qkernel_edge_lcb_non_positive"
    elif econ_edge_lcb > submit_edge + 1e-6:
        reason = "qkernel_edge_lcb_exceeds_submit_edge"
    elif expected_edge > econ_edge_lcb + 1e-6:
        reason = "expected_edge_exceeds_qkernel_edge_lcb"
    elif econ_optimal_delta_u is None or econ_optimal_delta_u <= 0.0:
        reason = "qkernel_optimal_delta_u_non_positive"
    elif econ_false_edge_rate is None or not (0.0 < econ_false_edge_rate <= max_false_edge_rate):
        reason = "qkernel_false_edge_rate_blocks"
    elif payoff_q_point is None or payoff_q_lcb is None:
        reason = "qkernel_payoff_probability_missing"
    elif abs((payoff_q_lcb - econ_cost) - econ_edge_lcb) > 1e-6:
        reason = "qkernel_payoff_edge_inconsistent"
    elif payoff_q_point > q_live + 1e-6:
        reason = "qkernel_payoff_q_point_exceeds_q_live"
    elif payoff_q_lcb > q_lcb + 1e-6:
        reason = "qkernel_payoff_q_lcb_exceeds_q_lcb"
    elif economics.get("direction_law_ok") is not True:
        reason = "qkernel_direction_law_not_ok"
    elif economics.get("coherence_allows") is not True:
        reason = "qkernel_coherence_blocks"
    else:
        reason = ""
    if reason:
        return _capability_component(
            "entry_economics",
            allowed=False,
            reason=reason,
            q_live=q_live,
            q_lcb_5pct=q_lcb,
            expected_edge=expected_edge,
            submit_edge=submit_edge,
            qkernel_side=econ_side,
            expected_side=expected_side,
            qkernel_source=econ_source,
            limit_price=limit_price,
            qkernel_cost=econ_cost if econ_cost is not None else "",
            qkernel_edge_lcb=econ_edge_lcb if econ_edge_lcb is not None else "",
            qkernel_optimal_delta_u=(
                econ_optimal_delta_u if econ_optimal_delta_u is not None else ""
            ),
            qkernel_false_edge_rate=(
                econ_false_edge_rate if econ_false_edge_rate is not None else ""
            ),
            max_false_edge_rate=max_false_edge_rate,
            qkernel_payoff_q_point=payoff_q_point if payoff_q_point is not None else "",
            qkernel_payoff_q_lcb=payoff_q_lcb if payoff_q_lcb is not None else "",
        )
    return _capability_component(
        "entry_economics",
        q_live=q_live,
        q_lcb_5pct=q_lcb,
        expected_edge=expected_edge,
        limit_price=limit_price,
        submit_edge=submit_edge,
        expected_profit_usd=expected_profit,
        min_entry_price=min_entry_price,
        min_expected_profit_usd=min_expected_profit,
        live_min_expected_profit_usd=_LIVE_ENTRY_MIN_EXPECTED_PROFIT_USD,
        submit_edge_density=edge_density,
        min_submit_edge_density=min_edge_density,
        live_min_submit_edge_density=_LIVE_ENTRY_MIN_SUBMIT_EDGE_DENSITY,
        shares=submitted_shares,
        qkernel_source=econ_source,
        qkernel_side=econ_side,
        qkernel_cost=econ_cost,
        qkernel_edge_lcb=econ_edge_lcb,
        qkernel_false_edge_rate=econ_false_edge_rate,
    )


def _entry_actionable_certificate_component(
    conn: sqlite3.Connection,
    intent: ExecutionIntent,
    *,
    decision_id: str = "",
) -> dict:
    """Require the live actionable certificate to be persisted and currently valid."""

    certificate_hash = str(getattr(intent, "actionable_certificate_hash", None) or "").strip()
    if not certificate_hash:
        return _capability_component(
            "entry_actionable_certificate",
            allowed=False,
            reason="missing_actionable_certificate_hash",
        )
    if _decision_certificate_is_quarantined(conn, certificate_hash):
        return _capability_component(
            "entry_actionable_certificate",
            allowed=False,
            reason="actionable_certificate_quarantined",
            certificate_hash=certificate_hash,
        )
    attach_error = _attach_world_for_trade_certificate_read(conn)
    matching_schema = ""
    payload_json: str | None = None
    table_seen = False
    for schema in _attached_schema_names(conn):
        try:
            if not _table_exists_in_schema(conn, schema, "decision_certificates"):
                continue
            table_seen = True
            schema_sql = _quote_sql_identifier(schema)
            row = conn.execute(
                f"""
                SELECT certificate_type, mode, verifier_status, payload_json
                  FROM {schema_sql}.decision_certificates
                 WHERE certificate_hash = ?
                   AND certificate_type = 'ActionableTradeCertificate'
                   AND mode = 'LIVE'
                   AND verifier_status = 'VERIFIED'
                 LIMIT 1
                """,
                (certificate_hash,),
            ).fetchone()
        except sqlite3.Error as exc:
            return _capability_component(
                "entry_actionable_certificate",
                allowed=False,
                reason="decision_certificate_read_failed",
                certificate_hash=certificate_hash,
                error=str(exc),
            )
        if row is not None:
            matching_schema = schema
            try:
                payload_json = str(row["payload_json"] if isinstance(row, sqlite3.Row) else row[3])
            except (IndexError, KeyError, TypeError):
                payload_json = None
            break
    if not table_seen:
        if attach_error:
            return _capability_component(
                "entry_actionable_certificate",
                allowed=False,
                reason="decision_certificate_world_attach_failed",
                certificate_hash=certificate_hash,
                error=attach_error,
            )
        return _capability_component(
            "entry_actionable_certificate",
            allowed=False,
            reason="decision_certificates_table_unavailable",
            certificate_hash=certificate_hash,
        )
    if not matching_schema:
        if attach_error:
            return _capability_component(
                "entry_actionable_certificate",
                allowed=False,
                reason="decision_certificate_world_attach_failed",
                certificate_hash=certificate_hash,
                error=attach_error,
            )
        return _capability_component(
            "entry_actionable_certificate",
            allowed=False,
            reason="actionable_certificate_not_persisted_live_verified",
            certificate_hash=certificate_hash,
        )
    if not payload_json:
        return _capability_component(
            "entry_actionable_certificate",
            allowed=False,
            reason="actionable_certificate_payload_missing",
            certificate_hash=certificate_hash,
            certificate_schema=matching_schema,
        )
    try:
        from src.decision_kernel.verifier import _verify_actionable_payload

        payload = json.loads(payload_json)
        if not isinstance(payload, dict):
            raise ValueError("payload_json is not an object")
        _verify_actionable_payload(type("_PayloadCarrier", (), {"payload": payload})())
        mismatch_reason = _actionable_certificate_intent_mismatch_reason(
            payload,
            intent,
            decision_id=decision_id,
        )
        if mismatch_reason:
            raise ValueError(mismatch_reason)
    except Exception as exc:  # noqa: BLE001
        return _capability_component(
            "entry_actionable_certificate",
            allowed=False,
            reason="actionable_certificate_fails_current_verifier",
            certificate_hash=certificate_hash,
            certificate_schema=matching_schema,
            verification_error=str(exc),
        )
    return _capability_component(
        "entry_actionable_certificate",
        certificate_hash=certificate_hash,
        certificate_schema=matching_schema,
    )


def _direction_value(value: object) -> str:
    raw = getattr(value, "value", value)
    return str(raw or "").strip()


def _float_values_match(left: object, right: object, *, tolerance: float = 1e-9) -> bool:
    parsed_left = _float_field(left)
    parsed_right = _float_field(right)
    if parsed_left is None or parsed_right is None:
        return False
    return abs(parsed_left - parsed_right) <= tolerance


def _actionable_certificate_intent_mismatch_reason(
    payload: Mapping[str, Any],
    intent: ExecutionIntent,
    *,
    decision_id: str = "",
) -> str:
    """Ensure the durable actionable certificate authorizes this exact submit."""

    token_id = str(getattr(intent, "token_id", "") or "").strip()
    if token_id and str(payload.get("token_id") or "").strip() != token_id:
        return "actionable_certificate_token_mismatch"

    direction = _direction_value(getattr(intent, "direction", ""))
    if direction and str(payload.get("direction") or "").strip() != direction:
        return "actionable_certificate_direction_mismatch"

    snapshot_id = str(
        getattr(
            intent,
            "actionable_executable_snapshot_id",
            getattr(intent, "executable_snapshot_id", ""),
        )
        or ""
    ).strip()
    payload_snapshot_id = str(payload.get("executable_snapshot_id") or "").strip()
    if snapshot_id and payload_snapshot_id and payload_snapshot_id != snapshot_id:
        return "actionable_certificate_snapshot_mismatch"

    for field_name in ("q_live", "q_lcb_5pct"):
        intent_value = getattr(intent, field_name, None)
        if intent_value is not None and not _float_values_match(payload.get(field_name), intent_value):
            return f"actionable_certificate_{field_name}_mismatch"

    intent_economics = getattr(intent, "qkernel_execution_economics", None)
    payload_economics = payload.get("qkernel_execution_economics")
    if isinstance(intent_economics, Mapping):
        if not isinstance(payload_economics, Mapping):
            return "actionable_certificate_qkernel_economics_missing"
        for key in (
            "source",
            "side",
            "direction_law_ok",
            "coherence_allows",
        ):
            if payload_economics.get(key) != intent_economics.get(key):
                return f"actionable_certificate_qkernel_{key}_mismatch"
        for key in (
            "cost",
            "edge_lcb",
            "optimal_delta_u",
            "false_edge_rate",
            "payoff_q_point",
            "payoff_q_lcb",
        ):
            if key in intent_economics and not _float_values_match(
                payload_economics.get(key),
                intent_economics.get(key),
            ):
                return f"actionable_certificate_qkernel_{key}_mismatch"

    decision_text = str(decision_id or "").strip()
    if decision_text.startswith("edli_exec_cmd:"):
        parts = decision_text.split(":")
        if len(parts) < 5:
            return "actionable_certificate_edli_decision_id_malformed"
        event_id = parts[1]
        command_direction = parts[-1]
        command_token = parts[-2]
        final_intent_id = ":".join(parts[2:-2])
        if str(payload.get("event_id") or "").strip() != event_id:
            return "actionable_certificate_edli_event_mismatch"
        if str(payload.get("final_intent_id") or "").strip() != final_intent_id:
            return "actionable_certificate_edli_final_intent_mismatch"
        if token_id and command_token != token_id:
            return "actionable_certificate_edli_token_mismatch"
        if direction and command_direction != direction:
            return "actionable_certificate_edli_direction_mismatch"
    return ""


def _decision_certificate_is_quarantined(
    conn: sqlite3.Connection,
    certificate_hash: str,
) -> bool:
    if not certificate_hash:
        return False
    try:
        from src.state.decision_integrity_quarantine import (
            DECISION_CERTIFICATES_TABLE,
            REASON_INVALID_LIVE_ACTIONABLE,
            REASON_INVALID_LIVE_PARENT_MODE,
        )
    except Exception:
        DECISION_CERTIFICATES_TABLE = "decision_certificates"
        REASON_INVALID_LIVE_ACTIONABLE = "QUARANTINED_INVALID_LIVE_ACTIONABLE_CERTIFICATE"
        REASON_INVALID_LIVE_PARENT_MODE = "QUARANTINED_INVALID_LIVE_MONEY_PARENT_MODE"
    reason_codes = (REASON_INVALID_LIVE_ACTIONABLE, REASON_INVALID_LIVE_PARENT_MODE)
    for schema in _attached_schema_names(conn):
        try:
            if not _table_exists_in_schema(conn, schema, "decision_integrity_quarantine"):
                continue
            schema_sql = _quote_sql_identifier(schema)
            placeholders = ",".join("?" for _ in reason_codes)
            row = conn.execute(
                f"""
                SELECT 1
                  FROM {schema_sql}.decision_integrity_quarantine
                 WHERE table_name = ?
                   AND row_id = ?
                   AND reason_code IN ({placeholders})
                 LIMIT 1
                """,
                (DECISION_CERTIFICATES_TABLE, certificate_hash, *reason_codes),
            ).fetchone()
        except sqlite3.Error:
            continue
        if row is not None:
            return True
    return False


def _parse_sqlite_timestamp(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _entry_terminal_no_fill_material_change(
    *,
    prior_price: object | None,
    prior_size: object | None,
    candidate_price: object | None,
    candidate_shares: object | None,
) -> tuple[bool, dict]:
    def _to_decimal(value: object | None) -> Decimal | None:
        if value is None:
            return None
        text = str(value).strip()
        if not text:
            return None
        try:
            return Decimal(text)
        except (InvalidOperation, ValueError):
            return None

    prior_price_dec = _to_decimal(prior_price)
    candidate_price_dec = _to_decimal(candidate_price)
    prior_size_dec = _to_decimal(prior_size)
    candidate_size_dec = _to_decimal(candidate_shares)
    price_delta = (
        None
        if prior_price_dec is None or candidate_price_dec is None
        else abs(candidate_price_dec - prior_price_dec)
    )
    size_delta = (
        None
        if prior_size_dec is None or candidate_size_dec is None
        else abs(candidate_size_dec - prior_size_dec)
    )
    changed = (
        price_delta is not None
        and price_delta >= _ENTRY_TERMINAL_NO_FILL_PRICE_CHANGE_EPS
    ) or (
        size_delta is not None
        and size_delta >= _ENTRY_TERMINAL_NO_FILL_SIZE_CHANGE_EPS
    )
    return changed, {
        "prior_price": "" if prior_price_dec is None else str(prior_price_dec),
        "candidate_price": ""
        if candidate_price_dec is None
        else str(candidate_price_dec),
        "price_delta": "" if price_delta is None else str(price_delta),
        "prior_size": "" if prior_size_dec is None else str(prior_size_dec),
        "candidate_shares": "" if candidate_size_dec is None else str(candidate_size_dec),
        "size_delta": "" if size_delta is None else str(size_delta),
    }


def _entry_same_token_cooldown_component(
    conn: sqlite3.Connection,
    *,
    token_id: str,
    candidate_position_id: str,
    limit_price: float | None = None,
    shares: float | None = None,
    now: datetime | None = None,
) -> dict:
    """Throttle repeated ENTRY attempts for a top-ranked token."""

    token = str(token_id or "").strip()
    if not token:
        return {
            "component": "entry_same_token_cooldown",
            "allowed": False,
            "reason": "missing_token_id",
        }
    if not _table_exists(conn, "venue_commands"):
        return {
            "component": "entry_same_token_cooldown",
            "allowed": True,
            "reason": "missing_venue_commands_table",
        }
    command_columns = _table_column_names(conn, "venue_commands")
    has_price_size = "price" in command_columns and "size" in command_columns
    select_price_size = ", price, size" if has_price_size else ""
    rows = conn.execute(
        f"""
        SELECT command_id, position_id, state, created_at, updated_at{select_price_size}
        FROM venue_commands
        WHERE intent_kind = 'ENTRY'
          AND side = 'BUY'
          AND token_id = ?
          AND position_id != ?
        ORDER BY updated_at DESC, created_at DESC
        """,
        (token, candidate_position_id),
    ).fetchall()
    if not rows:
        return {
            "component": "entry_same_token_cooldown",
            "allowed": True,
            "reason": "allowed_no_prior_entry",
            "token_id": token,
        }
    command_id = ""
    position_id = ""
    state = ""
    created_at = ""
    updated_at = ""
    prior_price: object | None = None
    prior_size: object | None = None
    terminal_no_fill_row: tuple[
        str, str, str, object, object, object | None, object | None
    ] | None = None
    for row in rows:
        if isinstance(row, sqlite3.Row):
            command_id = str(row["command_id"])
            position_id = str(row["position_id"])
            state = str(row["state"])
            created_at = row["created_at"]
            updated_at = row["updated_at"]
            row_price = row["price"] if has_price_size else None
            row_size = row["size"] if has_price_size else None
        else:
            command_id = str(row[0])
            position_id = str(row[1])
            state = str(row[2])
            created_at = row[3]
            updated_at = row[4]
            row_price = row[5] if has_price_size else None
            row_size = row[6] if has_price_size else None
        if _entry_terminal_command_has_no_fill_exposure(
            conn,
            command_id=command_id,
            state=state,
        ):
            if terminal_no_fill_row is None:
                terminal_no_fill_row = (
                    command_id,
                    position_id,
                    state,
                    created_at,
                    updated_at,
                    row_price,
                    row_size,
                )
            continue
        prior_price = row_price
        prior_size = row_size
        break
    else:
        if terminal_no_fill_row is None:
            return {
                "component": "entry_same_token_cooldown",
                "allowed": True,
                "reason": "allowed_no_blocking_prior_entries",
                "token_id": token,
            }
        (
            command_id,
            position_id,
            state,
            created_at,
            updated_at,
            prior_price,
            prior_size,
        ) = terminal_no_fill_row
    last_seen = _parse_sqlite_timestamp(updated_at) or _parse_sqlite_timestamp(created_at)
    if last_seen is None:
        return {
            "component": "entry_same_token_cooldown",
            "allowed": False,
            "reason": "prior_entry_timestamp_unparseable",
            "existing_command_id": command_id,
            "existing_position_id": position_id,
            "existing_command_state": state,
        }
    now_utc = now or datetime.now(timezone.utc)
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=timezone.utc)
    age_seconds = (now_utc.astimezone(timezone.utc) - last_seen).total_seconds()
    terminal_no_fill = _entry_terminal_command_has_no_fill_exposure(
        conn,
        command_id=command_id,
        state=state,
    )
    if terminal_no_fill:
        changed, change_details = _entry_terminal_no_fill_material_change(
            prior_price=prior_price,
            prior_size=prior_size,
            candidate_price=limit_price,
            candidate_shares=shares,
        )
        if changed:
            return {
                "component": "entry_same_token_cooldown",
                "allowed": True,
                "reason": "allowed_terminal_no_fill_redecision_material_change",
                "age_seconds": int(age_seconds),
                "existing_command_id": command_id,
                "existing_command_state": state,
                **change_details,
            }

    remaining_seconds = _ENTRY_SAME_TOKEN_COOLDOWN_SECONDS - age_seconds
    if remaining_seconds > 0:
        return {
            "component": "entry_same_token_cooldown",
            "allowed": False,
            "reason": (
                "same_token_terminal_no_fill_cooling_down"
                if terminal_no_fill
                else "same_token_entry_cooling_down"
            ),
            "cooldown_seconds": _ENTRY_SAME_TOKEN_COOLDOWN_SECONDS,
            "remaining_seconds": int(remaining_seconds),
            "existing_command_id": command_id,
            "existing_position_id": position_id,
            "existing_command_state": state,
            "existing_updated_at": str(updated_at or ""),
            "existing_created_at": str(created_at or ""),
            "existing_price": str(prior_price or ""),
            "existing_size": str(prior_size or ""),
            "candidate_price": str(limit_price or ""),
            "candidate_shares": str(shares or ""),
        }
    return {
        "component": "entry_same_token_cooldown",
        "allowed": True,
        "reason": (
            "allowed_terminal_no_fill_redecision_cooldown_elapsed"
            if terminal_no_fill
            else "allowed_cooldown_elapsed"
        ),
        "cooldown_seconds": _ENTRY_SAME_TOKEN_COOLDOWN_SECONDS,
        "age_seconds": int(age_seconds),
        "existing_command_id": command_id,
        "existing_command_state": state,
    }


def _entry_duplicate_same_token_component(
    conn: sqlite3.Connection,
    *,
    token_id: str,
    candidate_position_id: str,
) -> dict:
    """Final pre-submit duplicate-exposure gate for live entry orders.

    Evaluator-level dedup can be bypassed by retries, stale projections, or
    distinct decision/size idempotency keys. The executor is the last boundary
    before command persistence and SDK submission, so it must independently
    reject same-token open exposure.
    """

    token = str(token_id or "").strip()
    if not token:
        return {
            "component": "entry_duplicate_same_token",
            "allowed": False,
            "reason": "missing_token_id",
        }

    if _table_exists(conn, "position_current"):
        phase_placeholders = ",".join("?" for _ in _ENTRY_DUPLICATE_NON_OPEN_PHASES)
        rows = conn.execute(
            f"""
            SELECT position_id, phase, order_id, shares, cost_basis_usd
            FROM position_current
            WHERE (token_id = ? OR no_token_id = ?)
              AND position_id != ?
              AND phase NOT IN ({phase_placeholders})
            """,
            (
                token,
                token,
                candidate_position_id,
                *sorted(_ENTRY_DUPLICATE_NON_OPEN_PHASES),
            ),
        ).fetchall()
        for row in rows:
            if _pending_entry_terminal_no_fill_allows_entry(conn, row):
                continue
            return {
                "component": "entry_duplicate_same_token",
                "allowed": False,
                "reason": "open_position_same_token",
                "existing_position_id": str(row["position_id"] if isinstance(row, sqlite3.Row) else row[0]),
                "existing_phase": str(row["phase"] if isinstance(row, sqlite3.Row) else row[1]),
            }

    if _table_exists(conn, "venue_commands"):
        non_open_phase_placeholders = ",".join("?" for _ in _ENTRY_DUPLICATE_NON_OPEN_PHASES)
        open_state_placeholders = ",".join("?" for _ in _ENTRY_DUPLICATE_OPEN_COMMAND_STATES)
        terminal_no_exposure_placeholders = ",".join(
            "?" for _ in _ENTRY_DUPLICATE_TERMINAL_NO_EXPOSURE_COMMAND_STATES
        )
        rows = conn.execute(
            f"""
            SELECT vc.command_id, vc.position_id, vc.state, pc.phase
            FROM venue_commands vc
            LEFT JOIN position_current pc ON pc.position_id = vc.position_id
            WHERE vc.intent_kind = 'ENTRY'
              AND vc.side = 'BUY'
              AND vc.token_id = ?
              AND vc.position_id != ?
              AND (
                    vc.state IN ({open_state_placeholders})
                 OR (
                        vc.state = 'FILLED'
                    AND (
                            pc.phase IS NULL
                         OR pc.phase NOT IN ({non_open_phase_placeholders})
                    )
                 )
                 OR (
                        vc.state NOT IN ({terminal_no_exposure_placeholders})
                    AND vc.state != 'FILLED'
                    AND vc.state NOT IN ({open_state_placeholders})
                 )
              )
            ORDER BY vc.updated_at DESC, vc.created_at DESC
            """,
            (
                token,
                candidate_position_id,
                *sorted(_ENTRY_DUPLICATE_OPEN_COMMAND_STATES),
                *sorted(_ENTRY_DUPLICATE_NON_OPEN_PHASES),
                *sorted(_ENTRY_DUPLICATE_TERMINAL_NO_EXPOSURE_COMMAND_STATES),
                *sorted(_ENTRY_DUPLICATE_OPEN_COMMAND_STATES),
            ),
        ).fetchall()
        for row in rows:
            if isinstance(row, sqlite3.Row):
                command_id = str(row["command_id"])
                position_id = str(row["position_id"])
                state = str(row["state"])
                phase = row["phase"]
            else:
                command_id = str(row[0])
                position_id = str(row[1])
                state = str(row[2])
                phase = row[3]
            if (
                _entry_terminal_command_has_no_fill_exposure(
                    conn,
                    command_id=command_id,
                    state=state,
                )
            ):
                continue
            return {
                "component": "entry_duplicate_same_token",
                "allowed": False,
                "reason": "open_or_filled_entry_command_same_token",
                "existing_command_id": command_id,
                "existing_position_id": position_id,
                "existing_command_state": state,
                "existing_phase": "" if phase is None else str(phase),
            }

    return {
        "component": "entry_duplicate_same_token",
        "allowed": True,
        "reason": "allowed",
        "token_id": token,
    }


def _venue_submit_amount_precision_rejection_reason(
    intent: ExecutionIntent,
    *,
    shares: float,
    order_type: str,
) -> str | None:
    from src.contracts.execution_intent import venue_submit_amount_precision_error

    direction = getattr(getattr(intent, "direction", ""), "value", getattr(intent, "direction", ""))
    intent_tick = getattr(intent, "tick_size", None)
    return venue_submit_amount_precision_error(
        direction=str(direction),
        final_limit_price=Decimal(str(intent.limit_price)),
        submitted_shares=Decimal(str(shares)),
        order_type=order_type,
        tick_size=intent_tick,
    )


def _allocation_payload_for_intent(intent: ExecutionIntent) -> dict[str, str]:
    """Return JSON-safe A2 allocation metadata for SUBMIT_REQUESTED payloads."""

    market_id = _json_safe_string(getattr(intent, "market_id", ""), "")
    event_id = _json_safe_string(getattr(intent, "event_id", None), market_id)
    resolution_window = _json_safe_string(getattr(intent, "resolution_window", None), "default") or "default"
    correlation_key = _json_safe_string(getattr(intent, "correlation_key", None), event_id or market_id)
    return {
        "event_id": event_id,
        "resolution_window": resolution_window,
        "correlation_key": correlation_key,
    }


def _is_polymarket_geoblock_403(exc: Exception) -> bool:
    message = str(exc)
    return (
        type(exc).__name__ == "PolyApiException"
        and "status_code=403" in message
        and "Trading restricted in your region" in message
        and "geoblock" in message
    )


def _is_polymarket_invalid_amount_400(exc: Exception) -> bool:
    if type(exc).__name__ != "PolyApiException":
        return False
    return _is_polymarket_invalid_amount_400_message(str(exc))


def _is_polymarket_invalid_amount_400_message(message: str) -> bool:
    if "status_code=400" not in message:
        return False
    normalized = " ".join(message.split())
    precision_rejection = (
        "invalid amounts" in normalized
        and "maker amount" in normalized
        and "taker amount" in normalized
    )
    marketable_buy_min_rejection = (
        "invalid amount" in normalized
        and "marketable BUY order" in normalized
        and ("min size: $1" in normalized or "min size: 1" in normalized)
    )
    return precision_rejection or marketable_buy_min_rejection


def _is_polymarket_invalid_signature_400(exc: Exception) -> bool:
    if type(exc).__name__ != "PolyApiException":
        return False
    message = str(exc)
    if "status_code=400" not in message:
        return False
    return "invalid POLY_GNOSIS_SAFE signature" in message


def _geoblock_rejection_payload(exc: Exception, *, idempotency_key: str) -> dict:
    return {
        "reason": "venue_rejected_geoblock_403",
        "exception_type": type(exc).__name__,
        "exception_message": str(exc),
        "idempotency_key": idempotency_key,
        "proof_class": "deterministic_venue_geoblock_403",
        "venue_order_created": False,
    }


def _invalid_amount_rejection_payload(exc: Exception, *, idempotency_key: str) -> dict:
    return {
        "reason": "venue_rejected_invalid_amount_400",
        "exception_type": type(exc).__name__,
        "exception_message": str(exc),
        "idempotency_key": idempotency_key,
        "proof_class": "deterministic_venue_invalid_amount_400",
        "venue_order_created": False,
    }


def _invalid_signature_rejection_payload(exc: Exception, *, idempotency_key: str) -> dict:
    return {
        "reason": "venue_auth_invalid_signature_400",
        "exception_type": type(exc).__name__,
        "exception_message": str(exc),
        "idempotency_key": idempotency_key,
        "proof_class": "deterministic_venue_auth_signature_400",
        "venue_order_created": False,
    }


def _is_polymarket_deterministic_400(exc: Exception) -> bool:
    """Any Polymarket ``status_code=400`` is a request-VALIDATION rejection.

    A 400 means the venue rejected the HTTP request at validation BEFORE creating
    an order (``venue_order_created=False`` always). It is therefore a DETERMINISTIC
    submit rejection with NO venue side effect — it must NEVER be classified as an
    ``UNKNOWN_SIDE_EFFECT``. That mis-classification latches the risk governor's
    kill switch (``unknown_side_effect_limit=0``), which blocked EVERY subsequent
    submission for ~8h on 2026-06-15 off a single ``'invalid post-...'`` 400 (the
    specific ``invalid_amount`` 400 was already handled; this generalizes the class
    so any 400 message — invalid post, tick, etc. — is a clean reject, not a latch).
    400s are also non-retryable verbatim (same request → same 400); the family
    re-decides next cycle on fresh inputs.
    """
    return type(exc).__name__ == "PolyApiException" and "status_code=400" in str(exc)


def _generic_400_rejection_payload(exc: Exception, *, idempotency_key: str) -> dict:
    return {
        "reason": "venue_rejected_400",
        "exception_type": type(exc).__name__,
        "exception_message": str(exc),
        "idempotency_key": idempotency_key,
        "proof_class": "deterministic_venue_400",
        "venue_order_created": False,
    }


def _deterministic_submit_rejection_payload(
    exc: Exception,
    *,
    idempotency_key: str,
) -> dict | None:
    if _is_polymarket_geoblock_403(exc):
        return _geoblock_rejection_payload(exc, idempotency_key=idempotency_key)
    if _is_polymarket_invalid_amount_400(exc):
        return _invalid_amount_rejection_payload(exc, idempotency_key=idempotency_key)
    if _is_polymarket_invalid_signature_400(exc):
        return _invalid_signature_rejection_payload(exc, idempotency_key=idempotency_key)
    # GENERAL 400 fallback (kept LAST so the specific invalid_amount reason_code wins
    # for its downstream no-verbatim-retry handling): every other 400 is still a
    # deterministic venue rejection, never an unknown side effect / governor latch.
    if _is_polymarket_deterministic_400(exc):
        return _generic_400_rejection_payload(exc, idempotency_key=idempotency_key)
    return None


def _canonical_payload_hash(payload: object) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _jsonable_payload(payload: object) -> object:
    return json.loads(json.dumps(payload, sort_keys=True, default=str))


def _submit_result_envelope(result: dict) -> dict:
    if not isinstance(result, dict):
        return {}
    envelope = result.get("_venue_submission_envelope")
    return envelope if isinstance(envelope, dict) else {}


def _submit_result_raw_response(result: dict) -> dict:
    envelope = _submit_result_envelope(result)
    raw_json = envelope.get("raw_response_json")
    if not raw_json:
        return {}
    try:
        parsed = json.loads(str(raw_json))
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _first_submit_value(result: dict, *keys: str, raw_first: bool = False):
    if not isinstance(result, dict):
        return None
    raw = _submit_result_raw_response(result)
    sources = (raw, result) if raw_first else (result, raw)
    for source in sources:
        for key in keys:
            value = source.get(key)
            if value not in (None, ""):
                return value
    envelope = _submit_result_envelope(result)
    for key in keys:
        value = envelope.get(key)
        if value not in (None, ""):
            return value
    return None


def _venue_submit_status(result: dict) -> str:
    return str(
        _first_submit_value(result, "status", "state", raw_first=True) or ""
    ).upper()


def _normalised_order_side(value: object) -> str:
    return str(value or "").strip().upper()


def _venue_submit_side(result: dict, *, side: str | None = None) -> str:
    explicit = _normalised_order_side(side)
    if explicit:
        return explicit
    return _normalised_order_side(_first_submit_value(result, "side"))


def _decimal_or_none(value: object) -> Decimal | None:
    try:
        parsed = Decimal(str(value))
    except Exception:
        return None
    return parsed if parsed.is_finite() else None


def _positive_decimal_or_none(value: object) -> Decimal | None:
    parsed = _decimal_or_none(value)
    return parsed if parsed is not None and parsed > 0 else None


def _decimal_text(value: Decimal) -> str:
    text = format(value.normalize(), "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


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


def _submit_result_string_sequence(result: dict, *keys: str) -> tuple[str, ...]:
    for key in keys:
        values = _string_sequence_from_value(_first_submit_value(result, key))
        if values:
            return values
    return ()


def _venue_submit_trade_ids(result: dict) -> tuple[str, ...]:
    return _submit_result_string_sequence(
        result,
        "tradeIDs",
        "tradeIds",
        "trade_ids",
        "associate_trades",
        "trades",
    )


def _venue_submit_transaction_hashes(result: dict) -> tuple[str, ...]:
    return _submit_result_string_sequence(
        result,
        "transactionsHashes",
        "transactionHashes",
        "transaction_hashes",
        "txHashes",
        "tx_hashes",
    )


def _venue_submit_order_fact_state(result: dict) -> str:
    status = _venue_submit_status(result)
    if status in {"MATCHED", "FILLED"}:
        return "MATCHED"
    if status in {"PARTIALLY_MATCHED", "PARTIAL", "PARTIALLY_FILLED"}:
        return "PARTIALLY_MATCHED"
    return "LIVE"


def _venue_submit_matched_size(
    result: dict,
    *,
    side: str | None = None,
) -> str:
    for key in (
        "matched_size",
        "matchedSize",
        "size_matched",
        "sizeMatched",
    ):
        value = _first_submit_value(result, key)
        if value not in (None, ""):
            return str(value)
    side_value = _venue_submit_side(result, side=side)
    amount_keys = (
        ("makingAmount", "making_amount")
        if side_value == "SELL"
        else ("takingAmount", "taking_amount")
    )
    value = _first_submit_value(result, *amount_keys)
    if value not in (None, ""):
        return str(value)
    return "0"


def _venue_submit_remaining_size(
    result: dict,
    fallback_size: float | Decimal,
    *,
    matched_size: str | None = None,
    side: str | None = None,
) -> str:
    for key in ("remaining_size", "remainingSize"):
        value = _first_submit_value(result, key)
        if value not in (None, ""):
            return str(value)
    status = _venue_submit_status(result)
    matched = _decimal_or_none(
        matched_size
        if matched_size is not None
        else _venue_submit_matched_size(result, side=side)
    )
    fallback = _decimal_or_none(fallback_size)
    if status in {"MATCHED", "FILLED"} and matched is not None:
        if matched > Decimal("0"):
            return "0"
        if fallback is not None and fallback > matched:
            return _decimal_text(fallback - matched)
        return "0"
    for key in ("size", "original_size", "originalSize"):
        value = _first_submit_value(result, key)
        if value not in (None, ""):
            return str(value)
    return str(fallback_size)


def _venue_submit_fill_price(
    result: dict,
    *,
    side: str | None = None,
) -> str | None:
    making = _positive_decimal_or_none(_first_submit_value(result, "makingAmount", "making_amount"))
    taking = _positive_decimal_or_none(_first_submit_value(result, "takingAmount", "taking_amount"))
    if making is not None and taking is not None:
        if _venue_submit_side(result, side=side) == "SELL":
            return _decimal_text(taking / making)
        return _decimal_text(making / taking)
    for key in ("avgPrice", "avg_price", "fillPrice", "fill_price", "price"):
        value = _first_submit_value(result, key)
        if _positive_decimal_or_none(value) is not None:
            return str(value)
    return None


def _venue_fill_covers_submit(matched_size: str, submitted_size: float | Decimal) -> bool:
    matched = _decimal_or_none(matched_size)
    submitted = _decimal_or_none(submitted_size)
    return matched is not None and submitted is not None and matched >= submitted


def _merge_point_order_fill_truth(result: dict, point_order: dict | None) -> dict:
    if not point_order:
        return result
    merged = dict(result)
    for key, value in point_order.items():
        if value not in (None, ""):
            merged.setdefault(key, value)
    return merged


def _json_safe_string(value, fallback: str = "") -> str:
    if value is None:
        return str(fallback or "")
    if isinstance(value, (str, int, float, bool)):
        text = str(value)
        return text if text else str(fallback or "")
    return str(fallback or "")


def _buy_order_notional_micro(intent: ExecutionIntent, shares: float) -> int:
    """Return worst-case pUSD spend for the actual submitted BUY order.

    Entry sizing rounds BUY shares up to the venue's 0.01-share grid. The
    collateral gate must therefore use submitted `shares * limit_price`, not the
    original target_size_usd, otherwise a target-sized balance can pass preflight
    and still underfund the quantized order.
    """

    notional = Decimal(str(shares)) * Decimal(str(intent.limit_price)) * Decimal(1_000_000)
    return int(notional.to_integral_value(rounding=ROUND_CEILING))


def _assert_collateral_allows_buy(
    intent: ExecutionIntent,
    *,
    spend_micro: int | None = None,
    conn: sqlite3.Connection | None = None,
) -> dict:
    """Fail before command persistence or SDK contact when pUSD is insufficient."""
    from src.state.collateral_ledger import CollateralLedger, assert_buy_preflight

    if conn is not None:
        CollateralLedger(conn).buy_preflight(intent, spend_micro=spend_micro)
    else:
        assert_buy_preflight(intent, spend_micro=spend_micro)
    return _capability_component("collateral_ledger", collateral="pUSD", spend_micro=spend_micro or 0)


def _refresh_entry_collateral_snapshot_for_submit(conn: sqlite3.Connection) -> dict:
    """Refresh collateral truth synchronously on the submit path before preflight."""
    from src.execution.collateral import refresh_collateral_snapshot_for_submit

    return refresh_collateral_snapshot_for_submit(
        conn,
        action="entry_submit",
        reuse_fresh_snapshot=True,
    )


def _refresh_exit_collateral_snapshot_for_submit(
    conn: sqlite3.Connection,
    *,
    token_id: str | None = None,
    shares: float | None = None,
) -> dict:
    """Refresh CTF inventory truth before exit sell preflight."""
    from src.execution.collateral import refresh_collateral_snapshot_for_submit

    _ = shares
    return refresh_collateral_snapshot_for_submit(
        conn,
        action="exit_submit",
        reuse_fresh_snapshot=False,
        token_id=token_id,
    )


def _assert_collateral_allows_sell(
    token_id: str,
    shares: float,
    *,
    conn: sqlite3.Connection | None = None,
) -> dict:
    """Fail before command persistence or SDK contact when CTF inventory is insufficient."""
    from src.state.collateral_ledger import CollateralLedger, assert_sell_preflight

    if conn is not None:
        CollateralLedger(conn).sell_preflight(token_id=token_id, size=shares)
    else:
        assert_sell_preflight(token_id, shares)
    return _capability_component("collateral_ledger", collateral="CTF", token_id=token_id, shares=shares)


def _capability_component(component: str, *, allowed: bool = True, reason: str = "allowed", **details) -> dict:
    payload = {
        "component": component,
        "allowed": bool(allowed),
        "reason": str(reason),
    }
    if details:
        payload["details"] = {
            key: _json_safe_string(value, "") if not isinstance(value, (int, float, bool)) else value
            for key, value in details.items()
        }
    return payload


def _component_from_result(component: str, result=None, **details) -> dict:
    payload = _capability_component(
        component,
        allowed=bool(getattr(result, "allowed", True)),
        reason=str(getattr(result, "reason", "allowed")),
        **details,
    )
    for attr in (
        "requested_micro",
        "remaining_market_capacity_micro",
        "confirmed_exposure_micro",
        "optimistic_exposure_micro",
        "weighted_existing_exposure_micro",
        "reduce_only",
    ):
        if hasattr(result, attr):
            payload.setdefault("details", {})[attr] = getattr(result, attr)
    return payload


_PRE_SUBMIT_AUDIT_ONLY_DECISION_SOURCE_ERRORS = frozenset(
    {
        "missing_observation_time",
        "missing_observation_available_at",
        "missing_zeus_submit_intent_time",
        "missing_venue_ack_time",
        "clock_drift_warning",
    }
)


def _pre_submit_decision_source_errors(
    context: DecisionSourceContext,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Split source blockers from audit fields unavailable before venue submit."""

    errors = context.integrity_errors()
    blockers = tuple(
        error
        for error in errors
        if error not in _PRE_SUBMIT_AUDIT_ONLY_DECISION_SOURCE_ERRORS
    )
    deferred = tuple(
        error
        for error in errors
        if error in _PRE_SUBMIT_AUDIT_ONLY_DECISION_SOURCE_ERRORS
    )
    return blockers, deferred


def _entry_decision_source_component(intent: ExecutionIntent) -> dict:
    context = getattr(intent, "decision_source_context", None)
    if context is None:
        return _capability_component(
            "decision_source_integrity",
            allowed=False,
            reason="missing_decision_source_context",
        )
    errors, deferred_errors = _pre_submit_decision_source_errors(context)
    details = context.capability_details()
    if deferred_errors:
        details = {
            **details,
            "pre_submit_deferred_audit_errors": ",".join(deferred_errors),
        }
    if errors:
        return _capability_component(
            "decision_source_integrity",
            allowed=False,
            reason="invalid_decision_source_context",
            errors=",".join(errors),
            **details,
        )
    return _capability_component(
        "decision_source_integrity",
        **details,
    )


def _corrected_entry_identity_details(intent: ExecutionIntent) -> dict[str, str] | None:
    snapshot_hash = _json_safe_string(getattr(intent, "executable_snapshot_hash", ""), "")
    cost_basis_id = _json_safe_string(getattr(intent, "executable_cost_basis_id", ""), "")
    cost_basis_hash = _json_safe_string(getattr(intent, "executable_cost_basis_hash", ""), "")
    pricing_version = _json_safe_string(getattr(intent, "pricing_semantics_id", ""), "")
    snapshot_id = _json_safe_string(getattr(intent, "executable_snapshot_id", ""), "")
    has_corrected_identity = any(
        (snapshot_hash, cost_basis_id, cost_basis_hash, pricing_version)
    )
    if not has_corrected_identity:
        return None
    return {
        "snapshot_id": snapshot_id,
        "snapshot_hash": snapshot_hash,
        "cost_basis_id": cost_basis_id,
        "cost_basis_hash": cost_basis_hash,
        "pricing_semantics_id": pricing_version,
    }


def _corrected_entry_identity_component(
    conn: sqlite3.Connection,
    intent: ExecutionIntent,
) -> dict:
    """Verify corrected FinalExecutionIntent identity survived the legacy envelope."""

    details = _corrected_entry_identity_details(intent)
    if details is None:
        return _capability_component(
            "corrected_execution_identity",
            reason="legacy_execution_intent",
        )

    from src.contracts.execution_intent import CORRECTED_PRICING_SEMANTICS_VERSION

    snapshot_id = details["snapshot_id"]
    snapshot_hash = details["snapshot_hash"]
    cost_basis_id = details["cost_basis_id"]
    cost_basis_hash = details["cost_basis_hash"]
    pricing_version = details["pricing_semantics_id"]
    missing = [
        name
        for name, value in details.items()
        if name != "pricing_semantics_id" and not value
    ]
    if missing:
        return _capability_component(
            "corrected_execution_identity",
            allowed=False,
            reason="missing_corrected_execution_identity",
            missing=",".join(missing),
            **details,
        )
    if pricing_version != CORRECTED_PRICING_SEMANTICS_VERSION:
        return _capability_component(
            "corrected_execution_identity",
            allowed=False,
            reason="unsupported_pricing_semantics_id",
            **details,
        )
    if len(snapshot_hash) != 64 or len(cost_basis_hash) != 64:
        return _capability_component(
            "corrected_execution_identity",
            allowed=False,
            reason="invalid_identity_hash",
            **details,
        )
    expected_cost_basis_id = f"cost_basis:{cost_basis_hash[:16]}"
    if cost_basis_id != expected_cost_basis_id:
        return _capability_component(
            "corrected_execution_identity",
            allowed=False,
            reason="cost_basis_id_hash_mismatch",
            expected_cost_basis_id=expected_cost_basis_id,
            **details,
        )

    from src.state.snapshot_repo import get_snapshot

    try:
        snapshot = get_snapshot(conn, snapshot_id)
    except sqlite3.OperationalError as exc:
        return _capability_component(
            "corrected_execution_identity",
            allowed=False,
            reason="snapshot_lookup_unavailable",
            error=str(exc),
            **details,
        )
    if snapshot is None:
        return _capability_component(
            "corrected_execution_identity",
            allowed=False,
            reason="snapshot_missing",
            **details,
        )
    actual_hash = str(snapshot.executable_snapshot_hash or "")
    if actual_hash != snapshot_hash:
        return _capability_component(
            "corrected_execution_identity",
            allowed=False,
            reason="snapshot_hash_mismatch",
            actual_snapshot_hash=actual_hash,
            **details,
        )
    return _capability_component(
        "corrected_execution_identity",
        **details,
    )


def _corrected_identity_from_command_events(
    conn: sqlite3.Connection,
    command_id: str,
) -> dict[str, str] | None:
    from src.state.venue_command_repo import list_events

    events = list_events(conn, command_id)
    for event in reversed(events):
        if event.get("event_type") != "SUBMIT_REQUESTED":
            continue
        payload = event.get("payload_json")
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except ValueError:
                return None
        if not isinstance(payload, dict):
            return None
        capability = payload.get("execution_capability")
        if not isinstance(capability, dict):
            return None
        components = capability.get("components")
        if not isinstance(components, list):
            return None
        for component in components:
            if not isinstance(component, dict):
                continue
            if component.get("component") != "corrected_execution_identity":
                continue
            details = component.get("details")
            if not isinstance(details, dict):
                return None
            return {
                "snapshot_id": _json_safe_string(details.get("snapshot_id"), ""),
                "snapshot_hash": _json_safe_string(details.get("snapshot_hash"), ""),
                "cost_basis_id": _json_safe_string(details.get("cost_basis_id"), ""),
                "cost_basis_hash": _json_safe_string(details.get("cost_basis_hash"), ""),
                "pricing_semantics_id": _json_safe_string(
                    details.get("pricing_semantics_id"),
                    "",
                ),
            }
    return None


def _corrected_existing_command_mismatch_reason(
    conn: sqlite3.Connection,
    intent: ExecutionIntent,
    existing_command: dict,
) -> str | None:
    expected = _corrected_entry_identity_details(intent)
    if expected is None:
        return None
    command_id = _json_safe_string(existing_command.get("command_id"), "")
    if not command_id:
        return "existing_command_missing_command_id"
    existing_snapshot_id = _json_safe_string(existing_command.get("snapshot_id"), "")
    if existing_snapshot_id and existing_snapshot_id != expected["snapshot_id"]:
        return "existing_command_snapshot_id_mismatch"
    observed = _corrected_identity_from_command_events(conn, command_id)
    if observed is None:
        return "existing_command_missing_corrected_identity"
    for field_name, expected_value in expected.items():
        if observed.get(field_name) != expected_value:
            return f"existing_command_{field_name}_mismatch"
    return None


def _reject_corrected_existing_command_mismatch(
    *,
    trade_id: str,
    intent: ExecutionIntent,
    shares: float,
    idem_value: str,
    reason: str,
) -> "OrderResult":
    return OrderResult(
        trade_id=trade_id,
        status="rejected",
        reason=f"corrected_execution_identity:{reason}",
        submitted_price=intent.limit_price,
        shares=shares,
        order_role="entry",
        idempotency_key=idem_value,
    )


def _exit_snapshot_identity_details(intent) -> dict[str, str] | None:
    snapshot_hash = _json_safe_string(getattr(intent, "executable_snapshot_hash", ""), "")
    if not snapshot_hash:
        return None
    return {
        "snapshot_id": _json_safe_string(getattr(intent, "executable_snapshot_id", ""), ""),
        "snapshot_hash": snapshot_hash,
    }


def _exit_idempotency_decision_component(effective_decision_id: str, intent) -> str:
    """Scope exit idempotency to the executable snapshot while keeping decision_id stable."""

    details = _exit_snapshot_identity_details(intent)
    if details is None:
        return effective_decision_id
    snapshot_id = details.get("snapshot_id", "")
    snapshot_hash = details.get("snapshot_hash", "")
    if not snapshot_id or not snapshot_hash:
        return effective_decision_id
    return f"{effective_decision_id}:exit_snapshot:{snapshot_id}:{snapshot_hash}"


def _exit_snapshot_identity_component(
    conn: sqlite3.Connection,
    intent,
) -> dict:
    """Verify corrected exit executable snapshot identity survived to submit."""

    details = _exit_snapshot_identity_details(intent)
    if details is None:
        return _capability_component(
            "exit_snapshot_identity",
            reason="legacy_exit_order_intent",
        )

    snapshot_id = details["snapshot_id"]
    snapshot_hash = details["snapshot_hash"]
    missing = [name for name, value in details.items() if not value]
    if missing:
        return _capability_component(
            "exit_snapshot_identity",
            allowed=False,
            reason="missing_exit_snapshot_identity",
            missing=",".join(missing),
            **details,
        )
    if len(snapshot_hash) != 64:
        return _capability_component(
            "exit_snapshot_identity",
            allowed=False,
            reason="invalid_snapshot_hash",
            **details,
        )

    from src.state.snapshot_repo import get_snapshot

    try:
        snapshot = get_snapshot(conn, snapshot_id)
    except sqlite3.OperationalError as exc:
        return _capability_component(
            "exit_snapshot_identity",
            allowed=False,
            reason="snapshot_lookup_unavailable",
            error=str(exc),
            **details,
        )
    if snapshot is None:
        return _capability_component(
            "exit_snapshot_identity",
            allowed=False,
            reason="snapshot_missing",
            **details,
        )
    actual_hash = str(snapshot.executable_snapshot_hash or "")
    if actual_hash != snapshot_hash:
        return _capability_component(
            "exit_snapshot_identity",
            allowed=False,
            reason="snapshot_hash_mismatch",
            actual_snapshot_hash=actual_hash,
            **details,
        )
    return _capability_component(
        "exit_snapshot_identity",
        **details,
    )


def _exit_snapshot_identity_from_command_events(
    conn: sqlite3.Connection,
    command_id: str,
) -> dict[str, str] | None:
    from src.state.venue_command_repo import list_events

    events = list_events(conn, command_id)
    for event in reversed(events):
        if event.get("event_type") != "SUBMIT_REQUESTED":
            continue
        payload = event.get("payload_json")
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except ValueError:
                return None
        if not isinstance(payload, dict):
            return None
        capability = payload.get("execution_capability")
        if not isinstance(capability, dict):
            return None
        components = capability.get("components")
        if not isinstance(components, list):
            return None
        for component in components:
            if not isinstance(component, dict):
                continue
            if component.get("component") != "exit_snapshot_identity":
                continue
            details = component.get("details")
            if not isinstance(details, dict):
                return None
            return {
                "snapshot_id": _json_safe_string(details.get("snapshot_id"), ""),
                "snapshot_hash": _json_safe_string(details.get("snapshot_hash"), ""),
            }
    return None


def _exit_existing_command_mismatch_reason(
    conn: sqlite3.Connection,
    intent,
    existing_command: dict,
) -> str | None:
    expected = _exit_snapshot_identity_details(intent)
    if expected is None:
        return None
    command_id = _json_safe_string(existing_command.get("command_id"), "")
    if not command_id:
        return "existing_command_missing_command_id"
    existing_snapshot_id = _json_safe_string(existing_command.get("snapshot_id"), "")
    if existing_snapshot_id and existing_snapshot_id != expected["snapshot_id"]:
        return "existing_command_snapshot_id_mismatch"
    observed = _exit_snapshot_identity_from_command_events(conn, command_id)
    if observed is None:
        return "existing_command_missing_exit_snapshot_identity"
    for field_name, expected_value in expected.items():
        if observed.get(field_name) != expected_value:
            return f"existing_command_{field_name}_mismatch"
    return None


def _reject_exit_existing_command_mismatch(
    *,
    trade_id: str,
    intent,
    shares: float,
    limit_price: float,
    idem_value: str,
    reason: str,
) -> "OrderResult":
    return OrderResult(
        trade_id=trade_id,
        status="rejected",
        reason=f"exit_snapshot_identity:{reason}",
        submitted_price=limit_price,
        shares=shares,
        order_role="exit",
        intent_id=getattr(intent, "intent_id", None),
        idempotency_key=idem_value,
    )


def _exit_decision_source_component() -> dict:
    return _capability_component(
        "decision_source_integrity",
        reason="not_applicable_reduce_only",
    )


def _build_execution_capability(
    *,
    action: str,
    command_id: str,
    intent_kind: str,
    order_type: str,
    token_id: str,
    snapshot_id: str,
    components: list[dict],
    freshness_time: str,
    mode: str = "submit",
    venue_order_type: str | None = None,
    risk_allocator_selected_order_type: str | None = None,
) -> dict:
    normalized_components = [
        component if isinstance(component, dict) else _capability_component("unknown_component")
        for component in components
    ]
    proof = {
        "schema_version": 1,
        "action": action,
        "intent_kind": intent_kind,
        "mode": mode,
        "allowed": all(bool(component.get("allowed")) for component in normalized_components),
        "freshness_time": freshness_time,
        "command_id": command_id,
        "order_type": order_type,
        "token_id": token_id,
        "executable_snapshot_id": snapshot_id,
        "components": normalized_components,
    }
    if venue_order_type is not None:
        proof["venue_order_type"] = str(venue_order_type)
    if risk_allocator_selected_order_type is not None:
        proof["risk_allocator_selected_order_type"] = str(risk_allocator_selected_order_type)
    proof["capability_id"] = hashlib.sha256(
        json.dumps(proof, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:32]
    return proof


def _reserve_collateral_for_buy(
    command_id: str,
    intent: ExecutionIntent,
    conn: sqlite3.Connection,
    *,
    spend_micro: int,
) -> None:
    """Reserve pUSD on the same connection as the venue command row."""
    from src.state.collateral_ledger import CollateralLedger

    CollateralLedger(conn).reserve_pusd_for_buy(command_id, spend_micro)


def _reserve_collateral_for_sell(
    command_id: str, token_id: str, shares: float, conn: sqlite3.Connection
) -> None:
    """Reserve CTF inventory on the same connection as the venue command row."""
    from src.state.collateral_ledger import CollateralLedger

    CollateralLedger(conn).reserve_tokens_for_sell(command_id, token_id, shares)


def _persist_pre_submit_envelope(
    conn: sqlite3.Connection,
    *,
    command_id: str,
    snapshot_id: str,
    token_id: str,
    side: str,
    price: float,
    size: float,
    order_type: str,
    post_only: bool,
    captured_at: str,
) -> str | None:
    envelope = _build_pre_submit_envelope(
        conn,
        command_id=command_id,
        snapshot_id=snapshot_id,
        token_id=token_id,
        side=side,
        price=price,
        size=size,
        order_type=order_type,
        post_only=post_only,
        captured_at=captured_at,
    )
    return _persist_prebuilt_submit_envelope(conn, envelope, command_id=command_id)


def _build_pre_submit_envelope(
    conn: sqlite3.Connection,
    *,
    command_id: str,
    snapshot_id: str,
    token_id: str,
    side: str,
    price: float,
    size: float,
    order_type: str,
    post_only: bool,
    captured_at: str,
):
    """Build the U2 venue-submission envelope before SDK contact.

    This deliberately uses only the already-captured ExecutableMarketSnapshot
    plus the command's intended order shape and the canonical public funder
    identity. It does not touch the private key or instantiate the SDK client,
    preserving INV-30's persist-before-submit ordering. If the snapshot is
    missing or the token is not in that snapshot, return None and let
    insert_command's executable snapshot gate raise the more precise
    fail-closed error.
    """

    from src.contracts.venue_submission_envelope import VenueSubmissionEnvelope
    from src.contracts.executable_market_snapshot import canonicalize_fee_details
    from src.data.polymarket_client import resolve_funder_address
    from src.state.snapshot_repo import get_snapshot
    from src.venue.polymarket_v2_adapter import DEFAULT_V2_HOST

    if not snapshot_id:
        return None
    snapshot = get_snapshot(conn, snapshot_id)
    if snapshot is None:
        return None
    if token_id == snapshot.yes_token_id:
        outcome_label = "YES"
    elif token_id == snapshot.no_token_id:
        outcome_label = "NO"
    else:
        return None

    price_dec = Decimal(str(price))
    size_dec = Decimal(str(size))
    canonical_payload = {
        "command_id": command_id,
        "snapshot_id": snapshot.snapshot_id,
        "token_id": token_id,
        "side": side,
        "price": str(price_dec),
        "size": str(size_dec),
        "order_type": order_type,
        "post_only": bool(post_only),
        "condition_id": snapshot.condition_id,
        "question_id": snapshot.question_id,
    }
    canonical_json = json.dumps(
        canonical_payload,
        sort_keys=True,
        separators=(",", ":"),
    )
    payload_hash = hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()
    try:
        funder_address = str(resolve_funder_address() or "").strip()
    except Exception as exc:
        raise PreSubmitIdentityBindingError(str(exc)) from exc
    if not funder_address:
        raise PreSubmitIdentityBindingError("canonical funder_address is empty")
    envelope = VenueSubmissionEnvelope(
        sdk_package="py-clob-client-v2",
        sdk_version="pre-submit",
        host=os.environ.get("POLYMARKET_CLOB_V2_HOST", DEFAULT_V2_HOST),
        chain_id=int(os.environ.get("POLYMARKET_CHAIN_ID", "137")),
        funder_address=funder_address,
        condition_id=snapshot.condition_id,
        question_id=snapshot.question_id,
        yes_token_id=snapshot.yes_token_id,
        no_token_id=snapshot.no_token_id,
        selected_outcome_token_id=token_id,
        outcome_label=outcome_label,
        side=side,
        price=price_dec,
        size=size_dec,
        order_type=order_type,
        post_only=post_only,
        tick_size=snapshot.min_tick_size,
        min_order_size=snapshot.min_order_size,
        neg_risk=snapshot.neg_risk,
        fee_details=canonicalize_fee_details(snapshot.fee_details),
        canonical_pre_sign_payload_hash=payload_hash,
        signed_order=None,
        signed_order_hash=None,
        raw_request_hash=payload_hash,
        raw_response_json=None,
        order_id=None,
        trade_ids=(),
        transaction_hashes=(),
        error_code=None,
        error_message=None,
        captured_at=captured_at,
    )
    return envelope


def _persist_prebuilt_submit_envelope(
    conn: sqlite3.Connection,
    envelope,
    *,
    command_id: str,
) -> str | None:
    if envelope is None:
        return None
    from src.state.venue_command_repo import insert_submission_envelope

    return insert_submission_envelope(
        conn,
        envelope,
        envelope_id=f"pre-submit:{command_id}",
    )


class FinalSubmissionEnvelopePersistenceError(RuntimeError):
    """Raised when post-submit SDK provenance cannot be persisted."""


class PreSubmitIdentityBindingError(RuntimeError):
    """Raised when a pre-submit envelope cannot bind canonical live identity."""


def _persist_final_submission_envelope_payload(
    conn: sqlite3.Connection,
    result,
    *,
    command_id: str,
) -> dict[str, str]:
    """Persist the SDK-returned submission envelope as a second append-only row.

    The command row keeps pointing at the pre-side-effect envelope.  This helper
    pins the post-submit SDK response/signature facts and returns a compact
    event payload reference so ACK/REJECTED events can prove which final
    envelope row they observed.
    """

    if not isinstance(result, dict):
        raise FinalSubmissionEnvelopePersistenceError(
            f"submit result must be a dict, got {type(result).__name__}"
        )
    envelope_payload = result.get("_venue_submission_envelope")
    if envelope_payload is None:
        raise FinalSubmissionEnvelopePersistenceError(
            "submit result missing _venue_submission_envelope"
        )
    if not isinstance(envelope_payload, dict):
        raise FinalSubmissionEnvelopePersistenceError(
            f"_venue_submission_envelope must be dict, got {type(envelope_payload).__name__}"
        )

    try:
        from src.contracts.venue_submission_envelope import VenueSubmissionEnvelope
        from src.state.venue_command_repo import insert_submission_envelope

        envelope = VenueSubmissionEnvelope.from_dict(envelope_payload)
        envelope_id = hashlib.sha256(envelope.to_json().encode("utf-8")).hexdigest()
        try:
            envelope_id = insert_submission_envelope(conn, envelope)
        except sqlite3.IntegrityError:
            if conn.execute(
                "SELECT 1 FROM venue_submission_envelopes WHERE envelope_id = ?",
                (envelope_id,),
            ).fetchone() is None:
                raise
        return {
            "final_submission_envelope_stage": "post_submit_result",
            "final_submission_envelope_id": envelope_id,
            "final_submission_envelope_command_id": command_id,
        }
    except Exception as exc:
        raise FinalSubmissionEnvelopePersistenceError(str(exc)) from exc


def _submit_result_order_id(result) -> str | None:
    if not isinstance(result, dict):
        return None
    return result.get("orderID") or result.get("orderId") or result.get("id") or None


def _submit_result_review_required_payload(
    result,
    *,
    reason: str,
    detail: str,
    idempotency_key: str,
) -> dict[str, str]:
    payload = {
        "reason": reason,
        "detail": detail,
        "idempotency_key": idempotency_key,
    }
    order_id = _submit_result_order_id(result)
    if order_id:
        payload["venue_order_id"] = str(order_id)
    if isinstance(result, dict) and result.get("status") is not None:
        payload["venue_status"] = str(result.get("status"))
    return payload


def _current_command_state_value(conn: sqlite3.Connection, command_id: str) -> str | None:
    try:
        row = conn.execute(
            "SELECT state FROM venue_commands WHERE command_id = ?",
            (command_id,),
        ).fetchone()
    except Exception:
        return None
    if row is None:
        return None
    try:
        return str(row["state"])
    except Exception:
        return str(row[0])


def _venue_command_exists(conn: sqlite3.Connection, command_id: str) -> bool:
    try:
        row = conn.execute(
            "SELECT 1 FROM venue_commands WHERE command_id = ? LIMIT 1",
            (command_id,),
        ).fetchone()
    except Exception:
        return False
    return row is not None


def _submit_ack_already_persisted(
    conn: sqlite3.Connection,
    *,
    command_id: str,
    order_id: str,
) -> bool:
    try:
        row = conn.execute(
            """
            SELECT state, venue_order_id
              FROM venue_commands
             WHERE command_id = ?
             LIMIT 1
            """,
            (command_id,),
        ).fetchone()
    except Exception:
        return False
    if row is None:
        return False
    try:
        state = str(row["state"] or "")
        venue_order_id = str(row["venue_order_id"] or "")
    except Exception:
        state = str(row[0] or "")
        venue_order_id = str(row[1] or "")
    if state not in {"ACKED", "PARTIAL", "FILLED"} or venue_order_id != order_id:
        return False
    try:
        rows = conn.execute(
            """
            SELECT payload_json
              FROM venue_command_events
             WHERE command_id = ?
               AND event_type = 'SUBMIT_ACKED'
             ORDER BY sequence_no DESC
            """,
            (command_id,),
        ).fetchall()
    except Exception:
        return False
    for event in rows:
        try:
            raw = event["payload_json"]
        except Exception:
            raw = event[0]
        try:
            payload = json.loads(str(raw or "{}"))
        except json.JSONDecodeError:
            payload = {}
        if isinstance(payload, dict) and str(payload.get("venue_order_id") or "") == order_id:
            return True
    return False


def _order_fact_already_persisted(
    conn: sqlite3.Connection,
    *,
    command_id: str,
    order_id: str,
) -> bool:
    try:
        row = conn.execute(
            """
            SELECT 1
              FROM venue_order_facts
             WHERE command_id = ?
               AND venue_order_id = ?
             LIMIT 1
            """,
            (command_id, order_id),
        ).fetchone()
    except Exception:
        return False
    return row is not None


def _trade_fact_already_persisted(
    conn: sqlite3.Connection,
    *,
    command_id: str,
    trade_id: str,
) -> bool:
    try:
        row = conn.execute(
            """
            SELECT 1
              FROM venue_trade_facts
             WHERE command_id = ?
               AND trade_id = ?
             LIMIT 1
            """,
            (command_id, trade_id),
        ).fetchone()
    except Exception:
        return False
    return row is not None


def _command_event_already_persisted(
    conn: sqlite3.Connection,
    *,
    command_id: str,
    event_type: str,
    order_id: str,
    trade_id: str | None = None,
) -> bool:
    try:
        rows = conn.execute(
            """
            SELECT payload_json
              FROM venue_command_events
             WHERE command_id = ?
               AND event_type = ?
             ORDER BY sequence_no DESC
            """,
            (command_id, event_type),
        ).fetchall()
    except Exception:
        return False
    for event in rows:
        try:
            raw = event["payload_json"]
        except Exception:
            raw = event[0]
        try:
            payload = json.loads(str(raw or "{}"))
        except json.JSONDecodeError:
            payload = {}
        if not isinstance(payload, dict):
            continue
        if str(payload.get("venue_order_id") or "") != order_id:
            continue
        if trade_id is not None and str(payload.get("trade_id") or "") != trade_id:
            continue
        return True
    return False


def _retry_persist_on_db_lock(
    conn: sqlite3.Connection,
    persist_fn,
    *,
    what: str,
    attempts: int = 4,
    base_sleep_s: float = 0.1,
) -> None:
    """Run a POST-SIDE-EFFECT persistence closure, retrying ONLY on a transient
    SQLite 'database is locked' (C-DBLOCK-UNKNOWN, 2026-06-16).

    WHY: once the venue side effect has happened the order outcome is KNOWN; all that
    remains is to RECORD it (append_event SUBMIT_ACKED + order/trade facts + commit). A
    transient 'database is locked' on that record write — write-write contention, or a
    busy handler NULLed by a prior executescript (see src/state/db.py _apply_busy_timeout)
    so the 30s budget drops to 0 and the lock raises INSTANTLY rather than waiting —
    otherwise degrades a KNOWN-GOOD order to unknown_side_effect, which trips the
    governor's unknown_side_effect kill-switch (limit=0, src/risk_allocator/governor.py:242)
    and HALTS all submits until reconciled. Live evidence: 13x
    EXECUTOR_SUBMIT_UNKNOWN:'database is locked' Jun 12-16, the dominant current no-trade.

    SAFE to retry: this re-attempts only the LOCAL write — the venue is never re-called
    here, so there is no double-submit risk. A full conn.rollback() reverts the
    grammar-validated SAVEPOINT writes in append_event WITH the transaction, so the state
    machine returns to its pre-ACK state and re-running the whole closure is grammar-valid
    (the same rollback-reverts-state the existing _mark_post_submit_persistence_failure
    relies on). Retries ONLY OperationalError matching 'database is locked'; any other
    error (incl. the ValueError append_event raises on an illegal grammar transition)
    propagates immediately to the caller's existing failure path.
    """
    for attempt in range(1, attempts + 1):
        try:
            persist_fn()
            return
        except sqlite3.OperationalError as exc:
            if "database is locked" not in str(exc).lower() or attempt == attempts:
                raise
            try:
                conn.rollback()  # revert partial/uncommitted writes so the re-run is clean
            except Exception:
                pass
            logger.warning(
                "db locked persisting %s (attempt %d/%d); rolled back + retrying: %s",
                what, attempt, attempts, exc,
            )
            time.sleep(base_sleep_s * attempt)


def _mark_post_submit_persistence_failure(
    conn: sqlite3.Connection,
    *,
    command_id: str,
    order_id: str | None,
    occurred_at: str,
    reason: str,
    detail: str,
    idempotency_key: str,
    order_role: str,
) -> str | None:
    """Persist REVIEW_REQUIRED after SDK success but ACK facts failed.

    At this point the venue side effect may have happened. Any half-written ACK
    transaction must be rolled back before writing the minimal durable review
    event; returning a normal pending/filled result would make memory outrank
    canonical command truth.
    """

    from src.state.venue_command_repo import append_event

    try:
        conn.rollback()
    except Exception as rollback_exc:
        logger.error(
            "%s ACK persistence rollback failed (command_id=%s order_id=%s): %s",
            order_role,
            command_id,
            order_id,
            rollback_exc,
        )
    try:
        append_event(
            conn,
            command_id=command_id,
            event_type="REVIEW_REQUIRED",
            occurred_at=occurred_at,
            payload={
                "reason": reason,
                "detail": detail,
                "venue_order_id": order_id or "",
                "idempotency_key": idempotency_key,
                "side_effect_boundary_crossed": True,
                "sdk_submit_returned_order_id": bool(order_id),
                "requires_recovery": True,
            },
        )
        conn.commit()
    except Exception as review_exc:
        try:
            conn.rollback()
        except Exception:
            pass
        logger.error(
            "%s REVIEW_REQUIRED event failed after ACK persistence failure "
            "(command_id=%s order_id=%s): %s",
            order_role,
            command_id,
            order_id,
            review_exc,
        )
    return _current_command_state_value(conn, command_id)


@dataclass
class OrderResult:
    """Result of an order attempt."""
    trade_id: str
    status: str  # "filled", "pending", "cancelled", "rejected", "unknown_side_effect"
    fill_price: Optional[float] = None
    filled_at: Optional[str] = None
    reason: Optional[str] = None
    order_id: Optional[str] = None
    timeout_seconds: Optional[int] = None
    submitted_price: Optional[float] = None
    shares: Optional[float] = None
    order_role: Optional[str] = None
    intent_id: Optional[str] = None
    external_order_id: Optional[str] = None
    venue_status: Optional[str] = None
    idempotency_key: Optional[str] = None
    decision_edge: float = 0.0
    # P1.S5: INV-32 — materialize_position gates on this value.
    # Set to the CommandState enum string after the ack phase resolves.
    # None means the result was rejected before any command was persisted.
    command_state: Optional[str] = None
    # F7: FK to venue_commands.command_id — set when a command row was persisted
    # (post-persist path). None for pre-persist rejections.
    command_id: Optional[str] = None
    # Post-submit source-timing facts for decision_events lineage. These are
    # only populated after the SDK submit boundary has been reached.
    zeus_submit_intent_time: Optional[str] = None
    venue_ack_time: Optional[str] = None


@dataclass(frozen=True)
class ExitOrderIntent:
    """Executor-level contract for live sell/exit order placement."""

    trade_id: str
    token_id: str
    shares: float
    current_price: float
    best_bid: Optional[float] = None
    intent_id: Optional[str] = None
    idempotency_key: Optional[str] = None
    executable_snapshot_id: str = ""
    executable_snapshot_hash: str = ""
    executable_snapshot_min_tick_size: Decimal | str | None = None
    executable_snapshot_min_order_size: Decimal | str | None = None
    executable_snapshot_neg_risk: bool | None = None


def _orderresult_from_existing(
    conn: sqlite3.Connection,
    existing: "VenueCommand",  # type: ignore[name-defined]
    trade_id: str,
    limit_price: float,
    shares: float,
    idem_value: str,
    intent_id: Optional[str],
    order_role: str,
) -> "OrderResult":
    """Map an existing VenueCommand row to an OrderResult without re-submitting.

    P1.S5: used by both the pre-submit lookup path and the IntegrityError
    collision handler in _live_order and execute_exit_order. Extracted once to
    prevent 4-way drift (P1.S3 critic MAJOR-deferred, now closed).

    The command_state field is populated so cycle_runtime can gate
    materialize_position on INV-32.
    """
    # Lazy import to avoid circular deps at module load time.
    from src.execution.command_bus import CommandState
    from src.state.venue_command_repo import list_events

    def _timing_from_existing() -> tuple[Optional[str], Optional[str]]:
        submit_time: Optional[str] = None
        ack_time: Optional[str] = None
        for event in list_events(conn, existing.command_id):
            event_type = str(event.get("event_type") or "")
            occurred_at = str(event.get("occurred_at") or "")
            if event_type == "SUBMIT_REQUESTED" and occurred_at and submit_time is None:
                submit_time = occurred_at
            elif event_type == "SUBMIT_ACKED" and occurred_at and ack_time is None:
                ack_time = occurred_at
            if submit_time and ack_time:
                break
        return submit_time, ack_time

    submit_time, ack_time = _timing_from_existing()

    s = existing.state
    if s in (CommandState.ACKED, CommandState.PARTIAL):
        return OrderResult(
            trade_id=trade_id,
            status="pending",
            reason="idempotency_collision: prior attempt acked",
            submitted_price=limit_price,
            shares=shares,
            order_id=existing.venue_order_id,
            order_role=order_role,
            external_order_id=existing.venue_order_id,
            idempotency_key=idem_value,
            intent_id=intent_id,
            command_state=s.value,
            zeus_submit_intent_time=submit_time,
            venue_ack_time=ack_time,
        )
    if s == CommandState.FILLED:
        return OrderResult(
            trade_id=trade_id,
            status="pending",
            reason="idempotency_collision: prior attempt filled",
            submitted_price=limit_price,
            shares=shares,
            order_id=existing.venue_order_id,
            order_role=order_role,
            external_order_id=existing.venue_order_id,
            idempotency_key=idem_value,
            intent_id=intent_id,
            command_state=s.value,
            zeus_submit_intent_time=submit_time,
            venue_ack_time=ack_time,
        )
    if s == CommandState.SUBMIT_UNKNOWN_SIDE_EFFECT:
        return OrderResult(
            trade_id=trade_id,
            status="unknown_side_effect",
            reason="idempotency_collision: prior attempt unknown side effect; recovery required",
            submitted_price=limit_price,
            shares=shares,
            order_id=existing.venue_order_id,
            order_role=order_role,
            external_order_id=existing.venue_order_id,
            idempotency_key=idem_value,
            intent_id=intent_id,
            command_state=s.value,
        )
    if s in (CommandState.SUBMITTING, CommandState.UNKNOWN):
        return OrderResult(
            trade_id=trade_id,
            status="rejected",
            reason="idempotency_collision: prior attempt in flight; recovery will resolve",
            submitted_price=limit_price,
            shares=shares,
            order_role=order_role,
            idempotency_key=idem_value,
            intent_id=intent_id,
            command_state=s.value,
        )
    if s in (CommandState.REJECTED, CommandState.CANCELLED, CommandState.EXPIRED):
        return OrderResult(
            trade_id=trade_id,
            status="rejected",
            reason=f"idempotency_collision: prior attempt {s.value}",
            submitted_price=limit_price,
            shares=shares,
            order_role=order_role,
            external_order_id=existing.venue_order_id,
            idempotency_key=idem_value,
            intent_id=intent_id,
            command_state=s.value,
        )
    # REVIEW_REQUIRED, INTENT_CREATED, or any future state
    return OrderResult(
        trade_id=trade_id,
        status="rejected",
        reason=f"idempotency_collision: prior attempt {s.value}",
        submitted_price=limit_price,
        shares=shares,
        order_role=order_role,
        idempotency_key=idem_value,
        intent_id=intent_id,
        command_state=s.value,
    )


def _orderresult_from_economic_unknown(
    existing: "VenueCommand",  # type: ignore[name-defined]
    trade_id: str,
    limit_price: float,
    shares: float,
    idem_value: str,
    intent_id: Optional[str],
    order_role: str,
) -> "OrderResult":
    """Block a new command whose economics duplicate an unresolved unknown."""

    return OrderResult(
        trade_id=trade_id,
        status="unknown_side_effect",
        reason=(
            "economic_intent_duplication: prior attempt unknown side effect "
            f"command_id={existing.command_id}; recovery required"
        ),
        submitted_price=limit_price,
        shares=shares,
        order_role=order_role,
        external_order_id=existing.venue_order_id,
        idempotency_key=idem_value,
        intent_id=intent_id,
        command_state=existing.state.value,
    )


def create_execution_intent(
    edge_context: EdgeContext,
    edge: BinEdge,
    size_usd: float,
    mode: str,
    market_id: str,
    token_id: str = "",
    no_token_id: str = "",
    best_ask: Optional[float] = None,
    executable_snapshot_id: str = "",
    executable_snapshot_min_tick_size: Decimal | str | None = None,
    executable_snapshot_min_order_size: Decimal | str | None = None,
    executable_snapshot_neg_risk: bool | None = None,
    repriced_limit_price: Optional[float] = None,
    event_id: str = "",
    resolution_window: str = "",
    correlation_key: str = "",
    decision_source_context=None,
) -> ExecutionIntent:
    """Execution Planner: Generates the intent based on Fair Value Plane output."""
    if False: _ = edge.entry_method

    limit_offset = settings["execution"]["limit_offset_pct"]
    edge_direction = Direction(edge.direction)

    # Compute initial limit price in the native/held-side probability space.
    limit_price = compute_native_limit_price(
        HeldSideProbability(edge_context.p_posterior, edge_direction),
        NativeSidePrice(edge.vwmp, edge_direction),
        limit_offset=limit_offset,
    )
    expected_limit_price = float(limit_price)
    slippage_reference_price = min(float(edge_context.p_posterior), float(edge.vwmp))
    if slippage_reference_price <= 0.0:
        slippage_reference_price = expected_limit_price
    max_slippage = SlippageBps(value_bps=200.0, direction="adverse")

    # Dynamic limit price
    if best_ask is not None:
        adverse_gap = best_ask - slippage_reference_price
        adverse_slippage_bps = (
            max(0.0, adverse_gap) / slippage_reference_price * 10_000.0
            if slippage_reference_price > 0.0
            else float("inf")
        )
        if best_ask > limit_price and adverse_slippage_bps <= max_slippage.value_bps:
            logger.info(
                "Dynamic limit: jumping to best_ask %.3f (adverse_slippage %.1f bps)",
                best_ask,
                adverse_slippage_bps,
            )
            limit_price = best_ask
        elif best_ask > limit_price:
            logger.warning(
                "Limit %.3f below best_ask %.3f by %.1f bps vs reference %.3f; "
                "max_slippage %.1f bps blocks jump",
                limit_price,
                best_ask,
                adverse_slippage_bps,
                slippage_reference_price,
                max_slippage.value_bps,
            )
    if repriced_limit_price is not None:
        limit_price = float(repriced_limit_price)
    if limit_price > slippage_reference_price:
        adverse_slippage_bps = (
            (limit_price - slippage_reference_price) / slippage_reference_price * 10_000
        )
        if adverse_slippage_bps > max_slippage.value_bps:
            raise ValueError(
                "MAX_SLIPPAGE_EXCEEDED: "
                f"slippage_reference_price={slippage_reference_price:.6f} "
                f"limit_price={float(limit_price):.6f} "
                f"adverse_slippage_bps={adverse_slippage_bps:.2f} "
                f"max_slippage_bps={max_slippage.value_bps:.2f}"
            )

    if executable_snapshot_min_tick_size is not None:
        limit_price = _align_buy_limit_price_to_tick(
            limit_price,
            executable_snapshot_min_tick_size,
        )
    if float(edge_context.p_posterior) - float(limit_price) <= 0.0:
        raise ValueError(
            "REPRICED_LIMIT_REJECTED: "
            f"p_posterior={float(edge_context.p_posterior):.6f} "
            f"limit_price={float(limit_price):.6f}"
        )

    if edge_direction.value == "buy_yes":
        order_token = token_id
    elif edge_direction.value == "buy_no":
        order_token = no_token_id
    else:
        raise ValueError(f"Strict token routing failed: unsupported token direction '{edge.direction}'")

    if mode not in MODE_TIMEOUTS:
        raise ValueError(f"Unknown execution mode '{mode}' cannot default to timeout. Explicit runtime mode required.")
    timeout = MODE_TIMEOUTS[mode]

    # Slice P3.3 + P3-fix4 (post-review code-reviewer NIT-1): typed
    # slippage budget. 0.02 fraction = 200 bps (2% adverse-direction
    # limit). Wrapping in SlippageBps makes the units explicit at
    # construction; pre-fix the raw 0.02 was unit-ambiguous and the
    # type system couldn't catch a caller that meant 0.02 bps (200x
    # tighter) instead of 0.02 fraction. Import hoisted to module top
    # per PEP 8.
    return ExecutionIntent(
        direction=edge_direction,
        target_size_usd=size_usd,
        limit_price=limit_price,
        toxicity_budget=0.05,
        max_slippage=max_slippage,
        is_sandbox=False,
        market_id=market_id,
        token_id=order_token,
        timeout_seconds=timeout,
        decision_edge=edge.edge,
        executable_snapshot_id=executable_snapshot_id,
        executable_snapshot_min_tick_size=executable_snapshot_min_tick_size,
        executable_snapshot_min_order_size=executable_snapshot_min_order_size,
        executable_snapshot_neg_risk=executable_snapshot_neg_risk,
        event_id=event_id or market_id,
        resolution_window=resolution_window or "default",
        correlation_key=correlation_key or event_id or market_id,
        decision_source_context=decision_source_context,
    )


def _align_buy_limit_price_to_tick(limit_price: float, min_tick_size: Decimal | str) -> float:
    """Round a BUY limit down to the executable snapshot tick."""

    tick = Decimal(str(min_tick_size))
    if tick <= 0:
        raise ValueError("executable_snapshot_min_tick_size must be positive")
    price = Decimal(str(limit_price))
    aligned = (price / tick).to_integral_value(rounding=ROUND_FLOOR) * tick
    if aligned <= 0:
        aligned = tick
    upper = Decimal("1") - tick
    if aligned >= Decimal("1"):
        aligned = upper
    return float(aligned)


def _submit_tick_size_or_raise(min_tick_size: Decimal | str | float) -> Decimal:
    try:
        tick = Decimal(str(min_tick_size))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(
            f"executable_snapshot_min_tick_size must be decimal: {min_tick_size!r}"
        ) from exc
    if not tick.is_finite() or tick <= Decimal("0") or tick >= Decimal("1"):
        raise ValueError("executable_snapshot_min_tick_size must be finite and inside (0, 1)")
    return tick


def _align_sell_limit_price_to_tick(limit_price: float, min_tick_size: Decimal | str | float) -> float:
    """Round a SELL limit down to the executable snapshot tick."""

    tick = _submit_tick_size_or_raise(min_tick_size)
    price = Decimal(str(limit_price))
    min_price = tick
    max_price = Decimal("1") - tick
    if price < min_price:
        price = min_price
    elif price > max_price:
        price = max_price
    aligned = (price / tick).to_integral_value(rounding=ROUND_FLOOR) * tick
    if aligned < min_price:
        aligned = min_price
    elif aligned > max_price:
        aligned = max_price
    return float(aligned)


def _entry_buy_submit_shares(target_size_usd: float, limit_price: float) -> float:
    shares = target_size_usd / limit_price if limit_price > 0 else 0
    return math.ceil(shares * 100 - 1e-9) / 100.0  # BUY: round UP


def _final_intent_submit_shares(intent: FinalExecutionIntent) -> float:
    """Return the frozen venue share quantity from the final intent."""

    submitted_shares = float(intent.submitted_shares)
    if submitted_shares <= 0.0:
        raise ValueError("FinalExecutionIntent submitted_shares must be positive")
    return submitted_shares


def _final_intent_target_size_usd(intent: FinalExecutionIntent, shares: float) -> float:
    return float(Decimal(str(shares)) * intent.final_limit_price)


MIN_MARKETABLE_BUY_NOTIONAL_USD = Decimal("1")


def _assert_final_intent_buy_notional_meets_venue_minimum(
    intent: FinalExecutionIntent,
    *,
    submitted_shares: float,
) -> None:
    if intent.direction not in {"buy_yes", "buy_no"}:
        return
    notional = Decimal(str(submitted_shares)) * Decimal(str(intent.final_limit_price))
    if notional < MIN_MARKETABLE_BUY_NOTIONAL_USD:
        raise ValueError(
            "FinalExecutionIntent BUY notional is below venue minimum: "
            f"notional={notional} min_notional={MIN_MARKETABLE_BUY_NOTIONAL_USD}"
        )


def _final_intent_timeout_seconds(intent: FinalExecutionIntent) -> int:
    if intent.cancel_after is None:
        raise ValueError("FinalExecutionIntent missing cancel_after")
    timeout = math.ceil((intent.cancel_after - datetime.now(timezone.utc)).total_seconds())
    if timeout <= 0:
        raise ValueError("FinalExecutionIntent cancel_after has already expired")
    return timeout


def _final_intent_snapshot_metadata(
    intent: FinalExecutionIntent,
    conn: Optional[sqlite3.Connection],
    *,
    submitted_shares: float,
) -> tuple[str, str]:
    """Resolve venue identity from the cited executable snapshot."""

    from src.state.snapshot_repo import get_snapshot

    own_conn = conn is None
    lookup_conn = get_trade_connection_with_world_required() if own_conn else conn
    try:
        snapshot = get_snapshot(lookup_conn, intent.snapshot_id)
    finally:
        if own_conn:
            lookup_conn.close()
    if snapshot is None:
        raise ValueError(f"FinalExecutionIntent snapshot_id not found: {intent.snapshot_id}")
    if snapshot.executable_snapshot_hash != intent.snapshot_hash:
        raise ValueError("FinalExecutionIntent snapshot_hash does not match executable snapshot")
    if snapshot.selected_outcome_token_id != intent.selected_token_id:
        raise ValueError("FinalExecutionIntent selected_token_id does not match executable snapshot")
    if intent.direction in {"buy_yes", "sell_yes"}:
        expected_token_id = snapshot.yes_token_id
        expected_label = "YES"
    elif intent.direction in {"buy_no", "sell_no"}:
        expected_token_id = snapshot.no_token_id
        expected_label = "NO"
    else:
        raise ValueError(f"unsupported direction {intent.direction!r}")
    if intent.selected_token_id != expected_token_id:
        raise ValueError(
            "FinalExecutionIntent direction does not match executable snapshot side: "
            f"direction={intent.direction!r} selected_token_id={intent.selected_token_id!r} "
            f"expected_{expected_label.lower()}_token_id={expected_token_id!r}"
        )
    if intent.tick_size != snapshot.min_tick_size:
        raise ValueError("FinalExecutionIntent tick_size does not match executable snapshot")
    if intent.min_order_size != snapshot.min_order_size:
        raise ValueError("FinalExecutionIntent min_order_size does not match executable snapshot")
    # Some executable snapshots carry a stale/omitted false while the live
    # certificate path has already proven neg-risk true. True is monotonic here;
    # a false intent against a true snapshot remains a hard provenance mismatch.
    if intent.neg_risk != snapshot.neg_risk and not (
        intent.neg_risk is True and snapshot.neg_risk is False
    ):
        raise ValueError("FinalExecutionIntent neg_risk does not match executable snapshot")
    sweep = simulate_clob_sweep(
        snapshot=snapshot,
        direction=intent.direction,
        requested_size_kind="shares",
        requested_size_value=Decimal(str(submitted_shares)),
        limit_price=intent.final_limit_price,
    )
    if intent.order_policy == "post_only_passive_limit":
        if not intent.post_only:
            raise ValueError("FinalExecutionIntent post_only_passive_limit requires post_only")
        if intent.order_type not in {"GTC", "GTD"}:
            raise ValueError("FinalExecutionIntent post_only_passive_limit requires GTC/GTD")
        if sweep.filled_shares != Decimal("0"):
            raise ValueError(
                "FinalExecutionIntent post_only_passive_limit would cross executable snapshot book"
            )
        if intent.expected_fill_price_before_fee != intent.final_limit_price:
            raise ValueError(
                "FinalExecutionIntent passive expected_fill_price_before_fee must equal final_limit_price"
            )
        return snapshot.gamma_market_id, snapshot.event_id
    if sweep.depth_status != "PASS" or sweep.average_price is None:
        raise ValueError(
            "FinalExecutionIntent executable depth validation failed: "
            f"{sweep.depth_status}"
        )
    if sweep.average_price != intent.expected_fill_price_before_fee:
        raise ValueError(
            "FinalExecutionIntent expected_fill_price_before_fee does not match "
            "executable snapshot sweep"
        )
    return snapshot.gamma_market_id, snapshot.event_id


def _legacy_entry_intent_from_final(
    intent: FinalExecutionIntent,
    *,
    market_id: str,
    event_id: str,
    submitted_shares: float,
) -> ExecutionIntent:
    """Build the legacy executor envelope without repricing probability inputs."""

    if intent.direction not in {"buy_yes", "buy_no"}:
        raise ValueError(
            "execute_final_intent only supports buy_yes/buy_no entry directions; "
            f"got {intent.direction!r}"
        )
    if intent.decision_source_context is None:
        raise ValueError("FinalExecutionIntent missing decision_source_context")
    decision_source_errors, _deferred_errors = _pre_submit_decision_source_errors(
        intent.decision_source_context
    )
    if decision_source_errors:
        raise ValueError(
            "FinalExecutionIntent decision_source_context failed integrity: "
            + ",".join(decision_source_errors)
        )

    snapshot_event_id = str(event_id or "").strip()
    intent_event_id = str(intent.event_id or "").strip()
    if intent_event_id and snapshot_event_id and intent_event_id != snapshot_event_id:
        raise ValueError(
            "FinalExecutionIntent event_id does not match executable snapshot: "
            f"intent={intent_event_id!r} snapshot={snapshot_event_id!r}"
        )
    execution_event_id = snapshot_event_id or intent_event_id
    max_slippage_bps = float(intent.max_slippage_bps)
    max_slippage_direction = "zero" if max_slippage_bps == 0.0 else "adverse"
    return ExecutionIntent(
        direction=Direction(intent.direction),
        target_size_usd=_final_intent_target_size_usd(intent, submitted_shares),
        limit_price=float(intent.final_limit_price),
        toxicity_budget=0.05,
        max_slippage=SlippageBps(
            value_bps=max_slippage_bps,
            direction=max_slippage_direction,
        ),
        is_sandbox=False,
        market_id=market_id,
        token_id=intent.selected_token_id,
        timeout_seconds=_final_intent_timeout_seconds(intent),
        decision_edge=0.0,
        executable_snapshot_id=intent.snapshot_id,
        actionable_executable_snapshot_id=intent.snapshot_id,
        executable_snapshot_hash=intent.snapshot_hash,
        executable_cost_basis_id=intent.cost_basis_id,
        executable_cost_basis_hash=intent.cost_basis_hash,
        pricing_semantics_id=intent.pricing_semantics_id,
        executable_snapshot_min_tick_size=intent.tick_size,
        executable_snapshot_min_order_size=intent.min_order_size,
        executable_snapshot_neg_risk=intent.neg_risk,
        event_id=execution_event_id,
        resolution_window=intent.resolution_window,
        correlation_key=intent.correlation_key or execution_event_id or intent.hypothesis_id,
        decision_source_context=intent.decision_source_context,
        submit_order_type=intent.order_type,
        post_only=intent.post_only,
        taker_quality_proof=intent.taker_quality_proof,
        q_live=intent.q_live,
        q_lcb_5pct=intent.q_lcb_5pct,
        expected_edge=intent.expected_edge,
        min_entry_price=intent.min_entry_price,
        min_expected_profit_usd=intent.min_expected_profit_usd,
        min_submit_edge_density=intent.min_submit_edge_density,
        qkernel_execution_economics=intent.qkernel_execution_economics,
        actionable_certificate_hash=intent.actionable_certificate_hash,
    )


def _recapture_fresh_entry_snapshot_if_needed(
    legacy_intent: ExecutionIntent,
    final_intent: FinalExecutionIntent,
    *,
    conn: sqlite3.Connection | None,
    submitted_shares: float,
) -> ExecutionIntent:
    """Refresh a stale executable snapshot without changing final-intent economics."""

    from src.contracts.executable_market_snapshot import is_fresh
    from src.state.snapshot_repo import get_snapshot

    if conn is None:
        return legacy_intent
    snapshot = get_snapshot(conn, legacy_intent.executable_snapshot_id)
    if snapshot is None or is_fresh(snapshot, datetime.now(timezone.utc)):
        return legacy_intent
    if os.environ.get("ZEUS_REPRICE_RECAPTURE_DISABLED"):
        return legacy_intent
    from types import SimpleNamespace
    from src.data.market_scanner import capture_executable_market_snapshot
    from src.data.polymarket_client import PolymarketClient
    from src.engine.cycle_runtime import _market_dict_from_snapshot

    decision = SimpleNamespace(
        tokens={
            "token_id": snapshot.yes_token_id,
            "no_token_id": snapshot.no_token_id,
            "market_id": snapshot.condition_id,
        },
        edge=SimpleNamespace(direction=final_intent.direction),
    )
    captured_at = datetime.now(timezone.utc)
    with PolymarketClient() as clob:
        fields = capture_executable_market_snapshot(
            conn,
            market=_market_dict_from_snapshot(snapshot),
            decision=decision,
            clob=clob,
            captured_at=captured_at,
            scan_authority="VERIFIED",
            execution_side="BUY",
        )
    fresh_id = str(fields.get("executable_snapshot_id") or "")
    fresh = get_snapshot(conn, fresh_id) if fresh_id else None
    if fresh is None or not is_fresh(fresh, captured_at):
        return legacy_intent
    if fresh.selected_outcome_token_id != final_intent.selected_token_id:
        raise ValueError("recaptured executable snapshot selected token mismatch")
    fresh_limit_price = _align_buy_limit_price_to_tick(
        final_intent.final_limit_price,
        fresh.min_tick_size,
    )
    if Decimal(str(submitted_shares)) < Decimal(str(fresh.min_order_size)):
        raise ValueError(
            "recaptured executable snapshot submitted_shares below fresh min_order_size: "
            f"submitted_shares={submitted_shares} fresh_min_order_size={fresh.min_order_size}"
        )
    # neg_risk is venue metadata attached to the same condition/token identity.
    # Older elected/JIT snapshots can be missing the CLOB negRisk fact and carry
    # the default False; the fresh recapture below is the authority that gets
    # threaded into the submit envelope. Do not reject solely because this
    # metadata was corrected, provided selected token, tick/min-order, and
    # economics still validate against the fresh book.
    # MODE-CORRECT ECONOMICS VALIDATION (live 2026-06-12 02:16:49Z, Helsinki
    # POST_ONLY 219.77@0.14): the crossable-depth sweep is TAKER economics — a
    # post_only maker rest ADDS liquidity and by construction has no crossable
    # depth at its own limit, so the sweep returned DEPTH_INSUFFICIENT and this
    # check killed every resting maker whose elected snapshot went stale before
    # the executor ran (fourth instance of a taker-shaped check strangling the
    # maker lane; same family as WALL #1 passive_maker_context). Maker
    # economics depend only on the rest still being NON-CROSSING on the fresh
    # book: if the fresh ask moved through our limit the post_only premise is
    # gone and the abort is correct; an empty fresh ask is a bid-establishing
    # rest and stands.
    _is_maker_rest = bool(getattr(final_intent, "post_only", False))
    if _is_maker_rest:
        fresh_ask = fresh.orderbook_top_ask
        if fresh_ask is not None and Decimal(str(fresh_limit_price)) >= Decimal(str(fresh_ask)):
            raise ValueError(
                "recaptured executable snapshot changed final-intent economics: "
                f"post_only limit {fresh_limit_price} would cross fresh ask {fresh_ask}"
            )
    else:
        sweep = simulate_clob_sweep(
            snapshot=fresh,
            direction=final_intent.direction,
            requested_size_kind="shares",
            requested_size_value=Decimal(str(submitted_shares)),
            limit_price=fresh_limit_price,
        )
        expected_price = Decimal(str(final_intent.expected_fill_price_before_fee))
        if (
            sweep.depth_status != "PASS"
            or sweep.average_price is None
            or Decimal(str(sweep.average_price)) > expected_price
        ):
            raise ValueError(
                "recaptured executable snapshot changed final-intent economics: "
                f"depth_status={sweep.depth_status} average_price={sweep.average_price}"
            )
    return replace(
        legacy_intent,
        limit_price=fresh_limit_price,
        executable_snapshot_id=fresh.snapshot_id,
        executable_snapshot_hash=fresh.executable_snapshot_hash,
        executable_snapshot_min_tick_size=fresh.min_tick_size,
        executable_snapshot_min_order_size=fresh.min_order_size,
        executable_snapshot_neg_risk=fresh.neg_risk,
    )


@capability("live_venue_submit", lease=True)
@protects("INV-21", "INV-04")
def execute_final_intent(
    intent: FinalExecutionIntent,
    conn: Optional[sqlite3.Connection] = None,
    decision_id: str = "",
    snapshot_conn: Optional[sqlite3.Connection] = None,
) -> "OrderResult":
    """Submit an immutable corrected execution intent through the live entry path.

    This seam intentionally consumes only FinalExecutionIntent fields. It does
    not inspect BinEdge, VWMP, posterior probability, or any legacy fair-value
    inputs; those belong upstream of corrected cost-basis construction.
    """

    from src.architecture.gate_runtime import check as _gate_runtime_check
    _gate_runtime_check("live_venue_submit")
    if not isinstance(intent, FinalExecutionIntent):
        raise TypeError(
            "execute_final_intent requires FinalExecutionIntent, "
            f"got {type(intent).__name__}"
        )
    # PRE-VENUE validation span (depth/snapshot identity/intent expressibility).
    # All of this runs BEFORE _live_order touches the venue. A failure here means
    # the order PROVABLY never reached the venue; re-raise as PreVenueSubmitError so
    # the EDLI submit boundary classifies it as a TERMINAL PRE_SUBMIT_ERROR (cap
    # released, aggregate terminated) instead of an indeterminate POST_SUBMIT_UNKNOWN
    # that leaves an unresolved-submit + held-cap and crash-loops boot readiness.
    # Antibody: src/engine/event_bound_final_intent.py::PreVenueSubmitError (2026-06-01).
    from src.engine.event_bound_final_intent import PreVenueSubmitError as _PreVenueSubmitError

    try:
        intent.assert_no_recompute_inputs()
        intent.assert_submit_ready()
        submitted_shares = _final_intent_submit_shares(intent)
        _assert_final_intent_buy_notional_meets_venue_minimum(
            intent,
            submitted_shares=submitted_shares,
        )
        market_id, event_id = _final_intent_snapshot_metadata(
            intent,
            snapshot_conn if snapshot_conn is not None else conn,
            submitted_shares=submitted_shares,
        )
        legacy_intent = _legacy_entry_intent_from_final(
            intent,
            market_id=market_id,
            event_id=event_id,
            submitted_shares=submitted_shares,
        )
        legacy_intent = _recapture_fresh_entry_snapshot_if_needed(
            legacy_intent,
            intent,
            conn=snapshot_conn if snapshot_conn is not None else conn,
            submitted_shares=submitted_shares,
        )
    except _PreVenueSubmitError:
        raise
    except (ValueError, TypeError) as exc:
        raise _PreVenueSubmitError(str(exc)) from exc
    trade_id = str(uuid.uuid4())[:12]
    if not legacy_intent.token_id:
        return OrderResult(
            trade_id=trade_id,
            status="rejected",
            reason="No token_id provided for intent",
        )
    from src.execution.command_bus import IntentKind

    _assert_cutover_allows_submit(IntentKind.ENTRY)
    return _live_order(
        trade_id,
        legacy_intent,
        submitted_shares,
        conn=conn,
        decision_id=decision_id or intent.hypothesis_id,
    )


def execute_intent(
    intent: ExecutionIntent,
    edge_vwmp: float,  # Phase 2: remove this parameter (dead after simulated fill deletion)
    label: str,
    conn: Optional[sqlite3.Connection] = None,
    decision_id: str = "",
) -> "OrderResult":
    """Execute the instantiated live domain intent.

    P1.S5: conn and decision_id are threaded through to _live_order so that
    the pre-submit idempotency lookup (INV-32 / NC-19) uses the same DB
    connection as the insert. Callers that pass decision_id enable
    retry-safe idempotency; empty string falls back to a synthetic id
    with a WARNING log.
    """

    from src.config import get_mode

    if get_mode() == "live":
        raise RuntimeError(
            "LEGACY_EXECUTION_INTENT_LIVE_BLOCKED: live entry must use "
            "FinalExecutionIntent via execute_final_intent"
        )
    raise RuntimeError(
        "LEGACY_EXECUTION_INTENT_BLOCKED: legacy ExecutionIntent has no "
        "production execution route; use FinalExecutionIntent via execute_final_intent"
    )


def create_exit_order_intent(
    *,
    trade_id: str,
    token_id: str,
    shares: float,
    current_price: float,
    best_bid: Optional[float] = None,
    executable_snapshot_id: str = "",
    executable_snapshot_hash: str = "",
    executable_snapshot_min_tick_size: Decimal | str | None = None,
    executable_snapshot_min_order_size: Decimal | str | None = None,
    executable_snapshot_neg_risk: bool | None = None,
) -> ExitOrderIntent:
    """Build the explicit executor contract for a live sell/exit order."""

    return ExitOrderIntent(
        trade_id=trade_id,
        token_id=token_id,
        shares=shares,
        current_price=current_price,
        best_bid=best_bid,
        intent_id=f"{trade_id}:exit",
        idempotency_key=f"{trade_id}:exit:{token_id}",
        executable_snapshot_id=executable_snapshot_id,
        executable_snapshot_hash=executable_snapshot_hash,
        executable_snapshot_min_tick_size=executable_snapshot_min_tick_size,
        executable_snapshot_min_order_size=executable_snapshot_min_order_size,
        executable_snapshot_neg_risk=executable_snapshot_neg_risk,
    )


def place_sell_order(
    token_id: str,
    shares: float,
    current_price: float,
    best_bid: Optional[float] = None,
) -> dict:
    """Legacy compatibility wrapper for the executor-level exit-order path."""

    result = execute_exit_order(
        create_exit_order_intent(
            trade_id=f"exit-{token_id[:8]}",
            token_id=token_id,
            shares=shares,
            current_price=current_price,
            best_bid=best_bid,
        )
    )
    if result.status == "rejected":
        return {"error": result.reason or "rejected"}
    payload = {
        "orderID": result.external_order_id or result.order_id or "",
        "price": result.submitted_price,
        "shares": result.shares,
    }
    if result.venue_status:
        payload["status"] = result.venue_status
    return payload


def execute_exit_order(
    intent: ExitOrderIntent,
    conn: Optional[sqlite3.Connection] = None,
    decision_id: str = "",
) -> "OrderResult":
    """Place a live sell order via the executor and return a normalized OrderResult.

    Phase order (INV-30):
      1. Price derivation + NaN guard (pure, no I/O)
      2. build: VenueCommand + IdempotencyKey (pure, no I/O)
      3. persist: insert_command (INTENT_CREATED) + append_event (SUBMIT_REQUESTED)
      4. submit: client.place_limit_order (SDK call)
      5. ack: append_event SUBMIT_ACKED / SUBMIT_REJECTED / SUBMIT_UNKNOWN
    """
    from src.architecture.gate_runtime import check as _gate_runtime_check
    _gate_runtime_check("settlement_write")
    from src.data.polymarket_client import PolymarketClient
    from src.execution.command_bus import IdempotencyKey, IntentKind, VenueCommand
    from src.state.venue_command_repo import append_order_fact, insert_command, append_event
    from src.contracts.executable_market_snapshot import MarketSnapshotError
    from src.state.collateral_ledger import CollateralInsufficient

    current_price = intent.current_price
    best_bid = intent.best_bid
    # T5.b 2026-04-23: replace bare 0.01 magic with TickSize typed
    # contract. TickSize.for_market resolves per-token tick size (all
    # Polymarket weather markets currently share $0.01, but the
    # classmethod is the single truth surface for future per-market
    # differentiation).
    from src.contracts.tick_size import TickSize
    tick = TickSize.for_market(token_id=intent.token_id)
    effective_min_tick_size = _submit_tick_size_or_raise(
        intent.executable_snapshot_min_tick_size
        if intent.executable_snapshot_min_tick_size is not None
        else Decimal(str(tick.value))
    )
    base_price = current_price - float(effective_min_tick_size)
    limit_price = base_price

    if best_bid is not None and best_bid < base_price:
        # Slice P3.3b (PR #19 phase 4 closeout, 2026-04-26): typed
        # anticipated-slippage at the price-planning seam. Pre-fix used
        # raw `slippage = current_price - best_bid` + raw `slippage /
        # current_price <= 0.03` arithmetic — both unit-ambiguous and
        # invisible to the type system. Now wraps in SlippageBps which
        # enforces non-negative magnitude + direction semantics. The
        # `.fraction` accessor (200 bps == 0.02 fraction) makes the
        # 3% threshold compare cleanly against a typed value.
        if current_price > 0:
            slip_bps = abs(current_price - best_bid) / current_price * 10_000.0
            slippage = SlippageBps(
                value_bps=slip_bps,
                direction="adverse",  # sell crossing down to bid receives adverse
            )
            if slippage.fraction <= 0.03:
                limit_price = best_bid

    # T5.b 2026-04-23 (also closes T5.a-LOW follow-up): exit-path NaN/
    # ±inf guard. Pre-T5.b the `max(0.01, min(0.99, limit_price))`
    # clamp let NaN propagate into CLOB contact. Reject explicitly
    # here so non-finite prices never reach place_limit_order. Use
    # the same `malformed_limit_price` rejection reason convention as
    # T5.a's entry-path ExecutionPrice boundary guard for symmetry.
    if not math.isfinite(limit_price):
        return OrderResult(
            trade_id=intent.trade_id,
            status="rejected",
            reason=f"malformed_limit_price: non-finite value {limit_price!r}",
            order_role="exit",
            intent_id=intent.intent_id,
            idempotency_key=intent.idempotency_key,
        )
    limit_price = _align_sell_limit_price_to_tick(limit_price, effective_min_tick_size)

    shares = math.floor(intent.shares * 100 + 1e-9) / 100.0
    if shares <= 0:
        return OrderResult(
            trade_id=intent.trade_id,
            status="rejected",
            reason="shares_rounded_to_zero",
            order_role="exit",
            intent_id=intent.intent_id,
            idempotency_key=intent.idempotency_key,
        )
    if not intent.token_id:
        return OrderResult(
            trade_id=intent.trade_id,
            status="rejected",
            reason="no_token_id",
            order_role="exit",
            intent_id=intent.intent_id,
            idempotency_key=intent.idempotency_key,
        )

    cutover_component = _assert_cutover_allows_submit(IntentKind.EXIT)
    risk_allocator_decision = _assert_risk_allocator_allows_exit_submit()

    # -----------------------------------------------------------------------
    # build phase — pure, no I/O (INV-30)
    # -----------------------------------------------------------------------
    # Derive a synthetic decision_id from trade_id when the caller has not
    # supplied a real one. P1.S5 wires real decision_id from upstream;
    # exit path still uses synthetic when called without decision_id.
    effective_decision_id = decision_id or f"exit:{intent.trade_id}"
    idempotency_decision_id = _exit_idempotency_decision_component(
        effective_decision_id,
        intent,
    )
    idem = IdempotencyKey.from_inputs(
        decision_id=idempotency_decision_id,
        token_id=intent.token_id,
        side="SELL",
        price=limit_price,
        size=shares,
        intent_kind=IntentKind.EXIT,
    )
    command_id = uuid.uuid4().hex[:16]
    now_str = datetime.now(timezone.utc).isoformat()
    # ExitOrderIntent carries no market_id; use token_id as market identifier
    # for the command row. P1.S5 can refine if a market_id surface is added.
    market_id_for_cmd = intent.token_id

    # -----------------------------------------------------------------------
    # persist phase — insert command row + transition to SUBMITTING (INV-30)
    # P1.S5: open conn BEFORE lookup so lookup + insert share the same handle.
    # -----------------------------------------------------------------------
    # Post-critic CRITICAL/HIGH (2026-04-26): fallback uses
    # get_trade_connection_with_world() because that's where init_schema
    # actually runs (src/main.py:499-501); get_connection() targets the
    # legacy zeus.db where venue_command tables do not exist. Pre-fix every
    # production live order would have raised OperationalError. Wrapped in
    # try/finally below so the fallback connection is always closed.
    _own_conn = conn is None
    if _own_conn:
        conn = get_trade_connection_with_world_required()
    if not decision_id:
        logger.warning(
            "EXECUTOR: synthetic decision_id %s — retry-idempotency NOT guaranteed; "
            "pass decision_id explicitly",
            effective_decision_id,
        )
    try:
        exit_snapshot_identity_component = _exit_snapshot_identity_component(conn, intent)
        if not exit_snapshot_identity_component.get("allowed"):
            reason = str(
                exit_snapshot_identity_component.get("reason")
                or "exit_snapshot_identity_failed"
            )
            logger.warning(
                "execute_exit_order: exit snapshot identity blocked submit "
                "for trade_id=%s: %s",
                intent.trade_id,
                reason,
            )
            return OrderResult(
                trade_id=intent.trade_id,
                status="rejected",
                reason=f"exit_snapshot_identity:{reason}",
                submitted_price=limit_price,
                shares=shares,
                order_role="exit",
                intent_id=intent.intent_id,
                idempotency_key=idem.value,
            )
        # Exit is IOC, never all-or-nothing: coerce a TAKER FOK selection to FAK
        # so a thin/dying book realizes a partial exit instead of killing the
        # whole sell (live 2026-06-24: Houston FOK rejects, market 0.356->0.076).
        selected_order_type = _select_risk_allocator_order_type(conn, intent.executable_snapshot_id)
        order_type = _exit_order_type(selected_order_type)
        heartbeat_component = _assert_heartbeat_allows_submit(order_type)
        ws_gap_component = _assert_ws_gap_allows_submit(intent.token_id)
        collateral_refresh_component = _refresh_exit_collateral_snapshot_for_submit(
            conn,
            token_id=intent.token_id,
            shares=shares,
        )
        collateral_component = _assert_collateral_allows_sell(intent.token_id, shares, conn=conn)

        # -------------------------------------------------------------------
        # P1.S5: pre-submit idempotency lookup (NC-19 fast-path gate).
        # Check BEFORE the INSERT to avoid a failed-INSERT roundtrip on retries.
        # The IntegrityError handler below is the race-condition safety belt.
        # -------------------------------------------------------------------
        from src.state.venue_command_repo import (
            find_command_by_idempotency_key,
            find_unknown_command_by_economic_intent,
        )
        from src.execution.command_bus import VenueCommand
        from src.execution.exit_safety import (
            ExitMutex,
            can_submit_replacement_sell,
        )
        pre_lookup_row = find_command_by_idempotency_key(conn, idem.value)
        if pre_lookup_row is not None:
            exit_existing_mismatch = _exit_existing_command_mismatch_reason(
                conn,
                intent,
                pre_lookup_row,
            )
            if exit_existing_mismatch is not None:
                logger.warning(
                    "execute_exit_order: idempotency fast path blocked by "
                    "exit snapshot identity mismatch for trade_id=%s idem=%s: %s",
                    intent.trade_id,
                    idem.value,
                    exit_existing_mismatch,
                )
                return _reject_exit_existing_command_mismatch(
                    trade_id=intent.trade_id,
                    intent=intent,
                    shares=shares,
                    limit_price=limit_price,
                    idem_value=idem.value,
                    reason=exit_existing_mismatch,
                )
            logger.info(
                "execute_exit_order: pre-submit lookup found existing command for "
                "idem=%s trade_id=%s — skipping submit",
                idem.value, intent.trade_id,
            )
            return _orderresult_from_existing(
                conn,
                VenueCommand.from_row(pre_lookup_row),
                trade_id=intent.trade_id,
                limit_price=limit_price,
                shares=shares,
                idem_value=idem.value,
                intent_id=intent.intent_id,
                order_role="exit",
            )
        economic_unknown_row = find_unknown_command_by_economic_intent(
            conn,
            intent_kind=IntentKind.EXIT.value,
            token_id=intent.token_id,
            side="SELL",
            price=limit_price,
            size=shares,
            exclude_idempotency_key=idem.value,
        )
        if economic_unknown_row is not None:
            exit_existing_mismatch = _exit_existing_command_mismatch_reason(
                conn,
                intent,
                economic_unknown_row,
            )
            if exit_existing_mismatch is not None:
                logger.warning(
                    "execute_exit_order: economic-unknown fast path blocked by "
                    "exit snapshot identity mismatch for trade_id=%s idem=%s: %s",
                    intent.trade_id,
                    idem.value,
                    exit_existing_mismatch,
                )
                return _reject_exit_existing_command_mismatch(
                    trade_id=intent.trade_id,
                    intent=intent,
                    shares=shares,
                    limit_price=limit_price,
                    idem_value=idem.value,
                    reason=exit_existing_mismatch,
                )
            logger.warning(
                "execute_exit_order: same economic intent is already unresolved as "
                "unknown_side_effect (idem=%s trade_id=%s)",
                idem.value, intent.trade_id,
            )
            return _orderresult_from_economic_unknown(
                VenueCommand.from_row(economic_unknown_row),
                trade_id=intent.trade_id,
                limit_price=limit_price,
                shares=shares,
                idem_value=idem.value,
                intent_id=intent.intent_id,
                order_role="exit",
            )

        replacement_allowed, replacement_block_reason = can_submit_replacement_sell(
            conn,
            intent.trade_id,
            intent.token_id,
            exclude_idempotency_key=idem.value,
        )
        if not replacement_allowed:
            logger.warning(
                "execute_exit_order: replacement sell blocked for trade_id=%s token=%s: %s",
                intent.trade_id, intent.token_id, replacement_block_reason,
            )
            return OrderResult(
                trade_id=intent.trade_id,
                status="rejected",
                reason=replacement_block_reason or "replacement_sell_blocked",
                submitted_price=limit_price,
                shares=shares,
                order_role="exit",
                intent_id=intent.intent_id,
                idempotency_key=idem.value,
            )

        try:
            pre_submit_envelope = _build_pre_submit_envelope(
                conn,
                command_id=command_id,
                snapshot_id=intent.executable_snapshot_id,
                token_id=intent.token_id,
                side="SELL",
                price=limit_price,
                size=shares,
                order_type=order_type,
                post_only=False,
                captured_at=now_str,
            )
            envelope_id = _persist_prebuilt_submit_envelope(
                conn,
                pre_submit_envelope,
                command_id=command_id,
            )
            insert_command(
                conn,
                command_id=command_id,
                snapshot_id=intent.executable_snapshot_id,
                envelope_id=envelope_id,
                position_id=intent.trade_id,
                decision_id=effective_decision_id,
                idempotency_key=idem.value,
                intent_kind=IntentKind.EXIT.value,
                market_id=market_id_for_cmd,
                token_id=intent.token_id,
                side="SELL",
                size=shares,
                price=limit_price,
                created_at=now_str,
                snapshot_checked_at=now_str,
                expected_min_tick_size=intent.executable_snapshot_min_tick_size,
                expected_min_order_size=intent.executable_snapshot_min_order_size,
                expected_neg_risk=intent.executable_snapshot_neg_risk,
            )
            if not ExitMutex(conn).acquire(intent.trade_id, intent.token_id, command_id):
                append_event(
                    conn,
                    command_id=command_id,
                    event_type="REVIEW_REQUIRED",
                    occurred_at=now_str,
                    payload={"reason": "exit_mutex_held"},
                )
                conn.commit()
                return OrderResult(
                    trade_id=intent.trade_id,
                    status="rejected",
                    reason="exit_mutex_held",
                    submitted_price=limit_price,
                    shares=shares,
                    order_role="exit",
                    intent_id=intent.intent_id,
                    idempotency_key=idem.value,
                    command_state="REVIEW_REQUIRED",
                )
            append_event(
                conn,
                command_id=command_id,
                event_type="SUBMIT_REQUESTED",
                occurred_at=now_str,
                payload={
                    "order_type": order_type,
                    "execution_capability": _build_execution_capability(
                        action="EXIT",
                        command_id=command_id,
                        intent_kind=IntentKind.EXIT.value,
                        order_type=order_type,
                        venue_order_type=order_type,
                        risk_allocator_selected_order_type=selected_order_type,
                        token_id=intent.token_id,
                        snapshot_id=intent.executable_snapshot_id,
                        freshness_time=now_str,
                        components=[
                            cutover_component,
                            _component_from_result(
                                "risk_allocator",
                                risk_allocator_decision,
                                reduce_only=True,
                            ),
                            _capability_component(
                                "order_type_selection",
                                order_type=order_type,
                                selected_order_type=selected_order_type,
                            ),
                            heartbeat_component,
                            ws_gap_component,
                            collateral_refresh_component,
                            collateral_component,
                            _capability_component("replacement_sell_guard"),
                            _exit_decision_source_component(),
                            exit_snapshot_identity_component,
                            _capability_component("executable_snapshot_gate"),
                        ],
                    ),
                },
            )
            _reserve_collateral_for_sell(command_id, intent.token_id, shares, conn)
            conn.commit()
        except MarketSnapshotError as exc:
            return OrderResult(
                trade_id=intent.trade_id,
                status="rejected",
                reason=f"executable_snapshot_gate: {exc}",
                submitted_price=limit_price,
                shares=shares,
                order_role="exit",
                intent_id=intent.intent_id,
                idempotency_key=idem.value,
            )
        except PreSubmitIdentityBindingError as exc:
            try:
                conn.rollback()
            except Exception:
                pass
            return OrderResult(
                trade_id=intent.trade_id,
                status="rejected",
                reason=f"pre_submit_identity_binding_failed: {exc}",
                submitted_price=limit_price,
                shares=shares,
                order_role="exit",
                intent_id=intent.intent_id,
                idempotency_key=idem.value,
            )
        except CollateralInsufficient as exc:
            rej_time = datetime.now(timezone.utc).isoformat()
            if _venue_command_exists(conn, command_id):
                try:
                    append_event(
                        conn,
                        command_id=command_id,
                        event_type="SUBMIT_REJECTED",
                        occurred_at=rej_time,
                        payload={
                            "reason": "pre_submit_collateral_reservation_failed",
                            "detail": str(exc),
                            "exception_type": type(exc).__name__,
                            "side_effect_boundary_crossed": False,
                            "sdk_submit_attempted": False,
                        },
                    )
                    if _own_conn:
                        conn.commit()
                except Exception as inner:
                    logger.error(
                        "execute_exit_order: SUBMIT_REJECTED append_event failed after "
                        "pre-submit collateral reservation failure (command_id=%s "
                        "trade_id=%s): inner=%s original=%s",
                        command_id,
                        intent.trade_id,
                        inner,
                        exc,
                    )
            else:
                try:
                    conn.rollback()
                except Exception:
                    pass
                logger.warning(
                    "execute_exit_order: pre-command collateral rejection "
                    "(command_id=%s trade_id=%s) — no venue command/event to append; "
                    "no order placed: %s",
                    command_id,
                    intent.trade_id,
                    exc,
                )
            return OrderResult(
                trade_id=intent.trade_id,
                status="rejected",
                reason=f"pre_submit_collateral_reservation_failed: {exc}",
                submitted_price=limit_price,
                shares=shares,
                order_role="exit",
                intent_id=intent.intent_id,
                idempotency_key=idem.value,
                command_state="REJECTED",
            )
        except sqlite3.IntegrityError as exc:
            # Race-condition safety belt: another process inserted between our
            # lookup and our INSERT. Existing command is the canonical record.
            logger.warning(
                "execute_exit_order: idempotency key collision (race) for trade_id=%s idem=%s: %s",
                intent.trade_id, idem.value, exc,
            )
            existing_row = find_command_by_idempotency_key(conn, idem.value)
            if existing_row is not None:
                exit_existing_mismatch = _exit_existing_command_mismatch_reason(
                    conn,
                    intent,
                    existing_row,
                )
                if exit_existing_mismatch is not None:
                    logger.warning(
                        "execute_exit_order: idempotency race fallback blocked by "
                        "exit snapshot identity mismatch for trade_id=%s idem=%s: %s",
                        intent.trade_id,
                        idem.value,
                        exit_existing_mismatch,
                    )
                    return _reject_exit_existing_command_mismatch(
                        trade_id=intent.trade_id,
                        intent=intent,
                        shares=shares,
                        limit_price=limit_price,
                        idem_value=idem.value,
                        reason=exit_existing_mismatch,
                    )
                return _orderresult_from_existing(
                    conn,
                    VenueCommand.from_row(existing_row),
                    trade_id=intent.trade_id,
                    limit_price=limit_price,
                    shares=shares,
                    idem_value=idem.value,
                    intent_id=intent.intent_id,
                    order_role="exit",
                )
            # Defensive fallback: row not found despite collision
            return OrderResult(
                trade_id=intent.trade_id,
                status="rejected",
                reason=f"idempotency_collision: {exc}",
                submitted_price=limit_price,
                shares=shares,
                order_role="exit",
                intent_id=intent.intent_id,
                idempotency_key=intent.idempotency_key,
            )
        except sqlite3.OperationalError as exc:
            # C-DBLOCK-UNKNOWN (2026-06-16): symmetric with the entry path. A transient
            # 'database is locked' in this PRE-VENUE persist phase fires BEFORE
            # place_limit_order — NO order was placed (side_effect_boundary_crossed=False).
            # Without an OperationalError handler it propagated to the event-bound catch-all
            # as POST_SUBMIT_UNKNOWN, tripping the governor unknown_side_effect kill-switch
            # (limit=0) and HALTING all submits. It is NOT a side effect: roll back the
            # uncommitted persist and return a CLEAN transient rejection so the candidate
            # re-attempts next cycle. Non-lock OperationalError re-raises (unchanged).
            if "database is locked" not in str(exc).lower():
                raise
            try:
                conn.rollback()
            except Exception:
                pass
            logger.warning(
                "execute_exit_order: pre-venue persist 'database is locked' (command_id=%s "
                "trade_id=%s) — no order placed; transient reject, retry next cycle: %s",
                command_id, intent.trade_id, exc,
            )
            return OrderResult(
                trade_id=intent.trade_id,
                status="rejected",
                reason=f"pre_submit_db_locked_transient: {exc}",
                submitted_price=limit_price,
                shares=shares,
                order_role="exit",
                intent_id=intent.intent_id,
                idempotency_key=idem.value,
            )

        logger.info(
            "SELL ORDER: token=%s...%s @ %.3f limit, %.2f shares (mid=%.3f, bid=%s)",
            intent.token_id[:8], intent.token_id[-4:], limit_price, shares,
            current_price, f"{best_bid:.3f}" if best_bid else "N/A",
        )

        # -----------------------------------------------------------------------
        # submit phase — SDK call (INV-30: row already SUBMITTING)
        # -----------------------------------------------------------------------
        try:
            client = PolymarketClient()
        except Exception as exc:
            # Constructor / credential / adapter setup failures happen before
            # any venue submit side effect. They are safe terminal rejections,
            # not M2 unknown-side-effect outcomes.
            rej_time = datetime.now(timezone.utc).isoformat()
            try:
                append_event(
                    conn,
                    command_id=command_id,
                    event_type="SUBMIT_REJECTED",
                    occurred_at=rej_time,
                    payload={
                        "reason": "pre_submit_client_init_failed",
                        "exception_type": type(exc).__name__,
                        "exception_message": str(exc),
                    },
                )
                if _own_conn:
                    conn.commit()
            except Exception as inner:
                logger.error(
                    "execute_exit_order: SUBMIT_REJECTED append_event failed after client "
                    "init exception (command_id=%s trade_id=%s): inner=%s original=%s",
                    command_id, intent.trade_id, inner, exc,
                )
            return OrderResult(
                trade_id=intent.trade_id,
                status="rejected",
                reason=f"pre_submit_client_init_failed: {exc}",
                submitted_price=limit_price,
                shares=shares,
                order_role="exit",
                intent_id=intent.intent_id,
                idempotency_key=idem.value,
                command_state="REJECTED",
            )
        if pre_submit_envelope is not None and hasattr(client, "bind_submission_envelope"):
            client.bind_submission_envelope(pre_submit_envelope)
        # PR 6 (2026-05-19): capture zeus_submit_intent_time immediately before network call.
        _zeus_submit_intent_time = datetime.now(timezone.utc).isoformat()
        try:
            result = client.place_limit_order(
                token_id=intent.token_id,
                price=limit_price,
                size=shares,
                side="SELL",
                order_type=order_type,
            )
        except Exception as exc:
            # M2: place_limit_order has crossed the submit side-effect boundary.
            # Treat SDK/network exceptions as unknown side effects. Narrow
            # synchronous CLOB validation failures are deterministic rejections:
            # no order id is created and retry requires changed inputs/egress.
            ack_time = datetime.now(timezone.utc).isoformat()
            deterministic_rejection_payload = _deterministic_submit_rejection_payload(
                exc,
                idempotency_key=idem.value,
            )
            try:
                if deterministic_rejection_payload is not None:
                    append_event(
                        conn,
                        command_id=command_id,
                        event_type="SUBMIT_REJECTED",
                        occurred_at=ack_time,
                        payload=deterministic_rejection_payload,
                    )
                else:
                    append_event(
                        conn,
                        command_id=command_id,
                        event_type="SUBMIT_TIMEOUT_UNKNOWN",
                        occurred_at=ack_time,
                        payload={
                            "reason": "post_submit_exception_possible_side_effect",
                            "exception_type": type(exc).__name__,
                            "exception_message": str(exc),
                            "idempotency_key": idem.value,
                        },
                    )
                conn.commit()
            except Exception as inner:
                logger.error(
                    "execute_exit_order: terminal SDK-exception event append failed "
                    "(command_id=%s trade_id=%s): inner=%s original=%s",
                    command_id, intent.trade_id, inner, exc,
                )
            logger.error("Live exit order SDK exception: %s", exc)
            if deterministic_rejection_payload is not None:
                return OrderResult(
                    trade_id=intent.trade_id,
                    status="rejected",
                    reason=f"{deterministic_rejection_payload['reason']}: {exc}",
                    submitted_price=limit_price,
                    shares=shares,
                    order_role="exit",
                    intent_id=intent.intent_id,
                    idempotency_key=idem.value,
                    command_state="REJECTED",
                )
            return OrderResult(
                trade_id=intent.trade_id,
                status="unknown_side_effect",
                reason=f"submit_unknown_side_effect: {exc}",
                submitted_price=limit_price,
                shares=shares,
                order_role="exit",
                intent_id=intent.intent_id,
                idempotency_key=idem.value,
                command_state="SUBMIT_UNKNOWN_SIDE_EFFECT",
            )

        # -----------------------------------------------------------------------
        # ack phase — durable journal record of outcome
        # -----------------------------------------------------------------------
        ack_time = datetime.now(timezone.utc).isoformat()
        if result is None:
            try:
                append_event(
                    conn,
                    command_id=command_id,
                    event_type="REVIEW_REQUIRED",
                    occurred_at=ack_time,
                    payload={
                        "reason": "final_submission_envelope_persistence_failed",
                        "detail": "place_limit_order returned None",
                        "idempotency_key": idem.value,
                    },
                )
                conn.commit()
            except Exception as inner:
                logger.error(
                    "execute_exit_order: REVIEW_REQUIRED append_event failed after missing final "
                    "submission envelope (command_id=%s): %s",
                    command_id, inner,
                )
            return OrderResult(
                trade_id=intent.trade_id,
                status="unknown_side_effect",
                reason="final_submission_envelope_persistence_failed: place_limit_order returned None",
                submitted_price=limit_price,
                shares=shares,
                order_role="exit",
                intent_id=intent.intent_id,
                idempotency_key=idem.value,
                command_state="REVIEW_REQUIRED",
            )

        try:
            final_envelope_payload = _persist_final_submission_envelope_payload(
                conn,
                result,
                command_id=command_id,
            )
        except FinalSubmissionEnvelopePersistenceError as exc:
            try:
                append_event(
                    conn,
                    command_id=command_id,
                    event_type="REVIEW_REQUIRED",
                    occurred_at=ack_time,
                    payload=_submit_result_review_required_payload(
                        result,
                        reason="final_submission_envelope_persistence_failed",
                        detail=str(exc),
                        idempotency_key=idem.value,
                    ),
                )
                conn.commit()
            except Exception as inner:
                logger.error(
                    "execute_exit_order: REVIEW_REQUIRED append_event failed after final "
                    "submission envelope persistence failure (command_id=%s): inner=%s original=%s",
                    command_id, inner, exc,
                )
            return OrderResult(
                trade_id=intent.trade_id,
                status="unknown_side_effect",
                reason=f"final_submission_envelope_persistence_failed: {exc}",
                order_id=_submit_result_order_id(result),
                submitted_price=limit_price,
                shares=shares,
                order_role="exit",
                intent_id=intent.intent_id,
                external_order_id=_submit_result_order_id(result),
                venue_status=str(result.get("status") or "") if isinstance(result, dict) else "",
                idempotency_key=idem.value,
                command_state="REVIEW_REQUIRED",
            )
        order_id = _submit_result_order_id(result)
        if result.get("success") is False:
            rejection_reason = (
                result.get("errorCode")
                or result.get("error_code")
                or result.get("reason")
                or "submit_rejected"
            )
            try:
                append_event(
                    conn,
                    command_id=command_id,
                    event_type="SUBMIT_REJECTED",
                    occurred_at=ack_time,
                    payload={
                        "reason": str(rejection_reason),
                        "detail": result.get("errorMessage") or result.get("error_message") or "",
                        **final_envelope_payload,
                    },
                )
                conn.commit()
            except Exception as inner:
                logger.error(
                    "execute_exit_order: SUBMIT_REJECTED (success_false) append_event failed "
                    "(command_id=%s): %s",
                    command_id, inner,
                )
                durable_state = _mark_post_submit_persistence_failure(
                    conn,
                    command_id=command_id,
                    order_id=order_id,
                    occurred_at=ack_time,
                    reason="terminal_rejection_persistence_failed_after_side_effect",
                    detail=str(inner),
                    idempotency_key=idem.value,
                    order_role="exit",
                )
                return OrderResult(
                    trade_id=intent.trade_id,
                    status="unknown_side_effect",
                    reason="terminal_rejection_persistence_failed_after_side_effect",
                    submitted_price=limit_price,
                    shares=shares,
                    order_role="exit",
                    intent_id=intent.intent_id,
                    idempotency_key=idem.value,
                    venue_status=str(result.get("status") or ""),
                    command_id=command_id,
                    command_state=durable_state or "REVIEW_REQUIRED",
                )
            return OrderResult(
                trade_id=intent.trade_id,
                status="rejected",
                reason=str(rejection_reason),
                submitted_price=limit_price,
                shares=shares,
                order_role="exit",
                intent_id=intent.intent_id,
                idempotency_key=intent.idempotency_key,
                venue_status=str(result.get("status") or ""),
                command_id=command_id,  # F7: propagate so log_execution_fact records FK
            )
        if not order_id:
            try:
                append_event(
                    conn,
                    command_id=command_id,
                    event_type="SUBMIT_REJECTED",
                    occurred_at=ack_time,
                    payload={"reason": "missing_order_id", **final_envelope_payload},
                )
                conn.commit()
            except Exception as inner:
                logger.error(
                    "execute_exit_order: SUBMIT_REJECTED (missing_order_id) append_event failed "
                    "(command_id=%s): %s",
                    command_id, inner,
                )
                durable_state = _mark_post_submit_persistence_failure(
                    conn,
                    command_id=command_id,
                    order_id=None,
                    occurred_at=ack_time,
                    reason="terminal_rejection_persistence_failed_after_side_effect",
                    detail=str(inner),
                    idempotency_key=idem.value,
                    order_role="exit",
                )
                return OrderResult(
                    trade_id=intent.trade_id,
                    status="unknown_side_effect",
                    reason="terminal_rejection_persistence_failed_after_side_effect",
                    submitted_price=limit_price,
                    shares=shares,
                    order_role="exit",
                    intent_id=intent.intent_id,
                    idempotency_key=idem.value,
                    venue_status=str(result.get("status") or ""),
                    command_id=command_id,
                    command_state=durable_state or "REVIEW_REQUIRED",
                )
            return OrderResult(
                trade_id=intent.trade_id,
                status="rejected",
                reason="missing_order_id",
                submitted_price=limit_price,
                shares=shares,
                order_role="exit",
                intent_id=intent.intent_id,
                idempotency_key=intent.idempotency_key,
                venue_status=str(result.get("status") or ""),
                command_id=command_id,  # F7: propagate so log_execution_fact records FK
            )

        # SUBMIT_ACKED — order placed successfully
        # C-DBLOCK-UNKNOWN (2026-06-16): symmetric with the entry path. The venue side
        # effect already happened, so this records a KNOWN outcome — retried on a transient
        # 'database is locked' instead of degrading a good order to unknown_side_effect
        # (which trips the governor kill-switch). See _retry_persist_on_db_lock.
        def _persist_exit_ack_facts() -> None:
            append_event(
                conn,
                command_id=command_id,
                event_type="SUBMIT_ACKED",
                occurred_at=ack_time,
                payload={
                    "venue_order_id": order_id,
                    "order_type": order_type,
                    **final_envelope_payload,
                },
            )
            append_order_fact(
                conn,
                venue_order_id=order_id,
                command_id=command_id,
                state=_venue_submit_order_fact_state(result),
                remaining_size=_venue_submit_remaining_size(
                    result,
                    shares,
                    side="SELL",
                ),
                matched_size=_venue_submit_matched_size(result, side="SELL"),
                source="REST",
                observed_at=ack_time,
                # C4 telemetry-truth: REST ACK response carries no server matchTime;
                # venue_timestamp=None (honest absence). ack_time is Zeus receipt
                # wall-clock only, labelled via observed_at.
                venue_timestamp=None,
                raw_payload_hash=_canonical_payload_hash(
                    {
                        "command_id": command_id,
                        "venue_order_id": order_id,
                        "submit_result": result,
                    }
                ),
                raw_payload_json={
                    "venue_order_id": order_id,
                    "submit_result": _jsonable_payload(result),
                    "source": "place_limit_order_ack",
                },
            )
            # PR 6 (2026-05-19): persist submit intent + venue ack timing to settlement_commands.
            # Best-effort: do not fail the order on UPDATE error (column may not exist on older DBs).
            try:
                conn.execute(
                    "UPDATE settlement_commands SET zeus_submit_intent_time = COALESCE(zeus_submit_intent_time, ?), venue_ack_time = COALESCE(venue_ack_time, ?) WHERE command_id = ?",
                    (_zeus_submit_intent_time, ack_time, command_id),
                )
            except Exception as _timing_exc:
                logger.debug("PR6 timing update skipped (column absent on older DB): %s", _timing_exc)
            # Exit submission uses the same durable side-effect boundary as entry:
            # ACK/order facts must be visible even when the caller owns conn.
            conn.commit()

        try:
            _retry_persist_on_db_lock(
                conn, _persist_exit_ack_facts, what="exit_ack_persistence"
            )
        except Exception as inner:
            logger.error(
                "execute_exit_order: SUBMIT_ACKED append_event failed (command_id=%s order_id=%s): %s",
                command_id, order_id, inner,
            )
            durable_state = _mark_post_submit_persistence_failure(
                conn,
                command_id=command_id,
                order_id=order_id,
                occurred_at=ack_time,
                reason="exit_ack_persistence_failed_after_side_effect",
                detail=str(inner),
                idempotency_key=idem.value,
                order_role="exit_order",
            )
            return OrderResult(
                trade_id=intent.trade_id,
                status="unknown_side_effect",
                reason=f"exit_ack_persistence_failed_after_side_effect: {inner}",
                order_id=order_id,
                submitted_price=limit_price,
                shares=shares,
                order_role="exit",
                intent_id=intent.intent_id,
                external_order_id=order_id,
                command_id=command_id,
                venue_status=str(result.get("status") or "placed"),
                idempotency_key=idem.value,
                command_state=durable_state,
            )

        result_obj = OrderResult(
            trade_id=intent.trade_id,
            status="pending",
            reason="sell order posted",
            order_id=order_id,
            submitted_price=limit_price,
            shares=shares,
            order_role="exit",
            intent_id=intent.intent_id,
            external_order_id=order_id,
            command_id=command_id,  # F7: FK to venue_commands row
            venue_status=str(result.get("status") or "placed"),
            idempotency_key=idem.value,
            command_state="ACKED",  # P1.S5 INV-32: materialize_position gates on this
        )
        try:
            alert_trade(
                direction="SELL",
                market=intent.token_id,
                price=limit_price,
                size_usd=float(shares * limit_price),
                strategy="exit_order",
                edge=float(current_price - limit_price),
                mode=get_mode(),
            )
        except Exception as exc:
            logger.warning("Discord trade alert failed for exit order: %s", exc)
        return result_obj
    finally:
        if _own_conn:
            conn.close()


@capability("on_chain_mutation", lease=True)
@capability("live_venue_submit", lease=True)
@protects("INV-21", "INV-04")
def _live_order(
    trade_id: str,
    intent: ExecutionIntent,
    shares: float,
    conn: Optional[sqlite3.Connection] = None,
    decision_id: str = "",
) -> "OrderResult":
    """Live mode: place order via Polymarket CLOB API.

    Phase order (INV-30):
      1. ExecutionPrice validation (synchronous; no I/O)
      2. build: VenueCommand + IdempotencyKey (pure; no I/O)
      3. persist: insert_command (INTENT_CREATED) + append_event (SUBMIT_REQUESTED)
      4. V2 preflight (if fails, append SUBMIT_REJECTED; return rejected)
      5. submit: client.place_limit_order (SDK call)
      6. ack: append_event SUBMIT_ACKED / SUBMIT_REJECTED / SUBMIT_UNKNOWN
    """
    from src.architecture.gate_runtime import check as _gate_runtime_check
    _gate_runtime_check("live_venue_submit")
    _gate_runtime_check("on_chain_mutation")
    from src.data.polymarket_client import PolymarketClient, V2PreflightError
    from src.execution.command_bus import IdempotencyKey, IntentKind
    from src.state.venue_command_repo import append_order_fact, append_trade_fact, insert_command, append_event
    from src.contracts.executable_market_snapshot import MarketSnapshotError
    from src.state.collateral_ledger import CollateralInsufficient

    cutover_component = _assert_cutover_allows_submit(IntentKind.ENTRY)

    timeout = intent.timeout_seconds

    # -----------------------------------------------------------------------
    # Phase 1: ExecutionPrice validation (pre-persist guard)
    # T5.a typed-boundary assertion (D3 defense-in-depth): construct
    # ExecutionPrice from the pre-computed limit_price at the executor
    # seam. ExecutionPrice.__post_init__ refuses non-finite or
    # out-of-range values; with currency="probability_units" it also
    # refuses values > 1.0. This is a NARROW STRUCTURAL GUARD only —
    # not a Kelly-safety guarantee. The fee-deducted/Kelly-safe
    # semantics are upstream evaluator's responsibility, so we use
    # price_type="ask", fee_deducted=False here to avoid a semantic
    # white lie at the executor seam (see T5.a critic review
    # 2026-04-23: the guards fire identically for finite/nonneg/≤1
    # regardless of price_type or fee_deducted). This only catches
    # "malformed limit_price reached executor" regressions (NaN,
    # negative, >1.0 prob), not fee-accounting bugs. Rejection reason
    # is named "malformed_limit_price" to avoid implying Kelly-semantic
    # violation.
    # -----------------------------------------------------------------------
    try:
        ExecutionPrice(
            value=intent.limit_price,
            price_type="ask",
            fee_deducted=False,
            currency="probability_units",
        )
    except (ValueError, ExecutionPriceContractError) as exc:
        logger.error(
            "LIVE ORDER boundary check failed: limit_price=%r rejected by "
            "ExecutionPrice contract: %s",
            intent.limit_price,
            exc,
        )
        return OrderResult(
            trade_id=trade_id,
            status="rejected",
            reason=f"malformed_limit_price: {exc}",
            submitted_price=intent.limit_price,
            shares=shares,
            order_role="entry",
        )

    try:
        risk_allocator_decision = _assert_risk_allocator_allows_submit(intent)
    except Exception as exc:
        return OrderResult(
            trade_id=trade_id,
            status="rejected",
            reason=f"risk_allocator_pre_submit_blocked: {exc}",
            submitted_price=intent.limit_price,
            shares=shares,
            order_role="entry",
            command_state="REJECTED",
        )
    required_pusd_micro = _buy_order_notional_micro(intent, shares)

    # -----------------------------------------------------------------------
    # Phase 2: build — pure, no I/O (INV-30)
    # Derive a synthetic decision_id when caller hasn't supplied a real one.
    # -----------------------------------------------------------------------
    effective_decision_id = decision_id or f"entry:{trade_id}"
    idem = IdempotencyKey.from_inputs(
        decision_id=effective_decision_id,
        token_id=intent.token_id,
        side="BUY",
        price=intent.limit_price,
        size=shares,
        intent_kind=IntentKind.ENTRY,
    )
    command_id = uuid.uuid4().hex[:16]
    now_str = datetime.now(timezone.utc).isoformat()

    # -----------------------------------------------------------------------
    # Phase 3: persist — insert command row + transition to SUBMITTING (INV-30)
    # P1.S5: open conn BEFORE lookup so lookup + insert share the same handle.
    # -----------------------------------------------------------------------
    # Post-critic CRITICAL/HIGH: fallback uses get_trade_connection_with_world()
    # because that's where init_schema runs; get_connection() targets zeus.db.
    # Wrapped in try/finally so the fallback connection is always closed.
    _own_conn = conn is None
    if _own_conn:
        conn = get_trade_connection_with_world_required()
    if not decision_id:
        logger.warning(
            "EXECUTOR: synthetic decision_id %s — retry-idempotency NOT guaranteed; "
            "pass decision_id explicitly",
            effective_decision_id,
        )
    try:  # outer: ensures conn is closed when _own_conn (HIGH fix)
        if not decision_id or effective_decision_id.startswith("entry:"):
            return OrderResult(
                trade_id=trade_id,
                status="rejected",
                reason="entry_decision_identity:missing_durable_live_entry_decision_id",
                submitted_price=intent.limit_price,
                shares=shares,
                order_role="entry",
                idempotency_key=idem.value,
                command_state="REJECTED",
            )
        corrected_identity_component = _corrected_entry_identity_component(conn, intent)
        if not corrected_identity_component.get("allowed"):
            reason = str(
                corrected_identity_component.get("reason")
                or "corrected_identity_failed"
            )
            logger.warning(
                "_live_order: corrected execution identity blocked entry submit "
                "for trade_id=%s: %s",
                trade_id,
                reason,
            )
            return OrderResult(
                trade_id=trade_id,
                status="rejected",
                reason=f"corrected_execution_identity:{reason}",
                submitted_price=intent.limit_price,
                shares=shares,
                order_role="entry",
                idempotency_key=idem.value,
            )
        order_type = _select_risk_allocator_order_type(conn, intent.executable_snapshot_id)
        raw_submit_order_type = getattr(intent, "submit_order_type", None)
        submit_order_type = raw_submit_order_type if isinstance(raw_submit_order_type, str) else None
        if submit_order_type is not None and not _risk_allocator_order_type_allows_intent(
            selected_order_type=order_type,
            intent_order_type=submit_order_type,
        ):
            return OrderResult(
                trade_id=trade_id,
                status="rejected",
                reason=(
                    "final_order_type_mismatch: "
                    f"intent={submit_order_type} selected={order_type}"
                ),
                submitted_price=intent.limit_price,
                shares=shares,
                order_role="entry",
            )
        effective_order_type = submit_order_type or order_type
        submit_post_only = bool(getattr(intent, "post_only", False))
        if submit_post_only and effective_order_type not in {"GTC", "GTD"}:
            return OrderResult(
                trade_id=trade_id,
                status="rejected",
                reason=f"post_only_order_type_mismatch: order_type={effective_order_type}",
                submitted_price=intent.limit_price,
                shares=shares,
                order_role="entry",
                idempotency_key=idem.value,
            )
        taker_quality_component = _entry_taker_quality_component(
            effective_order_type=effective_order_type,
            post_only=submit_post_only,
            intent_order_type=submit_order_type,
            taker_quality_proof=getattr(intent, "taker_quality_proof", None),
        )
        if not taker_quality_component.get("allowed"):
            reason = str(taker_quality_component.get("reason") or "entry_taker_quality")
            logger.warning(
                "_live_order: entry taker-quality policy blocked before command "
                "persistence for trade_id=%s token=%s reason=%s details=%s",
                trade_id,
                intent.token_id,
                reason,
                taker_quality_component,
            )
            return OrderResult(
                trade_id=trade_id,
                status="rejected",
                reason=f"entry_taker_quality:{reason}",
                submitted_price=intent.limit_price,
                shares=shares,
                order_role="entry",
                idempotency_key=idem.value,
                command_state="REJECTED",
            )
        entry_economics_component = _entry_economics_component(intent, shares=shares)
        if not entry_economics_component.get("allowed"):
            reason = str(entry_economics_component.get("reason") or "entry_economics")
            logger.warning(
                "_live_order: entry economics blocked before command persistence "
                "for trade_id=%s token=%s reason=%s details=%s",
                trade_id,
                intent.token_id,
                reason,
                entry_economics_component,
            )
            return OrderResult(
                trade_id=trade_id,
                status="rejected",
                reason=f"entry_economics:{reason}",
                submitted_price=intent.limit_price,
                shares=shares,
                order_role="entry",
                idempotency_key=idem.value,
                command_state="REJECTED",
            )
        actionable_certificate_component = _entry_actionable_certificate_component(
            conn,
            intent,
            decision_id=effective_decision_id,
        )
        if not actionable_certificate_component.get("allowed"):
            reason = str(
                actionable_certificate_component.get("reason")
                or "entry_actionable_certificate"
            )
            logger.warning(
                "_live_order: actionable certificate guard blocked before command "
                "persistence for trade_id=%s token=%s reason=%s details=%s",
                trade_id,
                intent.token_id,
                reason,
                actionable_certificate_component,
            )
            return OrderResult(
                trade_id=trade_id,
                status="rejected",
                reason=f"entry_actionable_certificate:{reason}",
                submitted_price=intent.limit_price,
                shares=shares,
                order_role="entry",
                idempotency_key=idem.value,
                command_state="REJECTED",
            )
        amount_precision_error = _venue_submit_amount_precision_rejection_reason(
            intent,
            shares=shares,
            order_type=effective_order_type,
        )
        if amount_precision_error is not None:
            return OrderResult(
                trade_id=trade_id,
                status="rejected",
                reason=f"invalid_submit_amount_precision: {amount_precision_error}",
                submitted_price=intent.limit_price,
                shares=shares,
                order_role="entry",
                idempotency_key=idem.value,
            )
        heartbeat_component = _assert_heartbeat_allows_submit(effective_order_type)
        ws_gap_component = _assert_ws_gap_allows_submit(getattr(intent, "market_id", None) or getattr(intent, "token_id", None))

        # -------------------------------------------------------------------
        # P1.S5: pre-submit idempotency lookup (NC-19 fast-path gate).
        # Check BEFORE the INSERT to avoid a failed-INSERT roundtrip on retries.
        # The IntegrityError handler below is the race-condition safety belt.
        # -------------------------------------------------------------------
        from src.state.venue_command_repo import (
            find_command_by_idempotency_key,
            find_unknown_command_by_economic_intent,
        )
        from src.execution.command_bus import VenueCommand
        pre_lookup_row = find_command_by_idempotency_key(conn, idem.value)
        if pre_lookup_row is not None:
            corrected_existing_mismatch = _corrected_existing_command_mismatch_reason(
                conn,
                intent,
                pre_lookup_row,
            )
            if corrected_existing_mismatch is not None:
                logger.warning(
                    "_live_order: idempotency fast path blocked by corrected "
                    "identity mismatch for trade_id=%s idem=%s: %s",
                    trade_id,
                    idem.value,
                    corrected_existing_mismatch,
                )
                return _reject_corrected_existing_command_mismatch(
                    trade_id=trade_id,
                    intent=intent,
                    shares=shares,
                    idem_value=idem.value,
                    reason=corrected_existing_mismatch,
                )
            logger.info(
                "_live_order: pre-submit lookup found existing command for "
                "idem=%s trade_id=%s — skipping submit",
                idem.value, trade_id,
            )
            return _orderresult_from_existing(
                conn,
                VenueCommand.from_row(pre_lookup_row),
                trade_id=trade_id,
                limit_price=intent.limit_price,
                shares=shares,
                idem_value=idem.value,
                intent_id=None,
                order_role="entry",
            )
        economic_unknown_row = find_unknown_command_by_economic_intent(
            conn,
            intent_kind=IntentKind.ENTRY.value,
            token_id=intent.token_id,
            side="BUY",
            price=intent.limit_price,
            size=shares,
            exclude_idempotency_key=idem.value,
        )
        if economic_unknown_row is not None:
            corrected_existing_mismatch = _corrected_existing_command_mismatch_reason(
                conn,
                intent,
                economic_unknown_row,
            )
            if corrected_existing_mismatch is not None:
                logger.warning(
                    "_live_order: economic-unknown fast path blocked by corrected "
                    "identity mismatch for trade_id=%s idem=%s: %s",
                    trade_id,
                    idem.value,
                    corrected_existing_mismatch,
                )
                return _reject_corrected_existing_command_mismatch(
                    trade_id=trade_id,
                    intent=intent,
                    shares=shares,
                    idem_value=idem.value,
                    reason=corrected_existing_mismatch,
                )
            logger.warning(
                "_live_order: same economic intent is already unresolved as "
                "unknown_side_effect (idem=%s trade_id=%s)",
                idem.value, trade_id,
            )
            return _orderresult_from_economic_unknown(
                VenueCommand.from_row(economic_unknown_row),
                trade_id=trade_id,
                limit_price=intent.limit_price,
                shares=shares,
                idem_value=idem.value,
                intent_id=None,
                order_role="entry",
            )

        entries_pause_component = _entry_control_pause_component(conn)
        if not entries_pause_component.get("allowed"):
            reason = str(entries_pause_component.get("reason") or "entries_paused")
            logger.warning(
                "_live_order: entries pause blocked entry before command "
                "persistence for trade_id=%s token=%s reason=%s details=%s",
                trade_id,
                intent.token_id,
                reason,
                entries_pause_component,
            )
            return OrderResult(
                trade_id=trade_id,
                status="rejected",
                reason=f"entries_paused:{reason}",
                submitted_price=intent.limit_price,
                shares=shares,
                order_role="entry",
                intent_id=None,
                idempotency_key=idem.value,
                command_state="REJECTED",
            )

        duplicate_same_token_component = _entry_duplicate_same_token_component(
            conn,
            token_id=intent.token_id,
            candidate_position_id=trade_id,
        )
        if not duplicate_same_token_component.get("allowed"):
            reason = str(
                duplicate_same_token_component.get("reason")
                or "duplicate_entry_same_token"
            )
            logger.warning(
                "_live_order: duplicate same-token entry blocked before command "
                "persistence for trade_id=%s token=%s reason=%s details=%s",
                trade_id,
                intent.token_id,
                reason,
                duplicate_same_token_component,
            )
            return OrderResult(
                trade_id=trade_id,
                status="rejected",
                reason=f"duplicate_entry_same_token:{reason}",
                submitted_price=intent.limit_price,
                shares=shares,
                order_role="entry",
                intent_id=None,
                idempotency_key=idem.value,
                command_state="REJECTED",
            )

        cooldown_component = _entry_same_token_cooldown_component(
            conn,
            token_id=intent.token_id,
            candidate_position_id=trade_id,
            limit_price=intent.limit_price,
            shares=shares,
        )
        if not cooldown_component.get("allowed"):
            reason = str(
                cooldown_component.get("reason") or "same_token_entry_cooldown"
            )
            logger.warning(
                "_live_order: same-token entry cooldown blocked before command "
                "persistence for trade_id=%s token=%s reason=%s details=%s",
                trade_id,
                intent.token_id,
                reason,
                cooldown_component,
            )
            return OrderResult(
                trade_id=trade_id,
                status="rejected",
                reason=f"entry_cooldown:{reason}",
                submitted_price=intent.limit_price,
                shares=shares,
                order_role="entry",
                intent_id=None,
                idempotency_key=idem.value,
                command_state="REJECTED",
            )

        decision_source_component = _entry_decision_source_component(intent)
        if not decision_source_component.get("allowed"):
            reason = str(decision_source_component.get("reason") or "invalid_decision_source_context")
            details = decision_source_component.get("details") or {}
            errors = str(details.get("errors") or "").strip()
            if errors:
                reason = f"{reason}:{errors}"
            logger.warning(
                "_live_order: decision source integrity blocked entry submit for trade_id=%s: %s",
                trade_id,
                reason,
            )
            return OrderResult(
                trade_id=trade_id,
                status="rejected",
                reason=f"decision_source_integrity:{reason}",
                submitted_price=intent.limit_price,
                shares=shares,
                order_role="entry",
                intent_id=None,
                idempotency_key=idem.value,
            )

        try:
            collateral_refresh_component = _refresh_entry_collateral_snapshot_for_submit(conn)
        except CollateralInsufficient as exc:
            return OrderResult(
                trade_id=trade_id,
                status="rejected",
                reason=f"pre_submit_collateral_refresh_failed: {exc}",
                submitted_price=intent.limit_price,
                shares=shares,
                order_role="entry",
                idempotency_key=idem.value,
            )

        try:
            collateral_component = _assert_collateral_allows_buy(
                intent,
                spend_micro=required_pusd_micro,
                conn=conn,
            )
            pre_submit_envelope = _build_pre_submit_envelope(
                conn,
                command_id=command_id,
                snapshot_id=intent.executable_snapshot_id,
                token_id=intent.token_id,
                side="BUY",
                price=intent.limit_price,
                size=shares,
                order_type=effective_order_type,
                post_only=submit_post_only,
                captured_at=now_str,
            )
            envelope_id = _persist_prebuilt_submit_envelope(
                conn,
                pre_submit_envelope,
                command_id=command_id,
            )
            insert_command(
                conn,
                command_id=command_id,
                snapshot_id=intent.executable_snapshot_id,
                envelope_id=envelope_id,
                position_id=trade_id,
                decision_id=effective_decision_id,
                idempotency_key=idem.value,
                intent_kind=IntentKind.ENTRY.value,
                market_id=intent.market_id,
                token_id=intent.token_id,
                side="BUY",
                size=shares,
                price=intent.limit_price,
                created_at=now_str,
                snapshot_checked_at=now_str,
                expected_min_tick_size=intent.executable_snapshot_min_tick_size,
                expected_min_order_size=intent.executable_snapshot_min_order_size,
                expected_neg_risk=intent.executable_snapshot_neg_risk,
            )
            append_event(
                conn,
                command_id=command_id,
                event_type="SUBMIT_REQUESTED",
                occurred_at=now_str,
                payload={
                    "allocation": _allocation_payload_for_intent(intent),
                    "order_type": effective_order_type,
                    "post_only": submit_post_only,
                    "execution_capability": _build_execution_capability(
                        action="ENTRY",
                        command_id=command_id,
                        intent_kind=IntentKind.ENTRY.value,
                        order_type=effective_order_type,
                        token_id=intent.token_id,
                        snapshot_id=intent.executable_snapshot_id,
                        freshness_time=now_str,
                        components=[
                            cutover_component,
                            _component_from_result(
                                "risk_allocator",
                            risk_allocator_decision,
                            ),
                            _capability_component(
                                "order_type_selection",
                                order_type=effective_order_type,
                                selected_order_type=order_type,
                                intent_order_type=submit_order_type,
                                post_only=submit_post_only,
                            ),
                            taker_quality_component,
                            entry_economics_component,
                            actionable_certificate_component,
                            heartbeat_component,
                            ws_gap_component,
                            collateral_refresh_component,
                            collateral_component,
                            entries_pause_component,
                            cooldown_component,
                            duplicate_same_token_component,
                            decision_source_component,
                            corrected_identity_component,
                            _capability_component("executable_snapshot_gate"),
                        ],
                    ),
                },
            )
            _reserve_collateral_for_buy(
                command_id,
                intent,
                conn,
                spend_micro=required_pusd_micro,
            )
            conn.commit()
        except MarketSnapshotError as exc:
            return OrderResult(
                trade_id=trade_id,
                status="rejected",
                reason=f"executable_snapshot_gate: {exc}",
                submitted_price=intent.limit_price,
                shares=shares,
                order_role="entry",
            )
        except PreSubmitIdentityBindingError as exc:
            try:
                conn.rollback()
            except Exception:
                pass
            return OrderResult(
                trade_id=trade_id,
                status="rejected",
                reason=f"pre_submit_identity_binding_failed: {exc}",
                submitted_price=intent.limit_price,
                shares=shares,
                order_role="entry",
                idempotency_key=idem.value,
                command_state="REJECTED",
            )
        except CollateralInsufficient as exc:
            rej_time = datetime.now(timezone.utc).isoformat()
            if _venue_command_exists(conn, command_id):
                try:
                    append_event(
                        conn,
                        command_id=command_id,
                        event_type="SUBMIT_REJECTED",
                        occurred_at=rej_time,
                        payload={
                            "reason": "pre_submit_collateral_reservation_failed",
                            "detail": str(exc),
                            "exception_type": type(exc).__name__,
                            "side_effect_boundary_crossed": False,
                            "sdk_submit_attempted": False,
                        },
                    )
                    if _own_conn:
                        conn.commit()
                except Exception as inner:
                    logger.error(
                        "_live_order: SUBMIT_REJECTED append_event failed after "
                        "pre-submit collateral reservation failure (command_id=%s "
                        "trade_id=%s): inner=%s original=%s",
                        command_id,
                        trade_id,
                        inner,
                        exc,
                    )
            else:
                try:
                    conn.rollback()
                except Exception:
                    pass
                logger.warning(
                    "_live_order: pre-command collateral rejection "
                    "(command_id=%s trade_id=%s) — no venue command/event to append; "
                    "no order placed: %s",
                    command_id,
                    trade_id,
                    exc,
                )
            return OrderResult(
                trade_id=trade_id,
                status="rejected",
                reason=f"pre_submit_collateral_reservation_failed: {exc}",
                submitted_price=intent.limit_price,
                shares=shares,
                order_role="entry",
                idempotency_key=idem.value,
                command_state="REJECTED",
            )
        except sqlite3.IntegrityError as exc:
            # Race-condition safety belt: another process inserted between our
            # lookup and our INSERT. Existing command is the canonical record.
            logger.warning(
                "_live_order: idempotency key collision (race) for trade_id=%s idem=%s: %s",
                trade_id, idem.value, exc,
            )
            existing_row = find_command_by_idempotency_key(conn, idem.value)
            if existing_row is not None:
                corrected_existing_mismatch = _corrected_existing_command_mismatch_reason(
                    conn,
                    intent,
                    existing_row,
                )
                if corrected_existing_mismatch is not None:
                    logger.warning(
                        "_live_order: idempotency race fallback blocked by corrected "
                        "identity mismatch for trade_id=%s idem=%s: %s",
                        trade_id,
                        idem.value,
                        corrected_existing_mismatch,
                    )
                    return _reject_corrected_existing_command_mismatch(
                        trade_id=trade_id,
                        intent=intent,
                        shares=shares,
                        idem_value=idem.value,
                        reason=corrected_existing_mismatch,
                    )
                return _orderresult_from_existing(
                    conn,
                    VenueCommand.from_row(existing_row),
                    trade_id=trade_id,
                    limit_price=intent.limit_price,
                    shares=shares,
                    idem_value=idem.value,
                    intent_id=None,
                    order_role="entry",
                )
            # Defensive fallback: row not found despite collision
            return OrderResult(
                trade_id=trade_id,
                status="rejected",
                reason=f"idempotency_collision: {exc}",
                submitted_price=intent.limit_price,
                shares=shares,
                order_role="entry",
            )
        except sqlite3.OperationalError as exc:
            # C-DBLOCK-UNKNOWN (2026-06-16): a transient 'database is locked' in this
            # PRE-VENUE persist phase (insert_command + SUBMIT_REQUESTED + collateral
            # reserve + commit) fires BEFORE place_limit_order (line ~3838) — NO order
            # was placed (side_effect_boundary_crossed=False). With no OperationalError
            # handler it propagated out to the event-bound layer's catch-all, which
            # marked it POST_SUBMIT_UNKNOWN; that tripped the governor unknown_side_effect
            # kill-switch (limit=0, src/risk_allocator/governor.py:242) and HALTED ALL
            # submits until reconciled. Live: this is the DOMINANT current no-trade — 13x
            # EXECUTOR_SUBMIT_UNKNOWN:'database is locked' Jun 12-16, every one with NO
            # venue_order_id (proof: pre-venue). It is NOT a side effect: roll back the
            # uncommitted persist (nothing is committed until the conn.commit() above) and
            # return a CLEAN transient rejection so the candidate re-attempts next cycle
            # instead of halting the lane on a phantom unknown. Non-lock OperationalError
            # re-raises (unchanged). See docs/evidence/timing_audit/exec_submit_reject_breakdown_2026-06-16.md.
            if "database is locked" not in str(exc).lower():
                raise
            try:
                conn.rollback()
            except Exception:
                pass
            logger.warning(
                "_live_order: pre-venue persist 'database is locked' (command_id=%s "
                "trade_id=%s) — no order placed; transient reject, retry next cycle: %s",
                command_id, trade_id, exc,
            )
            return OrderResult(
                trade_id=trade_id,
                status="rejected",
                reason=f"pre_submit_db_locked_transient: {exc}",
                submitted_price=intent.limit_price,
                shares=shares,
                order_role="entry",
                idempotency_key=idem.value,
            )

        # -----------------------------------------------------------------------
        # Phase 4: V2 endpoint-identity preflight (INV-25 / K5)
        # Client is instantiated here so both preflight and place_limit_order
        # share the same instance. If preflight fails, append SUBMIT_REJECTED
        # (the row is already SUBMITTING and must reach a terminal state).
        # -----------------------------------------------------------------------
        try:
            client = PolymarketClient()
        except Exception as exc:
            # Constructor / credential / adapter setup failures happen before
            # any venue submit side effect. They are safe terminal rejections,
            # not M2 unknown-side-effect outcomes.
            rej_time = datetime.now(timezone.utc).isoformat()
            try:
                append_event(
                    conn,
                    command_id=command_id,
                    event_type="SUBMIT_REJECTED",
                    occurred_at=rej_time,
                    payload={
                        "reason": "pre_submit_client_init_failed",
                        "exception_type": type(exc).__name__,
                        "exception_message": str(exc),
                    },
                )
                if _own_conn:
                    conn.commit()
            except Exception as inner:
                logger.error(
                    "_live_order: SUBMIT_REJECTED append_event failed after client init "
                    "(command_id=%s trade_id=%s): inner=%s original=%s",
                    command_id, trade_id, inner, exc,
                )
            return OrderResult(
                trade_id=trade_id,
                status="rejected",
                reason=f"pre_submit_client_init_failed: {exc}",
                submitted_price=intent.limit_price,
                shares=shares,
                order_role="entry",
                idempotency_key=idem.value,
                command_state="REJECTED",
            )
        try:
            client.v2_preflight()
        except V2PreflightError as exc:
            logger.error(
                "LIVE ORDER rejected: v2_preflight_failed for trade_id=%s: %s",
                trade_id,
                exc,
            )
            rej_time = datetime.now(timezone.utc).isoformat()
            try:
                append_event(
                    conn,
                    command_id=command_id,
                    event_type="SUBMIT_REJECTED",
                    occurred_at=rej_time,
                    payload={"reason": "v2_preflight_failed", "detail": str(exc)},
                )
                if _own_conn:
                    conn.commit()
            except Exception as inner:
                logger.error(
                    "_live_order: SUBMIT_REJECTED append_event failed after v2_preflight "
                    "(command_id=%s): %s",
                    command_id, inner,
                )
            return OrderResult(
                trade_id=trade_id,
                status="rejected",
                reason=f"v2_preflight_failed: {exc}",
                submitted_price=intent.limit_price,
                shares=shares,
                order_role="entry",
            )
        except Exception as exc:
            logger.error(
                "LIVE ORDER rejected: v2_preflight_exception for trade_id=%s: %s",
                trade_id,
                exc,
            )
            rej_time = datetime.now(timezone.utc).isoformat()
            try:
                append_event(
                    conn,
                    command_id=command_id,
                    event_type="SUBMIT_REJECTED",
                    occurred_at=rej_time,
                    payload={
                        "reason": "v2_preflight_exception",
                        "exception_type": type(exc).__name__,
                        "exception_message": str(exc),
                    },
                )
                if _own_conn:
                    conn.commit()
            except Exception as inner:
                logger.error(
                    "_live_order: SUBMIT_REJECTED append_event failed after generic "
                    "v2_preflight exception (command_id=%s): %s",
                    command_id, inner,
                )
            return OrderResult(
                trade_id=trade_id,
                status="rejected",
                reason=f"v2_preflight_exception: {exc}",
                submitted_price=intent.limit_price,
                shares=shares,
                order_role="entry",
                idempotency_key=idem.value,
                command_state="REJECTED",
            )

        logger.info(
            "LIVE ORDER: %s token=%s...%s @ %.3f limit, %.2f shares, timeout=%ds",
            intent.direction.value,
            intent.token_id[:8], intent.token_id[-4:],
            intent.limit_price, shares, timeout,
        )
        if pre_submit_envelope is not None and hasattr(client, "bind_submission_envelope"):
            client.bind_submission_envelope(pre_submit_envelope)

        # -----------------------------------------------------------------------
        # Phase 5: submit — SDK call (INV-30: row already SUBMITTING)
        # -----------------------------------------------------------------------
        zeus_submit_intent_time = datetime.now(timezone.utc).isoformat()
        try:
            result = client.place_limit_order(
                token_id=intent.token_id,
                price=intent.limit_price,
                size=shares,
                side="BUY",  # Always BUY
                order_type=effective_order_type,
            )
        except Exception as exc:
            # M2: place_limit_order has crossed the submit side-effect boundary.
            # Treat SDK/network exceptions as unknown side effects. Narrow
            # synchronous CLOB validation failures are deterministic rejections:
            # no order id is created and retry requires changed inputs/egress.
            unk_time = datetime.now(timezone.utc).isoformat()
            deterministic_rejection_payload = _deterministic_submit_rejection_payload(
                exc,
                idempotency_key=idem.value,
            )
            try:
                terminal_event_type = (
                    "SUBMIT_REJECTED"
                    if deterministic_rejection_payload is not None
                    else "SUBMIT_TIMEOUT_UNKNOWN"
                )
                terminal_payload = (
                    deterministic_rejection_payload
                    if deterministic_rejection_payload is not None
                    else {
                        "reason": "post_submit_exception_possible_side_effect",
                        "exception_type": type(exc).__name__,
                        "exception_message": str(exc),
                        "idempotency_key": idem.value,
                    }
                )
                last_inner: Exception | None = None
                for attempt_idx, delay_s in enumerate((0.0, 0.05, 0.15, 0.35), start=1):
                    if delay_s:
                        time.sleep(delay_s)
                    try:
                        append_event(
                            conn,
                            command_id=command_id,
                            event_type=terminal_event_type,
                            occurred_at=unk_time,
                            payload={
                                **terminal_payload,
                                "terminal_write_attempt": attempt_idx,
                            },
                        )
                        # Commit UNCONDITIONALLY (same rule as the post-ACK path and the
                        # exit-order twin): the request crossed the venue boundary, so the
                        # venue may hold a live order. Under a caller-owned connection the
                        # old `if _own_conn` guard let a crash/rollback before the outer
                        # commit ERASE the unknown-side-effect fence — the next cycle then
                        # re-submits the same economic intent (duplicate live order).
                        # External review 2026-06-12 CRITICAL-2.
                        conn.commit()
                        last_inner = None
                        break
                    except sqlite3.OperationalError as inner:
                        if "locked" not in str(inner).lower() and "busy" not in str(inner).lower():
                            raise
                        last_inner = inner
                if last_inner is not None:
                    raise last_inner
            except Exception as inner:
                logger.error(
                    "_live_order: terminal SDK-exception event append/commit failed — "
                    "unknown-side-effect fence NOT durable; reconcile before next submit "
                    "(command_id=%s trade_id=%s): inner=%s original=%s",
                    command_id, trade_id, inner, exc,
                )
            logger.error("Live order SDK exception: %s", exc)
            if deterministic_rejection_payload is not None:
                return OrderResult(
                    trade_id=trade_id,
                    status="rejected",
                    reason=f"{deterministic_rejection_payload['reason']}: {exc}",
                    submitted_price=intent.limit_price,
                    shares=shares,
                    order_role="entry",
                    idempotency_key=idem.value,
                    command_state="REJECTED",
                    zeus_submit_intent_time=zeus_submit_intent_time,
                )
            return OrderResult(
                trade_id=trade_id,
                status="unknown_side_effect",
                reason=f"submit_unknown_side_effect: {exc}",
                submitted_price=intent.limit_price,
                shares=shares,
                order_role="entry",
                idempotency_key=idem.value,
                command_state="SUBMIT_UNKNOWN_SIDE_EFFECT",
                zeus_submit_intent_time=zeus_submit_intent_time,
            )

        # -----------------------------------------------------------------------
        # Phase 6: ack — durable journal record of outcome
        # -----------------------------------------------------------------------
        ack_time = datetime.now(timezone.utc).isoformat()
        if result is None:
            try:
                append_event(
                    conn,
                    command_id=command_id,
                    event_type="REVIEW_REQUIRED",
                    occurred_at=ack_time,
                    payload={
                        "reason": "final_submission_envelope_persistence_failed",
                        "detail": "place_limit_order returned None",
                        "idempotency_key": idem.value,
                    },
                )
                if _own_conn:
                    conn.commit()
            except Exception as inner:
                logger.error(
                    "_live_order: REVIEW_REQUIRED append_event failed after missing final "
                    "submission envelope (command_id=%s): %s",
                    command_id, inner,
                )
            return OrderResult(
                trade_id=trade_id,
                status="unknown_side_effect",
                reason="final_submission_envelope_persistence_failed: place_limit_order returned None",
                submitted_price=intent.limit_price,
                shares=shares,
                order_role="entry",
                idempotency_key=idem.value,
                command_state="REVIEW_REQUIRED",
                zeus_submit_intent_time=zeus_submit_intent_time,
                venue_ack_time=ack_time,
            )

        try:
            final_envelope_payload = _persist_final_submission_envelope_payload(
                conn,
                result,
                command_id=command_id,
            )
        except FinalSubmissionEnvelopePersistenceError as exc:
            try:
                append_event(
                    conn,
                    command_id=command_id,
                    event_type="REVIEW_REQUIRED",
                    occurred_at=ack_time,
                    payload=_submit_result_review_required_payload(
                        result,
                        reason="final_submission_envelope_persistence_failed",
                        detail=str(exc),
                        idempotency_key=idem.value,
                    ),
                )
                if _own_conn:
                    conn.commit()
            except Exception as inner:
                logger.error(
                    "_live_order: REVIEW_REQUIRED append_event failed after final "
                    "submission envelope persistence failure (command_id=%s): inner=%s original=%s",
                    command_id, inner, exc,
                )
            return OrderResult(
                trade_id=trade_id,
                status="unknown_side_effect",
                reason=f"final_submission_envelope_persistence_failed: {exc}",
                order_id=_submit_result_order_id(result),
                submitted_price=intent.limit_price,
                shares=shares,
                order_role="entry",
                venue_status=str(result.get("status") or "") if isinstance(result, dict) else "",
                idempotency_key=idem.value,
                command_state="REVIEW_REQUIRED",
                zeus_submit_intent_time=zeus_submit_intent_time,
                venue_ack_time=ack_time,
            )
        order_id = _submit_result_order_id(result)
        if result.get("success") is False:
            rejection_reason = (
                result.get("errorCode")
                or result.get("error_code")
                or result.get("reason")
                or "submit_rejected"
            )
            try:
                append_event(
                    conn,
                    command_id=command_id,
                    event_type="SUBMIT_REJECTED",
                    occurred_at=ack_time,
                    payload={
                        "reason": str(rejection_reason),
                        "detail": result.get("errorMessage") or result.get("error_message") or "",
                        **final_envelope_payload,
                    },
                )
                if _own_conn:
                    conn.commit()
            except Exception as inner:
                logger.error(
                    "_live_order: SUBMIT_REJECTED (success_false) append_event failed "
                    "(command_id=%s): %s",
                    command_id, inner,
                )
                durable_state = _mark_post_submit_persistence_failure(
                    conn,
                    command_id=command_id,
                    order_id=order_id,
                    occurred_at=ack_time,
                    reason="terminal_rejection_persistence_failed_after_side_effect",
                    detail=str(inner),
                    idempotency_key=idem.value,
                    order_role="entry",
                )
                return OrderResult(
                    trade_id=trade_id,
                    status="unknown_side_effect",
                    reason="terminal_rejection_persistence_failed_after_side_effect",
                    submitted_price=intent.limit_price,
                    shares=shares,
                    order_role="entry",
                    venue_status=str(result.get("status") or ""),
                    idempotency_key=idem.value,
                    command_id=command_id,
                    command_state=durable_state or "REVIEW_REQUIRED",
                    zeus_submit_intent_time=zeus_submit_intent_time,
                    venue_ack_time=ack_time,
                )
            return OrderResult(
                trade_id=trade_id,
                status="rejected",
                reason=str(rejection_reason),
                submitted_price=intent.limit_price,
                shares=shares,
                order_role="entry",
                venue_status=str(result.get("status") or ""),
                idempotency_key=idem.value,
                command_id=command_id,  # F7: propagate so log_execution_fact records FK
                zeus_submit_intent_time=zeus_submit_intent_time,
                venue_ack_time=ack_time,
            )
        if not order_id:
            try:
                append_event(
                    conn,
                    command_id=command_id,
                    event_type="SUBMIT_REJECTED",
                    occurred_at=ack_time,
                    payload={"reason": "missing_order_id", **final_envelope_payload},
                )
                if _own_conn:
                    conn.commit()
            except Exception as inner:
                logger.error(
                    "_live_order: SUBMIT_REJECTED (missing_order_id) append_event failed "
                    "(command_id=%s): %s",
                    command_id, inner,
                )
                durable_state = _mark_post_submit_persistence_failure(
                    conn,
                    command_id=command_id,
                    order_id=None,
                    occurred_at=ack_time,
                    reason="terminal_rejection_persistence_failed_after_side_effect",
                    detail=str(inner),
                    idempotency_key=idem.value,
                    order_role="entry",
                )
                return OrderResult(
                    trade_id=trade_id,
                    status="unknown_side_effect",
                    reason="terminal_rejection_persistence_failed_after_side_effect",
                    submitted_price=intent.limit_price,
                    shares=shares,
                    order_role="entry",
                    venue_status=str(result.get("status") or ""),
                    idempotency_key=idem.value,
                    command_id=command_id,
                    command_state=durable_state or "REVIEW_REQUIRED",
                    zeus_submit_intent_time=zeus_submit_intent_time,
                    venue_ack_time=ack_time,
                )
            return OrderResult(
                trade_id=trade_id,
                status="rejected",
                reason="missing_order_id",
                submitted_price=intent.limit_price,
                shares=shares,
                order_role="entry",
                venue_status=str(result.get("status") or ""),
                idempotency_key=idem.value,
                command_id=command_id,  # F7: propagate so log_execution_fact records FK
                zeus_submit_intent_time=zeus_submit_intent_time,
                venue_ack_time=ack_time,
            )
        order_fact_state = _venue_submit_order_fact_state(result)
        matched_size = _venue_submit_matched_size(result, side="BUY")
        remaining_size = _venue_submit_remaining_size(
            result,
            shares,
            matched_size=matched_size,
            side="BUY",
        )
        fill_event_type: str | None = None
        fill_price = _venue_submit_fill_price(result, side="BUY")
        fill_trade_id: str | None = None
        fill_tx_hash = next(iter(_venue_submit_transaction_hashes(result)), None)

        fill_evidence = result
        if order_fact_state in {"MATCHED", "PARTIALLY_MATCHED"}:
            trade_ids = _venue_submit_trade_ids(result)
            if (
                not trade_ids
                or not _positive_decimal_or_none(matched_size)
                or fill_price is None
            ):
                get_order = getattr(client, "get_order", None)
                if callable(get_order):
                    try:
                        point_order = get_order(order_id)
                    except Exception as exc:
                        logger.warning(
                            "_live_order: matched submit point-order lookup failed "
                            "(command_id=%s order_id=%s): %s",
                            command_id,
                            order_id,
                            exc,
                        )
                        point_order = None
                    if isinstance(point_order, dict):
                        fill_evidence = _merge_point_order_fill_truth(result, point_order)
                        trade_ids = _venue_submit_trade_ids(fill_evidence)
                        point_matched = _venue_submit_matched_size(
                            fill_evidence,
                            side="BUY",
                        )
                        if _positive_decimal_or_none(point_matched):
                            matched_size = point_matched
                            remaining_size = _venue_submit_remaining_size(
                                fill_evidence,
                                shares,
                                matched_size=matched_size,
                                side="BUY",
                            )
                        fill_price = _venue_submit_fill_price(
                            fill_evidence,
                            side="BUY",
                        )
                        fill_tx_hash = next(
                            iter(_venue_submit_transaction_hashes(fill_evidence)),
                            fill_tx_hash,
                        )
            fill_trade_id = next(iter(trade_ids), None)
            if fill_trade_id:
                fill_event_type = (
                    "FILL_CONFIRMED"
                    if _venue_fill_covers_submit(matched_size, shares)
                    else "PARTIAL_FILL_OBSERVED"
                )
            if fill_event_type and fill_price is None:
                fill_event_type = None

            if fill_event_type is None:
                if not _positive_decimal_or_none(matched_size):
                    review_reason = "matched_submit_missing_fill_size"
                    review_detail = "venue matched status lacked positive matched size in submit response and point-order proof"
                elif not fill_trade_id:
                    review_reason = "matched_submit_missing_trade_id"
                    review_detail = "venue matched status lacked trade id in submit response and point-order proof"
                else:
                    review_reason = "matched_submit_missing_fill_price"
                    review_detail = "venue matched status lacked fill price in submit response and point-order proof"
                try:
                    append_event(
                        conn,
                        command_id=command_id,
                        event_type="REVIEW_REQUIRED",
                        occurred_at=ack_time,
                        payload={
                            "reason": review_reason,
                            "detail": review_detail,
                            "venue_order_id": order_id,
                            "venue_status": str(result.get("status") or ""),
                            "idempotency_key": idem.value,
                            "side_effect_boundary_crossed": True,
                            "sdk_submit_returned_order_id": True,
                            "requires_recovery": True,
                            "submit_result": _jsonable_payload(result),
                            "fill_evidence": _jsonable_payload(fill_evidence),
                            **final_envelope_payload,
                        },
                    )
                    conn.commit()
                    durable_state = _current_command_state_value(conn, command_id)
                except Exception as inner:
                    logger.error(
                        "_live_order: REVIEW_REQUIRED append_event failed after "
                        "matched submit missing fill evidence (command_id=%s order_id=%s): %s",
                        command_id,
                        order_id,
                        inner,
                    )
                    durable_state = _mark_post_submit_persistence_failure(
                        conn,
                        command_id=command_id,
                        order_id=order_id,
                        occurred_at=ack_time,
                        reason="matched_submit_fill_evidence_review_persistence_failed",
                        detail=str(inner),
                        idempotency_key=idem.value,
                        order_role="entry_order",
                    )
                return OrderResult(
                    trade_id=trade_id,
                    status="unknown_side_effect",
                    reason=review_reason,
                    order_id=order_id,
                    submitted_price=intent.limit_price,
                    shares=shares,
                    order_role="entry",
                    external_order_id=order_id,
                    venue_status=str(result.get("status") or ""),
                    idempotency_key=idem.value,
                    command_state=durable_state or "REVIEW_REQUIRED",
                    command_id=command_id,
                    zeus_submit_intent_time=zeus_submit_intent_time,
                    venue_ack_time=ack_time,
                )

        # SUBMIT_ACKED
        # C-DBLOCK-UNKNOWN (2026-06-16): the venue side effect already happened, so this
        # records a KNOWN outcome. Extracted to a closure so a transient 'database is
        # locked' is retried (rollback + re-run) instead of degrading a good order to
        # unknown_side_effect (which trips the governor kill-switch). See
        # _retry_persist_on_db_lock.
        def _persist_entry_ack_facts() -> None:
            ack_already_persisted = _submit_ack_already_persisted(
                conn,
                command_id=command_id,
                order_id=order_id,
            )
            if not ack_already_persisted:
                append_event(
                    conn,
                    command_id=command_id,
                    event_type="SUBMIT_ACKED",
                    occurred_at=ack_time,
                    payload={
                        "venue_order_id": order_id,
                        "venue_status": str(result.get("status") or ""),
                        "order_type": order_type,
                        **final_envelope_payload,
                    },
                )
            if not _order_fact_already_persisted(
                conn,
                command_id=command_id,
                order_id=order_id,
            ):
                append_order_fact(
                    conn,
                    venue_order_id=order_id,
                    command_id=command_id,
                    state=order_fact_state,
                    remaining_size=remaining_size,
                    matched_size=matched_size,
                    source="REST",
                    observed_at=ack_time,
                    # C4 telemetry-truth: REST ACK response carries no server matchTime;
                    # venue_timestamp=None (honest absence). ack_time is Zeus receipt
                    # wall-clock only, labelled via observed_at.
                    venue_timestamp=None,
                    raw_payload_hash=_canonical_payload_hash(
                        {
                            "command_id": command_id,
                            "venue_order_id": order_id,
                            "submit_result": result,
                        }
                    ),
                    raw_payload_json={
                        "venue_order_id": order_id,
                        "submit_result": _jsonable_payload(result),
                        "source": "place_limit_order_ack",
                    },
                )
            if fill_event_type and fill_trade_id:
                if not _trade_fact_already_persisted(
                    conn,
                    command_id=command_id,
                    trade_id=fill_trade_id,
                ):
                    append_trade_fact(
                        conn,
                        trade_id=fill_trade_id,
                        venue_order_id=order_id,
                        command_id=command_id,
                        state="MATCHED",
                        filled_size=matched_size,
                        fill_price=fill_price,
                        source="REST",
                        observed_at=ack_time,
                        # C4 telemetry-truth: REST ACK carry no server matchTime;
                        # venue_timestamp=None (honest absence). Real match time
                        # arrives via the WS user-channel (matchtime field).
                        venue_timestamp=None,
                        tx_hash=fill_tx_hash,
                        raw_payload_hash=_canonical_payload_hash(
                            {
                                "command_id": command_id,
                                "venue_order_id": order_id,
                                "trade_id": fill_trade_id,
                                "fill_evidence": fill_evidence,
                            }
                        ),
                        raw_payload_json={
                            "venue_order_id": order_id,
                            "trade_id": fill_trade_id,
                            "submit_result": _jsonable_payload(result),
                            "fill_evidence": _jsonable_payload(fill_evidence),
                            "source": "place_limit_order_matched_submit",
                        },
                    )
                if not _command_event_already_persisted(
                    conn,
                    command_id=command_id,
                    event_type=fill_event_type,
                    order_id=order_id,
                    trade_id=fill_trade_id,
                ):
                    append_event(
                        conn,
                        command_id=command_id,
                        event_type=fill_event_type,
                        occurred_at=ack_time,
                        payload={
                            "reason": "place_limit_order_matched_submit",
                            "venue_order_id": order_id,
                            "trade_id": fill_trade_id,
                            "filled_size": matched_size,
                            "fill_price": fill_price,
                            "tx_hash": fill_tx_hash,
                            **final_envelope_payload,
                        },
                    )
            if not fill_event_type:
                from src.execution.command_recovery import ensure_live_entry_projection_for_command

                try:
                    ensure_live_entry_projection_for_command(
                        conn,
                        command_id=command_id,
                        client=client,
                    )
                except Exception as projection_exc:
                    logger.error(
                        "_live_order: immediate live entry projection skipped "
                        "(command_id=%s order_id=%s): %s",
                        command_id,
                        order_id,
                        projection_exc,
                    )
            # P1-1: durable commit independent of _own_conn — codereview-may19-2
            # ACK/order/trade facts must persist immediately regardless of whether
            # the caller provided an external connection. A crash after SDK ACK
            # but before the outer cycle commit would lose the venue order record.
            conn.commit()

        try:
            _retry_persist_on_db_lock(
                conn, _persist_entry_ack_facts, what="entry_ack_persistence"
            )
        except Exception as inner:
            logger.error(
                "_live_order: SUBMIT_ACKED append_event failed (command_id=%s order_id=%s): %s",
                command_id, order_id, inner,
            )
            durable_state = _mark_post_submit_persistence_failure(
                conn,
                command_id=command_id,
                order_id=order_id,
                occurred_at=ack_time,
                reason="entry_ack_persistence_failed_after_side_effect",
                detail=str(inner),
                idempotency_key=idem.value,
                order_role="entry_order",
            )
            return OrderResult(
                trade_id=trade_id,
                status="unknown_side_effect",
                reason=f"entry_ack_persistence_failed_after_side_effect: {inner}",
                order_id=order_id,
                submitted_price=intent.limit_price,
                shares=shares,
                order_role="entry",
                external_order_id=order_id,
                venue_status=str(result.get("status") or "placed"),
                idempotency_key=idem.value,
                command_state=durable_state,
                command_id=command_id,
                zeus_submit_intent_time=zeus_submit_intent_time,
                venue_ack_time=ack_time,
            )

        result_obj = OrderResult(
            trade_id=trade_id,
            status="filled" if fill_event_type == "FILL_CONFIRMED" else "pending",
            fill_price=float(fill_price) if fill_event_type == "FILL_CONFIRMED" else None,
            filled_at=ack_time if fill_event_type == "FILL_CONFIRMED" else None,
            reason=(
                "Order filled on submit"
                if fill_event_type == "FILL_CONFIRMED"
                else f"Order posted, timeout={timeout}s"
            ),
            order_id=order_id,
            timeout_seconds=timeout,
            submitted_price=intent.limit_price,
            shares=shares,
            order_role="entry",
            external_order_id=order_id,
            venue_status=str(result.get("status") or "placed"),
            idempotency_key=idem.value,
            command_state=(
                "FILLED"
                if fill_event_type == "FILL_CONFIRMED"
                else ("PARTIAL" if fill_event_type == "PARTIAL_FILL_OBSERVED" else "ACKED")
            ),
            command_id=command_id,  # F7: FK to venue_commands row
            zeus_submit_intent_time=zeus_submit_intent_time,
            venue_ack_time=ack_time,
        )
        try:
            alert_trade(
                direction="BUY",
                market=intent.market_id,
                price=intent.limit_price,
                size_usd=float(shares * intent.limit_price),
                strategy="live_order",
                edge=float(intent.decision_edge),
                mode=get_mode(),
            )
        except Exception as exc:
            logger.warning("Discord trade alert failed for live order: %s", exc)
        return result_obj
    finally:
        if _own_conn:
            conn.close()
