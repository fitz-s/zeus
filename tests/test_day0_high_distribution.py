# Created: 2026-05-22
# Last reused or audited: 2026-05-22
# Authority basis: docs/operations/P0_FORECAST_EXTREMA_AUTHORITY_2026-05-22.md §Physical law + §PR-B
# Lifecycle: created=2026-05-22; last_reviewed=2026-05-22; last_reused=never
# Purpose: Unit tests for build_day0_high_distribution and p_vector — physical law invariants and p_vector normalization.
# Reuse: Run when build_day0_high_distribution, p_vector, or Day0HighNowcastSignal settlement logic changes.
"""Tests for Day0 HIGH physical distribution (PR-B).

Physical law under test:
    H_j = settle(max(observed_high_so_far, future_member_max_j))
    current_temp must NEVER lower the future max.
"""
import numpy as np
import pytest

from src.signal.day0_high_distribution import build_day0_high_distribution, p_vector
from src.signal.day0_high_nowcast_signal import Day0HighNowcastSignal
from src.types.market import Bin


# ---------------------------------------------------------------------------
# (a) Physical-law invariant: current_temp=18 must not pull down members=[27,28,29]
# ---------------------------------------------------------------------------

def test_nowcast_settlement_samples_current_temp_cannot_lower_future_max():
    """PR-B invariant: settlement_samples().min() >= 27 when members=[27,28,29].

    observed_high_so_far=18, current_temp=18, member_maxes_remaining=[27,28,29].
    Physical law: H_j = settle(max(18, member_j)) = settle(member_j) >= 27.
    The bug: old code blended current_temp (18) into the value, pulling samples
    down to ~19. After the fix, current_temp must NOT enter the value path.
    """
    signal = Day0HighNowcastSignal(
        observed_high_so_far=18.0,
        member_maxes_remaining=np.array([27.0, 28.0, 29.0]),
        current_temp=18.0,
        hours_remaining=1.0,
    )
    samples = signal.settlement_samples()
    assert samples.min() >= 27.0, (
        f"settlement_samples().min()={samples.min()} < 27 — current_temp "
        f"illegally lowered future member maxes. samples={samples!r}"
    )


def test_nowcast_settlement_samples_obs_floor_applied():
    """obs_floor forms a hard floor: samples must be >= observed_high_so_far."""
    signal = Day0HighNowcastSignal(
        observed_high_so_far=30.0,
        member_maxes_remaining=np.array([25.0, 28.0, 29.0]),  # all below obs_floor
        current_temp=22.0,
        hours_remaining=2.0,
    )
    samples = signal.settlement_samples()
    assert np.all(samples >= 30.0), (
        f"obs_floor=30 not applied — min={samples.min()}, samples={samples!r}"
    )


# ---------------------------------------------------------------------------
# (b) build_day0_high_distribution + p_vector sums to 1.0
# ---------------------------------------------------------------------------

def _make_bins_fahrenheit():
    """Valid F bin set: open-low shoulder + 2-wide range + open-high shoulder."""
    return [
        Bin(low=None, high=25, unit="F", label="25°F or below"),   # open-low shoulder
        Bin(low=26, high=27, unit="F", label="26-27°F"),           # range bin, width=2
        Bin(low=28, high=29, unit="F", label="28-29°F"),           # range bin, width=2
        Bin(low=30, high=None, unit="F", label="30°F or above"),   # open-high shoulder
    ]


def test_p_vector_sums_to_1():
    """build_day0_high_distribution p_vector sums to 1.0."""
    bins = _make_bins_fahrenheit()
    probs = p_vector(
        bins,
        observed_high_so_far=18.0,
        future_member_maxes=np.array([27.0, 28.0, 29.0]),
    )
    assert abs(probs.sum() - 1.0) < 1e-9, (
        f"p_vector does not sum to 1.0: sum={probs.sum()}, probs={probs!r}"
    )


def test_p_vector_nonnegative():
    """All bin probabilities must be non-negative."""
    bins = _make_bins_fahrenheit()
    probs = p_vector(
        bins,
        observed_high_so_far=18.0,
        future_member_maxes=np.array([27.0, 28.0, 29.0]),
    )
    assert np.all(probs >= 0.0), f"Negative probability: {probs!r}"


def test_p_vector_raises_on_empty_members():
    """ValueError for empty future_member_maxes."""
    bins = _make_bins_fahrenheit()
    with pytest.raises(ValueError, match="non-empty"):
        p_vector(bins, observed_high_so_far=18.0, future_member_maxes=np.array([]))


def test_build_day0_high_distribution_physical_max():
    """build_day0_high_distribution samples = max(obs, member) for each member."""
    samples = build_day0_high_distribution(
        observed_high_so_far=20.0,
        future_member_maxes=np.array([15.0, 20.0, 25.0, 30.0]),
    )
    expected = np.array([20.0, 20.0, 25.0, 30.0])
    np.testing.assert_array_equal(
        samples, expected,
        err_msg=f"Physical max not applied correctly: got {samples!r}"
    )
