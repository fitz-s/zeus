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


def test_selector_prefers_lcb_kelly_growth_over_modal_adjacent_no():
    modal_adjacent_no = _evaluation(
        candidate_id="cand-22-no",
        condition_id="condition-22",
        token_id="token-22-no",
        bin_label="22C",
        execution_price=0.70,
        q_posterior=0.80,
        q_lcb_5pct=0.72,
        trade_score=0.02,
    )
    better_sibling = _evaluation(
        candidate_id="cand-23-yes",
        condition_id="condition-23",
        token_id="token-23-yes",
        direction="buy_yes",
        bin_label="23C",
        execution_price=0.30,
        q_posterior=0.46,
        q_lcb_5pct=0.42,
        trade_score=0.04,
    )

    result = select_best_family_candidate((modal_adjacent_no, better_sibling))

    assert result.selected is better_sibling
    assert result.loser_reasons["cand-22-no"].startswith("FAMILY_RANK_LOST:")
    assert "robust_kelly_growth_score" in result.loser_reasons["cand-22-no"]


def test_selector_keeps_large_lcb_kelly_edge_over_tiny_cheap_tail():
    tiny_tail = _evaluation(
        candidate_id="cand-cheap-tail",
        condition_id="condition-cheap",
        token_id="token-cheap",
        direction="buy_yes",
        bin_label="cheap-tail",
        execution_price=0.01,
        q_posterior=0.05,
        q_lcb_5pct=0.02,
        trade_score=0.005,
    )
    larger_lcb_edge = _evaluation(
        candidate_id="cand-larger",
        condition_id="condition-larger",
        token_id="token-larger",
        direction="buy_no",
        bin_label="better-edge",
        execution_price=0.70,
        q_posterior=0.90,
        q_lcb_5pct=0.86,
        trade_score=0.05,
    )

    result = select_best_family_candidate((larger_lcb_edge, tiny_tail))

    assert result.selected is larger_lcb_edge


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
    assert "robust_kelly_growth_score" in receipt["loser_reasons"]["loser"]
    assert receipt["cache_summary"]["price_cache"] == "snapshot_rows_refreshed_for_family"
