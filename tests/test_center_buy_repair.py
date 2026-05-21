# Created: 2026-04-29
# Last reused/audited: 2026-05-21
# Lifecycle: created=2026-04-29; last_reviewed=2026-05-21; last_reused=2026-05-21
# Purpose: Guard evaluator center-buy repair, forecast evidence causality, and snapshot persistence boundaries.
# Reuse: Run when changing evaluator forecast evidence validation or ENS snapshot/p_raw persistence routing.
# Authority basis: phase 1K live decision snapshot causality gate
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

import numpy as np
import pytest

import src.engine.evaluator as evaluator_module
from src.config import City
from src.engine.discovery_mode import DiscoveryMode
from src.engine.evaluator import MarketCandidate
from src.strategy.market_analysis_family_scan import FullFamilyHypothesis
from src.state.portfolio import PortfolioState
from src.types import BinEdge


NYC = City(
    name="NYC",
    lat=40.7772,
    lon=-73.8726,
    timezone="America/New_York",
    cluster="NYC",
    settlement_unit="F",
    wu_station="KLGA",
)

TEST_FETCH_TIME = datetime(2026, 4, 2, 6, 0, tzinfo=timezone.utc)
TEST_DECISION_TIME = datetime(2026, 4, 2, 12, 0, tzinfo=timezone.utc)


