# Created: 2026-06-14
# Last reused/audited: 2026-07-16
# Authority basis: docs/rebuild/consult_build_spec.md
#   ("Create src/decision/family_decision_engine.py" block lines 854-904: the
#   FamilyDecision dataclass 858-871; the decide() algorithm 876-901 — the candidate
#   filter chain direction_law_ok -> coherence_allows -> (edge_lcb>0 & optimal_delta_u>0)
#   -> selected = max total robust utility; the no_trade_reason + receipt_hash on every
#   exit) and the Stage 8 block lines 1166-1184 (the scalar robust_trade_score is
#   telemetry only — it CANNOT select). Reconciled against
#   docs/evidence/qkernel_rebuild/spec_vs_live_drift_ledger.md (GREENFIELD — the engine
#   ASSEMBLES the already-built spine modules; market coherence dies BEFORE scoring; the
#   scalar q-price is logged, never selected on).
"""RED-on-revert contract tests for the family_decision_engine (Stage 8b).

Spec-named tests fail if the corrected transformation is reverted to the broken
behavior the spec replaces:

  * ``test_decide_filters_direction_then_coherence_then_edge_then_total_utility`` — the
    full candidate filter chain runs in the spec ORDER (direction_law_ok ->
    coherence_allows -> edge_lcb>0 & optimal_delta_u>0) and the survivor is selected by
    total robust utility, NOT by the scalar ``q - price`` trade score or density alone.

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

import inspect
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from typing import Mapping, Optional, Sequence

import numpy as np
import pytest

import src.forecast.center as center_mod
import src.forecast.sigma_authority as sa
from src.decision import qlcb_reliability_guard as guard_mod
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
    NO_TRADE_NO_DIRECTION_LAW,
    NO_TRADE_NO_MIN_ORDER_UTILITY,
    NO_TRADE_NO_POSITIVE_EDGE,
    NO_TRADE_NO_POSITIVE_UTILITY,
    NO_TRADE_PREDICTIVE_NOT_LIVE_ELIGIBLE,
    CandidateDecision,
    FamilyDecision,
    FamilyDecisionEngine,
    PortfolioCandidateDecision,
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
from src.probability.joint_q import JointQ
from src.probability.joint_q_band import JointQBand, build_joint_q_band
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


@pytest.fixture(autouse=True)
def _isolate_qlcb_reliability_artifact(monkeypatch, tmp_path):
    """Keep core decision-engine contract tests independent from live artifacts."""
    monkeypatch.setattr(
        guard_mod,
        "_QLCB_OOF_RELIABILITY_PATH",
        str(tmp_path / "absent_qlcb_oof_reliability.json"),
    )
    guard_mod.reset_reliability_cache()
    yield
    guard_mod.reset_reliability_cache()


@pytest.fixture(autouse=True)
def _selection_calibrator_identity(monkeypatch):
    """Keep qkernel tests independent from generated live selection artifacts."""

    def _identity(**kwargs):
        raw = float(kwargs["raw_side_prob"])
        return SimpleNamespace(
            q_safe=raw,
            trade=True,
            abstained=False,
            cell_key="test|IDENTITY",
            L_g=float("nan"),
            n_g=0,
            basis="TEST_IDENTITY",
        )

    monkeypatch.setattr(fde_mod, "apply_selection_calibrator", _identity)


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
    # Direction law proves the native side is tradable; it no longer hard-codes the
    # rounded forecast bin as the only YES that may carry live alpha.
    assert direction_law_ok(yes_b25, forecast_bin=fbin) is True
    assert direction_law_ok(no_b25, forecast_bin=fbin) is True
    assert direction_law_ok(no_b27, forecast_bin=fbin) is True
    assert direction_law_ok(yes_b27, forecast_bin=fbin) is True


def test_buy_yes_direction_law_does_not_hard_gate_on_rounded_mu_bin():
    """Direction-law status is a native-side proof, not a forecast-bin veto.

    RED-on-revert: reintroducing ``YES only on rounded mu`` makes every non-forecast
    YES false here and recreates the live Shanghai/buy-yes starvation failure.
    """
    fbin = "b25"

    def yes_on(bin_id: str) -> CandidateRoute:
        return build_candidate_route(
            candidate_id=f"y:{bin_id}",
            instrument=_inst("YES", bin_id),
            route_cost=_rc("YES", bin_id),
            omega=_outcome_space(_case()),
        )

    # All native YES bins are allowed to reach the actual alpha gates. Bad bins die on
    # q/payoff/edge/DeltaU/coherence, not on the rounded center heuristic.
    assert direction_law_ok(yes_on("b25"), forecast_bin=fbin) is True
    assert direction_law_ok(yes_on("b24"), forecast_bin=fbin) is True
    assert direction_law_ok(yes_on("b23"), forecast_bin=fbin) is True
    assert direction_law_ok(yes_on("b26"), forecast_bin=fbin) is True
    assert direction_law_ok(yes_on("b28"), forecast_bin=fbin) is True
    assert direction_law_ok(yes_on("b_high"), forecast_bin=fbin) is True


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
    direction_law_ok: bool = True,
    coherence_allows: bool = True,
    q_lcb_guard_basis: str = "",
    q_lcb_guard_abstained: bool = False,
    q_lcb_guard_cell_key: str = "",
    selection_guard_basis: str = "",
    selection_guard_abstained: bool = False,
    selection_guard_cell_key: str = "",
    selection_guard_n: int = 0,
    selection_guard_q_safe: Optional[float] = None,
    payoff_q_lcb: Optional[float] = None,
    chosen_stake_cost: Optional[float] = None,
    chosen_stake_edge_lcb: Optional[float] = None,
    chosen_stake_point_ev: Optional[float] = None,
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
        payoff_q_lcb=payoff_q_lcb,
        chosen_stake_cost=(
            ExecutionPrice(
                chosen_stake_cost,
                price_type="fee_adjusted",
                fee_deducted=True,
                currency="probability_units",
            )
            if chosen_stake_cost is not None
            else None
        ),
        chosen_stake_edge_lcb=chosen_stake_edge_lcb,
        chosen_stake_point_ev=chosen_stake_point_ev,
    )
    return CandidateDecision(
        route=route,
        economics=economics,
        direction_law_ok=direction_law_ok,
        coherence_allows=coherence_allows,
        robust_trade_score=robust_trade_score,
        q_lcb_guard_basis=q_lcb_guard_basis,
        q_lcb_guard_abstained=q_lcb_guard_abstained,
        q_lcb_guard_cell_key=q_lcb_guard_cell_key,
        selection_guard_basis=selection_guard_basis,
        selection_guard_abstained=selection_guard_abstained,
        selection_guard_cell_key=selection_guard_cell_key,
        selection_guard_n=selection_guard_n,
        selection_guard_q_safe=selection_guard_q_safe,
    )


def _hand_joint_q_and_band(space: OutcomeSpace, q_by_bin: Mapping[str, float]) -> tuple[JointQ, JointQBand]:
    q = np.asarray([float(q_by_bin.get(b.bin_id, 0.0)) for b in space.bins], dtype=float)
    q = q / q.sum()
    joint = JointQ(
        omega=space,
        q=q,
        q_by_bin_id={b.bin_id: float(v) for b, v in zip(space.bins, q)},
        predictive_distribution_id="hand-pd",
        q_source="SETTLEMENT_STATION_NORMAL_V1",
        q_sum=float(q.sum()),
        identity_hash="hand-joint-q",
    )
    samples = np.repeat(q.reshape(1, -1), 8, axis=0)
    band = JointQBand(
        joint_q=joint,
        samples=samples,
        q_lcb=q,
        q_ucb=q,
        alpha=0.05,
        basis="PARAMETER_POSTERIOR_SIMPLEX_V1",
        sample_hash="hand-band",
    )
    return joint, band


# ===========================================================================
# SPEC RED-on-revert #1: filter chain order + total-utility selection (not the scalar).
# ===========================================================================

def test_decide_filters_direction_then_coherence_then_edge_then_total_utility(monkeypatch):
    """The survivor is selected by robust utility over the filter chain, NOT scalar score.

    Build a family centered on b25 with a DEEP, tight market whose YES midpoints AGREE
    with the model q (coherence does NOT block anything). Native YES/NO routes may all
    reach the vector economics gates. Each candidate is priced so its vector edge is
    positive but its robust utility density differs.

    The load-bearing RED-on-revert fact: scalar ``robust_trade_score`` is not a selection key.
    The engine selects by total ``optimal_delta_u`` over candidates that already pass
    direction/coherence/edge/ΔU gates; utility density is only a tie-breaker.
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

    # Sizing candidates: a YES candidate per executable bin priced at the (cheap) market
    # YES ask, so the sizing cost matches the route cost.
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
                space, t, q_point=float(qb), q_lcb=max(float(qb) - 0.03, 0.0),
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

    # The selected candidate survived native side proof + coherence + positive edge + positive ΔU.
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

    # PIN THE SELECTION KEY: the selected candidate has the MAXIMUM total robust utility among the
    # candidates that passed the full filter chain (direction-law-legal, coherent, executable,
    # positive edge, positive ΔU). This is the live total-utility contract.
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
    assert float(decision.selected.optimal_delta_u) == pytest.approx(
        max(float(d.economics.optimal_delta_u) for d in passing)
    )

    # Non-forecast YES reaches the same vector economics gates. If it has no
    # positive utility it will still fail there, but the rounded center is not a
    # hard direction veto.
    yes_b24 = [d for d in decision.candidate_decisions if d.route.side == "YES" and d.route.bin_id == "b24"]
    assert yes_b24 and yes_b24[0].direction_law_ok is True


