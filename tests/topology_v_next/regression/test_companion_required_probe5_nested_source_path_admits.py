# Created: 2026-05-15
# Last reused or audited: 2026-05-15
# Authority basis: docs/operations/task_2026-05-15_p2_companion_required_mechanism/SCAFFOLD.md §7 probe5
"""
probe5 — nested source path + companion → ADMIT.

Setup: binding with modify_calibration_weighting covering src/calibration/*.py and
       src/calibration/sub/*.py, requiring the calibration authority doc.
Action: admit(intent="modify_existing",
              files=["src/calibration/sub/internal_helper.py",
                     "docs/reference/zeus_calibration_weighting_authority.md"])
Assert: decision.severity == Severity.ADMIT, no missing_companion issue.

Verifies that the gate operates on the resolved profile_id (post-composition),
not on lexical file-prefix matching. A deeply-nested source file within the
profile's glob coverage must trigger the gate and accept the companion correctly.
"""
from __future__ import annotations

from scripts.topology_v_next.admission_engine import admit
from scripts.topology_v_next.dataclasses import (
    BindingLayer,
    CoverageMap,
    Intent,
    Severity,
)


_COMPANION = "docs/reference/zeus_calibration_weighting_authority.md"
_NESTED_SOURCE = "src/calibration/sub/internal_helper.py"


def _make_binding() -> BindingLayer:
    cm = CoverageMap(
        profiles={
            "modify_calibration_weighting": (
                "src/calibration/*.py",
                "src/calibration/sub/*.py",  # nested glob
                "tests/test_calibration_*.py",
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
            "modify_calibration_weighting": (_COMPANION,),
        },
        companion_skip_tokens={},
    )


BINDING = _make_binding()


class TestProbe5NestedSourcePathAdmits:
    def test_nested_source_with_companion_admits(self):
        """Gate uses resolved profile_id, not lexical prefix."""
        result = admit(
            intent=Intent.modify_existing,
            files=[_NESTED_SOURCE, _COMPANION],
            binding=BINDING,
        )
        assert result.severity == Severity.ADMIT

    def test_no_missing_companion_when_companion_present(self):
        result = admit(
            intent=Intent.modify_existing,
            files=[_NESTED_SOURCE, _COMPANION],
            binding=BINDING,
        )
        codes = {i.code for i in result.issues}
        assert "missing_companion" not in codes

    def test_ok_is_true(self):
        result = admit(
            intent=Intent.modify_existing,
            files=[_NESTED_SOURCE, _COMPANION],
            binding=BINDING,
        )
        assert result.ok is True

    def test_nested_source_without_companion_emits_missing(self):
        """Negative control: nested source without companion → MISSING_COMPANION."""
        result = admit(
            intent=Intent.modify_existing,
            files=[_NESTED_SOURCE],
            binding=BINDING,
        )
        codes = {i.code for i in result.issues}
        assert "missing_companion" in codes

    def test_profile_matched_on_nested(self):
        """Nested source file resolves to the parent profile, not orphan/gap."""
        result = admit(
            intent=Intent.modify_existing,
            files=[_NESTED_SOURCE, _COMPANION],
            binding=BINDING,
        )
        assert result.profile_matched == "modify_calibration_weighting"
