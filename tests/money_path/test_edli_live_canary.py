# Created: 2026-05-25
# Last reused/audited: 2026-05-25
# Authority basis: docs/operations/edli_v1/EDLI_REDEMPTION_FINAL_PACKAGE_SPEC.md §14 full-live increment.
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

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


def test_submit_disabled_live_bridge_writes_live_order_aggregate_without_command_builder_monkeypatch():
    from src.decision_kernel import claims
    from src.engine import event_reactor_adapter as adapter
    from tests.decision_kernel.no_submit_fixtures import build_test_no_submit_proof_bundle

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    event = _forecast_event()
    decision_time = datetime(2026, 5, 24, 18, 10, tzinfo=timezone.utc)
    accepted = _accepted_receipt(event)
    accepted = replace(
        accepted,
        decision_proof_bundle=build_test_no_submit_proof_bundle(event, accepted, decision_time=decision_time),
    )

    certificates = adapter._build_submit_disabled_live_certificates(
        event=event,
        receipt=accepted,
        decision_time=decision_time,
        tiny_live_max_notional_usd=5.0,
        live_cap_conn=conn,
        pre_submit_authority_provider=_pre_submit_authority_provider,
    )

    events = conn.execute(
        """
        SELECT event_type, event_hash
        FROM edli_live_order_events
        ORDER BY event_sequence
        """
    ).fetchall()
    event_types = [row["event_type"] for row in events]
    command = _required_cert(certificates, claims.EXECUTION_COMMAND)
    transition = _required_cert(certificates, claims.LIVE_CAP_TRANSITION)
    projection = conn.execute("SELECT * FROM edli_live_order_projection").fetchone()

    assert event_types == [
        "DecisionProofAccepted",
        "SubmitPlanBuilt",
        "PreSubmitRevalidated",
        "LiveCapReserved",
        "ExecutionCommandCreated",
        "CapTransitioned",
    ]
    assert command.payload["aggregate_pre_submit_event_hash"] == events[2]["event_hash"]
    assert command.payload["aggregate_execution_command_event_hash"] == events[4]["event_hash"]
    assert transition.payload["aggregate_cap_transition_event_hash"] == events[5]["event_hash"]
    assert projection["current_state"] == "CAP_TRANSITIONED"


def test_live_execution_command_build_fails_without_pre_submit_authority_witness():
    from src.engine import event_reactor_adapter as adapter
    from tests.decision_kernel.no_submit_fixtures import build_test_no_submit_proof_bundle

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    event = _forecast_event()
    decision_time = datetime(2026, 5, 24, 18, 10, tzinfo=timezone.utc)
    accepted = _accepted_receipt(event)
    accepted = replace(
        accepted,
        decision_proof_bundle=build_test_no_submit_proof_bundle(event, accepted, decision_time=decision_time),
    )

    with pytest.raises(ValueError, match="PRE_SUBMIT_AUTHORITY_WITNESS_REQUIRED"):
        adapter._build_live_execution_command_certificates(
            event=event,
            receipt=accepted,
            decision_time=decision_time,
            tiny_live_max_notional_usd=5.0,
            live_cap_conn=conn,
        )


def test_crossing_post_only_pre_submit_witness_blocks_command():
    from src.engine import event_reactor_adapter as adapter
    from tests.decision_kernel.no_submit_fixtures import build_test_no_submit_proof_bundle

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    event = _forecast_event()
    decision_time = datetime(2026, 5, 24, 18, 10, tzinfo=timezone.utc)
    accepted = _accepted_receipt(event)
    accepted = replace(
        accepted,
        decision_proof_bundle=build_test_no_submit_proof_bundle(event, accepted, decision_time=decision_time),
    )

    with pytest.raises(Exception, match="would_cross_book=false"):
        adapter._build_live_execution_command_certificates(
            event=event,
            receipt=accepted,
            decision_time=decision_time,
            tiny_live_max_notional_usd=5.0,
            live_cap_conn=conn,
            pre_submit_authority_provider=lambda *_args: _pre_submit_authority_witness(current_best_ask=0.39),
        )


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