def test_select_total_delta_u_objective_uses_utility_not_scalar_trade_score(monkeypatch):
    """The explicit total-utility objective is robust utility, NOT scalar score.

    This is the isolated RED-on-revert for the selection KEY. We hand-build two passing
    candidate decisions (both direction-law-legal, coherent, executable, positive edge,
    positive ΔU) where one has a STRICTLY HIGHER scalar ``robust_trade_score`` but lower
    total robust utility. The engine's ``_select`` must choose the robust-utility winner.
    """
    case = _case()
    space = _outcome_space(case)
    # Two hand-built candidate routes on valid native NO sides.
    route_lo_scalar_hi_du = _hand_route(space, side="NO", bin_id="b24", cost=0.20)
    route_hi_scalar_lo_du = _hand_route(space, side="NO", bin_id="b22", cost=0.05)

    # The robust-utility winner: lower scalar trade score, higher total DeltaU.
    win = _hand_decision(route_lo_scalar_hi_du, edge_lcb=0.10, optimal_delta_u=0.50,
                         delta_u_at_min=0.01, robust_trade_score=0.30,
                         optimal_stake_usd=Decimal("100"))
    # The scalar winner: HIGHER scalar trade score, lower total DeltaU (a scalar-argmax
    # reversion would pick this one).
    lose = _hand_decision(route_hi_scalar_lo_du, edge_lcb=0.20, optimal_delta_u=0.20,
                          delta_u_at_min=0.01, robust_trade_score=0.90,
                          optimal_stake_usd=Decimal("5"))

    engine = FamilyDecisionEngine(
        fresh_model_reader=_FreshModelReader(_model_set([25.0], case)),
        day0_reader=_Day0Reader(_no_obs()),
        predictive_builder=_PredictiveBuilder(DebiasAuthority(())),
        selection_objective="total_delta_u",
    )
    selected, reason = engine._select([win, lose])

    assert reason is None
    assert selected is not None
    # The selection is the total-utility winner, NOT the scalar-argmax loser.
    assert selected.route.bin_id == "b24"
    assert selected.economics.optimal_delta_u == pytest.approx(0.50)
    # Prove the trap: the LOSER had the strictly higher scalar but was NOT selected.
    assert lose.robust_trade_score > win.robust_trade_score
    assert float(selected.economics.optimal_delta_u) > float(lose.economics.optimal_delta_u)


def test_select_prefers_roi_frontier_by_default(monkeypatch):
    """Live default is confidence-weighted ROI, so strong cheap YES can beat larger NO."""
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
        edge_lcb=0.38,
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


def test_select_roi_frontier_keeps_small_stake_high_confidence_yes(monkeypatch):
    """A high-confidence cheap YES is not rejected just because its raw stake is small."""
    case = _case()
    space = _outcome_space(case)
    high_confidence_yes = _hand_decision(
        _hand_route(space, side="YES", bin_id="b25", cost=0.01),
        edge_lcb=0.56,
        optimal_delta_u=0.0037257573362340303,
        delta_u_at_min=0.00031653668040189115,
        robust_trade_score=0.56,
        optimal_stake_usd=Decimal("2.197675453300476"),
        payoff_q_lcb=0.57,
    )
    larger_notional_no = _hand_decision(
        _hand_route(space, side="NO", bin_id="b24", cost=0.80),
        edge_lcb=0.08,
        optimal_delta_u=0.20,
        delta_u_at_min=0.01,
        robust_trade_score=0.08,
        optimal_stake_usd=Decimal("100"),
    )

    engine = FamilyDecisionEngine(
        fresh_model_reader=_FreshModelReader(_model_set([25.0], case)),
        day0_reader=_Day0Reader(_no_obs()),
        predictive_builder=_PredictiveBuilder(DebiasAuthority(())),
    )
    selected, reason = engine._select([larger_notional_no, high_confidence_yes])

    assert high_confidence_yes.economics.optimal_stake_usd < Decimal("5")
    assert engine._roi_frontier_useful(high_confidence_yes) is True
    assert reason is None
    assert selected is high_confidence_yes


def test_select_roi_frontier_accepts_six_cent_yes_with_positive_robust_edge(monkeypatch):
    """Absolute hit rate cannot veto executable positive robust economics."""
    case = _case()
    space = _outcome_space(case)
    barely_positive_yes = _hand_decision(
        _hand_route(space, side="YES", bin_id="b25", cost=0.06),
        edge_lcb=0.01770,
        optimal_delta_u=0.000189,
        delta_u_at_min=0.000079,
        robust_trade_score=0.01770,
        optimal_stake_usd=Decimal("1.559569057373046875"),
        payoff_q_lcb=0.07770,
    )

    engine = FamilyDecisionEngine(
        fresh_model_reader=_FreshModelReader(_model_set([25.0], case)),
        day0_reader=_Day0Reader(_no_obs()),
        predictive_builder=_PredictiveBuilder(DebiasAuthority(())),
    )
    selected, reason = engine._select([barely_positive_yes])

    assert engine._profit_lcb_usd(barely_positive_yes) > 0.25
    assert engine._payoff_q_lcb(barely_positive_yes) >= fde_mod.roi_frontier_min_payoff_q_lcb(
        side="YES",
        cost=0.06,
    )
    assert engine._roi_frontier_useful(barely_positive_yes) is True
    assert selected is barely_positive_yes
    assert reason is None


def test_select_roi_frontier_keeps_strong_cheap_center_yes(monkeypatch):
    """A cheap center YES remains live when its q_lcb clears the center-YES quality floor."""
    case = _case()
    space = _outcome_space(case)
    strong_center_yes = _hand_decision(
        _hand_route(space, side="YES", bin_id="b25", cost=0.07),
        edge_lcb=0.51,
        optimal_delta_u=0.0025,
        delta_u_at_min=0.0002,
        robust_trade_score=0.51,
        optimal_stake_usd=Decimal("2.50"),
        payoff_q_lcb=0.58,
    )

    engine = FamilyDecisionEngine(
        fresh_model_reader=_FreshModelReader(_model_set([25.0], case)),
        day0_reader=_Day0Reader(_no_obs()),
        predictive_builder=_PredictiveBuilder(DebiasAuthority(())),
    )
    selected, reason = engine._select([strong_center_yes])

    assert engine._payoff_q_lcb(strong_center_yes) >= fde_mod.roi_frontier_min_payoff_q_lcb(
        side="YES",
        cost=0.07,
    )
    assert engine._roi_frontier_useful(strong_center_yes) is True
    assert reason is None
    assert selected is strong_center_yes


def test_select_roi_frontier_accepts_underpriced_buenos_aires_tail_yes(monkeypatch):
    """BA shape is admitted when q_lcb clears executable cost and utility gates."""
    case = _case()
    space = _outcome_space(case)
    ba_tail_yes = _hand_decision(
        _hand_route(space, side="YES", bin_id="b25", cost=0.041),
        edge_lcb=0.041246376484684766,
        optimal_delta_u=0.003,
        delta_u_at_min=0.0002,
        robust_trade_score=1.42,
        optimal_stake_usd=Decimal("23.68994700639801"),
        payoff_q_lcb=0.0990451308919892,
    )

    engine = FamilyDecisionEngine(
        fresh_model_reader=_FreshModelReader(_model_set([25.0], case)),
        day0_reader=_Day0Reader(_no_obs()),
        predictive_builder=_PredictiveBuilder(DebiasAuthority(())),
    )
    selected, reason = engine._select([ba_tail_yes])

    assert engine._payoff_q_lcb(ba_tail_yes) >= fde_mod.roi_frontier_min_payoff_q_lcb(
        side="YES",
        cost=0.041,
    )
    assert engine._roi_frontier_useful(ba_tail_yes) is True
    assert selected is ba_tail_yes
    assert reason is None


def test_select_roi_frontier_rejects_low_confidence_tail_over_strong_no(monkeypatch):
    """High raw edge/price is not enough when Kelly lower-bound confidence is tiny."""
    case = _case()
    space = _outcome_space(case)
    strong_no_route = _hand_route(space, side="NO", bin_id="b24", cost=0.74)
    cheap_tail_route = _hand_route(space, side="YES", bin_id="b25", cost=0.01)
    strong_no = _hand_decision(
        strong_no_route,
        edge_lcb=0.156,
        optimal_delta_u=0.20,
        delta_u_at_min=0.01,
        robust_trade_score=0.20,
        optimal_stake_usd=Decimal("100"),
    )
    cheap_tail = _hand_decision(
        cheap_tail_route,
        edge_lcb=0.0024,
        optimal_delta_u=0.05,
        delta_u_at_min=0.01,
        robust_trade_score=0.10,
        optimal_stake_usd=Decimal("100"),
    )

    engine = FamilyDecisionEngine(
        fresh_model_reader=_FreshModelReader(_model_set([25.0], case)),
        day0_reader=_Day0Reader(_no_obs()),
        predictive_builder=_PredictiveBuilder(DebiasAuthority(())),
    )
    selected, reason = engine._select([strong_no, cheap_tail])

    assert reason is None
    assert engine._edge_roi_lcb(cheap_tail) > engine._edge_roi_lcb(strong_no)
    assert engine._robust_kelly_growth_density(strong_no) > engine._robust_kelly_growth_density(cheap_tail)
    assert selected is strong_no


def test_select_roi_frontier_accepts_small_probability_when_absolute_profit_clears_floor(monkeypatch):
    """Low absolute q is admissible; profit and utility floors still govern dust."""
    case = _case()
    space = _outcome_space(case)
    dust_route = _hand_route(space, side="YES", bin_id="b25", cost=0.005)
    dust = _hand_decision(
        dust_route,
        edge_lcb=0.005,
        optimal_delta_u=0.01,
        delta_u_at_min=0.001,
        robust_trade_score=0.10,
        optimal_stake_usd=Decimal("1.50"),
    )

    engine = FamilyDecisionEngine(
        fresh_model_reader=_FreshModelReader(_model_set([25.0], case)),
        day0_reader=_Day0Reader(_no_obs()),
        predictive_builder=_PredictiveBuilder(DebiasAuthority(())),
    )
    selected, reason = engine._select([dust])

    assert engine._payoff_q_lcb(dust) >= fde_mod._ROI_FRONTIER_MIN_PAYOFF_Q_LCB
    assert engine._profit_lcb_usd(dust) >= fde_mod.roi_frontier_min_profit_lcb_usd()
    assert engine._roi_frontier_useful(dust) is True
    assert selected is dust
    assert reason is None


def test_select_roi_frontier_accepts_underpriced_kuala_lumpur_tail_yes(monkeypatch):
    """KL shape is admitted by robust price-relative economics, not absolute q."""
    case = _case()
    space = _outcome_space(case)
    kl_tail = _hand_decision(
        _hand_route(space, side="YES", bin_id="b25", cost=0.04001526925923045),
        edge_lcb=0.020510409830349664,
        optimal_delta_u=0.0006333828915951036,
        delta_u_at_min=0.00009152233738979263,
        robust_trade_score=0.08180248510788457,
        optimal_stake_usd=Decimal("1.4412832709285736083984375"),
        payoff_q_lcb=0.06052567908958011,
    )

    engine = FamilyDecisionEngine(
        fresh_model_reader=_FreshModelReader(_model_set([25.0], case)),
        day0_reader=_Day0Reader(_no_obs()),
        predictive_builder=_PredictiveBuilder(DebiasAuthority(())),
    )
    selected, reason = engine._select([kl_tail])

    assert engine._payoff_q_lcb(kl_tail) >= fde_mod.roi_frontier_min_payoff_q_lcb(
        side="YES",
        cost=0.04001526925923045,
    )
    assert engine._roi_frontier_useful(kl_tail) is True
    assert selected is kl_tail
    assert reason is None


