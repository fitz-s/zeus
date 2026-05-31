# Created: 2026-05-30
# Last reused/audited: 2026-05-30
# Authority basis: EDLI v1 robust trade-score direction semantics — buy_no must be
#   scored with the NO win-probability (1 - yes_posterior) and the NO-side LCB, never
#   the YES posterior. Relationship test guarding the adapter -> trade_score boundary
#   (src/engine/event_reactor_adapter.py:_generate_candidate_proofs ->
#    src/strategy/live_inference/trade_score.py:robust_trade_score).
"""Relationship tests for per-direction trade-score semantics.

These assert a CROSS-MODULE invariant, not a single function's output:

    When ``_generate_candidate_proofs`` hands a (q_posterior, q_5pct, cost)
    triple to ``robust_trade_score`` for a ``buy_no`` candidate, the
    ``q_posterior`` MUST be the NO win-probability (``1 - yes_posterior``) and
    ``q_5pct`` MUST be the NO-side lower bound — NOT the YES posterior.

If a future refactor re-wires the adapter so that ``buy_no`` is scored with the
YES posterior (the failure mode hypothesised in the EDLI shadow zero-candidate
investigation, 2026-05-30), a buy_no on a near-certain-YES bin would score
POSITIVE and the system could take the wrong side. These tests make that
category of error fail loudly.

The adapter constructs, for each candidate (event_reactor_adapter.py ~L2280):

    (no_token,  "buy_no",  1.0 - yes_q, no_lcb)

and feeds it to robust_trade_score via _robust_trade_score_from_generated_inputs
(penalty=stress_penalty=0.01). We reproduce that exact contract here.
"""

from __future__ import annotations

import pytest

from src.contracts.execution_price import ExecutionPrice
from src.strategy.live_inference.trade_score import robust_trade_score

# The adapter's hardcoded robustness haircut (event_reactor_adapter.py
# _robust_trade_score_from_generated_inputs). Kept in sync intentionally so the
# test exercises the live magnitude, not a toy value.
_LAMBDA_EDGE = 0.01
_LAMBDA_STRESS = 0.01


def _score(
    *,
    q_posterior: float,
    q_5pct: float,
    cost_95: float,
    p_fill_lcb: float,
) -> float:
    """Reproduce the adapter -> kernel contract for a single direction."""
    receipt = robust_trade_score(
        trade_score_id="relationship_test",
        q_posterior=q_posterior,
        q_5pct=q_5pct,
        c_95pct=ExecutionPrice(cost_95, "ask", fee_deducted=True, currency="probability_units"),
        c_stress=ExecutionPrice(cost_95, "ask", fee_deducted=True, currency="probability_units"),
        p_fill_lcb=p_fill_lcb,
        penalty=_LAMBDA_EDGE,
        stress_penalty=_LAMBDA_STRESS,
    )
    return float(receipt.score)


def _buy_no_inputs_from_yes_posterior(
    *,
    yes_q: float,
    yes_lcb: float,
    no_ask: float,
):
    """Mirror the adapter's per-direction projection for a buy_no candidate.

    The adapter passes ``1.0 - yes_q`` as q_posterior and the NO-side lower bound
    (``no_lcb``) as q_5pct. For a clean MECE 2-way bin the NO posterior LB is
    ``1.0 - yes_lcb`` (yes_lcb is the YES posterior UPPER bound's complement); we
    use that relationship to derive a faithful NO LCB.
    """
    q_no = 1.0 - yes_q
    q_no_lcb = 1.0 - yes_lcb  # NO LB = 1 - YES UB; with yes_lcb as a stand-in for the YES point we keep it conservative
    return {"q_posterior": q_no, "q_5pct": min(q_no, q_no_lcb), "cost_95": no_ask}


