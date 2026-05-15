# Created: 2026-05-15
# Last reused or audited: 2026-05-15
# Authority basis: docs/operations/task_2026-05-15_p1_topology_v_next_additive/SCAFFOLD.md §2.1
"""
Unit tests for scripts/topology_v_next/composition_rules.py.

Covers: C1 additive companion, C2 subsumption, C3 explicit union (structural),
C4 cohort delegation, cohort_admit matching, hint-never-routes property.
"""
from __future__ import annotations

import inspect

import pytest

from scripts.topology_v_next.dataclasses import (
    BindingLayer,
    CohortDecl,
    CoverageMap,
    Intent,
    IssueRecord,
    Severity,
)
from scripts.topology_v_next.composition_rules import (
    apply_composition,
    cohort_admit,
    explain_rejected,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_cohort(
    id: str = "test.cohort",
    profile: str = "test_suite",
    intent_classes: tuple[Intent, ...] = (Intent.create_new,),
    files: tuple[str, ...] = ("tests/test_foo.py", "architecture/test_topology.yaml"),
    description: str = "test cohort",
) -> CohortDecl:
    return CohortDecl(
        id=id,
        profile=profile,
        intent_classes=intent_classes,
        files=files,
        description=description,
    )


def _make_binding(
    profiles: dict[str, tuple[str, ...]] | None = None,
    cohorts: tuple[CohortDecl, ...] = (),
) -> BindingLayer:
    cm = CoverageMap(
        profiles=profiles or {
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


# ---------------------------------------------------------------------------
# Tests: cohort_admit
# ---------------------------------------------------------------------------

class TestCohortAdmit:
    def test_returns_none_when_no_cohorts(self):
        result = cohort_admit(Intent.create_new, ["tests/test_foo.py"], ())
        assert result is None

    def test_returns_cohort_when_all_files_present_and_intent_matches(self):
        cohort = _make_cohort(
            files=("tests/test_foo.py", "architecture/test_topology.yaml"),
        )
        result = cohort_admit(
            Intent.create_new,
            ["tests/test_foo.py", "architecture/test_topology.yaml"],
            (cohort,),
        )
        assert result is cohort

    def test_returns_none_when_intent_does_not_match(self):
        cohort = _make_cohort(intent_classes=(Intent.create_new,))
        result = cohort_admit(
            Intent.modify_existing,
            ["tests/test_foo.py", "architecture/test_topology.yaml"],
            (cohort,),
        )
        assert result is None

    def test_returns_none_when_file_missing(self):
        cohort = _make_cohort(
            files=("tests/test_foo.py", "architecture/test_topology.yaml"),
        )
        # Only one of the two required files present
        result = cohort_admit(
            Intent.create_new,
            ["tests/test_foo.py"],
            (cohort,),
        )
        assert result is None

    def test_template_pattern_matches_any_module(self):
        """'{new_module}' in cohort files should match any module name."""
        cohort = _make_cohort(
            files=(
                "tests/test_{new_module}.py",
                "architecture/test_topology.yaml",
            ),
        )
        # Both patterns must match — template expanded to '*'
        result = cohort_admit(
            Intent.create_new,
            ["tests/test_calibration.py", "architecture/test_topology.yaml"],
            (cohort,),
        )
        assert result is cohort

    def test_template_pattern_fails_when_companion_missing(self):
        cohort = _make_cohort(
            files=(
                "tests/test_{new_module}.py",
                "architecture/test_topology.yaml",
            ),
        )
        result = cohort_admit(
            Intent.create_new,
            ["tests/test_calibration.py"],  # missing companion
            (cohort,),
        )
        assert result is None

    def test_extra_files_do_not_prevent_match(self):
        """Submitted files may be a superset of cohort files."""
        cohort = _make_cohort(
            files=("tests/test_foo.py", "architecture/test_topology.yaml"),
        )
        result = cohort_admit(
            Intent.create_new,
            [
                "tests/test_foo.py",
                "architecture/test_topology.yaml",
                "scripts/extra.py",
            ],
            (cohort,),
        )
        assert result is cohort

    def test_first_matching_cohort_returned(self):
        """When multiple cohorts match, first one in tuple is returned."""
        c1 = _make_cohort(id="c1", profile="prof1")
        c2 = _make_cohort(id="c2", profile="prof2")
        result = cohort_admit(
            Intent.create_new,
            ["tests/test_foo.py", "architecture/test_topology.yaml"],
            (c1, c2),
        )
        assert result is c1

    def test_hint_not_a_parameter(self):
        """cohort_admit must NOT have hint/phrase/task in its signature."""
        sig = inspect.signature(cohort_admit)
        param_names = set(sig.parameters.keys())
        assert "hint" not in param_names
        assert "phrase" not in param_names
        assert "task" not in param_names


# ---------------------------------------------------------------------------
# Tests: apply_composition — C1/C2 single profile
# ---------------------------------------------------------------------------

class TestApplyCompositionSingleProfile:
    def test_c2_subsumption_single_profile(self):
        """All files match one profile → resolve under that profile."""
        candidates = {
            "scripts/topology_doctor.py": {"agent_runtime"},
            "architecture/test_topology.yaml": {"agent_runtime"},
        }
        binding = _make_binding()
        profile, issues = apply_composition(
            Intent.modify_existing,
            ["scripts/topology_doctor.py", "architecture/test_topology.yaml"],
            candidates,
            binding,
        )
        assert profile == "agent_runtime"
        assert issues == []

    def test_c1_additive_companion_single_profile(self):
        """New file (no profile) + existing profile file → resolve under existing."""
        # One file has agent_runtime, one has empty set (coverage gap)
        candidates = {
            "scripts/topology_doctor.py": {"agent_runtime"},
            "scripts/new_helper.py": set(),
        }
        # union_candidate_profiles = {"agent_runtime"} → single profile → C1/C2
        binding = _make_binding()
        profile, issues = apply_composition(
            Intent.create_new,
            ["scripts/topology_doctor.py", "scripts/new_helper.py"],
            candidates,
            binding,
        )
        assert profile == "agent_runtime"
        assert issues == []


# ---------------------------------------------------------------------------
# Tests: apply_composition — multi-profile conflict
# ---------------------------------------------------------------------------

class TestApplyCompositionConflict:
    def test_multi_profile_no_cohort_returns_soft_block(self):
        """Two profiles, no cohort → composition_conflict SOFT_BLOCK."""
        candidates = {
            "scripts/topology_doctor.py": {"agent_runtime"},
            "tests/test_foo.py": {"test_suite"},
        }
        binding = _make_binding(cohorts=())
        profile, issues = apply_composition(
            Intent.modify_existing,
            ["scripts/topology_doctor.py", "tests/test_foo.py"],
            candidates,
            binding,
        )
        assert profile is None
        assert len(issues) == 1
        assert issues[0].code == "composition_conflict"
        assert issues[0].severity == Severity.SOFT_BLOCK

    def test_conflict_metadata_includes_touched_profiles(self):
        candidates = {
            "scripts/topology_doctor.py": {"agent_runtime"},
            "tests/test_foo.py": {"test_suite"},
        }
        binding = _make_binding(cohorts=())
        _, issues = apply_composition(
            Intent.modify_existing,
            ["scripts/topology_doctor.py", "tests/test_foo.py"],
            candidates,
            binding,
        )
        assert "touched_profiles" in issues[0].metadata
        assert set(issues[0].metadata["touched_profiles"]) == {"agent_runtime", "test_suite"}

    def test_all_gaps_returns_soft_block(self):
        """No profiles matched at all → composition_conflict SOFT_BLOCK."""
        candidates = {"unlisted/file.py": set()}
        binding = _make_binding(cohorts=())
        profile, issues = apply_composition(
            Intent.create_new,
            ["unlisted/file.py"],
            candidates,
            binding,
        )
        assert profile is None
        assert issues[0].code == "composition_conflict"


# ---------------------------------------------------------------------------
# Tests: apply_composition — C4 cohort
# ---------------------------------------------------------------------------

class TestApplyCompositionCohort:
    def test_c4_cohort_resolves_multi_profile_conflict(self):
        """Cohort declaration admits a coherent multi-profile change."""
        cohort = CohortDecl(
            id="zeus.new_test_with_topology_registration",
            profile="test_suite",
            intent_classes=(Intent.create_new,),
            files=(
                "tests/test_{new_module}.py",
                "architecture/test_topology.yaml",
            ),
            description="new test + topology yaml",
        )
        candidates = {
            "tests/test_calibration.py": {"test_suite"},
            "architecture/test_topology.yaml": {"agent_runtime"},
        }
        binding = _make_binding(cohorts=(cohort,))
        profile, issues = apply_composition(
            Intent.create_new,
            ["tests/test_calibration.py", "architecture/test_topology.yaml"],
            candidates,
            binding,
        )
        assert profile == "test_suite"
        assert issues == []

    def test_c4_cohort_wrong_intent_falls_through_to_conflict(self):
        cohort = _make_cohort(intent_classes=(Intent.create_new,))
        candidates = {
            "tests/test_foo.py": {"test_suite"},
            "architecture/test_topology.yaml": {"agent_runtime"},
        }
        binding = _make_binding(cohorts=(cohort,))
        profile, issues = apply_composition(
            Intent.modify_existing,  # wrong intent for cohort
            ["tests/test_foo.py", "architecture/test_topology.yaml"],
            candidates,
            binding,
        )
        assert profile is None
        assert issues[0].code == "composition_conflict"

    def test_hint_not_a_parameter(self):
        """apply_composition signature must NOT have hint/phrase/task."""
        sig = inspect.signature(apply_composition)
        param_names = set(sig.parameters.keys())
        assert "hint" not in param_names
        assert "phrase" not in param_names
        assert "task" not in param_names


# ---------------------------------------------------------------------------
# Tests: explain_rejected — hint never routes
# ---------------------------------------------------------------------------

class TestExplainRejected:
    def test_returns_none_when_no_candidates(self):
        binding = _make_binding()
        result = explain_rejected({}, binding, hint="anything")
        assert result is None

    def test_returns_profile_id_string_or_none(self):
        candidates = {"scripts/topology_doctor.py": {"agent_runtime"}}
        binding = _make_binding()
        result = explain_rejected(candidates, binding, hint="topology")
        assert result is None or isinstance(result, str)

    def test_hint_matches_profile_id_preferred(self):
        """Profile whose id appears in hint is preferred for diagnostic."""
        candidates = {
            "scripts/foo.py": {"agent_runtime"},
            "tests/test_foo.py": {"test_suite"},
        }
        binding = _make_binding()
        result = explain_rejected(candidates, binding, hint="agent_runtime update")
        assert result == "agent_runtime"

    def test_different_hints_same_profile_returned(self):
        """
        PHRASING_GAME_TAX property: explain_rejected is diagnostic only.
        The actual routing decision (apply_composition) must be hint-independent.
        This test asserts explain_rejected itself produces a stable output type,
        not that it MUST return the same value for different hints (it's a
        ranking heuristic for human-readable output only).
        """
        candidates = {"scripts/topology_doctor.py": {"agent_runtime"}}
        binding = _make_binding()
        r1 = explain_rejected(candidates, binding, hint="fix the topology tool")
        r2 = explain_rejected(candidates, binding, hint="update architecture doc")
        # Both return same profile since only one candidate
        assert r1 == r2

    def test_apply_composition_ignores_hint(self):
        """
        The profile returned by apply_composition must be identical regardless
        of any hint value supplied. Hint only appears in explain_rejected.
        """
        candidates = {"scripts/topology_doctor.py": {"agent_runtime"}}
        binding = _make_binding()
        files = ["scripts/topology_doctor.py"]

        # apply_composition has no hint parameter — confirm same result
        profile_a, issues_a = apply_composition(
            Intent.modify_existing, files, candidates, binding
        )
        profile_b, issues_b = apply_composition(
            Intent.modify_existing, files, candidates, binding
        )
        assert profile_a == profile_b
        assert issues_a == issues_b
