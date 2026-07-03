"""Run-pinned Open-Meteo ECMWF IFS 9km deterministic anchor contract."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from src.contracts.availability_time import proof_of_possession_available_at
from src.data.raw_forecast_artifact_manifest import RawForecastArtifactManifest


SINGLE_RUNS_FORECAST_URL = "https://single-runs-api.open-meteo.com/v1/forecast"
# Meta-stamped CURRENT-run path (operator directive 2026-06-11 "data shortage must never
# recur"; measured basis docs/evidence/rule1_audits/ + K4.0b(f)): the standard forecast API
# serves the provider's freshest completed run, and the provider DECLARES that run's
# identity + completeness in its static meta.json (last_run_initialisation_time /
# last_run_availability_time / last_run_modification_time). single-runs stays the
# strongest (explicitly run-pinned) transport and is tried FIRST; meta-stamped standard
# fetch is the fallback when single-runs does not yet serve the wanted run.
STANDARD_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
MODEL_META_URL = "https://api.open-meteo.com/data/ecmwf_ifs/static/meta.json"
RUN_AUTHORITY_SINGLE_RUNS = "run_pinned_single_runs"
RUN_AUTHORITY_META_DECLARED = "provider_meta_declared"
SOURCE_ID = "openmeteo_ecmwf_ifs_9km"
PRODUCT_ID = "openmeteo_ecmwf_ifs9_deterministic_anchor_v1"
HIGH_DATA_VERSION = "openmeteo_ecmwf_ifs9_anchor_localday_high"
LOW_DATA_VERSION = "openmeteo_ecmwf_ifs9_anchor_localday_low"
MODEL = "ecmwf_ifs"
HOURLY_VARIABLES = ("temperature_2m",)
DEFAULT_FORECAST_HOURS = 120

# Local-day coverage span guard (2026-06-17): the daily extreme is trustworthy ONLY if the
# hourly samples SPAN the full settlement day, so the diurnal peak/trough is inside the window.
# A horizon-clipped partial day — e.g. a 2km model whose ~48h horizon ends at 17:00 on a lead-2
# target, or any model that returns only a morning slice — is OMITTED (the caller is fail-soft)
# rather than yielding a WRONG clipped extreme (a morning-only "high"). Step-resolution-agnostic:
# a 3-hourly model spanning 00:00..21:00 passes; a model clipped at 17:00 fails. require_full_localday
# (the live forward parser) enforces it; the de-bias/anchor callers keep the legacy >=1 behaviour.
LOCALDAY_SPAN_EARLY_HOUR = 3   # earliest contributing sample must be at/under 03:00 local
LOCALDAY_SPAN_LATE_HOUR = 20   # latest contributing sample must be at/over 20:00 local
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


def _parse_utc(value: datetime | str, *, field_name: str) -> datetime:
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return parsed.astimezone(UTC)


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
    trade_authority_status: str = "BLOCKED"
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
        if self.trade_authority_status != "BLOCKED" or self.training_allowed:
            raise ValueError("Open-Meteo ECMWF IFS 9km anchor is blocked until promoted by evidence")
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
        # 2026-06-17 land-cell fix: the 9km ecmwf_ifs anchor (the fusion PRIOR) must read the
        # airport's LAND surface, not the nearest OFFSHORE cell. OM's default was "nearest",
        # which snapped coastal airports over water (the cold drag — Tokyo high -4.09 -> -1.34
        # with land, all-cities settlement MAE 1.121 -> 0.996). Pure data-precision, not a de-bias.
        from src.data.bayes_precision_fusion_download import (  # noqa: PLC0415
            BAYES_PRECISION_FUSION_CELL_SELECTION,
        )

        return {
            "latitude": self.latitude,
            "longitude": self.longitude,
            "hourly": ",".join(self.hourly),
            "models": self.model,
            "run": self.run_iso,
            "forecast_hours": self.forecast_hours,
            "temperature_unit": self.temperature_unit,
            "timezone": self.timezone_name,
            "cell_selection": BAYES_PRECISION_FUSION_CELL_SELECTION,
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
            "trade_authority_status": "BLOCKED",
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
    fast_fail_429: bool = False,
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
        fast_fail_429=fast_fail_429,
    )
    if not isinstance(payload, Mapping):
        raise ValueError("Open-Meteo ECMWF IFS 9km response must be a JSON object")
    return payload


def fetch_openmeteo_ifs9_model_meta(
    *,
    timeout: float = 20.0,
    max_retries: int = 2,
    fast_fail_429: bool = False,
) -> Mapping[str, Any]:
    """Provider-declared run metadata for the ecmwf_ifs (9km) model.

    Returns the raw meta mapping plus parsed UTC datetimes under
    ``run_initialisation_utc`` / ``run_availability_utc`` / ``run_modification_utc``.
    The initialisation time IS the run/cycle identity, declared by the provider for
    the data the standard forecast API currently serves; availability marks the run's
    completed ingestion (the atomicity marker for meta-stamped fetches)."""
    from src.data.openmeteo_client import fetch

    meta = fetch(
        MODEL_META_URL,
        {},
        timeout=timeout,
        max_retries=max_retries,
        endpoint_label="openmeteo_ecmwf_ifs9_model_meta",
        fast_fail_429=fast_fail_429,
    )
    if not isinstance(meta, Mapping):
        raise ValueError("Open-Meteo model meta must be a JSON object")
    out: dict[str, Any] = dict(meta)
    for src_key, dst_key in (
        ("last_run_initialisation_time", "run_initialisation_utc"),
        ("last_run_availability_time", "run_availability_utc"),
        ("last_run_modification_time", "run_modification_utc"),
    ):
        raw = meta.get(src_key)
        if raw is None:
            raise ValueError(f"Open-Meteo model meta missing {src_key}")
        out[dst_key] = datetime.fromtimestamp(int(raw), UTC)
    return out


def fetch_openmeteo_ecmwf_ifs9_anchor_payload_meta_stamped(
    request: OpenMeteoEcmwfIfs9AnchorRequest,
    *,
    timeout: float = 30.0,
    max_retries: int = 3,
    fast_fail_429: bool = False,
    meta_fetch: Any = None,
) -> tuple[Mapping[str, Any], Mapping[str, Any]]:
    """Fetch the CURRENT run from the standard forecast API with provider meta run-stamp.

    Contract (no-guess + atomicity, K4.0b(f)):
      1. Read meta BEFORE: the declared run must EQUAL ``request.run`` and the run must be
         complete (availability >= initialisation). A different declared run ⇒ ValueError —
         this path can never silently serve a different cycle than the caller pairs with.
      2. Fetch from the standard API (same params, NO ``run`` parameter — the standard API
         serves exactly the run the provider declared in meta).
      3. Read meta AFTER: ``last_run_modification_time`` must be unchanged, else the
         provider ingested mid-fetch and the payload may mix runs ⇒ ValueError (caller
         retries next tick).
    Returns ``(payload, meta_provenance)``; the provenance dict must be threaded into the
    artifact manifest so the run authority is auditable and the retro single-runs
    cross-check can find these artifacts."""
    if not isinstance(request, OpenMeteoEcmwfIfs9AnchorRequest):
        raise TypeError("request must be OpenMeteoEcmwfIfs9AnchorRequest")
    from src.data.openmeteo_client import fetch

    def _fetch_meta() -> Mapping[str, Any]:
        if meta_fetch is not None:
            return meta_fetch(timeout=timeout)
        return fetch_openmeteo_ifs9_model_meta(
            timeout=timeout,
            max_retries=max_retries,
            fast_fail_429=fast_fail_429,
        )

    meta_before = _fetch_meta()
    declared_run = meta_before["run_initialisation_utc"]
    if declared_run != request.run:
        raise ValueError(
            "meta-stamped anchor fetch refused: provider declares run "
            f"{declared_run.isoformat()} but caller wants {request.run.isoformat()}"
        )
    if meta_before["run_availability_utc"] < meta_before["run_initialisation_utc"]:
        raise ValueError("meta-stamped anchor fetch refused: declared run not yet complete")
    params = {k: v for k, v in request.params().items() if k != "run"}
    payload = fetch(
        STANDARD_FORECAST_URL,
        params,
        timeout=timeout,
        max_retries=max_retries,
        endpoint_label="openmeteo_ecmwf_ifs9_standard_meta_stamped_anchor",
        fast_fail_429=fast_fail_429,
    )
    if not isinstance(payload, Mapping):
        raise ValueError("Open-Meteo ECMWF IFS 9km response must be a JSON object")
    meta_after = _fetch_meta()
    if meta_after["run_modification_utc"] != meta_before["run_modification_utc"]:
        raise ValueError(
            "meta-stamped anchor fetch discarded: provider modified the model dataset "
            "mid-fetch (possible mixed-run payload); retry next tick"
        )
    provenance: dict[str, Any] = {
        "openmeteo_endpoint": "standard_api_meta_stamped",
        "run_authority": RUN_AUTHORITY_META_DECLARED,
        "meta_run_initialisation_utc": declared_run.isoformat(),
        "meta_run_availability_utc": meta_before["run_availability_utc"].isoformat(),
        "meta_run_modification_utc": meta_before["run_modification_utc"].isoformat(),
        "cross_check_status": "PENDING_SINGLE_RUNS_PUBLICATION",
    }
    return payload, provenance


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
    captured_at_utc = _parse_utc(captured_at, field_name="captured_at")
    requested_source_available_at = _parse_utc(source_available_at, field_name="source_available_at")
    # C1-AVAIL-CLOCK (2026-06-16): source availability is PROOF OF POSSESSION = our captured_at
    # wall-clock, routed through the canonical producer. Open-Meteo serves no signed generation
    # time, so the requested source_available_at is diagnostic-only (recorded below) and never
    # credited as a publish; captured_at is the honest earliest-usable basis. Re-parsed to a
    # tz-aware datetime because RawForecastArtifactManifest.to_dict() calls .astimezone(UTC).
    effective_source_available_at = _parse_utc(
        proof_of_possession_available_at(captured_at_utc),
        field_name="source_available_at",
    )
    metadata["requested_source_available_at"] = requested_source_available_at.isoformat()
    metadata["source_available_at_authority"] = "captured_at_no_signed_openmeteo_generation_time"
    if requested_source_available_at != captured_at_utc:
        metadata["requested_source_available_at_role"] = "diagnostic_not_authority"
    return RawForecastArtifactManifest.from_file(
        artifact_path,
        source_id=SOURCE_ID,
        product_id=PRODUCT_ID,
        data_version=data_version,
        source_cycle_time=request.run,
        source_available_at=effective_source_available_at,  # AVAIL-POSSESSION-EXEMPTED: effective_source_available_at is produced by proof_of_possession_available_at(captured_at_utc) at its assignment above; this is a passthrough of that canonical value into the manifest.
        captured_at=captured_at_utc,
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
    require_full_localday: bool = False,
) -> OpenMeteoIfs9LocalDayAnchor:
    """Extract deterministic local-day high/low from a run-pinned Open-Meteo response.

    require_full_localday: when True, REJECT a target local day whose hourly samples do not
    SPAN the full settlement day (earliest <= LOCALDAY_SPAN_EARLY_HOUR and latest >=
    LOCALDAY_SPAN_LATE_HOUR). A horizon-clipped partial day (a fine model past its ~48h horizon
    on a far lead) is then omitted by the fail-soft caller instead of producing a wrong clipped
    extreme. Step-resolution-agnostic — depends on the time SPAN, not the sample count.
    """

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
        if raw_temperature is None:
            # Open-Meteo returns null for a missing hour at the forecast-horizon edge (notably
            # the 2-day-out low metric — London/Paris 2026-06-26 crashed the live materializer here
            # on float(None), 2026-06-24). Skip the gap rather than crash; the min_hourly_samples /
            # require_full_localday checks below still enforce coverage and raise a clear ValueError
            # if the surviving in-day samples are genuinely insufficient.
            continue
        contributing_local_times.append(local_time)
        contributing_valid_times_utc.append(local_time.astimezone(UTC))
        contributing_temperatures_c.append(_temperature_to_c(float(raw_temperature), temperature_unit))

    if len(contributing_temperatures_c) < min_hourly_samples:
        raise ValueError("insufficient Open-Meteo hourly samples inside target local day")

    if require_full_localday:
        _hours = [t.hour for t in contributing_local_times]
        if not (min(_hours) <= LOCALDAY_SPAN_EARLY_HOUR and max(_hours) >= LOCALDAY_SPAN_LATE_HOUR):
            raise ValueError(
                f"partial local-day coverage: hours span [{min(_hours):02d}..{max(_hours):02d}] "
                f"does not cover the full settlement day (need earliest<={LOCALDAY_SPAN_EARLY_HOUR:02d}:00 "
                f"and latest>={LOCALDAY_SPAN_LATE_HOUR:02d}:00) — horizon-clipped, excluded"
            )

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
