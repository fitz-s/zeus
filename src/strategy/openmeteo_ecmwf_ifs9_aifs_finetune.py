"""Nested fine-tune scaffold for the replacement soft-anchor posterior."""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from typing import Iterable, Mapping, Sequence


VERIFIED_TRUTH_AUTHORITY = "VERIFIED"
MIN_PROMOTION_OFFICIAL_DAYS = 5
MIN_PROMOTION_OFFICIAL_ROWS = 250
MIN_PROMOTION_ROWS_PER_GUARDRAIL_BUCKET = 20
EPSILON = 1e-15


@dataclass(frozen=True, order=True)
class SoftAnchorParameter:
    anchor_weight: float
    anchor_sigma_c: float

    def __post_init__(self) -> None:
        if not 0.0 <= self.anchor_weight <= 1.0:
            raise ValueError("anchor_weight must be in [0, 1]")
        if self.anchor_sigma_c <= 0.0 or not math.isfinite(self.anchor_sigma_c):
            raise ValueError("anchor_sigma_c must be positive and finite")


@dataclass(frozen=True)
class SoftAnchorFineTuneRow:
    official_date: date | str
    city: str
    temperature_metric: str
    bin_id: str
    truth_authority: str
    probabilities_by_parameter: Mapping[SoftAnchorParameter, Mapping[str, float]]
    settled_bin_id: str
    guardrail_bucket: str = "standard"

    def __post_init__(self) -> None:
        if not self.city:
            raise ValueError("city is required")
        if self.temperature_metric not in {"high", "low"}:
            raise ValueError("temperature_metric must be high or low")
        if not self.guardrail_bucket.strip():
            raise ValueError("guardrail_bucket is required")
        if not self.bin_id or not self.settled_bin_id:
            raise ValueError("bin_id and settled_bin_id are required")
        if not self.probabilities_by_parameter:
            raise ValueError("probabilities_by_parameter is required")
        for parameter, probabilities in self.probabilities_by_parameter.items():
            if not isinstance(parameter, SoftAnchorParameter):
                raise TypeError("probabilities_by_parameter keys must be SoftAnchorParameter")
            _probability_for_settlement(probabilities, self.settled_bin_id)

    @property
    def official_day(self) -> date:
        return date.fromisoformat(self.official_date) if isinstance(self.official_date, str) else self.official_date


@dataclass(frozen=True)
class SoftAnchorParameterScore:
    parameter: SoftAnchorParameter
    row_count: int
    brier: float
    log_loss: float


@dataclass(frozen=True)
class SoftAnchorLeaveDayOutFold:
    holdout_day: date
    selected_parameter: SoftAnchorParameter | None
    train_row_count: int
    holdout_row_count: int
    holdout_brier: float | None
    holdout_log_loss: float | None
    status: str
    reason_codes: tuple[str, ...]


@dataclass(frozen=True)
class SoftAnchorGuardrailBucketCoverage:
    guardrail_bucket: str
    row_count: int
    status: str
    reason_codes: tuple[str, ...]


@dataclass(frozen=True)
class SoftAnchorFineTuneResult:
    status: str
    reason_codes: tuple[str, ...]
    official_days: int
    official_rows: int
    candidate_grid: tuple[SoftAnchorParameter, ...]
    folds: tuple[SoftAnchorLeaveDayOutFold, ...]
    guardrail_bucket_coverage: tuple[SoftAnchorGuardrailBucketCoverage, ...]
    selected_parameter: SoftAnchorParameter | None
    mean_holdout_brier: float | None
    mean_holdout_log_loss: float | None

    @property
    def promotion_ready(self) -> bool:
        return self.status == "PROMOTION_EVIDENCE_READY"


def predeclared_soft_anchor_parameter_grid(
    *,
    weights: Sequence[float] = (0.60, 0.70, 0.80, 0.90),
    sigmas_c: Sequence[float] = (2.0, 3.0, 4.0),
) -> tuple[SoftAnchorParameter, ...]:
    if not weights or not sigmas_c:
        raise ValueError("weights and sigmas_c must be non-empty")
    return tuple(SoftAnchorParameter(float(weight), float(sigma)) for weight in weights for sigma in sigmas_c)


