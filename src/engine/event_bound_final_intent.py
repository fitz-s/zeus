"""Event-bound final-intent receipt contract for EDLI redemption."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from src.contracts.execution_price import ExecutionPrice


SideEffectStatus = Literal["NO_SUBMIT", "INTENT_BUILT", "COMMAND_CREATED", "SUBMITTED"]


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
