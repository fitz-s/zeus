# Created: 2026-06-14
# Last reused or audited: 2026-06-14
# Authority basis: docs/rebuild/consult_build_spec.md
#   ("Create src/decision/family_decision_engine.py" block lines 854-904: the
#   FamilyDecision dataclass 858-871 [decision_id, case, predictive, omega, joint_q,
#   band, family_book, market_coherence, candidates, selected, no_trade_reason,
#   receipt_hash]; the decide() algorithm 876-901 — event_resolution -> outcome_space
#   -> read fresh models + day0 -> predictive_builder.build -> (if not live_eligible:
#   no_trade PREDICTIVE_DISTRIBUTION_NOT_LIVE_ELIGIBLE) -> joint_q -> joint_q_band ->
#   family_book -> market_implied_q -> coherence -> routes -> payoff candidates ->
#   filter [direction_law_ok, coherence_allows, edge_lcb>0 & optimal_delta_u>0] ->
#   selected = max robust utility density) and the Stage 8 block lines 1166-1184.
#   Reconciled against docs/evidence/qkernel_rebuild/spec_vs_live_drift_ledger.md
#   (GREENFIELD — no live edits; reactor wiring is Wave 5. The scalar robust_trade_score
#   is telemetry only — it CANNOT select. This is the ONLY decision authority; it
#   ASSEMBLES the already-built spine modules, never re-implements them).
#   Live dependencies (ALL already built; imported, never re-implemented):
#     - src/probability/event_resolution.py::{event_resolution_for_city, EventResolution}
#     - src/probability/outcome_space.py::{OutcomeSpace, OutcomeBin}
#     - src/forecast/predictive_distribution_builder.py::{PredictiveDistribution,
#                       PredictiveDistributionBuilder} (predictive_builder.build)
#     - src/forecast/types.py::{ForecastCase, FreshModelSet}
#     - src/forecast/day0_conditioner.py::Day0ObservationState (the day0 obs read)
#     - src/probability/joint_q.py::{JointQ, build_joint_q}             (joint_q)
#     - src/probability/joint_q_band.py::{JointQBand, build_joint_q_band} (q band)
#     - src/execution/family_book.py::FamilyBook                        (family_book)
#     - src/decision/market_coherence.py::{assess_market_coherence,
#                       MarketCoherenceReport, build_market_implied_q} (coherence + market q)
#     - src/execution/negrisk_routes.py::{NegRiskRouteSet, RouteCost,
#                       build_negrisk_route_set}                        (routes)
#     - src/probability/instruments.py::Instrument
#     - src/decision/payoff_vector.py::{CandidateRoute, CandidateEconomics,
#                       build_candidate_route, compute_candidate_economics,
#                       live_candidate_passes, scalar_trade_score}      (payoff candidates)
#     - src/strategy/utility_ranker.py::{FamilyPayoffMatrix, PortfolioExposureVector}
#                       (the ΔU sizing geometry the payoff layer maximizes over)
#     - src/contracts/native_side_candidate.py::NativeSideCandidate     (the sizing candidate)
"""family_decision_engine — the terminal decision orchestrator (Stage 8b).

This is Stage 8b of the q-kernel rebuild (consult_build_spec.md lines 854-904, Stage 8
block 1166-1184). It is the SINGLE decision authority: one ``decide()`` over the whole
spine that runs the full pipeline once and emits a ``FamilyDecision``. It ASSEMBLES the
already-built modules — it does not re-implement any of forecast, q, band, family book,
coherence, routes, or payoff economics.

THE PIPELINE (spec decide() lines 876-901; the order is the contract):

    resolution = event_resolution_for_city(case)         # the one settlement identity
    omega      = outcome_space_from_family(family, resolution)   # the complete Omega
    models     = fresh_model_reader.read(case)           # fresh model members
    obs        = day0_reader.read(case)                  # observed running extreme
    predictive = predictive_builder.build(case, models, obs)     # ONE predictive dist

    if not predictive.live_eligible:                     # the FIRST gate
        return no_trade("PREDICTIVE_DISTRIBUTION_NOT_LIVE_ELIGIBLE")

    q          = joint_q_builder.build(predictive, omega)        # ONE normalized joint q
    band       = q_band_builder.build(predictive, omega, q)      # the coherent q band
    family_book= family_book_builder.build(omega, snapshots)     # executable family book
    market_q   = market_implied_q_builder.build(family_book)     # de-frictioned market q
    coherence  = market_coherence.evaluate(q, market_q)          # the typed incident report

    routes     = route_builder.build(omega, family_book)         # the family route set
    candidates = payoff_decision_builder.score(q, band, routes, portfolio)  # economics

    candidates = [c for c in candidates if direction_law_ok(c)]
    candidates = [c for c in candidates if coherence_allows(c)]
    candidates = [c for c in candidates if c.edge_lcb > 0 and c.optimal_delta_u > 0]

    selected   = max(candidates, key=lambda c: c.optimal_delta_u / c.optimal_stake_usd)
    return FamilyDecision(...)

THE THREE CORRECTED TRANSFORMATIONS THIS ORCHESTRATOR PRESERVES (operator law — make the
bad output mathematically impossible; NO gate/cap/clamp/haircut that catches a bad value
and leaves a broken transform in place):

  1. SELECTION IS ROBUST UTILITY DENSITY OVER THE SURVIVORS, NEVER A SCALAR TRADE
     SCORE (operator Shanghai correction over spec lines 900-903, 1184). The candidate filter chain is
     ``direction_law_ok -> coherence_allows -> (edge_lcb > 0 AND optimal_delta_u > 0)``,
     and the survivor with the maximum ``optimal_delta_u / optimal_stake_usd`` is selected.
     Total ``optimal_delta_u`` remains a secondary ordering signal. The scalar
     ``robust_trade_score`` (``scalar_trade_score`` from payoff_vector) is computed for
     EVERY candidate as TELEMETRY on the receipt, but it is never one of the filter
     conditions and never the argmax key. There is no code path where the scalar reaches
     the selection — the inputs are the vector quantities (``edge_lcb``, ``optimal_delta_u``,
     ``optimal_stake_usd``) and the structural proofs (direction law, coherence). A reversion
     that selected ``argmax robust_trade_score`` would pick a different candidate; here the
     scalar cannot select.

  2. COHERENCE BLOCKS BEFORE SCORING (spec lines 891, 897, 953; market_coherence Stage 9).
     ``coherence_allows(c)`` consults the typed ``MarketCoherenceReport``: when the report
     is ``INCOHERENT_BLOCK_LIVE`` and the candidate's bin is an offending bin, the
     candidate is DROPPED from the survivor list — it never reaches the edge/ΔU filter,
     so a Tokyo q=0.47 vs deep ask=0.001 incident dies BEFORE scoring (its
     ``optimal_delta_u`` is irrelevant because it was filtered out). The coherence filter
     runs SECOND, after direction law and BEFORE the edge gate — exactly the spec order
     (lines 896-898). This is honoring the typed-incident report (the spec transformation),
     not a bolted-on cap: the q is never mutated; the candidate is removed.

  3. LIVE ELIGIBILITY IS THE FIRST GATE (spec lines 884-885). When the predictive
     distribution is not live-eligible (no σ authority / refused center), the whole
     decision returns ``no_trade("PREDICTIVE_DISTRIBUTION_NOT_LIVE_ELIGIBLE")`` BEFORE q is
     ever integrated — there is no width-less q, no degenerate band, no candidate. The
     ineligible distribution still carries the full receipt contract, so the no-trade
     receipt is reconstructable.

EVERY EXIT EMITS A FamilyDecision WITH A receipt_hash (spec line 871). A decision that
selects a trade carries the selected candidate; a decision that selects nothing carries a
``no_trade_reason`` naming the first gate that emptied the survivor set
(``PREDICTIVE_DISTRIBUTION_NOT_LIVE_ELIGIBLE`` / ``MARKET_INCOHERENT_BLOCK_LIVE`` /
``NO_DIRECTION_LAW_CANDIDATE`` / ``NO_POSITIVE_EDGE_CANDIDATE`` / ...) AND the full
``candidates`` tuple so the no-trade is auditable. The ``receipt_hash`` anchors the exact
(predictive, omega, q, band, family_book, coherence, candidates, selected) tuple.

DIRECTION LAW (spec lines 947-951; relaxed 2026-06-15, settlement-justified): ``YES_i`` is
legal when buying the forecast bin OR when the candidate's point-q is inside the
settlement-validated calibrated domain (``point_q >= CALIBRATED_NONMODAL_Q_FLOOR``); ``NO_i``
is legal only when its bin is NOT the forecast bin. The forecast bin is the modal bin of the
joint q. The original modal-only YES rule was settlement-INVERTED: 6,450 settled non-modal
bin-obs grade the non-modal tail in (0.05,0.35] at pred/real 1.05× (CALIBRATED) while the
modal bin the rule TRUSTS grades 1.28× (1.82× at 72h, OVER-dispersed) — it suppressed the
calibrated side and admitted the over-dispersed side (docs/evidence/qkernel_rebuild/
nonmodal_bin_calibration_2026-06-15.md F1/F2). YES is now admitted on any bin whose point-q
reaches the calibrated floor, with the conservative ``edge_lcb > 0`` gate as the trade
authority; below the floor (the ≥3-4σ far tail, no settlement coverage, where edge_lcb
amplifies the over-dispersed served σ) YES stays modal-only. A NO on the modal bin remains
unconstructable.

GREENFIELD / WAVE-5 WIRING. The spec ``decide(case, family, snapshots, portfolio)``
references ``fresh_model_reader``, ``day0_reader``, ``predictive_builder``,
``joint_q_builder``, ``q_band_builder``, ``family_book_builder``,
``market_implied_q_builder``, ``route_builder``, ``payoff_decision_builder`` as collaborator
objects the reactor owns. This module defines them as small injected Protocols /
callables (the readers) and reuses the live builder functions directly (the builders), so
the engine is fully testable now and the reactor injects the real readers at Wave 5
WITHOUT editing this file. No live file is touched.

DRIFT RESOLVED (recorded per operator law; see docs/rebuild/impl_w4_family_decision_engine.md):

  * The spec ``decide()`` passes ``family`` and ``snapshots`` as opaque inputs. Resolved
    toward the live types: ``family`` is the already-built ``OutcomeSpace`` (the complete
    Omega — ``outcome_space_from_family`` is the identity when the caller already holds the
    Omega; a builder hook is provided for the reactor to construct it from a venue family),
    and ``snapshots`` is the ``Mapping[str, ExecutableMarketSnapshot]`` keyed by bin_id the
    ``FamilyBook`` builder already consumes. ``portfolio`` is the
    ``PortfolioExposureVector`` (A_y) the ΔU sizing measures against, plus the
    ``FamilyPayoffMatrix`` (the outcome geometry) — both live ``utility_ranker`` types the
    payoff layer already uses.

  * The spec writes ``market_coherence.evaluate(q, market_q)``. The live API is
    ``assess_market_coherence(joint_q=..., family_book=..., candidate_bin_ids=...)`` which
    builds the market-implied q FROM the family book internally (the de-frictioned midpoint
    projection) and compares per candidate bin. Resolution (toward the live API): the
    engine calls ``assess_market_coherence`` over the family book and the candidate bins —
    the market-implied q is built inside it (the ``market_implied_q_builder.build`` step is
    subsumed), and the report is the same typed ``MarketCoherenceReport`` the spec's
    ``coherence`` is. The market-implied q is ALSO surfaced on the decision (via
    ``build_market_implied_q``) for the receipt.

  * The spec ``payoff_decision_builder.score(q, band, routes, portfolio)`` returns a list
    of ``CandidateEconomics``. The live ``compute_candidate_economics`` is PER candidate
    route + sizing candidate. Resolution: the engine enumerates one ``CandidateRoute`` per
    (bin, side) executable route in the route set, pairs each with its ``NativeSideCandidate``
    sizing object, and computes the economics — the ``score`` over the family is the
    per-candidate fold.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace
from decimal import Decimal
from typing import (
    Mapping,
    Optional,
    Protocol,
    Sequence,
)

import numpy as np

from src.contracts.executable_market_snapshot import ExecutableMarketSnapshot
from src.contracts.native_side_candidate import NativeSideCandidate
from src.decision.market_coherence import (
    MarketCoherenceReport,
    MarketImpliedQ,
    assess_market_coherence,
    build_market_implied_q,
)
from src.decision.payoff_vector import (
    CandidateEconomics,
    CandidateRoute,
    PayoffVectorError,
    build_candidate_route,
    compute_candidate_economics,
    live_candidate_passes,
    scalar_trade_score,
)
from src.execution.family_book import FamilyBook, family_book_from_snapshots
from src.execution.negrisk_routes import (
    NegRiskRouteSet,
    RouteCost,
    build_negrisk_route_set,
)
from src.forecast.day0_conditioner import Day0ObservationState
from src.forecast.predictive_distribution_builder import PredictiveDistribution
from src.forecast.types import ForecastCase, FreshModelSet
from src.probability.instruments import Instrument, InstrumentError
from src.decision.qlcb_reliability_guard import apply_guard as _apply_qlcb_guard
from src.probability.joint_q import JointQ, build_joint_q
from src.probability.joint_q_band import JointQBand, build_joint_q_band
from src.probability.outcome_space import OutcomeSpace
from src.strategy.utility_ranker import (
    FamilyPayoffMatrix,
    PortfolioExposureVector,
)

# The no-trade reason vocabulary (the first gate that emptied the survivor set). These are
# the decision-engine-level reasons; the reactor's broader NoTradeReason vocabulary is
# wired at Wave 5. Each names exactly WHERE the pipeline stopped, so a no-trade receipt is
# auditable end-to-end.
NO_TRADE_PREDICTIVE_NOT_LIVE_ELIGIBLE = "PREDICTIVE_DISTRIBUTION_NOT_LIVE_ELIGIBLE"
NO_TRADE_NO_EXECUTABLE_ROUTE = "NO_EXECUTABLE_ROUTE_CANDIDATE"
NO_TRADE_NO_DIRECTION_LAW = "NO_DIRECTION_LAW_CANDIDATE"
NO_TRADE_MARKET_INCOHERENT = "MARKET_INCOHERENT_BLOCK_LIVE"
NO_TRADE_NO_POSITIVE_EDGE = "NO_POSITIVE_EDGE_CANDIDATE"
# q_lcb empirical reliability guard (FINAL no-shadow execution flow §6): every candidate's
# served q_lcb was deflated to 0 (abstain) because its reliability cell is thin / below floor.
NO_TRADE_QLCB_RELIABILITY_ABSTAIN = "QLCB_RELIABILITY_GUARD_ABSTAIN"


class FamilyDecisionError(ValueError):
    """Raised when a decision cannot be assembled coherently (a routing/wiring fault).

    Fail-closed signal: the candidate routes and the joint q disagree about the Omega, a
    sizing candidate is missing for an enumerated route, or a builder returned an object
    over a different Omega than the case resolves. These are wiring faults the reactor must
    fix — they are NOT no-trade outcomes (a no-trade is a valid ``FamilyDecision`` with a
    ``no_trade_reason``; this is a structural impossibility).
    """


# ===========================================================================
# Injected reader protocols (the reactor owns the real readers at Wave 5).
# ===========================================================================

class FreshModelReader(Protocol):
    """Reads the fresh model member set for a case (spec ``fresh_model_reader.read``)."""

    def read(self, case: ForecastCase) -> FreshModelSet: ...


class Day0Reader(Protocol):
    """Reads the observed running extreme for a case (spec ``day0_reader.read``).

    Returns ``None`` (or an inactive ``Day0ObservationState``) when no day0 observation is
    available — the predictive builder then serves the bare envelope-enforced center.
    """

    def read(self, case: ForecastCase) -> Optional[Day0ObservationState]: ...


class PredictiveBuilder(Protocol):
    """Builds the ONE predictive distribution (spec ``predictive_builder.build``)."""

    def build(
        self,
        case: ForecastCase,
        models: FreshModelSet,
        obs: Optional[Day0ObservationState] = None,
    ) -> PredictiveDistribution: ...


class FamilyBookBuilder(Protocol):
    """Builds the executable family book (spec ``family_book_builder.build``).

    Maps the complete Omega + the per-sibling snapshots to a captured ``FamilyBook``. The
    default implementation is ``family_book_from_snapshots`` (the live builder that parses
    each ``ExecutableMarketSnapshot.orderbook_depth_jsonb`` into native ladders). The reactor
    may inject a different source (e.g. a pre-captured book), and a test may inject a builder
    that returns a hand-built ``FamilyBook`` directly.
    """

    def __call__(
        self,
        *,
        omega: OutcomeSpace,
        snapshots_by_bin_id: Mapping[str, ExecutableMarketSnapshot],
        captured_at_utc,
    ) -> FamilyBook: ...


class RouteSetBuilder(Protocol):
    """Builds the family route set (spec ``route_builder.build``).

    The default implementation is ``build_negrisk_route_set`` (the live engine that
    walks the family book's native ask ladders, size-aware). The reactor may inject a
    different source — e.g. a PROOF-NATIVE direct-route builder that prices each direct
    YES/NO route at the reactor ``_CandidateProof``'s own ``execution_price`` (the exact
    maker/taker cost the submit path will use), so a maker buy_no into an empty NO ask
    is priced as the resting bid, NOT discarded by the ask-ladder taker cost. When a
    proof-native builder is injected the engine's ``enable_negrisk_routes`` is irrelevant
    (the builder owns the route surface).
    """

    def __call__(
        self,
        family_book: FamilyBook,
        *,
        shares,
        enable_negrisk_routes: bool,
    ) -> NegRiskRouteSet: ...


# ===========================================================================
# CandidateDecision — one enumerated candidate's full economics + provenance.
# (Internal carrier; the FamilyDecision.candidates tuple is CandidateEconomics
# per the spec, but the engine threads the route + side + scalar telemetry alongside.)
# ===========================================================================

@dataclass(frozen=True)
class CandidateDecision:
    """One enumerated candidate: its route, sizing economics, and decision flags.

    Carries everything the filter chain and the receipt need:

    * ``route`` — the ``CandidateRoute`` (instrument + payoff vector + executable route).
    * ``economics`` — the ``CandidateEconomics`` (point_ev / edge_lcb / delta_u_at_min /
      optimal_stake_usd / optimal_delta_u / q_dot_payoff / cost / route_id).
    * ``direction_law_ok`` — whether the candidate's (side, bin) is direction-law-legal
      against the forecast (modal) bin.
    * ``coherence_allows`` — whether the market-coherence report does NOT block this bin.
    * ``robust_trade_score`` — the SCALAR ``q - price`` telemetry (point fair value minus
      cost). RECORDED for the receipt; NEVER read by the selection. This is the demoted
      scalar that cannot select a trade.
    * ``q_lcb_guard_basis`` / ``q_lcb_guard_abstained`` — the side-aware OOF reliability
      verdict applied to this candidate. A NO-on-modal direction relaxation can only use
      an active OOF verdict, never an inert/missing evidence path.
    """

    route: CandidateRoute
    economics: CandidateEconomics
    direction_law_ok: bool
    coherence_allows: bool
    robust_trade_score: float
    q_lcb_guard_basis: str = ""
    q_lcb_guard_abstained: bool = False
    q_lcb_guard_cell_key: str = ""


# ===========================================================================
# FamilyDecision (spec lines 858-871) — EXACT field names, frozen.
# ===========================================================================

@dataclass(frozen=True)
class FamilyDecision:
    """The terminal decision over the whole spine (spec lines 858-871).

    Field names are verbatim from consult_build_spec.md. EVERY ``decide()`` exit returns
    one of these with a ``receipt_hash``; a no-trade carries ``selected=None`` and a
    ``no_trade_reason``.

    * ``decision_id`` — a stable id for this decision (``{family_id}@{captured}``-derived).
    * ``case`` — the ``ForecastCase`` this decision is for.
    * ``predictive`` — the ONE ``PredictiveDistribution`` (the only input to q).
    * ``omega`` — the complete ``OutcomeSpace`` (Omega) the whole decision ran over.
    * ``joint_q`` — the ONE normalized joint q (``None`` only when the predictive
      distribution was not live-eligible — q was never integrated).
    * ``band`` — the coherent ``JointQBand`` (``None`` likewise on the ineligible path).
    * ``family_book`` — the executable family book (``None`` on the ineligible path, since
      no book read is needed for a no-trade-before-q).
    * ``market_coherence`` — the typed ``MarketCoherenceReport`` (``None`` on the ineligible
      path). Its ``status`` is the calibration-incident contract.
    * ``candidates`` — the ``CandidateEconomics`` for EVERY enumerated candidate (passing or
      not), so the decision is fully auditable. Empty on the ineligible path.
    * ``selected`` — the ``CandidateEconomics`` of the chosen trade (maximum robust utility
      density over the survivors), or ``None`` for a no-trade.
    * ``no_trade_reason`` — the reason the survivor set was empty (``None`` when a trade was
      selected). Names the first gate that emptied it.
    * ``receipt_hash`` — a deterministic hash over the whole decision tuple (the receipt
      anchor — spec line 871).
    """

    decision_id: str
    case: ForecastCase
    predictive: PredictiveDistribution
    omega: OutcomeSpace
    joint_q: Optional[JointQ]
    band: Optional[JointQBand]
    family_book: Optional[FamilyBook]
    market_coherence: Optional[MarketCoherenceReport]
    candidates: tuple[CandidateEconomics, ...]
    selected: Optional[CandidateEconomics]
    no_trade_reason: Optional[str]
    receipt_hash: str

    # Engine provenance (not in the spec field list — carried so the receipt / a Wave-5
    # consumer can see the per-candidate route, direction-law and coherence flags, and the
    # scalar telemetry without re-deriving them). Excluded from the spec contract.
    candidate_decisions: tuple[CandidateDecision, ...] = ()
    market_implied_q: Optional[MarketImpliedQ] = None


# ===========================================================================
# Forecast (modal) bin — the direction-law reference (spec lines 947-951).
# ===========================================================================

def forecast_bin_id(joint_q: JointQ) -> str:
    """The forecast bin — the modal (max-mass) bin of the joint q (spec line 948-951).

    The direction law is anchored on the bin the predictive distribution most favors. The
    modal bin of the normalized joint q IS that bin: it carries the most settlement mass,
    so a YES on it is "buying the forecast bin" and a NO on it is illegal (you would be
    betting against your own forecast). Ties resolve to the first max bin (deterministic).
    """
    q = np.asarray(joint_q.q, dtype=float)
    if q.shape[0] == 0:
        raise FamilyDecisionError("EMPTY_OMEGA: joint q has no bins; no forecast bin")
    i = int(np.argmax(q))
    return joint_q.omega.bins[i].bin_id


# Settlement-validated lower edge of the calibrated non-modal q domain.
#
# This is NOT an arbitrary cap / throttle / q-haircut (operator law: no caps). It is the
# empirically-measured lower boundary of the q region where the non-modal forecast q is
# settlement-CALIBRATED. 6,450 settled non-modal bin-observations (313 families, WU UMA
# settlement source, 2026-06-08..06-14) grade the non-modal tail in (0.05,0.35] at
# pred/real ≈ 1.05× — calibrated to sampling noise (docs/evidence/qkernel_rebuild/
# nonmodal_bin_calibration_2026-06-15.md F1 / STAT:effect_size). BELOW this floor the bins
# are the ≥3-4σ far tail where (a) the live served σ is graded over-dispersed/honest, never
# over-tight (docs/rebuild/arm_replay_report.md §4: predictive_rss std(z)=0.92, σ/RMSE=1.00),
# so a wide σ + the conservative edge_lcb quantile AMPLIFY rather than shield far-tail q
# inflation, and (b) settlement coverage is absent (the q_lcb>price executable test was not
# runnable on the far tail). YES is admitted ONLY inside the proven-calibrated domain; the
# far tail stays modal-only until an executable settlement test extends the validated region.
CALIBRATED_NONMODAL_Q_FLOOR: float = 0.05


def direction_law_ok(
    route: CandidateRoute, *, forecast_bin: str, point_q: float
) -> bool:
    """Whether ``route`` is direction-law-legal against the forecast bin (spec 947-951).

    * ``YES_i`` is legal when ``i`` IS the forecast bin (buying the forecast bin) OR when the
      candidate's point-q ``point_q`` is at or above the settlement-validated calibration
      floor :data:`CALIBRATED_NONMODAL_Q_FLOOR` — the region where settlement grading proves
      the non-modal q is calibrated, so the bin is a tradeable Arrow-Debreu claim whose
      direction is a structural proof (#97), not a market view. The conservative edge gate
      (``edge_lcb > 0``) downstream remains the trade authority within this domain.
    * ``NO_i`` is legal ONLY when ``i`` is NOT the forecast bin (its payoff vector
      ``1 - e_i`` wins on the forecast bin — "not forecast bin"). NO direction is unchanged.

    The pre-2026-06-15 modal-only YES rule suppressed the settlement-CALIBRATED non-modal
    side (pred/real 1.05×) while admitting the OVER-dispersed modal bin (pred/real 1.28×,
    1.82× at 72h) — the law's premise was inverted (report F1/F2). Relaxing it inside the
    validated q domain restores the suppressed-but-real edge (RULE 1: a no-edge verdict is
    presumed our defect until settlement proves otherwise; here settlement proves the edge).
    The far-tail floor is the only retained structural constraint: below it the calibration
    evidence does not reach and edge_lcb amplifies σ-spread, so YES stays modal-only there.
    """
    if route.side == "YES":
        return (
            route.bin_id == forecast_bin
            or point_q >= CALIBRATED_NONMODAL_Q_FLOOR
        )
    # NO_i is legal exactly when its bin is NOT the forecast bin.
    return route.bin_id != forecast_bin


def coherence_allows(route: CandidateRoute, report: MarketCoherenceReport) -> bool:
    """Whether the market-coherence report does NOT block ``route``'s bin (spec 891-953).

    The candidate is allowed past the coherence filter unless the report is
    ``INCOHERENT_BLOCK_LIVE`` AND the candidate's bin is one of the offending bins. A
    ``COHERENT`` / ``INSUFFICIENT_MARKET_DEPTH`` / ``NO_MARKET_Q`` report allows every
    candidate (an insufficiently-deep / absent market never fabricates a block — the
    coherence module already encoded that, so this filter just reads ``offending_bins``).

    The Tokyo q=0.47 vs deep ask=0.001 candidate is dropped HERE — before the edge/ΔU
    gate — because its bin is in ``offending_bins`` of an ``INCOHERENT_BLOCK_LIVE`` report.
    """
    if report.status != "INCOHERENT_BLOCK_LIVE":
        return True
    return route.bin_id not in report.offending_bins


# ===========================================================================
# Candidate enumeration — one CandidateRoute per (bin, side) executable route,
# paired with its NativeSideCandidate sizing object.
# ===========================================================================

def _instrument_for(route_cost: RouteCost) -> Instrument:
    """The instrument a route acquires (the route already carries it)."""
    return route_cost.instrument


# ===========================================================================
# The decision engine — the single orchestrator.
# ===========================================================================

class FamilyDecisionEngine:
    """The terminal decision orchestrator — ONE decide() over the whole spine.

    Holds the injected readers/builders the reactor owns at Wave 5 (a fresh-model reader, a
    day0 reader, and the predictive builder). The q / band / family-book / coherence / route
    / payoff steps reuse the live builder FUNCTIONS directly (no injection needed — they are
    pure). ``decide`` is the only public method; it returns a ``FamilyDecision`` for EVERY
    input (a wiring fault raises ``FamilyDecisionError``; every TRADING outcome — trade or
    no-trade — is a valid decision).
    """

    def __init__(
        self,
        *,
        fresh_model_reader: FreshModelReader,
        day0_reader: Day0Reader,
        predictive_builder: PredictiveBuilder,
        family_book_builder: Optional[FamilyBookBuilder] = None,
        route_set_builder: Optional[RouteSetBuilder] = None,
        n_band_draws: int = 4000,
        band_alpha: float = 0.05,
        enable_negrisk_routes: bool = True,
        depth_reference_size: float = 100.0,
        min_depth: float = 1.0,
        max_spread: float = 0.10,
    ) -> None:
        self._fresh_model_reader = fresh_model_reader
        self._day0_reader = day0_reader
        self._predictive_builder = predictive_builder
        # Default the family-book builder to the live snapshot builder; a test / reactor may
        # inject one that returns a pre-captured FamilyBook.
        self._family_book_builder: FamilyBookBuilder = (
            family_book_builder
            if family_book_builder is not None
            else family_book_from_snapshots
        )
        # Default the route-set builder to the live neg-risk engine; the reactor injects a
        # PROOF-NATIVE direct-route builder (maker/taker cost from each proof's
        # execution_price) so the v1 direct-native policy is not priced off the ask ladder.
        self._route_set_builder: RouteSetBuilder = (
            route_set_builder if route_set_builder is not None else build_negrisk_route_set
        )
        self._n_band_draws = int(n_band_draws)
        self._band_alpha = float(band_alpha)
        self._enable_negrisk_routes = bool(enable_negrisk_routes)
        self._depth_reference_size = float(depth_reference_size)
        self._min_depth = float(min_depth)
        self._max_spread = float(max_spread)

    # ------------------------------------------------------------------ decide
    def decide(
        self,
        case: ForecastCase,
        omega: OutcomeSpace,
        snapshots: Mapping[str, ExecutableMarketSnapshot],
        *,
        portfolio: PortfolioExposureVector,
        matrix: FamilyPayoffMatrix,
        captured_at_utc,
        sizing_candidates: Mapping[tuple[str, str], NativeSideCandidate],
        max_stake_usd: Optional[Decimal] = None,
        shares_for_routing: Decimal = Decimal("1"),
        licensed_model_superiority=None,
    ) -> FamilyDecision:
        """Run the full decision pipeline once and emit a ``FamilyDecision`` (spec 876-901).

        Args:
            case: the ``ForecastCase`` (its ``resolution`` is the settlement identity).
            omega: the complete ``OutcomeSpace`` (Omega) the decision runs over —
                ``outcome_space_from_family`` already resolved (the reactor builds it; the
                engine threads it). Its ``resolution`` MUST match ``case.resolution``.
            snapshots: ``ExecutableMarketSnapshot`` per sibling, keyed by bin_id — the
                family book read.
            portfolio: the ``PortfolioExposureVector`` (A_y) the ΔU sizing measures against.
            matrix: the ``FamilyPayoffMatrix`` (the outcome geometry) the ΔU sizing uses.
            captured_at_utc: the family-book capture instant (tz-aware UTC).
            sizing_candidates: a ``NativeSideCandidate`` per (bin_id, side) carrying the
                executable cost curve the ΔU stake-sweep walks. A route with no sizing
                candidate is enumerated for the receipt but cannot be sized (it fails the
                live pass on a non-positive ΔU).
            max_stake_usd: the optional stake cap (the cash bound; the ΔU shape is unchanged).
            shares_for_routing: the share size the routes are priced at (the route surface).
            licensed_model_superiority: optional predicate(case_key, bin_id) -> bool licensing
                the model to disagree with a deep market q on that bin (waives the coherence
                block for that bin only). Defaults to "nothing licensed".

        Returns:
            A ``FamilyDecision``. Every exit carries a ``receipt_hash``; a no-trade carries
            ``selected=None`` and a ``no_trade_reason``.
        """
        if omega.resolution is not case.resolution and (
            omega.resolution.rounding_rule != case.resolution.rounding_rule
            or omega.resolution.station_id != case.resolution.station_id
        ):
            raise FamilyDecisionError(
                "OMEGA_RESOLUTION_MISMATCH: omega.resolution disagrees with case.resolution "
                f"(omega rule={omega.resolution.rounding_rule!r} station="
                f"{omega.resolution.station_id!r}; case rule="
                f"{case.resolution.rounding_rule!r} station={case.resolution.station_id!r})"
            )

        # --- (1) read fresh models + day0 (the reactor's injected readers) -------
        models = self._fresh_model_reader.read(case)
        obs = self._day0_reader.read(case)

        # --- (2) the ONE predictive distribution ---------------------------------
        predictive = self._predictive_builder.build(case, models, obs)

        decision_id = self._decision_id(case, captured_at_utc)

        # --- THE FIRST GATE: live eligibility (spec lines 884-885) ----------------
        # When the predictive distribution is not live-eligible, the whole decision is a
        # no-trade BEFORE q is ever integrated — no width-less q, no degenerate band, no
        # candidate. The ineligible distribution still carries the full receipt contract.
        if not predictive.live_eligible:
            return self._no_trade_before_q(
                decision_id=decision_id,
                case=case,
                predictive=predictive,
                omega=omega,
                reason=NO_TRADE_PREDICTIVE_NOT_LIVE_ELIGIBLE,
            )

        # --- (3) the joint q and its coherent band -------------------------------
        joint_q = build_joint_q(predictive, omega)
        band = build_joint_q_band(
            predictive, omega, n_draws=self._n_band_draws, alpha=self._band_alpha
        )

        # --- (4) the executable family book + de-frictioned market q -------------
        family_book = self._family_book_builder(
            omega=omega,
            snapshots_by_bin_id=dict(snapshots),
            captured_at_utc=captured_at_utc,
        )
        market_implied = build_market_implied_q(
            family_book, depth_reference_size=self._depth_reference_size
        )

        # --- (5) the family route set (the executable surface per bin/side) ------
        # The injected route_set_builder owns the route surface. Default = the live
        # neg-risk engine (ask-ladder, size-aware). The reactor injects a PROOF-NATIVE
        # direct-route builder so each direct YES/NO route's cost is the proof's own
        # maker/taker execution_price, not the ask-ladder taker cost (a maker buy_no into
        # an empty NO ask keeps its resting-bid cost instead of being discarded).
        route_set = self._route_set_builder(
            family_book,
            shares=shares_for_routing,
            enable_negrisk_routes=self._enable_negrisk_routes,
        )

        # The forecast (modal) bin — the direction-law reference.
        forecast_bin = forecast_bin_id(joint_q)

        # --- (6) enumerate + score every candidate route -------------------------
        enumerated = self._enumerate_candidates(
            joint_q=joint_q,
            band=band,
            omega=omega,
            route_set=route_set,
            matrix=matrix,
            exposure=portfolio,
            sizing_candidates=sizing_candidates,
            max_stake_usd=max_stake_usd,
        )

        # --- the market-coherence report over the candidate bins (spec 891) ------
        candidate_bin_ids = sorted({d.route.bin_id for d in enumerated})
        coherence = assess_market_coherence(
            joint_q=joint_q,
            family_book=family_book,
            candidate_bin_ids=candidate_bin_ids,
            case_key=case.family_id,
            licensed_model_superiority=(
                licensed_model_superiority
                if licensed_model_superiority is not None
                else _license_nothing
            ),
            min_depth=self._min_depth,
            max_spread=self._max_spread,
            depth_reference_size=self._depth_reference_size,
        )

        # Re-stamp each candidate's direction-law + coherence flags (coherence needs the
        # report, which needs the candidate bins — so it is computed after enumeration).
        scored = tuple(
            CandidateDecision(
                route=d.route,
                economics=d.economics,
                direction_law_ok=direction_law_ok(
                    d.route,
                    forecast_bin=forecast_bin,
                    point_q=float(joint_q.q_by_bin_id.get(d.route.bin_id, 0.0)),
                ),
                coherence_allows=coherence_allows(d.route, coherence),
                robust_trade_score=d.robust_trade_score,
            )
            for d in enumerated
        )

        # --- (6b) q_lcb EMPIRICAL RELIABILITY GUARD (FINAL no-shadow flow §6) -----
        # The RAW-honest serving rule: deflate each candidate's served q_lcb to
        # q_safe = min(band_q_lcb, L_g) and ABSTAIN (force a non-positive edge) when the
        # candidate's reliability cell (metric, lead_bucket, bin_position, q_lcb_bucket)
        # is thin (N_g < N_MIN) or its OOF realized frequency does not support the bucket
        # (L_g < bucket_floor − EPS). Applied here, where the decision layer consumes the
        # q_lcb (between scoring and selection). INERT when the OOF reliability artifact is
        # absent (current live state) -> scored is byte-identical (no abstain). Moves NO μ.
        scored = self._apply_qlcb_reliability_guard(
            scored=scored, case=case, joint_q=joint_q, forecast_bin=forecast_bin
        )

        # --- (7) the filter chain (spec lines 896-898) — ORDER IS THE CONTRACT ----
        #   direction_law_ok -> coherence_allows -> (edge_lcb > 0 AND optimal_delta_u > 0)
        # The scalar robust_trade_score is NOT one of the conditions.
        selected_decision, no_trade_reason = self._select(scored)

        candidates_economics = tuple(d.economics for d in scored)
        selected_economics = (
            selected_decision.economics if selected_decision is not None else None
        )

        receipt_hash = self._receipt_hash(
            decision_id=decision_id,
            predictive=predictive,
            joint_q=joint_q,
            band=band,
            family_book=family_book,
            coherence=coherence,
            candidates=candidates_economics,
            selected=selected_economics,
            no_trade_reason=no_trade_reason,
        )

        return FamilyDecision(
            decision_id=decision_id,
            case=case,
            predictive=predictive,
            omega=omega,
            joint_q=joint_q,
            band=band,
            family_book=family_book,
            market_coherence=coherence,
            candidates=candidates_economics,
            selected=selected_economics,
            no_trade_reason=no_trade_reason,
            receipt_hash=receipt_hash,
            candidate_decisions=scored,
            market_implied_q=market_implied,
        )

    # ----------------------------------------------------------- enumeration
    def _enumerate_candidates(
        self,
        *,
        joint_q: JointQ,
        band: JointQBand,
        omega: OutcomeSpace,
        route_set: NegRiskRouteSet,
        matrix: FamilyPayoffMatrix,
        exposure: PortfolioExposureVector,
        sizing_candidates: Mapping[tuple[str, str], NativeSideCandidate],
        max_stake_usd: Optional[Decimal],
    ) -> tuple[CandidateDecision, ...]:
        """Enumerate one candidate per (bin, side) executable route and score its economics.

        For each sibling bin: the direct YES route (side YES), and the DOMINANT NO route
        (``best_no_route`` — ``min(direct_no, synthetic_yes_basket)``, side NO). Each is
        turned into a ``CandidateRoute`` (its payoff vector IS the instrument's Arrow-Debreu
        vector) and scored by ``compute_candidate_economics`` (the VECTOR edge + the
        vector-argmax size). A route with no paired sizing candidate is enumerated with a
        zero-stake economics (it fails the live pass on a non-positive ΔU — never sized off
        a missing curve).
        """
        decisions: list[CandidateDecision] = []
        bin_ids = [b.bin_id for b in omega.bins]

        for bin_id in bin_ids:
            yes_route = route_set.direct_yes.get(bin_id)
            if yes_route is not None:
                d = self._score_route(
                    route_cost=yes_route,
                    side="YES",
                    bin_id=bin_id,
                    joint_q=joint_q,
                    band=band,
                    omega=omega,
                    matrix=matrix,
                    exposure=exposure,
                    sizing_candidates=sizing_candidates,
                    max_stake_usd=max_stake_usd,
                )
                if d is not None:
                    decisions.append(d)

            # The NO route is the DOMINANT route (direct vs synthetic-basket min). Only
            # build it when the family has a direct NO route for the bin.
            if bin_id in route_set.direct_no:
                no_route = route_set.best_no_route(bin_id)
                d = self._score_route(
                    route_cost=no_route,
                    side="NO",
                    bin_id=bin_id,
                    joint_q=joint_q,
                    band=band,
                    omega=omega,
                    matrix=matrix,
                    exposure=exposure,
                    sizing_candidates=sizing_candidates,
                    max_stake_usd=max_stake_usd,
                )
                if d is not None:
                    decisions.append(d)

        return tuple(decisions)

    def _score_route(
        self,
        *,
        route_cost: RouteCost,
        side: str,
        bin_id: str,
        joint_q: JointQ,
        band: JointQBand,
        omega: OutcomeSpace,
        matrix: FamilyPayoffMatrix,
        exposure: PortfolioExposureVector,
        sizing_candidates: Mapping[tuple[str, str], NativeSideCandidate],
        max_stake_usd: Optional[Decimal],
    ) -> Optional[CandidateDecision]:
        """Build a CandidateRoute for one route and compute its economics.

        Returns ``None`` only when the route's instrument bin is not in the Omega (a wiring
        fault surfaced by the instrument layer — skip rather than crash the whole family).
        The scalar ``robust_trade_score`` is computed for the receipt; it never selects.
        """
        instrument = _instrument_for(route_cost)
        try:
            route = build_candidate_route(
                candidate_id=f"{side}:{bin_id}:{route_cost.route_id}",
                instrument=instrument,
                route_cost=route_cost,
                omega=omega,
            )
        except (InstrumentError, PayoffVectorError):
            # An instrument whose bin is not a member of the Omega cannot be scored; skip
            # it (it is a stranger route, never a tradeable candidate of THIS family).
            return None

        sizing = sizing_candidates.get((bin_id, side))
        if sizing is None or not sizing.is_tradeable:
            # No executable sizing candidate for this (bin, side): the economics carry a
            # zero stake and -inf-class ΔU (the optimizer returns 0 stake / non-positive
            # ΔU for a non-tradeable candidate), so it CANNOT pass the live edge/ΔU gate.
            # It is still recorded for the receipt (a no-trade candidate is auditable).
            economics = self._zero_economics(route, joint_q, band)
        else:
            economics = compute_candidate_economics(
                route,
                joint_q=joint_q,
                band=band,
                sizing_candidate=sizing,
                matrix=matrix,
                exposure=exposure,
                max_stake_usd=max_stake_usd,
            )

        scalar = scalar_trade_score(joint_q, route)
        # direction_law_ok / coherence_allows are stamped by the caller (coherence needs the
        # full candidate-bin set first); here we record placeholders that the caller overrides.
        return CandidateDecision(
            route=route,
            economics=economics,
            direction_law_ok=False,
            coherence_allows=False,
            robust_trade_score=scalar,
        )

    def _zero_economics(
        self, route: CandidateRoute, joint_q: JointQ, band: JointQBand
    ) -> CandidateEconomics:
        """A no-trade economics for a route with no executable sizing candidate.

        The VECTOR edge (point_ev / edge_lcb) is still computed (it is a property of the q /
        band / route cost, independent of the sizing curve), so the receipt records the real
        edge; but the stake is 0 and the ΔU is non-positive, so the live pass refuses it. The
        scalar q-price is not used here either.
        """
        from src.decision.payoff_vector import (
            edge_lower_bound,
            point_fair_value,
        )

        payoff = np.asarray(route.payoff_vector, dtype=float)
        cost = float(route.route_cost.avg_cost.value)
        q_dot = point_fair_value(joint_q, payoff)
        edge_lcb = edge_lower_bound(band, payoff, cost)
        return CandidateEconomics(
            candidate_id=route.candidate_id,
            point_ev=q_dot - cost,
            edge_lcb=edge_lcb,
            delta_u_at_min=float("-inf"),
            optimal_stake_usd=Decimal("0"),
            optimal_delta_u=0.0,
            q_dot_payoff=q_dot,
            cost=route.route_cost.avg_cost,
            route_id=route.route_cost.route_id,
        )

    # ------------------------------------------------ q_lcb reliability guard
    def _apply_qlcb_reliability_guard(
        self,
        *,
        scored: tuple[CandidateDecision, ...],
        case: ForecastCase,
        joint_q: JointQ,
        forecast_bin: str,
    ) -> tuple[CandidateDecision, ...]:
        """Deflate each candidate's served q_lcb by the empirical OOF reliability guard.

        FINAL no-shadow execution flow §6. The candidate's served q_lcb (the route's robust
        lower bound) is ``q_lcb_route = economics.edge_lcb + cost`` (because
        ``edge_lcb = quantile(samples @ payoff) − cost``). The guard resolves the cell
        ``(metric, lead_bucket, side, bin_position, q_lcb_bucket)`` — ``side`` is the actual
        executable YES/NO claim, and ``bin_position`` is "modal" for the forecast (modal) bin,
        "nonmodal" otherwise (a stable, NON-per-city position label) — and returns
        ``q_safe = min(q_lcb_route, L_g)`` plus a trade/abstain verdict.

        On ABSTAIN (thin cell or below floor) the candidate's economics are re-stamped with a
        non-positive ``edge_lcb`` (and ``optimal_delta_u``) so the existing ``edge_lcb > 0``
        filter rejects it — the candidate publishes its point prob but never trades. On a
        licensed deflation (``q_safe < q_lcb_route``) the edge is lowered to
        ``q_safe − cost`` so the after-cost edge the selector reads is the GUARDED edge. INERT
        (artifact absent) -> every verdict is pass-through and ``scored`` is returned unchanged.

        Read-only on μ. FAIL-SOFT: any guard error leaves the candidate untouched (the
        conservative edge_lcb>0 gate is still the trade authority).
        """
        lead_days = float(getattr(case, "lead_hours", 0.0) or 0.0) / 24.0
        metric = str(getattr(case, "metric", "")).lower()
        out: list[CandidateDecision] = []
        for d in scored:
            try:
                econ = d.economics
                cost = float(econ.cost.value)
                edge_lcb = float(econ.edge_lcb)
                # The route's served q_lcb lower bound (payoff-space, pre-deflation).
                q_lcb_route = edge_lcb + cost
                bin_position = "modal" if d.route.bin_id == forecast_bin else "nonmodal"
                verdict = _apply_qlcb_guard(
                    band_q_lcb=q_lcb_route,
                    metric=metric,
                    lead_days=lead_days,
                    side=d.route.side,
                    bin_position=bin_position,
                )
                guard_fields = {
                    "q_lcb_guard_basis": verdict.basis,
                    "q_lcb_guard_abstained": bool(verdict.abstained),
                    "q_lcb_guard_cell_key": verdict.cell_key,
                }
                if verdict.basis == "INERT" and not verdict.abstained:
                    out.append(replace(d, **guard_fields))  # no artifact; pass-through
                    continue
                if verdict.abstained:
                    # ABSTAIN: deflate the edge to a non-positive value so edge_lcb>0 rejects
                    # it. q_safe = 0 -> guarded edge = 0 − cost = −cost (< 0 for any real cost).
                    new_edge = verdict.q_safe - cost
                    new_econ = replace(
                        econ,
                        edge_lcb=float(new_edge),
                        optimal_delta_u=min(float(econ.optimal_delta_u), 0.0),
                    )
                    out.append(replace(d, economics=new_econ, **guard_fields))
                    continue
                # Licensed deflation: lower the edge to q_safe − cost (>= the abstain edge,
                # <= the original). q_safe = min(q_lcb_route, L_g), so when L_g >= q_lcb_route
                # this is the original edge (no-op); when L_g < q_lcb_route it tightens it.
                guarded_edge = verdict.q_safe - cost
                if guarded_edge < edge_lcb:
                    new_econ = replace(econ, edge_lcb=float(guarded_edge))
                    out.append(replace(d, economics=new_econ, **guard_fields))
                else:
                    out.append(replace(d, **guard_fields))
            except Exception:  # noqa: BLE001 — fail-soft: leave the candidate untouched.
                out.append(d)
        return tuple(out)

    # --------------------------------------------------------------- selection
    def _select(
        self, scored: Sequence[CandidateDecision]
    ) -> tuple[Optional[CandidateDecision], Optional[str]]:
        """Apply the filter chain and select maximum robust utility density.

        The filter ORDER is the contract:

            1. direction_law_ok          (the candidate is on the legal side of the forecast)
            2. coherence_allows          (the market-coherence report does NOT block the bin)
            3. edge_lcb > 0 AND optimal_delta_u > 0   (the vector edge + the vector ΔU)
               (the executable-route + direction-law + coherence preconditions of the live
                pass are already true here, so live_candidate_passes is a re-proof)

        The survivor with the MAX ``optimal_delta_u / optimal_stake_usd`` is selected. The
        scalar ``robust_trade_score`` is NEVER consulted. When the survivor set is empty, the
        returned ``no_trade_reason`` names the FIRST filter that emptied it (so the no-trade
        is auditable to its cause).
        """
        if not scored:
            return None, NO_TRADE_NO_EXECUTABLE_ROUTE

        # Stage the chain so the no-trade reason can name where it emptied.
        after_executable = [
            d for d in scored if d.route.route_cost.executable
        ]
        if not after_executable:
            return None, NO_TRADE_NO_EXECUTABLE_ROUTE

        # NO-on-modal is direction-law illegal by geometry. It may only be admitted when the
        # side-aware OOF reliability guard actively licensed that exact NO-complement claim.
        # A positive edge from an INERT/missing-cell guard is not enough evidence for a live
        # direction override; otherwise a missing NO cell can pass while a center YES cell is
        # abstained, recreating the all-NO admission bias.
        def _direction_admitted(d):
            return d.direction_law_ok or (
                d.route.side == "NO"
                and d.economics.edge_lcb > 0.0
                and d.q_lcb_guard_basis == "OOF_WILSON_95"
                and not d.q_lcb_guard_abstained
            )

        after_direction = [d for d in after_executable if _direction_admitted(d)]
        if not after_direction:
            return None, NO_TRADE_NO_DIRECTION_LAW

        after_coherence = [d for d in after_direction if d.coherence_allows]
        if not after_coherence:
            return None, NO_TRADE_MARKET_INCOHERENT

        edge_survivors = [
            d
            for d in after_coherence
            if d.economics.edge_lcb > 0.0 and d.economics.optimal_delta_u > 0.0
        ]
        # The live pass is a final structural re-proof (executable route, direction-law
        # proof present, coherence accepted, the vector edge/ΔU). The direction-law proof
        # passed here MUST be the SAME `_direction_admitted` predicate used by
        # after_direction above — passing the bare `d.direction_law_ok` re-zeroes the
        # edge-gated NO-on-modal harvest (live_candidate_passes hard-requires
        # direction_law_proof_present=True). Every other vector gate still applies.
        survivors = [
            d
            for d in edge_survivors
            if live_candidate_passes(
                d.economics,
                d.route,
                direction_law_proof_present=_direction_admitted(d),
                market_coherence_accepted=d.coherence_allows,
            )
        ]
        if not survivors:
            # READ-ONLY per-gate attribution diag (2026-06-15). The spine no-trade diag
            # logs positive-edge candidates while the reason is NO_POSITIVE_EDGE; that
            # diag reads only flattened economics and cannot say WHICH gate dropped the
            # harvest. This names, per top candidate, every gate flag + the per-stage
            # survivor counts, so the exact suppressor is auditable. Fail-safe; the diag
            # never raises into the decision path and changes no behavior.
            try:
                import logging as _gate_diag

                _tops = sorted(
                    scored,
                    key=lambda d: (
                        d.economics.edge_lcb
                        if d.economics.edge_lcb is not None
                        else float("-inf")
                    ),
                    reverse=True,
                )[:4]
                _rows = "; ".join(
                    f"{d.route.side}:{d.route.bin_id} dlok={int(d.direction_law_ok)} "
                    f"adm={int(_direction_admitted(d))} coh={int(d.coherence_allows)} "
                    f"exec={int(d.route.route_cost.executable)} "
                    f"e={d.economics.edge_lcb:+.4f} dU={d.economics.optimal_delta_u:+.5f} "
                    f"dUmin={d.economics.delta_u_at_min:+.5f}"
                    for d in _tops
                )
                _gate_diag.getLogger("zeus.spine_edge").info(
                    "SELECT_GATE_DIAG n=%d exec=%d dir=%d coh=%d edge=%d live=%d tops=[%s]",
                    len(scored),
                    len(after_executable),
                    len(after_direction),
                    len(after_coherence),
                    len(edge_survivors),
                    len(survivors),
                    _rows,
                )
            except Exception:
                pass
            return None, NO_TRADE_NO_POSITIVE_EDGE

        def _utility_density(d: CandidateDecision) -> float:
            try:
                stake = float(d.economics.optimal_stake_usd)
            except Exception:  # noqa: BLE001
                stake = 0.0
            stake = max(stake, 1e-9)
            return float(d.economics.optimal_delta_u) / stake

        # SELECT: choose the best robust utility density over the survivors. Total ΔU remains
        # a secondary ordering signal, but a high-capital low-density NO cannot dominate a
        # lower-capital higher-density YES just because it ties up more dollars. The scalar
        # trade score is NOT a key.
        selected = max(
            survivors,
            key=lambda d: (
                _utility_density(d),
                d.economics.optimal_delta_u,
                d.economics.edge_lcb,
                -float(d.economics.cost.value),
            ),
        )
        return selected, None

    # ------------------------------------------------------- no-trade-before-q
    def _no_trade_before_q(
        self,
        *,
        decision_id: str,
        case: ForecastCase,
        predictive: PredictiveDistribution,
        omega: OutcomeSpace,
        reason: str,
    ) -> FamilyDecision:
        """A no-trade emitted BEFORE q (the predictive distribution was not live-eligible).

        Returns a complete ``FamilyDecision`` with ``joint_q`` / ``band`` / ``family_book``
        / ``market_coherence`` all ``None`` (none were built — there is no live-eligible
        distribution to integrate), an empty ``candidates`` tuple, ``selected=None``, the
        ``no_trade_reason``, and a ``receipt_hash`` over the (predictive, omega) pair so the
        no-trade is reconstructable.
        """
        receipt_hash = self._receipt_hash(
            decision_id=decision_id,
            predictive=predictive,
            joint_q=None,
            band=None,
            family_book=None,
            coherence=None,
            candidates=(),
            selected=None,
            no_trade_reason=reason,
        )
        return FamilyDecision(
            decision_id=decision_id,
            case=case,
            predictive=predictive,
            omega=omega,
            joint_q=None,
            band=None,
            family_book=None,
            market_coherence=None,
            candidates=(),
            selected=None,
            no_trade_reason=reason,
            receipt_hash=receipt_hash,
            candidate_decisions=(),
            market_implied_q=None,
        )

    # ------------------------------------------------------------- identity
    @staticmethod
    def _decision_id(case: ForecastCase, captured_at_utc) -> str:
        """A stable decision id: ``{family_id}@{captured_at_utc}``."""
        return f"{case.family_id}@{captured_at_utc}"

    @staticmethod
    def _receipt_hash(
        *,
        decision_id: str,
        predictive: PredictiveDistribution,
        joint_q: Optional[JointQ],
        band: Optional[JointQBand],
        family_book: Optional[FamilyBook],
        coherence: Optional[MarketCoherenceReport],
        candidates: tuple[CandidateEconomics, ...],
        selected: Optional[CandidateEconomics],
        no_trade_reason: Optional[str],
    ) -> str:
        """Deterministic hash over the whole decision tuple (the receipt anchor; spec 871).

        Covers the decision id, the predictive distribution identity, the joint q / band /
        family-book / coherence identities (each ``None`` on the no-trade-before-q path), the
        per-candidate economics (route id + edge_lcb + optimal_delta_u + optimal_stake), the
        selected candidate, and the no-trade reason. Stable across process runs so a receipt
        proves the exact decision tuple this engine produced.
        """
        h = hashlib.sha256()
        h.update(decision_id.encode("utf-8"))
        h.update(predictive.identity_hash.encode("utf-8"))
        h.update((joint_q.identity_hash if joint_q is not None else "no-q").encode("utf-8"))
        h.update((band.sample_hash if band is not None else "no-band").encode("utf-8"))
        h.update(
            (family_book.book_hash if family_book is not None else "no-book").encode("utf-8")
        )
        if coherence is not None:
            h.update(f"coh={coherence.status}:{coherence.offending_bins!r}".encode("utf-8"))
        else:
            h.update(b"coh=none")
        for c in candidates:
            h.update(
                (
                    f"|{c.candidate_id}|{c.route_id}|{c.edge_lcb!r}|"
                    f"{c.optimal_delta_u!r}|{c.optimal_stake_usd}"
                ).encode("utf-8")
            )
        h.update(
            (
                f"SELECTED={selected.candidate_id if selected is not None else 'none'}"
            ).encode("utf-8")
        )
        h.update(f"NO_TRADE={no_trade_reason!r}".encode("utf-8"))
        return h.hexdigest()


def _license_nothing(_case_key: str, _bin_id: str) -> bool:
    """Default licensing predicate: nothing is licensed (no bin waives a coherence block)."""
    return False
