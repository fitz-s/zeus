# Created: 2026-05-25
# Last reused/audited: 2026-05-25
# Authority basis: docs/operations/edli_v1/EDLI_REDEMPTION_FINAL_PACKAGE_SPEC.md §14 full-live increment.
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from datetime import datetime, timezone

import pytest


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


def test_live_cap_certificate_is_backed_by_usage_row():
    from src.engine import event_reactor_adapter as adapter
    from src.events.reactor import EventSubmissionReceipt

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    event = _forecast_event()
    cert = adapter._build_live_cap_certificate_from_ledger(
        event=event,
        receipt=EventSubmissionReceipt(
            submitted=False,
            proof_accepted=True,
            event_id=event.event_id,
            causal_snapshot_id=event.causal_snapshot_id,
            c_fee_adjusted=0.4,
            kelly_size_usd=3.0,
            final_intent_id="intent-1",
        ),
        decision_time=datetime(2026, 5, 24, 18, 10, tzinfo=timezone.utc),
        max_notional_usd=5.0,
        live_cap_conn=conn,
    )

    row = conn.execute(
        """
        SELECT event_id, cap_scope, reserved_notional_usd, reservation_status, final_intent_id
        FROM edli_live_cap_usage
        WHERE usage_id = ?
        """,
        (cert.payload["usage_id"],),
    ).fetchone()

    assert row is not None
    assert row["event_id"] == event.event_id
    assert row["cap_scope"] == "tiny_live_canary"
    assert row["reservation_status"] == "RESERVED"
    assert row["reserved_notional_usd"] == cert.payload["reserved_notional_usd"]
    assert row["final_intent_id"] == "intent-1"


def test_submit_disabled_live_bridge_releases_live_cap_row(monkeypatch):
    from src.engine import event_reactor_adapter as adapter
    from src.riskguard.risk_level import RiskLevel

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    event = _forecast_event()
    monkeypatch.setattr(adapter, "build_event_bound_no_submit_receipt", lambda *_args, **_kwargs: _accepted_receipt(event))

    def _command_bundle_with_real_cap(**kwargs):
        live_cap = adapter._build_live_cap_certificate_from_ledger(
            event=kwargs["event"],
            receipt=kwargs["receipt"],
            decision_time=kwargs["decision_time"],
            max_notional_usd=kwargs["tiny_live_max_notional_usd"],
            live_cap_conn=kwargs["live_cap_conn"],
        )
        actionable, final_intent, expressibility, _old_live_cap, command = _command_cert_bundle()
        return (actionable, live_cap, final_intent, expressibility, command)

    monkeypatch.setattr(adapter, "_build_live_execution_command_certificates", _command_bundle_with_real_cap)

    submit = adapter.event_bound_live_adapter_from_trade_conn(
        conn,
        live_cap_conn=conn,
        get_current_level=lambda: RiskLevel.GREEN,
        real_order_submit_enabled=False,
    )

    receipt = submit(event, datetime(2026, 5, 24, 18, 10, tzinfo=timezone.utc))
    rows = conn.execute("SELECT reservation_status FROM edli_live_cap_usage").fetchall()

    assert receipt.side_effect_status == "SUBMIT_DISABLED"
    assert rows
    assert {row["reservation_status"] for row in rows} == {"RELEASED"}
    assert _cap_transition_status(receipt) == "RELEASED"
    assert _cap_transition_projection_status(receipt) == "RELEASED"


def test_edli_live_cap_path_does_not_reference_legacy_cap_columns():
    from pathlib import Path

    source = "\n".join(
        Path(path).read_text()
        for path in (
            "src/events/reactor.py",
            "src/engine/event_reactor_adapter.py",
            "src/strategy/live_inference/promotion_ledger.py",
        )
    )

    assert "cap_name" not in source
    assert "usage_date" not in source
    assert "SUM(notional_usd)" not in source


