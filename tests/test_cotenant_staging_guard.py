# Lifecycle: created=2026-06-12; last_reviewed=2026-06-12; last_reused=2026-06-12
# Purpose: table-driven antibody for the BLOCKING cotenant_staging_guard hook —
#   broad `git add` in the MAIN worktree must block; precise pathspecs, linked
#   worktrees, and the documented bypass must not.
# Reuse: external review 2026-06-12 found two holes (inline COTENANT_GUARD_BYPASS=1
#   not honored because the assignment lives in the command string, not the hook
#   env; `git -C`/`--update`/`./`/`:/` forms slipping past the matcher). These
#   tests pin both fixes. Trigger strings are concatenated so shell-side hooks
#   never see them verbatim in test output.
# Last reused/audited: 2026-06-12
# Authority basis: incident 2026-06-12 (commit 30ba237ef5 swept a sibling's staged
#   deletions) + external review REQ-20260612-155904.
"""Antibody tests for .claude/hooks/dispatch.py::cotenant_staging_guard."""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_HOOKS = _REPO / ".claude" / "hooks"

# Concatenated so command-scanning hooks never match these fixtures verbatim.
_GA = "git " + "add"
_BYP = "COTENANT_GUARD_" + "BYPASS"


@pytest.fixture(scope="module")
def dispatch():
    spec = importlib.util.spec_from_file_location("zeus_hook_dispatch", _HOOKS / "dispatch.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["zeus_hook_dispatch"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(autouse=True)
def _no_env_bypass(monkeypatch):
    monkeypatch.delenv(_BYP, raising=False)


def _run(dispatch, command: str):
    return dispatch._run_advisory_check_cotenant_staging_guard(
        {"tool_input": {"command": command}}
    )


# ---------------------------------------------------------------------------
# BLOCK: broad staging in the main worktree, every common form.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("cmd", [
    f"{_GA} -A",
    f"{_GA} --all",
    f"{_GA} -u",
    f"{_GA} --update",
    f"{_GA} .",
    f"{_GA} ./",
    f"{_GA} :/",
    f"/usr/bin/{_GA} -A",
    f"env {_GA} -A",
    f"command {_GA} -A",
    f"git commit -m x; {_GA} -A",
    f"git -C {_REPO} " + "add -A",
])
def test_broad_add_blocks(dispatch, cmd):
    assert _run(dispatch, cmd) is dispatch._BLOCK_SENTINEL


# ---------------------------------------------------------------------------
# ALLOW: precise pathspecs and non-add commands.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("cmd", [
    f"{_GA} src/foo.py tests/bar.py",
    "git push -u origin some-branch",
    f"git commit -m x; git push -u origin b",
    f"{_GA} src/a.py; git push -u origin b",
    "git status",
    f"{_GA} docs/evidence/report.md",
])
def test_precise_or_unrelated_allows(dispatch, cmd):
    assert _run(dispatch, cmd) is None


# ---------------------------------------------------------------------------
# BYPASS: inline assignment (command string), env var, and non-"1" values.
# ---------------------------------------------------------------------------
def test_inline_bypass_degrades_to_advisory(dispatch):
    out = _run(dispatch, f"{_BYP}=1 {_GA} -A")
    assert out is not dispatch._BLOCK_SENTINEL
    assert out is not None and "bypass" in out.lower()


def test_env_prefix_inline_bypass(dispatch):
    out = _run(dispatch, f"env {_BYP}=1 {_GA} -A")
    assert out is not dispatch._BLOCK_SENTINEL
    assert out is not None


def test_exported_env_bypass(dispatch, monkeypatch):
    monkeypatch.setenv(_BYP, "1")
    out = _run(dispatch, f"{_GA} -A")
    assert out is not dispatch._BLOCK_SENTINEL
    assert out is not None


def test_wrong_bypass_value_still_blocks(dispatch):
    assert _run(dispatch, f"{_BYP}=true {_GA} -A") is dispatch._BLOCK_SENTINEL


