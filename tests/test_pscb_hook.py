# Created: 2026-04-25
# Last reused/audited: 2026-04-25
"""Behavioral tests for the soft-warn pre-commit hook (PSCB).

The hook lives at ``.zeus-githooks/pre-commit``. These tests verify
its observable contract end-to-end:

* ``test_hook_silent_when_in_scope`` -- staging only in-scope files
  produces no warning and exits ``0``.
* ``test_hook_warns_on_out_of_scope`` -- staging an out-of-scope file
  emits the standard ``[PSCB]`` block on stderr and still exits ``0``
  (soft-warn).
* ``test_hook_silent_when_no_active_packet`` -- a worktree without
  ``state/active_packet.txt`` gets no warning.
* ``test_hook_disabled_by_env`` -- ``PSCB_DISABLE=1`` opts out
  cleanly.
* ``test_hook_handles_corrupt_scope_file`` -- unreadable
  ``scope.yaml`` triggers a one-line stderr warning, no hard fail.
* ``test_hook_runs_under_real_git_commit`` -- end-to-end: ``git
  commit`` with ``core.hooksPath=.zeus-githooks`` succeeds even
  with a violation, mirroring the production install path.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "scripts"
HOOK_PATH = REPO_ROOT / ".zeus-githooks" / "pre-commit"
SCHEMA_PATH = REPO_ROOT / "architecture" / "scope_schema.json"


@pytest.fixture
def hook_repo(tmp_path: Path) -> Path:
    root = tmp_path / "hookrepo"
    root.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=root, check=True)
    subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=root, check=True)

    (root / "scripts").mkdir()
    shutil.copy(SCRIPTS_DIR / "_zpkt_scope.py", root / "scripts" / "_zpkt_scope.py")
    (root / "architecture").mkdir()
    shutil.copy(SCHEMA_PATH, root / "architecture" / "scope_schema.json")
    (root / ".zeus-githooks").mkdir()
    shutil.copy(HOOK_PATH, root / ".zeus-githooks" / "pre-commit")
    os.chmod(root / ".zeus-githooks" / "pre-commit", 0o755)

    # Seed a packet with a narrow scope.
    pkt = root / "docs" / "operations" / "task_2099-01-01_x"
    pkt.mkdir(parents=True)
    (pkt / "scope.yaml").write_text(
        "schema_version: 1\n"
        "packet: task_2099-01-01_x\n"
        "status: in_progress\n"
        "in_scope:\n"
        "  - docs/operations/task_2099-01-01_x/**\n"
        "  - scripts/legit.py\n"
        "allow_companions: []\n"
        "out_of_scope:\n"
        "  - state/zeus-world.db\n",
        encoding="utf-8",
    )
    (root / "state").mkdir()
    (root / "state" / "active_packet.txt").write_text("task_2099-01-01_x\n", encoding="utf-8")

    # Baseline commit so HEAD exists.
    (root / "README.md").write_text("seed\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "seed"], cwd=root, check=True)
    return root


def _run_hook(repo: Path, env_extra: dict | None = None) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [sys.executable, ".zeus-githooks/pre-commit"],
        cwd=repo, capture_output=True, text=True, env=env,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_hook_silent_when_in_scope(hook_repo: Path) -> None:
    pkt = hook_repo / "docs/operations/task_2099-01-01_x"
    (pkt / "good.md").write_text("ok", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=hook_repo, check=True)
    cp = _run_hook(hook_repo)
    assert cp.returncode == 0
    assert "[PSCB]" not in cp.stderr


def test_hook_warns_on_out_of_scope(hook_repo: Path) -> None:
    (hook_repo / "src").mkdir()
    (hook_repo / "src" / "main.py").write_text("touch", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=hook_repo, check=True)
    cp = _run_hook(hook_repo)
    assert cp.returncode == 0  # soft-warn
    assert "[PSCB]" in cp.stderr
    assert "src/main.py" in cp.stderr


def test_hook_silent_when_no_active_packet(hook_repo: Path) -> None:
    (hook_repo / "state" / "active_packet.txt").unlink()
    (hook_repo / "src").mkdir()
    (hook_repo / "src" / "main.py").write_text("touch", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=hook_repo, check=True)
    cp = _run_hook(hook_repo)
    assert cp.returncode == 0
    # No active packet -> hook is silent (advisory only).
    assert "[PSCB]" not in cp.stderr


def test_hook_disabled_by_env(hook_repo: Path) -> None:
    (hook_repo / "src").mkdir()
    (hook_repo / "src" / "main.py").write_text("touch", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=hook_repo, check=True)
    cp = _run_hook(hook_repo, env_extra={"PSCB_DISABLE": "1"})
    assert cp.returncode == 0
    assert "[PSCB]" not in cp.stderr


def test_hook_handles_corrupt_scope_file(hook_repo: Path) -> None:
    # Corrupt the scope file: invalid YAML
    (hook_repo / "docs/operations/task_2099-01-01_x/scope.yaml").write_text(
        ":\n: bad yaml\n", encoding="utf-8",
    )
    (hook_repo / "src").mkdir()
    (hook_repo / "src" / "main.py").write_text("touch", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=hook_repo, check=True)
    cp = _run_hook(hook_repo)
    assert cp.returncode == 0
    # Helper degrades gracefully — either silent or one-line skip notice.
    assert "Traceback" not in cp.stderr


def test_hook_runs_under_real_git_commit(hook_repo: Path) -> None:
    # Wire the hook in the same way `zpkt setup` does in production.
    subprocess.run(["git", "config", "core.hooksPath", ".zeus-githooks"],
                   cwd=hook_repo, check=True)
    (hook_repo / "src").mkdir()
    (hook_repo / "src" / "main.py").write_text("touch", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=hook_repo, check=True)
    cp = subprocess.run(
        ["git", "commit", "-m", "violation but soft"],
        cwd=hook_repo, capture_output=True, text=True,
    )
    assert cp.returncode == 0, f"git commit blocked unexpectedly: {cp.stderr}"
    assert "[PSCB]" in cp.stderr  # hook stderr is forwarded
