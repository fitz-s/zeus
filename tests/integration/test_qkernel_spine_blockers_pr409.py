# Created: 2026-06-15
# Lifecycle: created=2026-06-15; last_reviewed=2026-06-19; last_reused=2026-06-29
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
#     3. day0 observation lane: _DAY0_LANE_EVENT_TYPES are excluded from the forecast
#        spine call.
#     4. current exposure in SELECTION (per-bin family exposure into argmax ΔU).
"""Integration tests for the four PR #409 live-path blockers (RED-on-revert)."""
from __future__ import annotations

import datetime as _dt
import json
from decimal import Decimal

import numpy as np
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


@pytest.fixture(autouse=True)
def _fully_licensed_selection_calibrator(monkeypatch):
    """Keep qkernel integration fixtures independent from the generated live artifact.

    The q_lcb OOF reliability guard and the selection calibrator are separate live gates. These
    tests exercise qkernel route math, so they install a deep YES/NO selection artifact unless an
    individual test intentionally monkeypatches ``family_decision_engine.apply_selection_calibrator``.
    """

    from src.decision import selection_calibrator as sc

    cells: dict[str, dict[str, float | int]] = {}
    for lead in ("L1", "L2_3", "L4P"):
        for side in ("YES", "NO"):
            for bin_class in ("modal", "nonmodal"):
                for pb in range(len(sc.RAW_PROB_BUCKET_EDGES) - 1):
                    cells[f"{side}|{lead}|{bin_class}|pb{pb}"] = {
                        "n": 1000,
                        "hit_rate": 0.95,
                    }
    artifact = {
        "_meta": {
            "authority": "selection_calibrator_v1_walkforward",
            "version": "sel_v1",
            "posterior_version": sc.DEFAULT_POSTERIOR_VERSION,
            "min_n": 30,
            "armed_sides": ["YES", "NO"],
            "cell_key_schema": "side|lead_bucket|bin_class|raw_prob_bucket",
        },
        "cells": cells,
    }
    monkeypatch.setattr(sc, "load_artifact", lambda: artifact)
    sc.reset_artifact_cache()
    yield
    sc.reset_artifact_cache()


