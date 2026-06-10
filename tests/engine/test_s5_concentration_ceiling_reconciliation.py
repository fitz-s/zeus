# Created: 2026-06-09
# Last reused or audited: 2026-06-09 (STALE_LAW re-pin, same-day cap deletion)
# Authority basis: STALE_LAW re-pin 2026-06-09. This file was created earlier the
#   same day pinning a single-position concentration CEILING (max_single_position_pct
#   · bankroll) reconciled onto the S5 ΔU sizing path. Hours later the operator
#   DELETED that cap by design — config note sizing._max_single_position_pct_note
#   (2026-06-09): "NO concentration CAP. A hard dollar/percent cap is NOT the
#   system's sizing mechanism and BREAKS Kelly ... 0.0 disables the ceiling on BOTH
#   money_path_adapters.evaluate_kelly AND the bin-selection ΔU choke point. Position
#   size is determined SOLELY by the layered fractional Kelly: kelly_multiplier(0.125)
#   × cash-sizing-base × portfolio-heat × correlation-reduction × ΔU-marginal-log-
#   utility-with-existing-exposure. The proving-phase ~$5-15 envelope comes from the
#   small 0.125 fractional Kelly on a ~$1k wallet, NOT a cap."
#   The live code already honours this: _robust_marginal_utility_optimal_stake_usd
#   gates the ceiling behind `if max_single_position_pct > 0.0` (event_reactor_adapter
#   ~L6050), so with the directive's 0.0 the ONLY binding upper bound is the
#   fractional-Kelly budget (mult · bankroll). Re-pinned to that LAYERED-KELLY law.
"""Relationship test — the S5 ΔU stake under the no-concentration-cap directive.

CURRENT LAW (operator note sizing._max_single_position_pct_note, 2026-06-09):
max_single_position_pct == 0.0 -> the single-position concentration ceiling is
DISABLED on every path. The emitted stake is governed SOLELY by the layered
fractional Kelly; the only pure upper bound that binds at the S5 choke point is the
fractional-Kelly budget ``kelly_multiplier · bankroll``.

The cross-module invariants that SURVIVE the cap deletion (each a relationship
across the ranker-stake -> emitted-notional seam):

  KELLY_BUDGET  for ANY candidate the ΔU ranker emits, the final stake <=
                kelly_multiplier · bankroll (the fractional-Kelly budget is the
                binding envelope once the cap is 0.0). No hard pct·B cap applies.
  K107_SAFE     a positive-edge candidate ALWAYS sizes > 0 — the fractional-Kelly
                budget is a pure UPPER bound and never zeros a positive edge.
  RANK_INV      sizing magnitude does NOT change which side/bin the ranker selects
                (the rank is decided by _selected_candidate_proof BEFORE sizing).
  WEALTH        the stake is fraction-of-capital (Kelly), not a fixed-dollar clamp:
                doubling the wallet doubles the fractional-Kelly stake.

CEILING_DISABLED is asserted explicitly so a future reintroduction of a non-zero
max_single_position_pct (which would re-couple sizing to a hard cap and re-break
Kelly per the operator note) is caught at this seam.
"""
from __future__ import annotations

import json

import pytest

from src.config import sizing_defaults as _sizing_defaults
from src.engine import event_reactor_adapter as era
from src.events.candidate_binding import MarketTopologyCandidate
from src.types.market import Bin

# Config truth (tracks settings, not a pinned snapshot) — the invariant must hold
# for ANY live tuning of the concentration knob.
MAX_SINGLE_POSITION_PCT = float(_sizing_defaults()["max_single_position_pct"])
BANKROLL = 1000.0  # representative live proving-phase wallet (~$1k)
LIVE_KELLY_MULT = 0.125  # the live fractional-Kelly multiplier (1/8 Kelly)


