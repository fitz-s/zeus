# Created: 2026-05-25
# Last reused or audited: 2026-05-25
# Authority basis: docs/operations/edli_v1/EDLI_REDEMPTION_FINAL_PACKAGE_SPEC.md §14 full-live increment.
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from datetime import datetime, timezone


def test_live_canary_runtime_remains_disabled_until_executor_cut():
    settings = json.loads(Path("config/settings.json").read_text())
    edli = settings["edli_v1"]

    assert edli["reactor_mode"] == "live_no_submit"
    assert edli["real_order_submit_enabled"] is False
    assert edli["day0_extreme_trigger_enabled"] is False
    assert edli["market_channel_ingestor_enabled"] is False
    assert "live_canary_enabled" not in edli


def test_live_canary_groundwork_has_live_cap_schema_and_verifiers():
    from src.decision_kernel import claims
    from src.decision_kernel.verifier import verify_actionable_trade, verify_execution_command
    from src.events.live_cap import LiveCapLedger

    assert claims.LIVE_CAP == "LiveCapCertificate"
    assert claims.FINAL_INTENT == "FinalIntentCertificate"
    assert claims.EXECUTOR_EXPRESSIBILITY == "ExecutorExpressibilityCertificate"
    assert callable(verify_actionable_trade)
    assert callable(verify_execution_command)
    assert LiveCapLedger.__name__ == "LiveCapLedger"


def test_live_adapter_builds_actionable_final_intent_command_and_submit_disabled_receipt(monkeypatch):
    from src.engine import event_reactor_adapter as adapter
    from src.events.opportunity_event import ForecastSnapshotReadyPayload, make_opportunity_event
    from src.events.reactor import EventSubmissionReceipt
    from src.riskguard.risk_level import RiskLevel

    event = _forecast_event()
    marker_bundle = ("actionable", "final_intent", "expressibility", "command", "receipt")

    monkeypatch.setattr(
        adapter,
        "build_event_bound_no_submit_receipt",
        lambda *_args, **_kwargs: EventSubmissionReceipt(
            submitted=False,
            proof_accepted=True,
            event_id=event.event_id,
            causal_snapshot_id=event.causal_snapshot_id,
            condition_id="condition-1",
            token_id="yes-1",
            executable_snapshot_id="exec-1",
            family_id="family-1",
            candidate_id="candidate-1",
            direction="buy_yes",
            trade_score_positive=True,
            fdr_pass=True,
            fdr_family_id="family-1",
            fdr_hypothesis_count=2,
            kelly_pass=True,
            kelly_execution_price_type="ExecutionPrice",
            kelly_price_fee_deducted=True,
            kelly_size_usd=3.0,
            kelly_cost_basis_id="cost-1",
            kelly_decision_id="kelly-1",
            risk_decision_id="risk-1",
            final_intent_id="intent-1",
            decision_proof_bundle=object(),
        ),
    )
    monkeypatch.setattr(
        adapter,
        "_build_submit_disabled_live_certificates",
        lambda **_kwargs: marker_bundle,
    )

    submit = adapter.event_bound_live_adapter_from_trade_conn(
        sqlite3.connect(":memory:"),
        get_current_level=lambda: RiskLevel.GREEN,
        real_order_submit_enabled=False,
    )

    receipt = submit(event, datetime(2026, 5, 24, 18, 10, tzinfo=timezone.utc))

    assert receipt.side_effect_status == "SUBMIT_DISABLED"
    assert receipt.submitted is False
    assert receipt.proof_accepted is True
    assert receipt.decision_proof_bundle == marker_bundle


def test_live_adapter_does_not_call_executor_when_real_submit_disabled(monkeypatch):
    from src.engine import event_reactor_adapter as adapter
    from src.events.reactor import EventSubmissionReceipt
    from src.riskguard.risk_level import RiskLevel

    event = _forecast_event()
    called = {"builder": False}

    monkeypatch.setattr(
        adapter,
        "build_event_bound_no_submit_receipt",
        lambda *_args, **_kwargs: EventSubmissionReceipt(
            submitted=False,
            proof_accepted=True,
            event_id=event.event_id,
            causal_snapshot_id=event.causal_snapshot_id,
            trade_score_positive=True,
            fdr_pass=True,
            fdr_family_id="family-1",
            fdr_hypothesis_count=1,
            kelly_pass=True,
            kelly_execution_price_type="ExecutionPrice",
            kelly_price_fee_deducted=True,
            kelly_size_usd=1.0,
            kelly_cost_basis_id="cost-1",
            final_intent_id="intent-1",
            decision_proof_bundle=object(),
        ),
    )

    def _cert_builder(**_kwargs):
        called["builder"] = True
        return ("receipt-cert",)

    monkeypatch.setattr(adapter, "_build_submit_disabled_live_certificates", _cert_builder)

    submit = adapter.event_bound_live_adapter_from_trade_conn(
        sqlite3.connect(":memory:"),
        get_current_level=lambda: RiskLevel.GREEN,
        real_order_submit_enabled=False,
    )
    receipt = submit(event, datetime(2026, 5, 24, 18, 10, tzinfo=timezone.utc))

    assert called["builder"] is True
    assert receipt.submitted is False
    assert receipt.side_effect_status == "SUBMIT_DISABLED"


