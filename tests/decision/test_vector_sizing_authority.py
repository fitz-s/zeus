# Created: 2026-06-14
# Last reused or audited: 2026-07-09
# Authority basis: docs/rebuild/consult_build_spec.md
#   ("Create src/decision/payoff_vector.py" sizing block lines 776-791: robust_delta_u over
#   band samples + the EXISTING FamilyPayoffMatrix ΔU; s_star = argmax_s
#   robust_delta_u(candidate, s), NOT a binary f_star; the live candidate pass 793-802) +
#   the Stage 8 RED-on-revert names lines 1180-1181
#   (test_family_total_uses_vector_argmax_not_binary_kelly;
#   test_correlated_existing_position_reduces_delta_u_size) + the live signal 1184 (selected
#   candidate has optimal_stake / delta_u). Reconciled against
#   docs/evidence/qkernel_rebuild/spec_vs_live_drift_ledger.md (GREENFIELD — the size comes
#   from the FamilyPayoffMatrix ΔU argmax over band-sample q draws against the existing
#   exposure, REUSING src/strategy/utility_ranker.py's ΔU; it is NOT the binary scalar
#   f_star = (q - c)/(1 - c) the reactor seam :8632 used. The reactor demotion is Stage 11).
"""RED-on-revert contract tests for the payoff_vector vector SIZING (Stage 8a).

Two spec-named tests fail if the corrected transformation is reverted to the broken binary
Kelly behavior the spec replaces:

  * ``test_family_total_uses_vector_argmax_not_binary_kelly`` — the optimal stake is
    ``s* = argmax_s robust_delta_u(candidate, s)`` over band-sample q draws + the
    FamilyPayoffMatrix ΔU, NOT the closed-form binary Kelly ``f* = (q - c)/(1 - c)`` times
    bankroll. We prove the chosen stake maximizes the robust ΔU objective (no other probe
    stake beats it) AND that the binary-Kelly notional is a DIFFERENT number — so a
    reversion to ``family_total = bankroll * f*`` would size differently and the test fails.

  * ``test_correlated_existing_position_reduces_delta_u_size`` — an existing position
    correlated with the candidate (it wins on the SAME outcomes) lowers the candidate's
    optimal stake versus a flat (no-exposure) baseline, by the concavity of the log in the
    ΔU objective. RED-on-revert: a binary Kelly f* ignores existing exposure entirely
    (f* depends only on q and c), so a reversion would size IDENTICALLY with and without
    the correlated position — this test holds q / c fixed and varies only the exposure,
    proving the size responds to exposure (which only the ΔU objective does).
"""
from __future__ import annotations

import math
from datetime import date, timedelta
from decimal import Decimal
from typing import Mapping

import numpy as np
import pytest

from src.config import City
from src.contracts.executable_cost_curve import (
    BookLevel,
    ExecutableCostCurve,
    FeeModel,
)
from src.contracts.native_side_candidate import NativeSideCandidate
from src.decision.payoff_vector import (
    _PreparedSizing,
    _candidate_guarded_pi,
    _candidate_pi_matrix,
    _draw_to_pi,
    _local_maximum_indexes,
    optimize_vector_stake,
    robust_delta_u,
)
from src.probability.event_resolution import EventResolution, event_resolution_for_city
from src.probability.joint_q import JointQ
from src.probability.joint_q_band import JointQBand
from src.probability.outcome_space import (
    OutcomeBin,
    OutcomeSpace,
    compute_topology_hash,
)
from src.strategy.utility_ranker import (
    OUTSIDE_OUTCOME,
    FamilyPayoffMatrix,
    PortfolioExposureVector,
    _delta_u_at_stake,
    effective_outcome_pi,
)


# ---------------------------------------------------------------------------
# Fixtures — real Omega / JointQ / JointQBand + a real NativeSideCandidate with a
# real ExecutableCostCurve (the ΔU stake-sweep walks the curve).
# ---------------------------------------------------------------------------

