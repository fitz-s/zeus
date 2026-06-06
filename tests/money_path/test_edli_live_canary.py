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


def test_live_canary_runtime_stays_in_shadow_no_submit_until_operator_unshadow():
    """SHADOW-contract money guard (2026-05-30).

    The reactor runs in shadow (edli_shadow_no_submit): it forms decisions/candidates with NO
    venue submission so the p_raw-vs-online bias test (#24) can run on real flow. The
    load-bearing money guard is ``real_order_submit_enabled is False`` plus both write-side
    triggers (day0, market-channel) staying off — these must hold until the operator's
    irreversible unshadow. Supersedes the prior fully-disabled canary, which predated the
    deliberate shadow launch.
    """
    settings = json.loads(Path("config/settings.json").read_text())
    edli = settings["edli_v1"]

    # MONEY GUARD — no real capital can leave until operator unshadow.
    assert edli["real_order_submit_enabled"] is False
    assert edli["live_execution_mode"] == "edli_shadow_no_submit"
    assert edli["reactor_mode"] == "live_no_submit"
    # Shadow surfaces that are intentionally ON to produce decisions for the bias test.
    assert edli["enabled"] is True
    assert edli["event_writer_enabled"] is True
    assert edli["forecast_snapshot_trigger_enabled"] is True
    # Write-side venue triggers stay OFF in shadow.
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


def test_live_cap_provisional_and_durable_share_uncapped_normalization(monkeypatch):
    from copy import deepcopy

    from src.engine import event_reactor_adapter as adapter
    from src.events.reactor import EventSubmissionReceipt

    settings_copy = deepcopy(adapter.settings)
    settings_copy["edli_v1"]["tiny_live_notional_cap_enabled"] = False
    settings_copy["edli_v1"]["tiny_live_daily_order_cap_enabled"] = False
    monkeypatch.setattr(adapter, "settings", settings_copy)

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
        max_notional_usd=5.0,
        live_cap_conn=conn,
    )

    provisional = adapter._build_live_cap_certificate_from_ledger(**kwargs, persist=False)
    durable = adapter._build_live_cap_certificate_from_ledger(**kwargs, persist=True)

    assert provisional.payload["reserved_notional_usd"] == 800.0
    assert durable.payload["reserved_notional_usd"] == 800.0
    assert durable.payload["reserved_notional_usd"] == provisional.payload["reserved_notional_usd"]
    assert durable.payload["max_notional_usd"] == provisional.payload["max_notional_usd"] == 800.0


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
        taker_fok_fak_live_enabled=True,
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
        tiny_live_max_notional_usd=5.0,
        live_cap_conn=conn,
        pre_submit_authority_provider=_pre_submit_authority_provider,
        taker_fok_fak_live_enabled=True,
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


def test_locked_live_opportunity_suppresses_redecision_without_price_improvement():
    from src.engine import event_reactor_adapter as adapter

    conn = sqlite3.connect(":memory:")
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
    _insert_live_order_event(
        conn,
        aggregate_id="aggregate-1",
        sequence=2,
        event_type="ExecutionCommandCreated",
        payload={
            "event_id": "event-1",
            "final_intent_id": "intent-1",
            "execution_command_id": "command-1",
        },
    )

    unchanged = adapter._locked_live_opportunity_no_price_improvement_reason(
        conn,
        condition_id="condition-1",
        token_id="token-no-1",
        direction="buy_no",
        side="BUY",
        limit_price=0.70,
    )
    one_tick_better = adapter._locked_live_opportunity_no_price_improvement_reason(
        conn,
        condition_id="condition-1",
        token_id="token-no-1",
        direction="buy_no",
        side="BUY",
        limit_price=0.69,
    )
    materially_better = adapter._locked_live_opportunity_no_price_improvement_reason(
        conn,
        condition_id="condition-1",
        token_id="token-no-1",
        direction="buy_no",
        side="BUY",
        limit_price=0.68,
    )

    assert unchanged is not None
    assert one_tick_better is not None
    assert materially_better is None


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


