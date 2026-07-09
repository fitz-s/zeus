# Created: 2026-05-25
# Last reused/audited: 2026-07-02
# Authority basis: docs/operations/edli_v1/EDLI_REDEMPTION_FINAL_PACKAGE_SPEC.md §14 full-live increment.
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest


def _edli_settings() -> dict:
    from src.config import settings

    return settings._data["edli"]


def _install_unpaused_world_control_db(monkeypatch, tmp_path: Path) -> Path:
    from src.state import db as state_db
    from src.state.ledger import apply_architecture_kernel_schema

    world_path = tmp_path / "zeus-world.db"
    setup_conn = sqlite3.connect(str(world_path))
    try:
        apply_architecture_kernel_schema(setup_conn)
        setup_conn.commit()
    finally:
        setup_conn.close()

    def _get_world_connection():
        conn = sqlite3.connect(str(world_path))
        conn.row_factory = sqlite3.Row
        return conn

    monkeypatch.setattr(state_db, "get_world_connection", _get_world_connection)
    return world_path


def test_live_canary_runtime_requires_operator_unshadow_and_submit_guards():
    """Current live-canary contract after operator unshadow.

    This used to assert the pre-unshadow shadow/no-submit state. The operator has
    since authorized real live canary, so the load-bearing guard is now coherent
    real-submit wiring: live mode, live canary, durable outbox, and taker path all
    enabled together rather than a split shadow/live configuration.
    """
    edli = _edli_settings()

    assert edli["real_order_submit_enabled"] is True
    assert edli["live_execution_mode"] == "edli_live"
    assert edli["reactor_mode"] == "live"
    # Wave-1 2026-06-12: live_canary_enabled gate flag DELETED — live submit no longer
    # requires a separate canary on/off flag (operator arm + real-submit flag are the gates).
    assert "live_canary_enabled" not in edli
    assert edli["durable_submit_outbox_enabled"] is True
    assert edli["enabled"] is True
    assert edli["event_writer_enabled"] is True
    assert edli["forecast_snapshot_trigger_enabled"] is True
    # Day0 live promotion 2026-06-12 (task #49): scope moved to
    # forecast_plus_day0 once receipt-q persistence + obs fast lane landed.
    assert edli["edli_live_scope"] == "forecast_plus_day0"
    assert edli["day0_extreme_trigger_enabled"] is True
    assert edli["day0_hard_fact_live_enabled"] is True
    assert edli["market_channel_ingestor_enabled"] is True
    # Wave-2 item 8: taker_fok_fak_live_enabled DELETED (taker law unconditional) — key absent.
    assert "taker_fok_fak_live_enabled" not in edli


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


def test_event_bound_executable_neg_risk_uses_hydrated_snapshot_single_source():
    """Regression: executable cert metadata must match the cited snapshot."""
    from src.engine.event_reactor_adapter import _event_bound_executable_snapshot_neg_risk

    snapshot_false = SimpleNamespace(neg_risk=False)
    snapshot_true = SimpleNamespace(neg_risk=True)

    assert _event_bound_executable_snapshot_neg_risk(
        raw_receipt={"neg_risk": True},
        selected_snapshot_row={"neg_risk": 1},
        hydrated_snapshot=snapshot_false,
    ) is False
    assert _event_bound_executable_snapshot_neg_risk(
        raw_receipt={"neg_risk": False},
        selected_snapshot_row={"neg_risk": 0},
        hydrated_snapshot=snapshot_true,
    ) is True
    assert _event_bound_executable_snapshot_neg_risk(
        raw_receipt={},
        selected_snapshot_row={"neg_risk": 1},
        hydrated_snapshot=snapshot_false,
    ) is False


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
            mainstream_agreement_pass=True,
            mainstream_point=0.42,
            mainstream_delta=0.01,
            mainstream_bin_label="21C",
            mainstream_source="cache",
            mainstream_fetched_at_utc="2026-05-24T18:09:00+00:00",
            alpha_gap=0.12,
            q_source="emos",
            opportunity_book={
                "book_id": "book-1",
                "selected_candidate_id": "candidate-1",
            },
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
    assert receipt.q_source == "emos"
    assert receipt.opportunity_book == {
        "book_id": "book-1",
        "selected_candidate_id": "candidate-1",
    }
    assert receipt.mainstream_agreement_pass is True
    assert receipt.mainstream_point == 0.42
    assert receipt.alpha_gap == 0.12


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


def test_live_order_build_savepoint_retries_sqlite_lock_after_rollback(monkeypatch):
    from src.engine import event_reactor_adapter as adapter

    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE writes (value TEXT)")
    attempts = {"count": 0}
    sleeps = []
    monkeypatch.setenv("ZEUS_LIVE_ORDER_BUILD_LOCK_RETRY_SECONDS", "0.01")
    monkeypatch.setattr(adapter._time, "sleep", lambda delay: sleeps.append(delay))

    def _build():
        attempts["count"] += 1
        if attempts["count"] == 1:
            conn.execute("INSERT INTO writes(value) VALUES ('rolled-back')")
            raise sqlite3.OperationalError("database is locked")
        rows = conn.execute("SELECT value FROM writes").fetchall()
        assert rows == []
        conn.execute("INSERT INTO writes(value) VALUES ('committed')")
        return ("command-cert",)

    result = adapter._run_live_order_build_savepoint(conn, _build)

    assert result == ("command-cert",)
    assert attempts["count"] == 2
    assert sleeps == [0.01]
    assert [row[0] for row in conn.execute("SELECT value FROM writes")] == ["committed"]


def test_live_order_build_savepoint_scopes_busy_timeout(monkeypatch):
    from src.engine import event_reactor_adapter as adapter

    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA busy_timeout = 1234")
    conn.execute("CREATE TABLE writes (value TEXT)")
    observed = {}
    monkeypatch.setenv("ZEUS_LIVE_ORDER_BUILD_BUSY_TIMEOUT_MS", "8000")
    monkeypatch.setenv("ZEUS_LIVE_ORDER_BUILD_LOCK_RETRY_SECONDS", "0.01")
    monkeypatch.setattr(adapter._time, "sleep", lambda _delay: None)

    def _build():
        observed["inside"] = conn.execute("PRAGMA busy_timeout").fetchone()[0]
        conn.execute("INSERT INTO writes(value) VALUES ('committed')")
        return ("command-cert",)

    result = adapter._run_live_order_build_savepoint(conn, _build)

    assert result == ("command-cert",)
    assert observed == {"inside": 8000}
    assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 1234
    assert [row[0] for row in conn.execute("SELECT value FROM writes")] == ["committed"]


def test_live_order_build_savepoint_does_not_retry_non_lock_operational_error(monkeypatch):
    from src.engine import event_reactor_adapter as adapter

    conn = sqlite3.connect(":memory:")
    attempts = {"count": 0}
    monkeypatch.setenv("ZEUS_LIVE_ORDER_BUILD_LOCK_RETRY_SECONDS", "0.01")
    monkeypatch.setattr(adapter._time, "sleep", lambda _delay: pytest.fail("unexpected sleep"))

    def _build():
        attempts["count"] += 1
        raise sqlite3.OperationalError("no such table: live_cap")

    with pytest.raises(sqlite3.OperationalError, match="no such table"):
        adapter._run_live_order_build_savepoint(conn, _build)

    assert attempts["count"] == 1


def test_live_cap_certificate_is_backed_by_usage_row():
    from src.engine import event_reactor_adapter as adapter
    from src.events.live_cap import LIVE_EXECUTION_RESERVATION_SCOPE
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
    assert row["cap_scope"] == LIVE_EXECUTION_RESERVATION_SCOPE
    assert row["reservation_status"] == "RESERVED"
    assert row["reserved_notional_usd"] == cert.payload["reserved_notional_usd"]
    assert row["final_intent_id"] == "intent-1"
    assert cert.payload["cap_scope"] == LIVE_EXECUTION_RESERVATION_SCOPE
    assert "canary" not in cert.payload["cap_scope"]
    assert cert.header.algorithm_id == "edli.live_execution_reservation"
    assert cert.header.authority_id == "edli.live_execution_reservation"


def test_live_cap_provisional_and_durable_share_uncapped_notional(monkeypatch):
    # 2026-06-08: the tiny_live cap is DELETED. The provisional (persist=False)
    # and durable (persist=True) certificates record the SAME full Kelly notional
    # with no clamp and no notional-cap flag — proving the order size flows from
    # fractional Kelly, not from any $5 cap.
    from src.engine import event_reactor_adapter as adapter
    from src.events.reactor import EventSubmissionReceipt

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    event = _forecast_event()
    receipt = EventSubmissionReceipt(
        submitted=False,
        proof_accepted=True,
        event_id=event.event_id,
        causal_snapshot_id=event.causal_snapshot_id,
        c_fee_adjusted=0.4,
        kelly_size_usd=800.0,
        final_intent_id="intent-uncapped",
    )
    kwargs = dict(
        event=event,
        receipt=receipt,
        decision_time=datetime(2026, 5, 24, 18, 10, tzinfo=timezone.utc),
        live_cap_conn=conn,
    )

    provisional = adapter._build_live_cap_certificate_from_ledger(**kwargs, persist=False)
    durable = adapter._build_live_cap_certificate_from_ledger(**kwargs, persist=True)

    assert provisional.payload["reserved_notional_usd"] == 800.0
    assert durable.payload["reserved_notional_usd"] == 800.0
    assert durable.payload["reserved_notional_usd"] == provisional.payload["reserved_notional_usd"]
    # max_notional_usd is now an inert mirror of the reserved notional, not a cap.
    assert durable.payload["max_notional_usd"] == provisional.payload["max_notional_usd"] == 800.0
    # No notional-cap flag exists in the payload anymore.
    assert "notional_cap_enabled" not in provisional.payload
    assert "notional_cap_enabled" not in durable.payload


