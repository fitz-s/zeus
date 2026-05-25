"""Event-bound final-intent receipt contract for EDLI redemption."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from src.contracts.execution_price import ExecutionPrice


SideEffectStatus = Literal[
    "NO_SUBMIT",
    "INTENT_BUILT",
    "COMMAND_CREATED",
    "SUBMITTED",
    "REJECTED",
    "TIMEOUT_UNKNOWN",
    "ERROR_UNKNOWN",
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

    status: Literal["SUBMITTED", "REJECTED", "TIMEOUT_UNKNOWN", "ERROR_UNKNOWN"]
    reason_code: str = "OK"
    venue_order_id: str | None = None
    submit_started_at: str | None = None
    submit_finished_at: str | None = None
    raw_response: dict[str, object] = field(default_factory=dict)
    raw_response_hash: str | None = None
    reconciliation_followup_required: bool = False


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
