"""Build replacement materialization seed JSON from real market/source context."""

from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from src.config import cities_by_name
from src.contracts.replacement_pipeline_files import validate_materialization_seed
from src.data.raw_forecast_artifact_manifest import RawForecastArtifactManifest, read_manifest
from src.data.replacement_forecast_cycle_policy import replacement_readiness_expires_at
from src.data.replacement_forecast_source_run_identity import expected_replacement_dependency_identity_by_role


UTC = timezone.utc
_FORBIDDEN_TRANSCRIPT_ALIAS = "h" + "3"


@dataclass(frozen=True)
class ReplacementForecastMaterializationSeedResult:
    status: str
    reason_codes: tuple[str, ...]
    seed: Mapping[str, object] | None

    @property
    def ok(self) -> bool:
        return self.status == "READY"

    def as_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "reason_codes": list(self.reason_codes),
            "seed": dict(self.seed or {}),
        }


def _reject_alias(value: str, *, field_name: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{field_name} is required")
    if _FORBIDDEN_TRANSCRIPT_ALIAS in normalized.lower():
        raise ValueError(f"{field_name} must use the full replacement identity")
    return normalized


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


def _temperature_to_c(value: float | None, *, unit: str) -> float | None:
    if value is None:
        return None
    number = float(value)
    normalized = unit.strip().upper()
    if normalized == "C":
        return number
    if normalized == "F":
        return (number - 32.0) * 5.0 / 9.0
    if normalized in {"K", "KELVIN"}:
        return number - 273.15
    raise ValueError(f"unsupported settlement unit: {unit!r}")


def _settlement_step_c(unit: str) -> float:
    normalized = unit.strip().upper()
    if normalized == "C":
        return 1.0
    if normalized == "F":
        return 5.0 / 9.0
    raise ValueError(f"unsupported settlement unit: {unit!r}")


def _display_unit_for_label(label: str, *, fallback: str) -> str:
    if "\u00b0F" in label or "Fahrenheit" in label:
        return "F"
    if "\u00b0C" in label or "Celsius" in label:
        return "C"
    normalized = fallback.strip().upper()
    if normalized in {"C", "F"}:
        return normalized
    raise ValueError(f"unsupported display unit fallback: {fallback!r}")


def _market_bins_to_celsius(
    rows: Sequence[Mapping[str, object]],
    *,
    settlement_unit: str,
    rounding_rule: str = "wmo_half_up",
) -> list[dict[str, object]]:
    if not rows:
        raise ValueError("market bin rows are required")
    step_c = _settlement_step_c(settlement_unit)
    ordered = sorted(
        rows,
        key=lambda row: (
            float("-inf") if row.get("range_low") is None else float(row["range_low"]),
            float("inf") if row.get("range_high") is None else float(row["range_high"]),
            str(row.get("range_label") or ""),
        ),
    )
    bins: list[dict[str, object]] = []
    seen: set[str] = set()
    for row in ordered:
        label = _reject_alias(str(row.get("range_label") or row.get("token_id") or ""), field_name="range_label")
        if label in seen:
            raise ValueError(f"duplicate market bin label: {label}")
        seen.add(label)
        display_unit = _display_unit_for_label(label, fallback=settlement_unit)
        lower_c = _temperature_to_c(row.get("range_low"), unit=display_unit)
        upper_c = _temperature_to_c(row.get("range_high"), unit=display_unit)
        if lower_c is None and upper_c is None:
            raise ValueError(f"market bin {label!r} has no finite bound")
        if lower_c is not None and upper_c is not None:
            center_c = (lower_c + upper_c) / 2.0
        elif lower_c is None:
            center_c = float(upper_c) - step_c
        else:
            center_c = float(lower_c) + step_c
        bins.append(
            {
                "bin_id": label,
                "lower_c": lower_c,
                "upper_c": upper_c,
                "center_c": center_c,
                "display_unit": display_unit,
                "settlement_unit": settlement_unit.strip().upper(),
                "rounding_rule": rounding_rule,
            }
        )
    return bins


def _manifest_source_run_id(manifest: RawForecastArtifactManifest, *, role: str) -> str:
    explicit = manifest.product_metadata.get("source_run_id")
    if explicit is not None and str(explicit).strip():
        return _reject_alias(str(explicit), field_name=f"{role}_source_run_id")
    cycle = manifest.source_cycle_time.astimezone(UTC).isoformat()
    return f"raw:{manifest.source_id}:{manifest.data_version}:{cycle}"


def _relative_or_absolute(path: Path, *, base_dir: Path) -> str:
    resolved = path.resolve()
    base = base_dir.resolve()
    try:
        return str(resolved.relative_to(base))
    except ValueError:
        stable_root = base.parent
        try:
            resolved.relative_to(stable_root)
        except ValueError:
            return str(resolved)
        return os.path.relpath(resolved, base)


def build_replacement_forecast_materialization_seed(
    *,
    city: str,
    target_date: str,
    temperature_metric: str,
    market_bins: Sequence[Mapping[str, object]],
    baseline_coverage: Mapping[str, object],
    openmeteo_manifest: RawForecastArtifactManifest,
    openmeteo_payload_json: Path | str,
    precision_metadata_json: Path | str,
    computed_at: datetime | str,
    base_dir: Path | str,
    expires_at: datetime | str | None = None,
    anchor_weight: float = 0.80,
    anchor_sigma_c: float = 3.00,
    day0_observed_extreme_c: float | None = None,
    day0_observed_extreme_source: str | None = None,
    day0_observed_extreme_observation_time: str | None = None,
    day0_observed_extreme_sample_count: int | None = None,
    day0_observed_extreme_unit: str | None = None,
) -> ReplacementForecastMaterializationSeedResult:
    city_name = _reject_alias(city, field_name="city")
    metric = _reject_alias(temperature_metric, field_name="temperature_metric")
    if metric not in {"high", "low"}:
        raise ValueError("temperature_metric must be high or low")
    city_config = cities_by_name.get(city_name)
    city_timezone = str(baseline_coverage.get("city_timezone") or getattr(city_config, "timezone", "") or "")
    if not city_timezone:
        raise ValueError("city timezone is required")
    settlement_unit = str(getattr(city_config, "settlement_unit", "") or baseline_coverage.get("settlement_unit") or "")
    if not settlement_unit:
        raise ValueError("settlement unit is required")
    rounding_rule = "oracle_truncate" if str(getattr(city_config, "settlement_source_type", "") or "") == "hko" else "wmo_half_up"
    expected = expected_replacement_dependency_identity_by_role(metric)
    baseline_expected = expected["baseline_b0"]
    reasons: list[str] = []
    if baseline_coverage.get("source_id") != baseline_expected.source_id:
        reasons.append("BASELINE_COVERAGE_SOURCE_ID_MISMATCH")
    if baseline_coverage.get("data_version") != baseline_expected.data_version:
        reasons.append("BASELINE_COVERAGE_DATA_VERSION_MISMATCH")
    if baseline_coverage.get("temperature_metric") != metric:
        reasons.append("BASELINE_COVERAGE_METRIC_MISMATCH")
    if openmeteo_manifest.source_id != expected["openmeteo_ifs9_anchor"].source_id or openmeteo_manifest.data_version != expected["openmeteo_ifs9_anchor"].data_version:
        reasons.append("OPENMETEO_MANIFEST_IDENTITY_MISMATCH")
    baseline_source_cycle_time = baseline_coverage.get("source_cycle_time")
    if baseline_source_cycle_time is not None and str(baseline_source_cycle_time).strip():
        baseline_cycle = _dt(baseline_source_cycle_time, field_name="baseline_source_cycle_time")
        if openmeteo_manifest.source_cycle_time.astimezone(UTC) < baseline_cycle:
            reasons.append("REPLACEMENT_MATERIALIZATION_SEED_OM9_CYCLE_REGRESSES_BASELINE")
    if reasons:
        return ReplacementForecastMaterializationSeedResult(status="BLOCKED", reason_codes=tuple(reasons), seed=None)

    base_path = Path(base_dir)
    computed = _dt(computed_at, field_name="computed_at")
    baseline_available = _dt(
        baseline_coverage.get("source_available_at") or baseline_coverage.get("computed_at"),
        field_name="baseline_source_available_at",
    )
    if max(baseline_available, openmeteo_manifest.source_available_at) > computed:
        return ReplacementForecastMaterializationSeedResult(
            status="BLOCKED",
            reason_codes=("REPLACEMENT_MATERIALIZATION_SEED_HAS_FUTURE_DEPENDENCY",),
            seed=None,
        )
    source_cycle_time = openmeteo_manifest.source_cycle_time
    expiry = (
        _dt(expires_at, field_name="expires_at")
        if expires_at is not None
        else replacement_readiness_expires_at(source_cycle_time)
    )

    seed = {
        "city": city_name,
        "city_id": str(baseline_coverage.get("city_id") or city_name),
        "city_timezone": city_timezone,
        "target_date": target_date,
        "temperature_metric": metric,
        "source_cycle_time": source_cycle_time.isoformat(),
        "computed_at": computed.isoformat(),
        "expires_at": expiry.isoformat(),
        "baseline_source_run_id": _reject_alias(str(baseline_coverage.get("source_run_id") or ""), field_name="baseline_source_run_id"),
        "baseline_data_version": baseline_expected.data_version,
        "baseline_completeness_status": str(baseline_coverage.get("completeness_status") or ""),
        "baseline_readiness_status": str(baseline_coverage.get("readiness_status") or ""),
        "baseline_source_available_at": baseline_available.isoformat(),
        "openmeteo_source_run_id": _manifest_source_run_id(openmeteo_manifest, role="openmeteo"),
        "openmeteo_source_available_at": openmeteo_manifest.source_available_at.isoformat(),
        "anchor_weight": float(anchor_weight),
        "anchor_sigma_c": float(anchor_sigma_c),
        "settlement_step_c": _settlement_step_c(settlement_unit),
        "bins": _market_bins_to_celsius(
            market_bins,
            settlement_unit=settlement_unit,
            rounding_rule=rounding_rule,
        ),
        "openmeteo_payload_json": _relative_or_absolute(Path(openmeteo_payload_json), base_dir=base_path),
        "precision_metadata_json": _relative_or_absolute(Path(precision_metadata_json), base_dir=base_path),
        "openmeteo_anchor_artifact_id": openmeteo_manifest.product_metadata.get("artifact_id"),
        "openmeteo_manifest_json": _relative_or_absolute(Path(str(openmeteo_manifest.product_metadata.get("manifest_json") or "")), base_dir=base_path)
        if openmeteo_manifest.product_metadata.get("manifest_json")
        else None,
        "latitude": getattr(city_config, "lat", None),
        "longitude": getattr(city_config, "lon", None),
    }
    if day0_observed_extreme_c is not None:
        seed.update(
            {
                "day0_observed_extreme_c": float(day0_observed_extreme_c),
                "day0_observed_extreme_source": str(day0_observed_extreme_source or ""),
                "day0_observed_extreme_observation_time": str(day0_observed_extreme_observation_time or ""),
                "day0_observed_extreme_sample_count": int(day0_observed_extreme_sample_count or 0),
                "day0_observed_extreme_unit": str(day0_observed_extreme_unit or "C"),
            }
        )
    final_seed = {key: value for key, value in seed.items() if value is not None}
    # BOUNDARY CONTRACT (2026-06-10): validate the assembled seed against the
    # shared producer⇄consumer schema BEFORE returning it READY. A seed that
    # passes here is guaranteed to pass the queue's consumer-side
    # validate_materialization_seed, so this assembly site and the consumer can
    # never silently diverge. Authority basis: pipeline-contract project,
    # operator directive 2026-06-10.
    validate_materialization_seed(final_seed)
    return ReplacementForecastMaterializationSeedResult(
        status="READY",
        reason_codes=("REPLACEMENT_MATERIALIZATION_SEED_READY",),
        seed=final_seed,
    )


def latest_baseline_coverage_for_replacement_seed(
    conn: sqlite3.Connection,
    *,
    city: str,
    target_date: str,
    temperature_metric: str,
) -> Mapping[str, object] | None:
    expected = expected_replacement_dependency_identity_by_role(temperature_metric)["baseline_b0"]
    row = conn.execute(
        """
        SELECT c.*, sr.source_cycle_time AS source_cycle_time, sr.source_available_at AS source_available_at
        FROM source_run_coverage c
        LEFT JOIN source_run sr ON sr.source_run_id = c.source_run_id
        WHERE c.city = ?
          AND c.target_local_date = ?
          AND c.temperature_metric = ?
          AND c.source_id = ?
          AND c.data_version = ?
        ORDER BY
          CASE WHEN c.completeness_status = 'COMPLETE' THEN 0 ELSE 1 END,
          CASE WHEN c.readiness_status = 'LIVE_ELIGIBLE' THEN 0 ELSE 1 END,
          c.computed_at DESC,
          c.recorded_at DESC
        LIMIT 1
        """,
        (city, target_date, temperature_metric, expected.source_id, expected.data_version),
    ).fetchone()
    return dict(row) if row else None


def market_bins_for_replacement_seed(
    conn: sqlite3.Connection,
    *,
    city: str,
    target_date: str,
    temperature_metric: str,
) -> tuple[Mapping[str, object], ...]:
    rows = conn.execute(
        """
        SELECT market_slug, token_id, range_label, range_low, range_high
        FROM market_events
        WHERE city = ?
          AND target_date = ?
          AND temperature_metric = ?
          AND token_id IS NOT NULL
          AND range_label IS NOT NULL
        ORDER BY coalesce(range_low, -999), coalesce(range_high, 999), range_label
        """,
        (city, target_date, temperature_metric),
    ).fetchall()
    return tuple(dict(row) for row in rows)


def load_manifest_with_path(path: Path | str) -> RawForecastArtifactManifest:
    manifest = read_manifest(path)
    metadata = dict(manifest.product_metadata)
    metadata.setdefault("manifest_json", str(path))
    return RawForecastArtifactManifest(**{**manifest.to_dict(), "product_metadata": metadata})


def write_seed(path: Path | str, seed: Mapping[str, object]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name(f".{target.name}.{os.getpid()}.{id(seed)}.tmp")
    try:
        temp.write_text(
            json.dumps(dict(seed), sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temp, target)
    finally:
        temp.unlink(missing_ok=True)