def test_submit_disabled_live_bridge_releases_live_cap_row(monkeypatch):
    from src.engine import event_reactor_adapter as adapter
    from src.riskguard.risk_level import RiskLevel
    from src.state.schema.edli_live_cap_usage_schema import ensure_table
    from tests.decision_kernel.no_submit_fixtures import build_test_no_submit_proof_bundle

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    ensure_table(conn)
    event = _forecast_event()
    decision_time = datetime(2026, 5, 24, 18, 10, tzinfo=timezone.utc)
    accepted = _accepted_receipt(event)
    accepted = replace(
        accepted,
        decision_proof_bundle=build_test_no_submit_proof_bundle(event, accepted, decision_time=decision_time),
    )
    monkeypatch.setattr(adapter, "build_event_bound_no_submit_receipt", lambda *_args, **_kwargs: accepted)

    submit = adapter.event_bound_live_adapter_from_trade_conn(
        conn,
        live_cap_conn=conn,
        get_current_level=lambda: RiskLevel.GREEN,
        real_order_submit_enabled=False,
        pre_submit_authority_provider=_pre_submit_authority_provider,
    )

    receipt = submit(event, decision_time)
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
    accepted = _accepted_receipt(
        event,
        execution_mode_intent="MAKER",
        maker_limit_price=0.38,
        rest_then_cross_policy="REST_DEFAULT",
    )
    accepted = replace(
        accepted,
        decision_proof_bundle=build_test_no_submit_proof_bundle(event, accepted, decision_time=decision_time),
    )

    certificates = adapter._build_submit_disabled_live_certificates(
        event=event,
        receipt=accepted,
        decision_time=decision_time,
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


def test_submit_disabled_bridge_uses_latest_parent_evidence_time_for_redecision():
    from src.decision_kernel import claims
    from src.decision_kernel.compiler import DecisionCompiler, EvidenceClock
    from src.engine import event_reactor_adapter as adapter
    from tests.decision_kernel.no_submit_fixtures import build_test_no_submit_proof_bundle

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    event = _forecast_event()
    event_decision_time = datetime(2026, 5, 24, 18, 10, tzinfo=timezone.utc)
    refreshed_evidence_time = event_decision_time + timedelta(minutes=2)
    accepted = _accepted_receipt(event)
    proof_bundle = build_test_no_submit_proof_bundle(
        event,
        accepted,
        decision_time=event_decision_time,
    )
    proof_bundle = replace(
        proof_bundle,
        quote_feasibility=replace(
            proof_bundle.quote_feasibility,
            clock=EvidenceClock(
                refreshed_evidence_time,
                refreshed_evidence_time,
                refreshed_evidence_time,
            ),
        ),
    )
    accepted = replace(accepted, decision_proof_bundle=proof_bundle)

    direct = DecisionCompiler().compile_no_submit(
        event,
        decision_time=event_decision_time,
        proof_bundle=proof_bundle,
    )
    assert direct.status == "REJECTED"
    assert "after decision_time" in (direct.failures[0].reason_detail or "")

    certificates = adapter._build_submit_disabled_live_certificates(
        event=event,
        receipt=accepted,
        decision_time=event_decision_time,
        live_cap_conn=conn,
        pre_submit_authority_provider=_pre_submit_authority_provider,
    )

    command = _required_cert(certificates, claims.EXECUTION_COMMAND)
    receipt_cert = _required_cert(certificates, claims.EXECUTION_RECEIPT)
    transition = _required_cert(certificates, claims.LIVE_CAP_TRANSITION)
    assert command.header.decision_time == refreshed_evidence_time
    assert receipt_cert.header.decision_time == refreshed_evidence_time
    assert transition.header.decision_time == refreshed_evidence_time


def _seed_active_family_order(conn, *, aggregate_id="aggregate-1", plan_updates=None):
    """An OPEN/in-flight order for (condition-1, token-no-1, buy_no): no terminal event."""
    payload = {
        "event_id": "event-1",
        "final_intent_id": "intent-1",
        "condition_id": "condition-1",
        "token_id": "token-no-1",
        "direction": "buy_no",
        "limit_price": 0.70,
    }
    payload.update(plan_updates or {})
    _insert_live_order_event(
        conn,
        aggregate_id=aggregate_id,
        sequence=1,
        event_type="SubmitPlanBuilt",
        payload=payload,
    )
    _insert_live_order_event(
        conn,
        aggregate_id=aggregate_id,
        sequence=2,
        event_type="ExecutionCommandCreated",
        payload={
            "event_id": "event-1",
            "final_intent_id": "intent-1",
            "execution_command_id": "command-1",
        },
    )


def _lock_reason(
    conn,
    *,
    condition_id="condition-1",
    token_id="token-no-1",
    direction="buy_no",
    family_id=None,
    city=None,
    target_date=None,
    metric=None,
    limit_price=0.70,
):
    from src.engine import event_reactor_adapter as adapter

    return adapter._locked_live_opportunity_active_order_reason(
        conn,
        condition_id=condition_id,
        token_id=token_id,
        direction=direction,
        family_id=family_id,
        city=city,
        target_date=target_date,
        metric=metric,
        side="BUY",
        limit_price=limit_price,
    )


def test_terminal_no_fill_reprice_suppresses_before_live_order_append():
    """A same-price terminal zero-fill retry should not emit a new EDLI proof/cap."""

    from src.engine import event_reactor_adapter as adapter

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE venue_commands (
            command_id TEXT,
            position_id TEXT,
            intent_kind TEXT,
            side TEXT,
            token_id TEXT,
            state TEXT,
            price REAL,
            size REAL,
            created_at TEXT,
            updated_at TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE venue_order_facts (
            command_id TEXT,
            state TEXT,
            matched_size TEXT,
            local_sequence INTEGER,
            observed_at TEXT
        )
        """
    )
    conn.execute(
        """
        INSERT INTO venue_commands
        VALUES (
            'cmd-zero-fill', 'old-pos', 'ENTRY', 'BUY', 'tok-no',
            'CANCELLED', 0.61, 18.52,
            '2026-07-08T12:29:35+00:00',
            '2026-07-08T12:35:57+00:00'
        )
        """
    )
    conn.execute(
        """
        INSERT INTO venue_order_facts
        VALUES (
            'cmd-zero-fill', 'CANCEL_CONFIRMED', '0', 1,
            '2026-07-08T12:35:57+00:00'
        )
        """
    )

    reason = adapter._same_token_terminal_no_fill_reprice_suppression_reason(
        conn,
        token_id="tok-no",
        candidate_position_id="fresh-intent",
        limit_price=0.61,
    )

    assert reason is not None
    assert reason.startswith("ENTRY_COOLDOWN_TERMINAL_NO_FILL_REPRICE_REQUIRED")
    assert "existing_command_id=cmd-zero-fill" in reason


def test_terminal_no_fill_reprice_guard_allows_real_reprice_and_positive_fill():
    from src.engine import event_reactor_adapter as adapter

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE venue_commands (
            command_id TEXT,
            position_id TEXT,
            intent_kind TEXT,
            side TEXT,
            token_id TEXT,
            state TEXT,
            price REAL,
            size REAL,
            created_at TEXT,
            updated_at TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE venue_order_facts (
            command_id TEXT,
            state TEXT,
            matched_size TEXT,
            local_sequence INTEGER,
            observed_at TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE venue_trade_facts (
            command_id TEXT,
            filled_size REAL
        )
        """
    )
    conn.execute(
        """
        INSERT INTO venue_commands
        VALUES (
            'cmd-zero-fill', 'old-pos', 'ENTRY', 'BUY', 'tok-no',
            'CANCELLED', 0.61, 18.52,
            '2026-07-08T12:29:35+00:00',
            '2026-07-08T12:35:57+00:00'
        )
        """
    )
    conn.execute(
        """
        INSERT INTO venue_order_facts
        VALUES (
            'cmd-zero-fill', 'CANCEL_CONFIRMED', '0', 1,
            '2026-07-08T12:35:57+00:00'
        )
        """
    )

    repriced = adapter._same_token_terminal_no_fill_reprice_suppression_reason(
        conn,
        token_id="tok-no",
        candidate_position_id="fresh-intent",
        limit_price=0.611,
    )
    assert repriced is None

    conn.execute(
        "INSERT INTO venue_trade_facts VALUES ('cmd-zero-fill', 1.25)"
    )
    same_price_after_fill = adapter._same_token_terminal_no_fill_reprice_suppression_reason(
        conn,
        token_id="tok-no",
        candidate_position_id="fresh-intent",
        limit_price=0.61,
    )
    assert same_price_after_fill is None


def test_entry_global_submit_guard_suppresses_configured_reduce_only(monkeypatch):
    from src.engine import event_reactor_adapter as adapter
    from src.risk_allocator import AllocationDecision, AllocationDenied

    monkeypatch.setattr(
        "src.risk_allocator.summary",
        lambda: {
            "configured": True,
            "reduce_only": True,
            "kill_switch_reason": None,
            "entry": {"allow_submit": False, "reason": "reduce_only_mode_active"},
        },
    )

    def _deny_submit(*, reduce_only=False):
        raise AllocationDenied(
            AllocationDecision(
                False,
                "reduce_only_mode_active",
                0,
                reduce_only=reduce_only,
            )
        )

    monkeypatch.setattr("src.risk_allocator.assert_global_submit_allows", _deny_submit)

    reason = adapter._entry_global_submit_suppression_reason()

    assert reason is not None
    assert reason.startswith("RISK_ALLOCATOR_GLOBAL_ENTRY_UNAVAILABLE:")
    assert "reason=reduce_only_mode_active" in reason
    assert "reduce_only=True" in reason


def test_entry_global_submit_guard_does_not_early_block_unconfigured(monkeypatch):
    from src.engine import event_reactor_adapter as adapter

    monkeypatch.setattr(
        "src.risk_allocator.summary",
        lambda: {
            "configured": False,
            "entry": {"allow_submit": False, "reason": "allocator_not_configured"},
        },
    )

    def _unexpected_submit_check(*, reduce_only=False):
        raise AssertionError("unconfigured runtimes must fall through")

    monkeypatch.setattr("src.risk_allocator.assert_global_submit_allows", _unexpected_submit_check)

    assert adapter._entry_global_submit_suppression_reason() is None


def test_fixA_active_live_order_suppresses_new_submit():
    """FIX A (#125): a genuinely ACTIVE (OPEN/in-flight) order for the family blocks
    a duplicate submit — regardless of any price improvement (the retired 0.02 gate
    is gone). Duplicate-prevention is bound to ACTIVE orders only."""
    conn = sqlite3.connect(":memory:")
    _seed_active_family_order(conn)

    # Same price AND a materially better (lower) buy price are BOTH suppressed:
    # an active live order must never be duplicated, price-improvement irrelevant.
    same_price = _lock_reason(conn, limit_price=0.70)
    much_better = _lock_reason(conn, limit_price=0.50)

    assert same_price is not None
    assert same_price.startswith("EDLI_LIVE_ORDER_ACTIVE_DUPLICATE_SUPPRESSED")
    assert much_better is not None  # no price-improvement escape while order is live


def test_fixA_family_sibling_active_order_suppresses_new_submit():
    """A live resting order on one weather-family bin blocks sibling-bin entry."""
    conn = sqlite3.connect(":memory:")
    _seed_active_family_order(
        conn,
        plan_updates={
            "family_id": "weather-family-chicago-2026-06-19-high",
            "city": "Chicago",
            "target_date": "2026-06-19",
            "metric": "high",
        },
    )

    reason = _lock_reason(
        conn,
        condition_id="condition-sibling",
        token_id="token-no-sibling",
        direction="buy_no",
        family_id="weather-family-chicago-2026-06-19-high",
        city="Chicago",
        target_date="2026-06-19",
        metric="high",
        limit_price=0.68,
    )

    assert reason is not None
    assert reason.startswith("EDLI_LIVE_ORDER_ACTIVE_DUPLICATE_SUPPRESSED")
    assert "family_id=weather-family-chicago-2026-06-19-high" in reason


def test_fixA_newer_terminal_aggregate_does_not_hide_older_active_order():
    """Scan every matching aggregate; newest terminal cannot unlock older active."""
    conn = sqlite3.connect(":memory:")
    _seed_active_family_order(conn, aggregate_id="aggregate-active-old")
    _seed_active_family_order(conn, aggregate_id="aggregate-terminal-new")
    _insert_live_order_event(
        conn,
        aggregate_id="aggregate-terminal-new",
        sequence=3,
        event_type="SubmitRejected",
        payload={
            "event_id": "event-1",
            "final_intent_id": "intent-1",
            "reason": "venue_rejected",
        },
    )

    reason = _lock_reason(conn, limit_price=0.70)

    assert reason is not None
    assert "aggregate_id=aggregate-active-old" in reason


def test_fixA_terminal_cancel_releases_lock_for_rebid():
    """FIX A (#125): after the latest family order reaches a TERMINAL lifecycle event
    (a confirmed cancel/expiry/reconcile of an UNFILLED resting maker — the 900s
    timeout case), the lock RELEASES so the family re-bids at the fresh price."""
    conn = sqlite3.connect(":memory:")
    _seed_active_family_order(conn)
    # The resting maker times out unfilled at 900s -> reconcile records the terminal
    # closure (a CANCEL/expiry, no SubmitRejected, no 2c price move). Terminal.
    _insert_live_order_event(
        conn,
        aggregate_id="aggregate-1",
        sequence=3,
        event_type="Reconciled",
        payload={"event_id": "event-1", "final_intent_id": "intent-1"},
    )

    # Same price as the timed-out rest -> NOT suppressed (the old 0.02 gate would
    # have permanently blocked this exact re-bid). The family re-enters the pipeline.
    assert _lock_reason(conn, limit_price=0.70) is None


def test_fixA_terminal_venue_command_releases_stale_aggregate_lock():
    """A venue-command terminal state is canonical closure evidence even if the
    live-order aggregate missed its terminal release event."""
    conn = sqlite3.connect(":memory:")
    _seed_active_family_order(conn)
    conn.execute(
        """
        CREATE TABLE venue_commands (
            command_id TEXT,
            decision_id TEXT,
            state TEXT,
            created_at TEXT,
            updated_at TEXT
        )
        """
    )
    conn.execute(
        """
        INSERT INTO venue_commands (
            command_id, decision_id, state, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?)
        """,
        (
            "cmd-1",
            "command-1",
            "EXPIRED",
            "2026-06-17T20:22:02+00:00",
            "2026-06-17T20:33:19+00:00",
        ),
    )

    assert _lock_reason(conn, limit_price=0.70) is None


def test_fixA_terminal_venue_command_id_releases_stale_aggregate_lock():
    """Production command truth may key the row by command_id, not decision_id."""
    conn = sqlite3.connect(":memory:")
    _seed_active_family_order(conn)
    conn.execute(
        """
        CREATE TABLE venue_commands (
            command_id TEXT,
            decision_id TEXT,
            state TEXT,
            created_at TEXT,
            updated_at TEXT
        )
        """
    )
    conn.execute(
        """
        INSERT INTO venue_commands (
            command_id, decision_id, state, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?)
        """,
        (
            "command-1",
            "dec-other",
            "EXPIRED",
            "2026-06-17T20:22:02+00:00",
            "2026-06-17T20:33:19+00:00",
        ),
    )

    assert _lock_reason(conn, limit_price=0.70) is None


def test_fixA_terminal_venue_command_releases_lock_from_trade_db(monkeypatch):
    """Production shape: live-order aggregate is in world DB while venue_commands
    is owned by the trade DB. Terminal venue state must still release the lock."""
    from src.state import db as state_db

    world_conn = sqlite3.connect(":memory:")
    _seed_active_family_order(world_conn)
    trade_conn = sqlite3.connect(":memory:")
    trade_conn.execute(
        """
        CREATE TABLE venue_commands (
            command_id TEXT,
            decision_id TEXT,
            state TEXT,
            created_at TEXT,
            updated_at TEXT
        )
        """
    )
    trade_conn.execute(
        """
        INSERT INTO venue_commands (
            command_id, decision_id, state, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?)
        """,
        (
            "cmd-1",
            "command-1",
            "EXPIRED",
            "2026-06-17T20:22:02+00:00",
            "2026-06-17T20:33:19+00:00",
        ),
    )

    monkeypatch.setattr(state_db, "get_trade_connection_read_only", lambda: trade_conn)

    assert _lock_reason(world_conn, limit_price=0.70) is None


def test_fixA_terminal_venue_order_id_releases_lock_from_trade_db(monkeypatch):
    """Production cancel shape: EDLI aggregate ids and venue command ids can differ,
    so terminal closure must also match the canonical venue_order_id."""
    from src.state import db as state_db

    world_conn = sqlite3.connect(":memory:")
    _seed_active_family_order(world_conn)
    _insert_live_order_event(
        world_conn,
        aggregate_id="aggregate-1",
        sequence=3,
        event_type="VenueSubmitAcknowledged",
        payload={
            "event_id": "event-1",
            "final_intent_id": "intent-1",
            "venue_order_id": "0xterminalvenue",
        },
    )
    trade_conn = sqlite3.connect(":memory:")
    trade_conn.execute(
        """
        CREATE TABLE venue_commands (
            command_id TEXT,
            decision_id TEXT,
            venue_order_id TEXT,
            state TEXT,
            created_at TEXT,
            updated_at TEXT
        )
        """
    )
    trade_conn.execute(
        """
        INSERT INTO venue_commands (
            command_id, decision_id, venue_order_id, state, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            "short-command-id",
            "unrelated-decision-id",
            "0xterminalvenue",
            "CANCELLED",
            "2026-06-25T19:10:39+00:00",
            "2026-06-25T19:32:38+00:00",
        ),
    )

    monkeypatch.setattr(state_db, "get_trade_connection_read_only", lambda: trade_conn)

    assert _lock_reason(world_conn, limit_price=0.70) is None


def test_fixA_active_venue_order_id_does_not_release_lock_from_trade_db(monkeypatch):
    """A venue_order_id match only releases on terminal command state, not OPEN."""
    from src.state import db as state_db

    world_conn = sqlite3.connect(":memory:")
    _seed_active_family_order(world_conn)
    _insert_live_order_event(
        world_conn,
        aggregate_id="aggregate-1",
        sequence=3,
        event_type="VenueSubmitAcknowledged",
        payload={
            "event_id": "event-1",
            "final_intent_id": "intent-1",
            "venue_order_id": "0xopenvenue",
        },
    )
    trade_conn = sqlite3.connect(":memory:")
    trade_conn.execute(
        """
        CREATE TABLE venue_commands (
            command_id TEXT,
            decision_id TEXT,
            venue_order_id TEXT,
            state TEXT,
            created_at TEXT,
            updated_at TEXT
        )
        """
    )
    trade_conn.execute(
        """
        INSERT INTO venue_commands (
            command_id, decision_id, venue_order_id, state, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            "short-command-id",
            "unrelated-decision-id",
            "0xopenvenue",
            "OPEN",
            "2026-06-25T19:10:39+00:00",
            "2026-06-25T19:10:47+00:00",
        ),
    )

    monkeypatch.setattr(state_db, "get_trade_connection_read_only", lambda: trade_conn)

    reason = _lock_reason(world_conn, limit_price=0.70)
    assert reason is not None
    assert reason.startswith("EDLI_LIVE_ORDER_ACTIVE_DUPLICATE_SUPPRESSED")


def test_fixA_unknown_indeterminate_state_fails_closed_suppresses():
    """FIX A (#125) fail-closed: a family order that exists but carries NO terminal
    marker (state UNKNOWN/indeterminate) is treated as ACTIVE — suppress, never risk
    a double-submit on an order that might still be live on the venue."""
    conn = sqlite3.connect(":memory:")
    # Only a SubmitPlanBuilt: planned/in-flight, no terminal event -> active/unknown.
    _insert_live_order_event(
        conn,
        aggregate_id="aggregate-1",
        sequence=1,
        event_type="SubmitPlanBuilt",
        payload={
            "event_id": "event-1",
            "final_intent_id": "intent-1",
            "condition_id": "condition-1",
            "token_id": "token-no-1",
            "direction": "buy_no",
            "limit_price": 0.70,
        },
    )

    assert _lock_reason(conn, limit_price=0.70) is not None


def test_fixA_no_prior_order_does_not_suppress():
    """FIX A (#125): a family that has never planned an order is free to submit."""
    conn = sqlite3.connect(":memory:")
    from src.state.schema.edli_live_order_events_schema import ensure_tables

    ensure_tables(conn)
    assert _lock_reason(conn, limit_price=0.70) is None


def test_fixA_cap_transitioned_pending_reconcile_suppresses_not_terminal():
    """FIX A (#125) DEFECT REGRESSION: CapTransitioned(to_status=PENDING_RECONCILE)
    is NOT a terminal lifecycle event.

    A CapTransitioned with to_status=PENDING_RECONCILE is emitted alongside a
    SubmitUnknown event on a submit TIMEOUT_UNKNOWN / POST_SUBMIT_UNKNOWN.  At that
    point the cap is still RESERVED and the order MAY still be resting live on the
    venue.  The projection classifies this NON-terminal (current_state=PENDING_RECONCILE,
    pending_reconcile=True).  The lock MUST suppress (return not-None) — treating it
    as TERMINAL would release the dedup lock on a potentially live order, enabling a
    double-submit.

    The previously-buggy code treated bare presence of ANY CapTransitioned event as
    terminal → RELEASE.  This test MUST:
      - FAIL (RED) against the old bare-event-type-set code.
      - PASS (GREEN) after the payload-inspecting fix.
    """
    conn = sqlite3.connect(":memory:")
    _seed_active_family_order(conn)  # SubmitPlanBuilt + ExecutionCommandCreated

    # Submit timeout → SubmitUnknown then CapTransitioned(to_status=PENDING_RECONCILE)
    _insert_live_order_event(
        conn,
        aggregate_id="aggregate-1",
        sequence=3,
        event_type="SubmitUnknown",
        payload={"event_id": "event-1", "final_intent_id": "intent-1"},
    )
    _insert_live_order_event(
        conn,
        aggregate_id="aggregate-1",
        sequence=4,
        event_type="CapTransitioned",
        payload={
            "event_id": "event-1",
            "final_intent_id": "intent-1",
            "to_status": "PENDING_RECONCILE",
        },
    )

    reason = _lock_reason(conn, limit_price=0.70)

    # MUST suppress — the order may still be resting live; releasing here risks a
    # double-submit.  A correct fix returns a non-None suppress reason.
    assert reason is not None, (
        "CapTransitioned(to_status=PENDING_RECONCILE) must NOT release the live-order "
        "dedup lock — the order may still be resting on the venue.  "
        f"Got: {reason!r}"
    )
    assert "SUPPRESSED" in reason or "FAIL_CLOSED" in reason, (
        f"Suppress reason should contain 'SUPPRESSED' or 'FAIL_CLOSED', got: {reason!r}"
    )


def test_fixA_cap_transitioned_consumed_suppresses_resting_live_order():
    """FIX A (#125) DEFECT REGRESSION (live-money 2026-06-16):
    CapTransitioned(to_status=CONSUMED) is NOT terminal.

    CONSUMED is emitted the instant a submit SUCCEEDS (status=SUBMITTED) and the
    order is RESTING LIVE on the venue — the cap is COMMITTED to that resting
    order.  There is NO fill at this point; a fill is a later, separate
    UserTradeObserved event.  Treating CONSUMED as terminal made EVERY successful
    resting order instantly release its own duplicate lock, so a re-decision of
    the same family (same token/direction, same or worse price) placed a SECOND
    concurrent resting ENTRY order -> ~2x intended exposure.

    Live evidence: buy_no token
    35015396764119764057109967922516391182815114821189461579432074152958132060729
    rested TWICE @0.570 (18:40:20 then 18:47:19) because the first aggregate's
    CapTransitioned(CONSUMED) released the lock for the second event.

    A CONSUMED-resting order is ACTIVE -> the lock MUST SUPPRESS (return
    not-None).  This test:
      - FAILS (RED) against the buggy `IN ('CONSUMED', 'RELEASED')` terminal set.
      - PASSES (GREEN) after CONSUMED is dropped from the terminal set.
    """
    conn = sqlite3.connect(":memory:")
    _seed_active_family_order(conn)

    # Submit SUCCESS -> order rests live -> CapTransitioned(to_status=CONSUMED).
    # No UserTradeObserved (no fill).  The order is resting, ACTIVE.
    _insert_live_order_event(
        conn,
        aggregate_id="aggregate-1",
        sequence=3,
        event_type="VenueSubmitAcknowledged",
        payload={
            "event_id": "event-1",
            "final_intent_id": "intent-1",
            "venue_order_id": "0x72aee028",
        },
    )
    _insert_live_order_event(
        conn,
        aggregate_id="aggregate-1",
        sequence=4,
        event_type="CapTransitioned",
        payload={
            "event_id": "event-1",
            "final_intent_id": "intent-1",
            "to_status": "CONSUMED",
        },
    )

    # Re-decision at the SAME price (0.70) as the resting order: must HOLD.
    same_price = _lock_reason(conn, limit_price=0.70)
    assert same_price is not None, (
        "CapTransitioned(to_status=CONSUMED) is a RESTING-LIVE order, NOT a fill "
        "and NOT terminal — the lock MUST suppress the duplicate ENTRY.  "
        f"Got: {same_price!r}"
    )
    assert "SUPPRESSED" in same_price or "FAIL_CLOSED" in same_price, (
        f"Suppress reason should contain 'SUPPRESSED' or 'FAIL_CLOSED', got: {same_price!r}"
    )

    # A re-decision at a WORSE (higher buy) price must ALSO hold — never two
    # concurrent resting ENTRY orders on the same family-side, regardless of price.
    worse_price = _lock_reason(conn, limit_price=0.72)
    assert worse_price is not None, (
        "A non-improving re-bid against a CONSUMED resting order must suppress.  "
        f"Got: {worse_price!r}"
    )


def test_fixA_cap_transitioned_consumed_then_fill_releases_lock():
    """FIX A (#125): once a CONSUMED-resting order actually FILLS, the later
    UserTradeObserved IS terminal and releases the lock — so a genuinely closed
    (filled) order does not lock the family forever.  This guards the fix from
    over-suppressing: CONSUMED-resting suppresses, CONSUMED+fill releases."""
    conn = sqlite3.connect(":memory:")
    _seed_active_family_order(conn)

    _insert_live_order_event(
        conn,
        aggregate_id="aggregate-1",
        sequence=3,
        event_type="CapTransitioned",
        payload={
            "event_id": "event-1",
            "final_intent_id": "intent-1",
            "to_status": "CONSUMED",
        },
    )
    _insert_live_order_event(
        conn,
        aggregate_id="aggregate-1",
        sequence=4,
        event_type="UserTradeObserved",
        payload={
            "event_id": "event-1",
            "final_intent_id": "intent-1",
            "fill_authority_state": "FILL_CONFIRMED",
        },
    )

    assert _lock_reason(conn, limit_price=0.70) is None, (
        "A CONSUMED order that subsequently FILLED (UserTradeObserved) IS "
        "terminal — the lock must release."
    )


def test_fixA_cap_transitioned_released_releases_lock():
    """FIX A (#125): CapTransitioned(to_status=RELEASED) IS terminal — the cap was
    released without a position (reject / reconcile / submit-disabled)."""
    conn = sqlite3.connect(":memory:")
    _seed_active_family_order(conn)

    _insert_live_order_event(
        conn,
        aggregate_id="aggregate-1",
        sequence=3,
        event_type="CapTransitioned",
        payload={
            "event_id": "event-1",
            "final_intent_id": "intent-1",
            "to_status": "RELEASED",
        },
    )

    assert _lock_reason(conn, limit_price=0.70) is None, (
        "CapTransitioned(to_status=RELEASED) is terminal — lock must release."
    )


def test_fixA_reconciled_pending_reconcile_true_suppresses():
    """FIX A (#125) Reconciled(pending_reconcile=True) is NOT terminal for re-bid.
    Matched-pending-finality: the reconcile matched a pending order but finality
    is not confirmed.  Must SUPPRESS (fail-closed)."""
    conn = sqlite3.connect(":memory:")
    _seed_active_family_order(conn)

    _insert_live_order_event(
        conn,
        aggregate_id="aggregate-1",
        sequence=3,
        event_type="Reconciled",
        payload={
            "event_id": "event-1",
            "final_intent_id": "intent-1",
            "pending_reconcile": True,
        },
    )

    reason = _lock_reason(conn, limit_price=0.70)
    assert reason is not None, (
        "Reconciled(pending_reconcile=True) must NOT release the lock — "
        f"finality not confirmed.  Got: {reason!r}"
    )


def test_fixA_reconciled_pending_reconcile_false_releases():
    """FIX A (#125): Reconciled(pending_reconcile=False) IS terminal — fully settled."""
    conn = sqlite3.connect(":memory:")
    _seed_active_family_order(conn)

    _insert_live_order_event(
        conn,
        aggregate_id="aggregate-1",
        sequence=3,
        event_type="Reconciled",
        payload={
            "event_id": "event-1",
            "final_intent_id": "intent-1",
            "pending_reconcile": False,
        },
    )

    assert _lock_reason(conn, limit_price=0.70) is None, (
        "Reconciled(pending_reconcile=False) is terminal — lock must release."
    )


def test_selector_skips_locked_candidate_and_keeps_flowing_to_next_executable():
    from src.engine import event_reactor_adapter as adapter

    conn = sqlite3.connect(":memory:")
    _insert_live_order_event(
        conn,
        aggregate_id="aggregate-locked-best",
        sequence=1,
        event_type="SubmitPlanBuilt",
        payload={
            "event_id": "event-locked",
            "final_intent_id": "intent-locked",
            "condition_id": "condition-locked",
            "token_id": "token-locked",
            "direction": "buy_no",
            "limit_price": 0.40,
        },
    )
    _insert_live_order_event(
        conn,
        aggregate_id="aggregate-locked-best",
        sequence=2,
        event_type="ExecutionCommandCreated",
        payload={
            "event_id": "event-locked",
            "final_intent_id": "intent-locked",
            "execution_command_id": "command-locked",
        },
    )
    locked_best = _fake_candidate_proof(
        condition_id="condition-locked",
        token_id="token-locked",
        direction="buy_no",
        limit_price=0.70,
        trade_score=0.50,
        q_lcb_5pct=0.80,
    )
    next_best = _fake_candidate_proof(
        condition_id="condition-next",
        token_id="token-next",
        direction="buy_yes",
        limit_price=0.45,
        trade_score=0.20,
        q_lcb_5pct=0.60,
    )

    selected = adapter._selected_candidate_proof(
        {},
        (locked_best, next_best),
        locked_opportunity_conn=conn,
    )

    assert selected is next_best


def test_selector_skips_below_min_tick_candidate_and_keeps_flowing():
    from src.engine import event_reactor_adapter as adapter

    below_tick_best = _fake_candidate_proof(
        condition_id="condition-too-cheap",
        token_id="token-too-cheap",
        direction="buy_no",
        limit_price=0.004,
        trade_score=0.90,
        q_lcb_5pct=0.95,
        min_tick_size=0.01,
    )
    next_tradeable = _fake_candidate_proof(
        condition_id="condition-next",
        token_id="token-next",
        direction="buy_yes",
        limit_price=0.45,
        trade_score=0.20,
        q_lcb_5pct=0.60,
        min_tick_size=0.01,
    )

    selected = adapter._selected_candidate_proof(
        {},
        (below_tick_best, next_tradeable),
    )

    assert selected is next_tradeable


def test_candidate_below_min_tick_has_explicit_untradeable_reason():
    from src.engine import event_reactor_adapter as adapter

    below_tick = _fake_candidate_proof(
        condition_id="condition-too-cheap",
        token_id="token-too-cheap",
        direction="buy_no",
        limit_price=0.004,
        trade_score=0.90,
        q_lcb_5pct=0.95,
        min_tick_size=0.01,
    )

    reason = adapter._candidate_limit_price_untradeable_reason(below_tick)

    assert reason == "EXECUTION_PRICE_BELOW_MIN_TICK:limit_price=0.004:min_tick_size=0.01"


def test_selector_does_not_fall_back_to_locked_candidate_when_all_executable_locked():
    from src.engine import event_reactor_adapter as adapter

    conn = sqlite3.connect(":memory:")
    _insert_live_order_event(
        conn,
        aggregate_id="aggregate-locked-only",
        sequence=1,
        event_type="SubmitPlanBuilt",
        payload={
            "event_id": "event-locked",
            "final_intent_id": "intent-locked",
            "condition_id": "condition-locked",
            "token_id": "token-locked",
            "direction": "buy_no",
            "limit_price": 0.40,
        },
    )
    _insert_live_order_event(
        conn,
        aggregate_id="aggregate-locked-only",
        sequence=2,
        event_type="ExecutionCommandCreated",
        payload={
            "event_id": "event-locked",
            "final_intent_id": "intent-locked",
            "execution_command_id": "command-locked",
        },
    )
    locked = _fake_candidate_proof(
        condition_id="condition-locked",
        token_id="token-locked",
        direction="buy_no",
        limit_price=0.70,
        trade_score=0.50,
        q_lcb_5pct=0.80,
    )

    selected = adapter._selected_candidate_proof(
        {},
        (locked,),
        locked_opportunity_conn=conn,
    )

    assert selected is None


def test_fixA_terminal_prior_order_does_not_block_redecision_same_price(monkeypatch):
    """FIX A (#125): a prior order that reached a TERMINAL state (here the
    SUBMIT_DISABLED path ends in CapTransitioned RELEASED) must NOT block a fresh
    same-family, same-price re-decision. The retired historical-command lock
    suppressed this as 'no price improvement'; the live-order-state lock RELEASES
    once the prior order is terminal, so the family re-enters the pipeline and the
    second cycle builds its own full live-order aggregate."""
    from src.engine import event_reactor_adapter as adapter
    from src.riskguard.risk_level import RiskLevel
    from tests.decision_kernel.no_submit_fixtures import build_test_no_submit_proof_bundle

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    event_1 = _forecast_event()
    event_2 = replace(event_1, event_id="event-2", entity_key="Chicago|2026-05-24|high|live-canary-test-redecision")

    def _accepted_for_event(event, *_args, **kwargs):
        decision_time = kwargs["decision_time"]
        accepted = _accepted_receipt(event)
        return replace(
            accepted,
            decision_proof_bundle=build_test_no_submit_proof_bundle(
                event,
                accepted,
                decision_time=decision_time,
            ),
        )

    monkeypatch.setattr(adapter, "build_event_bound_no_submit_receipt", _accepted_for_event)
    submit = adapter.event_bound_live_adapter_from_trade_conn(
        conn,
        live_cap_conn=conn,
        get_current_level=lambda: RiskLevel.GREEN,
        real_order_submit_enabled=False,
        pre_submit_authority_provider=_pre_submit_authority_provider,
    )

    first = submit(event_1, datetime(2026, 5, 24, 18, 10, tzinfo=timezone.utc))
    event_count_after_first = _table_count(conn, "edli_live_order_events")
    second = submit(event_2, datetime(2026, 5, 24, 18, 11, tzinfo=timezone.utc))

    assert first.side_effect_status == "SUBMIT_DISABLED"
    assert event_count_after_first == 6
    # The prior aggregate is terminal (CapTransitioned RELEASED). The same-price
    # re-decision is NOT suppressed: it builds its own SUBMIT_DISABLED aggregate.
    assert second.side_effect_status == "SUBMIT_DISABLED"
    assert _table_count(conn, "edli_live_order_events") == 2 * event_count_after_first


def test_live_build_failure_rolls_back_partial_live_order_aggregate(monkeypatch):
    from src.engine import event_reactor_adapter as adapter
    from src.events.live_cap import LiveCapLedger
    from src.riskguard.risk_level import RiskLevel
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
    monkeypatch.setattr(adapter, "build_event_bound_no_submit_receipt", lambda *_args, **_kwargs: accepted)
    monkeypatch.setattr(
        LiveCapLedger,
        "reserve",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("forced reserve failure")),
    )

    submit = adapter.event_bound_live_adapter_from_trade_conn(
        conn,
        live_cap_conn=conn,
        get_current_level=lambda: RiskLevel.GREEN,
        real_order_submit_enabled=False,
        pre_submit_authority_provider=_pre_submit_authority_provider,
    )

    receipt = submit(event, decision_time)

    assert receipt.proof_accepted is False
    assert "forced reserve failure" in receipt.reason
    assert _table_count(conn, "edli_live_order_events") == 0
    assert _table_count(conn, "edli_live_order_projection") == 0
    assert _table_count(conn, "edli_live_cap_usage") == 0


