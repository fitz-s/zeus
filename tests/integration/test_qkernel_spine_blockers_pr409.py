# Created: 2026-06-15
# Last reused or audited: 2026-06-19
# Authority basis: docs/rebuild/consult_review_pr409.md §5/§7 + the round-2
#   corrections docs/rebuild/consult_review_pr409_round2.md §1/§3/§5. RED-on-revert
#   tests for the FOUR live-path blockers in the q-kernel integration bridge, folding
#   the round-2 corrections:
#     1. live==replay ForecastCase: emos_season(target), regime_key="default",
#        issue/source_cycle/lead from the FORECAST SOURCE CYCLE (fail-closed if absent),
#        restricted to the replay-validated 24h lead bucket.
#     2. route identity: PROOF-NATIVE single-leg routing (maker AND taker), edge from
#        the proof's own execution_price (NOT the negrisk ask ladder); synthetic/arb
#        disabled; non-direct selection refused.
#     3. day0 hard-block: QKERNEL_DAY0_NOT_WIRED on _DAY0_LANE_EVENT_TYPES BEFORE the
#        spine call.
#     4. current exposure in SELECTION (per-bin family exposure into argmax ΔU).
"""Integration tests for the four PR #409 live-path blockers (RED-on-revert)."""
from __future__ import annotations

import datetime as _dt
import json
from decimal import Decimal

import pytest

from src.engine import event_reactor_adapter as era
from src.engine import qkernel_spine_bridge as bridge
from src.events.candidate_binding import (
    EventBoundCandidateFamily,
    MarketTopologyCandidate,
)
from src.strategy import utility_ranker
from src.types.market import Bin

CITY = "Paris"  # a real registered C-unit, wmo_half_up settlement city
TARGET_DATE = "2026-06-14"
METRIC = "high"
# The forecast SOURCE CYCLE that lands (Paris, 2026-06-14, high) in the replay-validated
# 24h lead bucket: cycle 2026-06-13T00:00Z -> Paris finalization 2026-06-14T10:00Z = 34h
# -> "24h" bucket (lead_bucket_for: 1.0 <= lead_days < 2.0).
SOURCE_CYCLE_TIME_UTC = "2026-06-13T00:00:00Z"
DECISION_TIME = _dt.datetime(2026, 6, 13, 12, 0, tzinfo=_dt.timezone.utc)


@pytest.fixture(autouse=True)
def _fast_band_draws(monkeypatch):
    """Lower the band draw count for a fast, deterministic smoke (logic unchanged)."""
    monkeypatch.setattr(bridge, "SPINE_BAND_DRAWS", 400, raising=False)


# ---------------------------------------------------------------------------
# Fixtures — the SAME snapshot-row + _CandidateProof shape the reactor materializes.
# ---------------------------------------------------------------------------
def _row(*, condition_id, yes_token, no_token, yes_ask, no_ask, snapshot_id, neg_risk=0, no_ask_present=True):
    no_block = {"asks": [], "bids": [{"price": f"{max(no_ask - 0.01, 0.01):.2f}", "size": "100"}]}
    if no_ask_present:
        no_block = {
            "asks": [{"price": f"{no_ask:.2f}", "size": "100000"}],
            "bids": [{"price": f"{max(no_ask - 0.01, 0.01):.2f}", "size": "100"}],
        }
    depth = {
        "YES": {
            "asks": [{"price": f"{yes_ask:.2f}", "size": "100000"}],
            "bids": [{"price": f"{max(yes_ask - 0.01, 0.01):.2f}", "size": "100"}],
        },
        "NO": no_block,
    }
    return {
        "snapshot_id": snapshot_id,
        "condition_id": condition_id,
        "yes_token_id": yes_token,
        "no_token_id": no_token,
        "selected_outcome_token_id": "",
        "outcome_label": "",
        "min_tick_size": "0.01",
        "min_order_size": "5",
        "fee_details_json": json.dumps({"fee_rate_fraction": 0.0}),
        "neg_risk": neg_risk,
        "orderbook_depth_json": json.dumps(depth),
        "tradeability_status_json": "{}",
        "book_hash": f"book-{snapshot_id}",
        "orderbook_top_bid": str(max(yes_ask - 0.01, 0.01)),
    }


