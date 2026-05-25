# Created: 2026-05-24
# Last reused/audited: 2026-05-24
# Authority basis: EDLI v1 implementation prompt §15 reports and observability contract.
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
import json

from src.decision_kernel.canonicalization import stable_hash
from src.decision_kernel.compiler import DecisionCompiler
from src.decision_kernel.ledger import DecisionCertificateLedger
from src.analysis.event_opportunity_report import build_event_opportunity_report
from src.events.no_submit_receipts import EdliNoSubmitReceiptLedger
from src.events.opportunity_event import ForecastSnapshotReadyPayload, make_opportunity_event
from src.events.reactor import EventSubmissionReceipt
from src.state.db import init_schema
from src.strategy.live_inference.no_trade_regret import NoTradeRegretEvent, NoTradeRegretLedger
from tests.decision_kernel.no_submit_fixtures import build_test_no_submit_proof_bundle


def test_event_opportunity_report_counts_regret_and_violations():
    conn = sqlite3.connect(":memory:")
    init_schema(conn)
    NoTradeRegretLedger(conn).insert_idempotent(
        NoTradeRegretEvent("event-1", "FDR", "FDR_REJECTED", "FDR_REJECTED")
    )
    report = build_event_opportunity_report(conn)
    assert report["blocked_by_stage"] == {"FDR": 1}
    assert report["violations"]["midpoint_cost_uses"] == 0
    assert report["violations"]["no_complement_cost_uses"] == 0


def test_report_available_at_violation_uses_decision_time_not_received_at():
    conn = sqlite3.connect(":memory:")
    init_schema(conn)
    event = _forecast_event()
    conn.execute(
        """
        INSERT INTO opportunity_events (
            event_id, event_type, entity_key, source, observed_at, available_at,
            received_at, causal_snapshot_id, payload_hash, idempotency_key,
            payload_json, schema_version, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
        """,
        (
            event.event_id,
            event.event_type,
            event.entity_key,
            event.source,
            event.observed_at,
            "2026-05-25T10:05:00+00:00",
            "2026-05-25T10:06:00+00:00",
            event.causal_snapshot_id,
            event.payload_hash,
            event.idempotency_key,
            event.payload_json,
            event.created_at,
        ),
    )
    NoTradeRegretLedger(conn).insert_idempotent(
        NoTradeRegretEvent(
            event.event_id,
            "FORECAST",
            "BLOCKED",
            "BLOCKED",
            decision_time="2026-05-25T10:04:00+00:00",
        )
    )

    report = build_event_opportunity_report(conn)

    assert report["violations"]["event_available_after_decision"] == 1
    assert report["violations"]["available_at_violations"] == 1


def test_report_counts_parent_filtration_violations():
    conn = sqlite3.connect(":memory:")
    init_schema(conn)
    _insert_decision_certificate(
        conn,
        certificate_id="cert-1",
        certificate_type="ForecastAuthorityCertificate",
        decision_time="2026-05-25T10:04:00+00:00",
        source_available_at="2026-05-25T10:05:00+00:00",
        payload={},
    )

    report = build_event_opportunity_report(conn)

    assert report["violations"]["parent_source_available_after_decision"] == 1


def test_report_does_not_hardcode_cost_violation_zero():
    conn = sqlite3.connect(":memory:")
    init_schema(conn)
    _insert_decision_certificate(
        conn,
        certificate_id="cost-1",
        certificate_type="CostModelCertificate",
        payload={"cost_source": "midpoint"},
    )
    _insert_decision_certificate(
        conn,
        certificate_id="quote-1",
        certificate_type="QuoteFeasibilityCertificate",
        payload={"quote_source_kind": "last_trade"},
    )

    report = build_event_opportunity_report(conn)

    assert report["violations"]["midpoint_cost_uses"] == 1
    assert report["violations"]["last_trade_cost_uses"] == 1


def test_report_detects_midpoint_cost_source():
    conn = sqlite3.connect(":memory:")
    init_schema(conn)
    _insert_decision_certificate(conn, certificate_id="cost-1", certificate_type="CostModelCertificate", payload={"cost_source": "midpoint"})

    report = build_event_opportunity_report(conn)

    assert report["violations"]["midpoint_cost_uses"] == 1


def test_report_detects_complement_cost_source():
    conn = sqlite3.connect(":memory:")
    init_schema(conn)
    _insert_decision_certificate(conn, certificate_id="cost-1", certificate_type="CostModelCertificate", payload={"cost_source": "complement_price"})

    report = build_event_opportunity_report(conn)

    assert report["violations"]["no_complement_cost_uses"] == 1


