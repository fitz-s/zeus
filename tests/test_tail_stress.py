# Created: 2026-05-21
# Last reused or audited: 2026-05-21
# Authority basis: docs/operations/task_2026-05-21_strategy_vnext_phase3_shoulder/PHASE_3_SHOULDER_PLAN.md §2 T2 + 04_PHASE_3_SHOULDER.md §"Stress scenarios"

"""Tests for run_stress_tests (TailStressScenario stress kernel).

Activated in T2 production pass.
"""

from __future__ import annotations

import math
from types import SimpleNamespace

import pytest

from src.strategy.stress_scenarios import (
    ALL_SCENARIOS,
    CORRELATED_CITY_CRASH,
    run_stress_tests,
)


# ---------------------------------------------------------------------------
# Structural (run immediately — no production body needed)
# ---------------------------------------------------------------------------

def test_tail_stress_all_scenarios_have_positive_delta_sigma():
    """All 6 TailStressScenario instances have delta_sigma > 0 (adversarial perturbation)."""
    for s in ALL_SCENARIOS:
        assert s.delta_sigma > 0, (
            f"Scenario {s.scenario_id!r} has non-positive delta_sigma={s.delta_sigma}"
        )


def test_tail_stress_all_scenarios_have_nonempty_description():
    """All 6 TailStressScenario instances have non-empty description strings."""
    for s in ALL_SCENARIOS:
        assert s.description.strip(), (
            f"Scenario {s.scenario_id!r} has empty description"
        )


# ---------------------------------------------------------------------------
# Execution path (activated in T2 production pass)
# ---------------------------------------------------------------------------

def test_stress_fail_rejects_candidate_on_forecast_plus_2sigma():
    """P-3-12: Thin mode — run_stress_tests returns NaN for all scenarios when
    tail_probability_calibrated is NaN → SHOULDER_STRESS_FAIL (R-4)."""
    # Thin candidate: no tail_probability_calibrated (NaN)
    candidate = SimpleNamespace(tail_probability_calibrated=float("nan"))
    results = run_stress_tests(candidate)
    assert len(results) == len(ALL_SCENARIOS)
    for scenario_id, val in results.items():
        assert math.isnan(val), (
            f"Expected NaN for {scenario_id!r} with thin candidate, got {val}"
        )


def test_stress_fail_closed_on_insufficient_ens_members():
    """R-4: run_stress_tests returns NaN for ALL scenarios when
    tail_probability_calibrated is absent/NaN → SHOULDER_STRESS_FAIL."""
    # Candidate without the field at all
    candidate = SimpleNamespace()
    results = run_stress_tests(candidate)
    assert len(results) == len(ALL_SCENARIOS)
    for val in results.values():
        assert math.isnan(val), "All results must be NaN when tail_probability_calibrated absent"


def test_stress_pass_when_all_scenarios_survive():
    """run_stress_tests with thin candidate returns dict keyed by all scenario_ids."""
    candidate = SimpleNamespace(tail_probability_calibrated=float("nan"))
    results = run_stress_tests(candidate)
    expected_ids = {s.scenario_id for s in ALL_SCENARIOS}
    assert set(results.keys()) == expected_ids, (
        f"Missing scenario IDs: {expected_ids - set(results.keys())}"
    )


def test_stress_correlated_city_crash_scenario_is_in_all_scenarios():
    """CORRELATED_CITY_CRASH scenario applies across all cities in same cluster."""
    assert CORRELATED_CITY_CRASH in ALL_SCENARIOS
    ids = [s.scenario_id for s in ALL_SCENARIOS]
    assert "correlated_city_crash" in ids
    assert CORRELATED_CITY_CRASH.delta_sigma >= 3.0
