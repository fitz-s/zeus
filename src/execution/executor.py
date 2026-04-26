"""Order executor: limit-order-only execution engine. Spec §6.4.

Live mode only: places limit orders via Polymarket CLOB API.

Key rules:
- Limit orders ONLY (never market orders)
- Mode-based timeouts: Opening Hunt 4h, Update Reaction 1h, Day0 15min
- Whale toxicity detection: cancel on adjacent bin sweeps
- Share quantization: BUY rounds UP, SELL rounds DOWN (0.01 increments)
- Dynamic limit: if within 5% of best ask, jump to ask for guaranteed fill
"""

import logging
import math
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from src.config import get_mode, settings
from src.riskguard.discord_alerts import alert_trade
from src.contracts import (
    HeldSideProbability,
    NativeSidePrice,
    compute_native_limit_price,
    ExecutionIntent,
    EdgeContext,
    Direction,
)
from src.contracts.execution_price import (
    ExecutionPrice,
    ExecutionPriceContractError,
)
from src.types import BinEdge
from src.state.db import get_connection, get_trade_connection_with_world

logger = logging.getLogger(__name__)


# Mode-based fill timeout (seconds). Spec §6.4.
MODE_TIMEOUTS = {
    "opening_hunt": 4 * 3600,
    "update_reaction": 1 * 3600,
    "day0_capture": 15 * 60,
}


@dataclass
class OrderResult:
    """Result of an order attempt."""
    trade_id: str
    status: str  # "filled", "pending", "cancelled", "rejected"
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


def create_execution_intent(
    edge_context: EdgeContext,
    edge: BinEdge,
    size_usd: float,
    mode: str,
    market_id: str,
    token_id: str = "",
    no_token_id: str = "",
    best_ask: Optional[float] = None,
) -> ExecutionIntent:
    """Execution Planner: Generates the intent based on Fair Value Plane output."""
    if False: _ = edge.entry_method

    limit_offset = settings["execution"]["limit_offset_pct"]

    # Compute initial limit price in the native/held-side probability space.
    limit_price = compute_native_limit_price(
        HeldSideProbability(edge_context.p_posterior, edge.direction),
        NativeSidePrice(edge.vwmp, edge.direction),
        limit_offset=limit_offset,
    )

    # Dynamic limit price
    if best_ask is not None:
        gap = best_ask - limit_price
        if 0 < gap <= best_ask * 0.05:
            logger.info("Dynamic limit: jumping to best_ask %.3f (gap %.3f)", best_ask, gap)
            limit_price = best_ask
        elif gap > best_ask * 0.05:
            logger.warning("Limit %.3f far below best_ask %.3f (gap %.1f%%) — may not fill",
                           limit_price, best_ask, gap / best_ask * 100)
                           
    if edge.direction.value == "buy_yes":
        order_token = token_id
    elif edge.direction.value == "buy_no":
        order_token = no_token_id
    else:
        raise ValueError(f"Strict token routing failed: unsupported token direction '{edge.direction}'")

    if mode not in MODE_TIMEOUTS:
        raise ValueError(f"Unknown execution mode '{mode}' cannot default to timeout. Explicit runtime mode required.")
    timeout = MODE_TIMEOUTS[mode]
    
    return ExecutionIntent(
        direction=Direction(edge.direction),
        target_size_usd=size_usd,
        limit_price=limit_price,
        toxicity_budget=0.05,
        max_slippage=0.02,
        is_sandbox=False,
        market_id=market_id,
        token_id=order_token,
        timeout_seconds=timeout,
        decision_edge=edge.edge,
    )

def execute_intent(
    intent: ExecutionIntent,
    edge_vwmp: float,  # Phase 2: remove this parameter (dead after _paper_fill deletion)
    label: str,
) -> OrderResult:
    """Execute the instantiated live domain intent."""

    trade_id = str(uuid.uuid4())[:12]

    limit_price = intent.limit_price

    # V6: Compute shares with proper quantization
    shares = intent.target_size_usd / limit_price if limit_price > 0 else 0
    shares = math.ceil(shares * 100 - 1e-9) / 100.0  # BUY: round UP

    if not intent.token_id:
        return OrderResult(
            trade_id=trade_id, status="rejected",
            reason=f"No token_id provided for intent",
        )

    return _live_order(
        trade_id, intent, shares
    )