def test_report_detects_last_trade_cost_source():
    conn = sqlite3.connect(":memory:")
    init_schema(conn)
    _insert_decision_certificate(conn, certificate_id="quote-1", certificate_type="QuoteFeasibilityCertificate", payload={"quote_source_kind": "last_trade"})

    report = build_event_opportunity_report(conn)

    assert report["violations"]["last_trade_cost_uses"] == 1


def test_report_flags_missing_cost_source_for_cost_model_certificate():
    conn = sqlite3.connect(":memory:")
    init_schema(conn)
    _insert_decision_certificate(conn, certificate_id="cost-1", certificate_type="CostModelCertificate", payload={"execution_price_type": "ExecutionPrice"})

    report = build_event_opportunity_report(conn)

    assert report["violations"]["cost_source_missing"] == 1


def test_event_opportunity_report_counts_accepted_no_submit_receipts():
    conn = sqlite3.connect(":memory:")
    init_schema(conn)
    event = _forecast_event()
    decision_time = datetime(2026, 5, 25, 10, 3, tzinfo=timezone.utc)
    receipt = _receipt(event.event_id)
    compile_result = DecisionCompiler().compile_no_submit(
        event,
        decision_time=decision_time,
        proof_bundle=build_test_no_submit_proof_bundle(event, receipt, decision_time=decision_time),
    )
    assert compile_result.status == "VERIFIED"
    DecisionCertificateLedger(conn).persist_all(compile_result.certificates)
    EdliNoSubmitReceiptLedger(conn).insert_idempotent(receipt, decision_time=decision_time)

    report = build_event_opportunity_report(conn)

    assert report["accepted_no_submit_receipts"] == 1
    assert report["accepted_no_submit_receipt_rows"] == 1
    assert report["accepted_no_submit_distinct_decisions"] == 1
    assert report["blocked_by_stage"] == {}


def test_report_does_not_count_receipt_certificate_projection_hash_mismatch():
    conn = sqlite3.connect(":memory:")
    init_schema(conn)
    event = _forecast_event()
    receipt = _receipt(event.event_id)
    EdliNoSubmitReceiptLedger(conn).insert_idempotent(
        receipt,
        decision_time=datetime(2026, 5, 25, 10, 3, tzinfo=timezone.utc),
    )
    payload = _accepted_no_submit_payload(event.event_id, receipt)
    payload["projection_hash"] = "different-projection-hash"
    _insert_decision_certificate(
        conn,
        certificate_id="no-submit-1",
        certificate_type="NoSubmitDecisionCertificate",
        semantic_key=f"no_submit:{event.event_id}:{receipt.final_intent_id}",
        payload=payload,
    )

    report = build_event_opportunity_report(conn)

    assert report["accepted_no_submit_receipts"] == 0


def test_report_requires_receipt_projection_hash_match():
    conn = sqlite3.connect(":memory:")
    init_schema(conn)
    event = _forecast_event()
    receipt = _receipt(event.event_id)
    EdliNoSubmitReceiptLedger(conn).insert_idempotent(
        receipt,
        decision_time=datetime(2026, 5, 25, 10, 3, tzinfo=timezone.utc),
    )
    _insert_decision_certificate(
        conn,
        certificate_id="no-submit-1",
        certificate_type="NoSubmitDecisionCertificate",
        semantic_key=f"no_submit:{event.event_id}:{receipt.final_intent_id}",
        payload=_accepted_no_submit_payload(event.event_id, receipt),
    )

    report = build_event_opportunity_report(conn)

    assert report["accepted_no_submit_receipts"] == 1


def test_report_counts_distinct_accepted_no_submit_decisions():
    conn = sqlite3.connect(":memory:")
    init_schema(conn)
    event = _forecast_event()
    receipt = _receipt(event.event_id)
    EdliNoSubmitReceiptLedger(conn).insert_idempotent(
        receipt,
        decision_time=datetime(2026, 5, 25, 10, 3, tzinfo=timezone.utc),
    )
    semantic_key = f"no_submit:{event.event_id}:{receipt.final_intent_id}"
    payload = _accepted_no_submit_payload(event.event_id, receipt)
    _insert_decision_certificate(
        conn,
        certificate_id="no-submit-1",
        certificate_type="NoSubmitDecisionCertificate",
        semantic_key=semantic_key,
        payload=payload,
    )
    _insert_decision_certificate(
        conn,
        certificate_id="no-submit-2",
        certificate_type="NoSubmitDecisionCertificate",
        decision_time="2026-05-25T10:05:00+00:00",
        semantic_key=semantic_key,
        payload=payload,
    )

    report = build_event_opportunity_report(conn)

    assert report["accepted_no_submit_receipt_rows"] == 2
    assert report["accepted_no_submit_distinct_decisions"] == 1
    assert report["accepted_no_submit_receipts"] == 1


