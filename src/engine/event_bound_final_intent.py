"""Event-bound final-intent receipt contract for EDLI redemption."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import sqlite3
from typing import Literal

from src.contracts.execution_price import ExecutionPrice
from src.decision_kernel.canonicalization import stable_hash
from src.decision_kernel.certificate import DecisionCertificate


SideEffectStatus = Literal[
    "NO_SUBMIT",
    "INTENT_BUILT",
    "COMMAND_CREATED",
    "SUBMITTED",
    "REJECTED",
    "TIMEOUT_UNKNOWN",
    "PRE_SUBMIT_ERROR",
    "POST_SUBMIT_UNKNOWN",
    "SUBMIT_DISABLED",
    "NOT_SUBMITTED_DRY_RUN",
]
RECEIPT_SCHEMA = "edli_event_bound_no_submit_v1"


@dataclass(frozen=True)
class EventBoundFinalIntent:
    final_intent_id: str
    event_id: str
    family_id: str
    candidate_id: str
    condition_id: str
    token_id: str
    direction: str
    executable_snapshot_id: str
    execution_price: ExecutionPrice


@dataclass(frozen=True)
class EventBoundFinalIntentReceipt:
    event_id: str
    causal_snapshot_id: str
    family_id: str
    candidate_id: str
    condition_id: str
    token_id: str
    direction: str
    executable_snapshot_id: str
    execution_price: ExecutionPrice
    trade_score_id: str
    fdr_family_id: str
    kelly_decision_id: str
    risk_decision_id: str
    final_intent_id: str
    command_id: str | None
    side_effect_status: SideEffectStatus


@dataclass(frozen=True)
class EventBoundExecutorSubmitResult:
    """Normalized result from the sanctioned executor boundary.

    The EDLI adapter consumes this small value object so tests can inject the
    existing executor boundary without importing venue or executor code into the
    reactor path.
    """

    status: Literal["SUBMITTED", "REJECTED", "TIMEOUT_UNKNOWN", "PRE_SUBMIT_ERROR", "POST_SUBMIT_UNKNOWN"]
    reason_code: str = "OK"
    venue_order_id: str | None = None
    submit_started_at: str | None = None
    submit_finished_at: str | None = None
    raw_response: dict[str, object] = field(default_factory=dict)
    raw_response_hash: str | None = None
    reconciliation_followup_required: bool = False
    venue_call_started: bool = False
    venue_ack_received: bool = False
    side_effect_known: bool = True


class EventBoundExecutorExpressibilityError(ValueError):
    """Raised when EDLI proof cannot be expressed as the existing executor intent."""


class PreVenueSubmitError(ValueError):
    """Raised by the executor when a submit is rejected BEFORE any venue call.

    F-class deadlock antibody (2026-06-01): the executor validates executable
    depth / snapshot identity BEFORE contacting the venue (execute_final_intent
    runs _final_intent_snapshot_metadata + _legacy_entry_intent_from_final prior
    to _live_order). A failure there means the order PROVABLY never reached the
    venue — there is no indeterminate side effect to reconcile. The EDLI submit
    boundary classifies this as a TERMINAL ``PRE_SUBMIT_ERROR`` (venue_call_started
    =False), which releases the LIVE_CAP reservation and terminates the aggregate.
    Without this type, such pre-venue rejections were swept into the generic
    ``except Exception`` and mislabeled ``POST_SUBMIT_UNKNOWN`` (venue_call_started
    =True), leaving an unresolved-submit + held-cap that crash-loops boot at the
    edli_live readiness gate.
    """


def submit_event_bound_final_intent_via_existing_executor(
    *,
    final_intent_cert: DecisionCertificate,
    execution_command_cert: DecisionCertificate,
    conn: sqlite3.Connection,
    decision_time: datetime,
    snapshot_conn: sqlite3.Connection | None = None,
    executor_submit=None,
) -> EventBoundExecutorSubmitResult:
    """Submit EDLI's verified command through the existing executor seam.

    This engine-layer boundary is the only production bridge from EDLI
    certificates to the live executor. It intentionally accepts certificates,
    constructs the executor-native final intent, and returns a normalized
    receipt surface without interpreting fills.
    """

    started_at = decision_time.astimezone(timezone.utc).isoformat()
    try:
        intent = _final_execution_intent_from_cert(final_intent_cert, execution_command_cert)
        if executor_submit is None:
            from src.execution.executor import execute_final_intent as _submit

            executor_submit = _submit
        result = executor_submit(
            intent,
            conn=conn,
            decision_id=str(execution_command_cert.payload["execution_command_id"]),
            snapshot_conn=snapshot_conn if snapshot_conn is not None else conn,
        )
    except EventBoundExecutorExpressibilityError:
        raise
    except PreVenueSubmitError as exc:
        # PRE-VENUE rejection: the executor failed validation (e.g. executable
        # depth DEPTH_INSUFFICIENT) BEFORE any venue call. The order provably
        # never reached the venue, so the side effect is KNOWN (none) and there
        # is nothing to reconcile. Terminal PRE_SUBMIT_ERROR → cap RELEASED.
        return EventBoundExecutorSubmitResult(
            status="PRE_SUBMIT_ERROR",
            reason_code=f"EXECUTOR_PRE_VENUE_REJECTED:{exc}",
            submit_started_at=started_at,
            submit_finished_at=datetime.now(timezone.utc).isoformat(),
            raw_response={"error": str(exc), "stage": "existing_executor_pre_venue"},
            reconciliation_followup_required=False,
            venue_call_started=False,
            venue_ack_received=False,
            side_effect_known=True,
        )
    except Exception as exc:
        return EventBoundExecutorSubmitResult(
            status="POST_SUBMIT_UNKNOWN",
            reason_code=f"EXECUTOR_SUBMIT_UNKNOWN:{exc}",
            submit_started_at=started_at,
            submit_finished_at=datetime.now(timezone.utc).isoformat(),
            raw_response={"error": str(exc), "stage": "existing_executor_submit"},
            reconciliation_followup_required=True,
            venue_call_started=True,
            venue_ack_received=False,
            side_effect_known=False,
        )
    return _executor_order_result_to_submit_result(result, started_at=started_at)


def validate_final_intent_cert_for_existing_executor(final_intent_cert: DecisionCertificate) -> str:
    """Return the executor-native intent hash only if the final intent is expressible."""

    return stable_hash(_final_execution_intent_from_payload(final_intent_cert.payload))


def _final_execution_intent_from_cert(
    final_intent_cert: DecisionCertificate,
    execution_command_cert: DecisionCertificate,
):
    final_payload = final_intent_cert.payload
    command_payload = execution_command_cert.payload
    _require_payload_match(final_payload, command_payload, "event_id")
    _require_payload_match(final_payload, command_payload, "final_intent_id")
    _require_payload_match(final_payload, command_payload, "token_id")
    _require_payload_match(final_payload, command_payload, "direction")
    return _final_execution_intent_from_payload(final_payload)


def _final_execution_intent_from_payload(final_payload: dict):
    from src.contracts.execution_intent import (
        DecisionSourceContext,
        FinalExecutionIntent,
        PassiveMakerExecutionContext,
        quantize_submit_shares_for_venue_at_most,
    )

    snapshot_hash = _required_text(final_payload, "executable_snapshot_hash")
    cost_basis_hash = _required_text(final_payload, "cost_basis_hash")
    cost_basis_id = str(final_payload.get("cost_basis_id") or f"cost_basis:{cost_basis_hash[:16]}")
    if cost_basis_id != f"cost_basis:{cost_basis_hash[:16]}":
        raise EventBoundExecutorExpressibilityError("cost_basis_id does not match cost_basis_hash")
    decision_source_payload = final_payload.get("decision_source_context")
    decision_source_context = DecisionSourceContext.from_forecast_context(decision_source_payload)
    if decision_source_context is None:
        raise EventBoundExecutorExpressibilityError("decision_source_context missing")
    executor_order_type = str(final_payload.get("executor_order_type") or final_payload.get("time_in_force") or "")
    is_taker = (
        final_payload.get("post_only") is False
        and final_payload.get("maker_intent") is False
        and executor_order_type in {"FOK", "FAK"}
    )
    # WALL #1 (2026-06-01): passive_maker_context is MAKER-ONLY. A taker FOK/FAK
    # crosses the JIT book at submit and carries no maker context (the cert builder
    # emits None for taker). Require/parse it ONLY for the maker tuple; a taker order
    # passes None through to FinalExecutionIntent, which accepts None for the
    # marketable_limit_depth_bound order_policy (execution_intent.py:1735 requires the
    # context only for post_only_passive_limit). This is the executor-translator
    # instance of the same maker-only coupling that produced the dominant live wall.
    passive_payload = final_payload.get("passive_maker_context")
    if is_taker:
        passive_maker_context = None
    else:
        if not isinstance(passive_payload, dict):
            raise EventBoundExecutorExpressibilityError("passive_maker_context missing")
        passive_maker_context = PassiveMakerExecutionContext(
            spread_usd=_decimal(passive_payload.get("spread_usd"), "passive_maker_context.spread_usd"),
            quote_age_ms=int(passive_payload.get("quote_age_ms", 0)),
            expected_fill_probability=_decimal(
                passive_payload.get("expected_fill_probability"),
                "passive_maker_context.expected_fill_probability",
            ),
            queue_depth_ahead=_optional_decimal(passive_payload.get("queue_depth_ahead")),
            adverse_selection_score=_optional_decimal(passive_payload.get("adverse_selection_score")),
            orderbook_hash_age_ms=(
                None
                if passive_payload.get("orderbook_hash_age_ms") is None
                else int(passive_payload["orderbook_hash_age_ms"])
            ),
        )
    if is_taker:
        # Taker path is authorized when the governor-decided cert carries the full
        # taker tuple (post_only False, maker_intent False, FOK/FAK). Wave-2 item 8
        # (2026-06-12): the taker FOK/FAK law is verified and live — its legality is
        # now UNCONDITIONAL inside final-intent construction. The governor cert's
        # taker tuple is the single authority; the former config flag and its OFF
        # branch are deleted.
        order_policy = "marketable_limit_depth_bound"
        post_only = False
    else:
        if final_payload.get("post_only") is not True or final_payload.get("maker_intent") is not True:
            raise EventBoundExecutorExpressibilityError("current executor law requires post_only maker intent")
        if executor_order_type not in {"GTC", "GTD"}:
            raise EventBoundExecutorExpressibilityError("post_only maker final intent requires GTC/GTD executor_order_type")
        order_policy = "post_only_passive_limit"
        post_only = True
    limit_price = _decimal(final_payload.get("limit_price"), "limit_price")
    size = quantize_submit_shares_for_venue_at_most(
        str(final_payload["direction"]),
        _decimal(final_payload.get("size"), "size"),
        final_limit_price=limit_price,
        order_type=executor_order_type,
        tick_size=_decimal(final_payload.get("tick_size"), "tick_size"),
    )
    # Wall C (2026-06-01): for TAKER FOK orders the sweep VWAP (expected fill) may
    # differ from limit_price (multi-level book).  The cert builder stores the
    # pre-computed sweep average as "expected_fill_price_before_fee"; fall back to
    # limit_price for maker/passive certs that omit it.
    expected_fill = _decimal(
        final_payload.get("expected_fill_price_before_fee", final_payload.get("limit_price")),
        "expected_fill_price_before_fee",
    )
    return FinalExecutionIntent(
        hypothesis_id=str(final_payload.get("candidate_id") or final_payload["final_intent_id"]),
        selected_token_id=str(final_payload["token_id"]),
        direction=str(final_payload["direction"]),
        size_kind="shares",
        size_value=size,
        submitted_shares=size,
        final_limit_price=limit_price,
        expected_fill_price_before_fee=expected_fill,
        fee_adjusted_execution_price=expected_fill,
        order_policy=order_policy,
        order_type=executor_order_type,
        post_only=post_only,
        cancel_after=_cancel_after(final_payload),
        snapshot_id=str(final_payload["executable_snapshot_id"]),
        snapshot_hash=snapshot_hash,
        cost_basis_id=cost_basis_id,
        cost_basis_hash=cost_basis_hash,
        max_slippage_bps=_decimal(final_payload.get("max_slippage_bps", "0"), "max_slippage_bps"),
        tick_size=_decimal(final_payload.get("tick_size"), "tick_size"),
        min_order_size=_decimal(final_payload.get("min_order_size"), "min_order_size"),
        fee_rate=Decimal("0"),
        neg_risk=bool(final_payload.get("neg_risk", False)),
        event_id=str(final_payload.get("market_event_id") or final_payload["event_id"]),
        resolution_window=str(final_payload.get("resolution_window") or "default"),
        correlation_key=str(final_payload["final_intent_id"]),
        decision_source_context=decision_source_context,
        passive_maker_context=passive_maker_context,
        taker_quality_proof=(
            final_payload.get("taker_quality_proof") if is_taker else None
        ),
    )


def _executor_order_result_to_submit_result(result, *, started_at: str) -> EventBoundExecutorSubmitResult:
    status = str(getattr(result, "status", "") or "").lower()
    result_reason = str(getattr(result, "reason", None) or "")
    raw_response = {
        "status": getattr(result, "status", None),
        "reason": getattr(result, "reason", None),
        "command_state": getattr(result, "command_state", None),
        "order_id": getattr(result, "order_id", None),
        "external_order_id": getattr(result, "external_order_id", None),
    }
    if status in {"pending", "filled"}:
        receipt_status = "SUBMITTED"
        reason = "OK"
        reconcile = False
    elif status in {"rejected", "cancelled"}:
        reason = result_reason or "EXECUTOR_REJECTED"
        receipt_status = (
            "PRE_SUBMIT_ERROR"
            if _executor_rejection_is_pre_submit(reason)
            else "REJECTED"
        )
        reconcile = False
    elif status == "unknown_side_effect":
        receipt_status = "POST_SUBMIT_UNKNOWN"
        reason = str(getattr(result, "reason", None) or "EXECUTOR_UNKNOWN_SIDE_EFFECT")
        reconcile = True
    else:
        receipt_status = "PRE_SUBMIT_ERROR"
        reason = str(getattr(result, "reason", None) or f"EXECUTOR_STATUS_UNSUPPORTED:{status}")
        reconcile = False
    return EventBoundExecutorSubmitResult(
        status=receipt_status,  # type: ignore[arg-type]
        reason_code=reason,
        venue_order_id=getattr(result, "order_id", None) or getattr(result, "external_order_id", None),
        submit_started_at=getattr(result, "zeus_submit_intent_time", None) or started_at,
        submit_finished_at=getattr(result, "venue_ack_time", None) or datetime.now(timezone.utc).isoformat(),
        raw_response=raw_response,
        reconciliation_followup_required=reconcile,
        venue_call_started=receipt_status in {"SUBMITTED", "REJECTED", "TIMEOUT_UNKNOWN", "POST_SUBMIT_UNKNOWN"},
        venue_ack_received=receipt_status in {"SUBMITTED", "REJECTED"},
        side_effect_known=receipt_status in {"SUBMITTED", "REJECTED", "PRE_SUBMIT_ERROR"},
    )


def _executor_rejection_is_pre_submit(reason: str) -> bool:
    """True when an executor rejection happened before any venue call.

    These are designed local gates at the existing executor boundary. They must
    reject the order, but they must not masquerade as venue rejects/ACKs in EDLI
    status pulses or aggregate side-effect evidence.
    """

    text = str(reason or "")
    return text.startswith(
        (
            "entry_cooldown:",
            "entries_paused:",
            "duplicate_entry_same_token:",
            "executable_snapshot_gate:",
            "pre_submit_collateral_reservation_failed:",
        )
    )


def _require_payload_match(left: dict, right: dict, field: str) -> None:
    if str(left.get(field) or "") != str(right.get(field) or ""):
        raise EventBoundExecutorExpressibilityError(f"{field} mismatch")


def _required_text(payload: dict, field: str) -> str:
    value = str(payload.get(field) or "").strip()
    if not value:
        raise EventBoundExecutorExpressibilityError(f"{field} missing")
    return value


def _decimal(value, field: str) -> Decimal:
    if value is None or value == "":
        raise EventBoundExecutorExpressibilityError(f"{field} missing")
    return Decimal(str(value))


def _optional_decimal(value) -> Decimal | None:
    if value is None or value == "":
        return None
    return Decimal(str(value))


def _cancel_after(payload: dict) -> datetime:
    raw = payload.get("cancel_after")
    if raw:
        parsed = datetime.fromisoformat(str(raw))
        return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc).replace(microsecond=0) + timedelta(minutes=15)


def build_event_bound_final_intent_receipt(
    *,
    intent: EventBoundFinalIntent,
    causal_snapshot_id: str,
    trade_score_id: str,
    fdr_family_id: str,
    kelly_decision_id: str,
    risk_decision_id: str,
    command_id: str | None = None,
    live_submit_enabled: bool = False,
) -> EventBoundFinalIntentReceipt:
    intent.execution_price.assert_kelly_safe()
    status: SideEffectStatus = "INTENT_BUILT" if live_submit_enabled else "NO_SUBMIT"
    return EventBoundFinalIntentReceipt(
        event_id=intent.event_id,
        causal_snapshot_id=causal_snapshot_id,
        family_id=intent.family_id,
        candidate_id=intent.candidate_id,
        condition_id=intent.condition_id,
        token_id=intent.token_id,
        direction=intent.direction,
        executable_snapshot_id=intent.executable_snapshot_id,
        execution_price=intent.execution_price,
        trade_score_id=trade_score_id,
        fdr_family_id=fdr_family_id,
        kelly_decision_id=kelly_decision_id,
        risk_decision_id=risk_decision_id,
        final_intent_id=intent.final_intent_id,
        command_id=command_id,
        side_effect_status=status,
    )


def serialize_event_bound_final_intent_receipt(
    receipt: EventBoundFinalIntentReceipt,
    *,
    trade_score_positive: bool,
    fdr_pass: bool,
    fdr_hypothesis_count: int,
    kelly_pass: bool,
    kelly_size_usd: float,
    kelly_cost_basis_id: str,
    reason: str = "event_bound_final_intent_no_submit",
) -> dict[str, object]:
    """Serialize the typed no-submit receipt for the EDLI reactor adapter.

    The cycle summary may copy this dictionary for observability, but the proof
    source is the typed receipt object produced by this module.
    """

    receipt.execution_price.assert_kelly_safe()
    submitted = receipt.side_effect_status == "SUBMITTED"
    return {
        "schema": RECEIPT_SCHEMA,
        "proof_accepted": True,
        "submitted": submitted,
        "event_id": receipt.event_id,
        "causal_snapshot_id": receipt.causal_snapshot_id,
        "family_id": receipt.family_id,
        "candidate_id": receipt.candidate_id,
        "condition_id": receipt.condition_id,
        "token_id": receipt.token_id,
        "direction": receipt.direction,
        "executable_snapshot_id": receipt.executable_snapshot_id,
        "trade_score_id": receipt.trade_score_id,
        "trade_score_positive": bool(trade_score_positive),
        "fdr_pass": bool(fdr_pass),
        "fdr_family_id": receipt.fdr_family_id,
        "fdr_hypothesis_count": int(fdr_hypothesis_count),
        "kelly_pass": bool(kelly_pass),
        "kelly_decision_id": receipt.kelly_decision_id,
        "kelly_execution_price_type": receipt.execution_price.__class__.__name__,
        "kelly_price_fee_deducted": bool(receipt.execution_price.fee_deducted),
        "kelly_size_usd": float(kelly_size_usd),
        "kelly_cost_basis_id": str(kelly_cost_basis_id),
        "risk_decision_id": receipt.risk_decision_id,
        "final_intent_id": receipt.final_intent_id,
        "command_id": receipt.command_id,
        "side_effect_status": receipt.side_effect_status,
        "reason": reason,
    }
