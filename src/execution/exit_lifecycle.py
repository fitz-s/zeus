"""Exit lifecycle: state machine for live sell orders.

GOLDEN RULE: confirmed sell fill creates economic close, not settlement.
Settlement remains a later harvester-owned transition.

State machine:
  "" → exit_intent → sell_placed → sell_pending → sell_filled (economically_closed)
                    ↘ retry_pending → (back to "" after cooldown for re-evaluation)
                    → backoff_exhausted (hold to settlement, stop retrying)
  exit_intent with no order = stranded by exception → recovered via check_pending_exits

This module owns all exit state transitions. CycleRunner calls it;
CycleRunner does not contain exit business logic.
"""

import hashlib
import logging
import json
import math
import os
import re
import sqlite3
import threading
import time as _time_module
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from inspect import Parameter, signature
from types import SimpleNamespace
from typing import Callable, Optional

from src.execution.collateral import check_sell_collateral
from src.state.collateral_ledger import CollateralInsufficient
from src.observability.counters import increment as _cnt_inc
from src.execution.executor import (
    OrderResult,
    create_exit_order_intent,
    execute_exit_order,
    _refresh_exit_collateral_snapshot_for_submit,
)
from src.state.lifecycle_manager import (
    LifecyclePhase,
    enter_pending_exit_runtime_state,
    release_pending_exit_runtime_state,
)
from src.state.portfolio import (
    compute_economic_close,
    compute_settlement_close,
    ExitContext,
    mark_admin_closed,
    Position,
    PortfolioState,
)

logger = logging.getLogger(__name__)

_PENDING_EXIT_SCAN_INACTIVE_STATES = frozenset(
    {
        "settled",
        "voided",
        "admin_closed",
        "quarantined",
        "economically_closed",
    }
)


def _runtime_state_value(position: Position) -> str:
    raw_state = getattr(position, "state", "")
    return str(getattr(raw_state, "value", raw_state) or "").strip().lower()


def _venue_order_payload(value: object | None) -> dict | None:
    """Normalize CLOB order read models to the dict shape this module stores."""

    if value is None:
        return None
    if isinstance(value, Mapping):
        payload = dict(value)
    else:
        raw = getattr(value, "raw", None)
        payload = dict(raw) if isinstance(raw, Mapping) else dict(getattr(value, "__dict__", {}) or {})
    status = getattr(value, "status", None)
    if status not in (None, "") and not (payload.get("status") or payload.get("state")):
        payload["status"] = str(status)
    order_id = getattr(value, "order_id", None)
    if order_id not in (None, "") and not (
        payload.get("orderID") or payload.get("orderId") or payload.get("order_id") or payload.get("id")
    ):
        payload["orderID"] = str(order_id)
    return payload


def _emit_typed_realized_fill(
    *,
    actual_price: float,
    expected_price: float,
    side: str,
    shares: float,
    trade_id: str,
) -> None:
    """Slice P5-1 (PR #19 closeout completion, 2026-04-26): construct
    typed RealizedFill at the fill-receipt seam.

    P3.3 commit message promised "thread RealizedFill at fill receipt"
    but only delivered planning-side typing. P5-1 closes the receipt
    half: build RealizedFill from the actual vs intended price pair so
    SlippageBps + ExecutionPrice contracts validate on every exit fill.
    Construction itself is the value — invalid prices raise at
    __post_init__ before downstream attribution can consume bad data.

    Wrapped defensively so a malformed-price edge case (zero/NaN intent
    price, side mismatch) never crashes the exit flow; the typed
    construction failure surfaces as a WARNING for ops review.
    """
    try:
        from src.contracts.execution_price import ExecutionPrice
        from src.contracts.realized_fill import RealizedFill
        if expected_price <= 0 or actual_price < 0 or shares <= 0 or not trade_id:
            return  # Insufficient context for typed RealizedFill — skip silently
        actual = ExecutionPrice(
            value=float(actual_price),
            price_type="vwmp",
            fee_deducted=False,
            currency="probability_units",
        )
        expected = ExecutionPrice(
            value=float(expected_price),
            price_type="vwmp",
            fee_deducted=False,
            currency="probability_units",
        )
        realized = RealizedFill.from_prices(
            execution_price=actual,
            expected_price=expected,
            side=side,
            shares=float(shares),
            trade_id=trade_id,
        )
        logger.debug(
            "realized_fill: trade=%s side=%s shares=%.4f actual=%.4f "
            "expected=%.4f slippage=%.2f bps direction=%s",
            trade_id, side, realized.shares,
            realized.execution_price.value,
            realized.expected_price.value,
            realized.slippage.value_bps,
            realized.slippage.direction,
        )
    except Exception as exc:
        logger.warning(
            "RealizedFill construction failed at fill-receipt for trade=%s: %s",
            trade_id, exc,
        )

MAX_EXIT_RETRIES = 10
DEFAULT_COOLDOWN_SECONDS = 300  # 5 minutes between retries
DEFAULT_PENDING_EXIT_STATUS_MAX_POSITIONS = 6
DEFAULT_PENDING_EXIT_STATUS_BUDGET_SECONDS = 10.0
# Transient submit-channel gap: retry ~each monitor cycle and NEVER give up, so a
# correct reversal exit sells once the channel recovers instead of being abandoned.
CHANNEL_NOT_READY_COOLDOWN_SECONDS = 120
EXIT_LOCKED_COOLDOWN_SECONDS = 60
RUNTIME_SUBMIT_GATE_BLOCK_COOLDOWN_SECONDS = 15 * 60
_ACTIVE_EXIT_SELL_STATES = frozenset(
    {
        "INTENT_CREATED",
        "SNAPSHOT_BOUND",
        "SIGNED_PERSISTED",
        "POSTING",
        "POST_ACKED",
        "SUBMITTING",
        "ACKED",
        "UNKNOWN",
        "SUBMIT_UNKNOWN_SIDE_EFFECT",
        "PARTIAL",
        "CANCEL_PENDING",
        "REVIEW_REQUIRED",
    }
)
_VENUE_OPEN_ORDER_TERMINAL_STATUSES = frozenset(
    {
        "CANCELED",
        "CANCELLED",
        "CANCEL_CONFIRMED",
        "EXPIRED",
        "FILLED",
        "MATCHED",
        "MINED",
        "NOT_FOUND",
        "REJECTED",
    }
)
_PENDING_EXIT_SCAN_CURSOR = 0


def _pending_exit_status_max_positions() -> int:
    raw = os.environ.get(
        "ZEUS_PENDING_EXIT_STATUS_MAX_POSITIONS",
        str(DEFAULT_PENDING_EXIT_STATUS_MAX_POSITIONS),
    )
    try:
        return max(1, int(raw))
    except (TypeError, ValueError):
        return DEFAULT_PENDING_EXIT_STATUS_MAX_POSITIONS


def _pending_exit_status_budget_seconds() -> float:
    raw = os.environ.get(
        "ZEUS_PENDING_EXIT_STATUS_BUDGET_SECONDS",
        str(DEFAULT_PENDING_EXIT_STATUS_BUDGET_SECONDS),
    )
    try:
        return max(0.25, float(raw))
    except (TypeError, ValueError):
        return DEFAULT_PENDING_EXIT_STATUS_BUDGET_SECONDS


def _pending_exit_scan_candidate(position: Position) -> bool:
    raw_exit_state = getattr(position, "exit_state", "")
    exit_state = str(getattr(raw_exit_state, "value", raw_exit_state) or "")
    if exit_state == "retry_pending":
        return True
    if exit_state in ("sell_placed", "sell_pending", "exit_intent"):
        return True
    return str(getattr(position, "order_status", "") or "") == "sell_pending_confirmation"


def _rotated_pending_exit_scan_positions(
    portfolio: PortfolioState,
    *,
    stats: dict,
) -> list[Position]:
    positions: list[Position] = []
    for pos in list(portfolio.positions):
        if _runtime_state_value(pos) in _PENDING_EXIT_SCAN_INACTIVE_STATES:
            stats["skipped_inactive"] = stats.get("skipped_inactive", 0) + 1
            continue
        if _pending_exit_scan_candidate(pos):
            positions.append(pos)
    if len(positions) <= 1:
        return positions
    offset = int(_PENDING_EXIT_SCAN_CURSOR) % len(positions)
    return positions[offset:] + positions[:offset]


def _is_channel_not_ready_error(error: str) -> bool:
    """True for TRANSIENT submit-channel-not-ready conditions where the position
    is still sellable once the channel recovers — a user-channel WS disconnect
    (``ws_gap...m5_reconcile_required``) or a transient CLOB read. These must NOT
    consume the bounded exit-retry budget that terminates in
    ``backoff_exhausted`` → admin-close: a correct reversal exit has to keep
    retrying until a bid can be hit, not be abandoned over a brief gap (operator:
    react to reversal, sell before the market notices).

    EXCLUDES genuinely terminal / unsellable conditions — ``market_end`` (the
    market closed; it settles), no ``bid-side`` liquidity, and sub-min
    ``min_order_size`` dust — which keep the existing fail-closed budget path so
    they are not retried forever. (2026-06-23 exit-execution diagnosis.)
    """
    if not error:
        return False
    e = error.lower()
    if "market_end" in e or "min_order_size" in e or "bid-side" in e:
        return False
    return (
        ("ws_gap=" in e and "m5_reconcile_required=true" in e)
        or "clob_market_info" in e
        or "exit_executable_snapshot_unavailable" in e
        or "venue_read_transient" in e
        or "transientvenueread" in e
    )


def _is_exit_transient_lock_error(error: str) -> bool:
    """True when a sell is blocked by transient token reservation state.

    These errors mean the exit cannot be submitted *right now*, usually because
    an existing sell already locked the CTF shares or the wallet/read projection
    has not caught up. They must not consume the bounded economic-exit retry
    budget; the position is still supposed to be exited once the lock resolves.
    """

    if not error:
        return False
    e = error.lower()
    if "pusd" in e:
        return False
    return (
        "sum of active orders" in e
        or ("active orders" in e and "not enough balance" in e)
        or "ctf_tokens_insufficient" in e
    )


def _is_runtime_submit_gate_block_error(error: str) -> bool:
    """True for deterministic runtime/code-plane blocks before venue submit."""

    if not error:
        return False
    e = error.lower()
    return (
        "[gate_runtime] blocked" in e
        and ("live_venue_submit" in e or "reduce_only_exit_submit" in e)
        and (
            "deployment_freshness_mismatch" in e
            or "reduce_only_exit_deployment_freshness_mismatch" in e
            or "loaded_sha_mismatch" in e
            or "process_loaded_code_stale" in e
        )
    )


def _runtime_submit_gate_currently_allows_submit() -> bool:
    """Return whether the runtime gate would currently allow a live venue submit."""

    try:
        from src.architecture import gate_runtime

        gate_runtime.check("reduce_only_exit_submit")
        return True
    except Exception:  # noqa: BLE001 - monitor must fail closed on gate uncertainty.
        return False


def _row_value(row: object, key: str, index: int) -> object:
    try:
        return row[key]  # type: ignore[index]
    except Exception:
        try:
            return row[index]  # type: ignore[index]
        except Exception:
            return None


def _payload_first(payload: Mapping[str, object], *keys: str) -> object:
    for key in keys:
        value = payload.get(key)
        if value not in (None, ""):
            return value
    return None


def _venue_open_order_remaining_size(payload: Mapping[str, object]) -> Decimal | None:
    payload_dict = dict(payload)
    remaining = _payload_decimal(
        payload_dict,
        "remaining_size",
        "remainingSize",
        "remaining",
        "open_size",
        "openSize",
    )
    if remaining is not None:
        return remaining
    original = _payload_decimal(payload_dict, "original_size", "originalSize", "size")
    if original is None:
        return None
    matched = _payload_decimal(
        payload_dict,
        "size_matched",
        "sizeMatched",
        "matched_size",
        "matchedSize",
        "filled_size",
        "filledSize",
    ) or Decimal("0")
    return original - matched


def _venue_open_exit_sell_order(
    clob,
    *,
    token_id: str,
    expected_shares: float,
) -> dict[str, object] | None:
    if clob is None or not token_id:
        return None
    get_open_orders = getattr(clob, "get_open_orders", None)
    if not callable(get_open_orders):
        return None
    try:
        orders = get_open_orders()
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "active exit open-order scan failed for token=%s: %s",
            token_id,
            exc,
        )
        return None
    expected = Decimal(str(max(0.0, float(expected_shares or 0.0))))
    if expected <= 0:
        return None
    tolerance = max(Decimal("0.000001"), expected * Decimal("0.02"))
    for order in orders or []:
        payload = _venue_order_payload(order)
        if not payload:
            continue
        order_id = str(
            _payload_first(payload, "orderID", "orderId", "order_id", "id") or ""
        ).strip()
        asset_id = str(
            _payload_first(payload, "asset_id", "assetId", "token_id", "tokenId") or ""
        ).strip()
        side = str(_payload_first(payload, "side", "order_side") or "").strip().upper()
        status = str(_payload_first(payload, "status", "state") or "LIVE").strip().upper()
        if not order_id or asset_id != token_id or side != "SELL":
            continue
        if status in _VENUE_OPEN_ORDER_TERMINAL_STATUSES:
            continue
        remaining = _venue_open_order_remaining_size(payload)
        if remaining is None or remaining <= 0 or remaining > expected + tolerance:
            continue
        return {
            "command_id": "venue_open_order",
            "state": status or "LIVE",
            "venue_order_id": order_id,
            "updated_at": _payload_first(payload, "updated_at", "updatedAt") or "",
            "created_at": _payload_first(payload, "created_at", "createdAt") or "",
            "price": _payload_first(payload, "price", "limit_price") or "",
            "size": str(remaining),
        }
    return None


def _active_exit_sell_command(
    conn: sqlite3.Connection | None,
    *,
    position_id: str,
    token_id: str,
) -> object | None:
    if conn is None or not position_id or not token_id:
        return None
    states = tuple(sorted(_ACTIVE_EXIT_SELL_STATES))
    placeholders = ", ".join("?" for _ in states)
    try:
        return conn.execute(
            f"""
            SELECT command_id, state, venue_order_id, updated_at, created_at
              FROM venue_commands
             WHERE position_id = ?
               AND token_id = ?
               AND side = 'SELL'
               AND intent_kind = 'EXIT'
               AND UPPER(COALESCE(state, '')) IN ({placeholders})
             ORDER BY datetime(updated_at) DESC, datetime(created_at) DESC, command_id DESC
             LIMIT 1
            """,
            (position_id, token_id, *states),
        ).fetchone()
    except sqlite3.Error:
        return None


def _active_exit_sell_for_lock(
    conn: sqlite3.Connection | None,
    position: Position,
    *,
    token_id: str,
    clob,
) -> object | None:
    active_exit = _active_exit_sell_command(
        conn,
        position_id=str(getattr(position, "trade_id", "") or ""),
        token_id=token_id,
    )
    if active_exit is not None:
        return active_exit
    _commit_before_exit_venue_io(conn, stage="active_exit_open_order_scan")
    return _venue_open_exit_sell_order(
        clob,
        token_id=token_id,
        expected_shares=float(getattr(position, "effective_shares", 0.0) or 0.0),
    )


def _active_exit_already_projected(
    conn: sqlite3.Connection | None,
    *,
    position_id: str,
    venue_order_id: str,
) -> bool:
    if conn is None or not position_id or not venue_order_id:
        return False
    try:
        row = conn.execute(
            """
            SELECT order_id, order_status
              FROM position_current
             WHERE position_id = ?
             LIMIT 1
            """,
            (position_id,),
        ).fetchone()
    except sqlite3.Error:
        return False
    if row is None:
        return False
    order_id = str(_row_value(row, "order_id", 0) or "")
    order_status = str(_row_value(row, "order_status", 1) or "").lower()
    if order_id == venue_order_id and order_status.startswith("sell_"):
        return True
    try:
        event_row = conn.execute(
            """
            SELECT 1
              FROM position_events
             WHERE position_id = ?
               AND event_type = 'EXIT_ORDER_POSTED'
               AND order_id = ?
             LIMIT 1
            """,
            (position_id, venue_order_id),
        ).fetchone()
    except sqlite3.Error:
        return False
    return event_row is not None


def _venue_command_columns(conn: sqlite3.Connection) -> set[str]:
    try:
        return {str(row[1]) for row in conn.execute("PRAGMA table_info(venue_commands)").fetchall()}
    except sqlite3.Error:
        return set()


def _local_state_for_adopted_exit_sell(venue_state: str) -> str:
    normalized = venue_state.strip().upper()
    if normalized in {"LIVE", "OPEN", "RESTING"}:
        return "ACKED"
    return normalized or "ACKED"


def _ensure_adopted_exit_command(
    conn: sqlite3.Connection | None,
    position: Position,
    row: object,
    *,
    token_id: str,
) -> str:
    if conn is None:
        return str(_row_value(row, "command_id", 0) or "")
    venue_order_id = str(_row_value(row, "venue_order_id", 2) or "")
    position_id = str(getattr(position, "trade_id", "") or "")
    if not venue_order_id or not position_id:
        return str(_row_value(row, "command_id", 0) or "")
    try:
        existing = conn.execute(
            """
            SELECT command_id
              FROM venue_commands
             WHERE position_id = ?
               AND intent_kind = 'EXIT'
               AND venue_order_id = ?
             ORDER BY updated_at DESC, created_at DESC, command_id DESC
             LIMIT 1
            """,
            (position_id, venue_order_id),
        ).fetchone()
    except sqlite3.Error:
        return str(_row_value(row, "command_id", 0) or "")
    if existing is not None:
        return str(_row_value(existing, "command_id", 0) or "")

    columns = _venue_command_columns(conn)
    if not columns:
        return str(_row_value(row, "command_id", 0) or "")
    digest = hashlib.sha256(f"{position_id}:{venue_order_id}".encode()).hexdigest()[:16]
    command_id = f"adopted_exit_{digest}"
    now = _utcnow().isoformat()
    venue_state = str(_row_value(row, "state", 1) or "")
    values: dict[str, object] = {
        "command_id": command_id,
        "snapshot_id": f"adopted_exit:{venue_order_id}",
        "envelope_id": f"adopted_exit:{venue_order_id}",
        "position_id": position_id,
        "decision_id": f"adopted_exit:{position_id}:{venue_order_id}",
        "idempotency_key": f"adopted_exit:{position_id}:{venue_order_id}",
        "intent_kind": "EXIT",
        "market_id": str(getattr(position, "market_id", "") or ""),
        "token_id": token_id,
        "side": "SELL",
        "size": float(_row_value(row, "size", 6) or getattr(position, "effective_shares", 0.0) or 0.0),
        "price": float(_row_value(row, "price", 5) or 0.0),
        "venue_order_id": venue_order_id,
        "state": _local_state_for_adopted_exit_sell(venue_state),
        "last_event_id": None,
        "created_at": str(_row_value(row, "created_at", 8) or now),
        "updated_at": str(_row_value(row, "updated_at", 7) or now),
        "review_required_reason": f"adopted_from_clob_open_orders;venue_state={venue_state or 'UNKNOWN'}",
    }
    insert_columns = [column for column in values if column in columns]
    if "command_id" not in insert_columns:
        return str(_row_value(row, "command_id", 0) or "")
    placeholders = ", ".join("?" for _ in insert_columns)
    try:
        conn.execute(
            f"""
            INSERT OR IGNORE INTO venue_commands ({", ".join(insert_columns)})
            VALUES ({placeholders})
            """,
            tuple(values[column] for column in insert_columns),
        )
    except sqlite3.Error:
        return str(_row_value(row, "command_id", 0) or "")
    return command_id


def _adopt_active_exit_sell(
    position: Position,
    row: object,
    *,
    conn: sqlite3.Connection | None,
    reason: str,
) -> str:
    token_id = _asset_id_for_position(position)
    command_id = _ensure_adopted_exit_command(conn, position, row, token_id=token_id)
    command_state = str(_row_value(row, "state", 1) or "")
    venue_order_id = str(_row_value(row, "venue_order_id", 2) or "")
    _mark_pending_exit(position)
    if command_id:
        position.last_exit_command_id = command_id
    if venue_order_id:
        position.last_exit_order_id = venue_order_id
    position.exit_state = "sell_pending"
    position.order_status = "sell_pending"
    position.next_exit_retry_at = None
    position.last_exit_error = reason[:500]
    if not str(getattr(position, "exit_reason", "") or ""):
        position.exit_reason = reason
    if not _active_exit_already_projected(
        conn,
        position_id=str(getattr(position, "trade_id", "") or ""),
        venue_order_id=venue_order_id,
    ):
        _dual_write_canonical_pending_exit_if_available(
            conn,
            position,
            reason=position.exit_reason or reason,
            error=reason,
            event_type="EXIT_ORDER_POSTED",
        )
    return (
        "sell_pending: active_prior_exit_sell "
        f"command_id={command_id} order={venue_order_id or 'pending_ack'} state={command_state}"
    )
PENDING_EXIT_REPRICE_MIN_TICKS = 2

EXIT_EVENT_VOCABULARY = (
    "EXIT_INTENT",
    "EXIT_ORDER_POSTED",
    "EXIT_ORDER_FILLED",
    "EXIT_ORDER_VOIDED",
    "EXIT_ORDER_REJECTED",
)


@dataclass(frozen=True)
class ExitIntent:
    """Scaffolding contract for explicit exit intent at the engine/execution boundary."""

    trade_id: str
    reason: str
    token_id: str
    shares: float
    current_market_price: float
    best_bid: float | None
    fresh_prob: float | None = None
    fresh_prob_is_fresh: bool | None = None
    best_ask: float | None = None
    market_vig: float | None = None
    hours_to_settlement: float | None = None
    position_state: str = ""
    day0_active: bool | None = None


def place_sell_order(
    *,
    trade_id: str,
    token_id: str,
    shares: float,
    current_price: float,
    best_bid: float | None = None,
    executable_snapshot_id: str = "",
    executable_snapshot_hash: str = "",
    executable_snapshot_min_tick_size: str | None = None,
    executable_snapshot_min_order_size: str | None = None,
    executable_snapshot_neg_risk: bool | None = None,
    executable_snapshot_orderbook_top_bid: object | None = None,
    executable_snapshot_orderbook_top_ask: object | None = None,
    decision_id: str = "",
) -> OrderResult:
    """Thin compatibility adapter over the executor-level exit-order path."""

    intent = create_exit_order_intent(
        trade_id=trade_id,
        token_id=token_id,
        shares=shares,
        current_price=current_price,
        best_bid=best_bid,
        executable_snapshot_id=executable_snapshot_id,
        executable_snapshot_hash=executable_snapshot_hash,
        executable_snapshot_min_tick_size=executable_snapshot_min_tick_size,
        executable_snapshot_min_order_size=executable_snapshot_min_order_size,
        executable_snapshot_neg_risk=executable_snapshot_neg_risk,
    )
    if decision_id:
        try:
            params = signature(execute_exit_order).parameters
            accepts_decision_id = (
                "decision_id" in params
                or any(param.kind == Parameter.VAR_KEYWORD for param in params.values())
            )
        except (TypeError, ValueError):
            accepts_decision_id = True
        if accepts_decision_id:
            return execute_exit_order(intent, decision_id=decision_id)
    return execute_exit_order(intent)


# Statuses that indicate final fill authority. MATCHED/MINED/FILLED are
# venue/order observations; only CONFIRMED is success terminality.
FILL_STATUSES = frozenset({"CONFIRMED"})
PARTIAL_FILL_STATUSES = frozenset({"PARTIAL", "PARTIALLY_FILLED", "PARTIALLY_MATCHED"})
VOID_STATUSES = frozenset({"CANCELLED", "CANCELED", "EXPIRED", "REJECTED"})
EXIT_TRADE_FACT_CLOSE_STATES = frozenset({"CONFIRMED"})
EXIT_TRADE_FACT_CLOSE_COMMAND_STATES = frozenset({"ACKED", "POST_ACKED", "PARTIAL", "FILLED"})
EXIT_FULL_CLOSE_DUST_TOLERANCE = Decimal("0.011")
EXIT_LIFECYCLE_OWNED_STATES = frozenset({"exit_intent", "sell_placed", "sell_pending", "retry_pending"})
EXIT_LIFECYCLE_RECOVERY_STATES = frozenset({"exit_intent", "retry_pending", "backoff_exhausted"})
# FIX 2a (2026-06-20): an exit order that is already on the book. The still-held
# chain-truth branch must NOT route such a position into a fresh evaluate→execute
# pass — that would risk a second place_sell_order (single-flight law). It keeps
# the existing in-flight handling instead.
_EXIT_LIFECYCLE_IN_FLIGHT_STATES = frozenset({"exit_intent", "sell_placed", "sell_pending"})


def _parse_iso(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _commit_before_exit_venue_io(conn: sqlite3.Connection | None, *, stage: str) -> None:
    """Release trade DB writes before live cancel/sell HTTP calls."""

    if conn is None:
        return
    try:
        conn.commit()
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "exit lifecycle commit before venue I/O failed at %s: %s",
            stage,
            exc,
        )


# ---------------------------------------------------------------------------
# Lifecycle promoter — Bug #2 fix (PR-S2)
# Polls CLOB REST API for MATCHED/MINED rows and writes CONFIRMED facts.
# Authority: STRUCTURAL_PLAN.md v3 §2 PR-S2 + A_patches_plan.md §1
# ---------------------------------------------------------------------------

NON_TERMINAL_TRADE_STATUSES = frozenset({"MATCHED", "MINED"})
_PROMOTE_MIN_AGE_SECONDS = 60
_PROMOTE_MAX_AGE_SECONDS = 3600
_PROMOTE_LOCK_RETRY_ATTEMPTS = 5
_PROMOTE_LOCK_RETRY_SLEEP_SECONDS = 0.05


