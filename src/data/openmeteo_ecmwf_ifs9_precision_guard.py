"""Precision metadata guardrails for Open-Meteo ECMWF IFS 9km anchors."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Literal


PASS_STATUS = "PASS"
BLOCK_STATUS = "BLOCK"
SHADOW_ONLY_STATUS = "SHADOW_ONLY"
EndpointMode = Literal["hourly_zeus_aggregated", "daily_vendor_aggregated"]


@dataclass(frozen=True)
class OpenMeteoIfs9PrecisionMetadata:
    city: str
    station_id: str
    city_lat: float
    city_lon: float
    station_lat: float
    station_lon: float
    requested_lat: float
    requested_lon: float
    requested_coordinate_precision_decimals: int
    nearest_grid_lat: float
    nearest_grid_lon: float
    nearest_grid_distance_km: float
    native_grid: str
    delivery_grid_resolution: str
    interpolation_method: str
    endpoint_mode: EndpointMode
    local_day_start_utc: datetime | str
    local_day_end_utc: datetime | str
    timezone_name: str
    target_local_date: date | str
    temperature_unit: str
    anchor_sigma_c: float
    grid_elevation_m: float | None
    station_elevation_m: float | None
    land_sea_mask: str | None
    city_class: str
    station_mapping_policy: str

    def __post_init__(self) -> None:
        for field_name in ("city", "station_id", "native_grid", "delivery_grid_resolution", "interpolation_method", "endpoint_mode", "timezone_name", "temperature_unit", "city_class", "station_mapping_policy"):
            if not str(getattr(self, field_name)).strip():
                raise ValueError(f"{field_name} is required")
        for field_name in ("city_lat", "station_lat", "requested_lat", "nearest_grid_lat"):
            value = float(getattr(self, field_name))
            if not -90.0 <= value <= 90.0:
                raise ValueError(f"{field_name} must be in [-90, 90]")
        for field_name in ("city_lon", "station_lon", "requested_lon", "nearest_grid_lon"):
            value = float(getattr(self, field_name))
            if not -180.0 <= value <= 180.0:
                raise ValueError(f"{field_name} must be in [-180, 180]")
        if self.nearest_grid_distance_km < 0.0:
            raise ValueError("nearest_grid_distance_km must be non-negative")
        if self.requested_coordinate_precision_decimals < 0:
            raise ValueError("requested_coordinate_precision_decimals must be non-negative")
        if self.anchor_sigma_c <= 0.0:
            raise ValueError("anchor_sigma_c must be positive")
        start = _to_utc(self.local_day_start_utc, field_name="local_day_start_utc")
        end = _to_utc(self.local_day_end_utc, field_name="local_day_end_utc")
        if end <= start:
            raise ValueError("local_day_end_utc must be after local_day_start_utc")
        if (end - start).total_seconds() not in {23 * 3600, 24 * 3600, 25 * 3600}:
            raise ValueError("local-day UTC window must be 23, 24, or 25 hours")


@dataclass(frozen=True)
class OpenMeteoIfs9PrecisionGuardResult:
    status: str
    reason_codes: tuple[str, ...]
    metadata: OpenMeteoIfs9PrecisionMetadata
    elevation_delta_m: float | None
    high_risk_bucket: str

    @property
    def passable_for_shadow_veto(self) -> bool:
        return self.status == PASS_STATUS


def _to_utc(value: datetime | str, *, field_name: str) -> datetime:
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def _normalized(value: str) -> str:
    return value.strip().lower().replace(" ", "_").replace("-", "_")


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius_km = 6371.0088
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)
    a = math.sin(d_phi / 2.0) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2.0) ** 2
    return radius_km * 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))


def evaluate_openmeteo_ecmwf_ifs9_precision_guard(metadata: OpenMeteoIfs9PrecisionMetadata) -> OpenMeteoIfs9PrecisionGuardResult:
    """Evaluate whether OM9 anchor metadata is safe enough for shadow/veto use."""

    reasons: list[str] = []
    interpolation = _normalized(metadata.interpolation_method)
    endpoint_mode = _normalized(metadata.endpoint_mode)
    city_class = _normalized(metadata.city_class)
    land_sea = _normalized(metadata.land_sea_mask or "")
    native_grid = _normalized(metadata.native_grid)
    delivery_grid = _normalized(metadata.delivery_grid_resolution)
    station_policy = _normalized(metadata.station_mapping_policy)
    unit = _normalized(metadata.temperature_unit).replace("°", "")

    if endpoint_mode != "hourly_zeus_aggregated":
        reasons.append("OM9_ENDPOINT_MUST_BE_HOURLY_ZEUS_AGGREGATED")
    if interpolation in {"", "unknown", "vendor_unknown"}:
        reasons.append("OM9_INTERPOLATION_METHOD_REQUIRED")
    if native_grid not in {"0p1_latlon", "o1280", "openmeteo_ecmwf_ifs_9km"}:
        reasons.append("OM9_NATIVE_GRID_UNVERIFIED")
    if delivery_grid not in {"0p1", "9km", "0.1", "0p1_latlon"}:
        reasons.append("OM9_DELIVERY_GRID_RESOLUTION_UNVERIFIED")
    if station_policy not in {"settlement_station", "airport_settlement_station", "operator_verified_station"}:
        reasons.append("OM9_STATION_MAPPING_POLICY_REQUIRED")
    station_distance_km = _haversine_km(metadata.requested_lat, metadata.requested_lon, metadata.station_lat, metadata.station_lon)
    if metadata.requested_coordinate_precision_decimals < 4:
        reasons.append("OM9_REQUESTED_COORDINATE_PRECISION_TOO_LOW")
    if station_policy in {"settlement_station", "airport_settlement_station", "operator_verified_station"} and station_distance_km > 5.0:
        reasons.append("OM9_REQUESTED_COORDINATE_NOT_SETTLEMENT_STATION")
    if unit not in {"c", "celsius"}:
        reasons.append("OM9_ANCHOR_UNIT_MUST_BE_CELSIUS")
    if metadata.grid_elevation_m is None or metadata.station_elevation_m is None:
        reasons.append("OM9_ELEVATION_METADATA_REQUIRED")
        elevation_delta = None
    else:
        elevation_delta = float(metadata.grid_elevation_m) - float(metadata.station_elevation_m)
        if abs(elevation_delta) > 250.0:
            reasons.append("OM9_ELEVATION_DELTA_HIGH")
    if not land_sea:
        reasons.append("OM9_LAND_SEA_MASK_REQUIRED")
    if metadata.nearest_grid_distance_km > 20.0:
        reasons.append("OM9_NEAREST_GRID_DISTANCE_HIGH")
    if metadata.anchor_sigma_c <= 0.0:
        reasons.append("OM9_ANCHOR_SIGMA_INVALID")

    high_risk_bucket = "standard"
    if city_class in {"coastal", "island", "peninsula", "mountain", "valley"}:
        high_risk_bucket = city_class
    if city_class in {"coastal", "island", "peninsula"} and land_sea not in {"land", "coastal_land"}:
        reasons.append("OM9_LAND_SEA_HIGH_RISK_FOR_CITY_CLASS")
    if city_class in {"mountain", "valley"} and (elevation_delta is None or abs(elevation_delta) > 100.0):
        reasons.append("OM9_TERRAIN_ELEVATION_REVIEW_REQUIRED")

    blocking_reasons = {
        "OM9_ENDPOINT_MUST_BE_HOURLY_ZEUS_AGGREGATED",
        "OM9_INTERPOLATION_METHOD_REQUIRED",
        "OM9_NATIVE_GRID_UNVERIFIED",
        "OM9_DELIVERY_GRID_RESOLUTION_UNVERIFIED",
        "OM9_STATION_MAPPING_POLICY_REQUIRED",
        "OM9_REQUESTED_COORDINATE_PRECISION_TOO_LOW",
        "OM9_REQUESTED_COORDINATE_NOT_SETTLEMENT_STATION",
        "OM9_ANCHOR_UNIT_MUST_BE_CELSIUS",
        "OM9_ELEVATION_METADATA_REQUIRED",
        "OM9_LAND_SEA_MASK_REQUIRED",
        "OM9_NEAREST_GRID_DISTANCE_HIGH",
        "OM9_ANCHOR_SIGMA_INVALID",
    }
    reason_tuple = tuple(dict.fromkeys(reasons))
    if not reason_tuple:
        status = PASS_STATUS
        reason_tuple = ("OM9_PRECISION_METADATA_PASS",)
    elif any(reason in blocking_reasons for reason in reason_tuple):
        status = BLOCK_STATUS
    else:
        status = SHADOW_ONLY_STATUS
    return OpenMeteoIfs9PrecisionGuardResult(
        status=status,
        reason_codes=reason_tuple,
        metadata=metadata,
        elevation_delta_m=elevation_delta,
        high_risk_bucket=high_risk_bucket,
    )
