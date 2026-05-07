# Created: 2026-05-07
# Last reused or audited: 2026-05-07
# Authority basis: Navigation Topology v2 PLAN §3 Phase 2A exit criteria; admission_severity.yaml AS-07/AS-08; sunset 2026-11-07

"""Phase 2A runtime tests: K1 severity demotion wiring via admission_severity.yaml.

Verifies that the two F5 emitter sites in topology_doctor_script_checks.py consult
admission_severity.yaml at runtime and emit issues with severity="advisory" (NOT
severity="error" / "blocking") for WORKING-class codes.

Test matrix:
  1. script_long_lived_bad_name (F5, AS-07) → severity="advisory"
  2. script_diagnostic_forbidden_write_target (F5, AS-08) → severity="advisory"
  3. planning_lock_evidence_missing (AS-09, TRUTH_REWRITE) → severity="error" (still BLOCKING)
  4. Backward compat: code NOT in admission_severity.yaml → severity="error" (legacy default)

Per PLAN §3 Phase 2A deliverables and §1.3 K1 structural decision.
"""

from __future__ import annotations

import pathlib
import sys
import types
import unittest.mock
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

REPO_ROOT = pathlib.Path(__file__).parent.parent

# ---------------------------------------------------------------------------
# Import topology_doctor (the module-under-test)
# ---------------------------------------------------------------------------

# topology_doctor is in scripts/ which may or may not be on sys.path
if str(REPO_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "scripts"))

import topology_doctor  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _reset_severity_cache() -> None:
    """Clear the per-process _SEVERITY_CACHE between tests to allow patching."""
    topology_doctor._SEVERITY_CACHE = None


def _make_minimal_api() -> MagicMock:
    """Build a minimal mock of the topology_doctor module-as-api used by script checks.

    script_checks receives `sys.modules[__name__]` (topology_doctor) as `api`.
    We replicate just the attributes the F5 code paths touch.
    """
    api = MagicMock()
    # Wire the two methods under test to their real implementations
    api._issue = topology_doctor._issue
    api._issue_with_admission_severity = topology_doctor._issue_with_admission_severity
    # Naming conventions: no exceptions, no allowed_prefixes → every name is bad
    api.NAMING_CONVENTIONS_PATH = REPO_ROOT / "architecture" / "naming_conventions.yaml"
    api.load_naming_conventions = topology_doctor.load_naming_conventions
    api.ROOT = REPO_ROOT
    api.ONE_OFF_SCRIPT_NAME_PATTERN = topology_doctor.ONE_OFF_SCRIPT_NAME_PATTERN
    api.EMPTY_METADATA_VALUES = topology_doctor.EMPTY_METADATA_VALUES
    return api


# ---------------------------------------------------------------------------
# Test 1: script_long_lived_bad_name → severity="advisory"
# ---------------------------------------------------------------------------

def test_f5_bad_name_emits_advisory() -> None:
    """F5/AS-07: script_long_lived_bad_name must emit severity='advisory' at runtime.

    Simulate check_script_lifecycle() being called for a long-lived script whose
    name does not match any allowed prefix or exception.  The checker calls
    api._issue_with_admission_severity('script_long_lived_bad_name', ...) which
    must resolve ADVISORY from admission_severity.yaml and set severity='advisory'.

    Emitter site: scripts/topology_doctor_script_checks.py:121-128.
    """
    _reset_severity_cache()

    try:
        from scripts import topology_doctor_script_checks as sc
    except ModuleNotFoundError:
        import topology_doctor_script_checks as sc  # type: ignore[no-redef]

    api = _make_minimal_api()

    # Manifest: no allowed_prefixes, no exceptions → name will fail the check
    manifest = {
        "scripts": {
            "low_high_alignment_report.py": {
                "lifecycle": "long_lived",
                "reuse_when": "diagnostics",
                "do_not_use_when": "never",
                "canonical_command": "python scripts/low_high_alignment_report.py",
                "delete_policy": "never",
                "authority_scope": "diagnostic_non_promotion",
                "write_targets": ["stdout"],
            }
        },
        "allowed_lifecycles": ["long_lived", "packet_ephemeral", "promotion_candidate", "deprecated_fail_closed"],
        "long_lived_naming": {
            "allowed_prefixes": ["refit_", "diagnose_", "run_", "build_", "export_"],
            "exceptions": {},
        },
    }
    effective = manifest["scripts"]["low_high_alignment_report.py"]

    issues = sc.check_script_lifecycle(api, manifest, "low_high_alignment_report.py", effective)

    bad_name_issues = [i for i in issues if i.code == "script_long_lived_bad_name"]
    assert bad_name_issues, (
        "Expected at least one issue with code='script_long_lived_bad_name'. "
        f"Got issues: {[i.code for i in issues]}"
    )
    for issue in bad_name_issues:
        assert issue.severity == "advisory", (
            f"script_long_lived_bad_name must emit severity='advisory' (F5/AS-07 K1 demotion). "
            f"Got severity={issue.severity!r}. "
            "Check that _issue_with_admission_severity reads ADVISORY from admission_severity.yaml."
        )