def _hash_raw_payload(payload: object) -> str:
    raw = json.dumps(payload or {}, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _is_sqlite_lock_error(exc: sqlite3.OperationalError) -> bool:
    lock_codes = {
        getattr(sqlite3, "SQLITE_BUSY", 5),
        getattr(sqlite3, "SQLITE_LOCKED", 6),
    }
    code = getattr(exc, "sqlite_errorcode", None)
    if code is not None:
        return code in lock_codes

    message = str(exc).lower()
    return (
        "database is locked" in message
        or "database table is locked" in message
        or "database is busy" in message
    )


def promote_pending_trades(
    conn: sqlite3.Connection,
    clob_client,
    max_age_seconds: int = _PROMOTE_MIN_AGE_SECONDS,
    max_cycle_budget_ms: int = 3000,
    recovery_mode: bool = False,
) -> dict:
    """Advance MATCHED venue_trade_facts rows to CONFIRMED by polling CLOB REST.

    Candidate SELECT is bounded to LIMIT 10. Loop honors max_cycle_budget_ms
    (default 3000ms — below httpx's 5s default so the deadline check fires
    before a single slow call exhausts the entire budget).

    Per-row re-check + append_trade_fact are wrapped in _savepoint_atomic so
    they are atomic against concurrent WS_USER ingests. SAVEPOINT nests cleanly
    inside any outer implicit transaction (CRITIC_FLAG-2, PR-S2 critic R1 fix).
    BEGIN IMMEDIATE was the prior approach; it raises OperationalError when
    cycle_runner's conn already has an open implicit transaction from prior
    DML (chain_sync, allocator, etc.), silently disabling the promoter.

    Writes CONFIRMED rows only. MINED is skipped (no intermediate writes) —
    aligns with FILL_STATUSES gate and F3 provenance bundle (state='CONFIRMED').

    Only EXIT-intent commands are eligible candidates. ENTRY commands are
    excluded via intent_kind filter to avoid premature promotion of live entry
    orders (bot review finding #4, PR #142).

    recovery_mode=True bypasses the abandon-window cutoff (_PROMOTE_MAX_AGE_SECONDS),
    allowing recovery of aged-out MATCHED rows. Use only in explicit recovery
    workflows, never in the normal cycle path.

    Error handling per A_patches_plan.md §1 table:
      404             → silent skip, no phantom write
      429             → abort entire batch
      other 4xx       → log + skip row
      5xx             → log + skip row (retry next cycle)
      unexpected exc  → log + skip row
    """
    import httpx
    from src.state.venue_command_repo import _savepoint_atomic, append_trade_fact

    deadline_ms = _time_module.monotonic() * 1000 + max_cycle_budget_ms
    cutoff_old = _utcnow() - timedelta(seconds=max_age_seconds)
    cutoff_abandon = _utcnow() - timedelta(seconds=_PROMOTE_MAX_AGE_SECONDS)

    if recovery_mode:
        abandon_clause = ""
        abandon_params: tuple = ()
    else:
        abandon_clause = "AND vtf.observed_at > ?"
        abandon_params = (cutoff_abandon.isoformat(),)

    candidates = conn.execute(
        f"""
        SELECT vtf.trade_fact_id,
               vtf.trade_id,
               vtf.venue_order_id,
               vtf.command_id,
               vtf.state,
               vtf.local_sequence,
               vtf.observed_at,
               vtf.filled_size,
               vtf.fill_price
        FROM venue_trade_facts vtf
        JOIN venue_commands cmd ON cmd.command_id = vtf.command_id
        WHERE vtf.state IN ('MATCHED', 'MINED')
          AND UPPER(COALESCE(cmd.intent_kind, '')) = 'EXIT'
          AND vtf.observed_at < ?
          {abandon_clause}
          AND NOT EXISTS (
              SELECT 1 FROM venue_trade_facts c2
              WHERE c2.command_id = vtf.command_id
                AND c2.state = 'CONFIRMED'
          )
        ORDER BY vtf.observed_at ASC
        LIMIT 10
        """,
        (cutoff_old.isoformat(),) + abandon_params,
    ).fetchall()

    stats: dict = {"polled": 0, "promoted": 0, "errors": 0, "skipped": 0}

    persistent_lock_seen = False
    for row in candidates:
        if persistent_lock_seen:
            break
        if _time_module.monotonic() * 1000 >= deadline_ms:
            _cnt_inc("promote_pending_trades_budget_exhausted_total")
            logger.warning(
                "telemetry_counter event=promote_pending_trades_budget_exhausted_total"
            )
            break

        (
            _trade_fact_id,
            trade_id,
            venue_order_id,
            command_id,
            _state,
            _seq,
            _observed_at,
            filled_size,
            fill_price,
        ) = row

        try:
            raw = _venue_order_payload(clob_client.get_order(venue_order_id))
            stats["polled"] += 1
        except Exception as exc:
            if isinstance(exc, httpx.HTTPStatusError):
                status_code = exc.response.status_code
                if status_code == 429:
                    logger.warning(
                        "promote_pending_trades: 429 rate-limited; aborting batch"
                    )
                    stats["errors"] += 1
                    break
                if 400 <= status_code < 500:
                    logger.warning(
                        "promote_pending_trades: 4xx on order_id=%s: %s",
                        venue_order_id, exc,
                    )
                    stats["skipped"] += 1
                else:
                    logger.error(
                        "promote_pending_trades: 5xx on order_id=%s: %s",
                        venue_order_id, exc, exc_info=True,
                    )
                    stats["errors"] += 1
            else:
                logger.error(
                    "promote_pending_trades: unexpected exc on order_id=%s: %s",
                    venue_order_id, exc, exc_info=True,
                )
                stats["errors"] += 1
            continue

        if raw is None:
            # 404 — order unknown to CLOB; skip without writing phantom row.
            logger.warning(
                "promote_pending_trades: order_id=%s returned None (404) — skipping",
                venue_order_id,
            )
            stats["skipped"] += 1
            continue

        new_status = (raw.get("status") or raw.get("state") or "").upper()

        # Major fix #3: only write CONFIRMED rows. MINED is not a fill authority.
        if new_status != "CONFIRMED":
            stats["skipped"] += 1
            continue

        tx_hash = raw.get("transaction_hash") or raw.get("transactionHash") or raw.get("tx_hash")
        last_update = raw.get("last_update") or _utcnow().isoformat()
        rest_size = raw.get("size") or raw.get("filled_size") or filled_size or "0"
        rest_price = raw.get("price") or raw.get("fill_price") or fill_price or "0"

        promoted = False
        for attempt in range(_PROMOTE_LOCK_RETRY_ATTEMPTS):
            if _time_module.monotonic() * 1000 >= deadline_ms:
                _cnt_inc("promote_pending_trades_sqlite_lock_skipped_total")
                logger.warning(
                    "promote_pending_trades: cycle budget exhausted while "
                    "waiting for sqlite writer lock; skipping remaining "
                    "candidates until next cycle"
                )
                stats["skipped"] += 1
                persistent_lock_seen = True
                break
            try:
                # CRITIC_FLAG-2: SAVEPOINT wraps re-check + append_trade_fact
                # atomically. A concurrent promoter may hold the SQLite writer
                # lock for this command; retry and re-check so one winner writes
                # CONFIRMED and the loser observes it instead of surfacing
                # OperationalError to the cycle.
                with _savepoint_atomic(conn):
                    already = conn.execute(
                        "SELECT 1 FROM venue_trade_facts WHERE command_id=? AND state='CONFIRMED'",
                        (command_id,),
                    ).fetchone()
                    if already:
                        stats["skipped"] += 1
                        break

                    append_trade_fact(
                        conn,
                        trade_id=trade_id,
                        venue_order_id=venue_order_id,
                        command_id=command_id,
                        state="CONFIRMED",
                        filled_size=str(rest_size),
                        fill_price=str(rest_price),
                        tx_hash=tx_hash,
                        source="REST",
                        observed_at=last_update,
                        raw_payload_hash=_hash_raw_payload(raw),
                        raw_payload_json=raw,
                    )
                promoted = True
                break
            except sqlite3.OperationalError as exc:
                if not _is_sqlite_lock_error(exc):
                    raise
                if attempt + 1 >= _PROMOTE_LOCK_RETRY_ATTEMPTS:
                    _cnt_inc("promote_pending_trades_sqlite_lock_skipped_total")
                    logger.warning(
                        "promote_pending_trades: sqlite writer lock persisted for "
                        "command_id=%s order_id=%s; skipping remaining "
                        "candidates until next cycle",
                        command_id,
                        venue_order_id,
                    )
                    stats["skipped"] += 1
                    persistent_lock_seen = True
                    break
                _time_module.sleep(_PROMOTE_LOCK_RETRY_SLEEP_SECONDS * (attempt + 1))

        if promoted:
            stats["promoted"] += 1
            logger.info(
                "promote_pending_trades: promoted trade_id=%s order_id=%s → CONFIRMED tx=%s",
                trade_id, venue_order_id, tx_hash,
            )
        elif persistent_lock_seen:
            break

    return stats


def _active_runtime_state(position: Position) -> str:
    return "day0_window" if getattr(position, "day0_entered_at", "") else "holding"


def _mark_pending_exit(position: Position) -> None:
    if position.state == "pending_exit":
        return
    if not getattr(position, "pre_exit_state", ""):
        position.pre_exit_state = getattr(position.state, "value", position.state)
    position.state = enter_pending_exit_runtime_state(
        getattr(position, "state", ""),
        exit_state=getattr(position, "exit_state", ""),
        chain_state=getattr(position, "chain_state", ""),
    )


def _exit_context_is_after_settlement_or_market_closed(exit_context: ExitContext) -> bool:
    reason = str(getattr(exit_context, "exit_reason", "") or "").upper()
    if "MARKET_CLOSED" in reason or "CLOSED_MARKET" in reason:
        return True
    hours_to_settlement = getattr(exit_context, "hours_to_settlement", None)
    if hours_to_settlement is None:
        return False
    try:
        hours = float(hours_to_settlement)
    except (TypeError, ValueError):
        return False
    return math.isfinite(hours) and hours <= 0.0


def _market_closed_hold_reason_from_exit_context(exit_context: ExitContext) -> str:
    reason = str(getattr(exit_context, "exit_reason", "") or "").upper()
    if "DAY0_HARD_FACT_BIN_DEAD" in reason:
        return "DAY0_HARD_FACT_BIN_DEAD_MARKET_CLOSED"
    return "MARKET_CLOSED_AWAITING_SETTLEMENT"


def mark_market_closed_hold_to_settlement(
    position: Position,
    *,
    reason: str = "MARKET_CLOSED_AWAITING_SETTLEMENT",
    error: str = "market_closed_non_accepting_orders",
    conn: sqlite3.Connection | None = None,
    preserve_exit_reason: bool = False,
) -> None:
    """Record a market-closed hold without manufacturing a sell failure.

    Once the market is closed, quote freshness is no longer a solvable exit
    precondition. That is a held-to-settlement monitor fact, not an
    EXIT_ORDER_REJECTED event: no sell was submitted, no venue order failed,
    and the position must keep flowing through held-position redecision and
    settlement harvesting.
    """

    current_state = _runtime_state_value(position)
    if current_state in {
        # T5 (docs/rebuild/quarantine_excision_2026-07-11.md): QUARANTINED
        # retired — no writer mints it; Position.__post_init__ remaps any
        # legacy row before pos.state ever sees it.
        LifecyclePhase.ECONOMICALLY_CLOSED.value,
        LifecyclePhase.SETTLED.value,
        LifecyclePhase.VOIDED.value,
        LifecyclePhase.ADMIN_CLOSED.value,
    }:
        position.state = current_state
    else:
        position.state = LifecyclePhase.DAY0_WINDOW.value
    position.pre_exit_state = ""
    position.exit_state = ""
    position.next_exit_retry_at = ""
    position.exit_retry_count = 0
    order_status = getattr(position, "order_status", "")
    order_status = getattr(order_status, "value", order_status)
    if str(order_status or "") in {
        "backoff_exhausted",
        "retry_pending",
        "sell_pending",
        "sell_placed",
    }:
        position.order_status = "filled"
    if not preserve_exit_reason:
        position.exit_reason = reason
    position.last_exit_error = f"{reason}:{error}"[:500]
    monitor_provenance = str(position.selected_method or position.entry_method or "")
    if not bool(getattr(position, "last_monitor_prob_is_fresh", False)) or not monitor_provenance:
        position.last_monitor_prob = None
        position.last_monitor_edge = None
        position.last_monitor_market_price = None
        position.last_monitor_market_price_is_fresh = False
        position.last_monitor_best_bid = None
        position.last_monitor_best_ask = None
        position.last_monitor_market_vig = None
    validations = list(getattr(position, "applied_validations", []) or [])
    if not monitor_provenance and "monitor_probability_provenance_missing" not in validations:
        validations.append("monitor_probability_provenance_missing")
    if reason not in validations:
        validations.append(reason)
    position.applied_validations = validations
    _dual_write_market_closed_hold_if_available(
        conn,
        position,
        reason=reason,
        error=error,
        preserve_exit_reason=preserve_exit_reason,
    )


def _restore_last_monitor_snapshot_for_closed_hold(
    conn: sqlite3.Connection,
    position: Position,
) -> None:
    """Carry the last monitor evidence through a market-closed hold write.

    The hold event is not a new executable quote, but erasing the last fresh
    held-side belief/price makes the continuous redecision overlay blind until
    settlement. Prefer the durable projection when it is fresh, because the
    in-memory object may be stale on the closed-market preemption path.
    """

    columns = (
        "entry_method",
        "selected_method",
        "last_monitor_prob",
        "last_monitor_prob_is_fresh",
        "last_monitor_edge",
        "last_monitor_market_price",
        "last_monitor_market_price_is_fresh",
        "last_monitor_best_bid",
        "last_monitor_best_ask",
        "last_monitor_market_vig",
    )
    try:
        existing_columns = {
            str(row[1])
            for row in conn.execute("PRAGMA table_info(position_current)").fetchall()
        }
        select_exprs = [
            name if name in existing_columns else f"NULL AS {name}"
            for name in columns
        ]
        row = conn.execute(
            f"""
            SELECT {", ".join(select_exprs)}
              FROM position_current
             WHERE position_id = ?
             LIMIT 1
            """,
            (str(getattr(position, "trade_id", "") or ""),),
        ).fetchone()
    except sqlite3.Error:
        return
    if row is None:
        return

    def _value(name: str) -> object:
        try:
            return row[name]
        except Exception:
            try:
                return row[columns.index(name)]
            except Exception:
                return None

    monitor_provenance = str(_value("selected_method") or _value("entry_method") or "")
    if bool(_value("last_monitor_prob_is_fresh")) and monitor_provenance:
        position.entry_method = str(_value("entry_method") or getattr(position, "entry_method", "") or "")
        position.selected_method = str(
            _value("selected_method") or getattr(position, "selected_method", "") or ""
        )
        position.last_monitor_prob = _value("last_monitor_prob")  # type: ignore[assignment]
        position.last_monitor_prob_is_fresh = True
        position.last_monitor_edge = _value("last_monitor_edge")  # type: ignore[assignment]
    if bool(_value("last_monitor_market_price_is_fresh")):
        position.last_monitor_market_price = _value("last_monitor_market_price")
        position.last_monitor_market_price_is_fresh = True
        position.last_monitor_best_bid = _value("last_monitor_best_bid")
        position.last_monitor_best_ask = _value("last_monitor_best_ask")
        position.last_monitor_market_vig = _value("last_monitor_market_vig")


def _dual_write_market_closed_hold_if_available(
    conn: sqlite3.Connection | None,
    position: Position,
    *,
    reason: str,
    error: str,
    preserve_exit_reason: bool = False,
) -> bool:
    """Persist a no-transition Day0 monitor hold for closed markets."""

    if conn is None:
        return False
    trade_id = str(getattr(position, "trade_id", "") or "")
    if not trade_id:
        return False
    idempotency_key = _market_closed_hold_idempotency_key(
        trade_id=trade_id,
        reason=reason,
        error=error,
    )
    try:
        from src.engine.lifecycle_events import build_monitor_refreshed_canonical_write
        from src.state.db import append_many_and_project

        if _has_equivalent_market_closed_hold(
            conn,
            trade_id,
            reason=reason,
            error=error,
            idempotency_key=idempotency_key,
        ):
            return False
        sequence_no = _next_canonical_sequence_no(conn, trade_id)
        occurred_at = datetime.now(timezone.utc).isoformat()
        _restore_last_monitor_snapshot_for_closed_hold(conn, position)
        position.last_monitor_at = occurred_at
        if "closed_market_hold_preserved_monitor_evidence" not in position.applied_validations:
            position.applied_validations.append("closed_market_hold_preserved_monitor_evidence")
        phase_after = _runtime_state_value(position) or LifecyclePhase.DAY0_WINDOW.value
        events, projection = build_monitor_refreshed_canonical_write(
            position,
            sequence_no=sequence_no,
            phase_after=phase_after,
            source_module="src.execution.exit_lifecycle",
        )
        event = dict(events[0])
        payload = json.loads(str(event.get("payload_json") or "{}"))
        payload.update(
            {
                "semantic_event": "MARKET_CLOSED_HOLD_TO_SETTLEMENT",
                "hold_reason": reason,
                "market_closed_error": error,
                "exit_order_submitted": False,
                "exit_failure": False,
            }
        )
        event["event_id"] = f"{trade_id}:market_closed_hold:{sequence_no}"
        event["caused_by"] = "market_closed_hold_to_settlement"
        event["idempotency_key"] = idempotency_key
        event["occurred_at"] = occurred_at
        event["venue_status"] = None
        event["payload_json"] = json.dumps(payload, default=str, sort_keys=True)
        projection["updated_at"] = occurred_at
        projection["phase"] = phase_after
        projection["order_status"] = getattr(position, "order_status", "") or "filled"
        projection["exit_reason"] = (
            getattr(position, "exit_reason", "") or ""
            if preserve_exit_reason
            else reason
        )
        projection["exit_retry_count"] = 0
        projection["next_exit_retry_at"] = ""
        try:
            append_many_and_project(conn, [event], projection)
        except sqlite3.IntegrityError as exc:
            if _is_position_event_idempotency_collision(exc):
                return False
            raise
        return True
    except Exception as exc:  # noqa: BLE001 - monitor can retry next cycle
        logger.warning(
            "market closed hold projection failed for %s: %s",
            trade_id,
            exc,
        )
        return False


def _has_equivalent_market_closed_hold(
    conn: sqlite3.Connection,
    position_id: str,
    *,
    reason: str,
    error: str,
    idempotency_key: str | None = None,
) -> bool:
    """Return true when the same closed-market hold is already recorded."""

    try:
        if idempotency_key:
            row = conn.execute(
                """
                SELECT 1
                  FROM position_events
                 WHERE position_id = ?
                   AND idempotency_key = ?
                 LIMIT 1
                """,
                (position_id, idempotency_key),
            ).fetchone()
            if row is not None:
                return True
        rows = conn.execute(
            """
            SELECT payload_json
              FROM position_events
             WHERE position_id = ?
               AND event_type = 'MONITOR_REFRESHED'
             ORDER BY sequence_no DESC
            """,
            (position_id,),
        ).fetchall()
    except sqlite3.Error:
        return False
    for row in rows:
        try:
            raw_payload = row["payload_json"]
        except Exception:
            raw_payload = row[0] if row else None
        try:
            payload = json.loads(str(raw_payload or "{}"))
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue
        if payload.get("semantic_event") != "MARKET_CLOSED_HOLD_TO_SETTLEMENT":
            continue
        return (
            str(payload.get("hold_reason") or "") == reason
            and str(payload.get("market_closed_error") or "") == error
            and payload.get("exit_order_submitted") is False
            and payload.get("exit_failure") is False
        )
    return False


def _semantic_position_event_idempotency_key(prefix: str, *parts: object) -> str:
    canonical = json.dumps(
        [str(part if part is not None else "") for part in parts],
        ensure_ascii=True,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:32]
    return f"{prefix}:{digest}"


def _market_closed_hold_idempotency_key(
    *,
    trade_id: str,
    reason: str,
    error: str,
) -> str:
    return _semantic_position_event_idempotency_key(
        "market_closed_hold",
        trade_id,
        reason,
        error,
    )


def _chain_dust_projection_idempotency_key(
    *,
    trade_id: str,
    chain_balance_units: int,
    chain_balance_shares: Decimal,
    asset_id: str,
) -> str:
    return _semantic_position_event_idempotency_key(
        "chain_dust_projection_corrected",
        trade_id,
        chain_balance_units,
        str(chain_balance_shares),
        asset_id,
    )


def _is_position_event_idempotency_collision(exc: sqlite3.IntegrityError) -> bool:
    return "position_events.idempotency_key" in str(exc)


def release_market_closed_pending_exit_hold(
    position: Position,
    *,
    conn: sqlite3.Connection | None = None,
) -> bool:
    """Repair legacy market-closed pending_exit rows back into held Day0 state.

    This is deliberately narrow: only rows that were stranded by the old
    MARKET_CLOSED_AWAITING_SETTLEMENT projection, still have chain-confirmed
    shares, and have no EXIT venue command are repaired. Genuine dust/backoff
    exit failures stay in the exit lifecycle lane.
    """

    if _runtime_state_value(position) != "pending_exit":
        return False
    exit_state = getattr(position, "exit_state", "")
    exit_state = getattr(exit_state, "value", exit_state)
    if str(exit_state or "") != "backoff_exhausted":
        return False
    if str(getattr(position, "exit_reason", "") or "") != "MARKET_CLOSED_AWAITING_SETTLEMENT":
        return False
    chain_shares = _positive_decimal(getattr(position, "chain_shares", None))
    if chain_shares is None or chain_shares <= 0:
        return False
    if conn is None:
        return False
    try:
        row = conn.execute(
            """
            SELECT 1
              FROM venue_commands
             WHERE position_id = ?
               AND intent_kind = 'EXIT'
             LIMIT 1
            """,
            (str(getattr(position, "trade_id", "") or ""),),
        ).fetchone()
    except sqlite3.Error:
        return False
    if row is not None:
        return False
    mark_market_closed_hold_to_settlement(
        position,
        reason="MARKET_CLOSED_AWAITING_SETTLEMENT",
        error="legacy_pending_exit_projection_repaired",
        conn=conn,
    )
    return True


def _exit_token_id(position: Position) -> str:
    direction = getattr(position, "direction", "")
    direction = str(getattr(direction, "value", direction) or "")
    token_id = (
        getattr(position, "token_id", "")
        if direction == "buy_yes"
        else getattr(position, "no_token_id", "")
    )
    return str(token_id or "").strip()


def _is_below_latest_snapshot_min_order(
    position: Position,
    *,
    conn: sqlite3.Connection | None,
) -> bool:
    if conn is None:
        return False
    token_id = _exit_token_id(position)
    shares = _positive_decimal(getattr(position, "effective_shares", None))
    if shares is None:
        shares = _positive_decimal(getattr(position, "shares", None))
    if not token_id or shares is None:
        return False
    saved = conn.row_factory
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            """
            SELECT min_order_size
              FROM executable_market_snapshots
             WHERE selected_outcome_token_id = ?
             ORDER BY captured_at DESC, snapshot_id DESC
             LIMIT 1
            """,
            (token_id,),
        ).fetchone()
    except sqlite3.Error:
        return False
    finally:
        conn.row_factory = saved
    min_order = _positive_decimal(row["min_order_size"] if row is not None else None)
    return min_order is not None and shares < min_order


def _dust_evidence_marks_non_executable(evidence: str) -> bool:
    return (
        "[DUST:" in evidence
        or "EXIT_CHAIN_DUST_STILL_HELD" in evidence
        or (
            "executable_snapshot_gate:" in evidence
            and "min_order_size" in evidence
        )
    )


def _is_non_executable_dust_hold(
    position: Position,
    *,
    conn: sqlite3.Connection | None = None,
) -> bool:
    """True for dust/min-size holds that redecision cannot make executable."""

    if _runtime_state_value(position) != "pending_exit":
        return False
    exit_state = getattr(position, "exit_state", "")
    exit_state = getattr(exit_state, "value", exit_state)
    if str(exit_state or "") != "backoff_exhausted":
        return False
    if _is_below_latest_snapshot_min_order(position, conn=conn):
        return True
    reason = str(getattr(position, "exit_reason", "") or "")
    last_error = str(getattr(position, "last_exit_error", "") or "")
    return _dust_evidence_marks_non_executable(f"{reason} {last_error}")


def _canonical_non_executable_dust_hold(
    position: Position,
    *,
    conn: sqlite3.Connection | None,
    now: datetime | None = None,
) -> tuple[str, str] | None:
    """Return current canonical dust-hold evidence even if the runtime object is stale.

    A historical ``[DUST: ...]`` reason is not enough to suppress a fresh exit:
    min-order and chain balance are time-varying. Suppression requires a fresh
    executable snapshot proving the canonical shares remain below min order.
    """

    if conn is None:
        return None
    trade_id = str(getattr(position, "trade_id", "") or "").strip()
    if not trade_id:
        return None
    saved = conn.row_factory
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            """
            SELECT phase,
                   order_status,
                   exit_reason,
                   shares,
                   chain_shares,
                   direction,
                   token_id,
                   no_token_id
              FROM position_current
             WHERE position_id = ?
             LIMIT 1
            """,
            (trade_id,),
        ).fetchone()
    except sqlite3.Error:
        return None
    finally:
        conn.row_factory = saved
    if row is None:
        return None
    phase = str(row["phase"] or "")
    order_status = str(row["order_status"] or "")
    if phase != "pending_exit" or order_status != "backoff_exhausted":
        return None

    reason = str(row["exit_reason"] or "")

    direction = str(row["direction"] or "")
    token_id = str(row["token_id"] or "")
    no_token_id = str(row["no_token_id"] or "")
    selected_token_id = token_id if direction == "buy_yes" else no_token_id or token_id
    if not selected_token_id:
        return None
    shares = _positive_decimal(row["chain_shares"])
    if shares is None:
        shares = _positive_decimal(row["shares"])
    if shares is None:
        return None

    saved = conn.row_factory
    conn.row_factory = sqlite3.Row
    try:
        snapshot = conn.execute(
            """
            SELECT min_order_size
              FROM executable_market_snapshots
             WHERE selected_outcome_token_id = ?
               AND (freshness_deadline IS NULL OR datetime(freshness_deadline) >= datetime(?))
             ORDER BY captured_at DESC, snapshot_id DESC
             LIMIT 1
            """,
            (selected_token_id, (now or _utcnow()).astimezone(timezone.utc).isoformat()),
        ).fetchone()
    except sqlite3.Error:
        return None
    finally:
        conn.row_factory = saved
    min_order = _positive_decimal(snapshot["min_order_size"] if snapshot is not None else None)
    if min_order is None or shares >= min_order:
        return None
    error = f"executable_snapshot_gate: size {shares} is below snapshot min_order_size {min_order}"
    return reason or f"CANONICAL_DUST_HOLD [DUST: {error}]", error


def _sync_runtime_to_canonical_dust_hold(
    position: Position,
    *,
    reason: str,
    error: str,
) -> None:
    _mark_pending_exit(position)
    position.exit_state = "backoff_exhausted"
    position.order_status = "backoff_exhausted"
    position.next_exit_retry_at = ""
    position.exit_reason = reason
    position.last_exit_error = (error or reason)[:500]


def release_backoff_exhausted_pending_exit_for_redecision(
    position: Position,
    *,
    conn: sqlite3.Connection | None = None,
) -> bool:
    """Release a still-held exhausted exit attempt back to live redecision.

    ``backoff_exhausted`` belongs to the last sell-order attempt chain. It is
    not a permanent held-position lifecycle phase. If the position still has
    positive exposure, the next monitor cycle must recompute belief, market
    value, and exit/hold/shift intent instead of disappearing behind the old
    retry budget.
    """

    if _runtime_state_value(position) != "pending_exit":
        return False
    exit_state = getattr(position, "exit_state", "")
    exit_state = getattr(exit_state, "value", exit_state)
    if str(exit_state or "") != "backoff_exhausted":
        return False
    if _is_non_executable_dust_hold(position, conn=conn):
        return False
    chain_shares = _positive_decimal(getattr(position, "chain_shares", None))
    shares = _positive_decimal(getattr(position, "effective_shares", None))
    if shares is None:
        shares = _positive_decimal(getattr(position, "shares", None))
    if (chain_shares is None or chain_shares <= 0) and (shares is None or shares <= 0):
        return False

    prior_error = str(getattr(position, "last_exit_error", "") or "")
    position.exit_state = ""
    position.next_exit_retry_at = ""
    position.exit_retry_count = 0
    position.exit_reason = ""
    position.last_exit_error = ""
    if str(getattr(position, "order_status", "") or "") == "backoff_exhausted":
        position.order_status = "filled"
    _release_pending_exit(position)
    if conn is not None:
        from src.state.db import log_pending_exit_recovery_event

        log_pending_exit_recovery_event(
            conn,
            position,
            event_type="EXIT_RETRY_RELEASED",
            reason="BACKOFF_EXHAUSTED_REDECISION_RELEASED",
            error=prior_error,
        )
    return True


