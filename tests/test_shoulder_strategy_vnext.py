# Created: 2026-05-21
# Last reused or audited: 2026-05-21
# Authority basis: docs/operations/task_2026-05-21_strategy_vnext_phase3_shoulder/PHASE_3_SHOULDER_PLAN.md §2 T2 + 04_PHASE_3_SHOULDER.md
# Lifecycle: created=2026-05-21; last_reviewed=2026-05-21; last_reused=never
# Purpose: SCAFFOLD structural probes for ShoulderStrategyVNext 21-field model + Phase 3 T2 invariants
# Reuse: verify T2 production pass has been completed; probes are skipped (xfail) until then

"""SCAFFOLD test stubs for ShoulderStrategyVNext + Phase 3 T2 structural probes.

Probe coverage (10_VERIFIER_PROBES.md):
  P-3-1 : ShoulderStrategyVNext has 21 fields per authority §"Required object model"
  P-3-3 : Kelly clamp [0.05, 0.20] at phase_aware_kelly_multiplier L198
  P-3-4 : 6 stress scenarios present in ALL_SCENARIOS
  P-3-5 : SCHEMA_VERSION bumped to 17 (v16 claimed by PR #249) (tail_stress_scenarios + no_trade_events rebuild)
  P-3-6 : 6 new SHOULDER_* NoTradeReason members

Tests land BEFORE production logic per Fitz methodology.
Bodies are SCAFFOLD stubs — skip until T2 production pass.
"""

from __future__ import annotations

import dataclasses

import pytest


# ---------------------------------------------------------------------------
# P-3-1: ShoulderStrategyVNext has exactly 21 fields
# ---------------------------------------------------------------------------

def test_p_3_1_shoulder_vnext_has_21_fields():
    """P-3-1: ShoulderStrategyVNext frozen dataclass has exactly 21 fields per
    04_PHASE_3_SHOULDER.md §"Required object model" (verifier recount 2026-05-21)."""
    from src.contracts.shoulder_strategy_vnext import ShoulderStrategyVNext

    fields = dataclasses.fields(ShoulderStrategyVNext)
    assert len(fields) == 21, (
        f"Expected 21 fields, got {len(fields)}: {[f.name for f in fields]}"
    )


def test_p_3_1_shoulder_vnext_exact_field_names():
    """P-3-1: All 21 field names match authority §"Required object model" verbatim."""
    from src.contracts.shoulder_strategy_vnext import ShoulderStrategyVNext

    expected_fields = [
        "is_open_shoulder",
        "shoulder_side",
        "metric",
        "tail_direction",
        "finite_adjacent_bin",
        "tail_probability_raw",
        "tail_probability_calibrated",
        "tail_probability_stressed",
        "tail_regime_tag",
        "retail_lottery_bias_score",
        "extreme_weather_underpricing_score",
        "source_anomaly_score",
        "native_yes_quote",
        "native_no_quote",
        "liquidity_gate",
        "shoulder_family_id",
        "tail_correlation_cluster",
        "max_loss_scenario",
        "kelly_haircut",
        "max_exposure_cap",
        "no_trade_reason",
    ]
    actual_fields = [f.name for f in dataclasses.fields(ShoulderStrategyVNext)]
    assert actual_fields == expected_fields, (
        f"Field mismatch.\nExpected: {expected_fields}\nActual: {actual_fields}"
    )


def test_p_3_1_shoulder_vnext_is_frozen_dataclass():
    """P-3-1: ShoulderStrategyVNext is a frozen dataclass (immutable contract)."""
    from src.contracts.shoulder_strategy_vnext import ShoulderStrategyVNext

    assert dataclasses.is_dataclass(ShoulderStrategyVNext)
    assert ShoulderStrategyVNext.__dataclass_params__.frozen


# ---------------------------------------------------------------------------
# P-3-3: Kelly clamp [0.05, 0.20] at phase_aware_kelly_multiplier
# ---------------------------------------------------------------------------

@pytest.mark.skip(reason="SCAFFOLD — T2 production pass adds clamp body to phase_aware_kelly_multiplier L198")
def test_p_3_3_shoulder_kelly_clamp_lower_bound():
    """P-3-3: phase_aware_kelly_multiplier clamps shoulder paths to >= 0.05."""
    pass


@pytest.mark.skip(reason="SCAFFOLD — T2 production pass adds clamp body to phase_aware_kelly_multiplier L198")
def test_p_3_3_shoulder_kelly_clamp_upper_bound():
    """P-3-3: phase_aware_kelly_multiplier clamps shoulder paths to <= 0.20."""
    pass