# ---------------------------------------------------------------------------
# Test 2: script_diagnostic_forbidden_write_target → severity="advisory"
# ---------------------------------------------------------------------------

def test_f5_forbidden_write_target_emits_advisory() -> None:
    """F5/AS-08: script_diagnostic_forbidden_write_target must emit severity='advisory'.

    Simulate run_scripts() checking a diagnostic script whose write_targets include
    a path not in diagnostic_allowed_write_targets.  The checker calls
    api._issue_with_admission_severity('script_diagnostic_forbidden_write_target', ...)
    which must emit severity='advisory' per AS-08.

    Emitter site: scripts/topology_doctor_script_checks.py:267-274.
    """
    _reset_severity_cache()

    try:
        from scripts import topology_doctor_script_checks as sc
    except ModuleNotFoundError:
        import topology_doctor_script_checks as sc  # type: ignore[no-redef]

    api = _make_minimal_api()
    # We call run_scripts() but mock out the filesystem-dependent parts
    api._top_level_scripts = MagicMock(return_value={"diagnose_bad_target.py"})
    api._check_script_lifecycle = MagicMock(return_value=[])
    api._effective_script_entry = MagicMock(return_value={
        "lifecycle": "long_lived",
        "authority_scope": "diagnostic_non_promotion",
        "write_targets": ["docs/operations/low_high_alignment/output.json"],  # forbidden path
    })
    # Root must point somewhere that the script file does not exist (to skip read)
    api.ROOT = REPO_ROOT  # script won't exist → continue after forbidden_writes check
    # Wire StrictResult to the real dataclass so run_scripts return value is usable
    api.StrictResult = topology_doctor.StrictResult

    manifest = {
        "scripts": {"diagnose_bad_target.py": {}},
        "required_effective_fields": [],
        "diagnostic_allowed_write_targets": ["stdout", "temp", "evidence/**"],
        "canonical_db_targets": [],
    }
    api.load_script_manifest = MagicMock(return_value=manifest)

    result = sc.run_scripts(api)

    forbidden_issues = [
        i for i in result.issues
        if i.code == "script_diagnostic_forbidden_write_target"
    ]
    assert forbidden_issues, (
        "Expected at least one issue with code='script_diagnostic_forbidden_write_target'. "
        f"Got issues: {[i.code for i in result.issues]}"
    )
    for issue in forbidden_issues:
        assert issue.severity == "advisory", (
            f"script_diagnostic_forbidden_write_target must emit severity='advisory' "
            f"(F5/AS-08 K1 demotion). Got severity={issue.severity!r}. "
            "Check that _issue_with_admission_severity reads ADVISORY from admission_severity.yaml."
        )


# ---------------------------------------------------------------------------
# Test 3: BLOCKING code (TRUTH_REWRITE) still emits severity="error"
# ---------------------------------------------------------------------------

