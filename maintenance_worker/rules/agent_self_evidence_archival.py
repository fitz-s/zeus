# Created: 2026-05-16
# Last reused or audited: 2026-05-16
# Authority basis:
#   docs/operations/task_2026-05-15_runtime_improvement_engineering_package/02_daily_maintenance_agent/TASK_CATALOG.yaml
#   §agent_self_evidence_archival
#   docs/operations/task_2026-05-15_runtime_improvement_engineering_package/04_workspace_hygiene/PURGE_CATEGORIES.md
"""
Handler: agent_self_evidence_archival

Archives old evidence trail entries to cold storage.

enumerate(): walks ctx.config.evidence_dir for subdirectory entries older
  than evidence_retention_days (default 90). Skips:
    - current_tick_evidence_dir (today's date dir, e.g. evidence/2026-05-16/)
    - any path outside evidence_dir (symlink escape defense)
    - sqlite-companion files (reuses _is_sqlite_companion from zero_byte_state_cleanup)
    - entries not older than retention_days

apply(): this task has live_default=true + dry_run_floor_exempt=true.
  TOP guard: if ctx.dry_run_only, returns mock diff (no move).
  Path containment re-check before every shutil.move (TOCTOU defense).
  TOCTOU re-verify: re-checks path still exists and is still in evidence_dir
  before moving. Uses shutil.move to cold_archive_dir.

Verdict strings:
  ARCHIVAL_CANDIDATE         — old evidence dir, safe to archive
  SKIP_TOO_FRESH             — evidence entry within retention_days
  SKIP_CURRENT_TICK_DIR      — today's evidence dir, never archived
  SKIP_PATH_ESCAPE           — symlink or path outside evidence_dir
  SKIP_SQLITE_COMPANION      — sqlite-companion file, never moved
"""
from __future__ import annotations

import logging
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from maintenance_worker.rules.zero_byte_state_cleanup import _is_sqlite_companion
from maintenance_worker.types.candidates import Candidate
from maintenance_worker.types.results import ApplyResult
from maintenance_worker.types.specs import TickContext

logger = logging.getLogger(__name__)

# Verdict strings
VERDICT_CANDIDATE = "ARCHIVAL_CANDIDATE"
VERDICT_SKIP_FRESH = "SKIP_TOO_FRESH"
VERDICT_SKIP_CURRENT_TICK = "SKIP_CURRENT_TICK_DIR"
VERDICT_SKIP_PATH_ESCAPE = "SKIP_PATH_ESCAPE"
VERDICT_SKIP_SQLITE = "SKIP_SQLITE_COMPANION"

DEFAULT_RETENTION_DAYS = 90
DEFAULT_COLD_ARCHIVE_SUBDIR = "evidence_cold"  # under state_dir


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def enumerate(entry: Any, ctx: TickContext) -> list[Candidate]:  # noqa: A001
    """
    Walk ctx.config.evidence_dir for entries older than evidence_retention_days.

    Skips: current_tick_evidence_dir, paths outside evidence_dir (symlink
    escape), sqlite-companion files, and entries younger than retention_days.

    Returns list[Candidate] with ARCHIVAL_CANDIDATE or SKIP_*.
    """
    raw: dict = entry.raw
    config: dict = raw.get("config", {})

    retention_days: int = int(config.get("evidence_retention_days", DEFAULT_RETENTION_DAYS))
    evidence_dir: Path = ctx.config.evidence_dir
    retention_seconds = retention_days * 86400

    now_ts = time.time()
    today_str = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
    current_tick_dir = evidence_dir / today_str

    candidates: list[Candidate] = []

    if not evidence_dir.exists():
        logger.debug("agent_self_evidence_archival: evidence_dir missing: %s", evidence_dir)
        return candidates

    evidence_dir_resolved = evidence_dir.resolve()

    for path in sorted(evidence_dir.rglob("*")):
        if not path.is_file() and not path.is_dir():
            continue

        # Path containment check — symlink escape defense
        try:
            resolved = path.resolve()
        except (OSError, RuntimeError):
            candidates.append(Candidate(
                task_id="agent_self_evidence_archival",
                path=path,
                verdict=VERDICT_SKIP_PATH_ESCAPE,
                reason="could not resolve path (symlink loop or permission error)",
                evidence={"path": str(path)},
            ))
            continue

        if not resolved.is_relative_to(evidence_dir_resolved):
            candidates.append(Candidate(
                task_id="agent_self_evidence_archival",
                path=path,
                verdict=VERDICT_SKIP_PATH_ESCAPE,
                reason="path escapes evidence_dir boundary (symlink or traversal)",
                evidence={"path": str(path), "evidence_dir": str(evidence_dir_resolved)},
            ))
            continue

        # Current tick dir — forbidden by catalog safety spec
        try:
            if path == current_tick_dir or path.is_relative_to(current_tick_dir):
                candidates.append(Candidate(
                    task_id="agent_self_evidence_archival",
                    path=path,
                    verdict=VERDICT_SKIP_CURRENT_TICK,
                    reason=f"path is in current tick's evidence dir ({today_str})",
                    evidence={"today": today_str},
                ))
                continue
        except (TypeError, ValueError):
            pass

        # SQLite companion — never moved
        if path.is_file() and _is_sqlite_companion(path):
            candidates.append(Candidate(
                task_id="agent_self_evidence_archival",
                path=path,
                verdict=VERDICT_SKIP_SQLITE,
                reason="file matches sqlite-companion pattern",
                evidence={"suffix": path.suffix},
            ))
            continue

        # Age check — only top-level date dirs or files directly in evidence_dir
        # are the unit of archival; skip deeply nested children (they move with parent)
        try:
            rel = path.relative_to(evidence_dir)
        except ValueError:
            continue
        if len(rel.parts) > 1:
            # Child of a date dir — will be moved with its parent dir
            continue

        try:
            mtime = path.stat().st_mtime
        except (OSError, FileNotFoundError):
            continue

        age = now_ts - mtime
        if age < retention_seconds:
            candidates.append(Candidate(
                task_id="agent_self_evidence_archival",
                path=path,
                verdict=VERDICT_SKIP_FRESH,
                reason=f"evidence entry too fresh (age={round(age/86400, 1)}d < {retention_days}d)",
                evidence={"age_days": round(age / 86400, 1), "ttl_days": retention_days},
            ))
            continue

        age_days = round(age / 86400, 1)
        candidates.append(Candidate(
            task_id="agent_self_evidence_archival",
            path=path,
            verdict=VERDICT_CANDIDATE,
            reason=f"evidence entry older than retention_days (age={age_days}d > {retention_days}d)",
            evidence={"age_days": age_days, "ttl_days": retention_days},
        ))

    return candidates


