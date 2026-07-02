# Created: 2026-06-14
# Last reused or audited: 2026-06-29
# Authority basis: docs/rebuild/consult_build_spec.md
#   ("Create src/decision/family_decision_engine.py" block lines 854-904: the
#   FamilyDecision dataclass 858-871 [decision_id, case, predictive, omega, joint_q,
#   band, family_book, market_coherence, candidates, selected, no_trade_reason,
#   receipt_hash]; the decide() algorithm 876-901 — event_resolution -> outcome_space
#   -> read fresh models + day0 -> predictive_builder.build -> (if not live_eligible:
#   no_trade PREDICTIVE_DISTRIBUTION_NOT_LIVE_ELIGIBLE) -> joint_q -> joint_q_band ->
#   family_book -> market_implied_q -> coherence -> routes -> payoff candidates ->
#   filter [direction_law_ok, coherence_allows, edge_lcb>0 & optimal_delta_u>0] ->
#   selected = max ROI-frontier candidate) and the Stage 8 block lines 1166-1184.
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

    selected   = max(candidates, key=lambda c: c.roi_frontier)
    return FamilyDecision(...)

THE THREE CORRECTED TRANSFORMATIONS THIS ORCHESTRATOR PRESERVES (operator law — make the
bad output mathematically impossible; NO gate/cap/clamp/haircut that catches a bad value
and leaves a broken transform in place):

  1. SELECTION IS ROI-FRONTIER OVER THE SURVIVORS, NEVER A SCALAR TRADE
     SCORE (operator Shanghai correction over spec lines 900-903, 1184). The candidate filter chain is
     ``direction_law_ok -> coherence_allows -> (edge_lcb > 0 AND optimal_delta_u > 0)``,
     and live qkernel selects on an ROI frontier: guarded edge per dollar first, after
     excluding dust candidates with no meaningful lower-bound profit, with robust log-growth
     as the secondary tie-breaker. ``total_delta_u`` remains an explicit research objective,
     but it is not the live default. The scalar
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

SIDE ADMISSION:
``direction_law_ok`` is now a native-side proof: the executable route must be a valid YES/NO
claim, but the rounded forecast center is not a hard bin veto. Direct YES on any bin and direct
NO on any bin may reach the real live gates. Settlement-aware payoff vectors, q_lcb reliability,
market coherence, executable cost, and robust DeltaU decide whether that side/bin has alpha.

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

