"""Runtime guard and live-cycle wiring tests."""

from __future__ import annotations

import json
import sys
import tempfile
import types
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pytest

import src.data.ensemble_client as ensemble_client
import src.engine.cycle_runner as cycle_runner
import src.engine.cycle_runtime as cycle_runtime
import src.engine.evaluator as evaluator_module
import src.execution.exit_lifecycle as exit_lifecycle_module
from src.config import City
from src.control import control_plane as control_plane_module
from src.data.ecmwf_open_data import DATA_VERSION, collect_open_ens_cycle
from src.data.openmeteo_quota import DAILY_LIMIT, HARD_THRESHOLD, OpenMeteoQuotaTracker
from src.engine.discovery_mode import DiscoveryMode
from src.engine.time_context import lead_days_to_target
from src.engine.evaluator import EdgeDecision, MarketCandidate
from src.execution.executor import OrderResult
from src.riskguard.risk_level import RiskLevel
from src.contracts.exceptions import ObservationUnavailableError
from src.state.db import get_connection, init_schema, query_position_events
from src.state.decision_chain import CycleArtifact, NoTradeCase, query_learning_surface_summary, store_artifact
from src.state.chain_reconciliation import ChainPosition, reconcile
from src.state.portfolio import ExitContext, ExitDecision, PortfolioState, Position, load_portfolio, save_portfolio
from src.state.strategy_tracker import StrategyTracker
from src.types import Bin, BinEdge, Day0TemporalContext


NYC = City(
    name="NYC",
    lat=40.7772,
    lon=-73.8726,
    timezone="America/New_York",
    cluster="US-Northeast",
    settlement_unit="F",
    wu_station="KLGA",
)


def _position(**kwargs) -> Position:
    defaults = dict(
        trade_id="t1",
        market_id="m1",
        city="NYC",
        cluster="US-Northeast",
        target_date="2026-04-01",
        bin_label="39-40°F",
        direction="buy_yes",
        unit="F",
        size_usd=10.0,
        entry_price=0.40,
        p_posterior=0.60,
        edge=0.20,
        shares=25.0,
        cost_basis_usd=10.0,
        entered_at="2026-03-30T00:00:00Z",
        token_id="yes123",
        no_token_id="no456",
        state="entered",
        edge_source="opening_inertia",
        strategy="opening_inertia",
    )
    defaults.update(kwargs)
    return Position(**defaults)


def _edge() -> BinEdge:
    return BinEdge(
        bin=Bin(low=39, high=40, label="39-40°F", unit="F"),
        direction="buy_yes",
        edge=0.12,
        ci_lower=0.05,
        ci_upper=0.15,
        p_model=0.60,
        p_market=0.35,
        p_posterior=0.47,
        entry_price=0.35,
        p_value=0.02,
        vwmp=0.35,
    )


def test_chain_reconciliation_updates_live_position_from_chain(monkeypatch, tmp_path):
    db_path = tmp_path / "zeus.db"
    portfolio_path = tmp_path / "positions.json"
    conn = get_connection(db_path)
    init_schema(conn)
    conn.close()
    save_portfolio(PortfolioState(positions=[_position(size_usd=8.0, shares=20.0, cost_basis_usd=8.0)]), portfolio_path)

    class DummyClob:
        def __init__(self, paper_mode):
            self.paper_mode = False

        def get_positions_from_api(self):
            return [{
                "token_id": "yes123",
                "size": 25.0,
                "avg_price": 0.20,
                "cost": 5.0,
                "condition_id": "cond-1",
            }]

        def get_open_orders(self):
            return []

        def get_balance(self):
            return 100.0

    monkeypatch.setattr(cycle_runner, "get_current_level", lambda: RiskLevel.GREEN)
    monkeypatch.setattr(cycle_runner, "get_connection", lambda: get_connection(db_path))
    monkeypatch.setattr(cycle_runner, "load_portfolio", lambda: load_portfolio(portfolio_path))
    monkeypatch.setattr(cycle_runner, "save_portfolio", lambda state: save_portfolio(state, portfolio_path))
    monkeypatch.setattr(cycle_runner, "PolymarketClient", DummyClob)
    monkeypatch.setattr(cycle_runner, "get_tracker", lambda: StrategyTracker())
    monkeypatch.setattr(cycle_runner, "save_tracker", lambda tracker: None)
    monkeypatch.setattr(cycle_runner, "find_weather_markets", lambda **kwargs: [])
    monkeypatch.setattr("src.engine.monitor_refresh.refresh_position", lambda conn, clob, pos: (pos.entry_price, pos.p_posterior))
    monkeypatch.setattr("src.control.control_plane.process_commands", lambda: [])
    monkeypatch.setattr("src.observability.status_summary.write_status", lambda cycle_summary=None: None)

    summary = cycle_runner.run_cycle(DiscoveryMode.OPENING_HUNT)
    loaded = load_portfolio(portfolio_path)
    pos = loaded.positions[0]

    assert summary["chain_sync"]["synced"] == 1
    assert summary["chain_sync"]["updated"] == 1
    assert pos.shares == pytest.approx(25.0)
    assert pos.cost_basis_usd == pytest.approx(5.0)
    assert pos.chain_state == "synced"
    assert pos.condition_id == "cond-1"


def test_stale_order_cleanup_cancels_orphan_open_orders(monkeypatch, tmp_path):
    db_path = tmp_path / "zeus.db"
    portfolio_path = tmp_path / "positions.json"
    conn = get_connection(db_path)
    init_schema(conn)
    conn.close()
    save_portfolio(
        PortfolioState(positions=[_position(
            trade_id="pending-1",
            state="pending_tracked",
            order_id="tracked",
            order_posted_at="2026-03-30T00:00:00Z",
            order_timeout_at="2099-01-01T00:00:00+00:00",
        )]),
        portfolio_path,
    )
    cancelled: list[str] = []

    class DummyClob:
        def __init__(self, paper_mode):
            self.paper_mode = False

        def get_positions_from_api(self):
            return []

        def get_open_orders(self):
            return [{"id": "tracked"}, {"id": "orphan-1"}]

        def get_order_status(self, order_id):
            return {"status": "OPEN"}

        def cancel_order(self, order_id):
            cancelled.append(order_id)
            return {"status": "CANCELLED", "id": order_id}

        def get_balance(self):
            return 100.0

    monkeypatch.setattr(cycle_runner, "get_current_level", lambda: RiskLevel.GREEN)
    monkeypatch.setattr(cycle_runner, "get_connection", lambda: get_connection(db_path))
    monkeypatch.setattr(cycle_runner, "load_portfolio", lambda: load_portfolio(portfolio_path))
    monkeypatch.setattr(cycle_runner, "save_portfolio", lambda state: save_portfolio(state, portfolio_path))
    monkeypatch.setattr(cycle_runner, "PolymarketClient", DummyClob)
    monkeypatch.setattr(cycle_runner, "get_tracker", lambda: StrategyTracker())
    monkeypatch.setattr(cycle_runner, "save_tracker", lambda tracker: None)
    monkeypatch.setattr(cycle_runner, "find_weather_markets", lambda **kwargs: [])
    monkeypatch.setattr("src.control.control_plane.process_commands", lambda: [])
    monkeypatch.setattr("src.observability.status_summary.write_status", lambda cycle_summary=None: None)

    summary = cycle_runner.run_cycle(DiscoveryMode.OPENING_HUNT)

    assert summary["stale_orders_cancelled"] == 1
    assert cancelled == ["orphan-1"]


def test_reconcile_pending_positions_delegates_to_fill_tracker(monkeypatch):
    portfolio = PortfolioState()
    tracker = StrategyTracker()
    calls = {}

    def fake_check_pending_entries(portfolio_arg, clob_arg, tracker_arg=None, *, deps=None, now=None):
        calls["portfolio"] = portfolio_arg
        calls["clob"] = clob_arg
        calls["tracker"] = tracker_arg
        calls["deps"] = deps
        calls["now"] = now
        return {"entered": 1, "voided": 0, "still_pending": 0, "dirty": True, "tracker_dirty": True}

    monkeypatch.setattr("src.execution.fill_tracker.check_pending_entries", fake_check_pending_entries)

    clob = object()
    summary = cycle_runner._reconcile_pending_positions(portfolio, clob, tracker)

    assert calls["portfolio"] is portfolio
    assert calls["clob"] is clob
    assert calls["tracker"] is tracker
    assert calls["deps"] is cycle_runner
    assert calls["now"] is None
    assert summary == {"entered": 1, "voided": 0, "dirty": True, "tracker_dirty": True}


def test_reconcile_pending_positions_sets_verified_entry_but_keeps_chain_local(monkeypatch):
    db_path = Path(tempfile.mkdtemp()) / "zeus.db"
    conn = get_connection(db_path)
    init_schema(conn)
    conn.close()

    portfolio = PortfolioState(positions=[_position(
        trade_id="pending-fill-1",
        state="pending_tracked",
        order_id="ord-1",
        entry_order_id="",
        entry_fill_verified=False,
        token_id="tok_yes_pending",
        no_token_id="tok_no_pending",
        size_usd=10.0,
        entry_price=0.40,
    )])

    class Tracker:
        def __init__(self):
            self.entries = []
        def record_entry(self, position):
            self.entries.append(position.trade_id)

    class DummyClob:
        paper_mode = False
        def get_order_status(self, order_id):
            assert order_id == "ord-1"
            return {"status": "FILLED", "avgPrice": 0.41, "filledSize": 24.39}

    monkeypatch.setattr(cycle_runner, "_utcnow", lambda: datetime(2026, 4, 2, 6, 0, tzinfo=timezone.utc))
    monkeypatch.setattr(cycle_runner, "get_connection", lambda: get_connection(db_path))
    summary = cycle_runner._reconcile_pending_positions(portfolio, DummyClob(), Tracker())
    pos = portfolio.positions[0]
    conn = get_connection(db_path)
    events = query_position_events(conn, "pending-fill-1")
    conn.close()

    assert summary["entered"] == 1
    assert pos.state == "entered"
    assert pos.entry_fill_verified is True
    assert pos.entry_order_id == "ord-1"
    assert pos.order_status == "filled"
    assert pos.chain_state == "local_only"
    assert pos.size_usd == pytest.approx(24.39 * 0.41)
    assert pos.cost_basis_usd == pytest.approx(24.39 * 0.41)
    assert pos.fill_quality == pytest.approx((0.41 - 0.40) / 0.40)
    assert any(event["event_type"] == "ORDER_FILLED" for event in events)


def test_exposure_gate_skips_new_entries_without_forcing_reduction(monkeypatch, tmp_path):
    db_path = tmp_path / "zeus.db"
    conn = get_connection(db_path)
    init_schema(conn)
    conn.close()
    portfolio = PortfolioState(positions=[_position(size_usd=72.0, shares=180.0, cost_basis_usd=72.0)])

    class DummyClob:
        def __init__(self, paper_mode):
            self.paper_mode = True

    monkeypatch.setattr(cycle_runner, "get_current_level", lambda: RiskLevel.GREEN)
    monkeypatch.setattr(cycle_runner, "get_connection", lambda: get_connection(db_path))
    monkeypatch.setattr(cycle_runner, "load_portfolio", lambda: portfolio)
    monkeypatch.setattr(cycle_runner, "save_portfolio", lambda state: None)
    monkeypatch.setattr(cycle_runner, "PolymarketClient", DummyClob)
    monkeypatch.setattr(cycle_runner, "get_tracker", lambda: StrategyTracker())
    monkeypatch.setattr(cycle_runner, "save_tracker", lambda tracker: None)
    monkeypatch.setattr(cycle_runner, "is_entries_paused", lambda: False)
    monkeypatch.setattr("src.engine.monitor_refresh.refresh_position", lambda conn, clob, pos: (pos.entry_price, pos.p_posterior))
    monkeypatch.setattr("src.control.control_plane.process_commands", lambda: [])
    monkeypatch.setattr("src.observability.status_summary.write_status", lambda cycle_summary=None: None)
    monkeypatch.setattr(
        cycle_runner,
        "evaluate_candidate",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("scan should be skipped near max exposure")),
    )

    summary = cycle_runner.run_cycle(DiscoveryMode.OPENING_HUNT)

    assert summary["near_max_exposure"] is True
    assert summary["entries_blocked_reason"] == "near_max_exposure"
    assert summary["candidates"] == 0


