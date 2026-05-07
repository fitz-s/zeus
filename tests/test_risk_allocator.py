# Lifecycle: created=2026-04-27; last_reviewed=2026-05-01; last_reused=2026-05-01
# Authority basis: docs/operations/task_2026-04-26_ultimate_plan/r3/slice_cards/A2.yaml
# Purpose: Lock INV-NEW-R RiskAllocator / PortfolioGovernor cap and kill-switch behavior.
# Reuse: Run for A2 allocator/governor, executor pre-submit, and live-readiness gate changes.
"""R3 A2 RiskAllocator + PortfolioGovernor acceptance tests."""

from __future__ import annotations

import inspect
import sqlite3
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import numpy as np
import pytest

from src.control.heartbeat_supervisor import HeartbeatHealth, HeartbeatStatus
from src.contracts import Direction, EdgeContext, EntryMethod, ExecutionIntent, DecisionSourceContext
from src.contracts.slippage_bps import SlippageBps
from src.execution.executor import create_execution_intent, create_exit_order_intent, execute_exit_order, execute_intent
from src.risk_allocator import (
    AllocationDenied,
    CapPolicy,
    ExposureLot,
    GovernorState,
    PortfolioGovernor,
    RiskAllocator,
    assert_global_allocation_allows,
    assert_global_submit_allows,
    clear_global_allocator,
    configure_global_allocator,
    count_open_reconcile_findings,
    count_unknown_side_effects,
    load_cap_policy,
    load_position_lots,
    select_global_order_type,
    summary as risk_allocator_summary,
)
from src.riskguard.risk_level import RiskLevel
from src.types import Bin, BinEdge


def _intent(market="m1", size=100.0, token="t1", event="e1", resolution="day0", correlation="city-nyc"):
    intent = ExecutionIntent(
        direction=Direction.YES,
        target_size_usd=size,
        limit_price=0.5,
        toxicity_budget=0.01,
        max_slippage=SlippageBps(value_bps=100.0, direction="adverse"),
        is_sandbox=True,
        market_id=market,
        token_id=token,
        timeout_seconds=10,
        executable_snapshot_id="snap-1",
        executable_snapshot_min_tick_size=Decimal("0.01"),
        executable_snapshot_min_order_size=Decimal("0.01"),
        executable_snapshot_neg_risk=False,
        event_id=event,
        resolution_window=resolution,
        correlation_key=correlation,
        decision_source_context=DecisionSourceContext(
            source_id="tigge",
            model_family="ecmwf_ifs025",
            forecast_issue_time="2026-04-27T00:00:00+00:00",
            forecast_valid_time="2026-04-27T06:00:00+00:00",
            forecast_fetch_time="2026-04-27T01:00:00+00:00",
            forecast_available_at="2026-04-27T00:30:00+00:00",
            raw_payload_hash="a" * 64,
            degradation_level="OK",
            forecast_source_role="entry_primary",
            authority_tier="FORECAST",
            decision_time="2026-04-27T02:00:00+00:00",
            decision_time_status="OK",
        ),
    )
    return intent


def _state(**kwargs):
    base = dict(
        current_drawdown_pct=0.0,
        heartbeat_health=HeartbeatHealth.HEALTHY,
        ws_gap_active=False,
        ws_gap_seconds=0,
        unknown_side_effect_count=0,
        reconcile_finding_count=0,
    )
    base.update(kwargs)
    return GovernorState(**base)


def _trade_conn() -> sqlite3.Connection:
    from src.state.db import init_schema

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    init_schema(conn)
    return conn


