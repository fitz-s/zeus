#!/usr/bin/env python3
# Created: 2026-05-07
# Last reused or audited: 2026-05-07
# Authority basis: Navigation Topology v2 PLAN §2.6-§2.8; sunset 2027-05-07

"""ADVISORY-only worktree lifecycle helper.

Subcommands (positional):
  status                  — JSON summary of all active worktrees + sentinels
                            + ahead/behind vs origin/main + PR state
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

SENTINEL_FILENAME = "zeus_worktree.yaml"

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
            current = {"path": line[len("worktree "):].strip(), "branch": "", "head": "", "bare": False}
        elif line.startswith("HEAD "):
            current["head"] = line[len("HEAD "):].strip()
        elif line.startswith("branch "):
            ref = line[len("branch "):].strip()
            # refs/heads/branch-name -> branch-name
            current["branch"] = ref.replace("refs/heads/", "")
        elif line.strip() == "bare":
            current["bare"] = True
    if current:
        worktrees.append(current)
    return worktrees


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


def _write_worktree_sentinel(worktree_path: str, payload: dict[str, Any]) -> None:
    """Write zeus_worktree.yaml sentinel post-worktree-add success (M3).

    Race delegated to git atomicity — only called after `git worktree add` succeeds.
    """
    try:
        import yaml as _yaml
        from datetime import datetime, timezone
    except ImportError:
        return

    sentinel_path = Path(worktree_path) / SENTINEL_FILENAME
    branch = payload.get("branch", "unknown")
    base = _git("rev-parse", "--short", "HEAD").strip()
    data = {
        "schema_version": 1,
        "worktree": {
            "name": Path(worktree_path).name,
            "path": worktree_path,
            "branch": branch,
            "base": f"main@{base}",
            "agent_class": payload.get("agent_class", "claude_code"),
            "mode": payload.get("mode", "write"),
            "task_slug": payload.get("task_slug", "unknown"),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "intent": payload.get("intent", ""),
        },
        "sunset_date": "2026-08-07",
    }
    try:
        sentinel_path.write_text(_yaml.dump(data, default_flow_style=False))
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Ahead/behind + PR state helpers
# ---------------------------------------------------------------------------


def _ahead_behind(branch: str) -> tuple[int, int]:
    """Return (ahead, behind) vs origin/main."""
    try:
        ahead = int(_git("rev-list", "--count", f"origin/main..{branch}").strip() or "0")
        behind = int(_git("rev-list", "--count", f"{branch}..origin/main").strip() or "0")
    except (ValueError, TypeError):
        ahead, behind = 0, 0
    return ahead, behind


def _pr_state_for_branch(branch: str, pr_list_json: list[dict]) -> dict[str, Any] | None:
    """Find open PR entry for a branch from pre-fetched gh pr list output."""
    for pr in pr_list_json:
        if pr.get("headRefName", "") == branch:
            return {"number": pr.get("number"), "state": pr.get("state"), "title": pr.get("title", "")}
    return None


def _fetch_pr_list() -> list[dict[str, Any]]:
    """Fetch open PRs via gh pr list --json. Returns [] on failure."""
    raw = _gh("pr", "list", "--json", "number,state,title,headRefName", "--limit", "50")
    if not raw:
        return []
    try:
        data = json.loads(raw)
        return data if isinstance(data, list) else []
    except json.JSONDecodeError:
        return []


def _dirty_state(worktree_path: str) -> bool:
    """Check if a worktree has uncommitted changes."""
    out = _git("status", "--short", "--porcelain", cwd=Path(worktree_path))
    return bool(out.strip())


def _last_commit_ts(branch: str) -> str:
    """Return unix timestamp of last commit on branch, or empty string."""
    return _git("log", "-1", "--format=%ct", branch).strip()


# ---------------------------------------------------------------------------
# Subcommand: status
# ---------------------------------------------------------------------------


@capability("worktree_create", lease=False)
def cmd_worktree_create_advisory(_args: argparse.Namespace) -> int:
    """Advisory: emit worktree creation guidance (never auto-creates).

    worktree_create capability owner. Creation is always an explicit operator
    or agent action (git worktree add). This function emits a structured
    advisory reminding callers to write a sentinel file after creation.
    Called by WorktreeCreate hook handler via dispatch.py.
    """
    print(json.dumps({
        "advisory": (
            "worktree_create: create via `git worktree add -b <branch> <path> <base>`, "
            "then write a zeus_worktree.yaml sentinel with intent/agent/mode/base/created_at. "
            "Sentinel is read by SessionStart for cross-worktree visibility."
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

    current_path = str(REPO_ROOT)

    result_wts = []
    for wt in worktrees:
        branch = wt.get("branch", "")
        wt_path = wt.get("path", "")
        is_current = os.path.realpath(wt_path) == os.path.realpath(current_path)

        ahead, behind = _ahead_behind(branch) if branch else (0, 0)
        dirty = _dirty_state(wt_path) if wt_path else False
        sentinel = _read_sentinel(wt_path)
        pr = _pr_state_for_branch(branch, pr_list)

        result_wts.append({
            "path": wt_path,
            "branch": branch,
            "head": wt.get("head", ""),
            "is_current": is_current,
            "ahead_of_origin_main": ahead,
            "behind_origin_main": behind,
            "dirty": dirty,
            "last_commit_ts": _last_commit_ts(branch) if branch else "",
            "pr_state": pr,
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
        sentinel = _read_sentinel(wt_path) or {}
        wt_data = sentinel.get("worktree", {})
        intent = (wt_data.get("intent") or "no sentinel")[:80]
        task_slug = wt_data.get("task_slug", "")
        agent_class = wt_data.get("agent_class", "")
        ts = _last_commit_ts(branch) if branch else ""

        meta = ", ".join(filter(None, [task_slug, agent_class]))
        lines.append(f"  [{branch}] {wt_path}")
        lines.append(f"    intent: {intent}")
        if meta:
            lines.append(f"    meta: {meta}")
        if ts:
            lines.append(f"    last_commit_ts: {ts}")

        # Staleness advisory: >7d without commits
        if ts:
            try:
                import time
                age_days = (time.time() - float(ts)) / 86400
                if age_days > 7:
                    lines.append(f"    ADVISORY: stale (>{age_days:.0f}d since last commit)")
            except (ValueError, TypeError):
                pass

    print("\n".join(lines))
    return 0


# ---------------------------------------------------------------------------
# Subcommand: branch-keepup
# ---------------------------------------------------------------------------


def _decision_matrix(*, ahead: int, behind: int, merged: bool, dirty: bool) -> str:
    """Encode operator's draft §D: 5 cases."""
    if merged:
        return "branch_already_merged_close" if not dirty else "checkpoint_first_then_close"
    if ahead == 0 and behind > 0:
        return "fresh_branch_or_ff_only" if not dirty else "checkpoint_first"
    if ahead > 0 and behind > 0:
        return "rebase_if_private_else_merge_origin_main" if not dirty else "checkpoint_first_then_choose"
    if ahead == 0 and behind == 0:
        return "current_with_main_proceed"
    return "uncertain_block_and_report"


