# Created: 2026-06-08
# Last reused or audited: 2026-06-08
# Authority basis: "bin selection.md" §5.1-§5.3 (Kelly derivation + cost-curve
#   optimizer s* = argmax_s ΔU) + §5.2 (robust q_eff with fractional/microstructure
#   /portfolio haircuts) + §14.7/§14.10 (size with q_lcb, recompute-not-validate) +
#   §9 Hidden #6 ("scalar VWMP hides the convex cost curve"; over-bets into thin
#   levels) + §9 Hidden #15 (fee-hash drift -> Kelly monotonicity) + §12.C.2/.3/.4
#   (Kelly@price relationship tests) + operator directive 2026-06-08 (S5: size from
#   RobustCandidateScore.optimal_stake_usd; the chosen-stake ExecutionPrice is the
#   typed fee-deducted Kelly boundary via ExecutableCostCurve.avg_cost(optimal_stake),
#   REPLACING the scalar min-order/top-ask price as the size+price authority).
"""S5 RELATIONSHIP TESTS — the chosen-stake ExecutionPrice closes Hidden #6.

The cross-module invariant S5 enforces: the live order's Kelly cost-of-entry is
the DEPTH-WALKED avg cost AT THE CHOSEN STAKE (ExecutableCostCurve.avg_cost(
optimal_stake_usd)), not the cheap top-of-book / min-order scalar. Scalar Kelly on
a single top-ask price over-bets into thin levels (Hidden #6); the cost-curve
optimizer in score_candidate already maximized ΔU over the feasible depth-bounded
stake interval, so the price the order is sized against MUST be the same convex
curve evaluated at that stake.

These are RELATIONSHIP tests (Module A = the ΔU sizer's optimal_stake; Module B =
the ExecutableCostCurve's avg_cost): they assert a property that holds ACROSS the
size->price boundary, written before the implementation. The S5 site under test is
``_chosen_stake_execution_price`` (the typed boundary recomputation) wired into the
live decision body of ``evaluate_event_bound_submission`` at the sizing seam.
"""
from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

import pytest

from src.contracts.execution_price import ExecutionPrice
from src.contracts.executable_cost_curve import (
    BookLevel,
    ExecutableCostCurve,
    FeeModel,
)
from src.engine import event_reactor_adapter as era
from src.strategy import utility_ranker
from src.strategy.utility_ranker import (
    FamilyPayoffMatrix,
    PortfolioExposureVector,
    robust_probabilities,
    score_candidate,
)


# ---------------------------------------------------------------------------
# Builders: a single-bin family with a YES native side priced by a real curve.
# A thin top level forces the depth walk to cross into a worse second level.
# ---------------------------------------------------------------------------
def _curve(
    *,
    levels: tuple[tuple[str, str], ...],
    fee_rate: str = "0.0",
    min_order: str = "5",
    token_id: str = "yes-1",
    side: str = "YES",
) -> ExecutableCostCurve:
    from datetime import timedelta

    return ExecutableCostCurve(
        token_id=token_id,
        side=side,  # type: ignore[arg-type]
        snapshot_id="snap",
        book_hash="bh",
        levels=tuple(BookLevel(price=Decimal(p), size=Decimal(s)) for p, s in levels),
        fee_model=FeeModel(fee_rate=Decimal(fee_rate)),
        min_tick=Decimal("0.01"),
        min_order_size=Decimal(min_order),
        quote_ttl=timedelta(seconds=30),
    )


def _yes_candidate(curve: ExecutableCostCurve, *, q_lcb: float, q_point: float):
    from src.contracts.native_side_candidate import NativeSideCandidate
    from src.strategy.probability_uncertainty import probability_uncertainty_from_samples

    # A degenerate-but-valid ProbabilityUncertainty whose q_lcb/q_point match.
    pu = era._proof_probability_uncertainty(q_point=q_point, q_lcb=q_lcb)
    return NativeSideCandidate.tradeable(
        family_key="fam",
        bin_id="bin-1",
        side="YES",
        token_id=curve.token_id,
        condition_id="cond-1",
        q_point=q_point,
        q_lcb=q_lcb,
        probability_uncertainty=pu,
        executable_cost_curve=curve,
        forecast_snapshot_id="fsnap",
        market_snapshot_id="snap",
        hypothesis_id="hyp-1",
    )


