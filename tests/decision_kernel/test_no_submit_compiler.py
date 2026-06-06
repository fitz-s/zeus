# Created: 2026-05-25
# Last reused or audited: 2026-05-25
# Authority basis: docs/operations/edli_v1/EDLI_REDEMPTION_FINAL_PACKAGE_SPEC.md §10, §11, §14 PR-D.
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone

from src.decision_kernel import claims
from src.decision_kernel.compiler import (
    FORECAST_LIVE_ELIGIBLE_STATUS,
    FORECAST_READER_STATUS_ALIASES,
    DecisionCompiler,
    normalize_forecast_reader_status,
)
from tests.decision_kernel.no_submit_fixtures import build_test_no_submit_proof_bundle
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
        executable_snapshot_id="snapshot-exec-1",
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
        proof_bundle=build_test_no_submit_proof_bundle(event, receipt, decision_time=decision_time),
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


def test_no_submit_decision_certificate_generated_time_semantics():
    event = _event()
    decision_time = datetime(2026, 5, 25, 10, 3, tzinfo=timezone.utc)
    result = DecisionCompiler().compile_no_submit(
        event,
        decision_time=decision_time,
        proof_bundle=build_test_no_submit_proof_bundle(event, _receipt(event.event_id), decision_time=decision_time),
    )

    assert result.status == "VERIFIED"
    assert result.no_submit_certificate is not None
    assert result.no_submit_certificate.header.persisted_at == decision_time
    assert result.no_submit_certificate.payload["generated_at_decision_time"] is True
    assert result.no_submit_certificate.payload["header_persisted_at_semantics"] == "decision_kernel_generated_at_decision_time"


def test_compile_failure_when_receipt_missing():
    event = _event()
    result = DecisionCompiler().compile_no_submit(
        event,
        decision_time=datetime(2026, 5, 25, 10, 3, tzinfo=timezone.utc),
    )
    assert result.status == "REJECTED"
    assert result.failures[0].reason_code == "NO_SUBMIT_PROOF_BUNDLE_REQUIRED"


def test_no_submit_certificate_rejects_proof_accepted_false():
    event = _event()
    decision_time = datetime(2026, 5, 25, 10, 3, tzinfo=timezone.utc)
    bundle = build_test_no_submit_proof_bundle(event, _receipt(event.event_id), decision_time=decision_time)
    bad_projection = {**bundle.no_submit_projection, "proof_accepted": False}
    result = DecisionCompiler().compile_no_submit(
        event,
        decision_time=decision_time,
        proof_bundle=replace(bundle, no_submit_projection=bad_projection),
    )

    assert result.status == "REJECTED"
    assert "projection.proof_accepted" in (result.failures[0].reason_detail or "")


def test_no_submit_certificate_rejects_missing_proof_accepted():
    event = _event()
    decision_time = datetime(2026, 5, 25, 10, 3, tzinfo=timezone.utc)
    bundle = build_test_no_submit_proof_bundle(event, _receipt(event.event_id), decision_time=decision_time)
    bad_projection = {key: value for key, value in bundle.no_submit_projection.items() if key != "proof_accepted"}
    result = DecisionCompiler().compile_no_submit(
        event,
        decision_time=decision_time,
        proof_bundle=replace(bundle, no_submit_projection=bad_projection),
    )

    assert result.status == "REJECTED"
    assert "projection.proof_accepted" in (result.failures[0].reason_detail or "")


def test_no_submit_rejects_projection_event_id_mismatch():
    event = _event()
    decision_time = datetime(2026, 5, 25, 10, 3, tzinfo=timezone.utc)
    bundle = build_test_no_submit_proof_bundle(event, _receipt(event.event_id), decision_time=decision_time)
    result = DecisionCompiler().compile_no_submit(
        event,
        decision_time=decision_time,
        proof_bundle=replace(bundle, no_submit_projection={**bundle.no_submit_projection, "event_id": "wrong"}),
    )

    assert result.status == "REJECTED"
    assert "projection.event_id" in (result.failures[0].reason_detail or "")


def test_no_submit_rejects_projection_final_intent_mismatch():
    event = _event()
    decision_time = datetime(2026, 5, 25, 10, 3, tzinfo=timezone.utc)
    bundle = build_test_no_submit_proof_bundle(event, _receipt(event.event_id), decision_time=decision_time)
    result = DecisionCompiler().compile_no_submit(
        event,
        decision_time=decision_time,
        proof_bundle=replace(bundle, no_submit_projection={**bundle.no_submit_projection, "final_intent_id": "wrong"}),
    )

    assert result.status == "REJECTED"
    assert "projection.final_intent_id" in (result.failures[0].reason_detail or "")


def test_no_submit_rejects_projection_submitted_true():
    event = _event()
    decision_time = datetime(2026, 5, 25, 10, 3, tzinfo=timezone.utc)
    bundle = build_test_no_submit_proof_bundle(event, _receipt(event.event_id), decision_time=decision_time)
    result = DecisionCompiler().compile_no_submit(
        event,
        decision_time=decision_time,
        proof_bundle=replace(bundle, no_submit_projection={**bundle.no_submit_projection, "submitted": True}),
    )

    assert result.status == "REJECTED"
    assert "projection.submitted" in (result.failures[0].reason_detail or "")


def test_fdr_certificate_payload_not_receipt_projection_only():
    event = _event()
    decision_time = datetime(2026, 5, 25, 10, 3, tzinfo=timezone.utc)
    receipt = _receipt(event.event_id)

    result = DecisionCompiler().compile_no_submit(
        event,
        decision_time=decision_time,
        proof_bundle=build_test_no_submit_proof_bundle(event, receipt, decision_time=decision_time),
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
        proof_bundle=build_test_no_submit_proof_bundle(event, receipt, decision_time=decision_time),
    )

    kelly = next(cert for cert in result.certificates if cert.certificate_type == claims.KELLY_DRY_RUN)
    assert kelly.payload["execution_price_type"] == "ExecutionPrice"
    assert kelly.payload["kelly_decision_id"] == "kelly-1"


