# Created: 2026-06-04
# Last reused or audited: 2026-06-04
# Authority basis: Task #102 (BEST-ORDER SELECTION) CRITIC REVISE (aab33d99);
#   docs/operations/BEST_ORDER_SELECTION_ROOT_2026-06-01.md.
"""Antibody for the book-wide edge-zone admission gate (Task #102).

This is a RELATIONSHIP test (Fitz methodology): it pins the property that holds
when a *set* of candidate proofs flows into the admission boundary, not a single
function's output. The core invariant it makes structurally unconstructable:

    a positive-after-cost-EV candidate is ADMITTED and a negative-after-cost-EV
    candidate is REJECTED, regardless of which one arrived first.

Because the gate (``edge_zone_admits``) is a pure function of the candidate's
OWN (q_lcb, cost), arrival order cannot change a verdict — the test proves this
by evaluating the same two candidates in both orders and asserting identical,
quality-correct admission outcomes. The reactor's first-qualifying-in-arrival-
order defect (ROOT A) cannot fire a worse order ahead of a better one once this
gate is the LAST money-path step.

Adversarial coverage:
  * the gate uses q_lcb (conservative), so an overconfident POINT q cannot game
    it (a wide-CI bin with high point q but low q_lcb is rejected);
  * OFF is byte-identical (the blocker falls through exactly as legacy);
  * the gate only ever TIGHTENS (it never admits a proof the legacy chain
    rejected).
"""

from __future__ import annotations

import pytest

from src.contracts.edge_zone_admission import EdgeZoneVerdict, edge_zone_admits
from src.events.reactor import EventSubmissionReceipt, ReactorConfig, _receipt_money_path_blocker


# ---------------------------------------------------------------------------
# Pure-predicate properties (the contract the antibody rests on)
# ---------------------------------------------------------------------------


def _verdict(q_lcb, cost, floor=0.0) -> EdgeZoneVerdict:
    return edge_zone_admits(q_lcb=q_lcb, cost=cost, min_ev_per_dollar=floor)


def test_positive_after_cost_ev_admitted():
    # Mid-range market-uncertain bin: q_lcb 0.70 at cost 0.55 -> EV/$ = +0.27.
    v = _verdict(0.70, 0.55)
    assert v.admits is True
    assert v.ev_per_dollar == pytest.approx((0.70 - 0.55) / 0.55)


def test_negative_after_cost_ev_rejected():
    # Confident-favorite tail: q_lcb 0.90 at cost 0.93 -> EV/$ negative -> demoted.
    v = _verdict(0.90, 0.93)
    assert v.admits is False
    assert v.ev_per_dollar < 0.0
    assert v.reason == "EDGE_ZONE_EV_PER_DOLLAR_BELOW_FLOOR"


def test_order_independence_is_structural():
    """THE antibody: evaluating in either order yields identical, quality-correct
    verdicts — a negative-EV candidate can never be admitted ahead of a
    positive-EV one because the verdict ignores order entirely."""
    good = dict(q_lcb=0.70, cost=0.55)   # +EV/$ (mid-range)
    bad = dict(q_lcb=0.90, cost=0.93)    # -EV/$ (confident tail)

    # Order A: good first, bad second.
    va1, vb1 = _verdict(**good), _verdict(**bad)
    # Order B: bad first, good second.
    vb2, va2 = _verdict(**bad), _verdict(**good)

    # The good one is admitted and the bad one rejected in BOTH orders.
    assert va1.admits is True and va2.admits is True
    assert vb1.admits is False and vb2.admits is False
    # And the computed EV/$ is byte-identical regardless of position.
    assert va1.ev_per_dollar == va2.ev_per_dollar
    assert vb1.ev_per_dollar == vb2.ev_per_dollar


def test_overconfident_point_q_cannot_game_the_gate():
    """Wide-CI bin: high POINT q (would look great) but low q_lcb. The gate sees
    only q_lcb, so it is rejected — overconfidence cannot buy admission."""
    # point q ~0.99 (not passed in), but the conservative lower bound is 0.50
    # against a 0.60 cost -> EV/$ negative on the honest side.
    v = _verdict(0.50, 0.60)
    assert v.admits is False
    assert v.ev_per_dollar < 0.0


def test_fail_closed_on_missing_or_nonpositive_cost():
    assert _verdict(0.80, None).admits is False
    assert _verdict(None, 0.55).admits is False
    assert _verdict(0.80, 0.0).admits is False
    assert _verdict(0.80, -0.1).admits is False


