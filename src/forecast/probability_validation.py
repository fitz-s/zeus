"""Chronological, full-vector validation for precomputed probability candidates.

This module validates and scores candidate probability vectors produced by the
canonical probability path.  It neither fits nor transforms probabilities, and
does not read or write any runtime state.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from random import Random
from statistics import fmean
from typing import Mapping, Sequence

from src.calibration.scoring import (
    brier_score,
    categorical_log_loss,
    ranked_probability_score,
    validate_probability_group,
)


MIN_TRAINING_TARGET_DATES = 5
MIN_COMPARISON_TARGET_DATES = 30


class ProbabilityValidationError(ValueError):
    """Raised when a validation input lacks a complete causal probability proof."""


@dataclass(frozen=True)
class ProbabilityVector:
    """One canonical ordered probability vector available before a decision."""

    bin_ids: tuple[str, ...]
    values: tuple[float, ...]
    available_at: datetime


@dataclass(frozen=True)
class MarketVector:
    """A market comparator with its decision-time quality proof."""

    bin_ids: tuple[str, ...]
    values: tuple[float, ...]
    observed_at: datetime
    quality_proven: bool


@dataclass(frozen=True)
class ProbabilityValidationCase:
    """One settled categorical target evaluated from its decision-time vectors."""

    city: str
    metric: str
    target_date: date
    decision_at: datetime
    label_known_at: datetime
    bin_ids: tuple[str, ...]
    winner_index: int
    candidates: Mapping[str, ProbabilityVector]
    baseline: ProbabilityVector
    market: MarketVector | None = None


@dataclass(frozen=True)
class VectorScores:
    """Proper full-vector scores; lower is better for every component."""

    brier: float
    log_loss: float
    rps: float


@dataclass(frozen=True)
class ScoreSummary:
    case_count: int
    brier: float | None
    log_loss: float | None
    rps: float | None


@dataclass(frozen=True)
class PairedBootstrapInterval:
    mean_delta: float | None
    ci_lower: float | None
    ci_upper: float | None
    date_count: int
    group_count: int
    supported: bool


@dataclass(frozen=True)
class PairedComparison:
    """Selected minus comparator, resampled by UTC target-date blocks."""

    comparator: str
    paired_case_count: int
    total_case_count: int
    coverage: float
    date_count: int
    group_count: int
    brier: PairedBootstrapInterval
    log_loss: PairedBootstrapInterval
    rps: PairedBootstrapInterval


@dataclass(frozen=True)
class SelectionEvaluation:
    city: str
    metric: str
    target_date: str
    selected_name: str
    training_target_date_count: int
    training_case_count: int
    selected_scores: VectorScores
    baseline_scores: VectorScores
    candidate_scores: Mapping[str, VectorScores]
    market_scores: VectorScores | None


@dataclass(frozen=True)
class ProbabilityValidationResult:
    """Serializable, audit-oriented result of chronological candidate validation."""

    predeclared_candidates: tuple[str, ...]
    evaluations: tuple[SelectionEvaluation, ...]
    selected: ScoreSummary
    baseline: ScoreSummary
    candidates: Mapping[str, ScoreSummary]
    market: ScoreSummary
    selected_vs_baseline: PairedComparison
    selected_vs_candidates: Mapping[str, PairedComparison]
    selected_vs_market: PairedComparison
    market_coverage: float
    market_date_count: int
    market_lead_supported: bool
    best_tested_direction: str | None


def _utc(value: datetime, *, field: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ProbabilityValidationError(f"{field} must be a timezone-aware datetime")
    return value.astimezone(timezone.utc)


def _validate_bin_ids(bin_ids: tuple[str, ...], *, field: str) -> None:
    if not isinstance(bin_ids, tuple) or not bin_ids:
        raise ProbabilityValidationError(f"{field} must be a non-empty ordered tuple")
    if any(not isinstance(bin_id, str) or not bin_id.strip() for bin_id in bin_ids):
        raise ProbabilityValidationError(f"{field} contains an empty bin id")
    if len(set(bin_ids)) != len(bin_ids):
        raise ProbabilityValidationError(f"{field} contains duplicate bin ids")


def _validate_vector(
    vector: ProbabilityVector | MarketVector,
    *,
    canonical_bin_ids: tuple[str, ...],
    decision_at: datetime,
    field: str,
) -> None:
    if vector.bin_ids != canonical_bin_ids:
        raise ProbabilityValidationError(f"{field} bin topology does not match the case")
    if not isinstance(vector.values, tuple) or len(vector.values) != len(canonical_bin_ids):
        raise ProbabilityValidationError(f"{field} values do not match the case topology")
    if any(isinstance(value, bool) or not isinstance(value, (int, float)) for value in vector.values):
        raise ProbabilityValidationError(f"{field} values must be finite real numbers")
    try:
        validate_probability_group(vector.values)
    except (TypeError, ValueError) as exc:
        raise ProbabilityValidationError(f"{field} is not a valid probability group: {exc}") from exc
    available_at = _utc(
        vector.observed_at if isinstance(vector, MarketVector) else vector.available_at,
        field=f"{field}.available_at",
    )
    if available_at > decision_at:
        raise ProbabilityValidationError(f"{field} was unavailable at decision time")


def _validate_case(case: ProbabilityValidationCase, predeclared: frozenset[str]) -> None:
    if not isinstance(case.target_date, date) or isinstance(case.target_date, datetime):
        raise ProbabilityValidationError("target_date must be a date")
    if not isinstance(case.city, str) or not case.city.strip():
        raise ProbabilityValidationError("city must be non-empty")
    if not isinstance(case.metric, str) or not case.metric.strip():
        raise ProbabilityValidationError("metric must be non-empty")
    _validate_bin_ids(case.bin_ids, field="case.bin_ids")
    decision_at = _utc(case.decision_at, field="decision_at")
    label_known_at = _utc(case.label_known_at, field="label_known_at")
    if label_known_at <= decision_at:
        raise ProbabilityValidationError("label_known_at must be after decision_at")
    if isinstance(case.winner_index, bool) or not isinstance(case.winner_index, int):
        raise ProbabilityValidationError("winner_index must be an integer")
    if not 0 <= case.winner_index < len(case.bin_ids):
        raise ProbabilityValidationError("winner_index is outside the canonical bin topology")
    _validate_vector(
        case.baseline,
        canonical_bin_ids=case.bin_ids,
        decision_at=decision_at,
        field="baseline",
    )
    if not isinstance(case.candidates, Mapping):
        raise ProbabilityValidationError("candidates must be a mapping")
    unknown = set(case.candidates) - predeclared
    if unknown:
        raise ProbabilityValidationError(f"candidates were not predeclared: {sorted(unknown)!r}")
    for name, vector in case.candidates.items():
        if not isinstance(name, str) or not name.strip():
            raise ProbabilityValidationError("candidate name must be non-empty")
        _validate_vector(
            vector,
            canonical_bin_ids=case.bin_ids,
            decision_at=decision_at,
            field=f"candidate[{name!r}]",
        )
    if case.market is not None:
        if case.market.quality_proven is not True:
            raise ProbabilityValidationError("market vector lacks complete quality proof")
        _validate_vector(
            case.market,
            canonical_bin_ids=case.bin_ids,
            decision_at=decision_at,
            field="market",
        )


def _scores(vector: ProbabilityVector | MarketVector, winner_index: int) -> VectorScores:
    return VectorScores(
        brier=brier_score(vector.values, winner_index),
        log_loss=categorical_log_loss(vector.values, winner_index),
        rps=ranked_probability_score(vector.values, winner_index),
    )


def _summary(scores: Sequence[VectorScores]) -> ScoreSummary:
    if not scores:
        return ScoreSummary(case_count=0, brier=None, log_loss=None, rps=None)
    return ScoreSummary(
        case_count=len(scores),
        brier=fmean(score.brier for score in scores),
        log_loss=fmean(score.log_loss for score in scores),
        rps=fmean(score.rps for score in scores),
    )


def _bootstrap_interval(
    grouped_deltas: Mapping[str, Sequence[float]],
    *,
    seed: int,
    resamples: int,
) -> PairedBootstrapInterval:
    target_dates = tuple(sorted(grouped_deltas))
    date_values = tuple(fmean(grouped_deltas[target_date]) for target_date in target_dates)
    date_count = len(target_dates)
    if not date_values:
        return PairedBootstrapInterval(None, None, None, 0, 0, False)
    mean_delta = fmean(date_values)
    if resamples < 1:
        raise ProbabilityValidationError("bootstrap_resamples must be positive")
    rng = Random(seed)
    samples = sorted(
        fmean(date_values[rng.randrange(date_count)] for _ in range(date_count))
        for _ in range(resamples)
    )
    lower = samples[max(0, int(0.025 * (resamples - 1)))]
    upper = samples[min(resamples - 1, int(0.975 * (resamples - 1)))]
    return PairedBootstrapInterval(
        mean_delta=mean_delta,
        ci_lower=lower,
        ci_upper=upper,
        date_count=date_count,
        group_count=date_count,
        supported=date_count >= MIN_COMPARISON_TARGET_DATES and upper < 0.0,
    )


def _paired_comparison(
    evaluations: Sequence[SelectionEvaluation],
    *,
    comparator: str,
    seed: int,
    resamples: int,
) -> PairedComparison:
    grouped: dict[str, dict[str, list[float]]] = {
        "brier": {},
        "log_loss": {},
        "rps": {},
    }
    paired_cases = 0
    for evaluation in evaluations:
        if comparator == "baseline":
            other = evaluation.baseline_scores
        elif comparator == "market":
            other = evaluation.market_scores
        else:
            other = evaluation.candidate_scores.get(comparator)
        if other is None:
            continue
        paired_cases += 1
        for metric in grouped:
            delta = getattr(evaluation.selected_scores, metric) - getattr(other, metric)
            grouped[metric].setdefault(evaluation.target_date, []).append(delta)
    total = len(evaluations)
    coverage = paired_cases / total if total else 0.0
    intervals = {
        metric: _bootstrap_interval(
            grouped[metric],
            seed=seed + index,
            resamples=resamples,
        )
        for index, metric in enumerate(("brier", "log_loss", "rps"))
    }
    # A partial comparator may be reported, but never supports a lead claim.
    if coverage != 1.0:
        intervals = {
            metric: PairedBootstrapInterval(
                interval.mean_delta,
                interval.ci_lower,
                interval.ci_upper,
                interval.date_count,
                interval.group_count,
                False,
            )
            for metric, interval in intervals.items()
        }
    return PairedComparison(
        comparator=comparator,
        paired_case_count=paired_cases,
        total_case_count=total,
        coverage=coverage,
        date_count=intervals["log_loss"].date_count,
        group_count=intervals["log_loss"].group_count,
        brier=intervals["brier"],
        log_loss=intervals["log_loss"],
        rps=intervals["rps"],
    )


def validate_probability_candidates(
    cases: Sequence[ProbabilityValidationCase],
    *,
    predeclared_candidates: Sequence[str],
    bootstrap_seed: int = 0,
    bootstrap_resamples: int = 2_000,
) -> ProbabilityValidationResult:
    """Score precomputed candidate vectors without future-label selection leakage.

    Each outer decision selects the lowest mean full-vector log loss among
    candidates that cover every strictly-prior, already-known case for the same
    city and metric.  Five distinct target dates are the minimum computation
    readiness threshold, not a truncated lookback.  Before that threshold, the
    baseline remains selected.  Candidate absence never shrinks the window.
    """
    names = tuple(predeclared_candidates)
    if not names or len(set(names)) != len(names) or any(not isinstance(name, str) or not name.strip() for name in names):
        raise ProbabilityValidationError("predeclared_candidates must be unique non-empty names")
    if bootstrap_resamples < 1:
        raise ProbabilityValidationError("bootstrap_resamples must be positive")
    predeclared = frozenset(names)
    validated_cases = tuple(cases)
    if not validated_cases:
        raise ProbabilityValidationError("at least one validation case is required")
    for case in validated_cases:
        _validate_case(case, predeclared)
    ordered_cases = tuple(
        sorted(
            validated_cases,
            key=lambda case: (
                _utc(case.decision_at, field="decision_at"),
                case.target_date,
                case.city,
                case.metric,
            ),
        )
    )
    evaluations: list[SelectionEvaluation] = []
    for outer in ordered_cases:
        outer_decision = _utc(outer.decision_at, field="decision_at")
        training = tuple(
            case
            for case in ordered_cases
            if case.city == outer.city
            and case.metric == outer.metric
            and case.target_date < outer.target_date
            and _utc(case.label_known_at, field="label_known_at") < outer_decision
        )
        training_dates = tuple(sorted({case.target_date for case in training}))
        training_losses: dict[str, float] = {}
        if len(training_dates) >= MIN_TRAINING_TARGET_DATES:
            for name in names:
                if name not in outer.candidates or any(name not in case.candidates for case in training):
                    continue
                training_losses[name] = fmean(
                    _scores(case.candidates[name], case.winner_index).log_loss
                    for case in training
                )
        selected_name = min(training_losses, key=lambda name: (training_losses[name], name)) if training_losses else "baseline"
        selected_vector = outer.baseline if selected_name == "baseline" else outer.candidates[selected_name]
        evaluations.append(
            SelectionEvaluation(
                city=outer.city,
                metric=outer.metric,
                target_date=outer.target_date.isoformat(),
                selected_name=selected_name,
                training_target_date_count=len(training_dates),
                training_case_count=len(training),
                selected_scores=_scores(selected_vector, outer.winner_index),
                baseline_scores=_scores(outer.baseline, outer.winner_index),
                candidate_scores={
                    name: _scores(vector, outer.winner_index)
                    for name, vector in outer.candidates.items()
                },
                market_scores=(
                    _scores(outer.market, outer.winner_index)
                    if outer.market is not None
                    else None
                ),
            )
        )
    selected_vs_baseline = _paired_comparison(
        evaluations,
        comparator="baseline",
        seed=bootstrap_seed,
        resamples=bootstrap_resamples,
    )
    selected_vs_candidates = {
        name: _paired_comparison(
            evaluations,
            comparator=name,
            seed=bootstrap_seed + 100 * (index + 1),
            resamples=bootstrap_resamples,
        )
        for index, name in enumerate(names)
    }
    selected_vs_market = _paired_comparison(
        evaluations,
        comparator="market",
        seed=bootstrap_seed + 10_000,
        resamples=bootstrap_resamples,
    )
    market_scores = [evaluation.market_scores for evaluation in evaluations if evaluation.market_scores]
    market_coverage = len(market_scores) / len(evaluations)
    market_date_count = len({
        evaluation.target_date for evaluation in evaluations if evaluation.market_scores
    })
    market_lead_supported = selected_vs_market.log_loss.supported
    all_competitors_supported = (
        selected_vs_baseline.log_loss.supported
        and all(comparison.log_loss.supported for comparison in selected_vs_candidates.values())
        and market_lead_supported
    )
    return ProbabilityValidationResult(
        predeclared_candidates=names,
        evaluations=tuple(evaluations),
        selected=_summary([evaluation.selected_scores for evaluation in evaluations]),
        baseline=_summary([evaluation.baseline_scores for evaluation in evaluations]),
        candidates={
            name: _summary([
                evaluation.candidate_scores[name]
                for evaluation in evaluations
                if name in evaluation.candidate_scores
            ])
            for name in names
        },
        market=_summary(market_scores),
        selected_vs_baseline=selected_vs_baseline,
        selected_vs_candidates=selected_vs_candidates,
        selected_vs_market=selected_vs_market,
        market_coverage=market_coverage,
        market_date_count=market_date_count,
        market_lead_supported=market_lead_supported,
        best_tested_direction="selected" if all_competitors_supported else None,
    )
