# Created: 2026-09-30
# Last reused/audited: 2026-09-30
# Authority basis: operator approval 2026-09-30 ("执行1但是只有在小资金的时候才能执行"):
#   one minimum lot while capital keeps the fractional Kelly target below it.
"""Small-capital minimum-lot admission: one predicate, both BUY sizing paths.

``small_capital_minimum_lot_admits`` (h < κT < L and h + L <= T) is the only
relaxation of ``FRACTIONAL_KELLY_TARGET_BELOW_MINIMUM_LOT``. The single-order
sizer and the family joint planner both call it; every other admission law
(EV, log-wealth, price band, cash, allocator cap, joint fractional budget)
still binds the lot.
"""

from __future__ import annotations

import inspect
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace

import numpy as np
import pytest

import src.solve.solver as S
from tests.solve.test_solver_properties import (
    ALPHA,
    BookLevel,
    _DECISION_AT,
    _GLOBAL_PROBABILITY_WITNESSES,
    _current_maker_witness,
    _global_candidate,
    _global_curve,
    _global_probability_witness,
    _global_score,
    _global_select,
)

KAPPA = Decimal("0.125")


# --------------------------------------------------------------------------
# Live-shaped maker leg (single-order path): q 0.38, maker limit 0.30, lot 5,
# W $32 -> full Kelly 12.19 shares, 1/8 -> 1.52 shares.
# --------------------------------------------------------------------------


def _maker_leg(*, q=0.38, min_order="5", cid="live-maker"):
    seed = _global_candidate(
        candidate_id=cid,
        family=f"{cid}-family",
        side="YES",
        q=q,
        levels=(("0.31", "100"),),
        min_order=min_order,
    )
    native = SimpleNamespace(
        no_trade_reason=None,
        executable_cost_curve=seed.executable_cost_curve,
        family_key=seed.family_key,
        bin_id=seed.bin_id,
        condition_id=seed.condition_id,
        side="YES",
        token_id=seed.token_id,
        hypothesis_id=f"{cid}-hypothesis",
    )
    bids = (BookLevel(price=Decimal("0.299"), size=Decimal("100")),)
    common = dict(
        probability_witness=_global_probability_witness(seed),
        ledger_snapshot_id=seed.ledger_snapshot_id,
        book_captured_at_utc=seed.book_captured_at_utc,
        neg_risk=False,
        native_bid_levels=bids,
        include_maker=True,
        asset_epoch_identity=f"{cid}-epoch",
        current_token_shares=Decimal("0"),
    )
    placeholder = SimpleNamespace(
        fill_probability=1.0,
        fill_probability_source="placeholder",
        rest_deadline_minutes=20.0,
        witness_identity="placeholder",
    )
    _taker, provisional = S.global_candidates_from_native(
        native, maker_fill_witness=placeholder, **common
    )
    price = provisional.proposal_cost_curve.levels[0].price
    witness = _current_maker_witness(
        provisional,
        proposal=provisional.proposal_cost_curve,
        asset_epoch=f"{cid}-epoch",
        outcomes=(S.MakerFillOutcome(Decimal("1"), Decimal("1"), -price),),
    )
    taker, maker = S.global_candidates_from_native(
        native, maker_fill_witness=witness, **common
    )
    assert maker.execution_mode == "MAKER_REST"
    assert maker.economic_cost_curve.levels[0].price == Decimal("0.300")
    return taker, maker


def _score_mean(candidate, *, q, wealth="32", cash="15.24", held="0", win=None):
    return S._score_global_single_order_buy_expected(
        candidate,
        payoff_probability_mean=q,
        sample_count=400,
        band_alpha=ALPHA,
        wealth_floor_usd=Decimal(wealth),
        wealth_ceiling_usd=Decimal(win if win is not None else wealth),
        spendable_cash_usd=Decimal(cash),
        capital_limit_usd=Decimal(cash),
        fractional_kelly_multiplier=KAPPA,
        current_token_shares=Decimal(held),
    )


