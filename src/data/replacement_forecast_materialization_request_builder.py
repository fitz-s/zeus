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
_OM9_LOCALDAY_COVERAGE_INCOMPLETE = "REPLACEMENT_MATERIALIZATION_OM9_LOCALDAY_HOURLY_COVERAGE_INCOMPLETE"


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
    if not guard.passable_for_live_materialization:
        return metadata_payload, ("OM9_PRECISION_GUARD_NOT_LIVE_PASS_REQUEST_BUILD", *guard.reason_codes)
    return metadata_payload, ()


def _om9_localday_coverage_ready(
    request: Mapping[str, object],
    *,
    base_dir: Path,
) -> tuple[str, ...]:
    from src.data.replacement_forecast_materializer import (  # noqa: PLC0415
        _expected_om9_hourly_count,
        _om9_localday_hourly_coverage_ok,
    )

    try:
        materialize_request = build_materialize_request_dataclass(request, base_dir=base_dir)
    except ValueError as exc:
        if "insufficient Open-Meteo hourly samples inside target local day" in str(exc):
            return (_OM9_LOCALDAY_COVERAGE_INCOMPLETE,)
        raise

    expected_count = _expected_om9_hourly_count(
        city_timezone=materialize_request.city_timezone,
        target_date=materialize_request.target_date,
    )
    computed_at = _dt(request.get("computed_at"), field_name="computed_at")
    if _om9_localday_hourly_coverage_ok(
        materialize_request,
        expected_sample_count=expected_count,
        computed_at=computed_at,
    ):
        return ()
    return (_OM9_LOCALDAY_COVERAGE_INCOMPLETE,)


def build_replacement_forecast_materialization_request(
    payload: Mapping[str, object],
    *,
    base_dir: Path | str,
) -> ReplacementForecastMaterializationRequestBuildResult:
    """Build the exact JSON consumed by materialize_replacement_forecast_live.py."""

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
        "day0_observed_extreme_c",
        "day0_observed_extreme_source",
        "day0_observed_extreme_observation_time",
        "day0_observed_extreme_sample_count",
        "day0_observed_extreme_unit",
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
    coverage_reasons = _om9_localday_coverage_ready(request, base_dir=base_path)
    if coverage_reasons:
        return ReplacementForecastMaterializationRequestBuildResult(
            status="BLOCKED",
            reason_codes=coverage_reasons,
            request=None,
        )
    return ReplacementForecastMaterializationRequestBuildResult(
        status="READY",
        reason_codes=("REPLACEMENT_MATERIALIZATION_REQUEST_READY",),
        request=request,
    )


