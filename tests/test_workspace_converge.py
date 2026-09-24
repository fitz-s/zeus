# Created: 2026-09-24
# Last reused or audited: 2026-09-24
# Authority basis: AGENTS.md §5; scripts/workspace_converge.py
"""Workspace converger: abandoned work converges, and nothing it removes is lost."""

from __future__ import annotations

import importlib.util
import os
import subprocess
import time
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "workspace_converge.py"
spec = importlib.util.spec_from_file_location("workspace_converge", SCRIPT)
wc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wc)

DAY = 86400


def git(cwd: Path, *args: str) -> str:
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
    return subprocess.run(["git", *args], cwd=cwd, env=env, check=True,
                          capture_output=True, text=True).stdout.strip()


def age(path: Path, seconds: float) -> None:
    """Backdate path, everything under it, and its worktree reflog if it has one."""
    t = time.time() - seconds
    paths = [path, *path.rglob("*")]
    if (path / ".git").is_file():
        gitdir = Path((path / ".git").read_text().split("gitdir:", 1)[1].strip())
        paths.append(gitdir / "logs" / "HEAD")
    for p in paths:
        if p.exists():
            os.utime(p, (t, t), follow_symlinks=False)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    origin = tmp_path / "origin.git"
    git(tmp_path, "init", "-q", "--bare", "-b", "live", str(origin))
    main = tmp_path / "main"
    git(tmp_path, "clone", "-q", str(origin), str(main))
    (main / "a.txt").write_text("a\n")
    git(main, "add", "a.txt")
    git(main, "commit", "-q", "-m", "base")
    git(main, "push", "-q", "origin", "live")
    return main


def converger(repo: Path, tmp_path: Path, prs=None, now=None) -> wc.Converger:
    return wc.Converger(repo, tmp_path / "archive", now or time.time(), apply=True,
                        open_prs=prs or [], tmp_root=None, use_tmux=False)


def commit_on(repo: Path, branch: str, name: str) -> str:
    git(repo, "branch", branch, "origin/live")
    wt = repo.parent / f"wt-{branch.replace('/', '-')}"
    git(repo, "worktree", "add", "-q", str(wt), branch)
    (wt / name).write_text(name)
    git(wt, "add", name)
    git(wt, "commit", "-q", "-m", name)
    sha = git(wt, "rev-parse", "HEAD")
    git(repo, "worktree", "remove", str(wt))
    return sha


def test_idle_dirty_worktree_is_committed_to_its_branch_then_removed(repo, tmp_path):
    wt = tmp_path / "wt"
    git(repo, "worktree", "add", "-q", "-b", "task/x", str(wt), "origin/live")
    (wt / "work.txt").write_text("unfinished")
    age(wt, 7 * 3600)

    converger(repo, tmp_path).converge()

    assert not wt.exists()
    assert git(repo, "show", "task/x:work.txt") == "unfinished"


def test_recently_touched_worktree_is_kept(repo, tmp_path):
    wt = tmp_path / "wt"
    git(repo, "worktree", "add", "-q", "-b", "task/x", str(wt), "origin/live")
    (wt / "work.txt").write_text("in progress")

    converger(repo, tmp_path).converge()

    assert wt.exists()


def test_landed_branch_is_deleted_and_idle_unlanded_branch_is_bundled_first(repo, tmp_path):
    landed = commit_on(repo, "task/landed", "l.txt")
    git(repo, "push", "-q", "origin", f"{landed}:refs/heads/live")
    unlanded = commit_on(repo, "task/idea", "i.txt")

    converger(repo, tmp_path, now=time.time() + 8 * DAY).converge()

    heads = git(repo, "branch", "--format=%(refname:short)").split()
    assert "task/landed" not in heads and "task/idea" not in heads
    bundles = list((tmp_path / "archive").glob("*.bundle"))
    assert len(bundles) == 1
    assert unlanded in git(repo, "bundle", "list-heads", str(bundles[0]))


