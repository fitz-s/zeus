"""Precision metadata guardrails for Open-Meteo ECMWF IFS 9km anchors."""

from __future__ import annotations

import math
import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Literal
from typing import Mapping


PASS_STATUS = "PASS"
BLOCK_STATUS = "BLOCK"
REVIEW_REQUIRED_STATUS = "REVIEW_REQUIRED"
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
    source_geometry_proof: Mapping[str, object] | None = None

    def __post_init__(self) -> None:
        for field_name in ("city", "station_id", "native_grid", "delivery_grid_resolution", "interpolation_method", "endpoint_mode", "timezone_name", "temperature_unit", "city_class", "station_mapping_policy"):
            if not str(getattr(self, field_name)).strip():
                raise ValueError(f"{field_name} is required")
        for field_name in ("city_lat", "station_lat", "requested_lat", "nearest_grid_lat"):
            value = float(getattr(self, field_name))
            if not math.isfinite(value) or not -90.0 <= value <= 90.0:
                raise ValueError(f"{field_name} must be in [-90, 90]")
        for field_name in ("city_lon", "station_lon", "requested_lon", "nearest_grid_lon"):
            value = float(getattr(self, field_name))
            if not math.isfinite(value) or not -180.0 <= value <= 180.0:
                raise ValueError(f"{field_name} must be in [-180, 180]")
        if not math.isfinite(self.nearest_grid_distance_km) or self.nearest_grid_distance_km < 0.0:
            raise ValueError("nearest_grid_distance_km must be non-negative")
        if self.requested_coordinate_precision_decimals < 0:
            raise ValueError("requested_coordinate_precision_decimals must be non-negative")
        if not math.isfinite(self.anchor_sigma_c) or self.anchor_sigma_c <= 0.0:
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
    def passable_for_live_materialization(self) -> bool:
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


def grid_surface_elevation_m(proof: Mapping[str, object]) -> float:
    """Surface height of the provider's selected cell.

    HSURF marks a sea cell with the -999 sentinel, which is a class, not a
    height: a sea cell's surface is sea level (the provider applies no lapse
    correction to it either).
    """
    return 0.0 if proof["cell_is_sea"] else float(proof["raw_grid_elevation_m"])


