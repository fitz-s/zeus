# Created: 2026-06-23
# Last audited: 2026-06-23
# Authority basis: docs/archive/2026-Q2/plans_historical/2026-06-23-full-lifecycle-audit-impl.md (Phase 1, P0-1) +
#   external lifecycle audit P0-1 (taker authorization must use an execution-conditioned bound,
#   not the plain model q_lcb). Antibody: reverting the q_exec_lcb bound in
#   select_rest_then_cross_mode (back to gating on q_lcb) re-admits the blocked-taker case below.
"""The taker admissibility bound must honour q_exec_lcb when supplied.

select_rest_then_cross_mode's FIX-B bound currently gates on q_lcb. With q_exec_lcb supplied
(min(q_decision_lcb, settlement-evidenced cell LCB)), a taker that clears q_lcb but NOT the lower
q_exec_lcb must be refused. q_exec_lcb=None preserves today's exact behavior (identity). q_exec_lcb
can only TIGHTEN — a higher value never loosens the gate beyond q_lcb.
"""
from __future__ import annotations

from src.strategy.live_inference.mode_consistent_ev import (
    POLICY_MAKER_TAKER_FORBIDDEN,
    POLICY_TAKER_ESCALATED_AFTER_REST,
    select_rest_then_cross_mode,
)

# A two-sided, tight book where the escalated taker lane is reachable and maker is admissible.
_BOOK = dict(
    p_fill_taker=1.0,
    best_bid=0.68,
    best_ask=0.71,
    tick_size=0.01,
    reservation=0.69,
    escalated_after_rest=True,
)


def test_escalated_taker_crosses_under_q_lcb_when_no_exec_bound_supplied():
    # cost 0.70 <= q_lcb 0.72 -> the deadline-escalation cross fires (today's behavior, unchanged).
    r = select_rest_then_cross_mode(q_lcb=0.72, taker_all_in_cost=0.70, **_BOOK)
    assert r.chosen_mode == "TAKER"
    assert r.policy == POLICY_TAKER_ESCALATED_AFTER_REST


def test_lower_q_exec_lcb_blocks_a_taker_that_clears_q_lcb():
    # Same book: cost 0.70 <= q_lcb 0.72 (would cross), but q_exec_lcb 0.55 (settlement-evidenced
    # adverse selection: this fill class only pays ~55%) refuses it -> no cross.
    r = select_rest_then_cross_mode(
        q_lcb=0.72, q_exec_lcb=0.55, taker_all_in_cost=0.70, **_BOOK
    )
    assert r.chosen_mode == "MAKER"
    assert r.policy == POLICY_MAKER_TAKER_FORBIDDEN


def test_q_exec_lcb_none_is_identity_to_omitting_it():
    explicit = select_rest_then_cross_mode(
        q_lcb=0.72, q_exec_lcb=None, taker_all_in_cost=0.70, **_BOOK
    )
    omitted = select_rest_then_cross_mode(q_lcb=0.72, taker_all_in_cost=0.70, **_BOOK)
    assert explicit.chosen_mode == omitted.chosen_mode == "TAKER"
    assert explicit.policy == omitted.policy == POLICY_TAKER_ESCALATED_AFTER_REST


def test_high_q_exec_lcb_never_loosens_the_taker_gate_beyond_q_lcb():
    # q_exec_lcb above q_lcb must NOT loosen: a cost 0.73 above q_lcb 0.72 stays blocked
    # even though it sits below the (ignored-for-loosening) q_exec_lcb 0.99.
    r = select_rest_then_cross_mode(
        q_lcb=0.72,
        q_exec_lcb=0.99,
        taker_all_in_cost=0.73,
        p_fill_taker=1.0,
        best_bid=0.68,
        best_ask=0.74,
        tick_size=0.01,
        reservation=0.69,
        escalated_after_rest=True,
    )
    assert r.chosen_mode == "MAKER"
    assert r.policy == POLICY_MAKER_TAKER_FORBIDDEN


def test_q_exec_lcb_bound_travels_on_receipt_for_provenance():
    r = select_rest_then_cross_mode(
        q_lcb=0.72, q_exec_lcb=0.55, taker_all_in_cost=0.70, **_BOOK
    )
    # The effective bound used must be recorded so the settlement loop can audit it.
    assert getattr(r, "q_exec_lcb", None) == 0.55