def test_live_shaped_maker_leg_is_admitted_at_exactly_one_lot():
    _taker, maker = _maker_leg()
    decision = _score_mean(maker, q=0.38)

    assert decision.candidate is maker
    assert decision.buy_sizing_mode == "SMALL_CAPITAL_MINIMUM_LOT"
    assert decision.shares == Decimal("5")
    assert decision.full_kelly_target_shares == pytest.approx(Decimal("12.19"), abs=Decimal("0.01"))
    assert decision.fractional_kelly_target_shares == pytest.approx(Decimal("1.52"), abs=Decimal("0.01"))
    assert decision.buy_minimum_marketable_repair is None
    terminal = decision.expected_terminal_wealth
    assert terminal.expected_ev_usd == pytest.approx(0.38 * 5 - 1.5)
    assert terminal.expected_delta_log_wealth > 0.0


def test_live_shaped_maker_leg_wins_the_auction_at_one_lot():
    taker, maker = _maker_leg()
    decision = _global_select(
        (taker, maker),
        floor="32",
        ceiling="32",
        cash="15.24",
        cap="15.24",
        fractional_kelly_multiplier="0.125",
    )

    assert decision.candidate is maker
    assert decision.shares == Decimal("5")
    assert decision.buy_sizing_mode == "SMALL_CAPITAL_MINIMUM_LOT"
    assert S._positive_common_expected_growth(
        decision.expected_growth,
        capital_lock_hours=decision.capital_lock_hours,
    )
    selected = [e for e in decision.candidate_evaluations if e.status == "SELECTED"]
    assert [e.buy_sizing_mode for e in selected] == ["SMALL_CAPITAL_MINIMUM_LOT"]


def test_large_capital_uses_normal_fractional_sizing_not_the_lot():
    _taker, maker = _maker_leg()
    decision = _score_mean(maker, q=0.38, wealth="400", cash="400")

    assert decision.candidate is maker
    assert decision.fractional_kelly_target_shares >= Decimal("5")
    assert decision.buy_sizing_mode == "FRACTIONAL_TARGET"
    assert decision.shares <= decision.fractional_kelly_target_shares


def test_lot_is_refused_when_it_exceeds_full_kelly():
    # q 0.33 at 0.30: full Kelly 32*(0.33/0.3 - 0.67/0.7) = 4.57 < 5.
    _taker, maker = _maker_leg(q=0.33, cid="lot-above-full")
    decision = _score_mean(maker, q=0.33)

    assert decision.candidate is None
    assert decision.no_trade_reason == "FRACTIONAL_KELLY_TARGET_BELOW_MINIMUM_LOT"
    rejection = decision.buy_rejection_economics
    assert rejection.full_kelly_target_shares < Decimal("5")


def test_lot_is_refused_when_holding_plus_lot_exceeds_full_kelly():
    # q 0.3342 with no holding: additional full Kelly 5.2 >= 5 -> admitted.
    _taker, maker = _maker_leg(q=0.3342, cid="holding-bound")
    fresh = _score_mean(maker, q=0.3342)
    assert fresh.buy_sizing_mode == "SMALL_CAPITAL_MINIMUM_LOT"
    assert fresh.shares == Decimal("5")

    # Holding 0.5 (paid on the win branch): h < κT still, but h + L > T.
    held = _score_mean(maker, q=0.3342, held="0.5", win="32.5")
    assert held.candidate is None
    assert held.no_trade_reason == "FRACTIONAL_KELLY_TARGET_BELOW_MINIMUM_LOT"
    rejection = held.buy_rejection_economics
    assert Decimal("0.5") < rejection.fractional_kelly_target_shares
    assert Decimal("0.5") + Decimal("5") > rejection.full_kelly_target_shares


def test_lot_is_refused_when_ev_after_fee_is_not_positive():
    candidate = _global_candidate(
        candidate_id="fee-kills-lot",
        family="fee-kills-lot",
        side="YES",
        q=0.30,
        levels=(("0.20", "1000"),),
        fee="0.7",  # all-in 0.20 + 0.7*0.16 = 0.312 > q
    )
    decision = _global_score(
        candidate, floor="32", ceiling="32", cash="15", cap="15", multiplier="0.125"
    )

    assert decision.candidate is None
    assert decision.no_trade_reason == "NON_POSITIVE_ROBUST_OBJECTIVE"


