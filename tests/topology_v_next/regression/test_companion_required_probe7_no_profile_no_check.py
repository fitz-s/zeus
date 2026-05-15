# Created: 2026-05-15
# Last reused or audited: 2026-05-15
# Authority basis: docs/operations/task_2026-05-15_p2_companion_required_mechanism/SCAFFOLD.md §3.2
"""
probe7 — when profile_matched is None, _check_companion_required is a no-op.

SCAFFOLD §3.2: "if profile_id is None: return []"
Verifies that _check_companion_required does not emit any companion issue when
composition fails to resolve a profile (e.g. multi-profile without cohort resolution).
The companion gate should never add its own issue on top of an already-blocked admission.
"""
from __future__ import annotations

from scripts.topology_v_next.admission_engine import admit, _check_companion_required
from scripts.topology_v_next.dataclasses import (
    BindingLayer,
    CoverageMap,
    Intent,
    Severity,
)


def _make_binding_with_two_profiles() -> BindingLayer:
    """Two distinct profiles, no cohort — composition will return None."""
    cm = CoverageMap(
        profiles={
            "profile_a": ("src/module_a/*.py",),
            "profile_b": ("src/module_b/*.py",),
        },
        orphaned=("tmp/**",),
        hard_stop_paths=("src/execution/**",),
    )
    return BindingLayer(
        project_id="zeus",
        intent_extensions=(),
        coverage_map=cm,
        cohorts=(),
        severity_overrides={},
        high_fanout_hints=(),
        artifact_authority_status={},
        # Both profiles have companion_required declarations
        companion_required={
            "profile_a": ("docs/reference/authority_a.md",),
            "profile_b": ("docs/reference/authority_b.md",),
        },
        companion_skip_tokens={},
    )


BINDING = _make_binding_with_two_profiles()


class TestProbe7NoProfileNoCheck:
    def test_check_companion_required_returns_empty_when_profile_none(self):
        """Direct unit test: None profile_id → empty list."""
        issues = _check_companion_required(
            profile_id=None,
            files=["src/module_a/foo.py", "src/module_b/bar.py"],
            binding=BINDING,
        )
        assert issues == []

    def test_multi_profile_admit_no_companion_issue(self):
        """
        End-to-end: multi-profile composition_conflict does NOT also emit
        missing_companion (the companion gate is a no-op when profile is None).
        """
        result = admit(
            intent=Intent.modify_existing,
            files=["src/module_a/foo.py", "src/module_b/bar.py"],
            binding=BINDING,
        )
        codes = {i.code for i in result.issues}
        # composition_conflict is expected; missing_companion must NOT appear
        assert "composition_conflict" in codes
        assert "missing_companion" not in codes

    def test_profile_a_alone_emits_companion_issue(self):
        """Positive control: single-profile resolution does trigger the gate."""
        result = admit(
            intent=Intent.modify_existing,
            files=["src/module_a/foo.py"],
            binding=BINDING,
        )
        codes = {i.code for i in result.issues}
        assert "missing_companion" in codes

    def test_check_companion_required_empty_required_is_noop(self):
        """No companion_required for profile → empty list regardless of files."""
        binding_no_companion = BindingLayer(
            project_id="zeus",
            intent_extensions=(),
            coverage_map=CoverageMap(
                profiles={"plain": ("src/plain/*.py",)},
                orphaned=(),
                hard_stop_paths=(),
            ),
            cohorts=(),
            severity_overrides={},
            high_fanout_hints=(),
            artifact_authority_status={},
            companion_required={},  # empty — no declarations
            companion_skip_tokens={},
        )
        issues = _check_companion_required(
            profile_id="plain",
            files=["src/plain/foo.py"],
            binding=binding_no_companion,
        )
        assert issues == []