def test_live_certificate_build_failure_preserves_selected_leg_on_receipt(monkeypatch, tmp_path):
    from src.engine import event_reactor_adapter as adapter
    from src.riskguard.risk_level import RiskLevel
    from tests.decision_kernel.no_submit_fixtures import build_test_no_submit_proof_bundle

    event = _forecast_event()
    _install_unpaused_world_control_db(monkeypatch, tmp_path)
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    decision_time = datetime(2026, 5, 24, 18, 10, tzinfo=timezone.utc)
    accepted = _accepted_receipt(event)
    accepted = replace(
        accepted,
        decision_proof_bundle=build_test_no_submit_proof_bundle(event, accepted, decision_time=decision_time),
    )
    monkeypatch.setattr(adapter, "build_event_bound_no_submit_receipt", lambda *_args, **_kwargs: accepted)

    def _raise_build_failure(**_kwargs):
        raise ValueError("PRE_SUBMIT_BOOK_AUTHORITY_MISSING")

    monkeypatch.setattr(adapter, "_build_live_execution_command_certificates", _raise_build_failure)
    submit = adapter.event_bound_live_adapter_from_trade_conn(
        conn,
        live_cap_conn=conn,
        get_current_level=lambda: RiskLevel.GREEN,
        real_order_submit_enabled=True,
        durable_submit_outbox_enabled=True,
        executor_submit=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("executor must not be called after certificate build failure")
        ),
        operator_arm=_operator_arm(),
        pre_submit_authority_provider=_pre_submit_authority_provider,
        entry_live_health_authority_provider=_healthy_entry_live_health_provider(decision_time),
    )

    receipt = submit(event, decision_time)

    assert receipt.proof_accepted is False
    assert receipt.side_effect_status == "NO_SUBMIT"
    assert receipt.reason == "EDLI_LIVE_CERTIFICATE_BUILD_FAILED:PRE_SUBMIT_BOOK_AUTHORITY_MISSING"
    assert receipt.token_id == accepted.token_id
    assert receipt.condition_id == accepted.condition_id
    assert receipt.bin_label == accepted.bin_label
    assert receipt.direction == accepted.direction
    assert receipt.q_live == accepted.q_live
    assert receipt.c_fee_adjusted == accepted.c_fee_adjusted


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
            live_cap_conn=conn,
        )


def test_live_execution_command_day0_actionable_has_observation_source_parents():
    from src.decision_kernel import claims
    from src.engine import event_reactor_adapter as adapter
    from tests.decision_kernel.no_submit_fixtures import build_test_no_submit_proof_bundle

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    event = _day0_event()
    decision_time = datetime(2026, 5, 24, 18, 10, tzinfo=timezone.utc)
    accepted = _accepted_receipt(
        event,
        execution_mode_intent="MAKER",
        maker_limit_price=0.38,
        rest_then_cross_policy="REST_DEFAULT",
    )
    accepted = replace(
        accepted,
        decision_proof_bundle=build_test_no_submit_proof_bundle(
            event,
            accepted,
            decision_time=decision_time,
        ),
    )

    certificates = adapter._build_live_execution_command_certificates(
        event=event,
        receipt=accepted,
        decision_time=decision_time,
        live_cap_conn=conn,
        pre_submit_authority_provider=lambda *_args: _pre_submit_authority_witness(
            current_best_bid=0.39,
            current_best_ask=0.41,
        ),
    )
    actionable = _required_cert(certificates, claims.ACTIONABLE_TRADE)
    parent_types = {edge.certificate_type for edge in actionable.header.parent_edges}

    assert claims.DAY0_AUTHORITY in parent_types
    assert claims.ABSORBING_BOUNDARY in parent_types
    assert _required_cert(certificates, claims.DAY0_AUTHORITY).payload["authority"] == (
        "DAY0_LIVE_OBSERVATION_HARD_FACT"
    )
    assert _required_cert(certificates, claims.ABSORBING_BOUNDARY).payload["boundary"] == (
        "day0_absorbing_hard_fact"
    )


def test_raw_event_bound_receipt_hydrates_live_quality_floors():
    from src.engine import event_reactor_adapter as adapter

    event = _forecast_event()
    raw_receipt = {
        "schema": "edli_event_bound_no_submit_v1",
        "side_effect_status": "NO_SUBMIT",
        "submitted": False,
        "proof_accepted": True,
        "event_id": event.event_id,
        "causal_snapshot_id": event.causal_snapshot_id,
        "condition_id": "condition-1",
        "token_id": "yes-1",
        "candidate_id": "candidate-1",
        "executable_snapshot_id": "exec-1",
        "family_id": "family-1",
        "direction": "buy_yes",
        "strategy_key": "center_buy",
        "q_live": 0.7,
        "q_lcb_5pct": 0.6,
        "c_fee_adjusted": 0.4,
        "c_cost_95pct": 0.45,
        "p_fill_lcb": 0.1,
        "trade_score": 0.2,
        "min_expected_profit_usd": 0.05,
        "min_submit_edge_density": 0.02,
    }

    receipt = adapter._event_submission_receipt_from_typed_receipt_payload(raw_receipt, event)

    assert receipt.min_expected_profit_usd == pytest.approx(0.05)
    assert receipt.min_submit_edge_density == pytest.approx(0.02)


def test_live_execution_command_preserves_quality_floors_to_pre_submit():
    from src.decision_kernel import claims
    from src.engine import event_reactor_adapter as adapter
    from tests.decision_kernel.no_submit_fixtures import build_test_no_submit_proof_bundle

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    event = _forecast_event()
    decision_time = datetime(2026, 5, 24, 18, 10, tzinfo=timezone.utc)
    qkernel_cert = _qkernel_execution_cert(
        bin_id="bin-1",
        candidate_id="DIRECT_YES:bin-1",
        route_id="DIRECT_YES:bin-1@proof",
        payoff_q_point=0.7,
        payoff_q_lcb=0.6,
        cost=0.4,
    )
    accepted = replace(
        _accepted_receipt(event),
        selection_authority_applied="qkernel_spine",
        candidate_bin_id="bin-1",
        qkernel_execution_economics=qkernel_cert,
        opportunity_book=_opportunity_book_with_qkernel_cert(qkernel_cert),
    )
    accepted = replace(
        accepted,
        decision_proof_bundle=build_test_no_submit_proof_bundle(event, accepted, decision_time=decision_time),
    )

    certificates = adapter._build_live_execution_command_certificates(
        event=event,
        receipt=accepted,
        decision_time=decision_time,
        live_cap_conn=conn,
        pre_submit_authority_provider=_pre_submit_authority_provider,
    )
    pre_submit = next(cert for cert in certificates if cert.certificate_type == claims.PRE_SUBMIT_REVALIDATION)

    assert pre_submit.payload["min_expected_profit_usd"] == pytest.approx(0.25)
    assert pre_submit.payload["min_submit_edge_density"] == pytest.approx(0.05)


def test_live_execution_command_blocks_identity_fallback_calibration():
    from dataclasses import replace

    from src.engine import event_reactor_adapter as adapter
    from tests.decision_kernel.no_submit_fixtures import build_test_no_submit_proof_bundle

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    event = _forecast_event()
    decision_time = datetime(2026, 5, 24, 18, 10, tzinfo=timezone.utc)
    accepted = _accepted_receipt(event)
    proof_bundle = build_test_no_submit_proof_bundle(event, accepted, decision_time=decision_time)
    proof_bundle = replace(
        proof_bundle,
        calibration=replace(
            proof_bundle.calibration,
            payload={
                **proof_bundle.calibration.payload,
                "authority": "IDENTITY_FALLBACK_NO_PLATT_BUCKET",
                "n_samples": 0,
            },
        ),
    )
    accepted = replace(accepted, decision_proof_bundle=proof_bundle)

    with pytest.raises(ValueError, match="EDLI_LIVE_CALIBRATION_AUTHORITY_BLOCKED"):
        adapter._build_live_execution_command_certificates(
            event=event,
            receipt=accepted,
            decision_time=decision_time,
            live_cap_conn=conn,
            pre_submit_authority_provider=_pre_submit_authority_provider,
        )


def test_live_execution_command_requires_q_source_provenance():
    from dataclasses import replace

    from src.engine import event_reactor_adapter as adapter

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    event = _forecast_event()
    decision_time = datetime(2026, 5, 24, 18, 10, tzinfo=timezone.utc)
    accepted = replace(_accepted_receipt(event), q_source=None)

    with pytest.raises(ValueError, match="EDLI_LIVE_Q_SOURCE_MISSING"):
        adapter._build_live_execution_command_certificates(
            event=event,
            receipt=accepted,
            decision_time=decision_time,
            live_cap_conn=conn,
            pre_submit_authority_provider=_pre_submit_authority_provider,
        )


def test_actionable_payload_persists_live_authority_provenance():
    from types import SimpleNamespace

    from src.engine import event_reactor_adapter as adapter

    event = _forecast_event()
    receipt = _accepted_receipt(event)
    live_cap = SimpleNamespace(
        payload={
            "usage_id": "usage-1",
            "reserved_notional_usd": 3.0,
            "notional_cap_enabled": False,
        }
    )

    payload = adapter._actionable_payload_from_receipt(receipt, live_cap, event=event)

    assert payload["q_source"] == "emos"
    assert payload["opportunity_book"] == receipt.opportunity_book
    assert payload["strategy_key"] == "center_buy"


def test_actionable_payload_backfills_missing_receipt_quality_floors():
    from dataclasses import replace
    from types import SimpleNamespace

    from src.engine import event_reactor_adapter as adapter

    event = _forecast_event()
    receipt = replace(
        _accepted_receipt(event),
        min_entry_price=None,
        min_expected_profit_usd=None,
        min_submit_edge_density=None,
    )
    live_cap = SimpleNamespace(
        payload={
            "usage_id": "usage-1",
            "reserved_notional_usd": 3.0,
            "notional_cap_enabled": False,
        }
    )

    payload = adapter._actionable_payload_from_receipt(receipt, live_cap, event=event)
    floors = adapter._event_bound_strategy_live_quality_floors("center_buy")

    assert payload["min_entry_price"] == pytest.approx(floors["min_entry_price"])
    assert payload["min_expected_profit_usd"] == pytest.approx(floors["min_expected_profit_usd"])
    assert payload["min_submit_edge_density"] == pytest.approx(floors["min_submit_edge_density"])


def test_actionable_payload_persists_qkernel_execution_economics():
    from src.engine import event_reactor_adapter as adapter

    event = _forecast_event()
    qkernel_cert = _qkernel_execution_cert(
        payoff_q_point=0.7,
        payoff_q_lcb=0.6,
        cost=0.4,
    )
    receipt = replace(
        _accepted_receipt(event),
        q_source="qkernel_spine",
        qkernel_execution_economics=qkernel_cert,
    )
    live_cap = SimpleNamespace(
        payload={
            "usage_id": "usage-1",
            "reserved_notional_usd": 15.39,
            "notional_cap_enabled": False,
        }
    )

    payload = adapter._actionable_payload_from_receipt(receipt, live_cap, event=event)

    assert payload["q_source"] == "qkernel_spine"
    assert payload["qkernel_execution_economics"] == qkernel_cert


