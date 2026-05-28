# Created: 2026-05-28
# Last reused or audited: 2026-05-28
# Authority basis: operator pre-MC review Blocker A+B (SD3)
"""Relationship tests for selective_refit_from_manifest.compute_final_regen.

Tests the pure helper directly — no subprocess, no DB, no MC runs.
Verifies that replay output is correctly consumed to determine the
final_regen_manifest (SD3 defect fix: A cohorts that FAIL replay must be regenerated).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ZEUS_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ZEUS_ROOT))

from scripts.selective_refit_from_manifest import compute_final_regen  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures: fake manifest rows with the minimum columns compute_final_regen reads
# ---------------------------------------------------------------------------

def _row(city: str, season: str, metric: str, action: str) -> dict:
    return {"city": city, "season": season, "metric": metric, "action": action}


MANIFEST_ROWS = [
    _row("Amsterdam", "MAM", "high", "B_REFIT_AND_REGEN_COHORT"),   # B — always regen
    _row("London", "DJF", "high", "E_LOW_SCALE_REGEN"),             # E — always regen
    _row("Ankara", "DJF", "high", "A_REUSE_PENDING_REPLAY"),        # A — depends on replay
    _row("Helsinki", "MAM", "high", "A_REUSE_PENDING_REPLAY"),      # A — depends on replay
    _row("Denver", "DJF", "high", "D_MONTH_SCOPE"),                 # D — fit-only, not regen
    _row("Chicago", "MAM", "high", "C_NO_LEARNED_CORRECTION"),      # C — fit-only, not regen
]


# ---------------------------------------------------------------------------
# Test 1: A cohort that PASSES replay is NOT in final_regen
# ---------------------------------------------------------------------------

def test_a_pass_not_in_regen():
    """An A cohort whose replay PASSED must be excluded from final_regen."""
    replay_results = {
        ("Ankara", "DJF", "high"): True,   # PASS
        ("Helsinki", "MAM", "high"): True,  # PASS
    }
    regen = compute_final_regen(MANIFEST_ROWS, replay_results, gate_changed=False)

    assert ("Ankara", "DJF", "high") not in regen, (
        "A cohort with replay PASS must NOT be in final_regen"
    )
    assert ("Helsinki", "MAM", "high") not in regen, (
        "A cohort with replay PASS must NOT be in final_regen"
    )


# ---------------------------------------------------------------------------
# Test 2: A cohort that FAILS replay IS in final_regen
# ---------------------------------------------------------------------------

def test_a_fail_in_regen():
    """An A cohort whose replay FAILED must be included in final_regen."""
    replay_results = {
        ("Ankara", "DJF", "high"): False,  # FAIL
        ("Helsinki", "MAM", "high"): True,  # PASS
    }
    regen = compute_final_regen(MANIFEST_ROWS, replay_results, gate_changed=False)

    assert ("Ankara", "DJF", "high") in regen, (
        "A cohort with replay FAIL must be in final_regen"
    )
    assert ("Helsinki", "MAM", "high") not in regen, (
        "A cohort with replay PASS must NOT be in final_regen"
    )


# ---------------------------------------------------------------------------
# Test 3: A cohort with no replay result is fail-closed (treated as FAIL)
# ---------------------------------------------------------------------------

def test_a_missing_replay_result_is_fail_closed():
    """An A cohort with no replay result (missing key) must be treated as FAIL (fail-closed)."""
    replay_results: dict = {}  # no results at all
    regen = compute_final_regen(MANIFEST_ROWS, replay_results, gate_changed=False)

    assert ("Ankara", "DJF", "high") in regen, (
        "A cohort with no replay result must be fail-closed (in final_regen)"
    )
    assert ("Helsinki", "MAM", "high") in regen, (
        "A cohort with no replay result must be fail-closed (in final_regen)"
    )


# ---------------------------------------------------------------------------
# Test 4: B and E cohorts are ALWAYS in final_regen
# ---------------------------------------------------------------------------

def test_b_and_e_always_in_regen():
    """B and E cohorts must always appear in final_regen regardless of replay results."""
    # Provide empty replay results to isolate B/E behaviour
    replay_results: dict = {}
    regen = compute_final_regen(MANIFEST_ROWS, replay_results, gate_changed=False)

    assert ("Amsterdam", "MAM", "high") in regen, "B cohort must always be in final_regen"
    assert ("London", "DJF", "high") in regen, "E cohort must always be in final_regen"


# ---------------------------------------------------------------------------
# Test 5: gate change -> final_regen == all cohorts
# ---------------------------------------------------------------------------

def test_gate_change_full_reproduce():
    """When gate_changed=True, every cohort in the manifest must be in final_regen."""
    replay_results = {
        ("Ankara", "DJF", "high"): True,   # PASS — should be ignored
        ("Helsinki", "MAM", "high"): True,  # PASS — should be ignored
    }
    regen = compute_final_regen(MANIFEST_ROWS, replay_results, gate_changed=True)

    all_keys = {(r["city"], r["season"], r["metric"]) for r in MANIFEST_ROWS}
    assert regen == all_keys, (
        f"gate_changed=True must produce final_regen == all cohorts.\n"
        f"Expected: {sorted(all_keys)}\nGot: {sorted(regen)}"
    )


# ---------------------------------------------------------------------------
# Test 6: gate change ignores replay results (passed A cohorts still in regen)
# ---------------------------------------------------------------------------

def test_gate_change_ignores_replay():
    """gate_changed=True must include A cohorts even if they have replay PASS results."""
    replay_results = {
        ("Ankara", "DJF", "high"): True,   # PASS — but gate changed, so must still regen
    }
    regen = compute_final_regen(MANIFEST_ROWS, replay_results, gate_changed=True)

    assert ("Ankara", "DJF", "high") in regen, (
        "gate_changed=True must regenerate A cohorts even with replay PASS"
    )


# ---------------------------------------------------------------------------
# Test 7: C and D cohorts are NOT in final_regen under normal (no gate-change) conditions
# ---------------------------------------------------------------------------

def test_c_and_d_not_in_regen_normal():
    """C and D cohorts (fit-only) must not appear in final_regen when gate_changed=False."""
    replay_results = {
        ("Ankara", "DJF", "high"): True,
        ("Helsinki", "MAM", "high"): True,
    }
    regen = compute_final_regen(MANIFEST_ROWS, replay_results, gate_changed=False)

    assert ("Denver", "DJF", "high") not in regen, "D cohort must NOT be in final_regen"
    assert ("Chicago", "MAM", "high") not in regen, "C cohort must NOT be in final_regen"
