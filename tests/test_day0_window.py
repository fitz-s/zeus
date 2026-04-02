from datetime import date, datetime, timezone

import numpy as np

from src.signal.day0_window import remaining_member_maxes_for_day0


def test_day0_window_respects_target_local_date_for_tokyo():
    members = np.array([[10.0, 11.0, 12.0, 13.0]])
    times = [
        "2025-03-09T12:00:00+00:00",  # 21:00 JST on 03-09
        "2025-03-09T15:00:00+00:00",  # 00:00 JST on 03-10
        "2025-03-09T18:00:00+00:00",  # 03:00 JST on 03-10
        "2025-03-10T03:00:00+00:00",  # 12:00 JST on 03-10
    ]

    remaining, hours = remaining_member_maxes_for_day0(
        members,
        times,
        "Asia/Tokyo",
        date(2025, 3, 10),
        now=datetime(2025, 3, 9, 16, 0, tzinfo=timezone.utc),  # 01:00 JST on 03-10
    )

    assert hours == 2.0
    assert remaining.shape == (1,)
    assert remaining[0] == 13.0


def test_day0_window_respects_dst_transition_for_new_york():
    members = np.array([[30.0, 31.0, 32.0, 33.0]])
    times = [
        "2025-03-09T06:00:00+00:00",  # 01:00 EST
        "2025-03-09T07:00:00+00:00",  # 03:00 EDT
        "2025-03-09T08:00:00+00:00",  # 04:00 EDT
        "2025-03-09T12:00:00+00:00",  # 08:00 EDT
    ]

    remaining, hours = remaining_member_maxes_for_day0(
        members,
        times,
        "America/New_York",
        date(2025, 3, 9),
        now=datetime(2025, 3, 9, 7, 30, tzinfo=timezone.utc),  # 03:30 EDT
    )

    assert hours == 2.0
    assert remaining[0] == 33.0


def test_day0_window_returns_empty_when_target_day_has_no_remaining_hours():
    members = np.array([[30.0, 31.0, 32.0]])
    times = [
        "2025-03-09T06:00:00+00:00",  # 01:00 EST
        "2025-03-09T07:00:00+00:00",  # 03:00 EDT
        "2025-03-09T08:00:00+00:00",  # 04:00 EDT
    ]

    remaining, hours = remaining_member_maxes_for_day0(
        members,
        times,
        "America/New_York",
        date(2025, 3, 9),
        now=datetime(2025, 3, 9, 13, 0, tzinfo=timezone.utc),  # 09:00 EDT
    )

    assert hours == 0.0
    assert remaining.size == 0
