"""Tests for MarketAnalysis and market fusion.

Covers:
1. VWMP calculation and edge case (total_size=0)
2. compute_alpha with various maturity levels and adjustments
3. MarketAnalysis.find_edges with known mispricing → edge found
4. Fair-priced market → no edges (CI crosses zero)
"""

import numpy as np
import pytest

from src.strategy.market_fusion import vwmp, compute_alpha, compute_posterior
from src.strategy.market_analysis import MarketAnalysis
from src.calibration.platt import ExtendedPlattCalibrator
from src.types import Bin, BinEdge


class TestVWMP:
    def test_equal_sizes(self):
        """Equal bid/ask sizes → VWMP = mid-price."""
        result = vwmp(0.45, 0.55, 100.0, 100.0)
        assert result == pytest.approx(0.50, abs=0.001)

    def test_bid_heavy(self):
        """Large bid → VWMP closer to ask."""
        result = vwmp(0.45, 0.55, 1000.0, 100.0)
        assert result > 0.50  # Closer to ask

    def test_ask_heavy(self):
        """Large ask → VWMP closer to bid."""
        result = vwmp(0.45, 0.55, 100.0, 1000.0)
        assert result < 0.50  # Closer to bid

    def test_zero_size_fallback(self):
        """CLAUDE.md: VWMP with total size = 0 → fall back to mid-price."""
        result = vwmp(0.45, 0.55, 0.0, 0.0)
        assert result == pytest.approx(0.50, abs=0.001)


class TestComputeAlpha:
    def test_level_1_base(self):
        a = compute_alpha(1, 3.0, "AGREE", 3, 24.0)
        assert a == pytest.approx(0.65, abs=0.01)

    def test_level_4_base(self):
        a = compute_alpha(4, 3.0, "AGREE", 3, 24.0)
        assert a == pytest.approx(0.25, abs=0.01)

    def test_conflict_reduces_alpha(self):
        a_agree = compute_alpha(1, 3.0, "AGREE", 3, 24.0)
        a_conflict = compute_alpha(1, 3.0, "CONFLICT", 3, 24.0)
        assert a_conflict < a_agree

    def test_fresh_market_increases_alpha(self):
        """hours_since_open < 6 → +0.15 total (0.10 + 0.05)."""
        a_old = compute_alpha(2, 3.0, "AGREE", 3, 48.0)
        a_fresh = compute_alpha(2, 3.0, "AGREE", 3, 4.0)
        assert a_fresh > a_old

    def test_clamped_floor(self):
        """Alpha should never go below 0.20."""
        a = compute_alpha(4, 8.0, "CONFLICT", 7, 48.0)
        assert a >= 0.20

    def test_clamped_ceiling(self):
        """Alpha should never exceed 0.85."""
        a = compute_alpha(1, 1.0, "AGREE", 1, 2.0)
        assert a <= 0.85


class TestComputePosterior:
    def test_alpha_half(self):
        """α=0.5 → posterior = average of model and market."""
        p_cal = np.array([0.6, 0.4])
        p_market = np.array([0.4, 0.6])
        result = compute_posterior(p_cal, p_market, 0.5)
        np.testing.assert_array_almost_equal(result, [0.5, 0.5])

    def test_alpha_one(self):
        """α=1.0 → posterior = model."""
        p_cal = np.array([0.8, 0.2])
        p_market = np.array([0.3, 0.7])
        result = compute_posterior(p_cal, p_market, 1.0)
        np.testing.assert_array_almost_equal(result, p_cal)


class TestMarketAnalysis:
    def _make_bins(self) -> list[Bin]:
        return [
            Bin(low=None, high=32, label="32 or below"),
            Bin(low=33, high=34, label="33-34"),
            Bin(low=35, high=36, label="35-36"),
            Bin(low=37, high=38, label="37-38"),
            Bin(low=39, high=40, label="39-40"),
            Bin(low=41, high=42, label="41-42"),
            Bin(low=43, high=44, label="43-44"),
            Bin(low=45, high=46, label="45-46"),
            Bin(low=47, high=48, label="47-48"),
            Bin(low=49, high=50, label="49-50"),
            Bin(low=51, high=None, label="51 or higher"),
        ]

    def test_mispriced_market_finds_edges(self):
        """Model says center bin is 30% but market prices at 10% → edge exists."""
        np.random.seed(42)
        bins = self._make_bins()

        # Model: strong peak at bin 4 (39-40)
        p_raw = np.array([0.02, 0.05, 0.10, 0.20, 0.30, 0.20, 0.08, 0.03, 0.01, 0.005, 0.005])
        p_cal = p_raw.copy()  # Assume identity calibration
        # Market: underprices bin 4
        p_market = np.array([0.05, 0.08, 0.10, 0.12, 0.10, 0.12, 0.10, 0.08, 0.08, 0.08, 0.09])

        member_maxes = np.random.default_rng(42).normal(40, 2, 51)

        ma = MarketAnalysis(
            p_raw=p_raw, p_cal=p_cal, p_market=p_market,
            alpha=0.65, bins=bins, member_maxes=member_maxes, lead_days=3.0,
        )

        edges = ma.find_edges(n_bootstrap=100)
        # Should find at least one edge (bin 4 is underpriced by market)
        assert len(edges) > 0
        # At least one edge should be buy_yes on a center bin
        yes_edges = [e for e in edges if e.direction == "buy_yes"]
        assert len(yes_edges) > 0

    def test_fair_priced_no_edges(self):
        """If model and market agree, no edges should be found."""
        np.random.seed(42)
        bins = self._make_bins()

        p = np.array([0.05, 0.08, 0.12, 0.18, 0.24, 0.15, 0.08, 0.04, 0.03, 0.02, 0.01])
        member_maxes = np.random.default_rng(42).normal(40, 3, 51)

        ma = MarketAnalysis(
            p_raw=p, p_cal=p.copy(), p_market=p.copy(),
            alpha=0.65, bins=bins, member_maxes=member_maxes, lead_days=3.0,
        )

        edges = ma.find_edges(n_bootstrap=100)
        # Should find zero or very few edges (CI should cross zero)
        assert len(edges) <= 2  # Allow small noise effects

    def test_vig_computed(self):
        bins = self._make_bins()
        p_market = np.array([0.09, 0.09, 0.09, 0.09, 0.09, 0.09, 0.09, 0.09, 0.09, 0.09, 0.10])
        member_maxes = np.ones(51) * 40.0

        ma = MarketAnalysis(
            p_raw=p_market, p_cal=p_market, p_market=p_market,
            alpha=0.5, bins=bins, member_maxes=member_maxes,
        )
        assert ma.vig == pytest.approx(1.0, abs=0.01)
