# Created: 2026-06-08
# Last reused or audited: 2026-06-19
# Authority basis: "bin selection.md" §5 submit_candidate pseudocode (recompute not
#   validate) + §7 re-decision / reversal state machine + §9 Hidden #7/#14/#17 +
#   §13 no-trade gates (snapshot stale / recapture fails / rank reversed without
#   rerank) + §14.9/§14.10 + §12.E re-decision relationship tests + operator
#   directive 2026-06-08 (S6: route the submit recapture through RedecisionEngine.
#   evaluate_submit_recapture; only may_submit==True builds the intent; abort
#   branches map to no-submit receipt reasons PRICE_MOVED/EDGE_REVERSED/
#   FAMILY_REVERSED; single fail-closed state machine, no flag, no shadow).
"""S6 RELATIONSHIP TESTS — the live submit-recapture seam is ONE fail-closed gate.

These are RELATIONSHIP tests (not function tests): each asserts a cross-module
property that must hold when a recaptured executable curve / a recomputed stake /
a fresh-curve family re-rank flows from the live decision body
(`_evaluate_submit_recapture_for_selected`) into the RedecisionEngine submit
state machine. The invariant under test is "given what changed at the recapture
boundary, may the candidate submit, and which first-class abort state does it
land in?" — written before the implementation.

The seam under test is `_evaluate_submit_recapture_for_selected`, the ONE place
the live decision body recomputes-not-validates at the no-submit receipt
boundary: it materializes the selected leg's FRESH ExecutableCostCurve, recomputes
the chosen fractional-Kelly stake+price (the S5 kernel) and the family rank, then
routes ALL of it through a single RedecisionEngine.evaluate_submit_recapture. Only
may_submit==True proceeds to build the intent.

Driven through the REAL proof -> candidate -> curve -> score -> recapture path so
the tests exercise the true boundary, not a mock of it.

Mapped to spec §12.E + the S6 money-path invariants:
  E.1 test_price_jump_aborts_submit                    -> SUBMIT_ABORTED_PRICE_MOVED
  E.2 test_edge_reversal_aborts_submit                 -> SUBMIT_ABORTED_EDGE_REVERSED
  E.4 test_stale_recapture_fails_closed                -> abort, never submit from cache
  E.5 test_fallback_cannot_auto_submit_without_full_rerank -> SUBMIT_ABORTED_FAMILY_REVERSED
  + the money-path iron-law relationship invariants (direction law, robust q_lcb
    sizing, typed boundary, single path, abort-state taxonomy derived from the
    state machine).
"""
from __future__ import annotations

import inspect
import json as _json
from dataclasses import replace as dataclass_replace
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

from src.contracts.execution_price import ExecutionPrice
from src.engine import event_reactor_adapter as era
from src.strategy.redecision import (
    CandidateLifecycleState,
    ReversalReason,
    SUBMIT_ABORT_STATES,
)
from src.types.market import Bin


# ---------------------------------------------------------------------------
# Builders: a real snapshot row + a real _CandidateProof through the live path
# (reused shape from the S5 chosen-stake test so the boundary is the true one).
# ---------------------------------------------------------------------------
def _snapshot_row(
    *,
    yes_asks,
    no_asks=(("0.55", "100000"),),
    min_order="5",
    fee_rate_fraction=0.0,
    condition_id="cond-1",
    yes_token_id="yes-1",
    no_token_id="no-1",
    snapshot_id="snap",
):
    depth = {
        "YES": {
            "asks": [{"price": p, "size": s} for p, s in yes_asks],
            "bids": [{"price": "0.30", "size": "100"}],
        },
        "NO": {
            "asks": [{"price": p, "size": s} for p, s in no_asks],
            "bids": [{"price": "0.40", "size": "100"}],
        },
    }
    return {
        "snapshot_id": snapshot_id,
        "condition_id": condition_id,
        "yes_token_id": yes_token_id,
        "no_token_id": no_token_id,
        "selected_outcome_token_id": "",
        "outcome_label": "",
        "min_tick_size": "0.01",
        "min_order_size": min_order,
        "fee_details_json": _json.dumps({"fee_rate_fraction": fee_rate_fraction}),
        "neg_risk": 0,
        "orderbook_depth_json": _json.dumps(depth),
        "tradeability_status_json": "{}",
        "book_hash": f"bh-{snapshot_id}",
    }


def _proof_from_row(
    *,
    direction,
    row,
    token_id,
    q_posterior,
    q_lcb_5pct,
    bin_obj,
    city="paris",
    target_date="2026-06-10",
):
    from src.events.candidate_binding import MarketTopologyCandidate

    ep, _pf, _c = era._execution_price_from_snapshot(
        row, selected_token_id=token_id, direction=direction
    )
    return era._CandidateProof(
        candidate=MarketTopologyCandidate(
            city=city,
            target_date=target_date,
            metric="tmax",
            condition_id=str(row.get("condition_id") or ""),
            yes_token_id=str(row.get("yes_token_id") or ""),
            no_token_id=str(row.get("no_token_id") or ""),
            bin=bin_obj,
        ),
        token_id=token_id,
        direction=direction,
        row=row,
        executable_snapshot_id=str(row.get("snapshot_id") or ""),
        execution_price=ep,
        q_posterior=q_posterior,
        q_lcb_5pct=q_lcb_5pct,
        c_cost_95pct=None,
        p_fill_lcb=1.0,
        trade_score=1.0,
        p_value=0.01,
        passed_prefilter=True,
        native_quote_available=True,
        p_cal_vector_hash="ch",
        p_live_vector_hash="lh",
        missing_reason=None,
    )


def _unpriced_proof(
    *,
    direction,
    row,
    token_id,
    q_posterior,
    q_lcb_5pct,
    bin_obj,
    city="paris",
    target_date="2026-06-10",
):
    """A proof whose fresh side has NO executable ask at recapture (the quote went
    missing): execution_price=None, native_quote_available=False — exactly what the
    real proof path produces when `_execution_price_from_snapshot` raises on an empty
    native ask ladder (the caller routes the ValueError to EXECUTABLE_NATIVE_ASK_
    MISSING). Materializing it yields a no-trade candidate (no fresh recapture curve).
    """
    from src.events.candidate_binding import MarketTopologyCandidate

    return era._CandidateProof(
        candidate=MarketTopologyCandidate(
            city=city,
            target_date=target_date,
            metric="tmax",
            condition_id=str(row.get("condition_id") or ""),
            yes_token_id=str(row.get("yes_token_id") or ""),
            no_token_id=str(row.get("no_token_id") or ""),
            bin=bin_obj,
        ),
        token_id=token_id,
        direction=direction,
        row=row,
        executable_snapshot_id=str(row.get("snapshot_id") or ""),
        execution_price=None,
        q_posterior=q_posterior,
        q_lcb_5pct=q_lcb_5pct,
        c_cost_95pct=None,
        p_fill_lcb=0.0,
        trade_score=1.0,
        p_value=0.01,
        passed_prefilter=True,
        native_quote_available=False,
        p_cal_vector_hash="ch",
        p_live_vector_hash="lh",
        missing_reason="EXECUTABLE_NATIVE_ASK_MISSING:native NO ask ladder empty",
    )


_BIN_X = Bin(low=60.0, high=61.0, unit="F", label="60-61F")
_BIN_Y = Bin(low=61.0, high=62.0, unit="F", label="61-62F")