def test_select_roi_frontier_uses_chosen_stake_cost_not_route_cost(monkeypatch):
    """A route that is cheap only at scalar admission cannot survive live selection."""

    case = _case()
    space = _outcome_space(case)
    stale_top_of_book = _hand_decision(
        _hand_route(space, side="YES", bin_id="b25", cost=0.01),
        edge_lcb=0.20,
        optimal_delta_u=0.20,
        delta_u_at_min=0.01,
        robust_trade_score=0.20,
        optimal_stake_usd=Decimal("25"),
        chosen_stake_cost=0.50,
        chosen_stake_edge_lcb=-0.29,
        chosen_stake_point_ev=-0.10,
        payoff_q_lcb=0.21,
    )
    honest_no = _hand_decision(
        _hand_route(space, side="NO", bin_id="b24", cost=0.35),
        edge_lcb=0.21,
        optimal_delta_u=0.06,
        delta_u_at_min=0.01,
        robust_trade_score=0.21,
        optimal_stake_usd=Decimal("25"),
        payoff_q_lcb=0.56,
    )

    engine = FamilyDecisionEngine(
        fresh_model_reader=_FreshModelReader(_model_set([25.0], case)),
        day0_reader=_Day0Reader(_no_obs()),
        predictive_builder=_PredictiveBuilder(DebiasAuthority(())),
    )
    selected, reason = engine._select([stale_top_of_book, honest_no])

    assert engine._selection_edge_lcb(stale_top_of_book) < 0.0
    assert selected is honest_no
    assert reason is None


def test_select_roi_frontier_allows_positive_utility_candidate_below_old_roi_hurdle(monkeypatch):
    """Karachi/Shenzhen shape: real lower-bound profit + min-order utility is not dust."""
    case = _case()
    space = _outcome_space(case)
    route = _hand_route(space, side="YES", bin_id="b25", cost=0.14)
    candidate = _hand_decision(
        route,
        edge_lcb=0.0100720692012259,
        optimal_delta_u=0.000288,
        delta_u_at_min=0.000045,
        robust_trade_score=0.0100720692012259,
        optimal_stake_usd=Decimal("5.64889651611328125"),
        payoff_q_lcb=0.150072069201226,
    )

    engine = FamilyDecisionEngine(
        fresh_model_reader=_FreshModelReader(_model_set([25.0], case)),
        day0_reader=_Day0Reader(_no_obs()),
        predictive_builder=_PredictiveBuilder(DebiasAuthority(())),
    )
    selected, reason = engine._select([candidate])

    assert engine._profit_lcb_usd(candidate) >= 0.25
    assert engine._payoff_q_lcb(candidate) >= fde_mod._ROI_FRONTIER_MIN_PAYOFF_Q_LCB
    assert selected is candidate
    assert reason is None


def test_select_roi_frontier_allows_live_positive_profit_candidate_below_old_direct_roi_hurdle(monkeypatch):
    """Live 2026-06-30 shape: positive LCB profit must reach the ROI frontier.

    Regression: the selector returned NO_ROI_FRONTIER_USEFUL_CANDIDATE for a candidate with
    positive edge_lcb, positive DeltaU, positive min-order DeltaU, q_lcb around 0.779, and
    about $2.13 lower-bound profit solely because an extra 5% direct-ROI hard hurdle failed.
    ROI/growth density rank the frontier; they must not be a second arbitrary no-order gate.
    """
    case = _case()
    space = _outcome_space(case)
    route = _hand_route(space, side="NO", bin_id="b25", cost=0.76)
    candidate = _hand_decision(
        route,
        edge_lcb=0.01877,
        optimal_delta_u=0.000983,
        delta_u_at_min=0.000083,
        robust_trade_score=0.01877,
        optimal_stake_usd=Decimal("86.2839573930664062500"),
        payoff_q_lcb=0.77877,
    )

    engine = FamilyDecisionEngine(
        fresh_model_reader=_FreshModelReader(_model_set([25.0], case)),
        day0_reader=_Day0Reader(_no_obs()),
        predictive_builder=_PredictiveBuilder(DebiasAuthority(())),
    )
    selected, reason = engine._select([candidate])

    assert 0.0 < engine._edge_roi_lcb(candidate) < 0.05
    assert engine._profit_lcb_usd(candidate) >= 2.0
    assert engine._payoff_q_lcb(candidate) >= fde_mod._ROI_FRONTIER_MIN_PAYOFF_Q_LCB
    assert selected is candidate
    assert reason is None


def test_select_reports_positive_edge_but_no_positive_utility():
    """Positive edge with zero robust DeltaU is not a no-edge market."""
    case = _case()
    space = _outcome_space(case)
    candidate = _hand_decision(
        _hand_route(space, side="YES", bin_id="b25", cost=0.12),
        edge_lcb=0.04,
        optimal_delta_u=0.0,
        delta_u_at_min=0.0,
        robust_trade_score=0.04,
        optimal_stake_usd=Decimal("0"),
        payoff_q_lcb=0.16,
    )
    engine = FamilyDecisionEngine(
        fresh_model_reader=_FreshModelReader(_model_set([25.0], case)),
        day0_reader=_Day0Reader(_no_obs()),
        predictive_builder=_PredictiveBuilder(DebiasAuthority(())),
    )

    selected, reason = engine._select([candidate])

    assert selected is None
    assert reason == NO_TRADE_NO_POSITIVE_UTILITY


def test_select_reports_positive_utility_but_min_order_not_viable():
    """A theoretical utility winner that fails min-order DeltaU is its own gate."""
    case = _case()
    space = _outcome_space(case)
    candidate = _hand_decision(
        _hand_route(space, side="YES", bin_id="b25", cost=0.12),
        edge_lcb=0.04,
        optimal_delta_u=0.02,
        delta_u_at_min=0.0,
        robust_trade_score=0.04,
        optimal_stake_usd=Decimal("10"),
        payoff_q_lcb=0.16,
    )
    engine = FamilyDecisionEngine(
        fresh_model_reader=_FreshModelReader(_model_set([25.0], case)),
        day0_reader=_Day0Reader(_no_obs()),
        predictive_builder=_PredictiveBuilder(DebiasAuthority(())),
    )

    selected, reason = engine._select([candidate])

    assert selected is None
    assert reason == NO_TRADE_NO_MIN_ORDER_UTILITY


def test_abstained_oof_guard_blocks_nonmodal_yes_on_economics(monkeypatch):
    """Tokyo-class regression: an abstained OOF cell must zero economics, not direction."""
    case = _case(metric="low")
    space = _outcome_space(case)
    modal_yes = _hand_decision(
        _hand_route(space, side="YES", bin_id="b25", cost=0.20),
        edge_lcb=-0.01,
        optimal_delta_u=0.0,
        delta_u_at_min=-0.01,
        robust_trade_score=0.0,
        direction_law_ok=True,
        q_lcb_guard_basis="OOF_WILSON_95",
        q_lcb_guard_cell_key="low|L2_3|YES|modal|qb2|coarse_global",
    )
    nonmodal_yes = _hand_decision(
        _hand_route(space, side="YES", bin_id="b24", cost=0.005),
        edge_lcb=-0.005,
        optimal_delta_u=0.0,
        delta_u_at_min=0.0,
        robust_trade_score=0.13,
        direction_law_ok=True,
        q_lcb_guard_basis="OOF_WILSON_95",
        q_lcb_guard_abstained=True,
        q_lcb_guard_cell_key="low|L2_3|YES|nonmodal|qb2|coarse_global",
    )
    engine = FamilyDecisionEngine(
        fresh_model_reader=_FreshModelReader(_model_set([25.0], case)),
        day0_reader=_Day0Reader(_no_obs()),
        predictive_builder=_PredictiveBuilder(DebiasAuthority(())),
    )

    selected, reason = engine._select([modal_yes, nonmodal_yes])

    assert selected is None
    assert reason == NO_TRADE_NO_POSITIVE_EDGE
    assert nonmodal_yes.direction_law_ok is True
    assert engine._direction_admitted(nonmodal_yes) is True