def test_day0_actionable_payload_reads_authority_from_event_payload_json():
    from src.engine import event_reactor_adapter as adapter
    from src.events.opportunity_event import (
        Day0ExtremeUpdatedPayload,
        make_day0_extreme_updated_event,
    )

    event = make_day0_extreme_updated_event(
        entity_key="Chicago|2026-05-24|high|day0",
        source="observation_instants",
        observed_at="2026-05-24T19:00:00+00:00",
        received_at="2026-05-24T19:02:00+00:00",
        payload=Day0ExtremeUpdatedPayload(
            city="Chicago",
            target_date="2026-05-24",
            metric="high",
            settlement_source="WU",
            station_id="KMDW",
            observation_time="2026-05-24T19:00:00+00:00",
            observation_available_at="2026-05-24T19:01:00+00:00",
            raw_value=80.4,
            rounded_value=80,
            high_so_far=80.4,
            source_match_status="MATCH",
            local_date_status="MATCH",
            station_match_status="MATCH",
            dst_status="UNAMBIGUOUS",
            metric_match_status="MATCH",
            rounding_status="MATCH",
            source_authorized_status="AUTHORIZED",
            live_authority_status="live",
        ),
    )
    receipt = replace(
        _accepted_receipt(event),
        q_source="qkernel_spine",
        selection_authority_applied="qkernel_spine",
    )
    live_cap = SimpleNamespace(
        payload={
            "usage_id": "usage-1",
            "reserved_notional_usd": 15.39,
            "notional_cap_enabled": False,
        }
    )

    payload = adapter._actionable_payload_from_receipt(receipt, live_cap, event=event)

    assert payload["event_type"] == "DAY0_EXTREME_UPDATED"
    assert payload["source_match_status"] == "MATCH"
    assert payload["local_date_status"] == "MATCH"
    assert payload["station_match_status"] == "MATCH"
    assert payload["dst_status"] == "UNAMBIGUOUS"
    assert payload["metric_match_status"] == "MATCH"
    assert payload["rounding_status"] == "MATCH"
    assert payload["source_authorized_status"] == "AUTHORIZED"
    assert payload["live_authority_status"] == "live"
    assert payload["selection_authority_applied"] == "qkernel_spine"
    assert payload["qkernel_execution_economics"]["source"] == "qkernel_spine"
    adapter._assert_live_entry_submit_authority(payload)


def test_day0_actionable_payload_preserves_zero_observation_values():
    from src.engine import event_reactor_adapter as adapter

    event = _day0_event()
    event_payload = json.loads(event.payload_json)
    event_payload.update(
        {
            "raw_value": 0.0,
            "rounded_value": 0,
            "high_so_far": 0.0,
        }
    )
    event = replace(event, payload_json=json.dumps(event_payload, sort_keys=True))
    receipt = replace(
        _accepted_receipt(event),
        q_source="qkernel_spine",
        selection_authority_applied="qkernel_spine",
        day0_probability_authority={
            "observed_extreme_native": 20.0,
            "rounded_value": 20,
            "observation_time": "2026-05-24T19:00:00+00:00",
            "observation_available_at": "2026-05-24T19:01:00+00:00",
        },
    )
    live_cap = SimpleNamespace(
        payload={
            "usage_id": "usage-1",
            "reserved_notional_usd": 15.39,
            "notional_cap_enabled": False,
        }
    )

    payload = adapter._actionable_payload_from_receipt(receipt, live_cap, event=event)

    assert payload["raw_value"] == pytest.approx(0.0)
    assert payload["rounded_value"] == 0


def test_actionable_payload_drops_nonfinite_qkernel_economics_before_cert_boundary():
    from src.engine import event_reactor_adapter as adapter

    event = _forecast_event()
    bad_cert = _qkernel_execution_cert(
        payoff_q_point=float("nan"),
        payoff_q_lcb=0.6,
        cost=0.4,
    )
    receipt = replace(
        _accepted_receipt(event),
        q_source="replacement_0_1",
        qkernel_execution_economics=bad_cert,
    )
    live_cap = SimpleNamespace(
        payload={
            "usage_id": "usage-1",
            "reserved_notional_usd": 15.39,
            "notional_cap_enabled": False,
        }
    )

    payload = adapter._actionable_payload_from_receipt(receipt, live_cap, event=event)

    assert payload["selection_authority_applied"] == "qkernel_spine"
    assert payload["qkernel_execution_economics"] is None
    with pytest.raises(ValueError, match="LIVE_ENTRY_QKERNEL_EXECUTION_ECONOMICS_REQUIRED"):
        adapter._assert_live_entry_submit_authority(payload)


@pytest.mark.parametrize(
    "bad_cert",
    [
        None,
        {},
        {"source": "qkernel_spine"},
        {
            "source": "qkernel_spine",
            "candidate_id": "DIRECT_NO:bin-1",
            "route_id": "DIRECT_NO:bin-1@proof",
            "payoff_q_lcb": 0.72,
            "edge_lcb": 0.17,
            "delta_u_at_min": 0.01,
            "optimal_stake_usd": "15.39",
            "optimal_delta_u": 0.03,
            "cost": 0.55,
            "side": "NO",
        },
    ],
)
def test_live_execution_command_requires_qkernel_execution_economics_for_qkernel_receipt(bad_cert):
    from src.engine import event_reactor_adapter as adapter

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    event = _forecast_event()
    decision_time = datetime(2026, 5, 24, 18, 10, tzinfo=timezone.utc)
    accepted = replace(
        _accepted_receipt(event),
        q_source="qkernel_spine",
        qkernel_execution_economics=bad_cert,
        opportunity_book=_opportunity_book_with_qkernel_cert(bad_cert),
    )

    with pytest.raises(ValueError, match="EDLI_LIVE_QKERNEL_.*INVALID"):
        adapter._build_live_execution_command_certificates(
            event=event,
            receipt=accepted,
            decision_time=decision_time,
            live_cap_conn=conn,
            pre_submit_authority_provider=_pre_submit_authority_provider,
        )


def test_live_execution_command_requires_qkernel_book_certificate_match():
    from src.engine import event_reactor_adapter as adapter

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    event = _forecast_event()
    decision_time = datetime(2026, 5, 24, 18, 10, tzinfo=timezone.utc)
    receipt_cert = _qkernel_execution_cert(payoff_q_lcb=0.72)
    book_cert = _qkernel_execution_cert(
        payoff_q_point=0.73,
        payoff_q_lcb=0.73,
    )
    accepted = replace(
        _accepted_receipt(event),
        q_source="qkernel_spine",
        qkernel_execution_economics=receipt_cert,
        opportunity_book=_opportunity_book_with_qkernel_cert(book_cert),
    )

    with pytest.raises(ValueError, match="EDLI_LIVE_QKERNEL_EXECUTION_ECONOMICS_MISMATCH"):
        adapter._build_live_execution_command_certificates(
            event=event,
            receipt=accepted,
            decision_time=decision_time,
            live_cap_conn=conn,
            pre_submit_authority_provider=_pre_submit_authority_provider,
        )


def test_live_execution_command_rejects_qkernel_selected_book_rejection():
    from src.engine import event_reactor_adapter as adapter

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    event = _forecast_event()
    decision_time = datetime(2026, 5, 24, 18, 10, tzinfo=timezone.utc)
    qkernel_cert = _qkernel_execution_cert(
        payoff_q_point=0.8144224142386236,
        payoff_q_lcb=0.7042980463451396,
        cost=0.62,
        side="YES",
    )
    book = _opportunity_book_with_qkernel_cert(qkernel_cert)
    book["candidates"][0].update(
        {
            "missing_reason": (
                "QKERNEL_REST_THEN_CROSS_NOT_ACTIONABLE:"
                "policy=MAKER_TAKER_FORBIDDEN"
            ),
            "passed_prefilter": False,
            "trade_score": 0.0,
        }
    )
    accepted = replace(
        _accepted_receipt(event),
        q_source="qkernel_spine",
        q_live=0.8144224142386236,
        q_lcb_5pct=0.7042980463451396,
        c_fee_adjusted=0.62,
        trade_score=0.0842980463451396,
        qkernel_execution_economics=qkernel_cert,
        opportunity_book=book,
    )

    with pytest.raises(
        ValueError,
        match="EDLI_LIVE_QKERNEL_SELECTED_BOOK_CANDIDATE_REJECTED",
    ):
        adapter._build_live_execution_command_certificates(
            event=event,
            receipt=accepted,
            decision_time=decision_time,
            live_cap_conn=conn,
            pre_submit_authority_provider=_pre_submit_authority_provider,
        )


@pytest.mark.parametrize(
    "override",
    [
        {"direction_law_ok": False},
        {"coherence_allows": False},
    ],
)
def test_live_execution_command_rejects_qkernel_direction_or_coherence_failure(override):
    from src.engine import event_reactor_adapter as adapter
    from tests.decision_kernel.no_submit_fixtures import build_test_no_submit_proof_bundle

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    event = _forecast_event()
    decision_time = datetime(2026, 5, 24, 18, 10, tzinfo=timezone.utc)
    qkernel_cert = _qkernel_execution_cert(
        bin_id="bin-1",
        candidate_id="DIRECT_YES:bin-1",
        route_id="DIRECT_YES:bin-1@proof",
        payoff_q_point=0.7,
        payoff_q_lcb=0.6,
        cost=0.4,
        **override,
    )
    accepted = replace(
        _accepted_receipt(event),
        q_source="qkernel_spine",
        qkernel_execution_economics=qkernel_cert,
        opportunity_book=_opportunity_book_with_qkernel_cert(qkernel_cert),
    )
    accepted = replace(
        accepted,
        decision_proof_bundle=build_test_no_submit_proof_bundle(event, accepted, decision_time=decision_time),
    )

    with pytest.raises(ValueError, match="EDLI_LIVE_QKERNEL_.*INVALID"):
        adapter._build_live_execution_command_certificates(
            event=event,
            receipt=accepted,
            decision_time=decision_time,
            live_cap_conn=conn,
            pre_submit_authority_provider=_pre_submit_authority_provider,
        )


def test_qkernel_taker_quality_uses_guarded_payoff_lcb_not_receipt_q_lcb():
    from src.engine import event_reactor_adapter as adapter

    proof = adapter._build_event_bound_taker_quality_proof(
        actionable_payload={
            "q_source": "qkernel_spine",
            # B3: the authority stamp (q_source) AND the selected-candidate identity
            # (candidate_id matching the cert) are now mandatory to reach the qkernel
            # payoff path — this test exercises that path, so it supplies both.
            "candidate_id": "DIRECT_YES:bin-1",
            "direction": "buy_yes",
            "q_live": 0.72,
            "q_lcb_5pct": 0.52,
            "qkernel_execution_economics": _qkernel_execution_cert(
                payoff_q_lcb=0.52,
                cost=0.20,
            ),
            "live_cap_reserved_notional_usd": 10.0,
            "proof_maker_limit_price": 0.45,
            "proof_ev_maker": 0.01,
        },
        order_mode="TAKER",
        fresh_best_bid=0.49,
        fresh_best_ask=0.55,
    )

    assert proof is not None
    assert proof["q_lcb_source"] == "qkernel_execution_economics.payoff_q_lcb"
    assert proof["passed"] is False
    assert float(proof["taker_fee_adjusted_edge"]) < 0.0


def _b3_payload(**overrides):
    """An ADMISSIBLE taker payload (positive after-cost edge) carrying a qkernel cert.

    Base: q_source stamped, candidate_id matching the cert, payoff_q_lcb 0.72 vs a
    fresh ask 0.50 -> a clearly POSITIVE edge so a CONSUMED cert yields passed=True.
    The B3 guard only changes WHETHER the cert is consumed (authority + identity);
    the surplus math is unchanged.
    """
    payload = {
        "q_source": "qkernel_spine",
        "candidate_id": "DIRECT_YES:bin-1",  # matches _qkernel_execution_cert default
        "direction": "buy_yes",
        "q_live": 0.72,
        "q_lcb_5pct": 0.72,
        "qkernel_execution_economics": _qkernel_execution_cert(),  # candidate_id DIRECT_YES:bin-1
        "live_cap_reserved_notional_usd": 10.0,
        "proof_maker_limit_price": 0.45,
        "proof_ev_maker": 0.01,
    }
    payload.update(overrides)
    return payload


def test_unstamped_proof_with_stray_cert_is_not_qkernel_authority():
    """B3 residual (re-review): an UNSTAMPED (legacy-selected) proof that carries a
    valid qkernel execution-economics cert must NOT be treated as qkernel authority —
    otherwise its payoff_q_lcb feeds sizing/materialization
    (_qkernel_execution_economics -> _robust_marginal_utility_stake_and_price) without
    the spine being the selector. RED-on-revert: restoring the cert-alone branch in
    _proof_uses_qkernel_spine makes this return True.
    """
    from src.engine.event_reactor_adapter import _proof_uses_qkernel_spine

    unstamped = SimpleNamespace(
        q_source="legacy_calibrator",                  # NOT the qkernel stamp
        selection_authority_applied=None,              # NOT stamped
        qkernel_execution_economics=_qkernel_execution_cert(),  # stray VALID cert
        direction="buy_yes",
    )
    assert _proof_uses_qkernel_spine(unstamped) is False, (
        "an unstamped proof carrying a stray valid qkernel cert must not be treated "
        "as qkernel authority — the cert alone is not the selection stamp"
    )


def test_spine_stamped_proof_is_qkernel_authority():
    """No regression: a spine-selected (stamped) proof still uses the qkernel path,
    which is how the bridge marks every legitimate qkernel selection
    (selection_authority_applied == 'qkernel_spine')."""
    from src.engine.event_reactor_adapter import _proof_uses_qkernel_spine

    stamped = SimpleNamespace(
        q_source=None,
        selection_authority_applied="qkernel_spine",   # the stamp qkernel_spine_bridge sets
        qkernel_execution_economics=None,
        direction="buy_yes",
    )
    assert _proof_uses_qkernel_spine(stamped) is True


class TestB3QkernelCertAuthorityAndIdentityGuard:
    """B3 (PR415): the taker-quality proof may consume the qkernel payoff_q_lcb ONLY
    when the payload carries the qkernel AUTHORITY STAMP (q_source == qkernel_spine)
    AND the cert is bound to the SELECTED candidate (cert.candidate_id ==
    payload.candidate_id). RED-on-revert: on the unfixed tree a mismatched/unstamped
    cert is consumed (q_lcb_source == qkernel path, edge computed); after the fix it
    fails closed with a typed reason and never sizes off the foreign cert's q.
    """

    def _proof(self, **overrides):
        from src.engine import event_reactor_adapter as adapter

        return adapter._build_event_bound_taker_quality_proof(
            actionable_payload=_b3_payload(**overrides),
            order_mode="TAKER",
            fresh_best_bid=0.49,
            fresh_best_ask=0.50,
        )

    def test_matched_stamped_cert_is_consumed(self):
        """The happy path is UNCHANGED: stamped + identity-matched cert is consumed."""
        proof = self._proof()
        assert proof is not None
        assert proof["q_lcb_source"] == "qkernel_execution_economics.payoff_q_lcb"
        # positive after-cost surplus (0.72 payoff vs 0.50 ask) -> passes
        assert float(proof["taker_fee_adjusted_edge"]) > 0.0
        assert proof["passed"] is True

    def test_matched_stamped_cert_mismatched_receipt_probability_fails_closed(self):
        """Qkernel execution economics must be the receipt probability pair."""
        proof = self._proof(q_lcb_5pct=0.01)
        assert proof is not None
        assert proof["passed"] is False
        assert proof["reason"] == "qkernel_payoff_probability_mismatch"
        assert proof.get("q_lcb_source") != "qkernel_execution_economics.payoff_q_lcb"

    def test_candidate_identity_mismatch_fails_closed(self):
        """RED-ON-REVERT. Cert.candidate_id (DIRECT_YES:bin-1) != payload.candidate_id
        (a DIFFERENT selected candidate). On the unfixed tree the foreign cert is
        consumed (q_lcb_source == qkernel path). After the fix it fails closed and the
        cert's q never drives the proof."""
        proof = self._proof(candidate_id="DIRECT_YES:bin-OTHER")
        assert proof is not None
        assert proof["passed"] is False
        assert proof["reason"] == "qkernel_cert_candidate_identity_mismatch"
        assert proof.get("q_lcb_source") != "qkernel_execution_economics.payoff_q_lcb"

    def test_cert_without_authority_stamp_fails_closed(self):
        """RED-ON-REVERT. A qkernel cert present but the payload is NOT under qkernel
        authority (q_source != qkernel_spine). On the unfixed tree the cert is consumed
        anyway; after the fix it fails closed."""
        proof = self._proof(q_source="legacy_calibrator")
        assert proof is not None
        assert proof["passed"] is False
        assert proof["reason"] == "qkernel_cert_present_without_qkernel_authority_stamp"
        assert proof.get("q_lcb_source") != "qkernel_execution_economics.payoff_q_lcb"

    def test_missing_candidate_id_fails_closed(self):
        """A stamped cert with NO payload candidate_id cannot prove identity -> closed."""
        proof = self._proof(candidate_id="")
        assert proof is not None
        assert proof["passed"] is False
        assert proof["reason"] == "qkernel_cert_candidate_identity_mismatch"


def test_live_execution_command_requires_opportunity_book_selection_match():
    from dataclasses import replace

    from src.engine import event_reactor_adapter as adapter

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    event = _forecast_event()
    decision_time = datetime(2026, 5, 24, 18, 10, tzinfo=timezone.utc)
    accepted = replace(
        _accepted_receipt(event),
        opportunity_book={
            "selected_candidate_id": "candidate-2",
            "actual_receipt_selected_candidate_id": "candidate-1",
        },
    )

    with pytest.raises(ValueError, match="EDLI_LIVE_OPPORTUNITY_BOOK_SELECTION_MISMATCH"):
        adapter._build_live_execution_command_certificates(
            event=event,
            receipt=accepted,
            decision_time=decision_time,
            live_cap_conn=conn,
            pre_submit_authority_provider=_pre_submit_authority_provider,
        )


def test_crossing_maker_proof_upgrades_to_taker_when_fresh_book_clears_quality():
    # A maker proof whose limit would cross the fresh book is not emitted as a
    # mixed post-only/crossing order. The same rest-then-cross policy may upgrade
    # it to a taker order, which then has post_only=False and must pass taker
    # quality on the fresh book.
    from src.engine import event_reactor_adapter as adapter
    from src.state.schema.edli_live_cap_usage_schema import ensure_table
    from tests.decision_kernel.no_submit_fixtures import build_test_no_submit_proof_bundle

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    ensure_table(conn)
    event = _forecast_event()
    decision_time = datetime(2026, 5, 24, 18, 10, tzinfo=timezone.utc)
    # With limit_price ~0.4 and witness ask=0.39, the maker proof would cross.
    # Current live semantics upgrade the order to FOK taker only if the fresh
    # taker quality proof clears; otherwise it aborts for re-decision.
    # P0 mode-authority: declare the PROVEN maker mode so the fresh-book validator (which
    # also computes MAKER from the low EV) confirms it and proceeds to the would_cross verifier
    # check, rather than aborting on a proof/fresh mode disagreement.
    accepted = replace(
        _accepted_receipt(event, execution_mode_intent="MAKER", maker_limit_price=0.40),
        trade_score=0.001,
        p_fill_lcb=0.0,
    )
    accepted = replace(
        accepted,
        decision_proof_bundle=build_test_no_submit_proof_bundle(event, accepted, decision_time=decision_time),
    )

    certificates = adapter._build_live_execution_command_certificates(
        event=event,
        receipt=accepted,
        decision_time=decision_time,
        live_cap_conn=conn,
        pre_submit_authority_provider=lambda *_args: _pre_submit_authority_witness(current_best_ask=0.39),
    )
    final_intent = next(cert for cert in certificates if cert.certificate_type == "FinalIntentCertificate")
    pre_submit = next(cert for cert in certificates if cert.certificate_type == "PreSubmitRevalidationCertificate")

    assert final_intent.payload["post_only"] is False
    assert final_intent.payload["order_type"] == "FOK_LIMIT"
    assert pre_submit.payload["would_cross_book"] is True
    assert pre_submit.payload["post_only"] is False


