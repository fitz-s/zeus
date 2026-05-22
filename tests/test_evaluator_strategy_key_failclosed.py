# Created: 2026-05-02
# Last reused/audited: 2026-05-22
# Lifecycle: created=2026-05-02; last_reviewed=2026-05-21; last_reused=2026-05-22
# Purpose: Lock durable strategy_key classification and fail-closed behavior across evaluator/runtime persistence boundaries.
# Reuse: Run before changing evaluator strategy_key mapping, discovery-mode classification, or strategy_key DB persistence.
# Authority basis: PR44 review comment 3177316079 / 3177317099 strategy_key unclassified fail-closed contract;
#                  2026-05-21 live CHECK repair: discovery modes must not invent strategy_key values.

from __future__ import annotations

import ast
import json
import sqlite3
from pathlib import Path

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
from src.state.db import init_schema, init_schema_forecasts
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


def _day0_candidate_with_observed_high(observed_high: float) -> MarketCandidate:
    return MarketCandidate(
        city=_city(),
        target_date="2026-05-03",
        outcomes=[],
        hours_since_open=30.0,
        temperature_metric="high",
        discovery_mode=DiscoveryMode.DAY0_CAPTURE.value,
        observation={"high_so_far": observed_high, "current_temp": observed_high},
    )


def _imminent_candidate_with_observed_high(observed_high: float) -> MarketCandidate:
    return _settlement_day_candidate_with_observed_high(
        observed_high,
        discovery_mode=DiscoveryMode.IMMINENT_OPEN_CAPTURE.value,
        hours_since_open=0.5,
    )