def test_lot_is_refused_when_its_fill_leaves_the_price_band():
    # $1 taker lot is 10.01 shares: 10 at 0.04 (outside [0.05, 0.95]) + 0.01
    # at 0.30. Full Kelly at the 0.30 limit is 12.19 >= lot, κT = 1.52.
    candidate = _global_candidate(
        candidate_id="band-bound-lot",
        family="band-bound-lot",
        side="YES",
        q=0.38,
        levels=(("0.04", "10"), ("0.30", "1000")),
    )
    decision = _global_score(
        candidate, floor="32", ceiling="32", cash="15", cap="15", multiplier="0.125"
    )

    assert decision.candidate is None
    assert decision.no_trade_reason == "FRACTIONAL_KELLY_TARGET_BELOW_MINIMUM_LOT"


@pytest.mark.parametrize("cap,admitted", [("1.20", False), ("1.30", True)])
def test_lot_worst_limit_spend_must_fit_the_allocator_cap(cap, admitted):
    # Taker $1 lot is 4.10 shares (4 @ 0.20 + 0.10 @ 0.30; notional at the
    # 0.30 limit). Its cost 0.83 fits a $1.20 cap; its worst-limit spend
    # 4.10 * 0.30 = 1.23 does not.
    candidate = _global_candidate(
        candidate_id=f"spend-bound-{cap}",
        family=f"spend-bound-{cap}",
        side="YES",
        q=0.38,
        levels=(("0.20", "4"), ("0.30", "1000")),
    )
    decision = _global_score(
        candidate, floor="32", ceiling="32", cash="15", cap=cap, multiplier="0.125"
    )

    if admitted:
        assert decision.buy_sizing_mode == "SMALL_CAPITAL_MINIMUM_LOT"
        assert decision.shares == Decimal("4.10")
        assert decision.max_spend_usd == Decimal("1.2300")
    else:
        assert decision.candidate is None


@pytest.mark.parametrize("cash,admitted", [("1.01", False), ("1.03", True)])
def test_grid_rounded_lot_must_fit_cash_capacity(cash, admitted):
    # At 0.051 the $1 minimum is 19.61 shares but the venue amount grid makes
    # the smallest legal lot 20.00. $1.01 buys 19.8 shares: past the raw
    # minimum (so not DEPTH_INFEASIBLE) yet short of the legal lot.
    candidate = _global_candidate(
        candidate_id=f"grid-lot-{cash}",
        family=f"grid-lot-{cash}",
        side="YES",
        q=0.9,
        levels=(("0.051", "1000"),),
    )
    assert S._single_order_legal_minimum_lot(candidate) == Decimal("20.00")
    decision = _global_score(
        candidate, floor="5", ceiling="5", cash=cash, cap=cash, multiplier="0.125"
    )

    if admitted:
        assert decision.buy_sizing_mode == "SMALL_CAPITAL_MINIMUM_LOT"
        assert decision.shares == Decimal("20.00")
        assert decision.max_spend_usd <= Decimal(cash)
    else:
        assert decision.candidate is None
        assert decision.no_trade_reason == "FRACTIONAL_KELLY_TARGET_BELOW_MINIMUM_LOT"


def test_lot_is_refused_when_cash_cannot_pay_it():
    _taker, maker = _maker_leg(cid="cash-bound")
    decision = _score_mean(maker, q=0.38, cash="1.49")

    assert decision.candidate is None
    assert decision.no_trade_reason == "DEPTH_INFEASIBLE"


def test_one_lot_per_token_then_the_target_is_reached():
    _taker, maker = _maker_leg(cid="no-ratchet")
    first = _score_mean(maker, q=0.38)
    assert first.shares == Decimal("5")

    cost = first.cost_usd
    second = _score_mean(
        maker,
        q=0.38,
        wealth=str(Decimal("32") - cost),
        cash=str(Decimal("15.24") - cost),
        held="5",
        win=str(Decimal("32") - cost + 5),
    )
    assert second.candidate is None
    assert second.no_trade_reason == "FRACTIONAL_KELLY_TARGET_REACHED"


# --------------------------------------------------------------------------
# Family joint planner (TAKER legs only): ask 0.20 -> $1 notional lot = 5
# shares; q 0.26, W $32 -> full Kelly 12.0, 1/8 -> 1.5.
# --------------------------------------------------------------------------