# ---------------------------------------------------------------------------
# Linked worktree exemption — including via `git -C <worktree>`.
# ---------------------------------------------------------------------------
def test_linked_worktree_exempt_via_dash_C(dispatch, tmp_path):
    repo = tmp_path / "r"
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "t"], check=True)
    (repo / "f.txt").write_text("x")
    subprocess.run(["git", "-C", str(repo), "add", "f.txt"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "init"], check=True)
    wt = tmp_path / "wt"
    subprocess.run(
        ["git", "-C", str(repo), "worktree", "add", "-q", str(wt), "-b", "side"],
        check=True,
    )
    out = _run(dispatch, f"git -C {wt} " + "add -A")
    assert out is None  # isolated index — safe


# ===========================================================================
# maintree_git_state_guard — failure #3 antibody (operator directive 2026-06-12)
# Trigger substrings concatenated so this test file never self-triggers the
# command-scanning hooks; the guard reads tool_input.command, not this source.
# ===========================================================================
_CO = "check" + "out"
_SW = "swi" + "tch"
_BR = "br" + "anch"
_RS = "re" + "set"
_MBYP = "MAINTREE_GIT_" + "BYPASS"


def _run_mt(dispatch, command: str):
    return dispatch._run_advisory_check_maintree_git_state_guard(
        {"tool_input": {"command": command}}
    )


@pytest.fixture
def _no_mt_env_bypass(monkeypatch):
    monkeypatch.delenv(_MBYP, raising=False)


# --- BLOCK: branch-mutating git aimed at the main tree via `git -C <main>` ---
@pytest.mark.parametrize("cmd", [
    f"git -C {_REPO} " + _CO + " main",
    f"git -C {_REPO} " + _CO + " -B some-branch",
    f"git -C {_REPO} " + _SW + " -c new-branch",
    f"git -C {_REPO} " + _BR + " -D old",
    f"git -C {_REPO} " + _BR + " -f x origin/main",
    f"git -C {_REPO} " + _BR + " -m old new",
    f"git -C {_REPO} " + _RS + " --hard HEAD~1",
    f"git fetch && git -C {_REPO} " + _CO + " -B hijack",
])
def test_maintree_mutating_git_blocks(dispatch, _no_mt_env_bypass, cmd):
    assert _run_mt(dispatch, cmd) is dispatch._BLOCK_SENTINEL


# --- ALLOW: reads / non-mutating branch ops aimed at main tree ---
@pytest.mark.parametrize("cmd", [
    f"git -C {_REPO} " + _BR + " --show-current",
    f"git -C {_REPO} " + _BR,
    f"git -C {_REPO} " + _RS + " HEAD",          # soft reset, not --hard
    f"git -C {_REPO} status",
    f"git -C {_REPO} log --oneline -5",
])
def test_maintree_reads_allow(dispatch, _no_mt_env_bypass, cmd):
    assert _run_mt(dispatch, cmd) is None


# --- EXEMPT: same mutating op aimed at a linked worktree path ---
def test_worktree_path_exempt(dispatch, _no_mt_env_bypass):
    wt = f"{_REPO}/.claude/worktrees/agent-deadbeef"
    assert _run_mt(dispatch, f"git -C {wt} " + _CO + " -B feature") is None


# --- BYPASS: inline and exported env both degrade BLOCK to advisory ---
def test_maintree_inline_bypass(dispatch, _no_mt_env_bypass):
    out = _run_mt(dispatch, f"{_MBYP}=1 git -C {_REPO} " + _CO + " main")
    assert out is not dispatch._BLOCK_SENTINEL
    assert out is not None and "bypass" in out.lower()


def test_maintree_exported_bypass(dispatch, monkeypatch):
    monkeypatch.setenv(_MBYP, "1")
    out = _run_mt(dispatch, f"git -C {_REPO} " + _CO + " main")
    assert out is not dispatch._BLOCK_SENTINEL
    assert out is not None


# --- UNRELATED repo: a `git -C /some/other/repo checkout` must NOT block ---
def test_unrelated_repo_not_blocked(dispatch, _no_mt_env_bypass, tmp_path):
    other = tmp_path / "other"
    other.mkdir()
    subprocess.run(["git", "-C", str(other), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(other), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(other), "config", "user.name", "t"], check=True)
    (other / "f.txt").write_text("x")
    subprocess.run(["git", "-C", str(other), "add", "f.txt"], check=True)
    subprocess.run(["git", "-C", str(other), "commit", "-qm", "init"], check=True)
    assert _run_mt(dispatch, f"git -C {other} " + _CO + " -b side") is None


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