def test_floor_tightens_admission():
    # At cost 0.55, q_lcb 0.60 gives EV/$ = +0.0909. A floor of 0.10 rejects it;
    # a floor of 0.0 admits it. Monotone: a higher floor only ever tightens.
    assert _verdict(0.60, 0.55, floor=0.0).admits is True
    assert _verdict(0.60, 0.55, floor=0.10).admits is False


# ---------------------------------------------------------------------------
# Boundary: the gate as the LAST money-path step in _receipt_money_path_blocker
# ---------------------------------------------------------------------------


def _admissible_receipt(*, q_lcb: float, cost: float) -> EventSubmissionReceipt:
    """A receipt that clears EVERY legacy money-path gate (trade_score / FDR /
    Kelly / final_intent), so the ONLY thing that can block it is the new
    edge-zone step. This proves the gate is a tightening applied LAST."""
    return EventSubmissionReceipt(
        submitted=False,
        event_id="evt-1",
        side_effect_status="NO_SUBMIT",
        q_lcb_5pct=q_lcb,
        c_fee_adjusted=cost,
        trade_score_positive=True,
        fdr_pass=True,
        fdr_family_id="fam-1",
        fdr_hypothesis_count=22,
        kelly_pass=True,
        kelly_execution_price_type="ExecutionPrice",
        kelly_price_fee_deducted=True,
        kelly_size_usd=44.0,
        kelly_cost_basis_id="cb-1",
        final_intent_id="fi-1",
    )


def test_blocker_off_is_byte_identical_legacy():
    """OFF (no config / flag False): a negative-EV-per-dollar but otherwise
    fully-admissible proof passes the blocker exactly as it does on legacy HEAD."""
    receipt = _admissible_receipt(q_lcb=0.90, cost=0.93)  # negative EV/$
    # No config -> legacy chain, admitted.
    assert _receipt_money_path_blocker(receipt) == (None, "")
    # Explicit flag OFF -> identical.
    off = ReactorConfig(edge_zone_admission_enabled=False)
    assert _receipt_money_path_blocker(receipt, off) == (None, "")


def test_blocker_on_rejects_negative_ev_tail():
    receipt = _admissible_receipt(q_lcb=0.90, cost=0.93)  # confident tail, -EV/$
    on = ReactorConfig(edge_zone_admission_enabled=True)
    stage, reason = _receipt_money_path_blocker(receipt, on)
    assert stage == "TRADE_SCORE"
    assert reason == "EDGE_ZONE_EV_PER_DOLLAR_BELOW_FLOOR"


def test_blocker_on_admits_positive_ev_midrange():
    receipt = _admissible_receipt(q_lcb=0.70, cost=0.55)  # mid-range, +EV/$
    on = ReactorConfig(edge_zone_admission_enabled=True)
    assert _receipt_money_path_blocker(receipt, on) == (None, "")


def test_blocker_on_is_a_pure_tightening():
    """ON can only ever ADD a rejection; it never admits a proof the legacy
    chain already rejected. Verify with a proof that fails an EARLIER gate
    (FDR): the result is the legacy FDR rejection, unchanged by the edge gate."""
    receipt = _admissible_receipt(q_lcb=0.70, cost=0.55)
    receipt = EventSubmissionReceipt(  # same, but FDR fails
        **{**receipt.__dict__, "fdr_pass": False},
    )
    on = ReactorConfig(edge_zone_admission_enabled=True)
    stage, _ = _receipt_money_path_blocker(receipt, on)
    assert stage == "FDR"  # earlier gate wins; edge gate never reached.


def test_blocker_on_admission_is_order_independent_at_the_seam():
    """Cross-module: the SAME two admissible receipts, evaluated in either order
    through the real blocker, yield admit(good)/reject(bad) both ways — the
    arrival-order defect (ROOT A) cannot fire the worse order over the better."""
    good = _admissible_receipt(q_lcb=0.70, cost=0.55)   # +EV/$
    bad = _admissible_receipt(q_lcb=0.90, cost=0.93)    # -EV/$
    on = ReactorConfig(edge_zone_admission_enabled=True)

    # good-then-bad
    assert _receipt_money_path_blocker(good, on)[0] is None
    assert _receipt_money_path_blocker(bad, on)[0] == "TRADE_SCORE"
    # bad-then-good
    assert _receipt_money_path_blocker(bad, on)[0] == "TRADE_SCORE"
    assert _receipt_money_path_blocker(good, on)[0] is None