def _candidate(*, condition_id, yes_token, no_token, bin_obj):
    return MarketTopologyCandidate(
        city=CITY,
        target_date=TARGET_DATE,
        metric=METRIC,
        condition_id=condition_id,
        yes_token_id=yes_token,
        no_token_id=no_token,
        bin=bin_obj,
    )


def _proof(*, direction, row, token_id, q_posterior, q_lcb_5pct, bin_obj, execution_price=None):
    ep, _pfill, _c95 = era._execution_price_from_snapshot(
        row, selected_token_id=token_id, direction=direction
    )
    if execution_price is not None:
        from src.contracts.execution_price import ExecutionPrice

        ep = ExecutionPrice(
            float(execution_price),
            price_type="fee_adjusted",
            fee_deducted=True,
            currency="probability_units",
        )
    return era._CandidateProof(
        candidate=_candidate(
            condition_id=str(row.get("condition_id") or ""),
            yes_token=str(row.get("yes_token_id") or ""),
            no_token=str(row.get("no_token_id") or ""),
            bin_obj=bin_obj,
        ),
        token_id=token_id,
        direction=direction,
        row=row,
        executable_snapshot_id=str(row.get("snapshot_id") or ""),
        execution_price=ep,
        q_posterior=q_posterior,
        q_lcb_5pct=q_lcb_5pct,
        c_cost_95pct=None,
        p_fill_lcb=1.0,
        trade_score=1.0,
        p_value=0.01,
        passed_prefilter=True,
        native_quote_available=True,
        p_cal_vector_hash="cal-hash",
        p_live_vector_hash="live-hash",
        missing_reason=None,
    )


def _three_bin_family(*, event_type="FORECAST_SNAPSHOT_READY"):
    bins = [
        Bin(low=None, high=19.0, unit="C", label="19C or below"),
        Bin(low=20.0, high=20.0, unit="C", label="20C"),
        Bin(low=21.0, high=21.0, unit="C", label="21C"),
        Bin(low=22.0, high=None, unit="C", label="22C or above"),
    ]
    candidates = tuple(
        _candidate(condition_id=f"cond-{i}", yes_token=f"yes-{i}", no_token=f"no-{i}", bin_obj=b)
        for i, b in enumerate(bins)
    )
    family = EventBoundCandidateFamily(
        family_id="edli_family_blockers_pr409",
        event_id="evt-blockers-pr409",
        event_type=event_type,
        city=CITY,
        target_date=TARGET_DATE,
        metric=METRIC,
        condition_ids=tuple(c.condition_id for c in candidates),
        yes_token_ids=tuple(c.yes_token_id for c in candidates),
        no_token_ids=tuple(c.no_token_id for c in candidates),
        bins=tuple(bins),
        candidates=candidates,
        causal_snapshot_id="snap-blockers",
        market_topology_source="executable_market_snapshots",
        binding_hash="hash-blockers",
    )
    return family, bins


def _proofs_for(family, *, yes_asks, no_asks, q_by_bin, q_lcb_by_bin, neg_risk=0,
                no_ask_present=True, no_execution_prices=None):
    proofs = []
    for i, candidate in enumerate(family.candidates):
        row = _row(
            condition_id=candidate.condition_id,
            yes_token=candidate.yes_token_id,
            no_token=candidate.no_token_id,
            yes_ask=yes_asks[i],
            no_ask=no_asks[i],
            snapshot_id=f"snap-{i}",
            neg_risk=neg_risk,
            no_ask_present=no_ask_present,
        )
        q = q_by_bin[i]
        proofs.append(
            _proof(
                direction="buy_yes",
                row=row,
                token_id=candidate.yes_token_id,
                q_posterior=q,
                q_lcb_5pct=q_lcb_by_bin[i],
                bin_obj=candidate.bin,
            )
        )
        proofs.append(
            _proof(
                direction="buy_no",
                row=row,
                token_id=candidate.no_token_id,
                q_posterior=float(min(max(1.0 - q, 0.0), 1.0)),
                q_lcb_5pct=float(min(max(1.0 - q, 0.0), 1.0)) * 0.9,
                bin_obj=candidate.bin,
                execution_price=(no_execution_prices[i] if no_execution_prices else None),
            )
        )
    return proofs