def _recapture(selected_proof, all_proofs, *, bankroll=10000.0, kelly_mult=1.0,
               exposure=None, forecast_current=True, family_key="fam"):
    return era._evaluate_submit_recapture_for_selected(
        family_key=family_key,
        selected_proof=selected_proof,
        all_proofs=tuple(all_proofs),
        extra_exposure_by_bin_id=exposure or {},
        bankroll_usd=bankroll,
        kelly_multiplier=kelly_mult,
        forecast_still_current=forecast_current,
    )


# ===========================================================================
# BASELINE — a healthy fresh curve + held forecast may submit, and the chosen-
# stake price is the typed depth-walked boundary on the candidate's OWN curve.
# ===========================================================================
def test_healthy_recapture_may_submit_with_typed_chosen_stake_price():
    """A deep cheap book + q_lcb well above cost -> may_submit, READY_TO_SUBMIT,
    and the returned price is a fee-deducted probability-units ExecutionPrice that
    passes assert_kelly_safe (TYPED EXECUTION PRICE AT THE BOUNDARY invariant).
    """
    row = _snapshot_row(yes_asks=(("0.40", "1000000"),))
    proof = _proof_from_row(direction="buy_yes", row=row, token_id="yes-1",
                            q_posterior=0.70, q_lcb_5pct=0.65, bin_obj=_BIN_X)

    decision, stake, price = _recapture(proof, (proof,))

    assert decision.may_submit is True
    assert decision.state is CandidateLifecycleState.READY_TO_SUBMIT
    assert decision.reversal_reason is None
    assert stake > 0.0
    assert isinstance(price, ExecutionPrice)
    price.assert_kelly_safe()
    assert price.fee_deducted is True
    assert price.currency == "probability_units"
    # The boundary equals the SELECTED candidate's OWN fresh curve at the SAME stake
    # (size and price share one curve; no drift — money-path invariant).
    cand = era._native_side_candidate_from_proof(family_key="fam", proof=proof)
    expected = cand.executable_cost_curve.avg_cost(Decimal(str(stake)))
    assert abs(price.value - expected.value) < 1e-12


# ===========================================================================
# §12.E.1 — price jump aborts the submit (recaptured avg_cost > max_acceptable).
# ===========================================================================
def test_price_jump_aborts_submit():
    """Recaptured all-in cost at the chosen stake exceeds the decision-time
    admitted price (max_acceptable_price) -> SUBMIT_ABORTED_PRICE_MOVED, no intent.

    The decision-time admitted price is the proof's own S1 execution_price. We make
    the fresh chosen-stake cost cross it by forcing the stake to walk into a much
    worse deep level: a thin cheap top (5 shares @ 0.40, the min-order admit price)
    then a 0.95 deep level. A sizable chosen stake walks into 0.95, so the
    depth-walked avg cost exceeds the 0.40 the candidate was admitted at.
    """
    # min_order=5 so the admit price is the cheap top (0.40); the rest of the book is
    # punishingly expensive (0.95) so any real stake prices far above 0.40.
    row = _snapshot_row(
        yes_asks=(("0.40", "5"), ("0.95", "1000000")), min_order="5",
    )
    proof = _proof_from_row(direction="buy_yes", row=row, token_id="yes-1",
                            q_posterior=0.99, q_lcb_5pct=0.985, bin_obj=_BIN_X)

    decision, stake, price = _recapture(proof, (proof,))

    assert not decision.may_submit
    assert decision.state is CandidateLifecycleState.SUBMIT_ABORTED_PRICE_MOVED
    assert decision.reversal_reason is ReversalReason.PRICE
    assert stake == 0.0
    assert price is None


# ===========================================================================
# §12.E.2 — edge reversal aborts the submit (edge_lcb = q_lcb - cost <= 0).
# ===========================================================================
def test_edge_reversal_aborts_submit():
    """A q_lcb at/below the all-in cost -> negative robust edge -> no positive-ΔU
    stake -> SUBMIT_ABORTED_EDGE_REVERSED, no intent (§7 edge row; §5 'utility<=0:
    Abort'). The price gate alone would not catch this — edge reverses via q falling
    while price holds in band.
    """
    # Cheap-NO-overconfidence shape: a NO leg priced at 0.55 ask but with an HONEST
    # robust NO q_lcb (1 - q_ucb_yes) of only 0.30 -> edge_lcb = 0.30 - 0.55 < 0.
    row = _snapshot_row(
        yes_asks=(("0.40", "100000"),), no_asks=(("0.55", "100000"),),
    )
    proof = _proof_from_row(direction="buy_no", row=row, token_id="no-1",
                            q_posterior=0.45, q_lcb_5pct=0.30, bin_obj=_BIN_X)
    proof = dataclass_replace(proof, c_cost_95pct=0.56)

    decision, stake, price = _recapture(proof, (proof,))

    assert not decision.may_submit
    assert decision.state is CandidateLifecycleState.SUBMIT_ABORTED_EDGE_REVERSED
    assert decision.reversal_reason is ReversalReason.EDGE
    assert stake == 0.0
    assert price is None


def test_edge_reversed_abort_receipt_recomputes_nonpositive_economics():
    """Abort receipts must not persist the stale pre-recapture positive score.

    The live regret row is consumed by redecision/audit screens after the submit
    gate has re-run economics on the fresh curve. When that gate says
    EDGE_REVERSED, the queryable receipt score has to be the recaptured robust
    score, not the admission-time selected-proof score.
    """
    row = _snapshot_row(
        yes_asks=(("0.40", "100000"),), no_asks=(("0.55", "100000"),),
    )
    proof = _proof_from_row(direction="buy_no", row=row, token_id="no-1",
                            q_posterior=0.45, q_lcb_5pct=0.30, bin_obj=_BIN_X)
    proof = dataclass_replace(proof, c_cost_95pct=0.56)
    assert proof.trade_score > 0.0

    recaptured_score = era._robust_trade_score_from_generated_inputs(
        q_posterior=proof.q_posterior,
        q_lcb_5pct=proof.q_lcb_5pct,
        execution_price=proof.execution_price,
        c_cost_95pct=proof.c_cost_95pct,
        p_fill_lcb=proof.p_fill_lcb,
    )
    assert recaptured_score < 0.0

    source = inspect.getsource(era._build_event_bound_no_submit_receipt_core)
    assert (
        "_recapture.state is CandidateLifecycleState.SUBMIT_ABORTED_EDGE_REVERSED"
        in source
    )
    assert "trade_score=_abort_trade_score" in source
    assert "min(0.0, float(_abort_trade_score))" in source


def test_forecast_stale_aborts_submit_as_edge_reversed():
    """forecast_still_current=False -> the recapture aborts EDGE_REVERSED even on a
    healthy book/price: utility computed on a no-longer-current distribution is not
    trustworthy (§7 forecast row; §13 'forecast not live-eligible'). Fail closed."""
    row = _snapshot_row(yes_asks=(("0.40", "1000000"),))
    proof = _proof_from_row(direction="buy_yes", row=row, token_id="yes-1",
                            q_posterior=0.70, q_lcb_5pct=0.65, bin_obj=_BIN_X)

    decision, stake, price = _recapture(proof, (proof,), forecast_current=False)

    assert not decision.may_submit
    assert decision.state is CandidateLifecycleState.SUBMIT_ABORTED_EDGE_REVERSED
    assert decision.reversal_reason is ReversalReason.EDGE
    assert stake == 0.0
    assert price is None


