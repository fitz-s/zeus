# Created: 2026-06-14
# Last reused or audited: 2026-06-15 (vectorized robust-ΔU stake sweep: precompute the
#   stake-independent effective-π matrix once + single matmul reduction over band draws;
#   numerically identical to the per-draw _delta_u_at_stake sum, ~1400x faster — fixes the
#   live reactor cycle hang that blew the 45s budget to ~660s and starved snapshot freshness)
# Authority basis: docs/rebuild/consult_build_spec.md
#   ("Create src/decision/payoff_vector.py" block lines 734-802: CandidateRoute
#   738-746 [candidate_id, instrument, route_cost, payoff_vector, side, bin_id];
#   CandidateEconomics 747-758 [candidate_id, point_ev, edge_lcb, delta_u_at_min,
#   optimal_stake_usd, optimal_delta_u, q_dot_payoff, cost, route_id]; the edge
#   calculation 759-774 — point_fair_value = q @ payoff, point_edge = point_fair_value
#   - route.avg_cost.value, sample_edges = band.samples @ payoff - route.avg_cost.value,
#   edge_lcb = np.quantile(sample_edges, alpha), with the YES_i / NO_i / basket
#   reductions; the sizing 776-791 — robust_delta_u over band samples + the existing
#   FamilyPayoffMatrix ΔU, s_star = argmax_s robust_delta_u(candidate, s); the live
#   candidate pass 793-802) and the Stage 8 block lines 1166-1184 (RED-on-revert test
#   names + the live signal: selected candidate has edge_lcb / point_ev / delta_u /
#   optimal_stake / payoff_vector_hash; scalar q-price LOGGED but not selected on).
#   Reconciled against docs/evidence/qkernel_rebuild/spec_vs_live_drift_ledger.md
#   (GREENFIELD — no live edits; the reactor scalar-Kelly seam
#   event_reactor_adapter.py:8632 and the trade_score demotion happen at Stage 11, NOT
#   here. This module is pure objects + pure functions, wired into the reactor later).
#   Live dependencies (ALL already built; imported, never re-implemented):
#     - src/probability/instruments.py::Instrument
#                       (Instrument.payoff_vector(omega) — the (n_bins,) Arrow-Debreu
#                       payoff: e_i for YES_i, 1 - e_i for NO_i, aligned 1:1 with
#                       omega.bins, the SAME alignment JointQ.q / JointQBand.samples use)
#     - src/execution/negrisk_routes.py::RouteCost
#                       (RouteCost.avg_cost — the executable ALL-IN cost per share as a
#                       typed ExecutionPrice in probability_units; the ONLY cost term in
#                       the edge. RouteCost.executable / .route_id / .instrument carried)
#     - src/probability/joint_q.py::JointQ
#                       (JointQ.q — the point (n_bins,) joint mass for point_fair_value
#                       = q @ payoff; JointQ.omega for the bin alignment)
#     - src/probability/joint_q_band.py::JointQBand
#                       (JointQBand.samples — the (n_draws, n_bins) simplex matrix for
#                       edge_lcb = quantile(samples @ payoff - cost, alpha) AND for the
#                       per-draw robust_delta_u; JointQBand.alpha — the default tail)
#     - src/strategy/utility_ranker.py::{FamilyPayoffMatrix, PortfolioExposureVector,
#                       OUTSIDE_OUTCOME}
#                       (the EXISTING ΔU objective ΔU_j(s) = Σ_y π_y [log(A_y + R_{y,j}(s))
#                       - log(A_y)] over the FULL outcome set — REUSED for vector sizing.
#                       The vector-argmax sizing maximizes the alpha-quantile of this ΔU
#                       across band-sample q draws, NOT a binary scalar f_star.)
#     - src/contracts/native_side_candidate.py::NativeSideCandidate
#                       (the candidate object FamilyPayoffMatrix.payoff scores; carries the
#                       native executable_cost_curve the ΔU stake-sweep walks)
"""payoff_vector — Arrow-Debreu edge + vector-argmax sizing (Stage 8a).

This is Stage 8a of the q-kernel rebuild (consult_build_spec.md lines 734-802, Stage 8
block 1166-1184). It is the DECISION economics layer: given a candidate's payoff vector
over the complete Omega, the joint q point distribution, the coherent q band, and the
executable route cost, it computes the candidate's edge and its optimal stake — and the
scalar ``q - price`` trade_score is DEMOTED to telemetry that CANNOT select.

THE TWO CORRECTED TRANSFORMATIONS (operator law — make the bad output mathematically
impossible; NO gate/cap/clamp/haircut that catches a bad scalar and leaves the broken
scalar transform in place):

  1. EDGE IS A VECTOR DOT PRODUCT, NOT A SCALAR q - price (spec lines 759-774). The
     point edge is

         point_fair_value = q @ payoff          # the FULL Arrow-Debreu fair value
         point_edge       = point_fair_value - route.avg_cost.value

     and the robust lower bound is built from the alpha-quantile of the
     per-DRAW payoff fair value:

         sample_payoffs = band.samples @ payoff
         payoff_q_lcb   = min(np.quantile(sample_payoffs, alpha), point_fair_value)
         edge_lcb       = payoff_q_lcb - route.avg_cost.value

     Because ``payoff`` is the instrument's Arrow-Debreu vector over the COMPLETE Omega
     (``e_i`` for YES_i, ``1 - e_i`` for NO_i, the real bundle vector for a basket) and
     ``q`` / ``band.samples`` are aligned 1:1 with that Omega, the dot product is the
     genuine fair value of the WHOLE payoff. For a YES_i it reduces to ``q_i - ask_yes_i``;
     for a NO_i / synthetic NOT_i it reduces to ``(1 - q_i) - cost_not_i``; for a basket
     it is the real bundle payoff value — all three fall out of the ONE vector transform.
     There is no scalar ``q_i - price`` path that could disagree with the vector edge,
     because the vector edge IS how edge is computed. The scalar is never formed as the
     selection quantity.

  2. SIZE IS A VECTOR ΔU ARGMAX, NOT A BINARY f_star (spec lines 776-791). The optimal
     stake is

         s_star = argmax_s  robust_delta_u(candidate, s)

     where ``robust_delta_u(candidate, s)`` is the alpha-quantile, ACROSS band-sample q
     draws, of the EXISTING FamilyPayoffMatrix ΔU at stake ``s``:

         robust_delta_u(candidate, s) = quantile(
             [ delta_u(candidate, s, q_k, exposure) for q_k in band.samples ], alpha )

     ``delta_u`` is the spec's family-vector log-growth objective
     ``ΔU_j(s) = Σ_y π_y [log(A_y + R_{y,j}(s)) - log(A_y)]`` over the FULL outcome set
     (every bin PLUS the OUTSIDE residual) against the EXISTING/PENDING exposure baseline
     — the SAME geometry ``src/strategy/utility_ranker.py`` already implements. It is NOT
     a per-candidate ``f_star = (q - c) / (1 - c)`` binary Kelly fraction. The size falls
     out of the family payoff MATRIX and the existing exposure, so a correlated existing
     position SHRINKS the optimal stake by the concavity of the log — by construction of
     the ΔU objective, not a cap subtracted afterward.

THE SCALAR IS TELEMETRY (spec lines 793-802, 1184). ``CandidateEconomics.q_dot_payoff``
records ``q @ payoff`` (the point fair value) and a derived scalar ``q - price`` edge
is available ONLY as logged telemetry via :func:`scalar_trade_score`. The live
candidate pass (:func:`live_candidate_passes`) selects on the VECTOR quantities
(``edge_lcb > 0`` AND ``delta_u_at_min > 0`` AND ``optimal_delta_u > 0`` AND executable
route AND direction-law proof present AND market coherence accepted). The scalar score
is NOT one of the pass conditions — it cannot promote or block a candidate. There is no
code path where ``scalar_trade_score`` reaches the selection decision.

DRIFT RESOLVED (recorded per operator law; see docs/rebuild/impl_w4_payoff_vector.md):

  * The spec sizing pseudocode (lines 785-789) writes
    ``robust_delta_u(candidate, stake)`` calling a bare ``delta_u(candidate, stake, q_k,
    exposure)`` and ``argmax_s``. The LIVE ΔU objective lives in
    ``src/strategy/utility_ranker.py`` and is parameterized by a ``FamilyPayoffMatrix``
    (the R_{y,j}(s) geometry over outcomes), a ``pi`` outcome-probability MAPPING, a
    ``PortfolioExposureVector`` (A_y), and a ``NativeSideCandidate`` (the side + the
    executable cost curve the stake-sweep walks). Resolution (toward the live type): the
    spec's ``q_k`` band draw is converted to the live ``pi`` MAPPING by reading each
    bin's mass off the draw and assigning the OUTSIDE residual ``1 - Σ_bins q_k`` — the
    exact same outcome-probability shape ``utility_ranker.robust_probabilities`` produces,
    only built from ONE coherent draw rather than the per-bin q_lcb. ``delta_u`` is then
    the live ``_delta_u_at_stake`` evaluated at that per-draw ``pi``. So the vector
    sizing REUSES the existing ΔU machinery unchanged; it only varies the probability
    vector per band draw (the robustness) instead of a single fixed π.

  * ``CandidateEconomics.cost`` is typed ``ExecutionPrice`` (spec line 756 writes
    ``cost: ExecutionPrice``); it is the route's ``avg_cost``, carried through verbatim.
"""
from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal, Mapping, Sequence

