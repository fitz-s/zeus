# Created: 2026-05-16
# Last reused or audited: 2026-05-16
# Authority basis:
#   docs/operations/task_2026-05-15_runtime_improvement_engineering_package/02_daily_maintenance_agent/TASK_CATALOG.yaml
#   §untracked_top_level_quarantine
#   docs/operations/task_2026-05-15_runtime_improvement_engineering_package/04_workspace_hygiene/PURGE_CATEGORIES.md
#   §category-5-stale-untracked-top-level-files
"""
Handler: untracked_top_level_quarantine

Proposes quarantine of stale untracked top-level files.

enumerate(): shells `git ls-files --others --exclude-standard` to find
  untracked files; filters by mtime > untracked_ttl_days; respects
  forbidden_paths from catalog (docs/operations/task_*/** and secret
  patterns .env*, *credential*, *secret*, *key*); pre_check:
  not_under_active_packet_dir.

apply(): always dry_run_only (live_default: false in catalog). Returns
  mock diff showing what move to quarantine_dir would do.

Verdict strings:
  UNTRACKED_QUARANTINE_CANDIDATE — stale untracked file, safe to quarantine
  SKIP_FORBIDDEN_PATH            — matches a forbidden pattern
  SKIP_TOO_FRESH                 — mtime within untracked_ttl_days
  SKIP_ACTIVE_PACKET_DIR         — lives under an active task_* packet dir
"""
from __future__ import annotations

import fnmatch
import logging
import subprocess
import time
from pathlib import Path
from typing import Any

from maintenance_worker.types.candidates import Candidate
from maintenance_worker.types.results import ApplyResult
from maintenance_worker.types.specs import TaskSpec, TickContext

logger = logging.getLogger(__name__)

# Verdict strings
VERDICT_CANDIDATE = "UNTRACKED_QUARANTINE_CANDIDATE"
VERDICT_SKIP_FORBIDDEN = "SKIP_FORBIDDEN_PATH"
VERDICT_SKIP_FRESH = "SKIP_TOO_FRESH"
VERDICT_SKIP_ACTIVE_PACKET = "SKIP_ACTIVE_PACKET_DIR"

DEFAULT_TTL_DAYS = 14
DEFAULT_QUARANTINE_DIR = ".archive/untracked"
DEFAULT_FORBIDDEN_PATHS = [
    "docs/operations/task_*/**",
    ".env*",
    "*credential*",
    "*secret*",
    "*key*",
]

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def enumerate(entry: Any, ctx: TickContext) -> list[Candidate]:  # noqa: A001
    """
    Find stale untracked files via `git ls-files --others --exclude-standard`.

    Skips: forbidden patterns, active packet dirs, fresh files (< ttl_days).
    Returns list[Candidate] with UNTRACKED_QUARANTINE_CANDIDATE or SKIP_*.
    """
    raw: dict = entry.raw
    config: dict = raw.get("config", {})

    ttl_days: int = int(config.get("untracked_ttl_days", DEFAULT_TTL_DAYS))
    forbidden_paths: list[str] = list(
        raw.get("safety", {}).get("forbidden_paths", DEFAULT_FORBIDDEN_PATHS)
    )
    repo_root: Path = ctx.config.repo_root

    untracked_paths = _list_untracked_files(repo_root)
    now_ts = time.time()
    ttl_seconds = ttl_days * 86400

    candidates: list[Candidate] = []
    for rel_path_str in untracked_paths:
        abs_path = repo_root / rel_path_str
        rel_name = Path(rel_path_str).name

        # Check forbidden patterns against relative path and basename
        forbidden_verdict = _check_forbidden(rel_path_str, forbidden_paths)
        if forbidden_verdict:
            candidates.append(Candidate(
                task_id="untracked_top_level_quarantine",
                path=abs_path,
                verdict=VERDICT_SKIP_FORBIDDEN,
                reason=f"matches forbidden pattern: {forbidden_verdict}",
                evidence={"pattern": forbidden_verdict, "rel_path": rel_path_str},
            ))
            continue

        # pre_check: not_under_active_packet_dir
        if _is_under_active_packet_dir(rel_path_str, repo_root):
            candidates.append(Candidate(
                task_id="untracked_top_level_quarantine",
                path=abs_path,
                verdict=VERDICT_SKIP_ACTIVE_PACKET,
                reason="file is under an active docs/operations/task_*/ packet directory",
                evidence={"rel_path": rel_path_str},
            ))
            continue

        # mtime check
        mtime = _get_mtime(abs_path)
        if mtime is None or (now_ts - mtime) < ttl_seconds:
            age_days = round((now_ts - mtime) / 86400, 1) if mtime is not None else None
            candidates.append(Candidate(
                task_id="untracked_top_level_quarantine",
                path=abs_path,
                verdict=VERDICT_SKIP_FRESH,
                reason=f"file modified within {ttl_days}d TTL (age={age_days}d)",
                evidence={"rel_path": rel_path_str, "age_days": age_days, "ttl_days": ttl_days},
            ))
            continue

        age_days = round((now_ts - mtime) / 86400, 1)
        candidates.append(Candidate(
            task_id="untracked_top_level_quarantine",
            path=abs_path,
            verdict=VERDICT_CANDIDATE,
            reason=f"stale untracked file: age={age_days}d > ttl={ttl_days}d",
            evidence={"rel_path": rel_path_str, "age_days": age_days, "ttl_days": ttl_days},
        ))

    return candidates


