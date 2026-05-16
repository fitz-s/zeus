# Created: 2026-05-15
# Last reused or audited: 2026-05-15
# Authority basis: docs/operations/task_2026-05-15_p1_topology_v_next_additive/SCAFFOLD.md §2.1
"""
Unit tests for scripts/topology_v_next/hard_safety_kernel.py.

Covers: every hard_stop_paths pattern flags at least one canonical file;
non-matching paths return empty; is_hard_stopped boolean; metadata fields.
"""
from __future__ import annotations

import pytest

from scripts.topology_v_next.dataclasses import (
    BindingLayer,
    CohortDecl,
    CoverageMap,
    Intent,
    IssueRecord,
    Severity,
)
from scripts.topology_v_next.hard_safety_kernel import is_hard_stopped, kernel_check


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_binding(hard_stop_paths: tuple[str, ...] = ()) -> BindingLayer:
    """Build a minimal BindingLayer with the given hard_stop_paths."""
    cm = CoverageMap(
        profiles={"agent_runtime": ("scripts/topology_doctor.py",)},
        orphaned=("tmp/**",),
        hard_stop_paths=hard_stop_paths,
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


# Binding replicating stub binding hard_stop_paths
STUB_HARD_STOPS: tuple[str, ...] = (
    "src/execution/**",
    "src/venue/**",
    "src/riskguard/**",
    "architecture/source_rationale.yaml",
    "architecture/city_truth_contract.yaml",
    "architecture/fatal_misreads.yaml",
    "state/zeus-world.db",
    "state/zeus-forecasts.db",
)

STUB_BINDING = _make_binding(hard_stop_paths=STUB_HARD_STOPS)


# ---------------------------------------------------------------------------
# Tests: kernel_check
# ---------------------------------------------------------------------------

class TestKernelCheck:
    def test_empty_files_returns_empty(self):
        alerts = kernel_check([], binding=STUB_BINDING)
        assert alerts == []

    def test_non_matching_file_returns_empty(self):
        alerts = kernel_check(["scripts/topology_doctor.py"], binding=STUB_BINDING)
        assert alerts == []

    def test_direct_path_match(self):
        alerts = kernel_check(
            ["architecture/source_rationale.yaml"], binding=STUB_BINDING
        )
        assert len(alerts) == 1
        assert alerts[0].code == "hard_stop_path"
        assert alerts[0].severity == Severity.HARD_STOP
        assert alerts[0].path == "architecture/source_rationale.yaml"

    def test_glob_dir_pattern_matches_file_under_dir(self):
        """src/execution/** must match src/execution/orders.py."""
        alerts = kernel_check(
            ["src/execution/orders.py"], binding=STUB_BINDING
        )
        assert len(alerts) == 1
        assert alerts[0].path == "src/execution/orders.py"
        assert "src/execution/**" in alerts[0].metadata["matched_pattern"]

    def test_glob_dir_pattern_matches_nested_file(self):
        alerts = kernel_check(
            ["src/riskguard/riskguard.py"], binding=STUB_BINDING
        )
        assert len(alerts) == 1

    def test_glob_dir_pattern_does_not_match_sibling_dir(self):
        """src/execution/** must NOT match src/execution_v2/foo.py."""
        alerts = kernel_check(
            ["src/execution_v2/foo.py"], binding=STUB_BINDING
        )
        assert alerts == []

    def test_multiple_hard_stop_files_returns_one_alert_each(self):
        alerts = kernel_check(
            [
                "src/execution/orders.py",
                "src/venue/polymarket.py",
                "architecture/fatal_misreads.yaml",
            ],
            binding=STUB_BINDING,
        )
        assert len(alerts) == 3
        assert all(a.severity == Severity.HARD_STOP for a in alerts)

    def test_mixed_files_only_hard_stop_flagged(self):
        alerts = kernel_check(
            [
                "scripts/topology_doctor.py",       # safe
                "src/execution/submit.py",           # hard stop
                "tests/test_calibration.py",         # safe
            ],
            binding=STUB_BINDING,
        )
        assert len(alerts) == 1
        assert alerts[0].path == "src/execution/submit.py"

    def test_metadata_has_matched_pattern(self):
        alerts = kernel_check(
            ["src/venue/client.py"], binding=STUB_BINDING
        )
        assert len(alerts) == 1
        assert "matched_pattern" in alerts[0].metadata
        assert alerts[0].metadata["matched_pattern"] == "src/venue/**"

    def test_returns_list_of_issue_records(self):
        alerts = kernel_check(["src/execution/a.py"], binding=STUB_BINDING)
        assert all(isinstance(a, IssueRecord) for a in alerts)

    def test_state_db_direct_match(self):
        alerts = kernel_check(["state/zeus-world.db"], binding=STUB_BINDING)
        assert len(alerts) == 1
        assert alerts[0].metadata["matched_pattern"] == "state/zeus-world.db"

    def test_no_hard_stops_in_binding(self):
        binding = _make_binding(hard_stop_paths=())
        alerts = kernel_check(["src/execution/anything.py"], binding=binding)
        assert alerts == []

    def test_all_stub_patterns_flagged_by_canonical_file(self):
        """Every stub binding hard_stop pattern must fire for at least one file."""
        canonical_files = {
            "src/execution/**": "src/execution/core.py",
            "src/venue/**": "src/venue/api.py",
            "src/riskguard/**": "src/riskguard/guard.py",
            "architecture/source_rationale.yaml": "architecture/source_rationale.yaml",
            "architecture/city_truth_contract.yaml": "architecture/city_truth_contract.yaml",
            "architecture/fatal_misreads.yaml": "architecture/fatal_misreads.yaml",
            "state/zeus-world.db": "state/zeus-world.db",
            "state/zeus-forecasts.db": "state/zeus-forecasts.db",
        }
        for pattern, canonical_file in canonical_files.items():
            alerts = kernel_check([canonical_file], binding=STUB_BINDING)
            assert any(a.path == canonical_file for a in alerts), (
                f"Pattern '{pattern}' did not fire for canonical file '{canonical_file}'"
            )


# ---------------------------------------------------------------------------
# Tests: is_hard_stopped
# ---------------------------------------------------------------------------

class TestIsHardStopped:
    def test_returns_true_when_any_file_matches(self):
        assert is_hard_stopped(
            ["scripts/ok.py", "src/execution/bad.py"], STUB_BINDING
        ) is True

    def test_returns_false_when_no_match(self):
        assert is_hard_stopped(
            ["scripts/topology_doctor.py", "tests/test_foo.py"], STUB_BINDING
        ) is False

    def test_returns_false_for_empty_files(self):
        assert is_hard_stopped([], STUB_BINDING) is False

    def test_single_hard_stop_file(self):
        assert is_hard_stopped(["state/zeus-forecasts.db"], STUB_BINDING) is True

    def test_consistent_with_kernel_check(self):
        """is_hard_stopped result must agree with kernel_check non-empty."""
        files = ["src/venue/submit.py", "tests/test_something.py"]
        stopped = is_hard_stopped(files, STUB_BINDING)
        alerts = kernel_check(files, binding=STUB_BINDING)
        assert stopped == bool(alerts)