def _insert_snapshot(conn: sqlite3.Connection, *, token_id: str, snapshot_id: str = "snap-1", depth_json: str = "{}") -> str:
    from src.contracts.executable_market_snapshot_v2 import ExecutableMarketSnapshotV2
    from src.state.snapshot_repo import insert_snapshot

    now = datetime(2026, 4, 27, tzinfo=timezone.utc)
    insert_snapshot(
        conn,
        ExecutableMarketSnapshotV2(
            snapshot_id=snapshot_id,
            gamma_market_id=f"gamma-{snapshot_id}",
            event_id=f"event-{snapshot_id}",
            event_slug=f"event-{snapshot_id}",
            condition_id=f"condition-{snapshot_id}",
            question_id=f"question-{snapshot_id}",
            yes_token_id=token_id,
            no_token_id=f"{token_id}-no",
            selected_outcome_token_id=token_id,
            outcome_label="YES",
            enable_orderbook=True,
            active=True,
            closed=False,
            accepting_orders=True,
            market_start_at=None,
            market_end_at=now + timedelta(days=1),
            market_close_at=now + timedelta(days=1),
            sports_start_at=None,
            min_tick_size=Decimal("0.01"),
            min_order_size=Decimal("0.01"),
            fee_details={"fee_rate_fraction": 0.0},
            token_map_raw={"YES": token_id, "NO": f"{token_id}-no"},
            rfqe=None,
            neg_risk=False,
            orderbook_top_bid=Decimal("0.49"),
            orderbook_top_ask=Decimal("0.51"),
            orderbook_depth_jsonb=depth_json,
            raw_gamma_payload_hash="a" * 64,
            raw_clob_market_info_hash="b" * 64,
            raw_orderbook_hash="c" * 64,
            authority_tier="CLOB",
            captured_at=now,
            freshness_deadline=now + timedelta(days=365),
        ),
    )
    return snapshot_id


def _patch_submit_guards(monkeypatch, captured_order_types: list[str]) -> None:
    monkeypatch.setattr("src.control.cutover_guard.assert_submit_allowed", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        "src.control.heartbeat_supervisor.assert_heartbeat_allows_order_type",
        lambda order_type=None: captured_order_types.append(str(order_type or "GTC").upper()),
    )
    monkeypatch.setattr("src.control.ws_gap_guard.assert_ws_allows_submit", lambda *args, **kwargs: None)
    monkeypatch.setattr("src.state.collateral_ledger.assert_buy_preflight", lambda *args, **kwargs: None)
    monkeypatch.setattr("src.state.collateral_ledger.assert_sell_preflight", lambda *args, **kwargs: None)
    monkeypatch.setattr("src.execution.executor._reserve_collateral_for_buy", lambda *args, **kwargs: None)
    monkeypatch.setattr("src.execution.executor._reserve_collateral_for_sell", lambda *args, **kwargs: None)


def test_per_market_cap_enforced():
    allocator = RiskAllocator(
        CapPolicy(max_per_market_micro=150_000_000),
        [ExposureLot("m1", "e1", "day0", "t1", 100_000_000, "CONFIRMED_EXPOSURE")],
    )

    decision = allocator.can_allocate(_intent(size=60), _state())

    assert not decision.allowed
    assert decision.reason == "per_market_cap_exceeded"
    assert decision.confirmed_exposure_micro == 100_000_000


def test_correlated_market_cap_via_multiple_outcome_tokens_enforced():
    allocator = RiskAllocator(
        CapPolicy(max_correlated_exposure_micro=150_000_000, max_per_market_micro=500_000_000, max_per_event_micro=500_000_000),
        [
            ExposureLot("m1", "e1", "day0", "yes", 80_000_000, "CONFIRMED_EXPOSURE", correlation_key="city-nyc"),
            ExposureLot("m2", "e2", "day0", "no", 60_000_000, "CONFIRMED_EXPOSURE", correlation_key="city-nyc"),
        ],
    )

    decision = allocator.can_allocate(_intent(market="m3", size=20, token="other", event="e3", correlation="city-nyc"), _state())

    assert not decision.allowed
    assert decision.reason == "correlated_market_cap_exceeded"


def test_unknown_side_effect_blocks_new_risk_in_same_market():
    allocator = RiskAllocator(CapPolicy(max_per_market_micro=500_000_000))

    decision = allocator.can_allocate(_intent(market="m1", size=10), _state(unknown_side_effect_markets=("m1",)))

    assert not decision.allowed
    assert decision.reason == "unknown_side_effect_same_market"


