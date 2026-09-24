# Lifecycle: created=2026-07-22; last_reviewed=2026-07-22; last_reused=2026-07-22
# Purpose: Prevent agents from directly modifying the live checkout.
# Reuse: Run after changing the live-tree write or Git-mutation guard.
"""Regression tests for the live checkout agent-write boundary."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
DISPATCH_PATH = REPO_ROOT / ".claude" / "hooks" / "dispatch.py"
ROUTER_PATH = REPO_ROOT / ".codex" / "hooks" / "zeus-router.mjs"
# NOT a synthetic identity by design: DISPATCH_PATH is invoked as a subprocess
# (no monkeypatch reaches it), so LIVE_ROOT must equal whatever dispatch.py
# resolves as _MAIN_TREE or every "is this the live checkout?" assertion below
# silently degrades to always-False and the test stops proving anything.
# Derived the same way dispatch.py derives it — `--git-common-dir` names the
# live checkout's .git from inside any worktree — so this test pins the
# guard's behaviour rather than one machine's install path.
LIVE_ROOT = Path(
    os.environ.get("ZEUS_MAIN_TREE")
    or subprocess.run(
        ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
).resolve()
if not os.environ.get("ZEUS_MAIN_TREE"):
    LIVE_ROOT = LIVE_ROOT.parent


def _dispatch(payload: dict) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(DISPATCH_PATH), "live_tree_write_guard"],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        check=False,
    )


def _edit_payload(path: str, cwd: Path) -> dict:
    return {
        "hook_event_name": "PreToolUse",
        "tool_name": "Edit",
        "cwd": str(cwd),
        "tool_input": {"file_path": path},
    }


def test_live_checkout_edit_is_blocked_by_absolute_and_relative_paths():
    absolute = _dispatch(_edit_payload(str(LIVE_ROOT / "src/main.py"), REPO_ROOT))
    relative = _dispatch(_edit_payload("src/main.py", LIVE_ROOT))

    assert absolute.returncode == 2
    assert relative.returncode == 2
    assert "live_tree_write_guard" in absolute.stderr


def test_linked_worktree_edit_is_allowed_but_cross_tree_live_edit_is_blocked(tmp_path):
    worktree = tmp_path / "worktree"
    allowed = _dispatch(_edit_payload("src/main.py", worktree))
    cross_tree = _dispatch(_edit_payload(str(LIVE_ROOT / "src/main.py"), worktree))

    assert allowed.returncode == 0
    assert cross_tree.returncode == 2


def test_unknown_codex_patch_from_live_is_blocked():
    result = _dispatch(
        {
            "hook_event_name": "PreToolUse",
            "tool_name": "apply_patch",
            "cwd": str(LIVE_ROOT),
            "tool_input": {"codex_original_tool_name": "apply_patch"},
        }
    )

    assert result.returncode == 2


def test_direct_live_git_mutations_are_blocked_but_ff_sync_is_allowed():
    def run(command: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(DISPATCH_PATH), "maintree_git_state_guard"],
            input=json.dumps(
                {
                    "hook_event_name": "PreToolUse",
                    "tool_name": "Bash",
                    "tool_input": {"command": command},
                }
            ),
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
            check=False,
        )

    commit = run(f"git -C {LIVE_ROOT} commit -m forbidden")
    merge = run(f"git -C {LIVE_ROOT} merge forbidden")
    dry_clean = run(f"git -C {LIVE_ROOT} clean -nd")
    ancestry_read = run(f"git -C {LIVE_ROOT} fetch && git -C {LIVE_ROOT} merge-base --is-ancestor a b")
    assert ancestry_read.returncode == 0
    # A bare git after `cd <linked worktree>` runs in that worktree, not in live.
    in_worktree = run(f"cd {REPO_ROOT} && git commit -m ok")
    assert in_worktree.returncode == (2 if REPO_ROOT == LIVE_ROOT else 0)
    back_to_live = run(f"cd {REPO_ROOT} && cd {LIVE_ROOT} && git commit -m forbidden")
    assert back_to_live.returncode == 2
    unresolvable = run(f"cd $WT && git commit -m unknown")
    assert unresolvable.returncode == (2 if Path.cwd().resolve() == LIVE_ROOT else 0)
    ff_sync = run(f"git -C {LIVE_ROOT} pull --ff-only")
    ff_sync_named = run(f"git -C {LIVE_ROOT} pull --ff-only origin live")
    other_pull = run(f"git -C {LIVE_ROOT} pull origin feature")

    assert commit.returncode == 2
    assert merge.returncode == 2
    assert dry_clean.returncode == 0
    assert ff_sync.returncode == 0
    assert ff_sync_named.returncode == 0
    assert other_pull.returncode == 2


def test_codex_router_denies_live_target_but_allows_worktree_target():
    def invoke(patch: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["node", str(ROUTER_PATH), "live_tree_write_guard"],
            input=json.dumps(
                {
                    "hookEventName": "PreToolUse",
                    "toolName": "apply_patch",
                    "cwd": str(REPO_ROOT),
                    "toolInput": {"command": patch},
                }
            ),
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
            check=False,
        )

    denied = invoke(f"*** Begin Patch\n*** Update File: {LIVE_ROOT}/src/main.py\n*** End Patch\n")
    allowed = invoke("*** Begin Patch\n*** Update File: src/main.py\n*** End Patch\n")

    assert denied.returncode == 0
    assert json.loads(denied.stdout)["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert allowed.returncode == 0
    assert not allowed.stdout.strip()


def test_codex_router_denies_direct_live_commit_but_allows_ff_sync():
    def invoke(command: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["node", str(ROUTER_PATH), "maintree_git_state_guard"],
            input=json.dumps(
                {
                    "hookEventName": "PreToolUse",
                    "toolName": "Bash",
                    "cwd": str(REPO_ROOT),
                    "toolInput": {"command": command},
                }
            ),
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
            check=False,
        )

    denied = invoke(f"git -C {LIVE_ROOT} commit -m forbidden")
    allowed = invoke(f"git -C {LIVE_ROOT} pull --ff-only")

    assert denied.returncode == 0
    assert json.loads(denied.stdout)["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert allowed.returncode == 0
    assert not allowed.stdout.strip()
