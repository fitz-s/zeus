# Created: 2026-06-08
# Last reused or audited: 2026-06-08
# Authority basis: "bin selection.md" §3/§5.2 (robust q_lcb sizing) + §6 (why ΔU
#   beats q / q-c / ROI; "Native NO on a central bin can have high probability but
#   low marginal utility") + §9 Hidden #3 (native NO conservatism: q_lcb_no =
#   1 - q_ucb_yes) + Hidden #10 (central NO is broad correlated exposure) +
#   §13 (no-trade gate: robust marginal expected log utility <= 0) + §14.7 +
#   operator directive 2026-06-08 (the marginal-utility ranker subsumes the
#   central-NO-vs-adjacent-YES comparison through one payoff matrix; remove
#   redundant bolted-on gates — scattered on/off gates ARE the regression disease).
"""Antibodies: ΔU SUBSUMES the bolted-on scalar NO-demotion gates (so they go).

Two scalar gates that S4 retires, each with a settlement-grounded relationship
test proving the marginal-utility ranker ALREADY makes the losing bet
structurally unprofitable (Fitz methodology: make the CATEGORY impossible via the
robust q_lcb + cost-curve ΔU, not a separate score=0 gate):

  1. _market_disagreement_demotes_buy_no — the cheap-NO-overconfidence loser
     (market prices NO cheap = confident YES; the system buys NO on an
     overconfident q tail). With the HONEST robust NO q_lcb = 1 - q_ucb_yes the
     ΔU ranker scores it no-trade: a low honest NO win-prob cannot cover even a
     cheap NO all-in cost. The separate scalar gate is redundant.

  2. _family_structure_rejection_reasons buy_no-on-modal-bin guard — a NO on the
     forecast-modal bin. Through ONE FamilyPayoffMatrix the modal-bin NO simply
     scores LOWER ΔU than the modal YES (its honest robust NO q_lcb is small on a
     high-YES-mass bin), so it is dominated without a side-specific exclusion.

Also pins ROBUST-LOWER-BOUND SIZING: the live stake derives from q_lcb via
RobustCandidateScore.optimal_stake_usd, never from q_point.
"""
from __future__ import annotations

import json
from decimal import Decimal

from src.engine import event_reactor_adapter as era
from src.events.candidate_binding import MarketTopologyCandidate
from src.strategy import utility_ranker
from src.types.market import Bin


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
    snapshot_id="snap",
):
    depth = {
        "YES": {"asks": [{"price": p, "size": s} for p, s in yes_asks],
                "bids": [{"price": p, "size": s} for p, s in yes_bids]},
        "NO": {"asks": [{"price": p, "size": s} for p, s in no_asks],
               "bids": [{"price": p, "size": s} for p, s in no_bids]},
    }
    return {
        "snapshot_id": snapshot_id, "condition_id": condition_id,
        "yes_token_id": yes_token, "no_token_id": no_token,
        "selected_outcome_token_id": "", "outcome_label": "",
        "min_tick_size": min_tick, "min_order_size": min_order,
        "fee_details_json": json.dumps({"fee_rate_fraction": fee_rate_fraction}),
        "neg_risk": 0, "orderbook_depth_json": json.dumps(depth),
        "tradeability_status_json": "{}", "book_hash": "bh",
    }


def _proof(*, direction, row, token_id, q_posterior, q_lcb_5pct, bin_obj, trade_score=1.0):
    ep, _pf, _c = era._execution_price_from_snapshot(
        row, selected_token_id=token_id, direction=direction
    )
    return era._CandidateProof(
        candidate=MarketTopologyCandidate(
            city="paris", target_date="2026-06-10", metric="tmax",
            condition_id=str(row.get("condition_id") or ""),
            yes_token_id=str(row.get("yes_token_id") or ""),
            no_token_id=str(row.get("no_token_id") or ""),
            bin=bin_obj,
        ),
        token_id=token_id, direction=direction, row=row,
        executable_snapshot_id=str(row.get("snapshot_id") or ""),
        execution_price=ep, q_posterior=q_posterior, q_lcb_5pct=q_lcb_5pct,
        c_cost_95pct=None, p_fill_lcb=1.0, trade_score=trade_score, p_value=0.01,
        passed_prefilter=True, native_quote_available=True,
        p_cal_vector_hash="ch", p_live_vector_hash="lh", missing_reason=None,
    )


