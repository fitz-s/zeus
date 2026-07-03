# Last reused or audited: 2026-06-08
# Authority basis: "bin selection.md" §6 (best-candidate selection) + §9 Hidden
#   #10 (central NO is broad correlated exposure with low marginal utility) +
#   §14.7/§14.8 (single-primary-live; rank by robust marginal utility) +
#   operator directive 2026-06-08.
"""Display-only ranking for family opportunity books (NOT the live decision).

SINGLE DECISION SURFACE (S4, operator directive 2026-06-08). The LIVE selection
is the robust-marginal-expected-log-utility (ΔU) ranker
(``event_reactor_adapter._select_proof_by_robust_marginal_utility``). This module
NO LONGER decides anything — ``select_best_family_candidate`` produces ONLY the
display ordering + loser-reason annotations the receipt serializes (provenance).
``opportunity_book.build_family_opportunity_book`` records the upstream ΔU
decision verbatim; this scalar ranking never gates the live leg.

REMOVED 2026-06-08 (S4): the ``_family_structure_rejection_reasons`` buy_no-on-
forecast-modal-bin guard. It was a bolted-on side-specific exclusion subsumed by
the single FamilyPayoffMatrix / effective_outcome_pi comparison: a central/modal-
bin NO simply scores LOWER ΔU than the modal YES (its honest robust NO q_lcb =
1 - q_ucb_yes is small on a high-YES-mass bin, so its robust edge is negative),
so the matrix dominates it WITHOUT a side-specific gate (Hidden #10). Antibody:
tests/engine/test_s4_subsumed_gates.py::
test_modal_bin_no_dominated_by_modal_yes_through_one_matrix. A redundant gate is
the regression disease the directive abolishes.
"""

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
    """Display ordering + loser reasons for the receipt (NOT the live decision).

    Ranks admitted siblings by the legacy objective tuple for the receipt's
    display ``family_rank`` and loser-reason annotations only. The LIVE leg is the
    ΔU winner chosen upstream; this function is provenance, never selection.
    """

    material = tuple(evaluations)
    admitted = [evaluation for evaluation in material if evaluation.admitted]
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
            loser_reasons[evaluation.candidate_id] = _admission_rejection_reason(
                evaluation
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
        loser_reasons[evaluation.candidate_id] = _admission_rejection_reason(evaluation)
    return SelectionResult(selected=selected, ranked=ranked, loser_reasons=loser_reasons)


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
    win_rate_reason = evaluation.live_win_rate_floor_reason
    if win_rate_reason is not None:
        return win_rate_reason
    lcb_consistency_reason = evaluation.live_lcb_consistency_reason
    if lcb_consistency_reason is not None:
        return lcb_consistency_reason
    capital_efficiency_reason = evaluation.live_capital_efficiency_reason
    if capital_efficiency_reason is not None:
        return capital_efficiency_reason
    buy_no_conservative_evidence_reason = evaluation.live_buy_no_conservative_evidence_reason
    if buy_no_conservative_evidence_reason is not None:
        return buy_no_conservative_evidence_reason
    if not evaluation.selection_calibrator_admissible:
        return (
            "ADMISSION_SELECTION_CALIBRATOR:"
            f"q_safe={evaluation.calibrated_admission_q_lcb:.6f}:"
            f"price={float(evaluation.execution_price or 0.0):.6f}"
        )
    city_skill_reason = evaluation.city_skill_block_reason
    if city_skill_reason is not None:
        return city_skill_reason
    return "ADMISSION_NOT_SELECTED"