def _resolution(city_name: str = "Tokyo", metric: str = "high") -> EventResolution:
    city = City(
        name=city_name,
        lat=35.68,
        lon=139.69,
        timezone="Asia/Tokyo",
        settlement_unit="C",
        cluster="asia",
        wu_station="RJTT",
        settlement_source_type="wu_icao",
    )
    return event_resolution_for_city(city, date(2026, 6, 14), metric)


def _bin(bin_id: str, lo, hi, label: str, rule: str, *, executable: bool = True) -> OutcomeBin:
    return OutcomeBin(
        bin_id=bin_id,
        condition_id=f"cond-{bin_id}",
        label=label,
        lower_native=lo,
        upper_native=hi,
        yes_token_id=f"yes-{bin_id}",
        no_token_id=f"no-{bin_id}",
        executable=executable,
        rounding_rule=rule,
    )


def _complete_bins(rule: str) -> tuple[OutcomeBin, ...]:
    """A complete °C integer partition: (-inf,20], 21..29, [30,+inf)."""
    bins = [_bin("b_low", None, 20.0, "20°C or below", rule, executable=False)]
    for t in range(21, 30):
        bins.append(_bin(f"b{t}", float(t), float(t), f"{t}°C", rule))
    bins.append(_bin("b_high", 30.0, None, "30°C or above", rule, executable=False))
    return tuple(bins)


def _outcome_space(family_id: str = "tokyo-high") -> OutcomeSpace:
    resolution = _resolution()
    rule = resolution.rounding_rule
    bins = _complete_bins(rule)
    space = OutcomeSpace(
        family_id=family_id,
        resolution=resolution,
        bins=bins,
        topology_hash=compute_topology_hash(family_id, resolution, bins),
    )
    space.validate()
    return space


def _joint_q(space: OutcomeSpace, q_by_bin: Mapping[str, float]) -> JointQ:
    bin_ids = [b.bin_id for b in space.bins]
    explicit = {bid: float(q_by_bin.get(bid, 0.0)) for bid in bin_ids}
    explicit_total = sum(q_by_bin.values())
    assert explicit_total <= 1.0 + 1e-9
    free_bins = [bid for bid in bin_ids if bid not in q_by_bin]
    residual = max(1.0 - explicit_total, 0.0)
    per_free = residual / len(free_bins) if free_bins else 0.0
    masses = {bid: (explicit[bid] if bid in q_by_bin else per_free) for bid in bin_ids}
    q = np.array([masses[bid] for bid in bin_ids], dtype=float)
    q = q / q.sum()
    q_by_bin_id = {bid: float(m) for bid, m in zip(bin_ids, q)}
    jq = JointQ(
        omega=space,
        q=q,
        q_by_bin_id=q_by_bin_id,
        predictive_distribution_id="pd-test",
        q_source="SETTLEMENT_STATION_NORMAL_V1",
        q_sum=float(q.sum()),
        identity_hash="jq-test-identity",
    )
    jq.assert_valid()
    return jq


def _band_from_point(jq: JointQ, *, n_draws: int = 2000, jitter: float = 0.01,
                     alpha: float = 0.05, seed: int = 11) -> JointQBand:
    """A real JointQBand: Dirichlet jitters around the point q, every row on the simplex."""
    rng = np.random.default_rng(seed)
    q = np.asarray(jq.q, dtype=float)
    conc = q * (1.0 / max(jitter, 1e-6)) + 1e-6
    samples = rng.dirichlet(conc, size=n_draws)
    band = JointQBand(
        joint_q=jq,
        samples=samples,
        q_lcb=np.quantile(samples, alpha, axis=0),
        q_ucb=np.quantile(samples, 1.0 - alpha, axis=0),
        alpha=alpha,
        basis="PARAMETER_POSTERIOR_SIMPLEX_V1",
        sample_hash="band-test-hash",
    )
    band.assert_valid()
    return band


