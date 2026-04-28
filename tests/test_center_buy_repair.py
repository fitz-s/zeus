from __future__ import annotations

from datetime import datetime, timezone

import numpy as np

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
    cluster="US-Northeast",
    settlement_unit="F",
    wu_station="KLGA",
)


def _stub_full_family_scan(monkeypatch):
    def _scan(analysis, *args, **kwargs):
        return [
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
            for i, edge in enumerate(analysis.find_edges(n_bootstrap=kwargs.get("n_bootstrap", 0)))
        ]

    monkeypatch.setattr(evaluator_module, "scan_full_hypothesis_family", _scan)


def _patch_evaluator(monkeypatch, *, entry_price: float):
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
            edge = BinEdge(
                bin=self.bins[1],
                direction="buy_yes",
                edge=0.05,
                ci_lower=0.03,
                ci_upper=0.07,
                p_model=0.06,
                p_market=entry_price,
                p_posterior=0.06,
                entry_price=entry_price,
                p_value=0.02,
                vwmp=entry_price,
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

    monkeypatch.setattr(
        evaluator_module,
        "fetch_ensemble",
        lambda city, forecast_days=8, model=None: {
            "members_hourly": np.ones((51, 48)) * 40.0,
            "times": [datetime(2026, 4, 3, hour % 24, 0, tzinfo=timezone.utc).isoformat() for hour in range(48)],
            "issue_time": datetime.now(timezone.utc),
            "fetch_time": datetime.now(timezone.utc),
            "model": model or "ecmwf_ifs025",
        },
    )
    monkeypatch.setattr(evaluator_module, "validate_ensemble", lambda result, expected_members=51: result is not None)
    monkeypatch.setattr(evaluator_module, "EnsembleSignal", DummyEnsembleSignal)
    monkeypatch.setattr(evaluator_module, "_store_ens_snapshot", lambda *args, **kwargs: "snap-1")
    monkeypatch.setattr(evaluator_module, "_store_snapshot_p_raw", lambda *args, **kwargs: None)
    monkeypatch.setattr(evaluator_module, "get_calibrator", lambda *args, **kwargs: (None, 4))
    monkeypatch.setattr(evaluator_module, "MarketAnalysis", DummyAnalysis)
    _stub_full_family_scan(monkeypatch)
    monkeypatch.setattr(evaluator_module, "fdr_filter", lambda edges, fdr_alpha=0.10: list(edges))
    monkeypatch.setattr(evaluator_module, "dynamic_kelly_mult", lambda **kwargs: 0.25)
    monkeypatch.setattr(evaluator_module, "kelly_size", lambda *args, **kwargs: 5.0)
    monkeypatch.setattr(evaluator_module, "check_position_allowed", lambda **kwargs: (True, ""))
    return DummyClob()


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
        portfolio=PortfolioState(bankroll=150.0),
        clob=clob,
        limits=evaluator_module.RiskLimits(min_order_usd=1.0),
    )

    assert len(decisions) == 1
    assert decisions[0].should_trade is False
    assert decisions[0].strategy_key == "center_buy"
    assert decisions[0].rejection_stage == "MARKET_FILTER"
    assert decisions[0].rejection_reasons == ["CENTER_BUY_ULTRA_LOW_PRICE(0.0100<=0.02)"]
    assert "center_buy_ultra_low_price_guard" in decisions[0].applied_validations


def test_opening_inertia_low_price_entry_is_not_blocked_by_center_buy_guard(monkeypatch):
    clob = _patch_evaluator(monkeypatch, entry_price=0.01)

    decisions = evaluator_module.evaluate_candidate(
        _candidate(discovery_mode=DiscoveryMode.OPENING_HUNT.value),
        conn=None,
        portfolio=PortfolioState(bankroll=150.0),
        clob=clob,
        limits=evaluator_module.RiskLimits(min_order_usd=1.0),
    )

    assert len(decisions) == 1
    assert decisions[0].should_trade is True
    assert decisions[0].strategy_key == "opening_inertia"