def test_fresh_pre_submit_book_upgrades_proven_maker_to_taker_when_crossing_clears():
    # Live redecision repair 2026-06-30: a resting MAKER proof is not allowed to
    # disappear when the fresh book makes crossing better. The same rest-then-cross
    # policy may upgrade it to TAKER, but the final command must then carry taker
    # order semantics and prove against the fresh pre-submit book.
    from src.engine import event_reactor_adapter as adapter
    from tests.decision_kernel.no_submit_fixtures import build_test_no_submit_proof_bundle

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    event = _forecast_event()
    decision_time = datetime(2026, 5, 24, 18, 10, tzinfo=timezone.utc)
    # PROVEN MAKER (stale wide book at eval), large edge (q_live 0.7 vs reservation 0.4).
    accepted = replace(
        _accepted_receipt(event, execution_mode_intent="MAKER", maker_limit_price=0.39),
        trade_score=0.015,
        p_fill_lcb=0.10,
    )
    proof_bundle = build_test_no_submit_proof_bundle(event, accepted, decision_time=decision_time)
    proof_bundle = replace(
        proof_bundle,
        quote_feasibility=replace(
            proof_bundle.quote_feasibility,
            payload={
                **proof_bundle.quote_feasibility.payload,
                "best_bid": 0.38,
                "best_ask": 0.42,
                "book_hash": "stale-book-hash",
            },
        ),
        # Near deadline, the same rest-then-cross policy genuinely crosses
        # (fresh=TAKER) on the tight fresh book. The test verifies this becomes a
        # taker submit with fresh-book certificates, not a silent disappearance.
        executable_snapshot=replace(
            proof_bundle.executable_snapshot,
            payload={
                **proof_bundle.executable_snapshot.payload,
                "market_end_at": (decision_time + timedelta(minutes=5)).isoformat(),
            },
        ),
    )
    accepted = replace(accepted, decision_proof_bundle=proof_bundle)

    certs = adapter._build_live_execution_command_certificates(
        event=event,
        receipt=accepted,
        decision_time=decision_time,
        live_cap_conn=conn,
        pre_submit_authority_provider=lambda *_args: _pre_submit_authority_witness(
            current_best_bid=0.39,
            current_best_ask=0.40,
        ),
    )

    from src.decision_kernel import claims

    final_intent = next(c for c in certs if getattr(c, "certificate_type", None) == claims.FINAL_INTENT)
    pre_submit = next(
        c for c in certs if getattr(c, "certificate_type", None) == claims.PRE_SUBMIT_REVALIDATION
    )
    command = next(c for c in certs if getattr(c, "certificate_type", None) == claims.EXECUTION_COMMAND)

    assert final_intent.payload["order_mode"] == "TAKER"
    assert final_intent.payload["post_only"] is False
    assert pre_submit.payload["current_best_ask"] == 0.40
    assert pre_submit.payload["would_cross_book"] is True
    assert command.payload["order_type"] == "FOK_LIMIT"
    assert command.payload["time_in_force"] == "FOK"
    assert command.payload["post_only"] is False


def test_live_order_build_releases_stale_wal_snapshot_before_aggregate_write(tmp_path):
    from src.decision_kernel import claims
    from src.engine import event_reactor_adapter as adapter
    from tests.decision_kernel.no_submit_fixtures import build_test_no_submit_proof_bundle

    db_path = tmp_path / "trade.db"
    setup = sqlite3.connect(str(db_path))
    setup.execute("PRAGMA journal_mode=WAL")
    setup.execute("CREATE TABLE stale_probe (id INTEGER PRIMARY KEY, value TEXT)")
    setup.execute("INSERT INTO stale_probe (value) VALUES ('before')")
    setup.commit()
    setup.close()

    conn = sqlite3.connect(str(db_path), timeout=1.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout = 100")

    conn.execute("BEGIN")
    assert conn.execute("SELECT COUNT(*) FROM stale_probe").fetchone()[0] == 1
    assert conn.in_transaction is True

    other = sqlite3.connect(str(db_path), timeout=1.0)
    try:
        other.execute("PRAGMA journal_mode=WAL")
        other.execute("INSERT INTO stale_probe (value) VALUES ('after')")
        other.commit()
    finally:
        other.close()

    event = _forecast_event()
    decision_time = datetime(2026, 5, 24, 18, 10, tzinfo=timezone.utc)
    accepted = _accepted_receipt(event)
    accepted = replace(
        accepted,
        decision_proof_bundle=build_test_no_submit_proof_bundle(
            event,
            accepted,
            decision_time=decision_time,
        ),
    )

    certs = adapter._build_live_execution_command_certificates(
        event=event,
        receipt=accepted,
        decision_time=decision_time,
        live_cap_conn=conn,
        trade_conn=conn,
        pre_submit_authority_provider=lambda *_args: _pre_submit_authority_witness(
            current_best_bid=0.39,
            current_best_ask=0.40,
        ),
    )

    assert any(getattr(c, "certificate_type", None) == claims.EXECUTION_COMMAND for c in certs)
    conn.commit()
    assert conn.execute("SELECT COUNT(*) FROM edli_live_order_events").fetchone()[0] >= 1
    conn.close()


def test_live_command_reuses_single_pre_submit_authority_witness():
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
    calls = []

    def _provider(final_intent, _executable_snapshot, _decision_time):
        calls.append(final_intent.payload["token_id"])
        return _pre_submit_authority_witness(current_best_bid=0.39, current_best_ask=0.40)

    certs = adapter._build_live_execution_command_certificates(
        event=event,
        receipt=accepted,
        decision_time=decision_time,
        live_cap_conn=conn,
        pre_submit_authority_provider=_provider,
    )

    pre_submit = next(c for c in certs if getattr(c, "certificate_type", None) == claims.PRE_SUBMIT_REVALIDATION)
    assert calls == ["yes-1"]
    assert pre_submit.payload["book_hash"] == "book-hash-1"


def test_edli_live_cap_path_does_not_reference_legacy_cap_columns():
    from pathlib import Path

    # src/strategy/live_inference/promotion_ledger.py removed 2026-07-08 (R0-c zero-caller
    # corpse purge); dropped from this scan — the assertion is vacuously strengthened by
    # its absence (a deleted file cannot reference the legacy cap columns).
    source = "\n".join(
        Path(path).read_text()
        for path in (
            "src/events/reactor.py",
            "src/engine/event_reactor_adapter.py",
        )
    )

    assert "cap_name" not in source
    assert "usage_date" not in source
    assert "SUM(notional_usd)" not in source


def test_live_adapter_no_canary_gate_proceeds_past_deleted_canary_block(monkeypatch):
    """Wave-1 2026-06-12 antibody: the LIVE_CANARY_DISABLED gate is DELETED.

    With real_order_submit_enabled=True there is no longer any canary on/off flag that
    can refuse the submit with reason 'LIVE_CANARY_DISABLED'. The adapter proceeds past
    the (deleted) canary check to the NEXT real gate — here the durable-outbox requirement
    (no outbox passed) — proving the canary block is gone, not merely flipped on."""
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
        # durable_submit_outbox_enabled intentionally NOT passed (defaults False).
    )

    receipt = submit(event, datetime(2026, 5, 24, 18, 10, tzinfo=timezone.utc))

    # The deleted canary gate is unreachable: reason is the next honest gate, never the
    # old LIVE_CANARY_DISABLED.
    assert receipt.reason != "LIVE_CANARY_DISABLED"
    assert receipt.reason == "EDLI_DURABLE_SUBMIT_OUTBOX_REQUIRED"


def test_live_adapter_blocks_entry_when_live_health_surface_missing(monkeypatch, tmp_path):
    """Live-health entry authority must fail closed before command build.

    A missing q/provenance/lifecycle/monitor surface is not an operator pause
    and not a no-edge decision; it is degraded entry authority. ENTRY must stop
    before the no-submit receipt builder can reserve live cap or reach executor.
    """

    from src.engine import event_reactor_adapter as adapter
    from src.riskguard.risk_level import RiskLevel

    event = _forecast_event()
    decision_time = datetime(2026, 5, 24, 18, 10, tzinfo=timezone.utc)
    _install_unpaused_world_control_db(monkeypatch, tmp_path)
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    build_called = {"count": 0}
    executor_called = {"count": 0}

    def _build_should_not_run(*_args, **_kwargs):
        build_called["count"] += 1
        raise AssertionError("no-submit receipt builder must not run when live health blocks entry")

    def _executor_should_not_run(_final_intent, _command):
        executor_called["count"] += 1
        raise AssertionError("executor_submit must not run when live health blocks entry")

    def _missing_q_version_surface():
        payload = _healthy_entry_live_health_provider(decision_time)()
        payload["surfaces"].pop("entry_q_version")
        return payload

    monkeypatch.setattr(adapter, "build_event_bound_no_submit_receipt", _build_should_not_run)

    submit = adapter.event_bound_live_adapter_from_trade_conn(
        conn,
        live_cap_conn=conn,
        get_current_level=lambda: RiskLevel.GREEN,
        real_order_submit_enabled=True,
        durable_submit_outbox_enabled=True,
        operator_arm=_operator_arm(),
        executor_submit=_executor_should_not_run,
        entry_live_health_authority_provider=_missing_q_version_surface,
    )

    receipt = submit(event, decision_time)

    assert receipt.submitted is False
    assert receipt.proof_accepted is False
    assert receipt.reason == "live_health_entry_authority:missing_surfaces=entry_q_version"
    assert build_called["count"] == 0
    assert executor_called["count"] == 0


def test_entry_live_health_authority_blocks_legacy_composite_without_computed_at():
    from src.engine import event_reactor_adapter as adapter

    payload = _healthy_entry_live_health_provider(
        datetime(2026, 5, 24, 18, 10, tzinfo=timezone.utc)
    )()
    payload.pop("computed_at")

    reason = adapter._entry_live_health_authority_block_reason(
        lambda: payload,
        now=datetime(2026, 5, 24, 18, 11, tzinfo=timezone.utc),
    )

    assert reason == "computed_at_missing_or_invalid"


def test_entry_live_health_authority_ignores_business_plane_only_degraded():
    from src.engine import event_reactor_adapter as adapter

    decision_time = datetime(2026, 5, 24, 18, 10, tzinfo=timezone.utc)

    reason = adapter._entry_live_health_authority_block_reason(
        _healthy_entry_live_health_provider(decision_time),
        now=decision_time + timedelta(seconds=30),
    )

    assert reason is None


def test_live_adapter_submit_enabled_canary_enabled_calls_executor_mock(monkeypatch, tmp_path):
    from src.engine import event_reactor_adapter as adapter
    from src.engine.event_bound_final_intent import EventBoundExecutorSubmitResult
    from src.events.reactor import EventSubmissionReceipt
    from src.riskguard.risk_level import RiskLevel
    from tests.decision_kernel.no_submit_fixtures import build_test_no_submit_proof_bundle

    event = _forecast_event()
    _install_unpaused_world_control_db(monkeypatch, tmp_path)
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    called = {"count": 0}
    accepted = replace(
        _accepted_receipt(event),
        decision_proof_bundle=build_test_no_submit_proof_bundle(event, _accepted_receipt(event), decision_time=datetime(2026, 5, 24, 18, 10, tzinfo=timezone.utc)),
    )
    original_build = adapter.build_event_bound_no_submit_receipt
    adapter.build_event_bound_no_submit_receipt = lambda *_args, **_kwargs: accepted

    try:
        def _submit(_final_intent, _command):
            called["count"] += 1
            actionable_hash = _final_intent.payload["actionable_certificate_hash"]
            persisted = conn.execute(
                """
                SELECT certificate_type, mode, verifier_status
                FROM decision_certificates
                WHERE certificate_hash = ?
                """,
                (actionable_hash,),
            ).fetchone()
            assert persisted is not None
            assert persisted["certificate_type"] == "ActionableTradeCertificate"
            assert persisted["mode"] == "LIVE"
            assert persisted["verifier_status"] == "VERIFIED"
            return EventBoundExecutorSubmitResult(
                status="SUBMITTED",
                reason_code="OK",
                venue_order_id="venue-1",
                submit_started_at="2026-05-24T18:10:00+00:00",
                submit_finished_at="2026-05-24T18:10:01+00:00",
                raw_response={"status": "submitted"},
                venue_call_started=True,
                venue_ack_received=True,
            )

        submit = adapter.event_bound_live_adapter_from_trade_conn(
            conn,
            live_cap_conn=conn,
            get_current_level=lambda: RiskLevel.GREEN,
            real_order_submit_enabled=True,
            durable_submit_outbox_enabled=True,
            operator_arm=_operator_arm(),
            executor_submit=_submit,
            pre_submit_authority_provider=_pre_submit_authority_provider,
            entry_live_health_authority_provider=_healthy_entry_live_health_provider(
                datetime(2026, 5, 24, 18, 10, tzinfo=timezone.utc)
            ),
        )

        receipt = submit(event, datetime(2026, 5, 24, 18, 10, tzinfo=timezone.utc))

        assert called["count"] == 1, (
            receipt.side_effect_status,
            receipt.reason,
            getattr(receipt, "submit_diagnostics", None),
        )
        assert receipt.submitted is True
        assert receipt.side_effect_status == "SUBMITTED"
        assert _receipt_status(receipt) == "SUBMITTED"
        assert conn.execute("SELECT reservation_status FROM edli_live_cap_usage").fetchone()["reservation_status"] == "CONSUMED"
        assert _cap_transition_status(receipt) == "CONSUMED"
        assert _cap_transition_projection_status(receipt) == "CONSUMED"
    finally:
        adapter.build_event_bound_no_submit_receipt = original_build


def test_live_submit_aggregate_persists_decision_audit_payload(monkeypatch, tmp_path):
    from src.engine import event_reactor_adapter as adapter
    from src.engine.event_bound_final_intent import EventBoundExecutorSubmitResult
    from src.riskguard.risk_level import RiskLevel
    from tests.decision_kernel.no_submit_fixtures import build_test_no_submit_proof_bundle

    event = _forecast_event()
    _install_unpaused_world_control_db(monkeypatch, tmp_path)
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    decision_time = datetime(2026, 5, 24, 18, 10, tzinfo=timezone.utc)
    accepted = replace(
        _accepted_receipt(event),
        decision_proof_bundle=build_test_no_submit_proof_bundle(
            event,
            _accepted_receipt(event),
            decision_time=decision_time,
        ),
    )
    original_build = adapter.build_event_bound_no_submit_receipt
    adapter.build_event_bound_no_submit_receipt = lambda *_args, **_kwargs: accepted

    try:
        submit = adapter.event_bound_live_adapter_from_trade_conn(
            conn,
            live_cap_conn=conn,
            get_current_level=lambda: RiskLevel.GREEN,
            real_order_submit_enabled=True,
            durable_submit_outbox_enabled=True,
            operator_arm=_operator_arm(),
            executor_submit=lambda _final_intent, _command: EventBoundExecutorSubmitResult(
                status="SUBMITTED",
                reason_code="OK",
                venue_order_id="venue-1",
                submit_started_at="2026-05-24T18:10:00+00:00",
                submit_finished_at="2026-05-24T18:10:01+00:00",
                raw_response={"status": "submitted"},
                venue_call_started=True,
                venue_ack_received=True,
            ),
            pre_submit_authority_provider=_pre_submit_authority_provider,
            entry_live_health_authority_provider=_healthy_entry_live_health_provider(decision_time),
        )

        receipt = submit(event, decision_time)

        row = conn.execute(
            """
            SELECT payload_json
            FROM edli_live_order_events
            WHERE event_type = 'DecisionProofAccepted'
            """
        ).fetchone()
        payload = json.loads(row["payload_json"])
        audit = payload["decision_audit"]

        assert receipt.side_effect_status == "SUBMITTED"
        assert audit["schema"] == "edli_live_decision_audit_v1"
        assert audit["event_id"] == event.event_id
        assert audit["final_intent_id"] == "intent-1"
        assert audit["condition_id"] == "condition-1"
        assert audit["token_id"] == "yes-1"
        assert audit["direction"] == "buy_yes"
        assert audit["q_source"] == "emos"
        assert audit["q_live"] == pytest.approx(0.7)
        assert audit["q_lcb_5pct"] == pytest.approx(0.6)
        assert audit["c_fee_adjusted"] == pytest.approx(0.4)
        assert audit["trade_score"] == pytest.approx(0.2)
        assert audit["kelly_size_usd"] == pytest.approx(3.0)
        assert audit["selected_candidate_id"] == "candidate-1"
        assert audit["actual_receipt_selected_candidate_id"] == "candidate-1"
        assert audit["selected_condition_id"] == "condition-1"
        assert audit["selected_token_id"] == "yes-1"
        assert audit["selected_direction"] == "buy_yes"
        assert audit["actual_condition_id"] == "condition-1"
        assert audit["actual_token_id"] == "yes-1"
        assert audit["actual_direction"] == "buy_yes"
        opportunity_book = audit["opportunity_book"]
        assert opportunity_book["selected_candidate_id"] == "candidate-1"
        assert opportunity_book["actual_receipt_selected_candidate_id"] == "candidate-1"
        assert len(opportunity_book["candidates"]) == 1
        candidate = opportunity_book["candidates"][0]
        assert candidate["candidate_id"] == "candidate-1"
        assert candidate["condition_id"] == "condition-1"
        assert candidate["token_id"] == "yes-1"
        assert candidate["direction"] == "buy_yes"
        assert candidate["qkernel_execution_economics"]["source"] == "qkernel_spine"
        assert candidate["qkernel_execution_economics"]["route_id"]
        assert audit["actionable_certificate_hash"]
        assert audit["final_intent_certificate_hash"]
        assert {cert["certificate_type"] for cert in audit["parent_certificates"]}
    finally:
        adapter.build_event_bound_no_submit_receipt = original_build


def test_live_adapter_blocks_real_submit_without_durable_outbox(monkeypatch):
    from src.engine import event_reactor_adapter as adapter
    from src.engine.event_bound_final_intent import EventBoundExecutorSubmitResult
    from src.events.reactor import EventSubmissionReceipt
    from src.riskguard.risk_level import RiskLevel
    from tests.decision_kernel.no_submit_fixtures import build_test_no_submit_proof_bundle

    event = _forecast_event()
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    called = {"count": 0}
    accepted = replace(
        _accepted_receipt(event),
        decision_proof_bundle=build_test_no_submit_proof_bundle(event, _accepted_receipt(event), decision_time=datetime(2026, 5, 24, 18, 10, tzinfo=timezone.utc)),
    )
    original_build = adapter.build_event_bound_no_submit_receipt
    adapter.build_event_bound_no_submit_receipt = lambda *_args, **_kwargs: accepted

    try:
        def _submit(_final_intent, _command):
            called["count"] += 1
            return EventBoundExecutorSubmitResult(status="SUBMITTED")

        submit = adapter.event_bound_live_adapter_from_trade_conn(
            conn,
            live_cap_conn=conn,
            get_current_level=lambda: RiskLevel.GREEN,
            real_order_submit_enabled=True,
            executor_submit=_submit,
            pre_submit_authority_provider=_pre_submit_authority_provider,
        )

        receipt = submit(event, datetime(2026, 5, 24, 18, 10, tzinfo=timezone.utc))

        assert called["count"] == 0
        assert receipt.proof_accepted is False
        assert receipt.reason == "EDLI_DURABLE_SUBMIT_OUTBOX_REQUIRED"
        assert _table_count(conn, "edli_live_order_events") == 0
        assert _table_count(conn, "edli_live_cap_usage") == 0
    finally:
        adapter.build_event_bound_no_submit_receipt = original_build


