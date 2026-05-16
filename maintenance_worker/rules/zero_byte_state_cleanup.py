# Created: 2026-05-16
# Last reused or audited: 2026-05-16
# Authority basis:
#   docs/operations/task_2026-05-15_runtime_improvement_engineering_package/02_daily_maintenance_agent/TASK_CATALOG.yaml
#   §zero_byte_state_cleanup
#   docs/operations/task_2026-05-15_runtime_improvement_engineering_package/04_workspace_hygiene/PURGE_CATEGORIES.md
#   §category-6-empty-zero-byte-result-files
"""
Handler: zero_byte_state_cleanup

Removes stale zero-byte files from state/, logs/, evidence/, proofs/.

enumerate(): walks target_dirs for files with size==0 AND mtime >
  zero_byte_age_days (default 7); respects forbidden list (non_zero_files,
  paths_held_by_open_lsof_handle, paths_referenced_by_active_sqlite_attach).

apply(): this task has live_default=true + dry_run_floor_exempt=true per
  catalog. TOP guard: if ctx.dry_run_only, returns mock diff. Otherwise
  calls path.unlink() for real deletion.

Verdict strings:
  ZERO_BYTE_DELETE_CANDIDATE   — zero-byte stale file, safe to delete
  SKIP_NON_ZERO                — file is not zero bytes
  SKIP_LOCKED_LSOF             — open file handle held by another process
  SKIP_SQLITE_ATTACHED         — file matches sqlite db attachment pattern
  SKIP_TOO_FRESH               — mtime within zero_byte_age_days
"""
from __future__ import annotations

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
VERDICT_CANDIDATE = "ZERO_BYTE_DELETE_CANDIDATE"
VERDICT_SKIP_NON_ZERO = "SKIP_NON_ZERO"
VERDICT_SKIP_LOCKED = "SKIP_LOCKED_LSOF"
VERDICT_SKIP_SQLITE = "SKIP_SQLITE_ATTACHED"
VERDICT_SKIP_FRESH = "SKIP_TOO_FRESH"

DEFAULT_AGE_DAYS = 7
DEFAULT_TARGET_DIRS = ["state/", "logs/", "evidence/", "proofs/"]

# SQLite companion file suffixes — any file matching these must never be deleted.
# Includes WAL, SHM, journal modes (rollback + WAL journal), and sqlite3 extensions.
# Deleting a journal/WAL companion while the parent .db is mid-transaction = corruption.
_SQLITE_SUFFIXES = frozenset([
    ".db", ".db-wal", ".db-shm", ".db-journal",
    ".sqlite", ".sqlite-wal", ".sqlite-shm", ".sqlite-journal",
    ".sqlite3", ".sqlite3-wal", ".sqlite3-shm", ".sqlite3-journal",
])

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def enumerate(entry: Any, ctx: TickContext) -> list[Candidate]:  # noqa: A001
    """
    Walk target_dirs for zero-byte files older than zero_byte_age_days.

    Skips: non-zero files, lsof-locked files, sqlite-attached paths,
    fresh files (< age_days).

    Returns list[Candidate] with ZERO_BYTE_DELETE_CANDIDATE or SKIP_*.
    """
    raw: dict = entry.raw
    config: dict = raw.get("config", {})

    age_days: int = int(config.get("zero_byte_age_days", DEFAULT_AGE_DAYS))
    target_dirs: list[str] = list(config.get("target_dirs", DEFAULT_TARGET_DIRS))
    repo_root: Path = ctx.config.repo_root

    now_ts = time.time()
    age_seconds = age_days * 86400

    candidates: list[Candidate] = []

    for dir_rel in target_dirs:
        target_dir = repo_root / dir_rel.rstrip("/")
        if not target_dir.exists():
            logger.debug("zero_byte_state_cleanup: target_dir missing: %s", target_dir)
            continue

        for path in sorted(target_dir.rglob("*")):
            if not path.is_file():
                continue

            # Non-zero size → skip
            try:
                size = path.stat().st_size
                mtime = path.stat().st_mtime
            except (OSError, FileNotFoundError):
                continue

            if size != 0:
                candidates.append(Candidate(
                    task_id="zero_byte_state_cleanup",
                    path=path,
                    verdict=VERDICT_SKIP_NON_ZERO,
                    reason=f"file is not zero bytes (size={size})",
                    evidence={"size": size},
                ))
                continue

            # Fresh file → skip
            age = now_ts - mtime
            if age < age_seconds:
                candidates.append(Candidate(
                    task_id="zero_byte_state_cleanup",
                    path=path,
                    verdict=VERDICT_SKIP_FRESH,
                    reason=f"file is too fresh (age={round(age/86400, 1)}d < {age_days}d)",
                    evidence={"age_days": round(age / 86400, 1), "ttl_days": age_days},
                ))
                continue

            # SQLite attachment pattern → skip
            if _is_sqlite_companion(path):
                candidates.append(Candidate(
                    task_id="zero_byte_state_cleanup",
                    path=path,
                    verdict=VERDICT_SKIP_SQLITE,
                    reason="file matches sqlite attachment suffix pattern",
                    evidence={"suffix": path.suffix},
                ))
                continue

            # Open lsof handle → skip
            if _is_locked_by_lsof(path):
                candidates.append(Candidate(
                    task_id="zero_byte_state_cleanup",
                    path=path,
                    verdict=VERDICT_SKIP_LOCKED,
                    reason="file has open lsof handle",
                    evidence={"path": str(path)},
                ))
                continue

            age_d = round(age / 86400, 1)
            candidates.append(Candidate(
                task_id="zero_byte_state_cleanup",
                path=path,
                verdict=VERDICT_CANDIDATE,
                reason=f"zero-byte stale file: age={age_d}d > {age_days}d",
                evidence={"age_days": age_d, "ttl_days": age_days},
            ))

    return candidates


