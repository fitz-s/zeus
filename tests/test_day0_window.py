from datetime import date, datetime, timedelta, timezone

import numpy as np

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

    assert hours == 23.0
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
