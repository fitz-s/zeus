# Created: 2026-06-08
# Last reused or audited: 2026-06-08
# Authority basis: "bin selection.md" §14.7 (rank by robust marginal expected log
#   utility — NOT probability, NOT q-c, NOT ROI) + §14.8 (single-primary-live:
#   one primary leg per family) + §13 (no-trade gate: "Robust marginal expected
#   log utility <= 0") + §6 (best-candidate selection algorithm; argmax ΔU) +
#   §7 (the utility ranker is the live ranking surface) + §3 (ΔU objective) +
#   §9 Hidden #5 (OUTSIDE outcome in the family payoff matrix) +
#   operator directive 2026-06-08 (single primary-live decision path; the
#   bin-selection ranker IS the live decision, NOT the legacy scalar-Kelly
#   select_best_family_candidate / max(trade_score, q_lcb) selector; NO flag).
"""S3-fix relationship tests — the live decision IS the bin-selection ranker.

THE SINGLE-PATH INVARIANT (operator directive + spec §14.7/§14.8). The
adversarial verifier found that S3 materialized a ``NativeSideCandidate`` then
THREW IT AWAY: the live decision (``_selected_candidate_proof``) still ran
through the legacy scalar-Kelly ``select_best_family_candidate`` /
``max(executable, key=(trade_score, q_lcb_5pct))`` surface, and the
bin-selection ranker (``src/strategy/utility_ranker.py``) had ZERO call sites in
``src/`` — the §13 robust-marginal-log-utility no-trade gate was NOT enforced on
the live path.

These relationship tests pin the cross-module invariant at the seam where the
priced ``_CandidateProof`` set flows into the LIVE selection decision: the
returned proof MUST be the one whose materialized ``NativeSideCandidate`` is the
robust-marginal-expected-log-utility (ΔU) winner of the family payoff matrix,
and a family with no positive-ΔU candidate MUST return ``None`` (the §13
no-trade gate fired on the LIVE path, not a scalar gate).

Written BEFORE the implementation (relationship-tests -> implementation ->
function-tests). They are the antibody for "two ranking surfaces coexist; the
bin-selection one is observationally inert".
"""
from __future__ import annotations

import json
from decimal import Decimal

import pytest

from src.engine import event_reactor_adapter as era
from src.events.candidate_binding import MarketTopologyCandidate
from src.strategy import utility_ranker
from src.types.market import Bin


# ---------------------------------------------------------------------------
# Snapshot-row fixture (same shape S1/S3 tests use).
# ---------------------------------------------------------------------------
def _row(
    *,
    condition_id="condition-1",
    yes_token="yes-1",
    no_token="no-1",
    yes_asks=(("0.40", "100000"),),
    no_asks=(("0.55", "100000"),),
    yes_bids=(("0.39", "100"),),
    no_bids=(("0.19", "100"),),
    min_tick="0.01",
    min_order="5",
    fee_rate_fraction=0.0,
    snapshot_id="snap-s3",
):
    depth = {
        "YES": {
            "asks": [{"price": p, "size": s} for p, s in yes_asks],
            "bids": [{"price": p, "size": s} for p, s in yes_bids],
        },
        "NO": {
            "asks": [{"price": p, "size": s} for p, s in no_asks],
            "bids": [{"price": p, "size": s} for p, s in no_bids],
        },
    }
    return {
        "snapshot_id": snapshot_id,
        "condition_id": condition_id,
        "yes_token_id": yes_token,
        "no_token_id": no_token,
        "selected_outcome_token_id": "",
        "outcome_label": "",
        "min_tick_size": min_tick,
        "min_order_size": min_order,
        "fee_details_json": json.dumps({"fee_rate_fraction": fee_rate_fraction}),
        "neg_risk": 0,
        "orderbook_depth_json": json.dumps(depth),
        "tradeability_status_json": "{}",
        "book_hash": "book-hash-s3",
    }


def _candidate(*, condition_id, yes_token, no_token, bin_obj):
    return MarketTopologyCandidate(
        city="paris",
        target_date="2026-06-10",
        metric="tmax",
        condition_id=condition_id,
        yes_token_id=yes_token,
        no_token_id=no_token,
        bin=bin_obj,
    )