def test_report_does_not_count_receipt_certificate_final_intent_mismatch():
    conn = sqlite3.connect(":memory:")
    init_schema(conn)
    event = _forecast_event()
    receipt = _receipt(event.event_id)
    EdliNoSubmitReceiptLedger(conn).insert_idempotent(
        receipt,
        decision_time=datetime(2026, 5, 25, 10, 3, tzinfo=timezone.utc),
    )
    _insert_decision_certificate(
        conn,
        certificate_id="no-submit-1",
        certificate_type="NoSubmitDecisionCertificate",
        semantic_key=f"no_submit:{event.event_id}:{receipt.final_intent_id}",
        payload={
            "event_id": event.event_id,
            "final_intent_id": "different-intent",
            "side_effect_status": "NO_SUBMIT",
            "executable_snapshot_id": receipt.executable_snapshot_id,
            "proof_accepted": True,
            "submitted": False,
        },
    )

    report = build_event_opportunity_report(conn)

    assert report["accepted_no_submit_receipts"] == 0


def test_report_does_not_count_receipt_certificate_executable_snapshot_mismatch():
    conn = sqlite3.connect(":memory:")
    init_schema(conn)
    event = _forecast_event()
    receipt = _receipt(event.event_id)
    EdliNoSubmitReceiptLedger(conn).insert_idempotent(
        receipt,
        decision_time=datetime(2026, 5, 25, 10, 3, tzinfo=timezone.utc),
    )
    _insert_decision_certificate(
        conn,
        certificate_id="no-submit-1",
        certificate_type="NoSubmitDecisionCertificate",
        semantic_key=f"no_submit:{event.event_id}:{receipt.final_intent_id}",
        payload={
            "event_id": event.event_id,
            "final_intent_id": receipt.final_intent_id,
            "side_effect_status": "NO_SUBMIT",
            "executable_snapshot_id": "different-snapshot",
            "proof_accepted": True,
            "submitted": False,
        },
    )

    report = build_event_opportunity_report(conn)

    assert report["accepted_no_submit_receipts"] == 0


def test_report_does_not_count_receipt_when_certificate_payload_is_not_proof_accepted():
    conn = sqlite3.connect(":memory:")
    init_schema(conn)
    event = _forecast_event()
    receipt = _receipt(event.event_id)
    EdliNoSubmitReceiptLedger(conn).insert_idempotent(
        receipt,
        decision_time=datetime(2026, 5, 25, 10, 3, tzinfo=timezone.utc),
    )
    _insert_decision_certificate(
        conn,
        certificate_id="no-submit-1",
        certificate_type="NoSubmitDecisionCertificate",
        semantic_key=f"no_submit:{event.event_id}:{receipt.final_intent_id}",
        payload={
            "event_id": event.event_id,
            "final_intent_id": receipt.final_intent_id,
            "side_effect_status": "NO_SUBMIT",
            "executable_snapshot_id": receipt.executable_snapshot_id,
            "proof_accepted": False,
            "submitted": False,
        },
    )

    report = build_event_opportunity_report(conn)

    assert report["accepted_no_submit_receipts"] == 0


def test_certificate_created_at_can_be_after_header_persisted_at_for_generated_certificates_only():
    conn = sqlite3.connect(":memory:")
    init_schema(conn)
    event = _forecast_event()
    decision_time = datetime(2026, 5, 25, 10, 3, tzinfo=timezone.utc)
    receipt = _receipt(event.event_id)
    compile_result = DecisionCompiler().compile_no_submit(
        event,
        decision_time=decision_time,
        proof_bundle=build_test_no_submit_proof_bundle(event, receipt, decision_time=decision_time),
    )
    assert compile_result.status == "VERIFIED"
    DecisionCertificateLedger(conn).persist_all(compile_result.certificates)

    row = conn.execute(
        """
        SELECT persisted_at, created_at, payload_json
        FROM decision_certificates
        WHERE certificate_type = 'NoSubmitDecisionCertificate'
        """
    ).fetchone()
    payload = json.loads(row[2])
    assert row[0] == "2026-05-25T10:03:00+00:00"
    assert row[1]
    assert payload["generated_at_decision_time"] is True
    assert payload["db_created_at_may_follow_header_persisted_at"] is True