def _yes_curve(bin_id: str, *, price: str = "0.30", depth: str = "2000") -> ExecutableCostCurve:
    """A deep one-level YES BUY curve at ``price`` (the candidate's executable cost curve)."""
    return ExecutableCostCurve(
        token_id=f"yes-{bin_id}",
        side="YES",
        snapshot_id="snap-1",
        book_hash=f"hash-{bin_id}",
        levels=(BookLevel(price=Decimal(price), size=Decimal(depth)),),
        fee_model=FeeModel(fee_rate=Decimal("0.05")),
        min_tick=Decimal("0.01"),
        min_order_size=Decimal("1"),
        quote_ttl=timedelta(seconds=2),
    )


def _yes_candidate(
    space: OutcomeSpace, bin_id: str, *, q_point: float, q_lcb: float, price: str = "0.30"
) -> NativeSideCandidate:
    """A tradeable YES candidate with a real native cost curve for the ΔU stake-sweep."""
    return NativeSideCandidate.tradeable(
        family_key=space.family_id,
        bin_id=bin_id,
        side="YES",
        token_id=f"yes-{bin_id}",
        condition_id=f"cond-{bin_id}",
        q_point=q_point,
        q_lcb=q_lcb,
        probability_uncertainty=None,
        executable_cost_curve=_yes_curve(bin_id, price=price),
        forecast_snapshot_id="fc-1",
        market_snapshot_id="mk-1",
        hypothesis_id=f"hyp-{bin_id}-YES",
    )


def _matrix(space: OutcomeSpace) -> FamilyPayoffMatrix:
    """FamilyPayoffMatrix over the EXECUTABLE bins plus OUTSIDE (Hidden #5)."""
    bins = [b.bin_id for b in space.bins if b.executable]
    return FamilyPayoffMatrix.over_bins(bins)


@pytest.mark.parametrize(
    ("side", "guarded_q"),
    (("YES", None), ("NO", None), ("YES", 0.37), ("NO", 0.63)),
)
def test_candidate_pi_matrix_matches_scalar_reference(side, guarded_q):
    space = _outcome_space()
    jq = _joint_q(space, {"b25": 0.55, "b26": 0.20})
    band = _band_from_point(jq, n_draws=40, jitter=0.008)
    matrix = _matrix(space)
    curve = ExecutableCostCurve(
        token_id=f"{side.lower()}-b25",
        side=side,
        snapshot_id="snap-pi",
        book_hash=f"hash-pi-{side}",
        levels=(BookLevel(price=Decimal("0.30"), size=Decimal("2000")),),
        fee_model=FeeModel(fee_rate=Decimal("0.05")),
        min_tick=Decimal("0.01"),
        min_order_size=Decimal("1"),
        quote_ttl=timedelta(seconds=2),
    )
    candidate = NativeSideCandidate.tradeable(
        family_key=space.family_id,
        bin_id="b25",
        side=side,
        token_id=curve.token_id,
        condition_id="cond-b25",
        q_point=0.55,
        q_lcb=0.50,
        probability_uncertainty=None,
        executable_cost_curve=curve,
        forecast_snapshot_id="fc-pi",
        market_snapshot_id="mk-pi",
        hypothesis_id=f"hyp-pi-{side}",
    )

    def _scalar(samples):
        rows = []
        for draw in samples:
            base = _draw_to_pi(draw, space, matrix)
            effective = (
                _candidate_guarded_pi(
                    candidate,
                    matrix,
                    base,
                    guarded_payoff_q_lcb=guarded_q,
                )
                if guarded_q is not None
                else effective_outcome_pi(candidate, matrix, base)
            )
            rows.append([effective[outcome] for outcome in matrix.outcomes])
        return np.asarray(rows)

    actual = _candidate_pi_matrix(
        candidate,
        samples=band.samples,
        omega=space,
        matrix=matrix,
        guarded_payoff_q_lcb=guarded_q,
    )
    np.testing.assert_allclose(actual, _scalar(band.samples), rtol=0.0, atol=5e-15)

    own_only = np.zeros((1, len(space.bins)), dtype=float)
    own_only[0, next(i for i, b in enumerate(space.bins) if b.bin_id == "b25")] = 1.0
    degenerate = _candidate_pi_matrix(
        candidate,
        samples=own_only,
        omega=space,
        matrix=matrix,
        guarded_payoff_q_lcb=guarded_q,
    )
    np.testing.assert_allclose(degenerate, _scalar(own_only), rtol=0.0, atol=5e-15)