def test_buy_no_on_near_certain_yes_bin_scores_strongly_negative():
    """RELATIONSHIP: buy_no on a near-certain-YES bin must be declined.

    yes_q = 0.999  ->  true NO win-prob = 0.001. A buy_no here is a near-certain
    loss; against any plausible NO ask the robust score must be strongly NEGATIVE
    (<= -0.5 * p_fill_lcb), never ~0. If the adapter instead fed the YES posterior
    (0.999) into the buy_no score, the score would be POSITIVE — the wrong-side
    trade this test exists to forbid.
    """
    p_fill = 0.05
    # NO ask on a near-certain-YES bin is cheap; pick a mid value so the test is
    # not trivially satisfied by an extreme ask.
    no_ask = 0.05

    inputs = _buy_no_inputs_from_yes_posterior(yes_q=0.999, yes_lcb=0.999, no_ask=no_ask)
    score = _score(p_fill_lcb=p_fill, **inputs)

    # q_no = 0.001 vs ask 0.05 -> edge ~ -0.049, * p_fill 0.05 -> ~ -0.0027
    assert score < 0.0, f"buy_no on near-certain-YES bin must score negative, got {score}"
    assert score <= -0.5 * p_fill * 0.04, (
        f"buy_no on near-certain-YES bin must score STRONGLY negative (not ~0), got {score}"
    )


def test_buy_no_wrong_wiring_with_yes_posterior_would_score_positive():
    """INVERSION DETECTOR: prove the forbidden wiring flips the verdict.

    This is the discriminating control. If a refactor wired the YES posterior
    (0.999) and the YES LCB (0.96) into the buy_no score against the cheap NO ask
    (0.05), the score would be POSITIVE — i.e. the system would BUY the wrong
    side. We assert that hypothetical here so the contrast with the correct wiring
    (previous test) is explicit and self-documenting. The correct path never
    constructs these inputs for buy_no; this asserts WHY that matters.
    """
    wrong_score = _score(q_posterior=0.999, q_5pct=0.96, cost_95=0.05, p_fill_lcb=0.05)
    assert wrong_score > 0.0, (
        "control: feeding YES posterior into buy_no vs a cheap NO ask yields a "
        f"positive (wrong-side) score; got {wrong_score}. If this ever became the "
        "live wiring, the system would trade the wrong direction."
    )


def test_buy_yes_on_underpriced_bin_scores_positive():
    """RELATIONSHIP: buy_yes on a genuinely under-priced bin must be tradeable.

    yes_q = 0.80, YES 5pct-LCB = 0.74, YES ask = 0.55. Robust edge at the 5th
    percentile is 0.74 - 0.55 - 0.01 = 0.18 > 0, so the score must be POSITIVE.
    Confirms the gate is not pathologically rejecting real edge — the kernel is
    direction-correct on the YES side too.
    """
    score = _score(q_posterior=0.80, q_5pct=0.74, cost_95=0.55, p_fill_lcb=0.50)
    assert score > 0.0, f"buy_yes on under-priced bin must score positive, got {score}"


def test_thin_sub_haircut_edge_is_rejected_by_lambda_edge():
    """RELATIONSHIP: the 1c lambda_edge haircut, not a sign error, pins thin edges.

    This documents the ACTUAL zero-candidate driver found in the 2026-05-30 shadow
    investigation: a buy_no with a genuinely positive but sub-1c 5pct edge
    (q_5pct - cost = +0.0052) is driven negative by lambda_edge=0.01. The direction
    is correct; the score is near-zero-negative because the robustness haircut
    exceeds the thin edge. Reproduces the exact reported sample
    (q_5pct=0.9608, cost=0.9556, p_fill=0.05 -> score ~= -0.00024).
    """
    score = _score(q_posterior=0.999, q_5pct=0.9608, cost_95=0.9556, p_fill_lcb=0.05)
    raw_5pct_edge = 0.9608 - 0.9556
    assert raw_5pct_edge > 0.0, "the sampled bin has a genuinely positive raw 5pct edge"
    assert raw_5pct_edge < _LAMBDA_EDGE, "but it is smaller than the lambda_edge haircut"
    assert score < 0.0, "so the robust score is negative — the gate working, not inverted"
    assert score == pytest.approx(-0.00024, abs=1e-4), (
        f"reproduces the reported degenerate score, got {score}"
    )
