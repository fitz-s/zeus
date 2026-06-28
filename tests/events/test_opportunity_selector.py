from __future__ import annotations

# Created: 2026-06-06
# Last reused/audited: 2026-06-07
# Authority basis: Operator request — family-bin selector must choose the best sibling opportunity, not the arrival-triggered token.

import pytest

from src.decision import selection_calibrator as sc
from src.events.candidate_evaluation import CandidateEvaluation
from src.events.opportunity_book import build_family_opportunity_book
from src.events.opportunity_selector import select_best_family_candidate


@pytest.fixture(autouse=True)
def _isolate_selection_curse_bound(monkeypatch):
    monkeypatch.setattr("src.decision.selection_curse_bound_loader.load_bound", lambda: None)


def _selection_artifact(*, direction: str, raw_side_prob: float, hit_rate: float = 0.99):
    side = "NO" if str(direction).lower() == "buy_no" else "YES"
    key = sc.cell_key(
        side=side,
        lead_days=1.0,
        bin_class="nonmodal",
        raw_side_prob=raw_side_prob,
    )
    return {
        "_meta": {
            "posterior_version": sc.DEFAULT_POSTERIOR_VERSION,
            "min_n": 30,
            "armed_sides": ["YES", "NO"],
        },
        "cells": {key: {"n": 10000, "hit_rate": hit_rate}},
    }


def _evaluation(**overrides):
    base = dict(
        candidate_id="cand-expensive",
        family_id="family-1",
        condition_id="condition-expensive",
        token_id="token-expensive",
        direction="buy_no",
        bin_label="16C",
        execution_price=0.70,
        q_posterior=0.90,
        q_lcb_5pct=0.86,
        c_cost_95pct=0.71,
        p_fill_lcb=0.9,
        trade_score=0.015,
        p_value=0.01,
        passed_prefilter=True,
        native_quote_available=True,
        low_volume_usd=10.0,
        same_bin_yes_posterior=0.10,
    )
    base.update(overrides)
    base.setdefault(
        "selection_calibrator_artifact",
        _selection_artifact(
            direction=str(base["direction"]),
            raw_side_prob=float(base["q_posterior"]),
        ),
    )
    return CandidateEvaluation(**base)


