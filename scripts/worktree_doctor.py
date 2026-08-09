#!/usr/bin/env python3
# Created: 2026-05-07
# Last reused or audited: 2026-07-28
# Authority basis: Navigation Topology v2 PLAN §2.6-§2.8; sunset 2027-05-07

"""ADVISORY-only worktree lifecycle helper.

Subcommands (positional):
  status                  — JSON summary of all active worktrees + sentinels
                            + ahead/behind vs live + PR state
  advisory                — additionalContext-formatted cross-worktree map for SessionStart
  branch-keepup           — recommend ff/rebase/merge/close for current branch
  hygiene                 — list workspace clutter (NEVER deletes)

Flag aliases (task-brief compatibility):
  --status                — alias for 'status' subcommand
  --hygiene-audit         — alias for 'hygiene' subcommand
  --cross-worktree-visibility — alias for 'advisory' subcommand

Never mutates git state. Never deletes files. Exit code 0 always (advisory tool).
Operator-only destructive ops per feedback_commit_per_phase_or_lose_everything.md.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

try:
    from src.architecture.decorators import capability
except ImportError:
    # Fallback: no-op decorator when run outside the src package context
    def capability(cap_id: str, **kwargs):  # type: ignore[misc]
        def decorator(fn):
            return fn
        return decorator

REPO_ROOT = Path(__file__).resolve().parents[1]
CODEX_MANAGED_WORKTREE_ROOT = (
    Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))).expanduser()
    / "worktrees"
).resolve()

SENTINEL_FILENAME = "zeus_worktree.yaml"
LIVE_BRANCH = "live"
WORKTREE_ROLES = frozenset({"data", "strategy", "execution", "governance", "hotfix"})

# ---------------------------------------------------------------------------
# Git helpers
# ---------------------------------------------------------------------------


def _git(*args: str, cwd: Path = REPO_ROOT) -> str:
    """Run a git command; return stdout or empty string on error."""
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=5,
        )
        return result.stdout
    except (subprocess.TimeoutExpired, OSError):
        return ""


def _git_output_if_succeeded(*args: str, cwd: Path = REPO_ROOT) -> str | None:
    """Return stdout only when one git invocation completed successfully."""
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=5,
        )
        return result.stdout if result.returncode == 0 else None
    except (subprocess.TimeoutExpired, OSError):
        return None


def _gh(*args: str) -> str:
    """Run a gh command; return stdout or empty string on error."""
    try:
        result = subprocess.run(
            ["gh", *args],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=10,
        )
        return result.stdout if result.returncode == 0 else ""
    except (subprocess.TimeoutExpired, OSError):
        return ""


# ---------------------------------------------------------------------------
# Worktree parsing
# ---------------------------------------------------------------------------


def _parse_worktree_list(porcelain: str) -> list[dict[str, Any]]:
    """Parse `git worktree list --porcelain` output into list of dicts."""
    worktrees: list[dict[str, Any]] = []
    current: dict[str, Any] = {}
    for line in porcelain.splitlines():
        if line.startswith("worktree "):
            if current:
                worktrees.append(current)
            current = {
                "path": line[len("worktree "):].strip(),
                "branch": "",
                "head": "",
                "bare": False,
                "locked": False,
                "prunable": False,
            }
        elif line.startswith("HEAD "):
            current["head"] = line[len("HEAD "):].strip()
        elif line.startswith("branch "):
            ref = line[len("branch "):].strip()
            # refs/heads/branch-name -> branch-name
            current["branch"] = ref.replace("refs/heads/", "")
        elif line.strip() == "bare":
            current["bare"] = True
        elif line.startswith("locked"):
            current["locked"] = True
            current["lock_reason"] = line[len("locked"):].strip()
        elif line.startswith("prunable"):
            current["prunable"] = True
            current["prunable_reason"] = line[len("prunable"):].strip()
    if current:
        worktrees.append(current)
    return worktrees


def _is_codex_managed_worktree(path: str) -> bool:
    """Whether ``path`` is owned by Codex's snapshot-aware worktree lifecycle."""
    try:
        return Path(path).resolve().is_relative_to(CODEX_MANAGED_WORKTREE_ROOT)
    except (OSError, ValueError):
        return False


