# Created: 2026-05-15
# Last reused or audited: 2026-05-15
# Authority basis: docs/operations/task_2026-05-15_p1_topology_v_next_additive/SCAFFOLD.md §2.1
"""
Unit tests for scripts/topology_v_next/companion_loop_break.py.

Covers: Mode A admit (companion present), Mode B issue (companion absent),
delegation to cohort_admit, no-cohort-applies case, return type contract.
"""
from __future__ import annotations

import pytest

from scripts.topology_v_next.dataclasses import (
    BindingLayer,
    CohortDecl,
    CoverageMap,
    Intent,
    IssueRecord,
    Severity,
)
from scripts.topology_v_next.companion_loop_break import companion_loop_break


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_binding(cohorts: tuple[CohortDecl, ...] = ()) -> BindingLayer:
    cm = CoverageMap(
        profiles={
            "agent_runtime": ("scripts/topology_doctor.py", "architecture/**"),
            "test_suite": ("tests/test_*.py", "tests/topology_v_next/**"),
        },
        orphaned=("tmp/**",),
        hard_stop_paths=("src/execution/**",),
    )
    return BindingLayer(
        project_id="zeus",
        intent_extensions=(Intent.zeus_topology_tooling,),
        coverage_map=cm,
        cohorts=cohorts,
        severity_overrides={},
        high_fanout_hints=(),
        artifact_authority_status={},
    )


TWO_FILE_COHORT = CohortDecl(
    id="zeus.new_test_with_topology_registration",
    profile="test_suite",
    intent_classes=(Intent.create_new,),
    files=(
        "tests/test_{new_module}.py",
        "architecture/test_topology.yaml",
    ),
    description="new test requires companion test_topology.yaml update",
)

THREE_FILE_COHORT = CohortDecl(
    id="zeus.three_file_cohort",
    profile="agent_runtime",
    intent_classes=(Intent.modify_existing,),
    files=(
        "scripts/topology_doctor.py",
        "architecture/test_topology.yaml",
        "docs/operations/AGENTS.md",
    ),
    description="three file cohort — should NOT be checked by companion_loop_break",
)


# ---------------------------------------------------------------------------
# Tests: Mode A — companion present
# ---------------------------------------------------------------------------

class TestModeA:
    def test_mode_a_returns_true_none_none(self):
        """Companion pair complete → (True, None, None)."""
        binding = _make_binding(cohorts=(TWO_FILE_COHORT,))
        result = companion_loop_break(
            Intent.create_new,
            ["tests/test_calibration.py", "architecture/test_topology.yaml"],
            binding,
        )
        assert result == (True, None, None)

    def test_mode_a_with_extra_files(self):
        """Extra files beyond companion pair still mode A."""
        binding = _make_binding(cohorts=(TWO_FILE_COHORT,))
        result = companion_loop_break(
            Intent.create_new,
            [
                "tests/test_calibration.py",
                "architecture/test_topology.yaml",
                "scripts/extra.py",
            ],
            binding,
        )
        assert result == (True, None, None)

    def test_mode_a_different_module_name(self):
        """Template '{new_module}' expands to any name."""
        binding = _make_binding(cohorts=(TWO_FILE_COHORT,))
        result = companion_loop_break(
            Intent.create_new,
            ["tests/test_venue_adapter.py", "architecture/test_topology.yaml"],
            binding,
        )
        assert result == (True, None, None)


# ---------------------------------------------------------------------------
# Tests: Mode B — companion absent
# ---------------------------------------------------------------------------