def geometry_proof_authenticity_reason(
    metadata: OpenMeteoIfs9PrecisionMetadata,
    *,
    raw_payload_bytes: bytes | None = None,
    decision_at: datetime | str | None = None,
    station_ground_evidence: Mapping[str, object] | None = None,
) -> str | None:
    """Validate the provider's actual cell against the local surface and station.

    A precision JSON's self-assertion is not evidence. The request builder also
    supplies exact raw response bytes so the recorded response identity cannot
    be rebound to a different provider answer.
    """
    proof = metadata.source_geometry_proof
    if not isinstance(proof, Mapping) or proof.get("revision") != "openmeteo_ifs9_o1280_source_cell_v1":
        return "OM9_SOURCE_GEOMETRY_PROOF_MISSING"
    raw_sha = proof.get("raw_payload_sha256")
    if not isinstance(raw_sha, str) or len(raw_sha) != 64:
        return "OM9_SOURCE_RESPONSE_IDENTITY_MISSING"
    if raw_payload_bytes is None:
        return "OM9_SOURCE_RESPONSE_BYTES_MISSING"
    if hashlib.sha256(raw_payload_bytes).hexdigest() != raw_sha:
        return "OM9_SOURCE_RESPONSE_IDENTITY_MISMATCH"
    if decision_at is None:
        return "OM9_SOURCE_GEOMETRY_DECISION_CUT_REQUIRED"
    try:
        response = json.loads(raw_payload_bytes)
        if not isinstance(response, Mapping):
            return "OM9_SOURCE_RESPONSE_GEOMETRY_UNAVAILABLE"
        response_lat = float(response["latitude"])
        response_lon = float(response["longitude"])
        response_dem = float(response["elevation"])
        if not all(math.isfinite(v) for v in (response_lat, response_lon, response_dem)):
            return "OM9_SOURCE_RESPONSE_GEOMETRY_UNAVAILABLE"
        if (
            abs(response_lat - metadata.nearest_grid_lat) > 1e-5
            or abs(response_lon - metadata.nearest_grid_lon) > 1e-5
            or abs(response_dem - float(proof["target_dem_elevation_m"])) > 1e-6
            or response.get("timezone") != metadata.timezone_name
        ):
            return "OM9_SOURCE_RESPONSE_GEOMETRY_MISMATCH"
        scope = response.get("_zeus_current_target_scope")
        if isinstance(scope, Mapping) and scope.get("city") != metadata.city:
            return "OM9_SOURCE_RESPONSE_GEOMETRY_MISMATCH"
        from src.data.openmeteo_ecmwf_ifs9_bucket_transport import (
            same_grid_cell, validate_source_cell_geometry_proof,
        )
        from src.config import cities_by_name, runtime_station_geometry_for_city, station_ground_source_artifact_ref

        city = cities_by_name.get(metadata.city)
        if city is None:
            return "OM9_STATION_SOURCE_UNAVAILABLE"
        station = runtime_station_geometry_for_city(city, effective_at=_to_utc(decision_at, field_name="decision_at"))
        if station["validity_reason"] is not None:
            return "OM9_STATION_SOURCE_INVALID"
        ground_facts = station.get("ground_facts")
        ground_verified = station.get("ground_status") == "VERIFIED"
        if station_ground_evidence is not None:
            from src.data.station_ground_evidence import (
                read_current_station_ground_evidence, read_frozen_station_ground_evidence,
            )
            frozen = read_frozen_station_ground_evidence(station_ground_evidence, decision_at=decision_at)
            current = None if frozen is None else read_current_station_ground_evidence(
                frozen["forecast_db"], city=metadata.city, decision_at=decision_at,
            )
            if frozen is None or current is None or current["facts"] != frozen["facts"]:
                return "OM9_STATION_GROUND_PROOF_UNPROVEN"
            ground_facts = frozen["facts"]
            ground_verified = True
        ground = proof.get("station_ground_proof")
        if (
            not ground_verified
            or not isinstance(ground, Mapping)
            or ground.get("revision") != "station_ground_roles_v1"
            or ground.get("status") != "VERIFIED"
            or ground.get("facts") != ground_facts
            or not isinstance(ground.get("facts"), Mapping)
        ):
            return "OM9_STATION_GROUND_PROOF_UNPROVEN"
        audit = ground.get("audit")
        if (
            not isinstance(audit, Mapping)
            or audit.get("artifact_ref") != station_ground_source_artifact_ref(
                source_kind=ground["facts"].get("source_kind"), station_id=ground["facts"].get("station_id"),
            )
            or not isinstance(audit.get("body_sha256"), str)
            or len(audit["body_sha256"]) != 64
            or any(char not in "0123456789abcdef" for char in audit["body_sha256"])
            or not isinstance(audit.get("checked_at"), str)
        ):
            return "OM9_STATION_GROUND_PROOF_UNPROVEN"
        try:
            if _to_utc(audit["checked_at"], field_name="station_ground_checked_at") > _to_utc(
                decision_at, field_name="decision_at",
            ):
                return "OM9_STATION_GROUND_PROOF_UNPROVEN"
        except (ValueError, TypeError):
            return "OM9_STATION_GROUND_PROOF_UNPROVEN"
        # Producer validation uses actual current official bytes. Frozen readers
        # use their own canonical entity at the independent certificate cutoff;
        # neither a later page nor a later physical fact rewrites that old cut.
        station_height = float(ground_facts["elevation_m"])
        station_lat = float(station["lat"])
        station_lon = float(station["lon"])
        if not all(math.isfinite(v) for v in (station_height, station_lat, station_lon)):
            return "OM9_STATION_SOURCE_INVALID"
        if (
            str(station["station_id"]) != metadata.station_id
            or abs(station_height - float(metadata.station_elevation_m)) > 1e-6
            or abs(station_lat - metadata.station_lat) > 1e-6
            or abs(station_lon - metadata.station_lon) > 1e-6
        ):
            return "OM9_STATION_SOURCE_IDENTITY_MISMATCH"
        # The whole-registry hash is audit provenance, not a frozen reader gate:
        # an unrelated city row may change while this station remains identical.
        registry_hash = proof.get("station_registry_sha256")
        if not isinstance(registry_hash, str) or len(registry_hash) != 64:
            return "OM9_STATION_SOURCE_AUDIT_MISSING"
        target_dem = float(proof["target_dem_elevation_m"])
        if not math.isfinite(target_dem):
            return "OM9_TARGET_DEM_INVALID"
        reason = validate_source_cell_geometry_proof(
            proof, latitude=response_lat, longitude=response_lon,
            target_elevation_m=target_dem,
            requested_latitude=metadata.requested_lat, requested_longitude=metadata.requested_lon,
            decision_at=decision_at,
        )
        if reason is not None:
            return "OM9_SOURCE_GEOMETRY_PROOF_MISMATCH" if reason.endswith("MISMATCH") else "OM9_SOURCE_GEOMETRY_PROOF_UNAVAILABLE"
        actual = proof
        if (
            not same_grid_cell(
                metadata.nearest_grid_lat, metadata.nearest_grid_lon,
                float(actual["selected_grid_lat"]), float(actual["selected_grid_lon"]),
            )
            or abs(metadata.grid_elevation_m - grid_surface_elevation_m(actual)) > 1e-6
            or metadata.land_sea_mask != ("sea" if actual["cell_is_sea"] else "land")
            or metadata.city_class != ("coastal" if actual["nearby_sea"] else "standard")
            or abs(metadata.nearest_grid_distance_km - _haversine_km(
                metadata.requested_lat, metadata.requested_lon,
                metadata.nearest_grid_lat, metadata.nearest_grid_lon,
            )) > 1e-4
        ):
            return "OM9_SOURCE_GEOMETRY_METADATA_MISMATCH"
    except (OSError, ValueError, TypeError, KeyError, AttributeError, ImportError):
        return "OM9_SOURCE_GEOMETRY_PROOF_UNAVAILABLE"
    return None


