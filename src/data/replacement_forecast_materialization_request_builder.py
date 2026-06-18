"""Build validated replacement forecast materialization request JSON."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from src.config import cities_by_name
from src.contracts.replacement_pipeline_files import validate_materialization_request
from src.data.openmeteo_ecmwf_ifs9_precision_guard import (
    OpenMeteoIfs9PrecisionMetadata,
    evaluate_openmeteo_ecmwf_ifs9_precision_guard,
)


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
    out: list[dict[str, object]] = []
    for row in rows:
        if not isinstance(row, Mapping):
            raise ValueError("bins entries must be objects")
        bin_id = str(row.get("bin_id") or "")
        if not bin_id:
            raise ValueError("bin_id is required")
        lower_c = None if row.get("lower_c") is None else float(row["lower_c"])
        upper_c = None if row.get("upper_c") is None else float(row["upper_c"])
        center_c = None if row.get("center_c") is None else float(row["center_c"])
        if lower_c is not None and upper_c is not None and upper_c < lower_c:
            raise ValueError("bin upper_c must be >= lower_c")
        display_unit = str(row.get("display_unit") or "C").strip().upper()  # type: ignore[arg-type]
        settlement_unit = str(row.get("settlement_unit") or "C").strip().upper()  # type: ignore[arg-type]
        rounding_rule = str(row.get("rounding_rule") or "wmo_half_up").strip()  # type: ignore[arg-type]
        if display_unit not in {"C", "F"} or settlement_unit not in {"C", "F"}:
            raise ValueError("bin display_unit and settlement_unit must be C or F")
        if not rounding_rule:
            raise ValueError("bin rounding_rule is required")
        out.append(
            {
                "bin_id": bin_id,
                "lower_c": lower_c,
                "upper_c": upper_c,
                "center_c": center_c,
                "display_unit": display_unit,
                "settlement_unit": settlement_unit,
                "rounding_rule": rounding_rule,
            }
        )

    seen = {str(item["bin_id"]) for item in out}
    if len(seen) != len(out):
        raise ValueError("bins must have unique bin_id values")
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
    # SINGLE freshness authority (operator directive 2026-06-11): derive readiness expiry
    # from the cycle's staleness bound — same function as the materializer stamp site.
    from src.data.replacement_forecast_cycle_policy import (  # noqa: PLC0415
        replacement_readiness_expires_at,
    )

    expires_at = (
        _dt(payload.get("expires_at"), field_name="expires_at")
        if payload.get("expires_at") is not None
        else replacement_readiness_expires_at(source_cycle_time)
    )
    if expires_at <= computed_at:
        raise ValueError("expires_at must be after computed_at")

    baseline_available = _dt(payload.get("baseline_source_available_at"), field_name="baseline_source_available_at")
    openmeteo_available = _dt(payload.get("openmeteo_source_available_at"), field_name="openmeteo_source_available_at")
    future_candidates = [baseline_available, openmeteo_available]
    if max(future_candidates) > computed_at:
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
        "openmeteo_source_run_id": _required_text(payload, "openmeteo_source_run_id"),
        "openmeteo_source_available_at": openmeteo_available.isoformat(),
        "anchor_weight": float(payload.get("anchor_weight", 0.80)),
        "anchor_sigma_c": float(payload.get("anchor_sigma_c", 3.00)),
        "settlement_step_c": float(payload.get("settlement_step_c", 1.0)),
        "bins": _bins(payload.get("bins"), settlement_step_c=float(payload.get("settlement_step_c", 1.0))),
        "openmeteo_payload_json": _existing_path(payload, "openmeteo_payload_json", base_dir=base_path),
        "precision_metadata_json": precision_metadata_json,
    }
    for optional_key in (
        "openmeteo_manifest_json",
        "openmeteo_anchor_artifact_id",
        "latitude",
        "longitude",
        # Task #32: honest re-materialization provenance. When the seed was written by the
        # fusion-upgrade trigger it carries upgrade_trigger="instrument_set_expansion"; thread it
        # through verbatim so the materializer can record it in the posterior provenance_json.
        "upgrade_trigger",
    ):
        if optional_key in payload:
            request[optional_key] = payload[optional_key]
    # BOUNDARY CONTRACT (2026-06-10): validate the assembled request against the
    # shared producer⇄consumer schema BEFORE returning it READY. This is the
    # producer half of the contract: a request that passes here is guaranteed to
    # pass the queue's consumer-side validate_materialization_request, so a
    # divergence between this assembly site and the consumer's expectations can
    # never ship a poison file downstream. Authority basis: pipeline-contract
    # project, operator directive 2026-06-10.
    validate_materialization_request(request)
    return ReplacementForecastMaterializationRequestBuildResult(
        status="READY",
        reason_codes=("REPLACEMENT_MATERIALIZATION_REQUEST_READY",),
        request=request,
    )