def test_live_adapter_records_post_submit_unknown_as_pending_reconcile(monkeypatch):
    from src.engine import event_reactor_adapter as adapter
    from src.engine.event_bound_final_intent import EventBoundExecutorSubmitResult
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
            status="POST_SUBMIT_UNKNOWN",
            reason_code="SDK_EXCEPTION_AFTER_SEND",
            submit_started_at="2026-05-24T18:10:00+00:00",
            submit_finished_at="2026-05-24T18:10:01+00:00",
            raw_response={"status": "exception_after_send"},
            reconciliation_followup_required=True,
            venue_call_started=True,
            venue_ack_received=False,
            side_effect_known=False,
        ),
    )

    receipt = submit(event, datetime(2026, 5, 24, 18, 10, tzinfo=timezone.utc))

    assert receipt.submitted is False
    assert receipt.side_effect_status == "POST_SUBMIT_UNKNOWN"
    assert _receipt_status(receipt) == "POST_SUBMIT_UNKNOWN"
    receipt_cert = _receipt_cert(receipt)
    assert receipt_cert.payload["venue_call_started"] is True
    assert receipt_cert.payload["side_effect_known"] is False
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
    assert "live_bridge_mode" in source
    assert "submit_disabled_live_bridge" in source
    assert "pre_submit_authority_provider=_edli_pre_submit_authority_provider_from_world_conn" in source


