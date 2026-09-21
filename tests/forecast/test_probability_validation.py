# Created: 2026-09-20
# Last reused/audited: 2026-09-20
# Purpose: Validate chronological full-vector comparisons without future-label leakage.
# Reuse: Run before changing candidate selection or superiority claims.
# Authority basis: current-resolver probability validation operator request, 2026-09-20.
"""Antibodies for causal, full-vector probability candidate validation."""
from __future__ import annotations

from dataclasses import asdict
from datetime import date, datetime, timedelta, timezone
import json

import pytest

from src.forecast.probability_validation import (
    MarketVector,
    ProbabilityValidationCase,
    ProbabilityValidationError,
    ProbabilityVector,
    validate_probability_candidates,
)


BINS = ("bin-low", "bin-mid", "bin-high")
BASE_DATE = date(2026, 1, 1)


def _at(day: int, hour: int = 12) -> datetime:
    return datetime(2026, 1, 1, hour, tzinfo=timezone.utc) + timedelta(days=day - 1)


def _vector(values, *, day: int, available_offset_hours: int = -1) -> ProbabilityVector:
    return ProbabilityVector(
        bin_ids=BINS,
        values=tuple(values),
        available_at=_at(day) + timedelta(hours=available_offset_hours),
    )


def _case(
    day: int,
    *,
    winner: int = 0,
    candidates: dict[str, ProbabilityVector] | None = None,
    baseline: ProbabilityVector | None = None,
    label_known_at: datetime | None = None,
    market: MarketVector | None = None,
    city: str = "Taipei",
    metric: str = "high",
) -> ProbabilityValidationCase:
    return ProbabilityValidationCase(
        city=city,
        metric=metric,
        target_date=BASE_DATE + timedelta(days=day - 1),
        decision_at=_at(day),
        label_known_at=label_known_at or (_at(day) + timedelta(hours=1)),
        bin_ids=BINS,
        winner_index=winner,
        candidates=candidates or {},
        baseline=baseline or _vector((0.6, 0.2, 0.2), day=day),
        market=market,
    )


def _result(cases, *, candidates=("candidate",)):
    return validate_probability_candidates(
        cases,
        predeclared_candidates=candidates,
        bootstrap_resamples=100,
    )


def _evaluation(result, day: int):
    return next(
        item
        for item in result.evaluations
        if item.target_date == (BASE_DATE + timedelta(days=day - 1)).isoformat()
    )


def test_scores_brier_over_the_whole_categorical_vector():
    """Equal winner mass cannot hide different losing-bin mass under Brier."""
    result = _result([
        _case(
            1,
            candidates={"candidate": _vector((0.6, 0.4, 0.0), day=1)},
            baseline=_vector((0.6, 0.2, 0.2), day=1),
        )
    ])

    evaluation = _evaluation(result, 1)
    assert evaluation.candidate_scores["candidate"].log_loss == pytest.approx(
        evaluation.baseline_scores.log_loss
    )
    assert evaluation.candidate_scores["candidate"].brier == pytest.approx(0.32)
    assert evaluation.baseline_scores.brier == pytest.approx(0.24)


def test_future_known_labels_cannot_open_a_training_window():
    """Labels learned after the outer decision cannot select a candidate."""
    cases = [
        _case(
            day,
            candidates={"candidate": _vector((0.9, 0.1, 0.0), day=day)},
            label_known_at=_at(10) + timedelta(hours=1),
        )
        for day in range(1, 6)
    ]
    cases.append(_case(10, candidates={"candidate": _vector((0.9, 0.1, 0.0), day=10)}))

    evaluation = _evaluation(_result(cases), 10)
    assert evaluation.training_target_date_count == 0
    assert evaluation.selected_name == "baseline"


def test_new_candidate_missing_earliest_training_vector_cannot_backselect():
    """A late candidate cannot discard an unfavorable missing training date."""
    cases = []
    for day in range(1, 6):
        candidates = {"old": _vector((0.7, 0.2, 0.1), day=day)}
        if day > 1:
            candidates["new"] = _vector((0.99, 0.01, 0.0), day=day)
        cases.append(_case(day, candidates=candidates))
    cases.append(
        _case(
            6,
            candidates={
                "old": _vector((0.7, 0.2, 0.1), day=6),
                "new": _vector((0.99, 0.01, 0.0), day=6),
            },
        )
    )

    evaluation = _evaluation(_result(cases, candidates=("old", "new")), 6)
    assert evaluation.training_target_date_count == 5
    assert evaluation.selected_name == "old"


