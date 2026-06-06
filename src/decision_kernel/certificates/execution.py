# Created: 2026-05-01
# Last reused or audited: 2026-06-01
# Authority basis: GOAL#36 pre-arm parity — Bug A/B fix: tick_size from DB snap (str),
#   sweep_expected_fill_price as Decimal string (not float).
"""Execution certificate builders and verifier entrypoints."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from collections.abc import Mapping
from typing import Iterable

from src.decision_kernel import claims
from src.decision_kernel.canonicalization import stable_hash
from src.decision_kernel.certificate import DecisionCertificate, ParentEdge, build_certificate
from src.decision_kernel.verifier import (
    verify_execution_command,
    verify_execution_receipt,
    verify_executor_expressibility,
    verify_final_intent,
    verify_live_cap_transition,
)
from src.events.live_order_aggregate import LiveOrderAggregateEvent


def build_final_intent_certificate_from_actionable(
    *,
    actionable_cert: DecisionCertificate,
    executable_snapshot_cert: DecisionCertificate,
    quote_feasibility_cert: DecisionCertificate,
    cost_model_cert: DecisionCertificate,
    forecast_authority_cert: DecisionCertificate,
    decision_source_context,
    passive_maker_context,
    decision_time: datetime,
    order_mode: str = "MAKER",
    order_type: str | None = None,
    time_in_force: str | None = None,
    tick_size: float | str = 0.01,
    min_order_size: float = 1.0,
    fee_rate: float = 0.0,
    best_bid: float | None = None,
    best_ask: float | None = None,
    taker_fok_fak_live_enabled: bool = False,
    available_crossable_shares: float | None = None,
    sweep_expected_fill_price: str | None = None,
) -> DecisionCertificate:
    action = actionable_cert.payload
    # The governor-decided ``order_mode`` is the SOLE authority for the order-type
    # tuple (Fitz #4 provenance): this builder is the only authorized emitter of a
    # taker tuple, and it only emits one when order_mode == "TAKER". A partial
    # change across the three layers ships a broken order type, so the mode drives
    # order_type / time_in_force / post_only / maker_intent together here.
    order_spec = _order_spec_for_mode(
        order_mode=order_mode,
        order_type=order_type,
        time_in_force=time_in_force,
    )
    reservation = float(action["c_fee_adjusted"])
    limit_price = _branch_limit_price(
        side=_side_for_direction(str(action["direction"])),
        order_mode=order_spec.mode,
        reservation=reservation,
        best_bid=best_bid,
        best_ask=best_ask,
        tick_size=float(tick_size),
        passive_maker_context=passive_maker_context,
    )
    reserved_notional = float(action.get("live_cap_reserved_notional_usd") or action.get("kelly_size_usd") or 0.0)
    if limit_price <= 0.0:
        raise ValueError(
            f"CERT_BUILD_ZERO_LIMIT_PRICE: limit_price={limit_price!r} after tick-floor "
            f"(c_fee_adjusted={reservation!r}, tick_size={float(tick_size)!r}); "
            "candidate reservation below minimum tradeable price — skip"
        )
    size = max(float(min_order_size), reserved_notional / limit_price)
    # SIZE-TO-AVAILABLE-DEPTH (Wall B / 2026-06-01): for TAKER FOK orders cap the
    # requested size to the crossable book depth so the FOK can fully fill on a thin
    # book.  available_crossable_shares is computed by the caller (ERA) via
    # simulate_clob_sweep on the elected snapshot before cert build.  If the capped
    # size falls below min_order_size the book is too thin → raise so the candidate
    # correctly skips (fail-closed, no -EV order).
    if available_crossable_shares is not None and order_spec.mode == "TAKER":
        size = min(size, float(available_crossable_shares))
        if size < float(min_order_size):
            raise ValueError(
                f"DEPTH_BELOW_MIN_ORDER_SIZE: available_crossable_shares="
                f"{available_crossable_shares:.4f} < min_order_size={min_order_size:.4f}"
            )
    notional = size * limit_price
    expected_fill_price = float(
        sweep_expected_fill_price
        if sweep_expected_fill_price is not None
        else limit_price
    )
    max_slippage_bps = _declared_max_slippage_bps(
        direction=str(action["direction"]),
        order_mode=order_spec.mode,
        limit_price=limit_price,
        expected_fill_price=expected_fill_price,
    )
    executable_snapshot_hash = _required_text(executable_snapshot_cert.payload, "executable_snapshot_hash")
    cost_basis_hash = _required_text(cost_model_cert.payload, "cost_basis_hash")
    decision_source_context_payload = _context_payload(decision_source_context, "decision_source_context")
    # WALL #1 (2026-06-01): passive_maker_context is MAKER-ONLY. A taker FOK/FAK
    # crosses the JIT book at submit and never rests, so it carries no maker context.
    # Requiring it for taker was the design coupling that produced the dominant live
    # wall (QUOTE_FEASIBILITY_BID_ASK_REQUIRED). For a taker tuple the context is
    # absent (None) and the FINAL_INTENT payload records it as None; the downstream
    # executor-expressibility translator and verifier accept None iff the tuple is
    # taker. For maker the context remains REQUIRED (fail-closed — a resting maker
    # order genuinely needs the book).
    if order_spec.mode == "TAKER":
        passive_maker_context_payload = None
    else:
        passive_maker_context_payload = _context_payload(passive_maker_context, "passive_maker_context")
    payload = {
        "event_id": action["event_id"],
        "actionable_certificate_hash": actionable_cert.certificate_hash,
        "final_intent_id": action["final_intent_id"],
        "family_id": action["family_id"],
        "candidate_id": action["candidate_id"],
        "condition_id": action["condition_id"],
        "token_id": action["token_id"],
        "direction": action["direction"],
        "side": _side_for_direction(str(action["direction"])),
        "order_type": order_spec.order_type,
        "executor_order_type": order_spec.time_in_force,
        "time_in_force": order_spec.time_in_force,
        "post_only": order_spec.post_only,
        "maker_intent": order_spec.maker_intent,
        "order_mode": order_spec.mode,
        "limit_price": limit_price,
        # WALL C (2026-06-01): for multi-level TAKER fills the sweep VWAP (average
        # fill price) differs from limit_price.  executor.py:1778 checks
        # sweep.average_price == intent.expected_fill_price_before_fee; storing the
        # pre-computed sweep VWAP here makes the executor validation pass without any
        # DB re-query.  When sweep_expected_fill_price is None (maker / passive path)
        # the field falls back to limit_price — identical to the legacy behaviour.
        "expected_fill_price_before_fee": sweep_expected_fill_price if sweep_expected_fill_price is not None else limit_price,
        "size": size,
        "notional_usd": notional,
        "max_slippage_bps": max_slippage_bps,
        "executable_snapshot_id": action["executable_snapshot_id"],
        "execution_price_type": "ExecutionPrice",
        "fee_deducted": True,
        "executable_snapshot_hash": executable_snapshot_hash,
        "cost_basis_hash": cost_basis_hash,
        "cost_basis_id": f"cost_basis:{cost_basis_hash[:16]}",
        "decision_source_context": decision_source_context_payload,
        "passive_maker_context": passive_maker_context_payload,
        "neg_risk": bool(action.get("neg_risk", False)),
        "tick_size": str(Decimal(str(tick_size))),
        "min_order_size": float(min_order_size),
        "fee_rate": float(fee_rate),
        "live_cap_usage_id": action["live_cap_usage_id"],
        "source": "existing_final_intent_builder",
        "submitted": False,
        "venue_order_id": None,
        "taker_fok_fak_live_enabled": bool(taker_fok_fak_live_enabled),
    }
    return _build_cert(
        claims.FINAL_INTENT,
        f"final_intent:{payload['event_id']}:{payload['final_intent_id']}",
        payload,
        decision_time,
        (actionable_cert, executable_snapshot_cert, quote_feasibility_cert, cost_model_cert, forecast_authority_cert),
    )


def _declared_max_slippage_bps(
    *,
    direction: str,
    order_mode: str,
    limit_price: float,
    expected_fill_price: float,
) -> float:
    if str(order_mode).strip().upper() != "TAKER":
        return 0.0
    expected = Decimal(str(expected_fill_price))
    limit = Decimal(str(limit_price))
    if expected <= 0:
        return 0.0
    if direction.startswith("buy_"):
        adverse = limit - expected
    elif direction.startswith("sell_"):
        adverse = expected - limit
    else:
        return 0.0
    if adverse <= 0:
        return 0.0
    return float(adverse / expected * Decimal("10000"))


def build_executor_expressibility_certificate(
    *,
    final_intent_cert: DecisionCertificate,
    executable_snapshot_cert: DecisionCertificate,
    live_cap_cert: DecisionCertificate,
    decision_time: datetime,
    executor_native_intent_hash: str,
    executor_name: str = "execute_final_intent",
    executor_capability_version: str = "existing_executor_passive_limit_v1",
) -> DecisionCertificate:
    if not executor_native_intent_hash:
        raise ValueError("executor_native_intent_hash required")
    final_intent = final_intent_cert.payload
    payload = {
        "event_id": final_intent["event_id"],
        "final_intent_id": final_intent["final_intent_id"],
        "executor_name": executor_name,
        "executor_capability_version": executor_capability_version,
        "can_express": True,
        "passed": True,
        "reason_code": "OK",
        "executor_native_intent_hash": executor_native_intent_hash,
        "order_type": final_intent["order_type"],
        "side": final_intent["side"],
        "direction": final_intent["direction"],
        "token_id": final_intent["token_id"],
        "condition_id": final_intent["condition_id"],
        "limit_price": final_intent["limit_price"],
        "size": final_intent["size"],
        "time_in_force": final_intent["time_in_force"],
        "post_only": final_intent["post_only"],
        "maker_intent": final_intent["maker_intent"],
        "tick_size": final_intent["tick_size"],
        "min_order_size": final_intent["min_order_size"],
        "neg_risk": final_intent["neg_risk"],
        "fee_rate": final_intent["fee_rate"],
    }
    return _build_cert(
        claims.EXECUTOR_EXPRESSIBILITY,
        f"executor_expressibility:{payload['event_id']}:{payload['final_intent_id']}",
        payload,
        decision_time,
        (final_intent_cert, executable_snapshot_cert, live_cap_cert),
    )


def build_execution_command_certificate_from_final_intent(
    *,
    actionable_cert: DecisionCertificate,
    final_intent_cert: DecisionCertificate,
    executor_expressibility_cert: DecisionCertificate,
    live_cap_cert: DecisionCertificate,
    pre_submit_revalidation_cert: DecisionCertificate,
    decision_time: datetime,
) -> DecisionCertificate:
    action = actionable_cert.payload
    final_intent = final_intent_cert.payload
    execution_command_id = (
        f"edli_exec_cmd:{action['event_id']}:{final_intent['final_intent_id']}:"
        f"{final_intent['token_id']}:{final_intent['direction']}"
    )
    idempotency_key = stable_hash(
        {
            "event_id": action["event_id"],
            "causal_snapshot_id": action["causal_snapshot_id"],
            "final_intent_id": final_intent["final_intent_id"],
            "token_id": final_intent["token_id"],
            "direction": final_intent["direction"],
            "limit_price": final_intent["limit_price"],
            "size": final_intent["size"],
            "mode": "LIVE",
        }
    )
    payload = {
        "event_id": action["event_id"],
        "actionable_certificate_hash": actionable_cert.certificate_hash,
        "final_intent_id": final_intent["final_intent_id"],
        "execution_command_id": execution_command_id,
        "executor_name": executor_expressibility_cert.payload["executor_name"],
        "order_type": final_intent["order_type"],
        "side": final_intent["side"],
        "direction": final_intent["direction"],
        "condition_id": final_intent["condition_id"],
        "token_id": final_intent["token_id"],
        "limit_price": final_intent["limit_price"],
        "size": final_intent["size"],
        "time_in_force": final_intent["time_in_force"],
        "post_only": final_intent["post_only"],
        "maker": final_intent["maker_intent"],
        "maker_intent": final_intent["maker_intent"],
        "neg_risk": final_intent["neg_risk"],
        "tick_size": final_intent["tick_size"],
        "min_order_size": final_intent["min_order_size"],
        "fee_rate": final_intent["fee_rate"],
        "idempotency_key": idempotency_key,
        "aggregate_id": pre_submit_revalidation_cert.payload["aggregate_id"],
        "aggregate_pre_submit_event_hash": pre_submit_revalidation_cert.payload["aggregate_event_hash"],
        "aggregate_execution_command_event_hash": pre_submit_revalidation_cert.payload.get(
            "aggregate_execution_command_event_hash"
        ),
        "submitted": False,
        "venue_order_id": None,
    }
    return _build_cert(
        claims.EXECUTION_COMMAND,
        f"execution_command:{payload['event_id']}:{payload['execution_command_id']}",
        payload,
        decision_time,
        (actionable_cert, final_intent_cert, executor_expressibility_cert, live_cap_cert, pre_submit_revalidation_cert),
    )


def build_execution_receipt_certificate(
    *,
    execution_command_cert: DecisionCertificate,
    decision_time: datetime,
    status: str = "SUBMIT_DISABLED",
    reason_code: str = "REAL_ORDER_SUBMIT_DISABLED",
    submit_started_at: str | None = None,
    submit_finished_at: str | None = None,
    venue_order_id: str | None = None,
    raw_response: Mapping[str, object] | None = None,
    raw_response_hash: str | None = None,
    reconciliation_followup_required: bool | None = None,
    venue_call_started: bool | None = None,
    venue_ack_received: bool | None = None,
    side_effect_known: bool | None = None,
) -> DecisionCertificate:
    command = execution_command_cert.payload
    response_hash = raw_response_hash or stable_hash(
        {
            "status": status,
            "reason_code": reason_code,
            "venue_order_id": venue_order_id,
            "raw_response": dict(raw_response or {}),
        }
    )
    payload = {
        "event_id": command["event_id"],
        "execution_command_id": command["execution_command_id"],
        "final_intent_id": command["final_intent_id"],
        "executor_name": command["executor_name"],
        "status": status,
        "submit_started_at": submit_started_at,
        "submit_finished_at": submit_finished_at,
        "venue_order_id": venue_order_id,
        "raw_response_hash": response_hash,
        "idempotency_key": command["idempotency_key"],
        "reason_code": reason_code,
    }
    if reconciliation_followup_required is not None:
        payload["reconciliation_followup_required"] = reconciliation_followup_required
    if venue_call_started is not None:
        payload["venue_call_started"] = venue_call_started
    if venue_ack_received is not None:
        payload["venue_ack_received"] = venue_ack_received
    if side_effect_known is not None:
        payload["side_effect_known"] = side_effect_known
    return _build_cert(
        claims.EXECUTION_RECEIPT,
        f"execution_receipt:{payload['event_id']}:{payload['execution_command_id']}:{status}",
        payload,
        decision_time,
        (execution_command_cert,),
    )


def build_live_cap_transition_certificate(
    *,
    live_cap_cert: DecisionCertificate,
    execution_receipt_cert: DecisionCertificate,
    decision_time: datetime,
    to_status: str,
    reason_code: str,
    projection_status: str | None = None,
    aggregate_event_hash: str | None = None,
) -> DecisionCertificate:
    live_cap = live_cap_cert.payload
    receipt = execution_receipt_cert.payload
    payload = {
        "event_id": live_cap["event_id"],
        "usage_id": live_cap["usage_id"],
        "from_status": live_cap["reservation_status"],
        "to_status": to_status,
        "projection_status": projection_status or to_status,
        "transition_reason": reason_code,
        "final_intent_id": receipt["final_intent_id"],
        "execution_command_id": receipt["execution_command_id"],
        "execution_receipt_hash": execution_receipt_cert.certificate_hash,
    }
    if aggregate_event_hash:
        payload["aggregate_cap_transition_event_hash"] = aggregate_event_hash
    return _build_cert(
        claims.LIVE_CAP_TRANSITION,
        f"live_cap_transition:{payload['usage_id']}:{payload['to_status']}:{payload['execution_command_id']}",
        payload,
        decision_time,
        (live_cap_cert, execution_receipt_cert),
    )


def build_pre_submit_revalidation_certificate(
    *,
    pre_submit_event: LiveOrderAggregateEvent,
    final_intent_cert: DecisionCertificate,
    live_cap_cert: DecisionCertificate,
    decision_time: datetime,
    execution_command_event_hash: str | None = None,
) -> DecisionCertificate:
    payload = {
        **pre_submit_event.payload,
        "aggregate_id": pre_submit_event.aggregate_id,
        "aggregate_event_id": pre_submit_event.aggregate_event_id,
        "aggregate_event_hash": pre_submit_event.event_hash,
        "aggregate_event_sequence": pre_submit_event.event_sequence,
        "aggregate_execution_command_event_hash": execution_command_event_hash,
        "final_intent_certificate_hash": final_intent_cert.certificate_hash,
        "live_cap_usage_id": live_cap_cert.payload["usage_id"],
    }
    return _build_cert(
        claims.PRE_SUBMIT_REVALIDATION,
        f"pre_submit_revalidation:{payload['event_id']}:{payload['final_intent_id']}:{pre_submit_event.event_hash[:16]}",
        payload,
        decision_time,
        (final_intent_cert, live_cap_cert),
    )


def _build_cert(
    certificate_type: str,
    semantic_key: str,
    payload: dict,
    decision_time: datetime,
    parents: Iterable[DecisionCertificate],
) -> DecisionCertificate:
    parent_tuple = tuple(parents)
    return build_certificate(
        certificate_type=certificate_type,
        semantic_key=semantic_key,
        claim_type=certificate_type,
        mode="LIVE",
        decision_time=decision_time,
        source_available_at=decision_time,
        agent_received_at=decision_time,
        persisted_at=decision_time,
        payload=payload,
        parent_edges=tuple(
            ParentEdge(_role(parent.certificate_type), parent.certificate_hash, parent.certificate_type)
            for parent in parent_tuple
        ),
        parent_certificates=parent_tuple,
        authority_id="edli.final_intent_executor_boundary",
        authority_version="v1",
        algorithm_id="edli.event_bound_execution_certificate_builder",
        algorithm_version="v1",
    )


class _OrderSpec:
    """Resolved order-type tuple for a governor-decided mode.

    The four fields move together: a maker tuple is post-only GTC/GTD; a taker
    tuple is FOK/FAK marketable-limit with post_only=False. Emitting any mixed
    tuple is a defect the three verifier layers will reject.
    """

    __slots__ = ("mode", "order_type", "time_in_force", "post_only", "maker_intent")

    def __init__(self, *, mode: str, order_type: str, time_in_force: str, post_only: bool, maker_intent: bool):
        self.mode = mode
        self.order_type = order_type
        self.time_in_force = time_in_force
        self.post_only = post_only
        self.maker_intent = maker_intent


_TAKER_ORDER_TYPES = {"FOK_LIMIT", "FAK_LIMIT"}
_TAKER_TIF = {"FOK", "FAK"}
_MAKER_ORDER_TYPES = {"LIMIT", "GTC_LIMIT", "POST_ONLY_LIMIT"}
_MAKER_TIF = {"GTC", "GTD"}


def _order_spec_for_mode(*, order_mode: str, order_type: str | None, time_in_force: str | None) -> _OrderSpec:
    mode = str(order_mode or "MAKER").strip().upper()
    if mode == "TAKER":
        resolved_tif = str(time_in_force or "FOK").strip().upper()
        if resolved_tif not in _TAKER_TIF:
            raise ValueError(f"taker mode requires FOK/FAK time_in_force, got {resolved_tif!r}")
        resolved_order_type = str(order_type or f"{resolved_tif}_LIMIT").strip().upper()
        if resolved_order_type not in _TAKER_ORDER_TYPES:
            raise ValueError(f"taker mode requires FOK_LIMIT/FAK_LIMIT order_type, got {resolved_order_type!r}")
        return _OrderSpec(
            mode="TAKER",
            order_type=resolved_order_type,
            time_in_force=resolved_tif,
            post_only=False,
            maker_intent=False,
        )
    if mode == "MAKER":
        resolved_tif = str(time_in_force or "GTC").strip().upper()
        if resolved_tif not in _MAKER_TIF:
            raise ValueError(f"maker mode requires GTC/GTD time_in_force, got {resolved_tif!r}")
        resolved_order_type = str(order_type or "POST_ONLY_LIMIT").strip().upper()
        if resolved_order_type not in _MAKER_ORDER_TYPES:
            raise ValueError(f"maker mode requires passive order_type, got {resolved_order_type!r}")
        return _OrderSpec(
            mode="MAKER",
            order_type=resolved_order_type,
            time_in_force=resolved_tif,
            post_only=True,
            maker_intent=True,
        )
    raise ValueError(f"unsupported order_mode {order_mode!r}; expected MAKER or TAKER")


def _branch_limit_price(
    *,
    side: str,
    order_mode: str,
    reservation: float,
    best_bid: float | None,
    best_ask: float | None,
    tick_size: float,
    passive_maker_context,
) -> float:
    """Branch-correct, reservation-capped limit price.

    RESERVATION-CAP INVARIANT: no order, maker or taker, is ever priced worse
    than ``reservation`` (= c_fee_adjusted). This is the structural anti-anti-
    alpha guard — a cross can never be -EV.

    BUY:  taker -> min(best_ask, reservation); maker -> min(best_bid+tick, reservation)
    SELL: taker -> max(best_bid, reservation); maker -> max(best_ask-tick, reservation)

    When bid/ask are unavailable, the price falls back to ``reservation`` (the
    pre-change behavior — rest at the reservation), which is always within cap.
    """
    bid = _coerce_price(best_bid, passive_maker_context, "best_bid")
    ask = _coerce_price(best_ask, passive_maker_context, "best_ask")
    if order_mode == "TAKER":
        if side == "BUY":
            far = ask if ask is not None else reservation
            return _tick_round_down(min(far, reservation), tick_size)
        near = bid if bid is not None else reservation
        return _tick_round_up(max(near, reservation), tick_size)
    # maker: improve the touch by one tick, capped by reservation
    if side == "BUY":
        improved = (bid + tick_size) if bid is not None else reservation
        return _tick_round_down(min(improved, reservation), tick_size)
    improved = (ask - tick_size) if ask is not None else reservation
    return _tick_round_up(max(improved, reservation), tick_size)


def _coerce_price(value, passive_maker_context, key: str) -> float | None:
    if value is not None:
        try:
            return float(value)
        except (TypeError, ValueError):
            return None
    if isinstance(passive_maker_context, Mapping):
        raw = passive_maker_context.get(key)
    else:
        raw = getattr(passive_maker_context, key, None)
    if raw in (None, ""):
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _tick_round_down(price: float, tick_size: float) -> float:
    if tick_size <= 0:
        return price
    import math

    return round(math.floor(price / tick_size + 1e-9) * tick_size, 10)


def _tick_round_up(price: float, tick_size: float) -> float:
    if tick_size <= 0:
        return price
    import math

    return round(math.ceil(price / tick_size - 1e-9) * tick_size, 10)


def _side_for_direction(direction: str) -> str:
    if direction in {"buy_yes", "buy_no"}:
        return "BUY"
    if direction in {"sell_yes", "sell_no"}:
        return "SELL"
    raise ValueError(f"unsupported EDLI direction: {direction!r}")


def _role(certificate_type: str) -> str:
    import re

    base = certificate_type.removesuffix("Certificate").replace("Evidence", "")
    return re.sub(r"(?<!^)(?=[A-Z])", "_", base).lower()


def _context_payload(context, field_name: str) -> dict[str, object]:
    if context is None:
        raise ValueError(f"{field_name} required")
    if isinstance(context, Mapping):
        payload = dict(context)
    elif hasattr(context, "__dict__"):
        payload = {
            key: value
            for key, value in vars(context).items()
            if not key.startswith("_")
        }
    else:
        raise ValueError(f"{field_name} required")
    if not payload:
        raise ValueError(f"{field_name} required")
    return payload


def _required_text(payload: Mapping[str, object], field: str) -> str:
    value = str(payload.get(field) or "").strip()
    if not value:
        raise ValueError(f"{field} missing")
    return value


__all__ = [
    "build_execution_command_certificate_from_final_intent",
    "build_execution_receipt_certificate",
    "build_executor_expressibility_certificate",
    "build_final_intent_certificate_from_actionable",
    "build_live_cap_transition_certificate",
    "build_pre_submit_revalidation_certificate",
    "verify_execution_command",
    "verify_execution_receipt",
    "verify_executor_expressibility",
    "verify_final_intent",
    "verify_live_cap_transition",
]