def test_day0_finite_boundary_yes_routes_through_qlcb_reliability_guard(monkeypatch):
    """Day0 finite-bin YES uses remaining-window qLCB, not a hard-fact override --

    and (fix for the Wellington/Manila buy_yes point-bin losses) that remaining-window
    candidate is NOT a hard fact, so it is routed through the SAME empirical q_lcb OOF
    reliability guard every non-Day0 candidate passes through, instead of an unchecked
    raw-model pass-through. With no OOF artifact present (this suite's autouse fixture
    keeps the artifact path absent) the guard is INERT -- the numeric economics are
    unchanged from the pre-guard remaining-day qLCB, but the provenance now reflects the
    real guard verdict (``INERT``), not the Day0-only hard-fact stamp.
    """

    case = _case()
    space = _outcome_space(case)
    obs = Day0ObservationState(
        observed=True,
        station_id=STATION,
        source="wu_icao",
        samples_count=8,
        latest_observed_at_utc=_CAPTURED,
        observed_high_native=25.0,
        observed_low_native=None,
        observed_extreme_native=25.0,
        raw_observation_hash="day0-observed-boundary",
    )
    model_set = _model_set([24.9, 25.0, 25.1], case)
    pd = PredictiveDistributionBuilder(DebiasAuthority(())).build(
        case, model_set, obs, has_fusion_capture=True
    )
    jq = build_joint_q(pd, space)
    assert pd.day0.active is True
    assert forecast_bin_id(jq) == "b25"

    calls: list[str] = []
    real_apply_qlcb_guard = fde_mod._apply_qlcb_guard

    def _spy_apply_qlcb_guard(**kwargs):
        calls.append(kwargs["bin_position"])
        return real_apply_qlcb_guard(**kwargs)

    def _selection_guard_must_not_run(**_kwargs):
        raise AssertionError("forecast selection calibrator must not run for active Day0")

    monkeypatch.setattr(fde_mod, "_apply_qlcb_guard", _spy_apply_qlcb_guard)
    monkeypatch.setattr(
        fde_mod, "apply_selection_calibrator", _selection_guard_must_not_run
    )

    def factory(bin_id: str) -> MarketBook:
        fair = min(max(jq.q_by_bin_id.get(bin_id, 0.0), 0.02), 0.98)
        ya = _tick(max(fair * 0.55, 0.002))
        yb = _tick(max(ya - 0.01, 0.001))
        return _market_book(
            bin_id,
            yes_bid=yb,
            yes_ask=ya,
            no_bid=_tick(1 - ya),
            no_ask=_tick(1 - yb),
            size=5000.0,
        )

    fb = _family_book(space, factory)
    matrix = _matrix(space)
    route_set = build_negrisk_route_set(
        fb, shares=Decimal("100"), enable_negrisk_routes=False
    )
    sizing: dict[tuple[str, str], NativeSideCandidate] = {}
    for b in space.bins:
        if not b.executable:
            continue
        route = route_set.direct_yes.get(b.bin_id)
        if route is None or not route.executable:
            continue
        q_point = float(jq.q_by_bin_id[b.bin_id])
        sizing[(b.bin_id, "YES")] = _yes_sizing(
            space,
            b.bin_id,
            q_point=q_point,
            q_lcb=max(q_point - 0.03, 0.0),
            price=str(round(float(route.avg_cost.value), 3)),
        )

    engine = _engine(monkeypatch=monkeypatch, model_set=model_set, obs=obs, family_book=fb)
    decision = engine.decide(
        case,
        space,
        snapshots={},
        portfolio=PortfolioExposureVector.flat(matrix, baseline=Decimal("1000")),
        matrix=matrix,
        captured_at_utc=_CAPTURED,
        sizing_candidates=sizing,
        max_stake_usd=Decimal("1000"),
        shares_for_routing=Decimal("100"),
    )

    assert decision.selected is not None
    selected_decision = next(
        d
        for d in decision.candidate_decisions
        if d.route.side == "YES" and d.route.bin_id == "b25"
    )
    assert decision.selected.candidate_id == selected_decision.economics.candidate_id
    assert selected_decision.route.side == "YES"
    assert selected_decision.route.bin_id == "b25"
    assert "modal" in calls, "the b25 YES candidate must reach the real q_lcb OOF guard"
    assert selected_decision.q_lcb_guard_basis == "INERT", (
        "b25 YES is not hard-fact-certified -- it must show the real (INERT, no "
        "artifact) OOF reliability guard verdict, not the Day0 hard-fact stamp"
    )
    assert selected_decision.q_lcb_guard_abstained is False
    assert selected_decision.selection_guard_basis == "INERT"
    assert selected_decision.selection_guard_abstained is False
    assert selected_decision.selection_guard_n == 0
    assert selected_decision.selection_guard_q_safe == pytest.approx(
        selected_decision.economics.payoff_q_lcb
    )
    assert 0.0 < selected_decision.economics.payoff_q_lcb < 1.0
    assert selected_decision.economics.edge_lcb > 0.0


def test_day0_non_hard_fact_boundary_yes_deflated_by_miscalibrated_qlcb_cell(monkeypatch):
    """RED-on-revert regression for the Wellington/Manila buy_yes point-bin losses.

    Wellington (position ``142ee1d2-688``) and Manila (``5e36a294-907``) were both
    Day0-active buy_yes POINT bins where the observed running extreme sat inside (not
    beyond) the bin -- ``is_hard_fact=False`` -- yet the family-scoped Day0 dispatch
    skipped ``_apply_qlcb_reliability_guard`` entirely and kept the raw
    FORECAST_BOOTSTRAP q_lcb (~0.96, ``q_lcb_guard_basis`` stamped as the Day0
    remaining-day basis, ``abstained=False``) with zero empirical check. This
    reproduces the same shape with the proven Tokyo b25 harness (a point bin the
    observed extreme sits exactly on the boundary of, without foreclosing it) and
    proves that once the fix routes it through ``_apply_qlcb_reliability_guard``, an
    injected miscalibrated OOF cell can now reach -- and kill -- the trade.

    Before the fix: the injected miscalibrated table is never consulted for a Day0
    candidate, so b25 YES keeps ``q_lcb_guard_basis == DAY0_REMAINING_DAY_Q_LCB`` and a
    positive edge -> RED (this assertion fails on unfixed code).
    After the fix: b25 YES is routed through the real OOF guard, which finds the
    injected cell and deflates its served q_lcb far below cost -> GREEN.
    """

    case = _case()
    space = _outcome_space(case)
    obs = Day0ObservationState(
        observed=True,
        station_id=STATION,
        source="wu_icao",
        samples_count=8,
        latest_observed_at_utc=_CAPTURED,
        observed_high_native=25.0,
        observed_low_native=None,
        observed_extreme_native=25.0,
        raw_observation_hash="day0-observed-boundary",
    )
    model_set = _model_set([24.9, 25.0, 25.1], case)
    pd = PredictiveDistributionBuilder(DebiasAuthority(())).build(
        case, model_set, obs, has_fusion_capture=True
    )
    jq = build_joint_q(pd, space)
    assert pd.day0.active is True
    assert forecast_bin_id(jq) == "b25"

    # A deliberately miscalibrated OOF table: every high|L1 YES cell (any q_lcb bucket,
    # either bin position, either precision class) has a deep (n=500 >= N_MIN) but very
    # LOW realized hit-rate (0.05). If `_apply_qlcb_guard` is honestly consulted for the
    # b25 YES candidate this must deflate its served q_lcb far below the route's cost,
    # regardless of which exact bucket the candidate's raw q_lcb lands in.
    bad_table: dict[str, tuple[int, float]] = {}
    for pos in ("modal", "nonmodal"):
        for precision in ("fine_nest", "coarse_global"):
            for qb in range(len(guard_mod.QLCB_BUCKET_EDGES) - 1):
                bad_table[f"high|L1|YES|{pos}|qb{qb}|{precision}"] = (500, 0.05)
    monkeypatch.setattr(guard_mod, "_RELIABILITY_CACHE", bad_table)
    monkeypatch.setattr(guard_mod, "_RELIABILITY_LOADED", True)

    def factory(bin_id: str) -> MarketBook:
        fair = min(max(jq.q_by_bin_id.get(bin_id, 0.0), 0.02), 0.98)
        ya = _tick(max(fair * 0.55, 0.002))
        yb = _tick(max(ya - 0.01, 0.001))
        return _market_book(
            bin_id,
            yes_bid=yb,
            yes_ask=ya,
            no_bid=_tick(1 - ya),
            no_ask=_tick(1 - yb),
            size=5000.0,
        )

    fb = _family_book(space, factory)
    matrix = _matrix(space)
    route_set = build_negrisk_route_set(
        fb, shares=Decimal("100"), enable_negrisk_routes=False
    )
    sizing: dict[tuple[str, str], NativeSideCandidate] = {}
    for b in space.bins:
        if not b.executable:
            continue
        route = route_set.direct_yes.get(b.bin_id)
        if route is None or not route.executable:
            continue
        q_point = float(jq.q_by_bin_id[b.bin_id])
        sizing[(b.bin_id, "YES")] = _yes_sizing(
            space,
            b.bin_id,
            q_point=q_point,
            q_lcb=max(q_point - 0.03, 0.0),
            price=str(round(float(route.avg_cost.value), 3)),
        )

    engine = _engine(monkeypatch=monkeypatch, model_set=model_set, obs=obs, family_book=fb)
    decision = engine.decide(
        case,
        space,
        snapshots={},
        portfolio=PortfolioExposureVector.flat(matrix, baseline=Decimal("1000")),
        matrix=matrix,
        captured_at_utc=_CAPTURED,
        sizing_candidates=sizing,
        max_stake_usd=Decimal("1000"),
        shares_for_routing=Decimal("100"),
    )

    b25_yes = next(
        d
        for d in decision.candidate_decisions
        if d.route.side == "YES" and d.route.bin_id == "b25"
    )
    assert b25_yes.q_lcb_guard_basis != fde_mod.DAY0_REMAINING_DAY_GUARD_BASIS, (
        "b25 YES is not hard-fact-certified (the observed extreme merely touches its "
        "boundary, it does not foreclose it) -- it must not keep the raw Day0 "
        "remaining-day passthrough"
    )
    assert b25_yes.q_lcb_guard_basis == "OOF_WILSON_95", (
        "the injected miscalibrated OOF cell must be the one actually consulted"
    )
    assert b25_yes.economics.edge_lcb <= 0.0, (
        "a miscalibrated OOF cell must deflate the raw q_lcb below the route's cost -- "
        "b25 YES must not keep a positive edge from its raw, unchecked q_lcb"
    )
    assert (
        decision.selected is None
        or decision.selected.candidate_id != b25_yes.economics.candidate_id
    ), "b25 YES must not be selected once its edge is honestly deflated by the OOF guard"


def test_day0_hard_fact_certain_candidate_still_skips_qlcb_reliability_guard(monkeypatch):
    """PRESERVE: a genuinely-foreclosed Day0 candidate keeps its hard-fact grant.

    A running HIGH max that has already passed a finite bin's upper edge makes NO on
    that bin a monotone hard fact (``is_hard_fact=True``, ``yes_hard=0.0`` ->
    ``q_safe=1.0`` for NO) -- the same monotone-foreclosure family as an
    already-entered open shoulder. That grant must NOT be routed through the q_lcb
    empirical OOF reliability guard; it earned the hard-fact exemption and keeps the
    current recompute path unchanged.
    """

    case = _case()
    space = _outcome_space(case)
    obs = Day0ObservationState(
        observed=True,
        station_id=STATION,
        source="wu_icao",
        samples_count=8,
        latest_observed_at_utc=_CAPTURED,
        # Already PAST b24's upper edge (24.0): a running HIGH max can only rise, so
        # NO on b24 ("exactly 24C") is a monotone hard fact, not a remaining-day guess.
        observed_high_native=25.0,
        observed_low_native=None,
        observed_extreme_native=25.0,
        raw_observation_hash="day0-observed-already-passed",
    )
    model_set = _model_set([24.9, 25.0, 25.1], case)
    pd = PredictiveDistributionBuilder(DebiasAuthority(())).build(
        case, model_set, obs, has_fusion_capture=True
    )
    jq = build_joint_q(pd, space)
    band = build_joint_q_band(pd, space, n_draws=_TEST_BAND_DRAWS, alpha=0.05)
    assert pd.day0.active is True
    assert forecast_bin_id(jq) == "b25"

    route = _hand_route(space, side="NO", bin_id="b24", cost=0.40)
    candidate = CandidateDecision(
        route=route,
        economics=CandidateEconomics(
            candidate_id=route.candidate_id,
            point_ev=0.30,
            edge_lcb=0.20,
            delta_u_at_min=0.01,
            optimal_stake_usd=Decimal("50"),
            optimal_delta_u=0.05,
            q_dot_payoff=0.60,
            cost=route.route_cost.avg_cost,
            route_id=route.route_cost.route_id,
            payoff_q_lcb=0.60,
        ),
        direction_law_ok=True,
        coherence_allows=True,
        robust_trade_score=0.20,
    )

    def _guard_must_not_run(**_kwargs):
        raise AssertionError(
            "the q_lcb reliability guard must not run for a hard-fact-certified "
            "Day0 candidate"
        )

    monkeypatch.setattr(fde_mod, "_apply_qlcb_guard", _guard_must_not_run)

    engine = FamilyDecisionEngine(
        fresh_model_reader=_FreshModelReader(model_set),
        day0_reader=_Day0Reader(obs),
        predictive_builder=_PredictiveBuilder(DebiasAuthority(())),
    )
    matrix = _matrix(space)
    exposure = PortfolioExposureVector.flat(matrix, baseline=Decimal("1000"))
    guarded = engine._apply_day0_observed_boundary_guard(
        scored=(candidate,),
        case=case,
        predictive=pd,
        omega=space,
        joint_q=jq,
        band=band,
        forecast_bin="b25",
        matrix=matrix,
        exposure=exposure,
        sizing_candidates={
            ("b24", "NO"): _no_sizing(space, "b24", q_point=0.999, q_lcb=0.999, price="0.40"),
        },
        max_stake_usd=Decimal("1000"),
    )

    result = guarded[0]
    assert result.q_lcb_guard_basis == fde_mod.DAY0_REMAINING_DAY_GUARD_BASIS
    assert result.q_lcb_guard_abstained is False
    assert result.economics.payoff_q_lcb == pytest.approx(1.0)
    assert result.economics.edge_lcb > 0.0
    assert result.selection_guard_basis == fde_mod.DAY0_REMAINING_DAY_GUARD_BASIS
    assert result.selection_guard_abstained is False
    assert result.selection_guard_q_safe == pytest.approx(1.0)


