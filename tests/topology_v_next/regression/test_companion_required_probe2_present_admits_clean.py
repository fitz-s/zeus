# Created: 2026-05-15
# Last reused or audited: 2026-05-15
# Authority basis: docs/operations/task_2026-05-15_p2_companion_required_mechanism/SCAFFOLD.md §7 probe2
"""
probe2 — companion_required present → clean ADMIT (§3.0 composition trap closed).

Setup: binding with modify_calibration_weighting requiring the calibration authority doc.
Action: admit(intent="modify_existing",
              files=["src/calibration/weighting.py",
                     "docs/reference/zeus_calibration_weighting_authority.md"])
Assert: decision.severity == Severity.ADMIT,
        no missing_companion issue,
        decision.ok is True.

This is the regression probe for the SCAFFOLD §3.0 unsolvable-trap closure.
Without the _preregister_companion_paths() hook in composition_rules.py, the
companion doc would expand touched_profiles to ≥2, causing composition_conflict
SOFT_BLOCK before _check_companion_required ever ran. The probe verifies the trap
is permanently closed: adding the required companion does not break composition.
"""
from __future__ import annotations

from scripts.topology_v_next.admission_engine import admit
from scripts.topology_v_next.dataclasses import (
    BindingLayer,
    CoverageMap,
    Intent,
    Severity,
)


def _make_binding() -> BindingLayer:
    cm = CoverageMap(
        profiles={
            "modify_calibration_weighting": (
                "src/calibration/*.py",
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
            "modify_calibration_weighting": (
                "docs/reference/zeus_calibration_weighting_authority.md",
            ),
        },
        companion_skip_tokens={},
    )


BINDING = _make_binding()
_COMPANION = "docs/reference/zeus_calibration_weighting_authority.md"
_SOURCE = "src/calibration/weighting.py"


class TestProbe2PresentAdmitsClean:
    def test_severity_is_admit(self):
        """§3.0 composition trap closed: source + companion → ADMIT."""
        result = admit(
            intent=Intent.modify_existing,
            files=[_SOURCE, _COMPANION],
            binding=BINDING,
        )
        assert result.severity == Severity.ADMIT

    def test_no_missing_companion_issue(self):
        result = admit(
            intent=Intent.modify_existing,
            files=[_SOURCE, _COMPANION],
            binding=BINDING,
        )
        codes = {i.code for i in result.issues}
        assert "missing_companion" not in codes

    def test_ok_is_true(self):
        result = admit(
            intent=Intent.modify_existing,
            files=[_SOURCE, _COMPANION],
            binding=BINDING,
        )
        assert result.ok is True

    def test_no_composition_conflict(self):
        """Regression: pre-registration must prevent composition_conflict."""
        result = admit(
            intent=Intent.modify_existing,
            files=[_SOURCE, _COMPANION],
            binding=BINDING,
        )
        codes = {i.code for i in result.issues}
        assert "composition_conflict" not in codes

    def test_profile_matched_is_source_profile(self):
        """Profile resolves to the source profile, not docs_authority."""
        result = admit(
            intent=Intent.modify_existing,
            files=[_SOURCE, _COMPANION],
            binding=BINDING,
        )
        assert result.profile_matched == "modify_calibration_weighting"
