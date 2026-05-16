# Created: 2026-05-15
# Last reused or audited: 2026-05-15
# Authority basis: docs/operations/task_2026-05-15_p5_maintenance_worker_core/SCAFFOLD.md §3 core/ kill_switch.py
#                  docs/operations/task_2026-05-15_runtime_improvement_engineering_package/02_daily_maintenance_agent/SAFETY_CONTRACT.md §"Kill Switch" + §"What If A Forbidden Mutation Already Happened"
"""
kill_switch — kill-switch checks, self-quarantine, and post-mutation detector.

SCAFFOLD §3 Path A vs Path B (lines 190-202):
  Path A: validate_action() returns FORBIDDEN_* → refuse_fatal() only.
          SELF_QUARANTINE is NOT written on Path A.

  Path B: post_mutation_detector() sees divergence AFTER disk state changed
          → write_self_quarantine() + URGENT alert + non-zero exit.

write_self_quarantine() has EXACTLY ONE CALLER: post_mutation_detector().
This constraint is enforced by convention (C2 per SCAFFOLD BATCH_DONE).

Stdlib only. Imports only from maintenance_worker.types.*.
"""
from __future__ import annotations

import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from maintenance_worker.types.results import ApplyResult
from maintenance_worker.types.specs import ProposalManifest

logger = logging.getLogger(__name__)

# File names per SAFETY_CONTRACT.md §"Kill Switch"
_KILL_SWITCH_FILE = "KILL_SWITCH"
_MAINTENANCE_PAUSED_FILE = "MAINTENANCE_PAUSED"
_SELF_QUARANTINE_FILE = "SELF_QUARANTINE"

# Exit code for post-mutation detector divergence (distinct from refusal codes).
_POST_MUTATION_EXIT_CODE = 50


# ---------------------------------------------------------------------------
# Check helpers — read-only
# ---------------------------------------------------------------------------


def is_kill_switch_set(state_dir: Path) -> bool:
    """True if KILL_SWITCH file exists in state_dir."""
    return (state_dir / _KILL_SWITCH_FILE).exists()


def is_paused(state_dir: Path) -> bool:
    """True if MAINTENANCE_PAUSED file exists (soft pause, no re-ack needed)."""
    return (state_dir / _MAINTENANCE_PAUSED_FILE).exists()


def is_self_quarantined(state_dir: Path) -> bool:
    """
    True if SELF_QUARANTINE file exists.

    Checked every tick in CHECK_GUARDS per SCAFFOLD §3 lines 166.
    If True, the engine calls refuse_fatal(SELF_QUARANTINED, ...).
    """
    return (state_dir / _SELF_QUARANTINE_FILE).exists()


# ---------------------------------------------------------------------------
# Self-quarantine writer — ONE caller only: post_mutation_detector
# ---------------------------------------------------------------------------


def write_self_quarantine(state_dir: Path, reason: str) -> None:
    """
    Write SELF_QUARANTINE file to state_dir.

    MUST be called ONLY by post_mutation_detector() (Path B, SCAFFOLD §3
    lines 197-202). Never call from validate_action() violations or guard
    failures — those are Path A and must use refuse_fatal() only.

    Writes the reason and UTC timestamp for human investigation.
    Uses atomic tmp→replace pattern per OpenClaw state-update convention.
    """
    target = state_dir / _SELF_QUARANTINE_FILE
    state_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(tz=timezone.utc).isoformat()
    content = f"SELF_QUARANTINE\ntimestamp: {timestamp}\nreason: {reason}\n"
    tmp = target.with_suffix(".tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(target)
    logger.critical(
        "SELF_QUARANTINE written: state_dir=%s reason=%s", state_dir, reason
    )


# ---------------------------------------------------------------------------
# Post-mutation detector — Path B
# ---------------------------------------------------------------------------


def post_mutation_detector(
    apply_result: ApplyResult,
    manifest: ProposalManifest,
    state_dir: Path,
) -> None:
    """
    Compare applied filesystem diff against the allowed-write set in manifest.

    Any divergence → write_self_quarantine() + log URGENT + sys.exit(50).
    No divergence → returns normally.

    Divergence definition:
      - A path appears in apply_result.moved but NOT in manifest.proposed_moves
      - A path appears in apply_result.deleted but NOT in manifest.proposed_deletes
      - A path appears in apply_result.created but NOT in manifest.proposed_creates

    Only the SOURCE path of a move pair is checked against proposed_moves
    source paths (destination is the move target, not a new write surface).

    Per SAFETY_CONTRACT.md §229-238: this is the only trigger for SELF_QUARANTINE.
    The PR (if any) is left unmerged for human inspection. Human reconciles;
    agent never auto-reverts.
    """
    violations: list[str] = []

    # Build allowed sets from manifest
    allowed_move_sources: set[Path] = {src for src, _dst in manifest.proposed_moves}
    allowed_deletes: set[Path] = set(manifest.proposed_deletes)
    allowed_creates: set[Path] = set(manifest.proposed_creates)

    # Check applied moves against manifest
    for src, dst in apply_result.moved:
        if src not in allowed_move_sources:
            violations.append(f"UNEXPECTED_MOVE src={src} dst={dst}")

    # Check applied deletes against manifest
    for deleted_path in apply_result.deleted:
        if deleted_path not in allowed_deletes:
            violations.append(f"UNEXPECTED_DELETE path={deleted_path}")

    # Check applied creates against manifest
    for created_path in apply_result.created:
        if created_path not in allowed_creates:
            violations.append(f"UNEXPECTED_CREATE path={created_path}")

    if not violations:
        logger.debug(
            "post_mutation_detector: no divergence for task_id=%s",
            apply_result.task_id,
        )
        return

    # Divergence detected — Path B response
    reason = (
        f"post_mutation_detector divergence for task_id={apply_result.task_id!r}: "
        + "; ".join(violations)
    )
    logger.critical("URGENT: %s", reason)

    # write_self_quarantine is the ONLY caller site; any violation goes here.
    write_self_quarantine(state_dir, reason)
    sys.exit(_POST_MUTATION_EXIT_CODE)


# ---------------------------------------------------------------------------
# Scheduler invocation detection
# ---------------------------------------------------------------------------


def check_scheduler_invocation() -> str:
    """
    Detect how the agent was invoked.

    Returns an InvocationMode value string (avoids circular import with
    cli/scheduler_detect.py which owns the full implementation in P5.5).

    Detection order:
      1. MAINTENANCE_SCHEDULER env var → SCHEDULED
      2. Heuristic parent-process check (launchd ppid=1) → SCHEDULED
      3. Otherwise → MANUAL_CLI (forces DRY_RUN_ONLY per SAFETY_CONTRACT.md
         §"Accidental-Trigger Containment")

    Full scheduler_detect.py implementation deferred to P5.5.
    """
    if os.environ.get("MAINTENANCE_SCHEDULER") == "1":
        return "SCHEDULED"
    # Launchd parent: ppid == 1 on macOS (launchd is PID 1)
    try:
        ppid = os.getppid()
        if ppid == 1:
            return "SCHEDULED"
    except OSError:
        pass
    return "MANUAL_CLI"
