import json
import subprocess
import sys
import os
from pathlib import Path

# Authority: user memory feedback_hook_design_failure_cascades_to_discipline_violation
# Created: 2026-05-17
# Antibody for WorktreeCreate hook contract: must return path via STDERR, not STDOUT

def test_worktree_create_contract():
    """
    Assert that the worktree_create_advisor hook returns an advisory to stderr,
    leaving stdout empty (Harness protocol requirement for WorktreeCreate).
    """
    repo_root = Path(__file__).resolve().parents[3]
    dispatch_py = repo_root / ".claude" / "hooks" / "dispatch.py"

    # Mock payload for WorktreeCreate
    payload = {
        "hook_event_name": "WorktreeCreate",
        "tool_name": "EnterWorktree",
        "tool_input": {"name": "test-worktree"},
        "worktree_path": "/tmp/test-worktree"
    }

    # Run dispatch.py
    proc = subprocess.run(
        [sys.executable, str(dispatch_py), "worktree_create_advisor"],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        cwd=str(repo_root)
    )

    assert proc.returncode == 0, f"Hook failed with stderr: {proc.stderr}"

    # THE CORE ASSERTIONS
    assert proc.stdout.strip() == "", f"WorktreeCreate MUST NOT emit JSON to stdout, got: {proc.stdout}"
    assert "[advisory:worktree_create_advisor]" in proc.stderr
    assert "[worktree_doctor] WorktreeCreate advisory" in proc.stderr

if __name__ == "__main__":
    test_worktree_create_contract()
    print("Test passed.")