# ===========================================================================
# §12.E.4 / §13 — a stale / failed recapture (no fresh curve) fails closed,
# never submits from cache.
# ===========================================================================
def test_stale_recapture_fails_closed():
    """A selected proof whose fresh materialization yields NO executable curve (the
    native quote went missing at recapture) must fail closed — never submit from the
    decision-time snapshot. The engine's missing-recapture branch maps to
    SUBMIT_ABORTED_PRICE_MOVED (no executable price could be re-established, §13).
    """
    # A NO direction whose NO ask book is EMPTY at recapture -> the proof is unpriced
    # (execution_price=None, native_quote_available=False) -> the materialized
    # candidate has no executable NO curve -> no fresh recapture curve.
    row = _snapshot_row(
        yes_asks=(("0.40", "100000"),), no_asks=(),  # NO book empty -> no NO ask
    )
    proof = _unpriced_proof(direction="buy_no", row=row, token_id="no-1",
                            q_posterior=0.70, q_lcb_5pct=0.65, bin_obj=_BIN_X)

    # Sanity: the proof itself has no native NO executable price at recapture.
    cand = era._native_side_candidate_from_proof(family_key="fam", proof=proof)
    assert not cand.is_tradeable

    decision, stake, price = _recapture(proof, (proof,))

    assert not decision.may_submit
    assert decision.state is CandidateLifecycleState.SUBMIT_ABORTED_PRICE_MOVED
    assert decision.reversal_reason is ReversalReason.SUBMIT
    assert stake == 0.0
    assert price is None
    assert "fail closed" in decision.detail.lower()


# ===========================================================================
# §12.E.5 / Hidden #7 — a family-rank reversal aborts the submit (FAMILY_REVERSED);
# a fallback cannot auto-submit without a full re-rank promoting it to primary.
# ===========================================================================
def test_fallback_cannot_auto_submit_without_full_rerank():
    """When, on the FRESH curves, a sibling out-ranks the selected proof by ΔU, the
    family rank has reversed: the inline submit must ABORT (SUBMIT_ABORTED_FAMILY_
    REVERSED) and defer to a full re-rank — the selected proof's submit authority is
    revoked the moment it is no longer the primary (Hidden #7; §5 AbortOrSwitchOnly
    AfterFullRerank). The engine never switches inline.

    Setup: two YES bins. The 'selected' proof (bin X) is now priced at a much worse
    0.80 ask while the sibling (bin Y) has a strong cheap 0.40 ask and a higher
    q_lcb, so bin Y is the fresh ΔU primary, not bin X.
    """
    row_x = _snapshot_row(
        yes_asks=(("0.80", "100000"),), condition_id="cond-x",
        yes_token_id="yes-x", no_token_id="no-x", snapshot_id="snap-x",
    )
    row_y = _snapshot_row(
        yes_asks=(("0.40", "100000"),), condition_id="cond-y",
        yes_token_id="yes-y", no_token_id="no-y", snapshot_id="snap-y",
    )
    # bin X: expensive ask, modest q_lcb -> weak ΔU. bin Y: cheap ask, strong q_lcb.
    selected = _proof_from_row(direction="buy_yes", row=row_x, token_id="yes-x",
                               q_posterior=0.82, q_lcb_5pct=0.81, bin_obj=_BIN_X)
    sibling = _proof_from_row(direction="buy_yes", row=row_y, token_id="yes-y",
                              q_posterior=0.70, q_lcb_5pct=0.66, bin_obj=_BIN_Y)
    all_proofs = (selected, sibling)

    # Cross-check the relationship premise: the fresh ΔU primary is the sibling, so
    # the selected proof's rank reversed.
    assert era._family_rank_reversed_at_recapture(
        family_key="fam", selected_proof=selected, all_proofs=all_proofs,
    ) is True

    decision, stake, price = _recapture(selected, all_proofs)

    assert not decision.may_submit
    assert decision.state is CandidateLifecycleState.SUBMIT_ABORTED_FAMILY_REVERSED
    assert decision.reversal_reason is ReversalReason.FAMILY_RANK
    assert stake == 0.0
    assert price is None

    # And the genuine primary (the sibling) is NOT auto-submitted by this gate: the
    # selected-proof abort produces no intent for anyone. The sibling may submit ONLY
    # when IT is the selected proof of its own recapture (a full re-rank made it
    # primary) — proving a fallback never inherits submit authority.
    sib_decision, sib_stake, sib_price = _recapture(sibling, all_proofs)
    assert sib_decision.may_submit is True
    assert sib_stake > 0.0
    assert isinstance(sib_price, ExecutionPrice)


# ===========================================================================
# MONEY-PATH INVARIANT — SUBMIT RECOMPUTES NOT VALIDATES + FAIL-CLOSED: every abort
# branch yields a no-submit decision (may_submit False) in a first-class abort state,
# and zero stake/price; only the clean recapture yields a price.
# ===========================================================================
def test_every_abort_branch_is_a_first_class_no_submit_state():
    """The abort taxonomy is enumerable and fail-closed: each abort lands in a state
    that is a member of SUBMIT_ABORT_STATES with may_submit False and no priced
    stake. This is the structural invariant that there is ONE abort taxonomy derived
    from the state machine, not scattered ad-hoc rejection strings.
    """
    cases = []

    # price moved
    row_p = _snapshot_row(yes_asks=(("0.40", "5"), ("0.95", "1000000")), min_order="5")
    cases.append(_proof_from_row(direction="buy_yes", row=row_p, token_id="yes-1",
                                 q_posterior=0.99, q_lcb_5pct=0.985, bin_obj=_BIN_X))
    # edge reversed
    row_e = _snapshot_row(yes_asks=(("0.40", "100000"),), no_asks=(("0.55", "100000"),))
    cases.append(_proof_from_row(direction="buy_no", row=row_e, token_id="no-1",
                                 q_posterior=0.45, q_lcb_5pct=0.30, bin_obj=_BIN_X))
    # stale recapture (no NO ask -> unpriced proof, no fresh curve)
    row_s = _snapshot_row(yes_asks=(("0.40", "100000"),), no_asks=())
    cases.append(_unpriced_proof(direction="buy_no", row=row_s, token_id="no-1",
                                 q_posterior=0.70, q_lcb_5pct=0.65, bin_obj=_BIN_X))

    for proof in cases:
        decision, stake, price = _recapture(proof, (proof,))
        assert not decision.may_submit
        assert decision.state in SUBMIT_ABORT_STATES
        assert decision.reversal_reason is not None
        assert stake == 0.0
        assert price is None


# ===========================================================================
# MONEY-PATH INVARIANT — ROBUST-LOWER-BOUND SIZING: the stake that clears the gate
# derives from q_lcb, never q_point. Two proofs with EQUAL q_lcb but different
# q_posterior recapture to the SAME stake; a lower q_lcb sizes strictly smaller.
# ===========================================================================
def test_recapture_stake_derives_from_q_lcb_not_q_point():
    """Hold the curve fixed; vary q_point with q_lcb fixed -> identical chosen stake
    (size is on q_lcb, not q_point). Then lower q_lcb -> strictly smaller stake.

    Sized with a SMALL fractional-Kelly multiplier (0.02) so both stakes sit BELOW
    the single-position concentration ceiling (max_single_position_pct·B = $500 at
    B=$10k). The q_lcb monotonicity signal lives in the UNCLAMPED region: were the
    edges sized at full Kelly both would clamp to the ceiling and the strict-smaller
    signal would be masked (the ceiling bounds magnitude, not the q_lcb ordering —
    the same conditionality the K2/K5/K7 portfolio-Kelly tests document).
    """
    row = _snapshot_row(yes_asks=(("0.40", "1000000"),))

    # Same q_lcb (0.70), different q_point (0.72 vs 0.90): identical recapture stake.
    p_lo_point = _proof_from_row(direction="buy_yes", row=row, token_id="yes-1",
                                 q_posterior=0.72, q_lcb_5pct=0.70, bin_obj=_BIN_X)
    p_hi_point = _proof_from_row(direction="buy_yes", row=row, token_id="yes-1",
                                 q_posterior=0.90, q_lcb_5pct=0.70, bin_obj=_BIN_X)
    _, stake_lo_point, _ = _recapture(p_lo_point, (p_lo_point,), kelly_mult=0.02)
    _, stake_hi_point, _ = _recapture(p_hi_point, (p_hi_point,), kelly_mult=0.02)
    assert stake_lo_point > 0.0 and stake_hi_point > 0.0
    # Both below the ceiling (so q_point-invariance is tested in the unclamped region).
    assert stake_lo_point < 10000.0 * 0.05
    assert abs(stake_lo_point - stake_hi_point) < 1e-9, (
        "equal q_lcb must recapture to equal stake regardless of q_point — sizing is "
        "on the robust lower bound, not the point estimate (money-path iron law)"
    )

    # Lower q_lcb -> strictly smaller recapture stake (monotone in the robust bound).
    p_low_lcb = _proof_from_row(direction="buy_yes", row=row, token_id="yes-1",
                                q_posterior=0.72, q_lcb_5pct=0.60, bin_obj=_BIN_X)
    _, stake_low_lcb, _ = _recapture(p_low_lcb, (p_low_lcb,), kelly_mult=0.02)
    assert 0.0 < stake_low_lcb < stake_lo_point