def test_forecast_no_submit_certificate_requires_forecast_authority_parent():
    event = _event()
    decision_time = datetime(2026, 5, 25, 10, 3, tzinfo=timezone.utc)
    receipt = _receipt(event.event_id)
    bundle = build_test_no_submit_proof_bundle(event, receipt, decision_time=decision_time)
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
        proof_bundle=build_test_no_submit_proof_bundle(event, receipt, decision_time=decision_time),
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
        proof_bundle=build_test_no_submit_proof_bundle(event, _receipt(event.event_id), decision_time=decision_time),
    )

    assert result.status == "REJECTED"
    assert result.failures[0].reason_code == "EVENT_PERSISTED_AFTER_DECISION_TIME"


def test_no_submit_rejects_forecast_snapshot_parent_mismatch():
    event = _event()
    decision_time = datetime(2026, 5, 25, 10, 3, tzinfo=timezone.utc)
    bundle = build_test_no_submit_proof_bundle(event, _receipt(event.event_id), decision_time=decision_time)
    bad_forecast = replace(bundle.forecast_authority, payload={**bundle.forecast_authority.payload, "snapshot_id": "wrong"})
    result = DecisionCompiler().compile_no_submit(
        event,
        decision_time=decision_time,
        proof_bundle=replace(bundle, forecast_authority=bad_forecast),
    )

    assert result.status == "REJECTED"
    assert result.failures[0].reason_code == "NO_SUBMIT_CERTIFICATE_REJECTED"
    assert "forecast.snapshot_id" in (result.failures[0].reason_detail or "")


def _bundle_with_reader_elected_snapshot(event, receipt, *, decision_time, elected_snapshot_id):
    """Build a bundle where the forecast reader ELECTED an executable snapshot that differs
    from the event's causal (trigger) snapshot.

    Mirrors the live reader-elect path in event_reactor_adapter._forecast_snapshot_row_for_event:
    the causal cycle's source_run is still re-ingesting members, so the reader's causality gate
    drops the causal snapshot and elects the freshest fully-captured FULL_CONTRIBUTOR. The
    executable authority chain (forecast.snapshot_id == source_truth.derived_from_snapshot_id ==
    belief.forecast_snapshot_id) carries the ELECTED id; the causal-provenance chain
    (source_truth.causal_snapshot_id == event.causal_snapshot_id) carries the causal id.
    """
    bundle = build_test_no_submit_proof_bundle(event, receipt, decision_time=decision_time)
    causal = event.causal_snapshot_id
    assert elected_snapshot_id != causal
    forecast = replace(
        bundle.forecast_authority,
        payload={**bundle.forecast_authority.payload, "snapshot_id": elected_snapshot_id, "identity": elected_snapshot_id},
    )
    source = replace(
        bundle.source_truth,
        payload={
            **bundle.source_truth.payload,
            # causal-provenance chain: causal trigger snapshot
            "snapshot_id": causal,
            "causal_snapshot_id": causal,
            # executable-authority chain: reader-elected snapshot
            "derived_from_snapshot_id": elected_snapshot_id,
        },
    )
    belief = replace(
        bundle.belief,
        payload={**bundle.belief.payload, "forecast_snapshot_id": elected_snapshot_id},
    )
    return replace(bundle, forecast_authority=forecast, source_truth=source, belief=belief)


def test_no_submit_accepts_reader_elected_snapshot_differing_from_causal():
    """RELATIONSHIP: when the forecast reader elects an executable snapshot that differs from the
    event's causal trigger snapshot (source_run still ingesting), the no-submit cert MUST accept it.

    The causal id is preserved as provenance (source_truth.causal_snapshot_id == event.causal); the
    elected id is the single forecast authority (forecast.snapshot_id == derived_from_snapshot_id ==
    belief.forecast_snapshot_id). This is the live FORECAST_READER_SNAPSHOT_MISMATCH category that
    previously produced "forecast.snapshot_id != event.causal_snapshot_id" rejections.
    """
    event = _event()
    decision_time = datetime(2026, 5, 25, 10, 3, tzinfo=timezone.utc)
    bundle = _bundle_with_reader_elected_snapshot(
        event, _receipt(event.event_id), decision_time=decision_time, elected_snapshot_id="snap-elected-earlier",
    )
    result = DecisionCompiler().compile_no_submit(event, decision_time=decision_time, proof_bundle=bundle)

    assert result.status == "VERIFIED", (result.failures[0].reason_detail if result.failures else None)
    assert result.no_submit_certificate is not None


def test_no_submit_still_rejects_elected_snapshot_inconsistent_with_belief():
    """RELATIONSHIP guard: the elected-snapshot acceptance must NOT weaken the executable-authority
    chain. If belief.forecast_snapshot_id disagrees with forecast.snapshot_id, the cert still rejects.
    """
    event = _event()
    decision_time = datetime(2026, 5, 25, 10, 3, tzinfo=timezone.utc)
    bundle = _bundle_with_reader_elected_snapshot(
        event, _receipt(event.event_id), decision_time=decision_time, elected_snapshot_id="snap-elected-earlier",
    )
    bad_belief = replace(bundle.belief, payload={**bundle.belief.payload, "forecast_snapshot_id": "snap-elected-other"})
    result = DecisionCompiler().compile_no_submit(event, decision_time=decision_time, proof_bundle=replace(bundle, belief=bad_belief))

    assert result.status == "REJECTED"
    assert "belief.forecast_snapshot_id" in (result.failures[0].reason_detail or "")


def test_no_submit_still_rejects_causal_provenance_broken():
    """RELATIONSHIP guard: causal-provenance chain stays mandatory. If source_truth.causal_snapshot_id
    no longer matches event.causal_snapshot_id, the cert still rejects even with a valid elected id.
    """
    event = _event()
    decision_time = datetime(2026, 5, 25, 10, 3, tzinfo=timezone.utc)
    bundle = _bundle_with_reader_elected_snapshot(
        event, _receipt(event.event_id), decision_time=decision_time, elected_snapshot_id="snap-elected-earlier",
    )
    bad_source = replace(bundle.source_truth, payload={**bundle.source_truth.payload, "causal_snapshot_id": "snap-not-causal"})
    result = DecisionCompiler().compile_no_submit(event, decision_time=decision_time, proof_bundle=replace(bundle, source_truth=bad_source))

    assert result.status == "REJECTED"
    assert "source_truth.causal_snapshot_id" in (result.failures[0].reason_detail or "")


