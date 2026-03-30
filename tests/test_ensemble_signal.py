"""Tests for EnsembleSignal.

Covers:
1. Happy path: standard 51-member ensemble
2. Edge cases from CLAUDE.md §4.2:
   - All 51 members in same bin → P_raw ≈ 1.0
   - Bimodal detection (25 at 40°F + 26 at 60°F)
   - Boundary sensitivity near bin edge
3. Failure modes: < 51 members rejected, empty hours
"""

from datetime import date

import numpy as np
import pytest

from src.config import City
from src.signal.ensemble_signal import EnsembleSignal, SIGMA_INSTRUMENT
from src.types import Bin


# Test city fixture
NYC = City(
    name="NYC", lat=40.7772, lon=-73.8726,
    timezone="America/New_York", cluster="US-Northeast",
    settlement_unit="F", wu_station="KLGA",
)

# Standard 11-bin structure for NYC winter
NYC_BINS = [
    Bin(low=None, high=32, label="32°F or below"),
    Bin(low=33, high=34, label="33-34°F"),
    Bin(low=35, high=36, label="35-36°F"),
    Bin(low=37, high=38, label="37-38°F"),
    Bin(low=39, high=40, label="39-40°F"),
    Bin(low=41, high=42, label="41-42°F"),
    Bin(low=43, high=44, label="43-44°F"),
    Bin(low=45, high=46, label="45-46°F"),
    Bin(low=47, high=48, label="47-48°F"),
    Bin(low=49, high=50, label="49-50°F"),
    Bin(low=51, high=None, label="51°F or higher"),
]


def _make_constant_members(value: float, n_members: int = 51, n_hours: int = 24):
    """Create members_hourly where every member peaks at `value`."""
    # Members all have a constant temperature across hours
    return np.full((n_members, n_hours), value, dtype=np.float64)


def _make_bimodal_members(v1: float, n1: int, v2: float, n2: int, n_hours: int = 24):
    """Create bimodal ensemble: n1 members at v1, n2 members at v2."""
    arr = np.zeros((n1 + n2, n_hours), dtype=np.float64)
    arr[:n1, :] = v1
    arr[n1:, :] = v2
    return arr


class TestEnsembleSignalInit:
    def test_rejects_less_than_51_members(self):
        """CLAUDE.md: ENS response < 51 members → reject entirely."""
        members = np.zeros((30, 24), dtype=np.float64)
        with pytest.raises(ValueError, match="Expected ≥51"):
            EnsembleSignal(members, NYC, date(2026, 1, 15))

    def test_accepts_exactly_51_members(self):
        members = _make_constant_members(40.0)
        ens = EnsembleSignal(members, NYC, date(2026, 1, 15))
        assert len(ens.member_maxes) == 51

    def test_member_maxes_correct(self):
        """Each member's max across hours should be extracted."""
        members = np.random.default_rng(42).uniform(30, 50, (51, 24))
        ens = EnsembleSignal(members, NYC, date(2026, 1, 15))
        # member_maxes should be the max of first 24 hours for each member
        expected = members[:, :24].max(axis=1)
        np.testing.assert_array_almost_equal(ens.member_maxes, expected)


