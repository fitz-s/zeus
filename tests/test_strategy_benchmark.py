# Lifecycle: created=2026-04-27; last_reviewed=2026-04-27; last_reused=2026-04-27
# Authority basis: docs/operations/task_2026-04-26_ultimate_plan/r3/slice_cards/A1.yaml
# Purpose: Lock INV-NEW-Q StrategyBenchmarkSuite replay/paper/shadow promotion gate.
# Reuse: Run for A1 benchmark/promotion-gate changes and future strategy candidate wiring.
"""R3 A1 StrategyBenchmarkSuite acceptance tests."""

from __future__ import annotations

import sqlite3

import pytest
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from src.strategy.benchmark_suite import (
    BenchmarkEnvironment,
    BenchmarkObservation,
    PromotionVerdict,
    ReplayCorpus,
    SemanticDriftFinding,
    StrategyBenchmarkSuite,
)
from src.strategy.candidates import (
    CrossMarketCorrelationHedge,
    LiquidityProvisionWithHeartbeat,
    NegRiskBasket,
    ResolutionWindowMaker,
    StaleQuoteDetector,
    WeatherEventArbitrage,
)
from src.strategy.data_lake import DataLake, MarketFilter
from tests.fakes.polymarket_v2 import FakePolymarketVenue

NOW = datetime(2026, 4, 27, 12, 0, tzinfo=timezone.utc)
STRATEGY = "center_bin_buy_v2"


def _corpus(strategy_key: str = STRATEGY, *, drift: tuple[SemanticDriftFinding, ...] = ()) -> ReplayCorpus:
    observations = (
        BenchmarkObservation(
            strategy_key=strategy_key,
            observed_at=NOW,
            alpha_pnl=12.0,
            spread_pnl=3.0,
            fees=1.0,
            slippage=0.5,
            failed_settlement_cost=0.0,
            capital_lock_cost=0.25,
            fill_probability=0.7,
            adverse_selection_bps=8.0,
            time_to_resolution_hours=4.0,
            liquidity_decay=0.01,
            opportunity_cost=0.25,
            calibrated_probability=0.61,
            market_implied_probability=0.55,
            capital_at_risk=100.0,
        ),
        BenchmarkObservation(
            strategy_key=strategy_key,
            observed_at=NOW + timedelta(hours=1),
            alpha_pnl=8.0,
            spread_pnl=2.0,
            fees=1.0,
            slippage=0.5,
            failed_settlement_cost=0.0,
            capital_lock_cost=0.25,
            fill_probability=0.5,
            adverse_selection_bps=12.0,
            time_to_resolution_hours=2.0,
            liquidity_decay=0.03,
            opportunity_cost=0.25,
            calibrated_probability=0.49,
            market_implied_probability=0.45,
            capital_at_risk=50.0,
        ),
    )
    return ReplayCorpus(strategy_key=strategy_key, observations=observations, window_start=NOW, window_end=NOW + timedelta(hours=2), semantic_drift=drift)


def test_benchmark_metrics_computed_for_replay():
    suite = StrategyBenchmarkSuite()
    metrics = suite.evaluate_replay(STRATEGY, _corpus())

    assert metrics.environment is BenchmarkEnvironment.REPLAY
    assert metrics.sample_count == 2
    assert metrics.ev_after_fees_slippage > 0
    assert metrics.realized_spread_capture == 2.5
    assert metrics.fill_probability == 0.6
    assert metrics.time_to_resolution_risk == 10 / 3


def test_benchmark_metrics_computed_for_paper_against_fake_venue():
    venue = FakePolymarketVenue()
    result = venue.submit_limit_order(token_id="token-a", price=0.50, size=10, side="BUY")
    venue.force_partial_fill(result.envelope.order_id or "", 4)

    metrics = StrategyBenchmarkSuite().evaluate_paper(STRATEGY, venue, duration_hours=1)

    assert metrics.environment is BenchmarkEnvironment.PAPER
    assert metrics.sample_count == 1
    assert metrics.ev_after_fees_slippage > 0
    assert metrics.fill_probability == 1.0


def test_benchmark_metrics_computed_for_live_shadow():
    suite = StrategyBenchmarkSuite(shadow_corpora={STRATEGY: _corpus()})

    metrics = suite.evaluate_live_shadow(STRATEGY, capital_cap_micro=100_000, duration_hours=2)

    assert metrics.environment is BenchmarkEnvironment.SHADOW
    assert metrics.sample_count == 2
    assert metrics.ev_after_fees_slippage > 0


