# Created: 2026-07-22
# Last reused/audited: 2026-07-22
"""Regression tests for the live checkout agent-write boundary."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
DISPATCH_PATH = REPO_ROOT / ".claude" / "hooks" / "dispatch.py"
ROUTER_PATH = REPO_ROOT / ".codex" / "hooks" / "zeus-router.mjs"
LIVE_ROOT = Path("/Users/leofitz/zeus")


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


def test_direct_live_git_mutations_are_blocked_but_cherry_pick_is_allowed():
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
    hot_pick = run(f"git -C {LIVE_ROOT} cherry-pick deadbeef")

    assert commit.returncode == 2
    assert merge.returncode == 2
    assert hot_pick.returncode == 0


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


def test_codex_router_denies_direct_live_commit_but_allows_cherry_pick():
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
    allowed = invoke(f"git -C {LIVE_ROOT} cherry-pick deadbeef")

    assert denied.returncode == 0
    assert json.loads(denied.stdout)["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert allowed.returncode == 0
    assert not allowed.stdout.strip()