def test_day0_hard_fact_reuses_already_certain_economics(monkeypatch):
    case = _case()
    space = _outcome_space(case)
    obs = Day0ObservationState(
        observed=True,
        station_id=STATION,
        source="wu_icao",
        samples_count=8,
        latest_observed_at_utc=_CAPTURED,
        observed_high_native=25.0,
        observed_low_native=None,
        observed_extreme_native=25.0,
        raw_observation_hash="day0-observed-already-passed",
    )
    model_set = _model_set([24.9, 25.0, 25.1], case)
    pd = PredictiveDistributionBuilder(DebiasAuthority(())).build(
        case, model_set, obs, has_fusion_capture=True
    )
    jq = build_joint_q(pd, space)
    band = build_joint_q_band(pd, space, n_draws=_TEST_BAND_DRAWS, alpha=0.05)
    route = _hand_route(space, side="NO", bin_id="b24", cost=0.40)
    certain = CandidateDecision(
        route=route,
        economics=CandidateEconomics(
            candidate_id=route.candidate_id,
            point_ev=0.60,
            edge_lcb=0.60,
            delta_u_at_min=0.01,
            optimal_stake_usd=Decimal("50"),
            optimal_delta_u=0.05,
            q_dot_payoff=1.0,
            cost=route.route_cost.avg_cost,
            route_id=route.route_cost.route_id,
            payoff_q_lcb=1.0,
        ),
        direction_law_ok=True,
        coherence_allows=True,
        robust_trade_score=0.60,
    )

    monkeypatch.setattr(
        fde_mod,
        "compute_candidate_economics",
        lambda *_args, **_kwargs: pytest.fail(
            "already-certain hard-fact economics must not be sized twice"
        ),
    )
    engine = FamilyDecisionEngine(
        fresh_model_reader=_FreshModelReader(model_set),
        day0_reader=_Day0Reader(obs),
        predictive_builder=_PredictiveBuilder(DebiasAuthority(())),
    )
    matrix = _matrix(space)
    result = engine._apply_day0_observed_boundary_guard(
        scored=(certain,),
        case=case,
        predictive=pd,
        omega=space,
        joint_q=jq,
        band=band,
        forecast_bin="b25",
        matrix=matrix,
        exposure=PortfolioExposureVector.flat(matrix, baseline=Decimal("1000")),
        sizing_candidates={
            ("b24", "NO"): _no_sizing(
                space,
                "b24",
                q_point=0.999,
                q_lcb=0.999,
                price="0.40",
            ),
        },
        max_stake_usd=Decimal("1000"),
    )[0]

    assert result.economics.optimal_stake_usd == Decimal("50")
    assert result.economics.optimal_delta_u == pytest.approx(0.05)
    assert result.economics.payoff_q_lcb == pytest.approx(1.0)
    assert result.q_lcb_guard_cell_key == "day0_monotone_hard_fact_q_lcb"


def test_symmetric_center_yes_dominance_replaces_inferior_selected_no():
    """Shanghai correction mirror: an inferior selected NO yields to dominant center YES."""
    case = _case()
    space = _outcome_space(case)
    selected_no = _hand_decision(
        _hand_route(space, side="NO", bin_id="b24", cost=0.80),
        edge_lcb=0.04,
        optimal_delta_u=0.05,
        delta_u_at_min=0.01,
        robust_trade_score=0.90,
        optimal_stake_usd=Decimal("50"),
    )
    center_yes = _hand_decision(
        _hand_route(space, side="YES", bin_id="b25", cost=0.27),
        edge_lcb=0.08,
        optimal_delta_u=0.08,
        delta_u_at_min=0.01,
        robust_trade_score=0.10,
        optimal_stake_usd=Decimal("5"),
    )
    engine = FamilyDecisionEngine(
        fresh_model_reader=_FreshModelReader(_model_set([25.0], case)),
        day0_reader=_Day0Reader(_no_obs()),
        predictive_builder=_PredictiveBuilder(DebiasAuthority(())),
    )

    selected = engine._apply_symmetric_center_yes_dominance(
        selected_decision=selected_no,
        scored=[selected_no, center_yes],
        forecast_bin="b25",
    )

    assert selected is center_yes


def test_symmetric_center_yes_dominance_does_not_force_weaker_yes():
    case = _case()
    space = _outcome_space(case)
    selected_no = _hand_decision(
        _hand_route(space, side="NO", bin_id="b24", cost=0.40),
        edge_lcb=0.12,
        optimal_delta_u=0.08,
        delta_u_at_min=0.01,
        robust_trade_score=0.50,
        optimal_stake_usd=Decimal("5"),
    )
    weak_center_yes = _hand_decision(
        _hand_route(space, side="YES", bin_id="b25", cost=0.27),
        edge_lcb=0.02,
        optimal_delta_u=0.01,
        delta_u_at_min=0.01,
        robust_trade_score=0.10,
        optimal_stake_usd=Decimal("5"),
    )
    engine = FamilyDecisionEngine(
        fresh_model_reader=_FreshModelReader(_model_set([25.0], case)),
        day0_reader=_Day0Reader(_no_obs()),
        predictive_builder=_PredictiveBuilder(DebiasAuthority(())),
    )

    selected = engine._apply_symmetric_center_yes_dominance(
        selected_decision=selected_no,
        scored=[selected_no, weak_center_yes],
        forecast_bin="b25",
    )

    assert selected is selected_no


def test_modal_yes_missing_empirical_authority_is_candidate_local_not_family_veto():
    """A weak modal YES does not get re-expressed, but it also cannot veto live selection.

    Shanghai/Munich class failure: the modal YES has positive point value but its
    empirical guard is missing/thin. That candidate remains blocked by its own
    economics/guards, while a separate executable live candidate must still flow
    through selection rather than a family-level no-trade alias.
    """

    case = _case()
    space = _outcome_space(case)
    selected_no = _hand_decision(
        _hand_route(space, side="NO", bin_id="b24", cost=0.79),
        edge_lcb=0.02,
        optimal_delta_u=0.10,
        delta_u_at_min=0.01,
        robust_trade_score=0.20,
        optimal_stake_usd=Decimal("20"),
    )
    modal_yes = _hand_decision(
        _hand_route(space, side="YES", bin_id="b25", cost=0.27),
        edge_lcb=-0.27,
        optimal_delta_u=0.0,
        delta_u_at_min=0.0,
        robust_trade_score=0.53,
        optimal_stake_usd=Decimal("0"),
        selection_guard_basis="ACTIVE_MISSING_CELL",
        selection_guard_abstained=True,
        selection_guard_n=0,
        selection_guard_q_safe=0.0,
        chosen_stake_point_ev=0.53,
    )
    engine = FamilyDecisionEngine(
        fresh_model_reader=_FreshModelReader(_model_set([25.0], case)),
        day0_reader=_Day0Reader(_no_obs()),
        predictive_builder=_PredictiveBuilder(DebiasAuthority(())),
    )

    selected, reason = engine._select([selected_no, modal_yes])

    assert selected is selected_no
    assert reason is None
    assert modal_yes.selection_guard_basis == "ACTIVE_MISSING_CELL"
    assert modal_yes.selection_guard_abstained is True


def test_modal_yes_guard_receipt_does_not_block_independent_no():
    """Modal-YES guard evidence is receipt context, not a family-level NO ban."""

    case = _case()
    space = _outcome_space(case)
    selected_no = _hand_decision(
        _hand_route(space, side="NO", bin_id="b24", cost=0.79),
        edge_lcb=0.02,
        optimal_delta_u=0.10,
        delta_u_at_min=0.01,
        robust_trade_score=0.20,
        optimal_stake_usd=Decimal("20"),
    )
    licensed_modal_yes = _hand_decision(
        _hand_route(space, side="YES", bin_id="b25", cost=0.27),
        edge_lcb=0.01,
        optimal_delta_u=0.01,
        delta_u_at_min=0.001,
        robust_trade_score=0.28,
        optimal_stake_usd=Decimal("5"),
        selection_guard_basis="SELECTION_BETA_95",
        selection_guard_abstained=False,
        selection_guard_n=80,
        selection_guard_q_safe=0.28,
    )
    engine = FamilyDecisionEngine(
        fresh_model_reader=_FreshModelReader(_model_set([25.0], case)),
        day0_reader=_Day0Reader(_no_obs()),
        predictive_builder=_PredictiveBuilder(DebiasAuthority(())),
    )

    selected, reason = engine._select([selected_no, licensed_modal_yes])

    assert selected is selected_no
    assert reason is None


def test_modal_yes_family_veto_alias_is_not_live_contract():
    assert not hasattr(fde_mod, "NO_TRADE_MODAL_YES_EMPIRICAL_AUTHORITY_MISSING")
    assert not hasattr(FamilyDecisionEngine, "_apply_modal_yes_empirical_authority_invariant")


