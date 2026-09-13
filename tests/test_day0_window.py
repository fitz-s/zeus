# Created: 2026-04-01
# Last reused/audited: 2026-09-13
# Authority basis: Day0 causal remaining-window selection and local-day/DST law.
# Lifecycle: created=2026-04-01; last_reviewed=2026-09-13; last_reused=2026-09-13
# Purpose: Lock causal target-day hourly selection, exact-boundary exclusion, and DST geometry.
# Reuse: Run when Day0 hourly conditioning or remaining-window selection changes.

from datetime import date, datetime, timedelta, timezone

import numpy as np
import pytest

from src.signal.day0_window import remaining_member_extrema_for_day0


def _maxes_for_day0(*args, **kwargs):
    """Shim for test ergonomics — returns (maxes_array, hours) from the new dataclass API.
    HIGH-track callers only (uses .maxes). Migrated from removed
    `remaining_member_maxes_for_day0` alias in Phase 7B.
    """
    extrema, hours = remaining_member_extrema_for_day0(*args, **kwargs)
    if extrema is None:
        import numpy as _np
        return _np.array([]), hours
    return extrema.maxes, hours


def test_day0_window_respects_target_local_date_for_tokyo():
    start = datetime(2025, 3, 9, 15, 0, tzinfo=timezone.utc)  # 00:00 JST on 03-10
    times = [(start + timedelta(hours=i)).isoformat() for i in range(24)]
    members = np.array([np.arange(10.0, 34.0)])

    remaining, hours = _maxes_for_day0(
        members,
        times,
        "Asia/Tokyo",
        date(2025, 3, 10),
        now=datetime(2025, 3, 9, 16, 0, tzinfo=timezone.utc),  # 01:00 JST on 03-10
    )

    assert hours == 22.0
    assert remaining.shape == (1,)
    assert remaining[0] == 33.0


def test_day0_window_respects_dst_transition_for_new_york():
    start = datetime(2025, 3, 9, 5, 0, tzinfo=timezone.utc)  # 00:00 EST on DST day
    times = [(start + timedelta(hours=i)).isoformat() for i in range(23)]
    members = np.array([np.arange(30.0, 53.0)])

    remaining, hours = _maxes_for_day0(
        members,
        times,
        "America/New_York",
        date(2025, 3, 9),
        now=datetime(2025, 3, 9, 7, 30, tzinfo=timezone.utc),  # 03:30 EDT
    )

    assert hours == 20.0
    assert remaining[0] == 52.0


def test_day0_window_returns_empty_when_target_day_has_no_remaining_hours():
    start = datetime(2025, 3, 9, 5, 0, tzinfo=timezone.utc)  # 00:00 EST on DST day
    times = [(start + timedelta(hours=i)).isoformat() for i in range(23)]
    members = np.array([np.arange(30.0, 53.0)])

    remaining, hours = _maxes_for_day0(
        members,
        times,
        "America/New_York",
        date(2025, 3, 9),
        now=datetime(2025, 3, 10, 4, 0, tzinfo=timezone.utc),  # after target local day
    )

    assert hours == 0.0
    assert remaining.size == 0


def test_day0_window_keeps_terminal_subhour_anchor_for_high_and_low():
    from src.types.metric_identity import HIGH_LOCALDAY_MAX, LOW_LOCALDAY_MIN

    start = datetime(2025, 3, 10, 0, 0, tzinfo=timezone.utc)
    times = [(start + timedelta(hours=i)).isoformat() for i in range(24)]
    members = np.array(
        [
            np.arange(0.0, 24.0),
            np.arange(100.0, 124.0),
        ]
    )
    boundary = datetime(2025, 3, 10, 23, 30, tzinfo=timezone.utc)

    high, high_hours = remaining_member_extrema_for_day0(
        members,
        times,
        "UTC",
        date(2025, 3, 10),
        now=boundary,
        temperature_metric=HIGH_LOCALDAY_MAX,
    )
    low, low_hours = remaining_member_extrema_for_day0(
        members,
        times,
        "UTC",
        date(2025, 3, 10),
        now=boundary,
        temperature_metric=LOW_LOCALDAY_MIN,
    )

    assert high is not None and high.maxes.tolist() == [23.0, 123.0]
    assert low is not None and low.mins.tolist() == [23.0, 123.0]
    assert high_hours == low_hours == 1.0


