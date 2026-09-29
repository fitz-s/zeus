# Created: 2026-05-02
# Last reused/audited: 2026-07-23
# Lifecycle: created=2026-05-02; last_reviewed=2026-07-23; last_reused=2026-07-23
# Purpose: Lock durable strategy_key classification and fail-closed behavior across evaluator/runtime persistence boundaries.
# Reuse: Run before changing evaluator strategy_key mapping, discovery-mode classification, or strategy_key DB persistence.
# Authority basis: PR44 review comment 3177316079 / 3177317099 strategy_key unclassified fail-closed contract;
#                  2026-05-21 live CHECK repair: discovery modes must not invent strategy_key values.

from __future__ import annotations

import ast
import json
import sqlite3
from pathlib import Path

import pytest

from src.config import City
from src.engine.discovery_mode import DiscoveryMode
from src.engine import cycle_runner
from src.engine.evaluator import (
    MarketCandidate,
)
from src.state.db import init_schema, init_schema_forecasts
from src.strategy.market_analysis_family_scan import FullFamilyHypothesis
from src.types.market import Bin, BinEdge


def _city() -> City:
    return City(
        name="NYC",
        lat=40.7772,
        lon=-73.8726,
        timezone="America/New_York",
        cluster="NYC",
        settlement_unit="F",
        wu_station="KLGA",
        settlement_source_type="wu_icao",
    )


def _candidate(discovery_mode: str = DiscoveryMode.UPDATE_REACTION.value) -> MarketCandidate:
    return MarketCandidate(
        city=_city(),
        target_date="2026-05-03",
        outcomes=[],
        hours_since_open=30.0,
        temperature_metric="high",
        discovery_mode=discovery_mode,
    )


def _day0_candidate_with_observed_high(observed_high: float) -> MarketCandidate:
    from src.strategy.market_phase import MarketPhase

    return MarketCandidate(
        city=_city(),
        target_date="2026-05-03",
        outcomes=[],
        hours_since_open=30.0,
        temperature_metric="high",
        discovery_mode=DiscoveryMode.DAY0_CAPTURE.value,
        market_phase=MarketPhase.SETTLEMENT_DAY,
        observation={"high_so_far": observed_high, "current_temp": observed_high},
    )


def _imminent_candidate_with_observed_high(observed_high: float) -> MarketCandidate:
    return _settlement_day_candidate_with_observed_high(
        observed_high,
        discovery_mode=DiscoveryMode.IMMINENT_OPEN_CAPTURE.value,
        hours_since_open=0.5,
    )


def _settlement_day_candidate_with_observed_high(
    observed_high: float,
    *,
    discovery_mode: str,
    hours_since_open: float = 30.0,
) -> MarketCandidate:
    from src.strategy.market_phase import MarketPhase

    return MarketCandidate(
        city=_city(),
        target_date="2026-05-03",
        outcomes=[],
        hours_since_open=hours_since_open,
        temperature_metric="high",
        discovery_mode=discovery_mode,
        market_phase=MarketPhase.SETTLEMENT_DAY,
        observation={"high_so_far": observed_high, "current_temp": observed_high},
    )


def _unclassified_edge() -> BinEdge:
    return BinEdge(
        bin=Bin(70, 71, "F", "70-71°F"),
        direction="buy_no",
        edge=0.08,
        ci_lower=0.02,
        ci_upper=0.12,
        p_model=0.40,
        p_market=0.52,
        p_posterior=0.40,
        entry_price=0.48,
        p_value=0.01,
        vwmp=0.52,
        support_index=1,
    )


def _shoulder_buy_edge() -> BinEdge:
    return BinEdge(
        bin=Bin(low=None, high=38, label="38°F or below", unit="F"),
        direction="buy_yes",
        edge=0.08,
        ci_lower=0.02,
        ci_upper=0.12,
        p_model=0.40,
        p_market=0.52,
        p_posterior=0.40,
        entry_price=0.48,
        p_value=0.01,
        vwmp=0.52,
        support_index=0,
    )


def _finite_buy_yes_edge(low: float, high: float) -> BinEdge:
    return BinEdge(
        bin=Bin(low=low, high=high, label=f"{low:g}-{high:g}°F", unit="F"),
        direction="buy_yes",
        edge=0.80,
        ci_lower=0.70,
        ci_upper=0.90,
        p_model=0.90,
        p_market=0.10,
        p_posterior=0.90,
        entry_price=0.04,
        p_value=0.01,
        vwmp=0.04,
        support_index=1,
    )


def _finite_buy_no_edge(low: float, high: float) -> BinEdge:
    edge = _finite_buy_yes_edge(low, high)
    edge.direction = "buy_no"
    return edge


def _hypothesis(*, direction: str, is_shoulder: bool) -> FullFamilyHypothesis:
    return FullFamilyHypothesis(
        index=0,
        range_label="38°F or below" if is_shoulder else "70-71°F",
        direction=direction,
        edge=0.08,
        ci_lower=0.02,
        ci_upper=0.12,
        p_value=0.01,
        p_model=0.40,
        p_market=0.52,
        p_posterior=0.40,
        entry_price=0.48,
        is_shoulder=is_shoulder,
        passed_prefilter=True,
    )


@pytest.mark.parametrize(
    ("metric", "direction", "observed", "low", "high", "expected"),
    [
        ("high", "buy_yes", 38.0, 38.0, None, "locked"),
        ("high", "buy_no", 38.0, 38.0, None, "refuted"),
        ("high", "buy_no", 38.0, 36.0, 37.0, "locked"),
        ("low", "buy_yes", 20.0, None, 20.0, "locked"),
        ("low", "buy_no", 20.0, None, 20.0, "refuted"),
        ("low", "buy_no", 25.0, 26.0, 27.0, "locked"),
    ],
)
def test_day0_payoff_truth_covers_metric_side_quadrants(
    metric, direction, observed, low, high, expected
) -> None:
    from src.contracts.day0_payoff_truth import classify_day0_payoff_truth

    assert (
        classify_day0_payoff_truth(
            metric=metric,
            direction=direction,
            observed_extreme=observed,
            bin_low=low,
            bin_high=high,
        ).value
        == expected
    )


def test_imminent_open_capture_and_opening_inertia_phase_allowlists_diverge() -> None:
    """IOC and opening_inertia have distinct market-phase allowlists.

    This test is NON-VACUOUS: if strategy_key reverts to "opening_inertia" for IOC mode,
    the settlement_day membership assertion fails.
    """
    from src.strategy.strategy_profile import try_get as _try_get_profile

    ioc_profile = _try_get_profile("imminent_open_capture")
    oi_profile = _try_get_profile("opening_inertia")
    assert ioc_profile is not None, "imminent_open_capture profile missing from registry"
    assert oi_profile is not None, "opening_inertia profile missing from registry"

    # IOC's allowed_market_phases includes settlement_day; opening_inertia's does not
    assert "settlement_day" in ioc_profile.allowed_market_phases
    assert "settlement_day" not in oi_profile.allowed_market_phases
