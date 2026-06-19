# Created: 2026-06-14
# Last reused or audited: 2026-06-14
# Authority basis: docs/rebuild/consult_build_spec.md
#   ("Create src/decision/family_decision_engine.py" block lines 854-904: the
#   FamilyDecision dataclass 858-871; the decide() algorithm 876-901 — the candidate
#   filter chain direction_law_ok -> coherence_allows -> (edge_lcb>0 & optimal_delta_u>0)
#   -> selected = max robust utility density; the no_trade_reason + receipt_hash on every
#   exit) and the Stage 8 block lines 1166-1184 (the scalar robust_trade_score is
#   telemetry only — it CANNOT select). Reconciled against
#   docs/evidence/qkernel_rebuild/spec_vs_live_drift_ledger.md (GREENFIELD — the engine
#   ASSEMBLES the already-built spine modules; market coherence dies BEFORE scoring; the
#   scalar q-price is logged, never selected on).
"""RED-on-revert contract tests for the family_decision_engine (Stage 8b).

Spec-named tests fail if the corrected transformation is reverted to the broken
behavior the spec replaces:

  * ``test_decide_filters_direction_then_coherence_then_edge_then_utility_density`` — the
    full candidate filter chain runs in the spec ORDER (direction_law_ok ->
    coherence_allows -> edge_lcb>0 & optimal_delta_u>0) and the survivor is selected by
    robust utility density, NOT by the scalar ``q - price`` trade score or capital-heavy
    total ΔU alone.

  * ``test_no_trade_reason_present_when_no_candidate_passes`` — when nothing survives the
    filter chain, ``decide`` returns a ``FamilyDecision`` with ``selected=None``, a non-None
    ``no_trade_reason``, and a ``receipt_hash`` (every exit is a reconstructable decision).
    RED-on-revert: a reversion that returned ``None`` (or omitted the no_trade_reason /
    receipt_hash) on a no-trade would make the no-trade unauditable; the test asserts the
    full no-trade FamilyDecision contract.

  * ``test_tokyo_impossible_bin_blocked_by_coherence_before_scoring`` — a Tokyo-class
    candidate whose model q wildly disagrees with a DEEP market q (logit gap >> 2.5) is
    dropped by the coherence filter BEFORE the edge/ΔU gate — its bin is in the coherence
    report's ``offending_bins`` and it never reaches selection, even though its raw vector
    edge is positive. RED-on-revert: if the coherence filter is removed (or runs AFTER the
    edge gate, or is softened to a q haircut), the incoherent candidate would be scored and
    could be selected; the test asserts it is filtered out (not selected) and that the
    decision's market_coherence status is INCOHERENT_BLOCK_LIVE.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from typing import Mapping, Optional, Sequence

import numpy as np
import pytest

import src.forecast.center as center_mod
import src.forecast.sigma_authority as sa
import src.decision.family_decision_engine as fde_mod
from src.config import City
from src.contracts.executable_cost_curve import (
    BookLevel,
    ExecutableCostCurve,
    FeeModel,
)
from src.contracts.execution_price import ExecutionPrice
from src.contracts.native_side_candidate import NativeSideCandidate
from src.decision.family_decision_engine import (
    CALIBRATED_NONMODAL_Q_FLOOR,
    NO_TRADE_NO_DIRECTION_LAW,
    NO_TRADE_NO_POSITIVE_EDGE,
    NO_TRADE_PREDICTIVE_NOT_LIVE_ELIGIBLE,
    CandidateDecision,
    FamilyDecision,
    FamilyDecisionEngine,
    direction_law_ok,
    forecast_bin_id,
)
from src.decision.payoff_vector import (
    CandidateEconomics,
    CandidateRoute,
    build_candidate_route,
)
from src.execution.family_book import (
    ExecutableLadder,
    FamilyBook,
    MarketBook,
    build_family_book,
)
from src.execution.negrisk_routes import RouteCost, build_negrisk_route_set
from src.forecast.day0_conditioner import Day0ObservationState
from src.forecast.debias_authority import DebiasAuthority
from src.forecast.predictive_distribution_builder import (
    PredictiveDistribution,
    PredictiveDistributionBuilder,
)
from src.forecast.types import ForecastCase, FreshModelSet, RawModelMember
from src.probability.event_resolution import (
    EventResolution,
    SEMANTICS_VERSION,
    event_resolution_for_city,
)
from src.probability.instruments import Instrument
from src.probability.joint_q import build_joint_q
from src.probability.joint_q_band import build_joint_q_band
from src.probability.outcome_space import (
    OutcomeBin,
    OutcomeSpace,
    compute_topology_hash,
)
from src.strategy.live_inference.executable_cost import QuoteLevel
from src.strategy.utility_ranker import (
    FamilyPayoffMatrix,
    PortfolioExposureVector,
)

# ---------------------------------------------------------------------------
# Forecast-spine fixtures (mirror tests/forecast/test_single_predictive_distribution_
# authority.py so a REAL eligible PredictiveDistribution is built — q / band integrate
# genuinely; the modal bin is deterministic).
# ---------------------------------------------------------------------------

ISSUE = datetime(2026, 6, 14, 0, 0, 0)
STATION = "RJTT"
PRODUCT_HASH = "modelset_tokyo_high_v1"
STATION_MAPPING = "RJTT_wu_icao"
# A tight-but-real settlement floor so the modal bin is sharp (sub-1°C concentrates the
# mass on the modal integer bin while staying a genuine realized floor).
REALIZED_FLOOR_C = 0.6
# A small band-draw count: the quantile contract the tests pin (edge_lcb ordering, ΔU
# argmax, coherence block) is stable at a few hundred draws, and the per-draw full
# settlement integration is the runtime cost — 200 keeps each decide() fast.
_TEST_BAND_DRAWS = 200
_CAPTURED = datetime(2026, 6, 14, 12, 0, 0, tzinfo=timezone.utc)


def _resolution(metric: str = "high") -> EventResolution:
    city = City(
        name="Tokyo",
        lat=35.68,
        lon=139.69,
        timezone="Asia/Tokyo",
        settlement_unit="C",
        cluster="asia",
        wu_station=STATION,
        settlement_source_type="wu_icao",
    )
    return event_resolution_for_city(city, date(2026, 6, 14), metric)


def _case(metric: str = "high") -> ForecastCase:
    return ForecastCase(
        city="Tokyo",
        city_id="tokyo",
        station_id=STATION,
        settlement_source_type="wu_icao",
        target_local_date=date(2026, 6, 14),
        metric=metric,  # type: ignore[arg-type]
        issue_time_utc=ISSUE,
        lead_hours=6.0,
        season="summer",
        regime_key="zonal",
        unit="C",
        resolution=_resolution(metric),
        family_id="tokyo_high_2026-06-14",
        source_cycle_time_utc=ISSUE - timedelta(hours=6),
    )


def _member(model_id: str, value_native: float, case: ForecastCase) -> RawModelMember:
    return RawModelMember(
        model_id=model_id,
        product_id=f"{model_id}_mx2t3",
        source_run_id=f"{model_id}_run_2026061400",
        source_cycle_time_utc=ISSUE - timedelta(hours=6),
        available_at_utc=ISSUE - timedelta(hours=1),
        value_native=value_native,
        station_mapping_id=STATION_MAPPING,
        raw_forecast_artifact_id=f"{model_id}_artifact",
        data_version="ecmwf_opendata_mx2t3_local_calendar_day_max",
    )


def _model_set(values_native: Sequence[float], case: ForecastCase) -> FreshModelSet:
    model_ids = [f"m{i}" for i in range(len(values_native))]
    members = tuple(_member(mid, v, case) for mid, v in zip(model_ids, values_native))
    arr = np.asarray(values_native, dtype=float)
    return FreshModelSet(
        case=case,
        members=members,
        member_values_native=arr,
        min_native=float(arr.min()),
        max_native=float(arr.max()),
        model_set_hash=PRODUCT_HASH,
    )


def _no_obs() -> Day0ObservationState:
    return Day0ObservationState(
        observed=False,
        station_id=STATION,
        source="none",
        samples_count=0,
        latest_observed_at_utc=None,
        observed_high_native=None,
        observed_low_native=None,
        observed_extreme_native=None,
        raw_observation_hash=None,
    )


# ---------------------------------------------------------------------------
# Omega fixtures (the SAME complete °C partition the joint_q / coherence / payoff
# contract tests use).
# ---------------------------------------------------------------------------

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


def _outcome_space(case: ForecastCase) -> OutcomeSpace:
    resolution = case.resolution
    rule = resolution.rounding_rule
    bins = _complete_bins(rule)
    space = OutcomeSpace(
        family_id=case.family_id,
        resolution=resolution,
        bins=bins,
        topology_hash=compute_topology_hash(case.family_id, resolution, bins),
    )
    space.validate()
    return space


# ---------------------------------------------------------------------------
# FamilyBook fixtures — a per-bin MarketBook factory with controllable YES depth /
# spread / price so a route is cheap (positive edge) or expensive, and a bin can be
# made deeply incoherent (deep market ~0.001 vs a high model q).
# ---------------------------------------------------------------------------

def _ladder(side: str, price: float, size: float) -> ExecutableLadder:
    return ExecutableLadder(
        levels=(QuoteLevel(Decimal(str(price)), Decimal(str(size))),),
        side=side,  # type: ignore[arg-type]
        fee_rate=0.0,  # zero fee so the test's expected costs are exact
        min_tick_size=Decimal("0.001"),
        min_order_size=Decimal("1.0"),
    )


def _tick(p: float) -> float:
    """Snap a probability to the 0.001 tick grid, kept strictly inside (0.001, 0.999).

    The leaf ``executable_cost`` walker rejects any ladder price off the venue tick grid;
    every test price must therefore land on a 0.001 multiple. (Bounds keep a YES/NO pair on
    the grid: ``1 - 0.001*k`` is also a 0.001 multiple.)
    """
    snapped = round(round(p / 0.001) * 0.001, 3)
    return min(max(snapped, 0.001), 0.999)


def _market_book(
    bin_id: str,
    *,
    yes_bid: float,
    yes_ask: float,
    no_bid: float,
    no_ask: float,
    size: float,
    neg_risk: bool = False,
) -> MarketBook:
    return MarketBook(
        condition_id=f"cond-{bin_id}",
        bin_id=bin_id,
        yes_token_id=f"yes-{bin_id}",
        no_token_id=f"no-{bin_id}",
        yes_asks=_ladder("ask", yes_ask, size),
        yes_bids=_ladder("bid", yes_bid, size),
        no_asks=_ladder("ask", no_ask, size),
        no_bids=_ladder("bid", no_bid, size),
        neg_risk=neg_risk,
    )


def _family_book(space: OutcomeSpace, market_for_bin) -> FamilyBook:
    markets = {b.bin_id: market_for_bin(b.bin_id) for b in space.bins}
    return build_family_book(omega=space, markets=markets, captured_at_utc=_CAPTURED)


# ---------------------------------------------------------------------------
# Reader / builder stubs (the reactor injects the real ones at Wave 5).
# ---------------------------------------------------------------------------

class _FreshModelReader:
    def __init__(self, model_set: FreshModelSet) -> None:
        self._model_set = model_set

    def read(self, case: ForecastCase) -> FreshModelSet:
        return self._model_set


class _Day0Reader:
    def __init__(self, obs: Optional[Day0ObservationState]) -> None:
        self._obs = obs

    def read(self, case: ForecastCase) -> Optional[Day0ObservationState]:
        return self._obs


class _PredictiveBuilder:
    """A real PredictiveDistributionBuilder wrapper matching the engine's Protocol."""

    def __init__(self, debias_authority: DebiasAuthority) -> None:
        self._builder = PredictiveDistributionBuilder(debias_authority)

    def build(self, case, models, obs=None) -> PredictiveDistribution:
        return self._builder.build(case, models, obs, has_fusion_capture=True)


