"""Minimal forecast value objects shared by current materializer tests."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime


@dataclass(frozen=True)
class ForecastMemberLocalDayExtrema:
    member_id: str
    high_c: float
    low_c: float
    sample_count: int
    contributing_valid_times_utc: tuple[datetime, ...]


@dataclass(frozen=True)
class ForecastLocalDayExtraction:
    city_timezone: str
    target_local_date: date
    source_cycle_time: datetime | None
    target_window_start_utc: datetime
    target_window_end_utc: datetime
    members: tuple[ForecastMemberLocalDayExtrema, ...]
    source_id: str = "current_ensemble"
    product_id: str = "current_ensemble_local_day_v1"
    high_data_version: str = "current_ensemble_local_calendar_day_max"
    low_data_version: str = "current_ensemble_local_calendar_day_min"
    physical_quantity: str = "ensemble_local_calendar_day_extrema"
    aggregation_window_policy: str = "local_calendar_day"
    identity_decision_valid: bool = True
    identity_reason_codes: tuple[str, ...] = ()
    identity_decision_hash: str | None = None
    member_ids_hash: str | None = None
    step_hours_hash: str | None = None
    artifact_id: int | None = None
    raw_sha256: str | None = None
    source_product_id: str = "current_ensemble_local_day_v1"
    training_allowed: bool = False


@dataclass(frozen=True)
class ForecastTemperatureBin:
    bin_id: str
    lower_c: float | None = None
    upper_c: float | None = None
    center_c: float | None = None
    display_unit: str = "C"
    settlement_unit: str = "C"
    rounding_rule: str = "wmo_half_up"