@pytest.mark.parametrize("side", ["YES", "NO"])
def test_robust_delta_u_walks_cost_curve_once_without_numeric_drift(monkeypatch, side):
    """One candidate/stake has one cost; outcomes only choose win versus loss."""
    space = _outcome_space()
    jq = _joint_q(space, {"b25": 0.55, "b26": 0.20})
    band = _band_from_point(jq, n_draws=40, jitter=0.008)
    matrix = _matrix(space)
    exposure = PortfolioExposureVector.flat(matrix, baseline=Decimal("1000"))
    curve = ExecutableCostCurve(
        token_id=f"{side.lower()}-b25",
        side=side,
        snapshot_id="snap-1",
        book_hash=f"hash-b25-{side}",
        levels=(
            BookLevel(price=Decimal("0.20"), size=Decimal("50")),
            BookLevel(price=Decimal("0.30"), size=Decimal("1950")),
        ),
        fee_model=FeeModel(fee_rate=Decimal("0.05")),
        min_tick=Decimal("0.01"),
        min_order_size=Decimal("1"),
        quote_ttl=timedelta(seconds=2),
    )
    candidate = NativeSideCandidate.tradeable(
        family_key=space.family_id,
        bin_id="b25",
        side=side,
        token_id=curve.token_id,
        condition_id="cond-b25",
        q_point=0.55,
        q_lcb=0.50,
        probability_uncertainty=None,
        executable_cost_curve=curve,
        forecast_snapshot_id="fc-1",
        market_snapshot_id="mk-1",
        hypothesis_id=f"hyp-b25-{side}",
    )
    stake = Decimal("37")

    cost = Decimal(str(curve.avg_cost(stake).value))
    win_profit = stake * (Decimal("1") - cost) / cost
    loss = -stake
    per_draw = []
    for draw in band.samples:
        pi = effective_outcome_pi(candidate, matrix, _draw_to_pi(draw, space, matrix))
        total = 0.0
        for outcome in matrix.outcomes:
            wins = outcome == candidate.bin_id if side == "YES" else outcome != candidate.bin_id
            payoff = win_profit if wins else loss
            wealth = exposure.a(outcome)
            total += float(pi[outcome]) * (
                math.log(float(wealth + payoff)) - math.log(float(wealth))
            )
        per_draw.append(total)
    expected = float(np.quantile(per_draw, band.alpha))

    calls = 0
    original_avg_cost = ExecutableCostCurve.avg_cost

    def counted_avg_cost(self, stake_usd):
        nonlocal calls
        calls += 1
        return original_avg_cost(self, stake_usd)

    monkeypatch.setattr(ExecutableCostCurve, "avg_cost", counted_avg_cost)
    actual = robust_delta_u(
        candidate,
        stake,
        band=band,
        omega=space,
        matrix=matrix,
        exposure=exposure,
    )

    assert actual == pytest.approx(expected, abs=1e-15)
    assert calls == 1

    prepared = _PreparedSizing(
        candidate,
        band=band,
        omega=space,
        matrix=matrix,
        exposure=exposure,
        alpha=band.alpha,
    )
    stakes = [Decimal("1"), stake, Decimal("999"), Decimal("1000")]
    scalar = np.asarray([prepared.robust_at(value) for value in stakes])
    calls = 0
    batched = prepared.robust_many(stakes)
    np.testing.assert_allclose(batched, scalar, rtol=0.0, atol=5e-15)
    assert int(np.argmax(batched)) == int(np.argmax(scalar))
    assert calls == len(stakes)

    low_exposure = PortfolioExposureVector.flat(matrix, baseline=Decimal("100"))
    ruin_prepared = _PreparedSizing(
        candidate,
        band=band,
        omega=space,
        matrix=matrix,
        exposure=low_exposure,
        alpha=band.alpha,
    )
    ruin_stakes = [Decimal("1"), Decimal("200")]
    ruin_scalar = np.asarray(
        [ruin_prepared.robust_at(value) for value in ruin_stakes]
    )
    ruin_batched = ruin_prepared.robust_many(ruin_stakes)
    np.testing.assert_allclose(ruin_batched, ruin_scalar, rtol=0.0, atol=5e-15)
    assert int(np.argmax(ruin_batched)) == int(np.argmax(ruin_scalar))

    mixed_exposure = PortfolioExposureVector.from_outcome_wealth(
        matrix,
        baseline=Decimal("1000"),
        extra_by_outcome={"b25": Decimal("400"), "b26": Decimal("125")},
    )
    mixed_prepared = _PreparedSizing(
        candidate,
        band=band,
        omega=space,
        matrix=matrix,
        exposure=mixed_exposure,
        alpha=band.alpha,
    )
    reference = []
    for draw in band.samples:
        pi = effective_outcome_pi(candidate, matrix, _draw_to_pi(draw, space, matrix))
        reference.append(
            _delta_u_at_stake(candidate, matrix, pi, mixed_exposure, stake)
        )
    assert mixed_prepared.robust_at(stake) == pytest.approx(
        float(np.quantile(reference, band.alpha)), abs=1e-15
    )


