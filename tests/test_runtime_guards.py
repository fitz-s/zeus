"""Runtime guard and live-cycle wiring tests."""

from __future__ import annotations

import json
import sys
import types
from datetime import date, datetime, timezone

import numpy as np
import pytest

import src.data.ensemble_client as ensemble_client
import src.engine.cycle_runner as cycle_runner
import src.engine.evaluator as evaluator_module
from src.config import City
from src.data.ecmwf_open_data import DATA_VERSION, collect_open_ens_cycle
from src.data.openmeteo_quota import DAILY_LIMIT, HARD_THRESHOLD, OpenMeteoQuotaTracker
from src.engine.discovery_mode import DiscoveryMode
from src.engine.time_context import lead_days_to_target
from src.engine.evaluator import EdgeDecision, MarketCandidate
from src.execution.executor import OrderResult
from src.riskguard.risk_level import RiskLevel
from src.state.db import get_connection, init_schema
from src.state.chain_reconciliation import ChainPosition, reconcile
from src.state.portfolio import PortfolioState, Position, load_portfolio, save_portfolio
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


def test_unknown_direction_positions_are_not_monitored(monkeypatch, tmp_path):
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
    assert cycle_runner._classify_strategy(DiscoveryMode.DAY0_CAPTURE, center_edge, "") == "settlement_capture"


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
        lambda city, forecast_days=2, model=None: None if model == "gfs025" else {
            "members_hourly": np.ones((51, 24)) * 40.0,
            "times": [
                datetime(2026, 3, 30, 0, 0, tzinfo=timezone.utc).isoformat()
                for _ in range(24)
            ],
            "issue_time": datetime(2026, 3, 30, 0, 0, tzinfo=timezone.utc),
            "fetch_time": datetime(2026, 3, 30, 23, 30, tzinfo=timezone.utc),
            "model": "ecmwf_ifs025",
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
        lambda city, forecast_days=2, model=None: {
            "members_hourly": np.ones((51, 12)) * 44.0,
            "times": [datetime.now(timezone.utc).replace(microsecond=0).isoformat() for _ in range(12)],
            "issue_time": datetime.now(timezone.utc),
            "fetch_time": datetime.now(timezone.utc),
            "model": "ecmwf_ifs025",
        },
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


def test_materialize_position_carries_semantic_snapshot_jsons():
    candidate = type("Candidate", (), {"target_date": "2026-04-01", "hours_since_open": 2.0})()
    edge = _edge()
    edge.direction = "buy_yes"
    decision = type("Decision", (), {
        "edge": edge,
        "size_usd": 10.0,
        "tokens": {"market_id": "m1", "token_id": "yes123", "no_token_id": "no456"},
        "decision_snapshot_id": "snap-1",
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