def _joint_family(*, q, price="0.20", depth="1000", family="joint-small-capital"):
    captured_at = _DECISION_AT - timedelta(milliseconds=100)
    bindings = (
        S.OutcomeTokenBinding(
            bin_id="a", condition_id=f"{family}-a", yes_token_id=f"{family}-yes-a", no_token_id=f"{family}-no-a",
        ),
        S.OutcomeTokenBinding(
            bin_id="b", condition_id=f"{family}-b", yes_token_id=f"{family}-yes-b", no_token_id=f"{family}-no-b",
        ),
    )
    point = np.array([q, 1.0 - q])
    fields = {
        "family_key": family,
        "bindings": bindings,
        "q_version": f"q-{family}",
        "resolution_identity": f"resolution-{family}",
        "topology_identity": f"topology-{family}",
        "posterior_identity_hash": f"posterior-{family}",
        "source_truth_identity": f"source-{family}",
        "authority_certificate_hash": f"certificate-{family}",
        "band_alpha": ALPHA,
        "band_basis": "joint_q_band_samples",
        "yes_point_q": point,
        "yes_q_samples": np.tile(point, (400, 1)),
        "captured_at_utc": captured_at,
    }
    witness = S.JointOutcomeProbabilityWitness(
        **fields,
        max_age=timedelta(seconds=1),
        witness_identity=S.joint_probability_witness_identity(**fields),
    )
    _GLOBAL_PROBABILITY_WITNESSES[witness.witness_identity] = witness
    token = f"{family}-yes-a"
    curve = _global_curve(side="YES", token=token, levels=((price, depth),), min_order="5")
    candidate = S.GlobalSingleOrderCandidate(
        candidate_id=f"{family}-a-YES",
        family_key=family,
        bin_id="a",
        condition_id=f"{family}-a",
        side="YES",
        token_id=token,
        probability_witness_identity=witness.witness_identity,
        book_snapshot_id=curve.snapshot_id,
        book_captured_at_utc=captured_at,
        execution_curve_identity=S.executable_curve_identity(curve),
        ledger_snapshot_id="ledger-current",
        executable_cost_curve=curve,
        resolution_identity=witness.resolution_identity,
        neg_risk=False,
        native_bid_levels=(BookLevel(price=Decimal("0.19"), size=Decimal("1000")),),
    )
    return candidate, witness


def _joint_endowment(witness, *, wealth="32", cash="15.24", held=()):
    return S.FamilyPortfolioEndowment(
        family_key=witness.family_key,
        payout_by_bin_usd=tuple((bin_id, Decimal("0")) for bin_id in witness.bin_ids),
        current_token_shares=tuple(held),
        wealth_floor_usd=Decimal(wealth),
        spendable_cash_usd=Decimal(cash),
        portfolio_capital_usd=Decimal(wealth),
        committed_capital_usd=Decimal("0"),
        ledger_snapshot_id="ledger-current",
    )


def _plan(candidate, witness, **endowment):
    return S.plan_family_joint_buy_targets(
        (candidate,),
        probability_witness=witness,
        endowment=_joint_endowment(witness, **endowment),
        capital_limit_by_candidate={candidate.candidate_id: Decimal(endowment.get("cash", "15.24"))},
        fractional_kelly_multiplier=KAPPA,
    )


def test_joint_planner_admits_the_live_shaped_leg_at_exactly_one_lot():
    candidate, witness = _joint_family(q=0.26)
    plan = _plan(candidate, witness)

    assert plan.no_trade_reason is None
    (target,) = plan.targets
    assert target.shares == Decimal("5.00")
    assert target.full_kelly_target_shares >= Decimal("5")
    assert target.fractional_kelly_target_shares < Decimal("5")
    assert S.small_capital_minimum_lot_admits(
        current_token_shares=target.current_token_shares,
        full_kelly_target_shares=target.full_kelly_target_shares,
        fractional_kelly_target_shares=target.fractional_kelly_target_shares,
        minimum_lot_shares=target.shares,
    )


def test_joint_selection_orders_exactly_one_lot():
    candidate, witness = _joint_family(q=0.26, family="joint-select-small")
    decision = _global_select(
        (candidate,),
        probability_witnesses={witness.family_key: witness},
        floor="32",
        ceiling="32",
        cash="15.24",
        cap="15.24",
        fractional_kelly_multiplier="0.125",
        family_portfolio_endowment_resolver=lambda _family: _joint_endowment(witness),
    )

    assert decision.candidate is candidate
    assert decision.shares == Decimal("5.00")
    assert decision.buy_sizing_mode == "SMALL_CAPITAL_MINIMUM_LOT"