class TestModeB:
    def test_mode_b_returns_false_with_issue(self):
        """Cohort relevant but companion missing → (False, missing, issue)."""
        binding = _make_binding(cohorts=(TWO_FILE_COHORT,))
        admit, missing, issue = companion_loop_break(
            Intent.create_new,
            ["tests/test_calibration.py"],  # missing architecture/test_topology.yaml
            binding,
        )
        assert admit is False
        assert missing is not None
        assert issue is not None

    def test_mode_b_issue_is_soft_block(self):
        binding = _make_binding(cohorts=(TWO_FILE_COHORT,))
        _, _, issue = companion_loop_break(
            Intent.create_new,
            ["tests/test_calibration.py"],
            binding,
        )
        assert issue is not None
        assert issue.severity == Severity.SOFT_BLOCK

    def test_mode_b_issue_code(self):
        binding = _make_binding(cohorts=(TWO_FILE_COHORT,))
        _, _, issue = companion_loop_break(
            Intent.create_new,
            ["tests/test_calibration.py"],
            binding,
        )
        assert issue is not None
        assert issue.code == "companion_missing"

    def test_mode_b_missing_path_is_the_absent_companion(self):
        """Missing path should identify the cohort file pattern that is absent."""
        binding = _make_binding(cohorts=(TWO_FILE_COHORT,))
        _, missing, issue = companion_loop_break(
            Intent.create_new,
            ["tests/test_calibration.py"],
            binding,
        )
        # The missing file is the test_topology.yaml companion pattern
        assert missing is not None
        assert "test_topology" in missing or "architecture" in missing

    def test_mode_b_issue_metadata_has_cohort_id(self):
        binding = _make_binding(cohorts=(TWO_FILE_COHORT,))
        _, _, issue = companion_loop_break(
            Intent.create_new,
            ["tests/test_calibration.py"],
            binding,
        )
        assert issue is not None
        assert issue.metadata.get("cohort_id") == "zeus.new_test_with_topology_registration"

    def test_mode_b_only_companion_present_also_triggers(self):
        """Companion present but main test missing → also Mode B."""
        binding = _make_binding(cohorts=(TWO_FILE_COHORT,))
        admit, missing, issue = companion_loop_break(
            Intent.create_new,
            ["architecture/test_topology.yaml"],  # companion present, test missing
            binding,
        )
        assert admit is False
        assert issue is not None


# ---------------------------------------------------------------------------
# Tests: No cohort applies
# ---------------------------------------------------------------------------

class TestNoCohortApplies:
    def test_no_cohorts_returns_false_none_none(self):
        """No cohorts declared → (False, None, None)."""
        binding = _make_binding(cohorts=())
        result = companion_loop_break(
            Intent.create_new,
            ["tests/test_calibration.py"],
            binding,
        )
        assert result == (False, None, None)

    def test_wrong_intent_returns_false_none_none(self):
        """Cohort declared but intent doesn't match → not applicable."""
        binding = _make_binding(cohorts=(TWO_FILE_COHORT,))
        result = companion_loop_break(
            Intent.modify_existing,  # cohort requires create_new
            ["tests/test_calibration.py", "architecture/test_topology.yaml"],
            binding,
        )
        assert result == (False, None, None)

    def test_unrelated_files_returns_false_none_none(self):
        """Files not touching any cohort pattern → not applicable."""
        binding = _make_binding(cohorts=(TWO_FILE_COHORT,))
        result = companion_loop_break(
            Intent.create_new,
            ["scripts/topology_doctor.py"],  # doesn't match test_{new_module}.py
            binding,
        )
        assert result == (False, None, None)

    def test_three_file_cohort_not_checked(self):
        """companion_loop_break only checks 2-file cohorts per §1.9."""
        binding = _make_binding(cohorts=(THREE_FILE_COHORT,))
        result = companion_loop_break(
            Intent.modify_existing,
            ["scripts/topology_doctor.py"],
            binding,
        )
        assert result == (False, None, None)


# ---------------------------------------------------------------------------
# Tests: Return type contract
# ---------------------------------------------------------------------------

class TestReturnTypeContract:
    def test_return_is_three_tuple(self):
        binding = _make_binding(cohorts=(TWO_FILE_COHORT,))
        result = companion_loop_break(
            Intent.create_new,
            ["tests/test_foo.py", "architecture/test_topology.yaml"],
            binding,
        )
        assert isinstance(result, tuple)
        assert len(result) == 3

    def test_mode_a_first_element_is_bool_true(self):
        binding = _make_binding(cohorts=(TWO_FILE_COHORT,))
        admit, _, _ = companion_loop_break(
            Intent.create_new,
            ["tests/test_foo.py", "architecture/test_topology.yaml"],
            binding,
        )
        assert isinstance(admit, bool)
        assert admit is True

    def test_mode_b_third_element_is_issue_record(self):
        binding = _make_binding(cohorts=(TWO_FILE_COHORT,))
        _, _, issue = companion_loop_break(
            Intent.create_new,
            ["tests/test_foo.py"],
            binding,
        )
        assert isinstance(issue, IssueRecord)

    def test_delegates_to_cohort_admit(self):
        """
        Verify delegation to cohort_admit by asserting Mode A fires for
        the exact same file set that cohort_admit would accept.
        """
        from scripts.topology_v_next.composition_rules import cohort_admit
        binding = _make_binding(cohorts=(TWO_FILE_COHORT,))
        files = ["tests/test_engine.py", "architecture/test_topology.yaml"]

        # cohort_admit says it matches
        cohort_result = cohort_admit(Intent.create_new, files, (TWO_FILE_COHORT,))
        assert cohort_result is TWO_FILE_COHORT

        # companion_loop_break should therefore be Mode A
        admit, _, _ = companion_loop_break(Intent.create_new, files, binding)
        assert admit is True
