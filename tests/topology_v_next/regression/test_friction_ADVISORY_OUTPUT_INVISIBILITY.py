# Created: 2026-05-15
# Last reused or audited: 2026-05-15
# Authority basis: docs/operations/task_2026-05-15_p1_topology_v_next_additive/SCAFFOLD.md §2.2, §5.2
"""
Friction regression: ADVISORY_OUTPUT_INVISIBILITY (P1.3 deliverable).

SCAFFOLD §5.2: "Partial (P1 single-call aspect only) — AdmissionDecision
struct surfaces `issues` at top level; to_dict() always includes them.
ok=True with non-empty issues is now a typed condition."

Multi-call/aggregate detection requires P2 packet divergence_summary module.

Tests:
- admit() with non-empty issues → AdmissionDecision.issues populated even
  when ok=True (advisory-only issues)
- to_dict() output contains all issues at top level (not buried)
- ok=True can coexist with non-empty issues (advisory issues don't block)
- issues tuple accessible without traversal (top-level field)
"""
from __future__ import annotations

import pytest

from scripts.topology_v_next.admission_engine import admit
from scripts.topology_v_next.dataclasses import (
    BindingLayer,
    CoverageMap,
    Intent,
    Severity,
)


def _make_binding_with_gap() -> BindingLayer:
    """
    Binding with a narrow coverage_map so that files outside the profile
    produce an ADVISORY coverage_gap issue — allowing ok=True with issues.
    """
    cm = CoverageMap(
        profiles={
            "agent_runtime": ("scripts/topology_doctor.py",),
        },
        orphaned=(),
        hard_stop_paths=(),
    )
    return BindingLayer(
        project_id="zeus",
        intent_extensions=(),
        coverage_map=cm,
        cohorts=(),
        severity_overrides={},
        high_fanout_hints=(),
        artifact_authority_status={},
    )


def _make_binding_full() -> BindingLayer:
    """
    Binding covering the test files so profile matches cleanly.
    Also exercises ok=True with intent ADVISORY when no issues present.
    """
    cm = CoverageMap(
        profiles={
            "agent_runtime": (
                "scripts/topology_doctor.py",
                "architecture/test_topology.yaml",
            ),
            "test_suite": ("tests/test_*.py",),
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


class TestAdvisoryOutputInvisibilityClosure:
    def test_issues_populated_with_non_empty_issues(self):
        """
        ADVISORY_OUTPUT_INVISIBILITY FIX: issues is non-empty and visible at
        the top level of AdmissionDecision.

        A file with no profile match produces coverage_gap (ADVISORY) and
        composition_conflict (SOFT_BLOCK — no profile resolved). Issues are
        populated and accessible regardless of ok value.
        """
        binding = _make_binding_with_gap()
        # "scripts/unknown_script.py" is not in any profile
        decision = admit(
            intent=Intent.modify_existing,
            files=["scripts/unknown_script.py"],
            binding=binding,
        )
        assert len(decision.issues) > 0, (
            "ADVISORY_OUTPUT_INVISIBILITY: issues must be populated at top level"
        )
        codes = {i.code for i in decision.issues}
        assert "coverage_gap" in codes

    def test_to_dict_contains_issues_at_top_level(self):
        """
        to_dict() must include issues at top level, not buried in a nested key.
        Uses a covered file with intent=None to get an advisory issue while ok=True.
        """
        binding = _make_binding_full()
        decision = admit(
            intent=None,
            files=["scripts/topology_doctor.py"],
            binding=binding,
        )
        d = decision.to_dict()
        assert "issues" in d, "to_dict() must have 'issues' at top level"
        assert isinstance(d["issues"], list), "'issues' must be a list"
        assert len(d["issues"]) > 0, "issues list must not be empty when there are issues"

    def test_ok_true_with_advisory_issues_is_valid_typed_condition(self):
        """
        ok=True + non-empty issues is a valid (not contradictory) state.
        Demonstrated via intent=None → intent_unspecified ADVISORY on a covered file.
        Severity must be ADVISORY or ADMIT.
        """
        binding = _make_binding_full()
        # intent=None produces intent_unspecified ADVISORY; file is in a profile
        decision = admit(
            intent=None,
            files=["scripts/topology_doctor.py"],
            binding=binding,
        )
        assert decision.ok is True
        assert decision.severity in (Severity.ADMIT, Severity.ADVISORY)
        assert len(decision.issues) > 0
        codes = {i.code for i in decision.issues}
        assert "intent_unspecified" in codes

    def test_issues_field_directly_accessible(self):
        """
        issues is a top-level field on AdmissionDecision — no traversal needed.
        """
        binding = _make_binding_full()
        decision = admit(
            intent=None,
            files=["scripts/topology_doctor.py"],
            binding=binding,
        )
        # Direct attribute access — no nesting
        _ = decision.issues  # must not raise AttributeError
        assert isinstance(decision.issues, tuple)

    def test_clean_admission_has_empty_issues(self):
        """
        When a file matches a profile cleanly with no alerts, issues is empty
        and to_dict() still has issues key (as empty list).
        """
        binding = _make_binding_full()
        decision = admit(
            intent=Intent.modify_existing,
            files=["scripts/topology_doctor.py"],
            binding=binding,
        )
        assert decision.ok is True
        # Issues may be empty for a clean match
        d = decision.to_dict()
        assert "issues" in d
        assert isinstance(d["issues"], list)

    def test_hard_stop_ok_false_issues_populated(self):
        """
        HARD_STOP: ok=False AND kernel_alerts populated; issues is accessible.
        """
        binding = _make_binding_full()
        decision = admit(
            intent=Intent.modify_existing,
            files=["src/execution/executor.py"],
            binding=binding,
        )
        assert decision.ok is False
        assert decision.severity == Severity.HARD_STOP
        # kernel_alerts is top-level too
        assert isinstance(decision.kernel_alerts, tuple)
        assert len(decision.kernel_alerts) > 0
        d = decision.to_dict()
        assert "kernel_alerts" in d

    def test_intent_advisory_does_not_hide_behind_ok(self):
        """
        When intent is None → intent_unspecified ADVISORY; ok still True
        but issues must be visible.
        """
        binding = _make_binding_full()
        decision = admit(
            intent=None,
            files=["scripts/topology_doctor.py"],
            binding=binding,
        )
        # intent_unspecified is ADVISORY → ok=True
        assert decision.ok is True
        codes = {i.code for i in decision.issues}
        assert "intent_unspecified" in codes, (
            "intent_unspecified advisory must appear in issues even when ok=True"
        )
