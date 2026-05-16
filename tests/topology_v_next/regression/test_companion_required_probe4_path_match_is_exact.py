# Created: 2026-05-15
# Last reused or audited: 2026-05-15
# Authority basis: docs/operations/task_2026-05-15_p2_companion_required_mechanism/SCAFFOLD.md §7 probe4
"""
probe4 — path match is exact (PurePosixPath normalization, case-sensitive).

Action 1: backup file present, real doc absent → missing_companion still emitted.
Action 2: case mismatch path → missing_companion still emitted.

Verifies that the gate uses exact POSIX path equality, not substring matching
or case-insensitive comparison (SCAFFOLD §7 probe4, §8.4).
"""
from __future__ import annotations

from scripts.topology_v_next.admission_engine import admit
from scripts.topology_v_next.dataclasses import (
    BindingLayer,
    CoverageMap,
    Intent,
    Severity,
)


_REQUIRED_DOC = "docs/reference/zeus_calibration_weighting_authority.md"
_SOURCE = "src/calibration/weighting.py"


def _make_binding() -> BindingLayer:
    cm = CoverageMap(
        profiles={
            "modify_calibration_weighting": (
                "src/calibration/*.py",
            ),
            # No docs_authority profile — companion doc is orphaned unless
            # pre-registered by _preregister_companion_paths.
            # .bak files are explicitly orphaned; case-mismatched paths are
            # coverage gaps (no profile matches them).
        },
        orphaned=(
            "tmp/**",
            "*.bak",
            "docs/reference/*.bak",
            "docs/reference/**/*.bak",
        ),
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
        companion_required={"modify_calibration_weighting": (_REQUIRED_DOC,)},
        companion_skip_tokens={},
    )


BINDING = _make_binding()


class TestProbe4PathMatchIsExact:
    def test_backup_file_does_not_satisfy_companion(self):
        """
        Submitting the .bak copy must NOT count as the real companion.
        Substring match would falsely admit; exact path required.
        """
        result = admit(
            intent=Intent.modify_existing,
            files=[_SOURCE, _REQUIRED_DOC + ".bak"],
            binding=BINDING,
        )
        codes = {i.code for i in result.issues}
        assert "missing_companion" in codes, (
            "Backup file should not satisfy the companion requirement"
        )

    def test_case_mismatch_does_not_satisfy_companion(self):
        """
        Path comparison is case-sensitive on the POSIX form (git semantics).
        DOCS/REFERENCE/... must NOT match docs/reference/...
        """
        wrong_case = _REQUIRED_DOC.upper()  # "DOCS/REFERENCE/ZEUS_CALIBRATION_..."
        result = admit(
            intent=Intent.modify_existing,
            files=[_SOURCE, wrong_case],
            binding=BINDING,
        )
        codes = {i.code for i in result.issues}
        assert "missing_companion" in codes, (
            "Case-mismatched path should not satisfy the companion requirement"
        )

    def test_exact_path_satisfies_companion(self):
        """Positive control: exact path must admit cleanly."""
        result = admit(
            intent=Intent.modify_existing,
            files=[_SOURCE, _REQUIRED_DOC],
            binding=BINDING,
        )
        codes = {i.code for i in result.issues}
        assert "missing_companion" not in codes

    def test_extra_slash_normalized(self):
        """PurePosixPath normalization collapses double slashes."""
        normalized = _REQUIRED_DOC.replace("docs/", "docs//")  # docs//reference/...
        result = admit(
            intent=Intent.modify_existing,
            files=[_SOURCE, normalized],
            binding=BINDING,
        )
        # PurePosixPath("docs//reference/foo.md").as_posix() == "docs/reference/foo.md"
        codes = {i.code for i in result.issues}
        assert "missing_companion" not in codes, (
            "PurePosixPath normalization should collapse double slashes"
        )
