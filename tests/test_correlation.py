"""Tests for heuristic correlation matrix."""

import pytest

from src.strategy.correlation import get_correlation, correlated_exposure


class TestCorrelation:
    def test_same_cluster(self):
        assert get_correlation("US-Northeast", "US-Northeast") == 1.0

    def test_nearby_clusters(self):
        c = get_correlation("US-Northeast", "US-Midwest")
        assert 0.4 <= c <= 0.8

    def test_distant_clusters(self):
        c = get_correlation("US-Pacific", "Europe")
        assert c <= 0.2

    def test_symmetric(self):
        assert get_correlation("US-Northeast", "Europe") == \
               get_correlation("Europe", "US-Northeast")


class TestCorrelatedExposure:
    def test_empty_portfolio(self):
        exp = correlated_exposure([], "US-Northeast", 0.05, 100.0)
        assert exp == pytest.approx(0.05)  # Just the new position

    def test_same_cluster_increases(self):
        positions = [{"cluster": "US-Northeast", "size_usd": 10.0}]
        exp = correlated_exposure(positions, "US-Northeast", 0.05, 100.0)
        # 0.05 (new) + 0.10 * 1.0 (same cluster) = 0.15
        assert exp == pytest.approx(0.15)

    def test_distant_cluster_minimal(self):
        positions = [{"cluster": "Europe", "size_usd": 10.0}]
        exp = correlated_exposure(positions, "US-Pacific", 0.05, 100.0)
        # 0.05 (new) + 0.10 * 0.1 (distant) = 0.06
        assert exp == pytest.approx(0.06)