import numpy as np

from src.contracts.execution_price import ExecutionPrice
from src.contracts.native_side_candidate import NativeSideCandidate
from src.execution.negrisk_routes import RouteCost
from src.probability.instruments import Instrument
from src.probability.joint_q import JointQ
from src.probability.joint_q_band import JointQBand
from src.probability.outcome_space import OutcomeSpace
from src.strategy.utility_ranker import (
    OUTSIDE_OUTCOME,
    FamilyPayoffMatrix,
    PortfolioExposureVector,
    _delta_u_at_stake,
    effective_outcome_pi,
)

# The default lower-tail probability the robust quantities are taken at. ``None`` means
# "use the band's own ``alpha``" so the edge_lcb and the robust ΔU are read at the SAME
# tail the band was built at — coherent by default, exactly as the spec writes
# ``np.quantile(sample_edges, alpha)`` with the band's alpha.
_DEFAULT_ALPHA: float | None = None

# Numerical-optimizer resolution for the s* = argmax_s robust_delta_u(s) sweep. ΔU is
# concave in stake (a sum of concave logs of an affine-in-s wealth) and the alpha-quantile
# of a family of concave functions is itself concave, so robust_delta_u is unimodal and a
# coarse-to-fine grid converges. These mirror utility_ranker's optimizer resolution so the
# vector sizing has the same stake granularity the family ranker uses.
_COARSE_STEPS = 200
_REFINE_STEPS = 64
_REFINE_PASSES = 3


class PayoffVectorError(ValueError):
    """Raised when candidate economics cannot be computed coherently.

    Fail-closed signal: the payoff vector is not aligned to the joint q / band Omega, a
    degenerate alpha was requested, or a route's cost is not a usable probability-unit
    cost — so there is no coherent edge to serve, and it is refused rather than served a
    wrong number.
    """