def test_no_submit_accepts_forecast_horizon_profile_derived_to_match_calibration():
    """RELATIONSHIP: forecast.horizon_profile and calibration.horizon_profile must be a REAL,
    enforced equality — not skipped when forecast carries None.

    Live data has no ensemble_snapshots.horizon_profile column, so the forecast authority must
    DERIVE horizon_profile from the forecast cycle the same way the calibrator lookup does
    (derive_phase2_keys_from_ens_result: 00/12 -> 'full', else 'short'). This reproduces the live
    "calibration.horizon_profile != forecast.horizon_profile: 'full' != None" rejection by setting
    calibration to 'full' and requiring the cert to bind forecast's derived 'full' to it.
    """
    event = _event()
    decision_time = datetime(2026, 5, 25, 10, 3, tzinfo=timezone.utc)
    bundle = build_test_no_submit_proof_bundle(event, _receipt(event.event_id), decision_time=decision_time)
    # 00z cycle -> calibrator stratum 'full'; forecast authority must carry the same derived value.
    full_forecast = replace(bundle.forecast_authority, payload={**bundle.forecast_authority.payload, "horizon_profile": "full"})
    full_calibration = replace(bundle.calibration, payload={**bundle.calibration.payload, "horizon_profile": "full"})
    result = DecisionCompiler().compile_no_submit(
        event,
        decision_time=decision_time,
        proof_bundle=replace(bundle, forecast_authority=full_forecast, calibration=full_calibration),
    )

    assert result.status == "VERIFIED", (result.failures[0].reason_detail if result.failures else None)


def test_no_submit_rejects_horizon_profile_mismatch_when_both_present():
    """RELATIONSHIP guard: when forecast and calibration carry DIFFERENT horizon strata, the cert
    must still reject (the equality must be enforced, not silently skipped).
    """
    event = _event()
    decision_time = datetime(2026, 5, 25, 10, 3, tzinfo=timezone.utc)
    bundle = build_test_no_submit_proof_bundle(event, _receipt(event.event_id), decision_time=decision_time)
    full_forecast = replace(bundle.forecast_authority, payload={**bundle.forecast_authority.payload, "horizon_profile": "short"})
    full_calibration = replace(bundle.calibration, payload={**bundle.calibration.payload, "horizon_profile": "full"})
    result = DecisionCompiler().compile_no_submit(
        event,
        decision_time=decision_time,
        proof_bundle=replace(bundle, forecast_authority=full_forecast, calibration=full_calibration),
    )

    assert result.status == "REJECTED"
    assert "horizon_profile" in (result.failures[0].reason_detail or "")


def test_no_submit_rejects_quote_token_candidate_token_mismatch():
    event = _event()
    decision_time = datetime(2026, 5, 25, 10, 3, tzinfo=timezone.utc)
    bundle = build_test_no_submit_proof_bundle(event, _receipt(event.event_id), decision_time=decision_time)
    bad_quote = replace(bundle.quote_feasibility, payload={**bundle.quote_feasibility.payload, "token_id": "no-1"})
    result = DecisionCompiler().compile_no_submit(
        event,
        decision_time=decision_time,
        proof_bundle=replace(bundle, quote_feasibility=bad_quote),
    )

    assert result.status == "REJECTED"
    assert "candidate.selected_token_id" in (result.failures[0].reason_detail or "")


def test_no_submit_rejects_executable_snapshot_condition_mismatch():
    event = _event()
    decision_time = datetime(2026, 5, 25, 10, 3, tzinfo=timezone.utc)
    bundle = build_test_no_submit_proof_bundle(event, _receipt(event.event_id), decision_time=decision_time)
    bad_executable = replace(
        bundle.executable_snapshot,
        payload={**bundle.executable_snapshot.payload, "condition_id": "other-condition"},
    )
    result = DecisionCompiler().compile_no_submit(
        event,
        decision_time=decision_time,
        proof_bundle=replace(bundle, executable_snapshot=bad_executable),
    )

    assert result.status == "REJECTED"
    assert "candidate.condition_id" in (result.failures[0].reason_detail or "")


def test_no_submit_rejects_fdr_family_mismatch():
    event = _event()
    decision_time = datetime(2026, 5, 25, 10, 3, tzinfo=timezone.utc)
    bundle = build_test_no_submit_proof_bundle(event, _receipt(event.event_id), decision_time=decision_time)
    bad_fdr = replace(bundle.fdr, payload={**bundle.fdr.payload, "fdr_family_id": "other-family"})
    result = DecisionCompiler().compile_no_submit(
        event,
        decision_time=decision_time,
        proof_bundle=replace(bundle, fdr=bad_fdr),
    )

    assert result.status == "REJECTED"
    assert "fdr.fdr_family_id" in (result.failures[0].reason_detail or "")


def test_no_submit_rejects_kelly_cost_model_mismatch():
    event = _event()
    decision_time = datetime(2026, 5, 25, 10, 3, tzinfo=timezone.utc)
    bundle = build_test_no_submit_proof_bundle(event, _receipt(event.event_id), decision_time=decision_time)
    bad_kelly = replace(bundle.kelly_dry_run, payload={**bundle.kelly_dry_run.payload, "cost_basis_id": "other-cost"})
    result = DecisionCompiler().compile_no_submit(
        event,
        decision_time=decision_time,
        proof_bundle=replace(bundle, kelly_dry_run=bad_kelly),
    )

    assert result.status == "REJECTED"
    assert "kelly.cost_basis_id" in (result.failures[0].reason_detail or "")


def test_belief_certificate_links_to_calibration_model_key():
    event = _event()
    decision_time = datetime(2026, 5, 25, 10, 3, tzinfo=timezone.utc)
    result = DecisionCompiler().compile_no_submit(
        event,
        decision_time=decision_time,
        proof_bundle=build_test_no_submit_proof_bundle(event, _receipt(event.event_id), decision_time=decision_time),
    )

    belief = next(cert for cert in result.certificates if cert.certificate_type == claims.BELIEF)
    calibration = next(cert for cert in result.certificates if cert.certificate_type == claims.CALIBRATION)
    assert belief.payload["calibrator_model_key"] == calibration.payload["calibrator_model_key"]