def evaluate_openmeteo_ecmwf_ifs9_precision_guard(
    metadata: OpenMeteoIfs9PrecisionMetadata,
    *, raw_payload_bytes: bytes | None = None,
    decision_at: datetime | str | None = None,
    station_ground_evidence: Mapping[str, object] | None = None,
) -> OpenMeteoIfs9PrecisionGuardResult:
    """Evaluate whether OM9 anchor metadata is safe enough for live materialization."""

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
    if station_policy not in {"settlement_station", "airport_settlement_station", "operator_verified_station", "settlement_station_reference"}:
        reasons.append("OM9_STATION_MAPPING_POLICY_REQUIRED")
    station_distance_km = _haversine_km(metadata.requested_lat, metadata.requested_lon, metadata.station_lat, metadata.station_lon)
    if metadata.requested_coordinate_precision_decimals < 4:
        reasons.append("OM9_REQUESTED_COORDINATE_PRECISION_TOO_LOW")
    if station_policy in {"settlement_station", "airport_settlement_station", "operator_verified_station", "settlement_station_reference"} and station_distance_km > 5.0:
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
    source_reason = geometry_proof_authenticity_reason(
        metadata, raw_payload_bytes=raw_payload_bytes, decision_at=decision_at,
        station_ground_evidence=station_ground_evidence,
    )
    if source_reason is not None:
        reasons.append(source_reason)

    high_risk_bucket = "standard"
    if city_class in {"coastal", "island", "peninsula", "mountain", "valley"}:
        high_risk_bucket = city_class
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
        "OM9_SOURCE_GEOMETRY_PROOF_MISSING",
        "OM9_SOURCE_RESPONSE_IDENTITY_MISSING",
        "OM9_SOURCE_RESPONSE_BYTES_MISSING",
        "OM9_SOURCE_RESPONSE_IDENTITY_MISMATCH",
        "OM9_SOURCE_GEOMETRY_DECISION_CUT_REQUIRED",
        "OM9_SOURCE_RESPONSE_GEOMETRY_UNAVAILABLE",
        "OM9_SOURCE_RESPONSE_GEOMETRY_MISMATCH",
        "OM9_STATION_SOURCE_AUDIT_MISSING",
        "OM9_STATION_SOURCE_UNAVAILABLE",
        "OM9_STATION_SOURCE_INVALID",
        "OM9_STATION_SOURCE_IDENTITY_MISMATCH",
        "OM9_STATION_GROUND_PROOF_UNPROVEN",
        "OM9_TARGET_DEM_INVALID",
        "OM9_SOURCE_GEOMETRY_PROOF_MISMATCH",
        "OM9_SOURCE_GEOMETRY_METADATA_MISMATCH",
        "OM9_SOURCE_GEOMETRY_PROOF_UNAVAILABLE",
    }
    reason_tuple = tuple(dict.fromkeys(reasons))
    if not reason_tuple:
        status = PASS_STATUS
        reason_tuple = ("OM9_PRECISION_METADATA_PASS",)
    elif any(reason in blocking_reasons for reason in reason_tuple):
        status = BLOCK_STATUS
    else:
        status = REVIEW_REQUIRED_STATUS
    return OpenMeteoIfs9PrecisionGuardResult(
        status=status,
        reason_codes=reason_tuple,
        metadata=metadata,
        elevation_delta_m=elevation_delta,
        high_risk_bucket=high_risk_bucket,
    )