def _release_pending_exit(position: Position) -> None:
    if position.state == "pending_exit":
        position.state = release_pending_exit_runtime_state(
            getattr(position, "pre_exit_state", ""),
            day0_entered_at=getattr(position, "day0_entered_at", ""),
        )
        position.pre_exit_state = ""


def _next_canonical_sequence_no(conn: sqlite3.Connection, position_id: str) -> int:
    try:
        row = conn.execute(
            "SELECT COALESCE(MAX(sequence_no), 0) FROM position_events WHERE position_id = ?",
            (position_id,),
        ).fetchone()
    except sqlite3.OperationalError:
        return 1
    return int(row[0] or 0) + 1


_CANONICAL_ENTRY_EVENT_TYPES = (
    "POSITION_OPEN_INTENT",
    "ENTRY_ORDER_POSTED",
    "ENTRY_ORDER_FILLED",
)


def _existing_canonical_entry_event_types(conn: sqlite3.Connection, position_id: str) -> set[str]:
    try:
        rows = conn.execute(
            """
            SELECT event_type
            FROM position_events
            WHERE position_id = ?
              AND event_type IN ('POSITION_OPEN_INTENT', 'ENTRY_ORDER_POSTED', 'ENTRY_ORDER_FILLED')
            """,
            (position_id,),
        ).fetchall()
    except sqlite3.OperationalError:
        return set()
    return {str(row[0]) for row in rows}


def _append_sequence_numbers(events: list[dict], *, start_sequence_no: int) -> list[dict]:
    resequenced: list[dict] = []
    for offset, event in enumerate(events):
        updated = dict(event)
        updated["sequence_no"] = start_sequence_no + offset
        resequenced.append(updated)
    return resequenced


def _canonical_phase_before_for_economic_close(position: Position) -> str:
    return "pending_exit"


def _dual_write_canonical_economic_close_if_available(
    conn: sqlite3.Connection | None,
    position: Position,
    *,
    phase_before: str,
    command_id: str | None = None,
) -> bool:
    if conn is None:
        return False

    import copy

    from src.engine.lifecycle_events import build_economic_close_canonical_write, build_entry_canonical_write
    from src.state.db import append_many_and_project

    trade_id = getattr(position, "trade_id", "")
    existing_entry_types = _existing_canonical_entry_event_types(conn, trade_id)
    missing_entry_types = [
        event_type
        for event_type in _CANONICAL_ENTRY_EVENT_TYPES
        if event_type not in existing_entry_types
    ]

    next_sequence_no = _next_canonical_sequence_no(conn, trade_id)

    if missing_entry_types:
        # Backfill missing canonical entry events for positions that predate
        # full canonical entry history. Existing canonical events are
        # append-only history: even a DAY0_WINDOW_ENTERED row must not suppress
        # entry backfill, and no existing row may be renumbered or mutated.
        # Create an entry-phase snapshot so build_entry_canonical_write
        # produces the standard sequence (OPEN_INTENT / ORDER_POSTED /
        # ORDER_FILLED → active), filter to only missing event types, then
        # resequence the filtered events after the current max sequence.
        #
        # T4.1b 2026-04-23 (D4 Option E): these legacy positions have no
        # captured `DecisionEvidence` (the decision frame predates the
        # T4.1b accept-path wiring). Emit the `decision_evidence_reason`
        # sentinel "backfill_legacy_position" into the ENTRY_ORDER_POSTED
        # payload so the Wave31 D4 hard gate and post-hoc investigation can
        # distinguish missing-because-legacy from missing-because-bug. Without
        # this sentinel, every legacy position would look like a bug-level
        # missing-evidence case.
        entry_snapshot = copy.copy(position)
        entry_snapshot.state = "entered"
        entry_snapshot.exit_state = ""
        try:
            # F4 (docs/archive/2026-Q2/findings_historical/findings_2026_05_28.md §F4, 2026-05-28): backfill
            # synthesizes the canonical entry sequence for a legacy position
            # whose journey ended at exit. The snapshot is set to "entered"
            # (state=entered → phase ACTIVE) so we pass phase_after=ACTIVE
            # explicitly; the builder no longer derives it from the snapshot's
            # runtime strings.
            generated_entry_events, _ = build_entry_canonical_write(
                entry_snapshot,
                phase_after=LifecyclePhase.ACTIVE.value,
                source_module="src.execution.exit_lifecycle:backfill",
                decision_evidence_reason="backfill_legacy_position",
            )
        except Exception as exc:
            logger.debug(
                "Canonical entry backfill failed for %s: %s", trade_id, exc,
            )
            return False
        entry_events = [
            event
            for event in generated_entry_events
            if event.get("event_type") in missing_entry_types
        ]
        entry_events = _append_sequence_numbers(
            entry_events,
            start_sequence_no=next_sequence_no,
        )
        exit_seq = next_sequence_no + len(entry_events)
    else:
        entry_events = []
        exit_seq = next_sequence_no

    try:
        exit_events, projection = build_economic_close_canonical_write(
            position,
            sequence_no=exit_seq,
            phase_before=phase_before,
            source_module="src.execution.exit_lifecycle",
        )
        if command_id:
            for event in exit_events:
                if event.get("event_type") == "EXIT_ORDER_FILLED":
                    event["command_id"] = command_id
        all_events = entry_events + exit_events
        append_many_and_project(conn, all_events, projection)
    except Exception as exc:
        raise RuntimeError(
            f"canonical economic-close dual-write failed for {trade_id}: {exc}"
        ) from exc

    return True


def build_exit_intent(position: Position, exit_context: ExitContext) -> ExitIntent:
    """Build the explicit exit-intent contract before any execution behavior happens."""
    token_id = position.token_id if position.direction == "buy_yes" else position.no_token_id
    return ExitIntent(
        trade_id=position.trade_id,
        reason=exit_context.exit_reason,
        token_id=token_id,
        shares=position.effective_shares,
        current_market_price=float(exit_context.current_market_price) if exit_context.current_market_price is not None else 0.0,
        best_bid=exit_context.best_bid,
        fresh_prob=float(exit_context.fresh_prob) if exit_context.fresh_prob is not None else None,
        fresh_prob_is_fresh=exit_context.fresh_prob_is_fresh,
        best_ask=exit_context.best_ask,
        market_vig=exit_context.market_vig,
        hours_to_settlement=exit_context.hours_to_settlement,
        position_state=exit_context.position_state,
        day0_active=exit_context.day0_active,
    )


def _validate_exit_intent(position: Position, exit_context: ExitContext, exit_intent: ExitIntent) -> None:
    if exit_intent.trade_id != position.trade_id:
        raise ValueError("exit_intent trade_id mismatch")
    expected_token = position.token_id if position.direction == "buy_yes" else position.no_token_id
    if exit_intent.token_id != expected_token:
        raise ValueError("exit_intent token_id mismatch")
    if abs(exit_intent.shares - position.effective_shares) > 1e-9:
        raise ValueError("exit_intent shares mismatch")
    if exit_context.current_market_price is not None and abs(exit_intent.current_market_price - float(exit_context.current_market_price)) > 1e-9:
        raise ValueError("exit_intent current_market_price mismatch")
    if exit_context.fresh_prob is not None and exit_intent.fresh_prob is not None and abs(exit_intent.fresh_prob - float(exit_context.fresh_prob)) > 1e-9:
        raise ValueError("exit_intent fresh_prob mismatch")
    if exit_context.best_bid is not None and exit_intent.best_bid is not None and abs(exit_intent.best_bid - float(exit_context.best_bid)) > 1e-9:
        raise ValueError("exit_intent best_bid mismatch")
    if exit_context.best_ask is not None and exit_intent.best_ask is not None and abs(exit_intent.best_ask - float(exit_context.best_ask)) > 1e-9:
        raise ValueError("exit_intent best_ask mismatch")


def is_exit_cooldown_active(position: Position) -> bool:
    """Check if position is in retry cooldown period."""
    if position.exit_state != "retry_pending":
        return False
    deadline = _parse_iso(position.next_exit_retry_at)
    if deadline is None:
        return False
    if (
        _utcnow() < deadline
        and _is_runtime_submit_gate_block_error(str(getattr(position, "last_exit_error", "") or ""))
        and _runtime_submit_gate_currently_allows_submit()
    ):
        return False
    return _utcnow() < deadline


# ---------------------------------------------------------------------------
# CTF on-chain balance query — isolated helper for chain-truth void
# ---------------------------------------------------------------------------
# Created: 2026-05-19
# Authority basis: Fix A — ghost pending_exit chain-truth sync

_CTF_BALANCE_OF_SELECTOR = "0x00fdd58e"  # balanceOf(address,uint256) keccak256[:4]
_CTF_SCALE = Decimal("1000000")
_CHAIN_BALANCE_DUST_SHARES = Decimal("0.01")


def _abi_encode_balance_of(owner: str, token_id: str) -> str:
    """ABI-encode balanceOf(address,uint256) calldata.

    Returns hex string with 0x prefix:
      selector (4 bytes) + owner padded to 32 bytes + token_id padded to 32 bytes.
    """
    owner_clean = owner.lower().removeprefix("0x")
    if len(owner_clean) != 40:
        raise ValueError(f"invalid owner address: {owner!r}")
    try:
        int(owner_clean, 16)
    except ValueError:
        raise ValueError(f"invalid owner address (non-hex): {owner!r}")
    owner_word = owner_clean.rjust(64, "0")
    # token_id is a large decimal or hex string
    token_int = int(str(token_id), 10) if not str(token_id).startswith("0x") else int(str(token_id), 16)
    token_word = format(token_int, "064x")
    return f"{_CTF_BALANCE_OF_SELECTOR}{owner_word}{token_word}"


def _query_ctf_balance(
    asset_id: str,
    owner_address: str,
    rpc_url: str | None = None,
    rpc_call: Callable | None = None,
) -> int | None:
    """Query ERC-1155 balanceOf(owner, asset_id) on the Polygon CTF contract.

    Returns the integer balance (raw ERC-1155 units, scaled 1e6 for pUSD).
    Returns None on any RPC failure — callers must treat None as "unknown"
    and fail-open (no destructive action).

    Imports _json_rpc_call from polymarket_v2_adapter at call-time to avoid
    circular imports; the module-level import is deliberately deferred.
    """
    if not asset_id or not owner_address:
        return None
    try:
        from src.venue.polymarket_v2_adapter import (  # deferred: avoid circular import
            _json_rpc_call,
            DEFAULT_POLYGON_RPC_URL,
            POLYGON_CTF_ADDRESS,
        )
        if rpc_call is None:
            rpc_call = _json_rpc_call
        resolved_rpc_url = rpc_url or os.environ.get("POLYGON_RPC_URL", DEFAULT_POLYGON_RPC_URL)
        calldata = _abi_encode_balance_of(owner_address, asset_id)
        raw = rpc_call(resolved_rpc_url, "eth_call", [{"to": POLYGON_CTF_ADDRESS, "data": calldata}, "latest"])
        return int(str(raw or "0x0"), 16)
    except Exception as exc:
        logger.warning(
            "_query_ctf_balance failed for asset_id=%s owner=%s: %s",
            asset_id, owner_address, exc,
        )
        return None


def _ctf_units_to_shares(raw_units: int | str | Decimal) -> Decimal:
    """Convert raw ERC-1155 CTF units to Polymarket share units."""

    return Decimal(str(raw_units)) / _CTF_SCALE


def _decimal_to_float(value: Decimal) -> float:
    return float(value)


def _sync_position_to_chain_dust(
    position: Position,
    *,
    chain_balance_units: int,
    chain_balance_shares: Decimal,
    asset_id: str,
) -> tuple[float | None, bool]:
    """Shrink a pending-exit dust hold to the actual CTF balance.

    Chain truth is still positive, so the position must remain pending_exit,
    but local exposure must not continue to show the pre-exit size.
    """

    old_shares = _positive_decimal(getattr(position, "shares", None))
    if old_shares is None:
        old_shares = _positive_decimal(getattr(position, "effective_shares", None))
    old_chain_shares = _positive_decimal(getattr(position, "chain_shares", None))
    local_shares_before = float(old_shares) if old_shares is not None else None

    changed = old_shares != chain_balance_shares or old_chain_shares != chain_balance_shares
    ratio = Decimal("0")
    if old_shares is not None and old_shares > 0:
        ratio = chain_balance_shares / old_shares

    for field_name in ("cost_basis_usd", "size_usd", "filled_cost_basis_usd"):
        old_value = _positive_decimal(getattr(position, field_name, None))
        if old_value is None:
            continue
        new_value = old_value * ratio if ratio > 0 else Decimal("0")
        if old_value != new_value:
            setattr(position, field_name, _decimal_to_float(new_value))
            changed = True

    entry_price = _positive_decimal(getattr(position, "entry_price", None))
    if entry_price is not None:
        chain_cost_basis = chain_balance_shares * entry_price
        if _positive_decimal(getattr(position, "chain_cost_basis_usd", None)) != chain_cost_basis:
            position.chain_cost_basis_usd = _decimal_to_float(chain_cost_basis)
            changed = True
        if _positive_decimal(getattr(position, "chain_avg_price", None)) != entry_price:
            position.chain_avg_price = _decimal_to_float(entry_price)
            changed = True

    dust_shares_float = _decimal_to_float(chain_balance_shares)
    if getattr(position, "shares", None) != dust_shares_float:
        position.shares = dust_shares_float
        changed = True
    if getattr(position, "chain_shares", None) != dust_shares_float:
        position.chain_shares = dust_shares_float
        changed = True
    position.chain_state = "exit_pending_missing"
    position.chain_verified_at = datetime.now(timezone.utc).isoformat()
    position.last_exit_error = (
        f"chain_balance_units={chain_balance_units};"
        f"chain_balance_shares={chain_balance_shares};asset_id={asset_id}"
    )[:500]
    return local_shares_before, changed


def _write_chain_dust_projection_correction(
    conn: sqlite3.Connection | None,
    position: Position,
    *,
    local_shares_before: float | None,
    chain_balance_units: int,
    chain_balance_shares: Decimal,
    asset_id: str,
) -> bool:
    """Write a no-op-phase chain correction when dust event already exists."""

    if conn is None:
        return False
    trade_id = str(getattr(position, "trade_id", "") or "")
    if not trade_id:
        return False
    idempotency_key = _chain_dust_projection_idempotency_key(
        trade_id=trade_id,
        chain_balance_units=chain_balance_units,
        chain_balance_shares=chain_balance_shares,
        asset_id=asset_id,
    )
    if _chain_dust_projection_correction_already_recorded(
        conn,
        trade_id=trade_id,
        chain_balance_units=chain_balance_units,
        chain_balance_shares=chain_balance_shares,
        asset_id=asset_id,
        idempotency_key=idempotency_key,
    ):
        return False
    try:
        from src.engine.lifecycle_events import build_chain_size_corrected_canonical_write
        from src.state.db import append_many_and_project

        sequence_no = _next_canonical_sequence_no(conn, trade_id)
        events, projection = build_chain_size_corrected_canonical_write(
            position,
            local_shares_before=local_shares_before or 0.0,
            sequence_no=sequence_no,
            phase_after="pending_exit",
            source_module="src.execution.exit_lifecycle",
        )
        event = events[0]
        event["caused_by"] = "chain_dust_projection_corrected"
        event["idempotency_key"] = idempotency_key
        payload = json.loads(str(event.get("payload_json") or "{}"))
        payload.update(
            {
                "source": "exit_lifecycle",
                "reason": "chain_dust_projection_corrected",
                "chain_balance_units": chain_balance_units,
                "chain_balance_shares": str(chain_balance_shares),
                "asset_id": asset_id,
            }
        )
        event["payload_json"] = json.dumps(payload, default=str, sort_keys=True)
        try:
            append_many_and_project(conn, events, projection)
        except sqlite3.IntegrityError as exc:
            if _is_position_event_idempotency_collision(exc):
                return False
            raise
        return True
    except Exception as exc:  # noqa: BLE001 - fail closed to in-memory dust hold
        logger.warning(
            "chain dust projection correction failed for %s: %s",
            trade_id,
            exc,
        )
        return False


def _chain_dust_projection_correction_already_recorded(
    conn: sqlite3.Connection,
    *,
    trade_id: str,
    chain_balance_units: int,
    chain_balance_shares: Decimal,
    asset_id: str,
    idempotency_key: str | None = None,
) -> bool:
    try:
        if idempotency_key:
            row = conn.execute(
                """
                SELECT 1
                  FROM position_events
                 WHERE position_id = ?
                   AND idempotency_key = ?
                 LIMIT 1
                """,
                (trade_id, idempotency_key),
            ).fetchone()
            if row is not None:
                return True
        rows = conn.execute(
            """
            SELECT payload_json
              FROM position_events
             WHERE position_id = ?
               AND event_type = 'CHAIN_SIZE_CORRECTED'
               AND json_extract(payload_json, '$.reason') = 'chain_dust_projection_corrected'
             ORDER BY sequence_no DESC
            """,
            (trade_id,),
        ).fetchall()
    except sqlite3.Error:
        return False
    expected_units = str(chain_balance_units)
    expected_shares = str(chain_balance_shares)
    expected_asset = str(asset_id or "")
    for row in rows:
        try:
            raw_payload = row["payload_json"]
        except Exception:
            raw_payload = row[0] if row else None
        try:
            payload = json.loads(str(raw_payload or "{}"))
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue
        payload_units = payload.get("chain_balance_units")
        payload_shares = payload.get("chain_balance_shares")
        payload_asset = payload.get("asset_id")
        if (
            str(payload_units if payload_units is not None else "") == expected_units
            and str(payload_shares if payload_shares is not None else "") == expected_shares
            and str(payload_asset if payload_asset is not None else "") == expected_asset
        ):
            return True
    return False


def _asset_id_for_position(position: Position) -> str:
    """Return the ERC-1155 token ID (asset_id) the position holds."""
    if getattr(position, "direction", "") == "buy_yes":
        return str(getattr(position, "token_id", "") or "")
    return str(getattr(position, "no_token_id", "") or getattr(position, "token_id", "") or "")


def handle_exit_pending_missing(
    portfolio: PortfolioState,
    position: Position,
    conn: sqlite3.Connection | None = None,
    rpc_call: Callable | None = None,
) -> dict:
    """Own the `exit_pending_missing` escalation path for pending exits.

    Chain-truth gate (Fix A, 2026-05-19):
    Before falling back to in-memory exit_state branch logic, query the
    Polygon CTF ERC-1155 contract for the actual on-chain balance:
      - balance == 0 → position is sold on-chain; mark voided
      - balance > 0  → position still held; re-queue for exit retry
      - RPC failure  → fail-open, fall through to existing logic (no destructive action)
    """

    raw_chain_state = getattr(position, "chain_state", "") or ""
    chain_state_value = str(getattr(raw_chain_state, "value", raw_chain_state) or "")
    runtime_state_value = _runtime_state_value(position)
    if chain_state_value not in {
        "exit_pending_missing",
        "chain_absent_confirmed_position_unattributed",
    }:
        return {"action": "ignore", "position": None}
    if (
        chain_state_value == "chain_absent_confirmed_position_unattributed"
        and runtime_state_value != "pending_exit"
    ):
        return {"action": "ignore", "position": None}

    # ── Chain-truth gate ──────────────────────────────────────────────────────
    asset_id = _asset_id_for_position(position)
    safe_address = (
        os.environ.get("POLYMARKET_FUNDER_ADDRESS")
        or os.environ.get("POLYMARKET_PROXY_ADDRESS")
        or ""
    )
    if not safe_address:
        # SINGLE CONFIG AUTHORITY (2026-06-12): the env vars above were never
        # set in the daemon plist, so the chain-truth gate — the DESIGNED
        # resolution path for exit_pending_missing — was silently bypassed on
        # 100% of cycles and every position fell into the legacy branch.
        # Resolve the funder from the same Keychain authority
        # PolymarketClient uses; env vars remain an explicit override only.
        try:
            from src.data.polymarket_client import resolve_funder_address

            safe_address = resolve_funder_address()
        except Exception:  # noqa: BLE001 — credential absence falls back to legacy logic
            safe_address = ""
    if asset_id and safe_address:
        on_chain_balance = _query_ctf_balance(
            asset_id, safe_address, rpc_call=rpc_call
        )
        if on_chain_balance is not None:
            chain_balance_shares = _ctf_units_to_shares(on_chain_balance)
            if on_chain_balance == 0:
                # Chain confirms zero balance: position is closed. Void it.
                logger.info(
                    "CHAIN_TRUTH_VOID %s: on-chain balance=0 for asset_id=%s → voiding",
                    position.trade_id,
                    asset_id,
                )
                return _void_chain_confirmed_zero(portfolio, position, asset_id, conn)
            if chain_balance_shares <= _CHAIN_BALANCE_DUST_SHARES:
                dust_reason = "EXIT_CHAIN_DUST_STILL_HELD"
                dust_error = (
                    f"chain_balance_units={on_chain_balance};"
                    f"chain_balance_shares={chain_balance_shares};asset_id={asset_id}"
                )
                logger.info(
                    "CHAIN_TRUTH_DUST_HOLD %s: on-chain balance=%s units "
                    "(%s shares) for asset_id=%s → hold to settlement",
                    position.trade_id,
                    on_chain_balance,
                    chain_balance_shares,
                    asset_id,
                )
                _mark_exit_dust_hold(
                    position,
                    reason=dust_reason,
                    error=dust_error,
                    conn=conn,
                    chain_balance_units=int(on_chain_balance),
                    chain_balance_shares=chain_balance_shares,
                    asset_id=asset_id,
                )
                return {"action": "dust_hold", "position": position}
            else:
                # Position still held on-chain (balance > dust). FIX 2a
                # (2026-06-20): chain-truth confirms the position is genuinely
                # held, so it must reach the LIVE sell emitter this cycle — NOT
                # get re-stamped as EXIT_ORDER_REJECTED(EXIT_CHAIN_MISSING) with
                # last_exit_order_id=null and skipped on a cooldown. The prior
                # _mark_exit_retry armed an exponential cooldown that the monitor
                # loop then honored (is_exit_cooldown_active → continue), so the
                # position never reached evaluate_exit/execute_exit/place_sell_order
                # — one live position re-stamped an identical reject 1067×.
                #
                # Single-flight law: if a sell order is already on the book
                # (exit_state in exit_intent/sell_placed/sell_pending) we must NOT
                # route a fresh evaluate→execute pass (it could double-submit).
                # Keep the legacy in-flight handling for that case.
                # exit_state is a str-Enum (ExitState); str(member) yields the
                # enum repr ("ExitState.EXIT_INTENT"), so normalize to .value
                # before the membership test — otherwise the single-flight guard
                # silently never fires.
                _exit_state_value = getattr(
                    getattr(position, "exit_state", ""), "value", getattr(position, "exit_state", "")
                ) or ""
                in_flight = _exit_state_value in _EXIT_LIFECYCLE_IN_FLIGHT_STATES
                if in_flight:
                    # BLOCKER-1 fix (2026-06-20): this branch MUST be
                    # NON-MUTATING. A sell is already on the book (exit_state in
                    # {exit_intent, sell_placed, sell_pending}) and
                    # check_pending_exits (the exit-preflight fill poller) owns
                    # it — but it polls fills ONLY for exactly those exit_states.
                    # The prior _mark_exit_retry flipped exit_state→retry_pending
                    # and armed a cooldown, which (a) EVICTED the resting sell
                    # from the fast fill-polling lane and (b) could later
                    # repost/cancel it = churn / double-submit — the OPPOSITE of
                    # single-flight protection. So: do NOT _mark_exit_retry, do
                    # NOT touch exit_state / last_exit_order_id / order_status /
                    # next_exit_retry_at, and write NO EXIT_ORDER_REJECTED. Skip
                    # this position for the monitor THIS cycle and let the fill
                    # poller remain the sole order owner.
                    logger.info(
                        "CHAIN_TRUTH_IN_FLIGHT_SKIP %s: on-chain balance=%s units "
                        "(%s shares) for asset_id=%s; exit already in flight "
                        "(exit_state=%s, order_id=%s) → non-mutating skip, fill "
                        "poller owns the resting order",
                        position.trade_id,
                        on_chain_balance,
                        chain_balance_shares,
                        asset_id,
                        _exit_state_value,
                        getattr(position, "last_exit_order_id", "") or "",
                    )
                    return {"action": "skip", "position": None}
                # No resting order: release the pending_exit pre-emption so the
                # normal monitor path runs the full evaluate_exit → execute_exit →
                # place_sell_order lane THIS cycle. No reject stamp and no
                # cooldown — the canonical record of this state change is the
                # EXIT_INTENT / EXIT_ORDER_POSTED the live lane writes if it
                # decides to sell (or MONITOR_REFRESHED if it holds). chain_state
                # is left as the reconciliation lane owns it (settlement-only
                # truth: balance>dust is not the same claim as full share-parity
                # 'synced'); next cycle's chain-truth gate re-confirms and re-
                # routes identically, so the sell is attempted every cycle a bid
                # exists instead of being buried under a reject loop.
                logger.info(
                    "CHAIN_TRUTH_STILL_HELD_EVALUATE %s: on-chain balance=%s units "
                    "(%s shares) for asset_id=%s → routing to live exit evaluation",
                    position.trade_id,
                    on_chain_balance,
                    chain_balance_shares,
                    asset_id,
                )
                position.last_exit_error = (
                    f"chain_balance_units={on_chain_balance};"
                    f"chain_balance_shares={chain_balance_shares};asset_id={asset_id}"
                )[:500]
                position.next_exit_retry_at = ""
                if _exit_state_value == "retry_pending":
                    position.exit_state = ""
                _release_pending_exit(position)
                return {"action": "evaluate", "position": position}
        # on_chain_balance is None → RPC failure; fall through to legacy logic
        logger.warning(
            "CHAIN_TRUTH_RPC_FAIL %s: RPC unreachable, falling back to legacy exit_state logic",
            position.trade_id,
        )
    # ── Legacy exit_state branch logic ───────────────────────────────────────
    _mark_pending_exit(position)
    # FIX 2a (2026-06-20): the canonical payload's exit_reason is
    # `position.exit_reason or reason` (see canonical_write.transition_phase), so
    # dedupe against that effective value — NOT the bare `reason` arg — or the
    # epoch check would never match when a prior exit_reason is set.
    _legacy_reject_reason = str(getattr(position, "exit_reason", "") or "EXIT_CHAIN_MISSING")
    if not _latest_exit_reject_is_identical(conn, position, reason=_legacy_reject_reason):
        _dual_write_canonical_pending_exit_if_available(
            conn,
            position,
            reason="EXIT_CHAIN_MISSING",
            error=getattr(position, "last_exit_error", "") or "exit_pending_missing",
            event_type="EXIT_ORDER_REJECTED",
        )
    if position.exit_state == "backoff_exhausted":
        closed = mark_admin_closed(portfolio, position.trade_id, "EXIT_CHAIN_MISSING_REVIEW_REQUIRED")
        if closed is not None:
            _dual_write_canonical_admin_close_if_available(
                conn,
                closed,
                phase_before="pending_exit",
                reason="EXIT_CHAIN_MISSING_REVIEW_REQUIRED",
                error=getattr(position, "last_exit_error", "") or "exit_pending_missing",
            )
            return {"action": "closed", "position": closed}
        return {"action": "skip", "position": None}
    if position.exit_state in EXIT_LIFECYCLE_RECOVERY_STATES:
        # DELIBERATE in-memory-only close (antibody
        # test_recoverable_exit_pending_missing_does_not_persist_admin_close):
        # a recoverable state must keep its pending_exit projection so the next
        # cycle retries — persisting admin_closed here would hide real on-chain
        # exposure. The loop TERMINATES through the chain-truth gate above
        # (funder now resolves from Keychain, 2026-06-12) whose retries are
        # bounded by the persisted exit_retry_count → backoff_exhausted →
        # persisted admin close.
        closed = mark_admin_closed(portfolio, position.trade_id, "EXIT_CHAIN_MISSING_REVIEW_REQUIRED")
        return {"action": "closed", "position": closed}
    if position.exit_state in EXIT_LIFECYCLE_OWNED_STATES:
        return {"action": "skip", "position": None}
    return {"action": "ignore", "position": None}