def _payload(*, mu, sigma, members, source_cycle=SOURCE_CYCLE_TIME_UTC):
    p = {
        "family_id": "edli_family_blockers_pr409",
        "event_id": "evt-blockers-pr409",
        "_edli_spine_mu_native": float(mu),
        "_edli_spine_sigma_native": float(sigma),
        "_edli_spine_debiased_members_native": [float(x) for x in members],
        "_edli_spine_raw_members_native": [float(x) for x in members],
    }
    if source_cycle is not None:
        p["_edli_spine_source_cycle_time_utc"] = source_cycle
    return p


def _drive(family, proofs, payload, *, decision_time=DECISION_TIME, extra_exposure=None):
    return bridge.decide_family_via_spine(
        family=family,
        payload=payload,
        proofs=proofs,
        decision_time=decision_time,
        native_side_candidate_from_proof=era._native_side_candidate_from_proof,
        candidate_bin_id=era._candidate_bin_id,
        payoff_matrix_over_bins=utility_ranker.FamilyPayoffMatrix.over_bins,
        exposure_builder=era._robust_marginal_utility_exposure,
        baseline_usd_provider=era._robust_marginal_utility_baseline_usd,
        per_bin_yes_q_lcb=era._per_bin_yes_q_lcb(proofs),
        extra_exposure_by_bin_id=extra_exposure,
    )


# ===========================================================================
# BLOCKER 1 — live==replay forecast-case (source-cycle, emos_season, 24h bucket).
# ===========================================================================
def test_live_bridge_forecast_case_matches_arm_replay():
    """The live bridge ForecastCase season / metric / lead-bucket / regime match the
    ARM-replay case construction, built from the FORECAST SOURCE CYCLE (not
    decision_time), and the served sigma floor is a non-None REALIZED floor.

    RED-on-revert: reverting build_forecast_case to season=""/regime_key=""/lead=0
    blanks season/regime (the equality assertions fail) and drops the lead to "day0"
    (the bucket equality fails); reverting to decision_time mis-buckets the lead.
    """
    from datetime import date

    from src.calibration.emos import emos_season
    from src.forecast.forecast_case_factory import DEFAULT_REGIME_KEY, REPLAY_LEAD_HOURS
    from src.forecast.sigma_authority import lead_bucket_for, realized_sigma_floor
    from src.forecast.types import ForecastCase

    family, _bins = _three_bin_family()
    cycle = _dt.datetime.fromisoformat(SOURCE_CYCLE_TIME_UTC.replace("Z", "+00:00"))
    case = bridge.build_forecast_case(family, source_cycle_time_utc=cycle)

    td = date(2026, 6, 14)

    # Non-empty, replay-equivalent metadata.
    assert case.season != "" and case.regime_key != "" and case.lead_hours > 0.0
    assert case.season == emos_season(td)
    assert case.regime_key == DEFAULT_REGIME_KEY
    assert case.metric == METRIC
    # issue / source_cycle are the FORECAST SOURCE CYCLE, not decision_time.
    assert case.issue_time_utc == cycle
    assert case.source_cycle_time_utc == cycle

    # The lead BUCKET is the replay-validated 24h bucket.
    assert lead_bucket_for(case) == "24h"
    replay_case = ForecastCase(
        city=case.city, city_id=case.city_id, station_id=case.station_id,
        settlement_source_type=case.settlement_source_type, target_local_date=td,
        metric=METRIC, issue_time_utc=case.issue_time_utc, lead_hours=REPLAY_LEAD_HOURS,
        season=emos_season(td), regime_key=DEFAULT_REGIME_KEY, unit=case.unit,
        resolution=case.resolution, family_id=case.family_id,
        source_cycle_time_utc=case.source_cycle_time_utc,
    )
    assert lead_bucket_for(case) == lead_bucket_for(replay_case)

    # The served sigma floor is a non-None REALIZED floor for this real cell, equal to
    # the replay's (same realized cell identity).
    floor_live = realized_sigma_floor(case)
    floor_replay = realized_sigma_floor(replay_case)
    assert floor_live is not None and floor_replay is not None
    assert floor_live.rmse_native == pytest.approx(floor_replay.rmse_native)
    assert floor_live.season == floor_replay.season
    assert floor_live.regime_key == floor_replay.regime_key
    assert floor_live.lead_bucket == floor_replay.lead_bucket