# ===========================================================================
# MONEY-PATH INVARIANT — DIRECTION LAW + NATIVE EXECUTABLE SEPARATION at the gate:
# a buy_no recapture prices from the NO ask book (its own side), never 1 - p(YES);
# the cleared candidate's side agrees with the proof direction.
# ===========================================================================
def test_buy_no_recapture_prices_from_native_no_book():
    """A buy_no that clears the gate is priced from its OWN NO ask book (DIRECTION
    LAW + NATIVE EXECUTABLE SEPARATION): the chosen-stake price traces to the NO
    asks, and the materialized candidate's side is 'NO'. A YES ask of 0.40 with a
    NO ask of 0.30 must price the NO leg at ~0.30 (its own book), NOT 1 - 0.40 = 0.60.
    """
    row = _snapshot_row(
        yes_asks=(("0.40", "100000"),), no_asks=(("0.30", "1000000"),),
    )
    # Honest robust NO q_lcb (1 - q_ucb_yes) of 0.65, well above the 0.30 NO ask.
    proof = _proof_from_row(direction="buy_no", row=row, token_id="no-1",
                            q_posterior=0.70, q_lcb_5pct=0.65, bin_obj=_BIN_X)

    decision, stake, price = _recapture(proof, (proof,))
    assert decision.may_submit is True
    assert stake > 0.0
    assert isinstance(price, ExecutionPrice)

    cand = era._native_side_candidate_from_proof(family_key="fam", proof=proof)
    assert cand.side == "NO"
    assert cand.executable_cost_curve.side == "NO"
    # Priced from the 0.30 NO book, not the YES-complement 0.60.
    assert price.value < 0.45, (
        "buy_no recapture must price from the NO ask book (~0.30), never 1 - p(YES) "
        "(=0.60) — native executable separation"
    )


# ===========================================================================
# SINGLE PATH — the recapture gate is the engine's evaluate_submit_recapture; the
# receipt-reason taxonomy is DERIVED from the abort state, never set independently.
# ===========================================================================
def test_abort_receipt_reason_is_derived_from_lifecycle_state():
    """The decision body's abort receipt reason is a 1:1 derivation of the engine's
    terminal lifecycle state (the _SUBMIT_ABORT_RECEIPT_REASON map covers exactly the
    three abort states) — one taxonomy, no independent reason strings.
    """
    assert set(era._SUBMIT_ABORT_RECEIPT_REASON) == set(SUBMIT_ABORT_STATES)
    assert era._SUBMIT_ABORT_RECEIPT_REASON[
        CandidateLifecycleState.SUBMIT_ABORTED_PRICE_MOVED
    ] == "SUBMIT_ABORTED_PRICE_MOVED"
    assert era._SUBMIT_ABORT_RECEIPT_REASON[
        CandidateLifecycleState.SUBMIT_ABORTED_EDGE_REVERSED
    ] == "SUBMIT_ABORTED_EDGE_REVERSED"
    assert era._SUBMIT_ABORT_RECEIPT_REASON[
        CandidateLifecycleState.SUBMIT_ABORTED_FAMILY_REVERSED
    ] == "SUBMIT_ABORTED_FAMILY_REVERSED"


# ===========================================================================
# RELATIONSHIP ANTIBODY (2026-06-09): the submit-recapture family-rank check MUST
# re-rank the SAME scoped candidate set that SELECTION ranked over.
#
# Root cause (wf edge-reversal-diagnosis 2026-06-09, CONFIRMED high-confidence):
# selection ranks ΔU over `_selection_scoped_proofs(proofs, locked_opportunity_conn)`
# (limit-tradeable AND unlocked-with-price-improvement only), but
# `_family_rank_reversed_at_recapture` re-ranked the RAW full `all_proofs` with no
# conn and no scope filter. A leg correctly SCOPED OUT of selection (locked-no-
# improvement here; also below-min-tick in prod) still materializes is_tradeable and
# can be the ΔU argmax over the full set, so recapture declared a 'different fresh
# primary' and aborted the genuinely-best SELECTED leg as a FALSE
# SUBMIT_ABORTED_FAMILY_REVERSED at top precedence — the cause of proof_accepted>0
# but 0 submits for days. Fitz boundary bug: selection's scope semantics lost when
# its output set crosses into recapture. Fix: recapture re-ranks
# `_selection_scoped_proofs(all_proofs, locked_opportunity_conn)` — STRICTER, never
# looser (a scoped-out leg is untradeable/locked and must never be primary).
# ===========================================================================
def test_recapture_family_rank_honors_selection_scoping_no_false_reversal(monkeypatch):
    import sqlite3

    # Leg A: selected, tradeable, modest edge. Leg B: tradeable with HIGHER ΔU (cheaper)
    # but LOCKED with no price improvement -> SELECTION scopes it OUT. Recapture must
    # re-rank the SAME scoped set, so A stays primary -> NOT a family reversal.
    row_a = _snapshot_row(yes_asks=(("0.50", "1000000"),), condition_id="cond-A",
                          yes_token_id="yes-A", no_token_id="no-A", snapshot_id="snapA")
    row_b = _snapshot_row(yes_asks=(("0.20", "1000000"),), condition_id="cond-B",
                          yes_token_id="yes-B", no_token_id="no-B", snapshot_id="snapB")
    a = _proof_from_row(direction="buy_yes", row=row_a, token_id="yes-A",
                        q_posterior=0.62, q_lcb_5pct=0.58, bin_obj=_BIN_X)
    b = _proof_from_row(direction="buy_yes", row=row_b, token_id="yes-B",
                        q_posterior=0.62, q_lcb_5pct=0.58, bin_obj=_BIN_Y)
    # B is LOCKED with no price improvement (one of selection's scope-out conditions).
    monkeypatch.setattr(
        era, "_locked_candidate_no_price_improvement_reason",
        lambda conn, proof: ("LOCKED_NO_IMPROVEMENT"
                             if str(getattr(proof, "token_id", "")) == "yes-B" else None),
    )
    conn = sqlite3.connect(":memory:")
    # selection's scoped set excludes the locked B (the set the rank must be computed over):
    scoped = era._selection_scoped_proofs(proofs=(a, b), locked_opportunity_conn=conn)
    assert {p.token_id for p in scoped} == {"yes-A"}
    # recapture must honor that scoping: the locked higher-ΔU leg is invisible, A stays primary.
    assert era._family_rank_reversed_at_recapture(
        family_key="fam", selected_proof=a, all_proofs=(a, b), locked_opportunity_conn=conn,
    ) is False