def test_day0_window_compares_fall_back_folds_by_utc_instant():
    from src.types.metric_identity import HIGH_LOCALDAY_MAX, LOW_LOCALDAY_MIN

    start = datetime(2026, 10, 24, 22, 0, tzinfo=timezone.utc)
    times = [(start + timedelta(hours=i)).isoformat() for i in range(25)]
    members = np.full((2, 25), 10.0)
    members[0, 3] = 99.0
    members[1, 3] = -99.0
    boundary = datetime(2026, 10, 25, 0, 30, tzinfo=timezone.utc)

    high, high_hours = remaining_member_extrema_for_day0(
        members,
        times,
        "Europe/Paris",
        date(2026, 10, 25),
        now=boundary,
        temperature_metric=HIGH_LOCALDAY_MAX,
    )
    low, low_hours = remaining_member_extrema_for_day0(
        members,
        times,
        "Europe/Paris",
        date(2026, 10, 25),
        now=boundary,
        temperature_metric=LOW_LOCALDAY_MIN,
    )

    assert high is not None and high.maxes.tolist() == [99.0, 10.0]
    assert low is not None and low.mins.tolist() == [10.0, -99.0]
    assert high_hours == low_hours == 22.0


@pytest.mark.parametrize("slope", [4.0, -4.0])
@pytest.mark.parametrize("unit_scale,unit_offset", [(1.0, 0.0), (1.8, 32.0)])
@pytest.mark.parametrize("minute", [1, 30, 59])
def test_current_state_perfect_ramp_has_no_innovation(slope, unit_scale, unit_offset, minute):
    from src.signal.day0_window import condition_day0_hourly_members_on_current_state

    start = datetime(2026, 9, 13, 12, tzinfo=timezone.utc)
    times = [(start + timedelta(hours=i)).isoformat() for i in range(3)]
    members = np.array([[10.0 + slope * i for i in range(3)]]) * unit_scale + unit_offset
    original = members.copy()
    observed = (10.0 + slope * minute / 60.0) * unit_scale + unit_offset
    result = condition_day0_hourly_members_on_current_state(
        members, times, observation_time=start + timedelta(minutes=minute),
        current_temp=observed, e_fold_hours=4.2,
    )

    assert result is not None
    conditioned, innovations = result
    assert innovations == pytest.approx([0.0], abs=1e-12)
    assert conditioned[:, 1:] == pytest.approx(original[:, 1:])
    assert conditioned[0, 0] == observed
    np.testing.assert_array_equal(members, original)


@pytest.mark.parametrize("offset", [-2.0, 2.0])
def test_current_state_subhour_preserves_real_residual_and_decay(offset):
    from src.signal.day0_window import condition_day0_hourly_members_on_current_state

    start = datetime(2026, 9, 13, 12, tzinfo=timezone.utc)
    times = [(start + timedelta(hours=i)).isoformat() for i in range(3)]
    members = np.array([[10.0, 14.0, 18.0], [14.0, 10.0, 6.0]])
    result = condition_day0_hourly_members_on_current_state(
        members, times, observation_time=start + timedelta(minutes=30),
        current_temp=12.0 + offset, e_fold_hours=4.2,
    )

    assert result is not None
    conditioned, innovations = result
    assert innovations == pytest.approx([offset, offset])
    for index, hours in ((1, 0.5), (2, 1.5)):
        assert conditioned[:, index] == pytest.approx(
            members[:, index] + offset * np.exp(-hours / 4.2)
        )


def test_current_state_terminal_subhour_retains_observed_extreme():
    from src.signal.day0_window import condition_day0_hourly_members_on_current_state
    from src.types.metric_identity import HIGH_LOCALDAY_MAX, LOW_LOCALDAY_MIN

    observed_at = datetime(2026, 9, 13, 23, 30, tzinfo=timezone.utc)
    times = [f"2026-09-13T{hour:02d}:00:00+00:00" for hour in range(24)]
    result = condition_day0_hourly_members_on_current_state(
        np.array([[10.0] * 24, [20.0] * 24]), times, observation_time=observed_at,
        current_temp=15.0, e_fold_hours=4.2,
    )
    assert result is not None
    for metric in (HIGH_LOCALDAY_MAX, LOW_LOCALDAY_MIN):
        extremes, hours = remaining_member_extrema_for_day0(
            result[0], times, "UTC", observed_at.date(), now=observed_at,
            temperature_metric=metric,
        )
        assert extremes is not None
        assert (extremes.mins if metric.is_low() else extremes.maxes).tolist() == [15.0, 15.0]
        assert hours == 1.0