def _stub_full_family_scan(monkeypatch):
    def _scan(analysis, *args, **kwargs):
        selected_method = getattr(analysis, "selected_method", "test_fixture")
        edges = list(analysis.find_edges(n_bootstrap=kwargs.get("n_bootstrap", 0)))
        for edge in edges:
            edge.selected_method = getattr(edge, "selected_method", selected_method)
            assert edge.selected_method
        return [
            FullFamilyHypothesis(
                index=int(edge.support_index) if edge.support_index is not None else i,
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
            for i, edge in enumerate(edges)
        ]

    monkeypatch.setattr(evaluator_module, "scan_full_hypothesis_family", _scan)


def _patch_evaluator(
    monkeypatch,
    *,
    entry_price: float,
    p_posterior: float = 0.06,
    calibration_level: int = 1,
    snapshot_id: str = "snap-1",
    ens_overrides: dict | None = None,
    store_calls: list[str] | None = None,
    store_conn_calls: list[object] | None = None,
    metadata_conn_calls: list[object] | None = None,
    p_raw_store_conn_calls: list[object] | None = None,
    p_raw_store_result=None,
    real_snapshot_helpers: bool = False,
):
    class DummyEnsembleSignal:
        def __init__(self, members_hourly, times, city, target_d, settlement_semantics=None, decision_time=None, **kwargs):
            self.member_maxes = np.full(51, 40.0)
            self.member_extrema = self.member_maxes
            self.bias_corrected = False

        def p_raw_vector(self, bins, n_mc=5000):
            return np.array([0.60, 0.25, 0.15])

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
            edge = BinEdge(
                bin=self.bins[1],
                direction="buy_yes",
                edge=0.05,
                ci_lower=0.03,
                ci_upper=0.07,
                p_model=p_posterior,
                p_market=entry_price,
                p_posterior=p_posterior,
                entry_price=entry_price,
                p_value=0.02,
                vwmp=entry_price,
                support_index=1,
            )
            edge.forward_edge = edge.p_posterior - edge.p_market
            return [edge]

        def sigma_context(self):
            return {"base_sigma": 0.5, "lead_multiplier": 1.0, "spread_multiplier": 1.0, "final_sigma": 0.5}

        def mean_context(self):
            return {"offset": 0.0, "lead_days": 0.0}

    class DummyClob:
        def get_best_bid_ask(self, token_id):
            return (entry_price, entry_price + 0.01, 20.0, 20.0)

        def get_fee_rate(self, token_id):
            return 0.05

    class DummyAlpha:
        def value_for_consumer(self, target):
            return 0.5

    def _fetch(city, forecast_days=8, model=None, **kwargs):
        now = TEST_FETCH_TIME
        result = {
            "members_hourly": np.ones((51, 48)) * 40.0,
            "times": [datetime(2026, 4, 3, hour % 24, 0, tzinfo=timezone.utc).isoformat() for hour in range(48)],
            "issue_time": now,
            "first_valid_time": datetime(2026, 4, 3, 0, 0, tzinfo=timezone.utc),
            "fetch_time": now,
            "available_at": now,
            "model": model or "ecmwf_ifs025",
            "source_id": "tigge",
            "raw_payload_hash": "a" * 64,
            "authority_tier": "FORECAST",
            "degradation_level": "OK",
            "forecast_source_role": kwargs.get("role", "entry_primary"),
            "n_members": 51,
        }
        result.update(ens_overrides or {})
        return result

    monkeypatch.setattr(evaluator_module, "fetch_ensemble", _fetch)
    monkeypatch.setattr(evaluator_module, "_live_entry_forecast_config_or_blocker", lambda: (None, None))
    monkeypatch.setattr(evaluator_module, "validate_ensemble", lambda result, expected_members=51: result is not None)
    if real_snapshot_helpers:
        class SnapshotReadyEnsembleSignal(DummyEnsembleSignal):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self.temperature_metric = evaluator_module.MetricIdentity.for_high_localday_max("ecmwf_opendata")

        monkeypatch.setattr(evaluator_module, "EnsembleSignal", SnapshotReadyEnsembleSignal)
    else:
        monkeypatch.setattr(evaluator_module, "EnsembleSignal", DummyEnsembleSignal)

    if real_snapshot_helpers:
        if store_calls is not None:
            original_store = evaluator_module._store_ens_snapshot

            def _store_real(*args, **kwargs):
                store_calls.append("called")
                return original_store(*args, **kwargs)

            monkeypatch.setattr(evaluator_module, "_store_ens_snapshot", _store_real)
    else:
        def _store(*args, **kwargs):
            if store_calls is not None:
                store_calls.append("called")
            if store_conn_calls is not None:
                store_conn_calls.append(args[0])
            return snapshot_id

        monkeypatch.setattr(evaluator_module, "_store_ens_snapshot", _store)

    if metadata_conn_calls is not None and not real_snapshot_helpers:
        def _read_metadata(*args, **kwargs):
            metadata_conn_calls.append(args[0])
            return {}

        monkeypatch.setattr(evaluator_module, "_read_v2_snapshot_metadata", _read_metadata)

    if not real_snapshot_helpers:
        def _store_p_raw(*args, **kwargs):
            if p_raw_store_conn_calls is not None:
                p_raw_store_conn_calls.append(args[0])
            return p_raw_store_result

        monkeypatch.setattr(evaluator_module, "_store_snapshot_p_raw", _store_p_raw)
    calibrator = object() if calibration_level < 4 else None
    monkeypatch.setattr(
        evaluator_module,
        "get_calibrator",
        lambda *args, **kwargs: (calibrator, calibration_level),
    )
    monkeypatch.setattr(
        evaluator_module,
        "calibrate_and_normalize",
        lambda p_raw, *args, **kwargs: np.array(p_raw, dtype=float).copy(),
    )
    monkeypatch.setattr(evaluator_module, "compute_alpha", lambda *args, **kwargs: DummyAlpha())
    monkeypatch.setattr(evaluator_module, "MarketAnalysis", DummyAnalysis)
    _stub_full_family_scan(monkeypatch)
    monkeypatch.setattr(evaluator_module, "fdr_filter", lambda edges, fdr_alpha=0.10: list(edges))
    monkeypatch.setattr(evaluator_module, "dynamic_kelly_mult", lambda **kwargs: 0.25)
    monkeypatch.setattr(evaluator_module, "kelly_size", lambda *args, **kwargs: 5.0)
    monkeypatch.setattr(evaluator_module, "check_position_allowed", lambda **kwargs: (True, ""))
    return DummyClob()


def _no_op_oracle_patch_compat_shim(monkeypatch, tmp_path):
    """Deprecated compatibility shim.

    Oracle fail-closed gate was removed 2026-05-02; file presence/freshness is
    no longer a trade prerequisite. Existing tests can call this while older
    patch sites are retired.
    """
    return None


def _candidate(*, discovery_mode: str = DiscoveryMode.UPDATE_REACTION.value) -> MarketCandidate:
    return MarketCandidate(
        city=NYC,
        target_date="2026-04-03",
        outcomes=[
            {"title": "38°F or below", "range_low": None, "range_high": 38, "token_id": "yes0", "no_token_id": "no0", "market_id": "m0", "price": 0.01},
            {"title": "39-40°F", "range_low": 39, "range_high": 40, "token_id": "yes1", "no_token_id": "no1", "market_id": "m1", "price": 0.01},
            {"title": "41°F or higher", "range_low": 41, "range_high": None, "token_id": "yes2", "no_token_id": "no2", "market_id": "m2", "price": 0.03},
        ],
        hours_since_open=10.0,
        hours_to_resolution=30.0,
        discovery_mode=discovery_mode,
    )


def test_center_buy_rejects_ultra_low_price_buy_yes_cohort(monkeypatch):
    clob = _patch_evaluator(monkeypatch, entry_price=0.01)

    decisions = evaluator_module.evaluate_candidate(
        _candidate(),
        conn=None,
        portfolio=PortfolioState(bankroll=211.37),
        clob=clob,
        limits=evaluator_module.RiskLimits(min_order_usd=1.0),
        decision_time=TEST_DECISION_TIME,
    )

    assert len(decisions) == 1
    assert decisions[0].should_trade is False
    assert decisions[0].strategy_key == "center_buy"
    assert decisions[0].rejection_stage == "MARKET_FILTER"
    assert decisions[0].rejection_reasons == ["center_buy_ultra_low_price"]
    assert decisions[0].rejection_reason_detail == "CENTER_BUY_ULTRA_LOW_PRICE(0.0100<=0.02)"
    assert "center_buy_ultra_low_price_guard" in decisions[0].applied_validations


def test_opening_inertia_low_price_entry_is_not_blocked_by_center_buy_guard(monkeypatch, tmp_path):
    _no_op_oracle_patch_compat_shim(monkeypatch, tmp_path)
    clob = _patch_evaluator(monkeypatch, entry_price=0.06, p_posterior=0.12)

    decisions = evaluator_module.evaluate_candidate(
        _candidate(discovery_mode=DiscoveryMode.OPENING_HUNT.value),
        conn=None,
        portfolio=PortfolioState(bankroll=211.37),
        clob=clob,
        limits=evaluator_module.RiskLimits(min_order_usd=1.0),
        decision_time=TEST_DECISION_TIME,
    )

    assert len(decisions) == 1
    assert decisions[0].should_trade is True
    assert decisions[0].strategy_key == "opening_inertia"
    forecast_context = json.loads(decisions[0].epistemic_context_json)["forecast_context"]
    assert forecast_context["forecast_source_id"] == "tigge"
    assert forecast_context["model_family"] == "ecmwf"
    assert forecast_context["raw_payload_hash"] == "a" * 64
    assert forecast_context["degradation_level"] == "OK"
    assert forecast_context["forecast_source_role"] == "entry_primary"
    assert forecast_context["authority_tier"] == "FORECAST"
    assert forecast_context["forecast_issue_time"]
    assert forecast_context["forecast_valid_time"]
    assert forecast_context["forecast_fetch_time"]
    assert forecast_context["forecast_available_at"]
    assert forecast_context["decision_time"] == TEST_DECISION_TIME.isoformat()
    assert forecast_context["decision_time_status"] == "OK"


def test_missing_forecast_evidence_blocks_before_snapshot_persistence(monkeypatch):
    store_calls: list[str] = []
    clob = _patch_evaluator(
        monkeypatch,
        entry_price=0.06,
        p_posterior=0.12,
        ens_overrides={"raw_payload_hash": ""},
        store_calls=store_calls,
    )

    decisions = evaluator_module.evaluate_candidate(
        _candidate(discovery_mode=DiscoveryMode.OPENING_HUNT.value),
        conn=None,
        portfolio=PortfolioState(bankroll=211.37),
        clob=clob,
        limits=evaluator_module.RiskLimits(min_order_usd=1.0),
        decision_time=TEST_DECISION_TIME,
    )

    assert len(decisions) == 1
    assert decisions[0].should_trade is False
    assert decisions[0].edge is None
    assert decisions[0].strategy_key == ""
    assert decisions[0].rejection_stage == "SIGNAL_QUALITY"
    assert decisions[0].availability_status == "DATA_UNAVAILABLE"
    assert decisions[0].rejection_reasons == ["forecast_evidence_incomplete"]
    assert "forecast_evidence_missing_raw_payload_hash" in decisions[0].rejection_reason_detail
    assert "forecast_source_evidence" in decisions[0].applied_validations
    assert store_calls == []


def test_missing_available_at_blocks_before_snapshot_persistence(monkeypatch):
    store_calls: list[str] = []
    clob = _patch_evaluator(
        monkeypatch,
        entry_price=0.06,
        p_posterior=0.12,
        ens_overrides={"available_at": None},
        store_calls=store_calls,
    )

    decisions = evaluator_module.evaluate_candidate(
        _candidate(discovery_mode=DiscoveryMode.OPENING_HUNT.value),
        conn=None,
        portfolio=PortfolioState(bankroll=211.37),
        clob=clob,
        limits=evaluator_module.RiskLimits(min_order_usd=1.0),
        decision_time=TEST_DECISION_TIME,
    )

    assert len(decisions) == 1
    assert decisions[0].should_trade is False
    assert decisions[0].rejection_stage == "SIGNAL_QUALITY"
    assert decisions[0].rejection_reasons == ["forecast_evidence_incomplete"]
    assert "forecast_evidence_missing_available_at" in decisions[0].rejection_reason_detail
    assert store_calls == []


def test_future_forecast_evidence_blocks_before_snapshot_persistence(monkeypatch):
    store_calls: list[str] = []
    clob = _patch_evaluator(
        monkeypatch,
        entry_price=0.06,
        p_posterior=0.12,
        ens_overrides={
            "available_at": datetime(2026, 4, 2, 13, 0, tzinfo=timezone.utc),
            "fetch_time": datetime(2026, 4, 2, 13, 0, tzinfo=timezone.utc),
        },
        store_calls=store_calls,
    )

    decisions = evaluator_module.evaluate_candidate(
        _candidate(discovery_mode=DiscoveryMode.OPENING_HUNT.value),
        conn=None,
        portfolio=PortfolioState(bankroll=211.37),
        clob=clob,
        limits=evaluator_module.RiskLimits(min_order_usd=1.0),
        decision_time=TEST_DECISION_TIME,
    )

    assert len(decisions) == 1
    assert decisions[0].should_trade is False
    assert decisions[0].rejection_stage == "SIGNAL_QUALITY"
    assert decisions[0].rejection_reasons == ["forecast_evidence_incomplete"]
    assert "forecast_evidence_fetch_after_decision" not in decisions[0].rejection_reason_detail
    assert "forecast_evidence_available_after_decision" in decisions[0].rejection_reason_detail
    assert "forecast_source_evidence" in decisions[0].applied_validations
    assert store_calls == []


def test_available_forecast_before_decision_permits_late_local_fetch_time(monkeypatch):
    store_calls: list[str] = []
    clob = _patch_evaluator(
        monkeypatch,
        entry_price=0.06,
        p_posterior=0.12,
        ens_overrides={
            "issue_time": datetime(2026, 4, 2, 6, 0, tzinfo=timezone.utc),
            "available_at": datetime(2026, 4, 2, 6, 5, tzinfo=timezone.utc),
            "fetch_time": datetime(2026, 4, 2, 12, 5, tzinfo=timezone.utc),
        },
        store_calls=store_calls,
    )

    decisions = evaluator_module.evaluate_candidate(
        _candidate(discovery_mode=DiscoveryMode.OPENING_HUNT.value),
        conn=None,
        portfolio=PortfolioState(bankroll=211.37),
        clob=clob,
        limits=evaluator_module.RiskLimits(min_order_usd=1.0),
        decision_time=TEST_DECISION_TIME,
    )

    assert len(decisions) == 1
    assert decisions[0].should_trade is True
    assert store_calls == ["called"]
    forecast_context = json.loads(decisions[0].epistemic_context_json)["forecast_context"]
    assert forecast_context["forecast_available_at"] == "2026-04-02T06:05:00+00:00"
    assert forecast_context["forecast_fetch_time"] == "2026-04-02T12:05:00+00:00"


def test_available_at_before_issue_blocks_before_snapshot_persistence(monkeypatch):
    store_calls: list[str] = []
    clob = _patch_evaluator(
        monkeypatch,
        entry_price=0.06,
        p_posterior=0.12,
        ens_overrides={
            "issue_time": datetime(2026, 4, 2, 6, 0, tzinfo=timezone.utc),
            "available_at": datetime(2026, 4, 2, 5, 59, tzinfo=timezone.utc),
            "fetch_time": datetime(2026, 4, 2, 6, 1, tzinfo=timezone.utc),
        },
        store_calls=store_calls,
    )

    decisions = evaluator_module.evaluate_candidate(
        _candidate(discovery_mode=DiscoveryMode.OPENING_HUNT.value),
        conn=None,
        portfolio=PortfolioState(bankroll=211.37),
        clob=clob,
        limits=evaluator_module.RiskLimits(min_order_usd=1.0),
        decision_time=TEST_DECISION_TIME,
    )

    assert len(decisions) == 1
    assert decisions[0].should_trade is False
    assert decisions[0].rejection_stage == "SIGNAL_QUALITY"
    assert decisions[0].rejection_reasons == ["forecast_evidence_incomplete"]
    assert "forecast_evidence_issue_after_available_at" in decisions[0].rejection_reason_detail
    assert store_calls == []


def test_forecast_snapshot_persistence_uses_forecasts_owned_boundary(monkeypatch):
    store_conn_calls: list[object] = []
    metadata_conn_calls: list[object] = []
    p_raw_store_conn_calls: list[object] = []
    trade_rooted_cycle_conn = sqlite3.connect(":memory:")
    trade_rooted_cycle_conn.row_factory = sqlite3.Row
    monkeypatch.setattr(evaluator_module, "_layer7_dedup_fires", lambda *args, **kwargs: False)
    monkeypatch.setattr(
        evaluator_module,
        "evaluate_ddd_for_decision",
        lambda **kwargs: evaluator_module.SimpleNamespace(
            action="PASS",
            diagnostic={"final_discount_pre_mismatch": 0.0},
        ),
    )
    clob = _patch_evaluator(
        monkeypatch,
        entry_price=0.06,
        p_posterior=0.12,
        store_conn_calls=store_conn_calls,
        metadata_conn_calls=metadata_conn_calls,
        p_raw_store_conn_calls=p_raw_store_conn_calls,
    )

    decisions = evaluator_module.evaluate_candidate(
        _candidate(discovery_mode=DiscoveryMode.OPENING_HUNT.value),
        conn=trade_rooted_cycle_conn,
        portfolio=PortfolioState(bankroll=211.37),
        clob=clob,
        limits=evaluator_module.RiskLimits(min_order_usd=1.0),
        decision_time=TEST_DECISION_TIME,
        use_forecasts_live_snapshot_store=True,
    )

    assert len(decisions) == 1
    assert decisions[0].should_trade is True
    assert store_conn_calls == [None]
    assert metadata_conn_calls == [None]
    assert p_raw_store_conn_calls == [None]
    trade_rooted_cycle_conn.close()


def test_forecast_snapshot_persistence_respects_caller_owned_connection_by_default(monkeypatch):
    store_conn_calls: list[object] = []
    metadata_conn_calls: list[object] = []
    p_raw_store_conn_calls: list[object] = []
    audit_conn = sqlite3.connect(":memory:")
    audit_conn.row_factory = sqlite3.Row
    monkeypatch.setattr(evaluator_module, "_layer7_dedup_fires", lambda *args, **kwargs: False)
    monkeypatch.setattr(
        evaluator_module,
        "evaluate_ddd_for_decision",
        lambda **kwargs: evaluator_module.SimpleNamespace(
            action="PASS",
            diagnostic={"final_discount_pre_mismatch": 0.0},
        ),
    )
    clob = _patch_evaluator(
        monkeypatch,
        entry_price=0.06,
        p_posterior=0.12,
        store_conn_calls=store_conn_calls,
        metadata_conn_calls=metadata_conn_calls,
        p_raw_store_conn_calls=p_raw_store_conn_calls,
    )

    decisions = evaluator_module.evaluate_candidate(
        _candidate(discovery_mode=DiscoveryMode.OPENING_HUNT.value),
        conn=audit_conn,
        portfolio=PortfolioState(bankroll=211.37),
        clob=clob,
        limits=evaluator_module.RiskLimits(min_order_usd=1.0),
        decision_time=TEST_DECISION_TIME,
    )

    assert len(decisions) == 1
    assert decisions[0].should_trade is True
    assert store_conn_calls == [audit_conn]
    assert metadata_conn_calls == [audit_conn]
    assert p_raw_store_conn_calls == [audit_conn]
    audit_conn.close()


def test_forecast_snapshot_real_helpers_round_trip_forecasts_db(monkeypatch, tmp_path):
    import src.state.db as db_module

    forecasts_db = tmp_path / "zeus-forecasts.db"
    monkeypatch.setattr(db_module, "ZEUS_FORECASTS_DB_PATH", forecasts_db)
    monkeypatch.setenv("ZEUS_DB_MMAP_BYTES", "0")
    forecast_conn = db_module.get_forecasts_connection(write_class=None)
    db_module.init_schema_forecasts(forecast_conn)
    forecast_conn.close()

    trade_rooted_cycle_conn = sqlite3.connect(":memory:")
    trade_rooted_cycle_conn.row_factory = sqlite3.Row
    db_module.init_schema(trade_rooted_cycle_conn)
    monkeypatch.setattr(evaluator_module, "_layer7_dedup_fires", lambda *args, **kwargs: False)
    monkeypatch.setattr(
        evaluator_module,
        "evaluate_ddd_for_decision",
        lambda **kwargs: evaluator_module.SimpleNamespace(
            action="PASS",
            diagnostic={"final_discount_pre_mismatch": 0.0},
        ),
    )
    clob = _patch_evaluator(
        monkeypatch,
        entry_price=0.06,
        p_posterior=0.12,
        real_snapshot_helpers=True,
    )

    decisions = evaluator_module.evaluate_candidate(
        _candidate(discovery_mode=DiscoveryMode.OPENING_HUNT.value),
        conn=trade_rooted_cycle_conn,
        portfolio=PortfolioState(bankroll=211.37),
        clob=clob,
        limits=evaluator_module.RiskLimits(min_order_usd=1.0),
        decision_time=TEST_DECISION_TIME,
        use_forecasts_live_snapshot_store=True,
    )

    assert len(decisions) == 1
    assert decisions[0].should_trade is True
    snapshot_id = decisions[0].decision_snapshot_id
    assert snapshot_id
    if trade_rooted_cycle_conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='ensemble_snapshots_v2'"
    ).fetchone():
        assert trade_rooted_cycle_conn.execute(
            "SELECT COUNT(*) FROM ensemble_snapshots_v2 WHERE snapshot_id = ?",
            (snapshot_id,),
        ).fetchone()[0] == 0
    forecast_conn = db_module.get_forecasts_connection(write_class=None)
    try:
        row = forecast_conn.execute(
            """
            SELECT snapshot_id, city, target_date, temperature_metric, p_raw_json, bin_grid_id
              FROM ensemble_snapshots_v2
             WHERE snapshot_id = ?
            """,
            (snapshot_id,),
        ).fetchone()
        assert row is not None
        assert row["city"] == "NYC"
        assert row["target_date"] == "2026-04-03"
        assert row["temperature_metric"] == "high"
        assert json.loads(row["p_raw_json"]) == [0.6, 0.25, 0.15]
        metadata = evaluator_module._read_v2_snapshot_metadata(
            None,
            "NYC",
            "2026-04-03",
            "high",
            snapshot_id=snapshot_id,
        )
        assert str(metadata["snapshot_id"]) == snapshot_id
        assert metadata["bin_grid_id"] == row["bin_grid_id"]
    finally:
        forecast_conn.close()
        trade_rooted_cycle_conn.close()