def test_recapture_family_rank_still_detects_a_real_tradeable_sibling_reversal():
    # Regression guard: the scope fix is STRICTER, not broken. A genuinely tradeable,
    # UNLOCKED sibling that out-ranks the selected leg on the fresh curves STILL yields a
    # real family reversal — true reversals must be preserved.
    row_a = _snapshot_row(yes_asks=(("0.50", "1000000"),), condition_id="cond-A",
                          yes_token_id="yes-A", no_token_id="no-A", snapshot_id="snapA")
    row_b = _snapshot_row(yes_asks=(("0.20", "1000000"),), condition_id="cond-B2",
                          yes_token_id="yes-B2", no_token_id="no-B2", snapshot_id="snapB2")
    a = _proof_from_row(direction="buy_yes", row=row_a, token_id="yes-A",
                        q_posterior=0.62, q_lcb_5pct=0.58, bin_obj=_BIN_X)
    b = _proof_from_row(direction="buy_yes", row=row_b, token_id="yes-B2",
                        q_posterior=0.62, q_lcb_5pct=0.58, bin_obj=_BIN_Y)
    # No lock, default conn -> both legs in scope; B out-ranks A on ΔU -> TRUE reversal.
    assert era._family_rank_reversed_at_recapture(
        family_key="fam", selected_proof=a, all_proofs=(a, b),
    ) is True


def test_qkernel_selected_proof_is_not_overruled_by_legacy_family_ranker():
    """qkernel selection authority cannot be invalidated by the legacy rank surface.

    Live regression: qkernel picked the primary proof, but submit recapture re-ran the
    legacy robust-marginal-utility selector and returned SUBMIT_ABORTED_FAMILY_REVERSED
    when the old surface preferred a sibling. That is two selection authorities fighting
    in the execution layer. A qkernel proof may only be re-ranked by a qkernel-compatible
    rerank, not by the inert legacy selector.
    """

    row_a = _snapshot_row(yes_asks=(("0.50", "1000000"),), condition_id="cond-A",
                          yes_token_id="yes-A", no_token_id="no-A", snapshot_id="snapA")
    row_b = _snapshot_row(yes_asks=(("0.20", "1000000"),), condition_id="cond-B2",
                          yes_token_id="yes-B2", no_token_id="no-B2", snapshot_id="snapB2")
    selected = _proof_from_row(direction="buy_yes", row=row_a, token_id="yes-A",
                               q_posterior=0.62, q_lcb_5pct=0.58, bin_obj=_BIN_X)
    sibling = _proof_from_row(direction="buy_yes", row=row_b, token_id="yes-B2",
                              q_posterior=0.62, q_lcb_5pct=0.58, bin_obj=_BIN_Y)
    qkernel_selected = dataclass_replace(
        selected,
        selection_authority_applied="qkernel_spine",
        qkernel_execution_economics={
            "source": "qkernel_spine",
            "candidate_id": "DIRECT_YES:cond-A@proof",
            "route_id": "DIRECT_YES:cond-A@proof",
            "side": "YES",
            "payoff_q_point": 0.62,
            "payoff_q_lcb": 0.58,
            "edge_lcb": 0.08,
            "delta_u_at_min": 0.01,
            "optimal_stake_usd": "5",
            "optimal_delta_u": 0.02,
            "cost": 0.50,
            "false_edge_rate": 0.02,
        },
    )

    # The legacy ranker would prefer the sibling. That must not revoke a qkernel
    # proof's submit authority via SUBMIT_ABORTED_FAMILY_REVERSED.
    assert era._family_rank_reversed_at_recapture(
        family_key="fam", selected_proof=selected, all_proofs=(selected, sibling),
    ) is True
    assert era._family_rank_reversed_at_recapture(
        family_key="fam", selected_proof=qkernel_selected, all_proofs=(qkernel_selected, sibling),
    ) is False