def _install_sigma_floor_artifact(monkeypatch, tmp_path, *, sigma_floor_c: float = 1.0):
    """Install a minimal settlement sigma-floor artifact for tests that need a real floor."""

    from src.calibration import emos as emos_mod

    path = tmp_path / "settlement_sigma_floor.json"
    path.write_text(
        json.dumps(
            {
                "_meta": {"k_default": 1.0},
                "cells": {
                    f"{CITY}|JJA|{METRIC}": {
                        "sigma_floor_c": sigma_floor_c,
                        "n": 100,
                        "window": "test-fixture",
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(emos_mod, "_SIGMA_FLOOR_PATH", path)
    monkeypatch.setattr(emos_mod, "_sigma_floor_cache", None)
    return path


def test_qkernel_band_alpha_uses_live_fdr_budget(monkeypatch):
    """The spine tail must not be a hidden stricter pre-FDR filter."""

    from src.config import settings

    old_edge = dict(settings["edge"])
    monkeypatch.setitem(settings._data, "edge", {**old_edge, "fdr_alpha": 0.13})
    assert bridge._qkernel_spine_band_alpha() == pytest.approx(0.13)


def test_qkernel_band_alpha_invalid_config_falls_back(monkeypatch):
    """A bad config value keeps the historical conservative tail."""

    from src.config import settings

    old_edge = dict(settings["edge"])
    monkeypatch.setitem(settings._data, "edge", {**old_edge, "fdr_alpha": 0.65})
    assert bridge._qkernel_spine_band_alpha() == pytest.approx(0.05)


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


def _five_bin_center_family(*, event_type="FORECAST_SNAPSHOT_READY"):
    bins = [
        Bin(low=None, high=18.0, unit="C", label="18C or below"),
        Bin(low=19.0, high=19.0, unit="C", label="19C"),
        Bin(low=20.0, high=20.0, unit="C", label="20C"),
        Bin(low=21.0, high=21.0, unit="C", label="21C"),
        Bin(low=22.0, high=None, unit="C", label="22C or above"),
    ]
    candidates = tuple(
        _candidate(condition_id=f"cond-center-{i}", yes_token=f"yes-center-{i}",
                   no_token=f"no-center-{i}", bin_obj=b)
        for i, b in enumerate(bins)
    )
    family = EventBoundCandidateFamily(
        family_id="edli_family_center_yes_regression",
        event_id="evt-center-yes-regression",
        event_type=event_type,
        city=CITY,
        target_date=TARGET_DATE,
        metric=METRIC,
        condition_ids=tuple(c.condition_id for c in candidates),
        yes_token_ids=tuple(c.yes_token_id for c in candidates),
        no_token_ids=tuple(c.no_token_id for c in candidates),
        bins=tuple(bins),
        candidates=candidates,
        causal_snapshot_id="snap-center-yes-regression",
        market_topology_source="executable_market_snapshots",
        binding_hash="hash-center-yes-regression",
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


def _drive(
    family,
    proofs,
    payload,
    *,
    decision_time=DECISION_TIME,
    extra_exposure=None,
    selection_proofs=None,
):
    return bridge.decide_family_via_spine(
        family=family,
        payload=payload,
        proofs=proofs,
        selection_proofs=selection_proofs,
        decision_time=decision_time,
        native_side_candidate_from_proof=era._native_side_candidate_from_proof,
        candidate_bin_id=era._candidate_bin_id,
        payoff_matrix_over_bins=utility_ranker.FamilyPayoffMatrix.over_bins,
        exposure_builder=era._robust_marginal_utility_exposure,
        baseline_usd_provider=era._robust_marginal_utility_baseline_usd,
        per_bin_yes_q_lcb=era._per_bin_yes_q_lcb(proofs),
        extra_exposure_by_bin_id=extra_exposure,
    )


def _fully_licensed_reliability_cells(guard_mod, *, hit_rate: float = 0.95) -> dict[str, tuple[int, float]]:
    """Active-valid OOF guard table covering both live precision classes."""
    cells: dict[str, tuple[int, float]] = {}
    for lead in ("L1", "L2_3", "L4P"):
        for side in ("YES", "NO"):
            for pos in ("modal", "nonmodal"):
                for precision in ("fine_nest", "coarse_global"):
                    for qb in range(len(guard_mod.QLCB_BUCKET_EDGES) - 1):
                        cells[f"high|{lead}|{side}|{pos}|qb{qb}|{precision}"] = (1000, hit_rate)
    return cells


# ===========================================================================
# BLOCKER 1 — live==replay forecast-case (source-cycle, emos_season, 24h bucket).
# ===========================================================================
def test_live_bridge_forecast_case_matches_arm_replay(monkeypatch, tmp_path):
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

    _install_sigma_floor_artifact(monkeypatch, tmp_path)

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
    (no Day0Reader; routes to the Day0 observation lane) — covered by the
    day0 seam test.

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


def test_center_yes_selected_over_adjacent_no_when_guard_and_book_license(monkeypatch, tmp_path):
    """Shanghai-style direct selection: licensed cheap modal YES beats adjacent NO substitutes.

    The live qkernel v1 route surface is direct-only. It still must choose the best executable
    native leg when evidence supports the center YES; it must not drift into "just buy one
    adjacent NO" because old family/guard wiring made YES disappear.
    """
    from src.decision import qlcb_reliability_guard as guard_mod

    _install_sigma_floor_artifact(monkeypatch, tmp_path)

    reliability_cells = _fully_licensed_reliability_cells(guard_mod)
    monkeypatch.setattr(guard_mod, "_RELIABILITY_CACHE", reliability_cells)
    monkeypatch.setattr(guard_mod, "_RELIABILITY_LOADED", True)
    monkeypatch.setattr(guard_mod, "_RELIABILITY_ARTIFACT_ACTIVE", True)

    family, _bins = _five_bin_center_family()
    proofs = _proofs_for(
        family,
        # YES mids are coherent with the live sigma-floor distribution around 20C.
        yes_asks=[0.13, 0.24, 0.27, 0.24, 0.13],
        no_asks=[0.87, 0.76, 0.73, 0.76, 0.87],
        q_by_bin=[0.0, 0.0, 1.0, 0.0, 0.0],
        q_lcb_by_bin=[0.0, 0.0, 0.999, 0.0, 0.0],
        no_execution_prices=[0.87, 0.74, 0.73, 0.74, 0.87],
    )
    payload = _payload(mu=20.0, sigma=0.05, members=[20, 20, 20, 20, 20])

    result = _drive(family, proofs, payload)

    assert result.no_trade_reason is None
    assert result.selected_proof is not None
    assert result.selected_proof.direction == "buy_yes"
    assert result.selected_proof.candidate.bin.label == "20C"
    assert result.decision is not None
    selected = result.decision.selected
    assert selected is not None
    selected_decision = next(
        d for d in result.decision.candidate_decisions
        if d.economics.candidate_id == selected.candidate_id
    )
    assert selected_decision.q_lcb_guard_basis == "OOF_WILSON_95"
    assert selected_decision.q_lcb_guard_cell_key.startswith("high|L2_3|YES|modal|")
    assert selected_decision.coherence_allows is True


def test_oof_guard_licenses_center_yes_against_deep_market_disagreement(monkeypatch, tmp_path):
    """Empirical q_lcb guard is the model-superiority license consumed by coherence.

    Regression: market coherence used the default "license nothing" predicate, so a live-floor
    center YES with positive guarded edge/Delta-U could be blocked solely because the deep
    market disagreed. That preserved the all-NO failure mode in Shanghai-style families.
    """
    from src.decision import qlcb_reliability_guard as guard_mod

    _install_sigma_floor_artifact(monkeypatch, tmp_path)

    reliability_cells = _fully_licensed_reliability_cells(guard_mod)
    monkeypatch.setattr(guard_mod, "_RELIABILITY_CACHE", reliability_cells)
    monkeypatch.setattr(guard_mod, "_RELIABILITY_LOADED", True)
    monkeypatch.setattr(guard_mod, "_RELIABILITY_ARTIFACT_ACTIVE", True)

    family, _bins = _three_bin_family()
    proofs = _proofs_for(
        family,
        yes_asks=[0.90, 0.27, 0.90, 0.90],
        no_asks=[0.79, 0.90, 0.80, 0.95],
        q_by_bin=[0.0, 1.0, 0.0, 0.0],
        q_lcb_by_bin=[0.0, 0.999, 0.0, 0.0],
        no_execution_prices=[0.79, 0.90, 0.80, 0.95],
    )

    result = _drive(
        family,
        proofs,
        _payload(mu=20.0, sigma=0.1, members=[20, 20, 20, 20, 20]),
    )

    assert result.no_trade_reason is None
    assert result.selected_proof is not None
    assert result.selected_proof.direction == "buy_yes"
    assert result.selected_proof.candidate.bin.label == "20C"
    assert result.decision is not None
    selected_decision = next(
        d for d in result.decision.candidate_decisions
        if d.economics.candidate_id == result.decision.selected.candidate_id
    )
    assert selected_decision.q_lcb_guard_basis == "OOF_WILSON_95"
    assert selected_decision.coherence_allows is True


def test_spine_preserves_payload_served_sigma_for_point_bin_integration(monkeypatch):
    """Point-bin q uses the reactor-served sigma, not a rebuilt generic fallback.

    Regression: the bridge threaded ``sigma=0.05`` but the generic predictive builder
    rebuilt a 1.5C fallback width before q integration. That spread most mass out of the
    center point-bin preimage, making adjacent NO legs look better than the cheap center
    YES in Shanghai-style families.
    """
    from src.decision import qlcb_reliability_guard as guard_mod

    reliability_cells = _fully_licensed_reliability_cells(guard_mod)
    monkeypatch.setattr(guard_mod, "_RELIABILITY_CACHE", reliability_cells)
    monkeypatch.setattr(guard_mod, "_RELIABILITY_LOADED", True)
    monkeypatch.setattr(guard_mod, "_RELIABILITY_ARTIFACT_ACTIVE", True)

    family, _bins = _three_bin_family()
    proofs = _proofs_for(
        family,
        yes_asks=[0.90, 0.27, 0.90, 0.90],
        no_asks=[0.79, 0.90, 0.80, 0.95],
        q_by_bin=[0.0, 1.0, 0.0, 0.0],
        q_lcb_by_bin=[0.0, 0.999, 0.0, 0.0],
        no_execution_prices=[0.79, 0.90, 0.80, 0.95],
    )

    result = _drive(
        family,
        proofs,
        _payload(mu=20.0, sigma=0.05, members=[20, 20, 20, 20, 20]),
    )

    assert result.no_trade_reason is None
    assert result.decision is not None
    assert result.decision.predictive.sigma_native == pytest.approx(0.05)
    q_by_label = {
        bin_obj.label: float(q)
        for bin_obj, q in zip(result.decision.omega.bins, result.decision.joint_q.q)
    }
    assert q_by_label["20C"] > 0.999
    assert result.selected_proof is not None
    assert result.selected_proof.direction == "buy_yes"
    assert result.selected_proof.candidate.bin.label == "20C"


def test_unarmed_nonmodal_yes_tail_is_abstained_before_selection(monkeypatch):
    """Cheap nonmodal YES tails require selected-side evidence before live submit.

    Regression: legacy selection artifacts were NO-only, but returned an identity
    q_safe for unarmed YES. The qkernel then treated a very cheap tail YES as
    executable alpha even though no selected-side empirical evidence licensed it.
    """
    from src.decision import family_decision_engine as fde
    from src.decision import qlcb_reliability_guard as guard_mod
    from src.decision.selection_calibrator import CalibratorVerdict

    reliability_cells = _fully_licensed_reliability_cells(guard_mod)
    monkeypatch.setattr(guard_mod, "_RELIABILITY_CACHE", reliability_cells)
    monkeypatch.setattr(guard_mod, "_RELIABILITY_LOADED", True)
    monkeypatch.setattr(guard_mod, "_RELIABILITY_ARTIFACT_ACTIVE", True)

    def _fake_selection_guard(*, raw_side_prob, side, lead_days, bin_class, admission_margin=None):
        return CalibratorVerdict(
            q_safe=float(raw_side_prob),
            trade=True,
            abstained=False,
            cell_key=f"fake|{side}|{bin_class}",
            L_g=float("nan"),
            n_g=0,
            basis="SIDE_NOT_ARMED" if str(side).upper() == "YES" else "SELECTION_BETA_95",
        )

    monkeypatch.setattr(fde, "apply_selection_calibrator", _fake_selection_guard)

    family, _bins = _three_bin_family()
    proofs = _proofs_for(
        family,
        yes_asks=[0.90, 0.90, 0.90, 0.01],
        no_asks=[0.99, 0.99, 0.99, 0.99],
        q_by_bin=[0.04, 0.42, 0.41, 0.13],
        q_lcb_by_bin=[0.02, 0.28, 0.27, 0.08],
        no_execution_prices=[0.99, 0.99, 0.99, 0.99],
    )
    tail_proof = next(
        proof
        for proof in proofs
        if proof.direction == "buy_yes" and proof.candidate.bin.label == "22C or above"
    )
    tail_bin_id = era._candidate_bin_id(tail_proof)

    result = _drive(
        family,
        proofs,
        _payload(mu=20.5, sigma=1.2, members=[19.8, 20.1, 20.5, 21.0, 21.2]),
    )

    assert result.decision is not None
    tail_decisions = [
        decision
        for decision in result.decision.candidate_decisions
        if decision.route.side == "YES" and decision.route.bin_id == tail_bin_id
    ]
    assert tail_decisions
    tail_decision = tail_decisions[0]
    assert tail_decision.selection_guard_basis == "SIDE_NOT_ARMED"
    assert tail_decision.selection_guard_abstained is True
    assert tail_decision.selection_guard_q_safe == 0.0
    assert tail_decision.economics.edge_lcb < 0.0
    assert float(tail_decision.economics.optimal_stake_usd) == 0.0
    if result.selected_proof is not None:
        assert result.selected_proof.token_id != tail_proof.token_id


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
# BLOCKER 3 — day0 routes to its observation lane, never to the forecast spine.
# The spine bridge reads no day0 observation (_NoDay0Reader), so a day0 family
# must NOT be decided by the forecast spine. The replacement is an explicit day0 lane,
# while forecast flag-off remains QKERNEL_SPINE_REQUIRED no-trade.
# ===========================================================================
def test_day0_event_type_is_in_day0_lane_and_excluded_from_forecast_lane():
    """The reactor's module-level lanes: DAY0_EXTREME_UPDATED is the day0 lane and is NOT
    a forecast decision type. The seam routes the day0 lane outside the forecast spine.
    """
    assert "DAY0_EXTREME_UPDATED" in era._DAY0_LANE_EVENT_TYPES
    assert "DAY0_EXTREME_UPDATED" not in era._FORECAST_DECISION_EVENT_TYPES
    assert "FORECAST_SNAPSHOT_READY" in era._FORECAST_DECISION_EVENT_TYPES


def test_reactor_seam_routes_day0_to_observation_lane_not_forecast_spine():
    """Structural RED-on-revert: the reactor seam EXCLUDES the day0 lane from the spine
    and no longer emits the QKERNEL_DAY0_NOT_WIRED hard-block. If the hard-block is
    re-introduced — or the ``not _is_day0_event`` exclusion is dropped — this fails.
    """
    import inspect

    src = inspect.getsource(era)
    # The day0 lane gate is still computed at the seam.
    assert "_is_day0_event = event.event_type in _DAY0_LANE_EVENT_TYPES" in src, (
        "the day0 lane gate is missing from the reactor seam"
    )
    # The spine runs ONLY on a forecast-eligible, NON-day0 event — day0 is excluded.
    assert "_spine_flag_on and _spine_eligible_event and not _is_day0_event" in src, (
        "the day0 exclusion from the spine call is missing (day0 would regress to the spine)"
    )
    # The QKERNEL_DAY0_NOT_WIRED hard-block no-trade must NOT be emitted at the seam.
    assert "NO_TRADE_QKERNEL_DAY0_NOT_WIRED" not in src, (
        "the QKERNEL_DAY0_NOT_WIRED hard-block was re-introduced — day0 must route to "
        "its observation lane, not to a typed forecast-spine no-trade"
    )
    # Forecast flag-off must no-trade; it must not use the day0 selector as fallback.
    assert 'if _spine_eligible_event and not _is_day0_event and not _spine_flag_on' in src
    assert '_spine_no_trade_reason = "QKERNEL_SPINE_REQUIRED"' in src
    # Day0 still has an observation-lane selector seam.
    assert "_selected_candidate_proof(" in src, (
        "the day0 observation selector seam is missing"
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


def test_selection_exposure_includes_quarantined_chain_backed_position():
    """Quarantined chain-backed exposure must still shape family selection.

    A local quarantine label is not proof that the venue exposure is gone when
    chain_state still asserts current money risk. The selection exposure map
    must see that old NO payoff before choosing a sibling route.
    """
    from types import SimpleNamespace

    from src.state.portfolio import Position

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

    position = Position(
        trade_id="munich-30c",
        market_id="m",
        city=family.city,
        cluster="eu",
        target_date=family.target_date,
        bin_label="30C",
        direction="buy_no",
        state="quarantined",
        chain_state="entry_authority_quarantined",
        condition_id="cond-1",
        chain_shares=29.14,
        chain_cost_basis_usd=21.27,
        chain_avg_price=0.73,
        fill_authority="venue_position_observed",
    )
    state = SimpleNamespace(positions=[position])

    exposure = era._family_existing_exposure_for_selection_by_bin_id(
        proofs=proofs,
        portfolio_state_provider=lambda: state,
        family=family,
    )

    own_bin = bin_by_condition["cond-1"]
    assert own_bin not in exposure
    assert exposure[utility_ranker.OUTSIDE_OUTCOME] == pytest.approx(21.27)
    for cond, bin_id in bin_by_condition.items():
        if cond == "cond-1":
            continue
        assert exposure[bin_id] == pytest.approx(21.27)


def test_selection_exposure_reads_chain_backed_db_without_portfolio_provider():
    """The canonical trade DB path must not depend on an in-memory portfolio provider.

    Restart/recovery adapter constructions can have ``held_position_conn`` but no
    provider. Munich-style chain-backed NO exposure must still shape the next
    family selection in that shape.
    """
    import sqlite3

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

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
            CREATE TABLE position_current (
                condition_id TEXT,
                direction TEXT,
                phase TEXT,
                chain_state TEXT,
                chain_shares REAL,
                chain_cost_basis_usd REAL
            )
        """
    )
    conn.execute(
        """
            INSERT INTO position_current (
                condition_id, direction, phase, chain_state, chain_shares, chain_cost_basis_usd
            ) VALUES ('cond-1', 'buy_no', 'quarantined', 'entry_authority_quarantined', 29.14, 21.27)
            """
        )

    exposure = era._family_existing_exposure_for_selection_by_bin_id(
        proofs=proofs,
        portfolio_state_provider=None,
        held_position_conn=conn,
        family=family,
    )

    own_bin = bin_by_condition["cond-1"]
    assert own_bin not in exposure
    assert exposure[utility_ranker.OUTSIDE_OUTCOME] == pytest.approx(21.27)
    for cond, bin_id in bin_by_condition.items():
        if cond == "cond-1":
            continue
        assert exposure[bin_id] == pytest.approx(21.27)


def test_opportunity_book_receipt_records_selection_exposure():
    """The live receipt must prove which family exposure shaped qkernel selection."""

    family, _bins = _three_bin_family()
    proofs = _proofs_for(
        family,
        yes_asks=[0.25, 0.30, 0.25, 0.20],
        no_asks=[0.75, 0.70, 0.75, 0.80],
        q_by_bin=[0.20, 0.35, 0.30, 0.15],
        q_lcb_by_bin=[0.12, 0.20, 0.18, 0.08],
    )
    exposure = {
        era._candidate_bin_id(proofs[0]): 12.0,
        utility_ranker.OUTSIDE_OUTCOME: 12.0,
    }

    book = era._opportunity_book_from_proofs(
        event_id="evt-selection-exposure",
        family_id=family.family_id,
        proofs=proofs,
        selected_proof=proofs[0],
        selection_exposure_by_outcome=exposure,
    )

    summary = book.to_receipt_dict()["cache_summary"]["selection_exposure"]
    assert summary["source"] == "position_current_family_selection_exposure"
    assert summary["nonzero_outcome_count"] == 2
    assert summary["total_exposure_usd"] == pytest.approx(24.0)
    assert summary["max_outcome_exposure_usd"] == pytest.approx(12.0)
    assert summary["by_outcome_usd"][utility_ranker.OUTSIDE_OUTCOME] == pytest.approx(12.0)


def test_opportunity_book_receipt_records_checked_empty_selection_exposure():
    """Empty family exposure is explicit evidence, not an omitted receipt field."""

    family, _bins = _three_bin_family()
    proofs = _proofs_for(
        family,
        yes_asks=[0.25, 0.30, 0.25, 0.20],
        no_asks=[0.75, 0.70, 0.75, 0.80],
        q_by_bin=[0.20, 0.35, 0.30, 0.15],
        q_lcb_by_bin=[0.12, 0.20, 0.18, 0.08],
    )

    book = era._opportunity_book_from_proofs(
        event_id="evt-selection-exposure-empty",
        family_id=family.family_id,
        proofs=proofs,
        selected_proof=proofs[0],
        selection_exposure_by_outcome={},
    )

    summary = book.to_receipt_dict()["cache_summary"]["selection_exposure"]
    assert summary["source"] == "position_current_family_selection_exposure"
    assert summary["status"] == "checked_empty"
    assert summary["nonzero_outcome_count"] == 0
    assert summary["total_exposure_usd"] == pytest.approx(0.0)
    assert summary["by_outcome_usd"] == {}


def test_selection_exposure_fails_closed_for_same_family_position_outside_bound_topology():
    """Same city/date/metric exposure cannot flatten to zero when topology split hides it.

    Munich-style adjacent-NO losses can happen when an already-held sibling
    position belongs to the same weather family but its condition_id is absent
    from the current bound topology/proof set. That is not an empty portfolio;
    it is missing family-risk evidence, so live selection must fail closed.
    """
    import sqlite3

    family, _bins = _three_bin_family()
    proofs = _proofs_for(
        family,
        yes_asks=[0.25, 0.30, 0.25, 0.20],
        no_asks=[0.75, 0.70, 0.75, 0.80],
        q_by_bin=[0.20, 0.35, 0.30, 0.15],
        q_lcb_by_bin=[0.12, 0.20, 0.18, 0.08],
    )

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
            CREATE TABLE position_current (
                city TEXT,
                target_date TEXT,
                temperature_metric TEXT,
                condition_id TEXT,
                direction TEXT,
                phase TEXT,
                chain_state TEXT,
                chain_shares REAL,
                chain_cost_basis_usd REAL
            )
        """
    )
    conn.execute(
        """
            INSERT INTO position_current (
                city, target_date, temperature_metric, condition_id, direction,
                phase, chain_state, chain_shares, chain_cost_basis_usd
            ) VALUES (?, ?, ?, 'foreign-cond', 'buy_no', 'active', 'synced', 29.14, 21.27)
        """,
        (family.city, family.target_date, family.metric),
    )

    with pytest.raises(
        RuntimeError,
        match=(
            "EDLI_SELECTION_EXPOSURE_UNAVAILABLE:RuntimeError:"
            "SAME_FAMILY_POSITION_NOT_IN_TOPOLOGY"
        ),
    ):
        era._family_existing_exposure_for_selection_by_bin_id(
            proofs=proofs,
            portfolio_state_provider=None,
            held_position_conn=conn,
            family=family,
        )


def test_selection_exposure_excludes_chain_absent_quarantine():
    """Confirmed chain absence must not be reintroduced as live family exposure."""
    from types import SimpleNamespace

    from src.state.portfolio import Position

    family, _bins = _three_bin_family()
    proofs = _proofs_for(
        family,
        yes_asks=[0.25, 0.30, 0.25, 0.20],
        no_asks=[0.75, 0.70, 0.75, 0.80],
        q_by_bin=[0.20, 0.35, 0.30, 0.15],
        q_lcb_by_bin=[0.12, 0.20, 0.18, 0.08],
    )
    position = Position(
        trade_id="munich-30c-chain-absent",
        market_id="m",
        city=family.city,
        cluster="eu",
        target_date=family.target_date,
        bin_label="30C",
        direction="buy_no",
        state="quarantined",
        chain_state="chain_absent_confirmed_position_unattributed",
        condition_id="cond-1",
        chain_shares=29.14,
        chain_cost_basis_usd=21.27,
        chain_avg_price=0.73,
        fill_authority="venue_position_observed",
    )
    state = SimpleNamespace(positions=[position])

    assert era._family_existing_exposure_for_selection_by_bin_id(
        proofs=proofs,
        portfolio_state_provider=lambda: state,
        family=family,
    ) == {}


def test_selection_exposure_fails_closed_when_trade_db_truth_unreadable():
    """A supplied canonical trade DB must not flatten to empty exposure on schema loss."""
    import sqlite3

    family, _bins = _three_bin_family()
    proofs = _proofs_for(
        family,
        yes_asks=[0.25, 0.30, 0.25, 0.20],
        no_asks=[0.75, 0.70, 0.75, 0.80],
        q_by_bin=[0.20, 0.35, 0.30, 0.15],
        q_lcb_by_bin=[0.12, 0.20, 0.18, 0.08],
    )
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE position_current (phase TEXT)")

    with pytest.raises(RuntimeError, match="EDLI_SELECTION_EXPOSURE_UNAVAILABLE"):
        era._family_existing_exposure_for_selection_by_bin_id(
            proofs=proofs,
            portfolio_state_provider=None,
            held_position_conn=conn,
            family=family,
        )


# ===========================================================================
# BLOCKER 5 — the spine->legacy overlay must write one coherent qkernel-selected
# probability authority into the proof fields consumed by receipts, submit, monitor, and
# redecision. ``payoff_q_lcb`` is produced by the payoff-vector layer itself; the bridge
# must not reverse-derive probability by adding cost back to an edge.
# ===========================================================================
def _selected_economics(*, edge_lcb, cost, q_dot_payoff, point_ev, side="NO"):
    """A minimal spine ``CandidateEconomics`` for the overlay."""
    from decimal import Decimal as _D

    from src.contracts.execution_price import ExecutionPrice
    from src.decision.payoff_vector import CandidateEconomics

    route_side = "YES" if str(side).upper() == "YES" else "NO"
    return CandidateEconomics(
        candidate_id=f"{route_side}:b1:DIRECT_{route_side}:b1@proof",
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
        route_id=f"DIRECT_{route_side}:b1@proof",
        payoff_q_lcb=float(edge_lcb) + float(cost),
    )


def _overlay_proof(
    *,
    q_posterior,
    q_lcb_5pct,
    economics,
    direction="buy_no",
    missing_reason=None,
    direction_law_ok=True,
    coherence_allows=True,
    q_lcb_guard_basis="OOF_WILSON_95",
    q_lcb_guard_abstained=False,
    q_lcb_guard_cell_key=None,
):
    """Build a real reactor ``_CandidateProof`` and overlay the given spine economics."""
    from dataclasses import replace
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
    if missing_reason is not None:
        proof = replace(
            proof,
            missing_reason=missing_reason,
            passed_prefilter=False,
            trade_score=0.0,
        )
    selected_route = SimpleNamespace(
        side="NO" if direction == "buy_no" else "YES",
        bin_id="b1",
        payoff_vector=np.array([1.0]),
    )
    selected_decision = SimpleNamespace(
        economics=economics,
        route=selected_route,
        q_lcb_guard_basis=q_lcb_guard_basis,
        q_lcb_guard_abstained=q_lcb_guard_abstained,
        q_lcb_guard_cell_key=(
            q_lcb_guard_cell_key
            if q_lcb_guard_cell_key is not None
            else f"high|L2_3|{selected_route.side}|test|qb1|coarse_global"
        ),
        direction_law_ok=direction_law_ok,
        coherence_allows=coherence_allows,
        selection_guard_basis="SELECTION_BETA_95",
        selection_guard_abstained=False,
        selection_guard_cell_key=f"{selected_route.side}|L2_3|modal|pb4",
        selection_guard_n=80,
        selection_guard_q_safe=max(float(economics.q_dot_payoff) - 0.01, 0.001),
    )
    decision = SimpleNamespace(
        selected=economics,
        band=SimpleNamespace(samples=np.array([[1.0], [1.0], [1.0]])),
        candidate_decisions=(selected_decision,),
    )
    return bridge._overlay_spine_economics_onto_proof(proof, decision)


def test_overlay_uses_qkernel_probability_fields_and_updates_score():
    """qkernel route economics may tighten the lower bound on the same served belief."""
    economics = _selected_economics(
        edge_lcb=0.05, cost=0.002, q_dot_payoff=0.202, point_ev=0.200
    )
    new_proof = _overlay_proof(q_posterior=0.202, q_lcb_5pct=0.10, economics=economics)

    assert new_proof.q_posterior == pytest.approx(0.202)
    assert new_proof.q_lcb_5pct == pytest.approx(0.052)
    assert new_proof.trade_score == pytest.approx(0.050)
    assert new_proof.q_source != "qkernel_spine"
    assert new_proof.selection_authority_applied == "qkernel_spine"
    assert new_proof.qkernel_execution_economics["payoff_q_lcb"] == pytest.approx(
        0.052
    )
    assert new_proof.qkernel_execution_economics["payoff_q_point"] == pytest.approx(
        0.202
    )
    assert new_proof.qkernel_execution_economics["edge_lcb"] == pytest.approx(0.05)
    assert new_proof.qkernel_execution_economics["point_ev"] == pytest.approx(0.20)
    assert new_proof.qkernel_execution_economics["optimal_stake_usd"] == "5"
    assert new_proof.qkernel_execution_economics["pre_qkernel_q_posterior"] == pytest.approx(
        0.202
    )
    assert new_proof.qkernel_execution_economics["pre_qkernel_q_lcb_5pct"] == pytest.approx(
        0.10
    )
    assert new_proof.qkernel_execution_economics["q_lcb_authority"] == "qkernel_payoff_bound"
    assert new_proof.qkernel_execution_economics["probability_authority"] == (
        "qkernel_payoff_direct_route"
    )


def test_overlay_rejects_qkernel_point_probability_that_is_not_served_belief():
    """A direct route cannot mint a qkernel probability from a different q-space."""

    economics = _selected_economics(
        edge_lcb=0.05, cost=0.002, q_dot_payoff=0.202, point_ev=0.200
    )

    new_proof = _overlay_proof(
        q_posterior=0.80,
        q_lcb_5pct=0.001,
        economics=economics,
    )

    assert new_proof is None


def test_overlay_rejects_qkernel_lcb_that_loosens_served_belief():
    """A qkernel direct route can tighten, but not raise, the served lower bound."""

    economics = _selected_economics(
        edge_lcb=0.05, cost=0.002, q_dot_payoff=0.202, point_ev=0.200
    )

    new_proof = _overlay_proof(
        q_posterior=0.202,
        q_lcb_5pct=0.02,
        economics=economics,
    )

    assert new_proof is None


def test_overlay_live_direct_no_uses_qkernel_probability_authority():
    """Direct NO sizing uses the qkernel payoff pair after served-belief identity guards."""

    economics = _selected_economics(
        edge_lcb=0.22226499587493073,
        cost=0.65,
        q_dot_payoff=0.9154395759428866,
        point_ev=0.26543957594288655,
        side="NO",
    )

    new_proof = _overlay_proof(
        q_posterior=0.9154395759428866,
        q_lcb_5pct=0.90,
        economics=economics,
        direction="buy_no",
    )

    assert new_proof is not None
    assert new_proof.q_posterior == pytest.approx(0.9154395759428866)
    assert new_proof.q_lcb_5pct == pytest.approx(0.8722649958749307)
    assert new_proof.qkernel_execution_economics["pre_qkernel_q_posterior"] == pytest.approx(
        0.9154395759428866
    )


def test_no_trade_projection_uses_qkernel_rejection_reason_not_legacy_scalar():
    """No-trade receipts must not show stale scalar gates after qkernel scores a leg."""

    from dataclasses import replace

    row = _row(
        condition_id="cond-qk-reject",
        yes_token="yes-qk-reject",
        no_token="no-qk-reject",
        yes_ask=0.01,
        no_ask=0.99,
        snapshot_id="snap-qk-reject",
    )
    proof = _proof(
        direction="buy_yes",
        row=row,
        token_id="yes-qk-reject",
        q_posterior=0.02,
        q_lcb_5pct=0.000001,
        bin_obj=Bin(low=33.0, high=33.0, unit="C", label="33C"),
    )
    proof = replace(
        proof,
        missing_reason="ADMISSION_CAPITAL_EFFICIENCY_LCB_EV:ev_per_dollar=-0.9",
        passed_prefilter=False,
        trade_score=0.0,
    )
    cert = {
        "source": "qkernel_spine",
        "decision_id": "decision-qk-reject",
        "receipt_hash": "receipt-qk-reject",
        "candidate_id": "YES:b33:DIRECT_YES:b33@proof",
        "route_id": "DIRECT_YES:b33@proof",
        "side": "YES",
        "bin_id": era._candidate_bin_id(proof),
        "payoff_q_point": 0.061,
        "payoff_q_lcb": 0.012,
        "edge_lcb": 0.002,
        "point_ev": 0.051,
        "delta_u_at_min": 0.00001,
        "optimal_stake_usd": "2.5",
        "optimal_delta_u": 0.001,
        "q_dot_payoff": 0.061,
        "cost": 0.270,
        "direction_law_ok": False,
        "coherence_allows": True,
        "q_lcb_guard_basis": "OOF_WILSON_95",
        "q_lcb_guard_abstained": False,
        "q_lcb_guard_cell_key": "high|L2_3|YES|nonmodal|qb0|coarse_global",
    }

    (annotated,) = era._proofs_with_qkernel_candidate_economics(
        proofs=(proof,),
        qkernel_economics_by_bin_side={(era._candidate_bin_id(proof), "YES"): cert},
    )

    assert annotated.q_posterior == pytest.approx(0.02)
    assert annotated.q_lcb_5pct == pytest.approx(0.000001)
    assert annotated.trade_score == 0.0
    assert annotated.passed_prefilter is False
    assert annotated.missing_reason == "QKERNEL_DIRECTION_LAW_REJECTED:side=YES"
    assert annotated.selection_authority_applied is None
    assert annotated.qkernel_execution_economics == {
        **cert,
        "pre_qkernel_q_posterior": pytest.approx(0.02),
        "pre_qkernel_q_lcb_5pct": pytest.approx(0.000001),
        "q_lcb_authority": "qkernel_payoff_bound",
        "probability_authority": "qkernel_payoff_direct_route",
    }


def test_qkernel_receipt_annotation_uses_direct_no_qkernel_probability():
    """Non-selected receipt annotations record qkernel economics in the same q-space."""

    row = _row(
        condition_id="cond-qk-loosened-no",
        yes_token="yes-qk-loosened-no",
        no_token="no-qk-loosened-no",
        yes_ask=0.36,
        no_ask=0.65,
        snapshot_id="snap-qk-loosened-no",
    )
    proof = _proof(
        direction="buy_no",
        row=row,
        token_id="no-qk-loosened-no",
        q_posterior=0.9154395759428866,
        q_lcb_5pct=0.90,
        bin_obj=Bin(low=29.0, high=29.0, unit="C", label="29C"),
    )
    cert = {
        "source": "qkernel_spine",
        "decision_id": "decision-qk-loosened-no",
        "receipt_hash": "receipt-qk-loosened-no",
        "candidate_id": "NO:b29:DIRECT_NO:b29@proof",
        "route_id": "DIRECT_NO:b29@proof",
        "side": "NO",
        "bin_id": era._candidate_bin_id(proof),
        "payoff_q_point": 0.9154395759428866,
        "payoff_q_lcb": 0.8722649958749307,
        "edge_lcb": 0.22226499587493073,
        "point_ev": 0.26543957594288655,
        "delta_u_at_min": 0.00001,
        "optimal_stake_usd": "20.0",
        "optimal_delta_u": 0.012,
        "q_dot_payoff": 0.9154395759428866,
        "cost": 0.65,
        "direction_law_ok": True,
        "coherence_allows": True,
        "q_lcb_guard_basis": "OOF_WILSON_95",
        "q_lcb_guard_abstained": False,
        "q_lcb_guard_cell_key": "high|L2_3|NO|nonmodal|qb8|coarse_global",
        "selection_guard_basis": "SELECTION_BETA_95",
        "selection_guard_abstained": False,
        "selection_guard_cell_key": "NO|L2_3|nonmodal|pb8",
        "selection_guard_n": 80,
        "selection_guard_q_safe": 0.85,
    }

    (annotated,) = era._proofs_with_qkernel_candidate_economics(
        proofs=(proof,),
        qkernel_economics_by_bin_side={(era._candidate_bin_id(proof), "NO"): cert},
    )

    assert annotated.passed_prefilter is True
    assert annotated.trade_score == pytest.approx(0.22226499587493073)
    assert annotated.missing_reason is None
    assert annotated.q_posterior == pytest.approx(0.9154395759428866)
    assert annotated.q_lcb_5pct == pytest.approx(0.8722649958749307)
    assert annotated.qkernel_execution_economics["probability_authority"] == (
        "qkernel_payoff_direct_route"
    )


def test_qkernel_receipt_annotation_keeps_live_positive_profit_roi_frontier_candidate():
    """Receipt annotation must not preserve the removed 5% direct-ROI hard hurdle.

    Regression: live 2026-06-30 produced NO_ROI_FRONTIER_USEFUL_CANDIDATE for a candidate
    with positive edge_lcb, positive DeltaU, positive min-order DeltaU, q_lcb around 0.779,
    and about $2.13 lower-bound profit. The selected-spine engine now admits this shape into
    the ROI frontier, so the non-selected receipt annotation must use the same predicate.
    """

    row = _row(
        condition_id="cond-qk-live-profit-no",
        yes_token="yes-qk-live-profit-no",
        no_token="no-qk-live-profit-no",
        yes_ask=0.25,
        no_ask=0.76,
        snapshot_id="snap-qk-live-profit-no",
    )
    proof = _proof(
        direction="buy_no",
        row=row,
        token_id="no-qk-live-profit-no",
        q_posterior=0.85972,
        q_lcb_5pct=0.77877,
        bin_obj=Bin(low=25.0, high=25.0, unit="C", label="25C"),
    )
    cert = {
        "source": "qkernel_spine",
        "decision_id": "decision-qk-live-profit-no",
        "receipt_hash": "receipt-qk-live-profit-no",
        "candidate_id": "NO:b25:DIRECT_NO:b25@proof",
        "route_id": "DIRECT_NO:b25@proof",
        "side": "NO",
        "bin_id": era._candidate_bin_id(proof),
        "payoff_q_point": 0.85972,
        "payoff_q_lcb": 0.77877,
        "edge_lcb": 0.01877,
        "point_ev": 0.09972,
        "delta_u_at_min": 0.000083,
        "optimal_stake_usd": "86.2839573930664062500",
        "optimal_delta_u": 0.000983,
        "q_dot_payoff": 0.85972,
        "cost": 0.76,
        "direction_law_ok": True,
        "coherence_allows": True,
        "q_lcb_guard_basis": "OOF_WILSON_95",
        "q_lcb_guard_abstained": False,
        "q_lcb_guard_cell_key": "high|L2_3|NO|nonmodal|qb7|coarse_global",
    }

    (annotated,) = era._proofs_with_qkernel_candidate_economics(
        proofs=(proof,),
        qkernel_economics_by_bin_side={(era._candidate_bin_id(proof), "NO"): cert},
    )

    assert annotated.passed_prefilter is True
    assert annotated.missing_reason is None
    assert annotated.trade_score == pytest.approx(0.01877)
    assert annotated.q_lcb_5pct == pytest.approx(0.77877)


def test_overlay_rejects_qkernel_selected_yes_without_direction_law():
    """A non-native YES cannot become live through qkernel overlay without direction law."""

    economics = _selected_economics(
        edge_lcb=0.05, cost=0.01, q_dot_payoff=0.06, point_ev=0.05, side="YES"
    )

    assert (
        _overlay_proof(
            q_posterior=0.06,
            q_lcb_5pct=0.06,
            economics=economics,
            direction="buy_yes",
            missing_reason="ADMISSION_CAPITAL_EFFICIENCY_LCB_EV",
            direction_law_ok=False,
            coherence_allows=True,
            q_lcb_guard_abstained=True,
            q_lcb_guard_cell_key="",
        )
        is None
    )


def test_overlay_rejects_oof_reliability_direction_override():
    """OOF reliability cannot turn a structurally illegal route into a live proof."""

    economics = _selected_economics(
        edge_lcb=0.05, cost=0.01, q_dot_payoff=0.06, point_ev=0.05, side="YES"
    )

    assert (
        _overlay_proof(
            q_posterior=0.06,
            q_lcb_5pct=0.06,
            economics=economics,
            direction="buy_yes",
            missing_reason="ADMISSION_CAPITAL_EFFICIENCY_LCB_EV",
            direction_law_ok=False,
            coherence_allows=True,
            q_lcb_guard_cell_key="high|L2_3|YES|nonmodal|qb2|coarse_global",
        )
        is None
    )


def test_overlay_rejects_nonfinite_qkernel_execution_economics():
    """A non-finite qkernel value must not reach canonical JSON receipt hashing."""

    from dataclasses import replace

    economics = replace(
        _selected_economics(
            edge_lcb=0.05, cost=0.002, q_dot_payoff=0.202, point_ev=0.200
        ),
        delta_u_at_min=float("-inf"),
    )

    assert (
        _overlay_proof(q_posterior=0.80, q_lcb_5pct=0.990, economics=economics)
        is None
    )


def test_overlay_requires_first_class_payoff_q_lcb():
    """The bridge must not reverse-derive route qLCB from edge_lcb + cost."""

    from dataclasses import replace

    economics = replace(
        _selected_economics(
            edge_lcb=0.05, cost=0.002, q_dot_payoff=0.202, point_ev=0.200
        ),
        payoff_q_lcb=None,
    )

    assert (
        _overlay_proof(q_posterior=0.80, q_lcb_5pct=0.052, economics=economics)
        is None
    )


def test_overlay_rejects_payoff_q_lcb_edge_split():
    """The qkernel cert qLCB and edge must be one coherent pair."""

    from dataclasses import replace

    economics = replace(
        _selected_economics(
            edge_lcb=0.05, cost=0.002, q_dot_payoff=0.202, point_ev=0.200
        ),
        payoff_q_lcb=0.040,
    )

    assert (
        _overlay_proof(q_posterior=0.80, q_lcb_5pct=0.040, economics=economics)
        is None
    )


def test_qkernel_execution_economics_requires_direction_law_and_coherence():
    """Tokyo-class regression: positive qkernel edge is not enough without live structural proofs."""

    cert = {
        "source": "qkernel_spine",
        "candidate_id": "YES:b24:DIRECT_YES:b24@proof",
        "route_id": "DIRECT_YES:b24@proof",
        "side": "YES",
        "bin_id": "b24",
        "payoff_q_point": 0.20,
        "payoff_q_lcb": 0.137,
        "edge_lcb": 0.132,
        "delta_u_at_min": 0.001,
        "optimal_stake_usd": "1.50",
        "optimal_delta_u": 0.02,
        "cost": 0.005,
        "false_edge_rate": 0.05,
        "q_lcb_guard_basis": "OOF_WILSON_95",
        "q_lcb_guard_abstained": False,
        "q_lcb_guard_cell_key": "low|L2_3|YES|nonmodal|qb2|coarse_global",
        "selection_guard_basis": "SELECTION_BETA_95",
        "selection_guard_abstained": False,
        "selection_guard_cell_key": "YES|L2_3|modal|pb2",
        "selection_guard_n": 80,
        "selection_guard_q_safe": 0.137,
        "direction_law_ok": False,
        "coherence_allows": True,
    }

    assert era._valid_qkernel_execution_economics_payload(
        {
            **cert,
            "q_lcb_guard_abstained": True,
            "q_lcb_guard_cell_key": "",
        },
        direction="buy_yes",
    ) is None
    assert era._valid_qkernel_execution_economics_payload(
        {**cert, "direction_law_ok": True},
        direction="buy_yes",
    ) is not None
    assert era._valid_qkernel_execution_economics_payload(
        {
            **cert,
            "direction_law_ok": False,
            "q_lcb_guard_basis": "OOF_WILSON_95",
            "q_lcb_guard_abstained": False,
            "q_lcb_guard_cell_key": "high|L2_3|YES|nonmodal|qb2|coarse_global",
        },
        direction="buy_yes",
    ) is None
    assert era._valid_qkernel_execution_economics_payload(
        {
            **cert,
            "candidate_id": "NO:b24:DIRECT_NO:b24@proof",
            "route_id": "DIRECT_NO:b24@proof",
            "side": "NO",
            "direction_law_ok": False,
            "q_lcb_guard_basis": "OOF_WILSON_95",
            "q_lcb_guard_abstained": False,
            "q_lcb_guard_cell_key": "high|L2_3|NO|nonmodal|qb2|coarse_global",
        },
        direction="buy_no",
    ) is None
    assert era._valid_qkernel_execution_economics_payload(
        {**cert, "direction_law_ok": True, "coherence_allows": False},
        direction="buy_yes",
    ) is None


def test_qkernel_direct_route_receipt_probability_must_match_monitor_belief():
    """Jeddah-class regression: direct NO cannot size on qkernel q above receipt q."""

    from types import SimpleNamespace

    receipt = SimpleNamespace(q_live=0.986261171798223, q_lcb_5pct=0.986261171798223)
    cert = {
        "route_id": "DIRECT_NO:b24@proof",
        "payoff_q_point": 0.9999999257352632,
        "payoff_q_lcb": 0.998678563135879,
    }

    assert era._qkernel_direct_route_matches_receipt_probability(receipt, cert) is False
    assert era._qkernel_direct_route_matches_receipt_probability(
        receipt,
        {**cert, "payoff_q_point": receipt.q_live, "payoff_q_lcb": receipt.q_lcb_5pct},
    ) is True


def test_overlay_clears_legacy_rounded_mu_direction_veto_for_qkernel_selected_candidate():
    """A spine-positive route may clear the old rounded-mu direction veto.

    ``DIRECTION_LAW_BIN_FORECAST_MISMATCH`` was a pre-spine modal-bin heuristic. The
    qkernel selector owns payoff-vector economics now, so a selected direct native route
    with positive edge/DeltaU must not stay blocked by that legacy missing reason.
    """
    economics = _selected_economics(
        edge_lcb=0.05, cost=0.27, q_dot_payoff=0.32, point_ev=0.20, side="YES"
    )
    new_proof = _overlay_proof(
        q_posterior=0.32,
        q_lcb_5pct=0.32,
        economics=economics,
        direction="buy_yes",
        missing_reason="DIRECTION_LAW_BIN_FORECAST_MISMATCH:legacy-pre-spine",
    )

    assert new_proof is not None
    assert new_proof.missing_reason is None
    assert new_proof.passed_prefilter is True
    assert new_proof.selection_authority_applied == "qkernel_spine"


def test_overlay_clears_scalar_admission_missing_reason_for_qkernel_selected_candidate():
    """A spine-positive selected proof may clear stale scalar admission vetoes."""
    economics = _selected_economics(
        edge_lcb=0.05, cost=0.27, q_dot_payoff=0.32, point_ev=0.20, side="YES"
    )
    new_proof = _overlay_proof(
        q_posterior=0.32,
        q_lcb_5pct=0.32,
        economics=economics,
        direction="buy_yes",
        missing_reason="ADMISSION_CAPITAL_EFFICIENCY_LCB_EV:legacy-pre-spine",
    )

    assert new_proof is not None
    assert new_proof.missing_reason is None
    assert new_proof.passed_prefilter is True
    assert new_proof.trade_score == pytest.approx(0.05)
    assert new_proof.selection_authority_applied == "qkernel_spine"


def test_overlay_refuses_to_clear_center_buy_ultra_low_live_blocker():
    """qkernel may rescore stale scalar vetoes, not live strategy policy blockers."""
    economics = _selected_economics(
        edge_lcb=0.05, cost=0.015, q_dot_payoff=0.08, point_ev=0.20, side="YES"
    )
    new_proof = _overlay_proof(
        q_posterior=0.08,
        q_lcb_5pct=0.08,
        economics=economics,
        direction="buy_yes",
        missing_reason="CENTER_BUY_ULTRA_LOW_PRICE(0.0150<=0.02)",
    )

    assert new_proof is None


def test_overlay_allows_center_buy_yes_below_strategy_floor_without_legacy_blocker():
    """Qkernel selection does not hide cheap YES before submit authority sees it."""
    economics = _selected_economics(
        edge_lcb=0.05, cost=0.015, q_dot_payoff=0.08, point_ev=0.20, side="YES"
    )
    new_proof = _overlay_proof(
        q_posterior=0.08,
        q_lcb_5pct=0.08,
        economics=economics,
        direction="buy_yes",
    )

    assert new_proof is not None
    assert new_proof.selection_authority_applied == "qkernel_spine"
    assert new_proof.qkernel_execution_economics["cost"] == pytest.approx(0.015)


def test_overlay_allows_center_buy_yes_live_tail_lottery_price_for_downstream_gate():
    """Submit authority, not qkernel selection, blocks 0.0x center-buy YES live entries."""
    economics = _selected_economics(
        edge_lcb=0.05, cost=0.07, q_dot_payoff=0.15, point_ev=0.20, side="YES"
    )
    new_proof = _overlay_proof(
        q_posterior=0.15,
        q_lcb_5pct=0.15,
        economics=economics,
        direction="buy_yes",
    )

    assert new_proof is not None
    assert new_proof.selection_authority_applied == "qkernel_spine"
    assert new_proof.qkernel_execution_economics["cost"] == pytest.approx(0.07)


def test_overlay_allows_center_buy_yes_when_live_floor_clears():
    """Shanghai-style center-bin YES remains live-eligible above the tail floor."""
    economics = _selected_economics(
        edge_lcb=0.05, cost=0.27, q_dot_payoff=0.35, point_ev=0.20, side="YES"
    )
    new_proof = _overlay_proof(
        q_posterior=0.35,
        q_lcb_5pct=0.35,
        economics=economics,
        direction="buy_yes",
    )

    assert new_proof is not None
    assert new_proof.missing_reason is None
    assert new_proof.selection_authority_applied == "qkernel_spine"


def test_overlay_sets_qkernel_band_false_edge_p_value():
    """FDR consumes the selected qkernel route's empirical false-edge rate.

    A qkernel-selected proof must not keep the legacy proof p-value after the
    qkernel band has selected a different payoff-space route. The p-value is the
    finite-sample-corrected share of band route edges <= 0 when no active
    settlement reliability guard has re-authored the served lower bound.
    """
    from types import SimpleNamespace

    economics = _selected_economics(
        edge_lcb=0.01, cost=0.05, q_dot_payoff=0.08, point_ev=0.03
    )
    selected_route = SimpleNamespace(side="NO", bin_id="b1", payoff_vector=np.array([1.0]))
    selected_decision = SimpleNamespace(
        economics=economics,
        route=selected_route,
        q_lcb_guard_basis="INERT_TEST",
        q_lcb_guard_abstained=False,
        q_lcb_guard_cell_key="cell",
        direction_law_ok=True,
        coherence_allows=True,
        selection_guard_basis="UNGUARDED_TEST",
        selection_guard_abstained=False,
        selection_guard_cell_key="NO|L2_3|modal|pb1",
        selection_guard_n=80,
        selection_guard_q_safe=0.07,
    )
    decision = SimpleNamespace(
        selected=economics,
        band=SimpleNamespace(samples=np.array([[0.03], [0.06], [0.08]])),
        candidate_decisions=(selected_decision,),
    )
    base = _overlay_proof(
        q_posterior=0.08,
        q_lcb_5pct=0.06,
        economics=economics,
    )
    new_proof = bridge._overlay_spine_economics_onto_proof(base, decision)

    assert new_proof is not None
    assert new_proof.trade_score == pytest.approx(0.01)
    assert new_proof.p_value == pytest.approx(0.5)  # (one failure + 1) / (three draws + 1)
    assert new_proof.passed_prefilter is True
    assert new_proof.qkernel_execution_economics["false_edge_rate"] == pytest.approx(0.5)


def test_overlay_uses_guarded_false_edge_rate_when_guard_authors_edge():
    """FDR must consume the same guarded q_safe authority as the selected edge.

    Regression: after OOF/selection guards recomputed edge_lcb as q_safe-cost, the
    bridge still recomputed p_value from the raw q-band samples. That made live
    receipts self-contradictory: strong positive guarded edge but p=0.5, so every
    qkernel-selected route died FDR_REJECTED.
    """
    from types import SimpleNamespace

    economics = _selected_economics(
        edge_lcb=0.01, cost=0.05, q_dot_payoff=0.08, point_ev=0.03
    )
    selected_route = SimpleNamespace(side="NO", bin_id="b1", payoff_vector=np.array([1.0]))
    selected_decision = SimpleNamespace(
        economics=economics,
        route=selected_route,
        q_lcb_guard_basis="OOF_WILSON_95",
        q_lcb_guard_abstained=False,
        q_lcb_guard_cell_key="cell",
        direction_law_ok=True,
        coherence_allows=True,
        selection_guard_basis="SELECTION_BETA_95",
        selection_guard_abstained=False,
        selection_guard_cell_key="NO|L2_3|modal|pb1",
        selection_guard_n=80,
        selection_guard_q_safe=0.06,
    )
    decision = SimpleNamespace(
        selected=economics,
        band=SimpleNamespace(samples=np.array([[0.03], [0.06], [0.08]])),
        candidate_decisions=(selected_decision,),
    )
    base = _overlay_proof(
        q_posterior=0.08,
        q_lcb_5pct=0.06,
        economics=economics,
    )
    new_proof = bridge._overlay_spine_economics_onto_proof(base, decision)

    assert new_proof is not None
    assert new_proof.trade_score == pytest.approx(0.01)
    assert new_proof.p_value == pytest.approx(0.05)
    assert new_proof.passed_prefilter is True
    assert new_proof.qkernel_execution_economics["false_edge_rate"] == pytest.approx(0.05)


def test_fdr_maps_consume_selected_qkernel_overlay_authority():
    """FDR must see the selected qkernel false-edge rate, not the stale base proof p-value."""

    economics = _selected_economics(
        edge_lcb=0.05, cost=0.002, q_dot_payoff=0.052, point_ev=0.050
    )
    base = _overlay_proof(
        q_posterior=0.052,
        q_lcb_5pct=0.052,
        economics=economics,
    )
    stale_base = era.dataclass_replace(base, p_value=1.0, passed_prefilter=False)
    selected = era.dataclass_replace(base, p_value=0.00025, passed_prefilter=True)

    p_values, prefilter = era._fdr_maps_with_selected_authority(
        family_id="family-qkernel",
        proofs=(stale_base,),
        selected_proof=selected,
        selected_token_id=selected.token_id,
    )

    hypothesis_id = f"family-qkernel:{selected.token_id}"
    assert p_values[hypothesis_id] == pytest.approx(0.00025)
    assert prefilter[hypothesis_id] is True


def test_qkernel_selected_route_fdr_is_not_legacy_bh_denominator():
    """Qkernel FDR consumes the selected route's empirical false-edge rate.

    The spine selects a coherent family payoff route. Re-running legacy BH over
    many sibling binary hypotheses can reject a selected route whose own
    payoff-band false-edge rate is inside the configured FDR budget.
    """

    from dataclasses import replace

    from src.events.money_path_adapters import evaluate_fdr_full_family

    economics = _selected_economics(
        edge_lcb=0.05, cost=0.02, q_dot_payoff=0.09, point_ev=0.07
    )
    selected = _overlay_proof(
        q_posterior=0.09,
        q_lcb_5pct=0.07,
        economics=economics,
    )
    assert selected is not None
    bin_id = era._candidate_bin_id(selected)
    selected = replace(
        selected,
        p_value=0.02,
        qkernel_execution_economics={
            **selected.qkernel_execution_economics,
            "candidate_id": f"NO:{bin_id}:DIRECT_NO:{bin_id}@proof",
            "route_id": f"DIRECT_NO:{bin_id}@proof",
            "bin_id": bin_id,
            "false_edge_rate": 0.02,
            "direction_law_ok": True,
            "coherence_allows": True,
        },
    )
    family_id = "family-qkernel-route"
    selected_hypothesis_id = f"{family_id}:{selected.token_id}"
    all_hypothesis_ids = (selected_hypothesis_id,) + tuple(
        f"{family_id}:sibling-{idx}" for idx in range(19)
    )
    p_values = {hypothesis_id: 1.0 for hypothesis_id in all_hypothesis_ids}
    p_values[selected_hypothesis_id] = 0.02
    prefilter = {hypothesis_id: True for hypothesis_id in all_hypothesis_ids}

    legacy_bh = evaluate_fdr_full_family(
        family_id=family_id,
        all_hypothesis_ids=all_hypothesis_ids,
        selected_hypothesis_ids=(selected_hypothesis_id,),
        hypothesis_p_values=p_values,
        passed_prefilter=prefilter,
    )
    qkernel_fdr = era._qkernel_selected_route_fdr_proof(
        family_id=family_id,
        all_hypothesis_ids=all_hypothesis_ids,
        selected_hypothesis_id=selected_hypothesis_id,
        selected_proof=selected,
    )

    assert legacy_bh.passed is False
    assert qkernel_fdr is not None
    assert qkernel_fdr.passed is True
    assert qkernel_fdr.selected_post_fdr == (selected_hypothesis_id,)


def test_qkernel_selected_route_fdr_rejects_high_false_edge_rate():
    """The qkernel route gate remains fail-closed when the payoff band is weak."""

    from dataclasses import replace

    economics = _selected_economics(
        edge_lcb=0.05, cost=0.02, q_dot_payoff=0.09, point_ev=0.07
    )
    selected = _overlay_proof(
        q_posterior=0.09,
        q_lcb_5pct=0.07,
        economics=economics,
    )
    assert selected is not None
    bin_id = era._candidate_bin_id(selected)
    selected = replace(
        selected,
        p_value=0.50,
        qkernel_execution_economics={
            **selected.qkernel_execution_economics,
            "candidate_id": f"NO:{bin_id}:DIRECT_NO:{bin_id}@proof",
            "route_id": f"DIRECT_NO:{bin_id}@proof",
            "bin_id": bin_id,
            "false_edge_rate": 0.50,
            "direction_law_ok": True,
            "coherence_allows": True,
        },
    )
    family_id = "family-qkernel-route"
    selected_hypothesis_id = f"{family_id}:{selected.token_id}"

    qkernel_fdr = era._qkernel_selected_route_fdr_proof(
        family_id=family_id,
        all_hypothesis_ids=(selected_hypothesis_id,),
        selected_hypothesis_id=selected_hypothesis_id,
        selected_proof=selected,
    )

    assert qkernel_fdr is not None
    assert qkernel_fdr.passed is False
    assert qkernel_fdr.selected_post_fdr == ()


def test_day0_selected_route_fdr_uses_hard_fact_q_lcb_not_legacy_p_value():
    """Day0 observation routes must not be rejected by legacy p-value machinery.

    A same-day observed-fact route is one family decision conditioned on the running
    extreme. Legacy BH over every sibling YES/NO hypothesis can reject a selected
    Day0 route even when the selected hard-fact q_lcb implies the selected route's
    false-edge probability is inside the FDR budget.
    """

    from dataclasses import replace

    from src.events.money_path_adapters import evaluate_fdr_full_family

    row = _row(
        condition_id="cond-day0",
        yes_token="yes-day0",
        no_token="no-day0",
        yes_ask=0.70,
        no_ask=0.34,
        snapshot_id="snap-day0",
    )
    selected = _proof(
        direction="buy_yes",
        row=row,
        token_id="yes-day0",
        q_posterior=0.959,
        q_lcb_5pct=0.952,
        bin_obj=Bin(low=12.0, high=12.0, unit="C", label="12C"),
        execution_price=0.70,
    )
    selected = replace(
        selected,
        p_value=0.50,
        passed_prefilter=True,
        trade_score=0.238,
        qkernel_execution_economics=None,
        probability_authority="day0_absorbing_hard_fact",
    )
    family_id = "family-day0-route"
    selected_hypothesis_id = f"{family_id}:{selected.token_id}"
    all_hypothesis_ids = (selected_hypothesis_id,) + tuple(
        f"{family_id}:sibling-{idx}" for idx in range(21)
    )
    p_values = {hypothesis_id: 1.0 for hypothesis_id in all_hypothesis_ids}
    p_values[selected_hypothesis_id] = 0.50
    prefilter = {hypothesis_id: True for hypothesis_id in all_hypothesis_ids}

    legacy_bh = evaluate_fdr_full_family(
        family_id=family_id,
        all_hypothesis_ids=all_hypothesis_ids,
        selected_hypothesis_ids=(selected_hypothesis_id,),
        hypothesis_p_values=p_values,
        passed_prefilter=prefilter,
    )
    day0_fdr = era._day0_selected_route_fdr_proof(
        event_type="DAY0_EXTREME_UPDATED",
        family_id=family_id,
        all_hypothesis_ids=all_hypothesis_ids,
        selected_hypothesis_id=selected_hypothesis_id,
        selected_proof=selected,
    )

    assert legacy_bh.passed is False
    assert day0_fdr is not None
    assert day0_fdr.passed is True
    assert day0_fdr.selected_post_fdr == (selected_hypothesis_id,)


def test_day0_selected_route_fdr_rejects_high_false_edge_rate():
    """Day0 route-FDR remains fail-closed when selected evidence is weak."""

    from dataclasses import replace

    row = _row(
        condition_id="cond-day0-weak",
        yes_token="yes-day0-weak",
        no_token="no-day0-weak",
        yes_ask=0.70,
        no_ask=0.34,
        snapshot_id="snap-day0-weak",
    )
    selected = _proof(
        direction="buy_yes",
        row=row,
        token_id="yes-day0-weak",
        q_posterior=0.80,
        q_lcb_5pct=0.74,
        bin_obj=Bin(low=12.0, high=12.0, unit="C", label="12C"),
        execution_price=0.70,
    )
    selected = replace(
        selected,
        p_value=0.50,
        passed_prefilter=True,
        trade_score=0.04,
        probability_authority="day0_absorbing_hard_fact",
    )
    family_id = "family-day0-weak"
    selected_hypothesis_id = f"{family_id}:{selected.token_id}"

    day0_fdr = era._day0_selected_route_fdr_proof(
        event_type="DAY0_EXTREME_UPDATED",
        family_id=family_id,
        all_hypothesis_ids=(selected_hypothesis_id,),
        selected_hypothesis_id=selected_hypothesis_id,
        selected_proof=selected,
    )

    assert day0_fdr is not None
    assert day0_fdr.passed is False
    assert day0_fdr.selected_post_fdr == ()


def test_overlay_failure_returns_none_instead_of_original_proof():
    """A qkernel overlay wiring fault must become no-trade, not an unguarded proof."""
    from types import SimpleNamespace

    economics = _selected_economics(
        edge_lcb=0.05, cost=0.002, q_dot_payoff=0.052, point_ev=0.050
    )
    proof = SimpleNamespace(
        q_source=None,
        q_posterior=0.80,
        q_lcb_5pct=0.99,
        trade_score=0.01,
    )
    decision = SimpleNamespace(selected=economics, candidate_decisions=())

    assert bridge._overlay_spine_economics_onto_proof(proof, decision) is None


def test_overlay_does_not_create_milan_buy_yes_probability_contradiction():
    """A payoff-space NO-like lower bound must not overwrite buy_yes probability fields."""
    economics = _selected_economics(
        edge_lcb=0.3857045133438944,
        cost=0.41,
        q_dot_payoff=0.199009684818666,
        point_ev=-0.220821533,
        side="YES",
    )
    new_proof = _overlay_proof(
        q_posterior=0.199009684818666,
        q_lcb_5pct=0.04625961651748593,
        economics=economics,
        direction="buy_yes",
    )

    assert new_proof is None


def test_qkernel_scope_does_not_let_legacy_admission_filter_center_yes(monkeypatch, tmp_path):
    """The spine must rank executable family legs itself, not inherit legacy vetoes.

    Regression: qkernel was called after `_selection_scoped_proofs`, which filtered
    every proof with a legacy `missing_reason`. That meant an old capital/FDR veto
    could remove the center YES before the payoff-vector selector ever saw it,
    preserving the all-NO behavior the spine was introduced to replace.
    """
    from dataclasses import replace
    from src.decision import qlcb_reliability_guard as guard_mod

    _install_sigma_floor_artifact(monkeypatch, tmp_path)
    reliability_cells = _fully_licensed_reliability_cells(guard_mod)
    monkeypatch.setattr(guard_mod, "_RELIABILITY_CACHE", reliability_cells)
    monkeypatch.setattr(guard_mod, "_RELIABILITY_LOADED", True)
    monkeypatch.setattr(guard_mod, "_RELIABILITY_ARTIFACT_ACTIVE", True)

    family, _bins = _three_bin_family()
    proofs = _proofs_for(
        family,
        yes_asks=[0.90, 0.05, 0.90, 0.90],
        no_asks=[0.79, 0.90, 0.80, 0.95],
        q_by_bin=[0.10, 0.80, 0.10, 0.00],
        q_lcb_by_bin=[0.08, 0.65, 0.08, 0.00],
        no_execution_prices=[0.79, 0.90, 0.80, 0.95],
    )
    marked = tuple(
        replace(
            proof,
            missing_reason="ADMISSION_CAPITAL_EFFICIENCY_LCB_EV:legacy-pre-spine",
            passed_prefilter=False,
            trade_score=0.0,
        )
        if proof.direction == "buy_yes" and proof.candidate.bin.label == "20C"
        else proof
        for proof in proofs
    )

    legacy_scoped = era._selection_scoped_proofs(
        proofs=marked,
        honor_admission_rejections=True,
    )
    qkernel_scoped = era._selection_scoped_proofs(
        proofs=marked,
        honor_admission_rejections=False,
    )

    assert all(
        not (proof.direction == "buy_yes" and proof.candidate.bin.label == "20C")
        for proof in legacy_scoped
    )
    assert any(
        proof.direction == "buy_yes" and proof.candidate.bin.label == "20C"
        for proof in qkernel_scoped
    )

    res = _drive(
        family,
        qkernel_scoped,
        _payload(mu=20.0, sigma=0.1, members=[20, 20, 20, 20, 20]),
    )

    center_yes = [
        decision
        for decision in res.decision.candidate_decisions
        if decision.route.side == "YES"
        and decision.route.bin_id == era._candidate_bin_id(
            next(
                proof
                for proof in qkernel_scoped
                if proof.direction == "buy_yes" and proof.candidate.bin.label == "20C"
            )
        )
    ]

    assert center_yes
    assert center_yes[0].economics.edge_lcb > 0.0
    assert center_yes[0].economics.optimal_delta_u > 0.0


def test_qkernel_scope_uses_roi_not_legacy_win_rate_floor_for_yes():
    """Qkernel can score low-cost YES by ROI instead of legacy q_lcb >= 51% admission."""

    family, bins = _three_bin_family()
    row = _row(
        condition_id="cond-center",
        yes_token="yes-center",
        no_token="no-center",
        yes_ask=0.27,
        no_ask=0.73,
        snapshot_id="snap-center",
    )
    center_yes = _proof(
        direction="buy_yes",
        row=row,
        token_id="yes-center",
        q_posterior=0.20,
        q_lcb_5pct=0.20,
        bin_obj=bins[1],
        execution_price=0.27,
    )

    legacy_scoped = era._selection_scoped_proofs(proofs=(center_yes,))
    qkernel_scoped = era._selection_scoped_proofs(
        proofs=(center_yes,),
        honor_admission_rejections=False,
        enforce_win_rate_floor=False,
    )

    assert legacy_scoped == ()
    assert qkernel_scoped == (center_yes,)
    assert family.family_id


def test_qkernel_scope_allows_center_yes_below_static_price_floor_for_optimizer():
    """Qkernel selection must see low-price YES; evidence gates decide quality."""

    _family, bins = _three_bin_family()
    row = _row(
        condition_id="cond-cheap",
        yes_token="yes-cheap",
        no_token="no-cheap",
        yes_ask=0.01,
        no_ask=0.99,
        snapshot_id="snap-cheap",
    )
    cheap_yes = _proof(
        direction="buy_yes",
        row=row,
        token_id="yes-cheap",
        q_posterior=0.20,
        q_lcb_5pct=0.20,
        bin_obj=bins[1],
        execution_price=0.01,
    )

    qkernel_scoped = era._selection_scoped_proofs(
        proofs=(cheap_yes,),
        honor_admission_rejections=False,
        enforce_win_rate_floor=False,
    )

    assert qkernel_scoped == (cheap_yes,)


def test_qkernel_scope_rescores_legacy_direction_veto_but_still_honors_coherence():
    """Old rounded-mu direction vetoes may enter qkernel, then fail on real gates.

    The old ``DIRECTION_LAW_BIN_FORECAST_MISMATCH`` reason is not structural authority.
    It must not delete a proof before qkernel can score the family. Market coherence must
    still block the offending bins, while non-offending live-selectable bins remain eligible.
    """
    from dataclasses import replace

    family, _bins = _three_bin_family()
    proofs = _proofs_for(
        family,
        yes_asks=[0.90, 0.27, 0.90, 0.90],
        no_asks=[0.79, 0.90, 0.80, 0.95],
        q_by_bin=[0.10, 0.80, 0.10, 0.00],
        q_lcb_by_bin=[0.08, 0.65, 0.08, 0.00],
        no_execution_prices=[0.79, 0.90, 0.80, 0.95],
    )
    marked = tuple(
        replace(
            proof,
            missing_reason=(
                "DIRECTION_LAW_BIN_FORECAST_MISMATCH:"
                "direction=buy_no:forecast_boundary_zone"
            ),
            passed_prefilter=False,
            trade_score=0.0,
        )
        if proof.direction == "buy_no" and proof.candidate.bin.label == "21C"
        else proof
        for proof in proofs
    )

    qkernel_scoped = era._selection_scoped_proofs(
        proofs=marked,
        honor_admission_rejections=False,
    )

    assert any(
        proof.direction == "buy_no" and proof.candidate.bin.label == "21C"
        for proof in qkernel_scoped
    )
    assert any(
        proof.direction == "buy_yes" and proof.candidate.bin.label == "20C"
        for proof in qkernel_scoped
    )

    res = _drive(
        family,
        qkernel_scoped,
        _payload(mu=20.0, sigma=0.1, members=[20, 20, 20, 20, 20]),
    )
    legacy_vetoed_bin_id = era._candidate_bin_id(
        next(
            proof
            for proof in qkernel_scoped
            if proof.direction == "buy_no" and proof.candidate.bin.label == "21C"
        )
    )

    assert res.decision is not None
    legacy_vetoed_decisions = [
        decision
        for decision in res.decision.candidate_decisions
        if decision.route.side == "NO" and decision.route.bin_id == legacy_vetoed_bin_id
    ]
    assert legacy_vetoed_decisions
    assert all(decision.direction_law_ok is True for decision in legacy_vetoed_decisions)
    assert res.decision.market_coherence.status == "INCOHERENT_BLOCK_LIVE"
    assert res.decision.market_coherence.offending_bins
    assert any(
        decision.coherence_allows is False
        for decision in res.decision.candidate_decisions
        if decision.route.bin_id in res.decision.market_coherence.offending_bins
    )
    assert res.selected_proof is not None
    assert res.no_trade_reason is None
    assert era._candidate_bin_id(res.selected_proof) not in res.decision.market_coherence.offending_bins
    selected_decision = next(
        decision
        for decision in res.decision.candidate_decisions
        if decision.route.bin_id == era._candidate_bin_id(res.selected_proof)
        and decision.route.side == ("YES" if res.selected_proof.direction == "buy_yes" else "NO")
    )
    assert selected_decision.coherence_allows is True


def test_qkernel_rehydrates_served_proof_q_instead_of_reintegrating_member_normal():
    """Live qkernel must score the same point q served by admission proof.

    Regression: the bridge formerly fed proof/admission from the replacement posterior but
    let FamilyDecisionEngine rebuild ``joint_q`` from raw members + sigma.  A narrow member
    Normal around 20C then produced a different point q than the proof vector, tripping
    QKERNEL_SERVED_BELIEF_POINT_MISMATCH and leaving live entry stalled.  The bridge now
    rehydrates the served proof vector into ``decision.joint_q`` and injects each
    side-specific proof q_lcb into candidate economics.
    """

    family, _bins = _three_bin_family()
    served_yes_q = [0.10, 0.80, 0.10, 0.00]
    served_yes_lcb = [0.08, 0.65, 0.08, 0.00]
    proofs = _proofs_for(
        family,
        yes_asks=[0.90, 0.27, 0.90, 0.90],
        no_asks=[0.79, 0.90, 0.80, 0.95],
        q_by_bin=served_yes_q,
        q_lcb_by_bin=served_yes_lcb,
        no_execution_prices=[0.79, 0.90, 0.80, 0.95],
    )

    res = _drive(
        family,
        proofs,
        _payload(mu=20.0, sigma=0.05, members=[20.0, 20.0, 20.0, 20.0, 20.0]),
    )

    assert res.decision is not None
    assert res.decision.joint_q is not None
    assert list(res.decision.joint_q.q) == pytest.approx(served_yes_q)
    for decision in res.decision.candidate_decisions:
        proof = next(
            p
            for p in proofs
            if era._candidate_bin_id(p) == decision.route.bin_id
            and (("YES" if p.direction == "buy_yes" else "NO") == decision.route.side)
        )
        assert decision.economics.q_dot_payoff == pytest.approx(float(proof.q_posterior))
        assert decision.economics.payoff_q_lcb <= float(proof.q_lcb_5pct) + 1e-9


def test_qkernel_modal_guards_follow_served_joint_q_not_predictive_mu(monkeypatch):
    """Modal/nonmodal guard cells must use the same served q surface as selection.

    Regression: the bridge rehydrated qkernel point probabilities from proof posterior
    but ``FamilyDecisionEngine`` still keyed modal YES guard cells from
    ``predictive.mu_native``. When those disagreed, a high-quality center YES could be
    treated as nonmodal/sparse while adjacent NO substitutes stayed licensed.
    """

    from src.decision import family_decision_engine as fde
    from src.decision import qlcb_reliability_guard as guard_mod
    from src.decision.selection_calibrator import CalibratorVerdict

    reliability_cells = _fully_licensed_reliability_cells(guard_mod)
    monkeypatch.setattr(guard_mod, "_RELIABILITY_CACHE", reliability_cells)
    monkeypatch.setattr(guard_mod, "_RELIABILITY_LOADED", True)
    monkeypatch.setattr(guard_mod, "_RELIABILITY_ARTIFACT_ACTIVE", True)

    def _selection_guard(*, raw_side_prob, side, lead_days, bin_class, admission_margin=None):
        cell = f"{str(side).upper()}|L2_3|{bin_class}|test"
        if str(side).upper() == "YES" and bin_class != "modal":
            return CalibratorVerdict(
                q_safe=0.0,
                trade=False,
                abstained=True,
                cell_key=cell,
                L_g=0.0,
                n_g=0,
                basis="SIDE_NOT_ARMED",
            )
        return CalibratorVerdict(
            q_safe=float(raw_side_prob),
            trade=True,
            abstained=False,
            cell_key=cell,
            L_g=float(raw_side_prob),
            n_g=1000,
            basis="SELECTION_BETA_95",
        )

    monkeypatch.setattr(fde, "apply_selection_calibrator", _selection_guard)

    family, _bins = _three_bin_family()
    served_yes_q = [0.10, 0.80, 0.10, 0.00]
    served_yes_lcb = [0.08, 0.65, 0.08, 0.00]
    proofs = _proofs_for(
        family,
        yes_asks=[0.90, 0.27, 0.90, 0.90],
        no_asks=[0.79, 0.90, 0.80, 0.95],
        q_by_bin=served_yes_q,
        q_lcb_by_bin=served_yes_lcb,
        no_execution_prices=[0.79, 0.90, 0.80, 0.95],
    )

    # Predictive center points at 21C, but the served posterior's modal bin is 20C.
    res = _drive(
        family,
        proofs,
        _payload(mu=21.0, sigma=0.05, members=[21.0, 21.0, 21.0, 21.0, 21.0]),
    )

    assert res.no_trade_reason is None
    assert res.selected_proof is not None
    assert res.selected_proof.direction == "buy_yes"
    assert res.selected_proof.candidate.bin.label == "20C"
    selected_decision = next(
        d for d in res.decision.candidate_decisions
        if d.economics.candidate_id == res.decision.selected.candidate_id
    )
    assert selected_decision.selection_guard_cell_key == "YES|L2_3|modal|test"


def test_qkernel_belief_rehydration_uses_full_family_not_selection_scoped_subset():
    """Selection filtering must not delete bins from the served belief vector.

    Live regression: the adapter passed `_selection_scoped_proofs` as the only spine
    proof input. If selection scoping removed both sides of one family bin (locked,
    held, limit-untradeable, etc.), `_served_joint_belief_from_proofs` saw an
    incomplete Omega and blocked the whole family with SERVED_BELIEF_Q_MISSING.
    The full proof tuple is the probability authority; the scoped tuple is only
    the executable route surface.
    """

    family, _bins = _three_bin_family()
    served_yes_q = [0.10, 0.80, 0.10, 0.00]
    served_yes_lcb = [0.08, 0.65, 0.08, 0.00]
    proofs = _proofs_for(
        family,
        yes_asks=[0.90, 0.27, 0.90, 0.90],
        no_asks=[0.79, 0.90, 0.80, 0.95],
        q_by_bin=served_yes_q,
        q_lcb_by_bin=served_yes_lcb,
        no_execution_prices=[0.79, 0.90, 0.80, 0.95],
    )
    selection_proofs = tuple(
        proof for proof in proofs if proof.candidate.bin.label != "22C or above"
    )

    res = _drive(
        family,
        proofs,
        _payload(mu=20.0, sigma=0.1, members=[20, 20, 20, 20, 20]),
        selection_proofs=selection_proofs,
    )

    assert res.decision is not None
    assert list(res.decision.joint_q.q) == pytest.approx(served_yes_q)
    assert "SERVED_BELIEF_Q_MISSING" not in str(res.no_trade_reason or "")