def test_selector_prefers_lcb_kelly_growth_over_modal_adjacent_no():
    modal_adjacent_no = _evaluation(
        candidate_id="cand-22-no",
        condition_id="condition-22",
        token_id="token-22-no",
        bin_label="22C",
        execution_price=0.70,
        q_posterior=0.95,
        q_lcb_5pct=0.86,
        trade_score=0.015,
    )
    better_sibling = _evaluation(
        candidate_id="cand-23-yes",
        condition_id="condition-23",
        token_id="token-23-yes",
        direction="buy_yes",
        bin_label="23C",
        execution_price=0.30,
        q_posterior=0.62,
        q_lcb_5pct=0.58,
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


def test_selector_allows_low_win_rate_positive_ev_when_capital_efficient():
    low_win_rate_lottery = _evaluation(
        candidate_id="cand-low-win-rate",
        condition_id="condition-low-win-rate",
        token_id="token-low-win-rate",
        direction="buy_yes",
        bin_label="cheap-tail",
        execution_price=0.20,
        q_posterior=0.45,
        q_lcb_5pct=0.42,
        trade_score=0.22,
    )
    stable_win_rate_edge = _evaluation(
        candidate_id="cand-stable-win-rate",
        condition_id="condition-stable-win-rate",
        token_id="token-stable-win-rate",
        direction="buy_no",
        bin_label="stable-edge",
        execution_price=0.70,
        q_posterior=0.90,
        q_lcb_5pct=0.86,
        trade_score=0.03,
    )

    result = select_best_family_candidate((low_win_rate_lottery, stable_win_rate_edge))

    assert result.selected is low_win_rate_lottery or result.selected is stable_win_rate_edge
    assert not result.loser_reasons.get("cand-low-win-rate", "").startswith("ADMISSION_WIN_RATE_FLOOR:")


def test_selector_can_choose_buy_yes_below_win_rate_floor_when_objective_best():
    cheap_buy_yes = _evaluation(
        candidate_id="cand-cheap-buy-yes",
        condition_id="condition-cheap-buy-yes",
        token_id="token-cheap-buy-yes",
        direction="buy_yes",
        bin_label="tail-yes",
        execution_price=0.05,
        q_posterior=0.20,
        q_lcb_5pct=0.12,
        trade_score=0.07,
    )
    expensive_buy_no = _evaluation(
        candidate_id="cand-expensive-buy-no",
        condition_id="condition-expensive-buy-no",
        token_id="token-expensive-buy-no",
        direction="buy_no",
        bin_label="adjacent-no",
        execution_price=0.65,
        q_posterior=0.82,
        q_lcb_5pct=0.75,
        trade_score=0.03,
        q_lcb_calibration_source="EMOS_ANALYTIC",
    )

    result = select_best_family_candidate((expensive_buy_no, cheap_buy_yes))

    assert cheap_buy_yes.live_win_rate_admissible is False
    assert cheap_buy_yes.admitted is True
    assert result.selected is cheap_buy_yes
    assert result.loser_reasons["cand-expensive-buy-no"].startswith("FAMILY_RANK_LOST:")


def test_selector_ranks_low_payout_capital_inefficient_candidate_below_better_sibling():
    low_payout = _evaluation(
        candidate_id="cand-low-payout",
        condition_id="condition-low-payout",
        token_id="token-low-payout",
        direction="buy_no",
        bin_label="31C",
        execution_price=0.93,
        q_posterior=0.97,
        q_lcb_5pct=0.95,
        trade_score=0.02,
    )
    stable_edge = _evaluation(
        candidate_id="cand-stable-edge",
        condition_id="condition-stable-edge",
        token_id="token-stable-edge",
        direction="buy_no",
        bin_label="32C",
        execution_price=0.70,
        q_posterior=0.90,
        q_lcb_5pct=0.86,
        trade_score=0.04,
    )

    result = select_best_family_candidate((low_payout, stable_edge))

    assert low_payout.admitted is True
    assert result.selected is stable_edge
    assert result.loser_reasons["cand-low-payout"].startswith("FAMILY_RANK_LOST:")


def test_selector_uses_candidate_kelly_size_for_expected_dollars():
    high_roi_tiny_size = _evaluation(
        candidate_id="cand-high-roi-tiny-size",
        condition_id="condition-high-roi-tiny-size",
        token_id="token-high-roi-tiny-size",
        direction="buy_yes",
        bin_label="tiny",
        execution_price=0.10,
        q_posterior=0.30,
        q_lcb_5pct=0.22,
        trade_score=0.10,
        kelly_size_usd=0.05,
    )
    lower_roi_real_size = _evaluation(
        candidate_id="cand-lower-roi-real-size",
        condition_id="condition-lower-roi-real-size",
        token_id="token-lower-roi-real-size",
        direction="buy_no",
        bin_label="real-size",
        execution_price=0.70,
        q_posterior=0.90,
        q_lcb_5pct=0.86,
        trade_score=0.08,
        kelly_size_usd=10.0,
    )

    result = select_best_family_candidate((high_roi_tiny_size, lower_roi_real_size))

    assert result.selected is lower_roi_real_size
    assert result.loser_reasons["cand-high-roi-tiny-size"].startswith("FAMILY_RANK_LOST:")


def test_selector_does_not_choose_low_roi_boundary_candidate_on_dollars_alone():
    low_roi_boundary = _evaluation(
        candidate_id="cand-low-roi-boundary",
        condition_id="condition-low-roi-boundary",
        token_id="token-low-roi-boundary",
        direction="buy_no",
        bin_label="boundary-no",
        execution_price=0.93,
        q_posterior=0.97,
        q_lcb_5pct=0.95,
        trade_score=0.02,
        kelly_size_usd=240.0,
        q_lcb_calibration_source="EMOS_ANALYTIC",
    )
    efficient_sibling = _evaluation(
        candidate_id="cand-efficient-sibling",
        condition_id="condition-efficient-sibling",
        token_id="token-efficient-sibling",
        direction="buy_yes",
        bin_label="efficient-sibling",
        execution_price=0.38,
        q_posterior=0.62,
        q_lcb_5pct=0.58,
        trade_score=0.20,
        kelly_size_usd=8.0,
    )

    assert low_roi_boundary.expected_robust_dollars > efficient_sibling.expected_robust_dollars

    result = select_best_family_candidate((low_roi_boundary, efficient_sibling))

    assert result.selected is efficient_sibling
    assert result.loser_reasons["cand-low-roi-boundary"].startswith("FAMILY_RANK_LOST:")


def test_selector_excludes_buy_no_on_material_yes_bin():
    manila_like = _evaluation(
        candidate_id="cand-manila-like",
        condition_id="condition-manila-like",
        token_id="token-manila-like",
        direction="buy_no",
        bin_label="33C",
        execution_price=0.62,
        q_posterior=0.77,
        q_lcb_5pct=0.667,
        trade_score=0.021,
        same_bin_yes_posterior=0.23,
    )
    stable_edge = _evaluation(
        candidate_id="cand-stable-edge",
        condition_id="condition-stable-edge",
        token_id="token-stable-edge",
        direction="buy_no",
        bin_label="35C",
        execution_price=0.70,
        q_posterior=0.95,
        q_lcb_5pct=0.86,
        trade_score=0.04,
    )

    result = select_best_family_candidate((manila_like, stable_edge))

    assert result.selected is stable_edge
    assert result.loser_reasons["cand-manila-like"].startswith("ADMISSION_BUY_NO_CONSERVATIVE_EVIDENCE_MISSING:")


# REMOVED 2026-06-08 (S4; "bin selection.md" §6/§9 Hidden #10 + operator directive):
# test_selector_rejects_buy_no_on_own_forecast_modal_bin pinned the now-removed
# _family_structure_rejection_reasons ADMISSION_BUY_NO_ON_FORECAST_MODAL_BIN guard.
# That bolted-on side-specific exclusion is SUBSUMED by the marginal-utility ranker:
# a modal-bin NO scores lower ΔU than the modal YES through ONE FamilyPayoffMatrix
# (its honest robust NO q_lcb = 1 - q_ucb_yes is small on a high-YES-mass bin), so
# the matrix dominates it without a guard. The replacement relationship test is
# tests/engine/test_s4_subsumed_gates.py::
# test_modal_bin_no_dominated_by_modal_yes_through_one_matrix. select_best_family_
# candidate is now display-only and no longer the live decision, so a modal-bin
# loser-reason is no longer emitted.


def test_opportunity_book_receipt_contains_ranks_and_loser_reasons():
    # SINGLE-PATH update 2026-06-08 (operator directive; "bin selection.md"
    # §14.7/§14.8): the book no longer SELF-SELECTS via the legacy scalar-Kelly
    # select_best_family_candidate. The DECISION is the bin-selection ΔU ranker
    # upstream; the book RECORDS it via decided_candidate_id. select_best_family_
    # candidate is retained ONLY for the receipt's display ranks + loser reasons
    # (provenance), which this test still verifies. We pass the decided id ("selected")
    # so the book records the ΔU decision rather than re-deriving it.
    selected = _evaluation(candidate_id="selected", execution_price=0.2, trade_score=0.02)
    loser = _evaluation(candidate_id="loser", execution_price=0.80, trade_score=0.02)

    book = build_family_opportunity_book(
        family_id="family-1",
        evaluations=(loser, selected),
        event_id="event-1",
        decided_candidate_id="selected",
        cache_summary={
            "price_cache": "snapshot_rows_refreshed_for_family",
            "selector_enabled": True,
        },
    )
    receipt = book.to_receipt_dict()

    # The book records the upstream ΔU decision verbatim (no second selection surface).
    assert receipt["selected_candidate_id"] == "selected"
    assert receipt["proposed_selected_candidate_id"] == "selected"
    # Display ranks + loser reasons still come from select_best_family_candidate
    # (provenance only, not the decision).
    assert receipt["family_rank"]["selected"] == 1
    assert receipt["loser_reasons"]["loser"].startswith("FAMILY_RANK_LOST:")
    assert "robust_kelly_growth_score" in receipt["loser_reasons"]["loser"]
    assert receipt["cache_summary"]["price_cache"] == "snapshot_rows_refreshed_for_family"