def test_belief_certificate_rejects_bin_order_mismatch():
    event = _event()
    decision_time = datetime(2026, 5, 25, 10, 3, tzinfo=timezone.utc)
    bundle = build_test_no_submit_proof_bundle(event, _receipt(event.event_id), decision_time=decision_time)
    bad_belief = replace(bundle.belief, payload={**bundle.belief.payload, "bin_labels_hash": "wrong"})
    result = DecisionCompiler().compile_no_submit(
        event,
        decision_time=decision_time,
        proof_bundle=replace(bundle, belief=bad_belief),
    )

    assert result.status == "REJECTED"
    assert "belief.bin_labels_hash" in (result.failures[0].reason_detail or "")


def test_fdr_certificate_config_hash_matches_model_config():
    event = _event()
    decision_time = datetime(2026, 5, 25, 10, 3, tzinfo=timezone.utc)
    bundle = build_test_no_submit_proof_bundle(event, _receipt(event.event_id), decision_time=decision_time)
    bad_fdr = replace(bundle.fdr, payload={**bundle.fdr.payload, "edge_bootstrap_n": 999})
    result = DecisionCompiler().compile_no_submit(
        event,
        decision_time=decision_time,
        proof_bundle=replace(bundle, fdr=bad_fdr),
    )

    assert result.status == "REJECTED"
    assert "fdr.edge_bootstrap_n" in (result.failures[0].reason_detail or "")


def test_no_submit_rejects_source_truth_completeness_not_complete():
    event = _event()
    decision_time = datetime(2026, 5, 25, 10, 3, tzinfo=timezone.utc)
    bundle = build_test_no_submit_proof_bundle(event, _receipt(event.event_id), decision_time=decision_time)
    bad_source = replace(bundle.source_truth, payload={**bundle.source_truth.payload, "completeness_status": "PARTIAL"})
    result = DecisionCompiler().compile_no_submit(
        event,
        decision_time=decision_time,
        proof_bundle=replace(bundle, source_truth=bad_source),
    )

    assert result.status == "REJECTED"
    assert "source_truth.completeness_status" in (result.failures[0].reason_detail or "")


def test_no_submit_rejects_source_truth_snapshot_mismatch():
    event = _event()
    decision_time = datetime(2026, 5, 25, 10, 3, tzinfo=timezone.utc)
    bundle = build_test_no_submit_proof_bundle(event, _receipt(event.event_id), decision_time=decision_time)
    bad_source = replace(bundle.source_truth, payload={**bundle.source_truth.payload, "snapshot_id": "wrong"})
    result = DecisionCompiler().compile_no_submit(
        event,
        decision_time=decision_time,
        proof_bundle=replace(bundle, source_truth=bad_source),
    )

    assert result.status == "REJECTED"
    assert "source_truth.snapshot_id" in (result.failures[0].reason_detail or "")


def test_source_truth_certificate_not_hardcoded_match():
    event = _event()
    decision_time = datetime(2026, 5, 25, 10, 3, tzinfo=timezone.utc)
    bundle = build_test_no_submit_proof_bundle(event, _receipt(event.event_id), decision_time=decision_time)

    assert bundle.source_truth.payload["source_status"] == bundle.forecast_authority.payload["reader_status"]
    assert bundle.source_truth.payload["source_authority_id"] == "read_executable_forecast"
    assert bundle.source_truth.payload["derived_from_certificate_type"] == claims.FORECAST_AUTHORITY
    assert bundle.source_truth.payload["derived_from_snapshot_id"] == bundle.forecast_authority.payload["snapshot_id"]
    assert bundle.source_truth.payload["derived_from_reader_status"] == bundle.forecast_authority.payload["reader_status"]


def test_no_submit_rejects_source_truth_not_derived_from_forecast_parent():
    event = _event()
    decision_time = datetime(2026, 5, 25, 10, 3, tzinfo=timezone.utc)
    bundle = build_test_no_submit_proof_bundle(event, _receipt(event.event_id), decision_time=decision_time)
    bad_source = replace(
        bundle.source_truth,
        payload={**bundle.source_truth.payload, "derived_from_snapshot_id": "other-snapshot"},
    )
    result = DecisionCompiler().compile_no_submit(event, decision_time=decision_time, proof_bundle=replace(bundle, source_truth=bad_source))

    assert result.status == "REJECTED"
    assert "source_truth.derived_from_snapshot_id" in (result.failures[0].reason_detail or "")


def test_source_truth_and_forecast_status_vocabularies_identical():
    normalized = {normalize_forecast_reader_status(status) for status in FORECAST_READER_STATUS_ALIASES}

    assert normalized == {FORECAST_LIVE_ELIGIBLE_STATUS}


def test_reader_status_ok_normalizes_to_live_eligible():
    assert normalize_forecast_reader_status("OK") == FORECAST_LIVE_ELIGIBLE_STATUS


def test_reader_status_executable_forecast_ready_normalizes_to_live_eligible():
    assert normalize_forecast_reader_status("EXECUTABLE_FORECAST_READY") == FORECAST_LIVE_ELIGIBLE_STATUS


def test_no_submit_rejects_source_truth_status_unknown():
    event = _event()
    decision_time = datetime(2026, 5, 25, 10, 3, tzinfo=timezone.utc)
    bundle = build_test_no_submit_proof_bundle(event, _receipt(event.event_id), decision_time=decision_time)
    bad_source = replace(bundle.source_truth, payload={**bundle.source_truth.payload, "source_status": "UNKNOWN"})
    result = DecisionCompiler().compile_no_submit(
        event,
        decision_time=decision_time,
        proof_bundle=replace(bundle, source_truth=bad_source),
    )

    assert result.status == "REJECTED"
    assert "source_truth.source_status" in (result.failures[0].reason_detail or "")