# ===========================================================================
# RED-on-revert #1 (spec line 1180): family total = vector argmax, not binary Kelly.
# ===========================================================================

def test_family_total_uses_vector_argmax_not_binary_kelly():
    """The optimal stake maximizes robust ΔU over band draws — NOT bankroll * binary f*.

    RED-on-revert: the spec replaces the scalar binary Kelly notional
    ``family_total = bankroll * f*`` (reactor seam :8632, f* = (q - c)/(1 - c)) with the
    vector ΔU argmax. We prove:
      1. the chosen s* genuinely maximizes robust_delta_u (no probed stake beats it), AND
      2. the binary-Kelly notional is a DIFFERENT number than s*, so a reversion to
         bankroll * f* would size differently.
    """
    space = _outcome_space()
    jq = _joint_q(space, {"b25": 0.55, "b26": 0.20})
    band = _band_from_point(jq, jitter=0.008)
    matrix = _matrix(space)
    bankroll = Decimal("1000")
    exposure = PortfolioExposureVector.flat(matrix, baseline=bankroll)

    # YES_25: q ~ 0.55, native ask 0.30 -> strong positive edge.
    cand = _yes_candidate(space, "b25", q_point=0.55, q_lcb=0.50, price="0.30")

    s_star, optimal_du, du_at_min = optimize_vector_stake(
        cand, band=band, omega=space, matrix=matrix, exposure=exposure,
        max_stake_usd=bankroll,
    )

    # It is a real trade: positive robust ΔU at a positive stake.
    assert s_star > Decimal("0")
    assert optimal_du > 0.0
    assert du_at_min > 0.0  # ΔU positive even at the venue min order

    # (1) s* maximizes robust_delta_u: no nearby probe stake beats it.
    def _ru(stake: Decimal) -> float:
        return robust_delta_u(
            cand, stake, band=band, omega=space, matrix=matrix, exposure=exposure
        )
    here = _ru(s_star)
    for probe in (s_star * Decimal("0.5"), s_star * Decimal("1.5"),
                  s_star + Decimal("50"), max(Decimal("1"), s_star - Decimal("50"))):
        if probe > Decimal("0"):
            assert _ru(probe) <= here + 1e-9, (
                f"a probe stake {probe} beat the claimed argmax {s_star}"
            )

    # (2) The binary-Kelly notional is a DIFFERENT number. f* = (q - c)/(1 - c) with the
    # all-in cost c (price + p(1-p) fee); family_total = bankroll * f* is the seam :8632
    # transform the spec replaces. The vector ΔU argmax does not equal it.
    c = 0.30 + 0.05 * 0.30 * 0.70  # all-in cost at the best level
    f_star = (0.55 - c) / (1.0 - c)
    binary_kelly_notional = Decimal(str(bankroll)) * Decimal(str(f_star))
    # They must not coincide (the vector sizing is depth-bounded family log-growth, not the
    # closed-form binary fraction). A loose tolerance proves they are genuinely different.
    assert abs(float(s_star) - float(binary_kelly_notional)) > 1.0, (
        f"vector argmax s*={s_star} coincided with binary-Kelly notional "
        f"{binary_kelly_notional} — the sizing reverted to f*"
    )