def test_live_adapter_submit_enabled_canary_disabled_blocks(monkeypatch):
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

    submit = adapter.event_bound_live_adapter_from_trade_conn(
        sqlite3.connect(":memory:"),
        get_current_level=lambda: RiskLevel.GREEN,
        real_order_submit_enabled=True,
        live_canary_enabled=False,
    )

    receipt = submit(event, datetime(2026, 5, 24, 18, 10, tzinfo=timezone.utc))

    assert receipt.proof_accepted is False
    assert receipt.reason == "LIVE_CANARY_DISABLED"


def test_live_adapter_submit_enabled_canary_enabled_calls_executor_mock(monkeypatch):
    from src.engine import event_reactor_adapter as adapter
    from src.engine.event_bound_final_intent import EventBoundExecutorSubmitResult
    from src.events.reactor import EventSubmissionReceipt
    from src.riskguard.risk_level import RiskLevel

    event = _forecast_event()
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    called = {"count": 0}
    monkeypatch.setattr(adapter, "build_event_bound_no_submit_receipt", lambda *_args, **_kwargs: _accepted_receipt(event))
    monkeypatch.setattr(adapter, "_build_live_execution_command_certificates", _command_bundle_with_real_cap)

    def _submit(_final_intent, _command):
        called["count"] += 1
        return EventBoundExecutorSubmitResult(
            status="SUBMITTED",
            reason_code="OK",
            venue_order_id="venue-1",
            submit_started_at="2026-05-24T18:10:00+00:00",
            submit_finished_at="2026-05-24T18:10:01+00:00",
            raw_response={"status": "submitted"},
        )

    submit = adapter.event_bound_live_adapter_from_trade_conn(
        conn,
        live_cap_conn=conn,
        get_current_level=lambda: RiskLevel.GREEN,
        real_order_submit_enabled=True,
        live_canary_enabled=True,
        executor_submit=_submit,
    )

    receipt = submit(event, datetime(2026, 5, 24, 18, 10, tzinfo=timezone.utc))

    assert called["count"] == 1
    assert receipt.submitted is True
    assert receipt.side_effect_status == "SUBMITTED"
    assert _receipt_status(receipt) == "SUBMITTED"
    assert conn.execute("SELECT reservation_status FROM edli_live_cap_usage").fetchone()["reservation_status"] == "CONSUMED"
    assert _cap_transition_status(receipt) == "CONSUMED"
    assert _cap_transition_projection_status(receipt) == "CONSUMED"


def test_live_adapter_records_rejected_fixture_response(monkeypatch):
    from src.engine import event_reactor_adapter as adapter
    from src.engine.event_bound_final_intent import EventBoundExecutorSubmitResult
    from src.events.reactor import EventSubmissionReceipt
    from src.riskguard.risk_level import RiskLevel

    event = _forecast_event()
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    monkeypatch.setattr(adapter, "build_event_bound_no_submit_receipt", lambda *_args, **_kwargs: _accepted_receipt(event))
    monkeypatch.setattr(adapter, "_build_live_execution_command_certificates", _command_bundle_with_real_cap)
    submit = adapter.event_bound_live_adapter_from_trade_conn(
        conn,
        live_cap_conn=conn,
        get_current_level=lambda: RiskLevel.GREEN,
        real_order_submit_enabled=True,
        live_canary_enabled=True,
        executor_submit=lambda _final_intent, _command: EventBoundExecutorSubmitResult(
            status="REJECTED",
            reason_code="VENUE_REJECTED",
            submit_started_at="2026-05-24T18:10:00+00:00",
            submit_finished_at="2026-05-24T18:10:01+00:00",
            raw_response={"status": "rejected"},
        ),
    )

    receipt = submit(event, datetime(2026, 5, 24, 18, 10, tzinfo=timezone.utc))

    assert receipt.submitted is False
    assert receipt.side_effect_status == "REJECTED"
    assert receipt.reason == "VENUE_REJECTED"
    assert _receipt_status(receipt) == "REJECTED"
    assert conn.execute("SELECT reservation_status FROM edli_live_cap_usage").fetchone()["reservation_status"] == "RELEASED"
    assert _cap_transition_status(receipt) == "RELEASED"
    assert _cap_transition_projection_status(receipt) == "RELEASED"