def test_trade_and_no_trade_artifacts_carry_replay_reference_fields(monkeypatch, tmp_path):
    db_path = tmp_path / "zeus.db"
    conn = get_connection(db_path)
    init_schema(conn)
    conn.close()

    portfolio = PortfolioState()

    class DummyClob:
        def __init__(self, paper_mode):
            self.paper_mode = True

    class DummyDecision:
        def __init__(self, should_trade):
            self.should_trade = should_trade
            self.edge = _edge() if should_trade else None
            self.tokens = {"market_id": "m1", "token_id": "yes1", "no_token_id": "no1"} if should_trade else None
            self.size_usd = 5.0
            self.decision_id = "d1" if should_trade else "d2"
            self.rejection_stage = "EDGE_INSUFFICIENT"
            self.rejection_reasons = ["small"]
            self.selected_method = "ens_member_counting"
            self.applied_validations = ["ens_fetch"]
            self.decision_snapshot_id = "snap-1"
            self.edge_source = "center_buy"
            self.strategy_key = "center_buy" if should_trade else ""
            self.edge_context = None
            self.settlement_semantics_json = '{"measurement_unit":"F"}'
            self.epistemic_context_json = '{"decision_time_utc":"2026-04-01T00:00:00Z"}'
            self.edge_context_json = '{"forward_edge":0.12}'

    monkeypatch.setattr(cycle_runner, "get_current_level", lambda: RiskLevel.GREEN)
    monkeypatch.setattr(cycle_runner, "get_connection", lambda: get_connection(db_path))
    monkeypatch.setattr(cycle_runner, "load_portfolio", lambda: portfolio)
    monkeypatch.setattr(cycle_runner, "save_portfolio", lambda state: None)
    monkeypatch.setattr(cycle_runner, "PolymarketClient", DummyClob)
    monkeypatch.setattr(cycle_runner, "get_tracker", lambda: StrategyTracker())
    monkeypatch.setattr(cycle_runner, "save_tracker", lambda tracker: None)
    monkeypatch.setattr(cycle_runner, "find_weather_markets", lambda **kwargs: [{
        "city": NYC,
        "target_date": "2026-04-01",
        "hours_since_open": 12.0,
        "hours_to_resolution": 24.0,
        "outcomes": [{"title": "39-40°F", "range_low": 39, "range_high": 40, "token_id": "yes1", "no_token_id": "no1", "market_id": "m1", "price": 0.35}],
    }])
    monkeypatch.setattr(cycle_runner, "evaluate_candidate", lambda *args, **kwargs: [DummyDecision(True), DummyDecision(False)])
    monkeypatch.setattr(cycle_runner, "create_execution_intent", lambda **kwargs: object())
    monkeypatch.setattr(cycle_runner, "execute_intent", lambda *args, **kwargs: OrderResult(status="filled", trade_id="rt1", order_id="o1", fill_price=0.35, shares=10.0))
    monkeypatch.setattr("src.control.control_plane.process_commands", lambda: [])
    monkeypatch.setattr("src.observability.status_summary.write_status", lambda cycle_summary=None: None)
    monkeypatch.setattr("src.engine.monitor_refresh.refresh_position", lambda conn, clob, pos: (_ for _ in ()).throw(AssertionError("monitor not expected")))

    summary = cycle_runner.run_cycle(DiscoveryMode.OPENING_HUNT)

    conn = get_connection(db_path)
    artifact = conn.execute("SELECT artifact_json FROM decision_log ORDER BY id DESC LIMIT 1").fetchone()
    shadow = conn.execute("SELECT p_raw_json, p_cal_json, edges_json FROM shadow_signals ORDER BY id DESC LIMIT 1").fetchone()
    conn.close()
    payload = json.loads(artifact["artifact_json"])
    trade_case = payload["trade_cases"][0]
    no_trade_case = payload["no_trade_cases"][0]

    assert summary["trades"] == 1
    assert trade_case["decision_snapshot_id"] == "snap-1"
    assert trade_case["market_id"] == "m1"
    assert trade_case["settlement_semantics_json"] == '{"measurement_unit":"F"}'
    assert trade_case["bin_labels"] == ["39-40°F"]
    assert trade_case["p_market_vector"] == []
    assert no_trade_case["decision_snapshot_id"] == "snap-1"
    assert no_trade_case["selected_method"] == "ens_member_counting"
    assert no_trade_case["settlement_semantics_json"] == '{"measurement_unit":"F"}'
    assert no_trade_case["epistemic_context_json"] == '{"decision_time_utc":"2026-04-01T00:00:00Z"}'
    assert no_trade_case["edge_context_json"] == '{"forward_edge":0.12}'
    assert no_trade_case["applied_validations"] == ["ens_fetch"]
    assert no_trade_case["bin_labels"] == ["39-40°F"]
    assert shadow is not None
    assert json.loads(shadow["p_raw_json"]) == []
    assert json.loads(shadow["p_cal_json"]) == []
    assert len(json.loads(shadow["edges_json"])) == 2


def test_live_dynamic_cap_flows_to_evaluator(monkeypatch, tmp_path):
    db_path = tmp_path / "zeus.db"
    conn = get_connection(db_path)
    init_schema(conn)
    conn.close()
    portfolio = PortfolioState(positions=[_position(size_usd=20.0, shares=50.0, cost_basis_usd=20.0)])
    captured: dict[str, float] = {}

    class DummyClob:
        def __init__(self, paper_mode):
            self.paper_mode = False

        def get_positions_from_api(self):
            return [{
                "token_id": "yes123",
                "size": 50.0,
                "avg_price": 0.40,
                "cost": 20.0,
                "condition_id": "cond-1",
            }]

        def get_open_orders(self):
            return []

        def get_balance(self):
            return 100.0

    monkeypatch.setattr(cycle_runner, "get_current_level", lambda: RiskLevel.GREEN)
    monkeypatch.setattr(cycle_runner, "get_connection", lambda: get_connection(db_path))
    monkeypatch.setattr(cycle_runner, "load_portfolio", lambda: portfolio)
    monkeypatch.setattr(cycle_runner, "save_portfolio", lambda state: None)
    monkeypatch.setattr(cycle_runner, "PolymarketClient", DummyClob)
    monkeypatch.setattr(cycle_runner, "get_tracker", lambda: StrategyTracker())
    monkeypatch.setattr(cycle_runner, "save_tracker", lambda tracker: None)
    _market_list = [{
        "city": NYC,
        "target_date": "2026-04-01",
        "outcomes": [],
        "hours_since_open": 2.0,
        "hours_to_resolution": 30.0,
    }]
    monkeypatch.setattr(cycle_runner, "find_weather_markets", lambda **kwargs: _market_list)
    monkeypatch.setattr("src.data.market_scanner.find_weather_markets", lambda **kwargs: _market_list)

    def _dummy_refresh(conn, clob, pos):
        from src.contracts import EdgeContext, EntryMethod
        return EdgeContext(
            p_raw=np.array([pos.p_posterior]),
            p_cal=np.array([pos.p_posterior]),
            p_market=np.array([pos.entry_price]),
            p_posterior=pos.p_posterior,
            forward_edge=0.0,
            alpha=0.5,
            confidence_band_upper=0.6,
            confidence_band_lower=0.4,
            entry_provenance=EntryMethod.ENS_MEMBER_COUNTING,
            decision_snapshot_id="",
            n_edges_found=0,
            n_edges_after_fdr=0,
        )

    monkeypatch.setattr("src.engine.monitor_refresh.refresh_position", _dummy_refresh)
    monkeypatch.setattr("src.control.control_plane.process_commands", lambda: [])
    monkeypatch.setattr("src.observability.status_summary.write_status", lambda cycle_summary=None: None)

    def _capture_eval(candidate, conn, portfolio, clob, limits, entry_bankroll=None):
        captured["entry_bankroll"] = entry_bankroll
        return []

    monkeypatch.setattr(cycle_runner, "evaluate_candidate", _capture_eval)

    cycle_runner.run_cycle(DiscoveryMode.OPENING_HUNT)

    assert captured["entry_bankroll"] == pytest.approx(120.0)


def test_execute_discovery_phase_logs_rejected_live_entry_telemetry(monkeypatch, tmp_path):
    db_path = tmp_path / "zeus.db"
    conn = get_connection(db_path)
    init_schema(conn)
    conn.close()
    portfolio = PortfolioState(bankroll=150.0)

    class DummyClob:
        def __init__(self, paper_mode):
            self.paper_mode = False

        def get_positions_from_api(self):
            return []

        def get_open_orders(self):
            return []

        def get_balance(self):
            return 100.0

        def get_best_bid_ask(self, token_id):
            return (0.34, 0.36, 20.0, 20.0)

    class DummyDecision:
        def __init__(self):
            self.should_trade = True
            self.edge = _edge()
            self.tokens = {"market_id": "m1", "token_id": "yes1", "no_token_id": "no1"}
            self.size_usd = 5.0
            self.decision_id = "d-reject"
            self.rejection_stage = ""
            self.rejection_reasons = []
            self.selected_method = "ens_member_counting"
            self.applied_validations = ["ens_fetch"]
            self.decision_snapshot_id = "snap-reject"
            self.edge_source = "center_buy"
            self.strategy_key = "center_buy"
            self.edge_context = None
            self.settlement_semantics_json = '{"measurement_unit":"F"}'
            self.epistemic_context_json = '{"decision_time_utc":"2026-04-01T00:00:00Z"}'
            self.edge_context_json = '{"forward_edge":0.12}'

    monkeypatch.setattr(cycle_runner, "get_current_level", lambda: RiskLevel.GREEN)
    monkeypatch.setattr(cycle_runner, "get_connection", lambda: get_connection(db_path))
    monkeypatch.setattr(cycle_runner, "load_portfolio", lambda: portfolio)
    monkeypatch.setattr(cycle_runner, "save_portfolio", lambda state: None)
    monkeypatch.setattr(cycle_runner, "PolymarketClient", DummyClob)
    monkeypatch.setattr(cycle_runner, "get_tracker", lambda: StrategyTracker())
    monkeypatch.setattr(cycle_runner, "save_tracker", lambda tracker: None)
    monkeypatch.setattr(cycle_runner, "find_weather_markets", lambda **kwargs: [{
        "city": NYC,
        "target_date": "2026-04-01",
        "hours_since_open": 12.0,
        "hours_to_resolution": 24.0,
        "outcomes": [{"title": "39-40°F", "range_low": 39, "range_high": 40, "token_id": "yes1", "no_token_id": "no1", "market_id": "m1", "price": 0.35}],
    }])
    monkeypatch.setattr(cycle_runner, "evaluate_candidate", lambda *args, **kwargs: [DummyDecision()])
    monkeypatch.setattr(cycle_runner, "create_execution_intent", lambda **kwargs: object())
    monkeypatch.setattr(
        cycle_runner,
        "execute_intent",
        lambda *args, **kwargs: OrderResult(status="rejected", trade_id="rt-reject", order_id="o-reject", submitted_price=0.35, reason="insufficient_liquidity"),
    )
    monkeypatch.setattr("src.control.control_plane.process_commands", lambda: [])
    monkeypatch.setattr("src.observability.status_summary.write_status", lambda cycle_summary=None: None)
    monkeypatch.setattr("src.engine.monitor_refresh.refresh_position", lambda conn, clob, pos: (_ for _ in ()).throw(AssertionError("monitor not expected")))

    cycle_runner.run_cycle(DiscoveryMode.OPENING_HUNT)

    conn = get_connection(db_path)
    events = query_position_events(conn, "rt-reject")
    conn.close()

    assert any(event["event_type"] == "ORDER_REJECTED" for event in events)


def test_strategy_gate_blocks_trade_execution(monkeypatch, tmp_path):
    db_path = tmp_path / "zeus.db"
    conn = get_connection(db_path)
    init_schema(conn)
    conn.close()
    portfolio = PortfolioState(bankroll=150.0)

    class DummyClob:
        def __init__(self, paper_mode):
            self.paper_mode = False
        def get_positions_from_api(self):
            return []
        def get_open_orders(self):
            return []
        def get_balance(self):
            return 100.0

    class DummyDecision:
        def __init__(self):
            self.should_trade = True
            self.edge = _edge()
            self.tokens = {"market_id": "m1", "token_id": "yes1", "no_token_id": "no1"}
            self.size_usd = 5.0
            self.decision_id = "d-gated"
            self.rejection_stage = ""
            self.rejection_reasons = []
            self.selected_method = "ens_member_counting"
            self.applied_validations = ["ens_fetch"]
            self.decision_snapshot_id = "snap-gated"
            self.edge_source = "opening_inertia"
            self.strategy_key = "opening_inertia"
            self.edge_context = None
            self.settlement_semantics_json = '{"measurement_unit":"F"}'
            self.epistemic_context_json = '{"decision_time_utc":"2026-04-01T00:00:00Z"}'
            self.edge_context_json = '{"forward_edge":0.12}'

    monkeypatch.setattr(cycle_runner, "get_current_level", lambda: RiskLevel.GREEN)
    monkeypatch.setattr(cycle_runner, "get_connection", lambda: get_connection(db_path))
    monkeypatch.setattr(cycle_runner, "load_portfolio", lambda: portfolio)
    monkeypatch.setattr(cycle_runner, "save_portfolio", lambda state: None)
    monkeypatch.setattr(cycle_runner, "PolymarketClient", DummyClob)
    monkeypatch.setattr(cycle_runner, "get_tracker", lambda: StrategyTracker())
    monkeypatch.setattr(cycle_runner, "save_tracker", lambda tracker: None)
    monkeypatch.setattr(cycle_runner, "find_weather_markets", lambda **kwargs: [{
        "city": NYC,
        "target_date": "2026-04-01",
        "hours_since_open": 1.0,
        "hours_to_resolution": 24.0,
        "outcomes": [{"title": "39-40°F", "range_low": 39, "range_high": 40, "token_id": "yes1", "no_token_id": "no1", "market_id": "m1", "price": 0.35}],
    }])
    monkeypatch.setattr(cycle_runner, "evaluate_candidate", lambda *args, **kwargs: [DummyDecision()])
    monkeypatch.setattr(cycle_runner, "is_strategy_enabled", lambda strategy: strategy != "opening_inertia")
    monkeypatch.setattr(cycle_runner, "create_execution_intent", lambda **kwargs: (_ for _ in ()).throw(AssertionError("should not execute gated strategy")))
    monkeypatch.setattr("src.control.control_plane.process_commands", lambda: [])
    monkeypatch.setattr("src.observability.status_summary.write_status", lambda cycle_summary=None: None)
    monkeypatch.setattr("src.engine.monitor_refresh.refresh_position", lambda conn, clob, pos: (_ for _ in ()).throw(AssertionError("monitor not expected")))

    summary = cycle_runner.run_cycle(DiscoveryMode.OPENING_HUNT)
    conn = get_connection(db_path)
    artifact = conn.execute("SELECT artifact_json FROM decision_log ORDER BY id DESC LIMIT 1").fetchone()
    conn.close()
    payload = json.loads(artifact["artifact_json"])

    assert summary["strategy_gate_rejections"] == 1
    assert payload["trade_cases"] == []
    assert payload["no_trade_cases"][0]["rejection_stage"] == "RISK_REJECTED"
    assert payload["no_trade_cases"][0]["strategy"] == "opening_inertia"
    assert payload["no_trade_cases"][0]["edge_source"] == "opening_inertia"
    assert payload["no_trade_cases"][0]["rejection_reasons"] == ["strategy_gate_disabled:opening_inertia"]


