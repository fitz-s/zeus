"""Forecast fetch plans keyed by future target-local-date coverage."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from src.config import City, EntryForecastConfig
from src.contracts.ensemble_snapshot_provenance import (
    ECMWF_OPENDATA_HIGH_DATA_VERSION,
    ECMWF_OPENDATA_LOW_DATA_VERSION,
)
from src.data.forecast_target_contract import ForecastTargetScope, build_forecast_target_scope

UTC = timezone.utc


@dataclass(frozen=True)
class ForecastFetchPlan:
    source_id: str
    track: str
    source_cycle_time: datetime
    release_calendar_key: str
    source_transport: str
    required_scopes: tuple[ForecastTargetScope, ...]
    required_step_hours: tuple[int, ...]
    max_required_step_hour: int
    expected_members: int
    safe_fetch_not_before: datetime | None
    live_authorization: bool
    reason_code: str | None = None


def metric_for_track(track: str) -> str:
    if track.startswith("mx2t6_high"):
        return "high"
    if track.startswith("mn2t6_low"):
        return "low"
    raise ValueError(f"unknown Open Data track for metric mapping: {track!r}")


def data_version_for_track(track: str) -> str:
    metric = metric_for_track(track)
    if metric == "high":
        return ECMWF_OPENDATA_HIGH_DATA_VERSION
    return ECMWF_OPENDATA_LOW_DATA_VERSION


def track_for_metric(config: EntryForecastConfig, temperature_metric: str) -> str:
    if temperature_metric == "high":
        return config.high_track
    if temperature_metric == "low":
        return config.low_track
    raise ValueError("temperature_metric must be high or low")


def warm_horizon_target_dates(
    *,
    now_utc: datetime,
    city_timezone: str,
    warm_horizon_days: int,
) -> tuple[date, ...]:
    if warm_horizon_days < 0:
        raise ValueError("warm_horizon_days must be non-negative")
    if now_utc.tzinfo is None or now_utc.utcoffset() is None:
        raise ValueError("now_utc must be timezone-aware")
    local_today = now_utc.astimezone(ZoneInfo(city_timezone)).date()
    return tuple(local_today + timedelta(days=offset) for offset in range(warm_horizon_days + 1))


def build_warm_horizon_scopes(
    *,
    cities: tuple[City, ...],
    track: str,
    source_cycle_time: datetime,
    now_utc: datetime,
    warm_horizon_days: int,
    market_refs: tuple[str, ...] = (),
) -> tuple[ForecastTargetScope, ...]:
    temperature_metric = metric_for_track(track)
    data_version = data_version_for_track(track)
    scopes: list[ForecastTargetScope] = []
    for city in cities:
        for target_local_date in warm_horizon_target_dates(
            now_utc=now_utc,
            city_timezone=city.timezone,
            warm_horizon_days=warm_horizon_days,
        ):
            scopes.append(
                build_forecast_target_scope(
                    city_id=city.name.upper().replace(" ", "_"),
                    city_name=city.name,
                    city_timezone=city.timezone,
                    target_local_date=target_local_date,
                    temperature_metric=temperature_metric,
                    source_cycle_time=source_cycle_time,
                    data_version=data_version,
                    market_refs=market_refs,
                )
            )
    return tuple(scopes)


def build_fetch_plan(
    *,
    source_id: str,
    track: str,
    source_cycle_time: datetime,
    release_calendar_key: str,
    source_transport: str,
    required_scopes: tuple[ForecastTargetScope, ...],
    expected_members: int,
    safe_fetch_not_before: datetime | None,
    live_authorization: bool,
    reason_code: str | None = None,
) -> ForecastFetchPlan:
    if not required_scopes:
        raise ValueError("ForecastFetchPlan requires at least one target scope")
    required_steps = tuple(sorted({step for scope in required_scopes for step in scope.required_step_hours}))
    if not required_steps:
        raise ValueError("ForecastFetchPlan requires at least one forecast step")
    return ForecastFetchPlan(
        source_id=source_id,
        track=track,
        source_cycle_time=source_cycle_time.astimezone(UTC),
        release_calendar_key=release_calendar_key,
        source_transport=source_transport,
        required_scopes=required_scopes,
        required_step_hours=required_steps,
        max_required_step_hour=max(required_steps),
        expected_members=expected_members,
        safe_fetch_not_before=safe_fetch_not_before,
        live_authorization=live_authorization,
        reason_code=reason_code,
    )
