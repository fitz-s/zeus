# Created: 2026-05-16
# Last reused or audited: 2026-05-16
# Authority basis:
#   docs/operations/task_2026-05-15_runtime_improvement_engineering_package/02_daily_maintenance_agent/TASK_CATALOG.yaml
#   §in_repo_scratch_quarantine
#   docs/operations/task_2026-05-15_runtime_improvement_engineering_package/04_workspace_hygiene/PURGE_CATEGORIES.md
#   §category-3-in-repo-scratch-directories
"""
Handler: in_repo_scratch_quarantine

Identifies stale scratch files/directories in the repo root.

enumerate(): walks repo root for files/dirs matching scratch_patterns +
  older than scratch_ttl_days. Respects forbidden_paths (src/, tests/,
  architecture/, docs/, state/, config/, scripts/) — these are never
  candidates regardless of name match.

apply(): always dry_run_only (live_default: false). Returns mock diff
  showing what move to quarantine_dir would do.

Verdict strings:
  SCRATCH_QUARANTINE_CANDIDATE — stale scratch item, safe to quarantine
  SKIP_FORBIDDEN_PATH          — inside a forbidden directory
  SKIP_TOO_FRESH               — modified within ttl_days
  SKIP_NO_PATTERN_MATCH        — does not match any scratch_pattern
"""
from __future__ import annotations

import fnmatch
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from maintenance_worker.types.candidates import Candidate
from maintenance_worker.types.results import ApplyResult
from maintenance_worker.types.specs import TaskSpec, TickContext

logger = logging.getLogger(__name__)

# Verdict strings
VERDICT_CANDIDATE = "SCRATCH_QUARANTINE_CANDIDATE"
VERDICT_SKIP_FORBIDDEN = "SKIP_FORBIDDEN_PATH"
VERDICT_SKIP_FRESH = "SKIP_TOO_FRESH"
VERDICT_SKIP_NO_MATCH = "SKIP_NO_PATTERN_MATCH"

# Default config values (override via catalog raw dict)
DEFAULT_TTL_DAYS = 7
DEFAULT_SCRATCH_PATTERNS = [
    "tmp", "tmp/*", "scratch", "scratch/*",
    "debug_*", "*.scratch.*", "*.tmp",
]
DEFAULT_FORBIDDEN_PATHS = [
    "src", "tests", "architecture", "docs", "state", "config", "scripts",
]
DEFAULT_QUARANTINE_DIR = ".archive/scratch"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def enumerate(entry: Any, ctx: TickContext) -> list[Candidate]:  # noqa: A001
    """
    Walk repo root for scratch files/dirs matching scratch_patterns.

    Skips anything inside forbidden_paths. Skips items fresher than ttl_days.

    entry: TaskCatalogEntry — spec + raw config dict from catalog.
    ctx:   TickContext.
    """
    spec: TaskSpec = entry.spec
    raw: dict = entry.raw
    config: dict = raw.get("config", {})

    ttl_days: int = int(config.get("scratch_ttl_days", DEFAULT_TTL_DAYS))
    scratch_patterns: list[str] = list(config.get("scratch_patterns", DEFAULT_SCRATCH_PATTERNS))
    forbidden_paths: list[str] = list(
        raw.get("safety", {}).get("forbidden_paths", DEFAULT_FORBIDDEN_PATHS)
    )
    repo_root: Path = ctx.config.repo_root

    now_ts = time.time()
    ttl_seconds = ttl_days * 86400

    # Resolve forbidden paths to absolute strings for prefix matching
    forbidden_abs = {str((repo_root / fp.rstrip("/*")).resolve()) for fp in forbidden_paths}
    # Also handle glob-prefixed forbidden paths like "src/**"
    forbidden_prefixes = [
        str(repo_root / fp.split("*")[0].rstrip("/"))
        for fp in forbidden_paths
    ]

    candidates: list[Candidate] = []

    # Walk repo root (non-recursively for top-level items first)
    try:
        top_level = list(repo_root.iterdir())
    except OSError as exc:
        logger.warning("in_repo_scratch_quarantine: cannot list repo_root %s: %s", repo_root, exc)
        return candidates

    for item in top_level:
        # Skip hidden git internals
        if item.name.startswith(".git"):
            continue

        item_rel = str(item.relative_to(repo_root))
        evidence: dict[str, object] = {
            "path_rel": item_rel,
        }

        # Check forbidden: item itself is under a forbidden path
        if _is_forbidden(item, forbidden_prefixes, repo_root):
            # Only emit SKIP_FORBIDDEN if the item matched a scratch pattern
            # (avoids flooding output with every src/ file)
            if _matches_any_pattern(item.name, scratch_patterns):
                candidates.append(Candidate(
                    task_id=spec.task_id,
                    path=item,
                    verdict=VERDICT_SKIP_FORBIDDEN,
                    reason=f"Path '{item_rel}' is under a forbidden directory.",
                    evidence=evidence,
                ))
            continue

        # Check pattern match
        if not _matches_any_pattern(item.name, scratch_patterns):
            continue  # Not a scratch target — silently skip

        evidence["pattern_matched"] = True

        # Check mtime
        try:
            mtime = _item_mtime(item)
        except OSError as exc:
            logger.warning("in_repo_scratch_quarantine: cannot stat %s: %s", item, exc)
            continue

        age_days = (now_ts - mtime) / 86400
        evidence["age_days"] = round(age_days, 1)

        if age_days < ttl_days:
            candidates.append(Candidate(
                task_id=spec.task_id,
                path=item,
                verdict=VERDICT_SKIP_FRESH,
                reason=f"'{item_rel}' modified {age_days:.1f} days ago (< {ttl_days}d threshold).",
                evidence=evidence,
            ))
            continue

        candidates.append(Candidate(
            task_id=spec.task_id,
            path=item,
            verdict=VERDICT_CANDIDATE,
            reason=f"'{item_rel}' is stale scratch ({age_days:.1f} days, matches scratch pattern).",
            evidence=evidence,
        ))

    # Also walk inside known scratch dirs (e.g., tmp/, scratch/) recursively
    # to catch files like tmp/foo.txt that match "tmp/*"
    for scratch_root_name in ("tmp", "scratch"):
        scratch_dir = repo_root / scratch_root_name
        if not scratch_dir.exists() or not scratch_dir.is_dir():
            continue
        if _is_forbidden(scratch_dir, forbidden_prefixes, repo_root):
            continue
        for item in _walk_dir(scratch_dir):
            if item == scratch_dir:
                continue  # Already handled as top-level
            item_rel = str(item.relative_to(repo_root))
            evidence = {"path_rel": item_rel}
            try:
                mtime = _item_mtime(item)
            except OSError:
                continue
            age_days = (now_ts - mtime) / 86400
            evidence["age_days"] = round(age_days, 1)
            if age_days < ttl_days:
                continue  # Skip fresh nested items silently
            candidates.append(Candidate(
                task_id=spec.task_id,
                path=item,
                verdict=VERDICT_CANDIDATE,
                reason=f"'{item_rel}' is stale scratch content ({age_days:.1f} days).",
                evidence=evidence,
            ))

    logger.info(
        "in_repo_scratch_quarantine: found %d candidates (%d quarantinable)",
        len(candidates),
        sum(1 for c in candidates if c.verdict == VERDICT_CANDIDATE),
    )
    return candidates