def _void_chain_confirmed_zero(
    portfolio: PortfolioState,
    position: Position,
    asset_id: str,
    conn: sqlite3.Connection | None,
) -> dict:
    """Void a pending_exit position whose on-chain balance is confirmed zero.

    Emits an ADMIN_VOIDED position_event with evidence_source=CHAIN_BALANCEOF
    to make the chain-truth origin permanent in the audit trail.
    """
    from src.state.portfolio import void_position

    trade_id = position.trade_id
    voided = void_position(portfolio, trade_id, "CHAIN_CONFIRMED_ZERO")
    if voided is None:
        logger.warning(
            "_void_chain_confirmed_zero: void_position returned None for %s (already removed?)",
            trade_id,
        )
        return {"action": "skip", "position": None}
    voided.chain_state = "chain_confirmed_zero"
    voided.chain_shares = 0.0
    voided.order_status = "voided"
    voided.exit_state = ""
    voided.exit_retry_count = 0
    voided.next_exit_retry_at = ""

    # Emit canonical ADMIN_VOIDED event carrying chain-truth evidence
    if conn is not None:
        try:
            import json as _json

            from src.engine.lifecycle_events import build_position_current_projection
            from src.state.db import append_many_and_project
            from src.state.lifecycle_manager import fold_lifecycle_phase

            sequence_no = _next_canonical_sequence_no(conn, trade_id)
            occurred_at = getattr(voided, "last_exit_at", "") or datetime.now(timezone.utc).isoformat()
            projection = build_position_current_projection(voided)
            projection["updated_at"] = occurred_at
            projection["chain_state"] = "chain_confirmed_zero"
            projection["chain_shares"] = 0.0
            projection["order_status"] = "voided"
            projection["exit_retry_count"] = 0
            projection["next_exit_retry_at"] = None

            env = str(getattr(voided, "env", "") or "live")
            if env not in {"live", "test", "replay", "backtest"}:
                env = "live"

            event = {
                "event_id": f"{trade_id}:admin_voided_chain_zero:{sequence_no}",
                "position_id": trade_id,
                "event_version": 1,
                "sequence_no": sequence_no,
                "event_type": "ADMIN_VOIDED",
                "occurred_at": occurred_at,
                "phase_before": "pending_exit",
                "phase_after": fold_lifecycle_phase("pending_exit", "voided").value,
                "strategy_key": str(
                    getattr(voided, "strategy_key", "")
                    or getattr(voided, "strategy", "")
                    or ""
                ),
                "decision_id": None,
                "snapshot_id": getattr(voided, "decision_snapshot_id", "") or None,
                "order_id": getattr(voided, "last_exit_order_id", "") or getattr(voided, "order_id", "") or None,
                "command_id": None,
                "caused_by": "chain_truth_balance_zero",
                "idempotency_key": f"{trade_id}:admin_voided_chain_zero:{sequence_no}",
                "venue_status": "voided",
                "source_module": "src.execution.exit_lifecycle",
                "env": env,
                "payload_json": _json.dumps(
                    {
                        "reason": "CHAIN_CONFIRMED_ZERO",
                        "evidence_source": "CHAIN_BALANCEOF",
                        "asset_id": asset_id,
                        "chain_state": "chain_confirmed_zero",
                    },
                    default=str,
                    sort_keys=True,
                ),
            }
            append_many_and_project(conn, [event], projection)
        except Exception as exc:
            raise RuntimeError(
                f"_void_chain_confirmed_zero: canonical event write failed for {trade_id}: {exc}"
            ) from exc

    return {"action": "closed", "position": voided}


def _is_below_min_order_sell_error(error: str) -> bool:
    text = str(error or "").lower()
    return "below" in text and "min_order_size" in text


def _latest_exit_reject_is_identical(
    conn: sqlite3.Connection | None,
    position: Position,
    *,
    reason: str,
) -> bool:
    """Return True when the MOST RECENT canonical event is an identical reject.

    FIX 2a (2026-06-20): the RPC-fall-through legacy branch wrote a fresh
    EXIT_ORDER_REJECTED(EXIT_CHAIN_MISSING) every 2-min cycle. Because
    transition_phase keys idempotency on a monotonic sequence_no, each write
    is a distinct row — one live position accreted 1067 identical rejects with
    last_exit_order_id=null. Dedupe by state-epoch: suppress the re-stamp iff
    the single newest position_events row is already an EXIT_ORDER_REJECTED
    carrying this exit_reason. Any intervening state-change event (EXIT_INTENT,
    CHAIN_*, MONITOR_REFRESHED, a different reject, a backoff/admin escalation)
    becomes the newest row and re-opens the epoch, so a genuine escalation is
    never hidden — only the back-to-back identical re-stamp is dropped.
    """
    if conn is None:
        return False
    try:
        row = conn.execute(
            """
            SELECT event_type, payload_json
              FROM position_events
             WHERE position_id = ?
             ORDER BY sequence_no DESC
             LIMIT 1
            """,
            (position.trade_id,),
        ).fetchone()
    except sqlite3.Error:
        return False
    if row is None:
        return False
    try:
        event_type = str(row["event_type"] or "")
        payload = json.loads(str(row["payload_json"] or "{}"))
    except (TypeError, ValueError, IndexError, KeyError):
        return False
    if event_type != "EXIT_ORDER_REJECTED":
        return False
    if not isinstance(payload, dict):
        return False
    return str(payload.get("exit_reason") or "") == str(reason or "")


def _latest_exit_reject_error(
    conn: sqlite3.Connection | None,
    position: Position,
) -> str:
    """Return the newest canonical EXIT_ORDER_REJECTED error for retry recovery."""

    if conn is None:
        return ""
    trade_id = str(getattr(position, "trade_id", "") or "").strip()
    if not trade_id:
        return ""
    try:
        row = conn.execute(
            """
            SELECT payload_json
              FROM position_events
             WHERE position_id = ?
               AND event_type = 'EXIT_ORDER_REJECTED'
             ORDER BY sequence_no DESC
             LIMIT 1
            """,
            (trade_id,),
        ).fetchone()
    except sqlite3.Error:
        return ""
    if row is None:
        return ""
    try:
        payload = json.loads(str(row["payload_json"] or "{}"))
    except (TypeError, ValueError, IndexError, KeyError):
        return ""
    if not isinstance(payload, dict):
        return ""
    return str(payload.get("error") or "")


def _dust_hold_event_already_recorded(
    conn: sqlite3.Connection | None,
    position: Position,
    *,
    reason: str,
) -> bool:
    """Return True when this dust hold was already durably recorded.

    The latest position event may be a later chain-size correction, fill check,
    or status pulse.  Looking only at the newest EXIT_ORDER_REJECTED lets the
    same dust hold append again after any intervening event, which is exactly
    what makes a 0.01-share residue look like a live retry loop after restart.
    """
    if conn is None:
        return False
    try:
        rows = conn.execute(
            """
            SELECT payload_json
              FROM position_events
             WHERE position_id = ?
               AND event_type = 'EXIT_ORDER_REJECTED'
               AND json_extract(payload_json, '$.status') = 'backoff_exhausted'
               AND json_extract(payload_json, '$.exit_reason') = ?
             ORDER BY sequence_no DESC
             LIMIT 20
            """,
            (position.trade_id, str(reason or "")),
        ).fetchall()
    except sqlite3.Error:
        return False
    if not rows:
        return False
    for row in rows:
        try:
            payload = json.loads(str(row["payload_json"] or "{}"))
        except (TypeError, ValueError, IndexError, KeyError):
            continue
        if not isinstance(payload, dict):
            continue
        if (
            str(payload.get("status") or "") == "backoff_exhausted"
            and str(payload.get("exit_reason") or "") == str(reason or "")
        ):
            return True
    return False


def _mark_exit_dust_hold(
    position: Position,
    reason: str,
    error: str = "",
    conn: sqlite3.Connection | None = None,
    chain_balance_units: int | None = None,
    chain_balance_shares: Decimal | None = None,
    asset_id: str = "",
) -> None:
    """Hold a non-executable dust exit to settlement instead of retrying."""
    normalized_error = (error or "below_min_order_size")[:500]
    local_shares_before: float | None = None
    chain_projection_changed = False
    projection_changed = False
    if chain_balance_units is not None and chain_balance_shares is not None:
        local_shares_before, chain_projection_changed = _sync_position_to_chain_dust(
            position,
            chain_balance_units=chain_balance_units,
            chain_balance_shares=chain_balance_shares,
            asset_id=asset_id,
        )
        projection_changed = chain_projection_changed
        normalized_error = (getattr(position, "last_exit_error", "") or normalized_error)[:500]
    already_held = (
        str(getattr(position, "exit_state", "") or "") == "backoff_exhausted"
        and str(getattr(position, "exit_reason", "") or "") == str(reason or "")
    )
    _mark_pending_exit(position)
    old_order_status = str(getattr(position, "order_status", "") or "")
    position.exit_state = "backoff_exhausted"
    position.order_status = "backoff_exhausted"
    if old_order_status != "backoff_exhausted":
        projection_changed = True
    position.next_exit_retry_at = ""
    position.exit_reason = reason
    position.last_exit_error = normalized_error
    event_already_recorded = _dust_hold_event_already_recorded(conn, position, reason=reason)
    if already_held or event_already_recorded:
        if (
            chain_projection_changed
            and event_already_recorded
            and chain_balance_units is not None
            and chain_balance_shares is not None
        ):
            _write_chain_dust_projection_correction(
                conn,
                position,
                local_shares_before=local_shares_before,
                chain_balance_units=chain_balance_units or 0,
                chain_balance_shares=chain_balance_shares or Decimal("0"),
                asset_id=asset_id,
            )
        return
    _dual_write_canonical_pending_exit_if_available(
        conn,
        position,
        reason=reason,
        error=normalized_error,
        event_type="EXIT_ORDER_REJECTED",
        extra_payload=_snapshot_min_order_dust_audit_payload(
            position,
            reason=reason,
            error=normalized_error,
            chain_balance_shares=chain_balance_shares,
        ),
    )
    logger.warning(
        "EXIT DUST HOLD %s: %s. Holding to settlement; no sell retry is executable.",
        position.trade_id,
        reason,
    )


def _positive_decimal(value: object) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        numeric = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    if numeric <= 0:
        return None
    return numeric


def _snapshot_min_order_from_error(error: str) -> str:
    match = re.search(r"min_order_size\s+([0-9]+(?:\.[0-9]+)?)", error or "")
    return match.group(1) if match else ""


def _blocked_exit_shares(
    position: Position,
    *,
    chain_balance_shares: Decimal | None = None,
) -> str:
    for value in (
        chain_balance_shares,
        getattr(position, "effective_shares", None),
        getattr(position, "chain_shares", None),
        getattr(position, "shares", None),
    ):
        shares = _positive_decimal(value)
        if shares is not None:
            return str(shares)
    return ""


def _snapshot_min_order_dust_audit_payload(
    position: Position,
    *,
    reason: str,
    error: str,
    chain_balance_shares: Decimal | None = None,
) -> dict[str, object]:
    """Machine-readable evidence that a held exit is not currently executable."""

    return {
        "exit_block_class": "snapshot_min_order_dust",
        "exit_order_submitted": False,
        "operator_action_required": True,
        "held_to_settlement_unless_aggregate_exit_available": True,
        "blocked_shares": _blocked_exit_shares(
            position,
            chain_balance_shares=chain_balance_shares,
        ),
        "snapshot_min_order_size": _snapshot_min_order_from_error(error),
        "dust_hold_reason": reason,
    }


def _below_snapshot_min_order_error(position: Position, snapshot_context: dict[str, object]) -> str:
    min_order = _positive_decimal(snapshot_context.get("executable_snapshot_min_order_size"))
    shares = _positive_decimal(getattr(position, "effective_shares", None))
    if min_order is None or shares is None or shares >= min_order:
        return ""
    return f"executable_snapshot_gate: size {shares} is below snapshot min_order_size {min_order}"


def _latest_snapshot_min_order_dust_error(
    position: Position,
    *,
    conn: sqlite3.Connection | None,
) -> str:
    token_id = _exit_token_id(position)
    snapshot_context = _latest_exit_snapshot_context(
        conn,
        token_id,
        require_sell_bid=False,
    )
    return _below_snapshot_min_order_error(position, snapshot_context)


def _exit_no_executable_bid_error(
    exit_intent: ExitIntent,
    snapshot_context: dict[str, object],
) -> str:
    """Classify one-sided/no-bid sell attempts as liquidity blocked."""

    best_bid = _positive_decimal(exit_intent.best_bid)
    snapshot_bid = _positive_decimal(
        snapshot_context.get("executable_snapshot_orderbook_top_bid")
    )
    if best_bid is None or snapshot_bid is None:
        return "exit_no_executable_bid"
    return ""


def _record_exit_intent_before_execution_gates(
    conn: sqlite3.Connection | None,
    position: Position,
    exit_intent: ExitIntent,
) -> None:
    """Persist the semantic exit decision before executable-liquidity gates.

    Snapshot, liquidity, collateral, and venue checks are execution facts.  The
    monitor's decision to exit is a separate lifecycle fact and must be visible
    even when no sell command can be created.
    """

    _mark_pending_exit(position)
    position.exit_state = "exit_intent"
    position.order_status = "exit_intent"
    _dual_write_canonical_pending_exit_if_available(
        conn,
        position,
        reason=exit_intent.reason or "EXIT_INTENT",
        error="",
        event_type="EXIT_INTENT",
        extra_payload=_exit_intent_audit_payload(exit_intent),
    )
    _commit_before_exit_venue_io(conn, stage="exit_intent")


def _exit_intent_audit_payload(exit_intent: ExitIntent) -> dict[str, object]:
    """Canonical EXIT_INTENT evidence captured before execution gates mutate state."""

    return {
        "exit_intent_reason": exit_intent.reason,
        "exit_intent_token_id": exit_intent.token_id,
        "exit_intent_shares": exit_intent.shares,
        "exit_intent_current_market_price": exit_intent.current_market_price,
        "exit_intent_best_bid": exit_intent.best_bid,
        "exit_intent_best_ask": exit_intent.best_ask,
        "exit_intent_market_vig": exit_intent.market_vig,
        "exit_intent_fresh_prob": exit_intent.fresh_prob,
        "exit_intent_fresh_prob_is_fresh": exit_intent.fresh_prob_is_fresh,
        "exit_intent_hours_to_settlement": exit_intent.hours_to_settlement,
        "exit_intent_position_state": exit_intent.position_state,
        "exit_intent_day0_active": exit_intent.day0_active,
    }


def _dual_write_canonical_pending_exit_if_available(
    conn: sqlite3.Connection | None,
    position: Position,
    *,
    reason: str,
    error: str,
    event_type: str = "EXIT_ORDER_REJECTED",
    extra_payload: dict[str, object] | None = None,
) -> bool:
    """Backwards-compat shim — routes to the canonical transition_phase writer.

    WAVE-3 Batch B (F108 reframe, 2026-05-18): the prior in-file
    implementation was promoted into src.state.db.transition_phase so the
    same single-writer property holds for both the 9 already-paired sites
    that called this shim AND the 4 freshly-paired helper sites that now
    call transition_phase directly. Behaviour identical: returns False on
    conn=None or any append-projection failure, True on success.
    """
    from src.state.db import transition_phase

    return transition_phase(
        conn,
        position,
        event_type=event_type,
        reason=reason,
        error=error,
        source_module="src.execution.exit_lifecycle",
        extra_payload=extra_payload,
    )


def _dual_write_canonical_admin_close_if_available(
    conn: sqlite3.Connection | None,
    position: Position,
    *,
    phase_before: str,
    reason: str,
    error: str,
) -> bool:
    if conn is None:
        return False
    try:
        import json as _json

        from src.engine.lifecycle_events import build_position_current_projection
        from src.state.db import append_many_and_project
        from src.state.lifecycle_manager import fold_lifecycle_phase

        trade_id = str(getattr(position, "trade_id", "") or "")
        if not trade_id:
            return False
        sequence_no = _next_canonical_sequence_no(conn, trade_id)
        occurred_at = getattr(position, "last_exit_at", "") or datetime.now(timezone.utc).isoformat()
        projection = build_position_current_projection(position)
        if projection.get("phase") != "admin_closed":
            return False
        projection["updated_at"] = occurred_at
        env = str(getattr(position, "env", "") or "live")
        if env not in {"live", "test", "replay", "backtest"}:
            env = "live"
        event = {
            "event_id": f"{trade_id}:admin_closed:{sequence_no}",
            "position_id": trade_id,
            "event_version": 1,
            "sequence_no": sequence_no,
            "event_type": "MANUAL_OVERRIDE_APPLIED",
            "occurred_at": occurred_at,
            "phase_before": phase_before,
            "phase_after": fold_lifecycle_phase(phase_before, "admin_closed").value,
            "strategy_key": str(
                getattr(position, "strategy_key", "")
                or getattr(position, "strategy", "")
                or ""
            ),
            "decision_id": None,
            "snapshot_id": getattr(position, "decision_snapshot_id", "") or None,
            "order_id": getattr(position, "last_exit_order_id", "") or getattr(position, "order_id", "") or None,
            "command_id": None,
            "caused_by": "exit_pending_chain_absent",
            "idempotency_key": f"{trade_id}:admin_closed:{sequence_no}",
            "venue_status": "admin_closed",
            "source_module": "src.execution.exit_lifecycle",
            "env": env,
            "payload_json": _json.dumps(
                {
                    "reason": reason,
                    "error": error,
                    "exit_state": getattr(position, "exit_state", ""),
                    "chain_state": getattr(position, "chain_state", ""),
                    "last_exit_order_id": getattr(position, "last_exit_order_id", ""),
                },
                default=str,
                sort_keys=True,
            ),
        }
        append_many_and_project(conn, [event], projection)
        return True
    except Exception as exc:
        raise RuntimeError(
            f"canonical admin-close dual-write failed for {getattr(position, 'trade_id', '?')}: {exc}"
        ) from exc


def execute_exit(
    portfolio: PortfolioState,
    position: Position,
    exit_context: ExitContext,
    clob=None,
    conn: sqlite3.Connection | None = None,
    exit_intent: ExitIntent | None = None,
) -> str:
    """Execute an exit decision. Returns outcome description.

    Live mode: place sell order, check fill, retry on failure.
    NEVER close a live position without confirmed fill.
    """
    is_red_force_exit = (
        getattr(position, "exit_reason", "") == "red_force_exit"
        or str(exit_context.exit_reason or "").upper() == "RED_FORCE_EXIT"
    )
    # PR-S1 Bug #3: block SELL for tokens with unresolved aggregate violations.
    _eff_token_id = (
        position.token_id if getattr(position, "direction", "") == "buy_yes"
        else getattr(position, "no_token_id", "") or position.token_id
    )
    if _eff_token_id:
        from src.engine.cycle_runtime import tokens_blocked_until_resolution, _tokens_blocked_lock
        with _tokens_blocked_lock:
            _is_blocked = _eff_token_id in tokens_blocked_until_resolution
        if _is_blocked:
            logger.warning(
                "TOKEN_AGGREGATE_BLOCKED_PENDING_RESOLUTION: trade_id=%s token=%s",
                position.trade_id,
                _eff_token_id,
            )
            return "exit_blocked: TOKEN_AGGREGATE_BLOCKED_PENDING_RESOLUTION"

    if exit_context.current_market_price is None:
        if (
            not is_red_force_exit
            and _exit_context_is_after_settlement_or_market_closed(exit_context)
        ):
            mark_market_closed_hold_to_settlement(
                position,
                reason=_market_closed_hold_reason_from_exit_context(exit_context),
                error="missing_current_market_price_after_settlement",
                conn=conn,
            )
            return "exit_blocked: market_closed_hold_to_settlement"
        retry_reason = f"{exit_context.exit_reason or 'EXIT'} [INCOMPLETE_CONTEXT]"
        _mark_exit_retry(position, reason=retry_reason, error="missing_current_market_price", conn=conn)
        return "exit_blocked: incomplete_context"
    if not is_red_force_exit and not exit_context.current_market_price_is_fresh:
        if _exit_context_is_after_settlement_or_market_closed(exit_context):
            mark_market_closed_hold_to_settlement(
                position,
                reason=_market_closed_hold_reason_from_exit_context(exit_context),
                error="stale_current_market_price_after_settlement",
                conn=conn,
            )
            return "exit_blocked: market_closed_hold_to_settlement"
        retry_reason = f"{exit_context.exit_reason or 'EXIT'} [STALE_MARKET_PRICE]"
        _mark_exit_retry(position, reason=retry_reason, error="stale_current_market_price", conn=conn)
        return "exit_blocked: stale_market_price"

    exit_intent = exit_intent or build_exit_intent(position, exit_context)
    _validate_exit_intent(position, exit_context, exit_intent)

    # Live path: sell order lifecycle
    return _execute_live_exit(
        portfolio,
        position,
        exit_context,
        exit_intent,
        clob,
        conn=conn,
    )


