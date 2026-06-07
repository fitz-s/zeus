"""Build validated replacement forecast materialization request JSON."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from src.config import cities_by_name
from src.data.openmeteo_ecmwf_ifs9_precision_guard import (
    OpenMeteoIfs9PrecisionMetadata,
    evaluate_openmeteo_ecmwf_ifs9_precision_guard,
)
from src.strategy.ecmwf_aifs_sampled_2t_probabilities import AifsTemperatureBin


UTC = timezone.utc
_FORBIDDEN_TRANSCRIPT_ALIAS = "h" + "3"


@dataclass(frozen=True)
class ReplacementForecastMaterializationRequestBuildResult:
    status: str
    reason_codes: tuple[str, ...]
    request: Mapping[str, object] | None

    @property
    def ok(self) -> bool:
        return self.status == "READY"

    def as_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "reason_codes": list(self.reason_codes),
            "request": dict(self.request or {}),
        }


def _reject_alias(value: str, *, field_name: str) -> None:
    if _FORBIDDEN_TRANSCRIPT_ALIAS in value.lower():
        raise ValueError(f"{field_name} must use the full replacement identity")


def _json_file(path: Path) -> Mapping[str, object]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError(f"{path} must decode to an object")
    return payload


def _required_text(payload: Mapping[str, object], key: str) -> str:
    value = str(payload.get(key) or "").strip()
    if not value:
        raise ValueError(f"{key} is required")
    _reject_alias(value, field_name=key)
    return value


def _dt(value: object, *, field_name: str) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value.strip():
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    else:
        raise ValueError(f"{field_name} must be a timezone-aware datetime")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return parsed.astimezone(UTC)


def _date(value: object, *, field_name: str) -> date:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, str) and value.strip():
        return date.fromisoformat(value)
    raise ValueError(f"{field_name} must be an ISO date")


def _existing_path(payload: Mapping[str, object], key: str, *, base_dir: Path) -> str:
    text = _required_text(payload, key)
    path = Path(text)
    if not path.is_absolute():
        path = base_dir / path
    if not path.exists():
        raise ValueError(f"{key} does not exist: {path}")
    return str(path)


def _bins(rows: object, *, settlement_step_c: float = 1.0) -> list[dict[str, object]]:
    if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)) or not rows:
        raise ValueError("bins must be a non-empty array")
    if settlement_step_c <= 0:
        raise ValueError("settlement_step_c must be positive")
    validated: list[AifsTemperatureBin] = []
    out: list[dict[str, object]] = []
    for row in rows:
        if not isinstance(row, Mapping):
            raise ValueError("bins entries must be objects")
        bin_spec = AifsTemperatureBin(
            bin_id=str(row.get("bin_id") or ""),
            lower_c=None if row.get("lower_c") is None else float(row["lower_c"]),
            upper_c=None if row.get("upper_c") is None else float(row["upper_c"]),
            center_c=None if row.get("center_c") is None else float(row["center_c"]),
        )
        validated.append(bin_spec)
        out.append(
            {
                "bin_id": bin_spec.bin_id,
                "lower_c": bin_spec.lower_c,
                "upper_c": bin_spec.upper_c,
                "center_c": bin_spec.center_c,
            }
        )
    # Reuse the probability bridge validation without needing member data.
    from src.strategy.ecmwf_aifs_sampled_2t_probabilities import _validate_full_family_bins

    _validate_full_family_bins(validated, settlement_step_c=float(settlement_step_c))
    return out


def _precision_ready(path: Path) -> tuple[Mapping[str, object], tuple[str, ...]]:
    metadata_payload = _json_file(path)
    guard = evaluate_openmeteo_ecmwf_ifs9_precision_guard(
        OpenMeteoIfs9PrecisionMetadata(**dict(metadata_payload))
    )
    if not guard.passable_for_shadow_veto:
        return metadata_payload, ("OM9_PRECISION_GUARD_BLOCKED_REQUEST_BUILD", *guard.reason_codes)
    return metadata_payload, ()


def build_replacement_forecast_materialization_request(
    payload: Mapping[str, object],
    *,
    base_dir: Path | str,
) -> ReplacementForecastMaterializationRequestBuildResult:
    """Build the exact JSON consumed by materialize_replacement_forecast_shadow.py."""

    base_path = Path(base_dir)
    city = _required_text(payload, "city")
    city_config = cities_by_name.get(city)
    city_timezone = str(payload.get("city_timezone") or getattr(city_config, "timezone", "") or "")
    if not city_timezone:
        raise ValueError("city_timezone is required when city is not in config/cities.json")
    target_date = _date(payload.get("target_date"), field_name="target_date")
    metric = _required_text(payload, "temperature_metric")
    if metric not in {"high", "low"}:
        raise ValueError("temperature_metric must be high or low")
    source_cycle_time = _dt(payload.get("source_cycle_time"), field_name="source_cycle_time")
    computed_at = _dt(payload.get("computed_at"), field_name="computed_at")
    expires_at = (
        _dt(payload.get("expires_at"), field_name="expires_at")
        if payload.get("expires_at") is not None
        else computed_at + timedelta(hours=3)
    )
    if expires_at <= computed_at:
        raise ValueError("expires_at must be after computed_at")

    baseline_available = _dt(payload.get("baseline_source_available_at"), field_name="baseline_source_available_at")
    aifs_available = _dt(payload.get("aifs_source_available_at"), field_name="aifs_source_available_at")
    openmeteo_available = _dt(payload.get("openmeteo_source_available_at"), field_name="openmeteo_source_available_at")
    if max(baseline_available, aifs_available, openmeteo_available) > computed_at:
        return ReplacementForecastMaterializationRequestBuildResult(
            status="BLOCKED",
            reason_codes=("REPLACEMENT_MATERIALIZATION_REQUEST_HAS_FUTURE_DEPENDENCY",),
            request=None,
        )

    precision_metadata_json = _existing_path(payload, "precision_metadata_json", base_dir=base_path)
    _, precision_reasons = _precision_ready(Path(precision_metadata_json))
    if precision_reasons:
        return ReplacementForecastMaterializationRequestBuildResult(
            status="BLOCKED",
            reason_codes=precision_reasons,
            request=None,
        )

    if "aifs_samples_json" in payload:
        aifs_input_key = "aifs_samples_json"
        aifs_input_value = _existing_path(payload, "aifs_samples_json", base_dir=base_path)
    elif "aifs_grib_path" in payload:
        aifs_input_key = "aifs_grib_path"
        aifs_input_value = _existing_path(payload, "aifs_grib_path", base_dir=base_path)
    else:
        raise ValueError("aifs_samples_json or aifs_grib_path is required")

    request = {
        "city": city,
        "city_id": str(payload.get("city_id") or city),
        "city_timezone": city_timezone,
        "target_date": target_date.isoformat(),
        "temperature_metric": metric,
        "source_cycle_time": source_cycle_time.isoformat(),
        "computed_at": computed_at.isoformat(),
        "expires_at": expires_at.isoformat(),
        "baseline_source_run_id": _required_text(payload, "baseline_source_run_id"),
        "baseline_data_version": _required_text(payload, "baseline_data_version"),
        "baseline_source_available_at": baseline_available.isoformat(),
        "aifs_source_run_id": _required_text(payload, "aifs_source_run_id"),
        "aifs_source_available_at": aifs_available.isoformat(),
        "openmeteo_source_run_id": _required_text(payload, "openmeteo_source_run_id"),
        "openmeteo_source_available_at": openmeteo_available.isoformat(),
        "anchor_weight": float(payload.get("anchor_weight", 0.80)),
        "anchor_sigma_c": float(payload.get("anchor_sigma_c", 3.00)),
        "settlement_step_c": float(payload.get("settlement_step_c", 1.0)),
        "bins": _bins(payload.get("bins"), settlement_step_c=float(payload.get("settlement_step_c", 1.0))),
        aifs_input_key: aifs_input_value,
        "openmeteo_payload_json": _existing_path(payload, "openmeteo_payload_json", base_dir=base_path),
        "precision_metadata_json": precision_metadata_json,
    }
    for optional_key in (
        "aifs_manifest_json",
        "openmeteo_manifest_json",
        "aifs_artifact_id",
        "openmeteo_anchor_artifact_id",
        "latitude",
        "longitude",
    ):
        if optional_key in payload:
            request[optional_key] = payload[optional_key]
    return ReplacementForecastMaterializationRequestBuildResult(
        status="READY",
        reason_codes=("REPLACEMENT_MATERIALIZATION_REQUEST_READY",),
        request=request,
    )