def build_materialize_request_dataclass(
    request_json: Mapping[str, object],
    *,
    base_dir: Path | str,
):
    """Construct the ``ReplacementForecastMaterializeRequest`` dataclass from a
    READY request-JSON (the output of ``build_replacement_forecast_materialization_request``).

    SINGLE SOURCE OF TRUTH for the seed-JSON -> dataclass conversion: the live
    queue worker (``scripts/materialize_replacement_forecast_live.py``) and the
    monitor's read-only held-belief recompute (LAYER 2) both go through here, so
    the request the read-through computes is byte-equivalent to the one the live
    write path would have materialized. Reads the on-disk Open-Meteo anchor payload
    + precision metadata (the request-JSON carries their resolved paths); performs
    NO network fetch and NO DB write.
    """
    from src.data.openmeteo_ecmwf_ifs9_anchor import (
        extract_openmeteo_ecmwf_ifs9_localday_anchor,
    )
    from src.data.replacement_forecast_materializer import (
        ReplacementForecastMaterializeRequest,
    )

    base_path = Path(base_dir)
    metric = _required_text(request_json, "temperature_metric")
    target_date = _date(request_json.get("target_date"), field_name="target_date")
    source_cycle_time = _dt(request_json.get("source_cycle_time"), field_name="source_cycle_time")

    openmeteo_payload_path = _existing_path(request_json, "openmeteo_payload_json", base_dir=base_path)
    openmeteo_payload = _json_file(Path(openmeteo_payload_path))
    openmeteo_anchor = extract_openmeteo_ecmwf_ifs9_localday_anchor(
        openmeteo_payload,
        city_timezone=_required_text(request_json, "city_timezone"),
        target_local_date=target_date,
        source_cycle_time=source_cycle_time,
    )
    precision_metadata_path = _existing_path(request_json, "precision_metadata_json", base_dir=base_path)
    precision_payload = _json_file(Path(precision_metadata_path))
    precision_guard = evaluate_openmeteo_ecmwf_ifs9_precision_guard(
        OpenMeteoIfs9PrecisionMetadata(**dict(precision_payload))
    )

    def _opt_float(key: str) -> float | None:
        v = request_json.get(key)
        return None if v in (None, "") else float(v)  # type: ignore[arg-type]

    def _opt_text(key: str) -> str | None:
        v = request_json.get(key)
        return None if v in (None, "") else str(v)

    def _opt_int(key: str) -> int | None:
        v = request_json.get(key)
        return None if v in (None, "") else int(v)  # type: ignore[arg-type]

    anchor_artifact_id = _opt_int("openmeteo_anchor_artifact_id")
    return ReplacementForecastMaterializeRequest(
        city=_required_text(request_json, "city"),
        city_id=str(request_json.get("city_id") or request_json["city"]),
        city_timezone=_required_text(request_json, "city_timezone"),
        target_date=target_date,
        temperature_metric=metric,
        baseline_source_run_id=_required_text(request_json, "baseline_source_run_id"),
        baseline_data_version=_required_text(request_json, "baseline_data_version"),
        baseline_source_available_at=_dt(
            request_json.get("baseline_source_available_at"), field_name="baseline_source_available_at"
        ),
        openmeteo_anchor=openmeteo_anchor,
        openmeteo_source_run_id=str(request_json.get("openmeteo_source_run_id") or ""),
        openmeteo_source_available_at=_dt(
            request_json.get("openmeteo_source_available_at"), field_name="openmeteo_source_available_at"
        ),
        bins=_bins_to_temperature_bins(request_json.get("bins")),
        source_cycle_time=source_cycle_time,
        computed_at=_dt(request_json.get("computed_at"), field_name="computed_at"),
        expires_at=(
            None if request_json.get("expires_at") is None
            else _dt(request_json.get("expires_at"), field_name="expires_at")
        ),
        anchor_artifact_id=anchor_artifact_id,
        openmeteo_precision_guard=precision_guard,
        anchor_weight=float(request_json.get("anchor_weight", 0.80)),
        anchor_sigma_c=float(request_json.get("anchor_sigma_c", 3.00)),
        settlement_step_c=float(request_json.get("settlement_step_c", 1.0)),
        day0_observed_extreme_c=_opt_float("day0_observed_extreme_c"),
        day0_observed_extreme_source=_opt_text("day0_observed_extreme_source"),
        day0_observed_extreme_observation_time=_opt_text("day0_observed_extreme_observation_time"),
        day0_observed_extreme_sample_count=_opt_int("day0_observed_extreme_sample_count"),
        day0_observed_extreme_unit=_opt_text("day0_observed_extreme_unit"),
        upgrade_trigger=_opt_text("upgrade_trigger"),
    )


@dataclass(frozen=True)
class _TemperatureBin:
    bin_id: str
    lower_c: float | None
    upper_c: float | None
    center_c: float | None
    display_unit: str = "C"
    settlement_unit: str = "C"
    rounding_rule: str = "wmo_half_up"


def _bins_to_temperature_bins(rows: object) -> tuple[_TemperatureBin, ...]:
    if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)) or not rows:
        raise ValueError("bins must be a non-empty array")
    out: list[_TemperatureBin] = []
    for row in rows:
        if not isinstance(row, Mapping):
            raise ValueError("bins entries must be objects")
        out.append(
            _TemperatureBin(
                bin_id=str(row["bin_id"]),
                lower_c=None if row.get("lower_c") is None else float(row["lower_c"]),
                upper_c=None if row.get("upper_c") is None else float(row["upper_c"]),
                center_c=None if row.get("center_c") is None else float(row["center_c"]),
                display_unit=str(row.get("display_unit") or "C").strip().upper(),  # type: ignore[arg-type]
                settlement_unit=str(row.get("settlement_unit") or "C").strip().upper(),  # type: ignore[arg-type]
                rounding_rule=str(row.get("rounding_rule") or "wmo_half_up").strip(),  # type: ignore[arg-type]
            )
        )
    return tuple(out)