def _execute_live_exit(
    portfolio: PortfolioState,
    position: Position,
    exit_context: ExitContext,
    exit_intent: ExitIntent,
    clob,
    *,
    conn: sqlite3.Connection | None,
) -> str:
    """Live exit: place sell, check fill, retry on failure."""
    if conn is not None:
        from src.state.db import log_exit_attempt_event, log_exit_fill_event, log_exit_retry_event
        from src.state.db import log_pending_exit_recovery_event

    canonical_dust = _canonical_non_executable_dust_hold(position, conn=conn, now=_utcnow())
    if canonical_dust is not None:
        dust_reason, dust_error = canonical_dust
        _sync_runtime_to_canonical_dust_hold(
            position,
            reason=dust_reason,
            error=dust_error,
        )
        logger.info(
            "EXIT DUST HOLD %s already canonical; suppressing duplicate exit intent.",
            position.trade_id,
        )
        return f"sell_blocked_dust: existing_canonical_dust_hold: {dust_error or dust_reason}"

    _record_exit_intent_before_execution_gates(conn, position, exit_intent)

    token_id = exit_intent.token_id
    if not token_id:
        retry_reason = f"{exit_intent.reason} [NO_TOKEN_ID]"
        _mark_exit_retry(position, reason=retry_reason, error="no_token_id", conn=conn)
        if conn is not None:
            log_pending_exit_recovery_event(
                conn,
                position,
                event_type="EXIT_ORDER_REJECTED",
                reason=retry_reason,
                error="no_token_id",
            )
            log_exit_retry_event(conn, position, reason=retry_reason, error="no_token_id")
        return "exit_blocked: no_token_id"

    if not str(getattr(position, "last_exit_order_id", "") or ""):
        active_exit = _active_exit_sell_for_lock(
            conn,
            position,
            token_id=token_id,
            clob=clob,
        )
        if active_exit is not None:
            return _adopt_active_exit_sell(
                position,
                active_exit,
                conn=conn,
                reason=f"{exit_context.exit_reason} [ACTIVE_EXIT_SELL_IN_FLIGHT]",
            )

    try:
        snapshot_context = _latest_or_capture_exit_snapshot_context(
            conn,
            clob,
            position,
            token_id,
        )
    except Exception as exc:  # noqa: BLE001
        snapshot_reason = f"{exit_context.exit_reason} [EXECUTABLE_SNAPSHOT_ERROR]"
        snapshot_error = (
            f"exit_executable_snapshot_error:{type(exc).__name__}:{str(exc)[:400]}"
        )
        _mark_exit_retry(
            position,
            reason=snapshot_reason,
            error=snapshot_error,
            conn=conn,
        )
        if conn is not None:
            log_pending_exit_recovery_event(
                conn,
                position,
                event_type="EXIT_ORDER_REJECTED",
                reason=snapshot_reason,
                error=snapshot_error,
            )
            log_exit_retry_event(
                conn,
                position,
                reason=snapshot_reason,
                error=snapshot_error,
            )
        return "exit_blocked: executable_snapshot_error"
    dust_error = _below_snapshot_min_order_error(position, snapshot_context)
    if dust_error:
        dust_reason = f"{exit_context.exit_reason} [DUST: {dust_error}]"
        _mark_exit_dust_hold(
            position,
            reason=dust_reason,
            error=dust_error,
            conn=conn,
        )
        if conn is not None:
            log_pending_exit_recovery_event(
                conn,
                position,
                event_type="EXIT_ORDER_REJECTED",
                reason=dust_reason,
                error=dust_error,
            )
            log_exit_retry_event(conn, position, reason=dust_reason, error=dust_error)
        return f"sell_blocked_dust: {dust_error}"

    if conn is not None and not str(snapshot_context.get("executable_snapshot_id") or "").strip():
        snapshot_reason = f"{exit_context.exit_reason} [EXECUTABLE_SNAPSHOT_UNAVAILABLE]"
        snapshot_error = "exit_executable_snapshot_unavailable"
        _mark_exit_retry(
            position,
            reason=snapshot_reason,
            error=snapshot_error,
            conn=conn,
        )
        if conn is not None:
            log_pending_exit_recovery_event(
                conn,
                position,
                event_type="EXIT_ORDER_REJECTED",
                reason=snapshot_reason,
                error=snapshot_error,
            )
            log_exit_retry_event(
                conn,
                position,
                reason=snapshot_reason,
                error=snapshot_error,
            )
        return "exit_blocked: executable_snapshot_unavailable"

    liquidity_error = (
        _exit_no_executable_bid_error(exit_intent, snapshot_context)
        if conn is not None
        else ""
    )
    if liquidity_error:
        liquidity_reason = f"{exit_context.exit_reason} [NO_EXECUTABLE_BID]"
        _mark_exit_retry(
            position,
            reason=liquidity_reason,
            error=liquidity_error,
            conn=conn,
        )
        if conn is not None:
            log_pending_exit_recovery_event(
                conn,
                position,
                event_type="EXIT_ORDER_REJECTED",
                reason=liquidity_reason,
                error=liquidity_error,
            )
            log_exit_retry_event(conn, position, reason=liquidity_reason, error=liquidity_error)
        return "exit_blocked: no_executable_bid"

    if conn is not None:
        try:
            _refresh_exit_collateral_snapshot_for_submit(
                conn,
                token_id=token_id,
                shares=position.effective_shares,
            )
        except CollateralInsufficient as exc:
            collateral_reason = str(exc)
            if _is_exit_transient_lock_error(collateral_reason):
                active_exit = _active_exit_sell_for_lock(
                    conn,
                    position,
                    token_id=token_id,
                    clob=clob,
                )
                if active_exit is not None:
                    return _adopt_active_exit_sell(
                        position,
                        active_exit,
                        conn=conn,
                        reason=f"{exit_context.exit_reason} [ACTIVE_EXIT_SELL_LOCKED_COLLATERAL]",
                    )
            retry_reason = f"{exit_context.exit_reason} [COLLATERAL_REFRESH: {collateral_reason}]"
            _mark_exit_retry(
                position,
                reason=retry_reason,
                error=collateral_reason,
                conn=conn,
            )
            if conn is not None:
                log_pending_exit_recovery_event(
                    conn,
                    position,
                    event_type="EXIT_ORDER_REJECTED",
                    reason=retry_reason,
                    error=collateral_reason,
                )
                log_exit_retry_event(conn, position, reason=retry_reason, error=collateral_reason)
            return f"collateral_blocked: {collateral_reason}"
        _commit_before_exit_venue_io(conn, stage="collateral_refresh")

    # Pre-sell collateral check (fail-closed)
    can_sell, collateral_reason = check_sell_collateral(
        position.entry_price,
        position.effective_shares,
        clob,
        token_id=token_id,
        conn=conn,
    )
    if not can_sell:
        if _is_exit_transient_lock_error(collateral_reason or ""):
            active_exit = _active_exit_sell_for_lock(
                conn,
                position,
                token_id=token_id,
                clob=clob,
            )
            if active_exit is not None:
                return _adopt_active_exit_sell(
                    position,
                    active_exit,
                    conn=conn,
                    reason=f"{exit_context.exit_reason} [ACTIVE_EXIT_SELL_LOCKED_COLLATERAL]",
                )
        retry_reason = f"{exit_context.exit_reason} [COLLATERAL: {collateral_reason}]"
        _mark_exit_retry(
            position,
            reason=retry_reason,
            error=collateral_reason or "",
            conn=conn,
        )
        if conn is not None:
            log_pending_exit_recovery_event(
                conn,
                position,
                event_type="EXIT_ORDER_REJECTED",
                reason=retry_reason,
                error=collateral_reason or "",
            )
            log_exit_retry_event(conn, position, reason=retry_reason, error=collateral_reason or "")
        return f"collateral_blocked: {collateral_reason}"

    current_market_price = exit_intent.current_market_price
    best_bid = exit_intent.best_bid

    # Cancel stale sell order before retry.  M4: cancel uncertainty must not
    # fail open into a replacement sell.  When a command row is available, route
    # through the typed cancel parser so UNKNOWN becomes CANCEL_REPLACE_BLOCKED
    # and future M5 reconciliation owns any unblock.
    if position.last_exit_order_id and position.exit_retry_count > 0:
        cancel_fn = getattr(clob, "cancel_order", None)
        if not callable(cancel_fn):
            retry_reason = f"{exit_context.exit_reason} [CANCEL_UNAVAILABLE]"
            _mark_exit_retry(position, reason=retry_reason, error="cancel_order_unavailable", conn=conn)
            if conn is not None:
                log_pending_exit_recovery_event(
                    conn,
                    position,
                    event_type="EXIT_ORDER_REJECTED",
                    reason=retry_reason,
                    error="cancel_order_unavailable",
                )
                log_exit_retry_event(conn, position, reason=retry_reason, error="cancel_order_unavailable")
            return "exit_blocked: cancel_unavailable"
        if conn is not None:
            from src.execution.exit_safety import request_cancel_for_command

            row = conn.execute(
                """
                SELECT command_id
                  FROM venue_commands
                 WHERE venue_order_id = ?
                   AND position_id = ?
                   AND token_id = ?
                   AND intent_kind = 'EXIT'
                 ORDER BY updated_at DESC, created_at DESC
                 LIMIT 1
                """,
                (position.last_exit_order_id, position.trade_id, exit_intent.token_id),
            ).fetchone()
            if row is None:
                from src.execution.exit_safety import parse_cancel_response

                try:
                    outcome = parse_cancel_response(cancel_fn(position.last_exit_order_id))
                except Exception as exc:  # noqa: BLE001
                    retry_reason = f"{exit_context.exit_reason} [CANCEL_UNKNOWN: no_command_row]"
                    _mark_exit_retry(
                        position,
                        reason=retry_reason,
                        error=str(exc)[:500],
                        conn=conn,
                    )
                    log_pending_exit_recovery_event(
                        conn,
                        position,
                        event_type="EXIT_ORDER_REJECTED",
                        reason=retry_reason,
                        error=str(exc)[:500],
                    )
                    log_exit_retry_event(conn, position, reason=retry_reason, error=str(exc)[:500])
                    return "exit_blocked: cancel_unknown"
                if outcome.status != "CANCELED":
                    retry_reason = f"{exit_context.exit_reason} [CANCEL_{outcome.status}: no_command_row]"
                    _mark_exit_retry(
                        position,
                        reason=retry_reason,
                        error=outcome.reason or outcome.status,
                        conn=conn,
                    )
                    log_pending_exit_recovery_event(
                        conn,
                        position,
                        event_type="EXIT_ORDER_REJECTED",
                        reason=retry_reason,
                        error=outcome.reason or outcome.status,
                    )
                    log_exit_retry_event(conn, position, reason=retry_reason, error=outcome.reason or outcome.status)
                    return f"exit_blocked: cancel_{outcome.status.lower()}"
                position.last_exit_order_id = ""
                retry_reason = f"{exit_context.exit_reason} [CANCEL_ADOPTED_ORDER]"
                _mark_exit_retry(
                    position,
                    reason=retry_reason,
                    error="adopted_exit_order_cancelled",
                    cooldown_seconds=0,
                    conn=conn,
                )
                log_pending_exit_recovery_event(
                    conn,
                    position,
                    event_type="EXIT_ORDER_REJECTED",
                    reason=retry_reason,
                    error="adopted_exit_order_cancelled",
                )
                log_exit_retry_event(
                    conn,
                    position,
                    reason=retry_reason,
                    error="adopted_exit_order_cancelled",
                )
                return "exit_retry: adopted_order_cancelled"
            outcome = request_cancel_for_command(
                conn,
                str(row["command_id"]),
                lambda order_id: cancel_fn(order_id),
            )
            if outcome.status != "CANCELED":
                retry_reason = f"{exit_context.exit_reason} [CANCEL_{outcome.status}]"
                _mark_exit_retry(position, reason=retry_reason, error=outcome.reason or outcome.status, conn=conn)
                log_pending_exit_recovery_event(
                    conn,
                    position,
                    event_type="EXIT_ORDER_REJECTED",
                    reason=retry_reason,
                    error=outcome.reason or outcome.status,
                )
                log_exit_retry_event(conn, position, reason=retry_reason, error=outcome.reason or outcome.status)
                return f"exit_blocked: cancel_{outcome.status.lower()}"
        else:
            from src.execution.exit_safety import parse_cancel_response

            try:
                outcome = parse_cancel_response(cancel_fn(position.last_exit_order_id))
            except Exception as exc:
                logger.warning("Stale sell cancel unknown for %s: %s", position.trade_id, exc)
                _mark_exit_retry(position, reason=f"{exit_context.exit_reason} [CANCEL_UNKNOWN]", error=str(exc)[:500], conn=conn)
                return "exit_blocked: cancel_unknown"
            if outcome.status != "CANCELED":
                _mark_exit_retry(position, reason=f"{exit_context.exit_reason} [CANCEL_{outcome.status}]", error=outcome.reason or outcome.status, conn=conn)
                return f"exit_blocked: cancel_{outcome.status.lower()}"

    try:
        raw_sell_result = place_sell_order(
            trade_id=position.trade_id,
            token_id=token_id,
            shares=position.effective_shares,
            current_price=current_market_price,
            best_bid=best_bid,
            decision_id=f"exit:{position.trade_id}",
            **snapshot_context,
        )
        sell_result = _coerce_sell_result(position.trade_id, raw_sell_result)

        if sell_result.status == "rejected":
            sell_error = sell_result.reason or "sell_rejected"
            if _is_exit_transient_lock_error(sell_error):
                active_exit = _active_exit_sell_for_lock(
                    conn,
                    position,
                    token_id=token_id,
                    clob=clob,
                )
                if active_exit is not None:
                    return _adopt_active_exit_sell(
                        position,
                        active_exit,
                        conn=conn,
                        reason=f"{exit_context.exit_reason} [ACTIVE_EXIT_SELL_LOCKED_SUBMIT]",
                    )
            if _is_below_min_order_sell_error(sell_error):
                dust_reason = f"{exit_context.exit_reason} [DUST: {sell_error}]"
                _mark_exit_dust_hold(
                    position,
                    reason=dust_reason,
                    error=sell_error,
                    conn=conn,
                )
                if conn is not None:
                    log_pending_exit_recovery_event(
                        conn,
                        position,
                        event_type="EXIT_ORDER_REJECTED",
                        reason=dust_reason,
                        error=sell_error,
                    )
                    log_exit_retry_event(conn, position, reason=dust_reason, error=sell_error)
                return f"sell_blocked_dust: {sell_error}"
            retry_reason = f"{exit_context.exit_reason} [SELL_ERROR: {sell_error}]"
            _mark_exit_retry(
                position,
                reason=retry_reason,
                error=sell_error,
                conn=conn,
            )
            if conn is not None:
                log_pending_exit_recovery_event(
                    conn,
                    position,
                    event_type="EXIT_ORDER_REJECTED",
                    reason=retry_reason,
                    error=sell_error,
                )
                log_exit_retry_event(conn, position, reason=retry_reason, error=sell_error)
            return f"sell_error: {sell_error}"

        order_id = sell_result.external_order_id or sell_result.order_id or ""
        position.last_exit_order_id = order_id
        position.exit_state = "sell_placed"
        position.order_status = "sell_placed"
        if conn is not None:
            # FIX 2d (2026-06-20): canonical EXIT_ORDER_POSTED dual-write.
            # log_pending_exit_recovery_event below only writes the legacy
            # execution_fact row; it does NOT append a canonical
            # position_events.EXIT_ORDER_POSTED. Before this fix every
            # canonical EXIT_ORDER_POSTED row carried
            # source_module=command_recovery (5/5), so the live spine
            # emitter's own posts were invisible to the canonical audit and
            # RANK 2 could not be graded on the event store. Stamp the
            # canonical post here (source_module=src.execution.exit_lifecycle)
            # while the position is still phase=pending_exit / sell_placed so
            # transition_phase's projection resolves correctly.
            _dual_write_canonical_pending_exit_if_available(
                conn,
                position,
                reason=exit_intent.reason or "EXIT_ORDER_POSTED",
                error="",
                event_type="EXIT_ORDER_POSTED",
            )
            log_pending_exit_recovery_event(
                conn,
                position,
                event_type="EXIT_ORDER_POSTED",
                reason=exit_intent.reason,
                error="",
            )
            log_exit_attempt_event(
                conn,
                position,
                order_id=order_id,
                status="placed",
                current_market_price=current_market_price,
                best_bid=best_bid,
                shares=exit_intent.shares,
                details={
                    "token_id": token_id,
                    "semantic_event": "EXIT_ORDER_POSTED",
                    "sell_result": _serialize_sell_result(sell_result),
                },
            )

        # Quick fill check (non-blocking — next cycle does full check)
        if order_id and clob:
            status, status_payload = _check_order_fill(clob, order_id)
            if status in FILL_STATUSES:
                actual_price = _extract_fill_price(status_payload)
                if actual_price is None:
                    _mark_exit_fill_economics_missing(
                        position,
                        status=status,
                        order_id=order_id,
                        conn=conn,
                    )
                    return f"sell_pending: order={order_id}, status={status}, missing_fill_price"
                phase_before = _canonical_phase_before_for_economic_close(position)
                closed = compute_economic_close(portfolio, position.trade_id, actual_price, exit_context.exit_reason)
                if closed is not None:
                    closed.exit_state = "sell_filled"
                    _dual_write_canonical_economic_close_if_available(
                        conn,
                        closed,
                        phase_before=phase_before,
                    )
                    if conn is not None:
                        log_exit_fill_event(
                            conn,
                            closed,
                            order_id=order_id,
                            fill_price=actual_price,
                            current_market_price=current_market_price,
                            best_bid=best_bid,
                            timestamp=getattr(closed, "last_exit_at", None),
                        )
                    # Slice P5-1 (PR #19 closeout completion, 2026-04-26):
                    # construct typed RealizedFill at the fill-receipt seam.
                    # P3.3 commit message promised this; P3.3b delivered the
                    # planning-side SlippageBps wrap; P5-1 closes the receipt
                    # half. The construction is the structural value: any
                    # invalid price pair raises at __post_init__ before
                    # downstream attribution can consume bad data. DEBUG log
                    # surfaces typed slippage for ops audit.
                    _emit_typed_realized_fill(
                        actual_price=actual_price,
                        expected_price=current_market_price,
                        side="sell",
                        shares=getattr(closed, "shares", 0.0),
                        trade_id=getattr(closed, "trade_id", ""),
                    )
                return f"exit_filled: {exit_context.exit_reason}"
            else:
                # Not filled yet — will be checked next cycle
                position.exit_state = "sell_pending"
                position.order_status = "sell_pending"
                if conn is not None:
                    log_exit_attempt_event(
                        conn,
                        position,
                        order_id=order_id,
                        status=status or "pending",
                        current_market_price=current_market_price,
                        best_bid=best_bid,
                        shares=exit_intent.shares,
                        details={"semantic_event": "EXIT_ORDER_POSTED"},
                    )
                return f"sell_pending: order={order_id}, status={status}"

        position.exit_state = "sell_pending"
        position.order_status = "sell_pending"
        if conn is not None:
            log_exit_attempt_event(
                conn,
                position,
                order_id=order_id,
                status="pending",
                current_market_price=current_market_price,
                best_bid=best_bid,
                shares=exit_intent.shares,
                details={"semantic_event": "EXIT_ORDER_POSTED"},
            )
        return f"sell_placed: order={order_id}"

    except Exception as exc:
        # API error — retry next cycle, NEVER close
        retry_reason = f"{exit_context.exit_reason} [ERROR]"
        retry_error = str(exc)[:500]
        _mark_exit_retry(
            position,
            reason=retry_reason,
            error=retry_error,
            conn=conn,
        )
        if conn is not None:
            log_pending_exit_recovery_event(
                conn,
                position,
                event_type="EXIT_ORDER_REJECTED",
                reason=retry_reason,
                error=retry_error,
            )
            log_exit_retry_event(conn, position, reason=retry_reason, error=retry_error)
        return f"sell_exception: {exc}"


def _latest_exit_snapshot_context(
    conn: sqlite3.Connection | None,
    token_id: str,
    *,
    now: datetime | None = None,
    require_sell_bid: bool = True,
) -> dict[str, object]:
    """Return executor snapshot kwargs for the latest fresh snapshot by token.

    M4 exit lifecycle is upstream of executor's U1 snapshot gate.  When a DB
    connection is available, use the latest non-expired executable-market
    snapshot for the token being sold so lifecycle exits cite the same CLOB
    truth as direct executor exits.  Missing/failed lookup deliberately returns
    an empty dict; executor then fails closed with the existing
    ``executable_snapshot_gate`` rejection instead of bypassing U1.
    """

    if conn is None or not token_id:
        return {}
    now_s = (now or _utcnow()).isoformat()
    saved = conn.row_factory
    conn.row_factory = sqlite3.Row
    try:
        bid_filter = (
            """
               AND orderbook_top_bid IS NOT NULL
               AND TRIM(CAST(orderbook_top_bid AS TEXT)) != ''
               AND UPPER(TRIM(CAST(orderbook_top_bid AS TEXT))) != 'ABSENT'
            """
            if require_sell_bid
            else ""
        )
        row = conn.execute(
            f"""
            SELECT snapshot_id, min_tick_size, min_order_size, neg_risk,
                   orderbook_top_bid, orderbook_top_ask
              FROM executable_market_snapshots
             WHERE freshness_deadline >= ?
               AND selected_outcome_token_id = ?
               {bid_filter}
             ORDER BY captured_at DESC, snapshot_id DESC
             LIMIT 1
            """,
            (now_s, token_id),
        ).fetchone()
    except sqlite3.OperationalError:
        row = None
    finally:
        conn.row_factory = saved
    if row is None:
        return {}
    from src.state.snapshot_repo import get_snapshot

    snapshot_id = str(row["snapshot_id"])
    snapshot = get_snapshot(conn, snapshot_id)
    snapshot_hash = str(snapshot.executable_snapshot_hash or "") if snapshot is not None else ""
    return {
        "executable_snapshot_id": snapshot_id,
        "executable_snapshot_hash": snapshot_hash,
        "executable_snapshot_min_tick_size": str(row["min_tick_size"]),
        "executable_snapshot_min_order_size": str(row["min_order_size"]),
        "executable_snapshot_neg_risk": bool(row["neg_risk"]),
        "executable_snapshot_orderbook_top_bid": str(row["orderbook_top_bid"]),
        "executable_snapshot_orderbook_top_ask": str(row["orderbook_top_ask"]),
    }


def _latest_exit_snapshot_identity_seed(
    conn: sqlite3.Connection | None,
    token_id: str,
) -> dict[str, object]:
    """Return durable token identity from the latest executable snapshot.

    The snapshot may be price-stale; use it only to seed immutable market
    identity before capturing a fresh exit snapshot from current CLOB facts.
    """

    if conn is None or not token_id:
        return {}
    saved = conn.row_factory
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            """
            SELECT gamma_market_id, event_id, event_slug, condition_id, question_id,
                   yes_token_id, no_token_id, selected_outcome_token_id, outcome_label,
                   market_start_at, market_end_at, market_close_at, sports_start_at,
                   raw_gamma_payload_hash, captured_at
              FROM executable_market_snapshots
             WHERE selected_outcome_token_id = ?
                OR yes_token_id = ?
                OR no_token_id = ?
             ORDER BY captured_at DESC, snapshot_id DESC
             LIMIT 1
            """,
            (token_id, token_id, token_id),
        ).fetchone()
    except sqlite3.OperationalError:
        row = None
    finally:
        conn.row_factory = saved
    if row is None:
        return {}

    yes_token = str(row["yes_token_id"] or "")
    no_token = str(row["no_token_id"] or "")
    condition_id = str(row["condition_id"] or "")
    question_id = str(row["question_id"] or "")
    if not yes_token or not no_token or not condition_id or not question_id:
        return {}

    gamma_raw = {
        "id": str(row["gamma_market_id"] or condition_id),
        "conditionId": condition_id,
        "questionID": question_id,
        "clobTokenIds": [yes_token, no_token],
    }
    return {
        "market_id": condition_id,
        "condition_id": condition_id,
        "question_id": question_id,
        "gamma_market_id": str(row["gamma_market_id"] or condition_id),
        "event_id": str(row["event_id"] or ""),
        "event_slug": str(row["event_slug"] or ""),
        "token_id": yes_token,
        "no_token_id": no_token,
        "title": str(row["outcome_label"] or ""),
        "market_start_at": row["market_start_at"],
        "market_end_at": row["market_end_at"],
        "market_close_at": row["market_close_at"],
        "sports_start_at": row["sports_start_at"],
        "token_map_raw": {"YES": yes_token, "NO": no_token},
        "raw_gamma_payload_hash": str(row["raw_gamma_payload_hash"] or ""),
        "gamma_market_raw": gamma_raw,
        "source_contract": {
            "status": "MATCH",
            "source": "executable_market_snapshots_identity_seed",
            "captured_at": row["captured_at"],
        },
    }


def _outcome_has_executable_identity(outcome: object) -> bool:
    if not isinstance(outcome, Mapping):
        return False
    return all(
        str(outcome.get(key) or "").strip()
        for key in ("condition_id", "question_id", "token_id", "no_token_id")
    )


def _outcome_matches_exit_identity_seed(
    outcome: Mapping[str, object],
    identity_seed: Mapping[str, object],
    token_id: str,
    *,
    single_outcome: bool,
) -> bool:
    if single_outcome:
        return True
    seed_condition = str(identity_seed.get("condition_id") or "")
    values = {
        str(value)
        for value in (
            outcome.get("market_id"),
            outcome.get("condition_id"),
            outcome.get("token_id"),
            outcome.get("no_token_id"),
        )
        if value not in (None, "")
    }
    return bool(values & {str(token_id), seed_condition})


def _merge_current_outcome_with_exit_identity_seed(
    outcome: Mapping[str, object],
    identity_seed: Mapping[str, object],
) -> dict[str, object]:
    """Fill missing immutable identity without importing stale tradability facts."""

    merged = dict(outcome)
    for key in (
        "market_id",
        "condition_id",
        "question_id",
        "gamma_market_id",
        "event_id",
        "event_slug",
        "token_id",
        "no_token_id",
        "title",
        "market_start_at",
        "market_end_at",
        "market_close_at",
        "sports_start_at",
        "raw_gamma_payload_hash",
    ):
        if merged.get(key) in (None, "") and identity_seed.get(key) not in (None, ""):
            merged[key] = identity_seed[key]

    if not isinstance(merged.get("token_map_raw"), Mapping):
        token_map = identity_seed.get("token_map_raw")
        if isinstance(token_map, Mapping):
            merged["token_map_raw"] = dict(token_map)

    current_raw = merged.get("gamma_market_raw")
    gamma_raw = dict(current_raw) if isinstance(current_raw, Mapping) else {}
    seed_raw = identity_seed.get("gamma_market_raw")
    if isinstance(seed_raw, Mapping):
        for key in ("id", "conditionId", "questionID", "clobTokenIds"):
            if gamma_raw.get(key) in (None, "") and seed_raw.get(key) not in (None, ""):
                gamma_raw[key] = seed_raw[key]
    has_current_tradability = any(
        _field_present(source, key)
        for source in (merged, gamma_raw)
        for key in (
            "accepting_orders",
            "acceptingOrders",
            "enable_orderbook",
            "enableOrderBook",
            "orderbookEnabled",
        )
    )
    if not has_current_tradability:
        gamma_raw["tradability_authority"] = "persisted_snapshot_reconstruction"
    if gamma_raw:
        merged["gamma_market_raw"] = gamma_raw

    merged["source_contract"] = {
        "status": "MATCH",
        "source": "executable_market_snapshots_identity_seed",
        "captured_at": identity_seed.get("source_contract", {}).get("captured_at")
        if isinstance(identity_seed.get("source_contract"), Mapping)
        else None,
    }
    return merged


def _field_present(source: Mapping[str, object], key: str) -> bool:
    return key in source and source.get(key) not in (None, "")


def _seed_exit_snapshot_identity(
    siblings: list[object],
    identity_seed: Mapping[str, object],
    token_id: str,
) -> list[object]:
    mapping_siblings = [outcome for outcome in siblings if isinstance(outcome, Mapping)]
    if not mapping_siblings:
        return [dict(identity_seed)]

    seeded: list[object] = []
    applied = False
    single_outcome = len(mapping_siblings) == 1
    for outcome in siblings:
        if not isinstance(outcome, Mapping):
            seeded.append(outcome)
            continue
        if _outcome_matches_exit_identity_seed(
            outcome,
            identity_seed,
            token_id,
            single_outcome=single_outcome,
        ):
            seeded.append(_merge_current_outcome_with_exit_identity_seed(outcome, identity_seed))
            applied = True
        else:
            seeded.append(outcome)
    return seeded if applied else siblings


def _latest_or_capture_exit_snapshot_context(
    conn: sqlite3.Connection | None,
    clob,
    position: Position,
    token_id: str,
    *,
    now: datetime | None = None,
) -> dict[str, object]:
    """Return fresh snapshot kwargs for exits, capturing one when possible.

    Held positions can outlive entry snapshot freshness.  Before a live sell
    reaches the executor's U1 gate, refresh executable market facts from the
    current VERIFIED Gamma sibling set plus fresh CLOB market/orderbook/fee
    facts.  If any authority link is unavailable, return an empty context so
    executor rejects through the existing executable_snapshot_gate.
    """

    context = _latest_exit_snapshot_context(conn, token_id, now=now)
    if context:
        return context
    no_bid_context = _latest_exit_snapshot_context(
        conn,
        token_id,
        now=now,
        require_sell_bid=False,
    )
    if conn is None or clob is None or not token_id:
        return no_bid_context

    market_id = str(
        getattr(position, "market_id", "")
        or getattr(position, "condition_id", "")
        or ""
    ).strip()
    yes_token = str(getattr(position, "token_id", "") or "").strip()
    no_token = str(getattr(position, "no_token_id", "") or "").strip()
    identity_seed: dict[str, object] = {}
    if market_id and (not yes_token or not no_token):
        identity_seed = _latest_exit_snapshot_identity_seed(conn, token_id)
        yes_token = yes_token or str(identity_seed.get("token_id") or "").strip()
        no_token = no_token or str(identity_seed.get("no_token_id") or "").strip()
        market_id = market_id or str(identity_seed.get("condition_id") or "").strip()
    if not market_id or not yes_token or not no_token:
        return no_bid_context

    try:
        from src.data.market_scanner import (
            capture_executable_market_snapshot,
            get_last_scan_authority,
            get_sibling_outcomes,
        )

        siblings = get_sibling_outcomes(market_id)
        scan_authority = get_last_scan_authority()
        if str(scan_authority).strip().upper() != "VERIFIED":
            logger.warning(
                "Exit executable snapshot capture blocked for %s: scan_authority=%s",
                position.trade_id,
                scan_authority,
            )
            return no_bid_context
        if not siblings:
            logger.warning(
                "Exit executable snapshot capture blocked for %s: no Gamma siblings for market_id=%s",
                position.trade_id,
                market_id,
            )
            return no_bid_context
        if not any(_outcome_has_executable_identity(outcome) for outcome in siblings):
            if not identity_seed:
                identity_seed = _latest_exit_snapshot_identity_seed(conn, token_id)
            if identity_seed:
                siblings = _seed_exit_snapshot_identity(siblings, identity_seed, token_id)

        raw_direction = getattr(position, "direction", "")
        direction = str(getattr(raw_direction, "value", raw_direction))
        decision_stub = SimpleNamespace(
            tokens={
                "market_id": market_id,
                "token_id": yes_token,
                "no_token_id": no_token,
            },
            edge=SimpleNamespace(direction=direction),
        )
        captured_at = now or _utcnow()
        fields = capture_executable_market_snapshot(
            conn,
            market={
                "event_id": f"exit-refresh:{market_id}",
                "slug": f"exit-refresh:{market_id}",
                "outcomes": siblings,
            },
            decision=decision_stub,
            clob=clob,
            captured_at=captured_at,
            scan_authority=scan_authority,
            execution_side="SELL",
        )
        # The executor opens its own DB handle through place_sell_order(); make
        # the snapshot durable before any submit-side effect can observe it.
        conn.commit()
        snapshot_id = str(fields.get("executable_snapshot_id") or "")
        if not snapshot_id:
            logger.warning(
                "Exit executable snapshot capture returned no snapshot_id for %s token=%s",
                position.trade_id,
                token_id,
            )
            return no_bid_context
        refreshed_context = _latest_exit_snapshot_context(
            conn,
            token_id,
            now=captured_at,
        )
        if refreshed_context:
            return refreshed_context
        refreshed_no_bid_context = _latest_exit_snapshot_context(
            conn,
            token_id,
            now=captured_at,
            require_sell_bid=False,
        )
        if refreshed_no_bid_context:
            return refreshed_no_bid_context
        from src.state.snapshot_repo import get_snapshot

        snapshot = get_snapshot(conn, snapshot_id)
        snapshot_hash = str(snapshot.executable_snapshot_hash or "") if snapshot is not None else ""
        return {
            "executable_snapshot_id": snapshot_id,
            "executable_snapshot_hash": snapshot_hash,
            "executable_snapshot_min_tick_size": fields.get("executable_snapshot_min_tick_size"),
            "executable_snapshot_min_order_size": fields.get("executable_snapshot_min_order_size"),
            "executable_snapshot_neg_risk": fields.get("executable_snapshot_neg_risk"),
        }
    except Exception as exc:
        logger.warning(
            "Exit executable snapshot capture failed for %s token=%s: %s",
            position.trade_id,
            token_id,
            exc,
        )
        return no_bid_context


