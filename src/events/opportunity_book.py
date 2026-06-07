"""In-memory opportunity book receipt evidence."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.decision_kernel.canonicalization import stable_hash
from src.events.candidate_evaluation import CandidateEvaluation
from src.events.opportunity_selector import select_best_family_candidate


@dataclass(frozen=True)
class OpportunityBook:
    book_id: str
    book_version: int
    family_id: str
    evaluations: tuple[CandidateEvaluation, ...]
    selected_candidate_id: str | None
    family_rank: dict[str, int]
    global_rank: dict[str, int]
    loser_reasons: dict[str, str]
    cache_summary: dict[str, Any]

    def to_receipt_dict(self) -> dict[str, Any]:
        selected = next(
            (
                evaluation
                for evaluation in self.evaluations
                if evaluation.candidate_id == self.selected_candidate_id
            ),
            None,
        )
        selector_enabled = bool(self.cache_summary.get("selector_enabled"))
        actual_selected_candidate_id = self.cache_summary.get("actual_receipt_selected_candidate_id")
        return {
            "book_id": self.book_id,
            "book_version": self.book_version,
            "family_id": self.family_id,
            "evaluated_count": len(self.evaluations),
            "admitted_count": sum(1 for evaluation in self.evaluations if evaluation.admitted),
            "selected_candidate_id": self.selected_candidate_id if selector_enabled else None,
            "proposed_selected_candidate_id": self.selected_candidate_id,
            "actual_receipt_selected_candidate_id": actual_selected_candidate_id,
            "selected_objective": (
                {
                    "robust_ev_per_dollar": selected.robust_ev_per_dollar,
                    "robust_kelly_fraction_lcb": selected.robust_kelly_fraction_lcb,
                    "robust_kelly_growth_score": selected.robust_kelly_growth_score,
                    "expected_robust_dollars": selected.expected_robust_dollars,
                    "q_lcb_5pct": selected.q_lcb_5pct,
                    "trade_score": selected.trade_score,
                    "execution_price": selected.execution_price,
                }
                if selected is not None
                else None
            ),
            "family_rank": self.family_rank,
            "global_rank": self.global_rank,
            "loser_reasons": self.loser_reasons,
            "cache_summary": self.cache_summary,
            "candidates": [evaluation.to_receipt_dict() for evaluation in self.evaluations],
        }


def build_family_opportunity_book(
    *,
    family_id: str,
    evaluations: tuple[CandidateEvaluation, ...] | list[CandidateEvaluation],
    event_id: str,
    cache_summary: dict[str, Any] | None = None,
) -> OpportunityBook:
    material = tuple(evaluations)
    selection = select_best_family_candidate(material)
    ranked_ids = [evaluation.candidate_id for evaluation in selection.ranked]
    book_id = "opportunity_book:" + stable_hash(
        {
            "event_id": event_id,
            "family_id": family_id,
            "candidate_ids": [evaluation.candidate_id for evaluation in material],
            "selected_candidate_id": selection.selected.candidate_id if selection.selected else None,
        }
    )
    ranks = {candidate_id: rank for rank, candidate_id in enumerate(ranked_ids, start=1)}
    return OpportunityBook(
        book_id=book_id,
        book_version=1,
        family_id=family_id,
        evaluations=material,
        selected_candidate_id=selection.selected.candidate_id if selection.selected else None,
        family_rank=ranks,
        global_rank=ranks,
        loser_reasons=selection.loser_reasons,
        cache_summary=dict(cache_summary or {}),
    )
