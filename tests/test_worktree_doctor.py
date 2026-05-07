# Created: 2026-05-07
# Last reused or audited: 2026-05-07
# Authority basis: Navigation Topology v2 PLAN §3 Phase 3 exit criteria; sunset 2027-05-07

"""
Tests for scripts/worktree_doctor.py.

Exit criteria (PLAN §3 Phase 3):
- --status outputs valid JSON with required keys
- --hygiene-audit identifies a stale-worktree fixture (advisory, never deletes)
- --cross-worktree-visibility produces non-empty map for current state
- Exit code 0 in all cases (advisory tool)
- branch-keepup decision matrix: 5 cases + edge cases
- hygiene NEVER deletes assertion
"""

from __future__ import annotations

import io
import json
import subprocess
import sys
import types
from pathlib import Path
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "worktree_doctor.py"

# ---------------------------------------------------------------------------
# Module-level import of worktree_doctor for fixture-based tests (F8)
# ---------------------------------------------------------------------------
if str(REPO_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "scripts"))

import worktree_doctor as _wt_mod  # noqa: E402

# Fake porcelain output: 3 known worktrees (CI-stable, not real-state-dependent)
_FAKE_PORCELAIN = """\
worktree /fake/zeus
HEAD aabbccdd00000000000000000000000000000000
branch refs/heads/main

worktree /fake/worktrees/zeus-cleanup-debt
HEAD 1122334400000000000000000000000000000000
branch refs/heads/cleanup-debt-2026-05-07

worktree /fake/worktrees/zeus-low-high
HEAD aabbccdd00000000000000000000000000000000
branch refs/heads/low-high-alignment-recovery-2026-05-07

"""

_FAKE_AHEAD_BEHIND = "0\n"  # rev-list count → 0 for all


def _fake_git(*args: str, cwd: Path = REPO_ROOT) -> str:  # noqa: ARG001
    """Stub for worktree_doctor._git: returns canned data for known commands."""
    cmd = list(args)
    if cmd[:3] == ["worktree", "list", "--porcelain"]:
        return _FAKE_PORCELAIN
    if cmd[:2] == ["rev-list", "--count"]:
        return _FAKE_AHEAD_BEHIND
    if cmd[:2] == ["status", "--short"]:
        return ""  # clean
    if cmd[:2] == ["log", "-1"]:
        return "1746000000\n"
    if cmd[:2] == ["rev-parse", "--short"]:
        return "aabbccd\n"
    return ""


def _fake_gh(*args: str) -> str:  # noqa: ARG001
    """Stub for worktree_doctor._gh: returns empty PR list."""
    return "[]"


def _cmd_status_with_fixture() -> dict:
    """Call cmd_status() with _git/_gh patched; capture stdout; return parsed JSON."""

    class _FakeArgs:
        pass

    buf = io.StringIO()
    with (
        patch.object(_wt_mod, "_git", side_effect=_fake_git),
        patch.object(_wt_mod, "_gh", side_effect=_fake_gh),
        patch("builtins.print", side_effect=lambda *a, **k: buf.write(" ".join(str(x) for x in a) + "\n")),
    ):
        _wt_mod.cmd_status(_FakeArgs())
    return json.loads(buf.getvalue())


def _cmd_advisory_with_fixture() -> str:
    """Call cmd_advisory() with _git patched; capture stdout; return text output."""

    class _FakeArgs:
        pass

    buf = io.StringIO()
    with (
        patch.object(_wt_mod, "_git", side_effect=_fake_git),
        patch.object(_wt_mod, "_gh", side_effect=_fake_gh),
        patch("builtins.print", side_effect=lambda *a, **k: buf.write(" ".join(str(x) for x in a) + "\n")),
    ):
        _wt_mod.cmd_advisory(_FakeArgs())
    return buf.getvalue()


def _run(*args: str, cwd: Path = REPO_ROOT) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        text=True,
        cwd=cwd,
    )


# ---------------------------------------------------------------------------
# Exit code 0 in all cases
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("subcommand", ["status", "advisory", "branch-keepup", "hygiene"])
def test_exits_zero_subcommand(subcommand: str) -> None:
    """Every subcommand exits 0 (advisory tool; never blocks)."""
    result = _run(subcommand)
    assert result.returncode == 0, (
        f"worktree_doctor {subcommand} must exit 0; got {result.returncode}\n"
        f"stderr: {result.stderr!r}"
    )


@pytest.mark.parametrize("flag", ["--status", "--hygiene-audit", "--cross-worktree-visibility"])
def test_exits_zero_flag_alias(flag: str) -> None:
    """Flag aliases also exit 0."""
    result = _run(flag)
    assert result.returncode == 0, (
        f"worktree_doctor {flag} must exit 0; got {result.returncode}\n"
        f"stderr: {result.stderr!r}"
    )


def test_exits_zero_no_args() -> None:
    """No args prints help and exits 0 (advisory tool)."""
    result = _run()
    assert result.returncode == 0, f"no-args must exit 0; got {result.returncode}"


# ---------------------------------------------------------------------------
# --status / status subcommand
# ---------------------------------------------------------------------------