def test_no_submit_rejects_source_truth_reason_code():
    event = _event()
    decision_time = datetime(2026, 5, 25, 10, 3, tzinfo=timezone.utc)
    bundle = build_test_no_submit_proof_bundle(event, _receipt(event.event_id), decision_time=decision_time)
    bad_source = replace(bundle.source_truth, payload={**bundle.source_truth.payload, "source_reason_code": "BLOCKED"})
    result = DecisionCompiler().compile_no_submit(
        event,
        decision_time=decision_time,
        proof_bundle=replace(bundle, source_truth=bad_source),
    )

    assert result.status == "REJECTED"
    assert "source_truth.source_reason_code" in (result.failures[0].reason_detail or "")


def test_no_submit_rejects_source_truth_source_run_mismatch():
    event = _event()
    decision_time = datetime(2026, 5, 25, 10, 3, tzinfo=timezone.utc)
    bundle = build_test_no_submit_proof_bundle(event, _receipt(event.event_id), decision_time=decision_time)
    bad_source = replace(bundle.source_truth, payload={**bundle.source_truth.payload, "source_run_id": "other-run"})
    result = DecisionCompiler().compile_no_submit(event, decision_time=decision_time, proof_bundle=replace(bundle, source_truth=bad_source))

    assert result.status == "REJECTED"
    assert "source_truth.source_run_id" in (result.failures[0].reason_detail or "")


def test_no_submit_rejects_source_truth_source_id_mismatch():
    event = _event()
    decision_time = datetime(2026, 5, 25, 10, 3, tzinfo=timezone.utc)
    bundle = build_test_no_submit_proof_bundle(event, _receipt(event.event_id), decision_time=decision_time)
    bad_source = replace(bundle.source_truth, payload={**bundle.source_truth.payload, "source_id": "other-source"})
    result = DecisionCompiler().compile_no_submit(event, decision_time=decision_time, proof_bundle=replace(bundle, source_truth=bad_source))

    assert result.status == "REJECTED"
    assert "source_truth.source_id" in (result.failures[0].reason_detail or "")


def test_no_submit_rejects_source_truth_payload_hash_mismatch():
    event = _event()
    decision_time = datetime(2026, 5, 25, 10, 3, tzinfo=timezone.utc)
    bundle = build_test_no_submit_proof_bundle(event, _receipt(event.event_id), decision_time=decision_time)
    bad_source = replace(bundle.source_truth, payload={**bundle.source_truth.payload, "payload_hash": "other-hash"})
    result = DecisionCompiler().compile_no_submit(event, decision_time=decision_time, proof_bundle=replace(bundle, source_truth=bad_source))

    assert result.status == "REJECTED"
    assert "source_truth.payload_hash" in (result.failures[0].reason_detail or "")


def test_no_submit_rejects_source_truth_event_source_mismatch():
    event = _event()
    decision_time = datetime(2026, 5, 25, 10, 3, tzinfo=timezone.utc)
    bundle = build_test_no_submit_proof_bundle(event, _receipt(event.event_id), decision_time=decision_time)
    bad_source = replace(bundle.source_truth, payload={**bundle.source_truth.payload, "event_source": "other-source"})
    result = DecisionCompiler().compile_no_submit(event, decision_time=decision_time, proof_bundle=replace(bundle, source_truth=bad_source))

    assert result.status == "REJECTED"
    assert "source_truth.event_source" in (result.failures[0].reason_detail or "")


def test_no_submit_rejects_source_truth_status_forecast_status_mismatch():
    event = _event()
    decision_time = datetime(2026, 5, 25, 10, 3, tzinfo=timezone.utc)
    bundle = build_test_no_submit_proof_bundle(event, _receipt(event.event_id), decision_time=decision_time)
    bad_source = replace(bundle.source_truth, payload={**bundle.source_truth.payload, "source_status": "BLOCKED"})
    result = DecisionCompiler().compile_no_submit(event, decision_time=decision_time, proof_bundle=replace(bundle, source_truth=bad_source))

    assert result.status == "REJECTED"
    assert "source_truth.source_status" in (result.failures[0].reason_detail or "")


def test_no_submit_rejects_forecast_reader_reason_code():
    event = _event()
    decision_time = datetime(2026, 5, 25, 10, 3, tzinfo=timezone.utc)
    bundle = build_test_no_submit_proof_bundle(event, _receipt(event.event_id), decision_time=decision_time)
    bad_forecast = replace(bundle.forecast_authority, payload={**bundle.forecast_authority.payload, "reader_reason_code": "BLOCKED"})
    result = DecisionCompiler().compile_no_submit(event, decision_time=decision_time, proof_bundle=replace(bundle, forecast_authority=bad_forecast))

    assert result.status == "REJECTED"
    assert "forecast.reader_reason_code" in (result.failures[0].reason_detail or "")


def test_no_submit_rejects_forecast_missing_coverage_readiness():
    event = _event()
    decision_time = datetime(2026, 5, 25, 10, 3, tzinfo=timezone.utc)
    bundle = build_test_no_submit_proof_bundle(event, _receipt(event.event_id), decision_time=decision_time)
    payload = {key: value for key, value in bundle.forecast_authority.payload.items() if key != "coverage_readiness_status"}
    bad_forecast = replace(bundle.forecast_authority, payload=payload)
    result = DecisionCompiler().compile_no_submit(event, decision_time=decision_time, proof_bundle=replace(bundle, forecast_authority=bad_forecast))

    assert result.status == "REJECTED"
    assert "coverage_readiness_status" in (result.failures[0].reason_detail or "")


def test_no_submit_rejects_coverage_not_live_eligible():
    event = _event()
    decision_time = datetime(2026, 5, 25, 10, 3, tzinfo=timezone.utc)
    bundle = build_test_no_submit_proof_bundle(event, _receipt(event.event_id), decision_time=decision_time)
    bad_forecast = replace(bundle.forecast_authority, payload={**bundle.forecast_authority.payload, "coverage_readiness_status": "BLOCKED"})
    result = DecisionCompiler().compile_no_submit(event, decision_time=decision_time, proof_bundle=replace(bundle, forecast_authority=bad_forecast))

    assert result.status == "REJECTED"
    assert "forecast.coverage_readiness_status" in (result.failures[0].reason_detail or "")