def _optimal_stake(curve, *, q_lcb, q_point, max_stake_usd=Decimal("1000000")):
    """The ΔU optimizer's optimal_stake_usd for a single-bin YES candidate."""
    cand = _yes_candidate(curve, q_lcb=q_lcb, q_point=q_point)
    matrix = FamilyPayoffMatrix.over_bins([cand.bin_id])
    pi = robust_probabilities(matrix, per_bin_q_lcb={cand.bin_id: q_lcb})
    exposure = PortfolioExposureVector.flat(matrix, baseline=Decimal("100000"))
    score = score_candidate(cand, matrix, pi, exposure, max_stake_usd=max_stake_usd)
    return score


# ===========================================================================
# §12.C.3 / Hidden #6 — a thinner second level yields a SMALLER optimal stake
# than the scalar top-ask formula would, and a HIGHER chosen-stake avg cost.
# ===========================================================================
def test_depth_curve_worse_price_lowers_size():
    """A thin top level + worse deep level sizes SMALLER than the scalar top-ask
    Kelly would (Hidden #6). The scalar top-ask formula x* = (q_lcb - c_top)/(1-c_top)
    prices the WHOLE stake at the cheap top ask; the cost-curve optimizer sees the
    convex walk into the worse second level and never overbets into it.
    """
    q_lcb = 0.70
    # DEEP book: the whole stake fills at the cheap 0.40 top ask (no thin walk).
    deep = _curve(levels=(("0.40", "1000000"),))
    # THIN book: only 50 shares at 0.40, then a much worse 0.60 level. A large
    # stake must walk into 0.60 -> higher avg cost -> the optimizer pulls the stake
    # back so it does not overbet the convex tail.
    thin = _curve(levels=(("0.40", "50"), ("0.60", "1000000")))

    deep_score = _optimal_stake(deep, q_lcb=q_lcb, q_point=q_lcb)
    thin_score = _optimal_stake(thin, q_lcb=q_lcb, q_point=q_lcb)

    assert deep_score.optimal_stake_usd > Decimal("0")
    assert thin_score.optimal_stake_usd > Decimal("0")
    assert thin_score.optimal_stake_usd < deep_score.optimal_stake_usd, (
        "a thinner second level must yield a SMALLER optimal stake than the deep "
        "(scalar-top-ask-equivalent) book — the cost-curve optimizer does not "
        "overbet into thin depth (§12.C.3, Hidden #6)"
    )

    # And the chosen-stake ExecutionPrice on the THIN book is strictly WORSE than the
    # top-ask scalar (0.40): the realized boundary reflects the depth walk, not the
    # cheap top of book. This is the price the S5 boundary must carry to the intent.
    thin_price = thin.avg_cost(thin_score.optimal_stake_usd)
    assert isinstance(thin_price, ExecutionPrice)
    assert thin_price.value > 0.40, (
        "the chosen-stake avg cost on a thin book must exceed the top-ask 0.40 — "
        "the order is priced against the depth walk it actually consumes, not the "
        "top of book (Hidden #6)"
    )


# ===========================================================================
# §12.C.4 — monotone: a LOWER q_lcb yields a SMALLER optimal stake.
# ===========================================================================
@pytest.mark.parametrize("q_hi,q_lo", [(0.75, 0.60), (0.60, 0.55)])
def test_lower_q_lcb_lowers_size(q_hi, q_lo):
    """Monotonicity (§12.C.4): with the cost curve and exposure held fixed, a lower
    robust q_lcb sizes strictly smaller. The size derives from q_lcb (the robust
    LOWER bound), so shaving q_lcb shrinks the optimal stake.
    """
    curve = _curve(levels=(("0.40", "1000000"),))
    hi = _optimal_stake(curve, q_lcb=q_hi, q_point=q_hi)
    lo = _optimal_stake(curve, q_lcb=q_lo, q_point=q_lo)
    assert hi.optimal_stake_usd > Decimal("0")
    assert lo.optimal_stake_usd > Decimal("0")
    assert lo.optimal_stake_usd < hi.optimal_stake_usd, (
        f"lower q_lcb ({q_lo}) must size strictly smaller than higher q_lcb "
        f"({q_hi}) on the same curve (§12.C.4 monotonicity)"
    )