def test_heartbeat_degraded_switches_to_FOK_FAK_only():
    allocator = RiskAllocator()
    state = _state(heartbeat_health=HeartbeatHealth.DEGRADED)

    assert allocator.maker_or_taker(SimpleNamespace(orderbook_depth_micro=100_000_000), state) == "TAKER"
    assert allocator.allowed_order_types(state) == ("FOK", "FAK")
    assert allocator.reduce_only_mode_active(state)


def test_heartbeat_lost_switches_to_no_trade():
    allocator = RiskAllocator()
    state = _state(heartbeat_health=HeartbeatHealth.LOST)

    assert allocator.maker_or_taker(SimpleNamespace(orderbook_depth_micro=100_000_000), state) == "NO_TRADE"
    assert allocator.allowed_order_types(state) == ()
    assert allocator.can_allocate(_intent(size=1), state).reason == "heartbeat_lost"


def test_book_depth_json_can_select_maker_when_healthy_and_deep():
    allocator = RiskAllocator(CapPolicy(taker_min_depth_micro=50_000_000))
    snapshot = SimpleNamespace(orderbook_depth_jsonb='{"bids":[["0.49","100"]],"asks":[["0.51","100"]]}')

    assert allocator.maker_or_taker(snapshot, _state(heartbeat_health=HeartbeatHealth.HEALTHY)) == "MAKER"


def test_drawdown_governor_blocks_new_risk_at_threshold():
    allocator = RiskAllocator(CapPolicy(max_drawdown_pct=5.0))

    decision = allocator.can_allocate(_intent(size=1), _state(current_drawdown_pct=5.0))

    assert not decision.allowed
    assert decision.reason == "drawdown_threshold"


def test_reduce_only_mode_when_risk_state_degraded():
    allocator = RiskAllocator()

    decision = allocator.can_allocate(_intent(size=1), _state(risk_level=RiskLevel.DATA_DEGRADED))

    assert not decision.allowed
    assert decision.reason == "reduce_only_mode_active"


def test_manual_operator_trade_appears_as_external_position_drift_reduces_capacity():
    allocator = RiskAllocator(
        CapPolicy(max_per_market_micro=120_000_000),
        [ExposureLot("m1", "e1", "day0", "operator-lot", 100_000_000, "CONFIRMED_EXPOSURE", source="OPERATOR")],
    )

    decision = allocator.can_allocate(_intent(size=30), _state())

    assert not decision.allowed
    assert decision.reason == "per_market_cap_exceeded"
    assert decision.remaining_market_capacity_micro == 20_000_000


def test_kill_switch_blocks_all_submits():
    allocator = RiskAllocator()
    governor = PortfolioGovernor()
    governor.kill_switch("operator_manual_halt")
    state = governor.update_state({}, HeartbeatStatus(HeartbeatHealth.HEALTHY, None, 0, "h", 5), {}, 0, 0)

    with pytest.raises(AllocationDenied) as excinfo:
        configure_global_allocator(allocator, state)
        assert_global_allocation_allows(_intent(size=1))

    assert excinfo.value.decision.reason == "operator_manual_halt"
    clear_global_allocator()


def test_global_allocator_defaults_fail_closed_until_cycle_refresh():
    clear_global_allocator()

    try:
        with pytest.raises(AllocationDenied) as entry_exc:
            assert_global_allocation_allows(_intent(size=1))
        with pytest.raises(AllocationDenied) as exit_exc:
            assert_global_submit_allows(reduce_only=True)
        with pytest.raises(AllocationDenied) as order_type_exc:
            select_global_order_type(SimpleNamespace(orderbook_depth_micro=100_000_000))
        snapshot = risk_allocator_summary()
    finally:
        clear_global_allocator()

    assert entry_exc.value.decision.reason == "allocator_not_configured"
    assert exit_exc.value.decision.reason == "allocator_not_configured"
    assert order_type_exc.value.decision.reason == "allocator_not_configured"
    assert snapshot["entry"] == {
        "allow_submit": False,
        "reason": "allocator_not_configured",
    }


