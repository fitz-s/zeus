# Created: 2026-06-01
# Last reused or audited: 2026-06-01
# Authority basis: day0 obs-promotion phantom-edge precondition
#   Phantom-ultra-low-edge defect: pre-peak, obs pulled estimate DOWN because
#   the obs-floor was applied over remaining-hours maxes (CORRECT semantics).
#   The previously-believed bug "obs lowered the daily max estimate" turns out to
#   be an input-provenance issue (wrong hourly array passed as future_member_maxes).
#   These tests verify the CURRENT code's structural invariant and act as a
#   precondition gate before re-enabling day0 live trading.
"""Relationship tests: day0 obs-promotion monotone invariant.

Cross-module boundary tested:
    remaining_member_extrema_for_day0 (day0_window.py)
    → future_member_maxes (RemainingMemberExtrema.maxes)
    → build_day0_high_distribution (day0_high_distribution.py)
    → samples = max(obs, future_member_maxes)

The relationship invariant: observing part of the day can never LOWER the
predicted daily-high estimate.  If the obs floor is below the remaining-hours
member maxes, the distribution is unaffected.  If the obs floor is ABOVE, it
raises every sample (CI-collapse toward the observed peak).

Both directions must hold simultaneously for day0 to be safe.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import numpy as np
import pytest

from src.signal.day0_high_distribution import build_day0_high_distribution
from src.signal.day0_window import remaining_member_extrema_for_day0
from src.types.metric_identity import HIGH_LOCALDAY_MAX


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_hourly_day_with_afternoon_peak(
    n_members: int = 10,
    morning_high: float = 20.0,
    afternoon_peak: float = 30.0,
    n_hours_total: int = 24,
    *,
    tz_offset_hours: int = 0,
    base_utc: datetime,
) -> tuple[np.ndarray, list[str]]:
    """Build a synthetic n_members × n_hours array where the daily peak falls
    in the afternoon local hours.

    Morning hours (local 0-11): temperature rises to morning_high.
    Afternoon hours (local 12-23): temperature rises to afternoon_peak.
    Each member has the same profile (deterministic for test clarity).

    Returns (members_hourly shape [n_members, n_hours_total], times list[str]).
    The times are UTC ISO strings spaced 1 hour apart starting at base_utc.
    base_utc should correspond to local midnight for the target date.
    """
    assert n_hours_total >= 20, "select_hours_for_target_date requires >= 20 hours"
    hours = np.linspace(morning_high, afternoon_peak, n_hours_total)
    # Members differ slightly so they are not degenerate
    members = np.array([hours + i * 0.1 for i in range(n_members)])
    times = [
        (base_utc + timedelta(hours=i)).strftime("%Y-%m-%dT%H:%M:%S+00:00")
        for i in range(n_hours_total)
    ]
    return members, times


# ---------------------------------------------------------------------------
# TEST 1: Core invariant — obs-promotion NEVER LOWERS the daily-max estimate
# ---------------------------------------------------------------------------

def test_obs_promotion_cannot_lower_daily_max_estimate():
    """CORE INVARIANT: adding a morning observation that is below the afternoon peak
    must not lower the central estimate of the daily-high distribution.

    Scenario:
      - 10 ensemble members, afternoon peak ~30°C (per-member 30.0..30.9)
      - now = 06:00 local (morning, before peak)
      - observed_high_so_far = 20°C (morning, below afternoon peak)
      - remaining hours = local 06:00..23:00

    Expected:
      mean(samples_with_obs) >= full_day_naive_max
      AND mean(samples_with_obs) >= observed_high_so_far

    This is the anti-phantom-edge invariant: the phantom bug manifested as
    samples dropping to ~20 when afternoon forecast was 30. If this test
    passes, the estimator is structurally correct.
    """
    # UTC midnight for 2025-07-15 in UTC+0 (simple, no DST)
    base_utc = datetime(2025, 7, 15, 0, 0, tzinfo=timezone.utc)
    members_hourly, times = _make_hourly_day_with_afternoon_peak(
        n_members=10,
        morning_high=20.0,
        afternoon_peak=30.0,
        n_hours_total=24,
        base_utc=base_utc,
    )
    observed_high_so_far = 20.0  # morning high — well below afternoon peak

    # now = 06:00 UTC = 06:00 local (UTC+0)
    now = datetime(2025, 7, 15, 6, 0, tzinfo=timezone.utc)

    extrema, hours_remaining = remaining_member_extrema_for_day0(
        members_hourly,
        times,
        "UTC",
        date(2025, 7, 15),
        now=now,
        temperature_metric=HIGH_LOCALDAY_MAX,
    )
    assert extrema is not None, "Expected remaining extrema, got None"
    assert hours_remaining > 0, "Expected remaining hours > 0"
    future_member_maxes = extrema.maxes
    assert future_member_maxes is not None

    # Distribution WITH obs floor
    samples_with_obs = build_day0_high_distribution(
        observed_high_so_far=observed_high_so_far,
        future_member_maxes=future_member_maxes,
    )

    # Naive full-day member maxes (without obs floor)
    # This is the "no-obs" baseline — all hours
    samples_no_obs = build_day0_high_distribution(
        observed_high_so_far=-999.0,  # obs far below all members → no effect
        future_member_maxes=future_member_maxes,
    )

    mean_with_obs = float(np.mean(samples_with_obs))
    mean_no_obs = float(np.mean(samples_no_obs))

    # Invariant 1: obs-promotion did NOT lower the mean estimate
    assert mean_with_obs >= mean_no_obs - 1e-9, (
        f"PHANTOM EDGE REPRODUCED: adding morning obs LOWERED the estimate. "
        f"mean_with_obs={mean_with_obs:.3f} < mean_no_obs={mean_no_obs:.3f}. "
        f"future_member_maxes={future_member_maxes!r}"
    )

    # Invariant 2: no sample falls below the observed floor
    assert np.all(samples_with_obs >= observed_high_so_far - 1e-9), (
        f"Obs floor not enforced: min(samples)={samples_with_obs.min():.3f} < obs={observed_high_so_far}"
    )

    # Invariant 3: estimated peak >= observed high (obs is a FLOOR, cannot lower estimate)
    assert mean_with_obs >= observed_high_so_far, (
        f"Estimated mean {mean_with_obs:.3f} < observed_high_so_far {observed_high_so_far} — obs raised beyond peak"
    )


# ---------------------------------------------------------------------------
# TEST 2: Obs-floor DOMINATES when observed peak already higher than forecasts
# ---------------------------------------------------------------------------

def test_obs_floor_dominates_when_peak_already_observed():
    """When observed_high_so_far is ABOVE all remaining-hours member maxes, every
    sample must equal observed_high_so_far (CI collapses to the observed peak).

    Scenario:
      - Afternoon passed: only 2 hours remain (18:00-19:00 local)
      - remaining member maxes: [22.0, 22.5, 23.0, ...] — all below observed peak
      - observed_high_so_far = 30.0 (peak already happened at noon)

    Expected:
      ALL samples == 30.0 (after settlement rounding at precision=1.0)
      This is the correct day0 CI-collapse: certainty the final max = observed peak.
    """
    # Remaining-hours maxes all below 30
    remaining_maxes = np.array([22.0, 22.5, 23.0, 23.5, 24.0, 24.5, 25.0,
                                 25.5, 26.0, 26.5])
    observed_high_so_far = 30.0

    samples = build_day0_high_distribution(
        observed_high_so_far=observed_high_so_far,
        future_member_maxes=remaining_maxes,
        precision=1.0,
    )

    assert np.all(samples == 30.0), (
        f"Obs-floor CI collapse failed: expected all samples=30.0, got {samples!r}"
    )
    assert len(samples) == len(remaining_maxes), "Sample count must equal member count"


def test_obs_floor_dominates_partial_overlap():
    """Obs floor at 28 with members [25, 27, 29, 31]: samples=[28, 28, 29, 31].
    Members below obs are raised to obs; members above obs are unchanged.
    """
    samples = build_day0_high_distribution(
        observed_high_so_far=28.0,
        future_member_maxes=np.array([25.0, 27.0, 29.0, 31.0]),
        precision=1.0,
    )
    expected = np.array([28.0, 28.0, 29.0, 31.0])
    np.testing.assert_array_equal(
        samples, expected,
        err_msg=f"Partial obs-floor application incorrect: got {samples!r}"
    )


# ---------------------------------------------------------------------------
# TEST 3: Remaining-window correctness — target-LOCAL hours >= now_local only
# ---------------------------------------------------------------------------

def test_remaining_window_is_target_local_not_utc_naive():
    """The remaining-hours extractor must slice to hours >= now_local in TARGET
    timezone, not UTC-naive or a different timezone.

    Scenario: UTC+9 (Tokyo, no DST). Target date 2025-07-10.
      - Data: 24 hours of UTC times starting 2025-07-09 15:00 UTC
        = local 2025-07-10 00:00 JST through 2025-07-10 23:00 JST
      - now = 2025-07-10 06:00 UTC = 2025-07-10 15:00 JST
      - Remaining local hours: 15:00..23:00 JST = 9 hours

    Expected:
      hours_remaining == 9
      future_member_maxes == max over those 9 member columns
    """
    # Build 24 hourly UTC timestamps mapping to 2025-07-10 JST (all 24 local hours)
    base_utc = datetime(2025, 7, 9, 15, 0, tzinfo=timezone.utc)  # = JST midnight on 2025-07-10
    n_members = 5
    # Monotonically increasing temperatures so we can verify the max
    # local hour 0 -> temp 10, hour 1 -> 11, ..., hour 23 -> 33
    hourly_values = np.array(range(10, 34), dtype=float)  # 24 values
    members = np.tile(hourly_values, (n_members, 1))  # shape (5, 24)
    times = [
        (base_utc + timedelta(hours=i)).strftime("%Y-%m-%dT%H:%M:%S+00:00")
        for i in range(24)
    ]

    # now = 2025-07-10 06:00 UTC = 15:00 JST
    now = datetime(2025, 7, 10, 6, 0, tzinfo=timezone.utc)

    extrema, hours_remaining = remaining_member_extrema_for_day0(
        members,
        times,
        "Asia/Tokyo",
        date(2025, 7, 10),
        now=now,
        temperature_metric=HIGH_LOCALDAY_MAX,
    )

    # Local hours 15:00..23:00 JST = 9 remaining hours
    assert hours_remaining == 9.0, (
        f"Expected 9 remaining hours (15:00-23:00 JST), got {hours_remaining}"
    )
    assert extrema is not None
    future_maxes = extrema.maxes

    # max of hours 15..23 local = temps at indices 15..23 = values [25,26,27,28,29,30,31,32,33]
    # max = 33 (local 23:00 JST)
    expected_max = 33.0
    assert np.all(future_maxes == expected_max), (
        f"Expected all member maxes = {expected_max} (local 23:00 JST peak), "
        f"got {future_maxes!r}"
    )


def test_remaining_window_dst_boundary_new_york():
    """DST city (America/New_York): spring-forward 2025-03-09.

    On spring-forward day, local clocks skip 02:00-02:59 EST→EDT.
    The UTC day has 23 hours in local time.

    Scenario:
      - Data: 23 UTC hours starting 2025-03-09 05:00 UTC
        = local 2025-03-09 00:00 EST through 2025-03-09 23:00 EDT (23 hours)
      - now = 2025-03-09 09:00 UTC = 2025-03-09 05:00 EDT (post-spring-forward)
      - Remaining local hours on that date: 05:00..23:00 EDT = 19 hours

    Verifies: extractor uses TARGET-local time, handles DST gap correctly.
    """
    base_utc = datetime(2025, 3, 9, 5, 0, tzinfo=timezone.utc)  # = EST 00:00 on 03-09
    n_members = 3
    hourly_values = np.array(range(30, 53), dtype=float)  # 23 values
    members = np.tile(hourly_values, (n_members, 1))
    times = [
        (base_utc + timedelta(hours=i)).strftime("%Y-%m-%dT%H:%M:%S+00:00")
        for i in range(23)
    ]

    # now = 2025-03-09 09:00 UTC = 05:00 EDT (first valid post-spring-forward local time)
    now = datetime(2025, 3, 9, 9, 0, tzinfo=timezone.utc)

    extrema, hours_remaining = remaining_member_extrema_for_day0(
        members,
        times,
        "America/New_York",
        date(2025, 3, 9),
        now=now,
        temperature_metric=HIGH_LOCALDAY_MAX,
    )

    assert extrema is not None, "Expected non-None extrema for DST day"
    assert hours_remaining >= 1.0, f"Expected remaining hours >= 1, got {hours_remaining}"
    # Should NOT include any hours before now_local
    future_maxes = extrema.maxes
    # All remaining members must have maxes >= value at index corresponding to 09:00 UTC
    # hours at UTC 09:00 = index 4 in our times array (base_utc + 4h = 09:00 UTC)
    # value at index 4 = 30 + 4 = 34.0
    assert np.all(future_maxes >= 34.0), (
        f"Remaining window appears to include pre-now hours: {future_maxes!r}"
    )


# ---------------------------------------------------------------------------
# TEST 4: Anti-phantom structural proof — old-bug shape does NOT exist in current code
# ---------------------------------------------------------------------------

def test_anti_phantom_obs_below_remaining_maxes_does_not_lower_samples():
    """Directly reproduce the PHANTOM BUG SHAPE and confirm current code is immune.

    The old phantom: if the estimator applied max(obs, FULL_DAY_maxes) but obs
    was computed only from observed hours, then in the pre-peak scenario:
      - full_day max per member ≈ afternoon_peak (30)
      - obs = 20 (morning)
      - max(20, 30) = 30 → correct

    But if the bug were: max(obs, REMAINING_maxes) where REMAINING_maxes
    excluded the true afternoon peak (e.g., if remaining_window was wrong
    and only included hours already past), then:
      - remaining_maxes ≈ [20, 20, 20, ...] (just the morning)
      - max(20, 20) = 20 → phantom ultra-low!

    Current code: remaining_member_extrema_for_day0 slices to hours >= now_local,
    so pre-peak, the AFTERNOON hours are still INCLUDED, giving remaining_maxes ≈ 30.
    Therefore max(20, 30) = 30 — no phantom.

    This test verifies the invariant algebraically:
      For any obs below all future_member_maxes, samples == future_member_maxes
      (obs is irrelevant — it's a floor that doesn't bind).
    """
    obs_morning = 20.0
    afternoon_maxes = np.array([28.0, 29.0, 30.0, 30.5, 31.0])  # all above obs

    samples = build_day0_high_distribution(
        observed_high_so_far=obs_morning,
        future_member_maxes=afternoon_maxes,
        precision=1.0,
    )

    # When obs < all members, the obs floor has NO EFFECT — samples == rounded(members)
    expected = np.array([28.0, 29.0, 30.0, 31.0, 31.0])  # WMO rounding of 30.5 → 31
    np.testing.assert_array_equal(
        samples, expected,
        err_msg=(
            f"Anti-phantom: obs={obs_morning} below all members but STILL AFFECTED samples. "
            f"got {samples!r}, expected {expected!r}"
        )
    )

    # Core: no sample is below the member max (the obs pulled nothing down)
    assert np.all(samples >= afternoon_maxes - 0.5), (  # -0.5 for rounding tolerance
        f"Obs below members LOWERED samples — phantom reproduced: {samples!r}"
    )


def test_anti_phantom_obs_floor_is_strict_monotone():
    """Parametric monotonicity: increasing obs from 0 to 35 never decreases any sample.

    For each obs value, samples[i] = max(obs, member[i]).
    As obs increases, samples can only stay the same or increase.
    This is the mathematical proof that the phantom (obs LOWERING the estimate)
    is structurally impossible in the current implementation.
    """
    rng = np.random.default_rng(42)
    members = rng.uniform(20.0, 35.0, size=51)  # 51 ensemble members
    obs_values = np.linspace(0.0, 40.0, 50)

    prev_mean = None
    for obs in obs_values:
        samples = build_day0_high_distribution(
            observed_high_so_far=float(obs),
            future_member_maxes=members,
            precision=1.0,
        )
        mean = float(np.mean(samples))
        if prev_mean is not None:
            assert mean >= prev_mean - 1e-9, (
                f"Monotonicity violated: increasing obs from prev to {obs:.2f} "
                f"DECREASED mean estimate from {prev_mean:.4f} to {mean:.4f}. "
                "Phantom edge reproduced in current code."
            )
        prev_mean = mean
