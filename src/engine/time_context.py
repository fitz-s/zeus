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
        return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


def _coerce_target_date(target_date: date | str) -> date:
    if isinstance(target_date, date):
        return target_date
    return date.fromisoformat(target_date)


def lead_days_to_target(
    target_date: date | str,
    city_timezone: str,
    reference_time: datetime | str | None = None,
) -> float:
    """Fractional days until the city-local target date begins."""

    target_day = _coerce_target_date(target_date)
    reference = _coerce_datetime(reference_time)
    tz = ZoneInfo(city_timezone)
    target_start_local = datetime.combine(target_day, time.min, tzinfo=tz)
    reference_local = reference.astimezone(tz)
    delta = target_start_local - reference_local
    return delta.total_seconds() / 86400.0


def lead_hours_to_target(
    target_date: date | str,
    city_timezone: str,
    reference_time: datetime | str | None = None,
) -> float:
    """Fractional hours until the city-local target date begins."""

    return lead_days_to_target(target_date, city_timezone, reference_time) * 24.0
