# Created: 2026-05-15
# Last reused or audited: 2026-05-15
# Authority basis: docs/operations/task_2026-05-15_p1_topology_v_next_additive/SCAFFOLD.md §2.2
"""
Friction regression: UNION_SCOPE_EXPANSION (P1.2 variant).

SCAFFOLD §5.2: "Traceable closed (P1) — Cohort declarations + Composition
Rules C1-C4 admit coherent multi-profile change sets."

Test: a coherent multi-profile change (new test + test_topology.yaml cohort)
is admitted via cohort rather than blocked as a composition_conflict.
"""
from __future__ import annotations

import pytest

from scripts.topology_v_next.admission_engine import admit
from scripts.topology_v_next.dataclasses import (
    BindingLayer,
    CohortDecl,
    CoverageMap,
    Intent,
    Severity,
)


def _make_binding_with_cohort() -> BindingLayer:
    """Binding with the canonical new-test + topology-registration cohort."""
    cm = CoverageMap(
        profiles={
            "agent_runtime": (
                "scripts/topology_doctor.py",
                "architecture/test_topology.yaml",
            ),
            "test_suite": (
                "tests/test_*.py",
                "tests/topology_v_next/**",
            ),
        },
        orphaned=("tmp/**",),
        hard_stop_paths=("src/execution/**",),
    )
    cohort = CohortDecl(
        id="zeus.new_test_with_topology_registration",
        profile="test_suite",
        intent_classes=(Intent.create_new,),
        files=(
            "tests/test_{new_module}.py",
            "architecture/test_topology.yaml",
        ),
        description="Every new test file requires a companion entry in test_topology.yaml.",
    )
    return BindingLayer(
        project_id="zeus",
        intent_extensions=(Intent.zeus_topology_tooling,),
        coverage_map=cm,
        cohorts=(cohort,),
        severity_overrides={},
        high_fanout_hints=(),
        artifact_authority_status={},
    )


def _make_binding_without_cohort() -> BindingLayer:
    """Same binding but without the cohort — to verify conflict baseline."""
    cm = CoverageMap(
        profiles={
            "agent_runtime": (
                "scripts/topology_doctor.py",
                "architecture/test_topology.yaml",
            ),
            "test_suite": (
                "tests/test_*.py",
                "tests/topology_v_next/**",
            ),
        },
        orphaned=("tmp/**",),
        hard_stop_paths=("src/execution/**",),
    )
    return BindingLayer(
        project_id="zeus",
        intent_extensions=(Intent.zeus_topology_tooling,),
        coverage_map=cm,
        cohorts=(),
        severity_overrides={},
        high_fanout_hints=(),
        artifact_authority_status={},
    )


# Files for a coherent multi-profile change
CHANGE_FILES = ["tests/test_calibration.py", "architecture/test_topology.yaml"]
INTENT = Intent.create_new


class TestUnionScopeExpansionClosure:
    def test_cohort_admits_multi_profile_change(self):
        """
        UNION_SCOPE_EXPANSION CLOSED: coherent multi-profile change admitted
        via C4 cohort rather than blocked as composition_conflict.
        """
        binding = _make_binding_with_cohort()
        result = admit(intent=INTENT, files=CHANGE_FILES, binding=binding)

        assert result.ok is True
        assert result.profile_matched == "test_suite"
        codes = {i.code for i in result.issues}
        assert "composition_conflict" not in codes

    def test_without_cohort_produces_conflict(self):
        """
        Baseline: the same change without a cohort declaration produces
        composition_conflict SOFT_BLOCK. This confirms the cohort is doing
        the structural work, not a code bypass.
        """
        binding = _make_binding_without_cohort()
        result = admit(intent=INTENT, files=CHANGE_FILES, binding=binding)

        codes = {i.code for i in result.issues}
        assert "composition_conflict" in codes
        assert result.profile_matched is None

    def test_different_module_names_also_admitted_via_cohort(self):
        """
        Template '{new_module}' expands to any module name — not just calibration.
        Any new test + topology.yaml pair should be admitted.
        """
        binding = _make_binding_with_cohort()
        for module_name in ["venue_adapter", "settlement_helper", "platt_calibrator"]:
            files = [f"tests/test_{module_name}.py", "architecture/test_topology.yaml"]
            result = admit(intent=INTENT, files=files, binding=binding)
            assert result.ok is True, f"Expected admission for module '{module_name}'"
            assert result.profile_matched == "test_suite"

    def test_wrong_intent_still_conflicts(self):
        """
        Cohort only applies to create_new. modify_existing on the same files
        must still produce composition_conflict (pattern not closed for that intent).
        """
        binding = _make_binding_with_cohort()
        result = admit(
            intent=Intent.modify_existing,
            files=CHANGE_FILES,
            binding=binding,
        )
        codes = {i.code for i in result.issues}
        assert "composition_conflict" in codes
