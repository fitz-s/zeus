# Created: 2026-05-25
# Last reused/audited: 2026-06-19
# Authority basis: docs/operations/edli_v1/EDLI_REDEMPTION_FINAL_PACKAGE_SPEC.md §14 full-live increment.
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest


def test_live_canary_runtime_requires_operator_unshadow_and_submit_guards():
    """Current live-canary contract after operator unshadow.

    This used to assert the pre-unshadow shadow/no-submit state. The operator has
    since authorized real live canary, so the load-bearing guard is now coherent
    real-submit wiring: live mode, live canary, durable outbox, and taker path all
    enabled together rather than a split shadow/live configuration.
    """
    settings = json.loads(Path("config/settings.json").read_text())
    edli = settings["edli"]

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
    # Day0→live promotion 2026-06-12 (task #49): scope flipped day0_shadow →
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
    assert row["cap_scope"] == "tiny_live_canary"
    assert row["reservation_status"] == "RESERVED"
    assert row["reserved_notional_usd"] == cert.payload["reserved_notional_usd"]
    assert row["final_intent_id"] == "intent-1"


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
    accepted = _accepted_receipt(event)
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
    assert payload["opportunity_book"] == {
        "selected_candidate_id": "candidate-1",
        "actual_receipt_selected_candidate_id": "candidate-1",
        "candidates": [
            {
                "candidate_id": "candidate-1",
                "condition_id": "condition-1",
                "token_id": "yes-1",
                "direction": "buy_yes",
            }
        ],
    }
    assert payload["strategy_key"] == "center_buy"


def test_actionable_payload_persists_qkernel_execution_economics():
    from src.engine import event_reactor_adapter as adapter

    event = _forecast_event()
    qkernel_cert = {
        "source": "qkernel_spine",
        "candidate_id": "DIRECT_NO:bin-1",
        "route_id": "DIRECT_NO:bin-1@proof",
        "payoff_q_lcb": 0.72,
        "edge_lcb": 0.17,
        "delta_u_at_min": 0.01,
        "optimal_stake_usd": "15.39",
        "optimal_delta_u": 0.03,
        "cost": 0.55,
    }
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


def test_crossing_post_only_pre_submit_witness_blocks_command():
    # Tests that a POST_ONLY MAKER order whose limit_price >= current_best_ask
    # (i.e. would cross the book) is rejected by the pre-submit verifier with
    # "would_cross_book=false".  The receipt must have low EV (trade_score=0.0,
    # p_fill_lcb=0.0) so the EV boundary selects MAKER (post_only=True);
    # a TAKER order has post_only=False and skips the crossing check by design.
    from src.engine import event_reactor_adapter as adapter
    from src.state.schema.edli_live_cap_usage_schema import ensure_table
    from tests.decision_kernel.no_submit_fixtures import build_test_no_submit_proof_bundle

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    ensure_table(conn)
    event = _forecast_event()
    decision_time = datetime(2026, 5, 24, 18, 10, tzinfo=timezone.utc)
    # Low trade_score + p_fill_lcb keeps EV boundary False → MAKER (post_only=True).
    # With limit_price ~0.4 and witness ask=0.39, would_cross=True → verifier raises.
    # P0 mode-authority: declare the PROVEN maker mode so the fresh-book validator (which
    # also computes MAKER from the low EV) confirms it and proceeds to the would_cross verifier
    # check, rather than aborting on a proof/fresh mode disagreement.
    accepted = replace(
        _accepted_receipt(event, execution_mode_intent="MAKER", maker_limit_price=0.40),
        trade_score=0.0,
        p_fill_lcb=0.0,
    )
    accepted = replace(
        accepted,
        decision_proof_bundle=build_test_no_submit_proof_bundle(event, accepted, decision_time=decision_time),
    )

    with pytest.raises(Exception, match="would_cross_book=false"):
        adapter._build_live_execution_command_certificates(
            event=event,
            receipt=accepted,
            decision_time=decision_time,
            live_cap_conn=conn,
            pre_submit_authority_provider=lambda *_args: _pre_submit_authority_witness(current_best_ask=0.39),
        )