def _read_sentinel(worktree_path: str) -> dict[str, Any] | None:
    """Read zeus_worktree.yaml sentinel from worktree root or .git/worktrees/ sibling."""
    try:
        import yaml as _yaml
    except ImportError:
        return None

    candidates = [
        Path(worktree_path) / SENTINEL_FILENAME,
    ]
    # Also check .git/worktrees/<name>/zeus_worktree.yaml
    git_dir_file = Path(worktree_path) / ".git"
    if git_dir_file.is_file():
        # Linked worktree: .git is a file pointing to .git/worktrees/<name>
        try:
            ref = git_dir_file.read_text().strip()
            if ref.startswith("gitdir: "):
                git_meta = Path(ref[len("gitdir: "):])
                candidates.append(git_meta / SENTINEL_FILENAME)
        except OSError:
            pass

    for candidate in candidates:
        if candidate.exists():
            try:
                data = _yaml.safe_load(candidate.read_text())
                if isinstance(data, dict):
                    return data
            except Exception:
                pass
    return None


# ---------------------------------------------------------------------------
# Ahead/behind + PR state helpers
# ---------------------------------------------------------------------------


def _live_base_ref() -> str:
    """Return the local live checkout ref, with remote only as a fallback."""
    if _git("rev-parse", "--verify", "--quiet", "live^{commit}").strip():
        return LIVE_BRANCH
    if _git("rev-parse", "--verify", "--quiet", "origin/live^{commit}").strip():
        return "origin/live"
    return ""


def _ahead_behind(branch: str) -> tuple[int, int, str]:
    """Return (ahead, behind, baseline) against the verified live ref."""
    baseline = _live_base_ref()
    if not baseline:
        return 0, 0, ""
    try:
        ahead = int(_git("rev-list", "--count", f"{baseline}..{branch}").strip() or "0")
        behind = int(_git("rev-list", "--count", f"{branch}..{baseline}").strip() or "0")
    except (ValueError, TypeError):
        ahead, behind = 0, 0
    return ahead, behind, baseline


def _role_for_branch(branch: str) -> str:
    """Expose a task role from the branch prefix without creating metadata."""
    if not branch:
        return "detached"
    prefix = branch.split("/", 1)[0]
    if prefix in WORKTREE_ROLES or branch == LIVE_BRANCH:
        return prefix
    return f"legacy:{prefix}"


def _pr_state_for_branch(branch: str, pr_list_json: list[dict]) -> dict[str, Any] | None:
    """Find open PR entry for a branch from pre-fetched gh pr list output."""
    for pr in pr_list_json:
        if pr.get("headRefName", "") == branch:
            return {"number": pr.get("number"), "state": pr.get("state"), "title": pr.get("title", "")}
    return None


def _fetch_pr_list() -> list[dict[str, Any]] | None:
    """Fetch open PRs, returning None when the check cannot be verified."""
    raw = _gh("pr", "list", "--json", "number,state,title,headRefName", "--limit", "50")
    if not raw:
        return None
    try:
        data = json.loads(raw)
        return data if isinstance(data, list) else None
    except json.JSONDecodeError:
        return None


def _dirty_state(worktree_path: str) -> bool:
    """Check if a worktree has uncommitted changes."""
    out = _git("status", "--short", "--porcelain", cwd=Path(worktree_path))
    return bool(out.strip())


def _last_commit_ts(branch: str) -> str:
    """Return unix timestamp of last commit on branch, or empty string."""
    return _git("log", "-1", "--format=%ct", branch).strip()


def _branch_is_absorbed_by_live(branch: str) -> bool:
    """Whether every branch patch is already present in ``live``.

    ``git cherry live <branch>`` emits ``+`` only for patches that live does
    not contain. This recognizes both a merged branch and a branch whose
    commits reached live through hot-pick, without treating ancestry as the
    only proof of landing.
    """
    if not branch:
        return False
    if not _git("rev-parse", "--verify", "--quiet", f"{branch}^{{commit}}").strip():
        return False
    if not _git("rev-parse", "--verify", "--quiet", "live^{commit}").strip():
        return False
    cherry_output = _git_output_if_succeeded("cherry", "live", branch)
    if cherry_output is None:
        return False
    return not any(
        line.startswith("+")
        for line in cherry_output.splitlines()
    )


# ---------------------------------------------------------------------------
# Subcommand: status
# ---------------------------------------------------------------------------