# ===========================================================================
# §12.C.2 / Hidden #15 — a higher fee lowers the stake AND raises the avg_cost.
# ===========================================================================
def test_fee_increase_lowers_size():
    """Same q, a higher FeeModel rate -> SMALLER optimal stake AND a HIGHER chosen-
    stake avg_cost ExecutionPrice (§12.C.2, Hidden #15). The fee raises the all-in
    cost c(s), which both shrinks the robust edge (q_lcb - c) and the size, and is
    reflected in the typed price the order is sized against.
    """
    q_lcb = 0.70
    cheap = _curve(levels=(("0.40", "1000000"),), fee_rate="0.0")
    pricey = _curve(levels=(("0.40", "1000000"),), fee_rate="0.30")

    cheap_score = _optimal_stake(cheap, q_lcb=q_lcb, q_point=q_lcb)
    pricey_score = _optimal_stake(pricey, q_lcb=q_lcb, q_point=q_lcb)

    assert cheap_score.optimal_stake_usd > Decimal("0")
    assert pricey_score.optimal_stake_usd > Decimal("0")
    assert pricey_score.optimal_stake_usd < cheap_score.optimal_stake_usd, (
        "a higher fee rate must size strictly smaller (the fee raises c(s) and "
        "shrinks the robust edge q_lcb - c) — §12.C.2"
    )

    cheap_price = cheap.avg_cost(cheap_score.optimal_stake_usd)
    pricey_price = pricey.avg_cost(pricey_score.optimal_stake_usd)
    assert pricey_price.value > cheap_price.value, (
        "the higher fee must raise the chosen-stake avg_cost ExecutionPrice — the "
        "fee is in the all-in cost the order is priced at (Hidden #15)"
    )


# ===========================================================================
# §12.C / R1-R2 — the chosen-stake price is a typed fee-deducted probability-units
# ExecutionPrice that passes assert_kelly_safe (R1/R2 identity preserved).
# ===========================================================================
def test_chosen_stake_execution_price_is_fee_deducted_probability_units():
    """ExecutableCostCurve.avg_cost(optimal_stake) returns an ExecutionPrice that
    passes assert_kelly_safe (R1/R2 identity preserved): price_type != implied,
    fee_deducted=True, currency=probability_units. This is the typed Kelly boundary
    at the chosen stake that the S5 site feeds the intent — not a bare float, not a
    pre-fee ask.
    """
    curve = _curve(levels=(("0.40", "50"), ("0.55", "1000000")), fee_rate="0.05")
    score = _optimal_stake(curve, q_lcb=0.72, q_point=0.72)
    assert score.optimal_stake_usd > Decimal("0")

    price = curve.avg_cost(score.optimal_stake_usd)
    assert isinstance(price, ExecutionPrice)
    # R1/R2: it does not raise — typed, fee-deducted, probability_units.
    price.assert_kelly_safe()
    assert price.fee_deducted is True
    assert price.currency == "probability_units"
    assert price.price_type != "implied_probability"
    assert 0.0 < price.value < 1.0


# ===========================================================================
# S5 LIVE-SEAM RELATIONSHIP — the live boundary helper recomputes the chosen-stake
# price from the SELECTED candidate's OWN curve, and it is >= the min-order price
# whenever the chosen stake walks deeper than min order (Hidden #6 at the seam).
# ===========================================================================
def test_chosen_stake_boundary_helper_reprices_at_chosen_stake():
    """The S5 boundary helper (`_chosen_stake_execution_price`) reprices the SELECTED
    candidate at the CHOSEN stake on its OWN native cost curve. On a thin book where
    the chosen stake walks past the top level, the chosen-stake boundary is strictly
    WORSE than the min-order (top-of-book) price the proof was originally priced at —
    proving the live order no longer uses the cheap scalar top-ask (Hidden #6).
    """
    # Thin top level (50 shares @ 0.40) then a worse deep level (@ 0.60). The
    # min-order price (5 shares) fills entirely at 0.40; a larger chosen stake walks
    # into 0.60, so its avg cost exceeds 0.40.
    curve = _curve(levels=(("0.40", "50"), ("0.60", "1000000")))
    min_order_price = curve.avg_cost_for_shares(Decimal("5"))  # top-of-book, 0.40
    # A chosen stake big enough to exhaust the 50-share top level and walk into 0.60.
    chosen_stake_usd = Decimal("100")  # 50*0.40=20 fills top; remainder walks 0.60

    boundary = era._chosen_stake_execution_price(curve, chosen_stake_usd)
    assert isinstance(boundary, ExecutionPrice)
    boundary.assert_kelly_safe()
    assert boundary.value > float(min_order_price.value), (
        "the chosen-stake boundary on a thin book must exceed the min-order top-of-"
        "book price — the live order is priced against the depth it consumes, not "
        "the cheap top ask (S5 closes Hidden #6 at the live seam)"
    )


