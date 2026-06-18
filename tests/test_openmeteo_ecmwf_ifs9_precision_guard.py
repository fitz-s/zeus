# Created: 2026-06-06
# Last reused/audited: 2026-06-06
# Lifecycle: created=2026-06-06; last_reviewed=2026-06-06; last_reused=2026-06-06
# Purpose: Protect Open-Meteo ECMWF IFS 9km deterministic anchor precision metadata gates.
# Reuse: Run before allowing OM9 anchor rows into replacement posterior readiness.
# Authority basis: Operator-directed Open-Meteo ECMWF IFS 9km + AIFS ENS sampled-2t shadow/veto integration.
"""Open-Meteo ECMWF IFS 9km precision guard tests."""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from src.data.openmeteo_ecmwf_ifs9_precision_guard import (
    OpenMeteoIfs9PrecisionMetadata,
    evaluate_openmeteo_ecmwf_ifs9_precision_guard,
)


UTC = timezone.utc


def _metadata(**overrides: object) -> OpenMeteoIfs9PrecisionMetadata:
    values = {
        "city": "Shanghai",
        "station_id": "ZSSS",
        "city_lat": 31.2304,
        "city_lon": 121.4737,
        "station_lat": 31.1979,
        "station_lon": 121.3363,
        "requested_lat": 31.1979,
        "requested_lon": 121.3363,
        "requested_coordinate_precision_decimals": 4,
        "nearest_grid_lat": 31.2,
        "nearest_grid_lon": 121.3,
        "nearest_grid_distance_km": 3.5,
        "native_grid": "openmeteo_ecmwf_ifs_9km",
        "delivery_grid_resolution": "0p1",
        "interpolation_method": "nearest_gridpoint",
        "endpoint_mode": "hourly_zeus_aggregated",
        "local_day_start_utc": datetime(2026, 6, 5, 16, tzinfo=UTC),
        "local_day_end_utc": datetime(2026, 6, 6, 16, tzinfo=UTC),
        "timezone_name": "Asia/Shanghai",
        "target_local_date": date(2026, 6, 6),
        "temperature_unit": "C",
        "anchor_sigma_c": 3.0,
        "grid_elevation_m": 4.0,
        "station_elevation_m": 3.0,
        "land_sea_mask": "land",
        "city_class": "flat_inland",
        "station_mapping_policy": "settlement_station",
    }
    values.update(overrides)
    return OpenMeteoIfs9PrecisionMetadata(**values)  # type: ignore[arg-type]


def test_openmeteo_ifs9_precision_guard_passes_complete_hourly_station_metadata() -> None:
    result = evaluate_openmeteo_ecmwf_ifs9_precision_guard(_metadata())

    assert result.status == "PASS"
    assert result.reason_codes == ("OM9_PRECISION_METADATA_PASS",)
    assert result.elevation_delta_m == pytest.approx(1.0)
    assert result.high_risk_bucket == "standard"
    assert result.passable_for_live_materialization is True


def test_openmeteo_ifs9_precision_guard_blocks_vendor_daily_or_unknown_interpolation() -> None:
    daily = evaluate_openmeteo_ecmwf_ifs9_precision_guard(_metadata(endpoint_mode="daily_vendor_aggregated"))
    assert daily.status == "BLOCK"
    assert "OM9_ENDPOINT_MUST_BE_HOURLY_ZEUS_AGGREGATED" in daily.reason_codes

    unknown = evaluate_openmeteo_ecmwf_ifs9_precision_guard(_metadata(interpolation_method="unknown"))
    assert unknown.status == "BLOCK"
    assert "OM9_INTERPOLATION_METHOD_REQUIRED" in unknown.reason_codes