def test_live_adapter_records_timeout_unknown_fixture_response(monkeypatch):
    from src.engine import event_reactor_adapter as adapter
    from src.engine.event_bound_final_intent import EventBoundExecutorSubmitResult
    from src.events.reactor import EventSubmissionReceipt
    from src.riskguard.risk_level import RiskLevel

    event = _forecast_event()
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    monkeypatch.setattr(adapter, "build_event_bound_no_submit_receipt", lambda *_args, **_kwargs: _accepted_receipt(event))
    monkeypatch.setattr(adapter, "_build_live_execution_command_certificates", _command_bundle_with_real_cap)
    submit = adapter.event_bound_live_adapter_from_trade_conn(
        conn,
        live_cap_conn=conn,
        get_current_level=lambda: RiskLevel.GREEN,
        real_order_submit_enabled=True,
        live_canary_enabled=True,
        executor_submit=lambda _final_intent, _command: EventBoundExecutorSubmitResult(
            status="TIMEOUT_UNKNOWN",
            reason_code="SUBMIT_TIMEOUT",
            submit_started_at="2026-05-24T18:10:00+00:00",
            submit_finished_at="2026-05-24T18:10:30+00:00",
            raw_response={"status": "timeout"},
            reconciliation_followup_required=True,
        ),
    )

    receipt = submit(event, datetime(2026, 5, 24, 18, 10, tzinfo=timezone.utc))

    assert receipt.submitted is False
    assert receipt.side_effect_status == "TIMEOUT_UNKNOWN"
    assert _receipt_status(receipt) == "TIMEOUT_UNKNOWN"
    receipt_cert = _receipt_cert(receipt)
    assert receipt_cert.payload["reconciliation_followup_required"] is True
    assert conn.execute("SELECT reservation_status FROM edli_live_cap_usage").fetchone()["reservation_status"] == "RESERVED"
    assert _cap_transition_status(receipt) == "PENDING_RECONCILE"
    assert _cap_transition_projection_status(receipt) == "RESERVED"


def test_production_executor_boundary_rejects_unenriched_final_intent_before_executor():
    from src.engine.event_bound_final_intent import (
        EventBoundExecutorExpressibilityError,
        submit_event_bound_final_intent_via_existing_executor,
    )

    _actionable, final_intent, _expressibility, _live_cap, command = _command_cert_bundle()
    stripped = _replace_payload(final_intent, {"executable_snapshot_hash": ""})

    with pytest.raises(EventBoundExecutorExpressibilityError, match="executable_snapshot_hash missing"):
        submit_event_bound_final_intent_via_existing_executor(
            final_intent_cert=stripped,
            execution_command_cert=command,
            conn=sqlite3.connect(":memory:"),
            decision_time=datetime(2026, 5, 24, 18, 10, tzinfo=timezone.utc),
            executor_submit=lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("executor must not be called")),
        )


def test_production_executor_boundary_calls_spy_after_native_validation():
    from types import SimpleNamespace
    from src.engine.event_bound_final_intent import submit_event_bound_final_intent_via_existing_executor

    _actionable, final_intent, _expressibility, _live_cap, command = _command_cert_bundle()
    called = {"count": 0}

    def _spy(intent, **kwargs):
        called["count"] += 1
        assert intent.selected_token_id == final_intent.payload["token_id"]
        assert kwargs["decision_id"] == command.payload["execution_command_id"]
        return SimpleNamespace(status="pending", reason=None, order_id="venue-1", external_order_id=None)

    result = submit_event_bound_final_intent_via_existing_executor(
        final_intent_cert=final_intent,
        execution_command_cert=command,
        conn=sqlite3.connect(":memory:"),
        decision_time=datetime(2026, 5, 24, 18, 10, tzinfo=timezone.utc),
        executor_submit=_spy,
    )

    assert called["count"] == 1
    assert result.status == "SUBMITTED"


