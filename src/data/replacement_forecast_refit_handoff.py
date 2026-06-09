"""Durable handoff artifact for replacement forecast product-specific refit."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping, Sequence

from src.data.replacement_forecast_bundle_reader import HIGH_DATA_VERSION, LOW_DATA_VERSION
from src.data.replacement_forecast_emos_identity import (
    READY_STATUS as EMOS_READY_STATUS,
    REPLACEMENT_EMOS_KEY_SCHEMA,
    ReplacementForecastEmosIdentityEvidence,
    evaluate_replacement_forecast_emos_identity,
    replacement_emos_cell_key,
)
from src.data.replacement_forecast_finetune_artifact import (
    fine_tune_result_from_jsonable,
    parameter_key,
)
from src.data.replacement_forecast_readiness import PRODUCT_ID, SOURCE_ID
from src.data.replacement_forecast_refit_gate import (
    REQUIRED_REFIT_EVIDENCE,
    ReplacementForecastRefitDecision,
    ReplacementForecastRefitEvidence,
    evaluate_replacement_forecast_refit_gate,
)


HANDOFF_SCHEMA_VERSION = "replacement_forecast_refit_handoff_v1"
CALIBRATION_METHOD = "soft_anchor_product_specific_nested_refit"
SOURCE_FAMILY = "derived_posterior"
_FORBIDDEN_TRANSCRIPT_ALIAS = "h" + "3"


@dataclass(frozen=True)
class ReplacementForecastRefitHandoff:
    schema_version: str
    generated_at: str
    status: str
    reason_codes: tuple[str, ...]
    city: str
    season: str
    metric: str
    source_family: str
    source_id: str
    product_id: str
    data_version: str
    calibration_method: str
    emos_cell_key: str
    emos_key_schema: str
    selected_parameter: str | None
    mean_holdout_brier: float | None
    mean_holdout_log_loss: float | None
    official_days: int
    official_rows: int
    min_guardrail_bucket_rows: int
    refit_decision: ReplacementForecastRefitDecision
    live_promotion_allowed: bool = False
    training_scope: str = "replacement_product_specific_only"
    baseline_calibration_reused: bool = False

    @property
    def ready_for_product_refit(self) -> bool:
        return self.status == "REFIT_HANDOFF_READY"

    def as_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "generated_at": self.generated_at,
            "status": self.status,
            "reason_codes": list(self.reason_codes),
            "city": self.city,
            "season": self.season,
            "metric": self.metric,
            "source_family": self.source_family,
            "source_id": self.source_id,
            "product_id": self.product_id,
            "data_version": self.data_version,
            "calibration_method": self.calibration_method,
            "emos_cell_key": self.emos_cell_key,
            "emos_key_schema": self.emos_key_schema,
            "selected_parameter": self.selected_parameter,
            "mean_holdout_brier": self.mean_holdout_brier,
            "mean_holdout_log_loss": self.mean_holdout_log_loss,
            "official_days": self.official_days,
            "official_rows": self.official_rows,
            "min_guardrail_bucket_rows": self.min_guardrail_bucket_rows,
            "training_scope": self.training_scope,
            "baseline_calibration_reused": self.baseline_calibration_reused,
            "live_promotion_allowed": self.live_promotion_allowed,
            "ready_for_product_refit": self.ready_for_product_refit,
            "refit_decision": self.refit_decision.as_dict(),
        }


def _reject_alias(value: str, *, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field_name} is required")
    if _FORBIDDEN_TRANSCRIPT_ALIAS in text.lower():
        raise ValueError(f"{field_name} must use full replacement identity")
    return text


def _metric(value: str) -> str:
    metric = _reject_alias(value, field_name="metric")
    if metric not in {"high", "low"}:
        raise ValueError("metric must be high or low")
    return metric


def _data_version_for_metric(metric: str) -> str:
    return HIGH_DATA_VERSION if metric == "high" else LOW_DATA_VERSION


def _generated_at(value: datetime | str | None) -> datetime:
    if value is None:
        parsed = datetime.now(timezone.utc)
    elif isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("generated_at must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def _mapping(value: object, *, field_name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field_name} must be an object")
    return value


def _sequence(value: object, *, field_name: str) -> Sequence[object]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise ValueError(f"{field_name} must be an array")
    return value


def _fine_tune_result_payload(fine_tune_artifact: Mapping[str, object]) -> Mapping[str, object]:
    if fine_tune_artifact.get("schema_version") != "replacement_soft_anchor_finetune_artifact_v1":
        raise ValueError("fine_tune_artifact schema_version is not replacement_soft_anchor_finetune_artifact_v1")
    return _mapping(fine_tune_artifact.get("result"), field_name="fine_tune_artifact.result")


def refit_decision_from_handoff_payload(payload: Mapping[str, object]) -> ReplacementForecastRefitDecision:
    """Parse a ready, non-live handoff artifact into a refit gate decision."""

    handoff = _mapping(payload, field_name="refit_handoff")
    if handoff.get("schema_version") != HANDOFF_SCHEMA_VERSION:
        raise ValueError("refit_handoff schema_version is not replacement_forecast_refit_handoff_v1")
    if handoff.get("status") != "REFIT_HANDOFF_READY":
        raise ValueError("refit_handoff status must be REFIT_HANDOFF_READY")
    if handoff.get("ready_for_product_refit") is not True:
        raise ValueError("refit_handoff must be ready_for_product_refit")
    if handoff.get("live_promotion_allowed") is not False:
        raise ValueError("refit_handoff live_promotion_allowed must be false")
    if handoff.get("training_scope") != "replacement_product_specific_only":
        raise ValueError("refit_handoff training_scope must be replacement_product_specific_only")
    if handoff.get("baseline_calibration_reused") is not False:
        raise ValueError("refit_handoff must not reuse baseline calibration")

    for field_name in ("source_family", "source_id", "product_id", "data_version", "calibration_method", "emos_cell_key"):
        _reject_alias(str(handoff.get(field_name) or ""), field_name=f"refit_handoff.{field_name}")
    _metric(str(handoff.get("metric") or ""))

    raw_decision = _mapping(handoff.get("refit_decision"), field_name="refit_handoff.refit_decision")
    decision = ReplacementForecastRefitDecision(
        status=str(raw_decision.get("status") or ""),
        reason_codes=tuple(
            str(item)
            for item in _sequence(raw_decision.get("reason_codes", ()), field_name="refit_handoff.refit_decision.reason_codes")
        ),
        data_refit_required=bool(raw_decision.get("data_refit_required", False)),
        emos_replacement_ready=bool(raw_decision.get("emos_replacement_ready", False)),
        product_specific_training_allowed=bool(raw_decision.get("product_specific_training_allowed", False)),
        live_promotion_allowed=bool(raw_decision.get("live_promotion_allowed", False)),
        missing_evidence=tuple(
            str(item)
            for item in _sequence(raw_decision.get("missing_evidence", ()), field_name="refit_handoff.refit_decision.missing_evidence")
        ),
    )
    if decision.status != "PRODUCT_SPECIFIC_REFIT_READY":
        raise ValueError("refit_handoff refit_decision must be PRODUCT_SPECIFIC_REFIT_READY")
    if not decision.product_specific_training_allowed:
        raise ValueError("refit_handoff refit_decision must allow product-specific training")
    if not decision.emos_replacement_ready:
        raise ValueError("refit_handoff refit_decision must have EMOS replacement ready")
    if decision.live_promotion_allowed:
        raise ValueError("refit_handoff refit_decision must not allow live promotion")
    if decision.missing_evidence:
        raise ValueError("refit_handoff refit_decision must not have missing evidence")
    return decision


def build_replacement_forecast_refit_handoff(
    *,
    fine_tune_artifact: Mapping[str, object],
    city: str,
    season: str,
    metric: str,
    generated_at: datetime | str | None = None,
    source_family: str = SOURCE_FAMILY,
    source_id: str = SOURCE_ID,
    product_id: str = PRODUCT_ID,
    data_version: str | None = None,
    live_promotion_requested: bool = False,
) -> ReplacementForecastRefitHandoff:
    """Build an inspectable, non-live handoff for replacement-only data refit."""

    clean_city = _reject_alias(city, field_name="city")
    clean_season = _reject_alias(season, field_name="season")
    clean_metric = _metric(metric)
    clean_source_family = _reject_alias(source_family, field_name="source_family")
    clean_source_id = _reject_alias(source_id, field_name="source_id")
    clean_product_id = _reject_alias(product_id, field_name="product_id")
    clean_data_version = _reject_alias(data_version or _data_version_for_metric(clean_metric), field_name="data_version")
    generated = _generated_at(generated_at)
    result = fine_tune_result_from_jsonable(_fine_tune_result_payload(fine_tune_artifact))
    selected_parameter = None if result.selected_parameter is None else parameter_key(result.selected_parameter)
    bucket_rows = [bucket.row_count for bucket in result.guardrail_bucket_coverage]
    min_bucket_rows = min(bucket_rows) if bucket_rows else 0
    cell_key = replacement_emos_cell_key(
        city=clean_city,
        season=clean_season,
        metric=clean_metric,
        source_family=clean_source_family,
        source_id=clean_source_id,
        product_id=clean_product_id,
        data_version=clean_data_version,
    )
    emos_decision = evaluate_replacement_forecast_emos_identity(
        ReplacementForecastEmosIdentityEvidence(
            cell_key=cell_key,
            key_schema=REPLACEMENT_EMOS_KEY_SCHEMA,
            city=clean_city,
            season=clean_season,
            metric=clean_metric,
            source_family=clean_source_family,
            source_id=clean_source_id,
            product_id=clean_product_id,
            data_version=clean_data_version,
            calibration_method=CALIBRATION_METHOD,
        )
    )
    refit_decision = evaluate_replacement_forecast_refit_gate(
        ReplacementForecastRefitEvidence(
            official_days=result.official_days,
            official_rows=result.official_rows,
            temperature_metric=clean_metric,
            source_family=clean_source_family,
            product_id=clean_product_id,
            calibration_method=CALIBRATION_METHOD,
            enabled_evidence=tuple(REQUIRED_REFIT_EVIDENCE),
            min_guardrail_bucket_rows=min_bucket_rows,
            high_low_mixed=False,
            baseline_calibration_reused=False,
            emos_key_includes_product=emos_decision.product_keyed,
            emos_key_schema=emos_decision.key_schema,
            emos_identity_evidence_status=emos_decision.status,
            data_refit_requested=True,
            live_promotion_requested=False,
        )
    )
    reasons: list[str] = []
    if not bool(fine_tune_artifact.get("ready_for_refit", False)):
        reasons.append("REPLACEMENT_REFIT_HANDOFF_FINE_TUNE_ARTIFACT_NOT_READY")
    if not result.promotion_ready:
        reasons.extend(result.reason_codes)
    if not emos_decision.ready:
        reasons.extend(emos_decision.reason_codes)
    if not refit_decision.product_specific_training_allowed:
        reasons.extend(refit_decision.reason_codes)
    if live_promotion_requested:
        reasons.append("REPLACEMENT_REFIT_HANDOFF_LIVE_PROMOTION_NOT_ALLOWED")
    unique_reasons = tuple(dict.fromkeys(reasons))
    return ReplacementForecastRefitHandoff(
        schema_version=HANDOFF_SCHEMA_VERSION,
        generated_at=generated.isoformat(),
        status="REFIT_HANDOFF_READY" if not unique_reasons else "REFIT_HANDOFF_BLOCKED",
        reason_codes=unique_reasons or ("REPLACEMENT_REFIT_HANDOFF_READY",),
        city=clean_city,
        season=clean_season,
        metric=clean_metric,
        source_family=clean_source_family,
        source_id=clean_source_id,
        product_id=clean_product_id,
        data_version=clean_data_version,
        calibration_method=CALIBRATION_METHOD,
        emos_cell_key=cell_key,
        emos_key_schema=REPLACEMENT_EMOS_KEY_SCHEMA,
        selected_parameter=selected_parameter,
        mean_holdout_brier=result.mean_holdout_brier,
        mean_holdout_log_loss=result.mean_holdout_log_loss,
        official_days=result.official_days,
        official_rows=result.official_rows,
        min_guardrail_bucket_rows=min_bucket_rows,
        refit_decision=refit_decision,
        live_promotion_allowed=False,
    )


def read_replacement_forecast_refit_handoff_input(path: Path | str) -> Mapping[str, object]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return _mapping(payload, field_name="refit_handoff_input")


def write_replacement_forecast_refit_handoff(handoff: ReplacementForecastRefitHandoff, path: Path | str) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(handoff.as_dict(), sort_keys=True, indent=2) + "\n", encoding="utf-8")
