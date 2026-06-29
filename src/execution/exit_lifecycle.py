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
import sqlite3
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
# Transient submit-channel gap: retry ~each monitor cycle and NEVER give up, so a
# correct reversal exit sells once the channel recovers instead of being abandoned.
CHANNEL_NOT_READY_COOLDOWN_SECONDS = 120


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
        or "venue_read_transient" in e
        or "transientvenueread" in e
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


def mark_market_closed_hold_to_settlement(
    position: Position,
    *,
    reason: str = "MARKET_CLOSED_AWAITING_SETTLEMENT",
    error: str = "market_closed_non_accepting_orders",
    conn: sqlite3.Connection | None = None,
) -> None:
    """Record a market-closed hold without manufacturing a sell failure.

    Once the market is closed, quote freshness is no longer a solvable exit
    precondition. That is a held-to-settlement monitor fact, not an
    EXIT_ORDER_REJECTED event: no sell was submitted, no venue order failed,
    and the position must keep flowing through held-position redecision and
    settlement harvesting.
    """

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
    position.exit_reason = ""
    position.last_exit_error = f"{reason}:{error}"[:500]
    validations = list(getattr(position, "applied_validations", []) or [])
    if reason not in validations:
        validations.append(reason)
    position.applied_validations = validations
    _dual_write_market_closed_hold_if_available(
        conn,
        position,
        reason=reason,
        error=error,
    )


_CLOSED_HOLD_MONITOR_COLUMNS = (
    "last_monitor_prob",
    "last_monitor_prob_is_fresh",
    "last_monitor_edge",
    "last_monitor_market_price",
    "last_monitor_market_price_is_fresh",
    "last_monitor_best_bid",
    "last_monitor_best_ask",
    "last_monitor_market_vig",
)


def _position_current_columns(conn: sqlite3.Connection) -> set[str]:
    try:
        return {
            str(row[1])
            for row in conn.execute("PRAGMA table_info(position_current)").fetchall()
        }
    except Exception:
        return set()


def _read_current_monitor_snapshot(
    conn: sqlite3.Connection,
    *,
    trade_id: str,
) -> dict[str, object] | None:
    columns = [
        column
        for column in _CLOSED_HOLD_MONITOR_COLUMNS
        if column in _position_current_columns(conn)
    ]
    if not columns:
        return None
    row = conn.execute(
        f"SELECT {', '.join(columns)} FROM position_current WHERE position_id = ?",
        (trade_id,),
    ).fetchone()
    if row is None:
        return None
    return {column: row[index] for index, column in enumerate(columns)}


def _apply_monitor_snapshot_for_closed_hold(
    conn: sqlite3.Connection,
    position: Position,
    *,
    trade_id: str,
) -> None:
    snapshot = _read_current_monitor_snapshot(conn, trade_id=trade_id)
    if not snapshot:
        position.last_monitor_prob = None  # type: ignore[assignment]
        position.last_monitor_prob_is_fresh = False
        position.last_monitor_edge = None  # type: ignore[assignment]
        position.last_monitor_market_price = None
        position.last_monitor_market_price_is_fresh = False
        position.last_monitor_best_bid = None
        position.last_monitor_best_ask = None
        position.last_monitor_market_vig = None
        return

    for column, value in snapshot.items():
        if column in {
            "last_monitor_prob_is_fresh",
            "last_monitor_market_price_is_fresh",
        }:
            setattr(position, column, bool(value))
        else:
            setattr(position, column, value)