def test_fresh_pre_submit_book_aborts_mode_flip_for_proven_maker_that_would_cross():
    # P0 mode-authority (operator review 2026-06-10) — RE-PURPOSED from the former
    # "promotes_stale_maker_candidate_to_taker" test. The final command builder may NOT
    # promote a proven-MAKER candidate to TAKER on a fresh book that makes crossing newly
    # attractive: that was the validator-bypassing late EV-override flip (a maker that never
    # cleared TAKER recapture full-fee/PRICE_MOVED entering the taker submit path). The fresh
    # tight book (bid 0.39 / ask 0.40) now makes _select_edli_order_mode return TAKER, so a
    # PROVEN-MAKER proof must ABORT SUBMIT_ABORTED_MODE_FLIPPED — NO order built, defer to a
    # full re-rank next cycle.
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
        # Wave-1 2026-06-12 (canary force-taker deleted): the fresh mode is the deadline-aware
        # rest-then-cross policy. To exercise the proven-MAKER-would-cross flip, the snapshot
        # must sit NEAR the deadline so the policy genuinely crosses (fresh=TAKER) on the tight
        # fresh book — the MAKER proof then aborts MODE_FLIPPED. (The shared fixture gives a far
        # horizon to a MAKER proof; this test overrides it to the crossing scenario it targets.)
        executable_snapshot=replace(
            proof_bundle.executable_snapshot,
            payload={
                **proof_bundle.executable_snapshot.payload,
                "market_end_at": (decision_time + timedelta(minutes=5)).isoformat(),
            },
        ),
    )
    accepted = replace(accepted, decision_proof_bundle=proof_bundle)

    # The fresh tight book near the deadline makes the policy cross → fresh mode
    # TAKER vs proven MAKER → mode flip → typed abort, NO certificates built.
    with pytest.raises(adapter._SubmitAbortedModeFlipped, match="SUBMIT_ABORTED_MODE_FLIPPED"):
        adapter._build_live_execution_command_certificates(
            event=event,
            receipt=accepted,
            decision_time=decision_time,
            live_cap_conn=conn,
            pre_submit_authority_provider=lambda *_args: _pre_submit_authority_witness(
                current_best_bid=0.39,
                current_best_ask=0.40,
            ),
        )


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


def test_live_adapter_submit_enabled_canary_enabled_calls_executor_mock(monkeypatch):
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
        )

        receipt = submit(event, datetime(2026, 5, 24, 18, 10, tzinfo=timezone.utc))

        assert called["count"] == 1
        assert receipt.submitted is True
        assert receipt.side_effect_status == "SUBMITTED"
        assert _receipt_status(receipt) == "SUBMITTED"
        assert conn.execute("SELECT reservation_status FROM edli_live_cap_usage").fetchone()["reservation_status"] == "CONSUMED"
        assert _cap_transition_status(receipt) == "CONSUMED"
        assert _cap_transition_projection_status(receipt) == "CONSUMED"
    finally:
        adapter.build_event_bound_no_submit_receipt = original_build


def test_live_submit_aggregate_persists_decision_audit_payload(monkeypatch):
    from src.engine import event_reactor_adapter as adapter
    from src.engine.event_bound_final_intent import EventBoundExecutorSubmitResult
    from src.riskguard.risk_level import RiskLevel
    from tests.decision_kernel.no_submit_fixtures import build_test_no_submit_proof_bundle

    event = _forecast_event()
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
        assert audit["opportunity_book"] == {
            "selected_candidate_id": "candidate-1",
            "actual_receipt_selected_candidate_id": "candidate-1",
            "candidates": [
                {
                    "candidate_id": "candidate-1",
                    "condition_id": "condition-1",
                    "token_id": "yes-1",
                    "direction": "buy_yes",
                }
            ],
        }
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


def test_live_adapter_records_rejected_fixture_response(monkeypatch):
    from src.engine import event_reactor_adapter as adapter
    from src.engine.event_bound_final_intent import EventBoundExecutorSubmitResult
    from src.events.reactor import EventSubmissionReceipt
    from src.riskguard.risk_level import RiskLevel
    from tests.decision_kernel.no_submit_fixtures import build_test_no_submit_proof_bundle

    event = _forecast_event()
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