# ===========================================================================
# GATE 1 SUBSUMED — cheap-NO overconfidence loser is ΔU-no-trade (market-disagree).
# ===========================================================================
def test_cheap_no_overconfidence_loser_is_delta_u_no_trade():
    """Settlement-grounded antibody for removing _market_disagreement_demotes_buy_no.

    The dominant capital bleed (buy_no on cheap bins 0/12, -71%): market prices NO
    cheap (0.10 = confident YES); the system bets NO on an OVERCONFIDENT q tail.
    The HONEST robust NO q_lcb = 1 - q_ucb_yes is LOW when the market is confident
    YES (the upper YES tail is fat). The ΔU ranker scores the NO candidate with
    that honest q_lcb: a 0.10 all-in cost needs a win-prob > 0.10 to be positive,
    but the honest NO q_lcb is 0.08 < 0.10 -> robust edge negative -> ΔU <= 0 ->
    no-trade. The bet is UNCONSTRUCTABLE without any separate scalar gate.
    """
    bin_x = Bin(low=60.0, high=61.0, unit="F", label="60-61F")
    # Market confident YES -> NO ask cheap 0.10. Honest robust NO q_lcb = 0.08.
    row_x = _row(condition_id="cond-X", no_asks=(("0.10", "100000"),), snapshot_id="snap-X")
    no_proof = _proof(direction="buy_no", row=row_x, token_id="no-1",
                      q_posterior=0.85, q_lcb_5pct=0.08, bin_obj=bin_x, trade_score=5.0)

    cand = era._native_side_candidate_from_proof(family_key="fam", proof=no_proof)
    assert cand.is_tradeable and cand.side == "NO"
    matrix = utility_ranker.FamilyPayoffMatrix.over_bins([cand.bin_id])
    # The bin's YES q_lcb is high (market confident YES) -> shared π over the bin.
    pi = utility_ranker.robust_probabilities(matrix, per_bin_q_lcb={cand.bin_id: 0.85})
    exposure = utility_ranker.PortfolioExposureVector.flat(matrix, baseline=Decimal("1000"))
    score = utility_ranker.score_candidate(cand, matrix, pi, exposure)
    assert score.is_no_trade, (
        "the cheap-NO overconfidence bet (honest robust NO q_lcb 0.08 < all-in "
        "cost 0.10) must be ΔU-no-trade — the ranker subsumes the market-"
        "disagreement scalar gate (Hidden #3 honest NO q_lcb + §13)"
    )

    # And the LIVE selection path no-trades it (returns None) — single gate, no flag.
    selected = era._selected_candidate_proof(
        {"family_id": "fam", "event_id": "evt"}, (no_proof,)
    )
    assert selected is None

    # The removed scalar gate must no longer exist as a separate selection surface.
    assert not hasattr(era, "_market_disagreement_demotes_buy_no")


def test_cheap_no_with_honest_high_no_qlcb_survives():
    """The counter-case: a CHEAP NO whose HONEST robust NO q_lcb is genuinely high
    (the bin truly won't settle — legitimate settlement-licensed disagreement) is
    POSITIVE-ΔU and tradeable. The ΔU ranker does not blanket-ban cheap NO; it
    trades on the honest robust edge. (The old scalar gate only let this through
    above a hard 0.95 cutoff; ΔU lets it through whenever q_lcb_no > cost — the
    principled boundary.)
    """
    bin_x = Bin(low=60.0, high=61.0, unit="F", label="60-61F")
    row_x = _row(condition_id="cond-X", no_asks=(("0.10", "100000"),), snapshot_id="snap-X")
    # Honest robust NO q_lcb = 0.80 >> ask 0.10 -> fat robust edge.
    no_proof = _proof(direction="buy_no", row=row_x, token_id="no-1",
                      q_posterior=0.88, q_lcb_5pct=0.80, bin_obj=bin_x)
    selected = era._selected_candidate_proof(
        {"family_id": "fam", "event_id": "evt"}, (no_proof,)
    )
    assert selected is no_proof, (
        "a cheap NO with an honest HIGH robust NO q_lcb (q_lcb_no 0.80 > cost 0.10) "
        "is a positive-ΔU legitimate bet and must survive (ΔU trades on honest "
        "robust edge, not a hard 0.95 scalar cutoff)"
    )


# ===========================================================================
# GATE 2 SUBSUMED — NO on the forecast-modal bin scores lower ΔU than modal YES.
# ===========================================================================
def test_modal_bin_no_dominated_by_modal_yes_through_one_matrix():
    """Settlement-grounded antibody for removing the _family_structure_rejection_
    reasons buy_no-on-modal-bin guard.

    On the forecast-MODAL bin (highest YES mass) the YES candidate has a large
    honest robust q_lcb_yes; the NO on that SAME bin has a SMALL honest robust
    q_lcb_no = 1 - q_ucb_yes (the upper YES tail is fat on a high-mass bin). Through
    ONE FamilyPayoffMatrix the modal YES has strictly higher ΔU, so the modal-bin
    NO is dominated WITHOUT a side-specific exclusion. The scalar modal-bin guard
    is redundant.
    """
    bin_modal = Bin(low=60.0, high=61.0, unit="F", label="60-61F")
    # Modal bin: cheap YES (0.45) with high q_lcb_yes; NO priced 0.40, low q_lcb_no.
    row = _row(condition_id="cond-M", yes_token="yesM", no_token="noM",
               yes_asks=(("0.45", "100000"),), no_asks=(("0.40", "100000"),),
               snapshot_id="snap-M")
    yes_modal = _proof(direction="buy_yes", row=row, token_id="yesM",
                       q_posterior=0.72, q_lcb_5pct=0.68, bin_obj=bin_modal)
    # Honest robust NO q_lcb on the modal bin is small (0.20): upper YES tail fat.
    no_modal = _proof(direction="buy_no", row=row, token_id="noM",
                      q_posterior=0.28, q_lcb_5pct=0.20, bin_obj=bin_modal)

    selected = era._selected_candidate_proof(
        {"family_id": "fam", "event_id": "evt"}, (yes_modal, no_modal)
    )
    assert selected is yes_modal, (
        "the modal-bin YES (q_lcb 0.68 at ask 0.45) dominates the modal-bin NO "
        "(honest q_lcb_no 0.20 < all-in cost 0.40 -> negative robust edge) through "
        "ONE payoff matrix; the modal-bin NO is dominated without a side-specific "
        "guard (operator directive: the matrix subsumes it)"
    )
    # The removed guard helper must no longer exist on the selector module.
    import src.events.opportunity_selector as opp_sel
    assert not hasattr(opp_sel, "_family_structure_rejection_reasons")


