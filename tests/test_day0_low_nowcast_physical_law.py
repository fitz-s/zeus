# Created: 2026-06-17
# Authority basis: operator delta-package v2 (zeus_day0_low_physical_law_patch.diff).
#   RED-on-revert: locks the Day0 LOW physical settlement preimage. Reverting settlement_samples()
#   to the current_temp blend turns test 1 + 2 RED.
"""Day0 LOW physical-law contract: final_low = settle(min(observed_low_so_far, future_member_min))."""
from __future__ import annotations

import numpy as np

from src.signal.day0_low_distribution import build_day0_low_distribution
from src.signal.day0_low_nowcast_signal import Day0LowNowcastSignal


def test_day0_low_distribution_is_min_observed_and_future_member_min() -> None:
    samples = build_day0_low_distribution(
        observed_low_so_far=11.0,
        future_member_mins=np.asarray([12.0, 10.0, 14.0]),
        precision=1.0,
    )
    assert np.asarray(samples).tolist() == [11.0, 10.0, 11.0]


def test_day0_low_nowcast_current_temp_is_diagnostic_only() -> None:
    future_mins = np.asarray([12.0, 10.0, 14.0])
    hot_current = Day0LowNowcastSignal(
        observed_low_so_far=11.0,
        member_mins_remaining=future_mins,
        current_temp=30.0,
        hours_remaining=3.0,
        unit="C",
    ).settlement_samples()
    cold_current = Day0LowNowcastSignal(
        observed_low_so_far=11.0,
        member_mins_remaining=future_mins,
        current_temp=5.0,
        hours_remaining=3.0,
        unit="C",
    ).settlement_samples()
    assert np.asarray(hot_current).tolist() == [11.0, 10.0, 11.0]
    assert np.asarray(cold_current).tolist() == [11.0, 10.0, 11.0]
    assert np.array_equal(hot_current, cold_current)


def test_day0_low_nowcast_never_exceeds_observed_low_ceiling() -> None:
    future_mins = np.asarray([15.0, 16.0, 17.0])
    samples = Day0LowNowcastSignal(
        observed_low_so_far=11.0,
        member_mins_remaining=future_mins,
        current_temp=30.0,
        hours_remaining=3.0,
        unit="C",
    ).settlement_samples()
    assert float(np.max(samples)) <= 11.0