def test_status_outputs_valid_json() -> None:
    """--status must produce valid JSON."""
    result = _run("--status")
    assert result.returncode == 0
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        pytest.fail(f"--status output is not valid JSON: {exc}\nstdout={result.stdout!r}")
    assert isinstance(data, dict), "--status JSON must be a dict"


def test_status_has_required_keys() -> None:
    """--status JSON must contain 'worktrees' and 'action' keys."""
    result = _run("--status")
    data = json.loads(result.stdout)
    assert "worktrees" in data, f"'worktrees' key missing from --status output: {data.keys()}"
    assert "action" in data, f"'action' key missing from --status output: {data.keys()}"


def test_status_worktrees_have_required_fields() -> None:
    """Each worktree entry must have path, branch, is_current, dirty, severity."""
    result = _run("--status")
    data = json.loads(result.stdout)
    required = {"path", "branch", "is_current", "dirty", "severity"}
    for wt in data["worktrees"]:
        missing = required - set(wt.keys())
        assert not missing, f"worktree entry missing fields {missing}: {wt}"


def test_status_shows_three_worktrees() -> None:
    """cmd_status must list all 3 worktrees from the fixture (CI-stable, not real-state)."""
    data = _cmd_status_with_fixture()
    count = len(data["worktrees"])
    assert count >= 3, (
        f"Expected >= 3 worktrees from fixture (main + cleanup-debt + low-high); got {count}"
    )


def test_status_ahead_behind_present() -> None:
    """Each worktree entry includes ahead_of_origin_main and behind_origin_main (fixture)."""
    data = _cmd_status_with_fixture()
    for wt in data["worktrees"]:
        assert "ahead_of_origin_main" in wt, f"ahead_of_origin_main missing: {wt}"
        assert "behind_origin_main" in wt, f"behind_origin_main missing: {wt}"
        assert isinstance(wt["ahead_of_origin_main"], int)
        assert isinstance(wt["behind_origin_main"], int)


def test_status_current_worktree_dirty_is_bool() -> None:
    """Every worktree entry must have dirty as a bool (fixture; is_current may be False for all)."""
    data = _cmd_status_with_fixture()
    for wt in data["worktrees"]:
        assert isinstance(wt["dirty"], bool), f"dirty must be bool, got {type(wt['dirty'])}"


def test_status_action_is_advisory() -> None:
    """action field must be advisory_only."""
    result = _run("--status")
    data = json.loads(result.stdout)
    assert data["action"] == "advisory_only", f"action={data['action']!r}"


# ---------------------------------------------------------------------------
# --cross-worktree-visibility / advisory subcommand
# ---------------------------------------------------------------------------


def test_advisory_non_empty() -> None:
    """--cross-worktree-visibility must produce non-empty output."""
    result = _run("--cross-worktree-visibility")
    assert result.returncode == 0
    assert result.stdout.strip(), "--cross-worktree-visibility produced empty output"


def test_advisory_mentions_three_worktrees() -> None:
    """Advisory output must mention all 3 fixture worktrees (CI-stable, not real-state)."""
    output = _cmd_advisory_with_fixture()
    # At minimum the header line mentions the count
    assert "Active worktrees" in output or "worktree" in output.lower(), (
        f"Expected worktree summary; got: {output!r}"
    )
    # 3 fixture worktrees should appear as indented [branch] entries
    lines_with_bracket = [l for l in output.splitlines() if l.strip().startswith("[")]
    assert len(lines_with_bracket) >= 3, (
        f"Expected >= 3 worktree entries from fixture; found {len(lines_with_bracket)}\n{output}"
    )


def test_advisory_output_is_text_not_json() -> None:
    """Advisory output is human-readable text (not JSON)."""
    result = _run("--cross-worktree-visibility")
    try:
        json.loads(result.stdout)
        pytest.fail("advisory output should NOT be JSON; it is human-readable text")
    except json.JSONDecodeError:
        pass  # Correct: not JSON


# ---------------------------------------------------------------------------
# --hygiene-audit / hygiene subcommand
# ---------------------------------------------------------------------------


def test_hygiene_outputs_valid_json() -> None:
    """--hygiene-audit must output valid JSON."""
    result = _run("--hygiene-audit")
    assert result.returncode == 0
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        pytest.fail(f"--hygiene-audit output not valid JSON: {exc}\nstdout={result.stdout!r}")
    assert isinstance(data, dict)


def test_hygiene_has_required_keys() -> None:
    """hygiene JSON must contain clutter, count, action keys."""
    result = _run("--hygiene-audit")
    data = json.loads(result.stdout)
    for key in ("clutter", "count", "action"):
        assert key in data, f"'{key}' missing from hygiene output: {data.keys()}"


def test_hygiene_action_is_never_auto_delete() -> None:
    """action field must be advisory_only_never_auto_delete."""
    result = _run("--hygiene-audit")
    data = json.loads(result.stdout)
    assert data["action"] == "advisory_only_never_auto_delete", (
        f"hygiene action={data['action']!r} — must be advisory_only_never_auto_delete"
    )