def apply(decision: Candidate, ctx: TickContext) -> ApplyResult:
    """
    Archive an old evidence entry to cold storage.

    TOP-OF-FUNCTION GUARD: if ctx.dry_run_only, returns mock diff (no move).
    Path containment re-check + TOCTOU re-verify before shutil.move.
    live_default=true, dry_run_floor_exempt=true per catalog.

    Only acts on ARCHIVAL_CANDIDATE verdicts.
    """
    # TOP GUARD — defense-in-depth
    if ctx.dry_run_only:
        mock = _mock_diff(decision)
        return ApplyResult(
            task_id="agent_self_evidence_archival",
            dry_run_only=True,
            diff=mock,
        )

    # Only archive candidates
    if decision.verdict != VERDICT_CANDIDATE:
        return ApplyResult(task_id="agent_self_evidence_archival", dry_run_only=True)

    evidence_dir: Path = ctx.config.evidence_dir
    state_dir: Path = ctx.config.state_dir
    cold_archive_dir = state_dir / DEFAULT_COLD_ARCHIVE_SUBDIR

    # TOCTOU re-verify: path still exists?
    if not decision.path.exists():
        logger.info(
            "agent_self_evidence_archival: skip; path gone at apply time: %s",
            decision.path,
        )
        return ApplyResult(task_id="agent_self_evidence_archival", dry_run_only=True)

    # Path containment re-check (race: could have been replaced with a symlink)
    try:
        resolved = decision.path.resolve()
        evidence_dir_resolved = evidence_dir.resolve()
    except (OSError, RuntimeError):
        logger.warning(
            "agent_self_evidence_archival: skip; resolve failed at apply time: %s",
            decision.path,
        )
        return ApplyResult(task_id="agent_self_evidence_archival", dry_run_only=True)

    if not resolved.is_relative_to(evidence_dir_resolved):
        logger.warning(
            "agent_self_evidence_archival: skip; path-escape detected at apply time: %s",
            decision.path,
        )
        return ApplyResult(task_id="agent_self_evidence_archival", dry_run_only=True)

    # SQLite companion re-check
    if decision.path.is_file() and _is_sqlite_companion(decision.path):
        logger.info(
            "agent_self_evidence_archival: skip; sqlite companion at apply time: %s",
            decision.path,
        )
        return ApplyResult(task_id="agent_self_evidence_archival", dry_run_only=True)

    # Prepare destination
    cold_archive_dir.mkdir(parents=True, exist_ok=True)
    dest = cold_archive_dir / decision.path.name

    # Avoid overwrite: suffix with timestamp if dest exists
    if dest.exists():
        ts = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        dest = cold_archive_dir / f"{decision.path.name}.{ts}"

    try:
        shutil.move(str(decision.path), str(dest))
        logger.info(
            "agent_self_evidence_archival: archived %s → %s", decision.path, dest
        )
        return ApplyResult(
            task_id="agent_self_evidence_archival",
            dry_run_only=False,
            moved=((decision.path, dest),),
        )
    except (OSError, shutil.Error) as exc:
        logger.warning(
            "agent_self_evidence_archival: failed to archive %s → %s: %s",
            decision.path, dest, exc,
        )
        return ApplyResult(task_id="agent_self_evidence_archival", dry_run_only=True)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _mock_diff(decision: Candidate) -> tuple[str, ...]:
    """Return a mock diff string showing planned archival."""
    return (
        f"[DRY RUN] would archive: {decision.path}",
        f"       → state/evidence_cold/{decision.path.name}",
        f"  reason: {decision.reason}",
    )