def test_orange_risk_still_runs_monitoring(monkeypatch, tmp_path):
    db_path = tmp_path / "zeus.db"
    conn = get_connection(db_path)
    init_schema(conn)
    conn.close()
    portfolio = PortfolioState(positions=[_position()])

    class DummyClob:
        def __init__(self, paper_mode):
            self.paper_mode = True

    monitored: list[str] = []

    monkeypatch.setattr(cycle_runner, "get_current_level", lambda: RiskLevel.ORANGE)
    monkeypatch.setattr(cycle_runner, "get_connection", lambda: get_connection(db_path))
    monkeypatch.setattr(cycle_runner, "load_portfolio", lambda: portfolio)
    monkeypatch.setattr(cycle_runner, "save_portfolio", lambda state: None)
    monkeypatch.setattr(cycle_runner, "PolymarketClient", DummyClob)
    monkeypatch.setattr(cycle_runner, "get_tracker", lambda: StrategyTracker())
    monkeypatch.setattr(cycle_runner, "save_tracker", lambda tracker: None)
    monkeypatch.setattr(cycle_runner, "find_weather_markets", lambda **kwargs: [])
    monkeypatch.setattr(cycle_runner, "cities_by_name", {"NYC": NYC}, raising=False)

    def _tracking_refresh(conn, clob, pos):
        from src.contracts import EdgeContext, EntryMethod
        monitored.append(pos.trade_id)
        return EdgeContext(
            p_raw=np.array([pos.p_posterior]),
            p_cal=np.array([pos.p_posterior]),
            p_market=np.array([pos.entry_price]),
            p_posterior=pos.p_posterior,
            forward_edge=0.0,
            alpha=0.5,
            confidence_band_upper=0.6,
            confidence_band_lower=0.4,
            entry_provenance=EntryMethod.ENS_MEMBER_COUNTING,
            decision_snapshot_id="",
            n_edges_found=0,
            n_edges_after_fdr=0,
        )

    monkeypatch.setattr("src.engine.monitor_refresh.refresh_position", _tracking_refresh)
    monkeypatch.setattr("src.control.control_plane.process_commands", lambda: [])
    monkeypatch.setattr("src.observability.status_summary.write_status", lambda cycle_summary=None: None)
    monkeypatch.setattr(
        cycle_runner,
        "evaluate_candidate",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("entries should stay blocked at ORANGE")),
    )

    summary = cycle_runner.run_cycle(DiscoveryMode.OPENING_HUNT)

    assert monitored == ["t1"]
    assert summary["monitors"] == 1
    assert summary["entries_blocked_reason"] == "risk_level=ORANGE"
    assert summary["candidates"] == 0


def test_chain_quarantine_keeps_direction_unknown():
    portfolio = PortfolioState()
    stats = reconcile(
        portfolio,
        [ChainPosition(token_id="yes123", size=12.0, avg_price=0.42, condition_id="cond-1")],
    )

    assert stats["quarantined"] == 1
    pos = portfolio.positions[0]
    assert pos.direction == "unknown"
    assert pos.chain_state == "quarantined"
    assert pos.strategy == ""


def test_chain_quarantine_explicitly_warns_exclusion_without_db_calls(caplog):
    class GuardConn:
        def execute(self, *_args, **_kwargs):
            raise AssertionError("chain-only quarantine exclusion should not touch DB state")

    portfolio = PortfolioState()
    with caplog.at_level("WARNING"):
        stats = reconcile(
            portfolio,
            [ChainPosition(token_id="yes123", size=12.0, avg_price=0.42, condition_id="cond-1")],
            conn=GuardConn(),
        )

    assert stats["quarantined"] == 1
    assert len(portfolio.positions) == 1
    pos = portfolio.positions[0]
    assert pos.trade_id.startswith("quarantine_")
    assert pos.direction == "unknown"
    assert pos.chain_state == "quarantined"
    assert "EXCLUDED FROM CANONICAL MIGRATION" in caplog.text
    assert "pending future governance design" in caplog.text


def test_quarantine_blocks_new_entries(monkeypatch, tmp_path):
    db_path = tmp_path / "zeus.db"
    conn = get_connection(db_path)
    init_schema(conn)
    conn.close()
    portfolio = PortfolioState(positions=[_position(direction="unknown", chain_state="quarantined")])

    class DummyClob:
        def __init__(self, paper_mode):
            self.paper_mode = True

    monkeypatch.setattr(cycle_runner, "get_current_level", lambda: RiskLevel.GREEN)
    monkeypatch.setattr(cycle_runner, "get_connection", lambda: get_connection(db_path))
    monkeypatch.setattr(cycle_runner, "load_portfolio", lambda: portfolio)
    monkeypatch.setattr(cycle_runner, "save_portfolio", lambda state: None)
    monkeypatch.setattr(cycle_runner, "PolymarketClient", DummyClob)
    monkeypatch.setattr(cycle_runner, "get_tracker", lambda: StrategyTracker())
    monkeypatch.setattr(cycle_runner, "save_tracker", lambda tracker: None)
    monkeypatch.setattr(cycle_runner, "find_weather_markets", lambda **kwargs: [])
    monkeypatch.setattr("src.control.control_plane.process_commands", lambda: [])
    monkeypatch.setattr("src.observability.status_summary.write_status", lambda cycle_summary=None: None)
    monkeypatch.setattr(
        cycle_runner,
        "evaluate_candidate",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("entries should stay blocked while quarantined")),
    )

    summary = cycle_runner.run_cycle(DiscoveryMode.OPENING_HUNT)

    assert summary["portfolio_quarantined"] is True
    assert summary["entries_blocked_reason"] == "portfolio_quarantined"
    assert summary["candidates"] == 0


def test_operator_clear_ack_applies_ignored_token_only_after_explicit_ack(monkeypatch, tmp_path):
    db_path = tmp_path / "zeus.db"
    control_path = tmp_path / "control_plane.json"
    conn = get_connection(db_path)
    init_schema(conn)
    conn.close()
    portfolio = PortfolioState(positions=[_position(direction="unknown", chain_state="quarantined", token_id="tok-clear", no_token_id="tok-clear-no")])

    class DummyClob:
        def __init__(self, paper_mode):
            self.paper_mode = True

    control_plane_module.clear_control_state()

    monkeypatch.setattr(cycle_runner, "get_current_level", lambda: RiskLevel.GREEN)
    monkeypatch.setattr(cycle_runner, "get_connection", lambda: get_connection(db_path))
    monkeypatch.setattr(cycle_runner, "load_portfolio", lambda: portfolio)
    monkeypatch.setattr(cycle_runner, "save_portfolio", lambda state: None)
    monkeypatch.setattr(cycle_runner, "PolymarketClient", DummyClob)
    monkeypatch.setattr(cycle_runner, "get_tracker", lambda: StrategyTracker())
    monkeypatch.setattr(cycle_runner, "save_tracker", lambda tracker: None)
    monkeypatch.setattr(cycle_runner, "find_weather_markets", lambda **kwargs: [])
    monkeypatch.setattr("src.observability.status_summary.write_status", lambda cycle_summary=None: None)
    monkeypatch.setattr(control_plane_module, "CONTROL_PATH", control_path)

    summary = cycle_runner.run_cycle(DiscoveryMode.OPENING_HUNT)
    assert portfolio.ignored_tokens == []
    assert summary.get("operator_clears_applied", 0) == 0

    control_plane_module.write_commands([
        control_plane_module.build_quarantine_clear_command(
            token_id="tok-clear",
            condition_id="cond-clear",
            note="operator acknowledged",
        )
    ])

    summary = cycle_runner.run_cycle(DiscoveryMode.OPENING_HUNT)
    assert portfolio.ignored_tokens == ["tok-clear"]
    assert summary["operator_clears_applied"] == 1

    payload = control_plane_module.read_control_payload()
    assert payload["acks"][-1]["command"] == "acknowledge_quarantine_clear"
    assert payload["acks"][-1]["status"] == "executed"
    assert payload["acks"][-1]["token_id"] == "tok-clear"



def test_unknown_direction_positions_are_not_monitored(monkeypatch, tmp_path):
    db_path = tmp_path / "zeus.db"
    conn = get_connection(db_path)
    init_schema(conn)
    conn.close()
    portfolio = PortfolioState(positions=[_position(direction="unknown", chain_state="synced")])

    class DummyClob:
        def __init__(self, paper_mode):
            self.paper_mode = True

    monkeypatch.setattr(cycle_runner, "get_current_level", lambda: RiskLevel.GREEN)
    monkeypatch.setattr(cycle_runner, "get_connection", lambda: get_connection(db_path))
    monkeypatch.setattr(cycle_runner, "load_portfolio", lambda: portfolio)
    monkeypatch.setattr(cycle_runner, "save_portfolio", lambda state: None)
    monkeypatch.setattr(cycle_runner, "PolymarketClient", DummyClob)
    monkeypatch.setattr(cycle_runner, "get_tracker", lambda: StrategyTracker())
    monkeypatch.setattr(cycle_runner, "save_tracker", lambda tracker: None)
    monkeypatch.setattr(cycle_runner, "find_weather_markets", lambda **kwargs: [])
    monkeypatch.setattr("src.control.control_plane.process_commands", lambda: [])
    monkeypatch.setattr("src.observability.status_summary.write_status", lambda cycle_summary=None: None)
    monkeypatch.setattr(
        "src.engine.monitor_refresh.refresh_position",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("unknown direction should skip refresh")),
    )

    summary = cycle_runner.run_cycle(DiscoveryMode.OPENING_HUNT)

    assert summary["monitors"] == 0
    assert summary["monitor_skipped_unknown_direction"] == 1


def test_strategy_classification_preserves_day0_and_update_semantics():
    center_edge = _edge()
    shoulder_no = BinEdge(
        bin=Bin(low=None, high=38, label="38°F or below", unit="F"),
        direction="buy_no",
        edge=0.11,
        ci_lower=0.03,
        ci_upper=0.14,
        p_model=0.72,
        p_market=0.58,
        p_posterior=0.69,
        entry_price=0.58,
        p_value=0.02,
        vwmp=0.58,
    )
    base_candidate = dict(
        city=NYC,
        target_date="2026-04-01",
        outcomes=[],
        hours_since_open=30.0,
        hours_to_resolution=24.0,
    )

    assert evaluator_module._edge_source_for(
        MarketCandidate(discovery_mode=DiscoveryMode.DAY0_CAPTURE.value, **base_candidate),
        center_edge,
    ) == "settlement_capture"
    assert evaluator_module._edge_source_for(
        MarketCandidate(discovery_mode=DiscoveryMode.OPENING_HUNT.value, **base_candidate),
        center_edge,
    ) == "opening_inertia"
    assert evaluator_module._edge_source_for(
        MarketCandidate(discovery_mode=DiscoveryMode.UPDATE_REACTION.value, **base_candidate),
        shoulder_no,
    ) == "shoulder_sell"
    assert evaluator_module._strategy_key_for(
        MarketCandidate(discovery_mode=DiscoveryMode.DAY0_CAPTURE.value, **base_candidate),
        center_edge,
    ) == "settlement_capture"
    assert evaluator_module._strategy_key_for(
        MarketCandidate(discovery_mode=DiscoveryMode.OPENING_HUNT.value, **base_candidate),
        center_edge,
    ) == "opening_inertia"
    assert evaluator_module._strategy_key_for(
        MarketCandidate(discovery_mode=DiscoveryMode.UPDATE_REACTION.value, **base_candidate),
        shoulder_no,
    ) == "shoulder_sell"
    assert cycle_runner._classify_strategy(DiscoveryMode.DAY0_CAPTURE, center_edge, "") == "settlement_capture"