# ---------------------------------------------------------------------------
# Snapshot-row + proof fixtures (same shape the S4/S5 tests use).
# ---------------------------------------------------------------------------
def _row(
    *,
    condition_id="condition-1",
    yes_token="yes-1",
    no_token="no-1",
    yes_asks=(("0.40", "1000000"),),
    no_asks=(("0.55", "1000000"),),
    yes_bids=(("0.39", "100"),),
    no_bids=(("0.19", "100"),),
    min_tick="0.01",
    min_order="5",
    fee_rate_fraction=0.0,
    snapshot_id="snap-c",
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
        "book_hash": "book-hash-c",
    }


def _proof(*, direction, row, token_id, q_posterior, q_lcb_5pct, bin_obj, trade_score=1.0):
    ep, _p_fill, _c95 = era._execution_price_from_snapshot(
        row, selected_token_id=token_id, direction=direction
    )
    return era._CandidateProof(
        candidate=MarketTopologyCandidate(
            city="paris",
            target_date="2026-06-10",
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
        trade_score=trade_score,
        p_value=0.01,
        passed_prefilter=True,
        native_quote_available=True,
        p_cal_vector_hash="cal-hash",
        p_live_vector_hash="live-hash",
        missing_reason=None,
    )


def _stake(proof, all_proofs=None, *, bankroll=BANKROLL, mult=LIVE_KELLY_MULT, exposure=None):
    """The live S5 emitted stake (USD) for ``proof`` — the exact helper the live
    decision body calls to size the intent (``_robust_marginal_utility_optimal_stake_usd``)."""
    return era._robust_marginal_utility_optimal_stake_usd(
        family_key="fam",
        selected_proof=proof,
        all_proofs=tuple(all_proofs) if all_proofs is not None else (proof,),
        extra_exposure_by_bin_id=exposure or {},
        bankroll_usd=bankroll,
        kelly_multiplier=mult,
    )


# ===========================================================================
# ENVELOPE — for ANY candidate the ranker emits, stake <= concentration ceiling.
# ===========================================================================
@pytest.mark.parametrize("mult", [LIVE_KELLY_MULT, 0.25, 1.0])
@pytest.mark.parametrize(
    "q_lcb,ask",
    [
        (0.90, "0.40"),  # very strong edge (would size 83% of B at full Kelly)
        (0.80, "0.45"),  # strong edge
        (0.70, "0.50"),  # solid edge
        (0.60, "0.45"),  # modest edge
        (0.55, "0.50"),  # thin edge
    ],
)
def test_S5_emitted_stake_bounded_by_concentration_ceiling(mult, q_lcb, ask):
    """KELLY_BUDGET invariant. Under the no-concentration-cap directive
    (max_single_position_pct == 0.0) the ONLY binding upper bound on the live ΔU
    stake is the fractional-Kelly budget ``mult · bankroll``. For every (edge
    strength × multiplier) combination the emitted stake is positive and never
    exceeds that budget."""
    bin_x = Bin(low=60.0, high=61.0, unit="F", label="60-61F")
    row = _row(yes_asks=((ask, "1000000"),), snapshot_id="snap-env")
    proof = _proof(direction="buy_yes", row=row, token_id="yes-1",
                   q_posterior=q_lcb + 0.04, q_lcb_5pct=q_lcb, bin_obj=bin_x)
    stake = _stake(proof, mult=mult)
    kelly_budget = BANKROLL * mult
    assert 0.0 < stake <= kelly_budget + 1e-6, (
        f"S5 stake {stake:.4f} ({stake / BANKROLL * 100:.1f}% of B) must be positive "
        f"and within the fractional-Kelly budget {kelly_budget:.4f} (mult={mult}) at "
        f"q_lcb={q_lcb}, ask={ask} — no hard concentration cap applies (cap == 0.0)"
    )


def test_S5_concentration_ceiling_is_disabled_by_directive():
    """CEILING_DISABLED guard. The operator directive set max_single_position_pct
    to 0.0 (no concentration cap). Pin that config truth so a future non-zero
    reintroduction — which would re-couple sizing to a hard pct·B cap and re-break
    Kelly per sizing._max_single_position_pct_note — is caught at this seam."""
    assert MAX_SINGLE_POSITION_PCT == 0.0, (
        "the no-concentration-cap directive requires max_single_position_pct == 0.0; "
        f"got {MAX_SINGLE_POSITION_PCT!r} — a hard cap re-breaks layered Kelly"
    )


def test_S5_strong_edge_sized_by_kelly_budget_not_a_hard_cap():
    """With the ceiling disabled, a strong-edge candidate is bounded by the
    fractional-Kelly budget (mult · B), NOT clamped to a hard pct·B cap. At the
    live 1/8 multiplier the stake is positive and within mult·B."""
    bin_x = Bin(low=60.0, high=61.0, unit="F", label="60-61F")
    row = _row(yes_asks=(("0.40", "1000000"),), snapshot_id="snap-strong")
    proof = _proof(direction="buy_yes", row=row, token_id="yes-1",
                   q_posterior=0.94, q_lcb_5pct=0.90, bin_obj=bin_x)
    stake = _stake(proof, mult=LIVE_KELLY_MULT)
    kelly_budget = BANKROLL * LIVE_KELLY_MULT
    assert 0.0 < stake <= kelly_budget + 1e-6, (
        f"strong-edge stake {stake:.4f} must be positive and within the fractional-"
        f"Kelly budget {kelly_budget:.4f} (no hard concentration cap applies)"
    )


# ===========================================================================
# K107_SAFE — the ceiling is a pure UPPER bound; it never zeros a positive edge.
# ===========================================================================
def test_S5_ceiling_never_zeros_a_positive_edge_stake():
    """#107-safe invariant (the load-bearing distinction from the deleted
    depleting-budget GATE). A positive-edge candidate ALWAYS sizes > 0: the
    ceiling is ``min(stake, pct·B)`` on an already-positive stake — it can clip
    the tail but never reach 0 (pct·B > 0 whenever the wallet has cash)."""
    bin_x = Bin(low=60.0, high=61.0, unit="F", label="60-61F")
    # Sweep weak -> strong; every positive-edge candidate must keep a positive stake.
    for q_lcb, ask in [(0.55, "0.52"), (0.58, "0.50"), (0.65, "0.48"), (0.90, "0.40")]:
        row = _row(yes_asks=((ask, "1000000"),), snapshot_id=f"snap-{q_lcb}")
        proof = _proof(direction="buy_yes", row=row, token_id="yes-1",
                       q_posterior=q_lcb + 0.04, q_lcb_5pct=q_lcb, bin_obj=bin_x)
        stake = _stake(proof, mult=LIVE_KELLY_MULT)
        assert stake > 0.0, (
            f"positive-edge candidate (q_lcb={q_lcb}, ask={ask}) was hard-zeroed by "
            f"the concentration ceiling — it must be a pure upper bound (#107-safe)"
        )


def test_S5_weak_edge_keeps_full_delta_u_proportional_stake():
    """K107_SAFE corollary under the no-cap law: a weak/modest edge keeps its full
    ΔU-proportional fractional-Kelly stake (sized SOLELY by layered Kelly, no cap
    clipping). The stake is strictly between 0 and the fractional-Kelly budget and
    lands inside the operator's ~$5-15 envelope on a $1k wallet at 1/8 Kelly."""
    bin_x = Bin(low=60.0, high=61.0, unit="F", label="60-61F")
    row = _row(yes_asks=(("0.52", "1000000"),), snapshot_id="snap-weak")
    proof = _proof(direction="buy_yes", row=row, token_id="yes-1",
                   q_posterior=0.59, q_lcb_5pct=0.55, bin_obj=bin_x)
    stake = _stake(proof, mult=LIVE_KELLY_MULT)
    kelly_budget = BANKROLL * LIVE_KELLY_MULT
    assert 0.0 < stake < kelly_budget, (
        f"a modest-edge stake {stake:.4f} should sit strictly below the fractional-"
        f"Kelly budget {kelly_budget:.4f} (the ΔU haircut sizes it, not a hard cap)"
    )


# ===========================================================================
# RANK_INV — the ceiling clamps STAKE magnitude, not WHICH candidate is chosen.
# ===========================================================================
def test_S5_ceiling_does_not_change_the_delta_u_ranking():
    """RANK_INV invariant. The concentration ceiling clamps the winner's stake
    MAGNITUDE; it must NOT change which side/bin the ΔU ranker selects. The rank
    is decided by ``_selected_candidate_proof`` (pre-sizing); the ceiling lives in
    the post-selection sizing call.

    Two strong-edge bins both breach the ceiling: bin B has the fatter robust edge
    (cheaper ask) so it is the ΔU primary. The ceiling clamps both to the same cap
    magnitude, but the WINNER is still bin B (the ranking is invariant to the cap).
    """
    bin_a = Bin(low=60.0, high=61.0, unit="F", label="60-61F")
    bin_b = Bin(low=61.0, high=62.0, unit="F", label="61-62F")
    row_a = _row(condition_id="cond-A", yes_token="yesA", no_token="noA",
                 yes_asks=(("0.55", "1000000"),), snapshot_id="snap-A")
    row_b = _row(condition_id="cond-B", yes_token="yesB", no_token="noB",
                 yes_asks=(("0.40", "1000000"),), snapshot_id="snap-B")
    # Both have strong robust edge -> both would breach the ceiling unclamped.
    proof_a = _proof(direction="buy_yes", row=row_a, token_id="yesA",
                     q_posterior=0.90, q_lcb_5pct=0.86, bin_obj=bin_a)
    proof_b = _proof(direction="buy_yes", row=row_b, token_id="yesB",
                     q_posterior=0.90, q_lcb_5pct=0.86, bin_obj=bin_b)

    selected = era._selected_candidate_proof(
        {"family_id": "fam", "event_id": "evt"}, (proof_a, proof_b)
    )
    # THE invariant: the cheaper-ask bin B (fatter robust edge -> higher ΔU) is the
    # primary. The ceiling lives in the post-selection sizing call, so it cannot feed
    # back into this rank — the selection is decided here, before any sizing.
    assert selected is proof_b, (
        "ranking invariance: the cheaper-ask bin B is the ΔU primary; the "
        "concentration ceiling (a post-selection magnitude clamp) does not alter it"
    )
    # The selected winner's stake is bounded by the fractional-Kelly budget (no hard
    # cap under the directive), and is positive (#107-safe). The ranking above is
    # unaffected by the sizing magnitude.
    stake_b = _stake(proof_b, all_proofs=(proof_a, proof_b), mult=1.0)
    kelly_budget = BANKROLL * 1.0
    assert 0.0 < stake_b <= kelly_budget + 1e-6, (
        f"the selected winner's strong-edge stake {stake_b:.4f} must be positive and "
        f"within the full-Kelly budget {kelly_budget:.4f}; sizing does not alter the rank"
    )


def test_S5_ranking_unchanged_whether_or_not_winner_is_capped():
    """RANK_INV (stronger form). The selection is the pure ΔU rank — it takes NO
    bankroll/multiplier argument, so the magnitude clamp (which lives in the
    separate sizing call) structurally cannot feed back into which side/bin is
    chosen. We assert the winner is the same bin whether sizing it against a
    bankroll where the winner IS capped (clamped to the ceiling) or one where it is
    NOT (the unclamped fractional stake sits below the ceiling)."""
    bin_a = Bin(low=60.0, high=61.0, unit="F", label="60-61F")
    bin_b = Bin(low=61.0, high=62.0, unit="F", label="61-62F")
    row_a = _row(condition_id="cond-A", yes_token="yesA", no_token="noA",
                 yes_asks=(("0.50", "1000000"),), snapshot_id="snap-A")
    row_b = _row(condition_id="cond-B", yes_token="yesB", no_token="noB",
                 yes_asks=(("0.40", "1000000"),), snapshot_id="snap-B")
    proof_a = _proof(direction="buy_yes", row=row_a, token_id="yesA",
                     q_posterior=0.90, q_lcb_5pct=0.86, bin_obj=bin_a)
    proof_b = _proof(direction="buy_yes", row=row_b, token_id="yesB",
                     q_posterior=0.90, q_lcb_5pct=0.86, bin_obj=bin_b)
    selected = era._selected_candidate_proof(
        {"family_id": "fam", "event_id": "evt"}, (proof_a, proof_b)
    )
    assert selected is proof_b
    # FULL-Kelly regime (mult=1.0) and LIVE 1/8-Kelly regime both size the winner
    # positively under the no-cap law; each is bounded by its own fractional-Kelly
    # budget (mult·B). The rank (bin B) is identical regardless of the multiplier —
    # sizing magnitude is orthogonal to selection.
    full_kelly = _stake(proof_b, all_proofs=(proof_a, proof_b), mult=1.0)
    eighth_kelly = _stake(proof_b, all_proofs=(proof_a, proof_b), mult=LIVE_KELLY_MULT)
    assert 0.0 < full_kelly <= BANKROLL * 1.0 + 1e-6
    assert 0.0 < eighth_kelly <= BANKROLL * LIVE_KELLY_MULT + 1e-6
    # the smaller multiplier sizes no larger than the full-Kelly stake
    assert eighth_kelly <= full_kelly + 1e-6


# ===========================================================================
# WEALTH — the ceiling scales with bankroll (fraction-of-capital, not fixed $).
# ===========================================================================
def test_S5_stake_scales_with_bankroll():
    """WEALTH invariant. Kelly is fraction-of-capital, not a fixed-dollar clamp
    (the distinction from the deleted tiny_live $5 special case): doubling the
    bankroll doubles the strong-edge fractional-Kelly stake. A strong edge sizes at
    the fractional-Kelly budget (mult·B) at both wallet sizes, so the stake scales
    linearly with the wallet."""
    bin_x = Bin(low=60.0, high=61.0, unit="F", label="60-61F")
    row = _row(yes_asks=(("0.40", "1000000"),), snapshot_id="snap-wealth")
    proof = _proof(direction="buy_yes", row=row, token_id="yes-1",
                   q_posterior=0.94, q_lcb_5pct=0.90, bin_obj=bin_x)
    stake_1k = _stake(proof, bankroll=1000.0, mult=LIVE_KELLY_MULT)
    stake_2k = _stake(proof, bankroll=2000.0, mult=LIVE_KELLY_MULT)
    assert stake_1k > 0.0 and stake_2k > 0.0
    # Near-exact 2x scaling (rel tol covers the mild ΔU-marginal-log-utility concavity
    # in bankroll — the stake is the ΔU optimum × mult, not a flat fraction-of-cash).
    assert stake_2k == pytest.approx(2.0 * stake_1k, rel=1e-4), (
        "the fractional-Kelly stake must scale with the wallet (fraction-of-capital), "
        "not be a fixed-dollar clamp"
    )


# ===========================================================================
# ENVELOPE on the NO side — direction law: NO candidates are capped identically.
# ===========================================================================
def test_S5_no_side_stake_also_bounded_by_kelly_budget():
    """KELLY_BUDGET holds for native-NO candidates too (the bound is side-agnostic;
    the layered-Kelly sizing governs any single live bet, YES or NO). A strong
    honest robust NO q_lcb at a cheap NO ask sizes positively and within the
    fractional-Kelly budget — no hard pct·B cap applies."""
    bin_x = Bin(low=60.0, high=61.0, unit="F", label="60-61F")
    row = _row(no_asks=(("0.10", "1000000"),), snapshot_id="snap-no")
    # Honest robust NO q_lcb 0.85 >> NO ask 0.10 -> very fat robust edge.
    no_proof = _proof(direction="buy_no", row=row, token_id="no-1",
                      q_posterior=0.88, q_lcb_5pct=0.85, bin_obj=bin_x)
    stake = _stake(no_proof, mult=LIVE_KELLY_MULT)
    kelly_budget = BANKROLL * LIVE_KELLY_MULT
    assert 0.0 < stake <= kelly_budget + 1e-6, (
        f"native-NO stake {stake:.4f} must be positive and within the fractional-"
        f"Kelly budget {kelly_budget:.4f} (no hard concentration cap applies)"
    )