def test_live_adapter_records_rejected_fixture_response(monkeypatch, tmp_path):
    from src.engine import event_reactor_adapter as adapter
    from src.engine.event_bound_final_intent import EventBoundExecutorSubmitResult
    from src.events.reactor import EventSubmissionReceipt
    from src.riskguard.risk_level import RiskLevel
    from tests.decision_kernel.no_submit_fixtures import build_test_no_submit_proof_bundle

    event = _forecast_event()
    _install_unpaused_world_control_db(monkeypatch, tmp_path)
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    accepted = replace(
        _accepted_receipt(event),
        decision_proof_bundle=build_test_no_submit_proof_bundle(event, _accepted_receipt(event), decision_time=datetime(2026, 5, 24, 18, 10, tzinfo=timezone.utc)),
    )
    original_build = adapter.build_event_bound_no_submit_receipt
    adapter.build_event_bound_no_submit_receipt = lambda *_args, **_kwargs: accepted
    try:
        submit = adapter.event_bound_live_adapter_from_trade_conn(
            conn,
            live_cap_conn=conn,
            get_current_level=lambda: RiskLevel.GREEN,
            real_order_submit_enabled=True,
            durable_submit_outbox_enabled=True,
            operator_arm=_operator_arm(),
            executor_submit=lambda _final_intent, _command: EventBoundExecutorSubmitResult(
                status="REJECTED",
                reason_code="VENUE_REJECTED",
                submit_started_at="2026-05-24T18:10:00+00:00",
                submit_finished_at="2026-05-24T18:10:01+00:00",
                raw_response={"status": "rejected"},
                venue_call_started=True,
            ),
            pre_submit_authority_provider=_pre_submit_authority_provider,
            entry_live_health_authority_provider=_healthy_entry_live_health_provider(
                datetime(2026, 5, 24, 18, 10, tzinfo=timezone.utc)
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
    finally:
        adapter.build_event_bound_no_submit_receipt = original_build


def test_pre_venue_depth_rejection_terminates_aggregate_and_releases_cap(monkeypatch, tmp_path):
    """RELATIONSHIP (F-class deadlock antibody, 2026-06-01):

    A live order that FAILS the executor's PRE-VENUE depth validation
    (DEPTH_INSUFFICIENT, raised before any venue call) must terminate the
    live-order aggregate AND release its LIVE_CAP reservation — leaving NO
    unresolved-submit and NO held cap. This is the EXACT state that crash-looped
    the edli_live readiness gate. Contrast with
    test_live_adapter_records_timeout_unknown_fixture_response, which proves a
    GENUINE post-venue unknown still leaves cap RESERVED + pending_reconcile.

    The injected executor_submit returns the EXACT EventBoundExecutorSubmitResult
    that the real submit boundary produces for a PreVenueSubmitError
    (status=PRE_SUBMIT_ERROR, venue_call_started=False). The companion unit test
    tests/engine/test_pre_venue_rejection_terminal.py proves the boundary derives
    that result from a PreVenueSubmitError; this test proves that result then
    drives the aggregate + cap to a terminal RELEASED state (no deadlock).
    """
    from src.engine import event_reactor_adapter as adapter
    from src.engine.event_bound_final_intent import EventBoundExecutorSubmitResult
    from src.riskguard.risk_level import RiskLevel
    from tests.decision_kernel.no_submit_fixtures import build_test_no_submit_proof_bundle

    event = _forecast_event()
    _install_unpaused_world_control_db(monkeypatch, tmp_path)
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    decision_time = datetime(2026, 5, 24, 18, 10, tzinfo=timezone.utc)
    accepted = replace(
        _accepted_receipt(event),
        decision_proof_bundle=build_test_no_submit_proof_bundle(event, _accepted_receipt(event), decision_time=decision_time),
    )

    def _boundary_submit(_final_intent, _command):
        # Canonical pre-venue rejection result (what the real boundary emits for
        # PreVenueSubmitError: terminal, venue never reached, side effect known).
        return EventBoundExecutorSubmitResult(
            status="PRE_SUBMIT_ERROR",
            reason_code="EXECUTOR_PRE_VENUE_REJECTED:FinalExecutionIntent executable depth validation failed: DEPTH_INSUFFICIENT",
            submit_started_at="2026-05-24T18:10:00+00:00",
            submit_finished_at="2026-05-24T18:10:00+00:00",
            raw_response={"error": "DEPTH_INSUFFICIENT", "stage": "existing_executor_pre_venue"},
            reconciliation_followup_required=False,
            venue_call_started=False,
            venue_ack_received=False,
            side_effect_known=True,
        )

    original_build = adapter.build_event_bound_no_submit_receipt
    adapter.build_event_bound_no_submit_receipt = lambda *_args, **_kwargs: accepted
    try:
        submit = adapter.event_bound_live_adapter_from_trade_conn(
            conn,
            live_cap_conn=conn,
            get_current_level=lambda: RiskLevel.GREEN,
            real_order_submit_enabled=True,
            durable_submit_outbox_enabled=True,
            operator_arm=_operator_arm(),
            executor_submit=_boundary_submit,
            pre_submit_authority_provider=_pre_submit_authority_provider,
            entry_live_health_authority_provider=_healthy_entry_live_health_provider(decision_time),
        )

        receipt = submit(event, decision_time)

        assert receipt.submitted is False
        assert receipt.side_effect_status == "PRE_SUBMIT_ERROR"
        assert _receipt_status(receipt) == "PRE_SUBMIT_ERROR"

        # Cap RELEASED (not RESERVED) — no held-cap deadlock.
        assert conn.execute(
            "SELECT reservation_status FROM edli_live_cap_usage"
        ).fetchone()["reservation_status"] == "RELEASED"
        assert _cap_transition_status(receipt) == "RELEASED"
        assert _cap_transition_projection_status(receipt) == "RELEASED"

        # Aggregate terminal: SubmitRejected (not SubmitUnknown), no pending_reconcile.
        event_types = [row["event_type"] for row in conn.execute(
            "SELECT event_type FROM edli_live_order_events ORDER BY event_sequence"
        )]
        assert "SubmitRejected" in event_types
        assert "SubmitUnknown" not in event_types
        projection = conn.execute(
            "SELECT current_state, pending_reconcile FROM edli_live_order_projection"
        ).fetchone()
        assert bool(projection["pending_reconcile"]) is False

        # Boot readiness counts (the exact gate queries) are clear → no deadlock.
        unresolved = conn.execute(
            "SELECT COUNT(*) c FROM edli_live_order_projection WHERE pending_reconcile = 1"
        ).fetchone()["c"]
        reserved = conn.execute(
            "SELECT COUNT(*) c FROM edli_live_cap_usage WHERE reservation_status = 'RESERVED'"
        ).fetchone()["c"]
        assert unresolved == 0
        assert reserved == 0
    finally:
        adapter.build_event_bound_no_submit_receipt = original_build


def test_post_command_executor_exception_terminalizes_aggregate(monkeypatch, tmp_path):
    """Regression: command-created live orders cannot disappear into NO_SUBMIT.

    Once ExecutionCommandCreated is durable, an unexpected executor-boundary
    exception must append a terminal aggregate event. During the executor call
    side effect is unknown, so the correct terminal state is POST_SUBMIT_UNKNOWN
    with pending reconcile, not a proof-failed NO_SUBMIT that leaves the command
    aggregate stuck.
    """
    from src.engine import event_reactor_adapter as adapter
    from src.riskguard.risk_level import RiskLevel
    from tests.decision_kernel.no_submit_fixtures import build_test_no_submit_proof_bundle

    event = _forecast_event()
    _install_unpaused_world_control_db(monkeypatch, tmp_path)
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    decision_time = datetime(2026, 5, 24, 18, 10, tzinfo=timezone.utc)
    accepted = replace(
        _accepted_receipt(event),
        decision_proof_bundle=build_test_no_submit_proof_bundle(
            event,
            _accepted_receipt(event),
            decision_time=decision_time,
        ),
    )
    original_build = adapter.build_event_bound_no_submit_receipt
    adapter.build_event_bound_no_submit_receipt = lambda *_args, **_kwargs: accepted

    def _raising_submit(_final_intent, _command):
        raise RuntimeError("socket closed before executor result was normalized")

    try:
        submit = adapter.event_bound_live_adapter_from_trade_conn(
            conn,
            live_cap_conn=conn,
            get_current_level=lambda: RiskLevel.GREEN,
            real_order_submit_enabled=True,
            durable_submit_outbox_enabled=True,
            operator_arm=_operator_arm(),
            executor_submit=_raising_submit,
            pre_submit_authority_provider=_pre_submit_authority_provider,
            entry_live_health_authority_provider=_healthy_entry_live_health_provider(decision_time),
        )

        receipt = submit(event, decision_time)

        assert receipt.proof_accepted is True
        assert receipt.side_effect_status == "POST_SUBMIT_UNKNOWN"
        assert receipt.reason.startswith("EDLI_LIVE_POST_COMMAND_SUBMIT_FAILURE:calling_executor_submit")
        assert _receipt_status(receipt) == "POST_SUBMIT_UNKNOWN"
        event_types = [
            row["event_type"]
            for row in conn.execute(
                "SELECT event_type FROM edli_live_order_events ORDER BY event_sequence"
            )
        ]
        assert event_types == [
            "DecisionProofAccepted",
            "SubmitPlanBuilt",
            "PreSubmitRevalidated",
            "LiveCapReserved",
            "ExecutionCommandCreated",
            "VenueSubmitAttempted",
            "SubmitUnknown",
            "CapTransitioned",
        ]
        assert conn.execute(
            "SELECT reservation_status FROM edli_live_cap_usage"
        ).fetchone()["reservation_status"] == "RESERVED"
        projection = conn.execute(
            "SELECT current_state, pending_reconcile FROM edli_live_order_projection"
        ).fetchone()
        assert bool(projection["pending_reconcile"]) is True
        assert projection["current_state"] in {"PENDING_RECONCILE", "CAP_TRANSITIONED"}
    finally:
        adapter.build_event_bound_no_submit_receipt = original_build


def test_live_adapter_records_timeout_unknown_fixture_response(monkeypatch, tmp_path):
    from src.engine import event_reactor_adapter as adapter
    from src.engine.event_bound_final_intent import EventBoundExecutorSubmitResult
    from src.events.reactor import EventSubmissionReceipt
    from src.riskguard.risk_level import RiskLevel
    from tests.decision_kernel.no_submit_fixtures import build_test_no_submit_proof_bundle

    event = _forecast_event()
    _install_unpaused_world_control_db(monkeypatch, tmp_path)
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    accepted = replace(
        _accepted_receipt(event),
        decision_proof_bundle=build_test_no_submit_proof_bundle(event, _accepted_receipt(event), decision_time=datetime(2026, 5, 24, 18, 10, tzinfo=timezone.utc)),
    )
    original_build = adapter.build_event_bound_no_submit_receipt
    adapter.build_event_bound_no_submit_receipt = lambda *_args, **_kwargs: accepted
    try:
        submit = adapter.event_bound_live_adapter_from_trade_conn(
            conn,
            live_cap_conn=conn,
            get_current_level=lambda: RiskLevel.GREEN,
            real_order_submit_enabled=True,
            durable_submit_outbox_enabled=True,
            operator_arm=_operator_arm(),
            executor_submit=lambda _final_intent, _command: EventBoundExecutorSubmitResult(
                status="TIMEOUT_UNKNOWN",
                reason_code="SUBMIT_TIMEOUT",
                submit_started_at="2026-05-24T18:10:00+00:00",
                submit_finished_at="2026-05-24T18:10:30+00:00",
                raw_response={"status": "timeout"},
                reconciliation_followup_required=True,
                venue_call_started=True,
                side_effect_known=False,
            ),
            pre_submit_authority_provider=_pre_submit_authority_provider,
            entry_live_health_authority_provider=_healthy_entry_live_health_provider(
                datetime(2026, 5, 24, 18, 10, tzinfo=timezone.utc)
            ),
        )

        receipt = submit(event, datetime(2026, 5, 24, 18, 10, tzinfo=timezone.utc))

        assert receipt.submitted is False
        assert receipt.side_effect_status == "TIMEOUT_UNKNOWN"
        assert _receipt_status(receipt) == "TIMEOUT_UNKNOWN"
        receipt_cert = _receipt_cert(receipt)
        assert receipt_cert.payload["reconciliation_followup_required"] is True
        assert conn.execute("SELECT reservation_status FROM edli_live_cap_usage").fetchone()["reservation_status"] == "RESERVED"
        event_types = [row["event_type"] for row in conn.execute("SELECT event_type FROM edli_live_order_events ORDER BY event_sequence")]
        assert event_types == [
            "DecisionProofAccepted",
            "SubmitPlanBuilt",
            "PreSubmitRevalidated",
            "LiveCapReserved",
            "ExecutionCommandCreated",
            "VenueSubmitAttempted",
            "SubmitUnknown",
            "CapTransitioned",
        ]
        assert _cap_transition_status(receipt) == "PENDING_RECONCILE"
        assert _cap_transition_projection_status(receipt) == "RESERVED"
        projection = conn.execute("SELECT current_state, pending_reconcile FROM edli_live_order_projection").fetchone()
        assert bool(projection["pending_reconcile"]) is True
        assert projection["current_state"] in {"PENDING_RECONCILE", "CAP_TRANSITIONED"}
    finally:
        adapter.build_event_bound_no_submit_receipt = original_build


def test_live_adapter_records_post_submit_unknown_as_pending_reconcile(monkeypatch, tmp_path):
    from src.engine import event_reactor_adapter as adapter
    from src.engine.event_bound_final_intent import EventBoundExecutorSubmitResult
    from src.riskguard.risk_level import RiskLevel
    from tests.decision_kernel.no_submit_fixtures import build_test_no_submit_proof_bundle

    event = _forecast_event()
    _install_unpaused_world_control_db(monkeypatch, tmp_path)
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    accepted = replace(
        _accepted_receipt(event),
        decision_proof_bundle=build_test_no_submit_proof_bundle(event, _accepted_receipt(event), decision_time=datetime(2026, 5, 24, 18, 10, tzinfo=timezone.utc)),
    )
    original_build = adapter.build_event_bound_no_submit_receipt
    adapter.build_event_bound_no_submit_receipt = lambda *_args, **_kwargs: accepted
    try:
        submit = adapter.event_bound_live_adapter_from_trade_conn(
            conn,
            live_cap_conn=conn,
            get_current_level=lambda: RiskLevel.GREEN,
            real_order_submit_enabled=True,
            durable_submit_outbox_enabled=True,
            operator_arm=_operator_arm(),
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
            pre_submit_authority_provider=_pre_submit_authority_provider,
            entry_live_health_authority_provider=_healthy_entry_live_health_provider(
                datetime(2026, 5, 24, 18, 10, tzinfo=timezone.utc)
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
        event_types = [row["event_type"] for row in conn.execute("SELECT event_type FROM edli_live_order_events ORDER BY event_sequence")]
        assert event_types == [
            "DecisionProofAccepted",
            "SubmitPlanBuilt",
            "PreSubmitRevalidated",
            "LiveCapReserved",
            "ExecutionCommandCreated",
            "VenueSubmitAttempted",
            "SubmitUnknown",
            "CapTransitioned",
        ]
        assert _cap_transition_status(receipt) == "PENDING_RECONCILE"
        assert _cap_transition_projection_status(receipt) == "RESERVED"
        projection = conn.execute("SELECT current_state, pending_reconcile FROM edli_live_order_projection").fetchone()
        assert bool(projection["pending_reconcile"]) is True
        assert projection["current_state"] in {"PENDING_RECONCILE", "CAP_TRANSITIONED"}
    finally:
        adapter.build_event_bound_no_submit_receipt = original_build


@pytest.mark.parametrize(
    ("field", "message"),
    [
        ("executable_snapshot_hash", "executable_snapshot_hash missing"),
        ("cost_basis_hash", "cost_basis_hash missing"),
        ("decision_source_context", "decision_source_context missing"),
        ("passive_maker_context", "passive_maker_context missing"),
    ],
)
def test_production_executor_boundary_rejects_unenriched_final_intent_before_executor(field, message):
    from src.engine.event_bound_final_intent import (
        EventBoundExecutorExpressibilityError,
        submit_event_bound_final_intent_via_existing_executor,
    )

    _actionable, final_intent, _expressibility, _live_cap, command = _command_cert_bundle()
    stripped = _replace_payload(final_intent, {field: ""})

    with pytest.raises(EventBoundExecutorExpressibilityError, match=message):
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
    assert "pre_submit_authority_provider=_edli_pre_submit_authority_provider_from_book_evidence_conn" in source


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
    monkeypatch.setenv("ZEUS_PRE_SUBMIT_CLOB_TIMEOUT_SECONDS", "2.5")
    clob_timeouts = []

    class FakePolymarketClient:
        def __init__(self, *, public_http_timeout=None):
            clob_timeouts.append(public_http_timeout)

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def v2_preflight(self):
            return {"ok": True}

        def _ensure_v2_adapter(self):
            return self

        def get_collateral_payload(self):
            return {
                "pusd_balance_micro": 25_000_000,
                "pusd_allowance_micro": 25_000_000,
                "ctf_token_balances_units": {"yes-1": 25.0},
                "ctf_token_allowances_units": {"yes-1": 25.0},
            }

    monkeypatch.setattr(polymarket_client, "PolymarketClient", FakePolymarketClient)
    provider = main._edli_pre_submit_authority_provider_from_book_evidence_conn(
        conn,
        {
            "pre_submit_max_quote_age_ms": 1000,
            "pre_submit_balance_allowance_check_enabled": True,
        },
    )
    final_intent = SimpleNamespace(
        payload={
            "token_id": "yes-1",
            "side": "BUY",
            "tick_size": 0.01,
            "min_order_size": 1.0,
            "neg_risk": False,
            "notional_usd": 5.0,
        }
    )

    witness = provider(final_intent, object(), datetime(2026, 5, 25, 12, tzinfo=timezone.utc))
    witness_again = provider(final_intent, object(), datetime(2026, 5, 25, 12, tzinfo=timezone.utc))

    assert witness.book_hash == "book-hash-1"
    assert witness_again.book_hash == "book-hash-1"
    assert witness.book_authority_id == "execution_feasibility_evidence"
    assert witness.heartbeat_authority_id == "heartbeat_supervisor"
    assert witness.user_ws_authority_id == "ws_gap_guard"
    assert witness.balance_allowance_authority_id == "polymarket_wallet_readonly"
    assert witness.balance_allowance_status == "OK"
    assert len(clob_timeouts) == 2
    assert all(0 < timeout < 1.25 for timeout in clob_timeouts)


def test_main_pre_submit_buy_uses_pusd_payload_without_ctf_enumeration(monkeypatch):
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

    calls: list[str] = []

    class FakePolymarketClient:
        def __init__(self, *, public_http_timeout=None):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def v2_preflight(self):
            return {"ok": True}

        def _ensure_v2_adapter(self):
            return self

        def get_pusd_collateral_payload(self):
            calls.append("pusd")
            return {
                "pusd_balance_micro": 25_000_000,
                "pusd_allowance_micro": 25_000_000,
                "ctf_token_balances_units": {},
                "ctf_token_allowances_units": {},
            }

        def get_collateral_payload(self):
            calls.append("full")
            raise AssertionError("BUY pre-submit proof must not enumerate CTF positions")

    monkeypatch.setattr(polymarket_client, "PolymarketClient", FakePolymarketClient)
    provider = main._edli_pre_submit_authority_provider_from_book_evidence_conn(
        conn,
        {
            "pre_submit_max_quote_age_ms": 1000,
            "pre_submit_balance_allowance_check_enabled": True,
        },
    )
    final_intent = SimpleNamespace(
        payload={
            "token_id": "yes-1",
            "side": "BUY",
            "tick_size": 0.01,
            "min_order_size": 1.0,
            "neg_risk": False,
            "notional_usd": 5.0,
        }
    )

    witness = provider(final_intent, object(), datetime(2026, 5, 25, 12, tzinfo=timezone.utc))

    assert witness.balance_allowance_status == "OK"
    assert calls == ["pusd"]


def test_main_pre_submit_collateral_payload_timeout_fails_closed(monkeypatch):
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
    monkeypatch.setenv("ZEUS_PRE_SUBMIT_CLOB_TIMEOUT_SECONDS", "0.05")
    release = threading.Event()

    class FakePolymarketClient:
        def __init__(self, *, public_http_timeout=None):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def v2_preflight(self):
            return {"ok": True}

        def _ensure_v2_adapter(self):
            return self

        def get_collateral_payload(self):
            release.wait(timeout=5.0)
            return {
                "pusd_balance_micro": 25_000_000,
                "pusd_allowance_micro": 25_000_000,
            }

    monkeypatch.setattr(polymarket_client, "PolymarketClient", FakePolymarketClient)
    provider = main._edli_pre_submit_authority_provider_from_book_evidence_conn(
        conn,
        {
            "pre_submit_max_quote_age_ms": 1000,
            "pre_submit_balance_allowance_check_enabled": True,
        },
    )
    final_intent = SimpleNamespace(
        payload={
            "token_id": "yes-1",
            "side": "BUY",
            "tick_size": 0.01,
            "min_order_size": 1.0,
            "neg_risk": False,
            "notional_usd": 5.0,
        }
    )

    started = time.monotonic()
    try:
        with pytest.raises(TimeoutError, match="timeout_guard: pre_submit_collateral_payload"):
            provider(final_intent, object(), datetime(2026, 5, 25, 12, tzinfo=timezone.utc))
    finally:
        release.set()
        conn.close()

    assert time.monotonic() - started < 1.0


def test_main_pre_submit_jit_book_provider_uses_decoupled_bounded_timeout(monkeypatch):
    """The JIT book client must be built with an explicit connect/read Timeout
    whose connect budget clears the measured ~2.2-2.7s cold-handshake floor while
    connect+read stays under the outer daemon guard (corrected 2026-06-22: the old
    coupled scalar < 1.25s timed out 118/120 cold handshakes)."""
    import httpx

    import src.data.polymarket_client as polymarket_client
    import src.main as main

    captured = {}

    class FakePolymarketClient:
        def __init__(self, *, public_http_timeout=None, public_http_limits=None):
            captured["public_http_timeout"] = public_http_timeout
            captured["public_http_limits"] = public_http_limits

        def get_orderbook_snapshot(self, token_id):
            return {"hash": "book-hash", "bids": [{"price": "0.40"}], "asks": [{"price": "0.42"}]}

        def warm_public_connection(self, *, timeout=None):
            return True

        def close(self):
            pass

    monkeypatch.setattr(polymarket_client, "PolymarketClient", FakePolymarketClient)
    # Realistic outer guard (production default) so a connect budget exceeding the
    # cold-handshake floor is expressible (NOT under an artificially tight guard).
    monkeypatch.setenv("ZEUS_PRE_SUBMIT_CLOB_TIMEOUT_SECONDS", "6.0")

    main._edli_reset_pre_submit_jit_clob_client()
    try:
        provider = main._edli_pre_submit_jit_book_quote_provider()
        assert provider("yes-1")["hash"] == "book-hash"
        t = captured["public_http_timeout"]
        assert isinstance(t, httpx.Timeout), "JIT client must receive an explicit httpx.Timeout"
        # Strict submit profile: the boot pre-warm + keepalive pinger keep the socket
        # warm, so this connect budget is a fail-closed bound. httpcore double-applies
        # connect to TCP+TLS, so 2*connect+read+write+pool must stay under outer 6.0s.
        assert 2 * t.connect + t.read + t.write + t.pool < 6.0, (
            f"worst-case inner budget {2 * t.connect + t.read + t.write + t.pool:.2f}s "
            "must stay under outer guard 6.0s"
        )
    finally:
        main._edli_reset_pre_submit_jit_clob_client()


def test_main_pre_submit_inner_io_timeout_stays_inside_outer_guard(monkeypatch):
    import src.main as main

    monkeypatch.delenv("ZEUS_PRE_SUBMIT_CLOB_TIMEOUT_SECONDS", raising=False)
    monkeypatch.delenv("ZEUS_PRE_SUBMIT_INNER_IO_TIMEOUT_SECONDS", raising=False)
    timeout = main._edli_pre_submit_inner_io_timeout_seconds()

    assert 0 < timeout <= 2.0
    assert timeout * 2.0 < main._edli_pre_submit_clob_timeout_seconds()


def test_main_pre_submit_authority_provider_blocks_insufficient_buy_allowance(monkeypatch):
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
        def __init__(self, *, public_http_timeout=None):
            self.public_http_timeout = public_http_timeout

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def v2_preflight(self):
            return {"ok": True}

        def _ensure_v2_adapter(self):
            return self

        def get_collateral_payload(self):
            return {
                "pusd_balance_micro": 25_000_000,
                "pusd_allowance_micro": 1_000_000,
                "ctf_token_balances_units": {"yes-1": 25.0},
                "ctf_token_allowances_units": {"yes-1": 25.0},
            }

    monkeypatch.setattr(polymarket_client, "PolymarketClient", FakePolymarketClient)
    provider = main._edli_pre_submit_authority_provider_from_book_evidence_conn(
        conn,
        {
            "pre_submit_max_quote_age_ms": 1000,
            "pre_submit_balance_allowance_check_enabled": True,
        },
    )
    final_intent = SimpleNamespace(
        payload={
            "token_id": "yes-1",
            "side": "BUY",
            "tick_size": 0.01,
            "min_order_size": 1.0,
            "neg_risk": False,
            "notional_usd": 5.0,
        }
    )

    with pytest.raises(ValueError, match="PRE_SUBMIT_PUSD_ALLOWANCE_INSUFFICIENT"):
        provider(final_intent, object(), datetime(2026, 5, 25, 12, tzinfo=timezone.utc))


def test_main_pre_submit_authority_provider_blocks_venue_connectivity_failure(monkeypatch):
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
        def __init__(self, *, public_http_timeout=None):
            self.public_http_timeout = public_http_timeout

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def v2_preflight(self):
            raise ValueError("POLYMARKET_PREFLIGHT_DOWN")

        def _ensure_v2_adapter(self):
            return self

        def get_collateral_payload(self):
            return {
                "pusd_balance_micro": 25_000_000,
                "pusd_allowance_micro": 25_000_000,
                "ctf_token_balances_units": {"yes-1": 25.0},
                "ctf_token_allowances_units": {"yes-1": 25.0},
            }

    monkeypatch.setattr(polymarket_client, "PolymarketClient", FakePolymarketClient)
    provider = main._edli_pre_submit_authority_provider_from_book_evidence_conn(
        conn,
        {
            "pre_submit_max_quote_age_ms": 1000,
            "pre_submit_balance_allowance_check_enabled": True,
        },
    )
    final_intent = SimpleNamespace(
        payload={
            "token_id": "yes-1",
            "side": "BUY",
            "tick_size": 0.01,
            "min_order_size": 1.0,
            "neg_risk": False,
            "notional_usd": 5.0,
        }
    )

    with pytest.raises(ValueError, match="POLYMARKET_PREFLIGHT_DOWN"):
        provider(final_intent, object(), datetime(2026, 5, 25, 12, tzinfo=timezone.utc))


def _accepted_receipt(event, *, execution_mode_intent="TAKER", maker_limit_price=None,
                      rest_then_cross_policy=None):
    # P0 mode-authority (operator review 2026-06-10): a real accepted receipt that
    # reaches the FINAL command builder ALWAYS carries the selected proof's PROVEN
    # execution_mode_intent (the live path writes it from proof.execution_mode_intent).
    # The fixture declares it so the final-stage validator (_validate_final_order_mode_or_abort)
    # confirms the proven mode against the fresh book instead of failing closed on a missing
    # mode. Default TAKER: this fixture's economics (q_live 0.7 vs reservation 0.4 — a large
    # edge — on the tight fresh book bid 0.39 / ask 0.40) make the rest-then-cross policy
    # favor crossing, so the proven mode IS TAKER. A maker-proof test passes
    # execution_mode_intent="MAKER" (and a non-crossing book) to declare the proven maker mode.
    #
    # Wave-1 2026-06-12: the canary force-taker knob is DELETED — the proof's
    # rest_then_cross_policy is the SINGLE mode authority validated against the fresh book.
    # A TAKER proof therefore declares a TAKER_* policy lane so the fresh re-derivation
    # (_fresh_rest_then_cross_mode -> select_rest_then_cross_mode) is consistent with it.
    if rest_then_cross_policy is None:
        rest_then_cross_policy = (
            "TAKER_FLEETING_EDGE" if execution_mode_intent == "TAKER" else "REST_DEFAULT"
        )
    from src.events.reactor import EventSubmissionReceipt

    is_day0_event = getattr(event, "event_type", None) == "DAY0_EXTREME_UPDATED"
    qkernel_cert = _qkernel_execution_cert(
        payoff_q_point=0.7,
        payoff_q_lcb=0.6,
        cost=0.4,
    )
    day0_probability_authority = None
    q_source = "emos"
    if is_day0_event:
        q_source = "day0_remaining_day"
        day0_probability_authority = _day0_probability_authority()
        qkernel_cert.update(
            {
                "q_lcb_guard_basis": "DAY0_REMAINING_DAY_Q_LCB",
                "q_lcb_guard_cell_key": "day0_remaining_day_q_lcb",
                "selection_guard_basis": "DAY0_REMAINING_DAY_Q_LCB",
                "selection_guard_cell_key": "day0_remaining_day_q_lcb",
                "selection_guard_q_safe": qkernel_cert["payoff_q_lcb"],
            }
        )
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
        strategy_key="center_buy",
        city="Chicago",
        target_date="2026-05-24",
        metric="high",
        bin_label="80-82",
        outcome_label="YES",
        unit="F",
        q_live=0.7,
        q_lcb_5pct=0.6,
        c_fee_adjusted=0.4,
        c_cost_95pct=0.45,
        p_fill_lcb=0.1,
        trade_score=0.2,
        min_expected_profit_usd=0.05,
        min_submit_edge_density=0.02,
        native_quote_available=True,
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
        q_source=q_source,
        selection_authority_applied="qkernel_spine",
        candidate_bin_id="bin-1",
        qkernel_execution_economics=qkernel_cert,
        day0_probability_authority=day0_probability_authority,
        execution_mode_intent=execution_mode_intent,
        rest_then_cross_policy=rest_then_cross_policy,
        maker_limit_price=maker_limit_price,
        opportunity_book=_opportunity_book_with_qkernel_cert(qkernel_cert),
        decision_proof_bundle=object(),
    )


def _day0_lcb_transform() -> dict[str, object]:
    return {
        "yes_lcb_by_condition": {"condition-1": 0.6},
        "no_lcb_by_condition": {"condition-1": 0.2},
        "mask": [1.0],
        "absorbing_yes_conditions": [],
        "absorbing_no_conditions": [],
        "staleness_suppressed_conditions": [],
        "immature_finite_yes_suppressed_conditions": [],
        "day0_exit_authority_status": "mature",
        "day0_exit_authority_reason": "day0_high_extreme_post_peak",
        "rounded_extreme": 80.0,
        "metric": "high",
    }


def _day0_probability_authority() -> dict[str, object]:
    return {
        "q_source": "day0_remaining_day",
        "q_mode": "remaining_day",
        "remaining_models": 3,
        "remaining_model_names": ["ecmwf", "gfs", "icon"],
        "remaining_source_cycle_time_utc": "2026-05-24T12:00:00+00:00",
        "remaining_capture_times_utc": ["2026-05-24T18:00:00+00:00"],
        "exit_authority_status": "mature",
        "exit_authority_reason": "day0_high_extreme_post_peak",
        "observed_extreme_native": 80.0,
        "rounded_value": 80,
        "observation_time": "2026-05-24T18:00:00+00:00",
        "observation_available_at": "2026-05-24T18:01:00+00:00",
        "lcb_transform": _day0_lcb_transform(),
    }


def _qkernel_execution_cert(**overrides):
    cert = {
        "source": "qkernel_spine",
        "candidate_id": "DIRECT_YES:bin-1",
        "route_id": "DIRECT_YES:bin-1@proof",
        "payoff_q_point": 0.72,
        "payoff_q_lcb": 0.72,
        "edge_lcb": 0.17,
        "delta_u_at_min": 0.01,
        "optimal_stake_usd": "15.39",
        "optimal_delta_u": 0.03,
        "q_dot_payoff": 0.72,
        "cost": 0.55,
        "false_edge_rate": 0.02,
        "side": "YES",
        "bin_id": "bin-1",
        "direction_law_ok": True,
        "coherence_allows": True,
        "q_lcb_guard_basis": "OOF_WILSON_95",
        "q_lcb_guard_abstained": False,
        "q_lcb_guard_cell_key": "high|L2_3|YES|modal|qb4|coarse_global",
        "selection_guard_basis": "SELECTION_BETA_95",
        "selection_guard_abstained": False,
        "selection_guard_cell_key": "YES|L2_3|modal|pb4",
        "selection_guard_n": 80,
        "selection_guard_q_safe": 0.72,
    }
    cert.update(overrides)
    if (
        ("payoff_q_lcb" in overrides or "cost" in overrides)
        and "edge_lcb" not in overrides
    ):
        cert["edge_lcb"] = float(cert["payoff_q_lcb"]) - float(cert["cost"])
    if "payoff_q_lcb" in overrides and "payoff_q_point" not in overrides:
        cert["payoff_q_point"] = max(
            float(cert["payoff_q_point"]),
            float(cert["payoff_q_lcb"]),
        )
    if "payoff_q_point" in overrides and "q_dot_payoff" not in overrides:
        cert["q_dot_payoff"] = cert["payoff_q_point"]
    if "payoff_q_lcb" in overrides and "selection_guard_q_safe" not in overrides:
        cert["selection_guard_q_safe"] = cert["payoff_q_lcb"]
    return cert


def _opportunity_book_with_qkernel_cert(qkernel_cert):
    return {
        "selected_candidate_id": "candidate-1",
        "actual_receipt_selected_candidate_id": "candidate-1",
        "candidates": [
            {
                "candidate_id": "candidate-1",
                "condition_id": "condition-1",
                "token_id": "yes-1",
                "direction": "buy_yes",
                "live_decision_selected": True,
                "live_selection_authority": "qkernel_spine",
                "admitted": True,
                "qkernel_execution_economics": qkernel_cert,
            }
        ],
    }


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
            ParentEdge(
                __import__("re").sub(r"(?<!^)(?=[A-Z])", "_", parent.certificate_type.removesuffix("Certificate")).lower(),
                parent.certificate_hash,
                parent.certificate_type,
            )
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


def _table_count(conn, table_name):
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table_name,),
    ).fetchone()
    if row is None:
        return 0
    return conn.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0]