# ===========================================================================
# RED-on-revert #2 (spec line 1181): correlated existing position reduces ΔU size.
# ===========================================================================

def test_correlated_existing_position_reduces_delta_u_size():
    """An existing position that wins on the SAME outcome shrinks the optimal stake.

    RED-on-revert: a binary Kelly f* = (q - c)/(1 - c) depends ONLY on q and c — it is
    BLIND to existing exposure, so it would size identically here. The ΔU objective measures
    marginal log-growth against the existing wealth-by-outcome A_y, so a position already
    winning on the candidate's win-outcome makes the marginal dollar worth LESS (log
    concavity) and the optimal stake falls. We hold q / c / band fixed and vary ONLY the
    exposure on the candidate's own winning bin.
    """
    space = _outcome_space()
    jq = _joint_q(space, {"b25": 0.55, "b26": 0.20})
    band = _band_from_point(jq, jitter=0.008)
    matrix = _matrix(space)
    bankroll = Decimal("1000")

    cand = _yes_candidate(space, "b25", q_point=0.55, q_lcb=0.50, price="0.30")

    # Flat baseline: no existing exposure on any outcome.
    flat_exposure = PortfolioExposureVector.flat(matrix, baseline=bankroll)
    s_flat, du_flat, _ = optimize_vector_stake(
        cand, band=band, omega=space, matrix=matrix, exposure=flat_exposure,
        max_stake_usd=bankroll,
    )

    # Correlated baseline: a large existing win already realized on b25 (the candidate's
    # OWN winning outcome). The candidate's extra win on b25 now adds to an already-large
    # A_b25, so its marginal log-utility is lower -> smaller optimal stake.
    correlated_exposure = PortfolioExposureVector.from_outcome_wealth(
        matrix,
        baseline=bankroll,
        extra_by_outcome={"b25": Decimal("4000")},  # big existing exposure on the win bin
    )
    s_corr, du_corr, _ = optimize_vector_stake(
        cand, band=band, omega=space, matrix=matrix, exposure=correlated_exposure,
        max_stake_usd=bankroll,
    )

    # Both still trade (the edge is real), but the correlated position sizes SMALLER.
    assert s_flat > Decimal("0")
    assert s_corr >= Decimal("0")
    assert s_corr < s_flat, (
        f"correlated existing position did NOT reduce the stake "
        f"(flat s*={s_flat}, correlated s*={s_corr}); the size is ignoring exposure "
        "(a binary-Kelly reversion)"
    )


def test_no_trade_when_robust_delta_u_nonpositive():
    """A candidate priced above its robust fair value yields s* = 0 (no-trade).

    The vector pass needs optimal_delta_u > 0; a YES_25 with q ~ 0.30 but a native ask of
    0.45 (cost above fair value) has no positive robust ΔU at any stake, so the optimizer
    returns a zero stake — the no-trade signal the live pass reads.
    """
    space = _outcome_space()
    jq = _joint_q(space, {"b25": 0.30, "b26": 0.20})
    band = _band_from_point(jq, jitter=0.01)
    matrix = _matrix(space)
    exposure = PortfolioExposureVector.flat(matrix, baseline=Decimal("1000"))

    cand = _yes_candidate(space, "b25", q_point=0.30, q_lcb=0.26, price="0.45")
    s_star, optimal_du, _ = optimize_vector_stake(
        cand, band=band, omega=space, matrix=matrix, exposure=exposure,
        max_stake_usd=Decimal("1000"),
    )
    assert s_star == Decimal("0")
    assert optimal_du <= 0.0