class _OperationalErrorConn:
    def __init__(self, message: str, *, fail_on_table_discovery: bool = False):
        self.message = message
        self.fail_on_table_discovery = fail_on_table_discovery
        self.rolled_back = False

    def execute(self, *args, **kwargs):
        sql = str(args[0])
        if "sqlite_master" in sql and not self.fail_on_table_discovery:
            return _FetchOne(row=object())
        raise sqlite3.OperationalError(self.message)

    def rollback(self):
        self.rolled_back = True


class _DatabaseErrorConn(_OperationalErrorConn):
    def execute(self, *args, **kwargs):
        raise sqlite3.DatabaseError(self.message)


class _FetchOne:
    def __init__(self, row):
        self.row = row

    def fetchone(self):
        return self.row


def test_snapshot_p_raw_lock_defers_but_corruption_fails_closed():
    p_raw = np.array([0.60, 0.25, 0.15])

    locked_conn = _OperationalErrorConn("database is locked")
    corrupt_conn = _OperationalErrorConn("database disk image is malformed")
    discovery_locked_conn = _OperationalErrorConn("database is locked", fail_on_table_discovery=True)

    assert evaluator_module._store_snapshot_p_raw(locked_conn, "snap-1", p_raw) is None
    assert locked_conn.rolled_back is True
    assert evaluator_module._store_snapshot_p_raw(discovery_locked_conn, "snap-1", p_raw) is None
    assert discovery_locked_conn.rolled_back is True
    with pytest.raises(sqlite3.OperationalError, match="database disk image is malformed"):
        evaluator_module._store_snapshot_p_raw(corrupt_conn, "snap-1", p_raw)
    assert corrupt_conn.rolled_back is True


