"""Pure selection for family opportunity books."""

from __future__ import annotations

from dataclasses import dataclass

from src.events.candidate_evaluation import CandidateEvaluation


@dataclass(frozen=True)
class SelectionResult:
    selected: CandidateEvaluation | None
    ranked: tuple[CandidateEvaluation, ...]
    loser_reasons: dict[str, str]


def select_best_family_candidate(
    evaluations: tuple[CandidateEvaluation, ...] | list[CandidateEvaluation],
) -> SelectionResult:
    """Select the best admitted sibling candidate without treating volume as a gate."""

    admitted = [evaluation for evaluation in evaluations if evaluation.admitted]
    ranked = tuple(
        sorted(
            admitted,
            key=lambda evaluation: evaluation.objective_tuple,
            reverse=True,
        )
    )
    selected = ranked[0] if ranked else None
    loser_reasons: dict[str, str] = {}
    if selected is None:
        for evaluation in evaluations:
            if evaluation.missing_reason is not None:
                loser_reasons[evaluation.candidate_id] = evaluation.missing_reason
            elif not evaluation.passed_prefilter:
                loser_reasons[evaluation.candidate_id] = "ADMISSION_PREFILTER_FALSE"
            elif evaluation.execution_price is None or evaluation.execution_price <= 0.0:
                loser_reasons[evaluation.candidate_id] = "ADMISSION_EXECUTION_PRICE_MISSING"
            elif evaluation.trade_score <= 0.0:
                loser_reasons[evaluation.candidate_id] = "ADMISSION_TRADE_SCORE_NON_POSITIVE"
            elif not evaluation.quote_fresh:
                loser_reasons[evaluation.candidate_id] = "ADMISSION_QUOTE_STALE"
            else:
                loser_reasons[evaluation.candidate_id] = "ADMISSION_NOT_CONSTRUCTABLE"
        return SelectionResult(selected=None, ranked=ranked, loser_reasons=loser_reasons)

    for rank, evaluation in enumerate(ranked[1:], start=2):
        loser_reasons[evaluation.candidate_id] = (
            "FAMILY_RANK_LOST:"
            f"rank={rank}:selected={selected.candidate_id}:"
            f"robust_ev_per_dollar={evaluation.robust_ev_per_dollar:.8f}:"
            f"selected_robust_ev_per_dollar={selected.robust_ev_per_dollar:.8f}"
        )
    for evaluation in evaluations:
        if evaluation.admitted or evaluation.candidate_id in loser_reasons:
            continue
        if evaluation.missing_reason is not None:
            loser_reasons[evaluation.candidate_id] = evaluation.missing_reason
        elif not evaluation.passed_prefilter:
            loser_reasons[evaluation.candidate_id] = "ADMISSION_PREFILTER_FALSE"
        elif evaluation.execution_price is None or evaluation.execution_price <= 0.0:
            loser_reasons[evaluation.candidate_id] = "ADMISSION_EXECUTION_PRICE_MISSING"
        elif evaluation.trade_score <= 0.0:
            loser_reasons[evaluation.candidate_id] = "ADMISSION_TRADE_SCORE_NON_POSITIVE"
        elif not evaluation.quote_fresh:
            loser_reasons[evaluation.candidate_id] = "ADMISSION_QUOTE_STALE"
        else:
            loser_reasons[evaluation.candidate_id] = "ADMISSION_NOT_SELECTED"
    return SelectionResult(selected=selected, ranked=ranked, loser_reasons=loser_reasons)