@capability("worktree_create", lease=False)
def cmd_worktree_create_advisory(_args: argparse.Namespace) -> int:
    """Advisory: emit worktree creation guidance (never auto-creates).

    The host allocates a managed worktree by default. An exceptional native
    worktree requires explicit operator authorization. This function never
    creates a tree or writes an untracked sentinel into one.
    """
    print(json.dumps({
        "advisory": (
            "worktree_create: assign one task-scoped role (data, strategy, execution, "
            "governance, or declared hot-fix), one writer, and a branch from live. "
            "Use host-managed creation unless the operator explicitly authorizes a native tree; "
            "never create worktree-local sentinel files."
        ),
        "severity": "advisory",
        "action": "advisory_only_operator_creates",
    }, indent=2))
    return 0


def cmd_status(_args: argparse.Namespace) -> int:
    """JSON summary: all worktrees, branches, ahead/behind, dirty, PR state, sentinels."""
    porcelain = _git("worktree", "list", "--porcelain")
    worktrees = _parse_worktree_list(porcelain)
    pr_list = _fetch_pr_list()
    pr_check_verified = pr_list is not None
    verified_prs = pr_list or []

    current_path = str(REPO_ROOT)

    result_wts = []
    for wt in worktrees:
        branch = wt.get("branch", "")
        wt_path = wt.get("path", "")
        is_current = os.path.realpath(wt_path) == os.path.realpath(current_path)

        ahead, behind, baseline = _ahead_behind(branch) if branch else (0, 0, "")
        dirty = _dirty_state(wt_path) if wt_path else False
        sentinel = _read_sentinel(wt_path)
        pr = _pr_state_for_branch(branch, verified_prs)
        absorbed_by_live = _branch_is_absorbed_by_live(branch)

        result_wts.append({
            "path": wt_path,
            "branch": branch,
            "head": wt.get("head", ""),
            "is_current": is_current,
            "role": _role_for_branch(branch),
            "ahead_of_live": ahead,
            "behind_live": behind,
            "baseline_ref": baseline,
            "dirty": dirty,
            "locked": bool(wt.get("locked")),
            "prunable": bool(wt.get("prunable")),
            "last_commit_ts": _last_commit_ts(branch) if branch else "",
            "pr_state": pr,
            "pr_check_verified": pr_check_verified,
            "absorbed_by_live": absorbed_by_live,
            "sentinel": sentinel,
            "severity": "advisory",
        })

    print(json.dumps({"worktrees": result_wts, "action": "advisory_only"}, indent=2))
    return 0


# ---------------------------------------------------------------------------
# Subcommand: advisory (--cross-worktree-visibility)
# ---------------------------------------------------------------------------


@capability("cross_worktree_visibility", lease=False)
def cmd_advisory(_args: argparse.Namespace) -> int:
    """Cross-worktree visibility map for SessionStart additionalContext injection."""
    porcelain = _git("worktree", "list", "--porcelain")
    worktrees = _parse_worktree_list(porcelain)

    lines = [f"[worktree_doctor] Active worktrees: {len(worktrees)}"]
    for wt in worktrees:
        branch = wt.get("branch", "(detached)")
        wt_path = wt.get("path", "")
        sentinel = _read_sentinel(wt_path)
        ts = _last_commit_ts(branch) if branch else ""

        lines.append(f"  [role={_role_for_branch(branch)} branch={branch}] {wt_path}")
        if sentinel:
            lines.append("    legacy_sentinel: present (not required for lifecycle)")
        if ts:
            lines.append(f"    last_commit_ts: {ts}")

    print("\n".join(lines))
    return 0


# ---------------------------------------------------------------------------
# Subcommand: branch-keepup
# ---------------------------------------------------------------------------


def _decision_matrix(*, ahead: int, behind: int, merged: bool, dirty: bool) -> str:
    """Encode the advisory keep-up decision against live."""
    if merged:
        return "branch_absorbed_close" if not dirty else "checkpoint_first_then_close"
    if ahead == 0 and behind > 0:
        return "fast_forward_to_live" if not dirty else "checkpoint_first"
    if ahead > 0 and behind > 0:
        return "rebase_onto_live_or_refresh_pr" if not dirty else "checkpoint_first_then_choose"
    if ahead == 0 and behind == 0:
        return "current_with_live_proceed"
    return "ahead_of_live_continue_or_land"