def test_snapshot_storage_database_error_is_not_reported_as_missing_snapshot():
    p_raw = np.array([0.60, 0.25, 0.15])
    corrupt_snapshot_conn = _DatabaseErrorConn("database disk image is malformed")
    corrupt_p_raw_conn = _DatabaseErrorConn("database disk image is malformed")

    with pytest.raises(sqlite3.DatabaseError, match="database disk image is malformed"):
        evaluator_module._store_ens_snapshot(
            corrupt_snapshot_conn,
            NYC,
            "2026-04-03",
            object(),
            {},
        )
    assert corrupt_snapshot_conn.rolled_back is True

    with pytest.raises(sqlite3.DatabaseError, match="database disk image is malformed"):
        evaluator_module._store_snapshot_p_raw(corrupt_p_raw_conn, "snap-1", p_raw)
    assert corrupt_p_raw_conn.rolled_back is True


def test_level4_raw_probability_entry_blocks_before_edge_selection(monkeypatch):
    clob = _patch_evaluator(monkeypatch, entry_price=0.01, calibration_level=4)

    decisions = evaluator_module.evaluate_candidate(
        _candidate(discovery_mode=DiscoveryMode.OPENING_HUNT.value),
        conn=None,
        portfolio=PortfolioState(bankroll=211.37),
        clob=clob,
        limits=evaluator_module.RiskLimits(min_order_usd=1.0),
        decision_time=TEST_DECISION_TIME,
    )

    assert len(decisions) == 1
    assert decisions[0].should_trade is False
    assert decisions[0].edge is None
    assert decisions[0].strategy_key == ""
    assert decisions[0].rejection_stage == "CALIBRATION_IMMATURE"
    assert "calibration_maturity_level_4" in decisions[0].applied_validations
    assert "calibration_maturity_threshold_3x" in decisions[0].applied_validations
    assert "raw_probability_entry_blocked" in decisions[0].applied_validations


