"""Tests for FDR (Benjamini-Hochberg) filter.

Covers:
1. Happy path: 10 edges with known p-values → correct filtering
2. Edge case: all p-values below threshold → all pass
3. Edge case: all p-values above threshold → none pass
4. Empty input → empty output
"""

import pytest

from src.strategy.fdr_filter import fdr_filter
from src.types import Bin, BinEdge


def _make_edge(p_value: float) -> BinEdge:
    """Helper: create a BinEdge with given p_value."""
    return BinEdge(
        bin=Bin(low=40, high=41, unit="F"),
        direction="buy_yes",
        edge=0.05,
        ci_lower=0.01,
        ci_upper=0.10,
        p_model=0.15,
        p_market=0.10,
        p_posterior=0.15,
        entry_price=0.10,
        p_value=p_value,
        vwmp=0.10,
    )


class TestFDRFilter:
    def test_known_p_values(self):
        """Standard BH test with 10 edges at known p-values.

        fdr_alpha=0.10, m=10.
        BH thresholds: k/10 * 0.10 = [0.01, 0.02, 0.03, ..., 0.10]
        """
        p_values = [0.005, 0.01, 0.025, 0.05, 0.08, 0.12, 0.15, 0.30, 0.50, 0.90]
        edges = [_make_edge(p) for p in p_values]

        result = fdr_filter(edges, fdr_alpha=0.10)

        # k=1: p=0.005 <= 0.01 ✓
        # k=2: p=0.01  <= 0.02 ✓
        # k=3: p=0.025 <= 0.03 ✓
        # k=4: p=0.05  <= 0.04 ✗
        # So threshold_k = 3
        assert len(result) == 3
        # Results should be sorted by p-value
        assert result[0].p_value <= result[1].p_value <= result[2].p_value

    def test_all_significant(self):
        """All p-values very low → all pass."""
        edges = [_make_edge(0.001) for _ in range(5)]
        result = fdr_filter(edges, fdr_alpha=0.10)
        assert len(result) == 5

    def test_none_significant(self):
        """All p-values high → none pass."""
        edges = [_make_edge(0.50) for _ in range(5)]
        result = fdr_filter(edges, fdr_alpha=0.10)
        assert len(result) == 0

    def test_empty_input(self):
        assert fdr_filter([], fdr_alpha=0.10) == []

    def test_single_edge_passes(self):
        """Single edge with p=0.05, fdr=0.10 → passes (0.05 <= 0.10 * 1/1)."""
        result = fdr_filter([_make_edge(0.05)], fdr_alpha=0.10)
        assert len(result) == 1

    def test_single_edge_fails(self):
        """Single edge with p=0.15, fdr=0.10 → fails (0.15 > 0.10)."""
        result = fdr_filter([_make_edge(0.15)], fdr_alpha=0.10)
        assert len(result) == 0