def test_no_submit_rejects_forecast_missing_required_steps():
    event = _event()
    decision_time = datetime(2026, 5, 25, 10, 3, tzinfo=timezone.utc)
    bundle = build_test_no_submit_proof_bundle(event, _receipt(event.event_id), decision_time=decision_time)
    bad_forecast = replace(bundle.forecast_authority, payload={**bundle.forecast_authority.payload, "required_steps": ()})
    result = DecisionCompiler().compile_no_submit(event, decision_time=decision_time, proof_bundle=replace(bundle, forecast_authority=bad_forecast))

    assert result.status == "REJECTED"
    assert "required_steps" in (result.failures[0].reason_detail or "")


def test_no_submit_rejects_missing_required_steps_in_certificate():
    event = _event()
    decision_time = datetime(2026, 5, 25, 10, 3, tzinfo=timezone.utc)
    bundle = build_test_no_submit_proof_bundle(event, _receipt(event.event_id), decision_time=decision_time)
    bad_forecast = replace(bundle.forecast_authority, payload={**bundle.forecast_authority.payload, "required_steps": (0, 3), "observed_steps": (0,)})
    result = DecisionCompiler().compile_no_submit(event, decision_time=decision_time, proof_bundle=replace(bundle, forecast_authority=bad_forecast))

    assert result.status == "REJECTED"
    assert "observed_steps" in (result.failures[0].reason_detail or "")


def test_no_submit_rejects_forecast_missing_member_counts():
    event = _event()
    decision_time = datetime(2026, 5, 25, 10, 3, tzinfo=timezone.utc)
    bundle = build_test_no_submit_proof_bundle(event, _receipt(event.event_id), decision_time=decision_time)
    bad_forecast = replace(
        bundle.forecast_authority,
        payload={**bundle.forecast_authority.payload, "expected_members": None},
    )
    result = DecisionCompiler().compile_no_submit(event, decision_time=decision_time, proof_bundle=replace(bundle, forecast_authority=bad_forecast))

    assert result.status == "REJECTED"
    assert "expected_members" in (result.failures[0].reason_detail or "")


def test_no_submit_rejects_observed_members_below_expected():
    event = _event()
    decision_time = datetime(2026, 5, 25, 10, 3, tzinfo=timezone.utc)
    bundle = build_test_no_submit_proof_bundle(event, _receipt(event.event_id), decision_time=decision_time)
    bad_forecast = replace(bundle.forecast_authority, payload={**bundle.forecast_authority.payload, "expected_members": 51, "observed_members": 40})
    result = DecisionCompiler().compile_no_submit(event, decision_time=decision_time, proof_bundle=replace(bundle, forecast_authority=bad_forecast))

    assert result.status == "REJECTED"
    assert "observed_members" in (result.failures[0].reason_detail or "")


def test_no_submit_rejects_forecast_empty_applied_validations():
    event = _event()
    decision_time = datetime(2026, 5, 25, 10, 3, tzinfo=timezone.utc)
    bundle = build_test_no_submit_proof_bundle(event, _receipt(event.event_id), decision_time=decision_time)
    bad_forecast = replace(bundle.forecast_authority, payload={**bundle.forecast_authority.payload, "applied_validations": ()})
    result = DecisionCompiler().compile_no_submit(event, decision_time=decision_time, proof_bundle=replace(bundle, forecast_authority=bad_forecast))

    assert result.status == "REJECTED"
    assert "applied_validations" in (result.failures[0].reason_detail or "")


def test_no_submit_rejects_synthesized_applied_validations():
    event = _event()
    decision_time = datetime(2026, 5, 25, 10, 3, tzinfo=timezone.utc)
    bundle = build_test_no_submit_proof_bundle(event, _receipt(event.event_id), decision_time=decision_time)
    bad_forecast = replace(
        bundle.forecast_authority,
        payload={**bundle.forecast_authority.payload, "applied_validations": ("source_run_completeness_status",)},
    )
    result = DecisionCompiler().compile_no_submit(event, decision_time=decision_time, proof_bundle=replace(bundle, forecast_authority=bad_forecast))

    assert result.status == "REJECTED"
    assert "applied_validations" in (result.failures[0].reason_detail or "")


def test_no_submit_rejects_missing_authority_verified_validation():
    event = _event()
    decision_time = datetime(2026, 5, 25, 10, 3, tzinfo=timezone.utc)
    bundle = build_test_no_submit_proof_bundle(event, _receipt(event.event_id), decision_time=decision_time)
    validations = tuple(item for item in bundle.forecast_authority.payload["applied_validations"] if item != "authority_verified")
    bad_forecast = replace(bundle.forecast_authority, payload={**bundle.forecast_authority.payload, "applied_validations": validations})
    result = DecisionCompiler().compile_no_submit(event, decision_time=decision_time, proof_bundle=replace(bundle, forecast_authority=bad_forecast))

    assert result.status == "REJECTED"
    assert "authority_verified" in (result.failures[0].reason_detail or "")


def test_no_submit_rejects_missing_causality_ok_validation():
    event = _event()
    decision_time = datetime(2026, 5, 25, 10, 3, tzinfo=timezone.utc)
    bundle = build_test_no_submit_proof_bundle(event, _receipt(event.event_id), decision_time=decision_time)
    validations = tuple(item for item in bundle.forecast_authority.payload["applied_validations"] if item != "causality_status_ok")
    bad_forecast = replace(bundle.forecast_authority, payload={**bundle.forecast_authority.payload, "applied_validations": validations})
    result = DecisionCompiler().compile_no_submit(event, decision_time=decision_time, proof_bundle=replace(bundle, forecast_authority=bad_forecast))

    assert result.status == "REJECTED"
    assert "causality_status_ok" in (result.failures[0].reason_detail or "")


def test_no_submit_rejects_missing_available_at_validation():
    event = _event()
    decision_time = datetime(2026, 5, 25, 10, 3, tzinfo=timezone.utc)
    bundle = build_test_no_submit_proof_bundle(event, _receipt(event.event_id), decision_time=decision_time)
    validations = tuple(item for item in bundle.forecast_authority.payload["applied_validations"] if item != "available_at_not_future")
    bad_forecast = replace(bundle.forecast_authority, payload={**bundle.forecast_authority.payload, "applied_validations": validations})
    result = DecisionCompiler().compile_no_submit(event, decision_time=decision_time, proof_bundle=replace(bundle, forecast_authority=bad_forecast))

    assert result.status == "REJECTED"
    assert "available_at_not_future" in (result.failures[0].reason_detail or "")


