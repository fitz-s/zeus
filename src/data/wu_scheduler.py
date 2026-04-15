"""K2 Physical-Clock WU Daily Scheduler.

Replaces the fixed UTC 12:00 trigger with a per-city local-time schedule.
Each city collects after its `historical_peak_hour + 4h` (local) so that the
full diurnal cycle has occurred before the fetch.

DST-aware via ZoneInfo. On DST boundary days the trigger shifts correctly.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from src.config import City


class WuDailyScheduler:
    """Computes per-city WU daily collection trigger times.

    Convention: each city's trigger is at `peak_hour + 4h` in local time.
    Example: NYC peak_hour=15.8 -> trigger at local 19:48 EDT -> UTC conversion
    via ZoneInfo (DST-aware).
    """

    # Offset after local peak hour to ensure the daily max has occurred
    PEAK_OFFSET_HOURS = 4.0

    def __init__(self, offset_hours: float = PEAK_OFFSET_HOURS):
        self._offset_hours = offset_hours

    def next_trigger_utc(self, city: City, reference_utc: Optional[datetime] = None) -> datetime:
        """Return the next UTC datetime at which `city` should be collected.

        If the today-local peak+offset has already passed, return tomorrow's.
        """
        if reference_utc is None:
            reference_utc = datetime.now(timezone.utc)
        tz = ZoneInfo(city.timezone)
        reference_local = reference_utc.astimezone(tz)
        # Compute trigger hour + minute from historical_peak_hour (float in [10, 20])
        trigger_hour_float = city.historical_peak_hour + self._offset_hours
        # Handle hour overflow (peak_hour + 4 > 24 -> wraps to next day)
        day_offset_days, trigger_hour_float = divmod(trigger_hour_float, 24)
        trigger_hour = int(trigger_hour_float)
        trigger_minute = int((trigger_hour_float - trigger_hour) * 60)
        # Target local datetime for TODAY
        today_local_date = reference_local.date() + timedelta(days=int(day_offset_days))
        trigger_local_today = datetime.combine(
            today_local_date,
            time(hour=trigger_hour, minute=trigger_minute),
            tzinfo=tz,
        )
        # Convert to UTC; if it's before reference, advance by one day
        trigger_utc = trigger_local_today.astimezone(timezone.utc)
        if trigger_utc <= reference_utc:
            next_local = trigger_local_today + timedelta(days=1)
            trigger_utc = next_local.astimezone(timezone.utc)
        return trigger_utc

    def trigger_for_date(self, city: City, target_date: date) -> datetime:
        """Return the UTC datetime at which the collection for `target_date` should fire."""
        tz = ZoneInfo(city.timezone)
        trigger_hour_float = city.historical_peak_hour + self._offset_hours
        day_offset_days, trigger_hour_float = divmod(trigger_hour_float, 24)
        trigger_hour = int(trigger_hour_float)
        trigger_minute = int((trigger_hour_float - trigger_hour) * 60)
        trigger_local = datetime.combine(
            target_date + timedelta(days=int(day_offset_days)),
            time(hour=trigger_hour, minute=trigger_minute),
            tzinfo=tz,
        )
        return trigger_local.astimezone(timezone.utc)

    def should_collect_now(self, city: City, now_utc: Optional[datetime] = None, window_minutes: int = 60) -> bool:
        """True if `now_utc` is within `window_minutes` of the city's target trigger for today.

        Used by the daemon's hourly dispatch: each hour, ask every city
        whether it's its collection time.
        """
        if now_utc is None:
            now_utc = datetime.now(timezone.utc)
        tz = ZoneInfo(city.timezone)
        now_local = now_utc.astimezone(tz)
        # Today's trigger in local
        trigger_hour_float = city.historical_peak_hour + self._offset_hours
        day_offset_days, trigger_hour_float = divmod(trigger_hour_float, 24)
        trigger_hour = int(trigger_hour_float)
        trigger_minute = int((trigger_hour_float - trigger_hour) * 60)
        trigger_local = datetime.combine(
            now_local.date() + timedelta(days=int(day_offset_days)),
            time(hour=trigger_hour, minute=trigger_minute),
            tzinfo=tz,
        )
        trigger_utc = trigger_local.astimezone(timezone.utc)
        delta = abs((now_utc - trigger_utc).total_seconds()) / 60.0
        return delta <= window_minutes


def dispatch_wu_daily_collection(
    scheduler: WuDailyScheduler, now_utc: Optional[datetime] = None
) -> list[str]:
    """Daemon-facing dispatcher: return list of city names whose collection should fire now.

    The main daemon calls this hourly and collects for each returned city.
    """
    from src.config import cities_by_name

    if now_utc is None:
        now_utc = datetime.now(timezone.utc)
    targets = []
    for name, city in cities_by_name.items():
        if scheduler.should_collect_now(city, now_utc):
            targets.append(name)
    return targets