def test_branch_behind_an_open_pr_survives(repo, tmp_path):
    commit_on(repo, "task/pr", "p.txt")
    fresh_pr = [{"number": 1, "headRefName": "task/pr",
                 "updatedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}]

    converger(repo, tmp_path, prs=fresh_pr, now=time.time() + 8 * DAY).converge()

    assert "task/pr" in git(repo, "branch", "--format=%(refname:short)").split()


def test_unknown_pr_state_touches_no_branch(repo, tmp_path, monkeypatch):
    commit_on(repo, "task/idea", "i.txt")
    c = converger(repo, tmp_path, now=time.time() + 8 * DAY)
    monkeypatch.setattr(c, "converge_prs", lambda: None)

    c.converge()

    assert "task/idea" in git(repo, "branch", "--format=%(refname:short)").split()


def test_idle_untracked_file_in_live_is_archived_not_deleted(repo, tmp_path):
    stray = repo / "notes.md"
    stray.write_text("keep me")
    age(stray, 2 * DAY)

    converger(repo, tmp_path).converge()

    assert not stray.exists()
    moved = list((tmp_path / "archive").rglob("notes.md"))
    assert [m.read_text() for m in moved] == ["keep me"]


def test_dry_run_changes_nothing(repo, tmp_path):
    wt = tmp_path / "wt"
    git(repo, "worktree", "add", "-q", "-b", "task/x", str(wt), "origin/live")
    age(wt, 7 * 3600)

    actions = wc.Converger(repo, tmp_path / "archive", time.time(), apply=False,
                           open_prs=[], tmp_root=None, use_tmux=False).converge()

    assert wt.exists()
    assert any(a["kind"] == "remove-worktree" and not a["applied"] for a in actions)


def test_tmux_session_is_killed_when_frozen_or_unattended(tmp_path, monkeypatch):
    t0 = time.time()
    # name -> (screen per run, last human input)
    sessions = {
        "omc-driven": (["tick 1", "tick 2"], t0 + 24 * 3600),   # human typed recently
        "omc-frozen": (["done", "done"], t0 + 24 * 3600),       # human typed, screen froze
        "omc-orphan": (["tick 1", "tick 2"], t0 - 4 * DAY),     # agent prints, nobody drives
    }
    run_index = {"i": 0}
    killed: list[str] = []

    def fake_run(args, cwd=None, check=True):
        out = ""
        if args[:2] == ["tmux", "list-sessions"]:
            out = "".join(f"{n}\t0\t{act}\n" for n, (_, act) in sessions.items()) + "omc-attached\t1\t0\n"
        elif args[:2] == ["tmux", "capture-pane"]:
            out = sessions[args[-1]][0][run_index["i"]]
        elif args[:2] == ["tmux", "kill-session"]:
            killed.append(args[-1])
        return subprocess.CompletedProcess(args, 0, out, "")

    monkeypatch.setattr(wc, "run", fake_run)
    for i, hours in enumerate((0, 25)):
        run_index["i"] = i
        wc.Converger(tmp_path, tmp_path / "archive", t0 + hours * 3600, apply=True,
                     open_prs=[], tmp_root=None).converge_tmux()

    assert sorted(set(killed)) == ["omc-frozen", "omc-orphan"]

def test_missing_lsof_means_unknown_idleness_so_no_worktree_is_removed(repo, tmp_path, monkeypatch):
    wt = tmp_path / "wt"
    git(repo, "worktree", "add", "-q", "-b", "task/x", str(wt), "origin/live")
    age(wt, 7 * 3600)
    monkeypatch.setenv("PATH", "/nonexistent")
    assert wc.process_cwds() is None
    monkeypatch.undo()
    monkeypatch.setattr(wc, "process_cwds", lambda: None)

    converger(repo, tmp_path).converge()

    assert wt.exists()