import math
import hashlib
from dataclasses import dataclass, replace
from decimal import Decimal
from typing import (
    Literal,
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
from src.decision.qlcb_reliability_guard import (
    precision_class_for_city as _qlcb_precision_class_for_city,
)
from src.decision.selection_calibrator import apply_selection_calibrator
from src.probability.joint_q import JointQ, build_joint_q
from src.probability.joint_q_band import JointQBand, build_joint_q_band
from src.probability.outcome_space import OutcomeSpace
from src.strategy.live_inference.live_admission import LIVE_DIRECTION_WIN_RATE_FLOOR
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
NO_TRADE_NO_POSITIVE_UTILITY = "NO_POSITIVE_UTILITY_CANDIDATE"
NO_TRADE_NO_MIN_ORDER_UTILITY = "NO_MIN_ORDER_UTILITY_CANDIDATE"
NO_TRADE_NO_ROI_FRONTIER_CANDIDATE = "NO_ROI_FRONTIER_USEFUL_CANDIDATE"
# q_lcb empirical reliability guard (single-serving-rule flow §6): every candidate's
# served q_lcb was deflated to 0 (abstain) because its reliability cell is thin / below floor.
NO_TRADE_QLCB_RELIABILITY_ABSTAIN = "QLCB_RELIABILITY_GUARD_ABSTAIN"
_OOF_LIVE_RELIABILITY_BASES = frozenset(
    {
        "OOF_WILSON_95",
        "OOF_WILSON_95_POOLED_TAIL",
    }
)
DAY0_REMAINING_DAY_GUARD_BASIS = "DAY0_REMAINING_DAY_Q_LCB"
_ROI_FRONTIER_MIN_PROFIT_LCB_USD = 0.25
_ROI_FRONTIER_MIN_PAYOFF_Q_LCB = 0.02
_ROI_FRONTIER_CHEAP_YES_COST_CEILING = 0.15
_ROI_FRONTIER_CHEAP_YES_MIN_PAYOFF_Q_LCB = 0.07
_ROI_FRONTIER_CHEAP_YES_MAX_EDGE_MARGIN = 0.05
LIVE_ENTRY_MIN_ENTRY_PRICE = 0.10
CENTER_BUY_YES_MIN_ENTRY_PRICE = 0.02


@dataclass(frozen=True)
class EntryPriceFloorDecision:
    live_min_entry_price: float
    effective_min_entry_price: float
    qkernel_low_price_floor_authorized: bool


def roi_frontier_min_payoff_q_lcb(*, side: str | None, cost: float) -> float:
    """Conservative live floor for cheap YES routes.

    Cheap YES routes are valid when the forecast genuinely prices a tail too low,
    but raw edge/cost over-rewards lottery-like tails whose lower-bound probability
    barely clears the book. Require an absolute lower q floor and a continuous
    edge-over-cost margin that decays with price, instead of a discontinuous sub-5c
    special case. Normal-priced and NO routes stay on the global frontier floor
    plus the existing positive-edge/growth-density gates.
    """

    floor = float(_ROI_FRONTIER_MIN_PAYOFF_Q_LCB)
    side_text = str(side or "").strip().upper()
    if (
        side_text == "YES"
        and np.isfinite(cost)
        and 0.0 < float(cost) < _ROI_FRONTIER_CHEAP_YES_COST_CEILING
    ):
        cost_f = float(cost)
        edge_margin = float(_ROI_FRONTIER_CHEAP_YES_MAX_EDGE_MARGIN) * (
            1.0 - (cost_f / float(_ROI_FRONTIER_CHEAP_YES_COST_CEILING))
        )
        floor = max(
            floor,
            float(_ROI_FRONTIER_CHEAP_YES_MIN_PAYOFF_Q_LCB),
            cost_f + max(0.0, edge_margin),
        )
    return floor


def roi_frontier_min_profit_lcb_usd() -> float:
    return float(_ROI_FRONTIER_MIN_PROFIT_LCB_USD)


def roi_frontier_growth_density(
    *,
    cost: float,
    edge_lcb: float,
    payoff_q_lcb: float,
) -> float:
    roi = edge_lcb / cost if cost > 0.0 else float("-inf")
    if not (
        np.isfinite(roi)
        and np.isfinite(cost)
        and np.isfinite(payoff_q_lcb)
        and 0.0 < cost < 1.0
        and payoff_q_lcb > cost
    ):
        return float("-inf")
    return float(roi * ((payoff_q_lcb - cost) / (1.0 - cost)))


def roi_frontier_profit_lcb_usd(
    *,
    stake: float,
    cost: float,
    edge_lcb: float,
) -> float:
    roi = edge_lcb / cost if cost > 0.0 else float("-inf")
    if not (np.isfinite(stake) and stake > 0.0 and np.isfinite(roi)):
        return float("-inf")
    return float(stake * roi)


def roi_frontier_useful_values(
    *,
    side: str | None,
    cost: float,
    payoff_q_lcb: float,
    edge_lcb: float,
    stake: float,
    delta_u_at_min: float,
) -> bool:
    min_payoff_q_lcb = roi_frontier_min_payoff_q_lcb(side=side, cost=cost)
    return bool(
        np.isfinite(stake)
        and stake > 0.0
        and np.isfinite(delta_u_at_min)
        and delta_u_at_min > 0.0
        and np.isfinite(payoff_q_lcb)
        and payoff_q_lcb >= min_payoff_q_lcb
        and roi_frontier_profit_lcb_usd(
            stake=stake,
            cost=cost,
            edge_lcb=edge_lcb,
        )
        >= _ROI_FRONTIER_MIN_PROFIT_LCB_USD
        and np.isfinite(
            roi_frontier_growth_density(
                cost=cost,
                edge_lcb=edge_lcb,
                payoff_q_lcb=payoff_q_lcb,
            )
        )
    )


def native_curve_side_for_direction(direction: object) -> str | None:
    normalized = str(direction or "").strip().lower()
    if normalized.endswith("_yes"):
        return "YES"
    if normalized.endswith("_no"):
        return "NO"
    return None


def live_entry_min_price_floor(*, strategy_key: object, direction: object) -> float:
    if (
        str(strategy_key or "").strip() == "center_buy"
        and str(direction or "").strip().lower() == "buy_yes"
    ):
        return float(CENTER_BUY_YES_MIN_ENTRY_PRICE)
    return float(LIVE_ENTRY_MIN_ENTRY_PRICE)


def _finite_submit_float(value: object) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if not np.isfinite(parsed):
        return None
    return float(parsed)


def entry_price_floor_decision(
    *,
    strategy_key: object,
    direction: object,
    declared_min_entry_price: object,
    selection_authority_applied: object,
    economics: Mapping[str, object] | None,
    q_live: object,
    q_lcb: object,
    limit_price: object,
) -> EntryPriceFloorDecision:
    candidate_live_floor = live_entry_min_price_floor(
        strategy_key=strategy_key,
        direction=direction,
    )
    qkernel_floor_candidate = bool(
        str(strategy_key or "").strip() == "center_buy"
        and str(direction or "").strip().lower() == "buy_yes"
        and str(selection_authority_applied or "").strip() == "qkernel_spine"
        and isinstance(economics, Mapping)
        and str(economics.get("source") or "").strip() == "qkernel_spine"
    )
    live_floor = (
        candidate_live_floor if qkernel_floor_candidate else float(LIVE_ENTRY_MIN_ENTRY_PRICE)
    )
    declared_floor = _finite_submit_float(declared_min_entry_price)
    if declared_floor is None:
        declared_floor = 0.0
    effective_min_entry_price = (
        live_floor if qkernel_floor_candidate else max(declared_floor, live_floor)
    )
    return EntryPriceFloorDecision(
        live_min_entry_price=live_floor,
        effective_min_entry_price=effective_min_entry_price,
        qkernel_low_price_floor_authorized=qkernel_floor_candidate,
    )


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
    * ``selection_guard_basis`` / ``selection_guard_abstained`` — the selection-aware
      settlement calibrator verdict applied before live selection. This guard keys on
      the raw side probability that admission selected on, then only lowers the payoff
      qLCB consumed by edge/DeltaU/ROI.
    """

    route: CandidateRoute
    economics: CandidateEconomics
    direction_law_ok: bool
    coherence_allows: bool
    robust_trade_score: float
    q_lcb_guard_basis: str = ""
    q_lcb_guard_abstained: bool = False
    q_lcb_guard_cell_key: str = ""
    selection_guard_basis: str = ""
    selection_guard_abstained: bool = False
    selection_guard_cell_key: str = ""
    selection_guard_n: int = 0
    selection_guard_q_safe: float | None = None


@dataclass(frozen=True)
class PortfolioCandidateDecision:
    """Non-executable portfolio comparator over already-scored direct routes.

    This is not an execution route. It records whether a multi-leg family portfolio
    would dominate the selected single direct route. The live executor can only submit
    the selected direct route; this evidence is receipt telemetry until an atomic
    multi-leg executor exists.
    """

    portfolio_type: str
    reference_bin_id: str
    leg_candidate_ids: tuple[str, ...]
    leg_route_ids: tuple[str, ...]
    payoff_vector_hash: str
    point_ev: float
    edge_lcb: float
    q_dot_payoff: float
    cost_sum: float
    edge_lcb_density: float
    point_ev_density: float
    selected_candidate_id: str
    selected_edge_lcb_density: float
    selected_point_ev_density: float
    dominates_selected: bool


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
    * ``selected`` — the ``CandidateEconomics`` of the chosen trade (live default:
      ROI-frontier over the survivors), or ``None`` for a no-trade.
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
    portfolio_comparisons: tuple[PortfolioCandidateDecision, ...] = ()


# ===========================================================================
# Forecast-bin helpers — receipt/provenance context, not live side admission.
# ===========================================================================

def forecast_bin_id(joint_q: JointQ) -> str:
    """The modal max-mass bin of the joint q.

    This is useful for receipts and diagnostics. It is not a live admission rule; direct
    native side/bin selection is governed by vector economics.
    """
    q = np.asarray(joint_q.q, dtype=float)
    if q.shape[0] == 0:
        raise FamilyDecisionError("EMPTY_OMEGA: joint q has no bins; no forecast bin")
    i = int(np.argmax(q))
    return joint_q.omega.bins[i].bin_id


def forecast_settlement_bin_id(
    predictive: PredictiveDistribution, omega: OutcomeSpace
) -> str:
    """The bin where the served center settles under this family's rounding rule.

    This settlement bin is receipt/provenance context, not a largest-mass statement and
    not a live side/bin admission gate.
    Open shoulders can carry more probability than a one-degree center bin simply
    because they aggregate many integer outcomes. The settlement-center bin is the bin containing
    the settlement value of ``predictive.mu_native`` under ``omega.resolution``.
    """
    mu = float(predictive.mu_native)
    if not math.isfinite(mu):
        raise FamilyDecisionError(
            f"FORECAST_CENTER_NONFINITE: mu_native={predictive.mu_native!r}"
        )
    rule = str(omega.resolution.rounding_rule)
    if rule == "wmo_half_up":
        settled = math.floor(mu + 0.5)
    elif rule in ("floor", "oracle_truncate"):
        settled = math.floor(mu)
    elif rule == "ceil":
        settled = math.ceil(mu)
    else:  # pragma: no cover - EventResolution validates the closed rule set
        raise FamilyDecisionError(f"UNKNOWN_ROUNDING_RULE: {rule!r}")
    settled_value = float(settled)
    for b in omega.bins:
        lower_ok = b.lower_native is None or settled_value >= float(b.lower_native)
        upper_ok = b.upper_native is None or settled_value <= float(b.upper_native)
        if lower_ok and upper_ok:
            return b.bin_id
    raise FamilyDecisionError(
        f"FORECAST_SETTLEMENT_BIN_NOT_FOUND: mu={mu!r} settled={settled_value!r}"
    )


def direction_law_ok(route: CandidateRoute, *, forecast_bin: str) -> bool:
    """Whether ``route`` has a live-tradable native side.

    ``forecast_bin`` is retained in the signature because callers already compute and
    stamp it for receipt reconstruction, but it is not an admission gate. A weather
    binary is an Arrow-Debreu payoff; the mathematically relevant live question is
    whether the executable payoff vector has positive robust edge and positive robust
    utility after cost, coherence, q_lcb reliability, and exposure. The rounded
    predictive center is useful telemetry, not a proof that only ``YES_forecast`` or
    ``NO_nonforecast`` can carry alpha.

    This avoids the Shanghai failure mode: a cheap YES on a non-center bin, or a NO on
    the rounded-center bin, must not be rejected merely because ``mu_native`` rounds
    elsewhere. If its vector economics are wrong, the q/payoff/edge/DeltaU gates reject
    it. If they are right, the family selector may choose it.
    """
    _ = forecast_bin
    return route.side in {"YES", "NO"}


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
        selection_objective: Literal[
            "roi_frontier", "utility_density", "total_delta_u"
        ] = "roi_frontier",
    ) -> None:
        if selection_objective not in {"roi_frontier", "utility_density", "total_delta_u"}:
            raise ValueError(f"unknown selection_objective: {selection_objective!r}")
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
        self._selection_objective = selection_objective

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
        served_joint_q: JointQ | None = None,
        served_band: JointQBand | None = None,
        served_payoff_q_lcb_by_side: Mapping[tuple[str, str], float] | None = None,
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
        if served_joint_q is not None or served_band is not None:
            if served_joint_q is None or served_band is None:
                raise FamilyDecisionError("SERVED_BELIEF_INCOMPLETE")
            if served_joint_q.omega.topology_hash != omega.topology_hash:
                raise FamilyDecisionError(
                    "SERVED_JOINT_Q_OMEGA_MISMATCH: served joint q does not match Omega"
                )
            if served_band.joint_q.identity_hash != served_joint_q.identity_hash:
                raise FamilyDecisionError(
                    "SERVED_BAND_JOINT_Q_MISMATCH: served band does not bracket served joint q"
                )
            served_joint_q.assert_valid()
            served_band.assert_valid()
            joint_q = served_joint_q
            band = served_band
        else:
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

        # The forecast/modal bin must be derived from the same q surface the decision
        # is about to score.  When the reactor injects ``served_joint_q`` the predictive
        # center is still receipt context, but modal/nonmodal empirical guard cells and
        # center-YES dominance must follow the served posterior, not a second center.
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
            served_payoff_q_lcb_by_side=served_payoff_q_lcb_by_side,
        )

        # Stamp the native-side direction proof first. The q_lcb reliability guard is
        # empirical model-superiority evidence; it must run before market coherence so a
        # guarded, positive-edge candidate can carry the license promised by the
        # coherence contract.
        pre_coherence_scored = tuple(
            CandidateDecision(
                route=d.route,
                economics=d.economics,
                direction_law_ok=direction_law_ok(
                    d.route,
                    forecast_bin=forecast_bin,
                ),
                coherence_allows=True,
                robust_trade_score=d.robust_trade_score,
            )
            for d in enumerated
        )

        if bool(getattr(predictive.day0, "active", False)):
            # Day0 is the same qkernel family decision with a stronger information
            # state: an observed running extreme has already conditioned the posterior.
            # The conservative lower bound consumed by edge/DeltaU is therefore the
            # served Day0 observed-boundary q_lcb, not the offline forecast-selection
            # OOF cells fitted before that observation existed. Applying those forecast
            # OOF guards here zeroes near-deterministic Day0 modal bins and permanently
            # starves the Day0 capital-flow lane.
            guarded = self._apply_day0_observed_boundary_guard(
                scored=pre_coherence_scored
            )
        else:
            # --- (6b) q_lcb EMPIRICAL RELIABILITY GUARD (single-serving-rule flow §6) -----
            # The RAW-honest serving rule: deflate each candidate's served q_lcb to
            # q_safe = min(band_q_lcb, L_g) and ABSTAIN (force a non-positive edge) when the
            # candidate's reliability cell (metric, lead_bucket, bin_position, q_lcb_bucket)
            # is thin (N_g < N_MIN) or its OOF realized frequency does not support the bucket
            # (L_g < bucket_floor − EPS). Applied here, where the decision layer consumes the
            # q_lcb (between scoring and selection). INERT when the OOF reliability artifact is
            # absent -> scored is byte-identical (no abstain). Moves no μ.
            guarded = self._apply_qlcb_reliability_guard(
                scored=pre_coherence_scored,
                case=case,
                joint_q=joint_q,
                band=band,
                forecast_bin=forecast_bin,
                matrix=matrix,
                exposure=portfolio,
                sizing_candidates=sizing_candidates,
                max_stake_usd=max_stake_usd,
            )

            # --- (6c) selection-aware settlement guard --------------------------------
            # The OOF q_lcb guard is price-blind; the selection calibrator is keyed on the
            # raw side probability that admission selected on, so it catches the adverse
            # selection that made high-confidence NO entries lose after settlement. It runs
            # before coherence/selection so live ranking sees guarded edge, stake, and DeltaU.
            guarded = self._apply_selection_calibrator_guard(
                scored=guarded,
                case=case,
                joint_q=joint_q,
                band=band,
                forecast_bin=forecast_bin,
                matrix=matrix,
                exposure=portfolio,
                sizing_candidates=sizing_candidates,
                max_stake_usd=max_stake_usd,
            )

        # --- the market-coherence report over the candidate bins (spec 891) ------
        # A large model/market logit gap is not automatically a live-money incident.
        # Side-aware OOF reliability evidence can support market-coherence acceptance for
        # a candidate whose native side is live-tradable. It does not move mu or fabricate
        # edge; q/payoff economics still decide selection.
        empirical_reliability_bins = frozenset(
            d.route.bin_id
            for d in guarded
            if d.direction_law_ok
            and self._has_side_aware_oof_reliability_evidence(d)
            and self._selection_edge_lcb(d) > 0.0
            and d.economics.optimal_delta_u > 0.0
        )

        def _empirical_or_injected_reliability(case_key: str, bin_id: str) -> bool:
            if licensed_model_superiority is not None and licensed_model_superiority(
                case_key, bin_id
            ):
                return True
            return bin_id in empirical_reliability_bins

        candidate_bin_ids = sorted({d.route.bin_id for d in guarded})
        coherence = assess_market_coherence(
            joint_q=joint_q,
            family_book=family_book,
            candidate_bin_ids=candidate_bin_ids,
            case_key=case.family_id,
            licensed_model_superiority=_empirical_or_injected_reliability,
            min_depth=self._min_depth,
            max_spread=self._max_spread,
            depth_reference_size=self._depth_reference_size,
        )

        # Re-stamp each candidate's coherence flag from the report. Direction and q_lcb
        # guard fields are already authoritative on ``guarded``.
        scored = tuple(
            replace(d, coherence_allows=coherence_allows(d.route, coherence))
            for d in guarded
        )

        # --- (7) the filter chain (spec lines 896-898) — ORDER IS THE CONTRACT ----
        #   native side proof -> coherence_allows -> (edge_lcb > 0 AND optimal_delta_u > 0)
        # The scalar robust_trade_score is NOT one of the conditions.
        selected_decision, no_trade_reason = self._select(scored)
        if selected_decision is not None:
            selected_decision = self._apply_symmetric_center_yes_dominance(
                selected_decision=selected_decision,
                scored=scored,
                forecast_bin=forecast_bin,
                outcome_bin_ids=tuple(b.bin_id for b in omega.bins),
            )
        portfolio_comparisons: tuple[PortfolioCandidateDecision, ...] = ()
        if selected_decision is not None:
            portfolio_comparisons = self._portfolio_comparisons(
                selected_decision=selected_decision,
                scored=scored,
                joint_q=joint_q,
                band=band,
                forecast_bin=forecast_bin,
            )

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
            portfolio_comparisons=portfolio_comparisons,
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
            portfolio_comparisons=portfolio_comparisons,
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
        served_payoff_q_lcb_by_side: Mapping[tuple[str, str], float] | None = None,
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
                    served_payoff_q_lcb_by_side=served_payoff_q_lcb_by_side,
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
                    served_payoff_q_lcb_by_side=served_payoff_q_lcb_by_side,
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
        served_payoff_q_lcb_by_side: Mapping[tuple[str, str], float] | None = None,
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
            served_payoff_q_lcb = None
            if served_payoff_q_lcb_by_side is not None:
                served_payoff_q_lcb = served_payoff_q_lcb_by_side.get((bin_id, side))
            economics = compute_candidate_economics(
                route,
                joint_q=joint_q,
                band=band,
                sizing_candidate=sizing,
                matrix=matrix,
                exposure=exposure,
                max_stake_usd=max_stake_usd,
                guarded_payoff_q_lcb=served_payoff_q_lcb,
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

    # ------------------------------------------------ portfolio comparison
    def _has_side_aware_oof_reliability_evidence(self, d: CandidateDecision) -> bool:
        cell_key = str(d.q_lcb_guard_cell_key or "").strip()
        side = str(d.route.side or "").strip().upper()
        return (
            side in {"YES", "NO"}
            and d.q_lcb_guard_basis in _OOF_LIVE_RELIABILITY_BASES
            and not d.q_lcb_guard_abstained
            and bool(cell_key)
            and f"|{side}|" in cell_key
        )

    def _apply_day0_observed_boundary_guard(
        self,
        *,
        scored: tuple[CandidateDecision, ...],
    ) -> tuple[CandidateDecision, ...]:
        """Stamp Day0 remaining-day lower-bound provenance without OOF deflation.

        Day0's probability surface is already conditioned on the observed running
        extreme and the remaining-day member envelope.  This is not an empirical
        OOF selection cell; recording it as a one-sample selection guard made the
        live receipt look more statistically supported than it was.  The q_lcb
        remains the served remaining-day payoff lower bound, while the selection
        guard provenance records that no offline selection calibrator was applied.
        """

        guarded: list[CandidateDecision] = []
        for d in scored:
            try:
                q_safe = (
                    float(d.economics.payoff_q_lcb)
                    if d.economics.payoff_q_lcb is not None
                    else self._selection_edge_lcb(d) + self._selection_cost(d)
                )
            except Exception:  # noqa: BLE001
                q_safe = 0.0
            if not math.isfinite(q_safe):
                q_safe = 0.0
            q_safe = min(max(float(q_safe), 0.0), 1.0)
            guarded.append(
                replace(
                    d,
                    q_lcb_guard_basis=DAY0_REMAINING_DAY_GUARD_BASIS,
                    q_lcb_guard_abstained=False,
                    q_lcb_guard_cell_key="day0_remaining_day_q_lcb",
                    selection_guard_basis=DAY0_REMAINING_DAY_GUARD_BASIS,
                    selection_guard_abstained=False,
                    selection_guard_cell_key="day0_remaining_day_q_lcb",
                    selection_guard_n=0,
                    selection_guard_q_safe=q_safe,
                )
            )
        return tuple(guarded)

    def _direction_admitted(self, d: CandidateDecision) -> bool:
        return d.direction_law_ok is True

    def _utility_density(self, d: CandidateDecision) -> float:
        try:
            stake = float(d.economics.optimal_stake_usd)
        except Exception:  # noqa: BLE001
            stake = 0.0
        stake = max(stake, 1e-9)
        return float(d.economics.optimal_delta_u) / stake

    def _selection_cost(self, d: CandidateDecision) -> float:
        cost_obj = (
            d.economics.chosen_stake_cost
            if d.economics.chosen_stake_cost is not None
            else d.economics.cost
        )
        try:
            cost = float(cost_obj.value)
        except Exception:  # noqa: BLE001
            return float("inf")
        if not (np.isfinite(cost) and cost > 0.0):
            return float("inf")
        return cost

    def _selection_edge_lcb(self, d: CandidateDecision) -> float:
        edge = (
            d.economics.chosen_stake_edge_lcb
            if d.economics.chosen_stake_edge_lcb is not None
            else d.economics.edge_lcb
        )
        try:
            return float(edge)
        except Exception:  # noqa: BLE001
            return float("-inf")

    def _selection_point_ev(self, d: CandidateDecision) -> float:
        point = (
            d.economics.chosen_stake_point_ev
            if d.economics.chosen_stake_point_ev is not None
            else d.economics.point_ev
        )
        try:
            return float(point)
        except Exception:  # noqa: BLE001
            return float("-inf")

    def _edge_roi_lcb(self, d: CandidateDecision) -> float:
        cost = self._selection_cost(d)
        if not (np.isfinite(cost) and cost > 0.0):
            return float("-inf")
        return self._selection_edge_lcb(d) / cost

    def _profit_lcb_usd(self, d: CandidateDecision) -> float:
        try:
            stake = float(d.economics.optimal_stake_usd)
        except Exception:  # noqa: BLE001
            return float("-inf")
        return roi_frontier_profit_lcb_usd(
            stake=stake,
            cost=self._selection_cost(d),
            edge_lcb=self._selection_edge_lcb(d),
        )

    def _roi_frontier_useful(self, d: CandidateDecision) -> bool:
        try:
            stake = float(d.economics.optimal_stake_usd)
            delta_u_at_min = float(d.economics.delta_u_at_min)
        except Exception:  # noqa: BLE001
            stake = 0.0
            delta_u_at_min = 0.0
        q_lcb = self._payoff_q_lcb(d)
        cost = self._selection_cost(d)
        return roi_frontier_useful_values(
            side=getattr(d.route, "side", None),
            cost=cost,
            payoff_q_lcb=q_lcb,
            edge_lcb=self._selection_edge_lcb(d),
            stake=stake,
            delta_u_at_min=delta_u_at_min,
        )

    def _payoff_q_lcb(self, d: CandidateDecision) -> float:
        try:
            cost = self._selection_cost(d)
            payoff_q_lcb = d.economics.payoff_q_lcb
            q_lcb = (
                float(payoff_q_lcb)
                if payoff_q_lcb is not None
                else self._selection_edge_lcb(d) + cost
            )
        except Exception:  # noqa: BLE001
            return float("-inf")
        return q_lcb

    def _robust_kelly_growth_density(self, d: CandidateDecision) -> float:
        """Capital-efficiency objective with confidence, not raw payout odds.

        ``edge_lcb / cost`` alone over-rewards one-cent tails whose lower-bound
        win probability only barely clears price. Multiplying by the binary
        Kelly lower-bound fraction keeps cheap center YES legs dominant when
        their belief is genuinely strong, while refusing to let tiny
        low-confidence tails beat a high-confidence lower-ROI leg.
        """
        return roi_frontier_growth_density(
            cost=self._selection_cost(d),
            edge_lcb=self._selection_edge_lcb(d),
            payoff_q_lcb=self._payoff_q_lcb(d),
        )

    def _roi_frontier_key(self, d: CandidateDecision) -> tuple[float, float, float, float, float, float]:
        return (
            self._robust_kelly_growth_density(d),
            self._edge_roi_lcb(d),
            self._profit_lcb_usd(d),
            self._utility_density(d),
            float(d.economics.optimal_delta_u),
            -self._selection_cost(d),
        )

    def _roi_frontier_candidates(
        self, survivors: Sequence[CandidateDecision]
    ) -> list[CandidateDecision]:
        useful = [d for d in survivors if self._roi_frontier_useful(d)]
        if not useful:
            return []
        frontier: list[CandidateDecision] = []
        for candidate in useful:
            dominated = False
            c_growth_density = self._robust_kelly_growth_density(candidate)
            c_roi = self._edge_roi_lcb(candidate)
            c_profit = self._profit_lcb_usd(candidate)
            c_du = float(candidate.economics.optimal_delta_u)
            for other in useful:
                if other is candidate:
                    continue
                o_growth_density = self._robust_kelly_growth_density(other)
                o_roi = self._edge_roi_lcb(other)
                o_profit = self._profit_lcb_usd(other)
                o_du = float(other.economics.optimal_delta_u)
                if (
                    o_growth_density >= c_growth_density
                    and o_roi >= c_roi
                    and o_profit >= c_profit
                    and o_du >= c_du
                    and (
                        o_growth_density > c_growth_density
                        or o_roi > c_roi
                        or o_profit > c_profit
                        or o_du > c_du
                    )
                ):
                    dominated = True
                    break
            if not dominated:
                frontier.append(candidate)
        return frontier

    def _live_selectable_candidate(self, d: CandidateDecision) -> bool:
        return (
            d.route.route_cost.executable
            and self._direction_admitted(d)
            and d.coherence_allows
            and self._selection_edge_lcb(d) > 0.0
            and d.economics.optimal_delta_u > 0.0
            and live_candidate_passes(
                d.economics,
                d.route,
                direction_law_proof_present=self._direction_admitted(d),
                market_coherence_accepted=d.coherence_allows,
            )
        )

    def _apply_symmetric_center_yes_dominance(
        self,
        *,
        selected_decision: CandidateDecision,
        scored: Sequence[CandidateDecision],
        forecast_bin: str,
        outcome_bin_ids: Sequence[str] | None = None,
    ) -> CandidateDecision:
        """Replace an inferior selected NO with a strictly superior center YES.

        The older portfolio comparator protected a selected center YES against a
        superior adjacent-NO portfolio but had no mirror invariant for selected
        NO routes. Shanghai-style families then could express the same settlement
        exposure with higher capital cost and lower return density. This guard is
        intentionally strict: YES must already be live-selectable and must
        dominate the selected NO on guarded utility density plus both edge and
        point EV per dollar. Otherwise the selected NO remains authoritative.
        """

        if selected_decision.route.side != "NO":
            return selected_decision
        center_yes = next(
            (
                d
                for d in scored
                if d.route.side == "YES"
                and d.route.bin_id == forecast_bin
                and self._live_selectable_candidate(d)
            ),
            None,
        )
        if center_yes is None:
            return selected_decision
        selected_cost = self._selection_cost(selected_decision)
        center_cost = self._selection_cost(center_yes)
        if not (
            np.isfinite(selected_cost)
            and selected_cost > 0.0
            and np.isfinite(center_cost)
            and center_cost > 0.0
        ):
            return selected_decision
        selected_edge_density = self._selection_edge_lcb(selected_decision) / selected_cost
        center_edge_density = self._selection_edge_lcb(center_yes) / center_cost
        selected_point_density = self._selection_point_ev(selected_decision) / selected_cost
        center_point_density = self._selection_point_ev(center_yes) / center_cost
        if (
            self._utility_density(center_yes) > self._utility_density(selected_decision)
            and center_edge_density > selected_edge_density
            and center_point_density > selected_point_density
        ):
            return center_yes
        adjacent_pair = self._adjacent_no_pair_for_center(
            scored=scored,
            forecast_bin=forecast_bin,
            selected_decision=selected_decision,
            outcome_bin_ids=outcome_bin_ids,
        )
        if adjacent_pair is None:
            return selected_decision
        left, right = adjacent_pair
        pair_payoff = np.asarray(left.route.payoff_vector, dtype=float) + np.asarray(
            right.route.payoff_vector, dtype=float
        )
        center_payoff = np.asarray(center_yes.route.payoff_vector, dtype=float)
        # Shanghai-form equivalence/dominance: buying adjacent NOs can embed a
        # guaranteed floor plus the center-bin upside, sometimes with extra tail
        # payoff. The floor is not alpha; it is extra locked capital. Compare
        # the outcome-dependent upside after removing that guaranteed
        # component, and canonicalize to center YES only when the broader NO
        # expression does not earn its extra capital via better densities.
        pair_upside = pair_payoff - float(np.min(pair_payoff))
        if not np.all(pair_upside + 1e-9 >= center_payoff):
            return selected_decision
        pair_cost = self._selection_cost(left) + self._selection_cost(right)
        if not (np.isfinite(pair_cost) and pair_cost > 0.0):
            return selected_decision
        try:
            # Use the already-computed leg economics for a conservative density
            # proxy. This is enough for canonicalization: center YES must be no
            # worse than the NO expression on both lower-bound and point edge
            # density, while tying up strictly less capital.
            pair_edge = self._selection_edge_lcb(left) + self._selection_edge_lcb(right)
            pair_point = self._selection_point_ev(left) + self._selection_point_ev(right)
        except Exception:  # noqa: BLE001
            return selected_decision
        eps = 1e-9
        if (
            center_cost + eps < pair_cost
            and center_edge_density + eps >= pair_edge / pair_cost
            and center_point_density + eps >= pair_point / pair_cost
        ):
            return center_yes
        return selected_decision

    def _adjacent_no_pair_for_center(
        self,
        *,
        scored: Sequence[CandidateDecision],
        forecast_bin: str,
        selected_decision: CandidateDecision,
        outcome_bin_ids: Sequence[str] | None = None,
    ) -> tuple[CandidateDecision, CandidateDecision] | None:
        if outcome_bin_ids is None:
            bin_ids = [d.route.bin_id for d in scored]
            ordered_unique = list(dict.fromkeys(bin_ids))
        else:
            ordered_unique = list(dict.fromkeys(str(b) for b in outcome_bin_ids if str(b)))
        try:
            idx = ordered_unique.index(forecast_bin)
        except ValueError:
            return None
        if idx <= 0 or idx >= len(ordered_unique) - 1:
            return None
        left_id = ordered_unique[idx - 1]
        right_id = ordered_unique[idx + 1]
        if selected_decision.route.bin_id not in {left_id, right_id}:
            return None
        by_key = {(d.route.side, d.route.bin_id): d for d in scored}
        left = by_key.get(("NO", left_id))
        right = by_key.get(("NO", right_id))
        if left is None or right is None:
            return None
        for leg in (left, right):
            if (
                not leg.route.route_cost.executable
                or not leg.coherence_allows
                or self._selection_edge_lcb(leg) <= 0.0
                or leg.economics.optimal_delta_u <= 0.0
            ):
                return None
        return left, right

    def _portfolio_comparisons(
        self,
        *,
        selected_decision: CandidateDecision,
        scored: Sequence[CandidateDecision],
        joint_q: JointQ,
        band: JointQBand,
        forecast_bin: str,
    ) -> tuple[PortfolioCandidateDecision, ...]:
        """Compare non-executable family portfolios against the selected direct leg.

        The current live executor can submit one native leg. A Shanghai-style family can
        still present a capital-efficiency question that lives above a single leg: the
        two adjacent NO legs around the modal bin can approximate a center YES but with
        very different cost/payoff geometry. Until portfolio execution has parent/child
        command semantics, this comparator produces evidence only; it must not veto the
        selected executable route or create a synthetic route.
        """
        if selected_decision.route.side != "YES" or selected_decision.route.bin_id != forecast_bin:
            return ()

        bin_ids = [b.bin_id for b in joint_q.omega.bins]
        try:
            idx = bin_ids.index(forecast_bin)
        except ValueError:
            return ()
        if idx <= 0 or idx >= len(bin_ids) - 1:
            return ()
        left_id = bin_ids[idx - 1]
        right_id = bin_ids[idx + 1]
        by_key = {(d.route.side, d.route.bin_id): d for d in scored}
        legs = (by_key.get(("NO", left_id)), by_key.get(("NO", right_id)))
        if any(leg is None for leg in legs):
            return ()
        left, right = legs  # type: ignore[misc]
        if (
            not left.route.route_cost.executable
            or not right.route.route_cost.executable
            or not left.coherence_allows
            or not right.coherence_allows
            or self._selection_edge_lcb(left) <= 0.0
            or self._selection_edge_lcb(right) <= 0.0
        ):
            return ()

        payoff = np.asarray(left.route.payoff_vector, dtype=float) + np.asarray(
            right.route.payoff_vector, dtype=float
        )
        cost_sum = self._selection_cost(left) + self._selection_cost(right)
        if not (np.isfinite(cost_sum) and cost_sum > 0.0):
            return ()
        q_dot = float(np.asarray(joint_q.q, dtype=float) @ payoff)
        point_ev = q_dot - cost_sum
        edge_lcb = float(np.quantile(np.asarray(band.samples, dtype=float) @ payoff - cost_sum, band.alpha))
        selected_cost = self._selection_cost(selected_decision)
        if not (np.isfinite(selected_cost) and selected_cost > 0.0):
            return ()
        edge_density = edge_lcb / cost_sum
        point_density = point_ev / cost_sum
        selected_edge_density = self._selection_edge_lcb(selected_decision) / selected_cost
        selected_point_density = self._selection_point_ev(selected_decision) / selected_cost
        dominates = (
            edge_lcb > 0.0
            and point_ev > 0.0
            and edge_density > selected_edge_density
            and point_density > selected_point_density
        )
        h = hashlib.sha256()
        h.update(b"ADJACENT_NO_PAIR")
        h.update(forecast_bin.encode("utf-8"))
        for leg in (left, right):
            h.update(leg.economics.candidate_id.encode("utf-8"))
            h.update(leg.economics.route_id.encode("utf-8"))
            h.update(np.ascontiguousarray(np.round(leg.route.payoff_vector, 12)).tobytes())
        return (
            PortfolioCandidateDecision(
                portfolio_type="ADJACENT_NO_PAIR",
                reference_bin_id=forecast_bin,
                leg_candidate_ids=(left.economics.candidate_id, right.economics.candidate_id),
                leg_route_ids=(left.economics.route_id, right.economics.route_id),
                payoff_vector_hash=h.hexdigest(),
                point_ev=point_ev,
                edge_lcb=edge_lcb,
                q_dot_payoff=q_dot,
                cost_sum=cost_sum,
                edge_lcb_density=edge_density,
                point_ev_density=point_density,
                selected_candidate_id=selected_decision.economics.candidate_id,
                selected_edge_lcb_density=selected_edge_density,
                selected_point_ev_density=selected_point_density,
                dominates_selected=dominates,
            ),
        )

    # ------------------------------------------------ q_lcb reliability guard
    def _apply_qlcb_reliability_guard(
        self,
        *,
        scored: tuple[CandidateDecision, ...],
        case: ForecastCase,
        joint_q: JointQ,
        band: JointQBand,
        forecast_bin: str,
        matrix: FamilyPayoffMatrix,
        exposure: PortfolioExposureVector,
        sizing_candidates: Mapping[tuple[str, str], NativeSideCandidate],
        max_stake_usd: Optional[Decimal],
    ) -> tuple[CandidateDecision, ...]:
        """Deflate each candidate's served q_lcb by the empirical OOF reliability guard.

        Single-serving-rule flow §6. The candidate's served q_lcb is the route's
        robust payoff lower bound, ``economics.payoff_q_lcb``. It is produced in
        the payoff-vector layer from the same q-band/payoff vector that produced
        ``edge_lcb``; this guard does not reconstruct probability by adding cost
        back to an edge. The guard resolves the cell
        ``(metric, lead_bucket, side, bin_position, q_lcb_bucket)`` — ``side`` is the actual
        executable YES/NO claim, and ``bin_position`` is "modal" for the forecast (modal) bin,
        "nonmodal" otherwise (a stable, NON-per-city position label) — and returns
        ``q_safe = min(q_lcb_route, L_g)`` plus a trade/abstain verdict.

        On ABSTAIN (thin cell or below floor) the candidate's economics are re-stamped with a
        non-positive ``edge_lcb`` / ΔU / stake so the existing ``edge_lcb > 0`` and ΔU filters
        reject it — the candidate publishes its point prob but never trades. On a licensed
        deflation (``q_safe < q_lcb_route``), the engine recomputes ``edge_lcb``, robust ΔU,
        and stake with ``q_safe`` as the candidate-local guarded payoff lower bound. Point q
        / μ stay unchanged; only the downside economics consumed by selection are guarded.
        INERT (artifact absent) -> every verdict is pass-through and ``scored`` is returned
        unchanged.

        Read-only on μ. Guard faults are fail-closed: a broken active guard is not authority
        to trade, so the candidate is re-stamped as abstained and the existing edge_lcb>0
        filter rejects it.
        """
        lead_days = float(getattr(case, "lead_hours", 0.0) or 0.0) / 24.0
        metric = str(getattr(case, "metric", "")).lower()
        # The COVERAGE STRATIFIER for this family's city: a pure point-in-polygon coverage
        # property (fine_nest iff a sub-9km regional nest covers the settlement coordinate),
        # NOT a per-city de-bias. Derived identically to the OOF builder so coarse/fine cells
        # match. A coarse-only-nest (cold-biased) NO then reads its OWN realized hit-rate rather
        # than the fine-nest cities' calibration.
        precision_class = _qlcb_precision_class_for_city(getattr(case, "city", None))

        def _blocked_economics(econ: CandidateEconomics, *, edge_lcb: float) -> CandidateEconomics:
            return replace(
                econ,
                edge_lcb=float(edge_lcb),
                chosen_stake_edge_lcb=float(edge_lcb),
                delta_u_at_min=min(float(getattr(econ, "delta_u_at_min", 0.0) or 0.0), 0.0),
                optimal_stake_usd=Decimal("0"),
                optimal_delta_u=min(float(getattr(econ, "optimal_delta_u", 0.0) or 0.0), 0.0),
            )

        def _recomputed_guarded_economics(
            d: CandidateDecision,
            *,
            q_safe: float,
        ) -> CandidateEconomics:
            sizing = sizing_candidates.get((d.route.bin_id, d.route.side))
            if sizing is None or not sizing.is_tradeable:
                return _blocked_economics(
                    d.economics,
                    edge_lcb=float(q_safe) - self._selection_cost(d),
                )
            return compute_candidate_economics(
                d.route,
                joint_q=joint_q,
                band=band,
                sizing_candidate=sizing,
                matrix=matrix,
                exposure=exposure,
                max_stake_usd=max_stake_usd,
                guarded_payoff_q_lcb=float(q_safe),
            )

        out: list[CandidateDecision] = []
        for d in scored:
            try:
                econ = d.economics
                cost = self._selection_cost(d)
                edge_lcb = self._selection_edge_lcb(d)
                q_lcb_route = float(econ.payoff_q_lcb)
                if not math.isfinite(q_lcb_route) or not (0.0 <= q_lcb_route <= 1.0):
                    raise FamilyDecisionError("QKERNEL_PAYOFF_Q_LCB_MISSING")
                bin_position = "modal" if d.route.bin_id == forecast_bin else "nonmodal"
                verdict = _apply_qlcb_guard(
                    band_q_lcb=q_lcb_route,
                    metric=metric,
                    lead_days=lead_days,
                    side=d.route.side,
                    bin_position=bin_position,
                    precision_class=precision_class,
                )
                guard_fields = {
                    "q_lcb_guard_basis": verdict.basis,
                    "q_lcb_guard_abstained": bool(verdict.abstained),
                    "q_lcb_guard_cell_key": verdict.cell_key,
                }
                if (
                    d.route.side == "YES"
                    and bin_position == "modal"
                    and verdict.basis == "OOF_WILSON_95_POOLED_TAIL"
                    and q_lcb_route >= LIVE_DIRECTION_WIN_RATE_FLOOR
                    and edge_lcb > 0.0
                ):
                    # A pooled right-tail cell is same-claim evidence that the sparse
                    # high bucket is not an unknown family, but it is not the exact
                    # high-confidence bucket's numerical lower-bound authority. Let it
                    # license market-superiority while preserving the served qkernel
                    # qLCB/cost pair. Exact OOF cells still deflate below.
                    out.append(replace(d, **guard_fields))
                    continue
                if verdict.basis == "INERT" and not verdict.abstained:
                    out.append(replace(d, **guard_fields))  # no artifact; pass-through
                    continue
                if verdict.abstained:
                    # ABSTAIN: deflate the edge to a non-positive value so edge_lcb>0 rejects
                    # it. q_safe = 0 -> guarded edge = 0 − cost = −cost (< 0 for any real cost).
                    # Stake and both ΔU fields are blocked with the edge so no stale pre-guard
                    # sizing can survive into the selector or receipt.
                    new_edge = verdict.q_safe - cost
                    new_econ = _blocked_economics(econ, edge_lcb=float(new_edge))
                    out.append(replace(d, economics=new_econ, **guard_fields))
                    continue
                # Licensed deflation: lower the candidate payoff's q_lcb to q_safe and
                # recompute edge + robust ΔU + stake on a candidate-local guarded view.
                # The point q remains unchanged (the guard moves no μ), but the downside
                # samples consumed by edge_lcb and optimize_vector_stake now share the same
                # conservative reliability evidence.
                guarded_edge = verdict.q_safe - cost
                if guarded_edge < edge_lcb:
                    new_econ = _recomputed_guarded_economics(d, q_safe=float(verdict.q_safe))
                    out.append(replace(d, economics=new_econ, **guard_fields))
                else:
                    out.append(replace(d, **guard_fields))
            except Exception:  # noqa: BLE001 — guard failures are live-money abstains.
                econ = d.economics
                try:
                    cost = self._selection_cost(d)
                except Exception:  # noqa: BLE001
                    cost = 1.0
                new_econ = _blocked_economics(econ, edge_lcb=-max(cost, 1e-9))
                out.append(
                    replace(
                        d,
                        economics=new_econ,
                        q_lcb_guard_basis="QLCB_RELIABILITY_GUARD_ERROR",
                        q_lcb_guard_abstained=True,
                        q_lcb_guard_cell_key="ERROR",
                    )
                )
        return tuple(out)

    # ------------------------------------------- selection-aware settlement guard
    def _apply_selection_calibrator_guard(
        self,
        *,
        scored: tuple[CandidateDecision, ...],
        case: ForecastCase,
        joint_q: JointQ,
        band: JointQBand,
        forecast_bin: str,
        matrix: FamilyPayoffMatrix,
        exposure: PortfolioExposureVector,
        sizing_candidates: Mapping[tuple[str, str], NativeSideCandidate],
        max_stake_usd: Optional[Decimal],
    ) -> tuple[CandidateDecision, ...]:
        """Apply the selection-aware settlement lower bound before qkernel selection.

        This is the qkernel-side counterpart of the submit-time selection-curse check:
        each native YES/NO candidate is calibrated on the raw side probability that
        admission selected on (``q_dot_payoff`` for the candidate payoff). The guard never
        raises probability and never moves the forecast center; it only lowers the
        candidate-local ``payoff_q_lcb`` used by edge, robust DeltaU, stake, and ROI.

        Active armed cells that are missing/thin/stale remain live-money
        abstains for NO and nonmodal YES. Forecast-modal YES is not re-expressed
        through this selected-bias guard when the guard has no cell evidence:
        it stays under the qkernel payoff lower bound, the OOF reliability guard,
        market coherence, and live strategy price floors. That breaks the
        closed loop where a missing selected-bias cell starved all center-buy
        YES while still blocking tail/adjacent substitutes.
        """
        lead_days = float(getattr(case, "lead_hours", 0.0) or 0.0) / 24.0
        modal_yes_no_selection_bias_evidence = {
            "SIDE_NOT_ARMED",
            "ACTIVE_MISSING_CELL",
            "ACTIVE_THIN_CELL",
            "EB_THIN_SELECTED",
        }

        def _blocked_economics(econ: CandidateEconomics, *, edge_lcb: float) -> CandidateEconomics:
            return replace(
                econ,
                edge_lcb=float(edge_lcb),
                chosen_stake_edge_lcb=float(edge_lcb),
                delta_u_at_min=min(float(getattr(econ, "delta_u_at_min", 0.0) or 0.0), 0.0),
                optimal_stake_usd=Decimal("0"),
                optimal_delta_u=min(float(getattr(econ, "optimal_delta_u", 0.0) or 0.0), 0.0),
            )

        def _recomputed_guarded_economics(
            d: CandidateDecision,
            *,
            q_safe: float,
        ) -> CandidateEconomics:
            sizing = sizing_candidates.get((d.route.bin_id, d.route.side))
            if sizing is None or not sizing.is_tradeable:
                return _blocked_economics(
                    d.economics,
                    edge_lcb=float(q_safe) - self._selection_cost(d),
                )
            return compute_candidate_economics(
                d.route,
                joint_q=joint_q,
                band=band,
                sizing_candidate=sizing,
                matrix=matrix,
                exposure=exposure,
                max_stake_usd=max_stake_usd,
                guarded_payoff_q_lcb=float(q_safe),
            )

        out: list[CandidateDecision] = []
        for d in scored:
            try:
                econ = d.economics
                cost = self._selection_cost(d)
                prior_lcb = (
                    float(econ.payoff_q_lcb)
                    if econ.payoff_q_lcb is not None
                    else self._selection_edge_lcb(d) + cost
                )
                raw_side_prob = float(econ.q_dot_payoff)
                if not (
                    math.isfinite(cost)
                    and math.isfinite(prior_lcb)
                    and math.isfinite(raw_side_prob)
                    and 0.0 <= prior_lcb <= 1.0
                    and 0.0 <= raw_side_prob <= 1.0
                ):
                    raise FamilyDecisionError("SELECTION_GUARD_INPUT_MISSING")
                bin_class = "modal" if d.route.bin_id == forecast_bin else "nonmodal"
                verdict = apply_selection_calibrator(
                    raw_side_prob=raw_side_prob,
                    side=d.route.side,
                    lead_days=lead_days,
                    bin_class=bin_class,
                    admission_margin=cost,
                )
                unarmed_side = str(verdict.basis) == "SIDE_NOT_ARMED"
                modal_yes_without_selection_bias_evidence = (
                    d.route.side == "YES"
                    and bin_class == "modal"
                    and str(verdict.basis) in modal_yes_no_selection_bias_evidence
                )
                q_safe = float(min(prior_lcb, float(verdict.q_safe)))
                if unarmed_side:
                    q_safe = 0.0
                guard_fields = {
                    "selection_guard_basis": str(verdict.basis),
                    "selection_guard_abstained": bool(
                        verdict.abstained or not verdict.trade or unarmed_side
                    ),
                    "selection_guard_cell_key": str(verdict.cell_key),
                    "selection_guard_n": int(getattr(verdict, "n_g", 0) or 0),
                    "selection_guard_q_safe": float(q_safe),
                }
                if modal_yes_without_selection_bias_evidence:
                    out.append(
                        replace(
                            d,
                            selection_guard_basis="MODAL_YES_QKERNEL_OOF_GUARD",
                            selection_guard_abstained=False,
                            selection_guard_cell_key=(
                                f"{str(verdict.basis)}:{str(verdict.cell_key)}"
                            ),
                            selection_guard_n=int(getattr(verdict, "n_g", 0) or 0),
                            selection_guard_q_safe=float(prior_lcb),
                        )
                    )
                    continue
                if not verdict.trade or unarmed_side:
                    new_econ = _blocked_economics(econ, edge_lcb=-max(cost, 1e-9))
                    out.append(replace(d, economics=new_econ, **guard_fields))
                    continue
                guarded_edge = q_safe - cost
                if guarded_edge < self._selection_edge_lcb(d):
                    new_econ = _recomputed_guarded_economics(d, q_safe=q_safe)
                    out.append(replace(d, economics=new_econ, **guard_fields))
                else:
                    out.append(replace(d, **guard_fields))
            except Exception:  # noqa: BLE001 — active selection guard faults fail closed.
                econ = d.economics
                try:
                    cost = self._selection_cost(d)
                except Exception:  # noqa: BLE001
                    cost = 1.0
                new_econ = _blocked_economics(econ, edge_lcb=-max(cost, 1e-9))
                out.append(
                    replace(
                        d,
                        economics=new_econ,
                        selection_guard_basis="SELECTION_CALIBRATOR_GUARD_ERROR",
                        selection_guard_abstained=True,
                        selection_guard_cell_key="ERROR",
                        selection_guard_n=0,
                        selection_guard_q_safe=0.0,
                    )
                )
        return tuple(out)

    def _log_select_gate_diag(
        self,
        *,
        scored: Sequence[CandidateDecision],
        after_executable: Sequence[CandidateDecision],
        after_direction: Sequence[CandidateDecision],
        after_coherence: Sequence[CandidateDecision],
        positive_edge: Sequence[CandidateDecision],
        positive_utility: Sequence[CandidateDecision],
        positive_min_order: Sequence[CandidateDecision],
        survivors: Sequence[CandidateDecision],
    ) -> None:
        """Best-effort live evidence for the first empty selection gate."""

        try:
            import logging as _gate_diag

            _tops = sorted(
                scored,
                key=self._selection_edge_lcb,
                reverse=True,
            )[:4]
            _rows = "; ".join(
                f"{d.route.side}:{d.route.bin_id} dlok={int(d.direction_law_ok)} "
                f"adm={int(self._direction_admitted(d))} coh={int(d.coherence_allows)} "
                f"exec={int(d.route.route_cost.executable)} "
                f"e={self._selection_edge_lcb(d):+.4f} dU={d.economics.optimal_delta_u:+.5f} "
                f"dUmin={d.economics.delta_u_at_min:+.5f}"
                for d in _tops
            )
            _gate_diag.getLogger("zeus.spine_edge").info(
                "SELECT_GATE_DIAG n=%d exec=%d dir=%d coh=%d edge=%d du=%d min=%d live=%d tops=[%s]",
                len(scored),
                len(after_executable),
                len(after_direction),
                len(after_coherence),
                len(positive_edge),
                len(positive_utility),
                len(positive_min_order),
                len(survivors),
                _rows,
            )
        except Exception:
            pass

    # --------------------------------------------------------------- selection
    def _select(
        self, scored: Sequence[CandidateDecision]
    ) -> tuple[Optional[CandidateDecision], Optional[str]]:
        """Apply the filter chain and select by the configured robust objective.

        The filter ORDER is the contract:

            1. direction_law_ok          (the candidate has a live-tradable native side)
            2. coherence_allows          (the market-coherence report does NOT block the bin)
            3. edge_lcb > 0 AND optimal_delta_u > 0   (the vector edge + the vector ΔU)
               (the executable-route + native-side + coherence preconditions of the live
                pass are already true here, so live_candidate_passes is a re-proof)

        The default live objective selects on an ROI frontier: lower-bound edge per cost
        with absolute lower-bound profit and stake floors, then robust utility. Research
        callers may explicitly request ``total_delta_u`` or ``utility_density``. The scalar
        ``robust_trade_score`` is NEVER consulted. When the survivor set is empty, the
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

        # Direction law is a native-side route proof. It must not impose a rounded-mu
        # bin heuristic after q/payoff economics have already priced the route.
        after_direction = [d for d in after_executable if self._direction_admitted(d)]
        if not after_direction:
            return None, NO_TRADE_NO_DIRECTION_LAW

        after_coherence = [d for d in after_direction if d.coherence_allows]
        if not after_coherence:
            return None, NO_TRADE_MARKET_INCOHERENT

        positive_edge = [
            d
            for d in after_coherence
            if self._selection_edge_lcb(d) > 0.0
        ]
        if not positive_edge:
            self._log_select_gate_diag(
                scored=scored,
                after_executable=after_executable,
                after_direction=after_direction,
                after_coherence=after_coherence,
                positive_edge=positive_edge,
                positive_utility=(),
                positive_min_order=(),
                survivors=(),
            )
            return None, NO_TRADE_NO_POSITIVE_EDGE

        positive_utility = [
            d
            for d in positive_edge
            if d.economics.optimal_delta_u > 0.0
        ]
        if not positive_utility:
            self._log_select_gate_diag(
                scored=scored,
                after_executable=after_executable,
                after_direction=after_direction,
                after_coherence=after_coherence,
                positive_edge=positive_edge,
                positive_utility=positive_utility,
                positive_min_order=(),
                survivors=(),
            )
            return None, NO_TRADE_NO_POSITIVE_UTILITY

        positive_min_order = [
            d
            for d in positive_utility
            if d.economics.delta_u_at_min > 0.0
        ]
        if not positive_min_order:
            self._log_select_gate_diag(
                scored=scored,
                after_executable=after_executable,
                after_direction=after_direction,
                after_coherence=after_coherence,
                positive_edge=positive_edge,
                positive_utility=positive_utility,
                positive_min_order=positive_min_order,
                survivors=(),
            )
            return None, NO_TRADE_NO_MIN_ORDER_UTILITY

        # The live pass is a final structural re-proof (executable route, native-side
        # proof present, coherence accepted, vector edge/DeltaU).
        survivors = [
            d
            for d in positive_min_order
            if self._live_selectable_candidate(d)
        ]
        if not survivors:
            # READ-ONLY per-gate attribution diag (2026-06-15). The spine no-trade diag
            # logs positive-edge candidates while the reason is NO_POSITIVE_EDGE; that
            # diag reads only flattened economics and cannot say WHICH gate dropped the
            # harvest. This names, per top candidate, every gate flag + the per-stage
            # survivor counts, so the exact suppressor is auditable. Fail-safe; the diag
            # never raises into the decision path and changes no behavior.
            self._log_select_gate_diag(
                scored=scored,
                after_executable=after_executable,
                after_direction=after_direction,
                after_coherence=after_coherence,
                positive_edge=positive_edge,
                positive_utility=positive_utility,
                positive_min_order=positive_min_order,
                survivors=survivors,
            )
            return None, NO_TRADE_NO_POSITIVE_EDGE

        # SELECT: live defaults to an ROI frontier so capital-efficient YES can beat a
        # larger but lower-return NO without banning either side. There is deliberately
        # no selector-level fixed-dollar stake floor: venue minimum handling belongs to
        # ``delta_u_at_min`` and the submit-time min-order bump. Low-confidence tails are
        # rejected by confidence-weighted Kelly growth density plus lower-bound profit,
        # not by their raw notional size. Research callers can explicitly request total
        # utility or density.
        # The scalar trade score is NOT a key in any objective.
        if self._selection_objective == "roi_frontier":
            roi_frontier = self._roi_frontier_candidates(survivors)
            if not roi_frontier:
                return None, NO_TRADE_NO_ROI_FRONTIER_CANDIDATE
            selected = max(roi_frontier, key=self._roi_frontier_key)
        elif self._selection_objective == "total_delta_u":
            selected = max(
                survivors,
                key=lambda d: (
                    d.economics.optimal_delta_u,
                    self._edge_roi_lcb(d),
                    self._utility_density(d),
                    self._selection_edge_lcb(d),
                    -self._selection_cost(d),
                ),
            )
        else:
            selected = max(
                survivors,
                key=lambda d: (
                    self._utility_density(d),
                    d.economics.optimal_delta_u,
                    self._selection_edge_lcb(d),
                    -self._selection_cost(d),
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
            portfolio_comparisons=(),
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
        portfolio_comparisons: tuple[PortfolioCandidateDecision, ...] = (),
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
        for c in portfolio_comparisons:
            h.update(
                (
                    f"|PORTFOLIO|{c.portfolio_type}|{c.reference_bin_id}|"
                    f"{c.leg_candidate_ids!r}|{c.leg_route_ids!r}|"
                    f"{c.payoff_vector_hash}|{c.edge_lcb!r}|{c.cost_sum!r}|"
                    f"{int(c.dominates_selected)}"
                ).encode("utf-8")
            )
        return h.hexdigest()


def _license_nothing(_case_key: str, _bin_id: str) -> bool:
    """Default licensing predicate: nothing is licensed (no bin waives a coherence block)."""
    return False