def test_report_distinguishes_header_persisted_at_from_db_created_at():
    conn = sqlite3.connect(":memory:")
    init_schema(conn)
    event = _forecast_event()
    decision_time = datetime(2026, 5, 25, 10, 3, tzinfo=timezone.utc)
    receipt = _receipt(event.event_id)
    compile_result = DecisionCompiler().compile_no_submit(
        event,
        decision_time=decision_time,
        proof_bundle=build_test_no_submit_proof_bundle(event, receipt, decision_time=decision_time),
    )
    assert compile_result.status == "VERIFIED"
    DecisionCertificateLedger(conn).persist_all(compile_result.certificates)

    report = build_event_opportunity_report(conn)

    assert report["certificate_time_semantics"]["generated_no_submit_decisions"] == 1
    assert report["certificate_time_semantics"]["db_created_after_header_persisted_at"] == 1


def test_report_event_time_violation_counts_compile_failure_only_event():
    conn = sqlite3.connect(":memory:")
    init_schema(conn)
    event = _forecast_event()
    _insert_opportunity_event(
        conn,
        event,
        available_at="2026-05-25T10:05:00+00:00",
        received_at="2026-05-25T10:06:00+00:00",
    )
    conn.execute(
        """
        INSERT INTO decision_compile_failures (
            failure_id, event_id, decision_time, mode, claim_type, stage,
            reason_code, created_at
        ) VALUES (
            'failure-1', ?, '2026-05-25T10:04:00+00:00', 'NO_SUBMIT',
            'NoSubmitDecision', 'FORECAST', 'BLOCKED',
            '2026-05-25T10:04:00+00:00'
        )
        """,
        (event.event_id,),
    )

    report = build_event_opportunity_report(conn)

    assert report["violations"]["event_available_after_decision"] == 1


def test_report_event_time_violation_excludes_unfinalized_certificate_only_event():
    conn = sqlite3.connect(":memory:")
    init_schema(conn)
    event = _forecast_event()
    _insert_opportunity_event(
        conn,
        event,
        available_at="2026-05-25T10:05:00+00:00",
        received_at="2026-05-25T10:06:00+00:00",
    )
    _insert_decision_certificate(
        conn,
        certificate_id="no-submit-1",
        certificate_type="NoSubmitDecisionCertificate",
        decision_time="2026-05-25T10:04:00+00:00",
        payload={"event_id": event.event_id},
    )

    report = build_event_opportunity_report(conn)

    assert report["violations"]["event_available_after_decision"] == 0


def test_report_event_time_violation_counts_finalized_certificate_event():
    conn = sqlite3.connect(":memory:")
    init_schema(conn)
    event = _forecast_event()
    _insert_opportunity_event(
        conn,
        event,
        available_at="2026-05-25T10:05:00+00:00",
        received_at="2026-05-25T10:06:00+00:00",
    )
    receipt = _receipt(event.event_id)
    EdliNoSubmitReceiptLedger(conn).insert_idempotent(receipt, decision_time=datetime(2026, 5, 25, 10, 4, tzinfo=timezone.utc))
    _insert_decision_certificate(
        conn,
        certificate_id="no-submit-1",
        certificate_type="NoSubmitDecisionCertificate",
        decision_time="2026-05-25T10:04:00+00:00",
        semantic_key=f"no_submit:{event.event_id}:{receipt.final_intent_id}",
        payload={"event_id": event.event_id, "final_intent_id": receipt.final_intent_id},
    )

    report = build_event_opportunity_report(conn)

    assert report["violations"]["event_available_after_decision"] == 1


def test_report_decision_time_union_has_no_surface_gap():
    conn = sqlite3.connect(":memory:")
    init_schema(conn)
    event = _forecast_event()
    _insert_opportunity_event(
        conn,
        event,
        available_at="2026-05-25T10:05:00+00:00",
        received_at="2026-05-25T10:06:00+00:00",
    )
    NoTradeRegretLedger(conn).insert_idempotent(
        NoTradeRegretEvent(event.event_id, "FORECAST", "BLOCKED", "BLOCKED", decision_time="2026-05-25T10:04:00+00:00")
    )
    conn.execute(
        """
        INSERT INTO decision_compile_failures (
            failure_id, event_id, decision_time, mode, claim_type, stage,
            reason_code, created_at
        ) VALUES (
            'failure-1', ?, '2026-05-25T10:04:00+00:00', 'NO_SUBMIT',
            'NoSubmitDecision', 'FORECAST', 'BLOCKED',
            '2026-05-25T10:04:00+00:00'
        )
        """,
        (event.event_id,),
    )
    receipt = _receipt(event.event_id)
    EdliNoSubmitReceiptLedger(conn).insert_idempotent(receipt, decision_time=datetime(2026, 5, 25, 10, 4, tzinfo=timezone.utc))
    _insert_decision_certificate(
        conn,
        certificate_id="no-submit-1",
        certificate_type="NoSubmitDecisionCertificate",
        decision_time="2026-05-25T10:04:00+00:00",
        semantic_key=f"no_submit:{event.event_id}:{receipt.final_intent_id}",
        payload={"event_id": event.event_id, "final_intent_id": receipt.final_intent_id},
    )

    report = build_event_opportunity_report(conn)

    assert report["violations"]["event_available_after_decision"] == 1
    assert report["violations"]["event_available_after_decision_rows"] == 4


