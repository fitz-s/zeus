from __future__ import annotations

# Created: 2026-06-06
# Last reused/audited: 2026-06-06
# Authority basis: Operator request — event-driven Opportunity Book selector must separate admission from selection and keep low-volume markets eligible.

import pytest

from src.events.candidate_evaluation import CandidateEvaluation


def test_candidate_evaluation_computes_robust_ev_per_dollar_and_expected_dollars():
    evaluation = CandidateEvaluation(
        candidate_id="cand-1",
        family_id="family-1",
        condition_id="condition-1",
        token_id="token-1",
        direction="buy_no",
        bin_label="16C",
        execution_price=0.99,
        q_posterior=0.997,
        q_lcb_5pct=0.990,
        c_cost_95pct=0.995,
        p_fill_lcb=0.9,
        trade_score=0.0099,
        p_value=0.01,
        passed_prefilter=True,
        native_quote_available=True,
        kelly_size_usd=10.0,
        low_volume_usd=5.0,
    )

    assert evaluation.admitted is True
    assert evaluation.robust_ev_per_dollar == pytest.approx(0.01)
    assert evaluation.robust_kelly_fraction_lcb == pytest.approx((0.990 - 0.99) / (1.0 - 0.99), abs=1e-12)
    assert evaluation.robust_kelly_growth_score == pytest.approx(0.0, abs=1e-12)
    assert evaluation.expected_robust_dollars == pytest.approx(0.1)


def test_candidate_evaluation_low_volume_is_not_an_admission_reject():
    evaluation = CandidateEvaluation(
        candidate_id="cand-1",
        family_id="family-1",
        condition_id="condition-1",
        token_id="token-1",
        direction="buy_no",
        bin_label="new-market",
        execution_price=0.2,
        q_posterior=0.5,
        q_lcb_5pct=0.45,
        c_cost_95pct=0.21,
        p_fill_lcb=0.5,
        trade_score=0.01,
        p_value=0.02,
        passed_prefilter=True,
        native_quote_available=True,
        low_volume_usd=0.0,
    )

    assert evaluation.admitted is True
    assert evaluation.to_receipt_dict()["low_volume_usd"] == 0.0


def test_candidate_evaluation_objective_prioritizes_lcb_kelly_growth():
    modal_adjacent_low_growth = CandidateEvaluation(
        candidate_id="modal-adjacent-low-growth",
        family_id="family-1",
        condition_id="condition-1",
        token_id="token-1",
        direction="buy_no",
        bin_label="22C",
        execution_price=0.70,
        q_posterior=0.80,
        q_lcb_5pct=0.72,
        c_cost_95pct=0.71,
        p_fill_lcb=0.5,
        trade_score=0.02,
        p_value=0.02,
        passed_prefilter=True,
        native_quote_available=True,
    )
    better_family_trade = CandidateEvaluation(
        candidate_id="better-family-trade",
        family_id="family-1",
        condition_id="condition-2",
        token_id="token-2",
        direction="buy_yes",
        bin_label="23C",
        execution_price=0.30,
        q_posterior=0.46,
        q_lcb_5pct=0.42,
        c_cost_95pct=0.31,
        p_fill_lcb=0.5,
        trade_score=0.04,
        p_value=0.01,
        passed_prefilter=True,
        native_quote_available=True,
    )

    assert better_family_trade.robust_kelly_growth_score > modal_adjacent_low_growth.robust_kelly_growth_score
    assert better_family_trade.objective_tuple > modal_adjacent_low_growth.objective_tuple
