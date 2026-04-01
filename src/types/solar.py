"""Solar/time-of-day semantic types for DST-aware trading logic."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from enum import Enum


class DaylightPhase(str, Enum):
    PRE_SUNRISE = "pre_sunrise"
    DAYLIGHT = "daylight"
    POST_SUNSET = "post_sunset"


@dataclass(frozen=True, slots=True)
class ObservationInstant:
    """DST-safe observation timestamp semantics for one weather reading."""

    city: str
    target_date: date
    source: str
    timezone: str
    local_timestamp: datetime
    utc_timestamp: datetime
    utc_offset_minutes: int
    dst_active: bool
    is_ambiguous_local_hour: bool = False
    is_missing_local_hour: bool = False
    time_basis: str = ""
    local_hour: float | None = None

    def __post_init__(self) -> None:
        if self.local_timestamp.tzinfo is None:
            raise ValueError("ObservationInstant requires timezone-aware local_timestamp.")
        if self.utc_timestamp.tzinfo is None:
            raise ValueError("ObservationInstant requires timezone-aware utc_timestamp.")
        if self.utc_timestamp.utcoffset() != timedelta(0):
            raise ValueError("utc_timestamp must be normalized to UTC.")

    @property
    def local_hour_fraction(self) -> float:
        if self.local_hour is not None:
            return float(self.local_hour)
        return (
            self.local_timestamp.hour
            + self.local_timestamp.minute / 60.0
            + self.local_timestamp.second / 3600.0
        )


@dataclass(frozen=True, slots=True)
class SolarDay:
    """DST-aware daily solar context for one city and target date."""

    city: str
    target_date: date
    timezone: str
    sunrise_local: datetime
    sunset_local: datetime
    sunrise_utc: datetime
    sunset_utc: datetime
    utc_offset_minutes: int
    dst_active: bool

    def __post_init__(self) -> None:
        if self.sunrise_local.tzinfo is None or self.sunset_local.tzinfo is None:
            raise ValueError("SolarDay requires timezone-aware local sunrise/sunset datetimes.")
        if self.sunrise_utc.tzinfo is None or self.sunset_utc.tzinfo is None:
            raise ValueError("SolarDay requires timezone-aware UTC sunrise/sunset datetimes.")
        if self.sunset_local <= self.sunrise_local:
            raise ValueError("sunset_local must be after sunrise_local")

    @property
    def sunrise_hour(self) -> float:
        return self.sunrise_local.hour + self.sunrise_local.minute / 60.0

    @property
    def sunset_hour(self) -> float:
        return self.sunset_local.hour + self.sunset_local.minute / 60.0

    @property
    def daylight_hours(self) -> float:
        return (self.sunset_local - self.sunrise_local).total_seconds() / 3600.0

    def local_hour_fraction(self, value: int | float) -> float:
        return float(value)

    def is_before_sunrise(self, local_hour: int | float) -> bool:
        return self.local_hour_fraction(local_hour) < self.sunrise_hour

    def is_after_sunset(self, local_hour: int | float) -> bool:
        return self.local_hour_fraction(local_hour) >= self.sunset_hour

    def daylight_progress(self, local_hour: int | float) -> float:
        """0 at sunrise, 1 at sunset, clipped outside the daylight window."""
        current = self.local_hour_fraction(local_hour)
        if current <= self.sunrise_hour:
            return 0.0
        if current >= self.sunset_hour:
            return 1.0
        return (current - self.sunrise_hour) / max(1e-9, self.sunset_hour - self.sunrise_hour)

    def phase(self, local_hour: int | float) -> DaylightPhase:
        current = self.local_hour_fraction(local_hour)
        if self.is_before_sunrise(current):
            return DaylightPhase.PRE_SUNRISE
        if self.is_after_sunset(current):
            return DaylightPhase.POST_SUNSET
        return DaylightPhase.DAYLIGHT


@dataclass(frozen=True, slots=True)
class Day0TemporalContext:
    """Unified time-semantic context for Day0 trading decisions."""

    city: str
    target_date: date
    timezone: str
    current_local_timestamp: datetime
    current_utc_timestamp: datetime
    current_local_hour: float
    solar_day: SolarDay
    observation_instant: ObservationInstant | None
    peak_hour: int | None
    post_peak_confidence: float
    daylight_progress: float
    utc_offset_minutes: int
    dst_active: bool
    is_ambiguous_local_hour: bool = False
    is_missing_local_hour: bool = False
    time_basis: str = ""
    confidence_source: str = ""

    def __post_init__(self) -> None:
        if self.current_local_timestamp.tzinfo is None:
            raise ValueError("current_local_timestamp must be timezone-aware.")
        if self.current_utc_timestamp.tzinfo is None:
            raise ValueError("current_utc_timestamp must be timezone-aware.")
        if not 0.0 <= float(self.post_peak_confidence) <= 1.0:
            raise ValueError("post_peak_confidence must be in [0, 1]")
        if not 0.0 <= float(self.daylight_progress) <= 1.0:
            raise ValueError("daylight_progress must be in [0, 1]")

    @property
    def phase(self) -> DaylightPhase:
        return self.solar_day.phase(self.current_local_hour)
