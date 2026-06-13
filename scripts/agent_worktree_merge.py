#!/usr/bin/env python3
# Created: 2026-06-12
# Last reused or audited: 2026-06-12
# Authority basis: operator directive 2026-06-12 (subagent worktree lifecycle
#   redesign) — /tmp/agent_report_worktree_lifecycle.md. Failure #2 fix:
#   no merge-back automation existed; completed agent work sat in
#   .claude/worktrees/agent-*/ and the orchestrator had to hand-cherry-pick.
"""Merge a subagent's worktree branch back into the session branch — safely.

A subagent runs this as its LAST step, from INSIDE its own linked worktree.
The helper fast-forwards the session branch (the branch checked out in the
MAIN tree) to the agent's HEAD, but ONLY when that is a pure, conflict-free
fast-forward and the main tree is clean. Otherwise it prints MERGE_PENDING
with the exact command for the orchestrator and exits non-zero-but-soft.

------------------------------------------------------------------------------
DAEMON-SAFETY REASONING (why ff-only against a clean main tree is safe)
------------------------------------------------------------------------------
Zeus's LIVE daemons run from the MAIN checkout (/Users/leofitz/zeus). Mutating
that checkout's branch/working-files out from under a running process is the
hazard the cotenant guard and the new main-tree git guard exist to prevent.

This helper performs ONLY a fast-forward update:

  * A fast-forward means the session-branch tip is already an ANCESTOR of the
    agent commit. No merge commit is synthesized; no three-way content merge
    occurs; there is ZERO possibility of a conflict. The branch ref simply
    advances and the working tree gains the agent's already-committed files.
    This is byte-for-byte equivalent to the operator running `git commit` in
    the main tree — the exact event daemons already tolerate continuously.

  * All worktrees share ONE object store (`git rev-parse --git-common-dir`
    resolves to the same .git for every worktree). The agent's commits are
    therefore ALREADY present in the shared object DB the moment the agent
    committed in its worktree — no `push` / object transfer is needed. The
    merge-back is purely a ref + working-tree update of the main checkout.

  * Running daemons hold their own open file descriptors / already-imported
    modules. A working-tree file update does not retroactively rewrite an
    open fd or a loaded module; daemons pick up new code only on their next
    restart — identical to any normal commit landing on the branch. The
    helper does NOT signal, restart, or stop any daemon.

  * If the session branch has ADVANCED since the agent branched (not a pure
    ff), the helper refuses and defers to the orchestrator. A real merge
    commit on a live branch is the orchestrator's call, not an agent's.

  * The main tree must be CLEAN on the session branch. `merge --ff-only`
    against a dirty tree could clobber uncommitted operator work; we refuse
    rather than risk it.

This helper is the ONE sanctioned writer to the main tree's git state. It sets
MAINTREE_GIT_BYPASS=1 for its single `git -C <main> merge --ff-only` call so
the main-tree git guard (.claude/hooks/dispatch.py) permits this deliberate,
daemon-aware operation while still blocking ad-hoc agent git on the main tree.

REFUSES to run when invoked FROM the main tree (an agent must be in its own
worktree). Serializes concurrent mergers with a lockfile under .claude/.
------------------------------------------------------------------------------
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

# The live main checkout. Daemons run from here. Hard-coded because the whole
# point is to detect "am I the main tree?" independent of cwd tricks.
MAIN_TREE = Path("/Users/leofitz/zeus").resolve()
LOCK_PATH = MAIN_TREE / ".claude" / "agent_merge.lock"
LOCK_STALE_SECONDS = 600  # a merger that held the lock >10min is presumed dead


def _git(args: list[str], cwd: Path, *, env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
    )


def _toplevel(cwd: Path) -> Path | None:
    r = _git(["rev-parse", "--show-toplevel"], cwd)
    if r.returncode != 0 or not r.stdout.strip():
        return None
    return Path(r.stdout.strip()).resolve()


def _fail(msg: str, code: int = 1) -> int:
    print(f"MERGE_REFUSED: {msg}", file=sys.stderr)
    return code


def _acquire_lock() -> bool:
    """Best-effort exclusive lock to serialize concurrent mergers.

    O_CREAT|O_EXCL is atomic on local POSIX filesystems. A lock older than
    LOCK_STALE_SECONDS is reclaimed (a crashed merger must not deadlock the
    others forever).
    """
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    if LOCK_PATH.exists():
        try:
            age = time.time() - LOCK_PATH.stat().st_mtime
            if age > LOCK_STALE_SECONDS:
                LOCK_PATH.unlink()
        except OSError:
            pass
    try:
        fd = os.open(str(LOCK_PATH), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        os.write(fd, f"{os.getpid()} {time.time()}\n".encode())
        os.close(fd)
        return True
    except FileExistsError:
        return False
    except OSError:
        return False


def _release_lock() -> None:
    try:
        LOCK_PATH.unlink()
    except OSError:
        pass


def main() -> int:
    cwd = Path.cwd()

    # 1) Refuse if invoked from the main tree.
    top = _toplevel(cwd)
    if top is None:
        return _fail("not inside a git worktree.")
    if top == MAIN_TREE:
        return _fail(
            "invoked from the MAIN tree. This helper must be run from INSIDE an "
            "agent worktree (.claude/worktrees/agent-*). The main tree's branch "
            "is owned by the live session; agents never merge into it directly."
        )

    # Confirm we are a linked worktree (shared object store, isolated index).
    common = _git(["rev-parse", "--git-common-dir"], cwd)
    git_dir = _git(["rev-parse", "--git-dir"], cwd)
    if common.returncode != 0 or git_dir.returncode != 0:
        return _fail("could not resolve git dirs.")
    # In a linked worktree, --git-dir contains '/worktrees/'.
    if "/worktrees/" not in git_dir.stdout:
        return _fail(
            "not a linked worktree (git-dir lacks /worktrees/). Run from an "
            "agent worktree created via isolation:\"worktree\"."
        )

    # 2) Refuse on dirty worktree (uncommitted state).
    dirty = _git(["status", "--porcelain"], cwd)
    if dirty.stdout.strip():
        return _fail(
            "agent worktree has uncommitted changes. Commit (or discard) first; "
            "the merge-back operates on committed history only.\n"
            + dirty.stdout.strip()
        )

    agent_head = _git(["rev-parse", "HEAD"], cwd).stdout.strip()
    if not agent_head:
        return _fail("could not resolve agent HEAD.")

    agent_branch = _git(["symbolic-ref", "--short", "-q", "HEAD"], cwd).stdout.strip()

    # 3) Session branch = the branch checked out in the MAIN tree.
    main_branch = _git(["symbolic-ref", "--short", "-q", "HEAD"], MAIN_TREE).stdout.strip()
    if not main_branch:
        return _fail("main tree is in a detached HEAD; cannot identify session branch.")

    session_tip = _git(["rev-parse", main_branch], cwd).stdout.strip()
    if not session_tip:
        return _fail(f"could not resolve session branch '{main_branch}'.")

    # No-op guard: nothing to merge.
    if agent_head == session_tip:
        print(
            f"MERGE_NOOP: agent HEAD already equals session branch "
            f"'{main_branch}' tip ({session_tip[:10]}). Nothing to merge."
        )
        return 0

    # 4) Require a PURE fast-forward: session tip must be an ancestor of agent HEAD.
    anc = _git(["merge-base", "--is-ancestor", session_tip, agent_head], cwd)
    is_ff = anc.returncode == 0

    pending_cmd = (
        f"git -C {MAIN_TREE} merge --no-ff --no-edit {agent_head}   "
        f"# from branch {agent_branch or '(detached)'}"
    )

    if not is_ff:
        # Session branch advanced since the agent branched. Defer to orchestrator.
        print(
            "MERGE_PENDING: session branch '%s' (tip %s) has advanced beyond the "
            "agent's branch point; a non-fast-forward merge is required and is the "
            "orchestrator's decision (not an agent's) on a LIVE branch.\n"
            "  agent branch: %s\n  agent HEAD:   %s\n"
            "Orchestrator command (run when main tree is idle/clean):\n  %s"
            % (
                main_branch,
                session_tip[:10],
                agent_branch or "(detached)",
                agent_head,
                pending_cmd,
            )
        )
        return 3  # soft "deferred" code distinct from hard refusal (1)

    # 5) Main tree must be CLEAN before we touch its working files.
    main_dirty = _git(["status", "--porcelain"], MAIN_TREE)
    if main_dirty.stdout.strip():
        print(
            "MERGE_PENDING: main tree has uncommitted changes on '%s'; refusing to "
            "fast-forward its working tree (could clobber operator work). "
            "Orchestrator should stage/commit/stash, then run:\n  %s"
            % (main_branch, pending_cmd)
        )
        return 3

    # 6) Serialize concurrent mergers.
    if not _acquire_lock():
        print(
            "MERGE_PENDING: another merge-back holds %s. Retry shortly, or the "
            "orchestrator can run:\n  %s" % (LOCK_PATH, pending_cmd)
        )
        return 3

    try:
        # Re-check the tip under the lock (another merger may have just advanced it).
        session_tip2 = _git(["rev-parse", main_branch], cwd).stdout.strip()
        if session_tip2 != session_tip:
            # Re-evaluate ff against the new tip.
            anc2 = _git(["merge-base", "--is-ancestor", session_tip2, agent_head], cwd)
            if anc2.returncode != 0:
                print(
                    "MERGE_PENDING: session branch moved to %s while acquiring the "
                    "lock; no longer a fast-forward. Orchestrator:\n  %s"
                    % (session_tip2[:10], pending_cmd)
                )
                return 3
            if session_tip2 == agent_head:
                print(
                    f"MERGE_NOOP: session branch already at agent HEAD "
                    f"({agent_head[:10]}) after concurrent merge."
                )
                return 0

        # 7) The ONE sanctioned main-tree git mutation. ff-only on a clean tree:
        #    no merge commit, no conflict, daemon-equivalent to a normal commit.
        env = dict(os.environ)
        env["MAINTREE_GIT_BYPASS"] = "1"  # sanctioned, daemon-aware path
        merged = _git(
            ["-C", str(MAIN_TREE), "merge", "--ff-only", agent_head],
            cwd,
            env=env,
        )
        if merged.returncode != 0:
            print(
                "MERGE_PENDING: ff-only merge failed unexpectedly:\n%s\n%s\n"
                "Orchestrator:\n  %s"
                % (merged.stdout.strip(), merged.stderr.strip(), pending_cmd),
                file=sys.stderr,
            )
            return 3

        new_tip = _git(["rev-parse", main_branch], MAIN_TREE).stdout.strip()
        print(
            "MERGE_OK: fast-forwarded session branch '%s' to agent work.\n"
            "  merged sha: %s\n  agent branch: %s"
            % (main_branch, new_tip, agent_branch or "(detached)")
        )
        return 0
    finally:
        _release_lock()


if __name__ == "__main__":
    sys.exit(main())