def test_main_live_mode_wires_production_executor_boundary_source():
    from pathlib import Path

    source = Path("src/main.py").read_text()

    assert "submit_event_bound_final_intent_via_existing_executor" in source
    assert "executor_submit=lambda final_intent_cert, execution_command_cert" in source
    assert "reactor_mode == \"live\"" in source


def _accepted_receipt(event):
    from src.events.reactor import EventSubmissionReceipt

    return EventSubmissionReceipt(
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
    )


def _command_cert_bundle():
    from src.decision_kernel import claims
    from src.decision_kernel.certificates.execution import build_execution_command_certificate_from_final_intent
    from tests.decision_kernel.test_execution_command_certificate import builder_chain

    actionable, final_intent, expressibility, live_cap = builder_chain()
    command = build_execution_command_certificate_from_final_intent(
        actionable_cert=actionable,
        final_intent_cert=final_intent,
        executor_expressibility_cert=expressibility,
        live_cap_cert=live_cap,
        decision_time=datetime(2026, 5, 25, 12, tzinfo=timezone.utc),
    )
    assert final_intent.certificate_type == claims.FINAL_INTENT
    assert command.certificate_type == claims.EXECUTION_COMMAND
    return (actionable, final_intent, expressibility, live_cap, command)


def _command_bundle_with_real_cap(**kwargs):
    from src.engine import event_reactor_adapter as adapter

    live_cap = adapter._build_live_cap_certificate_from_ledger(
        event=kwargs["event"],
        receipt=kwargs["receipt"],
        decision_time=kwargs["decision_time"],
        max_notional_usd=kwargs["tiny_live_max_notional_usd"],
        live_cap_conn=kwargs["live_cap_conn"],
    )
    actionable, final_intent, expressibility, _old_live_cap, command = _command_cert_bundle()
    return (actionable, live_cap, final_intent, expressibility, command)


def _replace_payload(cert, updates):
    from src.decision_kernel.certificate import build_certificate

    return build_certificate(
        certificate_type=cert.certificate_type,
        semantic_key=cert.semantic_key + ":modified",
        claim_type=cert.header.claim_type,
        mode=cert.header.mode,
        decision_time=cert.header.decision_time,
        source_available_at=cert.header.source_available_at,
        agent_received_at=cert.header.agent_received_at,
        persisted_at=cert.header.persisted_at,
        payload={**cert.payload, **updates},
        parent_edges=cert.header.parent_edges,
        authority_id=cert.header.authority_id,
        authority_version=cert.header.authority_version,
        algorithm_id=cert.header.algorithm_id,
        algorithm_version=cert.header.algorithm_version,
    )


def _receipt_cert(receipt):
    from src.decision_kernel import claims

    for cert in receipt.decision_proof_bundle:
        if getattr(cert, "certificate_type", None) == claims.EXECUTION_RECEIPT:
            return cert
    raise AssertionError("ExecutionReceiptCertificate missing")


def _receipt_status(receipt):
    return _receipt_cert(receipt).payload["status"]


def _cap_transition_cert(receipt):
    from src.decision_kernel import claims

    for cert in receipt.decision_proof_bundle:
        if getattr(cert, "certificate_type", None) == claims.LIVE_CAP_TRANSITION:
            return cert
    raise AssertionError("LiveCapTransitionCertificate missing")


def _cap_transition_status(receipt):
    return _cap_transition_cert(receipt).payload["to_status"]


def _cap_transition_projection_status(receipt):
    return _cap_transition_cert(receipt).payload["projection_status"]


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
