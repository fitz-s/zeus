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

import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from src.execution.collateral import check_sell_collateral
from src.execution.executor import OrderResult, create_exit_order_intent, execute_exit_order
from src.state.lifecycle_manager import (
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
    executable_snapshot_min_tick_size: str | None = None,
    executable_snapshot_min_order_size: str | None = None,
    executable_snapshot_neg_risk: bool | None = None,
) -> OrderResult:
    """Thin compatibility adapter over the executor-level exit-order path."""

    return execute_exit_order(
        create_exit_order_intent(
            trade_id=trade_id,
            token_id=token_id,
            shares=shares,
            current_price=current_price,
            best_bid=best_bid,
            executable_snapshot_id=executable_snapshot_id,
            executable_snapshot_min_tick_size=executable_snapshot_min_tick_size,
            executable_snapshot_min_order_size=executable_snapshot_min_order_size,
            executable_snapshot_neg_risk=executable_snapshot_neg_risk,
        )
    )


# CLOB statuses that indicate a fill
FILL_STATUSES = frozenset({"MATCHED", "FILLED"})
VOID_STATUSES = frozenset({"CANCELLED", "CANCELED", "EXPIRED", "REJECTED"})
EXIT_LIFECYCLE_OWNED_STATES = frozenset({"exit_intent", "sell_placed", "sell_pending", "retry_pending"})
EXIT_LIFECYCLE_RECOVERY_STATES = frozenset({"exit_intent", "retry_pending", "backoff_exhausted"})