def test_joint_planner_refuses_a_lot_above_full_kelly():
    # q 0.22 at 0.20: full Kelly 32*(1.1 - 0.975) = 4.0 < 5.
    candidate, witness = _joint_family(q=0.22, family="joint-lot-above-full")
    plan = _plan(candidate, witness)

    assert plan.targets == ()
    assert plan.no_trade_reason == "FAMILY_JOINT_NO_POSITIVE_TARGET"


def test_joint_planner_refuses_a_lot_outside_the_price_band():
    # 0.95 < ask 0.96: the planner's tranche walk stops at the band ceiling.
    candidate, witness = _joint_family(q=0.99, price="0.96", family="joint-band")
    plan = _plan(candidate, witness, wealth="2", cash="2")

    assert plan.targets == ()


def test_joint_leg_below_the_band_floor_never_reaches_the_planner():
    # The lower band edge is enforced before sizing: a taker whose best ask is
    # below 0.05 is ineligible, so no joint lot can be minted from it.
    candidate, witness = _joint_family(q=0.38, family="joint-band-floor")
    curve = _global_curve(
        side="YES",
        token=candidate.token_id,
        levels=(("0.04", "10"), ("0.30", "1000")),
        min_order="5",
    )
    candidate = replace(
        candidate,
        executable_cost_curve=curve,
        book_snapshot_id=curve.snapshot_id,
        execution_curve_identity=S.executable_curve_identity(curve),
    )
    assert candidate.eligibility_reason == "LIVE_UNIT_PRICE_OUT_OF_BOUNDS"
    decision = _global_select(
        (candidate,),
        probability_witnesses={witness.family_key: witness},
        floor="32",
        ceiling="32",
        cash="15.24",
        cap="15.24",
        fractional_kelly_multiplier="0.125",
        family_portfolio_endowment_resolver=lambda _family: _joint_endowment(witness),
    )
    assert decision.candidate is None


def test_joint_planner_lot_respects_each_candidates_own_cap():
    # A cap of exactly the lot cost ($1.00 = 5 shares at 0.20) makes the lot
    # the optimizer's active bound; it must be admitted deterministically.
    candidate, witness = _joint_family(q=0.26, family="joint-own-cap")
    endowment = _joint_endowment(witness)
    admitted = S.plan_family_joint_buy_targets(
        (candidate,),
        probability_witness=witness,
        endowment=endowment,
        capital_limit_by_candidate={candidate.candidate_id: Decimal("1.00")},
        fractional_kelly_multiplier=KAPPA,
    )
    refused = S.plan_family_joint_buy_targets(
        (candidate,),
        probability_witness=witness,
        endowment=endowment,
        capital_limit_by_candidate={candidate.candidate_id: Decimal("0.99")},
        fractional_kelly_multiplier=KAPPA,
    )

    assert [t.shares for t in admitted.targets] == [Decimal("5.00")]
    assert refused.targets == ()


def test_joint_planner_snaps_an_active_optimizer_bound_onto_its_exact_cap(monkeypatch):
    # SLSQP reports an active 5-share bound as 4.999999999999998 on some numpy
    # builds; the at-most grid floor must not turn that into 4.95.
    candidate, witness = _joint_family(q=0.26, family="joint-bound-snap")
    original = S._ru_cvar_optimum

    def just_below_bound(**kwargs):
        _direct, utility, iterations = original(**kwargs)
        return np.nextafter(kwargs["caps"], 0.0), utility, iterations

    monkeypatch.setattr(S, "_ru_cvar_optimum", just_below_bound)
    plan = S.plan_family_joint_buy_targets(
        (candidate,),
        probability_witness=witness,
        endowment=_joint_endowment(witness),
        capital_limit_by_candidate={candidate.candidate_id: Decimal("1.00")},
        fractional_kelly_multiplier=KAPPA,
    )

    (target,) = plan.targets
    assert target.full_kelly_target_shares == Decimal("5.00")
    assert target.shares == Decimal("5.00")


def test_joint_planner_uses_normal_sizing_with_large_capital():
    candidate, witness = _joint_family(q=0.26, family="joint-large")
    plan = _plan(candidate, witness, wealth="400", cash="400")

    (target,) = plan.targets
    assert target.fractional_kelly_target_shares >= Decimal("5")
    assert target.shares <= target.fractional_kelly_target_shares
    assert target.shares > Decimal("5")


