"""K2 physical-clock WU scheduler tests."""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from src.config import cities_by_name
from src.data.wu_scheduler import WuDailyScheduler, dispatch_wu_daily_collection


def test_scheduler_basic_construction():
    s = WuDailyScheduler()
    assert s is not None
    assert s._offset_hours == 4.0


def test_next_trigger_uses_local_peak_plus_offset_nyc():
    """NYC peak_hour ~15.8 -> local trigger ~19:48 EDT -> UTC ~23:48 or 00:48 next day."""
    s = WuDailyScheduler()
    nyc = cities_by_name["NYC"]
    # Use a specific reference time: July 15, 2025, 10:00 UTC (pre-trigger)
    ref = datetime(2025, 7, 15, 10, 0, tzinfo=timezone.utc)
    trigger = s.next_trigger_utc(nyc, reference_utc=ref)
    # Trigger must be after reference
    assert trigger > ref
    # Trigger should be in local time at expected hour
    tz = ZoneInfo(nyc.timezone)
    trigger_local = trigger.astimezone(tz)
    expected_hour = int(nyc.historical_peak_hour + 4) % 24
    assert trigger_local.hour == expected_hour


def test_next_trigger_advances_to_tomorrow_when_past():
    """If the reference is after today's trigger, next trigger is tomorrow."""
    s = WuDailyScheduler()
    nyc = cities_by_name["NYC"]
    # Reference AFTER today's trigger: July 16 at 12:00 UTC (well after local trigger)
    ref = datetime(2025, 7, 16, 12, 0, tzinfo=timezone.utc)
    trigger = s.next_trigger_utc(nyc, reference_utc=ref)
    assert trigger > ref


def test_dst_boundary_day_london():
    """London 2025-03-30 is spring-forward; trigger must still produce a valid UTC datetime."""
    s = WuDailyScheduler()
    london = cities_by_name["London"]
    ref = datetime(2025, 3, 30, 5, 0, tzinfo=timezone.utc)  # Pre-trigger
    trigger = s.next_trigger_utc(london, reference_utc=ref)
    # Must not raise and must be valid datetime
    assert isinstance(trigger, datetime)
    assert trigger.tzinfo is not None


def test_zero_coverage_cities_get_valid_trigger():
    """Previously zero-coverage cities (Asia/Oceania) all return valid triggers now."""
    s = WuDailyScheduler()
    now = datetime(2025, 7, 15, 0, 0, tzinfo=timezone.utc)
    for name in ["Auckland", "Beijing", "Busan", "Chengdu", "Chongqing",
                 "Jakarta", "Kuala Lumpur", "Singapore", "Taipei", "Wuhan"]:
        city = cities_by_name.get(name)
        assert city is not None, f"{name} missing from cities_by_name"
        trigger = s.next_trigger_utc(city, reference_utc=now)
        assert isinstance(trigger, datetime)


def test_should_collect_now_within_window():
    """should_collect_now returns True when now is within +-60 min of the trigger."""
    s = WuDailyScheduler()
    nyc = cities_by_name["NYC"]
    target_day = date(2025, 7, 15)
    trigger_utc = s.trigger_for_date(nyc, target_day)
    # Exactly at the trigger: True
    assert s.should_collect_now(nyc, now_utc=trigger_utc) is True
    # 30 min before: True (within window)
    assert s.should_collect_now(nyc, now_utc=trigger_utc - timedelta(minutes=30)) is True
    # 2 hours before: False
    assert s.should_collect_now(nyc, now_utc=trigger_utc - timedelta(hours=2)) is False


def test_dispatch_returns_cities_in_their_window():
    """dispatch_wu_daily_collection returns cities whose trigger is near now_utc.

    Over a full 24-hour walk every city must fire at least once (window coverage).
    """
    s = WuDailyScheduler()
    fired_cities: set[str] = set()
    base = datetime(2025, 7, 15, 0, 0, tzinfo=timezone.utc)
    for hour in range(24):
        now = base + timedelta(hours=hour)
        for name in dispatch_wu_daily_collection(s, now_utc=now):
            fired_cities.add(name)
    expected = set(cities_by_name.keys())
    missing = expected - fired_cities
    assert not missing, f"Cities never fired over 24h: {sorted(missing)}"


def test_main_wu_daily_job_uses_scheduler_not_fixed_cron():
    """src/main.py WU daily job must NOT be registered as fixed hour=12 cron."""
    main_path = Path(__file__).parent.parent / "src" / "main.py"
    if not main_path.exists():
        pytest.skip("src/main.py not present in this worktree")
    content = main_path.read_text()
    # The job registration must NOT contain hour=12, minute=0 together
    fixed_noon_pattern = re.compile(r'add_job\([^)]*hour=12[^)]*minute=0', re.DOTALL)
    forbidden = fixed_noon_pattern.search(content)
    assert not forbidden, (
        f"src/main.py still has fixed hour=12, minute=0 WU daily cron: "
        f"{forbidden.group() if forbidden else ''}"
    )
    # Must reference the new scheduler or dispatch function
    assert (
        "wu_scheduler" in content
        or "WuDailyScheduler" in content
        or "_wu_daily_dispatch" in content
        or "should_collect_now" in content
    ), "src/main.py does not reference the new K2 scheduler/dispatcher"