def test_live_adapter_returns_submit_disabled_terminal_receipt(monkeypatch):
    from src.engine import event_reactor_adapter as adapter
    from src.events.reactor import EventSubmissionReceipt
    from src.riskguard.risk_level import RiskLevel

    event = _forecast_event()
    monkeypatch.setattr(
        adapter,
        "build_event_bound_no_submit_receipt",
        lambda *_args, **_kwargs: EventSubmissionReceipt(
            submitted=False,
            proof_accepted=True,
            event_id=event.event_id,
            causal_snapshot_id=event.causal_snapshot_id,
            trade_score_positive=True,
            fdr_pass=True,
            fdr_family_id="family-1",
            fdr_hypothesis_count=1,
            kelly_pass=True,
            kelly_execution_price_type="ExecutionPrice",
            kelly_price_fee_deducted=True,
            kelly_size_usd=1.0,
            kelly_cost_basis_id="cost-1",
            final_intent_id="intent-1",
            decision_proof_bundle=object(),
        ),
    )
    monkeypatch.setattr(adapter, "_build_submit_disabled_live_certificates", lambda **_kwargs: ("receipt-cert",))

    submit = adapter.event_bound_live_adapter_from_trade_conn(
        sqlite3.connect(":memory:"),
        get_current_level=lambda: RiskLevel.GREEN,
    )

    assert submit(event, datetime(2026, 5, 24, 18, 10, tzinfo=timezone.utc)).side_effect_status == "SUBMIT_DISABLED"


def test_live_adapter_rejects_if_actionable_certificate_fails(monkeypatch):
    from src.engine import event_reactor_adapter as adapter
    from src.events.reactor import EventSubmissionReceipt
    from src.riskguard.risk_level import RiskLevel

    event = _forecast_event()
    monkeypatch.setattr(
        adapter,
        "build_event_bound_no_submit_receipt",
        lambda *_args, **_kwargs: EventSubmissionReceipt(
            submitted=False,
            proof_accepted=True,
            event_id=event.event_id,
            causal_snapshot_id=event.causal_snapshot_id,
            decision_proof_bundle=object(),
        ),
    )
    monkeypatch.setattr(
        adapter,
        "_build_submit_disabled_live_certificates",
        lambda **_kwargs: (_ for _ in ()).throw(ValueError("ACTIONABLE_CERTIFICATE_REJECTED")),
    )

    submit = adapter.event_bound_live_adapter_from_trade_conn(
        sqlite3.connect(":memory:"),
        get_current_level=lambda: RiskLevel.GREEN,
    )
    receipt = submit(event, datetime(2026, 5, 24, 18, 10, tzinfo=timezone.utc))

    assert receipt.proof_accepted is False
    assert "ACTIONABLE_CERTIFICATE_REJECTED" in receipt.reason


def _forecast_event():
    from src.events.opportunity_event import ForecastSnapshotReadyPayload, make_opportunity_event

    payload = ForecastSnapshotReadyPayload(
        city="Chicago",
        target_date="2026-05-24",
        metric="high",
        source_id="opendata",
        source_run_id="run-1",
        cycle="00",
        track="live",
        snapshot_id="snap-1",
        snapshot_hash="hash-1",
        captured_at="2026-05-24T18:00:00+00:00",
        available_at="2026-05-24T18:01:00+00:00",
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
        entity_key="Chicago|2026-05-24|high|live-canary-test",
        source="forecast_live",
        observed_at="2026-05-24T18:00:00+00:00",
        available_at="2026-05-24T18:01:00+00:00",
        received_at="2026-05-24T18:02:00+00:00",
        payload=payload,
        causal_snapshot_id="snap-1",
    )
