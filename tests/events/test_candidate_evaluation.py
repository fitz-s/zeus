from __future__ import annotations

# Created: 2026-06-06
# Last reused/audited: 2026-06-06
# Authority basis: Operator request — event-driven Opportunity Book selector must separate admission from selection and keep low-volume markets eligible.

import pytest

from src.decision import selection_calibrator as sc
from src.events.candidate_evaluation import CandidateEvaluation
from src.events.opportunity_book import build_family_opportunity_book


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


def test_candidate_evaluation_computes_robust_ev_per_dollar_and_expected_dollars():
    evaluation = CandidateEvaluation(
        candidate_id="cand-1",
        family_id="family-1",
        condition_id="condition-1",
        token_id="token-1",
        direction="buy_yes",
        bin_label="16C",
        execution_price=0.80,
        q_posterior=0.90,
        q_lcb_5pct=0.88,
        c_cost_95pct=0.81,
        p_fill_lcb=0.9,
        trade_score=0.08,
        p_value=0.01,
        passed_prefilter=True,
        native_quote_available=True,
        kelly_size_usd=10.0,
        low_volume_usd=5.0,
        same_bin_yes_posterior=0.10,
        selection_calibrator_artifact=_selection_artifact(
            direction="buy_yes",
            raw_side_prob=0.90,
        ),
    )

    assert evaluation.admitted is True
    assert evaluation.robust_ev_per_dollar == pytest.approx(0.10)
    assert evaluation.robust_kelly_fraction_lcb == pytest.approx((0.88 - 0.80) / (1.0 - 0.80), abs=1e-12)
    assert evaluation.robust_kelly_growth_score == pytest.approx(0.04, abs=1e-12)
    assert evaluation.expected_robust_dollars == pytest.approx(1.0)
    assert evaluation.capital_weighted_growth_score == pytest.approx(0.04)


def test_candidate_evaluation_expected_dollars_uses_actual_sized_capital_not_depth():
    evaluation = CandidateEvaluation(
        candidate_id="cand-depth",
        family_id="family-1",
        condition_id="condition-1",
        token_id="token-1",
        direction="buy_yes",
        bin_label="tail",
        execution_price=0.04,
        q_posterior=0.12,
        q_lcb_5pct=0.06,
        c_cost_95pct=0.041,
        p_fill_lcb=0.9,
        trade_score=0.02,
        p_value=0.01,
        passed_prefilter=True,
        native_quote_available=True,
        max_executable_shares=100_000.0,
    )

    assert evaluation.expected_robust_dollars == 0.0
    assert evaluation.capital_weighted_growth_score == 0.0


def test_candidate_evaluation_low_volume_is_not_an_admission_reject():
    evaluation = CandidateEvaluation(
        candidate_id="cand-1",
        family_id="family-1",
        condition_id="condition-1",
        token_id="token-1",
        direction="buy_no",
        bin_label="new-market",
        execution_price=0.2,
        q_posterior=0.95,
        q_lcb_5pct=0.86,
        c_cost_95pct=0.21,
        p_fill_lcb=0.5,
        trade_score=0.03,
        p_value=0.02,
        passed_prefilter=True,
        native_quote_available=True,
        low_volume_usd=0.0,
        same_bin_yes_posterior=0.05,
        selection_calibrator_artifact=_selection_artifact(
            direction="buy_no",
            raw_side_prob=0.95,
        ),
    )

    assert evaluation.admitted is True
    assert evaluation.to_receipt_dict()["low_volume_usd"] == 0.0


def test_candidate_evaluation_low_probability_is_not_rejected_by_absolute_q():
    evaluation = CandidateEvaluation(
        candidate_id="cand-1",
        family_id="family-1",
        condition_id="condition-1",
        token_id="token-1",
        direction="buy_yes",
        bin_label="cheap-lottery",
        execution_price=0.2,
        q_posterior=0.5,
        q_lcb_5pct=0.45,
        c_cost_95pct=0.21,
        p_fill_lcb=0.5,
        trade_score=0.05,
        p_value=0.02,
        passed_prefilter=True,
        native_quote_available=True,
    )

    receipt = evaluation.to_receipt_dict()

    assert evaluation.admitted is False
    assert evaluation.live_win_rate_admissible is True
    assert evaluation.selection_calibrator_admissible is False
    assert receipt["live_win_rate_admissible"] is True
    assert receipt["live_win_rate_floor"] == 0.0