def _payload_decimal(payload: object, *keys: str) -> Decimal | None:
    if not isinstance(payload, dict):
        return None
    for key in keys:
        value = payload.get(key)
        if value in (None, ""):
            continue
        try:
            decimal = Decimal(str(value))
        except (InvalidOperation, ValueError):
            continue
        if decimal.is_finite():
            return decimal
    return None


def _payload_has_invalid_decimal(payload: object, *keys: str) -> bool:
    if not isinstance(payload, dict):
        return False
    for key in keys:
        if key not in payload:
            continue
        value = payload.get(key)
        if value in (None, ""):
            continue
        try:
            decimal = Decimal(str(value))
        except (InvalidOperation, ValueError):
            return True
        if not decimal.is_finite():
            return True
    return False


def _partial_exit_delta(
    *,
    status: str,
    payload: object,
    current_open_shares: float,
) -> tuple[float, float] | None:
    """Return (newly_filled_shares, remaining_shares) for a partial exit fill."""

    remaining_keys = ("remaining_size", "remainingSize", "remaining", "open_size", "openSize")
    cumulative_keys = (
        "filled_size",
        "filledSize",
        "matched_size",
        "matchedSize",
        "filled",
        "matched",
    )
    if _payload_has_invalid_decimal(payload, *remaining_keys, *cumulative_keys):
        return None
    open_shares = Decimal(str(max(0.0, float(current_open_shares))))
    if open_shares <= 0:
        return None
    remaining = _payload_decimal(payload, *remaining_keys)
    cumulative_filled = _payload_decimal(payload, *cumulative_keys)
    if remaining is None and cumulative_filled is not None:
        remaining = open_shares - cumulative_filled
    if remaining is None:
        return None
    remaining = max(Decimal("0"), remaining)
    if remaining <= 0 or remaining >= open_shares:
        return None
    if (
        status not in PARTIAL_FILL_STATUSES
        and status not in VOID_STATUSES
        and cumulative_filled in (None, Decimal("0"))
    ):
        return None
    newly_filled = open_shares - remaining
    if newly_filled <= 0:
        return None
    return float(newly_filled), float(remaining)


def _apply_partial_exit_fill(
    position: Position,
    *,
    filled_shares: float,
    remaining_shares: float,
    fill_price: float,
    order_id: str,
    status: str,
) -> bool:
    """Reduce local open exposure after an observed partial exit fill.

    This is not a full economic close. It keeps the active position's exposure
    aligned to the remaining CTF shares while recording the realized partial
    slice in nested_fills for audit/replay.
    """

    open_shares = float(position.effective_shares)
    if open_shares <= 0 or remaining_shares < 0 or remaining_shares >= open_shares:
        return False
    filled_shares = max(0.0, min(float(filled_shares), open_shares))
    remaining_shares = max(0.0, min(float(remaining_shares), open_shares))
    filled_ratio = filled_shares / open_shares
    remaining_ratio = remaining_shares / open_shares
    original_size = float(position.size_usd or 0.0)
    original_cost = float(position.effective_cost_basis_usd or 0.0)
    realized_cost = original_cost * filled_ratio
    realized_pnl = round(filled_shares * float(fill_price) - realized_cost, 2)
    position.nested_fills.append(
        {
            "type": "partial_exit_fill",
            "order_id": order_id,
            "status": status,
            "filled_shares": filled_shares,
            "remaining_shares": remaining_shares,
            "fill_price": float(fill_price),
            "realized_cost_basis_usd": realized_cost,
            "realized_pnl": realized_pnl,
            "observed_at": _utcnow().isoformat(),
        }
    )
    position.shares = remaining_shares
    position.size_usd = original_size * remaining_ratio
    if position.cost_basis_usd > 0:
        position.cost_basis_usd = original_cost * remaining_ratio
    # F1 (PR1 critic SEV-1): balance-only positions route effective_shares via
    # chain_shares.  Without this block, effective_exposure() returns stale
    # pre-exit chain aggregate until the next reconcile cycle — exit-sizing code
    # that calls effective_exposure() between cycles would overstate exposure and
    # re-issue exit orders the venue rejects.
    if position.has_chain_observed_authority:
        original_chain_shares = float(getattr(position, "chain_shares", 0.0) or 0.0)
        original_chain_cost = float(getattr(position, "chain_cost_basis_usd", 0.0) or 0.0)
        if original_chain_shares > 0:
            position.chain_shares = original_chain_shares * remaining_ratio
        if original_chain_cost > 0:
            position.chain_cost_basis_usd = original_chain_cost * remaining_ratio
    position.exit_state = "sell_pending"
    return True


def _log_partial_exit_execution_fact(
    conn: sqlite3.Connection,
    position: Position,
    *,
    status: str,
    fill_price: float,
    filled_shares: float,
    order_id: str,
) -> None:
    from src.state.db import log_execution_fact

    log_execution_fact(
        conn,
        intent_id=f"{getattr(position, 'trade_id', '')}:exit",
        position_id=getattr(position, "trade_id", ""),
        order_role="exit",
        strategy_key=str(
            getattr(position, "strategy_key", "")
            or getattr(position, "strategy", "")
            or ""
        )
        or None,
        filled_at=_utcnow().isoformat(),
        fill_price=fill_price,
        shares=filled_shares,
        venue_status=status or "PARTIAL",
        terminal_exec_status=status or "PARTIAL",
        command_id=_exit_command_id_for_order(conn, position, order_id),
    )


def _dual_write_partial_exit_projection_if_available(
    conn: sqlite3.Connection | None,
    position: Position,
    *,
    filled_shares: float,
    remaining_shares: float,
    fill_price: float,
    order_id: str,
    status: str,
) -> bool:
    """Persist the reduced open exposure after a partial exit fill."""

    if conn is None:
        return False
    try:
        import json as _json

        from src.engine.lifecycle_events import build_monitor_refreshed_canonical_write
        from src.state.db import append_many_and_project

        trade_id = str(getattr(position, "trade_id", "") or "")
        if not trade_id:
            return False
        sequence_no = _next_canonical_sequence_no(conn, trade_id)
        occurred_at = _utcnow().isoformat()
        if not str(getattr(position, "last_monitor_at", "") or "").strip():
            position.last_monitor_at = occurred_at
        env = str(getattr(position, "env", "") or "live")
        if env not in {"live", "test", "replay", "backtest"}:
            position.env = "live"
        events, projection = build_monitor_refreshed_canonical_write(
            position,
            sequence_no=sequence_no,
            phase_after="pending_exit",
            source_module="src.execution.exit_lifecycle",
        )
        if not events:
            return False
        event = dict(events[0])
        payload = _json.loads(str(event.get("payload_json") or "{}"))
        payload.update(
            {
                "semantic_event": "PARTIAL_FILL_OBSERVED",
                "order_id": order_id,
                "venue_status": status or "PARTIAL",
                "filled_shares": filled_shares,
                "remaining_shares": remaining_shares,
                "fill_price": fill_price,
            }
        )
        event["event_id"] = f"{trade_id}:partial_exit_fill:{sequence_no}"
        event["caused_by"] = "partial_exit_fill"
        event["occurred_at"] = occurred_at
        event["order_id"] = order_id or None
        event["venue_status"] = status or "PARTIAL"
        event["payload_json"] = _json.dumps(payload, default=str, sort_keys=True)
        projection["updated_at"] = occurred_at
        append_many_and_project(conn, [event], projection)
        return True
    except Exception:  # noqa: BLE001 - partial-fill projection must not hide venue facts
        logger.exception(
            "PARTIAL_EXIT_PROJECTION_WRITE_FAILED position_id=%s order_id=%s",
            getattr(position, "trade_id", ""),
            order_id,
        )
        return False


def _exit_command_id_for_order(
    conn: sqlite3.Connection,
    position: Position,
    order_id: str,
) -> str | None:
    if not order_id:
        return None
    try:
        row = conn.execute(
            """
            SELECT command_id
              FROM venue_commands
             WHERE venue_order_id = ?
               AND position_id = ?
               AND intent_kind = 'EXIT'
             ORDER BY updated_at DESC, created_at DESC
             LIMIT 1
            """,
            (order_id, getattr(position, "trade_id", "")),
        ).fetchone()
        if row is None:
            row = conn.execute(
                """
                SELECT command_id
                  FROM venue_commands
                 WHERE venue_order_id = ?
                   AND intent_kind = 'EXIT'
                 ORDER BY updated_at DESC, created_at DESC
                 LIMIT 1
                """,
                (order_id,),
            ).fetchone()
    except sqlite3.OperationalError:
        return None
    if row is None:
        return None
    return str(row["command_id"] if isinstance(row, sqlite3.Row) else row[0]) or None


def _last_exit_order_id(
    position: Position,
    *,
    conn: sqlite3.Connection | None = None,
) -> str:
    explicit = str(getattr(position, "last_exit_order_id", "") or "").strip()
    if explicit:
        return explicit

    # Legacy rows sometimes keep the ENTRY venue order in ``position.order_id`` even
    # after the position moves to pending_exit. Treating that entry id as a sell order
    # makes pending-exit recovery poll a filled BUY forever instead of retrying the
    # missing exit. Only accept the fallback when durable command truth proves it is an
    # EXIT command for this position, or when no DB is available and the runtime status
    # is explicitly sell-scoped.
    if conn is not None:
        trade_id = str(getattr(position, "trade_id", "") or "").strip()
        if not trade_id:
            return ""
        fallback = str(getattr(position, "order_id", "") or "").strip()
        candidates: list[str] = []
        if fallback:
            candidates.append(fallback)
        try:
            row = conn.execute(
                """
                SELECT order_id
                  FROM position_current
                 WHERE position_id = ?
                   AND COALESCE(order_status, '') LIKE 'sell_%'
                   AND COALESCE(order_id, '') <> ''
                 LIMIT 1
                """,
                (trade_id,),
            ).fetchone()
            current_order_id = str(row[0] if row is not None else "").strip()
            if current_order_id:
                candidates.append(current_order_id)
        except sqlite3.OperationalError:
            pass
        try:
            rows = conn.execute(
                """
                SELECT order_id
                  FROM position_events
                 WHERE position_id = ?
                   AND event_type = 'EXIT_ORDER_POSTED'
                   AND phase_after = 'pending_exit'
                   AND COALESCE(order_id, '') <> ''
                 ORDER BY sequence_no DESC, occurred_at DESC
                 LIMIT 3
                """,
                (trade_id,),
            ).fetchall()
            for row in rows:
                event_order_id = str(row[0] if row is not None else "").strip()
                if event_order_id:
                    candidates.append(event_order_id)
        except sqlite3.OperationalError:
            pass
        seen: set[str] = set()
        candidates = [candidate for candidate in candidates if not (candidate in seen or seen.add(candidate))]
        if not candidates:
            return ""
        try:
            placeholders = ", ".join("?" for _ in candidates)
            rows = conn.execute(
                f"""
                SELECT venue_order_id
                  FROM venue_commands
                 WHERE position_id = ?
                   AND intent_kind = 'EXIT'
                   AND venue_order_id IN ({placeholders})
                """,
                (trade_id, *candidates),
            ).fetchall()
            command_order_ids = {str(row[0] if row is not None else "") for row in rows}
        except sqlite3.OperationalError:
            command_order_ids = set()
        try:
            rows = conn.execute(
                f"""
                SELECT order_id
                  FROM position_events
                 WHERE position_id = ?
                   AND event_type = 'EXIT_ORDER_POSTED'
                   AND phase_after = 'pending_exit'
                   AND order_id IN ({placeholders})
                """,
                (trade_id, *candidates),
            ).fetchall()
            event_order_ids = {str(row[0] if row is not None else "") for row in rows}
        except sqlite3.OperationalError:
            event_order_ids = set()
        for candidate in candidates:
            if candidate in command_order_ids:
                return candidate
            try:
                retry_count = int(getattr(position, "exit_retry_count", 0) or 0)
            except (TypeError, ValueError):
                retry_count = 0
            if retry_count <= 0 and candidate in event_order_ids:
                return candidate
        return ""

    fallback = str(getattr(position, "order_id", "") or "").strip()
    if not fallback:
        return ""
    order_status = str(getattr(position, "order_status", "") or "").strip().lower()
    return fallback if order_status.startswith("sell_") else ""


def _canonical_exit_trade_fact_cte(cte_name: str = "canonical_exit_trade_fact") -> str:
    """Rank duplicate trade facts so weaker later rows cannot hide a fill."""

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


def _economic_exit_trade_fact_cte(
    *,
    canonical_cte_name: str = "canonical_exit_trade_fact",
    cte_name: str = "economic_exit_trade_fact",
) -> str:
    """Exclude a tx-hash alias once an exact child trade fact exists."""

    return f"""
        {cte_name} AS (
            SELECT fact.*
              FROM {canonical_cte_name} fact
             WHERE NOT (
                    TRIM(COALESCE(fact.tx_hash, '')) != ''
                AND LOWER(TRIM(COALESCE(fact.trade_id, '')))
                    = LOWER(TRIM(fact.tx_hash))
                AND EXISTS (
                        SELECT 1
                          FROM {canonical_cte_name} exact
                         WHERE exact.command_id = fact.command_id
                           AND LOWER(TRIM(COALESCE(exact.tx_hash, '')))
                               = LOWER(TRIM(fact.tx_hash))
                           AND LOWER(TRIM(COALESCE(exact.trade_id, '')))
                               != LOWER(TRIM(COALESCE(fact.trade_id, '')))
                           AND UPPER(COALESCE(exact.state, ''))
                               IN ('MATCHED', 'MINED', 'CONFIRMED')
                           AND CAST(COALESCE(exact.filled_size, '0') AS REAL) > 0
                    )
                )
        )
    """


def _exit_close_target_size(position: Position, command_size: object) -> Decimal | None:
    candidates = [
        _positive_decimal(command_size),
        _positive_decimal(getattr(position, "chain_shares", None)),
        _positive_decimal(getattr(position, "effective_shares", None)),
        _positive_decimal(getattr(position, "shares", None)),
    ]
    candidates = [candidate for candidate in candidates if candidate is not None]
    if not candidates:
        return None
    return max(candidates)


def _exit_trade_fact_close_candidate(
    conn: sqlite3.Connection | None,
    position: Position,
    *,
    exit_order_id: str = "",
) -> dict[str, object] | None:
    """Return durable full-fill evidence for an EXIT command, if already ingested."""

    if conn is None:
        return None
    position_id = str(getattr(position, "trade_id", "") or "").strip()
    if not position_id:
        return None

    trade_states = tuple(sorted(EXIT_TRADE_FACT_CLOSE_STATES))
    command_states = tuple(sorted(EXIT_TRADE_FACT_CLOSE_COMMAND_STATES))
    trade_placeholders = ", ".join("?" for _ in trade_states)
    command_placeholders = ", ".join("?" for _ in command_states)
    order_clause = ""
    params: list[object] = [position_id, *trade_states, *command_states]
    if exit_order_id:
        order_clause = "AND cmd.venue_order_id = ?"
        params.append(exit_order_id)

    try:
        row = conn.execute(
            "WITH "
            + _canonical_exit_trade_fact_cte()
            + ", "
            + _economic_exit_trade_fact_cte()
            + f"""
            SELECT cmd.command_id,
                   cmd.venue_order_id,
                   cmd.size AS command_size,
                   cmd.state AS command_state,
                   SUM(CAST(COALESCE(fact.filled_size, '0') AS REAL)) AS filled_size,
                   SUM(
                       CAST(COALESCE(fact.filled_size, '0') AS REAL)
                       * CAST(COALESCE(fact.fill_price, '0') AS REAL)
                   ) AS fill_notional,
                   GROUP_CONCAT(DISTINCT UPPER(COALESCE(fact.state, ''))) AS fill_states,
                   MAX(COALESCE(NULLIF(fact.venue_timestamp, ''), fact.observed_at)) AS observed_at
              FROM venue_commands cmd
              JOIN economic_exit_trade_fact fact
                ON fact.command_id = cmd.command_id
             WHERE cmd.position_id = ?
               AND UPPER(COALESCE(cmd.intent_kind, '')) = 'EXIT'
               AND cmd.venue_order_id IS NOT NULL
               AND cmd.venue_order_id != ''
               AND UPPER(COALESCE(fact.state, '')) IN ({trade_placeholders})
               AND UPPER(COALESCE(cmd.state, '')) IN ({command_placeholders})
               AND CAST(COALESCE(fact.filled_size, '0') AS REAL) > 0
               AND CAST(COALESCE(fact.fill_price, '0') AS REAL) > 0
               {order_clause}
             GROUP BY cmd.command_id, cmd.venue_order_id, cmd.size, cmd.state
             ORDER BY datetime(observed_at) DESC, cmd.updated_at DESC, cmd.command_id DESC
             LIMIT 1
            """,
            tuple(params),
        ).fetchone()
    except sqlite3.Error:
        return None
    if row is None:
        return None

    filled_size = _positive_decimal(row["filled_size"])
    fill_notional = _positive_decimal(row["fill_notional"])
    target_size = _exit_close_target_size(position, row["command_size"])
    if filled_size is None or fill_notional is None or target_size is None:
        return None
    if filled_size + EXIT_FULL_CLOSE_DUST_TOLERANCE < target_size:
        return None
    fill_price = fill_notional / filled_size
    if fill_price <= 0 or fill_price > 1:
        return None
    return {
        "command_id": str(row["command_id"] or ""),
        "venue_order_id": str(row["venue_order_id"] or ""),
        "filled_size": filled_size,
        "fill_price": fill_price,
        "observed_at": str(row["observed_at"] or ""),
        "fill_states": str(row["fill_states"] or ""),
        "command_state": str(row["command_state"] or ""),
    }


def _exit_trade_fact_confirmation_pending_candidate(
    conn: sqlite3.Connection | None,
    position: Position,
    *,
    exit_order_id: str = "",
) -> dict[str, object] | None:
    """Return non-final positive exit trade evidence that must block retries."""

    if conn is None:
        return None
    position_id = str(getattr(position, "trade_id", "") or "").strip()
    if not position_id:
        return None

    trade_states = tuple(sorted(NON_TERMINAL_TRADE_STATUSES))
    command_states = tuple(sorted(EXIT_TRADE_FACT_CLOSE_COMMAND_STATES))
    trade_placeholders = ", ".join("?" for _ in trade_states)
    command_placeholders = ", ".join("?" for _ in command_states)
    order_clause = ""
    params: list[object] = [position_id, *trade_states, *command_states]
    if exit_order_id:
        order_clause = "AND cmd.venue_order_id = ?"
        params.append(exit_order_id)

    try:
        row = conn.execute(
            "WITH "
            + _canonical_exit_trade_fact_cte()
            + ", "
            + _economic_exit_trade_fact_cte()
            + f"""
            SELECT cmd.command_id,
                   cmd.venue_order_id,
                   cmd.size AS command_size,
                   cmd.state AS command_state,
                   SUM(CAST(COALESCE(fact.filled_size, '0') AS REAL)) AS filled_size,
                   SUM(
                       CAST(COALESCE(fact.filled_size, '0') AS REAL)
                       * CAST(COALESCE(fact.fill_price, '0') AS REAL)
                   ) AS fill_notional,
                   GROUP_CONCAT(DISTINCT UPPER(COALESCE(fact.state, ''))) AS fill_states,
                   MAX(COALESCE(NULLIF(fact.venue_timestamp, ''), fact.observed_at)) AS observed_at
              FROM venue_commands cmd
              JOIN economic_exit_trade_fact fact
                ON fact.command_id = cmd.command_id
             WHERE cmd.position_id = ?
               AND UPPER(COALESCE(cmd.intent_kind, '')) = 'EXIT'
               AND cmd.venue_order_id IS NOT NULL
               AND cmd.venue_order_id != ''
               AND UPPER(COALESCE(fact.state, '')) IN ({trade_placeholders})
               AND UPPER(COALESCE(cmd.state, '')) IN ({command_placeholders})
               AND CAST(COALESCE(fact.filled_size, '0') AS REAL) > 0
               AND CAST(COALESCE(fact.fill_price, '0') AS REAL) > 0
               {order_clause}
             GROUP BY cmd.command_id, cmd.venue_order_id, cmd.size, cmd.state
             ORDER BY datetime(observed_at) DESC, cmd.updated_at DESC, cmd.command_id DESC
             LIMIT 1
            """,
            tuple(params),
        ).fetchone()
    except sqlite3.Error:
        return None
    if row is None:
        return None

    filled_size = _positive_decimal(row["filled_size"])
    fill_notional = _positive_decimal(row["fill_notional"])
    if filled_size is None or fill_notional is None:
        return None
    fill_price = fill_notional / filled_size
    if fill_price <= 0 or fill_price > 1:
        return None
    return {
        "command_id": str(row["command_id"] or ""),
        "venue_order_id": str(row["venue_order_id"] or ""),
        "filled_size": filled_size,
        "fill_price": fill_price,
        "observed_at": str(row["observed_at"] or ""),
        "fill_states": str(row["fill_states"] or ""),
        "command_state": str(row["command_state"] or ""),
    }


def _close_pending_exit_from_trade_fact(
    portfolio: PortfolioState,
    position: Position,
    fill: dict[str, object],
    *,
    conn: sqlite3.Connection | None,
) -> Position | None:
    fill_price = _positive_decimal(fill.get("fill_price"))
    if fill_price is None:
        return None
    order_id = str(fill.get("venue_order_id") or "")
    command_id = str(fill.get("command_id") or "")
    exit_reason = str(getattr(position, "exit_reason", "") or "DEFERRED_SELL_FILL")
    phase_before = _canonical_phase_before_for_economic_close(position)
    closed = compute_economic_close(
        portfolio,
        position.trade_id,
        float(fill_price),
        exit_reason,
    )
    if closed is None:
        return None

    closed.exit_state = "sell_filled"
    closed.order_status = "sell_filled"
    closed.last_exit_order_id = order_id
    closed.chain_shares = 0.0
    closed.chain_avg_price = 0.0
    closed.chain_cost_basis_usd = 0.0
    _dual_write_canonical_economic_close_if_available(
        conn,
        closed,
        phase_before=phase_before,
        command_id=command_id,
    )
    if conn is not None:
        conn.execute(
            """
            UPDATE position_current
               SET order_status = 'sell_filled',
                   exit_price = COALESCE(exit_price, ?),
                   chain_shares = 0.0,
                   chain_avg_price = 0.0,
                   chain_cost_basis_usd = 0.0
             WHERE position_id = ?
               AND phase = 'economically_closed'
            """,
            (float(fill_price), closed.trade_id),
        )
    return closed


