# Created: 2026-05-27
# Last reused or audited: 2026-05-27
# Authority basis: architecture/market_cost_seam_executable_uncertainty_2026_05_27.md §Wave3
"""Wave 3: depth-walk math for the asks ladder.

Pure-function tests for ``walk_asks_for_target_shares``. Codifies that:
  - top-of-book is preserved as the trivial case (single level fits).
  - multi-level orders walk the ladder and report depth-weighted fill price.
  - slippage_bps is non-negative and zero when fully filled at top.
  - thin-book scenarios mark ``depth_sufficient=False``.
  - degenerate inputs raise instead of silently returning bad numbers.
"""
from __future__ import annotations

import pytest

from src.data.orderbook_depth_walk import (
    DepthWalkResult,
    walk_asks_for_target_shares,
)


# ---------------------------------------------------------------------------
# Happy path — single level
# ---------------------------------------------------------------------------

class TestSingleLevel:
    def test_target_fits_in_top_level_uses_top_price(self):
        asks = [{"price": 0.42, "size": 200.0}]
        r = walk_asks_for_target_shares(asks, target_shares=100.0)
        assert r.fill_price_walk == pytest.approx(0.42)
        assert r.slippage_bps == 0.0
        assert r.depth_walked_shares == 100.0
        assert r.depth_sufficient is True
        assert r.levels_walked == 1
        assert r.best_ask == 0.42

    def test_top_level_exact_fit(self):
        asks = [{"price": 0.42, "size": 100.0}]
        r = walk_asks_for_target_shares(asks, target_shares=100.0)
        assert r.depth_sufficient is True
        assert r.levels_walked == 1


# ---------------------------------------------------------------------------
# Multi-level walk
# ---------------------------------------------------------------------------

class TestMultiLevel:
    def test_two_level_walk_returns_weighted_average(self):
        # 50 shares @ 0.40, 50 shares @ 0.42  ->  avg fill = 0.41
        asks = [{"price": 0.40, "size": 50.0}, {"price": 0.42, "size": 100.0}]
        r = walk_asks_for_target_shares(asks, target_shares=100.0)
        assert r.fill_price_walk == pytest.approx((50 * 0.40 + 50 * 0.42) / 100, abs=1e-9)
        assert r.slippage_bps > 0.0
        assert r.depth_sufficient is True
        assert r.levels_walked == 2

    def test_three_level_walk_returns_correct_average_and_slippage_sign(self):
        asks = [
            {"price": 0.40, "size": 30.0},
            {"price": 0.42, "size": 30.0},
            {"price": 0.45, "size": 100.0},
        ]
        r = walk_asks_for_target_shares(asks, target_shares=80.0)
        # 30@0.40 + 30@0.42 + 20@0.45 = 12.0 + 12.6 + 9.0 = 33.6; / 80 = 0.42
        assert r.fill_price_walk == pytest.approx((12.0 + 12.6 + 9.0) / 80, abs=1e-9)
        assert r.slippage_bps > 0.0
        assert r.levels_walked == 3
        assert r.depth_sufficient is True
        # slippage is fill - best_ask in bps; best_ask = 0.40
        assert r.slippage_bps == pytest.approx(((r.fill_price_walk - 0.40) / 0.40) * 10000, abs=1e-6)

    def test_unsorted_ladder_is_sorted_ascending_before_walk(self):
        # Same shape as previous test but rows out of order.
        asks = [
            {"price": 0.45, "size": 100.0},
            {"price": 0.40, "size": 30.0},
            {"price": 0.42, "size": 30.0},
        ]
        r = walk_asks_for_target_shares(asks, target_shares=80.0)
        # Result must match the sorted-input expectation.
        assert r.fill_price_walk == pytest.approx((12.0 + 12.6 + 9.0) / 80, abs=1e-9)
        assert r.best_ask == 0.40


# ---------------------------------------------------------------------------
# Thin book / insufficient depth
# ---------------------------------------------------------------------------

class TestThinBook:
    def test_thin_book_marks_depth_insufficient(self):
        asks = [{"price": 0.42, "size": 20.0}]
        r = walk_asks_for_target_shares(asks, target_shares=100.0)
        assert r.depth_sufficient is False
        assert r.depth_walked_shares == 20.0
        assert r.fill_price_walk == pytest.approx(0.42)

    def test_partial_fill_across_levels_marks_insufficient(self):
        asks = [
            {"price": 0.40, "size": 30.0},
            {"price": 0.42, "size": 30.0},
        ]
        r = walk_asks_for_target_shares(asks, target_shares=100.0)
        assert r.depth_sufficient is False
        assert r.depth_walked_shares == 60.0
        assert r.levels_walked == 2


# ---------------------------------------------------------------------------
# Edge cases / input validation
# ---------------------------------------------------------------------------

class TestInputValidation:
    def test_zero_or_negative_target_raises(self):
        asks = [{"price": 0.42, "size": 100.0}]
        with pytest.raises(ValueError, match="target_shares must be > 0"):
            walk_asks_for_target_shares(asks, target_shares=0)
        with pytest.raises(ValueError, match="target_shares must be > 0"):
            walk_asks_for_target_shares(asks, target_shares=-5)

    def test_empty_asks_raises(self):
        with pytest.raises(ValueError, match="empty"):
            walk_asks_for_target_shares([], target_shares=100.0)

    def test_price_out_of_bounds_raises(self):
        with pytest.raises(ValueError, match="out of"):
            walk_asks_for_target_shares([{"price": 1.5, "size": 100.0}], target_shares=10.0)
        with pytest.raises(ValueError, match="out of"):
            walk_asks_for_target_shares([{"price": 0.0, "size": 100.0}], target_shares=10.0)

    def test_zero_or_negative_size_raises(self):
        with pytest.raises(ValueError, match="size must be > 0"):
            walk_asks_for_target_shares([{"price": 0.42, "size": 0.0}], target_shares=10.0)

    def test_tuple_entries_accepted(self):
        # Some callers (test harnesses, replay paths) may pass (price, size) tuples.
        r = walk_asks_for_target_shares([(0.42, 100.0)], target_shares=50.0)
        assert r.depth_sufficient is True
        assert r.fill_price_walk == pytest.approx(0.42)
