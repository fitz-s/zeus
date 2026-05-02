# Created: 2026-05-02
# Last reused/audited: 2026-05-02
# Authority basis: docs/operations/task_2026-05-02_data_daemon_readiness/PLAN.md PR45a relationship-test contract pack.
"""PR45a data daemon readiness time and substrate relationship contracts.

These tests are executable contract antibodies. Later phases should route the
same assertions through production readiness builders/repos instead of changing
the relationship law encoded here.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone, tzinfo
from zoneinfo import ZoneInfo

import pytest

from src.contracts.ensemble_snapshot_provenance import (
    ECMWF_OPENDATA_HIGH_DATA_VERSION,
    ECMWF_OPENDATA_LOW_DATA_VERSION,
)


@dataclass(frozen=True)
class MetricIdentitySpec:
    temperature_metric: str
    physical_quantity: str
    observation_field: str
    data_version: str


@dataclass(frozen=True)
class ReadinessScopeSpec:
    city_id: str
    city_label: str
    city_timezone: str
    target_local_date: date
    metric_identity: MetricIdentitySpec

    def safety_key(self) -> tuple[str, str, date, str, str, str, str]:
        metric = self.metric_identity
        return (
            self.city_id,
            self.city_timezone,
            self.target_local_date,
            metric.temperature_metric,
            metric.physical_quantity,
            metric.observation_field,
            metric.data_version,
        )


HIGH_METRIC = MetricIdentitySpec(
    temperature_metric="high",
    physical_quantity="mx2t6_local_calendar_day_max",
    observation_field="high_temp",
    data_version=ECMWF_OPENDATA_HIGH_DATA_VERSION,
)
LOW_METRIC = MetricIdentitySpec(
    temperature_metric="low",
    physical_quantity="mn2t6_local_calendar_day_min",
    observation_field="low_temp",
    data_version=ECMWF_OPENDATA_LOW_DATA_VERSION,
)


def _scope_authorizes(fresh_scope: ReadinessScopeSpec, requested_scope: ReadinessScopeSpec) -> bool:
    return fresh_scope.safety_key() == requested_scope.safety_key()


def _local_day_utc_duration_hours(local_day: date, timezone_name: str) -> float:
    local_timezone = ZoneInfo(timezone_name)
    start_local = datetime.combine(local_day, time.min, tzinfo=local_timezone)
    next_start_local = datetime.combine(local_day + timedelta(days=1), time.min, tzinfo=local_timezone)
    return (
        next_start_local.astimezone(timezone.utc) - start_local.astimezone(timezone.utc)
    ).total_seconds() / 3600


def _local_day_wall_duration_hours(local_day: date, timezone_name: str) -> float:
    ZoneInfo(timezone_name)
    start_wall = datetime.combine(local_day, time.min)
    next_start_wall = datetime.combine(local_day + timedelta(days=1), time.min)
    return (next_start_wall - start_wall).total_seconds() / 3600


def _normalize_for_storage(timestamp: datetime, explicit_timezone: tzinfo | None = None) -> datetime:
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        if explicit_timezone is None:
            raise ValueError("naive timestamp requires explicit source timezone")
        timestamp = timestamp.replace(tzinfo=explicit_timezone)
    return timestamp.astimezone(timezone.utc)


def _all_persisted_timestamps_are_utc(*timestamps: datetime) -> bool:
    return all(timestamp.tzinfo is timezone.utc for timestamp in timestamps)


def test_fresh_scope_cannot_authorize_different_city_id_timezone_date_or_metric() -> None:
    fresh_scope = ReadinessScopeSpec(
        city_id="nyc_knyc",
        city_label="New York",
        city_timezone="America/New_York",
        target_local_date=date(2026, 5, 3),
        metric_identity=HIGH_METRIC,
    )

    assert not _scope_authorizes(
        fresh_scope,
        ReadinessScopeSpec(
            city_id="nyc_alt_station",
            city_label="New York",
            city_timezone="America/New_York",
            target_local_date=fresh_scope.target_local_date,
            metric_identity=HIGH_METRIC,
        ),
    )
    assert not _scope_authorizes(
        fresh_scope,
        ReadinessScopeSpec(
            city_id=fresh_scope.city_id,
            city_label="New York",
            city_timezone="America/Chicago",
            target_local_date=fresh_scope.target_local_date,
            metric_identity=HIGH_METRIC,
        ),
    )
    assert not _scope_authorizes(
        fresh_scope,
        ReadinessScopeSpec(
            city_id=fresh_scope.city_id,
            city_label="New York",
            city_timezone=fresh_scope.city_timezone,
            target_local_date=date(2026, 5, 4),
            metric_identity=HIGH_METRIC,
        ),
    )
    assert not _scope_authorizes(
        fresh_scope,
        ReadinessScopeSpec(
            city_id=fresh_scope.city_id,
            city_label="New York",
            city_timezone=fresh_scope.city_timezone,
            target_local_date=fresh_scope.target_local_date,
            metric_identity=LOW_METRIC,
        ),
    )


def test_utc_date_cannot_substitute_for_city_local_target_date() -> None:
    utc_timestamp = datetime(2024, 3, 10, 4, 30, tzinfo=timezone.utc)
    new_york_local_date = utc_timestamp.astimezone(ZoneInfo("America/New_York")).date()

    assert utc_timestamp.date() == date(2024, 3, 10)
    assert new_york_local_date == date(2024, 3, 9)
    assert utc_timestamp.date() != new_york_local_date


def test_dst_spring_fall_local_day_hour_counts_are_required() -> None:
    assert _local_day_utc_duration_hours(date(2024, 3, 10), "America/New_York") == 23
    assert _local_day_utc_duration_hours(date(2024, 11, 3), "America/New_York") == 25
    assert _local_day_utc_duration_hours(date(2024, 3, 10), "Asia/Tokyo") == 24

    assert _local_day_wall_duration_hours(date(2024, 3, 10), "America/New_York") == 24
    assert _local_day_wall_duration_hours(date(2024, 11, 3), "America/New_York") == 24


def test_naive_timestamp_ambiguity_blocks_live_readiness() -> None:
    naive_wall_time = datetime(2024, 3, 10, 4, 30)

    with pytest.raises(ValueError, match="explicit source timezone"):
        _normalize_for_storage(naive_wall_time)

    assumed_new_york = _normalize_for_storage(naive_wall_time, ZoneInfo("America/New_York"))
    assumed_utc = _normalize_for_storage(naive_wall_time, timezone.utc)

    assert assumed_new_york != assumed_utc
    assert _all_persisted_timestamps_are_utc(assumed_new_york, assumed_utc)


def test_source_health_fresh_cannot_authorize_live_without_source_run_and_coverage() -> None:
    source_health_fresh = True
    source_run_present = False
    matching_coverage_written = False

    live_authorized = source_health_fresh and source_run_present and matching_coverage_written

    assert not live_authorized


def test_data_coverage_written_cannot_authorize_live_without_source_run_and_release_provenance() -> None:
    data_coverage_written = True
    source_run_present = False
    release_provenance_present = False

    live_authorized = data_coverage_written and source_run_present and release_provenance_present

    assert not live_authorized