def test_materialize_position_preserves_evaluator_strategy_key():
    decision = evaluator_module.EdgeDecision(
        should_trade=True,
        edge=_edge(),
        tokens={"market_id": "m1", "token_id": "yes1", "no_token_id": "no1"},
        size_usd=10.0,
        decision_id="d1",
        selected_method="ens_member_counting",
        edge_source="opening_inertia",
        strategy_key="center_buy",
    )
    result = types.SimpleNamespace(
        trade_id="t1",
        fill_price=0.6,
        submitted_price=0.6,
        shares=5.0,
        timeout_seconds=None,
        order_id="o1",
        status="filled",
    )
    city = types.SimpleNamespace(name="New York", cluster="US", settlement_unit="F")
    candidate = types.SimpleNamespace(target_date="2026-04-01", hours_since_open=2.0)
    deps = types.SimpleNamespace(
        _utcnow=lambda: datetime(2026, 4, 3, 6, 0, tzinfo=timezone.utc),
        _classify_edge_source=lambda mode, edge: "opening_inertia",
        Position=cycle_runner.Position,
        settings=types.SimpleNamespace(mode="paper"),
    )

    pos = cycle_runtime.materialize_position(
        candidate,
        decision,
        result,
        cycle_runner.PortfolioState(),
        city,
        DiscoveryMode.UPDATE_REACTION,
        state="entered",
        bankroll_at_entry=100.0,
        deps=deps,
    )

    assert pos.strategy_key == "center_buy"
    assert pos.strategy == "center_buy"


def test_materialize_position_rejects_missing_strategy_key():
    decision = evaluator_module.EdgeDecision(
        should_trade=True,
        edge=_edge(),
        tokens={"market_id": "m1", "token_id": "yes1", "no_token_id": "no1"},
        size_usd=10.0,
        decision_id="d1",
        selected_method="ens_member_counting",
        edge_source="opening_inertia",
        strategy_key="",
    )
    result = types.SimpleNamespace(
        trade_id="t1",
        fill_price=0.6,
        submitted_price=0.6,
        shares=5.0,
        timeout_seconds=None,
        order_id="o1",
        status="filled",
    )
    city = types.SimpleNamespace(name="New York", cluster="US", settlement_unit="F")
    candidate = types.SimpleNamespace(target_date="2026-04-01", hours_since_open=2.0)
    deps = types.SimpleNamespace(
        _utcnow=lambda: datetime(2026, 4, 3, 6, 0, tzinfo=timezone.utc),
        _classify_edge_source=lambda mode, edge: "opening_inertia",
        Position=cycle_runner.Position,
        settings=types.SimpleNamespace(mode="paper"),
    )

    with pytest.raises(ValueError, match="strategy_key"):
        cycle_runtime.materialize_position(
            candidate,
            decision,
            result,
            cycle_runner.PortfolioState(),
            city,
            DiscoveryMode.UPDATE_REACTION,
            state="entered",
            bankroll_at_entry=100.0,
            deps=deps,
        )


def test_execution_stub_does_not_reinvent_strategy_without_strategy_key():
    decision = evaluator_module.EdgeDecision(
        should_trade=True,
        edge=_edge(),
        tokens={"market_id": "m1", "token_id": "yes1", "no_token_id": "no1"},
        decision_id="d1",
        edge_source="opening_inertia",
        strategy_key="",
        decision_snapshot_id="snap1",
    )
    result = types.SimpleNamespace(trade_id="t1", order_id="o1", status="rejected")
    city = types.SimpleNamespace(name="New York")
    candidate = types.SimpleNamespace(target_date="2026-04-01")
    deps = types.SimpleNamespace(_classify_edge_source=lambda mode, edge: "opening_inertia")

    stub = cycle_runtime._execution_stub(
        candidate,
        decision,
        result,
        city,
        DiscoveryMode.UPDATE_REACTION,
        deps=deps,
    )

    assert stub.strategy_key == ""
    assert stub.strategy == ""


def test_load_portfolio_backfills_strategy_key_from_legacy_strategy(tmp_path):
    path = tmp_path / "positions-paper.json"
    path.write_text(json.dumps({
        "positions": [{
            "trade_id": "t1",
            "market_id": "m1",
            "city": "NYC",
            "cluster": "US-Northeast",
            "target_date": "2026-04-01",
            "bin_label": "39-40°F",
            "direction": "buy_yes",
            "unit": "F",
            "token_id": "yes123",
            "no_token_id": "no456",
            "state": "entered",
            "strategy": "center_buy",
            "edge_source": "center_buy",
        }],
        "bankroll": 150.0,
    }))

    state = load_portfolio(path)

    assert state.positions[0].strategy_key == "center_buy"
    assert state.positions[0].strategy == "center_buy"


def test_lead_days_use_city_local_reference_time():
    lead_days = lead_days_to_target(
        "2026-04-01",
        "Asia/Tokyo",
        datetime(2026, 3, 30, 23, 30, tzinfo=timezone.utc),
    )

    assert lead_days == pytest.approx(15.5 / 24.0)


def test_evaluator_projects_exposure_across_multiple_edges(monkeypatch):
    candidate = MarketCandidate(
        city=NYC,
        target_date="2026-04-01",
        outcomes=[
            {
                "title": "38°F or below",
                "range_low": None,
                "range_high": 38,
                "token_id": "yes1",
                "no_token_id": "no1",
                "market_id": "m1",
                "price": 0.20,
            },
            {
                "title": "39-40°F",
                "range_low": 39,
                "range_high": 40,
                "token_id": "yes2",
                "no_token_id": "no2",
                "market_id": "m2",
                "price": 0.35,
            },
            {
                "title": "41°F or higher",
                "range_low": 41,
                "range_high": None,
                "token_id": "yes3",
                "no_token_id": "no3",
                "market_id": "m3",
                "price": 0.45,
            },
        ],
        hours_since_open=30.0,
        hours_to_resolution=24.0,
        discovery_mode=DiscoveryMode.UPDATE_REACTION.value,
    )

    class DummyEnsembleSignal:
        def __init__(self, members_hourly, times, city, target_d, settlement_semantics=None, decision_time=None):
            self.member_maxes = np.full(51, 40.0)

        def p_raw_vector(self, bins, n_mc=3000):
            return np.array([0.25, 0.50, 0.25])

        def spread(self):
            from src.types.temperature import TemperatureDelta

            return TemperatureDelta(2.0, "F")

        def spread_float(self):
            return 2.0

        def is_bimodal(self):
            return False

    edges = [
        BinEdge(
            bin=Bin(low=39, high=40, label="39-40°F", unit="F"),
            direction="buy_yes",
            edge=0.12,
            ci_lower=0.05,
            ci_upper=0.15,
            p_model=0.60,
            p_market=0.35,
            p_posterior=0.47,
            entry_price=0.35,
            p_value=0.02,
            vwmp=0.35,
        ),
        BinEdge(
            bin=Bin(low=41, high=None, label="41°F or higher", unit="F"),
            direction="buy_yes",
            edge=0.11,
            ci_lower=0.04,
            ci_upper=0.13,
            p_model=0.55,
            p_market=0.45,
            p_posterior=0.49,
            entry_price=0.45,
            p_value=0.03,
            vwmp=0.45,
        ),
    ]

    class DummyAnalysis:
        def __init__(self, **kwargs):
            pass

        def find_edges(self, n_bootstrap=500):
            result = list(edges)
            for e in result:
                e.forward_edge = e.p_posterior - e.p_market
            return result

        def sigma_context(self):
            return {"base_sigma": 0.5, "lead_multiplier": 1.1, "spread_multiplier": 1.05, "final_sigma": 0.5775}

        def mean_context(self):
            return {"offset": 0.0, "lead_days": 1.5}

    heats: list[float] = []

    def _check_position_allowed(**kwargs):
        heats.append(kwargs["current_portfolio_heat"])
        projected = kwargs["current_portfolio_heat"] + (kwargs["size_usd"] / kwargs["bankroll"])
        return (projected <= 0.5, "portfolio_heat")

    class DummyClob:
        def get_best_bid_ask(self, token_id):
            return (0.34, 0.36, 20.0, 20.0)

    monkeypatch.setattr(
        evaluator_module,
        "fetch_ensemble",
        lambda city, forecast_days=2, model=None: {
            "members_hourly": np.ones(((31 if model == "gfs025" else 51), 24)) * 40.0,
            "times": [
                datetime(2026, 4, 1, hour, 0, tzinfo=timezone.utc).isoformat()
                for hour in range(24)
            ],
            "issue_time": datetime(2026, 4, 1, 0, 0, tzinfo=timezone.utc),
            "fetch_time": datetime(2026, 4, 1, 23, 30, tzinfo=timezone.utc),
            "model": model or "ecmwf_ifs025",
        },
    )
    monkeypatch.setattr(evaluator_module, "validate_ensemble", lambda result, expected_members=51: result is not None)
    monkeypatch.setattr(evaluator_module, "EnsembleSignal", DummyEnsembleSignal)
    monkeypatch.setattr(evaluator_module, "_store_ens_snapshot", lambda *args, **kwargs: "snap-1")
    monkeypatch.setattr(evaluator_module, "_store_snapshot_p_raw", lambda *args, **kwargs: None)
    monkeypatch.setattr(evaluator_module, "get_calibrator", lambda *args, **kwargs: (None, 4))
    monkeypatch.setattr(evaluator_module, "MarketAnalysis", DummyAnalysis)
    monkeypatch.setattr(evaluator_module, "fdr_filter", lambda edges, fdr_alpha=0.10: list(edges))
    monkeypatch.setattr(evaluator_module, "dynamic_kelly_mult", lambda **kwargs: 0.25)
    monkeypatch.setattr(evaluator_module, "kelly_size", lambda *args, **kwargs: 4.0)
    monkeypatch.setattr(evaluator_module, "check_position_allowed", _check_position_allowed)

    decisions = evaluator_module.evaluate_candidate(
        candidate,
        conn=None,
        portfolio=PortfolioState(bankroll=10.0),
        clob=DummyClob(),
        limits=evaluator_module.RiskLimits(max_portfolio_heat_pct=0.5, min_order_usd=1.0),
        entry_bankroll=10.0,
    )

    assert [d.should_trade for d in decisions] == [True, False]
    assert heats[0] == pytest.approx(0.0)
    assert heats[1] == pytest.approx(0.4)


def test_day0_observation_path_reaches_day0_signal(monkeypatch):
    calls: dict[str, object] = {}

    candidate = MarketCandidate(
        city=NYC,
        target_date=str(date.today()),
        outcomes=[
            {
                "title": "39-40°F",
                "range_low": 39,
                "range_high": 40,
                "token_id": "yes1",
                "no_token_id": "no1",
                "market_id": "m1",
                "price": 0.35,
            },
            {
                "title": "41-42°F",
                "range_low": 41,
                "range_high": 42,
                "token_id": "yes2",
                "no_token_id": "no2",
                "market_id": "m2",
                "price": 0.33,
            },
            {
                "title": "43°F or higher",
                "range_low": 43,
                "range_high": None,
                "token_id": "yes3",
                "no_token_id": "no3",
                "market_id": "m3",
                "price": 0.32,
            },
        ],
        hours_since_open=30.0,
        hours_to_resolution=4.0,
        observation={
            "high_so_far": 44.0,
            "current_temp": 43.0,
            "source": "wu_api",
            "observation_time": datetime.now(timezone.utc).isoformat(),
            "unit": "F",
        },
        discovery_mode=DiscoveryMode.DAY0_CAPTURE.value,
    )

    class DummyDay0Signal:
        def __init__(self, observed_high_so_far, current_temp, hours_remaining, member_maxes_remaining, unit="F", diurnal_peak_confidence=0.0, **kwargs):
            calls["observed_high_so_far"] = observed_high_so_far
            calls["hours_remaining"] = hours_remaining
            calls["unit"] = unit
            calls["temporal_context"] = kwargs.get("temporal_context")

        def p_vector(self, bins, n_mc=3000):
            calls["bins"] = [b.label for b in bins]
            return np.array([0.60, 0.30, 0.10])

        def forecast_context(self):
            return {
                "observation_weight": 0.5,
                "temporal_closure_weight": 0.4,
                "backbone": {
                    "observation_source": "wu_api",
                    "backbone_high": 44.0,
                    "residual_adjustment": 0.0,
                },
            }

    class DummyEnsembleSignal:
        def __init__(self, members_hourly, times, city, target_d, settlement_semantics=None, decision_time=None):
            self.member_maxes = np.full(51, 44.0)

        def spread(self):
            from src.types.temperature import TemperatureDelta

            return TemperatureDelta(2.0, "F")

        def spread_float(self):
            return 2.0

        def is_bimodal(self):
            return False

    class DummyAnalysis:
        def __init__(self, **kwargs):
            self.bins = kwargs["bins"]

        def find_edges(self, n_bootstrap=500):
            result = [_edge()]
            for e in result:
                e.forward_edge = e.p_posterior - e.p_market
            return result

        def sigma_context(self):
            return {"base_sigma": 0.5, "lead_multiplier": 1.0, "spread_multiplier": 1.0, "final_sigma": 0.5}

        def mean_context(self):
            return {"offset": 0.0, "lead_days": 0.0}

    class DummyClob:
        def get_best_bid_ask(self, token_id):
            return (0.34, 0.36, 20.0, 20.0)

    monkeypatch.setattr(
        evaluator_module,
        "fetch_ensemble",
        lambda city, forecast_days=2, model=None: None if model == "gfs025" else {
            "members_hourly": np.ones((51, 12)) * 44.0,
            "times": [
                datetime.now(timezone.utc).replace(microsecond=0).isoformat()
                for _ in range(12)
            ],
            "issue_time": datetime.now(timezone.utc),
            "fetch_time": datetime.now(timezone.utc),
            "model": "ecmwf_ifs025",
        },
    )
    monkeypatch.setattr(evaluator_module, "validate_ensemble", lambda result, expected_members=51: result is not None)
    monkeypatch.setattr(evaluator_module, "EnsembleSignal", DummyEnsembleSignal)
    monkeypatch.setattr(evaluator_module, "Day0Signal", DummyDay0Signal)
    def _remaining_for_day0(members_hourly, times, timezone_name, target_d, now=None):
        calls["day0_now"] = now
        return np.full(51, 44.0), 6.0
    monkeypatch.setattr(evaluator_module, "remaining_member_maxes_for_day0", _remaining_for_day0)
    monkeypatch.setattr(evaluator_module, "_store_ens_snapshot", lambda *args, **kwargs: "snap-day0")
    monkeypatch.setattr(evaluator_module, "_store_snapshot_p_raw", lambda *args, **kwargs: None)
    monkeypatch.setattr(evaluator_module, "get_calibrator", lambda *args, **kwargs: (None, 4))
    monkeypatch.setattr(evaluator_module, "MarketAnalysis", DummyAnalysis)
    monkeypatch.setattr(evaluator_module, "fdr_filter", lambda edges, fdr_alpha=0.10: edges)
    monkeypatch.setattr(evaluator_module, "dynamic_kelly_mult", lambda **kwargs: 0.25)
    monkeypatch.setattr(evaluator_module, "kelly_size", lambda *args, **kwargs: 5.0)
    monkeypatch.setattr(evaluator_module, "check_position_allowed", lambda **kwargs: (True, "OK"))
    monkeypatch.setattr(
        evaluator_module,
        "_get_day0_temporal_context",
        lambda city, target_date, observation=None: Day0TemporalContext(
            city=city.name,
            target_date=target_date,
            timezone=city.timezone,
            current_local_timestamp=datetime(2026, 4, 1, 12, 0, tzinfo=timezone.utc).astimezone(timezone.utc),
            current_utc_timestamp=datetime(2026, 4, 1, 12, 0, tzinfo=timezone.utc),
            current_local_hour=12.0,
            solar_day=type("Solar", (), {"phase": lambda self, hour: "daylight", "daylight_progress": lambda self, hour: 0.5})(),
            observation_instant=None,
            peak_hour=15,
            post_peak_confidence=0.4,
            daylight_progress=0.5,
            utc_offset_minutes=0,
            dst_active=False,
            time_basis="test",
            confidence_source="test",
        ),
    )

    decisions = evaluator_module.evaluate_candidate(
        candidate,
        conn=None,
        portfolio=PortfolioState(bankroll=150.0),
        clob=DummyClob(),
        limits=evaluator_module.RiskLimits(),
    )

    assert decisions[0].should_trade is True
    assert decisions[0].selected_method == "day0_observation"
    assert calls["observed_high_so_far"] == pytest.approx(44.0)
    assert calls["temporal_context"] is not None
    forecast_context = json.loads(decisions[0].epistemic_context_json)["forecast_context"]["day0"]
    assert forecast_context["observation_weight"] >= 0.0
    assert forecast_context["backbone"]["observation_source"] == "wu_api"
    assert calls["temporal_context"].current_local_hour == 12.0
    assert calls["day0_now"] == datetime(2026, 4, 1, 12, 0, tzinfo=timezone.utc)
    assert "39-40°F" in calls["bins"]