def apply(decision: Any, ctx: TickContext) -> ApplyResult:
    """
    Apply scratch quarantine. Always dry_run_only (live_default: false in catalog).

    Top-of-function guard per PLAN §1.5.4: defense-in-depth.
    Returns ApplyResult with mock diff showing what move to quarantine_dir would do.
    """
    # TOP-OF-FUNCTION GUARD
    mock = _mock_diff(decision, ctx)
    return ApplyResult(
        task_id="in_repo_scratch_quarantine",
        dry_run_only=True,
        diff=mock,
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _is_forbidden(item: Path, forbidden_prefixes: list[str], repo_root: Path) -> bool:
    """
    Return True if item is under any forbidden directory prefix.
    """
    item_str = str(item.resolve())
    for prefix in forbidden_prefixes:
        if item_str.startswith(prefix):
            return True
    # Also check relative name: item itself is a forbidden root dir
    if item.is_dir():
        for prefix in forbidden_prefixes:
            prefix_name = Path(prefix).name
            if item.name == prefix_name and item.parent == repo_root:
                return True
    return False


def _matches_any_pattern(name: str, patterns: list[str]) -> bool:
    """
    Return True if `name` matches any of the scratch patterns.

    Patterns like "tmp", "scratch" match exact directory names.
    Patterns like "debug_*", "*.tmp" use fnmatch.
    Patterns like "tmp/*" are treated as "tmp" match on directory name.
    """
    for pattern in patterns:
        # Normalise: strip trailing /* for directory patterns
        base_pattern = pattern.rstrip("/*").rstrip("/")
        if base_pattern == name:
            return True
        if fnmatch.fnmatch(name, base_pattern):
            return True
        if fnmatch.fnmatch(name, pattern):
            return True
    return False


def _item_mtime(item: Path) -> float:
    """Return the most recent mtime for a file or directory (max across contents)."""
    mtime = item.stat().st_mtime
    if item.is_dir():
        for child in item.rglob("*"):
            try:
                child_mtime = child.stat().st_mtime
                if child_mtime > mtime:
                    mtime = child_mtime
            except OSError:
                pass
    return mtime


def _walk_dir(directory: Path):
    """Yield all files in directory recursively."""
    try:
        for child in directory.rglob("*"):
            yield child
    except OSError:
        pass


def _mock_diff(decision: Any, ctx: TickContext) -> tuple[str, ...]:
    """Return a mock diff tuple for dry-run proposals."""
    if decision is None:
        return ("# dry-run: no scratch quarantine decisions to apply",)
    path_str = str(getattr(decision, "path", decision))
    raw = getattr(ctx.config, "env_vars", {})
    quarantine_dir = raw.get("quarantine_dir", ".archive/scratch") if isinstance(raw, dict) else ".archive/scratch"
    dest = str(Path(quarantine_dir) / Path(path_str).name)
    return (
        f"# dry-run proposal for in_repo_scratch_quarantine",
        f"# would execute: mv {path_str} {dest}",
    )
