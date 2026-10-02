# Created: 2026-10-02
# Last reused/audited: 2026-10-02
# Authority basis: operator ruling 2026-10-02 (ended-day-held): the rolling capture-age
#   bound keeps a forecast of still-future hours the latest available. After local-day
#   end no newer forecast of those hours can exist, so the last pre-end capture is final
#   evidence: age = min(decision, day_end) - captured_at. Live 2026-10-02: Chongqing 10-02
#   vectors captured 15:56Z expired 18:56Z although the day ended 16:00Z.
"""Rolling Day0 vectors are aged at the close of the window they forecast."""

from __future__ import annotations

from datetime import datetime, timezone

from src.data.day0_hourly_vectors import (
    DAY0_ROLLING_CAPTURE_MAX_AGE_HOURS,
    Day0HourlyVector,
    select_ready_day0_hourly_vectors,
)

UTC = timezone.utc


def _vector(captured_at: str) -> Day0HourlyVector:
    times = tuple(f"2026-10-02T{hour:02d}:00" for hour in range(24))
    return Day0HourlyVector(
        model="icon_global", city="Chongqing", target_date="2026-10-02", timezone_name="Asia/Shanghai",
        captured_at=captured_at, times=times, temps_c=tuple(20.0 for _ in times))


def _ready(captured_at: str, now: str) -> bool:
    return bool(select_ready_day0_hourly_vectors(
        [_vector(captured_at)], target_date="2026-10-02", now=datetime.fromisoformat(now)))


def test_ended_day_capture_fresh_at_day_end_is_admitted():
    # Captured 15:56Z, day ends 16:00Z (age 4 min there); decision 19:10Z (3h14m wall age).
    assert DAY0_ROLLING_CAPTURE_MAX_AGE_HOURS == 3.0
    assert _ready("2026-10-02T15:56:33+00:00", "2026-10-02T19:10:00+00:00")
    assert _ready("2026-10-02T15:56:33+00:00", "2026-10-03T04:00:00+00:00")


def test_ended_day_capture_stale_at_day_end_stays_rejected():
    # Captured 12:30Z: 3.5 h old at the 16:00Z day end.
    assert not _ready("2026-10-02T12:30:00+00:00", "2026-10-02T19:10:00+00:00")


def test_open_day_is_judged_at_the_decision_clock():
    assert _ready("2026-10-02T12:30:00+00:00", "2026-10-02T15:00:00+00:00")
    assert not _ready("2026-10-02T11:30:00+00:00", "2026-10-02T15:00:00+00:00")


def test_capture_after_decision_is_still_rejected():
    assert not _ready("2026-10-02T19:30:00+00:00", "2026-10-02T19:10:00+00:00")