def test_day0_observation_path_rejects_missing_solar_context(monkeypatch):
    candidate = MarketCandidate(
        city=NYC,
        target_date=str(date.today()),
        outcomes=[
            {"title": "39-40°F", "range_low": 39, "range_high": 40, "token_id": "yes1", "no_token_id": "no1", "market_id": "m1", "price": 0.35},
            {"title": "41-42°F", "range_low": 41, "range_high": 42, "token_id": "yes2", "no_token_id": "no2", "market_id": "m2", "price": 0.33},
            {"title": "43°F or higher", "range_low": 43, "range_high": None, "token_id": "yes3", "no_token_id": "no3", "market_id": "m3", "price": 0.32},
        ],
        hours_since_open=30.0,
        hours_to_resolution=4.0,
        observation={
            "high_so_far": 44.0,
            "current_temp": 43.0,
            "source": "wu_api",
            "observation_time": datetime.now(timezone.utc).isoformat(),
            "unit": "F",
        },
        discovery_mode=DiscoveryMode.DAY0_CAPTURE.value,
    )

    monkeypatch.setattr(
        evaluator_module,
        "fetch_ensemble",
        lambda city, forecast_days=2, model=None: (
            lambda base_utc: {
                "members_hourly": np.ones((51, 12)) * 44.0,
                "times": [
                    (base_utc + timedelta(hours=i)).replace(microsecond=0).isoformat()
                    for i in range(12)
                ],
                "issue_time": datetime.now(timezone.utc),
                "fetch_time": datetime.now(timezone.utc),
                "model": "ecmwf_ifs025",
            }
        )(
            datetime.combine(
                date.fromisoformat(candidate.target_date),
                datetime.min.time(),
                tzinfo=timezone.utc,
            ) + timedelta(hours=4)
        ),
    )
    monkeypatch.setattr(evaluator_module, "validate_ensemble", lambda result, expected_members=51: result is not None)
    monkeypatch.setattr(evaluator_module, "_store_ens_snapshot", lambda *args, **kwargs: "snap-day0")
    monkeypatch.setattr(evaluator_module, "_store_snapshot_p_raw", lambda *args, **kwargs: None)
    monkeypatch.setattr(evaluator_module, "_get_day0_temporal_context", lambda city, target_date, observation=None: None)

    decisions = evaluator_module.evaluate_candidate(
        candidate,
        conn=None,
        portfolio=PortfolioState(bankroll=150.0),
        clob=type("DummyClob", (), {"get_best_bid_ask": lambda self, token_id: (0.34, 0.36, 20.0, 20.0)})(),
        limits=evaluator_module.RiskLimits(),
    )

    assert len(decisions) == 1
    assert decisions[0].should_trade is False
    assert decisions[0].rejection_stage == "SIGNAL_QUALITY"
    assert "Solar/DST context unavailable for Day0" in decisions[0].rejection_reasons[0]


def test_gfs_crosscheck_uses_local_target_day_hours_instead_of_first_24h(monkeypatch):
    target_date = "2026-01-15"
    calls: dict[str, np.ndarray] = {}

    candidate = MarketCandidate(
        city=NYC,
        target_date=target_date,
        outcomes=[
            {
                "title": "32°F or below",
                "range_low": None,
                "range_high": 32,
                "token_id": "yes-low",
                "no_token_id": "no-low",
                "market_id": "m-low",
                "price": 0.30,
            },
            {
                "title": "39-40°F",
                "range_low": 39,
                "range_high": 40,
                "token_id": "yes-mid",
                "no_token_id": "no-mid",
                "market_id": "m-mid",
                "price": 0.31,
            },
            {
                "title": "51°F or higher",
                "range_low": 51,
                "range_high": None,
                "token_id": "yes-high",
                "no_token_id": "no-high",
                "market_id": "m-high",
                "price": 0.32,
            },
        ],
        hours_since_open=8.0,
        hours_to_resolution=24.0,
        discovery_mode=DiscoveryMode.OPENING_HUNT.value,
    )

    tz = ZoneInfo(NYC.timezone)
    start_local = datetime(2026, 1, 14, 0, 0, tzinfo=tz)
    times = [
        (start_local + timedelta(hours=i)).astimezone(timezone.utc).isoformat()
        for i in range(48)
    ]
    ecmwf_members = np.full((51, 48), 55.0)
    gfs_members = np.concatenate(
        [
            np.full((31, 24), 20.0),
            np.full((31, 24), 60.0),
        ],
        axis=1,
    )

    class DummyEnsembleSignal:
        def __init__(self, members_hourly, times, city, target_d, settlement_semantics=None, decision_time=None):
            self.member_maxes = np.full(51, 55.0)

        def p_raw_vector(self, bins):
            return np.array([0.0, 0.0, 1.0])

        def spread(self):
            from src.types.temperature import TemperatureDelta

            return TemperatureDelta(1.0, "F")

        def spread_float(self):
            return 1.0

        def is_bimodal(self):
            return False

    class DummyAnalysis:
        def __init__(self, **kwargs):
            pass

        def find_edges(self, n_bootstrap=500):
            return []

        def sigma_context(self):
            return {"base_sigma": 0.5, "lead_multiplier": 1.1, "spread_multiplier": 1.05, "final_sigma": 0.5775}

        def mean_context(self):
            return {"offset": 0.0, "lead_days": 1.5}

    class DummyClob:
        def get_best_bid_ask(self, token_id):
            return (0.29, 0.31, 10.0, 10.0)

    def _fetch_ensemble(city, forecast_days=2, model=None):
        if model == "gfs025":
            return {
                "members_hourly": gfs_members,
                "times": times,
                "issue_time": None,
                "first_valid_time": datetime(2026, 1, 14, 5, 0, tzinfo=timezone.utc),
                "fetch_time": datetime(2026, 1, 14, 6, 0, tzinfo=timezone.utc),
                "model": "gfs025",
                "n_members": 31,
            }
        return {
            "members_hourly": ecmwf_members,
            "times": times,
            "issue_time": None,
            "first_valid_time": datetime(2026, 1, 14, 5, 0, tzinfo=timezone.utc),
            "fetch_time": datetime(2026, 1, 14, 6, 0, tzinfo=timezone.utc),
            "model": "ecmwf_ifs025",
            "n_members": 51,
        }

    def _model_agreement(p_raw, gfs_p):
        calls["gfs_p"] = gfs_p
        return "AGREE"

    monkeypatch.setattr(evaluator_module, "fetch_ensemble", _fetch_ensemble)
    monkeypatch.setattr(evaluator_module, "validate_ensemble", lambda result, expected_members=51: result is not None)
    monkeypatch.setattr(evaluator_module, "EnsembleSignal", DummyEnsembleSignal)
    monkeypatch.setattr(evaluator_module, "model_agreement", _model_agreement)
    monkeypatch.setattr(evaluator_module, "_store_ens_snapshot", lambda *args, **kwargs: "snap-gfs")
    monkeypatch.setattr(evaluator_module, "_store_snapshot_p_raw", lambda *args, **kwargs: None)
    monkeypatch.setattr(evaluator_module, "get_calibrator", lambda *args, **kwargs: (None, 4))
    monkeypatch.setattr(evaluator_module, "MarketAnalysis", DummyAnalysis)
    monkeypatch.setattr(evaluator_module, "fdr_filter", lambda edges, fdr_alpha=0.10: list(edges))

    decisions = evaluator_module.evaluate_candidate(
        candidate,
        conn=None,
        portfolio=PortfolioState(bankroll=150.0),
        clob=DummyClob(),
        limits=evaluator_module.RiskLimits(),
        decision_time=datetime(2026, 1, 14, 6, 0, tzinfo=timezone.utc),
    )

    assert len(decisions) == 1
    assert decisions[0].agreement == "AGREE"
    np.testing.assert_allclose(calls["gfs_p"], np.array([0.0, 0.0, 1.0]))


def test_gfs_crosscheck_failure_rejects_instead_of_defaulting_to_agree(monkeypatch):
    candidate = MarketCandidate(
        city=NYC,
        target_date="2026-01-15",
        outcomes=[
            {"title": "32°F or below", "range_low": None, "range_high": 32, "token_id": "yes1", "no_token_id": "no1", "market_id": "m1", "price": 0.35},
            {"title": "33-34°F", "range_low": 33, "range_high": 34, "token_id": "yes2", "no_token_id": "no2", "market_id": "m2", "price": 0.33},
            {"title": "35°F or higher", "range_low": 35, "range_high": None, "token_id": "yes3", "no_token_id": "no3", "market_id": "m3", "price": 0.32},
        ],
        hours_since_open=30.0,
        hours_to_resolution=40.0,
        discovery_mode=DiscoveryMode.OPENING_HUNT.value,
    )

    def _fetch(city, forecast_days=2, model=None):
        if model == "gfs025":
            return {
                "members_hourly": np.ones((31, 6)) * 40.0,
                "times": ["2026-01-14T00:00:00Z"] * 6,
                "issue_time": None,
                "first_valid_time": datetime(2026, 1, 14, 5, 0, tzinfo=timezone.utc),
                "fetch_time": datetime(2026, 1, 14, 6, 0, tzinfo=timezone.utc),
                "model": "gfs025",
                "n_members": 31,
            }
        return {
            "members_hourly": np.ones((51, 30)) * 40.0,
            "times": [f"2026-01-15T{hour:02d}:00:00Z" for hour in range(24)] + [f"2026-01-16T{hour:02d}:00:00Z" for hour in range(6)],
            "issue_time": None,
            "first_valid_time": datetime(2026, 1, 14, 5, 0, tzinfo=timezone.utc),
            "fetch_time": datetime(2026, 1, 14, 6, 0, tzinfo=timezone.utc),
            "model": "ecmwf_ifs025",
            "n_members": 51,
        }

    monkeypatch.setattr(evaluator_module, "fetch_ensemble", _fetch)
    monkeypatch.setattr(evaluator_module, "validate_ensemble", lambda result, expected_members=51: result is not None)
    monkeypatch.setattr(evaluator_module, "_store_ens_snapshot", lambda *args, **kwargs: "snap-gfs-fail")
    monkeypatch.setattr(evaluator_module, "_store_snapshot_p_raw", lambda *args, **kwargs: None)
    monkeypatch.setattr(evaluator_module, "get_calibrator", lambda *args, **kwargs: (None, 4))

    decisions = evaluator_module.evaluate_candidate(
        candidate,
        conn=None,
        portfolio=PortfolioState(bankroll=150.0),
        clob=type("DummyClob", (), {"get_best_bid_ask": lambda self, token_id: (0.34, 0.36, 20.0, 20.0)})(),
        limits=evaluator_module.RiskLimits(),
        decision_time=datetime(2026, 1, 14, 6, 0, tzinfo=timezone.utc),
    )

    assert len(decisions) == 1
    assert decisions[0].should_trade is False
    assert decisions[0].rejection_stage == "SIGNAL_QUALITY"
    assert decisions[0].agreement == "CROSSCHECK_UNAVAILABLE"


