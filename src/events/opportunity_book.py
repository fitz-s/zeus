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

import math
from dataclasses import dataclass
from typing import Any

from src.decision_kernel.canonicalization import stable_hash
from src.events.candidate_evaluation import CandidateEvaluation
from src.events.opportunity_selector import select_best_family_candidate


def _finite_float(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(parsed):
        return None
    return parsed


def _qkernel_selected_economics_live_admitted(economics: Any) -> bool:
    """Return whether qkernel economics are enough to be the live admission fact.

    The adapter/decision-kernel still performs the full money-path certificate
    verification. This local check exists only to keep the receipt's public
    opportunity-book vocabulary coherent: a qkernel-selected candidate cannot be
    serialized as both the live decision and "not admitted".
    """

    if not isinstance(economics, dict):
        return False
    if str(economics.get("source") or "").strip() != "qkernel_spine":
        return False
    if not str(economics.get("candidate_id") or "").strip():
        return False
    if not str(economics.get("route_id") or "").strip().startswith(("DIRECT_YES:", "DIRECT_NO:")):
        return False
    if economics.get("direction_law_ok") is not True:
        return False
    if economics.get("coherence_allows") is not True:
        return False
    basis = str(economics.get("selection_guard_basis") or "").strip()
    if not basis or basis == "SIDE_NOT_ARMED":
        return False
    if economics.get("selection_guard_abstained") is not False:
        return False
    payoff_q_point = _finite_float(economics.get("payoff_q_point"))
    payoff_q_lcb = _finite_float(economics.get("payoff_q_lcb"))
    cost = _finite_float(economics.get("cost"))
    edge_lcb = _finite_float(economics.get("edge_lcb"))
    delta_u_at_min = _finite_float(economics.get("delta_u_at_min"))
    optimal_delta_u = _finite_float(economics.get("optimal_delta_u"))
    optimal_stake = _finite_float(economics.get("optimal_stake_usd"))
    false_edge_rate = _finite_float(economics.get("false_edge_rate"))
    if None in (
        payoff_q_point,
        payoff_q_lcb,
        cost,
        edge_lcb,
        delta_u_at_min,
        optimal_delta_u,
        optimal_stake,
        false_edge_rate,
    ):
        return False
    assert payoff_q_point is not None
    assert payoff_q_lcb is not None
    assert cost is not None
    assert edge_lcb is not None
    assert delta_u_at_min is not None
    assert optimal_delta_u is not None
    assert optimal_stake is not None
    assert false_edge_rate is not None
    if not (0.0 <= payoff_q_lcb <= payoff_q_point <= 1.0):
        return False
    if not (0.0 < cost < 1.0):
        return False
    if edge_lcb <= 0.0 or delta_u_at_min <= 0.0 or optimal_delta_u <= 0.0:
        return False
    if optimal_stake <= 0.0:
        return False
    if not (0.0 <= false_edge_rate <= 0.10):
        return False
    return math.isclose(payoff_q_lcb, cost + edge_lcb, rel_tol=1e-9, abs_tol=1e-9)


def _qkernel_selected_objective(economics: Any) -> dict[str, Any] | None:
    if not _qkernel_selected_economics_live_admitted(economics):
        return None
    assert isinstance(economics, dict)
    cost = float(economics["cost"])
    edge_lcb = float(economics["edge_lcb"])
    optimal_stake = float(economics["optimal_stake_usd"])
    robust_ev_per_dollar = edge_lcb / cost
    expected_robust_dollars = robust_ev_per_dollar * optimal_stake
    return {
        "authority": "qkernel_spine",
        "robust_ev_per_dollar": robust_ev_per_dollar,
        "robust_kelly_fraction_lcb": float(economics["optimal_delta_u"]),
        "robust_kelly_growth_score": float(economics["delta_u_at_min"]),
        "capital_weighted_growth_score": expected_robust_dollars,
        "expected_robust_dollars": expected_robust_dollars,
        "q_lcb_5pct": float(economics["payoff_q_lcb"]),
        "trade_score": edge_lcb,
        "execution_price": cost,
        "optimal_stake_usd": optimal_stake,
        "optimal_delta_u": float(economics["optimal_delta_u"]),
    }


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
        qkernel_selection_admitted = (
            selection_authority == "qkernel_spine"
            and _qkernel_selected_economics_live_admitted(
                selected_qkernel_execution_economics
            )
        )
        candidate_receipts = [
            evaluation.to_receipt_dict() for evaluation in self.evaluations
        ]
        live_selected_candidate_id = actual_selected_candidate_id or self.selected_candidate_id
        for candidate in candidate_receipts:
            live_selected = bool(
                live_selected_candidate_id
                and str(candidate.get("candidate_id") or "") == str(live_selected_candidate_id)
            )
            candidate["live_decision_selected"] = live_selected
            if live_selected and selection_authority is not None:
                candidate["live_selection_authority"] = selection_authority
            if live_selected and selection_authority == "qkernel_spine":
                candidate["admitted"] = qkernel_selection_admitted
                candidate["live_admission_authority"] = "qkernel_spine"
                if not qkernel_selection_admitted:
                    candidate["live_admission_rejection_reason"] = (
                        "QKERNEL_EXECUTION_ECONOMICS_INVALID_FOR_LIVE_ADMISSION"
                    )
        if actual_selected_candidate_id:
            for candidate in candidate_receipts:
                if str(candidate.get("candidate_id") or "") != str(actual_selected_candidate_id):
                    continue
                if selection_authority is not None:
                    candidate["live_selection_authority"] = selection_authority
                if selected_qkernel_execution_economics is not None:
                    candidate["qkernel_execution_economics"] = selected_qkernel_execution_economics
                break
        qkernel_selected_objective = _qkernel_selected_objective(
            selected_qkernel_execution_economics
        )
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
            "selected_objective": qkernel_selected_objective or (
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