class _StaticFamilyBookBuilder:
    """Returns a PRE-BUILT FamilyBook (the test controls the book directly)."""

    def __init__(self, family_book: FamilyBook) -> None:
        self._family_book = family_book

    def __call__(self, *, omega, snapshots_by_bin_id, captured_at_utc) -> FamilyBook:
        return self._family_book


# ---------------------------------------------------------------------------
# Sizing fixtures — a NativeSideCandidate per (bin, side) with a real cost curve, and
# the FamilyPayoffMatrix + flat exposure the ΔU sizing measures against.
# ---------------------------------------------------------------------------

def _yes_curve(bin_id: str, *, price: str, depth: str = "5000") -> ExecutableCostCurve:
    return ExecutableCostCurve(
        token_id=f"yes-{bin_id}",
        side="YES",
        snapshot_id="snap-1",
        book_hash=f"hash-yes-{bin_id}",
        levels=(BookLevel(price=Decimal(price), size=Decimal(depth)),),
        fee_model=FeeModel(fee_rate=Decimal("0.0")),
        min_tick=Decimal("0.001"),
        min_order_size=Decimal("1"),
        quote_ttl=timedelta(seconds=2),
    )


def _no_curve(bin_id: str, *, price: str, depth: str = "5000") -> ExecutableCostCurve:
    return ExecutableCostCurve(
        token_id=f"no-{bin_id}",
        side="NO",
        snapshot_id="snap-1",
        book_hash=f"hash-no-{bin_id}",
        levels=(BookLevel(price=Decimal(price), size=Decimal(depth)),),
        fee_model=FeeModel(fee_rate=Decimal("0.0")),
        min_tick=Decimal("0.001"),
        min_order_size=Decimal("1"),
        quote_ttl=timedelta(seconds=2),
    )


def _yes_sizing(space: OutcomeSpace, bin_id: str, *, q_point: float, q_lcb: float, price: str) -> NativeSideCandidate:
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


def _no_sizing(space: OutcomeSpace, bin_id: str, *, q_point: float, q_lcb: float, price: str) -> NativeSideCandidate:
    return NativeSideCandidate.tradeable(
        family_key=space.family_id,
        bin_id=bin_id,
        side="NO",
        token_id=f"no-{bin_id}",
        condition_id=f"cond-{bin_id}",
        q_point=q_point,
        q_lcb=q_lcb,
        probability_uncertainty=None,
        executable_cost_curve=_no_curve(bin_id, price=price),
        forecast_snapshot_id="fc-1",
        market_snapshot_id="mk-1",
        hypothesis_id=f"hyp-{bin_id}-NO",
    )