def test_build_exit_context_uses_market_price_as_best_bid_in_paper_mode():
    edge_ctx = type(
        "EdgeContext",
        (),
        {
            "p_posterior": 0.41,
            "p_market": np.array([0.46]),
            "divergence_score": 0.0,
            "market_velocity_1h": 0.0,
        },
    )()
    pos = Position(
        trade_id="paper-buy-yes",
        market_id="m1",
        city="NYC",
        cluster="US-Northeast",
        target_date="2026-04-01",
        bin_label="39-40°F",
        direction="buy_yes",
        state="holding",
        last_monitor_prob=0.41,
        last_monitor_prob_is_fresh=True,
        last_monitor_market_price=0.46,
        last_monitor_market_price_is_fresh=True,
    )

    ctx = cycle_runtime._build_exit_context(
        pos,
        edge_ctx,
        hours_to_settlement=4.0,
        paper_mode=True,
        ExitContext=ExitContext,
    )

    assert ctx.best_bid == pytest.approx(0.46)


def test_monitoring_skips_sell_pending_when_chain_already_missing():
    pos = Position(
        trade_id="retry-missing-chain",
        market_id="m1",
        city="NYC",
        cluster="US-Northeast",
        target_date="2026-04-01",
        bin_label="39-40°F",
        direction="buy_yes",
        state="holding",
        chain_state="exit_pending_missing",
        exit_state="sell_pending",
        last_exit_order_id="sell-order-keep",
        next_exit_retry_at="2026-04-01T00:05:00Z",
    )
    portfolio = PortfolioState(positions=[pos])
    artifact = cycle_runner.CycleArtifact(mode="test", started_at="2026-01-01T00:00:00Z")
    summary = {"monitors": 0, "exits": 0}

    class Tracker:
        def record_exit(self, position):
            raise AssertionError("should not record exit")

    class LiveClob:
        paper_mode = False
        def get_order_status(self, order_id):
            return {"status": "UNKNOWN"}

    p_dirty, t_dirty = cycle_runner._execute_monitoring_phase(
        None,
        LiveClob(),
        portfolio,
        artifact,
        Tracker(),
        summary,
    )

    assert p_dirty is False
    assert t_dirty is False
    assert pos.exit_state == "sell_pending"
    assert summary["monitor_skipped_exit_pending_missing"] == 1


def test_monitoring_admin_closes_retry_pending_when_chain_missing_after_recovery():
    pos = Position(
        trade_id="retry-missing-chain-close",
        market_id="m1",
        city="NYC",
        cluster="US-Northeast",
        target_date="2026-04-01",
        bin_label="39-40°F",
        direction="buy_yes",
        state="holding",
        chain_state="exit_pending_missing",
        exit_state="retry_pending",
        next_exit_retry_at=None,
    )
    portfolio = PortfolioState(positions=[pos])
    artifact = cycle_runner.CycleArtifact(mode="test", started_at="2026-01-01T00:00:00Z")
    summary = {"monitors": 0, "exits": 0}

    class Tracker:
        def __init__(self):
            self.exits = []
        def record_exit(self, position):
            self.exits.append(position)

    class LiveClob:
        paper_mode = False
        def get_order_status(self, order_id):
            return {"status": "UNKNOWN"}

    tracker = Tracker()
    p_dirty, t_dirty = cycle_runner._execute_monitoring_phase(
        None,
        LiveClob(),
        portfolio,
        artifact,
        tracker,
        summary,
    )

    assert p_dirty is True
    assert t_dirty is True
    assert portfolio.positions == []
    assert tracker.exits[0].exit_reason == "EXIT_CHAIN_MISSING_REVIEW_REQUIRED"
    assert summary["exit_chain_missing_closed"] == 1


def test_openmeteo_parse_keeps_first_valid_time_and_does_not_fake_issue_time():
    fetch_time = datetime(2026, 1, 14, 6, 5, tzinfo=timezone.utc)
    parsed = ensemble_client._parse_response(
        {
            "hourly": {
                "time": ["2026-01-14T05:00:00+00:00", "2026-01-14T06:00:00+00:00"],
                "temperature_2m": [40.0, 41.0],
                **{f"temperature_2m_member{i:02d}": [40.0, 41.0] for i in range(1, 3)},
            }
        },
        "ecmwf_ifs025",
        fetch_time,
    )

    assert parsed["issue_time"] is None
    assert parsed["first_valid_time"] == datetime(2026, 1, 14, 5, 0, tzinfo=timezone.utc)


def test_store_ens_snapshot_marks_degraded_clock_metadata_explicitly(tmp_path):
    db_path = tmp_path / "zeus.db"
    conn = get_connection(db_path)
    init_schema(conn)

    fetch_time = datetime(2026, 1, 14, 6, 5, tzinfo=timezone.utc)
    ens = type(
        "DummyEns",
        (),
        {
            "member_maxes": np.array([40.0, 41.0, 42.0]),
            "spread_float": lambda self: 1.25,
            "is_bimodal": lambda self: False,
        },
    )()
    ens_result = {
        "issue_time": None,
        "first_valid_time": datetime(2026, 1, 14, 5, 0, tzinfo=timezone.utc),
        "fetch_time": fetch_time,
        "model": "ecmwf_ifs025",
    }

    snapshot_id = evaluator_module._store_ens_snapshot(
        conn,
        NYC,
        "2026-01-15",
        ens,
        ens_result,
    )
    row = conn.execute(
        """
        SELECT issue_time, valid_time, available_at, fetch_time
        FROM ensemble_snapshots
        WHERE snapshot_id = ?
        """,
        (snapshot_id,),
    ).fetchone()
    conn.close()

    assert row is not None
    assert row["issue_time"] == (
        "UNAVAILABLE_UPSTREAM_ISSUE_TIME(fetch_time=2026-01-14T06:05:00+00:00)"
    )
    assert row["valid_time"] == "FORECAST_WINDOW_START(2026-01-14T05:00:00+00:00)"
    assert row["available_at"] == "2026-01-14T06:05:00+00:00"
    assert row["fetch_time"] == "2026-01-14T06:05:00+00:00"


def test_open_ens_collection_stores_snapshots(monkeypatch, tmp_path):
    db_path = tmp_path / "zeus.db"
    conn = get_connection(db_path)
    init_schema(conn)

    test_city = City(
        name="NYC",
        lat=40.7772,
        lon=-73.8726,
        timezone="America/New_York",
        cluster="US-Northeast",
        settlement_unit="F",
        wu_station="KLGA",
    )
    call_count = {"n": 0}

    def _fake_run(args):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return {"generated_at": "2026-03-30T01:45:00+00:00"}
        return {
            "members": [
                {"step_range": "24", "value_native_unit": 44.0},
                {"step_range": "24", "value_native_unit": 45.0},
                {"step_range": "48", "value_native_unit": 46.0},
                {"step_range": "48", "value_native_unit": 47.0},
            ]
        }

    monkeypatch.setattr("src.data.ecmwf_open_data._run_json_command", _fake_run)
    monkeypatch.setattr("src.data.ecmwf_open_data.cities", [test_city])

    result = collect_open_ens_cycle(run_date=date(2026, 3, 30), run_hour=0, conn=conn)
    rows = conn.execute(
        "SELECT city, target_date, data_version, model_version, p_raw_json FROM ensemble_snapshots ORDER BY target_date"
    ).fetchall()
    conn.close()

    assert result["snapshots_inserted"] == 2
    assert [row["target_date"] for row in rows] == ["2026-03-31", "2026-04-01"]
    assert all(row["data_version"] == DATA_VERSION for row in rows)
    assert all(row["p_raw_json"] is None for row in rows)


def test_main_registers_ecmwf_open_data_jobs(monkeypatch, tmp_path):
    blocking_module = types.ModuleType("apscheduler.schedulers.blocking")

    class BootstrapScheduler:
        def add_job(self, *args, **kwargs):
            return None

        def get_jobs(self):
            return []

        def start(self):
            return None

    blocking_module.BlockingScheduler = BootstrapScheduler
    monkeypatch.setitem(sys.modules, "apscheduler", types.ModuleType("apscheduler"))
    monkeypatch.setitem(sys.modules, "apscheduler.schedulers", types.ModuleType("apscheduler.schedulers"))
    monkeypatch.setitem(sys.modules, "apscheduler.schedulers.blocking", blocking_module)

    import importlib

    main_module = importlib.import_module("src.main")
    db_path = tmp_path / "zeus.db"

    class FakeJob:
        def __init__(self, job_id):
            self.id = job_id

    class FakeScheduler:
        def __init__(self):
            self.jobs = []

        def add_job(self, func, trigger, **kwargs):
            self.jobs.append(FakeJob(kwargs["id"]))

        def get_jobs(self):
            return list(self.jobs)

        def start(self):
            return None

    fake_scheduler = FakeScheduler()

    monkeypatch.setattr(main_module, "BlockingScheduler", lambda: fake_scheduler)
    monkeypatch.setattr(main_module, "get_connection", lambda: get_connection(db_path))
    monkeypatch.setattr(main_module, "init_schema", lambda conn: None)
    monkeypatch.setattr(main_module.os, "environ", {})
    monkeypatch.setattr(main_module.sys, "argv", ["zeus"])

    main_module.main()

    assert any(job.id.startswith("ecmwf_open_data_") for job in fake_scheduler.get_jobs())


def test_openmeteo_quota_warns_blocks_and_resets(caplog):
    tracker = OpenMeteoQuotaTracker()
    tracker._count = int(DAILY_LIMIT * 0.80) - 1

    with caplog.at_level("WARNING"):
        tracker.record_call("ensemble")
    assert tracker.calls_today() == int(DAILY_LIMIT * 0.80)
    assert "WARNING" in caplog.text

    tracker._count = int(DAILY_LIMIT * HARD_THRESHOLD)
    assert tracker.can_call() is False

    tracker._today = date(2000, 1, 1)
    tracker._count = 9000
    assert tracker.calls_today() == 0


def test_openmeteo_quota_cooldown_blocks_after_429():
    tracker = OpenMeteoQuotaTracker()
    tracker.note_rate_limited(30)

    assert tracker.cooldown_remaining_seconds() >= 299
    assert tracker.can_call() is False


def test_fetch_ensemble_caches_identical_request(monkeypatch):
    ensemble_client._clear_cache()

    calls = {"n": 0}

    class _Resp:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "hourly": {
                    "time": ["2026-03-31T00:00"],
                    "temperature_2m": [70.0],
                    **{f"temperature_2m_member{i:02d}": [70.0] for i in range(1, 51)},
                }
            }

    monkeypatch.setattr(ensemble_client.quota_tracker, "can_call", lambda: True)
    monkeypatch.setattr(ensemble_client.quota_tracker, "record_call", lambda endpoint="": None)

    def _fake_get(*args, **kwargs):
        calls["n"] += 1
        return _Resp()

    monkeypatch.setattr(ensemble_client.httpx, "get", _fake_get)

    first = ensemble_client.fetch_ensemble(NYC, forecast_days=3, model="ecmwf_ifs025")
    second = ensemble_client.fetch_ensemble(NYC, forecast_days=3, model="ecmwf_ifs025")

    assert first is not None
    assert second is not None
    assert calls["n"] == 1


def test_fetch_ensemble_reuses_longer_horizon_for_shorter_request(monkeypatch):
    ensemble_client._clear_cache()

    calls = {"n": 0}

    class _Resp:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "hourly": {
                    "time": ["2026-03-31T00:00"],
                    "temperature_2m": [70.0],
                    **{f"temperature_2m_member{i:02d}": [70.0] for i in range(1, 51)},
                }
            }

    monkeypatch.setattr(ensemble_client.quota_tracker, "can_call", lambda: True)
    monkeypatch.setattr(ensemble_client.quota_tracker, "record_call", lambda endpoint="": None)

    def _fake_get(*args, **kwargs):
        calls["n"] += 1
        return _Resp()

    monkeypatch.setattr(ensemble_client.httpx, "get", _fake_get)

    long_result = ensemble_client.fetch_ensemble(NYC, forecast_days=8, model="ecmwf_ifs025")
    short_result = ensemble_client.fetch_ensemble(NYC, forecast_days=3, model="ecmwf_ifs025")

    assert long_result is not None
    assert short_result is not None
    assert calls["n"] == 1


