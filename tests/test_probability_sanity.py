# Created: 2026-05-22
# Last reused or audited: 2026-05-22
# Authority basis: docs/operations/P0_FORECAST_EXTREMA_AUTHORITY_2026-05-22.md §PR-D
# Lifecycle: created=2026-05-22; last_reviewed=2026-05-22; last_reused=never
# Purpose: Unit tests for probability_sanity.validate_high_distribution — all gate branches.
# Reuse: Run when validate_high_distribution gates, bin logic, or sanity thresholds change.
"""Tests for src/signal/probability_sanity.validate_high_distribution.

Cases:
  (a) categorical pass — all gates clear → (True, None)
  (b) p_raw sum=1.3 → P_RAW_NOT_CATEGORICAL
  (c) point bin p_cal=0.72 + zero member support → POINT_BUCKET_HIGH_PROB_WITHOUT_MEMBER_SUPPORT
  (d) market px=0.03 + p_cal=0.72 → EXTREME_MARKET_DISAGREEMENT_LOW_PRICE_HIGH_PROB
  (e) multiple bins >0.35 with low market price — first bad bin flagged
  Amsterdam 23°C@0.716 / 3¢ mirror case covered by (d).
"""
import numpy as np
import pytest

from src.signal.probability_sanity import validate_high_distribution
from src.types.market import Bin


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _bins_c(*centers: float) -> list[Bin]:
    """Make a list of point Celsius bins (low == high)."""
    return [Bin(low=c, high=c, unit="C", label=f"{c}°C") for c in centers]


def _uniform(n: int) -> np.ndarray:
    """Uniform probability vector summing to exactly 1."""
    return np.full(n, 1.0 / n)


# ---------------------------------------------------------------------------
# (a) categorical pass
# ---------------------------------------------------------------------------

def test_pass_categorical():
    """All gates clear: sums correct, no point-bucket issue, no market disagreement."""
    bins = _bins_c(20.0, 21.0, 22.0, 23.0, 24.0)
    n = len(bins)
    p_raw = _uniform(n)
    p_cal = _uniform(n)
    members = np.array([20.5, 21.0, 22.0, 23.5, 24.0, 21.5, 22.5])
    market = np.array([0.20, 0.20, 0.20, 0.20, 0.20])

    ok, reason = validate_high_distribution(
        bins=bins,
        p_raw=p_raw,
        p_cal=p_cal,
        member_samples=members,
        market_prices=market,
        strategy_key="test_pass",
    )
    assert ok is True
    assert reason is None


# ---------------------------------------------------------------------------
# (b) p_raw sum = 1.3 → P_RAW_NOT_CATEGORICAL
# ---------------------------------------------------------------------------

def test_p_raw_not_categorical():
    bins = _bins_c(20.0, 21.0, 22.0)
    p_raw = np.array([0.5, 0.5, 0.3])          # sum = 1.3
    p_cal = np.array([0.33, 0.34, 0.33])       # sum ≈ 1.0
    members = np.linspace(20, 22, 50)

    ok, reason = validate_high_distribution(
        bins=bins,
        p_raw=p_raw,
        p_cal=p_cal,
        member_samples=members,
        market_prices=None,
        strategy_key="test_b",
    )
    assert ok is False
    assert reason is not None
    assert reason.startswith("P_RAW_NOT_CATEGORICAL:")
    assert "1.3" in reason


# ---------------------------------------------------------------------------
# (c) point bin p_cal=0.72, zero member support → POINT_BUCKET_HIGH_PROB_WITHOUT_MEMBER_SUPPORT
# ---------------------------------------------------------------------------

def test_point_bucket_high_prob_without_member_support():
    """Mirrors a distribution with a single °C bin capturing 72% probability
    but members unanimously pointing elsewhere (Amsterdam-style: bin=23°C,
    all members far from 23)."""
    # 5 point bins: 20, 21, 22, 23, 24
    bins = _bins_c(20.0, 21.0, 22.0, 23.0, 24.0)
    p_raw = np.array([0.05, 0.10, 0.08, 0.72, 0.05])  # mode = bin[3] = 23°C
    p_cal = np.array([0.05, 0.10, 0.08, 0.72, 0.05])  # sum = 1.00
    # All 50 members land far from 23°C (at 19.5°C) → support in [23,23] = 0
    members = np.full(50, 19.5)

    ok, reason = validate_high_distribution(
        bins=bins,
        p_raw=p_raw,
        p_cal=p_cal,
        member_samples=members,
        market_prices=None,
        strategy_key="test_c",
    )
    assert ok is False
    assert reason is not None
    assert "POINT_BUCKET_HIGH_PROB_WITHOUT_MEMBER_SUPPORT" in reason
    assert "0.72" in reason or "0.7200" in reason
    assert "support=0.0000" in reason


# ---------------------------------------------------------------------------
# (d) market px=0.03 + p_cal=0.72 → EXTREME_MARKET_DISAGREEMENT
#     Direct mirror: Amsterdam 23°C @ p_cal=0.716, market_price=0.03
# ---------------------------------------------------------------------------

def test_extreme_market_disagreement_amsterdam_mirror():
    """Amsterdam 23°C@0.716 / 3¢ case: model says 72%, market says 3¢."""
    bins = _bins_c(20.0, 21.0, 22.0, 23.0, 24.0)
    p_raw = np.array([0.05, 0.08, 0.10, 0.72, 0.05])
    p_cal = np.array([0.05, 0.08, 0.10, 0.72, 0.05])
    members = np.full(50, 23.0)  # all members at 23 — passes point-bucket gate
    # bin[3]=23°C priced at 0.03 (3¢) despite p_cal=0.72
    market = np.array([0.20, 0.20, 0.20, 0.03, 0.37])

    ok, reason = validate_high_distribution(
        bins=bins,
        p_raw=p_raw,
        p_cal=p_cal,
        member_samples=members,
        market_prices=market,
        strategy_key="test_d",
    )
    assert ok is False
    assert reason is not None
    assert "EXTREME_MARKET_DISAGREEMENT_LOW_PRICE_HIGH_PROB" in reason
    assert "idx=3" in reason
    assert "price=0.0300" in reason
    assert "p=0.7200" in reason


# ---------------------------------------------------------------------------
# (e) multiple bins with low market price + high p_cal — first bad bin flagged
# ---------------------------------------------------------------------------

def test_multiple_extreme_disagreement_first_flagged():
    """Two bins could trigger; validator returns the FIRST one (idx=1)."""
    bins = _bins_c(20.0, 21.0, 22.0)
    p_raw = np.array([0.05, 0.50, 0.45])
    p_cal = np.array([0.05, 0.50, 0.45])
    members = np.array([21.0] * 30 + [22.0] * 20)
    # Both idx=1 and idx=2 have p_cal>0.35 and px<0.05
    market = np.array([0.10, 0.04, 0.04])

    ok, reason = validate_high_distribution(
        bins=bins,
        p_raw=p_raw,
        p_cal=p_cal,
        member_samples=members,
        market_prices=market,
        strategy_key="test_e",
    )
    assert ok is False
    assert reason is not None
    assert "EXTREME_MARKET_DISAGREEMENT_LOW_PRICE_HIGH_PROB" in reason
    assert "idx=1" in reason   # first offending bin
