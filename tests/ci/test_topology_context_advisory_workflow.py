# Created: 2026-05-26
# Last reused or audited: 2026-05-26
# Authority basis: docs/operations/current/plans/ci_topology_refactor_refined.md Phase C
#                  .github/workflows/topology-context-advisory.yml
"""
Structural validation tests for the Phase C advisory workflow yaml.

Verifies the workflow:
  - parses as valid YAML
  - declares pull_request trigger
  - has continue-on-error: true on its single job
  - references scripts that actually exist (workflow_refs_exist no-override rule)
  - has a reasonable timeout
  - declares pull-requests:write permission (required for the comment poster)
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "topology-context-advisory.yml"


@pytest.fixture(scope="module")
def workflow() -> dict:
    assert WORKFLOW_PATH.exists(), f"missing: {WORKFLOW_PATH}"
    with WORKFLOW_PATH.open() as f:
        return yaml.safe_load(f)


def test_workflow_yaml_parses(workflow: dict) -> None:
    assert workflow["name"] == "topology-context-advisory"


def test_workflow_has_pull_request_trigger(workflow: dict) -> None:
    # PyYAML parses bare `on:` as boolean True; the key is True after load.
    on = workflow.get("on") or workflow.get(True)
    assert on is not None
    assert "pull_request" in on
    types = on["pull_request"]["types"]
    assert "opened" in types
    assert "synchronize" in types


def test_workflow_paths_filter_includes_money_path(workflow: dict) -> None:
    on = workflow.get("on") or workflow.get(True)
    paths = on["pull_request"].get("paths", [])
    assert "src/**" in paths
    assert "architecture/**" in paths


def test_workflow_continue_on_error_true(workflow: dict) -> None:
    """Phase C is advisory — must never block the build."""
    jobs = workflow["jobs"]
    for job_name, job in jobs.items():
        assert job.get("continue-on-error") is True, (
            f"job {job_name!r} must declare continue-on-error: true"
        )


def test_workflow_has_pr_write_permission(workflow: dict) -> None:
    """Required by post_pr_context_pack_comment.py (needs to POST a comment)."""
    perms = workflow["permissions"]
    assert perms["pull-requests"] == "write"


def test_workflow_timeout_reasonable(workflow: dict) -> None:
    """≤10 min — context-pack assembly should be sub-second on any repo."""
    jobs = workflow["jobs"]
    for job in jobs.values():
        timeout = job.get("timeout-minutes")
        assert timeout is not None and timeout <= 10


def test_referenced_scripts_exist(workflow: dict) -> None:
    """Workflow_refs_exist no-override rule: every script the workflow runs
    must be a real file in the repo."""
    must_exist = [
        REPO_ROOT / "scripts" / "topology_doctor_context_pack.py",
        REPO_ROOT / "scripts" / "ci" / "post_pr_context_pack_comment.py",
    ]
    for path in must_exist:
        assert path.exists(), f"workflow references {path} but it does not exist"


def test_workflow_concurrency_group_cancels_in_progress(workflow: dict) -> None:
    """Per-PR concurrency: a new push cancels the prior in-flight run."""
    conc = workflow.get("concurrency")
    assert conc is not None
    assert conc.get("cancel-in-progress") is True
    assert "pull_request" in conc.get("group", "")


def test_workflow_uses_actions_with_pinned_majors(workflow: dict) -> None:
    """Pin actions to a major version (e.g. @v4) — avoid unpinned `main`."""
    jobs = workflow["jobs"]
    for job in jobs.values():
        for step in job.get("steps", []):
            uses = step.get("uses")
            if uses is None:
                continue
            assert "@" in uses, f"action {uses!r} must be pinned with @<version>"
            tag = uses.split("@", 1)[1]
            assert tag and tag != "main", (
                f"action {uses!r} must not use unpinned @main"
            )


def test_workflow_uploads_pack_artifact(workflow: dict) -> None:
    """Per spec: pack JSON + markdown uploaded as PR artifact (in addition to comment)."""
    jobs = workflow["jobs"]
    found = False
    for job in jobs.values():
        for step in job.get("steps", []):
            if step.get("uses", "").startswith("actions/upload-artifact"):
                found = True
                with_args = step.get("with", {})
                path = with_args.get("path", "")
                assert "context-packs" in path
                break
    assert found, "workflow must upload context-packs artifact"