@pytest.mark.skip(reason="SCAFFOLD — T2 production pass adds clamp; verify applies only when live_status=shadow AND mult > 0.0")
def test_p_3_3_shoulder_kelly_clamp_applies_only_when_shadow_and_positive():
    """P-3-3: Kelly clamp [0.05, 0.20] applies only when live_status=shadow AND
    kelly_default_multiplier > 0.0 (R-3: current 0.0 unchanged)."""
    pass


# ---------------------------------------------------------------------------
# P-3-4: 6 stress scenarios in ALL_SCENARIOS
# ---------------------------------------------------------------------------

def test_p_3_4_stress_scenarios_count():
    """P-3-4: ALL_SCENARIOS tuple contains exactly 6 TailStressScenario instances."""
    from src.strategy.stress_scenarios import ALL_SCENARIOS

    assert len(ALL_SCENARIOS) == 6, (
        f"Expected 6 stress scenarios, got {len(ALL_SCENARIOS)}"
    )


def test_p_3_4_stress_scenarios_exact_ids():
    """P-3-4: All 6 scenario_ids match dossier §7.5 enumeration (verbatim order)."""
    from src.strategy.stress_scenarios import ALL_SCENARIOS

    expected_ids = [
        "forecast_plus_2sigma",
        "station_anomaly",
        "late_day_advection",
        "source_revision",
        "model_tail_underdispersion",
        "correlated_city_crash",
    ]
    actual_ids = [s.scenario_id for s in ALL_SCENARIOS]
    assert actual_ids == expected_ids, (
        f"Scenario ID mismatch.\nExpected: {expected_ids}\nActual: {actual_ids}"
    )


def test_p_3_4_stress_scenarios_are_frozen_dataclasses():
    """P-3-4: Each TailStressScenario instance is a frozen dataclass."""
    from src.strategy.stress_scenarios import ALL_SCENARIOS, TailStressScenario

    assert dataclasses.is_dataclass(TailStressScenario)
    assert TailStressScenario.__dataclass_params__.frozen
    for s in ALL_SCENARIOS:
        assert isinstance(s, TailStressScenario)


# ---------------------------------------------------------------------------
# P-3-5: SCHEMA_VERSION bumped to 17 (v16 claimed by PR #249)
# ---------------------------------------------------------------------------

def test_p_3_5_schema_version_is_18():
    """P-3-5: db.py SCHEMA_VERSION == 18 (Phase 3 T2 bump; v17 claimed by PR #253)."""
    from src.state.db import SCHEMA_VERSION

    assert SCHEMA_VERSION == 18, (
        f"Expected SCHEMA_VERSION=18, got {SCHEMA_VERSION}"
    )


def test_p_3_5_tail_stress_scenarios_table_in_fresh_db():
    """P-3-5: tail_stress_scenarios table exists in a fresh init_schema DB."""
    import sqlite3

    from src.state.db import init_schema

    conn = sqlite3.connect(":memory:")
    init_schema(conn)
    tables = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    assert "tail_stress_scenarios" in tables, (
        f"tail_stress_scenarios missing from fresh DB. Tables: {tables}"
    )


@pytest.mark.skip(reason="SCAFFOLD — no_trade_events rebuild migration runs at T2 production pass")
def test_p_3_5_no_trade_events_check_accepts_shoulder_reasons_after_rebuild():
    """P-3-5: After table-rebuild migration, no_trade_events CHECK accepts SHOULDER_* values."""
    pass


# ---------------------------------------------------------------------------
# P-3-6: 6 SHOULDER_* NoTradeReason members
# ---------------------------------------------------------------------------

def test_p_3_6_shoulder_no_trade_reason_members_present():
    """P-3-6: NoTradeReason enum has all 6 SHOULDER_* members per authority."""
    from src.contracts.no_trade_reason import NoTradeReason

    expected_shoulder_members = {
        "SHOULDER_STRESS_FAIL",
        "SHOULDER_REGIME_MISMATCH",
        "SHOULDER_NATIVE_NO_DEPTH_INSUFFICIENT",
        "SHOULDER_DAY0_BOUND_NOT_ELIMINATED",
        "SHOULDER_NO_TRADE_GATE",
        "SHOULDER_CLUSTER_CAP_EXCEEDED",
    }
    actual_names = {m.name for m in NoTradeReason}
    missing = expected_shoulder_members - actual_names
    assert not missing, (
        f"Missing SHOULDER_* NoTradeReason members: {missing}"
    )


def test_p_3_6_shoulder_no_trade_reason_count():
    """P-3-6: Exactly 6 SHOULDER_* prefixed members in NoTradeReason."""
    from src.contracts.no_trade_reason import NoTradeReason

    shoulder_members = [m for m in NoTradeReason if m.name.startswith("SHOULDER_")]
    assert len(shoulder_members) == 6, (
        f"Expected 6 SHOULDER_* members, got {len(shoulder_members)}: {shoulder_members}"
    )