def _insert_live_order_event(
    conn,
    *,
    aggregate_id,
    sequence,
    event_type,
    payload,
    occurred_at="2026-05-24T18:10:00+00:00",
):
    from src.decision_kernel.canonicalization import canonical_json, stable_hash
    from src.state.schema.edli_live_order_events_schema import ensure_tables

    ensure_tables(conn)
    payload_json = canonical_json(payload)
    payload_hash = stable_hash(payload)
    event_hash = stable_hash(
        {
            "aggregate_id": aggregate_id,
            "event_sequence": sequence,
            "event_type": event_type,
            "payload_hash": payload_hash,
            "occurred_at": occurred_at,
        }
    )
    conn.execute(
        """
        INSERT INTO edli_live_order_events (
            aggregate_event_id, aggregate_id, event_sequence, event_type,
            parent_event_hash, event_hash, payload_json, payload_hash,
            source_authority, occurred_at, created_at, schema_version
        ) VALUES (?, ?, ?, ?, NULL, ?, ?, ?, ?, ?, ?, 1)
        """,
        (
            "edli_live_order_event:" + event_hash[:32],
            aggregate_id,
            sequence,
            event_type,
            event_hash,
            payload_json,
            payload_hash,
            "engine_adapter",
            occurred_at,
            occurred_at,
        ),
    )


def _fake_candidate_proof(
    *,
    condition_id,
    token_id,
    direction,
    limit_price,
    trade_score,
    q_lcb_5pct,
    min_tick_size=0.01,
):
    return SimpleNamespace(
        candidate=SimpleNamespace(condition_id=condition_id),
        token_id=token_id,
        direction=direction,
        execution_price=SimpleNamespace(value=limit_price),
        row={"min_tick_size": min_tick_size},
        trade_score=trade_score,
        q_posterior=q_lcb_5pct,
        q_lcb_5pct=q_lcb_5pct,
        c_cost_95pct=limit_price,
        p_fill_lcb=q_lcb_5pct,
        p_value=0.01,
        passed_prefilter=True,
        native_quote_available=True,
        missing_reason=None,
    )


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


def _operator_arm():
    # FIX-2b (PR_SPEC.md §2): the EDLI live submit adapter now requires the operator-
    # arm capability token for ANY real submit. Tests that exercise the executor path
    # mint the token exactly as main.py does (require_operator_arm with the operator
    # flag True), so they continue to reach the executor seam under the new gate.
    from src.main import require_operator_arm

    return require_operator_arm({"edli_live_operator_authorized": True})


def _healthy_entry_live_health_provider(decision_time: datetime):
    def _provider():
        from src.engine import event_reactor_adapter as adapter

        surfaces = {
            surface: {"ok": True, "issue": None}
            for surface in adapter._ENTRY_LIVE_HEALTH_REQUIRED_SURFACES
        }
        # Business-plane health is observability only and must not self-lock
        # entry authority when every provenance/runtime/monitor surface is OK.
        surfaces["business_plane"] = {
            "ok": False,
            "issue": "CANDIDATES_ONLY_NO_TRADE_NO_CAPITAL_FLOW",
        }
        return {
            "computed_at": decision_time.astimezone(timezone.utc).isoformat(),
            "failing_surfaces": ["business_plane"],
            "healthy": False,
            "status": "DEGRADED",
            "surfaces": surfaces,
        }

    return _provider


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