def apply(decision: Candidate, ctx: TickContext) -> ApplyResult:
    """
    Apply untracked file quarantine. Always dry_run_only (live_default: false).

    TOP-OF-FUNCTION GUARD per PLAN §1.5.4 — defense-in-depth.
    Returns ApplyResult with mock diff showing planned move to quarantine_dir.
    """
    # This task is ALWAYS proposal-only — dry_run_only unconditionally.
    mock = _mock_diff(decision)
    return ApplyResult(
        task_id="untracked_top_level_quarantine",
        dry_run_only=True,
        diff=mock,
    )


# ---------------------------------------------------------------------------
# Internal helpers (mockable)
# ---------------------------------------------------------------------------


def _list_untracked_files(repo_root: Path) -> list[str]:
    """
    Run `git ls-files --others --exclude-standard` and return relative paths.

    Returns [] on git failure.
    """
    try:
        result = subprocess.run(
            ["git", "ls-files", "--others", "--exclude-standard"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=15,
        )
        if result.returncode != 0:
            logger.warning("git ls-files failed: %s", result.stderr.strip())
            return []
        lines = [ln.strip() for ln in result.stdout.splitlines() if ln.strip()]
        return lines
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as exc:
        logger.warning("_list_untracked_files error: %s", exc)
        return []


def _check_forbidden(rel_path: str, forbidden_paths: list[str]) -> str | None:
    """
    Return the matching forbidden pattern string if rel_path matches any,
    else None. Uses fnmatch for glob patterns; also checks basename.
    """
    name = Path(rel_path).name
    for pattern in forbidden_paths:
        # Match against full relative path and basename
        if fnmatch.fnmatch(rel_path, pattern) or fnmatch.fnmatch(name, pattern):
            return pattern
        # Handle task_*/** style: prefix check on path components
        if "task_*" in pattern:
            # Check if any path component matches task_* prefix
            parts = Path(rel_path).parts
            for part in parts:
                if fnmatch.fnmatch(part, "task_*"):
                    return pattern
    return None


def _is_under_active_packet_dir(rel_path: str, repo_root: Path) -> bool:
    """
    Return True if rel_path is under docs/operations/task_*/.

    These directories are active packet dirs and must never be quarantined.
    """
    parts = Path(rel_path).parts
    # Check for docs/operations/task_*/ prefix
    if len(parts) >= 3 and parts[0] == "docs" and parts[1] == "operations":
        if fnmatch.fnmatch(parts[2], "task_*"):
            return True
    return False


def _get_mtime(path: Path) -> float | None:
    """Return mtime of path in seconds since epoch, or None if inaccessible."""
    try:
        return path.stat().st_mtime
    except (OSError, FileNotFoundError):
        return None


def _mock_diff(decision: Candidate) -> tuple[str, ...]:
    """Return a mock diff string showing planned move to quarantine dir."""
    return (
        f"[DRY RUN] would move: {decision.path}",
        f"       → .archive/untracked/{decision.path.name}",
        f"  reason: {decision.reason}",
    )