# ===========================================================================
# S5 KERNEL RELATIONSHIP — the sizing+pricing kernel returns a stake AND a chosen-
# stake price that AGREE: price == the SELECTED leg's OWN curve at the SAME stake.
# This is the load-bearing cross-module invariant — size (Module A: ΔU optimizer)
# and price (Module B: ExecutableCostCurve) come from ONE scored candidate and
# cannot drift. Driven through the real proof->candidate->curve->score->price path.
# ===========================================================================
def _proof_from_row(*, direction, row, token_id, q_posterior, q_lcb_5pct, bin_obj):
    from src.events.candidate_binding import MarketTopologyCandidate

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
        c_cost_95pct=None, p_fill_lcb=1.0, trade_score=1.0, p_value=0.01,
        passed_prefilter=True, native_quote_available=True,
        p_cal_vector_hash="ch", p_live_vector_hash="lh", missing_reason=None,
    )


def _snapshot_row(*, yes_asks, min_order="5", fee_rate_fraction=0.0):
    import json as _json

    depth = {
        "YES": {"asks": [{"price": p, "size": s} for p, s in yes_asks],
                "bids": [{"price": "0.30", "size": "100"}]},
        "NO": {"asks": [{"price": "0.55", "size": "100000"}],
               "bids": [{"price": "0.40", "size": "100"}]},
    }
    return {
        "snapshot_id": "snap", "condition_id": "cond-1",
        "yes_token_id": "yes-1", "no_token_id": "no-1",
        "selected_outcome_token_id": "", "outcome_label": "",
        "min_tick_size": "0.01", "min_order_size": min_order,
        "fee_details_json": _json.dumps({"fee_rate_fraction": fee_rate_fraction}),
        "neg_risk": 0, "orderbook_depth_json": _json.dumps(depth),
        "tradeability_status_json": "{}", "book_hash": "bh",
    }


def test_kernel_returns_stake_and_chosen_stake_price_that_agree():
    """The S5 kernel (`_robust_marginal_utility_stake_and_price`) returns a stake AND
    a typed chosen-stake price, and the price EQUALS the selected leg's OWN
    ExecutableCostCurve.avg_cost(stake). Size and price come from ONE scored
    candidate + ONE curve — they cannot drift (the cross-module S5 invariant).
    """
    from src.types.market import Bin

    bin_x = Bin(low=60.0, high=61.0, unit="F", label="60-61F")
    # Thin top (50 @ 0.40) then a worse deep level so a sizable stake walks deeper.
    row = _snapshot_row(yes_asks=(("0.40", "50"), ("0.60", "1000000")))
    proof = _proof_from_row(direction="buy_yes", row=row, token_id="yes-1",
                            q_posterior=0.75, q_lcb_5pct=0.72, bin_obj=bin_x)

    stake, price = era._robust_marginal_utility_stake_and_price(
        family_key="fam", selected_proof=proof, all_proofs=(proof,),
        extra_exposure_by_bin_id={}, bankroll_usd=10000.0, kelly_multiplier=1.0,
    )
    assert stake > 0.0
    assert isinstance(price, ExecutionPrice)
    price.assert_kelly_safe()

    # The price is the SELECTED candidate's OWN curve at the SAME chosen stake.
    cand = era._native_side_candidate_from_proof(family_key="fam", proof=proof)
    expected = cand.executable_cost_curve.avg_cost(Decimal(str(stake)))
    assert abs(price.value - expected.value) < 1e-12, (
        "the kernel's chosen-stake price must equal the selected leg's OWN cost "
        "curve evaluated at the SAME chosen stake — size and price share one curve "
        "(S5 cross-module invariant; no drift)"
    )

    # And the chosen-stake price differs from S1's cheap min-order boundary whenever
    # the chosen stake walks deeper than min order (Hidden #6): here the chosen
    # stake is large enough to cross into 0.60, so it is strictly worse than 0.40.
    min_order_boundary = proof.execution_price
    assert price.value > float(min_order_boundary.value), (
        "the chosen-stake boundary must beat (be worse than) the S1 min-order top-"
        "of-book price when the stake walks deeper — the intent no longer carries "
        "the cheap top-ask scalar (S5/Hidden #6)"
    )