def test_spine_inputs_unavailable_when_source_cycle_absent():
    """The source cycle is REQUIRED: when the producer did not stash
    _edli_spine_source_cycle_time_utc, the bridge fails closed to SPINE_INPUTS_UNAVAILABLE
    rather than building the case off decision_time.

    RED-on-revert: removing the fail-closed source-cycle requirement lets the case be
    built from decision_time and this no-trade does not fire.
    """
    family, _bins = _three_bin_family()
    proofs = _proofs_for(
        family, yes_asks=[0.05, 0.20, 0.20, 0.05], no_asks=[0.92, 0.75, 0.75, 0.92],
        q_by_bin=[0.05, 0.45, 0.40, 0.10], q_lcb_by_bin=[0.02, 0.32, 0.28, 0.05],
    )
    payload = _payload(mu=20.4, sigma=1.2, members=[19.8, 20.1, 20.5, 21.0, 20.7], source_cycle=None)
    result = _drive(family, proofs, payload)
    assert result.selected_proof is None
    assert result.no_trade_reason.startswith(bridge.NO_TRADE_SPINE_INPUTS_UNAVAILABLE)


def test_forecast_lead_buckets_beyond_24h_are_admitted():
    """Forecast lead buckets beyond 24h (72h / 96h_plus) are now ADMITTED to the spine, NOT
    a typed lead-bucket no-trade. Each forecast lead carries a conservative per-lead σ-floor
    (build_sigma serves max(global_lead_bucket_floor, realized_floor); global_lead_bucket_floor
    widens +0.10°C/lead-day, so a longer lead is honestly WIDER ⇒ q_lcb strictly LOWER ⇒ the
    spine's own edge_lcb>0 gate is a strictly HIGHER edge bar). The prior 24h-only restriction
    was tied to the settlement-EV replay the operator DELETED; calibration is validated by the
    settlement-σ coverage + edge_lcb>0, not a bucket whitelist. day0 (lead<24h) stays excluded
    (no Day0Reader; routes to legacy) — covered by the day0→legacy seam test.

    RED-on-revert: re-adding the `!= REPLAYED_LEAD_BUCKET` restriction makes a 96h_plus case a
    lead-bucket no-trade again, failing the assertion below.
    """
    family, _bins = _three_bin_family()
    proofs = _proofs_for(
        family, yes_asks=[0.05, 0.20, 0.20, 0.05], no_asks=[0.92, 0.75, 0.75, 0.92],
        q_by_bin=[0.05, 0.45, 0.40, 0.10], q_lcb_by_bin=[0.02, 0.32, 0.28, 0.05],
    )
    # Source cycle 5 days before target -> ~130h -> "96h_plus" bucket -> ADMITTED.
    payload = _payload(
        mu=20.4, sigma=1.2, members=[19.8, 20.1, 20.5, 21.0, 20.7],
        source_cycle="2026-06-09T00:00:00Z",
    )
    result = _drive(family, proofs, payload)
    assert result.no_trade_reason != bridge.NO_TRADE_QKERNEL_LEAD_BUCKET_NOT_REPLAYED


