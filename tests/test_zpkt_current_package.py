# Created: 2026-05-22
# Last reused or audited: 2026-05-22
# Authority basis: PR-T3 zpkt placement compatibility brief; architecture/file_arrangement.yaml
"""Behavioral tests for zpkt default path (current/plans/<slug>/) and --new-package flag.

Coverage map:

* ``test_zpkt_start_creates_current_package_subtask`` -- default ``zpkt start <slug>``
  places PLAN.md + scope.yaml under ``docs/operations/current/plans/<slug>/`` with the
  new scope.yaml schema (id, status, owner, frontier, allowed_files, forbidden_files,
  live_side_effects_allowed, supersedes, tests, closeout_required).  Does NOT create
  a top-level task_*/ directory.

* ``test_zpkt_new_top_level_package_requires_explicit_flag`` -- calling ``zpkt start``
  WITHOUT ``--new-package`` never creates a ``docs/operations/task_*`` directory.
  Calling WITH ``--new-package`` creates the legacy directory and emits an advisory
  WARNING to stderr.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "scripts"
HELPER_PATH = SCRIPTS_DIR / "_zpkt_scope.py"
CLI_PATH = SCRIPTS_DIR / "zpkt.py"
SCHEMA_PATH = REPO_ROOT / "architecture" / "scope_schema.json"


@pytest.fixture
def synthetic_repo(tmp_path: Path) -> Path:
    """Minimal git repo with zpkt helpers wired in."""
    root = tmp_path / "synthrepo"
    root.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=root, check=True)
    subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=root, check=True)

    (root / "scripts").mkdir()
    shutil.copy(HELPER_PATH, root / "scripts" / "_zpkt_scope.py")
    shutil.copy(CLI_PATH, root / "scripts" / "zpkt.py")
    (root / "architecture").mkdir()
    shutil.copy(SCHEMA_PATH, root / "architecture" / "scope_schema.json")
    (root / "docs" / "operations").mkdir(parents=True)

    (root / "README.md").write_text("seed\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "seed"], cwd=root, check=True)
    return root


def _run(repo: Path, *args: str, expect_rc: int = 0) -> subprocess.CompletedProcess[str]:
    cmd = [sys.executable, "scripts/zpkt.py", *args]
    cp = subprocess.run(cmd, cwd=repo, capture_output=True, text=True)
    assert cp.returncode == expect_rc, (
        f"zpkt {' '.join(args)} -> rc={cp.returncode}\n"
        f"--- stdout ---\n{cp.stdout}\n--- stderr ---\n{cp.stderr}"
    )
    return cp


def test_zpkt_start_creates_current_package_subtask(synthetic_repo: Path) -> None:
    """Default zpkt start places work under current/plans/<slug>/, not a top-level task_ dir."""
    cp = _run(synthetic_repo, "start", "my_feature", "--inplace")
    payload = json.loads(cp.stdout)

    # Response carries new-schema fields.
    assert payload["slug"] == "my_feature"
    assert payload["plan_path"] == "docs/operations/current/plans/my_feature/PLAN.md"
    assert payload["scope_path"] == "docs/operations/current/plans/my_feature/scope.yaml"
    assert payload["packet_path"] == "current/plans/my_feature"

    plan_file = synthetic_repo / "docs" / "operations" / "current" / "plans" / "my_feature" / "PLAN.md"
    scope_file = plan_file.parent / "scope.yaml"
    assert plan_file.is_file(), f"PLAN.md not found at {plan_file}"
    assert scope_file.is_file(), f"scope.yaml not found at {scope_file}"

    # scope.yaml must use new schema fields.
    scope_doc = yaml.safe_load(scope_file.read_text(encoding="utf-8"))
    assert scope_doc["id"] == "my_feature"
    assert scope_doc["status"] == "active"
    assert scope_doc["owner"] == "agent"
    assert scope_doc["live_side_effects_allowed"] is False
    assert scope_doc["closeout_required"] is True
    assert "allowed_files" in scope_doc
    assert "forbidden_files" in scope_doc
    assert "frontier" in scope_doc
    assert "supersedes" in scope_doc
    assert "tests" in scope_doc

    # active_packet.txt pointer must point to the new path.
    pointer = synthetic_repo / "state" / "active_packet.txt"
    assert pointer.is_file()
    assert pointer.read_text(encoding="utf-8").strip() == "current/plans/my_feature"

    # No top-level task_ directory was created.
    ops_dir = synthetic_repo / "docs" / "operations"
    legacy_dirs = [d for d in ops_dir.iterdir() if d.is_dir() and d.name.startswith("task_")]
    assert not legacy_dirs, f"Unexpected legacy task_ dir(s) created: {legacy_dirs}"


def test_zpkt_new_top_level_package_requires_explicit_flag(synthetic_repo: Path) -> None:
    """--new-package creates legacy task_*/ dir and emits advisory WARNING; without flag, no task_ dir."""
    # Without flag: only current/plans/ path is created.
    cp = _run(synthetic_repo, "start", "no_flag_slug", "--inplace")
    assert json.loads(cp.stdout)["packet_path"] == "current/plans/no_flag_slug"
    ops_dir = synthetic_repo / "docs" / "operations"
    legacy_before = [d for d in ops_dir.iterdir() if d.is_dir() and d.name.startswith("task_")]
    assert not legacy_before, f"Unexpected task_ dir without --new-package: {legacy_before}"

    # With --new-package: legacy dir is created + WARNING to stderr.
    cp2 = _run(synthetic_repo, "start", "flagged_slug", "--inplace", "--date", "2099-05-22", "--new-package")
    payload2 = json.loads(cp2.stdout)
    assert payload2["packet"] == "task_2099-05-22_flagged_slug"
    legacy_after = [d for d in ops_dir.iterdir() if d.is_dir() and d.name.startswith("task_")]
    assert legacy_after, "Expected a task_* dir when --new-package is used"
    assert "WARNING" in cp2.stderr or "operator-gated" in cp2.stderr, (
        f"Expected advisory WARNING in stderr; got: {cp2.stderr!r}"
    )