def test_executor_pre_submit_guard_raises_structured_allocation_denied():
    from src.execution.executor import _assert_risk_allocator_allows_submit

    configure_global_allocator(RiskAllocator(), _state(heartbeat_health=HeartbeatHealth.LOST))

    try:
        with pytest.raises(AllocationDenied) as excinfo:
            _assert_risk_allocator_allows_submit(_intent(size=1))
    finally:
        clear_global_allocator()

    assert excinfo.value.decision.reason == "heartbeat_lost"


def test_execute_exit_order_kill_switch_blocks_before_persistence_or_sdk(monkeypatch):
    monkeypatch.setattr("src.control.cutover_guard.assert_submit_allowed", lambda *args, **kwargs: None)
    monkeypatch.setattr("src.control.heartbeat_supervisor.assert_heartbeat_allows_order_type", lambda *args, **kwargs: None)
    monkeypatch.setattr("src.control.ws_gap_guard.assert_ws_allows_submit", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        "src.execution.executor.get_trade_connection_with_world",
        lambda: (_ for _ in ()).throw(AssertionError("DB persistence must not start")),
    )
    configure_global_allocator(RiskAllocator(), _state(kill_switch_armed=True, manual_reason="operator_manual_halt"))

    try:
        with pytest.raises(AllocationDenied) as excinfo:
            execute_exit_order(
                create_exit_order_intent(
                    trade_id="trade-exit",
                    token_id="token-exit",
                    shares=10,
                    current_price=0.5,
                )
            )
    finally:
        clear_global_allocator()

    assert excinfo.value.decision.reason == "operator_manual_halt"


def test_live_entry_submit_uses_allocator_selected_FOK_for_shallow_book(monkeypatch):
    conn = _trade_conn()
    heartbeat_order_types: list[str] = []
    captured: dict[str, object] = {}
    _patch_submit_guards(monkeypatch, heartbeat_order_types)
    snapshot_id = _insert_snapshot(conn, token_id="yes-entry", depth_json="{}")

    class DummyClient:
        def __init__(self):
            self.bound_envelope = None

        def v2_preflight(self):
            return None

        def bind_submission_envelope(self, envelope):
            self.bound_envelope = envelope

        def place_limit_order(self, *, token_id, price, size, side, order_type="GTC"):
            assert self.bound_envelope is not None
            assert self.bound_envelope.order_type == order_type
            captured.update(token_id=token_id, price=price, size=size, side=side, order_type=order_type)
            return {
                "orderID": "entry-order-1",
                "status": "OPEN",
                "_venue_submission_envelope": self.bound_envelope.with_updates(
                    order_id="entry-order-1",
                ).to_dict(),
            }

    monkeypatch.setattr("src.data.polymarket_client.PolymarketClient", DummyClient)
    configure_global_allocator(RiskAllocator(), _state(heartbeat_health=HeartbeatHealth.HEALTHY))
    try:
        intent = _intent(market="entry-market", size=5, token="yes-entry")
        intent = replace(
            intent,
            executable_snapshot_id=snapshot_id,
            event_id="entry-event",
            resolution_window="2026-04-27",
            correlation_key="nyc:2026-04-27",
        )
        result = execute_intent(intent, 0.50, "50-51", conn=conn, decision_id="decision-entry")
        envelope_order_type = conn.execute(
            "SELECT order_type FROM venue_submission_envelopes ORDER BY captured_at DESC LIMIT 1"
        ).fetchone()["order_type"]
    finally:
        clear_global_allocator()
        conn.close()

    assert result.status == "pending"
    assert captured["order_type"] == "FOK"
    assert heartbeat_order_types == ["FOK"]
    assert envelope_order_type == "FOK"


