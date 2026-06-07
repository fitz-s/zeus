"""Run-pinned Open-Meteo ECMWF IFS 9km deterministic anchor contract."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from src.data.raw_forecast_artifact_manifest import RawForecastArtifactManifest


SINGLE_RUNS_FORECAST_URL = "https://single-runs-api.open-meteo.com/v1/forecast"
SOURCE_ID = "openmeteo_ecmwf_ifs_9km"
PRODUCT_ID = "openmeteo_ecmwf_ifs9_deterministic_anchor_v1"
HIGH_DATA_VERSION = "openmeteo_ecmwf_ifs9_anchor_localday_high"
LOW_DATA_VERSION = "openmeteo_ecmwf_ifs9_anchor_localday_low"
MODEL = "ecmwf_ifs"
HOURLY_VARIABLES = ("temperature_2m",)
DEFAULT_FORECAST_HOURS = 120
UTC = timezone.utc
_FORBIDDEN_TRANSCRIPT_ALIAS = "h" + "3"


def _coerce_cycle(value: datetime | str) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value.strip():
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    else:
        raise ValueError("run must be a timezone-aware UTC cycle datetime")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("run must be timezone-aware")
    run = parsed.astimezone(UTC).replace(minute=0, second=0, microsecond=0)
    if run.hour not in {0, 6, 12, 18}:
        raise ValueError("Open-Meteo ECMWF IFS 9km run must be one of 00/06/12/18 UTC")
    if parsed.astimezone(UTC) != run:
        raise ValueError("run must be exactly on a UTC cycle hour")
    return run


def _reject_transcript_alias(value: str, *, field_name: str) -> str:
    if _FORBIDDEN_TRANSCRIPT_ALIAS in value.lower():
        raise ValueError(f"{field_name} must use the full product identity, not transcript shorthand")
    return value


def _temperature_to_c(value: float, unit: str) -> float:
    temperature = float(value)
    if not math.isfinite(temperature):
        raise ValueError("temperature_2m values must be finite")
    normalized = unit.strip().lower().replace("°", "")
    if normalized in {"c", "celsius"}:
        return temperature
    if normalized in {"f", "fahrenheit"}:
        return (temperature - 32.0) * 5.0 / 9.0
    if normalized in {"k", "kelvin"}:
        return temperature - 273.15
    raise ValueError("temperature_2m unit must be C, F, or K")


def _parse_openmeteo_time(value: str, *, city_timezone: str) -> datetime:
    if not value:
        raise ValueError("Open-Meteo hourly time values must be non-empty")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    zone = ZoneInfo(city_timezone)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return parsed.replace(tzinfo=zone)
    return parsed.astimezone(zone)


@dataclass(frozen=True)
class OpenMeteoIfs9LocalDayAnchor:
    city_timezone: str
    target_local_date: date
    high_c: float
    low_c: float
    sample_count: int
    contributing_local_times: tuple[datetime, ...]
    contributing_valid_times_utc: tuple[datetime, ...]
    source_cycle_time: datetime | None = None
    source_id: str = SOURCE_ID
    product_id: str = PRODUCT_ID
    high_data_version: str = HIGH_DATA_VERSION
    low_data_version: str = LOW_DATA_VERSION
    model: str = MODEL
    measurement_policy: str = "hourly_temperature_2m_localday_anchor"
    trade_authority_status: str = "SHADOW_ONLY"
    training_allowed: bool = False

    def __post_init__(self) -> None:
        for field_name, value in (
            ("source_id", self.source_id),
            ("product_id", self.product_id),
            ("high_data_version", self.high_data_version),
            ("low_data_version", self.low_data_version),
        ):
            _reject_transcript_alias(value, field_name=field_name)
        if self.high_c < self.low_c:
            raise ValueError("high_c cannot be below low_c")
        if self.sample_count <= 0:
            raise ValueError("sample_count must be positive")
        if len(self.contributing_local_times) != self.sample_count:
            raise ValueError("contributing_local_times length must equal sample_count")
        if len(self.contributing_valid_times_utc) != self.sample_count:
            raise ValueError("contributing_valid_times_utc length must equal sample_count")
        if self.trade_authority_status != "SHADOW_ONLY" or self.training_allowed:
            raise ValueError("Open-Meteo ECMWF IFS 9km anchor is shadow-only until promoted by evidence")
        if self.source_cycle_time is not None:
            object.__setattr__(self, "source_cycle_time", _coerce_cycle(self.source_cycle_time))


@dataclass(frozen=True)
class OpenMeteoEcmwfIfs9AnchorRequest:
    latitude: float
    longitude: float
    run: datetime
    timezone_name: str
    forecast_hours: int = DEFAULT_FORECAST_HOURS
    temperature_unit: str = "celsius"
    model: str = MODEL
    hourly: tuple[str, ...] = HOURLY_VARIABLES

    def __post_init__(self) -> None:
        object.__setattr__(self, "run", _coerce_cycle(self.run))
        if not -90 <= self.latitude <= 90:
            raise ValueError("latitude out of range")
        if not -180 <= self.longitude <= 180:
            raise ValueError("longitude out of range")
        if self.forecast_hours <= 0 or self.forecast_hours > 240:
            raise ValueError("forecast_hours must be in 1..240")
        if self.model != MODEL:
            raise ValueError("Open-Meteo ECMWF IFS 9km anchor must use model=ecmwf_ifs")
        if "temperature_2m" not in self.hourly:
            raise ValueError("temperature_2m is required for deterministic anchor extraction")
        if not self.timezone_name:
            raise ValueError("timezone_name is required for local-day extraction")
        _reject_transcript_alias(SOURCE_ID, field_name="source_id")
        _reject_transcript_alias(PRODUCT_ID, field_name="product_id")

    @property
    def run_iso(self) -> str:
        return self.run.strftime("%Y-%m-%dT%H:%M")

    def params(self) -> dict[str, Any]:
        return {
            "latitude": self.latitude,
            "longitude": self.longitude,
            "hourly": ",".join(self.hourly),
            "models": self.model,
            "run": self.run_iso,
            "forecast_hours": self.forecast_hours,
            "temperature_unit": self.temperature_unit,
            "timezone": self.timezone_name,
        }

    def url(self) -> str:
        return f"{SINGLE_RUNS_FORECAST_URL}?{urlencode(self.params())}"

    def manifest_metadata(self) -> dict[str, Any]:
        return {
            "source_id": SOURCE_ID,
            "product_id": PRODUCT_ID,
            "model": self.model,
            "openmeteo_endpoint": "single_runs_api",
            "run": self.run_iso,
            "forecast_hours": self.forecast_hours,
            "role": "soft_spatial_anchor",
            "trade_authority_status": "SHADOW_ONLY",
            "training_allowed": False,
            "measurement_policy": "hourly_temperature_2m_localday_anchor",
        }


def build_anchor_request(
    *,
    latitude: float,
    longitude: float,
    run: datetime | str,
    timezone_name: str,
    forecast_hours: int = DEFAULT_FORECAST_HOURS,
) -> OpenMeteoEcmwfIfs9AnchorRequest:
    return OpenMeteoEcmwfIfs9AnchorRequest(
        latitude=latitude,
        longitude=longitude,
        run=_coerce_cycle(run),
        timezone_name=timezone_name,
        forecast_hours=forecast_hours,
    )


def fetch_openmeteo_ecmwf_ifs9_anchor_payload(
    request: OpenMeteoEcmwfIfs9AnchorRequest,
    *,
    timeout: float = 30.0,
    max_retries: int = 3,
) -> Mapping[str, Any]:
    """Fetch a run-pinned Open-Meteo ECMWF IFS 9km Single Runs payload."""

    if not isinstance(request, OpenMeteoEcmwfIfs9AnchorRequest):
        raise TypeError("request must be OpenMeteoEcmwfIfs9AnchorRequest")
    from src.data.openmeteo_client import fetch

    payload = fetch(
        SINGLE_RUNS_FORECAST_URL,
        request.params(),
        timeout=timeout,
        max_retries=max_retries,
        endpoint_label="openmeteo_ecmwf_ifs9_single_runs_anchor",
    )
    if not isinstance(payload, Mapping):
        raise ValueError("Open-Meteo ECMWF IFS 9km response must be a JSON object")
    return payload


def build_openmeteo_ecmwf_ifs9_anchor_artifact_manifest(
    artifact_path: Path | str,
    *,
    request: OpenMeteoEcmwfIfs9AnchorRequest,
    metric: str,
    source_available_at: datetime | str,
    captured_at: datetime | str,
    product_metadata: Mapping[str, Any] | None = None,
) -> RawForecastArtifactManifest:
    """Build a raw artifact manifest for a captured run-pinned Open-Meteo JSON file."""

    normalized_metric = metric.strip().lower()
    if normalized_metric == "high":
        data_version = HIGH_DATA_VERSION
    elif normalized_metric == "low":
        data_version = LOW_DATA_VERSION
    else:
        raise ValueError("metric must be high or low")
    metadata = request.manifest_metadata()
    metadata.update(dict(product_metadata or {}))
    metadata["metric"] = normalized_metric
    metadata["openmeteo_single_runs_url"] = request.url()
    return RawForecastArtifactManifest.from_file(
        artifact_path,
        source_id=SOURCE_ID,
        product_id=PRODUCT_ID,
        data_version=data_version,
        source_cycle_time=request.run,
        source_available_at=source_available_at,
        captured_at=captured_at,
        request_url=SINGLE_RUNS_FORECAST_URL,
        request_params=request.params(),
        product_metadata=metadata,
    )


def extract_openmeteo_ecmwf_ifs9_localday_anchor(
    payload: Mapping[str, Any],
    *,
    city_timezone: str,
    target_local_date: date,
    source_cycle_time: datetime | None = None,
    min_hourly_samples: int = 1,
) -> OpenMeteoIfs9LocalDayAnchor:
    """Extract deterministic local-day high/low from a run-pinned Open-Meteo response."""

    if min_hourly_samples <= 0:
        raise ValueError("min_hourly_samples must be positive")
    if not isinstance(payload, Mapping):
        raise TypeError("payload must be a mapping")
    hourly = payload.get("hourly")
    if not isinstance(hourly, Mapping):
        raise ValueError("Open-Meteo payload must contain hourly data")
    times = hourly.get("time")
    temperatures = hourly.get("temperature_2m")
    if not isinstance(times, Sequence) or isinstance(times, (str, bytes)):
        raise ValueError("hourly.time must be a sequence")
    if not isinstance(temperatures, Sequence) or isinstance(temperatures, (str, bytes)):
        raise ValueError("hourly.temperature_2m must be a sequence")
    if len(times) != len(temperatures):
        raise ValueError("hourly.time and hourly.temperature_2m lengths must match")

    units = payload.get("hourly_units")
    temperature_unit = "C"
    if isinstance(units, Mapping):
        temperature_unit = str(units.get("temperature_2m", "C"))

    contributing_local_times: list[datetime] = []
    contributing_valid_times_utc: list[datetime] = []
    contributing_temperatures_c: list[float] = []
    for raw_time, raw_temperature in zip(times, temperatures, strict=True):
        if not isinstance(raw_time, str):
            raise ValueError("hourly.time values must be strings")
        local_time = _parse_openmeteo_time(raw_time, city_timezone=city_timezone)
        if local_time.date() != target_local_date:
            continue
        contributing_local_times.append(local_time)
        contributing_valid_times_utc.append(local_time.astimezone(UTC))
        contributing_temperatures_c.append(_temperature_to_c(float(raw_temperature), temperature_unit))

    if len(contributing_temperatures_c) < min_hourly_samples:
        raise ValueError("insufficient Open-Meteo hourly samples inside target local day")

    return OpenMeteoIfs9LocalDayAnchor(
        city_timezone=city_timezone,
        target_local_date=target_local_date,
        source_cycle_time=source_cycle_time,
        high_c=max(contributing_temperatures_c),
        low_c=min(contributing_temperatures_c),
        sample_count=len(contributing_temperatures_c),
        contributing_local_times=tuple(contributing_local_times),
        contributing_valid_times_utc=tuple(contributing_valid_times_utc),
    )
