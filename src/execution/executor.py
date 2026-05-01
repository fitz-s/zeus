"""Order executor: limit-order-only execution engine. Spec §6.4.

Live mode only: places limit orders via Polymarket CLOB API.

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
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
from typing import Mapping, Optional

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


def _assert_collateral_allows_buy(intent: ExecutionIntent, *, spend_micro: int | None = None) -> dict:
    """Fail before command persistence or SDK contact when pUSD is insufficient."""
    from src.state.collateral_ledger import assert_buy_preflight

    assert_buy_preflight(intent, spend_micro=spend_micro)
    return _capability_component("collateral_ledger", collateral="pUSD", spend_micro=spend_micro or 0)


def _assert_collateral_allows_sell(token_id: str, shares: float) -> dict:
    """Fail before command persistence or SDK contact when CTF inventory is insufficient."""
    from src.state.collateral_ledger import assert_sell_preflight

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


def _entry_decision_source_component(intent: ExecutionIntent) -> dict:
    context = getattr(intent, "decision_source_context", None)
    if context is None:
        return _capability_component(
            "decision_source_integrity",
            allowed=False,
            reason="missing_decision_source_context",
        )
    errors = context.integrity_errors()
    details = context.capability_details()
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

    This deliberately uses only the already-captured ExecutableMarketSnapshotV2
    plus the command's intended order shape.  It does not resolve keychain
    credentials or instantiate the SDK client, preserving INV-30's
    persist-before-submit ordering.  If the snapshot is missing or the token is
    not in that snapshot, return None and let insert_command's executable
    snapshot gate raise the more precise fail-closed error.
    """

    from src.contracts.venue_submission_envelope import VenueSubmissionEnvelope
    from src.contracts.executable_market_snapshot_v2 import canonicalize_fee_details
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
    envelope = VenueSubmissionEnvelope(
        sdk_package="py-clob-client-v2",
        sdk_version="pre-submit",
        host=os.environ.get("POLYMARKET_CLOB_V2_HOST", DEFAULT_V2_HOST),
        chain_id=int(os.environ.get("POLYMARKET_CHAIN_ID", "137")),
        funder_address=(
            os.environ.get("POLYMARKET_FUNDER_ADDRESS")
            or os.environ.get("POLYMARKET_PROXY_ADDRESS")
            or "UNRESOLVED_PRE_SUBMIT_FUNDER"
        ),
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


@dataclass(frozen=True)
class ExitOrderIntent:
    """Executor-level contract for live sell/exit order placement."""

    trade_id: str
    token_id: str
    shares: float
    current_price: float
    best_bid: Optional[float] = None
    market_id: str = ""
    condition_id: str = ""
    question_id: str = ""
    yes_token_id: str = ""
    no_token_id: str = ""
    intent_id: Optional[str] = None
    idempotency_key: Optional[str] = None
    executable_snapshot_id: str = ""
    executable_snapshot_min_tick_size: Decimal | str | None = None
    executable_snapshot_min_order_size: Decimal | str | None = None
    executable_snapshot_neg_risk: bool | None = None


def _orderresult_from_existing(
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


def execute_intent(
    intent: ExecutionIntent,
    edge_vwmp: float,  # Phase 2: remove this parameter (dead after _paper_fill deletion)
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

    from src.execution.command_bus import IntentKind
    _assert_cutover_allows_submit(IntentKind.ENTRY)

    return _live_order(
        trade_id, intent, shares, conn=conn, decision_id=decision_id
    )


def _coerce_entry_decision_source_context(
    context: DecisionSourceContext | Mapping[str, object] | None,
) -> DecisionSourceContext | None:
    if isinstance(context, DecisionSourceContext):
        return context
    if isinstance(context, Mapping):
        return DecisionSourceContext.from_forecast_context(context)
    return None


def _buy_entry_shares_from_final_intent(intent: FinalExecutionIntent) -> Decimal:
    if intent.size_kind == "shares":
        raw_shares = Decimal(intent.size_value)
    elif intent.size_kind == "notional_usd":
        raw_shares = Decimal(intent.size_value) / Decimal(intent.final_limit_price)
    else:
        raise ValueError(f"unsupported final intent size_kind {intent.size_kind!r}")
    if raw_shares <= Decimal("0"):
        raise ValueError("final intent shares must be positive")
    share_tick = Decimal("0.01")
    return (
        (raw_shares / share_tick).to_integral_value(rounding=ROUND_CEILING)
        * share_tick
    )


def _legacy_entry_intent_from_final_intent(
    intent: FinalExecutionIntent,
    *,
    market_id: str,
    mode: str,
    decision_source_context: DecisionSourceContext,
    event_id: str = "",
    resolution_window: str = "",
    correlation_key: str = "",
    decision_edge: float = 0.0,
) -> tuple[ExecutionIntent, Decimal]:
    direction = Direction.YES if intent.direction == "buy_yes" else Direction.NO
    shares = _buy_entry_shares_from_final_intent(intent)
    target_size_usd = shares * Decimal(intent.final_limit_price)
    return (
        ExecutionIntent(
            direction=direction,
            target_size_usd=float(target_size_usd),
            limit_price=float(intent.final_limit_price),
            toxicity_budget=0.05,
            max_slippage=SlippageBps(
                value_bps=float(intent.max_slippage_bps),
                direction="adverse",
            ),
            is_sandbox=False,
            market_id=market_id,
            token_id=intent.selected_token_id,
            timeout_seconds=MODE_TIMEOUTS[mode],
            decision_edge=decision_edge,
            executable_snapshot_id=intent.snapshot_id,
            executable_snapshot_min_tick_size=intent.tick_size,
            executable_snapshot_min_order_size=intent.min_order_size,
            executable_snapshot_neg_risk=intent.neg_risk,
            event_id=event_id or market_id,
            resolution_window=resolution_window or "default",
            correlation_key=correlation_key or event_id or market_id,
            decision_source_context=decision_source_context,
            order_type=intent.order_type,
            post_only=intent.post_only,
        ),
        shares,
    )


def execute_final_intent(
    intent: FinalExecutionIntent,
    *,
    market_id: str,
    mode: str,
    decision_source_context: DecisionSourceContext | Mapping[str, object] | None,
    conn: Optional[sqlite3.Connection] = None,
    decision_id: str = "",
    trade_id: str = "",
    event_id: str = "",
    resolution_window: str = "",
    correlation_key: str = "",
    decision_edge: float = 0.0,
) -> "OrderResult":
    """Submit an immutable corrected entry intent through the live command path."""

    effective_trade_id = trade_id or str(uuid.uuid4())[:12]
    if not isinstance(intent, FinalExecutionIntent):
        raise TypeError(
            "execute_final_intent requires FinalExecutionIntent; "
            f"got {type(intent).__name__}"
        )
    try:
        intent.assert_submit_ready()
        intent.assert_no_recompute_inputs()
    except (TypeError, ValueError) as exc:
        return OrderResult(
            trade_id=effective_trade_id,
            status="rejected",
            reason=f"final_intent_invalid:{exc}",
            submitted_price=getattr(intent, "final_limit_price", None),
            order_role="entry",
        )
    if intent.direction not in {"buy_yes", "buy_no"}:
        return OrderResult(
            trade_id=effective_trade_id,
            status="rejected",
            reason=f"unsupported_final_entry_direction:{intent.direction}",
            submitted_price=float(intent.final_limit_price),
            order_role="entry",
        )
    if intent.post_only:
        return OrderResult(
            trade_id=effective_trade_id,
            status="rejected",
            reason="post_only_entry_submit_not_supported",
            submitted_price=float(intent.final_limit_price),
            order_role="entry",
        )
    normalized_market_id = str(market_id or "").strip()
    if not normalized_market_id:
        return OrderResult(
            trade_id=effective_trade_id,
            status="rejected",
            reason="missing_market_id",
            submitted_price=float(intent.final_limit_price),
            order_role="entry",
        )
    if mode not in MODE_TIMEOUTS:
        return OrderResult(
            trade_id=effective_trade_id,
            status="rejected",
            reason=f"unknown_execution_mode:{mode}",
            submitted_price=float(intent.final_limit_price),
            order_role="entry",
        )
    source_context = _coerce_entry_decision_source_context(decision_source_context)
    if source_context is None:
        return OrderResult(
            trade_id=effective_trade_id,
            status="rejected",
            reason="decision_source_integrity:missing_decision_source_context",
            submitted_price=float(intent.final_limit_price),
            order_role="entry",
        )
    source_errors = source_context.integrity_errors()
    if source_errors:
        return OrderResult(
            trade_id=effective_trade_id,
            status="rejected",
            reason=(
                "decision_source_integrity:invalid_decision_source_context:"
                + ",".join(source_errors)
            ),
            submitted_price=float(intent.final_limit_price),
            order_role="entry",
        )
    legacy_intent, shares = _legacy_entry_intent_from_final_intent(
        intent,
        market_id=normalized_market_id,
        mode=mode,
        decision_source_context=source_context,
        event_id=event_id,
        resolution_window=resolution_window,
        correlation_key=correlation_key,
        decision_edge=decision_edge,
    )
    return _live_order(
        effective_trade_id,
        legacy_intent,
        float(shares),
        conn=conn,
        decision_id=decision_id,
    )


def create_exit_order_intent(
    *,
    trade_id: str,
    token_id: str,
    shares: float,
    current_price: float,
    best_bid: Optional[float] = None,
    market_id: str = "",
    condition_id: str = "",
    question_id: str = "",
    yes_token_id: str = "",
    no_token_id: str = "",
    executable_snapshot_id: str = "",
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
        market_id=market_id,
        condition_id=condition_id,
        question_id=question_id,
        yes_token_id=yes_token_id,
        no_token_id=no_token_id,
        intent_id=f"{trade_id}:exit",
        idempotency_key=f"{trade_id}:exit:{token_id}",
        executable_snapshot_id=executable_snapshot_id,
        executable_snapshot_min_tick_size=executable_snapshot_min_tick_size,
        executable_snapshot_min_order_size=executable_snapshot_min_order_size,
        executable_snapshot_neg_risk=executable_snapshot_neg_risk,
    )


def place_sell_order(
    token_id: str,
    shares: float,
    current_price: float,
    best_bid: Optional[float] = None,
    market_id: str = "",
    condition_id: str = "",
    question_id: str = "",
    yes_token_id: str = "",
    no_token_id: str = "",
) -> dict:
    """Legacy compatibility wrapper for the executor-level exit-order path."""

    result = execute_exit_order(
        create_exit_order_intent(
            trade_id=f"exit-{token_id[:8]}",
            token_id=token_id,
            shares=shares,
            current_price=current_price,
            best_bid=best_bid,
            market_id=market_id,
            condition_id=condition_id,
            question_id=question_id,
            yes_token_id=yes_token_id,
            no_token_id=no_token_id,
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


def _exit_market_id_for_command(conn: sqlite3.Connection, intent: ExitOrderIntent) -> str:
    """Return condition/gamma market identity for an exit command row."""

    explicit_market_id = str(getattr(intent, "market_id", "") or "").strip()
    if explicit_market_id and explicit_market_id != intent.token_id:
        return explicit_market_id

    if intent.executable_snapshot_id:
        from src.state.snapshot_repo import get_snapshot

        snapshot = get_snapshot(conn, intent.executable_snapshot_id)
        if snapshot is not None:
            for value in (
                getattr(snapshot, "gamma_market_id", ""),
                getattr(snapshot, "condition_id", ""),
                getattr(snapshot, "question_id", ""),
            ):
                normalized = str(value or "").strip()
                if normalized and normalized != intent.token_id:
                    return normalized

    for value in (
        getattr(intent, "condition_id", ""),
        getattr(intent, "question_id", ""),
    ):
        normalized = str(value or "").strip()
        if normalized and normalized != intent.token_id:
            return normalized

    return explicit_market_id or intent.token_id


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
    from src.data.polymarket_client import PolymarketClient
    from src.execution.command_bus import IdempotencyKey, IntentKind, VenueCommand, CommandState
    from src.state.venue_command_repo import insert_command, append_event, get_command
    from src.contracts.executable_market_snapshot_v2 import MarketSnapshotError

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

    cutover_component = _assert_cutover_allows_submit(IntentKind.EXIT)
    risk_allocator_decision = _assert_risk_allocator_allows_exit_submit()

    # -----------------------------------------------------------------------
    # build phase — pure, no I/O (INV-30)
    # -----------------------------------------------------------------------
    # Derive a synthetic decision_id from trade_id when the caller has not
    # supplied a real one. P1.S5 wires real decision_id from upstream;
    # exit path still uses synthetic when called without decision_id.
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
        conn = get_trade_connection_with_world()
    market_id_for_cmd = _exit_market_id_for_command(conn, intent)
    if not decision_id:
        logger.warning(
            "EXECUTOR: synthetic decision_id %s — retry-idempotency NOT guaranteed; "
            "pass decision_id explicitly",
            effective_decision_id,
        )
    try:
        order_type = _select_risk_allocator_order_type(conn, intent.executable_snapshot_id)
        heartbeat_component = _assert_heartbeat_allows_submit(order_type)
        ws_gap_component = _assert_ws_gap_allows_submit(market_id_for_cmd)
        collateral_component = _assert_collateral_allows_sell(intent.token_id, shares)

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
            logger.info(
                "execute_exit_order: pre-submit lookup found existing command for "
                "idem=%s trade_id=%s — skipping submit",
                idem.value, intent.trade_id,
            )
            return _orderresult_from_existing(
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
                if _own_conn:
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
                            _capability_component("order_type_selection", order_type=order_type),
                            heartbeat_component,
                            ws_gap_component,
                            collateral_component,
                            _capability_component("replacement_sell_guard"),
                            _exit_decision_source_component(),
                            _capability_component("executable_snapshot_gate"),
                        ],
                    ),
                },
            )
            _reserve_collateral_for_sell(command_id, intent.token_id, shares, conn)
            if not _own_conn:
                pass  # caller manages commit
            else:
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
        except sqlite3.IntegrityError as exc:
            # Race-condition safety belt: another process inserted between our
            # lookup and our INSERT. Existing command is the canonical record.
            logger.warning(
                "execute_exit_order: idempotency key collision (race) for trade_id=%s idem=%s: %s",
                intent.trade_id, idem.value, exc,
            )
            existing_row = find_command_by_idempotency_key(conn, idem.value)
            if existing_row is not None:
                return _orderresult_from_existing(
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
            # Treat SDK/network exceptions as unknown side effects, never as
            # semantic rejection; recovery proves ACK/FILL or safe replay.
            ack_time = datetime.now(timezone.utc).isoformat()
            try:
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
                if _own_conn:
                    conn.commit()
            except Exception as inner:
                logger.error(
                    "execute_exit_order: SUBMIT_TIMEOUT_UNKNOWN append_event failed after SDK exception "
                    "(command_id=%s trade_id=%s): inner=%s original=%s",
                    command_id, intent.trade_id, inner, exc,
                )
            logger.error("Live exit order SDK exception: %s", exc)
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
                if _own_conn:
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
                if _own_conn:
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
                if _own_conn:
                    conn.commit()
            except Exception as inner:
                logger.error(
                    "execute_exit_order: SUBMIT_REJECTED (success_false) append_event failed "
                    "(command_id=%s): %s",
                    command_id, inner,
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
                payload={
                    "venue_order_id": order_id,
                    "order_type": order_type,
                    **final_envelope_payload,
                },
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
    from src.data.polymarket_client import PolymarketClient, V2PreflightError
    from src.execution.command_bus import IdempotencyKey, IntentKind
    from src.state.venue_command_repo import insert_command, append_event
    from src.contracts.executable_market_snapshot_v2 import MarketSnapshotError

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

    risk_allocator_decision = _assert_risk_allocator_allows_submit(intent)
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
        conn = get_trade_connection_with_world()
    if not decision_id:
        logger.warning(
            "EXECUTOR: synthetic decision_id %s — retry-idempotency NOT guaranteed; "
            "pass decision_id explicitly",
            effective_decision_id,
        )
    try:  # outer: ensures conn is closed when _own_conn (HIGH fix)
        allocator_order_type = _select_risk_allocator_order_type(
            conn,
            intent.executable_snapshot_id,
        )
        intent_order_type = str(getattr(intent, "order_type", "") or "").strip()
        if intent_order_type and intent_order_type != allocator_order_type:
            return OrderResult(
                trade_id=trade_id,
                status="rejected",
                reason=(
                    "order_type_authority_mismatch:"
                    f"intent={intent_order_type}:risk_allocator={allocator_order_type}"
                ),
                submitted_price=intent.limit_price,
                shares=shares,
                order_role="entry",
            )
        if getattr(intent, "post_only", False):
            return OrderResult(
                trade_id=trade_id,
                status="rejected",
                reason="post_only_entry_submit_not_supported",
                submitted_price=intent.limit_price,
                shares=shares,
                order_role="entry",
            )
        order_type = intent_order_type or allocator_order_type
        heartbeat_component = _assert_heartbeat_allows_submit(order_type)
        ws_gap_component = _assert_ws_gap_allows_submit(getattr(intent, "market_id", None) or getattr(intent, "token_id", None))
        collateral_component = _assert_collateral_allows_buy(intent, spend_micro=required_pusd_micro)

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
            logger.info(
                "_live_order: pre-submit lookup found existing command for "
                "idem=%s trade_id=%s — skipping submit",
                idem.value, trade_id,
            )
            return _orderresult_from_existing(
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
            pre_submit_envelope = _build_pre_submit_envelope(
                conn,
                command_id=command_id,
                snapshot_id=intent.executable_snapshot_id,
                token_id=intent.token_id,
                side="BUY",
                price=intent.limit_price,
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
                    "order_type": order_type,
                    "execution_capability": _build_execution_capability(
                        action="ENTRY",
                        command_id=command_id,
                        intent_kind=IntentKind.ENTRY.value,
                        order_type=order_type,
                        token_id=intent.token_id,
                        snapshot_id=intent.executable_snapshot_id,
                        freshness_time=now_str,
                        components=[
                            cutover_component,
                            _component_from_result(
                                "risk_allocator",
                                risk_allocator_decision,
                            ),
                            _capability_component("order_type_selection", order_type=order_type),
                            heartbeat_component,
                            ws_gap_component,
                            collateral_component,
                            decision_source_component,
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
            if _own_conn:
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
        except sqlite3.IntegrityError as exc:
            # Race-condition safety belt: another process inserted between our
            # lookup and our INSERT. Existing command is the canonical record.
            logger.warning(
                "_live_order: idempotency key collision (race) for trade_id=%s idem=%s: %s",
                trade_id, idem.value, exc,
            )
            existing_row = find_command_by_idempotency_key(conn, idem.value)
            if existing_row is not None:
                return _orderresult_from_existing(
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
        try:
            result = client.place_limit_order(
                token_id=intent.token_id,
                price=intent.limit_price,
                size=shares,
                side="BUY",  # Always BUY
                order_type=order_type,
            )
        except Exception as exc:
            # M2: place_limit_order has crossed the submit side-effect boundary.
            # Treat SDK/network exceptions as unknown side effects, never as
            # semantic rejection; recovery proves ACK/FILL or safe replay.
            unk_time = datetime.now(timezone.utc).isoformat()
            try:
                append_event(
                    conn,
                    command_id=command_id,
                    event_type="SUBMIT_TIMEOUT_UNKNOWN",
                    occurred_at=unk_time,
                    payload={
                        "reason": "post_submit_exception_possible_side_effect",
                        "exception_type": type(exc).__name__,
                        "exception_message": str(exc),
                        "idempotency_key": idem.value,
                    },
                )
                if _own_conn:
                    conn.commit()
            except Exception as inner:
                logger.error(
                    "_live_order: SUBMIT_TIMEOUT_UNKNOWN append_event failed after SDK exception "
                    "(command_id=%s trade_id=%s): inner=%s original=%s",
                    command_id, trade_id, inner, exc,
                )
            logger.error("Live order SDK exception: %s", exc)
            return OrderResult(
                trade_id=trade_id,
                status="unknown_side_effect",
                reason=f"submit_unknown_side_effect: {exc}",
                submitted_price=intent.limit_price,
                shares=shares,
                order_role="entry",
                idempotency_key=idem.value,
                command_state="SUBMIT_UNKNOWN_SIDE_EFFECT",
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
            return OrderResult(
                trade_id=trade_id,
                status="rejected",
                reason=str(rejection_reason),
                submitted_price=intent.limit_price,
                shares=shares,
                order_role="entry",
                venue_status=str(result.get("status") or ""),
                idempotency_key=idem.value,
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
            return OrderResult(
                trade_id=trade_id,
                status="rejected",
                reason="missing_order_id",
                submitted_price=intent.limit_price,
                shares=shares,
                order_role="entry",
                venue_status=str(result.get("status") or ""),
                idempotency_key=idem.value,
            )
        # SUBMIT_ACKED
        try:
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
            command_state="ACKED",  # P1.S5 INV-32: materialize_position gates on this
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