def test_run_cycle_clears_ensemble_cache_each_cycle(monkeypatch, tmp_path):
    db_path = tmp_path / "zeus.db"
    conn = get_connection(db_path)
    init_schema(conn)
    conn.close()

    class DummyClob:
        def __init__(self, paper_mode):
            self.paper_mode = True

    cleared = {"n": 0}

    monkeypatch.setattr(cycle_runner, "get_current_level", lambda: RiskLevel.GREEN)
    monkeypatch.setattr(cycle_runner, "get_connection", lambda: get_connection(db_path))
    monkeypatch.setattr(cycle_runner, "load_portfolio", lambda: PortfolioState())
    monkeypatch.setattr(cycle_runner, "save_portfolio", lambda state: None)
    monkeypatch.setattr(cycle_runner, "PolymarketClient", DummyClob)
    monkeypatch.setattr(cycle_runner, "get_tracker", lambda: StrategyTracker())
    monkeypatch.setattr(cycle_runner, "save_tracker", lambda tracker: None)
    monkeypatch.setattr(cycle_runner, "find_weather_markets", lambda **kwargs: [])
    monkeypatch.setattr("src.control.control_plane.process_commands", lambda: [])
    monkeypatch.setattr("src.observability.status_summary.write_status", lambda cycle_summary=None: None)
    monkeypatch.setattr("src.data.ensemble_client._clear_cache", lambda: cleared.__setitem__("n", cleared["n"] + 1))

    cycle_runner.run_cycle(DiscoveryMode.OPENING_HUNT)

    assert cleared["n"] == 1


def test_run_cycle_clears_market_scanner_cache_each_cycle(monkeypatch, tmp_path):
    db_path = tmp_path / "zeus.db"
    conn = get_connection(db_path)
    init_schema(conn)
    conn.close()

    class DummyClob:
        def __init__(self, paper_mode):
            self.paper_mode = True

    cleared = {"n": 0}

    monkeypatch.setattr(cycle_runner, "get_current_level", lambda: RiskLevel.GREEN)
    monkeypatch.setattr(cycle_runner, "get_connection", lambda: get_connection(db_path))
    monkeypatch.setattr(cycle_runner, "load_portfolio", lambda: PortfolioState())
    monkeypatch.setattr(cycle_runner, "save_portfolio", lambda state: None)
    monkeypatch.setattr(cycle_runner, "PolymarketClient", DummyClob)
    monkeypatch.setattr(cycle_runner, "get_tracker", lambda: StrategyTracker())
    monkeypatch.setattr(cycle_runner, "save_tracker", lambda tracker: None)
    monkeypatch.setattr(cycle_runner, "find_weather_markets", lambda **kwargs: [])
    monkeypatch.setattr("src.control.control_plane.process_commands", lambda: [])
    monkeypatch.setattr("src.observability.status_summary.write_status", lambda cycle_summary=None: None)
    monkeypatch.setattr("src.data.market_scanner._clear_active_events_cache", lambda: cleared.__setitem__("n", cleared["n"] + 1))

    cycle_runner.run_cycle(DiscoveryMode.OPENING_HUNT)

    assert cleared["n"] == 1


def test_monitoring_phase_uses_tracker_record_exit_for_deferred_sell_fills(monkeypatch):
    class Tracker:
        def __init__(self):
            self.exits = []

        def record_exit(self, position):
            self.exits.append(position.trade_id)

    pos = _position(trade_id="filled-1", state="holding", exit_reason="DEFERRED_SELL_FILL")
    portfolio = PortfolioState(positions=[pos])
    artifact = cycle_runner.CycleArtifact(mode="test", started_at="2026-01-01T00:00:00Z")
    summary = {"monitors": 0, "exits": 0}
    tracker = Tracker()

    monkeypatch.setattr(
        "src.execution.exit_lifecycle.check_pending_exits",
        lambda portfolio, clob, conn=None: {
            "filled": 1,
            "retried": 0,
            "unchanged": 0,
            "filled_positions": [type("ClosedPos", (), {
                "trade_id": "filled-1",
                "exit_reason": "DEFERRED_SELL_FILL",
                "exit_price": 0.44,
            })()],
        },
    )
    monkeypatch.setattr("src.execution.exit_lifecycle.is_exit_cooldown_active", lambda pos: False)
    monkeypatch.setattr("src.execution.exit_lifecycle.check_pending_retries", lambda pos, conn=None: False)

    p_dirty, t_dirty = cycle_runner._execute_monitoring_phase(
        None,
        type("LiveClob", (), {"paper_mode": False})(),
        portfolio,
        artifact,
        tracker,
        summary,
    )

    assert p_dirty is True
    assert t_dirty is True
    assert tracker.exits == ["filled-1"]
    assert summary["pending_exits_filled"] == 1
    assert artifact.exit_cases[0].trade_id == "filled-1"


def test_monitoring_phase_persists_live_exit_telemetry_chain(monkeypatch, tmp_path):
    db_path = tmp_path / "zeus.db"
    conn = get_connection(db_path)
    init_schema(conn)

    pos = _position(
        trade_id="live-exit-1",
        state="holding",
        decision_snapshot_id="snap-live-exit",
        last_monitor_market_price=0.46,
    )
    portfolio = PortfolioState(positions=[pos])
    artifact = cycle_runner.CycleArtifact(mode="test", started_at="2026-01-01T00:00:00Z")
    summary = {"monitors": 0, "exits": 0}

    class Tracker:
        def __init__(self):
            self.exits = []

        def record_exit(self, position):
            self.exits.append(position.trade_id)

    class LiveClob:
        paper_mode = False

        def get_order_status(self, order_id):
            assert order_id == "sell-order-1"
            return {"status": "FILLED"}

    tracker = Tracker()
    captured = {}

    monkeypatch.setattr(cycle_runner, "cities_by_name", {"NYC": NYC}, raising=False)
    monkeypatch.setattr("src.execution.exit_lifecycle.check_sell_collateral", lambda *args, **kwargs: (True, None))
    def _refresh_position(conn, clob, pos):
        pos.last_monitor_market_price = 0.46
        pos.last_monitor_market_price_is_fresh = True
        pos.last_monitor_best_bid = 0.46
        pos.last_monitor_best_ask = 0.49
        pos.last_monitor_prob = 0.41
        pos.last_monitor_prob_is_fresh = True
        return type(
            "EdgeContext",
            (),
            {
                "p_market": np.array([0.46]),
                "p_posterior": 0.41,
                "divergence_score": 0.0,
                "market_velocity_1h": 0.0,
                "forward_edge": -0.08,
            },
        )()

    monkeypatch.setattr("src.engine.monitor_refresh.refresh_position", _refresh_position)

    def _evaluate_exit(self, exit_context):
        captured["context"] = exit_context
        return ExitDecision(
            True,
            "forward edge failed",
            selected_method=self.selected_method or self.entry_method,
            applied_validations=list(self.applied_validations),
            trigger="EDGE_REVERSAL",
        )

    monkeypatch.setattr(Position, "evaluate_exit", _evaluate_exit)
    monkeypatch.setattr(
        "src.execution.exit_lifecycle.execute_exit_order",
        lambda intent: OrderResult(
            trade_id=intent.trade_id,
            status="pending",
            order_id="sell-order-1",
            external_order_id="sell-order-1",
            submitted_price=0.46,
            shares=intent.shares,
            order_role="exit",
            venue_status="OPEN",
        ),
    )

    p_dirty, t_dirty = cycle_runner._execute_monitoring_phase(
        conn,
        LiveClob(),
        portfolio,
        artifact,
        tracker,
        summary,
    )

    events = query_position_events(conn, "live-exit-1")

    assert p_dirty is True
    assert t_dirty is True
    assert tracker.exits == ["live-exit-1"]
    assert summary["pending_exits_filled"] == 0
    assert summary["pending_exits_retried"] == 0
    assert summary["monitors"] == 1
    assert summary["exits"] == 1
    assert artifact.exit_cases == []
    assert portfolio.positions == []
    assert captured["context"].fresh_prob == pytest.approx(0.41)
    assert captured["context"].fresh_prob_is_fresh is True
    assert captured["context"].current_market_price == pytest.approx(0.46)
    assert captured["context"].current_market_price_is_fresh is True
    assert captured["context"].best_bid == pytest.approx(0.46)
    assert captured["context"].best_ask == pytest.approx(0.49)
    assert captured["context"].hours_to_settlement is not None
    assert captured["context"].day0_active is True
    assert captured["context"].position_state == "day0_window"
    assert captured["context"].whale_toxicity is None
    assert captured["context"].market_vig is None

    assert [event["event_type"] for event in events] == [
        "EXIT_INTENT",
        "EXIT_ORDER_POSTED",
        "EXIT_ORDER_ATTEMPTED",
        "EXIT_ORDER_FILLED",
    ]

    intent_event, posted_event, attempt_event, fill_event = events
    assert intent_event["event_type"] == "EXIT_INTENT"
    assert intent_event["source"] == "exit_lifecycle"
    assert intent_event["runtime_trade_id"] == "live-exit-1"
    assert intent_event["details"]["status"] in ("", "triggered")
    assert intent_event["details"]["exit_reason"] == "forward edge failed"
    assert posted_event["event_type"] == "EXIT_ORDER_POSTED"
    assert posted_event["source"] == "exit_lifecycle"
    assert posted_event["runtime_trade_id"] == "live-exit-1"
    assert posted_event["order_id"] == "sell-order-1"
    assert posted_event["details"]["last_exit_order_id"] == "sell-order-1"
    assert attempt_event["source"] == "exit_lifecycle"
    assert attempt_event["runtime_trade_id"] == "live-exit-1"
    assert attempt_event["position_state"] == "day0_window"
    assert attempt_event["order_id"] == "sell-order-1"
    assert attempt_event["city"] == "NYC"
    assert attempt_event["target_date"] == "2026-04-01"
    assert attempt_event["market_id"] == "m1"
    assert attempt_event["direction"] == "buy_yes"
    assert attempt_event["strategy"] == "opening_inertia"
    assert attempt_event["edge_source"] == "opening_inertia"
    assert attempt_event["decision_snapshot_id"] == "snap-live-exit"
    assert attempt_event["env"] == "paper"
    assert attempt_event["details"]["status"] == "placed"
    assert attempt_event["details"]["exit_reason"] == "forward edge failed"
    assert attempt_event["details"]["last_exit_order_id"] == "sell-order-1"
    assert attempt_event["details"]["retry_count"] == 0
    assert attempt_event["details"]["next_retry_at"] in (None, "")
    assert attempt_event["details"]["error"] == ""
    assert attempt_event["details"]["best_bid"] == pytest.approx(0.46)
    assert attempt_event["details"]["current_market_price"] == pytest.approx(0.46)
    assert attempt_event["details"]["shares"] == pytest.approx(25.0)

    assert fill_event["source"] == "exit_lifecycle"
    assert fill_event["runtime_trade_id"] == "live-exit-1"
    assert fill_event["position_state"] == "settled"
    assert fill_event["order_id"] == "sell-order-1"
    assert fill_event["city"] == "NYC"
    assert fill_event["target_date"] == "2026-04-01"
    assert fill_event["market_id"] == "m1"
    assert fill_event["direction"] == "buy_yes"
    assert fill_event["strategy"] == "opening_inertia"
    assert fill_event["edge_source"] == "opening_inertia"
    assert fill_event["decision_snapshot_id"] == "snap-live-exit"
    assert fill_event["env"] == "paper"
    assert fill_event["details"]["status"] == "FILLED"
    assert fill_event["details"]["exit_reason"] == "forward edge failed"
    assert fill_event["details"]["last_exit_order_id"] == "sell-order-1"
    assert fill_event["details"]["retry_count"] == 0
    assert fill_event["details"]["next_retry_at"] in (None, "")
    assert fill_event["details"]["error"] == ""
    assert fill_event["details"]["fill_price"] == pytest.approx(0.46)
    assert fill_event["details"]["best_bid"] == pytest.approx(0.46)
    assert fill_event["details"]["current_market_price"] == pytest.approx(0.46)
    assert fill_event["details"]["shares"] == pytest.approx(25.0)

    assert pos.state == "settled"
    assert pos.exit_state == "sell_filled"
    assert pos.exit_trigger == "EDGE_REVERSAL"
    assert pos.exit_reason == "forward edge failed"
    assert pos.last_exit_order_id == "sell-order-1"
    assert pos.last_monitor_prob == pytest.approx(0.41)
    assert pos.last_monitor_market_price == pytest.approx(0.46)
    assert pos.exit_price == pytest.approx(0.46)
    assert pos.last_exit_at == fill_event["timestamp"]
    assert fill_event["details"]["fill_price"] == pytest.approx(pos.exit_price)
    assert attempt_event["timestamp"] == pos.entered_at
    assert fill_event["timestamp"] != attempt_event["timestamp"]

    conn.close()


def test_materialize_position_carries_semantic_snapshot_jsons():
    candidate = type("Candidate", (), {"target_date": "2026-04-01", "hours_since_open": 2.0})()
    edge = _edge()
    edge.direction = "buy_yes"
    decision = type("Decision", (), {
        "edge": edge,
        "size_usd": 10.0,
        "tokens": {"market_id": "m1", "token_id": "yes123", "no_token_id": "no456"},
        "decision_snapshot_id": "snap-1",
        "strategy_key": "center_buy",
        "selected_method": "ens_member_counting",
        "applied_validations": ["ens_fetch"],
        "edge_source": "center_buy",
        "settlement_semantics_json": '{"measurement_unit":"F"}',
        "epistemic_context_json": '{"decision_time_utc":"2026-04-01T00:00:00Z"}',
        "edge_context_json": '{"forward_edge":0.12}',
    })()
    result = type("Result", (), {
        "trade_id": "t123",
        "fill_price": 0.4,
        "submitted_price": 0.4,
        "shares": 25.0,
        "timeout_seconds": None,
        "status": "filled",
        "order_id": "",
    })()
    portfolio = PortfolioState(bankroll=100.0)

    pos = cycle_runner._materialize_position(
        candidate, decision, result, portfolio, NYC, DiscoveryMode.OPENING_HUNT, state="entered"
    )

    assert pos.settlement_semantics_json == '{"measurement_unit":"F"}'
    assert pos.epistemic_context_json == '{"decision_time_utc":"2026-04-01T00:00:00Z"}'
    assert pos.edge_context_json == '{"forward_edge":0.12}'