# --------------------------------------------------------------------------
# The predicate and its single ownership.
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "held,full,fractional,lot,admitted",
    [
        ("0", "12", "1.5", "5", True),       # live shape
        ("0", "4.57", "0.57", "5", False),   # lot above full Kelly
        ("0.5", "5.2", "0.65", "5", False),  # holding + lot above full Kelly
        ("0", "40", "5", "5", False),        # capital large: κT reaches the lot
        ("0", "48", "6", "5", False),        # κT above the lot: normal sizing
        ("2", "12", "1.5", "5", False),      # target already reached
        ("0", "5", "0.625", "5", True),      # lot exactly at full Kelly
        ("0", "12", "0", "5", False),        # no positive target
        ("0", "12", "1.5", "0", False),      # no lot
    ],
)
def test_predicate_table(held, full, fractional, lot, admitted):
    assert S.small_capital_minimum_lot_admits(
        current_token_shares=Decimal(held),
        full_kelly_target_shares=Decimal(full),
        fractional_kelly_target_shares=Decimal(fractional),
        minimum_lot_shares=Decimal(lot),
    ) is admitted


def test_both_sizing_paths_and_both_validators_call_the_one_predicate():
    name = "small_capital_minimum_lot_admits("
    for owner in (
        S._single_order_small_capital_lot,
        S.plan_family_joint_buy_targets,
        S.GlobalSingleOrderDecision.__post_init__,
        S.GlobalSingleOrderCandidateEvaluation.__post_init__,
    ):
        assert name in inspect.getsource(owner), owner.__qualname__
    solver_source = inspect.getsource(S)
    assert solver_source.count("def small_capital_minimum_lot_admits(") == 1


def test_disabling_the_predicate_disables_both_paths(monkeypatch):
    monkeypatch.setattr(S, "small_capital_minimum_lot_admits", lambda **_kwargs: False)

    _taker, maker = _maker_leg(cid="predicate-off")
    single = _score_mean(maker, q=0.38)
    assert single.candidate is None
    assert single.no_trade_reason == "FRACTIONAL_KELLY_TARGET_BELOW_MINIMUM_LOT"

    candidate, witness = _joint_family(q=0.26, family="predicate-off-joint")
    assert _plan(candidate, witness).targets == ()


def test_evaluation_rows_refuse_a_lot_other_than_the_venue_minimum():
    taker, maker = _maker_leg(cid="forged-eval")
    bound = _global_select(
        (taker, maker),
        floor="32",
        ceiling="32",
        cash="15.24",
        cap="15.24",
        fractional_kelly_multiplier="0.125",
    )
    assert bound.candidate is maker and bound.shares == Decimal("5")
    # The bound winner builds a coherent row as-is.
    S._global_candidate_evaluations(
        (maker,), rejections={}, scores=(bound,), winner_id=maker.candidate_id,
    )
    # 6 shares still satisfies the predicate (h + 6 <= full Kelly 12.19), so
    # only the venue-minimum identity refuses it. The forged score skips its
    # own __post_init__, as a stale or hand-built row would.
    forged = S.GlobalSingleOrderDecision.__new__(S.GlobalSingleOrderDecision)
    for name, value in vars(bound).items():
        object.__setattr__(forged, name, value)
    object.__setattr__(forged, "shares", Decimal("6"))
    assert S.small_capital_minimum_lot_admits(
        current_token_shares=forged.current_token_shares,
        full_kelly_target_shares=forged.full_kelly_target_shares,
        fractional_kelly_target_shares=forged.fractional_kelly_target_shares,
        minimum_lot_shares=forged.shares,
    )

    with pytest.raises(ValueError, match="venue-legal minimum"):
        S._global_candidate_evaluations(
            (maker,), rejections={}, scores=(forged,), winner_id=maker.candidate_id,
        )


def test_validator_refuses_a_forged_small_capital_lot():
    _taker, maker = _maker_leg(cid="forged")
    decision = _score_mean(maker, q=0.38)
    from dataclasses import replace

    with pytest.raises(ValueError):
        replace(decision, full_kelly_target_shares=Decimal("4"))
    with pytest.raises(ValueError):
        replace(decision, fractional_kelly_target_shares=Decimal("5"))
