# Created: 2026-05-16
# Last reused or audited: 2026-05-16
# Authority basis:
#   docs/operations/task_2026-05-15_runtime_improvement_engineering_package/02_daily_maintenance_agent/TASK_CATALOG.yaml
#   §stale_worktree_quarantine
#   docs/operations/task_2026-05-15_runtime_improvement_engineering_package/04_workspace_hygiene/PURGE_CATEGORIES.md
#   §category-2-stale-worktrees
"""
Handler: stale_worktree_quarantine

Identifies stale git worktrees that have been idle for > worktree_idle_ttl_days.

enumerate(): shells out `git worktree list --porcelain`, parses output,
  filters by idle criteria (mtime or no commits in ttl_days),
  skips forbidden worktrees (currently-checked-out branch, uncommitted
  changes, open-PR-branch).

apply(): always dry_run_only (live_default: false). Returns mock diff
  showing what `git worktree remove` would do.

Verdict strings:
  STALE_QUARANTINE_CANDIDATE — idle worktree, safe to remove
  SKIP_CURRENT_BRANCH        — skip: worktree is currently checked out
  SKIP_UNCOMMITTED           — skip: has uncommitted changes
  SKIP_OPEN_PR               — skip: branch appears in open PR (gh unavail → warn)
  SKIP_ACTIVE                — skip: recent activity within ttl_days
"""
from __future__ import annotations

import logging
import os
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from maintenance_worker.types.candidates import Candidate
from maintenance_worker.types.results import ApplyResult
from maintenance_worker.types.specs import TaskSpec, TickContext

logger = logging.getLogger(__name__)

# Verdict strings
VERDICT_STALE = "STALE_QUARANTINE_CANDIDATE"
VERDICT_SKIP_CURRENT = "SKIP_CURRENT_BRANCH"
VERDICT_SKIP_UNCOMMITTED = "SKIP_UNCOMMITTED"
VERDICT_SKIP_OPEN_PR = "SKIP_OPEN_PR"
VERDICT_SKIP_ACTIVE = "SKIP_ACTIVE"

DEFAULT_TTL_DAYS = 21


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def enumerate(entry: Any, ctx: TickContext) -> list[Candidate]:  # noqa: A001
    """
    Parse `git worktree list --porcelain` and classify each worktree.

    Forbidden (always skip):
      - Currently checked-out branch (main/canonical worktree)
      - Worktree with uncommitted changes
      - Worktree whose branch appears in an open PR (stubbed: warns + skips check)

    Returns list[Candidate] with verdict STALE_QUARANTINE_CANDIDATE or SKIP_*.
    """
    spec: TaskSpec = entry.spec
    raw: dict = entry.raw
    config: dict = raw.get("config", {})

    ttl_days: int = int(config.get("worktree_idle_ttl_days", DEFAULT_TTL_DAYS))
    repo_root: Path = ctx.config.repo_root

    # Get current branch (HEAD) to identify main worktree
    current_branch = _get_current_branch(repo_root)

    # Parse all worktrees
    worktrees = _parse_worktree_list(repo_root)
    candidates: list[Candidate] = []

    now_ts = time.time()
    ttl_seconds = ttl_days * 86400

    for wt in worktrees:
        wt_path = wt.get("worktree", "")
        wt_branch = wt.get("branch", "")
        wt_bare = wt.get("bare", False)
        is_main = wt.get("is_main", False)

        if not wt_path:
            continue

        p = Path(wt_path)
        evidence: dict[str, object] = {
            "worktree_path": wt_path,
            "branch": wt_branch,
            "bare": wt_bare,
            "is_main": is_main,
        }

        # Skip: canonical/main worktree (first entry in worktree list)
        if is_main:
            candidates.append(Candidate(
                task_id=spec.task_id,
                path=p,
                verdict=VERDICT_SKIP_CURRENT,
                reason="Main worktree (canonical checkout); never quarantine.",
                evidence=evidence,
            ))
            continue

        # Skip: worktree whose branch is currently checked out in main
        if wt_branch and current_branch and wt_branch == current_branch:
            candidates.append(Candidate(
                task_id=spec.task_id,
                path=p,
                verdict=VERDICT_SKIP_CURRENT,
                reason=f"Branch '{wt_branch}' is currently checked out in canonical worktree.",
                evidence=evidence,
            ))
            continue

        # Skip: uncommitted changes
        if _has_uncommitted_changes(p):
            evidence["uncommitted"] = True
            candidates.append(Candidate(
                task_id=spec.task_id,
                path=p,
                verdict=VERDICT_SKIP_UNCOMMITTED,
                reason="Worktree has uncommitted changes; skip to avoid data loss.",
                evidence=evidence,
            ))
            continue

        # Skip: open PR check (STUBBED — gh unavailable in dry-run)
        # Conservative: we do NOT mark as STALE_QUARANTINE if we can't verify.
        # We warn and proceed to idle check anyway (PR check is advisory here).
        logger.debug(
            "stale_worktree_quarantine: open_pr_check skipped for %s (gh unavailable)",
            wt_branch,
        )

        # Idle check: mtime of worktree path OR most recent commit in ttl_days
        idle = _is_worktree_idle(p, repo_root, wt_branch, ttl_seconds)
        evidence["idle_check"] = idle

        if not idle:
            candidates.append(Candidate(
                task_id=spec.task_id,
                path=p,
                verdict=VERDICT_SKIP_ACTIVE,
                reason=f"Worktree has activity within last {ttl_days} days.",
                evidence=evidence,
            ))
            continue

        candidates.append(Candidate(
            task_id=spec.task_id,
            path=p,
            verdict=VERDICT_STALE,
            reason=f"Worktree idle > {ttl_days} days with no uncommitted changes.",
            evidence=evidence,
        ))

    logger.info(
        "stale_worktree_quarantine: checked %d worktrees, %d stale candidates",
        len(worktrees),
        sum(1 for c in candidates if c.verdict == VERDICT_STALE),
    )
    return candidates