def test_submit_disabled_redecision_returns_no_submit_for_locked_same_price(monkeypatch):
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
        taker_fok_fak_live_enabled=True,
    )

    first = submit(event_1, datetime(2026, 5, 24, 18, 10, tzinfo=timezone.utc))
    event_count_after_first = _table_count(conn, "edli_live_order_events")
    second = submit(event_2, datetime(2026, 5, 24, 18, 11, tzinfo=timezone.utc))

    assert first.side_effect_status == "SUBMIT_DISABLED"
    assert event_count_after_first == 6
    assert second.side_effect_status == "NO_SUBMIT"
    assert second.reason.startswith("EDLI_LOCKED_OPPORTUNITY_NO_PRICE_IMPROVEMENT")
    assert _table_count(conn, "edli_live_order_events") == event_count_after_first


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
        taker_fok_fak_live_enabled=True,
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
            tiny_live_max_notional_usd=5.0,
            live_cap_conn=conn,
            taker_fok_fak_live_enabled=True,
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
    accepted = replace(
        _accepted_receipt(event),
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
            tiny_live_max_notional_usd=5.0,
            live_cap_conn=conn,
            pre_submit_authority_provider=lambda *_args: _pre_submit_authority_witness(current_best_ask=0.39),
        )


def test_fresh_pre_submit_book_promotes_stale_maker_candidate_to_taker():
    from src.decision_kernel import claims
    from src.engine import event_reactor_adapter as adapter
    from tests.decision_kernel.no_submit_fixtures import build_test_no_submit_proof_bundle

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    event = _forecast_event()
    decision_time = datetime(2026, 5, 24, 18, 10, tzinfo=timezone.utc)
    accepted = replace(
        _accepted_receipt(event),
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
    )
    accepted = replace(accepted, decision_proof_bundle=proof_bundle)

    certs = adapter._build_live_execution_command_certificates(
        event=event,
        receipt=accepted,
        decision_time=decision_time,
        tiny_live_max_notional_usd=5.0,
        live_cap_conn=conn,
        pre_submit_authority_provider=lambda *_args: _pre_submit_authority_witness(
            current_best_bid=0.39,
            current_best_ask=0.40,
        ),
        taker_fok_fak_live_enabled=True,
    )

    final_intent = next(c for c in certs if getattr(c, "certificate_type", None) == claims.FINAL_INTENT)
    pre_submit = next(c for c in certs if getattr(c, "certificate_type", None) == claims.PRE_SUBMIT_REVALIDATION)

    assert final_intent.payload["order_mode"] == "TAKER"
    assert final_intent.payload["post_only"] is False
    assert final_intent.payload["time_in_force"] in {"FOK", "FAK"}
    assert final_intent.payload["limit_price"] == pytest.approx(0.40)
    assert pre_submit.payload["would_cross_book"] is True
    assert pre_submit.payload["post_only"] is False


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
        tiny_live_max_notional_usd=5.0,
        live_cap_conn=conn,
        pre_submit_authority_provider=_provider,
        taker_fok_fak_live_enabled=True,
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
            )

        submit = adapter.event_bound_live_adapter_from_trade_conn(
            conn,
            live_cap_conn=conn,
            get_current_level=lambda: RiskLevel.GREEN,
            real_order_submit_enabled=True,
            live_canary_enabled=True,
            durable_submit_outbox_enabled=True,
            executor_submit=_submit,
            pre_submit_authority_provider=_pre_submit_authority_provider,
            taker_fok_fak_live_enabled=True,
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
            live_canary_enabled=True,
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
            live_canary_enabled=True,
            durable_submit_outbox_enabled=True,
            executor_submit=lambda _final_intent, _command: EventBoundExecutorSubmitResult(
                status="REJECTED",
                reason_code="VENUE_REJECTED",
                submit_started_at="2026-05-24T18:10:00+00:00",
                submit_finished_at="2026-05-24T18:10:01+00:00",
                raw_response={"status": "rejected"},
            ),
            pre_submit_authority_provider=_pre_submit_authority_provider,
            taker_fok_fak_live_enabled=True,
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
            live_canary_enabled=True,
            durable_submit_outbox_enabled=True,
            taker_fok_fak_live_enabled=True,
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
            live_canary_enabled=True,
            durable_submit_outbox_enabled=True,
            executor_submit=lambda _final_intent, _command: EventBoundExecutorSubmitResult(
                status="TIMEOUT_UNKNOWN",
                reason_code="SUBMIT_TIMEOUT",
                submit_started_at="2026-05-24T18:10:00+00:00",
                submit_finished_at="2026-05-24T18:10:30+00:00",
                raw_response={"status": "timeout"},
                reconciliation_followup_required=True,
            ),
            pre_submit_authority_provider=_pre_submit_authority_provider,
            taker_fok_fak_live_enabled=True,
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
            live_canary_enabled=True,
            durable_submit_outbox_enabled=True,
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
            taker_fok_fak_live_enabled=True,
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
        q_lcb_5pct=q_lcb_5pct,
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
