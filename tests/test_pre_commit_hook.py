# Created: 2026-05-02
# Last reused/audited: 2026-05-02
# Authority basis: hook-marker-channel-b-fix-2026-05-02 (fix PR)
#
# Regression tests for .claude/hooks/pre-commit-invariant-test.sh
# Verifies that [skip-invariant] marker works in BOTH channels:
#   Channel A: agent PreToolUse (marker in $COMMAND)
#   Channel B: git pre-commit (marker detected via ps parent-process walk
#              or COMMIT_EDITMSG fallback)

import os
import subprocess
import tempfile
import textwrap
from pathlib import Path

import pytest

# Absolute path to the hook script (repo root / .claude / hooks / ...)
REPO_ROOT = Path(__file__).parent.parent
HOOK = REPO_ROOT / ".claude" / "hooks" / "pre-commit-invariant-test.sh"

SKIP_MARKER = "[skip-invariant]"


def _run_hook_channel_a(extra_env: dict | None = None, command: str = "") -> subprocess.CompletedProcess:
    """Invoke the hook in Channel A (agent / PreToolUse) mode.

    Feeds a minimal Claude hook JSON payload on stdin. The hook detects
    Channel A when basename($0) != 'pre-commit' and GIT_INDEX_FILE is unset.
    """
    import json
    payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": command}})
    env = {**os.environ}
    # Make sure we're not accidentally in git mode
    env.pop("GIT_INDEX_FILE", None)
    env["COMMIT_INVARIANT_TEST_SKIP"] = "0"
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        ["bash", str(HOOK)],
        input=payload,
        capture_output=True,
        text=True,
        env=env,
    )


