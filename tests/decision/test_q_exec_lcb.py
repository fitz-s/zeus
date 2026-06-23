# Created: 2026-06-23
# Last audited: 2026-06-23
# Authority basis: chatgpt-consult round-3 (rid REQ-20260623-040544-df089d) commit-1 spec for the
#   always-on execution-conditioned q_exec_lcb; real-chain money-path audit
#   docs/evidence/live_order_pathology/2026-06-23_end_to_end_moneypath_audit.md (q over-confidence:
#   buy_no realized ~65% vs served q_lcb ~0.83; 5-15% claimed-edge band realized 65.7% vs 72.3%
#   breakeven). Operator law: no flag/shadow/default-off, no abstain-to-halt, no fixed buckets,
#   no N_MIN, no edge floor, no overfit.
"""TDD for the pure execution-conditioned q_exec_lcb estimator.

Contract (consult commit-1):
  q_exec_lcb = min(model_q_lcb, block_lower_bound_5pct(raw_side_prob))
where the block is an isotonic (PAVA, no fixed buckets) nondecreasing calibration of realized
``won`` over ``raw_side_prob`` within the candidate's (actual_exec_class x side) group, with a
one-sided 5% beta lower bound on the pooled block containing raw_side_prob. Parent fallback when
the fine group is empty; NEVER abstains (no evidence anywhere -> serve model_q_lcb unchanged).
MAKER_FILL never borrows TAKER/ALL_EXECUTED evidence (preserves the adverse-fill conditioning).
"""
from __future__ import annotations

import pytest

from src.decision.q_exec_lcb import (
    ExecutionOutcomeFact,
    build_exec_blocks,
    q_exec_lcb,
)


def _fact(side, exec_class, raw, qlcb, won, fill=0.5):
    return ExecutionOutcomeFact(
        decision_time="2026-06-01T00:00:00+00:00",
        settled_at="2026-06-02T00:00:00+00:00",
        side=side,
        actual_exec_class=exec_class,
        raw_side_prob=float(raw),
        model_q_lcb=float(qlcb),
        fill_price=float(fill),
        won=int(won),
    )


class TestNeverAbstains:
    def test_no_evidence_serves_model_q_lcb_unchanged(self):
        # Empty table: no flag, no abstain, no q_safe=0. Serve the model bound as-is.
        blocks = build_exec_blocks([])
        out = q_exec_lcb(
            model_q_lcb=0.83, raw_side_prob=0.86, exec_class="TAKER_CROSS",
            side="buy_no", blocks=blocks,
        )
        assert out == pytest.approx(0.83)

    def test_serve_is_never_above_model_q_lcb(self):
        # q_exec_lcb = min(model_q_lcb, block_lb): the served bound is conservative — it can only
        # DEFLATE the model bound, never inflate it.
        facts = [_fact("buy_no", "TAKER_CROSS", 0.86, 0.83, 1) for _ in range(40)]
        blocks = build_exec_blocks(facts)
        out = q_exec_lcb(
            model_q_lcb=0.50, raw_side_prob=0.86, exec_class="TAKER_CROSS",
            side="buy_no", blocks=blocks,
        )
        assert out <= 0.50 + 1e-9


class TestOverconfidenceDeflation:
    def test_realized_below_served_deflates(self):
        # The real-chain pathology: served q_lcb 0.83 but realized ~0.65. The empirical lower
        # bound must sit BELOW the over-confident model bound, deflating it so the false-edge
        # candidate fails the honest q > price+cost gate.
        facts = (
            [_fact("buy_no", "TAKER_CROSS", 0.86, 0.83, 1) for _ in range(65)]
            + [_fact("buy_no", "TAKER_CROSS", 0.86, 0.83, 0) for _ in range(35)]
        )  # realized 65/100 = 0.65 at raw 0.86
        blocks = build_exec_blocks(facts)
        out = q_exec_lcb(
            model_q_lcb=0.83, raw_side_prob=0.86, exec_class="TAKER_CROSS",
            side="buy_no", blocks=blocks,
        )
        assert out < 0.83, "empirical LCB must deflate the over-confident model bound"
        assert out < 0.65, "a 5% lower bound sits below the realized point estimate 0.65"