# ---------------------------------------------------------------------------
# CandidateRoute (spec lines 738-746) — EXACT field names, frozen.
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CandidateRoute:
    """One candidate's Arrow-Debreu claim + the executable route to acquire it (738-746).

    Field names are verbatim from consult_build_spec.md.

    * ``candidate_id`` — a stable id for this candidate route.
    * ``instrument`` — the ``Instrument`` (YES/NO payoff-vector claim) this candidate is.
    * ``route_cost`` — the ``RouteCost`` (the executable, size-aware all-in route) whose
      ``avg_cost`` is the cost term of the edge. ``executable=False`` routes flow through
      (their economics are still computed for the receipt) but fail the live pass.
    * ``payoff_vector`` — the (n_bins,) Arrow-Debreu payoff over the COMPLETE Omega,
      aligned 1:1 with the joint q / band bins. It IS ``instrument.payoff_vector(omega)``;
      stored on the candidate so a receipt records the exact vector the edge ran over and
      its hash anchors the decision (spec line 1184 ``payoff_vector_hash``).
    * ``side`` — ``"YES"`` or ``"NO"`` (the instrument side; carried for the receipt).
    * ``bin_id`` — the Omega bin this candidate is about.
    """

    candidate_id: str
    instrument: Instrument
    route_cost: RouteCost
    payoff_vector: np.ndarray
    side: Literal["YES", "NO"]
    bin_id: str

    def payoff_vector_hash(self) -> str:
        """Deterministic hash over the payoff vector (spec line 1184 receipt anchor)."""
        h = hashlib.sha256()
        h.update(self.candidate_id.encode("utf-8"))
        h.update(self.bin_id.encode("utf-8"))
        h.update(self.side.encode("utf-8"))
        h.update(np.ascontiguousarray(np.round(self.payoff_vector, 12)).tobytes())
        return h.hexdigest()


# ---------------------------------------------------------------------------
# CandidateEconomics (spec lines 747-758) — EXACT field names, frozen.
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CandidateEconomics:
    """The economics of one candidate route (spec lines 747-758).

    Field names are verbatim from consult_build_spec.md.

    * ``candidate_id`` — the candidate this economics is for.
    * ``point_ev`` — the point edge ``q @ payoff - route.avg_cost.value`` (the
      Arrow-Debreu point expected value of the trade, all-in cost deducted).
    * ``edge_lcb`` — ``payoff_q_lcb - cost``: the robust lower bound of the VECTOR
      edge across coherent joint draws, tied to the explicit payoff-space qLCB below.
    * ``delta_u_at_min`` — robust ΔU at the venue MIN-ORDER stake (the feasible lower
      bound). Lets the live pass tell a genuine edge reversal (ΔU ≤ 0 even at min order)
      from a stake the optimizer shrank below the venue floor.
    * ``optimal_stake_usd`` — ``s* = argmax_s robust_delta_u(candidate, s)`` in USD; 0
      when no positive stake helps (no-trade).
    * ``optimal_delta_u`` — robust ΔU at ``s*`` (the maximized vector log-growth).
    * ``q_dot_payoff`` — ``q @ payoff`` (the point fair value before cost). This is the
      TELEMETRY anchor — the scalar fair value is RECORDED here but the selection runs on
      ``edge_lcb`` / ``optimal_delta_u``, never on this scalar.
    * ``payoff_q_lcb`` — conservative lower bound on the selected route's payoff
      probability. This is generated from the q-band/payoff vector directly (or from
      the empirical qLCB guard), not reverse-derived downstream from ``edge_lcb``.
    * ``cost`` — the route's all-in cost as a typed ``ExecutionPrice`` (spec line 756).
    * ``route_id`` — the route this economics priced against (provenance).
    * ``chosen_stake_cost`` — the same executable curve priced at ``optimal_stake_usd``.
      When present, live selection must use this cost/edge pair because it is the
      exact price boundary the submit path will carry.
    """

    candidate_id: str
    point_ev: float
    edge_lcb: float
    delta_u_at_min: float
    optimal_stake_usd: Decimal
    optimal_delta_u: float
    q_dot_payoff: float
    cost: ExecutionPrice
    route_id: str
    payoff_q_lcb: float | None = None
    chosen_stake_cost: ExecutionPrice | None = None
    chosen_stake_point_ev: float | None = None
    chosen_stake_edge_lcb: float | None = None


# ===========================================================================
# Edge calculation (spec lines 759-774) — the VECTOR Arrow-Debreu edge.
# ===========================================================================

def _validate_alignment(
    payoff: np.ndarray, joint_q: JointQ, band: JointQBand
) -> None:
    """Refuse a payoff vector not aligned 1:1 with the joint q / band Omega.

    The edge ``q @ payoff`` is only the Arrow-Debreu fair value when ``payoff`` is keyed
    on the SAME bin order as ``q`` and ``band.samples``. A length mismatch would silently
    compute a wrong dot product, so it fails closed here.
    """
    n_bins = int(joint_q.q.shape[0])
    if payoff.shape != (n_bins,):
        raise PayoffVectorError(
            f"PAYOFF_MISALIGNED: payoff vector shape {payoff.shape} != joint q "
            f"({n_bins},). The payoff must be aligned 1:1 with omega.bins."
        )
    if band.samples.shape[1] != n_bins:
        raise PayoffVectorError(
            f"BAND_MISALIGNED: band.samples has {band.samples.shape[1]} bins != joint q "
            f"({n_bins},). The band must run over the same Omega as the point q."
        )


def point_fair_value(joint_q: JointQ, payoff: np.ndarray) -> float:
    """``q @ payoff`` — the Arrow-Debreu point fair value (spec line 764).

    The dot product of the point joint mass with the instrument's payoff vector over the
    COMPLETE Omega. For YES_i it is ``q_i``; for NO_i (payoff ``1 - e_i``) it is
    ``Σ_{j≠i} q_j = 1 - q_i``; for a basket it is the real bundle value. This is the
    fair value of the WHOLE payoff, never a single scalar ``q_i`` masquerading as the
    edge basis.
    """
    return float(np.asarray(joint_q.q, dtype=float) @ np.asarray(payoff, dtype=float))


def payoff_lower_bound(
    band: JointQBand,
    payoff: np.ndarray,
    *,
    q_dot_payoff: float | None = None,
    alpha: float | None = _DEFAULT_ALPHA,
) -> float:
    """Conservative lower bound on the route payoff probability.

    This is the probability-space companion to ``edge_lcb``. It is read from the
    same q-band/payoff vector as selection, then capped at the point fair value
    so a lower-bound certificate can never exceed the live point belief carried
    into submit/monitor. The cap turns a band/point centering inconsistency into
    a stricter edge, not into an invalid downstream certificate.
    """

    a = band.alpha if alpha is None else float(alpha)
    if not (0.0 < a < 1.0):
        raise PayoffVectorError(f"DEGENERATE_ALPHA: alpha={a!r} (need 0 < alpha < 1)")
    sample_fair = np.asarray(band.samples, dtype=float) @ np.asarray(payoff, dtype=float)
    q_lcb = float(np.quantile(sample_fair, a))
    if q_dot_payoff is not None:
        q_lcb = min(q_lcb, float(q_dot_payoff))
    return float(min(max(q_lcb, 0.0), 1.0))