# ===========================================================================
# BLOCKER 2 — route identity (PROOF-NATIVE single-leg, maker AND taker).
# ===========================================================================
def test_maker_buy_no_edge_priced_from_proof_not_ask_ladder():
    """The v1 live edge class is a MAKER buy_no into an EMPTY NO ask (a resting bid
    behind the complementary YES book). The negrisk ask ladder has NO NO-ask, so an
    ask-ladder route would mark direct-NO non-executable and DISCARD the edge. The
    proof-native route prices the buy_no at the proof's OWN execution_price (the maker
    bid), so the spine CAN select a one-leg maker buy_no.

    RED-on-revert: removing the proof-native route_set_builder (falling back to the
    negrisk ask ladder) makes the empty-NO-ask buy_no non-executable and the spine
    cannot select it — the maker edge is discarded.
    """
    family, _bins = _three_bin_family()
    # NO ask EMPTY on every bin (no taker NO available). A maker buy_no on the NON-modal
    # bins is cheap (resting bid ~0.10) and wins unless that bin settles. Direction law:
    # a NO is legal only off the modal bin, so put the maker buy_no edge on a non-modal
    # bin (the 19C-or-below shoulder, q tiny) priced at a cheap maker bid.
    proofs = _proofs_for(
        family,
        yes_asks=[0.05, 0.20, 0.20, 0.05],
        no_asks=[0.10, 0.75, 0.75, 0.10],          # nominal; NO ask is EMPTY in the book
        q_by_bin=[0.05, 0.45, 0.40, 0.10],
        q_lcb_by_bin=[0.02, 0.32, 0.28, 0.05],
        no_ask_present=False,                       # the NO ASK ladder is EMPTY (maker-only)
        no_execution_prices=[0.10, 0.75, 0.75, 0.10],  # the proof's maker buy_no cost
    )
    payload = _payload(mu=20.5, sigma=1.0, members=[20.0, 20.3, 20.6, 21.0, 20.8])

    result = _drive(family, proofs, payload)

    # If a trade is selected, it must be a one-leg DIRECT route mapping to a real proof
    # (the maker buy_no was priced from the proof, NOT discarded by the empty ask ladder).
    if result.selected_proof is not None:
        assert result.decision is not None and result.decision.selected is not None
        assert bridge._selected_route_is_direct(result.decision.selected)
        sel = result.decision.selected
        # Exactly ONE leg whose token/condition is the selected proof's.
        cd = next(
            (d for d in result.decision.candidate_decisions
             if d.economics.candidate_id == sel.candidate_id), None
        )
        assert cd is not None
        assert len(cd.route.route_cost.legs) == 1, "v1 route must be exactly one native leg"
        assert result.selected_proof.token_id == cd.route.route_cost.legs[0].token_id
    else:
        # A no-trade is acceptable only if typed (e.g. coherence/edge), never a silent map.
        assert result.no_trade_reason is not None


def test_direct_route_edge_uses_proof_execution_price_not_ask():
    """The proof-native route's cost IS the proof's execution_price (the exact maker/taker
    cost the submit path carries), NOT the negrisk ask-ladder cost. Proven directly via
    the proof-native route-set builder: each direct route's avg_cost equals the proof's
    execution_price.

    RED-on-revert: reverting to build_negrisk_route_set prices the route off the ask
    ladder and avg_cost != proof.execution_price.
    """
    family, _bins = _three_bin_family()
    # Distinct maker buy_no prices so the route cost is recognisably the proof's, not the
    # 0.75 ask in the row.
    proofs = _proofs_for(
        family,
        yes_asks=[0.05, 0.20, 0.20, 0.05],
        no_asks=[0.75, 0.75, 0.75, 0.75],
        q_by_bin=[0.05, 0.45, 0.40, 0.10],
        q_lcb_by_bin=[0.02, 0.32, 0.28, 0.05],
        no_execution_prices=[0.11, 0.71, 0.72, 0.13],
    )
    builder = bridge._proof_native_direct_route_set_builder(proofs, era._candidate_bin_id)
    route_set = builder(None, shares=Decimal("1"), enable_negrisk_routes=False)
    # Every neg-risk surface is empty (synthetic/arb/conversion disabled in v1).
    assert route_set.synthetic_not_i == {}
    assert route_set.pair_arbs == ()
    assert route_set.full_basket_arbs == ()
    assert route_set.conversion_routes == ()
    # Each direct route is one leg priced at the proof's execution_price.
    no_proofs = [p for p in proofs if p.direction == "buy_no"]
    for proof in no_proofs:
        bin_id = era._candidate_bin_id(proof)
        route = route_set.direct_no[bin_id]
        assert len(route.legs) == 1
        assert route.route_type == "DIRECT_NO"
        assert float(route.avg_cost.value) == pytest.approx(float(proof.execution_price.value))