def _probability_for_settlement(probabilities: Mapping[str, float], settled_bin_id: str) -> float:
    if settled_bin_id not in probabilities:
        raise ValueError(f"settled_bin_id {settled_bin_id!r} missing from probability vector")
    total = 0.0
    for bin_id, value in probabilities.items():
        if not bin_id:
            raise ValueError("probability bin ids must be non-empty")
        number = float(value)
        if number < 0.0 or not math.isfinite(number):
            raise ValueError("probabilities must be non-negative finite numbers")
        total += number
    if abs(total - 1.0) > 1e-9:
        raise ValueError("probabilities must sum to 1")
    return max(EPSILON, min(1.0 - EPSILON, float(probabilities[settled_bin_id])))


def _score_parameter(rows: Sequence[SoftAnchorFineTuneRow], parameter: SoftAnchorParameter) -> SoftAnchorParameterScore:
    if not rows:
        return SoftAnchorParameterScore(parameter=parameter, row_count=0, brier=math.inf, log_loss=math.inf)
    brier_terms: list[float] = []
    log_terms: list[float] = []
    for row in rows:
        probability = _probability_for_settlement(row.probabilities_by_parameter[parameter], row.settled_bin_id)
        brier_terms.append((1.0 - probability) ** 2)
        log_terms.append(-math.log(probability))
    return SoftAnchorParameterScore(
        parameter=parameter,
        row_count=len(rows),
        brier=sum(brier_terms) / len(brier_terms),
        log_loss=sum(log_terms) / len(log_terms),
    )


def _select_parameter(rows: Sequence[SoftAnchorFineTuneRow], grid: Sequence[SoftAnchorParameter]) -> SoftAnchorParameter | None:
    if not rows:
        return None
    scores = [_score_parameter(rows, parameter) for parameter in grid]
    return min(scores, key=lambda score: (score.log_loss, score.brier, score.parameter.anchor_sigma_c, score.parameter.anchor_weight)).parameter


