# Created: 2026-05-15
# Last reused or audited: 2026-05-15
# Authority basis: docs/operations/task_2026-05-15_p2_companion_required_mechanism/SCAFFOLD.md §4.1
"""
probe8 — multiple missing companions → one issue per missing doc.

SCAFFOLD §4.1: "One issue record per missing companion (a profile requiring 2 docs
both absent → 2 issues)."

Verifies that when a profile requires 2 authority docs and both are absent,
_check_companion_required emits 2 distinct missing_companion issues, each with
a unique path field naming the exact missing doc.
"""
from __future__ import annotations

from scripts.topology_v_next.admission_engine import admit
from scripts.topology_v_next.dataclasses import (
    BindingLayer,
    CoverageMap,
    Intent,
    Severity,
)


_SOURCE = "src/calibration/weighting.py"
_DOC_A = "docs/reference/zeus_calibration_weighting_authority.md"
_DOC_B = "docs/reference/zeus_calibration_platt_tuning_notes.md"


def _make_binding_two_companions() -> BindingLayer:
    cm = CoverageMap(
        profiles={
            "modify_calibration_weighting": (
                "src/calibration/*.py",
            ),
            "docs_authority": (
                "docs/reference/**",
            ),
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
        companion_required={
            "modify_calibration_weighting": (_DOC_A, _DOC_B),
        },
        companion_skip_tokens={},
    )


BINDING = _make_binding_two_companions()


class TestProbe8MultipleMissingCompanions:
    def test_two_missing_companions_emit_two_issues(self):
        result = admit(
            intent=Intent.modify_existing,
            files=[_SOURCE],
            binding=BINDING,
        )
        mc_issues = [i for i in result.issues if i.code == "missing_companion"]
        assert len(mc_issues) == 2, (
            f"Expected 2 missing_companion issues, got {len(mc_issues)}: "
            f"{[i.path for i in mc_issues]}"
        )

    def test_each_issue_names_distinct_doc(self):
        result = admit(
            intent=Intent.modify_existing,
            files=[_SOURCE],
            binding=BINDING,
        )
        mc_issues = [i for i in result.issues if i.code == "missing_companion"]
        paths = {i.path for i in mc_issues}
        assert _DOC_A in paths
        assert _DOC_B in paths

    def test_one_companion_present_one_missing(self):
        """Supplying one of two required companions reduces issues to exactly one."""
        result = admit(
            intent=Intent.modify_existing,
            files=[_SOURCE, _DOC_A],  # DOC_A present, DOC_B missing
            binding=BINDING,
        )
        mc_issues = [i for i in result.issues if i.code == "missing_companion"]
        assert len(mc_issues) == 1
        assert mc_issues[0].path == _DOC_B

    def test_both_companions_present_clean_admit(self):
        result = admit(
            intent=Intent.modify_existing,
            files=[_SOURCE, _DOC_A, _DOC_B],
            binding=BINDING,
        )
        codes = {i.code for i in result.issues}
        assert "missing_companion" not in codes
        assert result.ok is True

    def test_severity_is_advisory_with_two_missing(self):
        """Multiple missing companions → ADVISORY in P2.a, not higher."""
        result = admit(
            intent=Intent.modify_existing,
            files=[_SOURCE],
            binding=BINDING,
        )
        assert result.severity == Severity.ADVISORY
        assert result.ok is True
