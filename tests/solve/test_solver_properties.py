# Created: 2026-07-03
# Last reused/audited: 2026-07-23
# Authority basis: current global auction, executable Kelly, and wealth contracts
"""Current global-auction solver properties over executable portfolio wealth."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import ROUND_FLOOR, Decimal
import math
from types import SimpleNamespace

import numpy as np
import pytest

from src.contracts.executable_cost_curve import BookLevel, ExecutableCostCurve, FeeModel
from src.contracts.execution_intent import (
    quantize_submit_shares_for_venue,
    quantize_submit_shares_for_venue_at_most,
    venue_submit_amount_precision_error,
)
from src.solve import solver as S
ALPHA = 0.05
DOM_TOL = 1e-9
_DECISION_AT = datetime(2026, 7, 10, 6, 0, tzinfo=UTC)
def _global_curve(*, side, token, levels, fee="0", min_order="0.01"):
    return ExecutableCostCurve(
        token_id=token,
        side=side,
        snapshot_id=f"book-{token}",
        book_hash=f"hash-{token}",
        levels=tuple(
            BookLevel(price=Decimal(price), size=Decimal(size))
            for price, size in levels
        ),
        fee_model=FeeModel(fee_rate=Decimal(fee)),
        min_tick=Decimal("0.001"),
        min_order_size=Decimal(min_order),
        quote_ttl=timedelta(seconds=1),
    )


_GLOBAL_PROBABILITY_WITNESSES = {}


def _global_candidate(
    *,
    candidate_id,
    family,
    side,
    q,
    levels=(("0.40", "100"),),
    fee="0",
    min_order="1",
    reason=None,
):
    token = f"token-{candidate_id}"
    condition = f"condition-{candidate_id}"
    curve = _global_curve(
        side=side,
        token=token,
        levels=levels,
        fee=fee,
        min_order=min_order,
    )
    curve_identity = S.executable_curve_identity(curve)
    resolution_identity = f"resolution-{family}"
    payoff_q_samples = np.full(400, q, dtype=np.float64)
    yes_q_samples = (
        payoff_q_samples if side == "YES" else 1.0 - payoff_q_samples
    )
    q_version = f"q-{candidate_id}"
    captured_at = _DECISION_AT - timedelta(milliseconds=100)
    candidate_binding = S.OutcomeTokenBinding(
        bin_id="bin",
        condition_id=condition,
        yes_token_id=token if side == "YES" else f"yes-{candidate_id}",
        no_token_id=token if side == "NO" else f"no-{candidate_id}",
    )
    other_binding = S.OutcomeTokenBinding(
        bin_id="other",
        condition_id=f"other-condition-{candidate_id}",
        yes_token_id=f"other-yes-{candidate_id}",
        no_token_id=f"other-no-{candidate_id}",
    )
    bindings = (candidate_binding, other_binding)
    samples = np.column_stack((yes_q_samples, 1.0 - yes_q_samples))
    identity = S.joint_probability_witness_identity(
        family_key=family,
        bindings=bindings,
        q_version=q_version,
        resolution_identity=resolution_identity,
        topology_identity=f"topology-{candidate_id}",
        posterior_identity_hash=f"posterior-{candidate_id}",
        source_truth_identity=f"source-{candidate_id}",
        authority_certificate_hash=f"decision-certificate-{candidate_id}",
        band_alpha=ALPHA,
        band_basis="joint_q_band_samples",
        yes_point_q=np.mean(samples, axis=0),
        yes_q_samples=samples,
        captured_at_utc=captured_at,
    )
    witness = S.JointOutcomeProbabilityWitness(
        family_key=family,
        bindings=bindings,
        yes_point_q=np.mean(samples, axis=0),
        yes_q_samples=samples,
        q_version=q_version,
        resolution_identity=resolution_identity,
        topology_identity=f"topology-{candidate_id}",
        posterior_identity_hash=f"posterior-{candidate_id}",
        source_truth_identity=f"source-{candidate_id}",
        authority_certificate_hash=f"decision-certificate-{candidate_id}",
        band_alpha=ALPHA,
        band_basis="joint_q_band_samples",
        captured_at_utc=captured_at,
        max_age=timedelta(seconds=1),
        witness_identity=identity,
    )
    _GLOBAL_PROBABILITY_WITNESSES[identity] = witness
    return S.GlobalSingleOrderCandidate(
        candidate_id=candidate_id,
        family_key=family,
        bin_id="bin",
        condition_id=condition,
        side=side,
        token_id=token,
        probability_witness_identity=identity,
        book_snapshot_id=f"book-{token}",
        book_captured_at_utc=captured_at,
        execution_curve_identity=curve_identity,
        ledger_snapshot_id="ledger-current",
        executable_cost_curve=curve,
        resolution_identity=resolution_identity,
        eligibility_reason=reason,
    )


def _replace_global_q_samples(candidate, payoff_q_samples):
    payoff_q = np.ascontiguousarray(np.asarray(payoff_q_samples, dtype=np.float64))
    yes_q = payoff_q if candidate.side == "YES" else 1.0 - payoff_q
    prior = _GLOBAL_PROBABILITY_WITNESSES[candidate.probability_witness_identity]
    samples = np.column_stack((yes_q, 1.0 - yes_q))
    identity = S.joint_probability_witness_identity(
        family_key=prior.family_key,
        bindings=prior.bindings,
        q_version=prior.q_version,
        resolution_identity=prior.resolution_identity,
        topology_identity=prior.topology_identity,
        posterior_identity_hash=prior.posterior_identity_hash,
        source_truth_identity=prior.source_truth_identity,
        authority_certificate_hash=prior.authority_certificate_hash,
        band_alpha=prior.band_alpha,
        band_basis=prior.band_basis,
        yes_point_q=np.mean(samples, axis=0),
        yes_q_samples=samples,
        captured_at_utc=prior.captured_at_utc,
    )
    witness = replace(
        prior,
        yes_point_q=np.mean(samples, axis=0),
        yes_q_samples=samples,
        witness_identity=identity,
    )
    _GLOBAL_PROBABILITY_WITNESSES[identity] = witness
    return replace(candidate, probability_witness_identity=identity)


def _replace_global_point_q(candidate, payoff_q):
    prior = _GLOBAL_PROBABILITY_WITNESSES[candidate.probability_witness_identity]
    column = prior.bin_ids.index(candidate.bin_id)
    point = np.array(prior.yes_point_q, copy=True)
    yes_q = float(payoff_q) if candidate.side == "YES" else 1.0 - float(payoff_q)
    point[column] = yes_q
    other = 1 - column
    point[other] = 1.0 - yes_q
    identity = S.joint_probability_witness_identity(
        family_key=prior.family_key,
        bindings=prior.bindings,
        q_version=prior.q_version,
        resolution_identity=prior.resolution_identity,
        topology_identity=prior.topology_identity,
        posterior_identity_hash=prior.posterior_identity_hash,
        source_truth_identity=prior.source_truth_identity,
        authority_certificate_hash=prior.authority_certificate_hash,
        band_alpha=prior.band_alpha,
        band_basis=prior.band_basis,
        yes_point_q=point,
        yes_q_samples=prior.yes_q_samples,
        captured_at_utc=prior.captured_at_utc,
    )
    witness = replace(prior, yes_point_q=point, witness_identity=identity)
    _GLOBAL_PROBABILITY_WITNESSES[identity] = witness
    return replace(candidate, probability_witness_identity=identity)


def _replace_global_band_alpha(candidate, alpha):
    prior = _GLOBAL_PROBABILITY_WITNESSES[candidate.probability_witness_identity]
    identity = S.joint_probability_witness_identity(
        family_key=prior.family_key,
        bindings=prior.bindings,
        q_version=prior.q_version,
        resolution_identity=prior.resolution_identity,
        topology_identity=prior.topology_identity,
        posterior_identity_hash=prior.posterior_identity_hash,
        source_truth_identity=prior.source_truth_identity,
        authority_certificate_hash=prior.authority_certificate_hash,
        band_alpha=alpha,
        band_basis=prior.band_basis,
        yes_point_q=prior.yes_point_q,
        yes_q_samples=prior.yes_q_samples,
        captured_at_utc=prior.captured_at_utc,
    )
    witness = replace(prior, band_alpha=alpha, witness_identity=identity)
    _GLOBAL_PROBABILITY_WITNESSES[identity] = witness
    return replace(candidate, probability_witness_identity=identity)


def _replace_global_band_basis(candidate, basis):
    prior = _GLOBAL_PROBABILITY_WITNESSES[candidate.probability_witness_identity]
    identity = S.joint_probability_witness_identity(
        family_key=prior.family_key,
        bindings=prior.bindings,
        q_version=prior.q_version,
        resolution_identity=prior.resolution_identity,
        topology_identity=prior.topology_identity,
        posterior_identity_hash=prior.posterior_identity_hash,
        source_truth_identity=prior.source_truth_identity,
        authority_certificate_hash=prior.authority_certificate_hash,
        band_alpha=prior.band_alpha,
        band_basis=basis,
        yes_point_q=prior.yes_point_q,
        yes_q_samples=prior.yes_q_samples,
        captured_at_utc=prior.captured_at_utc,
    )
    witness = replace(prior, band_basis=basis, witness_identity=identity)
    _GLOBAL_PROBABILITY_WITNESSES[identity] = witness
    return replace(candidate, probability_witness_identity=identity)


def _global_probability_projection(candidate):
    probability = _GLOBAL_PROBABILITY_WITNESSES[
        candidate.probability_witness_identity
    ]
    column = probability.bin_ids.index(candidate.bin_id)
    yes_q = probability.yes_q_samples[:, column]
    return (
        yes_q if candidate.side == "YES" else 1.0 - yes_q,
        probability.band_alpha,
    )


def _global_score(
    candidate,
    *,
    floor="100",
    ceiling="100",
    cash="100",
    cap="5",
    multiplier="1",
    current_token_shares="0",
):
    q_samples, alpha = _global_probability_projection(candidate)
    return S._score_global_single_order(
        candidate,
        q_samples=q_samples,
        band_alpha=alpha,
        wealth_floor_usd=Decimal(floor),
        wealth_ceiling_usd=Decimal(ceiling),
        spendable_cash_usd=Decimal(cash),
        capital_limit_usd=Decimal(cap),
        fractional_kelly_multiplier=Decimal(multiplier),
        current_token_shares=Decimal(current_token_shares),
    )


def _global_exact_oracle(
    candidate,
    *,
    floor="100",
    ceiling="100",
    cap="5",
    q_samples=None,
    alpha=None,
):
    projected_q, projected_alpha = _global_probability_projection(candidate)
    q_samples = (
        projected_q
        if q_samples is None
        else np.asarray(q_samples, dtype=float)
    )
    alpha = projected_alpha if alpha is None else float(alpha)
    max_shares = S._single_order_max_shares(
        candidate.executable_cost_curve,
        spend_limit_usd=min(Decimal(floor) * Decimal("0.999999999"), Decimal(cap)),
    )
    min_shares = S._single_order_min_marketable_shares(
        candidate.executable_cost_curve
    )
    if min_shares is None:
        return None
    best = None
    shares = min_shares
    while shares <= max_shares:
        limit_price, _, _ = S._single_order_execution_boundary(candidate, shares)
        direction = "buy_yes" if candidate.side == "YES" else "buy_no"
        if venue_submit_amount_precision_error(
            direction=direction,
            final_limit_price=limit_price,
            submitted_shares=shares,
            order_type="FOK",
            tick_size=candidate.executable_cost_curve.min_tick,
        ) is not None:
            shares += Decimal("0.01")
            continue
        metrics = S._single_order_metrics(
            candidate,
            q_samples=q_samples,
            shares=shares,
            wealth_floor_usd=Decimal(floor),
            wealth_ceiling_usd=Decimal(ceiling),
            alpha=alpha,
        )
        if best is None or metrics[0] > best[0]:
            best = (*metrics, shares)
        shares += Decimal("0.01")
    return best


def _global_sell_candidate(
    *,
    candidate_id,
    family,
    side,
    held_q,
    bids,
    shares="10",
    fee="0",
    probability_functional="LOWER_CVAR_PARAMETER_DRAWS",
    exit_authority_status="not_applicable",
    exit_authority_reason="non_day0_family",
):
    probability_seed = _global_candidate(
        candidate_id=f"{candidate_id}-q",
        family=family,
        side=side,
        q=held_q,
    )
    witness = _global_probability_witness(probability_seed)
    curve = S.ExecutableSellCurve(
        token_id=probability_seed.token_id,
        side=side,
        snapshot_id=f"sell-book-{candidate_id}",
        book_hash=f"sell-hash-{candidate_id}",
        levels=tuple(
            BookLevel(price=Decimal(price), size=Decimal(size))
            for price, size in bids
        ),
        fee_model=FeeModel(fee_rate=Decimal(fee)),
        min_tick=Decimal("0.001"),
        min_order_size=Decimal("1"),
        quote_ttl=timedelta(seconds=1),
    )
    return S.GlobalSingleOrderSellCandidate(
        candidate_id=candidate_id,
        family_key=family,
        bin_id=probability_seed.bin_id,
        condition_id=probability_seed.condition_id,
        side=side,
        token_id=probability_seed.token_id,
        position_id=f"position-{candidate_id}",
        held_shares=Decimal(shares),
        probability_witness_identity=witness.witness_identity,
        book_snapshot_id=curve.snapshot_id,
        book_captured_at_utc=probability_seed.book_captured_at_utc,
        execution_curve_identity=S.executable_curve_identity(curve),
        ledger_snapshot_id="ledger-current",
        executable_sell_curve=curve,
        resolution_identity=probability_seed.resolution_identity,
        probability_functional=probability_functional,
        exit_authority_status=exit_authority_status,
        exit_authority_reason=exit_authority_reason,
    )


@pytest.mark.parametrize("side", ("YES", "NO"))
@pytest.mark.parametrize(
    ("floor", "ceiling", "q_samples", "alpha"),
    (
        ("83.25", "127.40", np.linspace(0.51, 0.91, 80), 0.10),
        ("250.75", "401.20", np.linspace(0.62, 0.84, 41), 0.20),
        ("91.10", "91.10", np.array([0.58] * 20 + [0.86] * 60), 0.25),
    ),
)
def test_global_single_order_closed_form_matches_exact_venue_grid_oracle(
    side, floor, ceiling, q_samples, alpha
):
    candidate = _global_candidate(
        candidate_id=f"closed-form-{side}-{floor}",
        family=f"closed-form-{side}-{floor}",
        side=side,
        q=0.70,
        levels=(("0.19", "1.37"), ("0.34", "4.11"), ("0.57", "20")),
        fee="0.035",
    )
    oracle = _global_exact_oracle(
        candidate,
        floor=floor,
        ceiling=ceiling,
        cap="7.25",
        q_samples=q_samples,
        alpha=alpha,
    )
    score = S._score_global_single_order(
        candidate,
        q_samples=q_samples,
        band_alpha=alpha,
        wealth_floor_usd=Decimal(floor),
        wealth_ceiling_usd=Decimal(ceiling),
        spendable_cash_usd=Decimal("100"),
        capital_limit_usd=Decimal("7.25"),
    )

    assert oracle is not None
    assert score.candidate is not None
    assert score.shares == oracle[4]
    assert score.cost_usd == oracle[3]
    assert abs(score.robust_delta_log_wealth - oracle[0]) < 1e-12


def test_fractional_kelly_targets_final_holding_instead_of_reallocating_each_epoch():
    candidate = _global_candidate(
        candidate_id="cumulative-fractional-kelly",
        family="cumulative-fractional-kelly",
        side="YES",
        q=0.65,
        levels=(("0.40", "1000"),),
    )

    first = _global_score(
        candidate,
        cap="100",
        multiplier="0.25",
    )
    assert first.candidate is candidate
    assert first.current_token_shares == 0
    assert first.full_kelly_target_shares > first.fractional_kelly_target_shares
    assert first.shares <= first.fractional_kelly_target_shares

    cash_after = Decimal("100") - first.cost_usd
    second = _global_score(
        candidate,
        floor=str(cash_after),
        ceiling=str(cash_after + first.shares),
        cash=str(cash_after),
        cap="100",
        multiplier="0.25",
        current_token_shares=str(first.shares),
    )

    assert second.candidate is None
    assert second.shares == 0
    assert second.rejection_reasons[candidate.candidate_id] == (
        "FRACTIONAL_KELLY_TARGET_BELOW_MINIMUM_LOT"
    )


def test_fractional_kelly_rejects_a_positive_subminimum_target():
    candidate = _global_candidate(
        candidate_id="fractional-below-minimum",
        family="fractional-below-minimum",
        side="YES",
        q=0.51,
        levels=(("0.49", "1000"),),
        min_order="1",
    )

    decision = _global_score(
        candidate,
        cap="100",
        multiplier="0.03125",
    )

    assert decision.candidate is None
    assert decision.shares == 0
    assert decision.buy_sizing_mode == "NOT_APPLICABLE"
    assert decision.buy_minimum_marketable_repair is None
    assert decision.rejection_reasons[candidate.candidate_id] == (
        "FRACTIONAL_KELLY_TARGET_BELOW_MINIMUM_LOT"
    )
    rejection = decision.buy_rejection_economics
    assert rejection is not None
    assert rejection.candidate_id == candidate.candidate_id
    assert rejection.probe_kind == "MINIMUM_MARKETABLE"
    assert rejection.probe_shares >= Decimal("1")
    assert Decimal("0") < rejection.remaining_fractional_target_shares < (
        rejection.probe_shares
    )
    assert rejection.probe_robust_delta_log_wealth > 0
    assert rejection.probe_robust_ev_usd > 0


def test_fractional_kelly_does_not_turn_7_015625_target_into_a_five_share_buy():
    candidate = _global_candidate(
        candidate_id="strict-target-below-five-share-lot",
        family="strict-target-below-five-share-lot",
        side="YES",
        q=0.65,
        levels=(("0.40", "1000"),),
        min_order="5",
    )
    common = {
        "floor": "212.7",
        "ceiling": "219.7",
        "cash": "212.7",
        "cap": "1000",
        "current_token_shares": "7",
    }
    full = _global_score(candidate, multiplier="1", **common)
    assert full.full_kelly_target_shares == Decimal("224.50")
    assert full.full_kelly_target_shares * Decimal("0.03125") == Decimal(
        "7.015625"
    )

    decision = _global_score(candidate, multiplier="0.03125", **common)

    assert decision.candidate is None
    assert decision.shares == 0
    assert decision.rejection_reasons[candidate.candidate_id] == (
        "FRACTIONAL_KELLY_TARGET_BELOW_MINIMUM_LOT"
    )


def test_subminimum_fractional_kelly_rejection_is_exactly_symmetric_for_yes_and_no():
    yes = _global_candidate(
        candidate_id="repair-yes",
        family="repair-yes",
        side="YES",
        q=0.51,
        levels=(("0.49", "1000"),),
        min_order="1",
    )
    no = _global_candidate(
        candidate_id="repair-no",
        family="repair-no",
        side="NO",
        q=0.51,
        levels=(("0.49", "1000"),),
        min_order="1",
    )

    yes_decision = _global_score(yes, cap="100", multiplier="0.03125")
    no_decision = _global_score(no, cap="100", multiplier="0.03125")

    assert yes_decision.candidate is no_decision.candidate is None
    assert yes_decision.shares == no_decision.shares == 0
    assert yes_decision.no_trade_reason == no_decision.no_trade_reason == (
        "FRACTIONAL_KELLY_TARGET_BELOW_MINIMUM_LOT"
    )


def test_nonpositive_venue_minimum_does_not_masquerade_as_sizing_rejection():
    candidate = _global_candidate(
        candidate_id="venue-minimum-destroys-edge",
        family="venue-minimum-destroys-edge",
        side="YES",
        q=0.4901,
        levels=(("0.49", "1000"),),
        min_order="1",
    )

    decision = _global_score(
        candidate,
        cap="100",
        multiplier="0.03125",
    )

    assert decision.candidate is None
    assert decision.no_trade_reason == "NON_POSITIVE_ROBUST_OBJECTIVE"
    assert decision.buy_minimum_marketable_repair is None


def test_rejected_buy_cannot_claim_a_discrete_repair_mode_without_proof():
    with pytest.raises(ValueError, match="cannot carry BUY sizing"):
        S.GlobalSingleOrderCandidateEvaluation(
            candidate_id="rejected-repair",
            family_key="rejected-repair",
            bin_id="20C",
            condition_id="condition-rejected-repair",
            side="YES",
            token_id="token-rejected-repair",
            action="BUY",
            status="REJECTED",
            rejection_reason="NON_POSITIVE_ROBUST_OBJECTIVE",
            buy_sizing_mode="MINIMUM_MARKETABLE_DISCRETE_REPAIR",
        )


def test_subminimum_target_never_emits_a_minimum_lot_repair_certificate():
    candidate = _global_candidate(
        candidate_id="forged-minimum",
        family="forged-minimum",
        side="YES",
        q=0.90,
        levels=(("0.10", "1000"),),
        min_order="10",
    )
    decision = _global_score(
        candidate,
        cap="100",
        multiplier="0.001",
    )
    assert decision.candidate is None
    assert decision.shares == 0
    assert decision.buy_minimum_marketable_repair is None
    assert decision.rejection_reasons[candidate.candidate_id] == (
        "FRACTIONAL_KELLY_TARGET_BELOW_MINIMUM_LOT"
    )


@pytest.mark.parametrize("side", ("YES", "NO"))
@pytest.mark.parametrize("constrained_budget", ("cap", "cash"))
def test_subminimum_repair_never_overrides_cash_or_cap(side, constrained_budget):
    candidate = _global_candidate(
        candidate_id=f"budget-bound-{side}-{constrained_budget}",
        family=f"budget-bound-{side}-{constrained_budget}",
        side=side,
        q=0.51,
        levels=(("0.49", "1000"),),
        min_order="1",
    )
    budgets = {constrained_budget: "0.48"}

    decision = _global_score(
        candidate,
        multiplier="0.03125",
        **budgets,
    )

    assert decision.candidate is None
    assert decision.no_trade_reason == "DEPTH_INFEASIBLE"
    assert decision.buy_minimum_marketable_repair is None


def test_fractional_order_survives_nonpositive_full_kelly_ev():
    candidate = _global_candidate(
        candidate_id="fractional-positive-full-ev-negative",
        family="fractional-positive-full-ev-negative",
        side="YES",
        q=0.65,
        levels=(("0.20", "1"), ("0.40", "4"), ("0.80", "20")),
    )

    decision = _global_score(
        candidate,
        floor="175",
        ceiling="25",
        cash="100",
        cap="100",
        multiplier="0.25",
    )

    assert decision.candidate is candidate
    assert decision.shares == Decimal("6.25")
    assert decision.cost_usd == Decimal("2.8000")
    assert decision.robust_delta_log_wealth == pytest.approx(0.0783817345)
    assert decision.robust_ev_usd == pytest.approx(1.2625)


@pytest.mark.parametrize("side", ("YES", "NO"))
def test_zero_ev_fractional_order_is_not_admitted(side):
    candidate = _global_candidate(
        candidate_id=f"fractional-zero-ev-{side}",
        family=f"fractional-zero-ev-{side}",
        side=side,
        q=0.49,
        levels=(("0.49", "100"),),
    )

    decision = _global_score(
        candidate,
        floor="175",
        ceiling="25",
        cash="100",
        cap="100",
        multiplier="0.03125",
    )

    assert decision.candidate is None
    assert decision.no_trade_reason == "NON_POSITIVE_ROBUST_OBJECTIVE"


def test_global_selector_consumes_ledger_bound_cumulative_buy_endowment():
    candidate = _global_candidate(
        candidate_id="selector-cumulative-endowment",
        family="selector-cumulative-endowment",
        side="NO",
        q=0.65,
        levels=(("0.40", "1000"),),
    )
    initial_endowment = S.CandidatePortfolioEndowment(
        loss_wealth_floor_usd=Decimal("100"),
        win_wealth_floor_usd=Decimal("100"),
        current_token_shares=Decimal("0"),
        ledger_snapshot_id="ledger-current",
    )
    first = _global_select(
        (candidate,),
        cap="100",
        fractional_kelly_multiplier="0.25",
        candidate_portfolio_endowment_resolver=lambda _: initial_endowment,
    )
    assert first.candidate is candidate

    cash_after = Decimal("100") - first.cost_usd
    held_endowment = S.CandidatePortfolioEndowment(
        loss_wealth_floor_usd=cash_after,
        win_wealth_floor_usd=cash_after + first.shares,
        current_token_shares=first.shares,
        ledger_snapshot_id="ledger-current",
    )
    updated_wealth = _global_witness(
        floor=str(cash_after),
        ceiling=str(cash_after + first.shares),
        cash=str(cash_after),
        position_hash="positions-after-first-fill",
    )
    second = _global_select(
        (candidate,),
        cap="100",
        witness=updated_wealth,
        fractional_kelly_multiplier="0.25",
        candidate_portfolio_endowment_resolver=lambda _: held_endowment,
    )

    assert second.candidate is None
    assert second.shares == 0
    assert second.rejection_reasons[candidate.candidate_id] == (
        "FRACTIONAL_KELLY_TARGET_BELOW_MINIMUM_LOT"
    )


def test_family_joint_fractional_kelly_owns_one_shared_final_vector(monkeypatch):
    family = "guangzhou-joint-kelly"
    captured_at = _DECISION_AT - timedelta(milliseconds=100)
    specs = (
        ("33", "YES", "0.087", "550"),
        ("35", "NO", "0.64", "1300.75"),
        ("36", "NO", "0.76", "1121"),
        ("other", "YES", "0.90", "100"),
    )
    bindings = tuple(
        S.OutcomeTokenBinding(
            bin_id=bin_id,
            condition_id=f"condition-{bin_id}",
            yes_token_id=f"yes-{bin_id}",
            no_token_id=f"no-{bin_id}",
        )
        for bin_id, _side, _price, _depth in specs
    )
    samples = np.tile(np.array([0.62, 0.003, 0.00001, 0.37699]), (400, 1))
    samples[:20] = np.array([0.62, 0.10, 0.00001, 0.27999])
    witness_fields = {
        "family_key": family,
        "bindings": bindings,
        "q_version": "q-joint",
        "resolution_identity": "resolution-joint",
        "topology_identity": "topology-joint",
        "posterior_identity_hash": "posterior-joint",
        "source_truth_identity": "source-joint",
        "authority_certificate_hash": "certificate-joint",
        "band_alpha": ALPHA,
        "band_basis": "joint_q_band_samples",
        "yes_point_q": np.mean(samples, axis=0),
        "yes_q_samples": samples,
        "captured_at_utc": captured_at,
    }
    witness = S.JointOutcomeProbabilityWitness(
        **witness_fields,
        max_age=timedelta(seconds=1),
        witness_identity=S.joint_probability_witness_identity(**witness_fields),
    )
    candidates = []
    for bin_id, side, price, depth in specs:
        token = f"{side.lower()}-{bin_id}"
        binding = next(binding for binding in bindings if binding.bin_id == bin_id)
        if side == "YES":
            binding = replace(binding, yes_token_id=token)
        else:
            binding = replace(binding, no_token_id=token)
        rebound = tuple(binding if row.bin_id == bin_id else row for row in witness.bindings)
        rebound_fields = {**witness_fields, "bindings": rebound}
        rebound_witness = S.JointOutcomeProbabilityWitness(
            **rebound_fields,
            max_age=timedelta(seconds=1),
            witness_identity=S.joint_probability_witness_identity(**rebound_fields),
        )
        witness = rebound_witness
        curve = _global_curve(
            side=side,
            token=token,
            levels=(
                (("0.087", "10"), ("0.09", "540"))
                if bin_id == "33"
                else ((price, depth),)
            ),
            min_order="20",
        )
        candidates.append(
            S.GlobalSingleOrderCandidate(
                candidate_id=f"candidate-{bin_id}-{side}",
                family_key=family,
                bin_id=bin_id,
                condition_id=f"condition-{bin_id}",
                side=side,
                token_id=token,
                probability_witness_identity="unused-by-direct-planner",
                book_snapshot_id=curve.snapshot_id,
                book_captured_at_utc=captured_at,
                execution_curve_identity=S.executable_curve_identity(curve),
                ledger_snapshot_id="ledger-current",
                executable_cost_curve=curve,
                resolution_identity="resolution-joint",
            )
        )
    endowment = S.FamilyPortfolioEndowment(
        family_key=family,
        payout_by_bin_usd=tuple((bin_id, Decimal("0")) for bin_id in witness.bin_ids),
        current_token_shares=(),
        wealth_floor_usd=Decimal("1449.166"),
        spendable_cash_usd=Decimal("1449.166"),
        portfolio_capital_usd=Decimal("1449.166"),
        committed_capital_usd=Decimal("0"),
        ledger_snapshot_id="ledger-current",
    )
    optimize_calls = 0
    optimizer_shapes = []
    optimize_family = S._ru_cvar_optimum

    def counted_optimize(**kwargs):
        nonlocal optimize_calls
        optimize_calls += 1
        caps = kwargs["caps"]
        costs = kwargs["costs"]
        cash = kwargs["cash"]
        optimizer_shapes.append(len(caps))
        owners = (
            (0, 0, 1, 2, 3)
            if len(caps) == 5
            else (0, 0, 1, 2)
        )
        for owner in range(len(candidates)):
            owned = [
                i
                for i, tranche_owner in enumerate(owners)
                if tranche_owner == owner
            ]
            assert sum(costs[i] * caps[i] for i in owned) <= cash + 1e-9
        return optimize_family(**kwargs)

    monkeypatch.setattr(S, "_ru_cvar_optimum", counted_optimize)
    plan = S.plan_family_joint_buy_targets(
        tuple(candidates),
        probability_witness=witness,
        endowment=endowment,
        capital_limit_by_candidate={
            candidate.candidate_id: Decimal("1449.166")
            for candidate in candidates
        },
        fractional_kelly_multiplier=Decimal("0.03125"),
    )

    assert plan.no_trade_reason is None
    assert plan.primary_candidate_id is not None
    assert Decimal("0") < plan.fractional_target_cost_usd <= Decimal("1449.166") / 32
    assert plan.full_kelly_cost_usd > plan.fractional_target_cost_usd
    assert plan.robust_delta_log_wealth > 0
    for target in plan.targets:
        assert target.fractional_kelly_target_shares == (
            target.full_kelly_target_shares * Decimal("0.03125")
        )
        assert target.shares <= target.fractional_kelly_target_shares
    target_by_id = {target.candidate_id: target.shares for target in plan.targets}
    assert "candidate-33-YES" not in target_by_id
    assert "candidate-35-NO" in target_by_id
    assert "candidate-36-NO" in target_by_id
    assert optimize_calls == 1

    payout = {bin_id: Decimal("0") for bin_id in witness.bin_ids}
    holdings = []
    candidate_by_id = {candidate.candidate_id: candidate for candidate in candidates}
    for target in plan.targets:
        candidate = candidate_by_id[target.candidate_id]
        holdings.append((candidate.token_id, target.shares))
        for bin_id in witness.bin_ids:
            wins = (
                bin_id == candidate.bin_id
                if candidate.side == "YES"
                else bin_id != candidate.bin_id
            )
            if wins:
                payout[bin_id] += target.shares
    filled = S.FamilyPortfolioEndowment(
        family_key=family,
        payout_by_bin_usd=tuple(payout.items()),
        current_token_shares=tuple(holdings),
        wealth_floor_usd=Decimal("1449.166") - plan.fractional_target_cost_usd,
        spendable_cash_usd=Decimal("1449.166") - plan.fractional_target_cost_usd,
        portfolio_capital_usd=Decimal("1449.166"),
        committed_capital_usd=plan.fractional_target_cost_usd,
        ledger_snapshot_id="ledger-current",
    )
    repeated = S.plan_family_joint_buy_targets(
        tuple(candidates),
        probability_witness=witness,
        endowment=filled,
        capital_limit_by_candidate={
            candidate.candidate_id: Decimal("1449.166")
            for candidate in candidates
        },
        fractional_kelly_multiplier=Decimal("0.03125"),
    )

    assert repeated.primary_candidate_id is None
    assert repeated.no_trade_reason == "FAMILY_JOINT_FRACTIONAL_BUDGET_EXHAUSTED"
    assert repeated.fractional_target_cost_usd == 0
    assert optimize_calls == 1

    bound_candidates = tuple(
        replace(
            candidate,
            probability_witness_identity=witness.witness_identity,
        )
        for candidate in candidates
    )
    decision = _global_select(
        bound_candidates,
        floor="1449.166",
        ceiling="1449.166",
        cash="1449.166",
        cap="1449.166",
        probability_witnesses={family: witness},
        family_portfolio_endowment_resolver=lambda _: endowment,
        fractional_kelly_multiplier="0.03125",
    )

    assert decision.candidate is not None, tuple(
        (row.candidate_id, row.status, row.rejection_reason)
        for row in decision.candidate_evaluations
    )
    assert decision.candidate.candidate_id == plan.primary_candidate_id, tuple(
        (
            row.candidate_id,
            row.status,
            row.rejection_reason,
            row.shares,
            row.buy_sizing_mode,
        )
        for row in decision.candidate_evaluations
    )
    assert decision.buy_sizing_mode == "FAMILY_JOINT_FRACTIONAL_TARGET"
    assert optimizer_shapes == [5, 4]


def test_family_joint_does_not_spend_fixed_capital_fraction_above_kelly_target():
    candidate = _global_candidate(
        candidate_id="family-joint-weak-edge",
        family="family-joint-weak-edge",
        side="NO",
        q=0.8125733672356523,
        levels=(("0.78", "57.5"),),
        fee="0.05",
        min_order="5",
    )
    witness = _global_probability_witness(candidate)
    endowment = S.FamilyPortfolioEndowment(
        family_key=candidate.family_key,
        payout_by_bin_usd=tuple(
            (bin_id, Decimal("0")) for bin_id in witness.bin_ids
        ),
        current_token_shares=(),
        wealth_floor_usd=Decimal("465.531417"),
        spendable_cash_usd=Decimal("465.531417"),
        portfolio_capital_usd=Decimal("1450"),
        committed_capital_usd=Decimal("0"),
        ledger_snapshot_id="ledger-current",
    )

    full = S.plan_family_joint_buy_targets(
        (candidate,),
        probability_witness=witness,
        endowment=endowment,
        capital_limit_by_candidate={candidate.candidate_id: Decimal("1450")},
        fractional_kelly_multiplier=Decimal("1"),
    )
    fractional = S.plan_family_joint_buy_targets(
        (candidate,),
        probability_witness=witness,
        endowment=endowment,
        capital_limit_by_candidate={candidate.candidate_id: Decimal("1450")},
        fractional_kelly_multiplier=Decimal("0.03125"),
    )

    assert full.primary_candidate_id == candidate.candidate_id
    assert full.targets[0].shares > Decimal("55")
    assert full.targets[0].full_kelly_target_shares == full.targets[0].shares
    assert (
        full.targets[0].full_kelly_target_shares * Decimal("0.03125")
        < Decimal("5")
    )
    assert fractional.primary_candidate_id is None
    assert fractional.targets == ()
    assert fractional.no_trade_reason == "FAMILY_JOINT_NO_POSITIVE_TARGET"


def _global_witness(
    *,
    floor="100",
    ceiling="100",
    cash="100",
    reservations="0",
    collateral="CHAIN",
    position_hash="positions-current",
):
    captured_at = _DECISION_AT - timedelta(milliseconds=100)
    identity = S.portfolio_wealth_identity(
        ledger_snapshot_id="ledger-current",
        position_set_hash=position_hash,
        wealth_floor_usd=Decimal(floor),
        wealth_ceiling_usd=Decimal(ceiling),
        spendable_cash_usd=Decimal(cash),
        reservations_usd=Decimal(reservations),
        collateral_authority=collateral,
        captured_at_utc=captured_at,
    )
    return S.PortfolioWealthWitness(
        ledger_snapshot_id="ledger-current",
        position_set_hash=position_hash,
        wealth_floor_usd=Decimal(floor),
        wealth_ceiling_usd=Decimal(ceiling),
        spendable_cash_usd=Decimal(cash),
        reservations_usd=Decimal(reservations),
        collateral_authority=collateral,
        captured_at_utc=captured_at,
        max_age=timedelta(seconds=1),
        witness_identity=identity,
    )


def _global_probability_witness(candidate):
    return _GLOBAL_PROBABILITY_WITNESSES[candidate.probability_witness_identity]


def _global_universe(
    probability_witnesses,
    *,
    resolution_hours_by_family=None,
):
    captured_at = _DECISION_AT - timedelta(milliseconds=100)
    family_bindings = tuple(
        (family_key, witness.family_binding_identity)
        for family_key, witness in probability_witnesses.items()
    )
    hours_by_family = resolution_hours_by_family or {}
    family_resolution_at_utc = tuple(
        (
            family_key,
            _DECISION_AT
            + timedelta(hours=float(hours_by_family.get(family_key, 24.0))),
        )
        for family_key in probability_witnesses
    )
    identity = S.global_auction_universe_identity(
        family_bindings=family_bindings,
        family_resolution_at_utc=family_resolution_at_utc,
        venue_universe_identity="venue-universe-current",
        captured_at_utc=captured_at,
    )
    return S.GlobalAuctionUniverseWitness(
        family_bindings=family_bindings,
        family_resolution_at_utc=family_resolution_at_utc,
        venue_universe_identity="venue-universe-current",
        captured_at_utc=captured_at,
        max_age=timedelta(seconds=1),
        witness_identity=identity,
    )


def _global_select(
    candidates, *, floor="100", ceiling="100", cash="100", cap="5", witness=None,
    probability_witnesses=None, current_probabilities=None,
    current_executions=None, current_wealth_identity=None, universe=None,
    current_universe_identity=None,
    candidate_capital_limit_resolver=None,
    candidate_portfolio_endowment_resolver=None,
    family_portfolio_endowment_resolver=None,
    candidate_policy_rejection_resolver=None,
    fractional_kelly_multiplier="1",
    resolution_hours_by_family=None,
    cancelled=None,
):
    candidates = tuple(candidates)
    if probability_witnesses is None:
        probability_witnesses = {}
        for candidate in candidates:
            probability_witnesses.setdefault(
                candidate.family_key, _global_probability_witness(candidate)
            )
    if current_probabilities is None:
        current_probabilities = {
            family: S.CurrentFamilyProbabilityAuthority.from_witness(probability)
            for family, probability in probability_witnesses.items()
        }
    if current_executions is None:
        current_executions = {
            candidate.candidate_id: S.CurrentExecutionAuthority(
                token_id=candidate.token_id,
                side=candidate.side,
                book_snapshot_id=candidate.book_snapshot_id,
                execution_curve_identity=candidate.execution_curve_identity,
                action=getattr(candidate, "action", "BUY"),
            )
            for candidate in candidates
        }
    wealth = witness or _global_witness(floor=floor, ceiling=ceiling, cash=cash)
    universe = universe or _global_universe(
        probability_witnesses,
        resolution_hours_by_family=resolution_hours_by_family,
    )
    return S.select_global_single_order(
        candidates,
        probability_witnesses=probability_witnesses,
        universe_witness=universe,
        current_universe_identity_resolver=lambda: (
            universe.witness_identity
            if current_universe_identity is None
            else current_universe_identity
        ),
        current_probability_resolver=current_probabilities.get,
        current_execution_resolver=lambda candidate: current_executions.get(
            candidate.candidate_id
        ),
        current_wealth_identity_resolver=lambda: (
            wealth.economic_identity
            if current_wealth_identity is None
            else current_wealth_identity
        ),
        wealth_witness=wealth,
        capital_limit_usd=Decimal(cap),
        fractional_kelly_multiplier=Decimal(fractional_kelly_multiplier),
        decision_at_utc=_DECISION_AT,
        candidate_capital_limit_resolver=candidate_capital_limit_resolver,
        candidate_portfolio_endowment_resolver=(
            candidate_portfolio_endowment_resolver
        ),
        family_portfolio_endowment_resolver=family_portfolio_endowment_resolver,
        candidate_policy_rejection_resolver=candidate_policy_rejection_resolver,
        cancelled=cancelled,
    )


def test_global_rejected_buy_detail_failure_does_not_abort_auction(monkeypatch):
    candidate = _global_candidate(
        candidate_id="global-rejected-detail",
        family="detail-family",
        side="YES",
        q=0.10,
        levels=(("0.50", "100"),),
    )

    def reject_detail(**_kwargs):
        raise ValueError("detail shape unavailable")

    monkeypatch.setattr(S, "GlobalBuyRejectionEconomics", reject_detail)

    decision = _global_select((candidate,))

    assert decision.candidate is None
    assert decision.rejection_reasons[candidate.candidate_id] == (
        "NON_POSITIVE_ROBUST_OBJECTIVE"
    )
    assert decision.candidate_evaluations[0].buy_rejection_economics is None


def test_deterministic_day0_payoff_selects_exact_bin_and_rejects_unknown_sibling():
    family = "day0-deterministic-family"
    captured_at = _DECISION_AT - timedelta(milliseconds=100)
    bindings = (
        S.OutcomeTokenBinding(
            bin_id="dead-bin",
            condition_id="dead-condition",
            yes_token_id="dead-yes",
            no_token_id="dead-no",
        ),
        S.OutcomeTokenBinding(
            bin_id="unknown-bin",
            condition_id="unknown-condition",
            yes_token_id="unknown-yes",
            no_token_id="unknown-no",
        ),
    )
    fields = {
        "family_key": family,
        "bindings": bindings,
        "exact_yes_payoffs": (("dead-bin", 0),),
        "q_version": "day0-exact-q-v1",
        "resolution_identity": "day0-resolution",
        "topology_identity": "day0-topology",
        "posterior_identity_hash": "day0-payoff-state",
        "source_truth_identity": "day0-observation-fact",
        "authority_certificate_hash": "day0-certificate",
        "band_alpha": ALPHA,
        "band_basis": "day0_deterministic_bin_payoff_v1",
        "captured_at_utc": captured_at,
    }
    identity = S.deterministic_bin_payoff_witness_identity(**fields)
    witness = S.DeterministicBinPayoffWitness(
        **fields,
        max_age=timedelta(seconds=1),
        witness_identity=identity,
    )
    rebound_bindings = (
        replace(bindings[0], no_token_id="dead-no-current"),
        bindings[1],
    )
    rebound = S.rebind_family_payoff_witness(
        witness,
        bindings=rebound_bindings,
    )
    reissued = S.reissue_family_payoff_witness(
        rebound,
        authority_certificate_hash="day0-certificate-current",
        captured_at_utc=captured_at + timedelta(milliseconds=10),
    )
    assert isinstance(rebound, S.DeterministicBinPayoffWitness)
    assert rebound.exact_yes_payoffs == witness.exact_yes_payoffs
    assert rebound.witness_identity != witness.witness_identity
    assert reissued.exact_yes_payoffs == witness.exact_yes_payoffs
    assert reissued.witness_identity != rebound.witness_identity
    exact = S.global_candidate_from_native(
        SimpleNamespace(
            no_trade_reason=None,
            executable_cost_curve=_global_curve(
                side="NO",
                token="dead-no",
                levels=(("0.20", "100"),),
                min_order="1",
            ),
            family_key=family,
            bin_id="dead-bin",
            condition_id="dead-condition",
            side="NO",
            token_id="dead-no",
            hypothesis_id="buy-dead-no",
        ),
        probability_witness=witness,
        ledger_snapshot_id="ledger-current",
        book_captured_at_utc=captured_at,
    )
    unknown = S.global_candidate_from_native(
        SimpleNamespace(
            no_trade_reason=None,
            executable_cost_curve=_global_curve(
                side="YES",
                token="unknown-yes",
                levels=(("0.01", "100"),),
                min_order="1",
            ),
            family_key=family,
            bin_id="unknown-bin",
            condition_id="unknown-condition",
            side="YES",
            token_id="unknown-yes",
            hypothesis_id="buy-unknown-yes",
        ),
        probability_witness=witness,
        ledger_snapshot_id="ledger-current",
        book_captured_at_utc=captured_at,
    )

    decision = _global_select(
        (exact, unknown),
        probability_witnesses={family: witness},
    )

    assert decision.candidate == exact
    assert decision.terminal_wealth is not None
    assert decision.terminal_wealth.win_probability_lcb == pytest.approx(1.0)
    assert unknown.eligibility_reason == "DETERMINISTIC_PAYOFF_NOT_PROVED"
    assert decision.rejection_reasons[unknown.candidate_id] == (
        "DETERMINISTIC_PAYOFF_NOT_PROVED"
    )


def test_global_single_order_stops_before_scoring_when_cancelled(monkeypatch):
    candidate = _global_candidate(
        candidate_id="cancelled-before-score",
        family="cancelled-family",
        side="YES",
        q=0.80,
        levels=(("0.40", "20"),),
    )
    monkeypatch.setattr(
        S,
        "_score_global_single_order",
        lambda *_args, **_kwargs: pytest.fail(
            "cancelled selection must not score a candidate"
        ),
    )

    decision = _global_select((candidate,), cancelled=lambda: True)

    assert decision.candidate is None
    assert decision.no_trade_reason == "GLOBAL_SELECTION_CANCELLED"
    assert decision.rejection_reasons == {
        candidate.candidate_id: "GLOBAL_SELECTION_CANCELLED"
    }


def test_global_single_order_stops_between_candidate_scores():
    candidates = (
        _global_candidate(
            candidate_id="score-first",
            family="first-family",
            side="YES",
            q=0.80,
            levels=(("0.40", "20"),),
        ),
        _global_candidate(
            candidate_id="cancel-before-second",
            family="second-family",
            side="YES",
            q=0.80,
            levels=(("0.40", "20"),),
        ),
    )
    checks = 0

    def cancelled() -> bool:
        nonlocal checks
        checks += 1
        return checks >= 5

    decision = _global_select(candidates, cancelled=cancelled)

    assert checks == 5
    assert decision.candidate is None
    assert decision.no_trade_reason == "GLOBAL_SELECTION_CANCELLED"


def test_global_single_order_sell_can_beat_positive_buy_and_cash():
    sell = _global_sell_candidate(
        candidate_id="sell-winner",
        family="sell-family",
        side="YES",
        held_q=0.15,
        bids=(("0.40", "4"), ("0.30", "6")),
        shares="10",
    )
    buy = _global_candidate(
        candidate_id="positive-buy-runner-up",
        family="buy-family",
        side="NO",
        q=0.80,
        levels=(("0.60", "20"),),
    )

    decision = _global_select(
        (buy, sell), floor="100", ceiling="110", cash="100", cap="5"
    )

    assert decision.candidate is sell
    assert decision.shares == Decimal("10")
    assert decision.cash_proceeds_usd == Decimal("3.4")
    assert decision.cost_usd == Decimal("6.6")
    assert decision.limit_price == Decimal("0.30")
    assert decision.expected_fill_price_before_fee == Decimal("0.34")
    assert decision.max_spend_usd == 0
    assert decision.robust_delta_log_wealth > 0
    assert decision.robust_ev_usd > 0
    evaluations = {
        evaluation.candidate_id: evaluation
        for evaluation in decision.candidate_evaluations
    }
    assert set(evaluations) == {buy.candidate_id, sell.candidate_id}
    assert decision.candidate_input_count == len(evaluations) == 2
    assert evaluations[sell.candidate_id].status == "SELECTED"
    assert evaluations[sell.candidate_id].position_id == "position-sell-winner"
    assert evaluations[sell.candidate_id].held_shares == Decimal("10")
    assert evaluations[buy.candidate_id].status == "SCORED"
    assert (
        evaluations[sell.candidate_id].robust_delta_log_wealth
        > evaluations[buy.candidate_id].robust_delta_log_wealth
        > 0
    )
    assert decision.capital_lock_hours == pytest.approx(24)
    assert decision.robust_log_growth_per_hour == pytest.approx(
        decision.robust_delta_log_wealth / 24
    )
    assert (
        evaluations[sell.candidate_id].robust_log_growth_per_hour
        > evaluations[buy.candidate_id].robust_log_growth_per_hour
        > 0
    )


def test_positive_sell_still_beats_a_discrete_repair_buy():
    sell = _global_sell_candidate(
        candidate_id="sell-over-repair",
        family="sell-over-repair",
        side="YES",
        held_q=0.15,
        bids=(("0.40", "4"), ("0.30", "6")),
        shares="10",
    )
    repair_buy = _global_candidate(
        candidate_id="repair-runner-up",
        family="repair-runner-up",
        side="NO",
        q=0.70,
        levels=(("0.49", "1000"),),
        min_order="1",
    )

    decision = _global_select(
        (repair_buy, sell),
        floor="100",
        ceiling="110",
        cash="100",
        cap="5",
        fractional_kelly_multiplier="0.0001",
    )
    evaluations = {
        evaluation.candidate_id: evaluation
        for evaluation in decision.candidate_evaluations
    }

    assert decision.candidate is sell
    assert evaluations[sell.candidate_id].status == "SELECTED"
    assert evaluations[repair_buy.candidate_id].status == "REJECTED"
    assert evaluations[repair_buy.candidate_id].rejection_reason == (
        "FRACTIONAL_KELLY_TARGET_BELOW_MINIMUM_LOT"
    )


def test_global_single_order_sell_uses_incremental_growth_not_loss_majority():
    sell = _global_sell_candidate(
        candidate_id="sell-high-bid",
        family="sell-high-bid-family",
        side="YES",
        held_q=0.60,
        bids=(("0.90", "10"),),
        shares="10",
    )

    decision = _global_select(
        (sell,), floor="100", ceiling="110", cash="100", cap="5"
    )

    assert decision.candidate is sell
    assert decision.terminal_wealth is not None
    assert decision.terminal_wealth.win_probability_lcb == pytest.approx(0.40)
    assert decision.terminal_wealth.median_payoff_usd == Decimal("-1.0")
    assert decision.cash_proceeds_usd == Decimal("9.0")
    assert decision.robust_delta_log_wealth > 0
    assert decision.robust_ev_usd > 0


def test_global_single_order_ranks_buy_and_sell_by_one_capital_growth_rate():
    sell = _global_sell_candidate(
        candidate_id="sell-runner-up",
        family="sell-runner-family",
        side="YES",
        held_q=0.15,
        bids=(("0.40", "4"), ("0.30", "6")),
        shares="10",
    )
    buy = _global_candidate(
        candidate_id="buy-winner",
        family="buy-winner-family",
        side="YES",
        q=0.99,
        levels=(("0.10", "20"),),
    )

    decision = _global_select(
        (sell, buy),
        floor="100",
        ceiling="110",
        cash="100",
        cap="5",
        resolution_hours_by_family={
            sell.family_key: 24,
            buy.family_key: 6,
        },
    )

    assert decision.candidate is buy
    assert decision.cash_proceeds_usd == 0
    assert decision.robust_delta_log_wealth > 0
    assert decision.capital_action_mode == "SETTLEMENT_LOCKED_BUY"
    assert decision.robust_log_growth_per_hour == pytest.approx(
        decision.robust_delta_log_wealth / 6
    )
    evaluations = {
        evaluation.candidate_id: evaluation
        for evaluation in decision.candidate_evaluations
    }
    assert (
        evaluations[buy.candidate_id].robust_delta_log_wealth
        > evaluations[sell.candidate_id].robust_delta_log_wealth
        > 0
    )
    assert evaluations[sell.candidate_id].capital_lock_hours == pytest.approx(24)
    assert evaluations[sell.candidate_id].robust_log_growth_per_hour == pytest.approx(
        evaluations[sell.candidate_id].robust_delta_log_wealth / 24
    )
    assert (
        evaluations[buy.candidate_id].robust_log_growth_per_hour
        > evaluations[sell.candidate_id].robust_log_growth_per_hour
        > 0
    )


def test_global_single_order_entry_pause_blocks_buy_but_preserves_sell_and_cash():
    sell = _global_sell_candidate(
        candidate_id="sell-under-entry-pause",
        family="sell-under-entry-pause-family",
        side="YES",
        held_q=0.15,
        bids=(("0.40", "4"), ("0.30", "6")),
        shares="10",
    )
    buy = _global_candidate(
        candidate_id="buy-blocked-by-entry-pause",
        family="buy-blocked-by-entry-pause-family",
        side="YES",
        q=0.99,
        levels=(("0.10", "20"),),
    )

    decision = _global_select(
        (sell, buy),
        floor="100",
        ceiling="110",
        cash="100",
        cap="5",
        candidate_policy_rejection_resolver=lambda candidate: (
            "ENTRY_ACTION_PAUSED:external:operator"
            if getattr(candidate, "action", "BUY") == "BUY"
            else None
        ),
    )

    assert decision.candidate is sell
    assert decision.cash_proceeds_usd == Decimal("3.4")
    assert decision.robust_delta_log_wealth > 0
    assert decision.rejection_reasons[buy.candidate_id] == (
        "ENTRY_ACTION_PAUSED:external:operator"
    )
    evaluations = {
        evaluation.candidate_id: evaluation
        for evaluation in decision.candidate_evaluations
    }
    assert evaluations[sell.candidate_id].status == "SELECTED"
    assert evaluations[buy.candidate_id].status == "REJECTED"
    assert evaluations[buy.candidate_id].rejection_reason == (
        "ENTRY_ACTION_PAUSED:external:operator"
    )
    assert decision.candidate_input_count == len(evaluations) == 2


def test_global_single_order_zero_buy_capacity_preserves_sell_and_cash():
    sell = _global_sell_candidate(
        candidate_id="sell-with-zero-buy-capacity",
        family="sell-with-zero-buy-capacity-family",
        side="YES",
        held_q=0.15,
        bids=(("0.40", "4"), ("0.30", "6")),
        shares="10",
    )
    buy = _global_candidate(
        candidate_id="buy-with-zero-capacity",
        family="buy-with-zero-capacity-family",
        side="YES",
        q=0.99,
        levels=(("0.10", "20"),),
    )

    decision = _global_select(
        (sell, buy),
        floor="100",
        ceiling="110",
        cash="0",
        cap="0",
    )

    assert decision.candidate is sell
    assert decision.cash_proceeds_usd == Decimal("3.4")
    assert decision.robust_delta_log_wealth > 0
    assert decision.rejection_reasons[buy.candidate_id] == (
        "CAPITAL_CAPACITY_EXHAUSTED"
    )


@pytest.mark.parametrize(
    ("resolver", "reason"),
    (
        (lambda _candidate: "", "CANDIDATE_POLICY_AUTHORITY_INVALID"),
        (
            lambda _candidate: (_ for _ in ()).throw(RuntimeError("policy unavailable")),
            "CANDIDATE_POLICY_AUTHORITY_MISSING",
        ),
    ),
)
def test_global_single_order_policy_authority_fault_invalidates_epoch(
    resolver, reason
):
    buy = _global_candidate(
        candidate_id="buy-policy-authority-fault",
        family="buy-policy-authority-fault-family",
        side="YES",
        q=0.99,
        levels=(("0.10", "20"),),
    )

    decision = _global_select(
        (buy,), candidate_policy_rejection_resolver=resolver
    )

    assert decision.candidate is None
    assert decision.no_trade_reason == "GLOBAL_EPOCH_SUPERSEDED"
    assert decision.rejection_reasons == {buy.candidate_id: reason}


def test_global_single_order_cash_beats_non_positive_buy_and_sell():
    sell = _global_sell_candidate(
        candidate_id="bad-sell",
        family="bad-sell-family",
        side="YES",
        held_q=0.80,
        bids=(("0.20", "10"),),
        shares="10",
    )
    buy = _global_candidate(
        candidate_id="bad-buy",
        family="bad-buy-family",
        side="NO",
        q=0.55,
        levels=(("0.90", "20"),),
    )

    decision = _global_select((sell, buy))

    assert decision.candidate is None
    assert decision.no_trade_reason == "NO_CURRENT_EXECUTABLE_POSITIVE_ORDER"
    assert decision.robust_delta_log_wealth == 0
    assert decision.cost_usd == 0
    assert decision.candidate_input_count == 2
    assert {
        evaluation.candidate_id: (
            evaluation.status,
            evaluation.rejection_reason,
        )
        for evaluation in decision.candidate_evaluations
    } == {
        sell.candidate_id: ("REJECTED", "NON_POSITIVE_ROBUST_OBJECTIVE"),
        buy.candidate_id: ("REJECTED", "NON_POSITIVE_ROBUST_OBJECTIVE"),
    }
    evaluations = {
        evaluation.candidate_id: evaluation
        for evaluation in decision.candidate_evaluations
    }
    assert evaluations[sell.candidate_id].position_id == "position-bad-sell"
    assert evaluations[sell.candidate_id].held_shares == Decimal("10")
    assert evaluations[sell.candidate_id].shares == Decimal("1")
    assert evaluations[sell.candidate_id].cash_proceeds_usd == Decimal("0.2000")
    assert evaluations[sell.candidate_id].limit_price == Decimal("0.20")
    assert evaluations[sell.candidate_id].expected_fill_price_before_fee == Decimal(
        "0.20"
    )
    assert evaluations[sell.candidate_id].robust_delta_log_wealth < 0
    assert evaluations[sell.candidate_id].robust_ev_usd == pytest.approx(-0.6)
    assert evaluations[sell.candidate_id].terminal_wealth is not None
    assert evaluations[buy.candidate_id].position_id is None
    assert evaluations[buy.candidate_id].held_shares == 0
    buy_rejection = evaluations[buy.candidate_id].buy_rejection_economics
    assert buy_rejection is not None
    assert buy_rejection.resolution_at_utc is not None
    assert buy_rejection.capital_lock_hours == pytest.approx(24.0)
    assert buy_rejection.probe_robust_delta_log_wealth < 0
    assert buy_rejection.probe_robust_log_growth_per_hour == pytest.approx(
        buy_rejection.probe_robust_delta_log_wealth / 24.0
    )


def test_sell_point_counterfactual_is_identity_bound_and_cannot_change_live_action():
    sell = _global_sell_candidate(
        candidate_id="sell-point-counterfactual",
        family="sell-point-counterfactual-family",
        side="YES",
        held_q=0.10,
        bids=(("0.30", "10"),),
        shares="10",
    )
    held_q_samples = np.concatenate((np.full(380, 0.10), np.full(20, 0.90)))
    sell = _replace_global_q_samples(sell, held_q_samples)
    low_point = _replace_global_point_q(sell, 0.10)
    high_point = _replace_global_point_q(sell, 0.90)

    low_decision = _global_select((low_point,))
    high_decision = _global_select((high_point,))

    assert low_point.probability_witness_identity != high_point.probability_witness_identity
    assert low_decision.candidate is None
    assert high_decision.candidate is None
    assert low_decision.rejection_reasons == high_decision.rejection_reasons == {
        sell.candidate_id: "NON_POSITIVE_ROBUST_OBJECTIVE"
    }
    low_evaluation = low_decision.candidate_evaluations[0]
    high_evaluation = high_decision.candidate_evaluations[0]
    assert low_evaluation.robust_delta_log_wealth == (
        high_evaluation.robust_delta_log_wealth
    )
    assert low_evaluation.robust_ev_usd == high_evaluation.robust_ev_usd
    assert low_evaluation.sell_point_counterfactual is not None
    assert high_evaluation.sell_point_counterfactual is not None
    assert low_evaluation.sell_point_counterfactual.status == "POSITIVE"
    assert low_evaluation.sell_point_counterfactual.shares == Decimal("10.00")
    assert low_evaluation.sell_point_counterfactual.expected_ev_usd > 0.0
    assert high_evaluation.sell_point_counterfactual.status == "NON_POSITIVE"
    assert high_evaluation.sell_point_counterfactual.expected_ev_usd < 0.0


def test_mature_day0_sell_uses_point_expectation_after_temporal_gate():
    """Parameter-tail caution cannot replace fixed-action expected utility."""

    sell = _global_sell_candidate(
        candidate_id="mature-day0-point-functional",
        family="mature-day0-point-functional-family",
        side="YES",
        held_q=0.10,
        bids=(("0.30", "10"),),
        shares="10",
        probability_functional="POSTERIOR_PREDICTIVE_MEAN",
        exit_authority_status="mature",
        exit_authority_reason="day0_high_extreme_mature:post_peak_confidence=0.97",
    )
    held_q_samples = np.concatenate((np.full(380, 0.10), np.full(20, 0.90)))
    sell = _replace_global_q_samples(sell, held_q_samples)
    sell = _replace_global_point_q(sell, 0.10)
    alternate_tail = _replace_global_q_samples(
        sell,
        np.concatenate((np.full(300, 0.01), np.full(100, 0.99))),
    )
    alternate_tail = _replace_global_point_q(alternate_tail, 0.10)

    decision = _global_select((sell,))
    alternate_decision = _global_select((alternate_tail,))

    assert decision.candidate is sell
    assert decision.robust_delta_log_wealth == 0.0
    assert decision.robust_ev_usd == 0.0
    assert decision.terminal_wealth is None
    assert decision.expected_terminal_wealth is not None
    assert decision.expected_terminal_wealth.expected_delta_log_wealth > 0.0
    assert decision.expected_terminal_wealth.expected_ev_usd > 0.0
    assert decision.expected_growth is not None
    assert decision.expected_growth.expected_delta_log_wealth > 0.0
    assert alternate_decision.candidate is alternate_tail
    assert alternate_decision.shares == decision.shares
    assert alternate_decision.limit_price == decision.limit_price
    assert alternate_decision.expected_terminal_wealth is not None
    assert (
        alternate_decision.expected_terminal_wealth.expected_delta_log_wealth
        == pytest.approx(
            decision.expected_terminal_wealth.expected_delta_log_wealth
        )
    )
    assert alternate_decision.expected_terminal_wealth.expected_ev_usd == pytest.approx(
        decision.expected_terminal_wealth.expected_ev_usd
    )
    evaluation = decision.candidate_evaluations[0]
    assert evaluation.status == "SELECTED"
    assert evaluation.sell_probability_functional == "POSTERIOR_PREDICTIVE_MEAN"
    assert evaluation.sell_exit_authority_status == "mature"
    assert evaluation.sell_exit_authority_reason == sell.exit_authority_reason


def test_mature_mean_sell_cannot_masquerade_as_robust_certificate():
    mature = _global_sell_candidate(
        candidate_id="mature-explicit-mean",
        family="mature-explicit-mean-family",
        side="YES",
        held_q=0.10,
        bids=(("0.30", "10"),),
        shares="10",
        probability_functional="POSTERIOR_PREDICTIVE_MEAN",
        exit_authority_status="mature",
        exit_authority_reason="day0_high_extreme_mature:post_peak_confidence=0.97",
    )
    mature_decision = _global_select((mature,))
    robust = replace(
        mature,
        probability_functional="LOWER_CVAR_PARAMETER_DRAWS",
        exit_authority_status="not_applicable",
        exit_authority_reason="non_day0_family",
    )
    robust_decision = _global_select((robust,))

    assert mature_decision.candidate is mature
    assert robust_decision.candidate is robust
    with pytest.raises(ValueError, match="positive economics"):
        replace(
            mature_decision.candidate_evaluations[0],
            robust_delta_log_wealth=robust_decision.robust_delta_log_wealth,
            robust_ev_usd=robust_decision.robust_ev_usd,
            capital_efficiency=robust_decision.capital_efficiency,
            robust_log_growth_per_hour=(
                robust_decision.robust_log_growth_per_hour
            ),
            terminal_wealth=robust_decision.terminal_wealth,
            expected_terminal_wealth=None,
        )


def test_buy_robust_admission_and_size_are_invariant_to_common_mean_score():
    buy = _global_candidate(
        candidate_id="robust-buy-common-mean-ranking",
        family="robust-buy-common-mean-ranking-family",
        side="YES",
        q=0.80,
        levels=(("0.20", "20"),),
    )
    low_mean = _replace_global_point_q(buy, 0.70)
    high_mean = _replace_global_point_q(buy, 0.95)

    low_decision = _global_select((low_mean,))
    high_decision = _global_select((high_mean,))

    assert low_decision.candidate is low_mean
    assert high_decision.candidate is high_mean
    assert low_decision.shares == high_decision.shares
    assert low_decision.robust_delta_log_wealth == pytest.approx(
        high_decision.robust_delta_log_wealth
    )
    assert low_decision.robust_ev_usd == pytest.approx(
        high_decision.robust_ev_usd
    )
    assert low_decision.expected_growth is not None
    assert high_decision.expected_growth is not None
    assert (
        high_decision.expected_growth.expected_delta_log_wealth
        > low_decision.expected_growth.expected_delta_log_wealth
        > 0.0
    )


def test_sell_point_counterfactual_failure_cannot_block_profitable_live_sell(
    monkeypatch,
):
    sell = _global_sell_candidate(
        candidate_id="sell-point-detail-fault",
        family="sell-point-detail-fault-family",
        side="YES",
        held_q=0.10,
        bids=(("0.50", "10"),),
        shares="10",
    )
    monkeypatch.setattr(
        S,
        "_score_global_sell_point_counterfactual",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("detail")),
    )

    decision = _global_select((sell,))

    assert decision.candidate is sell
    assert decision.robust_delta_log_wealth > 0.0
    counterfactual = decision.candidate_evaluations[0].sell_point_counterfactual
    assert counterfactual is not None
    assert counterfactual.status == "UNAVAILABLE"
    assert counterfactual.rejection_reason == (
        "POINT_COUNTERFACTUAL_COMPUTATION_FAILED"
    )


def test_missing_posterior_mean_blocks_cross_action_selection_fail_closed(
    monkeypatch,
):
    sell = _global_sell_candidate(
        candidate_id="sell-point-missing",
        family="sell-point-missing-family",
        side="YES",
        held_q=0.10,
        bids=(("0.50", "10"),),
        shares="10",
    )
    monkeypatch.setattr(S, "family_payoff_point_q", lambda *args, **kwargs: None)

    decision = _global_select((sell,))

    assert decision.candidate is None
    assert decision.no_trade_reason == "GLOBAL_EPOCH_SUPERSEDED"
    assert decision.rejection_reasons == {
        sell.candidate_id: "EXPECTED_COMPARISON_UNAVAILABLE"
    }
    counterfactual = decision.candidate_evaluations[0].sell_point_counterfactual
    assert counterfactual is not None
    assert counterfactual.status == "UNAVAILABLE"
    assert counterfactual.point_held_payoff_q is None
    assert counterfactual.rejection_reason == "POINT_PROBABILITY_UNAVAILABLE"


def test_global_single_order_capital_authority_failure_preserves_sell_and_stops_retries():
    sell = _global_sell_candidate(
        candidate_id="sell-before-cap-failure",
        family="sell-before-cap-failure-family",
        side="YES",
        held_q=0.15,
        bids=(("0.40", "4"), ("0.30", "6")),
        shares="10",
    )
    buy = _global_candidate(
        candidate_id="cap-failure-buy",
        family="cap-failure-buy-family",
        side="YES",
        q=0.80,
    )
    later_buy = _global_candidate(
        candidate_id="later-cap-failure-buy",
        family="later-cap-failure-buy-family",
        side="NO",
        q=0.80,
    )
    calls = []

    def unavailable(candidate):
        calls.append(candidate.candidate_id)
        if candidate is buy:
            raise RuntimeError("allocator unavailable")
        return Decimal("5")

    decision = _global_select(
        (buy, sell, later_buy),
        candidate_capital_limit_resolver=unavailable,
    )

    assert decision.candidate is sell
    assert decision.capital_action_mode == "IMMEDIATE_REDUCE_ONLY_SELL"
    assert decision.cash_proceeds_usd == Decimal("3.4")
    assert calls == [buy.candidate_id]
    evaluations = {
        evaluation.candidate_id: evaluation
        for evaluation in decision.candidate_evaluations
    }
    assert evaluations[sell.candidate_id].status == "SELECTED"
    assert evaluations[buy.candidate_id].rejection_reason == (
        "CAPITAL_CONSTRAINT_UNAVAILABLE"
    )
    assert evaluations[later_buy.candidate_id].rejection_reason == (
        "CAPITAL_CONSTRAINT_UNAVAILABLE"
    )


def test_global_single_order_sell_yes_no_label_mirror_is_exact():
    yes = _global_sell_candidate(
        candidate_id="sell-mirror-yes",
        family="sell-mirror-yes-family",
        side="YES",
        held_q=0.20,
        bids=(("0.42", "3"), ("0.31", "7")),
        shares="10",
        fee="0.02",
    )
    no = _global_sell_candidate(
        candidate_id="sell-mirror-no",
        family="sell-mirror-no-family",
        side="NO",
        held_q=0.20,
        bids=(("0.42", "3"), ("0.31", "7")),
        shares="10",
        fee="0.02",
    )

    yes_decision = _global_select((yes,), floor="100", ceiling="110")
    no_decision = _global_select((no,), floor="100", ceiling="110")

    assert yes_decision.candidate is yes
    assert no_decision.candidate is no
    assert yes_decision.shares == no_decision.shares
    assert yes_decision.cost_usd == no_decision.cost_usd
    assert yes_decision.cash_proceeds_usd == no_decision.cash_proceeds_usd
    assert yes_decision.limit_price == no_decision.limit_price
    assert yes_decision.robust_delta_log_wealth == no_decision.robust_delta_log_wealth
    assert yes_decision.robust_ev_usd == no_decision.robust_ev_usd


@pytest.mark.parametrize("side", ("YES", "NO"))
@pytest.mark.parametrize(
    ("price", "held_q"),
    (("0.004", 0.001), ("0.999", 0.20)),
)
def test_global_single_order_sell_ranks_only_live_price_band_probes(
    side, price, held_q
):
    sell = _global_sell_candidate(
        candidate_id=f"sell-out-of-band-{side}-{price}",
        family=f"sell-out-of-band-{side}-{price}-family",
        side=side,
        held_q=held_q,
        bids=((price, "10"),),
        shares="10",
    )

    decision = _global_select((sell,))

    assert decision.candidate is None
    assert (
        decision.rejection_reasons[sell.candidate_id]
        == "LIVE_UNIT_PRICE_OUT_OF_BOUNDS"
    )


@pytest.mark.parametrize("side", ("YES", "NO"))
@pytest.mark.parametrize(
    ("price", "held_q"),
    (("0.05", 0.001), ("0.95", 0.20)),
)
def test_global_single_order_sell_price_band_is_inclusive(side, price, held_q):
    sell = _global_sell_candidate(
        candidate_id=f"sell-boundary-{side}-{price}",
        family=f"sell-boundary-{side}-{price}-family",
        side=side,
        held_q=held_q,
        bids=((price, "10"),),
        shares="10",
    )

    decision = _global_select((sell,))

    assert decision.candidate is sell
    assert decision.limit_price == Decimal(price)


def test_global_single_order_sell_uses_best_partial_depth_when_full_depth_is_absent():
    sell = _global_sell_candidate(
        candidate_id="sell-thin-depth",
        family="sell-thin-depth-family",
        side="YES",
        held_q=0.10,
        bids=(("0.50", "9.99"),),
        shares="10",
    )

    decision = _global_select((sell,))

    assert decision.candidate is sell
    assert decision.shares == Decimal("9.99")
    assert decision.cash_proceeds_usd == Decimal("4.995")
    assert decision.robust_delta_log_wealth > 0.0
    assert decision.robust_ev_usd > 0.0


def test_global_single_order_sell_with_subminimum_depth_is_a_complete_no_trade():
    sell = _global_sell_candidate(
        candidate_id="sell-subminimum-depth",
        family="sell-subminimum-depth-family",
        side="YES",
        held_q=0.10,
        bids=(("0.50", "0.99"),),
        shares="10",
    )

    decision = _global_select((sell,))

    assert decision.candidate is None
    assert decision.no_trade_reason == "NO_CURRENT_EXECUTABLE_POSITIVE_ORDER"
    assert decision.capital_action_mode == "UNSCORED"
    assert decision.rejection_reasons == {sell.candidate_id: "DEPTH_INFEASIBLE"}
    assert len(decision.candidate_evaluations) == 1
    assert decision.candidate_evaluations[0].status == "REJECTED"
    assert decision.candidate_evaluations[0].rejection_reason == "DEPTH_INFEASIBLE"


def test_global_single_order_sell_selects_interior_capital_optimal_reduction():
    sell = _global_sell_candidate(
        candidate_id="sell-interior-optimum",
        family="sell-interior-optimum-family",
        side="YES",
        held_q=0.49,
        bids=(("0.50", "10"),),
        shares="10",
    )

    decision = _global_select((sell,), floor="100", ceiling="109.40")

    assert decision.candidate is sell
    assert Decimal("4.98") <= decision.shares <= Decimal("5.00")
    assert decision.shares < sell.held_shares
    assert decision.robust_delta_log_wealth > 0.0
    assert decision.robust_ev_usd > 0.0


@pytest.mark.parametrize(
    ("held_q", "bids", "shares", "fee", "floor", "ceiling"),
    (
        (0.25, (("0.55", "2.37"), ("0.40", "4.11")), "6.48", "0.01", "83", "120"),
        (0.49, (("0.50", "10"),), "10", "0", "100", "109.40"),
        (0.10, (("0.62", "2.13"), ("0.51", "3.22")), "8", "0.02", "91", "130"),
    ),
)
def test_global_single_order_sell_matches_every_cent_grid_oracle(
    held_q, bids, shares, fee, floor, ceiling
):
    sell = _global_sell_candidate(
        candidate_id=f"sell-grid-{held_q}",
        family=f"sell-grid-{held_q}-family",
        side="NO",
        held_q=held_q,
        bids=bids,
        shares=shares,
        fee=fee,
    )
    held_samples = np.full(80, held_q, dtype=np.float64)
    score = S._score_global_single_order_sell(
        sell,
        held_payoff_q_samples=held_samples,
        band_alpha=0.10,
        wealth_floor_usd=Decimal(floor),
        wealth_ceiling_usd=Decimal(ceiling),
    )

    curve = sell.executable_sell_curve
    max_shares = min(
        sell.held_shares,
        sum((level.size for level in curve.levels), Decimal("0")),
    ).quantize(Decimal("0.01"), rounding=ROUND_FLOOR)
    robust_q = 1.0 - held_q
    loss_baseline = Decimal(floor) + sell.held_shares
    win_baseline = Decimal(ceiling)
    oracle = None
    size = Decimal("1")
    while size <= max_shares:
        proceeds, expected_fill_price, limit_price = curve.proceeds_for_shares(size)
        loss_at_risk = size - proceeds
        loss_after = loss_baseline - size + proceeds
        win_after = win_baseline + proceeds
        robust_du = (1.0 - robust_q) * math.log(
            float(loss_after / loss_baseline)
        ) + robust_q * math.log(float(win_after / win_baseline))
        efficiency = robust_du / float(loss_at_risk)
        point = (
            robust_du,
            efficiency,
            -loss_at_risk,
            size,
            proceeds,
            expected_fill_price,
            limit_price,
        )
        if oracle is None or point[:3] > oracle[:3]:
            oracle = point
        size += Decimal("0.01")

    assert oracle is not None
    assert score.shares == oracle[3]
    assert score.cash_proceeds_usd == oracle[4]
    assert score.expected_fill_price_before_fee == oracle[5]
    assert score.limit_price == oracle[6]
    assert score.robust_delta_log_wealth == pytest.approx(oracle[0], abs=1e-12)


def test_global_sell_materializer_floors_chain_fill_dust_to_venue_grid():
    seed = _global_candidate(
        candidate_id="sell-chain-dust",
        family="sell-chain-dust-family",
        side="NO",
        q=0.20,
    )
    probability = _global_probability_witness(seed)
    sell_curve = S.ExecutableSellCurve(
        token_id=seed.token_id,
        side=seed.side,
        snapshot_id="sell-chain-dust-book",
        book_hash="sell-chain-dust-hash",
        levels=(BookLevel(price=Decimal("0.80"), size=Decimal("100")),),
        fee_model=FeeModel(fee_rate=Decimal("0")),
        min_tick=Decimal("0.001"),
        min_order_size=Decimal("1"),
        quote_ttl=timedelta(seconds=1),
    )
    holding = SimpleNamespace(
        family_key=seed.family_key,
        bin_id=seed.bin_id,
        side=seed.side,
        token_id=seed.token_id,
        position_id="position-chain-dust",
        shares=Decimal("72.506664"),
    )

    candidate = S.global_sell_candidate_from_holding(
        holding,
        probability_witness=probability,
        ledger_snapshot_id="ledger-chain-dust",
        executable_sell_curve=sell_curve,
        book_captured_at_utc=_DECISION_AT,
    )

    assert candidate is not None
    assert candidate.held_shares == Decimal("72.50")


def test_global_sell_materializer_omits_venue_illegal_dust_only_holding():
    seed = _global_candidate(
        candidate_id="sell-dust-only",
        family="sell-dust-only-family",
        side="YES",
        q=0.20,
    )
    sell_curve = S.ExecutableSellCurve(
        token_id=seed.token_id,
        side=seed.side,
        snapshot_id="sell-dust-only-book",
        book_hash="sell-dust-only-hash",
        levels=(BookLevel(price=Decimal("0.80"), size=Decimal("100")),),
        fee_model=FeeModel(fee_rate=Decimal("0")),
        min_tick=Decimal("0.001"),
        min_order_size=Decimal("1"),
        quote_ttl=timedelta(seconds=1),
    )
    holding = SimpleNamespace(
        family_key=seed.family_key,
        bin_id=seed.bin_id,
        side=seed.side,
        token_id=seed.token_id,
        position_id="position-dust-only",
        shares=Decimal("0.006664"),
    )

    candidate = S.global_sell_candidate_from_holding(
        holding,
        probability_witness=_global_probability_witness(seed),
        ledger_snapshot_id="ledger-dust-only",
        executable_sell_curve=sell_curve,
        book_captured_at_utc=_DECISION_AT,
    )

    assert candidate is None


def test_global_single_order_yes_best_matches_full_depth_exact_oracle():
    yes = _global_candidate(
        candidate_id="yes-a",
        family="a",
        side="YES",
        q=0.70,
        levels=(("0.35", "3"), ("0.40", "30")),
        fee="0.05",
    )
    no = _global_candidate(
        candidate_id="no-b", family="b", side="NO", q=0.55
    )
    oracle = _global_exact_oracle(yes)
    decision = _global_select((no, yes))

    assert decision.candidate.candidate_id == "yes-a"
    assert decision.shares == oracle[4]
    assert decision.cost_usd == oracle[3]
    assert abs(decision.robust_delta_log_wealth - oracle[0]) < 1e-12


def test_global_single_order_sizes_each_native_side_inside_current_capital_envelope():
    yes = _global_candidate(
        candidate_id="capital-bounded-yes",
        family="capital-yes",
        side="YES",
        q=0.82,
        levels=(("0.40", "100"),),
    )
    unrestricted = _global_select((yes,), cap="5")
    bounded = _global_select(
        (yes,),
        cap="5",
        candidate_capital_limit_resolver=lambda _candidate: Decimal("1.20"),
    )

    assert unrestricted.max_spend_usd > Decimal("1.20")
    assert bounded.candidate is not None
    assert bounded.candidate.candidate_id == yes.candidate_id
    assert bounded.max_spend_usd <= Decimal("1.20")
    assert bounded.robust_delta_log_wealth > 0.0


def test_global_single_order_excludes_capacity_exhausted_winner_and_ranks_runner_up():
    exhausted = _global_candidate(
        candidate_id="exhausted-yes",
        family="exhausted",
        side="YES",
        q=0.90,
    )
    feasible = _global_candidate(
        candidate_id="feasible-no",
        family="feasible",
        side="NO",
        q=0.70,
    )
    decision = _global_select(
        (exhausted, feasible),
        candidate_capital_limit_resolver=lambda candidate: (
            Decimal("0")
            if candidate.candidate_id == exhausted.candidate_id
            else Decimal("5")
        ),
    )

    assert decision.candidate is not None
    assert decision.candidate.candidate_id == feasible.candidate_id
    assert decision.rejection_reasons[exhausted.candidate_id] == (
        "CAPITAL_CAPACITY_EXHAUSTED"
    )


def test_global_single_order_no_best_matches_full_depth_exact_oracle():
    yes = _global_candidate(
        candidate_id="yes-a", family="a", side="YES", q=0.56
    )
    no = _global_candidate(
        candidate_id="no-b",
        family="b",
        side="NO",
        q=0.74,
        levels=(("0.38", "2"), ("0.43", "30")),
        fee="0.05",
    )
    oracle = _global_exact_oracle(no)
    decision = _global_select((yes, no))

    assert decision.candidate.candidate_id == "no-b"
    assert decision.shares == oracle[4]
    assert decision.cost_usd == oracle[3]
    assert abs(decision.robust_delta_log_wealth - oracle[0]) < 1e-12


def test_global_single_order_binds_exact_shares_to_fundable_deepest_limit():
    candidate = _global_candidate(
        candidate_id="deep-book",
        family="deep",
        side="YES",
        q=0.99,
        levels=(("0.10", "10"), ("0.50", "100")),
    )

    decision = _global_select((candidate,), cap="6")

    assert decision.candidate is not None
    assert decision.shares == Decimal("12.00")
    assert decision.cost_usd == Decimal("2.000")
    assert decision.limit_price == Decimal("0.50")
    assert decision.expected_fill_price_before_fee == Decimal("0.1666666666666666666666666667")
    assert decision.max_spend_usd == Decimal("6.0000")


def test_global_buy_fak_certificate_proves_every_nonzero_fill_prefix():
    candidate = _global_candidate(
        candidate_id="fak-prefix-positive",
        family="fak-prefix-positive",
        side="NO",
        q=0.90,
        levels=(("0.10", "100"),),
        fee="0.05",
    )
    decision = _global_select((candidate,), cap="5")

    cert = S.global_buy_fak_prefix_certificate(decision)
    unit_cost = Decimal(str(cert["global_buy_fak_worst_unit_cost"]))
    assert cert["global_buy_fak_fee_rounding_bound"] == (
        "ROUNDED_FEE_AT_MOST_TWO_X_UNROUNDED"
    )
    assert Decimal(str(cert["global_buy_fak_worst_fee_shape"])) == Decimal("0.09")
    assert Decimal(str(cert["global_buy_fak_worst_fee_per_share"])) == Decimal("0.0090")
    assert "global_buy_fak_min_fill_quantum" not in cert
    terminal = decision.terminal_wealth
    assert terminal is not None
    floor = terminal.wealth_after_loss_usd - terminal.loss_payoff_usd
    ceiling = terminal.wealth_after_win_usd - terminal.win_payoff_usd
    for shares in (Decimal("0.01"), decision.shares / 2, decision.shares):
        cost = unit_cost * shares
        du = terminal.loss_probability_ucb * math.log(float((floor - cost) / floor))
        du += terminal.win_probability_lcb * math.log(
            float((ceiling - cost + shares) / ceiling)
        )
        ev = terminal.win_probability_lcb * float(shares) - float(cost)
        assert du > 0
        assert ev > 0

    high_limit = S.global_buy_fak_prefix_certificate(
        replace(decision, limit_price=Decimal("0.70"))
    )
    assert Decimal(str(high_limit["global_buy_fak_worst_fee_shape"])) == Decimal("0.21")
    assert Decimal(str(high_limit["global_buy_fak_worst_fee_per_share"])) == Decimal("0.0210")


def test_global_buy_fak_certificate_rejects_negative_worst_limit_endpoint():
    candidate = _global_candidate(
        candidate_id="fak-prefix-negative",
        family="fak-prefix-negative",
        side="YES",
        q=0.70,
        levels=(("0.10", "100"),),
        fee="0.05",
    )
    decision = _global_select((candidate,), cap="5")
    worse_limit = replace(decision, limit_price=Decimal("0.90"))

    with pytest.raises(ValueError, match="non-positive"):
        S.global_buy_fak_prefix_certificate(worse_limit)


def test_global_buy_fak_certificate_uses_coherent_joint_price_fee_bound_at_999():
    """The fee-shape maximum at .5 cannot be added to a .999 fill price."""
    candidate = _global_candidate(
        candidate_id="fak-prefix-999",
        family="fak-prefix-999",
        side="NO",
        q=1.0,
        levels=(("0.10", "100"),),
        fee="0.05",
    )
    decision = _global_select((candidate,), cap="5")
    cert = S.global_buy_fak_prefix_certificate(
        replace(
            decision,
            limit_price=Decimal("0.999"),
        )
    )

    assert Decimal(str(cert["global_buy_fak_worst_fee_shape"])) == Decimal(
        "0.000999"
    )
    assert Decimal(str(cert["global_buy_fak_worst_unit_cost"])) == Decimal(
        "0.99909990"
    )
    assert cert["global_buy_fak_full_robust_ev_usd"] == pytest.approx(
        float((Decimal("1") - Decimal("0.99909990")) * decision.shares)
    )


def test_global_buy_fak_certificate_binds_fee_curve_and_recomputes_independently():
    from src.decision_kernel.canonicalization import (
        qkernel_global_buy_fak_prefix_rejection_reason,
    )

    candidate = _global_candidate(
        candidate_id="fak-prefix-binding",
        family="fak-prefix-binding",
        side="YES",
        q=0.90,
        levels=(("0.10", "100"),),
        fee="0.05",
    )
    decision = _global_select((candidate,), cap="5")
    terminal = decision.terminal_wealth
    assert terminal is not None
    economics = {
        **S.global_buy_fak_prefix_certificate(decision),
        "side": candidate.side,
        "global_jit_execution_curve_identity": candidate.execution_curve_identity,
        "global_target_shares": str(decision.shares),
        "global_limit_price": str(decision.limit_price),
        "global_terminal_win_probability_lcb": terminal.win_probability_lcb,
        "global_terminal_loss_probability_ucb": terminal.loss_probability_ucb,
        "global_terminal_loss_payoff_usd": str(terminal.loss_payoff_usd),
        "global_terminal_win_payoff_usd": str(terminal.win_payoff_usd),
        "global_terminal_wealth_after_loss_usd": str(terminal.wealth_after_loss_usd),
        "global_terminal_wealth_after_win_usd": str(terminal.wealth_after_win_usd),
    }

    assert qkernel_global_buy_fak_prefix_rejection_reason(
        economics, direction="buy_yes"
    ) is None
    assert qkernel_global_buy_fak_prefix_rejection_reason(
        {**economics, "global_buy_fak_fee_rate": "0.10"},
        direction="buy_yes",
    ) == "global_buy_fak_worst_fee_per_share"
    assert qkernel_global_buy_fak_prefix_rejection_reason(
        {**economics, "global_buy_fak_fee_rounding_bound": "PER_CENTISHARE"},
        direction="buy_yes",
    ) == "fee_rounding_bound"
    assert qkernel_global_buy_fak_prefix_rejection_reason(
        {**economics, "global_buy_fak_execution_curve_identity": "tampered"},
        direction="buy_yes",
    ) == "execution_curve_identity"


@pytest.mark.parametrize("side", ("YES", "NO"))
def test_global_single_order_optimizes_on_price_dependent_venue_grid(side):
    candidate = _global_candidate(
        candidate_id=f"venue-grid-{side.lower()}",
        family=f"venue-grid-{side.lower()}",
        side=side,
        q=0.99,
        levels=(("0.37", "702.13"),),
    )

    decision = _global_select(
        (candidate,), floor="10000", ceiling="10000", cash="1000", cap="1000"
    )

    assert decision.candidate is not None
    assert decision.shares == Decimal("702.00")
    assert venue_submit_amount_precision_error(
        direction="buy_yes" if side == "YES" else "buy_no",
        final_limit_price=decision.limit_price,
        submitted_shares=decision.shares,
        order_type="FOK",
        tick_size=candidate.executable_cost_curve.min_tick,
    ) is None


@pytest.mark.parametrize("price", ("0.001", "0.008", "0.037", "0.37", "0.70"))
@pytest.mark.parametrize("raw", ("5.01", "99.99", "702.13"))
def test_global_venue_neighbor_matches_sdk_faithful_quantizer(price, raw):
    candidate = _global_candidate(
        candidate_id=f"venue-neighbor-{price}-{raw}",
        family=f"venue-neighbor-{price}-{raw}",
        side="YES",
        q=0.99,
        levels=((price, "2000"),),
    )
    shares = Decimal(raw)

    try:
        expected_at_most = quantize_submit_shares_for_venue_at_most(
            "buy_yes",
            shares,
            final_limit_price=Decimal(price),
            order_type="FOK",
            tick_size=candidate.executable_cost_curve.min_tick,
        )
    except ValueError:
        expected_at_most = None

    assert S._single_order_venue_legal_neighbor(
        candidate, shares, at_most=True
    ) == expected_at_most
    assert S._single_order_venue_legal_neighbor(
        candidate, shares, at_most=False
    ) == quantize_submit_shares_for_venue(
        "buy_yes",
        shares,
        final_limit_price=Decimal(price),
        order_type="FOK",
        tick_size=candidate.executable_cost_curve.min_tick,
    )


def test_global_venue_neighbor_validation_is_bounded(monkeypatch):
    candidate = _global_candidate(
        candidate_id="venue-neighbor-bounded",
        family="venue-neighbor-bounded",
        side="NO",
        q=0.99,
        levels=(("0.001", "2000"),),
    )
    calls = 0
    original = S.venue_submit_amount_precision_error

    def counted(**kwargs):
        nonlocal calls
        calls += 1
        return original(**kwargs)

    monkeypatch.setattr(S, "venue_submit_amount_precision_error", counted)

    assert S._single_order_venue_legal_neighbor(
        candidate, Decimal("99.99"), at_most=True
    ) == Decimal("90.00")
    assert calls <= 25


def test_global_single_order_label_mirror_preserves_size_cost_and_objective():
    yes = _global_candidate(
        candidate_id="yes", family="a", side="YES", q=0.70,
        levels=(("0.35", "2"), ("0.41", "20")), fee="0.05",
    )
    no = _global_candidate(
        candidate_id="no", family="b", side="NO", q=0.70,
        levels=(("0.35", "2"), ("0.41", "20")), fee="0.05",
    )
    yes_score = _global_score(yes)
    no_score = _global_score(no)
    yes_decision = _global_select(
        (yes,),
        resolution_hours_by_family={"a": 18.0},
    )
    no_decision = _global_select(
        (no,),
        resolution_hours_by_family={"b": 18.0},
    )

    assert yes_score.shares == no_score.shares
    assert yes_score.cost_usd == no_score.cost_usd
    assert yes_score.limit_price == no_score.limit_price
    assert (
        yes_score.expected_fill_price_before_fee
        == no_score.expected_fill_price_before_fee
    )
    assert yes_score.max_spend_usd == no_score.max_spend_usd
    assert yes_score.robust_delta_log_wealth == no_score.robust_delta_log_wealth
    assert (
        yes_decision.robust_log_growth_per_hour
        == no_decision.robust_log_growth_per_hour
    )


def test_global_single_order_fractional_kelly_bounds_final_holding_for_both_sides():
    yes = _global_candidate(
        candidate_id="fractional-yes",
        family="fractional-yes",
        side="YES",
        q=0.78,
        levels=(("0.27", "10"), ("0.33", "490")),
    )
    no = _global_candidate(
        candidate_id="fractional-no",
        family="fractional-no",
        side="NO",
        q=0.78,
        levels=(("0.27", "10"), ("0.33", "490")),
    )
    full_yes = _global_score(
        yes, floor="1253.44", ceiling="1253.44", cash="1141.98", cap="1141.98"
    )
    fractional_yes = _global_score(
        yes,
        floor="1253.44",
        ceiling="1253.44",
        cash="1141.98",
        cap="107.58",
        multiplier="0.03125",
    )
    fractional_no = _global_score(
        no,
        floor="1253.44",
        ceiling="1253.44",
        cash="1141.98",
        cap="107.58",
        multiplier="0.03125",
    )
    capacity_bounded = _global_score(
        yes,
        floor="1253.44",
        ceiling="1253.44",
        cash="1141.98",
        cap="3",
        multiplier="0.03125",
    )

    share_scaled = S._single_order_venue_legal_neighbor(
        yes,
        max(
            full_yes.shares * Decimal("0.03125"),
            S._single_order_min_marketable_shares(yes.executable_cost_curve),
        ),
        at_most=False,
    )
    assert share_scaled is not None
    loss_budget = full_yes.cost_usd * Decimal("0.03125")
    assert fractional_yes.cost_usd <= loss_budget
    assert fractional_yes.shares < share_scaled
    assert (
        fractional_yes.shares
        <= fractional_yes.fractional_kelly_target_shares
    )
    assert fractional_yes.fractional_kelly_target_shares == (
        fractional_yes.full_kelly_target_shares * Decimal("0.03125")
    )
    assert fractional_yes.shares == fractional_no.shares
    assert fractional_yes.cost_usd == fractional_no.cost_usd
    assert fractional_yes.max_spend_usd == fractional_no.max_spend_usd
    assert (
        fractional_yes.fractional_kelly_target_shares
        == fractional_no.fractional_kelly_target_shares
    )
    assert (
        fractional_yes.robust_delta_log_wealth
        == fractional_no.robust_delta_log_wealth
    )
    assert fractional_yes.max_spend_usd < Decimal("10")
    assert fractional_yes.max_spend_usd < full_yes.max_spend_usd
    assert capacity_bounded.max_spend_usd <= Decimal("3")
    assert capacity_bounded.shares < fractional_yes.shares


def test_global_single_order_rejects_cheap_minimum_lot_above_fractional_target():
    candidate = _global_candidate(
        candidate_id="cheap-depth",
        family="cheap-depth",
        side="YES",
        q=0.9187643552930886,
        levels=(
            ("0.001", "2063.59"),
            ("0.028", "70"),
            ("0.029", "129"),
            ("0.030", "265.8"),
            ("0.033", "73.36"),
            ("0.300", "500"),
            ("0.600", "1000"),
            ("0.900", "2000"),
        ),
        fee="0.1",
    )
    decision = _global_score(
        candidate,
        floor="1189.71",
        ceiling="1189.71",
        cash="1189.71",
        cap="107.58",
        multiplier="0.03125",
    )

    assert decision.candidate is None
    assert decision.shares == 0
    assert decision.rejection_reasons[candidate.candidate_id] == (
        "FRACTIONAL_KELLY_TARGET_BELOW_MINIMUM_LOT"
    )


def test_global_single_order_capacity_frontier_never_shrinks_on_a_deeper_price_jump():
    candidate = _global_candidate(
        candidate_id="monotone-capacity",
        family="monotone-capacity",
        side="YES",
        q=0.90,
        levels=(("0.001", "2063"), ("0.033", "500"), ("0.300", "500")),
        fee="0",
    )

    assert S._single_order_max_shares(
        candidate.executable_cost_curve,
        spend_limit_usd=Decimal("107.58"),
    ) == Decimal("2563.00")


@pytest.mark.parametrize("side", ("YES", "NO"))
def test_global_single_order_rejects_subminimum_kelly_target_symmetrically(side):
    candidate = _global_candidate(
        candidate_id=f"marketable-min-{side.lower()}",
        family=f"marketable-min-{side.lower()}",
        side=side,
        q=0.58,
        levels=(("0.06", "100"),),
    )

    decision = _global_select(
        (candidate,),
        floor="1000",
        ceiling="1000",
        cash="100",
        cap="100",
        fractional_kelly_multiplier="0.03125",
    )

    assert decision.candidate is None
    assert decision.shares == 0
    assert decision.rejection_reasons[candidate.candidate_id] == (
        "FRACTIONAL_KELLY_TARGET_BELOW_MINIMUM_LOT"
    )


@pytest.mark.parametrize("multiplier", ("0", "-0.1", "NaN", "1.01"))
def test_global_single_order_fractional_kelly_multiplier_fails_closed(multiplier):
    candidate = _global_candidate(
        candidate_id=f"invalid-kelly-{multiplier}",
        family=f"invalid-kelly-{multiplier}",
        side="YES",
        q=0.78,
    )

    with pytest.raises(ValueError, match="fractional Kelly multiplier"):
        _global_score(candidate, multiplier=multiplier)


def test_global_single_order_excludes_cheap_day0_without_current_observation():
    unsupported = _global_candidate(
        candidate_id="cheap-tail", family="helsinki", side="YES", q=0.13,
        levels=(("0.008", "1000"),), reason="DAY0_OBSERVATION_UNAVAILABLE",
    )
    current = _global_candidate(
        candidate_id="current-no", family="toronto", side="NO", q=0.65
    )
    decision = _global_select((unsupported, current))

    assert decision.candidate.candidate_id == "current-no"
    assert decision.rejection_reasons["cheap-tail"] == "DAY0_OBSERVATION_UNAVAILABLE"


def test_unverified_13pct_tail_is_lottery_not_an_executable_edge():
    ladder = (("0.008", "19.09"), ("0.009", "14"), ("0.010", "38.14"), ("0.020", "51"))
    current_13pct_yes = _global_candidate(
        candidate_id="current-13pct-yes",
        family="a",
        side="YES",
        q=0.13,
        levels=ladder,
        fee="0.05",
    )
    valid_no = _global_candidate(
        candidate_id="valid-no",
        family="b",
        side="NO",
        q=0.65,
        levels=(("0.60", "100"),),
    )

    probability_witnesses = {"b": _global_probability_witness(valid_no)}
    decision = _global_select(
        (current_13pct_yes, valid_no),
        probability_witnesses=probability_witnesses,
    )

    assert decision.candidate is None
    assert decision.no_trade_reason == "GLOBAL_FEASIBLE_SET_INCOMPLETE"


def test_current_13pct_at_live_floor_is_accepted_positive_growth():
    tail = _global_candidate(
        candidate_id="current-13pct-live-floor",
        family="tail",
        side="YES",
        q=0.13,
        levels=(("0.10", "1000"),),
    )

    decision = _global_select((tail,))

    assert decision.candidate is not None
    assert decision.robust_delta_log_wealth > 0
    assert decision.robust_ev_usd > 0
    assert (
        decision.terminal_wealth.median_payoff_usd
        == decision.terminal_wealth.loss_payoff_usd
    )


def test_global_selection_ranks_by_robust_growth_not_majority():
    tail = _global_candidate(
        candidate_id="current-13pct-live-floor",
        family="tail",
        side="YES",
        q=0.13,
        levels=(("0.10", "1000"),),
    )
    majority_no = _global_candidate(
        candidate_id="current-majority-no",
        family="majority",
        side="NO",
        q=0.65,
        levels=(("0.60", "100"),),
    )

    tail_solo = _global_select((tail,))
    majority_solo = _global_select((majority_no,))
    assert tail_solo.candidate is not None
    assert majority_solo.candidate is not None

    decision = _global_select((tail, majority_no))

    winner = (
        tail
        if tail_solo.robust_delta_log_wealth > majority_solo.robust_delta_log_wealth
        else majority_no
    )
    assert decision.candidate is winner


def test_global_single_order_positivity_boundary_is_strict():
    """The economic boundary is positive robust growth, not q=0.5."""

    def decision_at(q):
        candidate = _global_candidate(
            candidate_id=f"positivity-boundary-{q}",
            family=f"positivity-boundary-{q}",
            side="YES",
            q=q,
            levels=(("0.10", "100"),),
        )
        return candidate, _global_select((candidate,))

    lo, hi = 0.05, 0.20
    for _ in range(40):
        mid = (lo + hi) / 2
        if decision_at(mid)[1].candidate is None:
            lo = mid
        else:
            hi = mid

    below_candidate, below = decision_at(lo - 1e-6)
    _above_candidate, above = decision_at(hi + 1e-6)

    assert below.candidate is None
    assert (
        below.rejection_reasons[below_candidate.candidate_id]
        == "NON_POSITIVE_ROBUST_OBJECTIVE"
    )
    assert above.candidate is not None
    assert above.robust_delta_log_wealth > 0
    assert above.robust_ev_usd > 0


@pytest.mark.parametrize("side", ("YES", "NO"))
def test_global_single_order_certifies_exact_binary_terminal_payoffs(side):
    candidate = _global_candidate(
        candidate_id=f"terminal-certificate-{side.lower()}",
        family=f"terminal-certificate-{side.lower()}",
        side=side,
        q=0.70,
        levels=(("0.35", "100"),),
    )

    decision = _global_select((candidate,))

    assert decision.candidate is not None
    cert = decision.terminal_wealth
    assert cert is not None
    assert cert.win_probability_lcb == pytest.approx(0.70)
    assert cert.loss_probability_ucb == pytest.approx(0.30)
    assert cert.win_probability_lcb + cert.loss_probability_ucb == pytest.approx(1.0)
    assert cert.loss_payoff_usd == -decision.cost_usd
    assert cert.win_payoff_usd == decision.shares - decision.cost_usd
    assert cert.median_payoff_usd == cert.win_payoff_usd > 0
    assert cert.expected_value_usd == pytest.approx(decision.robust_ev_usd)


def test_global_single_order_self_issued_13pct_without_external_current_is_rejected():
    tail = _global_candidate(
        candidate_id="self-issued-13pct",
        family="tail",
        side="YES",
        q=0.13,
        levels=(("0.008", "1000"),),
    )
    valid_no = _global_candidate(
        candidate_id="valid-no-external",
        family="current",
        side="NO",
        q=0.65,
    )
    witnesses = {
        "tail": _global_probability_witness(tail),
        "current": _global_probability_witness(valid_no),
    }
    current = {
        "current": S.CurrentFamilyProbabilityAuthority.from_witness(
            witnesses["current"]
        )
    }

    decision = _global_select(
        (tail, valid_no),
        probability_witnesses=witnesses,
        current_probabilities=current,
    )

    assert decision.candidate is None
    assert decision.no_trade_reason == "GLOBAL_EPOCH_SUPERSEDED"
    assert (
        decision.rejection_reasons["self-issued-13pct"]
        == "PROBABILITY_AUTHORITY_SUPERSEDED"
    )


def test_global_single_order_refuses_partial_active_family_universe():
    yes = _global_candidate(candidate_id="yes-partial", family="a", side="YES", q=0.70)
    no = _global_candidate(candidate_id="no-missing", family="b", side="NO", q=0.70)
    complete_witnesses = {
        "a": _global_probability_witness(yes),
        "b": _global_probability_witness(no),
    }
    universe = _global_universe(complete_witnesses)

    decision = _global_select(
        (yes,),
        probability_witnesses={"a": complete_witnesses["a"]},
        universe=universe,
    )

    assert decision.candidate is None
    assert decision.no_trade_reason == "GLOBAL_FEASIBLE_SET_INCOMPLETE"


def test_global_single_order_refuses_native_token_changed_inside_same_family_key():
    candidate = _global_candidate(
        candidate_id="topology-superseded",
        family="same-family",
        side="YES",
        q=0.70,
    )
    witness = _global_probability_witness(candidate)
    captured_at = _DECISION_AT - timedelta(milliseconds=100)
    changed_outcomes = (
        replace(witness.bindings[0], yes_token_id="yes-token-current-new"),
        *witness.bindings[1:],
    )
    changed_bindings = (
        (
            candidate.family_key,
            S.outcome_token_binding_identity(
                family_key=candidate.family_key,
                bindings=changed_outcomes,
                resolution_identity=witness.resolution_identity,
                topology_identity=witness.topology_identity,
            ),
        ),
    )
    universe = S.GlobalAuctionUniverseWitness(
        family_bindings=changed_bindings,
        family_resolution_at_utc=(
            (candidate.family_key, _DECISION_AT + timedelta(hours=24)),
        ),
        venue_universe_identity="venue-universe-current",
        captured_at_utc=captured_at,
        max_age=timedelta(seconds=1),
        witness_identity=S.global_auction_universe_identity(
            family_bindings=changed_bindings,
            family_resolution_at_utc=(
                (candidate.family_key, _DECISION_AT + timedelta(hours=24)),
            ),
            venue_universe_identity="venue-universe-current",
            captured_at_utc=captured_at,
        ),
    )

    decision = _global_select(
        (candidate,),
        probability_witnesses={candidate.family_key: witness},
        universe=universe,
    )

    assert decision.candidate is None
    assert decision.no_trade_reason == "GLOBAL_FEASIBLE_SET_INCOMPLETE"


def test_global_probability_simplex_keeps_nonexecuted_sibling_without_no_token():
    candidate = _global_candidate(
        candidate_id="executable-with-illiquid-sibling",
        family="complete-simplex",
        side="YES",
        q=0.70,
    )
    prior = _global_probability_witness(candidate)
    bindings = (
        prior.bindings[0],
        replace(prior.bindings[1], no_token_id=None),
    )
    identity = S.joint_probability_witness_identity(
        family_key=prior.family_key,
        bindings=bindings,
        q_version=prior.q_version,
        resolution_identity=prior.resolution_identity,
        topology_identity=prior.topology_identity,
        posterior_identity_hash=prior.posterior_identity_hash,
        source_truth_identity=prior.source_truth_identity,
        authority_certificate_hash=prior.authority_certificate_hash,
        band_alpha=prior.band_alpha,
        band_basis=prior.band_basis,
        yes_point_q=prior.yes_point_q,
        yes_q_samples=prior.yes_q_samples,
        captured_at_utc=prior.captured_at_utc,
    )
    witness = replace(prior, bindings=bindings, witness_identity=identity)
    candidate = replace(candidate, probability_witness_identity=identity)
    _GLOBAL_PROBABILITY_WITNESSES[identity] = witness

    decision = _global_select(
        (candidate,),
        probability_witnesses={candidate.family_key: witness},
    )

    assert decision.candidate is not None
    assert decision.candidate.candidate_id == candidate.candidate_id


def test_global_single_order_binary_metric_has_only_win_one_and_lose_zero_states():
    candidate = _global_candidate(
        candidate_id="binary", family="binary", side="YES", q=0.70
    )
    shares = Decimal("5")
    q_samples, alpha = _global_probability_projection(candidate)
    robust_du, robust_ev, _efficiency, cost = S._single_order_metrics(
        candidate,
        q_samples=q_samples,
        shares=shares,
        wealth_floor_usd=Decimal("100"),
        wealth_ceiling_usd=Decimal("100"),
        alpha=alpha,
    )
    expected_du = 0.70 * np.log((100.0 - float(cost) + 5.0) / 100.0) + 0.30 * np.log(
        (100.0 - float(cost)) / 100.0
    )

    assert abs(robust_du - expected_du) < 1e-15
    assert abs(robust_ev - (0.70 * 5.0 - float(cost))) < 1e-15


def test_global_single_order_metrics_reuse_one_exact_probability_tail():
    candidate = _global_candidate(
        candidate_id="tail-reuse", family="tail-reuse", side="YES", q=0.70
    )
    q = np.linspace(0.31, 0.91, 401, dtype=np.float64)
    alpha = 0.17
    shares = Decimal("7.25")
    floor = Decimal("83.25")
    ceiling = Decimal("127.40")
    cost = S._single_order_cost(candidate.executable_cost_curve, shares)
    lose_du = np.log((float(floor) - float(cost)) / float(floor))
    win_du = np.log(
        (float(ceiling) - float(cost) + float(shares)) / float(ceiling)
    )
    weights = np.ones(q.size, dtype=np.float64)
    expected_du = S._lower_cvar(q * win_du + (1.0 - q) * lose_du, weights, alpha)
    expected_ev = S._lower_cvar(q * float(shares) - float(cost), weights, alpha)
    robust_q = S._lower_cvar(q, weights, alpha)

    robust_du, robust_ev, _efficiency, actual_cost = S._single_order_metrics(
        candidate,
        q_samples=q,
        shares=shares,
        wealth_floor_usd=floor,
        wealth_ceiling_usd=ceiling,
        alpha=alpha,
        robust_q=robust_q,
    )

    assert actual_cost == cost
    assert abs(robust_du - expected_du) < 1e-15
    assert abs(robust_ev - expected_ev) < 1e-15


def test_global_single_order_scores_probability_tail_once(monkeypatch):
    candidate = _global_candidate(
        candidate_id="one-tail-sort",
        family="one-tail-sort",
        side="YES",
        q=0.70,
        levels=(("0.19", "1.37"), ("0.34", "4.11"), ("0.57", "20")),
    )
    q = np.linspace(0.71, 0.91, 401, dtype=np.float64)
    original = S._lower_cvar
    calls = 0

    def counted(values, weights, alpha):
        nonlocal calls
        calls += 1
        return original(values, weights, alpha)

    monkeypatch.setattr(S, "_lower_cvar", counted)
    score = S._score_global_single_order(
        candidate,
        q_samples=q,
        band_alpha=0.17,
        wealth_floor_usd=Decimal("83.25"),
        wealth_ceiling_usd=Decimal("127.40"),
        spendable_cash_usd=Decimal("50"),
        capital_limit_usd=Decimal("20"),
    )

    assert score.candidate is not None
    assert calls == 1


def test_global_single_order_prunes_impossible_ev_before_stake_probes(
    monkeypatch,
):
    candidate = _global_candidate(
        candidate_id="impossible-fee-inclusive-ev",
        family="impossible-fee-inclusive-ev",
        side="YES",
        q=0.41,
        levels=(("0.40", "1000"),),
        fee="0.20",
    )
    monkeypatch.setattr(
        S,
        "_single_order_stationary_probes",
        lambda *_args, **_kwargs: pytest.fail(
            "an impossible robust EV must not enter stake optimization"
        ),
    )

    score = _global_score(candidate, cap="100")

    assert score.candidate is None
    assert score.no_trade_reason == "NON_POSITIVE_ROBUST_OBJECTIVE"
    assert score.rejection_reasons == {
        candidate.candidate_id: "NON_POSITIVE_ROBUST_OBJECTIVE"
    }
    rejection = score.buy_rejection_economics
    assert rejection is not None
    assert rejection.robust_q_lcb == pytest.approx(0.41)
    assert rejection.minimum_all_in_unit_cost > Decimal("0.41")
    assert rejection.probe_robust_delta_log_wealth < 0
    assert rejection.probe_robust_ev_usd < 0


def test_global_single_order_normalizes_each_probe_direction_once(monkeypatch):
    candidate = _global_candidate(
        candidate_id="probe-normalization-cache",
        family="probe-normalization-cache",
        side="YES",
        q=0.70,
        levels=(("0.19", "1.37"), ("0.34", "4.11"), ("0.57", "20")),
    )
    original = S._single_order_venue_legal_neighbor
    calls = []

    def counted(candidate_arg, shares, *, at_most):
        calls.append((Decimal(shares), at_most))
        return original(candidate_arg, shares, at_most=at_most)

    monkeypatch.setattr(S, "_single_order_venue_legal_neighbor", counted)
    score = S._score_global_single_order(
        candidate,
        q_samples=np.full(400, 0.70, dtype=np.float64),
        band_alpha=0.05,
        wealth_floor_usd=Decimal("100"),
        wealth_ceiling_usd=Decimal("100"),
        spendable_cash_usd=Decimal("50"),
        capital_limit_usd=Decimal("20"),
    )

    assert score.candidate is not None
    assert len(calls) == len(set(calls))


def test_global_single_order_resizes_on_candidate_executable_q_bound():
    candidate = _global_candidate(
        candidate_id="tightened-q",
        family="tightened-q",
        side="YES",
        q=0.90,
        levels=(("0.20", "400"),),
    )
    common = dict(
        q_samples=np.full(401, 0.90, dtype=np.float64),
        band_alpha=0.05,
        wealth_floor_usd=Decimal("100"),
        wealth_ceiling_usd=Decimal("100"),
        spendable_cash_usd=Decimal("80"),
        capital_limit_usd=Decimal("80"),
    )

    loose = S._score_global_single_order(candidate, **common)
    tightened = S._score_global_single_order(
        candidate,
        payoff_q_lcb=0.55,
        **common,
    )

    assert loose.candidate is not None
    assert tightened.candidate is not None
    assert tightened.shares < loose.shares
    assert tightened.terminal_wealth is not None
    assert tightened.terminal_wealth.win_probability_lcb == 0.55
    assert tightened.robust_delta_log_wealth > 0.0


def test_global_single_order_excludes_superseded_q_book_and_capital_identity():
    q_old = _global_candidate(candidate_id="q-old", family="q", side="YES", q=0.70)
    book_old = _global_candidate(candidate_id="book-old", family="book", side="YES", q=0.70)
    curve_old = _global_candidate(candidate_id="curve-old", family="curve", side="YES", q=0.70)
    ledger_old = replace(
        _global_candidate(candidate_id="ledger-old", family="ledger", side="YES", q=0.70),
        ledger_snapshot_id="ledger-old",
    )
    candidates = (
        q_old,
        book_old,
        curve_old,
        ledger_old,
    )
    witnesses = {c.family_key: _global_probability_witness(c) for c in candidates}
    current_probabilities = {
        family: S.CurrentFamilyProbabilityAuthority.from_witness(witness)
        for family, witness in witnesses.items()
    }
    current_probabilities["q"] = replace(
        current_probabilities["q"], q_version="q-new"
    )
    current_executions = {
        c.candidate_id: S.CurrentExecutionAuthority(
            token_id=c.token_id,
            side=c.side,
            book_snapshot_id=c.book_snapshot_id,
            execution_curve_identity=c.execution_curve_identity,
        )
        for c in candidates
    }
    current_executions["book-old"] = replace(
        current_executions["book-old"], book_snapshot_id="book-new"
    )
    current_executions["curve-old"] = replace(
        current_executions["curve-old"], execution_curve_identity="curve-new"
    )
    decision = _global_select(
        candidates,
        probability_witnesses=witnesses,
        current_probabilities=current_probabilities,
        current_executions=current_executions,
    )

    assert decision.candidate is None
    assert decision.rejection_reasons == {
        "q-old": "PROBABILITY_AUTHORITY_SUPERSEDED",
        "book-old": "BOOK_IDENTITY_SUPERSEDED",
        "curve-old": "EXECUTION_CURVE_SUPERSEDED",
        "ledger-old": "CAPITAL_IDENTITY_SUPERSEDED",
    }
    assert decision.no_trade_reason == "GLOBAL_EPOCH_SUPERSEDED"


def test_global_single_order_never_promotes_runner_up_after_book_drift():
    moved = _global_candidate(
        candidate_id="old-winner", family="a", side="YES", q=0.90
    )
    runner_up = _global_candidate(
        candidate_id="runner-up", family="b", side="NO", q=0.66
    )
    executions = {
        candidate.candidate_id: S.CurrentExecutionAuthority(
            token_id=candidate.token_id,
            side=candidate.side,
            book_snapshot_id=candidate.book_snapshot_id,
            execution_curve_identity=candidate.execution_curve_identity,
        )
        for candidate in (moved, runner_up)
    }
    executions[moved.candidate_id] = replace(
        executions[moved.candidate_id], book_snapshot_id="new-book"
    )

    decision = _global_select(
        (moved, runner_up),
        current_executions=executions,
    )

    assert decision.candidate is None
    assert decision.no_trade_reason == "GLOBAL_EPOCH_SUPERSEDED"
    assert decision.rejection_reasons[moved.candidate_id] == "BOOK_IDENTITY_SUPERSEDED"


def test_global_single_order_rejects_curve_from_another_token_or_snapshot():
    cheap_yes = _global_candidate(
        candidate_id="cheap-low-hit-yes",
        family="a",
        side="YES",
        q=0.02,
        levels=(("0.005", "1000"),),
    )
    wrong_curve = _global_curve(
        side="YES",
        token="stale-wrong-token",
        levels=(("0.005", "1000"),),
    )
    forged = replace(cheap_yes, executable_cost_curve=wrong_curve)
    valid_no = _global_candidate(
        candidate_id="valid-no",
        family="b",
        side="NO",
        q=0.65,
        levels=(("0.60", "100"),),
    )

    decision = _global_select((forged, valid_no))

    assert decision.candidate.candidate_id == "valid-no"
    assert decision.rejection_reasons["cheap-low-hit-yes"] == "BOOK_CERTIFICATE_MISMATCH"


@pytest.mark.parametrize("side", ("YES", "NO"))
@pytest.mark.parametrize(
    ("price", "q"),
    (("0.004", 0.20), ("0.999", 1.0)),
)
def test_global_single_order_ranks_only_live_price_band_buy_probes(side, price, q):
    out_of_band = _global_candidate(
        candidate_id=f"out-of-band-{side}-{price}",
        family="out-of-band",
        side=side,
        q=q,
        levels=((price, "1000"),),
    )
    legal = _global_candidate(
        candidate_id=f"legal-{side}-{price}",
        family="legal",
        side=side,
        q=0.80,
        levels=(("0.60", "100"),),
    )

    decision = _global_select((out_of_band, legal))

    assert decision.candidate is not None
    assert decision.candidate.candidate_id == legal.candidate_id
    assert (
        decision.rejection_reasons[out_of_band.candidate_id]
        == "LIVE_UNIT_PRICE_OUT_OF_BOUNDS"
    )


@pytest.mark.parametrize("side", ("YES", "NO"))
@pytest.mark.parametrize(
    ("price", "q"),
    (("0.05", 0.20), ("0.95", 1.0)),
)
def test_global_single_order_buy_price_band_is_inclusive(side, price, q):
    candidate = _global_candidate(
        candidate_id=f"boundary-{side}-{price}",
        family=f"boundary-{side}-{price}-family",
        side=side,
        q=q,
        levels=((price, "1000"),),
    )

    decision = _global_select((candidate,))

    assert decision.candidate is candidate
    assert decision.cost_usd / decision.shares == Decimal(price)


@pytest.mark.parametrize("side", ("YES", "NO"))
def test_global_single_order_buy_rejects_legal_limit_with_illegal_fill_vwap(side):
    out_of_band = _global_candidate(
        candidate_id=f"illegal-fill-vwap-{side}",
        family=f"illegal-fill-vwap-{side}-family",
        side=side,
        q=0.80,
        levels=(("0.03", "1000"), ("0.05", "1000")),
    )
    legal = _global_candidate(
        candidate_id=f"legal-fill-vwap-{side}",
        family=f"legal-fill-vwap-{side}-family",
        side=side,
        q=0.75,
        levels=(("0.06", "1000"),),
    )

    decision = _global_select((out_of_band, legal))

    assert decision.candidate is legal
    assert (
        decision.rejection_reasons[out_of_band.candidate_id]
        == "LIVE_UNIT_PRICE_OUT_OF_BOUNDS"
    )


@pytest.mark.parametrize("side", ("YES", "NO"))
def test_global_single_order_buy_price_band_applies_to_raw_limit_not_fee_vwap(side):
    candidate = _global_candidate(
        candidate_id=f"fee-boundary-{side}",
        family=f"fee-boundary-{side}-family",
        side=side,
        q=1.0,
        levels=(("0.95", "100"),),
        fee="0.02",
    )

    decision = _global_select((candidate,))

    assert decision.candidate is candidate
    assert decision.limit_price == Decimal("0.95")
    assert decision.cost_usd / decision.shares > Decimal("0.95")


@pytest.mark.parametrize("side", ("YES", "NO"))
def test_global_single_order_buy_never_crosses_illegal_deep_limit(side):
    candidate = _global_candidate(
        candidate_id=f"mixed-limit-{side}",
        family=f"mixed-limit-{side}-family",
        side=side,
        q=0.99,
        levels=(("0.94", "100"), ("0.96", "10")),
    )

    decision = _global_select(
        (candidate,),
        floor="1000",
        ceiling="1000",
        cash="1000",
        cap="200",
    )

    assert decision.candidate is candidate
    assert decision.shares == Decimal("100")
    assert decision.limit_price == Decimal("0.94")


def test_global_single_order_rejects_expired_quote_before_economics():
    expired = replace(
        _global_candidate(candidate_id="expired", family="a", side="YES", q=0.99),
        book_captured_at_utc=_DECISION_AT - timedelta(seconds=2),
    )
    valid_no = _global_candidate(
        candidate_id="valid-no", family="b", side="NO", q=0.65
    )

    decision = _global_select((expired, valid_no))

    assert decision.candidate is None
    assert decision.no_trade_reason == "GLOBAL_EPOCH_SUPERSEDED"
    assert decision.rejection_reasons["expired"] == "QUOTE_EXPIRED"


def test_global_single_order_unknown_collateral_makes_every_candidate_unrankable():
    candidate = _global_candidate(
        candidate_id="yes", family="a", side="YES", q=0.70
    )
    decision = _global_select(
        (candidate,), witness=_global_witness(collateral="DEGRADED")
    )

    assert decision.candidate is None
    assert decision.no_trade_reason == "COLLATERAL_UNKNOWN"


def test_global_single_order_rejects_stale_wealth_values_not_bound_to_current_ledger():
    cheap_yes = _global_candidate(
        candidate_id="cheap-yes", family="a", side="YES", q=0.02,
        levels=(("0.005", "1000"),),
    )
    valid_no = _global_candidate(
        candidate_id="valid-no", family="b", side="NO", q=0.65,
        levels=(("0.60", "100"),),
    )
    stale = _global_witness(floor="100", ceiling="100", cash="100")
    current = _global_witness(floor="10", ceiling="190", cash="10")
    decision = _global_select(
        (cheap_yes, valid_no),
        witness=stale,
        current_wealth_identity=current.economic_identity,
    )

    assert decision.candidate is None
    assert decision.no_trade_reason == "CAPITAL_IDENTITY_SUPERSEDED"


def test_global_single_order_does_not_couple_unmodelled_portfolio_upside_to_win():
    candidate = _global_candidate(
        candidate_id="yes", family="a", side="YES", q=0.70
    )
    cash_only = _global_select((candidate,), floor="50", ceiling="50")
    unrelated_upside = _global_select((candidate,), floor="50", ceiling="150")

    assert unrelated_upside.candidate is candidate
    assert unrelated_upside.shares == cash_only.shares
    assert unrelated_upside.robust_delta_log_wealth == cash_only.robust_delta_log_wealth


def test_global_single_order_maximizes_authority_bound_log_growth_rate():
    slow = _global_candidate(
        candidate_id="higher-growth", family="a", side="YES", q=0.74
    )
    fast = _global_candidate(
        candidate_id="lower-growth", family="b", side="NO", q=0.60
    )
    fast_score = _global_score(fast)
    decision = _global_select(
        (slow, fast),
        resolution_hours_by_family={"a": 48.0, "b": 12.0},
    )

    assert decision.robust_delta_log_wealth == fast_score.robust_delta_log_wealth
    assert decision.candidate.candidate_id == "lower-growth"
    assert decision.capital_lock_hours == 12.0
    assert decision.robust_log_growth_per_hour == pytest.approx(
        fast_score.robust_delta_log_wealth / 12.0
    )
    selected = next(
        evaluation
        for evaluation in decision.candidate_evaluations
        if evaluation.status == "SELECTED"
    )
    assert selected.capital_action_mode == "SETTLEMENT_LOCKED_BUY"
    assert selected.resolution_at_utc == decision.resolution_at_utc
    assert selected.capital_lock_hours == decision.capital_lock_hours
    assert (
        selected.robust_log_growth_per_hour
        == decision.robust_log_growth_per_hour
    )


def test_global_single_order_duration_is_universe_bound_not_candidate_authored():
    assert "capital_release_at_utc" not in S.GlobalSingleOrderCandidate.__dataclass_fields__
    assert (
        "family_resolution_at_utc"
        in S.GlobalAuctionUniverseWitness.__dataclass_fields__
    )
    assert (
        "robust_log_growth_per_hour"
        in S.GlobalSingleOrderDecision.__dataclass_fields__
    )


def test_global_single_order_nonpositive_capital_horizon_invalidates_epoch():
    buy = _global_candidate(
        candidate_id="elapsed-horizon",
        family="elapsed",
        side="YES",
        q=0.75,
    )
    sell = _global_sell_candidate(
        candidate_id="elapsed-sell-horizon",
        family="elapsed-sell",
        side="YES",
        held_q=0.15,
        bids=(("0.40", "4"), ("0.30", "6")),
        shares="10",
    )

    buy_decision = _global_select(
        (buy,),
        resolution_hours_by_family={"elapsed": 0.0},
    )
    sell_decision = _global_select(
        (sell,),
        resolution_hours_by_family={"elapsed-sell": 0.0},
    )

    for candidate, decision in ((buy, buy_decision), (sell, sell_decision)):
        assert decision.candidate is None
        assert decision.no_trade_reason == "GLOBAL_EPOCH_SUPERSEDED"
        assert decision.rejection_reasons[candidate.candidate_id] == (
            "CAPITAL_HORIZON_NON_POSITIVE"
        )


def test_global_single_order_rejects_probability_from_one_bin_welded_to_another_token():
    cheap_yes = _global_candidate(
        candidate_id="cheap-low-hit-yes",
        family="a",
        side="YES",
        q=0.002,
        levels=(("0.005", "1000"),),
    )
    probability = _global_probability_witness(cheap_yes)
    wrong_binding = probability.bindings[1]
    forged_curve = _global_curve(
        side="YES",
        token=wrong_binding.yes_token_id,
        levels=(("0.005", "1000"),),
    )
    forged = replace(
        cheap_yes,
        token_id=wrong_binding.yes_token_id,
        executable_cost_curve=forged_curve,
        book_snapshot_id=forged_curve.snapshot_id,
        execution_curve_identity=S.executable_curve_identity(forged_curve),
    )
    valid_no = _global_candidate(
        candidate_id="valid-no",
        family="b",
        side="NO",
        q=0.65,
        levels=(("0.60", "100"),),
    )

    decision = _global_select((forged, valid_no))

    assert decision.candidate.candidate_id == "valid-no"
    assert (
        decision.rejection_reasons["cheap-low-hit-yes"]
        == "JOINT_Q_MEMBERSHIP_MISMATCH"
    )


def test_global_single_order_rejects_external_current_authority_alpha_drift():
    tail_yes = _global_candidate(
        candidate_id="tail-yes",
        family="a",
        side="YES",
        q=0.03,
        levels=(("0.005", "1000"),),
    )
    tail_samples = np.concatenate(
        (np.full(20, 0.001, dtype=np.float64), np.full(380, 0.03, dtype=np.float64))
    )
    tail_yes = _replace_global_q_samples(tail_yes, tail_samples)
    valid_no = _global_candidate(
        candidate_id="valid-no", family="b", side="NO", q=0.65,
        levels=(("0.60", "100"),),
    )

    authoritative = _global_select((tail_yes, valid_no))
    witnesses = {
        "a": _global_probability_witness(tail_yes),
        "b": _global_probability_witness(valid_no),
    }
    current_probabilities = {
        family: S.CurrentFamilyProbabilityAuthority.from_witness(witness)
        for family, witness in witnesses.items()
    }
    current_probabilities["a"] = replace(
        current_probabilities["a"], band_alpha=0.25
    )
    forged = _global_select(
        (tail_yes, valid_no),
        probability_witnesses=witnesses,
        current_probabilities=current_probabilities,
    )

    assert authoritative.candidate.candidate_id == "valid-no"
    assert forged.candidate is None
    assert forged.no_trade_reason == "GLOBAL_EPOCH_SUPERSEDED"
    assert forged.rejection_reasons["tail-yes"] == "PROBABILITY_AUTHORITY_SUPERSEDED"


def test_global_single_order_ineligible_candidate_cannot_veto_survivor_band():
    excluded = _global_candidate(
        candidate_id="excluded-day0",
        family="a",
        side="YES",
        q=0.90,
        reason="DAY0_OBSERVATION_UNAVAILABLE",
    )
    excluded = _replace_global_band_alpha(excluded, 0.10)
    valid_no = _global_candidate(
        candidate_id="valid-no", family="b", side="NO", q=0.65,
        levels=(("0.60", "100"),),
    )

    decision = _global_select((excluded, valid_no))

    assert decision.candidate.candidate_id == "valid-no"
    assert decision.rejection_reasons["excluded-day0"] == "DAY0_OBSERVATION_UNAVAILABLE"


def test_global_single_order_eligible_candidates_with_different_band_alpha_fail_closed():
    yes = _replace_global_band_alpha(
        _global_candidate(candidate_id="yes", family="a", side="YES", q=0.70),
        0.10,
    )
    no = _global_candidate(candidate_id="no", family="b", side="NO", q=0.70)

    decision = _global_select((yes, no))

    assert decision.candidate is None
    assert decision.no_trade_reason == "BAND_ALPHA_MISMATCH"
    assert set(decision.rejection_reasons.values()) == {"BAND_ALPHA_MISMATCH"}


def test_global_single_order_same_alpha_compares_distinct_current_probability_bases():
    forecast = _replace_global_band_basis(
        _global_candidate(candidate_id="forecast", family="a", side="YES", q=0.70),
        "current_coherent_settlement_simplex_v1",
    )
    day0 = _replace_global_band_basis(
        _global_candidate(candidate_id="day0", family="b", side="NO", q=0.65),
        "current_coherent_day0_remaining_finite_evidence_v2",
    )

    decision = _global_select((forecast, day0))

    assert decision.candidate is not None
    assert decision.no_trade_reason is None
    assert "BAND_ALPHA_MISMATCH" not in decision.rejection_reasons.values()


def test_global_single_order_matches_exhaustive_grid_on_random_full_depth_books():
    rng = np.random.default_rng(20260710)
    for index in range(16):
        p0 = round(float(rng.uniform(0.08, 0.45)), 3)
        p1 = round(float(rng.uniform(p0 + 0.01, min(0.80, p0 + 0.25))), 3)
        candidate = _global_candidate(
            candidate_id=f"c{index}",
            family=f"f{index}",
            side="YES" if index % 2 == 0 else "NO",
            q=0.5,
            levels=((str(p0), str(rng.uniform(0.5, 4.0))), (str(p1), "30")),
            fee="0.05",
        )
        q_samples = np.clip(rng.normal(rng.uniform(p1 + 0.05, 0.90), 0.025, 400), 0, 1)
        candidate = _replace_global_q_samples(candidate, q_samples)
        oracle = _global_exact_oracle(candidate, cap="3")
        score = _global_score(candidate, cap="3")
        if oracle is None or oracle[0] <= 0.0 or oracle[1] <= 0.0:
            assert score.candidate is None
        else:
            assert score.shares == oracle[4]
            assert score.cost_usd == oracle[3]
            assert abs(score.robust_delta_log_wealth - oracle[0]) < 1e-12


def test_global_single_order_draw_permutation_is_invariant():
    q = np.linspace(0.55, 0.80, 400, dtype=np.float64)
    candidate = _replace_global_q_samples(
        _global_candidate(candidate_id="c", family="f", side="YES", q=0.5), q
    )
    permuted = _replace_global_q_samples(candidate, q[::-1].copy())
    left = _global_select((candidate,))
    right = _global_select((permuted,))

    assert left.shares == right.shares
    assert left.cost_usd == right.cost_usd
    assert left.robust_delta_log_wealth == right.robust_delta_log_wealth


def test_global_single_order_endowment_bound_is_below_every_frechet_coupling():
    candidate = _global_candidate(
        candidate_id="c", family="f", side="YES", q=0.70,
        levels=(("0.40", "100"),),
    )
    shares = Decimal("5")
    bound, _ev, _eff, cost = S._single_order_metrics(
        candidate,
        q_samples=_global_probability_projection(candidate)[0],
        shares=shares,
        wealth_floor_usd=Decimal("50"),
        wealth_ceiling_usd=Decimal("150"),
        alpha=ALPHA,
    )
    q = 0.70
    low_mass = 0.50
    win_low_min = max(0.0, q + low_mass - 1.0)
    win_low_max = min(q, low_mass)
    win_inc = {
        wealth: np.log((wealth - float(cost) + float(shares)) / wealth)
        for wealth in (50.0, 150.0)
    }
    loss_inc = {
        wealth: np.log((wealth - float(cost)) / wealth)
        for wealth in (50.0, 150.0)
    }
    for win_low in np.linspace(win_low_min, win_low_max, 101):
        true_du = (
            win_low * win_inc[50.0]
            + (q - win_low) * win_inc[150.0]
            + (low_mass - win_low) * loss_inc[50.0]
            + (1.0 - q - low_mass + win_low) * loss_inc[150.0]
        )
        assert bound <= true_du + 1e-15


def test_global_single_order_rejects_contingent_maker_asset_shape():
    with pytest.raises(ValueError, match="immediate taker-limit"):
        replace(
            _global_candidate(candidate_id="c", family="f", side="YES", q=0.70),
            execution_mode="MAKER",  # type: ignore[arg-type]
        )


def test_var_nonconcave_where_cvar_stays_concave():
    # Direct counterexample (consult REV-2): the α-quantile (VaR) of concave draws is NOT
    # concave, so a unimodality-only optimizer on it can fail; lower-tail CVaR stays concave.
    t = np.linspace(0.0, 1.0, 201)
    a = np.array([2.777, 2.91, 1.861, 0.973])
    mm = np.array([0.943, 0.551, 0.12, 0.472])
    b = np.array([0.779, 0.868, -0.284, 0.143])
    draws = np.array([-a[j] * (t - mm[j]) ** 2 + b[j] for j in range(4)])  # 4 concave-in-t draws
    M = draws.T  # (nt, 4)
    w = np.ones(4)
    alpha = 0.3
    var = np.quantile(M, alpha, axis=1)
    cvar = np.array([S._lower_cvar(M[i], w, alpha) for i in range(len(t))])

    def viol(f):
        return sum(1 for i in range(1, len(f) - 1) if f[i] < 0.5 * (f[i - 1] + f[i + 1]) - 1e-9)

    assert viol(var) >= 2, "expected the VaR/quantile objective to be non-concave"
    assert viol(cvar) == 0, "the CVaR objective must stay concave (the solver relies on it)"