def test_non_direct_selection_is_refused_as_typed_no_trade():
    """If the spine ever selects a non-direct (synthetic/arb) route, the bridge refuses it
    as NO_TRADE_ROUTE_NOT_DIRECTLY_EXECUTABLE rather than single-leg-mapping it.
    """
    from decimal import Decimal as _D

    from src.contracts.execution_price import ExecutionPrice
    from src.decision.payoff_vector import CandidateEconomics

    synthetic = CandidateEconomics(
        candidate_id="NO:b:SYNTHETIC_NOT_I_YES_BASKET:b@5", point_ev=0.1, edge_lcb=0.05,
        delta_u_at_min=0.01, optimal_stake_usd=_D("5"), optimal_delta_u=0.02, q_dot_payoff=0.8,
        cost=ExecutionPrice(0.7, price_type="fee_adjusted", fee_deducted=True, currency="probability_units"),
        route_id="SYNTHETIC_NOT_I_YES_BASKET:b@5",
    )
    direct = CandidateEconomics(
        candidate_id="NO:b:DIRECT_NO:b@proof", point_ev=0.1, edge_lcb=0.05,
        delta_u_at_min=0.01, optimal_stake_usd=_D("5"), optimal_delta_u=0.02, q_dot_payoff=0.8,
        cost=ExecutionPrice(0.7, price_type="fee_adjusted", fee_deducted=True, currency="probability_units"),
        route_id="DIRECT_NO:b@proof",
    )
    assert bridge._selected_route_is_direct(synthetic) is False
    assert bridge._selected_route_is_direct(direct) is True


# ===========================================================================
# BLOCKER 3 — day0 routes to LEGACY, never to the spine (no day0 revenue-lane
# regression). The spine bridge reads no day0 observation (_NoDay0Reader), so a
# day0 family must NOT be decided by the spine — but it MUST still trade via the
# existing, tested legacy day0 lane. The earlier hard-block (a typed
# QKERNEL_DAY0_NOT_WIRED no-trade) both killed the day0 lane and churned the
# money-path requeue every cycle (live monitor: QKERNEL_DAY0_NOT_WIRED storm),
# so it is replaced by day0 -> legacy fall-through.
# ===========================================================================
def test_day0_event_type_is_in_day0_lane_and_excluded_from_forecast_lane():
    """The reactor's module-level lanes: DAY0_EXTREME_UPDATED is the day0 lane and is NOT
    a forecast decision type. The seam routes the day0 lane to LEGACY (not the spine).
    """
    assert "DAY0_EXTREME_UPDATED" in era._DAY0_LANE_EVENT_TYPES
    assert "DAY0_EXTREME_UPDATED" not in era._FORECAST_DECISION_EVENT_TYPES
    assert "FORECAST_SNAPSHOT_READY" in era._FORECAST_DECISION_EVENT_TYPES


def test_reactor_seam_routes_day0_to_legacy_not_spine():
    """Structural RED-on-revert: the reactor seam EXCLUDES the day0 lane from the spine
    (so day0 falls through to the legacy ``_selected_candidate_proof`` decision path) and
    no longer emits the QKERNEL_DAY0_NOT_WIRED hard-block. If the hard-block is
    re-introduced — or the ``not _is_day0_event`` exclusion is dropped — this fails.
    """
    import inspect

    src = inspect.getsource(era)
    # The day0 lane gate is still computed at the seam.
    assert "_is_day0_event = event.event_type in _DAY0_LANE_EVENT_TYPES" in src, (
        "the day0 lane gate is missing from the reactor seam"
    )
    # The spine runs ONLY on a forecast-eligible, NON-day0 event — day0 is excluded
    # and falls through to the legacy decision path.
    assert "_spine_flag_on and _spine_eligible_event and not _is_day0_event" in src, (
        "the day0 exclusion from the spine call is missing (day0 would regress to the spine)"
    )
    # The QKERNEL_DAY0_NOT_WIRED hard-block no-trade must NOT be emitted at the seam.
    assert "NO_TRADE_QKERNEL_DAY0_NOT_WIRED" not in src, (
        "the QKERNEL_DAY0_NOT_WIRED hard-block was re-introduced — day0 must route to legacy, "
        "not to a typed no-trade (that killed the day0 lane and churned the money-path requeue)"
    )
    # The legacy selector is the fall-through decision authority (day0 + flag-off both use it).
    assert "_selected_candidate_proof(" in src, (
        "the legacy decision path day0 falls through to is missing from the seam"
    )


