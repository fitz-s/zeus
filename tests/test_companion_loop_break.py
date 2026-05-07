# Created: 2026-05-07
# Last reused or audited: 2026-05-07
# Authority basis: Navigation Topology v2 PLAN §3 Phase 2B exit criteria; K2 companion-loop-break; sunset 2026-11-07

"""Phase 2B tests: K2 companion-loop-break (4 admission-gate auto-admit).

Tests that _apply_companion_loop_break() correctly auto-admits manifest companion files
when typed_intent=create_new and both the parent file AND companion are in requested_files.

Test matrix:
  1. scripts/X.py + architecture/script_manifest.yaml + create_new → companion auto-admitted
  2. Missing header (companion absent from --files) → advisory companion_missing, no auto-admit
  3. typed_intent=modify_existing → no auto-admit (existing behavior preserved)
  4. All 4 parent→companion pairs fire correctly
  5. M4 batch-cap advisory fires at len(requested)=51

Per PLAN §3 Phase 2B exit criteria and evidence/topology_v2_critic_opus.md ATTACK 6.
"""

from __future__ import annotations

import pathlib
import sys
from typing import Any

import pytest

REPO_ROOT = pathlib.Path(__file__).parent.parent

if str(REPO_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "scripts"))

from topology_doctor_digest import (  # noqa: E402
    _apply_companion_loop_break,
    _COMPANION_LOOP_BREAK_PAIRS,
    _DEFAULT_COMPANION_BATCH_CAP,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _scope_expansion_admission(out_of_scope: list[str], admitted: list[str] | None = None) -> dict[str, Any]:
    """Build a minimal scope_expansion_required admission dict for testing."""
    return {
        "status": "scope_expansion_required",
        "profile_id": "test_profile",
        "confidence": 0.9,
        "admitted_files": list(admitted or []),
        "profile_suggested_files": [],
        "out_of_scope_files": list(out_of_scope),
        "forbidden_hits": [],
        "companion_required": [],
        "decision_basis": {
            "task_phrases": [],
            "file_globs": [],
            "negative_hits": [],
            "selected_by": "typed_intent",
            "candidates": [],
            "why": ["out_of_scope: files not declared in profile.allowed_files"],
        },
    }


def _admitted_admission(admitted: list[str]) -> dict[str, Any]:
    """Build a minimal admitted admission dict for testing."""
    return {
        "status": "admitted",
        "profile_id": "test_profile",
        "confidence": 1.0,
        "admitted_files": list(admitted),
        "profile_suggested_files": [],
        "out_of_scope_files": [],
        "forbidden_hits": [],
        "companion_required": [],
        "decision_basis": {
            "task_phrases": [],
            "file_globs": [],
            "negative_hits": [],
            "selected_by": "typed_intent",
            "candidates": [],
            "why": [],
        },
    }


# ---------------------------------------------------------------------------
# Test 1: scripts/X.py + architecture/script_manifest.yaml + create_new → auto-admitted
# ---------------------------------------------------------------------------

def test_script_companion_auto_admitted_create_new() -> None:
    """F3 fix: new script + manifest companion + typed_intent=create_new → auto-admitted.

    Agent includes both scripts/diagnose_x.py (new file, out-of-scope) and
    architecture/script_manifest.yaml (companion) in --files with --intent create_new.
    The companion-loop-break must upgrade status to 'admitted'.
    """
    admission = _scope_expansion_admission(
        out_of_scope=["scripts/diagnose_x.py"],
        admitted=["architecture/script_manifest.yaml"],  # companion already admitted via profile
    )
    requested = ["scripts/diagnose_x.py", "architecture/script_manifest.yaml"]

    result = _apply_companion_loop_break(admission, requested, "create_new")

    assert result["status"] == "admitted", (
        f"Expected status='admitted' after companion-loop-break. Got: {result['status']!r}. "
        "scripts/diagnose_x.py + architecture/script_manifest.yaml + create_new must auto-admit."
    )
    assert "scripts/diagnose_x.py" in result["admitted_files"], (
        f"scripts/diagnose_x.py must be in admitted_files. Got: {result['admitted_files']}"
    )
    assert result["out_of_scope_files"] == [], (
        f"out_of_scope_files must be empty after full resolution. Got: {result['out_of_scope_files']}"
    )
    assert result.get("companion_loop_break") is True, (
        "companion_loop_break flag must be True when auto-admit fires."
    )
    assert "scripts/diagnose_x.py" in (result.get("auto_admitted") or []), (
        "auto_admitted list must contain the auto-admitted file."
    )
    # why log must mention companion_loop_break
    why = (result.get("decision_basis") or {}).get("why") or []
    assert any("companion_loop_break" in w for w in why), (
        f"decision_basis.why must contain companion_loop_break entry. Got: {why}"
    )


# ---------------------------------------------------------------------------
# Test 2: Companion absent from --files → advisory companion_missing, no auto-admit
# ---------------------------------------------------------------------------

def test_companion_missing_emits_advisory_not_auto_admit() -> None:
    """When parent is in --files but companion is NOT in --files, emit advisory only.

    Agent creates scripts/diagnose_x.py but does NOT include architecture/script_manifest.yaml
    in --files. The loop-break must NOT auto-admit; must emit companion_missing advisory.
    """
    admission = _scope_expansion_admission(
        out_of_scope=["scripts/diagnose_x.py"],
    )
    # Companion is NOT in requested
    requested = ["scripts/diagnose_x.py"]

    result = _apply_companion_loop_break(admission, requested, "create_new")

    assert result["status"] == "scope_expansion_required", (
        "Status must remain scope_expansion_required when companion is not in --files. "
        f"Got: {result['status']!r}."
    )
    assert result.get("companion_loop_break") is not True, (
        "companion_loop_break must NOT be set when companion is absent from --files."
    )
    advisories = result.get("companion_loop_advisories") or []
    companion_missing = [a for a in advisories if a.get("code") == "companion_missing"]
    assert companion_missing, (
        "Expected companion_missing advisory when companion not in --files. "
        f"Got advisories: {advisories}"
    )
    assert companion_missing[0].get("severity") == "info", (
        "companion_missing advisory must have severity='info' (non-blocking)."
    )
    assert "architecture/script_manifest.yaml" in companion_missing[0].get("expected_companion", ""), (
        f"companion_missing advisory must name architecture/script_manifest.yaml. "
        f"Got: {companion_missing[0]}"
    )


# ---------------------------------------------------------------------------
# Test 3: typed_intent=modify_existing → no auto-admit (existing behavior preserved)
# ---------------------------------------------------------------------------

def test_no_auto_admit_for_modify_existing() -> None:
    """typed_intent=modify_existing must NOT trigger companion-loop-break.

    The loop-break is only for create_new and refactor. Existing modify_existing behavior
    (scope_expansion_required) must be preserved exactly.
    """
    admission = _scope_expansion_admission(
        out_of_scope=["scripts/diagnose_x.py"],
    )
    requested = ["scripts/diagnose_x.py", "architecture/script_manifest.yaml"]

    result = _apply_companion_loop_break(admission, requested, "modify_existing")

    assert result["status"] == "scope_expansion_required", (
        "modify_existing must NOT trigger companion-loop-break. "
        f"Got status: {result['status']!r}."
    )
    assert result.get("companion_loop_break") is not True, (
        "companion_loop_break must NOT be set for modify_existing intent."
    )
    # Admission dict must be unchanged (or reference-equal to input)
    assert result["out_of_scope_files"] == ["scripts/diagnose_x.py"], (
        f"out_of_scope_files must be unchanged for modify_existing. Got: {result['out_of_scope_files']}"
    )


def test_no_auto_admit_for_none_intent() -> None:
    """typed_intent=None (no --intent flag) must NOT trigger companion-loop-break."""
    admission = _scope_expansion_admission(out_of_scope=["scripts/foo.py"])
    requested = ["scripts/foo.py", "architecture/script_manifest.yaml"]

    result = _apply_companion_loop_break(admission, requested, None)

    assert result["status"] == "scope_expansion_required", (
        "None intent must not trigger companion-loop-break."
    )


# ---------------------------------------------------------------------------
# Test 4: All 4 parent→companion pairs fire correctly
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("parent_file,companion_path", [
    ("scripts/refit_something.py", "architecture/script_manifest.yaml"),
    ("tests/test_new_feature.py", "architecture/test_topology.yaml"),
    ("docs/operations/task_2026-05-07_my_task/PLAN.md", "docs/operations/AGENTS.md"),
    ("src/engine/new_module.py", "architecture/source_rationale.yaml"),
])
def test_all_four_companion_pairs_fire(parent_file: str, companion_path: str) -> None:
    """All 4 declared companion pairs must auto-admit when both files are in --files.

    Per PLAN §2.4 and evidence/topology_v2_critic_opus.md ATTACK 2: 4 pairs verified.
    """
    admission = _scope_expansion_admission(out_of_scope=[parent_file])
    requested = [parent_file, companion_path]

    result = _apply_companion_loop_break(admission, requested, "create_new")

    assert result["status"] == "admitted", (
        f"Companion pair ({parent_file!r} → {companion_path!r}) must auto-admit with create_new. "
        f"Got status: {result['status']!r}."
    )
    assert parent_file in result["admitted_files"], (
        f"{parent_file!r} must be in admitted_files after companion-loop-break."
    )
    assert result.get("companion_loop_break") is True, (
        "companion_loop_break flag must be True."
    )


# ---------------------------------------------------------------------------
# Test 5: M4 batch-cap fires advisory at len(requested)=51
# ---------------------------------------------------------------------------

def test_m4_batch_cap_advisory_at_51() -> None:
    """M4 (critic ATTACK 6): len(requested) > 50 must emit companion_loop_batch_advisory.

    The advisory is non-blocking — auto-admit still proceeds when conditions are met.
    """
    # Build 51 files: one is the parent script, one is the companion, rest are filler
    parent = "scripts/diagnose_batch_test.py"
    companion = "architecture/script_manifest.yaml"
    filler = [f"scripts/filler_{i}.py" for i in range(49)]
    requested = [parent, companion] + filler  # total = 51
    assert len(requested) == 51

    # Only the parent is out-of-scope; filler files are admitted
    admission = _scope_expansion_admission(out_of_scope=[parent])

    result = _apply_companion_loop_break(admission, requested, "create_new", batch_cap=50)

    advisories = result.get("companion_loop_advisories") or []
    batch_advisories = [a for a in advisories if a.get("code") == "companion_loop_batch_advisory"]
    assert batch_advisories, (
        f"Expected companion_loop_batch_advisory when len(requested)=51 > batch_cap=50. "
        f"Got advisories: {advisories}"
    )
    assert batch_advisories[0].get("severity") == "info", (
        "companion_loop_batch_advisory must have severity='info' (non-blocking)."
    )
    # Advisory is non-blocking: auto-admit must still fire
    assert result["status"] == "admitted", (
        "Batch-cap advisory must NOT block auto-admit. "
        f"Got status: {result['status']!r}."
    )


def test_m4_batch_cap_no_advisory_at_50() -> None:
    """Exactly 50 files must NOT emit batch advisory (threshold is strictly >)."""
    parent = "scripts/diagnose_at_cap.py"
    companion = "architecture/script_manifest.yaml"
    filler = [f"scripts/filler_{i}.py" for i in range(48)]
    requested = [parent, companion] + filler  # total = 50
    assert len(requested) == 50

    admission = _scope_expansion_admission(out_of_scope=[parent])
    result = _apply_companion_loop_break(admission, requested, "create_new", batch_cap=50)

    advisories = result.get("companion_loop_advisories") or []
    batch_advisories = [a for a in advisories if a.get("code") == "companion_loop_batch_advisory"]
    assert not batch_advisories, (
        f"No batch advisory expected at exactly batch_cap=50. Got: {batch_advisories}"
    )


def test_m4_batch_cap_tunable() -> None:
    """batch_cap parameter must be tunable: cap=5 with 6 files fires advisory."""
    parent = "scripts/diagnose_tunable.py"
    companion = "architecture/script_manifest.yaml"
    requested = [parent, companion] + ["scripts/x1.py", "scripts/x2.py", "scripts/x3.py", "scripts/x4.py"]
    assert len(requested) == 6

    admission = _scope_expansion_admission(out_of_scope=[parent])
    result = _apply_companion_loop_break(admission, requested, "create_new", batch_cap=5)

    advisories = result.get("companion_loop_advisories") or []
    batch_advisories = [a for a in advisories if a.get("code") == "companion_loop_batch_advisory"]
    assert batch_advisories, (
        f"Expected batch advisory with cap=5 and 8 files. Got: {advisories}"
    )


# ---------------------------------------------------------------------------
# Test 6: refactor intent also triggers auto-admit
# ---------------------------------------------------------------------------

def test_refactor_intent_triggers_auto_admit() -> None:
    """typed_intent=refactor must also trigger companion-loop-break (per PLAN §2.4)."""
    admission = _scope_expansion_admission(out_of_scope=["scripts/run_refactored.py"])
    requested = ["scripts/run_refactored.py", "architecture/script_manifest.yaml"]

    result = _apply_companion_loop_break(admission, requested, "refactor")

    assert result["status"] == "admitted", (
        f"refactor intent must trigger companion-loop-break. Got: {result['status']!r}."
    )
    assert result.get("companion_loop_break") is True


# ---------------------------------------------------------------------------
# Test 7: Already-admitted status unchanged (idempotent on admitted)
# ---------------------------------------------------------------------------

def test_admitted_status_unchanged() -> None:
    """When admission is already 'admitted', companion-loop-break must not modify it."""
    admission = _admitted_admission(admitted=["scripts/foo.py", "architecture/script_manifest.yaml"])
    requested = ["scripts/foo.py", "architecture/script_manifest.yaml"]

    result = _apply_companion_loop_break(admission, requested, "create_new")

    # Status stays admitted; no regression
    assert result["status"] == "admitted"
    # companion_loop_break flag is not set (no out_of_scope files to promote)
    assert result.get("companion_loop_break") is not True


# ---------------------------------------------------------------------------
# Test 8: _COMPANION_LOOP_BREAK_PAIRS covers all 4 declared pairs
# ---------------------------------------------------------------------------

def test_companion_loop_break_pairs_count() -> None:
    """_COMPANION_LOOP_BREAK_PAIRS must declare exactly 4 pairs per PLAN §2.4."""
    assert len(_COMPANION_LOOP_BREAK_PAIRS) == 4, (
        f"Expected exactly 4 companion pairs. Got {len(_COMPANION_LOOP_BREAK_PAIRS)}: "
        f"{_COMPANION_LOOP_BREAK_PAIRS}"
    )
    companions = [pair[1] for pair in _COMPANION_LOOP_BREAK_PAIRS]
    assert "architecture/script_manifest.yaml" in companions
    assert "architecture/test_topology.yaml" in companions
    assert "docs/operations/AGENTS.md" in companions
    assert "architecture/source_rationale.yaml" in companions


def test_companion_loop_break_default_batch_cap() -> None:
    """_DEFAULT_COMPANION_BATCH_CAP must be 50 per PLAN §0.5 M4 amendment."""
    assert _DEFAULT_COMPANION_BATCH_CAP == 50, (
        f"Default batch cap must be 50. Got: {_DEFAULT_COMPANION_BATCH_CAP}"
    )