def test_promotion_blocked_unless_replay_paper_shadow_all_pass():
    suite = StrategyBenchmarkSuite(shadow_corpora={STRATEGY: _corpus()})
    replay = suite.evaluate_replay(STRATEGY, _corpus())
    paper = replace(suite.evaluate_replay(STRATEGY, _corpus()), environment=BenchmarkEnvironment.PAPER)
    shadow = suite.evaluate_live_shadow(STRATEGY, capital_cap_micro=100_000, duration_hours=2)

    assert suite.promotion_decision(replay, paper, shadow).verdict is PromotionVerdict.PROMOTE

    blocked_shadow = StrategyBenchmarkSuite().evaluate_live_shadow(STRATEGY, capital_cap_micro=100_000, duration_hours=2)
    blocked = suite.promotion_decision(replay, paper, blocked_shadow)
    assert blocked.verdict is PromotionVerdict.BLOCK
    assert any("shadow" in reason for reason in blocked.reasons)


def test_pnl_split_into_alpha_spread_fees_slippage_failed_settlement_capital_lock():
    bad = BenchmarkObservation(
        strategy_key=STRATEGY,
        observed_at=NOW,
        alpha_pnl=5.0,
        spread_pnl=2.0,
        fees=0.5,
        slippage=0.25,
        failed_settlement_cost=1.5,
        capital_lock_cost=0.75,
        fill_probability=0.8,
    )
    metrics = StrategyBenchmarkSuite().evaluate_replay(
        STRATEGY,
        ReplayCorpus(strategy_key=STRATEGY, observations=(bad,), window_start=NOW, window_end=NOW),
    )

    assert metrics.pnl_breakdown == {
        "alpha": 5.0,
        "spread": 2.0,
        "fees": 0.5,
        "slippage": 0.25,
        "failed_settlement": 1.5,
        "capital_lock": 0.75,
    }
    assert metrics.ev_after_fees_slippage == 4.0


def test_backtest_to_paper_to_live_semantic_drift_report_empty_or_explicitly_waived():
    suite = StrategyBenchmarkSuite()
    waived = (SemanticDriftFinding("KNOWN_FIXTURE_DELTA", "fixture uses deterministic fake fills", waived=True),)
    clean = suite.evaluate_replay(STRATEGY, _corpus())
    waived_metrics = suite.evaluate_replay(STRATEGY, _corpus(drift=waived))
    unwaived = suite.evaluate_replay(STRATEGY, _corpus(drift=(SemanticDriftFinding("DRIFT", "unwaived"),)))

    assert clean.unwaived_semantic_drift == ()
    assert waived_metrics.unwaived_semantic_drift == ()
    assert suite.promotion_decision(clean, replace(clean, environment=BenchmarkEnvironment.PAPER), unwaived).verdict is PromotionVerdict.BLOCK


def test_calibration_error_vs_market_implied_p_computed():
    metrics = StrategyBenchmarkSuite().evaluate_replay(STRATEGY, _corpus())

    assert metrics.calibration_error_vs_market_implied == pytest.approx(0.05)


def test_strategy_benchmark_runs_are_persisted_to_supplied_connection_only():
    conn = sqlite3.connect(":memory:")
    suite = StrategyBenchmarkSuite()
    metrics = suite.evaluate_replay(STRATEGY, _corpus())

    run_id = suite.record_benchmark_run(conn, metrics, PromotionVerdict.NEEDS_REVIEW)

    row = conn.execute("SELECT run_id, strategy_key, environment, promotion_verdict FROM strategy_benchmark_runs").fetchone()
    assert row == (run_id, STRATEGY, "replay", "NEEDS_REVIEW")


def test_data_lake_fetch_replay_corpus_is_read_only_and_filterable():
    lake = DataLake([_corpus()])

    corpus = lake.fetch_replay_corpus(MarketFilter(strategy_key=STRATEGY), (NOW, NOW + timedelta(minutes=30)))

    assert len(corpus.observations) == 1
    assert corpus.observations[0].strategy_key == STRATEGY


def test_candidate_stubs_do_not_claim_executable_alpha():
    candidates = [
        WeatherEventArbitrage(),
        StaleQuoteDetector(),
        ResolutionWindowMaker(),
        NegRiskBasket(),
        CrossMarketCorrelationHedge(),
        LiquidityProvisionWithHeartbeat(),
    ]

    assert {candidate.strategy_key for candidate in candidates} == {
        "weather_event_arbitrage",
        "stale_quote_detector",
        "resolution_window_maker",
        "neg_risk_basket",
        "cross_market_correlation_hedge",
        "liquidity_provision_with_heartbeat",
    }
    assert all(candidate.metadata.executable_alpha is False for candidate in candidates)
