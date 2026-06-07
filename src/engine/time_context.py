"""Helpers for decision-time lead semantics.

Target dates are city-local settlement dates, so lead calculations must be
anchored to a timezone-aware reference time rather than `date.today()`.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
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
    target_end_local = datetime.combine(target_day, time.min, tzinfo=tz) + timedelta(days=1)
    reference_local = reference.astimezone(tz)
    delta = target_end_local - reference_local
    return delta.total_seconds() / 3600.0


def city_local_date_at(city_timezone: str, reference_time: datetime | str | None = None) -> date:
    """Calendar date at ``reference_time`` in the city's settlement timezone."""

    reference = _coerce_datetime(reference_time)
    return reference.astimezone(ZoneInfo(city_timezone)).date()


def city_local_fetch_window(
    city_timezone: str,
    *,
    reference_time: datetime | str | None = None,
    days_back: int,
) -> tuple[date, date]:
    """Rolling inclusive source-fetch window in the city-local calendar."""

    if days_back < 0:
        raise ValueError("days_back must be non-negative")
    local_date = city_local_date_at(city_timezone, reference_time)
    return local_date - timedelta(days=days_back), local_date


def has_city_local_day_started(
    target_date: date | str,
    city_timezone: str,
    reference_time: datetime | str | None = None,
) -> bool:
    """Whether the city-local settlement day has started at ``reference_time``."""

    target_day = _coerce_target_date(target_date)
    reference = _coerce_datetime(reference_time).astimezone(timezone.utc)
    target_start_local = datetime.combine(target_day, time.min, tzinfo=ZoneInfo(city_timezone))
    return target_start_local.astimezone(timezone.utc) <= reference