@capability("worktree_branch_keepup", lease=False)
def cmd_branch_keepup(_args: argparse.Namespace) -> int:
    """Decision matrix recommendation for current branch versus live."""
    current = _git("branch", "--show-current").strip()
    if not current or current == LIVE_BRANCH:
        print(json.dumps({
            "recommendation": "no-action",
            "reason": "on live or detached HEAD",
            "severity": "advisory",
        }, indent=2))
        return 0

    ahead, behind, baseline = _ahead_behind(current)
    merged = _branch_is_absorbed_by_live(current)
    dirty = _dirty_state(str(REPO_ROOT))
    rec = _decision_matrix(ahead=ahead, behind=behind, merged=merged, dirty=dirty)

    print(json.dumps({
        "branch": current,
        "ahead_of_live": ahead,
        "behind_live": behind,
        "baseline_ref": baseline,
        "absorbed_by_live": merged,
        "dirty": dirty,
        "recommendation": rec,
        "severity": "advisory",
        "action": "advisory_only_never_auto_executes",
    }, indent=2))
    return 0


# ---------------------------------------------------------------------------
# Subcommand: hygiene (--hygiene-audit)
# ---------------------------------------------------------------------------


def _collect_clutter() -> list[dict[str, Any]]:
    """Shared clutter-collection logic for hygiene audit and post-merge cleanup."""
    import time
    clutter: list[dict[str, Any]] = []

    # backups/ directory
    backups_dir = REPO_ROOT / "backups"
    if backups_dir.exists() and backups_dir.is_dir():
        size = sum(f.stat().st_size for f in backups_dir.rglob("*") if f.is_file())
        clutter.append({
            "path": "backups/",
            "type": "directory",
            "size_bytes": size,
            "severity": "advisory",
            "advisory": "stale backup directory; review and remove if no longer needed",
        })

    # *.bak files at repo root
    for p in REPO_ROOT.glob("*.bak"):
        clutter.append({
            "path": str(p.relative_to(REPO_ROOT)),
            "type": "file",
            "size_bytes": p.stat().st_size,
            "severity": "advisory",
            "advisory": "stale .bak file; safe to remove if no recovery in progress",
        })

    # Root-level scratch files (known stale patterns)
    for name in ("station_migration_alerts.json",):
        p = REPO_ROOT / name
        if p.exists():
            clutter.append({
                "path": name,
                "type": "file",
                "size_bytes": p.stat().st_size,
                "severity": "advisory",
                "advisory": "root-level scratch/migration file; archive to docs/ or remove",
            })

    # Stale agent-replay logs
    replay_dir = REPO_ROOT / ".omc" / "state"
    if replay_dir.exists():
        for p in replay_dir.glob("agent-replay-*.jsonl"):
            clutter.append({
                "path": str(p.relative_to(REPO_ROOT)),
                "type": "file",
                "size_bytes": p.stat().st_size,
                "severity": "advisory",
                "advisory": "stale agent replay log; safe to delete if no recovery in progress",
            })

    # Completed or stale clean worktrees without an open PR. A hot-pick makes
    # the source branch patch-equivalent to live without making it an ancestor.
    porcelain = _git("worktree", "list", "--porcelain")
    worktrees = _parse_worktree_list(porcelain)
    pr_list = _fetch_pr_list()
    if pr_list is None:
        return clutter
    current_path = os.path.realpath(REPO_ROOT)
    attached_branches = {wt.get("branch", "") for wt in worktrees}
    for wt in worktrees:
        if os.path.realpath(wt.get("path", "")) == current_path:
            continue
        if wt.get("locked") or wt.get("prunable"):
            continue
        branch = wt.get("branch", "")
        ts = _last_commit_ts(branch) if branch else ""
        if ts:
            try:
                age_days = (time.time() - float(ts)) / 86400
                has_pr = bool(_pr_state_for_branch(branch, pr_list))
                dirty = _dirty_state(wt.get("path", ""))
                absorbed_by_live = _branch_is_absorbed_by_live(branch)
                if age_days > 7 and absorbed_by_live and not has_pr and not dirty:
                    path = wt.get("path", "")
                    advisory = (
                        "Codex-managed worktree: its branch is patch-equivalent to live; only "
                        "its owning completed clean worker may archive its own thread with "
                        "set_thread_archived; never raw-remove it"
                        if _is_codex_managed_worktree(path) and absorbed_by_live
                        else "Codex-managed worktree: only its owning completed clean worker may "
                        "archive its own thread with set_thread_archived; never raw-remove it"
                        if _is_codex_managed_worktree(path)
                        else "native worktree is clean, inactive, and patch-equivalent to live; "
                        "manual removal still requires owner and process-idle verification"
                        if absorbed_by_live
                        else "unreachable"
                    )
                    clutter.append({
                        "path": path,
                        "type": "worktree",
                        "age_days": round(age_days, 1),
                        "branch": branch,
                        "severity": "advisory",
                        "advisory": advisory,
                    })
            except (ValueError, TypeError):
                pass

    # Local branches that are patch-absorbed, un-attached, and have no open PR.
    for b in _git("for-each-ref", "--format=%(refname:short)", "refs/heads").splitlines():
        if (
            b
            and b != LIVE_BRANCH
            and b not in attached_branches
            and not _pr_state_for_branch(b, pr_list)
            and _branch_is_absorbed_by_live(b)
        ):
            clutter.append({
                "path": f"branch:{b}",
                "type": "branch",
                "severity": "advisory",
                "advisory": "branch patch-equivalent to live with no attached worktree or open PR; consider local deletion after confirming",
            })

    return clutter