def _matrix(space: OutcomeSpace) -> FamilyPayoffMatrix:
    return FamilyPayoffMatrix.over_bins([b.bin_id for b in space.bins if b.executable])


def _engine(
    *,
    monkeypatch,
    model_set: FreshModelSet,
    obs: Optional[Day0ObservationState],
    family_book: FamilyBook,
    debias_authority: Optional[DebiasAuthority] = None,
    **engine_kwargs,
) -> FamilyDecisionEngine:
    """Assemble a FamilyDecisionEngine over real builders + a static family book.

    EMOS is patched OFF (the center is the in-envelope debiased consensus) and the realized
    σ-floor is pinned so the predictive distribution is genuinely live-eligible with a sharp
    modal bin — the q / band integrate for real.
    """
    monkeypatch.setattr(center_mod, "emos_predictive", lambda *a, **k: None)
    monkeypatch.setattr(sa, "settlement_sigma_floor", lambda *a, **k: REALIZED_FLOOR_C)
    return FamilyDecisionEngine(
        fresh_model_reader=_FreshModelReader(model_set),
        day0_reader=_Day0Reader(obs),
        predictive_builder=_PredictiveBuilder(debias_authority or DebiasAuthority(())),
        family_book_builder=_StaticFamilyBookBuilder(family_book),
        n_band_draws=_TEST_BAND_DRAWS,
        band_alpha=0.05,
        enable_negrisk_routes=False,  # direct YES/NO routing (no neg-risk basket needed)
        **engine_kwargs,
    )


# ===========================================================================
# Supporting primitive checks: the forecast (modal) bin + direction law.
# ===========================================================================

def test_forecast_bin_is_the_modal_bin_and_direction_law_reads_it():
    """forecast_bin_id is argmax q; YES is legal only on it, NO only off it (spec 947-951)."""
    case = _case()
    space = _outcome_space(case)
    # Members tightly around 25C -> modal bin b25.
    pd = PredictiveDistributionBuilder(DebiasAuthority(())).build(
        case, _model_set([24.5, 25.0, 25.5], case), _no_obs(), has_fusion_capture=True
    )
    assert pd.live_eligible
    jq = build_joint_q(pd, space)
    fbin = forecast_bin_id(jq)
    assert fbin == "b25", f"expected modal bin b25, got {fbin} (q={jq.q_by_bin_id})"

    yes_b25 = build_candidate_route(
        candidate_id="y25", instrument=_inst("YES", "b25"), route_cost=_rc("YES", "b25"), omega=space
    )
    yes_b27 = build_candidate_route(
        candidate_id="y27", instrument=_inst("YES", "b27"), route_cost=_rc("YES", "b27"), omega=space
    )
    no_b25 = build_candidate_route(
        candidate_id="n25", instrument=_inst("NO", "b25"), route_cost=_rc("NO", "b25"), omega=space
    )
    no_b27 = build_candidate_route(
        candidate_id="n27", instrument=_inst("NO", "b27"), route_cost=_rc("NO", "b27"), omega=space
    )
    # YES on the forecast bin is always legal regardless of its point-q (it IS the modal bin).
    assert direction_law_ok(yes_b25, forecast_bin=fbin, point_q=0.6) is True
    # NO is legal ONLY off the forecast bin (NO direction unchanged by the 2026-06-15 relax).
    assert direction_law_ok(no_b25, forecast_bin=fbin, point_q=0.6) is False
    assert direction_law_ok(no_b27, forecast_bin=fbin, point_q=0.02) is True
    # Non-modal YES (b27): legal IFF point-q reaches the settlement-validated calibrated floor.
    # Below the floor (the far tail with no settlement coverage) it stays modal-only-illegal;
    # at/above the floor it is admitted as a calibrated Arrow-Debreu claim (relax 2026-06-15).
    assert (
        direction_law_ok(yes_b27, forecast_bin=fbin, point_q=CALIBRATED_NONMODAL_Q_FLOOR - 0.01)
        is False
    )
    assert (
        direction_law_ok(yes_b27, forecast_bin=fbin, point_q=CALIBRATED_NONMODAL_Q_FLOOR)
        is True
    )


def test_nonmodal_yes_in_calibrated_domain_is_admitted_far_tail_still_modal_only():
    """RED-on-revert for the 2026-06-15 settlement-justified direction-law relax.

    The modal-only YES rule (pre-2026-06-15) suppressed settlement-CALIBRATED non-modal YES
    candidates: 6,450 settled non-modal bin-obs grade the non-modal tail in (0.05,0.35] at
    pred/real 1.05× (docs/evidence/qkernel_rebuild/nonmodal_bin_calibration_2026-06-15.md F1),
    while the modal bin the rule trusts grades 1.28× over-dispersed (F2) — the premise was
    inverted. The relax admits a non-modal YES whose point-q is INSIDE the validated
    calibrated domain (>= CALIBRATED_NONMODAL_Q_FLOOR), and keeps the far tail (below the
    floor, no settlement coverage, where the over-dispersed served σ + the conservative
    edge_lcb quantile amplify rather than shield) modal-only.

    A reversion to the modal-only rule (``return route.bin_id == forecast_bin`` for YES) makes
    the calibrated-domain assertion RED: the in-domain non-modal YES would flip to illegal.
    """
    fbin = "b25"

    def yes_on(bin_id: str) -> CandidateRoute:
        return build_candidate_route(
            candidate_id=f"y:{bin_id}",
            instrument=_inst("YES", bin_id),
            route_cost=_rc("YES", bin_id),
            omega=_outcome_space(_case()),
        )

    # In-domain non-modal YES (point-q at/above the calibrated floor) — ADMITTED by the relax,
    # REFUSED by a modal-only revert. This is the load-bearing RED-on-revert assertion.
    assert direction_law_ok(yes_on("b24"), forecast_bin=fbin, point_q=0.22) is True
    assert direction_law_ok(yes_on("b23"), forecast_bin=fbin, point_q=0.10) is True
    assert (
        direction_law_ok(yes_on("b26"), forecast_bin=fbin, point_q=CALIBRATED_NONMODAL_Q_FLOOR)
        is True
    )
    # Far-tail non-modal YES (point-q below the validated floor) — STILL modal-only-illegal:
    # the settlement evidence does not reach here and edge_lcb amplifies the over-dispersed σ.
    assert direction_law_ok(yes_on("b28"), forecast_bin=fbin, point_q=0.02) is False
    assert direction_law_ok(yes_on("b_high"), forecast_bin=fbin, point_q=0.0004) is False
    # The forecast (modal) bin YES is always legal; its point-q is irrelevant.
    assert direction_law_ok(yes_on("b25"), forecast_bin=fbin, point_q=0.0) is True


# Small helpers for the primitive check above.
def _inst(side: str, bin_id: str) -> Instrument:
    return Instrument(
        instrument_id=f"{side}:{bin_id}", bin_id=bin_id, side=side,  # type: ignore[arg-type]
        direct_token_id=f"{side.lower()}-{bin_id}",
    )


def _rc(side: str, bin_id: str, *, cost: float = 0.30, executable: bool = True) -> RouteCost:
    return RouteCost(
        route_id=f"{side}:{bin_id}@t",
        route_type="DIRECT_YES" if side == "YES" else "DIRECT_NO",
        instrument=_inst(side, bin_id),
        shares=Decimal("10"),
        avg_cost=ExecutionPrice(cost, price_type="fee_adjusted", fee_deducted=True, currency="probability_units"),
        max_shares=Decimal("100"),
        legs=(),
        executable=executable,
        reason=None if executable else "NO_DEPTH",
    )


