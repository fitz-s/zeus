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

    material = tuple(evaluations)
    family_rejections = _family_structure_rejection_reasons(material)
    admitted = [
        evaluation
        for evaluation in material
        if evaluation.admitted and evaluation.candidate_id not in family_rejections
    ]
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
        for evaluation in material:
            loser_reasons[evaluation.candidate_id] = family_rejections.get(
                evaluation.candidate_id,
                _admission_rejection_reason(evaluation),
            )
        return SelectionResult(selected=None, ranked=ranked, loser_reasons=loser_reasons)

    for rank, evaluation in enumerate(ranked[1:], start=2):
        loser_reasons[evaluation.candidate_id] = (
            "FAMILY_RANK_LOST:"
            f"rank={rank}:selected={selected.candidate_id}:"
            f"robust_kelly_growth_score={evaluation.robust_kelly_growth_score:.8f}:"
            f"selected_robust_kelly_growth_score={selected.robust_kelly_growth_score:.8f}:"
            f"robust_kelly_fraction_lcb={evaluation.robust_kelly_fraction_lcb:.8f}:"
            f"selected_robust_kelly_fraction_lcb={selected.robust_kelly_fraction_lcb:.8f}:"
            f"expected_robust_dollars={evaluation.expected_robust_dollars:.8f}:"
            f"selected_expected_robust_dollars={selected.expected_robust_dollars:.8f}:"
            f"robust_ev_per_dollar={evaluation.robust_ev_per_dollar:.8f}:"
            f"selected_robust_ev_per_dollar={selected.robust_ev_per_dollar:.8f}"
        )
    for evaluation in material:
        if evaluation.admitted or evaluation.candidate_id in loser_reasons:
            continue
        loser_reasons[evaluation.candidate_id] = family_rejections.get(
            evaluation.candidate_id,
            _admission_rejection_reason(evaluation),
        )
    for candidate_id, reason in family_rejections.items():
        loser_reasons.setdefault(candidate_id, reason)
    return SelectionResult(selected=selected, ranked=ranked, loser_reasons=loser_reasons)


def _family_structure_rejection_reasons(
    evaluations: tuple[CandidateEvaluation, ...],
) -> dict[str, str]:
    yes_posterior_by_condition: dict[str, float] = {}
    for evaluation in evaluations:
        condition_id = str(evaluation.condition_id or "").strip()
        if not condition_id:
            continue
        if evaluation.direction != "buy_yes":
            continue
        yes_posterior_by_condition[condition_id] = max(
            yes_posterior_by_condition.get(condition_id, 0.0),
            float(evaluation.q_posterior),
        )
    if not yes_posterior_by_condition:
        return {}
    modal_yes = max(yes_posterior_by_condition.values())
    reasons: dict[str, str] = {}
    for evaluation in evaluations:
        if evaluation.direction != "buy_no":
            continue
        condition_id = str(evaluation.condition_id or "").strip()
        yes_posterior = yes_posterior_by_condition.get(condition_id)
        if yes_posterior is None:
            continue
        if not evaluation.admitted:
            continue
        if yes_posterior >= modal_yes:
            reasons[evaluation.candidate_id] = (
                "ADMISSION_BUY_NO_ON_FORECAST_MODAL_BIN:"
                f"yes_posterior={yes_posterior:.8f}:modal_yes_posterior={modal_yes:.8f}"
            )
    return reasons


def _admission_rejection_reason(evaluation: CandidateEvaluation) -> str:
    if evaluation.missing_reason is not None:
        return evaluation.missing_reason
    if not evaluation.passed_prefilter:
        return "ADMISSION_PREFILTER_FALSE"
    if evaluation.execution_price is None or evaluation.execution_price <= 0.0:
        return "ADMISSION_EXECUTION_PRICE_MISSING"
    if evaluation.trade_score <= 0.0:
        return "ADMISSION_TRADE_SCORE_NON_POSITIVE"
    if not evaluation.quote_fresh:
        return "ADMISSION_QUOTE_STALE"
    lcb_consistency_reason = evaluation.live_lcb_consistency_reason
    if lcb_consistency_reason is not None:
        return lcb_consistency_reason
    capital_efficiency_reason = evaluation.live_capital_efficiency_reason
    if capital_efficiency_reason is not None:
        return capital_efficiency_reason
    buy_no_conservative_evidence_reason = evaluation.live_buy_no_conservative_evidence_reason
    if buy_no_conservative_evidence_reason is not None:
        return buy_no_conservative_evidence_reason
    return "ADMISSION_NOT_SELECTED"