def test_report_excludes_superseded_certificate_graphs():
    conn = sqlite3.connect(":memory:")
    init_schema(conn)
    event = _forecast_event()
    _insert_opportunity_event(
        conn,
        event,
        available_at="2026-05-25T10:05:00+00:00",
        received_at="2026-05-25T10:06:00+00:00",
    )
    receipt = _receipt(event.event_id)
    EdliNoSubmitReceiptLedger(conn).insert_idempotent(receipt, decision_time=datetime(2026, 5, 25, 10, 4, tzinfo=timezone.utc))
    _insert_decision_certificate(
        conn,
        certificate_id="no-submit-old",
        certificate_type="NoSubmitDecisionCertificate",
        decision_time="2026-05-25T10:04:00+00:00",
        semantic_key=f"no_submit:{event.event_id}:{receipt.final_intent_id}",
        payload={"event_id": event.event_id, "final_intent_id": receipt.final_intent_id},
    )
    conn.execute(
        """
        INSERT INTO decision_certificate_supersessions (
            supersession_id, old_certificate_hash, new_certificate_hash, reason, created_at
        ) VALUES ('sup-1', 'hash:no-submit-old', 'hash:no-submit-new', 'test', '2026-05-25T10:04:00+00:00')
        """
    )

    report = build_event_opportunity_report(conn)

    assert report["violations"]["event_available_after_decision"] == 0


def _forecast_event():
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


def _insert_opportunity_event(
    conn: sqlite3.Connection,
    event,
    *,
    available_at: str,
    received_at: str,
) -> None:
    conn.execute(
        """
        INSERT INTO opportunity_events (
            event_id, event_type, entity_key, source, observed_at, available_at,
            received_at, causal_snapshot_id, payload_hash, idempotency_key,
            payload_json, schema_version, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
        """,
        (
            event.event_id,
            event.event_type,
            event.entity_key,
            event.source,
            event.observed_at,
            available_at,
            received_at,
            event.causal_snapshot_id,
            event.payload_hash,
            event.idempotency_key,
            event.payload_json,
            event.created_at,
        ),
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


def _accepted_no_submit_payload(event_id: str, receipt) -> dict[str, object]:
    payload = {
        "event_id": event_id,
        "final_intent_id": receipt.final_intent_id,
        "side_effect_status": receipt.side_effect_status,
        "proof_accepted": True,
        "submitted": False,
        "executable_snapshot_id": receipt.executable_snapshot_id,
    }
    payload["projection_hash"] = stable_hash(payload)
    return payload


def _insert_decision_certificate(
    conn: sqlite3.Connection,
    *,
    certificate_id: str,
    certificate_type: str,
    decision_time: str = "2026-05-25T10:04:00+00:00",
    source_available_at: str = "2026-05-25T10:03:00+00:00",
    agent_received_at: str = "2026-05-25T10:03:00+00:00",
    persisted_at: str = "2026-05-25T10:03:00+00:00",
    semantic_key: str | None = None,
    payload: dict[str, object],
) -> None:
    conn.execute(
        """
        INSERT INTO decision_certificates (
            certificate_id, certificate_type, schema_version, canonicalization_version,
            semantic_key, claim_type, mode, decision_time, source_available_at,
            agent_received_at, persisted_at, authority_id, authority_version,
            algorithm_id, algorithm_version, payload_json, payload_hash,
            certificate_hash, verifier_status, created_at
        ) VALUES (?, ?, 1, 'test', ?, 'test', 'NO_SUBMIT', ?, ?, ?, ?,
            'test', 'test', 'test', 'test', ?, 'payload-hash', ?, 'VERIFIED',
            '2026-05-25T10:04:00+00:00')
        """,
        (
            certificate_id,
            certificate_type,
            semantic_key or f"test:{certificate_id}",
            decision_time,
            source_available_at,
            agent_received_at,
            persisted_at,
            json.dumps(payload, sort_keys=True),
            f"hash:{certificate_id}",
        ),
    )
