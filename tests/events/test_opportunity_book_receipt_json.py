from __future__ import annotations

# Created: 2026-06-06
# Last reused/audited: 2026-07-11
# Authority basis: Operator request — Opportunity Book evidence must persist in no-submit receipt_json without schema migration.

import json
import math

import pytest

from src.decision_kernel.canonicalization import qkernel_current_state_identity_hash
from src.events.candidate_evaluation import CandidateEvaluation
from src.events.no_submit_receipts import _receipt_json
from src.events.opportunity_book import build_family_opportunity_book
from src.events.reactor import EventSubmissionReceipt


def test_opportunity_book_omitted_from_receipt_json_when_absent():
    payload = json.loads(
        _receipt_json(
            EventSubmissionReceipt(
                submitted=False,
                event_id="event-1",
                final_intent_id="intent-1",
                side_effect_status="NO_SUBMIT",
                proof_accepted=True,
            )
        )
    )

    assert "opportunity_book" not in payload


def test_global_jit_refinements_are_internal_receipt_transport_only():
    payload = json.loads(
        _receipt_json(
            EventSubmissionReceipt(
                submitted=False,
                event_id="event-jit",
                global_jit_candidate=object(),
                global_jit_payoff_q_lcb=0.71,
            )
        )
    )

    assert "global_jit_candidate" not in payload
    assert "global_jit_payoff_q_lcb" not in payload


def test_opportunity_book_included_in_receipt_json_when_present():
    payload = json.loads(
        _receipt_json(
            EventSubmissionReceipt(
                submitted=False,
                event_id="event-1",
                final_intent_id="intent-1",
                side_effect_status="NO_SUBMIT",
                proof_accepted=True,
                opportunity_book={
                    "book_id": "book-1",
                    "selected_candidate_id": "candidate-1",
                    "loser_reasons": {"candidate-2": "FAMILY_RANK_LOST:rank=2"},
                },
            )
        )
    )

    assert payload["opportunity_book"]["book_id"] == "book-1"
    assert payload["opportunity_book"]["selected_candidate_id"] == "candidate-1"


def test_mean_selected_global_candidate_cannot_claim_live_admission():
    point = 0.87
    lcb = 0.11
    cost = 0.33
    shares = 10.0
    expected_cost = cost * shares
    loss_payoff = -expected_cost
    win_payoff = shares - expected_cost
    wealth_after_loss = 100.0 + loss_payoff
    wealth_after_win = 100.0 + win_payoff
    expected_du = (1.0 - point) * math.log(
        wealth_after_loss / 100.0
    ) + point * math.log(wealth_after_win / 100.0)
    expected_ev = point * shares - expected_cost
    cert = {
        "source": "qkernel_spine",
        "decision_id": "decision-mean",
        "receipt_hash": "receipt-mean",
        "q_version": "q-mean",
        "sample_hash": "sample-mean",
        "q_lcb_guard_basis": "CURRENT_POSTERIOR_BAND",
        "q_lcb_guard_abstained": False,
        "q_lcb_guard_cell_key": "sample-mean",
        "selection_guard_basis": "CURRENT_POSTERIOR_PREDICTIVE_MEAN",
        "selection_guard_abstained": False,
        "selection_guard_cell_key": "sample-mean",
        "selection_guard_n": 500,
        "side": "NO",
        "payoff_q_point": point,
        "payoff_q_lcb": lcb,
        "payoff_q_action": point,
        "cost": cost,
        "edge_lcb": lcb - cost,
        "edge_expected": point - cost,
        "global_actuation_identity": "actuation-mean",
        "global_economic_identity": "economic-mean",
        "global_optimum_semantics": "CUT_TIME_GLOBAL_OPTIMUM",
        "global_probability_functional": "POSTERIOR_PREDICTIVE_MEAN",
        "global_candidate_id": "global-candidate-mean",
        "global_bin_id": "bin-mean",
        "global_universe_witness_identity": "universe-mean",
        "global_wealth_witness_identity": "wealth-mean",
        "global_wealth_economic_identity": "wealth-economic-mean",
        "global_selection_epoch_identity": "epoch-mean",
        "global_selection_cut_at": "2026-07-26T07:51:12+00:00",
        "global_selection_decision_at": "2026-07-26T07:51:14+00:00",
        "global_jit_book_hash": "book-mean",
        "global_jit_venue_book_hash": "venue-book-mean",
        "global_jit_book_snapshot_id": "snapshot-mean",
        "global_jit_execution_curve_identity": "curve-mean",
        "global_target_shares": str(shares),
        "global_expected_cost_usd": str(expected_cost),
        "global_max_spend_usd": str(expected_cost),
        "global_expected_delta_log_wealth": expected_du,
        "global_expected_ev_usd": expected_ev,
        "global_expected_capital_efficiency": expected_du / expected_cost,
        "global_cut_time_win_probability_mean": point,
        "global_cut_time_loss_probability_mean": 1.0 - point,
        "global_terminal_win_probability_mean": point,
        "global_terminal_loss_probability_mean": 1.0 - point,
        "global_terminal_loss_payoff_usd": str(loss_payoff),
        "global_terminal_win_payoff_usd": str(win_payoff),
        "global_terminal_median_payoff_usd": str(win_payoff),
        "global_terminal_wealth_after_loss_usd": str(wealth_after_loss),
        "global_terminal_wealth_after_win_usd": str(wealth_after_win),
        "global_cut_time_expected_value_usd": expected_ev,
        "global_expected_value_usd": expected_ev,
        "global_expected_value_semantics": (
            "POINT_EVIDENCE_EXPECTATION_NOT_REALIZED_GAIN"
        ),
        "global_terminal_payoff_semantics": "BINARY_0_1",
    }
    cert["current_state_identity_hash"] = qkernel_current_state_identity_hash(cert)
    candidate = CandidateEvaluation(
        candidate_id="candidate-no",
        family_id="family-mean",
        condition_id="condition-no",
        token_id="token-no",
        direction="buy_no",
        bin_label="30C",
        execution_price=cost,
        q_posterior=point,
        q_lcb_5pct=lcb,
        c_cost_95pct=cost,
        p_fill_lcb=1.0,
        trade_score=point - cost,
        p_value=0.004,
        passed_prefilter=True,
        native_quote_available=True,
        qkernel_execution_economics=cert,
    )
    assert candidate.admitted is False

    payload = build_family_opportunity_book(
        family_id="family-mean",
        evaluations=(candidate,),
        event_id="event-mean",
        decided_candidate_id=candidate.candidate_id,
        cache_summary={
            "actual_receipt_selected_candidate_id": candidate.candidate_id,
            "selection_authority": "qkernel_spine",
            "selected_qkernel_execution_economics": cert,
        },
    ).to_receipt_dict()

    assert payload["candidates"][0]["admitted"] is False
    assert "objective_semantics" not in payload["selected_objective"]

    from src.engine.event_reactor_adapter import (
        _assert_event_bound_receipt_live_authority,
    )

    with pytest.raises(ValueError):
        _assert_event_bound_receipt_live_authority(
            EventSubmissionReceipt(
                submitted=False,
                event_id="event-mean",
                condition_id="condition-no",
                token_id="token-no",
                direction="buy_no",
                q_source="qkernel_spine",
                q_live=point,
                q_lcb_5pct=lcb,
                qkernel_execution_economics=cert,
                opportunity_book=payload,
            )
        )