def test_pre_venue_depth_rejection_terminates_aggregate_and_releases_cap(monkeypatch):
    """RELATIONSHIP (F-class deadlock antibody, 2026-06-01):

    A live order that FAILS the executor's PRE-VENUE depth validation
    (DEPTH_INSUFFICIENT, raised before any venue call) must terminate the
    live-order aggregate AND release its LIVE_CAP reservation — leaving NO
    unresolved-submit and NO held cap. This is the EXACT state that crash-looped
    the edli_live_canary boot readiness gate. Contrast with
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


def test_live_adapter_records_timeout_unknown_fixture_response(monkeypatch):
    from src.engine import event_reactor_adapter as adapter
    from src.engine.event_bound_final_intent import EventBoundExecutorSubmitResult
    from src.events.reactor import EventSubmissionReceipt
    from src.riskguard.risk_level import RiskLevel
    from tests.decision_kernel.no_submit_fixtures import build_test_no_submit_proof_bundle

    event = _forecast_event()
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


def test_live_adapter_records_post_submit_unknown_as_pending_reconcile(monkeypatch):
    from src.engine import event_reactor_adapter as adapter
    from src.engine.event_bound_final_intent import EventBoundExecutorSubmitResult
    from src.riskguard.risk_level import RiskLevel
    from tests.decision_kernel.no_submit_fixtures import build_test_no_submit_proof_bundle

    event = _forecast_event()
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
    assert clob_timeouts == [2.5, 2.5]


def test_main_pre_submit_jit_book_provider_uses_short_http_timeout(monkeypatch):
    import src.data.polymarket_client as polymarket_client
    import src.main as main

    captured = {}

    class FakePolymarketClient:
        def __init__(self, *, public_http_timeout=None):
            captured["public_http_timeout"] = public_http_timeout

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def get_orderbook_snapshot(self, token_id):
            return {"hash": "book-hash", "bids": [{"price": "0.40"}], "asks": [{"price": "0.42"}]}

    monkeypatch.setattr(polymarket_client, "PolymarketClient", FakePolymarketClient)
    monkeypatch.setenv("ZEUS_PRE_SUBMIT_CLOB_TIMEOUT_SECONDS", "2.5")

    provider = main._edli_pre_submit_jit_book_quote_provider()

    assert provider("yes-1")["hash"] == "book-hash"
    assert captured["public_http_timeout"] == 2.5


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
        q_source="emos",
        execution_mode_intent=execution_mode_intent,
        rest_then_cross_policy=rest_then_cross_policy,
        maker_limit_price=maker_limit_price,
        opportunity_book={
            "selected_candidate_id": "candidate-1",
            "actual_receipt_selected_candidate_id": "candidate-1",
            "candidates": [
                {
                    "candidate_id": "candidate-1",
                    "condition_id": "condition-1",
                    "token_id": "yes-1",
                    "direction": "buy_yes",
                }
            ],
        },
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


def _gate84_final_intent():
    return SimpleNamespace(
        payload={
            "token_id": "yes-1",
            "side": "BUY",
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

    provider = main._edli_pre_submit_authority_provider_from_world_conn(
        conn,
        {"pre_submit_max_quote_age_ms": 1000, "pre_submit_balance_allowance_check_enabled": True},
        book_quote_provider=_jit_book,
    )

    witness = provider(_gate84_final_intent(), object(), decision_time)

    # JIT fetch for the selected token must have fired.
    assert jit_calls == ["yes-1"]
    # Freshness anchored to observation time -> age within bound.
    quote_seen = datetime.fromisoformat(witness.quote_seen_at)
    age_ms = (decision_time - quote_seen).total_seconds() * 1000.0
    assert 0.0 <= age_ms <= witness.max_quote_age_ms, f"age_ms={age_ms} must be <= {witness.max_quote_age_ms}"
    # Fresh book content carried (the JIT book, not the stale DB best bid/ask).
    assert witness.current_best_bid == 0.40
    assert witness.current_best_ask == 0.42
    assert witness.book_hash == "fresh-jit-book-hash"


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

    provider = main._edli_pre_submit_authority_provider_from_world_conn(
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

    provider = main._edli_pre_submit_authority_provider_from_world_conn(
        conn,
        {"pre_submit_max_quote_age_ms": 1000, "pre_submit_balance_allowance_check_enabled": True},
    )

    witness = provider(_gate84_final_intent(), object(), decision_time)
    quote_seen = datetime.fromisoformat(witness.quote_seen_at)
    age_ms = (decision_time - quote_seen).total_seconds() * 1000.0
    assert 0.0 <= age_ms <= witness.max_quote_age_ms
    assert witness.book_hash == "venue-book-hash-STALE"  # DB-row path preserved