def edge_lower_bound(
    band: JointQBand, payoff: np.ndarray, cost: float, *, alpha: float | None = None
) -> float:
    """``payoff_lower_bound(band, payoff) - cost`` (spec lines 769-770).

    The robust lower credible bound of the VECTOR edge. ``band.samples @ payoff`` is, per
    coherent joint draw, the Arrow-Debreu fair value of the payoff under THAT draw (every
    row sums to 1, the simplex invariant); subtracting the all-in cost gives the per-draw
    edge, and its ``alpha``-quantile is the genuine downside of the edge across draws.

    The payoff lower bound is produced explicitly in probability space and capped at
    point fair value before cost is subtracted. That keeps the submit/monitor
    certificate and the selector on one qLCB instead of reconstructing qLCB later
    from an edge field.
    """
    q_dot = point_fair_value(band.joint_q, np.asarray(payoff, dtype=float))
    return float(payoff_lower_bound(band, payoff, q_dot_payoff=q_dot, alpha=alpha) - float(cost))


def _route_cost_value(route_cost: RouteCost) -> float:
    """The route's all-in per-share cost as a float in probability_units.

    ``RouteCost.avg_cost`` is a typed ``ExecutionPrice`` in probability_units (fee-applied).
    The edge subtracts this single cost term — never a midpoint, last, or complement price
    (the route engine already forbade those at the leaf). A non-executable route still
    carries a typed (zero-value) ExecutionPrice; its economics are computed for the receipt
    but it fails the live pass on ``executable``.
    """
    price = route_cost.avg_cost
    if price.currency != "probability_units":
        raise PayoffVectorError(
            f"COST_NOT_PROBABILITY_UNITS: route {route_cost.route_id!r} avg_cost currency "
            f"is {price.currency!r}; the edge needs a probability-unit all-in cost"
        )
    return float(price.value)


# ===========================================================================
# Vector ΔU sizing (spec lines 776-791) — argmax robust_delta_u, NOT binary f_star.
# ===========================================================================

def _draw_to_pi(
    q_draw: np.ndarray,
    omega: OutcomeSpace,
    matrix: FamilyPayoffMatrix,
) -> dict[str, float]:
    """Convert ONE coherent band draw ``q_k`` to the live outcome-probability ``pi`` map.

    DRIFT RESOLUTION (recorded in the report). The spec sizing writes
    ``delta_u(candidate, stake, q_k, exposure)`` with ``q_k`` a band draw vector, but the
    LIVE ΔU objective (``utility_ranker._delta_u_at_stake``) consumes a ``pi`` MAPPING
    over the FULL outcome set (every bin id PLUS ``OUTSIDE_OUTCOME``). This maps the draw
    to that shape: each bin's mass is read off the draw (aligned by position to
    ``omega.bins``), and ``OUTSIDE`` absorbs the residual ``max(0, 1 - Σ_bins q_k)``.

    Because each draw already sums to 1 over the COMPLETE Omega (the simplex invariant),
    and ``matrix.bins`` is a subset of the Omega bins (the family's tradeable + scored
    bins), the OUTSIDE residual is exactly the mass on the Omega bins NOT represented in
    the matrix — the SAME ``pi`` shape ``robust_probabilities`` produces, but from ONE
    coherent draw rather than per-bin q_lcb. So the per-draw ΔU is the existing family ΔU
    evaluated at that draw's probability vector — the vector sizing reuses the live
    machinery, only varying the probability vector per draw.
    """
    bin_index = {b.bin_id: i for i, b in enumerate(omega.bins)}
    pi: dict[str, float] = {}
    bin_mass = 0.0
    for outcome in matrix.bins:
        if outcome not in bin_index:
            raise PayoffVectorError(
                f"MATRIX_BIN_NOT_IN_OMEGA: matrix outcome {outcome!r} is not an Omega bin "
                f"({sorted(bin_index)})"
            )
        m = float(q_draw[bin_index[outcome]])
        pi[outcome] = m
        bin_mass += m
    pi[OUTSIDE_OUTCOME] = max(0.0, 1.0 - bin_mass)
    return pi


def _validate_guarded_payoff_q_lcb(guarded_payoff_q_lcb: float | None) -> float | None:
    if guarded_payoff_q_lcb is None:
        return None
    q = float(guarded_payoff_q_lcb)
    if not (0.0 <= q <= 1.0):
        raise PayoffVectorError(
            f"DEGENERATE_GUARDED_PAYOFF_Q_LCB: {q!r} (need 0 <= q <= 1)"
        )
    return q


