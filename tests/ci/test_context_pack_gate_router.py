# Created: 2026-05-26
# Last reused or audited: 2026-05-26
# Authority basis: docs/operations/current/plans/ci_topology_refactor_refined.md Phase E
#                  scripts/ci/context_pack_gate_router.py
"""
Unit + integration tests for the Phase E gate router.

Covers:
  - select_tests / select_static_gates filtering + dedup
  - build_gate_plan structure
  - CLI emit-tests / emit-gate-plan / stdout
  - End-to-end with topology_doctor_context_pack output
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "ci" / "context_pack_gate_router.py"

from scripts.ci.context_pack_gate_router import (
    build_gate_plan,
    select_static_gates,
    select_tests,
)
from scripts.topology_doctor_context_pack import assemble_context_packs


# ---------------------------------------------------------------------------
# Fixture bundles
# ---------------------------------------------------------------------------


def _bundle(packs: list[dict], matched_files: list[str] | None = None) -> dict:
    return {
        "schema_version": 1,
        "matched_files": matched_files or [],
        "packs": packs,
        "missing_surfaces_for_files": [],
        "review_required": [],
    }


def _pack(
    pid: str,
    *,
    tests: list[dict] | None = None,
    gates: list[dict] | None = None,
    chains: list[str] | None = None,
) -> dict:
    return {
        "id": pid,
        "required_relationship_tests": tests or [],
        "required_static_gates": gates or [],
        "failure_chains": [{"id": c} for c in (chains or [])],
        "matched_surfaces": [],
    }


# ---------------------------------------------------------------------------
# select_tests
# ---------------------------------------------------------------------------


def test_select_tests_returns_paths_in_order():
    b = _bundle([
        _pack("p1", tests=[
            {"path": "tests/a.py", "blocking": True},
            {"path": "tests/b.py", "blocking": True},
        ]),
        _pack("p2", tests=[
            {"path": "tests/c.py", "blocking": True},
        ]),
    ])
    assert select_tests(b) == ["tests/a.py", "tests/b.py", "tests/c.py"]


def test_select_tests_dedupes_across_packs():
    b = _bundle([
        _pack("p1", tests=[{"path": "tests/shared.py", "blocking": True}]),
        _pack("p2", tests=[{"path": "tests/shared.py", "blocking": True}]),
    ])
    assert select_tests(b) == ["tests/shared.py"]


def test_select_tests_blocking_only_filters_non_blocking():
    b = _bundle([
        _pack("p1", tests=[
            {"path": "tests/a.py", "blocking": True},
            {"path": "tests/b.py", "blocking": False},
        ]),
    ])
    assert select_tests(b, blocking_only=True) == ["tests/a.py"]
    assert select_tests(b, blocking_only=False) == ["tests/a.py", "tests/b.py"]


def test_select_tests_empty_bundle_returns_empty_list():
    assert select_tests(_bundle([])) == []


def test_select_tests_skips_entries_without_path():
    b = _bundle([_pack("p", tests=[{"blocking": True}, {"path": "tests/ok.py", "blocking": True}])])
    assert select_tests(b) == ["tests/ok.py"]


def test_select_tests_dedupe_false_preserves_duplicates():
    b = _bundle([
        _pack("p1", tests=[{"path": "tests/x.py", "blocking": True}]),
        _pack("p2", tests=[{"path": "tests/x.py", "blocking": True}]),
    ])
    assert select_tests(b, dedupe=False) == ["tests/x.py", "tests/x.py"]


# ---------------------------------------------------------------------------
# select_static_gates
# ---------------------------------------------------------------------------


def test_select_static_gates_returns_entries():
    b = _bundle([_pack("p", gates=[
        {"id": "g1", "script": "scripts/ci/x.py", "blocking": True},
        {"id": "g2", "script": "scripts/ci/y.py", "blocking": False},
    ])])
    out = select_static_gates(b)
    assert [g["id"] for g in out] == ["g1", "g2"]


def test_select_static_gates_blocking_only():
    b = _bundle([_pack("p", gates=[
        {"id": "g1", "script": "x", "blocking": True},
        {"id": "g2", "script": "y", "blocking": False},
    ])])
    out = select_static_gates(b, blocking_only=True)
    assert [g["id"] for g in out] == ["g1"]


def test_select_static_gates_dedupes_by_id():
    b = _bundle([
        _pack("p1", gates=[{"id": "g1", "script": "a", "blocking": True}]),
        _pack("p2", gates=[{"id": "g1", "script": "b", "blocking": True}]),
    ])
    out = select_static_gates(b)
    assert len(out) == 1 and out[0]["id"] == "g1"


# ---------------------------------------------------------------------------
# build_gate_plan
# ---------------------------------------------------------------------------


def test_build_gate_plan_has_required_fields():
    b = _bundle([_pack("p1", tests=[{"path": "tests/a.py", "blocking": True}], chains=["FC-01"])])
    plan = build_gate_plan(b)
    for key in ("schema_version", "matched_packs", "matched_files",
                "tests", "static_gates", "failure_chains", "review_required"):
        assert key in plan, f"plan missing {key}"
    assert plan["matched_packs"] == ["p1"]
    assert plan["tests"] == ["tests/a.py"]
    assert plan["failure_chains"] == ["FC-01"]


def test_build_gate_plan_failure_chains_dedup_first_occurrence():
    b = _bundle([
        _pack("p1", chains=["FC-01", "FC-02"]),
        _pack("p2", chains=["FC-01"]),
    ])
    assert build_gate_plan(b)["failure_chains"] == ["FC-01", "FC-02"]


def test_build_gate_plan_passes_through_review_required():
    b = _bundle([], matched_files=[])
    b["review_required"] = ["no surface matched"]
    plan = build_gate_plan(b)
    assert plan["review_required"] == ["no surface matched"]


# ---------------------------------------------------------------------------
# CLI behavior
# ---------------------------------------------------------------------------


def _run_cli(*args, stdin: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True, text=True, timeout=15,
        input=stdin, check=False,
    )


def test_cli_reads_stdin_when_no_path(tmp_path):
    b = _bundle([_pack("p", tests=[{"path": "tests/x.py", "blocking": True}])])
    r = _run_cli(stdin=json.dumps(b))
    assert r.returncode == 0
    assert "tests/x.py" in r.stdout


def test_cli_reads_file_path(tmp_path):
    b = _bundle([_pack("p", tests=[{"path": "tests/x.py", "blocking": True}])])
    bundle_path = tmp_path / "bundle.json"
    bundle_path.write_text(json.dumps(b))
    r = _run_cli("--context-packs", str(bundle_path))
    assert r.returncode == 0
    assert "tests/x.py" in r.stdout


def test_cli_emit_tests_writes_newline_delimited(tmp_path):
    b = _bundle([_pack("p", tests=[
        {"path": "tests/a.py", "blocking": True},
        {"path": "tests/b.py", "blocking": True},
    ])])
    bundle_path = tmp_path / "b.json"
    tests_out = tmp_path / "tests.txt"
    bundle_path.write_text(json.dumps(b))
    r = _run_cli("--context-packs", str(bundle_path), "--emit-tests", str(tests_out))
    assert r.returncode == 0
    lines = tests_out.read_text().strip().splitlines()
    assert lines == ["tests/a.py", "tests/b.py"]


def test_cli_emit_gate_plan_writes_full_plan(tmp_path):
    b = _bundle([_pack("p", tests=[{"path": "tests/a.py", "blocking": True}],
                       gates=[{"id": "g1", "script": "x", "blocking": True}],
                       chains=["FC-01"])])
    bundle_path = tmp_path / "b.json"
    plan_out = tmp_path / "plan.json"
    bundle_path.write_text(json.dumps(b))
    r = _run_cli("--context-packs", str(bundle_path), "--emit-gate-plan", str(plan_out))
    assert r.returncode == 0
    plan = json.loads(plan_out.read_text())
    assert plan["tests"] == ["tests/a.py"]
    assert plan["static_gates"][0]["id"] == "g1"
    assert plan["failure_chains"] == ["FC-01"]


def test_cli_json_format_prints_full_plan_to_stdout():
    b = _bundle([_pack("p", tests=[{"path": "tests/a.py", "blocking": True}])])
    r = _run_cli("--format", "json", stdin=json.dumps(b))
    assert r.returncode == 0
    plan = json.loads(r.stdout)
    assert "tests" in plan and plan["tests"] == ["tests/a.py"]


def test_cli_malformed_input_returns_1():
    r = _run_cli(stdin="not json")
    assert r.returncode == 1


def test_cli_empty_input_returns_0_empty_plan():
    r = _run_cli(stdin="")
    assert r.returncode == 0
    # Empty bundle → empty tests, no stdout text body
    assert r.stdout.strip() == ""


def test_cli_blocking_only_filters_to_blocking_entries():
    b = _bundle([_pack("p", tests=[
        {"path": "tests/blocking.py", "blocking": True},
        {"path": "tests/optional.py", "blocking": False},
    ])])
    r = _run_cli("--blocking-only", stdin=json.dumps(b))
    assert "tests/blocking.py" in r.stdout
    assert "tests/optional.py" not in r.stdout


# ---------------------------------------------------------------------------
# End-to-end with topology_doctor_context_pack
# ---------------------------------------------------------------------------


def test_end_to_end_pr330_yields_exec_freshness_test():
    bundle = assemble_context_packs(
        ["src/engine/cycle_runtime.py", "src/execution/order_planner.py"],
    )
    plan = build_gate_plan(bundle, blocking_only=True)
    assert "tests/test_exec_freshness_recapture.py" in plan["tests"]
    assert "FC-03" in plan["failure_chains"]


def test_end_to_end_pr312_yields_bundle_selection_tests():
    bundle = assemble_context_packs(
        ["src/data/executable_forecast_reader.py",
         "src/data/forecast_extrema_authority.py"],
    )
    plan = build_gate_plan(bundle, blocking_only=True)
    assert "tests/test_executable_forecast_bundle_selection.py" in plan["tests"]
    assert "tests/test_executable_forecast_reader.py" in plan["tests"]
    assert "FC-01" in plan["failure_chains"]


def test_end_to_end_unmatched_files_yields_empty_plan_and_review_required():
    bundle = assemble_context_packs(["some/random/path.txt"])
    plan = build_gate_plan(bundle)
    assert plan["tests"] == []
    assert plan["static_gates"] == []
    assert plan["review_required"]


def test_end_to_end_multi_surface_unions_tests():
    bundle = assemble_context_packs(
        ["src/engine/cycle_runtime.py", "src/data/market_scanner.py"],
    )
    plan = build_gate_plan(bundle, blocking_only=True)
    # Both surfaces' tests should be in the plan
    assert "tests/test_exec_freshness_recapture.py" in plan["tests"]
    assert "tests/test_market_discovery_full_coverage.py" in plan["tests"]
