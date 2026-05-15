# Created: 2026-05-15
# Last reused or audited: 2026-05-15
# Authority basis: docs/operations/task_2026-05-15_p2_companion_required_mechanism/SCAFFOLD.md §7 probe1
"""
probe1 — companion_required missing companion emits MISSING_COMPANION issue.

Setup: binding with modify_calibration_weighting requiring
       docs/reference/zeus_calibration_weighting_authority.md, NO skip token.
Action: admit(intent="modify_existing", files=["src/calibration/weighting.py"])
Assert: decision.severity == Severity.ADVISORY (P2.a),
        exactly one issue with code == "missing_companion",
        message contains exact MISSING_COMPANION pattern,
        decision.ok is True (advisory does not fail admission in P2.a).
"""
from __future__ import annotations

from scripts.topology_v_next.admission_engine import admit
from scripts.topology_v_next.dataclasses import (
    BindingLayer,
    CohortDecl,
    CoverageMap,
    Intent,
    Severity,
)


def _make_calibration_binding(skip_token: str = "") -> BindingLayer:
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
    companion_skip_tokens: dict[str, str] = {}
    if skip_token:
        companion_skip_tokens["modify_calibration_weighting"] = skip_token

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
        companion_skip_tokens=companion_skip_tokens,
    )


BINDING = _make_calibration_binding()


class TestProbe1MissingCompanionEmitsIssue:
    def test_missing_companion_emits_advisory(self):
        result = admit(
            intent=Intent.modify_existing,
            files=["src/calibration/weighting.py"],
            binding=BINDING,
        )
        assert result.severity == Severity.ADVISORY

    def test_ok_is_true_in_p2a(self):
        """Advisory does not block admission in P2.a."""
        result = admit(
            intent=Intent.modify_existing,
            files=["src/calibration/weighting.py"],
            binding=BINDING,
        )
        assert result.ok is True

    def test_exactly_one_missing_companion_issue(self):
        result = admit(
            intent=Intent.modify_existing,
            files=["src/calibration/weighting.py"],
            binding=BINDING,
        )
        mc_issues = [i for i in result.issues if i.code == "missing_companion"]
        assert len(mc_issues) == 1

    def test_message_contains_exact_missing_companion_pattern(self):
        """SCAFFOLD §4.1: exact grep-able format."""
        result = admit(
            intent=Intent.modify_existing,
            files=["src/calibration/weighting.py"],
            binding=BINDING,
        )
        mc_issues = [i for i in result.issues if i.code == "missing_companion"]
        assert mc_issues, "Expected at least one missing_companion issue"
        msg = mc_issues[0].message
        assert "MISSING_COMPANION" in msg
        assert "profile=modify_calibration_weighting" in msg
        assert "missing_companion=docs/reference/zeus_calibration_weighting_authority.md" in msg
        assert "triggered_by=src/calibration/weighting.py" in msg

    def test_issue_path_is_companion_doc(self):
        result = admit(
            intent=Intent.modify_existing,
            files=["src/calibration/weighting.py"],
            binding=BINDING,
        )
        mc_issues = [i for i in result.issues if i.code == "missing_companion"]
        assert mc_issues[0].path == "docs/reference/zeus_calibration_weighting_authority.md"

    def test_metadata_populated(self):
        result = admit(
            intent=Intent.modify_existing,
            files=["src/calibration/weighting.py"],
            binding=BINDING,
        )
        mc_issues = [i for i in result.issues if i.code == "missing_companion"]
        meta = mc_issues[0].metadata
        assert meta["profile"] == "modify_calibration_weighting"
        assert meta["missing_companion"] == "docs/reference/zeus_calibration_weighting_authority.md"
        assert meta["triggered_by"] == "src/calibration/weighting.py"
