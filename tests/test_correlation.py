"""Tests for heuristic correlation matrix."""

import pytest

from src.strategy.correlation import get_correlation, correlated_exposure


class TestCorrelation:
    def test_same_cluster(self):
        assert get_correlation("US-Northeast", "US-Northeast") == 1.0

    def test_nearby_clusters(self):
        c = get_correlation("US-Northeast", "US-GreatLakes")
        assert 0.4 <= c <= 0.8

    def test_distant_clusters(self):
        c = get_correlation("US-California-Coast", "Europe-Continental")
        assert c <= 0.2

    def test_new_cluster_taxonomy_entries_are_wired(self):
        assert get_correlation("US-Rockies", "US-Pacific-Northwest") > 0.0
        assert get_correlation("Asia-Northeast", "Europe-Maritime") > 0.0

    def test_symmetric(self):
        assert get_correlation("US-Northeast", "Europe-Maritime") == \
               get_correlation("Europe-Maritime", "US-Northeast")


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
        positions = [{"cluster": "Europe-Continental", "size_usd": 10.0}]
        exp = correlated_exposure(positions, "US-California-Coast", 0.05, 100.0)
        # 0.05 (new) + 0.10 * 0.1 (distant) = 0.06
        assert exp == pytest.approx(0.06)