def _candidate_guarded_pi(
    candidate: NativeSideCandidate,
    matrix: FamilyPayoffMatrix,
    base_pi: Mapping[str, float],
    *,
    guarded_payoff_q_lcb: float,
) -> dict[str, float]:
    """Candidate-local conservative outcome distribution for a guarded payoff qLCB.

    qLCB reliability evidence is side-specific: it licenses the candidate payoff's win
    probability lower bound ``q_safe``; it is not a new parameter-posterior draw matrix and
    must not be serialized as one. This function therefore builds the effective π consumed
    by robust ΔU directly:

      * YES_i: own-bin win mass is ``q_safe``; the remaining loss mass is spread over every
        other outcome proportional to the draw's original non-own mass.
      * NO_i: own-bin loss mass is ``1 - q_safe``; the win mass ``q_safe`` is spread over
        every other outcome proportional to the draw's original non-own mass.

    This is the same side-conservative idea as ``effective_outcome_pi`` for NO, extended to
    YES when empirical OOF evidence deflates the served qLCB. The point forecast μ and the
    global ``JointQBand`` remain untouched; only this candidate's economics consume the
    guarded lower-bound view.
    """
    q_safe = _validate_guarded_payoff_q_lcb(guarded_payoff_q_lcb)
    assert q_safe is not None  # for type checkers
    own_bin = candidate.bin_id
    if own_bin not in matrix.outcomes:
        raise PayoffVectorError(
            f"candidate bin {own_bin!r} is not a family outcome {matrix.outcomes}"
        )
    others = [y for y in matrix.outcomes if y != own_bin]
    if not others:
        raise PayoffVectorError(
            "candidate-local guarded π needs at least one losing/winning other outcome"
        )

    if candidate.side == "YES":
        own_mass = q_safe
        other_mass = 1.0 - q_safe
    else:
        own_mass = 1.0 - q_safe
        other_mass = q_safe

    other_total = sum(float(base_pi.get(y, 0.0)) for y in others)
    eff: dict[str, float] = {own_bin: own_mass}
    if other_total <= 0.0:
        share = other_mass / len(others)
        for y in others:
            eff[y] = share
    else:
        for y in others:
            eff[y] = other_mass * float(base_pi.get(y, 0.0)) / other_total

    total = sum(eff.values())
    if total <= 0.0:
        raise PayoffVectorError("candidate-local guarded π is degenerate")
    if abs(total - 1.0) > 1e-12:
        eff = {y: p / total for y, p in eff.items()}
    return {y: float(eff.get(y, 0.0)) for y in matrix.outcomes}


class _PreparedSizing:
    """Stake-INDEPENDENT precompute for the robust-ΔU stake sweep (performance only).

    ``robust_delta_u(s)`` is ``quantile_alpha( [ Σ_y π_y^(k) · g_y(s) for draw k ] )`` where

      * the per-draw side-conservative effective π (``effective_outcome_pi``) is INDEPENDENT
        of the stake ``s`` (it depends only on the draw and the candidate side), and
      * the per-outcome log-growth ``g_y(s) = log(A_y + R_y(s)) - log(A_y)`` is INDEPENDENT
        of the draw (it depends only on the stake, the candidate and the outcome).

    The naive nested ``optimize_vector_stake`` rebuilt the entire (n_draws) effective-π set
    at EVERY stake grid point and summed the draws in pure Python — ``O(grid · n_draws ·
    n_outcomes)`` Python work. With grid≈396 and n_draws=4000 that is ~1.6M
    ``_delta_u_at_stake`` calls PER candidate, which blew the live reactor cycle budget
    (a 22-family cycle ran ~660s at 100% CPU, starving snapshot freshness so every priced
    family expired before it could fill).

    This precomputes, ONCE per candidate, the (n_draws × n_outcomes) effective-π matrix
    ``Pi`` and the per-outcome existing wealth ``A``. Each stake evaluation then costs one
    per-outcome growth vector ``g(s)`` (``n_outcomes`` ``matrix.payoff`` walks) plus a
    single ``Pi @ g`` matmul and one quantile — the draw loop is gone. Because ΔU is LINEAR
    in π, the matmul is numerically identical to the per-draw ``_delta_u_at_stake`` sum: the
    SAME effective_outcome_pi, the SAME Decimal-wealth ruin rule, the SAME alpha-quantile.
    It is a pure speedup, NOT a cap/haircut/behavior change.
    """

    __slots__ = ("candidate", "matrix", "alpha", "outcomes", "_A", "_Pi")

    def __init__(
        self,
        candidate: NativeSideCandidate,
        *,
        band: JointQBand,
        omega: OutcomeSpace,
        matrix: FamilyPayoffMatrix,
        exposure: PortfolioExposureVector,
        alpha: float,
        guarded_payoff_q_lcb: float | None = None,
    ) -> None:
        self.candidate = candidate
        self.matrix = matrix
        self.alpha = alpha
        q_guard = _validate_guarded_payoff_q_lcb(guarded_payoff_q_lcb)
        outcomes = list(matrix.outcomes)
        self.outcomes = outcomes
        # Existing wealth A_y per outcome (Decimal — the ruin check and the log are taken
        # exactly as utility_ranker._delta_u_at_stake does, on Decimal wealth).
        self._A = [exposure.a(y) for y in outcomes]
        # Stake-INDEPENDENT effective-π matrix: Pi[k, j] = effective_outcome_pi(draw_k)[j].
        samples = np.asarray(band.samples, dtype=float)
        n_draws = samples.shape[0]
        n_out = len(outcomes)
        Pi = np.zeros((n_draws, n_out), dtype=float)
        for k in range(n_draws):
            pi = _draw_to_pi(samples[k, :], omega, matrix)
            eff_pi = (
                _candidate_guarded_pi(
                    candidate, matrix, pi, guarded_payoff_q_lcb=q_guard
                )
                if q_guard is not None
                else effective_outcome_pi(candidate, matrix, pi)
            )
            for j, y in enumerate(outcomes):
                Pi[k, j] = float(eff_pi.get(y, 0.0))
        self._Pi = Pi

    def robust_at(self, stake_usd: Decimal) -> float:
        """The alpha-quantile of ΔU across all band draws at ``stake_usd`` (vectorized)."""
        outcomes = self.outcomes
        A = self._A
        g = np.zeros(len(outcomes), dtype=float)
        ruin = np.zeros(len(outcomes), dtype=bool)
        for j, y in enumerate(outcomes):
            a = A[j]
            try:
                r = self.matrix.payoff(self.candidate, y, stake_usd)
            except (ValueError, ArithmeticError):
                # Infeasible stake at this outcome (depth / min order / off-grid): any draw
                # placing mass here is -inf — exactly _delta_u_at_stake's except branch.
                ruin[j] = True
                continue
            new_wealth = a + r
            if new_wealth <= Decimal("0"):
                # Ruin on this outcome -> log undefined -> -inf for any draw with mass here.
                ruin[j] = True
                continue
            g[j] = math.log(float(new_wealth)) - math.log(float(a))
        # ΔU per draw = Σ_y π_y · g_y  (LINEAR in π -> one matmul over ALL draws at once).
        du = self._Pi @ g
        if ruin.any():
            # A draw with POSITIVE mass on ANY ruin outcome is -inf. This matches the
            # per-draw `if p <= 0: continue` skip: an outcome with zero draw-mass never
            # triggers the ruin -inf for that draw.
            bad = (self._Pi[:, ruin] > 0.0).any(axis=1)
            if bad.any():
                du = np.where(bad, -np.inf, du)
        return float(np.quantile(du, self.alpha))