@capability("worktree_branch_keepup", lease=False)
def cmd_branch_keepup(_args: argparse.Namespace) -> int:
    """Decision matrix recommendation for current branch vs origin/main."""
    current = _git("branch", "--show-current").strip()
    if not current or current == "main":
        print(json.dumps({
            "recommendation": "no-action",
            "reason": "on main or detached HEAD",
            "severity": "advisory",
        }, indent=2))
        return 0

    ahead, behind = _ahead_behind(current)
    merged_output = _git("branch", "--merged", "origin/main")
    merged = any(b.strip().lstrip("* ") == current for b in merged_output.splitlines())
    dirty = _dirty_state(str(REPO_ROOT))
    rec = _decision_matrix(ahead=ahead, behind=behind, merged=merged, dirty=dirty)

    print(json.dumps({
        "branch": current,
        "ahead_of_origin_main": ahead,
        "behind_origin_main": behind,
        "merged_into_origin_main": merged,
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

    # Stale worktrees: >7d no commits + no open PR
    porcelain = _git("worktree", "list", "--porcelain")
    worktrees = _parse_worktree_list(porcelain)
    pr_list = _fetch_pr_list()
    for wt in worktrees[1:]:  # skip main worktree
        branch = wt.get("branch", "")
        ts = _last_commit_ts(branch) if branch else ""
        if ts:
            try:
                age_days = (time.time() - float(ts)) / 86400
                has_pr = bool(_pr_state_for_branch(branch, pr_list))
                if age_days > 7 and not has_pr:
                    clutter.append({
                        "path": wt.get("path", ""),
                        "type": "worktree",
                        "age_days": round(age_days, 1),
                        "branch": branch,
                        "severity": "advisory",
                        "advisory": "stale worktree (>7d no commits, no open PR); consider `git worktree remove` after verifying",
                    })
            except (ValueError, TypeError):
                pass

    # Stale branches: PR merged (branch merged into origin/main)
    merged_output = _git("branch", "--merged", "origin/main")
    for line in merged_output.splitlines():
        b = line.strip().lstrip("* ")
        if b and b not in ("main", "HEAD"):
            clutter.append({
                "path": f"branch:{b}",
                "type": "branch",
                "severity": "advisory",
                "advisory": "branch merged into origin/main; consider `git branch -d` after confirming",
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
    """Post-merge advisory checklist. NEVER deletes.

    worktree_post_merge_cleanup capability owner.
    Emits the same clutter advisory as workspace_hygiene_audit, scoped to
    post-merge context: branch close recommendation, worktree close
    recommendation, backup/draft sweep recommendation.
    Composes with .claude/hooks/registry.yaml::post_merge_cleanup hook.
    """
    clutter = _collect_clutter()
    print(json.dumps({
        "clutter": clutter,
        "count": len(clutter),
        "context": "post_merge_cleanup",
        "action": "advisory_only_never_auto_delete",
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