def _settlement_day_candidate_with_observed_high(
    observed_high: float,
    *,
    discovery_mode: str,
    hours_since_open: float = 30.0,
) -> MarketCandidate:
    from src.strategy.market_phase import MarketPhase

    return MarketCandidate(
        city=_city(),
        target_date="2026-05-03",
        outcomes=[],
        hours_since_open=hours_since_open,
        temperature_metric="high",
        discovery_mode=discovery_mode,
        market_phase=MarketPhase.SETTLEMENT_DAY,
        observation={"high_so_far": observed_high, "current_temp": observed_high},
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


def _finite_buy_yes_edge(low: float, high: float) -> BinEdge:
    return BinEdge(
        bin=Bin(low=low, high=high, label=f"{low:g}-{high:g}°F", unit="F"),
        direction="buy_yes",
        edge=0.80,
        ci_lower=0.70,
        ci_upper=0.90,
        p_model=0.90,
        p_market=0.10,
        p_posterior=0.90,
        entry_price=0.04,
        p_value=0.01,
        vwmp=0.04,
        support_index=1,
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

    assert _strategy_key_for_hypothesis(candidate, _hypothesis(direction="buy_no", is_shoulder=True)) == "shoulder_impossible_tail_capture"  # D6: shoulder_sell retired
    assert _strategy_key_for_hypothesis(candidate, _hypothesis(direction="buy_yes", is_shoulder=False)) == "center_buy"
    assert _strategy_key_for_hypothesis(candidate, _hypothesis(direction="buy_yes", is_shoulder=True)) is None
    assert _strategy_key_for_hypothesis(candidate, _hypothesis(direction="buy_no", is_shoulder=False)) is None


def test_imminent_open_capture_maps_to_own_strategy_key() -> None:
    """IMMINENT_OPEN_CAPTURE mode must attribute to its own registry profile.

    §22.1 C-2 (zeus_strategy_spec.md) and STRATEGY_TAXONOMY_DIRECTIVE.md §9
    treat imminent_open_capture as a distinct live strategy with its own profile
    in architecture/strategy_profile_registry.yaml:280. Evaluator strategy_key
    must resolve to "imminent_open_capture" so its decisions land in the correct
    promotion-evidence cohort and are NOT credited to opening_inertia.

    Note: cycle_runner._classify_strategy is a separate function not involved in
    evaluator cohort attribution; its mapping is tested separately in test_runtime_guards.
    """
    candidate = _candidate(DiscoveryMode.IMMINENT_OPEN_CAPTURE.value)
    edge = _shoulder_buy_edge()

    # edge_source and strategy_key must now agree on "imminent_open_capture"
    assert _edge_source_for(candidate, edge) == "imminent_open_capture"
    assert _strategy_key_for(candidate, edge) == "imminent_open_capture"

    assert (
        _strategy_key_for_hypothesis(
            candidate,
            _hypothesis(direction="buy_yes", is_shoulder=True),
        )
        == "imminent_open_capture"
    )


def test_imminent_open_capture_and_opening_inertia_strategy_keys_are_separate() -> None:
    """Mode→key separation: OPENING_HUNT→opening_inertia, IMMINENT_OPEN_CAPTURE→imminent_open_capture.

    Regression guard for §22.1 C-2 cohort contamination fix.
    """
    edge = _shoulder_buy_edge()

    oi_candidate = _candidate(DiscoveryMode.OPENING_HUNT.value)
    ioc_candidate = _candidate(DiscoveryMode.IMMINENT_OPEN_CAPTURE.value)

    assert _strategy_key_for(oi_candidate, edge) == "opening_inertia"
    assert _strategy_key_for(ioc_candidate, edge) == "imminent_open_capture"
    assert _strategy_key_for(oi_candidate, edge) != _strategy_key_for(ioc_candidate, edge)

    assert _strategy_key_for_hypothesis(oi_candidate, _hypothesis(direction="buy_yes", is_shoulder=True)) == "opening_inertia"
    assert _strategy_key_for_hypothesis(ioc_candidate, _hypothesis(direction="buy_yes", is_shoulder=True)) == "imminent_open_capture"


def test_day0_forecast_upside_does_not_masquerade_as_settlement_capture() -> None:
    candidate = _day0_candidate_with_observed_high(34.0)
    edge = _finite_buy_yes_edge(36.0, 37.0)

    assert _edge_source_for(candidate, edge) == "day0_nowcast_entry"
    assert _strategy_key_for(candidate, edge) == "day0_nowcast_entry"


def test_imminent_forecast_upside_does_not_masquerade_as_settlement_capture() -> None:
    candidate = _imminent_candidate_with_observed_high(34.0)
    edge = _finite_buy_yes_edge(36.0, 37.0)

    assert _edge_source_for(candidate, edge) == "day0_nowcast_entry"
    assert _strategy_key_for(candidate, edge) == "day0_nowcast_entry"


def test_any_settlement_day_forecast_upside_does_not_masquerade_as_settlement_capture() -> None:
    candidate = _settlement_day_candidate_with_observed_high(
        34.0,
        discovery_mode=DiscoveryMode.UPDATE_REACTION.value,
    )
    edge = _finite_buy_yes_edge(36.0, 37.0)

    assert _edge_source_for(candidate, edge) == "day0_nowcast_entry"
    assert _strategy_key_for(candidate, edge) == "day0_nowcast_entry"


def test_day0_nowcast_selection_facts_preserve_strategy_identity() -> None:
    candidate = _day0_candidate_with_observed_high(34.0)
    edge = _finite_buy_yes_edge(36.0, 37.0)
    hypothesis = FullFamilyHypothesis(
        index=0,
        range_label=edge.bin.label,
        direction=edge.direction,
        edge=edge.edge,
        ci_lower=edge.ci_lower,
        ci_upper=edge.ci_upper,
        p_value=edge.p_value,
        p_model=edge.p_model,
        p_market=edge.p_market,
        p_posterior=edge.p_posterior,
        entry_price=edge.entry_price,
        is_shoulder=False,
        passed_prefilter=True,
    )
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    init_schema_forecasts(conn)

    result = _record_selection_family_facts(
        conn,
        candidate=candidate,
        edges=[edge],
        filtered=[edge],
        hypotheses=[hypothesis],
        decision_snapshot_id="snap-day0-nowcast",
        selected_method="day0_observation",
        recorded_at="2026-05-22T17:06:14+00:00",
    )

    family = conn.execute("SELECT strategy_key FROM selection_family_fact").fetchone()
    row = conn.execute("SELECT meta_json FROM selection_hypothesis_fact").fetchone()
    assert result == {"status": "written", "families": 1, "hypotheses": 1}
    assert family["strategy_key"] == "day0_nowcast_entry"
    assert json.loads(row["meta_json"])["hypothesis_strategy_key"] == "day0_nowcast_entry"


def test_family_preselection_rejections_stamp_strategy_identity_before_runtime_persistence() -> None:
    source = Path("src/engine/evaluator.py").read_text()
    tree = ast.parse(source)
    target = None
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "EdgeDecision":
            keyword_names = {keyword.arg for keyword in node.keywords if keyword.arg}
            rejection_reasons = keyword_names and next(
                (
                    keyword.value
                    for keyword in node.keywords
                    if keyword.arg == "rejection_reasons"
                ),
                None,
            )
            if isinstance(rejection_reasons, ast.List) and any(
                isinstance(elt, ast.Name) and elt.id == "MUTUALLY_EXCLUSIVE_FAMILY_DEDUP"
                for elt in rejection_reasons.elts
            ):
                target = keyword_names
                break

    assert target is not None
    assert {"edge_source", "strategy_key"} <= target


def test_imminent_open_capture_kelly_phase_diverges_from_opening_inertia() -> None:
    """IOC and opening_inertia have distinct Kelly profiles — the C-2 fix reveals intended sizing.

    Critic verdict (C2_CRITIC_VERDICT.md):
    - opening_inertia: kelly_phase_overrides[settlement_day]=0.0 (alpha decayed by then)
    - imminent_open_capture: kelly_phase_overrides[settlement_day]=0.5 (0-24h window IS settlement_day)

    Under live-default flag ZEUS_MARKET_PHASE_DISPATCH=1 the IOC settlement_day=0.5 override
    is unreachable (dispatch.py intercepts settlement_day → settlement_capture first). It is a
    deliberate flag-OFF fallback added in commit f83db10008 (#205, 2026-05-19, operator urgency).
    VERDICT: CORRECT_DESIGN (C2_DESIGN_HOMEWORK.md).

    This test is NON-VACUOUS: if strategy_key reverts to "opening_inertia" for IOC mode,
    the settlement_day assertion fails (0.0 != 0.5).
    """
    from src.strategy.strategy_profile import try_get as _try_get_profile

    ioc_profile = _try_get_profile("imminent_open_capture")
    oi_profile = _try_get_profile("opening_inertia")
    assert ioc_profile is not None, "imminent_open_capture profile missing from registry"
    assert oi_profile is not None, "opening_inertia profile missing from registry"

    # Core divergence: settlement_day Kelly
    assert ioc_profile.kelly_for_phase("settlement_day") == 0.5
    assert oi_profile.kelly_for_phase("settlement_day") == 0.0
    assert ioc_profile.kelly_for_phase("settlement_day") != oi_profile.kelly_for_phase("settlement_day")

    # pre_settlement_day Kelly is equal (both 0.5) — only settlement_day diverges
    assert ioc_profile.kelly_for_phase("pre_settlement_day") == 0.5
    assert oi_profile.kelly_for_phase("pre_settlement_day") == 0.5

    # IOC's allowed_market_phases includes settlement_day; opening_inertia's does not
    assert "settlement_day" in ioc_profile.allowed_market_phases
    assert "settlement_day" not in oi_profile.allowed_market_phases