def test_live_exit_submit_uses_allocator_selected_FOK_when_heartbeat_is_degraded(monkeypatch):
    conn = _trade_conn()
    heartbeat_order_types: list[str] = []
    captured: dict[str, object] = {}
    _patch_submit_guards(monkeypatch, heartbeat_order_types)
    snapshot_id = _insert_snapshot(conn, token_id="yes-exit", depth_json='{"bids":[["0.49","500"]],"asks":[["0.51","500"]]}')

    class DummyClient:
        def __init__(self):
            self.bound_envelope = None

        def bind_submission_envelope(self, envelope):
            self.bound_envelope = envelope

        def place_limit_order(self, *, token_id, price, size, side, order_type="GTC"):
            assert self.bound_envelope is not None
            assert self.bound_envelope.order_type == order_type
            captured.update(token_id=token_id, price=price, size=size, side=side, order_type=order_type)
            return {
                "orderID": "exit-order-1",
                "status": "OPEN",
                "_venue_submission_envelope": self.bound_envelope.with_updates(
                    order_id="exit-order-1",
                ).to_dict(),
            }

    monkeypatch.setattr("src.data.polymarket_client.PolymarketClient", DummyClient)
    configure_global_allocator(RiskAllocator(), _state(heartbeat_health=HeartbeatHealth.DEGRADED))
    try:
        result = execute_exit_order(
            create_exit_order_intent(
                trade_id="trade-exit-fok",
                token_id="yes-exit",
                shares=10,
                current_price=0.50,
                best_bid=0.49,
                executable_snapshot_id=snapshot_id,
                executable_snapshot_min_tick_size=Decimal("0.01"),
                executable_snapshot_min_order_size=Decimal("0.01"),
                executable_snapshot_neg_risk=False,
            ),
            conn=conn,
            decision_id="decision-exit",
        )
        envelope_order_type = conn.execute(
            "SELECT order_type FROM venue_submission_envelopes ORDER BY captured_at DESC LIMIT 1"
        ).fetchone()["order_type"]
    finally:
        clear_global_allocator()
        conn.close()

    assert result.status == "pending"
    assert captured["order_type"] == "FOK"
    assert heartbeat_order_types == ["FOK"]
    assert envelope_order_type == "FOK"


def test_polymarket_client_threads_selected_order_type_to_v2_adapter():
    from src.contracts.venue_submission_envelope import VenueSubmissionEnvelope
    from src.data.polymarket_client import PolymarketClient

    captured: dict[str, object] = {}
    envelope = VenueSubmissionEnvelope(
        sdk_package="py-clob-client-v2",
        sdk_version="test",
        host="https://clob-v2.polymarket.com",
        chain_id=137,
        funder_address="0xfunder",
        condition_id="cond-fok",
        question_id="q-fok",
        yes_token_id="yes-token",
        no_token_id="no-token",
        selected_outcome_token_id="yes-token",
        outcome_label="YES",
        side="BUY",
        price=Decimal("0.50"),
        size=Decimal("10"),
        order_type="FOK",
        post_only=False,
        tick_size=Decimal("0.01"),
        min_order_size=Decimal("5"),
        neg_risk=False,
        fee_details={"fee_rate_fraction": 0.0},
        canonical_pre_sign_payload_hash="a" * 64,
        signed_order=None,
        signed_order_hash=None,
        raw_request_hash="b" * 64,
        raw_response_json=None,
        order_id=None,
        trade_ids=(),
        transaction_hashes=(),
        error_code=None,
        error_message=None,
        captured_at="2026-05-01T00:00:00+00:00",
    )

    class FakeAdapter:
        def submit(self, bound_envelope):
            captured["envelope"] = bound_envelope
            return SimpleNamespace(
                status="accepted",
                error_code=None,
                error_message=None,
                envelope=bound_envelope.with_updates(order_id="ord-fok"),
            )

        def submit_limit_order(self, **kwargs):  # pragma: no cover - tripwire
            raise AssertionError("bound submit must use the envelope path")

    client = PolymarketClient()
    client._v2_adapter = FakeAdapter()
    client.bind_submission_envelope(envelope)

    result = client.place_limit_order(
        token_id="yes-token",
        price=0.50,
        size=10.0,
        side="BUY",
        order_type="FOK",
    )

    assert result["orderID"] == "ord-fok"
    assert captured["envelope"].order_type == "FOK"