def test_kernel_no_trade_returns_no_price():
    """A no-trade ΔU stake yields (0.0, None): no chosen-stake price, so the live
    body keeps the S1 boundary for the not-passed receipt and builds no intent
    (fail-closed; spec §13).
    """
    from src.types.market import Bin

    bin_x = Bin(low=60.0, high=61.0, unit="F", label="60-61F")
    # q_lcb (0.30) below the all-in cost (0.55 NO ask) -> negative robust edge -> ΔU
    # no-trade. Use a buy_no with low honest q_lcb_no.
    row = _snapshot_row(yes_asks=(("0.40", "100000"),))
    proof = _proof_from_row(direction="buy_no", row=row, token_id="no-1",
                            q_posterior=0.30, q_lcb_5pct=0.05, bin_obj=bin_x)
    stake, price = era._robust_marginal_utility_stake_and_price(
        family_key="fam", selected_proof=proof, all_proofs=(proof,),
        extra_exposure_by_bin_id={}, bankroll_usd=10000.0, kelly_multiplier=1.0,
    )
    assert stake == 0.0
    assert price is None


def test_qkernel_execution_certificate_bounds_submit_sizing():
    """A qkernel-selected proof sizes from guarded execution economics, not proof q_lcb.

    The qkernel bridge preserves q_posterior/q_lcb_5pct as receipt probability fields.
    Submit must therefore consume the separate qkernel execution certificate; otherwise a
    guarded selection can be resized from the stale unguarded proof q_lcb.
    """
    from src.types.market import Bin

    bin_x = Bin(low=60.0, high=61.0, unit="F", label="60-61F")
    row = _snapshot_row(yes_asks=(("0.20", "1000000"),))
    unguarded = _proof_from_row(
        direction="buy_yes",
        row=row,
        token_id="yes-1",
        q_posterior=0.90,
        q_lcb_5pct=0.90,
        bin_obj=bin_x,
    )
    guarded = replace(
        unguarded,
        q_source="qkernel_spine",
        qkernel_execution_economics={
            "source": "qkernel_spine",
            "candidate_id": f"YES:{era._candidate_bin_id(unguarded)}:DIRECT_YES",
            "route_id": f"DIRECT_YES:{era._candidate_bin_id(unguarded)}@proof",
            "side": "YES",
            "bin_id": era._candidate_bin_id(unguarded),
            "payoff_q_lcb": 0.30,
            "edge_lcb": 0.10,
            "point_ev": 0.70,
            "delta_u_at_min": 0.01,
            "optimal_stake_usd": "6.25",
            "optimal_delta_u": 0.02,
            "q_dot_payoff": 0.90,
            "cost": 0.20,
            "false_edge_rate": 0.02,
            "q_lcb_guard_basis": "OOF_WILSON_95",
            "direction_law_ok": True,
            "coherence_allows": True,
        },
    )

    unguarded_stake, _ = era._robust_marginal_utility_stake_and_price(
        family_key="fam",
        selected_proof=unguarded,
        all_proofs=(unguarded,),
        extra_exposure_by_bin_id={},
        bankroll_usd=10000.0,
        kelly_multiplier=1.0,
    )
    guarded_stake, guarded_price = era._robust_marginal_utility_stake_and_price(
        family_key="fam",
        selected_proof=guarded,
        all_proofs=(guarded,),
        extra_exposure_by_bin_id={},
        bankroll_usd=10000.0,
        kelly_multiplier=1.0,
    )

    assert unguarded_stake > 100.0
    assert guarded_stake == pytest.approx(6.25)
    assert guarded_price is not None
    assert guarded_price.value == pytest.approx(0.20)


