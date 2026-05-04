"""Runtime guard and live-cycle wiring tests."""
# Lifecycle: created=2026-04-28; last_reviewed=2026-05-02; last_reused=2026-05-02
# Created: 2026-04-28
# Last reused/audited: 2026-05-02
# Authority basis: task_2026-04-28_contamination_remediation Batch G; Phase 1B ENS snapshot persistence; Phase 1D forecast source policy.
# Purpose: Lock runtime guard and live-cycle wiring contracts.
# Reuse: Run for runtime guard, live-only cleanup, and cycle wiring changes.

from dataclasses import dataclass
from decimal import Decimal
from zoneinfo import ZoneInfo
from pathlib import Path
from datetime import date, datetime, timedelta, timezone
import types
import json
import logging
import sqlite3
import sys
import tempfile

import numpy as np
import pytest

import src.data.ensemble_client as ensemble_client
import src.engine.cycle_runner as cycle_runner
import src.engine.cycle_runtime as cycle_runtime
import src.engine.evaluator as evaluator_module
import src.execution.exit_lifecycle as exit_lifecycle_module
from src.backtest.economics import check_economics_readiness
from src.data.observation_client import Day0ObservationContext
from src.config import City, settings
from src.control import control_plane as control_plane_module
from src.data.ecmwf_open_data import DATA_VERSION, collect_open_ens_cycle
from src.data.openmeteo_quota import DAILY_LIMIT, HARD_THRESHOLD, OpenMeteoQuotaTracker
from src.contracts import EdgeContext, EntryMethod, SettlementSemantics
from src.engine.discovery_mode import DiscoveryMode
from src.engine.time_context import lead_days_to_date_start
from src.engine.evaluator import EdgeDecision, MarketCandidate
from src.execution.executor import OrderResult, create_execution_intent
from src.riskguard.risk_level import RiskLevel
from src.contracts.exceptions import ObservationUnavailableError
import src.state.db as db_module
from src.state.db import get_connection, init_schema, query_position_events
from src.state.schema.v2_schema import apply_v2_schema
from src.state.decision_chain import CycleArtifact, NoTradeCase, query_learning_surface_summary, store_artifact
from src.state.chain_reconciliation import ChainPosition, reconcile
from src.state.portfolio import (
    DeprecatedStateFileError,
    ENTRY_ECONOMICS_AVG_FILL_PRICE,
    ENTRY_ECONOMICS_SUBMITTED_LIMIT,
    ExitContext,
    ExitDecision,
    FILL_AUTHORITY_NONE,
    FILL_AUTHORITY_VENUE_CONFIRMED_FULL,
    FILL_AUTHORITY_VENUE_CONFIRMED_PARTIAL,
    PortfolioState,
    Position,
    has_same_city_range_open,
    load_portfolio,
    save_portfolio,
    total_exposure_usd,
)
from src.state.strategy_tracker import StrategyTracker
from src.types import Bin, BinEdge, Day0TemporalContext
from src.strategy.market_analysis_family_scan import FullFamilyHypothesis
from src.types.temperature import TemperatureDelta
from src.types.metric_identity import HIGH_LOCALDAY_MAX


def test_evaluator_fee_rate_uses_canonical_fraction_from_clob_details():
    class FakeClob:
        def get_fee_rate_details(self, token_id):
            assert token_id == "yes-token"
            return {"base_fee": "30", "source": "clob_fee_rate"}

    assert evaluator_module._fee_rate_for_token(FakeClob(), "yes-token") == pytest.approx(0.003)


def test_evaluator_fee_rate_canonicalizes_legacy_bps_values():
    class FakeClob:
        def get_fee_rate(self, token_id):
            assert token_id == "yes-token"
            return 30

    assert evaluator_module._fee_rate_for_token(FakeClob(), "yes-token") == pytest.approx(0.003)


@pytest.fixture(autouse=True)
def _default_posture_normal_for_runtime_guards(monkeypatch):
    """INV-26 / O2-c isolation: tests in this file pre-date the runtime
    posture gate and assume new entries reach discovery. Default posture to
    NORMAL so the legacy fixtures keep exercising the gates they were
    written for. Tests that explicitly verify posture must override.
    """
    import src.runtime.posture as _posture_module
    _posture_module._clear_cache()
    monkeypatch.setattr(_posture_module, "read_runtime_posture", lambda: "NORMAL")


def _allow_entry_gates_for_runtime_test(monkeypatch) -> None:
    """Open only the outer runtime entry gates for tests that must reach discovery.

    This helper is intentionally targeted (not autouse): runtime_guards also
    contains tests that verify entry blocking behavior.
    """
    monkeypatch.setattr(cycle_runner, "get_force_exit_review", lambda: False)
    monkeypatch.setattr(cycle_runner.cutover_guard, "summary", lambda: {"state": "READY", "entry": {"allow_submit": True}})
    monkeypatch.setattr(
        "src.control.heartbeat_supervisor.summary",
        lambda: {"health": "OK", "entry": {"allow_submit": True}},
    )
    monkeypatch.setattr(
        "src.control.ws_gap_guard.summary",
        lambda: {
            "subscription_state": "CONNECTED",
            "gap_reason": "",
            "m5_reconcile_required": False,
            "entry": {"allow_submit": True},
        },
    )
    monkeypatch.setattr(
        "src.risk_allocator.refresh_global_allocator",
        lambda *args, **kwargs: {"entry": {"allow_submit": True}},
    )
    monkeypatch.setattr("src.runtime.posture.read_runtime_posture", lambda: "NORMAL")


def _set_native_multibin_buy_no_flags(monkeypatch, *, shadow: bool, live: bool = False) -> None:
    flags = dict(settings["feature_flags"])
    flags[evaluator_module.NATIVE_MULTIBIN_BUY_NO_SHADOW_FLAG] = shadow
    flags[evaluator_module.NATIVE_MULTIBIN_BUY_NO_LIVE_FLAG] = live
    monkeypatch.setitem(settings._data, "feature_flags", flags)


def _run_live_buy_no_authorization_case(
    monkeypatch,
    tmp_path,
    *,
    mode: DiscoveryMode,
    strategy_key: str,
    applied_validations: list[str],
):
    from dataclasses import replace

    monkeypatch.setattr(control_plane_module, "_control_state", {})
    conn = get_connection(tmp_path / f"live-buy-no-{strategy_key}-{mode.value}.db")
    init_schema(conn)
    artifact = CycleArtifact(mode=mode.value, started_at="2026-04-03T00:00:00Z")
    summary = {"candidates": 0, "no_trades": 0}
    market = {
        "city": NYC,
        "target_date": "2026-04-01",
        "hours_since_open": 30.0,
        "hours_to_resolution": 2.0 if mode is DiscoveryMode.DAY0_CAPTURE else 24.0,
        "event_id": f"evt-live-buy-no-{strategy_key}",
        "slug": f"slug-live-buy-no-{strategy_key}",
        "temperature_metric": "high",
        "outcomes": [
            {"title": "39°F or lower", "range_low": None, "range_high": 39, "token_id": "yes0", "no_token_id": "no0", "market_id": "m0"},
            {"title": "40°F or higher", "range_low": 40, "range_high": None, "token_id": "yes1", "no_token_id": "no1", "market_id": "m1"},
        ],
    }
    buy_no_edge = replace(
        _edge(),
        bin=Bin(low=None, high=39, label="39°F or lower", unit="F"),
        direction="buy_no",
        p_market=0.35,
        entry_price=0.35,
        vwmp=0.35,
        p_posterior=0.62,
        edge=0.27,
        forward_edge=0.27,
    )
    decision = EdgeDecision(
        should_trade=True,
        edge=buy_no_edge,
        tokens={"market_id": "m0", "token_id": "yes0", "no_token_id": "no0"},
        size_usd=5.0,
        decision_id=f"d-live-buy-no-{strategy_key}",
        selected_method="ens_member_counting",
        applied_validations=applied_validations,
        decision_snapshot_id=f"model-snap-live-buy-no-{strategy_key}",
        edge_source=strategy_key,
        strategy_key=strategy_key,
        settlement_semantics_json='{"measurement_unit":"F"}',
        epistemic_context_json='{"decision_time_utc":"2026-04-01T00:00:00Z"}',
        edge_context_json='{"forward_edge":0.27}',
        sizing_bankroll=100.0,
        kelly_multiplier_used=0.25,
        execution_fee_rate=0.0,
        safety_cap_usd=None,
    )

    deps = types.SimpleNamespace(
        MODE_PARAMS={mode: {}},
        find_weather_markets=lambda **kwargs: [market],
        get_last_scan_authority=lambda: "VERIFIED",
        DiscoveryMode=DiscoveryMode,
        logger=types.SimpleNamespace(warning=lambda *args, **kwargs: None, error=lambda *args, **kwargs: None),
        NoTradeCase=NoTradeCase,
        MarketCandidate=MarketCandidate,
        get_current_observation=lambda *args, **kwargs: Day0ObservationContext(
            high_so_far=70.0,
            low_so_far=62.0,
            current_temp=69.0,
            source="wu_api",
            observation_time="2026-04-03T00:00:00+00:00",
            unit="F",
        ),
        evaluate_candidate=lambda *args, **kwargs: [decision],
        is_strategy_enabled=control_plane_module.is_strategy_enabled,
        _classify_edge_source=lambda _mode, _edge: strategy_key,
        create_execution_intent=lambda **kwargs: (_ for _ in ()).throw(AssertionError("should not submit live buy_no")),
        execute_intent=lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("should not execute live buy_no")),
    )

    cycle_runtime.execute_discovery_phase(
        conn,
        clob=None,
        portfolio=PortfolioState(),
        artifact=artifact,
        tracker=StrategyTracker(),
        limits=types.SimpleNamespace(),
        mode=mode,
        summary=summary,
        entry_bankroll=100.0,
        decision_time=datetime(2026, 4, 3, tzinfo=timezone.utc),
        env="live",
        deps=deps,
    )
    conn.close()
    return summary, artifact


def _patch_mature_calibration(monkeypatch, *, level: int = 1) -> None:
    from src.contracts.alpha_decision import AlphaDecision

    class _Calibrator:
        pass

    monkeypatch.setattr(evaluator_module, "get_calibrator", lambda *args, **kwargs: (_Calibrator(), level))
    monkeypatch.setattr(
        evaluator_module,
        "calibrate_and_normalize",
        lambda p_raw, *args, **kwargs: np.array(p_raw, dtype=float).copy(),
    )
    monkeypatch.setattr(
        evaluator_module,
        "compute_alpha",
        lambda *args, **kwargs: AlphaDecision(
            value=0.5,
            optimization_target="risk_cap",
            evidence_basis="runtime guard mature calibration fixture",
            ci_bound=0.05,
        ),
    )


def _entry_forecast_evidence(
    *,
    model: str = "ecmwf_ifs025",
    source_id: str = "tigge",
    role: str = "entry_primary",
    issue_time: datetime | None = None,
    first_valid_time: datetime | None = None,
    fetch_time: datetime | None = None,
    available_at: datetime | None = None,
    n_members: int = 51,
) -> dict[str, object]:
    now = fetch_time or datetime(2026, 4, 1, 6, 0, tzinfo=timezone.utc)
    return {
        "issue_time": issue_time or now,
        "first_valid_time": first_valid_time or now,
        "fetch_time": now,
        "available_at": available_at or now,
        "model": model,
        "source_id": source_id,
        "raw_payload_hash": "a" * 64,
        "authority_tier": "FORECAST",
        "degradation_level": "OK",
        "forecast_source_role": role,
        "n_members": n_members,
    }


NYC = City(
    name="NYC",
    lat=40.7772,
    lon=-73.8726,
    timezone="America/New_York",
    cluster="NYC",
    settlement_unit="F",
    wu_station="KLGA",
)


class _CycleSettingsStub:
    # 2026-05-04 bankroll truth-chain cleanup: capital_base_usd and
    # smoke_test_portfolio_cap_usd are no longer read by production code.
    # The stub keeps both kwargs as harmless slots so existing call sites
    # (e.g. _CycleSettingsStub(capital_base_usd=150.0,
    # smoke_test_portfolio_cap_usd=None)) don't need editing; both values
    # are stored but ignored. __getitem__ raises KeyError for any key.
    def __init__(self, *, capital_base_usd: float = 150.0, smoke_test_portfolio_cap_usd=None):
        self.capital_base_usd = capital_base_usd
        self._smoke_test_portfolio_cap_usd = smoke_test_portfolio_cap_usd

    def __getitem__(self, key: str):
        raise KeyError(key)


def _position(**kwargs) -> Position:
    defaults = dict(
        trade_id="t1",
        market_id="m1",
        city="NYC",
        cluster="NYC",
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


def _buy_no_exit_position_for_quote_split() -> Position:
    pos = _position(
        trade_id="buy-no-exit-quote-split",
        target_date="2026-04-01",
        bin_label="39-40°F",
        direction="buy_no",
        size_usd=10.0,
        entry_price=0.50,
        p_posterior=0.70,
        entry_ci_width=0.02,
        token_id="yes-held",
        no_token_id="no-held",
    )
    pos.neg_edge_count = 1
    return pos


def _buy_no_exit_context_for_quote_split(*, p_market_quote: float) -> EdgeContext:
    return EdgeContext(
        p_raw=np.array([0.60, 0.40]),
        p_cal=np.array([0.60, 0.40]),
        p_market=np.array([p_market_quote]),
        p_posterior=0.60,
        forward_edge=-0.10,
        alpha=0.0,
        confidence_band_upper=-0.08,
        confidence_band_lower=-0.12,
        entry_provenance=EntryMethod.ENS_MEMBER_COUNTING,
        decision_snapshot_id="buy-no-exit-quote-split-snap",
        n_edges_found=1,
        n_edges_after_fdr=1,
        market_velocity_1h=0.0,
        divergence_score=0.0,
    )


def test_buy_no_exit_ev_gate_uses_held_token_best_bid_not_p_market_vector():
    from src.execution.exit_triggers import evaluate_exit_triggers

    pos = _buy_no_exit_position_for_quote_split()
    ctx = _buy_no_exit_context_for_quote_split(p_market_quote=0.95)

    signal = evaluate_exit_triggers(pos, ctx, best_bid=0.20)

    assert signal is None


def test_buy_no_exit_ev_gate_allows_sell_when_best_bid_beats_hold_value():
    from src.execution.exit_triggers import evaluate_exit_triggers

    pos = _buy_no_exit_position_for_quote_split()
    ctx = _buy_no_exit_context_for_quote_split(p_market_quote=0.05)

    signal = evaluate_exit_triggers(pos, ctx, best_bid=0.70)

    assert signal is not None
    assert signal.trigger == "BUY_NO_EDGE_EXIT"


class _MonitorQuoteSplitClob:
    def __init__(self, *, bid: float, ask: float, bid_size: float, ask_size: float):
        self.bid = bid
        self.ask = ask
        self.bid_size = bid_size
        self.ask_size = ask_size

    def get_best_bid_ask(self, token_id):
        assert token_id == "yes123"
        return self.bid, self.ask, self.bid_size, self.ask_size


def test_monitor_quote_refresh_changes_exit_price_not_posterior_dispatch(monkeypatch, tmp_path):
    from src.engine import monitor_refresh

    conn = get_connection(tmp_path / "monitor-quote-split.db")
    init_schema(conn)
    monkeypatch.setattr("src.state.db.log_microstructure", lambda *args, **kwargs: None)
    monkeypatch.setattr(monitor_refresh, "_detect_whale_toxicity_from_orderbook", lambda *args, **kwargs: False)

    dispatched_market_inputs: list[float] = []

    def _recompute(position, current_p_market, registry, **context):
        dispatched_market_inputs.append(float(current_p_market))
        return 0.63

    monkeypatch.setattr(monitor_refresh, "recompute_native_probability", _recompute)

    tight_quote_pos = _position(entry_price=0.44, p_posterior=0.58)
    wide_quote_pos = _position(entry_price=0.44, p_posterior=0.58)

    tight_ctx = monitor_refresh.refresh_position(
        conn,
        _MonitorQuoteSplitClob(bid=0.40, ask=0.50, bid_size=100.0, ask_size=100.0),
        tight_quote_pos,
    )
    wide_ctx = monitor_refresh.refresh_position(
        conn,
        _MonitorQuoteSplitClob(bid=0.20, ask=0.80, bid_size=10.0, ask_size=90.0),
        wide_quote_pos,
    )

    assert dispatched_market_inputs == pytest.approx([0.44, 0.44])
    assert tight_ctx.p_posterior == pytest.approx(wide_ctx.p_posterior)
    assert tight_ctx.p_posterior == pytest.approx(0.63)
    assert tight_ctx.p_market[0] != pytest.approx(wide_ctx.p_market[0])
    assert tight_quote_pos.last_monitor_best_bid == pytest.approx(0.40)
    assert wide_quote_pos.last_monitor_best_bid == pytest.approx(0.20)


def test_monitor_quote_refresh_survives_microstructure_log_failure(monkeypatch):
    from src.engine import monitor_refresh

    def _raise_log_failure(*args, **kwargs):
        raise RuntimeError("microstructure log unavailable")

    monkeypatch.setattr("src.state.db.log_microstructure", _raise_log_failure)
    monkeypatch.setattr(monitor_refresh, "_detect_whale_toxicity_from_orderbook", lambda *args, **kwargs: False)
    monkeypatch.setattr(
        monitor_refresh,
        "recompute_native_probability",
        lambda position, current_p_market, registry, **context: 0.63,
    )

    pos = _position(entry_price=0.44, p_posterior=0.58)
    edge_ctx = monitor_refresh.refresh_position(
        None,
        _MonitorQuoteSplitClob(bid=0.40, ask=0.50, bid_size=100.0, ask_size=100.0),
        pos,
    )

    assert pos.last_monitor_best_bid == pytest.approx(0.40)
    assert pos.last_monitor_best_ask == pytest.approx(0.50)
    assert pos.last_monitor_market_price == pytest.approx(0.45)
    assert edge_ctx.p_market[0] == pytest.approx(0.45)
    assert edge_ctx.p_posterior == pytest.approx(0.63)


def test_refresh_position_support_topology_stale_blocks_exit_probability(monkeypatch):
    from src.engine import monitor_refresh

    monkeypatch.setattr("src.state.db.log_microstructure", lambda *args, **kwargs: None)
    monkeypatch.setattr(monitor_refresh, "_detect_whale_toxicity_from_orderbook", lambda *args, **kwargs: False)

    def _stale_refresh(pos, *, conn, city, target_d):
        pos.applied_validations = ["day0_observation", "fresh_ens_fetch", "support_topology_stale"]
        return pos.p_posterior, pos, False

    monkeypatch.setattr(monitor_refresh, "monitor_probability_refresh", _stale_refresh)

    pos = _position(
        state="day0_window",
        entry_method="ens_member_counting",
        selected_method="ens_member_counting",
        entry_price=0.44,
        p_posterior=0.58,
        last_monitor_prob=0.41,
        edge=0.14,
        entry_ci_width=0.02,
    )

    edge_ctx = monitor_refresh.refresh_position(
        None,
        _MonitorQuoteSplitClob(bid=0.40, ask=0.50, bid_size=100.0, ask_size=100.0),
        pos,
    )

    assert pos.last_monitor_prob == pytest.approx(0.41)
    assert pos.last_monitor_prob_is_fresh is False
    assert "support_topology_stale" in pos.applied_validations
    assert not np.isfinite(edge_ctx.p_posterior)
    assert not np.isfinite(edge_ctx.forward_edge)
    assert not np.isfinite(edge_ctx.confidence_band_lower)


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
        support_index=0,
    )


def _insert_executable_snapshot(
    conn,
    *,
    snapshot_id: str,
    selected_outcome_token_id: str = "yes1",
    outcome_label: str = "YES",
    yes_token_id: str = "yes1",
    no_token_id: str = "no1",
    event_id: str = "evt-1",
    condition_id: str = "cond1",
    top_bid: str = "0.34",
    top_ask: str = "0.36",
    bid_size: str = "100",
    ask_size: str = "100",
    orderbook_depth: dict | None = None,
    captured_at: datetime | None = None,
) -> None:
    from src.contracts.executable_market_snapshot_v2 import ExecutableMarketSnapshotV2
    from src.state.snapshot_repo import insert_snapshot

    captured_at = captured_at or datetime.now(timezone.utc)
    insert_snapshot(
        conn,
        ExecutableMarketSnapshotV2(
            snapshot_id=snapshot_id,
            gamma_market_id="gamma-1",
            event_id=event_id,
            event_slug="slug-1",
            condition_id=condition_id,
            question_id="question-1",
            yes_token_id=yes_token_id,
            no_token_id=no_token_id,
            selected_outcome_token_id=selected_outcome_token_id,
            outcome_label=outcome_label,
            enable_orderbook=True,
            active=True,
            closed=False,
            accepting_orders=True,
            market_start_at=None,
            market_end_at=None,
            market_close_at=None,
            sports_start_at=None,
            min_tick_size=Decimal("0.01"),
            min_order_size=Decimal("5"),
            fee_details={"source": "test", "fee_rate_bps": 0},
            token_map_raw={"YES": yes_token_id, "NO": no_token_id},
            rfqe=None,
            neg_risk=False,
            orderbook_top_bid=Decimal(top_bid),
            orderbook_top_ask=Decimal(top_ask),
            orderbook_depth_jsonb=json.dumps(
                orderbook_depth
                if orderbook_depth is not None
                else {
                    "bids": [{"price": top_bid, "size": bid_size}],
                    "asks": [{"price": top_ask, "size": ask_size}],
                }
            ),
            raw_gamma_payload_hash="a" * 64,
            raw_clob_market_info_hash="b" * 64,
            raw_orderbook_hash="c" * 64,
            authority_tier="CLOB",
            captured_at=captured_at,
            freshness_deadline=captured_at + timedelta(seconds=30),
        ),
    )


def _stub_full_family_scan(monkeypatch) -> None:
    def _scan(analysis, *args, **kwargs):
        selected_method = getattr(analysis, "selected_method", "test_fixture")
        hypotheses = []
        for i, edge in enumerate(analysis.find_edges(n_bootstrap=kwargs.get("n_bootstrap", 0))):
            edge.selected_method = getattr(edge, "selected_method", selected_method)
            assert edge.selected_method
            hypotheses.append(
                FullFamilyHypothesis(
                    index=i,
                    range_label=edge.bin.label,
                    direction=edge.direction,
                    edge=edge.edge,
                    ci_lower=edge.ci_lower,
                    ci_upper=edge.ci_upper,
                    p_value=edge.p_value,
                    p_model=edge.p_model,
                    p_market=edge.p_market,
                    p_posterior=edge.p_posterior,
                    entry_price=edge.entry_price,
                    is_shoulder=bool(getattr(edge.bin, "is_shoulder", False)),
                    passed_prefilter=True,
                )
            )
        return hypotheses

    monkeypatch.setattr(evaluator_module, "scan_full_hypothesis_family", _scan)


@pytest.mark.parametrize("observation_source", ["iem_asos", "openmeteo_hourly"])
def test_day0_fallback_observation_source_rejected_before_signal_path(observation_source):
    candidate = MarketCandidate(
        city=NYC,
        target_date="2026-04-12",
        outcomes=[],
        hours_since_open=2.0,
        hours_to_resolution=4.0,
        observation=Day0ObservationContext(
            high_so_far=70.0,
            low_so_far=62.0,
            current_temp=69.0,
            source=observation_source,
            observation_time=datetime(2026, 4, 12, 18, 0, tzinfo=timezone.utc).isoformat(),
            unit="F",
        ),
        discovery_mode=DiscoveryMode.DAY0_CAPTURE.value,
    )

    decisions = evaluator_module.evaluate_candidate(
        candidate,
        conn=None,
        portfolio=PortfolioState(bankroll=150.0),
        clob=object(),
        limits=evaluator_module.RiskLimits(),
        decision_time=datetime(2026, 4, 12, 18, 5, tzinfo=timezone.utc),
    )

    assert len(decisions) == 1
    assert decisions[0].should_trade is False
    assert decisions[0].rejection_stage == "OBSERVATION_SOURCE_UNAUTHORIZED"
    assert "observation_source_policy" in decisions[0].applied_validations


@pytest.mark.parametrize("settlement_source_type", ["hko", "noaa", "cwa_station"])
def test_day0_entry_rejects_settlement_types_without_executable_source_policy(settlement_source_type):
    city = City(
        name=f"Test {settlement_source_type}",
        lat=40.7772,
        lon=-73.8726,
        timezone="America/New_York",
        cluster="TEST",
        settlement_unit="F",
        wu_station="KXXX",
        settlement_source_type=settlement_source_type,
    )
    candidate = MarketCandidate(
        city=city,
        target_date="2026-04-12",
        outcomes=[],
        hours_since_open=2.0,
        hours_to_resolution=4.0,
        observation=Day0ObservationContext(
            high_so_far=70.0,
            low_so_far=62.0,
            current_temp=69.0,
            source="wu_api",
            observation_time=datetime(2026, 4, 12, 18, 0, tzinfo=timezone.utc).isoformat(),
            unit="F",
        ),
        discovery_mode=DiscoveryMode.DAY0_CAPTURE.value,
    )

    decisions = evaluator_module.evaluate_candidate(
        candidate,
        conn=None,
        portfolio=PortfolioState(bankroll=150.0),
        clob=object(),
        limits=evaluator_module.RiskLimits(),
        decision_time=datetime(2026, 4, 12, 18, 5, tzinfo=timezone.utc),
    )

    assert len(decisions) == 1
    assert decisions[0].should_trade is False
    assert decisions[0].rejection_stage == "OBSERVATION_SOURCE_UNAUTHORIZED"
    assert "source role is not authorized" in decisions[0].rejection_reasons[0]
    assert "observation_source_policy" in decisions[0].applied_validations


def test_day0_entry_rejects_stale_epoch_observation_before_signal_path(monkeypatch):
    monkeypatch.setattr(
        evaluator_module,
        "fetch_ensemble",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("fetch_ensemble must not run")),
    )
    observed_at = datetime(2026, 4, 12, 16, 0, tzinfo=timezone.utc)
    decision_time = datetime(2026, 4, 12, 18, 5, tzinfo=timezone.utc)
    candidate = MarketCandidate(
        city=NYC,
        target_date="2026-04-12",
        outcomes=[],
        hours_since_open=2.0,
        hours_to_resolution=4.0,
        observation=Day0ObservationContext(
            high_so_far=70.0,
            low_so_far=62.0,
            current_temp=69.0,
            source="wu_api",
            observation_time=int(observed_at.timestamp()),
            unit="F",
        ),
        discovery_mode=DiscoveryMode.DAY0_CAPTURE.value,
    )

    decisions = evaluator_module.evaluate_candidate(
        candidate,
        conn=None,
        portfolio=PortfolioState(bankroll=150.0),
        clob=object(),
        limits=evaluator_module.RiskLimits(),
        decision_time=decision_time,
    )

    assert len(decisions) == 1
    assert decisions[0].should_trade is False
    assert decisions[0].rejection_stage == "SIGNAL_QUALITY"
    assert decisions[0].availability_status == "DATA_STALE"
    assert "observation_quality_gate" in decisions[0].applied_validations
    assert "stale" in decisions[0].rejection_reasons[0]


def test_day0_entry_rejects_nonfinite_observation_before_signal_path(monkeypatch):
    monkeypatch.setattr(
        evaluator_module,
        "fetch_ensemble",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("fetch_ensemble must not run")),
    )
    candidate = MarketCandidate(
        city=NYC,
        target_date="2026-04-12",
        outcomes=[],
        hours_since_open=2.0,
        hours_to_resolution=4.0,
        observation=Day0ObservationContext(
            high_so_far=70.0,
            low_so_far=62.0,
            current_temp=float("nan"),
            source="wu_api",
            observation_time=datetime(2026, 4, 12, 18, 0, tzinfo=timezone.utc).isoformat(),
            unit="F",
        ),
        discovery_mode=DiscoveryMode.DAY0_CAPTURE.value,
    )

    decisions = evaluator_module.evaluate_candidate(
        candidate,
        conn=None,
        portfolio=PortfolioState(bankroll=150.0),
        clob=object(),
        limits=evaluator_module.RiskLimits(),
        decision_time=datetime(2026, 4, 12, 18, 5, tzinfo=timezone.utc),
    )

    assert len(decisions) == 1
    assert decisions[0].should_trade is False
    assert decisions[0].rejection_stage == "SIGNAL_QUALITY"
    assert decisions[0].availability_status == "DATA_UNAVAILABLE"
    assert "observation_quality_gate" in decisions[0].applied_validations
    assert "non-finite" in decisions[0].rejection_reasons[0]


def test_chain_reconciliation_updates_live_position_from_chain(monkeypatch, tmp_path):
    db_path = tmp_path / "zeus.db"
    conn = get_connection(db_path)
    init_schema(conn)
    conn.execute(
        """
        INSERT INTO position_current (position_id, phase, trade_id, market_id, city, cluster, target_date, bin_label, direction, unit, size_usd, shares, cost_basis_usd, entry_price, p_posterior, entry_method, strategy_key, edge_source, discovery_mode, chain_state, order_id, order_status, updated_at, temperature_metric)
        VALUES ('t1', 'active', 't1', 'm1', 'NYC', 'NYC', '2026-04-01', '39-40°F', 'buy_yes', 'F', 8.0, 20.0, 8.0, 0.4, 0.6, 'ens_member_counting', 'center_buy', 'center_buy', 'opening_hunt', 'unknown', '', 'filled', '2026-04-01T00:00:00Z', 'high')
        """
    )
    conn.commit()
    conn.close()
    portfolio = PortfolioState(positions=[_position(size_usd=8.0, shares=20.0, cost_basis_usd=8.0)])

    class DummyClob:
        def __init__(self):
            pass

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
    monkeypatch.setattr(cycle_runner, "load_portfolio", lambda: portfolio)
    monkeypatch.setattr("src.state.db.get_trade_connection_with_world", lambda mode: get_connection(db_path))
    monkeypatch.setattr(cycle_runner, "save_portfolio", lambda state, *args, **kwargs: None)
    monkeypatch.setattr(cycle_runner, "PolymarketClient", DummyClob)
    monkeypatch.setattr(cycle_runner, "get_tracker", lambda: StrategyTracker())
    monkeypatch.setattr(cycle_runner, "save_tracker", lambda tracker: None)
    monkeypatch.setattr(cycle_runner, "find_weather_markets", lambda **kwargs: [])
    def _mock_refresh(conn, clob, pos):
        pos.entry_method = getattr(pos, "entry_method", "ens_member_counting") or "ens_member_counting"
        assert pos.entry_method
        pos.last_monitor_market_price_is_fresh = True
        pos.last_monitor_prob_is_fresh = True
        return EdgeContext(p_raw=np.array([]), p_cal=np.array([]), p_market=np.array([pos.entry_price]), p_posterior=pos.p_posterior, forward_edge=pos.p_posterior - pos.entry_price, alpha=0.0, confidence_band_upper=pos.p_posterior - pos.entry_price + 0.1, confidence_band_lower=pos.p_posterior - pos.entry_price - 0.1, entry_provenance=None, decision_snapshot_id="snap", n_edges_found=1, n_edges_after_fdr=1, market_velocity_1h=0.0, divergence_score=0.0)

    monkeypatch.setattr("src.engine.monitor_refresh.refresh_position", _mock_refresh)
    monkeypatch.setattr("src.control.control_plane.process_commands", lambda: [])
    monkeypatch.setattr("src.observability.status_summary.write_status", lambda cycle_summary=None: None)

    summary = cycle_runner.run_cycle(DiscoveryMode.OPENING_HUNT)
    pos = portfolio.positions[0]

    assert summary["chain_sync"]["synced"] == 1
    assert summary["chain_sync"]["updated"] == 1
    assert pos.shares == pytest.approx(25.0)
    assert pos.cost_basis_usd == pytest.approx(5.0)
    assert pos.chain_state == "synced"
    assert pos.condition_id == "cond-1"


def test_run_cycle_monitoring_uses_attached_shared_connection(monkeypatch, tmp_path):
    trade_db = tmp_path / "zeus-paper.db"
    shared_db = tmp_path / "zeus-world.db"
    conn = get_connection(trade_db)
    init_schema(conn)
    conn.close()

    shared_conn = sqlite3.connect(str(shared_db))
    shared_conn.execute("CREATE TABLE shared_sentinel (id INTEGER PRIMARY KEY)")
    shared_conn.commit()
    shared_conn.close()

    monkeypatch.setattr(db_module, "ZEUS_WORLD_DB_PATH", shared_db)
    monkeypatch.setattr(db_module, "_zeus_trade_db_path", lambda mode=None: trade_db)
    assert cycle_runner.get_connection is db_module.get_trade_connection_with_world
    monkeypatch.setattr(cycle_runner, "get_current_level", lambda: RiskLevel.RED)
    monkeypatch.setattr(cycle_runner, "load_portfolio", lambda: PortfolioState())
    monkeypatch.setattr(cycle_runner, "get_tracker", lambda: StrategyTracker())
    monkeypatch.setattr(cycle_runner, "save_tracker", lambda tracker: None)
    monkeypatch.setattr(cycle_runner, "_reconcile_pending_positions", lambda *args, **kwargs: {"entered": 0, "voided": 0, "dirty": False, "tracker_dirty": False})
    monkeypatch.setattr(cycle_runner, "_run_chain_sync", lambda portfolio, clob, conn: ({}, True))
    monkeypatch.setattr(cycle_runner, "_cleanup_orphan_open_orders", lambda portfolio, clob: 0)
    monkeypatch.setattr(cycle_runner, "_entry_bankroll_for_cycle", lambda portfolio, clob: (100.0, {"config_cap_usd": 100.0, "portfolio_initial_bankroll_usd": 100.0, "dynamic_cap_usd": 100.0}))
    monkeypatch.setattr(cycle_runner, "_execute_discovery_phase", lambda *args, **kwargs: (False, False))

    def fake_monitoring_phase(conn, clob, portfolio, artifact, tracker, summary, deps=None):
        assert conn.execute("SELECT name FROM world.sqlite_master WHERE type = 'table' AND name = 'shared_sentinel'").fetchone() is not None
        summary["monitor_incomplete_exit_context"] = 0
        return False, False

    monkeypatch.setattr(cycle_runner, "_execute_monitoring_phase", fake_monitoring_phase)
    monkeypatch.setattr("src.control.control_plane.process_commands", lambda: [])
    monkeypatch.setattr("src.observability.status_summary.write_status", lambda cycle_summary=None: None)
    monkeypatch.setattr(cycle_runner, "PolymarketClient", lambda: type("DummyClob", (), {"get_balance": lambda self: 100.0})())

    summary = cycle_runner.run_cycle(DiscoveryMode.OPENING_HUNT)

    assert summary["monitor_incomplete_exit_context"] == 0


def test_run_cycle_monitoring_fails_loudly_when_shared_seam_unavailable(monkeypatch):
    def broken_get_connection(*args, **kwargs):
        raise RuntimeError("shared unavailable")

    monkeypatch.setattr(cycle_runner, "get_connection", broken_get_connection)
    monkeypatch.setattr(cycle_runner, "get_current_level", lambda: RiskLevel.RED)
    monkeypatch.setattr("src.control.control_plane.process_commands", lambda: [])

    with pytest.raises(RuntimeError, match="shared unavailable"):
        cycle_runner.run_cycle(DiscoveryMode.OPENING_HUNT)


def test_stale_order_cleanup_cancels_orphan_open_orders(monkeypatch, tmp_path):
    db_path = tmp_path / "zeus.db"
    portfolio_path = tmp_path / "positions.json"
    conn = get_connection(db_path)
    init_schema(conn)
    _insert_executable_snapshot(
        conn,
        snapshot_id="snap-orphan",
        selected_outcome_token_id="yes-orphan",
        yes_token_id="yes-orphan",
        no_token_id="no-orphan",
        condition_id="cond-orphan",
        top_bid="0.39",
        top_ask="0.40",
    )
    from src.execution.command_bus import IdempotencyKey, IntentKind
    from src.execution.executor import _persist_pre_submit_envelope
    from src.state.venue_command_repo import append_event, insert_command

    created_at = datetime(2026, 4, 3, 2, 0, 5, tzinfo=timezone.utc).isoformat()
    command_id = "cmd-orphan-entry"
    envelope_id = _persist_pre_submit_envelope(
        conn,
        command_id=command_id,
        snapshot_id="snap-orphan",
        token_id="yes-orphan",
        side="BUY",
        price=0.40,
        size=10.0,
        order_type="GTC",
        post_only=True,
        captured_at=created_at,
    )
    insert_command(
        conn,
        command_id=command_id,
        envelope_id=envelope_id,
        snapshot_id="snap-orphan",
        position_id="orphan-position",
        decision_id="orphan-placement",
        idempotency_key=IdempotencyKey.from_inputs(
            decision_id="orphan-placement",
            token_id="yes-orphan",
            side="BUY",
            price=0.40,
            size=10.0,
            intent_kind=IntentKind.ENTRY,
        ).value,
        intent_kind=IntentKind.ENTRY.value,
        market_id="cond-orphan",
        token_id="yes-orphan",
        side="BUY",
        size=10.0,
        price=0.40,
        created_at=created_at,
        snapshot_checked_at=created_at,
        expected_min_tick_size=Decimal("0.01"),
        expected_min_order_size=Decimal("5"),
        expected_neg_risk=False,
        venue_order_id="orphan-1",
        reason="test_orphan_open_order",
    )
    append_event(
        conn,
        command_id=command_id,
        event_type="SUBMIT_REQUESTED",
        occurred_at=created_at,
        payload={"source": "test_stale_order_cleanup"},
    )
    append_event(
        conn,
        command_id=command_id,
        event_type="SUBMIT_ACKED",
        occurred_at=created_at,
        payload={"venue_order_id": "orphan-1"},
    )
    conn.commit()
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
        def __init__(self):
            pass

        def get_positions_from_api(self):
            return []

        def get_open_orders(self):
            return [{"id": "tracked"}, {"id": "orphan-1"}]

        def get_order_status(self, order_id):
            return {"status": "OPEN"}

        def cancel_order(self, order_id):
            read_conn = get_connection(db_path)
            try:
                event_types = [
                    row["event_type"]
                    for row in read_conn.execute(
                        "SELECT event_type FROM venue_command_events "
                        "WHERE command_id = ? ORDER BY sequence_no",
                        (command_id,),
                    ).fetchall()
                ]
            finally:
                read_conn.close()
            assert event_types[-1] == "CANCEL_REQUESTED"
            cancelled.append(order_id)
            return {"status": "CANCELLED", "id": order_id}

        def get_balance(self):
            return 100.0

    monkeypatch.setattr(cycle_runner, "get_current_level", lambda: RiskLevel.GREEN)
    monkeypatch.setattr(cycle_runner, "get_connection", lambda: get_connection(db_path))
    # P4 (Tier 2.1): provide portfolio directly instead of via JSON file path.
    # load_portfolio no longer falls back to JSON; this test isolates stale-order
    # cleanup from portfolio loading mechanism.
    monkeypatch.setattr(cycle_runner, "load_portfolio", lambda: PortfolioState(positions=[_position(
        trade_id="pending-1",
        state="pending_tracked",
        order_id="tracked",
        order_posted_at="2026-03-30T00:00:00Z",
        order_timeout_at="2099-01-01T00:00:00+00:00",
    )]))
    monkeypatch.setattr(cycle_runner, "save_portfolio", lambda state, *args, **kwargs: None)
    monkeypatch.setattr(cycle_runner, "PolymarketClient", DummyClob)
    monkeypatch.setattr(cycle_runner, "get_tracker", lambda: StrategyTracker())
    monkeypatch.setattr(cycle_runner, "save_tracker", lambda tracker: None)
    monkeypatch.setattr(cycle_runner, "find_weather_markets", lambda **kwargs: [])
    monkeypatch.setattr(
        "src.execution.exit_safety.gate_for_intent",
        lambda intent: types.SimpleNamespace(
            allow_cancel=True,
            block_reason=None,
            state=types.SimpleNamespace(value="READY"),
        ),
    )
    monkeypatch.setattr("src.control.control_plane.process_commands", lambda: [])
    monkeypatch.setattr("src.observability.status_summary.write_status", lambda cycle_summary=None: None)

    summary = cycle_runner.run_cycle(DiscoveryMode.OPENING_HUNT)

    assert summary["stale_orders_cancelled"] == 1
    assert cancelled == ["orphan-1"]
    conn = get_connection(db_path)
    try:
        events = [
            row["event_type"]
            for row in conn.execute(
                "SELECT event_type FROM venue_command_events "
                "WHERE command_id = ? ORDER BY sequence_no",
                (command_id,),
            ).fetchall()
        ]
        state = conn.execute(
            "SELECT state FROM venue_commands WHERE command_id = ?",
            (command_id,),
        ).fetchone()["state"]
    finally:
        conn.close()
    assert events[-2:] == ["CANCEL_REQUESTED", "CANCEL_ACKED"]
    assert state == "CANCELLED"


def test_stale_order_cleanup_blocks_without_command_journal():
    conn = sqlite3.connect(":memory:")
    conn.execute(
        """
        CREATE TABLE trade_decisions (
            id INTEGER PRIMARY KEY,
            order_id TEXT,
            order_posted_at TEXT
        )
        """
    )
    conn.commit()
    cancelled: list[str] = []

    class DummyClob:
        def get_open_orders(self):
            return [{"id": "orphan-no-journal"}]

        def cancel_order(self, order_id):
            cancelled.append(order_id)
            return {"status": "CANCELLED", "id": order_id}

    cancelled_count = cycle_runtime.cleanup_orphan_open_orders(
        PortfolioState(),
        DummyClob(),
        deps=types.SimpleNamespace(logger=logging.getLogger("test_no_journal")),
        conn=conn,
    )
    conn.close()

    assert cancelled_count == 0
    assert cancelled == []


def test_stale_order_cleanup_blocks_without_matching_command(tmp_path):
    conn = get_connection(tmp_path / "orphan-no-command.db")
    init_schema(conn)
    cancelled: list[str] = []

    class DummyClob:
        def get_open_orders(self):
            return [{"id": "orphan-no-command"}]

        def cancel_order(self, order_id):
            cancelled.append(order_id)
            return {"status": "CANCELLED", "id": order_id}

    cancelled_count = cycle_runtime.cleanup_orphan_open_orders(
        PortfolioState(),
        DummyClob(),
        deps=types.SimpleNamespace(logger=logging.getLogger("test_no_command")),
        conn=conn,
    )
    conn.close()

    assert cancelled_count == 0
    assert cancelled == []


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
            return {"status": "CONFIRMED", "avgPrice": 0.41, "filledSize": 24.39}

    monkeypatch.setattr(cycle_runner, "_utcnow", lambda: datetime(2026, 4, 2, 6, 0, tzinfo=timezone.utc))
    monkeypatch.setattr(cycle_runner, "get_connection", lambda: get_connection(db_path))
    summary = cycle_runner._reconcile_pending_positions(portfolio, DummyClob(), Tracker())
    pos = portfolio.positions[0]
    conn = get_connection(db_path)
    # Post-P9: entry fills go to execution_fact, not position_events
    exec_row = conn.execute(
        "SELECT fill_price, terminal_exec_status FROM execution_fact WHERE position_id = ? AND order_role = 'entry'",
        ("pending-fill-1",),
    ).fetchone()
    conn.close()

    assert summary["entered"] == 1
    assert pos.state == "entered"
    assert pos.entry_fill_verified is True
    assert pos.entry_order_id == "ord-1"
    assert pos.order_status == "confirmed"
    assert pos.chain_state == "local_only"
    assert pos.size_usd == pytest.approx(24.39 * 0.41)
    assert pos.cost_basis_usd == pytest.approx(24.39 * 0.41)
    assert pos.entry_price_avg_fill == pytest.approx(0.41)
    assert pos.shares_filled == pytest.approx(24.39)
    assert pos.filled_cost_basis_usd == pytest.approx(24.39 * 0.41)
    assert pos.shares_remaining == pytest.approx(0.0)
    assert pos.entry_economics_authority == ENTRY_ECONOMICS_AVG_FILL_PRICE
    assert pos.fill_authority == FILL_AUTHORITY_VENUE_CONFIRMED_FULL
    assert pos.corrected_executable_economics_eligible is False
    assert pos.has_fill_economics_authority is True
    assert pos.fill_quality == pytest.approx((0.41 - 0.40) / 0.40)
    assert exec_row is not None
    assert exec_row["terminal_exec_status"] == "filled"


def test_reconcile_pending_partial_fill_updates_fill_authority_without_finality(monkeypatch):
    db_path = Path(tempfile.mkdtemp()) / "zeus.db"
    conn = get_connection(db_path)
    init_schema(conn)
    conn.close()

    portfolio = PortfolioState(positions=[_position(
        trade_id="pending-partial-1",
        state="pending_tracked",
        order_id="ord-partial-1",
        entry_order_id="",
        entry_fill_verified=False,
        token_id="tok_yes_partial",
        no_token_id="tok_no_partial",
        size_usd=10.0,
        entry_price=0.40,
        shares=25.0,
        target_notional_usd=10.0,
        submitted_notional_usd=10.0,
        entry_price_submitted=0.40,
        shares_submitted=25.0,
    )])

    class DummyClob:
        paper_mode = False
        def get_order_status(self, order_id):
            assert order_id == "ord-partial-1"
            return {
                "status": "PARTIAL",
                "avgPrice": 0.41,
                "filledSize": 10.0,
                "trade_id": "venue-trade-partial-runtime",
            }

    monkeypatch.setattr(cycle_runner, "_utcnow", lambda: datetime(2026, 4, 2, 6, 0, tzinfo=timezone.utc))
    monkeypatch.setattr(cycle_runner, "get_connection", lambda: get_connection(db_path))

    summary = cycle_runner._reconcile_pending_positions(portfolio, DummyClob(), tracker=None)
    pos = portfolio.positions[0]

    assert summary["entered"] == 0
    assert summary["voided"] == 0
    assert summary["dirty"] is True
    assert pos.state == "pending_tracked"
    assert pos.order_status == "partial"
    assert pos.entry_fill_verified is False
    assert pos.entry_price_avg_fill == pytest.approx(0.41)
    assert pos.shares_filled == pytest.approx(10.0)
    assert pos.filled_cost_basis_usd == pytest.approx(4.10)
    assert pos.shares_remaining == pytest.approx(15.0)
    assert pos.entry_economics_authority == ENTRY_ECONOMICS_AVG_FILL_PRICE
    assert pos.fill_authority == FILL_AUTHORITY_VENUE_CONFIRMED_PARTIAL
    assert pos.has_fill_economics_authority is True


def test_exposure_gate_skips_new_entries_without_forcing_reduction(monkeypatch, tmp_path):
    db_path = tmp_path / "zeus.db"
    conn = get_connection(db_path)
    init_schema(conn)
    conn.close()
    # Use a future target_date so monitoring doesn't exit the position before
    # the exposure gate is evaluated.
    portfolio = PortfolioState(positions=[_position(size_usd=72.0, shares=180.0, cost_basis_usd=72.0, target_date="2026-12-01")])

    class DummyClob:
        def __init__(self):
            pass

        def get_positions_from_api(self):
            return []

        def get_balance(self):
            return 100.0

        def get_open_orders(self):
            return []

    monkeypatch.setattr(cycle_runner, "settings", _CycleSettingsStub(capital_base_usd=150.0, smoke_test_portfolio_cap_usd=None))
    monkeypatch.setattr(cycle_runner, "get_current_level", lambda: RiskLevel.GREEN)
    monkeypatch.setattr(cycle_runner, "get_force_exit_review", lambda: False)
    monkeypatch.setattr(cycle_runner, "get_connection", lambda: get_connection(db_path))
    monkeypatch.setattr(cycle_runner, "load_portfolio", lambda: portfolio)
    monkeypatch.setattr(cycle_runner, "save_portfolio", lambda state, *args, **kwargs: None)
    monkeypatch.setattr(cycle_runner, "PolymarketClient", DummyClob)
    monkeypatch.setattr(cycle_runner, "get_tracker", lambda: StrategyTracker())
    monkeypatch.setattr(cycle_runner, "save_tracker", lambda tracker: None)
    monkeypatch.setattr(cycle_runner, "is_entries_paused", lambda: False)
    monkeypatch.setattr(cycle_runner, "_run_chain_sync", lambda portfolio, clob, conn: ({}, True))
    def _mock_refresh(conn, clob, pos):
        pos.entry_method = getattr(pos, "entry_method", "ens_member_counting") or "ens_member_counting"
        assert pos.entry_method
        pos.last_monitor_market_price_is_fresh = True
        pos.last_monitor_prob_is_fresh = True
        return EdgeContext(p_raw=np.array([]), p_cal=np.array([]), p_market=np.array([pos.entry_price]), p_posterior=pos.p_posterior, forward_edge=pos.p_posterior - pos.entry_price, alpha=0.0, confidence_band_upper=pos.p_posterior - pos.entry_price + 0.1, confidence_band_lower=pos.p_posterior - pos.entry_price - 0.1, entry_provenance=None, decision_snapshot_id="snap", n_edges_found=1, n_edges_after_fdr=1, market_velocity_1h=0.0, divergence_score=0.0)

    monkeypatch.setattr("src.engine.monitor_refresh.refresh_position", _mock_refresh)
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
    _insert_executable_snapshot(
        conn,
        snapshot_id="snap-runtime-exec",
        selected_outcome_token_id="yes1",
        outcome_label="YES",
        yes_token_id="yes1",
        no_token_id="no1",
        condition_id="m1",
        top_bid="0.25",
        top_ask="0.25",
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS opportunity_fact (
            decision_id TEXT PRIMARY KEY,
            candidate_id TEXT,
            city TEXT,
            target_date TEXT,
            range_label TEXT,
            direction TEXT CHECK (direction IN ('buy_yes', 'buy_no', 'unknown')),
            strategy_key TEXT CHECK (strategy_key IN (
                'settlement_capture',
                'shoulder_sell',
                'center_buy',
                'opening_inertia'
            )),
            discovery_mode TEXT,
            entry_method TEXT,
            snapshot_id TEXT,
            p_raw REAL,
            p_cal REAL,
            p_market REAL,
            alpha REAL,
            best_edge REAL,
            ci_width REAL,
            rejection_stage TEXT,
            rejection_reason_json TEXT,
            availability_status TEXT CHECK (availability_status IN (
                'ok',
                'missing',
                'stale',
                'rate_limited',
                'unavailable',
                'chain_unavailable'
            )),
            should_trade INTEGER NOT NULL CHECK (should_trade IN (0, 1)),
            recorded_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS availability_fact (
            availability_id TEXT PRIMARY KEY,
            scope_type TEXT NOT NULL CHECK (scope_type IN ('cycle', 'candidate', 'city_target', 'order', 'chain')),
            scope_key TEXT NOT NULL,
            failure_type TEXT NOT NULL,
            started_at TEXT NOT NULL,
            ended_at TEXT,
            impact TEXT NOT NULL CHECK (impact IN ('skip', 'degrade', 'retry', 'block')),
            details_json TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS execution_fact (
            intent_id TEXT PRIMARY KEY,
            position_id TEXT,
            decision_id TEXT,
            order_role TEXT NOT NULL CHECK (order_role IN ('entry', 'exit')),
            strategy_key TEXT CHECK (strategy_key IN (
                'settlement_capture',
                'shoulder_sell',
                'center_buy',
                'opening_inertia'
            )),
            posted_at TEXT,
            filled_at TEXT,
            voided_at TEXT,
            submitted_price REAL,
            fill_price REAL,
            shares REAL,
            fill_quality REAL,
            latency_seconds REAL,
            venue_status TEXT,
            terminal_exec_status TEXT
        )
        """
    )
    conn.commit()
    conn.close()

    portfolio = PortfolioState()

    class DummyClob:
        def __init__(self):
            pass

        def get_positions_from_api(self):
            return []

        def get_balance(self):
            return 100.0

        def get_open_orders(self):
            return []

    class DummyDecision:
        def __init__(self, should_trade):
            self.should_trade = should_trade
            self.edge = _edge() if should_trade else None
            self.tokens = {
                "market_id": "m1",
                "token_id": "yes1",
                "no_token_id": "no1",
                "executable_snapshot_id": "snap-runtime-exec",
                "executable_snapshot_min_tick_size": "0.01",
                "executable_snapshot_min_order_size": "5",
                "executable_snapshot_neg_risk": False,
            } if should_trade else None
            self.size_usd = 5.0
            self.decision_id = "d1" if should_trade else "d2"
            self.rejection_stage = "EDGE_INSUFFICIENT"
            self.rejection_reasons = ["small"]
            self.selected_method = "ens_member_counting"
            self.applied_validations = ["ens_fetch"]
            self.decision_snapshot_id = "snap-1"
            self.edge_source = "opening_inertia"
            self.strategy_key = "opening_inertia" if should_trade else ""
            self.edge_context = None
            self.settlement_semantics_json = '{"measurement_unit":"F"}'
            self.epistemic_context_json = '{"decision_time_utc":"2026-04-01T00:00:00Z"}'
            self.edge_context_json = '{"forward_edge":0.12}'
            self.sizing_bankroll = 100.0
            self.kelly_multiplier_used = 0.25
            self.execution_fee_rate = 0.0
            self.safety_cap_usd = None

    monkeypatch.setattr(cycle_runner, "get_current_level", lambda: RiskLevel.GREEN)
    monkeypatch.setattr(cycle_runner, "get_connection", lambda: get_connection(db_path))
    monkeypatch.setattr("src.state.db.get_trade_connection_with_world", lambda mode: get_connection(db_path))
    monkeypatch.setattr(cycle_runner, "load_portfolio", lambda: portfolio)
    monkeypatch.setattr(cycle_runner, "save_portfolio", lambda state, *args, **kwargs: None)
    monkeypatch.setattr(cycle_runner, "PolymarketClient", DummyClob)
    monkeypatch.setattr(cycle_runner, "get_tracker", lambda: StrategyTracker())
    monkeypatch.setattr(cycle_runner, "save_tracker", lambda tracker: None)
    monkeypatch.setattr(cycle_runner, "get_last_scan_authority", lambda: "VERIFIED")
    monkeypatch.setattr(cycle_runner, "find_weather_markets", lambda **kwargs: [{
        "city": NYC,
        "target_date": "2026-04-01",
        "hours_since_open": 12.0,
        "hours_to_resolution": 24.0,
        "outcomes": [{"title": "39-40°F", "range_low": 39, "range_high": 40, "token_id": "yes1", "no_token_id": "no1", "market_id": "m1", "price": 0.35}],
        "temperature_metric": "high",
    }])
    monkeypatch.setattr(cycle_runner, "evaluate_candidate", lambda *args, **kwargs: [DummyDecision(True), DummyDecision(False)])
    monkeypatch.setattr(
        cycle_runner,
        "create_execution_intent",
        lambda **kwargs: (_ for _ in ()).throw(
            AssertionError("live final-intent path must not create legacy intent")
        ),
    )
    monkeypatch.setattr(
        cycle_runner,
        "execute_intent",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("live final-intent path must not submit legacy intent")
        ),
    )
    monkeypatch.setattr(
        cycle_runner,
        "execute_final_intent",
        lambda intent, **kwargs: OrderResult(
            status="filled",
            trade_id="rt1",
            order_id="o1",
            fill_price=float(intent.final_limit_price),
            submitted_price=float(intent.final_limit_price),
            shares=10.0,
            command_state="ACKED",
        ),
        raising=False,
    )
    monkeypatch.setattr(
        cycle_runner,
        "select_final_order_type",
        lambda conn, snapshot_id: "FOK",
        raising=False,
    )
    monkeypatch.setattr("src.control.control_plane.process_commands", lambda: [])
    monkeypatch.setattr("src.observability.status_summary.write_status", lambda cycle_summary=None: None)
    monkeypatch.setattr("src.engine.monitor_refresh.refresh_position", lambda conn, clob, pos: (_ for _ in ()).throw(AssertionError("monitor not expected")))
    monkeypatch.setattr(cycle_runner, "get_force_exit_review", lambda: False)
    monkeypatch.setattr(cycle_runner, "_run_chain_sync", lambda portfolio, clob, conn: ({}, True))
    monkeypatch.setattr(cycle_runner, "is_strategy_enabled", lambda strategy: True)
    _allow_entry_gates_for_runtime_test(monkeypatch)

    summary = cycle_runner.run_cycle(DiscoveryMode.OPENING_HUNT)

    conn = get_connection(db_path)
    artifact = conn.execute("SELECT artifact_json FROM decision_log ORDER BY id DESC LIMIT 1").fetchone()
    shadow = conn.execute("SELECT p_raw_json, p_cal_json, edges_json FROM shadow_signals ORDER BY id DESC LIMIT 1").fetchone()
    opportunity_rows = conn.execute(
        """
        SELECT decision_id, range_label, direction, strategy_key, snapshot_id, availability_status, should_trade, rejection_stage
        FROM opportunity_fact
        ORDER BY decision_id
        """
    ).fetchall()
    trace_rows = conn.execute(
        """
        SELECT decision_id, trace_status, p_raw_json, p_cal_json, p_market_json
        FROM probability_trace_fact
        ORDER BY decision_id
        """
    ).fetchall()
    availability_count = conn.execute("SELECT COUNT(*) AS n FROM availability_fact").fetchone()
    execution_rows = conn.execute(
        """
        SELECT intent_id, decision_id, order_role, terminal_exec_status
        FROM execution_fact
        ORDER BY intent_id
        """
    ).fetchall()
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
    assert [row["decision_id"] for row in opportunity_rows] == ["d1", "d2"]
    assert opportunity_rows[0]["should_trade"] == 1
    assert opportunity_rows[0]["range_label"] == "39-40°F"
    assert opportunity_rows[0]["direction"] == "buy_yes"
    assert opportunity_rows[0]["strategy_key"] == "opening_inertia"
    assert opportunity_rows[0]["snapshot_id"] == "snap-1"
    assert opportunity_rows[0]["availability_status"] == "ok"
    assert opportunity_rows[1]["should_trade"] == 0
    assert opportunity_rows[1]["range_label"] is None
    assert opportunity_rows[1]["direction"] == "unknown"
    assert opportunity_rows[1]["strategy_key"] is None
    assert opportunity_rows[1]["snapshot_id"] == "snap-1"
    assert opportunity_rows[1]["rejection_stage"] == "EDGE_INSUFFICIENT"
    assert [row["decision_id"] for row in trace_rows] == ["d1", "d2"]
    assert [row["trace_status"] for row in trace_rows] == ["pre_vector_unavailable", "pre_vector_unavailable"]
    assert trace_rows[0]["p_raw_json"] is None
    assert trace_rows[0]["p_cal_json"] is None
    assert trace_rows[0]["p_market_json"] is None
    assert availability_count["n"] == 0
    assert len(execution_rows) == 1
    assert execution_rows[0]["intent_id"] == "rt1:entry"
    assert execution_rows[0]["decision_id"] == "d1"
    assert execution_rows[0]["order_role"] == "entry"
    assert execution_rows[0]["terminal_exec_status"] == "filled"


def test_probability_trace_skip_is_warned_when_decision_id_missing(tmp_path, caplog):
    db_path = tmp_path / "zeus.db"
    conn = get_connection(db_path)
    init_schema(conn)

    decision = types.SimpleNamespace(
        should_trade=False,
        edge=None,
        decision_id="",
        rejection_stage="SIGNAL_QUALITY",
        rejection_reasons=["missing decision id"],
        selected_method="ens_member_counting",
        applied_validations=[],
        decision_snapshot_id="",
        edge_source="",
        strategy_key="",
        availability_status="DATA_UNAVAILABLE",
    )
    deps = types.SimpleNamespace(
        MODE_PARAMS={DiscoveryMode.OPENING_HUNT: {"min_hours_to_resolution": 6}},
        find_weather_markets=lambda **kwargs: [{
            "city": NYC,
            "target_date": "2026-04-01",
            "hours_since_open": 12.0,
            "hours_to_resolution": 24.0,
            "outcomes": [],
            "temperature_metric": "high",
            "event_id": "evt-missing-decision",
            "slug": "evt-missing-decision",
        }],
        MarketCandidate=MarketCandidate,
        DiscoveryMode=DiscoveryMode,
        evaluate_candidate=lambda *args, **kwargs: [decision],
        get_last_scan_authority=lambda: "VERIFIED",
        logger=logging.getLogger("test_probability_trace_skip"),
        NoTradeCase=NoTradeCase,
        _classify_edge_source=lambda mode, edge: "",
    )
    artifact = CycleArtifact(mode=DiscoveryMode.OPENING_HUNT.value, started_at="2026-04-03T00:00:00Z")
    summary = {"candidates": 0, "no_trades": 0}

    with caplog.at_level("WARNING"):
        cycle_runtime.execute_discovery_phase(
            conn,
            types.SimpleNamespace(),
            PortfolioState(),
            artifact,
            StrategyTracker(),
            types.SimpleNamespace(),
            DiscoveryMode.OPENING_HUNT,
            summary,
            150.0,
            datetime(2026, 4, 3, tzinfo=timezone.utc),
            env="paper",
            deps=deps,
        )
    conn.close()

    assert "Probability trace not written" in caplog.text
    assert "skipped_missing_decision_id" in caplog.text


def test_discovery_phase_blocks_stale_market_scan_before_evaluator(tmp_path):
    conn = get_connection(tmp_path / "scan-authority.db")
    init_schema(conn)
    artifact = CycleArtifact(mode=DiscoveryMode.OPENING_HUNT.value, started_at="2026-04-03T00:00:00Z")
    summary = {"candidates": 0, "no_trades": 0}
    market = {
        "city": NYC,
        "target_date": "2026-04-01",
        "hours_since_open": 12.0,
        "hours_to_resolution": 24.0,
        "event_id": "evt-stale",
        "slug": "slug-stale",
        "temperature_metric": "high",
        "outcomes": [
            {"title": "39-40°F", "range_low": 39, "range_high": 40},
        ],
    }
    deps = types.SimpleNamespace(
        MODE_PARAMS={DiscoveryMode.OPENING_HUNT: {}},
        find_weather_markets=lambda **kwargs: [market],
        get_last_scan_authority=lambda: "STALE",
        DiscoveryMode=DiscoveryMode,
        logger=types.SimpleNamespace(warning=lambda *args, **kwargs: None),
        NoTradeCase=NoTradeCase,
        evaluate_candidate=lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("stale Gamma authority must block before evaluator")
        ),
    )

    cycle_runtime.execute_discovery_phase(
        conn,
        clob=None,
        portfolio=PortfolioState(),
        artifact=artifact,
        tracker=StrategyTracker(),
        limits=types.SimpleNamespace(),
        mode=DiscoveryMode.OPENING_HUNT,
        summary=summary,
        entry_bankroll=100.0,
        decision_time=datetime(2026, 4, 3, tzinfo=timezone.utc),
        env="live",
        deps=deps,
    )

    row = conn.execute(
        """
        SELECT scope_type, scope_key, failure_type
        FROM availability_fact
        WHERE scope_type = 'city_target'
        ORDER BY availability_id DESC
        LIMIT 1
        """
    ).fetchone()
    conn.close()

    assert summary["market_scan_authority"] == "STALE"
    assert summary["forward_market_substrate_status"] == "refused_degraded_authority"
    assert summary["candidates"] == 0
    assert summary["no_trades"] == 1
    assert artifact.no_trade_cases[0].rejection_stage == "MARKET_FILTER"
    assert artifact.no_trade_cases[0].availability_status == "DATA_STALE"
    assert artifact.no_trade_cases[0].rejection_reasons == ["market_scan_authority=STALE"]
    assert row["scope_type"] == "city_target"
    assert row["scope_key"] == "NYC:2026-04-01"
    assert row["failure_type"] == "data_stale"


def test_discovery_phase_blocks_empty_fallback_market_scan_before_evaluator(tmp_path):
    conn = get_connection(tmp_path / "scan-empty-fallback.db")
    init_schema(conn)
    artifact = CycleArtifact(mode=DiscoveryMode.OPENING_HUNT.value, started_at="2026-04-03T00:00:00Z")
    summary = {"candidates": 0, "no_trades": 0}
    market = {
        "city": NYC,
        "target_date": "2026-04-01",
        "hours_since_open": 12.0,
        "hours_to_resolution": 24.0,
        "event_id": "evt-empty-fallback",
        "slug": "slug-empty-fallback",
        "temperature_metric": "high",
        "outcomes": [
            {"title": "39-40°F", "range_low": 39, "range_high": 40},
        ],
    }
    deps = types.SimpleNamespace(
        MODE_PARAMS={DiscoveryMode.OPENING_HUNT: {}},
        find_weather_markets=lambda **kwargs: [market],
        get_last_scan_authority=lambda: "EMPTY_FALLBACK",
        DiscoveryMode=DiscoveryMode,
        logger=types.SimpleNamespace(warning=lambda *args, **kwargs: None),
        NoTradeCase=NoTradeCase,
        evaluate_candidate=lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("keyword fallback authority must block before evaluator")
        ),
    )

    cycle_runtime.execute_discovery_phase(
        conn,
        clob=None,
        portfolio=PortfolioState(),
        artifact=artifact,
        tracker=StrategyTracker(),
        limits=types.SimpleNamespace(),
        mode=DiscoveryMode.OPENING_HUNT,
        summary=summary,
        entry_bankroll=100.0,
        decision_time=datetime(2026, 4, 3, tzinfo=timezone.utc),
        env="live",
        deps=deps,
    )

    row = conn.execute(
        """
        SELECT scope_type, scope_key, failure_type
        FROM availability_fact
        WHERE scope_type = 'city_target'
        ORDER BY availability_id DESC
        LIMIT 1
        """
    ).fetchone()
    conn.close()

    assert summary["market_scan_authority"] == "EMPTY_FALLBACK"
    assert summary["forward_market_substrate_status"] == "refused_degraded_authority"
    assert summary["candidates"] == 0
    assert summary["no_trades"] == 1
    assert artifact.no_trade_cases[0].rejection_stage == "MARKET_FILTER"
    assert artifact.no_trade_cases[0].availability_status == "DATA_UNAVAILABLE"
    assert artifact.no_trade_cases[0].rejection_reasons == ["market_scan_authority=EMPTY_FALLBACK"]
    assert row["scope_type"] == "city_target"
    assert row["scope_key"] == "NYC:2026-04-01"
    assert row["failure_type"] == "data_unavailable"


@pytest.mark.parametrize(
    "authority_getter",
    [
        lambda: "NEVER_FETCHED",
        None,
    ],
)
def test_discovery_phase_blocks_unverified_market_scan_authority_before_evaluator(
    tmp_path,
    authority_getter,
):
    conn = get_connection(tmp_path / "scan-never-fetched.db")
    init_schema(conn)
    artifact = CycleArtifact(mode=DiscoveryMode.OPENING_HUNT.value, started_at="2026-04-03T00:00:00Z")
    summary = {"candidates": 0, "no_trades": 0}
    market = {
        "city": NYC,
        "target_date": "2026-04-01",
        "hours_since_open": 12.0,
        "hours_to_resolution": 24.0,
        "event_id": "evt-never-fetched",
        "slug": "slug-never-fetched",
        "temperature_metric": "high",
        "outcomes": [
            {"title": "39-40°F", "range_low": 39, "range_high": 40},
        ],
    }
    deps_kwargs = {
        "MODE_PARAMS": {DiscoveryMode.OPENING_HUNT: {}},
        "find_weather_markets": lambda **kwargs: [market],
        "DiscoveryMode": DiscoveryMode,
        "logger": types.SimpleNamespace(warning=lambda *args, **kwargs: None),
        "NoTradeCase": NoTradeCase,
        "evaluate_candidate": lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("unverified Gamma authority must block before evaluator")
        ),
    }
    if authority_getter is not None:
        deps_kwargs["get_last_scan_authority"] = authority_getter
    deps = types.SimpleNamespace(**deps_kwargs)

    cycle_runtime.execute_discovery_phase(
        conn,
        clob=None,
        portfolio=PortfolioState(),
        artifact=artifact,
        tracker=StrategyTracker(),
        limits=types.SimpleNamespace(),
        mode=DiscoveryMode.OPENING_HUNT,
        summary=summary,
        entry_bankroll=100.0,
        decision_time=datetime(2026, 4, 3, tzinfo=timezone.utc),
        env="live",
        deps=deps,
    )
    conn.close()

    assert summary["market_scan_authority"] == "NEVER_FETCHED"
    assert summary["forward_market_substrate_status"] == "refused_degraded_authority"
    assert summary["candidates"] == 0
    assert summary["no_trades"] == 1
    assert artifact.no_trade_cases[0].rejection_stage == "MARKET_FILTER"
    assert artifact.no_trade_cases[0].availability_status == "DATA_UNAVAILABLE"
    assert artifact.no_trade_cases[0].rejection_reasons == ["market_scan_authority=NEVER_FETCHED"]


def test_discovery_phase_writes_verified_forward_market_substrate_before_evaluator(tmp_path):
    db_path = tmp_path / "forward-substrate-runtime.db"
    conn = get_connection(db_path)
    init_schema(conn)
    apply_v2_schema(conn)
    conn.commit()
    artifact = CycleArtifact(mode=DiscoveryMode.OPENING_HUNT.value, started_at="2026-04-03T00:00:00Z")
    summary = {"candidates": 0, "no_trades": 0}
    decision_time = datetime(2026, 4, 3, 6, 0, tzinfo=timezone.utc)
    market = {
        "city": NYC,
        "target_date": "2026-04-01",
        "hours_since_open": 12.0,
        "hours_to_resolution": 24.0,
        "event_id": "evt-forward-substrate",
        "slug": "slug-forward-substrate",
        "temperature_metric": "high",
        "outcomes": [
            {
                "condition_id": "cond-forward-substrate",
                "title": "39-40°F",
                "range_low": 39,
                "range_high": 40,
                "token_id": "yes-forward-substrate",
                "no_token_id": "no-forward-substrate",
                "price": 0.37,
                "no_price": 0.63,
                "market_start_at": "2026-04-02T00:00:00Z",
            },
        ],
    }
    observed = {}

    def _evaluate_after_forward_write(candidate, conn_arg, *args, **kwargs):
        del candidate, args, kwargs
        observed["same_conn_events"] = conn_arg.execute(
            "SELECT COUNT(*) FROM market_events_v2"
        ).fetchone()[0]
        observed["same_conn_prices"] = conn_arg.execute(
            "SELECT COUNT(*) FROM market_price_history"
        ).fetchone()[0]
        read_conn = get_connection(db_path)
        try:
            observed["external_events_before_commit"] = read_conn.execute(
                "SELECT COUNT(*) FROM market_events_v2"
            ).fetchone()[0]
            observed["external_prices_before_commit"] = read_conn.execute(
                "SELECT COUNT(*) FROM market_price_history"
            ).fetchone()[0]
        finally:
            read_conn.close()
        return []

    deps = types.SimpleNamespace(
        MODE_PARAMS={DiscoveryMode.OPENING_HUNT: {}},
        find_weather_markets=lambda **kwargs: [market],
        get_last_scan_authority=lambda: "VERIFIED",
        DiscoveryMode=DiscoveryMode,
        logger=types.SimpleNamespace(warning=lambda *args, **kwargs: None, error=lambda *args, **kwargs: None),
        NoTradeCase=NoTradeCase,
        MarketCandidate=MarketCandidate,
        evaluate_candidate=_evaluate_after_forward_write,
    )

    cycle_runtime.execute_discovery_phase(
        conn,
        clob=types.SimpleNamespace(),
        portfolio=PortfolioState(),
        artifact=artifact,
        tracker=StrategyTracker(),
        limits=types.SimpleNamespace(),
        mode=DiscoveryMode.OPENING_HUNT,
        summary=summary,
        entry_bankroll=100.0,
        decision_time=decision_time,
        env="live",
        deps=deps,
    )

    readiness = check_economics_readiness(conn)
    conn.close()

    assert observed == {
        "same_conn_events": 1,
        "same_conn_prices": 2,
        "external_events_before_commit": 0,
        "external_prices_before_commit": 0,
    }
    assert summary["forward_market_substrate_status"] == "written"
    assert summary["forward_market_substrate_market_events_inserted"] == 1
    assert summary["forward_market_substrate_price_rows_inserted"] == 2
    assert not summary.get("degraded", False)
    assert not readiness.ready
    assert "economics_engine_not_implemented" in readiness.blockers


def test_discovery_phase_forward_market_substrate_missing_schema_is_nonblocking(tmp_path):
    conn = get_connection(tmp_path / "forward-substrate-missing-schema.db")
    artifact = CycleArtifact(mode=DiscoveryMode.OPENING_HUNT.value, started_at="2026-04-03T00:00:00Z")
    summary = {"candidates": 0, "no_trades": 0}
    market = {
        "city": NYC,
        "target_date": "2026-04-01",
        "hours_since_open": 12.0,
        "hours_to_resolution": 24.0,
        "event_id": "evt-forward-substrate-missing-schema",
        "slug": "slug-forward-substrate-missing-schema",
        "temperature_metric": "high",
        "outcomes": [],
    }
    deps = types.SimpleNamespace(
        MODE_PARAMS={DiscoveryMode.OPENING_HUNT: {}},
        find_weather_markets=lambda **kwargs: [market],
        get_last_scan_authority=lambda: "VERIFIED",
        DiscoveryMode=DiscoveryMode,
        logger=types.SimpleNamespace(warning=lambda *args, **kwargs: None, error=lambda *args, **kwargs: None),
        NoTradeCase=NoTradeCase,
        MarketCandidate=MarketCandidate,
        evaluate_candidate=lambda *args, **kwargs: [],
    )

    cycle_runtime.execute_discovery_phase(
        conn,
        clob=None,
        portfolio=PortfolioState(),
        artifact=artifact,
        tracker=StrategyTracker(),
        limits=types.SimpleNamespace(),
        mode=DiscoveryMode.OPENING_HUNT,
        summary=summary,
        entry_bankroll=100.0,
        decision_time=datetime(2026, 4, 3, tzinfo=timezone.utc),
        env="live",
        deps=deps,
    )
    conn.close()

    assert summary["forward_market_substrate_status"] == "skipped_missing_tables"
    assert summary["candidates"] == 1
    assert not summary.get("degraded", False)


def test_discovery_phase_forward_market_substrate_invalid_schema_degrades(tmp_path):
    conn = get_connection(tmp_path / "forward-substrate-invalid-schema.db")
    conn.execute("CREATE TABLE market_events_v2 (market_slug TEXT)")
    conn.execute("CREATE TABLE market_price_history (token_id TEXT)")
    conn.commit()
    artifact = CycleArtifact(mode=DiscoveryMode.OPENING_HUNT.value, started_at="2026-04-03T00:00:00Z")
    summary = {"candidates": 0, "no_trades": 0}
    market = {
        "city": NYC,
        "target_date": "2026-04-01",
        "hours_since_open": 12.0,
        "hours_to_resolution": 24.0,
        "event_id": "evt-forward-substrate-invalid-schema",
        "slug": "slug-forward-substrate-invalid-schema",
        "temperature_metric": "high",
        "outcomes": [],
    }
    deps = types.SimpleNamespace(
        MODE_PARAMS={DiscoveryMode.OPENING_HUNT: {}},
        find_weather_markets=lambda **kwargs: [market],
        get_last_scan_authority=lambda: "VERIFIED",
        DiscoveryMode=DiscoveryMode,
        logger=types.SimpleNamespace(warning=lambda *args, **kwargs: None, error=lambda *args, **kwargs: None),
        NoTradeCase=NoTradeCase,
        MarketCandidate=MarketCandidate,
        evaluate_candidate=lambda *args, **kwargs: [],
    )

    cycle_runtime.execute_discovery_phase(
        conn,
        clob=None,
        portfolio=PortfolioState(),
        artifact=artifact,
        tracker=StrategyTracker(),
        limits=types.SimpleNamespace(),
        mode=DiscoveryMode.OPENING_HUNT,
        summary=summary,
        entry_bankroll=100.0,
        decision_time=datetime(2026, 4, 3, tzinfo=timezone.utc),
        env="live",
        deps=deps,
    )
    conn.close()

    assert summary["forward_market_substrate_status"] == "skipped_invalid_schema"
    assert summary["degraded"] is True
    assert summary["candidates"] == 1


def test_live_entry_requires_executable_market_identity_before_intent(tmp_path):
    conn = get_connection(tmp_path / "missing-executable-identity.db")
    init_schema(conn)
    artifact = CycleArtifact(mode=DiscoveryMode.OPENING_HUNT.value, started_at="2026-04-03T00:00:00Z")
    summary = {"candidates": 0, "no_trades": 0}
    market = {
        "city": NYC,
        "target_date": "2026-04-01",
        "hours_since_open": 12.0,
        "hours_to_resolution": 24.0,
        "event_id": "evt-no-snapshot",
        "slug": "slug-no-snapshot",
        "temperature_metric": "high",
        "outcomes": [
            {
                "title": "39-40°F",
                "range_low": 39,
                "range_high": 40,
                "token_id": "yes1",
                "no_token_id": "no1",
                "market_id": "m1",
            },
        ],
    }
    decision = EdgeDecision(
        should_trade=True,
        edge=_edge(),
        tokens={"market_id": "m1", "token_id": "yes1", "no_token_id": "no1"},
        size_usd=5.0,
        decision_id="d-missing-exec",
        selected_method="ens_member_counting",
        applied_validations=["ens_fetch"],
        decision_snapshot_id="model-snap-1",
        edge_source="opening_inertia",
        strategy_key="opening_inertia",
        edge_context=None,
    )
    deps = types.SimpleNamespace(
        MODE_PARAMS={DiscoveryMode.OPENING_HUNT: {}},
        find_weather_markets=lambda **kwargs: [market],
        get_last_scan_authority=lambda: "VERIFIED",
        DiscoveryMode=DiscoveryMode,
        logger=types.SimpleNamespace(warning=lambda *args, **kwargs: None, error=lambda *args, **kwargs: None),
        NoTradeCase=NoTradeCase,
        MarketCandidate=MarketCandidate,
        evaluate_candidate=lambda *args, **kwargs: [decision],
        is_strategy_enabled=lambda _strategy: True,
        _classify_edge_source=lambda _mode, _edge: "opening_inertia",
        create_execution_intent=lambda **kwargs: (_ for _ in ()).throw(
            AssertionError("missing executable identity must block before intent")
        ),
        execute_intent=lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("missing executable identity must block before submit")
        ),
    )

    cycle_runtime.execute_discovery_phase(
        conn,
        clob=None,
        portfolio=PortfolioState(),
        artifact=artifact,
        tracker=StrategyTracker(),
        limits=types.SimpleNamespace(),
        mode=DiscoveryMode.OPENING_HUNT,
        summary=summary,
        entry_bankroll=100.0,
        decision_time=datetime(2026, 4, 3, tzinfo=timezone.utc),
        env="live",
        deps=deps,
    )
    conn.close()

    assert summary["no_trades"] == 1
    case = artifact.no_trade_cases[0]
    assert case.rejection_stage == "EXECUTION_FAILED"
    assert case.rejection_reasons == [
        "missing_executable_market_identity:"
        "executable_snapshot_id,executable_snapshot_min_tick_size,"
        "executable_snapshot_min_order_size,executable_snapshot_neg_risk"
    ]


def test_live_entry_snapshot_capture_failure_blocks_before_intent(tmp_path):
    conn = get_connection(tmp_path / "capture-failure-blocks-intent.db")
    init_schema(conn)
    artifact = CycleArtifact(mode=DiscoveryMode.OPENING_HUNT.value, started_at="2026-04-03T00:00:00Z")
    summary = {"candidates": 0, "no_trades": 0}
    market = {
        "city": NYC,
        "target_date": "2026-04-01",
        "hours_since_open": 12.0,
        "hours_to_resolution": 24.0,
        "event_id": "evt-capture-failure",
        "slug": "slug-capture-failure",
        "temperature_metric": "high",
        "outcomes": [
            {
                "title": "39-40°F",
                "range_low": 39,
                "range_high": 40,
                "token_id": "yes1",
                "no_token_id": "no1",
                "market_id": "m1",
                "condition_id": "m1",
                "question_id": "q1",
            },
        ],
    }
    decision = EdgeDecision(
        should_trade=True,
        edge=_edge(),
        tokens={"market_id": "m1", "token_id": "yes1", "no_token_id": "no1"},
        size_usd=5.0,
        decision_id="d-capture-failure",
        selected_method="ens_member_counting",
        applied_validations=["ens_fetch"],
        decision_snapshot_id="model-snap-1",
        edge_source="opening_inertia",
        strategy_key="opening_inertia",
        edge_context=None,
    )
    deps = types.SimpleNamespace(
        MODE_PARAMS={DiscoveryMode.OPENING_HUNT: {}},
        find_weather_markets=lambda **kwargs: [market],
        get_last_scan_authority=lambda: "VERIFIED",
        capture_executable_market_snapshot=lambda *args, **kwargs: (_ for _ in ()).throw(
            RuntimeError("CLOB market info missing")
        ),
        DiscoveryMode=DiscoveryMode,
        logger=types.SimpleNamespace(warning=lambda *args, **kwargs: None, error=lambda *args, **kwargs: None),
        NoTradeCase=NoTradeCase,
        MarketCandidate=MarketCandidate,
        evaluate_candidate=lambda *args, **kwargs: [decision],
        is_strategy_enabled=lambda _strategy: True,
        _classify_edge_source=lambda _mode, _edge: "opening_inertia",
        create_execution_intent=lambda **kwargs: (_ for _ in ()).throw(
            AssertionError("snapshot capture failure must block before intent")
        ),
        execute_intent=lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("snapshot capture failure must block before submit")
        ),
    )

    cycle_runtime.execute_discovery_phase(
        conn,
        clob=types.SimpleNamespace(),
        portfolio=PortfolioState(),
        artifact=artifact,
        tracker=StrategyTracker(),
        limits=types.SimpleNamespace(),
        mode=DiscoveryMode.OPENING_HUNT,
        summary=summary,
        entry_bankroll=100.0,
        decision_time=datetime(2026, 4, 3, tzinfo=timezone.utc),
        env="live",
        deps=deps,
    )
    conn.close()

    case = artifact.no_trade_cases[0]
    assert case.rejection_stage == "EXECUTION_FAILED"
    assert case.rejection_reasons[0].startswith("missing_executable_market_identity:")
    assert case.rejection_reasons[1] == "executable_snapshot_capture_failed:CLOB market info missing"


@pytest.mark.parametrize("status", ["inserted", "unchanged"])
def test_forward_price_linkage_success_statuses_do_not_degrade_cycle(status):
    assert cycle_runtime._forward_price_linkage_status_degraded(status) is False


@pytest.mark.parametrize(
    "status",
    [
        "",
        "conflict",
        "skipped_invalid_schema",
        "skipped_missing_tables",
        "skipped_no_connection",
        "refused_missing_snapshot_id",
        "refused_missing_snapshot",
        "refused_missing_snapshot_facts",
        "refused_crossed_orderbook",
    ],
)
def test_forward_price_linkage_non_success_statuses_degrade_cycle(status):
    assert cycle_runtime._forward_price_linkage_status_degraded(status) is True


def test_live_entry_final_intent_receives_executable_snapshot_fields(tmp_path):
    conn = get_connection(tmp_path / "thread-executable-identity.db")
    init_schema(conn)
    artifact = CycleArtifact(mode=DiscoveryMode.UPDATE_REACTION.value, started_at="2026-04-03T00:00:00Z")
    summary = {"candidates": 0, "no_trades": 0}
    captured = {}
    decision_time = datetime(2026, 4, 3, 2, 0, tzinfo=timezone.utc)
    forecast_context = {
        "forecast_source_id": "tigge",
        "model_family": "ecmwf_ifs025",
        "forecast_issue_time": "2026-04-03T00:00:00+00:00",
        "forecast_valid_time": "2026-04-03T06:00:00+00:00",
        "forecast_fetch_time": "2026-04-03T01:00:00+00:00",
        "forecast_available_at": "2026-04-03T00:30:00+00:00",
        "raw_payload_hash": "a" * 64,
        "degradation_level": "OK",
        "forecast_source_role": "entry_primary",
        "authority_tier": "FORECAST",
        "decision_time": decision_time.isoformat(),
        "decision_time_status": "OK",
    }
    market = {
        "city": NYC,
        "target_date": "2026-04-01",
        "hours_since_open": 12.0,
        "hours_to_resolution": 24.0,
        "event_id": "evt-snapshot",
        "slug": "slug-snapshot",
        "temperature_metric": "high",
        "outcomes": [
            {
                "title": "39-40°F",
                "range_low": 39,
                "range_high": 40,
                "token_id": "yes1",
                "no_token_id": "no1",
                "market_id": "m1",
            },
        ],
    }
    _insert_executable_snapshot(
        conn,
        snapshot_id="snap-entry-1",
        selected_outcome_token_id="yes1",
        outcome_label="YES",
        yes_token_id="yes1",
        no_token_id="no1",
        event_id="evt-snapshot",
        condition_id="m1",
        top_bid="0.25",
        top_ask="0.25",
    )
    decision = EdgeDecision(
        should_trade=True,
        edge=_edge(),
        tokens={
            "market_id": "m1",
            "token_id": "yes1",
            "no_token_id": "no1",
            "executable_snapshot_id": "snap-entry-1",
            "executable_snapshot_min_tick_size": "0.01",
            "executable_snapshot_min_order_size": "5",
            "executable_snapshot_neg_risk": False,
        },
        size_usd=5.0,
        decision_id="d-exec-thread",
        selected_method="ens_member_counting",
        applied_validations=["ens_fetch"],
        decision_snapshot_id="model-snap-1",
        edge_source="center_buy",
        strategy_key="center_buy",
        edge_context=None,
        epistemic_context_json=json.dumps({"forecast_context": forecast_context}),
        sizing_bankroll=100.0,
        kelly_multiplier_used=0.25,
        execution_fee_rate=0.0,
        safety_cap_usd=None,
    )

    def _capture_final_intent(intent, **kwargs):
        captured["intent"] = intent
        captured["kwargs"] = kwargs
        return OrderResult(
            status="pending",
            trade_id="trade-1",
            order_id="ord-1",
            submitted_price=float(intent.final_limit_price),
            shares=10.0,
            command_state="ACKED",
        )

    deps = types.SimpleNamespace(
        MODE_PARAMS={DiscoveryMode.UPDATE_REACTION: {}},
        find_weather_markets=lambda **kwargs: [market],
        get_last_scan_authority=lambda: "VERIFIED",
        DiscoveryMode=DiscoveryMode,
        logger=types.SimpleNamespace(warning=lambda *args, **kwargs: None, error=lambda *args, **kwargs: None),
        NoTradeCase=NoTradeCase,
        MarketCandidate=MarketCandidate,
        evaluate_candidate=lambda *args, **kwargs: [decision],
        is_strategy_enabled=lambda _strategy: True,
        _classify_edge_source=lambda _mode, _edge: "center_buy",
        create_execution_intent=lambda **kwargs: (_ for _ in ()).throw(
            AssertionError("live final-intent path must not create legacy intent")
        ),
        execute_intent=lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("live final-intent path must not submit legacy intent")
        ),
        execute_final_intent=_capture_final_intent,
        select_final_order_type=lambda conn, snapshot_id: "FOK",
    )

    cycle_runtime.execute_discovery_phase(
        conn,
        clob=None,
        portfolio=PortfolioState(),
        artifact=artifact,
        tracker=StrategyTracker(),
        limits=types.SimpleNamespace(),
        mode=DiscoveryMode.UPDATE_REACTION,
        summary=summary,
        entry_bankroll=100.0,
        decision_time=decision_time,
        env="live",
        deps=deps,
    )
    conn.close()

    final_intent = captured["intent"]
    assert final_intent.snapshot_id == "snap-entry-1"
    assert final_intent.tick_size == Decimal("0.01")
    assert final_intent.min_order_size == Decimal("5")
    assert final_intent.neg_risk is False
    assert final_intent.order_type == "FOK"
    assert final_intent.cancel_after == decision_time + timedelta(seconds=60 * 60)
    assert final_intent.event_id == "evt-snapshot"
    assert final_intent.resolution_window == "2026-04-01"
    assert final_intent.correlation_key == "NYC:2026-04-01"
    assert captured["kwargs"]["conn"] is conn
    assert captured["kwargs"]["snapshot_conn"] is conn
    decision_source_context = final_intent.decision_source_context
    assert decision_source_context.source_id == "tigge"
    assert decision_source_context.model_family == "ecmwf_ifs025"
    assert decision_source_context.integrity_errors() == ()


def test_executable_snapshot_repricing_updates_edge_and_size(tmp_path):
    conn = get_connection(tmp_path / "snapshot-reprice.db")
    init_schema(conn)
    _insert_executable_snapshot(
        conn,
        snapshot_id="snap-reprice-1",
        selected_outcome_token_id="yes1",
        outcome_label="YES",
        yes_token_id="yes1",
        no_token_id="no1",
        top_bid="0.20",
        top_ask="0.30",
    )
    edge = _edge()
    decision = EdgeDecision(
        should_trade=True,
        edge=edge,
        tokens={"token_id": "yes1", "no_token_id": "no1"},
        size_usd=5.0,
        decision_snapshot_id="decision-snap",
        applied_validations=[],
        edge_context=EdgeContext(
            p_raw=np.array([0.5, 0.5]),
            p_cal=np.array([0.5, 0.5]),
            p_market=np.array([0.35]),
            p_posterior=edge.p_posterior,
            forward_edge=edge.forward_edge,
            alpha=1.0,
            confidence_band_upper=edge.ci_upper,
            confidence_band_lower=edge.ci_lower,
            entry_provenance=EntryMethod.ENS_MEMBER_COUNTING,
            decision_snapshot_id="decision-snap",
            n_edges_found=1,
            n_edges_after_fdr=1,
        ),
        edge_context_json=json.dumps({"forward_edge": edge.forward_edge, "p_posterior": edge.p_posterior}),
        sizing_bankroll=100.0,
        kelly_multiplier_used=0.25,
        execution_fee_rate=0.0,
        safety_cap_usd=None,
    )

    best_ask = cycle_runtime._reprice_decision_from_executable_snapshot(
        conn,
        decision,
        {"executable_snapshot_id": "snap-reprice-1"},
    )
    conn.close()

    assert best_ask is None
    assert decision.edge.vwmp == pytest.approx(0.25)
    assert decision.edge.entry_price == pytest.approx(0.25)
    assert decision.edge.edge == pytest.approx(0.22)
    assert decision.edge_context.forward_edge == pytest.approx(0.22)
    assert json.loads(decision.edge_context_json)["forward_edge"] == pytest.approx(0.22)
    assert decision.size_usd == pytest.approx((0.47 - 0.25) / (1 - 0.25) * 0.25 * 100.0)
    assert "executable_snapshot_repriced" in decision.applied_validations
    assert "corrected_pricing_shadow_built" in decision.applied_validations
    reprice = decision.tokens["executable_snapshot_reprice"]
    assert reprice["snapshot_id"] == "snap-reprice-1"
    assert reprice["snapshot_vwmp"] == pytest.approx(0.25)
    assert reprice["final_limit_price"] == pytest.approx(0.23)
    assert reprice["best_ask_blocked_by_slippage"] is True
    assert reprice["corrected_candidate_limit_price"] == pytest.approx(0.23)
    assert reprice["repriced_size_usd"] == pytest.approx(decision.size_usd)
    shadow = reprice["corrected_pricing_shadow"]
    assert shadow["shadow_only"] is True
    assert shadow["live_submit_authority"] is False
    assert shadow["field_semantics"] == "passive_limit_requires_maker_only_support"
    assert shadow["order_policy"] == "post_only_passive_limit"
    assert shadow["selected_token_id"] == "yes1"
    assert shadow["direction"] == "buy_yes"
    assert shadow["snapshot_id"] == "snap-reprice-1"
    assert shadow["snapshot_hash"] == reprice["executable_snapshot_hash"]
    assert shadow["snapshot_hash"] != reprice["raw_orderbook_hash"]
    assert shadow["candidate_final_limit_price"] == "0.23"
    assert shadow["candidate_fee_adjusted_execution_price"] == "0.23"
    assert shadow["sweep_attempted"] is False
    assert shadow["sweep_depth_status"] == "NOT_MARKETABLE_PASSIVE_LIMIT"
    assert shadow["unsupported_reason"] == "PASSIVE_LIMIT_REQUIRES_POST_ONLY_OR_MAKER_ONLY_SUBMIT"
    assert shadow["cost_basis_hash"]
    assert shadow["posterior_distribution_id"] == "decision_snapshot:decision-snap"


def test_executable_snapshot_repricing_rejects_stale_snapshot(tmp_path):
    conn = get_connection(tmp_path / "snapshot-stale.db")
    init_schema(conn)
    _insert_executable_snapshot(
        conn,
        snapshot_id="snap-stale",
        selected_outcome_token_id="yes1",
        outcome_label="YES",
        yes_token_id="yes1",
        no_token_id="no1",
        top_bid="0.20",
        top_ask="0.30",
        captured_at=datetime.now(timezone.utc) - timedelta(seconds=10),
    )
    decision = EdgeDecision(
        should_trade=True,
        edge=_edge(),
        tokens={"token_id": "yes1", "no_token_id": "no1"},
        size_usd=5.0,
        sizing_bankroll=100.0,
        kelly_multiplier_used=0.25,
        execution_fee_rate=0.0,
    )

    with pytest.raises(ValueError, match="executable_snapshot_stale"):
        cycle_runtime._reprice_decision_from_executable_snapshot(
            conn,
            decision,
            {"executable_snapshot_id": "snap-stale"},
        )
    conn.close()


def test_executable_snapshot_repricing_can_cross_ask_inside_slippage_budget(tmp_path):
    conn = get_connection(tmp_path / "snapshot-reprice-tight-ask.db")
    init_schema(conn)
    _insert_executable_snapshot(
        conn,
        snapshot_id="snap-reprice-tight-ask",
        selected_outcome_token_id="yes1",
        outcome_label="YES",
        yes_token_id="yes1",
        no_token_id="no1",
        top_bid="0.40",
        top_ask="0.41",
    )
    edge = _edge()
    decision = EdgeDecision(
        should_trade=True,
        edge=edge,
        tokens={"token_id": "yes1", "no_token_id": "no1"},
        size_usd=5.0,
        applied_validations=[],
        edge_context=types.SimpleNamespace(p_posterior=edge.p_posterior),
        decision_snapshot_id="decision-snap-tight-ask",
        sizing_bankroll=100.0,
        kelly_multiplier_used=0.25,
        execution_fee_rate=0.0,
        safety_cap_usd=None,
    )

    best_ask = cycle_runtime._reprice_decision_from_executable_snapshot(
        conn,
        decision,
        {"executable_snapshot_id": "snap-reprice-tight-ask"},
    )
    conn.close()

    assert best_ask == pytest.approx(0.41)
    assert decision.edge.vwmp == pytest.approx(0.405)
    assert decision.size_usd == pytest.approx((0.47 - 0.41) / (1 - 0.41) * 0.25 * 100.0)
    reprice = decision.tokens["executable_snapshot_reprice"]
    assert reprice["best_ask_slippage_bps"] == pytest.approx((0.41 - 0.405) / 0.405 * 10_000.0)
    assert reprice["best_ask_blocked_by_slippage"] is False
    assert reprice["final_limit_price"] == pytest.approx(0.41)
    assert reprice["corrected_pricing_shadow"]["candidate_final_limit_price"] == "0.41"
    assert reprice["corrected_pricing_shadow"]["candidate_fee_adjusted_execution_price"] == "0.41"
    assert reprice["corrected_pricing_shadow"]["sweep_attempted"] is True
    assert reprice["corrected_pricing_shadow"]["sweep_depth_status"] == "PASS"
    assert reprice["corrected_pricing_shadow"]["sweep_book_side"] == "asks"
    assert reprice["corrected_pricing_shadow"]["order_policy"] == "marketable_limit_depth_bound"
    assert reprice["corrected_pricing_shadow"]["live_submit_authority"] is False
    assert (
        reprice["corrected_pricing_shadow"]["unsupported_reason"]
        == "MARKETABLE_FINAL_INTENT_REQUIRES_IMMEDIATE_ORDER_TYPE"
    )
    assert getattr(decision, "final_execution_intent", None) is None


def test_executable_snapshot_repricing_sweeps_deeper_ask_inside_budget(tmp_path):
    from dataclasses import replace

    conn = get_connection(tmp_path / "snapshot-reprice-deeper-ask.db")
    init_schema(conn)
    _insert_executable_snapshot(
        conn,
        snapshot_id="snap-reprice-deeper-ask",
        selected_outcome_token_id="yes1",
        outcome_label="YES",
        yes_token_id="yes1",
        no_token_id="no1",
        top_bid="0.59",
        top_ask="0.60",
        bid_size="100",
        ask_size="1",
        orderbook_depth={
            "bids": [{"price": "0.59", "size": "100"}],
            "asks": [
                {"price": "0.60", "size": "1"},
                {"price": "0.61", "size": "100"},
            ],
        },
    )
    edge = replace(
        _edge(),
        edge=0.10,
        p_model=0.70,
        p_market=0.60,
        p_posterior=0.70,
        entry_price=0.60,
        vwmp=0.60,
        forward_edge=0.10,
    )
    decision = EdgeDecision(
        should_trade=True,
        edge=edge,
        tokens={"token_id": "yes1", "no_token_id": "no1"},
        size_usd=5.0,
        applied_validations=[],
        edge_context=types.SimpleNamespace(p_posterior=edge.p_posterior),
        decision_snapshot_id="decision-snap-deeper-ask",
        sizing_bankroll=100.0,
        kelly_multiplier_used=0.25,
        execution_fee_rate=0.0,
        safety_cap_usd=None,
    )

    best_ask = cycle_runtime._reprice_decision_from_executable_snapshot(
        conn,
        decision,
        {"executable_snapshot_id": "snap-reprice-deeper-ask"},
        {
            "order_type": "FOK",
            "cancel_after": datetime(2026, 4, 3, 1, tzinfo=timezone.utc),
            "resolution_window": "2026-04-03",
            "correlation_key": "NYC:2026-04-03",
        },
    )
    conn.close()

    expected_size = (0.70 - 0.61) / (1 - 0.61) * 0.25 * 100.0
    assert best_ask == pytest.approx(0.61)
    reprice = decision.tokens["executable_snapshot_reprice"]
    shadow = reprice["corrected_pricing_shadow"]
    assert decision.size_usd == pytest.approx(float(shadow["candidate_size_usd"]))
    assert decision.size_usd >= expected_size
    assert reprice["depth_sweep_limit_price"] == pytest.approx(0.61)
    assert reprice["corrected_candidate_limit_price"] == pytest.approx(0.61)
    assert reprice["live_submit_authority"] is True
    assert shadow["sweep_attempted"] is True
    assert shadow["sweep_depth_status"] == "PASS"
    assert shadow["order_policy"] == "marketable_limit_depth_bound"
    assert shadow["sweep_levels_consumed"] == 2
    assert float(shadow["sweep_average_price"]) < 0.61
    assert shadow["candidate_size_kind"] == "shares"
    assert shadow["candidate_submitted_shares"] == shadow["candidate_size_value"]
    assert decision.final_execution_intent.order_type == "FOK"
    assert decision.final_execution_intent.order_policy == "marketable_limit_depth_bound"
    assert decision.final_execution_intent.submitted_shares == Decimal(
        shadow["candidate_submitted_shares"]
    )


def test_live_multibin_buy_no_requires_live_feature_flag(monkeypatch, tmp_path):
    from dataclasses import replace

    _set_native_multibin_buy_no_flags(monkeypatch, shadow=True, live=False)
    conn = get_connection(tmp_path / "live-buy-no-flag.db")
    init_schema(conn)
    artifact = CycleArtifact(mode=DiscoveryMode.UPDATE_REACTION.value, started_at="2026-04-03T00:00:00Z")
    summary = {"candidates": 0, "no_trades": 0}
    market = {
        "city": NYC,
        "target_date": "2026-04-01",
        "hours_since_open": 12.0,
        "hours_to_resolution": 24.0,
        "event_id": "evt-buy-no-flag",
        "slug": "slug-buy-no-flag",
        "temperature_metric": "high",
        "outcomes": [
            {"title": "39°F or lower", "range_low": None, "range_high": 39, "token_id": "yes0", "no_token_id": "no0", "market_id": "m0"},
            {"title": "40-41°F", "range_low": 40, "range_high": 41, "token_id": "yes1", "no_token_id": "no1", "market_id": "m1"},
            {"title": "42°F or higher", "range_low": 42, "range_high": None, "token_id": "yes2", "no_token_id": "no2", "market_id": "m2"},
        ],
    }
    buy_no_edge = replace(
        _edge(),
        bin=Bin(low=None, high=39, label="39°F or lower", unit="F"),
        direction="buy_no",
        p_market=0.35,
        entry_price=0.35,
        vwmp=0.35,
        p_posterior=0.62,
        edge=0.27,
        forward_edge=0.27,
    )
    decision = EdgeDecision(
        should_trade=True,
        edge=buy_no_edge,
        tokens={"market_id": "m0", "token_id": "yes0", "no_token_id": "no0"},
        size_usd=5.0,
        decision_id="d-buy-no-live-disabled",
        selected_method="ens_member_counting",
        applied_validations=[evaluator_module.NATIVE_BUY_NO_QUOTE_AVAILABLE_VALIDATION],
        decision_snapshot_id="model-snap-buy-no",
        edge_source="shoulder_sell",
        strategy_key="shoulder_sell",
        settlement_semantics_json='{"measurement_unit":"F"}',
        epistemic_context_json='{"decision_time_utc":"2026-04-01T00:00:00Z"}',
        edge_context_json='{"forward_edge":0.27}',
        sizing_bankroll=100.0,
        kelly_multiplier_used=0.25,
        execution_fee_rate=0.0,
        safety_cap_usd=None,
    )

    deps = types.SimpleNamespace(
        MODE_PARAMS={DiscoveryMode.UPDATE_REACTION: {}},
        find_weather_markets=lambda **kwargs: [market],
        get_last_scan_authority=lambda: "VERIFIED",
        DiscoveryMode=DiscoveryMode,
        logger=types.SimpleNamespace(warning=lambda *args, **kwargs: None, error=lambda *args, **kwargs: None),
        NoTradeCase=NoTradeCase,
        MarketCandidate=MarketCandidate,
        evaluate_candidate=lambda *args, **kwargs: [decision],
        is_strategy_enabled=lambda _strategy: True,
        _classify_edge_source=lambda _mode, _edge: "shoulder_sell",
        create_execution_intent=lambda **kwargs: (_ for _ in ()).throw(AssertionError("should not submit")),
        execute_intent=lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("should not execute")),
    )

    cycle_runtime.execute_discovery_phase(
        conn,
        clob=None,
        portfolio=PortfolioState(),
        artifact=artifact,
        tracker=StrategyTracker(),
        limits=types.SimpleNamespace(),
        mode=DiscoveryMode.UPDATE_REACTION,
        summary=summary,
        entry_bankroll=100.0,
        decision_time=datetime(2026, 4, 3, tzinfo=timezone.utc),
        env="live",
        deps=deps,
    )
    conn.close()

    assert summary["no_trades"] == 1
    assert artifact.no_trade_cases[0].rejection_stage == "RISK_REJECTED"
    assert artifact.no_trade_cases[0].rejection_reasons == [
        "NATIVE_MULTIBIN_BUY_NO_LIVE_DISABLED"
    ]


def test_live_binary_buy_no_requires_native_live_feature_flag(monkeypatch, tmp_path):
    from dataclasses import replace

    _set_native_multibin_buy_no_flags(monkeypatch, shadow=True, live=False)
    conn = get_connection(tmp_path / "live-binary-buy-no-flag.db")
    init_schema(conn)
    artifact = CycleArtifact(mode=DiscoveryMode.UPDATE_REACTION.value, started_at="2026-04-03T00:00:00Z")
    summary = {"candidates": 0, "no_trades": 0}
    market = {
        "city": NYC,
        "target_date": "2026-04-01",
        "hours_since_open": 12.0,
        "hours_to_resolution": 24.0,
        "event_id": "evt-binary-buy-no-flag",
        "slug": "slug-binary-buy-no-flag",
        "temperature_metric": "high",
        "outcomes": [
            {"title": "39°F or lower", "range_low": None, "range_high": 39, "token_id": "yes0", "no_token_id": "no0", "market_id": "m0"},
            {"title": "40°F or higher", "range_low": 40, "range_high": None, "token_id": "yes1", "no_token_id": "no1", "market_id": "m1"},
        ],
    }
    buy_no_edge = replace(
        _edge(),
        bin=Bin(low=None, high=39, label="39°F or lower", unit="F"),
        direction="buy_no",
        p_market=0.35,
        entry_price=0.35,
        vwmp=0.35,
        p_posterior=0.62,
        edge=0.27,
        forward_edge=0.27,
    )
    decision = EdgeDecision(
        should_trade=True,
        edge=buy_no_edge,
        tokens={"market_id": "m0", "token_id": "yes0", "no_token_id": "no0"},
        size_usd=5.0,
        decision_id="d-binary-buy-no-live-disabled",
        selected_method="ens_member_counting",
        applied_validations=[evaluator_module.NATIVE_BUY_NO_QUOTE_AVAILABLE_VALIDATION],
        decision_snapshot_id="model-snap-binary-buy-no",
        edge_source="shoulder_sell",
        strategy_key="shoulder_sell",
        settlement_semantics_json='{"measurement_unit":"F"}',
        epistemic_context_json='{"decision_time_utc":"2026-04-01T00:00:00Z"}',
        edge_context_json='{"forward_edge":0.27}',
        sizing_bankroll=100.0,
        kelly_multiplier_used=0.25,
        execution_fee_rate=0.0,
        safety_cap_usd=None,
    )

    deps = types.SimpleNamespace(
        MODE_PARAMS={DiscoveryMode.UPDATE_REACTION: {}},
        find_weather_markets=lambda **kwargs: [market],
        get_last_scan_authority=lambda: "VERIFIED",
        DiscoveryMode=DiscoveryMode,
        logger=types.SimpleNamespace(warning=lambda *args, **kwargs: None, error=lambda *args, **kwargs: None),
        NoTradeCase=NoTradeCase,
        MarketCandidate=MarketCandidate,
        evaluate_candidate=lambda *args, **kwargs: [decision],
        is_strategy_enabled=lambda _strategy: True,
        _classify_edge_source=lambda _mode, _edge: "shoulder_sell",
        create_execution_intent=lambda **kwargs: (_ for _ in ()).throw(AssertionError("should not submit")),
        execute_intent=lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("should not execute")),
    )

    cycle_runtime.execute_discovery_phase(
        conn,
        clob=None,
        portfolio=PortfolioState(),
        artifact=artifact,
        tracker=StrategyTracker(),
        limits=types.SimpleNamespace(),
        mode=DiscoveryMode.UPDATE_REACTION,
        summary=summary,
        entry_bankroll=100.0,
        decision_time=datetime(2026, 4, 3, tzinfo=timezone.utc),
        env="live",
        deps=deps,
    )
    conn.close()

    assert summary["no_trades"] == 1
    assert artifact.no_trade_cases[0].rejection_stage == "RISK_REJECTED"
    assert artifact.no_trade_cases[0].rejection_reasons == [
        "NATIVE_MULTIBIN_BUY_NO_LIVE_DISABLED"
    ]


def test_live_buy_no_requires_canonical_quote_evidence_even_when_flags_true(monkeypatch, tmp_path):
    _set_native_multibin_buy_no_flags(monkeypatch, shadow=True, live=True)

    summary, artifact = _run_live_buy_no_authorization_case(
        monkeypatch,
        tmp_path,
        mode=DiscoveryMode.DAY0_CAPTURE,
        strategy_key="settlement_capture",
        applied_validations=[],
    )

    assert summary["no_trades"] == 1
    assert summary.get("strategy_phase_rejections", 0) == 0
    assert summary.get("strategy_gate_rejections", 0) == 0
    assert artifact.no_trade_cases[0].rejection_stage == "RISK_REJECTED"
    assert artifact.no_trade_cases[0].strategy == "settlement_capture"
    assert artifact.no_trade_cases[0].rejection_reasons == [
        "NATIVE_BUY_NO_QUOTE_EVIDENCE_MISSING"
    ]


def test_day0_live_buy_no_requires_promotion_even_when_flags_and_quote_true(monkeypatch, tmp_path):
    _set_native_multibin_buy_no_flags(monkeypatch, shadow=True, live=True)

    summary, artifact = _run_live_buy_no_authorization_case(
        monkeypatch,
        tmp_path,
        mode=DiscoveryMode.DAY0_CAPTURE,
        strategy_key="settlement_capture",
        applied_validations=[evaluator_module.NATIVE_BUY_NO_QUOTE_AVAILABLE_VALIDATION],
    )

    assert summary["no_trades"] == 1
    assert summary.get("strategy_phase_rejections", 0) == 0
    assert summary.get("strategy_gate_rejections", 0) == 0
    assert artifact.no_trade_cases[0].rejection_stage == "RISK_REJECTED"
    assert artifact.no_trade_cases[0].strategy == "settlement_capture"
    assert artifact.no_trade_cases[0].rejection_reasons == [
        "NATIVE_BUY_NO_LIVE_PROMOTION_MISSING:settlement_capture:day0_capture:buy_no"
    ]


def test_live_buy_no_approved_context_still_requires_promotion_evidence(monkeypatch, tmp_path):
    _set_native_multibin_buy_no_flags(monkeypatch, shadow=True, live=True)
    monkeypatch.setattr(
        cycle_runtime,
        "NATIVE_BUY_NO_LIVE_APPROVED_CONTEXTS",
        frozenset({("settlement_capture", "day0_capture", "buy_no")}),
    )

    summary, artifact = _run_live_buy_no_authorization_case(
        monkeypatch,
        tmp_path,
        mode=DiscoveryMode.DAY0_CAPTURE,
        strategy_key="settlement_capture",
        applied_validations=[evaluator_module.NATIVE_BUY_NO_QUOTE_AVAILABLE_VALIDATION],
    )

    assert summary["no_trades"] == 1
    assert summary.get("strategy_phase_rejections", 0) == 0
    assert summary.get("strategy_gate_rejections", 0) == 0
    assert artifact.no_trade_cases[0].rejection_stage == "RISK_REJECTED"
    assert artifact.no_trade_cases[0].strategy == "settlement_capture"
    assert artifact.no_trade_cases[0].rejection_reasons == [
        "NATIVE_BUY_NO_LIVE_PROMOTION_EVIDENCE_MISSING"
    ]


def test_executable_snapshot_repricing_uses_native_no_snapshot_for_buy_no(tmp_path):
    from dataclasses import replace

    conn = get_connection(tmp_path / "snapshot-reprice-no.db")
    init_schema(conn)
    _insert_executable_snapshot(
        conn,
        snapshot_id="snap-reprice-no-1",
        selected_outcome_token_id="no1",
        outcome_label="NO",
        yes_token_id="yes1",
        no_token_id="no1",
        top_bid="0.38",
        top_ask="0.42",
    )
    edge = replace(
        _edge(),
        direction="buy_no",
        edge=0.22,
        p_model=0.62,
        p_market=0.40,
        p_posterior=0.62,
        entry_price=0.40,
        vwmp=0.40,
        forward_edge=0.22,
    )
    decision = EdgeDecision(
        should_trade=True,
        edge=edge,
        tokens={"token_id": "yes1", "no_token_id": "no1"},
        size_usd=5.0,
        applied_validations=[],
        edge_context=types.SimpleNamespace(p_posterior=0.62),
        decision_snapshot_id="decision-snap-buy-no-reprice",
        sizing_bankroll=100.0,
        kelly_multiplier_used=0.25,
        execution_fee_rate=0.0,
        safety_cap_usd=None,
    )

    best_ask = cycle_runtime._reprice_decision_from_executable_snapshot(
        conn,
        decision,
        {"executable_snapshot_id": "snap-reprice-no-1"},
    )
    conn.close()

    assert best_ask is None
    assert decision.edge.direction == "buy_no"
    assert decision.edge.vwmp == pytest.approx(0.40)
    assert decision.edge.entry_price == pytest.approx(0.40)
    assert decision.edge.p_market == pytest.approx(0.40)
    assert decision.edge.edge == pytest.approx(0.22)
    assert decision.edge_context.forward_edge == pytest.approx(0.22)
    assert decision.size_usd == pytest.approx((0.62 - 0.40) / (1 - 0.40) * 0.25 * 100.0)
    assert decision.tokens["executable_snapshot_reprice"]["outcome_label"] == "NO"
    assert decision.tokens["executable_snapshot_reprice"]["best_ask_blocked_by_slippage"] is True
    shadow = decision.tokens["executable_snapshot_reprice"]["corrected_pricing_shadow"]
    assert shadow["selected_token_id"] == "no1"
    assert shadow["direction"] == "buy_no"
    assert shadow["snapshot_id"] == "snap-reprice-no-1"
    assert shadow["candidate_final_limit_price"] == "0.38"
    assert shadow["sweep_attempted"] is False
    assert shadow["posterior_distribution_id"] == "decision_snapshot:decision-snap-buy-no-reprice"


def test_executable_snapshot_repricing_does_not_jump_to_negative_edge_ask(tmp_path):
    conn = get_connection(tmp_path / "snapshot-reprice-negative-ask.db")
    init_schema(conn)
    _insert_executable_snapshot(
        conn,
        snapshot_id="snap-reprice-negative-ask",
        selected_outcome_token_id="yes1",
        outcome_label="YES",
        yes_token_id="yes1",
        no_token_id="no1",
        top_bid="0.20",
        top_ask="0.48",
    )
    edge = _edge()
    decision = EdgeDecision(
        should_trade=True,
        edge=edge,
        tokens={"token_id": "yes1", "no_token_id": "no1"},
        size_usd=5.0,
        decision_snapshot_id="decision-snap-negative-ask",
        applied_validations=[],
        edge_context=types.SimpleNamespace(p_posterior=edge.p_posterior),
        sizing_bankroll=100.0,
        kelly_multiplier_used=0.25,
        execution_fee_rate=0.0,
        safety_cap_usd=None,
    )

    best_ask = cycle_runtime._reprice_decision_from_executable_snapshot(
        conn,
        decision,
        {"executable_snapshot_id": "snap-reprice-negative-ask"},
    )
    conn.close()

    assert best_ask is None
    assert decision.edge.vwmp == pytest.approx(0.34)
    assert decision.edge.edge == pytest.approx(0.13)
    assert decision.tokens["executable_snapshot_reprice"]["final_limit_price"] == pytest.approx(0.32)
    assert decision.tokens["executable_snapshot_reprice"]["corrected_pricing_shadow"]["submit_path"] is None


def test_executable_snapshot_repricing_rejects_insufficient_best_ask_depth(tmp_path):
    conn = get_connection(tmp_path / "snapshot-reprice-thin-depth.db")
    init_schema(conn)
    _insert_executable_snapshot(
        conn,
        snapshot_id="snap-reprice-thin-depth",
        selected_outcome_token_id="yes1",
        outcome_label="YES",
        yes_token_id="yes1",
        no_token_id="no1",
        top_bid="0.40",
        top_ask="0.41",
        ask_size="1",
    )
    edge = _edge()
    decision = EdgeDecision(
        should_trade=True,
        edge=edge,
        tokens={"token_id": "yes1", "no_token_id": "no1"},
        size_usd=5.0,
        applied_validations=[],
        edge_context=types.SimpleNamespace(p_posterior=edge.p_posterior),
        sizing_bankroll=100.0,
        kelly_multiplier_used=0.25,
        execution_fee_rate=0.0,
        safety_cap_usd=None,
    )

    with pytest.raises(ValueError, match="EXECUTABLE_TAKER_DEPTH_CONSTRAINED"):
        cycle_runtime._reprice_decision_from_executable_snapshot(
            conn,
            decision,
            {"executable_snapshot_id": "snap-reprice-thin-depth"},
        )
    conn.close()


def test_executable_snapshot_repricing_ignores_thin_ask_outside_slippage_budget(tmp_path):
    conn = get_connection(tmp_path / "snapshot-reprice-thin-wide-ask.db")
    init_schema(conn)
    _insert_executable_snapshot(
        conn,
        snapshot_id="snap-reprice-thin-wide-ask",
        selected_outcome_token_id="yes1",
        outcome_label="YES",
        yes_token_id="yes1",
        no_token_id="no1",
        top_bid="0.20",
        top_ask="0.30",
        bid_size="1",
        ask_size="1",
    )
    edge = _edge()
    decision = EdgeDecision(
        should_trade=True,
        edge=edge,
        tokens={"token_id": "yes1", "no_token_id": "no1"},
        size_usd=5.0,
        decision_snapshot_id="decision-snap-thin-wide-ask",
        applied_validations=[],
        edge_context=types.SimpleNamespace(p_posterior=edge.p_posterior),
        sizing_bankroll=100.0,
        kelly_multiplier_used=0.25,
        execution_fee_rate=0.0,
        safety_cap_usd=None,
    )

    best_ask = cycle_runtime._reprice_decision_from_executable_snapshot(
        conn,
        decision,
        {"executable_snapshot_id": "snap-reprice-thin-wide-ask"},
    )
    conn.close()

    assert best_ask is None
    assert decision.edge.vwmp == pytest.approx(0.25)
    assert decision.size_usd == pytest.approx((0.47 - 0.25) / (1 - 0.25) * 0.25 * 100.0)
    reprice = decision.tokens["executable_snapshot_reprice"]
    assert reprice["best_ask_blocked_by_slippage"] is True
    assert reprice["final_limit_price"] == pytest.approx(0.23)


def test_live_reprice_binds_intent_limit_when_dynamic_gap_would_not_jump(tmp_path):
    db_path = tmp_path / "live-reprice-limit-contract.db"
    conn = get_connection(db_path)
    init_schema(conn)
    _insert_executable_snapshot(
        conn,
        snapshot_id="snap-wide-gap",
        selected_outcome_token_id="yes1",
        outcome_label="YES",
        yes_token_id="yes1",
        no_token_id="no1",
        event_id="evt-wide-gap",
        condition_id="m1",
        top_bid="0.25",
        top_ask="0.25",
    )
    artifact = CycleArtifact(mode=DiscoveryMode.OPENING_HUNT.value, started_at="2026-04-03T00:00:00Z")
    summary = {"candidates": 0, "no_trades": 0}
    market = {
        "city": NYC,
        "target_date": "2026-04-01",
        "hours_since_open": 12.0,
        "hours_to_resolution": 24.0,
        "event_id": "evt-wide-gap",
        "slug": "slug-wide-gap",
        "temperature_metric": "high",
        "outcomes": [
            {
                "title": "39-40°F",
                "range_low": 39,
                "range_high": 40,
                "token_id": "yes1",
                "no_token_id": "no1",
                "market_id": "m1",
            },
        ],
    }
    decision = EdgeDecision(
        should_trade=True,
        edge=_edge(),
        tokens={
            "market_id": "m1",
            "token_id": "yes1",
            "no_token_id": "no1",
            "executable_snapshot_id": "snap-wide-gap",
            "executable_snapshot_min_tick_size": "0.01",
            "executable_snapshot_min_order_size": "5",
            "executable_snapshot_neg_risk": False,
        },
        size_usd=5.0,
        decision_id="d-wide-gap",
        selected_method="ens_member_counting",
        applied_validations=["ens_fetch"],
        decision_snapshot_id="model-snap-wide-gap",
        edge_source="opening_inertia",
        strategy_key="opening_inertia",
        edge_context=types.SimpleNamespace(p_posterior=0.47),
        sizing_bankroll=100.0,
        kelly_multiplier_used=0.25,
        execution_fee_rate=0.0,
        safety_cap_usd=None,
    )
    captured = {}

    def _execute_capture(intent, **kwargs):
        captured["intent"] = intent
        return OrderResult(
            status="pending",
            trade_id="trade-wide-gap",
            order_id="ord-wide-gap",
            submitted_price=float(intent.final_limit_price),
            shares=1.0,
            command_state="INTENT_CREATED",
        )

    deps = types.SimpleNamespace(
        MODE_PARAMS={DiscoveryMode.OPENING_HUNT: {}},
        find_weather_markets=lambda **kwargs: [market],
        get_last_scan_authority=lambda: "VERIFIED",
        DiscoveryMode=DiscoveryMode,
        logger=types.SimpleNamespace(warning=lambda *args, **kwargs: None, error=lambda *args, **kwargs: None),
        NoTradeCase=NoTradeCase,
        MarketCandidate=MarketCandidate,
        evaluate_candidate=lambda *args, **kwargs: [decision],
        is_strategy_enabled=lambda _strategy: True,
        _classify_edge_source=lambda _mode, _edge: "opening_inertia",
        create_execution_intent=lambda **kwargs: (_ for _ in ()).throw(
            AssertionError("live final-intent path must not create legacy intent")
        ),
        execute_intent=lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("live final-intent path must not submit legacy intent")
        ),
        execute_final_intent=_execute_capture,
        select_final_order_type=lambda conn, snapshot_id: "FOK",
    )

    cycle_runtime.execute_discovery_phase(
        conn,
        clob=None,
        portfolio=PortfolioState(),
        artifact=artifact,
        tracker=StrategyTracker(),
        limits=types.SimpleNamespace(),
        mode=DiscoveryMode.OPENING_HUNT,
        summary=summary,
        entry_bankroll=100.0,
        decision_time=datetime(2026, 4, 3, tzinfo=timezone.utc),
        env="live",
        deps=deps,
    )
    conn.close()

    assert captured["intent"].final_limit_price == Decimal("0.25")
    assert captured["intent"].order_type == "FOK"
    reprice = artifact.trade_cases[0]["executable_snapshot_reprice"]
    assert reprice["snapshot_vwmp"] == pytest.approx(0.25)
    assert reprice["best_ask_blocked_by_slippage"] is False
    assert reprice["final_limit_price"] == pytest.approx(0.25)
    assert reprice["submitted_limit_price"] == pytest.approx(0.25)
    assert reprice["repriced_limit_forced"] is True
    assert reprice["corrected_candidate_limit_price"] == pytest.approx(0.25)
    shadow = reprice["corrected_pricing_shadow"]
    assert shadow["candidate_final_limit_price"] == "0.25"
    assert shadow["submitted_limit_price"] == "0.25"
    assert shadow["submit_path"] == "final_execution_intent"
    assert shadow["submitted_matches_corrected_candidate"] is True


def test_live_reprice_rejects_passive_without_maker_only_support(tmp_path):
    db_path = tmp_path / "live-reprice-passive-final-intent.db"
    conn = get_connection(db_path)
    init_schema(conn)
    _insert_executable_snapshot(
        conn,
        snapshot_id="snap-passive-mismatch",
        selected_outcome_token_id="yes1",
        outcome_label="YES",
        yes_token_id="yes1",
        no_token_id="no1",
        event_id="evt-passive-mismatch",
        condition_id="m1",
        top_bid="0.20",
        top_ask="0.30",
    )
    artifact = CycleArtifact(mode=DiscoveryMode.OPENING_HUNT.value, started_at="2026-04-03T00:00:00Z")
    summary = {"candidates": 0, "no_trades": 0}
    market = {
        "city": NYC,
        "target_date": "2026-04-01",
        "hours_since_open": 12.0,
        "hours_to_resolution": 24.0,
        "event_id": "evt-passive-mismatch",
        "slug": "slug-passive-mismatch",
        "temperature_metric": "high",
        "outcomes": [
            {
                "title": "39-40°F",
                "range_low": 39,
                "range_high": 40,
                "token_id": "yes1",
                "no_token_id": "no1",
                "market_id": "m1",
            },
        ],
    }
    decision = EdgeDecision(
        should_trade=True,
        edge=_edge(),
        tokens={
            "market_id": "m1",
            "token_id": "yes1",
            "no_token_id": "no1",
            "executable_snapshot_id": "snap-passive-mismatch",
            "executable_snapshot_min_tick_size": "0.01",
            "executable_snapshot_min_order_size": "5",
            "executable_snapshot_neg_risk": False,
        },
        size_usd=5.0,
        decision_id="d-passive-mismatch",
        selected_method="ens_member_counting",
        applied_validations=["ens_fetch"],
        decision_snapshot_id="model-snap-passive-mismatch",
        edge_source="opening_inertia",
        strategy_key="opening_inertia",
        edge_context=types.SimpleNamespace(p_posterior=0.47),
        sizing_bankroll=100.0,
        kelly_multiplier_used=0.25,
        execution_fee_rate=0.0,
        safety_cap_usd=None,
    )

    captured = {}

    def _execute_final_capture(intent, **kwargs):
        captured["intent"] = intent
        captured["kwargs"] = kwargs
        return OrderResult(
            status="pending",
            trade_id="trade-passive-final",
            order_id="ord-passive-final",
            submitted_price=float(intent.final_limit_price),
            shares=1.0,
            command_state="INTENT_CREATED",
        )

    deps = types.SimpleNamespace(
        MODE_PARAMS={DiscoveryMode.OPENING_HUNT: {}},
        find_weather_markets=lambda **kwargs: [market],
        get_last_scan_authority=lambda: "VERIFIED",
        DiscoveryMode=DiscoveryMode,
        logger=types.SimpleNamespace(warning=lambda *args, **kwargs: None, error=lambda *args, **kwargs: None),
        NoTradeCase=NoTradeCase,
        MarketCandidate=MarketCandidate,
        evaluate_candidate=lambda *args, **kwargs: [decision],
        is_strategy_enabled=lambda _strategy: True,
        _classify_edge_source=lambda _mode, _edge: "opening_inertia",
        create_execution_intent=lambda **kwargs: (_ for _ in ()).throw(
            AssertionError("live final-intent path must not create legacy intent")
        ),
        execute_intent=lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("live final-intent path must not submit legacy intent")
        ),
        execute_final_intent=_execute_final_capture,
        select_final_order_type=lambda conn, snapshot_id: "GTC",
    )

    cycle_runtime.execute_discovery_phase(
        conn,
        clob=None,
        portfolio=PortfolioState(),
        artifact=artifact,
        tracker=StrategyTracker(),
        limits=types.SimpleNamespace(),
        mode=DiscoveryMode.OPENING_HUNT,
        summary=summary,
        entry_bankroll=100.0,
        decision_time=datetime(2026, 4, 3, tzinfo=timezone.utc),
        env="live",
        deps=deps,
    )
    conn.close()

    assert summary["no_trades"] == 1
    assert captured == {}
    assert artifact.trade_cases == []
    assert artifact.no_trade_cases[0].rejection_stage == "EXECUTION_FAILED"
    assert "FINAL_EXECUTION_INTENT_UNAVAILABLE" in artifact.no_trade_cases[0].rejection_reasons[0]
    assert (
        "PASSIVE_LIMIT_REQUIRES_POST_ONLY_OR_MAKER_ONLY_SUBMIT"
        in artifact.no_trade_cases[0].rejection_reasons[0]
    )
    reprice = decision.tokens["executable_snapshot_reprice"]
    shadow = reprice["corrected_pricing_shadow"]
    assert shadow["submit_path"] is None
    assert shadow["live_submit_authority"] is False
    assert shadow["unsupported_reason"] == "PASSIVE_LIMIT_REQUIRES_POST_ONLY_OR_MAKER_ONLY_SUBMIT"


def test_live_entry_captures_and_commits_snapshot_before_executor(tmp_path, monkeypatch):
    from src.data.market_scanner import capture_executable_market_snapshot
    from src.execution.executor import execute_final_intent as real_execute_final_intent
    from src.state.snapshot_repo import get_snapshot

    db_path = tmp_path / "live-entry-forward-snapshot.db"
    conn = get_connection(db_path)
    init_schema(conn)
    artifact = CycleArtifact(mode=DiscoveryMode.OPENING_HUNT.value, started_at="2026-04-03T00:00:00Z")
    summary = {"candidates": 0, "no_trades": 0}
    decision_time = datetime.now(timezone.utc)
    forecast_context = {
        "forecast_source_id": "tigge",
        "model_family": "ecmwf_ifs025",
        "forecast_issue_time": "2026-04-03T00:00:00+00:00",
        "forecast_valid_time": "2026-04-03T06:00:00+00:00",
        "forecast_fetch_time": "2026-04-03T01:00:00+00:00",
        "forecast_available_at": "2026-04-03T00:30:00+00:00",
        "raw_payload_hash": "b" * 64,
        "degradation_level": "OK",
        "forecast_source_role": "entry_primary",
        "authority_tier": "FORECAST",
        "decision_time": decision_time.isoformat(),
        "decision_time_status": "OK",
    }
    market = {
        "city": NYC,
        "target_date": "2026-04-01",
        "hours_since_open": 12.0,
        "hours_to_resolution": 24.0,
        "event_id": "evt-forward-snapshot",
        "slug": "slug-forward-snapshot",
        "temperature_metric": "high",
        "outcomes": [
            {
                "title": "39-40°F",
                "range_low": 39,
                "range_high": 40,
                "token_id": "yes1",
                "no_token_id": "no1",
                "market_id": "cond1",
                "condition_id": "cond1",
                "question_id": "q1",
                "gamma_market_id": "gamma1",
                "active": True,
                "closed": False,
                "accepting_orders": True,
                "enable_orderbook": True,
                "token_map_raw": {"YES": "yes1", "NO": "no1"},
                "raw_gamma_payload_hash": "a" * 64,
                "gamma_market_raw": {
                    "id": "gamma1",
                    "conditionId": "cond1",
                    "questionID": "q1",
                    "active": True,
                    "closed": False,
                    "acceptingOrders": True,
                    "enableOrderBook": True,
                    "clobTokenIds": ["yes1", "no1"],
                },
            },
        ],
    }

    decision = EdgeDecision(
        should_trade=True,
        edge=_edge(),
        tokens={"market_id": "cond1", "token_id": "yes1", "no_token_id": "no1"},
        size_usd=5.0,
        decision_id="d-forward-snapshot",
        selected_method="ens_member_counting",
        applied_validations=["ens_fetch"],
        decision_snapshot_id="model-snap-1",
        edge_source="opening_inertia",
        strategy_key="opening_inertia",
        edge_context=types.SimpleNamespace(p_posterior=0.47),
        epistemic_context_json=json.dumps({"forecast_context": forecast_context}),
        sizing_bankroll=100.0,
        kelly_multiplier_used=0.25,
        execution_fee_rate=0.003,
        safety_cap_usd=None,
    )

    class FakeClob:
        def get_clob_market_info(self, condition_id):
            assert condition_id == "cond1"
            return {
                "condition_id": "cond1",
                "tokens": [{"token_id": "yes1"}, {"token_id": "no1"}],
                "feesEnabled": True,
            }

        def get_orderbook_snapshot(self, token_id):
            assert token_id == "yes1"
            return {
                "asset_id": "yes1",
                "tick_size": "0.001",
                "min_order_size": "5",
                "neg_risk": False,
                "bids": [{"price": "0.249", "size": "100"}],
                "asks": [{"price": "0.25", "size": "100"}],
            }

        def get_fee_rate(self, token_id):
            assert token_id == "yes1"
            return 30

    captured = {}

    def _execute_after_snapshot_commit(intent, **kwargs):
        captured["intent"] = intent
        assert kwargs["conn"] is conn
        snapshot_conn = kwargs["snapshot_conn"]
        snap = get_snapshot(snapshot_conn, intent.snapshot_id)
        assert snap is not None
        try:
            return real_execute_final_intent(intent, **kwargs)
        except Exception as exc:
            captured["error"] = str(exc)
            raise

    class DummySubmitClient:
        def __init__(self):
            self.bound_envelope = None

        def bind_submission_envelope(self, envelope):
            self.bound_envelope = envelope

        def v2_preflight(self):
            return None

        def place_limit_order(self, *, token_id, price, size, side, order_type="GTC"):
            return {
                "success": True,
                "orderID": "ord-forward-snapshot",
                "status": "OPEN",
                "_venue_submission_envelope": self.bound_envelope.to_dict(),
            }

    monkeypatch.setattr("src.control.cutover_guard.assert_submit_allowed", lambda *args, **kwargs: None)
    monkeypatch.setattr("src.execution.executor._assert_risk_allocator_allows_submit", lambda intent: None)
    monkeypatch.setattr("src.execution.executor._select_risk_allocator_order_type", lambda conn, snapshot_id: "FOK")
    monkeypatch.setattr("src.execution.executor._assert_heartbeat_allows_submit", lambda order_type="GTC": {"allowed": True})
    monkeypatch.setattr("src.execution.executor._assert_ws_gap_allows_submit", lambda target: {"allowed": True})
    monkeypatch.setattr("src.execution.executor._assert_collateral_allows_buy", lambda intent, spend_micro: {"allowed": True})
    monkeypatch.setattr("src.control.ws_gap_guard.assert_ws_allows_submit", lambda *args, **kwargs: None)
    monkeypatch.setattr("src.state.collateral_ledger.assert_buy_preflight", lambda *args, **kwargs: None)
    monkeypatch.setattr("src.execution.executor._reserve_collateral_for_buy", lambda *args, **kwargs: None)
    monkeypatch.setattr("src.data.polymarket_client.PolymarketClient", DummySubmitClient)

    deps = types.SimpleNamespace(
        MODE_PARAMS={DiscoveryMode.OPENING_HUNT: {}},
        find_weather_markets=lambda **kwargs: [market],
        get_last_scan_authority=lambda: "VERIFIED",
        capture_executable_market_snapshot=capture_executable_market_snapshot,
        DiscoveryMode=DiscoveryMode,
        logger=types.SimpleNamespace(warning=lambda *args, **kwargs: None, error=lambda *args, **kwargs: None),
        NoTradeCase=NoTradeCase,
        MarketCandidate=MarketCandidate,
        evaluate_candidate=lambda *args, **kwargs: [decision],
        is_strategy_enabled=lambda _strategy: True,
        _classify_edge_source=lambda _mode, _edge: "opening_inertia",
        create_execution_intent=lambda **kwargs: (_ for _ in ()).throw(
            AssertionError("live final-intent path must not create legacy intent")
        ),
        execute_intent=lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("live final-intent path must not submit legacy intent")
        ),
        execute_final_intent=_execute_after_snapshot_commit,
        select_final_order_type=lambda conn, snapshot_id: "FOK",
    )

    cycle_runtime.execute_discovery_phase(
        conn,
        clob=FakeClob(),
        portfolio=PortfolioState(),
        artifact=artifact,
        tracker=StrategyTracker(),
        limits=types.SimpleNamespace(),
        mode=DiscoveryMode.OPENING_HUNT,
        summary=summary,
        entry_bankroll=100.0,
        decision_time=decision_time,
        env="live",
        deps=deps,
    )

    snapshot_count = conn.execute("SELECT COUNT(*) FROM executable_market_snapshots").fetchone()[0]
    command_count = conn.execute("SELECT COUNT(*) FROM venue_commands").fetchone()[0]
    command_row = conn.execute(
        "SELECT decision_id, token_id, price, state FROM venue_commands WHERE decision_id = ?",
        ("d-forward-snapshot",),
    ).fetchone()
    price_linkage = conn.execute(
        """
        SELECT market_price_linkage, source, best_bid, best_ask,
               raw_orderbook_hash, snapshot_id, condition_id
        FROM market_price_history
        WHERE snapshot_id = ?
        """,
        (captured["intent"].snapshot_id,),
    ).fetchone()
    loaded_snapshot = get_snapshot(conn, captured["intent"].snapshot_id)
    conn.close()

    assert "error" not in captured, captured.get("error")
    assert captured["intent"].snapshot_id
    assert captured["intent"].final_limit_price % captured["intent"].tick_size == 0
    assert loaded_snapshot is not None
    assert loaded_snapshot.captured_at != datetime(2026, 4, 3, tzinfo=timezone.utc)
    assert snapshot_count == 1
    assert command_count == 1
    assert command_row["decision_id"] == "d-forward-snapshot"
    assert command_row["token_id"] == "yes1"
    assert command_row["price"] == pytest.approx(float(captured["intent"].final_limit_price))
    assert command_row["state"] == "ACKED"
    assert price_linkage is not None
    assert price_linkage["market_price_linkage"] == "full"
    assert price_linkage["source"] == "CLOB_ORDERBOOK"
    assert price_linkage["best_bid"] == pytest.approx(0.249)
    assert price_linkage["best_ask"] == pytest.approx(0.25)
    assert price_linkage["raw_orderbook_hash"]
    assert price_linkage["condition_id"] == "cond1"
    assert summary["forward_market_price_linkage_status"] == "inserted"
    assert summary["no_trades"] == 0


def test_executable_snapshot_requires_explicit_accepting_orders():
    from src.data.market_scanner import (
        ExecutableSnapshotCaptureError,
        capture_executable_market_snapshot,
    )

    market = {
        "outcomes": [
            {
                "title": "39-40°F",
                "range_low": 39,
                "range_high": 40,
                "token_id": "yes1",
                "no_token_id": "no1",
                "market_id": "cond1",
                "condition_id": "cond1",
                "question_id": "q1",
                "active": True,
                "closed": False,
                "enable_orderbook": True,
                "gamma_market_raw": {
                    "id": "gamma1",
                    "conditionId": "cond1",
                    "questionID": "q1",
                    "active": True,
                    "closed": False,
                    "enableOrderBook": True,
                    "clobTokenIds": ["yes1", "no1"],
                },
            },
        ],
    }
    decision = types.SimpleNamespace(
        tokens={"market_id": "cond1", "token_id": "yes1", "no_token_id": "no1"},
        edge=types.SimpleNamespace(direction="buy_yes"),
    )

    with pytest.raises(ExecutableSnapshotCaptureError, match="not currently tradable"):
        capture_executable_market_snapshot(
            None,
            market=market,
            decision=decision,
            clob=object(),
            captured_at=datetime(2026, 4, 1, tzinfo=timezone.utc),
            scan_authority="VERIFIED",
        )


def _trace_status_for_evaluator_decision(tmp_path, candidate):
    conn = get_connection(tmp_path / "trace-early.db")
    init_schema(conn)
    decisions = evaluator_module.evaluate_candidate(
        candidate,
        conn,
        PortfolioState(),
        types.SimpleNamespace(),
        types.SimpleNamespace(),
        entry_bankroll=150.0,
        decision_time=datetime(2026, 4, 1, tzinfo=timezone.utc),
    )
    assert len(decisions) == 1
    result = db_module.log_probability_trace_fact(
        conn,
        candidate=candidate,
        decision=decisions[0],
        recorded_at="2026-04-01T00:00:00+00:00",
        mode=candidate.discovery_mode,
    )
    conn.close()
    return decisions[0], result


def _three_outcomes():
    return [
        {"title": "38°F or lower", "range_low": None, "range_high": 38, "token_id": "yes0", "no_token_id": "no0", "market_id": "m0"},
        {"title": "39-40°F", "range_low": 39, "range_high": 40, "token_id": "yes1", "no_token_id": "no1", "market_id": "m1"},
        {"title": "41-42°F", "range_low": 41, "range_high": 42, "token_id": "yes2", "no_token_id": "no2", "market_id": "m2"},
        {"title": "43°F or higher", "range_low": 43, "range_high": None, "token_id": "yes3", "no_token_id": "no3", "market_id": "m3"},
    ]


def test_day0_missing_observation_is_pre_vector_traceable(tmp_path):
    candidate = MarketCandidate(
        city=NYC,
        target_date="2026-04-01",
        outcomes=_three_outcomes(),
        hours_since_open=12.0,
        hours_to_resolution=4.0,
        observation=None,
        discovery_mode=DiscoveryMode.DAY0_CAPTURE.value,
    )

    decision, result = _trace_status_for_evaluator_decision(tmp_path, candidate)

    assert decision.rejection_stage == "SIGNAL_QUALITY"
    assert decision.availability_status == "DATA_UNAVAILABLE"
    assert result["trace_status"] == "pre_vector_unavailable"


def test_unparseable_bin_filter_is_pre_vector_traceable(tmp_path, monkeypatch):
    monkeypatch.setattr(evaluator_module, "get_mode", lambda: "test")
    candidate = MarketCandidate(
        city=NYC,
        target_date="2026-04-01",
        outcomes=[
            {"title": "not a temp market", "range_low": None, "range_high": None, "token_id": "yes1", "no_token_id": "no1", "market_id": "m1"},
        ],
        hours_since_open=12.0,
        hours_to_resolution=24.0,
        discovery_mode=DiscoveryMode.OPENING_HUNT.value,
    )

    decision, result = _trace_status_for_evaluator_decision(tmp_path, candidate)

    assert decision.rejection_stage == "MARKET_FILTER"
    assert result["trace_status"] == "pre_vector_unavailable"


def test_ens_fetch_exception_is_pre_vector_traceable(tmp_path, monkeypatch):
    monkeypatch.setattr(evaluator_module, "fetch_ensemble", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("ens down")))
    candidate = MarketCandidate(
        city=NYC,
        target_date="2026-04-01",
        outcomes=_three_outcomes(),
        hours_since_open=12.0,
        hours_to_resolution=24.0,
        discovery_mode=DiscoveryMode.OPENING_HUNT.value,
    )

    decision, result = _trace_status_for_evaluator_decision(tmp_path, candidate)

    assert decision.rejection_stage == "SIGNAL_QUALITY"
    assert decision.availability_status == "DATA_UNAVAILABLE"
    assert result["trace_status"] == "pre_vector_unavailable"


def test_ens_validation_failure_is_pre_vector_traceable(tmp_path, monkeypatch):
    monkeypatch.setattr(evaluator_module, "fetch_ensemble", lambda *args, **kwargs: {"n_members": 0})
    monkeypatch.setattr(evaluator_module, "validate_ensemble", lambda *args, **kwargs: False)
    candidate = MarketCandidate(
        city=NYC,
        target_date="2026-04-01",
        outcomes=_three_outcomes(),
        hours_since_open=12.0,
        hours_to_resolution=24.0,
        discovery_mode=DiscoveryMode.OPENING_HUNT.value,
    )

    decision, result = _trace_status_for_evaluator_decision(tmp_path, candidate)

    assert decision.rejection_stage == "SIGNAL_QUALITY"
    assert decision.availability_status == "DATA_UNAVAILABLE"
    assert result["trace_status"] == "pre_vector_unavailable"


def test_openmeteo_degraded_forecast_fallback_blocks_entry_before_vector(tmp_path, monkeypatch):
    monkeypatch.setattr(evaluator_module, "get_mode", lambda: "test")
    monkeypatch.setattr(evaluator_module, "fetch_ensemble", lambda *args, **kwargs: {
        "members_hourly": np.ones((51, 24)) * 40.0,
        "times": [f"2026-04-01T{hour:02d}:00:00Z" for hour in range(24)],
        "issue_time": None,
        "first_valid_time": datetime(2026, 4, 1, tzinfo=timezone.utc),
        "fetch_time": datetime(2026, 4, 1, tzinfo=timezone.utc),
        "model": "ecmwf_ifs025",
        "source_id": "openmeteo_ensemble_ecmwf_ifs025",
        "degradation_level": "DEGRADED_FORECAST_FALLBACK",
        "forecast_source_role": "monitor_fallback",
        "n_members": 51,
    })
    monkeypatch.setattr(evaluator_module, "validate_ensemble", lambda *args, **kwargs: True)
    candidate = MarketCandidate(
        city=NYC,
        target_date="2026-04-01",
        outcomes=_three_outcomes(),
        hours_since_open=12.0,
        hours_to_resolution=24.0,
        discovery_mode=DiscoveryMode.OPENING_HUNT.value,
    )

    decision, result = _trace_status_for_evaluator_decision(tmp_path, candidate)

    assert decision.rejection_stage == "SIGNAL_QUALITY"
    assert decision.availability_status == "DATA_STALE"
    assert "DEGRADED_FORECAST_FALLBACK" in decision.rejection_reasons[0]
    assert "forecast_source_policy" in decision.applied_validations
    assert result["trace_status"] == "pre_vector_unavailable"


def test_entry_primary_source_policy_exception_blocks_entry_before_vector(tmp_path, monkeypatch):
    from src.data.forecast_source_registry import SourceNotEnabled
    monkeypatch.setattr(evaluator_module, "get_mode", lambda: "test")

    def _blocked_entry(*args, **kwargs):
        assert kwargs.get("role") == "entry_primary"
        raise SourceNotEnabled(
            "forecast source 'openmeteo_ensemble_ecmwf_ifs025' is not "
            "authorized for role 'entry_primary'"
        )

    monkeypatch.setattr(evaluator_module, "fetch_ensemble", _blocked_entry)
    candidate = MarketCandidate(
        city=NYC,
        target_date="2026-04-01",
        outcomes=_three_outcomes(),
        hours_since_open=12.0,
        hours_to_resolution=24.0,
        discovery_mode=DiscoveryMode.OPENING_HUNT.value,
    )

    decision, result = _trace_status_for_evaluator_decision(tmp_path, candidate)

    assert decision.rejection_stage == "SIGNAL_QUALITY"
    assert decision.availability_status == "DATA_STALE"
    assert "entry_primary" in decision.rejection_reasons[0]
    assert "forecast_source_policy" in decision.applied_validations
    assert result["trace_status"] == "pre_vector_unavailable"


def test_monitor_ens_refresh_records_forecast_fallback_provenance(monkeypatch):
    from src.engine import monitor_refresh

    monkeypatch.setitem(settings["ensemble"], "primary", "gfs025")
    captured: dict[str, object] = {}
    position = types.SimpleNamespace(
        temperature_metric="high",
        bin_label="30-31°F",
        unit="F",
        market_id="m-monitor",
        direction="buy_yes",
        p_posterior=0.42,
        entered_at=None,
        target_date="2026-04-01",
        entry_model_agreement="AGREE",
        selected_method="ens_member_counting",
        entry_method="ens_member_counting",
    )
    city = types.SimpleNamespace(
        name="NYC",
        lat=40.7772,
        timezone="America/New_York",
        cluster="NYC",
        settlement_unit="F",
        settlement_source_type="wu_icao",
        wu_station="KLGA",
    )

    class DummyEnsembleSignal:
        def __init__(self, *args, **kwargs):
            self.member_maxes = np.array([30.0, 31.0, 32.0])

        def p_raw_vector(self, bins, n_mc=None):
            return np.array([0.7, 0.3])

        def spread(self):
            return monitor_refresh.TemperatureDelta(1.0, "F")

    def _fetch(*args, **kwargs):
        captured["role"] = kwargs.get("role")
        captured["model"] = kwargs.get("model")
        return {
            "members_hourly": np.ones((51, 24)),
            "times": ["2026-04-01T00:00:00Z"] * 24,
            "fetch_time": datetime(2026, 4, 1, 12, tzinfo=timezone.utc),
            "source_id": "openmeteo_ensemble_ecmwf_ifs025",
            "forecast_source_role": "monitor_fallback",
            "degradation_level": "DEGRADED_FORECAST_FALLBACK",
            "n_members": 51,
        }

    monkeypatch.setattr(monitor_refresh, "fetch_ensemble", _fetch)
    monkeypatch.setattr(monitor_refresh, "validate_ensemble", lambda result: True)
    monkeypatch.setattr(monitor_refresh, "lead_days_to_date_start", lambda *args, **kwargs: 2.0)
    monkeypatch.setattr(monitor_refresh, "EnsembleSignal", DummyEnsembleSignal)
    monkeypatch.setattr(
        monitor_refresh,
        "_build_all_bins",
        lambda *args, **kwargs: (
            [
                Bin(low=30, high=31, label="30-31°F", unit="F"),
                Bin(low=32, high=33, label="32-33°F", unit="F"),
            ],
            0,
        ),
    )
    monkeypatch.setattr(monitor_refresh, "get_calibrator", lambda *args, **kwargs: (None, 4))
    monkeypatch.setattr("src.calibration.store.get_pairs_for_bucket", lambda *args, **kwargs: [])
    monkeypatch.setattr(monitor_refresh, "season_from_date", lambda *args, **kwargs: "MAM")
    monkeypatch.setattr(
        monitor_refresh,
        "compute_alpha",
        lambda **kwargs: types.SimpleNamespace(value_for_consumer=lambda consumer: 1.0),
    )
    monkeypatch.setattr(monitor_refresh, "_check_persistence_anomaly", lambda *args, **kwargs: 1.0)

    _posterior, applied = monitor_refresh._refresh_ens_member_counting(
        position=position,
        current_p_market=0.50,
        conn=types.SimpleNamespace(execute=lambda *args, **kwargs: None),
        city=city,
        target_d=date(2026, 4, 1),
    )

    assert captured["role"] == "monitor_fallback"
    assert captured["model"] == "gfs025"
    assert "forecast_source_id:openmeteo_ensemble_ecmwf_ifs025" in applied
    assert "forecast_source_role:monitor_fallback" in applied
    assert "forecast_degradation:DEGRADED_FORECAST_FALLBACK" in applied
    assert "alpha_posterior" in applied


def test_monitor_ens_refresh_marks_stale_when_support_topology_unavailable(monkeypatch):
    from src.engine import monitor_refresh

    position = types.SimpleNamespace(
        temperature_metric="high",
        bin_label="61-62°F",
        unit="F",
        market_id="m-center",
        direction="buy_yes",
        p_posterior=0.37,
        entered_at=None,
        target_date="2026-04-01",
        entry_model_agreement="AGREE",
        selected_method="ens_member_counting",
        entry_method="ens_member_counting",
    )
    city = types.SimpleNamespace(
        name="NYC",
        lat=40.7772,
        timezone="America/New_York",
        cluster="NYC",
        settlement_unit="F",
        settlement_source_type="wu_icao",
        wu_station="KLGA",
    )

    monkeypatch.setattr(
        monitor_refresh,
        "fetch_ensemble",
        lambda *args, **kwargs: {
            "members_hourly": np.ones((51, 24)),
            "times": ["2026-04-01T00:00:00Z"] * 24,
            "fetch_time": datetime(2026, 4, 1, 12, tzinfo=timezone.utc),
            "source_id": "openmeteo_ensemble_ecmwf_ifs025",
            "forecast_source_role": "monitor_fallback",
            "degradation_level": "DEGRADED_FORECAST_FALLBACK",
            "n_members": 51,
        },
    )
    monkeypatch.setattr(monitor_refresh, "validate_ensemble", lambda result: True)
    monkeypatch.setattr(monitor_refresh, "lead_days_to_date_start", lambda *args, **kwargs: 2.0)

    class DummyEnsembleSignal:
        member_maxes = np.ones(51)
        member_extrema = np.ones(51)

        def __init__(self, *args, **kwargs):
            pass

    monkeypatch.setattr(monitor_refresh, "EnsembleSignal", DummyEnsembleSignal)
    monkeypatch.setattr(
        monitor_refresh,
        "_build_all_bins",
        lambda *args, **kwargs: (_ for _ in ()).throw(ValueError("support topology incomplete")),
    )

    posterior, applied = monitor_refresh._refresh_ens_member_counting(
        position=position,
        current_p_market=0.50,
        conn=None,
        city=city,
        target_d=date(2026, 4, 1),
    )

    assert posterior == pytest.approx(position.p_posterior)
    assert "support_topology_stale" in applied
    assert "forecast_source_id:openmeteo_ensemble_ecmwf_ifs025" in applied
    assert getattr(position, monitor_refresh._MONITOR_PROBABILITY_FRESH_ATTR) is False


def test_monitor_ens_refresh_marks_stale_when_support_topology_authority_stale(monkeypatch):
    from src.engine import monitor_refresh

    position = types.SimpleNamespace(
        temperature_metric="high",
        bin_label="61-62°F",
        unit="F",
        market_id="m-center",
        direction="buy_yes",
        p_posterior=0.37,
        entered_at=None,
        target_date="2026-04-01",
        entry_model_agreement="AGREE",
        selected_method="ens_member_counting",
        entry_method="ens_member_counting",
    )
    city = types.SimpleNamespace(
        name="NYC",
        lat=40.7772,
        timezone="America/New_York",
        cluster="NYC",
        settlement_unit="F",
        settlement_source_type="wu_icao",
        wu_station="KLGA",
    )

    monkeypatch.setattr(
        monitor_refresh,
        "fetch_ensemble",
        lambda *args, **kwargs: {
            "members_hourly": np.ones((51, 24)),
            "times": ["2026-04-01T00:00:00Z"] * 24,
            "fetch_time": datetime(2026, 4, 1, 12, tzinfo=timezone.utc),
            "source_id": "openmeteo_ensemble_ecmwf_ifs025",
            "forecast_source_role": "monitor_fallback",
            "degradation_level": "DEGRADED_FORECAST_FALLBACK",
            "n_members": 51,
        },
    )
    monkeypatch.setattr(monitor_refresh, "validate_ensemble", lambda result: True)
    monkeypatch.setattr(monitor_refresh, "lead_days_to_date_start", lambda *args, **kwargs: 2.0)

    class DummyEnsembleSignal:
        member_maxes = np.ones(51)
        member_extrema = np.ones(51)

        def __init__(self, *args, **kwargs):
            pass

    monkeypatch.setattr(monitor_refresh, "EnsembleSignal", DummyEnsembleSignal)
    monkeypatch.setattr(monitor_refresh, "get_last_scan_authority", lambda: "STALE")
    monkeypatch.setattr(
        monitor_refresh,
        "get_sibling_outcomes",
        lambda market_id: [
            {
                "market_id": "m-low",
                "title": "Will the high temperature in NYC be 60°F or below?",
                "range_low": None,
                "range_high": 60,
            },
            {
                "market_id": "m-center",
                "title": "Will the high temperature in NYC be 61-62°F?",
                "range_low": 61,
                "range_high": 62,
            },
            {
                "market_id": "m-high",
                "title": "Will the high temperature in NYC be 63°F or higher?",
                "range_low": 63,
                "range_high": None,
            },
        ],
    )

    posterior, applied = monitor_refresh._refresh_ens_member_counting(
        position=position,
        current_p_market=0.50,
        conn=None,
        city=city,
        target_d=date(2026, 4, 1),
    )

    assert posterior == pytest.approx(position.p_posterior)
    assert "support_topology_stale" in applied
    assert getattr(position, monitor_refresh._MONITOR_PROBABILITY_FRESH_ATTR) is False


def test_day0_monitor_refresh_records_forecast_fallback_provenance(monkeypatch):
    from src.engine import monitor_refresh

    monkeypatch.setitem(settings["ensemble"], "primary", "gfs025")
    captured: dict[str, object] = {}
    position = types.SimpleNamespace(
        temperature_metric="high",
        bin_label="40-41°F",
        unit="F",
        market_id="m-day0",
        direction="buy_yes",
        p_posterior=0.31,
        entered_at=None,
        target_date="2026-04-01",
        entry_model_agreement="AGREE",
        selected_method="day0_observation",
        entry_method="day0_observation",
    )
    city = types.SimpleNamespace(
        name="NYC",
        lat=40.7772,
        timezone="America/New_York",
        cluster="NYC",
        settlement_unit="F",
        settlement_source_type="wu_icao",
        wu_station="KLGA",
    )

    def _fetch(*args, **kwargs):
        captured["role"] = kwargs.get("role")
        captured["model"] = kwargs.get("model")
        return {
            "members_hourly": np.ones((51, 24)),
            "times": ["2026-04-01T00:00:00Z"] * 24,
            "fetch_time": datetime(2026, 4, 1, 12, tzinfo=timezone.utc),
            "source_id": "openmeteo_ensemble_ecmwf_ifs025",
            "forecast_source_role": "monitor_fallback",
            "degradation_level": "DEGRADED_FORECAST_FALLBACK",
            "n_members": 51,
        }

    monkeypatch.setattr(
        monitor_refresh,
        "_fetch_day0_observation",
        lambda *args, **kwargs: types.SimpleNamespace(
            high_so_far=41.0,
            low_so_far=None,
            current_temp=40.5,
            source="wu_api",
            observation_time=datetime.now(timezone.utc).isoformat(),
        ),
    )
    monkeypatch.setattr(monitor_refresh, "fetch_ensemble", _fetch)
    monkeypatch.setattr(monitor_refresh, "validate_ensemble", lambda result: True)
    monkeypatch.setattr(
        "src.signal.diurnal.build_day0_temporal_context",
        lambda *args, **kwargs: types.SimpleNamespace(
            current_utc_timestamp=datetime(2026, 4, 1, 16, tzinfo=timezone.utc)
        ),
    )
    monkeypatch.setattr(
        monitor_refresh,
        "remaining_member_extrema_for_day0",
        lambda *args, **kwargs: (
            types.SimpleNamespace(maxes=np.array([40.0, 41.0, 42.0]), mins=None),
            2.0,
        ),
    )
    monkeypatch.setattr(
        monitor_refresh.Day0Router,
        "route",
        lambda inputs: types.SimpleNamespace(p_vector=lambda bins, n_mc=None: np.array([0.8, 0.2])),
    )
    monkeypatch.setattr(
        monitor_refresh,
        "_build_all_bins",
        lambda *args, **kwargs: (
            [
                Bin(low=40, high=41, label="40-41°F", unit="F"),
                Bin(low=42, high=43, label="42-43°F", unit="F"),
            ],
            0,
        ),
    )
    monkeypatch.setattr(monitor_refresh, "get_calibrator", lambda *args, **kwargs: (None, 4))
    monkeypatch.setattr("src.calibration.store.get_pairs_for_bucket", lambda *args, **kwargs: [])
    monkeypatch.setattr(monitor_refresh, "season_from_date", lambda *args, **kwargs: "MAM")
    monkeypatch.setattr(
        monitor_refresh,
        "compute_alpha",
        lambda **kwargs: types.SimpleNamespace(value_for_consumer=lambda consumer: 1.0),
    )

    _posterior, applied = monitor_refresh._refresh_day0_observation(
        position=position,
        current_p_market=0.50,
        conn=types.SimpleNamespace(execute=lambda *args, **kwargs: None),
        city=city,
        target_d=date(2026, 4, 1),
    )

    assert captured["role"] == "monitor_fallback"
    assert captured["model"] == "gfs025"
    assert "forecast_source_id:openmeteo_ensemble_ecmwf_ifs025" in applied
    assert "forecast_source_role:monitor_fallback" in applied
    assert "forecast_degradation:DEGRADED_FORECAST_FALLBACK" in applied
    assert "alpha_posterior" in applied


def test_day0_monitor_refresh_rejects_stale_observation_before_fetch(monkeypatch):
    from src.engine import monitor_refresh

    position = types.SimpleNamespace(
        temperature_metric="high",
        bin_label="40-41°F",
        unit="F",
        market_id="m-day0",
        direction="buy_yes",
        p_posterior=0.31,
        selected_method="day0_observation",
        entry_method="day0_observation",
    )
    city = types.SimpleNamespace(
        name="NYC",
        lat=40.7772,
        timezone="America/New_York",
        cluster="NYC",
        settlement_unit="F",
        settlement_source_type="wu_icao",
        wu_station="KLGA",
    )
    stale_observed_at = datetime(2026, 4, 1, 16, tzinfo=timezone.utc)
    monkeypatch.setattr(
        monitor_refresh,
        "_fetch_day0_observation",
        lambda *args, **kwargs: types.SimpleNamespace(
            high_so_far=41.0,
            low_so_far=39.0,
            current_temp=40.5,
            source="wu_api",
            observation_time=int(stale_observed_at.timestamp()),
        ),
    )
    monkeypatch.setattr(
        monitor_refresh,
        "fetch_ensemble",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("fetch_ensemble must not run")),
    )

    posterior, applied = monitor_refresh._refresh_day0_observation(
        position=position,
        current_p_market=0.50,
        conn=None,
        city=city,
        target_d=date(2026, 4, 1),
    )

    assert posterior == pytest.approx(position.p_posterior)
    assert "observation_quality_gate" in applied
    assert any("stale" in item for item in applied)


def test_evaluator_uses_configured_primary_and_crosscheck_models(monkeypatch):
    monkeypatch.setattr(evaluator_module, "get_mode", lambda: "test")
    monkeypatch.setitem(settings["ensemble"], "primary", "tigge")
    monkeypatch.setitem(settings["ensemble"], "crosscheck", "gfs025")
    calls: list[dict[str, object]] = []
    target_date = "2026-01-15"
    tz = ZoneInfo(NYC.timezone)
    start_local = datetime(2026, 1, 15, 0, 0, tzinfo=tz)
    times = [
        (start_local + timedelta(hours=i)).astimezone(timezone.utc).isoformat()
        for i in range(24)
    ]

    candidate = MarketCandidate(
        city=NYC,
        target_date=target_date,
        outcomes=_three_outcomes(),
        hours_since_open=8.0,
        hours_to_resolution=24.0,
        discovery_mode=DiscoveryMode.OPENING_HUNT.value,
    )

    class DummyEnsembleSignal:
        def __init__(self, *args, **kwargs):
            self.member_maxes = np.full(51, 40.0)
            self.member_extrema = self.member_maxes
            self.bias_corrected = False

        def p_raw_vector(self, bins, n_mc=None):
            return np.array([0.25, 0.25, 0.25, 0.25])

        def spread(self):
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

    def _fetch_ensemble(city, forecast_days=2, model=None, role=None):
        calls.append({"model": model, "role": role})
        n_members = 31 if role == "diagnostic" else 51
        return {
            "members_hourly": np.ones((n_members, len(times))) * 40.0,
            "times": times,
            **_entry_forecast_evidence(
                model=model or "ecmwf_ifs025",
                source_id=str(model or "ecmwf_ifs025"),
                role=role or "entry_primary",
                issue_time=datetime(2026, 1, 14, 0, tzinfo=timezone.utc),
                first_valid_time=datetime(2026, 1, 15, 5, tzinfo=timezone.utc),
                fetch_time=datetime(2026, 1, 14, 6, tzinfo=timezone.utc),
                n_members=n_members,
            ),
        }

    monkeypatch.setattr(evaluator_module, "fetch_ensemble", _fetch_ensemble)
    monkeypatch.setattr(evaluator_module, "validate_ensemble", lambda result, expected_members=51: result is not None)
    monkeypatch.setattr(evaluator_module, "EnsembleSignal", DummyEnsembleSignal)
    monkeypatch.setattr(evaluator_module, "_store_ens_snapshot", lambda *args, **kwargs: "snap-source-selection")
    monkeypatch.setattr(evaluator_module, "_store_snapshot_p_raw", lambda *args, **kwargs: None)
    _patch_mature_calibration(monkeypatch)
    monkeypatch.setattr(evaluator_module, "MarketAnalysis", DummyAnalysis)
    _stub_full_family_scan(monkeypatch)
    monkeypatch.setattr(evaluator_module, "fdr_filter", lambda edges, fdr_alpha=0.10: list(edges))
    monkeypatch.setattr(evaluator_module, "model_agreement", lambda *args, **kwargs: "CONFLICT")

    decisions = evaluator_module.evaluate_candidate(
        candidate,
        conn=None,
        portfolio=PortfolioState(bankroll=150.0),
        clob=type("DummyClob", (), {"get_best_bid_ask": lambda self, token_id: (0.34, 0.36, 20.0, 20.0)})(),
        limits=evaluator_module.RiskLimits(),
        decision_time=datetime(2026, 1, 14, 6, 0, tzinfo=timezone.utc),
    )

    assert len(decisions) == 1
    assert calls[0] == {"model": "tigge", "role": "entry_primary"}
    assert calls[1] == {"model": "gfs025", "role": "diagnostic"}
    assert decisions[0].rejection_reasons == ["tigge/gfs025 CONFLICT"]


def test_forecast_provider_identity_uses_source_id_not_model_family(monkeypatch):
    monkeypatch.setattr(evaluator_module, "get_mode", lambda: "test")
    captured: dict[str, object] = {}
    target_date = "2026-01-15"
    tz = ZoneInfo(NYC.timezone)
    start_local = datetime(2026, 1, 15, 0, 0, tzinfo=tz)
    times = [
        (start_local + timedelta(hours=i)).astimezone(timezone.utc).isoformat()
        for i in range(24)
    ]
    season = evaluator_module.season_from_date(target_date, lat=NYC.lat)
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE model_bias (
            city TEXT,
            season TEXT,
            source TEXT,
            bias REAL,
            mae REAL,
            n_samples INTEGER,
            discount_factor REAL
        )
        """
    )
    conn.execute(
        "INSERT INTO model_bias VALUES (?, ?, ?, ?, ?, ?, ?)",
        (NYC.name, season, "ecmwf", 99.0, 99.0, 1, 0.01),
    )
    conn.execute(
        "INSERT INTO model_bias VALUES (?, ?, ?, ?, ?, ?, ?)",
        (NYC.name, season, "tigge", 1.0, 2.0, 30, 0.5),
    )

    candidate = MarketCandidate(
        city=NYC,
        target_date=target_date,
        outcomes=_three_outcomes(),
        hours_since_open=8.0,
        hours_to_resolution=24.0,
        discovery_mode=DiscoveryMode.OPENING_HUNT.value,
    )

    class DummyEnsembleSignal:
        def __init__(self, *args, **kwargs):
            self.member_maxes = np.full(51, 40.0)
            self.member_extrema = self.member_maxes
            self.bias_corrected = False

        def p_raw_vector(self, bins, n_mc=None):
            return np.array([0.25, 0.25, 0.25, 0.25])

        def spread(self):
            return TemperatureDelta(1.0, "F")

        def spread_float(self):
            return 1.0

        def is_bimodal(self):
            return False

    class CapturingAnalysis:
        def __init__(self, **kwargs):
            captured["forecast_source"] = kwargs["forecast_source"]
            captured["bias_reference"] = kwargs["bias_reference"]
            captured["forecast_context_source"] = kwargs["forecast_source"]

        def find_edges(self, n_bootstrap=500):
            return []

        def sigma_context(self):
            return {"base_sigma": 0.5, "lead_multiplier": 1.1, "spread_multiplier": 1.05, "final_sigma": 0.5775}

        def mean_context(self):
            return {"offset": 0.0, "lead_days": 1.5}

        def forecast_context(self):
            return {"uncertainty": self.sigma_context(), "location": self.mean_context()}

    def _fetch_ensemble(city, forecast_days=2, model=None, role=None):
        n_members = 31 if role == "diagnostic" else 51
        return {
            "members_hourly": np.ones((n_members, len(times))) * 40.0,
            "times": times,
            **_entry_forecast_evidence(
                model="ecmwf_ifs025" if role == "entry_primary" else "gfs025",
                source_id="tigge" if role == "entry_primary" else "openmeteo_ensemble_gfs025",
                role=role or "entry_primary",
                issue_time=datetime(2026, 1, 14, 0, tzinfo=timezone.utc),
                first_valid_time=datetime(2026, 1, 15, tzinfo=timezone.utc),
                fetch_time=datetime(2026, 1, 14, 6, tzinfo=timezone.utc),
                n_members=n_members,
            ),
        }

    monkeypatch.setattr(evaluator_module, "fetch_ensemble", _fetch_ensemble)
    monkeypatch.setattr(evaluator_module, "validate_ensemble", lambda result, expected_members=51: result is not None)
    monkeypatch.setattr(evaluator_module, "EnsembleSignal", DummyEnsembleSignal)
    monkeypatch.setattr(evaluator_module, "_store_ens_snapshot", lambda *args, **kwargs: "snap-provider-id")
    monkeypatch.setattr(evaluator_module, "_store_snapshot_p_raw", lambda *args, **kwargs: None)
    _patch_mature_calibration(monkeypatch)
    monkeypatch.setattr(evaluator_module, "MarketAnalysis", CapturingAnalysis)
    _stub_full_family_scan(monkeypatch)
    monkeypatch.setattr(evaluator_module, "fdr_filter", lambda edges, fdr_alpha=0.10: list(edges))

    decisions = evaluator_module.evaluate_candidate(
        candidate,
        conn=conn,
        portfolio=PortfolioState(bankroll=150.0),
        clob=type("DummyClob", (), {"get_best_bid_ask": lambda self, token_id: (0.34, 0.36, 20.0, 20.0)})(),
        limits=evaluator_module.RiskLimits(),
        decision_time=datetime(2026, 1, 14, 6, 0, tzinfo=timezone.utc),
    )

    assert len(decisions) == 1
    assert captured["forecast_source"] == "tigge"
    assert captured["bias_reference"]["source"] == "tigge"
    assert captured["bias_reference"]["bias"] == 1.0


def _patch_day0_ens_prefix(monkeypatch):
    class DummyEnsembleSignal:
        def __init__(self, *args, **kwargs):
            self.member_maxes = np.full(51, 60.0)
            self.bias_corrected = False

        def spread_float(self):
            return 0.0

        def is_bimodal(self):
            return False

    monkeypatch.setattr(evaluator_module, "fetch_ensemble", lambda *args, **kwargs: {
        "members_hourly": np.zeros((51, 24)),
        "times": ["2026-04-01T00:00:00+00:00"],
        **_entry_forecast_evidence(
            issue_time=datetime(2026, 4, 1, tzinfo=timezone.utc),
            first_valid_time=datetime(2026, 4, 1, tzinfo=timezone.utc),
            fetch_time=datetime(2026, 4, 1, tzinfo=timezone.utc),
        ),
    })
    monkeypatch.setattr(evaluator_module, "validate_ensemble", lambda *args, **kwargs: True)
    monkeypatch.setattr(evaluator_module, "EnsembleSignal", DummyEnsembleSignal)


def test_day0_solar_context_failure_is_pre_vector_traceable(tmp_path, monkeypatch):
    _patch_day0_ens_prefix(monkeypatch)
    monkeypatch.setattr(evaluator_module, "_get_day0_temporal_context", lambda *args, **kwargs: None)
    candidate = MarketCandidate(
        city=NYC,
        target_date="2026-04-01",
        outcomes=_three_outcomes(),
        hours_since_open=12.0,
        hours_to_resolution=4.0,
        observation={
            "high_so_far": 60.0,
            "current_temp": 59.0,
            "observation_time": "2026-04-01T16:00:00+00:00",
            "source": "wu_api",
        },
        discovery_mode=DiscoveryMode.DAY0_CAPTURE.value,
    )

    decision, result = _trace_status_for_evaluator_decision(tmp_path, candidate)

    assert decision.rejection_stage == "SIGNAL_QUALITY"
    assert decision.availability_status == "DATA_STALE"
    assert result["trace_status"] == "pre_vector_unavailable"


def test_day0_no_remaining_forecast_hours_is_pre_vector_traceable(tmp_path, monkeypatch):
    _patch_day0_ens_prefix(monkeypatch)
    monkeypatch.setattr(
        evaluator_module,
        "_get_day0_temporal_context",
        lambda *args, **kwargs: types.SimpleNamespace(current_utc_timestamp=datetime(2026, 4, 1, 16, tzinfo=timezone.utc)),
    )
    monkeypatch.setattr(evaluator_module, "remaining_member_extrema_for_day0", lambda *args, **kwargs: (None, 0.0))
    candidate = MarketCandidate(
        city=NYC,
        target_date="2026-04-01",
        outcomes=_three_outcomes(),
        hours_since_open=12.0,
        hours_to_resolution=4.0,
        observation={
            "high_so_far": 60.0,
            "current_temp": 59.0,
            "observation_time": "2026-04-01T16:00:00+00:00",
            "source": "wu_api",
        },
        discovery_mode=DiscoveryMode.DAY0_CAPTURE.value,
    )

    decision, result = _trace_status_for_evaluator_decision(tmp_path, candidate)

    assert decision.rejection_stage == "SIGNAL_QUALITY"
    assert decision.availability_status == "DATA_STALE"
    assert result["trace_status"] == "pre_vector_unavailable"


def test_live_dynamic_cap_flows_to_evaluator(monkeypatch, tmp_path):
    db_path = tmp_path / "zeus.db"
    conn = get_connection(db_path)
    init_schema(conn)
    conn.close()
    portfolio = PortfolioState(positions=[_position(size_usd=20.0, shares=50.0, cost_basis_usd=20.0)])
    captured: dict[str, float] = {}

    class DummyClob:
        def __init__(self):
            pass

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

    monkeypatch.setattr(cycle_runner, "settings", _CycleSettingsStub(capital_base_usd=150.0, smoke_test_portfolio_cap_usd=None))
    monkeypatch.setattr(cycle_runner, "get_current_level", lambda: RiskLevel.GREEN)
    monkeypatch.setattr(cycle_runner, "get_connection", lambda: get_connection(db_path))
    monkeypatch.setattr(cycle_runner, "load_portfolio", lambda: portfolio)
    monkeypatch.setattr(cycle_runner, "save_portfolio", lambda state, *args, **kwargs: None)
    monkeypatch.setattr(cycle_runner, "PolymarketClient", DummyClob)
    monkeypatch.setattr(cycle_runner, "get_tracker", lambda: StrategyTracker())
    monkeypatch.setattr(cycle_runner, "save_tracker", lambda tracker: None)
    monkeypatch.setattr(cycle_runner, "get_last_scan_authority", lambda: "VERIFIED")
    _market_list = [{
        "city": NYC,
        "target_date": "2026-04-01",
        "outcomes": [],
        "hours_since_open": 2.0,
        "hours_to_resolution": 30.0,
        "temperature_metric": "high",
    }]
    monkeypatch.setattr(cycle_runner, "find_weather_markets", lambda **kwargs: _market_list)
    monkeypatch.setattr("src.data.market_scanner.find_weather_markets", lambda **kwargs: _market_list)

    def _dummy_refresh(conn, clob, pos):
        from src.contracts import EdgeContext, EntryMethod
        pos.entry_method = getattr(pos, "entry_method", EntryMethod.ENS_MEMBER_COUNTING.value)
        assert pos.entry_method
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
    monkeypatch.setattr(cycle_runner, "get_force_exit_review", lambda: False)
    monkeypatch.setattr(cycle_runner, "_run_chain_sync", lambda portfolio, clob, conn: ({}, True))

    def _capture_eval(candidate, conn, portfolio, clob, limits, entry_bankroll=None, **kwargs):
        captured["entry_bankroll"] = entry_bankroll
        return []

    monkeypatch.setattr(cycle_runner, "evaluate_candidate", _capture_eval)
    monkeypatch.setattr(cycle_runner, "is_strategy_enabled", lambda strategy: True)
    _allow_entry_gates_for_runtime_test(monkeypatch)

    cycle_runner.run_cycle(DiscoveryMode.OPENING_HUNT)

    # P7: wallet_balance is primary; config_cap is upper bound. Exposure no longer added.
    # wallet=$100, config_cap=$200 u2192 effective_bankroll=min(100, 200)=100.
    assert captured["entry_bankroll"] == pytest.approx(100.0)


def test_execute_discovery_phase_logs_rejected_live_entry_telemetry(monkeypatch, tmp_path):
    db_path = tmp_path / "zeus.db"
    conn = get_connection(db_path)
    init_schema(conn)
    _insert_executable_snapshot(
        conn,
        snapshot_id="snap-reject-exec",
        selected_outcome_token_id="yes1",
        outcome_label="YES",
        yes_token_id="yes1",
        no_token_id="no1",
        condition_id="m1",
        top_bid="0.25",
        top_ask="0.25",
    )
    conn.commit()
    conn.close()
    portfolio = PortfolioState(bankroll=150.0)

    class DummyClob:
        def __init__(self):
            self.paper_mode = False

        def get_positions_from_api(self):
            return []

        def get_open_orders(self):
            return []

        def get_balance(self):
            return 100.0

        def get_best_bid_ask(self, token_id):
            return (0.25, 0.25, 20.0, 20.0)

    class DummyDecision:
        def __init__(self):
            self.should_trade = True
            self.edge = _edge()
            self.tokens = {
                "market_id": "m1",
                "token_id": "yes1",
                "no_token_id": "no1",
                "executable_snapshot_id": "snap-reject-exec",
                "executable_snapshot_min_tick_size": "0.01",
                "executable_snapshot_min_order_size": "5",
                "executable_snapshot_neg_risk": False,
            }
            self.size_usd = 5.0
            self.decision_id = "d-reject"
            self.rejection_stage = ""
            self.rejection_reasons = []
            self.selected_method = "ens_member_counting"
            self.applied_validations = ["ens_fetch"]
            self.decision_snapshot_id = "snap-reject"
            self.edge_source = "opening_inertia"
            self.strategy_key = "opening_inertia"
            self.edge_context = None
            self.settlement_semantics_json = '{"measurement_unit":"F"}'
            self.epistemic_context_json = '{"decision_time_utc":"2026-04-01T00:00:00Z"}'
            self.edge_context_json = '{"forward_edge":0.12}'
            self.sizing_bankroll = 100.0
            self.kelly_multiplier_used = 0.25
            self.execution_fee_rate = 0.0
            self.safety_cap_usd = None

    monkeypatch.setattr(cycle_runner, "get_current_level", lambda: RiskLevel.GREEN)
    monkeypatch.setattr(cycle_runner, "get_connection", lambda: get_connection(db_path))
    monkeypatch.setattr("src.state.db.get_trade_connection_with_world", lambda mode: get_connection(db_path))
    monkeypatch.setattr(cycle_runner, "load_portfolio", lambda: portfolio)
    monkeypatch.setattr(cycle_runner, "save_portfolio", lambda state, *args, **kwargs: None)
    monkeypatch.setattr(cycle_runner, "PolymarketClient", DummyClob)
    monkeypatch.setattr(cycle_runner, "get_tracker", lambda: StrategyTracker())
    monkeypatch.setattr(cycle_runner, "save_tracker", lambda tracker: None)
    monkeypatch.setattr(cycle_runner, "get_last_scan_authority", lambda: "VERIFIED")
    monkeypatch.setattr(cycle_runner, "find_weather_markets", lambda **kwargs: [{
        "city": NYC,
        "target_date": "2026-04-01",
        "hours_since_open": 12.0,
        "hours_to_resolution": 24.0,
        "temperature_metric": "high",
        "outcomes": [{"title": "39-40°F", "range_low": 39, "range_high": 40, "token_id": "yes1", "no_token_id": "no1", "market_id": "m1", "price": 0.35}],
    }])
    monkeypatch.setattr(cycle_runner, "evaluate_candidate", lambda *args, **kwargs: [DummyDecision()])
    monkeypatch.setattr(
        cycle_runner,
        "create_execution_intent",
        lambda **kwargs: (_ for _ in ()).throw(
            AssertionError("live final-intent path must not create legacy intent")
        ),
    )
    monkeypatch.setattr(
        cycle_runner,
        "execute_intent",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("live final-intent path must not submit legacy intent")
        ),
    )
    monkeypatch.setattr(
        cycle_runner,
        "execute_final_intent",
        lambda intent, **kwargs: OrderResult(
            status="rejected",
            trade_id="rt-reject",
            order_id="o-reject",
            submitted_price=float(intent.final_limit_price),
            reason="insufficient_liquidity",
        ),
        raising=False,
    )
    monkeypatch.setattr(
        cycle_runner,
        "select_final_order_type",
        lambda conn, snapshot_id: "FOK",
        raising=False,
    )
    monkeypatch.setattr("src.control.control_plane.process_commands", lambda: [])
    monkeypatch.setattr("src.observability.status_summary.write_status", lambda cycle_summary=None: None)
    monkeypatch.setattr("src.engine.monitor_refresh.refresh_position", lambda conn, clob, pos: (_ for _ in ()).throw(AssertionError("monitor not expected")))
    monkeypatch.setattr(cycle_runner, "get_force_exit_review", lambda: False)
    monkeypatch.setattr(cycle_runner, "_run_chain_sync", lambda portfolio, clob, conn: ({}, True))
    monkeypatch.setattr(cycle_runner, "is_strategy_enabled", lambda strategy: True)
    _allow_entry_gates_for_runtime_test(monkeypatch)

    cycle_runner.run_cycle(DiscoveryMode.OPENING_HUNT)

    conn = get_connection(db_path)
    # Post-P9: rejected entry telemetry goes to execution_fact, not position_events
    exec_row = conn.execute(
        "SELECT terminal_exec_status FROM execution_fact WHERE position_id = ? AND order_role = 'entry'",
        ("rt-reject",),
    ).fetchone()
    conn.close()

    assert exec_row is not None
    assert exec_row["terminal_exec_status"] == "rejected"


def test_strategy_gate_blocks_trade_execution(monkeypatch, tmp_path):
    db_path = tmp_path / "zeus.db"
    conn = get_connection(db_path)
    init_schema(conn)
    conn.close()
    portfolio = PortfolioState(bankroll=150.0)

    class DummyClob:
        def __init__(self):
            pass
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
    monkeypatch.setattr(cycle_runner, "save_portfolio", lambda state, *args, **kwargs: None)
    monkeypatch.setattr(cycle_runner, "PolymarketClient", DummyClob)
    monkeypatch.setattr(cycle_runner, "get_tracker", lambda: StrategyTracker())
    monkeypatch.setattr(cycle_runner, "save_tracker", lambda tracker: None)
    monkeypatch.setattr(cycle_runner, "get_last_scan_authority", lambda: "VERIFIED")
    # P3-fix1c (post-review side-fix, 2026-04-26): market dict needs
    # temperature_metric — P2-fix3 routes via _normalize_temperature_metric
    # which now raises on missing/invalid (post-A3 antibody).
    monkeypatch.setattr(cycle_runner, "find_weather_markets", lambda **kwargs: [{
        "city": NYC,
        "target_date": "2026-04-01",
        "hours_since_open": 1.0,
        "hours_to_resolution": 24.0,
        "temperature_metric": "high",
        "outcomes": [{"title": "39-40°F", "range_low": 39, "range_high": 40, "token_id": "yes1", "no_token_id": "no1", "market_id": "m1", "price": 0.35}],
    }])
    monkeypatch.setattr(cycle_runner, "evaluate_candidate", lambda *args, **kwargs: [DummyDecision()])
    monkeypatch.setattr(cycle_runner, "is_strategy_enabled", lambda strategy: strategy != "opening_inertia")
    monkeypatch.setattr(cycle_runner, "create_execution_intent", lambda **kwargs: (_ for _ in ()).throw(AssertionError("should not execute gated strategy")))
    monkeypatch.setattr("src.control.control_plane.process_commands", lambda: [])
    monkeypatch.setattr("src.observability.status_summary.write_status", lambda cycle_summary=None: None)
    monkeypatch.setattr("src.engine.monitor_refresh.refresh_position", lambda conn, clob, pos: (_ for _ in ()).throw(AssertionError("monitor not expected")))
    monkeypatch.setattr(cycle_runner, "get_force_exit_review", lambda: False)
    monkeypatch.setattr(cycle_runner, "_run_chain_sync", lambda portfolio, clob, conn: ({}, True))
    _allow_entry_gates_for_runtime_test(monkeypatch)

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
    assert payload["no_trade_cases"][0]["market_hours_open"] == 1.0


def test_strategy_phase_gate_blocks_key_mode_mismatch(monkeypatch, tmp_path):
    db_path = tmp_path / "zeus.db"
    conn = get_connection(db_path)
    init_schema(conn)
    conn.close()
    portfolio = PortfolioState(bankroll=150.0)

    class DummyClob:
        def __init__(self):
            pass
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
            self.decision_id = "d-phase-mismatch"
            self.rejection_stage = ""
            self.rejection_reasons = []
            self.selected_method = "ens_member_counting"
            self.applied_validations = ["ens_fetch"]
            self.decision_snapshot_id = "snap-phase-mismatch"
            self.edge_source = "center_buy"
            self.strategy_key = "center_buy"
            self.edge_context = None
            self.settlement_semantics_json = '{"measurement_unit":"F"}'
            self.epistemic_context_json = '{"decision_time_utc":"2026-04-01T00:00:00Z"}'
            self.edge_context_json = '{"forward_edge":0.12}'

    monkeypatch.setattr(cycle_runner, "get_current_level", lambda: RiskLevel.GREEN)
    monkeypatch.setattr(cycle_runner, "get_connection", lambda: get_connection(db_path))
    monkeypatch.setattr(cycle_runner, "load_portfolio", lambda: portfolio)
    monkeypatch.setattr(cycle_runner, "save_portfolio", lambda state, *args, **kwargs: None)
    monkeypatch.setattr(cycle_runner, "PolymarketClient", DummyClob)
    monkeypatch.setattr(cycle_runner, "get_tracker", lambda: StrategyTracker())
    monkeypatch.setattr(cycle_runner, "save_tracker", lambda tracker: None)
    monkeypatch.setattr(cycle_runner, "get_last_scan_authority", lambda: "VERIFIED")
    monkeypatch.setattr(cycle_runner, "find_weather_markets", lambda **kwargs: [{
        "city": NYC,
        "target_date": "2026-04-01",
        "hours_since_open": 1.0,
        "hours_to_resolution": 24.0,
        "temperature_metric": "high",
        "outcomes": [{"title": "39-40°F", "range_low": 39, "range_high": 40, "token_id": "yes1", "no_token_id": "no1", "market_id": "m1", "price": 0.35}],
    }])
    monkeypatch.setattr(cycle_runner, "evaluate_candidate", lambda *args, **kwargs: [DummyDecision()])
    monkeypatch.setattr(cycle_runner, "is_strategy_enabled", lambda strategy: True)
    monkeypatch.setattr(cycle_runner, "create_execution_intent", lambda **kwargs: (_ for _ in ()).throw(AssertionError("phase-mismatched strategy must not execute")))
    monkeypatch.setattr("src.control.control_plane.process_commands", lambda: [])
    monkeypatch.setattr("src.observability.status_summary.write_status", lambda cycle_summary=None: None)
    monkeypatch.setattr("src.engine.monitor_refresh.refresh_position", lambda conn, clob, pos: (_ for _ in ()).throw(AssertionError("monitor not expected")))
    monkeypatch.setattr(cycle_runner, "get_force_exit_review", lambda: False)
    monkeypatch.setattr(cycle_runner, "_run_chain_sync", lambda portfolio, clob, conn: ({}, True))
    _allow_entry_gates_for_runtime_test(monkeypatch)

    summary = cycle_runner.run_cycle(DiscoveryMode.OPENING_HUNT)
    conn = get_connection(db_path)
    artifact = conn.execute("SELECT artifact_json FROM decision_log ORDER BY id DESC LIMIT 1").fetchone()
    conn.close()
    payload = json.loads(artifact["artifact_json"])

    assert summary["strategy_phase_rejections"] == 1
    assert payload["trade_cases"] == []
    assert payload["no_trade_cases"][0]["rejection_stage"] == "SIGNAL_QUALITY"
    assert payload["no_trade_cases"][0]["strategy"] == "center_buy"
    assert payload["no_trade_cases"][0]["edge_source"] == "center_buy"
    assert payload["no_trade_cases"][0]["rejection_reasons"] == [
        "strategy_phase_mismatch:center_buy:opening_hunt"
    ]
    assert payload["no_trade_cases"][0]["market_hours_open"] == 1.0


def test_shoulder_sell_is_phase_compatible_but_runtime_live_blocked(monkeypatch, tmp_path):
    from dataclasses import replace

    _set_native_multibin_buy_no_flags(monkeypatch, shadow=True, live=True)
    monkeypatch.setattr(control_plane_module, "_control_state", {})
    conn = get_connection(tmp_path / "shoulder-sell-runtime-live-blocked.db")
    init_schema(conn)
    artifact = CycleArtifact(mode=DiscoveryMode.UPDATE_REACTION.value, started_at="2026-04-03T00:00:00Z")
    summary = {"candidates": 0, "no_trades": 0}
    market = {
        "city": NYC,
        "target_date": "2026-04-01",
        "hours_since_open": 30.0,
        "hours_to_resolution": 24.0,
        "event_id": "evt-shoulder-sell-gate",
        "slug": "slug-shoulder-sell-gate",
        "temperature_metric": "high",
        "outcomes": [
            {"title": "39°F or lower", "range_low": None, "range_high": 39, "token_id": "yes0", "no_token_id": "no0", "market_id": "m0"},
            {"title": "40°F or higher", "range_low": 40, "range_high": None, "token_id": "yes1", "no_token_id": "no1", "market_id": "m1"},
        ],
    }
    shoulder_no_edge = replace(
        _edge(),
        bin=Bin(low=None, high=39, label="39°F or lower", unit="F"),
        direction="buy_no",
        p_market=0.35,
        entry_price=0.35,
        vwmp=0.35,
        p_posterior=0.62,
        edge=0.27,
        forward_edge=0.27,
    )
    decision = EdgeDecision(
        should_trade=True,
        edge=shoulder_no_edge,
        tokens={"market_id": "m0", "token_id": "yes0", "no_token_id": "no0"},
        size_usd=5.0,
        decision_id="d-shoulder-sell-runtime-blocked",
        selected_method="ens_member_counting",
        applied_validations=[evaluator_module.NATIVE_BUY_NO_QUOTE_AVAILABLE_VALIDATION],
        decision_snapshot_id="model-snap-shoulder-sell-runtime-blocked",
        edge_source="shoulder_sell",
        strategy_key="shoulder_sell",
        settlement_semantics_json='{"measurement_unit":"F"}',
        epistemic_context_json='{"decision_time_utc":"2026-04-01T00:00:00Z"}',
        edge_context_json='{"forward_edge":0.27}',
        sizing_bankroll=100.0,
        kelly_multiplier_used=0.25,
        execution_fee_rate=0.0,
        safety_cap_usd=None,
    )

    deps = types.SimpleNamespace(
        MODE_PARAMS={DiscoveryMode.UPDATE_REACTION: {}},
        find_weather_markets=lambda **kwargs: [market],
        get_last_scan_authority=lambda: "VERIFIED",
        DiscoveryMode=DiscoveryMode,
        logger=types.SimpleNamespace(warning=lambda *args, **kwargs: None, error=lambda *args, **kwargs: None),
        NoTradeCase=NoTradeCase,
        MarketCandidate=MarketCandidate,
        evaluate_candidate=lambda *args, **kwargs: [decision],
        is_strategy_enabled=control_plane_module.is_strategy_enabled,
        _classify_edge_source=lambda _mode, _edge: "shoulder_sell",
        create_execution_intent=lambda **kwargs: (_ for _ in ()).throw(AssertionError("should not submit shoulder_sell")),
        execute_intent=lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("should not execute shoulder_sell")),
    )

    cycle_runtime.execute_discovery_phase(
        conn,
        clob=None,
        portfolio=PortfolioState(),
        artifact=artifact,
        tracker=StrategyTracker(),
        limits=types.SimpleNamespace(),
        mode=DiscoveryMode.UPDATE_REACTION,
        summary=summary,
        entry_bankroll=100.0,
        decision_time=datetime(2026, 4, 3, tzinfo=timezone.utc),
        env="live",
        deps=deps,
    )
    conn.close()

    assert cycle_runtime._strategy_phase_rejection_reason("shoulder_sell", DiscoveryMode.UPDATE_REACTION) is None
    assert summary["no_trades"] == 1
    assert summary.get("strategy_phase_rejections", 0) == 0
    assert summary["strategy_gate_rejections"] == 1
    assert artifact.no_trade_cases[0].rejection_stage == "RISK_REJECTED"
    assert artifact.no_trade_cases[0].strategy == "shoulder_sell"
    assert artifact.no_trade_cases[0].rejection_reasons == [
        "strategy_gate_disabled:shoulder_sell"
    ]


@pytest.mark.parametrize("risk_level", [RiskLevel.YELLOW, RiskLevel.ORANGE])
def test_elevated_risk_still_runs_monitoring_and_reports_block_reason(monkeypatch, tmp_path, risk_level):
    db_path = tmp_path / "zeus.db"
    conn = get_connection(db_path)
    init_schema(conn)
    conn.close()
    portfolio = PortfolioState(positions=[_position()])

    class DummyClob:
        def __init__(self):
            pass

        def get_positions_from_api(self):
            return []

        def get_balance(self):
            return 100.0

        def get_open_orders(self):
            return []

    monitored: list[str] = []

    monkeypatch.setattr(cycle_runner, "get_current_level", lambda: risk_level)
    monkeypatch.setattr(cycle_runner, "get_connection", lambda: get_connection(db_path))
    monkeypatch.setattr(cycle_runner, "load_portfolio", lambda: portfolio)
    monkeypatch.setattr(cycle_runner, "save_portfolio", lambda state, *args, **kwargs: None)
    monkeypatch.setattr(cycle_runner, "PolymarketClient", DummyClob)
    monkeypatch.setattr(cycle_runner, "get_tracker", lambda: StrategyTracker())
    monkeypatch.setattr(cycle_runner, "save_tracker", lambda tracker: None)
    monkeypatch.setattr(cycle_runner, "find_weather_markets", lambda **kwargs: [])
    monkeypatch.setattr(cycle_runner, "cities_by_name", {"NYC": NYC}, raising=False)

    def _tracking_refresh(conn, clob, pos):
        from src.contracts import EdgeContext, EntryMethod
        pos.entry_method = getattr(pos, "entry_method", EntryMethod.ENS_MEMBER_COUNTING.value)
        assert pos.entry_method
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
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("entries should stay blocked at elevated risk")),
    )

    summary = cycle_runner.run_cycle(DiscoveryMode.OPENING_HUNT)

    assert monitored == ["t1"]
    assert summary["monitors"] == 1
    assert summary["entries_blocked_reason"] == f"risk_level={risk_level.value}"
    assert summary["candidates"] == 0


def test_force_exit_review_scope_is_entry_block_only(monkeypatch, tmp_path):
    db_path = tmp_path / "zeus.db"
    conn = get_connection(db_path)
    init_schema(conn)
    conn.close()
    portfolio = PortfolioState(positions=[_position(target_date="2026-12-01")])

    class DummyClob:
        def __init__(self):
            pass

        def get_balance(self):
            return 100.0

    monitored: list[str] = []

    monkeypatch.setattr(cycle_runner, "get_current_level", lambda: RiskLevel.GREEN)
    monkeypatch.setattr(cycle_runner, "get_force_exit_review", lambda: True)
    monkeypatch.setattr(cycle_runner, "get_connection", lambda: get_connection(db_path))
    monkeypatch.setattr(cycle_runner, "load_portfolio", lambda: portfolio)
    monkeypatch.setattr(cycle_runner, "save_portfolio", lambda state, *args, **kwargs: None)
    monkeypatch.setattr(cycle_runner, "PolymarketClient", DummyClob)
    monkeypatch.setattr(cycle_runner, "get_tracker", lambda: StrategyTracker())
    monkeypatch.setattr(cycle_runner, "save_tracker", lambda tracker: None)
    monkeypatch.setattr(cycle_runner, "is_entries_paused", lambda: False)
    monkeypatch.setattr(
        cycle_runner,
        "_reconcile_pending_positions",
        lambda *args, **kwargs: {"entered": 0, "voided": 0, "dirty": False, "tracker_dirty": False},
    )
    monkeypatch.setattr(cycle_runner, "_run_chain_sync", lambda portfolio, clob, conn: ({}, True))
    monkeypatch.setattr(cycle_runner, "_cleanup_orphan_open_orders", lambda portfolio, clob: 0)
    monkeypatch.setattr(
        cycle_runner,
        "_entry_bankroll_for_cycle",
        lambda portfolio, clob: (100.0, {"portfolio_initial_bankroll_usd": 100.0}),
    )

    def _monitor(conn, clob, portfolio, artifact, tracker, summary):
        monitored.extend(pos.trade_id for pos in portfolio.positions)
        summary["monitors"] += len(portfolio.positions)
        return False, False

    monkeypatch.setattr(cycle_runner, "_execute_monitoring_phase", _monitor)
    monkeypatch.setattr("src.control.control_plane.process_commands", lambda: [])
    monkeypatch.setattr("src.observability.status_summary.write_status", lambda cycle_summary=None: None)
    monkeypatch.setattr(
        cycle_runner,
        "_execute_discovery_phase",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("entries should stay blocked")),
    )

    summary = cycle_runner.run_cycle(DiscoveryMode.OPENING_HUNT)

    # Phase 9B DT#2 / R-BV: scope widened from "entry_block_only" to
    # "sweep_active_positions". Pre-P9B this test asserted entry-block-only
    # scope; P9B lands the sweep so the assertion flips to
    # "sweep_active_positions" AND the sweep mark is visible on the position.
    # This is a critic-beth-style "stale antibody flip at guard-removal"
    # update — the test is intentionally re-purposed for the new law.
    assert monitored == ["t1"]
    assert summary["force_exit_review"] is True
    assert summary["force_exit_review_scope"] == "sweep_active_positions"
    assert summary["force_exit_sweep"]["attempted"] == 1, (
        f"Phase 9B R-BV: sweep should have marked 1 active position; "
        f"got summary={summary.get('force_exit_sweep')!r}"
    )
    # Position must carry the sweep exit_reason (exit_lifecycle picks it up next cycle)
    assert portfolio.positions[0].exit_reason == "red_force_exit"
    assert summary["entries_blocked_reason"] == "force_exit_review_daily_loss_red"
    assert summary["monitors"] == 1
    assert summary["candidates"] == 0


def test_entries_paused_reports_block_reason(monkeypatch, tmp_path):
    db_path = tmp_path / "zeus.db"
    conn = get_connection(db_path)
    init_schema(conn)
    conn.close()
    portfolio = PortfolioState(positions=[_position(size_usd=0.0, cost_basis_usd=0.0, target_date="2026-12-01")])

    class DummyClob:
        def __init__(self):
            pass

    monkeypatch.setattr(cycle_runner, "get_current_level", lambda: RiskLevel.GREEN)
    monkeypatch.setattr(cycle_runner, "get_force_exit_review", lambda: False)
    monkeypatch.setattr(cycle_runner, "get_connection", lambda: get_connection(db_path))
    monkeypatch.setattr(cycle_runner, "load_portfolio", lambda: portfolio)
    monkeypatch.setattr(cycle_runner, "save_portfolio", lambda state, *args, **kwargs: None)
    monkeypatch.setattr(cycle_runner, "PolymarketClient", DummyClob)
    monkeypatch.setattr(cycle_runner, "get_tracker", lambda: StrategyTracker())
    monkeypatch.setattr(cycle_runner, "save_tracker", lambda tracker: None)
    monkeypatch.setattr(cycle_runner, "is_entries_paused", lambda: True)
    monkeypatch.setattr(
        cycle_runner,
        "_reconcile_pending_positions",
        lambda *args, **kwargs: {"entered": 0, "voided": 0, "dirty": False, "tracker_dirty": False},
    )
    monkeypatch.setattr(cycle_runner, "_run_chain_sync", lambda portfolio, clob, conn: ({}, True))
    monkeypatch.setattr(cycle_runner, "_cleanup_orphan_open_orders", lambda portfolio, clob: 0)
    monkeypatch.setattr(
        cycle_runner,
        "_entry_bankroll_for_cycle",
        lambda portfolio, clob: (100.0, {"portfolio_initial_bankroll_usd": 100.0}),
    )
    monitored: list[str] = []

    def _monitor(conn, clob, portfolio, artifact, tracker, summary):
        monitored.extend(pos.trade_id for pos in portfolio.positions)
        summary["monitors"] += len(portfolio.positions)
        return False, False

    monkeypatch.setattr(cycle_runner, "_execute_monitoring_phase", _monitor)
    monkeypatch.setattr("src.control.control_plane.process_commands", lambda: [])
    monkeypatch.setattr("src.observability.status_summary.write_status", lambda cycle_summary=None: None)
    monkeypatch.setattr(
        cycle_runner,
        "_execute_discovery_phase",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("entries should stay paused")),
    )

    summary = cycle_runner.run_cycle(DiscoveryMode.OPENING_HUNT)

    assert summary["entries_paused"] is True
    assert summary["entries_blocked_reason"] == "entries_paused"
    assert monitored == ["t1"]
    assert summary["monitors"] == 1
    assert summary["candidates"] == 0


def test_run_cycle_surfaces_fdr_family_scan_failure_without_entries(monkeypatch, tmp_path):
    db_path = tmp_path / "zeus.db"
    conn = get_connection(db_path)
    init_schema(conn)
    conn.close()

    class DummyClob:
        def __init__(self):
            pass

        def get_balance(self):
            return 100.0

    monkeypatch.setattr(cycle_runner, "get_current_level", lambda: RiskLevel.GREEN)
    monkeypatch.setattr(cycle_runner, "get_force_exit_review", lambda: False)
    monkeypatch.setattr(cycle_runner, "get_connection", lambda: get_connection(db_path))
    monkeypatch.setattr(cycle_runner, "load_portfolio", lambda: PortfolioState())
    monkeypatch.setattr(cycle_runner, "save_portfolio", lambda state, *args, **kwargs: None)
    monkeypatch.setattr(cycle_runner, "PolymarketClient", DummyClob)
    monkeypatch.setattr(cycle_runner, "get_tracker", lambda: StrategyTracker())
    monkeypatch.setattr(cycle_runner, "save_tracker", lambda tracker: None)
    monkeypatch.setattr(cycle_runner, "is_entries_paused", lambda: False)
    monkeypatch.setattr(
        cycle_runner,
        "_reconcile_pending_positions",
        lambda *args, **kwargs: {"entered": 0, "voided": 0, "dirty": False, "tracker_dirty": False},
    )
    monkeypatch.setattr(cycle_runner, "_run_chain_sync", lambda portfolio, clob, conn: ({}, True))
    monkeypatch.setattr(cycle_runner, "_cleanup_orphan_open_orders", lambda portfolio, clob: 0)
    monkeypatch.setattr(cycle_runner, "_execute_monitoring_phase", lambda *args, **kwargs: (False, False))
    monkeypatch.setattr("src.control.control_plane.process_commands", lambda: [])
    monkeypatch.setattr("src.observability.status_summary.write_status", lambda cycle_summary=None: None)
    monkeypatch.setattr(cycle_runner, "get_last_scan_authority", lambda: "VERIFIED")
    monkeypatch.setattr(
        cycle_runner,
        "find_weather_markets",
        lambda **kwargs: [
            {
                "city": NYC,
                "target_date": "2026-12-01",
                "hours_since_open": 1.0,
                "hours_to_resolution": 24.0,
                "temperature_metric": "high",
                "outcomes": [
                    {
                        "title": "39-40°F",
                        "range_low": 39,
                        "range_high": 40,
                        "token_id": "yes1",
                        "no_token_id": "no1",
                        "market_id": "m1",
                        "price": 0.35,
                    }
                ],
            }
        ],
    )
    monkeypatch.setattr(
        cycle_runner,
        "evaluate_candidate",
        lambda *args, **kwargs: [
            EdgeDecision(
                should_trade=False,
                decision_id="fdr-down",
                rejection_stage="FDR_FAMILY_SCAN_UNAVAILABLE",
                rejection_reasons=["full-family FDR scan unavailable; entry selection failed closed"],
                fdr_fallback_fired=True,
                fdr_family_size=0,
            )
        ],
    )
    monkeypatch.setattr(cycle_runner, "is_strategy_enabled", lambda strategy: True)
    _allow_entry_gates_for_runtime_test(monkeypatch)

    summary = cycle_runner.run_cycle(DiscoveryMode.OPENING_HUNT)
    conn = get_connection(db_path)
    artifact_row = conn.execute("SELECT artifact_json FROM decision_log ORDER BY id DESC LIMIT 1").fetchone()
    conn.close()
    artifact_payload = json.loads(artifact_row["artifact_json"])

    assert summary["fdr_fallback_fired"] is True
    assert summary["trades"] == 0
    assert summary["no_trades"] == 1
    assert summary["candidates"] == 1
    assert artifact_payload["no_trade_cases"][0]["rejection_stage"] == "FDR_FAMILY_SCAN_UNAVAILABLE"
    assert artifact_payload["no_trade_cases"][0]["rejection_reasons"] == [
        "full-family FDR scan unavailable; entry selection failed closed"
    ]


def test_only_green_risk_allows_new_entries():
    assert cycle_runner._risk_allows_new_entries(RiskLevel.GREEN) is True
    assert cycle_runner._risk_allows_new_entries(RiskLevel.YELLOW) is False
    assert cycle_runner._risk_allows_new_entries(RiskLevel.ORANGE) is False
    assert cycle_runner._risk_allows_new_entries(RiskLevel.RED) is False


def test_chain_quarantine_keeps_direction_unknown():
    portfolio = PortfolioState()
    stats = reconcile(
        portfolio,
        [ChainPosition(token_id="yes123", size=12.0, avg_price=0.42, condition_id="cond-1")],
    )

    assert stats["quarantined"] == 1
    pos = portfolio.positions[0]
    assert pos.direction == "unknown"
    assert pos.state == "quarantined"
    assert pos.chain_state == "quarantined"
    assert pos.strategy == ""


def test_chain_quarantine_fails_closed_when_fact_write_fails(caplog):
    class GuardConn:
        def execute(self, *_args, **_kwargs):
            raise RuntimeError("db write unavailable")

    portfolio = PortfolioState()
    with caplog.at_level("WARNING"):
        with pytest.raises(RuntimeError, match="chain-only quarantine fact write failed"):
            reconcile(
                portfolio,
                [ChainPosition(token_id="yes123", size=12.0, avg_price=0.42, condition_id="cond-1")],
                conn=GuardConn(),
            )

    assert portfolio.positions == []
    assert "EXCLUDED FROM CANONICAL MIGRATION" in caplog.text


def test_chain_only_quarantine_persists_reconciliation_fact_without_strategy_default(tmp_path):
    db_path = tmp_path / "zeus.db"
    conn = get_connection(db_path)
    init_schema(conn)
    portfolio = PortfolioState()

    stats = reconcile(
        portfolio,
        [ChainPosition(token_id="yes-chain-only", size=12.0, avg_price=0.42, cost=5.04, condition_id="cond-1")],
        conn=conn,
    )

    fact = conn.execute(
        """
        SELECT suppression_reason, source_module, evidence_json
        FROM token_suppression
        WHERE token_id = 'yes-chain-only'
        """
    ).fetchone()
    canonical_count = conn.execute(
        "SELECT COUNT(*) FROM position_current WHERE token_id = 'yes-chain-only'"
    ).fetchone()[0]
    conn.close()

    assert stats["quarantined"] == 1
    assert canonical_count == 0
    assert fact["suppression_reason"] == "chain_only_quarantined"
    assert fact["source_module"] == "src.state.chain_reconciliation"
    evidence = json.loads(fact["evidence_json"])
    assert evidence["condition_id"] == "cond-1"
    assert evidence["size"] == 12.0
    assert evidence["avg_price"] == 0.42
    assert portfolio.positions[0].strategy == ""


def test_load_portfolio_rehydrates_chain_only_quarantine_fact(tmp_path):
    db_path = tmp_path / "zeus.db"
    path = tmp_path / "positions-cache.json"
    conn = get_connection(db_path)
    init_schema(conn)
    conn.execute(
        """
        INSERT INTO token_suppression (
            token_id, condition_id, suppression_reason, source_module,
            created_at, updated_at, evidence_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "yes-chain-only",
            "cond-1",
            "chain_only_quarantined",
            "test",
            "2026-04-04T00:00:00Z",
            "2026-04-04T00:00:00Z",
            json.dumps({
                "size": 12.0,
                "avg_price": 0.42,
                "cost": 5.04,
                "condition_id": "cond-1",
            }),
        ),
    )
    conn.commit()
    conn.close()
    path.write_text(json.dumps({"positions": []}))

    state = load_portfolio(path)

    assert len(state.positions) == 1
    pos = state.positions[0]
    assert pos.trade_id == "quarantine_yes-chai"
    assert pos.token_id == "yes-chain-only"
    assert pos.direction == "unknown"
    assert pos.strategy == ""
    assert pos.chain_state == "quarantined"
    assert pos.quarantined_at == "2026-04-04T00:00:00Z"


def test_load_portfolio_rehydrates_chain_only_quarantine_fact_when_projection_degraded(tmp_path):
    db_path = tmp_path / "zeus.db"
    path = tmp_path / "positions-cache.json"
    conn = get_connection(db_path)
    init_schema(conn)
    conn.execute(
        """
        INSERT INTO token_suppression (
            token_id, condition_id, suppression_reason, source_module,
            created_at, updated_at, evidence_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "yes-chain-only",
            "cond-1",
            "chain_only_quarantined",
            "test",
            "2026-04-04T00:00:00Z",
            "2026-04-04T00:00:00Z",
            json.dumps({
                "size": 12.0,
                "avg_price": 0.42,
                "cost": 5.04,
                "condition_id": "cond-1",
            }),
        ),
    )
    conn.execute("DROP TABLE position_current")
    conn.commit()
    conn.close()
    path.write_text(json.dumps({"positions": []}))

    state = load_portfolio(path)

    assert state.portfolio_loader_degraded is True
    assert len(state.positions) == 1
    assert state.positions[0].token_id == "yes-chain-only"
    assert state.positions[0].chain_state == "quarantined"


def test_load_portfolio_dedupes_chain_only_fact_when_projection_already_has_token(tmp_path):
    db_path = tmp_path / "zeus.db"
    path = tmp_path / "positions-cache.json"
    conn = get_connection(db_path)
    init_schema(conn)
    conn.execute(
        """
        INSERT INTO position_current (
            position_id, phase, trade_id, market_id, city, cluster, target_date, bin_label,
            direction, unit, size_usd, shares, cost_basis_usd, entry_price, p_posterior,
            last_monitor_prob, last_monitor_edge, last_monitor_market_price,
            decision_snapshot_id, entry_method, strategy_key, edge_source, discovery_mode,
            chain_state, token_id, no_token_id, condition_id, order_id, order_status, updated_at, temperature_metric
        ) VALUES (
            'db-chain-only', 'quarantined', 'db-chain-only', 'cond-1', 'UNKNOWN', 'Other', 'UNKNOWN', 'UNKNOWN',
            'unknown', 'F', 5.04, 12.0, 5.04, 0.42, 0.42,
            NULL, NULL, NULL,
            NULL, '', 'opening_inertia', NULL, NULL,
            'quarantined', 'yes-chain-only', NULL, 'cond-1', NULL, NULL, '2026-04-04T00:00:00Z', 'high'
        )
        """
    )
    conn.execute(
        """
        INSERT INTO token_suppression (
            token_id, condition_id, suppression_reason, source_module,
            created_at, updated_at, evidence_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "yes-chain-only",
            "cond-1",
            "chain_only_quarantined",
            "test",
            "2026-04-04T00:00:00Z",
            "2026-04-04T00:00:00Z",
            json.dumps({"size": 12.0, "avg_price": 0.42, "cost": 5.04}),
        ),
    )
    conn.commit()
    conn.close()
    path.write_text(json.dumps({"positions": []}))

    state = load_portfolio(path)

    assert [pos.token_id for pos in state.positions] == ["yes-chain-only"]


def test_load_portfolio_uses_chain_quarantine_evidence_first_seen_at(tmp_path):
    db_path = tmp_path / "zeus.db"
    path = tmp_path / "positions-cache.json"
    conn = get_connection(db_path)
    init_schema(conn)
    conn.execute(
        """
        INSERT INTO token_suppression (
            token_id, condition_id, suppression_reason, source_module,
            created_at, updated_at, evidence_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "yes-chain-only",
            "cond-1",
            "chain_only_quarantined",
            "test",
            "2026-04-01T00:00:00Z",
            "2026-04-04T00:00:00Z",
            json.dumps({
                "size": 12.0,
                "avg_price": 0.42,
                "cost": 5.04,
                "first_seen_at": "2026-04-04T00:00:00Z",
            }),
        ),
    )
    conn.commit()
    conn.close()
    path.write_text(json.dumps({"positions": []}))

    state = load_portfolio(path)

    assert state.positions[0].quarantined_at == "2026-04-04T00:00:00Z"


def test_chain_only_quarantine_upsert_preserves_original_first_seen_at(tmp_path):
    db_path = tmp_path / "zeus.db"
    path = tmp_path / "positions-cache.json"
    conn = get_connection(db_path)
    init_schema(conn)
    conn.execute(
        """
        INSERT INTO token_suppression (
            token_id, condition_id, suppression_reason, source_module,
            created_at, updated_at, evidence_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "yes-chain-only",
            "cond-1",
            "chain_only_quarantined",
            "test",
            "2026-04-01T00:00:00Z",
            "2026-04-01T00:00:00Z",
            json.dumps({
                "size": 12.0,
                "avg_price": 0.42,
                "cost": 5.04,
                "first_seen_at": "2026-04-01T00:00:00Z",
            }),
        ),
    )
    portfolio = PortfolioState()

    reconcile(
        portfolio,
        [ChainPosition(token_id="yes-chain-only", size=12.0, avg_price=0.42, cost=5.04, condition_id="cond-1")],
        conn=conn,
    )
    row = conn.execute(
        "SELECT evidence_json FROM token_suppression WHERE token_id = 'yes-chain-only'"
    ).fetchone()
    conn.commit()
    conn.close()
    path.write_text(json.dumps({"positions": []}))

    evidence = json.loads(row["evidence_json"])
    state = load_portfolio(path)

    assert evidence["first_seen_at"] == "2026-04-01T00:00:00Z"
    assert state.positions[0].quarantined_at == "2026-04-01T00:00:00Z"


def test_quarantine_blocks_new_entries(monkeypatch, tmp_path):
    db_path = tmp_path / "zeus.db"
    conn = get_connection(db_path)
    init_schema(conn)
    conn.close()
    portfolio = PortfolioState(positions=[_position(direction="unknown", chain_state="quarantined")])

    class DummyClob:
        def __init__(self):
            pass

        def get_positions_from_api(self):
            return []

        def get_balance(self):
            return 100.0

        def get_open_orders(self):
            return []

    monkeypatch.setattr(cycle_runner, "get_current_level", lambda: RiskLevel.GREEN)
    monkeypatch.setattr(cycle_runner, "get_connection", lambda: get_connection(db_path))
    monkeypatch.setattr(cycle_runner, "load_portfolio", lambda: portfolio)
    monkeypatch.setattr(cycle_runner, "save_portfolio", lambda state, *args, **kwargs: None)
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
        def __init__(self):
            pass

    control_plane_module.clear_control_state()

    monkeypatch.setattr(cycle_runner, "get_current_level", lambda: RiskLevel.GREEN)
    monkeypatch.setattr(cycle_runner, "get_connection", lambda: get_connection(db_path))
    monkeypatch.setattr(cycle_runner, "load_portfolio", lambda: portfolio)
    monkeypatch.setattr(cycle_runner, "save_portfolio", lambda state, *args, **kwargs: None)
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
    conn = get_connection(db_path)
    row = conn.execute(
        """
        SELECT suppression_reason, source_module
        FROM token_suppression
        WHERE token_id = 'tok-clear'
        """
    ).fetchone()
    conn.close()
    assert dict(row) == {
        "suppression_reason": "operator_quarantine_clear",
        "source_module": "src.engine.cycle_runtime",
    }

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
        def __init__(self):
            pass

    monkeypatch.setattr(cycle_runner, "get_current_level", lambda: RiskLevel.GREEN)
    monkeypatch.setattr(cycle_runner, "get_connection", lambda: get_connection(db_path))
    monkeypatch.setattr(cycle_runner, "load_portfolio", lambda: portfolio)
    monkeypatch.setattr(cycle_runner, "save_portfolio", lambda state, *args, **kwargs: None)
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
    assert cycle_runtime._strategy_phase_rejection_reason("settlement_capture", DiscoveryMode.DAY0_CAPTURE) is None
    assert cycle_runtime._strategy_phase_rejection_reason("opening_inertia", DiscoveryMode.OPENING_HUNT) is None
    assert cycle_runtime._strategy_phase_rejection_reason("center_buy", DiscoveryMode.UPDATE_REACTION) is None
    assert cycle_runtime._strategy_phase_rejection_reason("shoulder_sell", DiscoveryMode.UPDATE_REACTION) is None
    assert (
        cycle_runtime._strategy_phase_rejection_reason("center_buy", DiscoveryMode.OPENING_HUNT)
        == "strategy_phase_mismatch:center_buy:opening_hunt"
    )


def test_settlement_sensitive_entry_ci_guard_rejects_degenerate_bands_by_mode():
    center_edge = _edge()
    center_edge.ci_lower = 0.0
    center_edge.ci_upper = 0.0
    shoulder_no = BinEdge(
        bin=Bin(low=None, high=38, label="38°F or below", unit="F"),
        direction="buy_no",
        edge=0.11,
        ci_lower=0.0,
        ci_upper=0.0,
        p_model=0.72,
        p_market=0.58,
        p_posterior=0.69,
        entry_price=0.58,
        p_value=0.02,
        vwmp=0.58,
    )
    update = MarketCandidate(
        city=NYC,
        target_date="2026-04-01",
        outcomes=[],
        hours_since_open=30.0,
        hours_to_resolution=24.0,
        discovery_mode=DiscoveryMode.UPDATE_REACTION.value,
    )
    day0 = MarketCandidate(
        city=NYC,
        target_date="2026-04-01",
        outcomes=[],
        hours_since_open=30.0,
        hours_to_resolution=2.0,
        discovery_mode=DiscoveryMode.DAY0_CAPTURE.value,
    )
    opening = MarketCandidate(
        city=NYC,
        target_date="2026-04-01",
        outcomes=[],
        hours_since_open=2.0,
        hours_to_resolution=24.0,
        discovery_mode=DiscoveryMode.OPENING_HUNT.value,
    )

    assert evaluator_module._entry_ci_rejection_reason(update, center_edge).startswith(
        "DEGENERATE_CONFIDENCE_BAND"
    )
    assert evaluator_module._strategy_key_for(update, center_edge) == "center_buy"
    assert evaluator_module._entry_ci_rejection_reason(update, shoulder_no).startswith(
        "DEGENERATE_CONFIDENCE_BAND"
    )
    assert evaluator_module._strategy_key_for(update, shoulder_no) == "shoulder_sell"
    buy_no_center = BinEdge(
        bin=Bin(low=39, high=40, label="39-40°F", unit="F"),
        direction="buy_no",
        edge=0.11,
        ci_lower=0.0,
        ci_upper=0.0,
        p_model=0.72,
        p_market=0.58,
        p_posterior=0.69,
        entry_price=0.58,
        p_value=0.02,
        vwmp=0.58,
    )
    assert evaluator_module._strategy_key_for(update, buy_no_center) is None
    assert evaluator_module._entry_ci_rejection_reason(day0, center_edge).startswith(
        "DEGENERATE_CONFIDENCE_BAND"
    )
    assert evaluator_module._strategy_key_for(day0, center_edge) == "settlement_capture"
    assert evaluator_module._entry_ci_rejection_reason(opening, center_edge) is None


def test_evaluate_candidate_rejects_unclassified_strategy_key(monkeypatch):
    from src.engine.evaluator import evaluate_candidate, MarketCandidate
    from src.state.portfolio import PortfolioState
    from src.config import City
    from src.engine.discovery_mode import DiscoveryMode
    import unittest.mock as mock
    monkeypatch.setattr(evaluator_module, "get_mode", lambda: "test")

    # Initial candidate for outer scope context
    city = City(
        name="Chicago", lat=41.8781, lon=-87.6298,
        timezone="America/Chicago", cluster="US",
        settlement_unit="F", wu_station="KORD",
    )
    # Patch fetch_ensemble to avoid real network calls
    now = datetime.now(timezone.utc)
    target_dt = now.date()
    target_date = target_dt.isoformat()

    # Ensure we have a full day of data for target_date by starting from its midnight
    midnight = datetime.combine(target_dt, datetime.min.time(), tzinfo=timezone.utc)
    mock_ens_result = {
        "members_hourly": np.zeros((51, 168)),
        "times": [(midnight + timedelta(hours=i)).isoformat() for i in range(168)],
        "fetch_time": now,
        "source_id": "tigge",
        "model": "tigge",
        "degradation_level": "OK",
        "forecast_source_role": "entry_primary",
        "authority_tier": "FORECAST",
        "raw_payload_hash": "a" * 64,
        "issue_time": now - timedelta(hours=1),
        "available_at": now - timedelta(minutes=30),
        "first_valid_time": midnight,
        "n_members": 51
    }

    candidate = MarketCandidate(
        city=city,
        target_date=target_date,
        discovery_mode=DiscoveryMode.UPDATE_REACTION.value,
        temperature_metric="high",
        hours_since_open=2.0,
        outcomes=[
            {
                "title": "39 or below°F",
                "range_low": None,
                "range_high": 39,
                "executable": True,
                "token_id": "yes1",
                "no_token_id": "no1",
                "market_id": "m1",
                "support_index": 0,
            },
            {
                "title": "40-41°F",
                "range_low": 40,
                "range_high": 41,
                "executable": True,
                "token_id": "yes2",
                "no_token_id": "no2",
                "market_id": "m2",
                "support_index": 1,
            },
            {
                "title": "42 or higher°F",
                "range_low": 42,
                "range_high": None,
                "executable": True,
                "token_id": "yes3",
                "no_token_id": "no3",
                "market_id": "m3",
                "support_index": 2,
            },
        ],
    )

    portfolio = PortfolioState()
    clob = mock.Mock()
    clob.get_best_bid_ask.return_value = (0.54, 0.56, 100.0, 100.0)
    limits = types.SimpleNamespace(
        max_city_exposure_usd=1000.0,
        max_cluster_exposure_usd=5000.0,
        max_total_exposure_usd=10000.0,
    )

    from src.types import BinEdge, Bin
    from src.types.metric_identity import HIGH_LOCALDAY_MAX
    from src.contracts.settlement_semantics import SettlementSemantics
    from src.signal.ensemble_signal import EnsembleSignal
    from src.types.temperature import TemperatureDelta

    sem = SettlementSemantics.for_city(city)
    ens = EnsembleSignal(
        members_hourly=mock_ens_result["members_hourly"],
        times=mock_ens_result["times"],
        city=city,
        target_date=datetime.strptime(target_date, "%Y-%m-%d").date(),
        settlement_semantics=sem,
        temperature_metric=HIGH_LOCALDAY_MAX,
    )

    # Mock AlphaDecision
    mock_alpha_decision = mock.Mock()
    mock_alpha_decision.value_for_consumer.return_value = 0.5

    target_edge = BinEdge(
        bin=Bin(low=40, high=41, label="40-41°F", unit="F"),
        direction="buy_no",
        edge=0.15,
        ci_lower=0.10,
        ci_upper=0.20,
        p_model=0.70,
        p_market=0.55,
        p_posterior=0.70,
        entry_price=0.55,
        vwmp=0.55,
        p_value=0.01,
        support_index=1,
    )

    import src.engine.evaluator as eval_mod
    import unittest.mock as mock

    # Mock calibrator for the unclassified test
    from src.calibration.platt import ExtendedPlattCalibrator
    mock_cal = ExtendedPlattCalibrator()
    mock_cal.predict_for_bin = lambda p, lead_days, bin_width=None: p

    from src.strategy.market_analysis_family_scan import FullFamilyHypothesis
    mock_hypothesis = FullFamilyHypothesis(
        index=1,
        range_label="40-41°F",
        direction="buy_no",
        edge=0.15,
        ci_lower=0.10,
        ci_upper=0.20,
        p_value=0.01,
        p_model=0.70,
        p_market=0.55,
        p_posterior=0.70,
        entry_price=0.55,
        is_shoulder=False,
        passed_prefilter=True,
    )

    with mock.patch("src.engine.evaluator.fetch_ensemble", return_value=mock_ens_result):
        with mock.patch("src.engine.evaluator.scan_full_hypothesis_family", return_value=[mock_hypothesis]):
            with mock.patch("src.engine.evaluator._filter_executable_selected_edges", return_value=[target_edge]):
                with mock.patch("src.engine.evaluator._store_ens_snapshot", return_value="snap123"):
                    with mock.patch("src.engine.evaluator._read_v2_snapshot_metadata", return_value={"boundary_ambiguous": False}):
                        with mock.patch("src.engine.evaluator._store_snapshot_p_raw", return_value=True):
                            with mock.patch("src.engine.evaluator.get_calibrator", return_value=(mock_cal, 1)):
                                with mock.patch("src.engine.evaluator.ensemble_crosscheck_model", return_value="gfs025"):
                                    with mock.patch("src.engine.evaluator.model_agreement", return_value="AGREE"):
                                        with mock.patch("src.engine.evaluator._record_selection_family_facts", return_value=None):
                                            with mock.patch("src.engine.evaluator.compute_alpha", return_value=mock_alpha_decision):
                                                with mock.patch("src.engine.evaluator.edge_n_bootstrap", return_value=10):
                                                    decisions = evaluate_candidate(candidate, None, portfolio, clob, limits, decision_time=now)

    assert len(decisions) == 1
    d = decisions[0]
    assert d.should_trade is False
    assert d.rejection_stage == "SIGNAL_QUALITY"
    assert "strategy_key_unclassified" in d.rejection_reasons
    assert d.strategy_key == ""


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
    candidate = types.SimpleNamespace(
        target_date="2026-04-01",
        hours_since_open=2.0,
        temperature_metric="high",
    )
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
        env="paper",
        bankroll_at_entry=100.0,
        deps=deps,
    )

    assert pos.strategy_key == "center_buy"
    assert pos.strategy == "center_buy"


def test_materialize_position_splits_submitted_target_from_fill_authority():
    decision = evaluator_module.EdgeDecision(
        should_trade=True,
        edge=_edge(),
        tokens={"market_id": "m1", "token_id": "yes1", "no_token_id": "no1"},
        size_usd=11.0,
        decision_id="d1",
        selected_method="ens_member_counting",
        edge_source="opening_inertia",
        strategy_key="center_buy",
    )
    result = types.SimpleNamespace(
        trade_id="t1",
        fill_price=None,
        submitted_price=0.55,
        shares=20.0,
        timeout_seconds=None,
        order_id="o1",
        status="pending",
    )
    city = types.SimpleNamespace(name="New York", cluster="US", settlement_unit="F")
    candidate = types.SimpleNamespace(
        target_date="2026-04-01",
        hours_since_open=2.0,
        temperature_metric="high",
    )
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
        state="pending_tracked",
        env="paper",
        bankroll_at_entry=100.0,
        deps=deps,
    )

    assert pos.target_notional_usd == pytest.approx(11.0)
    assert pos.entry_price_submitted == pytest.approx(0.55)
    assert pos.submitted_notional_usd == pytest.approx(11.0)
    assert pos.shares_submitted == pytest.approx(20.0)
    assert pos.entry_economics_authority == ENTRY_ECONOMICS_SUBMITTED_LIMIT
    assert pos.fill_authority == FILL_AUTHORITY_NONE
    assert pos.entry_price_avg_fill == 0.0
    assert pos.shares_filled == 0.0
    assert pos.filled_cost_basis_usd == 0.0
    assert pos.corrected_executable_economics_eligible is False
    assert pos.has_fill_economics_authority is False


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
    candidate = types.SimpleNamespace(
        target_date="2026-04-01",
        hours_since_open=2.0,
        temperature_metric="high",
    )
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
            env="paper",
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
    # Create empty sibling DB so load_portfolio uses it (empty → JSON fallback)
    # instead of falling through to the production DB.
    sibling_db = tmp_path / "zeus-paper.db"
    conn = get_connection(sibling_db)
    init_schema(conn)
    conn.close()

    path = tmp_path / "positions-paper.json"
    path.write_text(json.dumps({
        "positions": [{
            "trade_id": "t1",
            "market_id": "m1",
            "city": "NYC",
            "cluster": "NYC",
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

    # P4 (Tier 2.1): JSON fallback deleted. DB projection is empty in this
    # test fixture, so load_portfolio returns degraded empty portfolio.
    # strategy_key backfilling was a JSON-path feature; canonical DB path
    # stores strategy_key directly in position_current.
    assert state.positions == []
    assert state.portfolio_loader_degraded is True


def test_load_portfolio_prefers_position_current_when_projection_exists(tmp_path, monkeypatch):
    db_path = tmp_path / "zeus.db"
    path = tmp_path / "positions-paper.json"
    conn = get_connection(db_path)
    init_schema(conn)
    monkeypatch.setattr("src.state.db.get_trade_connection_with_world", lambda mode: get_connection(db_path))
    conn.execute(
        """
        INSERT INTO position_current (
            position_id, phase, trade_id, market_id, city, cluster, target_date, bin_label,
            direction, unit, size_usd, shares, cost_basis_usd, entry_price, p_posterior,
            last_monitor_prob, last_monitor_edge, last_monitor_market_price,
            decision_snapshot_id, entry_method, strategy_key, edge_source, discovery_mode,
            chain_state, order_id, order_status, updated_at, temperature_metric
        ) VALUES (
            'db-t1', 'active', 'db-t1', 'm-db', 'NYC', 'NYC', '2026-04-01', '39-40°F',
            'buy_yes', 'F', 12.0, 30.0, 12.0, 0.4, 0.61,
            NULL, NULL, NULL,
            'snap-db', 'ens_member_counting', 'opening_inertia', 'opening_inertia', 'opening_hunt',
            'unknown', '', 'filled', '2026-04-04T00:00:00Z', 'high'
        )
        """
    )
    conn.commit()
    conn.close()

    path.write_text(json.dumps({
        "positions": [{
            "trade_id": "db-t1",
            "market_id": "m-json",
            "city": "NYC",
            "cluster": "NYC",
            "target_date": "2026-04-01",
            "bin_label": "41-42°F",
            "direction": "buy_no",
            "unit": "F",
            "state": "entered",
            "strategy": "center_buy",
            "edge_source": "center_buy",
            "token_id": "json-yes",
            "no_token_id": "json-no",
        }],
        "bankroll": 99.0,
        "recent_exits": [{
            "city": "NYC",
            "bin_label": "json-shadow",
            "target_date": "2026-04-01",
            "pnl": 99.0,
        }],
    }))

    state = load_portfolio(path)

    assert [pos.trade_id for pos in state.positions] == ["db-t1"]
    assert state.positions[0].strategy_key == "opening_inertia"
    assert state.positions[0].state == "entered"
    assert state.positions[0].token_id == ""
    assert state.positions[0].no_token_id == ""
    # 2026-05-04 bankroll truth-chain cleanup: PortfolioState.bankroll defaults
    # to 0.0 ("uninitialized — ask bankroll_provider"). load_portfolio() no
    # longer seeds from settings.capital_base_usd ($150 fiction).
    assert state.bankroll == pytest.approx(0.0)
    assert state.daily_baseline_total == pytest.approx(0.0)
    assert state.weekly_baseline_total == pytest.approx(0.0)
    assert state.recent_exits == []


def test_load_portfolio_reads_token_identity_from_position_current(tmp_path, monkeypatch):
    db_path = tmp_path / "zeus.db"
    path = tmp_path / "positions-paper.json"
    conn = get_connection(db_path)
    init_schema(conn)
    monkeypatch.setattr("src.state.db.get_trade_connection_with_world", lambda mode: get_connection(db_path))
    conn.execute(
        """
        INSERT INTO position_current (
            position_id, phase, trade_id, market_id, city, cluster, target_date, bin_label,
            direction, unit, size_usd, shares, cost_basis_usd, entry_price, p_posterior,
            last_monitor_prob, last_monitor_edge, last_monitor_market_price,
            decision_snapshot_id, entry_method, strategy_key, edge_source, discovery_mode,
            chain_state, token_id, no_token_id, condition_id, order_id, order_status, updated_at, temperature_metric
        ) VALUES (
            'db-token', 'active', 'db-token', 'm-db', 'NYC', 'NYC', '2026-04-01', '39-40°F',
            'buy_yes', 'F', 12.0, 30.0, 12.0, 0.4, 0.61,
            NULL, NULL, NULL,
            'snap-db', 'ens_member_counting', 'opening_inertia', 'opening_inertia', 'opening_hunt',
            'unknown', 'yes-db-token', 'no-db-token', 'condition-db', '', 'filled', '2026-04-04T00:00:00Z', 'high'
        )
        """
    )
    conn.commit()
    conn.close()

    path.write_text(json.dumps({
        "positions": [{
            "trade_id": "db-token",
            "market_id": "m-json",
            "city": "NYC",
            "cluster": "NYC",
            "target_date": "2026-04-01",
            "bin_label": "41-42°F",
            "direction": "buy_no",
            "unit": "F",
            "state": "entered",
            "strategy": "center_buy",
            "edge_source": "center_buy",
            "token_id": "yes-json-token",
            "no_token_id": "no-json-token",
            "condition_id": "condition-json",
        }],
        "bankroll": 99.0,
    }))

    state = load_portfolio(path)

    assert [pos.trade_id for pos in state.positions] == ["db-token"]
    assert state.positions[0].token_id == "yes-db-token"
    assert state.positions[0].no_token_id == "no-db-token"
    assert state.positions[0].condition_id == "condition-db"


def test_load_portfolio_reads_ignored_tokens_from_canonical_suppression(tmp_path):
    db_path = tmp_path / "zeus.db"
    path = tmp_path / "positions-cache.json"
    conn = get_connection(db_path)
    init_schema(conn)
    conn.execute(
        """
        INSERT INTO position_current (
            position_id, phase, trade_id, market_id, city, cluster, target_date, bin_label,
            direction, unit, size_usd, shares, cost_basis_usd, entry_price, p_posterior,
            last_monitor_prob, last_monitor_edge, last_monitor_market_price,
            decision_snapshot_id, entry_method, strategy_key, edge_source, discovery_mode,
            chain_state, token_id, no_token_id, condition_id, order_id, order_status, updated_at, temperature_metric
        ) VALUES (
            'db-token', 'active', 'db-token', 'm-db', 'NYC', 'NYC', '2026-04-01', '39-40°F',
            'buy_yes', 'F', 12.0, 30.0, 12.0, 0.4, 0.61,
            NULL, NULL, NULL,
            'snap-db', 'ens_member_counting', 'opening_inertia', 'opening_inertia', 'opening_hunt',
            'unknown', 'yes-db-token', 'no-db-token', 'condition-db', '', 'filled', '2026-04-04T00:00:00Z', 'high'
        )
        """
    )
    conn.execute(
        """
        INSERT INTO token_suppression (
            token_id, suppression_reason, source_module, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?)
        """,
        (
            "db-suppressed-token",
            "operator_quarantine_clear",
            "test",
            "2026-04-04T00:00:00Z",
            "2026-04-04T00:00:00Z",
        ),
    )
    conn.commit()
    conn.close()

    path.write_text(json.dumps({
        "positions": [],
        "bankroll": 99.0,
        "ignored_tokens": ["json-shadow-token"],
    }))

    state = load_portfolio(path)

    assert [pos.trade_id for pos in state.positions] == ["db-token"]
    assert state.ignored_tokens == ["db-suppressed-token"]


def test_load_portfolio_reads_canonical_suppression_when_projection_empty(tmp_path):
    db_path = tmp_path / "zeus.db"
    path = tmp_path / "positions-cache.json"
    conn = get_connection(db_path)
    init_schema(conn)
    conn.execute(
        """
        INSERT INTO token_suppression (
            token_id, suppression_reason, source_module, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?)
        """,
        (
            "db-empty-suppressed-token",
            "operator_quarantine_clear",
            "test",
            "2026-04-04T00:00:00Z",
            "2026-04-04T00:00:00Z",
        ),
    )
    conn.commit()
    conn.close()

    path.write_text(json.dumps({
        "positions": [],
        "bankroll": 99.0,
        "ignored_tokens": ["json-shadow-token"],
    }))

    state = load_portfolio(path)

    assert state.positions == []
    assert state.portfolio_loader_degraded is False
    assert state.ignored_tokens == ["db-empty-suppressed-token"]


def test_load_portfolio_preserves_canonical_suppression_when_projection_degraded(tmp_path):
    db_path = tmp_path / "zeus.db"
    path = tmp_path / "positions-cache.json"
    conn = get_connection(db_path)
    init_schema(conn)
    conn.execute(
        """
        INSERT INTO token_suppression (
            token_id, suppression_reason, source_module, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?)
        """,
        (
            "db-degraded-suppressed-token",
            "operator_quarantine_clear",
            "test",
            "2026-04-04T00:00:00Z",
            "2026-04-04T00:00:00Z",
        ),
    )
    conn.execute("DROP TABLE position_current")
    conn.commit()
    conn.close()

    path.write_text(json.dumps({
        "positions": [],
        "bankroll": 99.0,
        "ignored_tokens": ["json-shadow-token"],
    }))

    state = load_portfolio(path)

    assert state.positions == []
    assert state.portfolio_loader_degraded is True
    assert state.ignored_tokens == ["db-degraded-suppressed-token"]


def test_json_payload_loader_does_not_hydrate_ignored_tokens():
    from src.state import portfolio as portfolio_module

    state = portfolio_module._load_portfolio_from_json_data(
        {
            "positions": [],
            "bankroll": 99.0,
            "daily_baseline_total": 88.0,
            "weekly_baseline_total": 77.0,
            "recent_exits": [{"pnl": 99.0}],
            "ignored_tokens": ["json-shadow-token"],
        },
        current_mode="live",
    )

    # 2026-05-04 bankroll truth-chain cleanup: PortfolioState.bankroll defaults
    # to 0.0 ("uninitialized — ask bankroll_provider"). load_portfolio() no
    # longer seeds from settings.capital_base_usd ($150 fiction).
    assert state.bankroll == pytest.approx(0.0)
    assert state.daily_baseline_total == pytest.approx(0.0)
    assert state.weekly_baseline_total == pytest.approx(0.0)
    assert state.recent_exits == []
    assert state.ignored_tokens == []


def test_load_portfolio_ignores_deprecated_json_when_projection_authoritative(tmp_path):
    db_path = tmp_path / "zeus.db"
    path = tmp_path / "positions-cache.json"
    conn = get_connection(db_path)
    init_schema(conn)
    conn.execute(
        """
        INSERT INTO position_current (
            position_id, phase, trade_id, market_id, city, cluster, target_date, bin_label,
            direction, unit, size_usd, shares, cost_basis_usd, entry_price, p_posterior,
            last_monitor_prob, last_monitor_edge, last_monitor_market_price,
            decision_snapshot_id, entry_method, strategy_key, edge_source, discovery_mode,
            chain_state, order_id, order_status, updated_at, temperature_metric
        ) VALUES (
            'db-deprecated-json', 'active', 'db-deprecated-json', 'm-db', 'NYC', 'NYC', '2026-04-01', '39-40°F',
            'buy_yes', 'F', 12.0, 30.0, 12.0, 0.4, 0.61,
            NULL, NULL, NULL,
            'snap-db', 'ens_member_counting', 'opening_inertia', 'opening_inertia', 'opening_hunt',
            'unknown', '', 'filled', '2026-04-04T00:00:00Z', 'high'
        )
        """
    )
    conn.commit()
    conn.close()
    path.write_text(json.dumps({
        "truth": {"deprecated": True},
        "bankroll": 999.0,
        "positions": [],
    }))

    state = load_portfolio(path)

    assert [pos.trade_id for pos in state.positions] == ["db-deprecated-json"]
    assert state.bankroll == pytest.approx(0.0)  # 2026-05-04 bankroll truth-chain cleanup


def test_load_portfolio_ignores_corrupt_json_when_projection_authoritative(tmp_path):
    db_path = tmp_path / "zeus.db"
    path = tmp_path / "positions-cache.json"
    conn = get_connection(db_path)
    init_schema(conn)
    conn.execute(
        """
        INSERT INTO position_current (
            position_id, phase, trade_id, market_id, city, cluster, target_date, bin_label,
            direction, unit, size_usd, shares, cost_basis_usd, entry_price, p_posterior,
            last_monitor_prob, last_monitor_edge, last_monitor_market_price,
            decision_snapshot_id, entry_method, strategy_key, edge_source, discovery_mode,
            chain_state, order_id, order_status, updated_at, temperature_metric
        ) VALUES (
            'db-corrupt-json', 'active', 'db-corrupt-json', 'm-db', 'NYC', 'NYC', '2026-04-01', '39-40°F',
            'buy_yes', 'F', 12.0, 30.0, 12.0, 0.4, 0.61,
            NULL, NULL, NULL,
            'snap-db', 'ens_member_counting', 'opening_inertia', 'opening_inertia', 'opening_hunt',
            'unknown', '', 'filled', '2026-04-04T00:00:00Z', 'high'
        )
        """
    )
    conn.commit()
    conn.close()
    path.write_text("{not-json")

    state = load_portfolio(path)

    assert [pos.trade_id for pos in state.positions] == ["db-corrupt-json"]
    assert state.bankroll == pytest.approx(0.0)  # 2026-05-04 bankroll truth-chain cleanup


def test_load_portfolio_db_connection_failure_ignores_corrupt_json_and_degrades(tmp_path, monkeypatch):
    path = tmp_path / "positions-cache.json"
    path.write_text("{not-json")

    def broken_get_connection(*args, **kwargs):
        raise OSError("db unavailable")

    monkeypatch.setattr("src.state.db.get_connection", broken_get_connection)

    state = load_portfolio(path)

    assert state.positions == []
    assert state.portfolio_loader_degraded is True
    assert state.bankroll == pytest.approx(0.0)  # 2026-05-04 bankroll truth-chain cleanup


def test_load_portfolio_db_connection_failure_ignores_unreadable_json_bytes(tmp_path, monkeypatch):
    path = tmp_path / "positions-cache.json"
    path.write_bytes(b"\xff\xfe")

    def broken_get_connection(*args, **kwargs):
        raise OSError("db unavailable")

    monkeypatch.setattr("src.state.db.get_connection", broken_get_connection)

    state = load_portfolio(path)

    assert state.positions == []
    assert state.portfolio_loader_degraded is True
    assert state.bankroll == pytest.approx(0.0)  # 2026-05-04 bankroll truth-chain cleanup


def test_load_portfolio_db_connection_failure_rejects_deprecated_json(tmp_path, monkeypatch):
    path = tmp_path / "positions-cache.json"
    path.write_text(json.dumps({
        "truth": {"deprecated": True},
        "positions": [],
    }))

    def broken_get_connection(*args, **kwargs):
        raise OSError("db unavailable")

    monkeypatch.setattr("src.state.db.get_connection", broken_get_connection)

    with pytest.raises(DeprecatedStateFileError):
        load_portfolio(path)


def test_load_portfolio_reads_recent_exits_from_authoritative_settlement_rows(tmp_path):
    db_path = tmp_path / "zeus.db"
    path = tmp_path / "positions-cache.json"
    conn = get_connection(db_path)
    init_schema(conn)
    payload = {
        "contract_version": "position_settled.v1",
        "winning_bin": "39-40°F",
        "position_bin": "39-40°F",
        "won": True,
        "outcome": 1,
        "p_posterior": 0.61,
        "exit_price": 1.0,
        "pnl": 4.2,
        "exit_reason": "SETTLEMENT",
    }
    conn.execute(
        """
        INSERT INTO position_current (
            position_id, phase, trade_id, market_id, city, cluster, target_date, bin_label,
            direction, unit, size_usd, shares, cost_basis_usd, entry_price, p_posterior,
            last_monitor_prob, last_monitor_edge, last_monitor_market_price,
            decision_snapshot_id, entry_method, strategy_key, edge_source, discovery_mode,
            chain_state, order_id, order_status, updated_at, temperature_metric
        ) VALUES (
            'db-recent-exit', 'active', 'db-recent-exit', 'm-db', 'NYC', 'NYC', '2026-04-01', '39-40°F',
            'buy_yes', 'F', 12.0, 30.0, 12.0, 0.4, 0.61,
            NULL, NULL, NULL,
            'snap-db', 'ens_member_counting', 'opening_inertia', 'opening_inertia', 'opening_hunt',
            'unknown', '', 'filled', '2026-04-04T00:00:00Z', 'high'
        )
        """
    )
    conn.execute(
        """
        INSERT INTO position_events (
            event_id, position_id, event_version, sequence_no, event_type, occurred_at,
            phase_before, phase_after, strategy_key, decision_id, snapshot_id, order_id,
            command_id, caused_by, idempotency_key, venue_status, source_module, payload_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "evt-recent-exit",
            "db-recent-exit",
            1,
            1,
            "SETTLED",
            "2026-04-04T01:00:00Z",
            "pending_exit",
            "settled",
            "opening_inertia",
            "dec-recent-exit",
            "snap-db",
            None,
            None,
            None,
            "db-recent-exit:settled:1",
            None,
            "test",
            json.dumps(payload),
        ),
    )
    conn.commit()
    conn.close()
    path.write_text(json.dumps({
        "positions": [],
        "recent_exits": [{"bin_label": "json-shadow", "pnl": 99.0}],
    }))

    state = load_portfolio(path)

    assert state.recent_exits == [{
        "city": "NYC",
        "bin_label": "39-40°F",
        "target_date": "2026-04-01",
        "direction": "buy_yes",
        "token_id": "",
        "no_token_id": "",
        "exit_reason": "SETTLEMENT",
        "exited_at": "2026-04-04T01:00:00Z",
        "pnl": 4.2,
    }]


def test_load_portfolio_treats_empty_projection_as_canonical_empty(tmp_path, monkeypatch):
    db_path = tmp_path / "zeus.db"
    path = tmp_path / "positions-paper.json"
    conn = get_connection(db_path)
    init_schema(conn)
    monkeypatch.setattr("src.state.db.get_trade_connection_with_world", lambda mode: get_connection(db_path))
    conn.close()

    path.write_text(json.dumps({
        "positions": [{
            "trade_id": "json-t1",
            "market_id": "m-json",
            "city": "NYC",
            "cluster": "NYC",
            "target_date": "2026-04-01",
            "bin_label": "39-40°F",
            "direction": "buy_yes",
            "unit": "F",
            "state": "entered",
            "strategy": "center_buy",
            "edge_source": "center_buy",
        }],
        "bankroll": 111.0,
    }))

    state = load_portfolio(path)

    # Empty position_current is canonical healthy truth, not JSON fallback.
    assert state.positions == []
    assert state.portfolio_loader_degraded is False
    assert state.bankroll == pytest.approx(0.0)  # 2026-05-04 bankroll truth-chain cleanup


def test_load_portfolio_treats_empty_projection_as_canonical_despite_legacy_json(tmp_path, monkeypatch):
    db_path = tmp_path / "zeus.db"
    path = tmp_path / "positions-paper.json"
    conn = get_connection(db_path)
    init_schema(conn)
    monkeypatch.setattr("src.state.db.get_trade_connection_with_world", lambda mode: get_connection(db_path))
    conn.commit()
    conn.close()

    path.write_text(json.dumps({
        "positions": [{
            "trade_id": "t1",
            "market_id": "m1",
            "city": "NYC",
            "cluster": "NYC",
            "target_date": "2026-04-01",
            "bin_label": "39-40°F",
            "direction": "buy_yes",
            "unit": "F",
            "state": "entered",
            "strategy": "opening_inertia",
            "edge_source": "opening_inertia",
            "shares": 25.0,
            "cost_basis_usd": 5.0,
            "token_id": "yes123",
        }],
        "bankroll": 111.0,
    }))

    state = load_portfolio(path)

    # Empty position_current remains canonical even when a stale JSON cache has
    # legacy positions. JSON is not promoted back into authority.
    assert state.positions == []
    assert state.portfolio_loader_degraded is False


def test_partial_stale_policy_uses_degraded_json_fallback():
    from src.state.portfolio_loader_policy import choose_portfolio_truth_source

    decision = choose_portfolio_truth_source("partial_stale")

    assert decision.source == "json_fallback"
    assert decision.escalate is True
    assert "partial_stale" in decision.reason


def test_lead_days_use_city_local_reference_time():
    lead_days = lead_days_to_date_start(
        "2026-04-01",
        "Asia/Tokyo",
        datetime(2026, 3, 30, 23, 30, tzinfo=timezone.utc),
    )

    assert lead_days == pytest.approx(15.5 / 24.0)


def test_evaluator_projects_exposure_across_multiple_edges(monkeypatch, tmp_path):
    monkeypatch.setattr(evaluator_module, "get_mode", lambda: "test")
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
                "title": "41-42°F",
                "range_low": 41,
                "range_high": 42,
                "token_id": "yes3",
                "no_token_id": "no3",
                "market_id": "m3",
                "price": 0.45,
            },
            {
                "title": "43°F or higher",
                "range_low": 43,
                "range_high": None,
                "token_id": "yes4",
                "no_token_id": "no4",
                "market_id": "m4",
                "price": 0.10,
            },
        ],
        hours_since_open=30.0,
        hours_to_resolution=24.0,
        discovery_mode=DiscoveryMode.UPDATE_REACTION.value,
    )

    class DummyEnsembleSignal:
        def __init__(self, members_hourly, times, city, target_d, settlement_semantics=None, decision_time=None, **kwargs):
            self.member_maxes = np.full(51, 40.0)
            self.member_extrema = self.member_maxes
            self.bias_corrected = False

        def p_raw_vector(self, bins, n_mc=3000):
            return np.array([0.20, 0.40, 0.25, 0.15])

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
            support_index=0,
        ),
        BinEdge(
            bin=Bin(low=41, high=42, label="41-42°F", unit="F"),
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
            support_index=1,
        ),
    ]

    class DummyAnalysis:
        def __init__(self, **kwargs):
            pass

        def find_edges(self, n_bootstrap=500):
            self.selected_method = getattr(self, "selected_method", "test_fixture")
            assert self.selected_method
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
        lambda city, forecast_days=2, model=None, role=None: {
            "members_hourly": np.ones(((31 if model == "gfs025" else 51), 24)) * 40.0,
            "times": [
                datetime(2026, 4, 1, hour, 0, tzinfo=timezone.utc).isoformat()
                for hour in range(24)
            ],
            **_entry_forecast_evidence(
                model=model or "ecmwf_ifs025",
                source_id="tigge" if (model or "ecmwf_ifs025") != "gfs025" else "gfs025",
                role=role or "entry_primary",
                issue_time=datetime(2026, 4, 1, 0, 0, tzinfo=timezone.utc),
                first_valid_time=datetime(2026, 4, 1, 0, 0, tzinfo=timezone.utc),
                fetch_time=datetime(2026, 4, 1, 23, 30, tzinfo=timezone.utc),
                n_members=31 if model == "gfs025" else 51,
            ),
        },
    )
    monkeypatch.setattr(evaluator_module, "validate_ensemble", lambda result, expected_members=51: result is not None)
    monkeypatch.setattr(evaluator_module, "EnsembleSignal", DummyEnsembleSignal)
    monkeypatch.setattr(evaluator_module, "_store_ens_snapshot", lambda *args, **kwargs: "snap-1")
    monkeypatch.setattr(evaluator_module, "_store_snapshot_p_raw", lambda *args, **kwargs: None)
    _patch_mature_calibration(monkeypatch)
    monkeypatch.setattr(evaluator_module, "MarketAnalysis", DummyAnalysis)
    _stub_full_family_scan(monkeypatch)
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
        decision_time=datetime(2026, 4, 2, 0, 0, tzinfo=timezone.utc),
    )

    assert [d.should_trade for d in decisions] == [True, False]
    assert heats[0] == pytest.approx(0.0)
    assert heats[1] == pytest.approx(0.4)


def test_update_reaction_degenerate_ci_fails_closed_before_sizing(monkeypatch):
    monkeypatch.setattr(evaluator_module, "get_mode", lambda: "test")
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
        def __init__(self, members_hourly, times, city, target_d, settlement_semantics=None, decision_time=None, **kwargs):
            self.member_maxes = np.full(51, 40.0)
            self.member_extrema = self.member_maxes
            self.bias_corrected = False

        def p_raw_vector(self, bins, n_mc=3000):
            return np.array([0.25, 0.50, 0.25])

        def spread(self):
            from src.types.temperature import TemperatureDelta

            return TemperatureDelta(2.0, "F")

        def spread_float(self):
            return 2.0

    degenerate_edge = BinEdge(
        bin=Bin(low=39, high=40, label="39-40°F", unit="F"),
        direction="buy_yes",
        edge=0.12,
        ci_lower=0.0,
        ci_upper=0.0,
        p_model=0.60,
        p_market=0.35,
        p_posterior=0.47,
        entry_price=0.35,
        p_value=0.02,
        vwmp=0.35,
        support_index=0,
    )

    class DummyAnalysis:
        def __init__(self, **kwargs):
            pass

        def find_edges(self, n_bootstrap=500):
            self.selected_method = getattr(self, "selected_method", "test_fixture")
            assert self.selected_method
            degenerate_edge.forward_edge = degenerate_edge.p_posterior - degenerate_edge.p_market
            return [degenerate_edge]

        def sigma_context(self):
            return {"base_sigma": 0.5, "lead_multiplier": 1.1, "spread_multiplier": 1.05, "final_sigma": 0.5775}

        def mean_context(self):
            return {"offset": 0.0, "lead_days": 1.5}

    class DummyClob:
        def get_best_bid_ask(self, token_id):
            return (0.34, 0.36, 20.0, 20.0)

    monkeypatch.setattr(
        evaluator_module,
        "fetch_ensemble",
        lambda city, forecast_days=2, model=None, role=None: {
            "members_hourly": np.ones(((31 if model == "gfs025" else 51), 24)) * 40.0,
            "times": [
                datetime(2026, 4, 1, hour, 0, tzinfo=timezone.utc).isoformat()
                for hour in range(24)
            ],
            **_entry_forecast_evidence(
                model=model or "ecmwf_ifs025",
                source_id="tigge" if (model or "ecmwf_ifs025") != "gfs025" else "gfs025",
                role=role or "entry_primary",
                issue_time=datetime(2026, 4, 1, 0, 0, tzinfo=timezone.utc),
                first_valid_time=datetime(2026, 4, 1, 0, 0, tzinfo=timezone.utc),
                fetch_time=datetime(2026, 4, 1, 23, 30, tzinfo=timezone.utc),
                n_members=31 if model == "gfs025" else 51,
            ),
        },
    )
    monkeypatch.setattr(evaluator_module, "validate_ensemble", lambda result, expected_members=51: result is not None)
    monkeypatch.setattr(evaluator_module, "EnsembleSignal", DummyEnsembleSignal)
    monkeypatch.setattr(evaluator_module, "_store_ens_snapshot", lambda *args, **kwargs: "snap-degenerate-ci")
    monkeypatch.setattr(evaluator_module, "_store_snapshot_p_raw", lambda *args, **kwargs: None)
    _patch_mature_calibration(monkeypatch)
    monkeypatch.setattr(evaluator_module, "MarketAnalysis", DummyAnalysis)
    _stub_full_family_scan(monkeypatch)
    monkeypatch.setattr(evaluator_module, "fdr_filter", lambda edges, fdr_alpha=0.10: list(edges))
    monkeypatch.setattr(evaluator_module, "kelly_size", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("degenerate CI must not reach sizing")))

    decisions = evaluator_module.evaluate_candidate(
        candidate,
        conn=None,
        portfolio=PortfolioState(bankroll=150.0),
        clob=DummyClob(),
        limits=evaluator_module.RiskLimits(min_order_usd=1.0),
        entry_bankroll=150.0,
        decision_time=datetime(2026, 4, 2, 0, 0, tzinfo=timezone.utc),
    )

    assert len(decisions) == 1
    assert decisions[0].should_trade is False
    assert decisions[0].rejection_stage == "EDGE_INSUFFICIENT"
    assert decisions[0].rejection_reasons[0].startswith("DEGENERATE_CONFIDENCE_BAND")
    assert decisions[0].strategy_key == "center_buy"
    assert "confidence_band_guard" in decisions[0].applied_validations


def test_update_reaction_brier_alpha_fails_closed_before_sizing(monkeypatch):
    monkeypatch.setattr(evaluator_module, "get_mode", lambda: "test")
    from src.contracts.alpha_decision import AlphaDecision

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
        def __init__(self, members_hourly, times, city, target_d, settlement_semantics=None, decision_time=None, **kwargs):
            self.member_maxes = np.full(51, 40.0)
            self.member_extrema = self.member_maxes
            self.bias_corrected = False

        def p_raw_vector(self, bins, n_mc=3000):
            return np.array([0.25, 0.50, 0.25])

        def spread(self):
            from src.types.temperature import TemperatureDelta

            return TemperatureDelta(2.0, "F")

        def spread_float(self):
            return 2.0

    class DummyClob:
        def get_best_bid_ask(self, token_id):
            return (0.34, 0.36, 20.0, 20.0)

    monkeypatch.setattr(
        evaluator_module,
        "fetch_ensemble",
        lambda city, forecast_days=2, model=None, role=None: {
            "members_hourly": np.ones(((31 if model == "gfs025" else 51), 24)) * 40.0,
            "times": [
                datetime(2026, 4, 1, hour, 0, tzinfo=timezone.utc).isoformat()
                for hour in range(24)
            ],
            **_entry_forecast_evidence(
                model=model or "ecmwf_ifs025",
                source_id="tigge" if (model or "ecmwf_ifs025") != "gfs025" else "gfs025",
                role=role or "entry_primary",
                issue_time=datetime(2026, 4, 1, 0, 0, tzinfo=timezone.utc),
                first_valid_time=datetime(2026, 4, 1, 0, 0, tzinfo=timezone.utc),
                fetch_time=datetime(2026, 4, 1, 23, 30, tzinfo=timezone.utc),
                n_members=31 if model == "gfs025" else 51,
            ),
        },
    )
    monkeypatch.setattr(evaluator_module, "validate_ensemble", lambda result, expected_members=51: result is not None)
    monkeypatch.setattr(evaluator_module, "EnsembleSignal", DummyEnsembleSignal)
    monkeypatch.setattr(evaluator_module, "_store_ens_snapshot", lambda *args, **kwargs: "snap-alpha-target")
    monkeypatch.setattr(evaluator_module, "_store_snapshot_p_raw", lambda *args, **kwargs: None)
    _patch_mature_calibration(monkeypatch)
    monkeypatch.setattr(
        evaluator_module,
        "compute_alpha",
        lambda *args, **kwargs: AlphaDecision(
            value=0.65,
            optimization_target="brier_score",
            evidence_basis="test brier alpha",
            ci_bound=0.1,
        ),
    )
    monkeypatch.setattr(evaluator_module, "kelly_size", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("alpha mismatch must not reach sizing")))

    decisions = evaluator_module.evaluate_candidate(
        candidate,
        conn=None,
        portfolio=PortfolioState(bankroll=150.0),
        clob=DummyClob(),
        limits=evaluator_module.RiskLimits(min_order_usd=1.0),
        entry_bankroll=150.0,
        decision_time=datetime(2026, 4, 2, 0, 0, tzinfo=timezone.utc),
    )

    assert len(decisions) == 1
    assert decisions[0].should_trade is False
    assert decisions[0].rejection_stage == "SIGNAL_QUALITY"
    assert decisions[0].rejection_reasons[0].startswith("ALPHA_TARGET_MISMATCH")
    assert "alpha_target_contract" in decisions[0].applied_validations


def test_day0_observation_path_reaches_day0_signal(monkeypatch):
    monkeypatch.setattr(evaluator_module, "get_mode", lambda: "test")
    calls: dict[str, object] = {}

    candidate = MarketCandidate(
        city=NYC,
        target_date=str(date.today()),
        outcomes=[
            {
                "title": "38°F or lower",
                "range_low": None,
                "range_high": 38,
                "token_id": "yes0",
                "no_token_id": "no0",
                "market_id": "m0",
                "price": 0.34,
            },
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
        observation=Day0ObservationContext(
            high_so_far=44.0,
            low_so_far=39.0,
            current_temp=43.0,
            source="wu_api",
            observation_time=datetime.now(timezone.utc).isoformat(),
            unit="F",
        ),
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
            return np.array([0.50, 0.30, 0.15, 0.05])

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
        def __init__(self, members_hourly, times, city, target_d, settlement_semantics=None, decision_time=None, **kwargs):
            self.member_maxes = np.full(51, 44.0)
            self.member_extrema = self.member_maxes
            self.bias_corrected = False

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
            self.selected_method = getattr(self, "selected_method", "test_fixture")
            assert self.selected_method
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
        lambda city, forecast_days=2, model=None, role=None: None if model == "gfs025" else {
            "members_hourly": np.ones((51, 12)) * 44.0,
            "times": [
                datetime.now(timezone.utc).replace(microsecond=0).isoformat()
                for _ in range(12)
            ],
            **_entry_forecast_evidence(
                model="ecmwf_ifs025",
                issue_time=datetime.now(timezone.utc),
                first_valid_time=datetime.now(timezone.utc),
                fetch_time=datetime.now(timezone.utc),
            ),
        },
    )
    monkeypatch.setattr(evaluator_module, "validate_ensemble", lambda result, expected_members=51: result is not None)
    monkeypatch.setattr(evaluator_module, "EnsembleSignal", DummyEnsembleSignal)

    def _route_day0(inputs):
        return DummyDay0Signal(
            inputs.observed_high_so_far,
            inputs.current_temp,
            inputs.hours_remaining,
            inputs.member_maxes_remaining,
            unit=inputs.unit,
            temporal_context=inputs.temporal_context,
        )

    monkeypatch.setattr(evaluator_module.Day0Router, "route", staticmethod(_route_day0))
    from src.signal.day0_extrema import RemainingMemberExtrema as _REM
    def _remaining_for_day0(members_hourly, times, timezone_name, target_d, now=None, **kwargs):
        calls["day0_now"] = now
        return _REM(maxes=np.full(51, 44.0), mins=None), 6.0
    monkeypatch.setattr(evaluator_module, "remaining_member_extrema_for_day0", _remaining_for_day0)
    monkeypatch.setattr(evaluator_module, "_store_ens_snapshot", lambda *args, **kwargs: "snap-day0")
    monkeypatch.setattr(evaluator_module, "_store_snapshot_p_raw", lambda *args, **kwargs: None)
    _patch_mature_calibration(monkeypatch)
    monkeypatch.setattr(evaluator_module, "MarketAnalysis", DummyAnalysis)
    _stub_full_family_scan(monkeypatch)
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
        decision_time=datetime.now(timezone.utc) + timedelta(minutes=5),
    )

    assert decisions[0].should_trade is True, decisions[0].rejection_reasons
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
    monkeypatch.setattr(evaluator_module, "get_mode", lambda: "test")
    candidate = MarketCandidate(
        city=NYC,
        target_date=str(date.today()),
        outcomes=[
            {"title": "38°F or lower", "range_low": None, "range_high": 38, "token_id": "yes0", "no_token_id": "no0", "market_id": "m0", "price": 0.34},
            {"title": "39-40°F", "range_low": 39, "range_high": 40, "token_id": "yes1", "no_token_id": "no1", "market_id": "m1", "price": 0.35},
            {"title": "41-42°F", "range_low": 41, "range_high": 42, "token_id": "yes2", "no_token_id": "no2", "market_id": "m2", "price": 0.33},
            {"title": "43°F or higher", "range_low": 43, "range_high": None, "token_id": "yes3", "no_token_id": "no3", "market_id": "m3", "price": 0.32},
        ],
        hours_since_open=30.0,
        hours_to_resolution=4.0,
        observation=Day0ObservationContext(
            high_so_far=44.0,
            low_so_far=39.0,
            current_temp=43.0,
            source="wu_api",
            observation_time=datetime.now(timezone.utc).isoformat(),
            unit="F",
        ),
        discovery_mode=DiscoveryMode.DAY0_CAPTURE.value,
    )

    monkeypatch.setattr(
        evaluator_module,
        "fetch_ensemble",
        lambda city, forecast_days=2, model=None, role=None: (
            lambda base_utc: {
                "members_hourly": np.ones((51, 12)) * 44.0,
                "times": [
                    (base_utc + timedelta(hours=i)).replace(microsecond=0).isoformat()
                    for i in range(12)
                ],
                **_entry_forecast_evidence(
                    model="ecmwf_ifs025",
                    issue_time=datetime.now(timezone.utc),
                    first_valid_time=base_utc,
                    fetch_time=datetime.now(timezone.utc),
                ),
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

    class DummyEnsembleSignal:
        def __init__(self, *args, **kwargs):
            self.member_maxes = np.full(51, 44.0)
            self.member_extrema = self.member_maxes
            self.bias_corrected = False

        def spread(self):
            from src.types.temperature import TemperatureDelta

            return TemperatureDelta(2.0, "F")

        def spread_float(self):
            return 2.0

    monkeypatch.setattr(evaluator_module, "EnsembleSignal", DummyEnsembleSignal)
    monkeypatch.setattr(evaluator_module, "_get_day0_temporal_context", lambda city, target_date, observation=None: None)

    decisions = evaluator_module.evaluate_candidate(
        candidate,
        conn=None,
        portfolio=PortfolioState(bankroll=150.0),
        clob=type("DummyClob", (), {"get_best_bid_ask": lambda self, token_id: (0.34, 0.36, 20.0, 20.0)})(),
        limits=evaluator_module.RiskLimits(),
        decision_time=datetime.now(timezone.utc) + timedelta(minutes=5),
    )

    assert len(decisions) == 1
    assert decisions[0].should_trade is False
    assert decisions[0].rejection_stage == "SIGNAL_QUALITY"
    assert "Solar/DST context unavailable for Day0" in decisions[0].rejection_reasons[0]


def test_gfs_crosscheck_uses_local_target_day_hours_instead_of_first_24h(monkeypatch):
    monkeypatch.setattr(evaluator_module, "get_mode", lambda: "test")
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
                "title": "33-34°F",
                "range_low": 33,
                "range_high": 34,
                "token_id": "yes-mid",
                "no_token_id": "no-mid",
                "market_id": "m-mid",
                "price": 0.31,
            },
            {
                "title": "35°F or higher",
                "range_low": 35,
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
        def __init__(self, members_hourly, times, city, target_d, settlement_semantics=None, decision_time=None, **kwargs):
            self.member_maxes = np.full(51, 55.0)
            self.member_extrema = self.member_maxes
            self.bias_corrected = False

        def p_raw_vector(self, bins, n_mc=None):
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

    def _fetch_ensemble(city, forecast_days=2, model=None, role=None):
        if model == "gfs025":
            return {
                "members_hourly": gfs_members,
                "times": times,
                **_entry_forecast_evidence(
                    model="gfs025",
                    source_id="openmeteo_ensemble_gfs025",
                    role=role or "diagnostic",
                    issue_time=datetime(2026, 1, 14, 0, 0, tzinfo=timezone.utc),
                    first_valid_time=datetime(2026, 1, 14, 5, 0, tzinfo=timezone.utc),
                    fetch_time=datetime(2026, 1, 14, 6, 0, tzinfo=timezone.utc),
                    n_members=31,
                ),
            }
        return {
            "members_hourly": ecmwf_members,
            "times": times,
            **_entry_forecast_evidence(
                model="ecmwf_ifs025",
                source_id="tigge",
                role=role or "entry_primary",
                issue_time=datetime(2026, 1, 14, 0, 0, tzinfo=timezone.utc),
                first_valid_time=datetime(2026, 1, 14, 5, 0, tzinfo=timezone.utc),
                fetch_time=datetime(2026, 1, 14, 6, 0, tzinfo=timezone.utc),
                n_members=51,
            ),
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
    _patch_mature_calibration(monkeypatch)
    monkeypatch.setattr(evaluator_module, "MarketAnalysis", DummyAnalysis)
    _stub_full_family_scan(monkeypatch)
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
    monkeypatch.setattr(evaluator_module, "get_mode", lambda: "test")
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

    def _fetch(city, forecast_days=2, model=None, role=None):
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
            **_entry_forecast_evidence(
                model="ecmwf_ifs025",
                source_id="tigge",
                role=role or "entry_primary",
                issue_time=datetime(2026, 1, 14, 0, 0, tzinfo=timezone.utc),
                first_valid_time=datetime(2026, 1, 14, 5, 0, tzinfo=timezone.utc),
                fetch_time=datetime(2026, 1, 14, 6, 0, tzinfo=timezone.utc),
                n_members=51,
            ),
        }

    monkeypatch.setattr(evaluator_module, "fetch_ensemble", _fetch)
    monkeypatch.setattr(evaluator_module, "validate_ensemble", lambda result, expected_members=51: result is not None)
    monkeypatch.setattr(evaluator_module, "_store_ens_snapshot", lambda *args, **kwargs: "snap-gfs-fail")
    monkeypatch.setattr(evaluator_module, "_store_snapshot_p_raw", lambda *args, **kwargs: None)
    _patch_mature_calibration(monkeypatch)

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


## Paper-mode test removed — Zeus is live-only (Phase 1 decommission).
## Original: test_build_exit_context_uses_market_price_as_best_bid_in_paper_mode


def test_build_exit_context_preserves_missing_best_bid_for_exit_audit():
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
        trade_id="live-buy-yes-missing-bid",
        market_id="m1",
        city="NYC",
        cluster="NYC",
        target_date="2026-04-01",
        bin_label="39-40°F",
        direction="buy_yes",
        state="holding",
        chain_state="synced",
        last_monitor_prob=0.41,
        last_monitor_prob_is_fresh=True,
        last_monitor_market_price=0.46,
        last_monitor_market_price_is_fresh=True,
        last_monitor_best_bid=None,
    )

    ctx = cycle_runtime._build_exit_context(
        pos,
        edge_ctx,
        hours_to_settlement=4.0,
        ExitContext=ExitContext,
    )

    assert ctx.best_bid is None
    assert ctx.current_market_price == pytest.approx(0.46)


def test_monitoring_skips_sell_pending_when_chain_already_missing():
    pos = Position(
        trade_id="retry-missing-chain",
        market_id="m1",
        city="NYC",
        cluster="NYC",
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
        cluster="NYC",
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
    assert tracker.exits[0].state == "admin_closed"
    assert tracker.exits[0].admin_exit_reason == "EXIT_CHAIN_MISSING_REVIEW_REQUIRED"
    assert tracker.exits[0].exit_reason == "EXIT_CHAIN_MISSING_REVIEW_REQUIRED"
    assert summary["exit_chain_missing_closed"] == 1


def test_monitoring_defers_exit_pending_missing_resolution_to_exit_lifecycle(monkeypatch):
    pos = Position(
        trade_id="retry-missing-chain-close",
        market_id="m1",
        city="NYC",
        cluster="NYC",
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

    closed = Position(
        trade_id="retry-missing-chain-close",
        market_id="m1",
        city="NYC",
        cluster="NYC",
        target_date="2026-04-01",
        bin_label="39-40°F",
        direction="buy_yes",
        state="admin_closed",
        exit_reason="EXIT_CHAIN_MISSING_REVIEW_REQUIRED",
    )

    monkeypatch.setattr(
        "src.execution.exit_lifecycle.handle_exit_pending_missing",
        lambda portfolio, position: {"action": "closed", "position": closed},
    )
    monkeypatch.setattr(
        cycle_runner,
        "void_position",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("cycle_runtime should delegate exit_pending_missing closure")),
    )

    p_dirty, t_dirty = cycle_runner._execute_monitoring_phase(
        None,
        type("LiveClob", (), {"paper_mode": False})(),
        portfolio,
        artifact,
        Tracker(),
        summary,
    )

    assert p_dirty is True
    assert t_dirty is True
    assert summary["exit_chain_missing_closed"] == 1


def test_monitoring_skips_backoff_exhausted_chain_missing_until_settlement():
    pos = Position(
        trade_id="backoff-missing-chain",
        market_id="m1",
        city="NYC",
        cluster="NYC",
        target_date="2026-04-01",
        bin_label="39-40°F",
        direction="buy_yes",
        state="pending_exit",
        chain_state="exit_pending_missing",
        exit_state="backoff_exhausted",
        next_exit_retry_at=None,
    )
    portfolio = PortfolioState(positions=[pos])
    artifact = cycle_runner.CycleArtifact(mode="test", started_at="2026-01-01T00:00:00Z")
    summary = {"monitors": 0, "exits": 0}

    class Tracker:
        def record_exit(self, position):
            raise AssertionError("backoff_exhausted chain-missing positions should wait for settlement, not admin-close")

    p_dirty, t_dirty = cycle_runner._execute_monitoring_phase(
        None,
        type("LiveClob", (), {"paper_mode": False})(),
        portfolio,
        artifact,
        Tracker(),
        summary,
    )

    assert p_dirty is False
    assert t_dirty is False
    assert portfolio.positions == [pos]
    assert summary["monitor_skipped_exit_pending_missing"] == 1


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


def test_store_ens_snapshot_links_openmeteo_valid_time_without_faking_issue_time(tmp_path):
    db_path = tmp_path / "zeus.db"
    conn = get_connection(db_path)
    init_schema(conn)

    fetch_time = datetime(2026, 1, 14, 6, 5, tzinfo=timezone.utc)
    # Slice A3 follow-up (PR #19 review fix, 2026-04-26): the writer requires
    # `member_extrema` (not the old `member_maxes` name) and now also requires
    # `temperature_metric` (MetricIdentity) — pre-A3 it silently defaulted to
    # HIGH; post-A3 it raises. The DummyEns fixture pre-existed both gaps and
    # was already failing on origin/main with `AttributeError: member_extrema`;
    # A3 just changed the failure surface to the metric assertion. Fixing the
    # fixture to satisfy both contracts lets the test exercise the writer.
    ens = type(
        "DummyEns",
        (),
        {
            "member_extrema": np.array([40.0, 41.0, 42.0]),
            "spread_float": lambda self: 1.25,
            "is_bimodal": lambda self: False,
            "temperature_metric": HIGH_LOCALDAY_MAX,
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
    evaluator_module._store_snapshot_p_raw(conn, snapshot_id, np.array([0.2, 0.3, 0.5]))
    v2_row = conn.execute(
        """
        SELECT issue_time, valid_time, available_at, fetch_time, p_raw_json,
               temperature_metric, physical_quantity, observation_field,
               data_version, training_allowed, causality_status, authority,
               members_unit, unit
        FROM ensemble_snapshots_v2
        WHERE snapshot_id = ? AND city = ? AND target_date = ?
        """,
        (snapshot_id, NYC.name, "2026-01-15"),
    ).fetchone()
    legacy_row = conn.execute(
        """
        SELECT issue_time, valid_time, available_at, fetch_time, p_raw_json,
               data_version, authority, temperature_metric
        FROM ensemble_snapshots
        WHERE snapshot_id = ? AND city = ? AND target_date = ?
        """,
        (snapshot_id, NYC.name, "2026-01-15"),
    ).fetchone()
    conn.close()

    assert snapshot_id
    assert v2_row is not None
    assert v2_row["issue_time"] is None
    assert v2_row["valid_time"] == "2026-01-14T05:00:00+00:00"
    assert v2_row["available_at"] == "2026-01-14T06:05:00+00:00"
    assert v2_row["fetch_time"] == "2026-01-14T06:05:00+00:00"
    assert json.loads(v2_row["p_raw_json"]) == [0.2, 0.3, 0.5]
    assert v2_row["temperature_metric"] == HIGH_LOCALDAY_MAX.temperature_metric
    assert v2_row["physical_quantity"] == HIGH_LOCALDAY_MAX.physical_quantity
    assert v2_row["observation_field"] == HIGH_LOCALDAY_MAX.observation_field
    assert v2_row["data_version"] == HIGH_LOCALDAY_MAX.data_version
    assert v2_row["training_allowed"] == 0
    assert v2_row["causality_status"] == "UNKNOWN"
    assert v2_row["authority"] == "VERIFIED"
    assert v2_row["members_unit"] == "degF"
    assert v2_row["unit"] == "F"
    assert legacy_row is not None
    assert legacy_row["issue_time"] is None
    assert legacy_row["valid_time"] == v2_row["valid_time"]
    assert legacy_row["available_at"] == v2_row["available_at"]
    assert legacy_row["fetch_time"] == v2_row["fetch_time"]
    assert json.loads(legacy_row["p_raw_json"]) == [0.2, 0.3, 0.5]
    assert legacy_row["data_version"] == HIGH_LOCALDAY_MAX.data_version
    assert legacy_row["authority"] == "VERIFIED"
    assert legacy_row["temperature_metric"] == HIGH_LOCALDAY_MAX.temperature_metric


def _seed_p_raw_snapshot(conn) -> str:
    fetch_time = datetime(2026, 1, 14, 6, 5, tzinfo=timezone.utc)
    ens = type(
        "DummyEns",
        (),
        {
            "member_extrema": np.array([40.0, 41.0, 42.0]),
            "spread_float": lambda self: 1.25,
            "is_bimodal": lambda self: False,
            "temperature_metric": HIGH_LOCALDAY_MAX,
        },
    )()
    ens_result = {
        "issue_time": None,
        "first_valid_time": datetime(2026, 1, 14, 5, 0, tzinfo=timezone.utc),
        "fetch_time": fetch_time,
        "model": "ecmwf_ifs025",
    }
    return evaluator_module._store_ens_snapshot(
        conn,
        NYC,
        "2026-01-15",
        ens,
        ens_result,
    )


def _support_topology_payload() -> dict:
    return {
        "schema_version": 1,
        "topology_status": "complete",
        "unit": "F",
        "support_count": 3,
        "executable_count": 2,
        "executable_hypothesis_count": 2,
        "executable_mask": [False, True, True],
        "skipped_support_indexes": [0],
        "market_fusion_status_by_support_index": [
            {"support_index": 0, "status": "disabled_non_executable"},
            {"support_index": 1, "status": "pending_executable_quote"},
            {"support_index": 2, "status": "pending_executable_quote"},
        ],
        "requires_atomic_topology": True,
        "support": [
            {"support_index": 0, "label": "60°F or below", "executable": False},
            {"support_index": 1, "label": "61-62°F", "executable": True},
            {"support_index": 2, "label": "63°F or higher", "executable": True},
        ],
    }


def test_store_snapshot_p_raw_persists_support_topology_in_v2_provenance(tmp_path):
    db_path = tmp_path / "zeus.db"
    conn = get_connection(db_path)
    init_schema(conn)

    snapshot_id = _seed_p_raw_snapshot(conn)
    topology = _support_topology_payload()

    assert evaluator_module._store_snapshot_p_raw(
        conn,
        snapshot_id,
        np.array([0.2, 0.3, 0.5]),
        p_raw_topology=topology,
    )
    row = conn.execute(
        """
        SELECT p_raw_json, provenance_json
        FROM ensemble_snapshots_v2
        WHERE snapshot_id = ?
        """,
        (snapshot_id,),
    ).fetchone()
    legacy_row = conn.execute(
        "SELECT p_raw_json FROM ensemble_snapshots WHERE snapshot_id = ?",
        (snapshot_id,),
    ).fetchone()
    conn.close()

    assert json.loads(row["p_raw_json"]) == [0.2, 0.3, 0.5]
    provenance = json.loads(row["provenance_json"])
    assert provenance["writer"] == "evaluator._store_ens_snapshot"
    assert provenance["p_raw_topology"]["executable_mask"] == [False, True, True]
    assert provenance["p_raw_topology"]["skipped_support_indexes"] == [0]
    assert provenance["p_raw_topology"]["executable_hypothesis_count"] == 2
    assert len(provenance["p_raw_topology"]["market_fusion_status_by_support_index"]) == 3
    assert json.loads(legacy_row["p_raw_json"]) == [0.2, 0.3, 0.5]


@pytest.mark.parametrize(
    "mutate",
    [
        lambda topology: topology.update({"topology_status": "corrupt_status"}),
        lambda topology: topology.update({"executable_count": 999}),
        lambda topology: topology.update({"skipped_support_indexes": [2]}),
        lambda topology: topology["market_fusion_status_by_support_index"][1].update(
            {"status": "disabled_non_executable"}
        ),
    ],
)
def test_store_snapshot_p_raw_rejects_invalid_support_topology(tmp_path, mutate):
    db_path = tmp_path / "zeus.db"
    conn = get_connection(db_path)
    init_schema(conn)
    snapshot_id = _seed_p_raw_snapshot(conn)
    topology = _support_topology_payload()
    mutate(topology)

    assert not evaluator_module._store_snapshot_p_raw(
        conn,
        snapshot_id,
        np.array([0.2, 0.3, 0.5]),
        p_raw_topology=topology,
    )
    row = conn.execute(
        "SELECT p_raw_json FROM ensemble_snapshots_v2 WHERE snapshot_id = ?",
        (snapshot_id,),
    ).fetchone()
    conn.close()

    assert row["p_raw_json"] is None


def test_store_ens_snapshot_routes_to_attached_world_db(tmp_path):
    trade_db = tmp_path / "zeus_trades.db"
    world_db = tmp_path / "zeus-world.db"
    trade_conn = get_connection(trade_db)
    init_schema(trade_conn)
    trade_conn.close()
    world_conn = get_connection(world_db)
    init_schema(world_conn)
    world_conn.close()

    conn = get_connection(trade_db)
    conn.execute("ATTACH DATABASE ? AS world", (str(world_db),))

    fetch_time = datetime(2026, 1, 14, 6, 5, tzinfo=timezone.utc)
    # Slice A3 follow-up (see twin fix above): satisfy both `member_extrema`
    # and `temperature_metric` contracts the writer enforces.
    ens = type(
        "DummyEns",
        (),
        {
            "member_extrema": np.array([40.0, 41.0, 42.0]),
            "spread_float": lambda self: 1.25,
            "is_bimodal": lambda self: False,
            "temperature_metric": HIGH_LOCALDAY_MAX,
        },
    )()
    ens_result = {
        "issue_time": datetime(2026, 1, 14, 0, 0, tzinfo=timezone.utc),
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
    evaluator_module._store_snapshot_p_raw(conn, snapshot_id, np.array([0.2, 0.3, 0.5]))

    main_legacy_count = conn.execute(
        "SELECT COUNT(*) FROM main.ensemble_snapshots WHERE city = 'NYC'"
    ).fetchone()[0]
    main_v2_count = conn.execute(
        "SELECT COUNT(*) FROM main.ensemble_snapshots_v2 WHERE city = 'NYC'"
    ).fetchone()[0]
    world_v2_row = conn.execute(
        """
        SELECT p_raw_json, data_version, training_allowed, causality_status,
               temperature_metric, physical_quantity, observation_field,
               members_unit, unit
        FROM world.ensemble_snapshots_v2
        WHERE snapshot_id = ? AND city = 'NYC'
        """,
        (snapshot_id,),
    ).fetchone()
    world_legacy_row = conn.execute(
        """
        SELECT p_raw_json, data_version, authority, temperature_metric
        FROM world.ensemble_snapshots
        WHERE snapshot_id = ? AND city = 'NYC'
        """,
        (snapshot_id,),
    ).fetchone()
    conn.close()

    assert snapshot_id
    assert main_legacy_count == 0
    assert main_v2_count == 0
    assert world_v2_row is not None
    assert json.loads(world_v2_row["p_raw_json"]) == [0.2, 0.3, 0.5]
    assert world_v2_row["data_version"] == HIGH_LOCALDAY_MAX.data_version
    assert world_v2_row["training_allowed"] == 1
    assert world_v2_row["causality_status"] == "OK"
    assert world_v2_row["temperature_metric"] == HIGH_LOCALDAY_MAX.temperature_metric
    assert world_v2_row["physical_quantity"] == HIGH_LOCALDAY_MAX.physical_quantity
    assert world_v2_row["observation_field"] == HIGH_LOCALDAY_MAX.observation_field
    assert world_v2_row["members_unit"] == "degF"
    assert world_v2_row["unit"] == "F"
    assert world_legacy_row is not None
    assert json.loads(world_legacy_row["p_raw_json"]) == [0.2, 0.3, 0.5]
    assert world_legacy_row["data_version"] == HIGH_LOCALDAY_MAX.data_version
    assert world_legacy_row["authority"] == "VERIFIED"
    assert world_legacy_row["temperature_metric"] == HIGH_LOCALDAY_MAX.temperature_metric


def test_store_ens_snapshot_refuses_legacy_id_collision_without_p_raw_corruption(tmp_path):
    db_path = tmp_path / "zeus.db"
    conn = get_connection(db_path)
    init_schema(conn)
    conn.execute(
        """
        INSERT INTO ensemble_snapshots
        (snapshot_id, city, target_date, issue_time, valid_time, available_at,
         fetch_time, lead_hours, members_json, spread, is_bimodal,
         model_version, data_version, authority, temperature_metric)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            1,
            "OLD",
            "2026-01-01",
            "2026-01-01T00:00:00+00:00",
            "2026-01-01T01:00:00+00:00",
            "2026-01-01T00:05:00+00:00",
            "2026-01-01T00:05:00+00:00",
            1.0,
            "[1.0]",
            0.0,
            0,
            "old_model",
            HIGH_LOCALDAY_MAX.data_version,
            "VERIFIED",
            HIGH_LOCALDAY_MAX.temperature_metric,
        ),
    )
    conn.commit()

    fetch_time = datetime(2026, 1, 14, 6, 5, tzinfo=timezone.utc)
    ens = type(
        "DummyEns",
        (),
        {
            "member_extrema": np.array([40.0, 41.0, 42.0]),
            "spread_float": lambda self: 1.25,
            "is_bimodal": lambda self: False,
            "temperature_metric": HIGH_LOCALDAY_MAX,
        },
    )()
    ens_result = {
        "issue_time": datetime(2026, 1, 14, 0, 0, tzinfo=timezone.utc),
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
    old_row = conn.execute(
        "SELECT city, target_date, p_raw_json FROM ensemble_snapshots WHERE snapshot_id = 1"
    ).fetchone()
    v2_count = conn.execute(
        "SELECT COUNT(*) FROM ensemble_snapshots_v2 WHERE city = 'NYC'"
    ).fetchone()[0]
    conn.close()

    assert snapshot_id == ""
    assert old_row["city"] == "OLD"
    assert old_row["target_date"] == "2026-01-01"
    assert old_row["p_raw_json"] is None
    assert v2_count == 0


def test_store_ens_snapshot_reuses_v2_conflict_without_legacy_fallback(tmp_path):
    db_path = tmp_path / "zeus.db"
    conn = get_connection(db_path)
    init_schema(conn)
    issue_time = "2026-01-14T00:00:00+00:00"
    conn.execute(
        """
        INSERT INTO ensemble_snapshots_v2
        (city, target_date, temperature_metric, physical_quantity,
         observation_field, issue_time, valid_time, available_at, fetch_time,
         lead_hours, members_json, spread, is_bimodal, model_version,
         data_version, training_allowed, causality_status, boundary_ambiguous,
         provenance_json, authority, members_unit, unit)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            NYC.name,
            "2026-01-15",
            HIGH_LOCALDAY_MAX.temperature_metric,
            HIGH_LOCALDAY_MAX.physical_quantity,
            HIGH_LOCALDAY_MAX.observation_field,
            issue_time,
            "2026-01-14T01:00:00+00:00",
            "2026-01-14T00:10:00+00:00",
            "2026-01-14T00:10:00+00:00",
            1.0,
            "[40.0, 41.0, 42.0]",
            1.0,
            0,
            "old_model",
            HIGH_LOCALDAY_MAX.data_version,
            1,
            "OK",
            0,
            "{}",
            "VERIFIED",
            "degF",
            "F",
        ),
    )
    conn.commit()

    fetch_time = datetime(2026, 1, 14, 6, 5, tzinfo=timezone.utc)
    ens = type(
        "DummyEns",
        (),
        {
            "member_extrema": np.array([40.0, 41.0, 42.0]),
            "spread_float": lambda self: 1.25,
            "is_bimodal": lambda self: False,
            "temperature_metric": HIGH_LOCALDAY_MAX,
        },
    )()
    ens_result = {
        "issue_time": datetime(2026, 1, 14, 0, 0, tzinfo=timezone.utc),
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
    legacy_count = conn.execute(
        "SELECT COUNT(*) FROM ensemble_snapshots WHERE city = ?",
        (NYC.name,),
    ).fetchone()[0]
    v2_rows = conn.execute(
        """
        SELECT snapshot_id, valid_time, available_at, fetch_time, model_version
          FROM ensemble_snapshots_v2
         WHERE city = ?
        """,
        (NYC.name,),
    ).fetchall()
    conn.close()

    assert snapshot_id
    assert legacy_count == 1
    assert len(v2_rows) == 1
    assert str(v2_rows[0]["snapshot_id"]) == snapshot_id
    assert v2_rows[0]["valid_time"] is None
    assert v2_rows[0]["available_at"] == "2026-01-14T06:05:00+00:00"
    assert v2_rows[0]["fetch_time"] == "2026-01-14T06:05:00+00:00"
    assert v2_rows[0]["model_version"] == "ecmwf_ifs025"


@pytest.mark.skip(
    reason=(
        "2026-05-01 structural rewrite: collect_open_ens_cycle now writes to "
        "ensemble_snapshots_v2 (not legacy ensemble_snapshots) with data_version "
        "ecmwf_opendata_mx2t6_local_calendar_day_max_v1 (and _min_v1). The new "
        "antibody tests/test_opendata_writes_v2_table.py covers the replacement "
        "behavior. This legacy test asserted the v1 path that is now retired."
    )
)
def test_ecmwf_open_data_collector_marks_rows_unverified_non_executable(monkeypatch, tmp_path):
    from src.data.forecast_source_registry import SOURCES, SourceNotEnabled, gate_source_role

    db_path = tmp_path / "zeus.db"
    conn = get_connection(db_path)
    init_schema(conn)

    test_city = City(
        name="NYC",
        lat=40.7772,
        lon=-73.8726,
        timezone="America/New_York",
        cluster="NYC",
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
        """
        SELECT city, target_date, data_version, model_version, p_raw_json, authority
        FROM ensemble_snapshots
        ORDER BY target_date
        """
    ).fetchall()
    conn.close()

    assert result["snapshots_inserted"] == 2
    assert result["source_id"] == "ecmwf_open_data"
    assert result["forecast_source_role"] == "diagnostic"
    assert result["degradation_level"] == "DIAGNOSTIC_NON_EXECUTABLE"
    assert result["authority"] == "UNVERIFIED"
    assert [row["target_date"] for row in rows] == ["2026-03-31", "2026-04-01"]
    assert all(row["data_version"] == DATA_VERSION for row in rows)
    assert all(row["p_raw_json"] is None for row in rows)
    assert all(row["authority"] == "UNVERIFIED" for row in rows)
    with pytest.raises(SourceNotEnabled, match="entry_primary"):
        gate_source_role(SOURCES["ecmwf_open_data"], "entry_primary")


@pytest.mark.skip(
    reason=(
        "Phase 3 (src/ingest_main.py introduction) moved every ecmwf_open_data "
        "job out of src/main.py. The 2026-05-01 daemon-correctness fix renamed "
        "the jobs to ingest_opendata_daily_mx2t6 / _mn2t6. Replacement antibody: "
        "tests/test_opendata_writes_v2_table.py covers the ingest daemon path."
    )
)
def test_main_registers_only_policy_owned_ecmwf_open_data_jobs(monkeypatch, tmp_path):
    from src.data.forecast_source_registry import SOURCES

    assert SOURCES["ecmwf_open_data"].allowed_roles == ("diagnostic",)
    assert SOURCES["ecmwf_open_data"].degradation_level == "DIAGNOSTIC_NON_EXECUTABLE"

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
    monkeypatch.setattr(main_module, "get_world_connection", lambda: get_connection(db_path))
    monkeypatch.setattr(main_module, "get_trade_connection", lambda: get_connection(db_path))
    monkeypatch.setattr(main_module, "init_schema", lambda conn: None)
    monkeypatch.setattr(main_module.os, "environ", {"ZEUS_MODE": "live"})
    monkeypatch.setattr(main_module, "_startup_wallet_check", lambda: None)
    monkeypatch.setattr(main_module, "_startup_data_health_check", lambda conn: None)
    monkeypatch.setattr(main_module, "_assert_live_safe_strategies_or_exit", lambda: None)
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

    first = ensemble_client.fetch_ensemble(
        NYC,
        forecast_days=3,
        model="ecmwf_ifs025",
        role="monitor_fallback",
    )
    second = ensemble_client.fetch_ensemble(
        NYC,
        forecast_days=3,
        model="ecmwf_ifs025",
        role="monitor_fallback",
    )

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

    long_result = ensemble_client.fetch_ensemble(
        NYC,
        forecast_days=8,
        model="ecmwf_ifs025",
        role="monitor_fallback",
    )
    short_result = ensemble_client.fetch_ensemble(
        NYC,
        forecast_days=3,
        model="ecmwf_ifs025",
        role="monitor_fallback",
    )

    assert long_result is not None
    assert short_result is not None
    assert calls["n"] == 1


def test_run_cycle_clears_ensemble_cache_each_cycle(monkeypatch, tmp_path):
    db_path = tmp_path / "zeus.db"
    conn = get_connection(db_path)
    init_schema(conn)
    conn.close()

    class DummyClob:
        def __init__(self):
            pass

    cleared = {"n": 0}

    monkeypatch.setattr(cycle_runner, "get_current_level", lambda: RiskLevel.GREEN)
    monkeypatch.setattr(cycle_runner, "get_connection", lambda: get_connection(db_path))
    monkeypatch.setattr(cycle_runner, "load_portfolio", lambda: PortfolioState())
    monkeypatch.setattr(cycle_runner, "save_portfolio", lambda state, *args, **kwargs: None)
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
        def __init__(self):
            pass

    cleared = {"n": 0}

    monkeypatch.setattr(cycle_runner, "get_current_level", lambda: RiskLevel.GREEN)
    monkeypatch.setattr(cycle_runner, "get_connection", lambda: get_connection(db_path))
    monkeypatch.setattr(cycle_runner, "load_portfolio", lambda: PortfolioState())
    monkeypatch.setattr(cycle_runner, "save_portfolio", lambda state, *args, **kwargs: None)
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


def _monitor_chain_deps(now: datetime):
    return types.SimpleNamespace(
        MonitorResult=cycle_runner.MonitorResult,
        logger=logging.getLogger("test_monitor_chain_missing"),
        cities_by_name={"NYC": NYC},
        _utcnow=lambda: now,
        has_acknowledged_quarantine_clear=lambda token_id: False,
    )


def test_orange_risk_exits_favorable_position_through_monitor_lifecycle(monkeypatch):
    pos = _position(
        trade_id="orange-favorable",
        state="holding",
        entry_price=0.40,
        p_posterior=0.62,
        target_date="2026-04-03",
    )
    portfolio = PortfolioState(positions=[pos])
    artifact = CycleArtifact(mode="opening_hunt", started_at="2026-04-01T20:00:00Z")
    summary = {"monitors": 0, "exits": 0, "risk_level": RiskLevel.ORANGE.value}
    captured = {}

    def _refresh_position(conn, clob, refreshed_pos):
        refreshed_pos.last_monitor_market_price = 0.43
        refreshed_pos.last_monitor_market_price_is_fresh = True
        refreshed_pos.last_monitor_best_bid = 0.42
        refreshed_pos.last_monitor_best_ask = 0.43
        refreshed_pos.last_monitor_prob = 0.62
        refreshed_pos.last_monitor_prob_is_fresh = True
        return types.SimpleNamespace(
            p_market=np.array([0.43]),
            p_posterior=0.62,
            divergence_score=0.0,
            market_velocity_1h=0.0,
            forward_edge=0.21,
        )

    def _execute_exit(**kwargs):
        captured["exit_context"] = kwargs["exit_context"]
        captured["position"] = kwargs["position"]
        return "sell_pending: orange"

    monkeypatch.setattr("src.engine.monitor_refresh.refresh_position", _refresh_position)
    monkeypatch.setattr("src.execution.exit_lifecycle.execute_exit", _execute_exit)

    p_dirty, t_dirty = cycle_runtime.execute_monitoring_phase(
        conn=None,
        clob=types.SimpleNamespace(),
        portfolio=portfolio,
        artifact=artifact,
        tracker=StrategyTracker(),
        summary=summary,
        deps=_monitor_chain_deps(datetime(2026, 4, 1, 20, 0, tzinfo=timezone.utc)),
    )

    assert p_dirty is True
    assert t_dirty is False
    assert summary["risk_orange_favorable_exits"] == 1
    assert summary["exits"] == 1
    assert artifact.monitor_results[0].should_exit is True
    assert artifact.monitor_results[0].exit_reason == "ORANGE_FAVORABLE_EXIT"
    assert captured["exit_context"].exit_reason == "ORANGE_FAVORABLE_EXIT"
    assert captured["position"].exit_trigger == "ORANGE_FAVORABLE_EXIT"
    assert "orange_favorable_bid_gate" in pos.applied_validations
    assert "orange_favorable_net_exit_gate" in pos.applied_validations


def test_orange_risk_holds_when_bid_is_unfavorable(monkeypatch):
    pos = _position(
        trade_id="orange-unfavorable",
        state="holding",
        entry_price=0.40,
        p_posterior=0.62,
        target_date="2026-04-03",
    )
    portfolio = PortfolioState(positions=[pos])
    artifact = CycleArtifact(mode="opening_hunt", started_at="2026-04-01T20:00:00Z")
    summary = {"monitors": 0, "exits": 0, "risk_level": RiskLevel.ORANGE.value}

    def _refresh_position(conn, clob, refreshed_pos):
        refreshed_pos.last_monitor_market_price = 0.39
        refreshed_pos.last_monitor_market_price_is_fresh = True
        refreshed_pos.last_monitor_best_bid = 0.39
        refreshed_pos.last_monitor_best_ask = 0.40
        refreshed_pos.last_monitor_prob = 0.62
        refreshed_pos.last_monitor_prob_is_fresh = True
        return types.SimpleNamespace(
            p_market=np.array([0.39]),
            p_posterior=0.62,
            divergence_score=0.0,
            market_velocity_1h=0.0,
            forward_edge=0.23,
        )

    monkeypatch.setattr("src.engine.monitor_refresh.refresh_position", _refresh_position)
    monkeypatch.setattr(
        "src.execution.exit_lifecycle.execute_exit",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("unfavorable ORANGE bid must hold")),
    )

    p_dirty, t_dirty = cycle_runtime.execute_monitoring_phase(
        conn=None,
        clob=types.SimpleNamespace(),
        portfolio=portfolio,
        artifact=artifact,
        tracker=StrategyTracker(),
        summary=summary,
        deps=_monitor_chain_deps(datetime(2026, 4, 1, 20, 0, tzinfo=timezone.utc)),
    )

    assert p_dirty is True
    assert t_dirty is False
    assert summary["risk_orange_holds"] == 1
    assert summary["exits"] == 0
    assert artifact.monitor_results[0].should_exit is False
    assert pos.exit_reason == ""


def test_orange_risk_does_not_override_incomplete_exit_context(monkeypatch):
    pos = _position(
        trade_id="orange-incomplete-authority",
        state="holding",
        entry_price=0.40,
        p_posterior=0.62,
        target_date="2026-04-03",
    )
    portfolio = PortfolioState(positions=[pos])
    artifact = CycleArtifact(mode="opening_hunt", started_at="2026-04-01T20:00:00Z")
    summary = {"monitors": 0, "exits": 0, "risk_level": RiskLevel.ORANGE.value}

    def _refresh_position(conn, clob, refreshed_pos):
        refreshed_pos.last_monitor_market_price = 0.43
        refreshed_pos.last_monitor_market_price_is_fresh = True
        refreshed_pos.last_monitor_best_bid = 0.42
        refreshed_pos.last_monitor_best_ask = 0.43
        refreshed_pos.last_monitor_prob = 0.62
        refreshed_pos.last_monitor_prob_is_fresh = False
        return types.SimpleNamespace(
            p_market=np.array([0.43]),
            p_posterior=0.62,
            divergence_score=0.0,
            market_velocity_1h=0.0,
            forward_edge=0.19,
        )

    monkeypatch.setattr("src.engine.monitor_refresh.refresh_position", _refresh_position)
    monkeypatch.setattr(
        "src.execution.exit_lifecycle.execute_exit",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("incomplete ORANGE authority must hold")),
    )

    p_dirty, t_dirty = cycle_runtime.execute_monitoring_phase(
        conn=None,
        clob=types.SimpleNamespace(),
        portfolio=portfolio,
        artifact=artifact,
        tracker=StrategyTracker(),
        summary=summary,
        deps=_monitor_chain_deps(datetime(2026, 4, 1, 20, 0, tzinfo=timezone.utc)),
    )

    assert p_dirty is True
    assert t_dirty is False
    assert summary["risk_orange_holds"] == 1
    assert summary["exits"] == 0
    assert artifact.monitor_results[0].should_exit is False
    assert artifact.monitor_results[0].exit_reason.startswith("INCOMPLETE_EXIT_CONTEXT")


def test_yellow_risk_does_not_take_favorable_exit(monkeypatch):
    pos = _position(
        trade_id="yellow-favorable",
        state="holding",
        entry_price=0.40,
        p_posterior=0.62,
        target_date="2026-04-03",
    )
    portfolio = PortfolioState(positions=[pos])
    artifact = CycleArtifact(mode="opening_hunt", started_at="2026-04-01T20:00:00Z")
    summary = {"monitors": 0, "exits": 0, "risk_level": RiskLevel.YELLOW.value}

    def _refresh_position(conn, clob, refreshed_pos):
        refreshed_pos.last_monitor_market_price = 0.43
        refreshed_pos.last_monitor_market_price_is_fresh = True
        refreshed_pos.last_monitor_best_bid = 0.42
        refreshed_pos.last_monitor_best_ask = 0.43
        refreshed_pos.last_monitor_prob = 0.62
        refreshed_pos.last_monitor_prob_is_fresh = True
        return types.SimpleNamespace(
            p_market=np.array([0.43]),
            p_posterior=0.62,
            divergence_score=0.0,
            market_velocity_1h=0.0,
            forward_edge=0.19,
        )

    monkeypatch.setattr("src.engine.monitor_refresh.refresh_position", _refresh_position)
    monkeypatch.setattr(
        "src.execution.exit_lifecycle.execute_exit",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("YELLOW must not trigger ORANGE exit")),
    )

    p_dirty, t_dirty = cycle_runtime.execute_monitoring_phase(
        conn=None,
        clob=types.SimpleNamespace(),
        portfolio=portfolio,
        artifact=artifact,
        tracker=StrategyTracker(),
        summary=summary,
        deps=_monitor_chain_deps(datetime(2026, 4, 1, 20, 0, tzinfo=timezone.utc)),
    )

    assert p_dirty is True
    assert t_dirty is False
    assert "risk_orange_favorable_exits" not in summary
    assert summary["exits"] == 0
    assert artifact.monitor_results[0].should_exit is False


def test_monitor_refresh_failure_near_settlement_is_operator_visible(monkeypatch):
    pos = _position(trade_id="monitor-chain-missing", state="day0_window")
    portfolio = PortfolioState(positions=[pos])
    artifact = CycleArtifact(mode="day0_capture", started_at="2026-04-01T20:00:00Z")
    summary = {"monitors": 0, "exits": 0}

    monkeypatch.setattr(
        "src.engine.monitor_refresh.refresh_position",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("refresh exploded")),
    )

    cycle_runtime.execute_monitoring_phase(
        conn=None,
        clob=types.SimpleNamespace(),
        portfolio=portfolio,
        artifact=artifact,
        tracker=StrategyTracker(),
        summary=summary,
        deps=_monitor_chain_deps(datetime(2026, 4, 1, 23, 0, tzinfo=timezone.utc)),
    )

    assert summary["monitor_failed"] == 1
    assert summary["monitor_chain_missing"] == 1
    assert summary["monitor_chain_missing_positions"] == ["monitor-chain-missing"]
    assert summary["monitor_chain_missing_reasons"][0]["reason"] == "refresh_failed:RuntimeError"
    assert len(artifact.monitor_results) == 1
    assert artifact.monitor_results[0].exit_reason == "MONITOR_CHAIN_MISSING:refresh_failed:RuntimeError"


def test_monitor_refresh_failure_far_from_settlement_is_not_chain_missing(monkeypatch):
    pos = _position(trade_id="monitor-far", state="holding", target_date="2026-04-10")
    portfolio = PortfolioState(positions=[pos])
    artifact = CycleArtifact(mode="opening_hunt", started_at="2026-04-01T20:00:00Z")
    summary = {"monitors": 0, "exits": 0}

    monkeypatch.setattr(
        "src.engine.monitor_refresh.refresh_position",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("refresh exploded")),
    )

    cycle_runtime.execute_monitoring_phase(
        conn=None,
        clob=types.SimpleNamespace(),
        portfolio=portfolio,
        artifact=artifact,
        tracker=StrategyTracker(),
        summary=summary,
        deps=_monitor_chain_deps(datetime(2026, 4, 1, 22, 0, tzinfo=timezone.utc)),
    )

    assert summary["monitor_failed"] == 1
    assert "monitor_chain_missing" not in summary
    assert artifact.monitor_results == []


def test_incomplete_exit_context_near_settlement_escalates_monitor_chain(monkeypatch):
    pos = _position(trade_id="monitor-incomplete", state="day0_window")
    portfolio = PortfolioState(positions=[pos])
    artifact = CycleArtifact(mode="day0_capture", started_at="2026-04-01T20:00:00Z")
    summary = {"monitors": 0, "exits": 0}

    monkeypatch.setattr(
        "src.engine.monitor_refresh.refresh_position",
        lambda *args, **kwargs: types.SimpleNamespace(
            p_market=np.array([]),
            p_posterior=0.41,
            divergence_score=0.0,
            market_velocity_1h=0.0,
            forward_edge=0.0,
        ),
    )

    cycle_runtime.execute_monitoring_phase(
        conn=None,
        clob=types.SimpleNamespace(),
        portfolio=portfolio,
        artifact=artifact,
        tracker=StrategyTracker(),
        summary=summary,
        deps=_monitor_chain_deps(datetime(2026, 4, 1, 23, 0, tzinfo=timezone.utc)),
    )

    assert summary["monitor_incomplete_exit_context"] == 1
    assert summary["monitor_chain_missing"] == 1
    assert summary["monitor_chain_missing_reasons"][0]["reason"].startswith(
        "incomplete_exit_context:INCOMPLETE_EXIT_CONTEXT"
    )
    assert len(artifact.monitor_results) == 1
    assert artifact.monitor_results[0].exit_reason.startswith("INCOMPLETE_EXIT_CONTEXT")


def test_monitor_execution_failure_does_not_become_chain_missing(monkeypatch):
    pos = _position(trade_id="monitor-execution-failed", state="day0_window")
    portfolio = PortfolioState(positions=[pos])
    artifact = CycleArtifact(mode="day0_capture", started_at="2026-04-01T20:00:00Z")
    summary = {"monitors": 0, "exits": 0}

    def _refresh_position(conn, clob, pos):
        pos.entry_method = getattr(pos, "entry_method", "ens_member_counting") or "ens_member_counting"
        assert pos.entry_method
        pos.last_monitor_market_price = 0.46
        pos.last_monitor_market_price_is_fresh = True
        pos.last_monitor_best_bid = 0.46
        pos.last_monitor_prob = 0.41
        pos.last_monitor_prob_is_fresh = True
        return types.SimpleNamespace(
            p_market=np.array([0.46]),
            p_posterior=0.41,
            divergence_score=0.0,
            market_velocity_1h=0.0,
            forward_edge=-0.05,
        )

    monkeypatch.setattr("src.engine.monitor_refresh.refresh_position", _refresh_position)
    monkeypatch.setattr(
        Position,
        "evaluate_exit",
        lambda self, exit_context: ExitDecision(True, "EDGE_REVERSAL", trigger="EDGE_REVERSAL"),
    )
    monkeypatch.setattr("src.execution.exit_lifecycle.build_exit_intent", lambda *args, **kwargs: object())
    monkeypatch.setattr(
        "src.execution.exit_lifecycle.execute_exit",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("execution failed")),
    )

    cycle_runtime.execute_monitoring_phase(
        conn=None,
        clob=types.SimpleNamespace(),
        portfolio=portfolio,
        artifact=artifact,
        tracker=StrategyTracker(),
        summary=summary,
        deps=_monitor_chain_deps(datetime(2026, 4, 1, 20, 0, tzinfo=timezone.utc)),
    )

    assert summary["monitor_failed"] == 1
    assert "monitor_chain_missing" not in summary
    assert len(artifact.monitor_results) == 1
    assert artifact.monitor_results[0].exit_reason == "EDGE_REVERSAL"


def test_time_context_failure_near_active_position_escalates_monitor_chain(monkeypatch):
    pos = _position(trade_id="monitor-time-context", state="holding", target_date="not-a-date")
    portfolio = PortfolioState(positions=[pos])
    artifact = CycleArtifact(mode="day0_capture", started_at="2026-04-01T20:00:00Z")
    summary = {"monitors": 0, "exits": 0}

    monkeypatch.setattr(
        "src.engine.monitor_refresh.refresh_position",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("refresh should not run")),
    )

    cycle_runtime.execute_monitoring_phase(
        conn=None,
        clob=types.SimpleNamespace(),
        portfolio=portfolio,
        artifact=artifact,
        tracker=StrategyTracker(),
        summary=summary,
        deps=_monitor_chain_deps(datetime(2026, 4, 1, 20, 0, tzinfo=timezone.utc)),
    )

    assert summary["monitor_failed"] == 1
    assert summary["monitor_chain_missing"] == 1
    assert summary["monitor_chain_missing_reasons"][0]["reason"].startswith("time_context_failed")
    assert len(artifact.monitor_results) == 1
    assert artifact.monitor_results[0].exit_reason.startswith("MONITOR_CHAIN_MISSING:time_context_failed")


def test_monitoring_phase_persists_live_exit_telemetry_chain_with_canonical_entry_baseline(monkeypatch, tmp_path):
    """Current canonical-entry baseline: entry events already exist before Day0/exit.

    Batch G intentionally does not mask the separate legacy ambiguity where a
    position has DAY0_WINDOW_ENTERED but no entry events; that production-source
    audit is deferred to Batch H.
    """
    # A6 (PLAN.md §A6): pin legacy cycle-axis explicitly. Pre-A6 the flag
    # default was OFF; this test was written for that path. Post-A6 the
    # default flips ON, but the legacy axis is still a valid kill-switch
    # path the test verifies.
    monkeypatch.setenv("ZEUS_MARKET_PHASE_DISPATCH", "0")
    db_path = tmp_path / "zeus.db"
    conn = get_connection(db_path)
    init_schema(conn)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS execution_fact (
            intent_id TEXT PRIMARY KEY,
            position_id TEXT,
            decision_id TEXT,
            order_role TEXT NOT NULL CHECK (order_role IN ('entry', 'exit')),
            strategy_key TEXT CHECK (strategy_key IN (
                'settlement_capture',
                'shoulder_sell',
                'center_buy',
                'opening_inertia'
            )),
            posted_at TEXT,
            filled_at TEXT,
            voided_at TEXT,
            submitted_price REAL,
            fill_price REAL,
            shares REAL,
            fill_quality REAL,
            latency_seconds REAL,
            venue_status TEXT,
            terminal_exec_status TEXT
        )
        """
    )
    conn.commit()

    pos = _position(
        trade_id="live-exit-1",
        state="holding",
        decision_snapshot_id="snap-live-exit",
        last_monitor_market_price=0.46,
    )
    pos.entry_method = getattr(pos, "entry_method", "ens_member_counting") or "ens_member_counting"
    assert pos.entry_method
    portfolio = PortfolioState(positions=[pos])
    artifact = cycle_runner.CycleArtifact(mode="test", started_at="2026-01-01T00:00:00Z")
    summary = {"monitors": 0, "exits": 0}

    from src.engine.lifecycle_events import build_entry_canonical_write
    from src.state.db import append_many_and_project

    entry_events, entry_projection = build_entry_canonical_write(
        pos,
        decision_id="decision-live-exit-seed",
        source_module="tests/test_runtime_guards:canonical_entry_baseline",
    )
    append_many_and_project(conn, entry_events, entry_projection)

    class Tracker:
        def __init__(self):
            self.exits = []

        def record_exit(self, position):
            self.exits.append(position.trade_id)

    class LiveClob:
        paper_mode = False

        def get_order_status(self, order_id):
            assert order_id == "sell-order-1"
            return {"status": "CONFIRMED"}

    tracker = Tracker()
    captured = {}

    monkeypatch.setattr(cycle_runner, "cities_by_name", {"NYC": NYC}, raising=False)
    monkeypatch.setattr("src.execution.exit_lifecycle.check_sell_collateral", lambda *args, **kwargs: (True, None))
    def _refresh_position(conn, clob, pos):
        pos.entry_method = getattr(pos, "entry_method", "ens_member_counting") or "ens_member_counting"
        assert pos.entry_method
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
    execution_fact_row = conn.execute(
        """
        SELECT order_role, posted_at, filled_at, submitted_price, fill_price, shares, venue_status, terminal_exec_status
        FROM execution_fact
        WHERE intent_id = 'live-exit-1:exit'
        """
    ).fetchone()

    assert p_dirty is True
    assert t_dirty is True
    assert tracker.exits == ["live-exit-1"]
    assert summary["pending_exits_filled"] == 0
    assert summary["pending_exits_retried"] == 0
    assert summary["monitors"] == 1
    assert summary["exits"] == 1
    assert artifact.exit_cases == []
    assert portfolio.positions == [pos]
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

    # Post-P9: query_position_events reads from position_events (canonical spine).
    # Current baseline positions already carry entry events. The same monitor
    # cycle may append DAY0_WINDOW_ENTERED before EXIT_ORDER_FILLED.
    assert [event["event_type"] for event in events] == [
        "POSITION_OPEN_INTENT",
        "ENTRY_ORDER_POSTED",
        "ENTRY_ORDER_FILLED",
        "DAY0_WINDOW_ENTERED",
        "EXIT_ORDER_FILLED",
    ]

    open_intent, entry_posted, entry_filled, day0_event, fill_event = events
    # Entry events come from the seeded canonical entry baseline.
    assert open_intent["runtime_trade_id"] == "live-exit-1"
    assert entry_posted["runtime_trade_id"] == "live-exit-1"
    assert entry_filled["runtime_trade_id"] == "live-exit-1"
    assert day0_event["event_type"] == "DAY0_WINDOW_ENTERED"
    assert day0_event["runtime_trade_id"] == "live-exit-1"

    # EXIT_ORDER_FILLED canonical event
    assert fill_event["event_type"] == "EXIT_ORDER_FILLED"
    assert fill_event["source"] == "src.execution.exit_lifecycle"
    assert fill_event["runtime_trade_id"] == "live-exit-1"
    assert fill_event["order_id"] == "sell-order-1"
    assert fill_event["strategy"] == "opening_inertia"
    assert fill_event["decision_snapshot_id"] == "snap-live-exit"
    assert fill_event["details"]["exit_reason"] == "forward edge failed"
    assert fill_event["details"]["fill_price"] == pytest.approx(0.46)
    assert fill_event["details"]["best_bid"] == pytest.approx(0.46)
    assert fill_event["details"]["current_market_price"] == pytest.approx(0.46)

    assert pos.state == "economically_closed"
    assert pos.exit_state == "sell_filled"
    assert pos.exit_trigger == "EDGE_REVERSAL"
    assert pos.exit_reason == "forward edge failed"
    assert pos.last_exit_order_id == "sell-order-1"
    assert pos.last_monitor_prob == pytest.approx(0.41)
    assert pos.last_monitor_market_price == pytest.approx(0.46)
    assert pos.exit_price == pytest.approx(0.46)
    assert pos.last_exit_at == fill_event["timestamp"]
    assert fill_event["details"]["fill_price"] == pytest.approx(pos.exit_price)
    assert entry_filled["timestamp"] == pos.entered_at
    assert fill_event["timestamp"] != entry_filled["timestamp"]
    assert execution_fact_row["order_role"] == "exit"
    assert execution_fact_row["posted_at"] == pos.entered_at
    assert execution_fact_row["filled_at"] == fill_event["timestamp"]
    assert execution_fact_row["submitted_price"] == pytest.approx(0.46)
    assert execution_fact_row["fill_price"] == pytest.approx(0.46)
    assert execution_fact_row["shares"] == pytest.approx(25.0)
    assert execution_fact_row["venue_status"] == "CONFIRMED"
    assert execution_fact_row["terminal_exec_status"] == "filled"

    conn.close()


def _raw_position_event_rows(conn, position_id):
    cursor = conn.execute(
        """
        SELECT event_id, sequence_no, event_type, source_module, idempotency_key, payload_json
        FROM position_events
        WHERE position_id = ?
        ORDER BY sequence_no ASC
        """,
        (position_id,),
    )
    columns = [column[0] for column in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def test_exit_dual_write_backfills_missing_entry_history_after_day0_only_canonical_event(tmp_path):
    """Legacy Day0-only canonical history must receive append-only entry backfill.

    Batch H regression: the existing DAY0_WINDOW_ENTERED row is history and must
    not be mutated or renumbered, but it also must not suppress missing legacy
    entry events before EXIT_ORDER_FILLED is appended.
    """
    conn = get_connection(tmp_path / "zeus.db")
    init_schema(conn)

    from src.engine.lifecycle_events import build_day0_window_entered_canonical_write
    from src.state.db import append_many_and_project

    position_id = "legacy-day0-only"
    day0_position = _position(
        trade_id=position_id,
        state="day0_window",
        order_id="entry-order-1",
        entered_at="2026-03-30T00:00:00Z",
        order_posted_at="2026-03-29T23:59:00Z",
        day0_entered_at="2026-04-01T00:00:00Z",
        decision_snapshot_id="snap-legacy-day0",
    )
    day0_events, day0_projection = build_day0_window_entered_canonical_write(
        day0_position,
        day0_entered_at=day0_position.day0_entered_at,
        sequence_no=1,
        previous_phase="active",
        source_module="tests/test_runtime_guards:seed_day0_only",
    )
    append_many_and_project(conn, day0_events, day0_projection)
    before_day0 = _raw_position_event_rows(conn, position_id)[0]

    closed = _position(
        trade_id=position_id,
        state="economically_closed",
        exit_state="sell_filled",
        pre_exit_state="day0_window",
        order_id="entry-order-1",
        last_exit_order_id="sell-order-1",
        entered_at="2026-03-30T00:00:00Z",
        order_posted_at="2026-03-29T23:59:00Z",
        day0_entered_at="2026-04-01T00:00:00Z",
        last_exit_at="2026-04-01T01:00:00Z",
        exit_price=0.46,
        exit_reason="forward edge failed",
        decision_snapshot_id="snap-legacy-day0",
    )

    assert exit_lifecycle_module._dual_write_canonical_economic_close_if_available(
        conn,
        closed,
        phase_before="pending_exit",
    ) is True

    events = _raw_position_event_rows(conn, position_id)
    assert events[0] == before_day0
    assert [event["sequence_no"] for event in events] == [1, 2, 3, 4, 5]
    assert [event["event_type"] for event in events] == [
        "DAY0_WINDOW_ENTERED",
        "POSITION_OPEN_INTENT",
        "ENTRY_ORDER_POSTED",
        "ENTRY_ORDER_FILLED",
        "EXIT_ORDER_FILLED",
    ]
    assert len({event["event_id"] for event in events}) == len(events)
    assert len({event["idempotency_key"] for event in events}) == len(events)

    posted_payload = json.loads(events[2]["payload_json"])
    assert posted_payload["decision_evidence_reason"] == "backfill_legacy_position"
    assert events[1]["source_module"] == "src.execution.exit_lifecycle:backfill"
    assert events[2]["source_module"] == "src.execution.exit_lifecycle:backfill"
    assert events[3]["source_module"] == "src.execution.exit_lifecycle:backfill"
    assert events[4]["source_module"] == "src.execution.exit_lifecycle"


def test_exit_dual_write_backfills_only_missing_entry_events_for_partial_history(tmp_path):
    """Partial canonical entry history must not be duplicated during backfill."""
    conn = get_connection(tmp_path / "zeus.db")
    init_schema(conn)

    from src.engine.lifecycle_events import (
        build_day0_window_entered_canonical_write,
        build_entry_canonical_write,
    )
    from src.state.db import append_many_and_project

    position_id = "legacy-partial-entry"
    pending_entry = _position(
        trade_id=position_id,
        state="pending_tracked",
        order_id="entry-order-1",
        order_posted_at="2026-03-29T23:59:00Z",
        entered_at="",
        day0_entered_at="",
        decision_snapshot_id="snap-partial-entry",
    )
    entry_events, entry_projection = build_entry_canonical_write(
        pending_entry,
        source_module="tests/test_runtime_guards:partial_entry_seed",
        decision_evidence_reason="already_seeded_partial",
    )
    append_many_and_project(conn, entry_events, entry_projection)

    day0_position = _position(
        trade_id=position_id,
        state="day0_window",
        order_id="entry-order-1",
        entered_at="2026-03-30T00:00:00Z",
        order_posted_at="2026-03-29T23:59:00Z",
        day0_entered_at="2026-04-01T00:00:00Z",
        decision_snapshot_id="snap-partial-entry",
    )
    day0_events, day0_projection = build_day0_window_entered_canonical_write(
        day0_position,
        day0_entered_at=day0_position.day0_entered_at,
        sequence_no=3,
        previous_phase="active",
        source_module="tests/test_runtime_guards:partial_entry_day0",
    )
    append_many_and_project(conn, day0_events, day0_projection)
    before_events = _raw_position_event_rows(conn, position_id)

    closed = _position(
        trade_id=position_id,
        state="economically_closed",
        exit_state="sell_filled",
        pre_exit_state="day0_window",
        order_id="entry-order-1",
        last_exit_order_id="sell-order-1",
        entered_at="2026-03-30T00:00:00Z",
        order_posted_at="2026-03-29T23:59:00Z",
        day0_entered_at="2026-04-01T00:00:00Z",
        last_exit_at="2026-04-01T01:00:00Z",
        exit_price=0.46,
        exit_reason="forward edge failed",
        decision_snapshot_id="snap-partial-entry",
    )

    assert exit_lifecycle_module._dual_write_canonical_economic_close_if_available(
        conn,
        closed,
        phase_before="pending_exit",
    ) is True

    events = _raw_position_event_rows(conn, position_id)
    assert events[:3] == before_events
    assert [event["sequence_no"] for event in events] == [1, 2, 3, 4, 5]
    assert [event["event_type"] for event in events] == [
        "POSITION_OPEN_INTENT",
        "ENTRY_ORDER_POSTED",
        "DAY0_WINDOW_ENTERED",
        "ENTRY_ORDER_FILLED",
        "EXIT_ORDER_FILLED",
    ]
    assert [event["event_type"] for event in events].count("POSITION_OPEN_INTENT") == 1
    assert [event["event_type"] for event in events].count("ENTRY_ORDER_POSTED") == 1
    assert [event["event_type"] for event in events].count("ENTRY_ORDER_FILLED") == 1
    assert len({event["event_id"] for event in events}) == len(events)
    assert len({event["idempotency_key"] for event in events}) == len(events)
    assert events[3]["source_module"] == "src.execution.exit_lifecycle:backfill"
    assert events[4]["source_module"] == "src.execution.exit_lifecycle"


def test_monitoring_skips_economically_closed_positions(monkeypatch):
    pos = _position(
        trade_id="econ-close-1",
        state="economically_closed",
        exit_state="sell_filled",
        exit_reason="forward edge failed",
        exit_price=0.46,
    )
    portfolio = PortfolioState(positions=[pos])
    artifact = cycle_runner.CycleArtifact(mode="test", started_at="2026-01-01T00:00:00Z")
    summary = {"monitors": 0, "exits": 0}

    class Tracker:
        def record_exit(self, position):
            raise AssertionError("economically closed positions should not be re-exited")

    monkeypatch.setattr(Position, "evaluate_exit", lambda self, ctx: (_ for _ in ()).throw(AssertionError("economically closed positions should not be monitored for exit")))

    p_dirty, t_dirty = cycle_runner._execute_monitoring_phase(
        None,
        type("LiveClob", (), {"paper_mode": False})(),
        portfolio,
        artifact,
        Tracker(),
        summary,
    )

    assert p_dirty is False
    assert t_dirty is False
    assert summary["monitor_skipped_economic_close"] == 1


def test_economically_closed_position_does_not_count_as_open_exposure():
    portfolio = PortfolioState(
        bankroll=100.0,
        positions=[
            _position(trade_id="closed-1", state="economically_closed", size_usd=10.0),
            _position(trade_id="open-1", state="holding", size_usd=5.0),
        ],
    )

    assert total_exposure_usd(portfolio) == pytest.approx(5.0)
    # portfolio_heat assertion removed


def test_inactive_positions_do_not_count_as_same_city_range_open():
    portfolio = PortfolioState(
        positions=[
            _position(trade_id="closed-1", state="economically_closed", city="NYC", bin_label="39-40°F"),
            _position(trade_id="admin-1", state="admin_closed", city="NYC", bin_label="39-40°F"),
        ],
    )

    assert has_same_city_range_open(portfolio, "NYC", "39-40°F") is False


def test_quarantined_positions_do_not_count_as_open_exposure():
    portfolio = PortfolioState(
        bankroll=100.0,
        positions=[
            _position(trade_id="quarantine-1", state="quarantined", chain_state="quarantined", size_usd=10.0),
            _position(trade_id="open-1", state="holding", size_usd=5.0),
        ],
    )

    assert total_exposure_usd(portfolio) == pytest.approx(5.0)
    # portfolio_heat assertion removed


def test_quarantine_expired_positions_do_not_count_as_same_city_range_open():
    portfolio = PortfolioState(
        positions=[
            _position(
                trade_id="quarantine-expired-1",
                state="quarantined",
                chain_state="quarantine_expired",
                city="NYC",
                bin_label="39-40°F",
            ),
        ],
    )

    assert has_same_city_range_open(portfolio, "NYC", "39-40°F") is False


def test_quarantine_expired_positions_do_not_count_as_open_exposure():
    portfolio = PortfolioState(
        bankroll=100.0,
        positions=[
            _position(
                trade_id="quarantine-expired-1",
                state="quarantined",
                chain_state="quarantine_expired",
                size_usd=10.0,
            ),
            _position(trade_id="open-1", state="holding", size_usd=5.0),
        ],
    )

    assert total_exposure_usd(portfolio) == pytest.approx(5.0)
    # portfolio_heat assertion removed


def test_materialize_position_carries_semantic_snapshot_jsons():
    candidate = type("Candidate", (), {
        "target_date": "2026-04-01",
        "hours_since_open": 2.0,
        "temperature_metric": "high",
    })()
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
        candidate,
        decision,
        result,
        portfolio,
        NYC,
        DiscoveryMode.OPENING_HUNT,
        state="entered",
        env="test",
        bankroll_at_entry=100.0,
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

    intent = exit_lifecycle_module.build_exit_intent(pos, ctx)

    assert intent.trade_id == pos.trade_id
    assert intent.reason == "forward edge failed"
    assert intent.token_id == pos.token_id
    assert intent.shares == pytest.approx(pos.effective_shares)
    assert intent.current_market_price == pytest.approx(0.46)
    assert intent.best_bid == pytest.approx(0.45)


def test_sell_result_without_order_id_is_rejected_not_trade_id_fallback():
    result = exit_lifecycle_module._coerce_sell_result(
        "trade-1",
        {"status": "OPEN", "price": 0.44, "shares": 25.0},
    )

    assert result.status == "rejected"
    assert result.order_id in (None, "")
    assert result.external_order_id in (None, "")
    assert result.order_id != "trade-1"
    assert result.external_order_id != "trade-1"
    assert result.reason == "missing_order_id"


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

    monkeypatch.setattr(exit_lifecycle_module, "check_sell_collateral", lambda *args, **kwargs: (True, None))
    monkeypatch.setattr("src.execution.exit_lifecycle.execute_exit_order", _execute_exit_order)

    outcome = exit_lifecycle_module.execute_exit(
        portfolio=portfolio,
        position=pos,
        exit_context=ctx,
        clob=LiveClob(),
    )

    assert outcome == "sell_pending: order=sell-order-1, status=OPEN"
    assert calls["intent"].trade_id == pos.trade_id
    assert calls["intent"].token_id == pos.token_id
    assert calls["intent"].shares == pytest.approx(pos.effective_shares)
    assert calls["intent"].current_price == pytest.approx(0.46)
    assert pos.state == "pending_exit"
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

    monkeypatch.setattr(exit_lifecycle_module, "check_sell_collateral", lambda *args, **kwargs: (True, None))
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
        clob=LiveClob(),
    )

    assert outcome == "sell_error: sell_api_down"
    assert pos in portfolio.positions
    assert pos.state == "pending_exit"
    assert pos.exit_state == "retry_pending"
    assert pos.last_exit_error == "sell_api_down"



## Paper-mode tests removed — Zeus is live-only (Phase 1 decommission).
## Original: test_execute_exit_accepts_prebuilt_exit_intent_in_paper_mode
## Original: test_execute_exit_paper_mode_dual_writes_economic_close_when_canonical_history_present


def test_monitor_refresh_has_no_production_paper_mode_branch():
    project_root = Path(__file__).resolve().parents[1]
    offenders = []
    for subdir in ("src/engine", "src/execution"):
        for path in (project_root / subdir).rglob("*.py"):
            text = path.read_text()
            for token in ("paper_mode", "paper_exit"):
                if token in text:
                    offenders.append(f"{path.relative_to(project_root)}:{token}")

    assert offenders == []


def test_discovery_phase_records_observation_unavailable_as_no_trade(monkeypatch, tmp_path):
    # A6 (PLAN.md §A6) flipped the ZEUS_MARKET_PHASE_DISPATCH default to ON.
    # This test was written for the pre-A6 legacy cycle-axis path and uses
    # a minimal market dict (no temperature_metric) that the post-A6
    # cycle_runtime would reject during candidate construction. Pin the
    # legacy axis explicitly so the test's pre-A6 contract holds —
    # exercising the LEGACY path is still a valid antibody, the kill-switch
    # mode operators flip to under the post-A6 emergency rollback.
    monkeypatch.setenv("ZEUS_MARKET_PHASE_DISPATCH", "0")
    conn = get_connection(tmp_path / "zeus.db")
    init_schema(conn)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS availability_fact (
            availability_id TEXT PRIMARY KEY,
            scope_type TEXT NOT NULL CHECK (scope_type IN ('cycle', 'candidate', 'city_target', 'order', 'chain')),
            scope_key TEXT NOT NULL,
            failure_type TEXT NOT NULL,
            started_at TEXT NOT NULL,
            ended_at TEXT,
            impact TEXT NOT NULL CHECK (impact IN ('skip', 'degrade', 'retry', 'block')),
            details_json TEXT NOT NULL
        )
        """
    )
    conn.commit()

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
        get_last_scan_authority=lambda: "VERIFIED",
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
        env="paper",
        deps=deps,
    )

    assert summary["no_trades"] == 1
    case = artifact.no_trade_cases[0]
    availability_row = conn.execute(
        """
        SELECT scope_type, scope_key, failure_type, impact
        FROM availability_fact
        ORDER BY availability_id DESC
        LIMIT 1
        """
    ).fetchone()
    assert case.availability_status == "DATA_UNAVAILABLE"
    assert case.rejection_stage == "SIGNAL_QUALITY"
    assert availability_row["scope_type"] == "city_target"
    assert availability_row["scope_key"] == "NYC:2026-04-01"
    assert availability_row["failure_type"] == "observation_missing"
    assert availability_row["impact"] == "skip"
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
    conn.commit()  # Fix B: store_artifact no longer commits internally; caller must commit.

    summary = query_learning_surface_summary(conn, env="paper")
    conn.close()

    assert summary["availability_status_counts"]["DATA_UNAVAILABLE"] == 1
    assert summary["no_trade_stage_counts"]["EDGE_INSUFFICIENT"] == 1


def test_availability_status_helper_maps_rate_limited_and_chain():
    assert cycle_runtime._availability_status_for_exception(RuntimeError("429 capacity exhausted")) == "RATE_LIMITED"
    assert cycle_runtime._availability_status_for_exception(RuntimeError("chain rpc unavailable")) == "CHAIN_UNAVAILABLE"


def test_discovery_phase_records_rate_limited_decision_as_availability_fact(tmp_path):
    conn = get_connection(tmp_path / "zeus.db")
    init_schema(conn)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS availability_fact (
            availability_id TEXT PRIMARY KEY,
            scope_type TEXT NOT NULL CHECK (scope_type IN ('cycle', 'candidate', 'city_target', 'order', 'chain')),
            scope_key TEXT NOT NULL,
            failure_type TEXT NOT NULL,
            started_at TEXT NOT NULL,
            ended_at TEXT,
            impact TEXT NOT NULL CHECK (impact IN ('skip', 'degrade', 'retry', 'block')),
            details_json TEXT NOT NULL
        )
        """
    )
    conn.commit()

    artifact = CycleArtifact(mode="opening_hunt", started_at="2026-04-03T00:00:00Z")
    tracker = StrategyTracker()
    portfolio = PortfolioState()
    summary = {"candidates": 0, "no_trades": 0}

    market = {
        "city": NYC,
        "target_date": "2026-04-01",
        "outcomes": [],
        "hours_since_open": 1.0,
        "hours_to_resolution": 4.0,
        "temperature_metric": "high",
        "event_id": "evt-rate",
        "slug": "slug-rate",
    }
    decision = types.SimpleNamespace(
        should_trade=False,
        edge=None,
        decision_id="d-rate",
        rejection_stage="SIGNAL_QUALITY",
        rejection_reasons=["429 capacity exhausted"],
        selected_method="ens_member_counting",
        applied_validations=["ens_fetch"],
        decision_snapshot_id="snap-rate",
        edge_source="",
        strategy_key="",
        availability_status="RATE_LIMITED",
        settlement_semantics_json="",
        epistemic_context_json="",
        edge_context_json="",
        p_raw=[],
        p_cal=[],
        p_market=[],
        alpha=0.0,
        agreement="",
    )

    deps = types.SimpleNamespace(
        MODE_PARAMS={DiscoveryMode.OPENING_HUNT: {}},
        DiscoveryMode=DiscoveryMode,
        logger=types.SimpleNamespace(
            warning=lambda *args, **kwargs: None,
            error=lambda *args, **kwargs: None,
        ),
        NoTradeCase=NoTradeCase,
        MarketCandidate=MarketCandidate,
        find_weather_markets=lambda **kwargs: [market],
        get_last_scan_authority=lambda: "VERIFIED",
        evaluate_candidate=lambda *args, **kwargs: [decision],
        _classify_edge_source=lambda *args, **kwargs: "",
    )

    cycle_runtime.execute_discovery_phase(
        conn,
        clob=None,
        portfolio=portfolio,
        artifact=artifact,
        tracker=tracker,
        limits=types.SimpleNamespace(),
        mode=DiscoveryMode.OPENING_HUNT,
        summary=summary,
        entry_bankroll=100.0,
        decision_time=datetime(2026, 4, 3, 6, 0, tzinfo=timezone.utc),
        env="test",
        deps=deps,
    )

    row = conn.execute(
        """
        SELECT scope_type, scope_key, failure_type, impact, details_json
        FROM availability_fact
        WHERE scope_key = 'd-rate'
        """
    ).fetchone()
    conn.close()

    assert summary["no_trades"] == 1
    assert row["scope_type"] == "candidate"
    assert row["scope_key"] == "d-rate"
    assert row["failure_type"] == "rate_limited"
    assert row["impact"] == "skip"
    assert "RATE_LIMITED" in row["details_json"]


def test_evaluator_ens_fetch_exception_becomes_explicit_availability_truth(monkeypatch):
    monkeypatch.setattr(evaluator_module, "get_mode", lambda: "test")
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
        decision_time=datetime(2026, 4, 3, 0, 0, tzinfo=timezone.utc),
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
    )

    with pytest.raises(ValueError, match="trade_id mismatch"):
        exit_lifecycle_module.execute_exit(
            portfolio=portfolio,
            position=pos,
            exit_context=ctx,
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
    assert pos.exit_state == ""
    assert pos.state == "entered"


def test_check_pending_exits_restores_entered_state_after_bare_exit_intent_release():
    pos = _position(state="entered")
    pos.exit_state = "exit_intent"
    pos.last_exit_error = ""
    portfolio = PortfolioState(positions=[pos])

    stats = exit_lifecycle_module.check_pending_exits(portfolio, clob=None, conn=None)

    assert stats["retried"] == 0
    assert stats["unchanged"] == 1
    assert pos.exit_state == ""
    assert pos.state == "entered"


def test_lifecycle_kernel_enters_pending_exit_from_active_and_day0_states():
    from src.state.lifecycle_manager import enter_pending_exit_runtime_state

    assert enter_pending_exit_runtime_state("entered") == "pending_exit"
    assert enter_pending_exit_runtime_state("holding") == "pending_exit"
    assert enter_pending_exit_runtime_state("day0_window") == "pending_exit"


def test_lifecycle_kernel_releases_pending_exit_to_preserved_or_active_runtime_state():
    from src.state.lifecycle_manager import release_pending_exit_runtime_state

    assert release_pending_exit_runtime_state("entered") == "entered"
    assert release_pending_exit_runtime_state("", day0_entered_at="2026-04-04T00:00:00Z") == "day0_window"
    assert release_pending_exit_runtime_state("", day0_entered_at="") == "holding"


def test_lifecycle_kernel_allows_touched_portfolio_terminal_transitions():
    from src.state.lifecycle_manager import (
        enter_admin_closed_runtime_state,
        enter_economically_closed_runtime_state,
        enter_settled_runtime_state,
        enter_voided_runtime_state,
    )

    assert enter_economically_closed_runtime_state("pending_exit", exit_state="sell_pending") == "economically_closed"
    assert enter_settled_runtime_state("economically_closed") == "settled"
    assert enter_settled_runtime_state(
        "pending_exit",
        exit_state="backoff_exhausted",
        chain_state="exit_pending_missing",
    ) == "settled"
    assert enter_admin_closed_runtime_state(
        "pending_exit",
        exit_state="retry_pending",
        chain_state="exit_pending_missing",
    ) == "admin_closed"
    assert enter_voided_runtime_state("pending_tracked") == "voided"


def test_lifecycle_kernel_rejects_portfolio_terminal_transition_from_wrong_phase():
    from src.state.lifecycle_manager import enter_admin_closed_runtime_state, enter_settled_runtime_state

    with pytest.raises(ValueError, match="admin close requires pending_exit runtime phase"):
        enter_admin_closed_runtime_state("entered")
    # Bug #53b: pending_exit → settled is now allowed without backoff_exhausted
    result = enter_settled_runtime_state(
        "pending_exit",
        exit_state="sell_pending",
        chain_state="exit_pending_missing",
    )
    assert result == "settled"


def test_compute_economic_close_routes_pending_exit_through_kernel():
    from src.state.portfolio import PortfolioState, compute_economic_close

    pos = _position(state="pending_exit", exit_state="sell_pending")
    state = PortfolioState(positions=[pos])

    closed = compute_economic_close(
        state,
        pos.trade_id,
        exit_price=0.46,
        exit_reason="forward edge failed",
    )

    assert closed is pos
    assert pos.state == "economically_closed"


def test_compute_settlement_close_routes_economically_closed_through_kernel():
    from src.state.portfolio import PortfolioState, compute_settlement_close

    pos = _position(state="economically_closed")
    state = PortfolioState(positions=[pos])

    closed = compute_settlement_close(
        state,
        pos.trade_id,
        settlement_price=1.0,
        exit_reason="SETTLEMENT",
    )

    assert closed is pos
    assert pos.state == "settled"


def test_settlement_economics_rejects_submitted_only_position_authority():
    from src.execution.harvester import _settlement_economics_for_position

    submitted_only = _position(
        entry_price=0.55,
        shares=20.0,
        size_usd=11.0,
        cost_basis_usd=11.0,
        target_notional_usd=11.0,
        submitted_notional_usd=11.0,
        entry_price_submitted=0.55,
        shares_submitted=20.0,
        entry_economics_authority=ENTRY_ECONOMICS_SUBMITTED_LIMIT,
        fill_authority=FILL_AUTHORITY_NONE,
    )

    with pytest.raises(ValueError, match="fill-derived economics"):
        _settlement_economics_for_position(submitted_only)

    fill_authoritative = _position(
        entry_price=0.53,
        shares=20.0,
        size_usd=10.6,
        cost_basis_usd=10.6,
        entry_price_avg_fill=0.53,
        shares_filled=20.0,
        filled_cost_basis_usd=10.6,
        entry_economics_authority=ENTRY_ECONOMICS_AVG_FILL_PRICE,
        fill_authority=FILL_AUTHORITY_VENUE_CONFIRMED_FULL,
    )

    assert _settlement_economics_for_position(fill_authoritative) == pytest.approx((20.0, 10.6))


def test_lifecycle_kernel_maps_entry_runtime_states_for_order_status():
    from src.state.lifecycle_manager import initial_entry_runtime_state_for_order_status

    assert initial_entry_runtime_state_for_order_status("filled") == "entered"
    assert initial_entry_runtime_state_for_order_status("pending") == "pending_tracked"
    assert initial_entry_runtime_state_for_order_status("rejected") == "voided"


def test_lifecycle_kernel_allows_touched_entry_runtime_transitions():
    from src.state.lifecycle_manager import (
        enter_filled_entry_runtime_state,
        enter_voided_entry_runtime_state,
    )

    assert enter_filled_entry_runtime_state("pending_tracked") == "entered"
    assert enter_voided_entry_runtime_state("pending_tracked") == "voided"


def test_lifecycle_kernel_rejects_entry_fill_from_non_pending_phase():
    from src.state.lifecycle_manager import enter_filled_entry_runtime_state

    with pytest.raises(ValueError, match="entry fill requires pending_entry runtime phase"):
        enter_filled_entry_runtime_state("entered")


def test_check_pending_entries_ignores_non_pending_states():
    from src.execution.fill_tracker import check_pending_entries
    from src.state.portfolio import PortfolioState

    pos = _position(state="entered", order_id="ord-1", entry_order_id="ord-1")
    stats = check_pending_entries(PortfolioState(positions=[pos]), clob=None)

    assert stats == {
        "entered": 0,
        "voided": 0,
        "still_pending": 0,
        "dirty": False,
        "tracker_dirty": False,
    }
    assert pos.state == "entered"


def test_check_pending_exits_restores_day0_window_state_after_bare_exit_intent_release():
    pos = _position(state="day0_window")
    pos.day0_entered_at = "2026-04-04T00:00:00Z"
    pos.exit_state = "exit_intent"
    pos.last_exit_error = ""
    portfolio = PortfolioState(positions=[pos])

    stats = exit_lifecycle_module.check_pending_exits(portfolio, clob=None, conn=None)

    assert stats["retried"] == 0
    assert stats["unchanged"] == 1
    assert pos.exit_state == ""
    assert pos.state == "day0_window"


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
    # Post-P9: EXIT_ORDER_VOIDED goes to execution_fact (not position_events)
    exec_row = conn.execute(
        "SELECT voided_at FROM execution_fact WHERE position_id = ? AND order_role = 'exit'",
        ("t1",),
    ).fetchone()
    conn.close()

    assert stats["retried"] == 1
    assert exec_row is not None
    assert exec_row["voided_at"] is not None
