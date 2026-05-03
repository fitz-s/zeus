# Created: 2026-05-03
# Last reused/audited: 2026-05-03
# Authority basis: docs/operations/task_2026-05-02_live_entry_data_contract/PLAN_v4.md Phase 5B ForecastFetchPlan contract.
"""ForecastFetchPlan relationship contract tests."""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from src.config import City, entry_forecast_config
from src.data.forecast_fetch_plan import (
    build_fetch_plan,
    build_warm_horizon_scopes,
    data_version_for_track,
    metric_for_track,
    track_for_metric,
    warm_horizon_target_dates,
)
from src.data.forecast_target_contract import build_forecast_target_scope

UTC = timezone.utc


def _utc(year: int, month: int, day: int, hour: int = 0) -> datetime:
    return datetime(year, month, day, hour, tzinfo=UTC)


def _city(name: str, timezone_name: str = "America/New_York") -> City:
    return City(
        name=name,
        lat=40.0,
        lon=-73.0,
        timezone=timezone_name,
        settlement_unit="F",
        cluster=name,
        wu_station="KXXX",
    )


def test_track_metric_mapping_is_strict() -> None:
    cfg = entry_forecast_config()

    assert track_for_metric(cfg, "high") == "mx2t6_high_full_horizon"
    assert track_for_metric(cfg, "low") == "mn2t6_low_full_horizon"
    assert metric_for_track("mx2t6_high_full_horizon") == "high"
    assert metric_for_track("mn2t6_low_full_horizon") == "low"
    assert data_version_for_track("mx2t6_high_full_horizon").startswith("ecmwf_opendata_mx2t6")

    with pytest.raises(ValueError, match="temperature_metric"):
        track_for_metric(cfg, "mean")
    with pytest.raises(ValueError, match="unknown Open Data track"):
        metric_for_track("2t_instant")


def test_warm_horizon_dates_are_city_local() -> None:
    dates = warm_horizon_target_dates(
        now_utc=_utc(2026, 5, 3, 1),
        city_timezone="America/New_York",
        warm_horizon_days=2,
    )

    assert dates == (date(2026, 5, 2), date(2026, 5, 3), date(2026, 5, 4))


def test_warm_horizon_scopes_drive_fetch_plan_when_no_market_yet() -> None:
    scopes = build_warm_horizon_scopes(
        cities=(_city("NYC"),),
        track="mx2t6_high_full_horizon",
        source_cycle_time=_utc(2026, 5, 3),
        now_utc=_utc(2026, 5, 3, 12),
        warm_horizon_days=2,
    )

    assert [scope.target_local_date for scope in scopes] == [
        date(2026, 5, 3),
        date(2026, 5, 4),
        date(2026, 5, 5),
    ]
    assert all(scope.temperature_metric == "high" for scope in scopes)
    assert all(scope.market_refs == () for scope in scopes)


def test_active_market_future_dates_drive_fetch_plan_steps() -> None:
    source_cycle_time = _utc(2026, 5, 3)
    nyc_dplus5 = build_forecast_target_scope(
        city_id="NYC",
        city_name="NYC",
        city_timezone="America/New_York",
        target_local_date=date(2026, 5, 8),
        temperature_metric="high",
        source_cycle_time=source_cycle_time,
        data_version=data_version_for_track("mx2t6_high_full_horizon"),
        market_refs=("condition-dplus5",),
    )
    tokyo_dplus2 = build_forecast_target_scope(
        city_id="TOKYO",
        city_name="Tokyo",
        city_timezone="Asia/Tokyo",
        target_local_date=date(2026, 5, 5),
        temperature_metric="high",
        source_cycle_time=source_cycle_time,
        data_version=data_version_for_track("mx2t6_high_full_horizon"),
        market_refs=("condition-dplus2",),
    )

    plan = build_fetch_plan(
        source_id="ecmwf_open_data",
        track="mx2t6_high_full_horizon",
        source_cycle_time=source_cycle_time,
        release_calendar_key="ecmwf_open_data_mx2t6_high:00z-full",
        source_transport="ensemble_snapshots_v2_db_reader",
        required_scopes=(nyc_dplus5, tokyo_dplus2),
        expected_members=51,
        safe_fetch_not_before=_utc(2026, 5, 3, 8),
        live_authorization=True,
    )

    assert plan.required_scopes == (nyc_dplus5, tokyo_dplus2)
    assert min(plan.required_step_hours) == min(tokyo_dplus2.required_step_hours)
    assert max(plan.required_step_hours) == max(nyc_dplus5.required_step_hours)
    assert plan.max_required_step_hour == max(plan.required_step_hours)
    assert plan.expected_members == 51


def test_fetch_plan_requires_target_scopes() -> None:
    with pytest.raises(ValueError, match="at least one target scope"):
        build_fetch_plan(
            source_id="ecmwf_open_data",
            track="mx2t6_high_full_horizon",
            source_cycle_time=_utc(2026, 5, 3),
            release_calendar_key="ecmwf_open_data_mx2t6_high:00z-full",
            source_transport="ensemble_snapshots_v2_db_reader",
            required_scopes=(),
            expected_members=51,
            safe_fetch_not_before=None,
            live_authorization=False,
        )