def robust_delta_u(
    candidate: NativeSideCandidate,
    stake_usd: Decimal,
    *,
    band: JointQBand,
    omega: OutcomeSpace,
    matrix: FamilyPayoffMatrix,
    exposure: PortfolioExposureVector,
    alpha: float | None = None,
    guarded_payoff_q_lcb: float | None = None,
) -> float:
    """Robust ΔU at ``stake_usd`` — the alpha-quantile of ΔU across band draws (785-789).

    Spec lines 785-789::

        def robust_delta_u(candidate, stake):
            values = []
            for q_k in band.samples:
                values.append(delta_u(candidate, stake, q_k, exposure))
            return np.quantile(values, alpha)

    ``delta_u(candidate, stake, q_k, exposure)`` is the EXISTING FamilyPayoffMatrix
    objective ``ΔU_j(s) = Σ_y π_y [log(A_y + R_{y,j}(s)) - log(A_y)]`` evaluated with the
    per-draw outcome distribution ``pi(q_k)`` (the live ``_delta_u_at_stake``). For a NO
    candidate the per-draw ``pi`` is re-anchored to the side-conservative effective
    distribution (``effective_outcome_pi``) exactly as the family ranker does, so the NO
    win-mass is the candidate's own side bound on EVERY draw — never the looser
    ``1 - q_lcb_yes``. The robust ΔU is the ``alpha``-quantile of those per-draw ΔU values
    — the downside log-growth across coherent joint draws. This is NOT a binary
    ``f_star``: it is the family-vector log-utility against the existing exposure, so a
    correlated existing position lowers it by the log concavity.
    """
    if not candidate.is_tradeable or candidate.executable_cost_curve is None:
        return float("-inf")
    a = band.alpha if alpha is None else float(alpha)
    if not (0.0 < a < 1.0):
        raise PayoffVectorError(f"DEGENERATE_ALPHA: alpha={a!r} (need 0 < alpha < 1)")

    # Vectorized: build the stake-INDEPENDENT effective-π matrix once, then reduce the draws
    # with a single matmul (ΔU is linear in π). Numerically identical to the per-draw
    # `_delta_u_at_stake` sum — same effective_outcome_pi, same Decimal-wealth ruin rule,
    # same alpha-quantile (see :class:`_PreparedSizing`). `_delta_u_at_stake` is retained as
    # the single-π reference the equivalence test pins this against.
    prepared = _PreparedSizing(
        candidate,
        band=band,
        omega=omega,
        matrix=matrix,
        exposure=exposure,
        alpha=a,
        guarded_payoff_q_lcb=guarded_payoff_q_lcb,
    )
    return prepared.robust_at(stake_usd)


def _feasible_stake_bounds(
    candidate: NativeSideCandidate, max_stake_usd: Decimal | None
) -> tuple[Decimal | None, Decimal | None]:
    """Feasible stake interval ``[lo, hi]`` for the candidate's executable cost curve.

    Mirrors ``utility_ranker._feasible_stake_bounds`` (the SAME bounds the family ranker
    sizes within): ``hi`` is the full-book all-in notional (capped by ``max_stake_usd``),
    ``lo`` is the min-order notional at the cheapest level. Returns ``(None, None)`` when
    no feasible stake exists.
    """
    curve = candidate.executable_cost_curve
    if curve is None:
        return None, None
    levels = getattr(curve, "levels", None)
    if not levels:
        return None, None
    full_notional = sum(
        (curve.fee_model.all_in_price(lvl.price) * lvl.size for lvl in levels),
        Decimal("0"),
    )
    hi = full_notional
    if max_stake_usd is not None:
        hi = min(hi, Decimal(max_stake_usd))
    best = levels[0]
    lo = curve.fee_model.all_in_price(best.price) * curve.min_order_size
    if lo <= Decimal("0") or hi <= Decimal("0") or hi < lo:
        return None, None
    return lo, hi