def _dual_write_market_closed_hold_if_available(
    conn: sqlite3.Connection | None,
    position: Position,
    *,
    reason: str,
    error: str,
) -> bool:
    """Persist a no-transition Day0 monitor hold for closed markets."""

    if conn is None:
        return False
    trade_id = str(getattr(position, "trade_id", "") or "")
    if not trade_id:
        return False
    try:
        from src.engine.lifecycle_events import build_monitor_refreshed_canonical_write
        from src.state.db import append_many_and_project

        sequence_no = _next_canonical_sequence_no(conn, trade_id)
        occurred_at = datetime.now(timezone.utc).isoformat()
        _apply_monitor_snapshot_for_closed_hold(conn, position, trade_id=trade_id)
        position.last_monitor_at = occurred_at
        events, projection = build_monitor_refreshed_canonical_write(
            position,
            sequence_no=sequence_no,
            phase_after=LifecyclePhase.DAY0_WINDOW.value,
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
        event["occurred_at"] = occurred_at
        event["venue_status"] = None
        event["payload_json"] = json.dumps(payload, default=str, sort_keys=True)
        projection["updated_at"] = occurred_at
        projection["phase"] = LifecyclePhase.DAY0_WINDOW.value
        projection["order_status"] = getattr(position, "order_status", "") or "filled"
        projection["exit_reason"] = ""
        projection["exit_retry_count"] = 0
        projection["next_exit_retry_at"] = ""
        append_many_and_project(conn, [event], projection)
        return True
    except Exception as exc:  # noqa: BLE001 - monitor can retry next cycle
        logger.warning(
            "market closed hold projection failed for %s: %s",
            trade_id,
            exc,
        )
        return False


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


def is_exit_cooldown_active(position: Position) -> bool:
    """Check if position is in retry cooldown period."""
    if position.exit_state != "retry_pending":
        return False
    deadline = _parse_iso(position.next_exit_retry_at)
    if deadline is None:
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
        append_many_and_project(conn, events, projection)
        return True
    except Exception as exc:  # noqa: BLE001 - fail closed to in-memory dust hold
        logger.warning(
            "chain dust projection correction failed for %s: %s",
            trade_id,
            exc,
        )
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

    if position.chain_state != "exit_pending_missing":
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
    projection_changed = False
    if chain_balance_units is not None and chain_balance_shares is not None:
        local_shares_before, projection_changed = _sync_position_to_chain_dust(
            position,
            chain_balance_units=chain_balance_units,
            chain_balance_shares=chain_balance_shares,
            asset_id=asset_id,
        )
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
        if projection_changed and event_already_recorded:
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


def _below_snapshot_min_order_error(position: Position, snapshot_context: dict[str, object]) -> str:
    min_order = _positive_decimal(snapshot_context.get("executable_snapshot_min_order_size"))
    shares = _positive_decimal(getattr(position, "effective_shares", None))
    if min_order is None or shares is None or shares >= min_order:
        return ""
    return f"executable_snapshot_gate: size {shares} is below snapshot min_order_size {min_order}"


def _dual_write_canonical_pending_exit_if_available(
    conn: sqlite3.Connection | None,
    position: Position,
    *,
    reason: str,
    error: str,
    event_type: str = "EXIT_ORDER_REJECTED",
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
        retry_reason = f"{exit_context.exit_reason or 'EXIT'} [INCOMPLETE_CONTEXT]"
        _mark_exit_retry(position, reason=retry_reason, error="missing_current_market_price", conn=conn)
        return "exit_blocked: incomplete_context"
    if not is_red_force_exit and not exit_context.current_market_price_is_fresh:
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

        log_pending_exit_recovery_event(
            conn,
            position,
            event_type="EXIT_INTENT",
            reason=exit_intent.reason,
            error="",
        )
        _commit_before_exit_venue_io(conn, stage="exit_intent")

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

    snapshot_context = _latest_or_capture_exit_snapshot_context(
        conn,
        clob,
        position,
        token_id,
    )
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

    if conn is not None:
        try:
            _refresh_exit_collateral_snapshot_for_submit(
                conn,
                token_id=token_id,
                shares=position.effective_shares,
            )
        except CollateralInsufficient as exc:
            collateral_reason = str(exc)
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
                retry_reason = f"{exit_context.exit_reason} [CANCEL_UNKNOWN: no_command_row]"
                _mark_exit_retry(position, reason=retry_reason, error="cancel_command_row_missing", conn=conn)
                log_pending_exit_recovery_event(
                    conn,
                    position,
                    event_type="EXIT_ORDER_REJECTED",
                    reason=retry_reason,
                    error="cancel_command_row_missing",
                )
                log_exit_retry_event(conn, position, reason=retry_reason, error="cancel_command_row_missing")
                return "exit_blocked: cancel_unknown"
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

    _mark_pending_exit(position)
    position.exit_state = "exit_intent"
    _dual_write_canonical_pending_exit_if_available(
        conn,
        position,
        reason=exit_intent.reason or "EXIT_INTENT",
        error="",
        event_type="EXIT_INTENT",
    )
    _commit_before_exit_venue_io(conn, stage="exit_intent")

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
        row = conn.execute(
            """
            SELECT snapshot_id, min_tick_size, min_order_size, neg_risk
              FROM executable_market_snapshots
             WHERE freshness_deadline >= ?
               AND selected_outcome_token_id = ?
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
    if context or conn is None or clob is None or not token_id:
        return context

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
        return {}

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
            return {}
        if not siblings:
            logger.warning(
                "Exit executable snapshot capture blocked for %s: no Gamma siblings for market_id=%s",
                position.trade_id,
                market_id,
            )
            return {}
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
            return {}
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
        return {}


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
    fallback = str(getattr(position, "order_id", "") or "").strip()
    if not fallback:
        return ""
    if conn is not None:
        trade_id = str(getattr(position, "trade_id", "") or "").strip()
        if not trade_id:
            return ""
        try:
            row = conn.execute(
                """
                SELECT 1
                  FROM venue_commands
                 WHERE position_id = ?
                   AND intent_kind = 'EXIT'
                   AND venue_order_id = ?
                 LIMIT 1
                """,
                (trade_id, fallback),
            ).fetchone()
        except sqlite3.OperationalError:
            row = None
        return fallback if row is not None else ""

    order_status = str(getattr(position, "order_status", "") or "").strip().lower()
    return fallback if order_status.startswith("sell_") else ""


def check_pending_exits(
    portfolio: PortfolioState,
    clob,
    conn: sqlite3.Connection | None = None,
) -> dict:
    """Check fill status for positions with pending sell orders.

    Called at start of each cycle, before monitor phase.
    Returns: {"filled": int, "retried": int, "unchanged": int, "filled_positions": list[Position]}
    """
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

    for pos in list(portfolio.positions):
        if _runtime_state_value(pos) in _PENDING_EXIT_SCAN_INACTIVE_STATES:
            stats["skipped_inactive"] = stats.get("skipped_inactive", 0) + 1
            continue
        if pos.exit_state not in ("sell_placed", "sell_pending", "exit_intent") and str(
            getattr(pos, "order_status", "") or ""
        ) != "sell_pending_confirmation":
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
                pos.exit_state = ""
                _release_pending_exit(pos)
                stats["unchanged"] += 1
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

    return stats


def check_pending_retries(position: Position, conn: sqlite3.Connection | None = None) -> bool:
    """Check if a retry-pending position's cooldown has expired.

    Returns True if position is ready for a new exit attempt.
    """
    if position.exit_state == "backoff_exhausted":
        return False  # Hold to settlement, stop retrying

    if position.exit_state != "retry_pending":
        return False

    if is_exit_cooldown_active(position):
        return False  # Still cooling down

    # Cooldown expired — position is eligible for exit re-evaluation
    position.exit_state = ""  # Reset to allow new exit attempt
    _release_pending_exit(position)
    if conn is not None:
        from src.state.db import log_exit_retry_released_event
        log_exit_retry_released_event(conn, position)
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

    position.exit_retry_count += 1
    position.last_exit_error = error[:500]

    if position.exit_retry_count >= MAX_EXIT_RETRIES:
        position.exit_state = "backoff_exhausted"
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