def test_main_pre_submit_authority_provider_hydrates_typed_provenance(monkeypatch):
    import src.main as main
    import src.control.heartbeat_supervisor as heartbeat_supervisor
    import src.control.ws_gap_guard as ws_gap_guard
    import src.data.polymarket_client as polymarket_client

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE execution_feasibility_evidence (
            token_id TEXT,
            quote_seen_at TEXT,
            book_hash_before TEXT,
            best_bid_before REAL,
            best_ask_before REAL
        )
        """
    )
    conn.execute(
        """
        INSERT INTO execution_feasibility_evidence
            (token_id, quote_seen_at, book_hash_before, best_bid_before, best_ask_before)
        VALUES (?, ?, ?, ?, ?)
        """,
        ("yes-1", "2026-05-25T11:59:59.950000+00:00", "book-hash-1", 0.39, 0.41),
    )
    monkeypatch.setattr(heartbeat_supervisor, "summary", lambda: {"entry": {"allow_submit": True}})
    monkeypatch.setattr(ws_gap_guard, "summary", lambda *, now=None: {"entry": {"allow_submit": True}})

    class FakePolymarketClient:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def get_wallet_balance(self):
            return 25.0

    monkeypatch.setattr(polymarket_client, "PolymarketClient", FakePolymarketClient)
    provider = main._edli_pre_submit_authority_provider_from_world_conn(
        conn,
        {
            "pre_submit_max_quote_age_ms": 1000,
            "pre_submit_balance_allowance_check_enabled": True,
        },
    )
    final_intent = SimpleNamespace(
        payload={
            "token_id": "yes-1",
            "tick_size": 0.01,
            "min_order_size": 1.0,
            "neg_risk": False,
            "notional_usd": 5.0,
        }
    )

    witness = provider(final_intent, object(), datetime(2026, 5, 25, 12, tzinfo=timezone.utc))

    assert witness.book_hash == "book-hash-1"
    assert witness.book_authority_id == "execution_feasibility_evidence"
    assert witness.heartbeat_authority_id == "heartbeat_supervisor"
    assert witness.user_ws_authority_id == "ws_gap_guard"
    assert witness.balance_allowance_authority_id == "polymarket_wallet_readonly"
    assert witness.balance_allowance_status == "OK"


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
        q_live=0.7,
        q_lcb_5pct=0.6,
        c_fee_adjusted=0.4,
        c_cost_95pct=0.45,
        p_fill_lcb=0.1,
        trade_score=0.2,
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
    pre_submit = _pre_submit_cert(final_intent, live_cap)
    command = build_execution_command_certificate_from_final_intent(
        actionable_cert=actionable,
        final_intent_cert=final_intent,
        executor_expressibility_cert=expressibility,
        live_cap_cert=live_cap,
        pre_submit_revalidation_cert=pre_submit,
        decision_time=datetime(2026, 5, 25, 12, tzinfo=timezone.utc),
    )
    assert final_intent.certificate_type == claims.FINAL_INTENT
    assert command.certificate_type == claims.EXECUTION_COMMAND
    return (actionable, final_intent, expressibility, live_cap, command)


def _pre_submit_cert(final_intent, live_cap):
    from src.decision_kernel import claims
    from src.decision_kernel.certificate import ParentEdge, build_certificate

    now = datetime(2026, 5, 25, 12, tzinfo=timezone.utc)
    payload = {
        "event_id": "event-1",
        "final_intent_id": "intent-1",
        "condition_id": "condition-1",
        "token_id": "yes-1",
        "side": "BUY",
        "direction": "buy_yes",
        "order_type": "POST_ONLY_LIMIT",
        "time_in_force": "GTC",
        "post_only": True,
        "checked_at": now.isoformat(),
        "quote_seen_at": now.isoformat(),
        "quote_age_ms": 0,
        "max_quote_age_ms": 1000,
        "book_hash": "book-hash",
        "current_best_bid": 0.39,
        "current_best_ask": 0.41,
        "limit_price": 0.4,
        "would_cross_book": False,
        "tick_size": 0.01,
        "tick_aligned": True,
        "min_order_size": 1.0,
        "size_ok": True,
        "neg_risk": False,
        "heartbeat_status": "OK",
        "user_ws_status": "OK",
        "venue_connectivity_status": "OK",
        "balance_allowance_status": "OK",
        "aggregate_id": "event-1:intent-1",
        "aggregate_event_hash": "pre-submit-hash",
        "aggregate_execution_command_event_hash": "command-hash",
        "final_intent_certificate_hash": final_intent.certificate_hash,
        "live_cap_usage_id": live_cap.payload["usage_id"],
    }
    parents = (final_intent, live_cap)
    return build_certificate(
        certificate_type=claims.PRE_SUBMIT_REVALIDATION,
        semantic_key="pre-submit:event-1:intent-1",
        claim_type=claims.PRE_SUBMIT_REVALIDATION,
        mode="LIVE",
        decision_time=now,
        source_available_at=now,
        agent_received_at=now,
        persisted_at=now,
        payload=payload,
        parent_edges=tuple(
            ParentEdge(parent.certificate_type.removesuffix("Certificate").lower(), parent.certificate_hash, parent.certificate_type)
            for parent in parents
        ),
        parent_certificates=parents,
        authority_id="test",
        authority_version="v1",
        algorithm_id="test",
        algorithm_version="v1",
    )


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


def _required_cert(certs, certificate_type):
    for cert in certs:
        if getattr(cert, "certificate_type", None) == certificate_type:
            return cert
    raise AssertionError(f"{certificate_type} missing")


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


def _pre_submit_authority_provider(_final_intent, _executable_snapshot, decision_time):
    return _pre_submit_authority_witness(decision_time=decision_time)


def _pre_submit_authority_witness(
    *,
    decision_time: datetime | None = None,
    current_best_bid: float = 0.39,
    current_best_ask: float = 0.41,
    tick_size: float = 0.01,
    min_order_size: float = 1.0,
    heartbeat_status: str = "OK",
    user_ws_status: str = "OK",
    venue_connectivity_status: str = "OK",
    balance_allowance_status: str = "OK",
):
    from src.engine.event_reactor_adapter import PreSubmitAuthorityWitness

    checked_at = decision_time or datetime(2026, 5, 24, 18, 10, tzinfo=timezone.utc)
    quote_seen_at = checked_at - timedelta(milliseconds=50)
    return PreSubmitAuthorityWitness(
        quote_seen_at=quote_seen_at.isoformat(),
        book_hash="book-hash-1",
        current_best_bid=current_best_bid,
        current_best_ask=current_best_ask,
        tick_size=tick_size,
        min_order_size=min_order_size,
        neg_risk=False,
        heartbeat_status=heartbeat_status,
        user_ws_status=user_ws_status,
        venue_connectivity_status=venue_connectivity_status,
        balance_allowance_status=balance_allowance_status,
        book_authority_id="execution_feasibility_evidence",
        book_captured_at=quote_seen_at.isoformat(),
        heartbeat_authority_id="heartbeat_supervisor",
        heartbeat_checked_at=checked_at.isoformat(),
        user_ws_authority_id="ws_gap_guard",
        user_ws_checked_at=checked_at.isoformat(),
        venue_connectivity_authority_id="polymarket_public_orderbook",
        venue_connectivity_checked_at=checked_at.isoformat(),
        balance_allowance_authority_id="polymarket_wallet_readonly",
        balance_allowance_checked_at=checked_at.isoformat(),
        checked_at=checked_at.isoformat(),
        max_quote_age_ms=1000,
    )
