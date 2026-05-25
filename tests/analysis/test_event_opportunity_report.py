# Created: 2026-05-24
# Last reused/audited: 2026-05-24
# Authority basis: EDLI v1 implementation prompt §15 reports and observability contract.
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

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
    assert report["blocked_by_stage"] == {}


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