def test_exit_intent_scaffolding_vocabulary_is_explicit():
    assert exit_lifecycle_module.EXIT_EVENT_VOCABULARY == (
        "EXIT_INTENT",
        "EXIT_ORDER_POSTED",
        "EXIT_ORDER_FILLED",
        "EXIT_ORDER_VOIDED",
        "EXIT_ORDER_REJECTED",
    )


def test_build_exit_intent_carries_boundary_fields():
    pos = _position()
    ctx = ExitContext(
        fresh_prob=0.41,
        fresh_prob_is_fresh=True,
        current_market_price=0.46,
        current_market_price_is_fresh=True,
        best_bid=0.45,
        best_ask=0.49,
        market_vig=None,
        hours_to_settlement=2.0,
        position_state="day0_window",
        day0_active=True,
        exit_reason="forward edge failed",
    )

    intent = exit_lifecycle_module.build_exit_intent(pos, ctx, paper_mode=True)

    assert intent.trade_id == pos.trade_id
    assert intent.reason == "forward edge failed"
    assert intent.token_id == pos.token_id
    assert intent.shares == pytest.approx(pos.effective_shares)
    assert intent.current_market_price == pytest.approx(0.46)
    assert intent.best_bid == pytest.approx(0.45)
    assert intent.paper_mode is True


def test_execute_exit_routes_live_sell_through_executor_exit_path(monkeypatch):
    pos = _position(state="day0_window")
    portfolio = PortfolioState(positions=[pos])
    ctx = ExitContext(
        fresh_prob=0.41,
        fresh_prob_is_fresh=True,
        current_market_price=0.46,
        current_market_price_is_fresh=True,
        best_bid=0.45,
        best_ask=0.49,
        market_vig=None,
        hours_to_settlement=2.0,
        position_state="day0_window",
        day0_active=True,
        exit_reason="forward edge failed",
    )
    calls = {}

    class LiveClob:
        def get_balance(self):
            return 100.0

        def get_order_status(self, order_id):
            calls["checked_order_id"] = order_id
            return {"status": "OPEN"}

    def _execute_exit_order(intent):
        calls["intent"] = intent
        return OrderResult(
            trade_id=intent.trade_id,
            status="pending",
            order_id="sell-order-1",
            external_order_id="sell-order-1",
            submitted_price=0.44,
            shares=intent.shares,
            order_role="exit",
            venue_status="OPEN",
        )

    monkeypatch.setattr("src.execution.exit_lifecycle.execute_exit_order", _execute_exit_order)

    outcome = exit_lifecycle_module.execute_exit(
        portfolio=portfolio,
        position=pos,
        exit_context=ctx,
        paper_mode=False,
        clob=LiveClob(),
    )

    assert outcome == "sell_pending: order=sell-order-1, status=OPEN"
    assert calls["intent"].trade_id == pos.trade_id
    assert calls["intent"].token_id == pos.token_id
    assert calls["intent"].shares == pytest.approx(pos.effective_shares)
    assert calls["intent"].current_price == pytest.approx(0.46)
    assert pos.exit_state == "sell_pending"


def test_execute_exit_rejected_orderresult_preserves_retry_semantics(monkeypatch):
    pos = _position(state="day0_window")
    portfolio = PortfolioState(positions=[pos])
    ctx = ExitContext(
        fresh_prob=0.41,
        fresh_prob_is_fresh=True,
        current_market_price=0.46,
        current_market_price_is_fresh=True,
        best_bid=0.45,
        best_ask=0.49,
        market_vig=None,
        hours_to_settlement=2.0,
        position_state="day0_window",
        day0_active=True,
        exit_reason="forward edge failed",
    )

    class LiveClob:
        def get_balance(self):
            return 100.0

    monkeypatch.setattr(
        "src.execution.exit_lifecycle.execute_exit_order",
        lambda intent: OrderResult(
            trade_id=intent.trade_id,
            status="rejected",
            reason="sell_api_down",
            order_role="exit",
        ),
    )

    outcome = exit_lifecycle_module.execute_exit(
        portfolio=portfolio,
        position=pos,
        exit_context=ctx,
        paper_mode=False,
        clob=LiveClob(),
    )

    assert outcome == "sell_error: sell_api_down"
    assert pos in portfolio.positions
    assert pos.exit_state == "retry_pending"
    assert pos.last_exit_error == "sell_api_down"


def test_execute_exit_accepts_prebuilt_exit_intent_in_paper_mode():
    pos = _position(state="day0_window")
    portfolio = PortfolioState(positions=[pos])
    ctx = ExitContext(
        fresh_prob=0.41,
        fresh_prob_is_fresh=True,
        current_market_price=0.46,
        current_market_price_is_fresh=True,
        best_bid=0.45,
        best_ask=0.49,
        market_vig=None,
        hours_to_settlement=2.0,
        position_state="day0_window",
        day0_active=True,
        exit_reason="forward edge failed",
    )
    intent = exit_lifecycle_module.build_exit_intent(pos, ctx, paper_mode=True)

    outcome = exit_lifecycle_module.execute_exit(
        portfolio=portfolio,
        position=pos,
        exit_context=ctx,
        paper_mode=True,
        exit_intent=intent,
    )

    assert outcome == "paper_exit: forward edge failed"
    assert pos.exit_state == "sell_filled"


def test_discovery_phase_records_observation_unavailable_as_no_trade(monkeypatch, tmp_path):
    conn = get_connection(tmp_path / "zeus.db")
    init_schema(conn)

    artifact = CycleArtifact(mode="day0_capture", started_at="2026-04-03T00:00:00Z")
    tracker = StrategyTracker()
    portfolio = PortfolioState()
    summary = {"candidates": 0, "no_trades": 0}

    market = {
        "city": NYC,
        "target_date": "2026-04-01",
        "outcomes": [],
        "hours_since_open": 1.0,
        "hours_to_resolution": 4.0,
        "event_id": "evt1",
        "slug": "slug1",
    }

    deps = types.SimpleNamespace(
        MODE_PARAMS={DiscoveryMode.DAY0_CAPTURE: {}},
        DiscoveryMode=DiscoveryMode,
        logger=types.SimpleNamespace(warning=lambda *args, **kwargs: None),
        NoTradeCase=NoTradeCase,
        find_weather_markets=lambda **kwargs: [market],
        get_current_observation=lambda *args, **kwargs: (_ for _ in ()).throw(ObservationUnavailableError("obs down")),
        evaluate_candidate=lambda *args, **kwargs: [],
    )

    cycle_runtime.execute_discovery_phase(
        conn,
        clob=None,
        portfolio=portfolio,
        artifact=artifact,
        tracker=tracker,
        limits=types.SimpleNamespace(),
        mode=DiscoveryMode.DAY0_CAPTURE,
        summary=summary,
        entry_bankroll=100.0,
        decision_time=datetime(2026, 4, 3, 6, 0, tzinfo=timezone.utc),
        deps=deps,
    )

    assert summary["no_trades"] == 1
    case = artifact.no_trade_cases[0]
    assert case.availability_status == "DATA_UNAVAILABLE"
    assert case.rejection_stage == "SIGNAL_QUALITY"
    conn.close()


def test_learning_summary_separates_no_data_from_no_edge(tmp_path):
    conn = get_connection(tmp_path / "zeus.db")
    init_schema(conn)

    artifact = CycleArtifact(mode="paper", started_at="2026-04-03T00:00:00Z", completed_at="2026-04-03T00:05:00Z")
    artifact.add_no_trade(
        NoTradeCase(
            decision_id="d1",
            city="NYC",
            target_date="2026-04-01",
            range_label="",
            direction="unknown",
            rejection_stage="SIGNAL_QUALITY",
            availability_status="DATA_UNAVAILABLE",
            rejection_reasons=["obs down"],
            timestamp="2026-04-03T00:00:00Z",
        )
    )
    artifact.add_no_trade(
        NoTradeCase(
            decision_id="d2",
            city="NYC",
            target_date="2026-04-01",
            range_label="39-40°F",
            direction="buy_yes",
            rejection_stage="EDGE_INSUFFICIENT",
            strategy_key="center_buy",
            strategy="center_buy",
            edge_source="center_buy",
            rejection_reasons=["small edge"],
            timestamp="2026-04-03T00:00:00Z",
        )
    )
    store_artifact(conn, artifact, env="paper")

    summary = query_learning_surface_summary(conn, env="paper")
    conn.close()

    assert summary["availability_status_counts"]["DATA_UNAVAILABLE"] == 1
    assert summary["no_trade_stage_counts"]["EDGE_INSUFFICIENT"] == 1


def test_availability_status_helper_maps_rate_limited_and_chain():
    assert cycle_runtime._availability_status_for_exception(RuntimeError("429 capacity exhausted")) == "RATE_LIMITED"
    assert cycle_runtime._availability_status_for_exception(RuntimeError("chain rpc unavailable")) == "CHAIN_UNAVAILABLE"


def test_evaluator_ens_fetch_exception_becomes_explicit_availability_truth(monkeypatch):
    candidate = MarketCandidate(
        city=NYC,
        target_date="2026-04-01",
        outcomes=[
            {"title": "38°F or below", "range_low": None, "range_high": 38, "token_id": "yes1", "no_token_id": "no1", "market_id": "m1", "price": 0.20},
            {"title": "39-40°F", "range_low": 39, "range_high": 40, "token_id": "yes2", "no_token_id": "no2", "market_id": "m2", "price": 0.20},
            {"title": "41°F or above", "range_low": 41, "range_high": None, "token_id": "yes3", "no_token_id": "no3", "market_id": "m3", "price": 0.20},
        ],
        hours_since_open=12.0,
        hours_to_resolution=24.0,
        discovery_mode=DiscoveryMode.UPDATE_REACTION.value,
    )

    monkeypatch.setattr(
        evaluator_module,
        "fetch_ensemble",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("429 capacity exhausted")),
    )

    decisions = evaluator_module.evaluate_candidate(
        candidate,
        conn=None,
        portfolio=PortfolioState(bankroll=150.0),
        clob=types.SimpleNamespace(),
        limits=evaluator_module.RiskLimits(),
    )

    assert len(decisions) == 1
    assert decisions[0].should_trade is False
    assert decisions[0].availability_status == "RATE_LIMITED"
    assert decisions[0].rejection_stage == "SIGNAL_QUALITY"


def test_execute_exit_rejects_mismatched_exit_intent():
    pos = _position(state="day0_window")
    portfolio = PortfolioState(positions=[pos])
    ctx = ExitContext(
        fresh_prob=0.41,
        fresh_prob_is_fresh=True,
        current_market_price=0.46,
        current_market_price_is_fresh=True,
        best_bid=0.45,
        best_ask=0.49,
        market_vig=None,
        hours_to_settlement=2.0,
        position_state="day0_window",
        day0_active=True,
        exit_reason="forward edge failed",
    )
    intent = exit_lifecycle_module.ExitIntent(
        trade_id="other-trade",
        reason="forward edge failed",
        token_id=pos.token_id,
        shares=pos.effective_shares,
        current_market_price=0.46,
        best_bid=0.45,
        paper_mode=True,
    )

    with pytest.raises(ValueError, match="trade_id mismatch"):
        exit_lifecycle_module.execute_exit(
            portfolio=portfolio,
            position=pos,
            exit_context=ctx,
            paper_mode=True,
            exit_intent=intent,
        )


def test_check_pending_exits_does_not_retry_bare_exit_intent_without_error():
    pos = _position()
    pos.exit_state = "exit_intent"
    pos.last_exit_error = ""
    portfolio = PortfolioState(positions=[pos])

    stats = exit_lifecycle_module.check_pending_exits(portfolio, clob=None, conn=None)

    assert stats["retried"] == 0
    assert stats["unchanged"] == 1
    assert pos.exit_state == "exit_intent"


def test_check_pending_exits_emits_void_semantics_for_rejected_sell(monkeypatch, tmp_path):
    conn = get_connection(tmp_path / "zeus.db")
    init_schema(conn)

    pos = _position(state="day0_window")
    pos.exit_state = "sell_pending"
    pos.last_exit_order_id = "sell-order-1"
    pos.exit_reason = "forward edge failed"
    pos.last_monitor_market_price = 0.46
    portfolio = PortfolioState(positions=[pos])

    class LiveClob:
        def get_order_status(self, order_id):
            assert order_id == "sell-order-1"
            return {"status": "REJECTED"}

    stats = exit_lifecycle_module.check_pending_exits(portfolio, clob=LiveClob(), conn=conn)
    events = query_position_events(conn, "t1")
    conn.close()

    assert stats["retried"] == 1
    assert any(event["event_type"] == "EXIT_ORDER_VOIDED" for event in events)
