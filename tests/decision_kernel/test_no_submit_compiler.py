# Created: 2026-05-25
# Last reused or audited: 2026-05-25
# Authority basis: docs/operations/edli_v1/EDLI_REDEMPTION_FINAL_PACKAGE_SPEC.md §10, §11, §14 PR-D.
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone

from src.decision_kernel import claims
from src.decision_kernel.compiler import DecisionCompiler, build_transition_proof_bundle_from_receipt
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
    decision_time = datetime(2026, 5, 25, 10, 3, tzinfo=timezone.utc)
    receipt = _receipt(event.event_id)
    result = DecisionCompiler().compile_no_submit(
        event,
        decision_time=decision_time,
        proof_bundle=build_transition_proof_bundle_from_receipt(event, receipt, decision_time=decision_time),
    )

    assert result.status == "VERIFIED"
    assert result.no_submit_certificate is not None
    assert result.no_submit_certificate.certificate_type == claims.NO_SUBMIT_DECISION
    assert {cert.certificate_type for cert in result.certificates} >= {
        claims.CLOCK_MODE,
        claims.CAUSAL_EVENT,
        claims.SOURCE_TRUTH,
        claims.MARKET_TOPOLOGY,
        claims.FAMILY_CLOSURE,
        claims.FORECAST_AUTHORITY,
        claims.CALIBRATION,
        claims.BELIEF,
        claims.EXECUTABLE_SNAPSHOT,
        claims.QUOTE_FEASIBILITY,
        claims.COST_MODEL,
        claims.PRE_TRADE_EVIDENCE,
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
    assert result.failures[0].reason_code == "NO_SUBMIT_PROOF_BUNDLE_REQUIRED"


def test_fdr_certificate_payload_not_receipt_projection_only():
    event = _event()
    decision_time = datetime(2026, 5, 25, 10, 3, tzinfo=timezone.utc)
    receipt = _receipt(event.event_id)

    result = DecisionCompiler().compile_no_submit(
        event,
        decision_time=decision_time,
        proof_bundle=build_transition_proof_bundle_from_receipt(event, receipt, decision_time=decision_time),
    )

    fdr = next(cert for cert in result.certificates if cert.certificate_type == claims.FDR)
    assert "receipt_projection" not in fdr.payload
    assert fdr.payload["fdr_family_id"] == "family-1"
    assert fdr.payload["fdr_hypothesis_count"] == 2


def test_kelly_certificate_payload_contains_typed_execution_price_and_kelly_inputs():
    event = _event()
    decision_time = datetime(2026, 5, 25, 10, 3, tzinfo=timezone.utc)
    receipt = _receipt(event.event_id)

    result = DecisionCompiler().compile_no_submit(
        event,
        decision_time=decision_time,
        proof_bundle=build_transition_proof_bundle_from_receipt(event, receipt, decision_time=decision_time),
    )

    kelly = next(cert for cert in result.certificates if cert.certificate_type == claims.KELLY_DRY_RUN)
    assert kelly.payload["execution_price_type"] == "ExecutionPrice"
    assert kelly.payload["kelly_decision_id"] == "kelly-1"


def test_forecast_no_submit_certificate_requires_forecast_authority_parent():
    event = _event()
    decision_time = datetime(2026, 5, 25, 10, 3, tzinfo=timezone.utc)
    receipt = _receipt(event.event_id)
    bundle = build_transition_proof_bundle_from_receipt(event, receipt, decision_time=decision_time)
    bad_bundle = replace(bundle, forecast_authority=replace(bundle.forecast_authority, certificate_type="WrongCertificate"))

    result = DecisionCompiler().compile_no_submit(event, decision_time=decision_time, proof_bundle=bad_bundle)

    assert result.status == "REJECTED" or all(
        cert.certificate_type != claims.NO_SUBMIT_DECISION for cert in result.certificates
    )


def test_quote_certificate_uses_quote_clock_not_event_available_at():
    event = _event()
    decision_time = datetime(2026, 5, 25, 10, 3, tzinfo=timezone.utc)
    receipt = _receipt(event.event_id)

    result = DecisionCompiler().compile_no_submit(
        event,
        decision_time=decision_time,
        proof_bundle=build_transition_proof_bundle_from_receipt(event, receipt, decision_time=decision_time),
    )

    quote = next(cert for cert in result.certificates if cert.certificate_type == claims.QUOTE_FEASIBILITY)
    assert quote.header.source_available_at == decision_time
    assert quote.header.source_available_at.isoformat() != event.available_at


def test_no_submit_compile_rejects_event_persisted_after_decision_time():
    event = replace(_event(), created_at="2026-05-25T10:04:00+00:00")
    decision_time = datetime(2026, 5, 25, 10, 3, tzinfo=timezone.utc)

    result = DecisionCompiler().compile_no_submit(
        event,
        decision_time=decision_time,
        proof_bundle=build_transition_proof_bundle_from_receipt(event, _receipt(event.event_id), decision_time=decision_time),
    )

    assert result.status == "REJECTED"
    assert result.failures[0].reason_code == "EVENT_PERSISTED_AFTER_DECISION_TIME"
