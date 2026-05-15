# Created: 2026-05-15
# Last reused or audited: 2026-05-15
# Authority basis: docs/operations/task_2026-05-15_p1_topology_v_next_additive/SCAFFOLD.md §2.1
"""
Unit tests for scripts/topology_v_next/coverage_map.py.

Covers: multi-profile candidates, orphan detection, gap reporting,
union_candidate_profiles set algebra, glob/prefix matching.
"""
from __future__ import annotations

import pytest

from scripts.topology_v_next.dataclasses import (
    CoverageMap,
    IssueRecord,
    Severity,
)
from scripts.topology_v_next.coverage_map import (
    coverage_gaps,
    resolve_candidates,
    union_candidate_profiles,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_coverage_map(
    profiles: dict[str, tuple[str, ...]] | None = None,
    orphaned: tuple[str, ...] = (),
    hard_stop_paths: tuple[str, ...] = (),
) -> CoverageMap:
    return CoverageMap(
        profiles=profiles or {},
        orphaned=orphaned,
        hard_stop_paths=hard_stop_paths,
    )


STUB_CM = _make_coverage_map(
    profiles={
        "agent_runtime": (
            "scripts/topology_doctor.py",
            "scripts/topology_doctor_digest.py",
            "architecture/task_boot_profiles.yaml",
            "architecture/admission_severity.yaml",
            "architecture/test_topology.yaml",
            "docs/operations/AGENTS.md",
        ),
        "test_suite": (
            "tests/test_*.py",
            "tests/topology_v_next/**",
            "tests/fixtures/**",
        ),
    },
    orphaned=("tmp/**", "*.bak.*", ".gitignore"),
    hard_stop_paths=(
        "src/execution/**",
        "src/venue/**",
        "src/riskguard/**",
        "architecture/source_rationale.yaml",
        "state/zeus-world.db",
    ),
)


# ---------------------------------------------------------------------------
# Tests: resolve_candidates
# ---------------------------------------------------------------------------

class TestResolveCandidates:
    def test_empty_files_returns_empty_dict(self):
        result = resolve_candidates([], STUB_CM)
        assert result == {}

    def test_file_matching_single_profile(self):
        result = resolve_candidates(["scripts/topology_doctor.py"], STUB_CM)
        assert result == {"scripts/topology_doctor.py": {"agent_runtime"}}

    def test_file_matching_glob_pattern(self):
        result = resolve_candidates(["tests/test_calibration.py"], STUB_CM)
        assert result == {"tests/test_calibration.py": {"test_suite"}}

    def test_file_under_dir_glob(self):
        result = resolve_candidates(["tests/topology_v_next/test_foo.py"], STUB_CM)
        assert result == {"tests/topology_v_next/test_foo.py": {"test_suite"}}

    def test_file_matching_no_profile_returns_empty_set(self):
        result = resolve_candidates(["src/models/forecast.py"], STUB_CM)
        assert result == {"src/models/forecast.py": set()}

    def test_multiple_files_independent(self):
        result = resolve_candidates(
            ["scripts/topology_doctor.py", "tests/test_foo.py"],
            STUB_CM,
        )
        assert result["scripts/topology_doctor.py"] == {"agent_runtime"}
        assert result["tests/test_foo.py"] == {"test_suite"}

    def test_file_matching_multiple_profiles(self):
        """A file can match multiple profiles when patterns overlap."""
        cm = _make_coverage_map(
            profiles={
                "prof_a": ("shared/util.py",),
                "prof_b": ("shared/**",),
            }
        )
        result = resolve_candidates(["shared/util.py"], cm)
        assert result["shared/util.py"] == {"prof_a", "prof_b"}

    def test_hard_stop_file_returns_empty_set(self):
        """Hard-stop files are not in profile patterns; resolve returns empty set."""
        result = resolve_candidates(["src/execution/orders.py"], STUB_CM)
        assert result["src/execution/orders.py"] == set()

    def test_orphaned_file_returns_empty_set(self):
        result = resolve_candidates(["tmp/scratch.py"], STUB_CM)
        assert result["tmp/scratch.py"] == set()

    def test_direct_architecture_file(self):
        result = resolve_candidates(["architecture/test_topology.yaml"], STUB_CM)
        assert result["architecture/test_topology.yaml"] == {"agent_runtime"}

    def test_phrase_not_a_parameter(self):
        """resolve_candidates signature must NOT have a hint/phrase/task param."""
        import inspect
        sig = inspect.signature(resolve_candidates)
        param_names = set(sig.parameters.keys())
        assert "hint" not in param_names
        assert "phrase" not in param_names
        assert "task" not in param_names

    def test_empty_profiles_all_files_unmatched(self):
        cm = _make_coverage_map(profiles={})
        result = resolve_candidates(["scripts/foo.py", "tests/bar.py"], cm)
        assert all(v == set() for v in result.values())


# ---------------------------------------------------------------------------
# Tests: coverage_gaps
# ---------------------------------------------------------------------------

class TestCoverageGaps:
    def test_no_gaps_when_all_matched(self):
        candidates = {
            "scripts/topology_doctor.py": {"agent_runtime"},
            "tests/test_foo.py": {"test_suite"},
        }
        issues = coverage_gaps(candidates, STUB_CM)
        assert issues == []

    def test_gap_for_uncovered_file(self):
        candidates = {"src/models/forecast.py": set()}
        issues = coverage_gaps(candidates, STUB_CM)
        assert len(issues) == 1
        assert issues[0].code == "coverage_gap"
        assert issues[0].severity == Severity.ADVISORY
        assert issues[0].path == "src/models/forecast.py"

    def test_orphaned_file_not_a_gap(self):
        """tmp/** files are orphaned — not a gap even with empty profile set."""
        candidates = {"tmp/scratch.py": set()}
        issues = coverage_gaps(candidates, STUB_CM)
        assert issues == []

    def test_hard_stop_file_not_a_gap(self):
        """Hard-stop files are intentionally unprofilable."""
        candidates = {"src/execution/orders.py": set()}
        issues = coverage_gaps(candidates, STUB_CM)
        assert issues == []

    def test_mixed_gap_and_non_gap(self):
        candidates = {
            "scripts/topology_doctor.py": {"agent_runtime"},  # matched
            "src/new_module.py": set(),                        # true gap
            "tmp/debug.py": set(),                             # orphaned
        }
        issues = coverage_gaps(candidates, STUB_CM)
        assert len(issues) == 1
        assert issues[0].path == "src/new_module.py"

    def test_returns_issue_records(self):
        candidates = {"unlisted/file.py": set()}
        issues = coverage_gaps(candidates, STUB_CM)
        assert all(isinstance(i, IssueRecord) for i in issues)

    def test_multiple_gaps_reported(self):
        candidates = {
            "unlisted/a.py": set(),
            "unlisted/b.py": set(),
        }
        issues = coverage_gaps(candidates, STUB_CM)
        assert len(issues) == 2

    def test_empty_candidates_no_gaps(self):
        issues = coverage_gaps({}, STUB_CM)
        assert issues == []


# ---------------------------------------------------------------------------
# Tests: union_candidate_profiles
# ---------------------------------------------------------------------------

class TestUnionCandidateProfiles:
    def test_empty_candidates_returns_empty_set(self):
        assert union_candidate_profiles({}) == set()

    def test_single_file_single_profile(self):
        candidates = {"scripts/foo.py": {"agent_runtime"}}
        assert union_candidate_profiles(candidates) == {"agent_runtime"}

    def test_multiple_files_union_deduplicates(self):
        candidates = {
            "scripts/foo.py": {"agent_runtime"},
            "scripts/bar.py": {"agent_runtime"},
            "tests/test_foo.py": {"test_suite"},
        }
        assert union_candidate_profiles(candidates) == {"agent_runtime", "test_suite"}

    def test_files_with_empty_sets_contribute_nothing(self):
        candidates = {
            "scripts/foo.py": {"agent_runtime"},
            "unlisted/new.py": set(),
        }
        assert union_candidate_profiles(candidates) == {"agent_runtime"}

    def test_all_empty_returns_empty(self):
        candidates = {"a.py": set(), "b.py": set()}
        assert union_candidate_profiles(candidates) == set()

    def test_multi_profile_file_contributes_all(self):
        candidates = {"shared/util.py": {"prof_a", "prof_b"}}
        result = union_candidate_profiles(candidates)
        assert result == {"prof_a", "prof_b"}

    def test_returns_set_type(self):
        candidates = {"scripts/foo.py": {"agent_runtime"}}
        assert isinstance(union_candidate_profiles(candidates), set)
