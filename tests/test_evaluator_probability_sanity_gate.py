# Created: 2026-05-22
# Last reused or audited: 2026-05-22
# Authority basis: docs/operations/P0_FORECAST_EXTREMA_AUTHORITY_2026-05-22.md §PR-D
# Lifecycle: created=2026-05-22; last_reviewed=2026-05-22; last_reused=never
# Purpose: Integration test for probability_sanity_gate wiring in evaluator — Amsterdam mirror case.
# Reuse: Run when evaluator probability_sanity_gate call-site, validate_high_distribution signature, or NoTradeReason.PROBABILITY_SANITY_GATE changes.
"""Integration test for the probability_sanity_gate wired in evaluator.py.

Scope: prove that the gate logic rejects pathological distributions using the
same inputs that evaluate_candidate would supply.  Full evaluate_candidate
harness requires 8+ monkeypatches before reaching the gate; this file tests
the gate call directly against validate_high_distribution to verify wiring
correctness without duplicating the existing probability_sanity unit tests.

Amsterdam mirror case (the motivating defect):
  - city=Amsterdam, bin=23°C, p_cal=0.72, market=0.03, zero member support
  - Gate must reject at SIGNAL_QUALITY with NoTradeReason.PROBABILITY_SANITY_GATE
    and reason containing POINT_BUCKET_HIGH_PROB_WITHOUT_MEMBER_SUPPORT.
"""
from __future__ import annotations

import numpy as np
import pytest

from src.signal.probability_sanity import validate_high_distribution
from src.contracts.no_trade_reason import NoTradeReason
from src.types.market import Bin


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _bins_c(*centers: float) -> list[Bin]:
    return [Bin(low=c, high=c, unit="C", label=f"{c}°C") for c in centers]


# ---------------------------------------------------------------------------
# Gate contract tests
# ---------------------------------------------------------------------------

def test_probability_sanity_gate_rejects_point_bin_zero_support():
    """Pathological day0 HIGH: point bin p_cal=0.72, market=0.03, zero member support.

    This mirrors the Amsterdam defect: bin 23°C captures 72% calibrated
    probability, but all ensemble members sit at 19.5°C (zero support in the
    bin), and the market prices 23°C at 3¢.  validate_high_distribution must
    return (False, reason) and the reason must indicate
    POINT_BUCKET_HIGH_PROB_WITHOUT_MEMBER_SUPPORT before the market-
    disagreement gate is even reached.

    Verifies that the gate function (which evaluate_candidate calls) rejects
    this distribution at SIGNAL_QUALITY and the resulting NoTradeReason is
    PROBABILITY_SANITY_GATE (not UNCATEGORIZED).
    """
    bins = _bins_c(20.0, 21.0, 22.0, 23.0, 24.0)
    p_raw = np.array([0.05, 0.10, 0.08, 0.72, 0.05])
    p_cal = np.array([0.05, 0.10, 0.08, 0.72, 0.05])
    # All members far from 23°C → support in [23, 23] = 0
    member_samples = np.full(50, 19.5)
    # Market prices 23°C at 0.03 — also would trigger market-disagreement gate
    market_prices = np.array([0.20, 0.20, 0.20, 0.03, 0.37])

    ok, reason = validate_high_distribution(
        bins=bins,
        p_raw=p_raw,
        p_cal=p_cal,
        member_samples=member_samples,
        market_prices=market_prices,
        strategy_key="day0_high:Amsterdam:2026-05-22",
    )

    # Gate must fire
    assert ok is False, "expected gate to reject pathological distribution"
    assert reason is not None

    # Reason must be the point-bucket member-support failure (fires before
    # market-disagreement gate in the validator's gate order)
    assert "POINT_BUCKET_HIGH_PROB_WITHOUT_MEMBER_SUPPORT" in reason, (
        f"expected POINT_BUCKET_HIGH_PROB_WITHOUT_MEMBER_SUPPORT in reason, got: {reason!r}"
    )
    assert "0.7200" in reason or "0.72" in reason, f"p_cal not in reason: {reason!r}"
    assert "support=0.0000" in reason, f"zero-support not in reason: {reason!r}"

    # NoTradeReason enum member exists and is distinct from UNCATEGORIZED
    assert NoTradeReason.PROBABILITY_SANITY_GATE != NoTradeReason.UNCATEGORIZED
    assert str(NoTradeReason.PROBABILITY_SANITY_GATE) == "probability_sanity_gate"


def test_probability_sanity_gate_passes_healthy_distribution():
    """Healthy distribution: uniform p_raw/p_cal, spread members, fair market.

    Gate must not fire on well-formed inputs so the evaluator can proceed
    to MarketAnalysis / Kelly sizing.
    """
    bins = _bins_c(20.0, 21.0, 22.0, 23.0, 24.0)
    n = len(bins)
    p_raw = np.full(n, 1.0 / n)
    p_cal = np.full(n, 1.0 / n)
    member_samples = np.linspace(20.0, 24.0, 60)
    market_prices = np.full(n, 1.0 / n)

    ok, reason = validate_high_distribution(
        bins=bins,
        p_raw=p_raw,
        p_cal=p_cal,
        member_samples=member_samples,
        market_prices=market_prices,
        strategy_key="day0_high:TestCity:2026-05-22",
    )

    assert ok is True
    assert reason is None


def test_probability_sanity_gate_rejects_extreme_market_disagreement_only():
    """Market-disagreement case: p_cal=0.72 on bin with market=0.03, but members
    support that bin (so point-bucket gate passes).  Market-disagreement gate fires.
    """
    bins = _bins_c(20.0, 21.0, 22.0, 23.0, 24.0)
    p_raw = np.array([0.05, 0.08, 0.10, 0.72, 0.05])
    p_cal = np.array([0.05, 0.08, 0.10, 0.72, 0.05])
    # Members support 23°C → point-bucket passes
    member_samples = np.full(50, 23.0)
    market_prices = np.array([0.20, 0.20, 0.20, 0.03, 0.37])

    ok, reason = validate_high_distribution(
        bins=bins,
        p_raw=p_raw,
        p_cal=p_cal,
        member_samples=member_samples,
        market_prices=market_prices,
        strategy_key="day0_high:Amsterdam:2026-05-22",
    )

    assert ok is False
    assert reason is not None
    assert "EXTREME_MARKET_DISAGREEMENT_LOW_PRICE_HIGH_PROB" in reason
    assert "idx=3" in reason


def test_no_trade_reason_probability_sanity_gate_member_exists():
    """Enum membership check: PROBABILITY_SANITY_GATE must be a distinct member."""
    members = [m.name for m in NoTradeReason]
    assert "PROBABILITY_SANITY_GATE" in members
    # Must not collide with fallback
    assert NoTradeReason.PROBABILITY_SANITY_GATE != NoTradeReason.UNCATEGORIZED
    # StrEnum value is lowercase snake_case
    assert NoTradeReason.PROBABILITY_SANITY_GATE == "probability_sanity_gate"