def optimize_vector_stake(
    candidate: NativeSideCandidate,
    *,
    band: JointQBand,
    omega: OutcomeSpace,
    matrix: FamilyPayoffMatrix,
    exposure: PortfolioExposureVector,
    max_stake_usd: Decimal | None = None,
    alpha: float | None = None,
    guarded_payoff_q_lcb: float | None = None,
) -> tuple[Decimal, float, float]:
    """``s* = argmax_s robust_delta_u(candidate, s)`` (spec line 791).

    Returns ``(optimal_stake_usd, optimal_delta_u, delta_u_at_min)``:

    * ``optimal_stake_usd`` — ``s*``, the stake maximizing the robust (alpha-quantile)
      ΔU over band draws. ``0`` when no feasible positive stake yields a positive robust
      ΔU (no-trade — spec live pass needs ``optimal_delta_u > 0``).
    * ``optimal_delta_u`` — robust ΔU at ``s*``. ``<= 0`` (here ``0.0`` when no positive
      stake helps) means no-trade.
    * ``delta_u_at_min`` — robust ΔU at the venue MIN-ORDER stake (the feasible lower
      bound ``lo``), so the live pass can distinguish a true edge reversal from a stake
      the optimizer shrank below the venue floor (spec line 798 ``delta_u_at_min > 0``).

    The argmax is a coarse-to-fine 1-D grid search over ``[lo, hi]`` — the SAME stake
    bounds and resolution the family ranker uses. ``robust_delta_u`` is unimodal in stake
    (a quantile of concave-in-s log-growth functions), so the refined grid converges.

    This is the VECTOR sizing: the stake comes from the FamilyPayoffMatrix ΔU against the
    existing exposure, maximized at the robust tail — NEVER a binary scalar ``f_star``.
    """
    lo, hi = _feasible_stake_bounds(candidate, max_stake_usd)
    if lo is None or hi is None or hi <= lo:
        return Decimal("0"), 0.0, float("-inf")

    # Untradeable candidates have no robust ΔU (robust_delta_u returns -inf for them and
    # effective_outcome_pi refuses them); guard BEFORE the precompute so the prepared matrix
    # is never built for a no-trade candidate. Matches the old per-call robust_delta_u guard.
    if not candidate.is_tradeable or candidate.executable_cost_curve is None:
        return Decimal("0"), 0.0, float("-inf")
    a = band.alpha if alpha is None else float(alpha)
    if not (0.0 < a < 1.0):
        raise PayoffVectorError(f"DEGENERATE_ALPHA: alpha={a!r} (need 0 < alpha < 1)")

    # Build the stake-INDEPENDENT effective-π matrix ONCE; every grid point reuses it (the
    # whole reason the sweep is now cheap — see :class:`_PreparedSizing`). Identical numbers
    # to calling robust_delta_u per stake, ~grid× less work.
    prepared = _PreparedSizing(
        candidate,
        band=band,
        omega=omega,
        matrix=matrix,
        exposure=exposure,
        alpha=a,
        guarded_payoff_q_lcb=guarded_payoff_q_lcb,
    )

    def _ru(stake: Decimal) -> float:
        return prepared.robust_at(stake)

    # import BEFORE first use: the delta_u_at_min NaN guard below references _math, so a
    # later `import math as _math` made _math a function-local that was unbound here ->
    # UnboundLocalError -> SPINE_WIRING_FAULT -> every spine decision crashed (no crosses).
    import math as _math

    delta_u_at_min = _ru(lo)
    if not _math.isfinite(delta_u_at_min):
        delta_u_at_min = 0.0

    best_u = float("-inf")
    best_s = Decimal("0")
    span_lo, span_hi = lo, hi
    steps = _COARSE_STEPS
    for _pass in range(_REFINE_PASSES + 1):
        width = span_hi - span_lo
        if width <= Decimal("0"):
            break
        step = width / Decimal(steps)
        s = span_lo
        pass_best_u = float("-inf")
        pass_best_s = span_lo
        for _ in range(steps + 1):
            u = _ru(s)
            if u > pass_best_u:
                pass_best_u = u
                pass_best_s = s
            s += step
        if pass_best_u > best_u:
            best_u = pass_best_u
            best_s = pass_best_s
        span_lo = max(lo, pass_best_s - step)
        span_hi = min(hi, pass_best_s + step)
        steps = _REFINE_STEPS

    if not _math.isfinite(best_u) or best_u <= 0.0 or best_s <= Decimal("0"):
        return Decimal("0"), (best_u if _math.isfinite(best_u) else 0.0), delta_u_at_min
    return best_s, best_u, delta_u_at_min


# ===========================================================================
# compute_candidate_economics — the full Stage 8a economics (edge + size).
# ===========================================================================

def compute_candidate_economics(
    candidate_route: CandidateRoute,
    *,
    joint_q: JointQ,
    band: JointQBand,
    sizing_candidate: NativeSideCandidate,
    matrix: FamilyPayoffMatrix,
    exposure: PortfolioExposureVector,
    max_stake_usd: Decimal | None = None,
    alpha: float | None = None,
    guarded_payoff_q_lcb: float | None = None,
) -> CandidateEconomics:
    """Compute one candidate's Arrow-Debreu edge + vector-argmax size (spec 759-791).

    Combines the two corrected transformations into the candidate's economics:

      * the VECTOR edge (``point_ev`` = ``q @ payoff - cost``; ``edge_lcb`` =
        ``payoff_q_lcb - cost`` where ``payoff_q_lcb`` is read directly from the
        q-band/payoff vector or, when the empirical qLCB reliability guard supplied
        a candidate-local lower bound, ``q_safe``); and
      * the VECTOR size (``optimal_stake_usd`` = ``argmax_s robust_delta_u``;
        ``optimal_delta_u`` / ``delta_u_at_min`` the robust ΔU at s* / min-order).

    ``q_dot_payoff`` records the point fair value as TELEMETRY (the scalar is never the
    selection quantity). ``cost`` carries the route's typed ``ExecutionPrice``.

    ``sizing_candidate`` is the ``NativeSideCandidate`` that carries the executable cost
    curve the ΔU stake-sweep walks (the family-ranker candidate object); it MUST be the
    same side and bin as ``candidate_route`` (a NO route is sized with a NO candidate).

    ``guarded_payoff_q_lcb`` is a candidate-local reliability lower bound on this route's
    payoff probability. It does not move point q / μ and does not mutate the global band;
    it makes edge_lcb and robust ΔU consume the same conservative side-specific q_safe.
    """
    payoff = np.asarray(candidate_route.payoff_vector, dtype=float)
    _validate_alignment(payoff, joint_q, band)

    if sizing_candidate.side != candidate_route.side:
        raise PayoffVectorError(
            f"SIZING_SIDE_MISMATCH: route side {candidate_route.side!r} != sizing "
            f"candidate side {sizing_candidate.side!r}"
        )
    if sizing_candidate.bin_id != candidate_route.bin_id:
        raise PayoffVectorError(
            f"SIZING_BIN_MISMATCH: route bin {candidate_route.bin_id!r} != sizing "
            f"candidate bin {sizing_candidate.bin_id!r}"
        )

    cost = _route_cost_value(candidate_route.route_cost)
    q_dot = point_fair_value(joint_q, payoff)
    point_ev = q_dot - cost
    q_guard = _validate_guarded_payoff_q_lcb(guarded_payoff_q_lcb)
    payoff_q_lcb = (
        float(q_guard)
        if q_guard is not None
        else payoff_lower_bound(band, payoff, q_dot_payoff=q_dot, alpha=alpha)
    )
    payoff_q_lcb = min(max(float(payoff_q_lcb), 0.0), float(q_dot))
    edge_lcb = payoff_q_lcb - cost

    optimal_stake, optimal_delta_u, delta_u_at_min = optimize_vector_stake(
        sizing_candidate,
        band=band,
        omega=joint_q.omega,
        matrix=matrix,
        exposure=exposure,
        max_stake_usd=max_stake_usd,
        alpha=alpha,
        guarded_payoff_q_lcb=q_guard,
    )

    chosen_stake_cost = None
    chosen_stake_point_ev = None
    chosen_stake_edge_lcb = None
    if (
        optimal_stake > Decimal("0")
        and optimal_delta_u > 0.0
        and sizing_candidate.executable_cost_curve is not None
    ):
        try:
            chosen_stake_cost = sizing_candidate.executable_cost_curve.avg_cost(
                optimal_stake
            )
            if chosen_stake_cost.currency != "probability_units":
                raise PayoffVectorError(
                    "CHOSEN_STAKE_COST_NOT_PROBABILITY_UNITS: "
                    f"route {candidate_route.route_cost.route_id!r} chosen-stake cost "
                    f"currency is {chosen_stake_cost.currency!r}"
                )
            chosen_cost = float(chosen_stake_cost.value)
            chosen_stake_point_ev = q_dot - chosen_cost
            chosen_stake_edge_lcb = payoff_q_lcb - chosen_cost
        except Exception:  # noqa: BLE001 - computed live economics must not trade unpriced stake.
            chosen_stake_cost = None
            chosen_stake_point_ev = None
            chosen_stake_edge_lcb = float("-inf")

    return CandidateEconomics(
        candidate_id=candidate_route.candidate_id,
        point_ev=point_ev,
        edge_lcb=edge_lcb,
        delta_u_at_min=delta_u_at_min,
        optimal_stake_usd=optimal_stake,
        optimal_delta_u=optimal_delta_u,
        q_dot_payoff=q_dot,
        cost=candidate_route.route_cost.avg_cost,
        route_id=candidate_route.route_cost.route_id,
        payoff_q_lcb=payoff_q_lcb,
        chosen_stake_cost=chosen_stake_cost,
        chosen_stake_point_ev=chosen_stake_point_ev,
        chosen_stake_edge_lcb=chosen_stake_edge_lcb,
    )


