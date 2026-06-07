"""AIFS ENS sampled-2t local-day extraction for replacement shadow research."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Iterable

from src.data.forecast_target_contract import compute_target_local_day_window_utc


SOURCE_ID = "ecmwf_aifs_ens"
PRODUCT_ID = "ecmwf_aifs_ens_sampled_2t_6h_v1"
HIGH_DATA_VERSION = "ecmwf_aifs_ens_sampled_2t_6h_local_calendar_day_max"
LOW_DATA_VERSION = "ecmwf_aifs_ens_sampled_2t_6h_local_calendar_day_min"
PHYSICAL_QUANTITY = "2t_sampled_local_calendar_day"
AGGREGATION_WINDOW_POLICY = "sampled_2t_6h_local_calendar_day"
SAMPLE_INTERVAL_HOURS = 6
UTC = timezone.utc
_FORBIDDEN_TRANSCRIPT_ALIAS = "h" + "3"


def _to_utc(value: datetime, *, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value.astimezone(UTC)


def _temperature_to_c(value: float, unit: str) -> float:
    temperature = float(value)
    if not math.isfinite(temperature):
        raise ValueError("temperature must be finite")
    normalized = unit.upper()
    if normalized in {"C", "CELSIUS"}:
        return temperature
    if normalized in {"K", "KELVIN"}:
        return temperature - 273.15
    if normalized in {"F", "FAHRENHEIT"}:
        return (temperature - 32.0) * 5.0 / 9.0
    raise ValueError("temperature_unit must be C, K, or F")


@dataclass(frozen=True)
class AifsInstantSample:
    member_id: str
    valid_time_utc: datetime
    temperature: float
    temperature_unit: str = "C"

    def __post_init__(self) -> None:
        if not self.member_id:
            raise ValueError("member_id must be set")
        object.__setattr__(self, "valid_time_utc", _to_utc(self.valid_time_utc, field_name="valid_time_utc"))
        object.__setattr__(self, "temperature", _temperature_to_c(self.temperature, self.temperature_unit))
        object.__setattr__(self, "temperature_unit", "C")


@dataclass(frozen=True)
class AifsMemberLocalDayExtrema:
    member_id: str
    high_c: float
    low_c: float
    sample_count: int
    contributing_valid_times_utc: tuple[datetime, ...]

    def __post_init__(self) -> None:
        if self.sample_count <= 0:
            raise ValueError("sample_count must be positive")
        if self.high_c < self.low_c:
            raise ValueError("high_c cannot be below low_c")
        if len(self.contributing_valid_times_utc) != self.sample_count:
            raise ValueError("contributing_valid_times_utc length must equal sample_count")


@dataclass(frozen=True)
class AifsSampledLocalDayExtraction:
    city_timezone: str
    target_local_date: date
    source_cycle_time: datetime | None
    target_window_start_utc: datetime
    target_window_end_utc: datetime
    members: tuple[AifsMemberLocalDayExtrema, ...]
    source_id: str = SOURCE_ID
    product_id: str = PRODUCT_ID
    high_data_version: str = HIGH_DATA_VERSION
    low_data_version: str = LOW_DATA_VERSION
    physical_quantity: str = PHYSICAL_QUANTITY
    aggregation_window_policy: str = AGGREGATION_WINDOW_POLICY
    trade_authority_status: str = "SHADOW_ONLY"
    training_allowed: bool = False

    def __post_init__(self) -> None:
        for field_name, value in (
            ("source_id", self.source_id),
            ("product_id", self.product_id),
            ("high_data_version", self.high_data_version),
            ("low_data_version", self.low_data_version),
        ):
            if _FORBIDDEN_TRANSCRIPT_ALIAS in value.lower():
                raise ValueError(f"{field_name} must use the full product identity")
        if "mx2t" in self.high_data_version or "mn2t" in self.low_data_version:
            raise ValueError("AIFS sampled-2t extraction cannot use period-extrema data_versions")
        if self.trade_authority_status != "SHADOW_ONLY" or self.training_allowed:
            raise ValueError("AIFS sampled-2t extraction is shadow-only until promoted by evidence")
        if not self.members:
            raise ValueError("AIFS sampled extraction requires at least one member")
        object.__setattr__(self, "target_window_start_utc", _to_utc(self.target_window_start_utc, field_name="target_window_start_utc"))
        object.__setattr__(self, "target_window_end_utc", _to_utc(self.target_window_end_utc, field_name="target_window_end_utc"))
        if self.source_cycle_time is not None:
            object.__setattr__(self, "source_cycle_time", _to_utc(self.source_cycle_time, field_name="source_cycle_time"))


def expected_aifs_sample_steps_for_local_day(
    *,
    source_cycle_time: datetime,
    city_timezone: str,
    target_local_date: date,
    interval_hours: int = SAMPLE_INTERVAL_HOURS,
    max_step_hours: int = 360,
) -> tuple[int, ...]:
    if interval_hours <= 0:
        raise ValueError("interval_hours must be positive")
    if max_step_hours <= 0:
        raise ValueError("max_step_hours must be positive")
    cycle = _to_utc(source_cycle_time, field_name="source_cycle_time")
    window = compute_target_local_day_window_utc(
        city_timezone=city_timezone,
        target_local_date=target_local_date,
    )
    steps: list[int] = []
    for step_hour in range(0, max_step_hours + interval_hours, interval_hours):
        valid_time = cycle + timedelta(hours=step_hour)
        if window.start_utc <= valid_time < window.end_utc:
            steps.append(step_hour)
    return tuple(steps)


def extract_aifs_sampled_2t_localday(
    samples: Iterable[AifsInstantSample],
    *,
    city_timezone: str,
    target_local_date: date,
    source_cycle_time: datetime | None = None,
    min_samples_per_member: int = 1,
) -> AifsSampledLocalDayExtraction:
    if min_samples_per_member <= 0:
        raise ValueError("min_samples_per_member must be positive")
    window = compute_target_local_day_window_utc(
        city_timezone=city_timezone,
        target_local_date=target_local_date,
    )
    grouped: dict[str, list[AifsInstantSample]] = {}
    for sample in samples:
        if not isinstance(sample, AifsInstantSample):
            raise TypeError("samples must contain AifsInstantSample objects")
        if window.start_utc <= sample.valid_time_utc < window.end_utc:
            grouped.setdefault(sample.member_id, []).append(sample)

    if not grouped:
        raise ValueError("no AIFS 2t samples fall inside the target local day")

    members: list[AifsMemberLocalDayExtrema] = []
    for member_id in sorted(grouped):
        member_samples = sorted(grouped[member_id], key=lambda item: item.valid_time_utc)
        if len(member_samples) < min_samples_per_member:
            raise ValueError(f"member {member_id!r} has insufficient sampled-2t coverage")
        temperatures = [sample.temperature for sample in member_samples]
        members.append(
            AifsMemberLocalDayExtrema(
                member_id=member_id,
                high_c=max(temperatures),
                low_c=min(temperatures),
                sample_count=len(member_samples),
                contributing_valid_times_utc=tuple(sample.valid_time_utc for sample in member_samples),
            )
        )

    return AifsSampledLocalDayExtraction(
        city_timezone=city_timezone,
        target_local_date=target_local_date,
        source_cycle_time=source_cycle_time,
        target_window_start_utc=window.start_utc,
        target_window_end_utc=window.end_utc,
        members=tuple(members),
    )