def test_rolling_selection_uses_all_prior_same_city_metric_dates():
    """Five dates only open selection; they do not freeze its training window."""
    cases = []
    for day in range(1, 11):
        a, b = ((0.9, 0.1, 0.0), (0.1, 0.9, 0.0)) if day <= 5 else (
            (0.01, 0.99, 0.0),
            (0.99, 0.01, 0.0),
        )
        cases.append(_case(day, candidates={"a": _vector(a, day=day), "b": _vector(b, day=day)}))
    cases.append(
        _case(11, candidates={"a": _vector((0.01, 0.99, 0.0), day=11), "b": _vector((0.99, 0.01, 0.0), day=11)})
    )

    evaluation = _evaluation(_result(cases, candidates=("a", "b")), 11)
    assert evaluation.training_target_date_count == 10
    assert evaluation.selected_name == "b"


def test_city_and_metric_training_cannot_cross_contaminate_selection():
    """Taipei HIGH cannot use Taipei LOW or Osaka HIGH labels for selection."""
    cases = []
    for day in range(1, 6):
        cases.append(
            _case(
                day,
                candidates={"a": _vector((0.9, 0.1, 0.0), day=day), "b": _vector((0.1, 0.9, 0.0), day=day)},
            city="Taipei",
            metric="high",
            )
        )
        cases.append(
            _case(
                day,
                candidates={"a": _vector((0.1, 0.9, 0.0), day=day), "b": _vector((0.9, 0.1, 0.0), day=day)},
                city="Taipei",
                metric="low",
            )
        )
        cases.append(
            _case(
                day,
                candidates={"a": _vector((0.1, 0.9, 0.0), day=day), "b": _vector((0.9, 0.1, 0.0), day=day)},
                city="Osaka",
                metric="high",
            )
        )
    cases.append(
        _case(
            6,
            candidates={"a": _vector((0.9, 0.1, 0.0), day=6), "b": _vector((0.1, 0.9, 0.0), day=6)},
            city="Taipei",
            metric="high",
        )
    )

    evaluation = _evaluation(_result(cases, candidates=("a", "b")), 6)
    assert evaluation.training_target_date_count == 5
    assert evaluation.selected_name == "a"


@pytest.mark.parametrize(
    "vector",
    [
        lambda: _vector((float("nan"), 0.5, 0.5), day=1),
        lambda: _vector((0.4, 0.4, 0.1), day=1),
        lambda: ProbabilityVector(BINS[::-1], (0.6, 0.2, 0.2), _at(1) - timedelta(hours=1)),
        lambda: _vector((0.6, 0.2, 0.2), day=1, available_offset_hours=1),
    ],
)
def test_rejects_invalid_vector_math_topology_and_source_time(vector):
    case = _case(1, candidates={"candidate": vector()})

    with pytest.raises(ProbabilityValidationError):
        _result([case])


def test_rejects_labels_known_at_or_before_the_decision():
    case = _case(1, label_known_at=_at(1))

    with pytest.raises(ProbabilityValidationError, match="label_known_at"):
        _result([case])


@pytest.mark.parametrize(
    "market",
    [
        lambda: MarketVector(BINS, (0.6, 0.2, 0.2), _at(1) + timedelta(hours=1), True),
        lambda: MarketVector(BINS, (0.6, 0.2, 0.2), _at(1) - timedelta(hours=1), False),
    ],
)
def test_rejects_future_or_partially_proven_market_comparators(market):
    with pytest.raises(ProbabilityValidationError):
        _result([_case(1, market=market())])


def test_future_excellent_test_label_does_not_change_past_selection():
    """A future outcome cannot alter the candidate chosen for an earlier outer case."""
    history = [
        _case(
            day,
            candidates={
                "a": _vector((0.9, 0.1, 0.0), day=day),
                "b": _vector((0.1, 0.9, 0.0), day=day),
            },
        )
        for day in range(1, 6)
    ]
    outer = _case(
        6,
        candidates={
            "a": _vector((0.9, 0.1, 0.0), day=6),
            "b": _vector((0.1, 0.9, 0.0), day=6),
        },
    )
    future = _case(
        7,
        candidates={
            "a": _vector((0.01, 0.99, 0.0), day=7),
            "b": _vector((0.99, 0.01, 0.0), day=7),
        },
    )

    before = _evaluation(_result([*history, outer], candidates=("a", "b")), 6)
    after = _evaluation(_result([*history, outer, future], candidates=("a", "b")), 6)
    assert before.selected_name == after.selected_name == "a"


def test_missing_market_prevents_market_lead_or_best_claim():
    result = _result([
        _case(day, candidates={"candidate": _vector((0.9, 0.1, 0.0), day=day)})
        for day in range(1, 7)
    ])

    assert result.market_coverage == 0.0
    assert result.market_lead_supported is False
    assert result.best_tested_direction is None
    json.dumps(asdict(result))