def test_candidate_evaluation_keeps_positive_ev_low_payout_for_ranking():
    evaluation = CandidateEvaluation(
        candidate_id="cand-1",
        family_id="family-1",
        condition_id="condition-1",
        token_id="token-1",
        direction="buy_no",
        bin_label="low-payout-no",
        execution_price=0.93,
        q_posterior=0.97,
        q_lcb_5pct=0.95,
        c_cost_95pct=0.94,
        p_fill_lcb=0.8,
        trade_score=0.02,
        p_value=0.01,
        passed_prefilter=True,
        native_quote_available=True,
        same_bin_yes_posterior=0.03,
        selection_calibrator_artifact=_selection_artifact(
            direction="buy_no",
            raw_side_prob=0.97,
        ),
    )

    assert evaluation.admitted is True
    assert evaluation.live_capital_efficiency_reason is None
    assert evaluation.max_payout_roi < 0.10


def test_opportunity_book_keeps_admission_separate_from_live_selection():
    evaluation = CandidateEvaluation(
        candidate_id="cand-positive-edge",
        family_id="family-1",
        condition_id="condition-1",
        token_id="token-1",
        direction="buy_yes",
        bin_label="31C",
        execution_price=0.07,
        q_posterior=0.12,
        q_lcb_5pct=0.10,
        c_cost_95pct=0.08,
        p_fill_lcb=0.8,
        trade_score=0.03,
        p_value=0.01,
        passed_prefilter=True,
        native_quote_available=True,
    )

    book = build_family_opportunity_book(
        family_id="family-1",
        evaluations=(evaluation,),
        event_id="event-1",
        decided_candidate_id=None,
    ).to_receipt_dict()
    receipt = book["candidates"][0]

    assert evaluation.admitted is True
    assert receipt["admitted"] is True
    assert receipt["live_decision_selected"] is False
    assert book["admitted_count"] == 1


def test_opportunity_book_counts_qkernel_selected_candidate_as_live_admitted():
    """The live book must not serialize qkernel selected as unadmitted."""

    evaluation = CandidateEvaluation(
        candidate_id="ba-tail-yes",
        family_id="family-1",
        condition_id="condition-1",
        token_id="token-1",
        direction="buy_yes",
        bin_label="11C",
        execution_price=0.041,
        q_posterior=0.24833093804728934,
        q_lcb_5pct=0.0990451308919892,
        c_cost_95pct=0.041,
        p_fill_lcb=0.9997,
        trade_score=0.058,
        p_value=0.05,
        passed_prefilter=True,
        native_quote_available=True,
    )

    book = build_family_opportunity_book(
        family_id="family-1",
        evaluations=(evaluation,),
        event_id="event-1",
        decided_candidate_id="ba-tail-yes",
        cache_summary={
            "actual_receipt_selected_candidate_id": "ba-tail-yes",
            "selection_authority": "qkernel_spine",
            "selected_qkernel_execution_economics": {
                "source": "qkernel_spine",
                "candidate_id": "DIRECT_YES:bin-11c",
                "route_id": "DIRECT_YES:bin-11c@proof",
                "side": "YES",
                "direction_law_ok": True,
                "coherence_allows": True,
                "cost": 0.041,
                "edge_lcb": 0.0580451308919892,
                "payoff_q_point": 0.24833093804728934,
                "payoff_q_lcb": 0.0990451308919892,
                "optimal_stake_usd": 1.0,
                "optimal_delta_u": 0.01,
                "delta_u_at_min": 0.01,
                "false_edge_rate": 0.05,
                "selection_guard_basis": "SELECTION_BETA_95",
                "selection_guard_abstained": False,
                "selection_guard_q_safe": 0.0990451308919892,
            },
        },
    ).to_receipt_dict()
    receipt = book["candidates"][0]

    assert evaluation.admitted is True
    assert receipt["admitted"] is True
    assert receipt["live_decision_selected"] is True
    assert receipt["live_selection_authority"] == "qkernel_spine"
    assert receipt["live_admission_authority"] == "qkernel_spine"
    assert book["admitted_count"] == 1
    assert book["selected_objective"]["authority"] == "qkernel_spine"


