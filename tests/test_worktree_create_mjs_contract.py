"""
# Created: 2026-05-18
# Last reused or audited: 2026-05-18
# Authority basis: Claude Code 2.1.143 binary string contract:
#   "WorktreeCreate hook failed: hook succeeded but returned no worktree path
#    (command: echo the path to stdout; http/callback: return
#    hookSpecificOutput.worktreePath)"
#   "Command hooks print the path on stdout instead."
#   feedback_hook_design_failure_cascades_to_discipline_violation.md

Antibody for the USER-GLOBAL WorktreeCreate hook
(~/.claude/hooks/worktree-create.mjs).

Companion to test_worktree_create_contract.py, which covers the in-repo
dispatch.py advisor only. That test passing is necessary but not
sufficient: the cwd-leakage bug seen 2026-05-17 (agents
a90cfda19f7ff32d3, a66c192ac98db4c54) lived in the user-global .mjs
handler, not in dispatch.py. The .mjs handler emitted a JSON envelope
(`{"continue":true,"hookSpecificOutput":{...,"worktreePath":"..."}}`)
on stdout. The Claude Code harness reads stdout LITERALLY as the
absolute worktree path for command-type hooks, so the spawned agent's
cwd became the JSON blob and every Bash/Read failed.

Contract this test enforces:
  - stdout is EITHER empty (no-op) OR a single absolute path + newline.
  - stdout MUST NOT contain `{`, `[`, `"continue"`, or
    `hookSpecificOutput`. Any of those indicates a regression to the
    JSON-envelope shape that broke EnterWorktree 2026-05-17.

The file under test is out-of-repo
(`~/.claude/hooks/worktree-create.mjs`). When it is absent, the test
SKIPS rather than fails: the antibody only fires when the file is
present and edited in a way that resurrects the bug.
"""

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


_HOOK_PATH = Path(os.path.expanduser("~/.claude/hooks/worktree-create.mjs"))


def _node_available() -> bool:
    return shutil.which("node") is not None


def _run_hook(payload_json: str, repo_cwd: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["node", str(_HOOK_PATH)],
        input=payload_json,
        capture_output=True,
        text=True,
        timeout=30,
        cwd=repo_cwd,
    )


def _assert_stdout_is_pathish(stdout: str) -> None:
    """stdout must be empty OR a plain absolute path + optional newline.

    No JSON envelope tokens are allowed. The harness treats stdout as
    the literal cwd for the spawned agent (command-hook contract per
    Claude Code 2.1.143 binary strings).
    """
    forbidden_tokens = ("{", "[", '"continue"', "hookSpecificOutput",
                        "worktreePath", "hookEventName")
    for token in forbidden_tokens:
        assert token not in stdout, (
            f"WorktreeCreate hook stdout MUST NOT contain {token!r} "
            f"(JSON-envelope regression). Full stdout: {stdout!r}"
        )
    stripped = stdout.strip()
    if not stripped:
        return
    # Must be a single line that looks like an absolute filesystem path.
    assert "\n" not in stripped, (
        f"stdout must be a single path line, got multi-line: {stdout!r}"
    )
    assert stripped.startswith("/"), (
        f"stdout path must be absolute, got: {stripped!r}"
    )


@pytest.mark.skipif(
    not _HOOK_PATH.exists(),
    reason=f"user-global hook not installed at {_HOOK_PATH}",
)
@pytest.mark.skipif(
    not _node_available(),
    reason="node binary not on PATH",
)
def test_worktree_create_mjs_stdout_is_plain_path(tmp_path):
    """
    Run the hook with a fresh, non-colliding name and verify stdout is
    a plain path (no JSON). Cleans up the worktree it creates as a side
    effect.
    """
    repo_root = Path(__file__).resolve().parents[1]
    # Use a unique slug so the hook can create a fresh worktree.
    slug = f"antibody-mjs-stdout-{os.getpid()}"
    payload = (
        '{"name":"' + slug + '","cwd":"' + str(repo_root) + '"}'
    )
    proc = _run_hook(payload, str(repo_root))

    try:
        assert proc.returncode == 0, (
            f"hook exited {proc.returncode}; stderr={proc.stderr!r}"
        )
        _assert_stdout_is_pathish(proc.stdout)
        # When inside a git repo, stdout MUST be non-empty (hook created
        # a worktree and must report the path so EnterWorktree succeeds).
        assert proc.stdout.strip(), (
            "hook returned empty stdout inside a git repo; harness will "
            "report 'hook succeeded but returned no worktree path'"
        )
        emitted_path = Path(proc.stdout.strip())
        assert emitted_path.exists(), (
            f"hook claims worktree at {emitted_path} but path does not exist"
        )
    finally:
        # Cleanup any worktree+branch the hook created.
        emitted = proc.stdout.strip()
        if emitted and Path(emitted).exists():
            subprocess.run(
                ["git", "-C", str(repo_root), "worktree", "remove",
                 "--force", emitted],
                capture_output=True, text=True, timeout=15,
            )
        # Best-effort branch cleanup. The hook prefixes branches with
        # "claude/" and uses the slug.
        for candidate in (f"claude/{slug}", f"claude/{slug}"):
            subprocess.run(
                ["git", "-C", str(repo_root), "branch", "-D", candidate],
                capture_output=True, text=True, timeout=5,
            )


@pytest.mark.skipif(
    not _HOOK_PATH.exists(),
    reason=f"user-global hook not installed at {_HOOK_PATH}",
)
def test_worktree_create_mjs_source_has_no_json_envelope_on_stdout():
    """
    Static check: the source file must not write a JSON envelope to
    stdout. This catches regressions even on systems where node is
    unavailable to run the dynamic probe.
    """
    src = _HOOK_PATH.read_text()
    # Whitelist: comments and string-literal mentions of the legacy
    # shape (in docstrings explaining the bug) are fine. The DANGEROUS
    # pattern is the actual JS expression `process.stdout.write(JSON.`
    # which is the exact mechanism that emitted the JSON envelope.
    dangerous = re.compile(r"process\.stdout\.write\s*\(\s*JSON\.")
    assert not dangerous.search(src), (
        "WorktreeCreate hook regression: process.stdout.write(JSON.*) "
        "is the legacy JSON-envelope wire that broke EnterWorktree on "
        "2026-05-17. Command-type WorktreeCreate hooks must emit the "
        "worktree path as a plain string on stdout."
    )


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