def test_center_yes_canonicalizes_adjacent_no_pair_equivalent_upside():
    """Shanghai correction: choose the cheaper center YES for the same upside.

    The selected single NO can have higher apparent utility density, but the
    family expression formed by the two adjacent NOs is a guaranteed floor plus
    the same center-bin upside as BUY_YES. If that expression ties up more
    capital and has no better edge density, the optimizer should canonicalize to
    center YES instead of letting repeated cycles assemble the costly NO pair.
    """
    case = _case()
    space = _outcome_space(case)
    selected_no = _hand_decision(
        _hand_route(space, side="NO", bin_id="b24", cost=0.79),
        edge_lcb=0.11,
        optimal_delta_u=0.20,
        delta_u_at_min=0.01,
        robust_trade_score=0.90,
        optimal_stake_usd=Decimal("5"),
    )
    sibling_no = _hand_decision(
        _hand_route(space, side="NO", bin_id="b26", cost=0.80),
        edge_lcb=0.10,
        optimal_delta_u=0.02,
        delta_u_at_min=0.01,
        robust_trade_score=0.80,
        optimal_stake_usd=Decimal("5"),
    )
    center_yes = _hand_decision(
        _hand_route(space, side="YES", bin_id="b25", cost=0.27),
        edge_lcb=0.53,
        optimal_delta_u=0.10,
        delta_u_at_min=0.01,
        robust_trade_score=0.53,
        optimal_stake_usd=Decimal("5"),
    )
    engine = FamilyDecisionEngine(
        fresh_model_reader=_FreshModelReader(_model_set([25.0], case)),
        day0_reader=_Day0Reader(_no_obs()),
        predictive_builder=_PredictiveBuilder(DebiasAuthority(())),
    )

    selected = engine._apply_symmetric_center_yes_dominance(
        selected_decision=selected_no,
        scored=[selected_no, center_yes, sibling_no],
        forecast_bin="b25",
    )

    assert selected is center_yes
    pair_payoff = selected_no.route.payoff_vector + sibling_no.route.payoff_vector
    assert np.all(pair_payoff - np.min(pair_payoff) >= center_yes.route.payoff_vector)
    assert float(center_yes.economics.cost.value) < (
        float(selected_no.economics.cost.value) + float(sibling_no.economics.cost.value)
    )


def test_center_yes_dominance_uses_full_outcome_space_not_scored_subset_adjacency():
    """Missing executable routes must not compress non-adjacent NOs into adjacent bins."""
    case = _case()
    space = _outcome_space(case)
    selected_no = _hand_decision(
        _hand_route(space, side="NO", bin_id="b23", cost=0.79),
        edge_lcb=0.11,
        optimal_delta_u=0.20,
        delta_u_at_min=0.01,
        robust_trade_score=0.90,
        optimal_stake_usd=Decimal("5"),
    )
    sibling_no = _hand_decision(
        _hand_route(space, side="NO", bin_id="b27", cost=0.80),
        edge_lcb=0.10,
        optimal_delta_u=0.02,
        delta_u_at_min=0.01,
        robust_trade_score=0.80,
        optimal_stake_usd=Decimal("5"),
    )
    center_yes = _hand_decision(
        _hand_route(space, side="YES", bin_id="b25", cost=0.27),
        edge_lcb=0.53,
        optimal_delta_u=0.10,
        delta_u_at_min=0.01,
        robust_trade_score=0.53,
        optimal_stake_usd=Decimal("5"),
    )
    engine = FamilyDecisionEngine(
        fresh_model_reader=_FreshModelReader(_model_set([25.0], case)),
        day0_reader=_Day0Reader(_no_obs()),
        predictive_builder=_PredictiveBuilder(DebiasAuthority(())),
    )

    selected = engine._apply_symmetric_center_yes_dominance(
        selected_decision=selected_no,
        scored=[selected_no, center_yes, sibling_no],
        forecast_bin="b25",
        outcome_bin_ids=[b.bin_id for b in space.bins],
    )

    assert selected is selected_no


def test_select_utility_density_objective_is_explicit_non_default(monkeypatch):
    """The density-first objective only applies when explicitly requested."""
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
        edge_lcb=0.38,
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
    total_engine = FamilyDecisionEngine(
        fresh_model_reader=_FreshModelReader(_model_set([25.0], case)),
        day0_reader=_Day0Reader(_no_obs()),
        predictive_builder=_PredictiveBuilder(DebiasAuthority(())),
        selection_objective="total_delta_u",
    )
    density_engine = FamilyDecisionEngine(
        fresh_model_reader=_FreshModelReader(_model_set([25.0], case)),
        day0_reader=_Day0Reader(_no_obs()),
        predictive_builder=_PredictiveBuilder(DebiasAuthority(())),
        selection_objective="utility_density",
    )

    default_selected, default_reason = default_engine._select([high_total, high_density])
    total_selected, total_reason = total_engine._select([high_total, high_density])
    density_selected, density_reason = density_engine._select([high_total, high_density])

    assert default_reason is None
    assert total_reason is None
    assert density_reason is None
    assert default_selected is high_density
    assert total_selected is high_total
    assert density_selected is high_density


def test_select_rejects_unknown_selection_objective():
    with pytest.raises(ValueError, match="unknown selection_objective"):
        FamilyDecisionEngine(
            fresh_model_reader=_FreshModelReader(_model_set([25.0], _case())),
            day0_reader=_Day0Reader(_no_obs()),
            predictive_builder=_PredictiveBuilder(DebiasAuthority(())),
            selection_objective="scalar_score",  # type: ignore[arg-type]
        )


def test_adjacent_no_pair_comparator_does_not_block_capital_efficient_center_yes(monkeypatch):
    """Shanghai correction: compare adjacent NO pair, but do not blindly prefer it."""
    case = _case()
    space = _outcome_space(case)
    joint_q, band = _hand_joint_q_and_band(
        space,
        {"b24": 0.10, "b25": 0.80, "b26": 0.10},
    )
    selected = _hand_decision(
        _hand_route(space, side="YES", bin_id="b25", cost=0.27),
        edge_lcb=0.80 - 0.27,
        optimal_delta_u=0.10,
        delta_u_at_min=0.01,
        robust_trade_score=0.53,
        optimal_stake_usd=Decimal("5"),
    )
    no24 = _hand_decision(
        _hand_route(space, side="NO", bin_id="b24", cost=0.79),
        edge_lcb=0.90 - 0.79,
        optimal_delta_u=0.01,
        delta_u_at_min=0.01,
        robust_trade_score=0.11,
    )
    no26 = _hand_decision(
        _hand_route(space, side="NO", bin_id="b26", cost=0.80),
        edge_lcb=0.90 - 0.80,
        optimal_delta_u=0.01,
        delta_u_at_min=0.01,
        robust_trade_score=0.10,
    )
    engine = FamilyDecisionEngine(
        fresh_model_reader=_FreshModelReader(_model_set([25.0], case)),
        day0_reader=_Day0Reader(_no_obs()),
        predictive_builder=_PredictiveBuilder(DebiasAuthority(())),
    )

    comparisons = engine._portfolio_comparisons(
        selected_decision=selected,
        scored=(selected, no24, no26),
        joint_q=joint_q,
        band=band,
        forecast_bin="b25",
    )

    assert len(comparisons) == 1
    assert comparisons[0].portfolio_type == "ADJACENT_NO_PAIR"
    assert comparisons[0].dominates_selected is False


def test_adjacent_no_pair_dominance_is_visible_as_non_executable_superior_route(monkeypatch):
    """If a non-executable portfolio is superior, the engine records that evidence."""
    case = _case()
    space = _outcome_space(case)
    joint_q, band = _hand_joint_q_and_band(
        space,
        {"b24": 0.10, "b25": 0.80, "b26": 0.10},
    )
    selected = _hand_decision(
        _hand_route(space, side="YES", bin_id="b25", cost=0.79),
        edge_lcb=0.80 - 0.79,
        optimal_delta_u=0.10,
        delta_u_at_min=0.01,
        robust_trade_score=0.01,
        optimal_stake_usd=Decimal("20"),
    )
    no24 = _hand_decision(
        _hand_route(space, side="NO", bin_id="b24", cost=0.30),
        edge_lcb=0.90 - 0.30,
        optimal_delta_u=0.10,
        delta_u_at_min=0.01,
        robust_trade_score=0.60,
    )
    no26 = _hand_decision(
        _hand_route(space, side="NO", bin_id="b26", cost=0.30),
        edge_lcb=0.90 - 0.30,
        optimal_delta_u=0.10,
        delta_u_at_min=0.01,
        robust_trade_score=0.60,
    )
    engine = FamilyDecisionEngine(
        fresh_model_reader=_FreshModelReader(_model_set([25.0], case)),
        day0_reader=_Day0Reader(_no_obs()),
        predictive_builder=_PredictiveBuilder(DebiasAuthority(())),
    )

    comparisons = engine._portfolio_comparisons(
        selected_decision=selected,
        scored=(selected, no24, no26),
        joint_q=joint_q,
        band=band,
        forecast_bin="b25",
    )

    assert len(comparisons) == 1
    comparison = comparisons[0]
    assert comparison.dominates_selected is True
    assert comparison.edge_lcb_density > comparison.selected_edge_lcb_density
    assert comparison.point_ev_density > comparison.selected_point_ev_density
    assert comparison.leg_candidate_ids == (no24.economics.candidate_id, no26.economics.candidate_id)


def test_adjacent_no_pair_dominance_is_telemetry_until_portfolio_executor_exists():
    """A hypothetical better multi-leg route must not veto an executable live leg."""

    source = inspect.getsource(FamilyDecisionEngine.decide)

    assert "portfolio_comparisons = self._portfolio_comparisons" in source
    assert "SUPERIOR_PORTFOLIO_ROUTE_NOT_EXECUTABLE" not in source
    assert "NO_TRADE_SUPERIOR_PORTFOLIO_ROUTE_NOT_EXECUTABLE" not in source