class TestMonotone:
    def test_isotonic_nondecreasing_in_raw_prob(self):
        # Higher raw side prob -> calibrated bound is nondecreasing (isotonic), never inverts.
        facts = []
        for raw, wr_n in ((0.55, 11), (0.70, 14), (0.85, 18)):
            wins = wr_n
            for _ in range(20):
                facts.append(_fact("buy_no", "TAKER_CROSS", raw, 0.99, 1 if wins > 0 else 0))
                wins -= 1
        blocks = build_exec_blocks(facts)
        lo = q_exec_lcb(model_q_lcb=0.99, raw_side_prob=0.55, exec_class="TAKER_CROSS", side="buy_no", blocks=blocks)
        mid = q_exec_lcb(model_q_lcb=0.99, raw_side_prob=0.70, exec_class="TAKER_CROSS", side="buy_no", blocks=blocks)
        hi = q_exec_lcb(model_q_lcb=0.99, raw_side_prob=0.85, exec_class="TAKER_CROSS", side="buy_no", blocks=blocks)
        assert lo <= mid + 1e-9 <= hi + 1e-9


class TestParentFallback:
    def test_empty_fine_group_falls_back_to_root_parent(self):
        # No (TAKER_CROSS, buy_yes) rows, but the (ALL_EXECUTED, ALL_SIDES) root has evidence
        # (the 133-row real-chain situation). The fine candidate must borrow the covered parent,
        # not abstain.
        facts = (
            [_fact("buy_no", "TAKER_CROSS", 0.80, 0.90, 1) for _ in range(60)]
            + [_fact("buy_no", "TAKER_CROSS", 0.80, 0.90, 0) for _ in range(40)]
        )  # populates (TAKER_CROSS,buy_no) + ALL_EXECUTED roots; buy_yes is empty
        blocks = build_exec_blocks(facts)
        out = q_exec_lcb(
            model_q_lcb=0.90, raw_side_prob=0.80, exec_class="TAKER_CROSS",
            side="buy_yes", blocks=blocks,
        )
        # Root parent realized 0.60 at raw 0.80 -> deflates below model 0.90.
        assert out < 0.90, "empty fine group must borrow the covered root parent, not abstain"


class TestMakerNeverBorrowsTaker:
    def test_maker_with_no_maker_parent_does_not_borrow_taker(self):
        # Only TAKER_CROSS evidence exists. A MAKER_FILL candidate must NOT use taker/all data to
        # authorize a maker bound (that would erase the adverse-fill conditioning). With no MAKER
        # parent it returns model_q_lcb unchanged (caller then reroutes uncertified maker -> taker).
        facts = (
            [_fact("buy_no", "TAKER_CROSS", 0.86, 0.83, 1) for _ in range(60)]
            + [_fact("buy_no", "TAKER_CROSS", 0.86, 0.83, 0) for _ in range(40)]
        )
        blocks = build_exec_blocks(facts)
        out = q_exec_lcb(
            model_q_lcb=0.83, raw_side_prob=0.86, exec_class="MAKER_FILL",
            side="buy_no", blocks=blocks,
        )
        assert out == pytest.approx(0.83), (
            "MAKER_FILL must not borrow TAKER/ALL evidence; with no maker parent serve model bound "
            "unchanged so the caller reroutes to taker"
        )

    def test_maker_uses_maker_evidence_when_present(self):
        facts = (
            [_fact("buy_no", "MAKER_FILL", 0.86, 0.83, 1) for _ in range(50)]
            + [_fact("buy_no", "MAKER_FILL", 0.86, 0.83, 0) for _ in range(50)]
        )  # maker realized 0.50 at raw 0.86 (adverse) -> strong deflation
        blocks = build_exec_blocks(facts)
        out = q_exec_lcb(
            model_q_lcb=0.83, raw_side_prob=0.86, exec_class="MAKER_FILL",
            side="buy_no", blocks=blocks,
        )
        assert out < 0.83
