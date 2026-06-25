# Last reused or audited: 2026-06-08
# Authority basis: "bin selection.md" §14 item 7 (rank by robust marginal utility) +
#   §14 item 8 (single-primary-live) + operator directive 2026-06-08. S3-fix:
#   build_family_opportunity_book RECORDS the upstream ΔU decision
#   (decided_candidate_id) instead of self-selecting via select_best_family_candidate;
#   that legacy scalar-Kelly selector now produces ONLY display ranks + loser reasons
#   (provenance), never the live selection. One decision surface, one truth.
#   S7 (2026-06-08): the off-able selector gate is GONE. to_receipt_dict no longer
#   reads a ``selector_enabled`` cache flag to decide whether to surface the
#   recorded selection — it records the ΔU decision unconditionally. There is no
#   runtime toggle that can silently null the live selection.
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
        actual_selected_candidate_id = self.cache_summary.get("actual_receipt_selected_candidate_id")
        selection_authority = self.cache_summary.get("selection_authority")
        selected_qkernel_execution_economics = self.cache_summary.get(
            "selected_qkernel_execution_economics"
        )
        candidate_receipts = [
            evaluation.to_receipt_dict() for evaluation in self.evaluations
        ]
        if actual_selected_candidate_id:
            for candidate in candidate_receipts:
                if str(candidate.get("candidate_id") or "") != str(actual_selected_candidate_id):
                    continue
                legacy_admitted = bool(candidate.get("admitted"))
                candidate["legacy_admitted"] = legacy_admitted
                candidate["admitted"] = True
                candidate["live_decision_selected"] = True
                if selection_authority is not None:
                    candidate["live_selection_authority"] = selection_authority
                if selected_qkernel_execution_economics is not None:
                    candidate["qkernel_execution_economics"] = selected_qkernel_execution_economics
                break
        receipt = {
            "book_id": self.book_id,
            "book_version": self.book_version,
            "family_id": self.family_id,
            "evaluated_count": len(self.evaluations),
            "admitted_count": sum(
                1
                for candidate in candidate_receipts
                if bool(candidate.get("admitted"))
            ),
            # SINGLE DECISION SURFACE (operator directive 2026-06-08; "bin
            # selection.md" §14 item 8). The recorded selection is the ΔU decision
            # (``self.selected_candidate_id`` == ``decided_candidate_id``)
            # UNCONDITIONALLY. The former off-able ``selector_enabled`` cache gate —
            # which nulled the recorded decision whenever that flag was falsy/absent —
            # is REMOVED: a scattered runtime toggle that can silently discard the
            # live selection is the regression disease the directive abolishes.
            "selected_candidate_id": self.selected_candidate_id,
            "proposed_selected_candidate_id": self.selected_candidate_id,
            "actual_receipt_selected_candidate_id": actual_selected_candidate_id,
            "selected_objective": (
                {
                    "robust_ev_per_dollar": selected.robust_ev_per_dollar,
                    "robust_kelly_fraction_lcb": selected.robust_kelly_fraction_lcb,
                    "robust_kelly_growth_score": selected.robust_kelly_growth_score,
                    "capital_weighted_growth_score": selected.capital_weighted_growth_score,
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
            "candidates": candidate_receipts,
        }
        if selection_authority is not None:
            receipt["selection_authority"] = selection_authority
        if selected_qkernel_execution_economics is not None:
            receipt["selected_qkernel_execution_economics"] = selected_qkernel_execution_economics
        return receipt


def build_family_opportunity_book(
    *,
    family_id: str,
    evaluations: tuple[CandidateEvaluation, ...] | list[CandidateEvaluation],
    event_id: str,
    cache_summary: dict[str, Any] | None = None,
    decided_candidate_id: str | None = None,
) -> OpportunityBook:
    """Serialize the family's candidate evaluations into a receipt book.

    SINGLE DECISION SURFACE (operator directive 2026-06-08; "bin selection.md"
    §14.7/§14.8). When ``decided_candidate_id`` is provided it is the ALREADY-MADE
    live decision — the robust-marginal-expected-log-utility (ΔU) winner chosen by
    ``event_reactor_adapter._select_proof_by_robust_marginal_utility``. The book
    then RECORDS that decision verbatim as ``selected_candidate_id``; it does NOT
    re-decide via the legacy scalar-Kelly ``select_best_family_candidate``. That
    keeps exactly ONE ranking surface: the ΔU ranker decides, the book serializes.
    ``select_best_family_candidate`` is used ONLY to produce display ordering and
    loser-reason annotations for the receipt (provenance), never the selection.

    When ``decided_candidate_id`` is None (no live decision — e.g. a no-trade
    family, or a legacy/test caller), the book records no selection rather than
    minting a second selection authority.
    """
    material = tuple(evaluations)
    # select_best_family_candidate is retained for DISPLAY ordering + loser
    # reasons only (provenance); it is NOT the selection authority.
    selection = select_best_family_candidate(material)
    ranked_ids = [evaluation.candidate_id for evaluation in selection.ranked]
    selected_candidate_id = decided_candidate_id
    book_id = "opportunity_book:" + stable_hash(
        {
            "event_id": event_id,
            "family_id": family_id,
            "candidate_ids": [evaluation.candidate_id for evaluation in material],
            "selected_candidate_id": selected_candidate_id,
        }
    )
    ranks = {candidate_id: rank for rank, candidate_id in enumerate(ranked_ids, start=1)}
    return OpportunityBook(
        book_id=book_id,
        book_version=1,
        family_id=family_id,
        evaluations=material,
        selected_candidate_id=selected_candidate_id,
        family_rank=ranks,
        global_rank=ranks,
        loser_reasons=selection.loser_reasons,
        cache_summary=dict(cache_summary or {}),
    )