def test_candidate_evaluation_projects_upstream_buy_no_rejection():
    evaluation = CandidateEvaluation(
        candidate_id="cand-1",
        family_id="family-1",
        condition_id="condition-1",
        token_id="token-1",
        direction="buy_no",
        bin_label="33C",
        execution_price=0.62,
        q_posterior=0.77,
        q_lcb_5pct=0.667,
        c_cost_95pct=0.63,
        p_fill_lcb=0.84,
        trade_score=0.021,
        p_value=0.01,
        passed_prefilter=True,
        native_quote_available=True,
        same_bin_yes_posterior=0.23,
        missing_reason=(
            "ADMISSION_BUY_NO_CONSERVATIVE_EVIDENCE_MISSING:"
            "yes_posterior=0.230000"
        ),
    )

    assert evaluation.admitted is False
    assert evaluation.live_buy_no_conservative_evidence_reason is not None
    assert evaluation.live_buy_no_conservative_evidence_reason.startswith("ADMISSION_BUY_NO_CONSERVATIVE_EVIDENCE_MISSING:")


def test_candidate_evaluation_does_not_redecide_certified_buy_no_projection():
    evaluation = CandidateEvaluation(
        candidate_id="cand-1",
        family_id="family-1",
        condition_id="condition-1",
        token_id="token-1",
        direction="buy_no",
        bin_label="33C",
        execution_price=0.32,
        q_posterior=0.652,
        q_lcb_5pct=0.617,
        c_cost_95pct=0.33,
        p_fill_lcb=0.84,
        trade_score=0.20,
        p_value=0.01,
        passed_prefilter=True,
        native_quote_available=True,
        q_lcb_calibration_source="FORECAST_BOOTSTRAP",
        same_bin_yes_posterior=0.348,
        settlement_coverage_status="INSUFFICIENT_DATA",
    )

    assert evaluation.live_buy_no_conservative_evidence_reason is None
    assert evaluation.admitted is True


def test_candidate_evaluation_objective_prioritizes_lcb_kelly_growth():
    modal_adjacent_low_growth = CandidateEvaluation(
        candidate_id="modal-adjacent-low-growth",
        family_id="family-1",
        condition_id="condition-1",
        token_id="token-1",
        direction="buy_no",
        bin_label="22C",
        execution_price=0.70,
        q_posterior=0.95,
        q_lcb_5pct=0.86,
        c_cost_95pct=0.71,
        p_fill_lcb=0.5,
        trade_score=0.01,
        p_value=0.02,
        passed_prefilter=True,
        native_quote_available=True,
        same_bin_yes_posterior=0.05,
    )
    better_family_trade = CandidateEvaluation(
        candidate_id="better-family-trade",
        family_id="family-1",
        condition_id="condition-2",
        token_id="token-2",
        direction="buy_yes",
        bin_label="23C",
        execution_price=0.30,
        q_posterior=0.62,
        q_lcb_5pct=0.58,
        c_cost_95pct=0.31,
        p_fill_lcb=0.5,
        trade_score=0.04,
        p_value=0.01,
        passed_prefilter=True,
        native_quote_available=True,
    )

    assert better_family_trade.robust_kelly_growth_score > modal_adjacent_low_growth.robust_kelly_growth_score
    assert better_family_trade.objective_tuple > modal_adjacent_low_growth.objective_tuple


def test_candidate_evaluation_objective_requires_capital_weighted_growth_not_dollars_only():
    low_roi_large_ticket = CandidateEvaluation(
        candidate_id="low-roi-large-ticket",
        family_id="family-1",
        condition_id="condition-1",
        token_id="token-1",
        direction="buy_no",
        bin_label="expensive-boundary-no",
        execution_price=0.93,
        q_posterior=0.97,
        q_lcb_5pct=0.95,
        c_cost_95pct=0.94,
        p_fill_lcb=0.8,
        trade_score=0.02,
        p_value=0.01,
        passed_prefilter=True,
        native_quote_available=True,
        kelly_size_usd=240.0,
        same_bin_yes_posterior=0.03,
    )
    efficient_sibling = CandidateEvaluation(
        candidate_id="efficient-sibling",
        family_id="family-1",
        condition_id="condition-2",
        token_id="token-2",
        direction="buy_yes",
        bin_label="efficient-sibling",
        execution_price=0.38,
        q_posterior=0.62,
        q_lcb_5pct=0.58,
        c_cost_95pct=0.39,
        p_fill_lcb=0.8,
        trade_score=0.20,
        p_value=0.01,
        passed_prefilter=True,
        native_quote_available=True,
        kelly_size_usd=8.0,
    )

    assert low_roi_large_ticket.expected_robust_dollars > efficient_sibling.expected_robust_dollars
    assert efficient_sibling.capital_weighted_growth_score > low_roi_large_ticket.capital_weighted_growth_score
    assert efficient_sibling.objective_tuple > low_roi_large_ticket.objective_tuple