def test_portfolio_dominance_receipt_does_not_no_trade_selected_live_leg(monkeypatch):
    """Even a dominating non-executable portfolio is telemetry, not submit authority."""

    case = _case()
    space = _outcome_space(case)
    model_set = _model_set([24.6, 25.0, 25.4], case)
    pd = PredictiveDistributionBuilder(DebiasAuthority(())).build(
        case, model_set, _no_obs(), has_fusion_capture=True
    )
    jq = build_joint_q(pd, space)

    def factory(bin_id: str) -> MarketBook:
        fair = min(max(jq.q_by_bin_id.get(bin_id, 0.0), 0.02), 0.98)
        ya = _tick(max(fair * 0.5, 0.002))
        yb = _tick(max(ya - 0.01, 0.001))
        return _market_book(
            bin_id,
            yes_bid=yb,
            yes_ask=ya,
            no_bid=_tick(1 - ya),
            no_ask=_tick(1 - yb),
            size=5000.0,
        )

    fb = _family_book(space, factory)
    matrix = _matrix(space)
    exposure = PortfolioExposureVector.flat(matrix, baseline=Decimal("1000"))
    route_set = build_negrisk_route_set(fb, shares=Decimal("100"), enable_negrisk_routes=False)
    sizing: dict[tuple[str, str], NativeSideCandidate] = {}
    for b in space.bins:
        if not b.executable:
            continue
        t = b.bin_id
        yr = route_set.direct_yes.get(t)
        if yr is not None and yr.executable:
            qb = jq.q_by_bin_id[t]
            sizing[(t, "YES")] = _yes_sizing(
                space,
                t,
                q_point=float(qb),
                q_lcb=max(float(qb) - 0.03, 0.0),
                price=str(round(float(yr.avg_cost.value), 3)),
            )

    forced_comparison = PortfolioCandidateDecision(
        portfolio_type="ADJACENT_NO_PAIR",
        reference_bin_id="b25",
        leg_candidate_ids=("left", "right"),
        leg_route_ids=("left-route", "right-route"),
        payoff_vector_hash="forced",
        point_ev=1.0,
        edge_lcb=1.0,
        q_dot_payoff=1.5,
        cost_sum=0.1,
        edge_lcb_density=10.0,
        point_ev_density=10.0,
        selected_candidate_id="selected",
        selected_edge_lcb_density=0.1,
        selected_point_ev_density=0.1,
        dominates_selected=True,
    )

    monkeypatch.setattr(
        FamilyDecisionEngine,
        "_portfolio_comparisons",
        lambda self, **kwargs: (forced_comparison,),
    )
    engine = _engine(monkeypatch=monkeypatch, model_set=model_set, obs=_no_obs(), family_book=fb)

    decision = engine.decide(
        case,
        space,
        snapshots={},
        portfolio=exposure,
        matrix=matrix,
        captured_at_utc=_CAPTURED,
        sizing_candidates=sizing,
        max_stake_usd=Decimal("1000"),
        shares_for_routing=Decimal("100"),
    )

    assert decision.no_trade_reason is None
    assert decision.selected is not None
    assert decision.portfolio_comparisons == (forced_comparison,)
    assert decision.portfolio_comparisons[0].dominates_selected is True


# ===========================================================================
# SPEC RED-on-revert #2: no_trade_reason present when no candidate passes.
# ===========================================================================