# ===========================================================================
# Live candidate pass (spec lines 793-802) — selects on the VECTOR quantities.
# The scalar trade_score is telemetry only and CANNOT select.
# ===========================================================================

def live_candidate_passes(
    economics: CandidateEconomics,
    candidate_route: CandidateRoute,
    *,
    direction_law_proof_present: bool,
    market_coherence_accepted: bool,
) -> bool:
    """Whether a candidate may go LIVE (spec lines 793-802).

    The live pass is the AND of the VECTOR conditions (spec 797-802):

        candidate.edge_lcb > 0
        candidate.delta_u_at_min > 0
        candidate.optimal_delta_u > 0
        executable route available
        native side proof present
        market coherence accepted

    EVERY condition is a vector / structural quantity. The scalar ``q - price`` trade
    score is NOT one of them — :func:`scalar_trade_score` exists ONLY as logged
    telemetry and is never read here. So the bad selection (promoting on a
    scalar ``q_i - price`` that ignores the rest of the payoff vector and the family
    exposure) is unconstructable: the only inputs to the pass are the vector edge_lcb,
    the vector DeltaU at min and at s*, the route's executability, the native-side proof,
    and the coherence report.
    """
    edge_lcb = (
        economics.chosen_stake_edge_lcb
        if economics.chosen_stake_edge_lcb is not None
        else economics.edge_lcb
    )
    return (
        edge_lcb > 0.0
        and economics.delta_u_at_min > 0.0
        and economics.optimal_delta_u > 0.0
        and candidate_route.route_cost.executable
        and direction_law_proof_present
        and market_coherence_accepted
    )


def scalar_trade_score(joint_q: JointQ, candidate_route: CandidateRoute) -> float:
    """TELEMETRY ONLY — the scalar ``q - price`` edge (spec line 1184; CANNOT select).

    This is the demoted scalar ``trade_score``: the point fair value of the payoff minus
    the route cost, returned as a bare float for LOGGING. It is intentionally NOT used by
    :func:`live_candidate_passes` — the live pass runs on ``edge_lcb`` / ``delta_u_at_min``
    / ``optimal_delta_u`` only. It is the SAME number as ``CandidateEconomics.point_ev``,
    surfaced under a name that documents its telemetry-only status. There is no code path
    where this value reaches the selection decision; the scalar score is logged, not acted
    on (the operator mandate: the scalar q-price is logged but not selected on).
    """
    payoff = np.asarray(candidate_route.payoff_vector, dtype=float)
    cost = _route_cost_value(candidate_route.route_cost)
    return point_fair_value(joint_q, payoff) - cost


# ---------------------------------------------------------------------------
# build_candidate_route — assemble the Arrow-Debreu candidate from its instrument.
# ---------------------------------------------------------------------------

def build_candidate_route(
    *,
    candidate_id: str,
    instrument: Instrument,
    route_cost: RouteCost,
    omega: OutcomeSpace,
) -> CandidateRoute:
    """Assemble a ``CandidateRoute`` — its payoff vector IS ``instrument.payoff_vector``.

    The payoff vector is read ONCE from the instrument over the complete Omega, so the
    candidate's edge runs over the exact Arrow-Debreu vector the instrument defines (``e_i``
    for YES_i, ``1 - e_i`` for NO_i). There is no place to pass a payoff vector that
    disagrees with the instrument — it is derived, not supplied.
    """
    payoff = instrument.payoff_vector(omega)
    return CandidateRoute(
        candidate_id=candidate_id,
        instrument=instrument,
        route_cost=route_cost,
        payoff_vector=payoff,
        side=instrument.side,
        bin_id=instrument.bin_id,
    )
