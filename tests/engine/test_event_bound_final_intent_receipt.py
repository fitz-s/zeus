# Created: 2026-05-24
# Last reused/audited: 2026-05-24
# Authority basis: docs/operations/edli_v1/PR328_REDEMPTION_PACKAGE.md R7 proof.

import inspect

from src.contracts.execution_price import ExecutionPrice
from src.engine import event_bound_final_intent
from src.engine.event_bound_final_intent import (
    EventBoundFinalIntent,
    build_event_bound_final_intent_receipt,
    serialize_event_bound_final_intent_receipt,
)


def _price():
    return ExecutionPrice(0.40, "ask", fee_deducted=False, currency="probability_units").with_taker_fee()


def test_final_intent_receipt_has_event_bound_fields_no_submit():
    intent = EventBoundFinalIntent(
        final_intent_id="intent-1",
        event_id="event-1",
        family_id="family-1",
        candidate_id="candidate-1",
        condition_id="condition-1",
        token_id="yes-1",
        direction="buy_yes",
        executable_snapshot_id="snapshot-1",
        execution_price=_price(),
    )
    receipt = build_event_bound_final_intent_receipt(
        intent=intent,
        causal_snapshot_id="causal-1",
        trade_score_id="score-1",
        fdr_family_id="fdr-family-1",
        kelly_decision_id="kelly-1",
        risk_decision_id="risk-1",
    )

    assert receipt.event_id == "event-1"
    assert receipt.final_intent_id == "intent-1"
    assert receipt.side_effect_status == "NO_SUBMIT"
    assert receipt.command_id is None


def test_no_submit_receipt_serializes_proof_accepted_not_order_submitted():
    intent = EventBoundFinalIntent(
        final_intent_id="intent-1",
        event_id="event-1",
        family_id="family-1",
        candidate_id="candidate-1",
        condition_id="condition-1",
        token_id="yes-1",
        direction="buy_yes",
        executable_snapshot_id="snapshot-1",
        execution_price=_price(),
    )
    receipt = build_event_bound_final_intent_receipt(
        intent=intent,
        causal_snapshot_id="causal-1",
        trade_score_id="score-1",
        fdr_family_id="fdr-family-1",
        kelly_decision_id="kelly-1",
        risk_decision_id="risk-1",
    )

    serialized = serialize_event_bound_final_intent_receipt(
        receipt,
        trade_score_positive=True,
        fdr_pass=True,
        fdr_hypothesis_count=2,
        kelly_pass=True,
        kelly_size_usd=1.0,
        kelly_cost_basis_id="kelly-cost-1",
    )

    assert serialized["proof_accepted"] is True
    assert serialized["submitted"] is False
    assert serialized["side_effect_status"] == "NO_SUBMIT"


def test_final_intent_receipt_module_has_no_venue_adapter_import():
    source = inspect.getsource(event_bound_final_intent)

    assert "venue_adapter" not in source
    assert "executor" not in source
