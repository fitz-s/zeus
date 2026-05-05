# Created: 2026-05-05
# Last reused/audited: 2026-05-05
# Authority basis: PR #64 Copilot review — worktree-list parser edge cases.
#
# Regression tests for .claude/hooks/post-merge-cleanup-reminder.sh
# Covers: paths-with-spaces and sibling-prefix worktree listing.
# Pattern mirrors tests/test_pre_commit_hook.py (subprocess + fixture approach).

import json
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
HOOK = REPO_ROOT / ".claude" / "hooks" / "post-merge-cleanup-reminder.sh"

# Minimal valid PostToolUse payload that triggers the hook (gh pr merge succeeded).
_PAYLOAD_TEMPLATE = json.dumps({
    "tool_name": "Bash",
    "tool_input": {"command": "gh pr merge 42 --merge"},
    "tool_response": {"exit_code": 0},
})


def _run_hook_with_porcelain(porcelain: str, tmp_path: Path) -> subprocess.CompletedProcess:
    """Invoke the hook with a fake `git worktree list --porcelain` output.

    Writes the porcelain to a fixture file, then places a git shim in PATH
    that cats the fixture for `worktree list` and delegates everything else
    to the real git.  Using a file avoids shell quoting/escaping issues with
    paths that contain spaces or special characters.
    """
    import os

    # Write porcelain content to a fixture file (safe from shell quoting issues).
    fixture = tmp_path / "porcelain.txt"
    fixture.write_text(porcelain)

    # Write the fake git shim — cat the fixture for worktree list.
    fake_git = tmp_path / "git"
    fake_git.write_text(
        "#!/usr/bin/env bash\n"
        'if [ "$1" = "worktree" ] && [ "$2" = "list" ]; then\n'
        f'  cat "{fixture}"\n'
        "  exit 0\n"
        "fi\n"
        # Delegate all other git subcommands to the real binary.
        f'exec "{subprocess.check_output(["which", "git"], text=True).strip()}" "$@"\n'
    )
    fake_git.chmod(0o755)

    env = {**os.environ, "PATH": f"{tmp_path}:{os.environ['PATH']}"}

    return subprocess.run(
        ["bash", str(HOOK)],
        input=_PAYLOAD_TEMPLATE,
        capture_output=True,
        text=True,
        env=env,
        cwd=str(REPO_ROOT),
    )


class TestWorktreeListParser:
    """Pin the sed-based porcelain parser against shell edge cases.

    Mirrors the style in TestWorktreeVenvDiscovery in test_pre_commit_hook.py:
    use the actual hook binary rather than duplicating parser logic in the test.
    """

    def test_worktree_path_with_embedded_space_is_preserved(self, tmp_path):
        """A linked worktree whose path contains a space must appear intact.

        A naive `awk '{print $2}'` truncates at the first space — the fix
        uses `sed -n 's/^worktree //p'` which preserves the full path.
        """
        porcelain = (
            "worktree /main/repo\n"
            "HEAD aaa000\n"
            "branch refs/heads/main\n"
            "\n"
            "worktree /linked/work trees/feature-branch\n"
            "HEAD bbb111\n"
            "branch refs/heads/feature\n"
            "\n"
        )
        result = _run_hook_with_porcelain(porcelain, tmp_path)
        assert result.returncode == 0, f"hook must exit 0; stderr={result.stderr}"
        # The linked worktree with a space must appear in the reminder output.
        assert "/linked/work trees/feature-branch" in result.stdout, (
            "Worktree path with embedded space was truncated or dropped. "
            f"stdout was:\n{result.stdout}"
        )

    def test_sibling_prefix_worktree_is_not_excluded(self, tmp_path):
        """A linked worktree whose path is a prefix of another must not be
        mistakenly excluded by the main-worktree grep.

        Example: main=/a/b, linked=/a/b-extra — a substring match on /a/b
        would wrongly swallow /a/b-extra. The fix uses `grep -vFx` (exact
        fixed-string line match), not a substring match.
        """
        porcelain = (
            "worktree /a/b\n"
            "HEAD aaa000\n"
            "branch refs/heads/main\n"
            "\n"
            "worktree /a/b-extra\n"
            "HEAD bbb111\n"
            "branch refs/heads/feature\n"
            "\n"
        )
        result = _run_hook_with_porcelain(porcelain, tmp_path)
        assert result.returncode == 0, f"hook must exit 0; stderr={result.stderr}"
        # /a/b is the main worktree — excluded. /a/b-extra is a linked worktree
        # with a path that starts with /a/b — must NOT be excluded.
        assert "/a/b-extra" in result.stdout, (
            "Sibling worktree /a/b-extra was wrongly excluded by prefix match. "
            "Check that grep uses -Fx (exact line match) not -F (substring). "
            f"stdout was:\n{result.stdout}"
        )
        # Sanity: the main worktree itself must not appear in the reminder list.
        lines = result.stdout.splitlines()
        worktree_lines = [l for l in lines if "worktree:" in l]
        assert not any(l.rstrip().endswith("/a/b  →  git worktree remove <path>")
                       or "worktree: /a/b " in l and "/a/b-" not in l
                       for l in worktree_lines), (
            f"Main worktree /a/b must not appear in output; got: {worktree_lines}"
        )
