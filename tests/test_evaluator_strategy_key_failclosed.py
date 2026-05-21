# Created: 2026-05-02
# Last reused/audited: 2026-05-21
# Lifecycle: created=2026-05-02; last_reviewed=2026-05-21; last_reused=2026-05-21
# Purpose: Lock durable strategy_key classification and fail-closed behavior across evaluator/runtime persistence boundaries.
# Reuse: Run before changing evaluator strategy_key mapping, discovery-mode classification, or strategy_key DB persistence.
# Authority basis: PR44 review comment 3177316079 / 3177317099 strategy_key unclassified fail-closed contract;
#                  2026-05-21 live CHECK repair: discovery modes must not invent strategy_key values.

from __future__ import annotations

import sqlite3

from src.config import City
from src.engine.discovery_mode import DiscoveryMode
from src.engine import cycle_runner
from src.engine.evaluator import (
    MarketCandidate,
    _edge_source_for,
    _record_selection_family_facts,
    _strategy_key_for,
    _strategy_key_for_hypothesis,
)
from src.strategy.market_analysis_family_scan import FullFamilyHypothesis
from src.types.market import Bin, BinEdge


def _city() -> City:
    return City(
        name="NYC",
        lat=40.7772,
        lon=-73.8726,
        timezone="America/New_York",
        cluster="NYC",
        settlement_unit="F",
        wu_station="KLGA",
    )


def _candidate(discovery_mode: str = DiscoveryMode.UPDATE_REACTION.value) -> MarketCandidate:
    return MarketCandidate(
        city=_city(),
        target_date="2026-05-03",
        outcomes=[],
        hours_since_open=30.0,
        temperature_metric="high",
        discovery_mode=discovery_mode,
    )


def _unclassified_edge() -> BinEdge:
    return BinEdge(
        bin=Bin(70, 71, "F", "70-71°F"),
        direction="buy_no",
        edge=0.08,
        ci_lower=0.02,
        ci_upper=0.12,
        p_model=0.40,
        p_market=0.52,
        p_posterior=0.40,
        entry_price=0.48,
        p_value=0.01,
        vwmp=0.52,
        support_index=1,
    )


def _shoulder_buy_edge() -> BinEdge:
    return BinEdge(
        bin=Bin(low=None, high=38, label="38°F or below", unit="F"),
        direction="buy_yes",
        edge=0.08,
        ci_lower=0.02,
        ci_upper=0.12,
        p_model=0.40,
        p_market=0.52,
        p_posterior=0.40,
        entry_price=0.48,
        p_value=0.01,
        vwmp=0.52,
        support_index=0,
    )


def _hypothesis(*, direction: str, is_shoulder: bool) -> FullFamilyHypothesis:
    return FullFamilyHypothesis(
        index=0,
        range_label="38°F or below" if is_shoulder else "70-71°F",
        direction=direction,
        edge=0.08,
        ci_lower=0.02,
        ci_upper=0.12,
        p_value=0.01,
        p_model=0.40,
        p_market=0.52,
        p_posterior=0.40,
        entry_price=0.48,
        is_shoulder=is_shoulder,
        passed_prefilter=True,
    )


def test_update_reaction_buy_no_without_strategy_classification_does_not_raise_family_id() -> None:
    candidate = _candidate()
    edge = _unclassified_edge()

    assert _strategy_key_for(candidate, edge) is None

    conn = sqlite3.connect(":memory:")
    result = _record_selection_family_facts(
        conn,
        candidate=candidate,
        edges=[edge],
        filtered=[edge],
        decision_snapshot_id="snap-unclassified",
        selected_method="ens_member_counting",
        recorded_at="2026-05-02T00:00:00+00:00",
    )

    assert result == {"status": "skipped_no_hypotheses"}


def test_update_reaction_unclassified_edge_source_does_not_fallback_to_opening_inertia() -> None:
    candidate = _candidate()
    edge = _unclassified_edge()

    assert _edge_source_for(candidate, edge) == "unclassified"
    assert _strategy_key_for(candidate, edge) is None
    assert cycle_runner._classify_edge_source(DiscoveryMode.UPDATE_REACTION, edge) == "unclassified"
    assert cycle_runner._classify_strategy(DiscoveryMode.UPDATE_REACTION, edge, "") == "unclassified"


def test_dormant_inverse_quadrants_do_not_masquerade_as_live_strategy_keys() -> None:
    candidate = _candidate()

    assert _edge_source_for(candidate, _shoulder_buy_edge()) == "unclassified"
    assert _strategy_key_for(candidate, _shoulder_buy_edge()) is None
    assert cycle_runner._classify_edge_source(DiscoveryMode.UPDATE_REACTION, _shoulder_buy_edge()) == "unclassified"
    assert cycle_runner._classify_strategy(DiscoveryMode.UPDATE_REACTION, _shoulder_buy_edge(), "") == "unclassified"

    assert _strategy_key_for_hypothesis(candidate, _hypothesis(direction="buy_no", is_shoulder=True)) == "shoulder_sell"
    assert _strategy_key_for_hypothesis(candidate, _hypothesis(direction="buy_yes", is_shoulder=False)) == "center_buy"
    assert _strategy_key_for_hypothesis(candidate, _hypothesis(direction="buy_yes", is_shoulder=True)) is None
    assert _strategy_key_for_hypothesis(candidate, _hypothesis(direction="buy_no", is_shoulder=False)) is None


def test_imminent_open_capture_maps_to_canonical_opening_inertia_strategy_key() -> None:
    """Discovery mode is not a governance strategy key.

    Live durable tables constrain strategy_key to the canonical four strategy
    families. `imminent_open_capture` is a cycle/discovery mode inside the
    opening-inertia family, so evaluator output must use `opening_inertia` for
    durable facts, position projection, and risk attribution.
    """
    candidate = _candidate(DiscoveryMode.IMMINENT_OPEN_CAPTURE.value)
    edge = _shoulder_buy_edge()

    assert _edge_source_for(candidate, edge) == "imminent_open_capture"
    assert _strategy_key_for(candidate, edge) == "opening_inertia"
    assert (
        cycle_runner._classify_strategy(
            DiscoveryMode.IMMINENT_OPEN_CAPTURE,
            edge,
            "imminent_open_capture",
        )
        == "opening_inertia"
    )
    assert (
        _strategy_key_for_hypothesis(
            candidate,
            _hypothesis(direction="buy_yes", is_shoulder=True),
        )
        == "opening_inertia"
    )