@pytest.mark.parametrize(
    ("state_kwargs", "reason"),
    [
        ({"unknown_side_effect_count": 1}, "unknown_side_effect_threshold"),
        ({"reconcile_finding_count": 1}, "reconcile_finding_threshold"),
        ({"ws_gap_active": True, "ws_gap_seconds": 16}, "ws_gap_threshold"),
    ],
)
def test_kill_switch_trips_on_configured_thresholds(state_kwargs, reason):
    allocator = RiskAllocator(CapPolicy(unknown_side_effect_limit=0, reconcile_finding_limit=0, ws_gap_seconds_limit=15))

    assert allocator.can_allocate(_intent(size=1), _state(**state_kwargs)).reason == reason


def test_portfolio_governor_update_state_arms_threshold_kill_switch():
    governor = PortfolioGovernor(CapPolicy(max_drawdown_pct=5.0))

    state = governor.update_state(
        {"current_drawdown_pct": 5.0, "risk_level": "GREEN"},
        {"health": "HEALTHY"},
        {"m5_reconcile_required": False},
        unknown_count=0,
        finding_count=0,
    )

    assert state.kill_switch_armed
    assert state.manual_reason == "drawdown_threshold"


def test_optimistic_vs_confirmed_split_in_capacity_check():
    allocator = RiskAllocator(
        CapPolicy(max_per_market_micro=110_000_000, optimistic_exposure_weight=0.25),
        [
            ExposureLot("m1", "e1", "day0", "t1", 80_000_000, "OPTIMISTIC_EXPOSURE"),
            ExposureLot("m1", "e1", "day0", "t1", 40_000_000, "CONFIRMED_EXPOSURE"),
        ],
    )

    decision = allocator.can_allocate(_intent(size=50), _state())

    assert decision.allowed
    assert decision.optimistic_exposure_micro == 80_000_000
    assert decision.confirmed_exposure_micro == 40_000_000
    assert decision.weighted_existing_exposure_micro == 60_000_000