def _proof(
    *,
    direction,
    row,
    token_id,
    q_posterior,
    q_lcb_5pct,
    bin_obj,
    trade_score=1.0,
):
    ep, _p_fill, _c95 = era._execution_price_from_snapshot(
        row, selected_token_id=token_id, direction=direction
    )
    return era._CandidateProof(
        candidate=_candidate(
            condition_id=str(row.get("condition_id") or ""),
            yes_token=str(row.get("yes_token_id") or ""),
            no_token=str(row.get("no_token_id") or ""),
            bin_obj=bin_obj,
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
        trade_score=trade_score,
        p_value=0.01,
        passed_prefilter=True,
        native_quote_available=True,
        p_cal_vector_hash="cal-hash",
        p_live_vector_hash="live-hash",
        missing_reason=None,
    )


# ===========================================================================
# Invariant 1 — the live decision IS the ΔU winner of the bin-selection ranker.
# ===========================================================================
def test_live_selection_returns_robust_marginal_utility_winner():
    """The proof ``_selected_candidate_proof`` returns is the candidate whose
    materialized NativeSideCandidate maximizes robust marginal expected log
    utility (spec §14.7 / §6), computed by ``utility_ranker.rank_candidates``.

    DISCRIMINATING construction (the ΔU ranker and the LEGACY scalar selector
    must DISAGREE, so a pass proves the ΔU ranker is what runs — not a coincidence):

      * The §14.7 ΔU ranker uses ONLY ``q_lcb`` and the cost curve. It does NOT
        read ``trade_score`` at all.
      * The legacy ``objective_tuple`` LEADS with ``capital_weighted_growth_score``
        = ``expected_robust_dollars · robust_kelly_growth_score``, both of which
        scale with ``trade_score`` (``robust_ev_per_dollar = trade_score/price``).

    Bin A: HIGH ``trade_score`` (10.0) but a worse robust edge — q_lcb 0.50 at
            ask 0.45 (edge 0.05).
    Bin B: LOW ``trade_score`` (0.10) but a better robust edge — q_lcb 0.65 at
            ask 0.45 (edge 0.20).

    The legacy tuple is dominated by bin A's large trade_score -> picks A.
    The ΔU ranker ignores trade_score and sees bin B's larger robust edge ->
    picks B. The live decision MUST pick bin B.
    """
    bin_a = Bin(low=60.0, high=61.0, unit="F", label="60-61F")
    bin_b = Bin(low=61.0, high=62.0, unit="F", label="61-62F")
    row_a = _row(
        condition_id="cond-A", yes_token="yesA", no_token="noA",
        yes_asks=(("0.45", "100000"),), snapshot_id="snap-A",
    )
    row_b = _row(
        condition_id="cond-B", yes_token="yesB", no_token="noB",
        yes_asks=(("0.45", "100000"),), snapshot_id="snap-B",
    )
    proof_a = _proof(
        direction="buy_yes", row=row_a, token_id="yesA",
        q_posterior=0.55, q_lcb_5pct=0.50, bin_obj=bin_a, trade_score=10.0,
    )
    proof_b = _proof(
        direction="buy_yes", row=row_b, token_id="yesB",
        q_posterior=0.70, q_lcb_5pct=0.65, bin_obj=bin_b, trade_score=0.10,
    )

    selected = era._selected_candidate_proof(
        {"family_id": "fam", "event_id": "evt"},
        (proof_a, proof_b),
    )
    assert selected is proof_b, (
        "live selection must pick the higher robust-edge (higher-ΔU) candidate "
        "bin B even though bin A has a 100x larger trade_score — proving the "
        "decision is the §14.7 marginal-log-utility ranker (which ignores "
        "trade_score), not the legacy scalar (capital_weighted_growth_score) tuple"
    )

    # Cross-check: the same winner falls out of utility_ranker directly over the
    # materialized candidates (the live path and the ranker agree by construction).
    cand_a = era._native_side_candidate_from_proof(family_key="fam", proof=proof_a)
    cand_b = era._native_side_candidate_from_proof(family_key="fam", proof=proof_b)
    matrix = utility_ranker.FamilyPayoffMatrix.over_bins(
        [cand_a.bin_id, cand_b.bin_id]
    )
    pi = utility_ranker.robust_probabilities(
        matrix,
        per_bin_q_lcb={cand_a.bin_id: cand_a.q_lcb, cand_b.bin_id: cand_b.q_lcb},
    )
    exposure = utility_ranker.PortfolioExposureVector.flat(
        matrix, baseline=Decimal("1000")
    )
    scored = utility_ranker.rank_candidates([cand_a, cand_b], matrix, pi, exposure)
    assert scored[0].candidate.bin_id == cand_b.bin_id
    assert scored[0].delta_u > 0.0


# ===========================================================================
# Invariant 2 — §13 no-trade gate fires on the LIVE path (ΔU <= 0 -> None).
# ===========================================================================
def test_live_selection_no_trades_when_robust_utility_nonpositive():
    """When every candidate's robust marginal expected log utility is <= 0,
    ``_selected_candidate_proof`` returns ``None`` (spec §13 live no-trade gate).

    The ask (0.80) exceeds the robust q_lcb (0.55), so q_lcb - c < 0 and ΔU is
    negative at every feasible stake -> the §13 gate fires. The legacy selector
    keyed on (trade_score, q_lcb) would still return a candidate (positive
    trade_score), so a None here proves the marginal-log-utility gate — not the
    scalar gate — is what runs live.
    """
    bin_a = Bin(low=60.0, high=61.0, unit="F", label="60-61F")
    row_a = _row(
        condition_id="cond-A", yes_token="yesA", no_token="noA",
        yes_asks=(("0.80", "100000"),), snapshot_id="snap-A",
    )
    proof_a = _proof(
        direction="buy_yes", row=row_a, token_id="yesA",
        q_posterior=0.58, q_lcb_5pct=0.55, bin_obj=bin_a, trade_score=1.0,
    )

    selected = era._selected_candidate_proof(
        {"family_id": "fam", "event_id": "evt"},
        (proof_a,),
    )
    assert selected is None, (
        "ask 0.80 > q_lcb 0.55 => robust marginal log utility <= 0 => the §13 "
        "no-trade gate must fire on the LIVE path (return None)"
    )


# ===========================================================================
# Invariant 3 — a lower-q candidate can WIN when its utility is higher (§6/§14.7).
# ===========================================================================
def test_live_selection_lower_q_higher_utility_wins():
    """A candidate with the LOWER robust q can be the live pick when its
    executable cost makes its marginal log utility higher (spec §6: "Highest q
    may be overpriced"; §14.7 sort key is ΔU, not q).

    Bin A: q_lcb 0.60, ask 0.55 -> edge 0.05.
    Bin B: q_lcb 0.55, ask 0.40 -> edge 0.15 (lower q, much cheaper).
    The scalar (trade_score, q_lcb) tuple would pick bin A (higher q_lcb). The
    ΔU ranker must pick bin B (higher robust marginal log utility).
    """
    bin_a = Bin(low=60.0, high=61.0, unit="F", label="60-61F")
    bin_b = Bin(low=61.0, high=62.0, unit="F", label="61-62F")
    row_a = _row(
        condition_id="cond-A", yes_token="yesA", no_token="noA",
        yes_asks=(("0.55", "100000"),), snapshot_id="snap-A",
    )
    row_b = _row(
        condition_id="cond-B", yes_token="yesB", no_token="noB",
        yes_asks=(("0.40", "100000"),), snapshot_id="snap-B",
    )
    proof_a = _proof(
        direction="buy_yes", row=row_a, token_id="yesA",
        q_posterior=0.70, q_lcb_5pct=0.60, bin_obj=bin_a, trade_score=1.0,
    )
    proof_b = _proof(
        direction="buy_yes", row=row_b, token_id="yesB",
        q_posterior=0.65, q_lcb_5pct=0.55, bin_obj=bin_b, trade_score=1.0,
    )

    selected = era._selected_candidate_proof(
        {"family_id": "fam", "event_id": "evt"},
        (proof_a, proof_b),
    )
    assert selected is proof_b, (
        "lower-q but much cheaper bin B has higher robust marginal log utility; "
        "the ΔU ranker must pick it over the higher-q bin A (spec §6/§14.7)"
    )


# ===========================================================================
# Invariant 4 — the live decision path consults the dead ranker (no longer dead).
# ===========================================================================
def test_utility_ranker_is_imported_by_the_live_adapter():
    """The §7 robust marginal-log-utility ranker is wired into the live adapter.

    The adversarial verifier's core finding was ``grep utility_ranker src/`` ->
    ZERO hits. This pins the antibody: the live adapter module imports and uses
    the ranker, so the bin-selection ranker is the live decision surface, not
    dead code.
    """
    import inspect

    src = inspect.getsource(era._selected_candidate_proof)
    ranker_src = inspect.getsource(era._select_proof_by_robust_marginal_utility)
    # The live decision delegates to the ΔU ranker, which CALLS utility_ranker.
    assert "_select_proof_by_robust_marginal_utility" in src
    assert "utility_ranker.rank_candidates" in ranker_src
    # The legacy scalar-Kelly surfaces must NOT be CALLED on the decision path
    # (a prose mention in a docstring is fine; an actual call is the violation).
    decision_src = src + ranker_src
    assert "select_best_family_candidate(" not in decision_src
    assert "build_family_opportunity_book(" not in decision_src
    # And the off-able runtime gate the directive forbids is gone from the decision.
    assert "_opportunity_book_selector_enabled(" not in decision_src
