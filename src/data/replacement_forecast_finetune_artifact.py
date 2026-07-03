"""Durable fine-tune artifacts for the replacement soft-anchor posterior."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Mapping, Sequence

from src.strategy.openmeteo_ecmwf_ifs9_aifs_finetune import (
    SoftAnchorFineTuneResult,
    SoftAnchorFineTuneRow,
    SoftAnchorGuardrailBucketCoverage,
    SoftAnchorLeaveDayOutFold,
    SoftAnchorParameter,
    evaluate_openmeteo_ecmwf_ifs9_aifs_nested_finetune,
)


ARTIFACT_SCHEMA_VERSION = "replacement_soft_anchor_finetune_artifact_v1"
_FORBIDDEN_TRANSCRIPT_ALIAS = "h" + "3"


@dataclass(frozen=True)
class ReplacementForecastFineTuneArtifact:
    schema_version: str
    generated_at: str
    status: str
    reason_codes: tuple[str, ...]
    result: SoftAnchorFineTuneResult
    row_count: int
    source_path: str | None = None

    @property
    def ready_for_refit(self) -> bool:
        return self.status == "FINE_TUNE_ARTIFACT_READY"

    def as_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "generated_at": self.generated_at,
            "status": self.status,
            "reason_codes": list(self.reason_codes),
            "source_path": self.source_path,
            "row_count": self.row_count,
            "result": fine_tune_result_to_jsonable(self.result),
            "ready_for_refit": self.ready_for_refit,
        }


def _reject_alias(value: str, *, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field_name} is required")
    if _FORBIDDEN_TRANSCRIPT_ALIAS in text.lower():
        raise ValueError(f"{field_name} must use full replacement identity")
    return text


def parameter_key(parameter: SoftAnchorParameter) -> str:
    return f"weight={parameter.anchor_weight:.6f}|sigma_c={parameter.anchor_sigma_c:.6f}"


def parse_parameter_key(value: str) -> SoftAnchorParameter:
    text = _reject_alias(value, field_name="parameter_key")
    parts = dict(part.split("=", 1) for part in text.split("|") if "=" in part)
    if "weight" not in parts or "sigma_c" not in parts:
        raise ValueError("parameter key must contain weight and sigma_c")
    return SoftAnchorParameter(anchor_weight=float(parts["weight"]), anchor_sigma_c=float(parts["sigma_c"]))


def _sequence(value: object, *, field_name: str) -> Sequence[object]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise ValueError(f"{field_name} must be an array")
    return value


def _mapping(value: object, *, field_name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field_name} must be an object")
    return value


def fine_tune_rows_from_payload(payload: Mapping[str, object]) -> tuple[SoftAnchorFineTuneRow, ...]:
    rows_payload = _sequence(payload.get("rows", ()), field_name="rows")
    if not rows_payload:
        raise ValueError("rows must be non-empty")
    rows: list[SoftAnchorFineTuneRow] = []
    for index, item in enumerate(rows_payload):
        row = _mapping(item, field_name=f"rows[{index}]")
        probabilities_payload = _mapping(row.get("probabilities_by_parameter"), field_name=f"rows[{index}].probabilities_by_parameter")
        probabilities: dict[SoftAnchorParameter, Mapping[str, float]] = {}
        for key, probability_map in probabilities_payload.items():
            probabilities[parse_parameter_key(str(key))] = {
                str(bin_id): float(value)
                for bin_id, value in _mapping(probability_map, field_name=f"rows[{index}].probabilities_by_parameter.{key}").items()
            }
        rows.append(
            SoftAnchorFineTuneRow(
                official_date=str(row.get("official_date") or ""),
                city=str(row.get("city") or ""),
                temperature_metric=str(row.get("temperature_metric") or ""),
                bin_id=str(row.get("bin_id") or ""),
                truth_authority=str(row.get("truth_authority") or ""),
                probabilities_by_parameter=probabilities,
                settled_bin_id=str(row.get("settled_bin_id") or ""),
                guardrail_bucket=str(row.get("guardrail_bucket") or "standard"),
            )
        )
    return tuple(rows)


def candidate_grid_from_payload(payload: Mapping[str, object], rows: tuple[SoftAnchorFineTuneRow, ...]) -> tuple[SoftAnchorParameter, ...]:
    if "candidate_grid" not in payload:
        return tuple(sorted(rows[0].probabilities_by_parameter))
    grid_payload = _sequence(payload["candidate_grid"], field_name="candidate_grid")
    grid = tuple(parse_parameter_key(str(item)) for item in grid_payload)
    if not grid:
        raise ValueError("candidate_grid must be non-empty")
    return grid


def fine_tune_result_to_jsonable(result: SoftAnchorFineTuneResult) -> dict[str, object]:
    return {
        "status": result.status,
        "reason_codes": list(result.reason_codes),
        "official_days": result.official_days,
        "official_rows": result.official_rows,
        "candidate_grid": [parameter_key(parameter) for parameter in result.candidate_grid],
        "selected_parameter": None if result.selected_parameter is None else parameter_key(result.selected_parameter),
        "mean_holdout_brier": result.mean_holdout_brier,
        "mean_holdout_log_loss": result.mean_holdout_log_loss,
        "promotion_ready": result.promotion_ready,
        "folds": [
            {
                "holdout_day": fold.holdout_day.isoformat(),
                "selected_parameter": None if fold.selected_parameter is None else parameter_key(fold.selected_parameter),
                "train_row_count": fold.train_row_count,
                "holdout_row_count": fold.holdout_row_count,
                "holdout_brier": fold.holdout_brier,
                "holdout_log_loss": fold.holdout_log_loss,
                "status": fold.status,
                "reason_codes": list(fold.reason_codes),
            }
            for fold in result.folds
        ],
        "guardrail_bucket_coverage": [
            {
                "guardrail_bucket": bucket.guardrail_bucket,
                "row_count": bucket.row_count,
                "status": bucket.status,
                "reason_codes": list(bucket.reason_codes),
            }
            for bucket in result.guardrail_bucket_coverage
        ],
    }


def fine_tune_result_from_jsonable(payload: Mapping[str, object]) -> SoftAnchorFineTuneResult:
    data = _mapping(payload, field_name="fine_tune_result")
    grid = tuple(parse_parameter_key(str(item)) for item in _sequence(data.get("candidate_grid", ()), field_name="candidate_grid"))
    selected_raw = data.get("selected_parameter")
    selected = None if selected_raw in (None, "") else parse_parameter_key(str(selected_raw))
    folds = tuple(
        SoftAnchorLeaveDayOutFold(
            holdout_day=date.fromisoformat(str(_mapping(item, field_name="folds[]").get("holdout_day") or "")),
            selected_parameter=None
            if _mapping(item, field_name="folds[]").get("selected_parameter") in (None, "")
            else parse_parameter_key(str(_mapping(item, field_name="folds[]").get("selected_parameter"))),
            train_row_count=int(_mapping(item, field_name="folds[]").get("train_row_count", 0)),
            holdout_row_count=int(_mapping(item, field_name="folds[]").get("holdout_row_count", 0)),
            holdout_brier=None if _mapping(item, field_name="folds[]").get("holdout_brier") is None else float(_mapping(item, field_name="folds[]").get("holdout_brier")),
            holdout_log_loss=None
            if _mapping(item, field_name="folds[]").get("holdout_log_loss") is None
            else float(_mapping(item, field_name="folds[]").get("holdout_log_loss")),
            status=str(_mapping(item, field_name="folds[]").get("status") or ""),
            reason_codes=tuple(str(reason) for reason in _sequence(_mapping(item, field_name="folds[]").get("reason_codes", ()), field_name="folds[].reason_codes")),
        )
        for item in _sequence(data.get("folds", ()), field_name="folds")
    )
    coverage = tuple(
        SoftAnchorGuardrailBucketCoverage(
            guardrail_bucket=str(_mapping(item, field_name="guardrail_bucket_coverage[]").get("guardrail_bucket") or ""),
            row_count=int(_mapping(item, field_name="guardrail_bucket_coverage[]").get("row_count", 0)),
            status=str(_mapping(item, field_name="guardrail_bucket_coverage[]").get("status") or ""),
            reason_codes=tuple(
                str(reason)
                for reason in _sequence(
                    _mapping(item, field_name="guardrail_bucket_coverage[]").get("reason_codes", ()),
                    field_name="guardrail_bucket_coverage[].reason_codes",
                )
            ),
        )
        for item in _sequence(data.get("guardrail_bucket_coverage", ()), field_name="guardrail_bucket_coverage")
    )
    return SoftAnchorFineTuneResult(
        status=str(data.get("status") or ""),
        reason_codes=tuple(str(reason) for reason in _sequence(data.get("reason_codes", ()), field_name="reason_codes")),
        official_days=int(data.get("official_days", 0)),
        official_rows=int(data.get("official_rows", 0)),
        candidate_grid=grid,
        folds=folds,
        guardrail_bucket_coverage=coverage,
        selected_parameter=selected,
        mean_holdout_brier=None if data.get("mean_holdout_brier") is None else float(data.get("mean_holdout_brier")),
        mean_holdout_log_loss=None if data.get("mean_holdout_log_loss") is None else float(data.get("mean_holdout_log_loss")),
    )


def build_replacement_forecast_finetune_artifact(
    payload: Mapping[str, object],
    *,
    generated_at: datetime | str | None = None,
    source_path: Path | str | None = None,
) -> ReplacementForecastFineTuneArtifact:
    """Evaluate nested fine-tune rows and return a durable artifact envelope."""

    rows = fine_tune_rows_from_payload(payload)
    grid = candidate_grid_from_payload(payload, rows)
    result = evaluate_openmeteo_ecmwf_ifs9_aifs_nested_finetune(
        rows,
        candidate_grid=grid,
        min_official_days=int(payload.get("min_official_days", 5)),
        min_official_rows=int(payload.get("min_official_rows", 250)),
        min_rows_per_guardrail_bucket=int(payload.get("min_rows_per_guardrail_bucket", 20)),
    )
    if generated_at is None:
        generated = datetime.now(timezone.utc)
    elif isinstance(generated_at, datetime):
        generated = generated_at
    else:
        generated = datetime.fromisoformat(str(generated_at).replace("Z", "+00:00"))
    if generated.tzinfo is None or generated.utcoffset() is None:
        raise ValueError("generated_at must be timezone-aware")
    return ReplacementForecastFineTuneArtifact(
        schema_version=ARTIFACT_SCHEMA_VERSION,
        generated_at=generated.astimezone(timezone.utc).isoformat(),
        status="FINE_TUNE_ARTIFACT_READY" if result.promotion_ready else "FINE_TUNE_ARTIFACT_BLOCKED",
        reason_codes=result.reason_codes,
        result=result,
        row_count=len(rows),
        source_path=None if source_path is None else str(source_path),
    )


def write_replacement_forecast_finetune_artifact(artifact: ReplacementForecastFineTuneArtifact, path: Path | str) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(artifact.as_dict(), sort_keys=True, indent=2) + "\n", encoding="utf-8")