def test_truth_rewrite_code_still_blocking() -> None:
    """TRUTH_REWRITE codes (AS-09) must remain severity='error' after K1 wiring.

    planning_lock_evidence_missing has target_severity=BLOCKING in admission_severity.yaml
    (reversibility_class=TRUTH_REWRITE).  Calling _issue_with_admission_severity with
    this code must produce severity='error', not 'advisory'.

    Asserts that the K1 demotion only applies to ADVISORY entries; BLOCKING entries
    are unchanged.
    """
    _reset_severity_cache()

    issue = topology_doctor._issue_with_admission_severity(
        "planning_lock_evidence_missing",
        "src/control/foo.py",
        "planning lock evidence missing",
    )
    assert issue.severity == "error", (
        f"planning_lock_evidence_missing (AS-09, TRUTH_REWRITE) must remain "
        f"severity='error'. Got severity={issue.severity!r}. "
        "BLOCKING codes must not be demoted by K1 wiring."
    )


# ---------------------------------------------------------------------------
# Test 4: Backward compat — code not in admission_severity.yaml → "error"
# ---------------------------------------------------------------------------

def test_unknown_code_falls_through_to_error() -> None:
    """Codes NOT in admission_severity.yaml must fall through to legacy severity='error'.

    _issue_with_admission_severity must default to 'error' (blocking) when the
    issue code has no entry in admission_severity.yaml, preserving all existing
    behavior for codes not yet listed in the registry.

    Per PLAN §3 Phase 2A: 'backward compat: rules NOT in admission_severity.yaml
    fall through to current behavior'.
    """
    _reset_severity_cache()

    issue = topology_doctor._issue_with_admission_severity(
        "some_completely_unknown_code_xyz_not_in_yaml",
        "scripts/foo.py",
        "test message",
    )
    assert issue.severity == "error", (
        f"Unknown code must fall through to severity='error' (backward compat). "
        f"Got severity={issue.severity!r}."
    )


# ---------------------------------------------------------------------------
# Test 5: _load_admission_severity returns a dict (cache warm path)
# ---------------------------------------------------------------------------

def test_load_admission_severity_returns_mapping() -> None:
    """_load_admission_severity() must return a non-empty dict from the YAML file.

    Verifies the cache-warm path: calling twice returns the same object (cached).
    """
    _reset_severity_cache()

    mapping1 = topology_doctor._load_admission_severity()
    assert isinstance(mapping1, dict), (
        f"_load_admission_severity() must return a dict; got {type(mapping1).__name__}"
    )
    assert mapping1, (
        "_load_admission_severity() returned an empty dict. "
        "architecture/admission_severity.yaml must have issue_severity entries."
    )
    # Verify the two F5 codes are present and ADVISORY
    assert mapping1.get("script_long_lived_bad_name") == "ADVISORY", (
        f"Expected script_long_lived_bad_name → ADVISORY; got {mapping1.get('script_long_lived_bad_name')!r}"
    )
    assert mapping1.get("script_diagnostic_forbidden_write_target") == "ADVISORY", (
        f"Expected script_diagnostic_forbidden_write_target → ADVISORY; "
        f"got {mapping1.get('script_diagnostic_forbidden_write_target')!r}"
    )
    # Cache warm path: second call returns same object
    mapping2 = topology_doctor._load_admission_severity()
    assert mapping1 is mapping2, "_load_admission_severity() must cache; second call must return same object"


# ---------------------------------------------------------------------------
# Test 6: F5 advisory severity does not propagate to error count
# ---------------------------------------------------------------------------

def test_f5_advisory_not_counted_as_error() -> None:
    """Advisory issues must not be counted in error tallies.

    The run_scripts() code paths that count blocking errors check
    issue.severity == 'error'.  An advisory issue (severity='advisory') must
    not increment the error count, confirming the K1 demotion has runtime effect.
    """
    _reset_severity_cache()

    issue = topology_doctor._issue_with_admission_severity(
        "script_long_lived_bad_name",
        "scripts/low_high_alignment_report.py",
        "long-lived script name must use an allowed prefix",
    )
    assert issue.severity != "error", (
        "script_long_lived_bad_name must NOT have severity='error' after K1 demotion. "
        f"Got severity={issue.severity!r}."
    )
    assert issue.severity == "advisory", (
        f"Expected severity='advisory', got {issue.severity!r}."
    )
    # Simulate the blocking count logic from topology_doctor.py line ~2602
    is_blocking = (issue.severity == "error")
    assert not is_blocking, (
        "F5 advisory issue must not count as a blocking error in error tallies."
    )