def _hand_route(space: OutcomeSpace, *, side: str, bin_id: str, cost: float) -> CandidateRoute:
    """A real CandidateRoute (executable) over the Omega for the _select unit test."""
    return build_candidate_route(
        candidate_id=f"{side}:{bin_id}:hand",
        instrument=_inst(side, bin_id),
        route_cost=_rc(side, bin_id, cost=cost, executable=True),
        omega=space,
    )


def _hand_decision(
    route: CandidateRoute,
    *,
    edge_lcb: float,
    optimal_delta_u: float,
    delta_u_at_min: float,
    robust_trade_score: float,
    optimal_stake_usd: Decimal = Decimal("5"),
) -> CandidateDecision:
    """A hand-built CandidateDecision (direction-law-legal + coherent) for the _select test."""
    economics = CandidateEconomics(
        candidate_id=route.candidate_id,
        point_ev=edge_lcb + 0.01,
        edge_lcb=edge_lcb,
        delta_u_at_min=delta_u_at_min,
        optimal_stake_usd=optimal_stake_usd,
        optimal_delta_u=optimal_delta_u,
        q_dot_payoff=0.5,
        cost=route.route_cost.avg_cost,
        route_id=route.route_cost.route_id,
    )
    return CandidateDecision(
        route=route,
        economics=economics,
        direction_law_ok=True,
        coherence_allows=True,
        robust_trade_score=robust_trade_score,
    )


# ===========================================================================
# SPEC RED-on-revert #1: filter chain order + utility-density selection (not the scalar).
# ===========================================================================

def test_decide_filters_direction_then_coherence_then_edge_then_utility_density(monkeypatch):
    """The survivor is selected by robust utility density over the filter chain, NOT scalar score.

    Build a family centered on b25 (the forecast/modal bin) with a DEEP, tight market whose
    YES midpoints AGREE with the model q (coherence does NOT block anything). The candidate
    set is the direction-law-legal YES_25 plus the direction-law-legal NO candidates on every
    OTHER bin (a NO_i is legal iff i is not the forecast bin). Each candidate is priced so its
    vector edge is positive but its robust utility density differs.

    The load-bearing RED-on-revert fact: scalar ``robust_trade_score`` is not a selection key,
    and total ΔU alone is not enough either when it only wins by tying up more capital. The
    engine selects by ``optimal_delta_u / optimal_stake_usd`` over candidates that already pass
    direction/coherence/edge/ΔU gates.
    """
    case = _case()
    space = _outcome_space(case)
    model_set = _model_set([24.6, 25.0, 25.4], case)

    pd = PredictiveDistributionBuilder(DebiasAuthority(())).build(
        case, model_set, _no_obs(), has_fusion_capture=True
    )
    jq = build_joint_q(pd, space)
    assert forecast_bin_id(jq) == "b25"

    # A genuinely tradeable family: every bin's executable YES ask sits well BELOW its model
    # fair value (real underpricing -> a positive robust edge) while the midpoint stays close
    # enough to the model q that the candidate bins are COHERENT (the deep-tick-floor tail
    # shoulders b_low/b_high may be flagged, but they are not candidate bins). Prices snap to
    # the 0.001 tick grid.
    def factory(bin_id: str) -> MarketBook:
        fair = min(max(jq.q_by_bin_id.get(bin_id, 0.0), 0.02), 0.98)
        ya = _tick(max(fair * 0.5, 0.002))
        yb = _tick(max(ya - 0.01, 0.001))
        return _market_book(
            bin_id, yes_bid=yb, yes_ask=ya, no_bid=_tick(1 - ya), no_ask=_tick(1 - yb),
            size=5000.0,
        )

    fb = _family_book(space, factory)
    matrix = _matrix(space)
    exposure = PortfolioExposureVector.flat(matrix, baseline=Decimal("1000"))

    # Sizing candidates: a YES candidate per executable bin priced at the (cheap) market YES
    # ask, so the sizing cost matches the route cost. YES_25 (the forecast bin) is the only
    # direction-law-legal YES; the others are enumerated but direction-law-filtered out.
    route_set = build_negrisk_route_set(fb, shares=Decimal("100"), enable_negrisk_routes=False)
    sizing: dict[tuple[str, str], NativeSideCandidate] = {}
    for b in space.bins:
        if not b.executable:
            continue
        t = b.bin_id
        qb = jq.q_by_bin_id[t]
        yr = route_set.direct_yes.get(t)
        if yr is not None and yr.executable:
            sizing[(t, "YES")] = _yes_sizing(
                space, t, q_point=float(qb), q_lcb=max(float(qb) - 0.03, 0.001),
                price=str(round(float(yr.avg_cost.value), 3)),
            )

    engine = _engine(monkeypatch=monkeypatch, model_set=model_set, obs=_no_obs(), family_book=fb)

    decision = engine.decide(
        case,
        space,
        snapshots={},  # ignored; the static family-book builder returns fb
        portfolio=exposure,
        matrix=matrix,
        captured_at_utc=_CAPTURED,
        sizing_candidates=sizing,
        max_stake_usd=Decimal("1000"),
        shares_for_routing=Decimal("100"),
    )

    assert isinstance(decision, FamilyDecision)
    assert decision.no_trade_reason is None, decision.no_trade_reason
    assert decision.selected is not None
    assert decision.receipt_hash and len(decision.receipt_hash) == 64

    # The selected candidate is the direction-law-legal YES on the forecast bin b25 — the only
    # candidate that survives direction_law_ok (YES) + coherence + positive edge + positive ΔU.
    selected_decision = next(
        d for d in decision.candidate_decisions
        if d.economics.candidate_id == decision.selected.candidate_id
    )
    assert selected_decision.route.side == "YES"
    # The whole filter chain held on the selected candidate.
    assert selected_decision.direction_law_ok
    assert selected_decision.coherence_allows
    assert selected_decision.route.route_cost.executable
    assert selected_decision.economics.edge_lcb > 0.0
    assert selected_decision.economics.optimal_delta_u > 0.0

    # PIN THE SELECTION KEY: the selected candidate has the MAXIMUM utility density among the
    # candidates that passed the full filter chain (direction-law-legal, coherent, executable,
    # positive edge, positive ΔU). This is the utility-density contract.
    passing = [
        d
        for d in decision.candidate_decisions
        if d.direction_law_ok
        and d.coherence_allows
        and d.route.route_cost.executable
        and d.economics.edge_lcb > 0.0
        and d.economics.optimal_delta_u > 0.0
    ]
    assert passing
    selected_density = float(decision.selected.optimal_delta_u) / max(
        float(decision.selected.optimal_stake_usd), 1e-9
    )
    assert selected_density == pytest.approx(
        max(
            float(d.economics.optimal_delta_u) / max(float(d.economics.optimal_stake_usd), 1e-9)
            for d in passing
        )
    )

    # Post-2026-06-15 relax: a non-modal YES whose point-q is INSIDE the settlement-validated
    # calibrated domain (point_q >= CALIBRATED_NONMODAL_Q_FLOOR) is now direction-law-LEGAL.
    # b24 is an adjacent ring bin with point_q ~0.22 (well above the 0.05 floor), so its YES is
    # admitted as a calibrated Arrow-Debreu claim — it is no longer suppressed by the rule.
    yes_b24 = [d for d in decision.candidate_decisions if d.route.side == "YES" and d.route.bin_id == "b24"]
    assert yes_b24 and yes_b24[0].direction_law_ok is True
    assert (
        decision.candidate_decisions  # the b24 point-q clears the validated floor
        and jq.q_by_bin_id["b24"] >= CALIBRATED_NONMODAL_Q_FLOOR
    )