@capability("workspace_hygiene_audit", lease=False)
def cmd_hygiene(_args: argparse.Namespace) -> int:
    """Advisory list of workspace clutter. NEVER deletes.

    workspace_hygiene_audit capability owner.
    Covers: backups/, *.bak, root-level scratch files, stale agent-replay logs,
    stale worktrees (>7d no commits + no open PR), merged branches.
    """
    clutter = _collect_clutter()
    print(json.dumps({
        "clutter": clutter,
        "count": len(clutter),
        "action": "advisory_only_never_auto_delete",
        "severity": "advisory",
    }, indent=2))
    return 0


@capability("worktree_post_merge_cleanup", lease=False)
def cmd_post_merge_cleanup(_args: argparse.Namespace) -> int:
    """Post-landing advisory checklist. NEVER deletes.

    worktree_post_merge_cleanup capability owner.
    Emits the same clutter advisory as workspace_hygiene_audit. For a
    Codex-managed path, the owning completed clean worker archives its own
    thread; Codex snapshots and reclaims the path. This script never bypasses
    that lifecycle with a raw worktree removal.
    Composes with .claude/hooks/registry.yaml::post_merge_cleanup hook.
    """
    clutter = _collect_clutter()
    print(json.dumps({
        "clutter": clutter,
        "count": len(clutter),
        "context": "post_merge_cleanup",
        "action": "advisory_only_never_auto_delete_or_raw_remove_codex_managed",
        "severity": "advisory",
    }, indent=2))
    return 0


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main() -> int:
    ap = argparse.ArgumentParser(
        description="ADVISORY-only worktree lifecycle helper. Exit code 0 always.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    # Subcommands
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("status", help="JSON summary of all worktrees + sentinels + PR state")
    sub.add_parser("advisory", help="Cross-worktree visibility map for SessionStart")
    sub.add_parser("branch-keepup", help="Recommend ff/rebase/merge/close for current branch")
    sub.add_parser("hygiene", help="Advisory list of workspace clutter (never deletes)")
    sub.add_parser("post-merge-cleanup", help="Post-merge advisory checklist (never deletes)")

    # Flag aliases for task-brief compatibility
    ap.add_argument("--status", action="store_true", help="Alias for 'status' subcommand")
    ap.add_argument("--hygiene-audit", action="store_true", help="Alias for 'hygiene' subcommand")
    ap.add_argument("--cross-worktree-visibility", action="store_true",
                    help="Alias for 'advisory' subcommand")

    args = ap.parse_args()

    # Resolve flag aliases
    if args.status and not args.cmd:
        args.cmd = "status"
    if args.hygiene_audit and not args.cmd:
        args.cmd = "hygiene"
    if args.cross_worktree_visibility and not args.cmd:
        args.cmd = "advisory"

    dispatch = {
        "status": cmd_status,
        "advisory": cmd_advisory,
        "branch-keepup": cmd_branch_keepup,
        "hygiene": cmd_hygiene,
        "post-merge-cleanup": cmd_post_merge_cleanup,
    }

    if args.cmd not in dispatch:
        ap.print_help()
        return 0  # advisory tool: always exit 0

    try:
        return dispatch[args.cmd](args)
    except Exception:
        # Advisory tool: always exit 0 even on crash
        return 0


if __name__ == "__main__":
    sys.exit(main())