class TestPRawVector:
    def test_all_members_same_bin(self):
        """All 51 members at 40°F → P_raw for 39-40 bin should be ≈1.0.

        With instrument noise σ=0.5°F, some probability leaks to adjacent bins,
        but the center bin should dominate.
        """
        np.random.seed(42)
        members = _make_constant_members(40.0)
        ens = EnsembleSignal(members, NYC, date(2026, 1, 15))
        p_raw = ens.p_raw_vector(NYC_BINS, n_mc=2000)

        assert p_raw.shape == (11,)
        assert pytest.approx(p_raw.sum(), abs=0.001) == 1.0

        # Bin index 4 is "39-40" → should have highest probability
        assert p_raw[4] > 0.5  # Most probability in the 39-40 bin
        # Adjacent bins get some noise spillover
        assert p_raw[4] + p_raw[3] + p_raw[5] > 0.95

    def test_sums_to_one(self):
        """P_raw vector must always sum to 1.0."""
        np.random.seed(42)
        members = np.random.default_rng(42).uniform(35, 50, (51, 24))
        ens = EnsembleSignal(members, NYC, date(2026, 1, 15))
        p_raw = ens.p_raw_vector(NYC_BINS, n_mc=1000)
        assert pytest.approx(p_raw.sum(), abs=0.001) == 1.0

    def test_no_negative_probabilities(self):
        """All probabilities must be ≥ 0."""
        np.random.seed(42)
        members = _make_constant_members(50.0)
        ens = EnsembleSignal(members, NYC, date(2026, 1, 15))
        p_raw = ens.p_raw_vector(NYC_BINS, n_mc=1000)
        assert np.all(p_raw >= 0)

    def test_shoulder_low_dominates_for_cold(self):
        """All members at 25°F → "32°F or below" bin should dominate."""
        np.random.seed(42)
        members = _make_constant_members(25.0)
        ens = EnsembleSignal(members, NYC, date(2026, 1, 15))
        p_raw = ens.p_raw_vector(NYC_BINS, n_mc=1000)
        assert p_raw[0] > 0.95  # "32 or below" bin

    def test_shoulder_high_dominates_for_hot(self):
        """All members at 60°F → "51°F or higher" bin should dominate."""
        np.random.seed(42)
        members = _make_constant_members(60.0)
        ens = EnsembleSignal(members, NYC, date(2026, 1, 15))
        p_raw = ens.p_raw_vector(NYC_BINS, n_mc=1000)
        assert p_raw[10] > 0.95  # "51 or higher" bin


class TestSpread:
    def test_zero_spread_for_constant(self):
        members = _make_constant_members(40.0)
        ens = EnsembleSignal(members, NYC, date(2026, 1, 15))
        assert ens.spread() == pytest.approx(0.0)

    def test_positive_spread_for_varied(self):
        members = np.random.default_rng(42).uniform(30, 50, (51, 24))
        ens = EnsembleSignal(members, NYC, date(2026, 1, 15))
        assert ens.spread() > 0


class TestBimodal:
    def test_unimodal_constant(self):
        """All members at same value → not bimodal."""
        members = _make_constant_members(40.0)
        ens = EnsembleSignal(members, NYC, date(2026, 1, 15))
        assert ens.is_bimodal() is False

    def test_bimodal_two_clusters(self):
        """25 members at 40°F + 26 at 60°F → bimodal."""
        members = _make_bimodal_members(40.0, 25, 60.0, 26)
        ens = EnsembleSignal(members, NYC, date(2026, 1, 15))
        assert ens.is_bimodal() is True

    def test_unimodal_tight_spread(self):
        """Members in narrow range → unimodal."""
        rng = np.random.default_rng(42)
        vals = rng.normal(40.0, 1.0, (51, 24))
        ens = EnsembleSignal(vals, NYC, date(2026, 1, 15))
        assert ens.is_bimodal() is False


class TestBoundarySensitivity:
    def test_all_members_at_boundary(self):
        """All members at 40.0°F, boundary at 40 → sensitivity should be 1.0."""
        members = _make_constant_members(40.0)
        ens = EnsembleSignal(members, NYC, date(2026, 1, 15))
        assert ens.boundary_sensitivity(40.0) == pytest.approx(1.0)

    def test_no_members_near_boundary(self):
        """All members at 40°F, boundary at 60 → sensitivity should be 0."""
        members = _make_constant_members(40.0)
        ens = EnsembleSignal(members, NYC, date(2026, 1, 15))
        assert ens.boundary_sensitivity(60.0) == pytest.approx(0.0)

    def test_partial_sensitivity(self):
        """Some members near boundary, some far."""
        # 10 members at 39.8 (within 0.5 of 40), 41 members at 50 (far)
        members = _make_bimodal_members(39.8, 10, 50.0, 41)
        ens = EnsembleSignal(members, NYC, date(2026, 1, 15))
        sensitivity = ens.boundary_sensitivity(40.0)
        assert sensitivity == pytest.approx(10.0 / 51.0, abs=0.01)