def test_select_uses_utility_density_not_scalar_trade_score(monkeypatch):
    """The engine's selection key is utility density, NOT scalar score.

    This is the isolated RED-on-revert for the selection KEY. We hand-build two passing
    candidate decisions (both direction-law-legal, coherent, executable, positive edge,
    positive ΔU) where one has a STRICTLY HIGHER scalar ``robust_trade_score`` but lower
    robust utility density. The engine's ``_select`` must choose the density winner.
    """
    case = _case()
    space = _outcome_space(case)
    # Two hand-built candidate routes on direction-law-legal sides (NO on non-forecast bins).
    route_lo_scalar_hi_du = _hand_route(space, side="NO", bin_id="b24", cost=0.20)
    route_hi_scalar_lo_du = _hand_route(space, side="NO", bin_id="b22", cost=0.05)

    # The density winner: lower scalar trade score, higher utility per staked dollar.
    win = _hand_decision(route_lo_scalar_hi_du, edge_lcb=0.10, optimal_delta_u=0.50,
                         delta_u_at_min=0.01, robust_trade_score=0.30,
                         optimal_stake_usd=Decimal("10"))
    # The scalar winner: HIGHER scalar trade score, lower density (a scalar-argmax
    # reversion would pick this one).
    lose = _hand_decision(route_hi_scalar_lo_du, edge_lcb=0.20, optimal_delta_u=0.20,
                          delta_u_at_min=0.01, robust_trade_score=0.90,
                          optimal_stake_usd=Decimal("100"))

    engine = FamilyDecisionEngine(
        fresh_model_reader=_FreshModelReader(_model_set([25.0], case)),
        day0_reader=_Day0Reader(_no_obs()),
        predictive_builder=_PredictiveBuilder(DebiasAuthority(())),
    )
    selected, reason = engine._select([win, lose])

    assert reason is None
    assert selected is not None
    # The selection is the density winner, NOT the scalar-argmax loser.
    assert selected.route.bin_id == "b24"
    assert selected.economics.optimal_delta_u == pytest.approx(0.50)
    # Prove the trap: the LOSER had the strictly higher scalar but was NOT selected.
    assert lose.robust_trade_score > win.robust_trade_score
    assert (
        float(selected.economics.optimal_delta_u) / float(selected.economics.optimal_stake_usd)
        > float(lose.economics.optimal_delta_u) / float(lose.economics.optimal_stake_usd)
    )


def test_select_prefers_capital_efficiency_over_capital_heavy_total_utility(monkeypatch):
    """A high-capital low-density route must not beat a lower-capital high-density route."""
    case = _case()
    space = _outcome_space(case)
    high_cost_route = _hand_route(space, side="NO", bin_id="b24", cost=0.80)
    low_cost_route = _hand_route(space, side="YES", bin_id="b25", cost=0.27)
    high_cost = _hand_decision(
        high_cost_route,
        edge_lcb=0.08,
        optimal_delta_u=0.20,
        delta_u_at_min=0.01,
        robust_trade_score=0.90,
        optimal_stake_usd=Decimal("100"),
    )
    low_cost = _hand_decision(
        low_cost_route,
        edge_lcb=0.08,
        optimal_delta_u=0.05,
        delta_u_at_min=0.01,
        robust_trade_score=0.10,
        optimal_stake_usd=Decimal("5"),
    )

    engine = FamilyDecisionEngine(
        fresh_model_reader=_FreshModelReader(_model_set([25.0], case)),
        day0_reader=_Day0Reader(_no_obs()),
        predictive_builder=_PredictiveBuilder(DebiasAuthority(())),
    )
    selected, reason = engine._select([high_cost, low_cost])

    assert reason is None
    assert selected is low_cost


def test_select_total_delta_u_objective_is_explicit_non_default(monkeypatch):
    """The terminal total-utility objective only applies when explicitly requested."""
    case = _case()
    space = _outcome_space(case)
    high_total_route = _hand_route(space, side="NO", bin_id="b24", cost=0.80)
    high_density_route = _hand_route(space, side="YES", bin_id="b25", cost=0.27)
    high_total = _hand_decision(
        high_total_route,
        edge_lcb=0.08,
        optimal_delta_u=0.20,
        delta_u_at_min=0.01,
        robust_trade_score=0.90,
        optimal_stake_usd=Decimal("100"),
    )
    high_density = _hand_decision(
        high_density_route,
        edge_lcb=0.08,
        optimal_delta_u=0.05,
        delta_u_at_min=0.01,
        robust_trade_score=0.10,
        optimal_stake_usd=Decimal("5"),
    )

    default_engine = FamilyDecisionEngine(
        fresh_model_reader=_FreshModelReader(_model_set([25.0], case)),
        day0_reader=_Day0Reader(_no_obs()),
        predictive_builder=_PredictiveBuilder(DebiasAuthority(())),
    )
    terminal_engine = FamilyDecisionEngine(
        fresh_model_reader=_FreshModelReader(_model_set([25.0], case)),
        day0_reader=_Day0Reader(_no_obs()),
        predictive_builder=_PredictiveBuilder(DebiasAuthority(())),
        selection_objective="total_delta_u",
    )

    default_selected, default_reason = default_engine._select([high_total, high_density])
    terminal_selected, terminal_reason = terminal_engine._select([high_total, high_density])

    assert default_reason is None
    assert terminal_reason is None
    assert default_selected is high_density
    assert terminal_selected is high_total


def test_select_rejects_unknown_selection_objective():
    with pytest.raises(ValueError, match="unknown selection_objective"):
        FamilyDecisionEngine(
            fresh_model_reader=_FreshModelReader(_model_set([25.0], _case())),
            day0_reader=_Day0Reader(_no_obs()),
            predictive_builder=_PredictiveBuilder(DebiasAuthority(())),
            selection_objective="scalar_score",  # type: ignore[arg-type]
        )


# ===========================================================================
# SPEC RED-on-revert #2: no_trade_reason present when no candidate passes.
# ===========================================================================

