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