def test_members_metric_identity_mismatch_blocks():
    event = _event()
    decision_time = datetime(2026, 5, 25, 10, 3, tzinfo=timezone.utc)
    bundle = build_test_no_submit_proof_bundle(event, _receipt(event.event_id), decision_time=decision_time)
    bad_forecast = replace(
        bundle.forecast_authority,
        payload={**bundle.forecast_authority.payload, "members_extrema_metric_identity": "low"},
    )
    result = DecisionCompiler().compile_no_submit(event, decision_time=decision_time, proof_bundle=replace(bundle, forecast_authority=bad_forecast))

    assert result.status == "REJECTED"
    assert "members_extrema_metric_identity" in (result.failures[0].reason_detail or "")


def test_forecast_certificate_records_unit_authority():
    event = _event()
    decision_time = datetime(2026, 5, 25, 10, 3, tzinfo=timezone.utc)
    bundle = build_test_no_submit_proof_bundle(event, _receipt(event.event_id), decision_time=decision_time)
    result = DecisionCompiler().compile_no_submit(event, decision_time=decision_time, proof_bundle=bundle)

    assert result.status == "VERIFIED"
    forecast = next(cert for cert in result.certificates if cert.certificate_type == claims.FORECAST_AUTHORITY)
    assert forecast.payload["unit"] == "F"
    assert forecast.payload["unit_authority_source"] == "ensemble_snapshots.settlement_unit"


def test_belief_certificate_unit_matches_family_bins():
    event = _event()
    decision_time = datetime(2026, 5, 25, 10, 3, tzinfo=timezone.utc)
    bundle = build_test_no_submit_proof_bundle(event, _receipt(event.event_id), decision_time=decision_time)
    result = DecisionCompiler().compile_no_submit(event, decision_time=decision_time, proof_bundle=bundle)

    assert result.status == "VERIFIED"
    belief = next(cert for cert in result.certificates if cert.certificate_type == claims.BELIEF)
    assert belief.payload["unit"] == "F"


def test_no_submit_rejects_unit_bin_mismatch():
    event = _event()
    decision_time = datetime(2026, 5, 25, 10, 3, tzinfo=timezone.utc)
    bundle = build_test_no_submit_proof_bundle(event, _receipt(event.event_id), decision_time=decision_time)
    bad_family = replace(bundle.family_closure, payload={**bundle.family_closure.payload, "bin_units": ("C",)})
    result = DecisionCompiler().compile_no_submit(event, decision_time=decision_time, proof_bundle=replace(bundle, family_closure=bad_family))

    assert result.status == "REJECTED"
    assert "family.bin_unit" in (result.failures[0].reason_detail or "")


def test_no_submit_rejects_unapproved_calibration_authority():
    event = _event()
    decision_time = datetime(2026, 5, 25, 10, 3, tzinfo=timezone.utc)
    bundle = build_test_no_submit_proof_bundle(event, _receipt(event.event_id), decision_time=decision_time)
    bad_calibration = replace(bundle.calibration, payload={**bundle.calibration.payload, "authority": "EXPERIMENTAL"})
    result = DecisionCompiler().compile_no_submit(event, decision_time=decision_time, proof_bundle=replace(bundle, calibration=bad_calibration))

    assert result.status == "REJECTED"
    assert "calibration.authority" in (result.failures[0].reason_detail or "")


def test_no_submit_rejects_calibration_authority_test():
    event = _event()
    decision_time = datetime(2026, 5, 25, 10, 3, tzinfo=timezone.utc)
    bundle = build_test_no_submit_proof_bundle(event, _receipt(event.event_id), decision_time=decision_time)
    bad_calibration = replace(bundle.calibration, payload={**bundle.calibration.payload, "authority": "test"})
    result = DecisionCompiler().compile_no_submit(event, decision_time=decision_time, proof_bundle=replace(bundle, calibration=bad_calibration))

    assert result.status == "REJECTED"
    assert "calibration.authority" in (result.failures[0].reason_detail or "")


def test_no_submit_rejects_calibration_missing_authority():
    event = _event()
    decision_time = datetime(2026, 5, 25, 10, 3, tzinfo=timezone.utc)
    bundle = build_test_no_submit_proof_bundle(event, _receipt(event.event_id), decision_time=decision_time)
    payload = {key: value for key, value in bundle.calibration.payload.items() if key != "authority"}
    bad_calibration = replace(bundle.calibration, payload=payload)
    result = DecisionCompiler().compile_no_submit(event, decision_time=decision_time, proof_bundle=replace(bundle, calibration=bad_calibration))

    assert result.status == "REJECTED"
    assert "calibration.authority" in (result.failures[0].reason_detail or "")


def test_no_submit_accepts_identity_fallback_platt_bucket_authority():
    event = _event()
    decision_time = datetime(2026, 5, 25, 10, 3, tzinfo=timezone.utc)
    bundle = build_test_no_submit_proof_bundle(event, _receipt(event.event_id), decision_time=decision_time)
    identity_calibration = replace(
        bundle.calibration,
        payload={
            **bundle.calibration.payload,
            "authority": "IDENTITY_FALLBACK_NO_PLATT_BUCKET",
            "calibrator_model_key": "identity_fallback_no_platt_bucket_v1:high:cluster:2026-05-25:00:ecmwf_opendata:full",
            "model_hash": "identity-fallback-hash",
            "maturity_level": 4,
            "n_samples": 0,
        },
    )
    identity_belief = replace(
        bundle.belief,
        payload={
            **bundle.belief.payload,
            "calibrator_model_key": identity_calibration.payload["calibrator_model_key"],
            "calibrator_model_hash": identity_calibration.payload["model_hash"],
        },
    )
    identity_model_config = replace(
        bundle.model_config,
        payload={
            **bundle.model_config.payload,
            "calibrator_model_key": identity_calibration.payload["calibrator_model_key"],
            "calibrator_model_hash": identity_calibration.payload["model_hash"],
        },
    )

    result = DecisionCompiler().compile_no_submit(
        event,
        decision_time=decision_time,
        proof_bundle=replace(
            bundle,
            calibration=identity_calibration,
            belief=identity_belief,
            model_config=identity_model_config,
        ),
    )

    assert result.status == "VERIFIED", (result.failures[0].reason_detail if result.failures else None)


