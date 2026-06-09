"""Compose replacement forecast promotion evidence from scored reports."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from src.data.replacement_forecast_before_after_report import ReplacementForecastBeforeAfterReport
from src.data.replacement_forecast_guardrail_report import ReplacementForecastGuardrailReport
from src.data.replacement_forecast_refit_gate import ReplacementForecastRefitDecision
from src.data.replacement_forecast_runtime_policy import (
    EXPECTED_ANCHOR_SIGMA_C,
    EXPECTED_ANCHOR_WEIGHT,
    ReplacementForecastPromotionEvidence,
)
from src.strategy.openmeteo_ecmwf_ifs9_aifs_finetune import SoftAnchorFineTuneResult


VERIFIED_TRUTH_AUTHORITY = "VERIFIED"


@dataclass(frozen=True)
class ReplacementForecastQLcbCoverageRow:
    official_date: str
    city: str
    temperature_metric: str
    guardrail_bucket: str
    truth_authority: str
    scored: bool
    covered_by_q_lcb: bool

    def __post_init__(self) -> None:
        for field_name in ("official_date", "city", "guardrail_bucket", "truth_authority"):
            if not str(getattr(self, field_name) or "").strip():
                raise ValueError(f"{field_name} is required")
        if self.temperature_metric not in {"high", "low"}:
            raise ValueError("temperature_metric must be high or low")


@dataclass(frozen=True)
class ReplacementForecastQLcbCoverageReport:
    status: str
    reason_codes: tuple[str, ...]
    official_rows: int
    covered_rows: int
    coverage: float
    row_exclusion_count: int

    @property
    def passable_for_promotion(self) -> bool:
        return self.status == "PASS"

    def as_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "reason_codes": list(self.reason_codes),
            "official_rows": self.official_rows,
            "covered_rows": self.covered_rows,
            "coverage": self.coverage,
            "row_exclusion_count": self.row_exclusion_count,
            "passable_for_promotion": self.passable_for_promotion,
        }


def build_replacement_forecast_q_lcb_coverage_report(
    rows: Iterable[ReplacementForecastQLcbCoverageRow],
    *,
    min_official_rows: int = 250,
    min_coverage: float = 0.95,
) -> ReplacementForecastQLcbCoverageReport:
    """Compute empirical q_lcb coverage from official scored rows."""

    if min_official_rows <= 0:
        raise ValueError("min_official_rows must be positive")
    if not 0.0 <= min_coverage <= 1.0:
        raise ValueError("min_coverage must be in [0, 1]")
    row_tuple = tuple(rows)
    official = tuple(row for row in row_tuple if row.scored and row.truth_authority == VERIFIED_TRUTH_AUTHORITY)
    excluded = len(row_tuple) - len(official)
    covered = sum(1 for row in official if row.covered_by_q_lcb)
    coverage = covered / len(official) if official else 0.0
    reasons: list[str] = []
    if not official:
        reasons.append("REPLACEMENT_Q_LCB_COVERAGE_NO_OFFICIAL_ROWS")
    if len(official) < min_official_rows:
        reasons.append("REPLACEMENT_Q_LCB_COVERAGE_INSUFFICIENT_ROWS")
    if excluded:
        reasons.append("REPLACEMENT_Q_LCB_COVERAGE_HAS_ROW_EXCLUSIONS")
    if coverage < min_coverage:
        reasons.append("REPLACEMENT_Q_LCB_COVERAGE_BELOW_THRESHOLD")
    return ReplacementForecastQLcbCoverageReport(
        status="PASS" if not reasons else ("BLOCKED" if not official else "SHADOW_ONLY"),
        reason_codes=tuple(reasons or ("REPLACEMENT_Q_LCB_COVERAGE_PASS",)),
        official_rows=len(official),
        covered_rows=covered,
        coverage=coverage,
        row_exclusion_count=excluded,
    )


@dataclass(frozen=True)
class ReplacementForecastPromotionEvidenceBuildReport:
    status: str
    reason_codes: tuple[str, ...]
    promotion_evidence: ReplacementForecastPromotionEvidence

    @property
    def promotion_allowed(self) -> bool:
        return self.status == "PROMOTION_EVIDENCE_READY"

    def as_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "reason_codes": list(self.reason_codes),
            "promotion_evidence": {
                "official_days": self.promotion_evidence.official_days,
                "official_rows": self.promotion_evidence.official_rows,
                "after_cost_pnl": self.promotion_evidence.after_cost_pnl,
                "q_lcb_coverage": self.promotion_evidence.q_lcb_coverage,
                "anti_lookahead_violations": self.promotion_evidence.anti_lookahead_violations,
                "source_availability_violations": self.promotion_evidence.source_availability_violations,
                "unresolved_regression_clusters": self.promotion_evidence.unresolved_regression_clusters,
                "same_clob_replay_passed": self.promotion_evidence.same_clob_replay_passed,
                "nested_walk_forward_passed": self.promotion_evidence.nested_walk_forward_passed,
                "same_clob_replay_scored_rows": self.promotion_evidence.same_clob_replay_scored_rows,
                "same_clob_replay_blocked_rows": self.promotion_evidence.same_clob_replay_blocked_rows,
                "fee_depth_fill_evidence_passed": self.promotion_evidence.fee_depth_fill_evidence_passed,
                "unit_pnl_only": self.promotion_evidence.unit_pnl_only,
                "nested_holdout_brier": self.promotion_evidence.nested_holdout_brier,
                "nested_holdout_log_loss": self.promotion_evidence.nested_holdout_log_loss,
                "nested_selected_anchor_weight": self.promotion_evidence.nested_selected_anchor_weight,
                "nested_selected_anchor_sigma_c": self.promotion_evidence.nested_selected_anchor_sigma_c,
                "nested_guardrail_bucket_count": self.promotion_evidence.nested_guardrail_bucket_count,
                "nested_guardrail_bucket_min_rows": self.promotion_evidence.nested_guardrail_bucket_min_rows,
                "product_specific_refit_passed": self.promotion_evidence.product_specific_refit_passed,
            },
            "promotion_allowed": self.promotion_allowed,
        }


def build_replacement_forecast_promotion_evidence(
    *,
    before_after_report: ReplacementForecastBeforeAfterReport,
    guardrail_report: ReplacementForecastGuardrailReport,
    q_lcb_coverage_report: ReplacementForecastQLcbCoverageReport,
    fine_tune_result: SoftAnchorFineTuneResult,
    refit_decision: ReplacementForecastRefitDecision,
) -> ReplacementForecastPromotionEvidenceBuildReport:
    """Build runtime promotion evidence from report objects, not hand-authored fields."""

    if not isinstance(before_after_report, ReplacementForecastBeforeAfterReport):
        raise TypeError("before_after_report must be ReplacementForecastBeforeAfterReport")
    if not isinstance(guardrail_report, ReplacementForecastGuardrailReport):
        raise TypeError("guardrail_report must be ReplacementForecastGuardrailReport")
    if not isinstance(q_lcb_coverage_report, ReplacementForecastQLcbCoverageReport):
        raise TypeError("q_lcb_coverage_report must be ReplacementForecastQLcbCoverageReport")
    if not isinstance(fine_tune_result, SoftAnchorFineTuneResult):
        raise TypeError("fine_tune_result must be SoftAnchorFineTuneResult")
    if not isinstance(refit_decision, ReplacementForecastRefitDecision):
        raise TypeError("refit_decision must be ReplacementForecastRefitDecision")

    selected = fine_tune_result.selected_parameter
    bucket_rows = [bucket.row_count for bucket in fine_tune_result.guardrail_bucket_coverage]
    same_clob_passed = (
        guardrail_report.status == "PASS"
        and guardrail_report.blocked_rows == 0
        and guardrail_report.scored_rows >= before_after_report.official_rows
    )
    evidence = ReplacementForecastPromotionEvidence(
        official_days=before_after_report.official_days,
        official_rows=before_after_report.official_rows,
        after_cost_pnl=0.0 if before_after_report.after_cost_delta is None else float(before_after_report.after_cost_delta),
        q_lcb_coverage=float(q_lcb_coverage_report.coverage),
        anti_lookahead_violations=0 if guardrail_report.blocked_rows == 0 else guardrail_report.blocked_rows,
        source_availability_violations=0 if guardrail_report.blocked_rows == 0 else guardrail_report.blocked_rows,
        unresolved_regression_clusters=len(guardrail_report.unresolved_regression_clusters),
        same_clob_replay_passed=same_clob_passed,
        nested_walk_forward_passed=fine_tune_result.promotion_ready,
        same_clob_replay_scored_rows=guardrail_report.scored_rows,
        same_clob_replay_blocked_rows=guardrail_report.blocked_rows,
        fee_depth_fill_evidence_passed=same_clob_passed,
        unit_pnl_only=False,
        nested_holdout_brier=fine_tune_result.mean_holdout_brier,
        nested_holdout_log_loss=fine_tune_result.mean_holdout_log_loss,
        nested_selected_anchor_weight=None if selected is None else selected.anchor_weight,
        nested_selected_anchor_sigma_c=None if selected is None else selected.anchor_sigma_c,
        nested_guardrail_bucket_count=len(fine_tune_result.guardrail_bucket_coverage),
        nested_guardrail_bucket_min_rows=min(bucket_rows) if bucket_rows else 0,
        product_specific_refit_passed=refit_decision.product_specific_training_allowed,
    )
    reasons: list[str] = []
    if before_after_report.status != "REPORT_READY":
        reasons.extend(before_after_report.reason_codes)
    if before_after_report.bucket_regressions:
        reasons.append("REPLACEMENT_PROMOTION_BEFORE_AFTER_BUCKET_REGRESSIONS")
    if guardrail_report.status != "PASS":
        reasons.extend(guardrail_report.reason_codes)
    if not q_lcb_coverage_report.passable_for_promotion:
        reasons.extend(q_lcb_coverage_report.reason_codes)
    if not fine_tune_result.promotion_ready:
        reasons.extend(fine_tune_result.reason_codes)
    if selected is None:
        reasons.append("REPLACEMENT_PROMOTION_FINE_TUNE_PARAMETER_MISSING")
    elif selected.anchor_weight != EXPECTED_ANCHOR_WEIGHT or selected.anchor_sigma_c != EXPECTED_ANCHOR_SIGMA_C:
        reasons.append("REPLACEMENT_PROMOTION_FINE_TUNE_PARAMETER_MISMATCH")
    if not refit_decision.product_specific_training_allowed:
        reasons.extend(refit_decision.reason_codes)
    reasons.extend(evidence.blocking_reason_codes())
    unique_reasons = tuple(dict.fromkeys(reasons))
    return ReplacementForecastPromotionEvidenceBuildReport(
        status="PROMOTION_EVIDENCE_READY" if not unique_reasons else "SHADOW_PROMOTION_BLOCKED",
        reason_codes=unique_reasons or ("REPLACEMENT_PROMOTION_EVIDENCE_READY",),
        promotion_evidence=evidence,
    )