def create_exit_order_intent(
    *,
    trade_id: str,
    token_id: str,
    shares: float,
    current_price: float,
    best_bid: Optional[float] = None,
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
) -> OrderResult:
    """Place a live sell order via the executor and return a normalized OrderResult.

    Phase order (INV-30):
      1. Price derivation + NaN guard (pure, no I/O)
      2. build: VenueCommand + IdempotencyKey (pure, no I/O)
      3. persist: insert_command (INTENT_CREATED) + append_event (SUBMIT_REQUESTED)
      4. submit: client.place_limit_order (SDK call)
      5. ack: append_event SUBMIT_ACKED / SUBMIT_REJECTED / SUBMIT_UNKNOWN
    """
    from src.data.polymarket_client import PolymarketClient
    from src.execution.command_bus import IdempotencyKey, IntentKind, VenueCommand, CommandState
    from src.state.venue_command_repo import insert_command, append_event, get_command

    current_price = intent.current_price
    best_bid = intent.best_bid
    # T5.b 2026-04-23: replace bare 0.01 magic with TickSize typed
    # contract. TickSize.for_market resolves per-token tick size (all
    # Polymarket weather markets currently share $0.01, but the
    # classmethod is the single truth surface for future per-market
    # differentiation).
    from src.contracts.tick_size import TickSize
    tick = TickSize.for_market(token_id=intent.token_id)
    base_price = current_price - tick.value
    limit_price = base_price

    if best_bid is not None and best_bid < base_price:
        slippage = current_price - best_bid
        if current_price > 0 and slippage / current_price <= 0.03:
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
    limit_price = tick.clamp_to_valid_range(limit_price)

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

    # -----------------------------------------------------------------------
    # build phase — pure, no I/O (INV-30)
    # -----------------------------------------------------------------------
    # Derive a synthetic decision_id from trade_id when the caller has not
    # supplied a real one. P1.S5 will wire real decision_id from upstream;
    # for now this ensures IdempotencyKey.from_inputs gets a non-empty string.
    effective_decision_id = decision_id or f"exit:{intent.trade_id}"
    idem = IdempotencyKey.from_inputs(
        decision_id=effective_decision_id,
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
    # -----------------------------------------------------------------------
    # Post-critic CRITICAL/HIGH (2026-04-26): fallback uses
    # get_trade_connection_with_world() because that's where init_schema
    # actually runs (src/main.py:499-501); get_connection() targets the
    # legacy zeus.db where venue_command tables do not exist. Pre-fix every
    # production live order would have raised OperationalError. Wrapped in
    # try/finally below so the fallback connection is always closed.
    _own_conn = conn is None
    if _own_conn:
        conn = get_trade_connection_with_world()
    if not decision_id:
        logger.warning(
            "EXECUTOR: synthetic decision_id %s — retry-idempotency NOT guaranteed; "
            "pass decision_id explicitly (P1.S5 will wire upstream)",
            effective_decision_id,
        )
    try:
        try:
            insert_command(
                conn,
                command_id=command_id,
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
            )
            append_event(
                conn,
                command_id=command_id,
                event_type="SUBMIT_REQUESTED",
                occurred_at=now_str,
            )
            if not _own_conn:
                pass  # caller manages commit
            else:
                conn.commit()
        except sqlite3.IntegrityError as exc:
            # Idempotency key collision: a command with identical inputs already
            # exists. Do NOT submit — the existing command is the canonical record.
            logger.warning(
                "execute_exit_order: idempotency key collision for trade_id=%s idem=%s: %s",
                intent.trade_id, idem.value, exc,
            )
            from src.state.venue_command_repo import find_command_by_idempotency_key
            from src.execution.command_bus import VenueCommand, CommandState
            existing_row = find_command_by_idempotency_key(conn, idem.value)
            if existing_row is not None:
                existing = VenueCommand.from_row(existing_row)
                s = existing.state
                if s in (CommandState.ACKED, CommandState.PARTIAL):
                    collision_result = OrderResult(
                        trade_id=intent.trade_id,
                        status="pending",
                        reason="idempotency_collision: prior attempt acked",
                        submitted_price=limit_price,
                        shares=shares,
                        order_role="exit",
                        intent_id=intent.intent_id,
                        external_order_id=existing.venue_order_id,
                        idempotency_key=idem.value,
                    )
                elif s == CommandState.FILLED:
                    collision_result = OrderResult(
                        trade_id=intent.trade_id,
                        status="pending",
                        reason="idempotency_collision: prior attempt filled",
                        submitted_price=limit_price,
                        shares=shares,
                        order_role="exit",
                        intent_id=intent.intent_id,
                        external_order_id=existing.venue_order_id,
                        idempotency_key=idem.value,
                    )
                elif s in (CommandState.REJECTED, CommandState.CANCELLED, CommandState.EXPIRED):
                    collision_result = OrderResult(
                        trade_id=intent.trade_id,
                        status="rejected",
                        reason=f"idempotency_collision: prior attempt {s.value}",
                        submitted_price=limit_price,
                        shares=shares,
                        order_role="exit",
                        intent_id=intent.intent_id,
                        external_order_id=existing.venue_order_id,
                        idempotency_key=idem.value,
                    )
                elif s in (CommandState.SUBMITTING, CommandState.UNKNOWN):
                    collision_result = OrderResult(
                        trade_id=intent.trade_id,
                        status="rejected",
                        reason="idempotency_collision: prior attempt in flight; recovery will resolve",
                        submitted_price=limit_price,
                        shares=shares,
                        order_role="exit",
                        intent_id=intent.intent_id,
                        idempotency_key=idem.value,
                    )
                else:
                    # REVIEW_REQUIRED, INTENT_CREATED, or any future state
                    collision_result = OrderResult(
                        trade_id=intent.trade_id,
                        status="rejected",
                        reason=f"idempotency_collision: prior attempt {s.value}",
                        submitted_price=limit_price,
                        shares=shares,
                        order_role="exit",
                        intent_id=intent.intent_id,
                        idempotency_key=idem.value,
                    )
            else:
                # Defensive fallback: row not found despite collision
                collision_result = OrderResult(
                    trade_id=intent.trade_id,
                    status="rejected",
                    reason=f"idempotency_collision: {exc}",
                    submitted_price=limit_price,
                    shares=shares,
                    order_role="exit",
                    intent_id=intent.intent_id,
                    idempotency_key=intent.idempotency_key,
                )
            return collision_result

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
            result = client.place_limit_order(
                token_id=intent.token_id,
                price=limit_price,
                size=shares,
                side="SELL",
            )
        except Exception as exc:
            # SUBMIT_UNKNOWN: the SDK raised — we don't know if the order reached
            # the venue. Row remains in SUBMITTING; recovery loop will resolve.
            ack_time = datetime.now(timezone.utc).isoformat()
            try:
                append_event(
                    conn,
                    command_id=command_id,
                    event_type="SUBMIT_UNKNOWN",
                    occurred_at=ack_time,
                    payload={"exception_type": type(exc).__name__, "exception_message": str(exc)},
                )
                if _own_conn:
                    conn.commit()
            except Exception as inner:
                logger.error(
                    "execute_exit_order: SUBMIT_UNKNOWN append_event failed after SDK exception "
                    "(command_id=%s trade_id=%s): inner=%s original=%s",
                    command_id, intent.trade_id, inner, exc,
                )
            logger.error("Live exit order SDK exception: %s", exc)
            return OrderResult(
                trade_id=intent.trade_id,
                status="rejected",
                reason=f"submit_unknown: {exc}",
                submitted_price=limit_price,
                shares=shares,
                order_role="exit",
                intent_id=intent.intent_id,
                idempotency_key=intent.idempotency_key,
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
                    event_type="SUBMIT_REJECTED",
                    occurred_at=ack_time,
                    payload={"reason": "clob_returned_none"},
                )
                if _own_conn:
                    conn.commit()
            except Exception as inner:
                logger.error(
                    "execute_exit_order: SUBMIT_REJECTED append_event failed (command_id=%s): %s",
                    command_id, inner,
                )
            return OrderResult(
                trade_id=intent.trade_id,
                status="rejected",
                reason="clob_returned_none",
                submitted_price=limit_price,
                shares=shares,
                order_role="exit",
                intent_id=intent.intent_id,
                idempotency_key=intent.idempotency_key,
            )
    
        order_id = (
            result.get("orderID")
            or result.get("orderId")
            or result.get("id")
        )
        if not order_id:
            try:
                append_event(
                    conn,
                    command_id=command_id,
                    event_type="SUBMIT_REJECTED",
                    occurred_at=ack_time,
                    payload={"reason": "clob_returned_none"},
                )
                if _own_conn:
                    conn.commit()
            except Exception as inner:
                logger.error(
                    "execute_exit_order: SUBMIT_REJECTED (missing_order_id) append_event failed "
                    "(command_id=%s): %s",
                    command_id, inner,
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
            )
    
        # SUBMIT_ACKED — order placed successfully
        try:
            append_event(
                conn,
                command_id=command_id,
                event_type="SUBMIT_ACKED",
                occurred_at=ack_time,
                payload={"venue_order_id": order_id},
            )
            if _own_conn:
                conn.commit()
        except Exception as inner:
            logger.error(
                "execute_exit_order: SUBMIT_ACKED append_event failed (command_id=%s order_id=%s): %s",
                command_id, order_id, inner,
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
            venue_status=str(result.get("status") or "placed"),
            idempotency_key=idem.value,
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


def _live_order(
    trade_id: str,
    intent: ExecutionIntent,
    shares: float,
    conn: Optional[sqlite3.Connection] = None,
    decision_id: str = "",
) -> OrderResult:
    """Live mode: place order via Polymarket CLOB API.

    Phase order (INV-30):
      1. ExecutionPrice validation (synchronous; no I/O)
      2. build: VenueCommand + IdempotencyKey (pure; no I/O)
      3. persist: insert_command (INTENT_CREATED) + append_event (SUBMIT_REQUESTED)
      4. V2 preflight (if fails, append SUBMIT_REJECTED; return rejected)
      5. submit: client.place_limit_order (SDK call)
      6. ack: append_event SUBMIT_ACKED / SUBMIT_REJECTED / SUBMIT_UNKNOWN
    """
    from src.data.polymarket_client import PolymarketClient, V2PreflightError
    from src.execution.command_bus import IdempotencyKey, IntentKind
    from src.state.venue_command_repo import insert_command, append_event

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

    # -----------------------------------------------------------------------
    # Phase 2: build — pure, no I/O (INV-30)
    # Derive a synthetic decision_id when caller hasn't supplied a real one.
    # P1.S5 will wire real decision_id from upstream.
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
    # -----------------------------------------------------------------------
    # Post-critic CRITICAL/HIGH: fallback uses get_trade_connection_with_world()
    # because that's where init_schema runs; get_connection() targets zeus.db.
    # Wrapped in try/finally so the fallback connection is always closed.
    _own_conn = conn is None
    if _own_conn:
        conn = get_trade_connection_with_world()
    if not decision_id:
        logger.warning(
            "EXECUTOR: synthetic decision_id %s — retry-idempotency NOT guaranteed; "
            "pass decision_id explicitly (P1.S5 will wire upstream)",
            effective_decision_id,
        )
    try:  # outer: ensures conn is closed when _own_conn (HIGH fix)
        try:
            insert_command(
                conn,
                command_id=command_id,
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
            )
            append_event(
                conn,
                command_id=command_id,
                event_type="SUBMIT_REQUESTED",
                occurred_at=now_str,
            )
            if _own_conn:
                conn.commit()
        except sqlite3.IntegrityError as exc:
            # Idempotency key collision: existing command is canonical record.
            logger.warning(
                "_live_order: idempotency key collision for trade_id=%s idem=%s: %s",
                trade_id, idem.value, exc,
            )
            from src.state.venue_command_repo import find_command_by_idempotency_key
            from src.execution.command_bus import VenueCommand, CommandState
            existing_row = find_command_by_idempotency_key(conn, idem.value)
            if existing_row is not None:
                existing = VenueCommand.from_row(existing_row)
                s = existing.state
                if s in (CommandState.ACKED, CommandState.PARTIAL):
                    collision_result = OrderResult(
                        trade_id=trade_id,
                        status="pending",
                        reason="idempotency_collision: prior attempt acked",
                        submitted_price=intent.limit_price,
                        shares=shares,
                        order_role="entry",
                        external_order_id=existing.venue_order_id,
                        idempotency_key=idem.value,
                    )
                elif s == CommandState.FILLED:
                    collision_result = OrderResult(
                        trade_id=trade_id,
                        status="pending",
                        reason="idempotency_collision: prior attempt filled",
                        submitted_price=intent.limit_price,
                        shares=shares,
                        order_role="entry",
                        external_order_id=existing.venue_order_id,
                        idempotency_key=idem.value,
                    )
                elif s in (CommandState.REJECTED, CommandState.CANCELLED, CommandState.EXPIRED):
                    collision_result = OrderResult(
                        trade_id=trade_id,
                        status="rejected",
                        reason=f"idempotency_collision: prior attempt {s.value}",
                        submitted_price=intent.limit_price,
                        shares=shares,
                        order_role="entry",
                        external_order_id=existing.venue_order_id,
                        idempotency_key=idem.value,
                    )
                elif s in (CommandState.SUBMITTING, CommandState.UNKNOWN):
                    collision_result = OrderResult(
                        trade_id=trade_id,
                        status="rejected",
                        reason="idempotency_collision: prior attempt in flight; recovery will resolve",
                        submitted_price=intent.limit_price,
                        shares=shares,
                        order_role="entry",
                        idempotency_key=idem.value,
                    )
                else:
                    # REVIEW_REQUIRED, INTENT_CREATED, or any future state
                    collision_result = OrderResult(
                        trade_id=trade_id,
                        status="rejected",
                        reason=f"idempotency_collision: prior attempt {s.value}",
                        submitted_price=intent.limit_price,
                        shares=shares,
                        order_role="entry",
                        idempotency_key=idem.value,
                    )
            else:
                # Defensive fallback: row not found despite collision
                collision_result = OrderResult(
                    trade_id=trade_id,
                    status="rejected",
                    reason=f"idempotency_collision: {exc}",
                    submitted_price=intent.limit_price,
                    shares=shares,
                    order_role="entry",
                )
            return collision_result

        # -----------------------------------------------------------------------
        # Phase 4: V2 endpoint-identity preflight (INV-25 / K5)
        # Client is instantiated here so both preflight and place_limit_order
        # share the same instance. If preflight fails, append SUBMIT_REJECTED
        # (the row is already SUBMITTING and must reach a terminal state).
        # -----------------------------------------------------------------------
        client = PolymarketClient()
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

        logger.info(
            "LIVE ORDER: %s token=%s...%s @ %.3f limit, %.2f shares, timeout=%ds",
            intent.direction.value,
            intent.token_id[:8], intent.token_id[-4:],
            intent.limit_price, shares, timeout,
        )

        # -----------------------------------------------------------------------
        # Phase 5: submit — SDK call (INV-30: row already SUBMITTING)
        # -----------------------------------------------------------------------
        try:
            result = client.place_limit_order(
                token_id=intent.token_id,
                price=intent.limit_price,
                size=shares,
                side="BUY",  # Always BUY
            )
        except Exception as exc:
            # SUBMIT_UNKNOWN: SDK raised; we don't know if the order reached venue.
            unk_time = datetime.now(timezone.utc).isoformat()
            try:
                append_event(
                    conn,
                    command_id=command_id,
                    event_type="SUBMIT_UNKNOWN",
                    occurred_at=unk_time,
                    payload={"exception_type": type(exc).__name__, "exception_message": str(exc)},
                )
                if _own_conn:
                    conn.commit()
            except Exception as inner:
                logger.error(
                    "_live_order: SUBMIT_UNKNOWN append_event failed after SDK exception "
                    "(command_id=%s trade_id=%s): inner=%s original=%s",
                    command_id, trade_id, inner, exc,
                )
            logger.error("Live order SDK exception: %s", exc)
            return OrderResult(
                trade_id=trade_id,
                status="rejected",
                reason=f"submit_unknown: {exc}",
                submitted_price=intent.limit_price,
                shares=shares,
                order_role="entry",
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
                    event_type="SUBMIT_REJECTED",
                    occurred_at=ack_time,
                    payload={"reason": "clob_returned_none"},
                )
                if _own_conn:
                    conn.commit()
            except Exception as inner:
                logger.error(
                    "_live_order: SUBMIT_REJECTED append_event failed (command_id=%s): %s",
                    command_id, inner,
                )
            return OrderResult(
                trade_id=trade_id, status="rejected",
                reason="clob_returned_none",
            )

        order_id = (
            result.get("orderID")
            or result.get("orderId")
            or result.get("id")
            or None
        )
        # SUBMIT_ACKED
        try:
            append_event(
                conn,
                command_id=command_id,
                event_type="SUBMIT_ACKED",
                occurred_at=ack_time,
                payload={"venue_order_id": order_id, "venue_status": str(result.get("status") or "")},
            )
            if _own_conn:
                conn.commit()
        except Exception as inner:
            logger.error(
                "_live_order: SUBMIT_ACKED append_event failed (command_id=%s order_id=%s): %s",
                command_id, order_id, inner,
            )

        result_obj = OrderResult(
            trade_id=trade_id,
            status="pending",
            reason=f"Order posted, timeout={timeout}s",
            order_id=order_id,
            timeout_seconds=timeout,
            submitted_price=intent.limit_price,
            shares=shares,
            order_role="entry",
            external_order_id=order_id,
            venue_status=str(result.get("status") or "placed"),
            idempotency_key=idem.value,
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