def test_empty_decision_snapshot_id_blocks_before_edge_selection(monkeypatch):
    clob = _patch_evaluator(monkeypatch, entry_price=0.01, snapshot_id="")

    decisions = evaluator_module.evaluate_candidate(
        _candidate(discovery_mode=DiscoveryMode.OPENING_HUNT.value),
        conn=None,
        portfolio=PortfolioState(bankroll=211.37),
        clob=clob,
        limits=evaluator_module.RiskLimits(min_order_usd=1.0),
        decision_time=TEST_DECISION_TIME,
    )

    assert len(decisions) == 1
    assert decisions[0].should_trade is False
    assert decisions[0].edge is None
    assert decisions[0].strategy_key == ""
    assert decisions[0].rejection_stage == "SIGNAL_QUALITY"
    assert decisions[0].availability_status == "DATA_UNAVAILABLE"
    assert decisions[0].rejection_reasons == ["ens_snapshot_persistence_failed"]
    assert decisions[0].rejection_reason_detail == "ENS snapshot persistence failed: decision_snapshot_id unavailable"
    assert "ens_snapshot_persistence" in decisions[0].applied_validations


def test_snapshot_p_raw_persistence_failure_blocks_before_edge_selection(monkeypatch):
    clob = _patch_evaluator(
        monkeypatch,
        entry_price=0.01,
        snapshot_id="snap-1",
        p_raw_store_result=False,
    )

    decisions = evaluator_module.evaluate_candidate(
        _candidate(discovery_mode=DiscoveryMode.OPENING_HUNT.value),
        conn=None,
        portfolio=PortfolioState(bankroll=211.37),
        clob=clob,
        limits=evaluator_module.RiskLimits(min_order_usd=1.0),
        decision_time=TEST_DECISION_TIME,
    )

    assert len(decisions) == 1
    assert decisions[0].should_trade is False
    assert decisions[0].edge is None
    assert decisions[0].strategy_key == ""
    assert decisions[0].rejection_stage == "SIGNAL_QUALITY"
    assert decisions[0].availability_status == "DATA_UNAVAILABLE"
    assert decisions[0].rejection_reasons == ["ens_snapshot_p_raw_persistence_failed"]
    assert decisions[0].rejection_reason_detail == "ENS snapshot p_raw persistence failed: canonical p_raw unavailable"
    assert "ens_snapshot_p_raw_persistence" in decisions[0].applied_validations