def evaluate_openmeteo_ecmwf_ifs9_aifs_nested_finetune(
    rows: Iterable[SoftAnchorFineTuneRow],
    *,
    candidate_grid: Sequence[SoftAnchorParameter],
    min_official_days: int = MIN_PROMOTION_OFFICIAL_DAYS,
    min_official_rows: int = MIN_PROMOTION_OFFICIAL_ROWS,
    min_rows_per_guardrail_bucket: int = MIN_PROMOTION_ROWS_PER_GUARDRAIL_BUCKET,
) -> SoftAnchorFineTuneResult:
    """Run leave-day-out parameter selection without using holdout rows to tune."""

    grid = tuple(candidate_grid)
    if not grid:
        raise ValueError("candidate_grid must be non-empty and predeclared")
    if min_rows_per_guardrail_bucket <= 0:
        raise ValueError("min_rows_per_guardrail_bucket must be positive")
    row_tuple = tuple(rows)
    rejected = [row for row in row_tuple if row.truth_authority != VERIFIED_TRUTH_AUTHORITY]
    if rejected:
        return SoftAnchorFineTuneResult(
            status="BLOCKED",
            reason_codes=("REPLACEMENT_FINETUNE_REQUIRES_VERIFIED_TRUTH",),
            official_days=0,
            official_rows=0,
            candidate_grid=grid,
            folds=(),
            guardrail_bucket_coverage=(),
            selected_parameter=None,
            mean_holdout_brier=None,
            mean_holdout_log_loss=None,
        )
    by_day: dict[date, list[SoftAnchorFineTuneRow]] = defaultdict(list)
    for row in row_tuple:
        if set(row.probabilities_by_parameter) != set(grid):
            raise ValueError("every row must contain exactly the predeclared candidate_grid")
        by_day[row.official_day].append(row)
    days = tuple(sorted(by_day))
    folds: list[SoftAnchorLeaveDayOutFold] = []
    for holdout_day in days:
        train_rows = [row for day, day_rows in by_day.items() if day != holdout_day for row in day_rows]
        holdout_rows = tuple(by_day[holdout_day])
        selected = _select_parameter(train_rows, grid)
        if selected is None:
            folds.append(
                SoftAnchorLeaveDayOutFold(
                    holdout_day=holdout_day,
                    selected_parameter=None,
                    train_row_count=0,
                    holdout_row_count=len(holdout_rows),
                    holdout_brier=None,
                    holdout_log_loss=None,
                    status="BLOCKED",
                    reason_codes=("REPLACEMENT_FINETUNE_TRAINING_DAY_MISSING",),
                )
            )
            continue
        score = _score_parameter(holdout_rows, selected)
        folds.append(
            SoftAnchorLeaveDayOutFold(
                holdout_day=holdout_day,
                selected_parameter=selected,
                train_row_count=len(train_rows),
                holdout_row_count=len(holdout_rows),
                holdout_brier=score.brier,
                holdout_log_loss=score.log_loss,
                status="SCORED",
                reason_codes=("REPLACEMENT_FINETUNE_HOLDOUT_SCORED",),
            )
        )

    scored_folds = tuple(fold for fold in folds if fold.status == "SCORED")
    official_days = len(days)
    official_rows = len(row_tuple)
    final_selected = _select_parameter(row_tuple, grid) if row_tuple else None
    metrics = {row.temperature_metric for row in row_tuple}
    bucket_counts: dict[str, int] = defaultdict(int)
    for row in row_tuple:
        bucket_counts[row.guardrail_bucket] += 1
    bucket_coverage = tuple(
        SoftAnchorGuardrailBucketCoverage(
            guardrail_bucket=bucket,
            row_count=count,
            status="PASS" if count >= min_rows_per_guardrail_bucket else "SHADOW_ONLY",
            reason_codes=(
                ("REPLACEMENT_FINETUNE_GUARDRAIL_BUCKET_ROW_COVERAGE_PASS",)
                if count >= min_rows_per_guardrail_bucket
                else ("REPLACEMENT_FINETUNE_GUARDRAIL_BUCKET_INSUFFICIENT_ROWS",)
            ),
        )
        for bucket, count in sorted(bucket_counts.items())
    )
    reasons: list[str] = []
    if len(metrics) > 1:
        reasons.append("REPLACEMENT_FINETUNE_HIGH_LOW_METRIC_MIXING_BLOCKED")
    if official_days < min_official_days:
        reasons.append("REPLACEMENT_FINETUNE_INSUFFICIENT_OFFICIAL_DAYS")
    if official_rows < min_official_rows:
        reasons.append("REPLACEMENT_FINETUNE_INSUFFICIENT_OFFICIAL_ROWS")
    if len(scored_folds) != official_days:
        reasons.append("REPLACEMENT_FINETUNE_INCOMPLETE_FOLDS")
    if any(bucket.status != "PASS" for bucket in bucket_coverage):
        reasons.append("REPLACEMENT_FINETUNE_GUARDRAIL_BUCKET_COVERAGE_INSUFFICIENT")
    status = "PROMOTION_EVIDENCE_READY" if not reasons else "SHADOW_EVIDENCE_ONLY"
    if "REPLACEMENT_FINETUNE_HIGH_LOW_METRIC_MIXING_BLOCKED" in reasons:
        status = "BLOCKED"
    mean_brier = sum(float(fold.holdout_brier) for fold in scored_folds) / len(scored_folds) if scored_folds else None
    mean_log_loss = sum(float(fold.holdout_log_loss) for fold in scored_folds) / len(scored_folds) if scored_folds else None
    return SoftAnchorFineTuneResult(
        status=status,
        reason_codes=tuple(reasons or ("REPLACEMENT_FINETUNE_NESTED_WALK_FORWARD_READY",)),
        official_days=official_days,
        official_rows=official_rows,
        candidate_grid=grid,
        folds=tuple(folds),
        guardrail_bucket_coverage=bucket_coverage,
        selected_parameter=final_selected,
        mean_holdout_brier=mean_brier,
        mean_holdout_log_loss=mean_log_loss,
    )