def check_pending_exits(
    portfolio: PortfolioState,
    clob,
    conn: sqlite3.Connection | None = None,
    *,
    max_positions: int | None = None,
    cycle_budget_seconds: float | None = None,
) -> dict:
    """Check fill status for positions with pending sell orders.

    Called at start of each cycle, before monitor phase.
    Returns: {"filled": int, "retried": int, "unchanged": int, "filled_positions": list[Position]}
    """
    global _PENDING_EXIT_SCAN_CURSOR

    if conn is not None:
        from src.state.db import (
            log_exit_fill_check_error_event,
            log_exit_fill_event,
            log_exit_attempt_event,
            log_pending_exit_recovery_event,
            log_pending_exit_status_event,
            log_exit_retry_event,
        )

    stats = {"filled": 0, "retried": 0, "unchanged": 0, "filled_positions": []}
    max_scan_positions = (
        _pending_exit_status_max_positions()
        if max_positions is None
        else max(1, int(max_positions))
    )
    budget_seconds = (
        _pending_exit_status_budget_seconds()
        if cycle_budget_seconds is None
        else max(0.25, float(cycle_budget_seconds))
    )
    deadline = _time_module.monotonic() + budget_seconds
    scan_positions = _rotated_pending_exit_scan_positions(portfolio, stats=stats)
    stats["pending_exit_scan_candidates"] = len(scan_positions)
    stats["pending_exit_scan_max_positions"] = max_scan_positions
    stats["pending_exit_scan_budget_seconds"] = budget_seconds
    processed_scan_positions = 0

    for index, pos in enumerate(scan_positions):
        if processed_scan_positions >= max_scan_positions:
            stats["pending_exit_positions_deferred"] = (
                len(scan_positions) - index
            )
            stats["pending_exit_defer_reason"] = "max_positions"
            break
        if _time_module.monotonic() >= deadline:
            stats["pending_exit_positions_deferred"] = (
                len(scan_positions) - index
            )
            stats["pending_exit_defer_reason"] = "cycle_budget"
            break
        processed_scan_positions += 1
        raw_exit_state = getattr(pos, "exit_state", "")
        exit_state = str(getattr(raw_exit_state, "value", raw_exit_state) or "")
        fill = _exit_trade_fact_close_candidate(conn, pos)
        if fill is not None:
            closed = _close_pending_exit_from_trade_fact(portfolio, pos, fill, conn=conn)
            if closed is not None:
                stats["filled_positions"].append(closed)
                if conn is not None:
                    fill_price = float(fill["fill_price"])
                    filled_shares = float(fill["filled_size"])
                    order_id = str(fill["venue_order_id"])
                    log_exit_fill_event(
                        conn,
                        closed,
                        order_id=order_id,
                        fill_price=fill_price,
                        current_market_price=pos.last_monitor_market_price or pos.entry_price,
                        best_bid=getattr(pos, "last_monitor_best_bid", None),
                        timestamp=getattr(closed, "last_exit_at", None),
                    )
                    _log_partial_exit_execution_fact(
                        conn,
                        closed,
                        status=str(fill.get("fill_states") or "MATCHED"),
                        fill_price=fill_price,
                        filled_shares=filled_shares,
                        order_id=order_id,
                    )
                    _emit_typed_realized_fill(
                        actual_price=fill_price,
                        expected_price=pos.last_monitor_market_price or pos.entry_price,
                        side="sell",
                        shares=getattr(closed, "shares", 0.0),
                        trade_id=getattr(closed, "trade_id", ""),
                    )
                stats["filled"] += 1
                stats["filled_from_trade_fact"] = stats.get("filled_from_trade_fact", 0) + 1
                continue
        confirmation_pending = _exit_trade_fact_confirmation_pending_candidate(conn, pos)
        if confirmation_pending is not None:
            stats["unchanged"] += 1
            stats["exit_confirmation_pending"] = stats.get("exit_confirmation_pending", 0) + 1
            continue
        if exit_state == "retry_pending":
            if (
                str(getattr(pos, "next_exit_retry_at", "") or "").strip()
                and check_pending_retries(pos, conn=conn)
            ):
                stats["retried"] += 1
                stats["released_retry"] = stats.get("released_retry", 0) + 1
            else:
                stats["unchanged"] += 1
            continue
        _mark_pending_exit(pos)
        # NOTE: no canonical event here — upstream transition sites (execute_exit,
        # handle_exit_pending_missing, _mark_exit_dust_hold) already emit the
        # transition event at the actual state change.  Emitting again on every
        # passive scan would append a duplicate EXIT_ORDER_POSTED row each cycle
        # and corrupt query_execution_event_summary() counts. (WAVE-3 Batch B
        # bot review fix, 2026-05-18)

        # exit_intent with no order ID = stranded from exception during place_sell_order
        if pos.exit_state == "exit_intent":
            if not pos.last_exit_error:
                if release_pending_exit_without_order_if_retryable(pos, conn=conn):
                    stats["retried"] += 1
                    stats["released_no_order"] = stats.get("released_no_order", 0) + 1
                    continue
                if not _last_exit_order_id(pos, conn=conn):
                    pos.exit_state = ""
                    if str(getattr(pos, "order_status", "") or "") == "exit_intent":
                        pos.order_status = "filled"
                    _release_pending_exit(pos)
                    stats["unchanged"] += 1
                    continue
                continue
            _mark_exit_retry(pos, reason="STRANDED_EXIT_INTENT", error="exception_during_sell", conn=conn)
            if conn is not None:
                log_pending_exit_recovery_event(
                    conn,
                    pos,
                    event_type="EXIT_INTENT_RECOVERED",
                    reason="STRANDED_EXIT_INTENT",
                    error="exception_during_sell",
                )
                log_exit_retry_event(conn, pos, reason="STRANDED_EXIT_INTENT", error="exception_during_sell")
            stats["retried"] += 1
            continue

        exit_order_id = _last_exit_order_id(pos, conn=conn)
        if not exit_order_id:
            if release_pending_exit_without_order_if_retryable(pos, conn=conn):
                stats["retried"] += 1
                stats["released_no_order"] = stats.get("released_no_order", 0) + 1
                continue
            _mark_exit_retry(pos, reason="SELL_NO_ORDER_ID", error="no_order_id", conn=conn)
            if conn is not None:
                log_pending_exit_recovery_event(
                    conn,
                    pos,
                    event_type="EXIT_ORDER_ID_MISSING",
                    reason="SELL_NO_ORDER_ID",
                    error="no_order_id",
                )
                log_exit_retry_event(conn, pos, reason="SELL_NO_ORDER_ID", error="no_order_id")
            stats["retried"] += 1
            continue
        if not str(getattr(pos, "last_exit_order_id", "") or "").strip():
            pos.last_exit_order_id = exit_order_id

        fill = _exit_trade_fact_close_candidate(conn, pos, exit_order_id=exit_order_id)
        if fill is not None:
            closed = _close_pending_exit_from_trade_fact(portfolio, pos, fill, conn=conn)
            if closed is not None:
                stats["filled_positions"].append(closed)
                if conn is not None:
                    fill_price = float(fill["fill_price"])
                    filled_shares = float(fill["filled_size"])
                    log_exit_fill_event(
                        conn,
                        closed,
                        order_id=exit_order_id,
                        fill_price=fill_price,
                        current_market_price=pos.last_monitor_market_price or pos.entry_price,
                        best_bid=getattr(pos, "last_monitor_best_bid", None),
                        timestamp=getattr(closed, "last_exit_at", None),
                    )
                    _log_partial_exit_execution_fact(
                        conn,
                        closed,
                        status=str(fill.get("fill_states") or "MATCHED"),
                        fill_price=fill_price,
                        filled_shares=filled_shares,
                        order_id=exit_order_id,
                    )
                    _emit_typed_realized_fill(
                        actual_price=fill_price,
                        expected_price=pos.last_monitor_market_price or pos.entry_price,
                        side="sell",
                        shares=getattr(closed, "shares", 0.0),
                        trade_id=getattr(closed, "trade_id", ""),
                    )
                stats["filled"] += 1
                stats["filled_from_trade_fact"] = stats.get("filled_from_trade_fact", 0) + 1
                continue

        _commit_before_exit_venue_io(conn, stage="pending_exit_status_poll")
        status, status_payload = _check_order_fill(clob, exit_order_id)
        if conn is not None:
            if status:
                log_pending_exit_status_event(conn, pos, status=status)
            else:
                log_exit_fill_check_error_event(conn, pos, order_id=exit_order_id)

        if status in FILL_STATUSES:
            # Filled! Close the position.
            actual_price = _extract_fill_price(status_payload)
            if actual_price is None:
                _mark_exit_fill_economics_missing(
                    pos,
                    status=status,
                    order_id=exit_order_id,
                    conn=conn,
                )
                stats["unchanged"] += 1
                continue
            exit_reason = pos.exit_reason or "DEFERRED_SELL_FILL"
            phase_before = _canonical_phase_before_for_economic_close(pos)
            filled_shares = float(pos.effective_shares)
            closed = compute_economic_close(portfolio, pos.trade_id, actual_price, exit_reason)
            if closed is not None:
                closed.exit_state = "sell_filled"
                _dual_write_canonical_economic_close_if_available(
                    conn,
                    closed,
                    phase_before=phase_before,
                )
                stats["filled_positions"].append(closed)
                if conn is not None:
                    log_exit_fill_event(
                        conn,
                        closed,
                        order_id=exit_order_id,
                        fill_price=actual_price,
                        current_market_price=pos.last_monitor_market_price or pos.entry_price,
                        best_bid=getattr(pos, "last_monitor_best_bid", None),
                        timestamp=getattr(closed, "last_exit_at", None),
                    )
                    _log_partial_exit_execution_fact(
                        conn,
                        closed,
                        status=status or "CONFIRMED",
                        fill_price=actual_price,
                        filled_shares=filled_shares,
                        order_id=exit_order_id,
                    )
                    # Slice P5-1 third site: typed RealizedFill at the
                    # async-monitor fill-receipt seam (same construction
                    # pattern as L453/L600).
                    _emit_typed_realized_fill(
                        actual_price=actual_price,
                        expected_price=pos.last_monitor_market_price or pos.entry_price,
                        side="sell",
                        shares=getattr(closed, "shares", 0.0),
                        trade_id=getattr(closed, "trade_id", ""),
                    )
            stats["filled"] += 1
        else:
            partial_applied = False
            partial = _partial_exit_delta(
                status=status,
                payload=status_payload,
                current_open_shares=pos.effective_shares,
            )
            if partial:
                filled_shares, remaining_shares = partial
                actual_price = _extract_fill_price(status_payload)
                if actual_price is None:
                    _mark_exit_fill_economics_missing(
                        pos,
                        status=status,
                        order_id=exit_order_id,
                        conn=conn,
                    )
                else:
                    partial_applied = _apply_partial_exit_fill(
                        pos,
                        filled_shares=filled_shares,
                        remaining_shares=remaining_shares,
                        fill_price=actual_price,
                        order_id=exit_order_id,
                        status=status,
                    )
                if partial_applied and conn is not None:
                    log_exit_attempt_event(
                        conn,
                        pos,
                        order_id=exit_order_id,
                        status=status or "PARTIAL",
                        current_market_price=pos.last_monitor_market_price or pos.entry_price,
                        best_bid=getattr(pos, "last_monitor_best_bid", None),
                        shares=filled_shares,
                        details={
                            "semantic_event": "PARTIAL_FILL_OBSERVED",
                            "filled_shares": filled_shares,
                            "remaining_shares": remaining_shares,
                            "fill_price": actual_price,
                        },
                    )
                    _dual_write_partial_exit_projection_if_available(
                        conn,
                        pos,
                        filled_shares=filled_shares,
                        remaining_shares=remaining_shares,
                        fill_price=actual_price,
                        order_id=exit_order_id,
                        status=status or "PARTIAL",
                    )
                    if status not in VOID_STATUSES:
                        _log_partial_exit_execution_fact(
                            conn,
                            pos,
                            status=status or "PARTIAL",
                            fill_price=actual_price,
                            filled_shares=filled_shares,
                            order_id=exit_order_id,
                        )
            if status in VOID_STATUSES:
                _mark_exit_retry(pos, reason=f"SELL_{status}", error=status, conn=conn)
                if conn is not None:
                    log_pending_exit_recovery_event(
                        conn,
                        pos,
                        event_type="EXIT_ORDER_VOIDED",
                        reason=f"SELL_{status}",
                        error=status,
                    )
                    log_exit_retry_event(conn, pos, reason=f"SELL_{status}", error=status)
                    if partial_applied and actual_price is not None:
                        _log_partial_exit_execution_fact(
                            conn,
                            pos,
                            status=status or "PARTIAL",
                            fill_price=actual_price,
                            filled_shares=filled_shares,
                            order_id=exit_order_id,
                        )
                stats["retried"] += 1
            elif partial_applied:
                stats["unchanged"] += 1
            elif status == "":
                # Empty status = CLOB outage or API error. Don't stall forever.
                # After 3 consecutive unknown statuses, trigger retry to avoid
                # permanent stall.
                pos.exit_retry_count += 1
                if pos.exit_retry_count >= 3:
                    _mark_exit_retry(pos, reason="SELL_STATUS_UNKNOWN", error="3_consecutive_unknown", conn=conn)
                    if conn is not None:
                        log_exit_retry_event(conn, pos, reason="SELL_STATUS_UNKNOWN", error="3_consecutive_unknown")
                    stats["retried"] += 1
                else:
                    stats["unchanged"] += 1
            else:
                token_id = _asset_id_for_position(pos)
                _commit_before_exit_venue_io(conn, stage="pending_exit_reprice")
                if _cancel_stale_pending_exit_for_reprice(
                    conn=conn,
                    position=pos,
                    clob=clob,
                    token_id=token_id,
                    log_pending_exit_recovery_event=(
                        log_pending_exit_recovery_event if conn is not None else None
                    ),
                    log_exit_retry_event=log_exit_retry_event if conn is not None else None,
                ):
                    stats["retried"] += 1
                else:
                    stats["unchanged"] += 1

    stats["pending_exit_positions_scanned"] = processed_scan_positions
    if scan_positions:
        _PENDING_EXIT_SCAN_CURSOR = (
            _PENDING_EXIT_SCAN_CURSOR + processed_scan_positions
        ) % len(scan_positions)

    return stats


def check_pending_retries(position: Position, conn: sqlite3.Connection | None = None) -> bool:
    """Check if a retry-pending position's cooldown has expired.

    Returns True if position is ready for a new exit attempt.
    """
    if position.exit_state == "backoff_exhausted":
        return False  # Hold to settlement, stop retrying

    if position.exit_state != "retry_pending":
        return False

    previous_next_retry_at = str(getattr(position, "next_exit_retry_at", "") or "")
    previous_retry_count = int(getattr(position, "exit_retry_count", 0) or 0)
    previous_error = str(getattr(position, "last_exit_error", "") or "")
    if not previous_error:
        previous_error = _latest_exit_reject_error(conn, position)

    dust_error = _latest_snapshot_min_order_dust_error(position, conn=conn)
    if dust_error:
        current_reason = str(getattr(position, "exit_reason", "") or "EXIT_RETRY_PENDING")
        dust_reason = (
            current_reason
            if _dust_evidence_marks_non_executable(current_reason)
            else f"{current_reason} [DUST: {dust_error}]"
        )
        _mark_exit_dust_hold(
            position,
            reason=dust_reason,
            error=dust_error,
            conn=conn,
        )
        return False

    runtime_gate_block = _is_runtime_submit_gate_block_error(previous_error)
    if runtime_gate_block and not _runtime_submit_gate_currently_allows_submit():
        if not is_exit_cooldown_active(position):
            current_reason = str(getattr(position, "exit_reason", "") or "RUNTIME_SUBMIT_GATE_BLOCKED")
            position.exit_state = "retry_pending"
            position.order_status = "retry_pending"
            position.next_exit_retry_at = (
                _utcnow() + timedelta(seconds=RUNTIME_SUBMIT_GATE_BLOCK_COOLDOWN_SECONDS)
            ).isoformat()
            _dual_write_canonical_pending_exit_if_available(
                conn,
                position,
                reason=current_reason,
                error=previous_error,
                event_type="EXIT_ORDER_REJECTED",
                extra_payload={
                    "status": "runtime_submit_gate_blocked",
                    "runtime_submit_gate_block": True,
                    "previous_retry_count": previous_retry_count,
                    "previous_next_retry_at": previous_next_retry_at,
                    "next_retry_at": position.next_exit_retry_at,
                    "retry_count": int(getattr(position, "exit_retry_count", 0) or 0),
                },
            )
        return False

    if not runtime_gate_block and is_exit_cooldown_active(position):
        return False  # Still cooling down

    # Cooldown expired — position is eligible for exit re-evaluation
    position.exit_state = ""  # Reset to allow new exit attempt
    position.next_exit_retry_at = ""
    position.exit_retry_count = 0
    if str(getattr(position, "order_status", "") or "") == "retry_pending":
        position.order_status = "filled"
    _release_pending_exit(position)
    if conn is not None:
        _dual_write_exit_retry_released_if_available(
            conn,
            position,
            previous_next_retry_at=previous_next_retry_at,
            previous_retry_count=previous_retry_count,
            previous_error=previous_error,
        )
    return True


def _dual_write_exit_retry_released_if_available(
    conn: sqlite3.Connection | None,
    position: Position,
    *,
    previous_next_retry_at: str,
    previous_retry_count: int,
    previous_error: str,
    event_type: str = "EXIT_RETRY_RELEASED",
    release_reason: str = "EXIT_RETRY_COOLDOWN_EXPIRED",
    caused_by: str = "exit_retry_cooldown_expired",
) -> bool:
    """Persist retry cooldown release and projection in one canonical write.

    A released pending exit is still a live held position; it must immediately
    re-enter normal monitor redecision. The release cannot be only an in-memory
    mutation, because restart/chain-correction projection would reload the old
    ``pending_exit/retry_pending`` state and strand the position again.
    """

    if conn is None:
        return False
    trade_id = str(getattr(position, "trade_id", "") or "")
    if not trade_id:
        return False
    try:
        from src.engine.lifecycle_events import build_position_current_projection
        from src.state.db import append_many_and_project
        from src.state.lifecycle_manager import fold_lifecycle_phase, phase_for_runtime_position

        sequence_no = _next_canonical_sequence_no(conn, trade_id)
        occurred_at = datetime.now(timezone.utc).isoformat()
        if not any(
            getattr(position, field, "")
            for field in (
                "last_monitor_at",
                "last_exit_at",
                "chain_verified_at",
                "day0_entered_at",
                "entered_at",
                "order_posted_at",
            )
        ):
            position.order_posted_at = occurred_at
        phase_after = phase_for_runtime_position(
            state=getattr(position, "state", ""),
            exit_state=getattr(position, "exit_state", ""),
            chain_state=getattr(position, "chain_state", ""),
        ).value
        if phase_after == LifecyclePhase.PENDING_EXIT.value:
            return False
        projection = build_position_current_projection(position)
        projection["phase"] = phase_after
        projection["updated_at"] = occurred_at
        projection["order_status"] = "filled"
        projection["next_exit_retry_at"] = ""
        projection["exit_retry_count"] = 0
        env = str(getattr(position, "env", "") or "live")
        if env not in {"live", "test", "replay", "backtest"}:
            env = "live"
        payload = {
            "status": "ready",
            "exit_reason": getattr(position, "exit_reason", "") or release_reason,
            "error": previous_error,
            "previous_retry_count": previous_retry_count,
            "retry_count": 0,
            "previous_next_retry_at": previous_next_retry_at,
            "next_retry_at": "",
            "release_reason": release_reason,
        }
        event = {
            "event_id": f"{trade_id}:{event_type.lower()}:{sequence_no}",
            "position_id": trade_id,
            "event_version": 1,
            "sequence_no": sequence_no,
            "event_type": event_type,
            "occurred_at": occurred_at,
            "phase_before": LifecyclePhase.PENDING_EXIT.value,
            "phase_after": fold_lifecycle_phase(
                LifecyclePhase.PENDING_EXIT.value,
                phase_after,
            ).value,
            "strategy_key": str(
                getattr(position, "strategy_key", "")
                or getattr(position, "strategy", "")
                or ""
            ),
            "decision_id": None,
            "snapshot_id": getattr(position, "decision_snapshot_id", "") or None,
            "order_id": None,
            "command_id": None,
            "caused_by": caused_by,
            "idempotency_key": f"{trade_id}:{event_type.lower()}:{sequence_no}",
            "venue_status": "ready",
            "source_module": "src.execution.exit_lifecycle",
            "env": env,
            "payload_json": json.dumps(payload, default=str, sort_keys=True),
        }
        append_many_and_project(conn, [event], projection)
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "EXIT_RETRY_RELEASED canonical write failed for %s: %s",
            trade_id,
            exc,
        )
        return False


def release_pending_exit_without_order_if_retryable(
    position: Position,
    *,
    conn: sqlite3.Connection | None = None,
) -> bool:
    """Release a stranded pending_exit that has no live sell order to monitor."""

    if _runtime_state_value(position) != "pending_exit":
        return False
    raw_exit_state = getattr(position, "exit_state", "")
    exit_state = str(getattr(raw_exit_state, "value", raw_exit_state) or "")
    if exit_state in {"backoff_exhausted", "retry_pending"}:
        return False
    if is_exit_cooldown_active(position):
        return False
    if _last_exit_order_id(position, conn=conn):
        return False
    if exit_state in _EXIT_LIFECYCLE_IN_FLIGHT_STATES and conn is None:
        return False
    previous_next_retry_at = str(getattr(position, "next_exit_retry_at", "") or "")
    previous_retry_count = int(getattr(position, "exit_retry_count", 0) or 0)
    previous_error = str(getattr(position, "last_exit_error", "") or "")
    position.exit_state = ""
    position.next_exit_retry_at = ""
    position.exit_retry_count = 0
    order_status = str(getattr(position, "order_status", "") or "")
    if order_status.startswith("sell_") or order_status in {"retry_pending", "exit_intent"}:
        position.order_status = "filled"
    _release_pending_exit(position)
    if conn is not None:
        _dual_write_exit_retry_released_if_available(
            conn,
            position,
            previous_next_retry_at=previous_next_retry_at,
            previous_retry_count=previous_retry_count,
            previous_error=previous_error,
            release_reason="PENDING_EXIT_NO_ORDER_RELEASED",
            caused_by="pending_exit_no_order_released",
        )
    return True


def _check_order_fill(clob, order_id: str) -> tuple[str, object]:
    """Check CLOB order status. Returns (normalized status, raw payload)."""
    try:
        payload = clob.get_order_status(order_id)
        if payload is None:
            return "", None
        if isinstance(payload, str):
            return payload.upper(), payload
        if isinstance(payload, dict):
            status = payload.get("status") or payload.get("state") or payload.get("orderStatus")
            return (str(status).upper() if status else "", payload)
        return "", payload
    except Exception as exc:
        logger.warning("Order fill check failed for %s: %s", order_id, exc)
        return "", None


def _coerce_sell_result(trade_id: str, sell_result: OrderResult | dict) -> OrderResult:
    if isinstance(sell_result, OrderResult):
        return sell_result
    if isinstance(sell_result, dict):
        if sell_result.get("error"):
            return OrderResult(
                trade_id=trade_id,
                status="rejected",
                reason=str(sell_result["error"]),
            )
        order_id = (
            sell_result.get("orderID")
            or sell_result.get("orderId")
            or sell_result.get("id")
        )
        if not order_id:
            return OrderResult(
                trade_id=trade_id,
                status="rejected",
                reason="missing_order_id",
                order_role="exit",
            )
        return OrderResult(
            trade_id=trade_id,
            status="pending",
            order_id=order_id,
            external_order_id=order_id,
            submitted_price=sell_result.get("price"),
            shares=sell_result.get("shares"),
            venue_status=str(sell_result.get("status") or "placed"),
            fill_price=_first_explicit_fill_price(sell_result),
            reason="sell order posted",
            order_role="exit",
        )
    raise TypeError(f"unsupported sell result type: {type(sell_result)!r}")


def _serialize_sell_result(sell_result: OrderResult | dict) -> dict:
    if isinstance(sell_result, OrderResult):
        return {
            "trade_id": sell_result.trade_id,
            "status": sell_result.status,
            "reason": sell_result.reason,
            "order_id": sell_result.order_id,
            "external_order_id": sell_result.external_order_id,
            "submitted_price": sell_result.submitted_price,
            "shares": sell_result.shares,
            "venue_status": sell_result.venue_status,
            "fill_price": sell_result.fill_price,
            "order_role": sell_result.order_role,
            "intent_id": sell_result.intent_id,
            "idempotency_key": sell_result.idempotency_key,
        }
    return dict(sell_result)


def _extract_fill_price(
    sell_result: OrderResult | dict | object,
) -> Optional[float]:
    """Extract explicit venue fill price only."""
    if isinstance(sell_result, OrderResult) and sell_result.fill_price not in (None, ""):
        return _positive_finite_float(sell_result.fill_price)
    if isinstance(sell_result, dict):
        return _first_explicit_fill_price(sell_result)
    return None


def _first_explicit_fill_price(payload: dict) -> Optional[float]:
    for key in ("avgPrice", "avg_price", "fillPrice", "fill_price"):
        if key in payload and payload[key] not in (None, ""):
            value = _positive_finite_float(payload[key])
            if value is not None:
                return value
    return None


def _positive_finite_float(value: object) -> Optional[float]:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(numeric) or numeric <= 0.0 or numeric > 1.0:
        return None
    return numeric


def _top_book_for_pending_exit_reprice(clob, token_id: str) -> tuple[float | None, float | None]:
    """Return current held-token top bid/ask, allowing one-sided books."""

    if clob is None or not token_id:
        return None, None
    book_fn = getattr(clob, "get_orderbook", None) or getattr(clob, "get_orderbook_snapshot", None)
    if not callable(book_fn):
        return None, None
    try:
        from src.data.market_scanner import _optional_top_book_level_decimal

        book = book_fn(token_id)
        top_bid, _bid_size = _optional_top_book_level_decimal(book, "bids")
        top_ask, _ask_size = _optional_top_book_level_decimal(book, "asks")
    except Exception as exc:
        logger.debug(
            "Pending-exit reprice book read failed for token=%s: %s",
            token_id,
            exc,
        )
        return None, None

    def _as_float(value):
        if value is None:
            return None
        numeric = float(value)
        return numeric if math.isfinite(numeric) and 0.0 < numeric < 1.0 else None

    return _as_float(top_bid), _as_float(top_ask)


def _exit_command_row_for_order(
    conn: sqlite3.Connection | None,
    position: Position,
    token_id: str,
) -> sqlite3.Row | None:
    exit_order_id = _last_exit_order_id(position, conn=conn)
    if conn is None or not exit_order_id:
        return None
    try:
        return conn.execute(
            """
            SELECT command_id, price, size, venue_order_id
              FROM venue_commands
             WHERE venue_order_id = ?
               AND position_id = ?
               AND token_id = ?
               AND intent_kind = 'EXIT'
             ORDER BY updated_at DESC, created_at DESC
             LIMIT 1
            """,
            (exit_order_id, position.trade_id, token_id),
        ).fetchone()
    except sqlite3.OperationalError:
        return None


def _pending_exit_reprice_reason(
    *,
    resting_price: float,
    best_bid: float | None,
    best_ask: float | None,
    min_tick: float,
) -> str:
    """Classify stale pending-exit sell orders from live book evidence."""

    if not math.isfinite(resting_price) or resting_price <= 0.0:
        return ""
    min_move = max(float(min_tick) * PENDING_EXIT_REPRICE_MIN_TICKS, 0.001)
    if best_bid is not None and resting_price - float(best_bid) >= min_move:
        return "SELL_REPRICE_BID_MOVED_AWAY"
    if best_bid is None and best_ask is not None and resting_price - float(best_ask) >= min_move:
        return "SELL_REPRICE_ONE_SIDED_NO_BID"
    return ""


def _cancel_stale_pending_exit_for_reprice(
    *,
    conn: sqlite3.Connection | None,
    position: Position,
    clob,
    token_id: str,
    log_pending_exit_recovery_event=None,
    log_exit_retry_event=None,
) -> bool:
    """Cancel a live pending-exit order whose price no longer tracks live CLOB.

    This does not close locally and does not submit a replacement directly.  It
    moves the position to retry_pending with zero cooldown so the normal
    monitor path can recapture a fresh snapshot/book and issue the next limit
    sell through existing exit safety.
    """

    row = _exit_command_row_for_order(conn, position, token_id)
    if row is None:
        return False
    try:
        resting_price = float(row["price"] if isinstance(row, sqlite3.Row) else row[1])
    except (TypeError, ValueError):
        return False
    best_bid, best_ask = _top_book_for_pending_exit_reprice(clob, token_id)
    reason = _pending_exit_reprice_reason(
        resting_price=resting_price,
        best_bid=best_bid,
        best_ask=best_ask,
        min_tick=0.001,
    )
    if not reason:
        return False

    cancel_fn = getattr(clob, "cancel_order", None)
    if not callable(cancel_fn):
        _mark_exit_retry(
            position,
            reason=f"{reason} [CANCEL_UNAVAILABLE]",
            error="cancel_order_unavailable",
            cooldown_seconds=0,
            conn=conn,
        )
        return True

    detail = (
        f"resting_price={resting_price:.6f};"
        f"best_bid={best_bid if best_bid is not None else 'none'};"
        f"best_ask={best_ask if best_ask is not None else 'none'}"
    )
    try:
        from src.execution.exit_safety import request_cancel_for_command

        command_id = str(row["command_id"] if isinstance(row, sqlite3.Row) else row[0])
        outcome = request_cancel_for_command(
            conn,
            command_id,
            lambda order_id: cancel_fn(order_id),
        )
        if outcome.status != "CANCELED":
            reason = f"{reason} [CANCEL_{outcome.status}]"
            detail = outcome.reason or detail
    except Exception as exc:
        reason = f"{reason} [CANCEL_UNKNOWN]"
        detail = str(exc)[:500]

    _mark_exit_retry(
        position,
        reason=reason,
        error=detail,
        cooldown_seconds=0,
        conn=conn,
    )
    if conn is not None and log_pending_exit_recovery_event is not None:
        log_pending_exit_recovery_event(
            conn,
            position,
            event_type="EXIT_ORDER_REJECTED",
            reason=reason,
            error=detail,
        )
    if conn is not None and log_exit_retry_event is not None:
        log_exit_retry_event(conn, position, reason=reason, error=detail)
    return True