def test_openmeteo_ifs9_precision_guard_blocks_missing_grid_identity_or_units() -> None:
    bad_grid = evaluate_openmeteo_ecmwf_ifs9_precision_guard(_metadata(native_grid="vendor_latest", delivery_grid_resolution="unknown"))
    assert bad_grid.status == "BLOCK"
    assert "OM9_NATIVE_GRID_UNVERIFIED" in bad_grid.reason_codes
    assert "OM9_DELIVERY_GRID_RESOLUTION_UNVERIFIED" in bad_grid.reason_codes

    fahrenheit = evaluate_openmeteo_ecmwf_ifs9_precision_guard(_metadata(temperature_unit="F"))
    assert fahrenheit.status == "BLOCK"
    assert "OM9_ANCHOR_UNIT_MUST_BE_CELSIUS" in fahrenheit.reason_codes


def test_openmeteo_ifs9_precision_guard_blocks_city_center_or_low_precision_requested_coordinates() -> None:
    city_center = evaluate_openmeteo_ecmwf_ifs9_precision_guard(
        _metadata(requested_lat=31.2304, requested_lon=121.4737)
    )
    assert city_center.status == "BLOCK"
    assert "OM9_REQUESTED_COORDINATE_NOT_SETTLEMENT_STATION" in city_center.reason_codes

    low_precision = evaluate_openmeteo_ecmwf_ifs9_precision_guard(
        _metadata(
            requested_lat=31.2,
            requested_lon=121.3,
            requested_coordinate_precision_decimals=1,
        )
    )
    assert low_precision.status == "BLOCK"
    assert "OM9_REQUESTED_COORDINATE_PRECISION_TOO_LOW" in low_precision.reason_codes


def test_openmeteo_ifs9_precision_guard_blocks_missing_elevation_landsea_or_far_gridpoint() -> None:
    missing = evaluate_openmeteo_ecmwf_ifs9_precision_guard(_metadata(grid_elevation_m=None, land_sea_mask=None))
    assert missing.status == "BLOCK"
    assert "OM9_ELEVATION_METADATA_REQUIRED" in missing.reason_codes
    assert "OM9_LAND_SEA_MASK_REQUIRED" in missing.reason_codes

    far = evaluate_openmeteo_ecmwf_ifs9_precision_guard(_metadata(nearest_grid_distance_km=25.0))
    assert far.status == "BLOCK"
    assert "OM9_NEAREST_GRID_DISTANCE_HIGH" in far.reason_codes


def test_openmeteo_ifs9_precision_guard_demotes_high_risk_coastal_and_terrain_buckets() -> None:
    coastal = evaluate_openmeteo_ecmwf_ifs9_precision_guard(_metadata(city_class="coastal", land_sea_mask="sea"))
    assert coastal.status == "REVIEW_REQUIRED"
    assert coastal.high_risk_bucket == "coastal"
    assert "OM9_LAND_SEA_HIGH_RISK_FOR_CITY_CLASS" in coastal.reason_codes
    assert coastal.passable_for_live_materialization is False

    mountain = evaluate_openmeteo_ecmwf_ifs9_precision_guard(
        _metadata(city_class="mountain", grid_elevation_m=300.0, station_elevation_m=120.0)
    )
    assert mountain.status == "REVIEW_REQUIRED"
    assert mountain.high_risk_bucket == "mountain"
    assert "OM9_TERRAIN_ELEVATION_REVIEW_REQUIRED" in mountain.reason_codes


def test_openmeteo_ifs9_precision_metadata_rejects_bad_local_day_window() -> None:
    with pytest.raises(ValueError, match="23, 24, or 25 hours"):
        _metadata(
            local_day_start_utc="2026-06-06T00:00:00+00:00",
            local_day_end_utc="2026-06-06T22:00:00+00:00",
        )

    dst_23h = evaluate_openmeteo_ecmwf_ifs9_precision_guard(
        _metadata(
            local_day_start_utc="2026-03-08T05:00:00+00:00",
            local_day_end_utc="2026-03-09T04:00:00+00:00",
            timezone_name="America/New_York",
            target_local_date="2026-03-08",
        )
    )
    assert dst_23h.status == "PASS"
