#!/usr/bin/env python3
# Created: 2026-09-24
# Last reused or audited: 2026-09-24
# Authority basis: AGENTS.md §5 (one live truth, task-scoped worktrees); operator
#   directive 2026-09-24: abandoned worktrees, branches, PRs and tmux sessions
#   must converge without relying on the agent that left them.
"""Converge the Zeus workspace to: the live checkout plus the worktrees of running tasks.

Agents clean up their own tasks (AGENTS.md §5). This job is the backstop for
whatever they abandon, and nothing it removes is lost:

  worktree idle >= 6h, no process inside   commit leftovers to its branch, remove
  local branch landed on origin/live       delete
  local branch unlanded, idle >= 7d        bundle, delete
  open PR idle >= 7d                       close with a comment
  remote branch landed                     delete
  remote branch unlanded, idle >= 7d       bundle, delete
  omc-* tmux session detached, screen frozen 24h or no human input 72h   kill
  untracked file in live, idle >= 24h      move to the archive
  /private/tmp/zeus*, idle >= 48h          delete

Branches that are checked out or head an open PR are never touched, and a
failed GitHub query skips every branch and PR decision. Bundles and moved files
land in ~/.zeus-wt-archive/converge/. Dry-run by default; --apply acts.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

HOUR = 3600
WORKTREE_IDLE = 6 * HOUR
BRANCH_IDLE = 7 * 24 * HOUR
PR_IDLE = 7 * 24 * HOUR
TMUX_IDLE = 24 * HOUR
TMUX_UNATTENDED = 72 * HOUR
UNTRACKED_IDLE = 24 * HOUR
TMP_IDLE = 48 * HOUR
LARGE_FILE = 20 * 1024 * 1024
SKIP_DIRS = frozenset(
    {".git", "__pycache__", ".pytest_cache", ".ruff_cache", ".mypy_cache", "node_modules", ".venv"}
)
LANDED = "refs/remotes/origin/live"
PR_CLOSE_NOTE = (
    "Closed by the workspace converger: no activity for 7 days (AGENTS.md §5). "
    "The branch is preserved in the local archive bundle; reopen or push again to continue."
)


def run(args: list[str], cwd: Path | None = None, check: bool = True) -> subprocess.CompletedProcess:
    try:
        proc = subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=300)
    except FileNotFoundError:
        if check:
            raise
        return subprocess.CompletedProcess(args, 127, "", f"{args[0]}: not found")
    if check and proc.returncode != 0:
        raise RuntimeError(f"{' '.join(args)} -> {proc.returncode}: {proc.stderr.strip()}")
    return proc


def newest_mtime(root: Path) -> float:
    """Latest mtime of root or anything under it, ignoring caches and .git."""
    try:
        newest = root.lstat().st_mtime
    except OSError:
        return 0.0
    if root.is_symlink() or not root.is_dir():
        return newest
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for name in dirnames + filenames:
            try:
                newest = max(newest, os.lstat(os.path.join(dirpath, name)).st_mtime)
            except OSError:
                pass
    return newest


def process_cwds() -> list[str] | None:
    proc = run(["lsof", "-a", "-d", "cwd", "-Fn"], check=False)
    if proc.returncode not in (0, 1):
        return None
    return [line[1:] for line in proc.stdout.splitlines() if line.startswith("n")]


def inside(path: str, roots: list[str]) -> bool:
    return any(r == path or r.startswith(path.rstrip("/") + "/") for r in roots)


class Converger:
    def __init__(
        self,
        repo: Path,
        archive: Path,
        now: float,
        apply: bool,
        open_prs: list[dict] | None = None,
        tmp_root: Path | None = Path("/private/tmp"),
        use_tmux: bool = True,
    ) -> None:
        self.repo = repo
        self.archive = archive
        self.now = now
        self.apply = apply
        self.open_prs = open_prs
        self.tmp_root = tmp_root
        self.use_tmux = use_tmux
        self.stamp = datetime.fromtimestamp(now, timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        self.actions: list[dict] = []

    def git(self, *args: str, cwd: Path | None = None, check: bool = True) -> str:
        return run(["git", *args], cwd=cwd or self.repo, check=check).stdout

    def act(self, kind: str, target: str, why: str, fn) -> bool:
        entry = {"kind": kind, "target": target, "why": why, "applied": False}
        if self.apply:
            try:
                fn()
                entry["applied"] = True
            except Exception as exc:  # one failure must not stop the sweep
                entry["error"] = str(exc)
        self.actions.append(entry)
        return entry["applied"]

    def is_landed(self, rev: str) -> bool:
        return run(["git", "merge-base", "--is-ancestor", rev, LANDED], cwd=self.repo, check=False).returncode == 0

    # -- worktrees -------------------------------------------------------

    def worktrees(self) -> list[dict]:
        items, cur = [], {}
        for line in self.git("worktree", "list", "--porcelain").splitlines() + [""]:
            if not line:
                if cur:
                    items.append(cur)
                cur = {}
                continue
            key, _, value = line.partition(" ")
            cur[key] = value or True
        return items

    def converge_worktrees(self, cwds: list[str] | None) -> set[str]:
        """Remove idle linked worktrees; return branches still checked out."""
        items = self.worktrees()
        checked_out = {w["branch"][len("refs/heads/"):] for w in items if "branch" in w}
        if cwds is None:
            return checked_out  # cannot prove idleness
        for wt in items[1:]:
            path = Path(wt["worktree"])
            if not path.exists():
                self.act("prune-worktree", str(path), "path missing", lambda: self.git("worktree", "prune"))
                continue
            if inside(str(path), cwds):
                continue
            gitdir = Path(self.git("rev-parse", "--absolute-git-dir", cwd=path).strip())
            reflog = gitdir / "logs" / "HEAD"
            activity = max(newest_mtime(path), reflog.stat().st_mtime if reflog.exists() else 0.0)
            idle = self.now - activity
            if idle < WORKTREE_IDLE:
                continue
            branch = wt["branch"][len("refs/heads/"):] if "branch" in wt else ""
            if self.act(
                "remove-worktree",
                str(path),
                f"idle {idle / HOUR:.0f}h; leftovers committed to {branch or 'a converge/ branch'}",
                lambda p=path, b=branch, w=wt: self._retire_worktree(p, b, w),
            ):
                checked_out.discard(branch)
        return checked_out

    def _retire_worktree(self, path: Path, branch: str, wt: dict) -> None:
        dirty = self.git("status", "--porcelain", cwd=path).strip()
        if not branch and (dirty or not self.is_landed(wt["HEAD"])):
            branch = f"converge/{path.name}-{wt['HEAD'][:9]}"
            self.git("checkout", "-q", "-b", branch, cwd=path)
        if dirty:
            self._evict_large_untracked(path)
            self.git("add", "-A", cwd=path)
            self.git(
                "commit", "-q", "--no-verify", "-m",
                f"wip(converge): preserve work abandoned in {path.name}",
                cwd=path,
            )
        if "locked" in wt:
            self.git("worktree", "unlock", str(path))
        self.git("worktree", "remove", "--force", str(path))

    def _evict_large_untracked(self, path: Path) -> None:
        listing = self.git("ls-files", "--others", "--exclude-standard", "-z", cwd=path)
        for rel in filter(None, listing.split("\0")):
            src = path / rel
            if src.is_file() and src.stat().st_size > LARGE_FILE:
                dst = self.archive / self.stamp / "large-untracked" / path.name / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(src), str(dst))

    # -- branches and PRs ------------------------------------------------

    def converge_prs(self) -> set[str] | None:
        """Close stale PRs; return head branches of PRs that stay open, or None if unknown."""
        prs = self.open_prs
        if prs is None:
            proc = run(
                ["gh", "pr", "list", "--state", "open", "--limit", "200",
                 "--json", "number,headRefName,updatedAt"],
                cwd=self.repo, check=False,
            )
            if proc.returncode != 0:
                return None
            prs = json.loads(proc.stdout or "[]")
        heads = set()
        for pr in prs:
            updated = datetime.fromisoformat(pr["updatedAt"].replace("Z", "+00:00")).timestamp()
            idle = self.now - updated
            if idle < PR_IDLE:
                heads.add(pr["headRefName"])
                continue
            closed = self.act(
                "close-pr", f"#{pr['number']} {pr['headRefName']}", f"idle {idle / 86400:.0f}d",
                lambda n=pr["number"]: run(
                    ["gh", "pr", "close", str(n), "--comment", PR_CLOSE_NOTE], cwd=self.repo
                ),
            )
            if not closed:
                heads.add(pr["headRefName"])
        return heads

    def _ref_idle(self, ref: str, committed: float) -> float:
        log = self.repo / ".git" / "logs" / ref
        touched = log.stat().st_mtime if log.exists() else 0.0
        return self.now - max(committed, touched)

    def _bundle(self, refs: list[str]) -> Path:
        out = self.archive / f"{self.stamp}-branches.bundle"
        out.parent.mkdir(parents=True, exist_ok=True)
        self.git("bundle", "create", str(out), *refs, "--not", LANDED)
        self.git("bundle", "verify", str(out))
        return out

    def converge_local_branches(self, keep: set[str]) -> None:
        stale: list[tuple[str, str]] = []
        fmt = "%(refname)%09%(objectname)%09%(committerdate:unix)"
        for line in self.git("for-each-ref", "refs/heads", f"--format={fmt}").splitlines():
            ref, sha, ts = line.split("\t")
            name = ref[len("refs/heads/"):]
            if name == "live" or name in keep:
                continue
            if self.is_landed(sha):
                self.act("delete-branch", name, "landed on origin/live",
                         lambda r=ref, s=sha: self.git("update-ref", "-d", r, s))
            elif self._ref_idle(ref, float(ts)) >= BRANCH_IDLE:
                stale.append((ref, sha))
        if stale:
            def archive_and_delete() -> None:
                self._bundle([r for r, _ in stale])
                for r, s in stale:
                    self.git("update-ref", "-d", r, s)
            self.act("bundle-delete-branches", ",".join(r[len("refs/heads/"):] for r, _ in stale),
                     "unlanded, idle >= 7d", archive_and_delete)

    def converge_remote_branches(self, keep: set[str]) -> None:
        landed, stale = [], []
        fmt = "%(refname)%09%(objectname)%09%(committerdate:unix)"
        for line in self.git("for-each-ref", "refs/remotes/origin", f"--format={fmt}").splitlines():
            ref, sha, ts = line.split("\t")
            name = ref[len("refs/remotes/origin/"):]
            if name in ("HEAD", "live") or name in keep:
                continue
            if self.is_landed(sha):
                landed.append(name)
            elif self.now - float(ts) >= BRANCH_IDLE:
                stale.append(name)
        if landed:
            self.act("delete-remote-branches", ",".join(landed), "landed on origin/live",
                     lambda: self.git("push", "-q", "origin", "--delete", *landed))
        if stale:
            def archive_and_delete() -> None:
                self._bundle([f"refs/remotes/origin/{n}" for n in stale])
                self.git("push", "-q", "origin", "--delete", *stale)
            self.act("bundle-delete-remote-branches", ",".join(stale), "unlanded, idle >= 7d",
                     archive_and_delete)

    # -- sessions and stray files ---------------------------------------

    def converge_tmux(self) -> None:
        """Kill detached omc-* sessions that are frozen or that nobody drives any more.

        A session is kept while a human still types into it (tmux's own
        activity clock counts only input) and its screen still moves. It is
        killed once its screen has not changed for TMUX_IDLE, or once no human
        input has reached it for TMUX_UNATTENDED even if an agent inside keeps
        printing: an unattended detached agent is abandoned work.
        """
        proc = run(["tmux", "list-sessions", "-F",
                    "#{session_name}\t#{session_attached}\t#{session_activity}"], check=False)
        if proc.returncode != 0:
            return
        seen_path = self.archive / "tmux-screens.json"
        try:
            seen = json.loads(seen_path.read_text())
        except (OSError, ValueError):
            seen = {}
        current = {}
        for line in proc.stdout.splitlines():
            name, attached, activity = line.split("\t")
            if not name.startswith("omc-") or attached != "0":
                continue
            screen = run(["tmux", "capture-pane", "-p", "-t", name], check=False).stdout
            digest = hashlib.sha256(screen.encode()).hexdigest()
            prior = seen.get(name, {})
            since = prior.get("since", self.now) if prior.get("hash") == digest else self.now
            current[name] = {"hash": digest, "since": since}
            frozen = self.now - since
            unattended = self.now - float(activity)
            if frozen >= TMUX_IDLE:
                why = f"detached, screen unchanged {frozen / 3600:.0f}h"
            elif unattended >= TMUX_UNATTENDED:
                why = f"detached, no human input for {unattended / 86400:.0f}d"
            else:
                continue
            self.act("kill-tmux", name, why, lambda n=name: run(["tmux", "kill-session", "-t", n]))
        if self.apply:
            seen_path.parent.mkdir(parents=True, exist_ok=True)
            seen_path.write_text(json.dumps(current))

    def converge_live_untracked(self) -> None:
        listing = self.git("ls-files", "--others", "--exclude-standard", "-z")
        for rel in filter(None, listing.split("\0")):
            src = self.repo / rel
            idle = self.now - newest_mtime(src)
            if idle < UNTRACKED_IDLE:
                continue
            dst = self.archive / self.stamp / "live-untracked" / rel
            def move(s=src, d=dst) -> None:
                d.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(s), str(d))
            self.act("archive-live-untracked", rel, f"idle {idle / HOUR:.0f}h", move)

    def converge_tmp(self, cwds: list[str] | None) -> None:
        if self.tmp_root is None or cwds is None:
            return
        registered = {w["worktree"] for w in self.worktrees()}
        for entry in sorted(self.tmp_root.glob("zeus*")):
            path = str(entry)
            if path in registered or inside(path, cwds):
                continue
            idle = self.now - newest_mtime(entry)
            if idle < TMP_IDLE:
                continue
            self.act("delete-tmp", path, f"idle {idle / HOUR:.0f}h",
                     lambda e=entry: shutil.rmtree(e) if e.is_dir() and not e.is_symlink() else e.unlink())

    # -- run -------------------------------------------------------------

    def converge(self) -> list[dict]:
        fetched = run(["git", "fetch", "-q", "--prune", "origin"], cwd=self.repo, check=False).returncode == 0
        cwds = process_cwds()
        open_heads = self.converge_prs() if fetched else None
        checked_out = self.converge_worktrees(cwds)
        if open_heads is not None:
            keep = checked_out | open_heads
            self.converge_local_branches(keep)
            self.converge_remote_branches(keep)
        if self.use_tmux:
            self.converge_tmux()
        self.converge_live_untracked()
        self.converge_tmp(cwds)
        return self.actions


def main_repo() -> Path:
    common = run(["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
                 cwd=Path(__file__).resolve().parent).stdout.strip()
    return Path(os.environ.get("ZEUS_PRIMARY_ROOT") or Path(common).parent)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="act; default is a dry run")
    args = parser.parse_args()

    archive = Path.home() / ".zeus-wt-archive" / "converge"
    archive.mkdir(parents=True, exist_ok=True)
    with open(archive / ".lock", "w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 0
        actions = Converger(main_repo(), archive, time.time(), args.apply).converge()

    log = archive / "converge.log"
    if log.exists() and log.stat().st_size > 5 * 1024 * 1024:
        log.replace(log.with_suffix(".log.1"))
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with open(log, "a") as fh:
        for entry in actions:
            fh.write(json.dumps({"at": stamp, **entry}) + "\n")
    for entry in actions:
        state = "done" if entry["applied"] else ("ERROR " + entry["error"] if "error" in entry else "would")
        print(f"{state:>6}  {entry['kind']:<30} {entry['target']}  ({entry['why']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
