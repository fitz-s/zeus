"""Tests for data-driven city correlation. K3 revision: clusters == city names."""

import pytest

from src.strategy.correlation import get_correlation, correlated_exposure


class TestCorrelation:
    def test_same_cluster(self):
        # K3: cluster == city name; self-correlation is always 1.0
        assert get_correlation("NYC", "NYC") == 1.0

    def test_nearby_clusters(self):
        # Atlanta and San Francisco: moderate data-driven correlation (~0.43)
        c = get_correlation("Atlanta", "San Francisco")
        assert 0.2 <= c <= 0.9

    def test_distant_clusters(self):
        # Auckland (Southern Hemisphere) vs Tokyo: strong inverse seasonal correlation
        c = get_correlation("Auckland", "Tokyo")
        assert c <= 0.2

    def test_new_cluster_taxonomy_entries_are_wired(self):
        # K3: any two city names should return a float (matrix or haversine fallback)
        assert get_correlation("Denver", "Seattle") > 0.0
        assert get_correlation("Tokyo", "London") > 0.0

    def test_symmetric(self):
        assert get_correlation("NYC", "London") == \
               get_correlation("London", "NYC")


class TestCorrelatedExposure:
    def test_empty_portfolio(self):
        exp = correlated_exposure([], "NYC", 0.05, 100.0)
        assert exp == pytest.approx(0.05)  # Just the new position

    def test_same_cluster_increases(self):
        # K3: cluster == city name; NYC-NYC is self-correlation = 1.0
        positions = [{"cluster": "NYC", "size_usd": 10.0}]
        exp = correlated_exposure(positions, "NYC", 0.05, 100.0)
        # 0.05 (new) + 0.10 * 1.0 (same city) = 0.15
        assert exp == pytest.approx(0.15)

    def test_distant_cluster_minimal(self):
        # Atlanta-Jakarta: get_correlation ~= 0.10 (data-driven matrix)
        # 0.05 (new) + 0.10 * 0.1011 (Atlanta-Jakarta) ≈ 0.0601
        positions = [{"cluster": "Atlanta", "size_usd": 10.0}]
        exp = correlated_exposure(positions, "Jakarta", 0.05, 100.0)
        # Allow ±0.02 tolerance around 0.06 for rounding
        assert 0.04 <= exp <= 0.08