# ===========================================================================
# ROBUST-LOWER-BOUND SIZING — stake derives from q_lcb via optimal_stake_usd.
# ===========================================================================
def test_stake_derives_from_q_lcb_not_q_point():
    """Money-path iron law (spec §3/§5.2). The live stake equals
    RobustCandidateScore.optimal_stake_usd from a score computed on q_lcb-based π
    (robust LOWER bound), NOT q_point. Two proofs identical in q_lcb but differing
    in q_point (q_posterior) must size IDENTICALLY — proving q_point is not the
    sizing authority. A q_point-based sizer (the legacy evaluate_kelly, which sizes
    on p_posterior=q_posterior) would size them DIFFERENTLY.
    """
    bin_x = Bin(low=60.0, high=61.0, unit="F", label="60-61F")
    row = _row(condition_id="cond-X", yes_asks=(("0.40", "100000"),), snapshot_id="snap-X")
    # Same q_lcb (0.55), different q_point (0.60 vs 0.95).
    low_qpoint = _proof(direction="buy_yes", row=row, token_id="yes-1",
                        q_posterior=0.60, q_lcb_5pct=0.55, bin_obj=bin_x)
    high_qpoint = _proof(direction="buy_yes", row=row, token_id="yes-1",
                         q_posterior=0.95, q_lcb_5pct=0.55, bin_obj=bin_x)

    stake_low = era._robust_marginal_utility_optimal_stake_usd(
        family_key="fam", selected_proof=low_qpoint, all_proofs=(low_qpoint,),
        extra_exposure_by_bin_id={}, bankroll_usd=10000.0, kelly_multiplier=1.0,
    )
    stake_high = era._robust_marginal_utility_optimal_stake_usd(
        family_key="fam", selected_proof=high_qpoint, all_proofs=(high_qpoint,),
        extra_exposure_by_bin_id={}, bankroll_usd=10000.0, kelly_multiplier=1.0,
    )
    assert stake_low > 0.0
    assert abs(stake_low - stake_high) < 1e-6, (
        "stake must derive from q_lcb (robust lower bound), not q_point; two proofs "
        "with equal q_lcb but different q_posterior must size identically (spec §3/§5.2)"
    )


def test_wider_fractional_kelly_haircut_sizes_strictly_smaller():
    """FRACTIONAL KELLY PRESERVED (spec §5.2). The CI-width/lead/portfolio-heat
    haircut still bounds the stake (as the fractional-Kelly multiplier ceiling into
    the ΔU sizing), so a smaller multiplier sizes strictly smaller — variance is
    never silently dropped on the way to size.

    Both multipliers are SMALL (0.05 / 0.0125) so the fractional stakes sit BELOW
    the single-position concentration ceiling (max_single_position_pct·B = $500 at
    B=$10k); the haircut signal lives in the unclamped region. At full Kelly this
    edge clamps to the ceiling at BOTH multipliers and the strict-smaller signal
    would be masked (the ceiling bounds magnitude, not the fractional-Kelly ordering).
    """
    bin_x = Bin(low=60.0, high=61.0, unit="F", label="60-61F")
    row = _row(condition_id="cond-X", yes_asks=(("0.40", "100000"),), snapshot_id="snap-X")
    proof = _proof(direction="buy_yes", row=row, token_id="yes-1",
                   q_posterior=0.60, q_lcb_5pct=0.55, bin_obj=bin_x)

    full = era._robust_marginal_utility_optimal_stake_usd(
        family_key="fam", selected_proof=proof, all_proofs=(proof,),
        extra_exposure_by_bin_id={}, bankroll_usd=10000.0, kelly_multiplier=0.05,
    )
    haircut = era._robust_marginal_utility_optimal_stake_usd(
        family_key="fam", selected_proof=proof, all_proofs=(proof,),
        extra_exposure_by_bin_id={}, bankroll_usd=10000.0, kelly_multiplier=0.0125,
    )
    assert full < 10000.0 * 0.05, (
        "fixture sanity: both stakes must sit below the concentration ceiling so the "
        "fractional-Kelly haircut signal is not masked by the magnitude clamp"
    )
    assert 0.0 < haircut < full, (
        "a smaller fractional-Kelly multiplier (the CI-width/lead/heat haircut) "
        "must size strictly smaller — the fractional Kelly ceiling is preserved"
    )
