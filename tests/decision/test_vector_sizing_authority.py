# Created: 2026-06-14
# Last reused or audited: 2026-06-14
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