def test_no_trade_reason_present_when_no_candidate_passes(monkeypatch):
    """When nothing survives the chain, decide returns a no-trade FamilyDecision (not None).

    Build a family where EVERY route is priced so expensive that no candidate has a positive
    robust edge/DeltaU. The filter chain empties, so ``decide`` returns a
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
    # is purely the empty positive-utility set. Prices snap to the 0.001 tick grid.
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
    assert decision.no_trade_reason == NO_TRADE_NO_POSITIVE_UTILITY, decision.no_trade_reason
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
    assert y.direction_law_ok is True
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


def test_modal_yes_with_pooled_oof_reliability_can_license_market_coherence(monkeypatch):
    """Pooled-tail OOF evidence is live empirical evidence, not a second-class receipt.

    This pins the Shanghai-class failure mode: a center/modal YES can have valid same-side
    OOF support after sparse-bucket pooling, but the coherence layer used to accept only the
    exact ``OOF_WILSON_95`` basis. That left a model-superiority receipt stranded below the
    market-coherence gate and structurally favored NO routes. The pooled basis may license
    only a valid native side with positive guarded edge/Delta-U; it does not bypass
    q/payoff economics.
    """

    from src.decision.qlcb_reliability_guard import GuardVerdict

    case = _case()
    space = _outcome_space(case)
    model_set = _model_set([24.6, 25.0, 25.4], case)
    pd = PredictiveDistributionBuilder(DebiasAuthority(())).build(
        case, model_set, _no_obs(), has_fusion_capture=True
    )
    jq = build_joint_q(pd, space)
    assert forecast_bin_id(jq) == "b25"

    def factory(bin_id: str) -> MarketBook:
        if bin_id == "b25":
            return _market_book(
                bin_id,
                yes_bid=0.001,
                yes_ask=0.001,
                no_bid=0.999,
                no_ask=0.999,
                size=5000.0,
            )
        return _market_book(
            bin_id,
            yes_bid=0.090,
            yes_ask=0.100,
            no_bid=0.900,
            no_ask=0.910,
            size=5000.0,
        )

    def _pooled_tail_license(**kwargs):
        if kwargs["side"] != "YES" or kwargs["bin_position"] != "modal":
            return GuardVerdict(
                q_safe=0.0,
                trade=False,
                abstained=True,
                cell_key="test_non_target_abstain",
                L_g=0.0,
                n_g=0,
                bucket_floor=0.0,
                basis="OOF_WILSON_95_MISSING_CELL",
            )
        return GuardVerdict(
            q_safe=0.20,
            trade=True,
            abstained=False,
            cell_key="high|L1|YES|modal|qb10->tail_qb6+",
            L_g=0.20,
            n_g=64,
            bucket_floor=0.20,
            basis="OOF_WILSON_95_POOLED_TAIL",
        )

    monkeypatch.setattr(fde_mod, "_apply_qlcb_guard", _pooled_tail_license)
    fb = _family_book(space, factory)
    matrix = _matrix(space)
    exposure = PortfolioExposureVector.flat(matrix, baseline=Decimal("1000"))
    sizing = {
        ("b25", "YES"): _yes_sizing(
            space,
            "b25",
            q_point=float(jq.q_by_bin_id["b25"]),
            q_lcb=0.20,
            price="0.050",
        ),
    }

    engine = _engine(monkeypatch=monkeypatch, model_set=model_set, obs=_no_obs(), family_book=fb)
    decision = engine.decide(
        case,
        space,
        snapshots={},
        portfolio=exposure,
        matrix=matrix,
        captured_at_utc=_CAPTURED,
        sizing_candidates=sizing,
        max_stake_usd=Decimal("1000"),
        shares_for_routing=Decimal("100"),
    )

    assert decision.market_coherence is not None
    assert "b25" not in decision.market_coherence.offending_bins
    selected = decision.selected
    assert selected is not None
    selected_decision = next(
        d for d in decision.candidate_decisions if d.economics.candidate_id == selected.candidate_id
    )
    assert selected_decision.route.side == "YES"
    assert selected_decision.route.bin_id == "b25"
    assert selected_decision.direction_law_ok is True
    assert selected_decision.coherence_allows is True
    assert selected_decision.q_lcb_guard_basis == "OOF_WILSON_95_POOLED_TAIL"


# ===========================================================================
# SPEC RED-on-revert #4: rounded-mu direction heuristics cannot block economic alpha.
# ===========================================================================

def test_nonforecast_yes_and_modal_no_are_live_selectable_on_vector_economics():
    """Positive vector economics, not rounded-mu bin identity, control live admission.

    RED-on-revert: restoring the old rule (YES only on rounded forecast bin; NO only away
    from it) rejects both hand-built candidates below before the edge/DeltaU gate. That is
    the live buy-YES starvation / Shanghai-class failure: the selector refuses profitable
    Arrow-Debreu payoffs because the served center rounded somewhere else.
    """
    case = _case()
    space = _outcome_space(case)
    modal_bin_id = "b25"

    no_on_modal_route = _hand_route(space, side="NO", bin_id=modal_bin_id, cost=0.72)
    no_on_modal_cand = _hand_decision(
        route=no_on_modal_route,
        edge_lcb=0.08,
        optimal_delta_u=0.05,
        delta_u_at_min=0.001,
        robust_trade_score=0.08,
        direction_law_ok=direction_law_ok(no_on_modal_route, forecast_bin=modal_bin_id),
    )

    engine = FamilyDecisionEngine(
        fresh_model_reader=_FreshModelReader(_model_set([25.0], case)),
        day0_reader=_Day0Reader(_no_obs()),
        predictive_builder=_PredictiveBuilder(DebiasAuthority(())),
    )

    selected, reason = engine._select([no_on_modal_cand])
    assert selected is no_on_modal_cand
    assert reason is None

    non_modal_bin_id = "b24"
    yes_on_non_modal_route = _hand_route(space, side="YES", bin_id=non_modal_bin_id, cost=0.30)
    yes_on_non_modal_cand = _hand_decision(
        yes_on_non_modal_route,
        edge_lcb=0.10,
        optimal_delta_u=0.06,
        delta_u_at_min=0.001,
        robust_trade_score=0.10,
        direction_law_ok=direction_law_ok(yes_on_non_modal_route, forecast_bin=modal_bin_id),
    )

    selected2, reason2 = engine._select([yes_on_non_modal_cand])
    assert selected2 is yes_on_non_modal_cand
    assert reason2 is None

    selected3, reason3 = engine._select([no_on_modal_cand, yes_on_non_modal_cand])
    assert selected3 is yes_on_non_modal_cand
    assert reason3 is None
    assert engine._direction_admitted(yes_on_non_modal_cand) is True
    assert engine._direction_admitted(no_on_modal_cand) is True


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
        payoff_q_lcb=0.50,
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
    assert 0.0 < guarded_economics.edge_lcb < economics.edge_lcb
    assert guarded_economics.delta_u_at_min > 0.0
    assert guarded_economics.optimal_delta_u > 0.0
    assert guarded_economics.optimal_stake_usd > Decimal("0")

    selected, reason = engine._select(guarded)
    assert reason is None
    assert selected is not None
    assert selected.economics.edge_lcb == pytest.approx(guarded_economics.edge_lcb)


def test_selection_calibrator_blocks_toxic_no_before_roi_selection(monkeypatch):
    """Selection-aware guard feeds qkernel economics before choosing the ROI frontier."""

    case = _case()
    space = _outcome_space(case)
    toxic_no = _hand_decision(
        _hand_route(space, side="NO", bin_id="b24", cost=0.79),
        edge_lcb=0.10,
        optimal_delta_u=0.20,
        delta_u_at_min=0.01,
        robust_trade_score=0.50,
        optimal_stake_usd=Decimal("20"),
    )
    center_yes = _hand_decision(
        _hand_route(space, side="YES", bin_id="b25", cost=0.27),
        edge_lcb=0.08,
        optimal_delta_u=0.08,
        delta_u_at_min=0.01,
        robust_trade_score=0.10,
        optimal_stake_usd=Decimal("5"),
    )

    def _selection_verdict(**kwargs):
        side = kwargs["side"]
        if side == "NO":
            return SimpleNamespace(
                q_safe=0.0,
                trade=False,
                abstained=True,
                cell_key="NO|L1|nonmodal|0.85",
                L_g=0.0,
                n_g=12,
                basis="EB_THIN_SELECTED",
            )
        return SimpleNamespace(
            q_safe=0.35,
            trade=True,
            abstained=False,
            cell_key="YES|L1|modal|pb10",
            L_g=0.35,
            n_g=80,
            basis="SELECTION_BETA_95",
        )

    monkeypatch.setattr(fde_mod, "apply_selection_calibrator", _selection_verdict)
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
    guarded = engine._apply_selection_calibrator_guard(
        scored=(toxic_no, center_yes),
        case=case,
        joint_q=jq,
        band=build_joint_q_band(pd, space, n_draws=_TEST_BAND_DRAWS, alpha=0.05),
        forecast_bin="b25",
        matrix=matrix,
        exposure=PortfolioExposureVector.flat(matrix, baseline=Decimal("1000")),
        sizing_candidates={
            ("b24", "NO"): _no_sizing(space, "b24", q_point=0.85, q_lcb=0.84, price="0.79"),
            ("b25", "YES"): _yes_sizing(space, "b25", q_point=0.50, q_lcb=0.35, price="0.27"),
        },
        max_stake_usd=Decimal("100"),
    )

    blocked_no = next(d for d in guarded if d.route.side == "NO")
    passed_yes = next(d for d in guarded if d.route.side == "YES")
    assert blocked_no.selection_guard_basis == "EB_THIN_SELECTED"
    assert blocked_no.selection_guard_abstained is True
    assert blocked_no.economics.edge_lcb < 0.0
    assert blocked_no.economics.optimal_delta_u <= 0.0
    assert passed_yes.selection_guard_basis == "SELECTION_BETA_95"

    selected, reason = engine._select(guarded)
    assert reason is None
    assert selected is passed_yes


def test_selection_calibrator_metric_scope_blocks_low_modal_yes(monkeypatch):
    """A HIGH-only selection artifact must fail closed on the LOW money path."""

    case = _case("low")
    space = _outcome_space(case)
    modal_yes = _hand_decision(
        _hand_route(space, side="YES", bin_id="b25", cost=0.27),
        edge_lcb=0.08,
        optimal_delta_u=0.08,
        delta_u_at_min=0.01,
        robust_trade_score=0.10,
        optimal_stake_usd=Decimal("5"),
    )

    def _selection_verdict(**kwargs):
        assert kwargs["temperature_metric"] == "low"
        return SimpleNamespace(
            q_safe=0.0,
            trade=False,
            abstained=True,
            cell_key="YES|L1|modal|pb10",
            L_g=0.0,
            n_g=0,
            basis="METRIC_NOT_ARMED",
        )

    monkeypatch.setattr(fde_mod, "apply_selection_calibrator", _selection_verdict)
    engine = FamilyDecisionEngine(
        fresh_model_reader=_FreshModelReader(_model_set([25.0], case)),
        day0_reader=_Day0Reader(_no_obs()),
        predictive_builder=_PredictiveBuilder(DebiasAuthority(())),
    )
    predictive = PredictiveDistributionBuilder(DebiasAuthority(())).build(
        case, _model_set([25.0], case), _no_obs(), has_fusion_capture=True
    )
    joint_q = build_joint_q(predictive, space)
    matrix = _matrix(space)

    guarded = engine._apply_selection_calibrator_guard(
        scored=(modal_yes,),
        case=case,
        joint_q=joint_q,
        band=build_joint_q_band(
            predictive, space, n_draws=_TEST_BAND_DRAWS, alpha=0.05
        ),
        forecast_bin="b25",
        matrix=matrix,
        exposure=PortfolioExposureVector.flat(matrix, baseline=Decimal("1000")),
        sizing_candidates={
            ("b25", "YES"): _yes_sizing(
                space, "b25", q_point=0.50, q_lcb=0.35, price="0.27"
            )
        },
        max_stake_usd=Decimal("100"),
    )

    blocked = guarded[0]
    assert blocked.selection_guard_basis == "METRIC_NOT_ARMED"
    assert blocked.selection_guard_abstained is True
    assert blocked.selection_guard_q_safe == 0.0
    assert blocked.economics.edge_lcb < 0.0
    assert blocked.economics.optimal_delta_u <= 0.0


@pytest.mark.parametrize("selection_basis", ["SIDE_NOT_ARMED", "ACTIVE_MISSING_CELL", "ACTIVE_THIN_CELL", "EB_THIN_SELECTED"])
def test_selection_calibrator_blocks_unarmed_tail_yes_but_not_modal_yes(monkeypatch, selection_basis):
    """Selection-bias evidence must not create a closed loop that starves modal YES.

    Nonmodal YES tails still require selected-side evidence. The modal YES leg is
    the center-buy route already guarded by qkernel payoff bounds, OOF reliability,
    coherence, and the live strategy price floor, so lack of a selected-bias cell
    must not force it to non-positive edge before selection.
    """

    case = _case()
    space = _outcome_space(case)
    tail_yes = _hand_decision(
        _hand_route(space, side="YES", bin_id="b24", cost=0.03),
        edge_lcb=0.07,
        optimal_delta_u=0.08,
        delta_u_at_min=0.01,
        robust_trade_score=0.10,
        optimal_stake_usd=Decimal("5"),
    )
    modal_yes = _hand_decision(
        _hand_route(space, side="YES", bin_id="b25", cost=0.27),
        edge_lcb=0.05,
        optimal_delta_u=0.06,
        delta_u_at_min=0.01,
        robust_trade_score=0.08,
        optimal_stake_usd=Decimal("5"),
    )

    def _selection_verdict(**kwargs):
        return SimpleNamespace(
            q_safe=0.0,
            trade=False,
            abstained=True,
            cell_key=f"{kwargs['side']}|SIDE_NOT_ARMED",
            L_g=0.0,
            n_g=0,
            basis=selection_basis,
        )

    monkeypatch.setattr(fde_mod, "apply_selection_calibrator", _selection_verdict)
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
    guarded = engine._apply_selection_calibrator_guard(
        scored=(tail_yes, modal_yes),
        case=case,
        joint_q=jq,
        band=build_joint_q_band(pd, space, n_draws=_TEST_BAND_DRAWS, alpha=0.05),
        forecast_bin="b25",
        matrix=matrix,
        exposure=PortfolioExposureVector.flat(matrix, baseline=Decimal("1000")),
        sizing_candidates={
            ("b24", "YES"): _yes_sizing(space, "b24", q_point=0.20, q_lcb=0.10, price="0.03"),
            ("b25", "YES"): _yes_sizing(space, "b25", q_point=0.50, q_lcb=0.35, price="0.27"),
        },
        max_stake_usd=Decimal("100"),
    )

    blocked_tail = next(d for d in guarded if d.route.bin_id == "b24")
    passed_modal = next(d for d in guarded if d.route.bin_id == "b25")
    assert blocked_tail.selection_guard_basis == selection_basis
    assert blocked_tail.selection_guard_abstained is True
    assert blocked_tail.selection_guard_n == 0
    assert blocked_tail.selection_guard_q_safe == pytest.approx(0.0)
    assert blocked_tail.economics.edge_lcb < 0.0
    assert blocked_tail.economics.optimal_delta_u <= 0.0
    assert passed_modal.selection_guard_basis == "MODAL_YES_QKERNEL_OOF_GUARD"
    assert passed_modal.selection_guard_abstained is False
    assert passed_modal.selection_guard_cell_key.startswith(f"{selection_basis}:")
    assert passed_modal.selection_guard_q_safe == pytest.approx(0.32)
    assert passed_modal.economics.edge_lcb > 0.0
    assert passed_modal.economics.optimal_delta_u > 0.0

    selected, reason = engine._select(guarded)
    assert reason is None
    assert selected is passed_modal


def test_selection_calibrator_deflation_recomputes_qkernel_stake(monkeypatch):
    """A licensed selection bound lowers payoff_q_lcb and recomputes ROI inputs."""

    case = _case()
    space = _outcome_space(case)
    route = _hand_route(space, side="NO", bin_id="b24", cost=0.40)
    economics = CandidateEconomics(
        candidate_id=route.candidate_id,
        point_ev=0.30,
        edge_lcb=0.25,
        delta_u_at_min=0.001,
        optimal_stake_usd=Decimal("25"),
        optimal_delta_u=0.15,
        q_dot_payoff=0.70,
        cost=route.route_cost.avg_cost,
        route_id=route.route_cost.route_id,
        payoff_q_lcb=0.65,
    )
    candidate = CandidateDecision(
        route=route,
        economics=economics,
        direction_law_ok=True,
        coherence_allows=True,
        robust_trade_score=0.20,
    )

    def _licensed_selection(**kwargs):
        assert kwargs["raw_side_prob"] == pytest.approx(0.70)
        assert kwargs["admission_margin"] == pytest.approx(0.40)
        return SimpleNamespace(
            q_safe=0.50,
            trade=True,
            abstained=False,
            cell_key="NO|L1|nonmodal|0.70",
            L_g=0.50,
            n_g=80,
            basis="SELECTION_EB_BETA",
        )

    monkeypatch.setattr(fde_mod, "apply_selection_calibrator", _licensed_selection)
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
    guarded = engine._apply_selection_calibrator_guard(
        scored=(candidate,),
        case=case,
        joint_q=jq,
        band=build_joint_q_band(pd, space, n_draws=_TEST_BAND_DRAWS, alpha=0.05),
        forecast_bin="b25",
        matrix=matrix,
        exposure=PortfolioExposureVector.flat(matrix, baseline=Decimal("1000")),
        sizing_candidates={
            ("b24", "NO"): _no_sizing(space, "b24", q_point=0.70, q_lcb=0.65, price="0.40")
        },
        max_stake_usd=Decimal("100"),
    )

    guarded_candidate = guarded[0]
    assert guarded_candidate.selection_guard_basis == "SELECTION_EB_BETA"
    assert guarded_candidate.selection_guard_abstained is False
    assert guarded_candidate.selection_guard_n == 80
    assert guarded_candidate.selection_guard_q_safe == pytest.approx(0.50)
    assert guarded_candidate.economics.payoff_q_lcb == pytest.approx(0.50)
    assert guarded_candidate.economics.edge_lcb == pytest.approx(0.10)
    assert guarded_candidate.economics.optimal_stake_usd != Decimal("25")