def test_quantile_optimizer_matches_global_oracle_with_multiple_local_maxima():
    """VaR sizing refines every coarse peak because its quantile need not be concave."""

    space = _outcome_space()
    bin_ids = [outcome.bin_id for outcome in space.bins]
    indexes = {bin_id: index for index, bin_id in enumerate(bin_ids)}
    samples = np.zeros((12, len(bin_ids)), dtype=float)
    low_draws = np.asarray(
        [
            [0.8563, 0.1031, 0.0406],
            [0.4775, 0.3657, 0.1568],
            [0.2680, 0.0002, 0.7318],
        ]
    )
    for row, masses in enumerate(low_draws):
        samples[row, indexes["b25"]] = masses[0]
        samples[row, indexes["b26"]] = masses[1]
        samples[row, indexes["b_low"]] = masses[2]
    samples[3:, indexes["b25"]] = 1.0

    mean_q = samples.mean(axis=0)
    joint_q = _joint_q(
        space,
        {bin_id: float(mean_q[index]) for index, bin_id in enumerate(bin_ids)},
    )
    alpha = 0.05
    band = JointQBand(
        joint_q=joint_q,
        samples=samples,
        q_lcb=np.quantile(samples, alpha, axis=0),
        q_ucb=np.quantile(samples, 1.0 - alpha, axis=0),
        alpha=alpha,
        basis="PARAMETER_POSTERIOR_SIMPLEX_V1",
        sample_hash="nonconcave-quantile-band",
    )
    band.assert_valid()

    curve = ExecutableCostCurve(
        token_id="yes-b25",
        side="YES",
        snapshot_id="snap-nonconcave",
        book_hash="book-nonconcave",
        levels=(BookLevel(price=Decimal("0.128"), size=Decimal("100")),),
        fee_model=FeeModel(fee_rate=Decimal("0")),
        min_tick=Decimal("0.001"),
        min_order_size=Decimal("1"),
        quote_ttl=timedelta(seconds=2),
    )
    candidate = NativeSideCandidate.tradeable(
        family_key=space.family_id,
        bin_id="b25",
        side="YES",
        token_id="yes-b25",
        condition_id="cond-b25",
        q_point=float(mean_q[indexes["b25"]]),
        q_lcb=float(np.quantile(samples[:, indexes["b25"]], alpha)),
        probability_uncertainty=None,
        executable_cost_curve=curve,
        forecast_snapshot_id="fc-nonconcave",
        market_snapshot_id="mk-nonconcave",
        hypothesis_id="hyp-nonconcave",
    )
    matrix = _matrix(space)
    wealth = {outcome: Decimal("3.76") for outcome in matrix.outcomes}
    wealth["b26"] = Decimal("3.93")
    wealth[OUTSIDE_OUTCOME] = Decimal("617.5")
    exposure = PortfolioExposureVector(wealth=wealth)
    max_stake = Decimal("3.7183")

    stake, utility, _ = optimize_vector_stake(
        candidate,
        band=band,
        omega=space,
        matrix=matrix,
        exposure=exposure,
        max_stake_usd=max_stake,
    )
    prepared = _PreparedSizing(
        candidate,
        band=band,
        omega=space,
        matrix=matrix,
        exposure=exposure,
        alpha=alpha,
    )
    lo = Decimal("0.128")
    oracle_stakes = [
        lo + (max_stake - lo) * Decimal(index) / Decimal(20_000)
        for index in range(20_001)
    ]
    oracle_values = prepared.robust_many(oracle_stakes)
    oracle_index = int(np.argmax(oracle_values))
    oracle_stake = oracle_stakes[oracle_index]
    oracle_utility = float(oracle_values[oracle_index])

    local_maxima = np.flatnonzero(
        (oracle_values[1:-1] > oracle_values[:-2])
        & (oracle_values[1:-1] >= oracle_values[2:])
    )
    assert len(local_maxima) >= 2
    assert utility == pytest.approx(oracle_utility, abs=1e-8)
    assert abs(float(stake - oracle_stake)) < 0.01


def test_quantile_optimizer_refines_finite_peak_between_ruin_nans():
    assert _local_maximum_indexes([float("nan"), 0.1, float("nan")]) == [1]