def test_edli_selection_honors_strategy_policy_gate_without_blocking_center_buy(monkeypatch):
    import sqlite3

    from src.riskguard import policy as risk_policy

    monkeypatch.setattr(risk_policy, "is_entries_paused", lambda: False)
    monkeypatch.setattr(risk_policy, "get_edge_threshold_multiplier", lambda: 1.0)

    decision_time = datetime(2026, 7, 1, tzinfo=timezone.utc)
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE risk_actions (
            action_id TEXT,
            strategy_key TEXT,
            action_type TEXT,
            value TEXT,
            issued_at TEXT,
            effective_until TEXT,
            precedence INTEGER,
            status TEXT
        )
        """
    )
    conn.execute(
        """
        INSERT INTO risk_actions (
            action_id, strategy_key, action_type, value, issued_at, effective_until,
            precedence, status
        ) VALUES (
            'riskguard:gate:opening_inertia', 'opening_inertia', 'gate', 'true',
            '2026-06-30T00:00:00+00:00', NULL, 1, 'active'
        )
        """
    )

    no_row = _snapshot_row(
        yes_asks=(("0.45", "1000000"),),
        no_asks=(("0.45", "1000000"),),
        condition_id="cond-no",
        yes_token_id="yes-no",
        no_token_id="no-no",
        snapshot_id="snap-no",
    )
    no_proof = _proof_from_row(
        direction="buy_no",
        row=no_row,
        token_id="no-no",
        q_posterior=0.70,
        q_lcb_5pct=0.65,
        bin_obj=_BIN_X,
    )
    yes_row = _snapshot_row(
        yes_asks=(("0.45", "1000000"),),
        condition_id="cond-yes",
        yes_token_id="yes-yes",
        no_token_id="no-yes",
        snapshot_id="snap-yes",
    )
    yes_proof = _proof_from_row(
        direction="buy_yes",
        row=yes_row,
        token_id="yes-yes",
        q_posterior=0.70,
        q_lcb_5pct=0.65,
        bin_obj=_BIN_Y,
    )

    scoped = era._selection_scoped_proofs(
        proofs=(no_proof, yes_proof),
        strategy_policy_conn=conn,
        strategy_policy_event_type="FORECAST_SNAPSHOT_READY",
        decision_time=decision_time,
        enforce_win_rate_floor=False,
    )

    assert scoped == (yes_proof,)

    book = era._opportunity_book_from_proofs(
        event_id="evt",
        family_id="fam",
        proofs=(no_proof,),
        selected_proof=None,
        strategy_policy_conn=conn,
        strategy_policy_event_type="FORECAST_SNAPSHOT_READY",
        decision_time=decision_time,
    )
    reason = book.evaluations[0].missing_reason
    assert reason is not None
    assert reason.startswith("STRATEGY_POLICY_GATED:opening_inertia:")
    family_reason = era._family_all_candidates_rejected_reason(book)
    assert family_reason is not None
    assert "strategy_policy=1" in family_reason


def test_edli_selection_does_not_treat_global_pause_as_strategy_rejection(monkeypatch):
    import sqlite3

    from src.riskguard import policy as risk_policy

    monkeypatch.setattr(risk_policy, "is_entries_paused", lambda: True)
    monkeypatch.setattr(risk_policy, "get_edge_threshold_multiplier", lambda: 1.0)

    decision_time = datetime(2026, 7, 1, tzinfo=timezone.utc)
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row

    no_row = _snapshot_row(
        yes_asks=(("0.45", "1000000"),),
        no_asks=(("0.45", "1000000"),),
        condition_id="cond-no",
        yes_token_id="yes-no",
        no_token_id="no-no",
        snapshot_id="snap-no",
    )
    no_proof = _proof_from_row(
        direction="buy_no",
        row=no_row,
        token_id="no-no",
        q_posterior=0.70,
        q_lcb_5pct=0.65,
        bin_obj=_BIN_X,
    )
    yes_row = _snapshot_row(
        yes_asks=(("0.45", "1000000"),),
        condition_id="cond-yes",
        yes_token_id="yes-yes",
        no_token_id="no-yes",
        snapshot_id="snap-yes",
    )
    yes_proof = _proof_from_row(
        direction="buy_yes",
        row=yes_row,
        token_id="yes-yes",
        q_posterior=0.70,
        q_lcb_5pct=0.65,
        bin_obj=_BIN_Y,
    )

    scoped = era._selection_scoped_proofs(
        proofs=(no_proof, yes_proof),
        strategy_policy_conn=conn,
        strategy_policy_event_type="FORECAST_SNAPSHOT_READY",
        decision_time=decision_time,
        enforce_win_rate_floor=False,
    )

    assert scoped == (no_proof, yes_proof)

    book = era._opportunity_book_from_proofs(
        event_id="evt",
        family_id="fam",
        proofs=(no_proof,),
        selected_proof=None,
        strategy_policy_conn=conn,
        strategy_policy_event_type="FORECAST_SNAPSHOT_READY",
        decision_time=decision_time,
    )
    assert book.evaluations[0].missing_reason is None


def test_qkernel_selection_skips_candidate_that_cannot_clear_final_submit_floor(monkeypatch):
    import sqlite3

    from src.riskguard import policy as risk_policy

    monkeypatch.setattr(risk_policy, "is_entries_paused", lambda: False)
    monkeypatch.setattr(risk_policy, "get_edge_threshold_multiplier", lambda: 1.0)

    decision_time = datetime(2026, 7, 1, tzinfo=timezone.utc)
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row

    low_floor_row = _snapshot_row(
        yes_asks=(("0.005", "1000000"),),
        min_order="5",
        condition_id="cond-low",
        yes_token_id="yes-low",
        no_token_id="no-low",
        snapshot_id="snap-low",
    )
    low_floor_proof = _proof_from_row(
        direction="buy_yes",
        row=low_floor_row,
        token_id="yes-low",
        q_posterior=0.12,
        q_lcb_5pct=0.08,
        bin_obj=_BIN_X,
    )
    live_floor_row = _snapshot_row(
        yes_asks=(("0.20", "1000000"),),
        condition_id="cond-live",
        yes_token_id="yes-live",
        no_token_id="no-live",
        snapshot_id="snap-live",
    )
    live_floor_proof = _proof_from_row(
        direction="buy_yes",
        row=live_floor_row,
        token_id="yes-live",
        q_posterior=0.70,
        q_lcb_5pct=0.50,
        bin_obj=_BIN_Y,
    )

    def _cert(proof, *, cost: float, q_lcb: float, stake: float) -> dict:
        bin_id = era._candidate_bin_id(proof)
        return {
            "source": "qkernel_spine",
            "candidate_id": f"YES:{bin_id}:DIRECT_YES:{bin_id}@proof",
            "bin_id": bin_id,
            "route_id": f"DIRECT_YES:{bin_id}@proof",
            "side": "YES",
            "payoff_q_point": float(proof.q_posterior),
            "payoff_q_lcb": q_lcb,
            "edge_lcb": q_lcb - cost,
            "delta_u_at_min": 0.01,
            "optimal_stake_usd": stake,
            "optimal_delta_u": 0.02,
            "cost": cost,
            "false_edge_rate": 0.01,
            "direction_law_ok": True,
            "coherence_allows": True,
            "selection_guard_basis": "SELECTION_BETA_95",
            "selection_guard_abstained": False,
            "selection_guard_q_safe": q_lcb,
        }

    proofs = era._proofs_with_qkernel_candidate_economics(
        proofs=(low_floor_proof, live_floor_proof),
        qkernel_economics_by_bin_side={
            (era._candidate_bin_id(low_floor_proof), "YES"): _cert(
                low_floor_proof,
                cost=0.005,
                q_lcb=0.08,
                stake=10.0,
            ),
            (era._candidate_bin_id(live_floor_proof), "YES"): _cert(
                live_floor_proof,
                cost=0.20,
                q_lcb=0.50,
                stake=10.0,
            ),
        },
        strategy_policy_event_type="FORECAST_SNAPSHOT_READY",
    )

    assert proofs[0].missing_reason is not None
    assert proofs[0].missing_reason.startswith(
        "QKERNEL_FINAL_SUBMIT_FLOOR:limit_price_below_strategy_entry_floor"
    )
    scoped = era._selection_scoped_proofs(
        proofs=proofs,
        strategy_policy_conn=conn,
        strategy_policy_event_type="FORECAST_SNAPSHOT_READY",
        decision_time=decision_time,
        enforce_win_rate_floor=False,
    )

    assert tuple(era._candidate_bin_id(proof) for proof in scoped) == (
        era._candidate_bin_id(live_floor_proof),
    )


def test_qkernel_selection_default_scopes_out_low_win_rate_tail_yes():
    low_tail_row = _snapshot_row(
        yes_asks=(("0.041", "1000000"),),
        condition_id="cond-low-tail",
        yes_token_id="yes-low-tail",
        no_token_id="no-low-tail",
        snapshot_id="snap-low-tail",
    )
    low_tail_proof = _proof_from_row(
        direction="buy_yes",
        row=low_tail_row,
        token_id="yes-low-tail",
        q_posterior=0.24833093804728934,
        q_lcb_5pct=0.0990451308919892,
        bin_obj=_BIN_X,
    )
    high_confidence_row = _snapshot_row(
        yes_asks=(("0.27", "1000000"),),
        condition_id="cond-high-yes",
        yes_token_id="yes-high",
        no_token_id="no-high",
        snapshot_id="snap-high",
    )
    high_confidence_proof = _proof_from_row(
        direction="buy_yes",
        row=high_confidence_row,
        token_id="yes-high",
        q_posterior=0.80,
        q_lcb_5pct=0.65,
        bin_obj=_BIN_Y,
    )

    scoped = era._selection_scoped_proofs(
        proofs=(low_tail_proof, high_confidence_proof),
        strategy_policy_event_type="FORECAST_SNAPSHOT_READY",
        decision_time=datetime(2026, 7, 1, tzinfo=timezone.utc),
    )

    assert scoped == (high_confidence_proof,)


def test_selection_scopes_out_open_position_token_but_keeps_tradeable_sibling():
    import sqlite3

    row_a = _snapshot_row(yes_asks=(("0.50", "1000000"),), condition_id="cond-A",
                          yes_token_id="yes-A", no_token_id="no-A", snapshot_id="snapA")
    row_b = _snapshot_row(yes_asks=(("0.20", "1000000"),), condition_id="cond-B",
                          yes_token_id="yes-B", no_token_id="no-B", snapshot_id="snapB")
    a = _proof_from_row(direction="buy_yes", row=row_a, token_id="yes-A",
                        q_posterior=0.62, q_lcb_5pct=0.58, bin_obj=_BIN_X)
    b = _proof_from_row(direction="buy_yes", row=row_b, token_id="yes-B",
                        q_posterior=0.62, q_lcb_5pct=0.58, bin_obj=_BIN_Y)
    conn = sqlite3.connect(":memory:")
    conn.execute(
        """
        CREATE TABLE position_current (
            position_id TEXT,
            phase TEXT,
            token_id TEXT,
            no_token_id TEXT,
            shares REAL,
            cost_basis_usd REAL
        )
        """
    )
    conn.execute(
        """
        INSERT INTO position_current (
            position_id, phase, token_id, no_token_id, shares, cost_basis_usd
        ) VALUES ('pos-held', 'active', 'yes-B', '', 10.0, 2.0)
        """
    )

    scoped = era._selection_scoped_proofs(proofs=(a, b), held_position_conn=conn)
    assert {p.token_id for p in scoped} == {"yes-A"}
    selected = era._selected_candidate_proof(
        {"family_id": "fam", "event_id": "evt"},
        (a, b),
        held_position_conn=conn,
    )
    assert selected is a

    book = era._opportunity_book_from_proofs(
        event_id="evt",
        family_id="fam",
        proofs=(a, b),
        selected_proof=selected,
        held_position_conn=conn,
    )
    held_eval = next(ev for ev in book.evaluations if ev.token_id == "yes-B")
    assert str(held_eval.missing_reason).startswith("OPEN_POSITION_SAME_TOKEN_MONITOR_OWNED:")


def test_selection_scopes_out_open_position_family_even_when_token_differs():
    import sqlite3

    row_a = _snapshot_row(yes_asks=(("0.50", "1000000"),), condition_id="cond-A",
                          yes_token_id="yes-A", no_token_id="no-A", snapshot_id="snapA")
    row_b = _snapshot_row(yes_asks=(("0.20", "1000000"),), condition_id="cond-B",
                          yes_token_id="yes-B", no_token_id="no-B", snapshot_id="snapB")
    a = _proof_from_row(direction="buy_yes", row=row_a, token_id="yes-A",
                        q_posterior=0.62, q_lcb_5pct=0.58, bin_obj=_BIN_X)
    b = _proof_from_row(direction="buy_yes", row=row_b, token_id="yes-B",
                        q_posterior=0.62, q_lcb_5pct=0.58, bin_obj=_BIN_Y)
    conn = sqlite3.connect(":memory:")
    conn.execute(
        """
        CREATE TABLE position_current (
            position_id TEXT,
            phase TEXT,
            city TEXT,
            target_date TEXT,
            temperature_metric TEXT,
            condition_id TEXT,
            bin_label TEXT,
            token_id TEXT,
            no_token_id TEXT,
            shares REAL,
            cost_basis_usd REAL
        )
        """
    )
    conn.execute(
        """
        INSERT INTO position_current (
            position_id, phase, city, target_date, temperature_metric,
            condition_id, bin_label, token_id, no_token_id, shares, cost_basis_usd
        ) VALUES (
            'pos-held-family', 'active', 'paris', '2026-06-10', 'high',
            'cond-held', '29C', 'yes-held', '', 7.0, 5.53
        )
        """
    )

    scoped = era._selection_scoped_proofs(proofs=(a, b), held_position_conn=conn)
    assert scoped == ()
    selected = era._selected_candidate_proof(
        {"family_id": "fam", "event_id": "evt"},
        (a, b),
        held_position_conn=conn,
    )
    assert selected is None

    book = era._opportunity_book_from_proofs(
        event_id="evt",
        family_id="fam",
        proofs=(a, b),
        selected_proof=selected,
        held_position_conn=conn,
    )
    reasons = {str(ev.missing_reason) for ev in book.evaluations}
    assert len(reasons) == 1
    reason = next(iter(reasons))
    assert reason.startswith("OPEN_POSITION_SAME_FAMILY_MONITOR_OWNED:")
    assert "position_id=pos-held-family" in reason


def test_redecision_scope_can_rank_same_family_without_allowing_same_token_duplicate():
    import sqlite3

    row_a = _snapshot_row(yes_asks=(("0.50", "1000000"),), condition_id="cond-A",
                          yes_token_id="yes-A", no_token_id="no-A", snapshot_id="snapA")
    row_b = _snapshot_row(yes_asks=(("0.20", "1000000"),), condition_id="cond-B",
                          yes_token_id="yes-B", no_token_id="no-B", snapshot_id="snapB")
    a = _proof_from_row(direction="buy_yes", row=row_a, token_id="yes-A",
                        q_posterior=0.62, q_lcb_5pct=0.58, bin_obj=_BIN_X)
    b = _proof_from_row(direction="buy_yes", row=row_b, token_id="yes-B",
                        q_posterior=0.62, q_lcb_5pct=0.58, bin_obj=_BIN_Y)
    conn = sqlite3.connect(":memory:")
    conn.execute(
        """
        CREATE TABLE position_current (
            position_id TEXT,
            phase TEXT,
            city TEXT,
            target_date TEXT,
            temperature_metric TEXT,
            condition_id TEXT,
            bin_label TEXT,
            token_id TEXT,
            no_token_id TEXT,
            shares REAL,
            cost_basis_usd REAL
        )
        """
    )
    conn.execute(
        """
        INSERT INTO position_current (
            position_id, phase, city, target_date, temperature_metric,
            condition_id, bin_label, token_id, no_token_id, shares, cost_basis_usd
        ) VALUES (
            'pos-held-family', 'active', 'paris', '2026-06-10', 'high',
            'cond-held', 'held-bin', 'held-token', '', 7.0, 5.53
        )
        """
    )

    scoped = era._selection_scoped_proofs(
        proofs=(a, b),
        held_position_conn=conn,
        allow_same_family_monitor_owned=True,
    )
    assert scoped == (a, b)
    selected = era._selected_candidate_proof(
        {"family_id": "fam", "event_id": "evt"},
        (a, b),
        held_position_conn=conn,
        allow_same_family_monitor_owned=True,
    )
    assert selected is b

    book = era._opportunity_book_from_proofs(
        event_id="evt",
        family_id="fam",
        proofs=(a, b),
        selected_proof=selected,
        held_position_conn=conn,
        allow_same_family_monitor_owned=True,
    )
    assert all(ev.missing_reason is None for ev in book.evaluations)
    assert {ev.support_index for ev in book.evaluations} == {0, 1}
    assert all(ev.bin_id for ev in book.evaluations)
    assert era._family_rank_reversed_at_recapture(
        family_key="fam",
        selected_proof=b,
        all_proofs=(a, b),
        held_position_conn=conn,
        allow_same_family_monitor_owned=True,
    ) is False


def test_same_family_monitor_owned_scope_is_management_lane_only():
    assert era._event_allows_same_family_monitor_owned("FORECAST_SNAPSHOT_READY") is False
    assert era._event_allows_same_family_monitor_owned("EDLI_REDECISION_PENDING") is True
    assert era._event_allows_same_family_monitor_owned("DAY0_EXTREME_UPDATED") is True
    assert (
        era._selection_allows_same_family_monitor_owned(
            event_allows_same_family_monitor_owned=False,
            selection_exposure_by_outcome={},
        )
        is False
    )
    assert (
        era._selection_allows_same_family_monitor_owned(
            event_allows_same_family_monitor_owned=True,
            selection_exposure_by_outcome={},
        )
        is True
    )
    assert (
        era._selection_allows_same_family_monitor_owned(
            event_allows_same_family_monitor_owned=False,
            selection_exposure_by_outcome={"bin-A": 5.53},
        )
        is True
    )

    import sqlite3

    row_a = _snapshot_row(yes_asks=(("0.50", "1000000"),), condition_id="cond-A",
                          yes_token_id="yes-A", no_token_id="no-A", snapshot_id="snapA")
    row_b = _snapshot_row(yes_asks=(("0.20", "1000000"),), condition_id="cond-B",
                          yes_token_id="yes-B", no_token_id="no-B", snapshot_id="snapB")
    a = _proof_from_row(direction="buy_yes", row=row_a, token_id="yes-A",
                        q_posterior=0.62, q_lcb_5pct=0.58, bin_obj=_BIN_X)
    b = _proof_from_row(direction="buy_yes", row=row_b, token_id="yes-B",
                        q_posterior=0.62, q_lcb_5pct=0.58, bin_obj=_BIN_Y)
    conn = sqlite3.connect(":memory:")
    conn.execute(
        """
        CREATE TABLE position_current (
            position_id TEXT,
            phase TEXT,
            city TEXT,
            target_date TEXT,
            temperature_metric TEXT,
            condition_id TEXT,
            bin_label TEXT,
            token_id TEXT,
            no_token_id TEXT,
            shares REAL,
            cost_basis_usd REAL
        )
        """
    )
    conn.execute(
        """
        INSERT INTO position_current (
            position_id, phase, city, target_date, temperature_metric,
            condition_id, bin_label, token_id, no_token_id, shares, cost_basis_usd
        ) VALUES (
            'pos-held-family', 'active', 'paris', '2026-06-10', 'high',
            'cond-held', 'held-bin', 'held-token', '', 7.0, 5.53
        )
        """
    )

    assert era._selection_scoped_proofs(
        proofs=(a,),
        held_position_conn=conn,
        allow_same_family_monitor_owned=era._event_allows_same_family_monitor_owned(
            "FORECAST_SNAPSHOT_READY"
        ),
    ) == ()
    assert era._selection_scoped_proofs(
        proofs=(a,),
        held_position_conn=conn,
        allow_same_family_monitor_owned=era._event_allows_same_family_monitor_owned(
            "DAY0_EXTREME_UPDATED"
        ),
    ) == (a,)

    conn.execute(
        """
        INSERT INTO position_current (
            position_id, phase, city, target_date, temperature_metric,
            condition_id, bin_label, token_id, no_token_id, shares, cost_basis_usd
        ) VALUES (
            'pos-held-token', 'active', 'paris', '2026-06-10', 'high',
            'cond-B', '61-62F', 'yes-B', '', 3.0, 0.60
        )
        """
    )
    scoped_after_same_token = era._selection_scoped_proofs(
        proofs=(a, b),
        held_position_conn=conn,
        allow_same_family_monitor_owned=True,
    )
    # D1 FILL-UP (2026-06-22 lifecycle consult REQ-20260622-060011): a held SAME-TOKEN
    # proof now SURVIVES redecision selection scoping (previously it was hard-dropped,
    # leaving only `a`). This is the deliberate admission widening that lets a fill-up
    # candidate be selected. The double-submit/over-exposure safety that the old hard
    # drop provided is now enforced DOWNSTREAM by decide_fill_up (residual sizing:
    # delta = target - current_live - pending, never a second full entry) + the
    # family-rebalance lease (one active rebalance per family) — NOT by excluding the
    # proof from selection. Both same-family (`a`) and same-token (`b`) are selectable.
    assert scoped_after_same_token == (a, b)


def test_forecast_with_proven_family_exposure_ranks_management_scope():
    """A fresh forecast trigger stays fresh-entry unless current position truth proves
    this family is already held; then selection must expose the family-management
    candidates so D1/D2 can decide fill-up, hold, shift, or no-trade."""

    import sqlite3

    row_a = _snapshot_row(
        yes_asks=(("0.50", "1000000"),),
        condition_id="cond-A",
        yes_token_id="yes-A",
        no_token_id="no-A",
        snapshot_id="snapA",
    )
    row_b = _snapshot_row(
        yes_asks=(("0.20", "1000000"),),
        condition_id="cond-B",
        yes_token_id="yes-B",
        no_token_id="no-B",
        snapshot_id="snapB",
    )
    a = _proof_from_row(
        direction="buy_yes",
        row=row_a,
        token_id="yes-A",
        q_posterior=0.62,
        q_lcb_5pct=0.58,
        bin_obj=_BIN_X,
    )
    b = _proof_from_row(
        direction="buy_yes",
        row=row_b,
        token_id="yes-B",
        q_posterior=0.62,
        q_lcb_5pct=0.58,
        bin_obj=_BIN_Y,
    )
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE position_current (
            position_id TEXT,
            phase TEXT,
            city TEXT,
            target_date TEXT,
            temperature_metric TEXT,
            condition_id TEXT,
            bin_label TEXT,
            token_id TEXT,
            no_token_id TEXT,
            shares REAL,
            cost_basis_usd REAL
        )
        """
    )
    conn.execute(
        """
        INSERT INTO position_current (
            position_id, phase, city, target_date, temperature_metric,
            condition_id, bin_label, token_id, no_token_id, shares, cost_basis_usd
        ) VALUES (
            'pos-held-family', 'active', 'paris', '2026-06-10', 'high',
            'cond-A', '60-61F', 'yes-A', '', 7.0, 5.53
        )
        """
    )

    direct_event_allow = era._event_allows_same_family_monitor_owned(
        "FORECAST_SNAPSHOT_READY"
    )
    assert direct_event_allow is False
    assert (
        era._selection_scoped_proofs(
            proofs=(a, b),
            held_position_conn=conn,
            allow_same_family_monitor_owned=direct_event_allow,
        )
        == ()
    )

    exposure = era._family_existing_exposure_for_selection_by_bin_id(
        proofs=(a, b),
        portfolio_state_provider=None,
        held_position_conn=conn,
        family=SimpleNamespace(city="paris", target_date="2026-06-10", metric="high"),
    )
    effective_scope = era._selection_allows_same_family_monitor_owned(
        event_allows_same_family_monitor_owned=direct_event_allow,
        selection_exposure_by_outcome=exposure,
    )

    assert exposure
    assert effective_scope is True
    assert era._selection_scoped_proofs(
        proofs=(a, b),
        held_position_conn=conn,
        allow_same_family_monitor_owned=effective_scope,
    ) == (a, b)