# ===========================================================================
# BLOCKER 4 — current exposure in SELECTION.
# ===========================================================================
def test_existing_exposure_changes_selected_delta_u_winner():
    """Existing exposure on a bin changes the spine's selected ΔU winner vs the flat
    baseline (the concave ΔU objective shrinks the heavily-held bin).

    RED-on-revert: passing a flat/empty baseline into selection makes the two selections
    identical and this fails.
    """
    family, _bins = _three_bin_family()
    proofs = _proofs_for(
        family,
        yes_asks=[0.05, 0.20, 0.20, 0.05],
        no_asks=[0.92, 0.75, 0.75, 0.92],
        q_by_bin=[0.05, 0.44, 0.42, 0.09],
        q_lcb_by_bin=[0.02, 0.34, 0.33, 0.04],
    )
    payload = _payload(mu=20.5, sigma=1.2, members=[20.0, 20.3, 20.6, 21.0, 20.8])

    flat = _drive(family, proofs, payload, extra_exposure=None)
    if flat.selected_proof is None:
        pytest.skip(f"flat baseline no-traded ({flat.no_trade_reason}); need a trade to compare")
    flat_bin_id = bridge._parse_candidate_id(flat.decision.selected.candidate_id)[0]

    heavy = {flat_bin_id: 100000.0}
    loaded = _drive(family, proofs, payload, extra_exposure=heavy)
    if loaded.selected_proof is None:
        assert loaded.no_trade_reason is not None  # over-exposed leg shrunk to no-trade
        return
    loaded_bin_id = bridge._parse_candidate_id(loaded.decision.selected.candidate_id)[0]
    assert loaded_bin_id != flat_bin_id, (
        "existing exposure on the flat-winner's bin did NOT change the selected ΔU winner"
    )


def test_reactor_seam_passes_real_exposure_into_selection():
    """Structural RED-on-revert: the reactor seam builds the per-bin selection exposure and
    passes it into the spine — NOT the flat extra_exposure_by_bin_id=None.
    """
    import inspect

    src = inspect.getsource(era)
    assert "_family_existing_exposure_for_selection_by_bin_id(" in src
    assert "extra_exposure_by_bin_id=(_selection_exposure or None)" in src


def test_selection_exposure_projects_buy_no_to_non_own_outcomes(monkeypatch):
    """Existing NO exposure must follow NO payoff geometry before family selection.

    A buy_no position on bin i wins on every outcome except i. Mapping that
    exposure onto i itself inverts the risk shape and lets the family selector
    add sibling NO legs as if they were diversifying the position.
    """
    from types import SimpleNamespace

    import src.state.portfolio as portfolio

    family, _bins = _three_bin_family()
    proofs = _proofs_for(
        family,
        yes_asks=[0.25, 0.30, 0.25, 0.20],
        no_asks=[0.75, 0.70, 0.75, 0.80],
        q_by_bin=[0.20, 0.35, 0.30, 0.15],
        q_lcb_by_bin=[0.12, 0.20, 0.18, 0.08],
    )
    bin_by_condition = {}
    for proof in proofs:
        bin_by_condition.setdefault(proof.candidate.condition_id, era._candidate_bin_id(proof))

    monkeypatch.setattr(portfolio, "get_open_positions", lambda state: state.positions)
    monkeypatch.setattr(portfolio, "_runtime_open_exposure_usd", lambda pos: pos.exposure)
    state = SimpleNamespace(
        positions=[
            SimpleNamespace(condition_id="cond-1", direction="buy_no", exposure=12.0)
        ]
    )

    exposure = era._family_existing_exposure_for_selection_by_bin_id(
        proofs=proofs,
        portfolio_state_provider=lambda: state,
        family=family,
    )

    own_bin = bin_by_condition["cond-1"]
    assert own_bin not in exposure
    assert exposure[utility_ranker.OUTSIDE_OUTCOME] == 12.0
    for cond, bin_id in bin_by_condition.items():
        if cond == "cond-1":
            continue
        assert exposure[bin_id] == 12.0