def _run_hook_channel_b(
    extra_env: dict | None = None,
    commit_editmsg_content: str | None = None,
    parent_cmd: str | None = None,
) -> subprocess.CompletedProcess:
    """Invoke the hook in Channel B (git pre-commit) mode.

    Sets GIT_INDEX_FILE to trigger Channel B detection. Optionally writes
    a temporary COMMIT_EDITMSG and/or wraps the invocation in a parent
    process whose argv contains the marker (to exercise the ps-walk path).
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        # Minimal fake .git dir so git rev-parse succeeds (use real repo)
        env = {**os.environ, "GIT_INDEX_FILE": "fake-index"}
        env["COMMIT_INVARIANT_TEST_SKIP"] = "0"
        if extra_env:
            env.update(extra_env)

        if commit_editmsg_content is not None:
            # Write the marker into .git/COMMIT_EDITMSG in the real repo
            msg_path = REPO_ROOT / ".git" / "COMMIT_EDITMSG"
            original = msg_path.read_text() if msg_path.exists() else None
            try:
                msg_path.write_text(commit_editmsg_content)
                result = subprocess.run(
                    ["bash", str(HOOK)],
                    capture_output=True,
                    text=True,
                    env=env,
                    cwd=str(REPO_ROOT),
                )
            finally:
                if original is not None:
                    msg_path.write_text(original)
                elif msg_path.exists():
                    msg_path.unlink()
            return result

        if parent_cmd is not None:
            # Wrap hook invocation in a shell whose argv contains the marker,
            # simulating `git commit -m "... [skip-invariant] ..."`. The
            # wrapping shell becomes the PPID that the hook's ps-walk finds.
            wrapper = textwrap.dedent(f"""\
                #!/usr/bin/env bash
                # argv includes: {parent_cmd}
                exec bash {HOOK}
            """)
            wrapper_path = Path(tmpdir) / "fake_git_commit.sh"
            wrapper_path.write_text(wrapper)
            wrapper_path.chmod(0o755)

            # We need the parent process argv to include the marker, so we
            # invoke via a Python wrapper that exec's the script with the
            # marker as part of argv[0] or a flag — simpler: use bash -c with
            # a positional that contains the marker so `ps` shows it.
            bash_cmd = (
                f'bash -c \'exec -a "git commit -m \\"{parent_cmd}\\"" '
                f'bash {HOOK}\''
            )
            result = subprocess.run(
                bash_cmd,
                shell=True,
                capture_output=True,
                text=True,
                env=env,
                cwd=str(REPO_ROOT),
            )
            return result

        return subprocess.run(
            ["bash", str(HOOK)],
            capture_output=True,
            text=True,
            env=env,
            cwd=str(REPO_ROOT),
        )


# ---------------------------------------------------------------------------
# Channel A tests
# ---------------------------------------------------------------------------


class TestChannelAMarker:
    def test_marker_in_m_flag_skips(self):
        """Channel A: marker in -m value triggers SKIPPED."""
        cmd = f'git commit -m "bump version {SKIP_MARKER} baseline was already failing"'
        result = _run_hook_channel_a(command=cmd)
        assert result.returncode == 0, f"Expected skip, got:\n{result.stderr}"
        assert "SKIPPED" in result.stderr
        assert "channel=agent" in result.stderr

    def test_marker_in_heredoc_skips(self):
        """Channel A: marker inside heredoc body triggers SKIPPED."""
        cmd = (
            'git commit -m "$(cat <<\'EOF\'\n'
            f'Reconcile regression\n\n{SKIP_MARKER} reason\nEOF\n)"'
        )
        result = _run_hook_channel_a(command=cmd)
        assert result.returncode == 0, f"Expected skip, got:\n{result.stderr}"
        assert "SKIPPED" in result.stderr

    def test_no_marker_does_not_skip(self):
        """Channel A: absence of marker does NOT trigger SKIPPED (marker bypass not active).

        Uses an invalid ZEUS_HOOK_PYTEST_BIN so the hook fails fast rather
        than running the full pytest suite inside CI.
        """
        cmd = 'git commit -m "normal commit without marker"'
        result = _run_hook_channel_a(
            command=cmd,
            extra_env={"ZEUS_HOOK_PYTEST_BIN": "/no/such/python"},
        )
        # Hook must not have skipped via the marker path
        assert "SKIPPED (marker" not in result.stderr

    def test_non_commit_command_passes_through(self):
        """Channel A: non-git-commit commands exit 0 without running pytest."""
        result = _run_hook_channel_a(command="ls -la")
        assert result.returncode == 0
        assert "SKIPPED" not in result.stderr and "BLOCKED" not in result.stderr


# ---------------------------------------------------------------------------
# Channel B tests
# ---------------------------------------------------------------------------


class TestChannelBMarker:
    def test_commit_editmsg_fallback_skips(self):
        """Channel B: marker in COMMIT_EDITMSG (interactive editor path) triggers SKIPPED."""
        content = f"Fix something\n\n{SKIP_MARKER} interactive editor commit\n"
        result = _run_hook_channel_b(commit_editmsg_content=content)
        assert result.returncode == 0, f"Expected skip, got:\n{result.stderr}"
        assert "SKIPPED" in result.stderr
        assert "channel=git" in result.stderr

    def test_commit_editmsg_without_marker_does_not_skip(self):
        """Channel B: COMMIT_EDITMSG without marker does NOT trigger marker SKIPPED.

        Uses an invalid ZEUS_HOOK_PYTEST_BIN so the hook fails fast (BLOCKED)
        rather than running the full pytest suite inside CI.  The important
        assertion is that the marker skip path was NOT taken.
        """
        content = "Fix something without the marker\n"
        result = _run_hook_channel_b(
            commit_editmsg_content=content,
            extra_env={"ZEUS_HOOK_PYTEST_BIN": "/no/such/python"},
        )
        assert "SKIPPED (marker" not in result.stderr

    def test_ps_walk_skips_when_marker_in_parent_argv(self):
        """Channel B: marker in parent process argv (ps walk) triggers SKIPPED.

        This covers `git commit -m "... [skip-invariant] ..."` where
        COMMIT_EDITMSG has NOT been written yet when pre-commit fires.
        """
        marker_cmd = f"bump version {SKIP_MARKER} reason here"
        result = _run_hook_channel_b(parent_cmd=marker_cmd)
        assert result.returncode == 0, (
            f"Expected SKIPPED via ps walk, got returncode={result.returncode}.\n"
            f"stderr: {result.stderr}\nstdout: {result.stdout}"
        )
        assert "SKIPPED" in result.stderr
        assert "channel=git" in result.stderr


# ---------------------------------------------------------------------------
# Sentinel and env-var tests (both channels share these)
# ---------------------------------------------------------------------------


class TestSentinelBypass:
    def test_sentinel_file_skips_channel_b(self):
        """Sentinel .git/skip-invariant-once causes Channel B to skip and auto-delete it."""
        sentinel = REPO_ROOT / ".git" / "skip-invariant-once"
        sentinel.touch()
        try:
            result = _run_hook_channel_b()
        finally:
            sentinel.unlink(missing_ok=True)
        assert result.returncode == 0, f"Expected skip, got:\n{result.stderr}"
        assert "SKIPPED" in result.stderr
        assert not sentinel.exists(), "Sentinel should have been auto-deleted"

    def test_env_var_skips_channel_a(self):
        """COMMIT_INVARIANT_TEST_SKIP=1 causes Channel A to skip."""
        cmd = 'git commit -m "test env skip"'
        result = _run_hook_channel_a(
            extra_env={"COMMIT_INVARIANT_TEST_SKIP": "1"}, command=cmd
        )
        assert result.returncode == 0
        assert "SKIPPED" in result.stderr

    def test_env_var_skips_channel_b(self):
        """COMMIT_INVARIANT_TEST_SKIP=1 causes Channel B to skip."""
        result = _run_hook_channel_b(extra_env={"COMMIT_INVARIANT_TEST_SKIP": "1"})
        assert result.returncode == 0
        assert "SKIPPED" in result.stderr


# ---------------------------------------------------------------------------
# BLOCKED error message content test
# ---------------------------------------------------------------------------


class TestBlockedErrorMessage:
    def test_blocked_message_mentions_marker_and_sentinel(self):
        """BLOCKED output must mention the marker and sentinel, not only the env var."""
        # Run Channel B with no bypass — will block (pytest runs). We just
        # need the error message text; capture it regardless of pass/fail.
        result = _run_hook_channel_b()
        if result.returncode != 0:
            # Only validate if actually blocked (baseline might pass in CI)
            assert SKIP_MARKER in result.stderr, (
                "BLOCKED message must mention [skip-invariant] marker"
            )
            assert "skip-invariant-once" in result.stderr, (
                "BLOCKED message must mention sentinel file"
            )
            assert "COMMIT_INVARIANT_TEST_SKIP" in result.stderr, (
                "BLOCKED message must still mention env var"
            )


# ---------------------------------------------------------------------------
# Worktree-tolerant venv discovery
# ---------------------------------------------------------------------------


class TestWorktreeVenvDiscovery:
    """Pin the hook's worktree-tolerant venv fall-through.

    Fresh `git worktree add`-ed worktrees lack a local `.venv` because the
    canonical workspace at the main repo holds the only real venv. The hook
    must fall through to `<main_worktree>/.venv/bin/python` automatically.
    Without this fall-through the very first commit in a fresh worktree
    hits the BLOCKED "PYTEST_BIN not found" path until the operator manually
    `ln -s <canonical>/.venv .venv` — recurring friction documented in
    auto-memory.

    Implementation note (PR #57 review): the original tests duplicated
    the discovery shell into a probe in this file, which Copilot+Codex
    correctly flagged as locking in the parser bug (`awk '{print $2}'`
    truncates paths with spaces) and silently going green if the hook's
    real discovery drifts. These tests now invoke the actual hook
    binary in dry-run mode (`ZEUS_HOOK_DRY_RUN=1`) — discovery runs
    end-to-end against the real script. A dry-run line is printed and
    the hook exits 0 BEFORE running pytest, so we don't pay the full
    suite run-time per test.
    """

    @staticmethod
    def _run_hook_dry_run(cwd: str | Path, extra_env: dict | None = None):
        """Invoke the actual hook in Channel B + dry-run mode.

        Returns CompletedProcess. The DRY_RUN line lives in stderr,
        formatted as: ``[pre-commit-invariant-test] DRY_RUN: PYTEST_BIN=<path>``
        """
        env = {**os.environ, "GIT_INDEX_FILE": "fake-index",
               "COMMIT_INVARIANT_TEST_SKIP": "0", "ZEUS_HOOK_DRY_RUN": "1"}
        # Strip prior overrides from outer env so the test controls them
        env.pop("ZEUS_HOOK_PYTEST_BIN", None)
        if extra_env:
            env.update(extra_env)
        return subprocess.run(
            ["bash", str(HOOK)],
            capture_output=True, text=True, env=env, cwd=str(cwd),
            timeout=30,
        )

    @staticmethod
    def _parse_dry_run_pytest_bin(stderr: str) -> str:
        """Pull the resolved PYTEST_BIN out of the hook's DRY_RUN line."""
        marker = "DRY_RUN: PYTEST_BIN="
        for line in stderr.splitlines():
            if marker in line:
                return line.split(marker, 1)[1].strip()
        raise AssertionError(
            f"hook did not emit DRY_RUN line; stderr was:\n{stderr}"
        )

    def test_falls_through_to_main_worktree_venv_when_local_venv_missing(self, tmp_path):
        """Run the actual hook in a fresh `git worktree add`-ed sibling
        without `.venv`. The hook's discovery must locate the canonical
        worktree's `.venv/bin/python` and surface the INFO line.

        This is the core regression scenario: fresh worktree first commit
        currently fails with BLOCKED before this fix.
        """
        fresh_wt = tmp_path / "fresh worktree with space"  # space in path — pins parser fix
        result = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "worktree", "add", "--detach", str(fresh_wt)],
            capture_output=True, text=True,
        )
        assert result.returncode == 0, f"git worktree add failed: {result.stderr}"
        try:
            assert not (fresh_wt / ".venv").exists(), (
                "fresh worktree should not have .venv; precondition for the fallback"
            )
            run = self._run_hook_dry_run(fresh_wt)
            assert run.returncode == 0, (
                f"dry-run hook must exit 0; got {run.returncode}\nstderr={run.stderr}"
            )
            # The INFO line announces the fall-through choice.
            assert "using main-worktree venv" in run.stderr, (
                f"expected fall-through INFO line; stderr was:\n{run.stderr}"
            )
            resolved = self._parse_dry_run_pytest_bin(run.stderr)
            # The resolved interpreter must be the canonical workspace's
            # python — which is always at <some main wt>/.venv/bin/python
            # — and must actually exist.
            assert resolved.endswith("/.venv/bin/python"), (
                f"resolved path is not a venv python: {resolved!r}"
            )
            assert Path(resolved).exists(), (
                f"resolved venv python must exist on disk: {resolved!r}"
            )
            # Pin the parser fix: resolved path should match an actual
            # worktree (no truncation at spaces). The fresh worktree
            # itself has a space — the SIBLING (main) we resolved to
            # doesn't necessarily, but the parser fix is exercised in
            # test_parser_preserves_paths_with_spaces below.
        finally:
            subprocess.run(
                ["git", "-C", str(REPO_ROOT), "worktree", "remove", "--force", str(fresh_wt)],
                capture_output=True,
            )

    def test_operator_override_wins_even_when_local_venv_missing(self, tmp_path):
        """If the operator pinned ZEUS_HOOK_PYTEST_BIN, the fall-through
        must NOT override that choice. The hook's downstream BLOCKED path
        surfaces a bad pin (correct behavior); a fall-through that
        silently corrects an operator's explicit override would mask
        intentional version-pinning.
        """
        fresh_wt = tmp_path / "fresh_pin"
        subprocess.run(
            ["git", "-C", str(REPO_ROOT), "worktree", "add", "--detach", str(fresh_wt)],
            capture_output=True, check=True,
        )
        try:
            bad_pin = "/this/path/should/never/exist/python"
            run = self._run_hook_dry_run(fresh_wt, extra_env={"ZEUS_HOOK_PYTEST_BIN": bad_pin})
            assert run.returncode == 0, run.stderr
            resolved = self._parse_dry_run_pytest_bin(run.stderr)
            assert resolved == bad_pin, (
                f"operator pin must win; got {resolved!r} instead of {bad_pin!r}. "
                f"Discovery fall-through silently overrode the explicit pin."
            )
            # And the INFO fall-through line must NOT have been emitted —
            # operator override suppresses the discovery branch entirely.
            assert "using main-worktree venv" not in run.stderr, (
                f"INFO fall-through fired despite explicit operator pin: {run.stderr}"
            )
        finally:
            subprocess.run(
                ["git", "-C", str(REPO_ROOT), "worktree", "remove", "--force", str(fresh_wt)],
                capture_output=True,
            )

    def test_parser_preserves_paths_with_spaces(self, tmp_path):
        """The discovery parses `git worktree list --porcelain` to find
        the canonical worktree. The parser must preserve paths with
        spaces — a naive `awk '{print $2}'` truncates at the first space
        (Copilot+Codex P2 on the initial PR push).

        Strategy: stage a fake porcelain output file with a space-bearing
        path, run only the parser line through bash, assert the full
        path comes through.
        """
        # The exact parser line from the hook (no copy-paste of more
        # logic — just the one substitution + head we want to pin).
        porcelain = "worktree /Users/alice/Work Trees/zeus\nHEAD abc123\nbranch refs/heads/main\n\nworktree /tmp/fresh_wt\nHEAD def456\nbranch refs/heads/feature\n"
        fixture = tmp_path / "porcelain.txt"
        fixture.write_text(porcelain)
        # This invocation MUST mirror the hook's discovery byte-for-byte.
        # Linked to the hook by:
        #   1. The hook reads `sed -n 's/^worktree //p' | head -n1`
        #   2. The end-to-end tests above run the actual hook
        # If a future PR changes the parser in the hook, the e2e tests
        # catch the contract drift; this test pins the regex pattern
        # itself for the space-with-paths edge case.
        result = subprocess.run(
            ["sh", "-c", "sed -n 's/^worktree //p' < \"$1\" | head -n1", "--", str(fixture)],
            capture_output=True, text=True,
        )
        assert result.returncode == 0
        assert result.stdout.strip() == "/Users/alice/Work Trees/zeus", (
            f"parser truncated path with spaces: got {result.stdout.strip()!r}. "
            f"awk '{{print $2}}' would emit '/Users/alice/Work' — confirm sed/head is in the hook."
        )
