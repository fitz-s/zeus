# Created: 2026-06-08
# Last reused or audited: 2026-06-08
# Authority basis: "bin selection.md" §3 (ΔU objective) + §6 (best-candidate
#   selection; why utility beats q / q-c / ROI) + §11 Phase 4 acceptance
#   (lower-q can beat higher-q; dominated candidate rejected; existing exposure
#   shrinks ΔU) + §12.D.1/.2/.3 (family-selection tests) + §14.7 (rank by robust
#   marginal utility — primary key is positive ΔU, NOT q, NOT q-price, NOT ROI) +
#   §9 Hidden #5 (OUTSIDE in the payoff matrix) + Hidden #10 (central NO vs
#   adjacent YES through ONE payoff matrix) + operator directive 2026-06-08
#   (single-primary-live; the marginal-utility ranker is THE live decision; no flag).
"""S4 relationship tests — the live decision is the robust marginal-utility ranker.

THE CROSS-MODULE INVARIANTS (relationship tests, written BEFORE implementation
per the project methodology: relationship-tests -> implementation -> function-
tests). Each pins a property that must hold ACROSS the seam where the priced
``_CandidateProof`` set flows into the single live selection decision
(``_selected_candidate_proof`` -> ``_select_proof_by_robust_marginal_utility``):

  D.2  test_lower_q_higher_log_utility_selected — a lower-q UNDERPRICED candidate
       beats a higher-q OVERPRICED one (ΔU, not q, decides).
  D.1  test_significant_but_dominated_candidate_rejected — an FDR-pass candidate
       with lower ΔU loses to the utility winner.
  D.3  test_central_no_vs_adjacent_yes_through_one_payoff_matrix — NO_i and
       YES_{i+1} are scored on the SAME FamilyPayoffMatrix outcome set including
       OUTSIDE; the correct utility winner is selected (Hidden #5 / #10).
  P4   test_existing_family_exposure_shrinks_marginal_utility — a nonzero
       PortfolioExposureVector reduces ΔU and can flip a candidate to no-trade
       (§11 Phase 4 acceptance, Hidden #10).

Plus the money-path iron-law invariants the directive enumerates (DIRECTION LAW,
ROBUST-LOWER-BOUND SIZING via optimal_stake_usd, OUTSIDE always present, SINGLE
SELECTION PATH, ONE PRIMARY LEG PER FAMILY) and the two gate-removal antibodies
(market-disagreement / modal-bin NO are SUBSUMED by ΔU, not separate gates).
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
# Snapshot-row + proof fixtures (same shape the S1/S3 tests use).
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
    snapshot_id="snap-s4",
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
        "book_hash": "book-hash-s4",
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
# §12.D.2 — a lower-q UNDERPRICED candidate beats a higher-q OVERPRICED one.
# ===========================================================================
def test_lower_q_higher_log_utility_selected():
    """Spec §12.D.2 / §6 ("Highest q may be overpriced"). The live decision is
    the ΔU winner, so the PRIMARY sort key is robust marginal log utility, NOT q.

    Bin A: HIGHER q (q_lcb 0.70) but OVERPRICED — YES ask 0.68, edge 0.02.
    Bin B: LOWER  q (q_lcb 0.55) but UNDERPRICED — YES ask 0.40, edge 0.15.

    A q-sorting selector picks A (higher q_lcb). The ΔU ranker must pick B: its
    much larger robust edge gives strictly higher marginal log utility despite
    the lower probability.
    """
    bin_a = Bin(low=60.0, high=61.0, unit="F", label="60-61F")
    bin_b = Bin(low=61.0, high=62.0, unit="F", label="61-62F")
    row_a = _row(condition_id="cond-A", yes_token="yesA", no_token="noA",
                 yes_asks=(("0.68", "100000"),), snapshot_id="snap-A")
    row_b = _row(condition_id="cond-B", yes_token="yesB", no_token="noB",
                 yes_asks=(("0.40", "100000"),), snapshot_id="snap-B")
    proof_a = _proof(direction="buy_yes", row=row_a, token_id="yesA",
                     q_posterior=0.74, q_lcb_5pct=0.70, bin_obj=bin_a)
    proof_b = _proof(direction="buy_yes", row=row_b, token_id="yesB",
                     q_posterior=0.60, q_lcb_5pct=0.55, bin_obj=bin_b)

    selected = era._selected_candidate_proof(
        {"family_id": "fam", "event_id": "evt"}, (proof_a, proof_b)
    )
    assert selected is proof_b, (
        "the lower-q but underpriced candidate has higher robust marginal log "
        "utility; the ΔU ranker must pick it over the higher-q overpriced one "
        "(spec §12.D.2 / §14.7 — primary key is ΔU, not q)"
    )


# ===========================================================================
# §12.D.1 — an FDR-pass candidate with lower ΔU loses to the utility winner.
# ===========================================================================
def test_significant_but_dominated_candidate_rejected():
    """Spec §12.D.1. Both candidates are statistically significant (FDR-pass:
    passed_prefilter True, small p_value) and both have POSITIVE robust edge, so
    neither is gated out. The ranker must still select the higher-ΔU one and
    reject the DOMINATED (lower-ΔU) sibling — significance is an inference gate,
    not the execution objective (§2: "FDR is an inference gate, not an execution
    objective").

    Bin A (dominated): q_lcb 0.58, ask 0.50 -> edge 0.08.
    Bin B (winner):    q_lcb 0.66, ask 0.45 -> edge 0.21.
    """
    bin_a = Bin(low=60.0, high=61.0, unit="F", label="60-61F")
    bin_b = Bin(low=61.0, high=62.0, unit="F", label="61-62F")
    row_a = _row(condition_id="cond-A", yes_token="yesA", no_token="noA",
                 yes_asks=(("0.50", "100000"),), snapshot_id="snap-A")
    row_b = _row(condition_id="cond-B", yes_token="yesB", no_token="noB",
                 yes_asks=(("0.45", "100000"),), snapshot_id="snap-B")
    proof_a = _proof(direction="buy_yes", row=row_a, token_id="yesA",
                     q_posterior=0.62, q_lcb_5pct=0.58, bin_obj=bin_a)
    proof_b = _proof(direction="buy_yes", row=row_b, token_id="yesB",
                     q_posterior=0.70, q_lcb_5pct=0.66, bin_obj=bin_b)

    selected = era._selected_candidate_proof(
        {"family_id": "fam", "event_id": "evt"}, (proof_a, proof_b)
    )
    assert selected is proof_b, (
        "both candidates are FDR-significant with positive edge; the ranker must "
        "select the higher-ΔU winner and reject the dominated significant sibling "
        "(spec §12.D.1)"
    )


# ===========================================================================
# §12.D.3 / Hidden #5 / #10 — central NO vs adjacent YES through ONE matrix.
# ===========================================================================
def test_central_no_vs_adjacent_yes_through_one_payoff_matrix():
    """Spec §12.D.3 / Hidden #5 / #10. A central-bin NO and an adjacent-bin YES
    are scored on the SAME FamilyPayoffMatrix outcome set (every bin PLUS
    OUTSIDE), so they are compared on equal footing. The correct utility winner
    is selected.

    Construction: bin_i is the CENTRAL/modal bin (high YES mass 0.55) but its YES
    is OVERPRICED (ask 0.53 -> edge 0.02). NO_i wins on every OTHER outcome but its
    honest robust NO q_lcb = 1 - q_ucb_yes is only ~0.40 (the upper YES tail is
    fat), at a not-cheap NO ask 0.55 -> the central NO's robust edge is negative.
    The ADJACENT YES_{i+1} has q_lcb 0.42 at a cheap ask 0.30 -> a fat robust edge
    0.12. Through one matrix the adjacent YES has the higher ΔU and must be the live
    pick — NOT the high-q central NO (Hidden #10: a central NO looks diversified but
    is broad correlated exposure with low marginal utility) and NOT the overpriced
    central YES.
    """
    bin_i = Bin(low=60.0, high=61.0, unit="F", label="60-61F")       # central/modal
    bin_ip1 = Bin(low=61.0, high=62.0, unit="F", label="61-62F")     # adjacent
    # Central bin: high YES mass but OVERPRICED YES (0.53); its NO not-cheap (0.55).
    row_i = _row(condition_id="cond-i", yes_token="yesI", no_token="noI",
                 yes_asks=(("0.53", "100000"),), no_asks=(("0.55", "100000"),),
                 snapshot_id="snap-i")
    # Adjacent bin: cheap YES (0.30).
    row_ip1 = _row(condition_id="cond-ip1", yes_token="yesIp1", no_token="noIp1",
                   yes_asks=(("0.30", "100000"),), snapshot_id="snap-ip1")

    # YES proof on the CENTRAL bin establishes its high YES q_lcb in the shared π,
    # but is overpriced (q_lcb 0.55 at ask 0.53 -> thin edge).
    yes_central = _proof(direction="buy_yes", row=row_i, token_id="yesI",
                         q_posterior=0.60, q_lcb_5pct=0.55, bin_obj=bin_i)
    # Central NO: honest robust NO q_lcb (1 - q_ucb_yes) ~ 0.40, ask 0.55.
    no_central = _proof(direction="buy_no", row=row_i, token_id="noI",
                        q_posterior=0.40, q_lcb_5pct=0.40, bin_obj=bin_i)
    # Adjacent YES: q_lcb 0.42 at cheap ask 0.30 -> fat robust edge.
    yes_adjacent = _proof(direction="buy_yes", row=row_ip1, token_id="yesIp1",
                          q_posterior=0.48, q_lcb_5pct=0.42, bin_obj=bin_ip1)

    selected = era._selected_candidate_proof(
        {"family_id": "fam", "event_id": "evt"},
        (yes_central, no_central, yes_adjacent),
    )
    assert selected is yes_adjacent, (
        "the central-bin NO (high apparent q_no but thin/negative robust edge at a "
        "not-cheap NO ask) and the adjacent-bin cheap YES are scored on the SAME "
        "payoff matrix including OUTSIDE; the adjacent YES has the higher ΔU and "
        "must be the live pick (spec §12.D.3, Hidden #5/#10)"
    )

    # And the central NO must NOT be the pick: its robust edge (q_lcb_no 0.40 vs
    # all-in cost 0.55) is negative.
    assert selected is not no_central


# ===========================================================================
# §11 Phase 4 / Hidden #10 — existing family exposure shrinks ΔU / flips no-trade.
# ===========================================================================
def test_existing_family_exposure_shrinks_marginal_utility():
    """Spec §11 Phase 4 acceptance / Hidden #10. A nonzero PortfolioExposureVector
    REDUCES a candidate's ΔU (concavity of log: winning on an outcome where wealth
    is already large is worth less) and, with large enough existing exposure,
    flips it to no-trade.

    This is a property of ``utility_ranker.score_candidate`` at the seam — the live
    path builds the exposure vector from current/pending family exposure
    (``_robust_marginal_utility_exposure``). The test asserts the cross-module
    invariant directly on the ranker: same candidate, same matrix, same π; the
    ONLY change is the exposure baseline on the candidate's WIN outcome.
    """
    bin_x = Bin(low=60.0, high=61.0, unit="F", label="60-61F")
    row_x = _row(condition_id="cond-X", yes_token="yesX", no_token="noX",
                 yes_asks=(("0.40", "100000"),), snapshot_id="snap-X")
    proof_x = _proof(direction="buy_yes", row=row_x, token_id="yesX",
                     q_posterior=0.62, q_lcb_5pct=0.55, bin_obj=bin_x)
    cand = era._native_side_candidate_from_proof(family_key="fam", proof=proof_x)
    assert cand.is_tradeable

    matrix = utility_ranker.FamilyPayoffMatrix.over_bins([cand.bin_id])
    pi = utility_ranker.robust_probabilities(
        matrix, per_bin_q_lcb={cand.bin_id: cand.q_lcb}
    )
    baseline = Decimal("1000")

    # No existing exposure (flat baseline): positive ΔU at the optimum.
    flat = utility_ranker.PortfolioExposureVector.flat(matrix, baseline=baseline)
    score_flat = utility_ranker.score_candidate(cand, matrix, pi, flat)
    assert not score_flat.is_no_trade
    assert score_flat.delta_u > 0.0

    # MODERATE existing exposure on the WIN outcome (this bin): ΔU strictly smaller.
    moderate = utility_ranker.PortfolioExposureVector.from_outcome_wealth(
        matrix, baseline=baseline, extra_by_outcome={cand.bin_id: Decimal("2000")}
    )
    score_moderate = utility_ranker.score_candidate(cand, matrix, pi, moderate)
    assert score_moderate.delta_u < score_flat.delta_u, (
        "existing exposure on the candidate's own win outcome must shrink its "
        "marginal log utility (concavity of log; spec §11 Phase 4 / Hidden #10)"
    )

    # HUGE existing exposure on the win outcome: ΔU collapses to no-trade. The
    # marginal value of winning more where wealth is already enormous -> 0, while
    # the loss on OUTSIDE still bites -> ΔU <= 0.
    huge = utility_ranker.PortfolioExposureVector.from_outcome_wealth(
        matrix, baseline=baseline, extra_by_outcome={cand.bin_id: Decimal("1e12")}
    )
    score_huge = utility_ranker.score_candidate(cand, matrix, pi, huge)
    assert score_huge.is_no_trade, (
        "with enormous existing exposure on the win outcome, the marginal log "
        "utility of adding more must flip to no-trade (spec §11 Phase 4 acceptance)"
    )


def test_live_path_exposure_reduces_or_holds_stake_vs_flat():
    """The LIVE seam (``_select_proof_by_robust_marginal_utility``) consumes a real
    PortfolioExposureVector built from current/pending family exposure: a portfolio
    state already heavily committed to the candidate's win city/outcome must size
    the marginal bet no LARGER than the flat-baseline bet (and typically smaller).

    Asserts the relationship via the public sizing helper the live path uses, so a
    regression that drops the exposure baseline (silently restoring flat sizing)
    fails. Pure-object level so it needs no DB.
    """
    bin_x = Bin(low=60.0, high=61.0, unit="F", label="60-61F")
    row_x = _row(condition_id="cond-X", yes_token="yesX", no_token="noX",
                 yes_asks=(("0.40", "100000"),), snapshot_id="snap-X")
    proof_x = _proof(direction="buy_yes", row=row_x, token_id="yesX",
                     q_posterior=0.62, q_lcb_5pct=0.55, bin_obj=bin_x)

    # No exposure -> baseline flat -> optimal_stake at the flat optimum.
    stake_flat = era._robust_marginal_utility_optimal_stake_usd(
        family_key="fam",
        selected_proof=proof_x,
        all_proofs=(proof_x,),
        extra_exposure_by_bin_id={},
        bankroll_usd=10000.0,
        kelly_multiplier=1.0,
    )
    # Heavy existing exposure on the win bin -> strictly smaller (or zero) stake.
    cand = era._native_side_candidate_from_proof(family_key="fam", proof=proof_x)
    stake_exposed = era._robust_marginal_utility_optimal_stake_usd(
        family_key="fam",
        selected_proof=proof_x,
        all_proofs=(proof_x,),
        extra_exposure_by_bin_id={cand.bin_id: 50000.0},
        bankroll_usd=10000.0,
        kelly_multiplier=1.0,
    )
    assert stake_flat > 0.0
    assert stake_exposed <= stake_flat, (
        "existing family exposure on the win outcome must not INCREASE the marginal "
        "stake; the exposure-aware ΔU sizing must shrink it (spec §11 Phase 4)"
    )


# ===========================================================================
# DIRECTION LAW — selected candidate side agrees with proof direction.
# ===========================================================================
@pytest.mark.parametrize("direction,expected_side", [("buy_yes", "YES"), ("buy_no", "NO")])
def test_direction_law_side_matches_selected_proof(direction, expected_side):
    """Money-path iron law (spec §4/§6). For the selected candidate,
    direction=='buy_yes' iff its NativeSideCandidate.side=='YES' (own bin is the
    WIN outcome) and direction=='buy_no' iff side=='NO' (own bin is the LOSE
    outcome). The win/loss geometry is never inverted.
    """
    bin_x = Bin(low=60.0, high=61.0, unit="F", label="60-61F")
    if direction == "buy_yes":
        row_x = _row(condition_id="cond-X", yes_asks=(("0.40", "100000"),), snapshot_id="snap-X")
        token_id = "yes-1"
        # YES wins when Y is IN the bin: q_lcb_yes 0.55 vs ask 0.40 -> positive edge.
        q_lcb = 0.55
    else:
        row_x = _row(condition_id="cond-X", no_asks=(("0.30", "100000"),), snapshot_id="snap-X")
        token_id = "no-1"
        # NO wins when Y is OUT of the bin: honest robust NO q_lcb 0.60 vs ask 0.30.
        q_lcb = 0.60
    proof_x = _proof(direction=direction, row=row_x, token_id=token_id,
                     q_posterior=q_lcb + 0.05, q_lcb_5pct=q_lcb, bin_obj=bin_x)

    selected = era._selected_candidate_proof(
        {"family_id": "fam", "event_id": "evt"}, (proof_x,)
    )
    assert selected is proof_x
    cand = era._native_side_candidate_from_proof(family_key="fam", proof=selected)
    assert cand.side == expected_side
    # The matrix win/loss geometry is never inverted: a YES wins on its own bin,
    # a NO wins on every OTHER outcome (incl. OUTSIDE).
    matrix = utility_ranker.FamilyPayoffMatrix.over_bins([cand.bin_id])
    own_win = utility_ranker._candidate_wins(cand, cand.bin_id)
    outside_win = utility_ranker._candidate_wins(cand, utility_ranker.OUTSIDE_OUTCOME)
    if expected_side == "YES":
        assert own_win is True and outside_win is False
    else:
        assert own_win is False and outside_win is True


# ===========================================================================
# OUTSIDE OUTCOME ALWAYS PRESENT — Hidden #5.
# ===========================================================================
def test_outside_outcome_always_in_scored_matrix():
    """Hidden #5. Every FamilyPayoffMatrix used for ranking includes
    OUTSIDE_OUTCOME so a settlement with no winning bin is a real losing outcome
    for YES and a winning outcome for NO. Built by ``over_bins`` for any family.
    """
    for bins in ([], ["a"], ["a", "b", "c"]):
        if not bins:
            # over_bins with no bins still appends OUTSIDE -> single-outcome matrix.
            matrix = utility_ranker.FamilyPayoffMatrix.over_bins(bins)
        else:
            matrix = utility_ranker.FamilyPayoffMatrix.over_bins(bins)
        assert utility_ranker.OUTSIDE_OUTCOME in matrix.outcomes


# ===========================================================================
# SINGLE SELECTION PATH — no env/config can route to an alternate ranker.
# ===========================================================================
def test_single_selection_path_no_alternate_ranker(monkeypatch):
    """Operator directive. ``_selected_candidate_proof`` has exactly ONE selection
    algorithm (the marginal-utility ranker); no env var or config value can route
    to an alternate ranker or the legacy ``max(trade_score, q_lcb)`` fallback.

    Flipping every plausible legacy toggle must NOT change the decision.
    """
    import inspect

    src = inspect.getsource(era._selected_candidate_proof)
    decision_src = src + inspect.getsource(era._select_proof_by_robust_marginal_utility)
    # The decision never CALLS the legacy scalar surfaces or an off-able gate.
    # (A prose mention in a docstring describing what was REMOVED is fine; an actual
    # call is the violation — so strip docstring/comment lines before checking the
    # legacy-fallback call patterns.)
    assert "select_best_family_candidate(" not in decision_src
    assert "_opportunity_book_selector_enabled" not in decision_src
    code_lines = [
        ln for ln in decision_src.splitlines()
        if not ln.lstrip().startswith("#") and "``" not in ln
    ]
    code_only = "\n".join(code_lines)
    # The legacy fallback was `max(executable, key=(trade_score, q_lcb))` — assert
    # no such ranking call survives in executable code.
    assert "max(executable," not in code_only
    assert "trade_score" not in code_only

    bin_a = Bin(low=60.0, high=61.0, unit="F", label="60-61F")
    bin_b = Bin(low=61.0, high=62.0, unit="F", label="61-62F")
    row_a = _row(condition_id="cond-A", yes_token="yesA", no_token="noA",
                 yes_asks=(("0.55", "100000"),), snapshot_id="snap-A")
    row_b = _row(condition_id="cond-B", yes_token="yesB", no_token="noB",
                 yes_asks=(("0.40", "100000"),), snapshot_id="snap-B")
    proof_a = _proof(direction="buy_yes", row=row_a, token_id="yesA",
                     q_posterior=0.70, q_lcb_5pct=0.60, bin_obj=bin_a, trade_score=99.0)
    proof_b = _proof(direction="buy_yes", row=row_b, token_id="yesB",
                     q_posterior=0.65, q_lcb_5pct=0.55, bin_obj=bin_b, trade_score=0.01)

    for var in ("ZEUS_OPPORTUNITY_BOOK_SELECTOR", "ZEUS_OPPORTUNITY_BOOK_SHADOW"):
        for val in ("0", "1", "false", "true", "off", "on"):
            monkeypatch.setenv(var, val)
            selected = era._selected_candidate_proof(
                {"family_id": "fam", "event_id": "evt"}, (proof_a, proof_b)
            )
            # Bin B has the higher ΔU (cheaper) regardless of trade_score / env.
            assert selected is proof_b


# ===========================================================================
# ONE PRIMARY LEG PER FAMILY — rank yields exactly one primary (§14.8 / Hidden #7).
# ===========================================================================
def test_one_primary_leg_per_family():
    """Spec §14.8 / Hidden #7. ``_selected_candidate_proof`` returns exactly ONE
    primary leg (the top positive-ΔU candidate); every other positive candidate is
    WATCH-only and cannot reach intent build without a full re-rank. The function
    returns a single proof, never a set.
    """
    bins = [Bin(low=60.0 + i, high=61.0 + i, unit="F", label=f"{60 + i}-{61 + i}F") for i in range(3)]
    proofs = []
    # Three mutually-exclusive bins whose YES q_lcb sum to <= 1 (a realistic family;
    # the OUTSIDE residual absorbs 1 - Σ q_lcb). Each YES is cheap enough that its
    # robust edge stays positive AFTER the renormalized π, with DESCENDING edge so
    # bin 0 is the unambiguous primary.
    for i, (ask, qlcb) in enumerate([("0.15", 0.30), ("0.18", 0.25), ("0.22", 0.20)]):
        row = _row(condition_id=f"cond-{i}", yes_token=f"yes{i}", no_token=f"no{i}",
                   yes_asks=((ask, "100000"),), snapshot_id=f"snap-{i}")
        proofs.append(_proof(direction="buy_yes", row=row, token_id=f"yes{i}",
                             q_posterior=qlcb + 0.05, q_lcb_5pct=qlcb, bin_obj=bins[i]))

    selected = era._selected_candidate_proof(
        {"family_id": "fam", "event_id": "evt"}, tuple(proofs)
    )
    # Exactly one proof is returned (the primary), not a collection.
    assert isinstance(selected, era._CandidateProof)
    # It is the highest-ΔU candidate (bin 0: cheapest ask, fattest edge).
    assert selected is proofs[0]