def test_position_lots_reader_uses_latest_append_only_state_and_counts_guards():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE venue_commands (
          command_id TEXT PRIMARY KEY,
          market_id TEXT,
          token_id TEXT,
          decision_id TEXT,
          state TEXT,
          updated_at TEXT
        );
        CREATE TABLE position_lots (
          lot_id INTEGER PRIMARY KEY AUTOINCREMENT,
          position_id INTEGER,
          state TEXT,
          shares INTEGER,
          entry_price_avg TEXT,
          source_command_id TEXT,
          source TEXT,
          raw_payload_json TEXT,
          local_sequence INTEGER
        );
        CREATE TABLE venue_command_events (
          event_id TEXT,
          command_id TEXT,
          sequence_no INTEGER,
          event_type TEXT,
          payload_json TEXT,
          state_after TEXT
        );
        CREATE TABLE exchange_reconcile_findings (
          finding_id INTEGER PRIMARY KEY,
          resolved_at TEXT
        );
        """
    )
    conn.execute(
        "INSERT INTO venue_commands VALUES ('cmd-1','m1','t1','event-1','FILLED','2026-04-27T00:00:00Z')"
    )
    conn.execute(
        "INSERT INTO venue_commands VALUES ('cmd-2','m2','t2','event-2','SUBMIT_UNKNOWN_SIDE_EFFECT','2026-04-27T00:01:00Z')"
    )
    conn.execute(
        "INSERT INTO venue_commands VALUES ('cmd-3','m3','t3','event-3','REVIEW_REQUIRED','2026-04-27T00:02:00Z')"
    )
    conn.execute(
        "INSERT INTO venue_commands VALUES ('cmd-4','m4','t4','event-4','UNKNOWN','2026-04-27T00:03:00Z')"
    )
    conn.execute(
        """
        INSERT INTO venue_command_events VALUES (
          'evt-1','cmd-2',2,'SUBMIT_REQUESTED',
          '{"allocation":{"event_id":"event-live","resolution_window":"2026-04-27","correlation_key":"city-nyc"}}',
          'SUBMITTING'
        )
        """
    )
    conn.execute(
        "INSERT INTO position_lots (position_id,state,shares,entry_price_avg,source_command_id,source,raw_payload_json,local_sequence) VALUES (1,'OPTIMISTIC_EXPOSURE',10,'0.50','cmd-1','WS_USER','{}',1)"
    )
    conn.execute(
        "INSERT INTO position_lots (position_id,state,shares,entry_price_avg,source_command_id,source,raw_payload_json,local_sequence) VALUES (1,'CONFIRMED_EXPOSURE',10,'0.50','cmd-1','CHAIN','{}',2)"
    )
    conn.execute(
        "INSERT INTO position_lots (position_id,state,shares,entry_price_avg,source_command_id,source,raw_payload_json,local_sequence) VALUES (2,'OPTIMISTIC_EXPOSURE',20,'0.25','cmd-2','WS_USER','{\"resolution_window\":\"day0\",\"correlation_key\":\"city-nyc\"}',1)"
    )
    conn.execute("INSERT INTO exchange_reconcile_findings (finding_id, resolved_at) VALUES (1, NULL)")

    lots = load_position_lots(conn)
    unknown_count, unknown_markets = count_unknown_side_effects(conn)

    assert [(lot.market_id, lot.state, lot.exposure_micro) for lot in lots] == [
        ("m1", "CONFIRMED_EXPOSURE", 5_000_000),
        ("m2", "OPTIMISTIC_EXPOSURE", 5_000_000),
    ]
    assert lots[1].event_id == "event-live"
    assert lots[1].resolution_window == "2026-04-27"
    assert lots[1].correlation_key == "city-nyc"
    assert unknown_count == 3
    assert unknown_markets == ("m2", "m3", "m4")
    assert count_open_reconcile_findings(conn) == 1


def test_create_execution_intent_populates_typed_allocation_metadata():
    edge_context = EdgeContext(
        p_raw=np.array([0.5]),
        p_cal=np.array([0.5]),
        p_market=np.array([0.4]),
        p_posterior=0.6,
        forward_edge=0.2,
        alpha=0.5,
        confidence_band_upper=0.7,
        confidence_band_lower=0.4,
        entry_provenance=EntryMethod.ENS_MEMBER_COUNTING,
        decision_snapshot_id="decision-snap",
        n_edges_found=1,
        n_edges_after_fdr=1,
    )
    edge = BinEdge(
        bin=Bin(low=50, high=51, unit="F", label="50-51"),
        direction="buy_yes",
        edge=0.1,
        ci_lower=0.05,
        ci_upper=0.2,
        p_model=0.6,
        p_market=0.4,
        p_posterior=0.6,
        entry_price=0.4,
        p_value=0.01,
        vwmp=0.4,
    )

    intent = create_execution_intent(
        edge_context=edge_context,
        edge=edge,
        size_usd=10,
        mode="opening_hunt",
        market_id="market-1",
        token_id="yes-token",
        no_token_id="no-token",
        event_id="event-1",
        resolution_window="2026-04-27",
        correlation_key="cluster-nyc:2026-04-27",
    )

    assert intent.event_id == "event-1"
    assert intent.resolution_window == "2026-04-27"
    assert intent.correlation_key == "cluster-nyc:2026-04-27"


def test_cycle_runner_refreshes_portfolio_governor_before_monitoring():
    from src.engine import cycle_runner

    source = inspect.getsource(cycle_runner.run_cycle)

    assert source.index("portfolio_governor_cycle_start") < source.index("_execute_monitoring_phase")


def test_cap_policy_config_defaults_load():
    policy = load_cap_policy("config/risk_caps.yaml")

    assert policy.max_per_market_micro > 0
    assert policy.optimistic_exposure_weight == 0.5