def _mark_exit_fill_economics_missing(
    position: Position,
    *,
    status: str,
    order_id: str,
    conn: sqlite3.Connection | None = None,
) -> None:
    _mark_pending_exit(position)
    position.exit_state = "sell_pending"
    position.last_exit_error = "missing_exit_fill_price"
    _dual_write_canonical_pending_exit_if_available(
        conn,
        position,
        reason=f"FILL_ECONOMICS_MISSING:{status}",
        error="missing_exit_fill_price",
        event_type="EXIT_ORDER_REJECTED",
    )
    logger.error(
        "Exit fill price missing for %s order=%s status=%s; holding pending exit",
        position.trade_id,
        order_id,
        status,
    )


def _mark_exit_retry(
    position: Position,
    reason: str,
    error: str = "",
    cooldown_seconds: int = DEFAULT_COOLDOWN_SECONDS,
    conn: sqlite3.Connection | None = None,
) -> None:
    """Transition position to retry_pending with exponential backoff."""
    _mark_pending_exit(position)

    if _is_channel_not_ready_error(error):
        # Transient channel gap: do NOT consume the bounded retry budget toward
        # backoff_exhausted/admin-close. Keep the exit alive and retrying on a
        # short fixed cooldown so it sells once the channel recovers, rather than
        # abandoning a still-sellable reversal exit. (2026-06-23 diagnosis.)
        position.last_exit_error = error[:500]
        position.exit_state = "retry_pending"
        position.order_status = "retry_pending"
        position.next_exit_retry_at = (
            _utcnow() + timedelta(seconds=CHANNEL_NOT_READY_COOLDOWN_SECONDS)
        ).isoformat()
        _dual_write_canonical_pending_exit_if_available(
            conn,
            position,
            reason=reason,
            error=error,
            event_type="EXIT_ORDER_REJECTED",
        )
        logger.info(
            "EXIT CHANNEL-NOT-READY %s: %s (budget NOT consumed; next retry %s)",
            position.trade_id, reason, position.next_exit_retry_at,
        )
        return

    if _is_exit_transient_lock_error(error):
        position.last_exit_error = error[:500]
        position.exit_state = "retry_pending"
        position.order_status = "retry_pending"
        position.next_exit_retry_at = (
            _utcnow() + timedelta(seconds=EXIT_LOCKED_COOLDOWN_SECONDS)
        ).isoformat()
        _dual_write_canonical_pending_exit_if_available(
            conn,
            position,
            reason=reason,
            error=error,
            event_type="EXIT_ORDER_REJECTED",
        )
        logger.info(
            "EXIT LOCKED %s: %s (budget NOT consumed; next retry %s)",
            position.trade_id,
            reason,
            position.next_exit_retry_at,
        )
        return

    if _is_runtime_submit_gate_block_error(error):
        position.last_exit_error = error[:500]
        position.exit_state = "retry_pending"
        position.order_status = "retry_pending"
        position.next_exit_retry_at = (
            _utcnow() + timedelta(seconds=RUNTIME_SUBMIT_GATE_BLOCK_COOLDOWN_SECONDS)
        ).isoformat()
        _dual_write_canonical_pending_exit_if_available(
            conn,
            position,
            reason=reason,
            error=error,
            event_type="EXIT_ORDER_REJECTED",
            extra_payload={
                "status": "runtime_submit_gate_blocked",
                "runtime_submit_gate_block": True,
                "retry_count": int(getattr(position, "exit_retry_count", 0) or 0),
                "next_retry_at": position.next_exit_retry_at,
            },
        )
        logger.warning(
            "EXIT RUNTIME-SUBMIT-GATE-BLOCKED %s: %s "
            "(budget NOT consumed; recheck gate by %s)",
            position.trade_id,
            reason,
            position.next_exit_retry_at,
        )
        return

    position.exit_retry_count += 1
    position.last_exit_error = error[:500]

    if position.exit_retry_count >= MAX_EXIT_RETRIES:
        position.exit_state = "backoff_exhausted"
        position.order_status = "backoff_exhausted"
        _dual_write_canonical_pending_exit_if_available(
            conn,
            position,
            reason=reason,
            error=error,
            event_type="EXIT_ORDER_REJECTED",
        )
        logger.warning(
            "EXIT BACKOFF EXHAUSTED %s: %s (after %d retries). Holding to settlement.",
            position.trade_id, reason, position.exit_retry_count,
        )
        return

    # Exponential cooldown: 5min, 10min, 20min, ... capped at 60min
    actual_cooldown = min(cooldown_seconds * (2 ** (position.exit_retry_count - 1)), 3600)
    position.exit_state = "retry_pending"
    position.order_status = "retry_pending"
    position.next_exit_retry_at = (
        _utcnow() + timedelta(seconds=actual_cooldown)
    ).isoformat()
    _dual_write_canonical_pending_exit_if_available(
        conn,
        position,
        reason=reason,
        error=error,
        event_type="EXIT_ORDER_REJECTED",
    )

    logger.warning(
        "EXIT RETRY %s: %s (attempt %d, next retry %s)",
        position.trade_id, reason, position.exit_retry_count,
        position.next_exit_retry_at,
    )


# ---------------------------------------------------------------------------
# F1: Settlement exit facade — single-writer contract for settlement closes
# ---------------------------------------------------------------------------

def mark_settled(
    portfolio: PortfolioState,
    trade_id: str,
    settlement_price: float,
    exit_reason: str = "SETTLEMENT",
) -> Optional[Position]:
    """Single canonical entry point for settlement-driven position close.

    Wraps compute_settlement_close so all exit state transitions
    (signal + settlement) route through exit_lifecycle.
    Covers buy_yes/buy_no settlements. Void/unknown-direction
    positions are handled separately by void_position.
    """
    closed = compute_settlement_close(portfolio, trade_id, settlement_price, exit_reason)
    if closed is not None:
        logger.info(
            "EXIT_LIFECYCLE mark_settled %s: price=%.4f reason=%s",
            trade_id, settlement_price, exit_reason,
        )
    return closed


# ---------------------------------------------------------------------------
# R4-b (2026-07-08): exit_monitor scheduler job + its exit-retry-release
# helpers, moved verbatim from src/main.py. Both the exit_monitor cycle and
# the M5 WS-gap-clear release path (invoked from main.py's venue background
# maintenance) share ``_append_exit_retry_release_events_and_update_projection``
# — this is the owning module for exit-retry-release state (position_current /
# position_events), so both callers import it from here rather than from
# src.main.
# ---------------------------------------------------------------------------

_EXIT_MONITOR_INTERVAL_SECONDS = 120.0
_MONITOR_CADENCE_GAP_FACTOR = 2.0


def _release_ws_gap_blocked_exit_retries_after_m5_clear(
    conn,
    *,
    observed_at: datetime,
) -> dict:
    """Release reduce-only exit retries that were delayed only by the M5 WS latch.

    M5 clearing proves the user-channel gap has been reconciled. Keeping positions
    that were rejected for ``ws_gap...m5_reconcile_required=True`` on exponential
    backoff after that proof delays exits for no additional safety evidence.
    """

    now_iso = observed_at.isoformat()
    recent_cutoff = (observed_at - timedelta(minutes=10)).isoformat()
    try:
        rows = conn.execute(
            """
            SELECT pc.position_id
              FROM position_current pc
             WHERE COALESCE(pc.exit_retry_count, 0) > 0
               AND COALESCE(pc.next_exit_retry_at, '') > ?
               AND COALESCE(pc.phase, '') IN ('active', 'day0_window', 'pending_exit')
               AND (
                    COALESCE(pc.chain_shares, 0) > 0
                 OR (
                        COALESCE(pc.chain_shares, 0) = 0
                    AND COALESCE(pc.shares, 0) > 0
                    AND COALESCE(pc.chain_state, '') = 'synced'
                    )
               )
               AND EXISTS (
                    SELECT 1
                      FROM position_events pe
                     WHERE pe.position_id = pc.position_id
                       AND pe.event_type = 'EXIT_ORDER_REJECTED'
                       AND pe.occurred_at >= ?
                       AND COALESCE(json_extract(pe.payload_json, '$.error'), '') LIKE 'ws_gap=%'
                       AND COALESCE(json_extract(pe.payload_json, '$.error'), '') LIKE '%m5_reconcile_required=True%'
               )
             ORDER BY pc.next_exit_retry_at, pc.position_id
            """,
            (now_iso, recent_cutoff),
        ).fetchall()
    except Exception as exc:  # noqa: BLE001 - maintenance must not crash heartbeat.
        logger.warning("M5 exit-retry release query failed closed: %s", exc)
        return {"released": 0, "position_ids": [], "error": str(exc)}
    position_ids = [str(row[0]) for row in rows if str(row[0] or "")]
    if not position_ids:
        return {"released": 0, "position_ids": []}
    released = _append_exit_retry_release_events_and_update_projection(
        conn,
        position_ids,
        observed_at=observed_at,
        release_reason="M5_WS_GAP_RECONCILE_CLEARED",
        release_error="ws_gap_m5_reconcile_cleared",
    )
    changed = int(released.get("released", 0) or 0)
    position_ids = list(released.get("position_ids", []) or [])
    logger.info(
        "M5 cleared WS latch; released %d ws-gap-blocked exit retries: %s",
        changed,
        position_ids,
    )
    return released


def _append_exit_retry_release_events_and_update_projection(
    conn,
    position_ids: list[str],
    *,
    observed_at: datetime,
    release_reason: str,
    release_error: str,
) -> dict:
    """Append retry-release evidence before shortening projection cooldowns."""

    if not position_ids:
        return {"released": 0, "position_ids": []}
    now_iso = observed_at.isoformat()
    placeholders = ",".join("?" for _ in position_ids)
    try:
        rows = conn.execute(
            f"""
            SELECT position_id,
                   COALESCE(phase, '') AS phase,
                   COALESCE(strategy_key, '') AS strategy_key,
                   COALESCE(order_id, '') AS order_id,
                   COALESCE(exit_retry_count, 0) AS exit_retry_count,
                   COALESCE(next_exit_retry_at, '') AS next_exit_retry_at
              FROM position_current
             WHERE position_id IN ({placeholders})
             ORDER BY position_id
            """,
            tuple(position_ids),
        ).fetchall()
    except Exception as exc:  # noqa: BLE001
        logger.warning("exit-retry release projection read failed closed: %s", exc)
        return {"released": 0, "position_ids": [], "error": str(exc)}

    changed = 0
    released_ids: list[str] = []
    for row in rows:
        position_id = str(row[0] or "")
        if not position_id:
            continue
        try:
            conn.execute("SAVEPOINT exit_retry_release")
            sequence_row = conn.execute(
                "SELECT COALESCE(MAX(sequence_no), 0) FROM position_events WHERE position_id = ?",
                (position_id,),
            ).fetchone()
            sequence_no = int(sequence_row[0] or 0) + 1
            payload = {
                "status": "ready",
                "exit_reason": release_reason,
                "error": release_error,
                "retry_count": int(row[4] or 0),
                "previous_next_retry_at": str(row[5] or ""),
                "next_retry_at": now_iso,
                "release_reason": release_reason,
            }
            conn.execute(
                """
                INSERT INTO position_events (
                    event_id, position_id, event_version, sequence_no, event_type,
                    occurred_at, phase_before, phase_after, strategy_key, decision_id,
                    snapshot_id, order_id, command_id, caused_by, idempotency_key,
                    venue_status, source_module, payload_json, env
                ) VALUES (?, ?, 1, ?, 'EXIT_RETRY_RELEASED',
                          ?, ?, ?, ?, NULL, NULL, ?, NULL, ?,
                          ?, 'ready', 'src.main', ?, 'live')
                """,
                (
                    f"{position_id}:exit_retry_released:{sequence_no}",
                    position_id,
                    sequence_no,
                    now_iso,
                    str(row[1] or "pending_exit"),
                    str(row[1] or "pending_exit"),
                    str(row[2] or ""),
                    str(row[3] or "") or None,
                    release_reason,
                    f"{position_id}:exit_retry_released:{sequence_no}",
                    json.dumps(payload, sort_keys=True),
                ),
            )
            cur = conn.execute(
                """
                UPDATE position_current
                   SET next_exit_retry_at = ?,
                       updated_at = ?
                 WHERE position_id = ?
                """,
                (now_iso, now_iso, position_id),
            )
            if int(cur.rowcount or 0) > 0:
                changed += int(cur.rowcount or 0)
                released_ids.append(position_id)
                conn.execute("RELEASE SAVEPOINT exit_retry_release")
            else:
                conn.execute("ROLLBACK TO SAVEPOINT exit_retry_release")
                conn.execute("RELEASE SAVEPOINT exit_retry_release")
        except Exception as exc:  # noqa: BLE001
            try:
                conn.execute("ROLLBACK TO SAVEPOINT exit_retry_release")
                conn.execute("RELEASE SAVEPOINT exit_retry_release")
            except Exception:  # noqa: BLE001
                pass
            logger.warning(
                "exit-retry release append/update failed closed for %s: %s",
                position_id,
                exc,
            )
    return {"released": changed, "position_ids": released_ids}


def _release_allocator_config_blocked_exit_retries_after_refresh(
    conn,
    portfolio,
    *,
    observed_at: datetime,
) -> dict:
    """Release exits delayed only because allocator refresh had not run yet."""

    now_iso = observed_at.isoformat()
    recent_cutoff = (observed_at - timedelta(minutes=10)).isoformat()
    try:
        rows = conn.execute(
            """
            SELECT pc.position_id
              FROM position_current pc
             WHERE COALESCE(pc.exit_retry_count, 0) > 0
               AND COALESCE(pc.next_exit_retry_at, '') > ?
               AND COALESCE(pc.phase, '') IN ('active', 'day0_window', 'pending_exit')
               AND (
                    COALESCE(pc.chain_shares, 0) > 0
                 OR (
                        COALESCE(pc.chain_shares, 0) = 0
                    AND COALESCE(pc.shares, 0) > 0
                    AND COALESCE(pc.chain_state, '') = 'synced'
                    )
               )
               AND EXISTS (
                    SELECT 1
                      FROM position_events pe
                     WHERE pe.position_id = pc.position_id
                       AND pe.event_type = 'EXIT_ORDER_REJECTED'
                       AND pe.occurred_at >= ?
                       AND COALESCE(json_extract(pe.payload_json, '$.error'), '') = 'allocator_not_configured'
               )
             ORDER BY pc.next_exit_retry_at, pc.position_id
            """,
            (now_iso, recent_cutoff),
        ).fetchall()
    except Exception as exc:  # noqa: BLE001 - maintenance must not crash monitor.
        logger.warning("Allocator-config exit-retry release query failed closed: %s", exc)
        return {"released": 0, "position_ids": [], "error": str(exc)}
    position_ids = [str(row[0]) for row in rows if str(row[0] or "")]
    if not position_ids:
        return {"released": 0, "position_ids": []}
    released = _append_exit_retry_release_events_and_update_projection(
        conn,
        position_ids,
        observed_at=observed_at,
        release_reason="ALLOCATOR_CONFIGURED_AFTER_REFRESH",
        release_error="allocator_not_configured_released",
    )
    changed = int(released.get("released", 0) or 0)
    position_ids = list(released.get("position_ids", []) or [])
    id_set = set(position_ids)
    for pos in getattr(portfolio, "positions", []) or []:
        if str(getattr(pos, "trade_id", "")) in id_set:
            pos.next_exit_retry_at = now_iso
    logger.info(
        "Allocator configured; released %d allocator-not-configured exit retries: %s",
        changed,
        position_ids,
    )
    return released


def _refresh_global_allocator_for_held_position_monitor(conn, portfolio) -> dict:
    """Configure risk allocator before held-position exit decisions run.

    The held-position monitor is an independent live lane and can run before the
    EDLI reactor's allocator refresh after daemon restart. It must not reach the
    executor with unconfigured risk singletons, because that turns real exit
    decisions into ``allocator_not_configured`` backoff.
    """

    from src.control.heartbeat_supervisor import summary as _heartbeat_summary
    from src.control.ws_gap_guard import summary as _ws_gap_summary
    from src.risk_allocator import configure_global_allocator, refresh_global_allocator
    from src.riskguard.riskguard import get_current_level

    try:
        _baseline = float(getattr(portfolio, "daily_baseline_total", 0.0) or 0.0)
        _current_bankroll = float(getattr(portfolio, "bankroll", 0.0) or 0.0)
        _drawdown_pct = (
            max(((_baseline - _current_bankroll) / _baseline) * 100.0, 0.0)
            if _baseline > 0.0
            else 0.0
        )
        result = refresh_global_allocator(
            conn,
            ledger={
                "current_drawdown_pct": _drawdown_pct,
                "risk_level": get_current_level().value,
            },
            heartbeat=_heartbeat_summary(),
            ws_status=_ws_gap_summary(),
        )
        logger.info(
            "held-position monitor allocator refresh: configured=%r drawdown_pct=%.3f",
            result.get("configured"),
            _drawdown_pct,
        )
        return result
    except Exception as exc:  # noqa: BLE001 - fail closed with explicit state.
        try:
            configure_global_allocator(None, None)
        except Exception:  # noqa: BLE001
            pass
        logger.error(
            "held-position monitor allocator refresh FAILED: %s; exit submit remains fail-closed",
            exc,
            exc_info=True,
        )
        return {
            "configured": False,
            "fail_closed": True,
            "error": str(exc),
            "entry": {"allow_submit": False, "reason": "allocator_not_configured"},
        }


def _check_monitor_cadence_watchdog(conn, summary: dict) -> dict | None:
    """Flag when MONITOR_REFRESHED cadence has lapsed beyond ~2× the interval.

    Reads the newest canonical MONITOR_REFRESHED occurred_at from position_events
    (same trade DB this conn owns) and compares to now. Detection only — records
    the gap in ``summary`` and logs a warning so operator supervision can act;
    never restarts or back-fills. Returns the watchdog record dict when a gap is
    flagged, else None. Fail-soft: any read/parse error returns None.
    """
    if conn is None:
        return None
    threshold_seconds = _EXIT_MONITOR_INTERVAL_SECONDS * _MONITOR_CADENCE_GAP_FACTOR
    try:
        row = conn.execute(
            """
            SELECT MAX(
                       (
                           SELECT pe.occurred_at
                             FROM position_events pe
                            WHERE pe.position_id = pc.position_id
                              AND pe.event_type = 'MONITOR_REFRESHED'
                            ORDER BY pe.sequence_no DESC
                            LIMIT 1
                       )
                   )
              FROM position_current pc
             WHERE pc.phase IN ('active', 'day0_window', 'pending_exit', 'quarantined')
            """
        ).fetchone()
    except Exception:
        return None
    if row is None or row[0] is None:
        return None
    last_refresh_raw = str(row[0])
    try:
        last_refresh = datetime.fromisoformat(last_refresh_raw.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if last_refresh.tzinfo is None:
        last_refresh = last_refresh.replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    gap_seconds = (now - last_refresh.astimezone(timezone.utc)).total_seconds()
    summary["monitor_cadence_gap_seconds"] = round(gap_seconds, 1)
    if gap_seconds <= threshold_seconds:
        return None
    record = {
        "last_monitor_refreshed_at": last_refresh_raw,
        "observed_at": now.isoformat(),
        "gap_seconds": round(gap_seconds, 1),
        "interval_seconds": _EXIT_MONITOR_INTERVAL_SECONDS,
        "threshold_seconds": threshold_seconds,
        "gap_factor": round(gap_seconds / _EXIT_MONITOR_INTERVAL_SECONDS, 2),
    }
    summary["monitor_cadence_gap_flagged"] = record
    logger.warning(
        "MONITOR_CADENCE_GAP: last MONITOR_REFRESHED was %s (%.1fs ago, %.1f× the "
        "%.0fs interval > %.1f× threshold). exit_monitor cadence lapsed — likely a "
        "daemon/scheduler process gap (operator supervision, out of code).",
        last_refresh_raw,
        gap_seconds,
        gap_seconds / _EXIT_MONITOR_INTERVAL_SECONDS,
        _EXIT_MONITOR_INTERVAL_SECONDS,
        _MONITOR_CADENCE_GAP_FACTOR,
    )
    return record


def run_exit_monitor_cycle(
    *,
    held_position_monitor_active: threading.Event,
    mark_held_position_monitor_complete: Callable[[], None],
) -> None:
    """Scheduler entrypoint (R4-b extraction from src/main.py::_exit_monitor_cycle).

    Standalone exit-lifecycle monitoring job owned by the order daemon.

    The chain-truth READ phase was lifted to the P4 post-trade-capital daemon.
    This order-runtime job keeps only the live exit-SUBMIT lane: held-position
    monitoring, exit preflight, pending-exit state transitions, and gated sell
    order submission when ``real_order_submit_enabled`` is true.

    ``held_position_monitor_active``/``mark_held_position_monitor_complete``
    are injected from src.main: they are cross-job scheduling-coordination
    primitives (other EDLI jobs defer while this one runs), so main.py — the
    dispatcher — retains ownership of the Event/callback; this module only
    consumes them for its own run/complete signalling.

    Called from the main daemon's ``exit_monitor`` scheduler job (2-minute
    cadence). Behavior-preserving relocation — was inline in src/main.py.
    """
    from src.config import settings
    from src.data.polymarket_client import PolymarketClient
    from src.engine.cycle_runner import (
        _execute_monitoring_phase,
        get_connection,
        get_tracker,
        load_portfolio,
        save_tracker,
        save_portfolio,
    )
    from src.observability.scheduler_health import _write_scheduler_health
    from src.state.canonical_write import commit_then_export
    from src.state.decision_chain import CycleArtifact
    from src.state.decision_chain import store_artifact

    _settings_source = settings._data if hasattr(settings, "_data") else settings
    edli_cfg = _settings_source.get("edli", {}) if isinstance(_settings_source, dict) else {}
    real_order_submit_enabled = bool(edli_cfg.get("real_order_submit_enabled", False))
    if held_position_monitor_active.is_set():
        logger.warning("exit_monitor skipped: previous monitor cycle is still running")
        return
    held_position_monitor_active.set()

    conn = get_connection()
    if conn is None:
        logger.warning("exit_monitor: DB write-lock degrade — skipping cycle")
        mark_held_position_monitor_complete()
        return

    summary: dict = {"monitors": 0, "exits": 0}
    # FIX 2c (2026-06-20): detect a lapsed MONITOR_REFRESHED cadence (whole-book
    # silence) on the first cycle after recovery. Detection only; the underlying
    # daemon supervision is operator infra.
    try:
        _check_monitor_cadence_watchdog(conn, summary)
    except Exception as _wd_exc:  # noqa: BLE001 — watchdog must never break the cycle
        logger.warning("exit_monitor: cadence watchdog failed (non-fatal): %s", _wd_exc)
    try:
        portfolio = load_portfolio()
        held_monitor_allocator_refresh = _refresh_global_allocator_for_held_position_monitor(
            conn,
            portfolio,
        )
        summary["held_monitor_allocator_refresh"] = held_monitor_allocator_refresh
        if held_monitor_allocator_refresh.get("configured"):
            summary["held_monitor_allocator_retry_release"] = (
                _release_allocator_config_blocked_exit_retries_after_refresh(
                    conn,
                    portfolio,
                    observed_at=datetime.now(timezone.utc),
                )
            )
        with PolymarketClient() as clob:
            tracker = get_tracker()
            artifact = CycleArtifact(
                mode="exit_monitor",
                started_at=datetime.now(timezone.utc).isoformat(),
                summary=summary,
            )
            portfolio_dirty = False
            tracker_dirty = False
            try:
                portfolio_dirty, tracker_dirty = _execute_monitoring_phase(
                    conn,
                    clob,
                    portfolio,
                    artifact,
                    tracker,
                    summary,
                    exit_order_submit_enabled=real_order_submit_enabled,
                    run_exit_preflight=True,
                )
            except Exception as exc:
                logger.error(
                    "exit_monitor: monitoring phase failed (non-fatal): %s",
                    exc,
                    exc_info=True,
                )
                summary["monitoring_error"] = str(exc)

            # DAY0 resting-order cancel sweep (adversarial review
            # 2026-06-10 fix 2 — finding 4 "standing free option"). Cancels OUR
            # open resting ENTRY orders whose day0 bin is hard-fact dead for the
            # order's side, or whose family is oracle-anomaly paused. Cancels
            # only REDUCE standing risk; gated to live-submit mode because in
            # submit-disabled posture no real resting orders of ours exist (and
            # the venue cancel is a real API call). Fail-soft.
            if real_order_submit_enabled and bool(
                edli_cfg.get("day0_dead_bin_order_cancel_enabled", True)
            ):
                try:
                    from src.config import runtime_cities_by_name
                    from src.execution.day0_hard_fact_exit import (
                        cancel_day0_dead_bin_resting_entries,
                    )

                    cancelled = cancel_day0_dead_bin_resting_entries(
                        clob=clob,
                        conn=conn,
                        cities_by_name=runtime_cities_by_name(),
                    )
                    if cancelled:
                        summary["day0_dead_bin_orders_cancelled"] = cancelled
                except Exception as exc:  # noqa: BLE001 — sweep is additive
                    logger.warning(
                        "exit_monitor: day0 dead-bin cancel sweep failed (non-fatal): %s",
                        exc,
                    )

        # INV-17 / DT#1: commit the DB transaction (monitoring state transitions) FIRST,
        # then export the derived portfolio/tracker JSON with the committed artifact id —
        # so canonical_write.detect_stale_portfolio's marker stays valid and JSON can
        # never lead the DB.
        _aid_box: list = [None]

        def _db_op():
            _aid_box[0] = store_artifact(conn, artifact)
            return _aid_box[0]

        def _export_portfolio():
            if portfolio_dirty:
                save_portfolio(
                    portfolio,
                    last_committed_artifact_id=_aid_box[0],
                    source="exit_monitor",
                )

        def _export_tracker():
            if tracker_dirty:
                save_tracker(tracker)

        commit_then_export(
            conn, db_op=_db_op, json_exports=[_export_portfolio, _export_tracker]
        )
    except Exception as exc:
        logger.error(
            "exit_monitor: unexpected error: %s", exc, exc_info=True
        )
    finally:
        try:
            conn.close()
        except Exception:
            pass
        mark_held_position_monitor_complete()

    # EDLI status-summary freshness writer (release-gate surface).
    # In EDLI event-driven modes run_cycle() is never called, so the legacy
    # _export_status -> write_cycle_pulse path is silent and state/status_summary.json
    # goes stale -> the live-release gate fails status_summary / edli_stage_readiness.
    # This exit monitor runs under ALL EDLI modes, so emit a genuine business-plane
    # status pulse here each cycle. write_cycle_pulse re-reads the live DB read model
    # (open orders, risk, portfolio, capability) -> it reflects REAL current state,
    # never a hardcoded healthy value. Non-fatal: a pulse failure must not abort the
    # chain-sync job. Authority: fix/edli-stage-readiness-2026-05-31 (status_summary).
    try:
        from src.observability.status_summary import write_cycle_pulse
        write_cycle_pulse(summary)
    except Exception as exc:
        logger.error(
            "exit_monitor: status pulse failed (non-fatal): %s",
            exc,
            exc_info=True,
        )

    _write_scheduler_health(
        "exit_monitor",
        failed=False,
        extra={
            "exit_order_submit_enabled": real_order_submit_enabled,
            "monitors": summary.get("monitors", 0),
            "exits": summary.get("exits", 0),
        },
    )