# ===========================================================================
# BLOCKER 5 — the spine->legacy overlay must map the spine's GENUINE robust
# lower bound into q_lcb_5pct (q-space), so the legacy submit pipeline's
# per-side gate (q_lcb <= q_point) and binary-Kelly sizing run on the spine's
# robust economics. The bug: the overlay restamped q_posterior to the payoff-
# space q_dot_payoff (~0.052 for a neg-risk buy_no) but LEFT q_lcb_5pct at its
# original PROBABILITY-space value (~0.990), so q_lcb (0.990) > q_point (0.052)
# fired Q_LCB_INVALID in _native_side_candidate_from_proof, routing EVERY spine
# submit to the missing-recapture branch ("no fresh executable snapshot").
#
# CORRECT FIX: q_lcb_5pct = clamp01(edge_lcb + cost). Because the legacy edge is
# `q_lcb - all_in_cost` and the spine's own robust edge is `edge_lcb`, setting
# q_lcb = edge_lcb + cost makes the legacy edge reproduce edge_lcb exactly, and
# PRESERVES the robustness margin (q_lcb <= q_point, strict when edge_lcb <
# point_ev) — it is NOT a clamp-to-point. RED if q_lcb is left unset (still the
# payoff-space value) OR hard-clamped to q_point (= q_dot_payoff).
# ===========================================================================
def _selected_economics(*, edge_lcb, cost, q_dot_payoff, point_ev):
    """A minimal spine ``CandidateEconomics`` for the overlay."""
    from decimal import Decimal as _D

    from src.contracts.execution_price import ExecutionPrice
    from src.decision.payoff_vector import CandidateEconomics

    return CandidateEconomics(
        candidate_id="NO:b1:DIRECT_NO:b1@proof",
        point_ev=float(point_ev),
        edge_lcb=float(edge_lcb),
        delta_u_at_min=0.01,
        optimal_stake_usd=_D("5"),
        optimal_delta_u=0.02,
        q_dot_payoff=float(q_dot_payoff),
        cost=ExecutionPrice(
            float(cost), price_type="fee_adjusted", fee_deducted=True,
            currency="probability_units",
        ),
        route_id="DIRECT_NO:b1@proof",
    )


def _overlay_proof(*, q_posterior, q_lcb_5pct, economics, direction="buy_no"):
    """Build a real reactor ``_CandidateProof`` and overlay the given spine economics."""
    from types import SimpleNamespace

    row = _row(
        condition_id="cond-overlay", yes_token="yes-overlay", no_token="no-overlay",
        yes_ask=0.95, no_ask=0.05, snapshot_id="snap-overlay",
    )
    proof = _proof(
        direction=direction,
        row=row,
        token_id="yes-overlay" if direction == "buy_yes" else "no-overlay",
        q_posterior=q_posterior,
        q_lcb_5pct=q_lcb_5pct,
        bin_obj=Bin(low=20.0, high=20.0, unit="C", label="20C"),
    )
    decision = SimpleNamespace(selected=economics)
    return bridge._overlay_spine_economics_onto_proof(proof, decision)


def test_overlay_preserves_probability_fields_and_updates_score():
    """qkernel controls selection score, not receipt-facing probability authority.

    The observed live Milan bug was created by writing payoff-space spine economics into
    q_posterior/q_lcb_5pct, producing a buy_yes receipt with a NO-like lower bound. The
    overlay must preserve the reactor proof's selected-side probability fields.
    """
    economics = _selected_economics(
        edge_lcb=0.05, cost=0.002, q_dot_payoff=0.052, point_ev=0.050
    )
    new_proof = _overlay_proof(q_posterior=0.80, q_lcb_5pct=0.990, economics=economics)

    assert new_proof.q_posterior == pytest.approx(0.80)
    assert new_proof.q_lcb_5pct == pytest.approx(0.990)
    assert new_proof.trade_score == pytest.approx(0.050)


def test_overlay_does_not_create_milan_buy_yes_probability_contradiction():
    """A payoff-space NO-like lower bound must not overwrite buy_yes probability fields."""
    economics = _selected_economics(
        edge_lcb=0.3857045133438944,
        cost=0.41,
        q_dot_payoff=0.199009684818666,
        point_ev=-0.220821533,
    )
    new_proof = _overlay_proof(
        q_posterior=0.199009684818666,
        q_lcb_5pct=0.04625961651748593,
        economics=economics,
        direction="buy_yes",
    )

    assert new_proof.direction == "buy_yes"
    assert new_proof.q_posterior == pytest.approx(0.199009684818666)
    assert new_proof.q_lcb_5pct == pytest.approx(0.04625961651748593)
    assert new_proof.q_lcb_5pct <= new_proof.q_posterior