def test_all_open_position_tokens_no_trade_with_honest_monitor_owned_reason():
    import sqlite3

    row_b = _snapshot_row(yes_asks=(("0.20", "1000000"),), condition_id="cond-B",
                          yes_token_id="yes-B", no_token_id="no-B", snapshot_id="snapB")
    b = _proof_from_row(direction="buy_yes", row=row_b, token_id="yes-B",
                        q_posterior=0.62, q_lcb_5pct=0.58, bin_obj=_BIN_Y)
    conn = sqlite3.connect(":memory:")
    conn.execute(
        """
        CREATE TABLE position_current (
            position_id TEXT,
            phase TEXT,
            token_id TEXT,
            no_token_id TEXT,
            shares REAL,
            cost_basis_usd REAL
        )
        """
    )
    conn.execute(
        """
        INSERT INTO position_current (
            position_id, phase, token_id, no_token_id, shares, cost_basis_usd
        ) VALUES ('pos-held', 'day0_window', 'yes-B', '', 10.0, 2.0)
        """
    )

    selected = era._selected_candidate_proof(
        {"family_id": "fam", "event_id": "evt"},
        (b,),
        held_position_conn=conn,
    )
    assert selected is None
    book = era._opportunity_book_from_proofs(
        event_id="evt",
        family_id="fam",
        proofs=(b,),
        selected_proof=selected,
        held_position_conn=conn,
    )
    reason = era._family_all_candidates_rejected_reason(book)
    assert reason is not None
    assert "held_position_monitor_owned=1" in reason
    assert "OPEN_POSITION_SAME_TOKEN_MONITOR_OWNED:" in reason