def test_qkernel_execution_certificate_not_capped_by_receipt_probability_lcb():
    """Qkernel submit sizing uses the selected execution cert, not stale receipt q_lcb.

    ``q_lcb_5pct`` is preserved on qkernel proofs for receipt-facing probability
    provenance. Once the proof carries the qkernel selection stamp and a valid
    route/side/bin-bound execution certificate, submit sizing must not apply the
    old proof lower bound a second time.
    """

    from src.types.market import Bin

    bin_x = Bin(low=60.0, high=61.0, unit="F", label="60-61F")
    row = _snapshot_row(yes_asks=(("0.20", "1000000"),))
    base = _proof_from_row(
        direction="buy_yes",
        row=row,
        token_id="yes-1",
        q_posterior=0.90,
        q_lcb_5pct=0.01,
        bin_obj=bin_x,
    )
    guarded = replace(
        base,
        selection_authority_applied="qkernel_spine",
        qkernel_execution_economics={
            "source": "qkernel_spine",
            "candidate_id": f"YES:{era._candidate_bin_id(base)}:DIRECT_YES",
            "route_id": f"DIRECT_YES:{era._candidate_bin_id(base)}@proof",
            "side": "YES",
            "bin_id": era._candidate_bin_id(base),
            "payoff_q_lcb": 0.30,
            "edge_lcb": 0.10,
            "point_ev": 0.70,
            "delta_u_at_min": 0.01,
            "optimal_stake_usd": "6.25",
            "optimal_delta_u": 0.02,
            "q_dot_payoff": 0.90,
            "cost": 0.20,
            "false_edge_rate": 0.02,
            "q_lcb_guard_basis": "OOF_WILSON_95",
            "direction_law_ok": True,
            "coherence_allows": True,
        },
    )

    guarded_stake, guarded_price = era._robust_marginal_utility_stake_and_price(
        family_key="fam",
        selected_proof=guarded,
        all_proofs=(guarded,),
        extra_exposure_by_bin_id={},
        bankroll_usd=10000.0,
        kelly_multiplier=1.0,
    )

    assert guarded_stake == pytest.approx(6.25)
    assert guarded_price is not None
    assert guarded_price.value == pytest.approx(0.20)


@pytest.mark.parametrize(
    "certificate",
    [
        None,
        ["not", "a", "mapping"],
        {"source": "qkernel_spine", "payoff_q_lcb": 0.30},
        {
            "source": "qkernel_spine",
            "candidate_id": "YES:bin-1:DIRECT_YES",
            "route_id": "DIRECT_NO:bin-1@proof",
            "payoff_q_lcb": 0.30,
            "edge_lcb": 0.10,
            "delta_u_at_min": 0.01,
            "optimal_stake_usd": "6.25",
            "optimal_delta_u": 0.02,
            "cost": 0.20,
            "side": "YES",
        },
    ],
)
def test_qkernel_missing_or_malformed_certificate_fails_closed_before_legacy_scorer(
    monkeypatch, certificate
):
    """qkernel source without a valid guarded cert cannot fall back to proof-qLCB sizing."""
    from src.types.market import Bin

    def _legacy_scorer_must_not_run(*args, **kwargs):  # noqa: ARG001
        raise AssertionError("qkernel proof fell through to legacy robust scorer")

    monkeypatch.setattr(
        era,
        "_score_family_candidates_by_robust_marginal_utility",
        _legacy_scorer_must_not_run,
    )

    bin_x = Bin(low=60.0, high=61.0, unit="F", label="60-61F")
    row = _snapshot_row(yes_asks=(("0.20", "1000000"),))
    proof = replace(
        _proof_from_row(
            direction="buy_yes",
            row=row,
            token_id="yes-1",
            q_posterior=0.90,
            q_lcb_5pct=0.90,
            bin_obj=bin_x,
        ),
        q_source="qkernel_spine",
        qkernel_execution_economics=certificate,
    )

    stake, price = era._robust_marginal_utility_stake_and_price(
        family_key="fam",
        selected_proof=proof,
        all_proofs=(proof,),
        extra_exposure_by_bin_id={},
        bankroll_usd=10000.0,
        kelly_multiplier=1.0,
    )

    assert stake == 0.0
    assert price is None
