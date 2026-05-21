# Created: 2026-05-21
# Last reused or audited: 2026-05-21
# Authority basis: docs/operations/task_2026-05-21_strategy_vnext_phase3_shoulder/PHASE_3_SHOULDER_PLAN.md §2 T2 + 04_PHASE_3_SHOULDER.md

"""Tests for classify_shoulder_candidate (ShoulderStrategyVNext classifier).

Activated in T2 production pass.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.contracts.no_trade_reason import NoTradeReason
from src.contracts.shoulder_strategy_vnext import ShoulderStrategyVNext, classify_shoulder_candidate
from src.strategy.strategy_profile import _classify_via_registry
from src.types.market import Bin, BinEdge


def _make_shoulder_edge(direction: str = "buy_no", is_open_high: bool = True) -> BinEdge:
    """Build a minimal open-shoulder BinEdge for testing."""
    if is_open_high:
        b = Bin(low=90.0, high=None, unit="F", label="90°F or higher")
    else:
        b = Bin(low=None, high=50.0, unit="F", label="50°F or below")
    return BinEdge(
        bin=b,
        direction=direction,
        edge=0.05,
        ci_lower=0.40,
        ci_upper=0.60,
        p_model=0.50,
        p_market=0.45,
        p_posterior=0.50,
        entry_price=0.45,
        p_value=0.03,
        vwmp=0.44,
    )


def _make_candidate():
    """Minimal candidate namespace for classifier tests."""
    city = SimpleNamespace(name="Chicago", timezone="America/Chicago")
    return SimpleNamespace(
        city=city,
        target_date="2026-07-15",
        temperature_metric="high",
        slug="chicago-high-2026-07-15",
        event_id="",
    )


def test_classify_shoulder_candidate_returns_vnext_for_valid_shoulder():
    """classify_shoulder_candidate returns ShoulderStrategyVNext for a valid open-shoulder edge."""
    edge = _make_shoulder_edge(direction="buy_no", is_open_high=True)
    candidate = _make_candidate()
    result = classify_shoulder_candidate(edge, candidate, market_phase=None, conn=None)

    assert result is not None
    assert isinstance(result, ShoulderStrategyVNext)
    assert result.is_open_shoulder is True
    assert result.shoulder_side == "upper"
    assert result.tail_direction == "above_threshold"
    assert result.metric == "high"
    assert result.no_trade_reason == NoTradeReason.SHOULDER_NO_TRADE_GATE
    # Thin mode: probabilistic fields are nan
    import math
    assert math.isnan(result.tail_probability_raw)
    assert math.isnan(result.tail_probability_calibrated)
    assert math.isnan(result.tail_probability_stressed)


def test_classify_shoulder_candidate_returns_none_for_finite_bin():
    """classify_shoulder_candidate returns None when edge.bin.is_shoulder is False."""
    # Fahrenheit bins must cover exactly 2 degrees per Bin validation.
    finite_bin = Bin(low=60.0, high=61.0, unit="F", label="60-61°F")
    edge = BinEdge(
        bin=finite_bin,
        direction="buy_no",
        edge=0.05,
        ci_lower=0.40,
        ci_upper=0.60,
        p_model=0.50,
        p_market=0.45,
        p_posterior=0.50,
        entry_price=0.45,
        p_value=0.03,
        vwmp=0.44,
    )
    candidate = _make_candidate()
    result = classify_shoulder_candidate(edge, candidate, market_phase=None, conn=None)
    assert result is None


def test_classify_via_registry_replaces_evaluator_hardcoded_shoulder_branch():
    """_classify_via_registry returns ShoulderStrategyVNext for shoulder buy_no edges."""
    edge = _make_shoulder_edge(direction="buy_no", is_open_high=True)
    candidate = _make_candidate()
    ctx = SimpleNamespace(edge=edge, candidate=candidate, market_phase=None, conn=None)
    result = _classify_via_registry("shoulder_sell", ctx)
    assert result is not None
    assert isinstance(result, ShoulderStrategyVNext)
    assert result.no_trade_reason == NoTradeReason.SHOULDER_NO_TRADE_GATE


def test_classify_via_registry_fail_closed_on_unknown_strategy():
    """_classify_via_registry returns None (not raises) for unknown strategy_id."""
    edge = _make_shoulder_edge(direction="buy_no", is_open_high=True)
    candidate = _make_candidate()
    ctx = SimpleNamespace(edge=edge, candidate=candidate, market_phase=None, conn=None)
    result = _classify_via_registry("completely_unknown_strategy_xyz", ctx)
    assert result is None


def test_inv_classifier_equals_registry_for_all_boot_safe_strategies():
    """Relationship test: _classify_via_registry returns ShoulderStrategyVNext for
    boot-safe shoulder strategies (shoulder_sell) and None for non-shoulder strategies."""
    from src.strategy.strategy_profile import live_safe_keys

    edge_shoulder = _make_shoulder_edge(direction="buy_no", is_open_high=True)
    edge_center = BinEdge(
        bin=Bin(low=60.0, high=61.0, unit="F", label="60-61°F"),
        direction="buy_yes",
        edge=0.05, ci_lower=0.40, ci_upper=0.60,
        p_model=0.50, p_market=0.45, p_posterior=0.50,
        entry_price=0.45, p_value=0.03, vwmp=0.44,
    )
    candidate = _make_candidate()
    ctx_shoulder = SimpleNamespace(edge=edge_shoulder, candidate=candidate, market_phase=None, conn=None)

    # shoulder_sell must classify successfully for a shoulder buy_no edge
    result = _classify_via_registry("shoulder_sell", ctx_shoulder)
    assert result is not None, "shoulder_sell must classify open-shoulder buy_no as ShoulderStrategyVNext"

    # Non-shoulder strategies must return None for the same edge
    for key in live_safe_keys() - {"shoulder_sell", "shoulder_buy"}:
        ctx = SimpleNamespace(edge=edge_shoulder, candidate=candidate, market_phase=None, conn=None)
        assert _classify_via_registry(key, ctx) is None, (
            f"Strategy {key!r} should return None for a shoulder edge "
            f"(only shoulder_sell/shoulder_buy classify shoulders)"
        )