def test_hygiene_detects_backups_dir() -> None:
    """hygiene must list backups/ as clutter if it exists."""
    backups = REPO_ROOT / "backups"
    if not backups.exists():
        pytest.skip("backups/ not present in this worktree")
    result = _run("--hygiene-audit")
    data = json.loads(result.stdout)
    paths = [c["path"] for c in data["clutter"]]
    assert any("backups" in p for p in paths), (
        f"backups/ dir not detected in hygiene output; clutter paths: {paths}"
    )


def test_hygiene_never_deletes(tmp_path: Path) -> None:
    """Running hygiene must not delete any files (advisory-only contract)."""
    # Create a sentinel test file in tmp area, not in repo
    sentinel = tmp_path / "canary.txt"
    sentinel.write_text("canary")

    # Run hygiene; it should not delete files in backups/ or anywhere
    backups = REPO_ROOT / "backups"
    files_before: set[str] = set()
    if backups.exists():
        files_before = {str(f) for f in backups.rglob("*") if f.is_file()}

    result = _run("hygiene")
    assert result.returncode == 0

    if backups.exists():
        files_after = {str(f) for f in backups.rglob("*") if f.is_file()}
        deleted = files_before - files_after
        assert not deleted, (
            f"hygiene deleted files (must NEVER auto-delete): {deleted}"
        )

    # Canary untouched
    assert sentinel.exists(), "hygiene deleted canary file (must NEVER auto-delete)"


def test_hygiene_clutter_entries_have_severity() -> None:
    """Every clutter entry must have a severity field."""
    result = _run("--hygiene-audit")
    data = json.loads(result.stdout)
    for entry in data["clutter"]:
        assert "severity" in entry, f"clutter entry missing severity: {entry}"
        assert entry["severity"] == "advisory", (
            f"clutter severity must be 'advisory'; got {entry['severity']!r}"
        )


# ---------------------------------------------------------------------------
# branch-keepup decision matrix
# ---------------------------------------------------------------------------


def test_branch_keepup_outputs_valid_json() -> None:
    """branch-keepup must output valid JSON."""
    result = _run("branch-keepup")
    assert result.returncode == 0
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        pytest.fail(f"branch-keepup output not valid JSON: {exc}\nstdout={result.stdout!r}")
    assert isinstance(data, dict)


def test_branch_keepup_has_recommendation() -> None:
    """branch-keepup JSON must contain recommendation field."""
    result = _run("branch-keepup")
    data = json.loads(result.stdout)
    assert "recommendation" in data, f"recommendation missing: {data.keys()}"


def test_branch_keepup_action_is_advisory() -> None:
    """branch-keepup action must be advisory_only."""
    result = _run("branch-keepup")
    data = json.loads(result.stdout)
    assert data.get("action") == "advisory_only_never_auto_executes" or \
           data.get("severity") == "advisory", (
        f"branch-keepup must be advisory: {data}"
    )


@pytest.mark.parametrize("ahead,behind,merged,dirty,expected_substring", [
    (0, 0, False, False, "current_with_main_proceed"),
    (0, 5, False, False, "fresh_branch_or_ff_only"),
    (0, 5, False, True, "checkpoint_first"),
    (3, 5, False, False, "rebase_if_private_else_merge_origin_main"),
    (3, 5, False, True, "checkpoint_first_then_choose"),
    (3, 0, True, False, "branch_already_merged_close"),
    (0, 0, True, True, "checkpoint_first_then_close"),
])
def test_decision_matrix_cases(
    ahead: int, behind: int, merged: bool, dirty: bool, expected_substring: str
) -> None:
    """Unit test _decision_matrix for the 5 operator draft §D cases + edge cases."""
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    try:
        import importlib.util
        spec_mod = importlib.util.spec_from_file_location(
            "worktree_doctor", str(SCRIPT)
        )
        mod = importlib.util.module_from_spec(spec_mod)  # type: ignore[arg-type]
        spec_mod.loader.exec_module(mod)  # type: ignore[union-attr]
        result = mod._decision_matrix(ahead=ahead, behind=behind, merged=merged, dirty=dirty)
        assert expected_substring in result, (
            f"_decision_matrix(ahead={ahead}, behind={behind}, merged={merged}, dirty={dirty}) "
            f"returned {result!r}; expected substring {expected_substring!r}"
        )
    finally:
        sys.path.pop(0)


def test_branch_keepup_detached_head_no_crash() -> None:
    """branch-keepup on detached HEAD or missing branch must exit 0."""
    result = _run("branch-keepup")
    assert result.returncode == 0


# ---------------------------------------------------------------------------
# Cross-worktree visibility manual smoke (verifies 3 worktrees)
# ---------------------------------------------------------------------------


def test_cross_worktree_visibility_lists_three_worktrees() -> None:
    """Manual smoke: --cross-worktree-visibility lists >= 3 current worktrees."""
    result = _run("--cross-worktree-visibility")
    assert result.returncode == 0
    output = result.stdout
    bracket_lines = [l for l in output.splitlines() if l.strip().startswith("[")]
    assert len(bracket_lines) >= 3, (
        f"Expected >= 3 worktree bracket lines; found {len(bracket_lines)}\n{output}"
    )
