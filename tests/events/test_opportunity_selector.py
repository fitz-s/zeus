from __future__ import annotations

# Created: 2026-06-06
# Last reused/audited: 2026-06-06
# Authority basis: Operator request — family-bin selector must choose the best sibling opportunity, not the arrival-triggered token.

from dataclasses import replace

from src.events.candidate_evaluation import CandidateEvaluation
from src.events.opportunity_book import build_family_opportunity_book
from src.events.opportunity_selector import select_best_family_candidate


def _evaluation(**overrides):
    base = CandidateEvaluation(
        candidate_id="cand-expensive",
        family_id="family-1",
        condition_id="condition-expensive",
        token_id="token-expensive",
        direction="buy_no",
        bin_label="16C",
        execution_price=0.99,
        q_posterior=0.999,
        q_lcb_5pct=0.99,
        c_cost_95pct=0.995,
        p_fill_lcb=0.9,
        trade_score=0.015,
        p_value=0.01,
        passed_prefilter=True,
        native_quote_available=True,
        low_volume_usd=10.0,
    )
    return replace(base, **overrides)


def test_selector_prefers_best_family_objective_not_arrival_order():
    expensive_micro_edge = _evaluation()
    cheaper_better_trade = _evaluation(
        candidate_id="cand-cheap",
        condition_id="condition-cheap",
        token_id="token-cheap",
        bin_label="12C",
        execution_price=0.2,
        q_lcb_5pct=0.4,
        trade_score=0.01,
    )

    result = select_best_family_candidate((expensive_micro_edge, cheaper_better_trade))

    assert result.selected is cheaper_better_trade
    assert result.loser_reasons["cand-expensive"].startswith("FAMILY_RANK_LOST:")


def test_opportunity_book_receipt_contains_ranks_and_loser_reasons():
    selected = _evaluation(candidate_id="selected", execution_price=0.2, trade_score=0.02)
    loser = _evaluation(candidate_id="loser", execution_price=0.99, trade_score=0.01)

    book = build_family_opportunity_book(
        family_id="family-1",
        evaluations=(loser, selected),
        event_id="event-1",
        cache_summary={"price_cache": "snapshot_rows_refreshed_for_family"},
    )
    receipt = book.to_receipt_dict()

    assert receipt["selected_candidate_id"] is None
    assert receipt["proposed_selected_candidate_id"] == "selected"
    assert receipt["family_rank"]["selected"] == 1
    assert receipt["loser_reasons"]["loser"].startswith("FAMILY_RANK_LOST:")
    assert receipt["cache_summary"]["price_cache"] == "snapshot_rows_refreshed_for_family"