def _day0_event():
    from src.events.opportunity_event import make_opportunity_event

    payload = {
        "city": "Chicago",
        "target_date": "2026-05-24",
        "metric": "high",
        "settlement_source": "aviationweather_metar",
        "station_id": "KORD",
        "observation_time": "2026-05-24T18:00:00+00:00",
        "observation_available_at": "2026-05-24T18:01:00+00:00",
        "raw_value": 80.06,
        "rounded_value": 80,
        "high_so_far": 80.06,
        "source_match_status": "MATCH",
        "station_match_status": "MATCH",
        "local_date_status": "MATCH",
        "dst_status": "UNAMBIGUOUS",
        "metric_match_status": "MATCH",
        "rounding_status": "MATCH",
        "source_authorized_status": "AUTHORIZED",
        "live_authority_status": "live",
        "source_id": "opendata",
        "source_run_id": "run-1",
        "required_fields_present": True,
        "required_steps_present": True,
        "completeness_status": "COMPLETE",
    }
    return make_opportunity_event(
        event_type="DAY0_EXTREME_UPDATED",
        entity_key="Chicago|2026-05-24|high|KORD",
        source="day0_extreme_updated_trigger",
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
    current_best_ask: float = 0.40,
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


# ---------------------------------------------------------------------------
# GATE #84 — pre-submit book authority freshness root cause
# Created: 2026-06-01
# Last reused/audited: 2026-06-01
# Authority basis: Task #84 — quote_age_ms exceeds max_quote_age_ms root cause.
#
# Root cause (evidence-first, against live state/ DBs 2026-06-01):
#   The feasibility feed (execution_feasibility_evidence) IS alive and writes
#   continuously, BUT its `quote_seen_at` is the venue book-change timestamp
#   (1s resolution, frequently MINUTES stale for slow weather books — measured
#   created_at − quote_seen_at ≈ 71s on live rows). The pre-submit gate measures
#   quote_age_ms = decision_time − quote_seen_at, so it rejects with
#   "quote_age_ms exceeds max_quote_age_ms" even though the book CONTENT is current.
#   Per-token WS tick gaps (median ~11s for candidates) and the venue-stamp lag
#   make the 1000ms bound unsatisfiable from the shared-feed DB row.
#
# Correct design: the 1000ms bound is a SUBMIT-TIME observation freshness bound.
#   For the ONE selected candidate at submit, JIT-fetch its book and anchor
#   quote_seen_at to OUR observation time (when we pulled the live book the FOK
#   will cross against). The venue hash stays as book_hash provenance. Fail-closed
#   if no fresh observation is obtainable.
# ---------------------------------------------------------------------------


def _gate84_world_conn_with_stale_row(*, quote_seen_at: str):
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
        ("yes-1", quote_seen_at, "venue-book-hash-STALE", 0.39, 0.41),
    )
    return conn


def _gate84_final_intent(*, side: str = "BUY"):
    return SimpleNamespace(
        payload={
            "token_id": "yes-1",
            "side": side,
            "tick_size": 0.01,
            "min_order_size": 1.0,
            "neg_risk": False,
            "notional_usd": 5.0,
        }
    )


def _gate84_patch_authority_guards(monkeypatch):
    import src.control.heartbeat_supervisor as heartbeat_supervisor
    import src.control.ws_gap_guard as ws_gap_guard
    import src.data.polymarket_client as polymarket_client

    monkeypatch.setattr(heartbeat_supervisor, "summary", lambda: {"entry": {"allow_submit": True}})
    monkeypatch.setattr(ws_gap_guard, "summary", lambda *, now=None: {"entry": {"allow_submit": True}})

    class FakePolymarketClient:
        def __init__(self, *, public_http_timeout=None):
            self.public_http_timeout = public_http_timeout

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def v2_preflight(self):
            return {"ok": True}

        def _ensure_v2_adapter(self):
            return self

        def get_collateral_payload(self):
            return {
                "pusd_balance_micro": 25_000_000,
                "pusd_allowance_micro": 25_000_000,
                "ctf_token_balances_units": {"yes-1": 25.0},
                "ctf_token_allowances_units": {"yes-1": 25.0},
            }

    monkeypatch.setattr(polymarket_client, "PolymarketClient", FakePolymarketClient)


def test_gate84_jit_book_quote_makes_quote_age_satisfiable_for_stale_db_row(monkeypatch):
    """RED: a venue-stale DB row (quote_seen_at 71s old) must NOT doom the order.

    With a just-in-time single-token book provider available, the witness must
    anchor freshness to OUR observation time (now), yielding quote_age_ms ~0
    against decision_time — i.e. <= max_quote_age_ms — and carry the freshly
    fetched best bid/ask. This is the structural fix for
    'quote_age_ms exceeds max_quote_age_ms'.
    """
    import src.main as main

    decision_time = datetime(2026, 6, 1, 6, 21, 0, tzinfo=timezone.utc)
    # DB row carries the venue book-change stamp from 71s earlier (the live pathology).
    conn = _gate84_world_conn_with_stale_row(
        quote_seen_at=(decision_time - timedelta(seconds=71)).isoformat()
    )
    _gate84_patch_authority_guards(monkeypatch)

    jit_calls: list[str] = []

    def _jit_book(token_id: str) -> dict:
        jit_calls.append(token_id)
        # Live /book shape: bids/asks lists + venue hash + (coarse) timestamp.
        return {
            "asset_id": token_id,
            "market": "cond-1",
            "hash": "fresh-jit-book-hash",
            "bids": [{"price": "0.40", "size": "50"}],
            "asks": [{"price": "0.42", "size": "50"}],
            "timestamp": str(int((decision_time - timedelta(seconds=71)).timestamp() * 1000)),
        }

    provider = main._edli_pre_submit_authority_provider_from_book_evidence_conn(
        conn,
        {"pre_submit_max_quote_age_ms": 1000, "pre_submit_balance_allowance_check_enabled": True},
        book_quote_provider=_jit_book,
    )

    witness = provider(_gate84_final_intent(), object(), decision_time)

    # JIT fetch for the selected token must have fired.
    assert jit_calls == ["yes-1"]
    # Freshness anchored to observation time -> age within bound.
    quote_seen = datetime.fromisoformat(witness.quote_seen_at)
    checked_at = datetime.fromisoformat(witness.checked_at)
    age_ms = (checked_at - quote_seen).total_seconds() * 1000.0
    assert 0.0 <= age_ms <= witness.max_quote_age_ms, f"age_ms={age_ms} must be <= {witness.max_quote_age_ms}"
    # Fresh book content carried (the JIT book, not the stale DB best bid/ask).
    assert witness.current_best_bid == 0.40
    assert witness.current_best_ask == 0.42
    assert witness.book_hash == "fresh-jit-book-hash"
    assert quote_seen > decision_time


def test_gate84_jit_buy_accepts_ask_only_book(monkeypatch):
    """A BUY submit-time book needs an executable ask, not a two-sided market.

    Thin weather longshots often have no bid but do have a live ask. Requiring a
    bid at the pre-submit authority seam converts a usable buy_yes/buy_no touch
    into PRE_SUBMIT_BOOK_AUTHORITY_STALE after the DB fallback misses; that is a
    false liveness failure, not a real no-edge decision.
    """
    import src.main as main

    decision_time = datetime(2026, 6, 1, 6, 21, 0, tzinfo=timezone.utc)
    conn = _gate84_world_conn_with_stale_row(
        quote_seen_at=(decision_time - timedelta(seconds=71)).isoformat()
    )
    _gate84_patch_authority_guards(monkeypatch)

    provider = main._edli_pre_submit_authority_provider_from_book_evidence_conn(
        conn,
        {"pre_submit_max_quote_age_ms": 1000, "pre_submit_balance_allowance_check_enabled": True},
        book_quote_provider=lambda token_id: {
            "asset_id": token_id,
            "market": "cond-1",
            "hash": "fresh-ask-only-book-hash",
            "bids": [],
            "asks": [{"price": "0.006", "size": "50"}],
        },
    )

    witness = provider(_gate84_final_intent(side="BUY"), object(), decision_time)

    assert witness.book_authority_id == "clob_jit_book"
    assert witness.current_best_bid is None
    assert witness.current_best_ask == 0.006
    assert witness.book_hash == "fresh-ask-only-book-hash"
    quote_seen = datetime.fromisoformat(witness.quote_seen_at)
    checked_at = datetime.fromisoformat(witness.checked_at)
    assert 0.0 <= (checked_at - quote_seen).total_seconds() * 1000.0 <= witness.max_quote_age_ms


def test_gate84_jit_buy_rejects_book_without_ask(monkeypatch):
    import src.main as main

    decision_time = datetime(2026, 6, 1, 6, 21, 0, tzinfo=timezone.utc)
    conn = _gate84_world_conn_with_stale_row(
        quote_seen_at=(decision_time - timedelta(seconds=71)).isoformat()
    )
    _gate84_patch_authority_guards(monkeypatch)

    provider = main._edli_pre_submit_authority_provider_from_book_evidence_conn(
        conn,
        {"pre_submit_max_quote_age_ms": 1000, "pre_submit_balance_allowance_check_enabled": True},
        book_quote_provider=lambda token_id: {
            "asset_id": token_id,
            "market": "cond-1",
            "hash": "fresh-bid-only-book-hash",
            "bids": [{"price": "0.005", "size": "50"}],
            "asks": [],
        },
    )

    with pytest.raises(ValueError, match="PRE_SUBMIT_BOOK_AUTHORITY"):
        provider(_gate84_final_intent(side="BUY"), object(), decision_time)


def test_gate84_db_fallback_buy_accepts_fresh_ask_only_row(monkeypatch):
    import src.main as main

    decision_time = datetime(2026, 6, 1, 6, 21, 0, tzinfo=timezone.utc)
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
        (
            "yes-1",
            (decision_time - timedelta(milliseconds=200)).isoformat(),
            "fresh-db-ask-only-book",
            None,
            0.006,
        ),
    )
    _gate84_patch_authority_guards(monkeypatch)

    provider = main._edli_pre_submit_authority_provider_from_book_evidence_conn(
        conn,
        {"pre_submit_max_quote_age_ms": 1000, "pre_submit_balance_allowance_check_enabled": True},
        book_quote_provider=lambda token_id: (_ for _ in ()).throw(RuntimeError("transient /book failure")),
    )

    witness = provider(_gate84_final_intent(side="BUY"), object(), decision_time)

    assert witness.book_authority_id == "execution_feasibility_evidence"
    assert witness.current_best_bid is None
    assert witness.current_best_ask == 0.006
    assert witness.book_hash == "fresh-db-ask-only-book"


def test_gate84_jit_book_uses_fetch_time_when_reactor_decision_time_is_old(monkeypatch):
    """A slow reactor cycle must not make a successful JIT book look stale.

    Live stalls were caused by evaluating a fresh /book fetch against the
    process_pending decision_time captured many seconds earlier. The witness
    must carry the fetch completion time so downstream quote_age_ms is computed
    against the actual observation instant, not the old cycle timestamp.
    """
    import src.main as main
    from src.engine.event_reactor_adapter import _pre_submit_revalidation_payload_from_final_intent

    decision_time = datetime.now(timezone.utc) - timedelta(seconds=25)
    conn = _gate84_world_conn_with_stale_row(
        quote_seen_at=(decision_time - timedelta(seconds=71)).isoformat()
    )
    _gate84_patch_authority_guards(monkeypatch)

    provider = main._edli_pre_submit_authority_provider_from_book_evidence_conn(
        conn,
        {"pre_submit_max_quote_age_ms": 1000, "pre_submit_balance_allowance_check_enabled": True},
        book_quote_provider=lambda token_id: {
            "asset_id": token_id,
            "market": "cond-1",
            "hash": "fresh-jit-book-hash-delayed",
            "bids": [{"price": "0.40", "size": "50"}],
            "asks": [{"price": "0.42", "size": "50"}],
            "timestamp": str(int(decision_time.timestamp() * 1000)),
        },
    )

    witness = provider(_gate84_final_intent(), object(), decision_time)
    final_intent = SimpleNamespace(
        certificate_hash="final-cert",
        payload={
            "event_id": "event-1",
            "event_type": "ForecastSnapshotReady",
            "final_intent_id": "intent-1",
            "strategy_key": "strategy-1",
            "condition_id": "cond-1",
            "token_id": "yes-1",
            "side": "BUY",
            "direction": "buy_yes",
            "city": "City",
            "target_date": "2026-06-01",
            "metric": "high",
            "actionable_certificate_hash": "actionable-cert",
            "cost_basis_hash": "cost-cert",
            "limit_price": 0.42,
            "order_type": "FOK",
            "time_in_force": "FOK",
            "post_only": False,
            "size": 5.0,
        },
    )
    payload = _pre_submit_revalidation_payload_from_final_intent(
        final_intent=final_intent,
        executable_snapshot=object(),
        decision_time=decision_time,
        authority_witness=witness,
    )

    assert witness.book_hash == "fresh-jit-book-hash-delayed"
    assert datetime.fromisoformat(witness.quote_seen_at) > decision_time
    assert payload["quote_age_ms"] <= witness.max_quote_age_ms


def test_pre_submit_payload_carries_receipt_economics_from_final_intent():
    from src.engine.event_reactor_adapter import (
        PreSubmitAuthorityWitness,
        _pre_submit_revalidation_payload_from_final_intent,
    )

    decision_time = datetime(2026, 6, 26, 21, 0, tzinfo=timezone.utc)
    qkernel_economics = {
        "source": "qkernel_spine",
        "route_id": "DIRECT_YES:bin-1@proof",
        "side": "YES",
        "payoff_q_point": 0.62,
        "payoff_q_lcb": 0.58,
        "edge_lcb": 0.08,
        "cost": 0.50,
    }
    final_intent = SimpleNamespace(
        certificate_hash="final-cert",
        payload={
            "event_id": "event-1",
            "event_type": "ForecastSnapshotReady",
            "final_intent_id": "intent-1",
            "strategy_key": "strategy-1",
            "condition_id": "cond-1",
            "token_id": "yes-1",
            "side": "BUY",
            "direction": "buy_yes",
            "city": "City",
            "target_date": "2026-06-28",
            "metric": "high",
            "actionable_certificate_hash": "actionable-cert",
            "cost_basis_hash": "cost-cert",
            "limit_price": 0.50,
            "order_type": "LIMIT",
            "time_in_force": "GTC",
            "post_only": True,
            "size": 10.0,
            "q_live": 0.62,
            "q_lcb_5pct": 0.58,
            "trade_score": 0.08,
            "action_score": 0.07,
            "min_expected_profit_usd": 0.05,
            "min_submit_edge_density": 0.02,
            "c_fee_adjusted": 0.50,
            "c_cost_95pct": 0.50,
            "selection_authority_applied": "qkernel_spine",
            "qkernel_execution_economics": qkernel_economics,
        },
    )
    witness = PreSubmitAuthorityWitness(
        checked_at=decision_time.isoformat(),
        quote_seen_at=(decision_time - timedelta(milliseconds=50)).isoformat(),
        max_quote_age_ms=1000,
        book_hash="book-hash",
        current_best_bid=0.49,
        current_best_ask=0.60,
        tick_size=0.01,
        min_order_size=5.0,
        neg_risk=False,
        heartbeat_status="OK",
        user_ws_status="OK",
        venue_connectivity_status="OK",
        balance_allowance_status="OK",
        book_authority_id="execution_feasibility_evidence",
        book_captured_at=decision_time.isoformat(),
        heartbeat_authority_id="heartbeat_supervisor",
        heartbeat_checked_at=decision_time.isoformat(),
        user_ws_authority_id="ws_gap_guard",
        user_ws_checked_at=decision_time.isoformat(),
        venue_connectivity_authority_id="polymarket_public_orderbook",
        venue_connectivity_checked_at=decision_time.isoformat(),
        balance_allowance_authority_id="polymarket_wallet_readonly",
        balance_allowance_checked_at=decision_time.isoformat(),
    )

    payload = _pre_submit_revalidation_payload_from_final_intent(
        final_intent=final_intent,
        executable_snapshot=object(),
        decision_time=decision_time,
        authority_witness=witness,
    )

    assert payload["q_live"] == pytest.approx(0.62)
    assert payload["q_lcb_5pct"] == pytest.approx(0.58)
    assert payload["expected_edge"] == pytest.approx(0.08)
    assert payload["size"] == pytest.approx(10.0)
    assert payload["min_expected_profit_usd"] == pytest.approx(0.05)
    assert payload["min_submit_edge_density"] == pytest.approx(0.02)
    assert payload["expected_edge_source_certificate_hash"] == "actionable-cert"
    assert payload["cost_basis_source_certificate_hash"] == "cost-cert"
    assert payload["qkernel_execution_economics"] == qkernel_economics


def test_gate84_jit_unavailable_and_db_row_stale_fails_closed(monkeypatch):
    """RED: when the JIT fetch is unavailable/fails AND the only DB row is genuinely
    stale (> max_quote_age_ms), the provider must FAIL CLOSED — never emit a witness
    carrying a stale quote that would pass the gate. No fabricated freshness.
    """
    import src.main as main

    decision_time = datetime(2026, 6, 1, 6, 21, 0, tzinfo=timezone.utc)
    conn = _gate84_world_conn_with_stale_row(
        quote_seen_at=(decision_time - timedelta(seconds=71)).isoformat()
    )
    _gate84_patch_authority_guards(monkeypatch)

    def _jit_book_fails(token_id: str) -> dict:
        raise RuntimeError("CLOB /book 503")

    provider = main._edli_pre_submit_authority_provider_from_book_evidence_conn(
        conn,
        {"pre_submit_max_quote_age_ms": 1000, "pre_submit_balance_allowance_check_enabled": True},
        book_quote_provider=_jit_book_fails,
    )

    with pytest.raises(ValueError, match="PRE_SUBMIT_BOOK_AUTHORITY"):
        provider(_gate84_final_intent(), object(), decision_time)


def test_gate84_jit_unavailable_but_db_row_fresh_uses_db_row(monkeypatch):
    """GREEN-guard: when no JIT provider is wired but the DB row is genuinely fresh
    (within bound), the existing DB-row path still works (backward compatible).
    """
    import src.main as main

    decision_time = datetime(2026, 6, 1, 6, 21, 0, tzinfo=timezone.utc)
    conn = _gate84_world_conn_with_stale_row(
        quote_seen_at=(decision_time - timedelta(milliseconds=200)).isoformat()
    )
    _gate84_patch_authority_guards(monkeypatch)

    provider = main._edli_pre_submit_authority_provider_from_book_evidence_conn(
        conn,
        {"pre_submit_max_quote_age_ms": 1000, "pre_submit_balance_allowance_check_enabled": True},
    )

    witness = provider(_gate84_final_intent(), object(), decision_time)
    quote_seen = datetime.fromisoformat(witness.quote_seen_at)
    age_ms = (decision_time - quote_seen).total_seconds() * 1000.0
    assert 0.0 <= age_ms <= witness.max_quote_age_ms
    assert witness.book_hash == "venue-book-hash-STALE"  # DB-row path preserved
