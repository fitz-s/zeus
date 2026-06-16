# Created: 2026-06-14
# Last reused or audited: 2026-06-14
# Authority basis: docs/rebuild/consult_build_spec.md
#   ("Create src/decision/payoff_vector.py" block lines 734-802: the edge calculation
#   759-774 — point_fair_value = q @ payoff, point_edge = point_fair_value -
#   route.avg_cost.value, sample_edges = band.samples @ payoff - route.avg_cost.value,
#   edge_lcb = np.quantile(sample_edges, alpha), with the YES_i reduction to q_i -
#   ask_yes_i and the NO_i reduction to (1 - q_i) - cost_not_i; the live candidate pass
#   793-802 where the scalar q-price is telemetry only) + the Stage 8 RED-on-revert names
#   lines 1178-1184 (test_edge_is_q_dot_payoff_minus_route_cost; scalar q-price logged
#   but not selected on). Reconciled against
#   docs/evidence/qkernel_rebuild/spec_vs_live_drift_ledger.md (GREENFIELD — the edge is
#   the VECTOR dot product q @ payoff - cost, the scalar q_i - price CANNOT select).
"""RED-on-revert contract tests for the payoff_vector EDGE (Stage 8a).

Two spec-named tests fail if the corrected transformation is reverted to the broken
scalar behavior the spec replaces:

  * ``test_edge_is_q_dot_payoff_minus_route_cost`` — the edge IS the Arrow-Debreu vector
    dot product ``q @ payoff - route.avg_cost.value`` (point) and
    ``quantile(band.samples @ payoff - cost, alpha)`` (lcb). For a NO_i candidate (payoff
    ``1 - e_i``) the vector edge equals ``(1 - q_i) - cost`` — the WHOLE other-bin basket
    value — which is DIFFERENT from the scalar ``q_i - cost`` a single-bin transform would
    produce. RED-on-revert: a reversion that computed ``q[bin] - cost`` (the bin's own
    mass minus cost) instead of ``q @ payoff - cost`` would give the wrong sign/magnitude
    for the NO side and the test fails.

  * ``test_scalar_q_minus_cost_cannot_select_candidate`` — a candidate whose SCALAR
    ``q_i - price`` looks positive but whose VECTOR ``edge_lcb`` / robust ΔU is not
    positive must NOT pass the live candidate gate; and a candidate that DOES pass passes
    on the vector quantities with the scalar trade_score never consulted. RED-on-revert:
    if the live pass were reverted to select on the scalar ``q - price``, the scalar-
    positive / vector-negative candidate would be admitted and the test fails.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Mapping

import numpy as np
import pytest

from src.config import City
from src.contracts.execution_price import ExecutionPrice
from src.decision.payoff_vector import (
    CandidateEconomics,
    CandidateRoute,
    build_candidate_route,
    edge_lower_bound,
    live_candidate_passes,
    optimize_vector_stake,
    point_fair_value,
    scalar_trade_score,
)
from src.execution.negrisk_routes import RouteCost
from src.probability.event_resolution import EventResolution, event_resolution_for_city
from src.probability.instruments import Instrument
from src.probability.joint_q import JointQ
from src.probability.joint_q_band import JointQBand
from src.probability.outcome_space import (
    OutcomeBin,
    OutcomeSpace,
    compute_topology_hash,
)
from src.strategy.utility_ranker import FamilyPayoffMatrix, PortfolioExposureVector


# ---------------------------------------------------------------------------
# Fixtures — a real complete Omega, a real JointQ, and a real JointQBand built the
# SAME way the joint_q / market_coherence contract tests build them.
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
    """A real JointQ over the Omega with a controlled (normalized) mass vector."""
    bin_ids = [b.bin_id for b in space.bins]
    explicit = {bid: float(q_by_bin.get(bid, 0.0)) for bid in bin_ids}
    explicit_total = sum(q_by_bin.values())
    assert explicit_total <= 1.0 + 1e-9, "explicit masses exceed 1"
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


def _band_from_point(jq: JointQ, *, n_draws: int = 4000, jitter: float = 0.02,
                     alpha: float = 0.05, seed: int = 7) -> JointQBand:
    """A real JointQBand whose draws are Dirichlet-like jitters around the point q.

    Each row is renormalized to the simplex (the band invariant), so band.samples @ payoff
    is a coherent per-draw fair value. The jitter controls the band width; a small jitter
    keeps edge_lcb close to the point edge, a larger one widens the downside.
    """
    rng = np.random.default_rng(seed)
    q = np.asarray(jq.q, dtype=float)
    n_bins = q.shape[0]
    # Dirichlet centered on q (concentration scales inversely with jitter) so every row is
    # a coherent point on the simplex and the marginal spread is controlled.
    conc = q * (1.0 / max(jitter, 1e-6)) + 1e-6
    samples = rng.dirichlet(conc, size=n_draws)
    q_lcb = np.quantile(samples, alpha, axis=0)
    q_ucb = np.quantile(samples, 1.0 - alpha, axis=0)
    band = JointQBand(
        joint_q=jq,
        samples=samples,
        q_lcb=q_lcb,
        q_ucb=q_ucb,
        alpha=alpha,
        basis="PARAMETER_POSTERIOR_SIMPLEX_V1",
        sample_hash="band-test-hash",
    )
    band.assert_valid()
    return band


def _route_cost(instrument: Instrument, *, cost: float, executable: bool = True) -> RouteCost:
    """A RouteCost carrying a typed all-in ExecutionPrice (the edge's only cost term)."""
    return RouteCost(
        route_id=f"{instrument.side}:{instrument.bin_id}@test",
        route_type="DIRECT_YES" if instrument.side == "YES" else "DIRECT_NO",
        instrument=instrument,
        shares=Decimal("10"),
        avg_cost=ExecutionPrice(
            cost,
            price_type="fee_adjusted",
            fee_deducted=True,
            currency="probability_units",
        ),
        max_shares=Decimal("100"),
        legs=(),
        executable=executable,
        reason=None if executable else "NO_DEPTH",
    )


def _yes_instrument(bin_id: str) -> Instrument:
    return Instrument(instrument_id=f"YES:{bin_id}", bin_id=bin_id, side="YES",
                      direct_token_id=f"yes-{bin_id}")


def _no_instrument(bin_id: str) -> Instrument:
    return Instrument(instrument_id=f"NO:{bin_id}", bin_id=bin_id, side="NO",
                      direct_token_id=f"no-{bin_id}")


# ===========================================================================
# Supporting primitive checks.
# ===========================================================================

def test_point_fair_value_is_q_dot_payoff_yes_and_no():
    """point_fair_value(q, payoff) is q_i for YES_i and 1 - q_i for NO_i (vector dot)."""
    space = _outcome_space()
    jq = _joint_q(space, {"b25": 0.40, "b26": 0.30})
    q_b25 = jq.q_by_bin_id["b25"]

    yes = _yes_instrument("b25")
    no = _no_instrument("b25")
    yes_payoff = yes.payoff_vector(space)
    no_payoff = no.payoff_vector(space)

    # YES_i fair value = q_i; NO_i fair value = 1 - q_i (the whole other-bin basket).
    assert point_fair_value(jq, yes_payoff) == pytest.approx(q_b25, abs=1e-12)
    assert point_fair_value(jq, no_payoff) == pytest.approx(1.0 - q_b25, abs=1e-12)


# ===========================================================================
# RED-on-revert #1 (spec line 1179): the edge IS q @ payoff - route cost.
# ===========================================================================

def test_edge_is_q_dot_payoff_minus_route_cost():
    """The Arrow-Debreu edge is the VECTOR dot product q @ payoff minus the route cost.

    RED-on-revert: the spec replaces a scalar single-bin edge with the full payoff-vector
    dot product. For the NO side this is the structural difference — a NO_i pays on every
    OTHER bin, so its edge is ``(1 - q_i) - cost``, NOT ``q_i - cost``. A reversion that
    used the bin's own mass ``q_i`` as the fair value would mis-price the NO and this
    asserts it cannot.
    """
    space = _outcome_space()
    # q_25 = 0.40 -> NO_25 fair value = 1 - 0.40 = 0.60.
    jq = _joint_q(space, {"b25": 0.40, "b26": 0.30})
    band = _band_from_point(jq, jitter=0.005)  # tight band: lcb close to point
    q_25 = jq.q_by_bin_id["b25"]

    # --- YES_25: edge = q_25 - ask_yes_25 (the spec's YES reduction, line 772). ---
    yes = _yes_instrument("b25")
    yes_cost = 0.30
    yes_route = build_candidate_route(
        candidate_id="cand-yes-25",
        instrument=yes,
        route_cost=_route_cost(yes, cost=yes_cost),
        omega=space,
    )
    yes_payoff = yes_route.payoff_vector
    yes_point_ev = point_fair_value(jq, yes_payoff) - yes_cost
    assert yes_point_ev == pytest.approx(q_25 - yes_cost, abs=1e-12)

    # --- NO_25: edge = (1 - q_25) - cost_not_25 (the spec's NO reduction, line 773). ---
    no = _no_instrument("b25")
    no_cost = 0.50
    no_route = build_candidate_route(
        candidate_id="cand-no-25",
        instrument=no,
        route_cost=_route_cost(no, cost=no_cost),
        omega=space,
    )
    no_payoff = no_route.payoff_vector
    no_point_ev = point_fair_value(jq, no_payoff) - no_cost
    # The vector edge for NO is the WHOLE other-bin basket value minus cost.
    assert no_point_ev == pytest.approx((1.0 - q_25) - no_cost, abs=1e-12)
    # And it DIFFERS from the broken scalar q_i - cost the spec replaces (q_25 - 0.50 < 0,
    # but the true NO edge (1 - q_25) - 0.50 = 0.10 > 0): the sign itself flips.
    broken_scalar_edge = q_25 - no_cost
    assert broken_scalar_edge < 0.0 < no_point_ev

    # --- edge_lcb is the quantile of the per-DRAW vector edge (line 770). ---
    no_lcb = edge_lower_bound(band, no_payoff, no_cost)
    # Recompute the contract directly from the band: quantile(samples @ payoff - cost).
    expected_lcb = float(
        np.quantile(band.samples @ no_payoff - no_cost, band.alpha)
    )
    assert no_lcb == pytest.approx(expected_lcb, abs=1e-12)
    # The robust lcb is a LOWER bound: at or below the point NO edge (0.10), and — with a
    # tight band on a genuine +edge — still strictly positive (the downside has not crossed
    # zero). (The NO basket payoff 1 - e_i sums spread across the other bins, so its lcb
    # sits meaningfully below the point edge even for a tight per-bin jitter — that is the
    # honest Arrow-Debreu downside of the whole basket, not a single bin's.)
    assert 0.0 < no_lcb <= no_point_ev + 1e-9


def test_edge_lcb_subtracts_cost_inside_the_quantile():
    """quantile(samples @ payoff - cost) == quantile(samples @ payoff) - cost (line 769).

    The two spec writings of the lcb (cost inside or outside the quantile) are identical
    because cost is a constant shift; this pins that the cost is a per-draw deduction, not
    a separately-handled scalar.
    """
    space = _outcome_space()
    jq = _joint_q(space, {"b24": 0.25, "b25": 0.35})
    band = _band_from_point(jq, jitter=0.02)
    yes = _yes_instrument("b24")
    payoff = yes.payoff_vector(space)
    cost = 0.20
    lcb = edge_lower_bound(band, payoff, cost)
    fair_quantile = float(np.quantile(band.samples @ payoff, band.alpha))
    assert lcb == pytest.approx(fair_quantile - cost, abs=1e-12)


# ===========================================================================
# RED-on-revert #2 (spec line 1184): the scalar q - cost CANNOT select.
# ===========================================================================

def test_scalar_q_minus_cost_cannot_select_candidate():
    """A scalar-positive but vector-negative candidate must NOT pass the live gate.

    RED-on-revert: the spec demotes the scalar ``q - price`` trade_score to telemetry —
    selection runs on edge_lcb / delta_u_at_min / optimal_delta_u. We construct a
    CandidateEconomics whose SCALAR edge (point_ev) is positive but whose robust
    ``edge_lcb`` is <= 0; the live pass must REFUSE it. If the gate were reverted to select
    on the positive scalar, it would admit this candidate and the test fails. We also prove
    the scalar trade_score is computable as telemetry but is NOT one of the pass inputs.
    """
    space = _outcome_space()
    jq = _joint_q(space, {"b25": 0.40})
    yes = _yes_instrument("b25")
    route = build_candidate_route(
        candidate_id="cand-yes-25",
        instrument=yes,
        route_cost=_route_cost(yes, cost=0.30),
        omega=space,
    )

    # The SCALAR trade_score is telemetry: q_25 (~0.40 normalized) - 0.30 > 0.
    scalar = scalar_trade_score(jq, route)
    assert scalar > 0.0  # scalar looks tradeable...

    # ...but the robust VECTOR edge_lcb is <= 0 (wide band downside). Build economics by
    # hand so the test pins the GATE, not the optimizer: scalar positive, vector negative.
    econ_scalar_positive_vector_negative = CandidateEconomics(
        candidate_id="cand-yes-25",
        point_ev=scalar,          # positive scalar edge (the telemetry number)
        edge_lcb=-0.01,           # robust vector lower bound is NEGATIVE
        delta_u_at_min=-0.001,    # robust ΔU at min order is NEGATIVE too
        optimal_stake_usd=Decimal("0"),
        optimal_delta_u=0.0,      # no positive robust ΔU -> no-trade
        q_dot_payoff=point_fair_value(jq, route.payoff_vector),
        cost=route.route_cost.avg_cost,
        route_id=route.route_cost.route_id,
    )
    # The live pass must REFUSE: a positive scalar cannot promote a vector-negative edge.
    assert not live_candidate_passes(
        econ_scalar_positive_vector_negative,
        route,
        direction_law_proof_present=True,
        market_coherence_accepted=True,
    )

    # A candidate that DOES pass passes on the VECTOR quantities — the scalar is never read.
    econ_vector_positive = CandidateEconomics(
        candidate_id="cand-yes-25",
        point_ev=scalar,
        edge_lcb=0.05,            # positive robust vector edge
        delta_u_at_min=0.01,      # positive robust ΔU at min order
        optimal_stake_usd=Decimal("5"),
        optimal_delta_u=0.02,     # positive robust ΔU at s*
        q_dot_payoff=point_fair_value(jq, route.payoff_vector),
        cost=route.route_cost.avg_cost,
        route_id=route.route_cost.route_id,
    )
    assert live_candidate_passes(
        econ_vector_positive,
        route,
        direction_law_proof_present=True,
        market_coherence_accepted=True,
    )


def test_optimize_vector_stake_sanitizes_nan_delta_u_at_min(monkeypatch):
    """A ruin-straddle NaN at venue-min stake cannot poison a positive-edge candidate."""

    class _Level:
        price = Decimal("0.10")
        size = Decimal("100")

    class _FeeModel:
        @staticmethod
        def all_in_price(price: Decimal) -> Decimal:
            return price

    class _Curve:
        levels = (_Level(),)
        fee_model = _FeeModel()
        min_order_size = Decimal("5")

    class _Candidate:
        is_tradeable = True
        executable_cost_curve = _Curve()

    calls = {"n": 0}

    def _fake_robust_delta_u(candidate, stake_usd, **kwargs):
        calls["n"] += 1
        if stake_usd == Decimal("0.50"):
            return float("nan")
        return 0.01

    monkeypatch.setattr("src.decision.payoff_vector.robust_delta_u", _fake_robust_delta_u)

    stake, optimal_delta_u, delta_u_at_min = optimize_vector_stake(
        _Candidate(),
        band=object(),
        omega=object(),
        matrix=object(),
        exposure=object(),
        max_stake_usd=Decimal("10"),
    )

    assert calls["n"] > 1
    assert stake > Decimal("0")
    assert optimal_delta_u == pytest.approx(0.01)
    assert delta_u_at_min == 0.0



def test_vector_positive_but_unexecutable_route_cannot_select():
    """Even a vector-positive candidate fails the gate if its route is not executable.

    The live pass requires ``executable route available`` (spec line 800); a conversion /
    depth-starved route (executable=False) is structurally blocked regardless of edge.
    """
    space = _outcome_space()
    jq = _joint_q(space, {"b25": 0.40})
    yes = _yes_instrument("b25")
    route = build_candidate_route(
        candidate_id="cand-yes-25",
        instrument=yes,
        route_cost=_route_cost(yes, cost=0.30, executable=False),
        omega=space,
    )
    econ = CandidateEconomics(
        candidate_id="cand-yes-25",
        point_ev=0.10,
        edge_lcb=0.05,
        delta_u_at_min=0.01,
        optimal_stake_usd=Decimal("5"),
        optimal_delta_u=0.02,
        q_dot_payoff=point_fair_value(jq, route.payoff_vector),
        cost=route.route_cost.avg_cost,
        route_id=route.route_cost.route_id,
    )
    assert not live_candidate_passes(
        econ, route, direction_law_proof_present=True, market_coherence_accepted=True
    )
    # And direction-law / coherence are also hard preconditions.
    assert not live_candidate_passes(
        econ, route, direction_law_proof_present=False, market_coherence_accepted=True
    )