def test_no_submit_rejects_low_maturity_calibrator():
    event = _event()
    decision_time = datetime(2026, 5, 25, 10, 3, tzinfo=timezone.utc)
    bundle = build_test_no_submit_proof_bundle(event, _receipt(event.event_id), decision_time=decision_time)
    bad_calibration = replace(bundle.calibration, payload={**bundle.calibration.payload, "maturity_level": 4})
    result = DecisionCompiler().compile_no_submit(event, decision_time=decision_time, proof_bundle=replace(bundle, calibration=bad_calibration))

    assert result.status == "REJECTED"
    assert "maturity_level" in (result.failures[0].reason_detail or "")


def test_no_submit_rejects_calibration_missing_maturity():
    event = _event()
    decision_time = datetime(2026, 5, 25, 10, 3, tzinfo=timezone.utc)
    bundle = build_test_no_submit_proof_bundle(event, _receipt(event.event_id), decision_time=decision_time)
    payload = {key: value for key, value in bundle.calibration.payload.items() if key != "maturity_level"}
    bad_calibration = replace(bundle.calibration, payload=payload)
    result = DecisionCompiler().compile_no_submit(event, decision_time=decision_time, proof_bundle=replace(bundle, calibration=bad_calibration))

    assert result.status == "REJECTED"
    assert "maturity_level" in (result.failures[0].reason_detail or "")


def test_no_submit_rejects_calibration_training_cutoff_after_decision():
    event = _event()
    decision_time = datetime(2026, 5, 25, 10, 3, tzinfo=timezone.utc)
    bundle = build_test_no_submit_proof_bundle(event, _receipt(event.event_id), decision_time=decision_time)
    bad_calibration = replace(bundle.calibration, payload={**bundle.calibration.payload, "training_cutoff": "2026-05-25T10:04:00+00:00"})
    result = DecisionCompiler().compile_no_submit(event, decision_time=decision_time, proof_bundle=replace(bundle, calibration=bad_calibration))

    assert result.status == "REJECTED"
    assert "training_cutoff" in (result.failures[0].reason_detail or "")


def test_no_submit_rejects_calibration_missing_input_space():
    event = _event()
    decision_time = datetime(2026, 5, 25, 10, 3, tzinfo=timezone.utc)
    bundle = build_test_no_submit_proof_bundle(event, _receipt(event.event_id), decision_time=decision_time)
    payload = {key: value for key, value in bundle.calibration.payload.items() if key != "input_space"}
    bad_calibration = replace(bundle.calibration, payload=payload)
    result = DecisionCompiler().compile_no_submit(event, decision_time=decision_time, proof_bundle=replace(bundle, calibration=bad_calibration))

    assert result.status == "REJECTED"
    assert "calibration.input_space" in (result.failures[0].reason_detail or "")


def test_no_submit_rejects_model_config_missing_calibration_input_space():
    event = _event()
    decision_time = datetime(2026, 5, 25, 10, 3, tzinfo=timezone.utc)
    bundle = build_test_no_submit_proof_bundle(event, _receipt(event.event_id), decision_time=decision_time)
    payload = {key: value for key, value in bundle.model_config.payload.items() if key != "calibration_input_space"}
    bad_model_config = replace(bundle.model_config, payload=payload)
    result = DecisionCompiler().compile_no_submit(event, decision_time=decision_time, proof_bundle=replace(bundle, model_config=bad_model_config))

    assert result.status == "REJECTED"
    assert "model_config.calibration_input_space" in (result.failures[0].reason_detail or "")


def test_no_submit_rejects_calibration_input_space_mismatch():
    event = _event()
    decision_time = datetime(2026, 5, 25, 10, 3, tzinfo=timezone.utc)
    bundle = build_test_no_submit_proof_bundle(event, _receipt(event.event_id), decision_time=decision_time)
    bad_calibration = replace(bundle.calibration, payload={**bundle.calibration.payload, "input_space": "wrong"})
    result = DecisionCompiler().compile_no_submit(event, decision_time=decision_time, proof_bundle=replace(bundle, calibration=bad_calibration))

    assert result.status == "REJECTED"
    assert "calibration.input_space" in (result.failures[0].reason_detail or "")


def test_belief_p_cal_hash_is_full_distribution_hash_not_selected_q():
    event = _event()
    decision_time = datetime(2026, 5, 25, 10, 3, tzinfo=timezone.utc)
    bundle = build_test_no_submit_proof_bundle(event, _receipt(event.event_id), decision_time=decision_time)

    assert bundle.belief.payload["p_cal_hash"] == bundle.belief.payload["p_cal_vector_hash"]
    assert bundle.belief.payload["p_live_hash"] == bundle.belief.payload["p_live_vector_hash"]


def test_belief_model_hash_matches_calibration_model_hash():
    event = _event()
    decision_time = datetime(2026, 5, 25, 10, 3, tzinfo=timezone.utc)
    bundle = build_test_no_submit_proof_bundle(event, _receipt(event.event_id), decision_time=decision_time)
    bad_belief = replace(bundle.belief, payload={**bundle.belief.payload, "calibrator_model_hash": "other-hash"})
    result = DecisionCompiler().compile_no_submit(event, decision_time=decision_time, proof_bundle=replace(bundle, belief=bad_belief))

    assert result.status == "REJECTED"
    assert "belief.calibrator_model_hash" in (result.failures[0].reason_detail or "")


def test_model_config_model_key_matches_calibration():
    event = _event()
    decision_time = datetime(2026, 5, 25, 10, 3, tzinfo=timezone.utc)
    bundle = build_test_no_submit_proof_bundle(event, _receipt(event.event_id), decision_time=decision_time)
    bad_model_config = replace(bundle.model_config, payload={**bundle.model_config.payload, "calibrator_model_key": "other-model"})
    result = DecisionCompiler().compile_no_submit(event, decision_time=decision_time, proof_bundle=replace(bundle, model_config=bad_model_config))

    assert result.status == "REJECTED"
    assert "model_config.calibrator_model_key" in (result.failures[0].reason_detail or "")
