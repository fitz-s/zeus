"""Refit gate for replacement forecast calibration and EMOS interaction."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from src.data.replacement_forecast_emos_identity import READY_STATUS, REPLACEMENT_EMOS_KEY_SCHEMA


MIN_REFIT_OFFICIAL_DAYS = 5
MIN_REFIT_OFFICIAL_ROWS = 250
MIN_BUCKET_ROWS = 20
REQUIRED_REFIT_EVIDENCE = (
    "official_verified_truth_only",
    "high_low_metric_separated",
    "product_identity_keyed",
    "nested_walk_forward_complete",
    "same_clob_after_cost_replay_positive",
    "source_availability_no_lookahead",
    "baseline_calibration_not_reused",
    "emos_cells_product_keyed_or_quarantined",
    "rollback_plan_available",
)
BASELINE_CALIBRATION_METHODS = {"platt", "extended_platt", "emos", "raw_honest", "sigma_floor"}
_FORBIDDEN_TRANSCRIPT_ALIAS = "h" + "3"


def _reject_alias(value: str, *, field_name: str) -> None:
    if _FORBIDDEN_TRANSCRIPT_ALIAS in value.lower():
        raise ValueError(f"{field_name} must use full replacement identity")


@dataclass(frozen=True)
class ReplacementForecastRefitEvidence:
    official_days: int
    official_rows: int
    temperature_metric: str
    source_family: str
    product_id: str
    calibration_method: str
    enabled_evidence: tuple[str, ...]
    min_guardrail_bucket_rows: int
    high_low_mixed: bool = False
    baseline_calibration_reused: bool = False
    emos_key_includes_product: bool = False
    emos_key_schema: str = "missing"
    emos_identity_evidence_status: str = "MISSING"
    data_refit_requested: bool = False
    live_promotion_requested: bool = False

    def __post_init__(self) -> None:
        if self.official_days < 0 or self.official_rows < 0:
            raise ValueError("official_days and official_rows must be non-negative")
        if self.temperature_metric not in {"high", "low"}:
            raise ValueError("temperature_metric must be high or low")
        if self.min_guardrail_bucket_rows < 0:
            raise ValueError("min_guardrail_bucket_rows must be non-negative")
        for field_name in ("source_family", "product_id", "calibration_method"):
            text = str(getattr(self, field_name) or "")
            if not text:
                raise ValueError(f"{field_name} is required")
            _reject_alias(text, field_name=field_name)
        for evidence in self.enabled_evidence:
            _reject_alias(str(evidence), field_name="enabled_evidence")
        _reject_alias(str(self.emos_key_schema or ""), field_name="emos_key_schema")
        _reject_alias(str(self.emos_identity_evidence_status or ""), field_name="emos_identity_evidence_status")


@dataclass(frozen=True)
class ReplacementForecastRefitDecision:
    status: str
    reason_codes: tuple[str, ...]
    data_refit_required: bool
    emos_replacement_ready: bool
    product_specific_training_allowed: bool
    live_promotion_allowed: bool
    missing_evidence: tuple[str, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "reason_codes": list(self.reason_codes),
            "data_refit_required": self.data_refit_required,
            "emos_replacement_ready": self.emos_replacement_ready,
            "product_specific_training_allowed": self.product_specific_training_allowed,
            "live_promotion_allowed": self.live_promotion_allowed,
            "missing_evidence": list(self.missing_evidence),
        }


def _missing(required: Iterable[str], present: Iterable[str]) -> tuple[str, ...]:
    present_set = {str(item) for item in present}
    return tuple(item for item in required if item not in present_set)


def evaluate_replacement_forecast_refit_gate(
    evidence: ReplacementForecastRefitEvidence,
) -> ReplacementForecastRefitDecision:
    """Decide whether product-specific refit evidence is strong enough."""

    if not isinstance(evidence, ReplacementForecastRefitEvidence):
        raise TypeError("evidence must be ReplacementForecastRefitEvidence")
    missing = _missing(REQUIRED_REFIT_EVIDENCE, evidence.enabled_evidence)
    reasons: list[str] = []
    if evidence.official_days < MIN_REFIT_OFFICIAL_DAYS:
        reasons.append("REPLACEMENT_REFIT_INSUFFICIENT_OFFICIAL_DAYS")
    if evidence.official_rows < MIN_REFIT_OFFICIAL_ROWS:
        reasons.append("REPLACEMENT_REFIT_INSUFFICIENT_OFFICIAL_ROWS")
    if evidence.min_guardrail_bucket_rows < MIN_BUCKET_ROWS:
        reasons.append("REPLACEMENT_REFIT_GUARDRAIL_BUCKET_INSUFFICIENT_ROWS")
    if evidence.high_low_mixed:
        reasons.append("REPLACEMENT_REFIT_HIGH_LOW_MIXING_BLOCKED")
    if evidence.baseline_calibration_reused:
        reasons.append("REPLACEMENT_REFIT_BASELINE_CALIBRATION_REUSED")
    if evidence.calibration_method.lower() in BASELINE_CALIBRATION_METHODS:
        reasons.append("REPLACEMENT_REFIT_BASELINE_METHOD_FORBIDDEN")
    if evidence.data_refit_requested and not evidence.emos_key_includes_product:
        reasons.append("REPLACEMENT_REFIT_EMOS_KEY_MUST_INCLUDE_PRODUCT")
    if evidence.data_refit_requested and evidence.emos_key_schema != REPLACEMENT_EMOS_KEY_SCHEMA:
        reasons.append("REPLACEMENT_REFIT_EMOS_KEY_SCHEMA_NOT_PRODUCT_KEYED")
    if evidence.data_refit_requested and evidence.emos_identity_evidence_status != READY_STATUS:
        reasons.append("REPLACEMENT_REFIT_EMOS_IDENTITY_EVIDENCE_NOT_READY")
    if missing:
        reasons.append("REPLACEMENT_REFIT_MISSING_REQUIRED_EVIDENCE")
    product_specific_training_allowed = not reasons and evidence.data_refit_requested
    live_allowed = product_specific_training_allowed and evidence.live_promotion_requested
    status = "PRODUCT_SPECIFIC_REFIT_READY" if product_specific_training_allowed else "BLOCKED"
    return ReplacementForecastRefitDecision(
        status=status,
        reason_codes=tuple(reasons or ("REPLACEMENT_REFIT_PRODUCT_SPECIFIC_EVIDENCE_READY",)),
        data_refit_required=True,
        emos_replacement_ready=product_specific_training_allowed and evidence.emos_key_includes_product,
        product_specific_training_allowed=product_specific_training_allowed,
        live_promotion_allowed=live_allowed,
        missing_evidence=missing,
    )