def apply(decision: Candidate, ctx: TickContext) -> ApplyResult:
    """
    Delete a zero-byte stale file.

    TOP-OF-FUNCTION GUARD: if ctx.dry_run_only, returns mock diff (no deletion).
    Otherwise unlinks the file for real (live_default=true, dry_run_floor_exempt=true).
    Only acts on ZERO_BYTE_DELETE_CANDIDATE verdicts.
    """
    # TOP GUARD — defense-in-depth
    if ctx.dry_run_only:
        mock = _mock_diff(decision)
        return ApplyResult(
            task_id="zero_byte_state_cleanup",
            dry_run_only=True,
            diff=mock,
        )

    # Only delete candidates, not skip verdicts
    if decision.verdict != VERDICT_CANDIDATE:
        return ApplyResult(task_id="zero_byte_state_cleanup", dry_run_only=True)

    try:
        decision.path.unlink()
        logger.info("zero_byte_state_cleanup: deleted %s", decision.path)
        return ApplyResult(
            task_id="zero_byte_state_cleanup",
            dry_run_only=False,
            deleted=(decision.path,),
        )
    except (OSError, FileNotFoundError) as exc:
        logger.warning("zero_byte_state_cleanup: failed to delete %s: %s", decision.path, exc)
        return ApplyResult(task_id="zero_byte_state_cleanup", dry_run_only=True)


# ---------------------------------------------------------------------------
# Internal helpers (mockable)
# ---------------------------------------------------------------------------


def _is_locked_by_lsof(path: Path) -> bool:
    """
    Return True if any process has an open file handle on path.

    Shells `lsof <path>` — output non-empty means locked.
    Returns False on lsof unavailability (fail-open: don't skip unnecessarily).
    """
    try:
        result = subprocess.run(
            ["lsof", str(path)],
            capture_output=True,
            text=True,
            timeout=5,
        )
        # lsof exits 0 with output when file is open; exits 1 with no output when not
        return result.returncode == 0 and bool(result.stdout.strip())
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        # lsof unavailable → assume not locked
        return False


def _is_sqlite_companion(path: Path) -> bool:
    """
    Return True if path is or may be a SQLite companion file — safe to never delete.

    Three checks (any match → True):
      1. Suffix in _SQLITE_SUFFIXES (covers .db, .db-wal, .db-journal, .sqlite3*, etc.)
      2. Name ends with -wal, -shm, or -journal (catches suffix-less WAL names)
      3. Companion-sibling check: if a .db/.sqlite/.sqlite3 file with the same
         stem prefix exists in the same directory, treat this file as sqlite-related.
         This covers Zeus-specific names like zeus-world.db.writer-lock.bulk where
         Path.suffix == ".bulk" but a zeus-world.db companion exists alongside it.
    """
    if path.suffix in _SQLITE_SUFFIXES:
        return True
    if path.name.endswith(("-wal", "-shm", "-journal")):
        return True
    # Companion-sibling check: stem prefix = everything before the first dot after the name root
    # e.g. "zeus-world.db.writer-lock.bulk" → stem_prefix = "zeus-world"
    stem_prefix = path.name.split(".")[0]
    for ext in (".db", ".sqlite", ".sqlite3"):
        if (path.parent / f"{stem_prefix}{ext}").exists():
            return True
    return False


def _mock_diff(decision: Candidate) -> tuple[str, ...]:
    """Return a mock diff string showing planned deletion."""
    return (
        f"[DRY RUN] would delete: {decision.path}",
        f"  reason: {decision.reason}",
        f"  size: 0 bytes",
    )
