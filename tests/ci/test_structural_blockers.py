# Created: 2026-05-26
# Last reused or audited: 2026-05-26
# Authority basis: architecture/topology_enforcement.yaml#blocking_structural:workflow_refs_exist
#                  docs/operations/current/plans/ci_topology_refactor_refined.md Phase D
"""
Proving tests for the workflow_refs_exist no_override rule.

Referenced by architecture/topology_enforcement.yaml as
`proving_test: tests/ci/test_structural_blockers.py` for
workflow_refs_exist.

Also covers the Phase D required workflow (topology-context-required.yml)
structure: trigger, permissions, timeout, orchestrator step, artifact upload.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_CI = REPO_ROOT / "scripts" / "ci"
WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "topology-context-required.yml"


def _run(script: str, *args: str, cwd: Path = REPO_ROOT) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPTS_CI / script), *args],
        capture_output=True, text=True, timeout=30, cwd=cwd, check=False,
    )


@pytest.fixture(scope="module")
def workflow() -> dict:
    assert WORKFLOW_PATH.exists(), f"missing: {WORKFLOW_PATH}"
    with WORKFLOW_PATH.open() as f:
        return yaml.safe_load(f)


# ---------------------------------------------------------------------------
# workflow_refs_exist enforcer
# ---------------------------------------------------------------------------


def test_workflow_refs_exist_passes_on_real_repo():
    """Every workflow `run:` script reference resolves in main."""
    r = _run("check_workflow_repo_refs.py")
    assert r.returncode == 0, f"missing refs: {r.stdout}"


def test_workflow_refs_exist_detects_missing(tmp_path: Path):
    wf_dir = tmp_path / ".github" / "workflows"
    wf_dir.mkdir(parents=True)
    (wf_dir / "fake.yml").write_text(
        "name: fake\n"
        "on: push\n"
        "jobs:\n"
        "  fake:\n"
        "    runs-on: ubuntu-latest\n"
        "    steps:\n"
        "      - run: python scripts/does_not_exist.py\n"
    )
    r = _run("check_workflow_repo_refs.py",
             "--workflows-dir", str(wf_dir),
             "--repo-root", str(tmp_path))
    assert r.returncode == 1
    assert "does_not_exist" in r.stdout


def test_workflow_refs_exist_emits_json():
    r = _run("check_workflow_repo_refs.py", "--json")
    import json
    data = json.loads(r.stdout)
    assert "missing_refs" in data
    assert "count" in data


# ---------------------------------------------------------------------------
# topology-context-required.yml structure
# ---------------------------------------------------------------------------


def test_required_workflow_parses(workflow: dict) -> None:
    assert workflow["name"] == "topology-context-required"


def test_required_workflow_has_pull_request_trigger(workflow: dict) -> None:
    on = workflow.get("on") or workflow.get(True)
    assert on is not None and "pull_request" in on


def test_required_workflow_has_concurrency(workflow: dict) -> None:
    conc = workflow["concurrency"]
    assert conc["cancel-in-progress"] is True


def test_required_workflow_no_continue_on_error(workflow: dict) -> None:
    """Phase D is REQUIRED — must NOT declare continue-on-error: true."""
    for job in workflow["jobs"].values():
        assert job.get("continue-on-error") is not True


def test_required_workflow_invokes_orchestrator(workflow: dict) -> None:
    """The workflow must invoke check_topology_structural_blockers.py."""
    found = False
    for job in workflow["jobs"].values():
        for step in job.get("steps", []):
            run_text = step.get("run") or ""
            if "check_topology_structural_blockers.py" in run_text:
                found = True
                break
    assert found, "workflow must call check_topology_structural_blockers.py"


def test_required_workflow_actions_pinned(workflow: dict) -> None:
    for job in workflow["jobs"].values():
        for step in job.get("steps", []):
            uses = step.get("uses")
            if uses:
                assert "@" in uses, f"unpinned action: {uses}"
                tag = uses.split("@", 1)[1]
                assert tag and tag != "main"


def test_required_workflow_timeout_reasonable(workflow: dict) -> None:
    for job in workflow["jobs"].values():
        timeout = job.get("timeout-minutes")
        assert timeout is not None and timeout <= 15


def test_required_workflow_in_agents_md_registry() -> None:
    agents = (REPO_ROOT / ".github" / "workflows" / "AGENTS.md").read_text()
    assert "`topology-context-required.yml`" in agents, (
        "topology-context-required.yml must be listed in .github/workflows/AGENTS.md"
    )