def _parse_iso(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


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


def _has_canonical_position_history(conn: sqlite3.Connection, position_id: str) -> bool:
    try:
        row = conn.execute(
            "SELECT 1 FROM position_events WHERE position_id = ? LIMIT 1",
            (position_id,),
        ).fetchone()
    except sqlite3.OperationalError:
        return False
    return row is not None


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
    has_history = _has_canonical_position_history(conn, trade_id)

    if not has_history:
        # Backfill canonical entry events for positions that only exist in
        # the legacy table.  Create an entry-phase snapshot so
        # build_entry_canonical_write produces the standard three-event
        # sequence (OPEN_INTENT / ORDER_POSTED / ORDER_FILLED → active).
        #
        # T4.1b 2026-04-23 (D4 Option E): these legacy positions have no
        # captured `DecisionEvidence` (the decision frame predates the
        # T4.1b accept-path wiring). Emit the `decision_evidence_reason`
        # sentinel "backfill_legacy_position" into the ENTRY_ORDER_POSTED
        # payload so T4.2-Phase1 exit-side audit can distinguish
        # missing-because-legacy from missing-because-bug. Without this
        # sentinel, audit_log_false_positive_rate would flag every legacy
        # position's exit as an asymmetry violation during Phase1 rollout.
        entry_snapshot = copy.copy(position)
        entry_snapshot.state = "entered"
        entry_snapshot.exit_state = ""
        try:
            entry_events, _ = build_entry_canonical_write(
                entry_snapshot,
                source_module="src.execution.exit_lifecycle:backfill",
                decision_evidence_reason="backfill_legacy_position",
            )
        except Exception as exc:
            logger.debug(
                "Canonical entry backfill failed for %s: %s", trade_id, exc,
            )
            return False
        exit_seq = len(entry_events) + 1
    else:
        entry_events = []
        exit_seq = _next_canonical_sequence_no(conn, trade_id)

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


def handle_exit_pending_missing(portfolio: PortfolioState, position: Position) -> dict:
    """Own the `exit_pending_missing` escalation path for pending exits."""

    if position.chain_state != "exit_pending_missing":
        return {"action": "ignore", "position": None}
    _mark_pending_exit(position)
    if position.exit_state == "backoff_exhausted":
        return {"action": "skip", "position": None}
    if position.exit_state in EXIT_LIFECYCLE_RECOVERY_STATES:
        closed = mark_admin_closed(portfolio, position.trade_id, "EXIT_CHAIN_MISSING_REVIEW_REQUIRED")
        return {"action": "closed", "position": closed}
    if position.exit_state in EXIT_LIFECYCLE_OWNED_STATES:
        return {"action": "skip", "position": None}
    return {"action": "ignore", "position": None}


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
    if exit_context.current_market_price is None:
        retry_reason = f"{exit_context.exit_reason or 'EXIT'} [INCOMPLETE_CONTEXT]"
        _mark_exit_retry(position, reason=retry_reason, error="missing_current_market_price")
        return "exit_blocked: incomplete_context"

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

    # Pre-sell collateral check (fail-closed)
    can_sell, collateral_reason = check_sell_collateral(
        position.entry_price,
        position.effective_shares,
        clob,
        token_id=exit_intent.token_id,
    )
    if not can_sell:
        retry_reason = f"{exit_context.exit_reason} [COLLATERAL: {collateral_reason}]"
        _mark_exit_retry(
            position,
            reason=retry_reason,
            error=collateral_reason or "",
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
            _mark_exit_retry(position, reason=retry_reason, error="cancel_order_unavailable")
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
                _mark_exit_retry(position, reason=retry_reason, error="cancel_command_row_missing")
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
                _mark_exit_retry(position, reason=retry_reason, error=outcome.reason or outcome.status)
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
                _mark_exit_retry(position, reason=f"{exit_context.exit_reason} [CANCEL_UNKNOWN]", error=str(exc)[:500])
                return "exit_blocked: cancel_unknown"
            if outcome.status != "CANCELED":
                _mark_exit_retry(position, reason=f"{exit_context.exit_reason} [CANCEL_{outcome.status}]", error=outcome.reason or outcome.status)
                return f"exit_blocked: cancel_{outcome.status.lower()}"

    # Determine the token to sell
    token_id = exit_intent.token_id
    if not token_id:
        retry_reason = f"{exit_intent.reason} [NO_TOKEN_ID]"
        _mark_exit_retry(position, reason=retry_reason, error="no_token_id")
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

    snapshot_context = _latest_exit_snapshot_context(conn, token_id)
    _mark_pending_exit(position)
    position.exit_state = "exit_intent"

    try:
        raw_sell_result = place_sell_order(
            trade_id=position.trade_id,
            token_id=token_id,
            shares=position.effective_shares,
            current_price=current_market_price,
            best_bid=best_bid,
            **snapshot_context,
        )
        sell_result = _coerce_sell_result(position.trade_id, raw_sell_result)

        if sell_result.status == "rejected":
            sell_error = sell_result.reason or "sell_rejected"
            retry_reason = f"{exit_context.exit_reason} [SELL_ERROR: {sell_error}]"
            _mark_exit_retry(
                position,
                reason=retry_reason,
                error=sell_error,
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
            status, _ = _check_order_fill(clob, order_id)
            if status in FILL_STATUSES:
                actual_price = _extract_fill_price(sell_result, current_market_price, best_bid)
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
               AND (
                 selected_outcome_token_id = ?
                 OR yes_token_id = ?
                 OR no_token_id = ?
               )
             ORDER BY captured_at DESC, snapshot_id DESC
             LIMIT 1
            """,
            (now_s, token_id, token_id, token_id),
        ).fetchone()
    except sqlite3.OperationalError:
        row = None
    finally:
        conn.row_factory = saved
    if row is None:
        return {}
    return {
        "executable_snapshot_id": str(row["snapshot_id"]),
        "executable_snapshot_min_tick_size": str(row["min_tick_size"]),
        "executable_snapshot_min_order_size": str(row["min_order_size"]),
        "executable_snapshot_neg_risk": bool(row["neg_risk"]),
    }


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
            log_pending_exit_recovery_event,
            log_pending_exit_status_event,
            log_exit_retry_event,
        )

    stats = {"filled": 0, "retried": 0, "unchanged": 0, "filled_positions": []}

    for pos in list(portfolio.positions):
        if pos.exit_state not in ("sell_placed", "sell_pending", "exit_intent"):
            continue
        _mark_pending_exit(pos)

        # exit_intent with no order ID = stranded from exception during place_sell_order
        if pos.exit_state == "exit_intent":
            if not pos.last_exit_error:
                pos.exit_state = ""
                _release_pending_exit(pos)
                stats["unchanged"] += 1
                continue
            _mark_exit_retry(pos, reason="STRANDED_EXIT_INTENT", error="exception_during_sell")
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

        if not pos.last_exit_order_id:
            _mark_exit_retry(pos, reason="SELL_NO_ORDER_ID", error="no_order_id")
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

        status, status_payload = _check_order_fill(clob, pos.last_exit_order_id)
        if conn is not None:
            if status:
                log_pending_exit_status_event(conn, pos, status=status)
            else:
                log_exit_fill_check_error_event(conn, pos, order_id=pos.last_exit_order_id)

        if status in FILL_STATUSES:
            # Filled! Close the position.
            actual_price = _extract_fill_price(
                status_payload,
                pos.last_monitor_market_price or pos.entry_price,
                getattr(pos, "last_monitor_best_bid", None),
            )
            exit_reason = pos.exit_reason or "DEFERRED_SELL_FILL"
            phase_before = _canonical_phase_before_for_economic_close(pos)
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
                        order_id=pos.last_exit_order_id,
                        fill_price=actual_price,
                        current_market_price=pos.last_monitor_market_price or pos.entry_price,
                        best_bid=getattr(pos, "last_monitor_best_bid", None),
                        timestamp=getattr(closed, "last_exit_at", None),
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
        elif status in VOID_STATUSES:
            _mark_exit_retry(pos, reason=f"SELL_{status}", error=status)
            if conn is not None:
                log_pending_exit_recovery_event(
                    conn,
                    pos,
                    event_type="EXIT_ORDER_VOIDED",
                    reason=f"SELL_{status}",
                    error=status,
                )
                log_exit_retry_event(conn, pos, reason=f"SELL_{status}", error=status)
            stats["retried"] += 1
        elif status == "":
            # Empty status = CLOB outage or API error. Don't stall forever.
            # After 3 consecutive unknown statuses, trigger retry to avoid
            # permanent stall.
            pos.exit_retry_count += 1
            if pos.exit_retry_count >= 3:
                _mark_exit_retry(pos, reason="SELL_STATUS_UNKNOWN", error="3_consecutive_unknown")
                if conn is not None:
                    log_exit_retry_event(conn, pos, reason="SELL_STATUS_UNKNOWN", error="3_consecutive_unknown")
                stats["retried"] += 1
            else:
                stats["unchanged"] += 1
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
            fill_price=sell_result.get("avgPrice") or sell_result.get("avg_price") or sell_result.get("price"),
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
    current_market_price: float,
    best_bid: Optional[float] = None,
) -> float:
    """Extract actual fill price from sell result. Fall back to best_bid or market."""
    if isinstance(sell_result, OrderResult) and sell_result.fill_price not in (None, ""):
        try:
            return float(sell_result.fill_price)
        except (TypeError, ValueError):
            pass
    for key in ("avgPrice", "avg_price", "price"):
        if isinstance(sell_result, dict) and key in sell_result and sell_result[key] not in (None, ""):
            try:
                return float(sell_result[key])
            except (TypeError, ValueError):
                continue
    return best_bid if best_bid is not None else current_market_price


def _mark_exit_retry(
    position: Position,
    reason: str,
    error: str = "",
    cooldown_seconds: int = DEFAULT_COOLDOWN_SECONDS,
) -> None:
    """Transition position to retry_pending with exponential backoff."""
    _mark_pending_exit(position)
    position.exit_retry_count += 1
    position.last_exit_error = error[:500]

    if position.exit_retry_count >= MAX_EXIT_RETRIES:
        position.exit_state = "backoff_exhausted"
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