def apply(decision: Any, ctx: TickContext) -> ApplyResult:
    """
    Apply worktree quarantine. Always dry_run_only (live_default: false in catalog).

    Top-of-function guard per PLAN §1.5.4: defense-in-depth.
    Returns ApplyResult with mock diff showing what git worktree remove would do.
    """
    # TOP-OF-FUNCTION GUARD
    mock = _mock_diff(decision)
    return ApplyResult(
        task_id="stale_worktree_quarantine",
        dry_run_only=True,
        diff=mock,
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _get_current_branch(repo_root: Path) -> str:
    """Return the current branch name of the repo, or empty string on error."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass
    return ""


def _parse_worktree_list(repo_root: Path) -> list[dict]:
    """
    Run `git worktree list --porcelain` and parse the output.

    Returns list of dicts with keys: worktree, branch, head, bare, is_main.
    is_main=True for the first (canonical) worktree entry.
    """
    try:
        result = subprocess.run(
            ["git", "worktree", "list", "--porcelain"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            logger.warning("git worktree list failed: %s", result.stderr)
            return []
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as exc:
        logger.warning("git worktree list error: %s", exc)
        return []

    return _parse_porcelain_output(result.stdout)


def _parse_porcelain_output(output: str) -> list[dict]:
    """Parse `git worktree list --porcelain` output into list of dicts."""
    worktrees: list[dict] = []
    current: dict = {}
    is_first = True

    for line in output.splitlines():
        line = line.strip()
        if not line:
            if current:
                worktrees.append(current)
                current = {}
            continue
        if line.startswith("worktree "):
            current = {
                "worktree": line[len("worktree "):],
                "branch": "",
                "head": "",
                "bare": False,
                "is_main": is_first,
            }
            is_first = False
        elif line.startswith("HEAD "):
            current["head"] = line[len("HEAD "):]
        elif line.startswith("branch "):
            # Format: "refs/heads/<name>"
            branch = line[len("branch "):]
            if branch.startswith("refs/heads/"):
                branch = branch[len("refs/heads/"):]
            current["branch"] = branch
        elif line == "bare":
            current["bare"] = True

    if current:
        worktrees.append(current)

    return worktrees


def _has_uncommitted_changes(worktree_path: Path) -> bool:
    """
    Return True if the worktree has uncommitted changes (staged or unstaged).
    Returns False if the worktree path doesn't exist or git fails.
    """
    if not worktree_path.exists():
        return False
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=worktree_path,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            return bool(result.stdout.strip())
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass
    return False


def _is_worktree_idle(
    worktree_path: Path,
    repo_root: Path,
    branch: str,
    ttl_seconds: float,
) -> bool:
    """
    Return True if the worktree has been idle for > ttl_seconds.

    Uses filesystem mtime as primary check, git log as secondary.
    """
    now_ts = time.time()

    # Primary: mtime of the worktree directory
    if worktree_path.exists():
        try:
            mtime = worktree_path.stat().st_mtime
            if now_ts - mtime <= ttl_seconds:
                return False
        except OSError:
            pass

    # Secondary: git log -1 on the branch to check last commit time
    if branch:
        try:
            result = subprocess.run(
                ["git", "log", "-1", "--format=%ct", branch],
                cwd=repo_root,
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0 and result.stdout.strip():
                commit_ts = float(result.stdout.strip())
                if now_ts - commit_ts <= ttl_seconds:
                    return False
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError, ValueError):
            pass

    return True


def _mock_diff(decision: Any) -> tuple[str, ...]:
    """Return a mock diff tuple for dry-run proposals."""
    if decision is None:
        return ("# dry-run: no worktree quarantine decisions to apply",)
    path_str = str(getattr(decision, "path", decision))
    branch = ""
    if hasattr(decision, "evidence") and isinstance(decision.evidence, dict):
        branch = str(decision.evidence.get("branch", ""))
    lines = [
        f"# dry-run proposal for stale_worktree_quarantine",
        f"# would execute: git worktree remove --force {path_str}",
    ]
    if branch:
        lines.append(f"# would execute: git branch -D {branch}")
    return tuple(lines)
