# Created: 2026-05-25
# Last reused or audited: 2026-05-25
# Authority basis: docs/operations/edli_v1/EDLI_REDEMPTION_FINAL_PACKAGE_SPEC.md §10, §11, §14 PR-D.
from __future__ import annotations

from datetime import datetime, timezone

from src.decision_kernel import claims
from src.decision_kernel.compiler import DecisionCompiler
from src.events.opportunity_event import ForecastSnapshotReadyPayload, make_opportunity_event
from src.events.reactor import EventSubmissionReceipt


def _event():
    payload = ForecastSnapshotReadyPayload(
        city="Chicago",
        target_date="2026-05-25",
        metric="high",
        source_id="opendata",
        source_run_id="run-1",
        cycle="00",
        track="live",
        snapshot_id="snap-1",
        snapshot_hash="hash-1",
        captured_at="2026-05-25T10:00:00+00:00",
        available_at="2026-05-25T10:01:00+00:00",
        required_fields_present=True,
        required_steps_present=True,
        member_count=51,
        min_members_floor=40,
        completeness_status="COMPLETE",
        required_steps=[0],
        observed_steps=[0],
        expected_members=51,
        source_run_status="SUCCESS",
        source_run_completeness_status="COMPLETE",
        coverage_completeness_status="COMPLETE",
        coverage_readiness_status="LIVE_ELIGIBLE",
    )
    return make_opportunity_event(
        event_type="FORECAST_SNAPSHOT_READY",
        entity_key="Chicago|2026-05-25|high",
        source="forecast_live",
        observed_at="2026-05-25T10:00:00+00:00",
        available_at="2026-05-25T10:01:00+00:00",
        received_at="2026-05-25T10:02:00+00:00",
        causal_snapshot_id="snap-1",
        payload=payload,
    )


def _receipt(event_id: str):
    return EventSubmissionReceipt(
        submitted=False,
        proof_accepted=True,
        event_id=event_id,
        causal_snapshot_id="snap-1",
        trade_score_positive=True,
        fdr_pass=True,
        fdr_family_id="family-1",
        fdr_hypothesis_count=2,
        kelly_pass=True,
        kelly_execution_price_type="ExecutionPrice",
        kelly_price_fee_deducted=True,
        kelly_size_usd=1.0,
        kelly_cost_basis_id="cost-1",
        kelly_decision_id="kelly-1",
        risk_decision_id="risk-1",
        final_intent_id="intent-1",
    )


def test_forecast_event_compiles_to_no_submit_decision_certificate():
    event = _event()
    result = DecisionCompiler().compile_no_submit(
        event,
        decision_time=datetime(2026, 5, 25, 10, 3, tzinfo=timezone.utc),
        receipt=_receipt(event.event_id),
    )

    assert result.status == "VERIFIED"
    assert result.no_submit_certificate is not None
    assert result.no_submit_certificate.certificate_type == claims.NO_SUBMIT_DECISION
    assert {cert.certificate_type for cert in result.certificates} >= {
        claims.CLOCK_MODE,
        claims.CAUSAL_EVENT,
        claims.CANDIDATE_EVIDENCE,
        claims.TESTING_PROTOCOL,
        claims.FDR,
        claims.KELLY_DRY_RUN,
        claims.RISK_LEVEL,
        claims.NO_SUBMIT_DECISION,
    }


def test_compile_failure_when_receipt_missing():
    event = _event()
    result = DecisionCompiler().compile_no_submit(
        event,
        decision_time=datetime(2026, 5, 25, 10, 3, tzinfo=timezone.utc),
    )
    assert result.status == "REJECTED"
    assert result.failures[0].reason_code == "NO_SUBMIT_RECEIPT_REQUIRED_FOR_TRANSITION_COMPILER"
