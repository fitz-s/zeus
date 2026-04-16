"""Helpers for decision-time lead semantics.

Target dates are city-local settlement dates, so lead calculations must be
anchored to a timezone-aware reference time rather than `date.today()`.
"""

from __future__ import annotations

from datetime import date, datetime, time, timezone
from zoneinfo import ZoneInfo


def _coerce_datetime(value: datetime | str | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise ValueError("reference_time must be tz-aware")
        return value
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("reference_time must be tz-aware")
    return parsed


def _coerce_target_date(target_date: date | str) -> date:
    if isinstance(target_date, date):
        return target_date
    return date.fromisoformat(target_date)


def lead_days_to_date_start(
    target_date: date | str,
    city_timezone: str,
    reference_time: datetime | str | None = None,
) -> float:
    """Fractional days until the city-local target date begins (00:00 local)."""

    target_day = _coerce_target_date(target_date)
    reference = _coerce_datetime(reference_time)
    tz = ZoneInfo(city_timezone)
    target_start_local = datetime.combine(target_day, time.min, tzinfo=tz)
    reference_local = reference.astimezone(tz)
    delta = target_start_local - reference_local
    return delta.total_seconds() / 86400.0


def lead_hours_to_date_start(
    target_date: date | str,
    city_timezone: str,
    reference_time: datetime | str | None = None,
) -> float:
    """Fractional hours until the city-local target date begins (00:00 local)."""

    return lead_days_to_date_start(target_date, city_timezone, reference_time) * 24.0


def lead_hours_to_settlement_close(
    target_date: date | str,
    city_timezone: str,
    reference_time: datetime | str | None = None,
) -> float:
    """Fractional hours until the city-local target date ends (24:00 local)."""
    
    target_day = _coerce_target_date(target_date)
    reference = _coerce_datetime(reference_time)
    tz = ZoneInfo(city_timezone)
    from datetime import timedelta
    target_end_local = datetime.combine(target_day, time.min, tzinfo=tz) + timedelta(days=1)
    reference_local = reference.astimezone(tz)
    delta = target_end_local - reference_local
    return delta.total_seconds() / 3600.0