def test_no_trade_reason_present_when_no_candidate_passes(monkeypatch):
    """When nothing survives the chain, decide returns a no-trade FamilyDecision (not None).

    Build a family where EVERY route is priced so expensive that no candidate has a positive
    robust edge/ΔU (the direction-law-legal YES on the forecast bin is priced ABOVE its fair
    value, and the legal NOs likewise). The filter chain empties, so ``decide`` returns a
    FamilyDecision with ``selected=None``, a non-None ``no_trade_reason``, and a receipt_hash.
    RED-on-revert: a reversion returning None / omitting the reason would break this contract.
    """
    case = _case()
    space = _outcome_space(case)
    model_set = _model_set([24.6, 25.0, 25.4], case)
    pd = PredictiveDistributionBuilder(DebiasAuthority(())).build(
        case, model_set, _no_obs(), has_fusion_capture=True
    )
    jq = build_joint_q(pd, space)
    q25 = jq.q_by_bin_id["b25"]

    # Coherent deep market (YES mids at model q) so coherence does NOT block — the no-trade
    # is purely the empty positive-edge set. Prices snap to the 0.001 tick grid.
    def factory(bin_id: str) -> MarketBook:
        qm = jq.q_by_bin_id.get(bin_id, 0.0)
        mid = min(max(qm, 0.02), 0.98)
        yb, ya = _tick(mid - 0.01), _tick(mid + 0.01)
        return _market_book(bin_id, yes_bid=yb, yes_ask=ya, no_bid=_tick(1 - ya), no_ask=_tick(1 - yb), size=5000.0)

    fb = _family_book(space, factory)
    matrix = _matrix(space)
    exposure = PortfolioExposureVector.flat(matrix, baseline=Decimal("1000"))

    # Price EVERY candidate ABOVE its fair value: YES_25 at 0.99 (>> q25), NOs at 0.999.
    sizing: dict[tuple[str, str], NativeSideCandidate] = {
        ("b25", "YES"): _yes_sizing(space, "b25", q_point=float(q25), q_lcb=float(q25) - 0.03, price="0.99"),
    }
    for t in ("b23", "b24", "b26", "b27"):
        qb = jq.q_by_bin_id[t]
        sizing[(t, "NO")] = _no_sizing(space, t, q_point=float(1 - qb), q_lcb=float(1 - qb) - 0.03, price="0.999")

    engine = _engine(monkeypatch=monkeypatch, model_set=model_set, obs=_no_obs(), family_book=fb)
    decision = engine.decide(
        case, space, snapshots={}, portfolio=exposure, matrix=matrix,
        captured_at_utc=_CAPTURED, sizing_candidates=sizing,
        max_stake_usd=Decimal("1000"), shares_for_routing=Decimal("100"),
    )

    assert isinstance(decision, FamilyDecision)
    assert decision.selected is None
    assert decision.no_trade_reason is not None
    assert decision.no_trade_reason == NO_TRADE_NO_POSITIVE_EDGE, decision.no_trade_reason
    assert decision.receipt_hash and len(decision.receipt_hash) == 64
    # The candidates are still recorded (the no-trade is auditable), and none was selected.
    assert len(decision.candidates) > 0
    assert all(c is not decision.selected for c in decision.candidates)


def test_no_trade_predictive_not_live_eligible_is_first_gate(monkeypatch):
    """An ineligible predictive distribution short-circuits BEFORE q (spec lines 884-885).

    When the σ authority is missing (no fusion capture AND no realized floor), the predictive
    distribution is not live-eligible; ``decide`` returns the no-trade reason
    PREDICTIVE_DISTRIBUTION_NOT_LIVE_ELIGIBLE with joint_q / band / family_book all None — q
    was never integrated. The receipt is still present (the no-trade is reconstructable).
    """
    case = _case()
    space = _outcome_space(case)
    model_set = _model_set([24.6, 25.0, 25.4], case)

    # Pin NO realized floor, NO global lead-bucket floor, and NO fusion capture -> the sigma
    # authority has nothing to floor with -> live_eligible=False / PREDICTIVE_SIGMA_AUTHORITY_MISSING.
    monkeypatch.setattr(center_mod, "emos_predictive", lambda *a, **k: None)
    monkeypatch.setattr(sa, "settlement_sigma_floor", lambda *a, **k: None)
    monkeypatch.setattr(sa, "global_lead_bucket_floor", lambda *a, **k: 0.0)

    class _IneligibleBuilder:
        def __init__(self) -> None:
            self._b = PredictiveDistributionBuilder(DebiasAuthority(()))

        def build(self, c, m, o=None):
            return self._b.build(c, m, o, has_fusion_capture=False)

    fb = _family_book(
        space,
        lambda bid: _market_book(bid, yes_bid=0.04, yes_ask=0.06, no_bid=0.94, no_ask=0.96, size=5000.0),
    )
    engine = FamilyDecisionEngine(
        fresh_model_reader=_FreshModelReader(model_set),
        day0_reader=_Day0Reader(_no_obs()),
        predictive_builder=_IneligibleBuilder(),
        family_book_builder=_StaticFamilyBookBuilder(fb),
        n_band_draws=_TEST_BAND_DRAWS,
    )
    matrix = _matrix(space)
    decision = engine.decide(
        case, space, snapshots={}, portfolio=PortfolioExposureVector.flat(matrix, baseline=Decimal("1000")),
        matrix=matrix, captured_at_utc=_CAPTURED, sizing_candidates={},
        max_stake_usd=Decimal("1000"), shares_for_routing=Decimal("100"),
    )
    assert decision.selected is None
    assert decision.no_trade_reason == NO_TRADE_PREDICTIVE_NOT_LIVE_ELIGIBLE
    assert decision.joint_q is None
    assert decision.band is None
    assert decision.family_book is None
    assert decision.market_coherence is None
    assert decision.candidates == ()
    assert decision.receipt_hash and len(decision.receipt_hash) == 64


# ===========================================================================
# SPEC RED-on-revert #3: a Tokyo-impossible bin is blocked by COHERENCE before scoring.
# ===========================================================================

def test_tokyo_impossible_bin_blocked_by_coherence_before_scoring(monkeypatch):
    """The forecast bin's model q wildly disagrees with a DEEP market q -> blocked before scoring.

    The Tokyo incident: the model says the forecast (modal) bin b25 is the favorite (q ~ 0.5+),
    but the DEEP market prices it at ~0.001. The coherence report is INCOHERENT_BLOCK_LIVE with
    b25 in offending_bins, so the ONLY direction-law-legal YES (YES_25) is dropped by the
    coherence filter BEFORE the edge/ΔU gate — even though YES_25's native ask is cheap (its
    raw vector edge is positive). The decision is therefore a no-trade on the YES side: nothing
    direction-law-legal survives coherence.

    RED-on-revert: if the coherence filter is removed (or runs after the edge gate, or softened
    to a q haircut), YES_25 would be scored on its (positive) cheap-ask edge and selected. The
    test asserts YES_25 is NOT selected, is filtered by coherence (not coherence_allows), and
    that the decision's market_coherence status is INCOHERENT_BLOCK_LIVE with b25 offending.
    """
    case = _case()
    space = _outcome_space(case)
    model_set = _model_set([24.6, 25.0, 25.4], case)
    pd = PredictiveDistributionBuilder(DebiasAuthority(())).build(
        case, model_set, _no_obs(), has_fusion_capture=True
    )
    jq = build_joint_q(pd, space)
    assert forecast_bin_id(jq) == "b25"
    q25 = jq.q_by_bin_id["b25"]
    assert q25 > 0.20  # the model genuinely favors b25 (modal mass dominates)

    # DEEP market that prices b25 at the tick floor (~0.001) — wildly contrary to the model.
    # The rest of the family carries deep, tight YES quotes elsewhere so the projected market
    # q on b25 stays ~0.001 and the depth precondition is satisfied (size 5000).
    def factory(bin_id: str) -> MarketBook:
        if bin_id == "b25":
            return _market_book(bin_id, yes_bid=0.001, yes_ask=0.001, no_bid=0.999, no_ask=0.999, size=5000.0)
        return _market_book(bin_id, yes_bid=0.090, yes_ask=0.100, no_bid=0.900, no_ask=0.910, size=5000.0)

    fb = _family_book(space, factory)
    matrix = _matrix(space)
    exposure = PortfolioExposureVector.flat(matrix, baseline=Decimal("1000"))

    # YES_25 native ask is CHEAP (0.05 << q25) so WITHOUT the coherence gate it would have a
    # strong positive edge and be selected. NO candidates on other bins are priced expensive
    # so they cannot win on edge — isolating the coherence block as the only thing stopping b25.
    sizing: dict[tuple[str, str], NativeSideCandidate] = {
        ("b25", "YES"): _yes_sizing(space, "b25", q_point=float(q25), q_lcb=float(q25) - 0.03, price="0.05"),
    }
    for t in ("b23", "b24", "b26", "b27"):
        qb = jq.q_by_bin_id[t]
        sizing[(t, "NO")] = _no_sizing(space, t, q_point=float(1 - qb), q_lcb=float(1 - qb) - 0.03, price="0.999")

    engine = _engine(monkeypatch=monkeypatch, model_set=model_set, obs=_no_obs(), family_book=fb)
    decision = engine.decide(
        case, space, snapshots={}, portfolio=exposure, matrix=matrix,
        captured_at_utc=_CAPTURED, sizing_candidates=sizing,
        max_stake_usd=Decimal("1000"), shares_for_routing=Decimal("100"),
    )

    # The coherence report blocked b25 (the Tokyo incident, before scoring).
    assert decision.market_coherence is not None
    assert decision.market_coherence.status == "INCOHERENT_BLOCK_LIVE", (
        f"expected coherence block; got {decision.market_coherence.status} "
        f"(reason={decision.market_coherence.reason})"
    )
    assert "b25" in decision.market_coherence.offending_bins

    # YES_25 was NOT selected — it was dropped by the coherence filter, not on edge.
    if decision.selected is not None:
        assert not decision.selected.candidate_id.startswith("YES:b25:"), (
            "YES_25 must NOT be selected — coherence must block it before scoring"
        )

    # PIN: the YES_25 candidate exists, has a POSITIVE vector edge (so only coherence stops
    # it), and its coherence_allows flag is False (it is the offending bin).
    yes25 = [
        d for d in decision.candidate_decisions
        if d.route.side == "YES" and d.route.bin_id == "b25"
    ]
    assert yes25, "expected a YES_25 candidate to be enumerated"
    y = yes25[0]
    assert y.economics.edge_lcb > 0.0, (
        "YES_25 must have a positive raw edge so the test proves coherence (not edge) blocks it"
    )
    assert y.direction_law_ok is True  # it IS the forecast bin -> direction-law-legal
    assert y.coherence_allows is False  # but coherence blocks it (offending bin)

    # And the no-trade reason (if a no-trade) names the coherence block as the emptying gate
    # for the direction-law-legal set — every legal YES was the offending b25, every legal NO
    # priced out of edge.
    if decision.selected is None:
        assert decision.no_trade_reason in (
            "MARKET_INCOHERENT_BLOCK_LIVE",
            NO_TRADE_NO_DIRECTION_LAW,
            NO_TRADE_NO_POSITIVE_EDGE,
        ), decision.no_trade_reason


# ===========================================================================
# SPEC RED-on-revert #4: NO-on-modal requires side-aware OOF evidence.
# ===========================================================================

def test_no_on_modal_requires_side_aware_oof_license():
    """A NO-on-modal candidate cannot bypass direction law on edge alone.

    The prior relaxation admitted any NO candidate with edge_lcb>0. That was unsafe once the
    q_lcb reliability guard became active, because a missing NO-complement cell could pass
    through as INERT while the center YES cell was abstained. The override now requires an
    active, non-abstaining side-aware OOF verdict for the exact NO claim.
    """
    case = _case()
    space = _outcome_space(case)
    # The modal bin for members tightly around 25C is b25 (confirmed by
    # test_forecast_bin_is_the_modal_bin_and_direction_law_reads_it).
    # A NO on b25 is direction-law-ILLEGAL (d.direction_law_ok = False) but must be
    # admitted when edge_lcb > 0.0 (the favorite-longshot relaxation).
    modal_bin_id = "b25"

    # Build a NO-on-modal route: side="NO", bin_id=b25, cost=0.72 (a realistic
    # favorite-NO ask; the favorite-NO loses edge at cost>=0.79 per settlement evidence).
    no_on_modal_route = _hand_route(space, side="NO", bin_id=modal_bin_id, cost=0.72)

    # Build the economics: edge_lcb=0.08 (>0 so the relaxation fires AND the edge gate
    # passes), delta_u_at_min=0.001 (>0 so live_candidate_passes passes the ΔU-at-min
    # check), optimal_delta_u=0.05 (>0 so the ΔU gate passes).
    no_on_modal_economics = CandidateEconomics(
        candidate_id=no_on_modal_route.candidate_id,
        point_ev=0.09,          # edge_lcb + small spread
        edge_lcb=0.08,
        delta_u_at_min=0.001,
        optimal_stake_usd=Decimal("5"),
        optimal_delta_u=0.05,
        q_dot_payoff=0.22,      # model q_no = 1 - q_modal ~ 0.22 (plausible for b25 favorite)
        cost=no_on_modal_route.route_cost.avg_cost,
        route_id=no_on_modal_route.route_cost.route_id,
    )
    # direction_law_ok=False — this is the key: NO-on-modal is direction-law-ILLEGAL.
    no_on_modal_cand = CandidateDecision(
        route=no_on_modal_route,
        economics=no_on_modal_economics,
        direction_law_ok=False,
        coherence_allows=True,
        robust_trade_score=0.08,
    )
    licensed_no_on_modal_cand = CandidateDecision(
        route=no_on_modal_route,
        economics=no_on_modal_economics,
        direction_law_ok=False,
        coherence_allows=True,
        robust_trade_score=0.08,
        q_lcb_guard_basis="OOF_WILSON_95",
        q_lcb_guard_abstained=False,
        q_lcb_guard_cell_key="high|L1|NO|modal|qb7",
    )

    engine = FamilyDecisionEngine(
        fresh_model_reader=_FreshModelReader(_model_set([25.0], case)),
        day0_reader=_Day0Reader(_no_obs()),
        predictive_builder=_PredictiveBuilder(DebiasAuthority(())),
    )

    selected, reason = engine._select([no_on_modal_cand])
    assert selected is None
    assert reason == NO_TRADE_NO_DIRECTION_LAW

    selected, reason = engine._select([licensed_no_on_modal_cand])
    assert reason is None
    assert selected is not None
    assert selected.route.side == "NO"
    assert selected.route.bin_id == modal_bin_id
    # The critical invariant: admitted DESPITE direction_law_ok being False only because an
    # active side-aware OOF verdict licensed the NO complement claim.
    assert selected.direction_law_ok is False, (
        "expected direction_law_ok=False on the selected candidate — the test proves the "
        "admission is via the side-aware OOF license, not bare direction-law legality"
    )

    # ---- Confirm the relaxation is NO-side-only --------------------------------
    # A YES-on-non-modal (direction_law_ok=False, side="YES") with edge_lcb>0 must
    # NOT be admitted. The "after graded after-cost NEGATIVE" evidence (documented in
    # the engine's _select comment) means buy_yes on a NON-modal bin stays banned.
    non_modal_bin_id = "b24"
    yes_on_non_modal_route = _hand_route(space, side="YES", bin_id=non_modal_bin_id, cost=0.30)
    yes_on_non_modal_economics = CandidateEconomics(
        candidate_id=yes_on_non_modal_route.candidate_id,
        point_ev=0.11,
        edge_lcb=0.10,          # positive edge — but the ban holds
        delta_u_at_min=0.001,
        optimal_stake_usd=Decimal("5"),
        optimal_delta_u=0.05,
        q_dot_payoff=0.40,
        cost=yes_on_non_modal_route.route_cost.avg_cost,
        route_id=yes_on_non_modal_route.route_cost.route_id,
    )
    yes_on_non_modal_cand = CandidateDecision(
        route=yes_on_non_modal_route,
        economics=yes_on_non_modal_economics,
        direction_law_ok=False,
        coherence_allows=True,
        robust_trade_score=0.10,
    )

    selected2, reason2 = engine._select([yes_on_non_modal_cand])
    assert selected2 is None, (
        "YES-on-non-modal (direction_law_ok=False, side='YES') must NOT be admitted — "
        "the favorite-longshot relaxation is NO-side-only"
    )
    assert reason2 == NO_TRADE_NO_DIRECTION_LAW, (
        f"expected NO_TRADE_NO_DIRECTION_LAW for YES-on-non-modal; got {reason2!r}"
    )


def test_qlcb_guard_exception_abstains_candidate(monkeypatch):
    """A broken qLCB guard cannot leave a positive-edge live candidate untouched."""

    case = _case()
    space = _outcome_space(case)
    route = _hand_route(space, side="YES", bin_id="b25", cost=0.30)
    economics = CandidateEconomics(
        candidate_id=route.candidate_id,
        point_ev=0.20,
        edge_lcb=0.15,
        delta_u_at_min=0.001,
        optimal_stake_usd=Decimal("5"),
        optimal_delta_u=0.05,
        q_dot_payoff=0.45,
        cost=route.route_cost.avg_cost,
        route_id=route.route_cost.route_id,
    )
    candidate = CandidateDecision(
        route=route,
        economics=economics,
        direction_law_ok=True,
        coherence_allows=True,
        robust_trade_score=0.15,
    )

    def _boom(**_kwargs):
        raise RuntimeError("artifact parser exploded")

    monkeypatch.setattr(fde_mod, "_apply_qlcb_guard", _boom)
    engine = FamilyDecisionEngine(
        fresh_model_reader=_FreshModelReader(_model_set([25.0], case)),
        day0_reader=_Day0Reader(_no_obs()),
        predictive_builder=_PredictiveBuilder(DebiasAuthority(())),
    )
    pd = PredictiveDistributionBuilder(DebiasAuthority(())).build(
        case, _model_set([25.0], case), _no_obs(), has_fusion_capture=True
    )
    jq = build_joint_q(pd, space)

    guarded = engine._apply_qlcb_reliability_guard(
        scored=(candidate,),
        case=case,
        joint_q=jq,
        band=build_joint_q_band(pd, space, n_draws=_TEST_BAND_DRAWS, alpha=0.05),
        forecast_bin="b25",
        matrix=_matrix(space),
        exposure=PortfolioExposureVector.flat(_matrix(space), baseline=Decimal("1000")),
        sizing_candidates={
            ("b25", "YES"): _yes_sizing(
                space, "b25", q_point=0.50, q_lcb=0.45, price="0.30"
            )
        },
        max_stake_usd=Decimal("100"),
    )

    assert guarded[0].q_lcb_guard_basis == "QLCB_RELIABILITY_GUARD_ERROR"
    assert guarded[0].q_lcb_guard_abstained is True
    assert guarded[0].economics.edge_lcb < 0.0
    selected, reason = engine._select(guarded)
    assert selected is None
    assert reason == NO_TRADE_NO_POSITIVE_EDGE


def test_licensed_qlcb_deflation_recomputes_delta_u_and_stake(monkeypatch):
    """A licensed qLCB deflation recomputes ΔU/stake from q_safe instead of abstaining."""

    from src.decision.qlcb_reliability_guard import GuardVerdict

    case = _case()
    space = _outcome_space(case)
    route = _hand_route(space, side="YES", bin_id="b25", cost=0.30)
    economics = CandidateEconomics(
        candidate_id=route.candidate_id,
        point_ev=0.30,
        edge_lcb=0.20,  # q_lcb_route = edge + cost = 0.50 before reliability deflation.
        delta_u_at_min=0.001,
        optimal_stake_usd=Decimal("5"),
        optimal_delta_u=0.05,
        q_dot_payoff=0.50,
        cost=route.route_cost.avg_cost,
        route_id=route.route_cost.route_id,
    )
    candidate = CandidateDecision(
        route=route,
        economics=economics,
        direction_law_ok=True,
        coherence_allows=True,
        robust_trade_score=0.20,
    )

    def _licensed_deflation(**kwargs):
        assert kwargs["band_q_lcb"] == pytest.approx(0.50)
        return GuardVerdict(
            q_safe=0.49,  # Guarded edge remains positive: 0.49 - 0.30 = 0.19.
            trade=True,
            abstained=False,
            cell_key="high|L1|YES|modal|qb10",
            L_g=0.49,
            n_g=500,
            bucket_floor=0.50,
            basis="OOF_WILSON_95",
        )

    monkeypatch.setattr(fde_mod, "_apply_qlcb_guard", _licensed_deflation)
    engine = FamilyDecisionEngine(
        fresh_model_reader=_FreshModelReader(_model_set([25.0], case)),
        day0_reader=_Day0Reader(_no_obs()),
        predictive_builder=_PredictiveBuilder(DebiasAuthority(())),
    )
    pd = PredictiveDistributionBuilder(DebiasAuthority(())).build(
        case, _model_set([25.0], case), _no_obs(), has_fusion_capture=True
    )
    jq = build_joint_q(pd, space)
    matrix = _matrix(space)

    guarded = engine._apply_qlcb_reliability_guard(
        scored=(candidate,),
        case=case,
        joint_q=jq,
        band=build_joint_q_band(pd, space, n_draws=_TEST_BAND_DRAWS, alpha=0.05),
        forecast_bin="b25",
        matrix=matrix,
        exposure=PortfolioExposureVector.flat(matrix, baseline=Decimal("1000")),
        sizing_candidates={
            ("b25", "YES"): _yes_sizing(
                space, "b25", q_point=0.50, q_lcb=0.45, price="0.30"
            )
        },
        max_stake_usd=Decimal("100"),
    )

    guarded_economics = guarded[0].economics
    assert guarded[0].q_lcb_guard_basis == "OOF_WILSON_95"
    assert guarded[0].q_lcb_guard_abstained is False
    assert guarded_economics.edge_lcb == pytest.approx(0.19)
    assert guarded_economics.delta_u_at_min > 0.0
    assert guarded_economics.optimal_delta_u > 0.0
    assert guarded_economics.optimal_stake_usd > Decimal("0")

    selected, reason = engine._select(guarded)
    assert reason is None
    assert selected is not None
    assert selected.economics.edge_lcb == pytest.approx(0.19)
